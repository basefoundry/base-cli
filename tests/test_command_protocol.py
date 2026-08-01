from __future__ import annotations

import unittest
from unittest.mock import patch

from base_cli.command_protocol import BOOLEAN
from base_cli.command_protocol import CommandProtocolError
from base_cli.command_protocol import NULLABLE_STRING
from base_cli.command_protocol import RECORD_SCHEMAS
from base_cli.command_protocol import STRING
from base_cli.command_protocol import dumps_record
from base_cli.command_protocol import dumps_records
from base_cli.command_protocol import loads_records
from base_cli.command_protocol import register_record_schema


RECORD_TYPE = "test-record"
RECORD_FIELDS = {
    "name": STRING,
    "enabled": BOOLEAN,
    "note": NULLABLE_STRING,
    "command": STRING,
}


def generic_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "name": "demo",
        "enabled": True,
        "note": None,
        "command": "printf 'tab=\t unicode=λ newline=\n control=\x01'",
    }
    record.update(overrides)
    return record


class CommandProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        register_record_schema(RECORD_TYPE, RECORD_FIELDS)
        self.addCleanup(RECORD_SCHEMAS.pop, RECORD_TYPE, None)

    def test_downstream_code_can_register_a_framing_safe_record_schema(self) -> None:
        record_type = "registered-record"
        self.addCleanup(RECORD_SCHEMAS.pop, record_type, None)
        register_record_schema(
            record_type,
            {
                "name": STRING,
                "enabled": BOOLEAN,
                "note": NULLABLE_STRING,
            },
        )

        payload = dumps_record(record_type, {"name": "demo", "enabled": True, "note": None})

        self.assertEqual(
            loads_records(payload, expected_record_type=record_type),
            (record_type, ({"name": "demo", "enabled": True, "note": None},)),
        )

    def test_record_schema_registration_rejects_invalid_or_duplicate_schemas(self) -> None:
        with self.assertRaisesRegex(CommandProtocolError, "already registered"):
            register_record_schema(RECORD_TYPE, {"name": STRING})
        with self.assertRaisesRegex(CommandProtocolError, "non-empty mapping"):
            register_record_schema("custom", {})
        with self.assertRaisesRegex(CommandProtocolError, "field name"):
            register_record_schema("custom", {"bad-name": STRING})

    def test_generic_registry_does_not_ship_application_record_schemas(self) -> None:
        self.assertEqual(set(RECORD_SCHEMAS), {RECORD_TYPE})
        for record_type in ("project-route", "project-command", "build-target", "demo"):
            self.assertNotIn(record_type, RECORD_SCHEMAS)

    def test_round_trip_preserves_manifest_strings_and_empty_optional_fields(self) -> None:
        records = (
            generic_record(),
            generic_record(command="line one\nline two\t雪", note=""),
        )

        payload = dumps_records(RECORD_TYPE, records)
        record_type, decoded = loads_records(payload, expected_record_type=RECORD_TYPE)

        self.assertEqual(record_type, RECORD_TYPE)
        self.assertEqual(decoded, records)
        self.assertIsNone(decoded[0]["note"])
        self.assertEqual(decoded[1]["note"], "")

    def test_consumer_can_preserve_a_legacy_wire_header(self) -> None:
        payload = dumps_record(
            RECORD_TYPE,
            generic_record(),
            protocol_header="BASE_COMMAND_PROTOCOL_V1",
        )

        self.assertTrue(payload.startswith("BASE_COMMAND_PROTOCOL_V1\n"))
        _, decoded = loads_records(
            f"{payload}\n",
            expected_record_type=RECORD_TYPE,
            protocol_header="BASE_COMMAND_PROTOCOL_V1",
        )
        self.assertEqual(decoded, (generic_record(),))

    def test_protocol_has_stable_generic_version_and_explicit_field_names(self) -> None:
        payload = dumps_record(RECORD_TYPE, generic_record())

        self.assertTrue(payload.startswith("COMMAND_PROTOCOL_V1\n"))
        self.assertIn(f"record_type={RECORD_TYPE}\n", payload)
        self.assertIn("record_count=1\n", payload)
        self.assertIn("field.name:string=", payload)
        self.assertIn("field.note:null=\n", payload)

        _, decoded = loads_records(f"{payload}\n", expected_record_type=RECORD_TYPE)
        self.assertEqual(decoded, (generic_record(),))

    def test_rejects_missing_and_unknown_fields_before_serializing(self) -> None:
        missing = generic_record()
        del missing["command"]
        unknown = generic_record(extra="value")

        with self.assertRaisesRegex(CommandProtocolError, "missing fields: command"):
            dumps_record(RECORD_TYPE, missing)
        with self.assertRaisesRegex(CommandProtocolError, "unknown fields: extra"):
            dumps_record(RECORD_TYPE, unknown)

    def test_rejects_oversized_record_sets_before_serializing(self) -> None:
        with patch("base_cli.command_protocol.MAX_RECORD_COUNT", 0):
            with self.assertRaisesRegex(CommandProtocolError, "protocol maximum"):
                dumps_record(RECORD_TYPE, generic_record())

    def test_rejects_wrong_field_types_and_nul(self) -> None:
        with self.assertRaisesRegex(CommandProtocolError, "enabled.*boolean"):
            dumps_record(RECORD_TYPE, generic_record(enabled="false"))
        with self.assertRaisesRegex(CommandProtocolError, "note.*string"):
            dumps_record(RECORD_TYPE, generic_record(note=7))
        with self.assertRaisesRegex(CommandProtocolError, "command.*NUL"):
            dumps_record(RECORD_TYPE, generic_record(command="bad\0command"))

    def test_rejects_wrong_protocol_version_and_record_type(self) -> None:
        payload = dumps_record(RECORD_TYPE, generic_record())

        with self.assertRaisesRegex(CommandProtocolError, "unsupported protocol header"):
            loads_records(payload.replace("_V1", "_V2", 1))
        with self.assertRaisesRegex(CommandProtocolError, "expected record_type 'demo'"):
            loads_records(payload, expected_record_type="demo")

    def test_rejects_malformed_record_metadata_and_trailing_data(self) -> None:
        payload = dumps_record(RECORD_TYPE, generic_record())

        with self.assertRaisesRegex(CommandProtocolError, "record_count"):
            loads_records(payload.replace("record_count=1", "record_count=one", 1))
        with self.assertRaisesRegex(CommandProtocolError, "canonical"):
            loads_records(payload.replace("record_count=1", "record_count=01", 1))
        with self.assertRaisesRegex(CommandProtocolError, "protocol maximum"):
            loads_records(payload.replace("record_count=1", "record_count=1000001", 1))
        with self.assertRaisesRegex(CommandProtocolError, "protocol maximum"):
            loads_records(payload.replace("record_count=1", f"record_count={'9' * 5000}", 1))
        with self.assertRaisesRegex(CommandProtocolError, "expected record=0"):
            loads_records(payload.replace("record=0", "record=1", 1))
        with self.assertRaisesRegex(CommandProtocolError, "unexpected data"):
            loads_records(f"{payload}\nextra=true")
        with self.assertRaisesRegex(CommandProtocolError, "unexpected data"):
            loads_records(f"{payload}\n\n")
        with self.assertRaisesRegex(CommandProtocolError, "protocol header"):
            loads_records(payload.replace("\n", "\r\n"))
        with self.assertRaisesRegex(CommandProtocolError, "protocol header"):
            loads_records(payload.replace("\n", "\v"))

    def test_rejects_duplicate_unknown_missing_and_invalidly_encoded_fields(self) -> None:
        payload = dumps_record(RECORD_TYPE, generic_record())
        duplicate = payload.replace(
            "field.enabled:boolean=",
            "field.name:string=",
            1,
        )
        unknown = payload.replace(
            "field.enabled:boolean=",
            "field.unknown:string=",
            1,
        )
        wrong_type = payload.replace(
            "field.enabled:boolean=true",
            "field.enabled:string=true",
            1,
        )
        malformed_hex = payload.replace(
            "field.name:string=64656d6f",
            "field.name:string=xyz",
            1,
        )

        with self.assertRaisesRegex(CommandProtocolError, "duplicates field 'name'"):
            loads_records(duplicate)
        with self.assertRaisesRegex(CommandProtocolError, "unknown field 'unknown'"):
            loads_records(unknown)
        with self.assertRaisesRegex(CommandProtocolError, "enabled.*boolean"):
            loads_records(wrong_type)
        with self.assertRaisesRegex(CommandProtocolError, "invalid lowercase hexadecimal"):
            loads_records(malformed_hex)


if __name__ == "__main__":
    unittest.main()
