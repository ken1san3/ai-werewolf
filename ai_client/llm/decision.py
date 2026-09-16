"""Strict request-local conversion from model JSON to BrainDecision."""

from __future__ import annotations

from dataclasses import dataclass
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
    brain_decision_identity,
)
from ai_client.discussion.context import canonical_json_bytes
from ai_client.discussion.model import (
    AssessmentUpdate,
    ClaimUpdate,
    ClaimVerdict,
    CoJudgment,
    CoJudgmentDecision,
    DiscussionProposal,
    DiscussionStance,
    DiscussionTopic,
    EvidenceRecordKind,
    EvidenceRef,
    EvidenceVisibility,
    OpinionDimension,
    PreVoteReassessment,
    ReactionAssessment,
    ReactionReason,
    RelationKind,
    RelationUpdate,
    SpeechActAnswer,
    SpeechActClaim,
    SpeechActKind,
    SpeechActNone,
    SpeechActOpinionChange,
    SpeechActQuestion,
    SpeechActRebuttal,
    SpeechActRelationHypothesis,
    StrategyMode,
    StrategyUpdate,
    _proposal_evidence,
    evidence_identity,
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


@dataclass(frozen=True)
class ParsedDiscussionOutput:
    decision: BrainDecision
    proposal: DiscussionProposal


def _load_json(text: str) -> dict[str, object]:
    if not isinstance(text, str) or not text:
        raise DecisionValidationError(DecisionValidationCode.JSON_SYNTAX)
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
    return value


def parse_llm_decision(
    text: str, *, projection: PromptProjection
) -> BrainDecision:
    if not isinstance(projection, PromptProjection):
        raise TypeError("projection must be PromptProjection")
    if projection.discussion_capture is not None:
        return parse_llm_output(text, projection=projection).decision
    value = _load_json(text)
    decision = _parse_decision_value(value, projection=projection, contextful=False)
    if not Draft202012Validator(_plain_json(projection.decision_schema)).is_valid(value):
        raise DecisionValidationError(DecisionValidationCode.SCHEMA)
    return decision


def _parse_decision_value(
    value: Mapping[str, object],
    *,
    projection: PromptProjection,
    contextful: bool,
) -> BrainDecision:

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
            if contextful:
                raise DecisionValidationError(DecisionValidationCode.SCHEMA)
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
        if contextful:
            raise DecisionValidationError(DecisionValidationCode.SCHEMA)
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

    return decision


def parse_llm_output(
    text: str, *, projection: PromptProjection
) -> BrainDecision | ParsedDiscussionOutput:
    """Atomically parse the legacy action or the complete Phase 6 result."""

    if not isinstance(projection, PromptProjection):
        raise TypeError("projection must be PromptProjection")
    if projection.discussion_capture is None:
        return parse_llm_decision(text, projection=projection)
    value = _load_json(text)
    if not Draft202012Validator(_plain_json(projection.decision_schema)).is_valid(value):
        raise DecisionValidationError(DecisionValidationCode.SCHEMA)
    _exact_keys(value, {"decision", "discussion"})
    decision_value = value.get("decision")
    proposal_value = value.get("discussion")
    if not isinstance(decision_value, Mapping) or not isinstance(proposal_value, Mapping):
        raise DecisionValidationError(DecisionValidationCode.SCHEMA)
    decision = _parse_decision_value(
        decision_value,
        projection=projection,
        contextful=True,
    )
    try:
        proposal = _parse_proposal(proposal_value)
        _validate_semantic_output(decision, proposal, projection)
    except DecisionValidationError:
        raise
    except (TypeError, ValueError):
        raise DecisionValidationError(DecisionValidationCode.SCHEMA) from None
    return ParsedDiscussionOutput(decision=decision, proposal=proposal)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DecisionValidationError(DecisionValidationCode.SCHEMA)
    return value


def _sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, list):
        raise DecisionValidationError(DecisionValidationCode.SCHEMA)
    return tuple(value)


def _enum(enum_type: type, value: object) -> object:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        raise DecisionValidationError(DecisionValidationCode.SCHEMA) from None


def _evidence_ref(value: object) -> EvidenceRef:
    data = _mapping(value)
    _exact_keys(data, {"record_kind", "order", "visibility"})
    try:
        return EvidenceRef(
            record_kind=_enum(EvidenceRecordKind, data["record_kind"]),  # type: ignore[arg-type]
            order=data["order"],  # type: ignore[arg-type]
            visibility=_enum(EvidenceVisibility, data["visibility"]),  # type: ignore[arg-type]
        )
    except (TypeError, ValueError):
        raise DecisionValidationError(DecisionValidationCode.SCHEMA) from None


def _evidence_values(value: object) -> tuple[EvidenceRef, ...]:
    return tuple(_evidence_ref(item) for item in _sequence(value))


def _parse_speech(value: object) -> object:
    data = _mapping(value)
    kind = data.get("kind")
    if kind == "NONE":
        _exact_keys(data, {"kind"})
        return SpeechActNone(SpeechActKind.NONE)
    if kind == "CLAIM":
        _exact_keys(data, {"kind", "subject_player_id", "topic", "stance", "evidence"})
        return SpeechActClaim(
            SpeechActKind.CLAIM,
            data["subject_player_id"],  # type: ignore[arg-type]
            _enum(DiscussionTopic, data["topic"]),  # type: ignore[arg-type]
            _enum(DiscussionStance, data["stance"]),  # type: ignore[arg-type]
            _evidence_values(data["evidence"]),
        )
    if kind == "QUESTION":
        _exact_keys(data, {"kind", "addressee_player_id", "subject_player_id", "topic", "source"})
        return SpeechActQuestion(
            SpeechActKind.QUESTION,
            data["addressee_player_id"],  # type: ignore[arg-type]
            data["subject_player_id"],  # type: ignore[arg-type]
            _enum(DiscussionTopic, data["topic"]),  # type: ignore[arg-type]
            None if data["source"] is None else _evidence_ref(data["source"]),
        )
    if kind in {"ANSWER", "REBUTTAL"}:
        interpretation = "QUESTION" if kind == "ANSWER" else "CLAIM"
        _exact_keys(data, {"kind", "addressee_player_id", "in_reply_to", "source_interpretation", "topic", "stance", "evidence"})
        cls = SpeechActAnswer if kind == "ANSWER" else SpeechActRebuttal
        return cls(
            SpeechActKind(kind),
            data["addressee_player_id"],  # type: ignore[arg-type]
            _evidence_ref(data["in_reply_to"]),
            interpretation,
            _enum(DiscussionTopic, data["topic"]),  # type: ignore[arg-type]
            _enum(DiscussionStance, data["stance"]),  # type: ignore[arg-type]
            _evidence_values(data["evidence"]),
        )
    if kind == "OPINION_CHANGE":
        _exact_keys(data, {"kind", "subject_player_id", "dimension", "prior", "current", "causes"})
        return SpeechActOpinionChange(
            SpeechActKind.OPINION_CHANGE,
            data["subject_player_id"],  # type: ignore[arg-type]
            _enum(OpinionDimension, data["dimension"]),  # type: ignore[arg-type]
            data["prior"],  # type: ignore[arg-type]
            data["current"],  # type: ignore[arg-type]
            _evidence_values(data["causes"]),
        )
    if kind == "RELATION_HYPOTHESIS":
        _exact_keys(data, {"kind", "source_player_id", "target_player_id", "relation", "confidence", "evidence"})
        return SpeechActRelationHypothesis(
            SpeechActKind.RELATION_HYPOTHESIS,
            data["source_player_id"],  # type: ignore[arg-type]
            data["target_player_id"],  # type: ignore[arg-type]
            _enum(RelationKind, data["relation"]),  # type: ignore[arg-type]
            data["confidence"],  # type: ignore[arg-type]
            _evidence_values(data["evidence"]),
        )
    raise DecisionValidationError(DecisionValidationCode.SCHEMA)


def _parse_proposal(value: Mapping[str, object]) -> DiscussionProposal:
    required = {
        "schema_version", "base_revision", "decision_kind", "option_id", "speech_act",
        "reaction", "assessment_updates", "claim_updates", "relation_updates",
        "strategy_update", "co_judgment", "pre_vote_reassessment",
    }
    _exact_keys(value, required)

    assessments = []
    for raw in _sequence(value["assessment_updates"]):
        item = _mapping(raw)
        _exact_keys(item, {"target_player_id", "suspicion", "credibility", "confidence", "evidence"})
        assessments.append(AssessmentUpdate(
            item["target_player_id"], item["suspicion"], item["credibility"], item["confidence"], _evidence_values(item["evidence"]),  # type: ignore[arg-type]
        ))

    claims = []
    for raw in _sequence(value["claim_updates"]):
        item = _mapping(raw)
        _exact_keys(item, {"claim", "speaker_player_id", "verdict", "confidence", "evidence"})
        claims.append(ClaimUpdate(
            _evidence_ref(item["claim"]), item["speaker_player_id"], _enum(ClaimVerdict, item["verdict"]), item["confidence"], _evidence_values(item["evidence"]),  # type: ignore[arg-type]
        ))

    relations = []
    for raw in _sequence(value["relation_updates"]):
        item = _mapping(raw)
        _exact_keys(item, {"source_player_id", "target_player_id", "relation", "confidence", "evidence", "provenance"})
        relations.append(RelationUpdate(
            item["source_player_id"], item["target_player_id"], _enum(RelationKind, item["relation"]), item["confidence"], _evidence_values(item["evidence"]), item["provenance"],  # type: ignore[arg-type]
        ))

    strategy = None
    if value["strategy_update"] is not None:
        item = _mapping(value["strategy_update"])
        _exact_keys(item, {"scope", "mode", "focus_player_ids", "evidence"})
        strategy = StrategyUpdate(
            item["scope"], _enum(StrategyMode, item["mode"]), tuple(_sequence(item["focus_player_ids"])), _evidence_values(item["evidence"]),  # type: ignore[arg-type]
        )

    reaction = None
    if value["reaction"] is not None:
        item = _mapping(value["reaction"])
        _exact_keys(item, {"trigger", "score", "reason"})
        reaction = ReactionAssessment(
            _evidence_ref(item["trigger"]), item["score"], _enum(ReactionReason, item["reason"]),  # type: ignore[arg-type]
        )

    judgment = None
    if value["co_judgment"] is not None:
        item = _mapping(value["co_judgment"])
        _exact_keys(item, {"decision", "selected_option_id", "claimed_role_id"})
        judgment = CoJudgment(
            _enum(CoJudgmentDecision, item["decision"]), item["selected_option_id"], item["claimed_role_id"],  # type: ignore[arg-type]
        )

    pre_vote = None
    if value["pre_vote_reassessment"] is not None:
        item = _mapping(value["pre_vote_reassessment"])
        _exact_keys(item, {"option_id", "ranked_target_player_ids", "preferred_target_player_id", "evidence"})
        pre_vote = PreVoteReassessment(
            item["option_id"], tuple(_sequence(item["ranked_target_player_ids"])), item["preferred_target_player_id"], _evidence_values(item["evidence"]),  # type: ignore[arg-type]
        )

    return DiscussionProposal(
        schema_version=value["schema_version"],  # type: ignore[arg-type]
        base_revision=value["base_revision"],  # type: ignore[arg-type]
        decision_kind=value["decision_kind"],  # type: ignore[arg-type]
        option_id=value["option_id"],  # type: ignore[arg-type]
        speech_act=_parse_speech(value["speech_act"]),  # type: ignore[arg-type]
        reaction=reaction,
        assessment_updates=tuple(assessments),
        claim_updates=tuple(claims),
        relation_updates=tuple(relations),
        strategy_update=strategy,
        co_judgment=judgment,
        pre_vote_reassessment=pre_vote,
    )


def _validate_semantic_output(
    decision: BrainDecision,
    proposal: DiscussionProposal,
    projection: PromptProjection,
) -> None:
    capture = projection.discussion_capture
    assert capture is not None
    kind, option_id = brain_decision_identity(decision)
    if kind == "co_report" or (proposal.decision_kind, proposal.option_id) != (kind, option_id):
        raise DecisionValidationError(DecisionValidationCode.OPTION_NOT_OFFERED)
    if proposal.base_revision != capture.base_revision:
        raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)

    memory = _mapping(projection.canonical_input.get("memory"))
    projected: dict[tuple[EvidenceRecordKind, int], EvidenceRef] = {}
    for raw in _sequence(list(memory.get("records", ()))):
        source = _mapping(raw).get("source")
        ref = _evidence_ref(source)
        projected[evidence_identity(ref)] = ref
    captured = {evidence_identity(item.source): item for item in capture.evidence}
    for ref in _proposal_evidence(proposal):
        identity = evidence_identity(ref)
        if projected.get(identity) != ref or identity not in captured or captured[identity].source != ref:
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)

    capture_data = _mapping(projection.canonical_input.get("capture"))
    players = set(_sequence(list(capture_data.get("current_player_ids", ()))))
    self_id = capture.player_id
    player_fields: list[str] = []
    speech = proposal.speech_act
    for name in ("subject_player_id", "source_player_id", "target_player_id", "addressee_player_id"):
        value = getattr(speech, name, None)
        if value is not None:
            player_fields.append(value)
    for item in proposal.assessment_updates:
        player_fields.append(item.target_player_id)
    for item in proposal.claim_updates:
        player_fields.append(item.speaker_player_id)
    for item in proposal.relation_updates:
        player_fields.extend((item.source_player_id, item.target_player_id))
    if proposal.strategy_update is not None:
        player_fields.extend(proposal.strategy_update.focus_player_ids)
    if any(value not in players for value in player_fields):
        raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
    if any(item.target_player_id == self_id for item in proposal.assessment_updates):
        raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)

    if isinstance(speech, (SpeechActAnswer, SpeechActRebuttal)):
        source = captured[evidence_identity(speech.in_reply_to)]
        if (
            source.source.record_kind not in {EvidenceRecordKind.CHAT, EvidenceRecordKind.CO_DECLARATION, EvidenceRecordKind.CO_REPORT}
            or len(source.actor_player_ids) != 1
            or source.actor_player_ids[0] == self_id
            or speech.addressee_player_id != source.actor_player_ids[0]
        ):
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
    if isinstance(speech, SpeechActOpinionChange):
        prior = next(
            (
                item
                for item in capture.state.assessments
                if item.player_id == speech.subject_player_id
            ),
            None,
        )
        if prior is None:
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
        prior_value = (
            prior.suspicion
            if speech.dimension is OpinionDimension.SUSPICION
            else prior.credibility
        )
        if speech.prior != prior_value or all(
            evidence_identity(ref)
            in {evidence_identity(value) for value in prior.evidence}
            for ref in speech.causes
        ):
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
    for item in proposal.claim_updates:
        source = captured[evidence_identity(item.claim)]
        if len(source.actor_player_ids) != 1 or source.actor_player_ids[0] == self_id or item.speaker_player_id != source.actor_player_ids[0]:
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)

    trigger = capture.trigger
    expected = {
        "INITIAL_CHAT": {"none", "chat"},
        "PEER_CHAT": {"none", "chat"},
        "CO_OPPORTUNITY": {"none", "co_declare"},
        "PRE_VOTE": {"none", "vote"},
        "ABILITY": {"none", "ability"},
    }
    if proposal.decision_kind not in expected[trigger.kind]:
        raise DecisionValidationError(DecisionValidationCode.OPTION_NOT_OFFERED)
    if trigger.kind == "PEER_CHAT":
        if proposal.reaction is None or proposal.reaction.trigger != trigger.source:
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
        if isinstance(decision, ChatDecision):
            option = _find_option(projection, "chat", decision.option_id)
            source = captured[evidence_identity(trigger.source)]  # type: ignore[arg-type]
            if option.get("channel") != source.channel_id:
                raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
    elif proposal.reaction is not None:
        raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)

    if trigger.kind == "CO_OPPORTUNITY":
        judgment = proposal.co_judgment
        if judgment is None:
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
        if judgment.decision is CoJudgmentDecision.DECLARE:
            if not isinstance(decision, CoDeclareDecision) or judgment.selected_option_id != decision.option_id or judgment.claimed_role_id != decision.claimed_role_id:
                raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
        elif not isinstance(decision, NoDecision):
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
    elif proposal.co_judgment is not None:
        raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)

    if trigger.kind == "PRE_VOTE":
        reassessment = proposal.pre_vote_reassessment
        if reassessment is None:
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
        option = _find_option(projection, "vote", reassessment.option_id)
        offered = tuple(option.get("valid_targets", ()))
        if any(target not in offered for target in reassessment.ranked_target_player_ids):
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
        if isinstance(decision, VoteDecision):
            if decision.target_player_id is None or reassessment.preferred_target_player_id != decision.target_player_id or reassessment.option_id != decision.option_id:
                raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
        elif reassessment.preferred_target_player_id is not None or option.get("allows_abstain") is not True:
            raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)
    elif proposal.pre_vote_reassessment is not None:
        raise DecisionValidationError(DecisionValidationCode.VALUE_NOT_OFFERED)

    limits = _mapping(projection.canonical_input.get("limits"))
    proposal_limit = limits.get("max_proposal_utf8_bytes")
    if type(proposal_limit) is not int or len(canonical_json_bytes(proposal)) > proposal_limit:
        raise DecisionValidationError(DecisionValidationCode.TEXT_BOUND)


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
        branches = schema.get("oneOf", [])
        if projection.discussion_capture is not None:
            decision_schema = schema.get("properties", {}).get("decision", {})
            if isinstance(decision_schema, dict):
                branches = decision_schema.get("oneOf", [])
        for branch in branches:
            properties = branch.get("properties", {})
            if (
                properties.get("kind", {}).get("const") == kind
                and properties.get("option_id", {}).get("const") == option_id
            ):
                value = properties.get(property_name, {}).get("maxLength")
                if type(value) is int and value >= 1:
                    return value
    raise DecisionValidationError(DecisionValidationCode.SCHEMA)
