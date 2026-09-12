from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
import random
import time
from typing import Any
import unittest
from unittest.mock import patch

from ai_client.brain import (
    AbilityDecision,
    BrainController,
    BrainInvocationArbiter,
    BrainInvocationPriority,
    BrainInput,
    BrainRunConfig,
    ChatDecision,
    CoDeclareDecision,
    CoReportDecision,
    CoordinatorExitReason,
    DecisionStatus,
    DispatchDeadline,
    DummyBrain,
    NoDecision,
    PhaseBrainCoordinator,
    VoteDecision,
)
from ai_client.network import (
    AbilityAction,
    ChatAction,
    CoDeclareAction,
    CoReportAction,
    DeliveryUnknownError,
    NotDeliveredError,
    SendReceipt,
    VoteAction,
)
from ai_client.world import (
    AbilityResultView,
    CoView,
    CurrentActionsView,
    CurrentPhaseDeadline,
    Freshness,
    HistoryView,
    WorldSnapshot,
    TransportObservationView,
)


class _FakeWorld:
    def __init__(self, *, action: object | None = None) -> None:
        snapshot = WorldSnapshot(
            version=1,
            freshness=Freshness.CURRENT,
            is_caught_up=True,
            last_applied_seq=10,
            phase=replace_phase(),
        )
        self._snapshot = snapshot
        self._actions = CurrentActionsView(
            world_version=1,
            world_last_applied_seq=10,
            network_last_seq=10,
            is_caught_up=True,
            actions=() if action is None else (action,),
        )
        self._update = asyncio.Event()
        self.reads = 0
        self.deadline = CurrentPhaseDeadline(1, "day", 1, 1, 1, 10.0)

    def snapshot(self) -> WorldSnapshot:
        return self._snapshot

    def current_actions(self) -> CurrentActionsView:
        self.reads += 1
        return self._actions

    def history(self) -> HistoryView:
        return HistoryView((), True, self._snapshot.history_retention)

    def co_for_day(self, day: int) -> CoView:
        return CoView((), (), True, self._snapshot.history_retention)

    def ability_results(self) -> AbilityResultView:
        return AbilityResultView((), True, self._snapshot.history_retention)

    def transport_observations(self) -> TransportObservationView:
        return TransportObservationView(
            world_version=self._snapshot.version,
            first_retained_order=None,
            last_order=None,
            gap_before_first=False,
            observations=(),
            current_deadline=self.deadline,
        )

    async def wait_for_update(self, after_version: int) -> WorldSnapshot:
        while self._snapshot.version <= after_version and self._snapshot.freshness not in {
            Freshness.ENDED,
            Freshness.FAILED,
        }:
            event = self._update
            await event.wait()
        return self._snapshot

    def update(
        self,
        *,
        action: object | None = None,
        freshness: Freshness = Freshness.CURRENT,
        phase: tuple[int, str] | None = None,
        caught_up: bool = True,
    ) -> None:
        next_version = self._snapshot.version + 1
        day, phase_name = phase or (
            self._snapshot.phase.day if self._snapshot.phase else 1,
            self._snapshot.phase.phase if self._snapshot.phase else "day",
        )
        self._snapshot = replace(
            self._snapshot,
            version=next_version,
            freshness=freshness,
            is_caught_up=caught_up,
            phase=replace_phase(day=day, phase=phase_name),
        )
        self._actions = replace(
            self._actions,
            world_version=next_version,
            world_last_applied_seq=self._snapshot.last_applied_seq,
            network_last_seq=self._snapshot.last_applied_seq,
            is_caught_up=caught_up,
            actions=() if action is None else (action,),
        )
        event = self._update
        self._update = asyncio.Event()
        event.set()


def replace_phase(*, day: int = 1, phase: str = "day"):
    from ai_client.world import PhaseView

    return PhaseView(phase=phase, day=day, phase_ends_at=100)


class _Sender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.receipt = SendReceipt("receipt-1", 1)
        self.error: BaseException | None = None

    async def _record(self, name: str, *args: Any) -> SendReceipt:
        self.calls.append((name, args))
        if self.error is not None:
            raise self.error
        return self.receipt

    async def send_chat(self, *args: Any) -> SendReceipt:
        return await self._record("chat", *args)

    async def send_vote(self, *args: Any) -> SendReceipt:
        return await self._record("vote", *args)

    async def send_ability(self, *args: Any) -> SendReceipt:
        return await self._record("ability", *args)

    async def send_co_declare(self, *args: Any) -> SendReceipt:
        return await self._record("co_declare", *args)

    async def send_co_report(self, *args: Any) -> SendReceipt:
        return await self._record("co_report", *args)


def action(kind: str) -> object:
    common = {
        "connection_generation": 1,
        "action_generation": 1,
        "phase": "day",
        "day": 1,
        "type": kind,
    }
    if kind == "chat":
        return ChatAction(**common, channel="public")
    if kind == "vote":
        return VoteAction(**common, valid_targets=("p2",), target_count=1, allows_abstain=False)
    if kind == "ability":
        return AbilityAction(
            **common,
            ability_id="inspect",
            description=None,
            valid_targets=("p2",),
            target_count=1,
            uses_remaining=1,
        )
    if kind == "co_declare":
        return CoDeclareAction(**common, claimed_role_ids=("seer",))
    if kind == "co_report":
        return CoReportAction(**common)
    raise AssertionError(kind)


class _ScriptedBrain:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[BrainInput] = []

    async def decide(self, request: BrainInput) -> object:
        self.calls.append(request)
        return self.result


class _BlockingBrain:
    def __init__(self, result: object | None = None) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.result = result if result is not None else NoDecision()

    async def decide(self, request: BrainInput) -> object:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.result


class _CancellationIgnoringBrain:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.calls = 0

    async def decide(self, request: BrainInput) -> object:
        self.calls += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.15)
            return ChatDecision("action:0", "late")
        raise AssertionError("unreachable")


class _OrderedBlockingBrain:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started = [asyncio.Event() for _ in range(3)]
        self.release = [asyncio.Event() for _ in range(3)]

    async def decide(self, request: BrainInput) -> object:
        index = len(self.calls)
        handle = request.action_context.options[0].handle
        kind = "vote" if isinstance(handle, VoteAction) else "chat"
        self.calls.append(kind)
        self.started[index].set()
        await self.release[index].wait()
        if kind == "vote":
            return VoteDecision("action:0", "p2")
        return ChatDecision("action:0", f"message-{index}")


def make_controller(
    kind: str = "chat", *, brain: object | None = None
) -> tuple[BrainController, _FakeWorld, _Sender, BrainInput]:
    handle = action(kind)
    world = _FakeWorld(action=handle)
    sender = _Sender()
    controller = BrainController(
        world=world,
        sender=sender,
        brain=brain or DummyBrain(seed=7),
        config=BrainRunConfig(max_decision_seconds=0.2, cancellation_grace_seconds=0.02),
    )
    request = controller.capture_input()
    assert request is not None
    return controller, world, sender, request


class BrainInterfaceTests(unittest.IsolatedAsyncioTestCase):
    def test_input_and_decisions_are_frozen(self) -> None:
        controller, _world, _sender, request = make_controller()
        with self.assertRaises(FrozenInstanceError):
            request.action_context = request.action_context  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            request.action_context.options[0].option_id = "other"  # type: ignore[misc]
        self.assertIsInstance(request, BrainInput)

    def test_capture_preserves_version_seq_and_original_handle(self) -> None:
        controller, world, _sender, request = make_controller()
        handle = world.current_actions().actions[0]
        option = request.action_context.options[0]
        self.assertEqual(option.option_id, "action:0")
        self.assertIs(option.handle, handle)
        self.assertEqual(request.snapshot.version, request.action_context.world_version)
        self.assertEqual(request.snapshot.last_applied_seq, request.action_context.network_last_seq)
        self.assertEqual(world.reads, 2)

    def test_capture_rejects_inconsistent_state_but_allows_empty_actions(self) -> None:
        controller, world, _sender, _request = make_controller()
        world.update(caught_up=False)
        self.assertIsNone(controller.capture_input())
        world.update(caught_up=True, action=None)
        request = controller.capture_input()
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.action_context.options, ())

    def test_filtered_capture_uses_canonical_order_and_original_handles(self) -> None:
        controller, world, _sender, _request = make_controller()
        chat = action("chat")
        vote = action("vote")
        ability = action("ability")
        world._actions = replace(world._actions, actions=(vote, chat, ability))
        request = controller.capture_input(allowed_handles=(ability, chat))
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(
            [option.option_id for option in request.action_context.options],
            ["action:0", "action:1"],
        )
        self.assertIs(request.action_context.options[0].handle, chat)
        self.assertIs(request.action_context.options[1].handle, ability)
        self.assertIsNone(controller.capture_input(allowed_handles=()))
        self.assertIsNone(controller.capture_input(allowed_handles=(chat, chat)))
        self.assertIsNone(controller.capture_input(allowed_handles=(action("co_report"),)))

    async def test_each_typed_decision_dispatches_once(self) -> None:
        cases = (
            ("chat", ChatDecision("action:0", "hello"), "chat"),
            ("vote", VoteDecision("action:0", "p2"), "vote"),
            ("ability", AbilityDecision("action:0", ("p2",)), "ability"),
            ("co_declare", CoDeclareDecision("action:0", "seer", "claim"), "co_declare"),
            ("co_report", CoReportDecision("action:0", "inspect", "p2", "clear"), "co_report"),
        )
        for kind, decision, expected_name in cases:
            with self.subTest(kind=kind):
                brain = _ScriptedBrain(decision)
                controller, _world, sender, request = make_controller(kind, brain=brain)
                outcome = await controller.decide_and_send(request)
                self.assertEqual(outcome.status, DecisionStatus.SENT)
                self.assertEqual([name for name, _args in sender.calls], [expected_name])

    async def test_no_decision_never_sends(self) -> None:
        controller, _world, sender, request = make_controller(brain=DummyBrain(seed=1))
        outcome = await controller.decide_and_send(request)
        self.assertEqual(outcome.status, DecisionStatus.NO_DECISION)
        self.assertEqual(sender.calls, [])

    async def test_dispatch_deadline_is_rechecked_immediately_before_send(self) -> None:
        brain = _ScriptedBrain(ChatDecision("action:0", "hello"))
        handle = action("chat")
        world = _FakeWorld(action=handle)
        sender = _Sender()
        now = [5.0]
        controller = BrainController(
            world=world,
            sender=sender,
            brain=brain,
            config=BrainRunConfig(max_decision_seconds=0.2),
            clock=lambda: now[0],
        )
        request = controller.capture_input()
        assert request is not None
        deadline = DispatchDeadline(1, "day", 1, 1, 1, 9.0)
        outcome = await controller.decide_and_send(request, dispatch_deadline=deadline)
        self.assertEqual(outcome.status, DecisionStatus.SENT)
        self.assertEqual(len(sender.calls), 1)

        sender.calls.clear()
        request = controller.capture_input()
        assert request is not None
        world.deadline = replace(world.deadline, mapping_order=2)
        outcome = await controller.decide_and_send(request, dispatch_deadline=deadline)
        self.assertEqual(outcome.status, DecisionStatus.DEADLINE_SUPPRESSED)
        self.assertEqual(sender.calls, [])

        world.deadline = replace(world.deadline, mapping_order=1)
        now[0] = 9.0
        request = controller.capture_input()
        assert request is not None
        outcome = await controller.decide_and_send(request, dispatch_deadline=deadline)
        self.assertEqual(outcome.status, DecisionStatus.DEADLINE_SUPPRESSED)
        self.assertEqual(sender.calls, [])

    async def test_public_dispatched_decision_is_exact_once_and_wrong_receipt_safe(
        self,
    ) -> None:
        decision = ChatDecision("action:0", "hello")
        controller, _world, sender, request = make_controller(
            brain=_ScriptedBrain(decision)
        )
        outcome = await controller.decide_and_send(request)
        self.assertEqual(outcome.status, DecisionStatus.SENT)
        self.assertIsNone(
            controller.take_dispatched_decision(SendReceipt("wrong", 1))
        )
        self.assertIs(controller.take_dispatched_decision(sender.receipt), decision)
        self.assertIsNone(controller.take_dispatched_decision(sender.receipt))
        self.assertIsNone(controller.take_dispatched_decision(None))

    async def test_arbiter_is_bounded_non_preemptive_and_grants_reservation_next(
        self,
    ) -> None:
        chat = action("chat")
        vote = action("vote")
        assert isinstance(chat, ChatAction)
        assert isinstance(vote, VoteAction)
        world = _FakeWorld(action=chat)
        world._actions = replace(world._actions, actions=(chat, vote))
        sender = _Sender()
        brain = _OrderedBlockingBrain()
        controller = BrainController(
            world=world,
            sender=sender,  # type: ignore[arg-type]
            brain=brain,
            config=BrainRunConfig(max_decision_seconds=1.0),
        )
        arbiter = BrainInvocationArbiter(controller=controller)
        deadline = DispatchDeadline(1, "day", 1, 1, 1, time.monotonic() + 5)

        active = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(chat,),
                timeout_seconds=1.0,
                dispatch_deadline=deadline,
            )
        )
        await brain.started[0].wait()
        pending_reaction = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(chat,),
                timeout_seconds=1.0,
                dispatch_deadline=deadline,
            )
        )
        await asyncio.sleep(0)
        pending_reservation = asyncio.create_task(
            arbiter.invoke(
                owner="vote_ability",
                priority=BrainInvocationPriority.RESERVATION_ACTION,
                allowed_handles=(vote,),
                timeout_seconds=1.0,
                dispatch_deadline=deadline,
            )
        )
        await asyncio.sleep(0)
        with self.assertRaisesRegex(RuntimeError, "already has a pending"):
            await arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(chat,),
                timeout_seconds=1.0,
                dispatch_deadline=deadline,
            )
        self.assertEqual(brain.calls, ["chat"])

        brain.release[0].set()
        await brain.started[1].wait()
        self.assertEqual(brain.calls, ["chat", "vote"])
        brain.release[1].set()
        await brain.started[2].wait()
        self.assertEqual(brain.calls, ["chat", "vote", "chat"])
        brain.release[2].set()
        results = await asyncio.gather(active, pending_reservation, pending_reaction)
        self.assertTrue(
            all(result.outcome.status is DecisionStatus.SENT for result in results)
        )
        self.assertEqual(len(sender.calls), 3)
        await arbiter.stop()

    async def test_arbiter_captures_after_grant_and_suppresses_cutoff_before_brain(
        self,
    ) -> None:
        chat = action("chat")
        vote = action("vote")
        assert isinstance(chat, ChatAction)
        assert isinstance(vote, VoteAction)
        world = _FakeWorld(action=chat)
        world._actions = replace(world._actions, actions=(chat, vote))
        sender = _Sender()
        brain = _OrderedBlockingBrain()
        now = [1.0]
        controller = BrainController(
            world=world,
            sender=sender,  # type: ignore[arg-type]
            brain=brain,
            config=BrainRunConfig(max_decision_seconds=1.0),
            clock=lambda: now[0],
        )
        arbiter = BrainInvocationArbiter(controller=controller, clock=lambda: now[0])
        deadline = DispatchDeadline(1, "day", 1, 1, 1, 5.0)
        active = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(chat,),
                timeout_seconds=1.0,
                dispatch_deadline=deadline,
            )
        )
        await brain.started[0].wait()
        pending = asyncio.create_task(
            arbiter.invoke(
                owner="vote_ability",
                priority=BrainInvocationPriority.RESERVATION_ACTION,
                allowed_handles=(vote,),
                timeout_seconds=1.0,
                dispatch_deadline=deadline,
            )
        )
        await asyncio.sleep(0)
        replacement_vote = replace(vote, valid_targets=("p3",))
        world._actions = replace(world._actions, actions=(chat, replacement_vote))
        brain.release[0].set()
        first, stale = await asyncio.gather(active, pending)
        self.assertEqual(first.outcome.status, DecisionStatus.SENT)
        self.assertEqual(stale.outcome.status, DecisionStatus.STALE)
        self.assertEqual(brain.calls, ["chat"])

        now[0] = 5.0
        suppressed = await arbiter.invoke(
            owner="vote_ability",
            priority=BrainInvocationPriority.RESERVATION_ACTION,
            allowed_handles=(replacement_vote,),
            timeout_seconds=1.0,
            dispatch_deadline=deadline,
        )
        self.assertEqual(suppressed.outcome.status, DecisionStatus.DEADLINE_SUPPRESSED)
        self.assertFalse(suppressed.outcome.invocation_started)
        self.assertEqual(brain.calls, ["chat"])
        await arbiter.stop()

    async def test_arbiter_stop_cancels_active_and_pending_and_is_permanent(self) -> None:
        chat = action("chat")
        vote = action("vote")
        assert isinstance(chat, ChatAction)
        assert isinstance(vote, VoteAction)
        world = _FakeWorld(action=chat)
        world._actions = replace(world._actions, actions=(chat, vote))
        brain = _OrderedBlockingBrain()
        controller = BrainController(
            world=world,
            sender=_Sender(),  # type: ignore[arg-type]
            brain=brain,
            config=BrainRunConfig(max_decision_seconds=1.0),
        )
        arbiter = BrainInvocationArbiter(controller=controller)
        deadline = DispatchDeadline(1, "day", 1, 1, 1, time.monotonic() + 5)
        active = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(chat,),
                timeout_seconds=1.0,
                dispatch_deadline=deadline,
            )
        )
        await brain.started[0].wait()
        pending = asyncio.create_task(
            arbiter.invoke(
                owner="vote_ability",
                priority=BrainInvocationPriority.RESERVATION_ACTION,
                allowed_handles=(vote,),
                timeout_seconds=1.0,
                dispatch_deadline=deadline,
            )
        )
        await asyncio.sleep(0)
        await arbiter.stop()
        active_result, pending_result = await asyncio.gather(active, pending)
        self.assertEqual(active_result.outcome.status, DecisionStatus.CANCELLED)
        self.assertEqual(pending_result.outcome.status, DecisionStatus.CANCELLED)
        with self.assertRaisesRegex(RuntimeError, "is stopped"):
            await arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(chat,),
                timeout_seconds=1.0,
                dispatch_deadline=deadline,
            )

    async def test_arbiter_rejects_owner_priority_family_mismatch(self) -> None:
        controller, _world, _sender, _request = make_controller()
        arbiter = BrainInvocationArbiter(controller=controller)
        chat = action("chat")
        deadline = DispatchDeadline(1, "day", 1, 1, 1, time.monotonic() + 5)
        with self.assertRaisesRegex(ValueError, "priority does not match owner"):
            await arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.RESERVATION_ACTION,
                allowed_handles=(chat,),  # type: ignore[arg-type]
                timeout_seconds=1.0,
                dispatch_deadline=deadline,
            )
        with self.assertRaisesRegex(ValueError, "do not match owner"):
            await arbiter.invoke(
                owner="vote_ability",
                priority=BrainInvocationPriority.RESERVATION_ACTION,
                allowed_handles=(chat,),  # type: ignore[arg-type]
                timeout_seconds=1.0,
                dispatch_deadline=deadline,
            )
        await arbiter.stop()

    async def test_arbiter_sent_without_public_decision_fails_visibly(self) -> None:
        decision = ChatDecision("action:0", "hello")
        controller, _world, _sender, _request = make_controller(
            brain=_ScriptedBrain(decision)
        )
        controller.take_dispatched_decision = lambda _receipt: None  # type: ignore[method-assign]
        arbiter = BrainInvocationArbiter(controller=controller)
        with self.assertRaisesRegex(RuntimeError, "decision is unavailable"):
            await arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(action("chat"),),  # type: ignore[arg-type]
                timeout_seconds=0.1,
                dispatch_deadline=DispatchDeadline(
                    1, "day", 1, 1, 1, time.monotonic() + 5
                ),
            )
        await arbiter.stop()

    async def test_invalid_decisions_are_observable_and_never_send(self) -> None:
        invalid = (
            object(),
            ChatDecision("missing", "hello"),
            VoteDecision("action:0", "not-listed"),
            AbilityDecision("action:0", ("p2", "p2")),
            CoDeclareDecision("action:0", "not-listed", "claim"),
            CoReportDecision("action:0", "", "p2", "clear"),
        )
        for decision in invalid:
            with self.subTest(decision=type(decision).__name__):
                kind = "chat" if isinstance(decision, (object, ChatDecision)) else "vote"
                if isinstance(decision, VoteDecision):
                    kind = "vote"
                elif isinstance(decision, AbilityDecision):
                    kind = "ability"
                elif isinstance(decision, CoDeclareDecision):
                    kind = "co_declare"
                elif isinstance(decision, CoReportDecision):
                    kind = "co_report"
                brain = _ScriptedBrain(decision)
                controller, _world, sender, request = make_controller(kind, brain=brain)
                outcome = await controller.decide_and_send(request)
                self.assertEqual(outcome.status, DecisionStatus.INVALID_DECISION)
                self.assertEqual(sender.calls, [])

    async def test_forbidden_vote_abstain_is_invalid_and_never_sends(self) -> None:
        brain = _ScriptedBrain(VoteDecision("action:0", None))
        controller, _world, sender, request = make_controller("vote", brain=brain)

        outcome = await controller.decide_and_send(request)

        self.assertEqual(outcome.status, DecisionStatus.INVALID_DECISION)
        self.assertEqual(sender.calls, [])

    async def test_decision_subtype_mismatch_is_invalid_and_never_sends(self) -> None:
        brain = _ScriptedBrain(ChatDecision("action:0", "hello"))
        controller, _world, sender, request = make_controller("vote", brain=brain)
        vote_with_chat_type = replace(action("vote"), type="chat")
        self.assertFalse(
            controller._decision_matches_handle(
                ChatDecision("action:0", "hello"), vote_with_chat_type
            )
        )

        outcome = await controller.decide_and_send(request)

        self.assertEqual(outcome.status, DecisionStatus.INVALID_DECISION)
        self.assertEqual(sender.calls, [])

    async def test_ability_target_count_mismatch_is_invalid_and_never_sends(self) -> None:
        brain = _ScriptedBrain(AbilityDecision("action:0", ()))
        controller, _world, sender, request = make_controller("ability", brain=brain)

        outcome = await controller.decide_and_send(request)

        self.assertEqual(outcome.status, DecisionStatus.INVALID_DECISION)
        self.assertEqual(sender.calls, [])

    async def test_incomplete_snapshot_prevents_brain_invocation(self) -> None:
        brain = _ScriptedBrain(NoDecision())
        controller, world, _sender, _request = make_controller(brain=brain)
        world._snapshot = replace(world.snapshot(), unknown_event_count=1)

        self.assertIsNone(controller.capture_input())
        self.assertEqual(brain.calls, [])

    async def test_stop_during_dispatch_cancels_without_sending(self) -> None:
        controller, _world, sender, request = make_controller()
        controller._stopping = True

        outcome = await controller._dispatch(request, ChatDecision("action:0", "late"))

        self.assertEqual(outcome.status, DecisionStatus.CANCELLED)
        self.assertEqual(sender.calls, [])

    async def test_explicit_stop_cancels_active_brain_without_sending(self) -> None:
        brain = _BlockingBrain(ChatDecision("action:0", "late"))
        controller, _world, sender, request = make_controller(brain=brain)
        task = asyncio.create_task(controller.decide_and_send(request))
        await asyncio.wait_for(brain.started.wait(), 0.2)

        await controller.stop()
        outcome = await task

        self.assertEqual(outcome.status, DecisionStatus.CANCELLED)
        self.assertEqual(sender.calls, [])

    async def test_stale_action_after_validation_does_not_send(self) -> None:
        brain = _BlockingBrain(ChatDecision("action:0", "hello"))
        controller, world, sender, request = make_controller(brain=brain)
        task = asyncio.create_task(controller.decide_and_send(request))
        await asyncio.wait_for(brain.started.wait(), 0.2)
        world.update(action=replace(action("chat"), action_generation=2))
        brain.release.set()
        outcome = await task
        self.assertEqual(outcome.status, DecisionStatus.STALE)
        self.assertEqual(sender.calls, [])

    async def test_stale_selected_action_is_not_hidden_by_another_option(self) -> None:
        brain = _BlockingBrain(VoteDecision("action:1", "p2"))
        controller, world, sender, _request = make_controller(brain=brain)
        chat_handle = action("chat")
        vote_handle = action("vote")
        world._actions = replace(world._actions, actions=(chat_handle, vote_handle))
        request = controller.capture_input()
        self.assertIsNotNone(request)
        assert request is not None

        task = asyncio.create_task(controller.decide_and_send(request))
        await asyncio.wait_for(brain.started.wait(), 0.2)
        world._actions = replace(world._actions, actions=(chat_handle,))
        brain.release.set()

        outcome = await task

        self.assertEqual(outcome.status, DecisionStatus.STALE)
        self.assertIsNone(outcome.error_type)
        self.assertEqual(sender.calls, [])

    async def test_failed_world_invalidates_in_flight_invocation(self) -> None:
        brain = _BlockingBrain(ChatDecision("action:0", "late"))
        controller, world, sender, request = make_controller(brain=brain)
        task = asyncio.create_task(controller.decide_and_send(request))
        await asyncio.wait_for(brain.started.wait(), 0.2)

        world.update(freshness=Freshness.FAILED)
        outcome = await task

        self.assertEqual(outcome.status, DecisionStatus.STALE)
        self.assertEqual(sender.calls, [])

    async def test_world_updates_while_brain_is_waiting(self) -> None:
        brain = _BlockingBrain()
        controller, world, _sender, request = make_controller(brain=brain)
        task = asyncio.create_task(controller.decide_and_send(request))
        await asyncio.wait_for(brain.started.wait(), 0.2)
        world.update(action=action("chat"))
        await asyncio.sleep(0)
        self.assertEqual(world.snapshot().version, 2)
        brain.release.set()
        outcome = await task
        self.assertEqual(outcome.status, DecisionStatus.NO_DECISION)

    async def test_timeout_exception_and_send_failures_have_no_fallback(self) -> None:
        brain = _BlockingBrain()
        controller, _world, sender, request = make_controller(brain=brain)
        controller.config = BrainRunConfig(max_decision_seconds=0.03, cancellation_grace_seconds=0.01)
        outcome = await controller.decide_and_send(request)
        self.assertEqual(outcome.status, DecisionStatus.TIMED_OUT)
        self.assertEqual(sender.calls, [])

        class _ErrorBrain:
            async def decide(self, request: BrainInput) -> object:
                raise ValueError("private failure detail")

        controller, _world, sender, request = make_controller(brain=_ErrorBrain())
        outcome = await controller.decide_and_send(request)
        self.assertEqual(outcome.status, DecisionStatus.BRAIN_FAILED)
        self.assertEqual(outcome.error_type, "ValueError")
        self.assertIsNone(getattr(outcome, "error", None))
        self.assertEqual(sender.calls, [])

        failing = _ScriptedBrain(object())
        controller, _world, sender, request = make_controller(brain=failing)
        outcome = await controller.decide_and_send(request)
        self.assertEqual(outcome.status, DecisionStatus.INVALID_DECISION)
        self.assertEqual(sender.calls, [])

        for error, expected in (
            (NotDeliveredError("not delivered"), DecisionStatus.SEND_NOT_DELIVERED),
            (DeliveryUnknownError("unknown"), DecisionStatus.SEND_DELIVERY_UNKNOWN),
            (Exception("unexpected"), DecisionStatus.SEND_DELIVERY_UNKNOWN),
        ):
            controller, _world, sender, request = make_controller(
                "chat", brain=_ScriptedBrain(ChatDecision("action:0", "hello"))
            )
            sender.error = error
            outcome = await controller.decide_and_send(request)
            self.assertEqual(outcome.status, expected)

    async def test_delivery_unknown_retains_sanitized_reservation_attempt(self) -> None:
        controller, _world, sender, request = make_controller(
            "vote", brain=_ScriptedBrain(VoteDecision("action:0", "p2"))
        )
        sender.error = DeliveryUnknownError(
            "unknown",
            request_event_id="10000000-0000-4000-8000-000000000001",
            action="vote.cast",
            connection_generation=1,
        )

        outcome = await controller.decide_and_send(request)

        self.assertEqual(outcome.status, DecisionStatus.SEND_DELIVERY_UNKNOWN)
        self.assertEqual(
            outcome.request_event_id,
            "10000000-0000-4000-8000-000000000001",
        )
        self.assertEqual(outcome.attempt_action, "vote.cast")
        self.assertEqual(outcome.send_connection_generation, 1)
        self.assertEqual(outcome.vote_target_player_id, "p2")
        self.assertIsNone(outcome.ability_id)
        self.assertEqual(outcome.ability_target_player_ids, ())

    async def test_unresponsive_brain_is_permanently_disabled(self) -> None:
        brain = _CancellationIgnoringBrain()
        controller, world, sender, request = make_controller(brain=brain)
        controller.config = BrainRunConfig(
            max_decision_seconds=0.02, cancellation_grace_seconds=0.005
        )
        outcome = await controller.decide_and_send(request)
        self.assertEqual(outcome.status, DecisionStatus.TIMED_OUT)
        self.assertTrue(controller.unresponsive)
        world.update(
            action=replace(action("chat"), phase="vote"), phase=(1, "vote")
        )
        next_request = controller.capture_input()
        self.assertIsNotNone(next_request)
        assert next_request is not None
        next_outcome = await controller.decide_and_send(next_request)
        self.assertEqual(next_outcome.status, DecisionStatus.BRAIN_FAILED)
        self.assertEqual(brain.calls, 1)
        self.assertEqual(sender.calls, [])
        await asyncio.sleep(0.16)
        self.assertEqual(sender.calls, [])

    async def test_replacement_brain_uses_same_controller_dispatch(self) -> None:
        scripted = _ScriptedBrain(ChatDecision("action:0", "hello"))
        controller, _world, sender, request = make_controller("chat", brain=scripted)
        outcome = await controller.decide_and_send(request)
        self.assertEqual(outcome.status, DecisionStatus.SENT)
        self.assertEqual(sender.calls[0][0], "chat")

    async def test_phase_coordinator_invokes_once_per_phase(self) -> None:
        controller, world, _sender, _request = make_controller(brain=DummyBrain(seed=4))
        brain = controller.brain
        coordinator = PhaseBrainCoordinator(world=world, controller=controller)
        task = asyncio.create_task(coordinator.run())
        await asyncio.sleep(0.02)
        world.update(action=action("chat"))
        await asyncio.sleep(0.02)
        world.update(action=action("chat"), phase=(1, "vote"))
        await asyncio.sleep(0.02)
        world.update(freshness=Freshness.ENDED, action=None, phase=(1, "game_end"))
        exit_value = await asyncio.wait_for(task, 0.5)
        self.assertEqual(exit_value.reason, CoordinatorExitReason.WORLD_ENDED)
        self.assertEqual(brain.call_count, 2)
        self.assertEqual(len(coordinator.attempted_phases), 2)

    async def test_phase_coordinator_run_is_single_use_and_stop_is_idempotent(self) -> None:
        controller, world, _sender, _request = make_controller(brain=DummyBrain(seed=4))
        coordinator = PhaseBrainCoordinator(world=world, controller=controller)
        task = asyncio.create_task(coordinator.run())
        await asyncio.sleep(0.02)
        world.update(freshness=Freshness.ENDED, action=None, phase=(1, "game_end"))
        await asyncio.wait_for(task, 0.5)

        with self.assertRaises(RuntimeError):
            await coordinator.run()
        await coordinator.stop()
        await coordinator.stop()

    async def test_dummy_brain_is_seeded_and_deterministic(self) -> None:
        controller, _world, _sender, request = make_controller()
        first = DummyBrain(seed=99)
        second = DummyBrain(seed=99)
        self.assertEqual(first.seed, second.seed)
        self.assertEqual(first.seed, 99)
        self.assertEqual(controller.brain.seed, 7)
        with patch("random.random", wraps=random.random) as random_spy, \
            patch("time.time", wraps=time.time) as time_spy:
            first_result = await first.decide(request)
            second_result = await second.decide(request)
            self.assertEqual(first_result, second_result)
            self.assertEqual(first_result, NoDecision())
            random_spy.assert_not_called()
            time_spy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
