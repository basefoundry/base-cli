from __future__ import annotations

from pathlib import Path
from typing import Any

from ._dependencies import require_yaml
from .errors import ConfigurationError


__all__ = [
    "load_yaml_file",
]


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}

    yaml = require_yaml("PyYAML is required to load the explicit CLI configuration file.")

    try:
        contents = path.read_text(encoding="utf-8")
    except OSError as exc:
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
