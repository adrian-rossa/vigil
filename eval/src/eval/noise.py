"""Background distraction noise injector (pod_delete + cpu_stress)"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_BASE_EXCLUDED: tuple[str, ...] = (
    "vigil-app",
    "app-backend",
    "kube-prometheus-stack",
    "grafana",
)

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kubectl(
    args: list[str], kubeconfig: str | None = None
) -> subprocess.CompletedProcess[str]:
    cmd = ["kubectl"]
    if kubeconfig:
        cmd += ["--kubeconfig", kubeconfig]
    return subprocess.run(cmd + args, capture_output=True, text=True, check=False)


def excluded_prefixes(scenario: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    """Return pod-name prefixes that pod_delete shouldn't touch"""
    names = list(_BASE_EXCLUDED)
    if scenario:
        for fault in scenario.get("faults") or []:
            comp = str(fault.get("component") or "")
            short = comp.rsplit("/", 1)[-1].strip()
            if short and short not in names:
                names.append(short)
    return tuple(names)


def pick_unrelated_pod(
    pods_json: str, excluded: Sequence[str], rng: random.Random
) -> str | None:
    """Pick Running pod whose name does not start with excluded prefix"""
    try:
        items = json.loads(pods_json).get("items", [])
    except ValueError:
        return None
    candidates: list[str] = []
    for pod in items:
        if pod.get("status", {}).get("phase") != "Running":
            continue
        name = str(pod.get("metadata", {}).get("name") or "")
        if not name or any(name.startswith(prefix) for prefix in excluded):
            continue
        candidates.append(name)
    return rng.choice(candidates) if candidates else None


def find_running_pod(pods_json: str, prefix: str) -> str | None:
    """Return the first Running pod whose name starts with the prefix"""
    try:
        items = json.loads(pods_json).get("items", [])
    except ValueError:
        return None
    for pod in items:
        if pod.get("status", {}).get("phase") != "Running":
            continue
        name = str(pod.get("metadata", {}).get("name") or "")
        if name.startswith(prefix):
            return name
    return None


class NoiseRunner:
    """Periodic background noise bursts"""

    def __init__(
        self,
        kinds: Sequence[str] = ("pod_delete", "cpu_stress"),
        schedule_s: float = 300.0,
        duration_s: int = 120,
        patterns: int = 2,
        *,
        seed: int = 1,
        initial_delay_s: float = 15.0,
        namespace: str = "default",
        excluded: Sequence[str] = _BASE_EXCLUDED,
        kubeconfig: str | None = None,
    ) -> None:
        self.kinds = list(kinds)
        self.schedule_s = schedule_s
        self.duration_s = duration_s
        self.patterns = patterns
        self.initial_delay_s = initial_delay_s
        self.namespace = namespace
        self.excluded = tuple(excluded)
        self.kubeconfig = kubeconfig

        self._rng = random.Random(seed)
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._procs: list[subprocess.Popen[str]] = []
        self.events: list[dict[str, Any]] = []

    def _inject_pod_delete(self) -> dict[str, Any]:
        listing = _kubectl(
            ["get", "pods", "-n", self.namespace, "-o", "json"], self.kubeconfig
        )
        pod = (
            pick_unrelated_pod(listing.stdout, self.excluded, self._rng)
            if listing.returncode == 0
            else None
        )
        if pod is None:
            return {"ts": _utc_now(), "kind": "pod_delete", "target": None, "ok": False}
        res = _kubectl(
            ["delete", "pod", pod, "-n", self.namespace, "--wait=false"],
            self.kubeconfig,
        )
        return {
            "ts": _utc_now(),
            "kind": "pod_delete",
            "target": f"{self.namespace}/{pod}",
            "ok": res.returncode == 0,
        }

    def _inject_cpu_stress(self) -> dict[str, Any]:
        listing = _kubectl(
            ["get", "pods", "-n", self.namespace, "-o", "json"], self.kubeconfig
        )
        pod = (
            find_running_pod(listing.stdout, "vigil-app")
            if listing.returncode == 0
            else None
        )
        if pod is None:
            return {"ts": _utc_now(), "kind": "cpu_stress", "target": None, "ok": False}
        cmd = ["kubectl"]
        if self.kubeconfig:
            cmd += ["--kubeconfig", self.kubeconfig]
        cmd += [
            "exec",
            "-n",
            self.namespace,
            pod,
            "-c",
            "mem-load",
            "--",
            "stress-ng",
            "--cpu",
            "1",
            "--timeout",
            f"{self.duration_s}s",
        ]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True
            )
            self._procs.append(proc)
            ok = True
        except OSError:
            ok = False
        return {
            "ts": _utc_now(),
            "kind": "cpu_stress",
            "target": f"{self.namespace}/{pod}:mem-load",
            "duration_s": self.duration_s,
            "ok": ok,
        }

    async def trigger_burst(self) -> list[dict[str, Any]]:
        """Execute noise burst (up to self.patterns distinct kinds)"""
        count = min(self.patterns, len(self.kinds))
        chosen = self._rng.sample(self.kinds, count) if count > 0 else []
        burst: list[dict[str, Any]] = []
        for kind in chosen:
            if kind == "pod_delete":
                event = await asyncio.to_thread(self._inject_pod_delete)
            elif kind == "cpu_stress":
                event = await asyncio.to_thread(self._inject_cpu_stress)
            else:
                log.warning("unknown noise kind %r - skipping", kind)
                continue
            self.events.append(event)
            burst.append(event)
            log.info("noise event: %s", event)
        return burst

    async def _wait_or_stop(self, seconds: float) -> bool:
        if seconds <= 0:
            return self._stop_event.is_set()
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
            return True
        except TimeoutError:
            return False

    async def _run_loop(self) -> None:
        if await self._wait_or_stop(self.initial_delay_s):
            # case run is stopped early
            return
        while not self._stop_event.is_set():
            await self.trigger_burst()
            if await self._wait_or_stop(self.schedule_s):
                return

    async def start(self) -> None:
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> dict[str, Any]:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except TimeoutError:
                self._task.cancel()
        for proc in self._procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self._procs.clear()
        return {
            "enabled": True,
            "schedule_s": self.schedule_s,
            "duration_s": self.duration_s,
            "patterns": self.patterns,
            "kinds": list(self.kinds),
            "total_events": len(self.events),
            "events": list(self.events),
        }
