from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast

from ._runtime import runtime_layout
from .config import (
    BatteriesIncludedConfigLoader,
    ConfigSnapshot,
    load_yaml_file,
)
from .context import Context
from .paths import default_cache_root, default_config_root, make_run_id, normalize_cli_name
from .runtime import RuntimeLayout

__all__ = [
    "CliProfile",
    "BatteriesIncludedConfigLoader",
    "ConfigSnapshot",
    "ConfigLoader",
    "DisplayCommandResolver",
    "EnvironmentConfigLoader",
    "HistoryDisplayResolver",
    "HistoryWriter",
    "ProjectDiscovery",
    "ProjectInfo",
    "RuntimeBinding",
    "RuntimeResolver",
    "UserConfigLoader",
    "WorkspaceRootResolver",
]


@dataclass(frozen=True)
class ProjectInfo:
    """Consumer-neutral project information discovered for an invocation."""

    root: Path | None = None
    manifest: Path | None = None
    name: str | None = None


@dataclass(frozen=True)
class RuntimeBinding:
    """Runtime decisions supplied by a consumer profile."""

    cache_root: Path
    layout: RuntimeLayout
    application_home: Path | None
    runtime_owner: str
    project_root: Path | None
    project_name: str | None
    inherited_path: Path | None
    history_parent_run_id: str | None
    run_id: str
    primary_log_file: Path | None = None
    history_scope: str = "primary"
    write_identity: bool = False


class ProjectDiscovery(Protocol):
    """Discover consumer-owned project information for the current directory."""

    def __call__(self, cwd: Path) -> ProjectInfo | None: ...


class UserConfigLoader(Protocol):
    """Load opaque consumer-owned user configuration."""

    def __call__(self) -> object | None: ...


class ConfigLoader(Protocol):
    """Load validated framework configuration and opaque consumer settings."""

    def __call__(
        self,
        project: ProjectInfo | None,
        explicit_path: Path | None,
    ) -> dict[str, Any] | ConfigSnapshot: ...


class EnvironmentConfigLoader(Protocol):
    """Load configuration with an explicitly selected environment."""

    def __call__(
        self,
        project: ProjectInfo | None,
        explicit_path: Path | None,
        environment: str,
    ) -> dict[str, Any] | ConfigSnapshot: ...


class RuntimeResolver(Protocol):
    """Resolve the runtime directories and ownership for one invocation."""

    def __call__(self, cli_name: str, project: ProjectInfo | None) -> RuntimeBinding: ...


class WorkspaceRootResolver(Protocol):
    """Project a consumer-owned user configuration into a workspace root."""

    def __call__(self, user_config: object | None) -> Path | None: ...


class HistoryWriter(Protocol):
    """Persist one completed invocation using the active typed Context."""

    def __call__(
        self,
        context: Context[Any, Any, Any],
        argv: list[str],
        sensitive_parameters: set[str],
        started_at: datetime,
        exit_code: int,
    ) -> None: ...


class DisplayCommandResolver(Protocol):
    """Resolve the process-facing command label used in diagnostics."""

    def __call__(self) -> str | None: ...


class HistoryDisplayResolver(Protocol):
    """Resolve the command label persisted in consumer history."""

    def __call__(self, cli_name: str, argv: list[str]) -> str: ...


def _no_display_command() -> str | None:
    return None


def _no_workspace_root(_user_config: object | None) -> Path | None:
    return None


def _generic_history_display_command(cli_name: str, _argv: list[str]) -> str:
    return cli_name.replace("_", "-")


@dataclass(frozen=True)
class CliProfile:
    """Policy boundary between the generic CLI lifecycle and its consumer.

    A profile supplies project discovery, configuration, runtime placement, and
    optional history persistence. The generic profile has no manifest convention,
    no product-owned config files, and no history writer.
    """

    discover_project: ProjectDiscovery
    load_user_config: UserConfigLoader
    load_config: ConfigLoader
    resolve_runtime: RuntimeResolver
    history_writer: HistoryWriter | None = None
    display_command: DisplayCommandResolver = _no_display_command
    history_display_command: HistoryDisplayResolver = cast(
        HistoryDisplayResolver,
        _generic_history_display_command,
    )
    resolve_workspace_root: WorkspaceRootResolver = cast(
        WorkspaceRootResolver,
        _no_workspace_root,
    )
    load_config_for_environment: EnvironmentConfigLoader | None = None

    @classmethod
    def generic(
        cls,
        *,
        cache_root: Path | None = None,
        application_home: Path | None = None,
        discover_project: ProjectDiscovery | None = None,
        load_user_config: UserConfigLoader | None = None,
        load_config: ConfigLoader | None = None,
        resolve_runtime: RuntimeResolver | None = None,
        history_display_command: HistoryDisplayResolver | None = None,
        resolve_workspace_root: WorkspaceRootResolver | None = None,
    ) -> CliProfile:
        """Create a profile with consumer-neutral defaults.

        Generic commands do not discover a manifest, load implicit config, or
        write command history unless the caller supplies those policies.
        """
        return cls(
            discover_project=discover_project or cast(ProjectDiscovery, _discover_no_project),
            load_user_config=load_user_config or _empty_user_config,
            load_config=load_config or cast(ConfigLoader, _load_explicit_config),
            resolve_runtime=resolve_runtime or _generic_runtime_resolver(cache_root, application_home),
            display_command=_no_display_command,
            history_display_command=history_display_command
            or cast(HistoryDisplayResolver, _generic_history_display_command),
            resolve_workspace_root=resolve_workspace_root
            or cast(WorkspaceRootResolver, _no_workspace_root),
        )

    @classmethod
    def batteries_included(
        cls,
        cli_name: str,
        *,
        cache_root: Path | None = None,
        application_home: Path | None = None,
        config_root: Path | None = None,
        user_config_dir: Path | None = None,
        user_config_name: str = "config.yaml",
        project_config_name: str = ".base-cli.yaml",
        environment_dir_name: str = "environments",
        discover_project: ProjectDiscovery | None = None,
        resolve_runtime: RuntimeResolver | None = None,
    ) -> CliProfile:
        """Create an opt-in profile with conventional layered YAML config.

        Layers are merged from lowest to highest precedence: defaults, user,
        project, user environment, project environment, and explicit ``--config``.
        The generic profile remains convention-free; this method is the explicit
        adoption point for applications that want these conventions.
        """
        normalized_name = normalize_cli_name(cli_name)
        if not normalized_name:
            raise ValueError("cli_name must contain a non-empty command name")
        root = (config_root or default_config_root()).expanduser()
        selected_user_dir = (
            user_config_dir.expanduser()
            if user_config_dir is not None
            else root / normalized_name
        )
        loader = BatteriesIncludedConfigLoader(
            normalized_name,
            user_config_dir=selected_user_dir,
            user_config_name=user_config_name,
            project_config_name=project_config_name,
            environment_dir_name=environment_dir_name,
        )
        project_discovery = discover_project or _conventional_project_discovery(project_config_name)

        def load_user_config() -> object | None:
            values = load_yaml_file(loader.user_config_path)
            return values or None

        def load_config(
            project: ProjectInfo | None,
            explicit_path: Path | None,
        ) -> ConfigSnapshot:
            return loader.load(
                project.root if project is not None else None,
                explicit_path,
            )

        def load_config_for_environment(
            project: ProjectInfo | None,
            explicit_path: Path | None,
            environment: str,
        ) -> ConfigSnapshot:
            return loader.load(
                project.root if project is not None else None,
                explicit_path,
                environment=environment,
            )

        return cls(
            discover_project=project_discovery,
            load_user_config=load_user_config,
            load_config=load_config,
            load_config_for_environment=load_config_for_environment,
            resolve_runtime=resolve_runtime
            or _generic_runtime_resolver(cache_root, application_home),
        )

def _discover_no_project(_cwd: Path) -> ProjectInfo | None:
    return None


def _conventional_project_discovery(config_name: str) -> ProjectDiscovery:
    def discover(cwd: Path) -> ProjectInfo | None:
        current = cwd.expanduser().resolve()
        for directory in (current, *current.parents):
            candidate = directory / config_name
            if candidate.is_file():
                return ProjectInfo(
                    root=directory,
                    manifest=candidate,
                    name=directory.name,
                )
        return None

    return discover


def _empty_user_config() -> None:
    return None


def _load_explicit_config(_project: ProjectInfo | None, explicit: Path | None) -> dict[str, Any]:
    return load_yaml_file(explicit, required=True) if explicit is not None else {}


def _generic_runtime_resolver(
    cache_root: Path | None,
    application_home: Path | None,
) -> RuntimeResolver:
    def resolve_runtime(cli_name: str, project: ProjectInfo | None) -> RuntimeBinding:
        root = (cache_root if cache_root is not None else default_cache_root()).expanduser().resolve()
        run_id = make_run_id()
        project_root = project.root if project is not None else None
        project_name = project.name if project is not None else None
        return RuntimeBinding(
            cache_root=root,
            layout=runtime_layout(
                root,
                cli_name,
                run_id,
                namespace=cli_name,
                project_name=project_name,
            ),
            application_home=application_home,
            runtime_owner="default",
            project_root=project_root,
            project_name=project_name,
            inherited_path=None,
            history_parent_run_id=None,
            run_id=run_id,
        )

    return resolve_runtime
