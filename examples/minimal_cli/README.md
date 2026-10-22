# Minimal CLI starter

This is the maintained, copyable starter for a new `base-cli` application. It
is intentionally small: one typed command, standard lifecycle flags, file
logging, cleanup, and a console-script entry point. It is a good starting
point for an internal tool or a new product.

The starter is a reference template, not a code generator. Copy the directory
into your project, run the normal install and test commands below, and then
replace the command implementation at `src/minimal_cli/cli.py`.

## Start in five minutes

From the root of a `base-cli` checkout:

```bash
cp -R examples/minimal_cli my-cli
cd my-cli
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
python -m pip install --upgrade pip
python -m pip install -e .
base-minimal --name Ada
python -m pytest
```

The copy is a complete installable project. It exercises the same package,
console-script, and test boundaries that a production consumer uses; it does
not import the repository checkout.

## Safe customization points

- `src/minimal_cli/cli.py`: replace `greet()` with the product's commands and
  keep `base_cli.run_app(app)` as the process boundary.
- `src/minimal_cli/__init__.py`: update the consumer package version and
  metadata when the product is released.
- `pyproject.toml`: rename the distribution and console script, then pin the
  supported `base-cli` release line.
- `tests/test_cli.py`: keep a happy-path test and add the product's error and
  JSON-contract tests.
- `README.md`: document the product's configuration, output, completion,
  release, and troubleshooting behavior.

Keep framework internals out of the consumer. Click remains the parser and
the public `base_cli` facade is the supported integration boundary.

## Install

```bash
python -m pip install "base-cli>=0.5.1,<1.0"
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
