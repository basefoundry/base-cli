from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from ._dependencies import require_yaml
from .errors import ConfigurationError
from .paths import config_namespace_component, default_config_root, normalize_cli_name

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
_CONFIG_MAX_BYTES = 1_048_576
_CONFIG_MAX_DEPTH = 64
_CONFIG_MAX_NODES = 100_000


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


def _validate_config_graph(value: Mapping[str, Any], *, source: str) -> None:
    """Validate nested mapping keys, cycles, depth, and traversal cost."""

    active: set[int] = set()
    stack: list[tuple[bool, Any, str, int]] = [(False, value, "", 0)]
    visited_nodes = 0
    while stack:
        exiting, current, path, depth = stack.pop()
        identity = id(current)
        if exiting:
            active.remove(identity)
            continue
        visited_nodes += 1
        if visited_nodes > _CONFIG_MAX_NODES:
            raise ConfigurationError(
                f"Configuration source {source} exceeds the maximum of {_CONFIG_MAX_NODES} nested values."
            )
        if not isinstance(current, (Mapping, list, tuple)):
            continue
        if identity in active:
            location = path or "<root>"
            raise ConfigurationError(f"Configuration source {source} contains a recursive value at '{location}'.")
        if depth > _CONFIG_MAX_DEPTH:
            location = path or "<root>"
            raise ConfigurationError(
                f"Configuration source {source} exceeds the maximum nesting depth of {_CONFIG_MAX_DEPTH} at "
                f"'{location}'."
            )
        active.add(identity)
        stack.append((True, current, path, depth))
        if isinstance(current, Mapping):
            children = list(current.items())
            for key, child in reversed(children):
                if not isinstance(key, str):
                    location = path or "<root>"
                    raise ConfigurationError(f"Configuration source {source} has a non-string key under '{location}'.")
                child_path = f"{path}.{key}" if path else key
                stack.append((False, child, child_path, depth + 1))
        else:
            for index, child in reversed(tuple(enumerate(current))):
                stack.append((False, child, f"{path}[{index}]", depth + 1))


def _merge_mapping(
    target: dict[str, Any],
    provenance: dict[str, str],
    incoming: Mapping[str, Any],
    source: str,
    *,
    prefix: str = "",
) -> None:
    _validate_config_graph(incoming, source=source)
    _merge_mapping_validated(target, provenance, incoming, source, prefix=prefix)


def _merge_mapping_validated(
    target: dict[str, Any],
    provenance: dict[str, str],
    incoming: Mapping[str, Any],
    source: str,
    *,
    prefix: str = "",
) -> None:
    for key, value in incoming.items():
        path = f"{prefix}.{key}" if prefix else key
        previous = target.get(key)
        if isinstance(previous, Mapping) and isinstance(value, Mapping):
            _merge_mapping_validated(target[key], provenance, value, source, prefix=path)
            continue
        for existing_path in tuple(provenance):
            if existing_path == path or existing_path.startswith(f"{path}."):
                del provenance[existing_path]
        target[key] = _copy_config_value(value)
        provenance.update(_leaf_provenance(value, source, path))


def _copy_config_value(value: Any) -> Any:
    """Copy nested configuration containers without preserving YAML aliases."""

    if isinstance(value, Mapping):
        return {key: _copy_config_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_copy_config_value(child) for child in value]
    if isinstance(value, tuple):
        return tuple(_copy_config_value(child) for child in value)
    return value


class BatteriesIncludedConfigLoader:
    """Load conventional user, project, environment, and explicit layers.

    ``cli_name`` is an optional identity used to derive the platform-default
    user configuration directory when ``user_config_dir`` is omitted. Pass an
    explicit directory when the consumer owns its configuration-root policy.
    """

    def __init__(
        self,
        cli_name: str | None = None,
        *,
        user_config_dir: Path | None = None,
        user_config_name: str = "config.yaml",
        project_config_name: str = ".base-cli.yaml",
        environment_dir_name: str = "environments",
        verify_project_config: bool = False,
    ) -> None:
        if _SAFE_FILENAME.fullmatch(user_config_name) is None:
            raise ValueError("user_config_name must be a simple filename")
        if _SAFE_FILENAME.fullmatch(project_config_name) is None:
            raise ValueError("project_config_name must be a simple filename")
        if _SAFE_NAME.fullmatch(environment_dir_name) is None:
            raise ValueError("environment_dir_name must be a simple directory name")
        if cli_name is not None:
            normalized_name = normalize_cli_name(cli_name)
            if not normalized_name:
                raise ValueError("cli_name must contain a non-empty command name")
        else:
            normalized_name = None
        if user_config_dir is None:
            if normalized_name is None:
                raise ValueError("either cli_name or user_config_dir must be provided")
            assert cli_name is not None
            user_config_dir = default_config_root() / config_namespace_component(cli_name)
        self.cli_name = normalized_name
        self.user_config_dir = user_config_dir.expanduser()
        self.user_config_name = user_config_name
        self.project_config_name = project_config_name
        self.environment_dir_name = environment_dir_name
        self.verify_project_config = verify_project_config

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
        if self.verify_project_config and project_path is not None and project_path.exists():
            validate_discovered_config_path(project_path, project_root)
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
        if self.verify_project_config and project_environment_path is not None and project_environment_path.exists():
            validate_discovered_config_path(project_environment_path, project_root)
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

        if environment is not None:
            # The caller selected this environment before loading its layers.
            # Keep the snapshot and provenance aligned with that authoritative
            # selection even when a base or environment file declares another
            # lifecycle value.
            _merge_mapping(merged, provenance, {"environment": selected_environment}, "command-line")

        framework_values = {key: merged[key] for key in _FRAMEWORK_KEYS if key in merged}
        framework = _validate_framework_config(framework_values)
        consumer_config = {key: value for key, value in merged.items() if key not in _FRAMEWORK_KEYS}
        return ConfigSnapshot(
            config=consumer_config,
            framework=framework,
            provenance=MappingProxyType(dict(provenance)),
        )


def validate_discovered_config_path(path: Path, root: Path | None = None) -> None:
    """Refuse implicit configuration controlled through unsafe path components."""
    paths = [path]
    if root is not None:
        parent = path.parent
        while True:
            paths.append(parent)
            if parent == root:
                break
            if parent == parent.parent:
                raise ConfigurationError(f"Discovered config '{path}' is outside project root '{root}'.")
            parent = parent.parent
    for candidate in paths:
        try:
            current = candidate.lstat()
        except OSError as exc:
            raise ConfigurationError(f"Cannot validate discovered configuration path '{candidate}': {exc}") from exc
        if stat.S_ISLNK(current.st_mode) or getattr(current, "st_file_attributes", 0) & 0x400:
            raise ConfigurationError(
                f"Refusing discovered configuration through symlink or reparse point '{candidate}'."
            )
        if os.name != "nt" and (current.st_mode & 0o002 or current.st_uid not in {0, os.getuid()}):
            raise ConfigurationError(
                f"Untrusted discovered configuration path '{candidate}': require user/root ownership "
                "and no other-write permission. Fix permissions or see the "
                "local configuration trust policy for an explicit shared-workspace opt-out."
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

    try:
        yaml = require_yaml("PyYAML is required to load the explicit CLI configuration file.")
    except RuntimeError as exc:
        raise ConfigurationError(str(exc)) from exc

    try:
        with path.open("rb") as stream:
            raw = stream.read(_CONFIG_MAX_BYTES + 1)
        if len(raw) > _CONFIG_MAX_BYTES:
            raise ConfigurationError(f"Config file '{path}' exceeds the maximum size of {_CONFIG_MAX_BYTES} bytes.")
        contents = raw.decode("utf-8")
    except FileNotFoundError as exc:
        if required:
            raise ConfigurationError(f"Config file '{path}' does not exist.") from exc
        return {}
    except OSError as exc:
        raise ConfigurationError(f"Unable to read config file '{path}': {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"Unable to read config file '{path}': {exc}") from exc
    try:
        loader = yaml.SafeLoader(contents)
        try:
            node = loader.get_single_node()
            # Inspect the composed graph before constructors expand YAML merge
            # aliases. Count repeated edges, not just distinct node identities.
            stack = [(False, node, 0, "")] if node is not None else []
            active: set[int] = set()
            nodes = 0
            while stack:
                exiting, current, depth, location = stack.pop()
                if exiting:
                    active.remove(id(current))
                    continue
                nodes += 1
                if id(current) in active:
                    raise ConfigurationError(f"Config file '{path}' contains a recursive value at '{location}'.")
                if nodes > _CONFIG_MAX_NODES:
                    raise ConfigurationError(f"Config file '{path}' exceeds YAML expansion limits.")
                if depth > _CONFIG_MAX_DEPTH:
                    raise ConfigurationError(
                        f"Config file '{path}' exceeds the maximum nesting depth of {_CONFIG_MAX_DEPTH}."
                    )
                if isinstance(current, (yaml.MappingNode, yaml.SequenceNode)):
                    active.add(id(current))
                    stack.append((True, current, depth, location))
                    if isinstance(current, yaml.MappingNode):
                        for key, child in current.value:
                            child_path = f"{location}.{key.value}" if location else str(key.value)
                            stack.append((False, child, depth + 1, child_path))
                    else:
                        stack.extend((False, child, depth + 1, location) for child in current.value)
            data = loader.construct_document(node) if node is not None else None
        finally:
            loader.dispose()
    except RecursionError as exc:
        raise ConfigurationError(
            f"Config file '{path}' exceeds the maximum nesting depth of {_CONFIG_MAX_DEPTH}."
        ) from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Config file '{path}' contains invalid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigurationError(f"Config file '{path}' must contain a YAML mapping.")
    _validate_config_graph(data, source=f"Config file '{path}'")
    return data
