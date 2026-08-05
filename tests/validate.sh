#!/usr/bin/env bash

required_files=(
  README.md
  VERSION
  CHANGELOG.md
  CONTRIBUTING.md
  .github/pull_request_template.md
  .github/base-project.yml
  LICENSE
  base_manifest.yaml
  .github/workflows/issue-branch-policy.yml
  .github/workflows/project-intake.yml
  .github/workflows/tests.yml
  .github/workflows/package.yml
  docs/releasing.md
  docs/api-stability.md
  docs/migrations.md
  MANIFEST.in
  scripts/validate_package_artifact.py
  scripts/validate_installed_package.py
  scripts/validate_docs.py
  scripts/benchmark_runtime.py
  tests/conftest.py
)

for file in "${required_files[@]}"; do
  [[ -f "$file" ]] || {
    printf 'Missing required file: %s\n' "$file" >&2
    exit 1
  }
done

printf 'Repository baseline is present.\n'
