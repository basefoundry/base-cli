# Migration guide

This page collects the format for changes that affect the public `base-cli`
contract. The compatibility rules and deprecation timeline are defined in
[`api-stability.md`](api-stability.md).

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
