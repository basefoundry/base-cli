from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import base_cli
from base_cli import (
    attachment,
    command_filters,
    command_protocol,
    config,
    context,
    deprecations,
    experimental,
    history,
    json_contracts,
    lifecycle_options,
    typer,
)


class PublicApiTests(unittest.TestCase):
    def test_version_matches_repository_contract(self) -> None:
        from pathlib import Path

        version_file = Path(__file__).resolve().parents[1] / "VERSION"
        self.assertEqual(base_cli.__version__, version_file.read_text(encoding="utf-8").splitlines()[0].strip())

    def test_version_resolution_ignores_unrelated_ancestor_version_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            checkout = root / "project" / "checkout"
            package_file = checkout / "lib" / "python" / "base_cli" / "__init__.py"
            (root / "VERSION").write_text("unrelated\n", encoding="utf-8")
            package_file.parent.mkdir(parents=True)
            (checkout / "pyproject.toml").write_text("[project]\nname = 'other'\n", encoding="utf-8")

            with mock.patch.object(base_cli, "__file__", str(package_file)):
                with mock.patch.object(base_cli, "distribution_version", return_value="installed"):
                    self.assertEqual(base_cli._resolve_version(), "installed")

    def test_facade_exports_supported_modules_functions_and_types(self) -> None:
        expected = {
            "CommandProtocolError",
            "CommandCodec",
            "CommandSchemaRegistry",
            "ConfigurationError",
            "AttachmentAdapter",
            "AttachmentContract",
            "BatteriesIncludedConfigLoader",
            "BaseCliDeprecationWarning",
            "deprecated",
            "TyperAdapter",
            "attach_typer",
            "get_typer_command",
            "JSON_CONTRACT_VERSION",
            "success_envelope",
            "error_envelope",
            "ConfigSnapshot",
            "RuntimeLayout",
            "attach",
            "command_filters",
            "command_matches",
            "command_protocol",
            "experimental",
            "dumps_record",
            "dumps_records",
            "get_command_app",
            "get_lifecycle_values",
            "history",
            "LIFECYCLE_META_KEY",
            "LifecycleOption",
            "LifecycleOptions",
            "LifecycleValues",
            "loads_records",
            "normalize_command_filter",
            "normalize_command_filters",
        }

        self.assertTrue(expected.issubset(base_cli.__all__))
        self.assertFalse(hasattr(base_cli, "UserConfig"))
        self.assertIs(base_cli.command_filters, command_filters)
        self.assertIs(base_cli.command_protocol, command_protocol)
        self.assertIs(base_cli.typer, typer)
        self.assertIs(base_cli.json_contracts, json_contracts)
        self.assertIs(base_cli.deprecations, deprecations)
        self.assertIs(base_cli.experimental, experimental)
        self.assertTrue(issubclass(base_cli.ConfigurationError, ValueError))

    def test_module_all_surfaces_are_explicit(self) -> None:
        self.assertEqual(
            set(command_filters.__all__),
            {"CommandFilterNormalizer", "command_matches", "normalize_command_filter", "normalize_command_filters"},
        )
        self.assertEqual(
            set(attachment.__all__),
            {
                "AttachmentAdapter",
                "AttachmentContextFactory",
                "AttachmentContract",
                "AttachmentServiceFactory",
            },
        )
        self.assertEqual(
            set(config.__all__),
            {
                "BatteriesIncludedConfigLoader",
                "ConfigSnapshot",
                "DEFAULT_ENVIRONMENT",
                "FrameworkConfig",
                "load_yaml_file",
            },
        )
        self.assertEqual(
            set(command_protocol.__all__),
            {
                "BOOLEAN",
                "CommandCodec",
                "CommandProtocolError",
                "CommandSchemaRegistry",
                "DEFAULT_SCHEMA_REGISTRY",
                "FieldSpec",
                "NULLABLE_STRING",
                "RECORD_SCHEMAS",
                "STRING",
                "dumps_record",
                "dumps_records",
                "loads_records",
                "register_record_schema",
            },
        )
        self.assertEqual(
            set(lifecycle_options.__all__),
            {
                "LIFECYCLE_META_KEY",
                "LifecycleOption",
                "LifecycleOptions",
                "LifecycleValues",
                "get_lifecycle_values",
            },
        )
        self.assertIn("write_primary_record", history.__all__)
        self.assertNotIn("lock_history_file", history.__all__)
        self.assertNotIn("write_all", history.__all__)
        self.assertEqual(
            set(typer.__all__),
            {"TyperAdapter", "attach_typer", "get_typer_command"},
        )
        self.assertEqual(
            set(json_contracts.__all__),
            {
                "JSON_CONTRACT_VERSION",
                "JSON_ERROR_SCHEMA",
                "JSON_LOG_SCHEMA",
                "JSON_OUTPUT_SCHEMA",
                "JsonLogFormatter",
                "MAX_JSON_LOG_MESSAGE_LENGTH",
                "error_envelope",
                "success_envelope",
                "dumps_envelope",
                "redact_json_value",
            },
        )
        self.assertEqual(set(deprecations.__all__), {"BaseCliDeprecationWarning", "deprecated"})
        self.assertEqual(experimental.__all__, [])
        self.assertEqual(base_cli.testing.__all__, ["invoke"])

    def test_entry_points_have_docstrings(self) -> None:
        self.assertTrue(base_cli.App.__doc__)
        self.assertTrue(base_cli.Context.__doc__)
        self.assertTrue(base_cli.attach.__doc__)
        self.assertTrue(base_cli.get_command_app.__doc__)
        self.assertTrue(base_cli.run_app.__doc__)

    def test_exported_callables_have_docstrings(self) -> None:
        for module in (history, command_protocol, context):
            for name in module.__all__:
                exported = getattr(module, name)
                if not (inspect.isclass(exported) or inspect.isfunction(exported)):
                    continue
                with self.subTest(module=module.__name__, name=name):
                    self.assertTrue(inspect.getdoc(exported), f"{module.__name__}.{name} needs a docstring")


if __name__ == "__main__":
    unittest.main()
