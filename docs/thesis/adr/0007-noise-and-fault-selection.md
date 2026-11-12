---
status: Accepted
date: 2026-11-12
decision-makers: [Adrian Rossa]
consulted: []
informed: []
---

# ADR-0007: Noise as a controlled factor, and which SREGym faults to port

## Context and Problem Statement

Two decisions decide what the testbed actually measures: the faults, and the distraction around
them. Both were taken together because they shape the same matrix.

**Noise.** **Accuracy under noise** is among the main metrics of this work, and describes the agent
working "while being distracted by independent concurrent faults and operational noise". Noise is **environment**, not vigil behaviour, it changes what the world looks
like, not what vigil does.

**Faults.** The candidate pool is large: 90 problems, 59 of which reference `astronomy-shop` alone.
Porting all of them is not the research, and porting the easiest ones produces a benchmark that
tests nothing new. Selection has to follow the research questions, not availability.

## Decision Drivers

- RQ1 is about **root-cause isolation across layers**; cross-layer OS faults are its subject
- RQ3 needs an independent pair with a **severity order**, or MTTR averages unrelated harms
- RQ2 needs faults whose repair path exercises **git → gate → reconcile → watchdog**
- Every ported fault needs an injector, a reset, an alert rule and a probe; effort is bounded
- A ported fault is verified against its **problem code**, not its one-line description
- Noise must be comparable across runs, so it is a factor rather than a per-run constant

## Considered Options

### Noise

- #N1 No noise
- #N2 Chaos Mesh, reproducing SREGym's mechanism exactly
- #N3 Two patterns on SREGym's schedule, without Chaos Mesh
- #N4 Continuous low-level noise throughout a run
- #N5 Noise as extra faults in the `faults:` list

### Fault selection

- #S1 Port the largest number of problems
- #S2 Port the easiest problems (single manifest edits)
- #S3 Port by research question: one clean case per RQ, plus the OS faults
- #S4 Port nothing; author fresh scenarios
- #O1 Skip OS faults — SREGym's are kubeadm-shaped
- #O2 Port `ephemeral_port_range` only
- #O3 Port all three that map onto NixOS declarative state
- #O4 Rewrite SREGym's kubeadm injectors for K3s

## Decision Outcome

**#N3** and **#S3 with #O3**.

### Noise: two patterns on SREGym's schedule

SREGym's schedule is reproducible without its infrastructure, and the schedule is the part that
matters:

| SREGym property | Value | Consequence |
|---|---|---|
| patterns per burst | `MAX_CONCURRENT = 2` | two independent patterns at once |
| burst duration | `DURATION = 120` | each lasts 2 minutes |
| cooldown | `COOLDOWN = 300` | a burst every 5 minutes |
| mechanism | Chaos Mesh CRDs | needs a DaemonSet on every node plus a runtime socket per node |

Chaos Mesh is a means to "two unrelated things break briefly, repeatedly". `kubectl delete pod` and a
CPU-stressing sidecar reach the same end. SREGym's own reported effect is that diagnosis success
fell for **every** agent–model pair it tested (61.5 % → 51.5 % for one), and that is observable with
two crude patterns.

```yaml
noise:
  enabled: true
  schedule_s: 300          # burst every 5 minutes  (SREGym COOLDOWN)
  duration_s: 120          # each lasts 2 minutes    (SREGym DURATION)
  patterns: 2              # two at once             (SREGym MAX_CONCURRENT)
  kinds: [pod_delete, cpu_stress]
  target: unrelated        # never the fault's own component
```

| Pattern | Mechanism | Why it is already available |
|---|---|---|
| `pod_delete` | `kubectl delete pod` on an unrelated pod | the harness already shells `kubectl` |
| `cpu_stress` | the `stress-ng` sidecar in `vigil-app` | already in the manifest as `mem-load` |

`network_delay` is deliberately omitted: it needs `tc`, which needs a privileged per-node helper,
which is Chaos Mesh by another name.

**It is a factor, not a constant.** Same scenario, `noise.enabled: false` vs `true`, five seeds
each. That is a paired design: the difference between arms is the finding, and no scenario is
re-authored. Cost: 2–3 scenarios × 5 seeds × 2 settings ≈ 20–30 extra runs, all waiting rather than
working, plus about half a day to write the injector.

**Two interactions must be recorded, not smoothed over.**

1. *Noise alerts reach the webhook.* A deleted unrelated pod fires `KubePodNotReady` too. That is
   the point, but vigil then spends runs on noise, and those queue behind the lock. Time-to-last-fix
   grows even when the real fix was fast. That is lock contention, and calling it a diagnosis
   failure would be wrong.
2. *Distractor alerts pollute attribution.* With `detection: synthetic` the scenario controls which
   alert vigil consumes; with real rules it does not. So a noise run records **which alertname the
   run consumed**, and noise-only runs are scored separately rather than counted as misses.

Both are why `_attempt.json` records `n_runs` and the inter-run gaps: they separate "the agent was
confused" from "the agent was queued".

### Faults: three OS faults, checked against their code

| SREGym problem | Its actual mechanism | NixOS/K3s equivalent | Verdict |
|---|---|---|---|
| `ephemeral_port_range_hotel_reservation` | patches every deployment's `securityContext.sysctls` to `net.ipv4.ip_local_port_range = "32000 32003"`, excluding `consul`/`jaeger`, then waits for rollouts | `boot.kernel.sysctl."net.ipv4.ip_local_port_range"`, or the same pod-level sysctl | **port** — 4 ports left, `EADDRNOTAVAIL`, pods stay `Running` |
| `node_conntrack_exhaustion_hotel_reservation` | lowers `nf_conntrack_max` on one node to ≤262144, then a gateway (16 ports) and a client pod hold ~10,000 connections until the table exceeds 98 % | `boot.kernel.sysctl."net.netfilter.nf_conntrack_max"` plus the same helpers | **port, with caveats** — needs two helper deployments and ≥2 workers |
| `kubelet_eviction_threshold_misconfig` | pins a service via `nodeName`, then rewrites `/var/lib/kubelet/config.yaml` so `nodefs.available` exceeds the real free-space ratio, producing a false `DiskPressure` | **`--kubelet-arg=eviction-hard=nodefs.available<…` in `services.k3s.extraFlags`** | **port — and it is better here** |

### Why the eviction fault is the strongest single argument in the thesis

Upstream's `inject_fault` patches the `nodeName`, waits for the rollout, runs `sed -i` on
`/var/lib/kubelet/config.yaml`, waits for `DiskPressure=True`, and on recovery `sed`s the file back
before removing the `nodeName`. Two live edits to a config file outside any controller, and the
recovery has to restore an unknown previous value.

On a NixOS stack the same fault is **one declared line**:

```nix
services.k3s.extraFlags = [ "--kubelet-arg=eviction-hard=nodefs.available<95%" ];
```

The fault and its repair are the **same object**. There is no live state to drift, no unknown value
to restore, and "what was the original threshold?" is answered by `git show`. That is RQ1's OS-layer
argument made concrete, using a fault an existing benchmark injects the hard way.
`infra/nixos/modules/k3s/agent.nix` already uses `extraFlags` for node labels, so the mechanism is
proven in this repo before it becomes a fault.

### The independent pair for RQ3

From the paper's Figure 4b: a **port misconfiguration on a user-facing service**, plus a **scheduler
misconfiguration that only makes an internal observability pod unschedulable**.

| Fault | Layer | `severity` | Why it is in the pair |
|---|---|---|---|
| port misconfiguration, user-facing service | K8s manifest | `user_facing` | the harm a customer sees |
| scheduler misconfiguration, internal observability pod | K8s manifest | `internal` | the harm nobody notices yet |

Both are single-manifest edits, so they compose from the existing `k8s-4g` injector machinery.

**Why the severity order matters:** without it, an agent that fixes the internal pod first and the
customer path second produces the same run-level MTTR as the reverse. With `severity` and per-fault
`time_to_restore_s`, the difference is visible — and that difference is where RQ1 and RQ3 overlap.

**Expected collision rate for the baseline: zero.** `GIT_COMMIT_BUDGET = 1` and per-run branches mean
the baseline structurally cannot collide. The metric only becomes interesting after the architecture
change, so this scenario belongs in the baseline set precisely as the reference point.

### Deliberately not ported

| Not ported | Reason |
|---|---|
| faults needing an app that will not be deployed (`saleor`, `frappe`, `slack_spine`, …) | each is a second application and a second failure surface |
| `assign_non_existent_node` | upstream deprecated it after 100 % pass@3, and `k8s-4g` already covers a bogus node selector |
| faults whose repair is not declarative (live `kubectl patch`, restart loops) | vigil has no such tool, and giving it one changes the system under study |
| `priority_preemption_cascade`, admission-webhook timeouts, other control-plane faults | they need a kubeadm control plane or a mutating webhook this cluster does not run |

### Consequences

- Good: accuracy under noise becomes measurable, which the Exposé asks for
- Good: no Chaos Mesh, no privileged DaemonSet, no runtime socket
- Good: a paired design means no new scenarios and a clean comparison
- Good: three OS faults give RQ1 a genuine cross-layer case
- Good: the strongest of them is *cleaner* on NixOS, a positive result rather than an apology for a
  port
- Good: the selection is small enough to verify one at a time and big enough to fill the matrix
- Bad: two crude patterns are not SREGym's catalogue, so results are not comparable to its published
  noise deltas — only the schedule is
- Bad: noise-only runs consume tokens and wall-clock, so the noisy arm is ~20–30 runs of waiting
- Bad: `cpu_stress` shares the node with the app, so noise can slow the *repair*, not just the
  diagnosis
- Bad: `node_conntrack_exhaustion` needs two helper deployments and ≥2 workers, so it is the most
  expensive port and the first to slip
- Bad: `ephemeral_port_range` has two possible shapes (pod-level or node-level sysctl), and the
  node-level one changes the layer under test from app to OS
- Bad: ported faults inherit severity claims designed for `hotel_reservation`
- Neutral: `astronomy-shop` is now required by two of the three, which couples the OS scenarios to
  ADR-0004

**Validation Status:** Read, not run. The three mechanisms were read in their source
(`_patch_sysctl` with `BAD_RANGE = "32000 32003"`; `conntrack_max_cap = 262144` with
`inject_ratio_threshold = 0.98`; `_description` and `inject_disk_pressure`). The noise constants are
read from `generators/noise/manager.py`. Nothing has been attempted on K3s or NixOS.

### Confirmation

The decision holds as long as:

- The same scenario is always run with noise both off and on, so the comparison never rests on a
  cross-scenario assumption
- Noise targets never overlap the fault's own component
- Noise-only runs are excluded from `detected`/`remediated` rates and reported separately
- Lock contention from noise is reported as latency, not as a wrong diagnosis
- Clean and noisy results are never pooled in one average
- Each ported fault is read against its problem code before authoring — the eviction fault computes
  its threshold from kubelet stats at injection time, which is more than a static value
- The NixOS shape of each ported fault is **declared state**, so `git_commit_nix` is a valid repair
  and a `both_drift` classification is meaningful
- `reset.sh` restores the sysctl, the flag and the branch; a ported OS fault that leaves host state
  modified silently invalidates every later seed
- The severity pair is scored per fault, never as one MTTR
- A fault that turns out not to be injectable on K3s is dropped, not faked with a synthetic alert
  that implies it happened

### Pros and Cons of the Options

#### No noise (#N1)

- Good: nothing to build; every run is clean
- Bad: a metric the Exposé names goes unanswered
- Bad: the thesis cannot say anything about behaviour under distraction

#### Chaos Mesh exactly as SREGym (#N2)

- Good: direct comparability with published noise numbers
- Good: a rich catalogue (network delay, loss, partition, IO)
- Bad: a DaemonSet on every Linux node, tolerating every taint
- Bad: needs the container-runtime socket per node
- Bad: finalizers stick when the target pod is in `CrashLoopBackOff`, and the code carries a
  workaround that patches them off — operational debt for a thesis
- Bad: days, not hours

#### Two patterns on SREGym's schedule (#N3)

- Good: reproduces the schedule and the shape in about half a day
- Good: no cluster-wide agent
- Good: both patterns already exist in this repo
- Bad: not comparable to SREGym's published deltas, only to its design
- Bad: no network-layer noise

#### Continuous low-level noise (#N4)

- Good: closer to a busy production cluster
- Bad: no clean reference arm, so the effect cannot be isolated
- Bad: a run is never quiet, so a late failure cannot be attributed to the fault

#### Noise as faults (#N5)

- Good: one mechanism for everything
- Bad: a **category error** — noise is not injected to be fixed, and grading would count it
- Bad: `relationship` and `severity` would need meaningless values

#### Port the largest number (#S1)

- Good: widest coverage, and a bigger number in the abstract
- Bad: each needs an injector, reset, rule and probe; verification is the bottleneck
- Bad: most reference applications that will not be deployed

#### Port the easiest (#S2)

- Good: fast, and single-manifest edits compose cleanly
- Bad: produces a benchmark a single-fault baseline can already pass
- Bad: tests nothing about the OS layer, which is RQ1's subject

#### Port by research question (#S3)

- Good: every scenario exists because a question needs it
- Good: the matrix stays small enough to verify one at a time
- Bad: the selection is a judgement and must be justified per scenario

#### Port nothing (#S4)

- Good: no inherited assumptions
- Bad: throws away 90 validated fault designs and their ground-truth wording
- Bad: "we invented our own faults" is a weaker claim than "we ported a published benchmark's"

#### Skip OS faults (#O1)

- Good: the K8s-only matrix is much cheaper
- Bad: removes RQ1's cross-layer case, the thesis's first research question

#### `ephemeral_port_range` only (#O2)

- Good: cheapest; a pod-level sysctl needs no host change
- Bad: does not use the declarative-NixOS argument, because the fault would live in the pod spec

#### All three (#O3)

- Good: one clean case, one hard case, one that is better here than upstream
- Bad: conntrack is expensive and may slip

#### Rewrite the kubeadm injectors for K3s (#O4)

- Good: maximum fidelity to upstream
- Bad: K3s has no `kubelet` unit and no `/var/lib/kubelet/config.yaml` in that form, so the rewrite
  would be a different fault wearing the same name

## More Information

- Noise schedule and Chaos Mesh install: `SREGym/sregym/generators/noise/manager.py`
- Noise pattern catalogue: `SREGym/sregym/generators/noise/catalog.py`
- Sidecar reusable for `cpu_stress`:
  `infra/overlays/hetzner/kubernetes/clusters/hetzner/apps/vigil-app.yaml`
- Portable-fault sources: `SREGym/sregym/conductor/problems/ephemeral_port_range_hotel_reservation.py`,
  `.../node_conntrack_exhaustion.py`, `.../kubelet_eviction_threshold_misconfig.py`
- Declared-state mechanism already in use: `infra/nixos/modules/k3s/agent.nix` (`extraFlags`),
  `infra/nixos/profiles/hetzner.nix`
- Sysctl-drift scenario to model the reset on: `eval/scenarios/os-drift-sysctl/`
- Scenario contract carrying `noise:` and `severity:`: ADR-0003
- Grading that consumes the probes and severity: ADR-0006
- Why `astronomy-shop` is required by two of the three: ADR-0004
