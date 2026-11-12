---
status: Accepted
date: 2026-11-11
decision-makers: [Adrian Rossa]
consulted: []
informed: []
---

# ADR-0003: Multi-fault scenario contract

## Context and Problem Statement

The testbed must inject several faults at once and score each one. That raises two questions:
what a scenario file contains, and how injection is structured.

A scenario is `scenario.yaml` + `inject.sh` + `reset.sh` and describes exactly one fault.
Adding a second means describing N faults, each with its own verification and score.

There is also a scoring gap. vigil's `_score_diagnosis_accuracy` reads `expected_action` and
`root_cause_keywords` and returns **one** boolean. A multi-fault run needs N verdicts. And the
relationship between faults has to be recorded data: RQ1 asks whether a cause was separated from
its symptom, which is unanswerable without knowing which was which.

## Decision Drivers

- Existing single-fault scenarios keep working unchanged
- No vigil change is needed for the schema to work
- Per-fault scoring is derivable from what vigil already emits
- The cascade/independent distinction is explicit data, not inference
- Five seeds of one scenario start byte-identical

## Considered Options

- #1 Separate scenario file per fault, grouped by directory
- #2 A `faults:` list inside the existing `scenario.yaml`
- #3 A new `scenario-multi.yaml` alongside the existing format
- #4 One injector script per scenario
- #5 One generic injector driven by `inject_params`

## Decision Outcome

**#2 and #5.**

### Schema: an additive, optional `faults:` list

```yaml
id: k8s-multi-1
layer: k8s
relationship: independent        # independent | cascading
detection: alert                 # alert | synthetic
workload: { enabled: false, profile: [50, 200, 50], ramp_s: 10 }
noise: { enabled: false, schedule_s: 300, duration_s: 120, patterns: 2,
         kinds: [pod_delete, cpu_stress], target: unrelated }

# top-level keys kept — vigil's own scoring reads only these
expected_action: git_commit_k8s
root_cause_keywords: ["memory", "limit"]
alert_name: KubeContainerOOMKilled

faults:
  - id: a
    component: vigil-app
    manifest: infra/overlays/hetzner/kubernetes/clusters/hetzner/apps/vigil-app.yaml
    expected_action: git_commit_k8s
    root_cause_keywords: ["memory", "limit"]
    alert_name: KubeContainerOOMKilled
    inject_params: { bad_memory_limit: "32Mi" }
    severity: user_facing        # user_facing | internal
    health_probe:
      kind: http_ok              # http_ok | kube_condition | host_cmd
      url: http://vigil-app.default.svc/healthz
      expect: 200
      window_s: 300
  - id: b
    component: vigil-app
    manifest: infra/overlays/hetzner/kubernetes/clusters/hetzner/apps/vigil-app.yaml
    expected_action: git_commit_k8s
    root_cause_keywords: ["replicas", "scale"]
    alert_name: KubeDeploymentReplicasMismatch
    inject_params: { bad_replicas: 0 }
    severity: user_facing
    health_probe:
      kind: kube_condition
      resource: deployment/vigil-app
      namespace: default
      condition: Available
      expect: "True"
      window_s: 300
```

Three properties make this cost no vigil change:

1. `faults:` is optional, so the 13 existing scenarios parse unchanged. Pydantic ignores unknown
   keys; an absent list is simply empty.
2. `expected_action` and `root_cause_keywords` stay at the top level, describing the *primary*
   fault, so `_score_diagnosis_accuracy` keeps returning a usable single-fault verdict.
3. `relationship:` is required only for multi-fault scenarios, where the scorer needs it and
   cannot infer it.

### `health_probe` and `severity` — added later, and they change what the schema is for

The first version of this contract described the **fault**. It did not describe the **fixed
state**, so scoring had to infer repair from a git diff. That is a process metric: it credits a
wrong edit to the right file (ADR-0006).

| Key | Purpose | Consumed by |
|---|---|---|
| `health_probe` | the healthy state, polled to `window_s` | `remediated`, `time_to_restore_s` |
| `severity` | `user_facing` or `internal` | ordering, and `time_to_restore_s` per path |

`severity` exists for one scenario: SREGym's Figure 4b pair, where a port misconfiguration breaks a
user-facing service while a scheduler misconfiguration only makes an internal observability pod
unschedulable. Without `severity` that pair yields one run-level MTTR that averages a customer
outage with a harmless annoyance, so an agent that fixes the wrong one first looks identical to
one that does not.

**`noise:` is an environment factor, not a fault.** It is absent by default and run both ways on
2–3 scenarios, so the difference is the finding. It must not be scored as a fault.

### Injection: one script, N data blocks

A single `inject.sh` and `reset.sh` read `inject_params`. Specificity lives in YAML, not shell.
The existing `k8s-1g` already works this way — 20 lines of `sed -i` driven by
`inject_params.bad_image` — so this is a small step, not a rewrite.

### Consequences

- Good: the 13 existing scenarios are untouched, so the control group needs no rework
- Good: per-fault scoring is computable offline from the `RunRecord`, the probes and the diff
- Good: `relationship` makes the cascade/independent distinction a recorded fact
- Good: `health_probe` makes `remediated` a state measurement, so a baseline that fixes two faults
  sequentially is not scored 1/2
- Good: one injector means 30 runs across 6 scenarios and 5 seeds start identically
- Bad: `relationship` is a judgement call per scenario and must be justified in the thesis
- Bad: the top-level keys now describe only the primary fault, so scoring vigil's own accuracy is
  lossy; readers must prefer `faults:` when present
- Bad: every fault needs a hand-written probe, so a scenario is not finished until its probe fails
  before the fix and passes after it
- Neutral: `inject_params` becomes a small DSL whose keys need documenting

**Validation Status:** Partially confirmed. `k8s-1g` demonstrates the data-driven pattern. The
`faults:` list is not yet in `ScenarioDefinition`, so backward compatibility is argued from
pydantic's defaults rather than measured. No probe has been written.

### Confirmation

The decision holds as long as:

- Every existing scenario still parses and runs without edits
- `_score_diagnosis_accuracy` still returns a meaningful verdict for multi-fault scenarios using
  the top-level keys
- No scenario encodes a mutation in `inject.sh` that could live in `inject_params`
- Every fault has a `health_probe` observed to fail before the repair and pass after it
- `reset.sh` restores host state (sysctls, systemd units) as well as cluster state — a drifting
  sysctl silently invalidates every seed after the first

### Pros and Cons of the Options

#### Scenario file per fault (#1)

- Good: each file stays simple and readable
- Bad: the harness must discover and merge N files and keep ids consistent
- Bad: no natural place for `relationship` or a per-fault probe

#### `faults:` list in the existing file (#2)

- Good: one file per scenario, found by the existing loader
- Good: additive, so nothing breaks
- Good: probe and severity sit next to the fault they describe
- Bad: two ways to express the same thing, which needs a stated precedence rule

#### Separate `scenario-multi.yaml` (#3)

- Good: no ambiguity about a file's shape
- Bad: two loaders, two validators, two sets of tests
- Bad: discourages converting an existing scenario

#### One injector per scenario (#4)

- Good: the script shows exactly what happens
- Bad: six scripts × five seeds must start byte-identical, and hand-written scripts drift
- Bad: cleanup logic is duplicated and diverges

#### One generic injector (#5)

- Good: identical by construction across scenarios and seeds
- Good: adding a scenario is a data edit, not a code edit
- Bad: reading a scenario means reading YAML then a script

#### Inferring `remediated` from the diff (#6, rejected)

- Good: no probe to write
- Bad: credits a wrong edit to the right file
- Bad: misses a correct fix made elsewhere
- Bad: incompatible with the lock — a baseline fixing both faults sequentially would score 1/2
  while having fixed 2/2

## More Information

- Existing scenario shape: `eval/scenarios/k8s-1g/`
- Loader to extend: `eval/src/eval/scenario.py` (`ScenarioDefinition`)
- Broken-state checks to extend with a healthy direction: `eval/src/eval/harness.py`
  (`_SYMPTOM_CHECKS`)
- vigil's single-fault scoring: `agents/orchestrator/src/orchestrator/agent.py`
  (`_score_diagnosis_accuracy`)
- Per-cause grading pattern: `SREGym/sregym/conductor/oracles/compound.py`
- Grading split that consumes `health_probe` and `severity`: ADR-0006
- Noise factor that consumes `noise:`: ADR-0007
