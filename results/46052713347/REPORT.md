# Eval Campaign Aggregation Report

Total runs: 40 across 1 models and 15 scenarios.

Target: hetzner.

## Per-Model Summary

| Model | N | Success Rate | Mean MTTR (s) | Std MTTR (s) | Diag. Accuracy | Destructive % | Rollback % | Rollback Success % | Mean In/Out Tokens | Mean Tool Calls | Mean Iterations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| deepseek-v4.1-flash:1017-cloud | 40 | 0.12 | 148.94 | 5.95 | 0.25 (1/4) | 0.15 | 0.10 | 0.50 | 80605.62/1749.52 | 10.90 | 2.83 |

## Per-Scenario Summary

7/40 runs passed, 23 agent-failed, 9 infra-error, 0 gate-uncertain, 1 awaiting-review, 0 not-run
5/15 scenarios passed all seeds

#### Kubernetes Layer

| scenario | pass | s1 s2 s3 s4 s5 s6 s7 s8 s9 s10 s11 s12 s13 s14 s15 s16 s17 s18 s19 s20 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| k8s-1g | no data | — — — — — — — — — — — — — — — — — — — — | — | — | — | — |
| k8s-2g | no data | — — — — — — — — — — — — — — — — — — — — | — | — | — | — |
| k8s-3g | 1/1 | OK — — — — — — — — — — — — — — — — — — — | 151 | — | 7 | 12 |
| k8s-3g-noise | 1/1 | OK — — — — — — — — — — — — — — — — — — — | 143 | — | 7 | 10 |
| k8s-3g-stepped | 1/1 | OK — — — — — — — — — — — — — — — — — — — | 144 | — | 7 | 12 |
| k8s-4g | 1/1 | OK — — — — — — — — — — — — — — — — — — — | 149 | — | 7 | 11 |
| k8s-5g | no data | — — — — — — — — — — — — — — — — — — — — | — | — | — | — |
| k8s-cascade-1 | 0/1 | KO — — — — — — — — — — — — — — — — — — — | — | 1/1 | 7 | 19 |
| k8s-pair-1 | 0/4 | ESC ESC ESC ?? — — — — — — — — — — — — — — — — | — | — | 7 | 18 |
| k8s-pair-1-noise | 0/1 | ?? — — — — — — — — — — — — — — — — — — — | — | — | 1 | 20 |
| k8s-pair-1-stepped | 0/4 | ?? ESC ESC ESC — — — — — — — — — — — — — — — — | — | — | 7 | 16 |
| k8s-pair-samefile-1 | 1/1 | OK — — — — — — — — — — — — — — — — — — — | 158 | — | 7 | 12 |
| k8s-rollback-1 | no data | — — — — — — — — — — — — — — — — — — — — | — | — | — | — |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

#### OS / NixOS Layer

| scenario | pass | s1 s2 s3 s4 s5 s6 s7 s8 s9 s10 s11 s12 s13 s14 s15 s16 s17 s18 s19 s20 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| os-1 | no data | — — — — — — — — — — — — — — — — — — — — | — | — | — | — |
| os-1g | no data | — — — — — — — — — — — — — — — — — — — — | — | — | — | — |
| os-drift-sysctl | 0/1 | ESC — — — — — — — — — — — — — — — — — — — | — | 0/1 | 1 | 21 |
| os-drift-sysctl-stepped | 0/1 | KO — — — — — — — — — — — — — — — — — — — | — | 0/1 | 7 | 27 |
| os-portrange-1 | 0/1 | ESC — — — — — — — — — — — — — — — — — — — | — | 0/1 | 1 | 11 |
| os-stale-generation | no data | — — — — — — — — — — — — — — — — — — — — | — | — | — | — |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

#### Cross-Layer

| scenario | pass | s1 s2 s3 s4 s5 s6 s7 s8 s9 s10 s11 s12 s13 s14 s15 s16 s17 s18 s19 s20 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| cross-pair-1 | 0/1 | ?? — — — — — — — — — — — — — — — — — — — | — | — | 3 | 15 |
| cross-pair-1-noise | 0/1 | ESC — — — — — — — — — — — — — — — — — — — | — | — | — | — |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

#### Infrastructure / Misc

| scenario | pass | s1 s2 s3 s4 s5 s6 s7 s8 s9 s10 s11 s12 s13 s14 s15 s16 s17 s18 s19 s20 | MTTR mean±std | diag | iters | tools |
|---|---:|---|---:|---:|---:|---:|
| deceptive-2 | no data | — — — — — — — — — — — — — — — — — — — — | — | — | — | — |
| disk-pressure | no data | — — — — — — — — — — — — — — — — — — — — | — | — | — | — |
| live-quota-injected | no data | — — — — — — — — — — — — — — — — — — — — | — | — | — | — |

legend: OK success  RB rollback  ESC escalated  TO abort/timeout  SE setup_error  KO healthy but fix not credited

## Cross-Layer Escalation Accuracy

| Scenario | Layer | Correct/Total | Accuracy |
|---|---|---:|---:|
| k8s-3g | k8s | — | N/A |
| k8s-1 | None | — | N/A |
| k8s-4g | k8s | — | N/A |
| k8s-pair-1 | k8s | — | N/A |
| k8s-pair-samefile-1 | k8s | — | N/A |
| k8s-cascade-1 | k8s | — | N/A |
| k8s-3g-noise | k8s | — | N/A |
| k8s-pair-1-noise | k8s | — | N/A |
| k8s-3g-stepped | k8s | — | N/A |
| k8s-pair-1-stepped | k8s | — | N/A |
| os-drift-sysctl | os | 0/1 | 0.00 |
| cross-pair-1 | cross | — | N/A |
| os-portrange-1 | cross | — | N/A |
| cross-pair-1-noise | cross | — | N/A |
| os-drift-sysctl-stepped | os | 0/1 | 0.00 |

## Multi-Fault & Headline MTTR

| Scenario | Attempts | Fault Remediated | All Remediated | Collision | Headline MTTR p50/p95 (s) | Mean Noise Runs |
|---|---:|---:|---:|---:|---|---:|
| cross-pair-1 | 1 | 0.50 | 0.00 | 0.00 | —/— | 0.00 |
| cross-pair-1-noise | 1 | 0.50 | 0.00 | 0.00 | —/— | 0.00 |
| k8s-3g | 1 | 1.00 | 1.00 | 0.00 | 159.68/159.68 | 0.00 |
| k8s-3g-noise | 1 | 1.00 | 1.00 | 0.00 | 142.94/142.94 | 0.00 |
| k8s-3g-stepped | 1 | 1.00 | 1.00 | 0.00 | 142.96/142.96 | 0.00 |
| k8s-4g | 1 | 1.00 | 1.00 | 0.00 | 259.29/259.29 | 0.00 |
| k8s-cascade-1 | 1 | 1.00 | 1.00 | 0.00 | 2486.58/2486.58 | 0.00 |
| k8s-pair-1 | 1 | 0.50 | 0.00 | 0.00 | —/— | 0.00 |
| k8s-pair-1-noise | 1 | 0.50 | 0.00 | 0.00 | —/— | 0.00 |
| k8s-pair-1-stepped | 1 | 0.50 | 0.00 | 0.00 | —/— | 0.00 |
| k8s-pair-samefile-1 | 1 | 0.50 | 0.00 | 0.00 | —/— | 0.00 |
| os-drift-sysctl | 1 | 0.00 | 0.00 | 0.00 | —/— | 0.00 |
| os-drift-sysctl-stepped | 1 | 0.00 | 0.00 | 0.00 | —/— | 0.00 |
| os-portrange-1 | 1 | 0.00 | 0.00 | 0.00 | —/— | 0.00 |

## Phase Timing Percentiles

| Phase | N | p50 (s) | p95 (s) |
|---|---:|---:|---:|
| inject_s | 14 | 68.91 | 191.04 |
| detect_s | 14 | 9.18 | 14.58 |
| diagnosis_s | 13 | 10.99 | 41.54 |
| remediation_s | 14 | 10.53 | 48.84 |
| gate_s | 6 | 0.00 | 0.00 |
| reconcile_s | 6 | 0.00 | 0.00 |
| watchdog_s | 14 | 125.25 | 1615.28 |
| active_run_s | 14 | 151.00 | 2354.47 |
| lock_wait_s | 14 | 0.00 | 1873.00 |
| total_s | 14 | 168.00 | 2486.58 |

## LLM Judge & Rule Agreement

Judge model(s): kimi-k3.1-pro:cloud · Graded faults: 20 (unscored excluded: 0) · Judge attribution rate: 0.40 · Rule-vs-Judge agreement: 1.00 (kappa=1.00, n=20).

---

_Note: std values computed from 20 seeds per cell; treat as directional only._
