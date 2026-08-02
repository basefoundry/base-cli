from __future__ import annotations

import unittest
from pathlib import Path

from base_cli.history import compact_home_text
from base_cli.paths import default_cache_root


class DefaultCacheRootTests(unittest.TestCase):
    def test_explicit_cache_override_wins_on_every_platform(self) -> None:
        root = default_cache_root(
            environ={
                "BASE_CLI_CACHE_DIR": "/custom/cache",
                "LOCALAPPDATA": "/local/app-data",
                "XDG_CACHE_HOME": "/xdg/cache",
            },
            home=Path("/home/alice"),
            platform_name="win32",
        )

        self.assertEqual(root, Path("/custom/cache"))

    def test_linux_prefers_xdg_cache_home(self) -> None:
        root = default_cache_root(
            environ={"XDG_CACHE_HOME": "/xdg/cache"},
            home=Path("/home/alice"),
            platform_name="linux",
        )

        self.assertEqual(root, Path("/xdg/cache"))

    def test_linux_falls_back_to_home_cache(self) -> None:
        root = default_cache_root(
            environ={},
            home=Path("/home/alice"),
            platform_name="linux",
        )

        self.assertEqual(root, Path("/home/alice/.cache"))

    def test_macos_uses_library_caches(self) -> None:
        root = default_cache_root(
            environ={"XDG_CACHE_HOME": "/xdg/cache"},
            home=Path("/Users/alice"),
            platform_name="darwin",
        )

        self.assertEqual(root, Path("/Users/alice/Library/Caches"))

    def test_windows_prefers_local_app_data(self) -> None:
        root = default_cache_root(
            environ={"LOCALAPPDATA": r"C:\Users\alice\AppData\Local"},
            home=Path(r"C:\Users\alice"),
            platform_name="win32",
        )

        self.assertEqual(root, Path(r"C:\Users\alice\AppData\Local"))

    def test_windows_falls_back_to_home_local_app_data(self) -> None:
        root = default_cache_root(
            environ={},
            home=Path(r"C:\Users\alice"),
            platform_name="win32",
        )

        self.assertEqual(root, Path(r"C:\Users\alice") / "AppData" / "Local")


class HomePathCompactionTests(unittest.TestCase):
    def test_compacts_home_paths_with_posix_separators(self) -> None:
        self.assertEqual(
            compact_home_text("/home/alice/project", home=Path("/home/alice")),
            "~/project",
        )

    def test_compacts_home_paths_with_windows_separators(self) -> None:
        self.assertEqual(
            compact_home_text(r"C:\Users\Alice\project", home=r"C:\Users\Alice"),
            "~/project",
        )

    def test_leaves_paths_outside_home_unchanged(self) -> None:
        value = r"C:\Users\Bob\project"

        self.assertEqual(compact_home_text(value, home=r"C:\Users\Alice"), value)
