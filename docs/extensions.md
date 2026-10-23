# Entry-point extensions

`base_cli.ExtensionDiscovery` provides an optional, lazy discovery boundary for
framework extensions. The core package only reads package metadata when a
consumer creates a discovery instance; third-party code is imported only when
an extension is explicitly loaded.

The supported entry-point groups are:

| Group | Contract | Purpose |
| --- | --- | --- |
| `base_cli.commands` | A callable command registrar | Add commands to a consumer-owned `App` or Click tree |
| `base_cli.profiles` | A callable profile factory | Supply a `CliProfile` for a named consumer |
| `base_cli.plugins` | A callable plugin installer | Register a coordinated extension with a consumer |

For example, a package can publish:

```toml
[project.entry-points."base_cli.commands"]
audit = "acme_cli.audit:register"

[project.entry-points."base_cli.profiles"]
acme = "acme_cli.profile:build_profile"

[project.entry-points."base_cli.plugins"]
telemetry = "acme_cli.telemetry:install"
```

The loaded callable receives the arguments documented by the consuming
application. `base-cli` intentionally discovers metadata without imposing a
single command-tree or profile-construction shape; this keeps Click, Typer,
and consumer-owned composition boundaries independent.

## Typed SDK and compatibility

The supported callable protocols are exported as `CommandExtension`,
`ProfileExtension`, and `PluginExtension`. They are intentionally small:
consumers own the concrete `App`, `CliProfile`, and service composition types,
while plugin packages type-check against a stable callback boundary.

Declare the SDK version and capabilities in the entry-point extras. The
`base-cli-api-v1` extra selects the current protocol version; additional
`base-cli-cap-<name>` extras advertise optional behavior without importing the
plugin:

```toml
[project.entry-points."base_cli.plugins"]
telemetry = "acme_cli.telemetry:install [base-cli-api-v1, base-cli-cap-tracing]"
```

`ExtensionDescriptor.api_version` and `.capabilities` expose this metadata.
`ExtensionDiscovery` rejects an unsupported API version before loading the
entry point and reports the supported versions in the error. Capabilities are
currently **informational inventory metadata**: they do not grant or restrict
permissions and do not sandbox an in-process Python extension. A consumer that
needs a capability policy must inspect the descriptor and make that decision
before calling `load()`.

This is a negotiation boundary, not an installation-order heuristic; the
consumer can select another plugin release or widen its
`supported_api_versions` policy after running its compatibility suite.

An entry point must declare at most one `base-cli-api-vN` extra. Discovery
skips an entry point with malformed metadata on a per-entry-point basis, so a
bad third-party package cannot prevent healthy commands, profiles, or plugins
from being listed and loaded.

## Determinism and safety

Descriptors are ordered by group, entry-point name, distribution, version, and
target value. Duplicate names are an error; installation order is never an
implicit precedence rule. Each descriptor retains distribution/version/extras
metadata for diagnostics and policy decisions.

Discovery is cached per `ExtensionDiscovery` instance. Call `refresh()` after a
runtime environment change. `load_all()` isolates broken third-party imports
and returns an `ExtensionLoadResult` for every descriptor so one broken plugin
does not hide healthy extensions.

Use `allowlist={"base_cli.commands:audit"}` to restrict one entry point, or
`allowlist={"dist:acme-cli"}` to trust all entry points from one distribution.
Distribution allowlist values use the exact installed metadata spelling,
including case; base-cli does not silently normalize case, hyphens, or
underscores for a security decision.
`ExtensionDiscovery(disabled=True)` disables discovery entirely. Bare entry
point names and unqualified distribution names are rejected because they can
silently trust an unintended group or distribution.

Loading uses the exact entry-point source and distribution identity approved by
discovery rather than re-selecting an entry point by a potentially colliding
name/value tuple. If that identity changes before loading, the load fails
closed. An allowlist is a selection boundary, not a sandbox for installed
Python code.
