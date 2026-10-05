from __future__ import annotations

import io
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import base_cli
import base_cli.logging as module
import pytest
from base_cli.testing import invoke


def test_sidecar_is_opened_once_and_closed(tmp_path: Path) -> None:
    handler = module.SecureLogFileHandler(tmp_path / "run.log")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "message", (), None)
    with patch.object(module, "_open_log_lock", wraps=module._open_log_lock) as opened:
        handler.emit(record)
        handler.emit(record)
        assert opened.call_count == 1
    stream = handler._lock_stream
    handler.close()
    assert stream.closed


@pytest.mark.skipif(os.name == "nt", reason="Windows does not permit unlinking an open sidecar")
def test_deleted_sidecar_reopens_and_preserves_later_records(tmp_path: Path) -> None:
    handler = module.SecureLogFileHandler(tmp_path / "run.log")
    try:
        handler.emit(logging.LogRecord("test", logging.INFO, __file__, 1, "before", (), None))
        handler._lock_path.unlink()
        for message in ("after-0", "after-1", "after-2"):
            handler.emit(logging.LogRecord("test", logging.INFO, __file__, 1, message, (), None))
    finally:
        handler.close()

    log_text = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert log_text.splitlines() == ["before", "after-0", "after-1", "after-2"]


def test_shared_formatter_handles_concurrent_user_and_file_logging(tmp_path: Path) -> None:
    user_stream = io.StringIO()
    formatter = module.CliFormatter()
    logger = base_cli.configure_logger(
        "shared-formatter-race",
        tmp_path / "run.log",
        debug=True,
        stream=user_stream,
        formatter=formatter,
        propagate=False,
    )
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(logger.info, (f"message-{index}" for index in range(64))))
        assert user_stream.getvalue().count("message-") == 64
        assert (tmp_path / "run.log").read_text(encoding="utf-8").count("message-") == 64
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


def test_forked_handler_recovers_after_inherited_lock_close_failure(tmp_path: Path) -> None:
    handler = module.SecureLogFileHandler(tmp_path / "run.log")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "message", (), None)

    class FailingStream:
        def close(self) -> None:
            raise OSError("already closed")

    handler._lock_stream = FailingStream()  # type: ignore[assignment]
    handler._lock_pid = os.getpid() - 1
    try:
        handler.emit(record)
        assert handler._lock_pid == os.getpid()
        assert handler._lock_stream is not None
    finally:
        handler.close()


def test_recursion_errors_follow_stdlib_handler_contract(tmp_path: Path) -> None:
    handler = module.SecureLogFileHandler(tmp_path / "run.log")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "message", (), None)
    try:
        with patch.object(logging.FileHandler, "emit", side_effect=RecursionError("recursive")):
            with pytest.raises(RecursionError, match="recursive"):
                handler.emit(record)
    finally:
        handler.close()


def test_logging_lock_failures_do_not_fail_command(tmp_path: Path) -> None:
    app = base_cli.App(name="logging-io-failure")

    @app.command()
    def main(ctx: base_cli.Context) -> None:
        for failure in (PermissionError("unwritable log directory"), OSError("volume full")):
            with patch.object(module, "_lock_log_stream", side_effect=failure):
                ctx.log.info("still completes")
        ctx.log.info("recovers")

    result = invoke(app, [], home=tmp_path)
    assert result.exit_code == 0
    assert "Logging error" in result.stderr


def test_formatter_repeated_paths_do_not_resolve_again(tmp_path: Path) -> None:
    app = base_cli.App(name="cached-log-source")

    @app.command()
    def main(ctx: base_cli.Context) -> None:
        ctx.log.info("warm cache")
        with patch.object(Path, "resolve", side_effect=AssertionError("unexpected resolution")):
            ctx.log.info("cached source")

    assert invoke(app, [], home=tmp_path).exit_code == 0


def test_timestamp_cache_preserves_seconds_and_timezone_format() -> None:
    for use_utc in (False, True):
        formatter = module.CliFormatter(use_utc=use_utc)
        reference = logging.Formatter(datefmt=formatter.datefmt)
        reference.converter = formatter.converter
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "message", (), None)
        for created in (1000.1, 1000.9, 1001.0, 1002.3):
            record.created = created
            assert formatter.formatTime(record, formatter.datefmt) == reference.formatTime(record, formatter.datefmt)


def test_opening_lock_does_not_write_an_unlocked_sentinel(tmp_path: Path) -> None:
    path = tmp_path / "append.lock"
    with module._open_log_lock(path) as stream:
        module._lock_log_stream(stream)
        try:
            assert path.stat().st_size == 0
        finally:
            module._unlock_log_stream(stream)
