"""Beacon: typed deployment commands with the optional Typer adapter."""

from __future__ import annotations

import base_cli
import typer


cli = typer.Typer(help="Deploy Beacon services with typed parameters.")


@cli.callback()
def callback() -> None:
    """Keep Typer's callback and generated help behavior."""


@cli.command()
def deploy(
    service: str = typer.Option(..., help="Service to deploy."),
    replicas: int = typer.Option(1, min=1, max=20, help="Desired replica count."),
    token: str | None = typer.Option(None, hidden=True),
) -> None:
    """Publish the requested deployment plan."""

    context = base_cli.get_current_context()
    context.log.info("Beacon deployment requested for %s", service)
    del token
    typer.echo(f"deploy {service} replicas={replicas}")


command = base_cli.attach_typer(
    cli,
    name="beacon-consumer",
    sensitive_parameters={"token"},
)


def main() -> int:
    return base_cli.run_app(command)


if __name__ == "__main__":
    raise SystemExit(main())
