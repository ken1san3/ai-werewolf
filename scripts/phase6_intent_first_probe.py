"""Pure helpers for the test-only Phase 6 intent-first experiment."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from scripts.phase6_context_probe import wire_bytes

DISCUSSION_FIELDS = (
    "speech_act", "base_revision", "co_judgment", "decision_kind", "option_id",
    "pre_vote_reassessment", "reaction", "schema_version",
)
UPDATE_FIELDS = ("assessment_updates", "claim_updates", "relation_updates", "strategy_update")
ROOT_ORDER = ("intent", "realization", "updates")
TEXT_FIELD = {"chat": "message", "co_declare": "comment"}
DECISION_FIELDS = {
    "none": ("kind",),
    "chat": ("kind", "option_id", "message"),
    "vote": ("kind", "option_id", "target_player_id"),
    "ability": ("kind", "option_id", "target_player_ids"),
    "co_declare": ("kind", "option_id", "claimed_role_id", "comment"),
}
LEGACY_DISCUSSION_FIELDS = frozenset(DISCUSSION_FIELDS) | frozenset(UPDATE_FIELDS)


def _shape_error() -> ValueError:
    return ValueError("SCHEMA_SHAPE_CHANGED")


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _validate_schema(schema):
    schema = _plain(schema)
    def local_only(value):
        if isinstance(value, dict):
            ref = value.get("$ref")
            if ref is not None and (not isinstance(ref, str) or not ref.startswith("#/$defs/")):
                raise _shape_error()
            for child in value.values():
                local_only(child)
        elif isinstance(value, list):
            for child in value:
                local_only(child)
    local_only(schema)
    Draft202012Validator.check_schema(schema)


def _closed(properties, required):
    return {"type": "object", "properties": properties,
            "required": list(required), "additionalProperties": False}


def _const(schema):
    return schema.get("const") if isinstance(schema, dict) else None


def _allows(schema, value):
    return Draft202012Validator(schema).is_valid(value)


def _legacy_pairs(schema):
    schema = _plain(schema)
    try:
        if (set(schema) != {"$defs", "type", "properties", "required", "additionalProperties"}
                or schema["type"] != "object" or schema["additionalProperties"] is not False
                or set(schema["properties"]) != {"decision", "discussion"}
                or set(schema["required"]) != {"decision", "discussion"}):
            raise _shape_error()
        decision_union = schema["properties"]["decision"]
        discussion_union = schema["properties"]["discussion"]
        if set(decision_union) != {"oneOf"} or set(discussion_union) != {"oneOf"}:
            raise _shape_error()
        decisions = decision_union["oneOf"]
        discussions = discussion_union["oneOf"]
        pairs, signatures = [], set()
        for decision in decisions:
            props = decision["properties"]
            kind = _const(props.get("kind"))
            expected = DECISION_FIELDS.get(kind)
            if (set(decision) != {"type", "properties", "required", "additionalProperties"}
                    or expected is None or decision.get("type") != "object"
                    or decision.get("additionalProperties") is not False):
                raise _shape_error()
            if set(props) != set(expected) or set(decision.get("required", ())) != set(expected):
                raise _shape_error()
            option = None if kind == "none" else _const(props.get("option_id"))
            if kind != "none" and option is None:
                raise _shape_error()
            signature = (kind, option)
            if signature in signatures:
                raise _shape_error()
            signatures.add(signature)
            matches = []
            for proposal in discussions:
                pprops = proposal.get("properties", {})
                if (set(proposal) == {"type", "properties", "required", "additionalProperties"}
                        and proposal.get("type") == "object"
                        and proposal.get("additionalProperties") is False
                        and set(pprops) == LEGACY_DISCUSSION_FIELDS
                        and set(proposal.get("required", ())) == LEGACY_DISCUSSION_FIELDS
                        and _const(pprops.get("decision_kind")) == kind
                        and _allows(pprops.get("option_id", {}), option)):
                    matches.append(proposal)
            if len(matches) != 1:
                raise _shape_error()
            pairs.append((kind, option, decision, matches[0]))
        if not pairs:
            raise _shape_error()
        return pairs
    except (KeyError, TypeError, SchemaError):
        raise _shape_error() from None


def _candidate_pairs(schema):
    """Validate only the closed wrapper shape introduced by this experiment."""
    schema = _plain(schema)
    try:
        if set(schema) != {"$defs", "oneOf"} or not isinstance(schema["oneOf"], list):
            raise _shape_error()
        signatures = set()
        for root in schema["oneOf"]:
            if (set(root) != {"type", "properties", "required", "additionalProperties"}
                    or root["type"] != "object" or root["additionalProperties"] is not False
                    or set(root["properties"]) != set(ROOT_ORDER)
                    or set(root["required"]) != set(ROOT_ORDER)):
                raise _shape_error()
            intent, realization, updates = (root["properties"][key] for key in ROOT_ORDER)
            if (set(intent) != {"type", "properties", "required", "additionalProperties"}
                    or intent["type"] != "object" or intent["additionalProperties"] is not False
                    or set(intent["properties"]) != {"discussion", "decision"}
                    or set(intent["required"]) != {"discussion", "decision"}):
                raise _shape_error()
            discussion, decision = intent["properties"]["discussion"], intent["properties"]["decision"]
            for item in (discussion, decision, realization, updates):
                if (set(item) != {"type", "properties", "required", "additionalProperties"}
                        or item["type"] != "object" or item["additionalProperties"] is not False
                        or set(item["required"]) != set(item["properties"])):
                    raise _shape_error()
            if set(discussion["properties"]) != set(DISCUSSION_FIELDS):
                raise _shape_error()
            if set(updates["properties"]) != set(UPDATE_FIELDS):
                raise _shape_error()
            dprops, rprops = decision["properties"], realization["properties"]
            kind = _const(dprops.get("kind"))
            text = TEXT_FIELD.get(kind)
            expected = DECISION_FIELDS.get(kind)
            if expected is None or set(dprops) != set(expected) - ({text} if text else set()):
                raise _shape_error()
            if set(rprops) != (set() if text is None else {text}):
                raise _shape_error()
            option = None if kind == "none" else _const(dprops.get("option_id"))
            signature = (kind, option)
            if (kind != "none" and option is None) or signature in signatures:
                raise _shape_error()
            signatures.add(signature)
        if not signatures:
            raise _shape_error()
        return schema
    except (KeyError, TypeError):
        raise _shape_error() from None


def _candidate_schema(legacy_schema):
    legacy_schema = _plain(legacy_schema)
    pairs = _legacy_pairs(legacy_schema)
    roots = []
    for kind, _, decision, proposal in pairs:
        dprops = decision["properties"]
        text = TEXT_FIELD.get(kind)
        intent_decision = {k: deepcopy(v) for k, v in sorted(dprops.items()) if k != text}
        realization = {} if text is None else {text: deepcopy(dprops[text])}
        pprops = proposal["properties"]
        discussion = {k: deepcopy(pprops[k]) for k in DISCUSSION_FIELDS}
        updates = {k: deepcopy(pprops[k]) for k in sorted(UPDATE_FIELDS)}
        roots.append(_closed({
            "intent": _closed({
                "discussion": _closed(discussion, DISCUSSION_FIELDS),
                "decision": _closed(intent_decision, sorted(intent_decision)),
            }, ("discussion", "decision")),
            "realization": _closed(realization, sorted(realization)),
            "updates": _closed(updates, sorted(updates)),
        }, ROOT_ORDER))
    result = {"$defs": deepcopy(legacy_schema.get("$defs", {})), "oneOf": roots}
    _validate_schema(result)
    return result


def candidate_body(baseline_body):
    """Return a canonical copy of a baseline request with only its schema replaced."""
    try:
        body = json.loads(wire_bytes(baseline_body))
        old = body["response_format"]["json_schema"]["schema"]
        body["response_format"]["json_schema"]["schema"] = _candidate_schema(old)
        return body
    except (KeyError, TypeError, ValueError, SchemaError, RecursionError):
        raise _shape_error() from None


def intent_first_wire_bytes(body):
    """Canonicalize a candidate request, restoring only declared wrapper order."""
    candidate = json.loads(wire_bytes(body))
    try:
        schema = candidate["response_format"]["json_schema"]["schema"]
        _candidate_pairs(schema)
        for branch in schema["oneOf"]:
            props = branch["properties"]
            branch["properties"] = {key: props[key] for key in ROOT_ORDER}
            intent = branch["properties"]["intent"]
            iprops = intent["properties"]
            intent["properties"] = {key: iprops[key] for key in ("discussion", "decision")}
            discussion = intent["properties"]["discussion"]
            dprops = discussion["properties"]
            discussion["properties"] = {key: dprops[key] for key in DISCUSSION_FIELDS}
    except (KeyError, TypeError, ValueError):
        raise _shape_error() from None
    return json.dumps(candidate, ensure_ascii=False, sort_keys=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def reject_constant(_):
        raise ValueError("NONFINITE_JSON_NUMBER")
    def finite_float(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("NONFINITE_JSON_NUMBER")
        return value
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant,
                      parse_float=finite_float)


def candidate_to_legacy(value, schema):
    """Map a candidate value to legacy form; schema must be the candidate schema."""
    schema = _plain(schema)
    _validate_schema(schema)
    _candidate_pairs(schema)
    if not Draft202012Validator(schema).is_valid(value):
        raise ValueError("CANDIDATE_SCHEMA_INVALID")
    result = deepcopy(value)
    intent = result.pop("intent")
    realization = result.pop("realization")
    updates = result.pop("updates")
    if result or set(intent) != {"discussion", "decision"}:
        raise ValueError("CANDIDATE_SCHEMA_INVALID")
    decision, discussion = intent["decision"], intent["discussion"]
    if set(decision) & set(realization) or set(discussion) & set(updates):
        raise ValueError("ADAPTER_KEY_COLLISION")
    return {"decision": deepcopy(decision) | deepcopy(realization),
            "discussion": deepcopy(discussion) | deepcopy(updates)}


def legacy_to_candidate(value, schema):
    """Map a legacy value to candidate form; schema must be the baseline schema."""
    schema = _plain(schema)
    _validate_schema(schema)
    _legacy_pairs(schema)
    if not Draft202012Validator(schema).is_valid(value):
        raise ValueError("LEGACY_SCHEMA_INVALID")
    old = deepcopy(value)
    decision, discussion = old["decision"], old["discussion"]
    kind = decision["kind"]
    text = TEXT_FIELD.get(kind)
    realization = {} if text is None else {text: decision.pop(text)}
    updates = {key: discussion.pop(key) for key in UPDATE_FIELDS}
    candidate = {"intent": {"discussion": discussion, "decision": decision},
                 "realization": realization, "updates": updates}
    candidate_schema = _candidate_schema(schema)
    if not Draft202012Validator(candidate_schema).is_valid(candidate):
        raise ValueError("ADAPTER_ROUNDTRIP_INVALID")
    return candidate


def assess_candidate(case, baseline_projection, body, raw):
    """Strictly validate, adapt, and pass a candidate through the legacy screen."""
    from scripts.phase6_model_comparison import screen
    row = {"candidate_schema_pass": False, "legacy_contract_pass": None,
           "adapter_status": "NOT_APPLIED", "adapted_output_sha256": None,
           "root_order_pass": False, "structural_pass": False, "hard_pass": False,
           "semantic_pass": None, "style_pass": None,
           "failure_reasons": ["CANDIDATE_SCHEMA_INVALID"], "screen_flags": [],
           "decision_kind": "UNKNOWN", "speech_act": "UNKNOWN",
           "manual_text_review_required": True}
    try:
        value = strict_json(raw)
        schema = body["response_format"]["json_schema"]["schema"]
        if not Draft202012Validator(schema).is_valid(value):
            return row
        adapted = candidate_to_legacy(value, schema)
    except (KeyError, TypeError, ValueError, SchemaError, RecursionError):
        return row
    adapted_bytes = wire_bytes(adapted)
    result = screen(case, baseline_projection, adapted_bytes.decode("utf-8"))
    result.update(candidate_schema_pass=True,
                  legacy_contract_pass=result["structural_pass"],
                  adapter_status="FIELD_MOVE_COMPLETE",
                  adapted_output_sha256=hashlib.sha256(adapted_bytes).hexdigest(),
                  root_order_pass=list(value) == list(ROOT_ORDER))
    return result
