# Adopter readiness

This guide is the handoff contract for a team evaluating `base-cli` for a
production Python CLI. It is intentionally consumer-neutral: the framework
owns invocation lifecycle, contracts, and safety defaults while the adopter
owns product configuration, commands, services, and release policy.

## Readiness checklist

Before the first production pilot, the adopter should be able to check every
box below:

- [ ] Pin a supported `base-cli` minor release (for example, `~=0.4.0`) and
  record Click, PyYAML, and any optional integration versions in a lock file.
- [ ] Run the adopter's command suite on CPython 3.10--3.14 on every platform
  the product supports; retain at least one installed-wheel smoke job.
- [ ] Use only the documented `base_cli` facade and module `__all__` exports;
  fail CI on private imports and deprecation warnings.
- [ ] Choose a `CliProfile` for project/configuration policy and document which
  files, environment variables, and credentials are trusted.
- [ ] Mark domain-specific secrets as sensitive, review redacted logs, and
  verify runtime/cache permissions in the deployment image.
- [ ] Select human or machine output intentionally. Machine consumers must
  pin the JSON/record schema and test error envelopes as well as success.
- [ ] Exercise `--debug`, `--quiet`, `--keep-temp`, `--log-file`, and any
  configured `--dry-run`/`--json` options in support runbooks.
- [ ] Define a rollback path: the previous wheel, configuration schema, and
  output contract remain available for the documented compatibility window.
- [ ] Publish an owner, escalation path, and a redacted support bundle format.

The maintainable downstream fixtures in
[`compatibility/consumers`](../compatibility/README.md) are the executable
version of this checklist.

## Migration path

1. **Inventory the current boundary.** Record the command name, Click/Typer
   version, options, exit codes, config sources, log locations, and machine
   output consumed by automation.
2. **Pin and install the framework.** Add `base-cli` to the application lock
   file and run the wheel-first smoke test before changing command behavior.
3. **Adopt the smallest lifecycle boundary.** Use `base_cli.App` for a new
   command, `base_cli.attach()` for an existing Click tree, or
   `base_cli.attach_typer()` for an existing Typer tree. Keep product callbacks
   and dependency injection in the consumer.
4. **Move policy into a profile.** Implement workspace discovery, config
   precedence, runtime ownership, and history formatting in a consumer-owned
   `CliProfile`; do not add product assumptions to generic framework code.
5. **Make contracts explicit.** Add JSON/record fixtures for successful,
   usage-error, and unexpected-error paths. Preserve legacy framing during the
   migration window and release a schema adapter when a wire contract changes.
6. **Harden operations.** Mark secrets, inspect permissions, configure log
   retention, and run the adopter's platform matrix with `--debug` and
   `--dry-run` where appropriate.
7. **Roll out progressively.** Pilot one command, compare output/log/run
   metadata with the inventory, then migrate the remaining commands. Keep the
   previous wheel available until the rollback check is complete.

For public API and deprecation rules, see [`api-stability.md`](api-stability.md)
and [`migrations.md`](migrations.md). The four framework reference applications
show copy-pasteable packaging patterns in [`examples/README.md`](../examples/README.md).

## Support channel

Use the repository's [Adoption support issue template](../.github/ISSUE_TEMPLATE/support.md)
for migration questions, compatibility failures, and redacted reproductions.
Security reports must follow [`SECURITY.md`](../SECURITY.md), not a public issue.
Include the framework version, Python/platform, installed dependency versions,
command shape (with secrets removed), exit code, and a support bundle path.
Maintainers triage adoption issues during normal project work and link any
release-blocking finding back to the compatibility register.

## Evidence and downstream compatibility

The repository does not claim a customer identity without permission. Until an
external team authorizes a public case study, three independent consumer
fixtures provide the reviewable evidence:

- **Atlas** — a Click inventory command migrated without rebuilding its tree;
- **Beacon** — a typed Typer deployment command using the optional adapter; and
- **Cinder** — a scheduled reconciliation command with dry-run and JSON output.

Each fixture has its own package metadata and tests, is installed against the
published framework wheel in CI, and records a stable invocation outcome. A
permissioned adopter can replace a fixture with a public case study without
changing the compatibility test contract.

## Adoption friction and release gate

The friction register is deliberately linked to the issues that delivered each
guardrail:

| Friction found during onboarding | Linked issue | Disposition |
| --- | --- | --- |
| Existing nested/lazy Click trees need lifecycle attachment | [#57](https://github.com/basefoundry/base-cli/issues/57) | Resolved; Atlas fixture remains a regression check. |
| Typed consumers need a supported adapter and version boundary | [#61](https://github.com/basefoundry/base-cli/issues/61) | Resolved; Beacon exercises the public and vendored Click boundaries across the supported Typer matrix. |
| Automation needs a stable machine contract | [#63](https://github.com/basefoundry/base-cli/issues/63) | Resolved; Cinder asserts JSON output and error behavior. |
| Teams need repeatable wheel, platform, and downstream checks | [#67](https://github.com/basefoundry/base-cli/issues/67), [#68](https://github.com/basefoundry/base-cli/issues/68) | Resolved; compatibility workflow is retained. |
| Adoption requires explicit API, migration, and security expectations | [#69](https://github.com/basefoundry/base-cli/issues/69), [#70](https://github.com/basefoundry/base-cli/issues/70) | Resolved; this checklist links the published policies. |
| Teams need copy-pasteable production examples | [#71](https://github.com/basefoundry/base-cli/issues/71) | Resolved; four installable examples remain in CI. |

There are no unresolved release-blocking findings in the three fixture
baseline. The accepted residuals are the documented Typer `<0.28` support
window, consumer-owned configuration/schema policy, and the absence of a
permissioned public customer case study. Any new blocker must be filed as a
linked issue before release and either fixed or explicitly accepted in the
release notes.
