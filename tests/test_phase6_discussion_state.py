from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from ai_client.discussion.context import (
    AuthorizedChatChannelContext,
    AuthorizedDiscussionContext,
    BoundDiscussionContext,
    CountParityWinCondition,
    DiscussionContextError,
    canonical_json_bytes,
    canonical_sha256,
)
from ai_client.discussion.model import (
    AbortAck,
    AiDiscussionGenerationStatus,
    AssessmentUpdate,
    ClaimAssessment,
    ClaimUpdate,
    ClaimVerdict,
    CoJudgment,
    CoJudgmentDecision,
    DiscussionAbortReason,
    DiscussionDeliveryStatus,
    DiscussionGenerationAck,
    DiscussionObservationStatus,
    DiscussionProposal,
    DiscussionProvenance,
    DiscussionResetReason,
    DiscussionStance,
    DiscussionStateSnapshot,
    DiscussionTopic,
    DiscussionTrigger,
    DiscussionValidationError,
    EvidenceRecordKind,
    EvidenceRef,
    EvidenceVisibility,
    ImportantEvent,
    MAX_ASSESSMENTS,
    MAX_CLAIMS,
    MAX_EVIDENCE_PER_ITEM,
    MAX_EVENT_PLAYERS,
    MAX_FOCUS_PLAYERS,
    MAX_IMPORTANT_EVENTS,
    MAX_PROPOSAL_BYTES,
    MAX_RECENT_SEMANTIC_TURNS,
    MAX_RELATIONS,
    MAX_STATE_BYTES,
    PlayerAssessment,
    PreVoteReassessment,
    ReactionAssessment,
    ReactionReason,
    RecentSemanticTurn,
    RelationHypothesis,
    RelationKind,
    RelationUpdate,
    SpeechActClaim,
    SpeechActKind,
    SpeechActNone,
    StrategyMode,
    StrategyState,
    StrategyUpdate,
)
from ai_client.discussion.state import (
    DiscussionStateConfig,
    DiscussionStateError,
    DiscussionStateStore,
    DiscussionViews,
    evidence_ref_for_record,
    important_event_for_record,
)
from ai_client.network.types import SendReceipt
from ai_client.world import (
    AbilityResultRecord,
    AbilityResultView,
    ActionAcceptedObservation,
    ActionRejectionObservation,
    ChatRecord,
    CoDeclarationRecord,
    CoReportRecord,
    CoView,
    DeathRecord,
    Freshness,
    GameLifecycleRecord,
    HistoryRetention,
    HistoryView,
    KnownUnmodeledEventRecord,
    MalformedEventRecord,
    PhaseDeadlineReachedObservation,
    PhaseTimingChangedRecord,
    PhaseTimingObservation,
    PhaseTransitionRecord,
    PhaseView,
    PlayerView,
    PublicNotifyRecord,
    ResumeRecoveryBarrier,
    SelfView,
    TieResolvedRandomRecord,
    TransportObservationView,
    UnknownEventRecord,
    VoteEntry,
    VoteRevealRecord,
    VoteResultRecord,
    WorldSnapshot,
)


def _bound() -> BoundDiscussionContext:
    context = AuthorizedDiscussionContext(
        schema_version="aiwolf.discussion-context.v1",
        game_id="opaque-game",
        player_id="opaque-self",
        role_id="opaque-role",
        modifier_ids=(),
        content_manifest_sha256="a" * 64,
        team="opaque-team",
        count_as="opaque-count",
        attack_result="opaque-attack",
        inspect_result="opaque-inspect",
        medium_result="opaque-medium",
        win_conditions=(
            CountParityWinCondition(
                type="count_parity",
                subject="opaque-count",
                against="opaque-other-count",
                operator="gte",
            ),
        ),
        abilities=(),
        passives=(),
        chat_channels=(
            AuthorizedChatChannelContext("opaque-private", False),
            AuthorizedChatChannelContext("opaque-public", True),
        ),
        knows_teammates=False,
        authorized_known_player_ids=(),
        known_players_complete=False,
    )
    return BoundDiscussionContext(
        manifest_sha256="a" * 64,
        context_sha256=canonical_sha256(context),
        context=context,
    )


def _retention(
    records: tuple[object, ...] = (),
    *,
    complete: bool = True,
    dropped_count: int = 0,
    dropped_through_order: int | None = None,
) -> HistoryRetention:
    orders = [record.order for record in records]
    return HistoryRetention(
        total_seen=len(records) + dropped_count,
        retained_count=len(records),
        retained_bytes=0,
        dropped_count=dropped_count,
        dropped_through_order=dropped_through_order,
        first_retained_order=min(orders) if orders else None,
        last_order=max(orders) if orders else dropped_through_order,
        max_history_records=128,
        max_history_bytes=65536,
        complete=complete,
    )


def _snapshot(
    *,
    version: int = 1,
    seq: int = 1,
    day: int = 1,
    phase: str = "opaque-phase",
    retention: HistoryRetention | None = None,
    freshness: Freshness = Freshness.CURRENT,
) -> WorldSnapshot:
    return WorldSnapshot(
        version=version,
        freshness=freshness,
        is_caught_up=freshness is Freshness.CURRENT,
        last_applied_seq=seq,
        players=(
            PlayerView("opaque-peer", "peer-display"),
            PlayerView("opaque-self", "self-display"),
        ),
        alive_player_ids=("opaque-peer", "opaque-self"),
        phase=PhaseView(phase, day),
        self_view=SelfView("opaque-self", "opaque-role", ()),
        history_retention=retention or _retention(),
    )


def _views(
    records: tuple[object, ...] = (),
    *,
    snapshot: WorldSnapshot | None = None,
    transport: tuple[object, ...] = (),
) -> DiscussionViews:
    history_records = tuple(records)
    world = snapshot or _snapshot(retention=_retention(history_records))
    history_retention = world.history_retention
    co_records = tuple(
        record
        for record in history_records
        if isinstance(record, (CoDeclarationRecord, CoReportRecord))
    )
    abilities = tuple(record for record in history_records if isinstance(record, AbilityResultRecord))
    transport_view = TransportObservationView(
        world_version=world.version,
        first_retained_order=(min(item.order for item in transport) if transport else None),
        last_order=(max(item.order for item in transport) if transport else None),
        gap_before_first=False,
        observations=transport,
        current_deadline=None,
    )
    return DiscussionViews(
        snapshot=world,
        history=HistoryView(history_records, True, history_retention),
        co=CoView(
            declarations=tuple(item for item in co_records if isinstance(item, CoDeclarationRecord)),
            reports=tuple(item for item in co_records if isinstance(item, CoReportRecord)),
            complete=True,
            retention=history_retention,
        ),
        ability_results=AbilityResultView(abilities, True, history_retention),
        transport_observations=transport_view,
    )


def _trigger(
    *,
    generation: int = 1,
    day: int = 1,
    phase: str = "opaque-phase",
    source: EvidenceRef | None = None,
    owner: str = "reaction_chat",
    kind: str | None = None,
) -> DiscussionTrigger:
    return DiscussionTrigger(
        owner=owner,  # type: ignore[arg-type]
        kind=kind or ("PEER_CHAT" if source is not None else "INITIAL_CHAT"),  # type: ignore[arg-type]
        day=day,
        phase=phase,
        connection_generation=generation,
        action_generation=1,
        mapping_order=source.order if source is not None else 0,
        source=source,
    )


def _proposal(
    capture,
    *,
    decision_kind: str = "none",
    option_id: str | None = None,
    assessment_updates: tuple[AssessmentUpdate, ...] = (),
    strategy_update: StrategyUpdate | None = None,
    co_judgment: CoJudgment | None = None,
    pre_vote_reassessment: PreVoteReassessment | None = None,
) -> DiscussionProposal:
    return DiscussionProposal(
        schema_version="aiwolf.discussion-proposal.v1",
        base_revision=capture.base_revision,
        decision_kind=decision_kind,  # type: ignore[arg-type]
        option_id=option_id,
        speech_act=SpeechActNone(SpeechActKind.NONE),
        reaction=None,
        assessment_updates=assessment_updates,
        claim_updates=(),
        relation_updates=(),
        strategy_update=strategy_update,
        co_judgment=co_judgment,
        pre_vote_reassessment=pre_vote_reassessment,
    )


def _generation_ack(
    capture,
    proposal: DiscussionProposal,
    *,
    status: AiDiscussionGenerationStatus,
    audit_sequence: int = 7,
) -> DiscussionGenerationAck:
    return DiscussionGenerationAck(
        capture_id=capture.capture_id,
        request_id=f"phase6:{capture.capture_id}",
        final_attempt_ordinal=1,
        audit_sequence=audit_sequence,
        generation_record_sha256="b" * 64,
        generation_status=status,
        context_sha256=capture.context_sha256,
        before_state_sha256=capture.state_sha256,
        after_state_sha256=None,
        proposal_sha256=canonical_sha256(proposal),
        durable=True,
    )


def _all_record_kinds() -> tuple[tuple[object, EvidenceRecordKind, EvidenceVisibility], ...]:
    return (
        (ChatRecord(1, 1, "opaque-phase", "opaque-public", "opaque-peer", "peer", "hi"), EvidenceRecordKind.CHAT, EvidenceVisibility.PUBLIC),
        (CoDeclarationRecord(2, 1, "opaque-phase", "opaque-peer", "opaque-role", "co"), EvidenceRecordKind.CO_DECLARATION, EvidenceVisibility.PUBLIC),
        (CoReportRecord(3, 1, "opaque-phase", "opaque-peer", "inspect", "opaque-self", "result"), EvidenceRecordKind.CO_REPORT, EvidenceVisibility.PUBLIC),
        (VoteResultRecord(4, 1, "opaque-phase", "result", {"opaque-peer": 1}, "opaque-peer", ()), EvidenceRecordKind.VOTE_RESULT, EvidenceVisibility.PUBLIC),
        (VoteRevealRecord(5, 1, "opaque-phase", "opaque-peer", "opaque-self", ()), EvidenceRecordKind.VOTE_REVEAL, EvidenceVisibility.PUBLIC),
        (DeathRecord(6, 1, "opaque-phase", "opaque-peer", "opaque-cause"), EvidenceRecordKind.DEATH, EvidenceVisibility.PUBLIC),
        (PhaseTransitionRecord(7, 1, "opaque-phase", None), EvidenceRecordKind.PHASE_TRANSITION, EvidenceVisibility.PUBLIC),
        (PhaseTimingChangedRecord(8, 1, "opaque-phase", "opaque-change", None), EvidenceRecordKind.PHASE_TIMING_CHANGED, EvidenceVisibility.PUBLIC),
        (AbilityResultRecord(9, 1, "opaque-phase", "opaque-result", "opaque-peer", "opaque", None), EvidenceRecordKind.ABILITY_RESULT, EvidenceVisibility.AUTHORIZED_PRIVATE),
        (GameLifecycleRecord(10, 1, "opaque-phase", "opaque-lifecycle"), EvidenceRecordKind.GAME_LIFECYCLE, EvidenceVisibility.PUBLIC),
        (PublicNotifyRecord(11, 1, "opaque-phase", "opaque-notify"), EvidenceRecordKind.PUBLIC_NOTIFY, EvidenceVisibility.PUBLIC),
        (TieResolvedRandomRecord(12, 1, "opaque-phase", ("opaque-peer", "opaque-self"), "opaque-peer"), EvidenceRecordKind.TIE_RESOLVED_RANDOM, EvidenceVisibility.PUBLIC),
        (KnownUnmodeledEventRecord(13, "PRIVATE_LOOKING"), EvidenceRecordKind.KNOWN_UNMODELED, EvidenceVisibility.VISIBILITY_LOST),
        (UnknownEventRecord(14, "PUBLIC_LOOKING"), EvidenceRecordKind.UNKNOWN, EvidenceVisibility.VISIBILITY_LOST),
        (MalformedEventRecord(15, "CHAT_MESSAGE"), EvidenceRecordKind.MALFORMED, EvidenceVisibility.VISIBILITY_LOST),
        (ActionAcceptedObservation(16, 1, "chat", "event-1", 1, 1, 0.1), EvidenceRecordKind.ACTION_ACCEPTED, EvidenceVisibility.AUTHORIZED_PRIVATE),
        (ActionRejectionObservation(17, 1, "chat", "reason", 1, 1, 0.1, "event-1"), EvidenceRecordKind.ACTION_REJECTION, EvidenceVisibility.AUTHORIZED_PRIVATE),
        (PhaseTimingObservation(18, 1, "opaque-phase", 1, 1, 1, 1, 1, None, 0.1, None), EvidenceRecordKind.PHASE_TIMING, EvidenceVisibility.AUTHORIZED_PRIVATE),
        (PhaseDeadlineReachedObservation(19, 1, "opaque-phase", 1, 1, 1, 0.1, 0.2), EvidenceRecordKind.PHASE_DEADLINE_REACHED, EvidenceVisibility.AUTHORIZED_PRIVATE),
        (ResumeRecoveryBarrier(20, 1, 1, 0, None, None, True, False, 1, 1, True), EvidenceRecordKind.RESUME_RECOVERY_BARRIER, EvidenceVisibility.AUTHORIZED_PRIVATE),
    )


def test_design_state_and_proposal_limits_are_literal() -> None:
    assert MAX_EVIDENCE_PER_ITEM == 8
    assert MAX_EVENT_PLAYERS == 32
    assert MAX_IMPORTANT_EVENTS == 32
    assert MAX_RECENT_SEMANTIC_TURNS == 16
    assert MAX_ASSESSMENTS == 32
    assert MAX_CLAIMS == 32
    assert MAX_RELATIONS == 32
    assert MAX_FOCUS_PLAYERS == 4
    assert MAX_PROPOSAL_BYTES == 16384
    assert MAX_STATE_BYTES == 65536


def _public_evidence(count: int) -> tuple[EvidenceRef, ...]:
    return tuple(
        EvidenceRef(EvidenceRecordKind.CHAT, order, EvidenceVisibility.PUBLIC)
        for order in range(1, count + 1)
    )


def _evidence_collection_owner(kind: str, evidence: tuple[EvidenceRef, ...]) -> object:
    if kind == "recent-turn":
        capture_id = "c" * 64
        return RecentSemanticTurn(
            schema_version="aiwolf.recent-semantic-turn.v1",
            committed_revision=1,
            capture_id=capture_id,
            request_id=f"phase6:{capture_id}",
            day=1,
            phase="opaque-phase",
            decision_kind="none",
            option_id=None,
            speech_act_kind=SpeechActKind.NONE,
            evidence=evidence,
            proposal_sha256="d" * 64,
        )
    if kind == "assessment-state":
        return PlayerAssessment("opaque-peer", 50, 50, 50, evidence)
    if kind == "assessment-update":
        return AssessmentUpdate("opaque-peer", 50, 50, 50, evidence)
    raise AssertionError(f"unknown test owner: {kind}")


@pytest.mark.parametrize(
    "kind",
    (
        "recent-turn",
        "assessment-state",
        "assessment-update",
    ),
)
@pytest.mark.parametrize("count,accepted", ((7, True), (8, True), (9, False)))
def test_representative_evidence_collections_have_literal_seven_eight_nine_boundary(
    kind: str,
    count: int,
    accepted: bool,
) -> None:
    evidence = _public_evidence(count)
    if accepted:
        assert _evidence_collection_owner(kind, evidence) is not None
    else:
        with pytest.raises(DiscussionValidationError, match="at most 8"):
            _evidence_collection_owner(kind, evidence)


def _nontext_event(
    order: int,
    *,
    actor_player_ids: tuple[str, ...] = (),
    target_player_ids: tuple[str, ...] = (),
) -> ImportantEvent:
    return ImportantEvent(
        schema_version="aiwolf.important-event.v1",
        source=EvidenceRef(
            EvidenceRecordKind.PUBLIC_NOTIFY,
            order,
            EvidenceVisibility.PUBLIC,
        ),
        day=1,
        phase="opaque-phase",
        actor_player_ids=actor_player_ids,
        target_player_ids=target_player_ids,
        channel_id=None,
        importance=50,
        text_excerpt=None,
        text_original_scalars=None,
        text_original_utf8_bytes=None,
        text_truncated=False,
        remembered_after_world_eviction=False,
    )


@pytest.mark.parametrize("field", ("actor_player_ids", "target_player_ids"))
@pytest.mark.parametrize("count,accepted", ((31, True), (32, True), (33, False)))
def test_important_event_actor_and_target_literal_boundaries(
    field: str,
    count: int,
    accepted: bool,
) -> None:
    player_ids = tuple(f"opaque-player-{index:02d}" for index in range(count))
    kwargs = {field: player_ids}
    if accepted:
        assert getattr(_nontext_event(1, **kwargs), field) == player_ids
    else:
        with pytest.raises(DiscussionValidationError, match="at most 32"):
            _nontext_event(1, **kwargs)


def _text_event(text: str) -> ImportantEvent:
    return ImportantEvent(
        schema_version="aiwolf.important-event.v1",
        source=EvidenceRef(EvidenceRecordKind.CHAT, 1, EvidenceVisibility.PUBLIC),
        day=1,
        phase="opaque-phase",
        actor_player_ids=("opaque-peer",),
        target_player_ids=(),
        channel_id="opaque-public",
        importance=50,
        text_excerpt=text,
        text_original_scalars=len(text),
        text_original_utf8_bytes=len(text.encode("utf-8")),
        text_truncated=False,
        remembered_after_world_eviction=False,
    )


@pytest.mark.parametrize("scalars,accepted", ((159, True), (160, True), (161, False)))
def test_important_event_text_scalar_literal_boundary(
    scalars: int,
    accepted: bool,
) -> None:
    text = "x" * scalars
    if accepted:
        assert _text_event(text).text_excerpt == text
    else:
        with pytest.raises(DiscussionValidationError, match="hard bound"):
            _text_event(text)


def test_important_event_utf8_ceiling_is_shadowed_by_scalar_ceiling() -> None:
    widest_legal_text = "\N{GRINNING FACE}" * 160
    assert len(widest_legal_text) == 160
    assert len(widest_legal_text.encode("utf-8")) == 640
    assert 640 < 768
    assert _text_event(widest_legal_text).text_excerpt == widest_legal_text


@pytest.mark.parametrize("utf8_bytes", (767, 768, 769))
def test_text_near_768_bytes_is_already_rejected_by_160_scalar_limit(
    utf8_bytes: int,
) -> None:
    fours, remainder = divmod(utf8_bytes, 4)
    tail = {0: "", 1: "x", 2: "β", 3: "界"}[remainder]
    text = "\N{GRINNING FACE}" * fours + tail
    assert len(text.encode("utf-8")) == utf8_bytes
    assert len(text) > 160
    with pytest.raises(DiscussionValidationError, match="hard bound"):
        _text_event(text)


@pytest.mark.parametrize("owner", (StrategyState, StrategyUpdate))
@pytest.mark.parametrize("count,accepted", ((3, True), (4, True), (5, False)))
def test_strategy_focus_player_literal_boundary(owner, count: int, accepted: bool) -> None:
    player_ids = tuple(f"opaque-focus-{index}" for index in range(count))
    if accepted:
        assert owner("GAME", StrategyMode.WAIT, player_ids, ()).focus_player_ids == player_ids
    else:
        with pytest.raises(DiscussionValidationError, match="at most 4"):
            owner("GAME", StrategyMode.WAIT, player_ids, ())


@pytest.mark.parametrize("count,accepted", ((31, True), (32, True), (33, False)))
def test_pre_vote_ranked_player_literal_boundary(count: int, accepted: bool) -> None:
    player_ids = tuple(f"opaque-rank-{index:02d}" for index in range(count))
    preferred = player_ids[0]
    if accepted:
        result = PreVoteReassessment("vote-option", player_ids, preferred, ())
        assert result.ranked_target_player_ids == player_ids
    else:
        with pytest.raises(DiscussionValidationError, match="at most 32"):
            PreVoteReassessment("vote-option", player_ids, preferred, ())


def _state_collection(field: str, count: int) -> tuple[object, ...]:
    if field == "assessments":
        return tuple(
            PlayerAssessment(f"opaque-player-{index:02d}", 50, 50, 50, ())
            for index in range(count)
        )
    if field == "claims":
        return tuple(
            ClaimAssessment(
                EvidenceRef(
                    EvidenceRecordKind.CHAT,
                    index + 1,
                    EvidenceVisibility.PUBLIC,
                ),
                f"opaque-player-{index:02d}",
                ClaimVerdict.UNVERIFIED,
                50,
                (),
            )
            for index in range(count)
        )
    if field == "relations":
        return tuple(
            RelationHypothesis(
                f"opaque-source-{index:02d}",
                f"opaque-target-{index:02d}",
                RelationKind.SUPPORTS,
                50,
                (),
                "PUBLIC_INFERENCE",
            )
            for index in range(count)
        )
    if field == "important_events":
        return tuple(_nontext_event(index + 1) for index in range(count))
    if field == "recent_semantic_turns":
        return tuple(
            RecentSemanticTurn(
                schema_version="aiwolf.recent-semantic-turn.v1",
                committed_revision=index + 1,
                capture_id=f"{index + 1:064x}",
                request_id=f"phase6:{index + 1:064x}",
                day=1,
                phase="opaque-phase",
                decision_kind="none",
                option_id=None,
                speech_act_kind=SpeechActKind.NONE,
                evidence=(),
                proposal_sha256=f"{index + 101:064x}",
            )
            for index in range(count)
        )
    raise AssertionError(f"unknown state collection: {field}")


@pytest.mark.parametrize(
    "field,limit",
    (
        ("assessments", 32),
        ("claims", 32),
        ("relations", 32),
        ("important_events", 32),
        ("recent_semantic_turns", 16),
    ),
)
@pytest.mark.parametrize("offset,accepted", ((-1, True), (0, True), (1, False)))
def test_state_collection_literal_boundaries(
    field: str,
    limit: int,
    offset: int,
    accepted: bool,
) -> None:
    values = _state_collection(field, limit + offset)
    initial = DiscussionStateStore(_bound()).snapshot
    if accepted:
        state = replace(initial, **{field: values})
        assert len(getattr(state, field)) == limit + offset
    else:
        with pytest.raises(DiscussionValidationError, match=f"invalid {field}"):
            replace(initial, **{field: values})


def _proposal_collection(field: str, count: int) -> tuple[object, ...]:
    if field == "assessment_updates":
        return tuple(
            AssessmentUpdate(f"opaque-player-{index}", 50, 50, 50, ())
            for index in range(count)
        )
    if field == "claim_updates":
        return tuple(
            ClaimUpdate(
                EvidenceRef(
                    EvidenceRecordKind.CHAT,
                    index + 1,
                    EvidenceVisibility.PUBLIC,
                ),
                f"opaque-player-{index}",
                ClaimVerdict.UNVERIFIED,
                50,
                (),
            )
            for index in range(count)
        )
    if field == "relation_updates":
        return tuple(
            RelationUpdate(
                f"opaque-source-{index}",
                f"opaque-target-{index}",
                RelationKind.SUPPORTS,
                50,
                (),
                "PUBLIC_INFERENCE",
            )
            for index in range(count)
        )
    raise AssertionError(f"unknown proposal collection: {field}")


@pytest.mark.parametrize(
    "field,limit",
    (
        ("assessment_updates", 4),
        ("claim_updates", 4),
        ("relation_updates", 2),
    ),
)
@pytest.mark.parametrize("offset,accepted", ((-1, True), (0, True), (1, False)))
def test_proposal_collection_literal_boundaries(
    field: str,
    limit: int,
    offset: int,
    accepted: bool,
) -> None:
    capture = DiscussionStateStore(_bound()).capture(_views(), _trigger())
    proposal = _proposal(capture, decision_kind="chat", option_id="chat-option")
    values = _proposal_collection(field, limit + offset)
    if accepted:
        updated = replace(proposal, **{field: values})
        assert len(getattr(updated, field)) == limit + offset
    else:
        with pytest.raises(DiscussionValidationError, match=f"invalid {field}"):
            replace(proposal, **{field: values})


@pytest.mark.parametrize("count,accepted", ((7, True), (8, True), (9, False)))
def test_proposal_distinct_evidence_literal_boundary(count: int, accepted: bool) -> None:
    capture = DiscussionStateStore(_bound()).capture(_views(), _trigger())
    evidence = _public_evidence(count)
    proposal = _proposal(capture, decision_kind="chat", option_id="chat-option")
    speech = SpeechActClaim(
        SpeechActKind.CLAIM,
        "opaque-peer",
        DiscussionTopic.ALIGNMENT,
        DiscussionStance.UNCERTAIN,
        evidence[:8],
    )
    reaction = (
        ReactionAssessment(evidence[8], 50, ReactionReason.NEW_INFORMATION)
        if count == 9
        else None
    )
    if accepted:
        assert replace(proposal, speech_act=speech, reaction=reaction) is not None
    else:
        with pytest.raises(DiscussionValidationError, match="at most eight"):
            replace(proposal, speech_act=speech, reaction=reaction)


def _proposal_kwargs_with_canonical_size(target_size: int) -> dict[str, object]:
    prefixes = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef")

    def material(padding: int) -> dict[str, object]:
        remaining = padding
        ranked: list[str] = []
        for prefix in prefixes:
            added = min(remaining, 4 * (128 - len(prefix)))
            fours, remainder = divmod(added, 4)
            tail = {0: "", 1: "x", 2: "β", 3: "界"}[remainder]
            ranked.append(prefix + "\N{GRINNING FACE}" * fours + tail)
            remaining -= added
        assert remaining == 0
        return {
            "schema_version": "aiwolf.discussion-proposal.v1",
            "base_revision": 0,
            "decision_kind": "vote",
            "option_id": "vote-option",
            "speech_act": SpeechActNone(SpeechActKind.NONE),
            "reaction": None,
            "assessment_updates": (),
            "claim_updates": (),
            "relation_updates": (),
            "strategy_update": None,
            "co_judgment": None,
            "pre_vote_reassessment": PreVoteReassessment(
                "vote-option",
                tuple(ranked),
                None,
                (),
            ),
        }

    base = material(0)
    padding = target_size - len(canonical_json_bytes(base))
    assert padding >= 0
    sized = material(padding)
    assert len(canonical_json_bytes(sized)) == target_size
    return sized


@pytest.mark.parametrize(
    "target_size,accepted",
    ((16383, True), (16384, True), (16385, False)),
)
def test_proposal_canonical_byte_literal_boundary(
    target_size: int,
    accepted: bool,
) -> None:
    kwargs = _proposal_kwargs_with_canonical_size(target_size)
    if accepted:
        proposal = DiscussionProposal(**kwargs)  # type: ignore[arg-type]
        assert len(canonical_json_bytes(proposal)) == target_size
    else:
        with pytest.raises(DiscussionValidationError, match="16 KiB"):
            DiscussionProposal(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("record,kind,visibility", _all_record_kinds())
def test_all_twenty_record_kinds_have_exact_visibility(record, kind, visibility) -> None:
    reference = evidence_ref_for_record(record, _bound())
    assert reference.record_kind is kind
    assert reference.visibility is visibility


def test_opaque_private_chat_is_private_and_unknown_channel_is_terminal() -> None:
    private = ChatRecord(1, 1, "opaque-phase", "opaque-private", "opaque-peer", "peer", "secret")
    assert evidence_ref_for_record(private, _bound()).visibility is EvidenceVisibility.AUTHORIZED_PRIVATE
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(), _trigger())
    proposal = _proposal(capture)
    stage = store.stage(
        capture,
        f"phase6:{capture.capture_id}",
        proposal,
        _generation_ack(
            capture,
            proposal,
            status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        ),
    )
    before = store.snapshot
    unknown = ChatRecord(1, 2, "opaque-next", "opaque-unknown", "opaque-peer", "peer", "message")
    replacement = _snapshot(
        version=2,
        seq=2,
        day=2,
        phase="opaque-next",
        retention=_retention((unknown,)),
    )
    with pytest.raises(DiscussionContextError):
        store.capture(
            _views((unknown,), snapshot=replacement),
            _trigger(day=2, phase="opaque-next"),
        )
    assert store.snapshot == before
    assert store.is_closed
    with pytest.raises(DiscussionStateError, match="closed"):
        store.commit(stage)
    with pytest.raises(DiscussionStateError, match="closed"):
        store.capture(_views(), _trigger())


def test_fixed_non_chat_visibility_and_identity_conflicts_fail_closed() -> None:
    with pytest.raises(DiscussionValidationError):
        EvidenceRef(EvidenceRecordKind.PUBLIC_NOTIFY, 1, EvidenceVisibility.AUTHORIZED_PRIVATE)
    public = EvidenceRef(EvidenceRecordKind.CHAT, 1, EvidenceVisibility.PUBLIC)
    private = EvidenceRef(EvidenceRecordKind.CHAT, 1, EvidenceVisibility.AUTHORIZED_PRIVATE)
    with pytest.raises(DiscussionValidationError):
        PlayerAssessment("opaque-peer", 1, 2, 3, (public, private))


def test_discussion_views_require_one_coherent_world_retention_and_version() -> None:
    views = _views()
    mismatched_retention = replace(views.history.retention, total_seen=1)
    with pytest.raises(DiscussionStateError, match="share the snapshot"):
        DiscussionViews(
            snapshot=views.snapshot,
            history=replace(views.history, retention=mismatched_retention),
            co=views.co,
            ability_results=views.ability_results,
            transport_observations=views.transport_observations,
        )
    assert views.transport_observations is not None
    with pytest.raises(DiscussionStateError, match="world version"):
        DiscussionViews(
            snapshot=views.snapshot,
            history=views.history,
            co=views.co,
            ability_results=views.ability_results,
            transport_observations=replace(
                views.transport_observations,
                world_version=views.snapshot.version + 1,
            ),
        )


def test_important_event_projection_is_structural_bounded_and_chronological() -> None:
    text = "opaque-self " + "\N{GRINNING FACE}" * 300
    chat = ChatRecord(
        3,
        1,
        "opaque-phase",
        "opaque-public",
        "opaque-peer",
        "peer",
        text,
    )
    source = evidence_ref_for_record(chat, _bound())
    event = important_event_for_record(
        chat,
        bound_context=_bound(),
        snapshot=_snapshot(),
        trigger_source=source,
    )
    assert event.actor_player_ids == ("opaque-peer",)
    assert event.target_player_ids == ()
    assert event.channel_id == "opaque-public"
    assert event.importance == 70  # base + exact self-ID mention + current trigger
    assert event.text_excerpt is not None
    assert len(event.text_excerpt) <= 160
    assert len(event.text_excerpt.encode("utf-8")) <= 768
    assert event.text_original_scalars == len(text)
    assert event.text_original_utf8_bytes == len(text.encode("utf-8"))
    assert event.text_truncated is True

    reveal = VoteRevealRecord(
        4,
        1,
        "opaque-phase",
        "opaque-peer",
        "opaque-self",
        (VoteEntry("opaque-self", "opaque-peer"),),
    )
    projected = important_event_for_record(
        reveal,
        bound_context=_bound(),
        snapshot=_snapshot(),
    )
    assert projected.actor_player_ids == ("opaque-peer", "opaque-self")
    assert projected.target_player_ids == ("opaque-peer", "opaque-self")
    assert projected.importance == 80
    assert projected.text_excerpt is None


@pytest.mark.parametrize(
    "record",
    (
        KnownUnmodeledEventRecord(1, "PUBLIC_NOTIFY"),
        UnknownEventRecord(2, "PRIVATE_RESULT"),
        MalformedEventRecord(3, "CHAT_MESSAGE"),
    ),
)
def test_visibility_lost_markers_never_recover_day_phase_or_audience(record) -> None:
    projected = important_event_for_record(
        record,
        bound_context=_bound(),
        snapshot=_snapshot(),
    )
    assert projected.source.visibility is EvidenceVisibility.VISIBILITY_LOST
    assert projected.day is None
    assert projected.phase is None
    assert projected.actor_player_ids == ()
    assert projected.target_player_ids == ()
    assert projected.channel_id is None


def test_capture_is_deterministic_replay_safe_and_ordinal_is_call_specific() -> None:
    chat = ChatRecord(1, 1, "opaque-phase", "opaque-public", "opaque-peer", "peer", "hello self-display")
    views = _views((chat,))
    source = evidence_ref_for_record(chat, _bound())
    first_store = DiscussionStateStore(_bound())
    first = first_store.capture(views, _trigger(source=source))
    second = first_store.capture(views, _trigger(source=source))
    other = DiscussionStateStore(_bound()).capture(views, _trigger(source=source))
    assert first.state_sha256 == second.state_sha256 == other.state_sha256
    assert first.capture_id == other.capture_id
    assert first.capture_id != second.capture_id
    assert first.capture_ordinal == other.capture_ordinal == 1
    assert second.capture_ordinal == 2
    assert first.state.fact_revision == second.state.fact_revision
    assert first.evidence[0].importance == 70


@pytest.mark.parametrize("conflict_location", ("prior_journal", "same_view"))
def test_conflicting_replay_is_terminal_before_state_mutation(conflict_location: str) -> None:
    original = ChatRecord(1, 1, "opaque-phase", "opaque-public", "opaque-peer", "peer", "one")
    store = DiscussionStateStore(_bound())
    capture = store.capture(
        _views((original,)) if conflict_location == "prior_journal" else _views(),
        _trigger(),
    )
    proposal = _proposal(capture)
    stage = store.stage(
        capture,
        f"phase6:{capture.capture_id}",
        proposal,
        _generation_ack(
            capture,
            proposal,
            status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        ),
    )
    before = store.snapshot
    if conflict_location == "prior_journal":
        records = (replace(original, message="two"),)
    else:
        records = (
            ChatRecord(1, 2, "opaque-next", "opaque-public", "opaque-peer", "peer", "one"),
            ChatRecord(1, 2, "opaque-next", "opaque-public", "opaque-peer", "peer", "two"),
        )
    replacement = _snapshot(
        version=2,
        seq=2,
        day=2,
        phase="opaque-next",
        retention=_retention(records),
    )
    with pytest.raises(DiscussionStateError):
        store.capture(
            _views(records, snapshot=replacement),
            _trigger(day=2, phase="opaque-next"),
        )
    assert store.snapshot == before
    assert store.is_closed
    with pytest.raises(DiscussionStateError, match="closed"):
        store.commit(stage)
    with pytest.raises(DiscussionStateError, match="closed"):
        store.stage(
            capture,
            stage.request_id,
            proposal,
            _generation_ack(
                capture,
                proposal,
                status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
            ),
        )
    with pytest.raises(DiscussionStateError, match="closed"):
        store.capture(_views(), _trigger())


def test_world_eviction_keeps_bounded_event_and_marks_provenance() -> None:
    chat = ChatRecord(1, 1, "opaque-phase", "opaque-public", "opaque-peer", "peer", "remember")
    store = DiscussionStateStore(_bound())
    store.capture(_views((chat,)), _trigger())
    dropped = _snapshot(
        version=2,
        seq=2,
        retention=_retention((), complete=False, dropped_count=1, dropped_through_order=1),
    )
    capture = store.capture(_views((), snapshot=dropped), _trigger())
    assert capture.evidence[0].remembered_after_world_eviction is True
    assert capture.state.provenance.remembered_after_world_eviction_count == 1
    assert capture.state.provenance.world_history_complete is False


def _projection_authority_records(
    *,
    day: int = 1,
    phase: str = "opaque-phase",
) -> tuple[tuple[object, ...], ChatRecord]:
    trigger = ChatRecord(
        1,
        day,
        phase,
        "opaque-public",
        "opaque-peer",
        "peer",
        "outside newest ceiling",
    )
    older = tuple(
        DeathRecord(order, day, phase, "opaque-peer", "opaque-cause")
        for order in range(10, 22)
    )
    tied = PublicNotifyRecord(21, day, phase, "opaque-notify")
    newest = tuple(
        ChatRecord(
            order,
            day,
            phase,
            "opaque-public",
            "opaque-peer",
            "peer",
            f"newest-{order}",
        )
        for order in range(100, 112)
    )
    return (trigger,) + older + (tied,) + newest, trigger


def _authority_metadata(store: DiscussionStateStore) -> dict[str, object]:
    """Snapshot every committed field the T192 failure contract preserves."""

    return {
        "state": store.snapshot,
        "revision": store._revision,
        "fact_revision": store._fact_revision,
        "source_fingerprints": tuple(store._source_fingerprints.items()),
        "important_memory": dict(store._important_memory),
        "epoch": store._epoch,
        "connection_generation": store._connection_generation,
        "phase_key": store._phase_key,
        "capture_ordinal": store._capture_ordinal,
    }


def test_p6a_capture_authority_preserves_newest_twelve_in_40_record_regression() -> None:
    deaths = tuple(
        DeathRecord(order, 1, "opaque-phase", "opaque-peer", "opaque-cause")
        for order in range(1, 33)
    )
    chats = tuple(
        ChatRecord(
            order,
            1,
            "opaque-phase",
            "opaque-public",
            "opaque-peer",
            "peer",
            f"low-importance-{order}",
        )
        for order in range(33, 41)
    )

    capture = DiscussionStateStore(_bound()).capture(
        _views(deaths + chats),
        _trigger(),
    )

    assert tuple(item.source.order for item in capture.evidence) == tuple(range(9, 41))
    assert {
        (item.source.record_kind, item.source.order) for item in capture.evidence
    }.issuperset(
        {
            (EvidenceRecordKind.DEATH, order) for order in range(29, 33)
        }
        | {(EvidenceRecordKind.CHAT, order) for order in range(33, 41)}
    )
    assert capture.evidence == capture.state.important_events


def test_p6a_capture_authority_union_trigger_newest_old_and_chronology() -> None:
    records, outside_newest_trigger = _projection_authority_records()
    outside_source = evidence_ref_for_record(outside_newest_trigger, _bound())
    capture = DiscussionStateStore(
        _bound(),
        DiscussionStateConfig(max_important_events=25),
    ).capture(_views(records), _trigger(source=outside_source))

    expected = (
        ((1, EvidenceRecordKind.CHAT.value),)
        + tuple((order, EvidenceRecordKind.DEATH.value) for order in range(11, 22))
        + ((21, EvidenceRecordKind.PUBLIC_NOTIFY.value),)
        + tuple((order, EvidenceRecordKind.CHAT.value) for order in range(100, 112))
    )
    actual = tuple(
        (item.source.order, item.source.record_kind.value) for item in capture.evidence
    )
    assert actual == expected
    assert len(actual) == len(set(actual)) == 25
    assert actual == tuple(sorted(actual))

    newest_trigger = next(
        item for item in records if isinstance(item, ChatRecord) and item.order == 111
    )
    overlap = DiscussionStateStore(
        _bound(),
        DiscussionStateConfig(max_important_events=24),
    ).capture(
        _views(records),
        _trigger(source=evidence_ref_for_record(newest_trigger, _bound())),
    )
    overlap_keys = tuple(
        (item.source.order, item.source.record_kind.value) for item in overlap.evidence
    )
    assert len(overlap_keys) == len(set(overlap_keys)) == 24
    assert (111, EvidenceRecordKind.CHAT.value) in overlap_keys
    assert (1, EvidenceRecordKind.CHAT.value) not in overlap_keys
    assert overlap_keys == tuple(sorted(overlap_keys))


def test_p6a_capture_authority_deduplicates_equal_and_rejects_conflicts() -> None:
    declaration = CoDeclarationRecord(
        7,
        1,
        "opaque-phase",
        "opaque-peer",
        "opaque-role",
        "equal duplicate",
    )
    equal = DiscussionStateStore(_bound()).capture(
        _views((declaration,)),
        _trigger(),
    )
    assert len(equal.evidence) == 1
    assert equal.evidence[0].source == evidence_ref_for_record(declaration, _bound())

    equal_views = _views((declaration,))
    conflicting_views = DiscussionViews(
        snapshot=equal_views.snapshot,
        history=equal_views.history,
        co=replace(
            equal_views.co,
            declarations=(replace(declaration, comment="unequal duplicate"),),
        ),
        ability_results=equal_views.ability_results,
        transport_observations=equal_views.transport_observations,
    )
    conflict_store = DiscussionStateStore(_bound())
    before_conflict = conflict_store.snapshot
    with pytest.raises(DiscussionStateError, match="conflicting canonical bytes"):
        conflict_store.capture(conflicting_views, _trigger())
    assert conflict_store.snapshot == before_conflict
    assert conflict_store.is_closed

    chat = ChatRecord(
        9,
        1,
        "opaque-phase",
        "opaque-public",
        "opaque-peer",
        "peer",
        "current replaces remembered",
    )
    memory_store = DiscussionStateStore(_bound())
    memory_store.capture(_views((chat,)), _trigger())
    dropped_snapshot = _snapshot(
        version=2,
        seq=2,
        retention=_retention(
            (),
            complete=False,
            dropped_count=1,
            dropped_through_order=9,
        ),
    )
    remembered = memory_store.capture(_views((), snapshot=dropped_snapshot), _trigger())
    assert remembered.evidence[0].remembered_after_world_eviction is True
    current_snapshot = _snapshot(
        version=3,
        seq=3,
        retention=_retention((chat,)),
    )
    current = memory_store.capture(_views((chat,), snapshot=current_snapshot), _trigger())
    assert len(current.evidence) == 1
    assert current.evidence[0].source == remembered.evidence[0].source
    assert current.evidence[0].remembered_after_world_eviction is False


def test_p6a_capture_authority_default_and_lowered_capacity_fail_closed() -> None:
    exact_error = "configured event bound cannot retain required projection evidence"

    zero_store = DiscussionStateStore(
        _bound(),
        DiscussionStateConfig(max_important_events=0),
    )
    empty = zero_store.capture(_views(), _trigger())
    assert empty.evidence == ()
    zero_before = _authority_metadata(zero_store)
    nonempty = ChatRecord(
        1,
        2,
        "opaque-next",
        "opaque-public",
        "opaque-peer",
        "peer",
        "protected newest",
    )
    nonempty_snapshot = _snapshot(
        version=2,
        seq=2,
        day=2,
        phase="opaque-next",
        retention=_retention((nonempty,)),
    )
    with pytest.raises(DiscussionStateError) as zero_failure:
        zero_store.capture(
            _views((nonempty,), snapshot=nonempty_snapshot),
            _trigger(day=2, phase="opaque-next"),
        )
    assert str(zero_failure.value) == exact_error
    assert _authority_metadata(zero_store) == zero_before
    assert zero_store._current_capture is None

    records, trigger_record = _projection_authority_records(
        day=2,
        phase="opaque-next",
    )
    lower_store = DiscussionStateStore(
        _bound(),
        DiscussionStateConfig(max_important_events=24),
    )
    old_record = KnownUnmodeledEventRecord(500, "OPAQUE_OLD_MEMORY")
    old_capture = lower_store.capture(_views((old_record,)), _trigger())
    old_proposal = _proposal(old_capture)
    old_stage = lower_store.stage(
        old_capture,
        f"phase6:{old_capture.capture_id}",
        old_proposal,
        _generation_ack(
            old_capture,
            old_proposal,
            status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        ),
    )
    before = _authority_metadata(lower_store)
    next_snapshot = _snapshot(
        version=2,
        seq=2,
        day=2,
        phase="opaque-next",
        retention=_retention(records),
    )
    with pytest.raises(DiscussionStateError) as one_under:
        lower_store.capture(
            _views(records, snapshot=next_snapshot),
            _trigger(
                day=2,
                phase="opaque-next",
                source=evidence_ref_for_record(trigger_record, _bound()),
            ),
        )
    assert str(one_under.value) == exact_error
    assert _authority_metadata(lower_store) == before
    assert not lower_store.is_closed
    assert lower_store._current_capture is None
    assert lower_store._staged is None
    with pytest.raises(DiscussionStateError, match="no proposal"):
        lower_store.commit(old_stage)
    with pytest.raises(DiscussionStateError, match="not the current exact capture"):
        lower_store.stage(
            old_capture,
            old_stage.request_id,
            old_proposal,
            _generation_ack(
                old_capture,
                old_proposal,
                status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
            ),
        )

    exact = DiscussionStateStore(
        _bound(),
        DiscussionStateConfig(max_important_events=25),
    ).capture(
        _views(records, snapshot=next_snapshot),
        _trigger(
            day=2,
            phase="opaque-next",
            source=evidence_ref_for_record(trigger_record, _bound()),
        ),
    )
    assert len(exact.evidence) == 25

    default = DiscussionStateStore(_bound()).capture(
        _views(records, snapshot=next_snapshot),
        _trigger(
            day=2,
            phase="opaque-next",
            source=evidence_ref_for_record(trigger_record, _bound()),
        ),
    )
    assert len(default.evidence) == 26


def test_p6a_capture_authority_hash_and_private_sentinel_regression() -> None:
    deaths = tuple(
        DeathRecord(order, 1, "opaque-phase", "opaque-peer", "opaque-cause")
        for order in range(1, 33)
    )
    sentinel = "T194_OWNER_PRIVATE_SENTINEL"
    private_chats = tuple(
        ChatRecord(
            order,
            1,
            "opaque-phase",
            "opaque-private",
            "opaque-peer",
            "peer",
            f"{sentinel}-{order}",
        )
        for order in range(33, 41)
    )
    former = DiscussionStateStore(_bound()).capture(_views(deaths), _trigger())
    revised = DiscussionStateStore(_bound()).capture(
        _views(deaths + private_chats),
        _trigger(),
    )
    assert former.context_sha256 == revised.context_sha256
    assert former.fact_revision == revised.fact_revision == 1
    assert former.state_sha256 != revised.state_sha256
    assert former.capture_id != revised.capture_id
    assert former.evidence != revised.evidence
    retained_private = tuple(
        item
        for item in revised.evidence
        if item.source.record_kind is EvidenceRecordKind.CHAT
    )
    assert tuple(item.source.order for item in retained_private) == tuple(range(33, 41))
    assert all(
        item.source.visibility is EvidenceVisibility.AUTHORIZED_PRIVATE
        and item.channel_id == "opaque-private"
        and item.text_excerpt is not None
        and sentinel in item.text_excerpt
        for item in retained_private
    )
    assert sentinel.encode("utf-8") in canonical_json_bytes(revised.state)

    sizing_store = DiscussionStateStore(_bound())
    sized = sizing_store.capture(_views(), _trigger())
    sized_proposal = _proposal(sized)
    sizing_store.stage(
        sized,
        f"phase6:{sized.capture_id}",
        sized_proposal,
        _generation_ack(
            sized,
            sized_proposal,
            status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        ),
    )
    assert sizing_store._staged is not None
    configured_limit = len(canonical_json_bytes(sizing_store._staged.next_state))
    configured_store = DiscussionStateStore(
        _bound(),
        DiscussionStateConfig(max_state_bytes=configured_limit),
    )
    configured_capture = configured_store.capture(_views(), _trigger())
    configured_proposal = _proposal(configured_capture)
    configured_stage = configured_store.stage(
        configured_capture,
        f"phase6:{configured_capture.capture_id}",
        configured_proposal,
        _generation_ack(
            configured_capture,
            configured_proposal,
            status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        ),
    )
    configured_before = _authority_metadata(configured_store)
    larger = ChatRecord(
        1,
        2,
        "opaque-next",
        "opaque-public",
        "opaque-peer",
        "peer",
        "x" * 160,
    )
    larger_snapshot = _snapshot(
        version=2,
        seq=2,
        day=2,
        phase="opaque-next",
        retention=_retention((larger,)),
    )
    with pytest.raises(DiscussionStateError) as configured_failure:
        configured_store.capture(
            _views((larger,), snapshot=larger_snapshot),
            _trigger(day=2, phase="opaque-next"),
        )
    assert str(configured_failure.value) == "discussion state exceeds configured byte limit"
    assert _authority_metadata(configured_store) == configured_before
    assert configured_store._current_capture is None
    assert configured_store._staged is None
    with pytest.raises(DiscussionStateError, match="no proposal"):
        configured_store.commit(configured_stage)

    peer_ids = tuple(f"p{index:02d}-" + "x" * 124 for index in range(31))
    player_ids = ("opaque-self",) + peer_ids
    votes = tuple(
        VoteEntry(player_id, player_ids[(index + 1) % len(player_ids)])
        for index, player_id in enumerate(player_ids)
    )
    oversized_records = tuple(
        VoteRevealRecord(
            order,
            2,
            "opaque-next",
            player_ids[1],
            player_ids[2],
            votes,
        )
        for order in range(1, 13)
    )
    oversized_snapshot = replace(
        _snapshot(
            version=2,
            seq=2,
            day=2,
            phase="opaque-next",
            retention=_retention(oversized_records),
        ),
        players=tuple(
            PlayerView(player_id, f"display-{index}")
            for index, player_id in enumerate(player_ids)
        ),
        alive_player_ids=player_ids,
    )
    hard_store = DiscussionStateStore(_bound())
    hard_capture = hard_store.capture(_views(), _trigger())
    hard_proposal = _proposal(hard_capture)
    hard_stage = hard_store.stage(
        hard_capture,
        f"phase6:{hard_capture.capture_id}",
        hard_proposal,
        _generation_ack(
            hard_capture,
            hard_proposal,
            status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        ),
    )
    hard_before = _authority_metadata(hard_store)
    with pytest.raises(DiscussionStateError) as hard_failure:
        hard_store.capture(
            _views(oversized_records, snapshot=oversized_snapshot),
            _trigger(day=2, phase="opaque-next"),
        )
    assert str(hard_failure.value) == "discussion state exceeds configured byte limit"
    assert _authority_metadata(hard_store) == hard_before
    assert not hard_store.is_closed
    assert hard_store._current_capture is None
    assert hard_store._staged is None
    with pytest.raises(DiscussionStateError, match="no proposal"):
        hard_store.commit(hard_stage)


def test_p6a_stage_rejects_reference_outside_authority_union() -> None:
    deaths = tuple(
        DeathRecord(order, 1, "opaque-phase", "opaque-peer", "opaque-cause")
        for order in range(1, 33)
    )
    chats = tuple(
        ChatRecord(
            order,
            1,
            "opaque-phase",
            "opaque-public",
            "opaque-peer",
            "peer",
            f"newest-{order}",
        )
        for order in range(33, 41)
    )
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(deaths + chats), _trigger())
    omitted = EvidenceRef(
        EvidenceRecordKind.DEATH,
        1,
        EvidenceVisibility.PUBLIC,
    )
    assert omitted not in tuple(item.source for item in capture.evidence)
    proposal = _proposal(
        capture,
        assessment_updates=(
            AssessmentUpdate("opaque-peer", 55, 45, 60, (omitted,)),
        ),
    )
    before = store.snapshot
    with pytest.raises(
        DiscussionStateError,
        match="absent from the captured authorized projection",
    ):
        store.stage(
            capture,
            f"phase6:{capture.capture_id}",
            proposal,
            _generation_ack(
                capture,
                proposal,
                status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
            ),
        )
    assert store.snapshot == before

    valid = _proposal(capture)
    staged = store.stage(
        capture,
        f"phase6:{capture.capture_id}",
        valid,
        _generation_ack(
            capture,
            valid,
            status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        ),
    )
    assert staged.staged is True


def test_clean_reconnect_preserves_epoch_but_gap_resets_it() -> None:
    store = DiscussionStateStore(_bound())
    store.capture(_views(), _trigger(generation=1))
    clean = ResumeRecoveryBarrier(1, 2, 2, 1, 2, 2, True, False, 2, 2, True)
    preserved = store.capture(
        _views(snapshot=_snapshot(version=2, seq=2), transport=(clean,)),
        _trigger(generation=2),
    )
    assert preserved.epoch == 0
    gap = ResumeRecoveryBarrier(2, 3, 3, 2, None, None, False, True, 3, 3, False)
    reset = store.capture(
        _views(snapshot=_snapshot(version=3, seq=3), transport=(gap,)),
        _trigger(generation=3),
    )
    assert reset.epoch == 1
    assert reset.state.revision == 0
    assert reset.state.provenance.reset_reason is DiscussionResetReason.RECOVERY_GAP


def test_phase_change_and_process_restart_are_explicit_and_close_freezes() -> None:
    restarted = DiscussionStateStore(_bound(), process_restart=True)
    first = restarted.capture(_views(), _trigger())
    assert first.state.provenance.reset_reason is DiscussionResetReason.PROCESS_RESTART
    second = restarted.capture(
        _views(snapshot=_snapshot(version=2, seq=2, day=2, phase="opaque-next")),
        _trigger(day=2, phase="opaque-next"),
    )
    assert (second.state.day, second.state.phase) == (2, "opaque-next")
    restarted.close()
    assert restarted.is_closed
    with pytest.raises(DiscussionStateError):
        restarted.capture(_views(), _trigger())


def test_context_mismatch_is_terminal_and_clears_capture() -> None:
    store = DiscussionStateStore(_bound())
    store.capture(_views(), _trigger())
    bad_snapshot = replace(
        _snapshot(version=2, seq=2),
        self_view=SelfView("opaque-self", "other-role", ()),
    )
    with pytest.raises(DiscussionContextError):
        store.capture(_views(snapshot=bad_snapshot), _trigger())
    assert store.is_closed
    with pytest.raises(DiscussionStateError, match="closed"):
        store.capture(_views(), _trigger())


@pytest.mark.parametrize(
    "snapshot,error",
    (
        (_snapshot(freshness=Freshness.EMPTY), "CURRENT"),
        (_snapshot(freshness=Freshness.STALE), "CURRENT"),
        (replace(_snapshot(), is_caught_up=False), "caught-up"),
    ),
    ids=("empty", "stale", "current-not-caught-up"),
)
def test_transient_snapshot_suppresses_capture_without_closing(
    snapshot: WorldSnapshot,
    error: str,
) -> None:
    store = DiscussionStateStore(_bound())
    with pytest.raises(DiscussionStateError, match=error):
        store.capture(_views(snapshot=snapshot), _trigger())
    assert not store.is_closed
    assert store.capture(_views(), _trigger()).capture_ordinal == 1


@pytest.mark.parametrize("freshness", (Freshness.ENDED, Freshness.FAILED))
def test_terminal_world_freshness_freezes_state_and_invalidates_stage(
    freshness: Freshness,
) -> None:
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(), _trigger())
    proposal = _proposal(capture)
    stage = store.stage(
        capture,
        f"phase6:{capture.capture_id}",
        proposal,
        _generation_ack(
            capture,
            proposal,
            status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        ),
    )
    before = store.snapshot
    with pytest.raises(DiscussionStateError, match="terminal"):
        store.capture(_views(snapshot=_snapshot(freshness=freshness)), _trigger())
    assert store.snapshot == before
    assert store.is_closed
    with pytest.raises(DiscussionStateError, match="closed"):
        store.commit(stage)


def _state_kwargs_with_canonical_size(target_size: int) -> dict[str, object]:
    provenance = DiscussionProvenance(
        schema_version="aiwolf.discussion-provenance.v1",
        history_complete=True,
        co_complete=True,
        ability_results_complete=True,
        world_history_complete=True,
        world_history_dropped_count=0,
        world_history_dropped_through_order=None,
        remembered_after_world_eviction_count=0,
        known_unmodeled_event_count=0,
        unknown_event_count=0,
        malformed_event_count=0,
        model_state_reset=False,
        reset_reason=None,
    )
    prefixes = tuple(
        tuple(f"p{event_index:02d}-{player_index:02d}-" for player_index in range(32))
        for event_index in range(32)
    )

    def material(padding: int) -> dict[str, object]:
        remaining = padding
        events: list[ImportantEvent] = []
        for event_index, event_prefixes in enumerate(prefixes, start=1):
            actor_ids: list[str] = []
            for prefix in event_prefixes:
                added = min(remaining, 128 - len(prefix))
                actor_ids.append(prefix + "x" * added)
                remaining -= added
            events.append(
                ImportantEvent(
                    schema_version="aiwolf.important-event.v1",
                    source=EvidenceRef(
                        EvidenceRecordKind.PUBLIC_NOTIFY,
                        event_index,
                        EvidenceVisibility.PUBLIC,
                    ),
                    day=1,
                    phase="opaque-phase",
                    actor_player_ids=tuple(actor_ids),
                    target_player_ids=(),
                    channel_id=None,
                    importance=90,
                    text_excerpt=None,
                    text_original_scalars=None,
                    text_original_utf8_bytes=None,
                    text_truncated=False,
                    remembered_after_world_eviction=False,
                )
            )
        assert remaining == 0
        return {
            "schema_version": "aiwolf.discussion-state.v1",
            "game_id": "opaque-game",
            "player_id": "opaque-self",
            "context_sha256": "a" * 64,
            "epoch": 0,
            "revision": 0,
            "fact_revision": 0,
            "world_version": 1,
            "last_applied_seq": 1,
            "phase": "opaque-phase",
            "day": 1,
            "assessments": (),
            "claims": (),
            "relations": (),
            "strategy": None,
            "important_events": tuple(events),
            "recent_semantic_turns": (),
            "provenance": provenance,
        }

    base = material(0)
    padding = target_size - len(canonical_json_bytes(base))
    assert padding >= 0
    sized = material(padding)
    assert len(canonical_json_bytes(sized)) == target_size
    return sized


@pytest.mark.parametrize(
    "target_size,accepted",
    ((65535, True), (65536, True), (65537, False)),
)
def test_state_constructor_enforces_absolute_64k_bound_and_event_identity(
    target_size: int,
    accepted: bool,
) -> None:
    kwargs = _state_kwargs_with_canonical_size(target_size)
    if accepted:
        state = DiscussionStateSnapshot(**kwargs)  # type: ignore[arg-type]
        assert len(canonical_json_bytes(state)) == target_size
    else:
        with pytest.raises(DiscussionValidationError, match="64 KiB"):
            DiscussionStateSnapshot(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "decision_kind,option_id,trigger,co_judgment,pre_vote,generation_status",
    (
        (
            "none",
            None,
            _trigger(),
            None,
            None,
            AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        ),
        (
            "chat",
            "chat-option",
            _trigger(),
            None,
            None,
            AiDiscussionGenerationStatus.DECISION,
        ),
        (
            "vote",
            "vote-option",
            _trigger(owner="vote_ability", kind="PRE_VOTE"),
            None,
            PreVoteReassessment(
                option_id="vote-option",
                ranked_target_player_ids=("opaque-peer",),
                preferred_target_player_id="opaque-peer",
                evidence=(),
            ),
            AiDiscussionGenerationStatus.DECISION,
        ),
        (
            "ability",
            "ability-option",
            _trigger(owner="vote_ability", kind="ABILITY"),
            None,
            None,
            AiDiscussionGenerationStatus.DECISION,
        ),
        (
            "co_declare",
            "co-option",
            _trigger(kind="CO_OPPORTUNITY"),
            CoJudgment(CoJudgmentDecision.DECLARE, "co-option", "opaque-role"),
            None,
            AiDiscussionGenerationStatus.DECISION,
        ),
    ),
)
def test_all_five_proposal_identities_stage_and_commit_exact_turn(
    decision_kind,
    option_id,
    trigger,
    co_judgment,
    pre_vote,
    generation_status,
) -> None:
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(), trigger)
    proposal = _proposal(
        capture,
        decision_kind=decision_kind,
        option_id=option_id,
        co_judgment=co_judgment,
        pre_vote_reassessment=pre_vote,
    )
    generation = _generation_ack(
        capture,
        proposal,
        status=generation_status,
    )
    before = store.snapshot
    stage = store.stage(capture, generation.request_id, proposal, generation)
    assert store.snapshot == before
    assert stage.after_state_sha256 is None
    commit = store.commit(stage)
    assert commit.committed_revision == capture.base_revision + 1
    assert commit.after_state_sha256 == canonical_sha256(store.snapshot)
    turn = store.snapshot.recent_semantic_turns[-1]
    assert turn.decision_kind == decision_kind
    assert turn.option_id == option_id
    assert turn.speech_act_kind is SpeechActKind.NONE
    assert turn.proposal_sha256 == canonical_sha256(proposal)
    assert turn.capture_id == capture.capture_id
    if decision_kind == "none":
        delivery = store.finish_no_action(commit)
        assert delivery.status is DiscussionDeliveryStatus.NO_ACTION
    else:
        dispatch = store.mark_dispatch_started(commit, decision_kind, option_id)
        delivery = store.finish_dispatch(
            dispatch,
            DiscussionDeliveryStatus.NOT_DELIVERED,
        )
        assert delivery.status is DiscussionDeliveryStatus.NOT_DELIVERED


def test_proposal_identity_nullability_and_hash_mutations_are_closed() -> None:
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(), _trigger())
    chat = _proposal(capture, decision_kind="chat", option_id="option-a")
    assert canonical_sha256(chat) != canonical_sha256(replace(chat, option_id="option-b"))
    assert canonical_sha256(chat) != canonical_sha256(replace(chat, decision_kind="vote"))
    with pytest.raises(DiscussionValidationError):
        replace(chat, decision_kind="none")
    with pytest.raises(DiscussionValidationError):
        replace(chat, option_id=None)
    with pytest.raises(DiscussionValidationError):
        replace(chat, decision_kind="co_report")


def test_stage_requires_exact_successful_generation_hashes_and_is_single_use() -> None:
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(), _trigger())
    proposal = _proposal(capture)
    generation = _generation_ack(
        capture,
        proposal,
        status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
    )
    before = store.snapshot

    failed = DiscussionGenerationAck(
        capture_id=capture.capture_id,
        request_id=generation.request_id,
        final_attempt_ordinal=1,
        audit_sequence=7,
        generation_record_sha256="b" * 64,
        generation_status=AiDiscussionGenerationStatus.BACKEND_FAILED,
        context_sha256=capture.context_sha256,
        before_state_sha256=capture.state_sha256,
        after_state_sha256=None,
        proposal_sha256=None,
        durable=True,
    )
    with pytest.raises(DiscussionStateError, match="successful generation"):
        store.stage(capture, generation.request_id, proposal, failed)
    with pytest.raises(DiscussionStateError, match="does not match"):
        store.stage(
            capture,
            generation.request_id,
            proposal,
            replace(generation, proposal_sha256="c" * 64),
        )
    assert store.snapshot == before

    stage = store.stage(capture, generation.request_id, proposal, generation)
    with pytest.raises(DiscussionStateError, match="duplicate stage"):
        store.stage(capture, generation.request_id, proposal, generation)
    with pytest.raises(DiscussionStateError, match="exact stage"):
        store.commit(replace(stage, proposal_sha256="c" * 64))
    assert store.snapshot == before
    commit = store.commit(stage)
    with pytest.raises(DiscussionStateError, match="no proposal"):
        store.commit(stage)
    store.finish_no_action(commit)


def test_stage_requires_exact_captured_evidence_and_never_upgrades_visibility() -> None:
    chat = ChatRecord(
        1,
        1,
        "opaque-phase",
        "opaque-private",
        "opaque-peer",
        "peer",
        "private evidence",
    )
    private = evidence_ref_for_record(chat, _bound())
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views((chat,)), _trigger())
    proposal = _proposal(
        capture,
        assessment_updates=(AssessmentUpdate("opaque-peer", 55, 45, 70, (private,)),),
    )
    stage = store.stage(
        capture,
        f"phase6:{capture.capture_id}",
        proposal,
        _generation_ack(
            capture,
            proposal,
            status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        ),
    )
    assert stage.proposal_sha256 == canonical_sha256(proposal)
    store.abort(
        capture.capture_id,
        stage.request_id,
        DiscussionAbortReason.CANCELLED_AFTER_STAGE,
        stage.proposal_sha256,
    )

    upgraded = EvidenceRef(EvidenceRecordKind.CHAT, private.order, EvidenceVisibility.PUBLIC)
    other = DiscussionStateStore(_bound())
    other_capture = other.capture(_views((chat,)), _trigger())
    invalid = _proposal(
        other_capture,
        assessment_updates=(AssessmentUpdate("opaque-peer", 55, 45, 70, (upgraded,)),),
    )
    before = other.snapshot
    with pytest.raises(DiscussionStateError, match="captured authorized projection"):
        other.stage(
            other_capture,
            f"phase6:{other_capture.capture_id}",
            invalid,
            _generation_ack(
                other_capture,
                invalid,
                status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
            ),
        )
    assert other.snapshot == before


@pytest.mark.parametrize(
    "decision_kind,option_id",
    (("none", None), ("chat", "chat-option")),
)
def test_repair_succeeded_generation_accepts_none_or_action(decision_kind, option_id) -> None:
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(), _trigger())
    proposal = _proposal(capture, decision_kind=decision_kind, option_id=option_id)
    generation = _generation_ack(
        capture,
        proposal,
        status=AiDiscussionGenerationStatus.REPAIR_SUCCEEDED,
    )
    stage = store.stage(capture, generation.request_id, proposal, generation)
    assert stage.proposal_sha256 == canonical_sha256(proposal)
    store.abort(
        capture.capture_id,
        generation.request_id,
        DiscussionAbortReason.CANCELLED_AFTER_STAGE,
        stage.proposal_sha256,
    )


@pytest.mark.parametrize(
    "reason,with_digest",
    (
        (DiscussionAbortReason.PROMPT_REJECTED, False),
        (DiscussionAbortReason.BACKEND_FAILED, False),
        (DiscussionAbortReason.FINAL_OUTPUT_INVALID, False),
        (DiscussionAbortReason.CANCELLED_BEFORE_RESULT, False),
        (DiscussionAbortReason.AUDIT_FAILED, False),
        (DiscussionAbortReason.STAGE_FAILED, True),
    ),
)
def test_before_stage_abort_matrix_is_atomic_and_identically_idempotent(
    reason,
    with_digest,
) -> None:
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(), _trigger())
    proposal = _proposal(capture)
    digest = canonical_sha256(proposal) if with_digest else None
    before = store.snapshot
    ack = store.abort(capture.capture_id, f"phase6:{capture.capture_id}", reason, digest)
    assert isinstance(ack, AbortAck)
    assert ack.stage_existed is False
    assert ack.after_state_sha256 is None
    assert store.snapshot == before
    assert store.abort(capture.capture_id, ack.request_id, reason, digest) is ack
    conflicting_reason = (
        DiscussionAbortReason.PROMPT_REJECTED
        if reason is not DiscussionAbortReason.PROMPT_REJECTED
        else DiscussionAbortReason.BACKEND_FAILED
    )
    with pytest.raises(DiscussionStateError, match="conflicting duplicate"):
        store.abort(capture.capture_id, ack.request_id, conflicting_reason)


@pytest.mark.parametrize(
    "reason,invalid_digest",
    (
        (DiscussionAbortReason.PROMPT_REJECTED, "d" * 64),
        (DiscussionAbortReason.STAGE_FAILED, None),
    ),
)
def test_before_stage_abort_rejects_branch_incompatible_proposal_digest(
    reason,
    invalid_digest,
) -> None:
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(), _trigger())
    before = store.snapshot
    with pytest.raises(DiscussionValidationError):
        store.abort(
            capture.capture_id,
            f"phase6:{capture.capture_id}",
            reason,
            invalid_digest,
        )
    assert store.snapshot == before


@pytest.mark.parametrize(
    "reason",
    (
        DiscussionAbortReason.CANCELLED_AFTER_STAGE,
        DiscussionAbortReason.STALE,
        DiscussionAbortReason.DEADLINE,
        DiscussionAbortReason.REVISION_CONFLICT,
    ),
)
def test_after_stage_abort_matrix_requires_exact_digest_and_never_commits(reason) -> None:
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(), _trigger())
    proposal = _proposal(capture)
    generation = _generation_ack(
        capture,
        proposal,
        status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
    )
    stage = store.stage(capture, generation.request_id, proposal, generation)
    before = store.snapshot
    with pytest.raises(DiscussionStateError, match="digest"):
        store.abort(capture.capture_id, generation.request_id, reason, "c" * 64)
    ack = store.abort(
        capture.capture_id,
        generation.request_id,
        reason,
        stage.proposal_sha256,
    )
    assert ack.stage_existed is True
    assert store.snapshot == before


@dataclass(frozen=True)
class _ExtraReceipt:
    event_id: str
    connection_generation: int
    sent: bool
    extra: str


def test_local_send_correlation_and_authoritative_observation_are_exact() -> None:
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(), _trigger())
    proposal = _proposal(capture, decision_kind="chat", option_id="chat-option")
    generation = _generation_ack(
        capture,
        proposal,
        status=AiDiscussionGenerationStatus.DECISION,
        audit_sequence=19,
    )
    commit = store.commit(store.stage(capture, generation.request_id, proposal, generation))
    dispatch = store.mark_dispatch_started(commit, "chat", "chat-option")
    with pytest.raises(DiscussionStateError, match="exact receipt"):
        store.finish_dispatch(dispatch, DiscussionDeliveryStatus.LOCAL_SENT)
    with pytest.raises(DiscussionStateError, match="absent or extra"):
        store.finish_dispatch(
            dispatch,
            DiscussionDeliveryStatus.LOCAL_SENT,
            _ExtraReceipt("event-1", 3, True, "extra"),
        )
    with pytest.raises(DiscussionStateError, match="sent=True"):
        store.finish_dispatch(
            dispatch,
            DiscussionDeliveryStatus.LOCAL_SENT,
            SendReceipt("event-1", 3, False),
        )

    delivery = store.finish_dispatch(
        dispatch,
        DiscussionDeliveryStatus.LOCAL_SENT,
        SendReceipt("event-1", 3),
    )
    correlation = delivery.correlation
    assert correlation is not None
    assert correlation.generation_audit_sequence == 19
    assert correlation.request_event_id == "event-1"
    with pytest.raises(DiscussionStateError, match="authoritative finalization"):
        store.close()

    evidence = EvidenceRef(EvidenceRecordKind.CHAT, 99, EvidenceVisibility.PUBLIC)
    observed = store.observe_authoritative(
        correlation,
        DiscussionObservationStatus.ACCEPTED,
        evidence,
    )
    assert observed.authoritative_evidence == evidence
    assert (
        store.observe_authoritative(
            correlation,
            DiscussionObservationStatus.ACCEPTED,
            evidence,
        )
        is observed
    )
    with pytest.raises(DiscussionStateError, match="conflicting duplicate"):
        store.observe_authoritative(
            correlation,
            DiscussionObservationStatus.ACCEPTED,
            EvidenceRef(EvidenceRecordKind.CHAT, 100, EvidenceVisibility.PUBLIC),
        )
    store.close()
    assert store.is_closed


@pytest.mark.parametrize(
    "status",
    (
        DiscussionObservationStatus.ACCEPTED,
        DiscussionObservationStatus.REJECTED,
    ),
)
def test_accepted_and_rejected_observations_require_authoritative_evidence(status) -> None:
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(), _trigger())
    proposal = _proposal(capture, decision_kind="chat", option_id="chat-option")
    generation = _generation_ack(
        capture,
        proposal,
        status=AiDiscussionGenerationStatus.DECISION,
    )
    commit = store.commit(store.stage(capture, generation.request_id, proposal, generation))
    dispatch = store.mark_dispatch_started(commit, "chat", "chat-option")
    delivery = store.finish_dispatch(
        dispatch,
        DiscussionDeliveryStatus.LOCAL_SENT,
        SendReceipt("event-1", 1),
    )
    assert delivery.correlation is not None
    with pytest.raises(DiscussionValidationError, match="requires evidence"):
        store.observe_authoritative(delivery.correlation, status)


@pytest.mark.parametrize(
    "status,evidence",
    (
        (
            DiscussionObservationStatus.REJECTED,
            EvidenceRef(
                EvidenceRecordKind.ACTION_REJECTION,
                101,
                EvidenceVisibility.AUTHORIZED_PRIVATE,
            ),
        ),
        (DiscussionObservationStatus.RECOVERY_UNKNOWN, None),
        (
            DiscussionObservationStatus.RECOVERY_UNKNOWN,
            EvidenceRef(
                EvidenceRecordKind.RESUME_RECOVERY_BARRIER,
                102,
                EvidenceVisibility.AUTHORIZED_PRIVATE,
            ),
        ),
    ),
)
def test_rejected_and_recovery_unknown_observation_nullability(status, evidence) -> None:
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(), _trigger())
    proposal = _proposal(capture, decision_kind="chat", option_id="chat-option")
    generation = _generation_ack(
        capture,
        proposal,
        status=AiDiscussionGenerationStatus.DECISION,
    )
    commit = store.commit(store.stage(capture, generation.request_id, proposal, generation))
    dispatch = store.mark_dispatch_started(commit, "chat", "chat-option")
    delivery = store.finish_dispatch(
        dispatch,
        DiscussionDeliveryStatus.LOCAL_SENT,
        SendReceipt("event-1", 1),
    )
    assert delivery.correlation is not None
    observation = store.observe_authoritative(
        delivery.correlation,
        status,
        evidence,
    )
    assert observation.status is status
    assert observation.authoritative_evidence == evidence


@pytest.mark.parametrize(
    "status",
    (
        DiscussionDeliveryStatus.NOT_DELIVERED,
        DiscussionDeliveryStatus.DELIVERY_UNKNOWN,
    ),
)
def test_unsent_delivery_has_no_receipt_or_correlation(status) -> None:
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(), _trigger())
    proposal = _proposal(capture, decision_kind="chat", option_id="chat-option")
    generation = _generation_ack(
        capture,
        proposal,
        status=AiDiscussionGenerationStatus.DECISION,
    )
    commit = store.commit(store.stage(capture, generation.request_id, proposal, generation))
    dispatch = store.mark_dispatch_started(commit, "chat", "chat-option")
    with pytest.raises(DiscussionStateError, match="cannot carry a receipt"):
        store.finish_dispatch(dispatch, status, SendReceipt("event-1", 1))
    delivery = store.finish_dispatch(dispatch, status)
    assert delivery.request_event_id is None
    assert delivery.send_connection_generation is None
    assert delivery.correlation is None


@pytest.mark.parametrize("transition", ("phase_change", "recovery_gap"))
def test_failed_authoritative_transition_invalidates_old_work_atomically(
    transition: str,
) -> None:
    store = DiscussionStateStore(
        _bound(),
        DiscussionStateConfig(max_important_events=0),
    )
    old_capture = store.capture(_views(), _trigger(generation=1))
    proposal = _proposal(old_capture)
    old_stage = store.stage(
        old_capture,
        f"phase6:{old_capture.capture_id}",
        proposal,
        _generation_ack(
            old_capture,
            proposal,
            status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        ),
    )
    before = store.snapshot
    before_hash = canonical_sha256(before)

    if transition == "phase_change":
        day = 2
        phase = "opaque-next"
        generation = 1
        gap: tuple[object, ...] = ()
    else:
        day = 1
        phase = "opaque-phase"
        generation = 2
        gap = (
            ResumeRecoveryBarrier(
                2,
                2,
                2,
                1,
                None,
                None,
                False,
                True,
                2,
                2,
                False,
            ),
        )
    chat = ChatRecord(
        1,
        day,
        phase,
        "opaque-public",
        "opaque-peer",
        "peer",
        "must be retained for this trigger",
    )
    source = evidence_ref_for_record(chat, _bound())
    failed_snapshot = _snapshot(
        version=2,
        seq=2,
        day=day,
        phase=phase,
        retention=_retention((chat,)),
    )
    with pytest.raises(
        DiscussionStateError,
        match="cannot retain required projection evidence",
    ):
        store.capture(
            _views((chat,), snapshot=failed_snapshot, transport=gap),
            _trigger(
                generation=generation,
                day=day,
                phase=phase,
                source=source,
            ),
        )

    assert not store.is_closed
    assert store.snapshot == before
    assert canonical_sha256(store.snapshot) == before_hash
    with pytest.raises(DiscussionStateError, match="no proposal"):
        store.commit(old_stage)
    with pytest.raises(DiscussionStateError, match="not the current exact capture"):
        store.stage(
            old_capture,
            old_stage.request_id,
            proposal,
            _generation_ack(
                old_capture,
                proposal,
                status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
            ),
        )

    retry_snapshot = _snapshot(
        version=2,
        seq=2,
        day=day,
        phase=phase,
    )
    retried = store.capture(
        _views(snapshot=retry_snapshot, transport=gap),
        _trigger(generation=generation, day=day, phase=phase),
    )
    assert retried.capture_ordinal == 2
    if transition == "phase_change":
        assert (retried.state.day, retried.state.phase, retried.epoch) == (
            2,
            "opaque-next",
            0,
        )
    else:
        assert retried.epoch == 1
        assert retried.state.provenance.reset_reason is DiscussionResetReason.RECOVERY_GAP


def test_phase_change_and_recovery_gap_invalidate_stage_and_reset_model_state() -> None:
    phase_store = DiscussionStateStore(_bound())
    capture = phase_store.capture(_views(), _trigger())
    proposal = _proposal(capture)
    old_stage = phase_store.stage(
        capture,
        f"phase6:{capture.capture_id}",
        proposal,
        _generation_ack(
            capture,
            proposal,
            status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        ),
    )
    changed = phase_store.capture(
        _views(snapshot=_snapshot(version=2, seq=2, day=2, phase="opaque-next")),
        _trigger(day=2, phase="opaque-next"),
    )
    assert changed.state.revision == 0
    with pytest.raises(DiscussionStateError, match="no proposal"):
        phase_store.commit(old_stage)

    reset_store = DiscussionStateStore(_bound())
    initial = reset_store.capture(_views(), _trigger(generation=1))
    assessment = AssessmentUpdate("opaque-peer", 80, 20, 90, ())
    model_proposal = _proposal(initial, assessment_updates=(assessment,))
    committed = reset_store.commit(
        reset_store.stage(
            initial,
            f"phase6:{initial.capture_id}",
            model_proposal,
            _generation_ack(
                initial,
                model_proposal,
                status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
            ),
        )
    )
    reset_store.finish_no_action(committed)
    staged_capture = reset_store.capture(
        _views(snapshot=_snapshot(version=2, seq=2)),
        _trigger(generation=1),
    )
    staged_proposal = _proposal(staged_capture)
    reset_store.stage(
        staged_capture,
        f"phase6:{staged_capture.capture_id}",
        staged_proposal,
        _generation_ack(
            staged_capture,
            staged_proposal,
            status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        ),
    )
    gap = ResumeRecoveryBarrier(2, 3, 2, 2, None, None, False, True, 3, 3, False)
    rebuilt = reset_store.capture(
        _views(snapshot=_snapshot(version=3, seq=3), transport=(gap,)),
        _trigger(generation=2),
    )
    assert rebuilt.epoch == 1
    assert rebuilt.state.revision == 0
    assert rebuilt.state.assessments == ()
    assert rebuilt.state.recent_semantic_turns == ()


def test_phase_scoped_strategy_clears_but_game_scoped_model_state_survives_phase() -> None:
    store = DiscussionStateStore(_bound())
    capture = store.capture(_views(), _trigger())
    strategy = StrategyUpdate("PHASE", StrategyMode.TEST_CLAIM, ("opaque-peer",), ())
    assessment = AssessmentUpdate("opaque-peer", 60, 40, 70, ())
    proposal = _proposal(
        capture,
        assessment_updates=(assessment,),
        strategy_update=strategy,
    )
    commit = store.commit(
        store.stage(
            capture,
            f"phase6:{capture.capture_id}",
            proposal,
            _generation_ack(
                capture,
                proposal,
                status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
            ),
        )
    )
    store.finish_no_action(commit)
    assert store.snapshot.strategy is not None
    changed = store.capture(
        _views(snapshot=_snapshot(version=2, seq=2, day=2, phase="opaque-next")),
        _trigger(day=2, phase="opaque-next"),
    )
    assert changed.state.strategy is None
    assert tuple(item.player_id for item in changed.state.assessments) == ("opaque-peer",)
    assert len(changed.state.recent_semantic_turns) == 1
