# Eval Campaign Aggregation Report

Total runs: 0 across 0 models and 0 scenarios.

Target: hetzner.

## Per-Model Summary

| Model | N | Success Rate | Mean MTTR (s) | Std MTTR (s) | Diag. Accuracy | Destructive % | Rollback % | Rollback Success % | Mean In/Out Tokens | Mean Tool Calls | Mean Iterations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|

## Per-Scenario Summary

0/0 runs passed, 0 agent-failed, 0 infra-error, 0 gate-uncertain, 0 awaiting-review, 0 not-run
0/0 scenarios passed all seeds

#### Kubernetes Layer

| scenario | pass | s1 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| k8s-1g | no data | — | — | — | — | — |
| k8s-2g | no data | — | — | — | — | — |
| k8s-3g | no data | — | — | — | — | — |
| k8s-3g-noise | no data | — | — | — | — | — |
| k8s-3g-stepped | no data | — | — | — | — | — |
| k8s-4g | no data | — | — | — | — | — |
| k8s-5g | no data | — | — | — | — | — |
| k8s-cascade-1 | no data | — | — | — | — | — |
| k8s-pair-1 | no data | — | — | — | — | — |
| k8s-pair-1-noise | no data | — | — | — | — | — |
| k8s-pair-1-stepped | no data | — | — | — | — | — |
| k8s-pair-samefile-1 | no data | — | — | — | — | — |
| k8s-rollback-1 | no data | — | — | — | — | — |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

#### OS / NixOS Layer

| scenario | pass | s1 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| os-1 | no data | — | — | — | — | — |
| os-1g | no data | — | — | — | — | — |
| os-drift-sysctl | no data | — | — | — | — | — |
| os-drift-sysctl-stepped | no data | — | — | — | — | — |
| os-portrange-1 | no data | — | — | — | — | — |
| os-stale-generation | no data | — | — | — | — | — |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

#### Cross-Layer

| scenario | pass | s1 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| cross-pair-1 | no data | — | — | — | — | — |
| cross-pair-1-noise | no data | — | — | — | — | — |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

#### Infrastructure / Misc

| scenario | pass | s1 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| deceptive-2 | no data | — | — | — | — | — |
| disk-pressure | no data | — | — | — | — | — |
| live-quota-injected | no data | — | — | — | — | — |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

## Cross-Layer Escalation Accuracy

| Scenario | Layer | Correct/Total | Accuracy |
|---|---|---:|---:|

---

_Single-seed campaign - std values omitted._
