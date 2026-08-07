"""Typed Typer commands wrapped by the base-cli lifecycle."""

from __future__ import annotations

import base_cli
import typer

cli = typer.Typer(help="A typed application with a shared lifecycle.")


@cli.callback()
def callback() -> None:
    """Keep Typer's callback and help behavior intact."""


@cli.command()
def greet(
    name: str = typer.Option(..., help="Name to greet."),
    count: int = typer.Option(1, min=1, max=5, help="Number of greetings."),
    access_code: str | None = typer.Option(None, hidden=True),
) -> None:
    """Greet someone using typed options and validation."""

    context = base_cli.get_current_context()
    context.log.info("greeting %s", name)
    del access_code
    for _ in range(count):
        typer.echo(f"hello {name}")


command = base_cli.attach_typer(
    cli,
    name="base-typer",
    sensitive_parameters={"access_code"},
)


def main() -> int:
    """Console-script entry point."""

    return base_cli.run_app(command)


if __name__ == "__main__":
    raise SystemExit(main())
