from __future__ import annotations

import json
import logging
import os
import stat
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator
from pathlib import Path

try:  # pragma: no cover - platform branch
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - platform branch
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX
    _msvcrt = None  # type: ignore[assignment]

from ._private_files import (
    PRIVATE_DIRECTORY_MODE,
    restrict_directory,
    restrict_file,
    write_private_json,
)
from .paths import runtime_run_directory_name, runtime_slug
from .runtime import RetentionPolicy, RuntimeLayout


_LOG_INDEX_NAME = ".base-cli-log-index.json"
_RUN_INDEX_NAME = ".base-cli-run-index.json"
_RUN_LOCK_NAME = ".base-cli-run-index.lock"


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
    # Modern runtimes have one ``run.json`` per direct child and must be
    # pruned as complete bundles.  Keep this legacy helper available for old
    # flat log directories, but never let it split a metadata-backed bundle.
    try:
        if any(
            (child / "run.json").is_file()
            for child in log_dir.iterdir()
            if child.is_dir() and not child.is_symlink()
        ):
            return
    except OSError:
        return
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


def prune_run_bundles(
    runs_root: Path,
    current_run_root: Path | None = None,
    *,
    policy: RetentionPolicy | None = None,
    max_bundles: int | None = None,
    max_age_seconds: float | None = None,
    max_total_bytes: int | None = None,
    protected_run_roots: Iterable[Path] = (),
    logger: logging.Logger | None = None,
    now: float | None = None,
) -> None:
    """Prune complete invocation bundles under ``runs_root``.

    A bundle is a direct child directory containing valid ``run.json``
    metadata in a terminal state.  The metadata file and the retention index
    are both updated atomically.  We intentionally scan the filesystem while
    holding a sidecar lock rather than trusting the index for deletion: a
    damaged or stale index can never cause an unrelated path to be removed.

    Running bundles are protected while active.  A running bundle is eligible
    for crash recovery only when an age bound is configured and it is older
    than that bound; the current and explicitly protected roots remain safe.
    """

    log = logger or logging.getLogger(__name__)
    if policy is not None:
        if any(value is not None for value in (max_bundles, max_age_seconds, max_total_bytes)):
            raise ValueError("pass either policy or individual retention bounds, not both")
        max_bundles = policy.max_bundles
        max_age_seconds = policy.max_age_seconds
        max_total_bytes = policy.max_total_bytes
    effective = RetentionPolicy(max_bundles, max_age_seconds, max_total_bytes)
    if effective.max_bundles is None and effective.max_age_seconds is None and effective.max_total_bytes is None:
        return

    runs_root = Path(runs_root)
    if not runs_root.exists() or runs_root.is_symlink():
        return
    protected = {_safe_resolved_path(path) for path in protected_run_roots}
    if current_run_root is not None:
        protected.add(_safe_resolved_path(current_run_root))
    clock = time.time() if now is None else now

    try:
        with _retention_lock(runs_root):
            bundles = _discover_run_bundles(
                runs_root,
                protected=protected,
                max_age_seconds=effective.max_age_seconds,
                now=clock,
            )
            _apply_bundle_retention(
                runs_root,
                bundles,
                policy=effective,
                protected=protected,
                logger=log,
                now=clock,
                reserved_active_bundles=1 if current_run_root is not None else 0,
            )
            _write_run_index(
                runs_root,
                bundles,
                log,
                current_run_root=current_run_root,
                now=clock,
            )
    except (OSError, RuntimeError) as exc:
        # Retention is maintenance.  An unavailable lock or a transient
        # filesystem failure must not turn an otherwise valid invocation into
        # a command failure, and the old bundle remains untouched.
        log.warning("Could not update run bundle retention under '%s': %s", runs_root, exc)


def refresh_run_bundle_index(
    runs_root: Path,
    *,
    logger: logging.Logger | None = None,
) -> None:
    """Refresh the diagnostic bundle index after a run becomes terminal."""

    log = logger or logging.getLogger(__name__)
    runs_root = Path(runs_root)
    if not runs_root.exists() or runs_root.is_symlink():
        return
    try:
        with _retention_lock(runs_root):
            bundles = _discover_run_bundles(
                runs_root,
                protected=set(),
                max_age_seconds=None,
                now=time.time(),
            )
            _write_run_index(runs_root, bundles, log)
    except (OSError, RuntimeError) as exc:
        log.debug("Could not refresh run bundle index under '%s': %s", runs_root, exc)


def _discover_run_bundles(
    runs_root: Path,
    *,
    protected: set[Path],
    max_age_seconds: float | None,
    now: float,
) -> list[dict[str, Any]]:
    bundles: list[dict[str, Any]] = []
    try:
        children = sorted(runs_root.iterdir(), key=lambda path: path.name)
    except OSError:
        return bundles
    for child in children:
        if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
            continue
        metadata = _read_bundle_metadata(child)
        if metadata is None:
            # Partial startup directories are deliberately not considered
            # complete.  They can be diagnosed or cleaned by a consumer's
            # explicit maintenance command without retention guessing.
            continue
        status = str(metadata.get("status", ""))
        started_at = _timestamp_to_epoch(metadata.get("started_at"))
        if started_at is None:
            try:
                started_at = child.stat().st_mtime
            except OSError:
                continue
        age = max(0.0, now - started_at)
        running = status == "running"
        stale_running = running and max_age_seconds is not None and age >= max_age_seconds
        if running and not stale_running:
            continue
        if status not in {"running", "ok", "error"}:
            continue
        resolved = _safe_resolved_path(child)
        try:
            size = _bundle_size(child)
        except OSError:
            continue
        retention_metadata = metadata.get("retention")
        preserve = bool(metadata.get("preserve")) or (
            isinstance(retention_metadata, dict) and retention_metadata.get("preserve") is True
        )
        bundles.append(
            {
                "path": child,
                "resolved": resolved,
                "run_id": metadata.get("run_id"),
                "status": status,
                "started_at": started_at,
                "age": age,
                "size": size,
                "preserve": preserve,
                "protected": resolved in protected,
            }
        )
    bundles.sort(key=lambda bundle: (float(bundle["started_at"]), str(bundle["path"])))
    return bundles


def _apply_bundle_retention(
    runs_root: Path,
    bundles: list[dict[str, Any]],
    *,
    policy: RetentionPolicy,
    protected: set[Path],
    logger: logging.Logger,
    now: float,
    reserved_active_bundles: int,
) -> None:
    del now  # retained for a stable extension point in policy implementations
    removable = [
        bundle
        for bundle in bundles
        if not bool(bundle["protected"])
        and not bool(bundle["preserve"])
        and _safe_resolved_path(bundle["path"]) not in protected
    ]
    removable.sort(key=lambda bundle: (float(bundle["started_at"]), str(bundle["path"])))

    def remove(bundle: dict[str, Any]) -> bool:
        path = Path(bundle["path"])
        try:
            _remove_run_bundle(runs_root, path)
        except OSError as exc:
            logger.warning("Could not prune run bundle '%s': %s", path, exc)
            removable.remove(bundle)
            return False
        bundles.remove(bundle)
        removable.remove(bundle)
        return True

    if policy.max_age_seconds is not None:
        for bundle in list(removable):
            if float(bundle["age"]) >= policy.max_age_seconds:
                remove(bundle)

    if policy.max_bundles is not None:
        while len(bundles) + reserved_active_bundles > policy.max_bundles and removable:
            remove(removable[0])

    if policy.max_total_bytes is not None:
        total = sum(int(bundle["size"]) for bundle in bundles)
        while total > policy.max_total_bytes and removable:
            candidate = removable[0]
            if remove(candidate):
                total -= int(candidate["size"])


def _write_run_index(
    runs_root: Path,
    bundles: list[dict[str, Any]],
    logger: logging.Logger,
    *,
    current_run_root: Path | None = None,
    now: float | None = None,
) -> None:
    indexed = list(bundles)
    if current_run_root is not None and current_run_root.exists():
        current_resolved = _safe_resolved_path(current_run_root)
        if not any(_safe_resolved_path(Path(bundle["path"])) == current_resolved for bundle in indexed):
            indexed.append(
                {
                    "path": current_run_root,
                    "run_id": current_run_root.name,
                    "status": "running",
                    "started_at": time.time() if now is None else now,
                    "size": _bundle_size(current_run_root),
                    "preserve": False,
                }
            )
    index_path = runs_root / _RUN_INDEX_NAME
    payload = {
        "version": 1,
        "bundles": [
            {
                "path": str(bundle["path"]),
                "run_id": bundle["run_id"],
                "status": bundle["status"],
                "started_at": bundle["started_at"],
                "size": bundle["size"],
                "preserve": bool(bundle["preserve"]),
            }
            for bundle in indexed
        ],
    }
    try:
        write_private_json(index_path, payload)
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("Could not update run bundle retention index '%s': %s", index_path, exc)


def _read_bundle_metadata(path: Path) -> dict[str, object] | None:
    metadata_path = path / "run.json"
    try:
        metadata_stat = metadata_path.stat()
        if not stat.S_ISREG(metadata_stat.st_mode) or metadata_path.is_symlink():
            return None
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _timestamp_to_epoch(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _bundle_size(path: Path) -> int:
    total = 0
    for root, directories, files in os.walk(path, topdown=True, followlinks=False):
        for name in [*directories, *files]:
            child = Path(root) / name
            try:
                total += child.lstat().st_size
            except OSError:
                continue
    try:
        total += path.lstat().st_size
    except OSError:
        pass
    return total


def _remove_run_bundle(runs_root: Path, path: Path) -> None:
    """Remove one direct child using descriptor-relative, no-follow operations."""

    root_fd = _open_directory_nofollow(runs_root)
    try:
        candidate = Path(path).name
        if Path(path).parent.resolve() != runs_root.resolve():
            raise OSError(f"refusing to prune run bundle outside '{runs_root}'")
        current = os.stat(candidate, dir_fd=root_fd, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode):
            raise OSError(f"refusing to prune non-directory run bundle '{path}'")
        _remove_tree_at(root_fd, candidate)
    finally:
        os.close(root_fd)


def _remove_tree_at(parent_fd: int, name: str) -> None:
    child_fd = os.open(name, _directory_open_flags(), dir_fd=parent_fd)
    try:
        for entry in list(os.scandir(child_fd)):
            entry_stat = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(entry_stat.st_mode):
                _remove_tree_at(child_fd, entry.name)
            else:
                os.unlink(entry.name, dir_fd=child_fd)
    finally:
        os.close(child_fd)
    os.rmdir(name, dir_fd=parent_fd)


def _open_directory_nofollow(path: Path) -> int:
    absolute = path.resolve(strict=True)
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        return os.open(absolute, os.O_RDONLY)
    descriptor = os.open(
        absolute.anchor,
        _directory_open_flags(),
    )
    try:
        for component in absolute.relative_to(Path(absolute.anchor)).parts:
            next_descriptor = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _safe_resolved_path(path: Path) -> Path:
    try:
        return Path(path).resolve(strict=False)
    except OSError:
        return Path(path).absolute()


@contextmanager
def _retention_lock(runs_root: Path) -> Iterator[None]:
    lock_path = runs_root / _RUN_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.is_symlink():
        raise OSError(f"refusing to use symlinked retention lock '{lock_path}'")
    if not lock_path.exists():
        try:
            fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        restrict_file(lock_path)
    stream = lock_path.open("a+b")
    try:
        _lock_retention_stream(stream)
        yield
    finally:
        try:
            _unlock_retention_stream(stream)
        finally:
            stream.close()


def _lock_retention_stream(stream: object) -> None:
    fd = stream.fileno()  # type: ignore[attr-defined]
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
    elif _msvcrt is not None:  # pragma: no cover - Windows
        stream.seek(0)
        _msvcrt.locking(fd, _msvcrt.LK_LOCK, 1)


def _unlock_retention_stream(stream: object) -> None:
    fd = stream.fileno()  # type: ignore[attr-defined]
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
    elif _msvcrt is not None:  # pragma: no cover - Windows
        stream.seek(0)
        _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)


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
