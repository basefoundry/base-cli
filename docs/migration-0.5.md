# Migrating from 0.4.x to 0.5.0

0.5.0 is a pre-1.0 minor release. It retains the Click/Typer lifecycle APIs while
strengthening configuration, output, logging, retention, and release contracts.
The published 0.4.3 tag and distributions remain immutable.

- Convenience-profile project configuration now verifies POSIX ownership and
  permissions, refuses symlink/reparse paths, and stops discovery at `.git`, a
  filesystem boundary, or the configured ancestor limit. Repair permissions or
  set `verify_discovered_config=False` explicitly for a knowingly shared
  workspace; see [local configuration](local-config.md).
- YAML inputs are bounded before alias construction. Split unusually large
  configuration files and remove recursive or excessive alias graphs.
- JSON mode captures descriptor and inherited child stdout. Wait for children
  and flush native stdio before returning. Output over 8 MiB produces an error;
  a detached child produces `capture_incomplete` with the partial captured
  output; use NDJSON for larger streams. See [JSON contracts](json-contracts.md).
- Consumer logging handlers and configured levels survive CLI cleanup. Foreign
  handlers and parent routing are preserved while `--debug` and `--quiet` still
  control the Base-owned stream. An explicitly configured host logger level may
  filter persistent DEBUG messages; a foreign handler without a level does not.
  Configure the host logger at DEBUG when those records are required. See
  [integrations](integrations.md).
- Native Windows enforces bundle retention through pinned directory handles;
  contended maintenance passes skip without blocking command execution.
- Repeated source paths and human log timestamps are cached; log-sidecar I/O
  errors use logging's error path without aborting commands.
- Click 8.5 is supported within the declared Click window. Typer consumers must
  use the documented compatible Click/Typer pairs.

Review the dated [changelog](https://github.com/basefoundry/base-cli/blob/main/CHANGELOG.md) and the [platform boundary](platform-support.md)
when upgrading. Test the installed wheel in a clean environment, including your
JSON consumers, config permissions, and host logging integration.
