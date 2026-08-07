from __future__ import annotations

import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from ._dependencies import require_yaml
from .errors import ConfigurationError

__all__ = [
    "BatteriesIncludedConfigLoader",
    "ConfigSnapshot",
    "FrameworkConfig",
    "DEFAULT_ENVIRONMENT",
    "load_yaml_file",
]


DEFAULT_ENVIRONMENT: Final = "dev"
_FRAMEWORK_KEYS = frozenset({"environment", "log_level", "keep_temp"})
_LOG_LEVELS = frozenset({"debug", "info", "warning", "error", "critical"})
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_SAFE_FILENAME = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9_.-]*|\.[A-Za-z0-9][A-Za-z0-9_.-]*)\Z")


@dataclass(frozen=True)
class FrameworkConfig:
    """Validated lifecycle settings separated from consumer configuration."""

    environment: str = DEFAULT_ENVIRONMENT
    log_level: str | None = None
    keep_temp: bool = False


@dataclass(frozen=True)
class ConfigSnapshot:
    """One deterministic layered configuration result.

    ``config`` contains only consumer-owned keys. Framework lifecycle settings
    are validated and exposed separately through ``framework``. ``provenance``
    maps dotted configuration paths to the source layer that supplied them.
    """

    config: dict[str, Any]
    framework: FrameworkConfig
    provenance: Mapping[str, str]


def _validate_framework_config(values: Mapping[str, Any]) -> FrameworkConfig:
    environment_value = values.get("environment", DEFAULT_ENVIRONMENT)
    if not isinstance(environment_value, str) or not environment_value.strip():
        raise ConfigurationError("Config key 'environment' must be a non-empty string.")
    environment = _validate_environment_name(environment_value)

    log_level_value = values.get("log_level")
    if log_level_value is not None:
        if not isinstance(log_level_value, str) or log_level_value.lower() not in _LOG_LEVELS:
            supported = ", ".join(sorted(_LOG_LEVELS))
            raise ConfigurationError(f"Config key 'log_level' must be one of: {supported}.")
        log_level = log_level_value.lower()
    else:
        log_level = None

    keep_temp_value = values.get("keep_temp", False)
    if not isinstance(keep_temp_value, bool):
        raise ConfigurationError("Config key 'keep_temp' must be a boolean.")

    return FrameworkConfig(
        environment=environment,
        log_level=log_level,
        keep_temp=keep_temp_value,
    )


def _validate_environment_name(value: object) -> str:
    if not isinstance(value, str):
        raise ConfigurationError("Config key 'environment' must be a string.")
    normalized = value.strip()
    if not normalized or _SAFE_NAME.fullmatch(normalized) is None:
        raise ConfigurationError("Config key 'environment' must contain only letters, digits, '.', '_' or '-'.")
    return normalized


def _leaf_provenance(
    value: Any,
    source: str,
    prefix: str = "",
) -> dict[str, str]:
    if isinstance(value, Mapping):
        result: dict[str, str] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.update(_leaf_provenance(child, source, path))
        return result or ({prefix: source} if prefix else {})
    return {prefix: source} if prefix else {}


def _merge_mapping(
    target: dict[str, Any],
    provenance: dict[str, str],
    incoming: Mapping[str, Any],
    source: str,
) -> None:
    for key, value in incoming.items():
        if not isinstance(key, str):
            raise ConfigurationError("Configuration keys must be strings.")
        previous = target.get(key)
        if isinstance(previous, Mapping) and isinstance(value, Mapping):
            _merge_mapping(target[key], provenance, value, source)
            provenance.update(_leaf_provenance(value, source, key))
            continue
        for path in tuple(provenance):
            if path == key or path.startswith(f"{key}."):
                del provenance[path]
        target[key] = dict(value) if isinstance(value, Mapping) else value
        provenance.update(_leaf_provenance(value, source, key))


class BatteriesIncludedConfigLoader:
    """Load conventional user, project, environment, and explicit layers."""

    def __init__(
        self,
        cli_name: str,
        *,
        user_config_dir: Path,
        user_config_name: str = "config.yaml",
        project_config_name: str = ".base-cli.yaml",
        environment_dir_name: str = "environments",
    ) -> None:
        if _SAFE_FILENAME.fullmatch(user_config_name) is None:
            raise ValueError("user_config_name must be a simple filename")
        if _SAFE_FILENAME.fullmatch(project_config_name) is None:
            raise ValueError("project_config_name must be a simple filename")
        if _SAFE_NAME.fullmatch(environment_dir_name) is None:
            raise ValueError("environment_dir_name must be a simple directory name")
        self.cli_name = cli_name
        self.user_config_dir = user_config_dir.expanduser()
        self.user_config_name = user_config_name
        self.project_config_name = project_config_name
        self.environment_dir_name = environment_dir_name

    @property
    def user_config_path(self) -> Path:
        return self.user_config_dir / self.user_config_name

    def project_config_path(self, project_root: Path | None) -> Path | None:
        if project_root is None:
            return None
        return project_root / self.project_config_name

    def _environment_paths(
        self,
        project_root: Path | None,
        environment: str,
    ) -> tuple[Path, Path | None]:
        user_path = self.user_config_dir / self.environment_dir_name / f"{environment}.yaml"
        project_path = (
            project_root / self.environment_dir_name / f"{environment}.yaml" if project_root is not None else None
        )
        return user_path, project_path

    def load(
        self,
        project_root: Path | None,
        explicit_path: Path | None,
        *,
        environment: str | None = None,
    ) -> ConfigSnapshot:
        user_values = load_yaml_file(self.user_config_path)
        project_path = self.project_config_path(project_root)
        project_values = load_yaml_file(project_path) if project_path is not None else {}
        explicit_values = load_yaml_file(explicit_path, required=True) if explicit_path is not None else {}

        selected_environment = environment
        if selected_environment is None:
            candidate = explicit_values.get("environment")
            if candidate is None:
                candidate = project_values.get("environment", user_values.get("environment"))
            selected_environment = candidate if candidate is not None else DEFAULT_ENVIRONMENT
        selected_environment = _validate_environment_name(selected_environment)

        user_environment_path, project_environment_path = self._environment_paths(
            project_root,
            selected_environment,
        )
        user_environment = load_yaml_file(user_environment_path)
        project_environment = load_yaml_file(project_environment_path) if project_environment_path is not None else {}

        merged: dict[str, Any] = {}
        provenance: dict[str, str] = {}
        _merge_mapping(merged, provenance, {"environment": DEFAULT_ENVIRONMENT}, "default")
        for source, values in (
            ("user", user_values),
            ("project", project_values),
            (f"user:environment:{selected_environment}", user_environment),
            (f"project:environment:{selected_environment}", project_environment),
            ("explicit", explicit_values),
        ):
            _merge_mapping(merged, provenance, values, source)

        framework_values = {key: merged[key] for key in _FRAMEWORK_KEYS if key in merged}
        framework = _validate_framework_config(framework_values)
        consumer_config = {key: value for key, value in merged.items() if key not in _FRAMEWORK_KEYS}
        return ConfigSnapshot(
            config=consumer_config,
            framework=framework,
            provenance=MappingProxyType(dict(provenance)),
        )


def load_yaml_file(path: Path, *, required: bool = False) -> dict[str, Any]:
    """Load a YAML mapping, optionally requiring a regular file to exist.

    Missing files remain an empty mapping by default so consumer profiles can
    use this helper for optional implicit configuration. Callers handling an
    explicitly requested file must set ``required=True``.
    """
    if required:
        try:
            mode = path.stat().st_mode
        except FileNotFoundError as exc:
            raise ConfigurationError(f"Config file '{path}' does not exist.") from exc
        except OSError as exc:
            raise ConfigurationError(f"Unable to read config file '{path}': {exc}") from exc
        if not stat.S_ISREG(mode):
            raise ConfigurationError(f"Config path '{path}' is not a regular file.")
    elif not path.is_file():
        return {}

    yaml = require_yaml("PyYAML is required to load the explicit CLI configuration file.")

    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        if required:
            raise ConfigurationError(f"Config file '{path}' does not exist.") from exc
        return {}
    except OSError as exc:
        raise ConfigurationError(f"Unable to read config file '{path}': {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"Unable to read config file '{path}': {exc}") from exc
    try:
        data = yaml.safe_load(contents)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Config file '{path}' contains invalid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigurationError(f"Config file '{path}' must contain a YAML mapping.")
    return data
