---
status: Accepted
date: 2026-11-12
decision-makers: [Adrian Rossa]
consulted: []
informed: []
---

# ADR-0008: Zero-change baseline, and characterising it before claiming a limit

## Context and Problem Statement

ADR-0002 keeps vigil's own harness as the runtime. That removes SREGym's conflicts but does not by
itself guarantee the measured system is unmodified, and it does not describe how the baseline
behaves. Both questions decide what the thesis can claim, so they are answered together.

### Problem 1: the testbed still has to read from vigil and clean up after it

Two candidate changes emerged:

1. **`root_cause` in `RunRecord`.** The record has `diagnosis_accuracy` but not the diagnosis text.
   The judge needs the text, and so does per-fault keyword scoring.
2. **Git-aware teardown.** SREGym's `recover_fault()` undoes live cluster state. If vigil committed
   a fix, the commit survives and Flux re-applies it, so the next run starts dirty.

A governance rule constrains both: `FORK.md` says everything outside `eval/` belongs to upstream
and is not modified. And a baseline that differs from upstream in ways that affect diagnosis or
repair cannot support the claim "unmodified vigil was measured".

### Problem 2: the baseline was described from code, never observed

Every RQ3 claim compares against a baseline. An earlier version of this work described it as:
*"unmodified vigil cannot handle two faults. On a two-fault scenario it produces one report and one
commit."*

Reading `main.py` and `agent.py` shows that this is **probably false**:

```python
# main.py — POST /webhook
task = asyncio.create_task(run_orchestration(...))
track_run(task)
return {"run_id": run_id}          # returns before the run starts

# agent.py — run_orchestration
async with _RUN_LOCK:              # the second task waits here
    return await _run_orchestration(...)
```

A second alert arriving mid-run gets its own task, its own `run_id` and its own
`remediation/run-<run_id>` branch, and it blocks until the first run finishes. It then runs normally
with a full `GIT_COMMIT_BUDGET` of 1. The two repairs cannot collide.

**What this does and does not change.** It is tempting to treat this as a threat to the thesis. It
is not:

| Item | Affected? |
|---|---|
| the scenario matrix | no |
| the schema, probes and generic injector | no |
| noise, traffic, timers, judge, campaign | no |
| **one sentence in §5.1 describing the baseline** | yes |
| **whether `detected` or `time_to_last_fix` is RQ3's headline** | yes |
| **how RQ3's contribution is worded** | yes |

The Exposé **already states** that "the unmodified vigil system uses sequential single-fault
processing" and that it "will be measured as the baseline", and RQ3 asks whether vigil handles
concurrency "more effectively than baseline sequential processing". A sequential baseline is the
comparison already promised — not a discovery that undermines it. So the response is proportionate:
run the experiment once, cheaply, when the harness can support it. It is not a gate on other work.

## Decision Drivers

- The baseline claim must survive scrutiny
- The testbed must not depend on artefacts that change format
- Teardown must leave a clean state, or five seeds are meaningless
- Deviations must be recorded where a reviewer finds them, not in commit messages
- A baseline described from code and never observed is a guess in the paragraph a reviewer will
  check against the source
- The cost of getting the framing wrong is a sentence, not a rewrite — so the mitigation matches

## Considered Options

### `root_cause`

- #R1 Add `root_cause` and `recommended_action` to `RunRecord` (two output fields)
- #R2 Parse the trace JSONL (`<run_id>_trace.jsonl`)
- #R3 Import vigil's diagnosis models and re-run diagnosis

### Teardown

- #T1 Patch SREGym's `recover_fault`
- #T2 Revert commits in the adapter's cleanup, using `RunRecord.agent_commits`
- #T3 Reset the eval branch to baseline between problems and let Flux reconcile

### Baseline characterisation

- #B1 Keep the "one report, one commit" claim; skip measurement
- #B2 Measure once, as Phase 1's acceptance test, reusing two working scenarios
- #B3 Measure as a blocking phase before any harness work
- #B4 Measure in Phase 4, with everything else
- #B5 Assume the worst case and present it as a lower bound

## Decision Outcome

**#R1**, **#T3**, and **#B2**.

### `root_cause`: #R1, taken and documented

Two new fields on `RunRecord`:

```python
root_cause: str | None = None
recommended_action: str | None = None
```

populated in `_attempt_record` from the `DiagnosisReport`.

**This is now recommended, not optional.** The judge is co-primary for RQ1 (ADR-0006), so the
diagnosis text is required. Without the field the only route is #R2, which is rejected below — so
"optional" would have meant "fall back to a rejected method".

**Why it is defensible as "unmodified":** both fields are *output only*. Nothing in diagnosis,
remediation, the watchdog, the gate or rollback reads them, so behaviour is identical with and
without the patch. That is a stronger argument than convenience, and it should be made that way.

**Why not #R2:** the trace is a debug log, truncated at 1000 characters per message, written for
human reading. `DiagnosisReport.root_cause` is the field that matters and a truncated copy is not a
substitute. Its shape also changes when message serialisation changes, which would break the
adapter silently.

**Why not #R3:** re-running diagnosis costs tokens and is not the same run.

### Teardown: #T3 first, #T2 only as a fallback

Resetting the eval branch to baseline and reconciling is simpler than reverting individual commits,
needs no vigil change, and needs no knowledge of which commits a run produced.

Reverting `RunRecord.agent_commits` (#T2) handles the case where a run's commits must be undone
without touching anything else. It is the fallback, not the default.

#T1 is rejected: SREGym's `recover_fault` is per-problem and correct for live faults. The missing
behaviour is git, which SREGym does not model.

### Baseline: #B2, one two-fault run as Phase 1's acceptance test

1. Pick two already-working scenarios (`k8s-3g` OOM and `k8s-4g` bogus nodeSelector), so no injector
   has to be written.
2. Inject both, back to back.
3. Post **two alert payloads**, omitting `?seed=` so the `run_id`s stay distinct.
4. Watch `eval/runs/` until no new record appears for longer than the run timeout.
5. Record `n_runs`, the `started_at` gaps, `agent_commits` per record, and whether the second report
   named the second fault.

| Observed | Baseline is | RQ3's headline becomes |
|---|---|---|
| two records, both faults fixed, starts far apart | **sequential** | `time_to_last_fix`, MTTR, collision safety |
| two records, second diagnosis names the first fault's leftovers | **state-polluted** | correctness and context isolation |
| one record only | **dropped** — find the cause | `detected` |

Exactly one row goes into §5.1, in one sentence, with the code path that produces it cited.

**What the harness must carry either way.** The three outcomes need different evidence, so collect
all of it — this is required regardless of which row applies:

| Field | Why |
|---|---|
| `n_runs` | distinguishes one record from two |
| `started_at` per record, and the gaps between them | the gap **is** the lock waiting time |
| `agent_commits` per record | proves each run had its own budget and branch |
| `run_id` set | proves no record was overwritten by a duplicate id |

A queued alert, a dropped alert and a harness bug all present as "fewer records than faults". Only
these four fields tell them apart. That is the real reason to build multi-record collection early —
not because the baseline wording depends on it.

**A trap worth recording.** `build_run_id` derives the id from `(scenario, seed, model, sha7)`, so
two synthetic payloads posted with the same `?seed=` produce the **same** `run_id`, and the second
`RunRecord` silently **overwrites** the first. Real Alertmanager deliveries carry no `seed`, get a
timestamp-plus-uuid suffix, and stay distinct. A synthetic multi-fault test that passes `seed=`
destroys its own evidence, and the overwrite is indistinguishable from a dropped run.

### Consequences

- Good: §5.1 states an observed fact with a cited code path, for almost no cost
- Good: the lock's queueing behaviour becomes an RQ3 *latency* finding (`run_gaps_s`)
- Good: multi-record collection also protects against the duplicate-`run_id` trap
- Good: the experiment validates the harness code that must exist anyway, before Phase 4
- Good: the only vigil change is two output fields, so the baseline claim holds
- Good: teardown is a branch reset — cheap and total
- Bad: if the answer is "one record", the cause still has to be found; the code says it queues, so a
  drop means something else is wrong, possibly the poller or Alertmanager grouping
- Bad: the harness gains a quiet-period heuristic that needs its own test
- Bad: the fork carries a patch outside `eval/`, and because the field now feeds a headline metric
  it is harder to drop later without changing the RQ1 instrument
- Bad: a branch reset discards anything else on that branch, so it must target the baseline ref
  (ADR-0001)
- Neutral: `_RUN_LOCK` is one global `asyncio.Lock`, so the experiment also measures whole-system
  behaviour under alert bursts
- Neutral: if upstream adds these fields, drop the patch and switch the adapter over

**Validation Status:** Not started. Neither the fields nor the reset step exist, and the two-fault
run has not been executed. The quote "sequential single-fault processing" comes from the Exposé. The
reading of `_RUN_LOCK` as a queue is verified by reading `main.py` and `agent.py`, and has not been
observed live.

### Confirmation

The decision holds as long as:

- `FORK.md` lists the `root_cause` patch under "Adapter-required patches", with commit hashes and the
  statement that it is output-only
- No code path in diagnosis, remediation, the watchdog, the gate or rollback reads the new fields
- `eval/` never mutates a `RunRecord` after it is written; scores go in a separate file
- The field is copied from `DiagnosisReport.root_cause` verbatim, not re-summarised
- §5.1 describes the observed behaviour and cites the code path, rather than asserting capability or
  incapability
- Multi-fault synthetic posts do not pass a shared `seed=`
- `_attempt.json` exists for every scored attempt, so the three outcomes stay distinguishable
- The quiet-period window is longer than `ORCHESTRATOR_RUN_TIMEOUT_S` (1800 s), or a second queued
  run is still in flight when scoring starts
- **The experiment does not block other work.** If it slips, everything else proceeds; only §5.1 waits

### Pros and Cons of the Options

#### Add two output fields (#R1)

- Good: stable contract; scoring reads a documented field
- Good: behaviour-neutral, so the baseline claim survives
- Bad: breaks the "nothing outside `eval/`" rule and must be documented
- Bad: a future upstream change to `RunRecord` may conflict

#### Parse the trace JSONL (#R2)

- Good: zero vigil change
- Bad: truncated at 1000 characters, so a long root cause is silently cut
- Bad: an internal debug format with no compatibility promise

#### Re-run diagnosis (#R3)

- Good: rich, unambiguous output
- Bad: costs tokens and measures something different

#### Patch SREGym's `recover_fault` (#T1)

- Good: cleanup lives where the fault was injected
- Bad: already correct for live faults; the gap is git, which SREGym does not model
- Bad: one more SREGym patch, which ADR-0002 avoided

#### Revert commits in adapter cleanup (#T2)

- Good: surgical; undoes exactly what a run did
- Bad: needs `agent_commits` to be complete and fails quietly if a commit went elsewhere

#### Reset the eval branch (#T3)

- Good: total, simple, needs no per-run knowledge
- Bad: discards anything else on the branch, so the baseline ref must be explicit

#### Keep the claim, skip measurement (#B1)

- Good: no time spent; the narrative is simple
- Bad: contradicts the Exposé's own definition of the baseline
- Bad: the lock queues, so the claim is probably false
- Bad: a reviewer reading `agent.py` finds the gap immediately

#### Measure as Phase 1's acceptance test (#B2)

- Good: near-zero cost, because two scenarios already work
- Good: validates the harness code that must exist anyway
- Good: independently reportable
- Bad: it can arrive at an inconvenient answer — but the answer changes a sentence, not a build

#### Measure as a blocking phase first (#B3)

- Good: the framing is settled before anything is designed
- Bad: **over-proportionate** — the testbed is needed whatever the answer is
- Bad: half a day inserted in front of work that does not depend on it

#### Measure in Phase 4 (#B4)

- Good: no extra step; verification already runs everything
- Bad: the record count would be noticed only after the scoring code is written, so the multi-record
  path would be bolted on late
- Bad: `_attempt.json` might be shaped around the wrong assumption

#### Assume the worst case (#B5)

- Good: rhetorically safe
- Bad: "worst case" is not a measurement, and a lower bound on an unmeasured quantity is a guess
- Bad: understates a real finding — the lock's latency cost is a better result than incapacity

## More Information

- Fork governance and the "only `eval/`" rule: `FORK.md`
- Measured-ref selection that #T3 must respect: ADR-0001
- Lock and run identity: `agents/orchestrator/src/orchestrator/agent.py` (`_RUN_LOCK`,
  `run_orchestration`, `build_run_id`)
- Fire-and-return webhook: `agents/orchestrator/src/orchestrator/main.py`
- Run timeout: `ORCHESTRATOR_RUN_TIMEOUT_S` (default 1800 s)
- Single-run follower to extend: `eval/src/eval/harness.py` (`trigger_and_wait`)
- Two scenarios to reuse: `eval/scenarios/k8s-3g/`, `eval/scenarios/k8s-4g/`
- Record shape: `agents/orchestrator/src/orchestrator/models.py`
- Trace format that #R2 would depend on: `agents/common/src/common/trace.py`
- Grading that consumes the records and the root cause: ADR-0006
