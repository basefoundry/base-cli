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

Pull requests and pushes to `main` build one sdist and one wheel, run `twine
check`, inspect metadata and package data, and install the reviewed wheel across
Python 3.10 through 3.14. The publish job downloads that same artifact; it does
not rebuild during publication.

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
