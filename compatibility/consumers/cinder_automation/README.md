# Cinder consumer fixture

Cinder represents a scheduled automation worker that needs a safe dry-run,
deterministic records, and an optional OpenTelemetry provider. The fixture
proves that a native `base_cli.App` can expose both a command-level record
contract and the versioned lifecycle JSON envelope provided by `base-cli`.

Install with `python -m pip install '.[observability]'`, run
`cinder-consumer --help`, and start with
`cinder-consumer --quiet --dry-run --target warehouse`. Exporters are optional;
the command remains successful when no provider is configured.
Run `python -m pytest tests` to repeat the dry-run and JSON contract tests.
