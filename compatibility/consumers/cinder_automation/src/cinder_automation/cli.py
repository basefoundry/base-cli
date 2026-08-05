"""Cinder: a scheduled reconciler with explicit machine contracts."""

from __future__ import annotations

import click
import base_cli


app = base_cli.App(
    name="cinder-consumer",
    version="0.1.0",
    help="Reconcile a Cinder target safely.",
    lifecycle_options=base_cli.LifecycleOptions(
        dry_run=base_cli.LifecycleOption("--dry-run", help="Plan without changing state."),
        json=base_cli.LifecycleOption("--json", help="Emit the versioned lifecycle envelope."),
    ),
    telemetry=base_cli.TelemetryOptions(),
)


@app.command()
@base_cli.option("--target", required=True)
@base_cli.option(
    "--format",
    "output_format",
    type=click.Choice(base_cli.output_format_choices().split("|")),
    default="json",
    show_default=True,
)
def reconcile(ctx: base_cli.Context, target: str, output_format: str) -> None:
    """Publish the result of one idempotent reconciliation step."""

    action = "would-reconcile" if ctx.dry_run else "reconciled"
    ctx.log.info("Cinder %s target=%s", action, target)
    base_cli.render_records(
        ({"consumer": "cinder", "target": target, "action": action},),
        requested_format=output_format,
        columns=(("CONSUMER", "consumer"), ("TARGET", "target"), ("ACTION", "action")),
    )


def main() -> int:
    return base_cli.run_app(app)


if __name__ == "__main__":
    raise SystemExit(main())
