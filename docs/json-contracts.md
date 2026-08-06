# JSON contracts

Machine-facing output is opt-in. Configure a JSON lifecycle option on an app
when a command is intended for scripts or automation:

```python
import base_cli

app = base_cli.App(
    name="example",
    lifecycle_options=base_cli.LifecycleOptions(
        json=base_cli.LifecycleOption("--json"),
    ),
)
```

`example --json` captures command stdout and emits exactly one success or error
envelope on stdout. Logs remain on stderr. Human mode, including the default
Click error rendering and command stdout behavior, is unchanged.

## Output and errors

Both envelopes use `schema_version: 1` and stable fields:

```json
{
  "schema_version": 1,
  "schema": "base-cli.output",
  "code": "ok",
  "type": "success",
  "message": "Success",
  "details": {"exit_code": 0, "stdout": "hello\n"},
  "run_id": "20260804T192202_dd231351"
}
```

Failures use `schema: "base-cli.error"`, `type: "error"`, and a deterministic
`code` derived from the lifecycle outcome (`usage_error`, `click_error`,
`aborted`, `interrupted`, `unexpected_error`, and so on). `details` always
contains the numeric `exit_code` and captured command stdout. A command's
human output is represented as a JSON string, so it cannot introduce prose or
ANSI escapes as a second stdout record.

`run_id` is the lifecycle run identifier when startup reached a runtime
context, otherwise it is `null`. Unexpected failures intentionally expose only
the generic message `Unexpected internal error.`; diagnostics stay in logs.

The lower-level `success_envelope()`, `error_envelope()`, `dumps_envelope()`,
and `redact_json_value()` helpers are public for commands that need to publish
their own structured `details` records. Secret-looking keys (`token`,
`password`, `secret`, `api_key`, and `authorization`) and credential-bearing
URLs are redacted recursively.

## Inspection envelopes

Read-only inspection commands can use the stable inspection helpers when their
result is a diagnostic snapshot rather than a command success or failure. The
public `inspection_envelope()` helper returns a versioned mapping with the
command name, an inspection status, caller-owned data, and an optional error:

```python
import base_cli

payload = base_cli.inspection_envelope(
    command="release check",
    status="ok",
    data={"project": "demo", "version": "0.4.0"},
)
```

The v1 shape is:

```json
{
  "schema_version": 1,
  "command": "release check",
  "status": "ok",
  "data": {"project": "demo", "version": "0.4.0"},
  "error": null
}
```

`status` is one of `ok`, `warn`, or `error`. The `data` mapping is copied into
the envelope; `error` is either `null` or a caller-defined mapping containing
diagnostic details. These envelopes are intended for idempotent, read-only
queries and do not replace the success/error lifecycle envelopes used for
command execution outcomes.

`render_inspection_json()` produces the same envelope as indented JSON with a
trailing newline and no ASCII-only escaping. Use it when the inspection result
is written directly to a machine-readable output stream.

## JSON logs

Pass `json_logs=True` and the run identifier to `configure_logger()` when an
integration needs structured logs without enabling machine output:

```python
logger = base_cli.configure_logger(
    "example",
    log_file,
    debug=True,
    json_logs=True,
    run_id="run-123",
)
```

Each line is a JSON object with `schema_version`, `schema`, `timestamp` (UTC),
`level`, `logger`, `message`, and `run_id`. Messages are redacted and capped at
8 KiB; persistent files retain base-cli's owner-only permissions and JSON mode
bounds default-log retention to the most recent 20 run bundles (or the
explicit `RetentionPolicy` setting). The legacy `max_log_files` option remains
available for compatibility. JSON logs never use terminal color codes.
