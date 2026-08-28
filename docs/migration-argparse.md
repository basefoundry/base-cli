# Migrating from argparse

## When this path fits

Use this path when a standard-library `argparse` CLI needs consistent runtime
state, diagnostics, cleanup, and machine-readable output. Keep the parser
stable first; replacing parsing and lifecycle in the same change makes
behavioral regressions difficult to diagnose.

## Incremental change

An `argparse` entry point commonly combines parsing and application work:

```python
import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    print("ready (verbose)" if args.verbose else "ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

The lowest-risk `base-cli` adoption is to move the command tree to Click while
preserving the option and callback contract:

```python
import base_cli
import click


@click.command(name="example")
@click.option("--verbose", is_flag=True)
def cli(verbose: bool) -> None:
    base_cli.get_current_context().log.info("status requested")
    click.echo("ready (verbose)" if verbose else "ready")


command = base_cli.attach(cli)

if __name__ == "__main__":
    raise SystemExit(base_cli.run_app(command))
```

`argparse`/Click remains responsible for parsing, help, completion, parameter
types, and usage errors. `base-cli` owns lifecycle options and hooks, context,
structured logging, redaction, runtime paths, cleanup, history, and output
contracts. If retaining `argparse` is a hard requirement, integrate the
consumer's parser at its own boundary and adopt the `base-cli` contracts
incrementally; `attach()` expects a Click command.

## Verification and rollback

- [ ] Snapshot option spelling, defaults, help text, exit codes, and parser
  errors before changing the command tree.
- [ ] Test both human output and JSON/NDJSON fixtures, with diagnostics on
  stderr and command output on stdout.
- [ ] Run the installed-wheel and supported-platform checks before removing
  the old parser entry point.
- [ ] Keep the `argparse` entry point and previous wheel available for rollback
  until every consumer has migrated.

See [`output-contracts.md`](output-contracts.md) and
[`json-contracts.md`](json-contracts.md) for the stable output boundary.
