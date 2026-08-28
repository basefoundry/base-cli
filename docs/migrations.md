# Migration guide

This page is the starting point for adopting `base-cli` in an existing Python
CLI. It collects framework-specific recipes as well as the format for changes
that affect the public `base-cli` contract. The compatibility rules and
deprecation timeline are defined in [`api-stability.md`](api-stability.md).

## Choose a migration path

- [Click](migration-click.md) — keep an existing Click command tree and add a
  shared lifecycle boundary.
- [Typer](migration-typer.md) — keep typed Typer declarations and attach the
  generated command to the lifecycle boundary.
- [Cement](migration-cement.md) — keep Cement while evaluating the boundary,
  then move command definitions incrementally to Click plus `base-cli`.
- [argparse](migration-argparse.md) — keep parser behavior stable while
  replacing application-owned lifecycle code one command at a time.

The [framework choice guide](framework-choice.md) explains the boundary and
the [adopter readiness guide](adopter-readiness.md) is the production handoff
checklist. These recipes assume a pinned `base-cli` release and a wheel-first
smoke test before changing a user's command behavior.

## Common rollout and rollback checklist

1. Inventory command names, options, exit codes, configuration precedence,
   output consumed by automation, and log/diagnostic locations.
2. Add `base-cli` to the lock file and preserve the existing parser and
   callback tests. Start with one low-risk command.
3. Attach the existing tree (or add a small `App`) and move policy into a
   consumer-owned `CliProfile`. Mark secrets as sensitive.
4. Add success, usage-error, and unexpected-error contract fixtures. Keep
   stdout reserved for command output and diagnostics on stderr.
5. Run the full platform/dependency matrix, then compare old and new output
   and retained run metadata in a pilot environment.
6. Keep the previous wheel, configuration schema, and output contract
   available for the documented compatibility window. If the pilot fails,
   restore the previous entry point and wheel, and retain the evidence for a
   follow-up issue.

`base-cli` owns invocation lifecycle, context, logging, redaction, runtime
state, cleanup, history hooks, and versioned output contracts. The parser
(Click, Typer, Cement, or `argparse`) remains responsible for command trees,
parameters, help, completion, and parser-specific errors. Product callbacks,
services, configuration policy, and domain schemas remain consumer-owned.

## Migrating a deprecated API

1. Upgrade to the first release that emits the warning.
2. Replace the old symbol with the alternative named in the warning and in the
   release notes.
3. Run tests with `BaseCliDeprecationWarning` enabled so no old call sites are
   missed:

   ```python
   import warnings

   from base_cli import BaseCliDeprecationWarning

   warnings.simplefilter("error", BaseCliDeprecationWarning)
   ```

4. Remove temporary compatibility shims before the stated removal release.

Warnings are ordinary Python warnings, so applications can instead record or
display them with their normal `warnings` configuration.

## Schema migrations

Versioned JSON envelopes and command framing must not change meaning in place.
Add a new schema or protocol version, keep the old version during its support
window, and provide an adapter when practical. A schema migration note should
include:

- old and new version identifiers;
- field additions, removals, and type changes;
- producer and consumer rollout order; and
- the release where the old version will stop being accepted.

## Migration note template

```markdown
### `old_name` → `new_name`

- **First warning:** 0.x.y
- **Removal target:** 1.0.0
- **Why:** explain the problem in one sentence.
- **Action:** show the smallest before/after example.
- **Compatibility:** describe the temporary adapter or schema version.
```

For release mechanics, see [`releasing.md`](releasing.md). For the public
surface and deprecation requirements, see [`api-stability.md`](api-stability.md).
