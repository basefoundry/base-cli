"""Optional Rich and OpenTelemetry integrations.

The core package deliberately imports no integration dependency. Optional
libraries are resolved only after a consumer opts in, and every integration
boundary is best-effort so a missing or broken plugin cannot change command
completion.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TextIO


@dataclass(frozen=True)
class TelemetryOptions:
    """Opt-in OpenTelemetry configuration for one :class:`base_cli.App`.

    ``tracer`` and ``tracer_provider`` are useful for applications that own
    SDK setup. When neither is supplied, the global OpenTelemetry provider is
    used. The API package is imported lazily only when telemetry is enabled.
    """

    enabled: bool = True
    tracer: Any | None = None
    tracer_provider: Any | None = None
    tracer_name: str = "base_cli"


@dataclass
class TelemetrySession:
    """Best-effort state for a single lifecycle span."""

    span: Any
    started_monotonic_ns: int


def try_render_rich_table(
    stream: TextIO,
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    footer: str | None,
    *,
    terminal_width: int | None = None,
) -> bool:
    """Render a human table with Rich when it is installed and healthy.

    The function returns ``False`` for every unavailable or failing Rich
    import/render path so the caller can use the deterministic plain-table
    renderer. Machine formats and redirected output never call this helper.
    """

    try:
        from rich.box import SIMPLE_HEAD
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text
    except BaseException:  # pragma: no cover - depends on optional package
        return False

    try:
        table = Table(
            box=SIMPLE_HEAD,
            expand=False,
            show_header=True,
            header_style="bold",
            pad_edge=False,
        )
        for header in headers:
            table.add_column(header, overflow="ellipsis")
        for row in rows:
            table.add_row(*(Text.from_plain(value) for value in row))

        console_kwargs: dict[str, Any] = {
            "file": stream,
            "force_terminal": True,
            "color_system": None,
            "markup": False,
            "highlight": False,
        }
        if terminal_width is not None:
            console_kwargs["width"] = max(1, terminal_width)
        console = Console(**console_kwargs)
        console.print(table)
        if footer:
            console.print()
            console.print(footer)
        return True
    except BaseException:  # pragma: no cover - depends on optional package
        return False


def start_telemetry(options: TelemetryOptions | None, context: Any) -> TelemetrySession | None:
    """Start a safe lifecycle span, returning ``None`` on any integration failure."""

    if options is None or not options.enabled:
        return None

    try:
        tracer = options.tracer
        if tracer is None:
            from opentelemetry import trace

            tracer = trace.get_tracer(
                options.tracer_name,
                tracer_provider=options.tracer_provider,
            )
        attributes = _start_attributes(context)
        try:
            span = tracer.start_span("base_cli.run", attributes=attributes)
        except TypeError:
            # Small test/demonstration tracers may only accept a span name.
            span = tracer.start_span("base_cli.run")
        if span is None:
            return None
        _safe_span_call(span, "add_event", "base_cli.run.started", attributes=attributes)
        return TelemetrySession(span=span, started_monotonic_ns=time.monotonic_ns())
    except BaseException as exc:  # pragma: no cover - optional package/runtime dependent
        _debug_integration_failure(context, "OpenTelemetry start failed", exc)
        return None


def finish_telemetry(
    session: TelemetrySession | None,
    context: Any,
    outcome: Any,
    *,
    ended_monotonic_ns: int | None = None,
) -> None:
    """Finish a lifecycle span without allowing exporters to affect teardown."""

    if session is None:
        return

    try:
        ended = ended_monotonic_ns if ended_monotonic_ns is not None else time.monotonic_ns()
        duration_ms = round(max(0, ended - session.started_monotonic_ns) / 1_000_000)
        attributes = {
            **_start_attributes(context),
            "base_cli.outcome": str(getattr(outcome, "kind", "unknown")),
            "base_cli.status": str(getattr(outcome, "status", "error")),
            "base_cli.exit_code": int(getattr(outcome, "exit_code", 1)),
            "base_cli.duration_ms": duration_ms,
        }
        for key, value in attributes.items():
            _safe_span_call(session.span, "set_attribute", key, value)
        _safe_span_call(
            session.span,
            "add_event",
            "base_cli.run.finished",
            attributes=attributes,
        )
        _safe_span_call(session.span, "end")
    except BaseException as exc:  # pragma: no cover - optional exporter dependent
        _debug_integration_failure(context, "OpenTelemetry finish failed", exc)


def _start_attributes(context: Any) -> dict[str, Any]:
    """Return a deliberately small, non-sensitive attribute set."""

    return {
        "base_cli.run_id": str(getattr(context, "run_id", "")),
        "base_cli.cli_name": str(getattr(context, "cli_name", "")),
        "base_cli.environment": str(getattr(context, "environment", "")),
        "base_cli.dry_run": bool(getattr(context, "dry_run", False)),
    }


def _safe_span_call(span: Any, method: str, *args: Any, **kwargs: Any) -> None:
    try:
        callback = getattr(span, method, None)
        if callback is not None:
            callback(*args, **kwargs)
    except BaseException:
        # Exporters and SDK shutdown hooks are outside the command's failure
        # boundary. A broken exporter must never fail the user command.
        pass


def _debug_integration_failure(context: Any, message: str, exc: BaseException) -> None:
    try:
        logger = getattr(context, "log", None)
        if logger is not None:
            logger.debug("%s: %s", message, exc)
    except BaseException:
        pass


__all__ = [
    "TelemetryOptions",
    "TelemetrySession",
    "finish_telemetry",
    "start_telemetry",
    "try_render_rich_table",
]
