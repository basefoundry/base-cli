# base-cli

`base-cli` is a small, consumer-neutral Python framework for writing
professional command-line applications. It gives commands a consistent
lifecycle, context, logging, cleanup, configuration, and test boundary while
leaving application policy in the consuming project.

In one sentence: **base-cli is the production lifecycle layer for a Click or
Typer CLI**. It keeps parsing and command policy familiar while making the
operational contract—context, logs, cleanup, configuration, and automation
output—repeatable across commands.

## Quick start

Install the package:

```bash
python -m pip install base-cli
```

Create a command:

```python
from __future__ import annotations

import base_cli


app = base_cli.App(name="hello", version="0.1.0")


@app.command()
@base_cli.option("--name", default="world", show_default=True)
def hello(ctx: base_cli.Context, name: str) -> int:
    ctx.log.info("greeting %s", name)
    print(f"Hello, {name}!")
    return base_cli.ExitCode.SUCCESS


if __name__ == "__main__":
    raise SystemExit(base_cli.run_app(app))
```

Run it with:

```bash
python hello.py --name Ada
```

The command receives a context with structured logging, per-run paths,
configuration, environment metadata, and deterministic cleanup. The same
lifecycle can be attached to an existing Click tree or an optional Typer
application.

## Choose a path

- Use the [framework choice guide](framework-choice.md) to compare base-cli
  with the underlying parser and decide whether its lifecycle boundary fits.
- Start with the [adopter readiness guide](adopter-readiness.md) for a
  production evaluation.
- Read [API stability](api-stability.md) and the [migration guide](migrations.md)
  before upgrading across a compatibility boundary.
- Follow [consumer profiles](consumer-profiles.md) when your application owns
  project discovery or configuration policy.
- Use the [Typer adapter](typer-adapter.md) to bring an existing Typer command
  tree under the same lifecycle.
- Review the [JSON contracts](json-contracts.md) and [output contracts](output-contracts.md)
  before building automation around command output.

## Design principles

`base-cli` is intentionally thin: Click owns parsing and command execution,
while the framework supplies reusable lifecycle behavior. It avoids import-time
filesystem writes, keeps logs on stderr, preserves application-owned state,
and treats optional integrations as explicit extras.

Continue with the framework guides:

- [API overview](api-stability.md) for the supported public surface and
  compatibility policy.
- [Reference applications](https://github.com/basefoundry/base-cli/tree/main/examples)
  for complete consumer patterns.
- [Installation and packaging](adopter-readiness.md) for dependency extras,
  distribution checks, and release guidance.
