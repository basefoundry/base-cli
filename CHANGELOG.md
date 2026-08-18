# Changelog

All notable changes to base-cli will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions are tracked in the repo-root `VERSION` file.

## [Unreleased]

### Planned

- Continue compatibility hardening and adoption work for the next release.

### Changed

- Bound the core Click and PyYAML dependency windows, publish the tested
  compatibility matrix, and document the dependency update policy.
- Move PyYAML behind the optional `base-cli[yaml]` extra and provide an
  actionable installation hint when YAML configuration or output is selected.
- Add an explicit `App.async_command()` adapter and `run_async()` helper for
  deterministic async callbacks without changing the synchronous core.
- Add versioned NDJSON output and typed writer protocols for bounded,
  flush-per-record machine output.
- Formalize typed extension callback protocols, entry-point capability metadata,
  and pre-load API-version negotiation.

### Security

- Redact recognized secret keys embedded in query strings, comma-separated
  values, and header-style `key: value` arguments before they reach logs or
  persisted history.

### Fixed

- Isolate malformed third-party entry-point metadata so one invalid extension
  cannot prevent healthy extensions from being discovered.

### Added

- Add a framework choice guide, five-minute evaluation path, and clearer
  production-lifecycle positioning for Click and Typer adopters.
- Add deterministic SPDX SBOMs, artifact checksums, and OIDC-backed GitHub
  attestations to protected release workflows.
- Add a generated public API reference and a strict CI drift check so every
  stable facade export remains searchable and documented.
- Add a permissioned-adopter evidence policy and dated compatibility-run
  artifacts without presenting maintained fixtures as customer adoption.
- Add a permissioned-adopter evidence policy and dated compatibility-run
  artifacts without presenting maintained fixtures as customer adoption.

### Changed

- Improve PyPI description and search keywords to make the framework's
  lifecycle, logging, configuration, and CLI integration surface discoverable.

## [0.4.2] - 2026-08-08

This is a compatible pre-1.0 patch release. It contains correctness fixes,
security hardening, documentation improvements, and adoption polish after
0.4.1; it does not introduce a new API or JSON-contract compatibility
boundary.

### Changed

- Apply the profile-resolved display command to ambient production invocation
  metadata so logs and history match the documented wrapper-label contract.

### Security

- Compact the launcher path in retained invocation logs so diagnostic bundles
  do not disclose the local user's home directory or username.

### Fixed

- Fix the published documentation link to the reference applications.
- Classify a command that returns exit code `130` as `nonzero_return`; the
  `interrupted` outcome remains reserved for actual interrupt exceptions.

### Documentation

- Document the exported history, command-protocol, and context APIs and enforce
  public-symbol docstrings in the API tests.

## [0.4.1] - 2026-08-07

This is a compatible pre-1.0 patch release. It contains adoption polish,
security hardening, correctness fixes, and CI improvements after 0.4.0; it does
not introduce a new API or JSON-contract compatibility boundary.

### Added

- Add the MkDocs documentation site configuration, strict documentation checks,
  and GitHub Pages deployment workflow.
- Add the importable, intentionally empty `base_cli.experimental` namespace for
  future preview APIs without expanding the stable API surface.

### Changed

- Reclassify the project as Beta in package metadata to reflect its documented
  API stability policy, compatibility suite, and production-adoption guidance.
- Add a version, license, install, and release-notes strip to the README and
  validate it against the repository version contract.
- Extend the optional Typer adapter through Typer 0.27.x by selecting the
  command tree's matching public or vendored Click dialect, with a Python 3.10
  through 3.14 compatibility matrix covering Typer 0.25.1, 0.26.0, and 0.27.1.
- Document the intentional opaque `Context.user_config` boundary and the
  compatibility requirements for any future fourth context type parameter.

- Expand CI quality gates with package-level strict mypy coverage, the full Ruff
  format surface, and the updated benchmark percentile calculation.
- Improve the public documentation for inspection envelopes, delegated display
  labels, environment configuration, typed user configuration, Typer access,
  release guidance, and contributor setup.

### Security

- Compact home-relative paths in diagnostic `run.json` and `identity.json`
  metadata so retained support bundles disclose less local path information.

### Fixed

- Capture command output consistently when JSON mode is supplied through Click's
  `default_map` or combined short flags, preserving the single-envelope stdout
  contract.
- Preserve every first-seen document column when rendering heterogeneous records
  instead of silently dropping fields found only in later rows.
- Compute a real interpolated p95 in the runtime benchmark instead of reporting
  the maximum under two names.
- Reject Unicode digit-like input that is not a decimal integer while retaining
  the friendly positive-integer error message.
- Reuse one history display-command resolver across contexts, profiles, and
  history records.
- Correct release-facing examples, changelog structure, API documentation,
  contribution guidance, and package-level formatting coverage.

## [0.4.0] - 2026-08-05

This is a pre-1.0 minor release and therefore a compatibility boundary. See
the API stability policy and migration guide before upgrading from `0.3.x`.

### Migration notes

- Consumer profiles should raise `base_cli.ConfigurationError` for expected,
  user-correctable configuration failures instead of plain `ValueError`.
- Consumers should import `RuntimeLayout` from `base_cli.runtime`; the private
  `_runtime` module is not a compatibility surface.
- `base_cli.testing.invoke()` now exercises the production `run_app()` boundary,
  so tests should assert the same exit status users receive.
- Click-native `--option=value` syntax is accepted and redacted like the
  space-separated form.

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

- Add README health and support badges for CI, downstream consumers, PyPI, and supported Python versions.
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
