from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import timings


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_extract_fault_event_and_diagnosis_report() -> None:
    fault_json = json.dumps(
        {
            "status": "firing",
            "alerts": [
                {
                    "startsAt": "2026-11-22T10:00:00Z",
                    "labels": {"alertname": "KubePodNotReady", "pod": "vigil-app-1"},
                }
            ],
        }
    )
    entries = [
        {
            "phase": "diagnosis",
            "parts": [
                {
                    "part_kind": "user-prompt",
                    "content": (
                        "Diagnose fault.\n\n"
                        f'Fault: <vigil-data-abc source="fault">{fault_json}'
                        "</vigil-data-abc>\n\nDiagnosisContext:"
                    ),
                    "timestamp": "2026-11-22T10:00:10Z",
                }
            ],
        },
        {
            "phase": "diagnosis",
            "timestamp": "2026-11-22T10:00:40Z",
            "parts": [
                {
                    "part_kind": "tool-call",
                    "tool_name": "final_result",
                    "args": json.dumps(
                        {
                            "root_cause": "targetPort 8080 closed on app-backend",
                            "root_cause_component": "service/app-backend",
                            "recommended_action": "git_commit_k8s",
                        }
                    ),
                }
            ],
        },
    ]

    event = timings.extract_fault_event(entries)
    assert event is not None
    assert event["alerts"][0]["labels"]["alertname"] == "KubePodNotReady"

    report = timings.extract_diagnosis_report(entries)
    assert report is not None
    assert report["root_cause_component"] == "service/app-backend"


def test_compute_attempt_timing_splits_phases_and_headline_mttr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec1 = {
        "run_id": "run-1",
        "started_at": "2026-11-22T10:00:15Z",
        "ended_at": "2026-11-22T10:03:00Z",
        "MTTR_s": 165.0,
        "merge_commit_sha": "abc1234",
        "gate_status": "merged",
    }
    rec2 = {
        "run_id": "run-2",
        "started_at": "2026-11-22T10:04:00Z",
        "ended_at": "2026-11-22T10:05:00Z",
        "MTTR_s": 60.0,
        "outcome": "escalated",
    }
    (tmp_path / "run-1.json").write_text(json.dumps(rec1))
    (tmp_path / "run-2.json").write_text(json.dumps(rec2))

    _write_jsonl(
        tmp_path / "run-1_trace.jsonl",
        [
            {
                "phase": "diagnosis",
                "parts": [
                    {
                        "part_kind": "user-prompt",
                        "content": (
                            'Fault: {"alerts":[{"startsAt":"2026-11-22T10:00:05Z"}]}'
                        ),
                        "timestamp": "2026-11-22T10:00:15Z",
                    }
                ],
            },
            {"phase": "diagnosis", "timestamp": "2026-11-22T10:01:00Z", "parts": []},
            {"phase": "remediation", "timestamp": "2026-11-22T10:01:05Z", "parts": []},
            {"phase": "remediation", "timestamp": "2026-11-22T10:01:45Z", "parts": []},
        ],
    )
    _write_jsonl(
        tmp_path / "run-2_trace.jsonl",
        [
            {"phase": "diagnosis", "timestamp": "2026-11-22T10:04:00Z", "parts": []},
            {"phase": "diagnosis", "timestamp": "2026-11-22T10:04:50Z", "parts": []},
        ],
    )

    # Merge happened 50s after remediation finished (10:02:35Z)
    merge_ts = timings.parse_iso("2026-11-22T10:02:35Z")
    monkeypatch.setattr(timings, "_git_commit_ts", lambda _sha, _root: merge_ts)

    attempt = {
        "scenario": "k8s-pair-1",
        "seed": 1,
        "primary_run_id": "run-1",
        "inject_s": 12.5,
        "injected_at": "2026-11-22T10:00:00Z",
        "run_ids": ["run-1", "run-2"],
        "gaps_s": [225.0],
    }
    probe_green = timings.parse_iso("2026-11-22T10:05:20Z")
    out = timings.compute_attempt_timing(
        attempt, tmp_path, 
        probe_green_ts=probe_green, 
        reconcile_s=5.0, 
        repo_root=tmp_path,
    )

    assert out["inject_s"] == 12.5
    assert out["detect_s"] == 10.0          # 10:00:15 - 10:00:05
    assert out["diagnosis_s"] == 95.0       # 45s + 50s
    assert out["remediation_s"] == 40.0     # 40s + 0s
    assert out["gate_s"] == 50.0            # 10:02:35 - 10:01:45
    assert out["reconcile_s"] == 5.0
    assert out["watchdog_s"] == 20.0        # 10:03:00 - 10:02:35 - 5s
    assert out["active_run_s"] == 225.0     # 165s + 60s
    assert out["lock_wait_s"] == 225.0
    assert out["total_s"] == 320.0          # headline MTTR: 10:05:20 - 10:00:00

    saved = timings.write_timing(tmp_path / "run-1.json", out)
    assert saved.name == "run-1_timing.json"
    assert json.loads(saved.read_text())["total_s"] == 320.0