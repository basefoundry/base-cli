"""Validated process-boundary propagation for runtime identity.

The environment is a transport, not an authority. Values received from it are
validated before they influence runtime state or filesystem paths.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ._runtime import _open_absolute_directory
from .errors import ConfigurationError

RUN_ID_ENV = "BASE_CLI_RUN_ID"
PARENT_RUN_ID_ENV = "BASE_CLI_PARENT_RUN_ID"
RUN_ROOT_ENV = "BASE_CLI_RUN_ROOT"
RUN_SCOPE_ENV = "BASE_CLI_RUN_SCOPE"
PROPAGATE_ENV = "BASE_CLI_PROPAGATE"
TRACEPARENT_ENV = "TRACEPARENT"
TRACESTATE_ENV = "TRACESTATE"

_SAFE_RUN_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
_VALID_SCOPES = frozenset({"primary", "internal"})


@dataclass(frozen=True)
class RuntimePropagation:
    """Validated values received across a process boundary."""

    run_id: str | None = None
    parent_run_id: str | None = None
    run_root: Path | None = None
    scope: str | None = None


def read_runtime_propagation(environ: Mapping[str, str] | None = None) -> RuntimePropagation:
    """Read and validate the base-cli runtime environment contract."""

    values = os.environ if environ is None else environ
    if not _is_enabled(values.get(PROPAGATE_ENV)):
        return RuntimePropagation()

    run_id = _optional_run_id(values.get(RUN_ID_ENV), RUN_ID_ENV)
    parent_run_id = _optional_run_id(values.get(PARENT_RUN_ID_ENV), PARENT_RUN_ID_ENV)
    raw_root = _optional_text(values.get(RUN_ROOT_ENV))
    scope = _optional_text(values.get(RUN_SCOPE_ENV))
    if scope is not None:
        scope = scope.lower()
        if scope not in _VALID_SCOPES:
            raise ConfigurationError(f"{RUN_SCOPE_ENV} must be 'primary' or 'internal', not {scope!r}.")
    if raw_root is not None and run_id is None and parent_run_id is None:
        raise ConfigurationError(
            f"{RUN_ROOT_ENV} requires {RUN_ID_ENV} or {PARENT_RUN_ID_ENV}; "
            "a runtime path cannot be trusted without an identity."
        )
    run_root = _validate_run_root(raw_root) if raw_root is not None else None
    return RuntimePropagation(
        run_id=run_id,
        parent_run_id=parent_run_id,
        run_root=run_root,
        scope=scope,
    )


def validate_run_root(run_root: Path, expected_run_id: str | None = None) -> Path:
    """Validate a parent run bundle supplied by a process boundary.

    The path must be absolute, an actual directory (not a symlink), and carry
    a private ``run.json`` whose identity matches the supplied run ID when one
    is available. The returned path is resolved and safe to use as an
    inherited runtime root.
    """

    candidate = Path(run_root).expanduser()
    if not candidate.is_absolute():
        raise ConfigurationError(f"{RUN_ROOT_ENV} must be an absolute path.")
    if candidate != candidate.resolve(strict=False):
        raise ConfigurationError(f"{RUN_ROOT_ENV} must not contain symlinked path components.")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ConfigurationError(f"{RUN_ROOT_ENV} does not point to a readable directory: {exc}") from exc
    if not resolved.is_dir() or resolved.is_symlink():
        raise ConfigurationError(f"{RUN_ROOT_ENV} must point to a real directory.")

    try:
        payload = _read_run_metadata(resolved)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"{RUN_ROOT_ENV} has no valid run metadata.") from exc
    metadata_run_id = payload.get("run_id") if isinstance(payload, dict) else None
    if not isinstance(metadata_run_id, str) or not _is_safe_run_id(metadata_run_id):
        raise ConfigurationError(f"{RUN_ROOT_ENV} has invalid run metadata.")
    if expected_run_id is not None and metadata_run_id != expected_run_id:
        raise ConfigurationError(f"{RUN_ROOT_ENV} belongs to run {metadata_run_id!r}, not {expected_run_id!r}.")
    return resolved


def _read_run_metadata(run_root: Path) -> object:
    """Read run metadata through a stable directory handle when available."""

    if _supports_secure_directory_reads():
        root_fd = _open_absolute_directory(run_root)
        metadata_fd: int | None = None
        try:
            flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            metadata_fd = os.open("run.json", flags, dir_fd=root_fd)
            metadata_stat = os.fstat(metadata_fd)
            if not stat.S_ISREG(metadata_stat.st_mode):
                raise OSError("run.json is not a regular file")
            with os.fdopen(metadata_fd, "r", encoding="utf-8") as stream:
                metadata_fd = None
                return json.load(stream)
        finally:
            if metadata_fd is not None:
                os.close(metadata_fd)
            os.close(root_fd)

    metadata_path = run_root / "run.json"
    if metadata_path.is_symlink() or not metadata_path.is_file():
        raise OSError("run.json is not a regular file")
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def _supports_secure_directory_reads() -> bool:
    return (
        os.name != "nt" and hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd
    )


def _validate_run_root(raw_root: str) -> Path:
    return validate_run_root(Path(raw_root))


def _optional_run_id(value: str | None, variable: str) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    if not _is_safe_run_id(text):
        raise ConfigurationError(
            f"{variable} must be a single filesystem-safe run ID containing only letters, digits, '.', '_' or '-'."
        )
    return text


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _is_safe_run_id(value: str) -> bool:
    return bool(value) and ".." not in value and all(character in _SAFE_RUN_ID_CHARS for character in value)


def _is_enabled(value: str | None) -> bool:
    return value is None or value.strip().lower() not in {"0", "false", "no", "off"}


__all__ = [
    "PARENT_RUN_ID_ENV",
    "PROPAGATE_ENV",
    "RUN_ID_ENV",
    "RUN_ROOT_ENV",
    "RUN_SCOPE_ENV",
    "TRACEPARENT_ENV",
    "TRACESTATE_ENV",
    "RuntimePropagation",
    "read_runtime_propagation",
    "validate_run_root",
]
