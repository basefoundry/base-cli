#!/usr/bin/env bash

# Composable validation gates. The default command remains the authoritative
# local aggregate; CI selects only the gate groups appropriate to each job.
set -euo pipefail

gate="all"
node_contracts_missing=0
if [[ $# -gt 0 ]]; then
  if [[ $# -ne 2 || "$1" != "--gate" ]]; then
    printf 'Usage: %s [--gate baseline|runtime|coverage|typing|style|contracts|benchmark|security]\n' "$0" >&2
    exit 2
  fi
  gate="$2"
fi

require_commands() {
  local command
  for command in "$@"; do
    command -v "$command" >/dev/null 2>&1 || {
      printf 'Missing validation tool: %s. Install the required development extras first.\n' "$command" >&2
      exit 1
    }
  done
}

run_baseline() {
  bash ./tests/validate.sh
}

run_runtime() {
  require_commands python
  python -m pytest
}

run_coverage() {
  require_commands python
  python -m pytest --cov=base_cli --cov-report=term-missing --cov-report=json:coverage.json --cov-fail-under=80
  python scripts/validate_coverage.py coverage.json
}

run_typing() {
  require_commands python mypy
  python -m mypy --strict examples/typed_consumer.py
  python -m mypy --strict lib/python/base_cli
}

run_style() {
  require_commands ruff
  ruff format --check lib/python/base_cli scripts examples tests
  ruff check lib/python/base_cli scripts examples tests
}

run_contracts() {
  require_commands python
  python scripts/validate_docs.py
  python scripts/validate_changelog.py
  python scripts/validate_schemas.py
  python scripts/validate_contract_fixtures.py
  if command -v node >/dev/null 2>&1; then
    node scripts/validate_contract_fixtures.mjs
  else
    node_contracts_missing=1
    printf 'Node.js is unavailable; the cross-language contract gate is incomplete.\n' >&2
  fi
  python scripts/generate_compatibility_dashboard.py --check
  python -m compileall -q examples
  if ((node_contracts_missing)) && [[ "$gate" != "all" ]]; then
    printf 'Install Node.js to complete the cross-language contract gate.\n' >&2
    return 2
  fi
}

run_benchmark() {
  require_commands python
  python scripts/benchmark_runtime.py --check
}

run_security() {
  require_commands bandit pip-audit python
  bandit -q -r lib/python/base_cli scripts -lll -iii

  # Audit third-party packages without asking pip-audit to resolve the
  # unpublished editable checkout itself.
  local audit_requirements
  audit_requirements="$(mktemp)"
  # Keep the path in a global shell variable so the EXIT trap still sees it
  # when errexit terminates the script from inside this function.
  SECURITY_AUDIT_REQUIREMENTS="$audit_requirements"
  cleanup_security_audit() { rm -f -- "$SECURITY_AUDIT_REQUIREMENTS"; }
  trap cleanup_security_audit EXIT
  python -m pip freeze \
    | sed -E '/(^-e .*#egg=base[_-]cli|^base[_-]cli([[:space:]=@]|$))/Id' \
    > "$audit_requirements"
  pip-audit --strict -r "$audit_requirements"
}

write_validation_result() {
  local validation_result
  validation_result="${BASE_CLI_VALIDATION_RESULT:-${TMPDIR:-/tmp}/base-cli-validation-result.json}"
  if ((node_contracts_missing)); then
    printf '%s\n' '{"status":"partial","skipped":["node contract validator"]}' > "$validation_result"
    printf 'Validation result: partial; Node.js contract validation was skipped (%s).\n' "$validation_result"
    return 2
  fi
  printf '%s\n' '{"status":"full","skipped":[]}' > "$validation_result"
  printf 'Validation result: full (%s)\n' "$validation_result"
}

case "$gate" in
  baseline) run_baseline ;;
  runtime) run_runtime ;;
  coverage) run_coverage ;;
  typing) run_typing ;;
  style) run_style ;;
  contracts) run_contracts ;;
  benchmark) run_benchmark ;;
  security) run_security ;;
  all)
    run_baseline
    run_coverage
    run_typing
    run_style
    run_contracts
    run_benchmark
    run_security
    write_validation_result
    printf 'Full base-cli validation passed.\n'
    ;;
  *)
    printf 'Unknown validation gate: %s\n' "$gate" >&2
    exit 2
    ;;
esac
