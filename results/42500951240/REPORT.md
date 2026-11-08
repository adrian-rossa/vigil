# Eval Campaign Aggregation Report

Total runs: 8 across 1 models and 8 scenarios.

Target: hetzner.

## Per-Model Summary

| Model | N | Success Rate | Mean MTTR (s) | Std MTTR (s) | Diag. Accuracy | Destructive % | Rollback % | Rollback Success % | Mean In/Out Tokens | Mean Tool Calls | Mean Iterations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| gpt-oss:120b | 8 | 0.75 | 310.78 | 474.21 | 0.88 (7/8) | 0.12 | 0.25 | 0.50 | 56022.38/3434.88 | 7.38 | 5.50 |

## Per-Scenario Summary

6/8 runs passed, 2 agent-failed, 0 infra-error, 0 gate-uncertain, 0 awaiting-review, 5 not-run
6/8 scenarios passed all seeds

#### Kubernetes Layer

| scenario | pass | s1 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| k8s-1g | 0/1 | ?? | — | 1/1 | 6 | 8 |
| k8s-2g | 1/1 | OK | 152 | 1/1 | 6 | 8 |
| k8s-3g | 1/1 | OK | 144 | 1/1 | 6 | 10 |
| k8s-4g | 1/1 | OK | 142 | 1/1 | 6 | 6 |
| k8s-5g | 1/1 | OK | 147 | 1/1 | 7 | 9 |
| k8s-rollback-1 | 1/1 | RB | 1272 | 1/1 | 6 | 10 |

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
| deceptive-2 | 0/1 | ?? | — | 0/1 | 6 | 6 |
| disk-pressure | no data | — | — | — | — | — |
| live-quota-injected | 1/1 | ESC | 7 | 1/1 | 1 | 2 |

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
