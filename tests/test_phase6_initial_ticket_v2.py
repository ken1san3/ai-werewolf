from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
import json
import pickle
import time

import pytest

from ai_client.brain.invocation import (
    BrainInvocationPriority,
    _PendingInvocation,
)
from ai_client.brain.model import DecisionStatus, DispatchDeadline
from ai_client.discussion import offer_composition_v2 as module
from ai_client.discussion.offer_composition_v2 import (
    InitialOfferTicketV2,
    OfferCompositionError,
    OfferPreparingTerminalCandidatesV2,
    _build_initial_request_candidate_v2,
    _issue_initial_ticket_owned_v2,
)
from ai_client.llm.admission_types import AdmissionStatus, GenerationPriority
from tests.test_phase6_offer_composition_v2 import bind, graph


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_pending(g, *, owner="reaction_chat"):
    action = g.source.snapshot().actions[0]
    deadline = g.world.transport_observations().current_deadline
    assert deadline is not None
    dispatch = DispatchDeadline(
        mapping_order=deadline.mapping_order,
        phase=deadline.phase,
        day=deadline.day,
        connection_generation=deadline.connection_generation,
        action_generation=deadline.action_generation,
        not_after_monotonic=(deadline.local_deadline_monotonic
                             if deadline.local_deadline_monotonic is not None
                             else time.monotonic() + 10.0),
    )
    return _PendingInvocation(
        owner=owner,
        priority=(BrainInvocationPriority.REACTION if owner == "reaction_chat"
                  else BrainInvocationPriority.RESERVATION_ACTION),
        allowed_handles=(action,),
        timeout_seconds=1.0,
        dispatch_deadline=dispatch,
        on_brain_start=None,
        result=asyncio.get_running_loop().create_future(),
    )


async def issue_actual(g, port, pending):
    arbiter = g.arbiter
    async with arbiter._lock:
        arbiter._active = pending
        arbiter._admission_driver = asyncio.current_task()
        arbiter._admission_state = "WAITING_ADMISSION"
    return await _issue_initial_ticket_owned_v2(port, pending)


@pytest.mark.anyio
async def test_actual_pending_issues_one_closed_active_ticket_without_offer(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    ticket = await issue_actual(g, port, pending)
    assert type(ticket) is InitialOfferTicketV2
    assert ticket.exact_composition is port._composition
    assert ticket.exact_caller_port is port
    assert ticket.exact_arbiter is g.arbiter
    assert ticket.exact_pending_invocation is pending
    assert ticket.exact_owner == "reaction_chat"
    assert ticket.exact_dispatch_deadline is pending.dispatch_deadline
    assert ticket.exact_admission_driver_task is asyncio.current_task()
    assert ticket.exact_initial_slot is port._composition.initial_slot_identity
    assert ticket.exact_offer_task_or_null is None
    assert ticket.state == "ACTIVE"
    assert pending.admission_invocation_id == ticket.exact_request.invocation_id
    assert port._composition.initial_invocation_slot == "TICKET_ACTIVE"
    assert port._composition.initial_pending_or_null is pending
    assert port._composition.active_initial_ticket_or_null is ticket
    assert g.arbiter._admission_state == "V2_INITIAL_REGISTERED"
    assert not pending.result.done() and not pending.execution_complete.is_set()
    assert not g.session._results and not g.session._control_lanes
    assert not g.backend.calls and not g.brain.calls
    for operation in (pickle.dumps, json.dumps, copy.copy, copy.deepcopy):
        with pytest.raises(TypeError):
            operation(ticket)
    with pytest.raises(TypeError):
        ticket.state = "CONSUMED_SOURCE"
    module._retire_offer_composition_v2(g.session)
    assert ticket.state == "RETIRED_UNKNOWN"
    assert port._composition.initial_invocation_slot == "CLEANUP_UNKNOWN"


@pytest.mark.anyio
async def test_request_candidate_is_pure_and_exact(graph):
    g = graph
    pending = make_pending(g)
    request = _build_initial_request_candidate_v2(pending, "candidate-id")
    assert pending.admission_invocation_id is None
    assert request.invocation_id == "candidate-id"
    assert request.priority is GenerationPriority.REACTION
    assert request.phase == pending.dispatch_deadline.phase
    assert request.day == pending.dispatch_deadline.day
    assert request.action_generation == pending.dispatch_deadline.action_generation
    assert request.mapping_order == pending.dispatch_deadline.mapping_order
    assert request.not_after_monotonic == pending.dispatch_deadline.not_after_monotonic
    with pytest.raises((TypeError, OfferCompositionError)):
        _build_initial_request_candidate_v2(object(), "candidate-id")
    mutations = (
        ("owner", "foreign"),
        ("priority", BrainInvocationPriority.RESERVATION_ACTION),
        ("allowed_handles", ()),
        ("dispatch_deadline", object()),
    )
    for field, value in mutations:
        original = getattr(pending, field)
        setattr(pending, field, value)
        try:
            with pytest.raises(OfferCompositionError):
                _build_initial_request_candidate_v2(pending, "candidate-id")
        finally:
            setattr(pending, field, original)


@pytest.mark.anyio
async def test_terminal_candidates_are_prebuilt_closed_and_alias_abort_failure(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    ticket = await issue_actual(g, port, pending)
    bundle = ticket.terminal_candidates
    assert type(bundle) is OfferPreparingTerminalCandidatesV2
    assert bundle.internal_failure is port._abort_failure_result
    assert bundle.preparing_complete.outcome.status is DecisionStatus.CANCELLED
    assert bundle.stale.outcome.status is DecisionStatus.STALE
    assert bundle.deadline_suppressed.outcome.status is DecisionStatus.DEADLINE_SUPPRESSED
    assert set(bundle.admission_terminals) == {
        AdmissionStatus.REPLACED, AdmissionStatus.EXPIRED, AdmissionStatus.OVERLOADED,
        AdmissionStatus.UNAVAILABLE, AdmissionStatus.CANCELLED, AdmissionStatus.POISONED,
    }
    assert bundle.cleanup_unknown.outcome.error_type == "OfferCleanupUnknownV2"
    for operation in (pickle.dumps, json.dumps, copy.copy, copy.deepcopy):
        with pytest.raises(TypeError):
            operation(bundle)
    module._retire_offer_composition_v2(g.session)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field,value",
    [
        ("_admission_state", "IDLE"),
        ("_admission_wait_task", object()),
        ("_admission_lease", object()),
        ("_suspended_reaction", object()),
        ("_attached_successor", object()),
    ],
)
async def test_ticket_rejects_wrong_arbiter_state_without_partial_publish(graph, field, value):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    async with g.arbiter._lock:
        g.arbiter._active = pending
        g.arbiter._admission_driver = asyncio.current_task()
        g.arbiter._admission_state = "WAITING_ADMISSION"
    original = getattr(g.arbiter, field)
    setattr(g.arbiter, field, value)
    try:
        with pytest.raises(OfferCompositionError):
            await _issue_initial_ticket_owned_v2(port, pending)
    finally:
        setattr(g.arbiter, field, original)
    assert pending.admission_invocation_id is None
    assert port._composition.initial_invocation_slot == "EMPTY"
    assert port._composition.active_initial_ticket_or_null is None


@pytest.mark.anyio
async def test_foreign_pending_driver_and_unlocked_calls_are_rejected(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    foreign = make_pending(g)
    with pytest.raises(OfferCompositionError):
        await _issue_initial_ticket_owned_v2(port, pending)
    async with g.arbiter._lock:
        g.arbiter._active = pending
        g.arbiter._admission_driver = asyncio.current_task()
        g.arbiter._admission_state = "WAITING_ADMISSION"
    with pytest.raises(OfferCompositionError):
        await _issue_initial_ticket_owned_v2(port, foreign)
    g.arbiter._admission_driver = asyncio.create_task(asyncio.sleep(0))
    try:
        with pytest.raises(OfferCompositionError):
            await _issue_initial_ticket_owned_v2(port, pending)
    finally:
        g.arbiter._admission_driver.cancel()
    assert pending.admission_invocation_id is None
    assert port._composition.initial_invocation_slot == "EMPTY"


@pytest.mark.anyio
@pytest.mark.parametrize("allocation", ["attempt", "id", "request", "terminal", "ticket"])
async def test_candidate_failure_rolls_back_and_returns_exact_prebuilt_failure(
    graph, monkeypatch, allocation,
):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    if allocation == "attempt":
        monkeypatch.setattr(module.InitialTicketBuildAttemptV2, "__init__",
                            lambda *args, **kwargs: (_ for _ in ()).throw(MemoryError("attempt")))
    elif allocation == "id":
        monkeypatch.setattr(g.arbiter, "_invocation_id_factory",
                            lambda: (_ for _ in ()).throw(MemoryError("id")))
    else:
        target = {
            "request": "_build_initial_request_candidate_v2",
            "terminal": "_build_terminal_candidates_v2",
            "ticket": "InitialOfferTicketV2",
        }[allocation]
        if target == "InitialOfferTicketV2":
            monkeypatch.setattr(module.InitialOfferTicketV2, "__init__",
                                lambda *args, **kwargs: (_ for _ in ()).throw(MemoryError("ticket")))
        else:
            monkeypatch.setattr(module, target,
                                lambda *args, **kwargs: (_ for _ in ()).throw(MemoryError(target)))
    result = await issue_actual(g, port, pending)
    assert result is port._abort_failure_result
    assert pending.admission_invocation_id is None
    assert not pending.result.done() and not pending.execution_complete.is_set()
    assert port._composition.initial_invocation_slot == "EMPTY"
    assert port._composition.initial_pending_or_null is None
    assert port._composition.active_initial_ticket_or_null is None
    assert g.arbiter._active is None
    assert g.arbiter._admission_driver is None
    assert g.arbiter._admission_state == "IDLE"


@pytest.mark.anyio
async def test_duplicate_and_same_value_pending_are_rejected(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    ticket = await issue_actual(g, port, pending)
    duplicate = make_pending(g)
    with pytest.raises(OfferCompositionError):
        await _issue_initial_ticket_owned_v2(port, duplicate)
    with pytest.raises(OfferCompositionError):
        await _issue_initial_ticket_owned_v2(port, pending)
    assert port._composition.active_initial_ticket_or_null is ticket
    assert pending.admission_invocation_id == ticket.exact_request.invocation_id
    module._retire_offer_composition_v2(g.session)


@pytest.mark.anyio
async def test_v1_request_and_driver_remain_unmodified(graph):
    g = graph
    pending = make_pending(g)
    request = g.arbiter._new_admission_request(pending)
    assert pending.admission_invocation_id == request.invocation_id
    assert g.arbiter._offer_preparing_composition_v2 is None
    assert g.arbiter._offer_preparing_mode_v2 is False


@pytest.mark.anyio
async def test_foreign_task_holding_lock_blocks_exact_driver_without_publish(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    async with g.arbiter._lock:
        g.arbiter._active = pending
        g.arbiter._admission_driver = asyncio.current_task()
        g.arbiter._admission_state = "WAITING_ADMISSION"
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with g.arbiter._lock:
            entered.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await entered.wait()
    try:
        with pytest.raises(OfferCompositionError, match="LOCK_OWNER"):
            await _issue_initial_ticket_owned_v2(port, pending)
    finally:
        release.set()
        await task
    assert pending.admission_invocation_id is None
    assert port._composition.initial_invocation_slot == "EMPTY"
    assert port._composition.active_ticket_build_attempt_or_null is None
    assert port._composition.active_initial_ticket_or_null is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "target,field,value",
    [
        ("composition", "_issuer", object()),
        ("composition", "owner_thread_loop", (0, None)),
        ("composition", "exact_runtime_source", object()),
        ("composition", "exact_world", object()),
        ("composition", "exact_discussion_store", object()),
        ("composition", "exact_authority_capture_bridge", object()),
        ("composition", "exact_context_receipt", object()),
        ("composition", "exact_controller", object()),
        ("composition", "exact_discussion_transaction", object()),
        ("composition", "exact_clock_callable", lambda: 0.0),
        ("port", "_issuer", object()),
        ("port", "_arbiter", object()),
        ("port", "_capability", object()),
        ("port", "_initial_slot_identity", object()),
        ("arbiter", "controller", object()),
        ("controller", "_discussion", object()),
        ("bridge._world_port", "_bridge", object()),
        ("bridge._capture_port", "_receipt_capability", object()),
        ("store", "_owner_token", (0, None)),
        ("composition", "prebuilt_pre_ticket_abort_bundle", object()),
    ],
)
async def test_immutable_owner_edge_mutation_rejects_ticket_publish(
    graph, target, field, value,
):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    async with g.arbiter._lock:
        g.arbiter._active = pending
        g.arbiter._admission_driver = asyncio.current_task()
        g.arbiter._admission_state = "WAITING_ADMISSION"
    subject = {"composition": port._composition, "port": port,
               "arbiter": g.arbiter, "controller": g.controller,
               "store": g.store, "bridge._world_port": g.bridge._world_port,
               "bridge._capture_port": g.bridge._capture_port}[target]
    original = getattr(subject, field)
    object.__setattr__(subject, field, value)
    try:
        with pytest.raises((OfferCompositionError, RuntimeError)):
            await _issue_initial_ticket_owned_v2(port, pending)
    finally:
        object.__setattr__(subject, field, original)
    assert pending.admission_invocation_id is None
    assert port._composition.initial_invocation_slot == "EMPTY"
    assert port._composition.active_initial_ticket_or_null is None


@pytest.mark.anyio
async def test_id_factory_reentry_is_rejected_and_outer_ticket_remains_single(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    async with g.arbiter._lock:
        g.arbiter._active = pending
        g.arbiter._admission_driver = asyncio.current_task()
        g.arbiter._admission_state = "WAITING_ADMISSION"
    nested = []

    def factory():
        nested.append(asyncio.create_task(
            _issue_initial_ticket_owned_v2(port, pending)))
        return "outer-ticket"

    g.arbiter._invocation_id_factory = factory
    ticket = await _issue_initial_ticket_owned_v2(port, pending)
    results = await asyncio.gather(*nested, return_exceptions=True)
    assert type(ticket) is InitialOfferTicketV2
    assert ticket.exact_request.invocation_id == "outer-ticket"
    assert len(results) == 1 and isinstance(results[0], OfferCompositionError)
    assert port._composition.active_initial_ticket_or_null is ticket
    module._retire_offer_composition_v2(g.session)


@pytest.mark.anyio
async def test_id_factory_owner_mutation_rolls_back_with_captured_abort_result(graph):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    original_bundle = port._composition.prebuilt_pre_ticket_abort_bundle
    async with g.arbiter._lock:
        g.arbiter._active = pending
        g.arbiter._admission_driver = asyncio.current_task()
        g.arbiter._admission_state = "WAITING_ADMISSION"

    def factory():
        attempt = port._composition.active_ticket_build_attempt_or_null
        object.__setattr__(attempt, "state", "CONSUMED")
        object.__setattr__(attempt, "exact_failure_result", object())
        port._composition.prebuilt_pre_ticket_abort_bundle = object()
        return "mutated-owner"

    g.arbiter._invocation_id_factory = factory
    result = await _issue_initial_ticket_owned_v2(port, pending)
    assert result is original_bundle.failure_result
    assert pending.admission_invocation_id is None
    assert port._composition.initial_invocation_slot == "EMPTY"
    assert port._composition.active_ticket_build_attempt_or_null is None
    assert port._composition.active_initial_ticket_or_null is None
    port._composition.prebuilt_pre_ticket_abort_bundle = original_bundle


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", [
    "request.invocation_id", "request.priority", "request.phase", "request.day",
    "request.action_generation",
    "request.mapping_order", "request.not_after_monotonic",
    "ticket.exact_composition", "ticket.exact_caller_port", "ticket.exact_arbiter",
    "ticket.exact_pending_invocation", "ticket.exact_owner",
    "ticket.exact_dispatch_deadline", "ticket.exact_admission_driver_task",
    "ticket.exact_initial_slot", "ticket.exact_request", "ticket.exact_offer_task_or_null",
    "ticket.terminal_candidates", "ticket.state", "ticket.issuer_capability", "ticket._issuer",
    "terminal.preparing_complete", "terminal.stale", "terminal.deadline_suppressed",
    "terminal.admission_terminals", "terminal.internal_failure", "terminal.cleanup_unknown",
    "terminal.next_empty_slot_identity", "terminal._issuer",
])
async def test_closed_candidate_field_mutation_aborts_without_publish(
    graph, monkeypatch, mutation,
):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    family, field = mutation.split(".", 1)
    if family == "request":
        original = module._build_initial_request_candidate_v2

        def wrong_request(*args, **kwargs):
            request = original(*args, **kwargs)
            value = {
                "invocation_id": request.invocation_id + "-wrong",
                "priority": GenerationPriority.RESERVATION,
                "phase": request.phase + "-wrong",
                "day": request.day + 1,
                "action_generation": request.action_generation + 1,
                "mapping_order": request.mapping_order + 1,
                "not_after_monotonic": request.not_after_monotonic + 1.0,
            }[field]
            return replace(request, **{field: value})

        monkeypatch.setattr(module, "_build_initial_request_candidate_v2", wrong_request)
    elif family == "ticket":
        original = module.InitialOfferTicketV2.__init__

        def wrong_ticket(self, *args, **kwargs):
            original(self, *args, **kwargs)
            value = {
                "exact_owner": "foreign", "state": "CONSUMED_SOURCE",
            }.get(field, object())
            object.__setattr__(self, field, value)

        monkeypatch.setattr(module.InitialOfferTicketV2, "__init__", wrong_ticket)
    else:
        original = module.OfferPreparingTerminalCandidatesV2.__init__

        def wrong_terminal(self, *args, **kwargs):
            original(self, *args, **kwargs)
            object.__setattr__(self, field, object())

        monkeypatch.setattr(module.OfferPreparingTerminalCandidatesV2,
                            "__init__", wrong_terminal)
    result = await issue_actual(g, port, pending)
    assert result is port._abort_failure_result
    assert pending.admission_invocation_id is None
    assert port._composition.initial_invocation_slot == "EMPTY"
    assert port._composition.active_initial_ticket_or_null is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field,value",
    [
        ("request_version", 1),
        ("phase", "DAY"),
        ("day", 1),
        ("option_id", "o1"),
        ("receipt", object()),
        ("error_type", "WrongTerminalError"),
        ("invocation_started", True),
        ("request_event_id", "evt-1"),
        ("attempt_action", "CHAT"),
        ("send_connection_generation", 1),
        ("vote_target_player_id", "p1"),
        ("ability_id", "a1"),
        ("ability_target_player_ids", ("p1",)),
        ("discussion", object()),
    ],
)
async def test_same_status_terminal_with_any_other_outcome_field_is_rejected(
    graph, monkeypatch, field, value,
):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    original = module.OfferPreparingTerminalCandidatesV2.__init__

    def wrong_terminal(self, *args, **kwargs):
        original(self, *args, **kwargs)
        # Keep the captured result identity and status unchanged.  A validator
        # that checks only status or terminal_refs would accept this mutation.
        object.__setattr__(self.preparing_complete.outcome, field, value)

    monkeypatch.setattr(
        module.OfferPreparingTerminalCandidatesV2, "__init__", wrong_terminal)
    result = await issue_actual(g, port, pending)
    assert result is port._abort_failure_result
    assert pending.admission_invocation_id is None
    assert port._composition.initial_invocation_slot == "EMPTY"
    assert port._composition.active_initial_ticket_or_null is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status",
    [
        AdmissionStatus.OVERLOADED,
        AdmissionStatus.UNAVAILABLE,
        AdmissionStatus.POISONED,
    ],
)
async def test_admission_failure_requires_exact_error_type(
    graph, monkeypatch, status,
):
    g = graph
    port = bind(g)
    pending = make_pending(g)
    original = module.OfferPreparingTerminalCandidatesV2.__init__

    def wrong_terminal(self, *args, **kwargs):
        original(self, *args, **kwargs)
        outcome = self.admission_terminals[status].outcome
        object.__setattr__(outcome, "error_type", "DifferentAdmissionFailure")

    monkeypatch.setattr(
        module.OfferPreparingTerminalCandidatesV2, "__init__", wrong_terminal)
    result = await issue_actual(g, port, pending)
    assert result is port._abort_failure_result
    assert pending.admission_invocation_id is None
    assert port._composition.initial_invocation_slot == "EMPTY"
    assert port._composition.active_initial_ticket_or_null is None
