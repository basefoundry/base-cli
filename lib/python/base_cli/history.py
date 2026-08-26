from __future__ import annotations

import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - fcntl is unavailable on Windows.
    _fcntl = None  # type: ignore[assignment]

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - msvcrt is unavailable outside Windows.
    _msvcrt = None  # type: ignore[assignment]

from ._private_files import _open_parent_directory, restrict_directory, restrict_file, write_private_json
from .exit_codes import ExitCode
from .redaction import redact_argv

if TYPE_CHECKING:
    from .context import Context

__all__ = [
    "HISTORY_SCOPE_INTERNAL",
    "HISTORY_SCOPE_PRIMARY",
    "SCHEMA_VERSION",
    "build_finished_record",
    "compact_home_text",
    "compact_optional_path",
    "compact_path",
    "display_command",
    "duration_ms",
    "format_timestamp",
    "optional_int",
    "optional_string",
    "parse_finished_history_record_line",
    "parse_positive_int",
    "redact_history_argv",
    "redact_history_text",
    "status_for_exit_code",
    "utc_now",
    "write_history_record",
    "write_primary_record",
]


SCHEMA_VERSION = 1
HISTORY_SCOPE_PRIMARY = "primary"
HISTORY_SCOPE_INTERNAL = "internal"


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def status_for_exit_code(exit_code: int) -> str:
    """Map a process result to its terminal lifecycle status."""
    if exit_code == ExitCode.SUCCESS:
        return "ok"
    if exit_code == ExitCode.INTERRUPTED:
        return "aborted"
    return "error"


def build_finished_record(
    context: Context[Any, Any, Any],
    argv: list[str],
    sensitive_options: set[str],
    started_at: datetime,
    exit_code: int,
) -> dict[str, Any]:
    """Build a redacted finished-invocation record from an active context."""
    ended_at = utc_now()
    safe_argv = redact_history_argv(argv, sensitive_options)
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": context.run_id,
        "event": "finished",
        "command": redact_history_text(context.history_display_command(context.cli_name, safe_argv)),
        "raw_command": context.cli_name,
        "argv": safe_argv,
        "started_at": format_timestamp(started_at),
        "ended_at": format_timestamp(ended_at),
        "duration_ms": duration_ms(started_at, ended_at),
        "exit_code": exit_code,
        "status": status_for_exit_code(exit_code),
        "owner": context.runtime_owner,
        "bundle_path": compact_path(context.run_root or context.state_dir),
        "os": normalized_os(),
    }
    optional_fields = {
        "project": context.project_name,
        "project_root": compact_optional_path(context.project_root),
        "manifest": compact_optional_path(context.manifest_path),
        "workspace_root": compact_optional_path(context.workspace_root),
        "log_path": compact_optional_path(context.log_file),
        "shell": current_shell(),
        "scope": context.history_scope,
        "parent_run_id": context.history_parent_run_id,
    }
    record.update({key: value for key, value in optional_fields.items() if value})
    return record


# pylint: disable=too-many-arguments,too-many-positional-arguments
def write_primary_record(
    path: Path,
    command: str,
    argv: list[str],
    started_at: datetime,
    exit_code: int,
    run_id: str,
    scope: str = HISTORY_SCOPE_PRIMARY,
    project: str | None = None,
    project_root: str | None = None,
    manifest: str | None = None,
    log_path: str | Path | None = None,
    owner: str = "default",
    bundle_path: str | Path | None = None,
    *,
    raw_command: str = "cli",
) -> None:
    """Build and append a user-facing command record to ``path``."""
    ended_at = utc_now()
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "event": "finished",
        "command": redact_history_text(command),
        "raw_command": raw_command,
        "argv": redact_history_argv(argv, sensitive_options=set()),
        "started_at": format_timestamp(started_at),
        "ended_at": format_timestamp(ended_at),
        "duration_ms": duration_ms(started_at, ended_at),
        "exit_code": exit_code,
        "status": status_for_exit_code(exit_code),
        "os": normalized_os(),
        "scope": scope,
    }
    resolved_bundle = Path(bundle_path).expanduser() if bundle_path else None
    resolved_log = (
        Path(log_path).expanduser()
        if log_path
        else (resolved_bundle / "logs" / "primary.log" if resolved_bundle is not None else None)
    )
    optional_fields = {
        "project": project,
        "project_root": compact_optional_path(Path(project_root)) if project_root else None,
        "manifest": compact_optional_path(Path(manifest)) if manifest else None,
        "log_path": compact_optional_path(resolved_log),
        "owner": owner,
        "bundle_path": compact_optional_path(resolved_bundle),
    }
    record.update({key: value for key, value in optional_fields.items() if value})
    write_history_record(path, record)
    if resolved_bundle is not None:
        update_run_metadata(resolved_bundle, record)


def write_history_record(path: Path, record: dict[str, Any]) -> None:
    """Append one serialized record to a consumer-selected history path."""
    missing: list[Path] = []
    candidate = path.parent
    while not candidate.exists():
        missing.append(candidate)
        candidate = candidate.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        for directory in [path.parent, *missing]:
            restrict_directory(directory)
    append_history_line(path, f"{json.dumps(record, sort_keys=True)}\n")


def update_run_metadata(run_root: Path, record: dict[str, Any]) -> None:
    metadata_path = run_root / "run.json"
    metadata: dict[str, Any] = {}
    try:
        if metadata_path.is_file():
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                metadata = loaded
        metadata.update(
            {
                "run_id": record.get("run_id"),
                "owner": record.get("owner", metadata.get("owner", "default")),
                "status": record.get("status"),
                "exit_code": record.get("exit_code"),
                "ended_at": record.get("ended_at"),
                "command": record.get("command"),
            }
        )
        for key in (
            "argv",
            "manifest",
            "parent_run_id",
            "project",
            "project_root",
            "raw_command",
            "scope",
            "workspace_root",
        ):
            if key in record and record[key] is not None:
                metadata[key] = record[key]
        write_private_json(metadata_path, metadata)
    except (OSError, TypeError, ValueError):
        pass


def append_history_line(path: Path, line: str) -> None:
    binary_flag = getattr(os, "O_BINARY", 0)
    open_flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | binary_flag | getattr(os, "O_NOFOLLOW", 0)
    parent_fd: int | None = None
    if os.name != "nt" and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY") and os.open in os.supports_dir_fd:
        opened_parent_fd = _open_parent_directory(path.parent)
        assert opened_parent_fd is not None
        parent_fd = opened_parent_fd
        try:
            try:
                fd = os.open(path.name, open_flags, 0o600, dir_fd=opened_parent_fd)
            except FileNotFoundError:
                # macOS can report ENOENT for a concurrent first creation via
                # a directory descriptor.  Retry the same no-follow open by
                # path; the final-component symlink guard remains in force.
                fd = os.open(path, open_flags, 0o600)
        except BaseException:
            os.close(opened_parent_fd)
            raise
    else:
        fd = os.open(path, open_flags, 0o600)
    lock_fd = fd
    sidecar_fd: int | None = None
    try:
        _restrict_open_file(fd, path)
        if _fcntl is None and _msvcrt is not None:
            sidecar_path = path.with_name(f".{path.name}.lock")
            sidecar_fd = os.open(sidecar_path, os.O_RDWR | os.O_CREAT | binary_flag, 0o600)
            if os.fstat(sidecar_fd).st_size == 0:
                try:
                    os.write(sidecar_fd, b"0")
                except PermissionError:
                    # Another Windows process can initialize the shared empty
                    # sidecar between fstat() and write(). Its byte is enough
                    # for the subsequent blocking msvcrt lock; do not turn
                    # that expected initialization race into a command error.
                    pass
            _restrict_open_file(sidecar_fd, sidecar_path)
            lock_fd = sidecar_fd
        lock_history_file(lock_fd)
        try:
            write_all(fd, line.encode("utf-8"))
        finally:
            unlock_history_file(lock_fd)
    finally:
        if sidecar_fd is not None:
            os.close(sidecar_fd)
        os.close(fd)
        if parent_fd is not None:
            os.close(parent_fd)


def _restrict_open_file(fd: int, path: Path) -> None:
    fchmod = getattr(os, "fchmod", None)
    if os.name != "nt" and fchmod is not None:
        fchmod(fd, 0o600)
    else:
        restrict_file(path)


def lock_history_file(fd: int) -> None:
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
    elif _msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        _msvcrt.locking(fd, _msvcrt.LK_LOCK, 1)


def unlock_history_file(fd: int) -> None:
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
    elif _msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)


def write_all(fd: int, data: bytes) -> None:
    remaining = data
    while remaining:
        written = os.write(fd, remaining)
        if written == 0:
            raise OSError("history append wrote zero bytes")
        remaining = remaining[written:]


def format_timestamp(value: datetime) -> str:
    """Format a datetime as a second-precision UTC ISO-8601 timestamp."""
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def duration_ms(started_at: datetime, ended_at: datetime) -> int:
    """Return the non-negative elapsed duration between two datetimes in milliseconds."""
    return max(0, round((ended_at - started_at).total_seconds() * 1000))


def display_command(cli_name: str, argv: list[str]) -> str:
    """Return the stable, human-readable command label used in history."""
    del argv
    return cli_name.replace("_", "-")


def parse_positive_int(option: str, value: str) -> int:
    """Parse a decimal option value and require it to be greater than zero."""
    if not value.isdecimal():
        raise ValueError(f"Option '{option}' must be a positive integer.")
    amount = int(value)
    if amount <= 0:
        raise ValueError(f"Option '{option}' must be greater than zero.")
    return amount


def parse_finished_history_record_line(line: str) -> dict[str, Any] | None:
    """Decode a line when it is a valid finished record for this schema version."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return None
    if payload.get("event") != "finished":
        return None
    return payload


def optional_string(value: Any) -> str | None:
    """Return a non-empty string value, or ``None`` for other values."""
    return value if isinstance(value, str) and value else None


def optional_int(value: Any) -> int | None:
    """Return an integer value, or ``None`` when the value has another type."""
    return value if isinstance(value, int) else None


def normalized_os() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    return system or platform.platform()


def current_shell() -> str | None:
    """Return the active shell identifier across POSIX and Windows."""

    return os.environ.get("SHELL") or os.environ.get("COMSPEC")


def redact_history_argv(argv: list[str], sensitive_options: set[str]) -> list[str]:
    """Redact sensitive arguments and compact home paths for history storage."""
    return [redact_history_text(arg) for arg in redact_argv(argv, sensitive_options)]


def redact_history_text(value: str) -> str:
    """Redact sensitive option values and compact home paths in text."""
    return compact_home_text(redact_argv([value], set())[0])


def compact_optional_path(path: Path | None, *, home: Path | str | None = None) -> str | None:
    """Return a compact path string, preserving ``None`` for absent paths."""
    if path is None:
        return None
    return compact_path(path, home=home)


def compact_path(path: Path, *, home: Path | str | None = None) -> str:
    """Resolve a path and replace the user's home directory with ``~``."""
    return compact_home_text(str(path.expanduser().resolve(strict=False)), home=home)


def compact_home_text(value: str, *, home: Path | str | None = None) -> str:
    """Replace a home-directory prefix in text with the portable ``~`` marker."""
    home_text = str(home) if home is not None else str(Path.home().expanduser().resolve(strict=False))
    normalized_value = value.replace("\\", "/")
    normalized_home = home_text.replace("\\", "/").rstrip("/")
    comparison_value = normalized_value.lower() if os.name == "nt" else normalized_value
    comparison_home = normalized_home.lower() if os.name == "nt" else normalized_home
    if comparison_value == comparison_home:
        return "~"
    if comparison_value.startswith(f"{comparison_home}/"):
        return f"~/{normalized_value[len(normalized_home) + 1 :]}"
    return value
