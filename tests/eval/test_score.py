from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import score, timings


def _write_trace(path: Path, labels: dict[str, str], report: dict) -> None:
    fault_json = json.dumps({"status": "firing", "alerts": [{"labels": labels}]})
    rows = [
        {
            "phase": "diagnosis",
            "parts": [
                {
                    "part_kind": "user-prompt",
                    "content": (
                        f'Fault: <vigil-data source="fault">{fault_json}</vigil-data>'
                    ),
                }
            ],
        },
        {
            "phase": "diagnosis",
            "parts": [
                {
                    "part_kind": "tool-call",
                    "tool_name": "final_result",
                    "args": json.dumps(report),
                }
            ],
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_poll_health_probes_requires_consecutive_streak() -> None:
    # Transient pass, then failure (resets streak), then 3 consecutive passes.
    answers = iter([True, False, True, True, True])
    clock = [1000.0]

    def fake_sleep(dt: float) -> None:
        clock[0] += dt

    faults = [
        {
            "id": "a",
            "health_probe": {
                "kind": "http_ok", 
                "url": "http://vigil-app.default.svc/", 
                "window_s": 60,
            },
        }
    ]
    onset_iso = score._utc_iso(950.0)
    res = score.poll_health_probes(
        faults,
        injected_at=onset_iso,
        probe_fn=lambda _p, _kc: next(answers),
        interval_s=5.0,
        required_streak=3,
        sleep_fn=fake_sleep,
        now_fn=lambda: clock[0],
    )

    assert res["a"]["remediated"] is True
    # 4 sleeps of 5s -> confirmed at t=1020.0, minus onset 950.0 = 70.0s
    assert res["a"]["time_to_restore_s"] == 70.0


def test_score_attempt_multi_fault_noise_collision_and_run_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = {
        "id": "k8s-pair-1-noise",
        "faults": [
            {
                "id": "a",
                "component": "service/app-backend",
                "manifest": "apps/app-backend.yaml",
                "expected_action": "git_commit_k8s",
                "root_cause_keywords": ["targetPort", "app-backend"],
                "severity": "user_facing",
            },
            {
                "id": "b",
                "component": "deployment/kube-prometheus-stack-grafana",
                "manifest": "values-alertmanager.yaml",
                "expected_action": "git_commit_k8s",
                "root_cause_keywords": ["nodeSelector", "grafana"],
                "severity": "internal",
            },
        ],
    }

    for rid, inc, sha in [
        ("run-a", "inc-a", "sha-a"),
        ("run-noise", "inc-n", None),
        ("run-b", "inc-b", "sha-b"),
    ]:
        rec = {"run_id": rid, "incident_id": inc, "agent_commits": [sha] if sha else []}
        (tmp_path / f"{rid}.json").write_text(json.dumps(rec), encoding="utf-8")

    _write_trace(
        tmp_path / "run-a_trace.jsonl",
        {"alertname": "KubePodNotReady", "pod": "vigil-app-1"},
        {
            "root_cause": "Service/app-backend targetPort 8080 is closed",
            "root_cause_component": "service/app-backend",
            "manifest_path": "apps/app-backend.yaml",
            "recommended_action": "git_commit_k8s",
        },
    )
    _write_trace(
        tmp_path / "run-noise_trace.jsonl",
        {"alertname": "KubePodNotReady", "pod": "redis-master-0"},
        {
            "root_cause": "redis pod deleted",
            "root_cause_component": "pod/redis-master-0",
            "recommended_action": "escalate",
        },
    )
    _write_trace(
        tmp_path / "run-b_trace.jsonl",
        {"alertname": "KubePodNotReady", "pod": "grafana-1"},
        {
            "root_cause": "Grafana nodeSelector points to bogus node",
            "root_cause_component": "deployment/kube-prometheus-stack-grafana",
            "manifest_path": "values-alertmanager.yaml",
            "recommended_action": "git_commit_k8s",
        },
    )

    commits = {
        "sha-a": ["apps/app-backend.yaml"], 
        "sha-b": ["values-alertmanager.yaml"],
    }
    monkeypatch.setattr(score, "_git_touched_paths", lambda sha, _r: commits[sha])

    attempt = {
        "scenario": "k8s-pair-1-noise",
        "seed": 1,
        "primary_run_id": "run-a",
        "detection_used": "alert",
        "run_ids": ["run-a", "run-noise", "run-b"],
        "noise": {
            "enabled": True,
            "events": [{
                "kind": "pod_delete", 
                "target": "default/redis-master-0", 
                "ok": True,
            }],
        },
    }
    probes = {
        "a": {
            "remediated": True, 
            "passed_at": "2026-11-25T10:02:00Z", 
            "time_to_restore_s": 120.0,
        },
        "b": {
            "remediated": True, 
            "passed_at": "2026-11-25T10:05:00Z", 
            "time_to_restore_s": 300.0,
        },
    }

    out = score.score_attempt(
        attempt, scenario, tmp_path, probe_results=probes, repo_root=tmp_path
    )
    assert out["noise_run_ids"] == ["run-noise"]
    assert out["fault_run_ids"] == ["run-a", "run-b"]
    assert out["n_detected"] == 2
    assert out["n_attributed"] == 2
    assert out["n_remediated"] == 2
    assert out["all_remediated"] is True
    assert out["time_to_last_fix_s"] == 300.0
    assert out["collision"] is False

    score_path = score.write_score(tmp_path / "run-a.json", out)
    assert score_path.name == "run-a_score.json"

    score.update_run_map(tmp_path, attempt)
    rmap = score.load_run_map(tmp_path)
    assert rmap == {
        "run-a": "k8s-pair-1-noise",
        "run-b": "k8s-pair-1-noise",
        "run-noise": "k8s-pair-1-noise",
    }


def test_collision_ignores_retries_but_flags_distinct_incidents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        score, 
        "_git_touched_paths", 
        lambda _sha, 
        _r: ["apps/vigil-app.yaml"]
    )

    retry_records = [
        {"run_id": "inc1", "incident_id": "inc1", "agent_commits": ["sha1"]},
        {"run_id": "inc1-try-2", "incident_id": "inc1", "agent_commits": ["sha2"]},
    ]
    assert score.detect_collisions(
        score.touched_paths_by_incident(retry_records, tmp_path)
    ) == []

    two_incidents = [
        {"run_id": "inc1", "incident_id": "inc1", "agent_commits": ["sha1"]},
        {"run_id": "inc2", "incident_id": "inc2", "agent_commits": ["sha2"]},
    ]
    assert score.detect_collisions(
        score.touched_paths_by_incident(two_incidents, tmp_path)
    ) == ["apps/vigil-app.yaml"]
    assert timings.parse_iso("2026-11-25T10:00:00Z") is not None


def test_cli_score_rescores_saved_attempts_offline(tmp_path: Path) -> None:
    from click.testing import CliRunner
    from eval.cli import cli

    scenarios_dir = tmp_path / "scenarios"
    scen_dir = scenarios_dir / "k8s-3g"
    scen_dir.mkdir(parents=True)
    (scen_dir / "scenario.yaml").write_text(
        "id: k8s-3g\n"
        "faults:\n"
        "  - id: a\n"
        "    component: deployment/vigil-app\n"
        "    expected_action: git_commit_k8s\n"
        "    root_cause_keywords: [memory, OOM]\n",
        encoding="utf-8",
    )

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "r1.json").write_text(
        json.dumps(
            {
                "run_id": "r1",
                "started_at": "2026-11-25T10:00:10Z",
                "ended_at": "2026-11-25T10:01:00Z",
                "MTTR_s": 50.0,
            }
        ),
        encoding="utf-8",
    )
    _write_trace(
        runs_dir / "r1_trace.jsonl",
        {"alertname": "KubeContainerOOMKilled"},
        {
            "root_cause": "OOM on vigil-app",
            "root_cause_component": "deployment/vigil-app",
            "recommended_action": "git_commit_k8s",
        },
    )
    green_ts = timings.parse_iso("2026-11-25T10:01:15Z")
    (runs_dir / "r1_attempt.json").write_text(
        json.dumps(
            {
                "scenario": "k8s-3g",
                "seed": 1,
                "primary_run_id": "r1",
                "detection_used": "alert",
                "inject_s": 5.0,
                "injected_at": "2026-11-25T10:00:00Z",
                "run_ids": ["r1"],
                "gaps_s": [],
                "probe_results": {
                    "a": {
                        "remediated": True,
                        "passed_at": "2026-11-25T10:01:15Z",
                        "passed_ts": green_ts,
                        "time_to_restore_s": 75.0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    res = CliRunner().invoke(
        cli,
        [
            "score",
            "--runs-dir",
            str(runs_dir),
            "--scenarios-dir",
            str(scenarios_dir),
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "Scored 1 attempt(s)" in res.output
    assert json.loads(
        (runs_dir / "r1_score.json").read_text()
    )["time_to_last_fix_s"] == 75.0
    assert json.loads((runs_dir / "r1_timing.json").read_text())["total_s"] == 75.0
    assert json.loads((runs_dir / "run_map.json").read_text()) == {"r1": "k8s-3g"}