# Beacon consumer fixture

Beacon represents a typed deployment CLI that already uses Typer. The fixture
proves that `base_cli.attach_typer()` preserves Typer's generated help,
validation, and callback behavior while adding the `base-cli` framework lifecycle.

Install with `python -m pip install .`, run `beacon-consumer --help`, and execute
`beacon-consumer --quiet deploy --service api --replicas 3`. The supported Typer
range is explicit in package metadata and is exercised by the compatibility
workflow. Run `python -m pytest tests` to repeat the downstream tests locally.
