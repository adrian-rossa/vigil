---
status: Accepted
date: 2026-11-11
decision-makers: [Adrian Rossa]
consulted: []
informed: []
---

# ADR-0002: Testbed runtime ownership

## Context and Problem Statement

The thesis needs a benchmark that evaluates vigil under concurrent, multi-layer faults with real
traffic. SREGym is the closest existing benchmark and the Exposé names it as the integration
target.

Reading both codebases surfaced four conflicts that are structural, not incidental:

1. **Deployment.** SREGym deletes the namespace of every problem it finishes
   (`deploy_app()` → `undeploy_app()` → `delete_namespace()`). vigil repairs through Flux, so the
   deletion races reconciliation.
2. **OS fault shape.** SREGym's OS injectors assume kubeadm: `systemctl stop kubelet`,
   `sed -i /var/lib/kubelet/config.yaml`. K3s embeds kubelet in the `k3s` process, so those faults
   cannot be injected at all.
3. **Cluster assumptions.** SREGym's provisioning playbook is ~143 Ubuntu tasks. Nothing in it
   targets NixOS or K3s, and nobody has verified it runs there.
4. **Agent sandbox.** SREGym runs each agent in its own container behind a filtered egress proxy.
   Accepting that means a driver, a result mount, an allowlisted endpoint and a firewall route.

Against this, vigil's harness already works: 13 scenarios, a `RunRecord` per run, an aggregator,
and a Hetzner campaign workflow with matrix, snapshots and teardown.

## Decision Drivers

- The comparison must isolate this work's contribution; infrastructure that fights the cluster
  produces failures unrelated to the research question
- The testbed must not change vigil's behaviour, so the baseline stays "unmodified vigil"
- Realism — a real app under real load — is the base of the work, not an optional extra
- Effort is bounded; an unverified cluster-compatibility question is a schedule risk
- Published comparability is nice to have, but secondary to answering RQ1–RQ3

## Considered Options

- #1 Register vigil as a SREGym agent
- #2 SREGym injects and stops; grade offline afterwards
- #3 SREGym as a library inside `vigil/eval/`
- #4 Reimplement vigil's agents inside SREGym's `clients/`
- #5 Rewrite SREGym's provisioning for NixOS and run everything there
- #6 vigil's own harness, with SREGym contributing knowledge only

## Decision Outcome

**#6.** vigil's harness owns the run. SREGym contributes five things that survive without its
runtime:

- the fault catalogue and root-cause wording (~90 problems, `Problem List.md`)
- the script-artifact bundle format (`fault.json` + `inject.sh` / `recover.sh` / `mitigate.sh`)
- the `DiagnosisJudge` module and its `rca_checklists.yaml` rubric, which score plain strings and
  need no `Problem`, no baseline and no cluster
- the per-cause grading pattern from `CompoundedOracle`
- the noise schedule (`MAX_CONCURRENT=2`, `DURATION=120`, `COOLDOWN=300`), reproduced without
  Chaos Mesh (ADR-0007)

**Why #2 is not a measurement path.** `MitigationOracle.capture_baseline()` stores deployment
names and replica counts **in memory**. A fresh process starts with an empty baseline, so the
"deployment deleted" and "scaled to 0" checks silently pass — a scaled-to-zero app would be graded
as success. That oracle is the default for about 68 problem files. Offline grading is therefore
usable for *diagnosis* only, where the judge needs no baseline.

**Why #5 was rejected:** it duplicates a working cluster, and SREGym's OS injectors would still
assume Ubuntu paths.

### Consequences

- Good: RQ1 gets full-pipeline evidence, because OS faults are NixOS-native and therefore
  repairable, not diagnosis-only
- Good: RQ3 gets per-fault granularity; SREGym offers one compound verdict per problem, the wrong
  shape for collision analysis
- Good: no driver, container, egress proxy, firewall port or SREGym patch is needed
- Good: no unverified K3s question blocks the schedule
- Good: three SREGym OS faults port cleanly (ADR-0008)
- Bad: results are not comparable with the published SREGym leaderboard
- Bad: the Exposé names "integration with SREGym" first, so the narrower scope must be reported to
  the supervisor before the evaluation chapter is written
- Bad: the judge's κ cannot be inherited from the paper; it must be re-measured here (ADR-0006)
- Neutral: fault ideas, grading design and the noise schedule still come from SREGym, and are
  cited as such

**Validation Status:** Not started. The K3s-incompatibility claim rests on reading
`inject_remote_os.py`; it has not been attempted against the cluster.

### Confirmation

The decision holds as long as:

- No benchmark run depends on SREGym's conductor, container or MCP server
- Every scenario is expressible as a `scenario.yaml` plus a fault script in `eval/`
- The diagnosis comparison is graded by SREGym's judge against SREGym problem text, so the
  comparability claim stays narrow but true
- The scope narrowing is stated wherever SREGym is cited as the benchmark

### Pros and Cons of the Options

#### Register vigil as a SREGym agent (#1)

- Good: SREGym's two-stage grading, container isolation and phase ledger come free
- Bad: namespace deletion conflicts with Flux ownership
- Bad: needs a SREGym patch to skip deploy and undeploy
- Bad: blocked on whether SREGym runs on K3s at all

#### SREGym injects, grade offline (#2)

- Good: smallest possible adapter; `run-oracle.py` already proves the pattern
- Good: diagnosis grading stays correct, since the judge needs no baseline
- Bad: mitigation grading silently loses its baseline checks and reports false successes
- Bad: the conductor is gone, so baseline and propagation windows must be recreated by hand

#### SREGym as a library inside `eval/` (#3)

- Good: keeps vigil's `RunRecord` native
- Bad: re-implements what the conductor already does well
- Bad: the re-implementation becomes thesis debt

#### Reimplement vigil's agents inside SREGym (#4)

- Good: full grading path
- Bad: rebuilds the MCP servers, the GitOps path and the NixOS path
- Bad: the object of study becomes the rewrite

#### Rewrite SREGym's provisioning for NixOS (#5)

- Good: everything runs on one platform
- Bad: ~143 Ubuntu tasks to translate
- Bad: OS injectors still assume kubeadm, so the work is not even sufficient

#### vigil harness plus borrowed knowledge (#6)

- Good: works today, on this cluster, with no open compatibility question
- Good: zero required changes to vigil
- Good: realism is cheap, because the app chart supplies telemetry and load generation
- Bad: no leaderboard comparability — a narrower claim than the Exposé promised

## More Information

- Measured-ref and series selection: ADR-0001
- Scenario contract: ADR-0003
- Application and licences: ADR-0004
- SREGym's docs: `SREGym/docs/incident-arena.md`, `SREGym/Problem List.md`
- vigil's repair surface: `agents/remediation/src/remediation/agent.py` (toolset is git, flux,
  nixos only), `infra/policy/remediation.rego`
