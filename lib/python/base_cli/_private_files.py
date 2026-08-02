"""Helpers for writing private runtime files."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


PRIVATE_FILE_MODE = 0o600
PRIVATE_DIRECTORY_MODE = 0o700


def restrict_file(path: Path) -> None:
    """Apply owner-only POSIX permissions where mode bits are meaningful.

    Windows inherits ACLs from the containing directory instead; the generic
    package deliberately does not pretend that ``chmod`` can rewrite them.
    """

    if os.name != "nt":
        path.chmod(PRIVATE_FILE_MODE)


def restrict_directory(path: Path) -> None:
    """Apply owner-only POSIX directory permissions when supported."""

    if os.name != "nt":
        path.chmod(PRIVATE_DIRECTORY_MODE)


def write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write a JSON mapping with owner-only permissions from the moment it is created."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE_MODE)
    try:
        fchmod = getattr(os, "fchmod", None)
        if os.name != "nt" and fchmod is not None:
            fchmod(fd, PRIVATE_FILE_MODE)
        stream = os.fdopen(fd, "w", encoding="utf-8")
        fd = -1
        with stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
    finally:
        if fd != -1:
            os.close(fd)
    restrict_file(path)
