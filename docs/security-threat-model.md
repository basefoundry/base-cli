# Runtime threat model

This model describes the assets, trust boundaries, controls, and residual
risks for a `base-cli` invocation. It covers the generic framework; a consumer
must extend it for its own commands, configuration schema, plugins, network
clients, and deployment environment.

## Security objectives and assets

The important assets are:

- **credentials and sensitive input:** argv, environment variables, explicit
  configuration values, Click prompt values, and consumer-owned service data;
- **diagnostics and history:** logs, `run.json`, temporary files, cache entries,
  and consumer history records;
- **execution integrity:** command selection, lifecycle state, cleanup
  ownership, exit status, and the parent/child runtime relationship;
- **extension and dependency supply chain:** installed distributions, entry
  points, optional Rich/Telemetry integrations, and Python dependencies; and
- **telemetry and machine contracts:** exported span attributes, JSON records,
  command-protocol frames, and schema meanings.

The primary security goals are to avoid accidental secret disclosure, prevent a
cleanup operation from deleting an unrelated path, preserve an accurate
command result when secondary persistence fails, and make optional integrations
unable to silently broaden the data sent or code executed by the framework.

## Trust boundaries

```text
Shell / OS (argv, env, cwd, identity)
             |
             v
Click parsing -> base-cli lifecycle -> consumer callbacks/profile
       |              |                    |
       v              v                    v
config files     runtime/log/history     plugins and services
                                      |
                                      v
                               telemetry exporter
```

The boundaries are intentionally explicit:

1. The shell and operating system supply untrusted strings and process
   authority. `base-cli` cannot distinguish a secret from an arbitrary value
   unless a parameter is marked sensitive or matches its documented heuristic.
2. Click parsing and the lifecycle transform input into a `Context`; consumer
   callbacks and profile policies remain application code and are not sandboxed.
3. Configuration files, project discovery, custom runtime roots, history
   writers, and service factories cross from consumer policy into framework
   persistence. The generic profile deliberately supplies fewer implicit
   sources than a product profile.
4. Entry-point plugins cross into third-party Python code only when a consumer
   creates discovery and loads a selected extension. Discovery is not a code
   sandbox.
5. Telemetry crosses a process/network boundary only when the consumer opts in
   and supplies a provider/exporter. The framework publishes a bounded safe
   attribute set but cannot secure the exporter's endpoint or credentials.

## Threats, controls, and residual risk

| Threat / asset | Framework controls and tests | Residual risk and consumer action |
| --- | --- | --- |
| Secrets in argv, environment-derived values, config, or prompts leak into logs | Sensitive options/arguments, secret-name heuristics, equals/short-option handling, and redaction before history callbacks; `tests/test_redaction_security.py`, `tests/test_app_security_boundaries.py`, and `tests/test_invocation_parity.py` | A custom secret name or consumer log can still disclose data. Mark domain-specific parameters with `sensitive=True`, do not log `ctx.config`, and review custom formatters/history writers. |
| Logs, history, JSON, or run metadata expose credentials or unbounded attacker text | Redacted history boundary, bounded JSON log messages, owner-only POSIX modes, atomic metadata writes, and JSON contract tests | Consumer-owned paths and history stores may have weaker permissions. Set private ACLs, avoid copying raw logs, and treat retained diagnostics as sensitive. |
| Symlink, traversal, replacement, or mount races redirect cleanup | Exclusive runtime-leaf ownership, retained descriptors, identity checks, no-follow traversal, run-ID containment, and fail-closed cleanup; `tests/test_cleanup_security.py`, `tests/test_app_security_boundaries.py`, and adversarial regression tests | A same-account process with the same filesystem authority can race user-owned paths. Use a private cache root and avoid sharing runtime trees between mutually hostile users. |
| Insecure permissions expose runtime files | POSIX `0600`/`0700` modes; Windows uses inherited user-profile ACLs and warns when secure handle operations are unavailable | A custom Windows cache root or network filesystem may not inherit private ACLs. Consumers must provision and verify permissions. |
| Malicious or accidental config content changes execution | Explicit config paths must be readable regular files; YAML is parsed as data; generic profile has no implicit product files; consumer profiles own schema validation and precedence | The consumer still decides which files, environment variables, and values are trusted. Validate schema, reject unexpected keys, and do not treat config as a secret store. |
| A plugin executes unwanted code or changes command behavior | Lazy discovery, deterministic ordering, duplicate detection, explicit `load`/`load_all`, `allowlist`, and `disabled`; `tests/test_extensions.py` | Loaded extensions have the process's authority and are not sandboxed. Pin and review distributions, disable discovery by default where possible, and use an allowlist. |
| Concurrency corrupts logs, history, metadata, or retention state | Atomic replacement, sidecar/process locks, bounded complete-bundle retention, and concurrency/adversarial tests | The framework cannot make consumer databases or external stores transactional. Use a transactional writer and define recovery for multi-process consumers. |
| Child/inherited runs mutate or delete a parent's artifacts | Inherited runtimes do not own a new bundle, do not finalize parent metadata, and are excluded from cleanup/retention; inherited-run tests cover startup, success, and failure | Parent and child processes still share the user's filesystem authority. Consumers must validate parent provenance and avoid inheriting from untrusted paths. |
| Telemetry leaks argv, config, paths, or secrets | Telemetry is opt-in; only run ID, CLI name, environment, dry-run, outcome, exit code, and duration are attached; missing/broken exporters are no-ops; `tests/test_integrations.py` | A consumer-supplied tracer/exporter can add arbitrary attributes or send to an untrusted endpoint. Review provider configuration, use TLS/authentication, and never attach raw command data. |
| Optional integrations or dependencies broaden the attack surface | Rich, Typer, and OpenTelemetry are optional extras; import/use is lazy; quality CI runs Bandit and pip-audit | A vulnerable or malicious dependency remains a supply-chain risk. Pin/lock deployments, review updates, and install only required extras. |
| Secondary persistence failure hides the real command result or leaves false state | Transactional startup/teardown, terminal outcome finalization, best-effort history, and rollback tests | A crash can still lose diagnostics. Treat logs/history as evidence, not an authorization or billing source, and alert on persistence warnings. |

## Secure defaults

The generic framework defaults to:

- no implicit product configuration files, project discovery, history writer,
  plugin discovery, or telemetry;
- redaction before data reaches persistent logs or consumer history callbacks;
- owner-only runtime files/directories on POSIX and user-profile ACL inheritance
  on Windows;
- fail-closed cleanup that retains uncertain paths and reports a warning;
- bounded, versioned machine contracts rather than arbitrary object
  serialization; and
- best-effort secondary persistence that cannot replace the primary command
  result.

These defaults reduce accidental exposure; they are not a sandbox or encryption
boundary.

## Non-goals

`base-cli` does not provide:

- encryption at rest, a secrets manager, key rotation, or secret redaction from
  arbitrary consumer output;
- isolation from a same-user process, root/administrator, a malicious shell,
  malicious Python package, or a loaded plugin;
- a policy for the consumer's network requests, subprocesses, project files,
  or authorization model; or
- a guarantee that consumer-owned configuration, history, telemetry, or cache
  paths are private when they are outside the framework's managed root.

## Consumer responsibilities

Before shipping a CLI built on the framework, the consumer should:

1. inventory secret-bearing options, positional arguments, environment values,
   config keys, and output fields; mark all non-obvious parameters sensitive;
2. validate configuration schemas and restrict config/cache/log/history roots
   to an appropriate owner or service account;
3. pin and review plugins and optional dependencies, using discovery allowlists
   or disabling discovery when extensions are not required;
4. configure telemetry exporters with TLS, authentication, retention, and an
   endpoint policy, and review every custom span attribute;
5. test the command's own subprocess, network, filesystem, and authorization
   behavior; and
6. run the release/security checklist in [`security-review.md`](security-review.md)
   for every release and whenever a trust boundary changes.
