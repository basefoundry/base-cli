# API stability and deprecation policy

This document is the compatibility contract for `base-cli`. It applies to the
current pre-1.0 releases and will be updated before the 1.0 release. The
repository's contract tests and release checklist are expected to change with
this document, so a policy change is itself a reviewed, changelogged change.

## Public surface

The supported Python facade is `import base_cli`. The names in
`base_cli.__all__` are the public facade; documented names in the explicitly
exported modules (`base_cli.command_protocol`, `base_cli.json_contracts`, and
the other modules listed in their module-level `__all__`) are public as well.
Names beginning with `_`, modules not documented here, and implementation
details are private and may change without notice.

The following machine-facing contracts are public and versioned:

- command framing defaults to `COMMAND_PROTOCOL_V1`; record schemas are owned
  and registered by the consumer;
- JSON output, error, and log records use `schema_version: 1` and the schema
  identifiers documented in [`json-contracts.md`](json-contracts.md); and
- the exported callable and type signatures exercised by the public API tests.

Human-readable messages, table layout, log wording, temporary directory names,
and history implementation details are not stable machine interfaces unless a
separate contract document says otherwise. Consumers that need automation
should select the JSON or record protocol contracts.

`base_cli.experimental` is reserved for preview APIs. No experimental symbols
are currently shipped. A future preview must live under that namespace, be
labelled experimental in its documentation, and must not be re-exported from
the stable facade until it is promoted.

## Versioning and compatibility

Starting with 1.0, `base-cli` follows Semantic Versioning:

- a **MAJOR** release may remove or change public APIs and versioned contracts;
- a **MINOR** release adds backwards-compatible functionality and may begin a
  deprecation; and
- a **PATCH** release contains compatible fixes, security fixes, and docs.

Until 1.0, the leading zero is meaningful: patch releases remain compatible,
while a minor release is a compatibility boundary and may contain a breaking
change. We still prefer additive changes, and any pre-1.0 break must include a
warning where practical, a migration path, and a changelog entry. Consumers
that need a frozen API should pin a minor release (for example, `~=0.3.0`).

The core package requires Python `>=3.10` and currently tests CPython 3.10
through 3.14 on Linux, macOS, and Windows. Core runtime dependencies are
Click `>=8.1` and PyYAML `>=6.0`. Optional integrations are independently
versioned and constrained in `pyproject.toml`: Typer `>=0.12,<0.26`, Rich
`>=13.7,<15`, and OpenTelemetry API `>=1.24,<2`. The lower bounds are the
minimum supported versions; a dependency major release is supported after it
passes the compatibility suite. A future minor release may drop an end-of-life
Python or dependency window with a migration note.

Platform tier details and the operating-system support test matrix are kept in
[`platform-support.md`](platform-support.md).

## Deprecation process

Use the public `base_cli.deprecated` decorator for callable APIs:

```python
from base_cli import deprecated


@deprecated("0.3", remove="1.0", alternative="new_name")
def old_name(value: str) -> str:
    return new_name(value)
```

The decorator preserves the callable's metadata and behavior, and emits a
`BaseCliDeprecationWarning` with `stacklevel=2` on every call. Applications can
show or fail on these warnings with the standard `warnings` filters. The
warning identifies the release that introduced the deprecation, the planned
removal release, and (when available) the replacement API.

Every deprecation must:

1. remain supported for at least **two minor releases and 90 calendar days,
   whichever is longer**;
2. include a migration note in [`migrations.md`](migrations.md) or the relevant
   contract document;
3. appear in `CHANGELOG.md` under the release that introduces the warning and
   the release that removes the API; and
4. be removed only in the stated removal release (or a later release), except
   for an urgent security or legal fix that is explicitly documented.

The removal PR must delete the contract test for the old symbol only after the
replacement and migration guidance are present. A deprecation is not complete
until the warning, docs, tests, and changelog agree.

## Contract guardrails

`tests/test_public_api.py` verifies that every facade export is resolvable,
that module `__all__` surfaces do not silently drift, and that private legacy
names stay absent. `tests/test_api_stability.py` additionally verifies the
warning behavior, the default command-protocol header, and the JSON v1 schema
identifiers and envelope fields. Changes to an exported symbol or a versioned
schema therefore require an intentional test and documentation update before
release.

When a schema must evolve incompatibly, add a new version and an adapter rather
than changing the meaning of an existing field in place. Keep the old version
available for the same deprecation window and document the migration.

## Release checklist

Before publishing a release, maintainers should confirm:

- the supported Python and dependency windows still match CI and `pyproject.toml`;
- public exports and schema constants have contract-test coverage;
- each deprecation has a warning, removal target, migration note, and changelog
  entry; and
- the release notes call out any pre-1.0 compatibility boundary or contract
  version addition.

See [`releasing.md`](releasing.md) for the mechanical package and publishing
steps.
