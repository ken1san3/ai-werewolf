"""Deterministic bounded Phase 6 prompt projection.

The module contains no backend or model call.  It receives one immutable
``BrainInput``-shaped value and emits the exact provider-visible contract while
debiting both canonical UTF-8 bytes and the provider-independent token proxy.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
import sys
from typing import Any, Mapping

from ai_client.network import (
    AbilityAction,
    ChatAction,
    CoDeclareAction,
    CoReportAction,
    VoteAction,
)

from .context import canonical_json_bytes, canonical_sha256
from .model import (
    DiscussionCapture,
    EvidenceRecordKind,
    EvidenceVisibility,
    ImportantEvent,
    evidence_sort_key,
)


@dataclass(frozen=True)
class DiscussionPromptConfig:
    max_important_old_records: int = 12
    max_newest_records: int = 12
    max_combined_records: int = 24
    max_important_text_utf8_bytes: int = 768
    max_important_text_scalars: int = 160
    max_recent_text_utf8_bytes: int = 2048
    max_recent_text_scalars: int = 512
    max_memory_section_bytes: int = 16 * 1024
    max_context_bytes: int = 8 * 1024
    max_state_section_bytes: int = 8 * 1024
    max_proposal_bytes: int = 16 * 1024
    max_token_proxy_units: int = 8192
    max_prompt_bytes: int = 32768

    def __post_init__(self) -> None:
        ceilings = {
            "max_important_old_records": 12,
            "max_newest_records": 12,
            "max_combined_records": 24,
            "max_important_text_utf8_bytes": 768,
            "max_important_text_scalars": 160,
            "max_recent_text_utf8_bytes": 2048,
            "max_recent_text_scalars": 512,
            "max_memory_section_bytes": 16 * 1024,
            "max_context_bytes": 8 * 1024,
            "max_state_section_bytes": 8 * 1024,
            "max_proposal_bytes": 16 * 1024,
            "max_token_proxy_units": 8192,
            "max_prompt_bytes": 32768,
        }
        zero_allowed = {
            "max_important_old_records",
            "max_newest_records",
            "max_combined_records",
        }
        for name, ceiling in ceilings.items():
            value = getattr(self, name)
            minimum = 0 if name in zero_allowed else 1
            if type(value) is not int or not minimum <= value <= ceiling:
                raise ValueError(f"{name} must be an int in [{minimum}, {ceiling}]")


def token_proxy_units(value: str) -> int:
    """Return the literal Phase 6 provider-independent proxy formula."""

    if not isinstance(value, str):
        raise TypeError("value must be str")
    units = 0
    run_bytes = 0
    for scalar in value:
        if scalar.isascii() and (scalar.isalnum() or scalar == "_"):
            run_bytes += 1
            continue
        if run_bytes:
            units += math.ceil(run_bytes / 4)
            run_bytes = 0
        units += 1 if scalar.isascii() else len(scalar.encode("utf-8"))
    if run_bytes:
        units += math.ceil(run_bytes / 4)
    return units


def _plain(value: object) -> object:
    """Use the already-reviewed canonicalizer as the sole dataclass projection."""

    return json.loads(canonical_json_bytes(value).decode("utf-8"))


def _closed(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {
        "additionalProperties": False,
        "properties": properties,
        "required": required,
        "type": "object",
    }


def _option(value: object, text_limit: int) -> dict[str, object]:
    handle = value.handle
    common: dict[str, object] = {
        "action_generation": handle.action_generation,
        "connection_generation": handle.connection_generation,
        "day": handle.day,
        "option_id": value.option_id,
        "phase": handle.phase,
        "type": handle.type,
    }
    if isinstance(handle, ChatAction):
        return common | {"action_kind": "chat", "channel": handle.channel}
    if isinstance(handle, VoteAction):
        return common | {
            "action_kind": "vote",
            "allows_abstain": handle.allows_abstain,
            "target_count": handle.target_count,
            "valid_targets": list(handle.valid_targets),
        }
    if isinstance(handle, AbilityAction):
        description = None
        if handle.description is not None:
            description = {
                "original_chars": len(handle.description),
                "text": handle.description[:text_limit],
                "truncated": len(handle.description) > text_limit,
            }
        return common | {
            "ability_id": handle.ability_id,
            "action_kind": "ability",
            "description": description,
            "target_count": handle.target_count,
            "uses_remaining": handle.uses_remaining,
            "valid_targets": list(handle.valid_targets),
        }
    if isinstance(handle, CoDeclareAction):
        return common | {
            "action_kind": "co_declare",
            "claimed_role_ids": list(handle.claimed_role_ids),
        }
    if isinstance(handle, CoReportAction):
        return common | {"action_kind": "co_report", "eligible": False}
    raise TypeError("unsupported action handle")


def _evidence_schema() -> dict[str, object]:
    return _closed(
        {
            "record_kind": {"enum": [value.value for value in EvidenceRecordKind]},
            "order": {"minimum": 0, "type": "integer"},
            "visibility": {"enum": [value.value for value in EvidenceVisibility]},
        },
        ["record_kind", "order", "visibility"],
    )


def _ref_or_null() -> dict[str, object]:
    return {"anyOf": [{"$ref": "#/$defs/evidence_ref"}, {"type": "null"}]}


def _id_or_null() -> dict[str, object]:
    return {"anyOf": [{"$ref": "#/$defs/player_id"}, {"type": "null"}]}


def _evidence_array(*, minimum: int = 0) -> dict[str, object]:
    return {
        "items": {"$ref": "#/$defs/evidence_ref"},
        "maxItems": 8,
        "minItems": minimum,
        "type": "array",
        "uniqueItems": True,
    }


def _decision_schema(options: list[dict[str, object]], max_text: int) -> dict[str, object]:
    branches: list[dict[str, object]] = [_closed({"kind": {"const": "none"}}, ["kind"])]
    for option in options:
        kind = option["action_kind"]
        if kind == "co_report":
            continue
        base = {"kind": {"const": kind}, "option_id": {"const": option["option_id"]}}
        if kind == "chat":
            branch = base | {
                "message": {"maxLength": max_text, "minLength": 1, "type": "string"}
            }
            required = ["kind", "option_id", "message"]
        elif kind == "vote":
            # Phase 6 abstention is the explicit NoDecision branch.  Legacy
            # context-free VoteDecision(None) remains handled by the old schema.
            branch = base | {"target_player_id": {"enum": list(option["valid_targets"])}}
            required = ["kind", "option_id", "target_player_id"]
        elif kind == "ability":
            branch = base | {
                "target_player_ids": {
                    "items": {"enum": list(option["valid_targets"])},
                    "maxItems": option["target_count"],
                    "minItems": option["target_count"],
                    "type": "array",
                    "uniqueItems": True,
                }
            }
            required = ["kind", "option_id", "target_player_ids"]
        elif kind == "co_declare":
            branch = base | {
                "claimed_role_id": {"enum": list(option["claimed_role_ids"])},
                "comment": {"maxLength": max_text, "minLength": 1, "type": "string"},
            }
            required = ["kind", "option_id", "claimed_role_id", "comment"]
        else:  # pragma: no cover - typed ActionHandle exhaustiveness
            continue
        branches.append(_closed(branch, required))
    return {"oneOf": branches}


def _speech_schema() -> dict[str, object]:
    topic = {"enum": ["ALIGNMENT", "ROLE_CLAIM", "VOTE", "EVENT", "RELATION", "STRATEGY"]}
    stance = {"enum": ["SUPPORT", "OPPOSE", "UNCERTAIN"]}
    branches = [
        _closed({"kind": {"const": "NONE"}}, ["kind"]),
        _closed(
            {"kind": {"const": "CLAIM"}, "subject_player_id": {"$ref": "#/$defs/player_id"}, "topic": topic, "stance": stance, "evidence": _evidence_array()},
            ["kind", "subject_player_id", "topic", "stance", "evidence"],
        ),
        _closed(
            {"kind": {"const": "QUESTION"}, "addressee_player_id": {"$ref": "#/$defs/player_id"}, "subject_player_id": _id_or_null(), "topic": topic, "source": _ref_or_null()},
            ["kind", "addressee_player_id", "subject_player_id", "topic", "source"],
        ),
        _closed(
            {"kind": {"const": "ANSWER"}, "addressee_player_id": {"$ref": "#/$defs/player_id"}, "in_reply_to": {"$ref": "#/$defs/evidence_ref"}, "source_interpretation": {"const": "QUESTION"}, "topic": topic, "stance": stance, "evidence": _evidence_array()},
            ["kind", "addressee_player_id", "in_reply_to", "source_interpretation", "topic", "stance", "evidence"],
        ),
        _closed(
            {"kind": {"const": "REBUTTAL"}, "addressee_player_id": {"$ref": "#/$defs/player_id"}, "in_reply_to": {"$ref": "#/$defs/evidence_ref"}, "source_interpretation": {"const": "CLAIM"}, "topic": topic, "stance": stance, "evidence": _evidence_array()},
            ["kind", "addressee_player_id", "in_reply_to", "source_interpretation", "topic", "stance", "evidence"],
        ),
        _closed(
            {"kind": {"const": "OPINION_CHANGE"}, "subject_player_id": {"$ref": "#/$defs/player_id"}, "dimension": {"enum": ["SUSPICION", "CREDIBILITY"]}, "prior": {"minimum": 0, "maximum": 100, "type": "integer"}, "current": {"minimum": 0, "maximum": 100, "type": "integer"}, "causes": _evidence_array(minimum=1)},
            ["kind", "subject_player_id", "dimension", "prior", "current", "causes"],
        ),
        _closed(
            {"kind": {"const": "RELATION_HYPOTHESIS"}, "source_player_id": {"$ref": "#/$defs/player_id"}, "target_player_id": {"$ref": "#/$defs/player_id"}, "relation": {"$ref": "#/$defs/relation"}, "confidence": {"minimum": 0, "maximum": 100, "type": "integer"}, "evidence": _evidence_array()},
            ["kind", "source_player_id", "target_player_id", "relation", "confidence", "evidence"],
        ),
    ]
    return {"oneOf": branches}


def discussion_output_schema(
    *,
    options: list[dict[str, object]],
    capture: DiscussionCapture,
    max_text: int,
    player_ids: tuple[str, ...],
) -> dict[str, object]:
    player_id_schema: dict[str, object] = {"enum": list(player_ids)}
    relation = {"enum": ["SUPPORTS", "CONTRADICTS", "DEFENDS", "ACCUSES", "DISTANCES_FROM"]}
    score = {"minimum": 0, "maximum": 100, "type": "integer"}
    assessment = _closed(
        {"target_player_id": {"$ref": "#/$defs/player_id"}, "suspicion": score, "credibility": score, "confidence": score, "evidence": _evidence_array()},
        ["target_player_id", "suspicion", "credibility", "confidence", "evidence"],
    )
    claim = _closed(
        {"claim": {"$ref": "#/$defs/evidence_ref"}, "speaker_player_id": {"$ref": "#/$defs/player_id"}, "verdict": {"enum": ["UNVERIFIED", "SUPPORTED", "CONTRADICTED"]}, "confidence": score, "evidence": _evidence_array()},
        ["claim", "speaker_player_id", "verdict", "confidence", "evidence"],
    )
    relation_update = _closed(
        {"source_player_id": {"$ref": "#/$defs/player_id"}, "target_player_id": {"$ref": "#/$defs/player_id"}, "relation": {"$ref": "#/$defs/relation"}, "confidence": score, "evidence": _evidence_array(), "provenance": {"const": "PUBLIC_INFERENCE"}},
        ["source_player_id", "target_player_id", "relation", "confidence", "evidence", "provenance"],
    )
    strategy = _closed(
        {"scope": {"enum": ["GAME", "PHASE"]}, "mode": {"enum": ["GATHER_INFORMATION", "TEST_CLAIM", "RESOLVE_CONTRADICTION", "BUILD_CONSENSUS", "PROTECT_PRIVATE_INFORMATION", "PREPARE_VOTE", "USE_OFFERED_CAPABILITY", "WAIT"]}, "focus_player_ids": {"items": {"$ref": "#/$defs/player_id"}, "maxItems": 4, "type": "array", "uniqueItems": True}, "evidence": _evidence_array()},
        ["scope", "mode", "focus_player_ids", "evidence"],
    )
    reaction = _closed(
        {"trigger": {"const": _plain(capture.trigger.source)}, "score": score, "reason": {"enum": ["DIRECT_QUESTION", "DIRECT_MENTION", "CLAIM_CONFLICT", "VOTE_PRESSURE", "NEW_INFORMATION", "OTHER_AUTHORIZED"]}},
        ["trigger", "score", "reason"],
    )
    co = _closed(
        {"decision": {"enum": ["DECLARE", "SILENCE", "DEFER"]}, "selected_option_id": {"type": ["string", "null"]}, "claimed_role_id": {"type": ["string", "null"]}},
        ["decision", "selected_option_id", "claimed_role_id"],
    )
    pre_vote = _closed(
        {"option_id": {"minLength": 1, "type": "string"}, "ranked_target_player_ids": {"items": {"$ref": "#/$defs/player_id"}, "maxItems": 32, "type": "array", "uniqueItems": True}, "preferred_target_player_id": _id_or_null(), "evidence": _evidence_array()},
        ["option_id", "ranked_target_player_ids", "preferred_target_player_id", "evidence"],
    )
    nullable = lambda value: {"anyOf": [value, {"type": "null"}]}
    peer_chat = capture.trigger.kind == "PEER_CHAT"
    co_opportunity = capture.trigger.kind == "CO_OPPORTUNITY"
    pre_vote_trigger = capture.trigger.kind == "PRE_VOTE"
    proposal = _closed(
        {
            "schema_version": {"const": "aiwolf.discussion-proposal.v1"},
            "base_revision": {"const": capture.base_revision},
            "decision_kind": {"enum": ["none", "chat", "vote", "ability", "co_declare"]},
            "option_id": {"type": ["string", "null"]},
            "speech_act": {"$ref": "#/$defs/speech_act"},
            "reaction": reaction if peer_chat else {"const": None},
            "assessment_updates": {"items": {"$ref": "#/$defs/assessment"}, "maxItems": 4, "type": "array"},
            "claim_updates": {"items": {"$ref": "#/$defs/claim"}, "maxItems": 4, "type": "array"},
            "relation_updates": {"items": {"$ref": "#/$defs/relation_update"}, "maxItems": 2, "type": "array"},
            "strategy_update": nullable({"$ref": "#/$defs/strategy"}),
            "co_judgment": co if co_opportunity else {"const": None},
            "pre_vote_reassessment": pre_vote if pre_vote_trigger else {"const": None},
        },
        ["schema_version", "base_revision", "decision_kind", "option_id", "speech_act", "reaction", "assessment_updates", "claim_updates", "relation_updates", "strategy_update", "co_judgment", "pre_vote_reassessment"],
    )
    return {
        "$defs": {
            "assessment": assessment,
            "claim": claim,
            "evidence_ref": _evidence_schema(),
            "player_id": player_id_schema,
            "relation": relation,
            "relation_update": relation_update,
            "speech_act": _speech_schema(),
            "strategy": strategy,
        },
        **_closed(
            {
                "decision": _decision_schema(options, max_text),
                "discussion": proposal,
            },
            ["decision", "discussion"],
        ),
    }


def _event_key(event: ImportantEvent) -> tuple[EvidenceRecordKind, int]:
    return event.source.record_kind, event.source.order


def _memory_rank(event: ImportantEvent) -> tuple[int, int, str]:
    return (-event.importance, -event.source.order, event.source.record_kind.value)


def _newest_rank(event: ImportantEvent) -> tuple[int, str]:
    return (-event.source.order, event.source.record_kind.value)


def _bounded_prefix(value: str, *, max_scalars: int, max_bytes: int) -> str:
    result: list[str] = []
    used = 0
    for scalar in value[:max_scalars]:
        encoded = scalar.encode("utf-8")
        if used + len(encoded) > max_bytes:
            break
        result.append(scalar)
        used += len(encoded)
    return "".join(result)


def _project_event(
    event: ImportantEvent,
    *,
    recent: bool,
    config: DiscussionPromptConfig,
) -> ImportantEvent:
    if event.text_excerpt is None:
        return event
    scalar_limit = (
        config.max_recent_text_scalars
        if recent
        else config.max_important_text_scalars
    )
    byte_limit = (
        config.max_recent_text_utf8_bytes
        if recent
        else config.max_important_text_utf8_bytes
    )
    excerpt = _bounded_prefix(
        event.text_excerpt,
        max_scalars=scalar_limit,
        max_bytes=byte_limit,
    )
    return replace(
        event,
        text_excerpt=excerpt,
        text_truncated=(
            event.text_original_scalars != len(excerpt)
            or event.text_original_utf8_bytes != len(excerpt.encode("utf-8"))
        ),
    )


def _prompt_contract(
    system_message: str,
    canonical_input: Mapping[str, object],
    schema: Mapping[str, object],
) -> tuple[str, int, int]:
    user = json.dumps(
        canonical_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    complete = json.dumps(
        {
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user},
            ],
            "output_schema": schema,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    encoded = complete.encode("utf-8")
    return user, len(encoded), token_proxy_units(complete)


def _repair_reservation() -> tuple[int, int]:
    """Return the serialized upper bound for the mandatory empty repair turn."""

    from ai_client.llm.decision import DecisionValidationCode
    from ai_client.llm.prompt import canonical_prompt_json
    from ai_client.llm.types import LLMMessage

    messages = (
        LLMMessage(role="system", content="x"),
        LLMMessage(role="user", content="x"),
    )
    schema: dict[str, object] = {}
    base = canonical_prompt_json(messages, schema)
    maximum_bytes = 0
    maximum_proxy = 0
    for code in DecisionValidationCode:
        for truncated in (False, True):
            repair = _repair_message(
                validation_code=code.value,
                excerpt="",
                original_scalars=sys.maxsize,
                original_utf8_bytes=sys.maxsize,
                excerpt_truncated=truncated,
                output_sha256="f" * 64,
            )
            complete = canonical_prompt_json(
                messages + (LLMMessage(role="user", content=repair),), schema
            )
            maximum_bytes = max(
                maximum_bytes,
                len(complete.encode("utf-8")) - len(base.encode("utf-8")),
            )
            maximum_proxy = max(
                maximum_proxy,
                token_proxy_units(complete) - token_proxy_units(base),
            )
    return maximum_bytes, maximum_proxy


def _repair_message(
    *,
    validation_code: str,
    excerpt: str,
    original_scalars: int,
    original_utf8_bytes: int,
    excerpt_truncated: bool,
    output_sha256: str,
) -> str:
    return json.dumps(
        {
            "repair": {
                "instruction": "Return exactly one value matching the unchanged output schema.",
                "invalid_output_excerpt": excerpt,
                "invalid_output_original_scalars": original_scalars,
                "invalid_output_original_utf8_bytes": original_utf8_bytes,
                "invalid_output_excerpt_truncated": excerpt_truncated,
                "invalid_output_sha256": output_sha256,
                "validation_code": validation_code,
            }
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def project_discussion_brain_input(
    request: object,
    *,
    llm_config: object,
    config: DiscussionPromptConfig,
    system_message: str,
) -> object:
    """Return a ``PromptProjection`` without importing Brain into this layer."""

    if not isinstance(config, DiscussionPromptConfig):
        raise TypeError("config must be DiscussionPromptConfig")
    capture = getattr(request, "discussion", None)
    if not isinstance(capture, DiscussionCapture):
        raise TypeError("contextful projection requires DiscussionCapture")
    if canonical_sha256(capture.context) != capture.context_sha256:
        raise ValueError("PROMPT_INVALID")
    if canonical_sha256(capture.state) != capture.state_sha256:
        raise ValueError("PROMPT_INVALID")
    snapshot = getattr(request, "snapshot", None)
    action_context = getattr(request, "action_context", None)
    if (
        snapshot is None
        or action_context is None
        or snapshot.version != capture.world_version
        or snapshot.last_applied_seq != capture.last_applied_seq
        or action_context.world_version != capture.world_version
        or action_context.world_last_applied_seq != capture.last_applied_seq
        or action_context.network_last_seq != capture.last_applied_seq
        or snapshot.phase is None
        or snapshot.phase.phase != capture.trigger.phase
        or snapshot.phase.day != capture.trigger.day
    ):
        raise ValueError("PROMPT_INVALID")
    context_bytes = len(canonical_json_bytes(capture.context))
    if context_bytes > config.max_context_bytes:
        raise ValueError("PROMPT_TOO_LARGE")

    max_human = getattr(llm_config, "max_human_text_chars")
    short_chat = getattr(llm_config, "short_chat")
    max_text = (
        getattr(llm_config, "max_generated_text_chars")
        if short_chat is None
        else short_chat.max_text_chars
    )
    options = [_option(item, max_human) for item in request.action_context.options]
    option_ids = [item["option_id"] for item in options]
    if len(option_ids) != len(set(option_ids)):
        raise ValueError("PROMPT_INVALID")
    player_ids = tuple(sorted({item.player_id for item in snapshot.players}))
    if capture.player_id not in player_ids or not player_ids:
        raise ValueError("PROMPT_INVALID")
    schema = discussion_output_schema(
        options=options,
        capture=capture,
        max_text=max_text,
        player_ids=player_ids,
    )

    effective_bytes = min(32768, getattr(llm_config, "max_prompt_bytes"), config.max_prompt_bytes)
    effective_proxy = min(8192, config.max_token_proxy_units)
    repair_bytes, repair_proxy = _repair_reservation()
    state_values: dict[str, list[object] | object] = {
        "strategy": None,
        "assessments": [],
        "claims": [],
        "relations": [],
    }
    state_omitted = {"strategy": 0, "assessments": 0, "claims": 0, "relations": 0}
    memory_records: list[ImportantEvent] = []
    memory_byte_exhausted = False
    proxy_exhausted = False

    def material() -> dict[str, object]:
        provenance = capture.state.provenance
        return {
            "schema_version": "aiwolf.discussion-prompt.v1",
            "action_context": {
                "is_caught_up": request.action_context.is_caught_up,
                "network_last_seq": request.action_context.network_last_seq,
                "options": options,
                "world_last_applied_seq": request.action_context.world_last_applied_seq,
                "world_version": request.action_context.world_version,
            },
            "capture": {
                "capture_id": capture.capture_id,
                "capture_ordinal": capture.capture_ordinal,
                "context_sha256": capture.context_sha256,
                "state_sha256": capture.state_sha256,
                "epoch": capture.epoch,
                "base_revision": capture.base_revision,
                "fact_revision": capture.fact_revision,
                "world_version": capture.world_version,
                "last_applied_seq": capture.last_applied_seq,
                "current_player_ids": list(player_ids),
                "trigger": _plain(capture.trigger),
            },
            "context": _plain(capture.context),
            "limits": {
                "max_proposal_utf8_bytes": config.max_proposal_bytes,
            },
            "lifecycle": {
                "history_complete": provenance.history_complete,
                "co_complete": provenance.co_complete,
                "ability_results_complete": provenance.ability_results_complete,
                "world_history_complete": provenance.world_history_complete,
                "world_history_dropped_count": provenance.world_history_dropped_count,
                "world_history_dropped_through_order": provenance.world_history_dropped_through_order,
                "remembered_after_world_eviction_count": provenance.remembered_after_world_eviction_count,
                "visibility_lost_count": sum(event.source.visibility is EvidenceVisibility.VISIBILITY_LOST for event in capture.evidence),
                "unknown_event_count": provenance.unknown_event_count,
                "known_unmodeled_event_count": provenance.known_unmodeled_event_count,
                "malformed_event_count": provenance.malformed_event_count,
                "model_state_reset": provenance.model_state_reset,
                "reset_reason": None if provenance.reset_reason is None else provenance.reset_reason.value,
            },
            "state": {
                "identity": {
                    "epoch": capture.state.epoch,
                    "revision": capture.state.revision,
                    "fact_revision": capture.state.fact_revision,
                    "world_version": capture.state.world_version,
                    "last_applied_seq": capture.state.last_applied_seq,
                    "phase": capture.state.phase,
                    "day": capture.state.day,
                },
                "strategy": state_values["strategy"],
                "assessments": state_values["assessments"],
                "claims": state_values["claims"],
                "relations": state_values["relations"],
                "omitted_counts": state_omitted,
                "state_projection_omitted": any(state_omitted.values()),
            },
            "memory": {
                "records": [_plain(event) for event in sorted(memory_records, key=lambda item: evidence_sort_key(item.source))],
                "included_records": len(memory_records),
                "omitted_records": len(capture.evidence) - len(memory_records),
                "omitted_through_order": (
                    max(
                        (
                            item.source.order
                            for item in capture.evidence
                            if _event_key(item)
                            not in {_event_key(value) for value in memory_records}
                        ),
                        default=None,
                    )
                ),
                "memory_byte_exhausted": memory_byte_exhausted,
                "token_proxy_exhausted": proxy_exhausted,
            },
        }

    def fits() -> tuple[bool, int, int, int, int]:
        value = material()
        _user, complete_bytes, proxy = _prompt_contract(system_message, value, schema)
        state_bytes = len(canonical_json_bytes(value["state"]))
        memory_bytes = len(canonical_json_bytes(value["memory"]))
        return (
            complete_bytes + repair_bytes <= effective_bytes
            and proxy + repair_proxy <= effective_proxy
            and state_bytes <= config.max_state_section_bytes
            and memory_bytes <= config.max_memory_section_bytes,
            complete_bytes,
            proxy,
            state_bytes,
            memory_bytes,
        )

    # Mandatory skeleton: static instruction, schema, complete context/options,
    # lifecycle, and the minimum revision identity.
    okay, complete_bytes, proxy, state_bytes, memory_bytes = fits()
    if not okay:
        raise ValueError("PROMPT_TOO_LARGE")

    evidence = tuple(capture.evidence)
    current = tuple(
        item for item in evidence if not item.remembered_after_world_eviction
    )
    newest = tuple(sorted(current, key=_newest_rank)[: config.max_newest_records])
    newest_ids = {_event_key(item) for item in newest}
    trigger_event: ImportantEvent | None = None
    if capture.trigger.source is not None:
        trigger_event = next(
            (item for item in current if item.source == capture.trigger.source), None
        )
        if trigger_event is None:
            raise ValueError("PROMPT_INVALID")
        if config.max_combined_records == 0:
            raise ValueError("PROMPT_TOO_LARGE")
        memory_records.append(
            _project_event(trigger_event, recent=True, config=config)
        )
        if not fits()[0]:
            memory_records.pop()
            raise ValueError("PROMPT_TOO_LARGE")

    # The reserved peer trigger is mandatory and consumes both budgets before
    # any optional cognitive state.  Optional state can therefore never crowd
    # the source out of a reaction prompt.
    state_candidates: list[tuple[str, object]] = []
    strategy = capture.state.strategy
    if strategy is not None:
        state_candidates.append(("strategy", strategy))
    state_candidates.extend(
        ("assessments", item)
        for item in sorted(capture.state.assessments, key=lambda item: (-item.confidence, item.player_id))
    )
    state_candidates.extend(
        ("claims", item)
        for item in sorted(capture.state.claims, key=lambda item: (-item.confidence, evidence_sort_key(item.claim)))
    )
    state_candidates.extend(
        ("relations", item)
        for item in sorted(capture.state.relations, key=lambda item: (-item.confidence, item.source_player_id, item.target_player_id, item.relation.value))
    )
    stop_state = False
    for index, (kind, candidate) in enumerate(state_candidates):
        if stop_state:
            state_omitted[kind] += 1
            continue
        previous = state_values[kind]
        if kind == "strategy":
            state_values[kind] = _plain(candidate)
        else:
            assert isinstance(previous, list)
            previous.append(_plain(candidate))
        if not fits()[0]:
            if kind == "strategy":
                state_values[kind] = None
            else:
                assert isinstance(previous, list)
                previous.pop()
            state_omitted[kind] += 1
            stop_state = True
    excluded = set(newest_ids)
    if trigger_event is not None:
        excluded.add(_event_key(trigger_event))
    old = tuple(
        sorted(
            (item for item in evidence if _event_key(item) not in excluded),
            key=_memory_rank,
        )[: config.max_important_old_records]
    )

    ordered_candidates: list[ImportantEvent] = []
    ordered_candidates.extend(newest)
    ordered_candidates.extend(old)
    seen: set[tuple[EvidenceRecordKind, int]] = (
        set() if trigger_event is None else {_event_key(trigger_event)}
    )
    for candidate in ordered_candidates:
        identity = _event_key(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        if len(memory_records) >= config.max_combined_records:
            continue
        projected = _project_event(
            candidate,
            recent=(identity in newest_ids or candidate is trigger_event),
            config=config,
        )
        memory_records.append(projected)
        okay, cb, pu, _sb, mb = fits()
        if not okay:
            memory_records.pop()
            if (
                mb > config.max_memory_section_bytes
                or cb + repair_bytes > effective_bytes
            ):
                memory_byte_exhausted = True
            if pu + repair_proxy > effective_proxy:
                proxy_exhausted = True

    final_input = material()
    user_message, prompt_bytes, proxy_units = _prompt_contract(
        system_message, final_input, schema
    )
    if (
        prompt_bytes + repair_bytes > effective_bytes
        or proxy_units + repair_proxy > effective_proxy
        or len(canonical_json_bytes(final_input["state"])) > config.max_state_section_bytes
        or len(canonical_json_bytes(final_input["memory"])) > config.max_memory_section_bytes
    ):
        raise ValueError("PROMPT_TOO_LARGE")
    if (
        canonical_sha256(capture.context) != capture.context_sha256
        or canonical_sha256(capture.state) != capture.state_sha256
        or final_input["memory"]["included_records"] != len(memory_records)
        or final_input["memory"]["omitted_records"] != len(evidence) - len(memory_records)
    ):
        raise ValueError("PROMPT_INVALID")

    # Delayed imports keep this lower module independent from Brain and avoid a
    # package cycle while still returning the established immutable projection.
    from ai_client.llm.types import LLMMessage, PromptProjection

    return PromptProjection(
        messages=(
            LLMMessage(role="system", content=system_message),
            LLMMessage(role="user", content=user_message),
        ),
        decision_schema=schema,
        canonical_input=final_input,
        prompt_bytes=prompt_bytes,
        prompt_sha256=hashlib.sha256(
            json.dumps(
                {
                    "messages": [
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": user_message},
                    ],
                    "output_schema": schema,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
        included_history_records=len(memory_records),
        omitted_history_records=len(evidence) - len(memory_records),
        short_chat=short_chat,
        token_proxy_units=proxy_units,
        discussion_capture=capture,
    )


def build_discussion_repair_projection(
    projection: object,
    *,
    validation_code: object,
    invalid_output: str,
    llm_config: object,
    config: DiscussionPromptConfig,
) -> object:
    """Append one bounded repair datum to the immutable original projection."""

    from ai_client.llm.types import LLMMessage, PromptProjection
    from ai_client.llm.prompt import canonical_prompt_json

    if not isinstance(projection, PromptProjection) or projection.discussion_capture is None:
        raise TypeError("repair requires a discussion PromptProjection")
    from ai_client.llm.prompt import PromptProjectionError

    if not isinstance(invalid_output, str):
        raise TypeError("invalid_output must be str")
    max_excerpt = getattr(llm_config, "max_repair_excerpt_chars")
    excerpt = invalid_output[:max_excerpt]
    response = invalid_output.encode("utf-8")
    effective_bytes = min(
        32768, getattr(llm_config, "max_prompt_bytes"), config.max_prompt_bytes
    )
    effective_proxy = min(8192, config.max_token_proxy_units)

    while True:
        repair = _repair_message(
            validation_code=validation_code.value,
            excerpt=excerpt,
            original_scalars=len(invalid_output),
            original_utf8_bytes=len(response),
            excerpt_truncated=excerpt != invalid_output,
            output_sha256=hashlib.sha256(response).hexdigest(),
        )
        messages = projection.messages + (LLMMessage(role="user", content=repair),)
        prompt_json = canonical_prompt_json(messages, projection.decision_schema)
        prompt_bytes = len(prompt_json.encode("utf-8"))
        proxy = token_proxy_units(prompt_json)
        repaired = replace(
            projection,
            messages=messages,
            prompt_bytes=prompt_bytes,
            prompt_sha256=hashlib.sha256(prompt_json.encode("utf-8")).hexdigest(),
            token_proxy_units=proxy,
        )
        if prompt_bytes <= effective_bytes and proxy <= effective_proxy:
            return repaired
        if not excerpt:
            raise PromptProjectionError("PROMPT_TOO_LARGE", projection=repaired)
        excerpt = excerpt[:-1]
