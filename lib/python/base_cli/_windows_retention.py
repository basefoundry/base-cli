"""Windows bundle removal with pinned, non-reparse directory components."""

from __future__ import annotations

import ctypes
import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path


def _check_directory(path: Path, volume: int | None = None) -> os.stat_result:
    current = path.lstat()
    if not stat.S_ISDIR(current.st_mode) or getattr(current, "st_file_attributes", 0) & 0x400:
        raise OSError(f"refusing non-directory or reparse point '{path}'")
    if volume is not None and current.st_dev != volume:
        raise OSError(f"refusing volume boundary at '{path}'")
    return current


@contextmanager
def _pin_directory(path: Path, volume: int | None = None) -> Iterator[os.stat_result]:
    # OPEN_REPARSE_POINT + BACKUP_SEMANTICS opens the directory itself. Omitting
    # SHARE_DELETE/SHARE_WRITE prevents rename/deletion and conflicting writers
    # while we inspect and descend. Keep every ancestor pinned until completion.
    # https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-createfilew
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    before = _check_directory(path, volume)
    handle = create(str(path), 0x80, 0x1, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), f"cannot pin retention directory '{path}'")  # type: ignore[attr-defined]
    try:
        current = _check_directory(path, volume)
        if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            raise OSError(f"retention directory identity changed: '{path}'")
        yield current
    finally:
        close(handle)


def _remove_tree(path: Path, volume: int) -> None:
    with _pin_directory(path, volume):
        with os.scandir(path) as entries:
            for entry in entries:
                child = path / entry.name
                current = child.lstat()
                if current.st_dev != volume or getattr(current, "st_file_attributes", 0) & 0x400:
                    raise OSError(f"refusing volume boundary or reparse point '{child}'")
                if stat.S_ISDIR(current.st_mode):
                    _remove_tree(child, volume)
                else:
                    # unlink never follows a replacement symlink. All ancestors
                    # remain pinned; a replacement directory makes unlink fail.
                    child.unlink()
    # The handle must close before rmdir. This operation only removes an empty
    # directory (or the junction itself); it cannot descend into a replacement.
    path.rmdir()


def remove_bundle(runs_root: Path, path: Path) -> None:
    root = Path(os.path.abspath(runs_root))
    target = Path(os.path.abspath(path))
    if target.parent != root or target.name in {"", ".", ".."}:
        raise OSError(f"refusing bundle outside '{root}'")
    anchor = Path(root.anchor)
    with ExitStack() as stack:
        volume = stack.enter_context(_pin_directory(anchor)).st_dev
        parent = anchor
        for component in root.relative_to(anchor).parts:
            parent = parent / component
            stack.enter_context(_pin_directory(parent, volume))
        _remove_tree(target, volume)
