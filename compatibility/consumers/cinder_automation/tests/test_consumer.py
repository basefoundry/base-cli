from __future__ import annotations

import tempfile
from pathlib import Path

import base_cli

from cinder_automation.cli import app


def test_dry_run_is_deterministic_json() -> None:
    with tempfile.TemporaryDirectory() as directory:
        result = base_cli.testing.invoke(
            app,
            ["--quiet", "--dry-run", "--target", "warehouse"],
            home=Path(directory),
        )

    assert result.exit_code == 0, result.output
    assert '"action":"would-reconcile"' in result.stdout


def test_json_lifecycle_envelope_remains_available() -> None:
    with tempfile.TemporaryDirectory() as directory:
        result = base_cli.testing.invoke(
            app,
            ["--quiet", "--json", "--target", "warehouse"],
            home=Path(directory),
        )

    assert result.exit_code == 0, result.output
    assert '"schema":"base-cli.output"' in result.stdout
