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
import base_cli

app = base_cli.App(name="catalog", rich=True)

@app.command()
def list_items(ctx: base_cli.Context) -> None:
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
