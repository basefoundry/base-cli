# Optional integrations

The core `base-cli` install has no Rich or OpenTelemetry dependency. Install
only the integration you use:

```bash
python -m pip install 'base-cli[rich]'
python -m pip install 'base-cli[telemetry]'
```

## Rich human tables

Pass `rich=True` when constructing an app and pass the active context's flag to
the shared record renderer:

```python
from typing import Any

import base_cli

app = base_cli.App(name="catalog", rich=True)

@app.command()
def list_items(ctx: base_cli.Context[Any, Any, Any]) -> None:
    base_cli.render_records(
        ({"name": "base", "path": "/work/base"},),
        requested_format="text",
        columns=(("NAME", "name"), ("PATH", "path")),
        rich=ctx.rich,
    )
```

Rich is used only for interactive human text. Redirected text remains TSV, and
CSV, TSV, JSON, and YAML contracts do not change. If Rich is missing or its
renderer fails, the deterministic built-in table is used automatically.

## OpenTelemetry lifecycle spans

Telemetry is opt-in and can use the application's configured global provider or
an explicitly supplied tracer:

```python
import base_cli

app = base_cli.App(
    name="catalog",
    telemetry=base_cli.TelemetryOptions(),
)
```

Each invocation emits a `base_cli.run` span with a start event and a finish
event. Safe attributes include the run ID, CLI name, environment, dry-run flag,
outcome, exit code, and duration. Raw argv, configuration values, filesystem
paths, and secrets are never attached. A missing API package, invalid provider,
or failing exporter is logged at debug level and treated as a no-op; it cannot
change the command's exit status or cleanup behavior.

The span status is `OK` for a successful invocation and `ERROR` for every
non-success outcome. Unexpected exceptions are recorded on failed spans;
successful `SystemExit(0)` and Click exit-control flow are not recorded as
exceptions. Interrupts and other non-success outcomes are therefore visible as
errors while retaining the existing `base_cli.outcome` attribute for detailed
dashboard filtering.

## Child-process run and trace propagation

Use the active context when a command launches another CLI:

```python
import subprocess

subprocess.run(["other-cli", "sync"], env=ctx.child_environment(), check=True)
```

`Context.child_environment()` returns a copy of the environment containing the
following language-neutral contract:

| Variable | Meaning |
| --- | --- |
| `BASE_CLI_RUN_ID` | The current run identity. It is validated as one safe path component. |
| `BASE_CLI_PARENT_RUN_ID` | The launching run identity. |
| `BASE_CLI_RUN_ROOT` | The absolute, framework-owned bundle to reuse when it exists. |
| `BASE_CLI_RUN_SCOPE` | `internal` for a propagated child, or `primary` for a deliberate top-level record. |
| `BASE_CLI_PROPAGATE` | Set to `0` to opt out of runtime and trace propagation. |
| `TRACEPARENT` / `TRACESTATE` | W3C trace context for OpenTelemetry-enabled consumers. |

The generic profile honors these variables without a custom `RuntimeResolver`.
An inherited `BASE_CLI_RUN_ROOT` must be absolute, non-symlinked, and contain
valid `run.json` metadata; malformed IDs, traversal, absolute IDs, and invalid
scopes fail with an actionable configuration error before any path is created.
The generic profile's explicit `RuntimeResolver`, when supplied, is
authoritative and is not overridden by environment values.

An inherited child reuses the parent's bundle and is marked `internal`; only
the process that created the bundle owns its lifecycle metadata and retention.
The framework cannot intercept arbitrary subprocess APIs, so callers must pass
the returned mapping (or selectively copy its documented variables). Use
`ctx.child_environment(propagate=False)` for an intentionally independent
child. OpenTelemetry extraction/injection remains best-effort and never makes
the optional dependency required.

## Logger ownership

The lifecycle uses `base_cli.<cli_name>` and owns only the handlers it creates.
Consumer handlers remain attached and open after configuration and cleanup.
An explicit consumer logger level, or configuration on the `base_cli` parent,
is preserved, including `logging.config.dictConfig()` routing. Such levels may
filter records before the lifecycle handlers see them. Configure the consumer
logger at DEBUG if the persistent handler should receive every record.

Unconfigured CLI loggers use DEBUG with propagation disabled to avoid duplicate
terminal output. `configure_logger(..., propagate=True)` explicitly enables host
routing; `False` disables it, and the default `None` preserves consumer routing.
Use a consumer handler or configure the `base_cli` parent before invoking an App
when embedding it in a host with centralized logging.

## Log timestamp environment variable

Set `BASE_CLI_LOG_UTC=1` to make the default text formatter use UTC
timestamps. The namespaced variable takes precedence over the legacy setting.
`LOG_UTC` remains recognized during the 0.5 compatibility window, but emits a
`BaseCliDeprecationWarning` and is scheduled for removal no earlier than 0.7.
