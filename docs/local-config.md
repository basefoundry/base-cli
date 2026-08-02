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
