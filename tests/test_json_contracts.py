from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import base_cli
from base_cli.json_contracts import MAX_JSON_LOG_MESSAGE_LENGTH


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class JsonContractTests(unittest.TestCase):
    def _lifecycle_options(self) -> base_cli.LifecycleOptions:
        return base_cli.LifecycleOptions(
            json=base_cli.LifecycleOption("--json"),
        )

    def _retention_app(self, name: str, **kwargs: object) -> base_cli.App:
        return base_cli.App(
            name=name,
            lifecycle_options=self._lifecycle_options(),
            **kwargs,
        )

    def test_envelopes_have_stable_fields_and_recursive_redaction(self) -> None:
        success = base_cli.success_envelope(
            run_id="run-1",
            details={"token": "secret", "nested": [{"password": "hidden"}]},
        )
        failure = base_cli.error_envelope(
            run_id="run-1",
            code="usage_error",
            message="authorization=secret",
            details={"api_key": "hidden"},
        )

        self.assertEqual(
            set(success),
            {"schema_version", "schema", "code", "type", "message", "details", "run_id"},
        )
        self.assertEqual(success["schema_version"], 1)
        self.assertEqual(success["schema"], "base-cli.output")
        self.assertEqual(success["details"]["token"], "[REDACTED]")
        self.assertEqual(success["details"]["nested"][0]["password"], "[REDACTED]")
        self.assertEqual(failure["schema"], "base-cli.error")
        self.assertEqual(failure["message"], "authorization=[REDACTED]")
        self.assertEqual(json.loads(base_cli.dumps_envelope(failure)), failure)

    def test_inline_secret_redaction_keeps_delimiters_inside_values(self) -> None:
        for value in ("abc,def", "abc;def"):
            with self.subTest(value=value):
                envelope = base_cli.error_envelope(
                    run_id=None,
                    code="bad",
                    message=f"PASSWORD={value}",
                )
                self.assertEqual(envelope["message"], "PASSWORD=[REDACTED]")

        envelope = base_cli.error_envelope(
            run_id=None,
            code="bad",
            message="PASSWORD=abc,def;LABEL=visible",
        )
        self.assertEqual(envelope["message"], "PASSWORD=[REDACTED];LABEL=visible")

    def test_json_log_formatter_is_bounded_redacted_and_color_free(self) -> None:
        stream = io.StringIO()
        logger = base_cli.configure_logger(
            "json-formatter",
            None,
            debug=True,
            stream=stream,
            json_logs=True,
            run_id="run-2",
        )
        logger.info("token=secret %s", "x" * (MAX_JSON_LOG_MESSAGE_LENGTH + 20))

        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["schema"], "base-cli.log")
        self.assertEqual(payload["run_id"], "run-2")
        self.assertEqual(payload["level"], "INFO")
        self.assertIn("token=[REDACTED]", payload["message"])
        self.assertLessEqual(len(payload["message"]), MAX_JSON_LOG_MESSAGE_LENGTH + 1)
        self.assertNotIn("\033[", stream.getvalue())

    def test_json_success_captures_stdout_and_keeps_logs_on_stderr(self) -> None:
        app = base_cli.App(
            name="json-success",
            log_to_file=False,
            lifecycle_options=self._lifecycle_options(),
        )
        observed: list[bool] = []

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            observed.append(base_cli.get_lifecycle_values().json)
            ctx.log.info("token=secret")
            print("hello")

        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(app, ["--json"], home=Path(home))

        self.assertEqual(result.exit_code, 0, result.output)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["schema"], "base-cli.output")
        self.assertEqual(envelope["code"], "ok")
        self.assertEqual(envelope["details"]["stdout"], "hello\n")
        self.assertEqual(observed, [True])
        log_lines = [json.loads(line) for line in result.stderr.splitlines() if line]
        self.assertTrue(log_lines)
        self.assertTrue(all(line["schema"] == "base-cli.log" for line in log_lines))
        self.assertTrue(all("[REDACTED]" in line["message"] or "token" not in line["message"] for line in log_lines))

    def test_json_error_is_machine_only_and_exit_code_is_deterministic(self) -> None:
        import click

        app = base_cli.App(
            name="json-error",
            log_to_file=False,
            lifecycle_options=self._lifecycle_options(),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            print("partial")
            raise click.UsageError("password=secret")

        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(app, ["--json"], home=Path(home))

        self.assertEqual(result.exit_code, 2)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["schema"], "base-cli.error")
        self.assertEqual(envelope["code"], "usage_error")
        self.assertEqual(envelope["details"]["exit_code"], 2)
        self.assertEqual(envelope["details"]["stdout"], "partial\n")
        self.assertEqual(envelope["message"], "password=[REDACTED]")
        self.assertEqual(result.stderr, "")

    def test_json_nonzero_command_return_is_an_error_envelope(self) -> None:
        app = base_cli.App(
            name="json-nonzero",
            log_to_file=False,
            lifecycle_options=self._lifecycle_options(),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> int:
            del ctx
            print("partial")
            return 7

        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(app, ["--json"], home=Path(home))

        self.assertEqual(result.exit_code, 7)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["type"], "error")
        self.assertEqual(envelope["code"], "nonzero_return")
        self.assertEqual(envelope["details"]["exit_code"], 7)

    def test_json_help_exit_is_a_success_envelope(self) -> None:
        app = base_cli.App(
            name="json-help",
            log_to_file=False,
            lifecycle_options=self._lifecycle_options(),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(app, ["--json", "--help"], home=Path(home))

        self.assertEqual(result.exit_code, 0)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["type"], "success")
        self.assertIn("Usage:", envelope["details"]["stdout"])

    def test_json_envvar_opt_in_also_captures_command_output(self) -> None:
        app = base_cli.App(
            name="json-envvar",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(
                json=base_cli.LifecycleOption("--json", envvar="BASE_JSON_MODE"),
            ),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            print("hello from env")

        with tempfile.TemporaryDirectory() as home:
            old_value = os.environ.get("BASE_JSON_MODE")
            os.environ["BASE_JSON_MODE"] = "1"
            try:
                result = base_cli.testing.invoke(app, [], home=Path(home))
            finally:
                if old_value is None:
                    os.environ.pop("BASE_JSON_MODE", None)
                else:
                    os.environ["BASE_JSON_MODE"] = old_value

        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["type"], "success")
        self.assertEqual(envelope["details"]["stdout"], "hello from env\n")

    def test_json_mode_captures_combined_short_flags(self) -> None:
        app = base_cli.App(
            name="json-combined-short",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(
                json=base_cli.LifecycleOption("--json", "-j"),
            ),
        )

        @app.command()
        @base_cli.option("-x", is_flag=True)
        def main(ctx: base_cli.Context, x: bool) -> None:
            del ctx, x
            print("hello from combined flags")

        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(app, ["-xj"], home=Path(home))

        self.assertEqual(result.exit_code, 0, result.output)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["type"], "success")
        self.assertEqual(
            envelope["details"]["stdout"],
            "hello from combined flags\n",
        )

    def test_json_preparse_errors_honor_combined_positive_declaration(self) -> None:
        app = base_cli.App(
            name="json-combined-declaration",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(
                json=base_cli.LifecycleOption("--json/--no-json"),
            ),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(app, ["--json", "--unknown"], home=Path(home))

        self.assertEqual(result.exit_code, 2)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["schema"], "base-cli.error")
        self.assertEqual(result.stderr, "")
        self.assertIn("No such option", envelope["message"])

    def test_json_preparse_errors_honor_explicit_negative_declaration_over_true_default(self) -> None:
        app = base_cli.App(
            name="json-negative-declaration",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(
                json=base_cli.LifecycleOption("--json/--no-json", default=True),
            ),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(app, ["--no-json", "--unknown"], home=Path(home))

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("No such option", result.stderr or result.output)

    def test_json_mode_captures_default_map_values(self) -> None:
        app = base_cli.App(
            name="json-default-map",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(
                json=base_cli.LifecycleOption("--json"),
            ),
        )

        @app.command(context_settings={"default_map": {"json": True}})
        def main(ctx: base_cli.Context) -> None:
            del ctx
            print("hello from default map")

        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(app, [], home=Path(home))

        self.assertEqual(result.exit_code, 0, result.output)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["type"], "success")
        self.assertEqual(envelope["details"]["stdout"], "hello from default map\n")

    def test_json_mode_is_opt_in_and_human_output_remains_unchanged(self) -> None:
        app = base_cli.App(name="human-default", log_to_file=False)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            print("hello")

        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(app, [], home=Path(home))

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout, "hello\n")

    def test_implicit_json_retention_is_count_only(self) -> None:
        app = self._retention_app("json-count-only")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("base_cli._app_core.prune_run_bundles") as prune:
                result = base_cli.testing.invoke(app, ["--json"], home=Path(tmpdir))
            self.assertEqual(result.exit_code, 0, result.output)
            prune.assert_called_once()
            self.assertEqual(prune.call_args.kwargs["policy"], base_cli.RetentionPolicy(max_bundles=20))

    def test_explicit_json_retention_overrides_count_only_default(self) -> None:
        app = self._retention_app(
            "json-explicit-retention",
            retention=base_cli.RetentionPolicy(max_age_seconds=60),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("base_cli._app_core.prune_run_bundles") as prune:
                result = base_cli.testing.invoke(app, ["--json"], home=Path(tmpdir))
            self.assertEqual(result.exit_code, 0, result.output)
            prune.assert_called_once()
            self.assertEqual(prune.call_args.kwargs["policy"], base_cli.RetentionPolicy(max_age_seconds=60))

    def test_implicit_human_retention_keeps_safe_defaults(self) -> None:
        app = base_cli.App(name="human-safe-defaults")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("base_cli._app_core.prune_run_bundles") as prune:
                result = base_cli.testing.invoke(app, home=Path(tmpdir))
            self.assertEqual(result.exit_code, 0, result.output)
            prune.assert_called_once()
            self.assertEqual(prune.call_args.kwargs["policy"], base_cli.RetentionPolicy.safe_defaults())


if __name__ == "__main__":
    unittest.main()
