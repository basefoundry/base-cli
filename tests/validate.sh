#!/usr/bin/env bash

required_files=(
  README.md
  VERSION
  CHANGELOG.md
  SECURITY.md
  CONTRIBUTING.md
  .github/pull_request_template.md
  .github/base-project.yml
  LICENSE
  base_manifest.yaml
  .github/workflows/issue-branch-policy.yml
  .github/workflows/project-intake.yml
  .github/workflows/tests.yml
  .github/workflows/package.yml
  .github/workflows/examples.yml
  .github/workflows/compatibility.yml
  .github/ISSUE_TEMPLATE/support.md
  docs/releasing.md
  docs/api-stability.md
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

printf 'Repository baseline is present.\n'
