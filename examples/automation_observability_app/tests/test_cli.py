from __future__ import annotations

import tempfile
from pathlib import Path

import base_cli
from automation_observability_app.cli import app


def test_dry_run_is_safe_and_machine_readable() -> None:
    with tempfile.TemporaryDirectory() as directory:
        result = base_cli.testing.invoke(
            app,
            ["--quiet", "--dry-run", "--target", "database", "--format", "json"],
            home=Path(directory),
        )

    assert result.exit_code == 0, result.output
    assert '"action":"would-reconcile"' in result.stdout


def test_telemetry_option_does_not_make_the_command_require_an_exporter() -> None:
    result = base_cli.testing.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "--dry-run" in result.stdout
