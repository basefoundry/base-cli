from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import base_cli


def _run(tmp_path: Path, body: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    script = (
        """
import os, subprocess, sys
import base_cli
app = base_cli.App(name='descriptor-capture', lifecycle_options=base_cli.LifecycleOptions(json=base_cli.LifecycleOption('--json')))
@app.command()
def main(ctx):
"""
        + "\n".join("    " + line for line in body.splitlines())
        + "\nraise SystemExit(base_cli.run_app(app))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", script, *args],
        text=True,
        capture_output=True,
        timeout=15,
        env={
            **os.environ,
            "BASE_CLI_CACHE_DIR": str(tmp_path),
            "PYTHONPATH": str(Path(base_cli.__file__).resolve().parents[1]),
        },
    )


def test_json_captures_inherited_subprocess_and_descriptor_writers(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        """print('python', flush=True)
os.write(1, b'descriptor\\n')
subprocess.run([sys.executable, '-c', "print('child')"], check=True)
sys.__stdout__.write('original\\n')
""",
        ["--json"],
    )
    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout)
    captured = envelope["details"]["stdout"]
    assert all(word in captured for word in ("python", "descriptor", "child", "original"))


def test_native_output_limit_is_a_single_error_envelope(tmp_path: Path) -> None:
    result = _run(tmp_path, "os.write(1, b'x' * (9 * 1024 * 1024))", ["--json"])
    assert result.returncode != 0
    envelope = json.loads(result.stdout)
    assert "limit" in str(envelope)


def test_human_stdout_is_unchanged(tmp_path: Path) -> None:
    result = _run(tmp_path, "subprocess.run([sys.executable, '-c', 'print(42)'], check=True)", [])
    assert result.returncode == 0
    assert result.stdout == "42\n"


def test_detached_child_reports_incomplete_capture_with_partial_stdout(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        """child = subprocess.Popen([sys.executable, '-c', 'import time; print(\\"child\\", flush=True); time.sleep(5)'])
print('parent', flush=True)
""",
        ["--json"],
    )
    assert result.returncode != 0
    envelope = json.loads(result.stdout)
    assert envelope["code"] == "capture_incomplete"
    assert "parent" in envelope["details"]["stdout"]
    assert "incomplete" in envelope["message"]
