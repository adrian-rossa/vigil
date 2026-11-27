from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from orchestrator.agent import MAX_TRIES, retry_run_id
from orchestrator.models import eval_target_from_env

from eval.campaign import combinations, completed_run_ids
from eval.harness import DEFAULT_ORCHESTRATOR_URL, is_final_record, run_one
from eval.scenario import load_scenarios

_TIMEOUT_HELP = (
    "Seconds to poll for each try's result file before exiting non-zero"
    " (default: the agent's run timeout plus its shutdown drain and a buffer)."
)


@click.group()
def cli() -> None:
    """Vigil eval harness."""


@cli.command("run")
@click.option("--scenario", required=True, help="Scenario id, e.g. k8s-1")
@click.option("--seed", required=True, type=int, help="Run counter (1, 2, 3)")
@click.option(
    "--model", required=True, help="Model identifier passed through to Orchestrator"
)
@click.option(
    "--timeout",
    "timeout_s",
    default=None,
    type=int,
    envvar="VIGIL_RUN_TIMEOUT_S",
    show_envvar=True,
    help=_TIMEOUT_HELP,
)
@click.option(
    "--orchestrator-url",
    default=DEFAULT_ORCHESTRATOR_URL,
    envvar="VIGIL_ORCHESTRATOR_URL",
    show_envvar=True,
)
@click.option(
    "--scenarios-dir",
    default="eval/scenarios",
    envvar="VIGIL_SCENARIOS_DIR",
    show_envvar=True,
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option(
    "--runs-dir",
    default=None,
    envvar="EVAL_RUNS_DIR",
    show_envvar=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Defaults to eval/runs (matches Orchestrator default).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Stream script output and log progress to stderr.",
)
@click.option(
    "--workload",
    type=click.Choice(["off", "stepped"]),
    default=None,
    help="Override scenario.yaml workload factor (off or stepped)",
)
@click.option(
    "--noise/--no-noise",
    "noise_override",
    default=None,
    help="Override scenario.yaml bg noise factor"
)
@click.option(
    "--no-vigil",
    is_flag=True,
    default=False,
    help="Inject fault and poll health_probes without triggering vigil.",
)
def run_cmd(
    scenario: str,
    seed: int,
    model: str,
    timeout_s: int,
    orchestrator_url: str,
    scenarios_dir: Path,
    runs_dir: Path | None,
    verbose: bool,
    workload: str | None,
    noise_override: bool | None,
) -> None:
    """Execute one eval run: reset -> inject -> POST -> poll -> print metrics."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        result_path = asyncio.run(
            run_one(
                scenario_id=scenario,
                seed=seed,
                model=model,
                scenarios_dir=scenarios_dir,
                orchestrator_url=orchestrator_url,
                runs_dir=runs_dir,
                timeout_s=timeout_s,
                verbose=verbose,
                workload_override=workload,
                noise_override=noise_override,
            )
        )
    except TimeoutError as e:
        resolved_runs_dir = Path(runs_dir) if runs_dir else Path("eval/runs")
        pending_id, _ = _pending_try(
            resolved_runs_dir, _run_id_for(scenario, seed, model)
        )
        result_path = resolved_runs_dir / f"{pending_id}.json"
        if _final_record_exists(result_path):
            click.echo(
                "NOTE: orchestrator result appeared after the wait deadline; using it",
                err=True,
            )
        else:
            click.echo(f"ERROR: {e}", err=True)
            _write_setup_error_record(
                scenario,
                seed,
                model,
                resolved_runs_dir,
                str(e),
                outcome="harness_timeout",
                started_at=started_at,
            )
            sys.exit(2)
    except (RuntimeError, FileNotFoundError) as e:
        click.echo(f"ERROR: {e}", err=True)
        resolved_runs_dir = Path(runs_dir) if runs_dir else Path("eval/runs")
        _write_setup_error_record(
            scenario,
            seed,
            model,
            resolved_runs_dir,
            str(e),
            started_at=started_at,
        )
        sys.exit(1)

    record = json.loads(result_path.read_text())
    click.echo(
        json.dumps(
            {
                "run_id": record["run_id"],
                "outcome": record["outcome"],
                "success_rate": record.get("success_rate"),
                "MTTR_s": record.get("MTTR_s"),
                "iteration_count": record.get("iteration_count"),
                "result_file": str(result_path),
            },
            indent=2,
        )
    )


def _run_id_for(scenario_id: str, seed: int, model: str) -> str:
    try:
        sha7 = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        sha7 = "unknown"
    safe_model = re.sub(r"[^a-zA-Z0-9_-]", "-", model)
    return f"{scenario_id}_{seed}_{safe_model}_{sha7}"


def _final_record_exists(path: Path) -> bool:
    try:
        return is_final_record(json.loads(path.read_text()))
    except (FileNotFoundError, ValueError):
        return False


def _pending_try(runs_dir: Path, incident_id: str) -> tuple[str, int]:
    for try_number in range(1, MAX_TRIES + 1):
        run_id = (
            incident_id if try_number == 1 else retry_run_id(incident_id, try_number)
        )
        path = runs_dir / f"{run_id}.json"
        if not path.exists() or _final_record_exists(path):
            return run_id, try_number
    return retry_run_id(incident_id, MAX_TRIES + 1), MAX_TRIES + 1


def _write_setup_error_record(
    scenario_id: str,
    seed: int,
    model: str,
    runs_dir: Path,
    error_msg: str,
    outcome: str = "setup_error",
    started_at: str | None = None,
) -> bool:
    target = eval_target_from_env()
    incident_id = _run_id_for(scenario_id, seed, model)
    run_id, try_number = _pending_try(runs_dir, incident_id)
    result_path = runs_dir / f"{run_id}.json"
    if _final_record_exists(result_path):
        return False

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs_dir.mkdir(parents=True, exist_ok=True)
    record: dict = {
        "run_id": run_id,
        "incident_id": incident_id,
        "attempts": try_number,
        "scenario": scenario_id,
        "seed": seed,
        "model": model,
        "outcome": outcome,
        "success_rate": False,
        "diagnosis_accuracy": None,
        "MTTR_s": None,
        "destructive_repair": False,
        "rollback_triggered": False,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_tool_calls": 0,
        "iteration_count": 0,
        "autonomy_level": "full",
        "actions_taken": [],
        "forbidden_action_violations": [],
        "started_at": started_at or now_str,
        "ended_at": now_str,
        "setup_error": error_msg[:500],
        "target": target,
    }
    result_path.write_text(json.dumps(record, indent=2))

    index_path = runs_dir.parent / "runs_index.jsonl"
    index_entry = {
        "run_id": run_id,
        "scenario": scenario_id,
        "seed": seed,
        "model": model,
        "outcome": outcome,
        "success_rate": False,
        "target": target,
    }
    with index_path.open("a") as fh:
        fh.write(json.dumps(index_entry) + "\n")
    return True


@cli.command("campaign")
@click.option(
    "--scenarios-dir",
    default="eval/scenarios",
    envvar="VIGIL_SCENARIOS_DIR",
    show_envvar=True,
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option(
    "--models",
    required=True,
    multiple=True,
    help="Model identifier(s). Repeat flag: --models m1 --models m2.",
)
@click.option("--seeds", default=(1, 2, 3), multiple=True, type=int, show_default=True)
@click.option(
    "--runs-dir",
    default=None,
    envvar="EVAL_RUNS_DIR",
    show_envvar=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Defaults to eval/runs (matches Orchestrator default).",
)
@click.option(
    "--orchestrator-url",
    default=DEFAULT_ORCHESTRATOR_URL,
    envvar="VIGIL_ORCHESTRATOR_URL",
    show_envvar=True,
)
@click.option(
    "--index",
    default=None,
    help="Path to runs_index.jsonl (default: {runs_dir}/../runs_index.jsonl).",
)
@click.option(
    "--timeout",
    "timeout_s",
    default=None,
    type=int,
    envvar="VIGIL_RUN_TIMEOUT_S",
    show_envvar=True,
    help=_TIMEOUT_HELP,
)
@click.option(
    "--retry-failed",
    is_flag=True,
    default=False,
    help="Re-run only combinations listed in failures.jsonl.",
)
@click.option("--verbose", "-v", is_flag=True, default=False)
def campaign_cmd(
    scenarios_dir: Path,
    models: tuple,
    seeds: tuple,
    runs_dir: Path | None,
    orchestrator_url: str,
    index: str | None,
    timeout_s: int,
    retry_failed: bool,
    verbose: bool,
) -> None:
    """Run all (scenarios × models × seeds) combinations; pause on quota exhaustion."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    runs_dir = Path(runs_dir) if runs_dir else Path("eval/runs")
    runs_dir.mkdir(parents=True, exist_ok=True)
    index_path = Path(index) if index else (runs_dir.parent / "runs_index.jsonl")
    failures_path = runs_dir / "failures.jsonl"

    if retry_failed:
        combos = _load_failed_combinations(failures_path)
    else:
        scenario_ids = [s.id for s in load_scenarios(scenarios_dir)]
        all_combos = list(combinations(scenario_ids, list(seeds), list(models)))
        done = completed_run_ids(index_path)
        combos = [
            (sc, sd, md)
            for sc, sd, md in all_combos
            if not any(rid.startswith(f"{sc}_{sd}_{md}_") for rid in done)
        ]

    total = len(combos)
    click.echo(
        f"Running {total} combinations (runs_dir={runs_dir}, index={index_path})",
        err=True,
    )

    succeeded_this_session: set[tuple[str, int, str]] = set()

    for n, (scenario, seed, model) in enumerate(combos, start=1):
        try:
            result_path = asyncio.run(
                run_one(
                    scenario_id=scenario,
                    seed=seed,
                    model=model,
                    scenarios_dir=scenarios_dir,
                    orchestrator_url=orchestrator_url,
                    runs_dir=runs_dir,
                    timeout_s=timeout_s,
                    verbose=verbose,
                )
            )
            record = json.loads(result_path.read_text())
            if record.get("outcome") == "quota_exhausted":
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                checkpoint = {
                    "stopped_at": now_str,
                    "reason": "quota_exhausted",
                    "completed_n": n - 1,
                    "remaining_combos": [
                        {"scenario": sc, "seed": sd, "model": md}
                        for sc, sd, md in combos[n - 1 :]
                    ],
                }
                checkpoint_path = runs_dir / "quota_checkpoint.json"
                checkpoint_path.write_text(json.dumps(checkpoint, indent=2))
                msg = (
                    f"[{n}/{total}] {scenario}/seed{seed}/{model} - QUOTA_EXHAUSTED: "
                    f"campaign paused. Resume with --resume "
                    f"(checkpoint: {checkpoint_path})"
                )
                click.echo(msg, err=True)
                break
            run_id = record["run_id"]
            trace_path = runs_dir / f"{run_id}_trace.jsonl"
            success = "SUCCESS" if record.get("success_rate") else "FAIL"
            mttr = record.get("MTTR_s")
            mttr_s = f"{mttr:.0f}s" if isinstance(mttr, (int, float)) else "?s"
            click.echo(
                f"[{n}/{total}] {scenario}/seed{seed}/{model}"
                f" - {success} (MTTR={mttr_s})"
                f" | trace: {trace_path}",
                err=True,
            )
            succeeded_this_session.add((scenario, seed, model))
        except Exception as e:
            failure = {
                "scenario": scenario,
                "seed": seed,
                "model": model,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "error": str(e),
            }
            with failures_path.open("a") as fh:
                fh.write(json.dumps(failure) + "\n")
            click.echo(
                f"[{n}/{total}] {scenario}/seed{seed}/{model} - FAILED: {e}",
                err=True,
            )

    if retry_failed and succeeded_this_session:
        _prune_failures(failures_path, succeeded_this_session)


def _load_failed_combinations(failures_path: Path) -> list[tuple[str, int, str]]:
    if not failures_path.exists():
        return []
    combos: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int, str]] = set()
    with failures_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            key = (rec["scenario"], int(rec["seed"]), rec["model"])
            if key not in seen:
                seen.add(key)
                combos.append(key)
    return combos


def _prune_failures(failures_path: Path, succeeded: set[tuple[str, int, str]]) -> None:
    if not failures_path.exists():
        return
    kept: list[str] = []
    with failures_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            key = (rec["scenario"], int(rec["seed"]), rec["model"])
            if key not in succeeded:
                kept.append(line)
    if kept:
        failures_path.write_text("\n".join(kept) + "\n")
    else:
        failures_path.unlink(missing_ok=True)


@cli.command("aggregate")
@click.option(
    "--runs-dir",
    default=None,
    envvar="EVAL_RUNS_DIR",
    show_envvar=True,
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option(
    "--index",
    default=None,
    help="Path to runs_index.jsonl (default: {runs_dir}/../runs_index.jsonl).",
)
@click.option(
    "--scenarios-dir",
    default="eval/scenarios",
    envvar="VIGIL_SCENARIOS_DIR",
    show_envvar=True,
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option(
    "--output-dir",
    default="eval/results",
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option(
    "--seed-count",
    default=None,
    type=int,
    envvar="VIGIL_SEED_COUNT",
    show_envvar=True,
    help="Seeds requested per scenario; sizes the planned-runs denominator.",
)
def aggregate_cmd(
    runs_dir: Path | None,
    index: str | None,
    scenarios_dir: Path,
    output_dir: Path,
    seed_count: int | None,
) -> None:
    """Read completed run JSONs and produce summary.json, REPORT.md,
    and step_summary.md."""
    from eval.aggregate import (
        MixedTargetsError,
        UnknownTargetError,
        aggregate_runs,
        write_report,
        write_step_summary,
    )

    runs_dir = Path(runs_dir) if runs_dir else Path("eval/runs")
    index_path = Path(index) if index else (runs_dir.parent / "runs_index.jsonl")
    output_dir = Path(output_dir)

    try:
        summary = aggregate_runs(runs_dir, index_path, scenarios_dir, seed_count)
    except (MixedTargetsError, UnknownTargetError) as exc:
        raise click.ClickException(str(exc)) from exc
    write_report(summary, output_dir)
    write_step_summary(runs_dir, index_path, output_dir, scenarios_dir, seed_count)
    click.echo(
        f"Written: {output_dir}/summary.json,"
        f" {output_dir}/REPORT.md, {output_dir}/step_summary.md"
    )


@cli.command("score")
@click.option(
    "--runs-dir",
    default=None,
    envvar="EVAL_RUNS_DIR",
    show_envvar=True,
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option(
    "--scenarios-dir",
    default="eval/scenarios",
    envvar="VIGIL_SCENARIOS_DIR",
    show_envvar=True,
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option(
    "--repo-root",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
    help="Git repo root used for diff-tree collision and merge timestamps",
)
def score_cmd(
    runs_dir: Path | None,
    scenarios_dir: Path,
    repo_root: Path,
) -> None:
    """Re-score every attempt.json offline and emit score/timing/run_map"""
    import yaml

    from eval import score as score_lib
    from eval import timings as timings_lib

    resolved_runs = Path(runs_dir) if runs_dir else Path("eval/runs")
    attempts = sorted(resolved_runs.glob("*_attempt.json"))
    scored = 0
    for attempt_path in attempts:
        attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
        scenario_id = str(attempt.get("scenario") or "")
        primary_id = str(attempt.get("primary_run_id") or "")
        if not scenario_id or not primary_id:
            continue
        scen_yaml = scenarios_dir / scenario_id / "scenario.yaml"
        scenario = (
            yaml.safe_load(scen_yaml.read_text(encoding="utf-8")) or {}
            if scen_yaml.is_file()
            else {}
        )
        primary_path = resolved_runs / f"{primary_id}.json"
        probes = attempt.get("probe_results") or None
        score_lib.update_run_map(resolved_runs, attempt)
        score_data = score_lib.score_attempt(
            attempt,
            scenario,
            resolved_runs,
            probe_results=probes,
            repo_root=repo_root,
        )
        score_lib.write_score(primary_path, score_data)
        stamps = [
            float(res["passed_ts"])
            for res in (probes or {}).values()
            if isinstance(res.get("passed_ts"), (int, float))
        ]
        probe_green_ts = (
            max(stamps) if score_data.get("all_remediated") and stamps else None
        )
        timing_data = timings_lib.compute_attempt_timing(
            attempt,
            resolved_runs,
            probe_green_ts=probe_green_ts,
            repo_root=repo_root,
        )
        timings_lib.write_timing(primary_path, timing_data)
        scored += 1
    click.echo(f"Scored {scored} attempt(s) in {resolved_runs}")


@cli.command("judge")
@click.option(
    "--runs-dir",
    default=None,
    envvar="EVAL_RUNS_DIR",
    show_envvar=True,
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option(
    "--scenarios-dir",
    default="eval/scenarios",
    envvar="VIGIL_SCENARIOS_DIR",
    show_envvar=True,
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option(
    "--model",
    default="",
    envvar="JUDGE_MODEL_NAME",
    show_envvar=True,
    help="Pinned LLM model for the checklist judge (required).",
)
@click.option(
    "--overwrite",
    is_flag=True,
    default=False,
    help="Re-judge attempts that already have a *_judge.json file.",
)
def judge_cmd(
    runs_dir: Path | None,
    scenarios_dir: Path,
    model: str,
    overwrite: bool,
) -> None:
    """Run the pinned LLM-as-a-Judge offline over every *_attempt.json."""
    import yaml

    from eval import judge as judge_lib

    if not model.strip():
        raise click.ClickException(
            "A pinned judge model is required (--model or JUDGE_MODEL_NAME)."
        )
    resolved_runs = Path(runs_dir) if runs_dir else Path("eval/runs")
    dj = judge_lib.DiagnosisJudge(model_name=model.strip())
    judged = 0
    skipped = 0
    for attempt_path in sorted(resolved_runs.glob("*_attempt.json")):
        attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
        scenario_id = str(attempt.get("scenario") or "")
        primary_id = str(attempt.get("primary_run_id") or "")
        if not scenario_id or not primary_id:
            continue
        primary_path = resolved_runs / f"{primary_id}.json"
        out_path = primary_path.with_name(f"{primary_id}_judge.json")
        if out_path.is_file() and not overwrite:
            skipped += 1
            continue
        scen_yaml = scenarios_dir / scenario_id / "scenario.yaml"
        scenario = (
            yaml.safe_load(scen_yaml.read_text(encoding="utf-8")) or {}
            if scen_yaml.is_file()
            else {}
        )
        result = judge_lib.judge_attempt(attempt, scenario, resolved_runs, dj)
        judge_lib.write_judge(primary_path, result)
        judged += 1
    click.echo(f"Judged {judged} attempt(s) (skipped {skipped}) in {resolved_runs}")


@cli.command("kappa")
@click.option(
    "--labels-file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="JSON array or JSONL of {primary_run_id, fault_id, human_verdict}.",
)
@click.option(
    "--runs-dir",
    default=None,
    envvar="EVAL_RUNS_DIR",
    show_envvar=True,
    type=click.Path(file_okay=False, path_type=Path),
)
def kappa_cmd(labels_file: Path, runs_dir: Path | None) -> None:
    """Compute Cohen's kappa between Phase 3b human labels and *_judge.json."""
    from eval.aggregate import cohens_kappa

    resolved_runs = Path(runs_dir) if runs_dir else Path("eval/runs")
    raw_text = labels_file.read_text(encoding="utf-8").strip()
    if raw_text.startswith("["):
        rows = json.loads(raw_text)
    else:
        rows = [json.loads(line) for line in raw_text.splitlines() if line.strip()]

    human_labels: list[bool] = []
    judge_labels: list[bool] = []
    models: set[str] = set()
    versions: set[str] = set()
    unscored_skipped = 0
    missing = 0

    for row in rows:
        pid = str(row["primary_run_id"])
        fid = str(row.get("fault_id") or "a")
        human_val = bool(row["human_verdict"])
        jpath = resolved_runs / f"{pid}_judge.json"
        if not jpath.is_file():
            missing += 1
            continue
        jdata = json.loads(jpath.read_text(encoding="utf-8"))
        if jdata.get("judge_model"):
            models.add(str(jdata["judge_model"]))
        if jdata.get("checklist_version"):
            versions.add(str(jdata["checklist_version"]))
        match = next(
            (f for f in (jdata.get("faults") or []) if str(f.get("id")) == fid),
            None,
        )
        if match is None:
            missing += 1
            continue
        if match.get("verdict") is None:
            unscored_skipped += 1
            continue
        human_labels.append(human_val)
        judge_labels.append(match["verdict"] == "True")

    n = len(human_labels)
    agree = (
        round(sum(a == b for a, b in zip(human_labels, judge_labels)) / n, 4)
        if n
        else None
    )
    click.echo(
        json.dumps(
            {
                "n_paired": n,
                "n_unscored_skipped": unscored_skipped,
                "n_missing": missing,
                "agreement_rate": agree,
                "cohens_kappa": cohens_kappa(human_labels, judge_labels),
                "judge_models": sorted(models),
                "checklist_versions": sorted(versions),
            },
            indent=2,
        )
    )

if __name__ == "__main__":
    cli()
