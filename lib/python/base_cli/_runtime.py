from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ._private_files import write_private_json
from .paths import runtime_owner_root, runtime_run_directory_name


@dataclass(frozen=True)
class RuntimeLayout:
    owner_root: Path
    run_root: Path
    state_dir: Path
    log_dir: Path
    cache_dir: Path
    temp_dir: Path


_LOG_INDEX_NAME = ".base-cli-log-index.json"


# pylint: disable=too-many-arguments
def runtime_layout(
    cache_root: Path,
    cli_name: str,
    run_id: str,
    *,
    owner: str = "base",
    project_name: str | None = None,
    project_root: Path | None = None,
    inherited_run_root: Path | None = None,
) -> RuntimeLayout:
    owner_root = runtime_owner_root(cache_root, owner, project_name, project_root)
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
        if restrict_permissions:
            for directory in [path, *missing]:
                directory.chmod(0o700)
    except OSError as exc:
        raise RuntimeError(_runtime_directory_error(path, cache_root, exc)) from exc


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
    tracked.add(current_log_file.resolve())
    candidates = [(path.name, path) for path in tracked if not _same_path(path, current_log_file)]

    excess_count = len(candidates) + 1 - max_log_files
    if excess_count > 0:
        for _, path in sorted(candidates)[:excess_count]:
            try:
                path.unlink()
                tracked.discard(path)
            except OSError as exc:
                logger.warning("Could not prune log file '%s': %s", path, exc)

    tracked = {path for path in tracked if path.exists() or _same_path(path, current_log_file)}
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
        f"Unable to create Base runtime directory '{path}': {exc}. "
        f"Check permissions on that directory. If the Base cache root '{cache_root}' is unusable, "
        "set BASE_CACHE_DIR to a writable directory."
    )


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()
