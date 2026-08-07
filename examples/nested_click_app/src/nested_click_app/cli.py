"""Native Click group with a base-cli lifecycle boundary."""

from __future__ import annotations

import base_cli
import click


@click.group(name="base-nested", help="A nested Click app with safe plugins.")
def cli() -> None:
    """Keep Click responsible for the command tree."""


@cli.command()
@click.option(
    "--format",
    "output_format",
    type=click.Choice(base_cli.output_format_choices().split("|")),
    default="text",
    show_default=True,
)
def status(output_format: str) -> None:
    """Report application status as a table or machine-readable records."""

    context = base_cli.get_current_context()
    base_cli.render_records(
        ({"service": "nested-click", "status": "ready"},),
        requested_format=output_format,
        columns=(("SERVICE", "service"), ("STATUS", "status")),
        rich=context.rich,
    )


@cli.command()
@click.option("--api-token", hidden=True, help="Example secret; never log this value.")
def inspect(api_token: str | None) -> None:
    """Exercise sensitive-parameter redaction at a nested command boundary."""

    context = base_cli.get_current_context()
    context.log.info("inspection requested")
    click.echo("token supplied" if api_token else "token not supplied")


@cli.command()
def plugins() -> None:
    """Load installed plugins independently and report their outcomes."""

    discovery = base_cli.ExtensionDiscovery()
    results = discovery.load_all(base_cli.PLUGIN_ENTRY_POINT_GROUP)
    if not results:
        click.echo("No installed plugins.")
        return
    for result in results:
        state = "loaded" if result.ok else f"error: {result.error}"
        click.echo(f"{result.descriptor.name}: {state}")


command = base_cli.attach(
    cli,
    sensitive_parameters={"api_token"},
)


def main() -> int:
    """Console-script entry point."""

    return base_cli.run_app(command)


if __name__ == "__main__":
    raise SystemExit(main())
