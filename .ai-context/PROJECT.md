# Base-CLI

`base-cli` is a Python invocation-lifecycle framework for Click and optional
Typer applications. Consumers import `base_cli`; the distribution name is
`base-cli`. It standardizes context, configuration boundaries, logging, cleanup,
runtime paths, structured errors, and automation-facing output.

Click/Typer retain parsing and command definitions. Consumer applications retain
domain services, configuration policy, and project discovery decisions. Base is
one consumer; its manifests, project model, and workspace conventions are not
requirements for generic consumers. The separately released Northstar demo shows
that boundary in an operational CLI.

Use the public facade and documented compatibility promises rather than importing
private implementation modules. Optional integrations remain opt-in. Consult
package metadata, release notes, and the compatibility dashboard for the exact
supported version/dependency matrix; this pack describes the source architecture.

Sources: [README](../README.md), [public API](../docs/api-reference.md),
[API stability](../docs/api-stability.md), and [metadata](../pyproject.toml).
