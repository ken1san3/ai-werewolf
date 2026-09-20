"""Pure adapters for the Phase 6 grounded-plan then message probe."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Mapping

from jsonschema import Draft202012Validator

from ai_client.discussion.context import canonical_json_bytes
from ai_client.llm.decision import parse_llm_output
from ai_client.llm.types import PromptProjection


PLAN_TOKENS = 384
MESSAGE_TOKENS = 128
PLAN_INSTRUCTION = (
    "Do not generate public message text in this call. Return only the grounded plan "
    "required by the response schema. Do not fill in or guess fields; use only the "
    "existing grounding and offered actions."
)
MESSAGE_INSTRUCTION = (
    "Keep every value in the locked plan unchanged. Return only the public message text "
    "required by the response schema, aligned with its speech act and evidence.\nLocked plan:"
)

_TEXT_FIELDS = {"chat": "message", "co_declare": "comment"}
_ACTION_KINDS = {"none", "chat", "vote", "ability", "co_declare"}
_ACTION_FIELDS = {
    "none": ("kind",),
    "chat": ("kind", "option_id", "message"),
    "vote": ("kind", "option_id", "target_player_id"),
    "ability": ("kind", "option_id", "target_player_ids"),
    "co_declare": ("kind", "option_id", "claimed_role_id", "comment"),
}
_SCHEMA_KEYWORDS = {
    "$defs", "$ref", "additionalProperties", "anyOf", "const", "enum", "items",
    "maxItems", "maxLength", "maximum", "minItems", "minLength", "minimum",
    "oneOf", "properties", "required", "type", "uniqueItems",
}


class _DuplicateKey(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError
    return parsed


def _constant(_value: str):
    raise ValueError


def strict_json(raw):
    """Parse one strict JSON object without duplicate keys or non-finite numbers."""
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


def _json_text(value):
    try:
        return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("JSON_SYNTAX") from None


def _decision_branches(schema):
    try:
        branches = schema["properties"]["decision"]["oneOf"]
    except (KeyError, TypeError):
        raise ValueError("SCHEMA_SHAPE_CHANGED") from None
    if not isinstance(branches, list) or not branches:
        raise ValueError("SCHEMA_SHAPE_CHANGED")
    found = {}
    for branch in branches:
        try:
            kind = branch["properties"]["kind"]["const"]
        except (KeyError, TypeError):
            raise ValueError("SCHEMA_SHAPE_CHANGED") from None
        if kind not in _ACTION_KINDS or kind in found:
            raise ValueError("SCHEMA_SHAPE_CHANGED")
        if branch.get("type") != "object" or branch.get("additionalProperties") is not False:
            raise ValueError("SCHEMA_SHAPE_CHANGED")
        fields = _ACTION_FIELDS[kind]
        if set(branch["properties"]) != set(fields) or set(branch.get("required", ())) != set(fields):
            raise ValueError("SCHEMA_SHAPE_CHANGED")
        found[kind] = branch
    return found


def _validate_schema_contract(schema):
    if set(schema) != {"$defs", "additionalProperties", "properties", "required", "type"}:
        raise ValueError("SCHEMA_SHAPE_CHANGED")
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        raise ValueError("SCHEMA_SHAPE_CHANGED")

    def visit(node):
        if not isinstance(node, dict) or any(key not in _SCHEMA_KEYWORDS for key in node):
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
            if not isinstance(properties, dict) or any(type(key) is not str for key in properties):
                raise ValueError("SCHEMA_SHAPE_CHANGED")
            for child in properties.values():
                visit(child)
        required = node.get("required")
        if required is not None:
            if (not isinstance(required, list) or any(type(key) is not str for key in required)
                    or len(required) != len(set(required)) or properties is None
                    or any(key not in properties for key in required)):
                raise ValueError("SCHEMA_SHAPE_CHANGED")
        for keyword in ("oneOf", "anyOf"):
            if keyword in node:
                choices = node[keyword]
                if not isinstance(choices, list) or not choices:
                    raise ValueError("SCHEMA_SHAPE_CHANGED")
                for child in choices:
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


def plan_schema(legacy_schema):
    schema = _plain(legacy_schema)
    _validate_schema_contract(schema)
    branches = _decision_branches(schema)
    for kind, field in _TEXT_FIELDS.items():
        branch = branches.get(kind)
        if branch is None:
            continue
        properties = branch.get("properties")
        required = branch.get("required")
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise ValueError("SCHEMA_SHAPE_CHANGED")
        if field not in properties or required.count(field) != 1:
            raise ValueError("SCHEMA_SHAPE_CHANGED")
        del properties[field]
        required.remove(field)
    Draft202012Validator.check_schema(schema)
    return schema


def _body_copy(body):
    value = _plain(body)
    if not isinstance(value, dict) or not isinstance(value.get("messages"), list):
        raise ValueError("BODY_SHAPE_CHANGED")
    if len(value["messages"]) != 2:
        raise ValueError("BODY_SHAPE_CHANGED")
    try:
        legacy_schema = value["response_format"]["json_schema"]["schema"]
    except (KeyError, TypeError):
        raise ValueError("BODY_SHAPE_CHANGED") from None
    return value, legacy_schema


def plan_body(baseline_body):
    body, legacy_schema = _body_copy(baseline_body)
    body["messages"].append({"role": "user", "content": PLAN_INSTRUCTION})
    body["response_format"]["json_schema"]["schema"] = plan_schema(legacy_schema)
    body["max_tokens"] = PLAN_TOKENS
    return body


def validate_plan(raw, projection):
    if not isinstance(projection, PromptProjection):
        raise TypeError("projection must be PromptProjection")
    value = strict_json(raw)
    schema = plan_schema(projection.decision_schema)
    if not Draft202012Validator(schema).is_valid(value):
        raise ValueError("PLAN_SCHEMA_INVALID")
    checked = deepcopy(value)
    decision = checked["decision"]
    parsed = None
    try:
        field = _TEXT_FIELDS.get(decision["kind"])
        if field is not None:
            decision[field] = "."
        # Never derive the returned plan from this placeholder-bearing result.
        parsed = parse_llm_output(
            canonical_json_bytes(checked).decode("utf-8"), projection=projection
        )
    finally:
        decision.clear()
        checked.clear()
        del parsed, decision, checked
    return deepcopy(value)


def _text_schema(legacy_schema, kind):
    field = _TEXT_FIELDS[kind]
    branch = _decision_branches(_plain(legacy_schema)).get(kind)
    if branch is None:
        raise ValueError("PLAN_KIND_NOT_OFFERED")
    try:
        scalar = deepcopy(branch["properties"][field])
    except (KeyError, TypeError):
        raise ValueError("SCHEMA_SHAPE_CHANGED") from None
    schema = {"type": "object", "properties": {field: scalar},
              "required": [field], "additionalProperties": False}
    Draft202012Validator.check_schema(schema)
    return schema


def message_body(baseline_body, plan, projection):
    if not isinstance(projection, PromptProjection):
        raise TypeError("projection must be PromptProjection")
    body, legacy_schema = _body_copy(baseline_body)
    plan_value = validate_plan(_json_text(plan), projection)
    kind = plan_value["decision"]["kind"]
    if kind not in _TEXT_FIELDS:
        raise ValueError("NO_MESSAGE_ACTION")
    locked = canonical_json_bytes(plan_value).decode("utf-8")
    body["messages"].append(
        {"role": "user", "content": MESSAGE_INSTRUCTION + "\n" + locked}
    )
    body["response_format"]["json_schema"]["schema"] = _text_schema(legacy_schema, kind)
    body["max_tokens"] = MESSAGE_TOKENS
    return body


def validate_final(plan, text_raw_or_none, projection):
    if not isinstance(projection, PromptProjection):
        raise TypeError("projection must be PromptProjection")
    plan_value = validate_plan(_json_text(plan), projection)
    kind = plan_value["decision"]["kind"]
    field = _TEXT_FIELDS.get(kind)
    final = deepcopy(plan_value)
    if field is None:
        if text_raw_or_none is not None:
            raise ValueError("UNEXPECTED_MESSAGE")
    else:
        if text_raw_or_none is None:
            raise ValueError("MESSAGE_MISSING")
        text = strict_json(text_raw_or_none)
        schema = _text_schema(projection.decision_schema, kind)
        if not Draft202012Validator(schema).is_valid(text):
            raise ValueError("MESSAGE_SCHEMA_INVALID")
        final["decision"][field] = text[field]
    parse_llm_output(canonical_json_bytes(final).decode("utf-8"), projection=projection)
    return final


def split_legacy(value, legacy_schema):
    legacy = _plain(value)
    schema = _plain(legacy_schema)
    _decision_branches(schema)
    if not Draft202012Validator(schema).is_valid(legacy):
        raise ValueError("LEGACY_SCHEMA_INVALID")
    try:
        kind = legacy["decision"]["kind"]
    except (KeyError, TypeError):
        raise ValueError("LEGACY_SCHEMA_INVALID") from None
    if kind not in _ACTION_KINDS:
        raise ValueError("LEGACY_KIND_INVALID")
    plan = deepcopy(legacy)
    field = _TEXT_FIELDS.get(kind)
    if field is None:
        return plan, None
    text = {field: plan["decision"].pop(field)}
    return plan, text
