# Consumer Profiles

`base_cli` provides a reusable command lifecycle. It does not own a project's
manifest format, configuration directory, workspace model, cache policy, or
history product.

Those decisions are supplied by a `CliProfile`. The profile is the boundary
between the generic lifecycle and an application-specific consumer:

```text
Click command
    |
    v
base_cli.App + Context + logging + cleanup
    |
    +-- project discovery       -> CliProfile.discover_project
    +-- user configuration      -> CliProfile.load_user_config
    +-- workspace projection    -> CliProfile.resolve_workspace_root
    +-- project/explicit config -> CliProfile.load_config
    +-- runtime placement       -> CliProfile.resolve_runtime
    +-- history command labels  -> CliProfile.history_display_command
    +-- optional history        -> CliProfile.history_writer
```

## Generic profile

Use `CliProfile.generic()` for a standalone application:

```python
from pathlib import Path

import base_cli


app = base_cli.App(
    name="hello",
    profile=base_cli.CliProfile.generic(
        cache_root=Path.home() / ".cache" / "hello",
    ),
)
```

The generic profile:

- does not search for a manifest;
- does not read product-owned user or project configuration files;
- loads only an explicitly supplied `--config` file by default;
- places runtime state under the configured cache root and application namespace;
- does not write command history unless a history policy is supplied.

An application can add its own policies without changing the lifecycle:

```python
from pathlib import Path

import base_cli


def discover_project(cwd: Path) -> base_cli.ProjectInfo | None:
    manifest = cwd / "tool.manifest"
    if not manifest.exists():
        return None
    return base_cli.ProjectInfo(root=cwd, manifest=manifest, name="demo")


profile = base_cli.CliProfile.generic(
    discover_project=discover_project,
)
```

The callback types are deliberately small. A consumer can wrap an existing
project library, use a different serialization format, return no project
metadata, or keep its typed user-configuration object entirely in the consumer.
`Context.user_config` is opaque to `base_cli`; `resolve_workspace_root` is an
optional projection used when commands need a workspace root without exposing
the consumer's configuration schema to the generic lifecycle.

If a consumer persists history, it can provide `history_display_command` to
translate internal entry-point names into user-facing labels. The generic
default only replaces underscores with hyphens; it does not know any product's
command aliases.

## Delegated display labels

Launchers and wrapper commands can use `delegated_display_command()` when the
user-facing command label should come from the invoking environment. It returns
the trimmed value of `BASE_CLI_DISPLAY_COMMAND` when that variable is set to a
non-blank value, and otherwise returns the supplied default. Pass it as a
profile's `display_command` resolver when a delegated invocation should retain
the wrapper's label in lifecycle-facing usage and invocation metadata:

```python
from dataclasses import replace

import base_cli


profile = replace(
    base_cli.CliProfile.generic(),
    display_command=base_cli.delegated_display_command,
)
app = base_cli.App(name="deploy", profile=profile)
```

For example, a launcher can select its public label without changing the
consumer's entry point:

```bash
BASE_CLI_DISPLAY_COMMAND="myorg deploy" python -m myapp deploy --env prod
```

## Batteries-included profile

Applications that want conventional configuration discovery can opt in without
changing the generic defaults:

```python
profile = base_cli.CliProfile.batteries_included("tool")
app = base_cli.App(name="tool", profile=profile)
```

The profile uses platform-aware user configuration roots (`XDG_CONFIG_HOME` or
`~/.config` on Linux, `~/Library/Application Support` on macOS, and `%APPDATA%`
on Windows). `BASE_CLI_CONFIG_DIR` overrides that root. User files live under
`<root>/<cli-name>/config.yaml`; a discovered project may provide
`.base-cli.yaml` and `environments/<name>.yaml` files. All of these layers are
optional and their filenames can be customized by the profile factory. An
explicit `--config` path remains strict and must exist as a readable regular
file.

Configuration precedence is deterministic, from lowest to highest:

1. framework default (`environment: dev`);
2. user base configuration;
3. project base configuration;
4. user environment configuration;
5. project environment configuration;
6. explicit `--config` configuration;
7. command-line lifecycle options.

The environment is selected by `--environment` when supplied. Otherwise the
explicit, project, or user base `environment` value is used, falling back to
`dev`. Mapping values merge recursively; scalar and list values replace the
lower-precedence value. `Context.config_provenance` records the winning source
for each dotted key.

The reserved framework keys `environment`, `log_level`, and `keep_temp` are
validated into `Context.framework_config` and are excluded from the consumer
configuration dictionary. All other keys remain consumer-owned and are exposed
through `Context.config`.

## Safe profile errors

Plain exceptions from profile callbacks are treated as unexpected internal
errors: production output hides their details, while `--debug` exposes the
traceback after option parsing. This prevents a programming error or a private
value in a callback from becoming user-facing output by accident.

For a user-correctable configuration problem whose message is safe to show,
raise `base_cli.ConfigurationError`; `run_app()` renders it as a Click usage
error with exit code `2`. A callback may raise `click.UsageError` or another
`click.ClickException` when it needs Click's standard rendering or a custom
exit code. Consumers that previously raised plain `ValueError` for expected
configuration failures should migrate those sites to `ConfigurationError`.

## Consumer-owned adapters

`App()` uses `CliProfile.generic()` when no profile is supplied. This keeps the
standalone default consumer-neutral. A product consumer that needs manifest
discovery, implicit configuration, owner-aware runtime placement, or history
should implement those policies in its own adapter module and pass the resulting
profile to `App`.

The generic history helpers likewise do not select a product-owned history
path. Consumers resolve that path in their adapter and pass it to
`write_history_record()` or `write_primary_record()`.

## Refactoring boundary

The following behaviors should not be added to generic lifecycle modules:

- a required product name or launcher name;
- a product-specific manifest filename;
- a product-specific home or cache directory;
- product-specific configuration keys or environment variables;
- IDE/editor settings;
- product-specific command lists or history schema;
- assumptions about a downstream repository's directory layout.

The generic profile and typed `Context` are now the stable framework boundary;
consumer-specific conventions belong in an opt-in profile or adapter. The
intentional typing boundary for `Context.user_config` and the recommended
consumer accessor pattern are documented in
[`user-config-typing.md`](user-config-typing.md).

## Typed extension contracts

The supported callback contracts are exported from `base_cli` as typed protocols
for static analyzers: `ProjectDiscovery`, `UserConfigLoader`,
`ConfigLoader`, `RuntimeResolver`, `WorkspaceRootResolver`, `HistoryWriter`,
`DisplayCommandResolver`, and `HistoryDisplayResolver`. A custom runtime
resolver returns `RuntimeBinding`, whose immutable `layout` is the public
`RuntimeLayout` dataclass. No consumer needs to import `_runtime`.

`Context` accepts three consumer payload types:

```python
Context[ConfigT, ApplicationStateT, ServicesT]
```

`config` is the validated configuration payload; `application_context` and
`services` are optional state and service payloads initialized by an attached
consumer. `AttachmentAdapter` and `AttachmentContract` describe the typed
boundary used by `App.attach()`. Attachment returns the same concrete Click
command object, so aliases, lazy groups, and custom Click subclasses remain
owned by the consumer.

`user_config` is intentionally opaque (`object | None`) because its schema is
consumer-owned. Define one typed accessor in the consumer instead of casting it
in every command; see [`user-config-typing.md`](user-config-typing.md). A fourth
generic parameter is reserved for a future compatibility boundary and is not
part of the 0.4.x API.

The core lifecycle is synchronous by design. Native `async def` callbacks and
callbacks that return awaitables are rejected with an actionable error. An
adapter that owns an event loop may run asynchronous work explicitly at its
boundary and return a normal synchronous callback result to base-cli.
