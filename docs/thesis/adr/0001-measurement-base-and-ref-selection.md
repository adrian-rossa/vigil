---
status: Accepted
date: 2026-11-07
decision-makers: [Adrian Rossa]
consulted: []
informed: []
---

# ADR-0001: Measurement base and measured-ref selection

## Context and Problem Statement

This work compares two series: a baseline and a modified run. Every difference between them has
to come from this work's contribution and nothing else, so both series must start from the same
commit.

There is a problem. The campaign cannot measure anything except `main`, because every cell branch
is created from and reset to `main` (`.github/workflows/eval-campaign.yml`). This work wants to
keep `main` as a clean mirror of upstream and run campaigns from a separate series branch instead.

## Decision Drivers

- Both series start from the identical commit, so a difference is attributable
- A measured state can be reconstructed later by a third party
- Upstream commits are never rewritten, so the fork stays diffable against upstream

## Considered Options

- #1 Upstream `v1.1.0` plus cherry-picked commits
- #2 Upstream `main` pinned at a commit SHA
- #3 `main` pinned at a release tag, plus one change: the measured ref as a workflow input

## Decision Outcome

**#3**, because it is the only option that both contains the architecture under study and yields a
citable, immutable reference.

The base is upstream release tag `v2.0.0`, used once it is published. A fork-only workflow input
`series_ref` selects which commit a campaign measures. It defaults to `main`, so upstream
behaviour is unchanged. The change stays inside `eval/`: campaign cells branch from
`commits/<series_ref>`, and the reset script resolves `<series_ref>` to a remote branch, tag or
SHA.

A measured state is recorded as an **annotated tag**, never a branch. The tag message names the
upstream pin and the fork commits on top, because a branch name cannot identify a past state once
the branch moves.

### Consequences

- Good: both series are selected by name, so the difference is the contribution alone
- Good: `git checkout <tag>` reproduces a measured state; a tag cannot drift
- Good: `series_ref` is behaviour-neutral, so it sits in both series and is not itself a difference
- Bad: results are not comparable with the published `v1.0.0` numbers
- Neutral: `series_ref` is a fork-only fix for an upstream gap
- Neutral: the base waits on an upstream release date

**Validation Status:** Partially confirmed. Verified on 2026-11-06 with a campaign run whose
`series_ref` pointed at a throwaway branch.

### Confirmation

The decision holds as long as:

- Every series is selected with `series_ref` and recorded as an annotated tag
- The selected tag resolves to a commit
- No campaign is reported as a series result while `series_ref` is left at its default
- The commit in each run record equals `git rev-parse <series tag>`
- `git log --oneline upstream/main..main` shows only fork documentation commits

### Pros and Cons of the Options

#### `v1.1.0` plus forwarded infrastructure commits

- Bad: `v1.1.0` predates the retry loop, the orchestrator-owned gate and the rollback finaliser
- Bad: forwarded commits must be ordered by hand; a partial forward yields a tree that fails at
  the first infrastructure step
- Good: published numbers exist for the `v1.0.0` lineage, though upstream ADR-0017 records them
  as superseded

#### `main` pinned at a commit SHA

- Bad: a SHA is neither memorable nor citable, and `main` moves, so it invites transcription errors
- Bad: a SHA says nothing about whether the tree is a released, tested state
- Good: no dependency on a release date

#### `main` pinned at a release tag, measured ref as input

- Good: a tag is immutable, citable and reproduces the exact tree
- Good: the base is upstream's own published and tested reference, not an arbitrary commit
- Bad: depends on an upstream release date, so it needs a fallback cutoff to protect the schedule

## More Information

- Upstream release tagging: `docs/adr/0016-release-tagging-and-changelog-automation.md`
- Superseded evaluation numbers: `docs/adr/0017-evaluation-oracle-leak-remediation.md`
- Campaign runner that consumes the ref: `docs/adr/0010-github-actions-eval-runner.md`
- Fork documentation lives in `docs/thesis/`; `docs/adr/` holds upstream decisions only
