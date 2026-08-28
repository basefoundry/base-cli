# JSON Schema artifacts

Versioned JSON Schema artifacts make the machine contracts usable from any
language. The same files are included in the `base-cli` wheel under
`base_cli/schemas/v1/` and are published here for direct browser or HTTP
consumption:

| Contract | Schema |
| --- | --- |
| Success envelope | [`output.schema.json`](schemas/v1/output.schema.json) |
| Error envelope | [`error.schema.json`](schemas/v1/error.schema.json) |
| Inspection envelope | [`inspection.schema.json`](schemas/v1/inspection.schema.json) |
| JSON log record | [`log.schema.json`](schemas/v1/log.schema.json) |
| NDJSON record | [`ndjson.schema.json`](schemas/v1/ndjson.schema.json) |
| Decoded command protocol | [`command-protocol.schema.json`](schemas/v1/command-protocol.schema.json) |

All artifacts use JSON Schema draft 2020-12 and carry a stable `$id` under the
`/schemas/v1/` URL prefix. A schema version is a compatibility boundary: an
additive change can remain in v1, while a change to required fields or field
meaning requires a new version and migration note.

The command-protocol artifact describes the decoded representation of a
`COMMAND_PROTOCOL_V1` frame. The wire framing itself remains line-oriented and
is validated by the protocol codec documented in
[`json-contracts.md`](json-contracts.md).

Consumers can load a packaged schema without importing base-cli:

```python
from importlib.resources import files

schema_text = files("base_cli").joinpath("schemas/v1/output.schema.json").read_text()
```
