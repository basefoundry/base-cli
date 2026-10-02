from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import base_cli
import base_cli.logging as module
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
