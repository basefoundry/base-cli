# Migrating from 0.4.x to 0.5.0

0.5.0 is a pre-1.0 minor release. It retains the Click/Typer lifecycle APIs while
strengthening configuration, output, logging, retention, and release contracts.
The published 0.4.3 tag and distributions remain immutable.

- Convenience-profile project configuration now validates POSIX ownership and
  permissions, refuses symlink/reparse paths, and stops discovery at `.git`, a
  filesystem boundary, or the configured ancestor limit. Repair permissions or
  make the shared-workspace opt-out explicit; see [local configuration](local-config.md).
- YAML inputs are bounded before alias construction. Split unusually large
  configuration files and remove recursive or excessive alias graphs.
- JSON mode captures descriptor and inherited child stdout. Wait for children
  and flush native stdio before returning. Output over 8 MiB produces an error;
  use NDJSON for larger streams. See [JSON contracts](json-contracts.md).
- Consumer logging handlers and configured levels survive CLI cleanup. A host
  level may filter persistent DEBUG messages; configure the host logger at DEBUG
  when those are required. See [integrations](integrations.md).
- Native Windows enforces bundle retention through pinned directory handles;
  contended maintenance passes skip without blocking command execution.
- Repeated source paths and human log timestamps are cached; log-sidecar I/O
  errors use logging's error path without aborting commands.
- Click 8.5 is supported within the declared Click window. Typer consumers must
  use the documented compatible Click/Typer pairs.

Review the dated [changelog](../CHANGELOG.md) and the [platform boundary](platform-support.md)
when upgrading. Test the installed wheel in a clean environment, including your
JSON consumers, config permissions, and host logging integration.
