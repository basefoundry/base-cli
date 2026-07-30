"""Helpers for writing private runtime files."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


PRIVATE_FILE_MODE = 0o600


def restrict_file(path: Path) -> None:
    """Ensure an existing runtime file is readable and writable only by its owner."""

    path.chmod(PRIVATE_FILE_MODE)


def write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write a JSON mapping with owner-only permissions from the moment it is created."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE_MODE)
    try:
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
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
