from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import judge


def _checklist_json(yes_ids: set[str]) -> str:
    all_ids = [
        "D1-Q1", "D1-Q2", "D1-Q3",
        "D2-Q1", "D2-Q2", "D2-Q3",
        "D3-Q1", "D3-Q2", "D3-Q3",
    ]
    return json.dumps(
        [
            {
                "id": qid,
                "answer": "Yes" if qid in yes_ids else "No",
                "evidence": "matched",
                "confidence": "High",
            }
            for qid in all_ids
        ]
    )


def _write_trace(path: Path, labels: dict[str, str], report: dict) -> None:
    fault_json = json.dumps({"status": "firing", "alerts": [{"labels": labels}]})
    rows = [
        {
            "phase": "diagnosis",
            "parts": [
                {
                    "part_kind": "user-prompt",
                    "content": f'Fault: <vigil-data>{fault_json}</vigil-data>',
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


def test_grade_fault_scores_dimensions_and_retries_bad_json() -> None:
    calls: list[str] = []
    replies = iter(
        [
            "not valid json at all",
            _checklist_json({
                "D1-Q1", "D1-Q2", "D1-Q3", "D2-Q1", "D2-Q2", "D2-Q3", "D3-Q1", "D3-Q2"
            }),
        ]
    )

    def fake_call(_sys: str, user: str, _max_tokens: int) -> str:
        calls.append(user)
        return next(replies)

    dj = judge.DiagnosisJudge(model_name="pinned-judge-v1", call_fn=fake_call)
    res = dj.grade_fault(
        "targetPort 8080 on service/app-backend", 
        "[fault_spec] target_component=service/app-backend"
    )

    assert len(calls) == 2
    assert "could not be parsed" in calls[1]
    assert res["verdict"] == "True"
    assert res["dimensions"]["D1"]["score"] == 1.0
    assert res["dimensions"]["D3"]["score"] == 0.67
    assert res["composite_score"] == 0.89
    assert res["unscored_reason"] is None


def test_parse_failure_is_unscored_never_zero() -> None:
    dj = judge.DiagnosisJudge(
        model_name="pinned-judge-v1",
        call_fn=lambda _s, _u, _m: "broken truncated output {{{",
    )
    res = dj.grade_fault("some diagnosis", "[fault_spec] x")

    assert res["verdict"] is None
    assert res["composite_score"] is None
    assert res["unscored_reason"] == judge.UNSCORED_PARSE_FAILURE
    assert "broken truncated" in res["raw_response"]


def test_empty_diagnosis_is_graded_zero() -> None:
    dj = judge.DiagnosisJudge(
        model_name="pinned-judge-v1",
        call_fn=lambda _s, _u, _m: "should not be called",
    )
    res = dj.grade_fault("   ", "[fault_spec] x")

    assert res["verdict"] == "False"
    assert res["composite_score"] == 0.0
    assert res["unscored_reason"] is None


def test_judge_attempt_multi_fault_filters_noise_and_checks_relationship(
    tmp_path: Path,
) -> None:
    _write_trace(
        tmp_path / "r1_trace.jsonl",
        {"pod": "vigil-app-1"},
        {
            "root_cause": "app-backend targetPort 8080 closed",
            "root_cause_component": "service/app-backend",
            "recommended_action": "git_commit_k8s",
        },
    )
    _write_trace(
        tmp_path / "r-noise_trace.jsonl",
        {"pod": "redis-master-0"},
        {
            "root_cause": "redis deleted",
            "root_cause_component": "pod/redis-master-0",
            "recommended_action": "escalate",
        },
    )

    prompts_seen: list[str] = []
    all_yes = _checklist_json(
        {
            "D1-Q1", "D1-Q2", "D1-Q3", 
            "D2-Q1", "D2-Q2", "D2-Q3", 
            "D3-Q1", "D3-Q2", "D3-Q3"
        }
    )

    def fake_call(sys_p: str, user_p: str, _m: int) -> str:
        prompts_seen.append(user_p)
        if "relationship" in sys_p:
            return '{"relationship": "independent", "evidence": "two faults"}'
        return all_yes

    dj = judge.DiagnosisJudge(model_name="pinned-judge-v1", call_fn=fake_call)
    scenario = {
        "id": "k8s-pair-1-noise",
        "relationship": "independent",
        "faults": [
            {
                "id": "a", 
                "component": "service/app-backend", 
                "expected_action": "git_commit_k8s"
            },
            {
                "id": "b", 
                "component": "deployment/grafana", 
                "expected_action": "git_commit_k8s"
            },
        ],
    }
    attempt = {
        "scenario": "k8s-pair-1-noise",
        "seed": 1,
        "primary_run_id": "r1",
        "run_ids": ["r1", "r-noise"],
        "noise": {
            "events": [{
                "kind": "pod_delete", 
                "target": "default/redis-master-0", 
                "ok": True
            }]
        },
    }

    out = judge.judge_attempt(attempt, scenario, tmp_path, dj)
    assert out["all_attributed"] is True
    assert len(out["faults"]) == 2
    assert "redis deleted" not in out["diagnosis_text"]
    assert "Full incident context" in prompts_seen[0]
    assert out["relationship"]["correct"] is True

    saved = judge.write_judge(tmp_path / "r1.json", out)
    assert saved.name == "r1_judge.json"


def test_cli_judge_runs_and_skips_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner
    from eval.cli import cli

    scenarios_dir = tmp_path / "scenarios"
    scen_dir = scenarios_dir / "k8s-3g"
    scen_dir.mkdir(parents=True)
    (scen_dir / "scenario.yaml").write_text(
        "id: k8s-3g\n"
        "name: oom\n"
        "faults:\n"
        "  - id: a\n"
        "    component: deployment/vigil-app\n"
        "    expected_action: git_commit_k8s\n",
        encoding="utf-8",
    )

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_trace(
        runs_dir / "r1_trace.jsonl",
        {"pod": "vigil-app-1"},
        {"root_cause": "OOM on mem-load", "root_cause_component": "vigil-app"},
    )
    (runs_dir / "r1_attempt.json").write_text(
        json.dumps(
            {
                "scenario": "k8s-3g",
                "seed": 1,
                "primary_run_id": "r1",
                "run_ids": ["r1"],
            }
        ),
        encoding="utf-8",
    )

    all_yes = _checklist_json(
        {
            "D1-Q1", "D1-Q2", "D1-Q3",
            "D2-Q1", "D2-Q2", "D2-Q3",
            "D3-Q1", "D3-Q2", "D3-Q3",
        }
    )
    monkeypatch.setattr(judge.DiagnosisJudge, "_invoke", lambda _s, _sp, _up: all_yes)

    runner = CliRunner()
    args = [
        "judge",
        "--runs-dir", str(runs_dir),
        "--scenarios-dir", str(scenarios_dir),
        "--model", "pinned-judge-v1",
    ]
    res1 = runner.invoke(cli, args)
    assert res1.exit_code == 0, res1.output
    assert "Judged 1 attempt(s) (skipped 0)" in res1.output
    assert (runs_dir / "r1_judge.json").is_file()

    # Second call without --overwrite skips the existing file.
    res2 = runner.invoke(cli, args)
    assert res2.exit_code == 0, res2.output
    assert "Judged 0 attempt(s) (skipped 1)" in res2.output


def test_cli_kappa_computes_agreement_and_skips_unscored(tmp_path: Path) -> None:
    from click.testing import CliRunner
    from eval.cli import cli

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "r1_judge.json").write_text(
        json.dumps(
            {
                "judge_model": "pinned-judge-v1",
                "checklist_version": "3.0",
                "faults": [
                    {"id": "a", "verdict": "True"},
                    {"id": "b", "verdict": "False"},
                    {"id": "c", "verdict": None, 
                    "unscored_reason": "judge_parse_failure"
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    labels_file = tmp_path / "labels.jsonl"
    labels_file.write_text(
        "\n".join(
            [
                json.dumps({
                    "primary_run_id": "r1", 
                    "fault_id": "a", 
                    "human_verdict": True
                }),
                json.dumps({
                    "primary_run_id": "r1", 
                    "fault_id": "b", 
                    "human_verdict": False
                }),
                json.dumps({
                    "primary_run_id": "r1", 
                    "fault_id": "c", 
                    "human_verdict": True
                }),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    res = CliRunner().invoke(
        cli,
        ["kappa", "--labels-file", str(labels_file), "--runs-dir", str(runs_dir)],
    )
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["n_paired"] == 2
    assert out["n_unscored_skipped"] == 1
    assert out["agreement_rate"] == 1.0
    assert out["cohens_kappa"] == 1.0
    assert out["judge_models"] == ["pinned-judge-v1"]