"""Pure schema and validation adapters for the Phase 6 IC2 probe."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Mapping

from jsonschema import Draft202012Validator

from ai_client.discussion.context import canonical_json_bytes
from ai_client.llm.decision import parse_llm_output
from ai_client.llm.types import PromptProjection


CHOICE_TOKENS = 32
OUTPUT_TOKENS = 480
CHOICE_INSTRUCTION = (
    "Using only the existing canonical input, choose the single speech act kind most "
    "appropriate for the later public response and return it exactly as the response "
    "schema requires. Do not generate its fields, evidence, or message text yet."
)
OUTPUT_INSTRUCTION = (
    "Keep the locked speech act kind unchanged. Using only the existing grounding and "
    "offered actions, return the complete legacy output required by the response schema.\n"
    "Locked choice:"
)

_KINDS = (
    "NONE", "CLAIM", "QUESTION", "ANSWER", "REBUTTAL", "OPINION_CHANGE",
    "RELATION_HYPOTHESIS",
)
_FIELDS = {
    "NONE": {"kind"},
    "CLAIM": {"kind", "subject_player_id", "topic", "stance", "evidence"},
    "QUESTION": {"kind", "addressee_player_id", "subject_player_id", "topic", "source"},
    "ANSWER": {"kind", "addressee_player_id", "in_reply_to", "source_interpretation",
               "topic", "stance", "evidence"},
    "REBUTTAL": {"kind", "addressee_player_id", "in_reply_to", "source_interpretation",
                 "topic", "stance", "evidence"},
    "OPINION_CHANGE": {"kind", "subject_player_id", "dimension", "prior", "current", "causes"},
    "RELATION_HYPOTHESIS": {"kind", "source_player_id", "target_player_id", "relation",
                            "confidence", "evidence"},
}
_KEYWORDS = {
    "$defs", "$ref", "additionalProperties", "anyOf", "const", "enum", "items",
    "maxItems", "maxLength", "maximum", "minItems", "minLength", "minimum", "oneOf",
    "properties", "required", "type", "uniqueItems",
}


class _DuplicateKey(ValueError):
    pass


def _pairs(items):
    value = {}
    for key, child in items:
        if key in value:
            raise _DuplicateKey
        value[key] = child
    return value


def _float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError
    return value


def _constant(_text):
    raise ValueError


def strict_json(raw):
    if not isinstance(raw, str) or not raw:
        raise ValueError("JSON_SYNTAX")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_float=_float,
                           parse_constant=_constant)
    except _DuplicateKey:
        raise ValueError("JSON_DUPLICATE_KEY") from None
    except (TypeError, ValueError, json.JSONDecodeError, OverflowError):
        raise ValueError("JSON_SYNTAX") from None
    if not isinstance(value, dict):
        raise ValueError("JSON_SCHEMA")
    return value


def _plain(value):
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("NON_PLAIN_VALUE")
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(child) for child in value]
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("NON_PLAIN_VALUE")


def _schema_contract(schema):
    if set(schema) != {"$defs", "additionalProperties", "properties", "required", "type"}:
        raise ValueError("SCHEMA_SHAPE_CHANGED")
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        raise ValueError("SCHEMA_SHAPE_CHANGED")

    def visit(node):
        if not isinstance(node, dict) or any(key not in _KEYWORDS for key in node):
            raise ValueError("SCHEMA_SHAPE_CHANGED")
        if "$ref" in node:
            ref = node["$ref"]
            if (set(node) != {"$ref"} or not isinstance(ref, str)
                    or not ref.startswith("#/$defs/")
                    or ref.removeprefix("#/$defs/") not in definitions):
                raise ValueError("SCHEMA_SHAPE_CHANGED")
            return
        properties = node.get("properties")
        if properties is not None:
            if not isinstance(properties, dict):
                raise ValueError("SCHEMA_SHAPE_CHANGED")
            for child in properties.values():
                visit(child)
        required = node.get("required")
        if required is not None and (
            not isinstance(required, list) or any(type(key) is not str for key in required)
            or len(required) != len(set(required)) or properties is None
            or any(key not in properties for key in required)
        ):
            raise ValueError("SCHEMA_SHAPE_CHANGED")
        for keyword in ("oneOf", "anyOf"):
            if keyword in node:
                children = node[keyword]
                if not isinstance(children, list) or not children:
                    raise ValueError("SCHEMA_SHAPE_CHANGED")
                for child in children:
                    visit(child)
        if "items" in node:
            visit(node["items"])
        if "$defs" in node:
            nested = node["$defs"]
            if not isinstance(nested, dict):
                raise ValueError("SCHEMA_SHAPE_CHANGED")
            for child in nested.values():
                visit(child)
        if "additionalProperties" in node and type(node["additionalProperties"]) is not bool:
            raise ValueError("SCHEMA_SHAPE_CHANGED")

    visit(schema)


def _speech_branches(schema):
    _schema_contract(schema)
    try:
        speech = schema["$defs"]["speech_act"]
        branches = speech["oneOf"]
    except (KeyError, TypeError):
        raise ValueError("SCHEMA_SHAPE_CHANGED") from None
    if set(speech) != {"oneOf"} or not isinstance(branches, list) or len(branches) != 7:
        raise ValueError("SCHEMA_SHAPE_CHANGED")
    found = []
    for branch in branches:
        try:
            kind = branch["properties"]["kind"]["const"]
            properties = branch["properties"]
            required = branch["required"]
        except (KeyError, TypeError):
            raise ValueError("SCHEMA_SHAPE_CHANGED") from None
        if (kind not in _FIELDS or kind in found or branch.get("type") != "object"
                or branch.get("additionalProperties") is not False
                or set(properties) != _FIELDS[kind] or set(required) != _FIELDS[kind]):
            raise ValueError("SCHEMA_SHAPE_CHANGED")
        found.append(kind)
    if tuple(found) != _KINDS:
        raise ValueError("SCHEMA_SHAPE_CHANGED")
    return branches


def choice_schema(legacy_schema):
    schema = _plain(legacy_schema)
    branches = _speech_branches(schema)
    kinds = [branch["properties"]["kind"]["const"] for branch in branches]
    return {"type": "object", "properties": {"speech_act_kind": {"type": "string",
        "enum": kinds}}, "required": ["speech_act_kind"], "additionalProperties": False}


def _body(body):
    value = _plain(body)
    if not isinstance(value, dict) or not isinstance(value.get("messages"), list):
        raise ValueError("BODY_SHAPE_CHANGED")
    if len(value["messages"]) != 2:
        raise ValueError("BODY_SHAPE_CHANGED")
    try:
        schema = value["response_format"]["json_schema"]["schema"]
    except (KeyError, TypeError):
        raise ValueError("BODY_SHAPE_CHANGED") from None
    return value, schema


def choice_body(baseline_body):
    body, legacy_schema = _body(baseline_body)
    body["messages"].append({"role": "user", "content": CHOICE_INSTRUCTION})
    body["response_format"]["json_schema"]["schema"] = choice_schema(legacy_schema)
    body["max_tokens"] = CHOICE_TOKENS
    return body


def validate_choice(raw, projection):
    if not isinstance(projection, PromptProjection):
        raise TypeError("projection must be PromptProjection")
    value = strict_json(raw)
    if not Draft202012Validator(choice_schema(projection.decision_schema)).is_valid(value):
        raise ValueError("CHOICE_SCHEMA_INVALID")
    return deepcopy(value)


def locked_schema(legacy_schema, choice):
    schema = _plain(legacy_schema)
    branches = _speech_branches(schema)
    choice_value = strict_json(json.dumps(_plain(choice), ensure_ascii=False,
                                          separators=(",", ":"), allow_nan=False))
    if not Draft202012Validator(choice_schema(schema)).is_valid(choice_value):
        raise ValueError("CHOICE_SCHEMA_INVALID")
    selected = choice_value["speech_act_kind"]
    branch = next(item for item in branches
                  if item["properties"]["kind"]["const"] == selected)
    schema["$defs"]["speech_act"] = deepcopy(branch)
    Draft202012Validator.check_schema(schema)
    return schema


def output_body(baseline_body, choice, projection):
    if not isinstance(projection, PromptProjection):
        raise TypeError("projection must be PromptProjection")
    body, legacy_schema = _body(baseline_body)
    choice_value = validate_choice(json.dumps(_plain(choice), ensure_ascii=False,
                                              separators=(",", ":")), projection)
    canonical = canonical_json_bytes(choice_value).decode("utf-8")
    body["messages"].append(
        {"role": "user", "content": OUTPUT_INSTRUCTION + "\n" + canonical}
    )
    body["response_format"]["json_schema"]["schema"] = locked_schema(
        legacy_schema, choice_value
    )
    body["max_tokens"] = OUTPUT_TOKENS
    return body


def validate_final(raw, choice, projection):
    if not isinstance(projection, PromptProjection):
        raise TypeError("projection must be PromptProjection")
    choice_value = validate_choice(json.dumps(_plain(choice), ensure_ascii=False,
                                              separators=(",", ":")), projection)
    value = strict_json(raw)
    schema = locked_schema(projection.decision_schema, choice_value)
    if not Draft202012Validator(schema).is_valid(value):
        raise ValueError("OUTPUT_SCHEMA_INVALID")
    try:
        kind = value["discussion"]["speech_act"]["kind"]
    except (KeyError, TypeError):
        raise ValueError("OUTPUT_KIND_MISMATCH") from None
    if kind != choice_value["speech_act_kind"]:
        raise ValueError("OUTPUT_KIND_MISMATCH")
    parse_llm_output(raw, projection=projection)
    return value
