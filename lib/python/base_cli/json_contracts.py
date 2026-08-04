"""Versioned JSON contracts for machine-facing CLI consumers.

The helpers in this module deliberately return plain dictionaries so a
consumer can extend ``details`` without depending on a framework-specific
model class. Field names and their meanings are part of the public v1
contract and are documented in ``docs/json-contracts.md``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from logging import LogRecord
from typing import Any, Mapping

from .redaction import REDACTED, redact_text_value


JSON_CONTRACT_VERSION = 1
JSON_LOG_SCHEMA = "base-cli.log"
JSON_OUTPUT_SCHEMA = "base-cli.output"
JSON_ERROR_SCHEMA = "base-cli.error"
MAX_JSON_LOG_MESSAGE_LENGTH = 8192

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:token|password|secret|api[-_]?key|authorization)\b\s*[:=]\s*)"
    r"([^\s,;]+)"
)

__all__ = [
    "JSON_CONTRACT_VERSION",
    "JSON_ERROR_SCHEMA",
    "JSON_LOG_SCHEMA",
    "JSON_OUTPUT_SCHEMA",
    "JsonLogFormatter",
    "MAX_JSON_LOG_MESSAGE_LENGTH",
    "error_envelope",
    "success_envelope",
    "dumps_envelope",
    "redact_json_value",
]


def success_envelope(
    *,
    run_id: str | None,
    details: Mapping[str, Any] | None = None,
    message: str = "Success",
    code: str = "ok",
) -> dict[str, Any]:
    """Return the stable v1 machine-readable success envelope."""

    return {
        "schema_version": JSON_CONTRACT_VERSION,
        "schema": JSON_OUTPUT_SCHEMA,
        "code": code,
        "type": "success",
        "message": _safe_text(message),
        "details": redact_json_value(dict(details or {})),
        "run_id": run_id,
    }


def error_envelope(
    *,
    run_id: str | None,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the stable v1 machine-readable error envelope."""

    return {
        "schema_version": JSON_CONTRACT_VERSION,
        "schema": JSON_ERROR_SCHEMA,
        "code": _safe_text(code),
        "type": "error",
        "message": _safe_text(message),
        "details": redact_json_value(dict(details or {})),
        "run_id": run_id,
    }


def dumps_envelope(envelope: Mapping[str, Any]) -> str:
    """Serialize an envelope as one compact, newline-terminated JSON record."""

    return json.dumps(
        redact_json_value(dict(envelope)),
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"


def redact_json_value(value: Any, *, _key: str | None = None) -> Any:
    """Recursively redact secret-looking JSON keys and text values."""

    if _key is not None and _is_sensitive_key(_key):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(key): redact_json_value(item, _key=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_json_value(item) for item in value]
    if isinstance(value, str):
        return _safe_text(value)
    return value


class JsonLogFormatter(logging.Formatter):
    """Format one ``LogRecord`` as a bounded, redacted JSON object."""

    def __init__(self, run_id: str | None = None) -> None:
        super().__init__()
        self.run_id = run_id

    def format(self, record: LogRecord) -> str:
        message = _safe_text(record.getMessage())
        if len(message) > MAX_JSON_LOG_MESSAGE_LENGTH:
            message = message[:MAX_JSON_LOG_MESSAGE_LENGTH] + "…"
        payload: dict[str, Any] = {
            "schema_version": JSON_CONTRACT_VERSION,
            "schema": JSON_LOG_SCHEMA,
            "timestamp": _timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "run_id": self.run_id,
        }
        if record.exc_info:
            payload["details"] = {
                "exception_type": record.exc_info[0].__name__,
            }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _timestamp(value: float) -> str:
    return (
        datetime.fromtimestamp(value, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _safe_text(value: str) -> str:
    redacted = redact_text_value(value)
    return _SENSITIVE_ASSIGNMENT.sub(r"\1" + REDACTED, redacted)


def _is_sensitive_key(value: str) -> bool:
    return re.search(r"(?i)(token|password|secret|api[-_]?key|authorization)", value) is not None
