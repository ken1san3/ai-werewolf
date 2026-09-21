from __future__ import annotations

import asyncio
from dataclasses import replace
import time

import pytest

from ai_client.brain import (
    BrainController,
    BrainInvocationArbiter,
    BrainInvocationPriority,
    BrainRunConfig,
    CaptureAttempt,
    ChatDecision,
    DecisionStatus,
    DispatchDeadline,
)
from tests.test_phase3_3_brain_interface import _FakeWorld, _Sender, action
from tests.test_phase5_brain_admission import (
    _Admission, _Brain as _AdmissionBrain, _Sender as _AdmissionSender,
    _World as _AdmissionWorld, _chat, _wait_until,
)


class _Brain:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, _request):
        self.calls += 1
        return ChatDecision("action:0", "caught up")


def setup(*, cutoff: float | None = None):
    handle = action("chat")
    world = _FakeWorld(action=handle)
    brain = _Brain()
    sender = _Sender()
    controller = BrainController(
        world=world,
        sender=sender,
        brain=brain,
        config=BrainRunConfig(max_decision_seconds=0.2),
    )
    arbiter = BrainInvocationArbiter(controller=controller)
    deadline = DispatchDeadline(
        1, "day", 1, 1, 1,
        time.monotonic() + 1 if cutoff is None else cutoff,
    )
    return handle, world, brain, sender, controller, arbiter, deadline


def network_ahead(world):
    world._actions = replace(
        world._actions,
        network_last_seq=world._actions.world_last_applied_seq + 1,
        is_caught_up=False,
        actions=(),
    )


def test_capture_attempt_contract_and_legacy_compatibility():
    handle, world, _brain, _sender, controller, _arbiter, deadline = setup()
    captured = controller.capture_input_attempt(
        allowed_handles=(handle,), dispatch_deadline=deadline
    )
    assert captured.status == "CAPTURED" and captured.request is not None
    assert controller.capture_input(
        allowed_handles=(handle,), dispatch_deadline=deadline
    ) == captured.request
    network_ahead(world)
    ahead = controller.capture_input_attempt(
        allowed_handles=(handle,), dispatch_deadline=deadline
    )
    assert ahead == CaptureAttempt("NETWORK_AHEAD", None, 1)
    assert controller.capture_input(
        allowed_handles=(handle,), dispatch_deadline=deadline
    ) is None


def test_direct_network_ahead_waits_once_then_sends_once():
    asyncio.run(_direct_network_ahead_waits_once_then_sends_once())


async def _direct_network_ahead_waits_once_then_sends_once():
    handle, world, brain, sender, _controller, arbiter, deadline = setup()
    network_ahead(world)
    task = asyncio.create_task(arbiter.invoke(
        owner="reaction_chat", priority=BrainInvocationPriority.REACTION,
        allowed_handles=(handle,), timeout_seconds=0.2,
        dispatch_deadline=deadline,
    ))
    await asyncio.sleep(0)
    assert brain.calls == 0 and sender.calls == []
    world.update(action=handle)
    result = await task
    assert result.outcome.status is DecisionStatus.SENT
    assert brain.calls == 1 and len(sender.calls) == 1
    await arbiter.stop()


def test_second_network_ahead_is_stale_without_third_wait():
    asyncio.run(_second_network_ahead_is_stale_without_third_wait())


async def _second_network_ahead_is_stale_without_third_wait():
    handle, world, brain, sender, controller, arbiter, deadline = setup()
    network_ahead(world)
    calls = 0
    original = controller.capture_input_attempt
    def capture(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            network_ahead(world)
        return original(**kwargs)
    controller.capture_input_attempt = capture  # type: ignore[method-assign]
    task = asyncio.create_task(arbiter.invoke(
        owner="reaction_chat", priority=BrainInvocationPriority.REACTION,
        allowed_handles=(handle,), timeout_seconds=0.2,
        dispatch_deadline=deadline,
    ))
    while calls < 1:
        await asyncio.sleep(0)
    world.update(action=handle)
    result = await task
    assert result.outcome.status is DecisionStatus.STALE
    assert calls == 2 and brain.calls == 0 and sender.calls == []
    await arbiter.stop()


@pytest.mark.parametrize("mutation", ["phase", "mapping", "connection", "generation", "handle"])
def test_catchup_rechecks_every_context_identity(mutation):
    asyncio.run(_catchup_rechecks_every_context_identity(mutation))


async def _catchup_rechecks_every_context_identity(mutation):
    handle, world, brain, sender, controller, arbiter, deadline = setup()
    network_ahead(world)
    attempts = 0
    original = controller.capture_input_attempt
    def capture(**kwargs):
        nonlocal attempts
        attempts += 1
        return original(**kwargs)
    controller.capture_input_attempt = capture  # type: ignore[method-assign]
    task = asyncio.create_task(arbiter.invoke(owner="reaction_chat",
        priority=BrainInvocationPriority.REACTION, allowed_handles=(handle,),
        timeout_seconds=0.2, dispatch_deadline=deadline))
    while attempts < 1:
        await asyncio.sleep(0)
    replacement = handle
    phase = None
    if mutation == "phase":
        phase = (1, "vote")
    elif mutation == "mapping":
        world.deadline = replace(world.deadline, mapping_order=2)
    elif mutation == "connection":
        replacement = replace(handle, connection_generation=2)
    elif mutation == "generation":
        replacement = replace(handle, action_generation=2)
    elif mutation == "handle":
        replacement = replace(handle, channel="other")
    world.update(action=replacement, phase=phase)
    result = await task
    assert result.outcome.status in {DecisionStatus.STALE, DecisionStatus.DEADLINE_SUPPRESSED}
    assert attempts == 2 and brain.calls == 0 and sender.calls == []
    await arbiter.stop()


def test_catchup_cutoff_is_deadline_suppressed_without_brain_or_wire():
    asyncio.run(_catchup_cutoff_is_deadline_suppressed_without_brain_or_wire())


async def _catchup_cutoff_is_deadline_suppressed_without_brain_or_wire():
    handle, world, brain, sender, _controller, arbiter, _deadline = setup()
    network_ahead(world)
    deadline = DispatchDeadline(1, "day", 1, 1, 1, time.monotonic() + 0.02)
    result = await arbiter.invoke(
        owner="reaction_chat", priority=BrainInvocationPriority.REACTION,
        allowed_handles=(handle,), timeout_seconds=0.2,
        dispatch_deadline=deadline,
    )
    assert result.outcome.status is DecisionStatus.DEADLINE_SUPPRESSED
    assert brain.calls == 0 and sender.calls == []
    await arbiter.stop()


def test_caller_cancel_joins_catchup_wait_and_releases_owner():
    asyncio.run(_caller_cancel_joins_catchup_wait_and_releases_owner())


async def _caller_cancel_joins_catchup_wait_and_releases_owner():
    handle, world, brain, sender, _controller, arbiter, deadline = setup()
    network_ahead(world)
    task = asyncio.create_task(arbiter.invoke(
        owner="reaction_chat", priority=BrainInvocationPriority.REACTION,
        allowed_handles=(handle,), timeout_seconds=0.2,
        dispatch_deadline=deadline,
    ))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert brain.calls == 0 and sender.calls == []
    assert not [task for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
                and "capture-catchup" in task.get_name()]
    await arbiter.stop()


def test_stop_joins_catchup_wait_without_brain_or_wire():
    asyncio.run(_stop_joins_catchup_wait_without_brain_or_wire())


async def _stop_joins_catchup_wait_without_brain_or_wire():
    handle, world, brain, sender, _controller, arbiter, deadline = setup()
    network_ahead(world)
    task = asyncio.create_task(arbiter.invoke(
        owner="reaction_chat", priority=BrainInvocationPriority.REACTION,
        allowed_handles=(handle,), timeout_seconds=0.2,
        dispatch_deadline=deadline,
    ))
    await asyncio.sleep(0)
    await arbiter.stop()
    result = await task
    assert result.outcome.status is DecisionStatus.CANCELLED
    assert brain.calls == 0 and sender.calls == []
    assert not [task for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
                and "capture-catchup" in task.get_name()]


def admission_stack():
    order = []
    world = _AdmissionWorld((_chat(),))
    brain = _AdmissionBrain(order)
    sender = _AdmissionSender(order)
    admission = _Admission(order)
    controller = BrainController(world=world, sender=sender, brain=brain,
        config=BrainRunConfig(max_decision_seconds=1.0))
    ids = iter(("i1", "i2", "i3", "i4"))
    arbiter = BrainInvocationArbiter(controller=controller, admission=admission,
                                     invocation_id_factory=lambda: next(ids))
    deadline = DispatchDeadline(1, "day", 1, 1, 1, time.monotonic() + 1)
    return world, brain, sender, admission, controller, arbiter, deadline


def admission_network_ahead(world):
    world._actions = replace(world._actions,
        network_last_seq=world._actions.world_last_applied_seq + 1,
        is_caught_up=False, actions=())


def admission_catch_up(world):
    world.update(actions=(_chat(),))
    world._actions = replace(world._actions, is_caught_up=True)


def test_admission_waits_before_request_then_uses_one_offer():
    asyncio.run(_admission_waits_before_request_then_uses_one_offer())


async def _admission_waits_before_request_then_uses_one_offer():
    world, brain, sender, admission, controller, arbiter, deadline = admission_stack()
    admission_network_ahead(world)
    attempts = 0
    original = controller.capture_readiness
    def capture(**kwargs):
        nonlocal attempts
        attempts += 1
        return original(**kwargs)
    controller.capture_readiness = capture  # type: ignore[method-assign]
    task = asyncio.create_task(arbiter.invoke(owner="reaction_chat",
        priority=BrainInvocationPriority.REACTION, allowed_handles=(_chat(),),
        timeout_seconds=1.0, dispatch_deadline=deadline))
    while attempts < 1:
        await asyncio.sleep(0)
    assert admission.requests == []
    admission_catch_up(world)
    await _wait_until(lambda: len(admission.requests) == 1)
    admission.offer("i1")
    result = await task
    assert result.outcome.status is DecisionStatus.SENT
    assert brain.calls == ["chat"] and sender.calls == ["chat"]
    assert len(admission.requests) == 1
    await arbiter.stop()


def test_offered_lease_waits_for_catchup_before_single_claim():
    asyncio.run(_offered_lease_waits_for_catchup_before_single_claim())


async def _offered_lease_waits_for_catchup_before_single_claim():
    world, brain, sender, admission, _controller, arbiter, deadline = admission_stack()
    task = asyncio.create_task(arbiter.invoke(owner="reaction_chat",
        priority=BrainInvocationPriority.REACTION, allowed_handles=(_chat(),),
        timeout_seconds=1.0, dispatch_deadline=deadline))
    await _wait_until(lambda: len(admission.requests) == 1)
    admission.offer("i1")
    admission_network_ahead(world)
    await asyncio.sleep(0)
    assert brain.calls == [] and sender.calls == []
    admission_catch_up(world)
    result = await task
    assert result.outcome.status is DecisionStatus.SENT
    assert admission.order.count("claim:i1") == 1
    assert brain.calls == ["chat"] and sender.calls == ["chat"]
    await arbiter.stop()


def test_offered_catchup_replacement_uses_replace_without_old_cancel():
    asyncio.run(_offered_catchup_replacement_uses_replace_without_old_cancel())


async def _offered_catchup_replacement_uses_replace_without_old_cancel():
    world, brain, sender, admission, _controller, arbiter, deadline = admission_stack()
    reaction = asyncio.create_task(arbiter.invoke(owner="reaction_chat",
        priority=BrainInvocationPriority.REACTION, allowed_handles=(_chat(),),
        timeout_seconds=1.0, dispatch_deadline=deadline))
    await _wait_until(lambda: len(admission.requests) == 1)
    admission.offer("i1")
    admission_network_ahead(world)
    await asyncio.sleep(0)
    reservation = asyncio.create_task(arbiter.invoke(owner="vote_ability",
        priority=BrainInvocationPriority.RESERVATION_ACTION,
        allowed_handles=(action("vote"),), timeout_seconds=1.0,
        dispatch_deadline=deadline))
    await _wait_until(lambda: admission.replacements == [("i1", "i2")])
    assert admission.cancelled == []
    world.update(actions=(action("chat"), action("vote")))
    world._actions = replace(world._actions, is_caught_up=True)
    admission.offer("i2")
    assert (await reservation).outcome.status is DecisionStatus.SENT
    await _wait_until(lambda: len(admission.requests) == 3)
    admission.offer("i3")
    assert (await reaction).outcome.status is DecisionStatus.SENT
    assert admission.replacements == [("i1", "i2")]
    assert brain.calls == ["vote", "chat"] and sender.calls == ["vote", "chat"]
    await arbiter.stop()


def _assert_no_catchup_tasks():
    assert not [task for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
                and "capture-catchup" in task.get_name()]


def test_poison_during_catchup_preserves_original_fatal_and_joins_waiters():
    asyncio.run(_poison_during_catchup_preserves_original_fatal_and_joins_waiters())


async def _poison_during_catchup_preserves_original_fatal_and_joins_waiters():
    handle, world, brain, sender, _controller, arbiter, deadline = setup()
    network_ahead(world)
    task = asyncio.create_task(arbiter.invoke(
        owner="reaction_chat", priority=BrainInvocationPriority.REACTION,
        allowed_handles=(handle,), timeout_seconds=0.2,
        dispatch_deadline=deadline,
    ))
    await asyncio.sleep(0)
    fatal = RuntimeError("original fatal")
    async with arbiter._lock:
        arbiter._fatal_error = fatal
        arbiter._poisoned = True
        arbiter._admission_changed.set()
    with pytest.raises(RuntimeError, match="original fatal") as raised:
        await task
    assert raised.value is fatal
    assert brain.calls == 0 and sender.calls == []
    _assert_no_catchup_tasks()


@pytest.mark.parametrize("terminal", ["caller_cancel", "stop"])
def test_offered_catchup_cancel_or_stop_cleans_lease_once(terminal):
    asyncio.run(_offered_catchup_cancel_or_stop_cleans_lease_once(terminal))


async def _offered_catchup_cancel_or_stop_cleans_lease_once(terminal):
    world, brain, sender, admission, _controller, arbiter, deadline = admission_stack()
    task = asyncio.create_task(arbiter.invoke(owner="reaction_chat",
        priority=BrainInvocationPriority.REACTION, allowed_handles=(_chat(),),
        timeout_seconds=1.0, dispatch_deadline=deadline))
    await _wait_until(lambda: len(admission.requests) == 1)
    admission.offer("i1")
    admission_network_ahead(world)
    await asyncio.sleep(0)
    if terminal == "caller_cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await arbiter.stop()
    else:
        await arbiter.stop()
        assert (await task).outcome.status is DecisionStatus.CANCELLED
    assert admission.cancelled == ["i1"]
    assert admission.replacements == []
    assert "claim:i1" not in admission.order
    assert brain.calls == [] and sender.calls == []
    _assert_no_catchup_tasks()


@pytest.mark.parametrize("terminal", ["deadline", "stale"])
def test_offered_catchup_terminal_cleans_lease_once(terminal):
    asyncio.run(_offered_catchup_terminal_cleans_lease_once(terminal))


async def _offered_catchup_terminal_cleans_lease_once(terminal):
    world, brain, sender, admission, _controller, arbiter, deadline = admission_stack()
    if terminal == "deadline":
        deadline = replace(deadline, not_after_monotonic=time.monotonic() + 0.02)
    task = asyncio.create_task(arbiter.invoke(owner="reaction_chat",
        priority=BrainInvocationPriority.REACTION, allowed_handles=(_chat(),),
        timeout_seconds=1.0, dispatch_deadline=deadline))
    await _wait_until(lambda: len(admission.requests) == 1)
    admission.offer("i1")
    admission_network_ahead(world)
    await asyncio.sleep(0)
    if terminal == "stale":
        world.update(actions=(_chat(),), phase=(1, "vote"))
    result = await task
    expected = (DecisionStatus.DEADLINE_SUPPRESSED if terminal == "deadline"
                else DecisionStatus.STALE)
    assert result.outcome.status is expected
    assert admission.cancelled == ["i1"]
    assert admission.replacements == []
    assert "claim:i1" not in admission.order
    assert brain.calls == [] and sender.calls == []
    _assert_no_catchup_tasks()
    await arbiter.stop()

@pytest.mark.parametrize('status', ['NETWORK_AHEAD', 'STALE'])
@pytest.mark.parametrize('captured_value', [object(), 'unexpected', {}, 0])
def test_non_captured_attempt_requires_exact_none(status, captured_value):
    with pytest.raises(ValueError):
        CaptureAttempt(status, captured_value, 1)
