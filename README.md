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

## Public API

The supported facade is `import base_cli`. It exports the command lifecycle
(`App`, `Context`, `run_app`, decorators, and logging helpers), command filters,
the structured command protocol helpers, and the user configuration types used
by `Context.user_config`. The corresponding modules are also available as
`base_cli.command_filters`, `base_cli.command_protocol`, and
`base_cli.history`.

Low-level implementation helpers are intentionally not included in the
module `__all__` surfaces. Downstream code should use the documented facade or
the explicitly supported symbols from those modules.

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

Long options with values use space-separated syntax. `base_cli.run_app()` rejects
equals-form values such as `--name=Ada` before Click parses arguments.
These options belong to the application-level lifecycle. A consumer may expose
them through its own launcher or compose them with a higher-level command
wrapper.

## Command Registration

Use `App` when you want a named command:

```python
app = base_cli.App(name="workspace-tools", version="0.1.0")
```

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
```

Prefer an explicit `App` when command names, versions, or consumer
policies should be visible at the top of the module.

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

## Options And Arguments

`base_cli.option` and `base_cli.argument` mirror Click's decorators:

```python
@app.command()
@base_cli.argument("project")
@base_cli.option("--workspace", type=str)
def main(ctx: base_cli.Context, project: str, workspace: str | None) -> None:
    ...
```

Use `sensitive=True` for options whose values should not appear in invocation
logs:

```python
@base_cli.option("--token", sensitive=True, required=True)
def main(ctx: base_cli.Context, token: str) -> None:
    ...
```

Both `--token secret` and an externally supplied `--token=secret` token are
redacted in debug logs. The lifecycle rejects equals-form option values before
Click parses them.

Use `dry_run=True` when a nonstandard option should drive `ctx.dry_run` and
the lifecycle's default durable-write suppression:

```python
@base_cli.option("--preview", is_flag=True, dry_run=True)
def main(ctx: base_cli.Context, preview: bool) -> None:
    if ctx.dry_run:
        ctx.log.info("previewing changes")
```

The conventional `dry_run` parameter is recognized automatically, so commands
using `@base_cli.option("--dry-run", is_flag=True)` do not need the marker.
Only one option on a command may be marked `dry_run=True`; duplicate dry-run
markers fail during command registration so authors do not accidentally ship an
option that is ignored by `ctx.dry_run`.

## Standard Options

Every `base_cli.App` command gets these options:

- `--debug`: enable DEBUG logging on the user-facing stderr stream.
- `--quiet`, `-q`: suppress INFO logs on the user-facing stderr stream.
- `--environment <name>`: set `ctx.environment` for the run.
- `--config <path>`: merge an additional YAML config file.
- `--keep-temp`: preserve the run's temp directory after command completion.
- `--log-file <path>`: write the persistent log to a specific file.
- `--version`: shown when the `App` was created with a version.

The command receives only its own application-specific options. Standard options
are consumed before the command function is called.

## Exit Codes

Use `base_cli.ExitCode` when command code or tests need to name standard
command result meanings:

- `ExitCode.SUCCESS` (`0`): the command completed successfully.
- `ExitCode.FAILURE` (`1`): the command was valid, but an operational problem
  prevented successful completion.
- `ExitCode.USAGE_ERROR` (`2`): the command could not proceed because user
  input, configuration, or environment setup was invalid or incomplete.

Existing commands can keep returning integers. New code should prefer the named
constants when it makes intent clearer:

```python
if ctx.project_root is None:
    ctx.log.error("run this command from a project recognized by the consumer")
    return base_cli.ExitCode.USAGE_ERROR
```

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
- `ctx.user_config`: typed user configuration returned by the profile.
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


`ctx.config` exposes the dictionary returned by the profile. `ctx.user_config`
exposes the user-configuration value returned by the profile. Consumers that
need user files, project files, environment variables, or a merge precedence
must implement those policies in `CliProfile.load_config` and
`CliProfile.load_user_config`.

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
applications; otherwise the platform cache directory is used. The generic
profile does not prescribe a product-wide cache name or cleanup command.

Each invocation is a run bundle containing private (`0600`) `run.json`,
`logs/`, and `tmp/`, while persistent component caches live in the
bundle's cache directory.

Use `ctx.on_cleanup()` for cleanup work that should happen even when helper code
does not own the main command wrapper:

```python
def close_connection() -> None:
    connection.close()


ctx.on_cleanup(close_connection)
```

Cleanup hooks run before temp directory removal. Hook failures are logged as
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

The helper wraps Click's `CliRunner`, sets `HOME` when requested, and supplies
`cwd` to the invocation for the duration of the test. Calls that use
`cwd` are serialized and the caller's cwd is restored afterward, but this
remains process-global: do not use it concurrently with code that changes cwd
outside `invoke()` or from threads spawned by the invoked command. A
generic profile should receive project fixtures through its
`discover_project` callback. The helper does not create or interpret any
product-specific manifest fixture.

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
