# Security release review checklist

Use this checklist for a base-cli release and for any change that adds a
filesystem path, input source, plugin, history field, telemetry attribute, or
concurrency boundary. Each row maps a threat to the control and the regression
tests that should be run or extended.

## Threat-to-control map

| Review area | Questions to answer | Control / evidence |
| --- | --- | --- |
| argv and secrets | Can a new option, argument, environment value, prompt, error, or debug line contain a secret? | Mark parameters sensitive; update the redaction plan; run `tests/test_redaction_security.py`, `tests/test_app_security_boundaries.py`, and `tests/test_invocation_parity.py`. |
| Config and serialization | Are config files regular/readable, parsed as data, schema-validated, and excluded from logs/telemetry? | Validate explicit paths and mappings; add malformed/secret cases; run config, JSON-contract, and output tests. |
| Logs and history | Does any new field cross a persistence callback or change permissions/retention? | Redact before the callback, preserve private modes, document consumer ownership; run history, logging, and run-metadata tests. |
| Filesystem and symlinks | Can a path be replaced, traversed, mounted, symlinked, or cleaned outside the owned run root? | Retain handles/identity, refuse uncertain paths, and preserve primary results; run `tests/test_cleanup_security.py` and `tests/test_adversarial_regressions.py`. |
| Permissions | Does the change create a file or directory under a custom root, on Windows, or on a network/mounted filesystem? | Verify POSIX modes and Windows ACL assumptions; update platform documentation and add a permission regression where practical. |
| Plugins and dependencies | Does an extension load earlier, discover more metadata, or gain new authority? | Keep loading explicit/lazy, preserve allowlists/disable switches, pin and audit dependencies; run `tests/test_extensions.py`, Bandit, and `pip-audit --strict`. |
| Concurrency | Can two threads/processes write, replace, retain, or prune the same artifact? | Use atomic replacement and the existing lock boundary; add a race/adversarial test; run the full concurrency suite. |
| Inherited runs | Can a child finalize, prune, or delete parent state? | Keep inherited bindings read-only/unowned; run inherited startup, metadata, and cleanup tests. |
| Telemetry | Could a new span attribute include argv, config, paths, identifiers, or secrets? | Keep the safe attribute allowlist; test broken/missing exporters and inspect provider configuration; run `tests/test_integrations.py`. |
| Release surface | Does the change alter a public export, schema, warning, or security promise? | Update `docs/api-stability.md`, `SECURITY.md`, changelog, contract tests, and migration guidance as applicable. |

## Required release checks

Run the repository gates appropriate to the change:

```bash
./tests/full_validate.sh
python -m pytest
ruff format --check scripts examples
ruff check lib/python/base_cli scripts examples tests
python -m mypy --strict examples/typed_consumer.py
python scripts/validate_docs.py
bandit -q -r lib/python/base_cli scripts -lll -iii
pip-audit --strict
```

For changes involving runtime ownership, redaction, history, extensions,
telemetry, or concurrency, run the focused suites listed in the map in
addition to the full suite. CI is the final release gate; a local pass does not
override a failing cross-platform check.

## Reviewer sign-off

Before merging, the reviewer should be able to answer “yes” to each question:

- [ ] The changed assets and trust boundaries are named in the threat model.
- [ ] New inputs and persistence destinations have an explicit secret and
      permission decision.
- [ ] Symlink, traversal, replacement, and concurrent-operation behavior is
      fail-closed or covered by a regression test.
- [ ] Plugins and telemetry remain opt-in, bounded, and consumer-auditable.
- [ ] Consumer responsibilities and residual same-account/process risks are
      documented.
- [ ] Security-relevant behavior, public contracts, and release notes agree.
- [ ] Full CI, security scans, and the focused tests are green.
