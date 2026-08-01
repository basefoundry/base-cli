from __future__ import annotations

from pathlib import Path
from typing import Any

from ._dependencies import require_yaml


__all__ = [
    "load_yaml_file",
]


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
