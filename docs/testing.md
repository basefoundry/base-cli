# Validation commands

The Base manifest declares `./tests/full_validate.sh` as the authoritative
local aggregate. It composes the same named gates used by CI: repository
baseline, Python tests with coverage, strict typing, formatting and lint,
documentation/schema/contract validation, benchmark budgets, and security.
Bandit and pip-audit are required; a missing tool is an error rather than a
skipped check.

Run it from a clean checkout after installing the development, quality, and
benchmark extras (the latter installs every declared framework comparator):

```bash
python -m pip install '.[dev,typer,quality,benchmark]'
./tests/full_validate.sh
```

`./tests/validate.sh` remains the fast repository-baseline check used when
dependencies are not yet installed. It is not a substitute for the full
validation gate. Individual gates can be selected for focused local work:

```bash
./tests/full_validate.sh --gate runtime
./tests/full_validate.sh --gate coverage
./tests/full_validate.sh --gate typing
./tests/full_validate.sh --gate style
./tests/full_validate.sh --gate contracts
./tests/full_validate.sh --gate benchmark
./tests/full_validate.sh --gate security
```

The Tests workflow runs the runtime suite across the OS/Python matrix and on
the supported Linux distributions/WSL. Its quality job runs platform-
independent coverage, typing, style, contract, and security gates once, with
each group visible as a named Actions step. A separate comparative benchmark
matrix measures Click, Typer, Cyclopts, and base-cli on Unix, macOS, Windows,
and WSL; each job publishes an Actions summary and retains its versioned JSON
report as a dated artifact. The workflow validates feature branches through
pull requests rather than launching a second full run on every feature-branch
push; direct pushes to `main` and version tags remain validated. The Package
workflow focuses on release-boundary checks: building and validating the
wheel/sdist, checksums/SBOM, and clean installed-wheel smoke tests. It does
not repeat the source test, typing, lint, documentation, benchmark, or
security suites. `./tests/full_validate.sh` remains the one-command local
aggregate of all source gates.

The full gate writes a machine-readable result to
`$BASE_CLI_VALIDATION_RESULT` (or `/tmp/base-cli-validation-result.json`). If
Node.js is unavailable, the result is marked `partial`, the gate exits with
status `2`, and it cannot be reported as an authoritative pass.
