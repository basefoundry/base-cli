# Nested Click app

This reference keeps Click's native group, nested commands, and exceptions,
then attaches one `base-cli` lifecycle boundary. It also shows the optional
entry-point plugin contract with deterministic discovery and isolated failures.

## Install

```bash
python -m pip install "base-cli>=0.3"
python -m pip install .
base-nested --help
```

## Configuration

Lifecycle configuration is available on the root command (`--environment`,
`--config`, `--log-file`, `--debug`, `--quiet`, and `--dry-run`). Product
configuration belongs in a consumer-owned `CliProfile`; do not let plugins
silently read global files or environment variables.

## Output and errors

`base-nested status --format json` emits stable records suitable for automation;
`text`, `csv`, and `tsv` are available from the core package. Install
`base-cli[yaml]` to enable `yaml` output. Click retains its normal
usage errors and exit codes. Plugin import failures are reported per plugin so
a broken optional extension cannot hide healthy ones.

## Tests

```bash
python -m pip install "base-cli[dev]"
python -m pytest tests
```

Tests exercise a nested command and machine-readable output through the public
testing helper.

## Completion

```bash
_BASE_NESTED_COMPLETE=bash_source base-nested
_BASE_NESTED_COMPLETE=zsh_source base-nested
_BASE_NESTED_COMPLETE=fish_source base-nested
```

Install the generated script using the completion mechanism for your shell.

## Release guidance

Pin both `base-cli` and Click, run compatibility tests across supported Python
versions, build and inspect a wheel, and publish plugin entry points only after
reviewing their import-time behavior. Treat plugin names as a compatibility
contract and document deprecations before removing one.

## Operational troubleshooting

- `base-nested --debug plugins` shows discovery diagnostics in the run log.
- Use `--log-file` to provide a redacted, reproducible support artifact.
- A collision means two distributions claim the same plugin name; rename one.
- Use `base-nested inspect --api-token ...` only for local testing; secrets are
  sensitive parameters and must never appear in tickets or shell history.
