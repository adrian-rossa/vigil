"""Reusable fault injection and cleanup library keyed by fault_type."""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_HETZNER = "infra/overlays/hetzner/kubernetes/clusters/hetzner"
_APPS = f"{_HETZNER}/apps"
_MONITORING = f"{_HETZNER}/infrastructure/monitoring/kube-prometheus-stack"

GitFaultHandler = Callable[[Path, Mapping[str, Any]], Path]


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)} exited {result.returncode}: {result.stderr.strip()}"
        )
    return result


def _ssh(
    host: str, command: str, *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    ssh_key = os.environ.get("SSH_KEY_PATH", os.path.expanduser("~/.ssh/id_ed25519"))
    return _run(
        [
            "ssh", "-i", ssh_key, "-o",
            "StrictHostKeyChecking=no", "-o", 
            "UserKnownHostsFile=/dev/null",
            f"root@{host}",
            command,
        ],
        check=check,
    )

def _get_params(fault: Mapping[str, Any]) -> dict[str, Any]:
    return dict(fault.get("inject_params") or {})


def _get_manifest_rel(fault: Mapping[str, Any], default: str) -> Path:
    return Path(str(
        fault.get("manifest") 
        or _get_params(fault).get("manifest") 
        or default
        )
    )


def _edit_yaml(path: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    obj = yaml.safe_load(path.read_text())
    if not isinstance(obj, dict):
        raise ValueError(f"Expected mapping YAML in {path}")
    mutate(obj)
    path.write_text(yaml.safe_dump(obj, default_flow_style=False, sort_keys=False))


def _find_container(obj: dict[str, Any], name: str) -> dict[str, Any]:
    containers = obj["spec"]["template"]["spec"]["containers"]
    for container in containers:
        if container.get("name") == name:
            return container
    raise ValueError(f"Container {name!r} not found in manifest")


def _apply_service_target_port(repo_root: Path, fault: Mapping[str, Any]) -> Path:
    rel = _get_manifest_rel(fault, f"{_APPS}/vigil-app-service.yaml")
    target_port = int(_get_params(fault).get("target_port", 8080))

    def _mutate(obj: dict[str, Any]) -> None:
        obj["spec"]["ports"][0]["targetPort"] = target_port

    _edit_yaml(repo_root / rel, _mutate)
    return rel


def _apply_grafana_node_selector(repo_root: Path, fault: Mapping[str, Any]) -> Path:
    rel = _get_manifest_rel(fault, f"{_MONITORING}/values-alertmanager.yaml")
    hostname = str(_get_params(fault).get("hostname", "nonexistent-bogus-node"))

    def _mutate(obj: dict[str, Any]) -> None:
        grafana = obj.setdefault("grafana", {})
        grafana["nodeSelector"] = {"kubernetes.io/hostname": hostname}

    _edit_yaml(repo_root / rel, _mutate)
    return rel


def _apply_container_memory_limit(repo_root: Path, fault: Mapping[str, Any]) -> Path:
    rel = _get_manifest_rel(fault, f"{_APPS}/vigil-app.yaml")
    params = _get_params(fault)
    name = str(params.get("container", "mem-load"))
    limit = str(params.get("memory_limit", "32Mi"))

    def _mutate(obj: dict[str, Any]) -> None:
        container = _find_container(obj, name)
        container.setdefault("resources", {}).setdefault("limits", {})["memory"] = limit

    _edit_yaml(repo_root / rel, _mutate)
    return rel


def _apply_readiness_probe_port(repo_root: Path, fault: Mapping[str, Any]) -> Path:
    rel = _get_manifest_rel(fault, f"{_APPS}/vigil-app.yaml")
    params = _get_params(fault)
    name = str(params.get("container", "vigil-app"))
    port = int(params.get("port", "8080"))

    def _mutate(obj: dict[str, Any]) -> None:
        container = _find_container(obj, name)
        container["readinessProbe"]["httpGet"]["port"] = port

    _edit_yaml(repo_root / rel, _mutate)
    return rel


def _apply_nginx_bad_config(repo_root: Path, fault: Mapping[str, Any]) -> Path:
    rel = _get_manifest_rel(fault, f"{_APPS}/vigil-app-nginx-config.yaml")
    bad_line = str(_get_params(fault).get("bad_directive", "invalid_directive on;"))

    def _mutate(obj: dict[str, Any]) -> None:
        tpl = str(obj["data"]["default.conf.template"])
        if bad_line not in tpl:
            obj["data"]["default.conf.template"] = tpl.replace(
                "server {\n", f"server {{\n     {bad_line}\n", 1
            )
    _edit_yaml(repo_root / rel, _mutate)
    return rel


GIT_FAULT_HANDLERS: dict[str, GitFaultHandler] = {
    "service_target_port": _apply_service_target_port,
    "grafana_node_selector": _apply_grafana_node_selector,
    "container_memory_limit": _apply_container_memory_limit,
    "readiness_probe_port": _apply_readiness_probe_port,
    "nginx_bad_config": _apply_nginx_bad_config,
}

HOST_FAULT_TYPES: frozenset[str] = frozenset({"sysctl_drift"})


def fault_entries(scenario: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return fault dicts from scenario.faults or single-fault top-level config."""
    faults = scenario.get("faults") or []
    if faults:
        return [dict(f) for f in faults]
    if _get_params(scenario).get("fault_type"):
        return [dict(scenario)]
    return []


def has_library_faults(scenario: Mapping[str, Any]) -> bool:
    """True when every declared fault has a registered Python handler"""
    entries = fault_entries(scenario)
    if not entries:
        return False
    known = set(GIT_FAULT_HANDLERS) | set(HOST_FAULT_TYPES)
    return all(_get_params(entry).get("fault_type") in known for entry in entries)


def _reconcile_flux(modified: list[Path], kubeconfig: str) -> None:
    kc = ["--kubeconfig", kubeconfig]
    _run(["flux", "reconcile", "source", "git", "flux-system", 
    "--timeout=60s", *kc,])
    _run(["flux", "reconcile", "kustomization", "flux-system", 
    "-n", "flux-system", "--timeout=60s", *kc,], check=False,)
    posix_paths = [path.as_posix() for path in modified]
    if any(path.startswith(f"{_APPS}/") for path in posix_paths):
        _run(["flux", "reconcile", "kustomization", "cluster-apps", 
        "-n", "flux-system", "--timeout=120s", *kc,], check=False,)
    if any("/infrastructure/" in path for path in posix_paths):
        _run(["flux", "reconcile", "kustomization", "cluster-infrastructure", 
        "-n", "flux-system", "--timeout=120s", *kc,], check=False,)
    if any("kube-prometheus-stack" in path for path in posix_paths):
        _run(["flux", "reconcile", "helmrelease", "kube-prometheus-stack", 
        "-n", "monitoring", "--timeout=120s", *kc,], check=False,)


def inject_faults(
    scenario_id: str,
    scenario: Mapping[str, Any],
    repo_root: Path,
    branch: str,
    kubeconfig: str | None = None,
) -> list[Path]:
    """Apply all git faults in one single commit for host to fault and restart"""
    entries = fault_entries(scenario)
    if not entries:
        raise ValueError(f"Scenario {scenario_id!r} declares no library faults")

    git_entries: list[tuple[str, dict[str, Any]]] = []
    host_entries: list[dict[str, Any]] = []
    for entry in entries:
        fault_type = str(_get_params(entry).get("fault_type") or "")
        if fault_type in GIT_FAULT_HANDLERS:
            git_entries.append((fault_type, entry))
        elif fault_type in HOST_FAULT_TYPES:
            host_entries.append(entry)
        else:
            raise ValueError(f"Unknown fault_type {fault_type!r} in {scenario_id}")

    modified: list[Path] = []
    for fault_type, entry in git_entries:
        rel = GIT_FAULT_HANDLERS[fault_type](repo_root, entry)
        if rel not in modified:
            modified.append(rel)

    if modified:
        git_paths = [path.as_posix() for path in modified]
        _run(["git", "-C", str(repo_root), "add", "--", *git_paths])
        _run(
            [
                "git", "-C", str(repo_root),
                "-c", "user.name=eval-harness",
                "-c", "user.email=eval@vigil.local",
                "commit", "-m", f"{scenario_id}: inject faults",
            ]
        )
        _run(["git", "-C", str(repo_root), "push", "origin", f"HEAD:{branch}"])

    for entry in host_entries:
        params = _get_params(entry)
        host = str(params.get("target_host", "vigil-worker-1"))
        pair = shlex.quote(f"{params['key']}={params['value']}")
        _ssh(host, f"sysctl -w {pair}")

    if modified and kubeconfig:
        _reconcile_flux(modified, kubeconfig)

    if kubeconfig:
        for fault_type, entry in git_entries:
            if fault_type == "nginx_bad_config":
                params = _get_params(entry)
                dep = str(params.get("restart_deployment", "vigil-app"))
                ns = str(params.get("namespace", "default"))
                _run(
                    [
                        "kubectl", "--kubeconfig", kubeconfig,
                        "rollout", "restart", f"deployment/{dep}", "-n", ns,
                    ],
                    check=False
                )

    return modified


def reset_faults(scenario: Mapping[str, Any]) -> list[str]:
    """Shared host-state cleanup for any sysctl_drift faults in the scenario"""
    by_host: dict[str, list[dict[str, Any]]] = {}
    for entry in fault_entries(scenario):
        params = _get_params(entry)
        if params.get("fault_type") == "sysctl_drift":
            host = str(params.get("target_host", "vigil-worker-1"))
            by_host.setdefault(host, []).append(params)
    
    for host, params_list in by_host.items():
        _ssh(
            host,
            "pkill -f '[v]igil-nixos-rebuild systemctl start rollback-gate.timer'"
            " || true",
            check=False,
        )
        _ssh(host, "systemctl stop rollback-gate.timer", check=False)
        for params in params_list:
            if "restore_value" in params:
                pair = shlex.quote(f"{params['key']}={params['restore_value']}")
                _ssh(host, f"sysctl -w {pair} || true", check=False)
        _ssh(
            host,
            "flock /var/lock/vigil-nixos-rebuild nixos-rebuild switch"
            " --flake /opt/nixos-config#$(cat /etc/vigil/flake-attr)",
        )
    return list(by_host)
    