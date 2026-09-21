from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai_client.brain import CaptureAttempt, CaptureReadiness, BrainInvocationPriority, DecisionStatus
from ai_client.world import Freshness
from tests.test_phase3_5_capture_catchup import (
    setup, network_ahead, admission_stack, admission_network_ahead,
    admission_catch_up, _chat, _wait_until, _assert_no_catchup_tasks,
)


@pytest.mark.parametrize('status,version', [
    ('READY', None), ('STALE', None), ('NETWORK_AHEAD', 0), ('NETWORK_AHEAD', 9),
])
def test_readiness_valid_combinations(status, version):
    value = CaptureReadiness(status, version)
    assert value.status == status and value.after_world_version == version


@pytest.mark.parametrize('status,version', [
    ('UNKNOWN', None), ('READY', 0), ('STALE', 0), ('NETWORK_AHEAD', None),
    ('NETWORK_AHEAD', True), ('NETWORK_AHEAD', -1), ('NETWORK_AHEAD', 1.0),
])
def test_readiness_invalid_combinations(status, version):
    with pytest.raises(ValueError):
        CaptureReadiness(status, version)


@pytest.mark.parametrize('status,version', [
    ('STALE', 0), ('STALE', 1), ('STALE', -1), ('STALE', 1.0), ('STALE', True),
    ('NETWORK_AHEAD', None), ('NETWORK_AHEAD', True), ('NETWORK_AHEAD', -1),
    ('NETWORK_AHEAD', 1.0), ('CAPTURED', None), ('CAPTURED', True),
    ('CAPTURED', -1), ('CAPTURED', 1.0),
])
def test_capture_attempt_status_cursor_invalid(status, version):
    handle, _, _, _, controller, _, _ = setup()
    request = controller.capture_input(allowed_handles=(handle,)) if status == 'CAPTURED' else None
    with pytest.raises(ValueError):
        CaptureAttempt(status, request, version)


@pytest.mark.parametrize('status,version', [('STALE', None), ('NETWORK_AHEAD', 0), ('CAPTURED', 1)])
def test_capture_attempt_status_cursor_valid(status, version):
    handle, _, _, _, controller, _, _ = setup()
    request = controller.capture_input(allowed_handles=(handle,)) if status == 'CAPTURED' else None
    result = CaptureAttempt(status, request, version)
    assert result.status == status and result.request is request and result.after_world_version == version


@pytest.mark.parametrize('ahead', [False, True])
def test_readiness_reads_only_coherence_and_current_deadline(ahead):
    handle, world, brain, sender, controller, _, deadline = setup()
    for name in ('history', 'co_for_day', 'ability_results'):
        setattr(world, name, Mock(side_effect=AssertionError('materialization forbidden')))
    # Only the current_deadline member of this view is exposed to readiness.
    original = world.transport_observations()
    world.transport_observations = Mock(return_value=SimpleNamespace(current_deadline=original.current_deadline))
    capture = Mock(side_effect=AssertionError('discussion capture forbidden'))
    controller._discussion = SimpleNamespace(state=SimpleNamespace(capture=capture))
    if ahead:
        network_ahead(world)
    result = controller.capture_readiness(allowed_handles=(handle,), dispatch_deadline=deadline)
    assert result.status == ('NETWORK_AHEAD' if ahead else 'READY')
    assert world.transport_observations.call_count == 1
    capture.assert_not_called()
    assert brain.calls == 0 and sender.calls == []


def test_early_stale_does_not_read_version_or_actions():
    handle, world, _, _, controller, _, deadline = setup()
    world.snapshot = Mock(return_value=SimpleNamespace(
        is_caught_up=False, phase=None, freshness=Freshness.CURRENT, complete=True))
    world.current_actions = Mock(side_effect=AssertionError('actions read after early stale'))
    assert controller.capture_input(allowed_handles=(handle,)) is None
    assert controller.capture_readiness(allowed_handles=(handle,), dispatch_deadline=deadline) == CaptureReadiness('STALE', None)
    attempt = controller.capture_input_attempt(allowed_handles=(handle,), dispatch_deadline=deadline)
    assert attempt == CaptureAttempt('STALE', None, None)
    world.current_actions.assert_not_called()


@pytest.mark.parametrize('field,value', [('day', 2), ('phase', 'vote'),
    ('connection_generation', 2), ('action_generation', 2)])
def test_readiness_rejects_handle_identity_even_when_in_current_actions(field, value):
    handle, world, _, _, controller, _, deadline = setup()
    changed = replace(handle, **{field: value})
    world._actions = replace(world._actions, actions=(changed,))
    assert controller.capture_readiness(allowed_handles=(changed,), dispatch_deadline=deadline) == CaptureReadiness('STALE', None)


@pytest.mark.parametrize('ahead', [False, True])
def test_preoffer_materialization_zero_offered_capture_once(ahead):
    asyncio.run(_preoffer_materialization_zero_offered_capture_once(ahead))


async def _preoffer_materialization_zero_offered_capture_once(ahead):
    world, brain, sender, admission, controller, arbiter, deadline = admission_stack()
    controller.capture_readiness = Mock(wraps=controller.capture_readiness)
    controller.capture_input_attempt = Mock(wraps=controller.capture_input_attempt)
    if ahead:
        admission_network_ahead(world)
    task = asyncio.create_task(arbiter.invoke(owner='reaction_chat',
        priority=BrainInvocationPriority.REACTION, allowed_handles=(_chat(),),
        timeout_seconds=1.0, dispatch_deadline=deadline))
    await _wait_until(lambda: controller.capture_readiness.call_count == 1)
    assert controller.capture_input_attempt.call_count == 0
    if ahead:
        assert admission.requests == []
        admission_catch_up(world)
    await _wait_until(lambda: len(admission.requests) == 1)
    assert controller.capture_input_attempt.call_count == 0
    admission.offer('i1')
    assert (await task).outcome.status is DecisionStatus.SENT
    assert controller.capture_readiness.call_count == (2 if ahead else 1)
    assert controller.capture_input_attempt.call_count == 1
    assert admission.order.count('claim:i1') == 1
    assert brain.calls == ['chat'] and sender.calls == ['chat']
    await arbiter.stop()
    _assert_no_catchup_tasks()


@pytest.mark.parametrize('terminal', ['caller_cancel', 'stop', 'poison', 'stale', 'ahead', 'deadline'])
def test_preoffer_terminal_never_materializes_or_acquires(terminal):
    asyncio.run(_preoffer_terminal_never_materializes_or_acquires(terminal))


async def _preoffer_terminal_never_materializes_or_acquires(terminal):
    world, brain, sender, admission, controller, arbiter, deadline = admission_stack()
    admission_network_ahead(world)
    controller.capture_readiness = Mock(wraps=controller.capture_readiness)
    controller.capture_input_attempt = Mock(wraps=controller.capture_input_attempt)
    task = asyncio.create_task(arbiter.invoke(owner='reaction_chat',
        priority=BrainInvocationPriority.REACTION, allowed_handles=(_chat(),),
        timeout_seconds=1.0, dispatch_deadline=deadline))
    await _wait_until(lambda: controller.capture_readiness.call_count == 1)
    if terminal == 'caller_cancel':
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif terminal == 'stop':
        await arbiter.stop()
        assert (await task).outcome.status is DecisionStatus.CANCELLED
    elif terminal == 'poison':
        fatal = RuntimeError('original fatal')
        async with arbiter._lock:
            arbiter._fatal_error = fatal
            arbiter._poisoned = True
            arbiter._admission_changed.set()
        with pytest.raises(RuntimeError) as raised:
            await task
        assert raised.value is fatal
    else:
        if terminal == 'deadline':
            arbiter._now = lambda: deadline.not_after_monotonic
        world.update(actions=(_chat(),), phase=(1, 'vote') if terminal == 'stale' else (1, 'day'))
        if terminal == 'ahead':
            admission_network_ahead(world)
        expected = DecisionStatus.DEADLINE_SUPPRESSED if terminal == 'deadline' else DecisionStatus.STALE
        assert (await task).outcome.status is expected
    if terminal == 'poison':
        with pytest.raises(RuntimeError) as stopped:
            await arbiter.stop()
        assert stopped.value is fatal
    else:
        await arbiter.stop()
    assert admission.requests == [] and admission.cancelled == [] and admission.replacements == []
    assert controller.capture_input_attempt.call_count == 0
    assert controller.capture_readiness.call_count <= 2
    assert brain.calls == [] and sender.calls == []
    _assert_no_catchup_tasks()
