from __future__ import annotations

import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - fcntl is unavailable on Windows.
    _fcntl = None  # type: ignore[assignment]

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - msvcrt is unavailable outside Windows.
    _msvcrt = None  # type: ignore[assignment]

from ._private_files import restrict_file, write_private_json
from .context import Context
from .redaction import REDACTED, is_secret_key, option_name_to_parameter, redact_argv, redact_text_value


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
    "utc_now",
    "write_history_record",
    "write_primary_record",
]


SCHEMA_VERSION = 1
HISTORY_SCOPE_PRIMARY = "primary"
HISTORY_SCOPE_INTERNAL = "internal"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_finished_record(
    context: Context,
    argv: list[str],
    sensitive_options: set[str],
    started_at: datetime,
    exit_code: int,
) -> dict[str, Any]:
    ended_at = utc_now()
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": context.run_id,
        "event": "finished",
        "command": context.history_display_command(context.cli_name, argv),
        "raw_command": context.cli_name,
        "argv": redact_history_argv(argv, sensitive_options),
        "started_at": format_timestamp(started_at),
        "ended_at": format_timestamp(ended_at),
        "duration_ms": duration_ms(started_at, ended_at),
        "exit_code": exit_code,
        "status": "ok" if exit_code == 0 else "error",
        "log_path": compact_path(context.log_file),
        "owner": context.runtime_owner,
        "bundle_path": compact_path(context.run_root or context.state_dir),
        "os": normalized_os(),
    }
    optional_fields = {
        "project": context.project_name,
        "project_root": compact_optional_path(context.project_root),
        "manifest": compact_optional_path(context.manifest_path),
        "workspace_root": compact_optional_path(context.workspace_root),
        "shell": os.environ.get("SHELL"),
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
        "command": command,
        "raw_command": raw_command,
        "argv": redact_history_argv(argv, sensitive_options=set()),
        "started_at": format_timestamp(started_at),
        "ended_at": format_timestamp(ended_at),
        "duration_ms": duration_ms(started_at, ended_at),
        "exit_code": exit_code,
        "status": "ok" if exit_code == 0 else "error",
        "os": normalized_os(),
        "scope": scope,
    }
    resolved_bundle = Path(bundle_path).expanduser() if bundle_path else None
    resolved_log = Path(log_path).expanduser() if log_path else (
        resolved_bundle / "logs" / "primary.log" if resolved_bundle is not None else None
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
    path.parent.mkdir(parents=True, exist_ok=True)
    append_history_line(path, f"{json.dumps(record, sort_keys=True)}\n")
    restrict_file(path)


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
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    lock_fd = fd
    sidecar_fd: int | None = None
    try:
        if _fcntl is None and _msvcrt is not None:
            sidecar_path = path.with_name(f".{path.name}.lock")
            sidecar_fd = os.open(sidecar_path, os.O_RDWR | os.O_CREAT, 0o600)
            if os.fstat(sidecar_fd).st_size == 0:
                os.write(sidecar_fd, b"0")
            restrict_file(sidecar_path)
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
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def duration_ms(started_at: datetime, ended_at: datetime) -> int:
    return max(0, round((ended_at - started_at).total_seconds() * 1000))


def display_command(cli_name: str, argv: list[str]) -> str:
    del argv
    return cli_name.replace("_", "-")


def parse_positive_int(option: str, value: str) -> int:
    if not value.isdigit():
        raise ValueError(f"Option '{option}' must be a positive integer.")
    amount = int(value)
    if amount <= 0:
        raise ValueError(f"Option '{option}' must be greater than zero.")
    return amount


def parse_finished_history_record_line(line: str) -> dict[str, Any] | None:
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
    return value if isinstance(value, str) and value else None


def optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def normalized_os() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    return system or platform.platform()


def redact_history_argv(argv: list[str], sensitive_options: set[str]) -> list[str]:
    redacted = redact_argv(argv, sensitive_options)
    result: list[str] = []
    redact_next = False
    for arg in redacted:
        if redact_next:
            result.append(REDACTED)
            redact_next = False
            continue

        option, separator, _value = arg.partition("=")
        normalized = option_name_to_parameter(option) if option.startswith("--") else option
        if option.startswith("--") and is_secret_key(normalized):
            if separator:
                result.append(f"{option}={REDACTED}")
            else:
                result.append(option)
                redact_next = True
            continue
        result.append(redact_history_text(arg))
    return result


def redact_history_text(value: str) -> str:
    key, separator, _value = value.partition("=")
    if separator and is_secret_key(key):
        return f"{key}={REDACTED}"
    return compact_home_text(redact_text_value(value))


def compact_optional_path(path: Path | None) -> str | None:
    if path is None:
        return None
    return compact_path(path)


def compact_path(path: Path) -> str:
    return compact_home_text(str(path.expanduser().resolve(strict=False)))


def compact_home_text(value: str) -> str:
    home = str(Path.home().expanduser().resolve(strict=False))
    if value == home:
        return "~"
    if value.startswith(f"{home}/"):
        return f"~/{value[len(home) + 1:]}"
    return value
