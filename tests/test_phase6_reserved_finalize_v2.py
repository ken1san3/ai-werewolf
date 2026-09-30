from __future__ import annotations

import pickle
from dataclasses import replace

import pytest

from ai_client.discussion.capture_v2 import GenerationCaptureV2, StateGenerationLeaseV2
from ai_client.discussion.reserved_finalize_v2 import (
    ReservedCaptureOwnerReceiptV2, ReservedFinalizeError,
    _finalize_preparing_owned_v2,
)
from tests.test_phase6_initial_ticket_v2 import make_pending
from tests.test_phase6_offer_composition_v2 import bind, graph


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_actual_driver_finalizes_exact_reserved_capture(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    g.world._current_deadline = replace(
        g.world._current_deadline,
        local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic)

    await g.arbiter._run_offer_preparing_offline_v2(pending)

    bundle = g.store._preparing_lease_bundle_v2
    lease, capture, receipt = bundle
    assert type(lease) is StateGenerationLeaseV2
    assert type(capture) is GenerationCaptureV2
    assert type(receipt) is ReservedCaptureOwnerReceiptV2
    assert lease.status == "RESERVED" and lease.lease_revision == 1
    assert lease.capture_id == capture.capture_id
    assert lease.input_sha256 == capture.input_sha256
    assert lease.catalog_sha256 == capture.catalog_sha256
    assert lease.option_catalog_sha256 == capture.option_catalog_sha256
    assert lease.profile_bundle_sha256 == capture.profile_bundle_sha256
    assert receipt.exact_lease is lease and receipt.exact_capture is capture
    assert receipt.exact_runtime_authorities == (
        receipt.exact_preparing_receipt.exact_prepared_material.public_channel_authorities)
    assert not g.backend.calls and not g.brain.calls
    assert g.session._claimed_invocation is None
    assert g.session._activated_invocation is None
    with pytest.raises(TypeError):
        pickle.dumps(receipt)


@pytest.mark.anyio
async def test_reserved_finalizer_is_one_shot_and_requires_exact_port(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    g.world._current_deadline = replace(
        g.world._current_deadline,
        local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic)
    await g.arbiter._run_offer_preparing_offline_v2(pending)
    receipt = g.store._preparing_lease_owner_receipt_v2
    with pytest.raises(ReservedFinalizeError):
        _finalize_preparing_owned_v2(object(), receipt)
    with pytest.raises(ReservedFinalizeError):
        _finalize_preparing_owned_v2(port, receipt)
    assert g.store._state_generation_lease_v2.status == "RESERVED"
    assert not g.backend.calls and not g.brain.calls


@pytest.mark.anyio
async def test_final_source_read_rejects_foreign_hidden_receipt(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    g.world._current_deadline = replace(
        g.world._current_deadline,
        local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic)
    await g.arbiter._run_offer_preparing_offline_v2(pending)
    preparing = g.store._preparing_lease_owner_receipt_v2.exact_preparing_receipt
    hidden = preparing.exact_hidden_owner_receipt
    with pytest.raises(Exception, match="FINAL_SOURCE_OWNER_MISMATCH"):
        g.bridge._read_final_capture_source_v2(
            preparing.exact_prepared_material, (hidden[0], hidden[1], hidden[2]))


async def preparing_graph(g):
    import asyncio
    from tests.test_phase6_initial_ticket_v2 import issue_actual
    from ai_client.discussion.offer_composition_v2 import (
        _register_initial_offer_source_v2, _owned_acquire_initial_offer_v2,
        _publish_preparing_from_offer_v2,
    )
    port = bind(g)
    pending = make_pending(g)
    g.world._current_deadline = replace(g.world._current_deadline,
        local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic)
    ticket = await issue_actual(g, port, pending)
    source = _register_initial_offer_source_v2(port, ticket)
    task = asyncio.create_task(_owned_acquire_initial_offer_v2(source))
    object.__setattr__(ticket, "exact_offer_task_or_null", task)
    g.arbiter._admission_wait_task = task
    result, receipt = await task
    g.arbiter._admission_wait_task = None
    g.arbiter._admission_state = "V2_PREPARING"
    _publish_preparing_from_offer_v2(port, ticket, result, receipt)
    return port, pending, g.store._preparing_lease_owner_receipt_v2


@pytest.mark.anyio
@pytest.mark.parametrize("point", [
    "_compile_catalog", "canonical_sha256_v2", "build_generation_v2_schema",
    "make_capture_v2", "ReservedBundleIdentityV2", "ReservedCaptureOwnerReceiptV2",
    "FinalizePostcheckObservationV2", "ReservedInvalidatedOwnerReceiptV2",
    "ReservedFinalizeAbortBundleV2", "_notification_slot",
])
async def test_build_fault_uses_prebuilt_invalid_bundle_and_exact_result(graph, monkeypatch, point):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    expected = owner.abort_bundle
    def fail(*args, **kwargs):
        raise MemoryError()
    monkeypatch.setattr(m, point, fail)
    assert m._finalize_preparing_owned_v2(port, owner) is None
    assert graph.store._preparing_lease_bundle_v2 is expected.candidate_cell.exact_tuple
    assert expected.invalidated_lease.lease_revision == 1
    assert expected.invalidated_lease.capture_id is None
    assert expected.abort_selection.selected_class_or_null == "INTERNAL_BUILD"
    result = m._notify_finalized_owned_v2(owner)
    assert result is owner.exact_initial_ticket.terminal_candidates.internal_failure
    assert pending.result.result() is result
    assert expected.abort_selection.state == "DELIVERED"
    assert expected.abort_notification_observation.state == "EMPTY"
    assert not graph.backend.calls and not graph.brain.calls


@pytest.mark.anyio
@pytest.mark.parametrize("failure,alias", [
    ("cancel", "preparing_complete"), ("deadline", "deadline_suppressed"),
    ("revision", "stale"), ("fact", "stale"), ("epoch", "stale"),
    ("active", "stale"), ("driver", "stale"), ("deadline_copy", "stale"),
    ("lane_callers", "stale"), ("lane_disposition", "stale"),
    ("claimed", "stale"), ("released", "stale"), ("session_registry", "stale"),
])
async def test_pre_cas_mutations_abort_without_authority(graph, failure, alias):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    if failure == "cancel": pending.cancel_requested = True
    elif failure == "deadline": port._composition.exact_clock_callable = lambda: owner.expires_at_monotonic_us / 1e6
    elif failure == "revision": graph.store._revision += 1
    elif failure == "fact": graph.store._fact_revision += 1
    elif failure == "epoch": graph.store._epoch += 1
    elif failure == "active": graph.arbiter._active = object()
    elif failure == "driver": graph.arbiter._admission_driver = None
    elif failure == "deadline_copy": pending.dispatch_deadline = replace(pending.dispatch_deadline)
    elif failure == "lane_callers": owner.exact_control_lane.callers = 1
    elif failure == "lane_disposition": owner.exact_control_lane.disposition = "foreign"
    elif failure == "claimed": owner.exact_client_lease._claimed = True
    elif failure == "released": owner.exact_client_lease._released = True
    elif failure == "session_registry": graph.session._control_lanes.pop(owner.exact_request.invocation_id)
    assert m._finalize_preparing_owned_v2(port, owner) is None
    result = m._notify_finalized_owned_v2(owner)
    if failure in {"active", "driver", "deadline_copy", "lane_callers", "lane_disposition", "claimed", "released", "session_registry"}:
        assert result is None
        assert graph.store._lease_bundle_cell_v2 is owner.cell_weakref()
        assert graph.store._preparing_lease_bundle_v2[0].status == "PREPARING"
        assert owner.abort_bundle.abort_selection.state == "BOUND_EMPTY"
        assert not pending.result.done()
    else:
        assert result is getattr(owner.exact_initial_ticket.terminal_candidates, alias)
        assert graph.store._state_generation_lease_v2.status == "INVALIDATED"
    assert not graph.backend.calls and not graph.brain.calls


@pytest.mark.anyio
async def test_conflicting_tuple_is_never_replaced_or_notified(graph):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    old_cell = owner.cell_weakref()
    foreign = m.LeaseBundleCellV2(m._ISSUER, stage=old_cell.stage,
        bundle_identity=old_cell.bundle_identity, exact_tuple=tuple(list(old_cell.exact_tuple)))
    graph.store._lease_bundle_cell_v2 = foreign
    assert m._finalize_preparing_owned_v2(port, owner) is None
    assert graph.store._lease_bundle_cell_v2 is foreign
    assert owner.abort_bundle.conflict_observation.observed_shape_or_null == "FOREIGN"
    assert port._composition.flow_state == "FINALIZE_CONFLICT_HELD"
    assert owner.abort_bundle.abort_selection.exact_selected_result_or_null is None
    assert m._notify_finalized_owned_v2(owner) is None
    assert not pending.result.done()


@pytest.mark.anyio
@pytest.mark.parametrize("reserved", [False, True])
async def test_retire_publishes_exact_preallocated_revision_once(graph, reserved):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    if reserved:
        bundle = m._finalize_preparing_owned_v2(port, owner)
        abort = bundle[2].abort_bundle
    else:
        abort = owner.abort_bundle
    assert m._retire_finalized_owned_v2(port._composition)
    assert graph.store._preparing_lease_bundle_v2 is abort.candidate_cell.exact_tuple
    assert abort.invalidated_lease.lease_revision == (2 if reserved else 1)
    m._retire_finalized_owned_v2(port._composition)
    assert graph.store._preparing_lease_bundle_v2 is abort.candidate_cell.exact_tuple
    assert not pending.result.done()
    assert port._composition.flow_state == "FINALIZE_RETIRED_HELD"
    assert not owner.exact_client_lease._released


@pytest.mark.anyio
@pytest.mark.parametrize("reserved,visible", [(False, False), (False, True), (True, False), (True, True)])
async def test_notification_exception_preserves_exact_bundle_and_observation(graph, monkeypatch, reserved, visible):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    receipt = None
    if reserved:
        receipt = m._finalize_preparing_owned_v2(port, owner)[2]
        slot = receipt.notification_observation
    else:
        m._abort_preparing_owned_v2(owner, "INTERNAL_BUILD")
        slot = owner.abort_bundle.abort_notification_observation
    stored = graph.store._preparing_lease_bundle_v2
    calls = []
    def fail(p, result):
        calls.append(result)
        if visible: p.result.set_result(result)
        raise RuntimeError("synthetic")
    monkeypatch.setattr(graph.arbiter, "_finish", fail)
    assert m._notify_finalized_owned_v2(owner, receipt) is None
    assert graph.store._preparing_lease_bundle_v2 is stored
    assert slot.state == "RECORDED"
    assert slot.exact_observation.future_done is visible
    assert slot.exact_observation.exact_result_visible is (slot.exact_expected_result if visible else None)
    assert port._composition.flow_state == ("RESERVED_NOTIFICATION_UNKNOWN" if reserved else "FINALIZE_NOTIFICATION_UNKNOWN")
    assert m._notify_finalized_owned_v2(owner, receipt) is None
    assert len(calls) == 1


@pytest.mark.anyio
async def test_abort_selection_is_opaque_and_one_shot(graph):
    import copy
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    selection = owner.abort_bundle.abort_selection
    for copier in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError): copier(selection)
    m._select_abort(owner, "INTERNAL_BUILD")
    with pytest.raises(m.ReservedFinalizeError): m._select_abort(owner, "STALE_FRESHNESS")
    object.__setattr__(selection, "exact_selected_result_or_null", owner.exact_initial_ticket.terminal_candidates.stale)
    with pytest.raises(m.ReservedFinalizeError):
        graph.store._cas_invalidated_lease_v2(owner.cell_weakref(), owner, owner.abort_bundle)
    assert graph.store._preparing_lease_bundle_v2 is owner.cell_weakref().exact_tuple


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["_revision", "_fact_revision", "_epoch"])
async def test_between_source_reads_freshness_change_invalidates(graph, monkeypatch, field):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    original = m._compile_catalog
    def changed(source):
        result = original(source)
        setattr(graph.store, field, getattr(graph.store, field) + 1)
        return result
    monkeypatch.setattr(m, "_compile_catalog", changed)
    m._finalize_preparing_owned_v2(port, owner)
    assert graph.store._state_generation_lease_v2.status == "INVALIDATED"
    assert m._notify_finalized_owned_v2(owner) is owner.exact_initial_ticket.terminal_candidates.stale


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["active_initial_ticket_or_null", "active_offer_source_or_null"])
async def test_postpublish_backlink_mismatch_holds_without_notification(graph, monkeypatch, field):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    original = graph.store._cas_reserved_lease_v2
    def publish(*args):
        original(*args)
        setattr(port._composition, field, object())
    monkeypatch.setattr(graph.store, "_cas_reserved_lease_v2", publish)
    reserved = m._finalize_preparing_owned_v2(port, owner)
    assert reserved is graph.store._preparing_lease_bundle_v2
    assert port._composition.flow_state == "RESERVED_POSTCHECK_UNKNOWN"
    assert reserved[2].postcheck_observation.state == "RECORDED"
    assert m._notify_finalized_owned_v2(owner, reserved[2]) is None
    assert not pending.result.done()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["hash", "stage", "profile", "capture"])
async def test_candidate_mutation_never_publishes_reserved(graph, monkeypatch, failure):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    original = m._build_reserved_candidate_v2
    def changed(*args):
        cell = original(*args)
        bundle = cell.exact_tuple
        receipt = bundle[2]
        if failure == "hash":
            object.__setattr__(receipt, "catalog_binding", {"foreign": True})
        elif failure == "stage":
            object.__setattr__(receipt, "stage_schemas", ())
        elif failure == "profile":
            object.__setattr__(receipt, "exact_profile_bundle", replace(receipt.exact_profile_bundle))
        else:
            object.__setattr__(receipt, "exact_capture", replace(receipt.exact_capture))
        return cell
    monkeypatch.setattr(m, "_build_reserved_candidate_v2", changed)
    assert m._finalize_preparing_owned_v2(port, owner) is None
    assert graph.store._preparing_lease_bundle_v2 is owner.abort_bundle.candidate_cell.exact_tuple


@pytest.mark.anyio
async def test_equal_material_copy_cannot_borrow_origin(graph):
    from ai_client.discussion import authority_capture_bridge_v2 as bridge
    port, pending, owner = await preparing_graph(graph)
    original = owner.exact_prepared_material
    foreign = object.__new__(type(original))
    for name in ("_values", "_hidden_owner_receipt", "_origin_ref"):
        object.__setattr__(foreign, name, getattr(original, name))
    with pytest.raises(bridge.AuthorityCaptureBridgeError):
        graph.bridge._read_final_capture_source_v2(foreign, original._hidden_owner_receipt)
    assert graph.store._preparing_lease_bundle_v2 is owner.cell_weakref().exact_tuple


@pytest.mark.anyio
@pytest.mark.parametrize("point", ["PreparingBundleIdentityV2", "FinalizeTerminalMapV2", "FinalizeAbortSelectionV2", "FinalizeConflictObservationV2", "FinalizeNotificationObservationSlotV2", "PreCaptureInvalidatedOwnerReceiptV2", "PreCaptureFinalizeAbortBundleV2"])
async def test_abort_preallocation_failure_prevents_preparing_publish(graph, monkeypatch, point):
    from ai_client.discussion import reserved_finalize_v2 as m
    port = bind(graph)
    pending = make_pending(graph)
    graph.world._current_deadline = replace(graph.world._current_deadline,
        local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic)
    def fail(*args, **kwargs): raise MemoryError()
    monkeypatch.setattr(m, point, fail)
    result = await graph.arbiter._run_offer_preparing_offline_v2(pending)
    assert graph.store._preparing_lease_bundle_v2 is None
    assert result is port._composition.prebuilt_pre_ticket_abort_bundle.failure_result
    assert not graph.backend.calls and not graph.brain.calls


@pytest.mark.anyio
async def test_foreign_task_cannot_finalize_or_abort(graph):
    import asyncio
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    original = graph.store._preparing_lease_bundle_v2
    async def foreign():
        with pytest.raises(m.ReservedFinalizeError):
            m._finalize_preparing_owned_v2(port, owner)
    await asyncio.create_task(foreign())
    assert graph.store._preparing_lease_bundle_v2 is original
    assert owner.abort_bundle.state == "ARMED"
    assert not pending.result.done()


@pytest.mark.anyio
async def test_reserved_abort_conflict_never_overwrites_foreign_tuple(graph):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    reserved = m._finalize_preparing_owned_v2(port, owner)
    old_cell = graph.store._lease_bundle_cell_v2
    foreign = m.LeaseBundleCellV2(m._ISSUER, stage=old_cell.stage,
        bundle_identity=old_cell.bundle_identity, exact_tuple=tuple(list(reserved)))
    graph.store._lease_bundle_cell_v2 = foreign
    m._retire_finalized_owned_v2(port._composition)
    assert graph.store._lease_bundle_cell_v2 is foreign
    assert reserved[2].abort_bundle.state == "ARMED"
    assert port._composition.flow_state == "RESERVED"
    assert not pending.result.done()


@pytest.mark.anyio
async def test_success_retire_keeps_exact_capture_hashes_and_schema(graph):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    reserved = m._finalize_preparing_owned_v2(port, owner)
    assert m._notify_finalized_owned_v2(owner, reserved[2]) is owner.exact_initial_ticket.terminal_candidates.preparing_complete
    m._retire_finalized_owned_v2(port._composition)
    invalid = graph.store._preparing_lease_bundle_v2
    assert invalid[1] is reserved[1]
    for field in ("capture_id", "input_sha256", "catalog_sha256", "option_catalog_sha256", "recipient_proof_sha256", "profile_bundle_sha256", "stage_schema_sha256s"):
        assert getattr(invalid[0], field) == getattr(reserved[0], field)
    assert reserved[2].notification_observation.state == "EMPTY"


@pytest.mark.anyio
@pytest.mark.parametrize("read_number", [1, 2])
async def test_source_read_allocation_fault_uses_prebuilt_abort(graph, monkeypatch, read_number):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    original = m._read_source_v2
    calls = 0
    def read(value):
        nonlocal calls
        calls += 1
        if calls == read_number: raise MemoryError()
        return original(value)
    monkeypatch.setattr(m, "_read_source_v2", read)
    m._finalize_preparing_owned_v2(port, owner)
    assert calls == read_number
    assert graph.store._preparing_lease_bundle_v2 is owner.abort_bundle.candidate_cell.exact_tuple
    assert m._notify_finalized_owned_v2(owner) is owner.exact_initial_ticket.terminal_candidates.internal_failure


@pytest.mark.anyio
@pytest.mark.parametrize("reserved,field", [(False, "predecessor_identity"), (True, "predecessor_identity"), (False, "invalidated_lease"), (True, "invalidated_lease"), (True, "exact_capture")])
async def test_invalid_candidate_equal_copy_or_foreign_identity_rejected(graph, reserved, field):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    if reserved:
        active = m._finalize_preparing_owned_v2(port, owner)
        target = active[2]
    else:
        active = owner.cell_weakref().exact_tuple
        target = owner
    abort = target.abort_bundle
    before = getattr(abort, field)
    foreign = object() if field == "predecessor_identity" else replace(before)
    object.__setattr__(abort, field, foreign)
    with pytest.raises(m.ReservedFinalizeError):
        graph.store._cas_invalidated_lease_v2(target.cell_weakref(), target, abort)
    assert graph.store._preparing_lease_bundle_v2 is active
    assert not pending.result.done()
    object.__setattr__(abort, field, before)


@pytest.mark.anyio
async def test_partial_and_double_notification_observation_are_rejected(graph):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    m._abort_preparing_owned_v2(owner, "INTERNAL_BUILD")
    slot = owner.abort_bundle.abort_notification_observation
    object.__setattr__(slot.exact_observation, "future_done", False)
    with pytest.raises(m.ReservedFinalizeError): m._record_notification_failure(slot)
    assert slot.state == "EMPTY"
    object.__setattr__(slot.exact_observation, "future_done", None)
    m._record_notification_failure(slot)
    assert slot.state == "RECORDED"
    with pytest.raises(m.ReservedFinalizeError): m._record_notification_failure(slot)


@pytest.mark.anyio
async def test_cancel_has_priority_over_deadline_and_build_fault(graph, monkeypatch):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    pending.cancel_requested = True
    port._composition.exact_clock_callable = lambda: owner.expires_at_monotonic_us / 1e6
    m._finalize_preparing_owned_v2(port, owner)
    assert owner.abort_bundle.abort_selection.selected_class_or_null == "CANCEL_REQUESTED"
    assert m._notify_finalized_owned_v2(owner) is owner.exact_initial_ticket.terminal_candidates.preparing_complete


@pytest.mark.anyio
@pytest.mark.parametrize("reserved", [False, True])
async def test_cell_swap_releases_predecessor_without_strong_backlink(graph, reserved):
    import gc
    import weakref
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    predecessor = graph.store._lease_bundle_cell_v2
    predecessor_ref = weakref.ref(predecessor)
    if reserved:
        result = m._finalize_preparing_owned_v2(port, owner)
        assert owner.cell_weakref() is predecessor
        del predecessor
        gc.collect()
        assert predecessor_ref() is None
        predecessor = graph.store._lease_bundle_cell_v2
        predecessor_ref = weakref.ref(predecessor)
        receipt = result[2]
    else:
        receipt = owner
    m._retire_finalized_owned_v2(port._composition)
    del predecessor
    gc.collect()
    assert predecessor_ref() is None
    assert receipt.cell_weakref() is None
    assert graph.store._preparing_lease_bundle_v2 is graph.store._lease_bundle_cell_v2.exact_tuple


@pytest.mark.anyio
async def test_cell_identity_reuse_and_read_slot_assignment_rejected(graph):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    cell = graph.store._lease_bundle_cell_v2
    with pytest.raises(m.ReservedFinalizeError, match="IDENTITY_REUSED"):
        m._issue_bundle_cell_v2(graph.store, cell.stage, cell.bundle_identity, tuple(list(cell.exact_tuple)))
    with pytest.raises(AttributeError):
        graph.store._preparing_lease_bundle_v2 = tuple(list(cell.exact_tuple))
    with pytest.raises(TypeError):
        m.LeaseBundleCellV2(object(), stage=cell.stage, bundle_identity=cell.bundle_identity, exact_tuple=cell.exact_tuple)
    assert graph.store._lease_bundle_cell_v2 is cell


@pytest.mark.anyio
async def test_compiler_rejects_public_equal_source_envelope(graph):
    from types import SimpleNamespace
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    source = m._read_source_v2(owner)
    public = SimpleNamespace(**{name: getattr(source, name) for name in source.__slots__})
    with pytest.raises(m.ReservedFinalizeError): m._compile_catalog(public)
    assert graph.store._state_generation_lease_v2.status == "PREPARING"


@pytest.mark.anyio
async def test_reserved_nested_catalog_and_schema_are_immutable(graph):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    receipt = m._finalize_preparing_owned_v2(port, owner)[2]
    with pytest.raises(TypeError): receipt.catalog_binding["player_entries"][0]["short_id"] = "foreign"
    with pytest.raises(TypeError): receipt.stage_schemas[0][1]["type"] = "foreign"


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["INITIAL_CHAT", "PEER_CHAT", "PRE_VOTE", "CO_OPPORTUNITY", "ABILITY"])
async def test_all_five_triggers_actual_owner_driver_reserved(kind, monkeypatch):
    import time
    from types import SimpleNamespace
    from ai_client.brain.controller import BrainController
    from ai_client.brain.invocation import BrainInvocationArbiter
    from ai_client.discussion.context import DiscussionAbilityContext
    from ai_client.discussion.model import EvidenceRef, EvidenceRecordKind, EvidenceVisibility
    from ai_client.discussion.state import DiscussionViews
    from ai_client.llm.admission_broker import GenerationAdmissionBroker
    from ai_client.llm.admission_client import BrokerAdmissionSession
    from ai_client.llm.admission_types import AdmissionCredentials
    from tests import test_phase6_authority_capture_bridge_v2 as fixtures
    from tests.test_phase6_discussion_context import _rehash_envelope
    from tests.test_phase6_discussion_transaction import _AuditSink, _ControllerBrain
    from tests.test_phase5_generation_admission import _FakeBackend, _token
    from ai_client.discussion.capture_v2 import canonical_sha256_v2
    actions = {
        "INITIAL_CHAT": ({"type": "chat", "channel": "opaque-public"},),
        "PEER_CHAT": ({"type": "chat", "channel": "opaque-public"},),
        "PRE_VOTE": ({"type": "vote", "valid_targets": ["opaque-player"], "target_count": 1, "allows_abstain": True},),
        "CO_OPPORTUNITY": ({"type": "co_declare", "claimed_role_ids": ["opaque-role"]},),
        "ABILITY": ({"type": "ability", "ability_id": "opaque-ability", "description": None, "valid_targets": ["opaque-player"], "target_count": 1, "uses_remaining": None},),
    }[kind]
    if kind == "ABILITY":
        original_envelope = fixtures._envelope
        def envelope():
            value = original_envelope()
            value["manifest_material"]["content_pack"]["action_timings"]["day"] = {"id": "day", "name": "Day", "phases": ["day"]}
            value["manifest_material"]["content_pack"]["selectors"]["alive"] = {"id": "alive", "name": "Alive"}
            value["manifest_material"]["content_pack"]["roles"]["opaque-role"]["abilities"] = [{
                "id": "opaque-ability", "timing": "day", "available_from_night": 1,
                "priority": 1, "resolution": "single", "target": {"selector": "alive", "count": 1, "options": {}},
                "uses": {"per_night": None, "per_game": None}, "no_selection": "skip",
                "restrictions": [], "effects": [], "description": None}]
            value["context_payload"]["abilities"] = [{
                "ability_id": "opaque-ability", "timing": "day", "available_from_night": 1,
                "priority": 1, "resolution": "single", "target_selector": "alive", "target_count": 1,
                "uses_per_night": None, "uses_per_game": None, "no_selection": "skip", "effect_ids": []}]
            _rehash_envelope(value)
            return value
        monkeypatch.setattr(fixtures, "_envelope", envelope)
    history = ()
    if kind == "PEER_CHAT":
        original_sync = fixtures.sync_payload
        def sync(*args):
            value = original_sync(*args)
            value["players"].append({"player_id": "opaque-peer", "display_name": "Peer"})
            return value
        monkeypatch.setattr(fixtures, "sync_payload", sync)
        history = ({"type": "chat.message", "payload": {"channel": "opaque-public", "message": {"player_id": "opaque-peer", "display_name": "Peer", "message": "synthetic"}}},)
    values = await fixtures.composed_bridge(actions=actions, history=history)
    socket, nt, wt, network, world, store, capture, bridge = values
    backend = _FakeBackend()
    broker = GenerationAdmissionBroker({"offline-owner": _token(81)}, backend, fairness_seed="t533")
    session = None
    try:
        await broker.start()
        session = await BrokerAdmissionSession.connect(broker.ready.host, broker.ready.port, AdmissionCredentials("offline-owner", _token(81)))
        brain = _ControllerBrain(None)
        controller = BrainController(world=world, sender=network._network, brain=brain,
            discussion_state=store, discussion_audit=_AuditSink(), clock=time.monotonic)
        arbiter = BrainInvocationArbiter(controller=controller, admission=session, clock=time.monotonic)
        source_ref = None
        if kind == "PEER_CHAT":
            source_ref = next(event.source for event in capture.evidence if event.source.record_kind is EvidenceRecordKind.CHAT)
        trigger = replace(capture.trigger, kind=kind,
            owner="vote_ability" if kind in {"PRE_VOTE", "ABILITY"} else "reaction_chat", source=source_ref)
        capture = store.capture(DiscussionViews(world.snapshot(), world.history(), world.co_for_day(1), world.ability_results(), world.transport_observations()), trigger)
        g = SimpleNamespace(world=world, source=network, arbiter=arbiter, bridge=bridge)
        port = bind(g)
        pending = make_pending(g, owner=trigger.owner)
        world._current_deadline = replace(world._current_deadline, local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic)
        result = await arbiter._run_offer_preparing_offline_v2(pending)
        bundle = store._preparing_lease_bundle_v2
        assert bundle[0].status == "RESERVED"
        assert result is port._composition.active_initial_ticket_or_null.terminal_candidates.preparing_complete
        expected = {"INITIAL_CHAT": ("chat_plan", "message"), "PEER_CHAT": ("chat_plan", "message"), "PRE_VOTE": ("pre_vote",), "CO_OPPORTUNITY": ("co_opportunity",), "ABILITY": ("ability",)}[kind]
        assert tuple(stage.stage for stage in bundle[1].stage_schema_sha256s) == expected
        assert not backend.calls and not brain.calls
        assert session._claimed_invocation is None and session._activated_invocation is None
    finally:
        if session is not None: await session.aclose()
        await broker.aclose()
        await fixtures.close_composed(socket, nt, wt, network)


@pytest.mark.anyio
async def test_retire_wins_before_finalize_without_second_cas_or_notification(graph, monkeypatch):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    m._retire_finalized_owned_v2(port._composition)
    stored = graph.store._lease_bundle_cell_v2
    def forbidden(*args): raise AssertionError("second CAS")
    monkeypatch.setattr(graph.store, "_cas_invalidated_lease_v2", forbidden)
    assert m._finalize_preparing_owned_v2(port, owner) is None
    assert graph.store._lease_bundle_cell_v2 is stored
    assert not pending.result.done()


@pytest.mark.anyio
async def test_retire_after_reserved_cas_wins_without_finalizer_notification(graph, monkeypatch):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    original = m._postcheck_reserved_v2
    def retire_after(*args):
        original(*args)
        m._retire_finalized_owned_v2(port._composition)
    monkeypatch.setattr(m, "_postcheck_reserved_v2", retire_after)
    finalized = m._finalize_preparing_owned_v2(port, owner)
    assert m._notify_finalized_owned_v2(owner, finalized[2]) is None
    assert graph.store._state_generation_lease_v2.lease_revision == 2
    assert port._composition.flow_state == "FINALIZE_RETIRED_HELD"
    assert owner.abort_bundle.state == "RETIRED"
    assert not pending.result.done()


@pytest.mark.anyio
async def test_dead_expected_cell_fails_closed_and_cannot_retry(graph):
    import gc
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    graph.store._lease_bundle_cell_v2 = None
    gc.collect()
    assert owner.cell_weakref() is None
    assert m._finalize_preparing_owned_v2(port, owner) is None
    assert port._composition.flow_state == "FINALIZE_CONFLICT_HELD"
    with pytest.raises(m.ReservedFinalizeError): m._finalize_preparing_owned_v2(port, owner)
    assert graph.store._lease_bundle_cell_v2 is None
    assert not pending.result.done()


@pytest.mark.anyio
@pytest.mark.parametrize("occupied", ["foreign", "reserved", "invalidated", "unregistered"])
async def test_r1_initial_publish_never_overwrites_raw_occupied_slot(graph, occupied):
    from ai_client.discussion import reserved_finalize_v2 as m
    from ai_client.discussion.state import DiscussionStateError
    port, pending, owner = await preparing_graph(graph)
    preparing_cell = owner.cell_weakref()
    if occupied == "foreign":
        graph.store._lease_bundle_cell_v2 = object()
    elif occupied == "reserved":
        m._finalize_preparing_owned_v2(port, owner)
    elif occupied == "invalidated":
        m._abort_preparing_owned_v2(owner, "INTERNAL_BUILD")
    else:
        graph.store._lease_bundle_cell_v2 = m.LeaseBundleCellV2(m._ISSUER,
            stage="PREPARING", bundle_identity=preparing_cell.bundle_identity,
            exact_tuple=tuple(list(preparing_cell.exact_tuple)))
    current = graph.store._lease_bundle_cell_v2
    with pytest.raises(DiscussionStateError):
        graph.store._publish_preparing_lease_v2(port._composition,
            owner.exact_store_capture_tuple[0], preparing_cell.exact_tuple[0], owner, preparing_cell)
    assert graph.store._lease_bundle_cell_v2 is current
    assert not pending.result.done()


@pytest.mark.anyio
async def test_r1_foreign_raw_slot_blocks_driver_before_ticket_or_notification(graph):
    from ai_client.discussion.offer_composition_v2 import OfferCompositionError
    port = bind(graph)
    pending = make_pending(graph)
    foreign = object()
    graph.store._lease_bundle_cell_v2 = foreign
    with pytest.raises(OfferCompositionError):
        await graph.arbiter._run_offer_preparing_offline_v2(pending)
    assert graph.store._lease_bundle_cell_v2 is foreign
    assert port._composition.active_initial_ticket_or_null is None
    assert not pending.result.done() and graph.arbiter._active is None
    assert not graph.session._control_lanes and not graph.backend.calls and not graph.brain.calls


SESSION_REGISTRY_MUTATIONS = ["_results", "_acks", "_call_ordinals", "_request_ids",
    "_terminal_controls", "_attachments", "_generations"]


ROW_MUTATIONS = SESSION_REGISTRY_MUTATIONS + ["lane_callers", "lane_disposition", "claimed", "released", "retired",
    "session_lane", "session_claim", "session_activate", "lane_lease", "pending", "driver",
    "ticket_state", "source_state", "offer_state", "slot", "lifecycle", "selection",
    "notification", "lease_revision", "cell_identity", "active_ticket", "active_source", "offer_backlink", "notification_result", "conflict_stage"]


@pytest.mark.anyio
@pytest.mark.parametrize("row,mutation", [
    (row, mutation) for row in ("PREPARING", "RESERVED", "PRECAPTURE_INVALIDATED", "RESERVED_INVALIDATED",
        "PRE_NOTIFICATION_UNKNOWN", "RESERVED_NOTIFICATION_UNKNOWN", "POSTCHECK_UNKNOWN", "CONFLICT_HOLD")
    for mutation in ROW_MUTATIONS
] + [(row, mutation) for row in ("PRE_RETIRED", "PRE_DELIVERED", "RESERVED_DELIVERED")
     for mutation in SESSION_REGISTRY_MUTATIONS])
async def test_r2_every_published_row_rejects_mutation_without_repair(graph, monkeypatch, row, mutation):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    c = port._composition
    reserved = None
    if row in {"RESERVED", "RESERVED_INVALIDATED", "RESERVED_NOTIFICATION_UNKNOWN", "POSTCHECK_UNKNOWN", "RESERVED_DELIVERED"}:
        if row == "POSTCHECK_UNKNOWN":
            original = graph.store._cas_reserved_lease_v2
            def mismatch(*args):
                original(*args)
                c.active_initial_ticket_or_null = object()
            monkeypatch.setattr(graph.store, "_cas_reserved_lease_v2", mismatch)
        reserved = m._finalize_preparing_owned_v2(port, owner)[2]
    if row in {"PRECAPTURE_INVALIDATED", "PRE_NOTIFICATION_UNKNOWN", "PRE_DELIVERED"}:
        m._abort_preparing_owned_v2(owner, "INTERNAL_BUILD")
    if row in {"PRE_RETIRED", "RESERVED_INVALIDATED"}:
        m._retire_finalized_owned_v2(c)
    if row in {"PRE_NOTIFICATION_UNKNOWN", "RESERVED_NOTIFICATION_UNKNOWN"}:
        def fail(*args): raise RuntimeError("synthetic notification")
        monkeypatch.setattr(graph.arbiter, "_finish", fail)
        m._notify_finalized_owned_v2(owner, reserved)
    if row in {"PRE_DELIVERED", "RESERVED_DELIVERED"}:
        assert m._notify_finalized_owned_v2(owner, reserved) is not None
    if row == "CONFLICT_HOLD":
        graph.store._lease_bundle_cell_v2 = object()
        m._finalize_preparing_owned_v2(port, owner)
    assert m._validate_published_row_v2(c, owner) == c.flow_state
    slot = owner.abort_bundle.abort_notification_observation if reserved is None else reserved.notification_observation
    registry_key = (owner.exact_request.invocation_id, 1) if mutation == "_generations" else owner.exact_request.invocation_id
    if mutation in SESSION_REGISTRY_MUTATIONS:
        getattr(graph.session, mutation)[registry_key] = object()
    elif mutation == "active_ticket": c.active_initial_ticket_or_null = object()
    elif mutation == "active_source": c.active_offer_source_or_null = object()
    elif mutation == "offer_backlink": object.__setattr__(owner.exact_offer_source, "exact_offer_receipt_or_null", object())
    elif mutation == "notification_result": object.__setattr__(slot, "exact_expected_result", object())
    elif mutation == "conflict_stage": object.__setattr__(owner.abort_bundle.conflict_observation, "expected_stage", "foreign")
    elif mutation == "lane_callers": owner.exact_control_lane.callers = 1
    elif mutation == "lane_disposition": owner.exact_control_lane.disposition = "foreign"
    elif mutation == "claimed": owner.exact_client_lease._claimed = True
    elif mutation == "released": owner.exact_client_lease._released = True
    elif mutation == "retired": owner.exact_client_lease._retired = True
    elif mutation == "session_lane": graph.session._control_lanes[owner.exact_request.invocation_id] = object()
    elif mutation == "session_claim": graph.session._claimed_invocation = "foreign"
    elif mutation == "session_activate": graph.session._activated_invocation = "foreign"
    elif mutation == "lane_lease": owner.exact_control_lane.lease = object()
    elif mutation == "pending": graph.arbiter._active = object()
    elif mutation == "driver": graph.arbiter._admission_driver = None
    elif mutation == "ticket_state": object.__setattr__(owner.exact_initial_ticket, "state", "foreign")
    elif mutation == "source_state": object.__setattr__(owner.exact_offer_source, "state", "foreign")
    elif mutation == "offer_state": object.__setattr__(owner.exact_offer_receipt, "state", "foreign")
    elif mutation == "slot": c.initial_invocation_slot = "foreign"
    elif mutation == "lifecycle": c.lifecycle = "foreign"
    elif mutation == "selection": object.__setattr__(owner.abort_bundle.abort_selection, "state", "foreign")
    elif mutation == "notification": object.__setattr__(slot, "state", "foreign")
    elif mutation == "lease_revision":
        lease = (owner.abort_bundle.invalidated_receipt.exact_preparing_lease if row == "CONFLICT_HOLD"
                 else graph.store._preparing_lease_bundle_v2[0])
        object.__setattr__(lease, "lease_revision", 99)
    elif mutation == "cell_identity":
        object.__setattr__(owner if row == "CONFLICT_HOLD" else graph.store._preparing_lease_bundle_v2[-1], "cell_identity", object())
    current = graph.store._lease_bundle_cell_v2
    states = (c.lifecycle, c.flow_state, c.initial_invocation_slot, owner.exact_initial_ticket.state,
        owner.exact_offer_source.state, owner.exact_offer_receipt.state, owner.abort_bundle.state,
        owner.abort_bundle.abort_selection.state, slot.state)
    with pytest.raises(m.ReservedFinalizeError): m._validate_published_row_v2(c, owner)
    cas_calls = []
    notify_calls = []
    monkeypatch.setattr(graph.store, "_cas_invalidated_lease_v2", lambda *args: cas_calls.append(args))
    monkeypatch.setattr(graph.arbiter, "_finish", lambda *args: notify_calls.append(args))
    m._retire_finalized_owned_v2(c)
    m._notify_finalized_owned_v2(owner, reserved)
    assert graph.store._lease_bundle_cell_v2 is current
    assert not cas_calls and not notify_calls
    assert states == (c.lifecycle, c.flow_state, c.initial_invocation_slot, owner.exact_initial_ticket.state,
        owner.exact_offer_source.state, owner.exact_offer_receipt.state, owner.abort_bundle.state,
        owner.abort_bundle.abort_selection.state, slot.state)

    # Restore only the synthetic transport corruption after all assertions so the
    # shared fixture can perform its unrelated session teardown.
    if mutation in SESSION_REGISTRY_MUTATIONS:
        del getattr(graph.session, mutation)[registry_key]
    if mutation == "session_lane":
        graph.session._control_lanes[owner.exact_request.invocation_id] = owner.exact_control_lane
    if mutation == "lane_lease":
        owner.exact_control_lane.lease = owner.exact_client_lease


@pytest.mark.anyio
@pytest.mark.parametrize("reserved", [False, True])
async def test_r2_delivered_row_accepts_retire_but_never_notifies_twice(graph, monkeypatch, reserved):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    receipt = m._finalize_preparing_owned_v2(port, owner)[2] if reserved else None
    if not reserved: m._abort_preparing_owned_v2(owner, "INTERNAL_BUILD")
    assert m._notify_finalized_owned_v2(owner, receipt) is not None
    calls = []
    monkeypatch.setattr(graph.arbiter, "_finish", lambda *args: calls.append(args))
    assert m._notify_finalized_owned_v2(owner, receipt) is None
    assert calls == []
    m._retire_finalized_owned_v2(port._composition)
    assert graph.store._read_generation_row_v2()[0].status == "INVALIDATED"


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["first_mismatch_or_null", "observed_driver", "observed_lease", "observed_slot_state"])
async def test_r2_postcheck_unknown_evidence_is_not_repaired(graph, monkeypatch, field):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    original = graph.store._cas_reserved_lease_v2
    def mismatch(*args):
        original(*args)
        port._composition.active_initial_ticket_or_null = object()
    monkeypatch.setattr(graph.store, "_cas_reserved_lease_v2", mismatch)
    receipt = m._finalize_preparing_owned_v2(port, owner)[2]
    slot = receipt.postcheck_observation
    object.__setattr__(slot, field, object())
    cell = graph.store._lease_bundle_cell_v2
    with pytest.raises(m.ReservedFinalizeError): m._validate_published_row_v2(port._composition, owner)
    m._retire_finalized_owned_v2(port._composition)
    assert m._notify_finalized_owned_v2(owner, receipt) is None
    assert graph.store._lease_bundle_cell_v2 is cell
    assert port._composition.flow_state == "RESERVED_POSTCHECK_UNKNOWN"
    assert not pending.result.done()


@pytest.mark.anyio
@pytest.mark.parametrize("reserved", [False, True])
async def test_r2_invalidated_read_rejects_broken_owner_without_mutation(graph, reserved):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    if reserved: m._finalize_preparing_owned_v2(port, owner)
    m._retire_finalized_owned_v2(port._composition)
    cell = graph.store._lease_bundle_cell_v2
    owner.exact_control_lane.callers = 1
    with pytest.raises(m.ReservedFinalizeError): _ = graph.store._state_generation_lease_v2
    with pytest.raises(m.ReservedFinalizeError): _ = graph.store._preparing_lease_owner_receipt_v2
    assert graph.store._lease_bundle_cell_v2 is cell


@pytest.mark.anyio
@pytest.mark.parametrize("reserved", [False, True])
@pytest.mark.parametrize("registry_name", SESSION_REGISTRY_MUTATIONS)
async def test_r3_registry_absence_is_scoped_to_exact_invocation(graph, reserved, registry_name):
    from ai_client.discussion import reserved_finalize_v2 as m
    port, pending, owner = await preparing_graph(graph)
    if reserved:
        m._finalize_preparing_owned_v2(port, owner)
    registry = getattr(graph.session, registry_name)
    foreign_key = ("unrelated-invocation", 1) if registry_name == "_generations" else "unrelated-invocation"
    foreign_entry = object()
    registry[foreign_key] = foreign_entry
    try:
        assert m._validate_published_row_v2(port._composition, owner) == port._composition.flow_state
        assert m._retire_finalized_owned_v2(port._composition)
        assert graph.store._read_generation_row_v2()[0].status == "INVALIDATED"
        assert registry[foreign_key] is foreign_entry
        assert not pending.result.done()
    finally:
        del registry[foreign_key]
