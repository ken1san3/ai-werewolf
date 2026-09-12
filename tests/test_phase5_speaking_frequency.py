from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import subprocess
import sys
import time
import unittest

from ai_client.brain import BrainController, BrainInvocationArbiter, BrainRunConfig, ChatDecision
from ai_client.network import (
    ChatAction,
    ClientLifecycle,
    ClientSnapshot,
    PhaseTimingMapped,
    SendReceipt,
    ServerEvent,
)
from ai_client.network.types import immutable_mapping
from ai_client.reaction_chat.controller import ReactionChatController
from ai_client.reaction_chat.frequency import (
    DeterministicSpeakingFrequencyPolicy,
    FrequencyDecision,
    FrequencySuppression,
    PreparedSpeakingOpportunity,
    SpeakingFrequencyState,
    SpeakingOpportunity,
    SpeakingProfile,
)
from ai_client.reaction_chat.types import (
    ReactionChatConfig,
    ReactionOutcomeStatus,
    ReactionPhaseKey,
    ReactionTriggerKind,
)
from ai_client.world import WorldState


_SOURCE_FINGERPRINT = (
    "b954070ef25497d406ba932f9c94f55a7b31a60b9f367965b4bdb9fd36243c9b"
)


def _opportunities() -> tuple[SpeakingOpportunity, SpeakingOpportunity]:
    key = ReactionPhaseKey(1, 1, "day", 3)
    return (
        SpeakingOpportunity(
            key,
            ReactionTriggerKind.INITIAL_CHAT,
            None,
            None,
            None,
            None,
            "p2",
            "P2",
            0,
        ),
        SpeakingOpportunity(
            key,
            ReactionTriggerKind.REACTION_CHAT,
            42,
            "p7",
            "public",
            "hello p2",
            "p2",
            "P2",
            0,
        ),
    )


class SpeakingFrequencyPolicyTests(unittest.TestCase):
    def test_public_hash_and_decision_vectors_in_process(self) -> None:
        policy = DeterministicSpeakingFrequencyPolicy(
            profile=SpeakingProfile(), master_seed=7
        )
        initial, reaction = _opportunities()
        initial_prepared = policy.prepare(initial)
        reaction_prepared = policy.prepare(reaction)
        self.assertEqual(initial_prepared.event_importance, 1.0)
        self.assertIsNone(initial_prepared.source_message_sha256)
        self.assertEqual(reaction_prepared.event_importance, 1.0)
        self.assertEqual(reaction_prepared.source_message_sha256, _SOURCE_FINGERPRINT)
        self.assertEqual(
            policy.evaluate(initial_prepared),
            FrequencyDecision(True, None, 1.0, 0.5, 0.17800791926725024),
        )
        self.assertEqual(
            policy.evaluate(reaction_prepared),
            FrequencyDecision(True, None, 1.0, 0.5, 0.19174690774662817),
        )

    def test_public_vectors_are_identical_in_two_spawned_processes(self) -> None:
        script = """
import json, sys
from ai_client.reaction_chat.frequency import DeterministicSpeakingFrequencyPolicy, SpeakingOpportunity, SpeakingProfile
from ai_client.reaction_chat.types import ReactionPhaseKey, ReactionTriggerKind
kind = ReactionTriggerKind(sys.argv[1])
reaction = kind is ReactionTriggerKind.REACTION_CHAT
opportunity = SpeakingOpportunity(
    ReactionPhaseKey(1, 1, 'day', 3), kind,
    42 if reaction else None, 'p7' if reaction else None,
    'public' if reaction else None, 'hello p2' if reaction else None,
    'p2', 'P2', 0,
)
policy = DeterministicSpeakingFrequencyPolicy(profile=SpeakingProfile(), master_seed=7)
prepared = policy.prepare(opportunity)
decision = policy.evaluate(prepared)
print(json.dumps({
    'fingerprint': prepared.source_message_sha256,
    'importance': prepared.event_importance,
    'draw': decision.draw,
    'threshold': decision.threshold,
    'invoke': decision.should_invoke,
    'suppression': None if decision.suppression is None else decision.suppression.value,
}, sort_keys=True))
"""
        expected = (
            {
                "fingerprint": None,
                "importance": 1.0,
                "draw": 0.17800791926725024,
                "threshold": 0.5,
                "invoke": True,
                "suppression": None,
            },
            {
                "fingerprint": _SOURCE_FINGERPRINT,
                "importance": 1.0,
                "draw": 0.19174690774662817,
                "threshold": 0.5,
                "invoke": True,
                "suppression": None,
            },
        )
        for opportunity, result in zip(_opportunities(), expected, strict=True):
            completed = subprocess.run(
                [sys.executable, "-c", script, opportunity.trigger_kind.value],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(json.loads(completed.stdout), result)

    def test_narrow_importance_has_exact_unicode_substring_semantics(self) -> None:
        profile = SpeakingProfile(
            ordinary_event_importance=0.25,
            direct_mention_importance=0.75,
        )
        policy = DeterministicSpeakingFrequencyPolicy(profile=profile, master_seed=1)
        _, base = _opportunities()
        cases = (
            ("hello p2", 0.75),
            ("hello P2", 0.75),
            ("hello P₂", 0.25),
            ("this is not a strategic question", 0.25),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                prepared = policy.prepare(replace(base, source_message=message))
                self.assertEqual(prepared.event_importance, expected)

    def test_profile_and_public_values_reject_invalid_boundaries(self) -> None:
        for changes in (
            {"talkativeness": True},
            {"talkativeness": -0.01},
            {"initial_event_importance": 1.01},
            {"cooldown_seconds": float("inf")},
            {"max_trigger_evaluations_per_phase": True},
            {"max_trigger_evaluations_per_phase": 65},
            {"repetition_window": 33},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                SpeakingProfile(**changes)
        self.assertEqual(
            SpeakingProfile(
                talkativeness=0,
                ordinary_event_importance=1,
                cooldown_seconds=0,
                max_trigger_evaluations_per_phase=0,
                repetition_window=0,
            ).repetition_window,
            0,
        )

    def test_deterministic_policy_profile_is_read_only_and_stable(self) -> None:
        profile = SpeakingProfile()
        policy = DeterministicSpeakingFrequencyPolicy(
            profile=profile, master_seed=7
        )
        self.assertIs(policy.profile, profile)
        with self.assertRaises(AttributeError):
            policy.profile = SpeakingProfile(talkativeness=1)  # type: ignore[misc]

    def test_single_chat_invocation_cap_accepts_only_one_or_two(self) -> None:
        self.assertEqual(ReactionChatConfig(max_chat_attempts_per_phase=1).max_chat_attempts_per_phase, 1)
        self.assertEqual(ReactionChatConfig(max_chat_attempts_per_phase=2).max_chat_attempts_per_phase, 2)
        for invalid in (0, True, 3):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ReactionChatConfig(max_chat_attempts_per_phase=invalid)


def _event(message_type: str, seq: int, payload: dict, *, day: int = 1) -> ServerEvent:
    return ServerEvent(
        type=message_type,
        protocol_version="1.0",
        event_id=f"event-{seq}",
        game_id="game-1",
        seq=seq,
        timestamp=100 + day,
        payload=immutable_mapping(payload),
    )


def _sync_payload(
    *,
    player_id: str = "p2",
    day: int = 1,
    chat: bool = True,
    history: list[dict] | None = None,
) -> dict:
    return {
        "players": [
            {"player_id": current, "display_name": current.upper()}
            for current in ("p0", "p1", "p2", "p7")
        ],
        "deaths": [],
        "action_state": {
            "phase": "day",
            "day": day,
            "phase_ends_at": 110 + day,
            "actions": ([{"type": "chat", "channel": "public"}] if chat else []),
        },
        "self": {"player_id": player_id, "role_id": "villager", "modifier_ids": []},
        "revealed_roles": [],
        "history": [] if history is None else history,
    }


class _Source:
    def __init__(self, action_generation: int = 3) -> None:
        self._snapshot = ClientSnapshot(
            lifecycle=ClientLifecycle.CONNECTED,
            player_id="p2",
            last_seq=1,
            actions=(
                ChatAction(
                    connection_generation=1,
                    action_generation=action_generation,
                    phase="day",
                    day=1,
                    type="chat",
                    channel="public",
                ),
            ),
            connection_generation=1,
            action_generation=action_generation,
        )

    def snapshot(self) -> ClientSnapshot:
        return self._snapshot

    def advance(self, seq: int) -> None:
        self._snapshot = replace(self._snapshot, last_seq=seq)

    def replace_phase(self, *, seq: int, day: int, action_generation: int, chat: bool) -> None:
        actions = (
            (
                ChatAction(
                    connection_generation=1,
                    action_generation=action_generation,
                    phase="day",
                    day=day,
                    type="chat",
                    channel="public",
                ),
            )
            if chat
            else ()
        )
        self._snapshot = replace(
            self._snapshot,
            last_seq=seq,
            actions=actions,
            action_generation=action_generation,
        )

    async def events(self):
        await asyncio.Future()
        yield  # pragma: no cover


class _Brain:
    def __init__(self) -> None:
        self.requests = []

    async def decide(self, request):
        self.requests.append(request)
        option = request.action_context.options[0]
        return ChatDecision(option.option_id, f"message-{len(self.requests)}")


class _Sender:
    def __init__(self, world: WorldState, source: _Source) -> None:
        self.world = world
        self.source = source
        self.calls = []

    async def send_chat(self, handle: ChatAction, message: str) -> SendReceipt:
        self.calls.append((handle, message))
        seq = self.source.snapshot().last_seq + 1
        self.source.advance(seq)
        self.world._consume(  # noqa: SLF001 - typed integration fixture
            _event(
                "chat.message",
                seq,
                {
                    "channel": handle.channel,
                    "message": {
                        "player_id": "p2",
                        "display_name": "P2",
                        "message": message,
                    },
                },
            )
        )
        return SendReceipt(f"request-{seq}", 1)


class _CallbackGateArbiter(BrainInvocationArbiter):
    def __init__(self, *, controller: BrainController) -> None:
        super().__init__(controller=controller)
        self.waiting_before_claim = asyncio.Event()
        self.allow_claim = asyncio.Event()

    async def invoke(self, *, on_brain_start=None, **kwargs):
        self.waiting_before_claim.set()
        await self.allow_claim.wait()
        if on_brain_start is not None:
            on_brain_start()
        return await super().invoke(**kwargs)


class _ImmediateCallbackArbiter(BrainInvocationArbiter):
    async def invoke(self, *, on_brain_start=None, **kwargs):
        if on_brain_start is not None:
            on_brain_start()
        return await super().invoke(**kwargs)


class _CallbackAbsentArbiter(BrainInvocationArbiter):
    async def invoke(
        self,
        *,
        owner,
        priority,
        allowed_handles,
        timeout_seconds,
        dispatch_deadline,
    ):
        return await super().invoke(
            owner=owner,
            priority=priority,
            allowed_handles=allowed_handles,
            timeout_seconds=timeout_seconds,
            dispatch_deadline=dispatch_deadline,
        )


class _VariadicOnlyArbiter(BrainInvocationArbiter):
    async def invoke(self, **kwargs):
        return await super().invoke(**kwargs)


class _RequiredCallbackArbiter(BrainInvocationArbiter):
    async def invoke(self, *, on_brain_start, **kwargs):
        return await super().invoke(**kwargs)


class _NonNoneDefaultCallbackArbiter(BrainInvocationArbiter):
    async def invoke(self, *, on_brain_start=False, **kwargs):
        return await super().invoke(**kwargs)


class _FakeClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _make_stack(
    profile: SpeakingProfile | None,
    *,
    max_chat_attempts: int = 2,
    preload_self_history: bool = False,
    clock=time.monotonic,
    policy_factory=None,
) -> tuple[ReactionChatController, WorldState, _Source, _Brain, _Sender]:
    source = _Source()
    world = WorldState(source)
    world._consume(_event("game.state_sync", 1, _sync_payload()))  # noqa: SLF001
    if preload_self_history:
        for index in range(41):
            _emit_chat(
                world,
                source,
                player_id="p2",
                message=f"prior-{index}",
            )
    mapped_at = clock()
    world._consume(  # noqa: SLF001
        PhaseTimingMapped(
            "day",
            1,
            source.snapshot().last_seq,
            1,
            3,
            101,
            111,
            mapped_at,
            mapped_at + 10,
        )
    )
    brain = _Brain()
    sender = _Sender(world, source)
    controller = BrainController(
        world=world,
        sender=sender,  # type: ignore[arg-type]
        brain=brain,
        config=BrainRunConfig(max_decision_seconds=0.2),
        clock=clock,
    )
    invoker = (
        BrainInvocationArbiter(controller=controller, clock=clock)
        if profile is None
        else _ImmediateCallbackArbiter(controller=controller, clock=clock)
    )
    frequency_policy = (
        None
        if profile is None
        else (
            policy_factory(profile)
            if policy_factory is not None
            else DeterministicSpeakingFrequencyPolicy(profile=profile, master_seed=7)
        )
    )
    reaction = ReactionChatController(
        world=world,
        invoker=invoker,
        master_seed=7,
        config=ReactionChatConfig(
            max_chat_attempts_per_phase=max_chat_attempts,
            initial_jitter_seconds=(0, 0),
            reaction_jitter_seconds=(0, 0),
            minimum_accepted_chat_interval_seconds=0,
            deadline_guard_seconds=0.01,
            minimum_start_budget_seconds=0.001,
            brain_timeout_seconds=0.1,
        ),
        frequency_policy=frequency_policy,
        clock=clock,
    )
    return reaction, world, source, brain, sender


def _emit_chat(
    world: WorldState,
    source: _Source,
    *,
    player_id: str,
    message: str,
) -> None:
    seq = source.snapshot().last_seq + 1
    source.advance(seq)
    world._consume(  # noqa: SLF001 - typed integration fixture
        _event(
            "chat.message",
            seq,
            {
                "channel": "public",
                "message": {
                    "player_id": player_id,
                    "display_name": player_id.upper(),
                    "message": message,
                },
            },
        )
    )


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), timeout)


class SpeakingFrequencyControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_none_is_exact_compatibility_and_has_no_frequency_state(self) -> None:
        reaction, _world, _source, brain, _sender = _make_stack(None)
        self.assertIsNone(reaction.snapshot().frequency_state)
        self.assertFalse(hasattr(reaction, "_frequency_evaluation_count"))
        self.assertFalse(hasattr(reaction, "_frequency_source_fingerprints"))
        reaction.start()
        try:
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            self.assertEqual(len(brain.requests), 1)
            outcome = reaction.snapshot().outcomes[-1]
            self.assertIsNone(outcome.frequency_suppression)
            self.assertIsNone(outcome.frequency_draw)
            self.assertIsNone(reaction.snapshot().frequency_state)
        finally:
            await reaction.stop()

    async def test_callback_absent_fails_before_profile_access_or_state_mutation(self) -> None:
        legacy, world, _source, brain, _sender = _make_stack(None)

        class UnreadPolicy:
            def __init__(self) -> None:
                self.profile_accessed = False

            @property
            def profile(self):
                self.profile_accessed = True
                raise AssertionError("profile must not be read")

            def prepare(self, opportunity):  # pragma: no cover - construction fails
                raise AssertionError

            def evaluate(self, prepared):  # pragma: no cover - construction fails
                raise AssertionError

        for invoker in (
            _CallbackAbsentArbiter(controller=legacy.invoker.controller),
            _VariadicOnlyArbiter(controller=legacy.invoker.controller),
            _RequiredCallbackArbiter(controller=legacy.invoker.controller),
            _NonNoneDefaultCallbackArbiter(controller=legacy.invoker.controller),
        ):
            policy = UnreadPolicy()
            with self.subTest(invoker=type(invoker).__name__), self.assertRaisesRegex(
                RuntimeError,
                "^frequency_policy requires BrainInvocationArbiter.on_brain_start$",
            ):
                ReactionChatController(
                    world=world,
                    invoker=invoker,
                    master_seed=7,
                    config=legacy.config,
                    frequency_policy=policy,  # type: ignore[arg-type]
                )
            self.assertFalse(policy.profile_accessed)
        self.assertEqual(brain.requests, [])
        self.assertIsNone(legacy.snapshot().frequency_state)

    async def test_complete_structural_policy_is_accepted_and_profile_read_once(self) -> None:
        holder = {}

        class StructuralPolicy:
            def __init__(self, profile: SpeakingProfile) -> None:
                self._profile = profile
                self.profile_reads = 0
                self.delegate = DeterministicSpeakingFrequencyPolicy(
                    profile=profile, master_seed=7
                )

            @property
            def profile(self) -> SpeakingProfile:
                self.profile_reads += 1
                return self._profile

            def prepare(self, opportunity):
                return self.delegate.prepare(opportunity)

            def evaluate(self, prepared):
                return self.delegate.evaluate(prepared)

        def factory(profile):
            policy = StructuralPolicy(profile)
            holder["policy"] = policy
            return policy

        reaction, _world, _source, brain, _sender = _make_stack(
            SpeakingProfile(), policy_factory=factory
        )
        policy = holder["policy"]
        self.assertEqual(policy.profile_reads, 1)
        reaction.start()
        try:
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            self.assertEqual(policy.profile_reads, 1)
            self.assertEqual(len(brain.requests), 1)
        finally:
            await reaction.stop()

    async def test_initial_public_vector_has_exact_outcome_and_final_state(self) -> None:
        reaction, _world, _source, brain, _sender = _make_stack(SpeakingProfile())
        reaction.start()
        try:
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            snapshot = reaction.snapshot()
            self.assertEqual(len(brain.requests), 1)
            self.assertEqual(
                snapshot.frequency_state,
                SpeakingFrequencyState(
                    ReactionPhaseKey(1, 1, "day", 3), 1, 1, snapshot.frequency_state.last_accepted_chat_at, ()  # type: ignore[union-attr]
                ),
            )
            outcome = snapshot.outcomes[-1]
            self.assertEqual(outcome.status, ReactionOutcomeStatus.ACCEPTED)
            self.assertEqual(outcome.frequency_evaluation_ordinal, 1)
            self.assertEqual(outcome.frequency_event_importance, 1.0)
            self.assertEqual(outcome.frequency_threshold, 0.5)
            self.assertEqual(outcome.frequency_draw, 0.17800791926725024)
            self.assertIsNone(outcome.frequency_source_fingerprint)
            self.assertIsNone(outcome.frequency_suppression)
        finally:
            await reaction.stop()

    async def test_evaluation_precedes_claim_and_claim_alone_commits_brain_count(self) -> None:
        reaction, _world, _source, brain, _sender = _make_stack(SpeakingProfile())
        gate = _CallbackGateArbiter(controller=reaction.invoker.controller)
        reaction.invoker = gate
        reaction.start()
        try:
            await asyncio.wait_for(gate.waiting_before_claim.wait(), 1)
            waiting = reaction.snapshot()
            self.assertEqual(waiting.frequency_state.evaluation_count, 1)  # type: ignore[union-attr]
            self.assertEqual(waiting.frequency_state.committed_brain_invocations, 0)  # type: ignore[union-attr]
            self.assertEqual(waiting.frequency_state.recent_source_fingerprints, ())  # type: ignore[union-attr]
            self.assertEqual(brain.requests, [])
            gate.allow_claim.set()
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            claimed = reaction.snapshot()
            self.assertEqual(claimed.frequency_state.evaluation_count, 1)  # type: ignore[union-attr]
            self.assertEqual(claimed.frequency_state.committed_brain_invocations, 1)  # type: ignore[union-attr]
            self.assertEqual(len(brain.requests), 1)
        finally:
            await reaction.stop()

    async def test_reaction_public_vector_has_exact_outcome_and_final_state(self) -> None:
        reaction, world, source, brain, _sender = _make_stack(
            SpeakingProfile(), preload_self_history=True
        )
        reaction.start()
        try:
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 1)
            self.assertEqual(
                reaction.snapshot().outcomes[0].frequency_suppression,
                FrequencySuppression.SELF_CHAIN,
            )
            _emit_chat(world, source, player_id="p7", message="hello p2")
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            snapshot = reaction.snapshot()
            self.assertEqual(len(brain.requests), 1)
            self.assertEqual(snapshot.frequency_state.evaluation_count, 1)  # type: ignore[union-attr]
            self.assertEqual(snapshot.frequency_state.committed_brain_invocations, 1)  # type: ignore[union-attr]
            self.assertEqual(
                snapshot.frequency_state.recent_source_fingerprints,  # type: ignore[union-attr]
                (_SOURCE_FINGERPRINT,),
            )
            outcome = snapshot.outcomes[-1]
            self.assertEqual(outcome.trigger.source_order, 42)
            self.assertEqual(outcome.frequency_evaluation_ordinal, 1)
            self.assertEqual(outcome.frequency_event_importance, 1.0)
            self.assertEqual(outcome.frequency_threshold, 0.5)
            self.assertEqual(outcome.frequency_draw, 0.19174690774662817)
            self.assertEqual(
                outcome.frequency_source_fingerprint, _SOURCE_FINGERPRINT
            )
            self.assertIsNone(outcome.frequency_suppression)
        finally:
            await reaction.stop()

    async def test_controller_vectors_are_identical_in_two_spawned_processes(self) -> None:
        script = """
import asyncio, json, sys
from tests.test_phase5_speaking_frequency import _emit_chat, _make_stack, _wait_until
from ai_client.reaction_chat.frequency import SpeakingProfile

async def main():
    reaction_mode = sys.argv[1] == 'reaction_chat'
    reaction, world, source, brain, sender = _make_stack(
        SpeakingProfile(), preload_self_history=reaction_mode
    )
    reaction.start()
    try:
        if reaction_mode:
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 1)
            _emit_chat(world, source, player_id='p7', message='hello p2')
        await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
        snapshot = reaction.snapshot()
        outcome = snapshot.outcomes[-1]
        state = snapshot.frequency_state
        print(json.dumps({
            'source_order': outcome.trigger.source_order,
            'fingerprint': outcome.frequency_source_fingerprint,
            'importance': outcome.frequency_event_importance,
            'threshold': outcome.frequency_threshold,
            'draw': outcome.frequency_draw,
            'suppression': outcome.frequency_suppression,
            'status': outcome.status.value,
            'evaluations': state.evaluation_count,
            'committed': state.committed_brain_invocations,
            'recent': state.recent_source_fingerprints,
            'brain_calls': len(brain.requests),
        }, sort_keys=True))
    finally:
        await reaction.stop()

asyncio.run(main())
"""
        expected = (
            {
                "source_order": None,
                "fingerprint": None,
                "importance": 1.0,
                "threshold": 0.5,
                "draw": 0.17800791926725024,
                "suppression": None,
                "status": "accepted",
                "evaluations": 1,
                "committed": 1,
                "recent": [],
                "brain_calls": 1,
            },
            {
                "source_order": 42,
                "fingerprint": _SOURCE_FINGERPRINT,
                "importance": 1.0,
                "threshold": 0.5,
                "draw": 0.19174690774662817,
                "suppression": None,
                "status": "accepted",
                "evaluations": 1,
                "committed": 1,
                "recent": [_SOURCE_FINGERPRINT],
                "brain_calls": 1,
            },
        )
        for kind, result in zip(
            ("initial_chat", "reaction_chat"), expected, strict=True
        ):
            completed = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-c", script, kind],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(json.loads(completed.stdout), result)

    async def test_probability_suppression_is_pre_brain_and_records_evidence(self) -> None:
        reaction, _world, _source, brain, sender = _make_stack(
            SpeakingProfile(talkativeness=0)
        )
        reaction.start()
        try:
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 1)
            snapshot = reaction.snapshot()
            self.assertEqual(brain.requests, [])
            self.assertEqual(sender.calls, [])
            self.assertEqual(snapshot.frequency_state.evaluation_count, 1)  # type: ignore[union-attr]
            self.assertEqual(snapshot.frequency_state.committed_brain_invocations, 0)  # type: ignore[union-attr]
            outcome = snapshot.outcomes[0]
            self.assertEqual(outcome.status, ReactionOutcomeStatus.FREQUENCY_SUPPRESSED)
            self.assertEqual(outcome.frequency_suppression, FrequencySuppression.PROBABILITY)
            self.assertEqual(outcome.frequency_evaluation_ordinal, 1)
        finally:
            await reaction.stop()

    async def test_invocation_cap_precedes_prepare_and_makes_no_second_brain_call(self) -> None:
        reaction, world, source, brain, _sender = _make_stack(
            SpeakingProfile(talkativeness=1, cooldown_seconds=0),
            max_chat_attempts=1,
        )
        reaction.start()
        try:
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            _emit_chat(world, source, player_id="p7", message="new peer message")
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 2)
            outcome = reaction.snapshot().outcomes[-1]
            self.assertEqual(len(brain.requests), 1)
            self.assertEqual(outcome.frequency_suppression, FrequencySuppression.INVOCATION_CAP)
            self.assertIsNone(outcome.frequency_event_importance)
            self.assertIsNone(outcome.frequency_evaluation_ordinal)
        finally:
            await reaction.stop()

    async def test_evaluation_cap_is_a_hard_suppression_before_prepare(self) -> None:
        reaction, _world, _source, brain, _sender = _make_stack(
            SpeakingProfile(max_trigger_evaluations_per_phase=0)
        )
        reaction.start()
        try:
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 1)
            outcome = reaction.snapshot().outcomes[0]
            self.assertEqual(brain.requests, [])
            self.assertEqual(outcome.frequency_suppression, FrequencySuppression.EVALUATION_CAP)
            self.assertIsNone(outcome.frequency_event_importance)
        finally:
            await reaction.stop()

    async def test_repetition_is_bounded_and_does_not_increment_evaluations(self) -> None:
        reaction, world, source, brain, _sender = _make_stack(
            SpeakingProfile(talkativeness=0, cooldown_seconds=0, repetition_window=1)
        )
        reaction.start()
        try:
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 1)
            _emit_chat(world, source, player_id="p7", message="repeat")
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 2)
            first_fingerprint = reaction.snapshot().outcomes[-1].frequency_source_fingerprint
            _emit_chat(world, source, player_id="p7", message="repeat")
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 3)
            snapshot = reaction.snapshot()
            self.assertEqual(brain.requests, [])
            self.assertEqual(snapshot.frequency_state.evaluation_count, 2)  # type: ignore[union-attr]
            self.assertEqual(snapshot.frequency_state.recent_source_fingerprints, (first_fingerprint,))  # type: ignore[union-attr]
            self.assertEqual(
                snapshot.outcomes[-1].frequency_suppression,
                FrequencySuppression.REPETITION,
            )
            self.assertIsNone(snapshot.outcomes[-1].frequency_evaluation_ordinal)
        finally:
            await reaction.stop()

    async def test_self_chain_precedes_cooldown_and_probability(self) -> None:
        reaction, world, source, brain, _sender = _make_stack(
            SpeakingProfile(talkativeness=1, cooldown_seconds=1)
        )
        reaction.start()
        try:
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            _emit_chat(world, source, player_id="p7", message="peer")
            _emit_chat(world, source, player_id="p2", message="external self chat")
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 2)
            outcome = reaction.snapshot().outcomes[-1]
            self.assertEqual(len(brain.requests), 1)
            self.assertEqual(outcome.frequency_suppression, FrequencySuppression.SELF_CHAIN)
            self.assertIsNotNone(outcome.frequency_source_fingerprint)
            self.assertIsNone(outcome.frequency_threshold)
        finally:
            await reaction.stop()

    async def test_cooldown_defers_once_without_mutation_then_evaluates(self) -> None:
        reaction, world, source, brain, _sender = _make_stack(
            SpeakingProfile(talkativeness=1, cooldown_seconds=0.10)
        )
        reaction.start()
        try:
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            _emit_chat(world, source, player_id="p7", message="peer after self")
            await asyncio.sleep(0.02)
            interim = reaction.snapshot()
            self.assertEqual(len(interim.outcomes), 1)
            self.assertEqual(interim.frequency_state.evaluation_count, 1)  # type: ignore[union-attr]
            self.assertTrue(reaction._pending_reaction.cooldown_deferred)  # type: ignore[union-attr]  # noqa: SLF001
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 2)
            final = reaction.snapshot()
            self.assertEqual(final.frequency_state.evaluation_count, 2)  # type: ignore[union-attr]
            self.assertIn(len(brain.requests), {1, 2})
        finally:
            await reaction.stop()

    async def test_already_deferred_opportunity_still_in_cooldown_is_suppressed(self) -> None:
        clock = _FakeClock(10.0)
        reaction, world, source, brain, _sender = _make_stack(
            SpeakingProfile(talkativeness=1, cooldown_seconds=0.03),
            clock=clock,
        )
        reaction.start()
        try:
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            _emit_chat(world, source, player_id="p7", message="peer while clock is frozen")
            await asyncio.sleep(0.01)
            interim = reaction.snapshot()
            self.assertEqual(len(interim.outcomes), 1)
            self.assertEqual(interim.frequency_state.evaluation_count, 1)  # type: ignore[union-attr]
            self.assertTrue(reaction._pending_reaction.cooldown_deferred)  # type: ignore[union-attr]  # noqa: SLF001
            reaction._last_accepted_chat_at = 10.02  # noqa: SLF001 - later correlated-acceptance race
            clock.value = 10.03
            world._commit()  # noqa: SLF001 - wake the deterministic fake-clock fixture
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 2)
            final = reaction.snapshot()
            self.assertEqual(len(brain.requests), 1)
            self.assertEqual(final.frequency_state.evaluation_count, 1)  # type: ignore[union-attr]
            self.assertEqual(
                final.outcomes[-1].frequency_suppression,
                FrequencySuppression.COOLDOWN,
            )
            self.assertIsNotNone(final.outcomes[-1].frequency_event_importance)
            self.assertIsNone(final.outcomes[-1].frequency_evaluation_ordinal)
        finally:
            await reaction.stop()

    async def test_post_evaluation_deadline_retains_evidence_without_brain(self) -> None:
        clock = _FakeClock(10.0)

        class ExpiringPolicy:
            def __init__(self, profile: SpeakingProfile) -> None:
                self._profile = profile
                self.delegate = DeterministicSpeakingFrequencyPolicy(
                    profile=profile, master_seed=7
                )

            @property
            def profile(self) -> SpeakingProfile:
                return self._profile

            def prepare(self, opportunity):
                return self.delegate.prepare(opportunity)

            def evaluate(self, prepared):
                decision = self.delegate.evaluate(prepared)
                clock.value = 20.0
                return decision

        reaction, world, source, brain, sender = _make_stack(
            SpeakingProfile(),
            preload_self_history=True,
            clock=clock,
            policy_factory=ExpiringPolicy,
        )
        reaction.start()
        try:
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 1)
            self.assertEqual(
                reaction.snapshot().outcomes[0].frequency_suppression,
                FrequencySuppression.SELF_CHAIN,
            )
            _emit_chat(world, source, player_id="p7", message="hello p2")
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 2)
            snapshot = reaction.snapshot()
            outcome = snapshot.outcomes[-1]
            self.assertEqual(outcome.status, ReactionOutcomeStatus.DEADLINE_SUPPRESSED)
            self.assertEqual(outcome.frequency_evaluation_ordinal, 1)
            self.assertEqual(outcome.frequency_event_importance, 1.0)
            self.assertEqual(outcome.frequency_threshold, 0.5)
            self.assertEqual(outcome.frequency_draw, 0.19174690774662817)
            self.assertEqual(outcome.frequency_source_fingerprint, _SOURCE_FINGERPRINT)
            self.assertIsNone(outcome.frequency_suppression)
            self.assertEqual(snapshot.frequency_state.evaluation_count, 1)  # type: ignore[union-attr]
            self.assertEqual(snapshot.frequency_state.committed_brain_invocations, 0)  # type: ignore[union-attr]
            self.assertEqual(
                snapshot.frequency_state.recent_source_fingerprints,  # type: ignore[union-attr]
                (_SOURCE_FINGERPRINT,),
            )
            self.assertEqual(snapshot.deadline_suppressed_count, 1)
            self.assertEqual(brain.requests, [])
            self.assertEqual(sender.calls, [])
        finally:
            await reaction.stop()

    async def test_latest_one_coalescing_only_evaluates_the_newest_source(self) -> None:
        reaction, world, source, _brain, _sender = _make_stack(
            SpeakingProfile(talkativeness=0, cooldown_seconds=0)
        )
        reaction.start()
        try:
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 1)
            _emit_chat(world, source, player_id="p7", message="old")
            _emit_chat(world, source, player_id="p1", message="new")
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 2)
            policy = DeterministicSpeakingFrequencyPolicy(
                profile=SpeakingProfile(talkativeness=0, cooldown_seconds=0),
                master_seed=7,
            )
            expected = policy.prepare(
                SpeakingOpportunity(
                    ReactionPhaseKey(1, 1, "day", 3),
                    ReactionTriggerKind.REACTION_CHAT,
                    2,
                    "p1",
                    "public",
                    "new",
                    "p2",
                    "P2",
                    0,
                )
            ).source_message_sha256
            snapshot = reaction.snapshot()
            self.assertEqual(snapshot.frequency_state.evaluation_count, 2)  # type: ignore[union-attr]
            self.assertEqual(snapshot.frequency_state.recent_source_fingerprints, (expected,))  # type: ignore[union-attr]
        finally:
            await reaction.stop()

    async def test_new_phase_resets_all_frequency_state(self) -> None:
        reaction, world, source, _brain, _sender = _make_stack(
            SpeakingProfile(talkativeness=0, cooldown_seconds=0)
        )
        reaction.start()
        try:
            await _wait_until(lambda: reaction.snapshot().frequency_state.evaluation_count == 1)  # type: ignore[union-attr]
            source.replace_phase(seq=2, day=2, action_generation=4, chat=False)
            world._consume(_event("game.state_sync", 2, _sync_payload(day=2, chat=False), day=2))  # noqa: SLF001
            mapped_at = time.monotonic()
            world._consume(  # noqa: SLF001
                PhaseTimingMapped("day", 2, 2, 1, 4, 102, 112, mapped_at, mapped_at + 10)
            )
            await _wait_until(
                lambda: reaction.snapshot().frequency_state.phase_key  # type: ignore[union-attr]
                == ReactionPhaseKey(1, 2, "day", 4)
            )
            self.assertEqual(
                reaction.snapshot().frequency_state,
                SpeakingFrequencyState(ReactionPhaseKey(1, 2, "day", 4), 0, 0, None, ()),
            )
        finally:
            await reaction.stop()

    async def test_invalid_injected_policy_uses_failed_path_without_mutation(self) -> None:
        class InvalidPolicy:
            profile = SpeakingProfile()

            def prepare(self, opportunity):
                return "not prepared"

            def evaluate(self, prepared):  # pragma: no cover - must not run
                raise AssertionError

        reaction, world, source, brain, sender = _make_stack(None)
        await reaction.stop()
        controller = BrainController(
            world=world,
            sender=sender,  # type: ignore[arg-type]
            brain=brain,
            config=BrainRunConfig(max_decision_seconds=0.2),
        )
        reaction = ReactionChatController(
            world=world,
            invoker=_ImmediateCallbackArbiter(controller=controller),
            master_seed=7,
            config=ReactionChatConfig(
                initial_jitter_seconds=(0, 0),
                reaction_jitter_seconds=(0, 0),
                minimum_accepted_chat_interval_seconds=0,
                deadline_guard_seconds=0.01,
                minimum_start_budget_seconds=0.001,
                brain_timeout_seconds=0.1,
            ),
            frequency_policy=InvalidPolicy(),  # type: ignore[arg-type]
        )
        reaction.start()
        exit_result = await reaction.wait()
        self.assertEqual(exit_result.error_type, "TypeError")
        snapshot = reaction.snapshot()
        self.assertEqual(brain.requests, [])
        self.assertEqual(snapshot.frequency_state.evaluation_count, 0)  # type: ignore[union-attr]
        self.assertEqual(snapshot.frequency_state.recent_source_fingerprints, ())  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
