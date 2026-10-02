# Local configuration

`base-cli` does not read machine-local or project configuration implicitly.
Standalone applications can accept an explicit `--config` file through the
generic profile, or provide their own `load_user_config` and `load_config`
callbacks for application-owned configuration sources.

Command-line `--config` values are strict: the path is expanded and must be an
existing, readable regular file before profile and runtime startup. By contrast,
`base_cli.config.load_yaml_file(path)` keeps its optional-file behavior for
profile-discovered configuration; pass `required=True` when a consumer-owned
call site represents an explicit user request.

The consumer owns the configuration schema, merge semantics, and operational
choice of whether to back up or synchronize its machine-local files.

Applications that prefer conventional policy can opt into
`CliProfile.batteries_included("tool")` after installing the `base-cli[yaml]`
extra. It loads optional platform-aware user,
project, environment, and explicit YAML layers with documented precedence and
records the winning source for each key in `Context.config_provenance`. Its
reserved lifecycle keys are validated separately as `Context.framework_config`;
consumer-owned keys remain in `Context.config`. `CliProfile.generic()` remains
the convention-free default.

For direct use, `BatteriesIncludedConfigLoader` accepts an optional `cli_name`.
When no `user_config_dir` is supplied, that identity selects an isolated
directory below the platform's default config root (for example,
`~/.config/tool` on Linux). Consumers with an existing configuration-root
policy should pass `user_config_dir` explicitly; the identity is then metadata
only.

## Trust of discovered project configuration

`CliProfile.batteries_included()` validates implicit project configuration before
loading it. On POSIX, the file and directories between the working directory and
the discovered project must be owned by the invoking user or root and must not
be writable by group/other. Symlinks and Windows reparse points are refused,
including project environment files. Refusals raise `ConfigurationError` naming
the path. Windows ACL ownership/write permissions are not evaluated; applications
using shared Windows workspaces must provide their own discovery/trust policy.

Discovery checks the current directory and at most 32 ancestors, stops at `.git`
(including worktree marker files), and never crosses a filesystem boundary.
`max_project_ancestor_depth=0` restricts discovery to the current directory;
`project_boundary_marker` changes the marker or accepts `None` to disable markers.
`trust_discovered_config=False` explicitly opts out of permission/reparse checks
for knowingly shared workspaces. It does not disable depth/filesystem limits.
Custom discovery callbacks own discovery boundaries; their project files still
receive the loader's trust checks. `CliProfile.generic()` remains unchanged.

All YAML files are limited to 1 MiB of UTF-8 input, 64 container levels, and
100,000 visited values (including alias expansion); recursive aliases are refused.
Explicit `--config` is an intentional file choice and bypasses discovery trust,
but still uses these parsing bounds.
