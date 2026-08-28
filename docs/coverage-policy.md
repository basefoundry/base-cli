# Coverage policy

The aggregate test gate requires at least 80% branch-aware coverage. The
following high-risk modules also have explicit floors because regressions in
these paths affect filesystem safety, lifecycle attachment, or machine-facing
contracts:

| Module | Floor |
| --- | ---: |
| `base_cli._attach` | 75% |
| `base_cli._click_compat` | 80% |
| `base_cli._private_files` | 75% |
| `base_cli._runtime` | 80% |
| `base_cli.command_protocol` | 85% |
| `base_cli.history` | 75% |
| `base_cli.redaction` | 90% |

CI writes a branch-aware `coverage.json` report and runs
`scripts/validate_coverage.py` against these floors. A floor change belongs in
the same pull request as the tests that justify it. Platform-specific tests
must cover both the native path and the safe fallback where the operating
system provides a different filesystem primitive.
