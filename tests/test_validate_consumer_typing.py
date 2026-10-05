from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_consumer_typing.py"
SPEC = importlib.util.spec_from_file_location("validate_consumer_typing", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover
    raise ImportError(f"Unable to load {SCRIPT}")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_typing_gate_discovers_and_reports_only_consumer_sources() -> None:
    discovery = subprocess.CompletedProcess(
        ["git"],
        0,
        "lib/python/base_cli/core.py\nscripts/tool.py\ntests/test.py\n"
        "examples/demo/src/demo/cli.py\ncompatibility/consumers/atlas/src/atlas/cli.py\n",
        "",
    )
    mypy = subprocess.CompletedProcess(["mypy"], 0)
    with patch.object(module.subprocess, "run", side_effect=[discovery, mypy]) as run:
        assert module.main() == 0

    command = run.call_args_list[1].args[0]
    assert command[-2:] == ["compatibility/consumers/atlas/src/atlas/cli.py", "examples/demo/src/demo/cli.py"]
    assert "lib/python/base_cli/core.py" not in command
    assert "scripts/tool.py" not in command
    assert "tests/test.py" not in command
    environment = run.call_args_list[1].kwargs["env"]
    assert str(SCRIPT.parents[1] / "lib/python") in environment["MYPYPATH"]
