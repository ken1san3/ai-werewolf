from __future__ import annotations

import asyncio
import copy
import json
import pickle
import time
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from ai_client.brain.controller import BrainController
from ai_client.brain.invocation import BrainInvocationArbiter
from ai_client.discussion import offer_composition_v2 as module
from ai_client.discussion.offer_composition_v2 import OfferCompositionError
from ai_client.llm.admission_broker import GenerationAdmissionBroker
from ai_client.llm.admission_client import BrokerAdmissionSession
from ai_client.llm.admission_types import AdmissionCredentials
from ai_client.llm.admission_types import AdmissionStatus
from ai_client.llm.types import LLMBackendError
from ai_client.runtime import _create_phase6_v2_offer_composition
from tests.test_phase5_generation_admission import _FakeBackend, _request, _token
from tests.test_phase6_authority_capture_bridge_v2 import composed_bridge, close_composed
from tests.test_phase6_discussion_transaction import _AuditSink, _ControllerBrain


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def graph():
    values = await composed_bridge()
    socket, network_task, world_task, source, world, store, capture, bridge = values
    backend = _FakeBackend()
    broker = GenerationAdmissionBroker({"offline-owner": _token(81)}, backend,
                                       fairness_seed="t523")
    session = None
    try:
        await broker.start()
        session = await BrokerAdmissionSession.connect(
            broker.ready.host, broker.ready.port,
            AdmissionCredentials("offline-owner", _token(81)))
        brain = _ControllerBrain(None)
        controller = BrainController(world=world, sender=source._network, brain=brain,
                                     discussion_state=store, discussion_audit=_AuditSink(),
                                     clock=time.monotonic)
        arbiter = BrainInvocationArbiter(controller=controller, admission=session,
                                        clock=time.monotonic)
        yield SimpleNamespace(session=session, source=source, world=world, store=store,
                              capture=capture, bridge=bridge, arbiter=arbiter,
                              controller=controller, backend=backend, brain=brain)
    finally:
        if session is not None:
            await session.aclose()
        await broker.aclose()
        await close_composed(socket, network_task, world_task, source)


def bind(g):
    return _create_phase6_v2_offer_composition(g.bridge, g.arbiter)


def assert_unpublished(g):
    assert g.session._offer_preparing_composition_v2 is None
    assert g.store._offer_preparing_composition_v2 is None
    assert g.arbiter._offer_preparing_composition_v2 is None
    assert g.session._offer_preparing_mode_v2 is False
    assert g.store._offer_preparing_mode_v2 is False
    assert g.arbiter._offer_preparing_mode_v2 is False
    assert not g.session._results and not g.session._control_lanes
    assert not g.backend.calls and not g.brain.calls


@pytest.mark.anyio
async def test_actual_composition_is_idle_read_only_and_one_shot(graph):
    g = graph
    before = (g.store.snapshot, g.store._current_capture, g.world.snapshot())
    port = bind(g)
    c = port._composition
    port.validate_idle()
    assert c is g.session._offer_preparing_composition_v2
    assert c is g.store._offer_preparing_composition_v2
    assert c is g.arbiter._offer_preparing_composition_v2
    assert c.exact_caller_port is port
    assert c.initial_invocation_slot == "EMPTY"
    assert c.flow_state == "IDLE" and c.lifecycle == "ACTIVE"
    assert c.active_initial_ticket_or_null is None and c.active_offer_source_or_null is None
    assert c.exact_context_receipt is g.source.context_owner_receipt_v2
    assert c.exact_owner_registration is g.bridge._registration
    assert c.exact_clock_callable is g.arbiter._clock is g.controller._clock
    assert before == (g.store.snapshot, g.store._current_capture, g.world.snapshot())
    assert not hasattr(port, "acquire_and_prepare_initial_v2")
    assert g.store._state_generation_lease_v2 is None
    assert g.store._preparing_lease_owner_receipt_v2 is None
    with pytest.raises(OfferCompositionError, match="ALREADY_BOUND"):
        bind(g)
    assert not g.backend.calls and not g.brain.calls


@pytest.mark.anyio
async def test_partial_mode_blocks_execution_before_any_queue_or_pending(graph):
    g = graph
    bind(g)
    request = _request("no-call")
    for operation in (g.session.acquire(request),
                      g.session.replace_waiting("old", request),
                      g.session.reserve_successor("old", request)):
        with pytest.raises(LLMBackendError):
            await operation
    with pytest.raises(OfferCompositionError, match="INITIAL_TICKET_NOT_CONNECTED"):
        await g.arbiter.invoke(owner="reaction_chat", priority=None, allowed_handles=(),
                               timeout_seconds=1, dispatch_deadline=None)
    assert g.arbiter._active is None and not g.arbiter._pending
    assert g.arbiter._admission_driver is None
    assert not g.session._results and not g.session._control_lanes
    assert not g.backend.calls and not g.brain.calls


MUTATIONS = [
    ("arbiter", "_admission", object()),
    ("arbiter", "_clock", lambda: 0),
    ("controller", "_clock", lambda: 0),
    ("controller", "world", object()),
    ("controller", "sender", object()),
    ("controller._discussion", "state", object()),
    ("controller._discussion", "_closed", True),
    ("session", "_closed", True),
    ("session", "_transport_closed", True),
    ("session", "_claimed_invocation", "foreign"),
    ("session", "_activated_invocation", "foreign"),
    ("session", "_results", {"foreign": object()}),
    ("session", "_control_lanes", {"foreign": object()}),
    ("session", "_acks", {"foreign": object()}),
    ("session", "_terminal_controls", {"foreign": object()}),
    ("session", "_attachments", {"foreign": object()}),
    ("session", "_generations", {"foreign": object()}),
    ("arbiter", "_pending", {"foreign": object()}),
    ("arbiter", "_active", object()),
    ("arbiter", "_active_task", object()),
    ("arbiter", "_admission_driver", object()),
    ("arbiter", "_admission_wait_task", object()),
    ("arbiter", "_admission_brain_task", object()),
    ("arbiter", "_admission_lease", object()),
    ("arbiter", "_attached_successor", object()),
    ("arbiter", "_suspended_reaction", object()),
    ("arbiter", "_stopped", True),
    ("arbiter", "_poisoned", True),
    ("arbiter", "_admission_closed", True),
    ("arbiter", "_admission_state", "WAITING_ADMISSION"),
    ("store", "_closed", True),
    ("store", "_staged", object()),
    ("store", "_committed", object()),
    ("store", "_dispatch", object()),
    ("store", "_delivery", object()),
    ("source", "_retired", True),
    ("bridge._registration", "lifecycle", "RETIRED"),
    ("bridge._world_port", "_bridge", object()),
    ("bridge._capture_port", "_bridge", object()),
    ("bridge._capture_port", "_bound", object()),
]


def resolve(g, path):
    for name in path.split("."):
        g = getattr(g, name)
    return g


@pytest.mark.anyio
@pytest.mark.parametrize("path,field,value", MUTATIONS, ids=[f"{p}.{f}" for p,f,_ in MUTATIONS])
async def test_wrong_or_busy_owner_rejected_before_and_after_binding(graph, path, field, value):
    g = graph
    target = resolve(g, path)
    original = getattr(target, field)
    try:
        setattr(target, field, value)
        with pytest.raises(OfferCompositionError):
            bind(g)
    finally:
        setattr(target, field, original)
    assert_unpublished(g)
    port = bind(g)
    try:
        setattr(target, field, value)
        with pytest.raises(OfferCompositionError):
            port.validate_idle()
    finally:
        setattr(target, field, original)
    port.validate_idle()


@pytest.mark.anyio
@pytest.mark.parametrize("owner", ["session", "store", "arbiter"])
async def test_close_retires_identity_and_never_rebinds(graph, owner):
    g = graph
    port = bind(g)
    if owner == "session":
        await g.session.aclose()
    elif owner == "store":
        g.store.close()
    else:
        await g.arbiter.stop()
    assert port._composition.lifecycle == "RETIRED"
    with pytest.raises(OfferCompositionError):
        port.validate_idle()
    with pytest.raises(OfferCompositionError):
        bind(g)
    assert getattr(g, owner)._offer_preparing_composition_v2 is port._composition


@pytest.mark.anyio
@pytest.mark.parametrize("class_name", ["OfferPreparingCallerPortV2", "OfferPreparingCompositionV2",
                                        "PreTicketAbortBundleV2"])
async def test_candidate_allocation_failure_publishes_nothing(graph, monkeypatch, class_name):
    def fail(*args, **kwargs):
        raise MemoryError("synthetic allocation failure")
    with monkeypatch.context() as patch:
        patch.setattr(getattr(module, class_name), "__init__", fail)
        with pytest.raises(MemoryError):
            bind(graph)
    assert_unpublished(graph)
    bind(graph).validate_idle()


@pytest.mark.anyio
async def test_final_owner_recheck_precedes_publication(graph, monkeypatch):
    original = module._validate_edges
    count = 0
    def change_before_second(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise OfferCompositionError("synthetic late validation failure")
        return original(*args)
    with monkeypatch.context() as patch:
        patch.setattr(module, "_validate_edges", change_before_second)
        with pytest.raises(OfferCompositionError):
            bind(graph)
    assert count == 2
    assert_unpublished(graph)


@pytest.mark.anyio
async def test_owner_graph_is_opaque_and_wrong_loop_rejected(graph):
    port = bind(graph)
    for obj in (port, port._composition):
        assert repr(obj).endswith("(<opaque>)")
        assert "offline-owner" not in repr(obj)
        for operation in (pickle.dumps, json.dumps, asdict, copy.copy, copy.deepcopy):
            with pytest.raises(TypeError):
                operation(obj)
    async def other_loop():
        with pytest.raises(OfferCompositionError):
            port.validate_idle()
    await asyncio.to_thread(lambda: asyncio.run(other_loop()))
    port.validate_idle()


@pytest.mark.anyio
async def test_foreign_public_shapes_and_subclass_do_not_bind(graph):
    with pytest.raises(OfferCompositionError):
        module._bind_offer_composition_v2(SimpleNamespace(), graph.bridge,
                                          graph.arbiter, time.monotonic)
    with pytest.raises(OfferCompositionError):
        module._bind_offer_composition_v2(graph.session, SimpleNamespace(),
                                          graph.arbiter, time.monotonic)
    class DerivedArbiter(BrainInvocationArbiter):
        pass
    derived = DerivedArbiter(controller=graph.controller, admission=graph.session)
    with pytest.raises(TypeError):
        _create_phase6_v2_offer_composition(graph.bridge, derived)
    assert_unpublished(graph)


@pytest.mark.anyio
@pytest.mark.parametrize("field,value", [
    ("lifecycle", "RETIRED"), ("flow_state", "OFFERED"),
    ("initial_invocation_slot", "PENDING_SELECTED"),
    ("initial_slot_identity", object()), ("issuer_capability", object()),
    ("active_initial_ticket_or_null", object()), ("active_offer_source_or_null", object()),
    ("exact_context_receipt", object()), ("exact_owner_registration", object()),
    ("exact_authority_capture_bridge", object()), ("exact_runtime_source", object()),
    ("exact_world", object()), ("exact_discussion_store", object()),
    ("exact_clock_callable", lambda: 0), ("exact_controller", object()),
    ("exact_discussion_transaction", object()), ("owner_thread_loop", (0, None)),
])
async def test_composition_identity_and_empty_matrix_tampering_rejected(graph, field, value):
    port = bind(graph)
    c = port._composition
    original = getattr(c, field)
    try:
        setattr(c, field, value)
        with pytest.raises(OfferCompositionError):
            port.validate_idle()
    finally:
        setattr(c, field, original)
    port.validate_idle()


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["session", "store", "arbiter"])
async def test_backlink_loss_is_not_fallback_to_v1(graph, path):
    port = bind(graph)
    owner = getattr(graph, path)
    c = owner._offer_preparing_composition_v2
    owner._offer_preparing_composition_v2 = None
    try:
        with pytest.raises(OfferCompositionError):
            port.validate_idle()
        with pytest.raises(OfferCompositionError):
            bind(graph)
        with pytest.raises(LLMBackendError):
            await graph.session.acquire(_request("lost-edge"))
        with pytest.raises(OfferCompositionError):
            await graph.arbiter.invoke(owner="reaction_chat", priority=None,
                allowed_handles=(), timeout_seconds=1, dispatch_deadline=None)
    finally:
        owner._offer_preparing_composition_v2 = c
    port.validate_idle()


@pytest.mark.anyio
async def test_unbound_v1_still_acquires_and_cancels_without_provider(graph):
    g = graph
    assert_unpublished(g)
    result = await g.session.acquire(_request("v1-unbound"))
    assert result.status is AdmissionStatus.OFFERED
    assert result.lease is not None
    assert await g.session.cancel("v1-unbound") is AdmissionStatus.CANCELLED
    assert not g.backend.calls
    assert g.session._offer_preparing_composition_v2 is None


@pytest.mark.anyio
async def test_same_store_replacement_transaction_and_controller_rejected(graph):
    port = bind(graph)
    from ai_client.discussion.transaction import DiscussionTransaction
    original_transaction = graph.controller._discussion
    graph.controller._discussion = DiscussionTransaction(state=graph.store, audit=_AuditSink())
    try:
        with pytest.raises(OfferCompositionError):
            port.validate_idle()
    finally:
        graph.controller._discussion = original_transaction
    original_controller = graph.arbiter.controller
    graph.arbiter.controller = BrainController(
        world=graph.world, sender=graph.source._network, brain=graph.brain,
        discussion_state=graph.store, discussion_audit=_AuditSink())
    try:
        with pytest.raises(OfferCompositionError):
            port.validate_idle()
    finally:
        graph.arbiter.controller = original_controller
    port.validate_idle()


@pytest.mark.anyio
async def test_abort_bundle_is_prebuilt_shared_immutable_and_exact(graph):
    from ai_client.brain.model import DecisionStatus
    from dataclasses import replace
    port = bind(graph)
    c = port._composition
    bundle = c.prebuilt_pre_ticket_abort_bundle
    assert bundle is port._abort_bundle
    assert bundle.original_empty_slot_identity is c.initial_slot_identity is port._initial_slot_identity
    result = bundle.failure_result
    assert result is port._abort_failure_result
    assert result.outcome.status is DecisionStatus.BRAIN_FAILED
    assert result.outcome.error_type == "OfferPreparingFailureV2"
    assert result.dispatched_decision is None
    assert "exact_discussion_transaction" in c.__slots__
    assert "exact_transaction" not in c.__slots__
    for operation in (pickle.dumps, json.dumps, asdict, copy.copy, copy.deepcopy):
        with pytest.raises(TypeError):
            operation(bundle)
    for field in bundle.__slots__:
        with pytest.raises(TypeError):
            setattr(bundle, field, object())
    for field, value in (("original_empty_slot_identity", object()),
                         ("failure_result", replace(result)), ("_issuer", object())):
        original = getattr(bundle, field)
        try:
            object.__setattr__(bundle, field, value)
            with pytest.raises(OfferCompositionError):
                port.validate_idle()
        finally:
            object.__setattr__(bundle, field, original)
    clone = object.__new__(module.PreTicketAbortBundleV2)
    for field in bundle.__slots__:
        object.__setattr__(clone, field, getattr(bundle, field))
    c.prebuilt_pre_ticket_abort_bundle = clone
    try:
        with pytest.raises(OfferCompositionError):
            port.validate_idle()
    finally:
        c.prebuilt_pre_ticket_abort_bundle = bundle
    port.validate_idle()


@pytest.mark.anyio
async def test_failure_result_allocation_failure_keeps_every_owner_unpublished(graph, monkeypatch):
    def fail():
        raise MemoryError("synthetic result allocation")
    with monkeypatch.context() as patch:
        patch.setattr(module, "_new_pre_ticket_failure_result_v2", fail)
        with pytest.raises(MemoryError):
            bind(graph)
    assert_unpublished(graph)
