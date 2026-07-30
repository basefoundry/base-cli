# Local configuration

`base-cli` reads machine-local configuration from `~/.base.d/config.yaml`.
Project configuration is read from `<project>/.base/config.yaml`, and an
explicit `--config` file can provide the final project-specific override.

The package owns the configuration schema and merge semantics. Users own the
operational choice of whether to back up or synchronize the machine-local file,
using tools such as iCloud, chezmoi, a dotfiles repository, Time Machine, or a
manual copy. The file can contain paths and other machine-specific values and
should not be synchronized blindly across incompatible machines.
