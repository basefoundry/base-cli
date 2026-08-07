"""Helpers for writing private runtime files."""

from __future__ import annotations

import json
import os
import secrets
import time
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
    """Atomically write a private JSON mapping.

    The temporary file is created beside the destination, written and synced
    before it is replaced.  A failed serialization, write, or replacement
    therefore leaves the previous snapshot intact instead of exposing a
    truncated metadata/index file.  On POSIX, directory-relative operations
    also prevent a swapped ancestor from redirecting the replacement through
    a symlink.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    parent_fd: int | None = None
    temporary_name: str | None = None
    fd: int | None = None
    try:
        parent_fd = _open_parent_directory(path.parent)
        for _ in range(32):
            candidate = f".{path.name}.{secrets.token_hex(12)}.tmp"
            try:
                if parent_fd is not None and _supports_dir_fd_open():
                    fd = os.open(
                        candidate,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        PRIVATE_FILE_MODE,
                        dir_fd=parent_fd,
                    )
                else:
                    fd = os.open(
                        path.parent / candidate,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        PRIVATE_FILE_MODE,
                    )
                temporary_name = candidate
                break
            except FileExistsError:
                continue
        if fd is None or temporary_name is None:
            raise OSError("unable to allocate a private JSON temporary file")

        fchmod = getattr(os, "fchmod", None)
        if os.name != "nt" and fchmod is not None:
            fchmod(fd, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        # Refuse an existing symlink as the destination.  Replacing a regular
        # file is safe and atomic; replacing a symlink entry would be safe from
        # following it, but rejecting it makes an unexpected redirection
        # explicit to callers.
        if parent_fd is not None and _supports_dir_fd_stat():
            try:
                existing = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None and _is_symlink_mode(existing.st_mode):
                raise OSError(f"refusing to replace symlink '{path}'")
            _atomic_replace_relative(parent_fd, temporary_name, path.name)
        else:
            if path.is_symlink():
                raise OSError(f"refusing to replace symlink '{path}'")
            _replace_with_retry(path.parent / temporary_name, path)
        temporary_name = None
        if parent_fd is not None:
            _sync_directory(parent_fd)
    finally:
        if fd is not None:
            os.close(fd)
        if temporary_name is not None:
            try:
                if parent_fd is not None and _supports_dir_fd_unlink():
                    os.unlink(temporary_name, dir_fd=parent_fd)
                else:
                    (path.parent / temporary_name).unlink()
            except OSError:
                pass
        if parent_fd is not None:
            os.close(parent_fd)


def _supports_dir_fd_open() -> bool:
    return os.open in os.supports_dir_fd


def _supports_dir_fd_stat() -> bool:
    return os.stat in os.supports_dir_fd and os.stat in os.supports_follow_symlinks


def _supports_dir_fd_unlink() -> bool:
    return os.unlink in os.supports_dir_fd


def _is_symlink_mode(mode: int) -> bool:
    # Avoid importing stat in the hot path and keep this helper usable on
    # platforms where the POSIX mode constants are still available.
    return (mode & 0o170000) == 0o120000


def _open_parent_directory(path: Path) -> int | None:
    """Open a parent directory without following path components on POSIX."""

    if os.name == "nt" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        return None
    # Resolve the platform's standard compatibility links (for example
    # macOS's /var -> /private/var), then bind each real component with
    # O_NOFOLLOW so a later ancestor swap cannot redirect the write.
    absolute = path.resolve(strict=True)
    descriptor = os.open(
        absolute.anchor,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        for component in absolute.relative_to(Path(absolute.anchor)).parts:
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _atomic_replace_relative(parent_fd: int, source: str, destination: str) -> None:
    if os.rename in os.supports_dir_fd:
        os.rename(source, destination, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        return
    os.replace(source, destination)


def _sync_directory(parent_fd: int) -> None:
    try:
        os.fsync(parent_fd)
    except OSError:
        # Directory fsync is not available on all supported filesystems and
        # platforms.  The file itself was still flushed before replacement.
        pass


def _replace_with_retry(source: Path, destination: Path) -> None:
    """Replace a private file, tolerating transient Windows sharing races."""

    attempts = 1 if os.name != "nt" else 10
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.001 * (attempt + 1))
