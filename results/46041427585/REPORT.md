# Eval Campaign Aggregation Report

Total runs: 1 across 1 models and 1 scenarios.

Target: hetzner.

## Per-Model Summary

| Model | N | Success Rate | Mean MTTR (s) | Std MTTR (s) | Diag. Accuracy | Destructive % | Rollback % | Rollback Success % | Mean In/Out Tokens | Mean Tool Calls | Mean Iterations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| deepseek-v4.1-flash:1017-cloud | 1 | 0.00 | — | — | — | 0.00 | 0.00 | — | 0/0 | 0 | 0 |

## Per-Scenario Summary

0/1 runs passed, 0 agent-failed, 1 infra-error, 0 gate-uncertain, 0 awaiting-review, 23 not-run
0/1 scenarios passed all seeds

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
| k8s-pair-1-noise | 0/1 | ?? | — | — | — | — |
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
| k8s-pair-1-noise | k8s | — | N/A |

## Multi-Fault & Headline MTTR

| Scenario | Attempts | Fault Remediated | All Remediated | Collision | Headline MTTR p50/p95 (s) | Mean Noise Runs |
|---|---:|---:|---:|---:|---|---:|
| cross-pair-1 | 1 | 0.50 | 0.00 | 0.00 | —/— | 0.00 |
| k8s-3g | 1 | 0.00 | 0.00 | 0.00 | —/— | 0.00 |
| k8s-4g | 1 | 0.00 | 0.00 | 0.00 | —/— | 0.00 |
| k8s-cascade-1 | 1 | 0.00 | 0.00 | 0.00 | —/— | 0.00 |
| k8s-pair-1 | 1 | 0.50 | 0.00 | 0.00 | —/— | 0.00 |
| k8s-pair-samefile-1 | 1 | 0.50 | 0.00 | 0.00 | —/— | 0.00 |
| os-drift-sysctl | 1 | 0.00 | 0.00 | 0.00 | —/— | 0.00 |
| os-drift-sysctl-stepped | 1 | 0.00 | 0.00 | 0.00 | —/— | 0.00 |
| os-portrange-1 | 1 | 0.00 | 0.00 | 0.00 | —/— | 0.00 |

## Phase Timing Percentiles

| Phase | N | p50 (s) | p95 (s) |
|---|---:|---:|---:|
| inject_s | 9 | 69.06 | 190.97 |

| detect_s | 0 | — | — |

| diagnosis_s | 0 | — | — |

| remediation_s | 0 | — | — |

| gate_s | 0 | — | — |

| reconcile_s | 0 | — | — |

| watchdog_s | 0 | — | — |

| active_run_s | 0 | — | — |

| lock_wait_s | 9 | 0.00 | 0.00 |

| total_s | 0 | — | — |

---

_Single-seed campaign - std values omitted._
