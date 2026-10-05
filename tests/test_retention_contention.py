from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

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


def test_contended_index_refresh_is_eventually_consistent(tmp_path: Path) -> None:
    logger = logging.getLogger("retention-index-refresh")
    with patch.object(runtime, "_retention_lock", side_effect=BlockingIOError("busy")):
        runtime.refresh_run_bundle_index(tmp_path, logger=logger)


def test_concurrent_passes_converge_after_a_serial_pass(tmp_path: Path) -> None:
    for index in range(8):
        bundle = tmp_path / f"run-{index:02d}"
        bundle.mkdir()
        (bundle / "run.json").write_text(
            f'{{"run_id": "run-{index:02d}", "status": "ok", "started_at": "2020-01-01T00:00:00Z"}}',
            encoding="utf-8",
        )
    code = """
import sys
from pathlib import Path
from base_cli._runtime import prune_run_bundles
prune_run_bundles(Path(sys.argv[1]), max_bundles=3)
"""
    env = {**os.environ, "PYTHONPATH": str(Path(runtime.__file__).resolve().parents[1])}
    children = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(tmp_path)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(8)
    ]
    for child in children:
        _stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr.decode()

    runtime.prune_run_bundles(tmp_path, max_bundles=3)
    assert len([path for path in tmp_path.iterdir() if path.is_dir() and path.name.startswith("run-")]) <= 3
