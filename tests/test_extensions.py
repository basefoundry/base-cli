from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import base_cli


def _entry_point(
    name: str,
    value: str,
    *,
    group: str = base_cli.COMMAND_ENTRY_POINT_GROUP,
    distribution: str = "sample-package",
    version: str = "1.0",
    extras: tuple[str, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        value=value,
        group=group,
        extras=extras,
        dist=SimpleNamespace(name=distribution, version=version),
    )


class ExtensionDiscoveryTests(unittest.TestCase):
    def test_metadata_discovery_is_lazy_deterministic_and_cached(self) -> None:
        loaded = mock.Mock(return_value="command")
        entry_point = _entry_point("audit", "sample:register")
        entry_point.load = loaded
        calls: list[None] = []

        def provider() -> tuple[SimpleNamespace]:
            calls.append(None)
            return (entry_point,)

        discovery = base_cli.ExtensionDiscovery(entry_points=provider)
        descriptors = discovery.list_commands()
        self.assertEqual(descriptors[0].key, "base_cli.commands:audit")
        self.assertEqual(descriptors[0].distribution, "sample-package")
        self.assertEqual(descriptors[0].api_version, base_cli.EXTENSION_API_VERSION)
        self.assertEqual(descriptors[0].capabilities, ())
        self.assertEqual(calls, [None])
        self.assertEqual(discovery.load(base_cli.COMMAND_ENTRY_POINT_GROUP, "audit"), "command")
        self.assertEqual(discovery.load(base_cli.COMMAND_ENTRY_POINT_GROUP, "audit"), "command")
        self.assertEqual(calls, [None])
        loaded.assert_called_once_with()

    def test_duplicate_names_fail_instead_of_using_installation_order(self) -> None:
        first = _entry_point("audit", "one:register", distribution="one", version="1")
        second = _entry_point("audit", "two:register", distribution="two", version="2")
        discovery = base_cli.ExtensionDiscovery(entry_points=(first, second))

        with self.assertRaises(base_cli.ExtensionCollisionError) as raised:
            discovery.load(base_cli.COMMAND_ENTRY_POINT_GROUP, "audit")
        self.assertIn("one", str(raised.exception))
        self.assertIn("two", str(raised.exception))

    def test_allowlist_and_disable_switch_are_enforced(self) -> None:
        allowed = _entry_point("allowed", "one:register")
        blocked = _entry_point("blocked", "two:register")
        discovery = base_cli.ExtensionDiscovery(
            entry_points=(allowed, blocked),
            allowlist={"base_cli.commands:allowed"},
        )
        self.assertEqual([item.name for item in discovery.list_commands()], ["allowed"])

        disabled = base_cli.ExtensionDiscovery(entry_points=(allowed,), disabled=True)
        self.assertEqual(disabled.list_commands(), ())
        with self.assertRaises(base_cli.ExtensionsDisabledError):
            disabled.load(base_cli.COMMAND_ENTRY_POINT_GROUP, "allowed")

    def test_malformed_metadata_is_skipped_without_hiding_healthy_extensions(self) -> None:
        malformed = _entry_point(
            "broken",
            "broken:register",
            extras=("base-cli-api-v1", "base-cli-api-v2"),
        )
        healthy_command = _entry_point("healthy", "healthy:register")
        healthy_profile = _entry_point(
            "profile",
            "healthy:profile",
            group=base_cli.PROFILE_ENTRY_POINT_GROUP,
        )
        discovery = base_cli.ExtensionDiscovery(
            entry_points=(malformed, healthy_command, healthy_profile),
        )

        self.assertEqual([item.name for item in discovery.list_commands()], ["healthy"])
        self.assertEqual([item.name for item in discovery.list_profiles()], ["profile"])
        self.assertEqual(
            [result.descriptor.name for result in discovery.load_all(base_cli.COMMAND_ENTRY_POINT_GROUP)],
            ["healthy"],
        )

    def test_load_all_isolates_broken_extensions(self) -> None:
        healthy = _entry_point("healthy", "one:register")
        broken = _entry_point("broken", "two:register")
        healthy.load = lambda: "healthy"
        broken.load = lambda: (_ for _ in ()).throw(ImportError("missing optional dependency"))
        results = base_cli.ExtensionDiscovery(entry_points=(healthy, broken)).load_all(
            base_cli.COMMAND_ENTRY_POINT_GROUP
        )

        self.assertEqual([result.descriptor.name for result in results], ["broken", "healthy"])
        self.assertTrue(results[1].ok)
        self.assertFalse(results[0].ok)
        self.assertIn("missing optional dependency", str(results[0].error))

    def test_api_version_and_capabilities_are_negotiated_from_entry_point_extras(self) -> None:
        compatible = _entry_point(
            "telemetry",
            "one:install",
            group=base_cli.PLUGIN_ENTRY_POINT_GROUP,
            extras=("base-cli-api-v1", "base-cli-cap-tracing", "base-cli-cap-metrics"),
        )
        discovery = base_cli.ExtensionDiscovery(entry_points=(compatible,))
        descriptor = discovery.list_plugins()[0]
        self.assertEqual(descriptor.api_version, "1")
        self.assertEqual(descriptor.capabilities, ("metrics", "tracing"))

        incompatible = _entry_point(
            "future",
            "two:install",
            group=base_cli.PLUGIN_ENTRY_POINT_GROUP,
            extras=("base-cli-api-v2",),
        )
        incompatible.load = lambda: self.fail("incompatible extensions must not load")
        future = base_cli.ExtensionDiscovery(entry_points=(incompatible,))
        with self.assertRaisesRegex(base_cli.ExtensionCompatibilityError, "API version '2'"):
            future.load(base_cli.PLUGIN_ENTRY_POINT_GROUP, "future")

    def test_real_distribution_metadata_is_discovered_from_a_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "sample_extension.py").write_text(
                "def register():\n    return 'installed'\n",
                encoding="utf-8",
            )
            dist_info = root / "sample_extension-1.2.3.dist-info"
            dist_info.mkdir()
            (dist_info / "METADATA").write_text(
                "Metadata-Version: 2.1\nName: sample-extension\nVersion: 1.2.3\n",
                encoding="utf-8",
            )
            (dist_info / "entry_points.txt").write_text(
                "[base_cli.commands]\naudit = sample_extension:register\n",
                encoding="utf-8",
            )
            sys.path.insert(0, str(root))
            try:
                importlib.invalidate_caches()
                discovery = base_cli.ExtensionDiscovery(paths=(root,))
                descriptors = discovery.list_commands()
                self.assertEqual(descriptors[0].version, "1.2.3")
                self.assertEqual(discovery.load(base_cli.COMMAND_ENTRY_POINT_GROUP, "audit")(), "installed")
            finally:
                sys.path.remove(str(root))


if __name__ == "__main__":
    unittest.main()
