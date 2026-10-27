# Architecture

The package lives under `lib/python/base_cli/`. `App` declares or attaches a
consumer command tree; `run_app` enters the framework lifecycle. A Context makes
run state, logging, paths, configuration, and cleanup available to handlers.
Profiles define consumer policy without embedding Base's project conventions in
the generic runtime.

The public facade in `__init__.py` is the supported import boundary. Internal
`_lifecycle`, `_run`, `_runtime`, `_attach`, and cleanup modules coordinate
execution. Public modules and facade exports cover configuration, output
rendering, logging, attachment adapters/contracts, runtime/history, inspection,
testing, and selected JSON-redaction helpers. The implementation-heavy
`redaction` module is not itself part of the stable facade. Optional Typer and
other integrations do not become unconditional core dependencies.

Human-oriented output remains the default. Opt-in versioned JSON contracts
support automation; consumer payload formats remain distinct from a framework
lifecycle envelope. Logging goes to stderr and runtime log files, keeping command
stdout usable as data. Cleanup, error status, and sensitive-value redaction are
cross-cutting contracts, not application business logic.

Sources: [package facade](../lib/python/base_cli/__init__.py),
[profiles](../lib/python/base_cli/profile.py), [JSON contracts](../docs/json-contracts.md),
[testing guide](../docs/testing.md), and
[security model](../docs/security-threat-model.md).
