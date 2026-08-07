from __future__ import annotations

import tempfile
from pathlib import Path

import base_cli
from nested_click_app.cli import command


def test_nested_status_is_machine_readable() -> None:
    with tempfile.TemporaryDirectory() as directory:
        result = base_cli.testing.invoke(
            command,
            ["--quiet", "status", "--format", "json"],
            home=Path(directory),
        )

    assert result.exit_code == 0, result.output
    assert '"service":"nested-click"' in result.stdout


def test_plugin_command_is_safe_when_metadata_is_available() -> None:
    with tempfile.TemporaryDirectory() as directory:
        result = base_cli.testing.invoke(
            command,
            ["--quiet", "plugins"],
            home=Path(directory),
        )
    assert result.exit_code == 0, result.output
