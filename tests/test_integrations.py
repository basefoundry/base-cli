from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import base_cli
from base_cli.integrations import start_telemetry
from base_cli.testing import invoke


class _Span:
    def __init__(self) -> None:
        self.name = ""
        self.attributes: dict[str, object] = {}
        self.events: list[tuple[str, dict[str, object]]] = []
        self.ended = False

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, *, attributes: dict[str, object]) -> None:
        self.events.append((name, attributes))

    def end(self) -> None:
        self.ended = True


class _Tracer:
    def __init__(self) -> None:
        self.span = _Span()

    def start_span(self, name: str, *, attributes: dict[str, object]) -> _Span:
        self.span.name = name
        self.span.attributes.update(attributes)
        return self.span


class _BrokenTracer:
    def start_span(self, *_args: object, **_kwargs: object) -> object:
        raise RuntimeError("exporter unavailable")


class _ControlFlowTracer:
    def __init__(self, exception: BaseException) -> None:
        self.exception = exception

    def start_span(self, *_args: object, **_kwargs: object) -> object:
        raise self.exception


class _RichImportFailure:
    def __init__(self, exception_type: type[BaseException], original_import: object) -> None:
        self.exception_type = exception_type
        self.original_import = original_import

    def __call__(self, name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("rich."):
            raise self.exception_type()
        return self.original_import(name, *args, **kwargs)  # type: ignore[operator]


class IntegrationTests(unittest.TestCase):
    def test_rich_is_a_graceful_fallback_and_machine_output_is_unchanged(self) -> None:
        stream = io.StringIO()
        stream.isatty = lambda: True  # type: ignore[method-assign]

        base_cli.render_records(
            ({"name": "base", "path": "/tmp/base"},),
            requested_format="text",
            columns=(("NAME", "name"), ("PATH", "path")),
            stream=stream,
            rich=True,
        )

        self.assertIn("NAME", stream.getvalue())
        self.assertIn("base", stream.getvalue())

        redirected = io.StringIO()
        base_cli.render_records(
            ({"name": "base", "path": "/tmp/base"},),
            requested_format="tsv",
            columns=(("NAME", "name"), ("PATH", "path")),
            stream=redirected,
            rich=True,
        )
        self.assertEqual(redirected.getvalue(), "base\t/tmp/base\n")

    def test_telemetry_emits_safe_lifecycle_events(self) -> None:
        tracer = _Tracer()
        app = base_cli.App(
            name="telemetry-demo",
            log_to_file=False,
            rich=True,
            telemetry=base_cli.TelemetryOptions(tracer=tracer),
        )
        seen: dict[str, object] = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["rich"] = ctx.rich
            seen["run_id"] = ctx.run_id

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(app, [], home=Path(tmpdir))

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(seen["rich"])
        self.assertEqual(tracer.span.name, "base_cli.run")
        self.assertEqual(tracer.span.attributes["base_cli.run_id"], seen["run_id"])
        self.assertNotIn("argv", tracer.span.attributes)
        self.assertNotIn("config", tracer.span.attributes)
        self.assertTrue(tracer.span.ended)
        self.assertEqual(
            [name for name, _attributes in tracer.span.events],
            ["base_cli.run.started", "base_cli.run.finished"],
        )
        self.assertIn("base_cli.duration_ms", tracer.span.attributes)

    def test_missing_or_broken_telemetry_never_changes_completion(self) -> None:
        app = base_cli.App(
            name="broken-telemetry",
            log_to_file=False,
            telemetry=base_cli.TelemetryOptions(tracer=_BrokenTracer()),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(app, [], home=Path(tmpdir))

        self.assertEqual(result.exit_code, 0, result.output)

    def test_optional_boundaries_propagate_process_control_exceptions(self) -> None:
        for exception_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(boundary="rich", exception=exception_type.__name__):
                with mock.patch(
                    "builtins.__import__",
                    side_effect=_RichImportFailure(exception_type, __import__),
                ):
                    with self.assertRaises(exception_type):
                        base_cli.try_render_rich_table(io.StringIO(), ("NAME",), (("x",),), None)

            with self.subTest(boundary="telemetry", exception=exception_type.__name__):
                with self.assertRaises(exception_type):
                    start_telemetry(
                        base_cli.TelemetryOptions(tracer=_ControlFlowTracer(exception_type())),
                        object(),
                    )
