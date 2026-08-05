"""Atlas: a pre-existing Click tree adopting base-cli lifecycle services."""

from __future__ import annotations

import click
import base_cli


@click.group(name="atlas-consumer", help="Inventory resources managed by Atlas.")
def cli() -> None:
    """The consumer owns this tree and its command names."""


@cli.command()
@click.option(
    "--format",
    "output_format",
    type=click.Choice(base_cli.output_format_choices().split("|")),
    default="json",
    show_default=True,
)
@click.option("--api-key", hidden=True)
def inventory(output_format: str, api_key: str | None) -> None:
    """Return a small inventory suitable for a monitoring job."""

    context = base_cli.get_current_context()
    context.log.info("Atlas inventory requested")
    del api_key
    base_cli.render_records(
        ({"consumer": "atlas", "resource": "catalog", "status": "ready"},),
        requested_format=output_format,
        columns=(("CONSUMER", "consumer"), ("RESOURCE", "resource"), ("STATUS", "status")),
        rich=context.rich,
    )


command = base_cli.attach(cli, sensitive_parameters={"api_key"})


def main() -> int:
    return base_cli.run_app(command)


if __name__ == "__main__":
    raise SystemExit(main())
