# Adoption and compatibility evidence

This page separates verifiable framework evidence from adoption claims. It is
part of the v1.0 release gate: every public statement must be traceable to a
repository file, a dated CI run, or a permissioned adopter record.

## Current status

As of the latest review, **zero independent external adopters are publicly
confirmed**. The repository maintains three reference consumer fixtures—Atlas,
Beacon, and Cinder—to exercise the compatibility boundary. They are useful
engineering evidence, but they are not customer outcomes and are not counted
as adoption.

The independent-adopter target is three permissioned outcomes. Recruitment is
**not started** until a team opts in through the support channel. No name,
metric, logo, download count, or case-study language is published without
written permission from that team.

## Evidence categories

| Category | What it proves | Source | Public claim allowed |
| --- | --- | --- | --- |
| Reference fixture | A maintained consumer shape remains compatible | [`compatibility/README.md`](https://github.com/basefoundry/base-cli/blob/main/compatibility/README.md) and CI | “The fixture passed.” |
| Compatibility run | A specific revision/version passed a dated matrix | `base-cli-compatibility-evidence-<run-id>` artifact | “Version X passed run Y.” |
| External adopter | A real independent team completed an agreed outcome | Permissioned adopter record | Only the approved case-study facts |

Reference fixtures must remain separate packages with their own metadata and
tests. Base, base-demo, and other adjacent repositories are explicitly
excluded from the independent-adopter count.

## Adopter program

1. **Recruit:** offer a short evaluation against the [adopter readiness
   checklist](adopter-readiness.md); record only a consented contact and the
   intended CLI shape.
2. **Onboard:** pin a supported minor release, run the installed-wheel smoke
   test, and capture migration friction without receiving private credentials
   or production data.
3. **Verify:** run the adopter's agreed compatibility and contract tests; keep
   the framework revision, package versions, platform, and dated result.
4. **Publish (optional):** request written approval for an anonymized metric or
   named case study. A refusal or incomplete pilot remains a valid private
   outcome and is never presented as adoption.
5. **Review quarterly:** re-confirm consent, compatibility status, and the
   exact wording of every public claim.

The support issue template is the intake path. Security reports and private
reproductions must follow [`SECURITY.md`](https://github.com/basefoundry/base-cli/blob/main/SECURITY.md).

## Reproducible compatibility records

Every successful `Reference consumers` workflow can emit a JSON record named
`base-cli-compatibility-evidence-<run-id>`. It contains the source revision,
framework version, fixture names, Python/Typer matrix, timestamp, and the
commands needed to repeat the check. Download a record from a run with:

```bash
gh run download <run-id> \
  --repo basefoundry/base-cli \
  --name base-cli-compatibility-evidence-<run-id> \
  --dir compatibility-evidence
```

The record is an engineering result, not a customer claim. Before publishing
an external outcome, maintainers must link its permissioned source record and
make the approved scope clear.
