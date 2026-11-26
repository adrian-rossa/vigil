"""LLM-as-a-Judge RCA evaluator ported from SREGym (MIT License)"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from eval import score as score_lib
from eval import timings

UNSCORED_PARSE_FAILURE =  "judge_parse_failure"
UNSCORED_BACKEND_UNAVAILABLE = "judge_backend_unavailable"
DEFAULT_MAX_TOKENS = 16384
MAX_PARSE_ATTEMPTS = 3

JudgeCallFn = Callable[[str, str, int], str]

_SYSTEM_PROMPT = (
    "You are an expert SRE evaluator grading an AI agent's root cause analysis.\n"
    "Compare the Agent Diagnosis against the Ground-Truth Root Cause using ONLY "
    "what is explicitly stated in the diagnosis (do not infer missing facts).\n"
    "For each checklist question return:\n"
    '  {"id": "<QID>", "answer": "Yes|No", "evidence": "<=30 words", '
    '"confidence": "High|Medium|Low"}\n'
    '"Yes" always means the correct/positive property is satisfied.\n'
    "Respond ONLY with a single JSON array of exactly {num_questions} objects: "
    "no markdown fences, no preamble, no commentary."
)

_REL_SYSTEM_PROMPT = (
    "You are an expert SRE evaluator. Read the Agent Diagnosis and decide how "
    "the agent characterizes the relationship between the faults/symptoms.\n"
    "Allowed values for 'relationship':\n"
    "- 'independent': two or more separate root causes\n"
    "- 'cascading': one root cause triggering downstream symptom(s)\n"
    "- 'single': one isolated issue with no relationship described\n"
    "- 'unknown': unclear or empty\n"
    'Respond ONLY with JSON: {"relationship": "...", "evidence": "<=30 words"}'
)


class ChecklistParseError(Exception):
    """Raised when on judge reply is not a complete, valid checklist array"""


class JudgeParseError(Exception):
    """Raised after retries fail, preserves raw_response for audit/reruns"""

    def __init__(self, message: str, *, raw_response: str = "", attempts: int = 0):
        super().__init__(message)
        self.raw_response = raw_response
        self.attempts = attempts


def _default_call_llm(
    model_name: str, system_prompt: str, user_prompt: str, max_tokens: int
) -> str:
    from common.provider import build_model
    from pydantic_ai import Agent

    model = build_model(
        model_name=model_name,
        model_options={"max_tokens": max_tokens},
        temperature=0.0,
    )
    agent = Agent(model, system_prompt=system_prompt)
    result = agent.run_sync(user_prompt)
    return str(result.output)


def extract_json_candidates(text: str) -> Iterator[object]:
    """Get outer JSON arrays/objects from text, ignoring prose/fences"""
    decoder = json.JSONDecoder()
    stack: list[str] = []
    start = 0
    for match in re.finditer(r'"(?:\\.|[^"\\])*"?|[\[\]{}]', text):
        token = match.group()
        if token.startswith('"'):
            continue
        if token in ("[", "{"):
            if not stack:
                start = match.start()
            stack.append("]" if token == "[" else "}")
            continue
        if not stack:
            continue
        if token != stack.pop():
            return
        if not stack:
            try:
                candidate, end = decoder.raw_decode(text, start)
            except (ValueError, RecursionError):
                continue
            if end == match.end():
                yield candidate


def parse_checklist_response(
    text: str, expected_ids: Sequence[str]
) -> list[dict[str, str]]:
    """Return the first valid checklist JSON array matching all expected IDs"""
    expected = set(expected_ids)
    last_err = ChecklistParseError("Invalid JSON: no checklist array found")
    for candidate in extract_json_candidates(text):
        try:
            return _validate_checklist(candidate, expected)
        except ChecklistParseError as exc:
            last_err = exc
    raise last_err


def _validate_checklist(candidate: object, expected: set[str]) -> list[dict[str, str]]:
    if not isinstance(candidate, list):
        raise ChecklistParseError("Response is not a JSON array")
    seen: set[str] = set()
    cleaned: list[dict[str, str]] = []
    for item in candidate:
        if not isinstance(item, dict):
            raise ChecklistParseError("Each checklist item must be an object")
        qid = item.get("id")
        if not isinstance(qid, str) or qid not in expected:
            raise ChecklistParseError(f"Invalid question ID: {qid!r}")
        if qid in seen:
            raise ChecklistParseError(f"Duplicate question ID: {qid}")
        ans = item.get("answer")
        if not isinstance(ans, str) or ans.strip().lower() not in ("yes", "no"):
            raise ChecklistParseError(f"Question {qid} needs a Yes/No answer")
        evidence = str(item.get("evidence") or "")
        confidence = str(item.get("confidence") or "Low")
        seen.add(qid)
        cleaned.append(
            {
                "id": qid,
                "answer": "Yes" if ans.strip().lower() == "yes" else "No",
                "evidence": evidence,
                "confidence": confidence,
            }
        )
    missing = sorted(expected - seen)
    if missing:
        raise ChecklistParseError(f"Missing question IDs: {missing}")
    return cleaned


def build_fault_spec(
    fault: Mapping[str, Any], scenario: Mapping[str, Any]
) -> str:
    """Format SREGym-style fault_spec ground truth from scenario.yaml"""
    params = dict(fault.get("inject_params") or {})
    namespace = str(params.get("namespace") or "default")
    comp = str(fault.get("component") or scenario.get("root_cause_component") or "")
    manifest = str(fault.get("manifest") or params.get("manifest") or "none")
    action = str(fault.get("expected_action") or scenario.get("expected_action") or "")
    desc = str(
        scenario.get("root_cause_component") or scenario.get("name") or comp
    )
    parts = [
        f"target_component={comp}",
        f"namespace={namespace}",
        f"manifest={manifest}",
        f"expected_action={action}",
        f"params={json.dumps(params, sort_keys=True)}",
    ]
    return f"[fault_spec] {'; '.join(parts)} || {desc}"
    

def format_diagnosis_text(reports: Sequence[Mapping[str, Any]]) -> str:
    """Format extracted DiagnosisReport dicts into plain text for judge"""
    blocks: list[str] = []
    for idx, report in enumerate(reports, start=1):
        lines = [
            f"Diagnosis {idx}:",
            f" root_cause: {report.get('root_cause') or ''}"
            f" root_cause_component: {report.get('root_cause_component') or ''}",
            f" recommended_action: {report.get('recommended_action') or ''}",
            f" manifest_path: {report.get('manifest_path') or ''}",
        ]
        evidence = report.get("evidence") or []
        if evidence:
            lines.append(
                " evidence: " + "; ".join(str(entry) for entry in evidence)
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks).strip()


class DiagnosisJudge:
    """Pinned, temp 0 checklist judge for RCA/diagnosis quality"""

    def __init__(
        self,
        model_name: str | None = None,
        *,
        checklist_path: Path | None = None,
        temperature: float = 0.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        call_fn: JudgeCallFn | None = None,
    ) -> None:
        self.model_name = (
            model_name
            if model_name is not None
            else os.environ.get("JUDGE_MODEL_NAME", "")
        )
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._call_fn = call_fn

        cfg_path = checklist_path or Path(__file__).with_name("rca_checklists.yaml")
        self._config: dict[str, Any] = yaml.safe_load(
            cfg_path.read_text(encoding="utf-8")
        )
        self.checklist_version = str(self._config.get("version", "unknown"))
        scoring = self._config.get("scoring") or {}
        self.threshold = float(scoring.get("threshold", 0.70))
        self.weights: dict[str, float] = {
            str(key): float(value) 
            for key, value in (scoring.get("weights") or {}).items()
        }
        self._question_ids: list[str] = [
            str(question["id"])
            for dim in self._config.get("dimensions", [])
            for question in dim.get("questions", [])
        ]
        self._system_prompt = _SYSTEM_PROMPT.replace(
            "{num_questions}", str(len(self._question_ids))
        )

    def _invoke(self, system_prompt: str, user_prompt: str) -> str:
        if self._call_fn is not None:
            return self._call_fn(system_prompt, user_prompt, self.max_tokens)
        if not self.model_name:
            raise RuntimeError(
                "No Judge model pinned (set --model or JUDGE_MODEL_NAME)"
            )
        return _default_call_llm(
            self.model_name, system_prompt, user_prompt, self.max_tokens
        )
    
    def _build_user_prompt(self, diagnosis_text: str, expectation: str) -> str:
        lines = [
            "## Ground-Truth Root Cause",
            expectation,
            "",
            "## Agent Diagnosis",
            diagnosis_text,
            "",
            "## Evaluation Checklist",
        ]
        for dim in self._config.get("dimensions", []):
            lines.append(f"\n### {dim['id']} - {dim['name']}")
            lines.append(str(dim.get("definition", "")).strip())
            for question in dim.get("questions", []):
                lines.append(f"{question['id']}: {question['text']}")
                hint = question.get("evaluator_hint")
                if hint:
                    lines.append(f" Hint: {hint}")
        return "\n".join(lines)

    def _call_with_retry(self, user_prompt: str) -> list[dict[str, str]]:
        prompt = user_prompt
        last_raw = ""
        last_err: Exception | None = None
        for attempt in range(MAX_PARSE_ATTEMPTS):
            last_raw = self._invoke(self._system_prompt, prompt)
            try:
                return parse_checklist_response(last_raw, self._question_ids)
            except ChecklistParseError as exc:
                last_err = exc
                if attempt + 1 == MAX_PARSE_ATTEMPTS:
                    break
                prompt = (
                    f"{user_prompt}\n\nPrevious response could not be parsed: {exc}. "
                    f"Return ONLY a JSON array with all {len(self._question_ids)}" 
                    "question IDs and Yes/No answers."
                )
        raise JudgeParseError(
            f"Unparseable after {MAX_PARSE_ATTEMPTS} attempts: {last_err}",
            raw_response=last_raw,
            attempts=MAX_PARSE_ATTEMPTS,
        )

    def _unscored(self, reason: str, message: str, raw: str = "") -> dict[str, Any]:
        return {
            "verdict": None,
            "composite_score": None,
            "dimensions": {},
            "checklist": [],
            "unscored_reason": reason,
            "error": message,
            "raw_response": raw,
        }

    def grade_fault(self, diagnosis_text: str, expectation: str) -> dict[str, Any]:
        """Grade one fault against the checklist, returns verdict or unscored"""
        if not self._call_fn and not self.model_name:
            return self._unscored(
                UNSCORED_BACKEND_UNAVAILABLE, "No judge model pinned"
            )
        if not diagnosis_text.strip():
            dims = {
                str(dimension["id"]): {"name": dimension["name"], "score": 0.0}
                for dimension in self._config.get("dimensions", [])
            }
            return {
                "verdict": "False",
                "composite_score": 0.0,
                "dimensions": dims,
                "checklist": [],
                "unscored_reason": None,
                "raw_response": "",
            }

        user_prompt = self._build_user_prompt(diagnosis_text, expectation)
        try:
            items = self._call_with_retry(user_prompt)
        except JudgeParseError as exc:
            return self._unscored(
                UNSCORED_PARSE_FAILURE, str(exc), raw=exc.raw_response
            )
        except Exception as exc:
            return self._unscored(UNSCORED_BACKEND_UNAVAILABLE, str(exc))

        by_id = {item["id"]: item for item in items}
        dims_out: dict[str, dict[str, Any]] = {}
        composite = 0.0
        n_dims = max(1, len(self._config.get("dimensions", [])))
        for dim in self._config.get("dimensions", []):
            did = str(dim["id"])
            q_ids = [str(question["id"]) for question in dim.get("questions", [])]
            yes_count = sum(1 for qid in q_ids if by_id[qid]["answer"] == "Yes")
            dim_score = round(yes_count / max(1, len(q_ids)), 2)
            dims_out[did] = {"name": dim["name"], "score": dim_score}
            composite += self.weights.get(did, 1.0 / n_dims) * dim_score
            
        composite = round(composite, 2)
        verdict = "True" if composite >= self.threshold else "False"
        return {
            "verdict": verdict,
            "composite_score": composite,
            "dimensions": dims_out,
            "checklist": items,
            "unscored_reason": None,
            "raw_response": "",
        }

    def grade_relationship(
        self, diagnosis_text: str, expected: str
    ) -> dict[str, Any]:
        """Check whether the diagnosis matches independent vs cascading"""
        if not self._call_fn and not self.model_name:
            return {
                "expected": expected,
                "predicted": None,
                "correct": None,
                "unscored_reason": UNSCORED_BACKEND_UNAVAILABLE,
            }
        if not diagnosis_text.strip():
            return {
                "expected": expected,
                "predicted": "unknown",
                "correct": False,
                "unscored_reason": None,
            }
        prompt = f"## Agent Diagnosis\n{diagnosis_text}"
        try:
            raw = self._invoke(_REL_SYSTEM_PROMPT, prompt)
        except Exception:
            return {
                "expected": expected,
                "predicted": None,
                "correct": None,
                "unscored_reason": UNSCORED_BACKEND_UNAVAILABLE
            }
        for candidate in extract_json_candidates(raw):
            if isinstance(candidate, dict) and isinstance(
                candidate.get("relationship"), str
            ):
                pred = candidate["relationship"].strip().lower()
                return {
                    "expected": expected,
                    "predicted": pred,
                    "correct": pred == expected.lower(),
                    "evidence": str(candidate.get("evidence") or ""),
                    "unscored_reason": None,
                }
        return {
            "expected": expected,
            "predicted": None,
            "correct": None,
            "unscored_reason": UNSCORED_PARSE_FAILURE,
            "raw_response": raw,
        }


def _extract_non_noise_reports(
    attempt: Mapping[str, Any], runs_dir: Path
) -> list[dict[str, Any]]:
    noise_pods = score_lib._noise_pod_names(attempt)
    reports: list[dict[str, Any]] = []
    for rid in attempt.get("run_ids") or []:
        entries = timings.load_trace(runs_dir / f"{rid}_trace.jsonl")
        labels = score_lib._alert_labels(timings.extract_fault_event(entries))
        if score_lib._is_noise_run(labels, noise_pods):
            continue
        report = timings.extract_diagnosis_report(entries)
        if report is not None:
            reports.append(report)
    return reports


def judge_attempt(
    attempt: Mapping[str, Any],
    scenario: Mapping[str, Any],
    runs_dir: Path,
    judge: DiagnosisJudge,
) -> dict[str, Any]:
    """Grade all faults (and relationship) for one attempt"""
    reports = _extract_non_noise_reports(attempt, runs_dir)
    diagnosis_text = format_diagnosis_text(reports)

    raw_faults = list(scenario.get("faults") or [])
    if not raw_faults:
        raw_faults = [
            {
                "id": "a",
                "component": scenario.get("root_cause_component", ""),
                "expected_action": scenario.get("expected_action", ""),
                "inject_params": scenario.get("inject_params") or {},
            }
        ]

    specs = [build_fault_spec(fault, scenario) for fault in raw_faults]
    multi = len(specs) > 1
    full_context = "\n\n".join(
        f"Cause {i}:\n{spec}" for i, spec in enumerate(specs, start=1)
    )

    fault_results: list[dict[str, Any]] = []
    for idx, (fault, spec) in enumerate(zip(raw_faults, specs), start=1):
        if multi:
            expectation = (
                f"This incident has {len(specs)} root causes. Grade ONLY Cause {idx} "
                "below. Mentioning the other known caues is NOT over-attribution, "
                "and omitting them must not lower this causes score.\n\n"
                f"Full incident context:\n{full_context}\n\n"
                f"Evaluation target - Cause {idx}:\n{spec}"
            )
        else:
            expectation = spec
        graded = judge.grade_fault(diagnosis_text, expectation)
        fault_results.append(
            {
                "id": str(fault.get("id") or f"f{idx}"),
                "component": fault.get("component"),
                **graded,
            }
        )

    verdicts = [res["verdict"] for res in fault_results]
    if "False" in verdicts:
        all_attributed: bool | None = False
    elif any(verdict is None for verdict in verdicts):
        all_attributed = None
    else:
        all_attributed = bool(verdicts)

    rel_expected = scenario.get("relationship")
    rel_result = (
        judge.grade_relationship(diagnosis_text, str(rel_expected))
        if rel_expected in ("independent", "cascading")
        else None
    )

    return {
        "scenario": attempt.get("scenario"),
        "seed": attempt.get("seed"),
        "primary_run_id": attempt.get("primary_run_id"),
        "judge_model": judge.model_name,
        "checklist_version": judge.checklist_version,
        "temperature": judge.temperature,
        "all_attributed": all_attributed,
        "faults": fault_results,
        "relationship": rel_result,
        "diagnosis_text": diagnosis_text,
    }


def write_judge(primary_record_path: Path, result: Mapping[str, Any]) -> Path:
    """Persist judge.json next to the run records"""
    out = primary_record_path.with_name(f"{primary_record_path.stem}_judge.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return out