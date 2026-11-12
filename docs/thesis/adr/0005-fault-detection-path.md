---
status: Accepted
date: 2026-11-11
decision-makers: [Adrian Rossa]
consulted: []
informed: []
---

# ADR-0005: Fault detection path

## Context and Problem Statement

vigil reacts to Prometheus alerts. The repository has two delivery paths:

| Path | Mechanism | Latency |
|---|---|---|
| poller (on by default) | orchestrator polls `/api/v1/alerts` every 120 s | up to 120 s |
| Alertmanager webhook | `POST http://10.250.0.20:9099/webhook`, bearer auth | 10 s `group_wait` |

The Alertmanager receiver is configured. **The alert rules are not.** The overlay ships
kube-prometheus-stack defaults and no fault-specific rules, so no fault ever fires an alert.

The harness therefore synthesises an Alertmanager payload and posts it to the webhook
(`harness.py`, `_build_fault_event`). That works, but it removes detection from the measurement:
the time from fault onset to alert is fabricated, not observed. RQ2 asks what GitOps and
declarative reconciliation do to MTTR, and detection is part of MTTR — so a fabricated detection
step leaves a hole in the metric.

A second interaction matters. The poller **does not queue** an alert that arrives during an active
run: it checks `active_run_count()` and returns without starting a run. That alert produces **no
`RunRecord` at all**. It is not waiting behind a lock; it is gone.

| Path | Behaviour while a run is active | Result |
|---|---|---|
| poller | returns without starting a run | **silent data loss** — no record, no retry |
| webhook | creates a task at once | the task **queues** on the lock and runs afterwards |

So the poller is not part of the concurrency model at all. Turning it off does not *create*
concurrency; it removes a silent-loss path, so every alert becomes a run that can be observed and
measured (ADR-0009).

## Decision Drivers

- Detection latency is measured, not assumed
- A missing rule degrades one scenario, not a campaign
- Real alerts exercise the real path, including the existing bearer token and grouping
- No extra cluster dependency where one already exists
- Multi-fault runs must not lose alerts

## Considered Options

- #1 Synthetic payloads only
- #2 Real `PrometheusRule`s only, no fallback
- #3 Real rules primary, synthetic payload as a recorded fallback
- #4 Keep the poller as the detection mechanism

## Decision Outcome

**#3**: real rules primary, synthetic fallback, poller disabled.

### Rules to add

One `PrometheusRule` per fault class, under
`infra/overlays/hetzner/kubernetes/clusters/hetzner/infrastructure/monitoring/rules/`. Six cover the
scenario set, because several faults share an alert condition:

| Rule | Fires for |
|---|---|
| `KubeContainerWaiting` | bad image, bad tag |
| `KubeContainerOOMKilled` | memory limit below working set |
| `KubeDeploymentReplicasMismatch` | replicas set to 0, wrong selector |
| `KubePodNotReady` | readiness probe, finalizer deadlock |
| `NodeSystemdUnitInactive` | OS unit drift |
| `NodeSysctlDrift` | OS sysctl drift (recording rule plus alert) |

### The poller stays off

`PROM_POLLER_ENABLED=false` for every measured run. It **abandons** an alert while a run is active,
and that alert never becomes a `RunRecord` — so the run is not late, it does not exist. The webhook
path has no such skip: extra alerts queue and execute afterwards, which is the baseline to measure.

### The fallback is recorded, not silent

`detection: alert | synthetic` is a scenario field and a results column. The synthetic path is
legitimate when a rule does not exist yet or the symptom never becomes alertable (some OS drifts are
silent), but the split must be reported so a reader knows how much detection latency is observed.

One fix is needed for the synthetic path to be measurable at all: `_build_fault_event` sends
`"startsAt": "1970-01-01T00:00:00Z"` on every payload. Any `detect_s` derived from that is nonsense.
It must be the real post time (ADR-0006).

### Consequences

- Good: `detect_s` becomes measured instead of assumed
- Good: the claim strengthens from "we posted JSON" to "vigil answered real Prometheus alerts"
- Good: the real path is exercised, including bearer auth and `group_by: [alertname]`
- Good: a missing rule costs one scenario, not the campaign
- Good: every alert either produces a run or visibly fails to, so a missing record becomes evidence
- Bad: rules are fault-shaped, so each new scenario may need one; about half a day for the first set
- Bad: Alertmanager's 10 s `group_wait` is now inside the measurement, and must be reported as part
  of detection rather than subtracted
- Bad: `group_by: ['alertname']` means two simultaneous faults sharing an alertname arrive as
  **one** notification, so a multi-fault scenario must not pair them unless that is the point
- Neutral: rules live in `infra/`, so they are Flux-managed and survive campaigns

**Validation Status:** Not started. No rule exists; the six above are a design, and none has been
verified to fire. The fixed-epoch `startsAt` is verified by reading `_build_fault_event`.

### Confirmation

The decision holds as long as:

- Every measured run records which detection path produced its alert
- `PROM_POLLER_ENABLED` is false in the campaign environment, not just locally
- Each scenario with `detection: alert` has a rule verified to fire before Phase 4
- `detect_s` runs from the alert's `startsAt` to webhook receipt, and `startsAt` is never the epoch
- Multi-fault scenarios avoid pairing faults that share an `alertname`, or say why they share one

### Pros and Cons of the Options

#### Synthetic payloads only (#1)

- Good: zero cluster work; the harness already does it
- Good: perfectly reproducible timing
- Bad: detection is fabricated, so MTTR is incomplete and RQ2 has a hole
- Bad: the "real alert path" claim is unavailable

#### Real rules only (#2)

- Good: everything measured
- Bad: one missing rule blocks a scenario with no workaround, and OS-drift rules are the hardest
- Bad: debugging a non-firing rule mid-campaign is expensive

#### Real rules primary, synthetic fallback (#3)

- Good: measures what can be measured, records what cannot
- Good: degrades gracefully
- Bad: two code paths, so `detection` must be handled everywhere downstream

#### Keep the poller enabled (#4)

- Good: no rules needed, since Prometheus evaluates them internally
- Bad: the 120 s interval dominates the metric and hides the effect under study
- Bad: it returns without starting a run while another is active, so the alert leaves **no record**
  — a lost measurement, not a queued one

## More Information

- Receiver config:
  `infra/overlays/hetzner/kubernetes/clusters/hetzner/infrastructure/monitoring/kube-prometheus-stack/values-alertmanager.yaml`
- Poller skip condition: `agents/orchestrator/src/orchestrator/poller.py`
- Webhook endpoint and auth: `agents/orchestrator/src/orchestrator/main.py`
- Payload builder with the fixed-epoch `startsAt`: `eval/src/eval/harness.py` (`_build_fault_event`)
- Timing model that consumes `detect_s`: ADR-0006
- What happens to a queued alert, measured: ADR-0009
