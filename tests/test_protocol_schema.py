from __future__ import annotations

import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "protocol" / "aiwolf-v1.schema.json"


class ProtocolSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        registry = Registry().with_resource(
            self.schema["$id"], Resource.from_contents(self.schema)
        )
        format_checker = FormatChecker()
        self.client_validator = Draft202012Validator(
            {"$ref": f"{self.schema['$id']}#/$defs/client_request"},
            registry=registry,
            format_checker=format_checker,
        )
        self.server_validator = Draft202012Validator(
            {"$ref": f"{self.schema['$id']}#/$defs/server_event"},
            registry=registry,
            format_checker=format_checker,
        )
        self.envelope_validator = Draft202012Validator(
            {"$ref": f"{self.schema['$id']}#/$defs/envelope"},
            registry=registry,
            format_checker=format_checker,
        )

    def test_schema_declares_language_independent_protocol_version_policy(self) -> None:
        self.assertEqual(
            self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertEqual(self.schema["$id"], "urn:aiwolf:protocol:1.0")
        self.assertIn("major versions", self.schema["description"])
        self.assertIn("only by seq", self.schema["description"])
        self.assertEqual(
            self.schema["$defs"]["protocol_version"]["pattern"],
            "^[0-9]+\\.[0-9]+$",
        )

    def test_server_event_requires_common_envelope_and_positive_seq(self) -> None:
        event = self.make_server_event()
        self.assert_valid(self.server_validator, event)

        missing_seq = dict(event)
        missing_seq.pop("seq")
        self.assert_invalid(self.server_validator, missing_seq)

        zero_seq = dict(event, seq=0)
        self.assert_invalid(self.server_validator, zero_seq)

        missing_version = dict(event)
        missing_version.pop("protocol_version")
        self.assert_invalid(self.server_validator, missing_version)

    def test_client_request_omits_seq(self) -> None:
        request = self.make_client_request()
        self.assert_valid(self.client_validator, request)
        self.assert_invalid(self.client_validator, dict(request, seq=1))

    def test_common_schema_does_not_assign_message_types_to_a_direction(self) -> None:
        self.assert_valid(
            self.client_validator,
            self.make_client_request(event_type="game.state_sync"),
        )

    def test_strict_envelope_rejects_unknown_header_fields(self) -> None:
        request = self.make_client_request()
        event = self.make_server_event()
        self.assert_valid(self.envelope_validator, request)
        self.assert_invalid(self.client_validator, dict(request, evil=True))
        self.assert_invalid(self.server_validator, dict(event, admin=True))

    def test_action_rejected_requires_machine_readable_action_and_reason(self) -> None:
        rejected = self.make_server_event(
            event_type="action.rejected",
            payload={"action": "ability.use", "reason": "invalid_target"},
        )
        self.assert_valid(self.server_validator, rejected)
        self.assert_invalid(
            self.server_validator,
            dict(rejected, payload={"action": "ability.use"}),
        )
        self.assert_invalid(
            self.server_validator,
            dict(rejected, payload={"action": "", "reason": "invalid_target"}),
        )

    def test_common_identifiers_version_and_timestamp_are_validated(self) -> None:
        event = self.make_server_event()
        self.assert_invalid(
            self.server_validator,
            dict(event, event_id="not-a-uuid"),
        )
        self.assert_invalid(
            self.server_validator,
            dict(event, protocol_version="1"),
        )
        self.assert_invalid(self.server_validator, dict(event, timestamp=-1))

    def test_client_timestamp_is_diagnostic_only(self) -> None:
        description = self.schema["$defs"]["client_timestamp"]["description"]
        self.assertIn("Client local timestamp", description)
        self.assertIn("never uses it", description)

    @staticmethod
    def make_server_event(
        *, event_type: str = "player.list", payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        return {
            "type": event_type,
            "protocol_version": "1.0",
            "event_id": "123e4567-e89b-12d3-a456-426614174000",
            "game_id": "123e4567-e89b-12d3-a456-426614174001",
            "seq": 1,
            "timestamp": 0,
            "payload": {} if payload is None else payload,
        }

    @staticmethod
    def make_client_request(*, event_type: str = "ability.use") -> dict[str, object]:
        return {
            "type": event_type,
            "protocol_version": "1.0",
            "event_id": "123e4567-e89b-12d3-a456-426614174002",
            "game_id": "123e4567-e89b-12d3-a456-426614174001",
            "timestamp": 0,
            "payload": {},
        }

    def assert_valid(self, validator: Draft202012Validator, message: object) -> None:
        errors = list(validator.iter_errors(message))
        self.assertEqual([], errors, [error.message for error in errors])

    def assert_invalid(self, validator: Draft202012Validator, message: object) -> None:
        self.assertTrue(list(validator.iter_errors(message)))


if __name__ == "__main__":
    unittest.main()
