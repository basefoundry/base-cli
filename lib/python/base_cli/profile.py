from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ._runtime import RuntimeLayout, runtime_layout
from .config import UserConfig, UserIdeConfig, load_yaml_file
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
UserConfigLoader = Callable[[], UserConfig]
ConfigLoader = Callable[[ProjectInfo | None, Path | None], dict[str, Any]]
RuntimeResolver = Callable[[str, ProjectInfo | None], RuntimeBinding]
HistoryWriter = Callable[[Any, list[str], set[str], datetime, int], None]
DisplayCommandResolver = Callable[[], str | None]


def _no_display_command() -> str | None:
    return None


@dataclass(frozen=True)
class CliProfile:
    """Policy boundary between the generic CLI lifecycle and its consumer.

    A profile supplies project discovery, configuration, runtime placement, and
    optional history persistence. The generic profile has no manifest convention,
    no product-owned config files, and no history writer. The legacy Base profile
    remains available temporarily so existing consumers can migrate explicitly.
    """

    discover_project: ProjectDiscovery
    load_user_config: UserConfigLoader
    load_config: ConfigLoader
    resolve_runtime: RuntimeResolver
    history_writer: HistoryWriter | None = None
    display_command: DisplayCommandResolver = _no_display_command

    @classmethod
    def generic(
        cls,
        *,
        cache_root: Path | None = None,
        application_home: Path | None = None,
        discover_project: ProjectDiscovery | None = None,
        load_user_config: UserConfigLoader | None = None,
        load_config: ConfigLoader | None = None,
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
        )

    @classmethod
    def legacy_base(cls) -> CliProfile:
        """Return the pre-profile Base behavior during the migration period."""
        from .config import load_config as load_base_config
        from .config import read_user_config
        from .history import write_finished_record
        from .paths import (
            base_cache_root,
            discover_manifest,
            normalize_runtime_owner,
            resolve_base_home,
            runtime_project_name,
            runtime_project_root,
        )

        def discover(cwd: Path) -> ProjectInfo | None:
            manifest_override = os.environ.get("BASE_CLI_PROJECT_MANIFEST")
            manifest = (
                Path(manifest_override).expanduser().resolve()
                if manifest_override
                else discover_manifest(cwd)
            )
            if manifest is None:
                return None

            name: str | None = None
            try:
                project = load_yaml_file(manifest).get("project")
                if isinstance(project, dict) and isinstance(project.get("name"), str):
                    name = project["name"]
            except (OSError, RuntimeError, ValueError):
                pass
            return ProjectInfo(root=manifest.parent, manifest=manifest, name=name)

        def resolve_runtime(cli_name: str, project: ProjectInfo | None) -> RuntimeBinding:
            runtime_owner = normalize_runtime_owner()
            selected_project_root = runtime_project_root() or (project.root if project else None)
            selected_project_name = runtime_project_name() or (
                project.name if project else (selected_project_root.name if selected_project_root else None)
            )
            inherited_run_root = os.environ.get("BASE_CLI_RUN_ROOT") if runtime_owner == "base" else None
            inherited_path = Path(inherited_run_root).expanduser().resolve() if inherited_run_root else None
            inherited_run_id = os.environ.get("BASE_CLI_RUN_ID") if inherited_path is not None else None
            run_id = inherited_run_id or (
                inherited_path.name if inherited_path is not None else make_run_id()
            )
            cache_root = base_cache_root()
            return RuntimeBinding(
                cache_root=cache_root,
                layout=runtime_layout(
                    cache_root,
                    cli_name,
                    run_id,
                    owner=runtime_owner,
                    project_name=selected_project_name,
                    project_root=selected_project_root,
                    inherited_run_root=inherited_path,
                ),
                application_home=resolve_base_home(),
                runtime_owner=runtime_owner,
                project_root=selected_project_root,
                project_name=selected_project_name,
                inherited_path=inherited_path,
                history_parent_run_id=os.environ.get("BASE_CLI_HISTORY_PARENT_RUN_ID") or None,
                run_id=run_id,
                primary_log_file=(
                    Path(os.environ["BASE_CLI_PRIMARY_LOG"]).expanduser()
                    if inherited_path is not None and os.environ.get("BASE_CLI_PRIMARY_LOG")
                    else None
                ),
                history_scope=os.environ.get(
                    "BASE_CLI_HISTORY_SCOPE",
                    "internal" if inherited_path is not None else "primary",
                ),
                write_identity=runtime_owner == "project",
            )

        return cls(
            discover_project=discover,
            load_user_config=read_user_config,
            load_config=lambda project, explicit: load_base_config(
                project.root if project is not None else None,
                explicit,
            ),
            resolve_runtime=resolve_runtime,
            history_writer=write_finished_record,
            display_command=_legacy_display_command,
        )


def _discover_no_project(_cwd: Path) -> ProjectInfo | None:
    return None


def _empty_user_config() -> UserConfig:
    return UserConfig(raw={}, ide=UserIdeConfig(enabled=None, preferences={}))


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
                project_root=project_root,
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
    root = Path.home()
    if sys.platform == "darwin":
        return root / "Library" / "Caches"
    return root / ".cache"


def _legacy_display_command() -> str | None:
    value = os.environ.get("BASE_CLI_DISPLAY_COMMAND", "").strip()
    return value or None
