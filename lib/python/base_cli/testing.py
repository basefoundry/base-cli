from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, cast

from .paths import use_working_dir

if TYPE_CHECKING:
    from click.testing import Result


__all__ = ["invoke"]


_INVOKE_CWD_LOCK = RLock()


# pylint: disable=too-many-arguments
def invoke(
    app: Any,
    args: list[str] | None = None,
    home: Path | None = None,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    *,
    reraise_unexpected: bool = False,
) -> Result:
    """Invoke an app through the production ``run_app`` boundary."""

    cwd_path = Path(cwd).expanduser().resolve() if cwd is not None else None
    invocation_argv = list(args or [])

    try:
        from click import testing as click_testing
    except ImportError as exc:
        raise RuntimeError("Click is required for base_cli.testing. Install it with 'pip install click'.") from exc
    CliRunner = click_testing.CliRunner

    invoke_env = dict(env or {})
    if home is not None:
        invoke_env.setdefault("HOME", str(home))
        invoke_env.setdefault("USERPROFILE", str(home))
        invoke_env.setdefault("LOCALAPPDATA", str(home / "AppData" / "Local"))
        invoke_env.setdefault("XDG_CACHE_HOME", str(home / ".cache"))
        invoke_env.setdefault("BASE_CLI_CACHE_DIR", str(home / ".cache"))
    runner_kwargs = {}
    if "mix_stderr" in inspect.signature(CliRunner).parameters:
        runner_kwargs["mix_stderr"] = False
    runner = cast(Any, CliRunner)(**runner_kwargs)
    command = _RunAppInvocation(
        app,
        invocation_argv,
        reraise_unexpected=reraise_unexpected,
    )
    with _INVOKE_CWD_LOCK:
        if cwd_path is None:
            with use_working_dir(None):
                return cast("Result", runner.invoke(command, [], env=invoke_env))

        with use_working_dir(cwd_path):
            original_cwd = Path.cwd()
            os.chdir(cwd_path)
            try:
                return cast("Result", runner.invoke(command, [], env=invoke_env))
            finally:
                os.chdir(original_cwd)


class _RunAppInvocation:
    """Minimal command interface used only by ``CliRunner.invoke``."""

    name = "base-cli-testing-invoke"

    def __init__(
        self,
        app: Any,
        argv: list[str],
        *,
        reraise_unexpected: bool,
    ) -> None:
        self._app = app
        self._argv = tuple(argv)
        self._reraise_unexpected = reraise_unexpected

    def main(self, *_args: Any, **_kwargs: Any) -> None:
        from .app import run_app

        status = run_app(
            self._app,
            list(self._argv),
            reraise_unexpected=self._reraise_unexpected,
        )
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        raise SystemExit(status)
