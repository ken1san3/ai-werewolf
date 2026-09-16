from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from functools import wraps
import time

import pytest

from ai_client.brain import (
    BrainDispatchResult,
    BrainInvocationArbiter,
    BrainInvocationPriority,
    ChatDecision,
    CoDeclareDecision,
    DecisionOutcome,
    DecisionStatus,
    DispatchDeadline,
)
from ai_client.discussion import (
    AuthorizedChatChannelContext,
    AuthorizedDiscussionContext,
    BoundDiscussionContext,
    CountParityWinCondition,
    DiscussionDispatchCorrelation,
    DiscussionObservationStatus,
    DiscussionTerminalReason,
    DiscussionTrigger,
    DiscussionValidationError,
    EvidenceRecordKind,
    EvidenceRef,
    EvidenceVisibility,
    ObservationAck,
    RelationKind,
    SpeechActKind,
    SpeechActRelationHypothesis,
    canonical_sha256,
)
from ai_client.network import (
    ActionRejected,
    ChatAction,
    ClientLifecycle,
    ClientSnapshot,
    CoDeclareAction,
    CoReportAction,
    PhaseDeadlineReached,
    PhaseTimingMapped,
    SendReceipt,
    ServerEvent,
)
from ai_client.network.types import immutable_mapping
from ai_client.reaction_chat import (
    CoGenerationState,
    DeterministicSpeakingFrequencyPolicy,
    ReactionChatConfig,
    ReactionChatController,
    ReactionChatLifecycle,
    ReactionOutcomeStatus,
    ReactionTriggerKind,
    SpeakingProfile,
)
from ai_client.world import (
    ActionRejectionObservation,
    TransportObservationRetention,
    WorldState,
    WorldStateConfig,
)


OPAQUE_PRIVATE = "opaque-private"
OPAQUE_PUBLIC = "opaque-public"
OPAQUE_SELF = "opaque-self"
OPAQUE_PEER = "opaque-peer"


def _bound_context(
    channels: tuple[AuthorizedChatChannelContext, ...] = (
        AuthorizedChatChannelContext(OPAQUE_PRIVATE, False),
        AuthorizedChatChannelContext(OPAQUE_PUBLIC, True),
    ),
) -> BoundDiscussionContext:
    context = AuthorizedDiscussionContext(
        schema_version="aiwolf.discussion-context.v1",
        game_id="opaque-game",
        player_id=OPAQUE_SELF,
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
        chat_channels=channels,
        knows_teammates=False,
        authorized_known_player_ids=(),
        known_players_complete=False,
    )
    return BoundDiscussionContext(
        manifest_sha256="a" * 64,
        context_sha256=canonical_sha256(context),
        context=context,
    )


def _event(message_type: str, seq: int, payload: dict[str, object]) -> ServerEvent:
    return ServerEvent(
        type=message_type,
        protocol_version="1.0",
        event_id=f"opaque-event-{seq}",
        game_id="opaque-game",
        seq=seq,
        timestamp=100,
        payload=immutable_mapping(payload),
    )


class _Source:
    def __init__(self, actions: tuple[object, ...]) -> None:
        self._snapshot = ClientSnapshot(
            lifecycle=ClientLifecycle.CONNECTED,
            player_id=OPAQUE_SELF,
            last_seq=1,
            actions=actions,
            connection_generation=1,
            action_generation=1,
        )

    def snapshot(self) -> ClientSnapshot:
        return self._snapshot

    def advance(self, seq: int) -> None:
        self._snapshot = replace(self._snapshot, last_seq=seq)

    async def events(self):
        await asyncio.Future()
        yield  # pragma: no cover


@dataclass(frozen=True)
class _Invocation:
    priority: BrainInvocationPriority
    allowed_handles: tuple[object, ...]
    deadline: DispatchDeadline


@dataclass(frozen=True)
class _FinalizationCall:
    owner: str
    correlation: DiscussionDispatchCorrelation
    status: DiscussionObservationStatus
    reason: DiscussionTerminalReason
    evidence: EvidenceRef | None


class _ScriptedArbiter(BrainInvocationArbiter):
    """Public-boundary fake; it never inspects Brain/controller private state."""

    def __init__(
        self,
        *,
        world: WorldState,
        source: _Source,
        context: BoundDiscussionContext,
        scripts: dict[str, str] | None = None,
        semantic_reaction_score: int = 50,
    ) -> None:
        self.world = world
        self.source = source
        self.context = context
        self.scripts = scripts or {}
        self.semantic_reaction_score = semantic_reaction_score
        self.invocations: list[_Invocation] = []
        self.finalizations: list[_FinalizationCall] = []
        self.network_sends: list[object] = []
        self.brain_starts = 0
        self.semantic_mutations = 0

    async def invoke(
        self,
        *,
        owner: str,
        priority: BrainInvocationPriority,
        allowed_handles: tuple[object, ...],
        timeout_seconds: float,
        dispatch_deadline: DispatchDeadline,
        on_brain_start=None,
    ) -> BrainDispatchResult:
        assert owner == "reaction_chat"
        assert timeout_seconds > 0
        trigger = dispatch_deadline.discussion_trigger
        assert isinstance(trigger, DiscussionTrigger)
        self.invocations.append(_Invocation(priority, allowed_handles, dispatch_deadline))
        script = self.scripts.get(trigger.kind, "none")
        if script == "unprojected":
            return BrainDispatchResult(
                DecisionOutcome(
                    DecisionStatus.BRAIN_FAILED,
                    invocation_started=False,
                    error_type="DiscussionStateError",
                ),
                None,
            )
        if on_brain_start is not None:
            on_brain_start()
        self.brain_starts += 1
        if script in {"try_public", "public_inference"}:
            return BrainDispatchResult(
                DecisionOutcome(
                    DecisionStatus.INVALID_DECISION,
                    invocation_started=True,
                    error_type="DiscussionStateError",
                ),
                None,
            )
        if script == "none":
            return BrainDispatchResult(
                DecisionOutcome(DecisionStatus.NO_DECISION, invocation_started=True),
                None,
            )

        handle = allowed_handles[0]
        option_id = "action:0"
        ordinal = len(self.invocations)
        request_event_id = f"opaque-request-{ordinal}"
        receipt = SendReceipt(request_event_id, trigger.connection_generation)
        if isinstance(handle, ChatAction):
            decision: object = ChatDecision(option_id, f"opaque-response-{ordinal}")
            action = "chat"
            attempt_action = "chat.send"
        elif isinstance(handle, CoDeclareAction):
            decision = CoDeclareDecision(option_id, "opaque-role", "opaque-comment")
            action = "co_declare"
            attempt_action = "co.declare"
        else:  # pragma: no cover - the controller must never offer contextful CO report
            raise AssertionError(type(handle).__name__)
        correlation = DiscussionDispatchCorrelation(
            capture_id=f"{ordinal:064x}",
            request_id=f"phase6:{ordinal:064x}",
            context_sha256=self.context.context_sha256,
            before_state_sha256="b" * 64,
            after_state_sha256="c" * 64,
            proposal_sha256="d" * 64,
            generation_audit_sequence=ordinal,
            base_revision=ordinal - 1,
            committed_revision=ordinal,
            action=action,  # type: ignore[arg-type]
            option_id=option_id,
            request_event_id=request_event_id,
            send_connection_generation=trigger.connection_generation,
        )
        self.network_sends.append(decision)
        self._apply_script(script, handle, decision, correlation)
        return BrainDispatchResult(
            DecisionOutcome(
                DecisionStatus.SENT,
                option_id=option_id,
                receipt=receipt,
                invocation_started=True,
                request_event_id=request_event_id,
                attempt_action=attempt_action,
                send_connection_generation=trigger.connection_generation,
                discussion=correlation,
            ),
            decision,  # type: ignore[arg-type]
        )

    def _next_seq(self) -> int:
        return self.source.snapshot().last_seq + 1

    def _apply_script(
        self,
        script: str,
        handle: object,
        decision: object,
        correlation: DiscussionDispatchCorrelation,
    ) -> None:
        if script in {"wait", "stop"}:
            return
        if script in {"accept", "accept_gap", "accept_phase", "both", "duplicate"}:
            repetitions = 2 if script == "duplicate" else 1
            for _ in range(repetitions):
                if isinstance(handle, ChatAction) and isinstance(decision, ChatDecision):
                    _emit_chat(
                        self.world,
                        self.source,
                        channel=handle.channel,
                        player_id=OPAQUE_SELF,
                        message=decision.message,
                    )
                elif isinstance(handle, CoDeclareAction) and isinstance(
                    decision, CoDeclareDecision
                ):
                    _emit_co(
                        self.world,
                        self.source,
                        claimed_role_id=decision.claimed_role_id,
                        comment=decision.comment,
                    )
        if script in {"reject", "both", "missing_id"}:
            _emit_rejection(
                self.world,
                self.source,
                action="chat.send" if isinstance(handle, ChatAction) else "co.declare",
                request_event_id=(
                    None if script == "missing_id" else correlation.request_event_id
                ),
            )
        if script in {"gap", "accept_gap"}:
            _emit_rejection(
                self.world,
                self.source,
                action="vote.cast",
                request_event_id="opaque-other-1",
            )
            _emit_rejection(
                self.world,
                self.source,
                action="vote.cast",
                request_event_id="opaque-other-2",
            )
        if script in {"phase", "accept_phase"}:
            _emit_phase_change(self.world, self.source)
        if script == "deadline":
            _emit_deadline(self.world)

    async def finalize_discussion_observation(
        self,
        *,
        owner: str,
        correlation: DiscussionDispatchCorrelation,
        status: DiscussionObservationStatus,
        reason: DiscussionTerminalReason,
        evidence: EvidenceRef | None = None,
    ) -> ObservationAck:
        call = _FinalizationCall(owner, correlation, status, reason, evidence)
        self.finalizations.append(call)
        return ObservationAck(
            capture_id=correlation.capture_id,
            request_id=correlation.request_id,
            base_revision=correlation.base_revision,
            committed_revision=correlation.committed_revision,
            context_sha256=correlation.context_sha256,
            before_state_sha256=correlation.before_state_sha256,
            after_state_sha256=correlation.after_state_sha256,
            proposal_sha256=correlation.proposal_sha256,
            generation_audit_sequence=correlation.generation_audit_sequence,
            action=correlation.action,
            option_id=correlation.option_id,
            request_event_id=correlation.request_event_id,
            send_connection_generation=correlation.send_connection_generation,
            status=status,
            authoritative_evidence=evidence,
        )


@dataclass(frozen=True)
class _Harness:
    reaction: ReactionChatController
    arbiter: _ScriptedArbiter
    world: WorldState
    source: _Source


def _make_harness(
    *,
    handle_channels: tuple[str, ...] = (OPAQUE_PRIVATE,),
    context: BoundDiscussionContext | None = None,
    with_co: bool = False,
    with_co_report: bool = False,
    scripts: dict[str, str] | None = None,
    config: ReactionChatConfig | None = None,
    frequency_policy: DeterministicSpeakingFrequencyPolicy | None = None,
    world_config: WorldStateConfig = WorldStateConfig(),
    transport_retention: TransportObservationRetention = TransportObservationRetention(),
    semantic_reaction_score: int = 50,
) -> _Harness:
    bound = context or _bound_context()
    common = {
        "connection_generation": 1,
        "action_generation": 1,
        "phase": "opaque-phase",
        "day": 1,
    }
    actions: tuple[object, ...] = tuple(
        ChatAction(**common, type="chat", channel=channel)
        for channel in handle_channels
    )
    if with_co:
        actions += (
            CoDeclareAction(
                **common,
                type="co_declare",
                claimed_role_ids=("opaque-role",),
            ),
        )
    if with_co_report:
        actions += (CoReportAction(**common, type="co_report"),)
    source = _Source(actions)
    world = WorldState(
        source,
        config=world_config,
        transport_retention=transport_retention,
    )
    action_payload: list[dict[str, object]] = [
        {"type": "chat", "channel": channel} for channel in handle_channels
    ]
    if with_co:
        action_payload.append(
            {"type": "co_declare", "claimed_role_ids": ["opaque-role"]}
        )
    if with_co_report:
        action_payload.append({"type": "co_report"})
    world._consume(  # noqa: SLF001 - integration fixture feeds typed World input
        _event(
            "game.state_sync",
            1,
            {
                "players": [
                    {"player_id": OPAQUE_SELF, "display_name": "opaque-self-display"},
                    {"player_id": OPAQUE_PEER, "display_name": "opaque-peer-display"},
                ],
                "deaths": [],
                "action_state": {
                    "phase": "opaque-phase",
                    "day": 1,
                    "phase_ends_at": 200,
                    "actions": action_payload,
                },
                "self": {
                    "player_id": OPAQUE_SELF,
                    "role_id": "opaque-role",
                    "modifier_ids": [],
                },
                "revealed_roles": [],
                "history": [],
            },
        )
    )
    now = time.monotonic()
    world._consume(  # noqa: SLF001
        PhaseTimingMapped(
            "opaque-phase", 1, 1, 1, 1, 100, 200, now, now + 10
        )
    )
    arbiter = _ScriptedArbiter(
        world=world,
        source=source,
        context=bound,
        scripts=scripts,
        semantic_reaction_score=semantic_reaction_score,
    )
    reaction = ReactionChatController(
        world=world,
        invoker=arbiter,
        master_seed=91,
        config=config
        or ReactionChatConfig(
            initial_jitter_seconds=(0, 0),
            reaction_jitter_seconds=(0, 0),
            minimum_accepted_chat_interval_seconds=0,
            deadline_guard_seconds=0.01,
            minimum_start_budget_seconds=0.001,
            brain_timeout_seconds=0.2,
        ),
        frequency_policy=frequency_policy,
        discussion_context=bound,
    )
    return _Harness(reaction, arbiter, world, source)


def _emit_chat(
    world: WorldState,
    source: _Source,
    *,
    channel: str,
    player_id: str | None,
    message: str,
) -> None:
    seq = source.snapshot().last_seq + 1
    source.advance(seq)
    world._consume(  # noqa: SLF001
        _event(
            "chat.message",
            seq,
            {
                "channel": channel,
                "message": {
                    "player_id": player_id,
                    "display_name": None if player_id is None else f"{player_id}-display",
                    "message": message,
                },
            },
        )
    )


def _emit_co(
    world: WorldState,
    source: _Source,
    *,
    claimed_role_id: str,
    comment: str,
) -> None:
    seq = source.snapshot().last_seq + 1
    source.advance(seq)
    world._consume(  # noqa: SLF001
        _event(
            "game.event",
            seq,
            {
                "event_type": "CO_DECLARED",
                "event_payload": {
                    "player_id": OPAQUE_SELF,
                    "claimed_role_id": claimed_role_id,
                    "comment": comment,
                },
            },
        )
    )


def _emit_rejection(
    world: WorldState,
    source: _Source,
    *,
    action: str,
    request_event_id: str | None,
) -> None:
    seq = source.snapshot().last_seq + 1
    source.advance(seq)
    world._consume(  # noqa: SLF001
        _event(
            "action.rejected",
            seq,
            {"action": action, "reason": "opaque-rejection"},
        )
    )
    world._consume(  # noqa: SLF001
        ActionRejected(
            action,
            "opaque-rejection",
            seq,
            1,
            time.monotonic(),
            request_event_id=request_event_id,
        )
    )


def _emit_phase_change(world: WorldState, source: _Source) -> None:
    seq = source.snapshot().last_seq + 1
    source.advance(seq)
    world._consume(  # noqa: SLF001
        _event(
            "game.event",
            seq,
            {
                "event_type": "PHASE_STARTED",
                "event_payload": {
                    "phase": "opaque-next-phase",
                    "day": 1,
                    "phase_ends_at": 300,
                },
            },
        )
    )


def _emit_deadline(world: WorldState) -> None:
    deadline = world.transport_observations().current_deadline
    assert deadline is not None
    assert deadline.local_deadline_monotonic is not None
    world._consume(  # noqa: SLF001
        PhaseDeadlineReached(
            "opaque-phase",
            1,
            1,
            1,
            deadline.local_deadline_monotonic,
            time.monotonic(),
        )
    )


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout)


def _async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


def _frequency_policy() -> DeterministicSpeakingFrequencyPolicy:
    return DeterministicSpeakingFrequencyPolicy(
        profile=SpeakingProfile(
            talkativeness=1,
            ordinary_event_importance=1,
            direct_mention_importance=1,
            initial_event_importance=1,
            cooldown_seconds=0,
            max_trigger_evaluations_per_phase=32,
            repetition_window=8,
        ),
        master_seed=91,
    )


@_async_test
async def test_p6c_trigger_mapping_and_opaque_visibility_uses_sealed_context() -> None:
    private = _make_harness(with_co=True)
    private.reaction.start()
    try:
        await _wait_until(lambda: len(private.arbiter.invocations) == 2)
        _emit_chat(
            private.world,
            private.source,
            channel=OPAQUE_PRIVATE,
            player_id=OPAQUE_PEER,
            message="opaque-private-source",
        )
        await _wait_until(lambda: len(private.arbiter.invocations) == 3)
        triggers = tuple(
            call.deadline.discussion_trigger for call in private.arbiter.invocations
        )
        assert tuple(item.kind for item in triggers if item is not None) == (
            "CO_OPPORTUNITY",
            "INITIAL_CHAT",
            "PEER_CHAT",
        )
        assert triggers[0] == DiscussionTrigger(
            "reaction_chat", "CO_OPPORTUNITY", 1, "opaque-phase", 1, 1, 1, None
        )
        assert triggers[1] == DiscussionTrigger(
            "reaction_chat", "INITIAL_CHAT", 1, "opaque-phase", 1, 1, 1, None
        )
        assert triggers[2] == DiscussionTrigger(
            "reaction_chat",
            "PEER_CHAT",
            1,
            "opaque-phase",
            1,
            1,
            1,
            EvidenceRef(
                EvidenceRecordKind.CHAT,
                1,
                EvidenceVisibility.AUTHORIZED_PRIVATE,
            ),
        )
    finally:
        await private.reaction.stop()

    public = _make_harness(handle_channels=(OPAQUE_PUBLIC,))
    public.reaction.start()
    try:
        await _wait_until(lambda: len(public.arbiter.invocations) == 1)
        _emit_chat(
            public.world,
            public.source,
            channel=OPAQUE_PUBLIC,
            player_id=OPAQUE_PEER,
            message="opaque-public-source",
        )
        await _wait_until(lambda: len(public.arbiter.invocations) == 2)
        trigger = public.arbiter.invocations[-1].deadline.discussion_trigger
        assert trigger is not None
        assert trigger.source == EvidenceRef(
            EvidenceRecordKind.CHAT, 1, EvidenceVisibility.PUBLIC
        )
    finally:
        await public.reaction.stop()

    co = _make_harness(with_co=True, scripts={"CO_OPPORTUNITY": "accept"})
    co.reaction.start()
    try:
        await _wait_until(
            lambda: any(
                outcome.discussion is not None
                for outcome in co.reaction.snapshot().outcomes
            )
        )
        await _wait_until(lambda: len(co.arbiter.invocations) == 2)
        co_outcome = next(
            outcome
            for outcome in co.reaction.snapshot().outcomes
            if outcome.discussion is not None
        )
        co_finalization = co.arbiter.finalizations[0]
        assert co_outcome.status is ReactionOutcomeStatus.ACCEPTED
        assert co_outcome.action_kind == "co.declare"
        assert co_outcome.discussion == co_finalization.correlation
        assert co_finalization.status is DiscussionObservationStatus.ACCEPTED
        assert co_finalization.reason is DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED
        assert co_finalization.evidence == EvidenceRef(
            EvidenceRecordKind.CO_DECLARATION,
            1,
            EvidenceVisibility.PUBLIC,
        )
        assert co.reaction.snapshot().chat_brain_invocations == 1
    finally:
        await co.reaction.stop()

    report_only = _make_harness(handle_channels=(), with_co_report=True)
    report_only.reaction.start()
    try:
        await asyncio.sleep(0.03)
        assert report_only.arbiter.invocations == []
        assert report_only.arbiter.network_sends == []
        assert report_only.reaction.snapshot().co_generation_state is None
    finally:
        await report_only.reaction.stop()


@_async_test
async def test_p6c_private_peer_same_channel_response_is_accepted_and_linked() -> None:
    harness = _make_harness(scripts={"PEER_CHAT": "accept"})
    harness.reaction.start()
    try:
        await _wait_until(lambda: len(harness.arbiter.invocations) == 1)
        _emit_chat(
            harness.world,
            harness.source,
            channel=OPAQUE_PRIVATE,
            player_id=OPAQUE_PEER,
            message="opaque-question",
        )
        await _wait_until(
            lambda: any(
                outcome.discussion is not None
                for outcome in harness.reaction.snapshot().outcomes
            )
        )
        snapshot = harness.reaction.snapshot()
        outcome = next(
            outcome for outcome in reversed(snapshot.outcomes) if outcome.discussion is not None
        )
        finalization = harness.arbiter.finalizations[0]
        assert outcome.status is ReactionOutcomeStatus.ACCEPTED
        assert outcome.action_kind == "chat.send"
        assert outcome.discussion == finalization.correlation
        assert finalization.status is DiscussionObservationStatus.ACCEPTED
        assert finalization.reason is DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED
        assert finalization.evidence == EvidenceRef(
            EvidenceRecordKind.CHAT,
            2,
            EvidenceVisibility.AUTHORIZED_PRIVATE,
        )
        assert snapshot.chat_brain_invocations == 2
        assert snapshot.send_count == 1
        assert snapshot.accepted_count == 1
        assert len(harness.arbiter.network_sends) == 1
    finally:
        await harness.reaction.stop()


@_async_test
async def test_p6c_private_peer_different_or_public_channel_fails_before_mutation_send() -> None:
    different = _make_harness(handle_channels=(OPAQUE_PUBLIC,))
    different.reaction.start()
    try:
        await _wait_until(lambda: len(different.arbiter.invocations) == 1)
        _emit_chat(
            different.world,
            different.source,
            channel=OPAQUE_PRIVATE,
            player_id=OPAQUE_PEER,
            message="opaque-private-source",
        )
        await asyncio.sleep(0.03)
        assert len(different.arbiter.invocations) == 1
        assert different.arbiter.network_sends == []
        assert different.arbiter.semantic_mutations == 0
    finally:
        await different.reaction.stop()

    public_attempt = _make_harness(
        handle_channels=(OPAQUE_PRIVATE, OPAQUE_PUBLIC),
        scripts={"PEER_CHAT": "try_public"},
    )
    public_attempt.reaction.start()
    try:
        await _wait_until(lambda: len(public_attempt.arbiter.invocations) == 1)
        _emit_chat(
            public_attempt.world,
            public_attempt.source,
            channel=OPAQUE_PRIVATE,
            player_id=OPAQUE_PEER,
            message="opaque-private-source",
        )
        await _wait_until(lambda: len(public_attempt.arbiter.invocations) == 2)
        offered = public_attempt.arbiter.invocations[-1].allowed_handles
        assert tuple(handle.channel for handle in offered if isinstance(handle, ChatAction)) == (
            OPAQUE_PRIVATE,
        )
        assert public_attempt.arbiter.network_sends == []
        assert public_attempt.arbiter.semantic_mutations == 0
        assert public_attempt.reaction.snapshot().outcomes[-1].status is ReactionOutcomeStatus.INVALID
    finally:
        await public_attempt.reaction.stop()


@_async_test
async def test_p6c_private_source_cannot_authorize_public_inference() -> None:
    private_reference = EvidenceRef(
        EvidenceRecordKind.CHAT,
        1,
        EvidenceVisibility.AUTHORIZED_PRIVATE,
    )
    with pytest.raises(
        DiscussionValidationError, match="public relation evidence must be public"
    ):
        SpeechActRelationHypothesis(
            SpeechActKind.RELATION_HYPOTHESIS,
            OPAQUE_SELF,
            OPAQUE_PEER,
            RelationKind.ACCUSES,
            80,
            (private_reference,),
        )

    harness = _make_harness(scripts={"PEER_CHAT": "public_inference"})
    harness.reaction.start()
    try:
        await _wait_until(lambda: len(harness.arbiter.invocations) == 1)
        _emit_chat(
            harness.world,
            harness.source,
            channel=OPAQUE_PRIVATE,
            player_id=OPAQUE_PEER,
            message="opaque-private-source",
        )
        await _wait_until(lambda: len(harness.arbiter.invocations) == 2)
        trigger = harness.arbiter.invocations[-1].deadline.discussion_trigger
        assert trigger is not None
        assert trigger.source == private_reference
        assert harness.arbiter.network_sends == []
        assert harness.arbiter.semantic_mutations == 0
        assert harness.arbiter.finalizations == []
        assert harness.reaction.snapshot().outcomes[-1].status is ReactionOutcomeStatus.INVALID
    finally:
        await harness.reaction.stop()


@pytest.mark.parametrize(
    ("script", "expected_status", "expected_reason", "expected_evidence"),
    (
        (
            "accept",
            ReactionOutcomeStatus.ACCEPTED,
            DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            EvidenceRef(
                EvidenceRecordKind.CHAT,
                2,
                EvidenceVisibility.AUTHORIZED_PRIVATE,
            ),
        ),
        (
            "accept_phase",
            ReactionOutcomeStatus.ACCEPTED,
            DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            EvidenceRef(
                EvidenceRecordKind.CHAT,
                2,
                EvidenceVisibility.AUTHORIZED_PRIVATE,
            ),
        ),
        (
            "reject",
            ReactionOutcomeStatus.REJECTED,
            DiscussionTerminalReason.AUTHORITATIVE_REJECTED,
            EvidenceRef(
                EvidenceRecordKind.ACTION_REJECTION,
                2,
                EvidenceVisibility.AUTHORIZED_PRIVATE,
            ),
        ),
        (
            "both",
            ReactionOutcomeStatus.TRANSPORT_GAP,
            DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
            None,
        ),
        (
            "duplicate",
            ReactionOutcomeStatus.TRANSPORT_GAP,
            DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
            None,
        ),
        (
            "missing_id",
            ReactionOutcomeStatus.TRANSPORT_GAP,
            DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
            EvidenceRef(
                EvidenceRecordKind.ACTION_REJECTION,
                2,
                EvidenceVisibility.AUTHORIZED_PRIVATE,
            ),
        ),
        (
            "gap",
            ReactionOutcomeStatus.TRANSPORT_GAP,
            DiscussionTerminalReason.RECOVERY_GAP,
            None,
        ),
        (
            "accept_gap",
            ReactionOutcomeStatus.TRANSPORT_GAP,
            DiscussionTerminalReason.RECOVERY_GAP,
            None,
        ),
        (
            "phase",
            ReactionOutcomeStatus.TRANSPORT_GAP,
            DiscussionTerminalReason.PHASE_CHANGED,
            EvidenceRef(
                EvidenceRecordKind.PHASE_TRANSITION,
                2,
                EvidenceVisibility.PUBLIC,
            ),
        ),
        (
            "deadline",
            ReactionOutcomeStatus.TRANSPORT_GAP,
            DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
            EvidenceRef(
                EvidenceRecordKind.PHASE_DEADLINE_REACHED,
                2,
                EvidenceVisibility.AUTHORIZED_PRIVATE,
            ),
        ),
        (
            "stop",
            ReactionOutcomeStatus.TRANSPORT_GAP,
            DiscussionTerminalReason.OWNER_STOPPED,
            None,
        ),
    ),
)
@_async_test
async def test_p6c_accept_reject_ambiguity_gap_phase_and_stop_terminal_matrix(
    script: str,
    expected_status: ReactionOutcomeStatus,
    expected_reason: DiscussionTerminalReason,
    expected_evidence: EvidenceRef | None,
) -> None:
    harness = _make_harness(
        scripts={"PEER_CHAT": script},
        transport_retention=(
            TransportObservationRetention(max_records=1, max_bytes=4096)
            if script in {"gap", "accept_gap"}
            else TransportObservationRetention()
        ),
    )
    harness.reaction.start()
    stopped = False
    try:
        await _wait_until(lambda: len(harness.arbiter.invocations) == 1)
        _emit_chat(
            harness.world,
            harness.source,
            channel=OPAQUE_PRIVATE,
            player_id=OPAQUE_PEER,
            message="opaque-trigger",
        )
        await _wait_until(lambda: len(harness.arbiter.network_sends) == 1)
        if script == "stop":
            await harness.reaction.stop()
            stopped = True
        else:
            await _wait_until(
                lambda: any(
                    outcome.discussion is not None
                    for outcome in harness.reaction.snapshot().outcomes
                )
            )
        finalization = harness.arbiter.finalizations[0]
        outcome = next(
            outcome
            for outcome in reversed(harness.reaction.snapshot().outcomes)
            if outcome.discussion is not None
        )
        assert outcome.status is expected_status
        assert outcome.discussion == finalization.correlation
        assert finalization.status is (
            DiscussionObservationStatus.ACCEPTED
            if expected_status is ReactionOutcomeStatus.ACCEPTED
            else DiscussionObservationStatus.REJECTED
            if expected_status is ReactionOutcomeStatus.REJECTED
            else DiscussionObservationStatus.RECOVERY_UNKNOWN
        )
        assert finalization.reason is expected_reason
        assert finalization.evidence == expected_evidence
        assert len(harness.arbiter.finalizations) == 1
    finally:
        if not stopped:
            await harness.reaction.stop()


@_async_test
async def test_p6c_source_missing_evicted_stale_self_or_unprojected_suppresses_backend() -> None:
    missing = _make_harness()
    missing.reaction.start()
    try:
        await _wait_until(lambda: len(missing.arbiter.invocations) == 1)
        _emit_chat(
            missing.world,
            missing.source,
            channel=OPAQUE_PRIVATE,
            player_id=None,
            message="opaque-missing-actor",
        )
        await asyncio.sleep(0.03)
        assert len(missing.arbiter.invocations) == 1
    finally:
        await missing.reaction.stop()

    self_source = _make_harness()
    self_source.reaction.start()
    try:
        await _wait_until(lambda: len(self_source.arbiter.invocations) == 1)
        _emit_chat(
            self_source.world,
            self_source.source,
            channel=OPAQUE_PRIVATE,
            player_id=OPAQUE_SELF,
            message="opaque-self-source",
        )
        await asyncio.sleep(0.03)
        assert len(self_source.arbiter.invocations) == 1
    finally:
        await self_source.reaction.stop()

    delayed = ReactionChatConfig(
        initial_jitter_seconds=(0, 0),
        reaction_jitter_seconds=(0.08, 0.08),
        minimum_accepted_chat_interval_seconds=0,
        deadline_guard_seconds=0.01,
        minimum_start_budget_seconds=0.001,
        brain_timeout_seconds=0.2,
    )
    evicted = _make_harness(
        config=delayed,
        world_config=WorldStateConfig(max_history_records=1, max_history_bytes=4096),
    )
    evicted.reaction.start()
    try:
        await _wait_until(lambda: len(evicted.arbiter.invocations) == 1)
        _emit_chat(
            evicted.world,
            evicted.source,
            channel=OPAQUE_PRIVATE,
            player_id=OPAQUE_PEER,
            message="opaque-evicted-source",
        )
        await asyncio.sleep(0.02)
        _emit_chat(
            evicted.world,
            evicted.source,
            channel=OPAQUE_PRIVATE,
            player_id=OPAQUE_SELF,
            message="opaque-retained-self",
        )
        await asyncio.sleep(0.09)
        assert len(evicted.arbiter.invocations) == 1
    finally:
        await evicted.reaction.stop()

    stale = _make_harness(config=delayed)
    stale.reaction.start()
    try:
        await _wait_until(lambda: len(stale.arbiter.invocations) == 1)
        _emit_chat(
            stale.world,
            stale.source,
            channel=OPAQUE_PRIVATE,
            player_id=OPAQUE_PEER,
            message="opaque-stale-source",
        )
        await asyncio.sleep(0.02)
        _emit_phase_change(stale.world, stale.source)
        await asyncio.sleep(0.09)
        assert len(stale.arbiter.invocations) == 1
    finally:
        await stale.reaction.stop()

    unprojected = _make_harness(scripts={"PEER_CHAT": "unprojected"})
    unprojected.reaction.start()
    try:
        await _wait_until(lambda: len(unprojected.arbiter.invocations) == 1)
        _emit_chat(
            unprojected.world,
            unprojected.source,
            channel=OPAQUE_PRIVATE,
            player_id=OPAQUE_PEER,
            message="opaque-unprojected-source",
        )
        await _wait_until(lambda: len(unprojected.arbiter.invocations) == 2)
        assert unprojected.arbiter.brain_starts == 1
        assert unprojected.arbiter.network_sends == []
        assert unprojected.arbiter.semantic_mutations == 0
        assert unprojected.reaction.snapshot().outcomes[-1].status is ReactionOutcomeStatus.BRAIN_FAILED
    finally:
        await unprojected.reaction.stop()


@_async_test
async def test_p6c_safety_frequency_coalescing_and_two_chat_cap_are_unchanged() -> None:
    harness = _make_harness(
        with_co=True,
        frequency_policy=_frequency_policy(),
    )
    harness.reaction.start()
    try:
        await _wait_until(lambda: len(harness.arbiter.invocations) == 2)
        _emit_chat(
            harness.world,
            harness.source,
            channel=OPAQUE_PRIVATE,
            player_id=OPAQUE_PEER,
            message="opaque-coalesced-one",
        )
        _emit_chat(
            harness.world,
            harness.source,
            channel=OPAQUE_PRIVATE,
            player_id=OPAQUE_PEER,
            message="opaque-coalesced-two",
        )
        await _wait_until(lambda: len(harness.arbiter.invocations) == 3)
        _emit_chat(
            harness.world,
            harness.source,
            channel=OPAQUE_PRIVATE,
            player_id=OPAQUE_PEER,
            message="opaque-over-cap",
        )
        await asyncio.sleep(0.03)
        snapshot = harness.reaction.snapshot()
        triggers = tuple(
            call.deadline.discussion_trigger.kind  # type: ignore[union-attr]
            for call in harness.arbiter.invocations
        )
        assert triggers == ("CO_OPPORTUNITY", "INITIAL_CHAT", "PEER_CHAT")
        assert snapshot.chat_brain_invocations == 2
        assert snapshot.frequency_state is not None
        assert snapshot.frequency_state.evaluation_count == 2
        assert snapshot.frequency_state.committed_brain_invocations == 2
        assert snapshot.co_generation_state == CoGenerationState(1, True, True)
        assert len(harness.arbiter.invocations) == 3
        assert harness.arbiter.network_sends == []
    finally:
        await harness.reaction.stop()


@_async_test
async def test_p6c_semantic_reaction_score_never_changes_frequency_or_admission() -> None:
    async def run(score: int):
        harness = _make_harness(
            frequency_policy=_frequency_policy(),
            semantic_reaction_score=score,
        )
        harness.reaction.start()
        try:
            await _wait_until(lambda: len(harness.arbiter.invocations) == 1)
            _emit_chat(
                harness.world,
                harness.source,
                channel=OPAQUE_PRIVATE,
                player_id=OPAQUE_PEER,
                message="opaque-score-independent-source",
            )
            await _wait_until(lambda: len(harness.arbiter.invocations) == 2)
            snapshot = harness.reaction.snapshot()
            reaction_outcome = snapshot.outcomes[-1]
            return (
                snapshot.frequency_state,
                reaction_outcome.frequency_evaluation_ordinal,
                reaction_outcome.frequency_event_importance,
                reaction_outcome.frequency_threshold,
                reaction_outcome.frequency_draw,
                reaction_outcome.frequency_source_fingerprint,
                tuple(call.priority for call in harness.arbiter.invocations),
                tuple(
                    call.deadline.discussion_trigger.kind  # type: ignore[union-attr]
                    for call in harness.arbiter.invocations
                ),
            )
        finally:
            await harness.reaction.stop()

    zero = await run(0)
    hundred = await run(100)
    assert zero == hundred
    assert zero[0] is not None
    assert zero[0].evaluation_count == 2
    assert zero[0].committed_brain_invocations == 2
    assert zero[1] == 2
    assert zero[6] == (
        BrainInvocationPriority.REACTION,
        BrainInvocationPriority.REACTION,
    )
    assert zero[7] == ("INITIAL_CHAT", "PEER_CHAT")
