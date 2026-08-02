from __future__ import annotations

import inspect
import os
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any

from .paths import use_working_dir

if TYPE_CHECKING:
    from click.testing import Result


_INVOKE_CWD_LOCK = RLock()


# pylint: disable=too-many-arguments
def invoke(
    app: Any,
    args: list[str] | None = None,
    home: Path | None = None,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> Result:
    cwd_path = Path(cwd).expanduser().resolve() if cwd is not None else None

    try:
        from click.testing import CliRunner
    except ImportError as exc:
        raise RuntimeError("Click is required for base_cli.testing. Install it with 'pip install click'.") from exc

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
    runner = CliRunner(**runner_kwargs)
    if cwd_path is None:
        with use_working_dir(None):
            return runner.invoke(app.click_command, args or [], env=invoke_env)

    with _INVOKE_CWD_LOCK:
        with use_working_dir(cwd_path):
            original_cwd = Path.cwd()
            os.chdir(cwd_path)
            try:
                return runner.invoke(app.click_command, args or [], env=invoke_env)
            finally:
                os.chdir(original_cwd)
