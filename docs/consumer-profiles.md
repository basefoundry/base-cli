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

The next migration step is to generalize the remaining context/config types
whose compatibility names still reflect one historical consumer.

The package rename is deliberately separate from this refactor. Names can be
changed after the dependency boundary is stable.
