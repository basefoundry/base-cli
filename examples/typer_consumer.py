"""Typer migration example for the optional base-cli adapter.

Install the integration with ``python -m pip install 'base-cli[typer]'``.
Typer continues to own decorators, typed parameters, nested apps, and its
dependency injection; base-cli supplies the invocation lifecycle around the
generated Click tree.
"""

from __future__ import annotations

import typer

import base_cli


cli = typer.Typer(help="A small Typer application with a shared lifecycle.")


@cli.callback()
def callback() -> None:
    """Keep Typer's normal callback and help behavior."""


@cli.command()
def greet(
    name: str = typer.Option(..., help="Name to greet."),
    access_code: str = typer.Option(..., hidden=True),
) -> None:
    """Greet someone using Typer's typed options."""

    context = base_cli.get_current_context()
    context.log.info("greeting %s", name)
    # The value is available to the command but is redacted from lifecycle
    # invocation logs by the same policy as an attached Click application.
    del access_code
    typer.echo(f"hello {name}")


# A multi-command Typer app without an explicit Typer name produces an unnamed
# Click group.  ``name=`` gives base-cli (and the generated usage) a stable
# program name without changing any Typer command declarations.
command = base_cli.attach_typer(
    cli,
    name="typer-example",
    sensitive_parameters={"access_code"},
)


if __name__ == "__main__":
    raise SystemExit(base_cli.run_app(command))
