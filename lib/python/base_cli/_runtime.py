from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

from ._private_files import PRIVATE_DIRECTORY_MODE, restrict_directory, write_private_json
from .paths import runtime_run_directory_name, runtime_slug
from .runtime import RuntimeLayout


_LOG_INDEX_NAME = ".base-cli-log-index.json"


class RuntimeDirectoryError(RuntimeError):
    """An actionable failure to create a framework-owned runtime directory."""


# pylint: disable=too-many-arguments
def runtime_layout(
    cache_root: Path,
    cli_name: str,
    run_id: str,
    *,
    namespace: str | None = None,
    project_name: str | None = None,
    inherited_run_root: Path | None = None,
) -> RuntimeLayout:
    owner_root = runtime_namespace_root(cache_root, namespace or cli_name)
    run_root = inherited_run_root or owner_root / "runs" / runtime_run_directory_name(run_id, cli_name, project_name)
    state_dir = owner_root
    # Every public invocation owns one run bundle and one diagnostic log.
    # Child processes inherit that bundle instead of creating component logs.
    log_dir = run_root / "logs"
    return RuntimeLayout(
        owner_root=owner_root,
        run_root=run_root,
        state_dir=state_dir,
        log_dir=log_dir,
        cache_dir=owner_root / "cache" / "components" / cli_name,
        temp_dir=run_root / "tmp" / cli_name / run_id,
    )


def create_runtime_directory(path: Path, cache_root: Path) -> None:
    missing: list[Path] = []
    candidate = path
    while not candidate.exists():
        missing.append(candidate)
        candidate = candidate.parent
    restrict_permissions = _is_within(path, cache_root)
    try:
        path.mkdir(parents=True, exist_ok=True)
        if restrict_permissions and os.name != "nt":
            for directory in [path, *missing]:
                restrict_directory(directory)
    except OSError as exc:
        raise RuntimeDirectoryError(_runtime_directory_error(path, cache_root, exc)) from exc


def create_owned_runtime_directory(
    path: Path,
    cache_root: Path,
) -> tuple[tuple[int, int], int | None]:
    """Exclusively create one invocation-owned leaf and retain its stable handle."""

    create_runtime_directory(path.parent, cache_root)
    if not _supports_secure_owned_directory_creation():
        return _create_owned_runtime_directory_portable(path, cache_root)

    try:
        parent_fd = _open_absolute_directory(path.parent)
    except OSError as exc:
        raise RuntimeDirectoryError(_runtime_directory_error(path, cache_root, exc)) from exc

    leaf_fd: int | None = None
    try:
        try:
            os.mkdir(path.name, PRIVATE_DIRECTORY_MODE, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise RuntimeDirectoryError(_owned_directory_collision_error(path)) from exc

        leaf_fd = os.open(path.name, _directory_open_flags(), dir_fd=parent_fd)
        created_stat = os.fstat(leaf_fd)
        _require_current_owned_entry(parent_fd, path.name, created_stat, path)
        if _is_within(path, cache_root) and os.name != "nt":
            os.fchmod(leaf_fd, PRIVATE_DIRECTORY_MODE)
            _require_current_owned_entry(parent_fd, path.name, created_stat, path)
        return (created_stat.st_dev, created_stat.st_ino), leaf_fd
    except RuntimeDirectoryError:
        if leaf_fd is not None:
            os.close(leaf_fd)
        raise
    except OSError as exc:
        if leaf_fd is not None:
            os.close(leaf_fd)
        raise RuntimeDirectoryError(_runtime_directory_error(path, cache_root, exc)) from exc
    except BaseException:
        if leaf_fd is not None:
            os.close(leaf_fd)
        raise
    finally:
        os.close(parent_fd)


def _supports_secure_owned_directory_creation() -> bool:
    return bool(
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.mkdir in os.supports_dir_fd
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and hasattr(os, "fchmod")
    )


def _create_owned_runtime_directory_portable(
    path: Path,
    cache_root: Path,
) -> tuple[tuple[int, int], int | None]:
    """Use the strongest available binding where directory-relative APIs are absent."""

    try:
        path.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    except FileExistsError as exc:
        raise RuntimeDirectoryError(_owned_directory_collision_error(path)) from exc
    except OSError as exc:
        raise RuntimeDirectoryError(_runtime_directory_error(path, cache_root, exc)) from exc

    try:
        created_stat = os.stat(path, follow_symlinks=False)
        if not stat.S_ISDIR(created_stat.st_mode):
            raise RuntimeDirectoryError(
                f"Unable to claim invocation temp directory '{path}': it changed during creation."
            )
        if _is_within(path, cache_root) and os.name != "nt":
            restrict_directory(path)
        return (created_stat.st_dev, created_stat.st_ino), None
    except RuntimeDirectoryError:
        raise
    except OSError as exc:
        raise RuntimeDirectoryError(_runtime_directory_error(path, cache_root, exc)) from exc


def _open_absolute_directory(path: Path) -> int:
    absolute = path.resolve(strict=True)
    anchor = Path(absolute.anchor)
    descriptor = os.open(anchor, _directory_open_flags())
    try:
        for component in absolute.relative_to(anchor).parts:
            next_descriptor = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _directory_open_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _require_current_owned_entry(
    parent_fd: int,
    name: str,
    created_stat: os.stat_result,
    path: Path,
) -> None:
    current_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        not stat.S_ISDIR(current_stat.st_mode)
        or (current_stat.st_dev, current_stat.st_ino) != (created_stat.st_dev, created_stat.st_ino)
    ):
        raise RuntimeDirectoryError(
            f"Unable to claim invocation temp directory '{path}': it changed during creation."
        )


def _owned_directory_collision_error(path: Path) -> str:
    return (
        f"Unable to claim invocation temp directory '{path}': it appeared concurrently. "
        "Refusing to treat pre-existing content as framework-owned."
    )


def runtime_namespace_root(cache_root: Path, namespace: str) -> Path:
    """Return an application-owned runtime namespace without product assumptions."""
    return cache_root / runtime_slug(namespace, fallback="application")


def prune_log_files(
    log_dir: Path,
    current_log_file: Path,
    max_log_files: int,
    logger: logging.Logger,
) -> None:
    index_path = log_dir / _LOG_INDEX_NAME
    tracked = _read_log_index(index_path)
    if tracked is None:
        tracked = {path.resolve() for path in log_dir.glob("*/logs/*.log")}
    current_log_file = current_log_file.resolve()
    tracked = {path.resolve() for path in tracked}
    tracked = {path for path in tracked if path.exists() or path == current_log_file}
    tracked.add(current_log_file)
    candidates = [(path.name, path) for path in tracked if path != current_log_file]

    excess_count = len(candidates) + 1 - max_log_files
    if excess_count > 0:
        for _, path in sorted(candidates)[:excess_count]:
            try:
                path.unlink()
                tracked.discard(path)
            except OSError as exc:
                logger.warning("Could not prune log file '%s': %s", path, exc)

    tracked = {path for path in tracked if path.exists() or path == current_log_file}
    try:
        write_private_json(index_path, {"version": 1, "logs": sorted(str(path) for path in tracked)})
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("Could not update log retention index '%s': %s", index_path, exc)


def _read_log_index(path: Path) -> set[Path] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None
    logs = payload.get("logs")
    if not isinstance(logs, list) or not all(isinstance(value, str) for value in logs):
        return None
    return {Path(value) for value in logs}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _runtime_directory_error(path: Path, cache_root: Path, exc: OSError) -> str:
    return (
        f"Unable to create runtime directory '{path}': {exc}. "
        f"Check permissions on that directory. If the cache root '{cache_root}' is unusable, "
        "configure the application's cache root."
    )
