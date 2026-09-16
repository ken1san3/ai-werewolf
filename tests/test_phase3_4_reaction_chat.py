from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
import math
import time
import unittest

from ai_client.brain import (
    BrainController,
    BrainInvocationArbiter,
    BrainRunConfig,
    ChatDecision,
    CoDeclareDecision,
    FeatureControllerExit,
    FeatureControllerExitReason,
    NoDecision,
)
from ai_client.network import (
    ActionRejected,
    ChatAction,
    ClientLifecycle,
    ClientSnapshot,
    CoDeclareAction,
    PhaseDeadlineReached,
    PhaseTimingMapped,
    SendReceipt,
    ServerEvent,
)
from ai_client.network.types import immutable_mapping
from ai_client.reaction_chat import (
    CoGenerationState,
    ReactionChatConfig,
    ReactionChatController,
    ReactionChatLifecycle,
    ReactionOutcomeStatus,
    ReactionPhaseKey,
    ReactionTriggerKind,
    deterministic_jitter_seconds,
)
from ai_client.world import (
    ActionRejectionObservation,
    CurrentPhaseDeadline,
    Freshness,
    PhaseTimingObservation,
    TransportObservationKind,
    TransportObservationQuery,
    TransportObservationRetention,
    WorldState,
    WorldStateConfig,
)


def _event(message_type: str, seq: int, payload: dict) -> ServerEvent:
    return ServerEvent(
        type=message_type,
        protocol_version="1.0",
        event_id=f"event-{seq}",
        game_id="game-1",
        seq=seq,
        timestamp=100,
        payload=immutable_mapping(payload),
    )


def _sync_payload(
    *, history: list[dict] | None = None, self_player_id: str = "p0"
) -> dict:
    player_ids = ["p0", "p1"]
    if self_player_id not in player_ids:
        player_ids.append(self_player_id)
    return {
        "players": [
            {"player_id": player_id, "display_name": player_id.upper()}
            for player_id in player_ids
        ],
        "deaths": [],
        "action_state": {
            "phase": "day",
            "day": 1,
            "phase_ends_at": 110,
            "actions": [{"type": "chat", "channel": "public"}],
        },
        "self": {
            "player_id": self_player_id,
            "role_id": "villager",
            "modifier_ids": [],
        },
        "revealed_roles": [],
        "history": [] if history is None else history,
    }


class _Source:
    def __init__(self, actions: tuple[object, ...], *, player_id: str = "p0") -> None:
        self._snapshot = ClientSnapshot(
            lifecycle=ClientLifecycle.CONNECTED,
            player_id=player_id,
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


class _Brain:
    def __init__(self, *, silent: bool = False) -> None:
        self.silent = silent
        self.requests = []

    async def decide(self, request):
        self.requests.append(request)
        if self.silent or not request.action_context.options:
            return NoDecision()
        option = request.action_context.options[0]
        if isinstance(option.handle, ChatAction):
            return ChatDecision(option.option_id, f"message-{len(self.requests)}")
        return NoDecision()


class _BlockingBrain(_Brain):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def decide(self, request):
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        option = request.action_context.options[0]
        return ChatDecision(option.option_id, f"message-{len(self.requests)}")


class _Sender:
    def __init__(self, world: WorldState, source: _Source, *, reject: bool = False) -> None:
        self.world = world
        self.source = source
        self.reject = reject
        self.calls = []
        self.chat_call_times: list[float] = []
        self._seq = 1

    async def send_chat(self, handle: ChatAction, message: str) -> SendReceipt:
        self.calls.append((handle, message))
        self.chat_call_times.append(time.monotonic())
        self._seq = max(self._seq, self.source.snapshot().last_seq)
        self._seq += 1
        self.source.advance(self._seq)
        if self.reject:
            self.world._consume(  # noqa: SLF001 - layer integration fixture
                _event(
                    "action.rejected",
                    self._seq,
                    {"action": "chat.send", "reason": "action_unavailable"},
                )
            )
            self.world._consume(  # noqa: SLF001
                ActionRejected(
                    "chat.send", "action_unavailable", self._seq, 1, time.monotonic()
                )
            )
        else:
            player_id = self.source.snapshot().player_id
            assert player_id is not None
            self.world._consume(  # noqa: SLF001
                _event(
                    "chat.message",
                    self._seq,
                    {
                        "channel": handle.channel,
                        "message": {
                            "player_id": player_id,
                            "display_name": player_id.upper(),
                            "message": message,
                        },
                    },
                )
            )
        return SendReceipt(f"request-{self._seq}", 1)


class _CoSender(_Sender):
    async def send_co_declare(
        self, handle: CoDeclareAction, claimed_role_id: str, comment: str
    ) -> SendReceipt:
        self.calls.append((handle, claimed_role_id, comment))
        self._seq = max(self._seq, self.source.snapshot().last_seq) + 1
        self.source.advance(self._seq)
        player_id = self.source.snapshot().player_id
        assert player_id is not None
        self.world._consume(  # noqa: SLF001 - layer integration fixture
            _event(
                "game.event",
                self._seq,
                {
                    "event_type": "CO_DECLARED",
                    "event_payload": {
                        "player_id": player_id,
                        "claimed_role_id": claimed_role_id,
                        "comment": comment,
                    },
                },
            )
        )
        return SendReceipt(f"request-{self._seq}", 1)


class _DeferredAcceptanceSender(_Sender):
    async def send_chat(self, handle: ChatAction, message: str) -> SendReceipt:
        self.calls.append((handle, message))
        self.chat_call_times.append(time.monotonic())
        return SendReceipt(f"request-{len(self.calls)}", 1)


class _FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance_to(self, now: float) -> None:
        if now < self.now:
            raise ValueError("fake clock must be monotonic")
        self.now = now


def _make_stack(
    *,
    brain: _Brain | None = None,
    reject: bool = False,
    with_co: bool = False,
    config: ReactionChatConfig | None = None,
    world_config: WorldStateConfig = WorldStateConfig(),
    transport_retention: TransportObservationRetention = TransportObservationRetention(),
    player_id: str = "p0",
    master_seed: int = 17,
    clock=time.monotonic,
):
    common = dict(
        connection_generation=1,
        action_generation=1,
        phase="day",
        day=1,
    )
    chat = ChatAction(**common, type="chat", channel="public")
    actions: tuple[object, ...] = (chat,)
    if with_co:
        actions += (
            CoDeclareAction(
                **common,
                type="co_declare",
                claimed_role_ids=("villager",),
            ),
        )
    source = _Source(actions, player_id=player_id)
    world = WorldState(
        source,
        config=world_config,
        transport_retention=transport_retention,
    )
    world._consume(  # noqa: SLF001
        _event("game.state_sync", 1, _sync_payload(self_player_id=player_id))
    )
    mapped_at = clock()
    world._consume(  # noqa: SLF001
        PhaseTimingMapped(
            phase="day",
            day=1,
            source_seq=1,
            connection_generation=1,
            action_generation=1,
            server_timestamp=100,
            phase_ends_at=110,
            mapped_at_monotonic=mapped_at,
            local_deadline_monotonic=mapped_at + 10,
        )
    )
    chosen_brain = brain or _Brain()
    sender = _Sender(world, source, reject=reject)
    brain_controller = BrainController(
        world=world,
        sender=sender,  # type: ignore[arg-type]
        brain=chosen_brain,
        config=BrainRunConfig(max_decision_seconds=0.2),
        clock=clock,
    )
    invoker = BrainInvocationArbiter(controller=brain_controller, clock=clock)
    reaction = ReactionChatController(
        world=world,
        invoker=invoker,
        master_seed=master_seed,
        config=config
        or ReactionChatConfig(
            initial_jitter_seconds=(0, 0),
            reaction_jitter_seconds=(0, 0),
            minimum_accepted_chat_interval_seconds=0.01,
            deadline_guard_seconds=0.01,
            minimum_start_budget_seconds=0.001,
            brain_timeout_seconds=0.1,
        ),
        clock=clock,
    )
    return reaction, brain_controller, world, source, sender, chosen_brain


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), timeout)


async def _yield_until(predicate, attempts: int = 1000) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true within deterministic yields")


class ReactionTypeAndRandomnessTests(unittest.TestCase):
    def test_public_values_are_frozen_and_validate_bool_numeric_inputs(self) -> None:
        key = ReactionPhaseKey(1, 1, "day", 2)
        with self.assertRaises(FrozenInstanceError):
            key.day = 2  # type: ignore[misc]
        with self.assertRaises(ValueError):
            ReactionPhaseKey(True, 1, "day", 2)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ReactionChatConfig(outcome_retention=0)
        with self.assertRaises(ValueError):
            ReactionChatConfig(initial_jitter_seconds=(0.2, 0.1))
        exit_result = FeatureControllerExit(
            "reaction_chat", FeatureControllerExitReason.STOP_REQUESTED
        )
        with self.assertRaises(FrozenInstanceError):
            exit_result.reason = FeatureControllerExitReason.FAILED  # type: ignore[misc]
        with self.assertRaises(ValueError):
            FeatureControllerExit(
                "reaction_chat",
                FeatureControllerExitReason.WORLD_ENDED,
                "RuntimeError",
            )
        with self.assertRaises(ValueError):
            deterministic_jitter_seconds(
                master_seed=1,
                player_id="p0",
                phase_key=key,
                trigger_kind=ReactionTriggerKind.INITIAL_CHAT,
                source_order=None,
                attempt_ordinal=0,
                lower_seconds=math.nan,
                upper_seconds=1,
            )

    def test_jitter_is_exactly_reproducible_and_range_independent(self) -> None:
        key = ReactionPhaseKey(1, 1, "day", 2)
        arguments = dict(
            master_seed=41,
            player_id="p0",
            phase_key=key,
            trigger_kind=ReactionTriggerKind.REACTION_CHAT,
            source_order=7,
            attempt_ordinal=1,
        )
        first = deterministic_jitter_seconds(
            **arguments, lower_seconds=0, upper_seconds=1
        )
        repeated = deterministic_jitter_seconds(
            **arguments, lower_seconds=0, upper_seconds=1
        )
        remapped = deterministic_jitter_seconds(
            **arguments, lower_seconds=10, upper_seconds=20
        )
        self.assertEqual(first, repeated)
        self.assertEqual(first, 0.2383528302007011)
        self.assertEqual(remapped, 10 + 10 * first)
        self.assertNotEqual(
            first,
            deterministic_jitter_seconds(
                **{**arguments, "master_seed": 42}, lower_seconds=0, upper_seconds=1
            ),
        )
        players = tuple(f"player-{index}" for index in range(9))
        first_due = {
            seed: min(
                players,
                key=lambda player_id: deterministic_jitter_seconds(
                    master_seed=seed,
                    player_id=player_id,
                    phase_key=ReactionPhaseKey(1, 1, "day", 1),
                    trigger_kind=ReactionTriggerKind.INITIAL_CHAT,
                    source_order=None,
                    attempt_ordinal=0,
                    lower_seconds=0,
                    upper_seconds=0.2,
                ),
            )
            for seed in (7341, 7342)
        }
        self.assertEqual(first_due, {7341: "player-5", 7342: "player-1"})

    def test_transport_query_and_retention_values_are_strict(self) -> None:
        self.assertEqual(TransportObservationRetention().max_records, 256)
        query = TransportObservationQuery(
            after_order=0,
            kinds=frozenset({TransportObservationKind.PHASE_TIMING}),
        )
        self.assertEqual(query.kinds, frozenset({TransportObservationKind.PHASE_TIMING}))
        with self.assertRaises(ValueError):
            TransportObservationRetention(max_records=0)


class ReactionControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_clock_controller_runs_are_reproducible_and_seed_separated(
        self,
    ) -> None:
        config = ReactionChatConfig(
            max_chat_attempts_per_phase=1,
            initial_jitter_seconds=(0.0, 0.2),
            reaction_jitter_seconds=(0.05, 0.15),
            minimum_accepted_chat_interval_seconds=0,
            deadline_guard_seconds=0.01,
            minimum_start_budget_seconds=0.001,
            brain_timeout_seconds=0.1,
        )

        async def run(seed: int):
            outcomes = []
            for index in range(9):
                player_id = f"player-{index}"
                clock = _FakeClock(10.0)
                reaction, _controller, world, _source, _sender, _brain = _make_stack(
                    player_id=player_id,
                    master_seed=seed,
                    clock=clock,
                    config=config,
                )
                reaction.start()
                try:
                    await _yield_until(
                        lambda: reaction._pending_initial is not None  # noqa: SLF001
                    )
                    clock.advance_to(10.5)
                    world._commit()  # noqa: SLF001 - wake the fake-clock due wait
                    await _yield_until(
                        lambda: reaction.snapshot().accepted_count == 1
                    )
                    outcome = reaction.snapshot().outcomes[-1]
                    outcomes.append(
                        (
                            player_id,
                            outcome.scheduled_due_monotonic,
                            outcome.trigger.kind,
                            outcome.trigger.attempt_ordinal,
                            outcome.status,
                        )
                    )
                finally:
                    await reaction.stop()
            return (
                tuple(sorted(outcomes, key=lambda item: item[1])),
                tuple(outcomes),
            )

        first = await run(7341)
        repeated = await run(7341)
        other_seed = await run(7342)
        self.assertEqual(first, repeated)
        self.assertEqual(first[0][0][0], "player-5")
        self.assertEqual(other_seed[0][0][0], "player-1")
        self.assertNotEqual(first[0][0][0], other_seed[0][0][0])

    async def test_chat_handle_absence_suppresses_initial_opportunity(self) -> None:
        reaction, _controller, world, source, sender, brain = _make_stack()
        source._snapshot = replace(source._snapshot, actions=())  # noqa: SLF001
        world._commit()  # noqa: SLF001 - wake the controller on a coherent fake view
        reaction.start()
        try:
            await asyncio.sleep(0.03)
            self.assertEqual(brain.requests, [])
            self.assertEqual(sender.calls, [])
            self.assertEqual(reaction.snapshot().chat_brain_invocations, 0)
        finally:
            await reaction.stop()

    async def test_initial_then_same_channel_other_chat_reacts_and_accepts(self) -> None:
        reaction, _controller, world, source, sender, brain = _make_stack()
        reaction.start()
        try:
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            source.advance(3)
            world._consume(  # noqa: SLF001
                _event(
                    "chat.message",
                    3,
                    {
                        "channel": "public",
                        "message": {
                            "player_id": "p1",
                            "display_name": "P1",
                            "message": "other",
                        },
                    },
                )
            )
            await _wait_until(lambda: reaction.snapshot().accepted_count == 2)
            snapshot = reaction.snapshot()
            self.assertEqual(snapshot.chat_brain_invocations, 2)
            self.assertEqual(snapshot.send_count, 2)
            self.assertEqual(len(sender.calls), 2)
            self.assertGreaterEqual(
                sender.chat_call_times[1] - sender.chat_call_times[0], 0.01
            )
            self.assertEqual(len(brain.requests), 2)
            self.assertEqual(
                [outcome.status for outcome in snapshot.outcomes],
                [ReactionOutcomeStatus.ACCEPTED, ReactionOutcomeStatus.ACCEPTED],
            )
        finally:
            await reaction.stop()

    async def test_default_config_enforces_exact_accepted_chat_interval(self) -> None:
        clock = _FakeClock(10.0)
        config = ReactionChatConfig()
        reaction, controller, world, source, _sender, _brain = _make_stack(
            clock=clock,
            config=config,
        )

        class ClockedSender(_Sender):
            def __init__(self) -> None:
                super().__init__(world, source)
                self.accepted_at: list[float] = []

            async def send_chat(self, handle: ChatAction, message: str) -> SendReceipt:
                self.accepted_at.append(clock())
                return await super().send_chat(handle, message)

        sender = ClockedSender()
        controller.sender = sender  # type: ignore[assignment]
        reaction.start()
        try:
            await _yield_until(
                lambda: reaction._pending_initial is not None  # noqa: SLF001
            )
            initial_due = reaction._pending_initial.due  # type: ignore[union-attr]  # noqa: SLF001
            clock.advance_to(initial_due)
            world._commit()  # noqa: SLF001 - deterministic fake-clock wake
            await _yield_until(lambda: reaction.snapshot().accepted_count == 1)
            first_accepted_at = sender.accepted_at[0]

            next_seq = source.snapshot().last_seq + 1
            source.advance(next_seq)
            world._consume(  # noqa: SLF001
                _event(
                    "chat.message",
                    next_seq,
                    {
                        "channel": "public",
                        "message": {
                            "player_id": "p1",
                            "display_name": "P1",
                            "message": "default interval trigger",
                        },
                    },
                )
            )
            await _yield_until(
                lambda: reaction._pending_reaction is not None  # noqa: SLF001
            )
            exact_boundary = (
                first_accepted_at
                + config.minimum_accepted_chat_interval_seconds
            )
            self.assertEqual(config.minimum_accepted_chat_interval_seconds, 0.20)
            self.assertAlmostEqual(
                reaction._pending_reaction.due,  # type: ignore[union-attr]  # noqa: SLF001
                exact_boundary,
            )

            clock.advance_to(exact_boundary - 0.000001)
            world._commit()  # noqa: SLF001
            for _ in range(20):
                await asyncio.sleep(0)
            self.assertEqual(len(sender.accepted_at), 1)

            clock.advance_to(exact_boundary)
            world._commit()  # noqa: SLF001
            await _yield_until(lambda: reaction.snapshot().accepted_count == 2)
            self.assertGreaterEqual(sender.accepted_at[1], exact_boundary)
        finally:
            await reaction.stop()

    async def test_no_decision_and_invalid_decision_each_consume_chat_cap(self) -> None:
        class SequenceBrain:
            def __init__(self) -> None:
                self.results = [NoDecision(), object()]
                self.requests = []

            async def decide(self, request):
                self.requests.append(request)
                return self.results.pop(0)

        brain = SequenceBrain()
        config = ReactionChatConfig(
            initial_jitter_seconds=(0, 0),
            reaction_jitter_seconds=(0, 0),
            minimum_accepted_chat_interval_seconds=0,
            deadline_guard_seconds=0.01,
            minimum_start_budget_seconds=0.001,
            brain_timeout_seconds=0.1,
            outcome_retention=1,
        )
        reaction, _controller, world, source, sender, _brain = _make_stack(
            brain=brain, config=config  # type: ignore[arg-type]
        )
        reaction.start()
        try:
            await _wait_until(lambda: len(brain.requests) == 1)
            for seq in (2, 3):
                source.advance(seq)
                world._consume(  # noqa: SLF001
                    _event(
                        "chat.message",
                        seq,
                        {
                            "channel": "public",
                            "message": {
                                "player_id": "p1",
                                "display_name": "P1",
                                "message": str(seq),
                            },
                        },
                    )
                )
                await _wait_until(
                    lambda: reaction.snapshot().chat_brain_invocations
                    == min(seq, 2)
                )
            await asyncio.sleep(0.02)
            snapshot = reaction.snapshot()
            self.assertEqual(len(brain.requests), 2)
            self.assertEqual(sender.calls, [])
            self.assertEqual(snapshot.chat_brain_invocations, 2)
            self.assertEqual(len(snapshot.outcomes), 1)
            self.assertEqual(snapshot.outcomes[0].status, ReactionOutcomeStatus.INVALID)
        finally:
            await reaction.stop()

    async def test_unresolved_send_serializes_latest_reaction_and_ignores_unmatched_rejection(
        self,
    ) -> None:
        reaction, controller, world, source, _sender, brain = _make_stack()
        sender = _DeferredAcceptanceSender(world, source)
        controller.sender = sender  # type: ignore[assignment]
        reaction.start()
        try:
            await _wait_until(lambda: len(sender.calls) == 1)
            source.advance(2)
            world._consume(  # noqa: SLF001
                _event(
                    "chat.message",
                    2,
                    {
                        "channel": "public",
                        "message": {
                            "player_id": "p1",
                            "display_name": "P1",
                            "message": "pending reaction",
                        },
                    },
                )
            )
            source.advance(3)
            world._consume(  # noqa: SLF001
                _event(
                    "action.rejected",
                    3,
                    {"action": "co.report", "reason": "arbitrary_reason"},
                )
            )
            world._consume(  # noqa: SLF001
                ActionRejected("co.report", "arbitrary_reason", 3, 1, time.monotonic())
            )
            await asyncio.sleep(0.02)
            self.assertEqual(len(brain.requests), 1)
            self.assertEqual(reaction.snapshot().rejected_count, 1)

            source.advance(4)
            world._consume(  # noqa: SLF001
                _event(
                    "chat.message",
                    4,
                    {
                        "channel": "public",
                        "message": {
                            "player_id": "p0",
                            "display_name": "P0",
                            "message": "different-concurrent-message",
                        },
                    },
                )
            )
            await asyncio.sleep(0.02)
            self.assertEqual(len(brain.requests), 1)
            self.assertEqual(reaction.snapshot().accepted_count, 0)

            source.advance(5)
            world._consume(  # noqa: SLF001
                _event(
                    "chat.message",
                    5,
                    {
                        "channel": "public",
                        "message": {
                            "player_id": "p0",
                            "display_name": "P0",
                            "message": sender.calls[0][1],
                        },
                    },
                )
            )
            await _wait_until(lambda: len(brain.requests) == 2)
            await _wait_until(lambda: len(sender.calls) == 2)
            self.assertEqual(len(sender.calls), 2)
        finally:
            await reaction.stop()

    async def test_new_action_generation_rejection_does_not_reject_old_send(self) -> None:
        reaction, controller, world, source, _sender, _brain = _make_stack()
        sender = _DeferredAcceptanceSender(world, source)
        controller.sender = sender  # type: ignore[assignment]
        reaction.start()
        try:
            await _wait_until(lambda: len(sender.calls) == 1)
            new_handle = replace(
                source.snapshot().actions[0], action_generation=2
            )
            source._snapshot = replace(  # noqa: SLF001 - one drained update batch
                source._snapshot,
                last_seq=3,
                action_generation=2,
                actions=(new_handle,),
            )
            world._consume(  # noqa: SLF001
                _event(
                    "player.action_state",
                    2,
                    {
                        "phase": "day",
                        "day": 1,
                        "phase_ends_at": 120,
                        "actions": [{"type": "chat", "channel": "public"}],
                    },
                )
            )
            mapped_at = time.monotonic()
            world._consume(  # noqa: SLF001
                PhaseTimingMapped(
                    "day", 1, 2, 1, 2, 100, 120, mapped_at, mapped_at + 20
                )
            )
            world._consume(  # noqa: SLF001
                _event(
                    "action.rejected",
                    3,
                    {"action": "chat.send", "reason": "new_generation_rejection"},
                )
            )
            world._consume(  # noqa: SLF001
                ActionRejected(
                    "chat.send", "new_generation_rejection", 3, 1, time.monotonic()
                )
            )
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 1)
            outcome = reaction.snapshot().outcomes[0]
            self.assertEqual(outcome.trigger.phase_key.action_generation, 1)
            self.assertEqual(outcome.status, ReactionOutcomeStatus.TRANSPORT_GAP)
            self.assertNotEqual(outcome.status, ReactionOutcomeStatus.REJECTED)
        finally:
            await reaction.stop()

    async def test_replaced_mapping_ignores_old_deadline_notice_during_finalization(
        self,
    ) -> None:
        reaction, controller, world, source, _sender, _brain = _make_stack()
        sender = _DeferredAcceptanceSender(world, source)
        controller.sender = sender  # type: ignore[assignment]
        old_deadline = world.transport_observations().current_deadline
        assert old_deadline is not None
        reaction.start()
        try:
            await _wait_until(lambda: len(sender.calls) == 1)
            source.advance(2)
            world._consume(  # noqa: SLF001
                _event(
                    "game.event",
                    2,
                    {
                        "event_type": "DAY_EXTENDED",
                        "event_payload": {
                            "phase": "day",
                            "day": 1,
                            "phase_ends_at": 120,
                            "extensions_used": 1,
                        },
                    },
                )
            )
            mapped_at = time.monotonic()
            world._consume(  # noqa: SLF001
                PhaseTimingMapped(
                    "day", 1, 2, 1, 1, 100, 120, mapped_at, mapped_at + 20
                )
            )
            world._consume(  # noqa: SLF001
                PhaseDeadlineReached(
                    "day",
                    1,
                    1,
                    1,
                    old_deadline.local_deadline_monotonic,
                    time.monotonic(),
                )
            )
            await asyncio.sleep(0.02)
            self.assertEqual(reaction.snapshot().outcomes, ())
            self.assertEqual(len(sender.calls), 1)

            source.advance(3)
            world._consume(  # noqa: SLF001
                _event(
                    "chat.message",
                    3,
                    {
                        "channel": "public",
                        "message": {
                            "player_id": "p0",
                            "display_name": "P0",
                            "message": sender.calls[0][1],
                        },
                    },
                )
            )
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            self.assertEqual(
                reaction.snapshot().outcomes[0].status,
                ReactionOutcomeStatus.ACCEPTED,
            )
        finally:
            await reaction.stop()

    async def test_multiple_reactions_coalesce_to_latest_history_order(self) -> None:
        reaction, _controller, world, source, _sender, brain = _make_stack()
        reaction.start()
        try:
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            for seq, message in ((3, "first"), (4, "latest")):
                source.advance(seq)
                world._consume(  # noqa: SLF001
                    _event(
                        "chat.message",
                        seq,
                        {
                            "channel": "public",
                            "message": {
                                "player_id": "p1",
                                "display_name": "P1",
                                "message": message,
                            },
                        },
                    )
                )
            await _wait_until(lambda: reaction.snapshot().accepted_count == 2)
            outcomes = reaction.snapshot().outcomes
            self.assertEqual(len(brain.requests), 2)
            self.assertEqual(outcomes[-1].trigger.kind, ReactionTriggerKind.REACTION_CHAT)
            self.assertEqual(outcomes[-1].trigger.source_order, 3)
        finally:
            await reaction.stop()

    async def test_self_system_and_other_channel_do_not_trigger_reaction(self) -> None:
        brain = _Brain(silent=True)
        reaction, _controller, world, source, _sender, _brain = _make_stack(brain=brain)
        reaction.start()
        try:
            await _wait_until(lambda: len(brain.requests) == 1)
            for seq, channel, player_id in (
                (2, "public", "p0"),
                (3, "public", None),
                (4, "other", "p1"),
            ):
                source.advance(seq)
                world._consume(  # noqa: SLF001
                    _event(
                        "chat.message",
                        seq,
                        {
                            "channel": channel,
                            "message": {
                                "player_id": player_id,
                                "display_name": None,
                                "message": str(seq),
                            },
                        },
                    )
                )
            await asyncio.sleep(0.05)
            self.assertEqual(len(brain.requests), 1)
            self.assertEqual(reaction.snapshot().intentional_silence_count, 1)
        finally:
            await reaction.stop()

    async def test_co_is_filtered_and_prioritized_before_initial_chat(self) -> None:
        reaction, _controller, _world, _source, _sender, brain = _make_stack(with_co=True)
        reaction.start()
        try:
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            self.assertGreaterEqual(len(brain.requests), 2)
            self.assertTrue(
                all(
                    isinstance(option.handle, CoDeclareAction)
                    for option in brain.requests[0].action_context.options
                )
            )
            self.assertTrue(
                all(
                    isinstance(option.handle, ChatAction)
                    for option in brain.requests[1].action_context.options
                )
            )
            self.assertEqual(
                reaction.snapshot().co_generation_state,
                CoGenerationState(action_generation=1, invoked=True, closed=True),
            )
        finally:
            await reaction.stop()

    async def test_co_acceptance_matches_action_kind_and_same_generation_is_not_retried(
        self,
    ) -> None:
        class CoBrain:
            def __init__(self) -> None:
                self.requests = []

            async def decide(self, request):
                self.requests.append(request)
                option = request.action_context.options[0]
                if isinstance(option.handle, CoDeclareAction):
                    return CoDeclareDecision(option.option_id, "villager", "claim")
                return NoDecision()

        brain = CoBrain()
        reaction, controller, world, source, _sender, _brain = _make_stack(
            brain=brain, with_co=True  # type: ignore[arg-type]
        )
        sender = _CoSender(world, source)
        controller.sender = sender  # type: ignore[assignment]
        reaction.start()
        try:
            await _wait_until(
                lambda: any(
                    outcome.action_kind == "co.declare"
                    and outcome.status is ReactionOutcomeStatus.ACCEPTED
                    for outcome in reaction.snapshot().outcomes
                )
            )
            await _wait_until(lambda: reaction.snapshot().intentional_silence_count == 1)
            co_calls = sum(
                isinstance(request.action_context.options[0].handle, CoDeclareAction)
                for request in brain.requests
            )
            self.assertEqual(co_calls, 1)

            chat_handle = next(
                handle
                for handle in source.snapshot().actions
                if isinstance(handle, ChatAction)
            )
            co_handle = next(
                handle
                for handle in source.snapshot().actions
                if isinstance(handle, CoDeclareAction)
            )
            source._snapshot = replace(source._snapshot, actions=(chat_handle,))  # noqa: SLF001
            world._commit()  # noqa: SLF001
            await asyncio.sleep(0)
            source._snapshot = replace(  # noqa: SLF001
                source._snapshot, actions=(chat_handle, co_handle)
            )
            world._commit()  # noqa: SLF001
            await asyncio.sleep(0.02)
            co_calls = sum(
                isinstance(request.action_context.options[0].handle, CoDeclareAction)
                for request in brain.requests
            )
            self.assertEqual(co_calls, 1)
            self.assertEqual(
                reaction.snapshot().co_generation_state,
                CoGenerationState(action_generation=1, invoked=True, closed=True),
            )
        finally:
            await reaction.stop()

    async def test_matching_rejection_is_observable_and_not_retried(self) -> None:
        reaction, _controller, world, _source, sender, brain = _make_stack(reject=True)
        reaction.start()
        try:
            await _wait_until(
                lambda: any(
                    outcome.status is ReactionOutcomeStatus.REJECTED
                    for outcome in reaction.snapshot().outcomes
                )
            )
            await asyncio.sleep(0.02)
            snapshot = reaction.snapshot()
            self.assertEqual(snapshot.rejected_count, 1)
            self.assertEqual(len(sender.calls), 1)
            self.assertEqual(len(brain.requests), 1)
            observations = world.transport_observations().observations
            self.assertTrue(any(isinstance(item, ActionRejectionObservation) for item in observations))
        finally:
            await reaction.stop()

    async def test_timeout_consumes_chat_cap_without_fallback_or_send(self) -> None:
        class NeverBrain:
            def __init__(self) -> None:
                self.calls = 0
                self.entered: asyncio.Queue[int] = asyncio.Queue()

            async def decide(self, request):
                self.calls += 1
                self.entered.put_nowait(self.calls)
                await asyncio.Future()

        brain = NeverBrain()
        clock = _FakeClock(10.0)
        config = ReactionChatConfig(
            initial_jitter_seconds=(0, 0),
            reaction_jitter_seconds=(0, 0),
            minimum_accepted_chat_interval_seconds=0,
            deadline_guard_seconds=0.01,
            minimum_start_budget_seconds=0.001,
            brain_timeout_seconds=0.2,
        )
        reaction, _controller, world, source, sender, _brain = _make_stack(
            brain=brain, config=config, clock=clock  # type: ignore[arg-type]
        )
        reaction.start()
        try:
            self.assertEqual(await asyncio.wait_for(brain.entered.get(), 1), 1)
            source.advance(2)
            world._consume(  # noqa: SLF001
                _event(
                    "chat.message",
                    2,
                    {
                        "channel": "public",
                        "message": {
                            "player_id": "p1",
                            "display_name": "P1",
                            "message": "reaction",
                        },
                    },
                )
            )
            self.assertEqual(await asyncio.wait_for(brain.entered.get(), 1), 2)
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 2)
            self.assertEqual(sender.calls, [])
            self.assertEqual(
                [outcome.status for outcome in reaction.snapshot().outcomes],
                [ReactionOutcomeStatus.TIMED_OUT, ReactionOutcomeStatus.TIMED_OUT],
            )
        finally:
            await reaction.stop()

    async def test_transport_gap_closes_current_send_paths_but_remains_observable(self) -> None:
        reaction, _controller, world, source, sender, brain = _make_stack(
            transport_retention=TransportObservationRetention(max_records=1, max_bytes=4096)
        )
        source.advance(2)
        world._consume(_event("action.rejected", 2, {"action": "vote.cast", "reason": "x"}))  # noqa: SLF001
        world._consume(ActionRejected("vote.cast", "x", 2, 1, time.monotonic()))  # noqa: SLF001
        mapped_at = time.monotonic()
        world._consume(  # noqa: SLF001
            PhaseTimingMapped("day", 1, 2, 1, 1, 100, 110, mapped_at, mapped_at + 10)
        )
        reaction.start()
        try:
            await _wait_until(
                lambda: any(
                    outcome.status is ReactionOutcomeStatus.TRANSPORT_GAP
                    for outcome in reaction.snapshot().outcomes
                )
            )
            await asyncio.sleep(0.02)
            self.assertEqual(brain.requests, [])
            self.assertEqual(sender.calls, [])
        finally:
            await reaction.stop()

    async def test_history_gap_suppresses_reactions_but_allows_initial_opportunity(self) -> None:
        reaction, _controller, world, source, _sender, brain = _make_stack(
            world_config=WorldStateConfig(max_history_records=1, max_history_bytes=4096)
        )
        for seq in (2, 3):
            source.advance(seq)
            world._consume(  # noqa: SLF001
                _event(
                    "chat.message",
                    seq,
                    {
                        "channel": "public",
                        "message": {
                            "player_id": "p1",
                            "display_name": "P1",
                            "message": str(seq),
                        },
                    },
                )
            )
        reaction.start()
        try:
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            self.assertEqual(len(brain.requests), 1)
            self.assertTrue(
                any(
                    outcome.status is ReactionOutcomeStatus.HISTORY_GAP
                    for outcome in reaction.snapshot().outcomes
                )
            )
        finally:
            await reaction.stop()

    async def test_deadline_extension_creates_one_remaining_chat_opportunity(self) -> None:
        brain = _Brain(silent=True)
        reaction, _controller, world, source, _sender, _brain = _make_stack(brain=brain)
        reaction.start()
        try:
            # 延長前の機会が完了した後で、残りの機会を検証する。
            await _wait_until(
                lambda: reaction.snapshot().intentional_silence_count == 1
            )
            self.assertEqual(len(brain.requests), 1)
            source.advance(2)
            world._consume(  # noqa: SLF001
                _event(
                    "game.event",
                    2,
                    {
                        "event_type": "DAY_EXTENDED",
                        "event_payload": {
                            "phase": "day",
                            "day": 1,
                            "phase_ends_at": 120,
                            "extensions_used": 1,
                        },
                    },
                )
            )
            mapped_at = time.monotonic()
            world._consume(  # noqa: SLF001
                PhaseTimingMapped("day", 1, 2, 1, 1, 100, 120, mapped_at, mapped_at + 20)
            )
            await _wait_until(lambda: len(brain.requests) == 2)
            await _wait_until(
                lambda: reaction.snapshot().intentional_silence_count == 2
            )
        finally:
            await reaction.stop()

    async def test_pending_extension_discards_old_due_and_rebases_without_duplicate(
        self,
    ) -> None:
        clock = _FakeClock(10.0)
        config = ReactionChatConfig(
            max_chat_attempts_per_phase=1,
            initial_jitter_seconds=(0.08, 0.08),
            reaction_jitter_seconds=(0.08, 0.08),
            minimum_accepted_chat_interval_seconds=0,
            deadline_guard_seconds=0.01,
            minimum_start_budget_seconds=0.001,
            brain_timeout_seconds=0.1,
        )
        reaction, _controller, world, source, sender, _brain = _make_stack(
            clock=clock, config=config
        )
        reaction.start()
        try:
            await _yield_until(
                lambda: reaction._pending_initial is not None  # noqa: SLF001
            )
            old_due = reaction._pending_initial.due  # type: ignore[union-attr]  # noqa: SLF001
            clock.advance_to(10.06)
            source.advance(2)
            world._consume(  # noqa: SLF001
                _event(
                    "game.event",
                    2,
                    {
                        "event_type": "DAY_EXTENDED",
                        "event_payload": {
                            "phase": "day",
                            "day": 1,
                            "phase_ends_at": 120,
                            "extensions_used": 1,
                        },
                    },
                )
            )
            world._consume(  # noqa: SLF001
                PhaseTimingMapped("day", 1, 2, 1, 1, 100, 120, 10.06, 30.06)
            )
            await _yield_until(
                lambda: reaction._pending_initial is not None  # noqa: SLF001
                and reaction._pending_initial.mapping_order == 2  # type: ignore[union-attr]  # noqa: SLF001
            )
            new_due = reaction._pending_initial.due  # type: ignore[union-attr]  # noqa: SLF001
            self.assertAlmostEqual(old_due, 10.08)
            self.assertAlmostEqual(new_due, 10.14)

            clock.advance_to(old_due)
            world._commit()  # noqa: SLF001
            for _ in range(20):
                await asyncio.sleep(0)
            self.assertEqual(sender.calls, [])

            clock.advance_to(new_due)
            world._commit()  # noqa: SLF001
            await _yield_until(lambda: reaction.snapshot().accepted_count == 1)
            self.assertEqual(len(sender.calls), 1)
            self.assertEqual(reaction.snapshot().chat_brain_invocations, 1)
            self.assertEqual(len(reaction.snapshot().outcomes), 1)
        finally:
            await reaction.stop()

    async def test_pending_shortening_rebases_and_rechecks_cutoff(self) -> None:
        clock = _FakeClock(10.0)
        config = ReactionChatConfig(
            max_chat_attempts_per_phase=1,
            initial_jitter_seconds=(0.08, 0.08),
            reaction_jitter_seconds=(0.08, 0.08),
            minimum_accepted_chat_interval_seconds=0,
            deadline_guard_seconds=0.01,
            minimum_start_budget_seconds=0.001,
            brain_timeout_seconds=0.1,
        )
        reaction, _controller, world, source, sender, _brain = _make_stack(
            clock=clock, config=config
        )
        reaction.start()
        try:
            await _yield_until(
                lambda: reaction._pending_initial is not None  # noqa: SLF001
            )
            old_due = reaction._pending_initial.due  # type: ignore[union-attr]  # noqa: SLF001
            clock.advance_to(10.06)
            source.advance(2)
            world._consume(  # noqa: SLF001
                _event(
                    "game.event",
                    2,
                    {
                        "event_type": "DAY_SHORTENED",
                        "event_payload": {
                            "phase": "day",
                            "day": 1,
                            "phase_ends_at": 100,
                            "extensions_used": 1,
                        },
                    },
                )
            )
            world._consume(  # noqa: SLF001
                PhaseTimingMapped("day", 1, 2, 1, 1, 100, 100, 10.06, 10.06)
            )
            await _yield_until(
                lambda: reaction._pending_initial is not None  # noqa: SLF001
                and reaction._pending_initial.mapping_order == 2  # type: ignore[union-attr]  # noqa: SLF001
            )
            new_due = reaction._pending_initial.due  # type: ignore[union-attr]  # noqa: SLF001
            self.assertAlmostEqual(new_due, 10.14)

            clock.advance_to(old_due)
            world._commit()  # noqa: SLF001
            for _ in range(20):
                await asyncio.sleep(0)
            self.assertEqual(sender.calls, [])

            clock.advance_to(new_due)
            world._commit()  # noqa: SLF001
            await _yield_until(
                lambda: reaction.snapshot().deadline_suppressed_count == 1
            )
            self.assertEqual(sender.calls, [])
            self.assertEqual(reaction.snapshot().chat_brain_invocations, 0)
            self.assertEqual(
                reaction.snapshot().outcomes[-1].status,
                ReactionOutcomeStatus.DEADLINE_SUPPRESSED,
            )
        finally:
            await reaction.stop()

    async def test_mapping_replacement_during_brain_work_suppresses_stale_send(self) -> None:
        brain = _BlockingBrain()
        reaction, _controller, world, source, sender, _brain = _make_stack(brain=brain)
        reaction.start()
        try:
            await asyncio.wait_for(brain.started.wait(), 0.2)
            source.advance(2)
            world._consume(  # noqa: SLF001
                _event(
                    "game.event",
                    2,
                    {
                        "event_type": "DAY_EXTENDED",
                        "event_payload": {
                            "phase": "day",
                            "day": 1,
                            "phase_ends_at": 120,
                            "extensions_used": 1,
                        },
                    },
                )
            )
            mapped_at = time.monotonic()
            world._consume(  # noqa: SLF001
                PhaseTimingMapped("day", 1, 2, 1, 1, 100, 120, mapped_at, mapped_at + 20)
            )
            brain.release.set()
            await _wait_until(lambda: len(reaction.snapshot().outcomes) >= 2)
            statuses = [outcome.status for outcome in reaction.snapshot().outcomes]
            self.assertEqual(
                statuses,
                [
                    ReactionOutcomeStatus.DEADLINE_SUPPRESSED,
                    ReactionOutcomeStatus.ACCEPTED,
                ],
            )
            self.assertEqual(len(sender.calls), 1)
        finally:
            await reaction.stop()

    async def test_world_failure_before_phase_is_propagated_by_stop(self) -> None:
        reaction, _controller, _world, _source, _sender, _brain = _make_stack()

        class FailingWorld:
            def snapshot(self):
                raise RuntimeError("pre-phase world failure")

        reaction.world = FailingWorld()  # type: ignore[assignment]
        reaction.start()
        exit_result = await reaction.wait()
        self.assertEqual(exit_result.reason, FeatureControllerExitReason.FAILED)
        self.assertEqual(exit_result.error_type, "RuntimeError")
        self.assertIs(await reaction.wait(), exit_result)
        await reaction.stop()
        self.assertEqual(reaction.snapshot().lifecycle, ReactionChatLifecycle.FAILED)
        self.assertEqual(reaction.snapshot().outcomes, ())

    async def test_world_failure_during_unresolved_send_is_propagated_by_stop(
        self,
    ) -> None:
        reaction, controller, world, source, _sender, _brain = _make_stack()
        sender = _DeferredAcceptanceSender(world, source)
        controller.sender = sender  # type: ignore[assignment]
        reaction.start()
        await _wait_until(lambda: len(sender.calls) == 1)

        def failing_history(*_args, **_kwargs):
            raise RuntimeError("active world failure")

        world.history = failing_history  # type: ignore[method-assign]
        world._commit()  # noqa: SLF001 - wake unresolved finalization
        exit_result = await reaction.wait()
        self.assertEqual(exit_result.reason, FeatureControllerExitReason.FAILED)
        self.assertEqual(exit_result.error_type, "RuntimeError")
        await reaction.stop()
        self.assertEqual(reaction.snapshot().lifecycle, ReactionChatLifecycle.FAILED)
        self.assertFalse(
            any(
                outcome.status is ReactionOutcomeStatus.BRAIN_FAILED
                for outcome in reaction.snapshot().outcomes
            )
        )

    async def test_public_wait_is_non_cancelling_and_stop_is_idempotent(self) -> None:
        reaction, controller, _world, _source, _sender, _brain = _make_stack()
        with self.assertRaises(RuntimeError):
            await reaction.wait()
        reaction.start()
        with self.assertRaises(RuntimeError):
            reaction.start()
        waiter = asyncio.create_task(reaction.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        await reaction.stop()
        await reaction.stop()
        exit_result = await reaction.wait()
        self.assertEqual(exit_result.reason, FeatureControllerExitReason.STOP_REQUESTED)
        self.assertIs(await reaction.wait(), exit_result)
        self.assertEqual(reaction.snapshot().lifecycle, ReactionChatLifecycle.STOPPED)
        self.assertFalse(controller._stopping)  # noqa: SLF001 - ownership contract
        await reaction.invoker.stop()

        stopped_before_start, *_rest = _make_stack()
        await stopped_before_start.stop()
        before_start_exit = await stopped_before_start.wait()
        self.assertEqual(
            before_start_exit.reason, FeatureControllerExitReason.STOP_REQUESTED
        )
        await stopped_before_start.invoker.stop()

    async def test_public_wait_distinguishes_world_ended_and_world_failed(self) -> None:
        for freshness, expected_reason, expected_lifecycle in (
            (
                Freshness.ENDED,
                FeatureControllerExitReason.WORLD_ENDED,
                ReactionChatLifecycle.STOPPED,
            ),
            (
                Freshness.FAILED,
                FeatureControllerExitReason.WORLD_FAILED,
                ReactionChatLifecycle.FAILED,
            ),
        ):
            with self.subTest(freshness=freshness):
                reaction, _controller, world, _source, _sender, _brain = _make_stack()
                world._set_freshness(freshness)  # noqa: SLF001
                reaction.start()
                exit_result = await reaction.wait()
                self.assertEqual(exit_result.reason, expected_reason)
                self.assertIsNone(exit_result.error_type)
                self.assertEqual(reaction.snapshot().lifecycle, expected_lifecycle)
                await reaction.stop()


class WorldTransportTests(unittest.TestCase):
    def test_world_commits_immutable_transport_records_and_filters(self) -> None:
        common = dict(
            connection_generation=1,
            action_generation=1,
            phase="day",
            day=1,
        )
        action = ChatAction(**common, type="chat", channel="public")
        source = _Source((action,))
        world = WorldState(source)
        world._consume(_event("game.state_sync", 1, _sync_payload()))  # noqa: SLF001
        world._consume(  # noqa: SLF001
            PhaseTimingMapped(
                phase="day",
                day=1,
                source_seq=1,
                connection_generation=1,
                action_generation=1,
                server_timestamp=100,
                phase_ends_at=110,
                mapped_at_monotonic=5.0,
                local_deadline_monotonic=15.0,
            )
        )
        source.advance(2)
        world._consume(_event("action.rejected", 2, {"action": "chat.send", "reason": "late"}))  # noqa: SLF001
        world._consume(ActionRejected("chat.send", "late", 2, 1, 6.0))  # noqa: SLF001
        view = world.transport_observations(
            TransportObservationQuery(
                after_order=0,
                kinds=frozenset({TransportObservationKind.ACTION_REJECTION}),
            )
        )
        self.assertEqual(len(view.observations), 1)
        self.assertIsInstance(view.observations[0], ActionRejectionObservation)
        self.assertEqual(view.observations[0].world_version, world.snapshot().version)
        self.assertEqual(
            view.current_deadline,
            CurrentPhaseDeadline(1, "day", 1, 1, 1, 15.0),
        )
        with self.assertRaises(FrozenInstanceError):
            view.observations[0].reason = "changed"  # type: ignore[misc]

    def test_deadline_reached_is_journaled_and_lifecycle_clears_current(self) -> None:
        common = dict(
            connection_generation=1,
            action_generation=1,
            phase="day",
            day=1,
        )
        source = _Source((ChatAction(**common, type="chat", channel="public"),))
        world = WorldState(source)
        world._consume(_event("game.state_sync", 1, _sync_payload()))  # noqa: SLF001
        world._consume(PhaseTimingMapped("day", 1, 1, 1, 1, 100, 110, 5.0, 15.0))  # noqa: SLF001
        world._consume(PhaseDeadlineReached("day", 1, 1, 1, 15.0, 15.1))  # noqa: SLF001
        self.assertEqual(len(world.transport_observations().observations), 2)
        from ai_client.network import LifecycleChanged

        world._consume(  # noqa: SLF001
            LifecycleChanged(ClientLifecycle.CONNECTED, ClientLifecycle.RECONNECT_WAIT)
        )
        self.assertIsNone(world.transport_observations().current_deadline)
        self.assertEqual(len(world.transport_observations().observations), 2)

    def test_transport_retention_keeps_oversized_newest_and_reports_gap(self) -> None:
        common = dict(
            connection_generation=1,
            action_generation=1,
            phase="day",
            day=1,
        )
        source = _Source((ChatAction(**common, type="chat", channel="public"),))
        world = WorldState(
            source,
            transport_retention=TransportObservationRetention(max_records=1, max_bytes=1),
        )
        world._consume(_event("game.state_sync", 1, _sync_payload()))  # noqa: SLF001
        world._consume(PhaseTimingMapped("day", 1, 1, 1, 1, 100, 110, 5.0, 15.0))  # noqa: SLF001
        source.advance(2)
        world._consume(_event("action.rejected", 2, {"action": "chat.send", "reason": "x"}))  # noqa: SLF001
        world._consume(ActionRejected("chat.send", "x", 2, 1, 6.0))  # noqa: SLF001
        view = world.transport_observations(TransportObservationQuery(after_order=0))
        self.assertTrue(view.gap_before_first)
        self.assertEqual(len(view.observations), 1)
        self.assertIsInstance(view.observations[0], ActionRejectionObservation)
        self.assertEqual(view.first_retained_order, 2)
        self.assertEqual(view.last_order, 2)

    def test_sync_clears_deadline_but_retains_journal_and_stale_mapping_is_not_current(self) -> None:
        common = dict(
            connection_generation=1,
            action_generation=1,
            phase="day",
            day=1,
        )
        action = ChatAction(**common, type="chat", channel="public")
        source = _Source((action,))
        world = WorldState(source)
        world._consume(_event("game.state_sync", 1, _sync_payload()))  # noqa: SLF001
        world._consume(PhaseTimingMapped("day", 1, 1, 1, 1, 100, 110, 5.0, 15.0))  # noqa: SLF001
        self.assertIsNotNone(world.transport_observations().current_deadline)

        source.advance(2)
        world._consume(_event("game.state_sync", 2, _sync_payload()))  # noqa: SLF001
        self.assertIsNone(world.transport_observations().current_deadline)
        self.assertEqual(len(world.transport_observations().observations), 1)
        world._consume(PhaseTimingMapped("day", 1, 1, 0, 1, 100, 110, 6.0, 16.0))  # noqa: SLF001
        self.assertIsNone(world.transport_observations().current_deadline)
        self.assertEqual(len(world.transport_observations().observations), 2)


if __name__ == "__main__":
    unittest.main()
