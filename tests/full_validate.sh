#!/usr/bin/env bash

# Authoritative local validation entry point for the Base manifest.
set -euo pipefail

required_commands=(python ruff mypy)
for command in "${required_commands[@]}"; do
  command -v "$command" >/dev/null 2>&1 || {
    printf 'Missing validation tool: %s. Install the dev and quality extras first.\n' "$command" >&2
    exit 1
  }
done

./tests/validate.sh
python -m pytest --cov=base_cli --cov-report=term-missing --cov-report=json:coverage.json --cov-fail-under=80
python -m mypy --strict examples/typed_consumer.py
python -m mypy --strict lib/python/base_cli
ruff format --check lib/python/base_cli scripts examples tests
ruff check lib/python/base_cli scripts examples tests
python scripts/validate_docs.py
python scripts/validate_changelog.py
python scripts/validate_schemas.py
python scripts/validate_contract_fixtures.py
if command -v node >/dev/null 2>&1; then
  node scripts/validate_contract_fixtures.mjs
fi
python scripts/generate_compatibility_dashboard.py --check
python scripts/benchmark_runtime.py --check
python -m compileall -q examples
python scripts/validate_coverage.py coverage.json

if command -v bandit >/dev/null 2>&1; then
  bandit -q -r lib/python/base_cli scripts -lll -iii
fi

printf 'Full base-cli validation passed.\n'
