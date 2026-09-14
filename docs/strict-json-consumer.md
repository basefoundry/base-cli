# Strict JSON consumer validation

Use a strict cross-language parser when a downstream application consumes
`base-cli` machine output. Python's `json` module is useful for local tooling,
but Node's built-in `JSON.parse` is a closer model for a consumer that must
reject malformed JSON before using the payload.

## Validate one JSON envelope

The repository's success fixture is a representative `base-cli.output` JSON
document. From the repository root, validate its syntax and stable top-level
contract with Node:

```bash
node -e '
const fs = require("node:fs");
const payload = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const required = ["schema_version", "schema", "code", "type", "message", "details", "run_id"];
for (const key of required) {
  if (!(key in payload)) throw new Error(`missing ${key}`);
}
if (payload.schema_version !== 1 || payload.schema !== "base-cli.output") {
  throw new Error("unexpected base-cli output contract");
}
console.log(`${payload.schema} ${payload.code}`);
' tests/fixtures/contracts/output-success.json
```

The expected output is `base-cli.output ok`. `JSON.parse` validates syntax and
the small assertions validate the envelope identity; it does not make
undocumented nested fields stable. Consumers should treat `details.stdout` as
the captured string described by the [JSON contract](json-contracts.md), and
preserve the process exit status and stderr boundary.

## Validate NDJSON one record at a time

NDJSON is a stream of independent records, not one JSON envelope. Parse each
non-empty line separately and reject the stream if any line is malformed:

```bash
node -e '
const fs = require("node:fs");
const lines = fs.readFileSync(process.argv[1], "utf8").split(/\r?\n/).filter(Boolean);
for (const [index, line] of lines.entries()) {
  const record = JSON.parse(line);
  if (record.schema_version !== 1 || record.schema !== "base-cli.record") {
    throw new Error(`unexpected record contract on line ${index + 1}`);
  }
}
console.log(`validated ${lines.length} record(s)`);
' tests/fixtures/contracts/ndjson-record.json
```

The expected output is `validated 1 record(s)`. Do not concatenate NDJSON lines
and pass them to one `JSON.parse` call; consume one record per line and keep
diagnostics on stderr.

## Syntax failure versus contract failure

Consumers should distinguish two failures:

- malformed JSON: `JSON.parse` throws a `SyntaxError`; reject the complete
  payload or record stream and report the producer's exit status;
- valid JSON with an invalid shape: parsing succeeds, but schema validation
  must reject it. The repository's
  [`scripts/validate_contract_fixtures.mjs`](../scripts/validate_contract_fixtures.mjs)
  checks both the valid fixtures and the intentionally invalid
  `tests/fixtures/contracts/invalid-output-extra-field.json` fixture.

Run the repository-owned cross-language contract check with:

```bash
node scripts/validate_contract_fixtures.mjs
```

The schemas and fixtures are the source of truth. Do not add a new parser or
redefine the wire contract in an adopter guide; link the exact contract version
and record the `base-cli` release used for validation. Never place secrets or
private paths in fixtures or public failure reports.
