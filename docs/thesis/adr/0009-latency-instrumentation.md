---
status: Accepted
date: 2026-11-11
decision-makers: [Adrian Rossa]
consulted: []
informed: []
---

# ADR-0009: Latency instrumentation for RQ2

## Context and Problem Statement

RQ2 asks what transactional and declarative reconciliation mechanisms do to MTTR, and whether that
latency can be reduced. Answering it needs per-component timings, not one total.

vigil already emits more than is used. `PhaseLedger` appends to `phases.jsonl` at every phase
boundary with **both** wall and monotonic clocks, fsynced as each phase happens, and the orchestrator
wraps stages in `_phase(...)`. `RunRecord` carries `MTTR_s`.

What is missing is the part RQ2 is actually about: **the cost of the transactional machinery**. The
CI gate and the Flux reconcile are invisible today. `gate_status` records the outcome but not the
duration, and the reconcile is not timed at all.

A second problem: `MTTR_s` and the sum of phase durations will not agree. Phases overlap (remediation
and the watchdog run concurrently in an `asyncio.TaskGroup`) and the model retries.

## Decision Drivers

- `gate` and `reconcile` are the quantities RQ2 exists to measure, and neither can be reconstructed
  from logs afterwards
- Instrumentation must not change vigil's behaviour
- The ledger already writes what it writes; re-reading beats duplicating
- Overlapping phases must be reported honestly, not silently summed

## Considered Options

- #1 Add explicit timing instrumentation inside vigil
- #2 Derive all timings in `eval/` from existing artefacts
- #3 Derive most timings, and add only the two that cannot be derived

## Decision Outcome

**#3.**

### Already available — read, not rebuilt

| Timer | Source |
|---|---|
| `diagnosis` | `phases.jsonl` |
| `remediation` | `phases.jsonl` |
| `total` | `RunRecord.MTTR_s` |

### New, derived in `eval/`

| Timer | Derivation |
|---|---|
| `inject` | harness clock around `inject.sh` |
| **`detect`** | alert `startsAt` → webhook receipt (needs ADR-0005) |
| **`gate`** | poll `get_pr_status` from first sighting until merged |
| **`reconcile`** | `reconcile.fluxcd.io/requestedAt` vs `status.lastAppliedRevision` |
| `watchdog` | watchdog start → first healthy snapshot |

`reconcile` is the notable one: it needs **no code change**, only a read of two fields Flux already
maintains.

`gate` needs polling rather than a new endpoint, because the git MCP server already returns PR
status; the harness just timestamps the calls.

### One existing bug must be fixed first

`_build_fault_event` sets:

```python
"startsAt": "1970-01-01T00:00:00Z",
```

That is a fixed epoch, sent on every synthetic alert. Any `detect_s` derived from it is nonsense —
about 56 years. It must be `datetime.now(timezone.utc)` in `%Y-%m-%dT%H:%M:%SZ` form, or `detect_s`
is meaningless and the error is invisible, because a huge number looks like a slow cluster rather
than a bug. The fix is one line in `eval/`, and it is what lets the fallback path still produce a
valid `detect_s`: the harness knows when it posted.

### Multi-run attempts

With the lock serialising alerts (ADR-0008), an attempt can produce several `RunRecord`s, so the
timing file has two scopes:

```json
{
  "attempt_id": "k8s-multi-1_seed1_...",
  "n_runs": 2,
  "run_gaps_s": [214.6],
  "per_run": [
    { "run_id": "...a", "detect_s": 11.3, "diagnosis_s": 88.4, "gate_s": 214.0,
      "reconcile_s": 9.7, "watchdog_s": 31.5, "total_s": 552.9 },
    { "run_id": "...b", "detect_s": 0.0,  "diagnosis_s": 62.1, "gate_s": 190.2,
      "reconcile_s": 8.8, "watchdog_s": 28.0, "total_s": 301.4 }
  ],
  "time_to_first_fix_s": 512.3,
  "time_to_last_fix_s": 813.7
}
```

`run_gaps_s` is the lock waiting time and is an RQ3 finding in its own right. For the second run,
`detect_s` is 0 by construction: its alert arrived while the first run held the lock, so detection
was instant and the wait was the lock.

### No timers are added to vigil

Every quantity is either already emitted or observable from outside. Adding timers inside vigil would
change the system being measured for the sake of measuring it.

### Overlap is reported, not hidden

```
eval/runs/<attempt_id>_timing.json
{ "inject_s": 42.1, "detect_s": 11.3, "diagnosis_s": 88.4,
  "remediation_s": 156.2, "gate_s": 214.0, "reconcile_s": 9.7,
  "watchdog_s": 31.5, "total_s": 552.9, "sum_of_phases_s": 553.2,
  "overlap_s": 0.3 }
```

`total_s` will not equal `sum_of_phases_s`. Report both and explain the gap; where the concurrent
remediation-and-watchdog path is taken, the gap is itself an RQ2 observation about where time is and
is not saved.

### Consequences

- Good: RQ2 is answerable with zero vigil changes
- Good: `reconcile` costs nothing to obtain
- Good: `run_gaps_s` turns the lock queue into a measured latency — a better RQ3 result than a
  claimed incapacity (ADR-0008)
- Good: re-scoring an old run is possible, because the input artefacts persist
- Bad: `gate` polling has its own sampling error, bounded by the poll interval, and must be reported
- Bad: Alertmanager's `group_wait` sits inside `detect_s`, which makes detection look slower than the
  cluster's real reaction; that is a property of the deployed configuration and should be stated
- Bad: the fixed-epoch `startsAt` must be fixed before any synthetic `detect_s` is trusted; until
  then the field is worse than absent, because it looks plausible
- Neutral: `phases.jsonl` must be collected from the agent host alongside the run records; the
  campaign workflow already rsyncs run files

**Validation Status:** Partially confirmed. The ledger's dual-clock behaviour and the `_phase(...)`
wrappers are verified by reading the code, as is the fixed-epoch `startsAt`. The `reconcile`
derivation is a design not yet exercised against a live Kustomization.

### Confirmation

The decision holds as long as:

- `gate_s` and `reconcile_s` are non-null for every measured run; a null means missing, not instant
- no `detect_s` is reported from a payload whose `startsAt` is the epoch
- `phases.jsonl` is archived with each campaign, not just the `RunRecord`
- multi-run attempts report `n_runs` and `run_gaps_s` alongside per-run timings
- no timing is computed from the harness's own clock where an artefact already records it
- the overlap between `total_s` and the phase sum is reported wherever latency is discussed

### Pros and Cons of the Options

#### Instrument inside vigil (#1)

- Good: exact, single-clock measurements
- Bad: changes the system under measurement
- Bad: contradicts the "unmodified baseline" claim that ADR-0002 depends on

#### Derive everything in `eval/` (#2)

- Good: zero vigil change
- Bad: `gate_s` and `reconcile_s` have no artefact with a start time, so they need polling and
  annotation reads anyway

#### Derive most, add only what cannot be derived (#3)

- Good: no vigil change, and the two critical quantities are still obtained
- Bad: two provenance styles in one results file (measured vs derived), which needs a note
- Neutral: the reconcile derivation depends on Flux's annotation semantics staying stable

## More Information

- Ledger implementation: `agents/orchestrator/src/orchestrator/agent.py` (`_phase`, `_mark`)
- Phase result columns: `eval/src/eval/aggregate.py`
- Flux annotation semantics: `mcp-servers/flux-mcp/internal/flux/client.go`
  (`requestedAtAnnotation`)
- Detection timer that `detect_s` depends on: ADR-0005
- Record fields added for scoring: ADR-0008
