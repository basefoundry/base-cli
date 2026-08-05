# Automation and observability app

This reference is shaped like a scheduled automation command: it supports
idempotent dry runs, structured output, Rich terminal presentation, JSON logs,
and an optional OpenTelemetry lifecycle span. Integrations remain best-effort;
missing exporters cannot change the command's exit status.

## Install

```bash
python -m pip install "base-cli[rich,telemetry]"
python -m pip install ".[observability]"
base-automation --help
```

The first command installs framework integrations; the second installs this
example. In a normal project, pin both sets of dependencies in one lock file.

## Configuration

Use `--environment`, `--config`, and `--log-file` for lifecycle configuration.
`--dry-run` is the safety gate for scheduled changes, and `--keep-temp` is
useful while investigating a failed run. Real products should map a validated
consumer profile into `ctx.config` rather than reading untrusted environment
variables directly in a command.

## Output and errors

```bash
base-automation --quiet --target database --format json
base-automation --quiet --dry-run --target database --format json
```

Both commands emit one deterministic record; the dry-run action is
`would-reconcile`. Human `text` output uses Rich when installed and falls back
to the built-in renderer. Invalid options return a non-zero status and keep
diagnostics in the redacted log.

## Tests

```bash
python -m pip install "base-cli[dev,rich,telemetry]"
python -m pytest tests
```

Tests cover the safety gate and verify that telemetry remains optional.

## Completion

```bash
_BASE_AUTOMATION_COMPLETE=bash_source base-automation
_BASE_AUTOMATION_COMPLETE=zsh_source base-automation
_BASE_AUTOMATION_COMPLETE=fish_source base-automation
```

Install generated scripts through your shell's usual completion directory.

## Release guidance

Publish a wheel built from a clean, locked environment, run the command against
a staging target with `--dry-run`, and verify JSON output and log schemas before
promoting. Keep OpenTelemetry exporters and Rich as optional extras so minimal
automation images stay small.

## Operational troubleshooting

- Add `--debug --log-file /tmp/base-automation.log` to collect diagnostics.
- If spans are absent, verify the OpenTelemetry SDK/provider is configured; the
  command intentionally treats an absent exporter as a no-op.
- Start every incident with `--dry-run` and compare the JSON record to the
  expected state before permitting writes.
- Include the run ID and redacted logs in support requests, never tokens.
