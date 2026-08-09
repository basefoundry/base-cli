from __future__ import annotations

import unittest
import warnings

import base_cli
from base_cli import command_protocol, json_contracts

EXPECTED_FACADE_EXPORTS = frozenset(
    {
        "App",
        "asyncio_adapter",
        "__version__",
        "AttachmentAdapter",
        "AttachmentContextFactory",
        "AttachmentContract",
        "AttachmentServiceFactory",
        "TyperAdapter",
        "BatteriesIncludedConfigLoader",
        "BaseCliDeprecationWarning",
        "BOOLEAN",
        "ApplicationStateT",
        "CliProfile",
        "ConfigLoader",
        "ConfigSnapshot",
        "CommandFilterNormalizer",
        "CommandCodec",
        "CommandProtocolError",
        "CommandSchemaRegistry",
        "ConfigurationError",
        "COMMAND_ENTRY_POINT_GROUP",
        "Context",
        "ConfigT",
        "ENTRY_POINT_GROUPS",
        "ExtensionCollisionError",
        "ExtensionDescriptor",
        "ExtensionDiscovery",
        "ExtensionDiscoveryError",
        "ExtensionLoadError",
        "ExtensionLoadResult",
        "ExtensionsDisabledError",
        "DEFAULT_SCHEMA_REGISTRY",
        "DisplayCommandResolver",
        "EnvironmentConfigLoader",
        "ExitCode",
        "FieldSpec",
        "FrameworkConfig",
        "LIFECYCLE_META_KEY",
        "LifecycleOption",
        "LifecycleOptions",
        "LifecycleValues",
        "NULLABLE_STRING",
        "STRING",
        "command_filters",
        "command_matches",
        "command_protocol",
        "deprecations",
        "experimental",
        "json_contracts",
        "JSON_CONTRACT_VERSION",
        "JSON_ERROR_SCHEMA",
        "JSON_LOG_SCHEMA",
        "JSON_OUTPUT_SCHEMA",
        "JsonLogFormatter",
        "MAX_JSON_LOG_MESSAGE_LENGTH",
        "NDJSON_SCHEMA",
        "NDJSON_SCHEMA_VERSION",
        "NdjsonWriter",
        "dumps_envelope",
        "dumps_record",
        "dumps_records",
        "error_envelope",
        "extensions",
        "history",
        "integrations",
        "inspection_envelope",
        "render_inspection_json",
        "testing",
        "argument",
        "attach",
        "attach_typer",
        "command",
        "configure_logger",
        "delegated_display_command",
        "deprecated",
        "get_command_app",
        "get_current_context",
        "get_typer_command",
        "get_lifecycle_values",
        "log_critical",
        "log_debug",
        "log_error",
        "log_info",
        "log_warning",
        "loads_records",
        "normalize_command_filter",
        "normalize_command_filters",
        "OutputFormatError",
        "PUBLIC_OUTPUT_FORMATS",
        "StructuredRecord",
        "StructuredResultWriter",
        "ProjectInfo",
        "ProjectDiscovery",
        "RECORD_SCHEMAS",
        "RuntimeLayout",
        "PLUGIN_ENTRY_POINT_GROUP",
        "PROFILE_ENTRY_POINT_GROUP",
        "RetentionPolicy",
        "RuntimeResolver",
        "is_terminal",
        "output_format_choices",
        "option",
        "render_document",
        "render_records",
        "register_record_schema",
        "redact_json_value",
        "resolve_output_format",
        "run_app",
        "run_async",
        "success_envelope",
        "RuntimeBinding",
        "ServicesT",
        "TelemetryOptions",
        "TelemetrySession",
        "try_render_rich_table",
        "HistoryWriter",
        "HistoryDisplayResolver",
        "UserConfigLoader",
        "WorkspaceRootResolver",
    }
)


class ApiStabilityTests(unittest.TestCase):
    def test_facade_export_snapshot_is_resolvable(self) -> None:
        self.assertEqual(set(base_cli.__all__), EXPECTED_FACADE_EXPORTS)
        self.assertEqual(len(base_cli.__all__), len(set(base_cli.__all__)))
        for name in base_cli.__all__:
            self.assertTrue(hasattr(base_cli, name), name)

    def test_versioned_contract_identifiers_and_shapes_are_stable(self) -> None:
        self.assertEqual(command_protocol.PROTOCOL_HEADER, "COMMAND_PROTOCOL_V1")
        self.assertEqual(json_contracts.JSON_CONTRACT_VERSION, 1)
        self.assertEqual(
            {
                json_contracts.JSON_OUTPUT_SCHEMA,
                json_contracts.JSON_ERROR_SCHEMA,
                json_contracts.JSON_LOG_SCHEMA,
            },
            {"base-cli.output", "base-cli.error", "base-cli.log"},
        )

        success = base_cli.success_envelope(run_id="run-1")
        error = base_cli.error_envelope(run_id="run-1", code="bad", message="Nope")
        expected_fields = {"schema_version", "schema", "code", "type", "message", "details", "run_id"}
        self.assertEqual(set(success), expected_fields)
        self.assertEqual(set(error), expected_fields)
        self.assertEqual(success["schema_version"], 1)
        self.assertEqual(error["schema_version"], 1)

    def test_deprecated_emits_actionable_warning_and_preserves_metadata(self) -> None:
        @base_cli.deprecated("0.3", remove="1.0", alternative="new_name")
        def old_name(value: str) -> str:
            """Legacy implementation."""

            return value.upper()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", base_cli.BaseCliDeprecationWarning)
            self.assertEqual(old_name("hello"), "HELLO")

        self.assertEqual(old_name.__name__, "old_name")
        self.assertEqual(old_name.__doc__, "Legacy implementation.")
        self.assertEqual(len(caught), 1)
        self.assertIs(caught[0].category, base_cli.BaseCliDeprecationWarning)
        self.assertIn("deprecated since 0.3", str(caught[0].message))
        self.assertIn("removed in 1.0", str(caught[0].message))
        self.assertIn("Use new_name instead", str(caught[0].message))
        self.assertEqual(caught[0].filename, __file__)

    def test_deprecated_requires_release_identifiers(self) -> None:
        with self.assertRaises(ValueError):
            base_cli.deprecated("", remove="1.0")
        with self.assertRaises(ValueError):
            base_cli.deprecated("0.3", remove=" ")


if __name__ == "__main__":
    unittest.main()
