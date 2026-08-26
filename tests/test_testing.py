from __future__ import annotations

import importlib.util
import inspect
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest import mock

import base_cli
from base_cli.testing import invoke


def manifest_app(**kwargs: object) -> base_cli.App:
    def discover(cwd: Path) -> base_cli.ProjectInfo | None:
        manifest = cwd / "tool.manifest"
        if not manifest.is_file():
            return None
        return base_cli.ProjectInfo(root=cwd, manifest=manifest, name="demo")

    profile = base_cli.CliProfile.generic(discover_project=discover)
    return base_cli.App(profile=profile, **kwargs)


class PackageExportTests(unittest.TestCase):
    def test_package_exports_testing_module_for_documented_access(self) -> None:
        env = os.environ.copy()
        pythonpath = str(Path(__file__).resolve().parents[1] / "lib" / "python")
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = pythonpath if not existing_pythonpath else f"{pythonpath}{os.pathsep}{existing_pythonpath}"

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import base_cli; assert base_cli.testing.invoke",
            ],
            check=False,
            capture_output=True,
            env=env,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class InvokeTests(unittest.TestCase):
    def test_invoke_declares_click_result_return_type(self) -> None:
        return_annotation = inspect.signature(invoke).return_annotation

        self.assertNotEqual(return_annotation, inspect.Signature.empty)
        self.assertIn("Result", str(return_annotation))

    def test_invoke_exposes_keyword_only_unexpected_exception_debugging(self) -> None:
        parameter = inspect.signature(invoke).parameters["reraise_unexpected"]

        self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIs(parameter.default, False)

    def test_invoke_reraise_preserves_click_special_exception_identity(self) -> None:
        import click

        original = click.exceptions.Exit(9)
        profile = replace(
            base_cli.CliProfile.generic(),
            display_command=lambda: (_ for _ in ()).throw(original),
        )
        app = base_cli.App(name="testing-reraise-click-exit", profile=profile)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        result = invoke(app, [], reraise_unexpected=True)

        self.assertEqual(result.exit_code, 1)
        self.assertIs(result.exception, original)

    def test_invoke_writes_manifest_fixture_into_cwd(self) -> None:
        app = manifest_app(name="testing-manifest", log_to_file=False)
        seen: dict[str, Path | None] = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["project_root"] = ctx.project_root
            seen["manifest_path"] = ctx.manifest_path

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            project = root / "project"
            project.mkdir()
            (project / "tool.manifest").write_text(
                "name: demo\n",
                encoding="utf-8",
            )

            result = invoke(
                app,
                [],
                home=home,
                cwd=project,
            )

            manifest_path = project / "tool.manifest"

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(seen["project_root"], project.resolve())
        self.assertEqual(seen["manifest_path"], manifest_path.resolve())

    def test_invoke_with_cwd_exposes_process_cwd_and_restores_it(self) -> None:
        app = manifest_app(name="testing-cwd-isolation", log_to_file=False)
        seen: dict[str, Path | None | str] = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["project_root"] = ctx.project_root
            seen["manifest_path"] = ctx.manifest_path
            seen["cwd"] = str(Path.cwd())
            seen["relative_content"] = Path("relative.txt").read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            project = root / "project"
            home.mkdir()
            project.mkdir()
            manifest_path = project / "tool.manifest"
            manifest_path.write_text("name: demo\n", encoding="utf-8")
            (project / "relative.txt").write_text("cwd works\n", encoding="utf-8")
            original_cwd = Path.cwd()

            result = invoke(app, [], home=home, cwd=project)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(seen["project_root"], project.resolve())
        self.assertEqual(seen["manifest_path"], manifest_path.resolve())
        self.assertEqual(seen["cwd"], str(project.resolve()))
        self.assertEqual(seen["relative_content"], "cwd works\n")
        self.assertEqual(Path.cwd(), original_cwd)

    def test_invoke_with_cwd_without_manifest_preserves_no_manifest_behavior(self) -> None:
        app = base_cli.App(name="testing-no-manifest", log_to_file=False)
        seen: dict[str, Path | None] = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["project_root"] = ctx.project_root
            seen["manifest_path"] = ctx.manifest_path

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            isolated = root / "isolated"
            isolated.mkdir()

            result = invoke(app, [], home=home, cwd=isolated)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIsNone(seen["project_root"])
        self.assertIsNone(seen["manifest_path"])

    def test_invoke_with_cwd_serializes_process_cwd_mutation(self) -> None:
        app = base_cli.App(name="testing-cwd-serialization", log_to_file=False)

        @app.command()
        def main() -> None:
            return None

        first_started = threading.Event()
        second_started = threading.Event()
        release_first = threading.Event()
        state_lock = threading.Lock()
        observed_cwds: list[Path] = []
        active_calls = 0
        max_active_calls = 0

        def fake_invoke(_runner: object, *_args: object, **_kwargs: object) -> object:
            nonlocal active_calls, max_active_calls
            with state_lock:
                active_calls += 1
                max_active_calls = max(max_active_calls, active_calls)
                observed_cwds.append(Path.cwd())
                if len(observed_cwds) == 1:
                    first_started.set()
                else:
                    second_started.set()
            if not release_first.wait(timeout=5):
                raise AssertionError("timed out waiting to release invoke")
            with state_lock:
                active_calls -= 1
            return mock.sentinel.result

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first_cwd = root / "first"
            second_cwd = root / "second"
            first_cwd.mkdir()
            second_cwd.mkdir()
            original_cwd = Path.cwd()

            with mock.patch("click.testing.CliRunner.invoke", new=fake_invoke):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    first = executor.submit(invoke, app, home=root / "home", cwd=first_cwd)
                    self.assertTrue(first_started.wait(timeout=5))
                    second = executor.submit(invoke, app, home=root / "home", cwd=second_cwd)
                    self.assertFalse(second_started.wait(timeout=0.1))
                    release_first.set()
                    self.assertIs(first.result(timeout=5), mock.sentinel.result)
                    self.assertIs(second.result(timeout=5), mock.sentinel.result)

        self.assertEqual(max_active_calls, 1)
        self.assertEqual(set(observed_cwds), {first_cwd.resolve(), second_cwd.resolve()})
        self.assertEqual(Path.cwd(), original_cwd)

    def test_invoke_without_cwd_waits_for_process_cwd_mutation(self) -> None:
        app = base_cli.App(name="testing-mixed-cwd-serialization", log_to_file=False)

        @app.command()
        def main() -> None:
            return None

        cwd_started = threading.Event()
        release_cwd = threading.Event()
        no_cwd_started = threading.Event()
        observed_cwds: list[Path] = []

        def fake_invoke(_runner: object, *_args: object, **_kwargs: object) -> object:
            observed_cwds.append(Path.cwd())
            if len(observed_cwds) == 1:
                cwd_started.set()
                if not release_cwd.wait(timeout=5):
                    raise AssertionError("timed out waiting to release cwd invocation")
            else:
                no_cwd_started.set()
            return mock.sentinel.result

        with tempfile.TemporaryDirectory() as tmpdir:
            isolated = Path(tmpdir) / "isolated"
            isolated.mkdir()
            original_cwd = Path.cwd()

            with mock.patch("click.testing.CliRunner.invoke", new=fake_invoke):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    cwd_call = executor.submit(invoke, app, cwd=isolated)
                    self.assertTrue(cwd_started.wait(timeout=5))
                    no_cwd_call = executor.submit(invoke, app)
                    self.assertFalse(no_cwd_started.wait(timeout=0.1))
                    release_cwd.set()
                    self.assertIs(cwd_call.result(timeout=5), mock.sentinel.result)
                    self.assertIs(no_cwd_call.result(timeout=5), mock.sentinel.result)

        self.assertEqual(observed_cwds, [isolated.resolve(), original_cwd])
        self.assertEqual(Path.cwd(), original_cwd)
