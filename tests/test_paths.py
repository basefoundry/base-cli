from __future__ import annotations

import unittest
from pathlib import Path

from base_cli.history import compact_home_text
from base_cli.paths import (
    default_cache_root,
    default_config_root,
    normalize_explicit_cli_name,
    runtime_namespace_component,
)


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


class DefaultConfigRootTests(unittest.TestCase):
    def test_explicit_config_override_wins_on_every_platform(self) -> None:
        root = default_config_root(
            environ={
                "BASE_CLI_CONFIG_DIR": "/custom/config",
                "APPDATA": "/app-data",
                "XDG_CONFIG_HOME": "/xdg/config",
            },
            home=Path("/home/alice"),
            platform_name="win32",
        )
        self.assertEqual(root, Path("/custom/config"))

    def test_linux_prefers_xdg_config_home(self) -> None:
        root = default_config_root(
            environ={"XDG_CONFIG_HOME": "/xdg/config"},
            home=Path("/home/alice"),
            platform_name="linux",
        )
        self.assertEqual(root, Path("/xdg/config"))

    def test_linux_falls_back_to_home_config(self) -> None:
        self.assertEqual(
            default_config_root(environ={}, home=Path("/home/alice"), platform_name="linux"),
            Path("/home/alice/.config"),
        )

    def test_macos_uses_application_support(self) -> None:
        self.assertEqual(
            default_config_root(environ={}, home=Path("/Users/alice"), platform_name="darwin"),
            Path("/Users/alice/Library/Application Support"),
        )

    def test_windows_prefers_app_data(self) -> None:
        self.assertEqual(
            default_config_root(
                environ={"APPDATA": r"C:\Users\alice\AppData\Roaming"},
                home=Path(r"C:\Users\alice"),
                platform_name="win32",
            ),
            Path(r"C:\Users\alice\AppData\Roaming"),
        )


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


class CliIdentityPathTests(unittest.TestCase):
    def test_explicit_identity_is_preserved_and_whitespace_only_is_rejected(self) -> None:
        self.assertEqual(normalize_explicit_cli_name("ops.prod"), "ops.prod")
        self.assertEqual(normalize_explicit_cli_name("ops+prod"), "ops+prod")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            normalize_explicit_cli_name("   ")

    def test_unsafe_identity_components_are_readable_and_collision_resistant(self) -> None:
        first = runtime_namespace_component("ops+prod")
        second = runtime_namespace_component("ops@prod")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("ops-prod--"))
        self.assertTrue(second.startswith("ops-prod--"))
        self.assertEqual(runtime_namespace_component("ops.prod"), "ops.prod")

    def test_path_like_identity_cannot_escape_runtime_owner(self) -> None:
        component = runtime_namespace_component("../../outside")
        self.assertNotIn("/", component)
        self.assertTrue(component.startswith("outside--"))
