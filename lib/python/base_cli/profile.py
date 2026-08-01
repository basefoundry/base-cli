from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ._runtime import RuntimeLayout, runtime_layout
from .config import load_yaml_file
from .paths import make_run_id


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


ProjectDiscovery = Callable[[Path], ProjectInfo | None]
UserConfigLoader = Callable[[], object | None]
ConfigLoader = Callable[[ProjectInfo | None, Path | None], dict[str, Any]]
RuntimeResolver = Callable[[str, ProjectInfo | None], RuntimeBinding]
WorkspaceRootResolver = Callable[[object | None], Path | None]
HistoryWriter = Callable[[Any, list[str], set[str], datetime, int], None]
DisplayCommandResolver = Callable[[], str | None]
HistoryDisplayResolver = Callable[[str, list[str]], str]


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
    history_display_command: HistoryDisplayResolver = _generic_history_display_command
    resolve_workspace_root: WorkspaceRootResolver = _no_workspace_root

    @classmethod
    def generic(
        cls,
        *,
        cache_root: Path | None = None,
        application_home: Path | None = None,
        discover_project: ProjectDiscovery | None = None,
        load_user_config: UserConfigLoader | None = None,
        load_config: ConfigLoader | None = None,
        history_display_command: HistoryDisplayResolver | None = None,
        resolve_workspace_root: WorkspaceRootResolver | None = None,
    ) -> CliProfile:
        """Create a profile with consumer-neutral defaults.

        Generic commands do not discover a manifest, load implicit config, or
        write command history unless the caller supplies those policies.
        """
        return cls(
            discover_project=discover_project or _discover_no_project,
            load_user_config=load_user_config or _empty_user_config,
            load_config=load_config or _load_explicit_config,
            resolve_runtime=_generic_runtime_resolver(cache_root, application_home),
            display_command=_no_display_command,
            history_display_command=history_display_command or _generic_history_display_command,
            resolve_workspace_root=resolve_workspace_root or _no_workspace_root,
        )

def _discover_no_project(_cwd: Path) -> ProjectInfo | None:
    return None


def _empty_user_config() -> None:
    return None


def _load_explicit_config(_project: ProjectInfo | None, explicit: Path | None) -> dict[str, Any]:
    return load_yaml_file(explicit) if explicit is not None else {}


def _generic_runtime_resolver(
    cache_root: Path | None,
    application_home: Path | None,
) -> RuntimeResolver:
    def resolve_runtime(cli_name: str, project: ProjectInfo | None) -> RuntimeBinding:
        root = cache_root.expanduser().resolve() if cache_root is not None else _default_cache_root()
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


def _default_cache_root() -> Path:
    configured = os.environ.get("BASE_CLI_CACHE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    root = Path.home()
    if sys.platform == "darwin":
        return root / "Library" / "Caches"
    return root / ".cache"
