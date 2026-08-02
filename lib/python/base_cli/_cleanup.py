from __future__ import annotations

import errno
import os
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


class UnsafeCleanupPathError(RuntimeError):
    """Raised when a recursive cleanup target cannot be proven safe."""


@dataclass(frozen=True)
class _ValidatedCleanup:
    root: Path
    relative: Path
    root_identity: tuple[int, int]
    target_identity: tuple[int, int]


def remove_owned_temp_directory(
    temp_dir: Path,
    run_root: Path | None,
    run_id: str,
    *,
    expected_identity: tuple[int, int],
    owned_descriptor: int | None,
    before_remove: Callable[[], None] | None = None,
) -> None:
    """Erase an invocation-owned temp tree through its retained directory handle.

    The empty leaf itself is intentionally retained: portable POSIX APIs cannot
    atomically bind a directory removal to an already-verified inode.
    """

    cleanup = _validated_cleanup_paths(temp_dir, run_root, run_id, expected_identity)
    if cleanup is None:
        return
    if before_remove is not None:
        before_remove()
    if not _supports_fd_relative_cleanup():
        raise UnsafeCleanupPathError(
            "platform does not provide the directory-handle operations required for safe recursive cleanup"
        )
    if owned_descriptor is None:
        raise UnsafeCleanupPathError("the invocation-owned temp directory handle is unavailable")
    _remove_with_directory_handles(cleanup, owned_descriptor)


def _validated_cleanup_paths(
    temp_dir: Path,
    run_root: Path | None,
    run_id: str,
    expected_identity: tuple[int, int],
) -> _ValidatedCleanup | None:
    if run_root is None:
        raise UnsafeCleanupPathError("run root is unavailable")

    target_path = Path(temp_dir)
    root_path = Path(run_root)
    if _contains_parent_reference(target_path) or _contains_parent_reference(root_path):
        raise UnsafeCleanupPathError("path traversal is not allowed")
    if _is_root_like(target_path) or _is_root_like(root_path):
        raise UnsafeCleanupPathError("filesystem-root cleanup targets are not allowed")
    if not _is_single_path_component(run_id):
        raise UnsafeCleanupPathError("run ID is not a valid ownership marker")
    if target_path.name != run_id:
        raise UnsafeCleanupPathError("temp directory does not carry the run ID ownership marker")

    target = _lexical_absolute(target_path)
    root = _lexical_absolute(root_path)
    if _is_root_like(target) or _is_root_like(root):
        raise UnsafeCleanupPathError("filesystem-root cleanup targets are not allowed")

    relative = _strict_relative_path(target, root, resolved=False)
    if not _inspect_directory_chain(root, relative):
        return None

    try:
        resolved_root = root.resolve(strict=True)
        resolved_target = target.resolve(strict=True)
    except OSError as exc:
        raise UnsafeCleanupPathError("cleanup path could not be resolved safely") from exc
    resolved_relative = _strict_relative_path(resolved_target, resolved_root, resolved=True)
    if resolved_relative.parts != relative.parts:
        raise UnsafeCleanupPathError("resolved cleanup path does not match its lexical run-root path")
    if _is_root_like(resolved_target) or _is_root_like(resolved_root):
        raise UnsafeCleanupPathError("resolved filesystem-root cleanup targets are not allowed")
    if _is_mount_target(target):
        raise UnsafeCleanupPathError("mounted temp directories are not recursive cleanup targets")

    root_stat = os.stat(resolved_root, follow_symlinks=False)
    target_stat = os.stat(resolved_target, follow_symlinks=False)
    _require_identity(
        target_stat,
        expected_identity,
        "temp directory no longer matches the invocation-owned directory",
    )
    return _ValidatedCleanup(
        root=resolved_root,
        relative=relative,
        root_identity=_identity(root_stat),
        target_identity=expected_identity,
    )


def _supports_fd_relative_cleanup() -> bool:
    return bool(
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and os.unlink in os.supports_dir_fd
        and os.scandir in os.supports_fd
    )


def _remove_with_directory_handles(cleanup: _ValidatedCleanup, owned_descriptor: int) -> None:
    root_fd = _open_absolute_directory(cleanup.root)
    descriptors = [root_fd]
    try:
        root_stat = os.fstat(root_fd)
        _require_identity(root_stat, cleanup.root_identity, "run root changed during cleanup validation")
        root_device = root_stat.st_dev
        root_mount = _required_mount_identity(root_fd)

        for component in cleanup.relative.parts[:-1]:
            child_fd = _open_child_directory(descriptors[-1], component)
            try:
                child_stat = os.fstat(child_fd)
                if _crosses_mount(child_fd, child_stat, root_device, root_mount):
                    raise UnsafeCleanupPathError("cleanup path crosses a mounted filesystem")
            except BaseException:
                os.close(child_fd)
                raise
            descriptors.append(child_fd)

        target_name = cleanup.relative.parts[-1]
        owned_stat = os.fstat(owned_descriptor)
        _require_identity(
            owned_stat,
            cleanup.target_identity,
            "retained temp directory handle no longer matches invocation ownership",
        )
        _require_current_entry(descriptors[-1], target_name, owned_stat)
        if _crosses_mount(owned_descriptor, owned_stat, root_device, root_mount):
            raise UnsafeCleanupPathError("cleanup target crosses a mounted filesystem")
        _remove_directory_contents(owned_descriptor, root_device, root_mount)
        _require_identity(
            os.fstat(owned_descriptor),
            cleanup.target_identity,
            "retained temp directory handle changed during cleanup",
        )
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _open_absolute_directory(path: Path) -> int:
    anchor = Path(path.anchor)
    descriptor = os.open(anchor, _directory_open_flags())
    try:
        for component in path.relative_to(anchor).parts:
            next_descriptor = _open_child_directory(descriptor, component)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_child_directory(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, _directory_open_flags(), dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise UnsafeCleanupPathError("symlink cleanup targets are not allowed") from exc
        raise


def _directory_open_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _remove_directory_contents(
    directory_fd: int,
    root_device: int,
    root_mount: str | None,
) -> None:
    with os.scandir(directory_fd) as entries:
        names = [entry.name for entry in entries]
    for name in names:
        try:
            child_fd = _open_child_directory(directory_fd, name)
        except UnsafeCleanupPathError:
            entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(entry_stat.st_mode):
                raise
            os.unlink(name, dir_fd=directory_fd)
            continue

        try:
            child_stat = os.fstat(child_fd)
            if _crosses_mount(child_fd, child_stat, root_device, root_mount):
                raise UnsafeCleanupPathError("cleanup tree contains a mounted filesystem")
            _remove_directory_contents(child_fd, root_device, root_mount)
        finally:
            os.close(child_fd)


def _require_current_entry(parent_fd: int, name: str, opened_stat: os.stat_result) -> None:
    try:
        current_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise UnsafeCleanupPathError("cleanup directory changed while it was open") from exc
    if not stat.S_ISDIR(current_stat.st_mode) or _identity(current_stat) != _identity(opened_stat):
        raise UnsafeCleanupPathError("cleanup directory changed while it was open")


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _require_identity(value: os.stat_result, expected: tuple[int, int], message: str) -> None:
    if _identity(value) != expected or not stat.S_ISDIR(value.st_mode):
        raise UnsafeCleanupPathError(message)


def _crosses_mount(
    descriptor: int,
    value: os.stat_result,
    root_device: int,
    root_mount: str | None,
) -> bool:
    if value.st_dev != root_device:
        return True
    mount = _mount_identity(descriptor)
    if _requires_mount_identity() and (root_mount is None or mount is None):
        raise UnsafeCleanupPathError("Linux mount identity is unavailable; refusing recursive cleanup")
    return root_mount is not None and mount is not None and mount != root_mount


def _required_mount_identity(descriptor: int) -> str | None:
    mount = _mount_identity(descriptor)
    if _requires_mount_identity() and mount is None:
        raise UnsafeCleanupPathError("Linux mount identity is unavailable; refusing recursive cleanup")
    return mount


def _requires_mount_identity() -> bool:
    return sys.platform.startswith("linux")


def _mount_identity(descriptor: int) -> str | None:
    """Return Linux's stable mount ID for an open directory when available."""

    try:
        with open(f"/proc/self/fdinfo/{descriptor}", encoding="utf-8") as handle:
            for line in handle:
                key, separator, value = line.partition(":")
                if separator and key == "mnt_id":
                    return value.strip() or None
    except OSError:
        return None
    return None


def _contains_parent_reference(path: Path) -> bool:
    return ".." in path.parts or ".." in PureWindowsPath(os.fspath(path)).parts


def _is_single_path_component(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    native_path = Path(value)
    windows_path = PureWindowsPath(value)
    return (
        not native_path.anchor
        and not windows_path.anchor
        and native_path.parts == (value,)
        and windows_path.parts == (value,)
    )


def _lexical_absolute(path: Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError) as exc:
        raise UnsafeCleanupPathError("cleanup path could not be normalized") from exc


def _is_root_like(path: Path) -> bool:
    if path.anchor and path == Path(path.anchor):
        return True
    windows_path = PureWindowsPath(os.fspath(path))
    return bool(windows_path.anchor) and windows_path == PureWindowsPath(windows_path.anchor)


def _strict_relative_path(target: Path, root: Path, *, resolved: bool) -> Path:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        qualifier = "resolved " if resolved else ""
        raise UnsafeCleanupPathError(f"temp directory is outside the {qualifier}run root") from exc
    if relative == Path("."):
        qualifier = "resolved " if resolved else ""
        raise UnsafeCleanupPathError(f"temp directory equals the {qualifier}run root")
    return relative


def _inspect_directory_chain(root: Path, relative: Path) -> bool:
    current = root
    for index, component in enumerate(("", *relative.parts)):
        if index:
            current /= component
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise UnsafeCleanupPathError("cleanup path could not be inspected safely") from exc
        if stat.S_ISLNK(mode):
            raise UnsafeCleanupPathError("symlink cleanup targets are not allowed")
        if not stat.S_ISDIR(mode):
            raise UnsafeCleanupPathError("cleanup target path contains a non-directory")
    return True


def _is_mount_target(path: Path) -> bool:
    try:
        return os.path.ismount(path)
    except (OSError, ValueError) as exc:
        raise UnsafeCleanupPathError("cleanup target mount status could not be inspected") from exc
