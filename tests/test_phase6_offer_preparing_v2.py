from __future__ import annotations

import asyncio
import pickle
from dataclasses import replace
import time

import pytest

from ai_client.brain.model import DecisionStatus
from ai_client.discussion.capture_v2 import StateGenerationLeaseV2
from ai_client.discussion import offer_composition_v2 as module
from ai_client.discussion.offer_composition_v2 import (
    BrokerOfferOwnershipReceiptV2,
    InitialOfferSourceV2,
    OfferCompositionError,
    PreparingLeaseOwnerReceiptV2,
    _cleanup_offered_source_v2,
    _owned_acquire_initial_offer_v2,
    _publish_preparing_from_offer_v2,
    _register_initial_offer_source_v2,
    _retire_offer_composition_v2,
)
from ai_client.llm.admission_types import AdmissionStatus, GENERATION_IPC_PROTOCOL
from tests.test_phase6_initial_ticket_v2 import issue_actual, make_pending
from tests.test_phase6_offer_composition_v2 import bind, graph


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_actual_private_driver_routes_offer_and_publishes_preparing(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    g.world._current_deadline = replace(
        g.world._current_deadline,
        local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic,
    )

    result = await g.arbiter._run_offer_preparing_offline_v2(pending)

    composition = port._composition
    source = composition.active_offer_source_or_null
    ticket = composition.active_initial_ticket_or_null
    snapshot = g.store._state_generation_lease_v2
    owner = g.store._preparing_lease_owner_receipt_v2
    receipt = source.exact_offer_receipt_or_null
    assert result.outcome.status is DecisionStatus.CANCELLED
    assert pending.result.done() and pending.result.result() is result
    assert pending.execution_complete.is_set()
    assert composition.flow_state == "PREPARING"
    assert composition.initial_invocation_slot == "PREPARING_HELD"
    assert ticket.state == "CONSUMED_PREPARING"
    assert source.state == "PREPARING"
    assert type(receipt) is BrokerOfferOwnershipReceiptV2
    assert receipt.state == "CONSUMED_PREPARING"
    assert type(snapshot) is StateGenerationLeaseV2
    assert snapshot.status == "PREPARING" and snapshot.lease_revision == 0
    assert snapshot.state_lease_id == ticket.exact_request.invocation_id
    assert snapshot.broker_lease_ids == (snapshot.state_lease_id,)
    assert type(owner) is PreparingLeaseOwnerReceiptV2
    assert owner.exact_offer_receipt is receipt
    assert owner.exact_prepared_material.status == "UNLEASED"
    assert not g.backend.calls and not g.brain.calls
    assert g.session._claimed_invocation is None
    assert g.session._activated_invocation is None
    for value in (source, receipt, owner):
        with pytest.raises(TypeError):
            pickle.dumps(value)


@pytest.mark.anyio
async def test_normal_invoke_stays_blocked_while_offline_mode_is_bound(graph):
    g = graph
    bind(g)
    pending = make_pending(g)
    with pytest.raises(OfferCompositionError, match="INITIAL_TICKET_NOT_CONNECTED"):
        await g.arbiter.invoke(
            owner=pending.owner,
            priority=pending.priority,
            allowed_handles=pending.allowed_handles,
            timeout_seconds=pending.timeout_seconds,
            dispatch_deadline=pending.dispatch_deadline,
        )
    assert getattr(g.store, "_state_generation_lease_v2", None) is None
    assert not g.backend.calls and not g.brain.calls


@pytest.mark.anyio
async def test_ticket_to_source_is_exact_and_one_shot(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    ticket = await issue_actual(g, port, pending)
    source = _register_initial_offer_source_v2(port, ticket)
    assert type(source) is InitialOfferSourceV2
    assert source.exact_initial_ticket is ticket
    assert source.exact_request is ticket.exact_request
    assert ticket.state == "CONSUMED_SOURCE"
    assert port._composition.active_offer_source_or_null is source
    with pytest.raises(OfferCompositionError):
        _register_initial_offer_source_v2(port, ticket)
    with pytest.raises(TypeError):
        InitialOfferSourceV2(object())
    assert not g.session._results and not g.session._control_lanes


@pytest.mark.anyio
async def test_freshness_mismatch_cancels_offer_without_preparing_publish(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    g.world._current_deadline = replace(
        g.world._current_deadline,
        mapping_order=pending.dispatch_deadline.mapping_order + 1,
        local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic,
    )
    result = await g.arbiter._run_offer_preparing_offline_v2(pending)
    assert result.outcome.status is DecisionStatus.STALE
    assert getattr(g.store, "_state_generation_lease_v2", None) is None
    assert port._composition.flow_state == "IDLE"
    assert port._composition.initial_invocation_slot == "EMPTY"
    assert port._composition.active_offer_source_or_null is None
    assert port._composition.active_initial_ticket_or_null is None
    assert g.session._claimed_invocation is None
    assert g.session._activated_invocation is None
    assert not g.backend.calls and not g.brain.calls
    assert g.arbiter._admission_state == "IDLE"
    second = make_pending(g)
    result2 = await g.arbiter._run_offer_preparing_offline_v2(second)
    assert result2.outcome.status is DecisionStatus.CANCELLED


@pytest.mark.anyio
async def test_second_private_driver_is_blocked_by_preparing_hold(graph):
    g = graph
    bind(g)
    first = make_pending(g)
    g.world._current_deadline = replace(
        g.world._current_deadline,
        local_deadline_monotonic=first.dispatch_deadline.not_after_monotonic,
    )
    assert (await g.arbiter._run_offer_preparing_offline_v2(first)).outcome.status \
        is DecisionStatus.CANCELLED
    second = make_pending(g)
    with pytest.raises(OfferCompositionError):
        await g.arbiter._run_offer_preparing_offline_v2(second)
    assert g.store._state_generation_lease_v2.status == "PREPARING"
    assert not g.backend.calls and not g.brain.calls


@pytest.mark.anyio
async def test_offer_notification_failure_holds_exact_owner_and_never_prepares(
    graph, monkeypatch,
):
    g = graph
    port = bind(g)
    pending = make_pending(g)

    class RaisingResultFuture(asyncio.Future):
        def set_result(self, value):
            super().set_result(value)
            raise RuntimeError("synthetic wakeup failure")

    monkeypatch.setattr(module, "_new_offer_future_v2", RaisingResultFuture)
    task = asyncio.create_task(g.arbiter._run_offer_preparing_offline_v2(pending))
    for _ in range(100):
        if port._composition.flow_state == "OFFER_NOTIFICATION_UNKNOWN":
            break
        await asyncio.sleep(0)
    assert port._composition.flow_state == "OFFER_NOTIFICATION_UNKNOWN"
    source = port._composition.active_offer_source_or_null
    assert source.state == "NOTIFICATION_UNKNOWN"
    assert source.exact_offer_receipt_or_null.state == "RETIRED_UNKNOWN"
    assert port._composition.active_initial_ticket_or_null.state == "RETIRED_UNKNOWN"
    assert getattr(g.store, "_state_generation_lease_v2", None) is None
    assert g.arbiter._active is pending
    assert g.arbiter._admission_driver is task
    assert not pending.execution_complete.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert port._composition.flow_state in {"OFFER_NOTIFICATION_UNKNOWN", "IDLE"}
    assert not g.backend.calls and not g.brain.calls


@pytest.mark.anyio
@pytest.mark.parametrize("candidate", ["receipt", "state_lease", "owner_receipt", "store_cas"])
async def test_candidate_allocation_failure_never_partially_publishes_preparing(
    graph, monkeypatch, candidate,
):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    g.world._current_deadline = replace(
        g.world._current_deadline,
        local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic,
    )

    def fail(*args, **kwargs):
        raise MemoryError(f"synthetic {candidate}")

    if candidate == "receipt":
        monkeypatch.setattr(BrokerOfferOwnershipReceiptV2, "__init__", fail)
    elif candidate == "state_lease":
        monkeypatch.setattr(StateGenerationLeaseV2, "__init__", fail)
    elif candidate == "owner_receipt":
        monkeypatch.setattr(PreparingLeaseOwnerReceiptV2, "__init__", fail)
    else:
        monkeypatch.setattr(g.store, "_publish_preparing_lease_v2", fail)
    result = await g.arbiter._run_offer_preparing_offline_v2(pending)
    assert result.outcome.status is DecisionStatus.BRAIN_FAILED
    assert getattr(g.store, "_state_generation_lease_v2", None) is None
    assert getattr(g.store, "_preparing_lease_owner_receipt_v2", None) is None
    assert port._composition.flow_state in {"IDLE", "OFFER_CLEANUP_UNKNOWN"}
    assert not g.backend.calls and not g.brain.calls


@pytest.mark.anyio
async def test_enqueue_failure_atomically_recovers_lane_future_and_owner_slots(
    graph, monkeypatch,
):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    original = g.session._send
    original_register = module._register_initial_offer_source_v2
    captured = []

    def capture_source(*args, **kwargs):
        source = original_register(*args, **kwargs)
        captured.append((source, source.exact_initial_ticket))
        return source

    async def fail_enqueue(operation, **fields):
        if operation == "ENQUEUE":
            raise ConnectionError("synthetic enqueue failure")
        return await original(operation, **fields)

    monkeypatch.setattr(g.session, "_send", fail_enqueue)
    monkeypatch.setattr(module, "_register_initial_offer_source_v2", capture_source)
    result = await g.arbiter._run_offer_preparing_offline_v2(pending)
    assert result.outcome.status is DecisionStatus.BRAIN_FAILED
    assert not g.session._results and not g.session._control_lanes
    assert port._composition.flow_state == "IDLE"
    assert port._composition.initial_invocation_slot == "EMPTY"
    assert port._composition.active_offer_source_or_null is None
    assert port._composition.active_initial_ticket_or_null is None
    assert getattr(g.store, "_state_generation_lease_v2", None) is None
    source, ticket = captured[0]
    assert source.state == "RETIRED_CLEAN"
    assert source.exact_composition is None and source.exact_request is None
    assert source.exact_dispatch_deadline is None and source.exact_initial_ticket is None
    assert ticket.state == "RETIRED_CLEAN"
    assert ticket.exact_pending_invocation is None and ticket.exact_request is None


@pytest.mark.anyio
async def test_expired_offer_returns_deadline_terminal_and_clean_refs(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    pending.dispatch_deadline = replace(
        pending.dispatch_deadline, not_after_monotonic=time.monotonic() + 0.15)
    ticket = await issue_actual(g, port, pending)
    source = _register_initial_offer_source_v2(port, ticket)
    result, receipt = await _owned_acquire_initial_offer_v2(source)
    g.world._current_deadline = replace(
        g.world._current_deadline,
        local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic,
    )
    await asyncio.sleep(0.16)
    with pytest.raises(OfferCompositionError, match="DEADLINE_EXPIRED"):
        _publish_preparing_from_offer_v2(port, ticket, result, receipt)
    terminal = await _cleanup_offered_source_v2(
        source, ticket.terminal_candidates.deadline_suppressed)
    assert terminal.outcome.status is DecisionStatus.DEADLINE_SUPPRESSED
    assert receipt.state == "RETIRED_CLEAN"
    assert receipt.exact_raw_offer_frame_object is None
    assert receipt.exact_client_lease is None
    assert receipt.issuer_capability is None
    assert source.state == "RETIRED_CLEAN"
    assert source.exact_request is None and source.exact_initial_ticket is None
    assert source.exact_offer_candidates_or_null is None
    assert ticket.state == "RETIRED_CLEAN"
    assert ticket.exact_pending_invocation is None and ticket.exact_request is None
    assert port._composition.flow_state == "IDLE"
    assert not g.session._control_lanes


@pytest.mark.anyio
async def test_owner_stop_after_preparing_preserves_placeholder_and_marks_held(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    g.world._current_deadline = replace(
        g.world._current_deadline,
        local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic,
    )
    await g.arbiter._run_offer_preparing_offline_v2(pending)
    snapshot = g.store._state_generation_lease_v2
    owner = g.store._preparing_lease_owner_receipt_v2
    await g.arbiter.stop()
    assert port._composition.lifecycle == "RETIRED"
    assert port._composition.flow_state == "PREPARING_TERMINAL_HELD"
    assert port._composition.initial_invocation_slot == "PREPARING_HELD"
    assert port._composition.active_offer_source_or_null.state == "TERMINAL_HELD"
    assert g.store._state_generation_lease_v2 is snapshot
    assert g.store._preparing_lease_owner_receipt_v2 is owner


@pytest.mark.anyio
async def test_pending_notification_failure_after_preparing_holds_owner(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)

    class RaisingPendingFuture(asyncio.Future):
        def set_result(self, value):
            super().set_result(value)
            raise RuntimeError("synthetic pending notification failure")

    pending.result = RaisingPendingFuture()
    g.world._current_deadline = replace(
        g.world._current_deadline,
        local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic,
    )
    task = asyncio.create_task(g.arbiter._run_offer_preparing_offline_v2(pending))
    for _ in range(100):
        if port._composition.flow_state == "PREPARING_TERMINAL_HELD":
            break
        await asyncio.sleep(0)
    assert port._composition.flow_state == "PREPARING_TERMINAL_HELD"
    assert port._composition.active_offer_source_or_null.state == "TERMINAL_HELD"
    assert g.store._state_generation_lease_v2.status == "PREPARING"
    assert g.arbiter._active is pending and g.arbiter._admission_driver is task
    assert not pending.execution_complete.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert g.store._state_generation_lease_v2.status == "PREPARING"


@pytest.mark.anyio
async def test_retire_consumed_source_moves_to_unknown_hold(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    ticket = await issue_actual(g, port, pending)
    source = _register_initial_offer_source_v2(port, ticket)
    await g.session.aclose()
    assert port._composition.lifecycle == "RETIRED"
    assert port._composition.flow_state == "OFFER_CLEANUP_UNKNOWN"
    assert port._composition.initial_invocation_slot == "CLEANUP_UNKNOWN"
    assert ticket.state == "RETIRED_UNKNOWN"
    assert source.state == "CLEANUP_UNKNOWN"
    assert getattr(g.store, "_state_generation_lease_v2", None) is None


@pytest.mark.anyio
async def test_cleanup_session_matrix_foreign_field_forces_unknown_hold(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    ticket = await issue_actual(g, port, pending)
    source = _register_initial_offer_source_v2(port, ticket)
    _result, _receipt = await _owned_acquire_initial_offer_v2(source)
    invocation_id = ticket.exact_request.invocation_id
    g.session._call_ordinals[invocation_id] = 17
    terminal = await _cleanup_offered_source_v2(
        source, ticket.terminal_candidates.stale)
    assert terminal is ticket.terminal_candidates.cleanup_unknown
    assert source.state == "CLEANUP_UNKNOWN"
    assert source.exact_request is ticket.exact_request
    assert port._composition.flow_state == "OFFER_CLEANUP_UNKNOWN"
    assert port._composition.initial_invocation_slot == "CLEANUP_UNKNOWN"


@pytest.mark.anyio
async def test_session_close_during_acquire_cannot_overwrite_retired_unknown(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    ticket = await issue_actual(g, port, pending)
    source = _register_initial_offer_source_v2(port, ticket)
    task = asyncio.create_task(_owned_acquire_initial_offer_v2(source))
    for _ in range(100):
        if source.state == "ACQUIRE_REGISTERED":
            break
        await asyncio.sleep(0)
    assert source.state == "ACQUIRE_REGISTERED"
    await g.session.aclose()
    with pytest.raises(OfferCompositionError, match="OFFER_OWNER_RETIRED"):
        await task
    assert port._composition.lifecycle == "RETIRED"
    assert port._composition.flow_state == "OFFER_CLEANUP_UNKNOWN"
    assert source.state == "CLEANUP_UNKNOWN"
    assert ticket.state == "RETIRED_UNKNOWN"


@pytest.mark.anyio
async def test_same_message_from_foreign_exception_is_internal_failure(graph, monkeypatch):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    original = module._publish_preparing_from_offer_v2

    def wrong_type(*args, **kwargs):
        raise ValueError("PREPARING_DEADLINE_EXPIRED")

    monkeypatch.setattr(module, "_publish_preparing_from_offer_v2", wrong_type)
    result = await g.arbiter._run_offer_preparing_offline_v2(pending)
    monkeypatch.setattr(module, "_publish_preparing_from_offer_v2", original)
    assert result.outcome.status is DecisionStatus.BRAIN_FAILED
    assert result.outcome.error_type == "OfferPreparingFailureV2"


@pytest.mark.anyio
async def test_post_publish_identity_mismatch_holds_complete_placeholder(graph, monkeypatch):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    g.world._current_deadline = replace(
        g.world._current_deadline,
        local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic,
    )
    original = g.store._publish_preparing_lease_v2

    def corrupt_after_publish(*args, **kwargs):
        original(*args, **kwargs)
        g.store._preparing_lease_bundle_v2 = (object(), object())

    monkeypatch.setattr(g.store, "_publish_preparing_lease_v2", corrupt_after_publish)
    task = asyncio.create_task(g.arbiter._run_offer_preparing_offline_v2(pending))
    for _ in range(100):
        if port._composition.flow_state == "PREPARING_TERMINAL_HELD":
            break
        await asyncio.sleep(0)
    assert port._composition.flow_state == "PREPARING_TERMINAL_HELD"
    assert port._composition.active_offer_source_or_null.state == "TERMINAL_HELD"
    assert g.arbiter._active is pending
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
@pytest.mark.parametrize(
    "cancel_status,clean",
    [
        (AdmissionStatus.CANCELLED, True),
        (AdmissionStatus.EXPIRED, True),
        (AdmissionStatus.POISONED, True),
        (AdmissionStatus.UNAVAILABLE, False),
        (AdmissionStatus.OVERLOADED, False),
        (AdmissionStatus.REPLACED, False),
    ],
)
async def test_cancel_terminal_matrix_is_closed(graph, monkeypatch, cancel_status, clean):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    ticket = await issue_actual(g, port, pending)
    source = _register_initial_offer_source_v2(port, ticket)
    _result, receipt = await _owned_acquire_initial_offer_v2(source)
    lane = receipt.exact_control_lane
    lease = receipt.exact_client_lease
    invocation_id = receipt.invocation_id

    async def cancel(_lane, operation):
        assert _lane is lane and operation == "CANCEL"
        lane.disposition = cancel_status.value
        if clean:
            lane.lease = None
            lease._retired = True
            g.session._control_lanes.pop(invocation_id, None)
            g.session._terminal_controls[invocation_id] = cancel_status
        return cancel_status

    monkeypatch.setattr(g.session, "_ordinary_control", cancel)
    terminal = await _cleanup_offered_source_v2(
        source, ticket.terminal_candidates.stale)
    if clean:
        assert terminal.outcome.status is DecisionStatus.STALE
        assert port._composition.flow_state == "IDLE"
        assert source.exact_request is None and receipt.exact_client_lease is None
    else:
        assert terminal is ticket.terminal_candidates.cleanup_unknown
        assert port._composition.flow_state == "OFFER_CLEANUP_UNKNOWN"
        assert source.exact_request is ticket.exact_request


@pytest.mark.anyio
@pytest.mark.parametrize(
    "release_status,clean",
    [
        ("RELEASED", True),
        (AdmissionStatus.EXPIRED.value, True),
        (AdmissionStatus.POISONED.value, True),
        (AdmissionStatus.UNAVAILABLE.value, False),
    ],
)
async def test_granted_release_terminal_matrix_is_closed(
    graph, monkeypatch, release_status, clean,
):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    ticket = await issue_actual(g, port, pending)
    source = _register_initial_offer_source_v2(port, ticket)
    _result, receipt = await _owned_acquire_initial_offer_v2(source)
    lane = receipt.exact_control_lane
    lease = receipt.exact_client_lease
    invocation_id = receipt.invocation_id

    async def grant(_lane, operation):
        assert _lane is lane and operation == "CANCEL"
        lane.disposition = AdmissionStatus.GRANTED.value
        lease._claimed = True
        g.session._claimed_invocation = invocation_id
        g.session._call_ordinals[invocation_id] = 0
        g.session._request_ids[invocation_id] = set()
        return AdmissionStatus.GRANTED

    async def release():
        lane.disposition = release_status
        lane.lease = None
        lease._claimed = False
        lease._released = True
        lease._retired = release_status == AdmissionStatus.EXPIRED.value
        g.session._claimed_invocation = None
        g.session._call_ordinals.pop(invocation_id, None)
        g.session._request_ids.pop(invocation_id, None)
        g.session._control_lanes.pop(invocation_id, None)
        if release_status == "RELEASED":
            g.session._terminal_controls.pop(invocation_id, None)
        elif clean:
            g.session._terminal_controls[invocation_id] = AdmissionStatus(release_status)

    monkeypatch.setattr(g.session, "_ordinary_control", grant)
    monkeypatch.setattr(lease, "release", release)
    terminal = await _cleanup_offered_source_v2(
        source, ticket.terminal_candidates.stale)
    if clean:
        assert terminal.outcome.status is DecisionStatus.STALE
        assert port._composition.flow_state == "IDLE"
    else:
        assert terminal is ticket.terminal_candidates.cleanup_unknown
        assert port._composition.flow_state == "OFFER_CLEANUP_UNKNOWN"


@pytest.mark.anyio
async def test_driver_cancellation_ends_in_exact_clean_or_unknown_row(graph, monkeypatch):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    entered = asyncio.Event()

    async def blocked_send(operation, **fields):
        if operation == "ENQUEUE":
            entered.set()
            await asyncio.Future()
        raise ConnectionError("synthetic control transport loss")

    monkeypatch.setattr(g.session, "_send", blocked_send)
    task = asyncio.create_task(g.arbiter._run_offer_preparing_offline_v2(pending))
    await entered.wait()
    task.cancel()
    try:
        returned = await task
    except asyncio.CancelledError:
        returned = None
    flow = port._composition.flow_state
    if flow == "IDLE":
        assert pending.result.done()
        assert pending.result.result().outcome.status is DecisionStatus.CANCELLED
        assert pending.execution_complete.is_set()
        assert g.arbiter._admission_state == "IDLE"
        assert g.arbiter._active is None and g.arbiter._admission_driver is None
        assert not g.session._results and not g.session._control_lanes
    else:
        assert flow == "OFFER_CLEANUP_UNKNOWN"
        assert g.arbiter._admission_state == "V2_CLEANUP_UNKNOWN"
        assert port._composition.active_offer_source_or_null is not None
        if returned is None:
            assert not pending.execution_complete.is_set()
        else:
            assert returned.outcome.error_type == "OfferCleanupUnknownV2"
            assert pending.result.result() is returned
            assert pending.execution_complete.is_set()


@pytest.mark.anyio
async def test_retained_raw_offer_mutation_is_stale_and_never_publishes(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    ticket = await issue_actual(g, port, pending)
    source = _register_initial_offer_source_v2(port, ticket)
    result, receipt = await _owned_acquire_initial_offer_v2(source)
    g.world._current_deadline = replace(
        g.world._current_deadline,
        local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic,
    )
    receipt.exact_raw_offer_frame_object["queue_wait_microseconds"] += 1
    with pytest.raises(OfferCompositionError, match="OWNER_MISMATCH"):
        _publish_preparing_from_offer_v2(port, ticket, result, receipt)
    terminal = await _cleanup_offered_source_v2(
        source, ticket.terminal_candidates.stale)
    assert terminal.outcome.status is DecisionStatus.STALE
    assert getattr(g.store, "_state_generation_lease_v2", None) is None


@pytest.mark.anyio
@pytest.mark.parametrize("status", [
    AdmissionStatus.REPLACED,
    AdmissionStatus.EXPIRED,
    AdmissionStatus.OVERLOADED,
    AdmissionStatus.UNAVAILABLE,
    AdmissionStatus.CANCELLED,
    AdmissionStatus.POISONED,
])
async def test_each_pre_offer_terminal_uses_closed_matrix_and_clears_refs(
    graph, monkeypatch, status,
):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    ticket = await issue_actual(g, port, pending)
    source = _register_initial_offer_source_v2(port, ticket)
    invocation_id = ticket.exact_request.invocation_id

    async def terminal_send(operation, **fields):
        assert operation == "ENQUEUE"
        g.session._route_frame({
            "type": "ACK", "protocol": GENERATION_IPC_PROTOCOL,
            "invocation_id": invocation_id, "status": status.value,
        })

    monkeypatch.setattr(g.session, "_send", terminal_send)
    result, receipt = await _owned_acquire_initial_offer_v2(source)

    assert result.status is status and receipt is None
    assert source.state == "RETIRED_CLEAN" and ticket.state == "RETIRED_CLEAN"
    assert port._composition.flow_state == "IDLE"
    assert port._composition.initial_invocation_slot == "EMPTY"
    assert port._composition.active_offer_source_or_null is None
    assert port._composition.active_initial_ticket_or_null is None
    assert g.session._terminal_controls.get(invocation_id) is status
    assert invocation_id not in g.session._results
    assert invocation_id not in g.session._control_lanes
    assert invocation_id not in g.session._call_ordinals
    assert invocation_id not in g.session._request_ids
    assert g.session._claimed_invocation is None
    assert g.session._activated_invocation is None
    for field in (
        "exact_composition", "exact_caller_port", "exact_arbiter", "exact_request",
        "exact_dispatch_deadline", "exact_control_lane_or_null",
        "exact_result_future_or_null", "exact_client_lease_or_null",
        "exact_offer_receipt_or_null", "exact_offer_candidates_or_null",
        "exact_initial_ticket",
    ):
        assert getattr(source, field) is None
    for field in (
        "exact_composition", "exact_caller_port", "exact_arbiter",
        "exact_pending_invocation", "exact_dispatch_deadline",
        "exact_admission_driver_task", "exact_initial_slot", "exact_request",
        "exact_offer_task_or_null", "issuer_capability",
    ):
        assert getattr(ticket, field) is None


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", [
    "results", "lane", "disposition", "callers", "tombstone",
    "call_ordinal", "request_ids", "claimed", "activated",
])
async def test_pre_offer_terminal_matrix_mutation_holds_unknown(
    graph, monkeypatch, mutation,
):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    ticket = await issue_actual(g, port, pending)
    source = _register_initial_offer_source_v2(port, ticket)
    invocation_id = ticket.exact_request.invocation_id
    original_cleanup = g.session._cleanup_control_lane

    def corrupt_after_cleanup(lane):
        original_cleanup(lane)
        if mutation == "results":
            g.session._results[invocation_id] = source.exact_result_future_or_null
        elif mutation == "lane":
            g.session._control_lanes[invocation_id] = lane
        elif mutation == "disposition":
            lane.disposition = AdmissionStatus.EXPIRED.value
        elif mutation == "callers":
            lane.callers = 1
        elif mutation == "tombstone":
            g.session._terminal_controls.pop(invocation_id, None)
        elif mutation == "call_ordinal":
            g.session._call_ordinals[invocation_id] = 1
        elif mutation == "request_ids":
            g.session._request_ids[invocation_id] = {"foreign"}
        elif mutation == "claimed":
            g.session._claimed_invocation = invocation_id
        else:
            g.session._activated_invocation = invocation_id

    async def terminal_send(operation, **fields):
        assert operation == "ENQUEUE"
        g.session._route_frame({
            "type": "ACK", "protocol": GENERATION_IPC_PROTOCOL,
            "invocation_id": invocation_id,
            "status": AdmissionStatus.CANCELLED.value,
        })

    monkeypatch.setattr(g.session, "_cleanup_control_lane", corrupt_after_cleanup)
    monkeypatch.setattr(g.session, "_send", terminal_send)
    with pytest.raises(OfferCompositionError, match="PRE_OFFER_CLEANUP_UNCONFIRMED"):
        await _owned_acquire_initial_offer_v2(source)
    assert source.state == "CLEANUP_UNKNOWN"
    assert ticket.state == "RETIRED_UNKNOWN"
    assert port._composition.flow_state == "OFFER_CLEANUP_UNKNOWN"
    assert port._composition.initial_invocation_slot == "CLEANUP_UNKNOWN"
    assert port._composition.active_offer_source_or_null is source
    assert source.exact_composition is port._composition
    assert source.exact_initial_ticket is ticket


@pytest.mark.anyio
@pytest.mark.parametrize("owner_name", ["session", "store", "arbiter"])
@pytest.mark.parametrize("send_stage", ["encode", "write", "drain"])
async def test_owner_retire_precedes_each_send_failure_and_preserves_unknown(
    graph, monkeypatch, owner_name, send_stage,
):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    ticket = await issue_actual(g, port, pending)
    source = _register_initial_offer_source_v2(port, ticket)

    async def retire_then_fail(operation, **fields):
        assert operation == "ENQUEUE"
        _retire_offer_composition_v2(getattr(g, owner_name))
        raise ConnectionError(f"synthetic {send_stage} failure")

    monkeypatch.setattr(g.session, "_send", retire_then_fail)
    with pytest.raises(ConnectionError, match=send_stage):
        await _owned_acquire_initial_offer_v2(source)
    assert port._composition.lifecycle == "RETIRED"
    assert port._composition.flow_state == "OFFER_CLEANUP_UNKNOWN"
    assert port._composition.initial_invocation_slot == "CLEANUP_UNKNOWN"
    assert port._composition.active_offer_source_or_null is source
    assert port._composition.active_initial_ticket_or_null is ticket
    assert source.state == "CLEANUP_UNKNOWN"
    assert ticket.state == "RETIRED_UNKNOWN"
    assert source.exact_composition is port._composition
    assert source.exact_initial_ticket is ticket
    assert ticket.exact_pending_invocation is pending


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["_claimed_invocation", "_activated_invocation"])
@pytest.mark.parametrize("point", ["before", "after"])
async def test_pre_offer_foreign_owner_is_never_clean(graph, monkeypatch, field, point):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    ticket = await issue_actual(g, port, pending)
    source = _register_initial_offer_source_v2(port, ticket)
    invocation_id = ticket.exact_request.invocation_id
    original_cleanup = g.session._cleanup_control_lane

    def corrupt_after_cleanup(lane):
        original_cleanup(lane)
        setattr(g.session, field, "foreign-owner")

    async def terminal_send(operation, **fields):
        assert operation == "ENQUEUE"
        g.session._route_frame({
            "type": "ACK", "protocol": GENERATION_IPC_PROTOCOL,
            "invocation_id": invocation_id,
            "status": AdmissionStatus.CANCELLED.value,
        })
        if point == "before":
            setattr(g.session, field, "foreign-owner")

    if point == "after":
        monkeypatch.setattr(g.session, "_cleanup_control_lane", corrupt_after_cleanup)
    monkeypatch.setattr(g.session, "_send", terminal_send)
    with pytest.raises(OfferCompositionError, match="PRE_OFFER_CLEANUP_UNCONFIRMED"):
        await _owned_acquire_initial_offer_v2(source)
    assert getattr(g.session, field) == "foreign-owner"
    assert source.state == "CLEANUP_UNKNOWN"
    assert ticket.state == "RETIRED_UNKNOWN"
    assert port._composition.flow_state == "OFFER_CLEANUP_UNKNOWN"
    assert port._composition.initial_invocation_slot == "CLEANUP_UNKNOWN"
    assert port._composition.active_offer_source_or_null is source
    assert source.exact_initial_ticket is ticket
    assert ticket.exact_pending_invocation is pending
