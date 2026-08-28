# Migrating from Cement

## When this path fits

Use this path when Cement provides an established controller tree, hooks, and
configuration system, but the team wants the shared lifecycle and contracts of
`base-cli`. Cement can remain in place during an evaluation; migrate one
controller at a time rather than rewriting the whole application.

## Incremental change

A small Cement application commonly owns both parsing and process lifecycle:

```python
from cement import App, Controller, ex


class BaseController(Controller):
    class Meta:
        label = "base"

    @ex(help="report status")
    def status(self) -> None:
        print("ready")


class Example(App):
    class Meta:
        label = "example"
        handlers = [BaseController]


with Example() as app:
    app.run()
```

The incremental target is a Click command with the same user-visible command
and options:

```python
import base_cli
import click


@click.group(name="example")
def cli() -> None:
    """Report status."""


@cli.command()
def status() -> None:
    base_cli.get_current_context().log.info("status requested")
    click.echo("ready")


command = base_cli.attach(cli)

if __name__ == "__main__":
    raise SystemExit(base_cli.run_app(command))
```

The parser-owned boundary is the Cement/Click command tree, parameters, help,
completion, and parser errors. Cement hooks and extensions need an explicit
consumer-owned replacement or an adapter; `base-cli` does not emulate Cement's
plugin or configuration conventions. Move lifecycle concerns—logging,
redaction, runtime paths, cleanup, history, and versioned output—into the
`base-cli` boundary and keep domain services in the consumer.

## Verification and rollback

- [ ] Inventory Cement hooks, extensions, config precedence, and controller
  exit behavior before moving each command.
- [ ] Run old and new commands with identical arguments and compare stdout,
  stderr, exit codes, and machine output fixtures.
- [ ] Pilot a single controller and retain the Cement entry point as the
  rollback until diagnostics and cleanup are equivalent.
- [ ] Remove Cement only after all required hooks have consumer-owned tests.

See [`migrations.md`](migrations.md) for the common contract and rollback
checklist.
