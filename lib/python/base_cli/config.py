from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

from ._dependencies import require_yaml
from .errors import ConfigurationError


__all__ = [
    "load_yaml_file",
]


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
