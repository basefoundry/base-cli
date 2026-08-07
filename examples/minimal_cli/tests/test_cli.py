from __future__ import annotations

import tempfile
from pathlib import Path

import base_cli
from minimal_cli.cli import app


def test_greeting_uses_the_installed_lifecycle() -> None:
    with tempfile.TemporaryDirectory() as directory:
        result = base_cli.testing.invoke(
            app,
            ["--quiet", "--name", "Ada"],
            home=Path(directory),
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "hello Ada"


def test_help_exposes_lifecycle_options() -> None:
    result = base_cli.testing.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "--name" in result.stdout
    assert "--environment" in result.stdout
