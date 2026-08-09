"""Optional dependency loaders shared by base_cli modules."""

from __future__ import annotations

from typing import Any


def require_yaml(error_message: str) -> Any:
    """Import PyYAML or explain how to enable the optional YAML feature."""

    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            f"{error_message} Install the optional dependency with `python -m pip install 'base-cli[yaml]'`."
        ) from exc
    return yaml
