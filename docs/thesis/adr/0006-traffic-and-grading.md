---
status: Accepted
date: 2026-11-11
decision-makers: [Adrian Rossa]
consulted: []
informed: []
---

# ADR-0006: Traffic generation and grading posture

## Context and Problem Statement

Two questions remain once the app is chosen: how to generate load, and how to grade.

**Load.** Part of this work is evaluation "under operational load" using a workload generator. SREGym already ships wrk2, Locust and stream generators, and its `SLOProblem` grades
real error rate, tail latency and goodput against a measured baseline.

Two mechanisms are available:

- **Locust inside `astronomy-shop`** — user journeys, ramped spawn, OpenTelemetry metrics, on by
  default in the chart
- **`wrk2` / a stepped fixed-rate profile** — one endpoint, coordinated-omission-corrected latency

**Grading.** vigil's own scoring returns one boolean per run. The testbed needs one verdict per
fault.

An earlier version of this record set three rules that a later review overturned:

| Old rule | Why it fails |
|---|---|
| `remediated` = the commit touched this fault's manifest path | a *process* metric: it credits a wrong edit to the right file and misses a correct fix made elsewhere. SREGym's paper cites a generic pod-restart loop passing 8 of 18 ITBench mitigation tasks merely by clearing the alert, which is why its oracles probe live state |
| rule-based primary, judge as a recorded second opinion | keyword matching cannot tell "named the origin" from "listed the same components as symptoms", and that distinction *is* RQ1 |
| an LLM judge is non-deterministic, so it cannot be primary | true of an unpinned judge; at temperature 0.0 with a pinned model it is reproducible, and the paper reports κ = 0.90 against a human expert for one judge model |

## Decision Drivers

- Realism is the base of the work, so load must produce user-visible behaviour
- RQ2 needs comparable latency across phases, which a ramping generator does not give directly
- A thesis needs reproducible numbers
- Every grading rule must be computable offline, from artefacts that persist
- **A fix is judged by the system's state, not by which file was edited**
- **RQ1 is a semantic question and needs a semantic instrument**
- Effort stays small; no mechanism becomes a workstream

## Considered Options

### Traffic

- #T1 No traffic
- #T2 Fixed-rate `wrk2` against one endpoint
- #T3 Stepped profile `[50, 200, 50]` rps
- #T4 Locust inside `astronomy-shop`
- #T5 Continuous variable load with a modelled diurnal curve

### Grading

- #G1 vigil's own `success_rate` only
- #G2 Rule-based per-fault scoring, `remediated` from the diff
- #G3 Rule-based per-fault scoring, `remediated` from a **health probe**
- #G4 SREGym's checklist judge as the sole instrument
- #G5 **Split by instrument**: probes for state, rules for mechanics, judge for semantics

## Decision Outcome

### Traffic: **#T4 and #T3**, for two different purposes

| Mode | Used for | Why |
|---|---|---|
| A — Locust (`astronomy-shop`) | the realism scenario, RQ1 evidence | user journeys, real traces |
| B — stepped (`vigil-app` `/healthz`) | the RQ2 timing scenario | fixed per-phase rate, precise percentiles |

Both cost about half a day in total. Mode A is a `values.yaml` knob; Mode B is a harness option.

**Why two:** they answer different questions. Locust gives realistic behaviour and coarser numbers;
a fixed rate gives precise numbers and no user behaviour. RQ1 needs the former, RQ2 the latter.

**Why not #T5:** a modelled diurnal curve needs a traffic model, a justification for its shape, and
grading that attributes each request to a phase. That is a contribution about traffic, not vigil.

### Grading: **#G5**, split by instrument

Each fault declares a `health_probe` of the **healthy** state, and each metric is owned by exactly
one instrument.

| Metric | Instrument | Derivation |
|---|---|---|
| `detected` | rule | report text mentions this fault's keywords or component |
| `attributed` | **judge** (+ rule, for agreement) | D1/D2 checklist dimensions vs. `[fault_spec]` ground truth |
| `remediated` | rule, **state** | the fault's `health_probe` passes within `window_s` |
| `collision` | rule, **path** | two faults' fixes touched the same file |
| `relationship_correct` | **judge** | cascade called cascade, independent called independent |
| `time_to_restore_s` | rule, state | fault onset → that fault's probe first passing |
| `time_to_last_fix_s` | rule, state | fault onset → last fault's probe passing |
| `touched_path` | rule | collision only; **never** for credit |

Path-touch survives only where the file genuinely matters. That is the whole change.

### Probe kinds

Three cover every scenario in the matrix:

| Kind | Checks | Example |
|---|---|---|
| `http_ok` | a request returns the expected status | `http://vigil-app.default.svc/healthz` → 200 |
| `kube_condition` | a resource condition | `deployment/vigil-app` `Available=True` |
| `host_cmd` | an OS fact | a sysctl reads back its declared value |

This is one new entry in `_SYMPTOM_CHECKS`, used in the healthy direction. The map already has nine
broken-state checks and no healthy one; the broken direction stays, because injection still has to
assert that the fault landed.

### The judge is co-primary for RQ1, pinned and validated

| Setting | Value | Why |
|---|---|---|
| rubric | `rca_checklists.yaml` v3.0 | 9 yes/no questions in 3 dimensions, threshold 0.70; D1-Q2 asks exactly whether the origin was separated from cascading victims |
| model | **one pinned model and version** | the paper reports κ = 0.90 for one judge model and **0.70–0.76** for the other two |
| temperature | `0.0` | already the default in `judge.py` |
| ground truth | `build_structured_root_cause` format | `[fault_spec] component=…; namespace=…` — already parsed by their judge |
| validation | **≈30 hand-labelled diagnoses**, report κ | their κ is their rubric on their problems; this thesis needs its own |

An unpinned judge is not a measurement. A judge number without a κ against human labels would be
the weakest claim in the thesis.

### What stays deterministic

All timings, all state checks and the collision metric. The judge contributes two metrics, each
marked `judge` in the results, so a reader can re-derive most of the table without any model.

### Unscored is not zero

`judge.py` separates "scored 0" from "never answered" (`unscored_reason`, `raw_response`,
`verdict=None`). Port that distinction. An unscored attempt must leave the numerator **and** the
denominator; counting it as a wrong diagnosis silently depresses accuracy. That is a bug they
already fixed and it is easy to reintroduce.

### Consequences

- Good: `remediated` measures the system, so a baseline that fixes both faults sequentially is not
  scored 1/2
- Good: RQ1 has an instrument that can answer its actual question
- Good: most metrics stay deterministic and re-scorable without a model
- Good: realism is supported by evidence, for about half a day
- Good: RQ2 gets precise per-phase numbers from a separate, controlled run
- Bad: probes cost more than a diff check — polling, timeouts, a new kind of scenario data
- Bad: the judge adds a pinned model dependency and about a day of validation
- Bad: two traffic modes and two applications mean more surface area
- Bad: `LOCUST_HEADLESS=true` must be set, or the generator starts a UI and does not autostart
- Neutral: the Exposé's claim that SREGym lacks traffic simulation is wrong and must be corrected in
  the thesis rather than repeated

**Validation Status:** Not started. Neither traffic mode has been exercised, no probe has been
written, and no κ has been computed.

### Confirmation

The decision holds as long as:

- `remediated` is never computed from a diff
- every fault has a `health_probe` that fails before the fix and passes after it
- the judge model and version are recorded in every `_judge.json`
- a κ against ≥30 hand labels exists before any judge number reaches the results chapter
- rule-based and judge verdicts are both stored, so agreement can be recomputed later
- the RQ2 scenario uses a fixed rate and the realism scenario uses Locust, and the two are never
  pooled
- the thesis does not repeat the Exposé's incorrect premise about SREGym and traffic

### Pros and Cons of the Options

#### No traffic (#T1)

- Good: nothing to configure
- Bad: no realism claim, and RQ2 has no load to measure latency under

#### Fixed-rate `wrk2` (#T2)

- Good: simplest precise option
- Bad: one URL, no user behaviour, so realism is unsupported

#### Stepped profile (#T3)

- Good: two load levels give a latency-versus-load observation
- Bad: still one endpoint

#### Locust inside the application (#T4)

- Good: user journeys, real traces, zero integration work
- Bad: ramping load makes per-phase comparison awkward; spawn rate is not a fixed rate

#### Variable modelled load (#T5)

- Good: closest to production
- Bad: needs a traffic model and phase attribution — a separate research contribution

#### vigil's own `success_rate` only (#G1)

- Good: no new code
- Bad: one boolean cannot express per-fault outcomes, so RQ3 has no data

#### Rule-based with diff-based `remediated` (#G2)

- Good: no model dependency, fully deterministic
- Bad: credits a wrong edit to the right file
- Bad: misses a correct fix made elsewhere
- Bad: cannot answer RQ1, because symptom lists match the same keywords as causes

#### Rule-based with probe-based `remediated` (#G3)

- Good: `remediated` becomes a real measurement
- Good: still deterministic
- Bad: still cannot separate cause from symptom, so RQ1 stays unanswerable

#### Judge as sole instrument (#G4)

- Good: best semantic matching, and one grading path to maintain
- Bad: makes *every* number depend on a model
- Bad: mechanical facts would be second-hand

#### Split by instrument (#G5)

- Good: each metric uses the instrument that can actually answer it
- Good: most of the table stays reproducible without a model
- Bad: two grading paths and two output files, one of which costs tokens
- Bad: `attributed` is computed twice, so agreement must be reported, not hidden

## More Information

- Scoring output: `eval/runs/<attempt_id>_score.json`, `_judge.json`, `_timing.json`, `_attempt.json`
- Timing output: ADR-0009
- Noise as a separate controlled factor: ADR-0007
- Judge implementation: `SREGym/sregym/conductor/oracles/llm_as_a_judge/judge.py`
- Rubric: `SREGym/sregym/conductor/oracles/llm_as_a_judge/rca_checklists.yaml`
- Ground-truth format: `SREGym/sregym/conductor/problems/base.py` (`build_structured_root_cause`)
- Per-cause aggregate pattern: `SREGym/sregym/conductor/oracles/compound.py`
- Broken-state checks to extend: `eval/src/eval/harness.py` (`_SYMPTOM_CHECKS`)
- Scenario contract carrying `detection:`, `workload:`, `health_probe:`: ADR-0003
