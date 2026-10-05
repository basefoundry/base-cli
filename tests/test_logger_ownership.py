from __future__ import annotations

import io
import logging
from pathlib import Path
from unittest.mock import Mock

import base_cli
from base_cli.testing import invoke


def test_consumer_handler_and_level_survive_invocation(tmp_path: Path) -> None:
    logger = logging.getLogger("base_cli.owned-handler-test")
    sink = logging.StreamHandler(io.StringIO())
    sink.close = Mock()
    logger.addHandler(sink)
    logger.setLevel(logging.INFO)
    logger.propagate = True
    app = base_cli.App(name="owned-handler-test", version="1")

    @app.command()
    def main(ctx: base_cli.Context) -> None:
        ctx.log.info("consumer message")

    try:
        assert invoke(app, [], home=tmp_path).exit_code == 0
        assert logger.handlers == [sink]
        assert logger.level == logging.INFO
        assert logger.propagate
        sink.close.assert_not_called()
        assert "consumer message" in sink.stream.getvalue()
    finally:
        logger.removeHandler(sink)


def test_foreign_handler_without_level_preserves_debug_records(tmp_path: Path) -> None:
    logger = logging.getLogger("base_cli.foreign-handler-no-level")
    consumer_stream = io.StringIO()
    consumer_handler = logging.StreamHandler(consumer_stream)
    user_stream = io.StringIO()
    log_file = tmp_path / "run.log"
    logger.addHandler(consumer_handler)
    logger.setLevel(logging.NOTSET)
    logger.propagate = False
    try:
        configured = base_cli.configure_logger(
            "foreign-handler-no-level",
            log_file,
            debug=True,
            stream=user_stream,
            propagate=False,
        )
        configured.info("info marker")
        configured.debug("debug marker")
        assert "info marker" in user_stream.getvalue()
        assert "debug marker" in user_stream.getvalue()
        assert "info marker" in consumer_stream.getvalue()
        assert "debug marker" in consumer_stream.getvalue()
        file_text = log_file.read_text(encoding="utf-8")
        assert "info marker" in file_text
        assert "debug marker" in file_text
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        logger.setLevel(logging.NOTSET)
        logger.propagate = True


def test_default_logger_does_not_duplicate_through_root() -> None:
    stream = io.StringIO()
    root_sink = logging.StreamHandler(stream)
    logging.root.addHandler(root_sink)
    try:
        logger = base_cli.configure_logger("default-no-duplicates", None, False, stream=stream)
        logger.warning("one warning")
        assert stream.getvalue().count("one warning") == 1
    finally:
        logging.root.removeHandler(root_sink)


def test_explicit_logger_level_does_not_enable_root_propagation() -> None:
    logger = logging.getLogger("base_cli.explicit-level-only")
    logger.setLevel(logging.INFO)
    logger.propagate = True
    try:
        configured = base_cli.configure_logger("explicit-level-only", None, False, stream=io.StringIO())
        assert configured.level == logging.INFO
        assert configured.propagate is False
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        logger.setLevel(logging.NOTSET)


def test_parent_consumer_configuration_is_preserved() -> None:
    parent = logging.getLogger("base_cli.host")
    stream = io.StringIO()
    sink = logging.StreamHandler(stream)
    parent.addHandler(sink)
    try:
        logger = base_cli.configure_logger("host.child", None, False, stream=io.StringIO())
        logger.warning("host record")
        assert logger.propagate
        assert "host record" in stream.getvalue()
    finally:
        parent.removeHandler(sink)
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
