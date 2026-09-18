import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai_client.brain import BrainController, DecisionStatus
from ai_client.network import VoteAction
from ai_client.world import Freshness
from tests.fixtures.reservation_probe import ReservationProbe


def stack(*, limit=32):
    handle = VoteAction(1, 1, "vote", 1, "vote", ("p1",), 1, False)
    snapshot = SimpleNamespace(is_caught_up=True, freshness=Freshness.CURRENT, complete=True,
                               phase=SimpleNamespace(day=1, phase="vote"),
                               version=7, last_applied_seq=9)
    actions = SimpleNamespace(is_caught_up=True, world_version=7,
                              world_last_applied_seq=9, network_last_seq=9, actions=(handle,))
    world = SimpleNamespace(snapshot=Mock(return_value=snapshot),
                            current_actions=Mock(return_value=actions))
    calls = {}

    def method(name, result):
        def body(*args, **kwargs):
            world.snapshot()
            world.current_actions()
            return result
        calls[name] = Mock(side_effect=body)
        return calls[name]

    captured = object()
    brain = SimpleNamespace(world=world,
        capture_input=method("capture_input", captured),
        _request_is_current=method("_request_is_current", True),
        _stale_before_send=method("_stale_before_send", False))
    reservation = SimpleNamespace(_handle_non_sent=Mock(return_value=captured))
    reads = (world.snapshot, world.current_actions)
    probe = ReservationProbe(brain, reservation, limit=limit)
    request = SimpleNamespace(action_context=SimpleNamespace(
        options=(SimpleNamespace(handle=handle),)))
    return brain, reservation, probe, handle, request, calls, reads, captured


@pytest.mark.parametrize("name", ["capture_input", "_request_is_current", "_stale_before_send"])
def test_original_check_and_read_counts_and_return_identity(name):
    brain, _, probe, handle, request, calls, reads, _ = stack()
    expected = object()
    calls[name].side_effect = lambda *a, **kw: (brain.world.snapshot(),
                                               brain.world.current_actions(), expected)[-1]
    args, kwargs = ((), {"allowed_handles": (handle,)}) if name == "capture_input" else ((request,), {})
    assert getattr(brain, name)(*args, **kwargs) is expected
    calls[name].assert_called_once_with(*args, **kwargs)
    for read in reads:
        read.assert_called_once_with()
    assert probe.snapshot() == []


@pytest.mark.parametrize("name", ["capture_input", "_request_is_current", "_stale_before_send"])
def test_exception_identity_is_preserved_without_exception_text(name):
    brain, _, probe, handle, request, calls, _, _ = stack()
    error = RuntimeError("sensitive-example-do-not-record")
    calls[name].side_effect = error
    with pytest.raises(RuntimeError) as caught:
        if name == "capture_input":
            brain.capture_input(allowed_handles=(handle,))
        else:
            getattr(brain, name)(request)
    assert caught.value is error
    assert calls[name].call_count == 1
    assert probe.snapshot()[0]["status"] == "ERROR"
    assert "sensitive" not in str(probe.snapshot())
    assert probe.frames == []


def test_failures_classify_actual_reads_and_bound_records():
    brain, reservation, probe, handle, request, calls, reads, sentinel = stack(limit=3)
    for name, result in (("capture_input", None), ("_request_is_current", False),
                         ("_stale_before_send", True)):
        calls[name].side_effect = lambda *a, result=result, **kw: (
            brain.world.snapshot(), brain.world.current_actions(), result)[-1]
        if name == "capture_input":
            brain.capture_input(allowed_handles=(handle,))
        else:
            getattr(brain, name)(request)
    assert [x["site"] for x in probe.snapshot()] == ["CAPTURE_NONE", "BEFORE_BRAIN", "BEFORE_SEND"]
    for item in probe.snapshot():
        assert item["actions_current"] is True and item["handles_current"] is True
    for started in (False, True):
        outcome = SimpleNamespace(status=DecisionStatus.STALE, invocation_started=started)
        assert reservation._handle_non_sent(None, outcome) is sentinel
    assert len(probe.snapshot()) == 3
    assert [x["invocation_started"] for x in probe.snapshot()[-2:]] == [False, True]
    assert all(type(value) is bool or value in {
        "STALE", "BEFORE_SEND", "OUTCOME", "UNKNOWN"}
        for record in probe.snapshot() for value in record.values())


def test_non_sent_returns_original_coroutine_without_running_or_awaiting_it():
    brain, reservation, _, _, _, _, _, sentinel = stack()
    ran = []

    async def original_body():
        ran.append(True)
        return sentinel

    coroutine = original_body()
    original = Mock(return_value=coroutine)
    reservation._handle_non_sent = original
    probe = ReservationProbe(brain, reservation)
    candidate = object()
    outcome = SimpleNamespace(status=DecisionStatus.STALE, invocation_started=False)
    returned = reservation._handle_non_sent(candidate, outcome)
    assert returned is coroutine and ran == []
    original.assert_called_once_with(candidate, outcome)
    assert asyncio.run(returned) is sentinel
    assert ran == [True]
    assert probe.snapshot() == [{"site": "OUTCOME", "status": "STALE", "invocation_started": False}]


def test_non_sent_original_exception_identity_is_preserved():
    brain, reservation, _, _, _, _, _, _ = stack()
    error = RuntimeError("sensitive-outcome")

    async def original_body():
        raise error

    coroutine = original_body()
    original = Mock(return_value=coroutine)
    reservation._handle_non_sent = original
    probe = ReservationProbe(brain, reservation)
    outcome = SimpleNamespace(status=DecisionStatus.STALE, invocation_started=False)
    returned = reservation._handle_non_sent(None, outcome)
    assert returned is coroutine
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(returned)
    assert caught.value is error
    original.assert_called_once_with(None, outcome)
    assert "sensitive" not in str(probe.snapshot())


def test_real_capture_failure_observes_only_reads_executed_by_production():
    brain, reservation, _, handle, _, _, _, _ = stack()
    real = object.__new__(BrainController)
    real.world = brain.world
    # Uncaught-up input exits capture before current_actions is read.
    snapshot = SimpleNamespace(is_caught_up=False, phase=None)
    snapshot.freshness = Freshness.CURRENT
    snapshot.complete = True
    real.world.snapshot = Mock(return_value=snapshot)
    real.world.current_actions = Mock()
    actions_read = real.world.current_actions
    probe = ReservationProbe(real, reservation)
    assert real.capture_input(allowed_handles=(handle,)) is None
    actions_read.assert_not_called()
    assert probe.snapshot() == [{"site": "CAPTURE_NONE", "status": "STALE",
                                "caught_up": False, "phase_matches": False,
                                "freshness_current": True, "world_complete": True,
                                "actions_current": "UNKNOWN", "handles_current": "UNKNOWN"}]
