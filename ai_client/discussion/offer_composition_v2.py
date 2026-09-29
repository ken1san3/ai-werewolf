"""Private offline composition only; no admission or generation authority."""

from __future__ import annotations

import asyncio
from threading import get_ident

from .authority_capture_bridge_v2 import (
    AuthorityCaptureBridgeV2,
    AuthorityCaptureBridgeError,
    _validate_owner_registration_v2,
)

_ISSUER = object()


class OfferCompositionError(RuntimeError):
    """Fixed, public-safe error: never include an owner object or payload."""


class _Opaque:
    __slots__ = ()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<opaque>)"

    def __reduce_ex__(self, protocol: int):
        raise TypeError("private composition cannot be serialized or copied")


def _new_pre_ticket_failure_result_v2():
    from ai_client.brain.invocation import BrainDispatchResult
    from ai_client.brain.model import DecisionOutcome, DecisionStatus
    return BrainDispatchResult(
        DecisionOutcome(status=DecisionStatus.BRAIN_FAILED,
                        error_type="OfferPreparingFailureV2"), None)


class PreTicketAbortBundleV2(_Opaque):
    __slots__ = ("original_empty_slot_identity", "failure_result", "_issuer")

    def __init__(self, token: object, slot: object, failure: object) -> None:
        if token is not _ISSUER:
            raise TypeError("private pre-ticket abort bundle")
        object.__setattr__(self, "original_empty_slot_identity", slot)
        object.__setattr__(self, "failure_result", failure)
        object.__setattr__(self, "_issuer", _ISSUER)

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("pre-ticket abort bundle is immutable")


class OfferPreparingCallerPortV2(_Opaque):
    __slots__ = ("_composition", "_arbiter", "_issuer", "_initial_slot_identity", "_capability",
                 "_abort_bundle", "_abort_failure_result")

    def __init__(self, token: object, arbiter: object) -> None:
        if token is not _ISSUER:
            raise TypeError("private caller port")
        self._composition = None
        self._arbiter = arbiter
        self._issuer = _ISSUER
        self._initial_slot_identity = object()
        self._capability = object()
        self._abort_failure_result = _new_pre_ticket_failure_result_v2()
        self._abort_bundle = PreTicketAbortBundleV2(
            _ISSUER, self._initial_slot_identity, self._abort_failure_result)

    def validate_idle(self) -> None:
        """Read guard only. No request, ticket or provider API is available."""
        composition = self._composition
        if (type(composition) is not OfferPreparingCompositionV2
                or composition.exact_caller_port is not self):
            raise OfferCompositionError("COMPOSITION_OWNER_MISMATCH")
        _validate_composition_v2(composition)


class OfferPreparingCompositionV2(_Opaque):
    __slots__ = (
        "exact_broker_session", "exact_owner_registration", "exact_runtime_source",
        "exact_world", "exact_discussion_store", "exact_authority_capture_bridge",
        "exact_context_receipt", "exact_arbiter", "exact_caller_port",
        "exact_controller", "exact_discussion_transaction",
        "exact_clock_callable", "owner_thread_loop", "lifecycle", "flow_state",
        "initial_invocation_slot", "initial_slot_identity",
        "active_initial_ticket_or_null", "active_offer_source_or_null",
        "prebuilt_pre_ticket_abort_bundle", "issuer_capability", "_issuer",
    )

    def __init__(self, token: object, session: object, bridge: AuthorityCaptureBridgeV2,
                 arbiter: object, clock: object, port: OfferPreparingCallerPortV2) -> None:
        if token is not _ISSUER:
            raise TypeError("private offer composition")
        registration = bridge._registration
        self.exact_broker_session = session
        self.exact_owner_registration = registration
        self.exact_runtime_source = registration.exact_runtime_source
        self.exact_world = registration.exact_world
        self.exact_discussion_store = registration.exact_discussion_store
        self.exact_authority_capture_bridge = bridge
        self.exact_context_receipt = bridge._receipt
        self.exact_arbiter = arbiter
        self.exact_controller = arbiter.controller
        self.exact_discussion_transaction = arbiter.controller._discussion
        self.exact_caller_port = port
        self.exact_clock_callable = clock
        self.owner_thread_loop = (get_ident(), asyncio.get_running_loop())
        self.lifecycle = "ACTIVE"
        self.flow_state = "IDLE"
        self.initial_invocation_slot = "EMPTY"
        self.initial_slot_identity = port._initial_slot_identity
        self.active_initial_ticket_or_null = None
        self.active_offer_source_or_null = None
        self.prebuilt_pre_ticket_abort_bundle = port._abort_bundle
        self.issuer_capability = port._capability
        self._issuer = _ISSUER


def _validate_edges(session: object, bridge: object, arbiter: object, clock: object) -> None:
    from ai_client.brain.controller import BrainController
    from ai_client.brain.invocation import BrainInvocationArbiter
    from ai_client.llm.admission_client import BrokerAdmissionSession
    from .transaction import DiscussionTransaction

    if (type(session) is not BrokerAdmissionSession
            or type(bridge) is not AuthorityCaptureBridgeV2
            or type(arbiter) is not BrainInvocationArbiter
            or not callable(clock)):
        raise OfferCompositionError("COMPOSITION_OWNER_MISMATCH")
    registration = bridge._registration
    receipt = bridge._receipt
    try:
        _validate_owner_registration_v2(registration, receipt, require_store=True)
    except AuthorityCaptureBridgeError:
        raise OfferCompositionError("COMPOSITION_OWNER_MISMATCH") from None
    store = registration.exact_discussion_store
    world = registration.exact_world
    controller = arbiter.controller
    if (registration.discussion_owner_stage != "STORE_ATTACHED"
            or registration.registered_receipt is not receipt
            or receipt.owner_registration_identity is not registration
            or bridge._receipt_capability is not receipt.port_issuer_capability
            or world._authority_read_port_v2 is not bridge._world_port
            or store._capture_read_port_v2 is not bridge._capture_port
            or bridge._world_port._bridge is not bridge
            or bridge._capture_port._bridge is not bridge
            or bridge._world_port._world is not world
            or bridge._world_port._source is not registration.exact_runtime_source
            or bridge._capture_port._store is not store
            or bridge._world_port._registration is not registration
            or bridge._capture_port._registration is not registration
            or bridge._world_port._receipt is not receipt
            or bridge._capture_port._receipt is not receipt
            or bridge._world_port._receipt_capability is not receipt.port_issuer_capability
            or bridge._capture_port._receipt_capability is not receipt.port_issuer_capability
            or bridge._capture_port._bound is not receipt.exact_bound_context_object
            or type(controller) is not BrainController
            or type(controller._discussion) is not DiscussionTransaction
            or controller._discussion.state is not store
            or controller._discussion._closed
            or controller.world is not world
            or controller.sender is not registration.exact_network_client
            or controller._clock is not clock
            or arbiter._admission is not session or arbiter._clock is not clock):
        raise OfferCompositionError("COMPOSITION_OWNER_MISMATCH")
    if (session._closed or session._transport_closed or session._reader_task.done()
            or session._reader_task.get_loop() is not asyncio.get_running_loop()
            or session._writer.is_closing() or session._reader.at_eof()
            or store.is_closed or arbiter._stopped or arbiter._poisoned
            or arbiter._admission_closed or controller._stopping or controller._unresponsive):
        raise OfferCompositionError("COMPOSITION_CLOSED")
    if (session._results or session._acks or session._control_lanes
            or session._terminal_controls or session._attachments or session._generations
            or session._call_ordinals or session._request_ids
            or session._claimed_invocation is not None or session._activated_invocation is not None
            or arbiter._pending or arbiter._active is not None
            or arbiter._active_task is not None or arbiter._admission_driver is not None
            or arbiter._admission_wait_task is not None or arbiter._admission_brain_task is not None
            or arbiter._admission_lease is not None or arbiter._suspended_reaction is not None
            or arbiter._attached_successor is not None or arbiter._observation_gate is not None
            or arbiter._completed_observation is not None or arbiter._admission_state != "IDLE"
            or arbiter._fatal_error is not None or arbiter._poison_shutdown_task is not None
            or arbiter._controller_stop_task is not None or arbiter._stop_task is not None
            or arbiter._lock.locked() or controller._active is not None
            or store._staged is not None or store._committed is not None
            or store._dispatch is not None or store._delivery is not None):
        raise OfferCompositionError("COMPOSITION_BUSY")
    store._check_owner()


def _validate_composition_v2(composition: object) -> None:
    if type(composition) is not OfferPreparingCompositionV2:
        raise OfferCompositionError("COMPOSITION_OWNER_MISMATCH")
    c = composition
    if (c._issuer is not _ISSUER or c.lifecycle != "ACTIVE"
            or c.flow_state != "IDLE" or c.initial_invocation_slot != "EMPTY"
            or c.active_initial_ticket_or_null is not None or c.active_offer_source_or_null is not None
            or c.owner_thread_loop != (get_ident(), asyncio.get_running_loop())):
        raise OfferCompositionError("COMPOSITION_NOT_IDLE")
    r = c.exact_owner_registration
    p = c.exact_caller_port
    b = c.exact_authority_capture_bridge
    if (type(b) is not AuthorityCaptureBridgeV2
            or type(p) is not OfferPreparingCallerPortV2 or p._issuer is not _ISSUER
            or p._composition is not c or p._arbiter is not c.exact_arbiter
            or c.exact_arbiter._admission is not c.exact_broker_session
            or c.exact_arbiter.controller is not c.exact_controller
            or c.exact_controller._discussion is not c.exact_discussion_transaction
            or c.initial_slot_identity is not p._initial_slot_identity
            or c.issuer_capability is not p._capability
            or b._registration is not r or b._receipt is not c.exact_context_receipt
            or r.exact_runtime_source is not c.exact_runtime_source
            or r.exact_world is not c.exact_world or r.exact_discussion_store is not c.exact_discussion_store
            or c.exact_broker_session._offer_preparing_mode_v2 is not True
            or c.exact_discussion_store._offer_preparing_mode_v2 is not True
            or c.exact_arbiter._offer_preparing_mode_v2 is not True
            or c.exact_broker_session._offer_preparing_composition_v2 is not c
            or c.exact_discussion_store._offer_preparing_composition_v2 is not c
            or c.exact_arbiter._offer_preparing_composition_v2 is not c):
        raise OfferCompositionError("COMPOSITION_OWNER_MISMATCH")
    abort = c.prebuilt_pre_ticket_abort_bundle
    if (type(abort) is not PreTicketAbortBundleV2 or abort._issuer is not _ISSUER
            or abort is not p._abort_bundle
            or abort.original_empty_slot_identity is not c.initial_slot_identity
            or abort.failure_result is not p._abort_failure_result):
        raise OfferCompositionError("COMPOSITION_ABORT_BUNDLE_MISMATCH")
    _validate_edges(c.exact_broker_session, b, c.exact_arbiter, c.exact_clock_callable)


def _bind_offer_composition_v2(session: object, bridge: object, arbiter: object,
                               clock: object) -> OfferPreparingCallerPortV2:
    _validate_edges(session, bridge, arbiter, clock)
    store = bridge._registration.exact_discussion_store
    owners = (session, store, arbiter)
    if any(owner._offer_preparing_composition_v2 is not None
           or owner._offer_preparing_mode_v2 is not False for owner in owners):
        raise OfferCompositionError("COMPOSITION_ALREADY_BOUND")
    port = OfferPreparingCallerPortV2(_ISSUER, arbiter)
    composition = OfferPreparingCompositionV2(_ISSUER, session, bridge, arbiter, clock, port)
    # All allocations and validation precede publication. No wakeup or callback.
    _validate_edges(session, bridge, arbiter, clock)
    if any(owner._offer_preparing_composition_v2 is not None
           or owner._offer_preparing_mode_v2 is not False for owner in owners):
        raise OfferCompositionError("COMPOSITION_ALREADY_BOUND")
    session._offer_preparing_composition_v2 = composition
    store._offer_preparing_composition_v2 = composition
    arbiter._offer_preparing_composition_v2 = composition
    port._composition = composition
    session._offer_preparing_mode_v2 = True
    store._offer_preparing_mode_v2 = True
    arbiter._offer_preparing_mode_v2 = True
    return port


def _retire_offer_composition_v2(owner: object) -> None:
    c = getattr(owner, "_offer_preparing_composition_v2", None)
    if type(c) is OfferPreparingCompositionV2 and (
            owner is c.exact_broker_session or owner is c.exact_discussion_store
            or owner is c.exact_arbiter):
        c.lifecycle = "RETIRED"
    # Retain the retired backlink: closing never grants permission to rebind.
