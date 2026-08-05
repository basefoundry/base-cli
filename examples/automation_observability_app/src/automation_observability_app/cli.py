"""A machine-friendly command with optional Rich and telemetry integrations."""

from __future__ import annotations

import click
import base_cli


app = base_cli.App(
    name="base-automation",
    version="0.1.0",
    help="Run an idempotent automation step with observable lifecycle state.",
    lifecycle_options=base_cli.LifecycleOptions(
        dry_run=base_cli.LifecycleOption(
            "--dry-run",
            help="Describe the change without applying it.",
        ),
        json=base_cli.LifecycleOption(
            "--json",
            help="Wrap command output in the versioned JSON envelope.",
        ),
    ),
    rich=True,
    telemetry=base_cli.TelemetryOptions(),
)


@app.command()
@base_cli.option("--target", required=True, help="Resource to reconcile.")
@base_cli.option(
    "--format",
    "output_format",
    type=click.Choice(base_cli.output_format_choices().split("|")),
    default="text",
    show_default=True,
)
@base_cli.option("--api-token", hidden=True, help="Optional secret for a real adapter.")
def run(
    ctx: base_cli.Context,
    target: str,
    output_format: str,
    api_token: str | None,
) -> None:
    """Reconcile TARGET and publish a stable status record."""

    ctx.log.info("reconciliation requested for %s", target)
    del api_token
    action = "would-reconcile" if ctx.dry_run else "reconciled"
    base_cli.render_records(
        ({"target": target, "action": action, "status": "ok"},),
        requested_format=output_format,
        columns=(("TARGET", "target"), ("ACTION", "action"), ("STATUS", "status")),
        rich=ctx.rich,
    )


def main() -> int:
    """Console-script entry point."""

    return base_cli.run_app(app)


if __name__ == "__main__":
    raise SystemExit(main())
