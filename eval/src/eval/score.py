"""Rule-based multi-fault scoring using health probes and collision checks"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eval import timings

ProbeFn = Callable[[Mapping[str, Any], str | None], bool]


def _utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_probe_fn(probe: Mapping[str, Any], kubeconfig: str | None) -> bool:
    from eval.harness import _probe_passes

    return _probe_passes(dict(probe), kubeconfig)


def poll_health_probes(
    faults: Sequence[Mapping[str, Any]],
    *,
    injected_at: str | None = None,
    kubeconfig: str | None = None,
    probe_fn: ProbeFn | None = None,
    interval_s: float = 5.0,
    required_streak: int = 3,
    
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.time,
) -> dict[str, dict[str, Any]]:
    """Poll each faults health_probe until N consecutive passes or timeout"""
    health_check = probe_fn or _default_probe_fn
    start_ts = now_fn()
    injected_at = timings.parse_iso(injected_at)

    probes: dict[str, Mapping[str, Any]] = {}
    timeouts: dict[str, float] = {}
    streaks: dict[str, int] = {}
    confirmed: dict[str, float | None] = {}

    for idx, fault in enumerate(faults):
        probe = fault.get("health_probe")
        if not isinstance(probe, Mapping):
            continue
        fid = str(fault.get("id") or f"f{idx}")
        probes[fid] = probe
        timeouts[fid] = start_ts + float(probe.get("window_s", 300))
        streaks[fid] = 0
        confirmed[fid] = None

    while True:
        now = now_fn()
        pending = [fid for fid, ts in confirmed.items() if ts is None]
        active = [fid for fid in pending if now <= timeouts[fid]]
        if not active:
            break

        for fid in active:
            if health_check(probes[fid], kubeconfig):
                streaks[fid] += 1
                if streaks[fid] >= required_streak:
                    confirmed[fid] = now_fn()
            else:
                streaks[fid] = 0

        still_active = [
            fid for fid in active 
            if confirmed[fid] is None and now_fn() < timeouts[fid]
        ]
        if not still_active:
            break
        sleep_fn(interval_s)

    results: dict[str, dict[str, Any]] = {}
    for fid, ts in confirmed.items():
        restore_s = (
            round(max(0.0, ts - injected_at), 3)
            if ts is not None and injected_at is not None
            else None
        )
        results[fid] = {
            "remediated": ts is not None,
            "passed_at": _utc_iso(ts) if ts is not None else None,
            "passed_ts": ts,
            "time_to_restore_s": restore_s,
        }
    return results


def _git_touched_paths(sha: str, repo_root: Path) -> list[str]:
    res = subprocess.run(
        [
            "git", "-C", str(repo_root), "diff-tree", 
            "--no-commit-id", "--name-only", "-r", sha
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        return []
    return [line.strip() for line in res.stdout.splitlines() if line.stip()]


def touched_paths_by_incident(
    records: Sequence[Mapping[str, Any]],
    repo_root: Path | None,
) -> dict[str, list[str]]:
    """Collect modified repo paths grouped by incident id (ignores retry dupl.)"""
    if repo_root is None:
        return {}
    by_incident: dict[str, set[str]] = {}
    for rec in records:
        inc_id = str(rec.get("incident_id") or rec.get("run_id") or "")
        if not inc_id:
            continue
        shas = list(rec.get("agent_commits") or [])
        if not shas and rec.get("merge_commit_sha"):
            shas = [str(rec["merge_commit_sha"])]
        for sha in shas:
            by_incident.setdefault(inc_id, set()).update(
                _git_touched_paths(str(sha), repo_root)
            )
    return {inc_id: sorted(paths) for inc_id, paths in by_incident.items() if paths}


def detect_collisions(paths_by_incident: Mapping[str, Sequence[str]]) -> list[str]:
    """Return sorted file paths modified by distinct incidents"""
    path_owners: dict[str, set[str]] = {}
    for inc_id, paths in paths_by_incident.items():
        for path in paths:
            path_owners.setdefault(path, set()).add(inc_id)
    return sorted(path for path, owners in path_owners.items() if len(owners) > 1)


def _noise_pod_names(attempt: Mapping[str, Any]) -> set[str]:
    noise = attempt.get("noise")
    if not isinstance(noise, Mapping):
        return set()
    pods: set[str] = set()
    for event in noise.get("events") or []:
        if not isinstance(event, Mapping):
            continue
        if (
            event.get("kind") == "pod_delete" 
            and event.get("ok") and event.get("target")
        ):
            target = str(event["target"])
            pods.add(target.rsplit("/", 1)[-1])
    return pods


def _alert_labels(fault_event: Mapping[str, Any] | None) -> dict[str, str]:
    if not fault_event:
        return {}
    labels: dict[str, str] = {}
    common = fault_event.get("commonLabels")
    if isinstance(common, Mapping):
        labels.update({str(k): str(v) for k, v in common.items()})
    alerts = fault_event.get("alerts") or []
    if alerts and isinstance(alerts[0], Mapping):
        first_labels = alerts[0].get("labels")
        if isinstance(first_labels, Mapping):
            labels.update({str(k): str(v) for k, v in first_labels.items()})
    return labels


def _is_noise_run(labels: Mapping[str, str], noise_pods: set[str]) -> bool:
    pod = labels.get("pod", "")
    return bool(pod and pod in noise_pods)


def _report_matches_fault(
    report: Mapping[str, Any], fault: Mapping[str, Any]
) -> tuple[bool, bool]:
    """Return (detected, attributed) for one DiagnosisReport against one fault"""
    evidence = report.get("evidence") or []
    evidence_text = " ".join(str(item) for item in evidence)
    report_text = " ".join(
        [
            str(report.get("root_cause") or ""),
            str(report.get("root_cause_component") or ""),
            str(report.get("manifest_path") or ""),
            evidence_text,
        ]
    ).lower()

    component = str(fault.get("component") or "")
    short_comp = component.rsplit("/", 1)[-1].lower()
    keywords = [
        str(keyword).lower() 
        for keyword in (fault.get("root_cause_keywords") or [])
    ]

    if keywords:
        detected = any(keyword in report_text for keyword in keywords)
    else:
        detected = bool(short_comp and short_comp in report_text)

    target_blob = " ".join(
        [
            str(report.get("root_cause_component") or ""),
            str(report.get("manifest_path") or ""),
            " ".join(str(resource) 
            for resource in (report.get("affected_resources") or []))
        ]
    ).lower()
    manifest_name = Path(str(fault.get("manifest") or "")).name.lower()
    comp_match = bool(
        (short_comp and short_comp in target_blob)
        or (manifest_name and manifest_name in target_blob)
    )
    expected_action = str(fault.get("expected_action") or "")
    action_match = not expected_action or (
        str(report.get("recommended_action") or "") == expected_action
    )
    attributed = detected and comp_match and action_match
    return detected, attributed


def score_attempt(
    attempt: Mapping[str, Any],
    scenario: Mapping[str, Any],
    runs_dir: Path,
    *,
    probe_results: Mapping[str, Mapping[str, Any]] | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Score all faults in an attempt from its RunRecords, traces, probes"""
    run_ids = list(attempt.get("run_ids") or [])
    noise_pods = _noise_pod_names(attempt)

    records: list[dict[str, Any]] = []
    fault_reports: list[dict[str, Any]] = []
    noise_run_ids: list[str] = []
    fault_run_ids: list[str] = []
    run_alerts: dict[str, dict[str, str]] = {}

    for rid in run_ids:
        rec_path = runs_dir / f"{rid}.json"
        if rec_path.is_file():
            records.append(json.loads(rec_path.read_text(encoding="utf-8")))
        entries = timings.load_trace(runs_dir / f"{rid}_trace.jsonl")
        labels = _alert_labels(timings.extract_fault_event(entries))
        if labels:
            run_alerts[rid] = labels
        if _is_noise_run(labels, noise_pods):
            noise_run_ids.append(rid)
            continue
        fault_run_ids.append(rid)
        report = timings.extract_diagnosis_report(entries)
        if report is not None:
            fault_reports.append(report)

    paths_by_inc = touched_paths_by_incident(records, repo_root)
    all_touched = sorted({path for paths in paths_by_inc.values() for path in paths})
    colliding_paths = detect_collisions(paths_by_inc)
    probes = dict(probe_results or attempt.get("probe_results") or {})

    faults = list(scenario.get("faults") or [])
    per_fault: list[dict[str, Any]] = []
    for idx, fault in enumerate(faults):
        fid = str(fault.get("id") or f"f{idx}")
        detected = False
        attributed = False
        for report in fault_reports:
            det, attr = _report_matches_fault(report, fault)
            detected = detected or det
            attributed = attributed or attr

        p_res = probes.get(fid) or {}
        manifest = str(fault.get("manifest") or "")
        matched_paths = [
            path for path in all_touched 
            if manifest and path.endswith(manifest)
        ]
        per_fault.append(
            {
                "id": fid,
                "component": fault.get("component"),
                "severity": fault.get("severity", "user_facing"),
                "detected": detected,
                "attributed": attributed,
                "remediated": bool(p_res.get("remediated", False)),
                "passed_at": p_res.get("passed_at"),
                "time_to_restore_s": p_res.get("time_to_restore_s"),
                "touched_paths": matched_paths,
            }
        )

    n_faults = len(per_fault)
    n_remediated = sum(1 for fault in per_fault if fault["remediated"])
    all_remediated = n_faults > 0 and n_remediated == n_faults
    restore_times = [
        float(fault["time_to_restore_s"])
        for fault in per_fault
        if isinstance(fault.get("time_to_restore_s"), (int, float))
    ]
    time_to_last_fix_s = (
        round(max(restore_times), 3) if all_remediated and restore_times else None
    )

    return {
        "scenario": attempt.get("scenario"),
        "seed": attempt.get("seed"),
        "primary_run_id": attempt.get("primary_run_id"),
        "detection_used": attempt.get("detection_used"),
        "n_runs": len(run_ids),
        "fault_run_ids": fault_run_ids,
        "noise_run_ids": noise_run_ids,
        "run_alerts": run_alerts,
        "n_faults": n_faults,
        "n_detected": sum(1 for f in per_fault if f["detected"]),
        "n_attributed": sum(1 for f in per_fault if f["attributed"]),
        "n_remediated": n_remediated,
        "all_remediated": all_remediated,
        "time_to_last_fix_s": time_to_last_fix_s,
        "collision": bool(colliding_paths),
        "colliding_paths": colliding_paths,
        "touched_paths_by_incident": paths_by_inc,
        "faults": per_fault,
    }


def write_score(primary_record_path: Path, score: Mapping[str, Any]) -> Path:
    """Persist runs score.json next to the run records"""
    out = primary_record_path.with_name(f"{primary_record_path.stem}_score.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(score, indent=2) + "\n", encoding="utf-8")
    return out

def load_run_map(runs_dir: Path) -> dict[str, str]:
    """Load run_id -> scenario mapping from run_map.json"""
    path = runs_dir / "run_map.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def update_run_map(runs_dir: Path, attempt: Mapping[str, Any]) -> Path:
    """Record every run_id in attempt under its true scenario"""
    mapping = load_run_map(runs_dir)
    scenario = str(attempt.get("scenario") or "")
    if scenario:
        for rid in attempt.get("run_ids") or []:
            mapping[str(rid)] = scenario
        if attempt.get("primary_run_id"):
            mapping[str(attempt["primary_run_id"])] = scenario
    out = runs_dir / "run_map.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(dict(sorted(mapping.items())), indent=2) + "\n",
        encoding="utf-8",
    )
    return out
