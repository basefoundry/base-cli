# Typer adapter

`base-cli` can wrap an existing [Typer](https://typer.tiangolo.com/) app
without making Typer a core dependency:

```bash
python -m pip install 'base-cli[typer]'
```

The extra currently supports Typer 0.12 through 0.25.  Typer 0.26 and later
ship a private Click fork; the adapter will reject those command objects until
base-cli can provide an equivalent compatibility boundary without changing the
core Click integration.

Use `attach_typer()` at the same boundary where a Click app would use
`attach()`:

```python
import typer
import base_cli

cli = typer.Typer()


@cli.command()
def status(verbose: bool = typer.Option(False, "--verbose")) -> None:
    ctx = base_cli.get_current_context()
    if verbose:
        ctx.log.info("checking status")
    typer.echo("ready")


command = base_cli.attach_typer(cli, name="example")

if __name__ == "__main__":
    raise SystemExit(base_cli.run_app(command))
```

The adapter calls Typer's supported command materializer and returns that same
Click command object.  Typer remains responsible for command decorators,
typed parameters, nested apps, dependency injection, help, completion, and
Typer/Click exceptions.  Base-cli adds its normal lifecycle options, context,
logging, redaction, runtime state, cleanup, and outcome handling.

For an application that needs a custom profile or factories, pass an explicit
`base_cli.App`:

```python
lifecycle = base_cli.App(name="example", profile=my_profile)
command = base_cli.attach_typer(cli, app=lifecycle)
```

Typer's single-command form normally derives a root name from the callback.
Pass `name=` when the lifecycle should use a different program name.  A
multi-command Typer app with no explicit name produces an unnamed group, so
`name=` (or a named `App`) is required.  The generated command is renamed in
place; callbacks and Typer parameter metadata are not copied or rewritten.

`TyperAdapter` is available when the generated command needs to be retained:

```python
adapter = base_cli.TyperAdapter(cli)
command = adapter.attach(name="example")
```

Typer is an optional extra and is imported lazily.  Importing `base_cli` and
using the Click integration never imports or requires Typer.
