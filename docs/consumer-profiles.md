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
project library, use a different serialization format, or return no project
metadata at all.

If a consumer persists history, it can provide `history_display_command` to
translate internal entry-point names into user-facing labels. The generic
default only replaces underscores with hyphens; it does not know any product's
command aliases.

## Compatibility profile

`App()` uses `CliProfile.generic()` when no profile is supplied. This keeps the
standalone default consumer-neutral. During the migration, Base and other
existing integrations that still need these conventions can opt into
`CliProfile.legacy_base()` explicitly while they move their adapters out of the
generic package.

The legacy profile contains the current Base conventions, including:

- upward discovery of `base_manifest.yaml`;
- `BASE_HOME`, `BASE_CACHE_DIR`, and Base owner/runtime environment variables;
- `~/.base.d/config.yaml` and project `.base/config.yaml`;
- Base's owner-aware cache and run layout;
- Base history persistence and delegation metadata. Base's command-label policy
  is supplied by Base rather than encoded in `base_cli.history`.

These conventions are intentionally isolated behind one profile so they can be
moved into the Base consumer without changing command lifecycle code.

## Refactoring boundary

The following behaviors should not be added to generic lifecycle modules:

- a required product name or launcher name;
- a product-specific manifest filename;
- a product-specific home or cache directory;
- product-specific configuration keys or environment variables;
- IDE/editor settings;
- product-specific command lists or history schema;
- assumptions about a downstream repository's directory layout.

The remaining migration phases are:

1. Move Base discovery, config, runtime, and history adapters into Base.
2. Generalize the remaining context/config types where their names still encode
   Base concepts.
3. Remove the compatibility profile and keep `base_cli` focused on the generic
   lifecycle.

The package rename is deliberately separate from this refactor. Names can be
changed after the dependency boundary is stable.
