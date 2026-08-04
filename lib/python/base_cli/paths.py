from __future__ import annotations

import contextlib
import contextvars
import os
import re
import sys
import time
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path

_WORKING_DIRECTORY_OVERRIDE: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "base_cli_working_directory_override",
    default=None,
)


def default_cache_root(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform_name: str | None = None,
) -> Path:
    """Return the platform-default cache root for the generic profile.

    ``BASE_CLI_CACHE_DIR`` always wins so consumers and tests can provide an
    explicit location. Linux follows ``XDG_CACHE_HOME`` when it is set,
    macOS uses ``Library/Caches``, and Windows uses ``LOCALAPPDATA``.
    """

    environment = os.environ if environ is None else environ
    configured = environment.get("BASE_CLI_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()

    root = home.expanduser() if home is not None else Path.home()
    system = platform_name or sys.platform
    if system == "darwin":
        return root / "Library" / "Caches"
    if system.startswith("win"):
        local_app_data = environment.get("LOCALAPPDATA")
        return Path(local_app_data).expanduser() if local_app_data else root / "AppData" / "Local"

    xdg_cache_home = environment.get("XDG_CACHE_HOME")
    if xdg_cache_home:
        return Path(xdg_cache_home).expanduser()
    return root / ".cache"


def default_config_root(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform_name: str | None = None,
) -> Path:
    """Return the platform-default root for optional user configuration."""

    environment = os.environ if environ is None else environ
    configured = environment.get("BASE_CLI_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()

    root = home.expanduser() if home is not None else Path.home()
    system = platform_name or sys.platform
    if system == "darwin":
        return root / "Library" / "Application Support"
    if system.startswith("win"):
        app_data = environment.get("APPDATA")
        return Path(app_data).expanduser() if app_data else root / "AppData" / "Roaming"

    xdg_config_home = environment.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser()
    return root / ".config"


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
