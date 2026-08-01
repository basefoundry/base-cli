# Changelog

All notable changes to base-cli will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions are tracked in the repo-root `VERSION` file.

## [Unreleased]

### Changed

- Make `base_cli.App()` use the consumer-neutral profile by default.
- Move manifest discovery, implicit configuration, owner-aware runtime layout,
  and history persistence out of the generic package. Consumers now provide
  those policies through an explicit `CliProfile`.
- Make `Context.user_config` an opaque consumer-owned value and remove the
  Base-shaped `UserConfig` types from the public package facade.

### Migration notes

- The removed implicit Base profile and Base path/config/history helpers are no
  longer available from `base_cli`. Existing Base integrations should use the
  adapter modules in the Base repository and pass `base_cli_profile()` to
  `base_cli.App`.
- `base_cli.history.write_history_record()` and
  `base_cli.history.write_primary_record()` now require a consumer-selected
  history path; they never choose an application cache location themselves.
- Consumers that need a workspace root should provide the optional
  `CliProfile.resolve_workspace_root` projection; the generic lifecycle no
  longer reads fields from a prescribed user-configuration schema.

### Added

- Initialized the repository with the Base-managed repo baseline.
- Added the guarded package build, artifact validation, and protected
  TestPyPI/PyPI publication workflow.
- Exposed `base_cli.__version__` from the repository and installed package
  version contract.
- Pinned the build backend to metadata compatible with the bundled publication
  action and made license-file validation portable across setuptools versions.
