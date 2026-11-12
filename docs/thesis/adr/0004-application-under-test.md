---
status: Accepted
date: 2026-11-11
decision-makers: [Adrian Rossa]
consulted: []
informed: []
---

# ADR-0004: Application under test and licence posture

## Context and Problem Statement

The Exposé's realism claim rests on evaluating under operational traffic. That needs an
application with a user-facing request path, plus a way to generate load against it.

vigil's own application is `vigil-app`: nginx plus a `stress-ng` sidecar, with a real dependency on
`app-backend` via `REQUIRED_API_BASE` and a readiness probe on `/healthz`. That produces latency
which responds to breakage, but it has no user journeys and no telemetry.

Three candidates were considered, and each brings a licensing question.

| Candidate | Licence | Chart | Traffic | Telemetry |
|---|---|---|---|---|
| DeathStarBench upstream | Apache 2.0 per LICENSE; README says GPLv2 | `helm-chart/` plus dated raw YAML | `wrk2` scripts, manual | tracing support, not deployed |
| `astronomy-shop` via `xlab-uiuc/opentelemetry-helm-charts` | Apache 2.0 | maintained, `values.yaml`, JSON schema | **Locust, on by default** | OpenTelemetry throughout |
| `astronomy-shop` via `SREGym-applications` | **no licence file at repo root** | patched copy | inherited | inherited |

`SREGym-applications` is a submodule with a README and `.gitmodules` but no `LICENSE`. Its parent
project is MIT, but that does not extend to a vendored submodule with no licence of its own.

DeathStarBench's `LICENSE` says Apache 2.0 while its `README.md` still says GPLv2. The licence file
governs, but the inconsistency is worth recording so a reviewer does not read it as an error.

## Decision Drivers

- Realism needs real user paths and load, not a static endpoint
- Load generation should not become its own workstream
- Every vendored component needs a clear, file-level licence
- The app must fit a four-node `cpx22` cluster
- SREGym compatibility is desirable, since its scenarios reference these apps

## Considered Options

- #1 `vigil-app` only
- #2 DeathStarBench `hotelReservation` from upstream
- #3 `astronomy-shop` from `SREGym-applications`
- #4 `astronomy-shop` from `xlab-uiuc/opentelemetry-helm-charts` upstream
- #5 Podinfo as the measured application

## Decision Outcome

**#4**, with `vigil-app` kept as the control and Podinfo used only as a two-hour Flux smoke test.

### Why `astronomy-shop` from upstream

- a maintained Helm chart with `values.yaml` and a JSON schema, so a Flux `HelmRelease` is about
  15 lines
- **Locust ships enabled by default**, so traffic is a `values.yaml` knob, not a day of work
- OpenTelemetry throughout, so latency is measurable *and* explainable with traces
- about 10 pods, which fits the cluster
- 59 of SREGym's problem files reference it (hotel_reservation 64, social_network 32), so ground
  truth is inherited rather than invented
- **it enables the ported OS faults.** Of the three faults judged portable (ADR-0008),
  `kubelet_eviction_threshold_misconfig` needs a service pinned to a node and watched as it
  evicts, and conntrack exhaustion needs a connection-heavy client against a gateway. Only
  `ephemeral_port_range` works with a bare pod `securityContext.sysctl`

That last point was added later. The app was originally chosen for scenario count; it now also
makes the RQ1 cross-layer scenarios possible, so it is not the "realism extra" it once looked like.

### Why upstream and not SREGym's copy

The licence gap above. Taking the chart from `xlab-uiuc/opentelemetry-helm-charts` gives an explicit
Apache 2.0 licence and the unpatched chart, which a hand-written `HelmRelease` wants anyway.

### Why not DeathStarBench

It is a 2019 research artefact. Its `kubernetes/` directory is raw YAML rather than a configurable
chart, its images are dated, and it ships no load-generator integration. It costs more than
`astronomy-shop` and yields less.

### Why Podinfo is still worth deploying

It is Flux's own demo app with an official `HelmRelease` example. Two hours of work proves the Flux
ownership path — the riskiest new mechanism here — before a day goes into `astronomy-shop`.

### Cluster capacity is a real constraint

`astronomy-shop`'s load generator alone requests **1500Mi**. With about ten service pods plus the
OpenTelemetry collector, the `cpx22` (4 GB) workers are tight. Check `kubectl top nodes` before
deploying; if it does not fit, add a third worker (~5 €/month) or disable `llm`, `kafka` and
`fraud-detection`.

### Consequences

- Good: realism costs about half a day instead of two, because the chart supplies app, telemetry
  and load generation
- Good: licence posture is explicit and file-backed
- Good: ground truth for 59 faults comes free with the app choice
- Bad: a second application means a second Flux resource and a second failure surface
- Bad: 59 scenarios *reference* the app; they do not become runnable. Expect to port 10–15, each
  needing a git-shaped injector and an alert rule
- Neutral: the DeathStarBench licence discrepancy must be noted in the thesis, not resolved

**Validation Status:** Not started. Neither app has been deployed to this cluster, and the `cpx22`
capacity question is open.

### Confirmation

The decision holds as long as:

- Every vendored directory keeps its licence file
- The chart comes from `xlab-uiuc/opentelemetry-helm-charts`, not `SREGym-applications`
- The thesis has a "Third-party components" section listing each licence, including the
  DeathStarBench discrepancy
- `LOCUST_HEADLESS=true` in the override; the default `false` starts a web UI and does not autostart

### Pros and Cons of the Options

#### `vigil-app` only (#1)

- Good: already Flux-managed, no setup, no licence question
- Good: enough for RQ3 collision analysis
- Bad: no user journeys and no telemetry, so realism has no support
- Bad: latency reflects a probe, not application health

#### DeathStarBench `hotelReservation` (#2)

- Good: more SREGym scenarios reference it than any other app (64 files)
- Bad: raw YAML rather than a chart; dated images
- Bad: no bundled load generator
- Bad: readme/licence discrepancy needs explaining

#### `astronomy-shop` via `SREGym-applications` (#3)

- Good: exactly the copy SREGym's scenarios were written against
- Bad: no licence file at the repo root, so reuse rights are unclear

#### `astronomy-shop` via upstream (#4)

- Good: explicit Apache 2.0, maintained chart, bundled Locust and OpenTelemetry
- Bad: not SREGym's patched copy, so a scenario depending on a patch would need adapting
- Neutral: two hops from SREGym, so the provenance chain should be stated

#### Podinfo as the measured application (#5)

- Good: official Flux example, ten minutes to deploy
- Bad: no scenarios, no telemetry, no user paths

## More Information

- Control application: `infra/overlays/hetzner/kubernetes/clusters/hetzner/apps/vigil-app.yaml`
- Traffic modes that consume this app: ADR-0006
- SREGym's application list: `SREGym-applications` README
- Fault catalogue inherited from it: `SREGym/Problem List.md`
