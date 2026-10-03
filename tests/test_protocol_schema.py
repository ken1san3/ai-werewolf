from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from server.network.protocol import (
    PROTOCOL_VERSION,
    SCHEMA_PATH as ACTIVE_SERVER_SCHEMA_PATH,
    ProtocolMessageValidator,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "protocol" / "aiwolf-v1.2.schema.json"
LEGACY_SCHEMA_PATH = PROJECT_ROOT / "protocol" / "aiwolf-v1.schema.json"
REQUEST_EVENT_ID = "123e4567-e89b-12d3-a456-426614174002"
NON_CANONICAL_REQUEST_IDS = (
    "123e4567e89b12d3a456426614174002",
    "{123e4567-e89b-12d3-a456-426614174002}",
    "urn:uuid:123e4567-e89b-12d3-a456-426614174002",
    "123E4567-E89B-12D3-A456-426614174002",
    "not-a-uuid",
)


class ProtocolSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.client_validator = self.make_validator("client_request")
        self.server_validator = self.make_validator("server_event")

    def test_schema_declares_exact_language_independent_protocol_version(self) -> None:
        self.assertEqual(
            self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertEqual(self.schema["$id"], "urn:aiwolf:protocol:1.2")
        self.assertIn("exact protocol version 1.2", self.schema["description"])
        self.assertIn("only by seq", self.schema["description"])
        self.assertEqual(
            self.schema["$defs"]["protocol_version"]["const"],
            "1.2",
        )
        self.assertEqual(PROTOCOL_VERSION, "1.2")
        self.assertEqual(ACTIVE_SERVER_SCHEMA_PATH, SCHEMA_PATH)
        self.assertEqual(
            self.schema["$defs"]["uuid"]["pattern"],
            "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        )

    def test_legacy_1_0_schema_still_validates_recorded_envelopes(self) -> None:
        legacy_schema = json.loads(LEGACY_SCHEMA_PATH.read_text(encoding="utf-8"))
        legacy_validator = self.make_validator("server_event", legacy_schema)
        recorded = self.make_server_event(
            event_type="session.ready",
            payload={"player_id": "player-0", "ready": True},
            protocol_version="1.0",
        )

        self.assert_valid(legacy_validator, recorded)
        self.assert_invalid(self.server_validator, recorded)
        ProtocolMessageValidator(schema_path=LEGACY_SCHEMA_PATH).validate_server(recorded)

    def test_role_assignment_teammates_are_validated_without_rejecting_old_assignments(self) -> None:
        validator = ProtocolMessageValidator()
        assignment = {"player_id": "p-0", "role_id": "custom_role", "modifier_ids": []}
        event = self.make_server_event(event_type="game.event", payload={
            "event_type": "ROLE_ASSIGNED", "event_payload": assignment, "visibility": "private",
        })
        validator.validate_server(event)
        for teammates in ([], ["p-1", "p-2"]):
            assignment["teammate_player_ids"] = teammates
            validator.validate_server(event)
        for teammates in ("p-1", [""], [1], ["p-1", "p-1"], [{"player_id": "p-1", "role_id": "hidden"}]):
            with self.subTest(teammates=teammates):
                assignment["teammate_player_ids"] = teammates
                with self.assertRaises(ValueError):
                    validator.validate_server(event)

    def test_core_event_visibility_is_required_and_limited_to_deliverable_events(self):
        validator = ProtocolMessageValidator()
        for visibility in ('public', 'private'):
            event = self.make_server_event(event_type='game.event', payload={
                'event_type': 'CUSTOM_NOTICE', 'event_payload': {'text': 'notice'}, 'visibility': visibility})
            validator.validate_server(event)
        for visibility in (None, 'server', 'ai', 'unknown'):
            payload = {'event_type': 'CUSTOM_NOTICE', 'event_payload': {}}
            if visibility is not None:
                payload['visibility'] = visibility
            with self.subTest(visibility=visibility), self.assertRaises(ValueError):
                validator.validate_server(self.make_server_event(event_type='game.event', payload=payload))

    def test_recorded_1_1_events_remain_valid_in_archived_schema(self):
        old_schema = PROJECT_ROOT / 'protocol/aiwolf-v1.1.schema.json'
        event = self.make_server_event(event_type='game.event', protocol_version='1.1', payload={
            'event_type': 'ROLE_ASSIGNED', 'event_payload': {'player_id': 'p-0', 'role_id': 'custom_role', 'modifier_ids': []}})
        ProtocolMessageValidator(schema_path=old_schema).validate_server(event)
        with self.assertRaises(ValueError):
            ProtocolMessageValidator().validate_server(event)

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
        self.assert_invalid(self.client_validator, dict(request, evil=True))
        self.assert_invalid(self.server_validator, dict(event, admin=True))

    def test_directional_schemas_inherit_headers_from_envelope(self) -> None:
        schema = deepcopy(self.schema)
        envelope = schema["$defs"]["envelope"]
        envelope["properties"]["trace_id"] = {"type": "string", "minLength": 1}
        envelope["required"].append("trace_id")

        request = dict(self.make_client_request(), trace_id="request-trace")
        event = dict(self.make_server_event(), trace_id="event-trace")
        client_validator = self.make_validator("client_request", schema)
        server_validator = self.make_validator("server_event", schema)
        self.assert_valid(client_validator, request)
        self.assert_valid(server_validator, event)
        self.assert_invalid(client_validator, self.make_client_request())
        self.assert_invalid(server_validator, self.make_server_event())

    def test_action_rejected_requires_machine_readable_action_and_reason(self) -> None:
        rejected = self.make_server_event(
            event_type="action.rejected",
            payload={
                "action": "ability.use",
                "reason": "invalid_target",
                "request_event_id": REQUEST_EVENT_ID,
            },
        )
        self.assert_valid(self.server_validator, rejected)
        self.assert_invalid(
            self.server_validator,
            dict(
                rejected,
                payload={"action": "ability.use", "request_event_id": REQUEST_EVENT_ID},
            ),
        )
        self.assert_invalid(
            self.server_validator,
            dict(
                rejected,
                payload={
                    "action": "",
                    "reason": "invalid_target",
                    "request_event_id": REQUEST_EVENT_ID,
                },
            ),
        )

    def test_phase3_5_acceptance_and_rejection_payloads_are_strict(self) -> None:
        accepted = self.make_server_event(
            event_type="action.accepted",
            payload={"action": "vote.cast", "request_event_id": REQUEST_EVENT_ID},
        )
        rejected = self.make_server_event(
            event_type="action.rejected",
            payload={
                "action": "vote.cast",
                "reason": "action_deadline_passed",
                "request_event_id": None,
            },
        )
        self.assert_valid(self.server_validator, accepted)
        self.assert_valid(self.server_validator, rejected)

        for invalid_payload in (
            {"action": "vote.cast"},
            {"action": "vote.cast", "request_event_id": None},
            {"action": "chat.send", "request_event_id": REQUEST_EVENT_ID},
            {
                "action": "vote.cast",
                "request_event_id": REQUEST_EVENT_ID,
                "extra": True,
            },
            *(
                {"action": "vote.cast", "request_event_id": event_id}
                for event_id in NON_CANONICAL_REQUEST_IDS
            ),
        ):
            with self.subTest(accepted_payload=invalid_payload):
                self.assert_invalid(
                    self.server_validator, dict(accepted, payload=invalid_payload)
                )

        for invalid_payload in (
            {"action": "vote.cast", "reason": "invalid_target"},
            *(
                {
                    "action": "vote.cast",
                    "reason": "invalid_target",
                    "request_event_id": event_id,
                }
                for event_id in NON_CANONICAL_REQUEST_IDS
            ),
            {
                "action": "vote.cast",
                "reason": "invalid_target",
                "request_event_id": REQUEST_EVENT_ID,
                "extra": True,
            },
        ):
            with self.subTest(rejected_payload=invalid_payload):
                self.assert_invalid(
                    self.server_validator, dict(rejected, payload=invalid_payload)
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
        *,
        event_type: str = "player.list",
        payload: dict[str, object] | None = None,
        protocol_version: str = "1.2",
    ) -> dict[str, object]:
        return {
            "type": event_type,
            "protocol_version": protocol_version,
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
            "protocol_version": "1.2",
            "event_id": REQUEST_EVENT_ID,
            "game_id": "123e4567-e89b-12d3-a456-426614174001",
            "timestamp": 0,
            "payload": {},
        }

    def assert_valid(self, validator: Draft202012Validator, message: object) -> None:
        errors = list(validator.iter_errors(message))
        self.assertEqual([], errors, [error.message for error in errors])

    def assert_invalid(self, validator: Draft202012Validator, message: object) -> None:
        self.assertTrue(list(validator.iter_errors(message)))

    def make_validator(
        self, definition: str, schema: dict[str, object] | None = None
    ) -> Draft202012Validator:
        resolved_schema = self.schema if schema is None else schema
        registry = Registry().with_resource(
            resolved_schema["$id"], Resource.from_contents(resolved_schema)
        )
        return Draft202012Validator(
            {"$ref": f"{resolved_schema['$id']}#/$defs/{definition}"},
            registry=registry,
            format_checker=FormatChecker(),
        )


if __name__ == "__main__":
    unittest.main()
