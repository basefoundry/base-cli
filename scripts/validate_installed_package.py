#!/usr/bin/env python3
"""Exercise the public API from an installed wheel, never the source tree."""

from __future__ import annotations

import importlib.metadata
import io
import os
import tempfile
from pathlib import Path

import base_cli
from base_cli.testing import invoke


def main() -> None:
    expected_version = os.environ.get("EXPECTED_VERSION")
    installed_version = importlib.metadata.version("base-cli")
    if expected_version and installed_version != expected_version:
        raise AssertionError((installed_version, expected_version))
    if Path(base_cli.__file__).resolve().is_relative_to(Path.cwd().resolve() / "lib"):
        raise AssertionError(f"import resolved to the source tree: {base_cli.__file__}")

    app = base_cli.App(name="installed-smoke", log_to_file=False)
    seen: dict[str, str] = {}

    @app.command()
    def main_command(ctx: base_cli.Context) -> None:
        seen["run_id"] = ctx.run_id

    with tempfile.TemporaryDirectory() as home:
        result = invoke(app, [], home=Path(home))
    if result.exit_code != 0 or not seen.get("run_id"):
        raise AssertionError(result.output)

    stream = io.StringIO()
    base_cli.render_records(
        ({"name": "installed", "version": installed_version},),
        requested_format="tsv",
        columns=(("NAME", "name"), ("VERSION", "version")),
        stream=stream,
    )
    if stream.getvalue() != f"installed\t{installed_version}\n":
        raise AssertionError(stream.getvalue())

    print(f"Validated installed base-cli {installed_version} public API and lifecycle.")


if __name__ == "__main__":
    main()
