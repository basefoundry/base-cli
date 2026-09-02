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
