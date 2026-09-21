"""Pure grounding-closed schema adapter for the Phase 6 GC2 probe."""
from __future__ import annotations

from copy import deepcopy
from enum import Enum
import json
from typing import Mapping

from jsonschema import Draft202012Validator

from ai_client.discussion.model import EvidenceRecordKind, EvidenceVisibility
from ai_client.llm.decision import parse_llm_output
from ai_client.llm.types import PromptProjection
from scripts import phase6_intent_choice_probe as ic2


class NoLegalGrounding(ValueError):
    def __init__(self):
        ValueError.__init__(self, "NO_LEGAL_GROUNDING")


_REF_KEYS = {"record_kind", "order", "visibility"}
_KINDS_REQUIRING_REF = {"ANSWER", "REBUTTAL", "OPINION_CHANGE"}


def _invalid():
    raise ValueError("GROUNDING_INPUT_INVALID")


def _plain(value):
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            _invalid()
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(child) for child in value]
    if value is None or type(value) in {bool, int, str}:
        return value
    _invalid()


def _ref(value):
    if all(hasattr(value, name) for name in _REF_KEYS):
        data = {name: _plain(getattr(value, name)) for name in _REF_KEYS}
    else:
        data = _plain(value)
    if not isinstance(data, dict) or set(data) != _REF_KEYS:
        _invalid()
    if data["record_kind"] not in {item.value for item in EvidenceRecordKind}:
        _invalid()
    if data["visibility"] not in {item.value for item in EvidenceVisibility}:
        _invalid()
    if type(data["order"]) is not int or data["order"] < 0:
        _invalid()
    return data


def _identity(ref):
    return ref["record_kind"], ref["order"]


def _refs(values):
    result = {}
    for value in values:
        ref = _ref(value)
        identity = _identity(ref)
        if identity in result:
            _invalid()
        result[identity] = ref
    return result


def grounding_sets(projection):
    if not isinstance(projection, PromptProjection) or projection.discussion_capture is None:
        _invalid()
    try:
        memory = projection.canonical_input["memory"]
        records = memory["records"]
        capture_data = projection.canonical_input["capture"]
        current_players = capture_data["current_player_ids"]
    except (KeyError, TypeError):
        _invalid()
    if (not isinstance(memory, Mapping) or not isinstance(records, (tuple, list))
            or not isinstance(capture_data, Mapping) or not isinstance(current_players, (tuple, list))
            or any(type(player) is not str for player in current_players)
            or len(current_players) != len(set(current_players))):
        _invalid()
    try:
        projected = _refs(record["source"] for record in records
                          if isinstance(record, Mapping))
    except (KeyError, TypeError):
        _invalid()
    if len(projected) != len(records):
        _invalid()
    evidence = projection.discussion_capture.evidence
    if not isinstance(evidence, tuple):
        _invalid()
    try:
        captured = _refs(item.source for item in evidence)
    except (AttributeError, TypeError):
        _invalid()
    events = {}
    for item in evidence:
        identity = _identity(_ref(item.source))
        events[identity] = item
    common = set(projected) & set(captured)
    for identity in common:
        if projected[identity] != captured[identity]:
            _invalid()
    allowed_refs = tuple(sorted(
        (deepcopy(projected[identity]) for identity in common),
        key=lambda value: (value["record_kind"], value["order"], value["visibility"]),
    ))
    players = set(current_players)
    self_id = projection.discussion_capture.player_id
    claims = []
    claim_by_ref = {}
    for ref in allowed_refs:
        event = events[_identity(ref)]
        actors = event.actor_player_ids
        if not isinstance(actors, tuple) or any(type(actor) is not str for actor in actors):
            _invalid()
        if len(actors) != 1 or actors[0] == self_id or actors[0] not in players:
            continue
        identity = _identity(ref)
        actor = actors[0]
        if identity in claim_by_ref and claim_by_ref[identity] != actor:
            _invalid()
        claim_by_ref[identity] = actor
        claims.append((deepcopy(ref), actor))
    if len({(_identity(ref), actor) for ref, actor in claims}) != len(claims):
        _invalid()
    return allowed_refs, tuple(claims)


def _ref_branch(ref):
    return {"type": "object", "properties": {
        "record_kind": {"const": ref["record_kind"]},
        "order": {"const": ref["order"]},
        "visibility": {"const": ref["visibility"]},
    }, "required": ["record_kind", "order", "visibility"],
        "additionalProperties": False}


def _require_choice(choice, projection):
    try:
        raw = json.dumps(_plain(choice), ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False)
    except (TypeError, ValueError):
        _invalid()
    return ic2.validate_choice(raw, projection)


def candidate_schema(projection, choice):
    choice_value = _require_choice(choice, projection)
    schema = ic2.locked_schema(projection.decision_schema, choice_value)
    allowed_refs, allowed_claims = grounding_sets(projection)
    kind = choice_value["speech_act_kind"]
    trigger = projection.discussion_capture.trigger
    if kind in _KINDS_REQUIRING_REF and not allowed_refs:
        raise NoLegalGrounding()
    if trigger.kind == "PEER_CHAT":
        trigger_ref = _ref(trigger.source)
        if trigger_ref not in allowed_refs:
            raise NoLegalGrounding()

    definitions = schema["$defs"]
    if allowed_refs:
        definitions["evidence_ref"] = {"oneOf": [_ref_branch(ref) for ref in allowed_refs]}
    else:
        definitions["evidence_array"]["maxItems"] = 0
        definitions["claim_updates"]["maxItems"] = 0
        if kind == "QUESTION":
            definitions["speech_act"]["properties"]["source"] = {"const": None}

    if allowed_claims:
        baseline_claim = definitions["claim"]
        branches = []
        for ref, actor in allowed_claims:
            branch = deepcopy(baseline_claim)
            branch["properties"]["claim"] = {"const": deepcopy(ref)}
            branch["properties"]["speaker_player_id"] = {"const": actor}
            branches.append(branch)
        definitions["claim"] = {"oneOf": branches}
    else:
        definitions["claim_updates"]["maxItems"] = 0
    Draft202012Validator.check_schema(schema)
    return schema


def output_body(baseline_body, choice, projection):
    body = ic2.output_body(baseline_body, choice, projection)
    body["response_format"]["json_schema"]["schema"] = candidate_schema(
        projection, choice
    )
    return body


def validate_final(raw, choice, projection):
    choice_value = _require_choice(choice, projection)
    value = ic2.strict_json(raw)
    schema = candidate_schema(projection, choice_value)
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
