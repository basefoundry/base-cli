from __future__ import annotations

import logging
import os
import platform
import sys
import time
from pathlib import Path
from typing import BinaryIO, TextIO

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - fcntl is unavailable on Windows.
    _fcntl = None  # type: ignore[assignment]

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - msvcrt is unavailable outside Windows.
    _msvcrt = None  # type: ignore[assignment]

from ._private_files import restrict_file
from .context import get_current_context
from .json_contracts import JsonLogFormatter
from .paths import current_working_dir
from .redaction import redact_argv

_COLOR_RESET = "\033[0m"
_LEVEL_COLORS = {
    logging.DEBUG: "\033[0;36m",
    logging.INFO: "\033[0;32m",
    logging.WARNING: "\033[0;33m",
    logging.ERROR: "\033[0;31m",
    logging.CRITICAL: "\033[0;31m",
}


# pylint: disable=too-many-arguments
def configure_logger(
    cli_name: str,
    log_file: Path | None,
    debug: bool,
    *,
    quiet: bool = False,
    stream: TextIO | None = None,
    formatter: logging.Formatter | None = None,
    json_logs: bool = False,
    run_id: str | None = None,
) -> logging.Logger:
    logger = logging.getLogger(f"base_cli.{cli_name}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    user_stream = stream if stream is not None else sys.stderr
    user_handler = logging.StreamHandler(user_stream)
    user_handler.setLevel(_user_stream_level(debug, quiet))
    user_handler.setFormatter(
        _handler_formatter(
            formatter,
            use_color=_use_color(user_stream),
            json_logs=json_logs,
            run_id=run_id,
        )
    )
    logger.addHandler(user_handler)

    if log_file is not None:
        file_handler = SecureLogFileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            _handler_formatter(
                formatter,
                use_color=False,
                json_logs=json_logs,
                run_id=run_id,
            )
        )
        logger.addHandler(file_handler)
    return logger


def _user_stream_level(debug: bool, quiet: bool) -> int:
    if quiet:
        return logging.WARNING
    if debug:
        return logging.DEBUG
    return logging.INFO


def _handler_formatter(
    formatter: logging.Formatter | None,
    *,
    use_color: bool,
    json_logs: bool,
    run_id: str | None,
) -> logging.Formatter:
    if formatter is not None:
        return formatter
    if json_logs:
        return JsonLogFormatter(run_id)
    return CliFormatter(use_color=use_color)


def _use_color(stream: TextIO) -> bool:
    if os.environ.get("BASE_CLI_COLOR") == "0" or "NO_COLOR" in os.environ:
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def secure_log_file_permissions(log_file: Path) -> None:
    restrict_file(log_file)


class SecureLogFileHandler(logging.FileHandler):
    def __init__(self, filename: str | os.PathLike[str], *args: object, **kwargs: object) -> None:
        self._lock_path = Path(filename).with_name(f".{Path(filename).name}.lock")
        super().__init__(filename, *args, **kwargs)  # type: ignore[arg-type]

    def _open(self) -> TextIO:
        fd = os.open(self.baseFilename, _secure_log_file_open_flags(self.mode), 0o600)
        try:
            fchmod = getattr(os, "fchmod", None)
            if os.name != "nt" and fchmod is not None:
                fchmod(fd, 0o600)
            return open(fd, self.mode, encoding=self.encoding, errors=self.errors, closefd=True)
        except BaseException:
            os.close(fd)
            raise

    def emit(self, record: logging.LogRecord) -> None:
        lock_stream = _open_log_lock(self._lock_path)
        try:
            _lock_log_stream(lock_stream)
            super().emit(record)
        finally:
            _unlock_log_stream(lock_stream)
            lock_stream.close()


def _open_log_lock(path: Path) -> BinaryIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"0")
            stream.flush()
        restrict_file(path)
        return stream
    except BaseException:
        stream.close()
        raise


def _lock_log_stream(stream: BinaryIO) -> None:
    fd = stream.fileno()
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
    elif _msvcrt is not None:
        stream.seek(0)
        _msvcrt.locking(fd, _msvcrt.LK_LOCK, 1)


def _unlock_log_stream(stream: BinaryIO) -> None:
    fd = stream.fileno()
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
    elif _msvcrt is not None:
        stream.seek(0)
        _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)


def _secure_log_file_open_flags(mode: str) -> int:
    flags = os.O_CREAT
    if "x" in mode:
        return flags | os.O_EXCL | os.O_WRONLY
    if "w" in mode:
        return flags | os.O_TRUNC | os.O_WRONLY
    return flags | os.O_APPEND | os.O_WRONLY


class CliFormatter(logging.Formatter):
    def __init__(self, *, use_utc: bool | None = None, use_color: bool = False) -> None:
        self.use_utc = use_utc if use_utc is not None else os.environ.get("LOG_UTC") == "1"
        self.use_color = use_color
        datefmt = "%Y-%m-%d %H:%M:%S UTC" if self.use_utc else "%Y-%m-%d %H:%M:%S %z"
        super().__init__(datefmt=datefmt)
        self.converter = time.gmtime if self.use_utc else time.localtime

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        source = _source_path(record)
        level = _level_name(record)
        line = f"{timestamp} {level:<7} {source}:{record.lineno} {record.getMessage()}"
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            line = f"{line}\n{record.exc_text}"
        if record.stack_info:
            line = f"{line}\n{self.formatStack(record.stack_info)}"
        if not self.use_color:
            return line
        color = _LEVEL_COLORS.get(record.levelno)
        return f"{color}{line}{_COLOR_RESET}" if color else line


def _level_name(record: logging.LogRecord) -> str:
    if record.levelno == logging.WARNING:
        return "WARN"
    if record.levelno == logging.CRITICAL:
        return "FATAL"
    return record.levelname


def _source_path(record: logging.LogRecord) -> str:
    path = Path(record.pathname)
    candidates = []
    application_home = _active_application_home()
    if application_home is not None:
        candidates.append(application_home)
    project_root = _active_project_root()
    if project_root is not None:
        candidates.append(project_root)
    candidates.append(current_working_dir())

    for root in candidates:
        try:
            return str(path.resolve().relative_to(root.resolve()))
        except ValueError:
            continue
    return str(path.resolve())


def _active_project_root() -> Path | None:
    try:
        context = get_current_context()
    except RuntimeError:
        return None
    return context.project_root


def _active_application_home() -> Path | None:
    try:
        context = get_current_context()
    except RuntimeError:
        return None
    return context.application_home


def log_invocation(
    logger: logging.Logger,
    argv: list[str],
    sensitive_options: set[str] | None,
) -> None:
    safe_argv = list(argv) if sensitive_options is None else redact_argv(argv, sensitive_options)
    logger.debug("argv=%s", safe_argv)
    logger.debug("platform=%s %s", platform.system(), platform.machine())
    logger.debug("python=%s", sys.version.replace("\n", " "))


def log_debug(message: str, *args: object) -> None:
    get_current_context().log.debug(message, *args, stacklevel=2)


def log_info(message: str, *args: object) -> None:
    get_current_context().log.info(message, *args, stacklevel=2)


def log_warning(message: str, *args: object) -> None:
    get_current_context().log.warning(message, *args, stacklevel=2)


def log_error(message: str, *args: object) -> None:
    get_current_context().log.error(message, *args, stacklevel=2)


def log_critical(message: str, *args: object) -> None:
    get_current_context().log.critical(message, *args, stacklevel=2)
