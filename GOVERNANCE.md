# Project governance

This document describes how `base-cli` makes API, compatibility, security, and
release decisions. It is deliberately lightweight: the project is maintained
in the open through issues, pull requests, required checks, and dated release
artifacts.

## Current responsibilities

| Role | GitHub identity | Responsibility |
| --- | --- | --- |
| Project maintainer | [`codeforester`](https://github.com/codeforester) | Roadmap, issue triage, merge decisions, release preparation, and continuity planning |
| Independent release/security reviewer | [`rameshulugar`](https://github.com/rameshulugar) | Review protected release and security-source changes independently of the last pusher |

The maintainer is accountable for keeping this table current. A role is not a
claim that a person has reviewed a particular change; the pull request and
release records remain authoritative.

## Review and decision rules

- Every implementation starts from a GitHub issue with a stated scope and
  acceptance criteria.
- A pull request must identify the issue, describe compatibility impact, and
  report the authoritative validation gates.
- Public API, JSON schema, command-protocol, dependency-window, security, and
  release-workflow changes require explicit documentation and regression
  evidence in the same change train.
- The last pusher must not be the sole reviewer for a protected release.
- A disagreement is recorded in the issue or pull-request thread; the
  maintainer records the final decision and its rationale in the changelog or
  relevant contract document.
- Security reports follow `SECURITY.md` and are never disclosed through a
  public implementation issue before coordinated remediation.

## Release ownership and continuity

The release owner prepares the version/changelog change, candidate artifact,
TestPyPI rehearsal, downstream wheel validation, and release evidence. The
independent reviewer checks the exact commit, required gates, artifact
checksums, SBOM/provenance attestations, and PyPI/GitHub metadata before the
protected environment is approved.

If the maintainer is unavailable, the independent reviewer may coordinate an
already-approved release or security response, but may not silently change the
public API or bypass required checks. A temporary exception must be time-bound,
name an owner and expiry, and be recorded in the relevant issue.

## Reviewer onboarding checklist

An additional reviewer is considered trained after completing and recording:

1. a clean checkout and `./tests/full_validate.sh` run;
2. the public API, JSON-contract, compatibility, and security review guides;
3. a TestPyPI candidate install and downstream consumer smoke test;
4. checksum, SBOM, provenance, and attestation verification for a release
   artifact; and
5. a supervised review of one release-source or security pull request.

The training record belongs in the linked governance issue or release checklist
and must not be inferred solely from repository write access.

## Project health

Maintainers review these indicators at least once per release cycle:

- open P1 issues and their dependency graph;
- time to first response and time to review for pull requests;
- green required checks on the default branch;
- release-candidate and downstream-consumer evidence;
- security-alert age and remediation status; and
- independent adopter pilots and permissioned outcomes.

The current `v1.0.0` plan targets a stable core release after the documented
release-candidate, downstream, and independent-review evidence is complete;
the milestone and linked issues are the source of truth for scheduling. The
broader language-neutral platform and catalog work remains explicitly deferred
unless a separate roadmap decision brings it into scope.

## Becoming a maintainer

Regular contributors can be nominated through a public issue after they have
demonstrated sustained, high-quality reviews and familiarity with the full
validation and release gates. The maintainer records the nomination, conflict
disclosures, granted repository permissions, and a succession handoff plan.

See [`CONTRIBUTING.md`](CONTRIBUTING.md), [`SECURITY.md`](SECURITY.md), and
[`docs/releasing.md`](docs/releasing.md) for the operational procedures.
