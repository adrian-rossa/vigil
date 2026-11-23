from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml
from common.constants import SHUTDOWN_DRAIN_S
from orchestrator.agent import MAX_TRIES, _write_run_record, build_run_id, retry_run_id
from orchestrator.models import RunRecord

from eval import faults as fault_lib
from eval.noise import NoiseRunner, excluded_prefixes
from eval.traffic import SteppedTrafficRunner

_HARNESS_WAIT_BUFFER_S = 300
_RESULT_POLL_INTERVAL_S = 2
_WAIT_LOG_INTERVAL_S = 30
DEFAULT_TIMEOUT_S = 600.0
DEFAULT_ORCHESTRATOR_URL = "http://localhost:9099"
DEFAULT_EVAL_BRANCH = "chore/eval-cluster-baseline"
BASELINE_KUSTOMIZATIONS = ("cluster-apps", "cluster-infrastructure")
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_EXIT_CODE = 10
DEFAULT_CIRCUIT_BREAKER_STATE_PATH = "/tmp/vigil-circuit-breaker.json"
_TRUNC = 300
_DEFAULT_ALERT_NAME = "VigilFaultDetected"
_ALERT_DETECTION_TIMEOUT_S = 120.0
_FINGERPRINT_LEN = 16

log = logging.getLogger(__name__)

_INJECT_ASSERT_TIMEOUT_S = 90
_INJECT_ASSERT_POLL_INTERVAL_S = 5
_INJECT_FLUX_UPTAKE_TIMEOUT_S = 300
_FLUX_NAMESPACE = "flux-system"
_FLUX_APPS_KUSTOMIZATION = BASELINE_KUSTOMIZATIONS[0]


class InjectAssertionFailed(RuntimeError):
    """Raised when the cluster does not show the expected failure within the timeout."""


def eval_branch(environ: Mapping[str, str]) -> str:
    return environ.get("VIGIL_EVAL_BRANCH") or DEFAULT_EVAL_BRANCH


def _kubectl(
    args: list[str], kubeconfig: str | None = None
) -> subprocess.CompletedProcess[str]:
    cmd = ["kubectl"]
    if kubeconfig:
        cmd += ["--kubeconfig", kubeconfig]
    return subprocess.run(cmd + args, capture_output=True, text=True, check=False)


def _ssh(host: str, command: str) -> subprocess.CompletedProcess[str]:
    ssh_key = os.environ.get("SSH_KEY_PATH", os.path.expanduser("~/.ssh/id_ed25519"))
    return subprocess.run(
        [
            "ssh",
            "-i",
            ssh_key,
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            f"root@{host}",
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _discover_failing_pod(
    namespace: str, deployment: str, kubeconfig: str | None
) -> str | None:
    """Return the first non-Running pod name matching the deployment app label."""
    r = _kubectl(
        ["get", "pods", "-n", namespace, "-l", f"app={deployment}", "-o", "json"],
        kubeconfig,
    )
    if r.returncode != 0:
        return None
    try:
        items = json.loads(r.stdout).get("items", [])
    except json.JSONDecodeError:
        return None
    for pod in items:
        if pod.get("status", {}).get("phase", "") != "Running":
            return pod["metadata"]["name"]
    if items:
        return items[0]["metadata"]["name"]
    return None


def _check_pod_not_ready(verify: dict, ns: str, kubeconfig: str | None) -> bool:
    deployment = verify["deployment"]
    r = _kubectl(["get", "deployment", deployment, "-n", ns, "-o", "json"], kubeconfig)
    if r.returncode != 0:
        return False
    obj = json.loads(r.stdout)
    spec_replicas = obj.get("spec", {}).get("replicas", 1)
    avail = obj.get("status", {}).get("availableReplicas", spec_replicas)
    if avail < spec_replicas:
        return True
    for cond in obj.get("status", {}).get("conditions", []):
        if cond.get("type") == "ReplicaFailure" and cond.get("status") == "True":
            return True
    match_labels = obj.get("spec", {}).get("selector", {}).get("matchLabels") or {
        "app": deployment,
    }
    selector = ",".join(f"{k}={v}" for k, v in sorted(match_labels.items()))
    r2 = _kubectl(
        ["get", "pods", "-n", ns, "-l", selector, "-o", "json"],
        kubeconfig,
    )
    if r2.returncode != 0:
        return False
    for pod in json.loads(r2.stdout).get("items", []):
        phase = pod.get("status", {}).get("phase", "")
        if phase in ("Pending", "Failed", "Unknown"):
            return True
        for cs in pod.get("status", {}).get("containerStatuses", []):
            if not cs.get("ready", True):
                return True
    return False


def _check_replica_failure(verify: dict, ns: str, kubeconfig: str | None) -> bool:
    deployment = verify["deployment"]
    r = _kubectl(["get", "deployment", deployment, "-n", ns, "-o", "json"], kubeconfig)
    if r.returncode != 0:
        return False
    for cond in json.loads(r.stdout).get("status", {}).get("conditions", []):
        if cond.get("type") == "ReplicaFailure" and cond.get("status") == "True":
            return True
    return False


def _check_deployment_unavailable(
    verify: dict, ns: str, kubeconfig: str | None
) -> bool:
    deployment = verify["deployment"]
    r = _kubectl(["get", "deployment", deployment, "-n", ns, "-o", "json"], kubeconfig)
    if r.returncode != 0:
        return False
    obj = json.loads(r.stdout)
    spec_replicas = obj.get("spec", {}).get("replicas", 0)
    avail = obj.get("status", {}).get("availableReplicas", 0)
    return spec_replicas > 0 and avail == 0


def _check_kustomization_suspended(
    verify: dict, ns: str, kubeconfig: str | None
) -> bool:
    name = verify["name"]
    kust_ns = verify.get("kustomization_namespace", "flux-system")
    r = _kubectl(
        ["get", "kustomization", name, "-n", kust_ns, "-o", "json"], kubeconfig
    )
    if r.returncode != 0:
        return False
    return json.loads(r.stdout).get("spec", {}).get("suspend", False) is True


def _check_kustomization_not_ready(
    verify: dict, ns: str, kubeconfig: str | None
) -> bool:
    name = verify["name"]
    kust_ns = verify.get("kustomization_namespace", "flux-system")
    r = _kubectl(
        ["get", "kustomization", name, "-n", kust_ns, "-o", "json"], kubeconfig
    )
    if r.returncode != 0:
        return False
    for cond in json.loads(r.stdout).get("status", {}).get("conditions", []):
        if cond.get("type") == "Ready" and cond.get("status") == "False":
            return True
    return False


def _check_node_not_ready(verify: dict, ns: str, kubeconfig: str | None) -> bool:
    node = verify["node"]
    r = _kubectl(["get", "node", node, "-o", "json"], kubeconfig)
    if r.returncode != 0:
        return False
    for cond in json.loads(r.stdout).get("status", {}).get("conditions", []):
        if cond.get("type") == "Ready" and cond.get("status") != "True":
            return True
    return False


def _check_node_disk_pressure(verify: dict, ns: str, kubeconfig: str | None) -> bool:
    node = verify["node"]
    r = _kubectl(["get", "node", node, "-o", "json"], kubeconfig)
    if r.returncode != 0:
        return False
    for cond in json.loads(r.stdout).get("status", {}).get("conditions", []):
        if cond.get("type") == "DiskPressure" and cond.get("status") == "True":
            return True
    return False


def _check_systemd_unit_inactive(verify: dict, ns: str, kubeconfig: str | None) -> bool:
    host = verify["host"]
    unit = verify["unit"]
    r = _ssh(host, f"systemctl is-active {unit}")
    return r.returncode != 0


def _check_sysctl_modified(verify: dict, ns: str, kubeconfig: str | None) -> bool:
    host = verify["host"]
    key = verify["key"]
    expected = str(verify["expected_value"])
    r = _ssh(host, f"sysctl -n {key}")
    return r.returncode == 0 and " ".join(r.stdout.split()) == " ".join(
        expected.split()
    )


_SYMPTOM_CHECKS = {
    "pod_not_ready": _check_pod_not_ready,
    "replica_failure": _check_replica_failure,
    "deployment_unavailable": _check_deployment_unavailable,
    "kustomization_suspended": _check_kustomization_suspended,
    "kustomization_not_ready": _check_kustomization_not_ready,
    "node_not_ready": _check_node_not_ready,
    "node_disk_pressure": _check_node_disk_pressure,
    "systemd_unit_inactive": _check_systemd_unit_inactive,
    "sysctl_modified": _check_sysctl_modified,
}


def _svc_proxy_path(url: str) -> str | None:
    """Convert http://<svc>.<ns>.svc[:<port>]/<path> to a k8s API proxy path"""
    if url.startswith("/api/v1/namespaces/"):
        return url
    parsed = urlsplit(url)
    parts = (parsed.hostname or "").split(".")
    if len(parts) < 3 or parts[2] != "svc":
        return None
    svc, ns = parts[0], parts[1]
    port = parsed.port or 80
    subpath = parsed.path.lstrip("/")
    return f"/api/v1/namespaces/{ns}/services/{svc}:{port}/proxy/{subpath}"
    

def _probe_passes(probe: dict, kubeconfig: str | None = None) -> bool:
    """True when the faults declared healthy state is positively observed"""
    kind = probe.get("kind")

    if kind == "http_ok":
        url = str(probe["url"])
        proxy_path = _svc_proxy_path(url)
        if proxy_path is not None:
            r = _kubectl(["get", "--raw", proxy_path], kubeconfig)
            return r.returncode == 0
        r = subprocess.run(
            [
                "curl", "-sf", "-o", "/dev/null", "-w", "%{http_code}",
                "--max-time", "10", url,
            ],
            capture_output=True, text=True, check=False,
        )
        return r.returncode == 0 and r.stdout.strip() == str(probe.get("expect", 200))

    if kind == "kube_condition":
        resource = str(probe["resource"])
        rkind, _, name = resource.partition("/")
        ns = probe.get("namespace", "default")
        r = _kubectl(["get", rkind, name, "-n", ns, "-o", "json"], kubeconfig)
        if r.returncode != 0:
            return False
        obj = json.loads(r.stdout)
        condition = probe.get("condition")
        if condition is None:
            spec = obj.get("spec", {}).get("replicas", 0)
            avail = obj.get("status", {}).get("availableReplicas", 0)
            unavail = obj.get("status", {}).get("unavailableReplicas", 0)
            return spec > 0 and avail == spec and unavail == 0
        for c in obj.get("status", {}).get("conditions", []):
            if c.get("type") == condition:
                return str(c.get("status")) == str(probe.get("expect", "True"))
        return False

    if kind == "host_cmd":
        r = _ssh(str(probe["host"]), str(probe["command"]))
        return r.returncode == 0 and r.stdout.strip() == str(probe.get("expect", ""))

    return False


def _symptom_observed(verify: dict, kubeconfig: str | None = None) -> bool:
    """Return True when the fault symptom declared in verify_broken is present."""
    symptom = verify.get("symptom", "")
    ns = verify.get("namespace", "default")

    check = _SYMPTOM_CHECKS.get(symptom)
    if check is None:
        log.warning("unknown symptom kind %r - skipping assertion", symptom)
        return True
    return check(verify, ns, kubeconfig)


def _wait_for_inject_symptom(
    scenario_id: str, verify: dict, scenarios_dir: Path
) -> None:
    """Poll until the declared failure symptom is observed or the timeout expires."""
    kubeconfig = os.environ.get("EVAL_RUNNER_KUBECONFIG") or None
    timeout = int(verify.get("timeout_s", _INJECT_ASSERT_TIMEOUT_S))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _symptom_observed(verify, kubeconfig):
            log.info(
                "inject-assert: symptom %r confirmed for %s",
                verify["symptom"],
                scenario_id,
            )
            return
        time.sleep(_INJECT_ASSERT_POLL_INTERVAL_S)
    raise InjectAssertionFailed(
        f"scenario {scenario_id}: symptom {verify['symptom']!r} not observed"
        f" within {timeout}s"
    )


def _repo_head() -> str | None:
    repo_root = os.environ.get("VIGIL_REPO_ROOT")
    if not repo_root:
        return None
    result = subprocess.run(
        ["git", "-C", repo_root, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _flux_has_taken_up(sha: str, kubeconfig: str | None) -> bool:
    result = _kubectl(
        [
            "get",
            "kustomization",
            _FLUX_APPS_KUSTOMIZATION,
            "-n",
            _FLUX_NAMESPACE,
            "-o",
            "json",
        ],
        kubeconfig,
    )
    if result.returncode != 0:
        return False
    try:
        kustomization = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    if kustomization.get("spec", {}).get("suspend"):
        return True
    attempted = kustomization.get("status", {}).get("lastAttemptedRevision") or ""
    return attempted.endswith(f":{sha}")


def _wait_for_flux_uptake(scenario_id: str, sha: str) -> None:
    kubeconfig = os.environ.get("EVAL_RUNNER_KUBECONFIG") or None
    deadline = time.monotonic() + _INJECT_FLUX_UPTAKE_TIMEOUT_S
    while time.monotonic() < deadline:
        if _flux_has_taken_up(sha, kubeconfig):
            log.info("inject-assert: flux took up %s for %s", sha, scenario_id)
            return
        time.sleep(_INJECT_ASSERT_POLL_INTERVAL_S)
    raise InjectAssertionFailed(
        f"scenario {scenario_id}: flux did not take up {sha}"
        f" within {_INJECT_FLUX_UPTAKE_TIMEOUT_S}s"
    )


def _emit_inject_failed_record(
    scenario_id: str,
    seed: int,
    model: str,
    reason: str,
    runs_dir: Path | None = None,
) -> Path:
    run_id, seed_str, sha7 = build_run_id(scenario_id, model, seed=seed)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = RunRecord(
        run_id=run_id,
        scenario=scenario_id,
        seed=seed_str,
        model=model,
        git_sha7=sha7,
        started_at=now,
        ended_at=now,
        outcome="inject_did_not_break",
        success_rate=False,
        diagnosis_accuracy=None,
        MTTR_s=None,
        destructive_repair=False,
        rollback_triggered=False,
        rollback_success=None,
        total_input_tokens=0,
        total_output_tokens=0,
        total_tool_calls=0,
        iteration_count=0,
        autonomy_level="full",
        actions_taken=[],
        setup_error=reason,
        model_version=model,
    )
    _write_run_record(record)
    if runs_dir is None:
        runs_dir = Path(os.environ.get("EVAL_RUNS_DIR", "eval/runs"))
    return runs_dir / f"{run_id}.json"


def _script_path(scenarios_dir: Path, scenario_id: str, name: str) -> Path:
    return scenarios_dir / scenario_id / name


def _run_script(path: Path, seed: int, verbose: bool = False) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"script not found: {path}")
    result = subprocess.run(
        [str(path), str(seed)],
        capture_output=True,
        text=True,
        check=False,
    )
    if verbose:
        if result.stdout:
            log.debug("%s stdout:\n%s", path.name, result.stdout.rstrip())
        if result.stderr:
            log.debug("%s stderr:\n%s", path.name, result.stderr.rstrip())
    if result.returncode != 0:
        raise RuntimeError(
            f"{path} exited {result.returncode}: {result.stderr.strip()}"
        )


def _capture_kust_condition(name: str) -> dict:
    kubeconfig = os.environ.get("EVAL_RUNNER_KUBECONFIG", "")
    cmd = ["kubectl"]
    if kubeconfig:
        cmd += ["--kubeconfig", kubeconfig]
    cmd += ["get", "kustomization", name, "-n", "flux-system", "-o", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {
            "ready": "Unknown",
            "reason": "kubectl_error",
            "message": result.stderr.strip()[:500],
        }
    try:
        obj = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return {
            "ready": "Unknown",
            "reason": "kubectl_error",
            "message": str(result.stdout)[:500],
        }
    conditions = obj.get("status", {}).get("conditions", [])
    for cond in conditions:
        if cond.get("type") == "Ready":
            return {
                "ready": cond.get("status", "Unknown"),
                "reason": cond.get("reason", ""),
                "message": cond.get("message", ""),
            }
    return {"ready": "Unknown", "reason": "no_ready_condition", "message": ""}


async def capture_flux_baseline() -> tuple[dict, bool]:
    baseline: dict = {}
    baseline["cluster_apps"] = _capture_kust_condition(BASELINE_KUSTOMIZATIONS[0])
    baseline["cluster_infra"] = _capture_kust_condition(BASELINE_KUSTOMIZATIONS[1])
    all_ready = (
        baseline["cluster_apps"]["ready"] == "True"
        and baseline["cluster_infra"]["ready"] == "True"
    )
    if not all_ready:
        log.warning(
            "cluster baseline not ready:"
            " cluster-apps=%s(reason=%s) cluster-infra=%s(reason=%s)",
            baseline["cluster_apps"]["ready"],
            baseline["cluster_apps"]["reason"],
            baseline["cluster_infra"]["ready"],
            baseline["cluster_infra"]["reason"],
        )
    return baseline, all_ready


def _emit_baseline_degraded_record(
    scenario_id: str,
    seed: int,
    model: str,
    baseline: dict,
    runs_dir: Path | None = None,
) -> Path:
    run_id, seed_str, sha7 = build_run_id(scenario_id, model, seed=seed)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if baseline["cluster_infra"]["ready"] != "True":
        sub_reason = "cluster_infrastructure_not_ready"
    elif baseline["cluster_apps"]["ready"] != "True":
        sub_reason = "cluster_apps_not_ready"
    else:
        sub_reason = "unknown"
    record = RunRecord(
        run_id=run_id,
        scenario=scenario_id,
        seed=seed_str,
        model=model,
        git_sha7=sha7,
        started_at=now,
        ended_at=now,
        outcome="baseline_degraded",
        success_rate=False,
        diagnosis_accuracy=None,
        MTTR_s=None,
        destructive_repair=False,
        rollback_triggered=False,
        rollback_success=None,
        total_input_tokens=0,
        total_output_tokens=0,
        total_tool_calls=0,
        iteration_count=0,
        autonomy_level="full",
        actions_taken=[],
        setup_error=sub_reason,
        model_version=model,
    )
    _write_run_record(record)
    if runs_dir is None:
        runs_dir = Path(os.environ.get("EVAL_RUNS_DIR", "eval/runs"))
    return runs_dir / f"{run_id}.json"


def _load_circuit_breaker_count() -> int:
    state_path = os.environ.get(
        "VIGIL_CIRCUIT_BREAKER_STATE", DEFAULT_CIRCUIT_BREAKER_STATE_PATH
    )
    try:
        with open(state_path) as f:
            loaded = json.load(f)
        return int(loaded.get("consecutive_baseline_degraded", 0))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return 0


def _record_circuit_breaker_baseline_degraded() -> int:
    state_path = os.environ.get(
        "VIGIL_CIRCUIT_BREAKER_STATE", DEFAULT_CIRCUIT_BREAKER_STATE_PATH
    )
    new_count = _load_circuit_breaker_count() + 1
    try:
        with open(state_path, "w") as f:
            json.dump({"consecutive_baseline_degraded": new_count}, f)
    except OSError:
        pass
    return new_count


def _reset_circuit_breaker() -> None:
    state_path = os.environ.get(
        "VIGIL_CIRCUIT_BREAKER_STATE", DEFAULT_CIRCUIT_BREAKER_STATE_PATH
    )
    try:
        with open(state_path, "w") as f:
            json.dump({"consecutive_baseline_degraded": 0}, f)
    except OSError:
        pass


_POD_SCOPED_ALERTS: frozenset[str] = frozenset(
    {"KubePodCrashLooping", "KubePodNotReady", "KubeContainerWaiting"}
)

_K8S_LABEL_KEYS: frozenset[str] = frozenset(
    {
        "deployment",
        "kustomization",
        "namespace",
        "pod",
        "statefulset",
        "daemonset",
        "name",
        "service",
    }
)


def _load_scenario(scenario_id: str) -> dict[str, Any]:
    scenarios_dir = Path(os.environ.get("VIGIL_SCENARIOS_DIR", "eval/scenarios"))
    scenario_yaml = scenarios_dir / scenario_id / "scenario.yaml"
    if scenario_yaml.exists():
        with scenario_yaml.open() as f:
            return yaml.safe_load(f) or {}
    return {}


def _alert_fingerprint(labels: dict[str, str]) -> str:
    """Derive an opaque fingerprint from the sorted label set, as Alertmanager does."""
    digest_input = ",".join(f"{k}={labels[k]}" for k in sorted(labels))
    return hashlib.sha256(digest_input.encode()).hexdigest()[:_FINGERPRINT_LEN]


def _build_fault_event(
    scenario_id: str, 
    target_host: str | None = None,
    fault: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    kubeconfig = os.environ.get("EVAL_RUNNER_KUBECONFIG") or None
    scenario = _load_scenario(scenario_id)
    source = fault if fault is not None else scenario
    alert_name = (
        source.get("alert_name")
        or scenario.get("alert_name")
        or _DEFAULT_ALERT_NAME
    )
    inject_params: dict[str, Any] = (
        source.get("inject_params") or scenario.get("inject_params") or {}
    )
    host = (
        target_host or (fault.get("inject_params") or {}).get("target_host")
        if fault is not None
        else target_host
    )
    labels: dict[str, str] = {"alertname": alert_name}

    if alert_name in _POD_SCOPED_ALERTS:
        namespace = str(inject_params.get("namespace", "default"))
        deployment = str(inject_params.get("deployment", ""))
        labels["namespace"] = namespace
        if deployment:
            pod_name = _discover_failing_pod(namespace, deployment, kubeconfig)
            if pod_name:
                labels["pod"] = pod_name
            else:
                labels["deployment"] = deployment
    else:
        for key in _K8S_LABEL_KEYS:
            if key in inject_params:
                labels[key] = str(inject_params[key])

    if host:
        labels["node"] = str(host)
    return {
        "receiver": "vigil-webhook",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": labels,
                "annotations": {"summary": f"{alert_name} firing"},
                "startsAt": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "endsAt": "",
                "generatorURL": "",
                "fingerprint": _alert_fingerprint(labels),
            }
        ],
        "groupLabels": {"alertname": alert_name},
        "commonLabels": labels,
        "commonAnnotations": {},
        "externalURL": "",
        "version": "4",
        "groupKey": f'{{}}:{{alertname="{alert_name}"}}',
        "truncatedAlerts": 0,
    }


async def _healthz_check(client: httpx.AsyncClient, orchestrator_url: str) -> float:
    try:
        r = await client.get(f"{orchestrator_url}/healthz", timeout=5)
        r.raise_for_status()
    except httpx.ConnectError as e:
        raise RuntimeError(
            f"Orchestrator not reachable at {orchestrator_url} - start it first "
            f"(uv run uvicorn orchestrator.main:app --port 9099). Underlying: {e}"
        ) from e
    return float(r.json()["run_timeout_s"])


def _emit_trace_line(line: str) -> None:
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return
    phase = entry.get("phase", "?")
    for part in entry.get("parts", []):
        kind = part.get("part_kind") or part.get("type", "")
        if kind == "tool-call":
            args = part.get("args", {})
            if isinstance(args, dict) and "args_dict" in args:
                args = args["args_dict"]
            args_s = json.dumps(args) if isinstance(args, dict) else str(args)
            if len(args_s) > _TRUNC:
                args_s = args_s[:_TRUNC] + "..."
            log.info("[%s] -> %s(%s)", phase, part.get("tool_name", "?"), args_s)
        elif kind == "tool-return":
            content = str(part.get("content", ""))
            if len(content) > _TRUNC:
                content = content[:_TRUNC] + "..."
            log.debug("[%s] <- %s: %s", phase, part.get("tool_name", "?"), content)
        elif kind == "text":
            content = part.get("content", "").strip()
            if content:
                if len(content) > _TRUNC:
                    content = content[:_TRUNC] + "..."
                log.debug("[%s] model: %s", phase, content)


async def _tail_trace(trace_path: Path, stop: asyncio.Event) -> None:
    while not trace_path.exists():
        if stop.is_set():
            return
        await asyncio.sleep(0.5)
    offset = 0
    while True:
        with trace_path.open() as f:
            f.seek(offset)
            for raw in f:
                raw = raw.strip()
                if raw:
                    _emit_trace_line(raw)
            offset = f.tell()
        if stop.is_set():
            return
        await asyncio.sleep(0.5)


def is_final_record(record: Mapping[str, Any]) -> bool:
    return not record.get("retry_queued")


async def _wait_for_record(
    runs_dir: Path,
    run_id: str, 
    timeout_s: float, 
    verbose: bool,
    window_start: float | None = None,
) -> dict[str, Any]:
    result_path = runs_dir / f"{run_id}.json"
    stop = asyncio.Event()
    tail: asyncio.Task[None] | None = None
    if verbose:
        tail = asyncio.create_task(
            _tail_trace(runs_dir / f"{run_id}_trace.jsonl", stop)
        )

    loop = asyncio.get_event_loop()
    started = loop.time()
    prev = started
    last_log = started
    elapsed = 0.0
    max_wait_s = timeout_s * 4
    try:
        while elapsed < timeout_s and (prev - started) < max_wait_s:
            if result_path.exists():
                try:
                    return json.loads(result_path.read_text())
                except ValueError:
                    log.debug("record %s is still being written", result_path)
            await asyncio.sleep(_RESULT_POLL_INTERVAL_S)
            now = loop.time()
            other_active = window_start is not None and any(
                run != run_id for run in _in_flight_traces(runs_dir, window_start)
            )
            if not other_active:
                elapsed += max(0.0, now - prev)
            prev = now
            if now - last_log >= _WAIT_LOG_INTERVAL_S:
                log.debug(
                    "still waiting for %s ... %.0fs elapsed", run_id, elapsed
                )
                last_log = now
        raise TimeoutError(
            f"No result file for run_id={run_id} within {timeout_s}s at {result_path}"
        )
    finally:
        stop.set()
        if tail is not None:
            await tail


def _ts(record: dict) -> float | None:
    """converts started_at to a POSIX timestamp, None when missing or not parseable"""
    raw = record.get("started_at")
    if not raw: 
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _records_in_window(runs_dir: Path, since: float, until: float) -> list[dict]:
    """Every RunRecord whose started_at falls inside test time window"""
    found: list[dict] = []
    for path in sorted(runs_dir.glob("*.json")):
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        started = rec.get("started_at")
        if not started:
            continue
        ts = _ts(rec)
        if ts and since <= ts <= until:
            found.append(rec)
    return sorted(found, key=lambda r: r.get("started_at") or "")


def _log_run_gaps(records: list[dict]) -> list[float]:
    """Log n_runs and Return start-to-start gaps (=RUN_LOCK waiting time)"""
    log.info("window contains %d run(s)", len(records))
    gaps: list[float] = []
    for prev, cur in zip(records, records[1:]):
        prev_ts, cur_ts = _ts(prev), _ts(cur)
        if prev_ts is None or cur_ts is None:
            log.info("gap %s -> %s: unknown", prev.get("run_id"), cur.get("run_id"))
            continue
        gap_s = round(cur_ts - prev_ts, 3)
        gaps.append(gap_s)
        log.info("gap %s -> %s: %.1fs", prev.get("run_id"), cur.get("run_id"), gap_s)
    return gaps


def _write_attempt(
    path: Path,
    scenario_id: str,
    seed: int,
    inject_s: float,
    records: list[dict],
    gaps_s: list[float],
    detection_used: str = "synthetic",
    traffic: dict[str, Any] | None = None,
    noise: dict[str, Any] | None = None,
    injected_at: str | None = None,
) -> Path: 
    """Persist attempt-level metadata next to the primary run record"""
    attempt_path = path.with_name(f"{path.stem}_attempt.json")
    attempt_path.parent.mkdir(parents=True, exist_ok=True)
    run_ids = [str(r["run_id"]) for r in records if r.get("run_id")]
    payload = {
        "scenario": scenario_id,
        "seed": seed,
        "primary_run_id": path.stem,
        "detection_used": detection_used,
        "inject_s": round(inject_s, 3),
        "n_runs": len(records),
        "run_ids": run_ids,
        "gaps_s": gaps_s,
    }
    if injected_at is not None:
        payload["injected_at"] = injected_at
    if traffic is not None:
        payload["traffic"] = traffic
    if noise is not None:
        payload["noise"] = noise
    attempt_path.write_text(json.dumps(payload, indent=2) + "\n")
    return attempt_path


def _in_flight_traces(runs_dir: Path, since: float) -> list[str]:
    """Return active runs that have a recent _trace.jsonl but no final.json"""
    active: list[str] = []
    for trace_path in sorted(runs_dir.glob("*_trace.jsonl")):
        try:
            if trace_path.stat().st_mtime < since:
                continue
        except OSError:
            continue
        run_id = trace_path.name.removesuffix("_trace.jsonl")
        if not (runs_dir / f"{run_id}.json").exists():
            active.append(run_id)
    return active


async def _wait_for_drain(
    runs_dir: Path,
    since: float,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> None:
    """Wait until every inflight run in window has written .json"""
    started = asyncio.get_event_loop().time()
    while True:
        active = _in_flight_traces(runs_dir, since)
        if not active:
            return
        if asyncio.get_event_loop().time() - started >= timeout_s:
            log.warning("drain timeout with remaining active runs: %s", active)
            return
        log.info("waiting for queued run(s) to finish: %s", active)
        await asyncio.sleep(_RESULT_POLL_INTERVAL_S)


async def _follow_run(
    runs_dir: Path, 
    run_id: str, 
    per_try_s: float, 
    verbose: bool, 
    window_start: float | None = None,
) -> Path:
    """Wait for run and any queued retries until final record is written"""
    current = run_id 
    while True:
        record = await _wait_for_record(
            runs_dir, current, per_try_s, verbose, window_start
        )
        if is_final_record(record):
            return runs_dir / f"{current}.json"
        current = retry_run_id(record["incident_id"], record["attempts"] + 1)
        log.info("run_id=%s - polling for the next try", current)


def _detected_run_id(runs_dir: Path, since: float) -> str | None:
    """Return first run_id started/tracing in runs_dir since window_start"""
    records = _records_in_window(runs_dir, since, time.time() + 5)
    if records and records[0].get("run_id"):
        return str(records[0]["run_id"])
    active = _in_flight_traces(runs_dir, since)
    return active[0] if active else None


async def _wait_for_alert_run(
    runs_dir: Path,
    since: float,
    per_try_s: float,
    detect_timeout_s: float = _ALERT_DETECTION_TIMEOUT_S,
    verbose: bool = False,
) -> Path | None:
    """Wait for Alertmanager to start run, return None if no alert arrived in time"""
    started = asyncio.get_event_loop().time()
    while True:
        run_id = _detected_run_id(runs_dir, since)
        if run_id is not None:
            log.info("detected real alert run_id %s", run_id)
            return await _follow_run(
                runs_dir, run_id, per_try_s, verbose, window_start=since
            )
        if asyncio.get_event_loop().time() - started >= detect_timeout_s:
            log.warning(
                "no alert run within %.0fs - falling back to synthetic webhook",
                detect_timeout_s,
            )
            return None
        await asyncio.sleep(_RESULT_POLL_INTERVAL_S)


async def trigger_and_wait(
    scenario_id: str,
    seed: int,
    orchestrator_url: str,
    runs_dir: Path,
    model: str,
    timeout_s: int | None = None,
    verbose: bool = False,
    target_host: str | None = None,
    faults: list[dict[str, Any]] | None = None,
    window_start: float | None = None,
) -> Path:
    webhook_secret = os.environ.get("VIGIL_WEBHOOK_SECRET", "")
    if not webhook_secret:
        raise ValueError("VIGIL_WEBHOOK_SECRET is not set")
    multi = faults is not None and len(faults) > 1
    events = (
        [
            _build_fault_event(scenario_id, target_host=target_host, fault=f)
            for f in faults
        ]
        if multi and faults
        else [_build_fault_event(scenario_id, target_host=target_host)]
    )
    params: dict[str, Any] = {"scenario": scenario_id, "model": model}
    if not multi:
        params["seed"] = seed
    
    run_ids: list[str] = []
    async with httpx.AsyncClient() as client:
        run_timeout_s = await _healthz_check(client, orchestrator_url)
        for fault_event in events:
            log.info("triggering orchestrator webhook for %s", scenario_id)
            resp = await client.post(
                f"{orchestrator_url}/webhook",
                params=params,
                json=fault_event,
                headers={"Authorization": f"Bearer {webhook_secret}"},
                timeout=720,
            )
            resp.raise_for_status()
            run_ids.append(resp.json()["run_id"])

    per_try_s = (
        timeout_s
        if timeout_s is not None
        else run_timeout_s + SHUTDOWN_DRAIN_S + _HARNESS_WAIT_BUFFER_S
    )
    for rid in run_ids:
        log.info("run_id=%s - polling for result", rid)
        (runs_dir / f"{rid}.json").unlink(missing_ok=True)
        for try_number in range(2, MAX_TRIES + 1):
            stale = retry_run_id(rid, try_number)
            (runs_dir / f"{stale}.json").unlink(missing_ok=True)
            (runs_dir / f"{stale}_trace.jsonl").unlink(missing_ok=True)

    paths = [
        await _follow_run(
            runs_dir, rid, per_try_s, verbose, window_start
            ) 
            for rid in run_ids
            ]
    return paths[0]


def _build_traffic_runner(
    data: Mapping[str, Any],
    workload_override: str | None,
    kubeconfig: str | None,
) -> SteppedTrafficRunner | None:
    cfg = dict(data.get("workload") or {})
    enabled = (
        workload_override == "stepped"
        if workload_override is not None
        else bool(cfg.get("enabled")) and cfg.get("mode", "stepped") == "stepped"
    )
    if not enabled:
        return None
    return SteppedTrafficRunner(
        profile=list(cfg.get("profile") or [50,200,50]),
        ramp_s=float(cfg.get("ramp_s", 10.0)),
        kubeconfig=kubeconfig,
    )
   

def _build_noise_runner(
    data: Mapping[str, Any],
    noise_override: str | None,
    seed: int,
    kubeconfig: str | None,
) -> NoiseRunner | None:
    cfg = dict(data.get("noise") or {})
    enabled = noise_override if noise_override is not None else bool(cfg.get("enabled"))
    if not enabled:
        return None
    return NoiseRunner(
        kinds=list(cfg.get("kinds") or ["pod_delete", "cpu_stress"]),
        schedule_s=float(cfg.get("schedule_s", 300.0)),
        duration_s=int(cfg.get("duration_s", 120.0)),
        patterns=int(cfg.get("patterns", 2)),
        seed=seed,
        excluded=excluded_prefixes(data),
        kubeconfig=kubeconfig,
    )


async def run_one(
    scenario_id: str,
    seed: int,
    scenarios_dir: Path,
    model: str,
    orchestrator_url: str = DEFAULT_ORCHESTRATOR_URL,
    runs_dir: Path | None = None,
    timeout_s: int | None = None,
    verbose: bool = False,
    workload_override: str | None = None,
    noise_override: bool | None = None,
) -> Path:
    if runs_dir is None:
        runs_dir = Path(os.environ.get("EVAL_RUNS_DIR", "eval/runs"))

    reset_baseline_sh = scenarios_dir.parent / "scripts" / "reset-eval-baseline.sh"
    wait_flux_ready_sh = scenarios_dir.parent / "scripts" / "wait-flux-ready.sh"
    reset_sh = _script_path(scenarios_dir, scenario_id, "reset.sh")
    inject_sh = _script_path(scenarios_dir, scenario_id, "inject.sh")

    scenario_yaml = scenarios_dir / scenario_id / "scenario.yaml"
    data: dict[str, Any] = {}
    if scenario_yaml.exists():
        with scenario_yaml.open() as file:
            data = yaml.safe_load(file) or {}
    use_fault_lib = fault_lib.has_library_faults(data)

    if reset_baseline_sh.is_file():
        log.info(
            "resetting %s before %s",
            eval_branch(os.environ),
            scenario_id,
        )
        _run_script(reset_baseline_sh, seed, verbose=verbose)

    if wait_flux_ready_sh.is_file():
        log.info("waiting for flux ready before %s", scenario_id)
        result = subprocess.run(
            [str(wait_flux_ready_sh)],
            capture_output=not verbose,
            text=True,
            check=False,
        )
        if verbose and result.stdout:
            log.debug("wait-flux-ready stdout:\n%s", result.stdout.rstrip())
        if result.returncode != 0:
            raise RuntimeError(
                f"wait-flux-ready.sh exited {result.returncode}: "
                + (result.stderr.strip() if result.stderr else "")
            )

    if reset_sh.is_file():
        log.info("running reset.sh for %s", scenario_id)
        _run_script(reset_sh, seed, verbose=verbose)
    elif use_fault_lib:
        log.info("running Python fault reset for %s", scenario_id)
        fault_lib.reset_faults(data)
    else:
        raise FileNotFoundError(f"script not found: {reset_sh}")

    baseline, all_ready = await capture_flux_baseline()
    if not all_ready:
        result_path = _emit_baseline_degraded_record(
            scenario_id, seed, model, baseline, runs_dir
        )
        new_count = _record_circuit_breaker_baseline_degraded()
        log.error(
            "campaign baseline_degraded count=%d/%d (scenario=%s)",
            new_count,
            CIRCUIT_BREAKER_THRESHOLD,
            scenario_id,
        )
        if new_count >= CIRCUIT_BREAKER_THRESHOLD:
            log.error(
                "Campaign aborted: %d consecutive baseline_degraded"
                " - check cluster-infrastructure readiness before retrying",
                new_count,
            )
            sys.exit(CIRCUIT_BREAKER_EXIT_CODE)
        return result_path

    _reset_circuit_breaker()

    kubeconfig = os.environ.get("EVAL_RUNNER_KUBECONFIG") or None
    traffic_runner = _build_traffic_runner(data, workload_override, kubeconfig)
    noise_runner = _build_noise_runner(data, noise_override, seed, kubeconfig)
    traffic_started = False
    noise_started = False
    inject_ok = False
    inject_s = 0.0
    window_start = time.time() - 5
    path: Path | None = None
    detection_used = "synthetic"

    try:
        if traffic_runner is not None:
            log.info("starting stepped traffic runner for %s", scenario_id)
            await traffic_runner.start()
            traffic_started = True

        head_before = _repo_head()
        window_start = time.time() -5
        inject_start = time.monotonic()
        if inject_sh.is_file():
            log.info("running inject.sh for %s", scenario_id)
            _run_script(inject_sh, seed, verbose=verbose)
        elif use_fault_lib:
            log.info("running Python fault injection for %s", scenario_id)
            repo_root = Path(os.environ.get("VIGIL_REPO_ROOT") or ".")
            fault_lib.inject_faults(
                scenario_id,
                data,
                repo_root,
                eval_branch(os.environ),
                kubeconfig=kubeconfig,
            )
        else:
            raise FileNotFoundError(f"script not found: {inject_sh}")
        inject_s = time.monotonic() - inject_start
        injected_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        head_after = _repo_head()
        log.info("inject.sh for %s took %.1fs", scenario_id, inject_s)

        target_host = (data.get("inject_params") or {}).get("target_host")
        verify_broken = data.get("verify_broken") or None
        faults = list(data.get("faults") or [])
        detection = str(data.get("detection") or "synthetic")

        verify_checks = [verify_broken] if verify_broken else []
        verify_checks.extend(
            f["verify_broken"] for f in faults if f.get("verify_broken")
        )

        try:
            if head_before and head_after and head_after != head_before:
                _wait_for_flux_uptake(scenario_id, head_after)
            if verify_checks:
                for check in verify_checks:
                    _wait_for_inject_symptom(scenario_id, check, scenarios_dir)
            else:
                log.warning(
                    "no verify_broken block for %s - skipping inject assertion",
                    scenario_id,
                )
        except InjectAssertionFailed as exc:
            log.error("inject-assert failed for %s: %s", scenario_id, exc)
            return _emit_inject_failed_record(
                scenario_id, seed, model, str(exc), runs_dir
            )

        inject_ok = True
        if noise_runner is not None:
            log.info("starting background noise runner for %s", scenario_id)
            await noise_runner.start()
            noise_started = True

        if detection == "alert":
            path = await _wait_for_alert_run(
                runs_dir,
                window_start,
                timeout_s or DEFAULT_TIMEOUT_S,
                verbose = verbose,
            )
            if path is not None:
                detection_used = "alert"
        if path is None:
            path = await trigger_and_wait(
                scenario_id=scenario_id,
                seed=seed,
                orchestrator_url=orchestrator_url,
                runs_dir = runs_dir,
                model = model,
                timeout_s = timeout_s,
                verbose = verbose,
                target_host = target_host,
                faults = faults or None,
                window_start=window_start,
            )
        await _wait_for_drain(runs_dir, window_start, timeout_s or DEFAULT_TIMEOUT_S)
    finally:
        noise_summary: dict[str, Any] | None = None
        traffic_summary: dict[str, Any] | None = None
        if noise_runner is not None and noise_started:
            noise_summary = await noise_runner.stop()
        if traffic_runner is not None and traffic_started:
            traffic_summary = await traffic_runner.stop()
        if inject_ok:
            records = _records_in_window(runs_dir, window_start, time.time() + 5)
            gaps_s =_log_run_gaps(records)
            attempt_target = path or (
                runs_dir / f"{records[0]['run_id']}.json"
                if records and records[0].get("run_id")
                else None
            )
            if attempt_target is not None:
                _write_attempt(
                    attempt_target, 
                    scenario_id, 
                    seed, 
                    inject_s, 
                    records,
                    gaps_s, 
                    detection_used,
                    traffic=traffic_summary,
                    noise=noise_summary,
                    injected_at=injected_at,
                )
            log.info("inject_s=%.1f n_runs=%d", inject_s, len(records))
    assert path is not None
    return path
