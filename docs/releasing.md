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
during publication.

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
   builds and tests the artifact, then publishes the exact artifact to PyPI via
   trusted publishing.
4. Verify installation from PyPI:

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
