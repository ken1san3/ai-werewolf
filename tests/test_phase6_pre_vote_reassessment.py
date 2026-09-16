from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import json
import unittest

from ai_client.brain import (
    AbilityDecision,
    BrainController,
    BrainDispatchResult,
    BrainInvocationArbiter,
    BrainInvocationPriority,
    BrainRunConfig,
    DecisionOutcome,
    DecisionStatus,
    DispatchDeadline,
    FeatureControllerExitReason,
    NoDecision,
    VoteDecision,
)
from ai_client.discussion import (
    AuthorizedChatChannelContext,
    AuthorizedDiscussionContext,
    BoundDiscussionContext,
    CountParityWinCondition,
    DiscussionDispatchCorrelation,
    DiscussionObservationStatus,
    DiscussionStateStore,
    DiscussionTerminalReason,
    EvidenceRecordKind,
    EvidenceRef,
    EvidenceVisibility,
    ObservationAck,
    PreVoteReassessment,
    canonical_sha256,
)
from ai_client.llm.decision import DecisionValidationError, parse_llm_output
from ai_client.llm.prompt import project_brain_input
from ai_client.llm.types import LLMBrainConfig
from ai_client.network import (
    AbilityAction,
    ActionAccepted,
    ActionRejected,
    ClientLifecycle,
    ClientSnapshot,
    PhaseTimingMapped,
    ResumeRecoveryCompleted,
    SendReceipt,
    ServerEvent,
    VoteAction,
)
from ai_client.network.types import immutable_mapping
from ai_client.vote_ability import (
    VoteAbilityController,
    VoteAbilityLifecycle,
    VoteAbilityOutcomeStatus,
)
from ai_client.world import TransportObservationRetention, WorldState


REQUEST_ID = "10000000-0000-4000-8000-000000000001"


def _server_event(message_type: str, seq: int, payload: dict) -> ServerEvent:
    return ServerEvent(
        type=message_type,
        protocol_version="1.1",
        event_id=f"00000000-0000-4000-8000-{seq:012d}",
        game_id="game-1",
        seq=seq,
        timestamp=100,
        payload=immutable_mapping(payload),
    )


def _sync_payload(
    *,
    phase: str,
    day: int,
    late_discussion: bool = False,
) -> dict:
    history = []
    if late_discussion:
        history.append(
            {
                "type": "chat.message",
                "payload": {
                    "channel": "opaque-public",
                    "message": {
                        "player_id": "p1",
                        "display_name": "P1",
                        "message": "opaque-late-signal",
                    },
                },
            }
        )
    return {
        "players": [
            {"player_id": "p0", "display_name": "P0"},
            {"player_id": "p1", "display_name": "P1"},
            {"player_id": "p2", "display_name": "P2"},
        ],
        "deaths": [],
        "action_state": {
            "phase": phase,
            "day": day,
            "phase_ends_at": 110,
            "actions": [],
        },
        "self": {"player_id": "p0", "role_id": "opaque-role", "modifier_ids": []},
        "revealed_roles": [],
        "history": history,
    }


def _bound() -> BoundDiscussionContext:
    context = AuthorizedDiscussionContext(
        schema_version="aiwolf.discussion-context.v1",
        game_id="game-1",
        player_id="p0",
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
        chat_channels=(AuthorizedChatChannelContext("opaque-public", True),),
        knows_teammates=False,
        authorized_known_player_ids=(),
        known_players_complete=False,
    )
    return BoundDiscussionContext(
        manifest_sha256="a" * 64,
        context_sha256=canonical_sha256(context),
        context=context,
    )


class _Source:
    def __init__(self, actions) -> None:
        self._snapshot = ClientSnapshot(
            lifecycle=ClientLifecycle.CONNECTED,
            player_id="p0",
            last_seq=1,
            actions=tuple(actions),
            connection_generation=actions[0].connection_generation,
            action_generation=actions[0].action_generation,
        )

    def snapshot(self) -> ClientSnapshot:
        return self._snapshot

    def update(self, **changes) -> None:
        self._snapshot = replace(self._snapshot, **changes)

    async def events(self):
        await asyncio.Future()
        yield  # pragma: no cover


class _UnusedBrain:
    async def decide(self, _request):
        return NoDecision()


class _UnusedSender:
    async def send_vote(self, *_args):  # pragma: no cover - fake arbiter bypasses sender
        raise AssertionError("unexpected sender call")

    async def send_ability(self, *_args):  # pragma: no cover - fake arbiter bypasses sender
        raise AssertionError("unexpected sender call")


class _AuditSink:
    async def write(self, record):
        return record


@dataclass(frozen=True)
class _InvocationCall:
    owner: str
    priority: BrainInvocationPriority
    allowed_handles: tuple[VoteAction | AbilityAction, ...]
    timeout_seconds: float
    dispatch_deadline: DispatchDeadline


@dataclass(frozen=True)
class _FinalizerCall:
    owner: str
    correlation: DiscussionDispatchCorrelation
    status: DiscussionObservationStatus
    reason: DiscussionTerminalReason
    evidence: EvidenceRef | None


class _RecordingArbiter(BrainInvocationArbiter):
    def __init__(
        self,
        *,
        world: WorldState,
        bound: BoundDiscussionContext | None,
        capture_semantics: bool = False,
        gate_finalizer: bool = False,
        finalizer_error: BaseException | None = None,
    ) -> None:
        state = DiscussionStateStore(bound) if capture_semantics and bound is not None else None
        controller = BrainController(
            world=world,
            sender=_UnusedSender(),
            brain=_UnusedBrain(),
            config=BrainRunConfig(max_decision_seconds=0.25),
            clock=lambda: 1.0,
            discussion_state=state,
            discussion_audit=_AuditSink() if state is not None else None,
        )
        super().__init__(controller=controller, clock=lambda: 1.0)
        self.bound = bound
        self.capture_semantics = capture_semantics
        self.invocations: list[_InvocationCall] = []
        self.finalizations: list[_FinalizerCall] = []
        self.captured_requests = []
        self.reassessments: list[PreVoteReassessment] = []
        self.finalizer_started = asyncio.Event()
        self.finalizer_release = asyncio.Event()
        if not gate_finalizer:
            self.finalizer_release.set()
        self.finalizer_error = finalizer_error

    async def invoke(
        self,
        *,
        owner,
        priority,
        allowed_handles,
        timeout_seconds,
        dispatch_deadline,
        on_brain_start=None,
    ) -> BrainDispatchResult:
        del on_brain_start
        self.invocations.append(
            _InvocationCall(
                owner,
                priority,
                allowed_handles,
                timeout_seconds,
                dispatch_deadline,
            )
        )
        handle = allowed_handles[0]
        option_id = "action:0"
        if isinstance(handle, VoteAction):
            target = handle.valid_targets[0] if handle.valid_targets else None
            decision = VoteDecision(option_id, target)
            action = "vote.cast"
            correlation_action = "vote"
            vote_target = target
            ability_id = None
            ability_targets = ()
        else:
            targets = tuple(handle.valid_targets[: handle.target_count])
            decision = AbilityDecision(option_id, targets)
            action = "ability.use"
            correlation_action = "ability"
            vote_target = None
            ability_id = handle.ability_id
            ability_targets = targets

        if self.capture_semantics:
            request = self.controller.capture_input(
                allowed_handles=allowed_handles,
                dispatch_deadline=dispatch_deadline,
            )
            if request is None or request.discussion is None:
                raise AssertionError("typed trigger did not create a sealed capture")
            self.captured_requests.append(request)
            if isinstance(handle, VoteAction):
                late = any(
                    getattr(record, "message", None) == "opaque-late-signal"
                    for record in request.history.records
                )
                target = handle.valid_targets[1 if late else 0]
                decision = VoteDecision(option_id, target)
                vote_target = target
                payload = _semantic_payload(request, target=target)
                parsed = parse_llm_output(
                    json.dumps(payload),
                    projection=project_brain_input(request, config=LLMBrainConfig()),
                )
                reassessment = parsed.proposal.pre_vote_reassessment
                if reassessment is None:
                    raise AssertionError("valid pre-vote reassessment was not parsed")
                self.reassessments.append(reassessment)

        receipt = SendReceipt(REQUEST_ID, handle.connection_generation)
        correlation = None
        if dispatch_deadline.discussion_trigger is not None:
            if self.bound is None:
                raise AssertionError("contextful trigger requires a bound context")
            correlation = DiscussionDispatchCorrelation(
                capture_id="1" * 64,
                request_id=f"phase6:{'1' * 64}",
                context_sha256=self.bound.context_sha256,
                before_state_sha256="2" * 64,
                after_state_sha256="3" * 64,
                proposal_sha256="4" * 64,
                generation_audit_sequence=1,
                base_revision=0,
                committed_revision=1,
                action=correlation_action,
                option_id=option_id,
                request_event_id=receipt.event_id,
                send_connection_generation=receipt.connection_generation,
            )
        return BrainDispatchResult(
            DecisionOutcome(
                DecisionStatus.SENT,
                request_version=self.controller.world.snapshot().version,
                phase=handle.phase,
                day=handle.day,
                option_id=option_id,
                receipt=receipt,
                invocation_started=True,
                request_event_id=receipt.event_id,
                attempt_action=action,
                send_connection_generation=receipt.connection_generation,
                vote_target_player_id=vote_target,
                ability_id=ability_id,
                ability_target_player_ids=ability_targets,
                discussion=correlation,
            ),
            decision,
        )

    async def finalize_discussion_observation(
        self,
        *,
        owner,
        correlation,
        status,
        reason,
        evidence=None,
    ) -> ObservationAck:
        self.finalizations.append(
            _FinalizerCall(owner, correlation, status, reason, evidence)
        )
        self.finalizer_started.set()
        await self.finalizer_release.wait()
        if self.finalizer_error is not None:
            raise self.finalizer_error
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
class _Stack:
    source: _Source
    world: WorldState
    arbiter: _RecordingArbiter
    controller: VoteAbilityController
    bound: BoundDiscussionContext | None


def _stack(
    handle: VoteAction | AbilityAction,
    *,
    contextful: bool = True,
    late_discussion: bool = False,
    capture_semantics: bool = False,
    transport_records: int = 256,
    gate_finalizer: bool = False,
    finalizer_error: BaseException | None = None,
) -> _Stack:
    source = _Source((handle,))
    world = WorldState(
        source,
        transport_retention=TransportObservationRetention(
            max_records=transport_records,
            max_bytes=262144,
        ),
    )
    world._consume(  # noqa: SLF001 - typed fixture input
        _server_event(
            "game.state_sync",
            1,
            _sync_payload(
                phase=handle.phase,
                day=handle.day,
                late_discussion=late_discussion,
            ),
        )
    )
    world._consume(  # noqa: SLF001 - typed fixture input
        PhaseTimingMapped(
            handle.phase,
            handle.day,
            1,
            handle.connection_generation,
            handle.action_generation,
            100,
            110,
            1.0,
            11.0,
        )
    )
    bound = _bound() if contextful else None
    arbiter = _RecordingArbiter(
        world=world,
        bound=bound,
        capture_semantics=capture_semantics,
        gate_finalizer=gate_finalizer,
        finalizer_error=finalizer_error,
    )
    controller = VoteAbilityController(
        world=world,
        invoker=arbiter,
        discussion_context=bound,
        clock=lambda: 1.0,
    )
    return _Stack(source, world, arbiter, controller, bound)


def _semantic_payload(request, *, target: str | None, ranked=None) -> dict:
    if ranked is None:
        ranked = [] if target is None else [target]
    kind = "none" if target is None else "vote"
    decision = (
        {"kind": "none"}
        if target is None
        else {
            "kind": "vote",
            "option_id": "action:0",
            "target_player_id": target,
        }
    )
    return {
        "decision": decision,
        "discussion": {
            "schema_version": "aiwolf.discussion-proposal.v1",
            "base_revision": request.discussion.base_revision,
            "decision_kind": kind,
            "option_id": None if target is None else "action:0",
            "speech_act": {"kind": "NONE"},
            "reaction": None,
            "assessment_updates": [],
            "claim_updates": [],
            "relation_updates": [],
            "strategy_update": None,
            "co_judgment": None,
            "pre_vote_reassessment": {
                "option_id": "action:0",
                "ranked_target_player_ids": list(ranked),
                "preferred_target_player_id": target,
                "evidence": [],
            },
        },
    }


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout)


def _accept(
    stack: _Stack,
    *,
    action: str,
    request_id: str = REQUEST_ID,
    generation: int | None = None,
) -> None:
    if generation is None:
        generation = stack.source.snapshot().connection_generation
    stack.world._consume(  # noqa: SLF001 - typed fixture input
        ActionAccepted(action, request_id, 2, generation, 2.0)
    )


def _reject(
    stack: _Stack,
    *,
    action: str,
    request_id: str | None = REQUEST_ID,
    generation: int | None = None,
) -> None:
    if generation is None:
        generation = stack.source.snapshot().connection_generation
    stack.world._consume(  # noqa: SLF001 - typed fixture input
        ActionRejected(action, "opaque-rejection", 2, generation, 2.0, request_id)
    )


def _barrier(stack: _Stack, *, contiguous: bool) -> None:
    handle = stack.source.snapshot().actions[0]
    fresh = replace(handle, connection_generation=2, action_generation=2)
    stack.source.update(
        last_seq=4,
        connection_generation=2,
        action_generation=2,
        actions=(fresh,),
    )
    stack.world._consume(  # noqa: SLF001 - typed fixture input
        _server_event(
            "game.state_sync",
            4,
            _sync_payload(phase=handle.phase, day=handle.day),
        )
    )
    stack.world._consume(  # noqa: SLF001 - typed fixture input
        ResumeRecoveryCompleted(
            2,
            1,
            None,
            None,
            contiguous,
            not contiguous,
            2,
            4,
        )
    )


class Phase6PreVoteIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_vote_trigger_uses_one_existing_call_and_retains_correlation(self) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1", "p2"), 1, False)
        stack = _stack(handle)
        stack.controller.start()
        await _wait_until(
            lambda: stack.controller.snapshot().unresolved_reservation is not None
        )

        self.assertEqual(len(stack.arbiter.invocations), 1)
        call = stack.arbiter.invocations[0]
        self.assertEqual(call.owner, "vote_ability")
        self.assertIs(call.priority, BrainInvocationPriority.RESERVATION_ACTION)
        self.assertEqual(call.allowed_handles, (handle,))
        trigger = call.dispatch_deadline.discussion_trigger
        self.assertIsNotNone(trigger)
        assert trigger is not None
        self.assertEqual(
            (
                trigger.owner,
                trigger.kind,
                trigger.day,
                trigger.phase,
                trigger.connection_generation,
                trigger.action_generation,
                trigger.mapping_order,
                trigger.source,
            ),
            ("vote_ability", "PRE_VOTE", 1, "vote", 1, 1, 1, None),
        )
        unresolved = stack.controller.snapshot().unresolved_reservation
        assert unresolved is not None
        self.assertIsNotNone(unresolved.discussion)

        _accept(stack, action="vote.cast")
        await _wait_until(lambda: stack.controller.snapshot().accepted_count == 1)
        self.assertEqual(len(stack.arbiter.finalizations), 1)
        finalization = stack.arbiter.finalizations[0]
        self.assertEqual(
            (finalization.status, finalization.reason, finalization.evidence),
            (
                DiscussionObservationStatus.ACCEPTED,
                DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
                EvidenceRef(
                    EvidenceRecordKind.ACTION_ACCEPTED,
                    2,
                    EvidenceVisibility.AUTHORIZED_PRIVATE,
                ),
            ),
        )
        outcome = stack.controller.snapshot().outcomes[-1]
        self.assertIs(outcome.discussion, unresolved.discussion)
        self.assertEqual(outcome.vote_target_player_id, "p1")
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(len(stack.arbiter.invocations), 1)
        await stack.controller.stop()

    async def test_ability_trigger_and_exact_wire_action_use_the_same_call(self) -> None:
        handle = AbilityAction(
            3,
            4,
            "night0",
            0,
            "ability",
            "opaque-ability",
            None,
            ("p2", "p1"),
            1,
            None,
        )
        stack = _stack(handle)
        stack.controller.start()
        await _wait_until(
            lambda: stack.controller.snapshot().unresolved_reservation is not None
        )
        trigger = stack.arbiter.invocations[0].dispatch_deadline.discussion_trigger
        self.assertIsNotNone(trigger)
        assert trigger is not None
        self.assertEqual(
            (trigger.owner, trigger.kind, trigger.day, trigger.phase),
            ("vote_ability", "ABILITY", 0, "night0"),
        )

        _accept(stack, action="vote.cast", generation=4)
        await asyncio.sleep(0)
        self.assertEqual(stack.arbiter.finalizations, [])
        _accept(stack, action="ability.use", generation=4)
        await _wait_until(lambda: stack.controller.snapshot().accepted_count == 1)
        outcome = stack.controller.snapshot().outcomes[-1]
        self.assertEqual(outcome.action, "ability.use")
        self.assertEqual(outcome.ability_id, "opaque-ability")
        self.assertEqual(outcome.ability_target_player_ids, ("p2",))
        self.assertEqual(stack.arbiter.finalizations[0].correlation.action, "ability")
        self.assertIs(
            stack.arbiter.finalizations[0].correlation,
            outcome.discussion,
        )
        self.assertEqual(len(stack.arbiter.invocations), 1)
        await stack.controller.stop()

    async def test_context_free_path_has_no_trigger_correlation_or_finalizer(self) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)
        stack = _stack(handle, contextful=False)
        stack.controller.start()
        await _wait_until(
            lambda: stack.controller.snapshot().unresolved_reservation is not None
        )
        self.assertIsNone(
            stack.arbiter.invocations[0].dispatch_deadline.discussion_trigger
        )
        unresolved = stack.controller.snapshot().unresolved_reservation
        assert unresolved is not None
        self.assertIsNone(unresolved.discussion)
        _accept(stack, action="vote.cast")
        await _wait_until(lambda: stack.controller.snapshot().accepted_count == 1)
        self.assertEqual(stack.arbiter.finalizations, [])
        self.assertIsNone(stack.controller.snapshot().outcomes[-1].discussion)
        await stack.controller.stop()

    async def test_context_free_two_exact_accepts_first_while_contextful_is_ambiguous(
        self,
    ) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)

        legacy = _stack(handle, contextful=False)
        legacy.controller.start()
        await _wait_until(
            lambda: legacy.controller.snapshot().unresolved_reservation is not None
        )
        _accept(legacy, action="vote.cast", generation=1)
        _accept(legacy, action="vote.cast", generation=2)
        await _wait_until(lambda: legacy.controller.snapshot().accepted_count == 1)
        legacy_snapshot = legacy.controller.snapshot()
        self.assertEqual(
            (
                legacy_snapshot.accepted_count,
                legacy_snapshot.rejected_count,
                legacy_snapshot.unknown_count,
                legacy_snapshot.outcomes[-1].status,
                legacy_snapshot.outcomes[-1].observation_connection_generation,
            ),
            (1, 0, 0, VoteAbilityOutcomeStatus.ACCEPTED, 1),
        )
        self.assertIsNone(legacy_snapshot.outcomes[-1].discussion)
        self.assertEqual(legacy.arbiter.finalizations, [])
        self.assertEqual(len(legacy.arbiter.invocations), 1)
        await legacy.controller.stop()

        contextful = _stack(handle)
        contextful.controller.start()
        await _wait_until(
            lambda: contextful.controller.snapshot().unresolved_reservation is not None
        )
        _accept(contextful, action="vote.cast", generation=1)
        _accept(contextful, action="vote.cast", generation=2)
        await _wait_until(lambda: contextful.controller.snapshot().unknown_count == 1)
        contextful_snapshot = contextful.controller.snapshot()
        self.assertEqual(
            (
                contextful_snapshot.accepted_count,
                contextful_snapshot.rejected_count,
                contextful_snapshot.unknown_count,
                contextful_snapshot.outcomes[-1].status,
                contextful_snapshot.outcomes[-1].observation_connection_generation,
            ),
            (0, 0, 1, VoteAbilityOutcomeStatus.UNKNOWN, None),
        )
        self.assertEqual(len(contextful.arbiter.finalizations), 1)
        self.assertEqual(
            (
                contextful.arbiter.finalizations[0].status,
                contextful.arbiter.finalizations[0].reason,
                contextful.arbiter.finalizations[0].evidence,
            ),
            (
                DiscussionObservationStatus.RECOVERY_UNKNOWN,
                DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
                None,
            ),
        )
        self.assertEqual(len(contextful.arbiter.invocations), 1)
        await contextful.controller.stop()

    async def test_context_free_retained_gap_exact_response_keeps_legacy_precedence(
        self,
    ) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)

        legacy = _stack(handle, contextful=False, transport_records=1)
        legacy.controller.start()
        await _wait_until(
            lambda: legacy.controller.snapshot().unresolved_reservation is not None
        )
        _accept(legacy, action="opaque-wrong-action", generation=1)
        _accept(legacy, action="vote.cast", generation=2)
        await _wait_until(lambda: legacy.controller.snapshot().accepted_count == 1)
        legacy_snapshot = legacy.controller.snapshot()
        self.assertEqual(
            (
                legacy_snapshot.accepted_count,
                legacy_snapshot.unknown_count,
                legacy_snapshot.outcomes[-1].status,
                legacy_snapshot.outcomes[-1].observation_connection_generation,
            ),
            (1, 0, VoteAbilityOutcomeStatus.ACCEPTED, 2),
        )
        self.assertEqual(legacy.arbiter.finalizations, [])
        self.assertEqual(len(legacy.arbiter.invocations), 1)
        await legacy.controller.stop()

        contextful = _stack(handle, transport_records=1)
        contextful.controller.start()
        await _wait_until(
            lambda: contextful.controller.snapshot().unresolved_reservation is not None
        )
        _accept(contextful, action="opaque-wrong-action", generation=1)
        _accept(contextful, action="vote.cast", generation=2)
        await _wait_until(lambda: contextful.controller.snapshot().unknown_count == 1)
        contextful_snapshot = contextful.controller.snapshot()
        self.assertEqual(
            (
                contextful_snapshot.accepted_count,
                contextful_snapshot.unknown_count,
                contextful_snapshot.outcomes[-1].status,
                contextful_snapshot.outcomes[-1].observation_connection_generation,
            ),
            (0, 1, VoteAbilityOutcomeStatus.UNKNOWN, None),
        )
        self.assertEqual(
            (
                contextful.arbiter.finalizations[0].reason,
                contextful.arbiter.finalizations[0].evidence,
            ),
            (DiscussionTerminalReason.RECOVERY_GAP, None),
        )
        self.assertEqual(len(contextful.arbiter.invocations), 1)
        await contextful.controller.stop()

    async def test_context_free_ordinary_accept_reject_and_barrier_stay_legacy(
        self,
    ) -> None:
        cases = (
            (
                "accept",
                VoteAbilityOutcomeStatus.ACCEPTED,
                (1, 0, 0),
                1,
                None,
            ),
            (
                "reject",
                VoteAbilityOutcomeStatus.REJECTED,
                (0, 1, 0),
                1,
                "opaque-rejection",
            ),
            (
                "barrier",
                VoteAbilityOutcomeStatus.UNKNOWN,
                (0, 0, 1),
                2,
                None,
            ),
        )
        for case, expected_status, expected_counts, generation, rejection in cases:
            with self.subTest(case=case):
                handle = VoteAction(
                    1, 1, "vote", 1, "vote", ("p1",), 1, False
                )
                stack = _stack(handle, contextful=False)
                stack.controller.start()
                await _wait_until(
                    lambda: stack.controller.snapshot().unresolved_reservation
                    is not None
                )
                if case == "accept":
                    _accept(stack, action="vote.cast")
                elif case == "reject":
                    _reject(stack, action="vote.cast")
                else:
                    _barrier(stack, contiguous=True)
                await _wait_until(
                    lambda: stack.controller.snapshot().unresolved_reservation is None
                )
                snapshot = stack.controller.snapshot()
                outcome = snapshot.outcomes[-1]
                self.assertEqual(
                    (
                        outcome.status,
                        (
                            snapshot.accepted_count,
                            snapshot.rejected_count,
                            snapshot.unknown_count,
                        ),
                        outcome.observation_connection_generation,
                        outcome.rejection_reason,
                        outcome.discussion,
                    ),
                    (
                        expected_status,
                        expected_counts,
                        generation,
                        rejection,
                        None,
                    ),
                )
                self.assertEqual(stack.arbiter.finalizations, [])
                self.assertEqual(len(stack.arbiter.invocations), 1)
                await stack.controller.stop()

    async def test_exact_rejection_closes_one_linked_terminal_without_retry(self) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)
        stack = _stack(handle)
        stack.controller.start()
        await _wait_until(
            lambda: stack.controller.snapshot().unresolved_reservation is not None
        )
        unresolved = stack.controller.snapshot().unresolved_reservation
        assert unresolved is not None
        _reject(stack, action="vote.cast")
        await _wait_until(lambda: stack.controller.snapshot().rejected_count == 1)

        self.assertEqual(len(stack.arbiter.finalizations), 1)
        finalization = stack.arbiter.finalizations[0]
        self.assertEqual(
            (finalization.status, finalization.reason, finalization.evidence),
            (
                DiscussionObservationStatus.REJECTED,
                DiscussionTerminalReason.AUTHORITATIVE_REJECTED,
                EvidenceRef(
                    EvidenceRecordKind.ACTION_REJECTION,
                    2,
                    EvidenceVisibility.AUTHORIZED_PRIVATE,
                ),
            ),
        )
        outcome = stack.controller.snapshot().outcomes[-1]
        self.assertEqual(outcome.rejection_reason, "opaque-rejection")
        self.assertIs(outcome.discussion, unresolved.discussion)
        self.assertEqual(len(stack.arbiter.invocations), 1)
        await stack.controller.stop()

    async def test_pre_vote_semantics_use_late_context_inside_the_same_fresh_call(self) -> None:
        results = []
        for late in (False, True):
            handle = VoteAction(
                1, 1, "vote", 1, "vote", ("p1", "p2"), 1, False
            )
            stack = _stack(
                handle,
                late_discussion=late,
                capture_semantics=True,
            )
            stack.controller.start()
            await _wait_until(
                lambda: stack.controller.snapshot().unresolved_reservation is not None
            )
            self.assertEqual(len(stack.arbiter.invocations), 1)
            self.assertEqual(len(stack.arbiter.captured_requests), 1)
            request = stack.arbiter.captured_requests[0]
            self.assertIs(request.discussion.context, stack.bound.context)
            self.assertEqual(request.discussion.trigger.kind, "PRE_VOTE")
            reassessment = stack.arbiter.reassessments[0]
            unresolved = stack.controller.snapshot().unresolved_reservation
            assert unresolved is not None
            self.assertEqual(
                reassessment.preferred_target_player_id,
                unresolved.vote_target_player_id,
            )
            self.assertEqual(
                reassessment.ranked_target_player_ids[0],
                reassessment.preferred_target_player_id,
            )
            results.append(reassessment)
            await stack.controller.stop()
        self.assertEqual(results[0].preferred_target_player_id, "p1")
        self.assertEqual(results[1].preferred_target_player_id, "p2")

    async def test_current_trigger_keeps_pre_vote_validation_strict(self) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1", "p2"), 1, True)
        stack = _stack(handle, capture_semantics=True)
        stack.controller.start()
        await _wait_until(lambda: bool(stack.arbiter.captured_requests))
        request = stack.arbiter.captured_requests[0]
        projection = project_brain_input(request, config=LLMBrainConfig())

        invalid_payloads = []
        duplicate = _semantic_payload(request, target="p1", ranked=("p1", "p1"))
        invalid_payloads.append(duplicate)
        unoffered = _semantic_payload(request, target="p1", ranked=("p1", "p3"))
        invalid_payloads.append(unoffered)
        wrong_first = _semantic_payload(request, target="p1", ranked=("p2", "p1"))
        invalid_payloads.append(wrong_first)
        wrong_option = _semantic_payload(request, target="p1")
        wrong_option["discussion"]["pre_vote_reassessment"]["option_id"] = "opaque-option"
        invalid_payloads.append(wrong_option)
        mismatch = _semantic_payload(request, target="p1")
        mismatch["decision"]["target_player_id"] = "p2"
        invalid_payloads.append(mismatch)
        multiple = _semantic_payload(request, target="p1")
        multiple["discussion"]["pre_vote_reassessment"] = [
            multiple["discussion"]["pre_vote_reassessment"],
            multiple["discussion"]["pre_vote_reassessment"],
        ]
        invalid_payloads.append(multiple)
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(DecisionValidationError):
                    parse_llm_output(json.dumps(payload), projection=projection)

        abstain = _semantic_payload(request, target=None, ranked=("p1", "p2"))
        parsed = parse_llm_output(json.dumps(abstain), projection=projection)
        self.assertIsInstance(parsed.decision, NoDecision)
        self.assertEqual(
            parsed.proposal.pre_vote_reassessment,
            PreVoteReassessment("action:0", ("p1", "p2"), None, ()),
        )
        await stack.controller.stop()

    async def test_n_minus_one_and_wrong_id_wait_but_n_plus_one_accepts(self) -> None:
        handle = VoteAction(7, 9, "vote", 1, "vote", ("p1",), 1, False)
        stack = _stack(handle)
        stack.controller.start()
        await _wait_until(
            lambda: stack.controller.snapshot().unresolved_reservation is not None
        )
        _accept(stack, action="vote.cast", generation=6)
        _accept(stack, action="vote.cast", request_id="opaque-wrong-id", generation=7)
        await asyncio.sleep(0)
        self.assertIsNotNone(stack.controller.snapshot().unresolved_reservation)
        self.assertEqual(stack.arbiter.finalizations, [])

        _accept(stack, action="vote.cast", generation=8)
        await _wait_until(lambda: stack.controller.snapshot().accepted_count == 1)
        self.assertEqual(
            stack.arbiter.finalizations[0].evidence,
            EvidenceRef(
                EvidenceRecordKind.ACTION_ACCEPTED,
                4,
                EvidenceVisibility.AUTHORIZED_PRIVATE,
            ),
        )
        self.assertEqual(
            stack.controller.snapshot().outcomes[-1].observation_connection_generation,
            8,
        )
        await stack.controller.stop()

    async def test_null_id_rejection_is_ambiguity_not_rejection(self) -> None:
        handle = VoteAction(7, 9, "vote", 1, "vote", ("p1",), 1, False)
        stack = _stack(handle)
        stack.controller.start()
        await _wait_until(
            lambda: stack.controller.snapshot().unresolved_reservation is not None
        )
        _reject(stack, action="vote.cast", request_id=None, generation=7)
        await _wait_until(lambda: stack.controller.snapshot().unknown_count == 1)
        finalization = stack.arbiter.finalizations[0]
        self.assertEqual(
            (finalization.status, finalization.reason, finalization.evidence),
            (
                DiscussionObservationStatus.RECOVERY_UNKNOWN,
                DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
                EvidenceRef(
                    EvidenceRecordKind.ACTION_REJECTION,
                    2,
                    EvidenceVisibility.AUTHORIZED_PRIVATE,
                ),
            ),
        )
        self.assertEqual(stack.controller.snapshot().rejected_count, 0)
        await stack.controller.stop()

    async def test_contextful_accept_and_null_id_rejection_are_ambiguous_in_both_orders(
        self,
    ) -> None:
        for null_id_first in (False, True):
            with self.subTest(null_id_first=null_id_first):
                handle = VoteAction(
                    1, 1, "vote", 1, "vote", ("p1",), 1, False
                )
                stack = _stack(handle)
                stack.controller.start()
                await _wait_until(
                    lambda: stack.controller.snapshot().unresolved_reservation
                    is not None
                )
                if null_id_first:
                    _reject(stack, action="vote.cast", request_id=None)
                    _accept(stack, action="vote.cast")
                else:
                    _accept(stack, action="vote.cast")
                    _reject(stack, action="vote.cast", request_id=None)
                await _wait_until(
                    lambda: stack.controller.snapshot().unknown_count == 1
                )

                snapshot = stack.controller.snapshot()
                outcome = snapshot.outcomes[-1]
                self.assertEqual(
                    (
                        outcome.status,
                        snapshot.accepted_count,
                        snapshot.rejected_count,
                        snapshot.unknown_count,
                        outcome.observation_connection_generation,
                        outcome.rejection_reason,
                    ),
                    (VoteAbilityOutcomeStatus.UNKNOWN, 0, 0, 1, None, None),
                )
                self.assertIsNotNone(outcome.discussion)
                self.assertEqual(len(stack.arbiter.finalizations), 1)
                self.assertEqual(
                    (
                        stack.arbiter.finalizations[0].status,
                        stack.arbiter.finalizations[0].reason,
                        stack.arbiter.finalizations[0].evidence,
                    ),
                    (
                        DiscussionObservationStatus.RECOVERY_UNKNOWN,
                        DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
                        None,
                    ),
                )
                self.assertEqual(len(stack.arbiter.invocations), 1)
                await stack.controller.stop()

    async def test_contextful_exact_and_null_id_rejections_are_ambiguous(
        self,
    ) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)
        stack = _stack(handle)
        stack.controller.start()
        await _wait_until(
            lambda: stack.controller.snapshot().unresolved_reservation is not None
        )
        _reject(stack, action="vote.cast")
        _reject(stack, action="vote.cast", request_id=None)
        await _wait_until(lambda: stack.controller.snapshot().unknown_count == 1)

        snapshot = stack.controller.snapshot()
        outcome = snapshot.outcomes[-1]
        self.assertEqual(
            (
                outcome.status,
                snapshot.accepted_count,
                snapshot.rejected_count,
                snapshot.unknown_count,
                outcome.observation_connection_generation,
                outcome.rejection_reason,
            ),
            (VoteAbilityOutcomeStatus.UNKNOWN, 0, 0, 1, None, None),
        )
        self.assertIsNotNone(outcome.discussion)
        self.assertEqual(len(stack.arbiter.finalizations), 1)
        self.assertEqual(
            (
                stack.arbiter.finalizations[0].status,
                stack.arbiter.finalizations[0].reason,
                stack.arbiter.finalizations[0].evidence,
            ),
            (
                DiscussionObservationStatus.RECOVERY_UNKNOWN,
                DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
                None,
            ),
        )
        self.assertEqual(len(stack.arbiter.invocations), 1)
        await stack.controller.stop()

    async def test_exact_response_precedes_either_barrier_in_complete_batch(self) -> None:
        for contiguous in (True, False):
            with self.subTest(contiguous=contiguous):
                handle = VoteAction(
                    1, 1, "vote", 1, "vote", ("p1",), 1, False
                )
                stack = _stack(handle)
                stack.controller.start()
                await _wait_until(
                    lambda: stack.controller.snapshot().unresolved_reservation
                    is not None
                )
                _accept(stack, action="vote.cast", generation=1)
                _barrier(stack, contiguous=contiguous)
                await _wait_until(
                    lambda: stack.controller.snapshot().accepted_count == 1
                )
                finalization = stack.arbiter.finalizations[0]
                self.assertEqual(
                    (finalization.status, finalization.reason),
                    (
                        DiscussionObservationStatus.ACCEPTED,
                        DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
                    ),
                )
                self.assertEqual(finalization.evidence.order, 2)
                await stack.controller.stop()

    async def test_multiple_or_conflicting_exact_candidates_have_no_arbitrary_evidence(self) -> None:
        for conflict in (False, True):
            with self.subTest(conflict=conflict):
                handle = VoteAction(
                    1, 1, "vote", 1, "vote", ("p1",), 1, False
                )
                stack = _stack(handle)
                stack.controller.start()
                await _wait_until(
                    lambda: stack.controller.snapshot().unresolved_reservation
                    is not None
                )
                _accept(stack, action="vote.cast")
                if conflict:
                    _reject(stack, action="vote.cast")
                else:
                    _accept(stack, action="vote.cast")
                await _wait_until(
                    lambda: stack.controller.snapshot().unknown_count == 1
                )
                finalization = stack.arbiter.finalizations[0]
                self.assertEqual(
                    (finalization.status, finalization.reason, finalization.evidence),
                    (
                        DiscussionObservationStatus.RECOVERY_UNKNOWN,
                        DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
                        None,
                    ),
                )
                await stack.controller.stop()

    async def test_contiguous_gap_retention_and_phase_terminal_matrix(self) -> None:
        for contiguous, reason in (
            (True, DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS),
            (False, DiscussionTerminalReason.RECOVERY_GAP),
        ):
            with self.subTest(reason=reason):
                handle = VoteAction(
                    1, 1, "vote", 1, "vote", ("p1",), 1, False
                )
                stack = _stack(handle)
                stack.controller.start()
                await _wait_until(
                    lambda: stack.controller.snapshot().unresolved_reservation
                    is not None
                )
                _barrier(stack, contiguous=contiguous)
                await _wait_until(
                    lambda: stack.controller.snapshot().unknown_count == 1
                )
                finalization = stack.arbiter.finalizations[0]
                self.assertEqual(finalization.reason, reason)
                self.assertEqual(
                    finalization.evidence,
                    EvidenceRef(
                        EvidenceRecordKind.RESUME_RECOVERY_BARRIER,
                        2,
                        EvidenceVisibility.AUTHORIZED_PRIVATE,
                    ),
                )
                await stack.controller.stop()

        retention = _stack(
            VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False),
            transport_records=1,
        )
        retention.controller.start()
        await _wait_until(
            lambda: retention.controller.snapshot().unresolved_reservation is not None
        )
        _accept(retention, action="opaque-wrong-action")
        _accept(retention, action="vote.cast")
        await _wait_until(lambda: retention.controller.snapshot().unknown_count == 1)
        self.assertEqual(
            (
                retention.arbiter.finalizations[0].reason,
                retention.arbiter.finalizations[0].evidence,
            ),
            (DiscussionTerminalReason.RECOVERY_GAP, None),
        )
        await retention.controller.stop()

        phase = _stack(
            VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)
        )
        phase.controller.start()
        await _wait_until(
            lambda: phase.controller.snapshot().unresolved_reservation is not None
        )
        phase.source.update(last_seq=2, action_generation=2, actions=())
        phase.world._consume(  # noqa: SLF001 - typed fixture input
            _server_event(
                "game.state_sync",
                2,
                _sync_payload(phase="runoff", day=1),
            )
        )
        phase.world._consume(  # noqa: SLF001 - typed fixture input
            PhaseTimingMapped("runoff", 1, 2, 1, 2, 100, 120, 2.0, 20.0)
        )
        await _wait_until(lambda: phase.controller.snapshot().unknown_count == 1)
        self.assertEqual(
            (
                phase.arbiter.finalizations[0].reason,
                phase.arbiter.finalizations[0].evidence,
            ),
            (
                DiscussionTerminalReason.PHASE_CHANGED,
                EvidenceRef(
                    EvidenceRecordKind.PHASE_TIMING,
                    2,
                    EvidenceVisibility.AUTHORIZED_PRIVATE,
                ),
            ),
        )
        await phase.controller.stop()

    async def test_repeated_stop_joins_one_owner_stopped_finalizer(self) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)
        stack = _stack(handle, gate_finalizer=True)
        stack.controller.start()
        await _wait_until(
            lambda: stack.controller.snapshot().unresolved_reservation is not None
        )

        first_stop = asyncio.create_task(stack.controller.stop())
        await asyncio.wait_for(stack.arbiter.finalizer_started.wait(), 1.0)
        second_stop = asyncio.create_task(stack.controller.stop())
        await asyncio.sleep(0)
        self.assertFalse(first_stop.done())
        self.assertFalse(second_stop.done())
        self.assertEqual(len(stack.arbiter.finalizations), 1)

        stack.arbiter.finalizer_release.set()
        await asyncio.wait_for(asyncio.gather(first_stop, second_stop), 1.0)
        self.assertEqual(len(stack.arbiter.finalizations), 1)
        finalization = stack.arbiter.finalizations[0]
        self.assertEqual(
            (finalization.status, finalization.reason, finalization.evidence),
            (
                DiscussionObservationStatus.RECOVERY_UNKNOWN,
                DiscussionTerminalReason.OWNER_STOPPED,
                None,
            ),
        )
        snapshot = stack.controller.snapshot()
        self.assertIs(snapshot.lifecycle, VoteAbilityLifecycle.STOPPED)
        self.assertEqual(snapshot.outcomes[-1].status, VoteAbilityOutcomeStatus.CANCELLED)
        self.assertIsNotNone(snapshot.outcomes[-1].discussion)

    async def test_finalizer_failure_is_fatal_and_never_retried(self) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)
        stack = _stack(handle, finalizer_error=RuntimeError("opaque-audit-failure"))
        stack.controller.start()
        await _wait_until(
            lambda: stack.controller.snapshot().unresolved_reservation is not None
        )
        _accept(stack, action="vote.cast")
        exit_value = await asyncio.wait_for(stack.controller.wait(), 1.0)
        self.assertEqual(exit_value.reason, FeatureControllerExitReason.FAILED)
        self.assertEqual(exit_value.error_type, "RuntimeError")
        self.assertEqual(len(stack.arbiter.finalizations), 1)
        self.assertEqual(stack.controller.snapshot().outcomes, ())
        await stack.controller.stop()
        self.assertEqual(len(stack.arbiter.finalizations), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
