"""Shared output-format resolution and rendering for Python CLIs."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sys
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TextIO, TypeAlias

from ._dependencies import require_yaml
from .integrations import try_render_rich_table

PUBLIC_OUTPUT_FORMATS = ("text", "csv", "tsv", "yaml", "json", "ndjson")
NDJSON_SCHEMA = "base-cli.record"
NDJSON_SCHEMA_VERSION = 1
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_DEFAULT_TERMINAL_WIDTH = 120
_DEFAULT_MAX_CELL_WIDTH = 80


class OutputFormatError(ValueError):
    """Raised when a public output format is not supported."""


StructuredRecord: TypeAlias = Mapping[str, Any]


class StructuredResultWriter(Protocol):
    """Typed sink for one structured result at a time."""

    def write(self, record: StructuredRecord) -> None:
        """Write one record without retaining the stream in memory."""


@dataclass
class NdjsonWriter:
    """Write versioned structured records as newline-delimited JSON."""

    stream: TextIO
    schema: str = NDJSON_SCHEMA
    schema_version: int = NDJSON_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.schema.strip():
            raise ValueError("schema must be a non-empty string")
        if self.schema_version < 1:
            raise ValueError("schema_version must be greater than 0")

    def write(self, record: StructuredRecord) -> None:
        """Write one record and flush it for pipeline consumers."""

        if not isinstance(record, Mapping):
            raise TypeError(f"structured records must be mappings, got {type(record).__name__}")
        payload = {
            "schema_version": self.schema_version,
            "schema": self.schema,
            "record": dict(record),
        }
        self.stream.write(json.dumps(payload, separators=(",", ":")))
        self.stream.write("\n")
        self.stream.flush()


def output_format_choices() -> str:
    """Return the public choices in help/error-message order."""

    return "|".join(PUBLIC_OUTPUT_FORMATS)


def is_terminal(stream: TextIO | None = None) -> bool:
    """Return whether *stream* is an interactive terminal."""

    candidate = stream if stream is not None else sys.stdout
    try:
        return bool(candidate.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def resolve_output_format(
    requested: str | None,
    *,
    stream: TextIO | None = None,
) -> str:
    """Resolve a requested format, making text TTY-aware.

    ``text`` is intentionally a presentation mode rather than a wire format:
    it renders a table for terminals and tab-delimited rows for redirected or
    piped output.  Omitting the format follows the same policy.
    """

    normalized = (requested or "text").lower()
    if normalized not in PUBLIC_OUTPUT_FORMATS:
        raise OutputFormatError(
            f"Unsupported output format '{requested}'. Expected one of: {', '.join(PUBLIC_OUTPUT_FORMATS)}."
        )
    if normalized == "text" and not is_terminal(stream):
        return "tsv"
    return normalized


# pylint: disable=too-many-arguments
def render_records(
    records: Iterable[Mapping[str, Any]],
    *,
    requested_format: str | None,
    columns: Sequence[tuple[str, str]],
    stream: TextIO | None = None,
    footer: str | None = None,
    minimum_widths: Sequence[int] | None = None,
    terminal_width: int | None = None,
    max_cell_width: int | None = _DEFAULT_MAX_CELL_WIDTH,
    rich: bool = False,
) -> str:
    """Render records according to the shared public output contract.

    The returned format name is also written to *stream* when supplied (or
    stdout when omitted). JSON and YAML retain the mapping shape supplied by
    the caller; NDJSON and delimited formats stream one row at a time, use the
    explicit ``columns`` order where applicable, sanitize terminal control
    sequences, and never emit a header or footer. ``minimum_widths`` applies only to terminal table
    columns. Terminal cells use Unicode display-cell widths and are bounded by
    ``terminal_width`` and ``max_cell_width`` with deterministic ellipsis
    truncation. ``rich=True`` opts terminal text into the optional Rich
    renderer and otherwise falls back to the built-in table.
    """

    target = stream if stream is not None else sys.stdout
    resolved = resolve_output_format(requested_format, stream=target)

    if resolved in ("csv", "tsv"):
        delimiter = "," if resolved == "csv" else "\t"
        writer = csv.writer(target, delimiter=delimiter, lineterminator="\n")
        for record in records:
            writer.writerow([_delimited_value(record.get(key)) for _header, key in columns])
        return resolved

    if resolved == "ndjson":
        ndjson_writer = NdjsonWriter(target)
        for record in records:
            ndjson_writer.write(record)
        return resolved

    record_list = [dict(record) for record in records]
    if resolved == "json":
        target.write(json.dumps(record_list, separators=(",", ":")))
        target.write("\n")
        return resolved

    if resolved == "yaml":
        try:
            yaml = require_yaml("PyYAML is required for YAML output.")
        except RuntimeError as exc:
            raise OutputFormatError(str(exc)) from exc
        target.write(yaml.safe_dump(record_list, sort_keys=False, allow_unicode=True))
        return resolved

    _write_table(
        target,
        record_list,
        columns,
        footer,
        minimum_widths,
        terminal_width=terminal_width,
        max_cell_width=max_cell_width,
        rich=rich,
    )
    return resolved


def render_document(
    document: Mapping[str, Any],
    *,
    requested_format: str | None,
    records_key: str | None = None,
    columns: Sequence[tuple[str, str]] | None = None,
    stream: TextIO | None = None,
) -> str:
    """Render a structured report or leave terminal text to its existing renderer.

    Structured formats preserve the complete document.  Delimited output uses
    the selected record list (or the document itself) and never emits report
    prose, headers, or footers.  A terminal ``text`` request returns ``text``
    without writing so the caller can keep its established human report.
    """

    target = stream if stream is not None else sys.stdout
    resolved = resolve_output_format(requested_format, stream=target)
    if resolved == "text":
        return resolved
    if resolved == "json":
        target.write(json.dumps(dict(document), indent=2))
        target.write("\n")
        return resolved
    if resolved == "yaml":
        try:
            yaml = require_yaml("PyYAML is required for YAML output.")
        except RuntimeError as exc:
            raise OutputFormatError(str(exc)) from exc
        target.write(yaml.safe_dump(dict(document), sort_keys=False, allow_unicode=True))
        return resolved

    if records_key:
        candidate = document.get(records_key)
        if isinstance(candidate, list):
            records = [record for record in candidate if isinstance(record, Mapping)]
        else:
            records = [document]
    else:
        records = [document]
    selected_columns = columns or _document_columns(records)
    render_records(
        records,
        requested_format=resolved,
        columns=selected_columns,
        stream=target,
    )
    return resolved


def _document_columns(records: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    column_keys: list[str] = []
    seen: set[str] = set()
    for record in records:
        for key in record:
            normalized_key = str(key)
            if normalized_key not in seen:
                seen.add(normalized_key)
                column_keys.append(normalized_key)
    return [(key.upper(), key) for key in column_keys]


def _cell_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _delimited_value(value: Any) -> str:
    """Return a safe scalar for redirected CSV/TSV output.

    Delimited output is commonly piped into another process. Keep the normal
    csv module's quoting behavior, but remove ANSI/control sequences so a
    producer cannot inject terminal presentation or unexpectedly split a
    record across physical lines.
    """

    return _table_cell(_cell_value(value))


def _write_table(
    stream: TextIO,
    records: Sequence[Mapping[str, Any]],
    columns: Sequence[tuple[str, str]],
    footer: str | None,
    minimum_widths: Sequence[int] | None,
    *,
    terminal_width: int | None,
    max_cell_width: int | None,
    rich: bool,
) -> None:
    selected_minimums = minimum_widths or ()
    if len(selected_minimums) > len(columns):
        raise ValueError("minimum_widths cannot contain more entries than columns")

    if not records:
        if footer:
            stream.write(f"{footer}\n")
        return

    if not columns:
        if footer:
            stream.write(f"{footer}\n")
        return

    table_rows = [[_table_cell(_cell_value(record.get(key))) for _header, key in columns] for record in records]
    headers = [_table_cell(header) for header, _key in columns]
    widths = [
        max(_display_width(header), selected_minimums[index] if index < len(selected_minimums) else 0)
        for index, header in enumerate(headers)
    ]
    for row in table_rows:
        widths = [max(width, _display_width(value)) for width, value in zip(widths, row, strict=False)]

    if max_cell_width is not None:
        if max_cell_width < 1:
            raise ValueError("max_cell_width must be greater than 0 when set")
        widths = [min(width, max_cell_width) for width in widths]

    if terminal_width is not None and terminal_width < 1:
        raise ValueError("terminal_width must be greater than 0 when set")
    available_width = terminal_width if terminal_width is not None else _terminal_width(stream)
    widths = _fit_table_width(widths, available_width)

    if rich and try_render_rich_table(
        stream,
        headers,
        table_rows,
        footer,
        terminal_width=terminal_width,
    ):
        return

    stream.write(
        "  ".join(
            _pad_cell(_truncate(header, width), width) for header, width in zip(headers, widths, strict=False)
        ).rstrip()
    )
    stream.write("\n")
    for row in table_rows:
        stream.write(
            "  ".join(
                _pad_cell(_truncate(value, width), width) for value, width in zip(row, widths, strict=False)
            ).rstrip()
        )
        stream.write("\n")
    if footer:
        stream.write(f"\n{footer}\n")


def _terminal_width(stream: TextIO) -> int:
    try:
        return max(1, shutil.get_terminal_size(fallback=(_DEFAULT_TERMINAL_WIDTH, 24)).columns)
    except OSError:
        try:
            return max(1, os.get_terminal_size(stream.fileno()).columns)
        except (AttributeError, OSError, ValueError):
            return _DEFAULT_TERMINAL_WIDTH


def _fit_table_width(widths: list[int], terminal_width: int) -> list[int]:
    if not widths:
        return widths
    available = max(1, terminal_width - 2 * (len(widths) - 1))
    if sum(widths) <= available:
        return widths
    result = list(widths)
    while sum(result) > available:
        index = max(range(len(result)), key=result.__getitem__)
        if result[index] <= 1:
            break
        result[index] -= 1
    return result


def _table_cell(value: str) -> str:
    value = _ANSI_ESCAPE_RE.sub("", value)
    return "".join(
        character if character == "\t" or (character >= " " and character != "\x7f") else " " for character in value
    )


def _display_width(value: str) -> int:
    width = 0
    for character in value:
        if character == "\t":
            width += 1
            continue
        if unicodedata.combining(character):
            continue
        if unicodedata.category(character) in {"Cc", "Cf"}:
            continue
        width += 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
    return width


def _truncate(value: str, width: int) -> str:
    if _display_width(value) <= width:
        return value
    if width <= 1:
        return "…"[:width]
    remaining = width - 1
    result: list[str] = []
    used = 0
    for character in value:
        character_width = _display_width(character)
        if used + character_width > remaining:
            break
        result.append(character)
        used += character_width
    return "".join(result) + "…"


def _pad_cell(value: str, width: int) -> str:
    return value + " " * max(0, width - _display_width(value))
