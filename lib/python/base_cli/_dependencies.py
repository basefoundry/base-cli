"""Optional dependency loaders shared by base_cli modules."""

from __future__ import annotations

from typing import Any


def require_yaml(error_message: str) -> Any:
    """Import PyYAML or raise the caller's feature-specific error."""

    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(error_message) from exc
    return yaml
