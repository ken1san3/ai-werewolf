"""Strict request-local conversion from model JSON to BrainDecision."""

from __future__ import annotations

import json
from typing import Mapping

from jsonschema import Draft202012Validator

from ai_client.brain import (
    AbilityDecision,
    BrainDecision,
    ChatDecision,
    CoDeclareDecision,
    CoReportDecision,
    NoDecision,
    VoteDecision,
)

from .types import DecisionValidationCode, PromptProjection


class DecisionValidationError(RuntimeError):
    def __init__(self, code: DecisionValidationCode) -> None:
        if not isinstance(code, DecisionValidationCode):
            raise TypeError("code must be DecisionValidationCode")
        self.code = code
        RuntimeError.__init__(self, code.value)


class _DuplicateKey(ValueError):
    pass


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, child in pairs:
        if key in value:
            raise _DuplicateKey
        value[key] = child
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(child) for child in value]
    return value


def _options(projection: PromptProjection) -> tuple[Mapping[str, object], ...]:
    context = projection.canonical_input.get("action_context")
    if not isinstance(context, Mapping):
        return ()
    options = context.get("options")
    if not isinstance(options, tuple):
        return ()
    return tuple(option for option in options if isinstance(option, Mapping))


def _find_option(
    projection: PromptProjection, kind: str, option_id: object
) -> Mapping[str, object]:
    for option in _options(projection):
        action_kind = option.get("action_kind", option.get("kind"))
        if action_kind == kind and option.get("option_id") == option_id:
            if kind == "co_report" and option.get("eligible") is False:
                break
            return option
    raise DecisionValidationError(DecisionValidationCode.OPTION_NOT_OFFERED)


def _exact_keys(value: Mapping[str, object], names: set[str]) -> None:
    if set(value) != names:
        raise DecisionValidationError(DecisionValidationCode.SCHEMA)


def parse_llm_decision(
    text: str, *, projection: PromptProjection
) -> BrainDecision:
    if not isinstance(text, str) or not text:
        raise DecisionValidationError(DecisionValidationCode.JSON_SYNTAX)
    if not isinstance(projection, PromptProjection):
        raise TypeError("projection must be PromptProjection")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except _DuplicateKey:
        raise DecisionValidationError(
            DecisionValidationCode.JSON_DUPLICATE_KEY
        ) from None
    except (ValueError, TypeError, json.JSONDecodeError):
        raise DecisionValidationError(DecisionValidationCode.JSON_SYNTAX) from None
    if not isinstance(value, dict):
        raise DecisionValidationError(DecisionValidationCode.SCHEMA)

    kind = value.get("kind")
    if kind == "none":
        _exact_keys(value, {"kind"})
        decision: BrainDecision = NoDecision()
    elif kind == "chat":
        _exact_keys(value, {"kind", "option_id", "message"})
        option = _find_option(projection, kind, value.get("option_id"))
        message = value.get("message")
        if not isinstance(message, str):
            raise DecisionValidationError(DecisionValidationCode.SCHEMA)
        branch_limit = _text_limit(projection, kind, option["option_id"], "message")
        _validate_generated_text(message, projection=projection, char_limit=branch_limit)
        decision = ChatDecision(option_id=option["option_id"], message=message)
    elif kind == "vote":
        _exact_keys(value, {"kind", "option_id", "target_player_id"})
        option = _find_option(projection, kind, value.get("option_id"))
        target = value.get("target_player_id")
        valid_targets = tuple(option.get("valid_targets", ()))
        if target is None:
            if option.get("allows_abstain") is not True:
                raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
        elif not isinstance(target, str) or target not in valid_targets:
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
        decision = VoteDecision(option_id=option["option_id"], target_player_id=target)
    elif kind == "ability":
        _exact_keys(value, {"kind", "option_id", "target_player_ids"})
        option = _find_option(projection, kind, value.get("option_id"))
        targets = value.get("target_player_ids")
        if not isinstance(targets, list):
            raise DecisionValidationError(DecisionValidationCode.SCHEMA)
        valid_targets = tuple(option.get("valid_targets", ()))
        target_count = option.get("target_count")
        if (
            type(target_count) is not int
            or len(targets) != target_count
            or any(not isinstance(target, str) or target not in valid_targets for target in targets)
            or len(set(targets)) != len(targets)
        ):
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
        decision = AbilityDecision(
            option_id=option["option_id"], target_player_ids=tuple(targets)
        )
    elif kind == "co_declare":
        _exact_keys(value, {"kind", "option_id", "claimed_role_id", "comment"})
        option = _find_option(projection, kind, value.get("option_id"))
        role = value.get("claimed_role_id")
        if not isinstance(role, str) or role not in tuple(option.get("claimed_role_ids", ())):
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
        comment = value.get("comment")
        if not isinstance(comment, str):
            raise DecisionValidationError(DecisionValidationCode.SCHEMA)
        branch_limit = _text_limit(projection, kind, option["option_id"], "comment")
        _validate_generated_text(comment, projection=projection, char_limit=branch_limit)
        decision = CoDeclareDecision(
            option_id=option["option_id"],
            claimed_role_id=role,
            comment=comment,
        )
    elif kind == "co_report":
        _exact_keys(
            value,
            {"kind", "option_id", "report_kind", "target_player_id", "claimed_result"},
        )
        option = _find_option(projection, kind, value.get("option_id"))
        report_kind = value.get("report_kind")
        target = value.get("target_player_id")
        result = value.get("claimed_result")
        if (
            report_kind not in tuple(option.get("report_kinds", ()))
            or target not in tuple(option.get("valid_targets", ()))
            or result not in tuple(option.get("claimed_results", ()))
        ):
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
        decision = CoReportDecision(
            option_id=option["option_id"],
            kind=report_kind,
            target_player_id=target,
            claimed_result=result,
        )
    else:
        raise DecisionValidationError(DecisionValidationCode.SCHEMA)

    if not Draft202012Validator(_plain_json(projection.decision_schema)).is_valid(value):
        raise DecisionValidationError(DecisionValidationCode.SCHEMA)
    return decision


def _validate_generated_text(
    value: str, *, projection: PromptProjection, char_limit: int
) -> None:
    if not 1 <= len(value) <= char_limit:
        raise DecisionValidationError(DecisionValidationCode.TEXT_BOUND)
    short_chat = projection.short_chat
    if short_chat is None:
        return
    try:
        encoded_length = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise DecisionValidationError(DecisionValidationCode.TEXT_BOUND) from None
    if encoded_length > short_chat.max_text_utf8_bytes:
        raise DecisionValidationError(DecisionValidationCode.TEXT_BOUND)


def _text_limit(
    projection: PromptProjection, kind: str, option_id: object, property_name: str
) -> int:
    schema = _plain_json(projection.decision_schema)
    if isinstance(schema, dict):
        for branch in schema.get("oneOf", []):
            properties = branch.get("properties", {})
            if (
                properties.get("kind", {}).get("const") == kind
                and properties.get("option_id", {}).get("const") == option_id
            ):
                value = properties.get(property_name, {}).get("maxLength")
                if type(value) is int and value >= 1:
                    return value
    raise DecisionValidationError(DecisionValidationCode.SCHEMA)
