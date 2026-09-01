"""Canonical protocol-schema validation for the client boundary."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import best_match
from referencing import Registry, Resource


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "protocol" / "aiwolf-v1.schema.json"
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+$")


class ProtocolValidationError(ValueError):
    """A message does not satisfy the canonical language-independent schema."""


class ProtocolMessageValidator:
    """Validate a common envelope and its schema-declared concrete message type."""

    def __init__(
        self,
        schema_path: Path = SCHEMA_PATH,
        *,
        schema: Mapping[str, Any] | None = None,
        reject_unknown_types: bool = True,
    ) -> None:
        loaded = json.loads(schema_path.read_text(encoding="utf-8")) if schema is None else schema
        if not isinstance(loaded, Mapping):
            raise ValueError("protocol schema must be an object")
        self.schema = loaded
        schema_id = loaded.get("$id")
        if not isinstance(schema_id, str) or not schema_id:
            raise ValueError("protocol schema must define $id")
        self.schema_id = schema_id
        self.reject_unknown_types = reject_unknown_types
        self.client_definitions = _message_definitions(loaded, "client_request")
        self.server_definitions = _message_definitions(loaded, "server_event")
        # action.rejected is declared by the server_event conditional in the
        # canonical schema rather than as a separate allOf definition.
        self._known_server_types = frozenset(
            {*self.server_definitions, "action.rejected"}
        )
        self._registry = Registry().with_resource(
            schema_id, Resource.from_contents(loaded)
        )
        definitions = {
            "client_request",
            "server_event",
            "action_rejected_payload",
            *self.client_definitions.values(),
            *self.server_definitions.values(),
        }
        self._validators = {
            definition: Draft202012Validator(
                {"$ref": f"{schema_id}#/$defs/{definition}"},
                registry=self._registry,
                format_checker=FormatChecker(),
            )
            for definition in definitions
        }

    @property
    def protocol_version(self) -> str:
        match = re.search(r"(?<!\d)(\d+\.\d+)$", self.schema_id)
        if match is None:
            raise ValueError("schema id must end with a protocol major.minor version")
        return match.group(1)

    @property
    def client_message_types(self) -> frozenset[str]:
        return frozenset(self.client_definitions)

    @property
    def server_message_types(self) -> frozenset[str]:
        return self._known_server_types

    def validate_client(self, message: Mapping[str, Any]) -> None:
        self._validate("client_request", message)
        message_type = message.get("type")
        definition = self.client_definitions.get(message_type)
        if definition is None:
            if self.reject_unknown_types:
                raise ProtocolValidationError(f"unknown client message type: {message_type!r}")
            return
        self._validate(definition, message)

    def validate_server(self, message: Mapping[str, Any]) -> None:
        self._validate("server_event", message)
        message_type = message.get("type")
        definition = self.server_definitions.get(message_type)
        if definition is None:
            if message_type == "action.rejected":
                self._validate("action_rejected_payload", message["payload"])
                return
            if self.reject_unknown_types:
                raise ProtocolValidationError(f"unknown server message type: {message_type!r}")
            return
        self._validate(definition, message)

    def decode_server(self, raw_message: str | bytes) -> dict[str, Any]:
        if isinstance(raw_message, bytes):
            try:
                raw_message = raw_message.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ProtocolValidationError("binary message is not valid UTF-8") from error
        if not isinstance(raw_message, str):
            raise ProtocolValidationError("server message must be text")
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError as error:
            raise ProtocolValidationError("server message is not valid JSON") from error
        if not isinstance(message, dict):
            raise ProtocolValidationError("server message must be an object")
        self.validate_server(message)
        return message

    def _validate(self, definition: str, message: Mapping[str, Any]) -> None:
        errors = list(self._validators[definition].iter_errors(message))
        if errors:
            raise ProtocolValidationError(best_match(errors).message)


def make_client_request(
    message_type: str,
    game_id: str,
    payload: Mapping[str, Any],
    *,
    protocol_version: str | None = None,
    timestamp: int,
    event_id_factory: Callable[[], str] = lambda: str(uuid4()),
    validator: ProtocolMessageValidator | None = None,
) -> dict[str, Any]:
    validator = validator or ProtocolMessageValidator()
    version = protocol_version or validator.protocol_version
    if not _VERSION_PATTERN.fullmatch(version):
        raise ProtocolValidationError("protocol version must be major.minor")
    message = {
        "type": message_type,
        "protocol_version": version,
        "event_id": event_id_factory(),
        "game_id": game_id,
        "timestamp": timestamp,
        "payload": dict(payload),
    }
    validator.validate_client(message)
    return message


def same_major_version(left: str, right: str) -> bool:
    return left.partition(".")[0] == right.partition(".")[0]


def _message_definitions(schema: Mapping[str, Any], direction: str) -> dict[str, str]:
    definitions = schema.get("$defs")
    if not isinstance(definitions, Mapping):
        raise ValueError("protocol schema must define $defs")
    direction_ref = f"#/$defs/{direction}"
    result: dict[str, str] = {}
    for definition_name, definition in definitions.items():
        if not isinstance(definition_name, str) or not isinstance(definition, Mapping):
            continue
        branches = definition.get("allOf")
        if not isinstance(branches, list) or not any(
            isinstance(branch, Mapping) and branch.get("$ref") == direction_ref
            for branch in branches
        ):
            continue
        message_type = next(
            (
                value
                for branch in branches
                if isinstance(branch, Mapping)
                for value in (_message_type_constant(branch),)
                if value is not None
            ),
            None,
        )
        if message_type is None:
            continue
        if message_type in result:
            raise ValueError(f"duplicate {direction} message type: {message_type}")
        result[message_type] = definition_name
    return result


def _message_type_constant(branch: Mapping[str, Any]) -> str | None:
    properties = branch.get("properties")
    if not isinstance(properties, Mapping):
        return None
    type_schema = properties.get("type")
    if not isinstance(type_schema, Mapping):
        return None
    value = type_schema.get("const")
    return value if isinstance(value, str) else None


PROTOCOL_VERSION = ProtocolMessageValidator().protocol_version


__all__ = [
    "PROTOCOL_VERSION",
    "ProtocolMessageValidator",
    "ProtocolValidationError",
    "SCHEMA_PATH",
    "make_client_request",
    "same_major_version",
]
