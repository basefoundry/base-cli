# 0.5.0 release candidate checklist

This is release preparation. A dated changelog section does not mean the version
has been tagged or published. The proposed date must be refreshed if publication
happens on another day. Issue #307 remains open until publication evidence exists.

1. Merge the reviewed 0.5.0 issue train and all remaining 0.5.0 PRs. Refresh the
   release PR against their final integrated commit and include their changelog
   entries in 0.5.0 before approval.
2. Obtain independent approval and passing required checks on the exact release
   head. Keep the repository and environment approval rules in place.
3. Validate the 0.4.x migration boundary in [migration guidance](migration-0.5.md),
   then run the full local gate and hosted platform, dependency, consumer,
   benchmark, package, and provenance checks.
4. Rehearse the reviewed artifact through the protected TestPyPI environment.
   Install exactly `base-cli==0.5.0` from a new environment and run lifecycle/JSON
   smoke checks; retain the artifact digest and workflow URL.
5. After the release PR is merged, create annotated `v0.5.0` on the independently
   approved protected-main commit. Never alter `v0.4.3` or its distributions.
6. Approve the protected production environment. Publish the exact reviewed
   wheel/sdist, then verify matching SHA256SUMS, SPDX SBOM, provenance and SBOM
   attestations, RELEASE-BOM-ROW, tag target, and GitHub release assets.
7. Verify a clean installation of exactly `base-cli==0.5.0` from PyPI. Record the
   immutable release URL and evidence on #307, then close it.

The operational commands and recovery rules remain in [Releasing](releasing.md).
