from __future__ import annotations

from dataclasses import replace
import asyncio
import hashlib
import json

import pytest
from jsonschema import Draft202012Validator
import httpx

from ai_client.brain import (
    AbilityDecision,
    BrainActionContext,
    BrainActionOption,
    BrainInput,
    ChatDecision,
    CoDeclareDecision,
    NoDecision,
    VoteDecision,
)
from ai_client.discussion.context import (
    AuthorizedChatChannelContext,
    AuthorizedDiscussionContext,
    BoundDiscussionContext,
    CountParityWinCondition,
    canonical_json_bytes,
    canonical_sha256,
)
from ai_client.discussion.model import (
    AiDiscussionGenerationStatus,
    AssessmentUpdate,
    ClaimUpdate,
    ClaimVerdict,
    CoJudgment,
    CoJudgmentDecision,
    DiscussionGenerationAck,
    DiscussionProposal,
    DiscussionStance,
    DiscussionTopic,
    DiscussionTrigger,
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
)
from ai_client.discussion.state import DiscussionStateStore, DiscussionViews, evidence_ref_for_record
from ai_client.llm.decision import DecisionValidationError, ParsedDiscussionOutput, parse_llm_decision, parse_llm_output
from ai_client.llm.backend import OpenAICompatibleBackend
from ai_client.llm.prompt import build_repair_projection, canonical_prompt_json, project_brain_input
from ai_client.llm.types import DecisionValidationCode, DiscussionChatConfig, LLMBrainConfig, LLMMessage, OpenAICompatibleBackendConfig, PromptProjection, StructuredGenerationRequest
from ai_client.network import AbilityAction, ChatAction, CoDeclareAction, CoReportAction, VoteAction
from ai_client.world import (
    AbilityResultView,
    ChatRecord,
    CoDeclarationRecord,
    CoReportRecord,
    CoView,
    Freshness,
    HistoryRetention,
    HistoryView,
    PhaseView,
    PlayerView,
    PublicNotifyRecord,
    SelfView,
    TransportObservationView,
    UnknownEventRecord,
    WorldSnapshot,
)


def _bound() -> BoundDiscussionContext:
    context = AuthorizedDiscussionContext(
        "aiwolf.discussion-context.v1", "game", "self", "role", (), "a" * 64,
        "team", "count", "attack", "inspect", "medium",
        (CountParityWinCondition("count_parity", "count", "other", "gte"),),
        (), (),
        (AuthorizedChatChannelContext("private", False), AuthorizedChatChannelContext("public", True)),
        False, (), False,
    )
    return BoundDiscussionContext("a" * 64, canonical_sha256(context), context)


def _retention(records: tuple[object, ...]) -> HistoryRetention:
    orders = [item.order for item in records]
    return HistoryRetention(len(records), len(records), 0, 0, None, min(orders) if orders else None, max(orders) if orders else None, 128, 65536, True)


def _request(
    kind: str,
    *,
    peer: bool = False,
    private: bool = False,
    abstain: bool = False,
    records: tuple[object, ...] | None = None,
    action_channel: str | None = None,
    prior_assessment: bool = False,
    prior_evidence: bool = False,
) -> BrainInput:
    channel = "private" if private else "public"
    if records is None:
        records = (
            (ChatRecord(1, 1, "day", channel, "peer", "peer", "source"),)
            if peer
            else ()
        )
    retention = _retention(records)
    last_order = max((item.order for item in records), default=1)
    snapshot = WorldSnapshot(
        1, Freshness.CURRENT, True, last_order,
        (PlayerView("peer", "peer"), PlayerView("self", "self")),
        ("peer", "self"), (), PhaseView("day", 1), SelfView("self", "role", ()), (),
        retention,
    )
    history = HistoryView(records, True, retention)
    co = CoView(
        tuple(item for item in records if isinstance(item, CoDeclarationRecord)),
        tuple(item for item in records if isinstance(item, CoReportRecord)),
        True,
        retention,
    )
    ability_view = AbilityResultView((), True, retention)
    views = DiscussionViews(snapshot, history, co, ability_view, TransportObservationView(1, None, None, False, (), None))
    bound = _bound()
    trigger_record = next(
        (
            item
            for item in reversed(records)
            if isinstance(item, ChatRecord) and item.player_id == "peer"
        ),
        None,
    )
    if peer:
        assert trigger_record is not None
        channel = trigger_record.channel
    source = (
        evidence_ref_for_record(trigger_record, bound_context=bound)
        if trigger_record is not None and peer
        else None
    )
    trigger_kind = {
        "chat": "PEER_CHAT" if peer else "INITIAL_CHAT",
        "vote": "PRE_VOTE",
        "ability": "ABILITY",
        "co_declare": "CO_OPPORTUNITY",
        "co_report": "CO_OPPORTUNITY",
        "none": "INITIAL_CHAT",
    }[kind]
    owner = "vote_ability" if trigger_kind in {"PRE_VOTE", "ABILITY"} else "reaction_chat"
    store = DiscussionStateStore(bound)
    if prior_assessment:
        initial_trigger = DiscussionTrigger(
            "reaction_chat", "INITIAL_CHAT", 1, "day", 1, 1, 0, None
        )
        initial = store.capture(views, initial_trigger)
        seed_evidence = ()
        if prior_evidence:
            assert trigger_record is not None
            seed_evidence = (
                next(
                    event.source
                    for event in initial.evidence
                    if event.source.order == trigger_record.order
                    and event.source.record_kind is EvidenceRecordKind.CHAT
                ),
            )
        seed = DiscussionProposal(
            schema_version="aiwolf.discussion-proposal.v1",
            base_revision=initial.base_revision,
            decision_kind="none",
            option_id=None,
            speech_act=SpeechActNone(SpeechActKind.NONE),
            reaction=None,
            assessment_updates=(AssessmentUpdate("peer", 25, 75, 80, seed_evidence),),
            claim_updates=(),
            relation_updates=(),
            strategy_update=None,
            co_judgment=None,
            pre_vote_reassessment=None,
        )
        request_id = f"phase6:{initial.capture_id}"
        generation = DiscussionGenerationAck(
            capture_id=initial.capture_id,
            request_id=request_id,
            final_attempt_ordinal=1,
            audit_sequence=1,
            generation_record_sha256="b" * 64,
            generation_status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
            context_sha256=initial.context_sha256,
            before_state_sha256=initial.state_sha256,
            after_state_sha256=None,
            proposal_sha256=canonical_sha256(seed),
            durable=True,
        )
        committed = store.commit(store.stage(initial, request_id, seed, generation))
        store.finish_no_action(committed)
    trigger = DiscussionTrigger(
        owner,
        trigger_kind,
        1,
        "day",
        1,
        1,
        source.order if source is not None else 0,
        source,
    )
    capture = store.capture(views, trigger)
    common = dict(connection_generation=1, action_generation=1, phase="day", day=1, type=kind)
    if kind in {"chat", "none"}:
        handle = ChatAction(
            **(common | {"type": "chat"}), channel=action_channel or channel
        )
    elif kind == "vote":
        handle = VoteAction(**common, valid_targets=("peer",), target_count=1, allows_abstain=abstain)
    elif kind == "ability":
        handle = AbilityAction(**common, ability_id="ability", description=None, valid_targets=("peer",), target_count=1, uses_remaining=1)
    elif kind == "co_declare":
        handle = CoDeclareAction(**common, claimed_role_ids=("claim-role",))
    else:
        handle = CoReportAction(**common)
    return BrainInput(
        snapshot,
        BrainActionContext(
            1,
            last_order,
            last_order,
            True,
            (BrainActionOption("action:0", handle),),
        ),
        history,
        co,
        ability_view,
        capture,
    )


def _captured_ref(
    request: BrainInput,
    order: int,
    record_kind: EvidenceRecordKind,
) -> EvidenceRef:
    assert request.discussion is not None
    return next(
        event.source
        for event in request.discussion.evidence
        if event.source.order == order and event.source.record_kind is record_kind
    )


def _ref_json(value: EvidenceRef) -> dict[str, object]:
    return {
        "record_kind": value.record_kind.value,
        "order": value.order,
        "visibility": value.visibility.value,
    }


def _payload(request: BrainInput, kind: str) -> dict[str, object]:
    proposal: dict[str, object] = {
        "schema_version": "aiwolf.discussion-proposal.v1",
        "base_revision": request.discussion.base_revision,
        "decision_kind": kind,
        "option_id": None if kind == "none" else "action:0",
        "speech_act": {"kind": "NONE"},
        "reaction": None,
        "assessment_updates": [],
        "claim_updates": [],
        "relation_updates": [],
        "strategy_update": None,
        "co_judgment": None,
        "pre_vote_reassessment": None,
    }
    if request.discussion.trigger.kind == "PEER_CHAT":
        proposal["reaction"] = {
            "trigger": json.loads(json.dumps(request.discussion.trigger.source, default=lambda value: value.value if hasattr(value, "value") else value.__dict__)),
            "score": 50,
            "reason": "DIRECT_QUESTION",
        }
    if kind == "none":
        decision: dict[str, object] = {"kind": "none"}
    elif kind == "chat":
        decision = {"kind": "chat", "option_id": "action:0", "message": "reply"}
    elif kind == "vote":
        decision = {"kind": "vote", "option_id": "action:0", "target_player_id": "peer"}
        proposal["pre_vote_reassessment"] = {"option_id": "action:0", "ranked_target_player_ids": ["peer"], "preferred_target_player_id": "peer", "evidence": []}
    elif kind == "ability":
        decision = {"kind": "ability", "option_id": "action:0", "target_player_ids": ["peer"]}
    elif kind == "co_declare":
        decision = {"kind": "co_declare", "option_id": "action:0", "claimed_role_id": "claim-role", "comment": "claim"}
        proposal["co_judgment"] = {"decision": "DECLARE", "selected_option_id": "action:0", "claimed_role_id": "claim-role"}
    else:
        decision = {"kind": "co_report", "option_id": "action:0", "report_kind": "x", "target_player_id": "peer", "claimed_result": "x"}
    return {"decision": decision, "discussion": proposal}


def _parse(request: BrainInput, payload: dict[str, object]) -> ParsedDiscussionOutput:
    projection = project_brain_input(request, config=LLMBrainConfig())
    result = parse_llm_output(json.dumps(payload), projection=projection)
    assert isinstance(result, ParsedDiscussionOutput)
    return result


def test_phase6_discussion_profile_prompt_parser_boundary() -> None:
    request = _request("chat")
    profile = DiscussionChatConfig()
    projection = project_brain_input(
        request, config=LLMBrainConfig(short_chat=profile)
    )
    system = projection.messages[0].content
    assert "20" in system and "120" in system
    for text in ("x" * 200, "あ" * 200):
        payload = _payload(request, "chat")
        payload["decision"]["message"] = text
        parsed = parse_llm_output(json.dumps(payload), projection=projection)
        assert parsed.decision.message == text
    for text in ("x" * 201, "あ" * 199 + "😀"):
        payload = _payload(request, "chat")
        payload["decision"]["message"] = text
        with pytest.raises(DecisionValidationError):
            parse_llm_output(json.dumps(payload), projection=projection)


@pytest.mark.parametrize(
    "case",
    [
        "claim",
        "question",
        "answer",
        "rebuttal",
        "opinion_change",
        "relation_hypothesis",
        "assessment_update",
        "claim_update",
        "relation_update",
        "strategy_update",
        "reaction_score",
        "co_judgment",
        "pre_vote_reassessment",
    ],
)
def test_p6b_semantic_pass_authority_closed_positive_matrix(case: str) -> None:
    assert {item.value for item in SpeechActKind} == {
        "NONE",
        "CLAIM",
        "QUESTION",
        "ANSWER",
        "REBUTTAL",
        "OPINION_CHANGE",
        "RELATION_HYPOTHESIS",
    }
    assert {item.value for item in DiscussionTopic} == {
        "ALIGNMENT",
        "ROLE_CLAIM",
        "VOTE",
        "EVENT",
        "RELATION",
        "STRATEGY",
    }
    assert {item.value for item in DiscussionStance} == {
        "SUPPORT",
        "OPPOSE",
        "UNCERTAIN",
    }
    assert {item.value for item in OpinionDimension} == {
        "SUSPICION",
        "CREDIBILITY",
    }
    assert {item.value for item in RelationKind} == {
        "SUPPORTS",
        "CONTRADICTS",
        "DEFENDS",
        "ACCUSES",
        "DISTANCES_FROM",
    }
    assert {item.value for item in ClaimVerdict} == {
        "UNVERIFIED",
        "SUPPORTED",
        "CONTRADICTED",
    }
    assert {item.value for item in ReactionReason} == {
        "DIRECT_QUESTION",
        "DIRECT_MENTION",
        "CLAIM_CONFLICT",
        "VOTE_PRESSURE",
        "NEW_INFORMATION",
        "OTHER_AUTHORIZED",
    }
    assert {item.value for item in CoJudgmentDecision} == {
        "DECLARE",
        "SILENCE",
        "DEFER",
    }
    assert {item.value for item in StrategyMode} == {
        "GATHER_INFORMATION",
        "TEST_CLAIM",
        "RESOLVE_CONTRADICTION",
        "BUILD_CONSENSUS",
        "PROTECT_PRIVATE_INFORMATION",
        "PREPARE_VOTE",
        "USE_OFFERED_CAPABILITY",
        "WAIT",
    }
    public_chat = ChatRecord(1, 1, "day", "public", "peer", "peer", "source")

    if case == "claim":
        request = _request("ability", records=(public_chat,))
        ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
        payload = _payload(request, "ability")
        payload["discussion"]["speech_act"] = {
            "kind": "CLAIM",
            "subject_player_id": "peer",
            "topic": "ALIGNMENT",
            "stance": "OPPOSE",
            "evidence": [_ref_json(ref)],
        }
        expected = SpeechActClaim(
            SpeechActKind.CLAIM,
            "peer",
            DiscussionTopic.ALIGNMENT,
            DiscussionStance.OPPOSE,
            (ref,),
        )
        result = _parse(request, payload)
        assert result.proposal.speech_act == expected
        expected_identity = ("ability", "action:0")
    elif case == "question":
        request = _request("chat", records=(public_chat,))
        ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
        payload = _payload(request, "chat")
        payload["discussion"]["speech_act"] = {
            "kind": "QUESTION",
            "addressee_player_id": "peer",
            "subject_player_id": "self",
            "topic": "STRATEGY",
            "source": _ref_json(ref),
        }
        expected = SpeechActQuestion(
            SpeechActKind.QUESTION,
            "peer",
            "self",
            DiscussionTopic.STRATEGY,
            ref,
        )
        result = _parse(request, payload)
        assert result.proposal.speech_act == expected
        nullable = _payload(request, "chat")
        nullable["discussion"]["speech_act"] = {
            "kind": "QUESTION",
            "addressee_player_id": "peer",
            "subject_player_id": None,
            "topic": "STRATEGY",
            "source": None,
        }
        assert _parse(request, nullable).proposal.speech_act == SpeechActQuestion(
            SpeechActKind.QUESTION,
            "peer",
            None,
            DiscussionTopic.STRATEGY,
            None,
        )
        expected_identity = ("chat", "action:0")
    elif case == "answer":
        request = _request("chat", peer=True)
        ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
        payload = _payload(request, "chat")
        payload["discussion"]["speech_act"] = {
            "kind": "ANSWER",
            "addressee_player_id": "peer",
            "in_reply_to": _ref_json(ref),
            "source_interpretation": "QUESTION",
            "topic": "VOTE",
            "stance": "UNCERTAIN",
            "evidence": [_ref_json(ref)],
        }
        expected = SpeechActAnswer(
            SpeechActKind.ANSWER,
            "peer",
            ref,
            "QUESTION",
            DiscussionTopic.VOTE,
            DiscussionStance.UNCERTAIN,
            (ref,),
        )
        result = _parse(request, payload)
        assert result.proposal.speech_act == expected
        expected_identity = ("chat", "action:0")
    elif case == "rebuttal":
        records = (
            CoDeclarationRecord(1, 1, "day", "peer", "claim-role", "claim"),
            ChatRecord(2, 1, "day", "public", "peer", "peer", "trigger"),
        )
        request = _request("chat", peer=True, records=records)
        ref = _captured_ref(request, 1, EvidenceRecordKind.CO_DECLARATION)
        payload = _payload(request, "chat")
        payload["discussion"]["speech_act"] = {
            "kind": "REBUTTAL",
            "addressee_player_id": "peer",
            "in_reply_to": _ref_json(ref),
            "source_interpretation": "CLAIM",
            "topic": "ROLE_CLAIM",
            "stance": "OPPOSE",
            "evidence": [_ref_json(ref)],
        }
        expected = SpeechActRebuttal(
            SpeechActKind.REBUTTAL,
            "peer",
            ref,
            "CLAIM",
            DiscussionTopic.ROLE_CLAIM,
            DiscussionStance.OPPOSE,
            (ref,),
        )
        result = _parse(request, payload)
        assert result.proposal.speech_act == expected
        expected_identity = ("chat", "action:0")
    elif case == "opinion_change":
        request = _request("chat", peer=True, prior_assessment=True)
        ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
        payload = _payload(request, "chat")
        payload["discussion"]["speech_act"] = {
            "kind": "OPINION_CHANGE",
            "subject_player_id": "peer",
            "dimension": "SUSPICION",
            "prior": 25,
            "current": 70,
            "causes": [_ref_json(ref)],
        }
        expected = SpeechActOpinionChange(
            SpeechActKind.OPINION_CHANGE,
            "peer",
            OpinionDimension.SUSPICION,
            25,
            70,
            (ref,),
        )
        result = _parse(request, payload)
        assert result.proposal.speech_act == expected
        expected_identity = ("chat", "action:0")
    elif case == "relation_hypothesis":
        request = _request("chat", peer=True)
        ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
        payload = _payload(request, "chat")
        payload["discussion"]["speech_act"] = {
            "kind": "RELATION_HYPOTHESIS",
            "source_player_id": "self",
            "target_player_id": "peer",
            "relation": "SUPPORTS",
            "confidence": 64,
            "evidence": [_ref_json(ref)],
        }
        expected = SpeechActRelationHypothesis(
            SpeechActKind.RELATION_HYPOTHESIS,
            "self",
            "peer",
            RelationKind.SUPPORTS,
            64,
            (ref,),
        )
        result = _parse(request, payload)
        assert result.proposal.speech_act == expected
        expected_identity = ("chat", "action:0")
    elif case == "assessment_update":
        request = _request("none", records=(public_chat,))
        ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
        payload = _payload(request, "none")
        payload["discussion"]["assessment_updates"] = [
            {
                "target_player_id": "peer",
                "suspicion": 61,
                "credibility": 39,
                "confidence": 80,
                "evidence": [_ref_json(ref)],
            }
        ]
        result = _parse(request, payload)
        assert result.proposal.assessment_updates == (
            AssessmentUpdate("peer", 61, 39, 80, (ref,)),
        )
        expected_identity = ("none", None)
    elif case == "claim_update":
        records = (
            CoReportRecord(1, 1, "day", "peer", "inspect", "self", "claim"),
            ChatRecord(2, 1, "day", "public", "peer", "peer", "trigger"),
        )
        request = _request("chat", peer=True, records=records)
        ref = _captured_ref(request, 1, EvidenceRecordKind.CO_REPORT)
        payload = _payload(request, "chat")
        payload["discussion"]["claim_updates"] = [
            {
                "claim": _ref_json(ref),
                "speaker_player_id": "peer",
                "verdict": "CONTRADICTED",
                "confidence": 77,
                "evidence": [_ref_json(ref)],
            }
        ]
        result = _parse(request, payload)
        assert result.proposal.claim_updates == (
            ClaimUpdate(ref, "peer", ClaimVerdict.CONTRADICTED, 77, (ref,)),
        )
        expected_identity = ("chat", "action:0")
    elif case == "relation_update":
        request = _request("chat", peer=True)
        ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
        payload = _payload(request, "chat")
        payload["discussion"]["relation_updates"] = [
            {
                "source_player_id": "self",
                "target_player_id": "peer",
                "relation": "ACCUSES",
                "confidence": 66,
                "evidence": [_ref_json(ref)],
                "provenance": "PUBLIC_INFERENCE",
            }
        ]
        result = _parse(request, payload)
        assert result.proposal.relation_updates == (
            RelationUpdate(
                "self", "peer", RelationKind.ACCUSES, 66, (ref,), "PUBLIC_INFERENCE"
            ),
        )
        expected_identity = ("chat", "action:0")
    elif case == "strategy_update":
        request = _request("ability", records=(public_chat,))
        ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
        payload = _payload(request, "ability")
        payload["discussion"]["strategy_update"] = {
            "scope": "PHASE",
            "mode": "TEST_CLAIM",
            "focus_player_ids": ["peer"],
            "evidence": [_ref_json(ref)],
        }
        result = _parse(request, payload)
        assert result.proposal.strategy_update == StrategyUpdate(
            "PHASE", StrategyMode.TEST_CLAIM, ("peer",), (ref,)
        )
        expected_identity = ("ability", "action:0")
    elif case == "reaction_score":
        request = _request("chat", peer=True, private=True)
        ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
        payload = _payload(request, "chat")
        result = _parse(request, payload)
        assert result.proposal.reaction == ReactionAssessment(
            ref, 50, ReactionReason.DIRECT_QUESTION
        )
        expected_identity = ("chat", "action:0")
    elif case == "co_judgment":
        request = _request("co_declare", records=(public_chat,))
        payload = _payload(request, "co_declare")
        result = _parse(request, payload)
        assert result.proposal.co_judgment == CoJudgment(
            CoJudgmentDecision.DECLARE, "action:0", "claim-role"
        )
        expected_identity = ("co_declare", "action:0")
    else:
        assert case == "pre_vote_reassessment"
        request = _request("vote", records=(public_chat,))
        ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
        payload = _payload(request, "vote")
        payload["discussion"]["pre_vote_reassessment"]["evidence"] = [
            _ref_json(ref)
        ]
        result = _parse(request, payload)
        assert result.proposal.pre_vote_reassessment == PreVoteReassessment(
            "action:0", ("peer",), "peer", (ref,)
        )
        expected_identity = ("vote", "action:0")

    assert request.discussion is not None
    assert result.proposal.base_revision == request.discussion.base_revision
    assert (result.proposal.decision_kind, result.proposal.option_id) == expected_identity
    expected_decision_types = {
        "none": NoDecision,
        "chat": ChatDecision,
        "vote": VoteDecision,
        "ability": AbilityDecision,
        "co_declare": CoDeclareDecision,
    }
    expected_kind, expected_option = expected_identity
    assert type(result.decision) is expected_decision_types[expected_kind]
    assert getattr(result.decision, "option_id", None) == expected_option


def test_p6b_all_five_decision_proposal_offered_handle_shapes_parse_atomically() -> None:
    for kind in ("none", "chat", "vote", "ability", "co_declare"):
        request = _request(kind)
        result = _parse(request, _payload(request, kind))
        expected_option = None if kind == "none" else "action:0"
        assert (result.proposal.decision_kind, result.proposal.option_id) == (
            kind,
            expected_option,
        )
        assert getattr(result.decision, "option_id", None) == expected_option


def test_p6b_sent_schema_requires_chat_reaction_trigger() -> None:
    peer_request = _request("chat", peer=True)
    peer_projection = project_brain_input(peer_request, config=LLMBrainConfig())
    peer_validator = Draft202012Validator(peer_projection.decision_schema)

    for kind in ("none", "chat", "vote", "ability", "co_declare"):
        request = _request(kind, peer=kind == "chat")
        projection = project_brain_input(request, config=LLMBrainConfig())
        Draft202012Validator(projection.decision_schema).validate(_payload(request, kind))

    non_chat_trigger = _payload(peer_request, "chat")
    non_chat_trigger["discussion"]["reaction"]["trigger"]["record_kind"] = (
        EvidenceRecordKind.PUBLIC_NOTIFY.value
    )
    assert not peer_validator.is_valid(non_chat_trigger)

    missing_reaction = _payload(peer_request, "chat")
    missing_reaction["discussion"]["reaction"] = None
    assert not peer_validator.is_valid(missing_reaction)

    wrong_reaction = _payload(peer_request, "chat")
    wrong_reaction["discussion"]["reaction"]["trigger"]["order"] = 999
    assert not peer_validator.is_valid(wrong_reaction)

    initial_request = _request("chat")
    initial_payload = _payload(initial_request, "chat")
    initial_payload["discussion"]["reaction"] = _payload(
        peer_request, "chat"
    )["discussion"]["reaction"]
    assert not Draft202012Validator(
        project_brain_input(initial_request, config=LLMBrainConfig()).decision_schema
    ).is_valid(initial_payload)

    co_request = _request("co_declare")
    missing_co = _payload(co_request, "co_declare")
    missing_co["discussion"]["co_judgment"] = None
    assert not Draft202012Validator(
        project_brain_input(co_request, config=LLMBrainConfig()).decision_schema
    ).is_valid(missing_co)

    pre_vote_request = _request("vote")
    missing_pre_vote = _payload(pre_vote_request, "vote")
    missing_pre_vote["discussion"]["pre_vote_reassessment"] = None
    assert not Draft202012Validator(
        project_brain_input(pre_vote_request, config=LLMBrainConfig()).decision_schema
    ).is_valid(missing_pre_vote)

    action_without_option = _payload(peer_request, "chat")
    action_without_option["discussion"]["option_id"] = None
    assert peer_validator.is_valid(action_without_option)
    with pytest.raises(DecisionValidationError):
        _parse(peer_request, action_without_option)


def test_p6b_projection_schema_is_the_backend_response_format_schema() -> None:
    projection = project_brain_input(
        _request("chat", peer=True), config=LLMBrainConfig()
    )
    backend = OpenAICompatibleBackend(
        OpenAICompatibleBackendConfig(
            endpoint="http://127.0.0.1:1/v1/chat/completions",
            model="opaque-model",
        ),
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
    )
    payload = backend._request_payload(
        StructuredGenerationRequest(
            request_id="opaque-request",
            messages=projection.messages,
            output_schema=projection.decision_schema,
        )
    )
    asyncio.run(backend.aclose())
    body = json.loads(payload)
    assert (
        body["response_format"]["json_schema"]["schema"]
        == json.loads(canonical_json_bytes(projection.decision_schema))
    )


@pytest.mark.parametrize(
    ("kind", "peer"),
    (("none", False), ("chat", False), ("chat", True), ("vote", False), ("ability", False), ("co_declare", False)),
)
def test_p6b_every_trigger_family_producer_allows_reserved_empty_repair(
    kind: str, peer: bool
) -> None:
    projection = project_brain_input(
        _request(kind, peer=peer), config=LLMBrainConfig()
    )
    repaired = build_repair_projection(
        projection,
        validation_code=DecisionValidationCode.SCHEMA,
        invalid_output="",
        config=LLMBrainConfig(),
    )
    assert projection.token_proxy_units <= 8_192
    assert repaired.token_proxy_units <= 8_192
    assert projection.prompt_bytes <= 32_768
    assert repaired.prompt_bytes <= 32_768

def test_p6b_closed_keys_types_ranges_duplicates_and_unknown_references_fail() -> None:
    request = _request("chat", peer=True)
    ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
    raw_ref = _ref_json(ref)
    invalid: list[dict[str, object]] = []

    payload = _payload(request, "chat")
    payload["extra"] = True
    invalid.append(payload)

    payload = _payload(request, "chat")
    del payload["discussion"]["speech_act"]
    invalid.append(payload)

    payload = _payload(request, "chat")
    payload["discussion"]["speech_act"] = {"kind": "UNKNOWN"}
    invalid.append(payload)

    payload = _payload(request, "chat")
    payload["discussion"]["speech_act"] = {
        "kind": "ANSWER",
        "addressee_player_id": "peer",
        "in_reply_to": raw_ref,
        "source_interpretation": "CLAIM",
        "topic": "ALIGNMENT",
        "stance": "SUPPORT",
        "evidence": [raw_ref],
    }
    invalid.append(payload)

    for bad_score in (True, 1.5, -1, 101):
        payload = _payload(request, "chat")
        payload["discussion"]["assessment_updates"] = [
            {
                "target_player_id": "peer",
                "suspicion": bad_score,
                "credibility": 1,
                "confidence": 1,
                "evidence": [],
            }
        ]
        invalid.append(payload)

    payload = _payload(request, "chat")
    update = {
        "target_player_id": "peer",
        "suspicion": 1,
        "credibility": 1,
        "confidence": 1,
        "evidence": [],
    }
    payload["discussion"]["assessment_updates"] = [update, dict(update)]
    invalid.append(payload)

    payload = _payload(request, "chat")
    payload["discussion"]["assessment_updates"] = [dict(update) for _ in range(5)]
    invalid.append(payload)

    payload = _payload(request, "chat")
    claim = {
        "claim": raw_ref,
        "speaker_player_id": "peer",
        "verdict": "UNVERIFIED",
        "confidence": 1,
        "evidence": [raw_ref],
    }
    payload["discussion"]["claim_updates"] = [claim, dict(claim)]
    invalid.append(payload)

    payload = _payload(request, "chat")
    payload["discussion"]["claim_updates"] = [dict(claim) for _ in range(5)]
    invalid.append(payload)

    payload = _payload(request, "chat")
    relation = {
        "source_player_id": "self",
        "target_player_id": "peer",
        "relation": "SUPPORTS",
        "confidence": 1,
        "evidence": [raw_ref],
        "provenance": "PUBLIC_INFERENCE",
    }
    payload["discussion"]["relation_updates"] = [relation, dict(relation)]
    invalid.append(payload)

    payload = _payload(request, "chat")
    payload["discussion"]["relation_updates"] = [dict(relation) for _ in range(3)]
    invalid.append(payload)

    payload = _payload(request, "chat")
    payload["discussion"]["speech_act"] = {
        "kind": "CLAIM",
        "subject_player_id": "peer",
        "topic": "ALIGNMENT",
        "stance": "SUPPORT",
        "evidence": [raw_ref, raw_ref],
    }
    invalid.append(payload)

    payload = _payload(request, "chat")
    payload["discussion"]["strategy_update"] = {
        "scope": "GAME",
        "mode": "WAIT",
        "focus_player_ids": ["peer", "peer"],
        "evidence": [],
    }
    invalid.append(payload)

    payload = _payload(request, "chat")
    payload["discussion"]["strategy_update"] = {
        "scope": "GAME",
        "mode": "WAIT",
        "focus_player_ids": ["peer"] * 5,
        "evidence": [],
    }
    invalid.append(payload)

    payload = _payload(request, "chat")
    payload["discussion"]["assessment_updates"] = [
        {
            "target_player_id": "unknown-player",
            "suspicion": 1,
            "credibility": 1,
            "confidence": 1,
            "evidence": [],
        }
    ]
    invalid.append(payload)

    payload = _payload(request, "chat")
    payload["discussion"]["reaction"]["extra"] = True
    invalid.append(payload)

    for candidate in invalid:
        with pytest.raises(DecisionValidationError):
            _parse(request, candidate)

    records = tuple(
        ChatRecord(order, 1, "day", "public", "peer", "peer", f"source-{order}")
        for order in range(1, 10)
    )
    request = _request("ability", records=records)
    refs = [
        _ref_json(_captured_ref(request, order, EvidenceRecordKind.CHAT))
        for order in range(1, 10)
    ]
    payload = _payload(request, "ability")
    payload["discussion"]["speech_act"] = {
        "kind": "CLAIM",
        "subject_player_id": "peer",
        "topic": "EVENT",
        "stance": "UNCERTAIN",
        "evidence": refs,
    }
    with pytest.raises(DecisionValidationError):
        _parse(request, payload)

    ordered_request = _request(
        "chat",
        peer=True,
        records=(
            ChatRecord(1, 1, "day", "public", "peer", "peer", "earlier"),
            ChatRecord(2, 1, "day", "public", "peer", "peer", "trigger"),
        ),
    )
    earlier = _captured_ref(ordered_request, 1, EvidenceRecordKind.CHAT)
    later = _captured_ref(ordered_request, 2, EvidenceRecordKind.CHAT)
    payload = _payload(ordered_request, "chat")
    payload["discussion"]["assessment_updates"] = [
        {
            "target_player_id": "peer",
            "suspicion": 1,
            "credibility": 1,
            "confidence": 1,
            "evidence": [_ref_json(later), _ref_json(earlier)],
        }
    ]
    with pytest.raises(DecisionValidationError):
        _parse(ordered_request, payload)


def test_p6b_visibility_matrix_rejects_upgrade_downgrade_and_public_inference_misuse() -> None:
    private_request = _request("chat", peer=True, private=True)
    private_ref = _captured_ref(private_request, 1, EvidenceRecordKind.CHAT)
    assert private_ref.visibility is EvidenceVisibility.AUTHORIZED_PRIVATE
    payload = _payload(private_request, "chat")
    payload["discussion"]["assessment_updates"] = [
        {
            "target_player_id": "peer",
            "suspicion": 1,
            "credibility": 1,
            "confidence": 1,
            "evidence": [_ref_json(private_ref)],
        }
    ]
    parsed = _parse(private_request, payload)
    assert parsed.proposal.assessment_updates == (
        AssessmentUpdate("peer", 1, 1, 1, (private_ref,)),
    )

    payload = _payload(private_request, "chat")
    payload["discussion"]["strategy_update"] = {
        "scope": "GAME",
        "mode": "PROTECT_PRIVATE_INFORMATION",
        "focus_player_ids": ["peer"],
        "evidence": [_ref_json(private_ref)],
    }
    assert _parse(private_request, payload).proposal.strategy_update == StrategyUpdate(
        "GAME",
        StrategyMode.PROTECT_PRIVATE_INFORMATION,
        ("peer",),
        (private_ref,),
    )

    upgraded = _ref_json(private_ref)
    upgraded["visibility"] = "PUBLIC"
    payload = _payload(private_request, "chat")
    payload["discussion"]["assessment_updates"] = [
        {
            "target_player_id": "peer",
            "suspicion": 1,
            "credibility": 1,
            "confidence": 1,
            "evidence": [upgraded],
        }
    ]
    with pytest.raises(DecisionValidationError):
        _parse(private_request, payload)

    payload = _payload(private_request, "chat")
    payload["discussion"]["relation_updates"] = [
        {
            "source_player_id": "self",
            "target_player_id": "peer",
            "relation": "ACCUSES",
            "confidence": 1,
            "evidence": [_ref_json(private_ref)],
            "provenance": "PUBLIC_INFERENCE",
        }
    ]
    with pytest.raises(DecisionValidationError):
        _parse(private_request, payload)

    payload = _payload(private_request, "chat")
    payload["discussion"]["claim_updates"] = [
        {
            "claim": _ref_json(private_ref),
            "speaker_player_id": "peer",
            "verdict": "UNVERIFIED",
            "confidence": 1,
            "evidence": [],
        }
    ]
    with pytest.raises(DecisionValidationError):
        _parse(private_request, payload)

    public_request = _request("chat", peer=True)
    public_ref = _captured_ref(public_request, 1, EvidenceRecordKind.CHAT)
    downgraded = _ref_json(public_ref)
    downgraded["visibility"] = "AUTHORIZED_PRIVATE"
    payload = _payload(public_request, "chat")
    payload["discussion"]["assessment_updates"] = [
        {
            "target_player_id": "peer",
            "suspicion": 1,
            "credibility": 1,
            "confidence": 1,
            "evidence": [downgraded],
        }
    ]
    with pytest.raises(DecisionValidationError):
        _parse(public_request, payload)

    unknown_visibility = _ref_json(public_ref)
    unknown_visibility["visibility"] = "UNKNOWN_VISIBILITY"
    payload = _payload(public_request, "chat")
    payload["discussion"]["assessment_updates"] = [
        {
            "target_player_id": "peer",
            "suspicion": 1,
            "credibility": 1,
            "confidence": 1,
            "evidence": [unknown_visibility],
        }
    ]
    with pytest.raises(DecisionValidationError):
        _parse(public_request, payload)

    lost_records = (
        UnknownEventRecord(1, "private-looking"),
        ChatRecord(2, 1, "day", "public", "peer", "peer", "trigger"),
    )
    lost_request = _request("chat", peer=True, records=lost_records)
    lost_ref = _captured_ref(lost_request, 1, EvidenceRecordKind.UNKNOWN)
    assert lost_ref.visibility is EvidenceVisibility.VISIBILITY_LOST
    payload = _payload(lost_request, "chat")
    payload["discussion"]["relation_updates"] = [
        {
            "source_player_id": "self",
            "target_player_id": "peer",
            "relation": "DISTANCES_FROM",
            "confidence": 1,
            "evidence": [_ref_json(lost_ref)],
            "provenance": "PUBLIC_INFERENCE",
        }
    ]
    with pytest.raises(DecisionValidationError):
        _parse(lost_request, payload)

    mixed_records = (
        CoDeclarationRecord(1, 1, "day", "peer", "claim-role", "claim"),
        ChatRecord(2, 1, "day", "private", "peer", "peer", "trigger"),
    )
    mixed_request = _request("chat", peer=True, records=mixed_records)
    claim_ref = _captured_ref(mixed_request, 1, EvidenceRecordKind.CO_DECLARATION)
    private_ref = _captured_ref(mixed_request, 2, EvidenceRecordKind.CHAT)
    payload = _payload(mixed_request, "chat")
    payload["discussion"]["claim_updates"] = [
        {
            "claim": _ref_json(claim_ref),
            "speaker_player_id": "peer",
            "verdict": "SUPPORTED",
            "confidence": 1,
            "evidence": [_ref_json(private_ref)],
        }
    ]
    with pytest.raises(DecisionValidationError):
        _parse(mixed_request, payload)


def test_p6b_peer_actor_addressee_and_claim_speaker_binding_is_mechanical() -> None:
    request = _request("chat", peer=True, private=True)
    ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
    payload = _payload(request, "chat")
    payload["discussion"]["speech_act"] = {
        "kind": "ANSWER",
        "addressee_player_id": "peer",
        "in_reply_to": _ref_json(ref),
        "source_interpretation": "QUESTION",
        "topic": "ALIGNMENT",
        "stance": "UNCERTAIN",
        "evidence": [_ref_json(ref)],
    }
    assert _parse(request, payload).proposal.speech_act == SpeechActAnswer(
        SpeechActKind.ANSWER,
        "peer",
        ref,
        "QUESTION",
        DiscussionTopic.ALIGNMENT,
        DiscussionStance.UNCERTAIN,
        (ref,),
    )

    payload["discussion"]["speech_act"]["addressee_player_id"] = "self"
    with pytest.raises(DecisionValidationError):
        _parse(request, payload)

    typed_co_records = (
        CoDeclarationRecord(1, 1, "day", "peer", "claim-role", "claim"),
        ChatRecord(2, 1, "day", "public", "peer", "peer", "trigger"),
    )
    request = _request("chat", peer=True, records=typed_co_records)
    co_ref = _captured_ref(request, 1, EvidenceRecordKind.CO_DECLARATION)
    payload = _payload(request, "chat")
    payload["discussion"]["speech_act"] = {
        "kind": "REBUTTAL",
        "addressee_player_id": "peer",
        "in_reply_to": _ref_json(co_ref),
        "source_interpretation": "CLAIM",
        "topic": "ROLE_CLAIM",
        "stance": "OPPOSE",
        "evidence": [_ref_json(co_ref)],
    }
    assert _parse(request, payload).proposal.speech_act.in_reply_to == co_ref

    payload = _payload(request, "chat")
    payload["discussion"]["claim_updates"] = [
        {
            "claim": _ref_json(co_ref),
            "speaker_player_id": "self",
            "verdict": "UNVERIFIED",
            "confidence": 1,
            "evidence": [_ref_json(co_ref)],
        }
    ]
    with pytest.raises(DecisionValidationError):
        _parse(request, payload)

    actor_cases = (
        ChatRecord(1, 1, "day", "public", None, None, "missing actor"),
        ChatRecord(1, 1, "day", "public", "self", "self", "self actor"),
        PublicNotifyRecord(1, 1, "day", "system actor"),
    )
    for source_record in actor_cases:
        records = (
            source_record,
            ChatRecord(2, 1, "day", "public", "peer", "peer", "trigger"),
        )
        actor_request = _request("chat", peer=True, records=records)
        source_ref = next(
            event.source
            for event in actor_request.discussion.evidence
            if event.source.order == 1
        )
        payload = _payload(actor_request, "chat")
        payload["discussion"]["speech_act"] = {
            "kind": "ANSWER",
            "addressee_player_id": "peer",
            "in_reply_to": _ref_json(source_ref),
            "source_interpretation": "QUESTION",
            "topic": "EVENT",
            "stance": "UNCERTAIN",
            "evidence": [_ref_json(source_ref)],
        }
        with pytest.raises(DecisionValidationError):
            _parse(actor_request, payload)

    wrong_channel = _request(
        "chat", peer=True, private=True, action_channel="public"
    )
    payload = _payload(wrong_channel, "chat")
    with pytest.raises(DecisionValidationError):
        _parse(wrong_channel, payload)


def test_p6b_contextful_co_report_rejected_context_free_raw_compatibility_retained() -> None:
    request = _request("co_report")
    projection = project_brain_input(request, config=LLMBrainConfig())
    with pytest.raises(DecisionValidationError):
        parse_llm_output(json.dumps(_payload(request, "co_report")), projection=projection)
    legacy = replace(request, discussion=None)
    legacy_projection = project_brain_input(legacy, config=LLMBrainConfig())
    raw = '{"kind":"none"}'
    assert parse_llm_decision(raw, projection=legacy_projection).__class__.__name__ == "NoDecision"


def test_p6b_identity_nullability_option_handle_family_and_base_revision_mutations_fail() -> None:
    request = _request("chat")
    for mutate in ("kind", "option", "revision"):
        payload = _payload(request, "chat")
        if mutate == "kind":
            payload["discussion"]["decision_kind"] = "vote"
        elif mutate == "option":
            payload["discussion"]["option_id"] = None
        else:
            payload["discussion"]["base_revision"] += 1
        with pytest.raises(DecisionValidationError):
            _parse(request, payload)

    payload = _payload(request, "chat")
    payload["decision"]["option_id"] = "not-offered"
    payload["discussion"]["option_id"] = "not-offered"
    with pytest.raises(DecisionValidationError):
        _parse(request, payload)

    payload = _payload(request, "vote")
    with pytest.raises(DecisionValidationError):
        _parse(request, payload)

    vote_request = _request("vote")
    payload = _payload(vote_request, "vote")
    payload["decision"]["target_player_id"] = "self"
    payload["discussion"]["pre_vote_reassessment"] = {
        "option_id": "action:0",
        "ranked_target_player_ids": ["self"],
        "preferred_target_player_id": "self",
        "evidence": [],
    }
    with pytest.raises(DecisionValidationError):
        _parse(vote_request, payload)

    ability_request = _request("ability")
    payload = _payload(ability_request, "ability")
    payload["decision"]["target_player_ids"] = ["self"]
    with pytest.raises(DecisionValidationError):
        _parse(ability_request, payload)

    co_request = _request("co_declare")
    payload = _payload(co_request, "co_declare")
    payload["decision"]["claimed_role_id"] = "not-offered"
    payload["discussion"]["co_judgment"]["claimed_role_id"] = "not-offered"
    with pytest.raises(DecisionValidationError):
        _parse(co_request, payload)


def test_p6b_opinion_change_requires_exact_prior_and_new_evidence() -> None:
    request = _request("chat", peer=True)
    ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
    payload = _payload(request, "chat")
    payload["discussion"]["speech_act"] = {
        "kind": "OPINION_CHANGE",
        "subject_player_id": "peer",
        "dimension": "SUSPICION",
        "prior": 25,
        "current": 70,
        "causes": [_ref_json(ref)],
    }
    with pytest.raises(DecisionValidationError):
        _parse(request, payload)

    request = _request("chat", peer=True, prior_assessment=True)
    ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
    for prior, current, causes in (
        (24, 70, [_ref_json(ref)]),
        (25, 25, [_ref_json(ref)]),
        (25, 70, []),
    ):
        payload = _payload(request, "chat")
        payload["discussion"]["speech_act"] = {
            "kind": "OPINION_CHANGE",
            "subject_player_id": "peer",
            "dimension": "SUSPICION",
            "prior": prior,
            "current": current,
            "causes": causes,
        }
        with pytest.raises(DecisionValidationError):
            _parse(request, payload)

    request = _request(
        "chat", peer=True, prior_assessment=True, prior_evidence=True
    )
    ref = _captured_ref(request, 1, EvidenceRecordKind.CHAT)
    payload = _payload(request, "chat")
    payload["discussion"]["speech_act"] = {
        "kind": "OPINION_CHANGE",
        "subject_player_id": "peer",
        "dimension": "SUSPICION",
        "prior": 25,
        "current": 70,
        "causes": [_ref_json(ref)],
    }
    with pytest.raises(DecisionValidationError):
        _parse(request, payload)


def test_p6b_trigger_specific_reaction_co_and_pre_vote_semantics_are_exact() -> None:
    peer_request = _request(
        "chat",
        peer=True,
        records=(
            ChatRecord(1, 1, "day", "public", "peer", "peer", "earlier"),
            ChatRecord(2, 1, "day", "public", "peer", "peer", "trigger"),
        ),
    )
    payload = _payload(peer_request, "chat")
    payload["discussion"]["reaction"] = None
    with pytest.raises(DecisionValidationError):
        _parse(peer_request, payload)

    wrong_trigger = _captured_ref(peer_request, 1, EvidenceRecordKind.CHAT)
    payload = _payload(peer_request, "chat")
    payload["discussion"]["reaction"]["trigger"] = _ref_json(wrong_trigger)
    with pytest.raises(DecisionValidationError):
        _parse(peer_request, payload)

    for field, invalid in (("score", True), ("score", 101), ("reason", "UNKNOWN")):
        payload = _payload(peer_request, "chat")
        payload["discussion"]["reaction"][field] = invalid
        with pytest.raises(DecisionValidationError):
            _parse(peer_request, payload)

    initial_request = _request(
        "chat",
        records=(ChatRecord(1, 1, "day", "public", "peer", "peer", "history"),),
    )
    initial_ref = _captured_ref(initial_request, 1, EvidenceRecordKind.CHAT)
    payload = _payload(initial_request, "chat")
    payload["discussion"]["reaction"] = {
        "trigger": _ref_json(initial_ref),
        "score": 50,
        "reason": "DIRECT_MENTION",
    }
    with pytest.raises(DecisionValidationError):
        _parse(initial_request, payload)

    co_request = _request("co_declare")
    payload = _payload(co_request, "co_declare")
    payload["discussion"]["co_judgment"] = None
    with pytest.raises(DecisionValidationError):
        _parse(co_request, payload)

    payload = _payload(co_request, "co_declare")
    payload["discussion"]["co_judgment"] = {
        "decision": "SILENCE",
        "selected_option_id": None,
        "claimed_role_id": None,
    }
    with pytest.raises(DecisionValidationError):
        _parse(co_request, payload)

    for decision in (CoJudgmentDecision.SILENCE, CoJudgmentDecision.DEFER):
        silent_payload = _payload(co_request, "none")
        silent_payload["discussion"]["co_judgment"] = {
            "decision": decision.value,
            "selected_option_id": None,
            "claimed_role_id": None,
        }
        assert _parse(co_request, silent_payload).proposal.co_judgment == CoJudgment(
            decision, None, None
        )

    payload = _payload(co_request, "co_declare")
    payload["discussion"]["co_judgment"]["selected_option_id"] = "not-offered"
    with pytest.raises(DecisionValidationError):
        _parse(co_request, payload)

    vote_request = _request("vote")
    payload = _payload(vote_request, "vote")
    payload["discussion"]["pre_vote_reassessment"] = None
    with pytest.raises(DecisionValidationError):
        _parse(vote_request, payload)

    payload = _payload(vote_request, "vote")
    payload["discussion"]["pre_vote_reassessment"]["option_id"] = "not-offered"
    with pytest.raises(DecisionValidationError):
        _parse(vote_request, payload)

    payload = _payload(vote_request, "vote")
    payload["discussion"]["pre_vote_reassessment"]["ranked_target_player_ids"] = [
        "peer",
        "peer",
    ]
    with pytest.raises(DecisionValidationError):
        _parse(vote_request, payload)

    payload = _payload(vote_request, "vote")
    payload["discussion"]["pre_vote_reassessment"][
        "preferred_target_player_id"
    ] = None
    with pytest.raises(DecisionValidationError):
        _parse(vote_request, payload)

    non_abstain = _payload(vote_request, "none")
    non_abstain["discussion"]["pre_vote_reassessment"] = {
        "option_id": "action:0",
        "ranked_target_player_ids": ["peer"],
        "preferred_target_player_id": None,
        "evidence": [],
    }
    with pytest.raises(DecisionValidationError):
        _parse(vote_request, non_abstain)

    abstain_request = _request("vote", abstain=True)
    abstain_payload = _payload(abstain_request, "none")
    abstain_payload["discussion"]["pre_vote_reassessment"] = {
        "option_id": "action:0",
        "ranked_target_player_ids": ["peer"],
        "preferred_target_player_id": None,
        "evidence": [],
    }
    assert _parse(
        abstain_request, abstain_payload
    ).proposal.pre_vote_reassessment == PreVoteReassessment(
        "action:0", ("peer",), None, ()
    )


def test_p6b_uncaptured_or_unprojected_reference_fails_before_stage() -> None:
    request = _request("chat", peer=True)
    payload = _payload(request, "chat")
    payload["discussion"]["assessment_updates"] = [{"target_player_id": "peer", "suspicion": 1, "credibility": 1, "confidence": 1, "evidence": [{"record_kind": "chat", "order": 2, "visibility": "PUBLIC"}]}]
    with pytest.raises(DecisionValidationError):
        _parse(request, payload)


def test_p6b_canonical_input_cannot_cite_evidence_absent_from_emitted_user_json() -> None:
    request = _request("chat", peer=True)
    projection = project_brain_input(request, config=LLMBrainConfig())
    emitted_input = json.loads(projection.messages[1].content)
    assert emitted_input["memory"]["records"]
    emitted_input["memory"]["records"] = []
    emitted_input["memory"]["included_records"] = 0
    messages = (
        projection.messages[0],
        LLMMessage(
            "user",
            json.dumps(
                emitted_input,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    )
    encoded = canonical_prompt_json(messages, projection.decision_schema).encode("utf-8")

    # Recomputing the complete prompt digest cannot make a divergent semantic
    # authority map valid: the immutable projection binds that map to the
    # actual canonical user JSON before parse/semantic validation can run.
    with pytest.raises(ValueError, match="canonical_input"):
        PromptProjection(
            messages=messages,
            decision_schema=projection.decision_schema,
            canonical_input=projection.canonical_input,
            prompt_bytes=len(encoded),
            prompt_sha256=hashlib.sha256(encoded).hexdigest(),
            included_history_records=projection.included_history_records,
            omitted_history_records=projection.omitted_history_records,
            short_chat=projection.short_chat,
            token_proxy_units=projection.token_proxy_units,
            discussion_capture=projection.discussion_capture,
        )
