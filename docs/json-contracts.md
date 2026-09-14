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

Capture is activated only when JSON mode is selected, including an environment
variable or Click `default_map`. Human and NDJSON invocations write directly to
the caller's stdout, preserving progress visibility and flush behavior. JSON
capture allows at most 8 MiB of UTF-8 command stdout. It keeps the first 1 MiB
in memory and rolls the remainder to a temporary file, so both temporary-disk
use and finalization memory remain bounded. The temporary file is removed when
the invocation ends.

If a command exceeds the limit, base-cli emits one `base-cli.error` envelope
with `code: "capture_limit"` and exit code `1`; it never silently truncates
the captured text. Use the NDJSON contract for larger record sets.

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
`capture_limit`, `aborted`, `interrupted`, `unexpected_error`, and so on). `details` always
contains the numeric `exit_code` and captured command stdout. A command's
human output is represented as a JSON string, so it cannot introduce prose or
ANSI escapes as a second stdout record.

`run_id` is the lifecycle run identifier when startup reached a runtime
context, otherwise it is `null`. Unexpected failures intentionally expose only
the generic message `Unexpected internal error.`; diagnostics stay in logs.

For large or long-running record sets, use the `ndjson` output contract in
[`output-contracts.md`](output-contracts.md). NDJSON is intentionally a stream
of versioned records rather than a single success/error envelope; command
errors and diagnostics still use the normal stderr and exit-code boundary.

The lower-level `success_envelope()`, `error_envelope()`, `dumps_envelope()`,
and `redact_json_value()` helpers are public for commands that need to publish
their own structured `details` records. Secret-looking keys (`token`,
`password`, `secret`, `api_key`, and `authorization`) and credential-bearing
URLs are redacted recursively.

Golden payloads for each public contract live in
[`tests/fixtures/contracts`](https://github.com/basefoundry/base-cli/tree/main/tests/fixtures/contracts).
CI validates them against the packaged schemas with both a Python validator and
a dependency-free Node.js reader; consumers can use the same fixtures as
cross-language conformance tests.


## Strict JSON consumer validation with Node

Base-cli's JSON output is intended to be consumed by strict parsers such as Node's `JSON.parse`.
The following Node script validates that a base-cli JSON envelope (success or error) is valid JSON and conforms to the expected schema:

```javascript
const fs = require('fs');

// Read the JSON output from a file (or stdin)
const jsonOutput = fs.readFileSync(0, 'utf8');

try {
  const parsed = JSON.parse(jsonOutput);
  // Validate the envelope structure
  if (parsed.schema_version !== 1) {
    throw new Error('Unsupported schema_version');
  }
  if (!['base-cli.output', 'base-cli.error'].includes(parsed.schema)) {
    throw new Error('Unexpected schema');
  }
  if (typeof parsed.code !== 'string') {
    throw new Error('Missing or invalid code');
  }
  console.log('Valid base-cli JSON envelope');
} catch (err) {
  console.error('Invalid base-cli JSON envelope:', err.message);
  process.exit(1);
}
```

For NDJSON output (e.g., from `--format ndjson`), each line is a separate JSON object.
Validate each line individually:

```javascript
const fs = require('fs');
const readline = require('readline');

const rl = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});

rl.on('line', (line) => {
  try {
    const parsed = JSON.parse(line);
    if (parsed.schema_version !== 1) {
      throw new Error('Unsupported schema_version');
    }
    if (parsed.schema !== 'base-cli.record') {
      throw new Error('Unexpected schema for NDJSON record');
    }
    // Optionally validate the record shape
  } catch (err) {
    console.error('Invalid NDJSON line:', err.message);
    process.exit(1);
  }
});
```

### Failure shape

When base-cli emits an error envelope (e.g., due to invalid usage or an internal error), the JSON will have:
- `schema: "base-cli.error"`
- `type: "error"`
- `code`: a string indicating the error type (e.g., `usage_error`, `unexpected_error`)
- `details`: an object containing `exit_code` and the captured stdout (if any)

Example error envelope for a missing required argument:

```json
{
  "schema_version": 1,
  "schema": "base-cli.error",
  "code": "usage_error",
  "type": "error",
  "message": "Missing argument 'NAME'...",
  "details": {
    "exit_code": 2,
    "stdout": ""
  },
  "run_id": null
}
```

### Contract fixtures and validator

Golden payloads for each public contract live in
[`tests/fixtures/contracts`](https://github.com/basefoundry/base-cli/tree/main/tests/fixtures/contracts).
The CI validates them against the packaged schemas with both a Python validator and a dependency-free Node.js reader.
Consumers can use the same fixtures as cross-language conformance tests.

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
