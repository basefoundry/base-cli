# Migrating from Click

## When this path fits

Use this path when the application already has a Click group, commands, and
tests that should remain intact. `base-cli` is a lifecycle layer around the
tree; it is not a replacement for Click decorators or parameter parsing.

## Incremental change

Before, the application usually owns invocation and logging directly:

```python
import click


@click.group()
def cli() -> None:
    """Example command tree."""


@cli.command()
def status() -> None:
    click.echo("ready")


if __name__ == "__main__":
    cli()
```

After, keep the decorators and callbacks, and attach the tree at the process
boundary:

```python
import base_cli
import click


@click.group()
def cli() -> None:
    """Example command tree."""


@cli.command()
def status() -> None:
    context = base_cli.get_current_context()
    context.log.info("status requested")
    click.echo("ready")


command = base_cli.attach(cli, sensitive_parameters=())

if __name__ == "__main__":
    raise SystemExit(base_cli.run_app(command))
```

Click still owns the group, options, parameters, help, completion, and Click
exceptions. `base-cli` adds the context, lifecycle options, structured logging,
redaction, runtime paths, cleanup, history hooks, and outcome handling. Put
workspace/configuration policy in a consumer `CliProfile`; do not hard-code
product assumptions in the framework.

## Verification and rollback

- [ ] Compare `--help`, option defaults, exit codes, and stdout with the
  inventory from the old entry point.
- [ ] Add JSON/NDJSON fixtures if automation consumes command output.
- [ ] Exercise `--debug`, `--quiet`, `--keep-temp`, and configured `--json`.
- [ ] Run the installed-wheel smoke test on every supported platform.
- [ ] Keep the old console-script entry point and last known-good wheel until
  the pilot passes; reverting the entry point is the rollback.

See [`output-contracts.md`](output-contracts.md),
[`json-contracts.md`](json-contracts.md), and
[`adopter-readiness.md`](adopter-readiness.md) for the operational details.
