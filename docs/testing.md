# Validation commands

The Base manifest declares `./tests/full_validate.sh` as the authoritative
test command. It runs the repository baseline checks, Python tests with the
coverage policy, strict typing, formatting and lint checks, schema and
contract validation, documentation checks, compatibility-dashboard and
performance checks, Bandit, and a strict `pip-audit` of the resolved
third-party environment. Bandit and pip-audit are required; a missing tool is
an error rather than a skipped check.

Run it from a clean checkout after installing the development and quality
extras:

```bash
python -m pip install '.[dev,typer,quality]'
./tests/full_validate.sh
```

`./tests/validate.sh` remains the fast repository-baseline check used when
dependencies are not yet installed. It is not a substitute for the full
validation gate. The full gate writes a machine-readable result to
`$BASE_CLI_VALIDATION_RESULT` (or `/tmp/base-cli-validation-result.json`). If
Node.js is unavailable, the result is marked `partial`, the gate exits with
status `2`, and it cannot be reported as an authoritative pass.
