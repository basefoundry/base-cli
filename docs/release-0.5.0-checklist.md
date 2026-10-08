# 0.5.0 release candidate checklist

This is release preparation. A dated changelog section does not mean the version
has been tagged or published. The proposed date must be refreshed if publication
happens on another day. Keep the active 0.5.0 release-tracking issue open until
publication evidence exists; this checklist intentionally does not hard-code a
historical issue number.

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
   smoke checks; retain the artifact digest and workflow URL. Using that exact
   candidate wheel, check out the pinned `base-cli-demo` revision and install
   it with `python -m pip install --no-deps .` so dependency resolution cannot
   replace the candidate with an older published framework. Verify that
   `importlib.metadata.version("base-cli")` is exactly `0.5.0`, run
   `python -m pip check`, and then run the demo's documented validation suite,
   including optional integration scenarios when their declared extras are
   installed. If the pinned demo still declares a pre-0.5 dependency window,
   record that compatibility mismatch and the required #47 update as an
   explicit deferral; do not accept a run against 0.4.x. Record the demo
   commit, candidate digest, and result on the active release-tracking issue.
   A demo failure must block completion or be explicitly deferred there.
5. After the release PR is merged, create annotated `v0.5.0` on the independently
   approved protected-main commit. Never alter `v0.4.3` or its distributions.
6. Approve the protected production environment. Publish the exact reviewed
   wheel/sdist, then verify matching SHA256SUMS, SPDX SBOM, provenance and SBOM
   attestations, RELEASE-BOM-ROW, tag target, and GitHub release assets.
7. Verify a clean installation of exactly `base-cli==0.5.0` from PyPI. Record the
   immutable tag and GitHub release URLs, PyPI result, checksums, SPDX SBOM,
   provenance and SBOM attestations, RELEASE-BOM-ROW, and downstream evidence
   on the active release-tracking issue. Keep that issue open until the
   post-publication consumer step below and every required artifact and
   compatibility result have been independently verified.

8. Complete the post-publication consumer step tracked by
   [`base-cli-demo#47`](https://github.com/basefoundry/base-cli-demo/issues/47):
   update the demo dependency window, lockfile, compatibility/release
   workflows, README, and docs for `base-cli>=0.5.0,<0.6`, then run the demo
   suite against the published PyPI package. Do not call the 0.5.0 release
   train complete until that consumer validation is green or the active
   release-tracking issue records an explicit deferral and owner.

Merged release preparation is not publication completion: the release is not
complete while the tag, GitHub release, PyPI package, or required evidence is
missing, even when the preparation pull request and local gates are green.

The operational commands and recovery rules remain in [Releasing](releasing.md).
