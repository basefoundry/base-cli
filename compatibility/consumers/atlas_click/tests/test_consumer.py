from __future__ import annotations

import tempfile
from pathlib import Path

import base_cli

from atlas_click.cli import command


def test_existing_click_tree_keeps_its_machine_contract() -> None:
    with tempfile.TemporaryDirectory() as directory:
        result = base_cli.testing.invoke(
            command,
            ["--quiet", "inventory"],
            home=Path(directory),
        )

    assert result.exit_code == 0, result.output
    assert '"consumer":"atlas"' in result.stdout


def test_help_retains_consumer_command_name() -> None:
    result = base_cli.testing.invoke(command, ["--help"])
    assert result.exit_code == 0, result.output
    assert "inventory" in result.stdout
