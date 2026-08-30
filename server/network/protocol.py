"""Protocol-schema validation at the WebSocket trust boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "protocol" / "aiwolf-v1.schema.json"

_CLIENT_DEFINITIONS = {
    "session.join": "session_join_request",
    "session.resume": "session_resume_request",
    "session.ready": "session_ready_request",
}
_SERVER_DEFINITIONS = {
    "session.joined": "session_joined_event",
    "session.resumed": "session_resumed_event",
    "session.ready": "session_ready_event",
}


class ProtocolValidationError(ValueError):
    """A client or server message does not satisfy the versioned schema."""


class ProtocolMessageValidator:
    """Validate common envelopes and the Session message types used in Phase 2.2."""

    def __init__(self, schema_path: Path = SCHEMA_PATH) -> None:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self._schema_id = schema["$id"]
        self._registry = Registry().with_resource(
            self._schema_id, Resource.from_contents(schema)
        )
        self._validators = {
            definition: Draft202012Validator(
                {"$ref": f"{self._schema_id}#/$defs/{definition}"},
                registry=self._registry,
                format_checker=FormatChecker(),
            )
            for definition in {
                "client_request",
                "server_event",
                *_CLIENT_DEFINITIONS.values(),
                *_SERVER_DEFINITIONS.values(),
            }
        }

    def validate_client(self, message: Mapping[str, Any]) -> None:
        self._validate("client_request", message)
        message_type = message.get("type")
        if isinstance(message_type, str) and message_type in _CLIENT_DEFINITIONS:
            self._validate(_CLIENT_DEFINITIONS[message_type], message)

    def validate_server(self, message: Mapping[str, Any]) -> None:
        self._validate("server_event", message)
        message_type = message.get("type")
        if isinstance(message_type, str) and message_type in _SERVER_DEFINITIONS:
            self._validate(_SERVER_DEFINITIONS[message_type], message)

    def _validate(self, definition: str, message: Mapping[str, Any]) -> None:
        errors = sorted(
            self._validators[definition].iter_errors(message),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            raise ProtocolValidationError(errors[0].message)
