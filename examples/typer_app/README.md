# Typer app

This reference uses Typer for typed parameters and validation while
`base-cli.attach_typer()` supplies the shared lifecycle. Typer remains an
optional dependency for applications that prefer annotations and generated
help.

## Install

```bash
python -m pip install "base-cli[typer]"
python -m pip install .
base-typer --help
```

The example pins the supported Typer range (`0.12` through `0.25`) because
newer releases use a private Click fork that the adapter intentionally rejects.

## Configuration

The root command accepts base-cli lifecycle configuration such as
`--environment`, `--config`, `--log-file`, `--debug`, `--quiet`, and
`--dry-run`. Keep application settings in Typer's typed options or pass an
explicit `base_cli.App` profile when a product needs richer configuration.

## Output and errors

`base-typer greet --name Ada --count 2` prints two human lines. Typer's native
validation reports invalid counts and missing names with non-zero exits; the
adapter preserves those exceptions and help text. The hidden `--access-code`
option demonstrates sensitive-parameter redaction.

## Tests

```bash
python -m pip install "base-cli[dev,typer]"
python -m pytest tests
```

The tests invoke the generated Click command, not a private Typer implementation.

## Completion

```bash
_BASE_TYPER_COMPLETE=bash_source base-typer
_BASE_TYPER_COMPLETE=zsh_source base-typer
_BASE_TYPER_COMPLETE=fish_source base-typer
```

Typer delegates completion to Click; install the generated script in the
standard directory for your shell.

## Release guidance

Lock the Typer and Click versions together, test the adapter on every supported
Python version, run `python -m build`, and inspect the generated wheel before
publishing. Announce adapter support-range changes as release notes.

## Operational troubleshooting

- Run with `--debug` and capture `--log-file` when diagnosing a command.
- If help fails after a Typer upgrade, check that the version is below 0.26.
- Use `--count 1` to distinguish application failures from input validation.
- Redact access codes and other credentials from issue reports and transcripts.
