from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
import unittest

from ai_client.brain import (
    AbilityDecision,
    BrainActionContext,
    BrainActionOption,
    BrainController,
    BrainInput,
    BrainInvocationArbiter,
    BrainRunConfig,
    DispatchDeadline,
    FeatureControllerExitReason,
    NoDecision,
    VoteDecision,
)
from ai_client.network import (
    AbilityAction,
    ActionAccepted,
    ActionRejected,
    ChatAction,
    ClientLifecycle,
    ClientSnapshot,
    DeliveryUnknownError,
    LifecycleChanged,
    NotDeliveredError,
    PhaseTimingMapped,
    ResumeRecoveryCompleted,
    SendReceipt,
    ServerEvent,
    VoteAction,
)
from ai_client.network.types import immutable_mapping
from ai_client.vote_ability import (
    DeterministicVoteAbilityBrain,
    OpportunityKey,
    ReservationKey,
    VoteAbilityConfig,
    VoteAbilityController,
    VoteAbilityLifecycle,
    VoteAbilityOutcomeStatus,
)
from ai_client.world import (
    AbilityResultView,
    CoView,
    Freshness,
    HistoryView,
    PhaseView,
    SelfView,
    WorldSnapshot,
    WorldState,
)


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


def _sync_payload(*, phase: str, day: int, ends_at: int = 110) -> dict:
    return {
        "players": [
            {"player_id": "p0", "display_name": "P0"},
            {"player_id": "p1", "display_name": "P1"},
            {"player_id": "p2", "display_name": "P2"},
            {"player_id": "p3", "display_name": "P3"},
        ],
        "deaths": [],
        "action_state": {
            "phase": phase,
            "day": day,
            "phase_ends_at": ends_at,
            "actions": [],
        },
        "self": {"player_id": "p0", "role_id": "opaque", "modifier_ids": []},
        "revealed_roles": [],
        "history": [],
    }


def _brain_input(handles, *, seed_phase: str = "vote", day: int = 1) -> BrainInput:
    phase = PhaseView(seed_phase, day, 110)
    snapshot = WorldSnapshot(
        version=1,
        freshness=Freshness.CURRENT,
        is_caught_up=True,
        last_applied_seq=1,
        players=(),
        phase=phase,
        self_view=SelfView("p0", "opaque"),
    )
    retention = snapshot.history_retention
    options = tuple(
        BrainActionOption(f"action:{index}", handle)
        for index, handle in enumerate(handles)
    )
    return BrainInput(
        snapshot,
        BrainActionContext(1, 1, 1, True, options),
        HistoryView((), True, retention),
        CoView((), (), True, retention),
        AbilityResultView((), True, retention),
    )


class _Delegate:
    def __init__(self) -> None:
        self.calls = []

    async def decide(self, request):
        self.calls.append(request)
        return NoDecision()


class _FirstCallBlockingVoteBrain:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def decide(self, request):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
        option = request.action_context.options[0]
        return VoteDecision(option.option_id, option.handle.valid_targets[0])


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

    def snapshot(self):
        return self._snapshot

    def update(self, **changes) -> None:
        self._snapshot = replace(self._snapshot, **changes)

    async def events(self):
        await asyncio.Future()
        yield  # pragma: no cover


class _Sender:
    def __init__(self, world: WorldState, source: _Source, *, auto="accepted") -> None:
        self.world = world
        self.source = source
        self.auto = auto
        self.calls = []
        self.ordinal = 0

    async def _sent(self, action: str) -> SendReceipt:
        self.ordinal += 1
        event_id = f"10000000-0000-4000-8000-{self.ordinal:012d}"
        generation = self.source.snapshot().connection_generation
        if self.auto == "not_delivered":
            raise NotDeliveredError("before wire")
        if self.auto == "delivery_unknown":
            raise DeliveryUnknownError(
                "after wire",
                request_event_id=event_id,
                action=action,
                connection_generation=generation,
            )
        if self.auto in {"accepted", "rejected"}:
            seq = self.source.snapshot().last_seq + 1
            self.source.update(last_seq=seq)
            message_type = f"action.{self.auto}"
            payload = {"action": action, "request_event_id": event_id}
            if self.auto == "rejected":
                payload["reason"] = "invalid_target"
            self.world._consume(_server_event(message_type, seq, payload))  # noqa: SLF001
            if self.auto == "accepted":
                self.world._consume(  # noqa: SLF001
                    ActionAccepted(action, event_id, seq, generation, 2.0)
                )
            else:
                self.world._consume(  # noqa: SLF001
                    ActionRejected(
                        action,
                        "invalid_target",
                        seq,
                        generation,
                        2.0,
                        event_id,
                    )
                )
        return SendReceipt(event_id, generation)

    async def send_vote(self, handle, target):
        self.calls.append(("vote.cast", handle, target))
        return await self._sent("vote.cast")

    async def send_ability(self, handle, targets):
        self.calls.append(("ability.use", handle, tuple(targets)))
        return await self._sent("ability.use")


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout)


class DeterministicSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_vote_fixed_vector_is_order_independent_and_never_prefers_abstain(self) -> None:
        first = VoteAction(1, 1, "vote", 1, "vote", ("p3", "p1", "p2"), 1, True)
        reordered = replace(first, valid_targets=("p2", "p3", "p1"))
        brain = DeterministicVoteAbilityBrain(master_seed=73, delegate=_Delegate())
        decision = await brain.decide(_brain_input((first,)))
        repeated = await brain.decide(_brain_input((reordered,)))
        self.assertEqual(decision, VoteDecision("action:0", "p3"))
        self.assertEqual(repeated, decision)

    async def test_vote_abstains_only_when_server_lists_no_target_and_allows_it(self) -> None:
        delegate = _Delegate()
        brain = DeterministicVoteAbilityBrain(master_seed=1, delegate=delegate)
        abstain = VoteAction(1, 1, "runoff", 2, "vote", (), 1, True)
        closed = replace(abstain, allows_abstain=False)
        malformed = replace(abstain, valid_targets=("p1",), target_count=2)
        self.assertEqual(
            await brain.decide(_brain_input((abstain,), seed_phase="runoff", day=2)),
            VoteDecision("action:0", None),
        )
        self.assertIsInstance(
            await brain.decide(_brain_input((closed,), seed_phase="runoff", day=2)),
            NoDecision,
        )
        self.assertIsInstance(
            await brain.decide(_brain_input((malformed,), seed_phase="runoff", day=2)),
            NoDecision,
        )
        self.assertEqual(delegate.calls, [])

    async def test_ability_fixed_vector_filters_uses_and_selects_exact_unique_targets(self) -> None:
        unavailable = AbilityAction(
            2, 4, "night", 2, "ability", "a-zero", None, ("p1",), 1, 0
        )
        first = AbilityAction(
            2, 4, "night", 2, "ability", "a-one", None, ("p3", "p2", "p1"), 2, None
        )
        second = AbilityAction(
            2, 4, "night", 2, "ability", "a-two", None, ("p2", "p3"), 1, 3
        )
        brain = DeterministicVoteAbilityBrain(master_seed=73, delegate=_Delegate())
        decision = await brain.decide(
            _brain_input((unavailable, first, second), seed_phase="night", day=2)
        )
        self.assertEqual(decision, AbilityDecision("action:2", ("p3",)))
        reordered = await brain.decide(
            _brain_input((second, unavailable, first), seed_phase="night", day=2)
        )
        self.assertEqual(reordered, AbilityDecision("action:0", ("p3",)))

    async def test_duplicate_ability_identity_or_mixed_family_never_dispatches_or_delegates(self) -> None:
        delegate = _Delegate()
        brain = DeterministicVoteAbilityBrain(master_seed=4, delegate=delegate)
        ability = AbilityAction(
            1, 1, "night0", 0, "ability", "opaque", None, ("p1",), 1, None
        )
        duplicate = replace(ability, action_generation=2)
        vote = VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)
        self.assertIsInstance(
            await brain.decide(_brain_input((ability, duplicate), seed_phase="night0", day=0)),
            NoDecision,
        )
        self.assertIsInstance(
            await brain.decide(_brain_input((ability, vote))), NoDecision
        )
        self.assertEqual(delegate.calls, [])

    async def test_non_reservation_input_delegates_exactly_once(self) -> None:
        delegate = _Delegate()
        brain = DeterministicVoteAbilityBrain(master_seed=-9, delegate=delegate)
        chat = ChatAction(1, 1, "day", 1, "chat", "public")
        request = _brain_input((chat,), seed_phase="day")
        result = await brain.decide(request)
        self.assertIsInstance(result, NoDecision)
        self.assertEqual(delegate.calls, [request])

    def test_identity_and_config_are_frozen_and_validate(self) -> None:
        reservation = ReservationKey(0, "night0", "ability")
        opportunity = OpportunityKey(reservation, 1, 2)
        self.assertEqual(opportunity.reservation, reservation)
        with self.assertRaises(FrozenInstanceError):
            opportunity.action_generation = 3  # type: ignore[misc]
        with self.assertRaises(ValueError):
            ReservationKey(-1, "night0", "ability")
        with self.assertRaises(ValueError):
            VoteAbilityConfig(brain_timeout_seconds=0)

    def test_dispatch_deadline_accepts_night_zero_but_rejects_negative_day(self) -> None:
        deadline = DispatchDeadline(1, "night0", 0, 1, 1, 2.0)
        self.assertEqual(deadline.day, 0)
        with self.assertRaises(ValueError):
            DispatchDeadline(1, "night0", -1, 1, 1, 2.0)


class VoteAbilityControllerTests(unittest.IsolatedAsyncioTestCase):
    def _stack(self, handle, *, auto="accepted", now=1.0):
        source = _Source((handle,))
        world = WorldState(source)
        world._consume(  # noqa: SLF001
            _server_event(
                "game.state_sync",
                1,
                _sync_payload(phase=handle.phase, day=handle.day),
            )
        )
        world._consume(  # noqa: SLF001
            PhaseTimingMapped(
                handle.phase,
                handle.day,
                1,
                handle.connection_generation,
                handle.action_generation,
                100,
                110,
                now,
                now + 10,
            )
        )
        delegate = _Delegate()
        brain = DeterministicVoteAbilityBrain(master_seed=73, delegate=delegate)
        sender = _Sender(world, source, auto=auto)
        brain_controller = BrainController(
            world=world,
            sender=sender,
            brain=brain,
            config=BrainRunConfig(max_decision_seconds=0.25),
            clock=lambda: now,
        )
        arbiter = BrainInvocationArbiter(controller=brain_controller, clock=lambda: now)
        controller = VoteAbilityController(
            world=world,
            invoker=arbiter,
            clock=lambda: now,
        )
        return source, world, sender, arbiter, controller

    async def test_vote_send_is_one_final_reservation_and_send_receipt_is_not_acceptance(self) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1", "p2"), 1, False)
        source, world, sender, arbiter, controller = self._stack(handle, auto=None)
        controller.start()
        await _wait_until(lambda: controller.snapshot().unresolved_reservation is not None)
        self.assertEqual(controller.snapshot().accepted_count, 0)
        unresolved = controller.snapshot().unresolved_reservation
        assert unresolved is not None
        self.assertEqual(len(sender.calls), 1)
        seq = source.snapshot().last_seq + 1
        source.update(last_seq=seq)
        world._consume(  # noqa: SLF001
            ActionAccepted(
                "ability.use",
                unresolved.request_event_id,
                seq,
                unresolved.send_connection_generation,
                2.0,
            )
        )
        await asyncio.sleep(0)
        self.assertEqual(controller.snapshot().accepted_count, 0)
        world._consume(  # noqa: SLF001
            ActionAccepted(
                "vote.cast",
                unresolved.request_event_id,
                seq,
                unresolved.send_connection_generation + 1,
                2.1,
            )
        )
        await _wait_until(lambda: controller.snapshot().accepted_count == 1)
        world._consume(  # noqa: SLF001
            PhaseTimingMapped("vote", 1, seq, 1, 1, 100, 120, 2.2, 20.0)
        )
        await asyncio.sleep(0)
        self.assertEqual(len(sender.calls), 1)
        self.assertEqual(controller.snapshot().outcomes[-1].status, VoteAbilityOutcomeStatus.ACCEPTED)
        await controller.stop()
        await arbiter.stop()

    async def test_night_zero_ability_dispatches_received_handle_only(self) -> None:
        handle = AbilityAction(
            1, 1, "night0", 0, "ability", "opaque-action", None, ("p1", "p2"), 1, None
        )
        _source, _world, sender, arbiter, controller = self._stack(handle)
        controller.start()
        await _wait_until(lambda: controller.snapshot().accepted_count == 1)
        self.assertEqual(len(sender.calls), 1)
        action, sent_handle, targets = sender.calls[0]
        self.assertEqual(action, "ability.use")
        self.assertIs(sent_handle, handle)
        self.assertEqual(len(targets), 1)
        self.assertIn(targets[0], handle.valid_targets)
        self.assertEqual(controller.snapshot().outcomes[-1].ability_id, "opaque-action")
        await controller.stop()
        await arbiter.stop()

    async def test_exact_rejection_correlation_is_terminal_without_retry(self) -> None:
        handle = VoteAction(1, 1, "runoff", 2, "vote", ("p1",), 1, False)
        _source, world, sender, arbiter, controller = self._stack(handle, auto="rejected")
        controller.start()
        await _wait_until(lambda: controller.snapshot().rejected_count == 1)
        self.assertEqual(len(sender.calls), 1)
        self.assertEqual(controller.snapshot().outcomes[-1].rejection_reason, "invalid_target")
        world._consume(  # noqa: SLF001
            PhaseTimingMapped("runoff", 2, 2, 1, 1, 100, 120, 2.2, 20.0)
        )
        await asyncio.sleep(0)
        self.assertEqual(len(sender.calls), 1)
        await controller.stop()
        await arbiter.stop()

    async def test_recovery_barrier_not_generation_change_finalizes_unknown(self) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)
        source, world, _sender, arbiter, controller = self._stack(handle, auto=None)
        controller.start()
        await _wait_until(lambda: controller.snapshot().unresolved_reservation is not None)
        source.update(connection_generation=2)
        world._consume(LifecycleChanged(ClientLifecycle.CONNECTED, ClientLifecycle.RESUMING))  # noqa: SLF001
        await asyncio.sleep(0)
        self.assertEqual(controller.snapshot().unknown_count, 0)
        source.update(last_seq=4, lifecycle=ClientLifecycle.CONNECTED)
        world._consume(  # noqa: SLF001
            _server_event("game.state_sync", 4, _sync_payload(phase="vote", day=1))
        )
        world._consume(  # noqa: SLF001
            ResumeRecoveryCompleted(2, 1, None, None, True, False, 2, 4)
        )
        await _wait_until(lambda: controller.snapshot().unknown_count == 1)
        self.assertEqual(controller.snapshot().outcomes[-1].status, VoteAbilityOutcomeStatus.UNKNOWN)
        await controller.stop()
        await arbiter.stop()

    async def test_deadline_guard_suppresses_without_brain_or_wire(self) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)
        _source, world, sender, arbiter, controller = self._stack(handle, now=5.0)
        world._consume(  # noqa: SLF001
            PhaseTimingMapped("vote", 1, 1, 1, 1, 100, 101, 5.0, 5.25)
        )
        controller.start()
        await _wait_until(lambda: controller.snapshot().deadline_suppressed_count == 1)
        self.assertEqual(sender.calls, [])
        await controller.stop()
        await arbiter.stop()

    async def test_not_delivered_retries_once_only_after_fresh_generation(self) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)
        source, world, sender, arbiter, controller = self._stack(
            handle, auto="not_delivered"
        )
        controller.start()
        await _wait_until(
            lambda: bool(controller.snapshot().outcomes)
            and controller.snapshot().outcomes[-1].status
            is VoteAbilityOutcomeStatus.NOT_DELIVERED
        )
        self.assertEqual(len(sender.calls), 1)
        await asyncio.sleep(0)
        self.assertEqual(len(sender.calls), 1)
        fresh = replace(handle, connection_generation=2, action_generation=2)
        source.update(
            last_seq=2,
            connection_generation=2,
            action_generation=2,
            actions=(fresh,),
        )
        sender.auto = "accepted"
        world._consume(  # noqa: SLF001
            _server_event("game.state_sync", 2, _sync_payload(phase="vote", day=1))
        )
        world._consume(  # noqa: SLF001
            PhaseTimingMapped("vote", 1, 2, 2, 2, 100, 110, 2.0, 11.0)
        )
        await _wait_until(lambda: controller.snapshot().accepted_count == 1)
        self.assertEqual(len(sender.calls), 2)
        self.assertEqual(controller.snapshot().outcomes[-1].attempts, 2)
        await controller.stop()
        await arbiter.stop()

    async def test_delivery_unknown_is_counted_with_identity_and_never_retried(self) -> None:
        handle = AbilityAction(
            1,
            1,
            "night0",
            0,
            "ability",
            "opaque-action",
            None,
            ("p1", "p2"),
            1,
            None,
        )
        source, world, sender, arbiter, controller = self._stack(
            handle, auto="delivery_unknown"
        )
        controller.start()
        await _wait_until(lambda: controller.snapshot().unknown_count == 1)

        outcome = controller.snapshot().outcomes[-1]
        self.assertEqual(outcome.status, VoteAbilityOutcomeStatus.UNKNOWN)
        self.assertEqual(outcome.action, "ability.use")
        self.assertEqual(outcome.ability_id, "opaque-action")
        self.assertEqual(
            outcome.ability_target_player_ids,
            sender.calls[0][2],
        )
        self.assertEqual(
            outcome.request_event_id,
            "10000000-0000-4000-8000-000000000001",
        )
        self.assertEqual(outcome.send_connection_generation, 1)
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(len(sender.calls), 1)

        world._consume(  # noqa: SLF001
            PhaseTimingMapped("night0", 0, 1, 1, 1, 100, 120, 2.0, 21.0)
        )
        fresh = replace(handle, connection_generation=2, action_generation=2)
        source.update(
            last_seq=2,
            connection_generation=2,
            action_generation=2,
            actions=(fresh,),
        )
        world._consume(  # noqa: SLF001
            _server_event("game.state_sync", 2, _sync_payload(phase="night0", day=0))
        )
        world._consume(  # noqa: SLF001
            PhaseTimingMapped("night0", 0, 2, 2, 2, 100, 130, 2.1, 31.0)
        )
        await _wait_until(
            lambda: controller.snapshot().transport_cursor
            == world.transport_observations().last_order
        )
        self.assertEqual(len(sender.calls), 1)
        self.assertEqual(controller.snapshot().unknown_count, 1)
        await controller.stop()
        await arbiter.stop()

    async def test_pre_wire_deadline_suppression_rearms_on_later_extension(self) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1", "p2"), 1, False)
        _source, world, sender, arbiter, controller = self._stack(handle, now=5.0)
        world._consume(  # noqa: SLF001
            PhaseTimingMapped("vote", 1, 1, 1, 1, 100, 101, 5.0, 5.25)
        )
        controller.start()
        await _wait_until(lambda: controller.snapshot().deadline_suppressed_count == 1)
        await asyncio.sleep(0)
        self.assertEqual(len(controller.snapshot().outcomes), 1)
        self.assertEqual(sender.calls, [])

        world._consume(  # noqa: SLF001
            PhaseTimingMapped("vote", 1, 1, 1, 1, 100, 120, 5.0, 20.0)
        )
        await _wait_until(lambda: controller.snapshot().accepted_count == 1)
        self.assertEqual(len(sender.calls), 1)
        self.assertEqual(
            [outcome.status for outcome in controller.snapshot().outcomes],
            [
                VoteAbilityOutcomeStatus.DEADLINE_SUPPRESSED,
                VoteAbilityOutcomeStatus.ACCEPTED,
            ],
        )
        await controller.stop()
        await arbiter.stop()

    async def test_pre_wire_shortening_consumes_rearm_cap_without_busy_loop(self) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)
        _source, world, sender, arbiter, controller = self._stack(handle, now=5.0)
        world._consume(  # noqa: SLF001
            PhaseTimingMapped("vote", 1, 1, 1, 1, 100, 101, 5.0, 5.25)
        )
        controller.start()
        await _wait_until(lambda: controller.snapshot().deadline_suppressed_count == 1)
        await asyncio.sleep(0)
        self.assertEqual(len(controller.snapshot().outcomes), 1)

        world._consume(  # noqa: SLF001
            PhaseTimingMapped("vote", 1, 1, 1, 1, 100, 100, 5.0, 5.20)
        )
        await _wait_until(lambda: controller.snapshot().deadline_suppressed_count == 2)
        world._consume(  # noqa: SLF001
            PhaseTimingMapped("vote", 1, 1, 1, 1, 100, 99, 5.0, 5.15)
        )
        await _wait_until(lambda: controller.snapshot().deadline_suppressed_count == 3)
        self.assertEqual(sender.calls, [])

        world._consume(  # noqa: SLF001
            PhaseTimingMapped("vote", 1, 1, 1, 1, 100, 130, 5.0, 30.0)
        )
        await _wait_until(
            lambda: controller.snapshot().transport_cursor
            == world.transport_observations().last_order
        )
        await asyncio.sleep(0)
        self.assertEqual(sender.calls, [])
        self.assertEqual(len(controller.snapshot().outcomes), 3)
        self.assertTrue(
            all(
                outcome.status is VoteAbilityOutcomeStatus.DEADLINE_SUPPRESSED
                for outcome in controller.snapshot().outcomes
            )
        )
        await controller.stop()
        await arbiter.stop()

    async def test_mapping_replacement_rearms_before_wire_without_duplicate_send(self) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1", "p2"), 1, False)
        source = _Source((handle,))
        world = WorldState(source)
        world._consume(  # noqa: SLF001
            _server_event("game.state_sync", 1, _sync_payload(phase="vote", day=1))
        )
        world._consume(  # noqa: SLF001
            PhaseTimingMapped("vote", 1, 1, 1, 1, 100, 110, 1.0, 11.0)
        )
        brain = _FirstCallBlockingVoteBrain()
        sender = _Sender(world, source, auto="accepted")
        brain_controller = BrainController(
            world=world,
            sender=sender,
            brain=brain,
            config=BrainRunConfig(max_decision_seconds=0.25),
            clock=lambda: 1.0,
        )
        arbiter = BrainInvocationArbiter(controller=brain_controller, clock=lambda: 1.0)
        controller = VoteAbilityController(
            world=world, invoker=arbiter, clock=lambda: 1.0
        )
        controller.start()
        await asyncio.wait_for(brain.started.wait(), 1.0)
        world._consume(  # noqa: SLF001
            PhaseTimingMapped("vote", 1, 1, 1, 1, 100, 120, 1.0, 21.0)
        )
        brain.release.set()
        await _wait_until(lambda: controller.snapshot().accepted_count == 1)
        self.assertEqual(brain.calls, 2)
        self.assertEqual(len(sender.calls), 1)
        self.assertEqual(
            [outcome.status for outcome in controller.snapshot().outcomes],
            [
                VoteAbilityOutcomeStatus.DEADLINE_SUPPRESSED,
                VoteAbilityOutcomeStatus.ACCEPTED,
            ],
        )
        await controller.stop()
        await arbiter.stop()

    async def test_public_wait_is_repeatable_and_stop_does_not_stop_shared_arbiter(self) -> None:
        handle = VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)
        _source, _world, _sender, arbiter, controller = self._stack(handle)
        with self.assertRaises(RuntimeError):
            await controller.wait()
        controller.start()
        waiter = asyncio.create_task(controller.wait())
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        await controller.stop()
        first = await controller.wait()
        second = await controller.wait()
        self.assertIs(first, second)
        self.assertEqual(first.reason, FeatureControllerExitReason.STOP_REQUESTED)
        self.assertEqual(controller.snapshot().lifecycle, VoteAbilityLifecycle.STOPPED)
        self.assertFalse(arbiter._stopped)  # noqa: SLF001 - ownership assertion
        await arbiter.stop()
