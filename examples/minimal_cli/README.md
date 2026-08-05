# Minimal CLI

This is the smallest complete `base-cli` application: one typed command,
standard lifecycle flags, file logging, cleanup, and a console-script entry
point. It is a good starting point for an internal tool or a new product.

## Install

```bash
python -m pip install "base-cli>=0.3"
python -m pip install .
base-minimal --help
```

## Configuration

`base-cli` supplies `--environment`, `--config`, `--log-file`, `--keep-temp`,
`--debug`, `--quiet`, and `--dry-run` lifecycle options. Keep product settings
in your own profile when the application grows; this reference deliberately
has only the required `--name` option.

## Output and errors

Successful human output is `hello NAME` on stdout. Invalid or missing options
use Click's non-zero exit status and actionable stderr message. Use
`--json` when integrating with a machine-facing wrapper; framework lifecycle
errors follow the versioned JSON contract documented by base-cli.

## Tests

```bash
python -m pip install "base-cli[dev]"
python -m pytest tests
```

The tests invoke the same attached command object used by the console script.

## Completion

Click can generate completion for the installed command:

```bash
_BASE_MINIMAL_COMPLETE=bash_source base-minimal
_BASE_MINIMAL_COMPLETE=zsh_source base-minimal
_BASE_MINIMAL_COMPLETE=fish_source base-minimal
```

Persist the generated script using your shell's normal completion directory.

## Release guidance

Pin `base-cli` in a lock file, run the tests on Python 3.10--3.14, build with
`python -m build`, inspect the wheel contents, and upload through your trusted
PyPI publisher. Keep the example's version independent of framework releases.

## Operational troubleshooting

- Run `base-minimal --debug --name Ada` for verbose diagnostics.
- Add `--log-file /path/to/run.log` when collecting a support bundle.
- If a config path is rejected, verify it is readable and use an absolute path.
- For a reproducible bug, include the command, Python version, exit code, and
  the redacted log; never include secrets.
