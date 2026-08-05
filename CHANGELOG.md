# Changelog

All notable changes to base-cli will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions are tracked in the repo-root `VERSION` file.

## [Unreleased]

### Added

- Add the security policy, runtime threat model, and threat-to-control release
  checklist covering secret handling, filesystem ownership, plugins, inherited
  runs, concurrency, and telemetry boundaries.
- Add the public API stability and deprecation policy, migration guide, and
  `base_cli.deprecated()` warning helper with contract-test guardrails.
- Add immutable `LifecycleOptions` and `LifecycleOption` policies for enabling,
  disabling, renaming, and configuring each standard option independently, with
  normalized `LifecycleValues` stored in namespaced Click metadata without
  replacing application-owned `Context.obj` state.
- Add `App.attach()` and `base_cli.attach()` for applying one lifecycle to
  existing nested, aliased, chained, and lazy Click command trees while
  preserving their native callbacks, contexts, and result values.
- Add public `sensitive_parameters` attachment policy for existing and lazy
  Click parameters with domain-specific secret names.
- Add `ConfigurationError` so consumer profiles can explicitly mark
  user-correctable configuration messages as safe usage errors.
- Add public typed runtime/profile/context contracts, generic attachment
  factories, isolated command-schema registries/codecs, and a strict consumer
  typing example.
- Add opt-in `CliProfile.batteries_included()` layered configuration with
  platform-aware user paths, project and environment files, provenance, and
  validated framework settings.
- Add the optional `base-cli[typer]` integration with `attach_typer()`,
  `TyperAdapter`, and `get_typer_command()` so Typer command trees can adopt
  the same lifecycle without making Typer a core dependency.
- Add opt-in versioned JSON success/error envelopes, redacted bounded JSON
  logs, and public contract helpers for machine-facing integrations.

### Changed

- Normalize command returns, Click errors, aborts, interrupts, `SystemExit`, and
  unexpected exceptions through one core outcome model and clean `run_app()`
  process boundary.
- Treat plain profile-callback exceptions as private internal failures. Profiles
  that used `ValueError` for expected configuration problems should raise
  `ConfigurationError` instead.
- Route `base_cli.testing.invoke()` through the production `run_app()` boundary
  and add a keyword-only `reraise_unexpected` opt-in for tests that need the
  original exception.
- Reject native async callbacks explicitly and preserve Click command subtypes
  through typed `attach()` decorators and adapters.

### Fixed

- Reject missing, unreadable, and non-regular explicit `--config` paths before
  profile or runtime startup while preserving optional profile-owned files.
- Make `App.name` authoritative for single-command identity, reject duplicate
  or post-materialization registrations deterministically, stabilize inferred
  names across Click releases, and make module-level `@command()` functions
  retrievable and directly runnable through `run_app()`.
- Redact sensitive option values across every declared alias and Click value
  form, redact sensitive positional arguments, and protect conventional secret
  parameter names automatically before argv reaches logs or history writers.
- Claim invocation temp leaves exclusively and refuse recursive cleanup unless
  ownership, strict run-root containment, the run-ID marker, and a symlink-free
  path can all be proven; content erasure uses a retained directory handle and
  intentionally leaves the empty directory skeleton instead of reopening a
  pathname-removal race.
- Finalize core-owned run metadata for successful, failed, aborted, interrupted,
  and unexpected command outcomes without letting secondary persistence
  failures replace the command result.
- Roll back partially constructed command contexts without leaking handlers,
  temporary directories, or incomplete run bundles.
- Preserve exception tracebacks in persistent logs and show them on stderr only
  when debug output is enabled.
- Make history persistence best-effort so secondary failures cannot mask the
  command outcome or skip cleanup, context reset, and logger shutdown.
- Allow finished history records to omit `log_path` when file logging is
  disabled.
- Restore Click-native `--option=value` parsing, including redaction of
  sensitive equals-form values.

## [0.3.0] - 2026-08-01

### Changed

- Keep private runtime files and directories owner-only on POSIX, use inherited
  user-profile ACLs on Windows, and make history appends binary-safe across
  locking backends.
- Make terminal detection tolerate closed streams and record `COMSPEC` when
  Windows has no `SHELL` environment variable.
- Add Linux distribution and WSL2 validation guidance for the generic package.
- Document the Linux, WSL2, macOS, and native Windows support tiers and add
  matching operating-system package classifiers.

## [0.2.0] - 2026-08-01

### Changed

- Select platform-aware cache roots (`XDG_CACHE_HOME`, macOS Caches, and
  Windows `LOCALAPPDATA`) and normalize home-relative paths across separators.
- Make `base_cli.App()` use the consumer-neutral profile by default.
- Move manifest discovery, implicit configuration, owner-aware runtime layout,
  and history persistence out of the generic package. Consumers now provide
  those policies through an explicit `CliProfile`.
- Make `Context.user_config` an opaque consumer-owned value and remove the
  Base-shaped `UserConfig` types from the public package facade.
- Make command-filter normalization consumer-neutral by default. Consumers
  can provide a normalizer callback for legacy prefixes or aliases.
- Remove the Base-branded logger formatter and IDE schema parser from the
  standalone package; those consumer-specific concerns now belong to adapters.
- Make command protocol schemas consumer-owned. The generic protocol now ships
  only framing and validation, with `COMMAND_PROTOCOL_V1` as its default
  header; consumers can register schemas and preserve a legacy header through
  the protocol helper's `protocol_header` argument.

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
