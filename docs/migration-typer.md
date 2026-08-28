# Migrating from Typer

## When this path fits

Use this path when typed Typer commands and dependency injection are already
valuable. Install the optional adapter so Typer remains optional for Click-only
consumers:

```bash
python -m pip install 'base-cli[typer]'
```

## Incremental change

Before, Typer owns the process entry point:

```python
import typer

app = typer.Typer()


@app.command()
def status(verbose: bool = typer.Option(False, "--verbose")) -> None:
    typer.echo("ready" if not verbose else "ready (verbose)")


if __name__ == "__main__":
    app()
```

After, retain the Typer declarations and attach the materialized command:

```python
import base_cli
import typer

app = typer.Typer()


@app.command()
def status(verbose: bool = typer.Option(False, "--verbose")) -> None:
    context = base_cli.get_current_context()
    context.log.info("status requested")
    typer.echo("ready" if not verbose else "ready (verbose)")


command = base_cli.attach_typer(app, name="example")

if __name__ == "__main__":
    raise SystemExit(base_cli.run_app(command))
```

Typer remains responsible for decorators, type-driven parameters, nested apps,
help, completion, dependency injection, and Typer/Click exceptions. The
adapter returns Typer's generated Click command; `base-cli` owns the lifecycle,
context, logging, redaction, runtime state, cleanup, history hooks, and output
contracts. For custom configuration use `base_cli.attach_typer(app,
app=base_cli.App(...))`.

## Verification and rollback

- [ ] Test the supported Typer and Python matrix, including the vendored Click
  boundary used by newer Typer releases.
- [ ] Compare typed defaults, callback injection, help, completion, and exit
  codes with the original app.
- [ ] Add contract fixtures and exercise debug, quiet, JSON, and retained
  diagnostics.
- [ ] Keep the old Typer entry point available until the wheel-first pilot is
  green; restore it to roll back without rewriting command code.

See [`typer-adapter.md`](typer-adapter.md) for adapter details.
