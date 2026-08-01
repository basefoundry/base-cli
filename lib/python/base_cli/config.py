from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._dependencies import require_yaml


__all__ = [
    "UserConfig",
    "UserGithubConfig",
    "UserIdeConfig",
    "UserIdePreference",
    "UserWorkspaceConfig",
    "load_yaml_file",
]


@dataclass(frozen=True)
class UserIdePreference:
    enabled: bool | None
    install: bool | None
    extra_extensions: tuple[str, ...]
    settings: dict[str, Any]


@dataclass(frozen=True)
class UserIdeConfig:
    enabled: bool | None
    preferences: dict[str, UserIdePreference]


@dataclass(frozen=True)
class UserWorkspaceConfig:
    root: Path | None
    manifest: Path | None = None
    manifest_source: str | None = None


@dataclass(frozen=True)
class UserGithubConfig:
    default_owner: str | None
    clone_protocol: str | None


@dataclass(frozen=True)
class UserConfig:
    raw: dict[str, Any]
    ide: UserIdeConfig
    workspace: UserWorkspaceConfig = UserWorkspaceConfig(root=None)
    github: UserGithubConfig = UserGithubConfig(default_owner=None, clone_protocol=None)


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}

    yaml = require_yaml("PyYAML is required to load the explicit CLI configuration file.")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Config file '{path}' contains invalid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file '{path}' must contain a YAML mapping.")
    return data
