from __future__ import annotations

import json
from pathlib import Path

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