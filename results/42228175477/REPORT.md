# Eval Campaign Aggregation Report

Total runs: 8 across 1 models and 8 scenarios.

Target: runner.

## Per-Model Summary

| Model | N | Success Rate | Mean MTTR (s) | Std MTTR (s) | Diag. Accuracy | Destructive % | Rollback % | Rollback Success % | Mean In/Out Tokens | Mean Tool Calls | Mean Iterations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| gpt-oss:120b | 8 | 0.75 | 318.85 | 484.07 | 0.88 (7/8) | 0.12 | 0.12 | 1.00 | 58623.38/3565.88 | 6.50 | 4.62 |

## Per-Scenario Summary

6/8 runs passed, 2 agent-failed, 0 infra-error, 0 gate-uncertain, 0 awaiting-review, 5 not-run
6/8 scenarios passed all seeds

#### Kubernetes Layer

| scenario | pass | s1 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| k8s-1g | 1/1 | OK | 143 | 1/1 | 7 | 7 |
| k8s-2g | 1/1 | OK | 150 | 1/1 | 6 | 9 |
| k8s-3g | 1/1 | OK | 149 | 1/1 | 6 | 11 |
| k8s-4g | 1/1 | OK | 144 | 1/1 | 6 | 6 |
| k8s-5g | 0/1 | ?? | — | 1/1 | 2 | 4 |
| k8s-rollback-1 | 1/1 | RB | 1302 | 1/1 | 7 | 10 |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

#### OS / NixOS Layer

| scenario | pass | s1 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| os-1 | no data | — | — | — | — | — |
| os-1g | no data | — | — | — | — | — |
| os-drift-sysctl | no data | — | — | — | — | — |
| os-stale-generation | no data | — | — | — | — | — |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

#### Infrastructure / Misc

| scenario | pass | s1 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| deceptive-2 | 1/1 | ESC | 25 | 1/1 | 1 | 1 |
| disk-pressure | no data | — | — | — | — | — |
| live-quota-injected | 0/1 | ?? | — | 0/1 | 2 | 4 |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

## Cross-Layer Escalation Accuracy

| Scenario | Layer | Correct/Total | Accuracy |
|---|---|---:|---:|
| k8s-1g | k8s | — | N/A |
| k8s-2g | k8s | — | N/A |
| k8s-3g | k8s | — | N/A |
| k8s-4g | k8s | — | N/A |
| k8s-5g | k8s | — | N/A |
| live-quota-injected | k8s | — | N/A |
| deceptive-2 | k8s | — | N/A |
| k8s-rollback-1 | k8s | — | N/A |

---

_Single-seed campaign - std values omitted._
