---
status: Accepted
date: 2026-11-07
decision-makers: [Adrian Rossa]
consulted: []
informed: []
---

# ADR-0001: Measurement base and measured-ref selection

## Context and Problem Statement

This work compares two series, baseline and a modified run. The difference between them must be attributable to this works contribution alone.
Both series therefore have to start from one commit. There is a problem blocking that.

First, the campaign cannot measure anything other than `main`. Every cell branch is created from and reseted to `main` (`.github/workflows/eval-campaign.yml`).
This work plans to keep `main` as an upstream mirror and instead run the campaign using a series branch comparing it to a tagged commit.

## Decision Drivers

- Both series must start from the identical commit, a difference in results has to be attributed to the contribution
- a measured state must be reconstructible after the fact by a third party
- Upstream commits must not be rewritten, so the fork stays diffable against upstream

## Considered Options

- #1 upstream `v1.1.0` plus cherry picked commits
- #2 upstream `main` pinned at a commit SHA
- #3 `main` pinned at a release tag, with one change: the measured ref as workflow input

## Decision Outcome

Chosen option is #3. Because it is the only option that both contains the architecture under study and yields a citable, immutable reference.

The base is upstream release tag `v2.0.0`, chosen once it is published.
A fork-only workflow input `series_ref` selectes the commit a compaign measures.
It defaults to `main`, so upstream behavior is unchanged.
The change is confined to `eval/`. The campaign derives cell branches from `commits/<series_ref>`, and the reset script resolves `<series_ref>` to a remote branch, tag or SHA.

A measured state is recorded as an annotaged tag, never as a branch. The tag message names the upstream pin and the fork commits above it because branch name cannot identify a past state once the branch moves.

### Consequences

- Good: both series can be selected by name, so the difference between them is the
  contribution and nothing else
- Good: `git checkout <tag>` reproduces a measured state; a tag cannot drift
- Good: the `series_ref` change is behaviour-neutral, so it sits in both series and does
  not itself become a measured difference
- Bad: results are not comparable with the published `v1.0.0` numbers
- Neutral: `series_ref` is a fork-only fix for an upstream gap
- Neutral: the base waits on an upstream release date

**Validation Status:** Partially confirmed. The ref-selection mechanism was verified on
2026-11-06 by a campaign run with `series_ref` pointing at a throwaway branch.

### Confirmation

The decision holds as long as:

- Every measurement series is selected with `series_ref` and recorded as an annotated tag
- The selected tag resolves to a commit
- No campaign is reported as a series result while `series_ref` is left at its default (`main`)
- The measured commit recorded in each run record equals `git rev-parse <series tag>`
- `git log --oneline upstream/main..main` shows only fork documentation commits

### Pros and Cons of the Options

#### `v1.1.0` plus forwarded infrastructure commits

- Bad: `v1.1.0` predates the retry loop, the orchestrator-owned gate, and the rollback
  finaliser
- Bad: forwarded commits have to be ordered by hand and maintained. A partial forward
  produces an internally inconsistent tree that fails at the first infrastructure step
- Good: published results exist for the `v1.0.0` lineage, so a comparison is possible,
  though ADR-0017 records those numbers as superseded

#### `main` pinned at a commit SHA

- Bad: a SHA is neither memorable nor citable, and `main` is a moving target, so
  "the SHA in the thesis" invites transcription errors
- Bad: a SHA carries no indication of whether the tree is a released, tested state
- Good: no dependency on a release date

#### `main` pinned at a release tag, with the measured ref as a workflow input

- Good: a tag is immutable, citable, and reproduces the exact measured tree
- Good: the tag is upstream's own published and tested reference, so the base is not an
  arbitrary commit chosen mid-development
- Bad: depends on an upstream release date the author does not control, which must be
  bounded by a fallback cutoff to protect the schedule

## More Information

- Upstream release tagging and the frozen evaluated version: `docs/adr/0016-release-tagging-and-changelog-automation.md`
- Superseded evaluation numbers from the leak correction: `docs/adr/0017-evaluation-oracle-leak-remediation.md`
- Campaign runner that consumes the selected ref: `docs/adr/0010-github-actions-eval-runner.md`
- Fork documentation is kept in `docs/thesis/`, `docs/adr/` holds upstream decisions only