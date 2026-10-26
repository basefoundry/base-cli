from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from unittest import mock

import base_cli
import pytest
from base_cli._propagation import read_runtime_propagation
from base_cli.integrations import inject_trace_context, start_telemetry
from base_cli.testing import invoke


def _run_metadata(run_root: Path, run_id: str) -> None:
    run_root.mkdir(parents=True)
    (run_root / "run.json").write_text(
        json.dumps({"schema_version": 1, "run_id": run_id, "status": "running"}),
        encoding="utf-8",
    )


def test_generic_profile_inherits_a_valid_run_bundle_and_marks_child_internal(tmp_path: Path) -> None:
    run_root = tmp_path / "cache" / "parent" / "runs" / "parent-run__parent"
    _run_metadata(run_root, "parent-run")
    seen: dict[str, object] = {}
    app = base_cli.App(name="child", profile=base_cli.CliProfile.generic(cache_root=tmp_path / "cache"))

    @app.command()
    def main(ctx: base_cli.Context) -> None:
        seen.update(
            {
                "run_id": ctx.run_id,
                "parent": ctx.history_parent_run_id,
                "scope": ctx.history_scope,
                "run_root": ctx.run_root,
            }
        )

    result = invoke(
        app,
        home=tmp_path / "home",
        env={
            "BASE_CLI_RUN_ID": "child-run",
            "BASE_CLI_PARENT_RUN_ID": "parent-run",
            "BASE_CLI_RUN_ROOT": str(run_root),
        },
    )

    assert result.exit_code == 0, result.output
    assert seen == {
        "run_id": "child-run",
        "parent": "parent-run",
        "scope": "internal",
        "run_root": run_root,
    }
    assert not (run_root / "run.json").read_text(encoding="utf-8").__contains__("child-run")


@pytest.mark.parametrize("value", ["../escape", "/absolute", "parent/run", "bad\x00id", ".."])
def test_inbound_run_id_is_rejected_before_runtime_resolution(value: str) -> None:
    with mock.patch.dict("os.environ", {}, clear=True):
        with pytest.raises(base_cli.ConfigurationError, match="BASE_CLI_RUN_ID"):
            read_runtime_propagation({"BASE_CLI_RUN_ID": value})


def test_inbound_run_root_must_be_a_real_active_bundle(tmp_path: Path) -> None:
    with mock.patch.dict(
        os.environ,
        {
            "BASE_CLI_RUN_ID": "parent-run",
            "BASE_CLI_RUN_ROOT": str(tmp_path / "missing"),
        },
        clear=True,
    ):
        with pytest.raises(base_cli.ConfigurationError, match="BASE_CLI_RUN_ROOT"):
            read_runtime_propagation()


def test_explicit_runtime_resolver_takes_precedence_over_inbound_environment(tmp_path: Path) -> None:
    layout = base_cli.RuntimeLayout(
        owner_root=tmp_path / "owner",
        run_root=tmp_path / "owner" / "run",
        state_dir=tmp_path / "owner",
        log_dir=tmp_path / "owner" / "logs",
        cache_dir=tmp_path / "owner" / "cache",
        temp_dir=tmp_path / "owner" / "tmp",
    )

    def resolve_runtime(
        _cli_name: str,
        _project: base_cli.ProjectInfo | None,
    ) -> base_cli.RuntimeBinding:
        return base_cli.RuntimeBinding(
            cache_root=tmp_path,
            layout=layout,
            application_home=None,
            runtime_owner="explicit",
            project_root=None,
            project_name=None,
            inherited_path=None,
            history_parent_run_id=None,
            run_id="explicit-run",
        )

    profile = base_cli.CliProfile.generic(resolve_runtime=resolve_runtime)
    with mock.patch.dict("os.environ", {"BASE_CLI_RUN_ID": "../escape"}, clear=True):
        assert profile.resolve_runtime("tool", None).run_id == "explicit-run"


def test_child_environment_exports_runtime_identity_and_supports_opt_out(tmp_path: Path) -> None:
    ctx = base_cli.Context(
        cli_name="parent",
        run_id="parent-run",
        state_dir=tmp_path,
        log_dir=tmp_path,
        cache_dir=tmp_path,
        temp_dir=tmp_path,
        log_file=None,
        config={},
        environment="dev",
        debug=False,
        keep_temp=False,
        log=logging.getLogger("test"),
        run_root=tmp_path / "bundle",
    )
    child = ctx.child_environment(environ={"KEEP": "yes"})
    assert child["KEEP"] == "yes"
    assert child["BASE_CLI_RUN_ID"] == "parent-run"
    assert child["BASE_CLI_PARENT_RUN_ID"] == "parent-run"
    assert child["BASE_CLI_RUN_ROOT"] == str((tmp_path / "bundle").resolve())
    assert child["BASE_CLI_RUN_SCOPE"] == "internal"

    independent = ctx.child_environment(environ=child, propagate=False)
    assert independent["BASE_CLI_PROPAGATE"] == "0"
    assert "BASE_CLI_RUN_ID" not in independent
    assert "BASE_CLI_RUN_ROOT" not in independent


def test_otel_trace_context_is_extracted_and_injected_for_a_child() -> None:
    otel_trace = pytest.importorskip("opentelemetry.trace")
    otel_propagate = pytest.importorskip("opentelemetry.propagate")
    otel_baggage = pytest.importorskip("opentelemetry.baggage")
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, TraceState

    parent = SpanContext(
        trace_id=0x1,
        span_id=0x3,
        is_remote=False,
        trace_flags=TraceFlags(1),
        trace_state=TraceState(),
    )
    carrier: dict[str, str] = {}
    otel_propagate.inject(carrier, context=otel_trace.set_span_in_context(NonRecordingSpan(parent)))
    received_context: object | None = None

    class Tracer:
        def start_span(self, name: str, *, attributes: dict[str, object], context: object) -> object:
            nonlocal received_context
            del name, attributes
            received_context = context
            received = otel_trace.get_current_span(context).get_span_context()
            assert received.trace_id == parent.trace_id
            return NonRecordingSpan(
                SpanContext(
                    trace_id=received.trace_id,
                    span_id=0x4,
                    is_remote=False,
                    trace_flags=TraceFlags(1),
                    trace_state=TraceState(),
                )
            )

    with mock.patch.dict(
        "os.environ",
        {"TRACEPARENT": carrier["traceparent"], "BAGGAGE": "unrelated=environment"},
        clear=True,
    ):
        session = start_telemetry(base_cli.TelemetryOptions(tracer=Tracer()), object())
        child_env: dict[str, str] = {}
        inject_trace_context(child_env)

    assert session is not None
    assert received_context is not None
    assert otel_baggage.get_all(received_context) == {}
    assert child_env["TRACEPARENT"].endswith("0000000000000004-01")
