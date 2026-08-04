# `base-cli`

`base-cli` is the PyPI distribution; import it in Python as `base_cli`.

Install it with:

```bash
python -m pip install base-cli
```

Release builds, TestPyPI rehearsals, and protected PyPI publication are
documented in [`docs/releasing.md`](docs/releasing.md). The package exposes
`base_cli.__version__`, which matches the distribution version.

The package is distributed under the Apache License 2.0. Base itself remains
licensed separately under AGPL-3.0-or-later.

`base_cli` is a small Python framework for writing command-line tools with
a consistent lifecycle. It is designed to be embedded by applications rather
than to define an application's project model. Base is one consumer of the
library, not part of its generic contract.

It is intentionally thin. Click still owns argument parsing and command
execution, while `base_cli` provides reusable lifecycle behavior:

- standard command options such as `--debug`, `--quiet`, `--environment`,
  `--config`, `--keep-temp`, and `--log-file`
- structured logging to stderr and, by default, to a persistent per-run log file
- optional project discovery and configuration policies supplied by the consumer
- per-run temp directories, persistent cache directories, and cleanup hooks
- sensitive argument redaction in debug invocation logs
- a command context object shared by command code and helper functions
- test helpers built on Click's `CliRunner`

## Design Goals

CLI tools should be easy to write, but not magical. A command should be
explicitly registered, receive an explicit `Context`, and use standard Python
functions instead of import-time side effects.

The package follows these rules:

- **Decorator-driven setup**: commands opt in by creating an `App` and
  decorating a function.
- **Logs go to stderr**: user-facing program output can stay on stdout, while
  logs remain redirectable and skippable.
- **Every run has a context**: logs, paths, configuration, environment, and
  cleanup are available through one object. Project metadata is available when
  the selected consumer profile supplies it.
- **No import-time filesystem writes**: state directories are created only when
  a command runs.
- **Consumer-neutral, Click-compatible**: command authors keep using familiar
  Click concepts such as options and arguments.

## Consumer Profiles

`App` accepts a `CliProfile` that supplies the policies which vary
between applications: project discovery, configuration, runtime placement, and
optional history persistence.

Standalone consumers use the generic profile by default. It can also be passed
explicitly when making the policy boundary visible:

```python
app = base_cli.App(
    name="hello",
    version="0.1.0",
    profile=base_cli.CliProfile.generic(),
)
```

The generic profile has no manifest filename convention, no product-owned
configuration directory, and no implicit history writer. Applications can
provide those policies through callbacks or build their own profile. The
consumer-owned adapters should supply any product-specific policies. See
[`docs/consumer-profiles.md`](docs/consumer-profiles.md) for the boundary and
migration guidance.

### Typed extension contracts

The public profile contract includes `ProjectDiscovery`, `ConfigLoader`,
`RuntimeResolver`, `HistoryWriter`, and the other resolver protocols exported
from `base_cli`. `RuntimeBinding.layout` uses the public immutable
`RuntimeLayout` type; consumers do not need to import private runtime modules.

`Context` is generic over the validated configuration, application state, and
service payloads owned by a consumer:

```python
Config = dict[str, object]
context: base_cli.Context[Config, ApplicationState, Services]
```

`App.command()`, `App.subcommand()`, `@base_cli.command()`, `@base_cli.option()`,
and `@base_cli.argument()` preserve the decorated callable's `ParamSpec`
signature. `base_cli.attach()` and `App.attach()` preserve the concrete Click
command subtype in their return type.

`AttachmentAdapter`, `AttachmentContract`, and the typed context/service
factories define the Click attachment boundary for adapters that compose or
wrap an attached command.

Command protocol schemas can be isolated per consumer with
`CommandSchemaRegistry` and `CommandCodec`. The module-level registration and
codec helpers remain compatible defaults backed by `RECORD_SCHEMAS`, but new
integrations should prefer an instance-owned registry when multiple protocol
boundaries share a process.

Native async callbacks are intentionally rejected with an actionable error.
The core lifecycle is synchronous so cleanup, Click resource unwinding, and
outcome finalization remain deterministic; an adapter may provide an explicit
async runner without changing the core contract.

## Public API

The supported facade is `import base_cli`. It exports the command lifecycle
(`App`, `Context`, `run_app`, decorators, and logging helpers), command filters,
and the structured command protocol helpers. Consumer-owned user configuration
is passed through `Context.user_config`; the library does not impose a schema.
The corresponding modules are also available as
`base_cli.command_filters`, `base_cli.command_protocol`, and
`base_cli.history`.

The command protocol owns only generic framing, field validation, and schema
registration. It ships with no application record types and uses
`COMMAND_PROTOCOL_V1` by default. A consumer can register its own schemas and
pass a compatibility `protocol_header` when it must interoperate with an
existing peer protocol.

Command filters use consumer-neutral name normalization by default. Consumers
with legacy command names can pass a `normalizer` callback to
`normalize_command_filter`, `normalize_command_filters`, and
`command_matches` to define compatibility aliases or prefixes.

Low-level implementation helpers are intentionally not included in the
module `__all__` surfaces. Downstream code should use the documented facade or
the explicitly supported symbols from those modules.

The repository includes [`examples/typed_consumer.py`](examples/typed_consumer.py),
a strict-typechecked consumer showing the public profile, runtime, and generic
context contracts. CI runs `mypy --strict` against that sample.

## Minimal Command

```python
from __future__ import annotations

import base_cli


app = base_cli.App(
    name="hello",
    version="0.1.0",
    profile=base_cli.CliProfile.generic(),
)


@app.command()
@base_cli.option("--name", required=True)
def main(ctx: base_cli.Context, name: str) -> None:
    ctx.log.info("starting hello")
    print(f"hello {name}")


if __name__ == "__main__":
    raise SystemExit(base_cli.run_app(app))
```

Running this command directly as a Python package automatically adds the
standard options:

```bash
hello --name Ada
hello --debug --name Ada
hello --quiet --name Ada
hello --environment prod --name Ada
hello --keep-temp --name Ada
hello --log-file /tmp/hello.log --name Ada
```

Long options use Click's native syntax, so both `--name Ada` and `--name=Ada`
are accepted. These options belong to the application-level lifecycle. A
consumer may expose them through its own launcher or compose them with a
higher-level command wrapper.

## Command Registration

Use `App` when you want a named command:

```python
app = base_cli.App(name="workspace-tools", version="0.1.0")
```

`App.name` is the canonical Click command and program name. It controls usage,
help, version output, runtime identity, and the default invocation label. Do not
pass a conflicting name to `@app.command(...)`; change `App(name=...)` instead.

Register the command function explicitly:

```python
@app.command()
def main(ctx: base_cli.Context) -> None:
    ...
```

The command function always receives `ctx` as its first argument. User-defined
options and arguments are passed after the standard lifecycle options have been
removed from Click's keyword arguments.

For small scripts, the module-level decorators are available:

```python
@base_cli.command()
def main(ctx: base_cli.Context) -> None:
    ...


if __name__ == "__main__":
    raise SystemExit(base_cli.run_app(main))
```

The decorator returns the original function. `base_cli.get_command_app(main)`
retrieves its private owning `App` when an embedding layer needs the command
object. Every module-level registration gets an independent app; there is no
process-global command registry. Its default name is inferred from the function;
pass a public name such as `@base_cli.command("workspace-tools")` when needed.
Prefer an explicit `App` when versions or consumer policies should be visible at
the top of the module.

Use `@app.subcommand()` when one CLI needs multiple verbs while keeping the
standard context, logging, redaction, and cleanup lifecycle for each invocation:

```python
app = base_cli.App(
    name="workspace-tools",
    version="0.1.0",
    help="Inspect and sync workspace projects.",
)


@app.subcommand()
@base_cli.argument("project")
def status(ctx: base_cli.Context, project: str) -> None:
    ctx.log.info("checking %s", project)


@app.subcommand("sync")
@base_cli.option("--dry-run", is_flag=True)
def sync_project(ctx: base_cli.Context, dry_run: bool) -> None:
    if ctx.dry_run:
        ctx.log.info("previewing sync")
```

Subcommands use the same `base_cli.option()` and `base_cli.argument()` metadata
as single commands. `App(help=...)` appears in the command group's `--help`
output. For subcommand apps, prefer standard options before the subcommand
name, for example `workspace-tools --debug status demo`. The post-subcommand
form, such as `workspace-tools status --debug demo`, remains accepted for
compatibility. Use either `@app.command()` for a single-command CLI or
`@app.subcommand()` for a command group; do not mix the two registration styles
on one `App`.

Finish all command and subcommand registration before the first access to
`app.click_command`, direct app invocation, `run_app()`, or
`base_cli.testing.invoke()`. Successful materialization freezes registration;
late mutations and duplicate effective command names fail deterministically.
Inferred names are stable across supported Click releases: underscores become
hyphens and conventional `_command`, `_cmd`, `_group`, and `_grp` suffixes are
removed. Pass an explicit subcommand name when a different public spelling is
required.

### Attach an existing Click application

An established Click command tree can adopt the lifecycle without rebuilding
its commands around `App`:

```python
import base_cli
import click


@click.group()
@click.pass_context
def cli(click_ctx: click.Context) -> None:
    click_ctx.ensure_object(dict)


@cli.command()
def status() -> None:
    ctx = base_cli.get_current_context()
    ctx.log.info("checking status")


cli = base_cli.attach(cli)


if __name__ == "__main__":
    raise SystemExit(base_cli.run_app(cli))
```

`attach()` returns the same Click command object. Existing callbacks,
parameters, command and alias names, help text, context settings, result
callbacks, and `click.Context.obj` values keep their Click semantics. The
lifecycle wraps the whole selected invocation once, so nested groups and lazy
`get_command()` implementations are supported without listing or importing
unselected commands. A normal Click callback may keep returning any value its
parent result callback expects; attached commands do not adopt the stricter
`None`-or-integer return contract used by `App.command()` callbacks.
Lifecycle options are installed only on the attached root and should precede
the first subcommand. Existing option declarations, including a vendor-owned
`--version`, are retained instead of being duplicated. Matching lifecycle
declarations must expose one compatible scalar value; callbacks on those Click
parameters are preserved, and their parsed results are validated before
lifecycle startup.

Pass destination names or option aliases through `sensitive_parameters` when
an existing or lazily loaded Click parameter has a domain-specific name that
does not look secret:

```python
cli = base_cli.attach(
    cli,
    sensitive_parameters={"access_code", "--credential"},
)
```

These names are applied to the selected lazy path without enumerating other
commands. Click password prompts configured with `hide_input=True` are treated
as sensitive automatically.

The module helper creates a generic `App`. Use an explicit app with the same
canonical name as the Click root when the CLI has consumer-specific runtime or
configuration policies:

```python
lifecycle = base_cli.App(name=cli.name, profile=my_profile)
lifecycle.attach(cli)
```

Factories can create application state and services after Click has parsed the
root parameters and before any existing group, command, or result callback
runs:

```python
def make_application_context(ctx: base_cli.Context) -> ApplicationContext:
    return ApplicationContext(environment=ctx.environment)


def make_services(ctx: base_cli.Context) -> Services:
    services = Services(ctx.config)
    ctx.on_cleanup(services.close)
    return services


cli = base_cli.attach(
    cli,
    context_factory=make_application_context,
    service_factory=make_services,
)
```

Their results are available as `ctx.application_context` and `ctx.services`.
The factories receive the active `base_cli.Context`, may register cleanup hooks,
and never replace the existing Click context object. `get_current_context()` is
valid in group callbacks, leaf callbacks, result callbacks, and factory-created
helpers for the duration of the attached invocation. Root Click parameter
callbacks run during initial parsing, before the attached lifecycle is active;
descendant parameter callbacks run inside it as Click dispatches the selected
path. Any root resources registered during pre-parse retain that early entry
timing but close inside the Base lifecycle: the deliberate order is enter
pre-parse resource, enter Base lifecycle, exit pre-parse resource, exit Base
lifecycle. Resources and close hooks registered by either factory also exit
before Base cleanup, so failures are reflected in history and run metadata
while `get_current_context()` is still valid.

Attach only the highest Click root that should share a lifecycle. A separately
attached child or a native `base_cli.App` command selected beneath an attached
root is rejected before its callbacks run, preventing duplicate lifecycle
boundaries.

## Options And Arguments

`base_cli.option` and `base_cli.argument` mirror Click's decorators:

```python
@app.command()
@base_cli.argument("project")
@base_cli.option("--workspace", type=str)
def main(ctx: base_cli.Context, project: str, workspace: str | None) -> None:
    ...
```

Use `sensitive=True` for options or arguments whose values must not reach
invocation logs or history writers:

```python
@base_cli.option("--token", sensitive=True, required=True)
def main(ctx: base_cli.Context, token: str) -> None:
    ...
```

All aliases declared for a sensitive option are protected, including short and
alternate long forms. Spaced values, equals forms, and attached short-option
values are redacted. Sensitive positional arguments are redacted according to
the Click command schema:

```python
@app.command()
@base_cli.argument("credential", sensitive=True)
def login(ctx: base_cli.Context, credential: str) -> None:
    ...
```

Parameters whose names contain `token`, `password`, `secret`, `api-key`
(`api_key`), or `authorization` are protected automatically. Use
`sensitive=True` for domain-specific secret names. Custom history writers
receive already-redacted argv, so raw secret-bearing argv never crosses the
framework's persistence boundary.

For native `App` commands, use `dry_run=True` when a nonstandard option should
drive `ctx.dry_run` and the lifecycle's default durable-write suppression:

```python
@base_cli.option("--preview", is_flag=True, dry_run=True)
def main(ctx: base_cli.Context, preview: bool) -> None:
    if ctx.dry_run:
        ctx.log.info("previewing changes")
```

The conventional `dry_run` parameter is still recognized automatically, so
native commands using `@base_cli.option("--dry-run", is_flag=True)` do not need
the marker. Only one option on a command may be marked `dry_run=True`; duplicate
dry-run markers fail during command registration so authors do not accidentally
ship an option that is ignored by `ctx.dry_run`.

## Standard Options

Every `base_cli.App` command gets these options:

- `--debug`: enable DEBUG logging on the user-facing stderr stream.
- `--quiet`, `-q`: suppress INFO logs on the user-facing stderr stream.
- `--environment <name>`: set `ctx.environment` for the run.
- `--config <path>`: merge an additional YAML config file.
- `--keep-temp`: preserve the run's temp directory after command completion.
- `--log-file <path>`: write the persistent log to a specific file.
- `--version`: shown when the `App` was created with a version.

`LifecycleOptions()` preserves this default set. Its `debug`, `quiet`,
`environment`, `config`, `keep_temp`, `log_file`, and `version` fields are
enabled by default; `dry_run` is opt-in. Set one field to `None` to disable it,
or replace it with a `LifecycleOption` to rename and configure it independently:

```python
lifecycle_options = base_cli.LifecycleOptions(
    config=None,
    quiet=base_cli.LifecycleOption(
        "--silent",
        "-s",
        help="Suppress routine status messages.",
    ),
    environment=base_cli.LifecycleOption(
        "--stage",
        help="Select the deployment stage.",
        metavar="NAME",
        envvar="WORKSPACE_STAGE",
        show_envvar=True,
        default="dev",
        show_default=True,
    ),
)

app = base_cli.App(
    name="workspace-tools",
    version="1.2.3",
    lifecycle_options=lifecycle_options,
)
```

`LifecycleOption` accepts Click declarations followed by the keyword-only
`name`, `help`, `metavar`, `envvar`, `show_envvar`, `show_default`, `hidden`,
and `default` presentation and value-source settings. When `name` is omitted,
Click derives the public destination from the visible declaration: the
`--stage` option above therefore uses `stage` in a Click `default_map` and
`WORKSPACE_STAGE` as its explicit environment variable. Use `name` only when a
different stable Click destination is intentional. Option shapes remain owned
by the lifecycle: flags stay scalar flags, paths retain their validation, and
declaration or destination collisions fail when commands are materialized or
attached.

Click value sources use this precedence, from strongest to weakest:

1. command-line value;
2. explicit `envvar` or Click `auto_envvar_prefix` value;
3. Click `default_map` value;
4. configured option default.

For native command groups, a stronger source wins across root and leaf
placements; when both values have the same source, the leaf value wins. An
unspecified leaf value never erases a root value. Thus an explicit root
`--stage prod` beats a leaf `default_map`, while a leaf command-line value beats
a root command-line value.

The default placement remains compatibility-oriented and deterministic. A
native single-command `App` installs lifecycle options on that command. A
native subcommand `App` installs them on both the root and every leaf, so both
`workspace-tools --debug status` and `workspace-tools status --debug` work and
the corresponding help page shows the option. `--version` remains root-only
for groups. An attached Click tree installs lifecycle options only on its root,
without enumerating lazy descendants, so they must precede the first
subcommand. Disabled and hidden options do not appear in help; renamed options
appear only under their configured declarations.

Normalized values are available as one typed `LifecycleValues` record in the
active Click context's namespaced metadata:

```python
@click.pass_context
def inspect(click_ctx: click.Context) -> None:
    values = base_cli.get_lifecycle_values(click_ctx)
    assert isinstance(values, base_cli.LifecycleValues)
    assert values is click_ctx.meta[base_cli.LIFECYCLE_META_KEY]
    print(values.environment, values.debug, values.dry_run)
```

The metadata record, rather than `click.Context.obj`, carries values between a
native group and its leaf. Base-cli neither replaces nor copies `obj`; typed
objects, dictionaries, and `None` retain their existing Click semantics. The
command callback receives only its application-specific parameters, not the
lifecycle fields.

Attached applications also honor public Click source names. For example,
`default_map={"stage": "test"}` supplies the renamed `--stage` option above,
and a runtime `auto_envvar_prefix="WORKSPACE"` reads `WORKSPACE_STAGE`. Callers
never need private `_base_cli_*` destination or environment names.

Dry-run attachment is explicit because existing Click applications may already
own that spelling. Opt it in through the same configuration:

```python
lifecycle_options = base_cli.LifecycleOptions(
    dry_run=base_cli.LifecycleOption(
        "--dry-run",
        help="Run without default durable writes.",
    ),
)
lifecycle = base_cli.App(
    name=cli.name,
    lifecycle_options=lifecycle_options,
)
lifecycle.attach(cli)
```

If the attached root already exposes a compatible `--dry-run` option, base-cli
reuses it while preserving its callback and destination. Otherwise the option
is added at the root and consumed by the lifecycle. The default
`LifecycleOptions()` does not add attached dry-run behavior; the native
conventional-name and `dry_run=True` decorator contracts described above remain
supported. This preservation rule applies to every adopted vendor option: omit
`name=` to use its existing Click destination, or choose a distinct declaration
when an explicitly configured destination must be enforced. A conflicting
explicit destination fails during attachment instead of being silently ignored.
Every configured alias must already be present on an adopted option; base-cli
never mutates the vendor declaration list. The vendor option also continues to
own its callback, type, default, environment-variable settings, help text,
metavar, and visibility. Those `LifecycleOption` settings configure options
created by base-cli; choose a distinct primary declaration when base-cli should
own those semantics.

## Exit Codes

Use `base_cli.ExitCode` when command code or tests need to name standard
command result meanings:

- `ExitCode.SUCCESS` (`0`): the command completed successfully.
- `ExitCode.FAILURE` (`1`): the command was valid, but an operational problem
  prevented successful completion.
- `ExitCode.USAGE_ERROR` (`2`): the command could not proceed because user
  input, configuration, or environment setup was invalid or incomplete.
- `ExitCode.INTERRUPTED` (`130`): the user interrupted the command with
  <kbd>Ctrl</kbd>+<kbd>C</kbd>.

Existing commands can keep returning integers. New code should prefer the named
constants when it makes intent clearer:

```python
if ctx.project_root is None:
    ctx.log.error("run this command from a project recognized by the consumer")
    return base_cli.ExitCode.USAGE_ERROR
```

`run_app()` is the process boundary for production entry points. It preserves
Click's messages and exit codes for usage and application errors, reports an
explicit abort as `1`, and reports <kbd>Ctrl</kbd>+<kbd>C</kbd> during startup or
command execution as `130` without a traceback. After the command outcome has
settled, history, metadata, and cleanup are best-effort teardown: even a second
interrupt there cannot replace the primary result.

An unexpected exception returns `1` with a stable, detail-free message. The run
ID and diagnostic-log path are included when context and file logging are
available. The traceback is kept in the persistent log when enabled and is
shown on stderr with an effective `--debug` setting. A failure before option
parsing can provide a traceback only when `--debug` is an unambiguous leading
flag; otherwise the message says that diagnostic context was unavailable.
Embedding code that needs the original exception can pass the keyword-only
`reraise_unexpected=True` argument to `run_app()`.

| Command result or exception | `outcome` | Exit code | Default message |
| --- | --- | ---: | --- |
| `None` or returned `0` | `success` | 0 | none |
| returned `2` | `usage_error` | 2 | none |
| another returned nonzero integer | `nonzero_return` | returned value | none |
| `click.UsageError` | `usage_error` | exception code | Click usage error |
| another `click.ClickException` | `click_error` | exception code | Click error |
| `click.Abort` | `aborted` | 1 | `Aborted!` |
| <kbd>Ctrl</kbd>+<kbd>C</kbd> | `interrupted` | 130 | `Interrupted.` |
| `SystemExit` | `system_exit` | normalized payload | string payload, if any |
| another unexpected exception | `unexpected_error` | 1 | stable internal-error message |

For `SystemExit`, a missing payload becomes `0`, an integer payload is preserved,
and any other payload is printed and normalized to `1`.

## Context

`Context` is the object command code should pass around instead of rediscovering
runtime paths or global settings.

Important fields include:

- `ctx.cli_name`: normalized CLI name used for state paths and logger names.
- `ctx.run_id`: timestamp plus short random suffix for this invocation.
- `ctx.application_home`: optional application home supplied by the profile.
- `ctx.project_root`: project root returned by the profile, when any.
- `ctx.workspace_root`: optional workspace root supplied by user configuration.
- `ctx.manifest_path`: project metadata path returned by the profile, when any.
- `ctx.history_scope`: history scope supplied by the profile or its
  compatibility adapter.
- `ctx.history_parent_run_id`: optional parent invocation ID supplied by
  the consumer.
- `ctx.runtime_owner`: consumer-defined runtime owner; the generic
  profile uses `default`.
- `ctx.owner_root`: application namespace root under the configured cache root.
- `ctx.run_root`: this invocation's run bundle.
- `ctx.state_dir`: owner root (compatibility alias).
- `ctx.log_dir`: run-bundle log directory.
- `ctx.cache_dir`: persistent component cache directory.
- `ctx.temp_dir`: per-run temp directory inside the bundle.
- `ctx.log_file`: the run's shared `logs/primary.log`, or `None` when persistent
  logging is disabled.
- `ctx.config`: merged configuration dictionary.
- `ctx.application_context`: optional application state returned by an
  attachment's `context_factory`, or `None`.
- `ctx.services`: optional services returned by an attachment's
  `service_factory`, or `None`.
- `ctx.user_config`: opaque consumer-owned user configuration returned by the
  profile, or `None` for the generic default.
- `ctx.environment`: active environment, defaulting to `dev`.
- `ctx.debug`: whether debug logging is enabled for the stderr stream.
- `ctx.quiet`: whether INFO logs are suppressed on the stderr stream.
- `ctx.dry_run`: whether the command is running in a no-durable-write mode.
- `ctx.keep_temp`: whether `ctx.temp_dir` should survive cleanup.
- `ctx.log`: standard Python logger configured by `base_cli`.

Helpers can retrieve the active context without threading it through every call:

```python
from base_cli import get_current_context


def helper() -> None:
    ctx = get_current_context()
    ctx.log.debug("helper is running")
```

`get_current_context()` is valid only while a `base_cli.App` command is running.

## Logging

`base_cli` configures two handlers:

- a user-facing stderr handler at INFO by default, DEBUG with `--debug`, or
  WARNING with `--quiet` / `-q`
- a persistent file handler that records DEBUG logs when persistent logging is
  enabled

`--quiet` suppresses INFO output on the user-facing stream but still shows
warnings and errors. `--debug` and `--quiet` cannot be used together. Persistent
log files still receive DEBUG-level detail, including INFO messages suppressed
from stderr. User-facing logs use colors automatically on interactive terminals;
persistent log files remain plain text. Set `NO_COLOR=1` or
`BASE_CLI_COLOR=0` to disable colors. A consumer wrapper may add its own color
option and map it to the environment variable.

Click also provides shell completion. For an app named `hello`, request a
completion script with `_HELLO_COMPLETE=bash_source hello`, replacing `bash`
with `zsh` or `fish` as needed. `base_cli` leaves installation to the caller so
shell startup files remain under user control.

Advanced tests and CI wrappers can call `base_cli.configure_logger(...,
stream=..., formatter=...)` to capture user-facing logs or apply a custom
formatter. Leave those arguments as `None` to keep the default stderr stream
and formatter. Log timestamps use the host's local timezone and include its
numeric offset by default. A consumer can set `LOG_UTC=1` to use UTC and
include an explicit `UTC` marker.

This setting affects log presentation only. Run metadata, history records, and
run IDs retain their canonical UTC representation.

Commands that inspect runtime artifacts can use `base_cli.App(log_to_file=False)`
to keep the standard context, `--debug`, and `--quiet` behavior without creating
default `logs/`, `cache/`, or `tmp/<run-id>/` directories. An explicit
`--log-file <path>` still enables file logging for that invocation.

Commands running with `ctx.dry_run` also skip default `logs/`, `cache/`, and
`tmp/<run-id>/` creation. Passing `--log-file <path>` still writes to that
explicit file so tests and diagnostics can inspect dry-run logs when needed.

The generic profile does not write command history. A profile may provide a
history writer to persist redacted command metadata, timing, exit status,
project context, and a pointer to the raw log file. History writes should be
best-effort and should not fail the user command when an index cannot be updated.

High-frequency tools can set `base_cli.App(max_log_files=<count>)` to keep at
most that many default persistent log files across the owner's run bundles.
Retention runs during startup after the current run's default log file is
resolved, and the current run's log file is never pruned. The policy is skipped
for `ctx.dry_run`,
`log_to_file=False`, and explicit `--log-file` paths so no-durable-write modes
and caller-selected log locations stay under caller control. Use this as a
small guardrail for busy local tools; an application can provide broader
maintenance commands for caches, logs, and retained temp files.

Logs use a stable, human-readable shape:

```text
2026-05-26 12:34:56 INFO    path/to/file.py:42 message
```

Use either `ctx.log` directly:

```python
ctx.log.info("processed %s items", count)
```

or the convenience functions:

```python
base_cli.log_debug("cache_dir=%s", ctx.cache_dir)
base_cli.log_info("done")
base_cli.log_warning("using fallback")
base_cli.log_error("failed")
```

Program output should still use stdout when another command might consume it.
Logs should stay on stderr so users can redirect or ignore logs without losing
the real command output.

## Config Precedence

The generic profile has no implicit configuration files. It loads the file
passed through `--config`, when present, and otherwise starts with an empty
configuration dictionary. Standard command-line options are applied by the
lifecycle after the profile's configuration is loaded; for example,
`--environment prod` overrides `environment: dev` from an explicit
configuration file.

An explicit `--config` value must identify an existing, readable regular file;
invalid paths are rejected as usage errors before profile loading or runtime
state begins. Invalid YAML reports that the file `contains invalid YAML`, while
a non-mapping document reports that it `must contain a YAML mapping`. Each error
includes the selected path. Quoted home-relative paths such as
`--config "~/tool.yml"` are expanded before validation.

`ctx.config` exposes the dictionary returned by the profile. `ctx.user_config`
exposes the opaque user-configuration value returned by the profile. Consumers
that need user files, project files, environment variables, or a merge
precedence must implement those policies in `CliProfile.load_config` and
`CliProfile.load_user_config`; `base_cli` does not define the value's fields.

## Project Discovery

The generic profile does not discover projects or assume a manifest filename.
Its `ctx.project_root` and `ctx.manifest_path` fields are `None` unless the
consumer supplies a `discover_project` policy. A profile can discover projects
from a manifest, workspace, repository metadata, or any other application-owned
source and return a `ProjectInfo` value.

Commands that require a project should validate the profile-provided value
explicitly and return a clear usage error or actionable message.

## Runtime Directories

The generic profile uses the configured cache root and an application namespace
to create per-run logs, caches, and temporary directories. Pass
`cache_root` to `CliProfile.generic()` for deterministic placement in tests or
applications; otherwise the platform cache directory is used. Linux and WSL2
follow `XDG_CACHE_HOME` or `~/.cache`, macOS uses `~/Library/Caches`, and
Windows uses `%LOCALAPPDATA%` (falling back to `~/AppData/Local`). Set
`BASE_CLI_CACHE_DIR` to override the default on any platform. The generic
profile does not prescribe a product-wide cache name or cleanup command.

Each lifecycle-owned invocation is a run bundle containing a private
`run.json`, `logs/`, and `tmp/`, while persistent component caches live in the
owner's cache directory. When persistence succeeds, `run.json` begins with
`status: "running"` and is finalized with `status`, `outcome`, `exit_code`,
`ended_at`, and `duration_ms`, including command failures and interruptions.
The stable outcome values are `success`, `usage_error`, `nonzero_return`,
`click_error`, `aborted`, `interrupted`, `system_exit`, and
`unexpected_error`. Terminal-write failures are warnings and cannot change the
process result; base-cli then removes a matching or corrupt owned record on a
best-effort basis so history data cannot masquerade as authoritative core data.

Parsing errors, help, and version requests occur before the command lifecycle
owns a bundle and therefore do not create one. Neither do inherited runtimes,
`log_to_file=False`, or dry-run invocations; an explicit log path can still
receive diagnostics in the latter two modes. Context startup is transactional:
if directory creation, logger setup, or retention fails, base-cli closes
partially installed handlers and erases new bundle-local temp files through the
same retained handle used by normal teardown. It retains log files and empty
directory boundaries rather than attempting race-prone pathname removal.
Pre-existing content, persistent component caches, and parent-runtime data are
preserved.

Recursive temp cleanup is fail-closed. Base-cli erases contents only when
its leaf was claimed exclusively for the invocation, its retained directory
handle and creation-time filesystem identity still match, and the path remains
a strict, run-ID-marked descendant of the selected run root with no symlinked
component. Filesystem roots, the run root itself, replaced directories,
traversal paths, mounted targets, external paths, and paths whose ownership or
mount identity cannot be proven are kept and reported as cleanup warnings.
Content erasure is descriptor-relative. Empty directory nodes—including the
leaf and its ancestors—are intentionally retained because portable POSIX APIs
cannot atomically remove an already-verified open directory; avoiding pathname
`rmdir` closes the final replacement race. Platforms without the required
handle operations retain files too and warn. `--keep-temp` preserves both the
directory tree and files.

This boundary assumes the per-user runtime tree is not maliciously mutated by
another process running with the same account while ownership is acquired or
cleanup runs. Processes with the same filesystem authority can otherwise
rename or replace any user-owned runtime path; base-cli still verifies the
retained handle against the published path before erasing contents.

On POSIX, base-cli enforces owner-only `0600`/`0700` modes. On Windows, the
default user-local cache root relies on inherited user-profile ACLs; consumers
using a custom cache root must provide the appropriate ACL themselves.

See [Platform support](docs/platform-support.md) for the supported Linux,
WSL2, macOS, and native Windows boundaries. Native Windows support covers the
generic `base-cli` framework; it does not imply native Windows support for
Base or `basectl`.

Use `ctx.on_cleanup()` for cleanup work that should happen even when helper code
does not own the main command wrapper:

```python
def close_connection() -> None:
    connection.close()


ctx.on_cleanup(close_connection)
```

Cleanup hooks run before temp-content erasure. Hook failures are logged as
warnings and do not prevent later hooks from running.

## Testing

Use `base_cli.testing.invoke` for unit tests:

```python
from pathlib import Path

from base_cli.testing import invoke


def test_command(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    result = invoke(
        app,
        ["--name", "Ada"],
        home=tmp_path,
        cwd=project,
    )

    assert result.exit_code == 0
    assert "hello Ada" in result.stdout
```

The helper wraps Click's `CliRunner` but routes the invocation through the same
`run_app()` boundary used by production entry points. Option parsing (including
native forms such as `--name=Ada`), effective and logged argv, exit-code and
error normalization, lifecycle behavior, and command-group dispatch therefore
follow the production path.

By default, unexpected exceptions receive the production-safe rendering and
exit code. Pass the keyword-only `reraise_unexpected=True` argument when a test
needs the original unexpected exception in `result.exception`:

```python
result = invoke(app, [], home=tmp_path, reraise_unexpected=True)
assert isinstance(result.exception, RuntimeError)
```

As with direct `CliRunner` use, a handled nonzero exit normally also gives
`Result.exception` a `SystemExit` carrying that exit code. That does not by
itself indicate an unexpected crash; assert `result.exit_code` and the rendered
stdout or stderr for expected usage or application failures.

`invoke()` sets `HOME` plus the relevant `USERPROFILE`, `LOCALAPPDATA`, and
`XDG_CACHE_HOME` values when requested, and supplies `cwd` to the invocation for
the duration of the test. Calls that use `cwd` are serialized and the caller's
cwd is restored afterward, but this remains process-global: do not use it
concurrently with code that changes cwd outside `invoke()` or from threads
spawned by the invoked command. A generic profile should receive project
fixtures through its `discover_project` callback. The helper does not create or
interpret any product-specific manifest fixture.

When `home` is supplied, `invoke()` provides an isolated default cache
environment for tests. Pass `env={"BASE_CLI_CACHE_DIR": str(path)}` when a test
needs an explicit cache location.

## When To Use `base_cli`

Use `base_cli` for Python commands that need a predictable command
lifecycle: standard options, logging, redaction, runtime state, cleanup, and
test helpers. Standalone consumers should use `CliProfile.generic()` or
provide an explicit profile with their own project and configuration policies.

It is a good fit for:

- project discovery commands
- setup and artifact management commands
- developer workflow commands
- CLIs that need predictable logs, temp directories, and config precedence

It is not meant to replace Click, Typer, argparse, or rich terminal UI
frameworks. It is the reusable layer around command lifecycle, context, logging,
configuration, and state.
