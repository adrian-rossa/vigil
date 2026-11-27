# Eval Campaign Aggregation Report

Total runs: 7 across 1 models and 3 scenarios.

Target: hetzner.

## Per-Model Summary

| Model | N | Success Rate | Mean MTTR (s) | Std MTTR (s) | Diag. Accuracy | Destructive % | Rollback % | Rollback Success % | Mean In/Out Tokens | Mean Tool Calls | Mean Iterations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| deepseek-v4.1-flash:1017-cloud | 7 | 0.00 | — | — | 1.00 (1/1) | 0.29 | 0.00 | — | 99862.57/1437.43 | 12.43 | 1.29 |

## Per-Scenario Summary

0/7 runs passed, 7 agent-failed, 0 infra-error, 0 gate-uncertain, 0 awaiting-review, 17 not-run
0/3 scenarios passed all seeds

#### Kubernetes Layer

| scenario | pass | s1 s2 s3 s4 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| k8s-1g | no data | — — — — | — | — | — | — |
| k8s-2g | no data | — — — — | — | — | — | — |
| k8s-3g | no data | — — — — | — | — | — | — |
| k8s-3g-noise | no data | — — — — | — | — | — | — |
| k8s-3g-stepped | no data | — — — — | — | — | — | — |
| k8s-4g | no data | — — — — | — | — | — | — |
| k8s-5g | no data | — — — — | — | — | — | — |
| k8s-cascade-1 | no data | — — — — | — | — | — | — |
| k8s-pair-1 | 0/4 | ESC ESC ?? ESC | — | — | 2 | 22 |
| k8s-pair-1-noise | no data | — — — — | — | — | — | — |
| k8s-pair-1-stepped | no data | — — — — | — | — | — | — |
| k8s-pair-samefile-1 | no data | — — — — | — | — | — | — |
| k8s-rollback-1 | no data | — — — — | — | — | — | — |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

#### OS / NixOS Layer

| scenario | pass | s1 s2 s3 s4 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| os-1 | no data | — — — — | — | — | — | — |
| os-1g | no data | — — — — | — | — | — | — |
| os-drift-sysctl | no data | — — — — | — | — | — | — |
| os-drift-sysctl-stepped | no data | — — — — | — | — | — | — |
| os-portrange-1 | 0/1 | ?? — — — | — | 1/1 | 4 | 22 |
| os-stale-generation | no data | — — — — | — | — | — | — |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

#### Cross-Layer

| scenario | pass | s1 s2 s3 s4 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| cross-pair-1 | no data | — — — — | — | — | — | — |
| cross-pair-1-noise | no data | — — — — | — | — | — | — |

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
| os-portrange-1 | cross | — | N/A |
| k8s-pair-1 | k8s | — | N/A |
| k8s-1 | None | — | N/A |

## Multi-Fault & Headline MTTR

| Scenario | Attempts | Fault Remediated | All Remediated | Collision | Headline MTTR p50/p95 (s) | Mean Noise Runs |
|---|---:|---:|---:|---:|---|---:|
| k8s-pair-1 | 1 | 0.50 | 0.00 | 0.00 | —/— | 0.00 |
| os-portrange-1 | 1 | 1.00 | 1.00 | 0.00 | 484.43/484.43 | 0.00 |

## Phase Timing Percentiles

| Phase | N | p50 (s) | p95 (s) |
|---|---:|---:|---:|
| inject_s | 2 | 0.13 | 191.39 |

| detect_s | 2 | 0.00 | 9.69 |

| diagnosis_s | 2 | 12.45 | 39.14 |

| remediation_s | 2 | 1.58 | 4.35 |

| gate_s | 2 | 0.00 | 0.00 |

| reconcile_s | 2 | 0.00 | 0.00 |

| watchdog_s | 2 | 179.08 | 298.39 |

| active_run_s | 2 | 198.19 | 344.35 |

| lock_wait_s | 2 | 0.00 | 457.00 |

| total_s | 2 | 295.00 | 484.43 |

---

_Note: std values computed from 4 seeds per cell; treat as directional only._
