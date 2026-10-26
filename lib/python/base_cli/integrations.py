"""Optional Rich and OpenTelemetry integrations.

The core package deliberately imports no integration dependency. Optional
libraries are resolved only after a consumer opts in, and ordinary integration
failures are best-effort so a missing or broken plugin cannot change command
completion. Process-control exceptions always retain their normal meaning;
only the explicitly documented teardown shield keeps them from replacing the
primary command outcome.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TextIO

from ._propagation import TRACEPARENT_ENV, TRACESTATE_ENV


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
    context_api: Any | None = None
    context_token: Any | None = None


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
    except Exception:  # pragma: no cover - depends on optional package
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
            table.add_row(*(Text(value) for value in row))

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
    except Exception:  # pragma: no cover - depends on optional package
        return False


def start_telemetry(options: TelemetryOptions | None, context: Any) -> TelemetrySession | None:
    """Start a safe lifecycle span, returning ``None`` on any integration failure."""

    if options is None or not options.enabled:
        return None

    try:
        tracer = options.tracer
        otel_context_api: Any | None = None
        parent_context: Any | None = None
        otel_trace_api: Any | None = None
        try:
            from opentelemetry import context as otel_context
            from opentelemetry import propagate
            from opentelemetry import trace as otel_trace

            # W3C carriers are lower-case; process environments are commonly
            # upper-case. Normalize only the documented trace variables, never
            # the complete environment (which could accidentally carry W3C
            # baggage from an unrelated variable).
            carrier: dict[str, str] = {}
            for variable in (TRACEPARENT_ENV, TRACESTATE_ENV):
                value = os.environ.get(variable)
                if value is None:
                    value = os.environ.get(variable.lower())
                if value is not None:
                    carrier[variable.lower()] = value
            if carrier:
                parent_context = propagate.extract(carrier)
            otel_context_api = otel_context
            otel_trace_api = otel_trace
        except ImportError:
            pass
        if tracer is None:
            if otel_trace_api is None:
                from opentelemetry import trace as otel_trace

                otel_trace_api = otel_trace
            tracer = otel_trace_api.get_tracer(
                options.tracer_name,
                tracer_provider=options.tracer_provider,
            )
        attributes = _start_attributes(context)
        try:
            if parent_context is None:
                span = tracer.start_span("base_cli.run", attributes=attributes)
            else:
                span = tracer.start_span("base_cli.run", attributes=attributes, context=parent_context)
        except TypeError:
            try:
                # Small test/demonstration tracers may reject context or
                # attributes; preserve the historical name-only fallback.
                span = tracer.start_span("base_cli.run", attributes=attributes)
            except TypeError:
                span = tracer.start_span("base_cli.run")
        if span is None:
            return None
        _safe_span_call(span, "add_event", "base_cli.run.started", attributes=attributes)
        context_token = None
        if otel_context_api is not None and otel_trace_api is not None and hasattr(span, "get_span_context"):
            try:
                active_context = otel_trace_api.set_span_in_context(span, parent_context)
                context_token = otel_context_api.attach(active_context)
            except Exception:
                context_token = None
        return TelemetrySession(
            span=span,
            started_monotonic_ns=time.monotonic_ns(),
            context_api=otel_context_api,
            context_token=context_token,
        )
    except Exception as exc:  # pragma: no cover - optional package/runtime dependent
        _debug_integration_failure(context, "OpenTelemetry start failed", exc)
        return None


def finish_telemetry(
    session: TelemetrySession | None,
    context: Any,
    outcome: Any,
    *,
    ended_monotonic_ns: int | None = None,
    exception: BaseException | None = None,
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
        if exception is not None and str(getattr(outcome, "status", "error")) != "ok":
            _safe_span_call(session.span, "record_exception", exception)
        _set_span_status(session.span, outcome)
        _safe_span_call(
            session.span,
            "add_event",
            "base_cli.run.finished",
            attributes=attributes,
        )
        _safe_span_call(session.span, "end")
    except BaseException as exc:  # pragma: no cover - optional exporter dependent
        # Finishing telemetry is teardown. Preserve the already-determined
        # command result even if an exporter or logger raises process-control
        # exceptions while the span is being closed.
        _debug_integration_failure(context, "OpenTelemetry finish failed", exc)
    finally:
        if session.context_api is not None and session.context_token is not None:
            try:
                session.context_api.detach(session.context_token)
            except Exception:
                pass


def inject_trace_context(environ: dict[str, str]) -> None:
    """Inject the current OpenTelemetry span into a child process environment."""

    try:
        from opentelemetry import propagate

        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        for key, value in carrier.items():
            if key.lower() == TRACEPARENT_ENV.lower():
                environ[TRACEPARENT_ENV] = value
            elif key.lower() == TRACESTATE_ENV.lower():
                environ[TRACESTATE_ENV] = value
    except Exception:
        # Trace propagation is optional and must never change command behavior.
        pass


def _start_attributes(context: Any) -> dict[str, Any]:
    """Return a deliberately small, non-sensitive attribute set."""

    return {
        "base_cli.run_id": str(getattr(context, "run_id", "")),
        "base_cli.cli_name": str(getattr(context, "cli_name", "")),
        "base_cli.environment": str(getattr(context, "environment", "")),
        "base_cli.dry_run": bool(getattr(context, "dry_run", False)),
    }


def _set_span_status(span: Any, outcome: Any) -> None:
    """Set an OpenTelemetry status while remaining compatible with test spans."""

    is_success = str(getattr(outcome, "status", "error")) == "ok"
    try:
        from opentelemetry.trace import Status, StatusCode

        status: Any = Status(StatusCode.OK if is_success else StatusCode.ERROR)
    except Exception:  # pragma: no cover - optional dependency boundary
        status = "ok" if is_success else "error"
    _safe_span_call(span, "set_status", status)


def _safe_span_call(span: Any, method: str, *args: Any, **kwargs: Any) -> None:
    try:
        callback = getattr(span, method, None)
        if callback is not None:
            callback(*args, **kwargs)
    except Exception:
        # Exporters and SDK shutdown hooks are outside the command's failure
        # boundary. Ordinary exporter failures must never fail the user
        # command. Process-control exceptions propagate to the caller; the
        # finish_telemetry teardown shield above retains the primary result.
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
    "inject_trace_context",
    "start_telemetry",
    "try_render_rich_table",
]
