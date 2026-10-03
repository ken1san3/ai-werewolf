"""Protocol-schema validation at the WebSocket trust boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import best_match
from referencing import Registry, Resource


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "protocol" / "aiwolf-v1.2.schema.json"
PROTOCOL_VERSION = "1.2"

_ACTIVE_SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
if _ACTIVE_SCHEMA.get("$defs", {}).get("protocol_version", {}).get("const") != PROTOCOL_VERSION:
    raise RuntimeError("active protocol schema and server protocol version disagree")


class ProtocolValidationError(ValueError):
    """A client or server message does not satisfy the versioned schema."""


class ProtocolMessageValidator:
    """Validate common envelopes and every schema-declared network message type."""

    def __init__(
        self,
        schema_path: Path = SCHEMA_PATH,
        *,
        schema: Mapping[str, Any] | None = None,
    ) -> None:
        schema = json.loads(schema_path.read_text(encoding="utf-8")) if schema is None else schema
        self._schema_id = schema["$id"]
        self._client_definitions = _message_definitions(schema, "client_request")
        self._server_definitions = _message_definitions(schema, "server_event")
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
                *self._client_definitions.values(),
                *self._server_definitions.values(),
            }
        }

    def validate_client(self, message: Mapping[str, Any]) -> None:
        self._validate("client_request", message)
        message_type = message.get("type")
        if (
            isinstance(message_type, str)
            and message_type in self._server_definitions
            and message_type not in self._client_definitions
        ):
            raise ProtocolValidationError(f"'{message_type}' is a server-only message type")
        if isinstance(message_type, str) and message_type in self._client_definitions:
            self._validate(self._client_definitions[message_type], message)

    def validate_server(self, message: Mapping[str, Any]) -> None:
        self._validate("server_event", message)
        message_type = message.get("type")
        if (
            isinstance(message_type, str)
            and message_type in self._client_definitions
            and message_type not in self._server_definitions
        ):
            raise ProtocolValidationError(f"'{message_type}' is a client-only message type")
        if isinstance(message_type, str) and message_type in self._server_definitions:
            self._validate(self._server_definitions[message_type], message)

    def _validate(self, definition: str, message: Mapping[str, Any]) -> None:
        errors = list(self._validators[definition].iter_errors(message))
        if errors:
            raise ProtocolValidationError(best_match(errors).message)


def _message_definitions(schema: Mapping[str, Any], direction: str) -> dict[str, str]:
    """Derive type-specific validators from the protocol's own ``$defs``."""

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
                message_type
                for branch in branches
                if isinstance(branch, Mapping)
                for message_type in (_message_type_constant(branch),)
                if message_type is not None
            ),
            None,
        )
        if message_type is None:
            continue
        if message_type in result:
            raise ValueError(
                f"protocol schema has duplicate {direction} type '{message_type}'"
            )
        result[message_type] = definition_name
    return result


def _message_type_constant(branch: Mapping[str, Any]) -> str | None:
    properties = branch.get("properties")
    if not isinstance(properties, Mapping):
        return None
    type_schema = properties.get("type")
    if not isinstance(type_schema, Mapping):
        return None
    message_type = type_schema.get("const")
    return message_type if isinstance(message_type, str) else None
