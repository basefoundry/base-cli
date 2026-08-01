# Local configuration

`base-cli` does not read machine-local or project configuration implicitly.
Standalone applications can accept an explicit `--config` file through the
generic profile, or provide their own `load_user_config` and `load_config`
callbacks for application-owned configuration sources.

The consumer owns the configuration schema, merge semantics, and operational
choice of whether to back up or synchronize its machine-local files.
