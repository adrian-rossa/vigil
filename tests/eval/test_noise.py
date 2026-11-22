from __future__ import annotations

import json
import random
import subprocess
from unittest.mock import MagicMock

import pytest

from eval import noise


def test_excluded_prefixes_protects_grafana_and_fault_components() -> None:
    scenario = {
        "faults": [
            {"component": "deployment/custom-svc"},
            {"component": "vigil-worker-1"},
        ]
    }
    excluded = noise.excluded_prefixes(scenario)
    assert "grafana" in excluded
    assert "vigil-app" in excluded
    assert "custom-svc" in excluded
    assert "vigil-worker-1" in excluded


def test_pick_unrelated_pod_skips_excluded_and_non_running() -> None:
    pods = json.dumps(
        {
            "items": [
                {
                    "metadata": {"name": "vigil-app-abc"}, 
                    "status": {"phase": "Running"}
                },
                {
                    "metadata": {"name": "grafana-1"}, 
                    "status": {"phase": "Running"}
                },
                {
                    "metadata": {"name": "postgresql-0"}, 
                    "status": {"phase": "Pending"}
                },
                {
                    "metadata": {"name": "redis-master-0"}, 
                    "status": {"phase": "Running"}
                },
            ]
        }
    )
    chosen = noise.pick_unrelated_pod(
        pods, noise.excluded_prefixes(), random.Random(1)
    )
    assert chosen == "redis-master-0"


async def test_trigger_burst_runs_both_patterns_and_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pods_json = json.dumps(
        {
            "items": [
                {
                    "metadata": {"name": "vigil-app-xyz"}, 
                    "status": {"phase": "Running"}
                },
                {
                    "metadata": {"name": "redis-master-0"}, 
                    "status": {"phase": "Running"}
                },
            ]
        }
    )
    kubectl_calls: list[list[str]] = []

    def fake_kubectl(
        args: list[str], kubeconfig: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        kubectl_calls.append(args)
        stdout = pods_json if args[:2] == ["get", "pods"] else ""
        return subprocess.CompletedProcess(["kubectl", *args], 0, stdout, "")

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    popen_cmds: list[list[str]] = []

    def fake_popen(cmd: list[str], **_kwargs: object) -> MagicMock:
        popen_cmds.append(cmd)
        return fake_proc

    monkeypatch.setattr(noise, "_kubectl", fake_kubectl)
    monkeypatch.setattr(noise.subprocess, "Popen", fake_popen)

    runner = noise.NoiseRunner(seed=1, duration_s=120, patterns=2)
    burst = await runner.trigger_burst()
    summary = await runner.stop()

    kinds = {e["kind"] for e in burst}
    assert kinds == {"pod_delete", "cpu_stress"}
    assert all(e["ok"] for e in burst)
    assert any(c[:3] == ["delete", "pod", "redis-master-0"] for c in kubectl_calls)
    assert any("stress-ng" in c and "120s" in c for c in popen_cmds)
    fake_proc.terminate.assert_called_once()
    assert summary["total_events"] == 2