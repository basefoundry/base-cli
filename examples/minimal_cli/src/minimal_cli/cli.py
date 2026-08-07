"""A small, installable single-command application."""

from __future__ import annotations

import base_cli

app = base_cli.App(
    name="base-minimal",
    version="0.1.0",
    help="Greet a person with the base-cli lifecycle.",
)


@app.command()
@base_cli.option("--name", required=True, help="Name to greet.")
def greet(ctx: base_cli.Context, name: str) -> None:
    """Print a deterministic greeting."""

    ctx.log.info("greeting requested for %s", name)
    print(f"hello {name}")


def main() -> int:
    """Console-script entry point."""

    return base_cli.run_app(app)


if __name__ == "__main__":
    raise SystemExit(main())
