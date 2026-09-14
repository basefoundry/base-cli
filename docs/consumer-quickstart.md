# Five-minute consumer quickstart

This is the smallest useful `base-cli` application: one public `App`, one
command, one option, and the `run_app()` process boundary. It is suitable for
a temporary consumer project and does not require Typer, Rich, YAML, or any
private repository layout.

## Install and create the command

Use an isolated virtual environment when trying the recipe. Activate it using
the command appropriate for your shell, then install the core package:

```bash
python -m venv .venv
python -m pip install base-cli
```

Create `hello.py`:

```python
from __future__ import annotations

import base_cli


app = base_cli.App(
    name="hello",
    lifecycle_options=base_cli.LifecycleOptions(
        json=base_cli.LifecycleOption("--json"),
    ),
)


@app.command()
@base_cli.option("--name", default="world", show_default=True)
def hello(ctx: base_cli.Context, name: str) -> int:
    ctx.log.info("greeting %s", name)
    print(f"Hello, {name}!")
    return base_cli.ExitCode.SUCCESS


if __name__ == "__main__":
    raise SystemExit(base_cli.run_app(app))
```

The recipe uses only the public `base_cli` facade. Click remains the parser,
while the consumer owns the command and its application behavior.

## Run it in human mode

```bash
python hello.py --name Ada
```

The command prints `Hello, Ada!` on stdout. The greeting log is on stderr, so a
consumer can redirect or suppress logs without corrupting command output.

## Run the same command as JSON

The recipe explicitly enables the optional lifecycle `--json` flag. Capture and
parse the one JSON envelope like this:

```bash
python hello.py --json --name Ada > result.json
python -m json.tool result.json
```

The success payload has `schema: "base-cli.output"`, `code: "ok"`, and a
`details.stdout` string containing the command's human output. The `run_id` is
runtime data and should not be hard-coded. Logs and diagnostics remain on
stderr. See [JSON contracts](json-contracts.md) for the complete success,
usage-error, and unexpected-error boundary.

## What to validate

The focused consumer checks are the two invocations above: confirm the human
stdout and the JSON parse independently, and confirm that the process exit
status is zero. For a repository change, run the focused documentation test
with `python -m pytest tests/test_consumer_quickstart_docs.py` and run
`git diff --check`.

The core package requires Click and Python 3.10 or newer. Install
`base-cli[yaml]` only when the consumer selects YAML configuration or output;
Typer and other integrations are separate optional boundaries. See
[dependency support](dependency-support.md) and [output contracts](output-contracts.md)
before adding those integrations.
