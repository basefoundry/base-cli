from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from base_cli import _runtime as runtime


def test_contended_retention_returns_without_waiting(tmp_path: Path) -> None:
    # A separate process faithfully models advisory lock contention on both OSes.
    code = """
import sys
from pathlib import Path
from base_cli._runtime import _retention_lock
with _retention_lock(Path(sys.argv[1])):
    print('locked', flush=True)
    sys.stdin.readline()
"""
    env = {**os.environ, "PYTHONPATH": str(Path(runtime.__file__).resolve().parents[1])}
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
from pathlib import Path
from base_cli._runtime import prune_run_bundles, refresh_run_bundle_index
root = Path(sys.argv[1])
prune_run_bundles(root, max_bundles=1)
refresh_run_bundle_index(root)
""",
                str(tmp_path),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert probe.returncode == 0, probe.stderr
    finally:
        child.communicate("done\n", timeout=5)
