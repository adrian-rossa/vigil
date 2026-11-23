"""Trace extraction and phase-timing breakdown for eval runs"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any


def parse_iso(raw: str | None) -> float | None:
    """Parse an ISO Timestamp string to POSIX Seconds or none"""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def load_trace(trace_path: Path) -> list[dict[str, Any]]:
    """Read a <run_id>_trace.jsonl file into a list of message dicts"""
    if not trace_path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for raw in trace_path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries


def extract_fault_event(entries: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Extract the fenced FaultEvent JSON from first diagnosis user-prompt"""
    decoder = json.JSONDecoder()
    for entry in entries:
        if entry.get("phase") != "diagnosis":
            continue
        for part in entry.get("parts") or []:
            if part.get("part_kind") != "user-prompt":
                continue
            content = str(part.get("content") or "")
            marker = content.find("Fault:")
            if marker == -1:
                continue
            brace = content.find("{", marker)
            if brace == -1:
                continue
            try: 
                obj, _ = decoder.raw_decode(content, brace)
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


def extract_diagnosis_report(
    entries: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    """Extract the final DiagnosisReport args emitted during diagnosis"""
    report: dict[str, Any] | None = None
    for entry in entries:
        if entry.get("phase") != "diagnosis":
            continue
        for part in entry.get("parts") or []:
            if(
                part.get("part_kind") == "tool-call"
                and part.get("tool_name") == "final_result"
            ):
                args = part.get("args")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        continue
                if isinstance(args, dict):
                    report = args
    return report


def entry_timestamps(entry: Mapping[str, Any]) -> list[float]:
    stamps: list[float] = []
    top = parse_iso(entry.get("timestamp"))
    if top is not None:
        stamps.append(top)
    for part in entry.get("parts") or []:
        if isinstance(part, Mapping):
            ts = parse_iso(part.get("timestamp"))
            if ts is not None:
                stamps.append(ts)
    return stamps


def phase_bounds(
    entries: Sequence[dict[str, Any]], phase: str
) -> tuple[float | None, float | None]:
    """Return earliest/latest ts for all messages belonging to a phase"""
    stamps: list[float] = []
    for entry in entries:
        if entry.get("phase") == phase:
            stamps.extend(entry_timestamps(entry))
    if not stamps:
        return None, None
    return min(stamps), max(stamps)


def _git_commit_ts(sha: str | None, repo_root: Path | None) -> float | None:
    if not sha or repo_root is None:
        return None
    res =  subprocess.run(
        ["git", "-C", str(repo_root), "show", "-s", "--format=%ct", sha],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        return None
    try:
        return float(res.stdout.strip())
    except ValueError:
        return None


def _round(value: float | None) -> float | None:
    return round(max(0.0, value), 3) if value is not None else None


def compute_run_timing(
    record: Mapping[str, Any],
    trace_entries: Sequence[dict[str, Any]],
    *,
    inject_s: float | None = None,
    injected_at: str | None = None,
    probe_green_ts: float | None = None,
    reconcile_s: float | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Compute phase timers for a single RunRecord and its trace"""
    started_ts = parse_iso(record.get("started_at"))
    ended_ts = parse_iso(record.get("ended_at"))
    injected_at = parse_iso(injected_at)

    fault_event = extract_fault_event(trace_entries)
    alert_starts_ts: float | None = None
    if fault_event:
        alerts = fault_event.get("alerts") or []
        if alerts and isinstance(alerts[0], Mapping):
            alert_starts_ts = parse_iso(alerts[0].get("startsAt"))
    # Detection Phase
    detect_anchor = alert_starts_ts if alert_starts_ts is not None else injected_at
    detect_s = (
        started_ts - detect_anchor
        if started_ts is not None and detect_anchor is not None
        else None
    )
    # Diagnosis Phase
    diag_start, diag_end = phase_bounds(trace_entries, "diagnosis")
    diagnosis_s = (
        diag_end - diag_start
        if diag_start is not None and diag_end is not None
        else None
    )
    # Remediation Phase
    rem_start, rem_end = phase_bounds(trace_entries, "remediation")
    has_remediation = rem_start is not None and rem_end is not None
    remediation_s = (rem_end - rem_start) if has_remediation else 0.0

    # Post Remediation (Gate, Flux, Watchdog)
    merge_sha = record.get("merge_commit_sha")
    is_git_run = bool(merge_sha or record.get("gate_status"))
    merge_ts = _git_commit_ts(str(merge_sha), repo_root) if merge_sha else None

    if not has_remediation:
        # Escalated
        gate_s: float | None = 0.0
        rec_s: float | None = 0.0
        watchdog_s: float | None = 0.0
    elif is_git_run:
        # Git Remediation
        gate_s = (
            merge_ts - rem_end
            if merge_ts is not None and rem_end is not None
            else None
        )
        rec_s = reconcile_s
        post_anchor = merge_ts if merge_ts is not None else rem_end
        if ended_ts is not None and post_anchor is not None:
            watchdog_s = ended_ts - post_anchor - (rec_s or 0.0)
        else:
            watchdog_s = None
    else:
        # Non-Git Remediation (nixos_rebuild)
        gate_s = 0.0
        rec_s = 0.0
        watchdog_s = (
            ended_ts - rem_end
            if ended_ts is not None and rem_end is not None
            else None
        )
    # MTTR
    active_run = record.get("MTTR_s")
    active_run_s = float(active_run) if isinstance(active_run, (int, float)) else None

    end_anchor = probe_green_ts if probe_green_ts is not None else ended_ts
    start_anchor = injected_at if injected_at is not None else started_ts
    total_s = (
        end_anchor - start_anchor
        if end_anchor is not None and start_anchor is not None
        else active_run_s
    )

    return {
        "run_id": record.get("run_id"),
        "inject_s": _round(inject_s),
        "detect_s": _round(detect_s),
        "diagnosis_s": _round(diagnosis_s),
        "remediation_s": _round(remediation_s),
        "gate_s": _round(gate_s),
        "reconcile_s": _round(rec_s),
        "watchdog_s": _round(watchdog_s),
        "active_run_s": _round(active_run_s),
        "total_s": _round(total_s),
    }


def _sum_known(rows: Sequence[Mapping[str]], key: str) -> float | None:
    vals = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
    return round(sum(vals), 3) if vals else None


def compute_attempt_timing(
    attempt: Mapping[str, Any],
    runs_dir: Path,
    *,
    probe_green_ts: float | None = None,
    reconcile_s: float | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Compute timers across all RunRecords in attempt window"""
    run_ids = list(attempt.get("run_ids") or [])
    inject_s = attempt.get("inject_s")
    injected_at = attempt.get("injected_at")

    per_run: list[dict[str, Any]] = []
    last_ended_ts: float | None = None
    for idx, rid in enumerate(run_ids):
        rec_path = runs_dir / f"{rid}.json"
        if not rec_path.is_file():
            continue
        record = json.loads(rec_path.read_text(encoding="utf-8"))
        entries = load_trace(runs_dir / f"{rid}_trace.jsonl")
        ended = parse_iso(record.get("ended_at"))
        if ended is not None and (last_ended_ts is None or ended > last_ended_ts):
            last_ended_ts = ended
        per_run.append(
            compute_run_timing(
                record,
                entries,
                inject_s=float(inject_s) if idx == 0 and inject_s is not None else 0.0,
                injected_at=str(injected_at) if injected_at else None,
                probe_green_ts=probe_green_ts,
                reconcile_s=reconcile_s,
                repo_root=repo_root,
            )
        )

    injected_at = parse_iso(injected_at)
    end_anchor = probe_green_ts if probe_green_ts is not None else last_ended_ts
    total_s = (
        end_anchor - injected_at
        if end_anchor is not None and injected_at is not None
        else _sum_known(per_run, "total_s")
    )
    gaps = [float(gap) for gap in (attempt.get("gaps_s") or [])]

    return {
        "scenario": attempt.get("scenario"),
        "seed": attempt.get("seed"),
        "primary_run_id": attempt.get("primary_run_id"),
        "inject_s": _round(float(inject_s)) if inject_s is not None else None,
        "detect_s": per_run[0]["detect_s"] if per_run else None,
        "diagnosis_s": _sum_known(per_run, "diagnosis_s"),
        "remediation_s": _sum_known(per_run, "remediation_s"),
        "gate_s": _sum_known(per_run, "gate_s"),
        "reconcile_s": _sum_known(per_run, "reconcile_s"),
        "watchdog_s": _sum_known(per_run, "watchdog_s"),
        "active_run_s": _sum_known(per_run, "active_run_s"),
        "lock_wait_s": _round(sum(gaps)),
        "total_s": _round(total_s),
        "per_run": per_run,
    }


def write_timing(primary_record_path: Path, timing: Mapping[str, Any]) -> Path:
    """Persist run timing.json next to run records"""
    out = primary_record_path.with_name(f"{primary_record_path.stem}_timing.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(timing, indent=2) + "\n", encoding="utf-8")
    return out