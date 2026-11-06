# About this Fork

This is a fork of [lucawalz/vigil](https://github.com/lucawalz/vigil), maintained by [Adrian Rossa](https://github.com/adrian-rossa) for a bachelor thesis at HdM Stuttgart (November 2026 – February 2027).

**Thesis title:** *Autonomous Multi-Layer Agentic Fault Diagnosis and Remediation on Declarative Infrastructure under Operational Load*

`vigil` is the result of Luca Walz's (@lucawalz) bachelor thesis. It diagnoses and repairs faults on a fully declarative NixOS/k3s cluster using LLM agents, typed MCP tools and GitOps, enabling transactional safety using rollbacks.

The goal of this work is to evaluate `vigil` under realistic conditions: several faults at once, across more than one layer, while traffic runs. `vigil` currently handles one fault at a time, sequentially. For evaluation `vigil` will be integrated and evaluated with [SREGym](https://github.com/SREGym/SREGym).

The research questions:

| Question | Focus |
|---|---|
| **RQ1** | How accurately can the agent system isolate host- and operating-system-level root causes, such as resource exhaustion in NixOS, from secondary symptom alerts at the Kubernetes layer? |
| **RQ2** | What impact do other factors, such as transactional and declarative GitOps reconciliation mechanisms, have on the mean time to recovery (MTTR) and can their latency be reduced? |
| **RQ3** | To what extent can `vigil` be extended to handle concurrent multi-fault scenarios more effectively than baseline sequential processing, measured by root-cause accuracy, remediation success, MTTR, token cost, and configuration-collision rate? |

To improve concurrent fault-handling, a deterministic config-path lock is implemented. Instead of an LLM deciding whether two repairs collide, compare the target paths they touch as strings. Same path, run them in order. Different paths, run them in parallel. Declarative infrastructure makes this possible because the target state is deterministic code.

## Differences to upstream `vigil`

| Area | Owner |
|---|---|
| Everything outside `eval/` | upstream. Not modified. |
| SREGym adapter, check scripts and latency probes in `eval/` | this work. |
| `FORK.md` | this work. |

Planned, not yet implemented: improved multi-fault handling. It will land only in the second series, never in the baseline.

`main` is a copy of `upstream/main` plus this file. None of my code lands there.

## About Measurements

Two series, both starting from the same upstream commit/release, so the difference between them is only this works contribution, making it measurable.

```
baseline = upstream vigil + SREGym adapter + check scripts + latency probes + series ref branch parameter
maineval = the same        + improved multi-fault handling
```

The baseline has to stay behaviour-neutral. The adapter injects faults, the probes observe, and neither touches how `vigil` diagnoses or repairs. Any further changes will be documented.

The measured state is always a git tag. If you want to reproduce a number from this work, check out the tag named in the text, not the branch.

## Status

| Area | State |
|---|---|
| Base | waiting for upstream: a `nixos_rebuild` confirmation bugfix, then release `v2.0.0` |
| SREGym adapter | next |
| Baseline run | planned November 2026 |
| Main evaluation | planned December 2026 |

Base will be pinned and tagged End of November 2026 when the code is ready for baseline measurement.

## If you are here for `vigil` itself

Read the [upstream README](README.md) and [docs/adr/](docs/adr/). This fork does not change the system, only how it is tested. Upstream's [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), [SECURITY.md](SECURITY.md) and [AI-USAGE.md](AI-USAGE.md) all still apply as written.

If you want to cite the system, cite `vigil`. If you want to cite the evaluation under concurrent multi-layer faults, cite this thesis and the tag.
