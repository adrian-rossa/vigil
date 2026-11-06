"""Watchdog agent: deterministic poll loop asserting absolute workload health.

Watchdog OBSERVES only. It does not call mutation tools and does not issue
rollbacks. When the target has not converged to a healthy state, it returns
WatchdogResult(degraded=True) to the Orchestrator, which owns the rollback
decision.

Poll interval and window are env-var overridable:
  WATCHDOG_POLL_INTERVAL_S (default 5.0)
  WATCHDOG_WINDOW_S        (default 300.0)

This module intentionally does NOT use pydantic-ai Agent -- Watchdog logic is
deterministic: capture snapshot, evaluate an absolute-health predicate, sleep,
repeat. No LLM reasoning is required and adding one would increase latency and
cost for no benefit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import NamedTuple

from common.constants import WATCHDOG_HEALTHY_STREAK_K
from common.flux_status import coerce_flux_status
from common.mcp_call import call_tool

from .models import (
    HealthSnapshot,
    HealthSnapshotUnavailable,
    WatchdogDeps,
    WatchdogResult,
)

log = logging.getLogger(__name__)

WATCHDOG_POLL_INTERVAL_S: float = float(
    os.environ.get("WATCHDOG_POLL_INTERVAL_S", "5.0")
)
WATCHDOG_WINDOW_S: float = float(os.environ.get("WATCHDOG_WINDOW_S", "300.0"))

# Module-level aliases so tests can monkeypatch POLL_INTERVAL_S / WINDOW_S directly.
POLL_INTERVAL_S = WATCHDOG_POLL_INTERVAL_S
WINDOW_S = WATCHDOG_WINDOW_S
HEALTHY_STREAK_K = WATCHDOG_HEALTHY_STREAK_K

_WORKLOAD_KINDS = frozenset({"Deployment", "StatefulSet"})
_PROGRESS_DEADLINE_REASON = "ProgressDeadlineExceeded"

_ACTIVE_RUNNING = "Active: active (running)"
OS_CHECK_SYSTEMD = "systemd"
OS_CHECK_SYSCTL = "sysctl"
OS_CHECK_NODE_CONDITION = "node_condition"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pod_line_ready(line: str) -> bool:
    """Report whether a `get_pods` text line describes a fully-ready pod.

    A pod is ready only when its READY column numerator equals the denominator
    (e.g. 2/2, not 0/1) AND its phase is Running or Succeeded. A crashlooping
    pod shown as `Running 0/1` is not ready.
    """
    tokens = line.split()
    if not any(phase in tokens for phase in ("Running", "Succeeded")):
        return False
    for token in tokens:
        if "/" in token:
            num, _, den = token.partition("/")
            if num.isdigit() and den.isdigit():
                return den != "0" and num == den
    return False


def _parse_pod_counts(tool_output: object) -> tuple[int, int]:
    """Extract (ready_pods, total_pods) from a kubectl-mcp get_pods response.

    Accepts a dict with a "content" key (string or list) or a bare string/list.
    A pod counts as ready only when its READY fraction is fully satisfied and
    its phase is Running/Succeeded. Raises HealthSnapshotUnavailable on
    unrecognised shapes: returning (0, 0) would let a transient or malformed
    response masquerade as zero ready pods and fabricate degradation mid-poll.
    """
    if isinstance(tool_output, dict):
        content: object = tool_output.get("content", tool_output)
    else:
        content = tool_output

    if isinstance(content, str):
        lines = [line for line in content.splitlines() if line.strip()]
        total = len(lines)
        ready = sum(1 for line in lines if _pod_line_ready(line))
        return ready, total

    if isinstance(content, list):
        total = len(content)
        ready = sum(
            1
            for item in content
            if isinstance(item, dict) and item.get("status") in ("Running", "Ready")
        )
        return ready, total

    raise HealthSnapshotUnavailable(
        f"unrecognised get_pods response shape: {type(content).__name__}"
    )


def _strip_flux_revision(revision: str | None) -> str | None:
    """Return the bare SHA from a Flux revision like `branch/name@sha1:abc123`."""
    if not revision:
        return None
    sha = revision.rsplit("@", 1)[-1]
    if ":" in sha:
        sha = sha.split(":", 1)[1]
    return sha or None


def revision_matches(flux_revision: str | None, expected_revision: str) -> bool:
    sha = _strip_flux_revision(flux_revision)
    if not sha:
        return False
    return sha.startswith(expected_revision) or expected_revision.startswith(sha)


def _apply_workload_status(snapshot_fields: dict, status: dict) -> None:
    snapshot_fields["workload_found"] = bool(status.get("found"))
    snapshot_fields["generation"] = status.get("generation")
    snapshot_fields["observed_generation"] = status.get("observedGeneration")
    snapshot_fields["spec_replicas"] = status.get("specReplicas")
    snapshot_fields["ready_replicas"] = status.get("readyReplicas")
    snapshot_fields["updated_replicas"] = status.get("updatedReplicas")
    snapshot_fields["available_replicas"] = status.get("availableReplicas")
    for condition in status.get("conditions") or []:
        ctype = condition.get("type")
        cstatus = condition.get("status")
        if ctype == "Available":
            snapshot_fields["available_condition"] = cstatus == "True"
        elif ctype == "Progressing":
            snapshot_fields["progressing_ok"] = cstatus != "False"
            snapshot_fields["progress_deadline_exceeded"] = (
                condition.get("reason") == _PROGRESS_DEADLINE_REASON
            )


_ROLLOUT_ENVELOPE_KEYS = ("content", "text", "result")


def _coerce_rollout_status(result: object) -> dict:
    payload: object = result
    if isinstance(payload, dict) and "found" not in payload:
        for key in _ROLLOUT_ENVELOPE_KEYS:
            if key in payload:
                payload = payload[key]
                break
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise HealthSnapshotUnavailable(
                f"rollout_status not JSON-decodable: {payload[:120]}"
            ) from exc
    if not (isinstance(payload, dict) and "found" in payload):
        raise HealthSnapshotUnavailable("rollout_status missing workload fields")
    return payload


async def capture_health_snapshot(deps: WatchdogDeps) -> HealthSnapshot:
    """Single-shot absolute-health probe via kubectl-mcp and flux-mcp.

    For Deployment/StatefulSet targets it reads structured rollout status; it
    always reads pod counts (namespace-liveness fallback and crashloop coverage)
    and the Flux Kustomization Ready state plus its applied revision.
    """
    snapshot_fields: dict = {
        "workload_found": False,
        "generation": None,
        "observed_generation": None,
        "spec_replicas": None,
        "ready_replicas": None,
        "updated_replicas": None,
        "available_replicas": None,
        "available_condition": None,
        "progressing_ok": None,
        "progress_deadline_exceeded": False,
    }

    if deps.target_kind in _WORKLOAD_KINDS and deps.target_name:
        rollout_result = await call_tool(
            deps.kubectl_mcp,
            "rollout_status",
            {"namespace": deps.namespace, "deployment": deps.target_name},
        )
        status = _coerce_rollout_status(rollout_result)
        _apply_workload_status(snapshot_fields, status)

    pods_result = await call_tool(
        deps.kubectl_mcp, "get_pods", {"namespace": deps.namespace}
    )
    ready, total = _parse_pod_counts(pods_result)

    flux_ready: bool | None = None
    flux_revision: str | None = None
    try:
        kust_result = await call_tool(
            deps.flux_mcp,
            "get_kustomization_status",
            {
                "namespace": deps.flux_kustomization_namespace,
                "name": deps.flux_kustomization_name,
            },
        )
        kust_data = coerce_flux_status(kust_result)
        ready_value = kust_data.get("ready")
        flux_ready = ready_value if isinstance(ready_value, bool) else None
        flux_revision = kust_data.get("revision")
    except (RuntimeError, ValueError, AttributeError, TimeoutError) as exc:
        log.warning("flux kustomization status unavailable: %s", exc)
        flux_ready = None

    return HealthSnapshot(
        ready_pods=ready,
        total_pods=total,
        flux_ready=flux_ready,
        flux_revision=flux_revision,
        captured_at=_now_iso(),
        **snapshot_fields,
    )


def is_workload_healthy(
    snapshot: HealthSnapshot, expected_revision: str | None
) -> bool:
    """Return True when the target has converged to an absolutely healthy state.

    For a workload target (rollout status present) this mirrors the Kubernetes
    rollout-complete criteria: generation observed, all replicas updated, ready,
    and available, Available not False, Progressing not False, and never
    ProgressDeadlineExceeded. For a non-workload target it falls back to
    namespace liveness. Either path additionally requires Flux not to be
    Not-Ready and, when an expected revision is supplied, the applied revision
    to match it.
    """
    if snapshot.progress_deadline_exceeded:
        return False

    is_workload = (
        snapshot.observed_generation is not None and snapshot.spec_replicas is not None
    )
    if is_workload:
        if not snapshot.workload_found:
            return False
        if snapshot.observed_generation != snapshot.generation:
            return False
        replicas = (
            snapshot.ready_replicas,
            snapshot.updated_replicas,
            snapshot.available_replicas,
        )
        if any(value != snapshot.spec_replicas for value in replicas):
            return False
        if snapshot.available_condition is False:
            return False
        if snapshot.progressing_ok is False:
            return False
    else:
        if not (snapshot.total_pods > 0 and snapshot.ready_pods == snapshot.total_pods):
            return False

    if snapshot.flux_ready is False:
        return False
    if expected_revision and snapshot.flux_revision:
        if not revision_matches(snapshot.flux_revision, expected_revision):
            return False
    return True


def _parse_sysctl_value(text: str) -> str:
    _, _, value = text.rpartition("=")
    return value.strip()


def _coerce_tool_text(result: object) -> str:
    if isinstance(result, dict):
        content = result.get("content", result)
    else:
        content = result
    return content if isinstance(content, str) else str(content)


def _node_condition_satisfied(
    text: str, condition: str | None, expected: str | None
) -> bool:
    return f"{condition}: {expected}" in text


_LEASE_RENEW_RE = re.compile(r"^LeaseRenewTime: (\S+)$", re.MULTILINE)
_LEASE_DURATION_RE = re.compile(r"^LeaseDurationSeconds: (\d+)$", re.MULTILINE)


class OsReading(NamedTuple):
    healthy: bool
    lease_renew: datetime | None = None


def _lease_renew_time(text: str) -> datetime | None:
    match = _LEASE_RENEW_RE.search(text)
    if match is None:
        return None
    try:
        return datetime.fromisoformat(match.group(1))
    except ValueError:
        return None


def _lease_duration_s(text: str) -> int | None:
    match = _LEASE_DURATION_RE.search(text)
    return int(match.group(1)) if match else None


async def probe_os_target(deps: WatchdogDeps) -> OsReading:
    """Report whether the NixOS host has converged to the declared OS state.

    systemd targets are healthy when the unit reports Active: active (running);
    sysctl targets when the live value equals os_check_expected; node-condition
    targets when describe_node reports os_check_key at os_check_expected. A
    target with no resolved check is indeterminate and raises, as does a failed
    read.
    """
    if not deps.target_host:
        return OsReading(False)
    if deps.os_check_kind == OS_CHECK_NODE_CONDITION:
        text = await _describe_node_text(deps)
        return OsReading(
            _node_condition_satisfied(text, deps.os_check_key, deps.os_check_expected),
            _lease_renew_time(text),
        )
    has_keyed_check = deps.os_check_kind in (
        OS_CHECK_SYSTEMD,
        OS_CHECK_SYSCTL,
    ) and bool(deps.os_check_key)
    if not has_keyed_check:
        raise HealthSnapshotUnavailable("no resolvable os check")
    if deps.nixos_mcp is None:
        return OsReading(False)
    if deps.os_check_kind == OS_CHECK_SYSTEMD:
        result = await call_tool(
            deps.nixos_mcp,
            "get_systemd_status",
            {"host": deps.target_host, "unit": deps.os_check_key},
        )
        return OsReading(_ACTIVE_RUNNING in _coerce_tool_text(result))
    result = await call_tool(
        deps.nixos_mcp,
        "get_sysctl",
        {"host": deps.target_host, "key": deps.os_check_key},
    )
    return OsReading(
        _parse_sysctl_value(_coerce_tool_text(result)) == deps.os_check_expected
    )


async def _describe_node_text(deps: WatchdogDeps) -> str:
    result = await call_tool(
        deps.kubectl_mcp, "describe_node", {"name": deps.target_host}
    )
    return _coerce_tool_text(result)


async def node_condition_fresh(deps: WatchdogDeps) -> bool:
    """Report whether the condition holds and the lease renewed since the first read."""
    loop = asyncio.get_event_loop()
    text = await _describe_node_text(deps)
    floor = _lease_renew_time(text)
    duration = _lease_duration_s(text)
    if floor is None or duration is None:
        return False
    deadline = loop.time() + duration
    while True:
        await asyncio.sleep(POLL_INTERVAL_S)
        text = await _describe_node_text(deps)
        renew = _lease_renew_time(text)
        if (
            renew is not None
            and renew > floor
            and _node_condition_satisfied(
                text, deps.os_check_key, deps.os_check_expected
            )
        ):
            return True
        if loop.time() >= deadline:
            return False


async def os_target_healthy(deps: WatchdogDeps) -> OsReading:
    """Probe the NixOS host for one watchdog poll; a failed read counts as unhealthy."""
    try:
        return await probe_os_target(deps)
    except Exception as exc:
        log.warning("os watchdog probe failed, treating poll as unhealthy: %s", exc)
        return OsReading(False)


class _Streak:
    """Consecutive healthy readings, restarted whenever a stage call returns."""

    def __init__(self, staged_hosts: Sequence[str]) -> None:
        self._staged_hosts = staged_hosts
        self.counted = 0
        self.length = 0
        self.lease_floor: datetime | None = None

    def stages_so_far(self) -> int:
        return len(self._staged_hosts)

    def observe(
        self,
        healthy: bool,
        stages_before: int,
        lease_renew: datetime | None = None,
        lease_required: bool = False,
    ) -> bool:
        stages_now = len(self._staged_hosts)
        if stages_now != self.counted:
            self.length = 0
            self.counted = stages_now
            self.lease_floor = None
        if lease_required:
            moved = (
                lease_renew is not None
                and self.lease_floor is not None
                and lease_renew > self.lease_floor
            )
            if self.lease_floor is None and stages_before == self.counted:
                self.lease_floor = lease_renew
            healthy = healthy and moved
        self.length = (
            self.length + 1 if healthy and stages_before == self.counted else 0
        )
        return self.length >= HEALTHY_STREAK_K


async def _run_os_watchdog(deps: WatchdogDeps) -> WatchdogResult:
    """Poll an OS host with the same window/streak rules as the K8s path."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + WINDOW_S
    streak = _Streak(deps.staged_hosts)
    while loop.time() < deadline:
        stages_before = streak.stages_so_far()
        reading = await os_target_healthy(deps)
        if streak.observe(
            reading.healthy,
            stages_before,
            reading.lease_renew,
            deps.os_check_kind == OS_CHECK_NODE_CONDITION,
        ):
            return WatchdogResult(
                degraded=False,
                snapshot=None,
                reason="healthy",
                stages_seen=streak.counted,
            )
        await asyncio.sleep(POLL_INTERVAL_S)
    return WatchdogResult(
        degraded=True,
        snapshot=None,
        reason="deadline_reached",
        stages_seen=streak.counted,
    )


async def run_watchdog(deps: WatchdogDeps) -> WatchdogResult:
    """Poll until the target is healthy for K consecutive observations.

    Returns degraded=False, reason="healthy" once HEALTHY_STREAK_K consecutive
    healthy polls are seen. Fast-fails with reason="progress_deadline_exceeded"
    when the rollout reports ProgressDeadlineExceeded. On reaching the
    WATCHDOG_WINDOW_S deadline without a healthy streak returns degraded=True,
    reason="deadline_reached" carrying the last snapshot. An indeterminate read
    (HealthSnapshotUnavailable) skips that poll rather than fabricating a result.
    OS host targets (target_host set, target_kind None) verify the declared
    NixOS state via nixos-mcp instead. No rollback action is taken here -- the
    Orchestrator decides. A reading counts only if no stage call returned since
    the streak began, including during that reading.
    """
    if deps.target_host and deps.target_kind is None:
        return await _run_os_watchdog(deps)

    loop = asyncio.get_event_loop()
    deadline = loop.time() + WINDOW_S
    last_snapshot: HealthSnapshot | None = None
    streak = _Streak(deps.staged_hosts)
    while loop.time() < deadline:
        stages_before = streak.stages_so_far()
        try:
            snapshot = await capture_health_snapshot(deps)
        except HealthSnapshotUnavailable as exc:
            log.warning("watchdog poll indeterminate, skipping: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_S)
            continue
        last_snapshot = snapshot
        if snapshot.progress_deadline_exceeded:
            return WatchdogResult(
                degraded=True,
                snapshot=snapshot,
                reason="progress_deadline_exceeded",
                stages_seen=streak.counted,
            )
        healthy = is_workload_healthy(snapshot, deps.expected_revision)
        if streak.observe(healthy, stages_before):
            return WatchdogResult(
                degraded=False,
                snapshot=snapshot,
                reason="healthy",
                stages_seen=streak.counted,
            )
        await asyncio.sleep(POLL_INTERVAL_S)
    return WatchdogResult(
        degraded=True,
        snapshot=last_snapshot,
        reason="deadline_reached",
        stages_seen=streak.counted,
    )
