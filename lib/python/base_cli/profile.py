from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast

from ._runtime import runtime_layout
from .config import load_yaml_file
from .context import Context
from .paths import default_cache_root, make_run_id
from .runtime import RuntimeLayout

__all__ = [
    "CliProfile",
    "ConfigLoader",
    "DisplayCommandResolver",
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
    ) -> dict[str, Any]: ...


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

def _discover_no_project(_cwd: Path) -> ProjectInfo | None:
    return None


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
