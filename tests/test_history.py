from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from base_cli import history


class _FakeMsvcrt:
    LK_LOCK = 1
    LK_UNLCK = 2

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def locking(self, _fd: int, mode: int, size: int) -> None:
        self.calls.append((mode, size))


class HistoryAppendTests(unittest.TestCase):
    def test_current_shell_falls_back_to_comspec(self) -> None:
        with mock.patch.dict("os.environ", {"COMSPEC": r"C:\Windows\System32\cmd.exe"}, clear=True):
            self.assertEqual(history.current_shell(), r"C:\Windows\System32\cmd.exe")

    def test_current_shell_prefers_shell(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"SHELL": "/bin/zsh", "COMSPEC": r"C:\Windows\System32\cmd.exe"},
            clear=True,
        ):
            self.assertEqual(history.current_shell(), "/bin/zsh")

    def test_concurrent_appends_produce_complete_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.jsonl"
            lines = [json.dumps({"run": index}) + "\n" for index in range(24)]

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(lambda line: history.append_history_line(path, line), lines))

            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(sorted(record["run"] for record in records), list(range(24)))

    def test_msvcrt_backend_uses_a_private_sidecar_lock(self) -> None:
        fake_msvcrt = _FakeMsvcrt()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.jsonl"
            with mock.patch.object(history, "_fcntl", None), mock.patch.object(
                history, "_msvcrt", fake_msvcrt
            ):
                history.append_history_line(path, '{"run": 1}\n')

            self.assertEqual(path.read_text(encoding="utf-8"), '{"run": 1}\n')
            self.assertTrue(path.with_name(".history.jsonl.lock").is_file())

        self.assertEqual(fake_msvcrt.calls, [(_FakeMsvcrt.LK_LOCK, 1), (_FakeMsvcrt.LK_UNLCK, 1)])

    def test_msvcrt_sidecar_initialization_race_is_tolerated(self) -> None:
        fake_msvcrt = _FakeMsvcrt()
        original_write = history.os.write
        calls = 0

        def write_with_initialization_race(fd: int, data: bytes) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PermissionError(13, "sidecar is being initialized")
            return original_write(fd, data)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.jsonl"
            with mock.patch.object(history, "_fcntl", None), mock.patch.object(
                history, "_msvcrt", fake_msvcrt
            ), mock.patch.object(history.os, "write", side_effect=write_with_initialization_race):
                history.append_history_line(path, '{"run": 1}\n')

            self.assertEqual(path.read_text(encoding="utf-8"), '{"run": 1}\n')
