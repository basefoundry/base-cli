# Releasing `base-cli`

The `base-cli` distribution is built and published from the standalone
`basefoundry/base-cli` repository. The package name on PyPI is `base-cli`; the
Python import name is `base_cli`.

## Version and tag contract

`VERSION` is the release version source of truth. The build backend reads it for
the wheel and sdist metadata, and `base_cli.__version__` reports the same value
from a source checkout or from installed distribution metadata.

Production releases use a matching annotated-style tag such as `v0.1.0`.
The Package workflow rejects a tag that does not exactly match `v${VERSION}`.

## Validation workflow

Pull requests and pushes to `main` start from a clean artifact destination,
build one sdist and one wheel, enforce the source allowlist, run `twine check`,
and install the reviewed wheel in an isolated environment. The installed-wheel
smoke test exercises public API, lifecycle, and output behavior without the
source tree on `sys.path`. Tests run across Python 3.10 through 3.14 on Linux,
macOS, and Windows, with Debian, Fedora, and WSL validation retained. Blocking
quality gates cover Ruff formatting/lint, strict public-sample typing, an 80%
branch-coverage threshold, documentation/example checks, and dependency/static
security scans. The [performance contract](performance.md) also checks fresh
import and isolated invocation budgets, while the adversarial suite exercises
redaction, protocol framing, persistence, concurrency, retention, and signal
cleanup.

The publish job downloads that same reviewed artifact; it does not rebuild
during publication. The build also emits a deterministic `SHA256SUMS` file and
an SPDX 2.3 `SBOM.spdx.json` release artifact. On tag and protected dispatch
runs, GitHub's OIDC-backed `actions/attest` job records both build provenance
and an SBOM attestation for the exact artifact digests; no PyPI token or other
long-lived publish secret is used.

For a version tag, the same Package workflow creates a GitHub Release after
the protected PyPI publication and attestations succeed. The release attaches
the exact reviewed wheel, sdist, `SHA256SUMS`, and `SBOM.spdx.json` downloaded
from the build job. GitHub-generated comparison notes are supplemented by the
dated section in `CHANGELOG.md`; the tagged release is rejected when `VERSION`
or that section does not match the tag. Rerunning a tag updates an existing
release's assets with `--clobber` instead of creating a second release.

## Independent verification

The release job uses the reviewed, hash-locked toolchain in
`requirements/release.txt`; it does not install mutable latest build or
publishing packages. The lock includes transitive release-path dependencies,
and the PEP 517 backend is pinned to the same setuptools and wheel versions in
`pyproject.toml`. The job builds two clean source archives with the same
`SOURCE_DATE_EPOCH` and rejects digest drift before publishing the reviewed
artifacts.

To intentionally refresh the toolchain, edit the four direct requirements in
`requirements/release.in` and regenerate the lock with:

```bash
uv pip compile requirements/release.in --python-version 3.13 \
  --generate-hashes --output-file requirements/release.txt
```

Review the complete diff, run the package workflow on a pull request, and only
then merge the update. Runtime dependency windows are deliberately not tied to
this release-only toolchain.

Download the release metadata artifact from the successful Package workflow
run (the artifact is named `base-cli-release-metadata-<run-id>`), alongside
the wheel or sdist you downloaded from PyPI:

```bash
gh run download <run-id> \
  --repo basefoundry/base-cli \
  --name base-cli-release-metadata-<run-id> \
  --dir release-metadata
sha256sum -c release-metadata/SHA256SUMS
```

The SPDX document's namespace and comment include the source revision used by
the workflow. For a tagged release, verify the matching GitHub attestations
with the GitHub CLI:

```bash
gh attestation verify base_cli-<version>-py3-none-any.whl \
  --repo basefoundry/base-cli
```

The same command can verify the sdist. A clean-room verifier should compare
the downloaded artifact's digest with `SHA256SUMS`, confirm the SBOM namespace
contains the expected tag commit, and inspect the attestation's workflow and
repository identity before installation.

## Changelog and release notes

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Keep `[Unreleased]` first, use one section per change category, and write
user-facing bullets rather than internal implementation notes. Every released
version must include its release date and a reference link at the bottom of the
file. Move entries from `[Unreleased]` into the dated section when the release
PR is prepared; do not rewrite an already published section.

The repository validates these rules in CI with
`python scripts/validate_changelog.py`, including duplicate bullets, duplicate
categories, missing release links, malformed dates, and accidental internal
planning text.

## Documentation site

The Documentation workflow builds this site with `mkdocs build --strict` and
publishes the reviewed site to GitHub Pages after changes land on `main`. The
canonical URL is <https://basefoundry.github.io/base-cli/> and is exposed in
the PyPI project metadata as the `Documentation` link.

Repository administrators should enable GitHub Pages for the repository using
the GitHub Actions source and approve the `github-pages` environment the first
time the workflow deploys. Pull requests run the strict build and repository
link checks without publishing.

## TestPyPI rehearsal

1. Dispatch **Package** from the branch or tag to be rehearsed and choose
   `testpypi`.
2. Approve the protected `testpypi` environment when prompted.
3. Verify the published artifact from a clean environment:

   ```bash
   python -m venv /tmp/base-cli-smoke
   /tmp/base-cli-smoke/bin/python -m pip install \
     --index-url https://test.pypi.org/simple/ \
     --extra-index-url https://pypi.org/simple/ \
     base-cli
   /tmp/base-cli-smoke/bin/python -c \
     'import base_cli; print(base_cli.__version__)'
   ```

The `testpypi` GitHub environment must be configured with PyPI trusted
publishing for this repository and workflow before the dispatch can upload.

## Production release

1. Update `VERSION` and the changelog in a reviewed pull request.
2. Merge to `main` and create the matching `v${VERSION}` tag.
3. Approve the protected `pypi` environment. The workflow verifies the tag,
   dated changelog section, builds and tests the artifact, then publishes the
   exact artifact to PyPI via trusted publishing and creates the matching
   GitHub Release.
4. Verify installation from PyPI and download the matching GitHub Release
   assets:

   ```bash
   python -m venv /tmp/base-cli-smoke
   /tmp/base-cli-smoke/bin/python -m pip install --upgrade base-cli
   /tmp/base-cli-smoke/bin/python -c \
     'import base_cli; import importlib.metadata as m; assert base_cli.__version__ == m.version("base-cli"); print(base_cli.__version__)'
   ```

The `pypi` GitHub environment must require approval and be configured with the
PyPI trusted publisher for `.github/workflows/package.yml`. No long-lived PyPI
token is stored in the repository.

## Recovery

PyPI versions cannot be overwritten. If validation fails, fix the branch and
rerun the workflow before creating a tag. If TestPyPI succeeds but a production
publish fails, inspect the workflow logs and rerun the same approved tag only
after confirming that neither artifact nor metadata needs correction. A version
that was published successfully must be incremented for the next release.
