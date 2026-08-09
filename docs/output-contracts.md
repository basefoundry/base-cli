# Output contracts

`base_cli.output.render_records()` supports `text`, `csv`, `tsv`, `yaml`, `json`,
and `ndjson` formats. Install `base-cli[yaml]` before selecting `yaml`; the
other formats are available from the core package. The requested `text` format is presentation-aware: it renders
a table on a TTY and tab-delimited rows when stdout is redirected or piped.

Delimited output is intentionally automation-friendly:

- rows are streamed directly from the iterable, so CSV and TSV do not retain
  the complete result set in memory;
- the supplied `columns` sequence controls both column order and cell lookup;
- no column header or footer is emitted;
- values use the standard `csv` quoting rules, while ANSI escape sequences and
  other control characters are replaced with spaces.

`ndjson` is the bounded machine-output format for large or long-running
results. It consumes the input iterable once and writes one flushed JSON object
per record without first building a list. Each line has this stable shape:

```json
{"schema_version":1,"schema":"base-cli.record","record":{"name":"base"}}
```

Use `base_cli.NdjsonWriter` when a consumer produces records incrementally.
The `StructuredRecord` and `StructuredResultWriter` types describe the public
producer boundary. Diagnostics remain on stderr; a consumer should not mix log
lines into the NDJSON stream.

Terminal tables use Unicode display-cell width rather than Python string length.
Long cells are bounded by `max_cell_width` (80 by default), and the complete
table is fitted to the detected terminal width (120 columns as a safe fallback)
using an ellipsis. Pass `terminal_width` and `max_cell_width` explicitly when a
caller needs deterministic rendering in tests or a custom frontend.

For an optional polished human table, pass `rich=True` to `render_records()`.
Rich is consulted only for interactive `text`; all redirected and structured
formats retain the rules above and fall back to the built-in renderer if Rich
is unavailable or fails.
