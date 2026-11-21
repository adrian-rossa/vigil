from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from eval import faults

APPS = Path("infra/overlays/hetzner/kubernetes/clusters/hetzner/apps")
MONITORING = Path(
    "infra/overlays/hetzner/kubernetes/clusters/hetzner"
    "/infrastructure/monitoring/kube-prometheus-stack"
)


def _ok(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, 0, "", "")


def test_same_file_git_faults_compose_into_one_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_yaml = tmp_path / APPS / "vigil-app.yaml"
    app_yaml.parent.mkdir(parents=True)
    app_yaml.write_text(
        yaml.safe_dump(
            {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "vigil-app",
                                    "readinessProbe": {"httpGet": {"port": 80}},
                                },
                                {
                                    "name": "mem-load",
                                    "resources": {"limits": {"memory": "96Mi"}},
                                },
                            ]
                        }
                    }
                }
            }
        )
    )

    cmds: list[list[str]] = []
    monkeypatch.setattr(
        faults, "_run", lambda cmd, check=True: cmds.append(cmd) or _ok(cmd)
    )

    scenario: dict[str, Any] = {
        "faults": [
            {"id": "a", "inject_params": {"fault_type": "container_memory_limit"}},
            {"id": "b", "inject_params": {"fault_type": "readiness_probe_port"}},
        ]
    }
    modified = faults.inject_faults("k8s-pair-samefile-1", scenario, tmp_path, "eval-b")

    assert modified == [APPS / "vigil-app.yaml"]
    updated = yaml.safe_load(app_yaml.read_text())
    containers = {
        container["name"]: container for container 
        in updated["spec"]["template"]["spec"]["containers"]
    }
    assert containers["mem-load"]["resources"]["limits"]["memory"] == "32Mi"
    assert containers["vigil-app"]["readinessProbe"]["httpGet"]["port"] == 8080
    assert sum("commit" in cmd for cmd in cmds) == 1
    assert sum("push" in cmd for cmd in cmds) == 1


def test_service_grafana_and_nginx_mutators_reconcile_and_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / APPS).mkdir(parents=True)
    (tmp_path / MONITORING).mkdir(parents=True)
    (tmp_path / APPS / "vigil-app-service.yaml").write_text(
        yaml.safe_dump({"spec": {"ports": [{"port": 80, "targetPort": 80}]}})
    )
    (tmp_path / MONITORING / "values-alertmanager.yaml").write_text(
        yaml.safe_dump({"customRules": {}})
    )
    (tmp_path / APPS / "vigil-app-nginx-config.yaml").write_text(
        yaml.safe_dump(
            {"data": {"default.conf.template": "server {\n    listen 80;\n}\n"}}
        )
    )

    cmds: list[list[str]] = []
    monkeypatch.setattr(
        faults, "_run", lambda cmd, check=True: cmds.append(cmd) or _ok(cmd)
    )

    scenario: dict[str, Any] = {
        "faults": [
            {"id": "a", "inject_params": {"fault_type": "service_target_port"}},
            {"id": "b", "inject_params": {"fault_type": "grafana_node_selector"}},
            {"id": "c", "inject_params": {"fault_type": "nginx_bad_config"}},
        ]
    }
    faults.inject_faults("mix-1", scenario, tmp_path, "eval-b", kubeconfig="/tmp/kc")

    svc = yaml.safe_load((tmp_path / APPS / "vigil-app-service.yaml").read_text())
    mon = yaml.safe_load(
        (tmp_path / MONITORING / "values-alertmanager.yaml").read_text()
    )
    cfg = yaml.safe_load((tmp_path / APPS / "vigil-app-nginx-config.yaml").read_text())

    assert svc["spec"]["ports"][0]["targetPort"] == 8080
    assert mon["grafana"]["nodeSelector"] == {
        "kubernetes.io/hostname": "nonexistent-bogus-node"
    }
    assert "invalid_directive on;" in cfg["data"]["default.conf.template"]
    assert any("cluster-infrastructure" in cmd for cmd in cmds)
    assert any("helmrelease" in cmd for cmd in cmds)
    assert any("rollout" in cmd and "restart" in cmd for cmd in cmds)


def test_sysctl_drift_inject_and_shared_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ssh_calls: list[tuple[str, str]] = []

    def fake_ssh(
        host: str, command: str, *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        ssh_calls.append((host, command))
        return _ok(["ssh", host, command])

    monkeypatch.setattr(faults, "_ssh", fake_ssh)

    scenario: dict[str, Any] = {
        "inject_params": {
            "fault_type": "sysctl_drift",
            "target_host": "vigil-worker-1",
            "key": "net.ipv4.ip_local_port_range",
            "value": "32000 32003",
            "restore_value": "32768 60999",
        }
    }
    faults.inject_faults("os-portrange-1", scenario, tmp_path, "eval-b")
    assert ssh_calls == [
        ("vigil-worker-1", "sysctl -w 'net.ipv4.ip_local_port_range=32000 32003'")
    ]

    ssh_calls.clear()
    hosts = faults.reset_faults(scenario)
    assert hosts == ["vigil-worker-1"]
    cmds = [cmd for _, cmd in ssh_calls]
    assert any("pkill -f" in c for c in cmds)
    assert any("systemctl stop rollback-gate.timer" in c for c in cmds)
    assert any("32768 60999" in c for c in cmds)
    assert any("nixos-rebuild switch" in c for c in cmds)


def test_unknown_fault_type_raises_before_mutating(tmp_path: Path) -> None:
    scenario = {"faults": [{"id": "a", "inject_params": {"fault_type": "bogus"}}]}
    assert not faults.has_library_faults(scenario)
    with pytest.raises(ValueError, match="Unknown fault_type"):
        faults.inject_faults("bad-1", scenario, tmp_path, "eval-b")
