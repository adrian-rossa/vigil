# Eval Campaign Aggregation Report

Total runs: 6 across 2 models and 2 scenarios.

Target: hetzner.

## Per-Model Summary

| Model | N | Success Rate | Mean MTTR (s) | Std MTTR (s) | Diag. Accuracy | Destructive % | Rollback % | Rollback Success % | Mean In/Out Tokens | Mean Tool Calls | Mean Iterations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| gpt-oss:120b | 2 | 0.00 | — | — | 0.00 (0/2) | 0.00 | 0.00 | — | 74675/1403 | 5 | 1 |
| deepseek-v4.1-flash:1017-cloud | 4 | 0.00 | — | — | — | 0.00 | 0.00 | — | 107219.75/2869.25 | 13.50 | 7 |

## Per-Scenario Summary

0/6 runs passed, 6 agent-failed, 0 infra-error, 0 gate-uncertain, 0 awaiting-review, 8 not-run
0/2 scenarios passed all seeds

#### Kubernetes Layer

| scenario | pass | s1 s2 s3 s4 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| k8s-1g | no data | — — — — | — | — | — | — |
| k8s-2g | no data | — — — — | — | — | — | — |
| k8s-3g | no data | — — — — | — | — | — | — |
| k8s-4g | no data | — — — — | — | — | — | — |
| k8s-5g | no data | — — — — | — | — | — | — |
| k8s-pair-1 | 0/2 | ESC ESC — — | — | 0/2 | 1 | 5 |
| k8s-rollback-1 | no data | — — — — | — | — | — | — |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

#### OS / NixOS Layer

| scenario | pass | s1 s2 s3 s4 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| os-1 | no data | — — — — | — | — | — | — |
| os-1g | no data | — — — — | — | — | — | — |
| os-drift-sysctl | no data | — — — — | — | — | — | — |
| os-stale-generation | no data | — — — — | — | — | — | — |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

#### Infrastructure / Misc

| scenario | pass | s1 s2 s3 s4 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| deceptive-2 | no data | — — — — | — | — | — | — |
| disk-pressure | no data | — — — — | — | — | — | — |
| live-quota-injected | no data | — — — — | — | — | — | — |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

## Cross-Layer Escalation Accuracy

| Scenario | Layer | Correct/Total | Accuracy |
|---|---|---:|---:|
| k8s-pair-1 | cross | — | N/A |
| k8s-1 | None | — | N/A |

---

_Note: std values computed from 4 seeds per cell; treat as directional only._
