from __future__ import annotations

import tempfile
from pathlib import Path

import base_cli

from beacon_typer.cli import command


def test_typer_deployment_preserves_typed_parameters() -> None:
    with tempfile.TemporaryDirectory() as directory:
        result = base_cli.testing.invoke(
            command,
            ["--quiet", "deploy", "--service", "api", "--replicas", "3"],
            home=Path(directory),
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "deploy api replicas=3"


def test_typer_rejects_invalid_replica_count() -> None:
    result = base_cli.testing.invoke(command, ["deploy", "--service", "api", "--replicas", "0"])
    assert result.exit_code != 0
