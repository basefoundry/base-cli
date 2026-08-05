from __future__ import annotations

import tempfile
from pathlib import Path

import base_cli

from typer_app.cli import command


def test_typed_command_preserves_typer_validation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        result = base_cli.testing.invoke(
            command,
            ["--quiet", "greet", "--name", "Ada", "--count", "2"],
            home=Path(directory),
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.count("hello Ada") == 2


def test_typer_help_is_available() -> None:
    result = base_cli.testing.invoke(command, ["--help"])
    assert result.exit_code == 0, result.output
    assert "greet" in result.stdout
