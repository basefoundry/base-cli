from __future__ import annotations

import io
import json
import unittest

from base_cli.output import OutputFormatError
from base_cli.output import render_records
from base_cli.output import resolve_output_format


class _Stream(io.StringIO):
    def __init__(self, *, terminal: bool) -> None:
        super().__init__()
        self.terminal = terminal

    def isatty(self) -> bool:
        return self.terminal


class _ClosedStream(io.StringIO):
    def isatty(self) -> bool:
        raise ValueError("stream is closed")


class _CountingSink(io.StringIO):
    """A non-buffering sink for proving large iterables are streamed."""

    def __init__(self) -> None:
        super().__init__()
        self.rows = 0

    def write(self, value: str) -> int:
        self.rows += value.count("\n")
        return len(value)

    def isatty(self) -> bool:
        return False


RECORDS = (
    {"name": "base", "path": "/work/base"},
    {"name": "demo,one", "path": "/work/demo\tone"},
)
COLUMNS = (("PROJECT", "name"), ("PATH", "path"))


class OutputTest(unittest.TestCase):
    def test_tsv_consumes_one_pass_iterable_without_materializing(self) -> None:
        consumed = False

        def records():
            nonlocal consumed
            consumed = True
            yield {"name": "one", "path": "/tmp/one"}

        stream = _Stream(terminal=False)
        render_records(records(), requested_format="tsv", columns=COLUMNS, stream=stream)

        self.assertTrue(consumed)
        self.assertEqual(stream.getvalue(), "one\t/tmp/one\n")

    def test_large_tsv_generator_is_consumed_once(self) -> None:
        class OnePassRows:
            def __init__(self) -> None:
                self.iterations = 0

            def __iter__(self):
                self.iterations += 1
                if self.iterations > 1:
                    raise AssertionError("records were materialized and iterated twice")
                for index in range(10_000):
                    yield {"name": f"project-{index}", "path": "/tmp/project"}

        records = OnePassRows()
        stream = _CountingSink()
        render_records(records, requested_format="tsv", columns=COLUMNS, stream=stream)

        self.assertEqual(records.iterations, 1)
        self.assertEqual(stream.rows, 10_000)

    def test_terminal_table_uses_display_width_and_deterministic_truncation(self) -> None:
        stream = _Stream(terminal=True)
        render_records(
            ({"name": "\x1b[31m界界界界界\x1b[0m", "path": "line\nwith\tcontrols"},),
            requested_format="text",
            columns=COLUMNS,
            stream=stream,
            terminal_width=20,
        )

        output = stream.getvalue()
        self.assertNotIn("\x1b[", output)
        self.assertNotIn("\nwith", output)
        self.assertIn("…", output)
        self.assertLessEqual(max(len(line) for line in output.splitlines()), 20)

    def test_terminal_width_and_cell_width_validate_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "terminal_width"):
            render_records(
                RECORDS,
                requested_format="text",
                columns=COLUMNS,
                stream=_Stream(terminal=True),
                terminal_width=0,
            )
        with self.assertRaisesRegex(ValueError, "max_cell_width"):
            render_records(
                RECORDS,
                requested_format="text",
                columns=COLUMNS,
                stream=_Stream(terminal=True),
                max_cell_width=0,
            )

    def test_closed_stream_is_not_treated_as_terminal(self) -> None:
        self.assertEqual(resolve_output_format("text", stream=_ClosedStream()), "tsv")

    def test_text_is_pretty_on_terminal(self) -> None:
        stream = _Stream(terminal=True)

        render_records(RECORDS, requested_format="text", columns=COLUMNS, stream=stream, footer="2 projects.")

        self.assertEqual(
            stream.getvalue(),
            "PROJECT   PATH\nbase      /work/base\ndemo,one  /work/demo\tone\n\n2 projects.\n",
        )

    def test_terminal_table_honors_minimum_widths(self) -> None:
        stream = _Stream(terminal=True)

        render_records(
            ({"name": "base", "path": "/work/base"},),
            requested_format="text",
            columns=COLUMNS,
            stream=stream,
            minimum_widths=(12,),
        )

        lines = stream.getvalue().splitlines()
        path_column = lines[0].index("PATH")
        self.assertEqual(path_column, 14)
        self.assertEqual(lines[1].index("/work/base"), path_column)

    def test_terminal_table_expands_beyond_minimum_widths(self) -> None:
        stream = _Stream(terminal=True)

        render_records(
            (
                {"name": "base", "path": "/work/base"},
                {"name": "base-bash-libs", "path": "/work/base-bash-libs"},
            ),
            requested_format="text",
            columns=COLUMNS,
            stream=stream,
            minimum_widths=(12,),
        )

        lines = stream.getvalue().splitlines()
        path_column = lines[0].index("PATH")
        self.assertEqual(path_column, len("base-bash-libs") + 2)
        self.assertEqual(lines[1].index("/work/base"), path_column)
        self.assertEqual(lines[2].index("/work/base-bash-libs"), path_column)

    def test_terminal_table_rejects_excess_minimum_widths(self) -> None:
        stream = _Stream(terminal=True)

        with self.assertRaisesRegex(ValueError, "more entries than columns"):
            render_records(
                RECORDS,
                requested_format="text",
                columns=COLUMNS,
                stream=stream,
                minimum_widths=(1, 2, 3),
            )

    def test_text_is_tsv_when_redirected(self) -> None:
        stream = _Stream(terminal=False)

        render_records(
            RECORDS, requested_format="text", columns=COLUMNS, stream=stream, footer="ignored"
        )

        self.assertEqual(stream.getvalue(), "base\t/work/base\ndemo,one\t\"/work/demo\tone\"\n")

    def test_redirected_text_sanitizes_ansi_and_control_characters(self) -> None:
        stream = _Stream(terminal=False)

        render_records(
            ({"name": "\x1b[32mbase\x1b[0m", "path": "line\nwith"},),
            requested_format="text",
            columns=COLUMNS,
            stream=stream,
            footer="ignored",
        )

        self.assertEqual(stream.getvalue(), "base\tline with\n")

    def test_csv_and_tsv_stream_rows_without_headers_or_ansi(self) -> None:
        stream = _Stream(terminal=False)

        render_records(
            ({"name": "\x1b[31mone\x1b[0m", "path": "/tmp/one"},),
            requested_format="csv",
            columns=COLUMNS,
            stream=stream,
            footer="ignored",
        )

        self.assertEqual(stream.getvalue(), "one,/tmp/one\n")

    def test_csv_quotes_cells_and_has_no_header(self) -> None:
        stream = _Stream(terminal=True)

        render_records(RECORDS, requested_format="csv", columns=COLUMNS, stream=stream, footer="ignored")

        self.assertEqual(stream.getvalue(), "base,/work/base\n\"demo,one\",/work/demo\tone\n")

    def test_json_preserves_record_shape(self) -> None:
        stream = _Stream(terminal=True)

        render_records(RECORDS, requested_format="json", columns=COLUMNS, stream=stream)

        self.assertEqual(json.loads(stream.getvalue()), list(RECORDS))

    def test_yaml_preserves_record_shape(self) -> None:
        import yaml

        stream = _Stream(terminal=True)

        render_records(RECORDS, requested_format="yaml", columns=COLUMNS, stream=stream)

        self.assertEqual(yaml.safe_load(stream.getvalue()), list(RECORDS))

    def test_resolve_rejects_unknown_format(self) -> None:
        with self.assertRaisesRegex(OutputFormatError, "Expected one of: text, csv, tsv, yaml, json"):
            resolve_output_format("xml")

    def test_empty_tty_result_keeps_footer(self) -> None:
        stream = _Stream(terminal=True)

        render_records((), requested_format="text", columns=COLUMNS, stream=stream, footer="0 projects.")

        self.assertEqual(stream.getvalue(), "0 projects.\n")
