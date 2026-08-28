#!/usr/bin/env node
/** Validate golden contract fixtures with a non-Python reference reader. */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const schemaDir = path.join(root, "lib", "python", "base_cli", "schemas", "v1");
const fixtureDir = path.join(root, "tests", "fixtures", "contracts");
const fixtures = new Map([
  ["output-success.json", "output.schema.json"],
  ["error-usage.json", "error.schema.json"],
  ["inspection-warn.json", "inspection.schema.json"],
  ["log-record.json", "log.schema.json"],
  ["ndjson-record.json", "ndjson.schema.json"],
  ["command-protocol.json", "command-protocol.schema.json"],
]);

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function validate(schema, value, label) {
  if (schema.type === "object" && (value === null || Array.isArray(value) || typeof value !== "object")) {
    throw new Error(`${label} must be an object`);
  }
  for (const field of schema.required ?? []) {
    if (!Object.prototype.hasOwnProperty.call(value, field)) throw new Error(`${label} is missing ${field}`);
  }
  if (schema.additionalProperties === false) {
    for (const field of Object.keys(value)) {
      if (!Object.prototype.hasOwnProperty.call(schema.properties ?? {}, field)) throw new Error(`${label} has unexpected field ${field}`);
    }
  }
  for (const [field, rule] of Object.entries(schema.properties ?? {})) {
    if (!Object.prototype.hasOwnProperty.call(value, field)) continue;
    if (Object.prototype.hasOwnProperty.call(rule, "const") && value[field] !== rule.const) throw new Error(`${label}.${field} constant mismatch`);
    if (rule.enum && !rule.enum.includes(value[field])) throw new Error(`${label}.${field} enum mismatch`);
  }
}

for (const [fixtureName, schemaName] of fixtures) {
  validate(readJson(path.join(schemaDir, schemaName)), readJson(path.join(fixtureDir, fixtureName)), fixtureName);
  console.log(`Validated ${fixtureName} against ${schemaName}`);
}

let rejected = false;
try {
  validate(readJson(path.join(schemaDir, "output.schema.json")), readJson(path.join(fixtureDir, "invalid-output-extra-field.json")), "invalid fixture");
} catch {
  rejected = true;
}
if (!rejected) throw new Error("invalid fixture was accepted");
console.log("Rejected invalid-output-extra-field.json as expected");
