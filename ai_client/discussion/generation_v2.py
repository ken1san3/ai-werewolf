"""Catalog-bound JSON schemas and strict offline validation for generation v2.

The module deliberately has no dependency on the design fixture or provider code.  A
caller must construct the frozen catalog from an authorized capture; wiring that
capture boundary is a later task.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from types import MappingProxyType
from typing import Any, Literal, Mapping

from jsonschema import Draft202012Validator


StageV2 = Literal["chat_plan", "message", "pre_vote", "co_opportunity", "ability"]
_SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
_SCORES = [0, 25, 50, 75, 100]
_TOPICS = ["ALIGNMENT", "ROLE_CLAIM", "VOTE", "EVENT", "RELATION", "STRATEGY"]
_OPINION_TOPICS = ["ALIGNMENT", "ROLE_CLAIM", "RELATION"]
_STANCES = ["SUPPORT", "OPPOSE", "UNCERTAIN"]
_ID_RE = re.compile(r"^[a-z][0-9]{3}$")


class GenerationV2Error(ValueError):
    """A catalog, JSON value, or semantic constraint is invalid."""


@dataclass(frozen=True)
class ParsedGenerationV2Candidate:
    """A schema-valid immutable candidate, before authority and text guards."""

    stage: StageV2
    value: Mapping[str, object]


@dataclass(frozen=True)
class OpinionBasisV2:
    basis_id: str
    subject_player_id: str
    dimension: Literal["SUSPICION", "CREDIBILITY"]
    prior: int
    allowed_current: tuple[int, ...]
    prior_fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class VoteOptionV2:
    option_id: str
    valid_target_ids: tuple[str, ...]
    allows_abstain: bool


@dataclass(frozen=True)
class CoOptionV2:
    option_id: str
    claimed_role_option_ids: tuple[str, ...]


@dataclass(frozen=True)
class AbilityOptionV2:
    option_id: str
    target_count: int
    valid_target_ids: tuple[str, ...]
    allows_none: bool


@dataclass(frozen=True)
class GenerationCatalogV2:
    reply_ids: tuple[str, ...]
    player_ids: tuple[str, ...]
    peer_player_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]
    disclose_ids: tuple[str, ...]
    utterance_claim_ids: tuple[str, ...]
    observed_claim_ids: tuple[str, ...]
    opinion_bases: tuple[OpinionBasisV2, ...]
    vote_options: tuple[VoteOptionV2, ...]
    co_options: tuple[CoOptionV2, ...]
    ability_options: tuple[AbilityOptionV2, ...]

    def __post_init__(self) -> None:
        groups = (
            ("reply_ids", self.reply_ids, "r", 6),
            ("player_ids", self.player_ids, "p", 32),
            ("peer_player_ids", self.peer_player_ids, "p", 31),
            ("fact_ids", self.fact_ids, "f", 48),
            ("disclose_ids", self.disclose_ids, "d", 16),
            ("utterance_claim_ids", self.utterance_claim_ids, "c", 32),
            ("observed_claim_ids", self.observed_claim_ids, "k", 32),
        )
        for name, values, prefix, maximum in groups:
            _check_ids(name, values, prefix, maximum)
        if not set(self.peer_player_ids).issubset(self.player_ids):
            raise GenerationV2Error("peer_player_ids must be a subset of player_ids")
        _check_objects("opinion_bases", self.opinion_bases, OpinionBasisV2, "basis_id", "u", 64)
        _check_objects("vote_options", self.vote_options, VoteOptionV2, "option_id", "o", 32)
        _check_objects("co_options", self.co_options, CoOptionV2, "option_id", "o", 32)
        _check_objects("ability_options", self.ability_options, AbilityOptionV2, "option_id", "o", 32)
        for basis in self.opinion_bases:
            if basis.subject_player_id not in self.player_ids:
                raise GenerationV2Error("opinion basis subject is outside player catalog")
            if (basis.dimension not in {"SUSPICION", "CREDIBILITY"}
                    or type(basis.prior) is not int or not 0 <= basis.prior <= 100
                    or type(basis.allowed_current) is not tuple or not basis.allowed_current):
                raise GenerationV2Error("opinion basis requires a score prior and current choices")
            expected_current = tuple(score for score in _SCORES if score != basis.prior)
            if basis.allowed_current != expected_current:
                raise GenerationV2Error(
                    "opinion allowed_current must be the ordered complement of prior"
                )
            _check_subset("opinion prior facts", basis.prior_fact_ids, self.fact_ids)
        for option in self.vote_options:
            if type(option.allows_abstain) is not bool:
                raise GenerationV2Error("allows_abstain must be an exact bool")
            _check_subset("vote targets", option.valid_target_ids, self.player_ids, nonempty=True)
        role_ids: list[str] = []
        for option in self.co_options:
            _check_ids("claimed role options", option.claimed_role_option_ids, "q", 32, nonempty=True)
            role_ids.extend(option.claimed_role_option_ids)
        if len(role_ids) > 32 or len(role_ids) != len(set(role_ids)):
            raise GenerationV2Error("claimed role option IDs must be globally unique and bounded")
        for option in self.ability_options:
            if type(option.allows_none) is not bool:
                raise GenerationV2Error("allows_none must be an exact bool")
            _check_subset("ability targets", option.valid_target_ids, self.player_ids)
            if type(option.target_count) is not int or not 0 <= option.target_count <= len(option.valid_target_ids):
                raise GenerationV2Error("ability target_count exceeds its valid target catalog")


def _check_ids(name: str, values: tuple[str, ...], prefix: str, maximum: int, *, nonempty: bool = False) -> None:
    if type(values) is not tuple or len(values) > maximum or (nonempty and not values):
        raise GenerationV2Error(f"{name} must be a bounded tuple")
    if len(values) != len(set(values)):
        raise GenerationV2Error(f"{name} contains duplicate IDs")
    if any(type(v) is not str or _ID_RE.fullmatch(v) is None or not v.startswith(prefix) for v in values):
        raise GenerationV2Error(f"{name} contains an invalid stage ID")


def _check_objects(name: str, values: tuple[Any, ...], cls: type, attr: str, prefix: str, maximum: int) -> None:
    if type(values) is not tuple or len(values) > maximum or any(not isinstance(v, cls) for v in values):
        raise GenerationV2Error(f"{name} must be a bounded typed tuple")
    _check_ids(name, tuple(getattr(v, attr) for v in values), prefix, maximum)


def _check_subset(name: str, values: tuple[str, ...], allowed: tuple[str, ...], *, nonempty: bool = False) -> None:
    if type(values) is not tuple or (nonempty and not values) or len(values) != len(set(values)):
        raise GenerationV2Error(f"{name} must be a unique tuple")
    if not set(values).issubset(allowed):
        raise GenerationV2Error(f"{name} contains an ID outside its catalog")


def _array_enum(values: tuple[str, ...], maximum: int, *, minimum: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "array"}
    if values:
        result["items"] = {"enum": list(values)}
    else:
        maximum = 0
    if minimum is not None:
        result["minItems"] = minimum
    result["maxItems"] = maximum
    result["uniqueItems"] = True
    return result


def _empty_array() -> dict[str, Any]:
    return {"type": "array", "maxItems": 0}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def build_generation_v2_schema(stage: StageV2, catalog: GenerationCatalogV2) -> dict[str, Any]:
    """Build a Draft 2020-12 schema while preserving the approved insertion order."""
    builders = {
        "chat_plan": _chat_plan_schema,
        "message": _message_schema,
        "pre_vote": _pre_vote_schema,
        "co_opportunity": _co_schema,
        "ability": _ability_schema,
    }
    if type(stage) is not str or stage not in builders:
        raise GenerationV2Error(f"unknown generation v2 stage: {stage!r}")
    if not isinstance(catalog, GenerationCatalogV2):
        raise GenerationV2Error("catalog must be GenerationCatalogV2")
    schema = builders[stage](catalog)
    Draft202012Validator.check_schema(schema)
    return schema


def _chat_plan_schema(c: GenerationCatalogV2) -> dict[str, Any]:
    defs = {
        "topic": {"enum": _TOPICS.copy()},
        "stance": {"enum": _STANCES.copy()},
        "subject_or_null": {"enum": [*c.player_ids, None]},
        "claim_or_null": {"enum": [*c.utterance_claim_ids, None]},
        "fact_ids": _array_enum(c.fact_ids, 2),
        "disclose_ids": _array_enum(c.disclose_ids, 1),
    }
    branches: list[dict[str, Any]] = []
    if c.reply_ids:
        branches.extend([
            _plan_branch({"enum": list(c.reply_ids)}, {"enum": ["ANSWER", "REBUTTAL"]}, {"$ref": "#/$defs/subject_or_null"}, {"$ref": "#/$defs/topic"}, {"$ref": "#/$defs/stance"}, None, None, {"$ref": "#/$defs/claim_or_null"}, {"$ref": "#/$defs/fact_ids"}, {"$ref": "#/$defs/disclose_ids"}),
            _plan_branch({"enum": list(c.reply_ids)}, {"const": "QUESTION"}, {"enum": list(c.player_ids)}, {"$ref": "#/$defs/topic"}, None, None, None, None, {"$ref": "#/$defs/fact_ids"}, _empty_array()),
            _plan_branch({"enum": list(c.reply_ids)}, {"const": "CLAIM"}, {"enum": list(c.player_ids)}, {"$ref": "#/$defs/topic"}, {"$ref": "#/$defs/stance"}, None, None, {"$ref": "#/$defs/claim_or_null"}, {"$ref": "#/$defs/fact_ids"}, {"$ref": "#/$defs/disclose_ids"}),
        ])
        for basis in c.opinion_bases:
            branches.append(_opinion_branch({"enum": list(c.reply_ids)}, basis, {"$ref": "#/$defs/fact_ids"}))
    branches.extend([
        _plan_branch(None, {"const": "QUESTION"}, {"enum": list(c.player_ids)}, {"$ref": "#/$defs/topic"}, None, None, None, None, {"$ref": "#/$defs/fact_ids"}, _empty_array()),
        _plan_branch(None, {"const": "CLAIM"}, {"enum": list(c.player_ids)}, {"$ref": "#/$defs/topic"}, {"$ref": "#/$defs/stance"}, None, None, {"$ref": "#/$defs/claim_or_null"}, {"$ref": "#/$defs/fact_ids"}, {"$ref": "#/$defs/disclose_ids"}),
    ])
    for basis in c.opinion_bases:
        branches.append(_opinion_branch(None, basis, _array_enum(c.fact_ids, 2, minimum=1)))
    branches.append(_plan_branch(None, {"const": "NONE"}, None, None, None, None, None, None, _empty_array(), _empty_array()))
    return {"$schema": _SCHEMA_URI, "$defs": defs, "oneOf": branches}


def _const_null(value: dict[str, Any] | None) -> dict[str, Any]:
    return {"const": None} if value is None else value


def _plan_branch(reply: dict[str, Any] | None, act: dict[str, Any], subject: dict[str, Any] | None,
                 topic: dict[str, Any] | None, stance: dict[str, Any] | None, basis: dict[str, Any] | None,
                 current: dict[str, Any] | None, claim: dict[str, Any] | None, facts: dict[str, Any], disclosures: dict[str, Any]) -> dict[str, Any]:
    return _object({"reply_to": _const_null(reply), "act": act, "subject_player_id": _const_null(subject),
                    "topic": _const_null(topic), "stance": _const_null(stance), "opinion_basis_id": _const_null(basis),
                    "opinion_current": _const_null(current), "claim_id": _const_null(claim),
                    "fact_ids": facts, "disclose_ids": disclosures})


def _opinion_branch(reply: dict[str, Any] | None, basis: OpinionBasisV2, facts: dict[str, Any]) -> dict[str, Any]:
    return _plan_branch(reply, {"const": "OPINION_CHANGE"}, {"const": basis.subject_player_id},
                        {"enum": _OPINION_TOPICS.copy()}, None, {"const": basis.basis_id},
                        {"enum": list(basis.allowed_current)}, None, facts, _empty_array())


def _message_schema(c: GenerationCatalogV2) -> dict[str, Any]:
    return {"$schema": _SCHEMA_URI, **_object({"message": {"type": "string", "minLength": 1, "maxLength": 200}})}


def _common_defs(c: GenerationCatalogV2) -> dict[str, Any]:
    return {"fact_ids": _array_enum(c.fact_ids, 2)}


def _pre_vote_schema(c: GenerationCatalogV2) -> dict[str, Any]:
    defs = {
        "score": {"enum": _SCORES.copy()},
        **_common_defs(c),
        "assessment": _object({"player_id": _enum_or_impossible(c.peer_player_ids), "suspicion": {"$ref": "#/$defs/score"}, "credibility": {"$ref": "#/$defs/score"}, "confidence": {"$ref": "#/$defs/score"}, "fact_ids": {"$ref": "#/$defs/fact_ids"}}),
        "claim_assessment": _object({"claim_id": _enum_or_impossible(c.observed_claim_ids), "verdict": {"enum": ["UNVERIFIED", "SUPPORTED", "CONTRADICTED"]}, "confidence": {"$ref": "#/$defs/score"}, "fact_ids": {"$ref": "#/$defs/fact_ids"}}),
    }
    branches = []
    for option in c.vote_options:
        branches.append(_vote_branch(option, "VOTE"))
        if option.allows_abstain:
            branches.append(_vote_branch(option, "ABSTAIN"))
    return {"$schema": _SCHEMA_URI, "$defs": defs, "oneOf": branches}


def _enum_or_impossible(values: tuple[str, ...]) -> dict[str, Any]:
    return {"enum": list(values)} if values else {"not": {}}


def _vote_branch(option: VoteOptionV2, decision: str) -> dict[str, Any]:
    ranked = (_array_enum(option.valid_target_ids, len(option.valid_target_ids), minimum=1)
              if decision == "VOTE" else _empty_array())
    return _object({"vote_option_id": {"const": option.option_id}, "decision": {"const": decision},
                    "ranked_player_ids": ranked, "assessment_updates": {"type": "array", "items": {"$ref": "#/$defs/assessment"}, "maxItems": 2},
                    "claim_assessments": {"type": "array", "items": {"$ref": "#/$defs/claim_assessment"}, "maxItems": 2},
                    "fact_ids": {"$ref": "#/$defs/fact_ids"}})


def _co_schema(c: GenerationCatalogV2) -> dict[str, Any]:
    defs = _common_defs(c)
    branches = [
        _object({"decision": {"const": "SILENCE"}, "co_option_id": {"const": None}, "claimed_role_option_id": {"const": None}, "comment": {"const": None}, "fact_ids": {"$ref": "#/$defs/fact_ids"}}),
        _object({"decision": {"const": "DEFER"}, "co_option_id": {"const": None}, "claimed_role_option_id": {"const": None}, "comment": {"const": None}, "fact_ids": {"$ref": "#/$defs/fact_ids"}}),
    ]
    for option in c.co_options:
        # A branch per role keeps the chosen option pair fixed by const.
        for role_id in option.claimed_role_option_ids:
            branches.append(_object({"decision": {"const": "DECLARE"}, "co_option_id": {"const": option.option_id}, "claimed_role_option_id": {"const": role_id}, "comment": {"type": "string", "minLength": 1, "maxLength": 200}, "fact_ids": {"$ref": "#/$defs/fact_ids"}}))
    return {"$schema": _SCHEMA_URI, "$defs": defs, "oneOf": branches}


def _ability_schema(c: GenerationCatalogV2) -> dict[str, Any]:
    defs = _common_defs(c)
    branches = []
    for option in c.ability_options:
        targets: dict[str, Any] = {"type": "array"}
        if option.valid_target_ids:
            targets["items"] = {"enum": list(option.valid_target_ids)}
        targets.update({"minItems": option.target_count, "maxItems": option.target_count, "uniqueItems": True})
        branches.append(_object({"decision": {"const": "USE"}, "ability_option_id": {"const": option.option_id}, "target_player_ids": targets, "fact_ids": {"$ref": "#/$defs/fact_ids"}}))
        if option.allows_none:
            none_targets = {"type": "array", "minItems": 0, "maxItems": 0, "uniqueItems": True}
            branches.append(_object({"decision": {"const": "NONE"}, "ability_option_id": {"const": option.option_id}, "target_player_ids": none_targets, "fact_ids": {"$ref": "#/$defs/fact_ids"}}))
    return {"$schema": _SCHEMA_URI, "$defs": defs, "oneOf": branches}


def parse_and_validate_generation_v2_candidate_structure(
    stage: StageV2,
    raw: str | bytes,
    catalog: GenerationCatalogV2,
) -> ParsedGenerationV2Candidate:
    """Parse one strict JSON value into a schema-valid structural candidate.

    This result is not guard-accepted output.  TEXT_BOUND, authority, selected
    disclosure/claim expression, act/message agreement, and authority-backed cause
    validation are intentionally outside this offline unit.
    In particular, proving that an OPINION_CHANGE has a cause absent from prior
    evidence also requires the selected reply's evidence identity.  The capture
    projection that supplies that identity is not connected yet, so this validator
    limits itself to catalog candidate IDs instead of accepting a partial proof.
    """
    def reject_constant(value: str) -> None:
        raise GenerationV2Error(f"non-finite JSON number is forbidden: {value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise GenerationV2Error(f"duplicate JSON member: {key}")
            result[key] = value
        return result

    if type(stage) is not str:
        raise GenerationV2Error("stage must be a generation v2 stage string")
    if type(raw) not in {str, bytes}:
        raise GenerationV2Error("raw candidate must be str or bytes")
    if not isinstance(catalog, GenerationCatalogV2):
        raise GenerationV2Error("catalog must be GenerationCatalogV2")
    try:
        value = json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_object)
    except GenerationV2Error:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise GenerationV2Error("generation output is not one strict JSON value") from error
    if type(value) is not dict:
        raise GenerationV2Error("generation output must be a JSON object")
    _reject_nonfinite(value)
    errors = sorted(Draft202012Validator(build_generation_v2_schema(stage, catalog)).iter_errors(value), key=lambda e: list(e.path))
    if errors:
        raise GenerationV2Error(errors[0].message)
    if stage == "message":
        _check_text_bytes("message", value["message"])
    elif stage == "co_opportunity" and value["decision"] == "DECLARE":
        _check_text_bytes("comment", value["comment"])
    elif stage == "pre_vote":
        _reject_duplicate_field(value["assessment_updates"], "player_id")
        _reject_duplicate_field(value["claim_assessments"], "claim_id")
    frozen = _freeze_candidate(value)
    assert isinstance(frozen, Mapping)
    return ParsedGenerationV2Candidate(stage=stage, value=frozen)


def _reject_nonfinite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise GenerationV2Error("non-finite JSON number is forbidden")
    if isinstance(value, dict):
        for child in value.values():
            _reject_nonfinite(child)
    elif isinstance(value, list):
        for child in value:
            _reject_nonfinite(child)


def _freeze_candidate(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_candidate(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_candidate(child) for child in value)
    return value


def _check_text_bytes(name: str, value: str) -> None:
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise GenerationV2Error(f"{name} contains an invalid Unicode surrogate") from error
    if size > 600:
        raise GenerationV2Error(f"{name} exceeds 600 UTF-8 bytes")


def _reject_duplicate_field(values: list[dict[str, Any]], field: str) -> None:
    identities = [value[field] for value in values]
    if len(identities) != len(set(identities)):
        raise GenerationV2Error(f"duplicate {field} is forbidden")
