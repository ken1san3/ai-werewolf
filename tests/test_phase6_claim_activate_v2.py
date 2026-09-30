from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_client.discussion import claim_activate_v2 as claim
from tests.test_phase6_offer_composition_v2 import graph, bind
from tests.test_phase6_initial_ticket_v2 import make_pending
from ai_client.llm.admission_types import AdmissionStatus
from ai_client.llm.admission_client import BrokerAdmissionSession, _ClientGenerationLease


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def offline_effect_guard(graph, monkeypatch):
    effects = {name: 0 for name in ("provider", "brain", "audit", "action", "active_cas", "invalid_cas")}
    async def forbidden_provider(*args, **kwargs):
        effects["provider"] += 1
        raise AssertionError("provider forbidden in offline claim proof")
    async def forbidden_audit(*args, **kwargs):
        effects["audit"] += 1
        raise AssertionError("audit forbidden in offline claim proof")
    async def forbidden_action(*args, **kwargs):
        effects["action"] += 1
        raise AssertionError("delivery forbidden in offline claim proof")
    monkeypatch.setattr(graph.backend, "generate", forbidden_provider)
    monkeypatch.setattr(graph.controller._discussion.audit, "write", forbidden_audit)
    for method in ("send_chat", "send_vote", "send_ability", "send_co_declare", "send_co_report"):
        monkeypatch.setattr(graph.source._network, method, forbidden_action)
    for method, counter in (("_cas_active_lease_v2", "active_cas"), ("_cas_claim_invalidated_v2", "invalid_cas")):
        original = getattr(type(graph.store), method)
        def counted(store, *args, _original=original, _counter=counter):
            effects[_counter] += 1
            return _original(store, *args)
        monkeypatch.setattr(type(graph.store), method, counted)
    graph.claim_effects = effects
    yield effects
    assert not graph.backend.calls and not graph.brain.calls
    assert effects["provider"] == effects["audit"] == effects["action"] == 0


async def reserved_graph(g, *, ttl=None):
    offer_port = bind(g)
    pending = make_pending(g)
    if ttl is not None:
        pending.dispatch_deadline = replace(pending.dispatch_deadline,
            not_after_monotonic=g.arbiter._clock() + ttl)
    g.world._current_deadline = replace(g.world._current_deadline,
        local_deadline_monotonic=pending.dispatch_deadline.not_after_monotonic)
    await g.arbiter._run_offer_preparing_offline_v2(pending)
    c = offer_port._composition
    assert g.store._state_generation_lease_v2.status == "RESERVED"
    return c, c.claim_activate_port


@pytest.mark.anyio
async def test_active_then_scope_cleanup(graph):
    g = graph
    c, port = await reserved_graph(g)
    receipt = await claim._claim_activate_reserved_owned_v2(port)
    assert receipt is not None, c.flow_state
    assert type(receipt) is claim.ActiveCaptureOwnerReceiptV2, c.flow_state
    assert g.store._state_generation_lease_v2.status == "ACTIVE"
    owner = c.claim_activate_owner_or_null
    assert owner.claim_task_or_null is None
    assert g.session._activated_invocation == owner.exact_request.invocation_id
    invalid = await claim._retire_active_owned_v2(port)
    assert type(invalid) is claim.ActiveInvalidatedOwnerReceiptV2, c.flow_state
    assert invalid.invalidated_lease.lease_revision == 3
    assert g.store._state_generation_lease_v2.status == "INVALIDATED"
    assert c.claim_activate_owner_or_null is None
    assert not g.backend.calls and not g.brain.calls
    with pytest.raises(claim.ClaimActivateError):
        await claim._claim_activate_reserved_owned_v2(port)
    with pytest.raises(claim.ClaimActivateError):
        await claim._retire_active_owned_v2(port)


class ControlCounts(dict):
    pass


def controls(monkeypatch, *, claim_status="GRANTED", release_status="RELEASED", ready=None, proceed=None,
             release_error=None, after_claim=None, after_release=None, abandon_status="CANCELLED",
             abandon_ready=None, abandon_proceed=None, abandon_error=None):
    counts = ControlCounts(CLAIM=0, ABANDON=0, RELEASE=0, ACTIVATE=0, ENTER=0, EXIT=0)
    counts.cancelled_control_seen = asyncio.Event()
    original_finish = BrokerAdmissionSession._finish_cancelled_control
    async def finish(task):
        counts.cancelled_control_seen.set()
        return await original_finish(task)
    monkeypatch.setattr(BrokerAdmissionSession, "_finish_cancelled_control", staticmethod(finish))
    async def control(session, operation, key, acknowledgement, fields, lane=None):
        counts[operation] += 1
        try:
            if operation == "CLAIM":
                if ready:
                    ready.set()
                if proceed:
                    await proceed.wait()
                if after_claim:
                    after_claim()
                return claim_status
            if operation == "ABANDON":
                if abandon_ready is not None:
                    abandon_ready.set()
                if abandon_proceed is not None:
                    await abandon_proceed.wait()
                if abandon_error is not None:
                    raise abandon_error
                return claim_status if claim_status != "GRANTED" else abandon_status
            if release_error:
                raise release_error
            if after_release:
                after_release()
            return release_status
        finally:
            if session._acks.get(key) is acknowledgement:
                session._acks.pop(key)
    original_activate = _ClientGenerationLease.activate
    from ai_client.llm.admission_client import _LeaseActivation
    original_enter, original_exit = _LeaseActivation.__enter__, _LeaseActivation.__exit__
    def activate(lease):
        counts["ACTIVATE"] += 1
        return original_activate(lease)
    def enter(context):
        counts["ENTER"] += 1
        return original_enter(context)
    def leave(context, *args):
        counts["EXIT"] += 1
        return original_exit(context, *args)
    monkeypatch.setattr(BrokerAdmissionSession, "_control_with_registered_ack", control)
    monkeypatch.setattr(_ClientGenerationLease, "activate", activate)
    monkeypatch.setattr(_LeaseActivation, "__enter__", enter)
    monkeypatch.setattr(_LeaseActivation, "__exit__", leave)
    return counts


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["CANCELLED", "EXPIRED", "POISONED", "UNAVAILABLE"])
async def test_non_grant_exact_terminal_i2(graph, monkeypatch, status):
    g = graph
    c, port = await reserved_graph(g)
    counts = controls(monkeypatch, claim_status=status)
    result = await claim._claim_activate_reserved_owned_v2(port)
    assert result is not None, c.flow_state
    assert g.store._state_generation_lease_v2.lease_revision == 2
    assert c.claim_activate_outcome_or_null == "CLAIM_RETURNED_NON_GRANT"
    assert c.claim_activate_owner_or_null is None
    assert counts == dict(CLAIM=1, ABANDON=0, RELEASE=0, ACTIVATE=0, ENTER=0, EXIT=0)
    assert not g.backend.calls and not g.brain.calls


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["RELEASED", "EXPIRED", "POISONED"])
async def test_release_none_exact_shapes(graph, monkeypatch, status):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch, release_status=status)
    assert await claim._claim_activate_reserved_owned_v2(port) is not None
    result = await claim._retire_active_owned_v2(port)
    assert type(result) is claim.ActiveInvalidatedOwnerReceiptV2, c.flow_state
    assert counts == dict(CLAIM=1, ABANDON=0, RELEASE=1, ACTIVATE=1, ENTER=1, EXIT=1)


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["CANCELLED", "EXPIRED", "POISONED", "UNAVAILABLE", "GRANTED"])
async def test_cancelled_claim_converges_using_client_semantics(graph, monkeypatch, status):
    c, port = await reserved_graph(graph)
    ready, proceed = asyncio.Event(), asyncio.Event()
    counts = controls(monkeypatch, claim_status=status, ready=ready, proceed=proceed)
    driver = asyncio.create_task(claim._claim_activate_reserved_owned_v2(port))
    await asyncio.wait_for(ready.wait(), timeout=1.0)
    c.claim_activate_cancel_event.set()
    # Wait for cancellation to enter the actual client's control settlement.
    await asyncio.wait_for(counts.cancelled_control_seen.wait(), timeout=1.0)
    proceed.set()
    result = await driver
    assert result is not None, c.flow_state
    assert counts["CLAIM"] == 1 and counts["ABANDON"] == (1 if status == "GRANTED" else 0)
    assert counts["RELEASE"] == counts["ACTIVATE"] == counts["ENTER"] == 0
    assert c.claim_activate_outcome_or_null == (
        "CLAIM_CANCELLED_ABANDONED" if status == "GRANTED" else "CLAIM_CANCELLED_NON_GRANT")


@pytest.mark.anyio
async def test_stale_before_claim_uses_i2_without_control(graph, monkeypatch):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    graph.world._current_deadline = replace(graph.world._current_deadline, mapping_order=900)
    assert await claim._claim_activate_reserved_owned_v2(port) is not None
    assert c.claim_activate_outcome_or_null == "PRECLAIM_FRESHNESS"
    assert not any(counts.values())


@pytest.mark.anyio
async def test_stale_after_grant_releases_before_i2(graph, monkeypatch):
    c, port = await reserved_graph(graph)
    def stale():
        graph.world._current_deadline = replace(graph.world._current_deadline, mapping_order=900)
    counts = controls(monkeypatch, after_claim=stale)
    assert await claim._claim_activate_reserved_owned_v2(port) is not None, c.flow_state
    assert c.claim_activate_outcome_or_null == "GRANTED_CLEAN_RELEASE"
    assert counts == dict(CLAIM=1, ABANDON=0, RELEASE=1, ACTIVATE=0, ENTER=0, EXIT=0)


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["UNAVAILABLE", "ALIEN"])
async def test_release_unknown_never_retries(graph, monkeypatch, status):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch, release_status=status)
    assert await claim._claim_activate_reserved_owned_v2(port) is not None
    current = graph.store._lease_bundle_cell_v2
    assert await claim._retire_active_owned_v2(port) is None
    assert c.flow_state == "ACTIVE_CLEANUP_UNKNOWN"
    assert graph.store._lease_bundle_cell_v2 is current
    with pytest.raises(claim.ClaimActivateError):
        await claim._retire_active_owned_v2(port)
    assert counts["RELEASE"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("field,value", [
    ("lane.callers", 1), ("lane.disposition", "GRANTED"), ("lane.lease", None),
    ("lease._claimed", True), ("lease._released", True), ("lease._retired", True),
    ("lease._abandon_disposition", "ABANDON_ACKNOWLEDGED"),
    ("session._claimed_invocation", "foreign"), ("session._activated_invocation", "foreign"),
    ("session._closed", True), ("session._transport_closed", True),
    ("composition.lifecycle", "RETIRED"), ("composition.initial_invocation_slot", "EMPTY"),
    ("composition.flow_state", "ACTIVE"), ("port.exact_store", None),
    ("port.exact_session", None), ("port.exact_arbiter", None),
    ("composition.claim_activate_owner_or_null", object()),
])
async def test_begin_one_field_mismatch_is_readonly(graph, monkeypatch, field, value):
    c, port = await reserved_graph(graph)
    current = graph.store._lease_bundle_cell_v2
    p = current.exact_tuple[-1].exact_preparing_receipt
    counts = controls(monkeypatch)
    roots = dict(lane=p.exact_control_lane, lease=p.exact_client_lease, session=graph.session, composition=c, port=port)
    root, attribute = field.split(".")
    target = roots[root]
    old = getattr(target, attribute)
    object.__setattr__(target, attribute, value)
    try:
        with pytest.raises(claim.OfferCompositionError):
            await claim._claim_activate_reserved_owned_v2(port)
        assert graph.store._lease_bundle_cell_v2 is current
        assert not any(counts.values())
    finally:
        object.__setattr__(target, attribute, old)


@pytest.mark.anyio
@pytest.mark.parametrize("registry", ["_results", "_acks", "_attachments", "_call_ordinals", "_request_ids", "_terminal_controls", "_generations"])
async def test_begin_target_registry_contamination_rejects(graph, monkeypatch, registry):
    c, port = await reserved_graph(graph)
    current = graph.store._lease_bundle_cell_v2
    invocation = current.exact_tuple[-1].exact_preparing_receipt.exact_request.invocation_id
    key = (invocation, 1) if registry == "_generations" else invocation
    values = getattr(graph.session, registry)
    values[key] = object()
    counts = controls(monkeypatch)
    try:
        with pytest.raises(claim.OfferCompositionError):
            await claim._claim_activate_reserved_owned_v2(port)
        assert not any(counts.values())
        assert graph.store._lease_bundle_cell_v2 is current
    finally:
        values.pop(key)


@pytest.mark.anyio
@pytest.mark.parametrize("position", [1, 2, 3, 4])
async def test_task_create_failure_never_opens_gate(graph, monkeypatch, position):
    c, port = await reserved_graph(graph)
    current = graph.store._lease_bundle_cell_v2
    counts = controls(monkeypatch)
    original = asyncio.create_task
    made, attempt = [], 0
    def create(coro, *args, **kwargs):
        nonlocal attempt
        attempt += 1
        if attempt == position:
            raise MemoryError("synthetic")
        task = original(coro, *args, **kwargs)
        made.append(task)
        return task
    monkeypatch.setattr(asyncio, "create_task", create)
    assert await claim._claim_activate_reserved_owned_v2(port) is None
    assert c.claim_activate_owner_or_null is None
    assert c.claim_activate_prepublication_hold_or_null is None
    assert all(task.done() and task.cancelled() for task in made)
    assert graph.store._lease_bundle_cell_v2 is current
    assert not any(counts.values())


@pytest.mark.anyio
@pytest.mark.parametrize("point", ["_new_owner", "ClaimActivateOwnerV2", "ClaimActivateObservationV2", "ClaimOwnerIdentityV2"])
async def test_owner_allocation_failure_has_no_control(graph, monkeypatch, point):
    c, port = await reserved_graph(graph)
    current = graph.store._lease_bundle_cell_v2
    counts = controls(monkeypatch)
    def fail(*args, **kwargs):
        raise MemoryError("synthetic")
    monkeypatch.setattr(claim, point, fail)
    assert await claim._claim_activate_reserved_owned_v2(port) is None
    assert c.claim_activate_owner_or_null is None
    assert graph.store._lease_bundle_cell_v2 is current
    assert not any(counts.values())


@pytest.mark.anyio
@pytest.mark.parametrize("point", ["ActiveCaptureOwnerReceiptV2", "ActivePostcheckObservationV2", "ActiveCleanupSelectionV2",
                                  "ActiveInvalidatedOwnerReceiptV2", "ActiveCleanupCandidateV2"])
async def test_active_candidate_allocation_failure_releases_once(graph, monkeypatch, point):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    original = getattr(claim, point)
    def fail(*args, **kwargs):
        raise MemoryError("synthetic")
    monkeypatch.setattr(claim, point, fail)
    result = await claim._claim_activate_reserved_owned_v2(port)
    monkeypatch.setattr(claim, point, original)
    assert result is not None, c.flow_state
    assert c.claim_activate_outcome_or_null == "GRANTED_CLEAN_RELEASE"
    assert counts["CLAIM"] == counts["RELEASE"] == 1
    assert counts["ENTER"] == counts["EXIT"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("point", ["activate", "enter"])
async def test_activation_failure_has_clean_i2(graph, monkeypatch, point):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    from ai_client.llm.admission_client import _LeaseActivation
    def fail(*args):
        raise MemoryError("synthetic")
    monkeypatch.setattr(_ClientGenerationLease if point == "activate" else _LeaseActivation,
                        "activate" if point == "activate" else "__enter__", fail)
    assert await claim._claim_activate_reserved_owned_v2(port) is not None, c.flow_state
    assert counts["RELEASE"] == 1
    assert c.claim_activate_outcome_or_null == "GRANTED_CLEAN_RELEASE"


@pytest.mark.anyio
async def test_active_cas_conflict_exits_and_releases(graph, monkeypatch):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    foreign = object()
    def lose(store, expected, candidate):
        store._lease_bundle_cell_v2 = foreign
        raise claim.ClaimActivateError("SYNTHETIC_CONFLICT")
    monkeypatch.setattr(type(graph.store), "_cas_active_lease_v2", lose)
    assert await claim._claim_activate_reserved_owned_v2(port) is None
    assert c.flow_state == "CLAIM_ABORT_CONFLICT_HOLD"
    assert graph.store._lease_bundle_cell_v2 is foreign
    assert counts == dict(CLAIM=1, ABANDON=0, RELEASE=1, ACTIVATE=1, ENTER=1, EXIT=1)


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["callers", "disposition", "lease"])
async def test_active_postpublish_corruption_keeps_activation(graph, monkeypatch, field):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    original = type(graph.store)._cas_active_lease_v2
    def publish(store, expected, candidate):
        result = original(store, expected, candidate)
        lane = c.claim_activate_owner_or_null.exact_lane
        setattr(lane, field, 1 if field == "callers" else None)
        return result
    monkeypatch.setattr(type(graph.store), "_cas_active_lease_v2", publish)
    assert await claim._claim_activate_reserved_owned_v2(port) is None
    assert c.flow_state == "ACTIVE_POSTCHECK_UNKNOWN"
    assert graph.store._lease_bundle_cell_v2.stage == "ACTIVE"
    assert c.claim_activate_owner_or_null.activation_context_or_null._entered
    assert counts["RELEASE"] == counts["EXIT"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("field,value", [("exact_session", None), ("exact_lane", None), ("exact_client_lease", None),
    ("exact_request", None), ("exact_capture", None), ("active_candidate_cell_or_null", None),
    ("activation_context_or_null", None), ("cleanup_task_or_null", object()), ("world_watcher_task_or_null", object())])
async def test_active_owner_mutation_cannot_cleanup(graph, monkeypatch, field, value):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    assert await claim._claim_activate_reserved_owned_v2(port) is not None
    owner = c.claim_activate_owner_or_null
    current = graph.store._lease_bundle_cell_v2
    old = getattr(owner, field)
    object.__setattr__(owner, field, value)
    try:
        with pytest.raises(claim.OfferCompositionError):
            await claim._retire_active_owned_v2(port)
        assert graph.store._lease_bundle_cell_v2 is current
        assert counts["RELEASE"] == counts["EXIT"] == 0
    finally:
        object.__setattr__(owner, field, old)
    assert await claim._retire_active_owned_v2(port) is not None


@pytest.mark.anyio
async def test_clean_owner_and_unselected_candidates_collect_without_gc_cycle(graph, monkeypatch):
    import weakref
    c, port = await reserved_graph(graph)
    controls(monkeypatch)
    receipt = await claim._claim_activate_reserved_owned_v2(port)
    owner = c.claim_activate_owner_or_null
    owner_ref = weakref.ref(owner)
    candidates = tuple(weakref.ref(candidate) for candidate in owner.active_cleanup_candidates_or_null)
    active_cell = weakref.ref(owner.active_candidate_cell_or_null)
    assert await claim._retire_active_owned_v2(port) is not None
    del owner, receipt
    assert owner_ref() is None
    assert active_cell() is None
    assert all(ref() is None for ref in candidates)


@pytest.mark.anyio
async def test_stop_active_uses_owned_cleanup_once(graph, monkeypatch):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    await claim._claim_activate_reserved_owned_v2(port)
    await graph.arbiter.stop()
    assert c.claim_activate_owner_or_null is None
    assert c.claim_activate_outcome_or_null == "ACTIVE_CANCEL"
    assert counts["RELEASE"] == counts["EXIT"] == 1


@pytest.mark.anyio
async def test_claim_settlement_timeout_holds_live_task_without_second_control(graph, monkeypatch):
    graph.session._config = replace(graph.session._config, cancellation_grace_seconds=0.03)
    c, port = await reserved_graph(graph, ttl=0.20)
    proceed = asyncio.Event()
    counts = controls(monkeypatch, proceed=proceed)
    current = graph.store._lease_bundle_cell_v2
    assert await claim._claim_activate_reserved_owned_v2(port) is None
    owner = c.claim_activate_owner_or_null
    assert c.flow_state == "CLAIM_RESULT_UNKNOWN"
    assert owner.observation.failure_code_or_null == "CLAIM_SETTLE_TIMEOUT"
    assert not owner.claim_task_or_null.done()
    assert graph.store._lease_bundle_cell_v2 is current
    assert claim._validate_claim_row_v2(c) == "CLAIM_RESULT_UNKNOWN"
    with pytest.raises(claim.ClaimActivateError):
        await claim._claim_activate_reserved_owned_v2(port)
    proceed.set()
    await asyncio.gather(owner.claim_task_or_null, return_exceptions=True)
    assert graph.store._lease_bundle_cell_v2 is current
    assert c.flow_state == "CLAIM_RESULT_UNKNOWN"
    assert counts["CLAIM"] == counts["ABANDON"] == 1
    assert counts["ACTIVATE"] == counts["RELEASE"] == 0


@pytest.mark.anyio
async def test_release_timeout_retains_task_and_cell(graph, monkeypatch):
    graph.session._config = replace(graph.session._config, shutdown_grace_seconds=0.03,
        cancellation_grace_seconds=0.01, provider_drain_grace_seconds=0.01)
    c, port = await reserved_graph(graph)
    controls(monkeypatch)
    await claim._claim_activate_reserved_owned_v2(port)
    owner = c.claim_activate_owner_or_null
    current = graph.store._lease_bundle_cell_v2
    proceed = asyncio.Event()
    original = _ClientGenerationLease.release
    releases = 0
    async def release(lease):
        nonlocal releases
        releases += 1
        await proceed.wait()
        await original(lease)
    monkeypatch.setattr(_ClientGenerationLease, "release", release)
    assert await claim._retire_active_owned_v2(port) is None
    assert c.flow_state == "ACTIVE_CLEANUP_UNKNOWN"
    assert owner.observation.failure_code_or_null == "RELEASE_TIMEOUT"
    assert not owner.cleanup_task_or_null.done()
    assert graph.store._lease_bundle_cell_v2 is current
    assert claim._validate_claim_row_v2(c) == "ACTIVE_CLEANUP_UNKNOWN"
    proceed.set()
    await owner.cleanup_task_or_null
    with pytest.raises(claim.ClaimActivateError):
        await claim._retire_active_owned_v2(port)
    assert releases == 1
    assert c.flow_state == "ACTIVE_CLEANUP_UNKNOWN"


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["raise", "cancel", "same_value_status", "offered"])
async def test_claim_unproven_result_has_no_i2(graph, monkeypatch, mode):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    current = graph.store._lease_bundle_cell_v2
    calls = 0
    async def bad(lease):
        nonlocal calls
        calls += 1
        if mode == "raise":
            raise RuntimeError("synthetic")
        if mode == "cancel":
            raise asyncio.CancelledError()
        return "GRANTED" if mode == "same_value_status" else AdmissionStatus.OFFERED
    monkeypatch.setattr(_ClientGenerationLease, "claim", bad)
    assert await claim._claim_activate_reserved_owned_v2(port) is None
    assert c.flow_state == "CLAIM_RESULT_UNKNOWN"
    assert graph.store._lease_bundle_cell_v2 is current
    assert claim._validate_claim_row_v2(c) == "CLAIM_RESULT_UNKNOWN"
    assert calls == 1 and counts["RELEASE"] == counts["ACTIVATE"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["raise", "cancel", "mixed", "transport"])
async def test_release_failure_has_no_clean_promotion(graph, monkeypatch, mode):
    c, port = await reserved_graph(graph)
    controls(monkeypatch)
    await claim._claim_activate_reserved_owned_v2(port)
    original = _ClientGenerationLease.release
    releases = 0
    async def release(lease):
        nonlocal releases
        releases += 1
        if mode == "raise":
            raise RuntimeError("synthetic")
        if mode == "cancel":
            raise asyncio.CancelledError()
        await original(lease)
        if mode == "mixed":
            lease._claimed = True
        else:
            lease._session._transport_closed = True
    monkeypatch.setattr(_ClientGenerationLease, "release", release)
    current = graph.store._lease_bundle_cell_v2
    assert await claim._retire_active_owned_v2(port) is None
    assert c.flow_state == "ACTIVE_CLEANUP_UNKNOWN"
    assert graph.store._lease_bundle_cell_v2 is current
    assert releases == 1


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["order", "reason", "identity", "tuple", "foreign_cell"])
async def test_cleanup_candidate_mutation_rejects_before_exit(graph, monkeypatch, mode):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    await claim._claim_activate_reserved_owned_v2(port)
    owner = c.claim_activate_owner_or_null
    selection = owner.active_cleanup_selection
    candidates = selection.candidates
    if mode == "order":
        object.__setattr__(selection, "candidates", candidates[::-1])
    elif mode == "tuple":
        object.__setattr__(selection, "candidates", tuple(list(candidates)))
    elif mode == "reason":
        object.__setattr__(candidates[0], "reason", "ACTIVE_CANCEL")
    elif mode == "identity":
        object.__setattr__(selection, "selected_candidate_identity_or_null", candidates[1].candidate_cell.bundle_identity)
    else:
        object.__setattr__(candidates[0], "candidate_cell", candidates[1].candidate_cell)
    with pytest.raises(claim.ClaimActivateError):
        await claim._retire_active_owned_v2(port)
    assert counts["RELEASE"] == counts["EXIT"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("field,value", [
    ("state", "CLAIM_TERMINAL"), ("claim_status_or_null", AdmissionStatus.GRANTED),
    ("cleanup_status_or_null", "RELEASED"), ("failure_code_or_null", "CLAIM_RETURNED_NON_GRANT"),
    ("expected_reserved_identity", object()), ("observed_store_identity_or_null", object()),
    ("observed_lane_disposition_or_null", "GRANTED"), ("observed_claimed_invocation_or_null", "foreign"),
    ("observed_activated_invocation_or_null", "foreign"), ("observed_lease_claimed_or_null", False),
    ("observed_lease_released_or_null", False), ("observed_lease_retired_or_null", False)])
async def test_active_empty_observation_each_field_is_closed(graph, monkeypatch, field, value):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    await claim._claim_activate_reserved_owned_v2(port)
    observation = c.claim_activate_owner_or_null.observation
    object.__setattr__(observation, field, value)
    with pytest.raises(claim.ClaimActivateError):
        await claim._retire_active_owned_v2(port)
    assert counts["RELEASE"] == counts["EXIT"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("field,value", [("failure_code_or_null", "CLAIM_CANCELLED_NON_GRANT"),
    ("cleanup_status_or_null", "RELEASED"), ("claim_status_or_null", "CANCELLED"), ("state", "EMPTY")])
async def test_terminal_subrow_mutation_cannot_publish_i2(graph, monkeypatch, field, value):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch, claim_status="CANCELLED")
    current = graph.store._lease_bundle_cell_v2
    original = claim._publish_reserved_invalidated_after_claim_v2
    def mutate(port, owner):
        object.__setattr__(owner.observation, field, value)
        return original(port, owner)
    monkeypatch.setattr(claim, "_publish_reserved_invalidated_after_claim_v2", mutate)
    assert await claim._claim_activate_reserved_owned_v2(port) is None
    assert c.flow_state == "CLAIM_ABORT_CONFLICT_HOLD"
    assert graph.store._lease_bundle_cell_v2 is current
    assert counts["CLAIM"] == 1 and counts["RELEASE"] == counts["ACTIVATE"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["active", "i2", "i3"])
async def test_cas_raises_after_exchange_keeps_published_cell_unknown(graph, monkeypatch, phase):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch, claim_status="CANCELLED" if phase == "i2" else "GRANTED")
    name = "_cas_active_lease_v2" if phase == "active" else "_cas_claim_invalidated_v2"
    if phase == "i3":
        await claim._claim_activate_reserved_owned_v2(port)
    original = getattr(type(graph.store), name)
    def publish(store, *args):
        original(store, *args)
        raise claim.ClaimActivateError("SYNTHETIC_POSTPUBLISH")
    monkeypatch.setattr(type(graph.store), name, publish)
    if phase == "i3":
        assert await claim._retire_active_owned_v2(port) is None
    else:
        assert await claim._claim_activate_reserved_owned_v2(port) is None
    assert c.flow_state == {"active": "ACTIVE_POSTCHECK_UNKNOWN", "i2": "I2_POSTCHECK_UNKNOWN", "i3": "I3_POSTCHECK_UNKNOWN"}[phase]
    assert graph.store._lease_bundle_cell_v2.stage == {"active": "ACTIVE", "i2": "RESERVED_INVALIDATED", "i3": "ACTIVE_INVALIDATED"}[phase]
    assert counts["RELEASE"] == (1 if phase == "i3" else 0)


@pytest.mark.anyio
@pytest.mark.parametrize("reason", ["ACTIVE_CANCEL", "ACTIVE_EXPIRED", "ACTIVE_STALE"])
async def test_cleanup_reason_selection(graph, monkeypatch, reason):
    original_clock = graph.arbiter._clock
    offset = [0.0]
    def clock():
        return original_clock() + offset[0]
    if reason == "ACTIVE_EXPIRED":
        graph.session._config = replace(graph.session._config, cancellation_grace_seconds=0.01)
        graph.arbiter._clock = graph.controller._clock = clock
    c, port = await reserved_graph(graph, ttl=0.15 if reason == "ACTIVE_EXPIRED" else None)
    controls(monkeypatch)
    await claim._claim_activate_reserved_owned_v2(port)
    if reason == "ACTIVE_CANCEL":
        c.claim_activate_cancel_event.set()
    elif reason == "ACTIVE_EXPIRED":
        offset[0] = 0.16
    else:
        graph.world._current_deadline = replace(graph.world._current_deadline, mapping_order=999)
    result = await claim._retire_active_owned_v2(port)
    assert result.reason == reason
    assert graph.claim_effects["active_cas"] == graph.claim_effects["invalid_cas"] == 1


@pytest.mark.anyio
async def test_world_update_cancels_pending_claim(graph, monkeypatch):
    c, port = await reserved_graph(graph)
    ready, proceed, update = asyncio.Event(), asyncio.Event(), asyncio.Event()
    counts = controls(monkeypatch, claim_status="CANCELLED", ready=ready, proceed=proceed)
    async def world_wait(version):
        assert version == graph.store._lease_bundle_cell_v2.exact_tuple[1].world_version
        await update.wait()
    monkeypatch.setattr(graph.world, "wait_for_update", world_wait)
    task = asyncio.create_task(claim._claim_activate_reserved_owned_v2(port))
    await asyncio.wait_for(ready.wait(), timeout=1.0)
    update.set()
    await asyncio.wait_for(counts.cancelled_control_seen.wait(), timeout=1.0)
    proceed.set()
    assert await task is not None
    assert c.claim_activate_outcome_or_null == "CLAIM_CANCELLED_NON_GRANT"
    assert counts["ABANDON"] == counts["RELEASE"] == 0


@pytest.mark.anyio
async def test_same_turn_grant_and_cancel_releases_instead_of_activation(graph, monkeypatch):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    original = claim._wait_claim
    async def simultaneous(owner):
        status, _ = await original(owner)
        c.claim_activate_cancel_event.set()
        return status, True
    monkeypatch.setattr(claim, "_wait_claim", simultaneous)
    assert await claim._claim_activate_reserved_owned_v2(port) is not None
    assert c.claim_activate_outcome_or_null == "GRANTED_CLEAN_RELEASE"
    assert counts["RELEASE"] == 1 and counts["ACTIVATE"] == 0


@pytest.mark.anyio
async def test_caller_cancellation_waits_claim_settlement(graph, monkeypatch):
    c, port = await reserved_graph(graph)
    ready, proceed = asyncio.Event(), asyncio.Event()
    counts = controls(monkeypatch, ready=ready, proceed=proceed)
    driver = asyncio.create_task(claim._claim_activate_reserved_owned_v2(port))
    await asyncio.wait_for(ready.wait(), timeout=1.0)
    driver.cancel()
    await asyncio.wait_for(counts.cancelled_control_seen.wait(), timeout=1.0)
    proceed.set()
    assert await driver is not None
    assert counts["CLAIM"] == counts["ABANDON"] == 1
    assert counts["ACTIVATE"] == counts["RELEASE"] == 0


@pytest.mark.anyio
async def test_prepublication_task_cleanup_unknown_preserves_unopened_gate(graph, monkeypatch):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    current = graph.store._lease_bundle_cell_v2
    original = asyncio.create_task
    attempts = 0
    def create(coro):
        nonlocal attempts
        attempts += 1
        if attempts == 4:
            raise MemoryError("synthetic")
        return original(coro)
    async def exceeded(tasks, deadline, clock):
        await asyncio.gather(*tasks, return_exceptions=True)
        return False
    monkeypatch.setattr(asyncio, "create_task", create)
    monkeypatch.setattr(claim, "_settle", exceeded)
    assert await claim._claim_activate_reserved_owned_v2(port) is None
    assert c.claim_activate_owner_or_null is None
    hold = c.claim_activate_prepublication_hold_or_null
    assert not hold.exact_gate.is_set() and sum(getattr(hold, name) is not None for name in claim._TASK_SLOTS) == 3
    assert claim._validate_claim_row_v2(c) == "PREPUBLICATION_TASK_UNKNOWN"
    assert graph.store._lease_bundle_cell_v2 is current
    with pytest.raises(claim.ClaimActivateError):
        await claim._claim_activate_reserved_owned_v2(port)
    assert not any(counts.values())


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["i2", "i3"])
@pytest.mark.parametrize("field", ["state", "lifecycle", "flow_state", "initial_invocation_slot"])
async def test_post_invalidation_corruption_does_not_clear_owner(graph, monkeypatch, stage, field):
    c, port = await reserved_graph(graph)
    controls(monkeypatch, claim_status="CANCELLED" if stage == "i2" else "GRANTED")
    if stage == "i3":
        await claim._claim_activate_reserved_owned_v2(port)
    original = claim._retire_components
    def corrupt(c, p, flow):
        original(c, p, flow)
        if field == "state":
            object.__setattr__(p.exact_offer_source, "state", "FOREIGN")
        else:
            setattr(c, field, "FOREIGN")
    monkeypatch.setattr(claim, "_retire_components", corrupt)
    result = await (claim._retire_active_owned_v2(port) if stage == "i3" else claim._claim_activate_reserved_owned_v2(port))
    assert result is None
    assert c.flow_state == ("I3_POSTCHECK_UNKNOWN" if stage == "i3" else "I2_POSTCHECK_UNKNOWN")
    assert c.claim_activate_owner_or_null is not None


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["foreign", "equal_tuple", "identity_reuse"])
async def test_cell_identity_replacement_is_not_authority(graph, monkeypatch, mode):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    current = graph.store._lease_bundle_cell_v2
    r = claim.reserved
    if mode == "foreign":
        graph.store._lease_bundle_cell_v2 = object()
    elif mode == "equal_tuple":
        graph.store._lease_bundle_cell_v2 = r.LeaseBundleCellV2(r._ISSUER, stage=current.stage,
            bundle_identity=current.bundle_identity, exact_tuple=tuple(list(current.exact_tuple)))
    else:
        with pytest.raises(r.ReservedFinalizeError):
            r._issue_bundle_cell_v2(graph.store, current.stage, current.bundle_identity, current.exact_tuple)
        assert graph.store._lease_bundle_cell_v2 is current
        return
    replacement = graph.store._lease_bundle_cell_v2
    with pytest.raises(claim.OfferCompositionError):
        await claim._claim_activate_reserved_owned_v2(port)
    assert graph.store._lease_bundle_cell_v2 is replacement
    assert not any(counts.values())


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["CANCELLED", "EXPIRED", "POISONED", "UNAVAILABLE"])
async def test_cancelled_grant_abandon_status_matrix(graph, monkeypatch, status):
    c, port = await reserved_graph(graph)
    ready, proceed = asyncio.Event(), asyncio.Event()
    counts = controls(monkeypatch, ready=ready, proceed=proceed, abandon_status=status)
    current = graph.store._lease_bundle_cell_v2
    task = asyncio.create_task(claim._claim_activate_reserved_owned_v2(port))
    await asyncio.wait_for(ready.wait(), timeout=1.0)
    c.claim_activate_cancel_event.set()
    await asyncio.wait_for(counts.cancelled_control_seen.wait(), timeout=1.0)
    proceed.set()
    result = await task
    if status == "UNAVAILABLE":
        assert result is None and c.flow_state == "CLAIM_RESULT_UNKNOWN"
        assert graph.store._lease_bundle_cell_v2 is current
        assert c.claim_activate_owner_or_null.exact_client_lease._abandon_disposition == "ABANDON_SESSION_LOST"
        assert graph.claim_effects["invalid_cas"] == 0
    else:
        assert result is not None
        assert c.claim_activate_outcome_or_null == "CLAIM_CANCELLED_ABANDONED"
        assert graph.claim_effects["invalid_cas"] == 1
    assert counts["CLAIM"] == counts["ABANDON"] == 1
    assert counts["RELEASE"] == counts["ACTIVATE"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["claimed", "released", "retired", "abandon", "tombstone", "callers", "requests", "ordinal", "generation"])
@pytest.mark.parametrize("status", ["RELEASED", "EXPIRED", "POISONED"])
async def test_release_each_matrix_field_cannot_promote_clean(graph, monkeypatch, mutation, status):
    c, port = await reserved_graph(graph)
    controls(monkeypatch, release_status=status)
    await claim._claim_activate_reserved_owned_v2(port)
    owner = c.claim_activate_owner_or_null
    original = _ClientGenerationLease.release
    invocation = owner.exact_request.invocation_id
    async def mixed(lease):
        await original(lease)
        if mutation in ("claimed", "released", "retired"):
            setattr(lease, "_" + mutation, not getattr(lease, "_" + mutation))
        elif mutation == "abandon":
            lease._abandon_disposition = None if status == "EXPIRED" else "ABANDON_ACKNOWLEDGED"
        elif mutation == "tombstone":
            graph.session._terminal_controls[invocation] = AdmissionStatus.GRANTED
        elif mutation == "callers":
            owner.exact_lane.callers = 1
        elif mutation == "requests":
            graph.session._request_ids[invocation] = set()
        elif mutation == "ordinal":
            graph.session._call_ordinals[invocation] = 0
        else:
            graph.session._generations[(invocation, 1)] = asyncio.get_running_loop().create_future()
    monkeypatch.setattr(_ClientGenerationLease, "release", mixed)
    current = graph.store._lease_bundle_cell_v2
    assert await claim._retire_active_owned_v2(port) is None
    assert c.flow_state == "ACTIVE_CLEANUP_UNKNOWN"
    assert graph.store._lease_bundle_cell_v2 is current
    assert graph.claim_effects["invalid_cas"] == 0
    graph.session._generations.pop((invocation, 1), None)


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["input_sha256", "catalog_sha256", "option_catalog_sha256", "recipient_proof_sha256",
                                   "profile_bundle_sha256", "capture_id", "world_version", "fact_revision", "action_generation"])
async def test_active_capture_field_mismatch_rejects_cleanup(graph, monkeypatch, field):
    c, port = await reserved_graph(graph)
    controls(monkeypatch)
    await claim._claim_activate_reserved_owned_v2(port)
    capture = c.claim_activate_owner_or_null.exact_capture
    old = getattr(capture, field)
    object.__setattr__(capture, field, old + 1 if type(old) is int else "f" * 64)
    with pytest.raises(claim.ClaimActivateError):
        await claim._retire_active_owned_v2(port)
    assert graph.claim_effects["invalid_cas"] == 0
    object.__setattr__(capture, field, old)


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["_results", "_acks", "_attachments", "_terminal_controls", "_generations", "_call_ordinals", "_request_ids"])
async def test_active_registry_field_mismatch_rejects_cleanup(graph, monkeypatch, field):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    await claim._claim_activate_reserved_owned_v2(port)
    invocation = c.claim_activate_owner_or_null.exact_request.invocation_id
    registry = getattr(graph.session, field)
    key = (invocation, 1) if field == "_generations" else invocation
    missing = object()
    old = registry.get(key, missing)
    registry[key] = 1 if field == "_call_ordinals" else {"foreign"} if field == "_request_ids" else object()
    try:
        with pytest.raises(claim.ClaimActivateError):
            await claim._retire_active_owned_v2(port)
        assert counts["RELEASE"] == counts["EXIT"] == 0
        assert graph.claim_effects["invalid_cas"] == 0
    finally:
        if old is missing:
            registry.pop(key)
        else:
            registry[key] = old


@pytest.mark.anyio
async def test_foreign_invocation_registries_are_not_mistaken_for_this_owner(graph, monkeypatch):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    graph.session._request_ids["other"] = {"other-request"}
    graph.session._call_ordinals["other"] = 1
    assert await claim._claim_activate_reserved_owned_v2(port) is not None
    assert await claim._retire_active_owned_v2(port) is not None
    assert counts["CLAIM"] == counts["RELEASE"] == 1


@pytest.mark.anyio
async def test_second_claim_task_never_reissues_owner(graph, monkeypatch):
    c, port = await reserved_graph(graph)
    ready, proceed = asyncio.Event(), asyncio.Event()
    counts = controls(monkeypatch, ready=ready, proceed=proceed)
    driver = asyncio.create_task(claim._claim_activate_reserved_owned_v2(port))
    await asyncio.wait_for(ready.wait(), timeout=1.0)
    owner = c.claim_activate_owner_or_null
    assert claim._validate_claim_row_v2(c) == "CLAIM_WAIT"
    with pytest.raises(claim.ClaimActivateError):
        await claim._claim_activate_reserved_owned_v2(port)
    assert c.claim_activate_owner_or_null is owner
    proceed.set()
    assert await driver is not None
    assert counts["CLAIM"] == 1


@pytest.mark.anyio
async def test_cancel_cleanup_caller_does_not_cancel_release_task(graph, monkeypatch):
    c, port = await reserved_graph(graph)
    controls(monkeypatch)
    await claim._claim_activate_reserved_owned_v2(port)
    owner = c.claim_activate_owner_or_null
    original = _ClientGenerationLease.release
    ready, proceed = asyncio.Event(), asyncio.Event()
    async def release(lease):
        ready.set()
        await proceed.wait()
        return await original(lease)
    monkeypatch.setattr(_ClientGenerationLease, "release", release)
    retire = asyncio.create_task(claim._retire_active_owned_v2(port))
    await asyncio.wait_for(ready.wait(), timeout=1.0)
    assert claim._validate_claim_row_v2(c) == "ACTIVE_CLEANUP_PENDING"
    retire.cancel()
    await asyncio.sleep(0)
    assert not owner.cleanup_task_or_null.cancelled() and not owner.cleanup_task_or_null.done()
    proceed.set()
    assert await retire is not None
    assert c.claim_activate_owner_or_null is None


@pytest.mark.anyio
@pytest.mark.parametrize("expired_before_abandon", [False, True])
async def test_abandon_operation_and_wire_counts_are_distinct(graph, monkeypatch, expired_before_abandon):
    import json
    c, port = await reserved_graph(graph)
    session = graph.session
    invocation = graph.store._lease_bundle_cell_v2.exact_tuple[0].owner_invocation_id
    wire = {"CLAIM": 0, "ABANDON": 0, "RELEASE": 0}
    operations = dict(wire)
    ready = asyncio.Event()
    def write(encoded):
        frame = json.loads(encoded[4:])
        operation = frame["type"]
        wire[operation] += 1
        if operation == "CLAIM":
            ready.set()
        else:
            session._acks[invocation].set_result("CANCELLED")
    original_control = BrokerAdmissionSession._control_with_registered_ack
    cancelled_seen = asyncio.Event()
    original_finish = BrokerAdmissionSession._finish_cancelled_control
    async def finish(task):
        cancelled_seen.set()
        return await original_finish(task)
    async def control(self, operation, *args, **kwargs):
        operations[operation] += 1
        return await original_control(self, operation, *args, **kwargs)
    original_apply = BrokerAdmissionSession._apply_control_result
    async def apply(self, lane, operation, text):
        result = await original_apply(self, lane, operation, text)
        if expired_before_abandon and operation == "CLAIM" and result is AdmissionStatus.GRANTED:
            lane.disposition = "EXPIRED"
        return result
    monkeypatch.setattr(session._writer, "write", write)
    monkeypatch.setattr(BrokerAdmissionSession, "_control_with_registered_ack", control)
    monkeypatch.setattr(BrokerAdmissionSession, "_apply_control_result", apply)
    monkeypatch.setattr(BrokerAdmissionSession, "_finish_cancelled_control", staticmethod(finish))
    driver = asyncio.create_task(claim._claim_activate_reserved_owned_v2(port))
    await asyncio.wait_for(ready.wait(), timeout=1.0)
    c.claim_activate_cancel_event.set()
    await asyncio.wait_for(cancelled_seen.wait(), timeout=1.0)
    session._acks[invocation].set_result("GRANTED")
    assert await driver is not None
    assert c.claim_activate_outcome_or_null == "CLAIM_CANCELLED_ABANDONED"
    assert operations == dict(CLAIM=1, ABANDON=1, RELEASE=0)
    assert wire == dict(CLAIM=1, ABANDON=0 if expired_before_abandon else 1, RELEASE=0)


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["raise", "cancel", "mixed"])
async def test_abandon_uncertain_never_invalidates(graph, monkeypatch, failure):
    c, port = await reserved_graph(graph)
    ready, proceed = asyncio.Event(), asyncio.Event()
    counts = controls(monkeypatch, ready=ready, proceed=proceed,
        abandon_error=RuntimeError("synthetic") if failure == "raise" else asyncio.CancelledError() if failure == "cancel" else None)
    if failure == "mixed":
        original = BrokerAdmissionSession._apply_control_result
        async def apply(session, lane, operation, text):
            result = await original(session, lane, operation, text)
            if operation == "ABANDON":
                c.claim_activate_owner_or_null.exact_client_lease._retired = False
            return result
        monkeypatch.setattr(BrokerAdmissionSession, "_apply_control_result", apply)
    current = graph.store._lease_bundle_cell_v2
    driver = asyncio.create_task(claim._claim_activate_reserved_owned_v2(port))
    await asyncio.wait_for(ready.wait(), timeout=1.0)
    c.claim_activate_cancel_event.set()
    await asyncio.wait_for(counts.cancelled_control_seen.wait(), timeout=1.0)
    proceed.set()
    assert await driver is None
    assert c.flow_state == "CLAIM_RESULT_UNKNOWN"
    assert graph.store._lease_bundle_cell_v2 is current
    assert graph.claim_effects["invalid_cas"] == 0
    assert counts["CLAIM"] == counts["ABANDON"] == 1
    assert counts["RELEASE"] == counts["ACTIVATE"] == 0


@pytest.mark.anyio
async def test_abandon_settlement_timeout_preserves_exact_tasks(graph, monkeypatch):
    graph.session._config = replace(graph.session._config, cancellation_grace_seconds=0.03)
    c, port = await reserved_graph(graph, ttl=0.2)
    ready, proceed, abandon_ready, abandon_proceed = (asyncio.Event() for _ in range(4))
    counts = controls(monkeypatch, ready=ready, proceed=proceed,
        abandon_ready=abandon_ready, abandon_proceed=abandon_proceed)
    current = graph.store._lease_bundle_cell_v2
    driver = asyncio.create_task(claim._claim_activate_reserved_owned_v2(port))
    await asyncio.wait_for(ready.wait(), timeout=1.0)
    c.claim_activate_cancel_event.set()
    await asyncio.wait_for(counts.cancelled_control_seen.wait(), timeout=1.0)
    proceed.set()
    await asyncio.wait_for(abandon_ready.wait(), timeout=1.0)
    assert await driver is None
    owner = c.claim_activate_owner_or_null
    assert c.flow_state == "CLAIM_RESULT_UNKNOWN"
    assert owner.observation.failure_code_or_null == "CLAIM_SETTLE_TIMEOUT"
    assert not owner.claim_task_or_null.done()
    assert graph.store._lease_bundle_cell_v2 is current
    assert graph.claim_effects["invalid_cas"] == 0
    abandon_proceed.set()
    await asyncio.gather(owner.claim_task_or_null, return_exceptions=True)
    assert c.flow_state == "CLAIM_RESULT_UNKNOWN"
    assert counts["CLAIM"] == counts["ABANDON"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("entry", ["terminal", "release", "active_release"])
@pytest.mark.parametrize("field", ["lifecycle", "flow_state", "initial_invocation_slot", "owner_state"])
async def test_i2_i3_entry_closed_state_each_field(graph, monkeypatch, entry, field):
    c, port = await reserved_graph(graph)
    def stale():
        graph.world._current_deadline = replace(graph.world._current_deadline, mapping_order=900)
    counts = controls(monkeypatch, claim_status="CANCELLED" if entry == "terminal" else "GRANTED",
        after_claim=stale if entry == "release" else None)
    if entry == "active_release":
        await claim._claim_activate_reserved_owned_v2(port)
    current = graph.store._lease_bundle_cell_v2
    original = type(graph.store)._cas_claim_invalidated_v2
    def mutate(store, expected, candidate, owner):
        if field == "owner_state":
            object.__setattr__(owner, "state", "FOREIGN")
        else:
            setattr(c, field, "FOREIGN")
        return original(store, expected, candidate, owner)
    monkeypatch.setattr(type(graph.store), "_cas_claim_invalidated_v2", mutate)
    result = await (claim._retire_active_owned_v2(port) if entry == "active_release"
                    else claim._claim_activate_reserved_owned_v2(port))
    assert result is None
    assert graph.store._lease_bundle_cell_v2 is current
    assert c.flow_state == ("ACTIVE_ABORT_CONFLICT_HOLD" if entry == "active_release" else "CLAIM_ABORT_CONFLICT_HOLD")
    assert counts["CLAIM"] == 1
    assert counts["RELEASE"] == (0 if entry == "terminal" else 1)


async def preclaim_hold_graph(g, monkeypatch, kind):
    c, port = await reserved_graph(g)
    r = g.store._lease_bundle_cell_v2.exact_tuple[-1]
    g.world._current_deadline = replace(g.world._current_deadline, mapping_order=900)
    if kind == "conflict":
        def conflict(store, *args):
            store._lease_bundle_cell_v2 = object()
            return False
        monkeypatch.setattr(type(g.store), "_cas_claim_invalidated_v2", conflict)
    else:
        original = claim._validate_i2_post
        def post(c, receipt, outcome):
            original(c, receipt, outcome)
            raise claim.ClaimActivateError("SYNTHETIC_I2_POSTCHECK")
        monkeypatch.setattr(claim, "_validate_i2_post", post)
    assert await claim._claim_activate_reserved_owned_v2(port) is None
    assert c.claim_activate_preclaim_hold_or_null is r
    return c, port


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["conflict", "postcheck"])
async def test_preclaim_hold_keeps_exact_receipt_through_gc(graph, monkeypatch, kind):
    import gc
    import weakref
    c, port = await preclaim_hold_graph(graph, monkeypatch, kind)
    counts = controls(monkeypatch)
    receipt_ref = weakref.ref(c.claim_activate_preclaim_hold_or_null)
    gc.collect()
    assert receipt_ref() is c.claim_activate_preclaim_hold_or_null
    assert receipt_ref().exact_preparing_receipt.exact_composition is c
    assert c.claim_activate_owner_or_null is None
    assert claim._validate_claim_row_v2(c) == ("CLAIM_ABORT_CONFLICT_HOLD" if kind == "conflict" else "I2_POSTCHECK_UNKNOWN")
    with pytest.raises(claim.ClaimActivateError):
        await claim._claim_activate_reserved_owned_v2(port)
    assert not any(counts.values())


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["conflict", "postcheck"])
@pytest.mark.parametrize("mutation", ["early_clear", "foreign", "copy", "backlink", "post_expected", "lease", "capture"])
async def test_preclaim_hold_one_field_mutation_never_recovers(graph, monkeypatch, kind, mutation):
    c, port = await preclaim_hold_graph(graph, monkeypatch, kind)
    counts = controls(monkeypatch)
    r = c.claim_activate_preclaim_hold_or_null
    current = graph.store._lease_bundle_cell_v2
    if mutation == "early_clear":
        c.claim_activate_preclaim_hold_or_null = None
    elif mutation == "foreign":
        c.claim_activate_preclaim_hold_or_null = object()
    elif mutation == "copy":
        values = {name: getattr(r, name) for name in r.__slots__ if name not in ("__weakref__", "_issuer")}
        c.claim_activate_preclaim_hold_or_null = claim.reserved.ReservedCaptureOwnerReceiptV2(claim.reserved._ISSUER, **values)
    elif mutation == "backlink":
        object.__setattr__(r.exact_preparing_receipt, "exact_composition", object())
    elif mutation == "post_expected":
        object.__setattr__(r.postcheck_observation, "expected_receipt", object())
    elif mutation == "lease":
        object.__setattr__(r, "exact_lease", replace(r.exact_lease))
    else:
        object.__setattr__(r, "exact_capture", replace(r.exact_capture))
    with pytest.raises(claim.ClaimActivateError):
        claim._validate_claim_row_v2(c)
    with pytest.raises(claim.ClaimActivateError):
        await claim._claim_activate_reserved_owned_v2(port)
    assert graph.store._lease_bundle_cell_v2 is current
    assert not any(counts.values())


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["conflict", "postcheck"])
async def test_preclaim_hold_graph_collects_after_acknowledged_harness_teardown(graph, monkeypatch, kind):
    """Model a later terminal owner teardown; no recovery API is added to T540."""
    import gc
    import weakref
    c, port = await preclaim_hold_graph(graph, monkeypatch, kind)
    comp_ref = weakref.ref(c)
    receipt_ref = weakref.ref(c.claim_activate_preclaim_hold_or_null)
    invocation = c.claim_activate_preclaim_hold_or_null.exact_lease.owner_invocation_id
    # The real finite broker acknowledges offered-lease cancellation before the
    # test harness releases terminal owner roots. This is not T540 recovery.
    assert await graph.session.cancel(invocation) is AdmissionStatus.CANCELLED
    assert invocation not in graph.session._control_lanes and invocation not in graph.session._acks
    c.claim_activate_preclaim_hold_or_null = None
    for owner in (graph.store, graph.session, graph.arbiter):
        owner._offer_preparing_composition_v2 = None
    graph.store._lease_bundle_cell_v2 = None
    del c, port
    gc.collect()
    assert comp_ref() is None and receipt_ref() is None


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["reserved", "active", "i2", "i3", "unknown"])
async def test_preclaim_hold_must_be_null_in_other_rows(graph, monkeypatch, stage):
    c, port = await reserved_graph(graph)
    original_receipt = graph.store._lease_bundle_cell_v2.exact_tuple[-1]
    controls(monkeypatch, claim_status="CANCELLED" if stage == "i2" else "GRANTED",
        release_status="UNAVAILABLE" if stage == "unknown" else "RELEASED")
    if stage != "reserved":
        await claim._claim_activate_reserved_owned_v2(port)
    if stage in ("i3", "unknown"):
        await claim._retire_active_owned_v2(port)
    assert c.claim_activate_preclaim_hold_or_null is None
    c.claim_activate_preclaim_hold_or_null = original_receipt
    with pytest.raises(claim.ClaimActivateError):
        claim._validate_claim_row_v2(c)


@pytest.mark.anyio
async def test_synchronous_build_rows_have_closed_validators(graph, monkeypatch):
    c, port = await reserved_graph(graph)
    assert claim._validate_claim_row_v2(c) == "RESERVED_IDLE"
    controls(monkeypatch)
    rows = []
    original_build = claim._build_active
    def build(owner):
        rows.append(claim._validate_claim_row_v2(c))
        result = original_build(owner)
        rows.append(claim._validate_claim_row_v2(c))
        return result
    original_cas = type(graph.store)._cas_active_lease_v2
    def cas(store, expected, candidate):
        rows.append(claim._validate_claim_row_v2(c))
        return original_cas(store, expected, candidate)
    monkeypatch.setattr(claim, "_build_active", build)
    monkeypatch.setattr(type(graph.store), "_cas_active_lease_v2", cas)
    assert await claim._claim_activate_reserved_owned_v2(port) is not None
    assert rows == ["CLAIM_GRANTED_BUILD", "CLAIM_GRANTED_BUILD", "ACTIVATION_ENTERED_LOCAL"]


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["CANCELLED", "GRANTED"])
async def test_terminal_before_cas_has_closed_validator(graph, monkeypatch, status):
    c, port = await reserved_graph(graph)
    def stale():
        graph.world._current_deadline = replace(graph.world._current_deadline, mapping_order=900)
    controls(monkeypatch, claim_status=status, after_claim=stale if status == "GRANTED" else None)
    original = claim._publish_reserved_invalidated_after_claim_v2
    rows = []
    def publish(port, owner):
        rows.append(claim._validate_claim_row_v2(c))
        return original(port, owner)
    monkeypatch.setattr(claim, "_publish_reserved_invalidated_after_claim_v2", publish)
    assert await claim._claim_activate_reserved_owned_v2(port) is not None
    assert rows == ["GRANTED_CLEAN_RELEASE" if status == "GRANTED" else "CLAIM_RETURNED_NON_GRANT_TERMINAL"]


@pytest.mark.anyio
@pytest.mark.parametrize("point", ["event", "claim_coroutine", "world_coroutine", "cancel_coroutine", "deadline_coroutine"])
async def test_each_prepublication_allocation_failure_is_claim_zero(graph, monkeypatch, point):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    original_event, original_watch = asyncio.Event, claim._gated_watch
    def fail(*args):
        raise MemoryError("synthetic")
    if point == "event":
        monkeypatch.setattr(asyncio, "Event", fail)
    elif point == "claim_coroutine":
        monkeypatch.setattr(claim, "_gated_claim", fail)
    else:
        kind = point.split("_")[0]
        def watch(owner, requested):
            if requested == kind:
                raise MemoryError("synthetic")
            return original_watch(owner, requested)
        monkeypatch.setattr(claim, "_gated_watch", watch)
    assert await claim._claim_activate_reserved_owned_v2(port) is None
    monkeypatch.setattr(asyncio, "Event", original_event)
    assert c.claim_activate_owner_or_null is None and c.claim_activate_preclaim_hold_or_null is None
    assert c.claim_activate_prepublication_hold_or_null is None
    assert c.flow_state == "RESERVED" and not any(counts.values())


@pytest.mark.anyio
async def test_cancelled_stop_caller_keeps_complete_arbiter_stop_owner(graph, monkeypatch):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    await claim._claim_activate_reserved_owned_v2(port)
    original = _ClientGenerationLease.release
    ready, proceed = asyncio.Event(), asyncio.Event()
    async def release(lease):
        ready.set()
        await proceed.wait()
        return await original(lease)
    monkeypatch.setattr(_ClientGenerationLease, "release", release)
    caller = asyncio.create_task(graph.arbiter.stop())
    await asyncio.wait_for(ready.wait(), timeout=1.0)
    assert graph.arbiter._stop_task is not None
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert not graph.arbiter._stop_task.done()
    proceed.set()
    await asyncio.wait_for(asyncio.shield(graph.arbiter._stop_task), timeout=1.0)
    assert graph.arbiter._stopped and graph.arbiter._controller_stop_task.done()
    assert c.claim_activate_owner_or_null is None
    assert c.claim_activate_outcome_or_null == "ACTIVE_CANCEL"
    assert counts["RELEASE"] == counts["EXIT"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["active", "i2", "i2_preclaim", "i3"])
@pytest.mark.parametrize("edge", ["store", "session", "arbiter", "caller", "port", "controller"])
async def test_review_postpublish_backlink_drift_preserves_unknown(graph, monkeypatch, phase, edge):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch, claim_status="CANCELLED" if phase == "i2" else "GRANTED")
    if phase == "i3":
        await claim._claim_activate_reserved_owned_v2(port)
    if phase == "i2_preclaim":
        monkeypatch.setattr(claim, "_fresh", lambda *args: False)
    method = "_cas_active_lease_v2" if phase == "active" else "_cas_claim_invalidated_v2"
    original = getattr(type(graph.store), method)
    foreign = object()
    def corrupt(store, *args):
        result = original(store, *args)
        if edge in ("store", "session", "arbiter"):
            setattr({"store": graph.store, "session": graph.session, "arbiter": graph.arbiter}[edge],
                    "_offer_preparing_composition_v2", foreign)
        elif edge == "caller":
            object.__setattr__(c.exact_caller_port, "_composition", foreign)
        elif edge == "port":
            object.__setattr__(port, "exact_composition", foreign)
        else:
            c.exact_controller = foreign
        return result
    monkeypatch.setattr(type(graph.store), method, corrupt)
    result = await (claim._retire_active_owned_v2(port) if phase == "i3"
                    else claim._claim_activate_reserved_owned_v2(port))
    assert result is None
    assert c.flow_state == {"active": "ACTIVE_POSTCHECK_UNKNOWN", "i2": "I2_POSTCHECK_UNKNOWN",
                            "i3": "I3_POSTCHECK_UNKNOWN", "i2_preclaim": "I2_POSTCHECK_UNKNOWN"}[phase]
    assert (c.claim_activate_owner_or_null is not None) is (phase != "i2_preclaim")
    if phase == "i2_preclaim":
        assert c.claim_activate_preclaim_hold_or_null is not None
    assert counts["CLAIM"] == (0 if phase == "i2_preclaim" else 1) and counts["RELEASE"] == (1 if phase == "i3" else 0)
    if phase == "i2" and edge == "store":
        assert c.claim_activate_owner_or_null.exact_reserved_receipt.postcheck_observation.observed_composition is foreign


async def review_unknown_row(graph, monkeypatch, row):
    c, port = await reserved_graph(graph)
    if row == "claim_result":
        controls(monkeypatch, claim_status="ALIEN")
    elif row == "claim_cleanup":
        controls(monkeypatch, release_error=RuntimeError("synthetic"),
                 after_claim=lambda: c.claim_activate_cancel_event.set())
    elif row == "active_cleanup":
        controls(monkeypatch, release_error=RuntimeError("synthetic"))
    else:
        controls(monkeypatch, claim_status="CANCELLED" if row.startswith("i2") else "GRANTED")
    if row == "active_post":
        monkeypatch.setattr(claim, "_postcheck_active_v2", lambda owner: "ACTIVE_OWNER")
    if row in ("active_cleanup", "i3_conflict", "i3_post"):
        assert await claim._claim_activate_reserved_owned_v2(port) is not None
    if row in ("i2_conflict", "i3_conflict"):
        monkeypatch.setattr(type(graph.store), "_cas_claim_invalidated_v2", lambda *args: False)
    elif row in ("i2_post", "i3_post"):
        name = "_validate_i2_post" if row == "i2_post" else "_validate_i3_post"
        def fail(*args):
            raise claim.ClaimActivateError("synthetic")
        monkeypatch.setattr(claim, name, fail)
    if row in ("active_cleanup", "i3_conflict", "i3_post"):
        await claim._retire_active_owned_v2(port)
    else:
        await claim._claim_activate_reserved_owned_v2(port)
    assert claim._validate_claim_row_v2(c) == c.flow_state
    return c


@pytest.mark.anyio
@pytest.mark.parametrize("row", ["claim_result", "claim_cleanup", "active_cleanup", "active_post",
                                  "i2_conflict", "i2_post", "i3_conflict", "i3_post"])
@pytest.mark.parametrize("field", ["claim_task_or_null", "world_watcher_task_or_null",
                                   "cancel_watcher_task_or_null", "deadline_watcher_task_or_null",
                                   "activation_context_or_null", "active_candidate_cell_or_null",
                                   "active_receipt_or_null", "active_cleanup_candidates_or_null",
                                   "active_cleanup_selection", "cleanup_task_or_null"])
async def test_review_unknown_row_rejects_foreign_or_missing_refs(graph, monkeypatch, row, field):
    c = await review_unknown_row(graph, monkeypatch, row)
    owner = c.claim_activate_owner_or_null
    original = getattr(owner, field)
    current = graph.store._lease_bundle_cell_v2
    effects = dict(graph.claim_effects)
    object.__setattr__(owner, field, object() if original is None or (row == "active_post" and field == "claim_task_or_null") else None)
    with pytest.raises(claim.ClaimActivateError):
        claim._validate_claim_row_v2(c)
    assert graph.store._lease_bundle_cell_v2 is current and graph.claim_effects == effects
    object.__setattr__(owner, field, original)


@pytest.mark.anyio
@pytest.mark.parametrize("row", ["active_cleanup", "active_post", "i3_conflict", "i3_post"])
@pytest.mark.parametrize("field", ["context_copy", "context_entered", "receipt_state", "post_state", "selection_state", "selection_reason", "selection_identity"])
async def test_review_active_unknown_exact_subrows(graph, monkeypatch, row, field):
    from ai_client.llm.admission_client import _LeaseActivation
    c = await review_unknown_row(graph, monkeypatch, row)
    owner = c.claim_activate_owner_or_null
    if field == "context_copy":
        target, name = owner, "activation_context_or_null"
        value = _LeaseActivation(owner.exact_session, owner.exact_request.invocation_id)
        value._entered = owner.activation_context_or_null._entered
    elif field == "context_entered":
        target, name = owner.activation_context_or_null, "_entered"
        value = not target._entered
    elif field == "receipt_state":
        target, name, value = owner.active_receipt_or_null, "state", "ARMED"
    elif field == "post_state":
        target, name = owner.active_receipt_or_null.postcheck_observation, "state"
        value = "RECORDED" if target.state == "EMPTY" else "EMPTY"
    else:
        target = owner.active_cleanup_selection
        name = {"selection_state": "state", "selection_reason": "selected_reason_or_null",
                "selection_identity": "selected_candidate_identity_or_null"}[field]
        value = "FOREIGN" if field != "selection_identity" else object()
    original = getattr(target, name)
    object.__setattr__(target, name, value)
    with pytest.raises(claim.ClaimActivateError):
        claim._validate_claim_row_v2(c)
    object.__setattr__(target, name, original)


@pytest.mark.anyio
@pytest.mark.parametrize("point", [0, 1, 2, 3, "tuple", "publish", "hold"])
async def test_review_preallocated_hold_survives_all_failure_boundaries(graph, monkeypatch, point):
    c, port = await reserved_graph(graph)
    counts = controls(monkeypatch)
    original_create = asyncio.create_task
    original_hold = claim.PrepublicationTaskHoldV2
    original_projection = claim._prepublication_projection
    returned = []
    def no_new_hold(*args, **kwargs):
        raise MemoryError("no allocations after task creation")
    def create(coro):
        if isinstance(point, int) and len(returned) == point:
            monkeypatch.setattr(claim, "PrepublicationTaskHoldV2", no_new_hold)
            raise MemoryError("create boundary")
        task = original_create(coro)
        returned.append(task)
        return task
    async def no_settle(*args):
        return False
    def fault(*args):
        monkeypatch.setattr(claim, "PrepublicationTaskHoldV2", no_new_hold)
        raise MemoryError("prepublication boundary")
    monkeypatch.setattr(asyncio, "create_task", create)
    monkeypatch.setattr(claim, "_settle", no_settle)
    if point == "tuple":
        monkeypatch.setattr(claim, "_prepublication_projection", fault)
    elif point == "publish":
        monkeypatch.setattr(claim, "_publish_claim_tasks", fault)
    elif point == "hold":
        monkeypatch.setattr(claim, "PrepublicationTaskHoldV2", no_new_hold)
    assert await claim._claim_activate_reserved_owned_v2(port) is None
    monkeypatch.setattr(claim, "PrepublicationTaskHoldV2", original_hold)
    monkeypatch.setattr(claim, "_prepublication_projection", original_projection)
    assert c.claim_activate_owner_or_null is None and not any(counts.values())
    hold = c.claim_activate_prepublication_hold_or_null
    if not returned:
        assert hold is None and c.flow_state == "RESERVED"
    else:
        assert not hold.exact_gate.is_set()
        assert claim._validate_claim_row_v2(c) == "PREPUBLICATION_TASK_UNKNOWN"
        assert all(getattr(hold, name) is task for name, task in zip(claim._TASK_SLOTS, returned))
        assert all(getattr(hold, name) is None for name in claim._TASK_SLOTS[len(returned):])
        assert (hold.exact_tasks_or_null is not None) is (point == "publish")
        await asyncio.gather(*returned, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["foreign", "hole", "duplicate", "tuple_order", "tuple_only"])
async def test_review_prepublication_hold_closed_slots(graph, monkeypatch, mutation):
    c, port = await reserved_graph(graph)
    controls(monkeypatch)
    def fail(*args):
        raise MemoryError("publish")
    async def unsettled(*args):
        return False
    monkeypatch.setattr(claim, "_publish_claim_tasks", fail)
    monkeypatch.setattr(claim, "_settle", unsettled)
    await claim._claim_activate_reserved_owned_v2(port)
    hold = c.claim_activate_prepublication_hold_or_null
    tasks = hold.exact_tasks_or_null
    assert claim._validate_claim_row_v2(c) == "PREPUBLICATION_TASK_UNKNOWN"
    foreign = None
    if mutation == "foreign":
        foreign = asyncio.create_task(asyncio.sleep(0))
        object.__setattr__(hold, "claim_task_or_null", foreign)
    elif mutation in ("hole", "tuple_only"):
        for name in (claim._TASK_SLOTS if mutation == "tuple_only" else ("claim_task_or_null",)):
            object.__setattr__(hold, name, None)
    elif mutation == "duplicate":
        object.__setattr__(hold, "world_watcher_task_or_null", tasks[0])
    else:
        object.__setattr__(hold, "exact_tasks_or_null", tuple(reversed(tasks)))
    with pytest.raises(claim.ClaimActivateError):
        claim._validate_claim_row_v2(c)
    await asyncio.gather(*tasks, *([foreign] if foreign else []), return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("origin", ["start", "raise", "cancel"])
async def test_review_release_origin_cannot_downgrade_task_shape(graph, monkeypatch, origin):
    c, port = await reserved_graph(graph)
    controls(monkeypatch)
    await claim._claim_activate_reserved_owned_v2(port)
    owner = c.claim_activate_owner_or_null
    original_create = asyncio.create_task
    async def broken(lease):
        if origin == "cancel":
            raise asyncio.CancelledError()
        raise RuntimeError("synthetic")
    def start_fail(coro):
        raise MemoryError("synthetic")
    if origin == "start":
        monkeypatch.setattr(asyncio, "create_task", start_fail)
    else:
        monkeypatch.setattr(_ClientGenerationLease, "release", broken)
    assert await claim._retire_active_owned_v2(port) is None
    monkeypatch.setattr(asyncio, "create_task", original_create)
    code = {"start": "RELEASE_START_RAISED", "raise": "RELEASE_TASK_RAISED", "cancel": "RELEASE_TASK_CANCELLED"}[origin]
    assert owner.observation.failure_code_or_null == code
    assert claim._validate_claim_row_v2(c) == "ACTIVE_CLEANUP_UNKNOWN"
    original = owner.cleanup_task_or_null
    if origin == "start":
        foreign = asyncio.create_task(broken(None))
        await asyncio.gather(foreign, return_exceptions=True)
        object.__setattr__(owner, "cleanup_task_or_null", foreign)
    else:
        object.__setattr__(owner, "cleanup_task_or_null", None)
    with pytest.raises(claim.ClaimActivateError):
        claim._validate_claim_row_v2(c)
    object.__setattr__(owner, "cleanup_task_or_null", original)
    object.__setattr__(owner.observation, "failure_code_or_null", "RELEASE_TASK_RAISED" if origin == "start" else "RELEASE_START_RAISED")
    with pytest.raises(claim.ClaimActivateError):
        claim._validate_claim_row_v2(c)


async def review_partial_build(graph, monkeypatch, point):
    from ai_client.llm.admission_client import _LeaseActivation
    c, port = await reserved_graph(graph)
    controls(monkeypatch, release_error=RuntimeError("release uncertainty"))
    def fail(*args, **kwargs):
        raise MemoryError("build boundary")
    if point == "activate":
        target, name = _ClientGenerationLease, "activate"
    elif point == "identity":
        target, name = claim.reserved, "ActiveBundleIdentityV2"
    elif point == "enter":
        target, name = _LeaseActivation, "__enter__"
    elif point == "cas":
        target, name = type(graph.store), "_cas_active_lease_v2"
    elif point == "tuple":
        target, name = claim, "_freeze_active_candidates"
    elif isinstance(point, int):
        target, name = claim, "ActiveInvalidatedOwnerReceiptV2"
    else:
        target, name = claim, point
    original = getattr(target, name)
    attempts = 0
    def nth(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == point:
            return fail()
        return original(*args, **kwargs)
    monkeypatch.setattr(target, name, nth if isinstance(point, int) else fail)
    assert await claim._claim_activate_reserved_owned_v2(port) is None
    monkeypatch.setattr(target, name, original)
    assert c.flow_state == "CLAIM_CLEANUP_UNKNOWN"
    owner = c.claim_activate_owner_or_null
    assert claim._validate_claim_row_v2(c) == "CLAIM_CLEANUP_UNKNOWN"
    return c, owner


@pytest.mark.anyio
@pytest.mark.parametrize("point,origin", [
    ("activate", "BUILD_NOT_STARTED"), ("identity", "BUILD_CONTEXT_ONLY"),
    ("ActivePostcheckObservationV2", "BUILD_CONTEXT_ONLY"),
    ("ActiveCleanupSelectionV2", "BUILD_CONTEXT_ONLY"),
    ("ActiveCaptureOwnerReceiptV2", "BUILD_CONTEXT_ONLY"),
    (1, "BUILD_CELL_BOUND"), (2, "BUILD_CELL_BOUND"), (3, "BUILD_CELL_BOUND"), (4, "BUILD_CELL_BOUND"),
    ("ActiveCleanupCandidateV2", "BUILD_CELL_BOUND"), ("tuple", "BUILD_CELL_BOUND"),
    ("enter", "BUILD_CANDIDATES_BOUND"), ("cas", "BUILD_ENTERED")])
async def test_review_partial_build_origin_and_prefix_registry(graph, monkeypatch, point, origin):
    c, owner = await review_partial_build(graph, monkeypatch, point)
    assert owner.build_origin == origin
    if origin in ("BUILD_NOT_STARTED", "BUILD_CONTEXT_ONLY", "BUILD_CELL_BOUND"):
        assert owner.active_cleanup_candidates_or_null is None
        assert not any(ref() is not None and ref().stage == "ACTIVE_INVALIDATED"
                       for ref in graph.store._lease_bundle_cells_v2.values())
    else:
        assert len(owner.active_cleanup_candidates_or_null) == 4
    if owner.activation_context_or_null is not None:
        assert owner.activation_context_or_null._entered is False
    assert graph.claim_effects["invalid_cas"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("point", ["activate", "identity", 2, "enter", "cas"])
@pytest.mark.parametrize("field", ["build_origin", "activation_context_or_null", "active_candidate_cell_or_null",
                                   "active_receipt_or_null", "active_cleanup_selection", "active_cleanup_candidates_or_null"])
async def test_review_partial_origin_rejects_nullable_shape_downgrade(graph, monkeypatch, point, field):
    c, owner = await review_partial_build(graph, monkeypatch, point)
    original = getattr(owner, field)
    value = ("BUILD_CONTEXT_ONLY" if original == "BUILD_NOT_STARTED" else "BUILD_NOT_STARTED") if field == "build_origin" else (None if original is not None else object())
    object.__setattr__(owner, field, value)
    with pytest.raises(claim.ClaimActivateError):
        claim._validate_claim_row_v2(c)
    object.__setattr__(owner, field, original)


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["active", "i2", "i3"])
@pytest.mark.parametrize("field", ["exact_owner_loop", "exact_claim_driver_task"])
async def test_review_postpublish_owner_graph_is_rechecked(graph, monkeypatch, phase, field):
    c, port = await reserved_graph(graph)
    controls(monkeypatch, claim_status="CANCELLED" if phase == "i2" else "GRANTED")
    if phase == "i3":
        await claim._claim_activate_reserved_owned_v2(port)
    method = "_cas_active_lease_v2" if phase == "active" else "_cas_claim_invalidated_v2"
    original = getattr(type(graph.store), method)
    def corrupt(store, *args):
        result = original(store, *args)
        object.__setattr__(c.claim_activate_owner_or_null, field, object())
        return result
    monkeypatch.setattr(type(graph.store), method, corrupt)
    result = await (claim._retire_active_owned_v2(port) if phase == "i3" else claim._claim_activate_reserved_owned_v2(port))
    assert result is None and c.claim_activate_owner_or_null is not None
    assert c.flow_state == {"active": "ACTIVE_POSTCHECK_UNKNOWN", "i2": "I2_POSTCHECK_UNKNOWN", "i3": "I3_POSTCHECK_UNKNOWN"}[phase]
