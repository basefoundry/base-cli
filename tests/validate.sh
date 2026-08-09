#!/usr/bin/env bash

required_files=(
  README.md
  VERSION
  CHANGELOG.md
  SECURITY.md
  CONTRIBUTING.md
  .github/pull_request_template.md
  .github/base-project.yml
  .github/workflows/docs.yml
  LICENSE
  mkdocs.yml
  base_manifest.yaml
  .github/workflows/issue-branch-policy.yml
  .github/workflows/project-intake.yml
  .github/workflows/tests.yml
  .github/workflows/package.yml
  .github/workflows/examples.yml
  .github/workflows/compatibility.yml
  .github/workflows/dependency-matrix.yml
  .github/ISSUE_TEMPLATE/support.md
  docs/releasing.md
  docs/index.md
  docs/api-stability.md
  docs/dependency-support.md
  docs/user-config-typing.md
  docs/migrations.md
  docs/security-threat-model.md
  docs/security-review.md
  docs/adopter-readiness.md
  MANIFEST.in
  scripts/validate_package_artifact.py
  scripts/validate_installed_package.py
  scripts/validate_docs.py
  scripts/validate_examples.py
  scripts/validate_consumers.py
  scripts/generate_release_metadata.py
  scripts/validate_release_metadata.py
  scripts/benchmark_runtime.py
  tests/conftest.py
  compatibility/README.md
  compatibility/consumers/manifest.json
)

for file in "${required_files[@]}"; do
  [[ -f "$file" ]] || {
    printf 'Missing required file: %s\n' "$file" >&2
    exit 1
  }
done

version="$(head -n 1 VERSION | tr -d '\r')"
readme_head="$(sed -n '1,18p' README.md | tr -d '\r')"
if ! printf '%s\n' "$readme_head" | grep -F "[![Tests](https://img.shields.io/github/actions/workflow/status/basefoundry/base-cli/tests.yml?branch=main&label=tests)](https://github.com/basefoundry/base-cli/actions/workflows/tests.yml)" >/dev/null; then
  printf 'README.md is missing the main-branch tests health badge.\n' >&2
  exit 1
fi
if ! printf '%s\n' "$readme_head" | grep -F "[![Reference consumers](https://img.shields.io/github/actions/workflow/status/basefoundry/base-cli/compatibility.yml?branch=main&label=consumers)](https://github.com/basefoundry/base-cli/actions/workflows/compatibility.yml)" >/dev/null; then
  printf 'README.md is missing the reference-consumer health badge.\n' >&2
  exit 1
fi
if ! printf '%s\n' "$readme_head" | grep -F "[![PyPI](https://img.shields.io/pypi/v/base-cli.svg)](https://pypi.org/project/base-cli/)" >/dev/null; then
  printf 'README.md is missing the PyPI release badge.\n' >&2
  exit 1
fi
if ! printf '%s\n' "$readme_head" | grep -F "[![Python](https://img.shields.io/pypi/pyversions/base-cli.svg)](https://pypi.org/project/base-cli/)" >/dev/null; then
  printf 'README.md is missing the supported Python versions badge.\n' >&2
  exit 1
fi
if ! printf '%s\n' "$readme_head" | grep -F "| \`$version\` | [Apache-2.0](LICENSE) | \`python -m pip install base-cli\` | [v$version](https://github.com/basefoundry/base-cli/releases/tag/v$version) |" >/dev/null; then
  printf 'README.md release strip does not match VERSION (%s), license, install command, and release link.\n' "$version" >&2
  exit 1
fi

printf 'Repository baseline is present.\n'
