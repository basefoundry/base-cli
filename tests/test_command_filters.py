from __future__ import annotations

import unittest

from base_cli.command_filters import command_matches, normalize_command_filter, normalize_command_filters


def strip_tool_prefix(value: str) -> str:
    return value.removeprefix("tool_")


class CommandFilterTests(unittest.TestCase):
    def test_default_normalization_is_consumer_neutral(self) -> None:
        self.assertEqual(normalize_command_filter("build_target"), "build-target")
        self.assertEqual(normalize_command_filter("base_build"), "base-build")

    def test_consumer_normalizer_can_define_prefix_policy(self) -> None:
        self.assertEqual(
            normalize_command_filter("TOOL_BUILD", normalizer=strip_tool_prefix),
            "build",
        )
        self.assertEqual(
            normalize_command_filters("build, tool_build", normalizer=strip_tool_prefix),
            ("build",),
        )

    def test_command_matches_uses_the_same_consumer_policy(self) -> None:
        filters = normalize_command_filters("build", normalizer=strip_tool_prefix)
        self.assertTrue(command_matches("tool_build", filters, normalizer=strip_tool_prefix))
        self.assertFalse(command_matches("tool_release", filters, normalizer=strip_tool_prefix))


if __name__ == "__main__":
    unittest.main()
