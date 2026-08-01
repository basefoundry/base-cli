from __future__ import annotations

import contextlib
import contextvars
import re
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

_WORKING_DIRECTORY_OVERRIDE: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "base_cli_working_directory_override",
    default=None,
)


def current_working_dir() -> Path:
    return _WORKING_DIRECTORY_OVERRIDE.get() or Path.cwd()


@contextlib.contextmanager
def use_working_dir(path: Path | None) -> Iterator[None]:
    if path is None:
        yield
        return

    token = _WORKING_DIRECTORY_OVERRIDE.set(path.expanduser().resolve())
    try:
        yield
    finally:
        _WORKING_DIRECTORY_OVERRIDE.reset(token)


def make_run_id() -> str:
    timestamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    return f"{timestamp}_{uuid.uuid4().hex[:8]}"


def normalize_cli_name(name: str) -> str:
    stem = Path(name).name
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    return stem.replace(" ", "-")


def runtime_slug(value: str, fallback: str = "unnamed") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip(".-_").lower()
    return normalized or fallback


def runtime_run_directory_name(run_id: str, cli_name: str, project_name: str | None = None) -> str:
    """Return a readable bundle directory name without changing the canonical run ID."""
    labels = [runtime_slug(cli_name, fallback="run")]
    if project_name:
        labels.append(runtime_slug(project_name))
    return f"{run_id}__{'__'.join(labels)}"
