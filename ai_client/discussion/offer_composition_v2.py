"""Private offline composition only; no admission or generation authority."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from threading import get_ident
from types import MappingProxyType

from .authority_capture_bridge_v2 import (
    AuthorityCaptureBridgeV2,
    AuthorityCaptureBridgeError,
    _validate_owner_registration_v2,
)

_ISSUER = object()
PHASE6_V2_SYSTEM_INSTRUCTION_VERSION = "phase6-v2-system-instruction.v1"


@dataclass(frozen=True)
class GenerationProfileBundleV2:
    schema_version: str
    generation_profile: str
    system_instruction_version: str
    generation_config_fingerprint: str


class OfferCompositionError(RuntimeError):
    """Fixed, public-safe error: never include an owner object or payload."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


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
                 "_abort_bundle", "_abort_failure_result", "generation_profile_bundle_v2")

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
        self.generation_profile_bundle_v2 = None

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
        "initial_invocation_slot", "initial_slot_identity", "initial_pending_or_null",
        "active_ticket_build_attempt_or_null",
        "active_initial_ticket_or_null", "active_offer_source_or_null",
        "prebuilt_pre_ticket_abort_bundle", "generation_profile_bundle_v2",
        "issuer_capability", "_issuer",
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
        self.initial_pending_or_null = None
        self.active_ticket_build_attempt_or_null = None
        self.active_initial_ticket_or_null = None
        self.active_offer_source_or_null = None
        self.prebuilt_pre_ticket_abort_bundle = port._abort_bundle
        self.generation_profile_bundle_v2 = GenerationProfileBundleV2(
            "aiwolf.generation-profile-bundle.v2", "phase6_v2",
            PHASE6_V2_SYSTEM_INSTRUCTION_VERSION, session.identity.config_fingerprint)
        self.issuer_capability = port._capability
        self._issuer = _ISSUER


def _validate_edges(session: object, bridge: object, arbiter: object, clock: object) -> None:
    from ai_client.brain.controller import BrainController
    from ai_client.brain.invocation import BrainInvocationArbiter
    from ai_client.llm.admission_client import BrokerAdmissionSession, _ControlLane
    from .transaction import DiscussionTransaction

    _validate_immutable_owner_edges_v2(session, bridge, arbiter, clock)
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
    try:
        store._check_owner()
    except Exception:
        raise OfferCompositionError("COMPOSITION_OWNER_MISMATCH") from None


def _validate_immutable_owner_edges_v2(
    session: object, bridge: object, arbiter: object, clock: object,
) -> None:
    from ai_client.brain.controller import BrainController
    from ai_client.brain.invocation import BrainInvocationArbiter
    from ai_client.llm.admission_client import BrokerAdmissionSession, _ControlLane
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
            or controller is not getattr(arbiter._offer_preparing_composition_v2,
                                         "exact_controller", controller)
            or controller._discussion is not getattr(
                arbiter._offer_preparing_composition_v2,
                "exact_discussion_transaction", controller._discussion)
            or controller._discussion.state is not store or controller._discussion._closed
            or controller.world is not world
            or controller.sender is not registration.exact_network_client
            or controller._clock is not clock
            or arbiter._admission is not session or arbiter._clock is not clock
            or session._reader_task.get_loop() is not asyncio.get_running_loop()):
        raise OfferCompositionError("COMPOSITION_OWNER_MISMATCH")
    try:
        store._check_owner()
    except Exception:
        raise OfferCompositionError("COMPOSITION_OWNER_MISMATCH") from None


def _validate_composition_v2(composition: object) -> None:
    if type(composition) is not OfferPreparingCompositionV2:
        raise OfferCompositionError("COMPOSITION_OWNER_MISMATCH")
    c = composition
    if (c._issuer is not _ISSUER or c.lifecycle != "ACTIVE"
            or c.flow_state != "IDLE" or c.initial_invocation_slot != "EMPTY"
            or c.initial_pending_or_null is not None
            or c.active_ticket_build_attempt_or_null is not None
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
    port.generation_profile_bundle_v2 = composition.generation_profile_bundle_v2
    session._offer_preparing_mode_v2 = True
    store._offer_preparing_mode_v2 = True
    arbiter._offer_preparing_mode_v2 = True
    return port


def _retire_offer_composition_v2(owner: object) -> None:
    c = getattr(owner, "_offer_preparing_composition_v2", None)
    if type(c) is OfferPreparingCompositionV2 and (
            owner is c.exact_broker_session or owner is c.exact_discussion_store
            or owner is c.exact_arbiter):
        from .reserved_finalize_v2 import _retire_finalized_owned_v2
        if _retire_finalized_owned_v2(c):
            return
        c.lifecycle = "RETIRED"
        ticket = c.active_initial_ticket_or_null
        if type(ticket) is InitialOfferTicketV2 and ticket.state == "ACTIVE":
            object.__setattr__(ticket, "state", "RETIRED_UNKNOWN")
            c.initial_invocation_slot = "CLEANUP_UNKNOWN"
            c.flow_state = "OFFER_CLEANUP_UNKNOWN"
        elif (type(ticket) is InitialOfferTicketV2
              and ticket.state == "CONSUMED_PREPARING"):
            source = c.active_offer_source_or_null
            if type(source) is InitialOfferSourceV2:
                object.__setattr__(source, "state", "TERMINAL_HELD")
            c.initial_invocation_slot = "PREPARING_HELD"
            c.flow_state = "PREPARING_TERMINAL_HELD"
        elif (type(ticket) is InitialOfferTicketV2
              and ticket.state == "CONSUMED_SOURCE"):
            source = c.active_offer_source_or_null
            if type(source) is InitialOfferSourceV2:
                receipt = source.exact_offer_receipt_or_null
                object.__setattr__(source, "state", "CLEANUP_UNKNOWN")
                if type(receipt) is BrokerOfferOwnershipReceiptV2:
                    object.__setattr__(receipt, "state", "RETIRED_UNKNOWN")
            object.__setattr__(ticket, "state", "RETIRED_UNKNOWN")
            c.initial_invocation_slot = "CLEANUP_UNKNOWN"
            c.flow_state = "OFFER_CLEANUP_UNKNOWN"
    # Retain the retired backlink: closing never grants permission to rebind.


class OfferPreparingTerminalCandidatesV2(_Opaque):
    __slots__ = (
        "preparing_complete", "stale", "deadline_suppressed", "admission_terminals",
        "internal_failure", "cleanup_unknown", "next_empty_slot_identity", "_issuer",
    )

    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER:
            raise TypeError("private terminal candidate bundle")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_issuer", _ISSUER)

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("terminal candidate bundle is immutable")


class InitialTicketBuildAttemptV2(_Opaque):
    __slots__ = (
        "exact_composition", "exact_arbiter", "exact_pending", "exact_driver_task",
        "exact_abort_bundle", "exact_failure_result", "exact_empty_slot_identity",
        "state", "_issuer",
    )

    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER:
            raise TypeError("private initial ticket build attempt")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_issuer", _ISSUER)

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("initial ticket build attempt is immutable outside its issuer")


class InitialOfferTicketV2(_Opaque):
    __slots__ = (
        "exact_composition", "exact_caller_port", "exact_arbiter",
        "exact_pending_invocation", "exact_owner", "exact_dispatch_deadline",
        "exact_admission_driver_task", "exact_initial_slot", "exact_request",
        "exact_offer_task_or_null", "terminal_candidates", "state", "issuer_capability",
        "_issuer",
    )

    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER:
            raise TypeError("private initial offer ticket")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_issuer", _ISSUER)

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("initial offer ticket is immutable outside its issuer")


class InitialOfferSourceV2(_Opaque):
    __slots__ = (
        "exact_composition", "exact_caller_port", "exact_arbiter", "exact_request",
        "exact_dispatch_deadline", "exact_control_lane_or_null",
        "exact_result_future_or_null", "exact_client_lease_or_null",
        "exact_offer_receipt_or_null", "exact_offer_candidates_or_null",
        "exact_initial_ticket", "state", "_issuer",
    )

    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER:
            raise TypeError("private initial offer source")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_issuer", _ISSUER)

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("initial offer source is immutable outside its issuer")


class BrokerOfferOwnershipReceiptV2(_Opaque):
    __slots__ = (
        "exact_composition", "exact_session", "exact_offer_source", "exact_request",
        "exact_raw_offer_frame_object", "exact_result_future", "exact_control_lane",
        "exact_client_lease", "invocation_id", "queue_wait_microseconds",
        "offer_observed_monotonic", "offer_observed_monotonic_us",
        "notification_failure_observation_or_null", "state", "issuer_capability",
        "_issuer",
    )

    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER:
            raise TypeError("private broker offer receipt")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_issuer", _ISSUER)

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("broker offer receipt is immutable outside its issuer")


class PreparingLeaseOwnerReceiptV2(_Opaque):
    __slots__ = (
        "exact_composition", "exact_store", "exact_initial_ticket", "exact_pending",
        "exact_driver_task", "exact_offer_receipt", "exact_offer_source",
        "exact_session", "exact_control_lane", "exact_client_lease", "exact_request",
        "exact_dispatch_deadline", "exact_prepared_material", "exact_hidden_owner_receipt",
        "exact_store_capture_tuple", "raw_deadline", "expires_at_monotonic_us",
        "state", "_issuer", "preparing_identity", "abort_bundle", "cell_identity", "cell_weakref", "__weakref__",
    )

    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER:
            raise TypeError("private preparing lease owner receipt")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_issuer", _ISSUER)

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("preparing lease owner receipt is immutable")


class NotificationFailureObservationV2(_Opaque):
    __slots__ = ("future_done", "exact_result_visible", "session_results_member", "_issuer")
    def __init__(self, token: object) -> None:
        if token is not _ISSUER:
            raise TypeError("private notification observation")
        object.__setattr__(self, "future_done", None)
        object.__setattr__(self, "exact_result_visible", None)
        object.__setattr__(self, "session_results_member", None)
        object.__setattr__(self, "_issuer", _ISSUER)
    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("notification observation is immutable outside its issuer")


class OfferPublishCandidatesV2(_Opaque):
    __slots__ = (
        "exact_client_lease", "exact_offer_receipt", "exact_admission_result",
        "exact_owned_completion_tuple", "exact_no_offer_cleanup_sentinel",
        "preallocated_notification_failure_observation_slot", "_issuer",
    )
    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER:
            raise TypeError("private offer candidates")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_issuer", _ISSUER)
    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("offer candidates are immutable")


def _build_initial_request_candidate_v2(pending: object, invocation_id: object):
    """Build an admission request without publishing its ID to the pending owner."""
    from ai_client.brain.invocation import BrainInvocationPriority, _PendingInvocation
    from ai_client.brain.model import DispatchDeadline
    from ai_client.llm.admission_types import AdmissionRequest, GenerationPriority
    from ai_client.network.types import (
        AbilityAction, ChatAction, CoDeclareAction, CoReportAction, VoteAction,
    )

    if type(pending) is not _PendingInvocation:
        raise OfferCompositionError("INITIAL_PENDING_MISMATCH")
    expected_priority = {
        "vote_ability": BrainInvocationPriority.RESERVATION_ACTION,
        "reaction_chat": BrainInvocationPriority.REACTION,
    }
    handles = pending.allowed_handles
    valid_family = False
    if isinstance(handles, tuple) and handles:
        valid_family = (
            pending.owner == "vote_ability"
            and all(isinstance(handle, (VoteAction, AbilityAction)) for handle in handles)
        ) or (
            pending.owner == "reaction_chat"
            and all(isinstance(handle, (ChatAction, CoDeclareAction, CoReportAction))
                    for handle in handles)
        )
    if (pending.owner not in expected_priority
            or pending.priority is not expected_priority[pending.owner]
            or not isinstance(handles, tuple) or not handles
            or len(set(handles)) != len(handles) or not valid_family
            or type(pending.dispatch_deadline) is not DispatchDeadline):
        raise OfferCompositionError("INITIAL_PENDING_MISMATCH")
    return AdmissionRequest(
        invocation_id=invocation_id,
        priority=(GenerationPriority.RESERVATION
                  if pending.priority is BrainInvocationPriority.RESERVATION_ACTION
                  else GenerationPriority.REACTION),
        phase=pending.dispatch_deadline.phase,
        day=pending.dispatch_deadline.day,
        action_generation=pending.dispatch_deadline.action_generation,
        mapping_order=pending.dispatch_deadline.mapping_order,
        not_after_monotonic=pending.dispatch_deadline.not_after_monotonic,
    )


def _build_terminal_candidates_v2(composition: OfferPreparingCompositionV2):
    from ai_client.brain.invocation import BrainDispatchResult, BrainInvocationArbiter
    from ai_client.brain.model import DecisionOutcome, DecisionStatus
    from ai_client.llm.admission_types import AdmissionStatus

    abort = composition.prebuilt_pre_ticket_abort_bundle
    preparing_complete = BrainInvocationArbiter._cancelled_result()
    stale = BrainInvocationArbiter._stale_result()
    deadline_suppressed = BrainInvocationArbiter._deadline_suppressed_result()
    admissions = MappingProxyType({
        status: BrainInvocationArbiter._admission_terminal(status)
        for status in (
            AdmissionStatus.REPLACED, AdmissionStatus.EXPIRED, AdmissionStatus.OVERLOADED,
            AdmissionStatus.UNAVAILABLE, AdmissionStatus.CANCELLED, AdmissionStatus.POISONED,
        )
    })
    cleanup_unknown = BrainDispatchResult(
        DecisionOutcome(status=DecisionStatus.BRAIN_FAILED,
                        error_type="OfferCleanupUnknownV2"), None)
    next_empty_slot_identity = object()
    bundle = OfferPreparingTerminalCandidatesV2(
        _ISSUER,
        preparing_complete=preparing_complete,
        stale=stale,
        deadline_suppressed=deadline_suppressed,
        admission_terminals=admissions,
        internal_failure=abort.failure_result,
        cleanup_unknown=cleanup_unknown,
        next_empty_slot_identity=next_empty_slot_identity,
    )
    return bundle, (
        preparing_complete, stale, deadline_suppressed, admissions,
        abort.failure_result, cleanup_unknown, next_empty_slot_identity,
    )


def _validate_initial_ticket_context_v2(
    port: object, pending: object, attempt: object = None,
):
    from ai_client.brain.invocation import _PendingInvocation

    if type(port) is not OfferPreparingCallerPortV2:
        raise OfferCompositionError("INITIAL_TICKET_OWNER_MISMATCH")
    composition = port._composition
    if (type(composition) is not OfferPreparingCompositionV2
            or composition.exact_caller_port is not port
            or composition.lifecycle != "ACTIVE" or composition.flow_state != "IDLE"
            or composition.initial_invocation_slot not in {"EMPTY", "PENDING_SELECTED"}
            or composition.active_ticket_build_attempt_or_null is not attempt
            or composition.active_initial_ticket_or_null is not None
            or composition.active_offer_source_or_null is not None
            or type(pending) is not _PendingInvocation):
        raise OfferCompositionError("INITIAL_TICKET_OWNER_MISMATCH")
    arbiter = composition.exact_arbiter
    _validate_initial_ticket_edges_v2(composition)
    try:
        arbiter._validate_call(
            owner=pending.owner, priority=pending.priority,
            allowed_handles=pending.allowed_handles,
            timeout_seconds=pending.timeout_seconds,
            dispatch_deadline=pending.dispatch_deadline,
            on_brain_start=pending.on_brain_start,
        )
    except (TypeError, ValueError):
        raise OfferCompositionError("INITIAL_PENDING_MISMATCH") from None
    driver = asyncio.current_task()
    if (not arbiter._lock.locked() or arbiter._admission_driver is not driver
            or driver is None or driver.done() or arbiter._active is not pending
            or arbiter._pending or arbiter._admission_state != "WAITING_ADMISSION"
            or arbiter._admission_wait_task is not None or arbiter._admission_lease is not None
            or arbiter._suspended_reaction is not None or arbiter._attached_successor is not None
            or pending.result.done() or pending.execution_complete.is_set()
            or pending.cancel_requested or pending.admission_invocation_id is not None):
        raise OfferCompositionError("INITIAL_TICKET_STATE_MISMATCH")
    if composition.initial_invocation_slot == "PENDING_SELECTED":
        if composition.initial_pending_or_null is not pending:
            raise OfferCompositionError("INITIAL_TICKET_STATE_MISMATCH")
    elif composition.initial_pending_or_null is not None:
        raise OfferCompositionError("INITIAL_TICKET_STATE_MISMATCH")
    return composition, arbiter, driver


def _validate_initial_ticket_edges_v2(composition: OfferPreparingCompositionV2) -> None:
    session = composition.exact_broker_session
    registration = composition.exact_owner_registration
    receipt = composition.exact_context_receipt
    arbiter = composition.exact_arbiter
    store = composition.exact_discussion_store
    _validate_immutable_owner_edges_v2(
        session, composition.exact_authority_capture_bridge,
        arbiter, composition.exact_clock_callable)
    port = composition.exact_caller_port
    abort = composition.prebuilt_pre_ticket_abort_bundle
    controller = composition.exact_controller
    try:
        _validate_owner_registration_v2(registration, receipt, require_store=True)
    except AuthorityCaptureBridgeError:
        raise OfferCompositionError("INITIAL_TICKET_OWNER_MISMATCH") from None
    if (composition._issuer is not _ISSUER or composition.lifecycle != "ACTIVE"
            or composition.owner_thread_loop != (get_ident(), asyncio.get_running_loop())
            or type(port) is not OfferPreparingCallerPortV2 or port._issuer is not _ISSUER
            or port._composition is not composition or port._arbiter is not arbiter
            or port._capability is not composition.issuer_capability
            or port._initial_slot_identity is not composition.initial_slot_identity
            or registration.exact_discussion_store is not store
            or registration.exact_runtime_source is not composition.exact_runtime_source
            or registration.exact_world is not composition.exact_world
            or registration.exact_discussion_store is not composition.exact_discussion_store
            or arbiter.controller is not controller
            or controller._discussion is not composition.exact_discussion_transaction
            or composition.exact_authority_capture_bridge._registration is not registration
            or composition.exact_authority_capture_bridge._receipt is not receipt
            or store._offer_preparing_composition_v2 is not composition
            or arbiter._offer_preparing_composition_v2 is not composition
            or session._offer_preparing_composition_v2 is not composition
            or not store._offer_preparing_mode_v2 or not arbiter._offer_preparing_mode_v2
            or not session._offer_preparing_mode_v2
            or arbiter._admission is not session
            or arbiter._clock is not composition.exact_clock_callable
            or session._closed or session._transport_closed or session._reader_task.done()
            or session._writer.is_closing() or session._reader.at_eof()
            or session._results or session._acks or session._control_lanes
            or session._attachments or session._generations
            or session._call_ordinals or session._request_ids
            or session._claimed_invocation is not None
            or session._activated_invocation is not None
            or store.is_closed or arbiter._stopped or arbiter._poisoned
            or arbiter._admission_closed or arbiter._active_task is not None
            or arbiter._admission_brain_task is not None
            or arbiter._observation_gate is not None
            or arbiter._completed_observation is not None
            or arbiter._fatal_error is not None
            or arbiter._poison_shutdown_task is not None
            or arbiter._controller_stop_task is not None
            or arbiter._stop_task is not None
            or controller._stopping or controller._unresponsive
            or controller._active is not None
            or store._staged is not None or store._committed is not None
            or store._dispatch is not None or store._delivery is not None):
        raise OfferCompositionError("INITIAL_TICKET_OWNER_MISMATCH")
    if (type(abort) is not PreTicketAbortBundleV2 or abort._issuer is not _ISSUER
            or abort is not port._abort_bundle
            or abort.original_empty_slot_identity is not composition.initial_slot_identity
            or abort.failure_result is not port._abort_failure_result):
        raise OfferCompositionError("COMPOSITION_ABORT_BUNDLE_MISMATCH")


async def _issue_initial_ticket_owned_v2(port: object, pending: object):
    """Non-routable offline issuer whose lexical body owns the arbiter lock."""
    if type(port) is not OfferPreparingCallerPortV2:
        raise OfferCompositionError("INITIAL_TICKET_OWNER_MISMATCH")
    composition = port._composition
    if type(composition) is not OfferPreparingCompositionV2:
        raise OfferCompositionError("INITIAL_TICKET_OWNER_MISMATCH")
    arbiter = composition.exact_arbiter
    from ai_client.brain.invocation import BrainInvocationArbiter
    if type(arbiter) is not BrainInvocationArbiter:
        raise OfferCompositionError("INITIAL_TICKET_OWNER_MISMATCH")
    driver = asyncio.current_task()
    if (driver is None or arbiter._admission_driver is not driver
            or arbiter._lock.locked() or arbiter._active is not pending):
        raise OfferCompositionError("INITIAL_TICKET_LOCK_OWNER_MISMATCH")
    async with arbiter._lock:
        composition, arbiter, driver = _validate_initial_ticket_context_v2(port, pending)
        abort = composition.prebuilt_pre_ticket_abort_bundle
        failure = abort.failure_result
        empty_slot = abort.original_empty_slot_identity
        try:
            attempt = InitialTicketBuildAttemptV2(
                _ISSUER,
                exact_composition=composition, exact_arbiter=arbiter,
                exact_pending=pending, exact_driver_task=driver,
                exact_abort_bundle=abort, exact_failure_result=failure,
                exact_empty_slot_identity=empty_slot, state="ACTIVE",
            )
        except Exception:
            if (composition.initial_invocation_slot != "EMPTY"
                    or composition.initial_pending_or_null is not None
                    or composition.active_ticket_build_attempt_or_null is not None
                    or composition.active_initial_ticket_or_null is not None
                    or arbiter._active is not pending
                    or pending.admission_invocation_id is not None
                    or abort is not composition.prebuilt_pre_ticket_abort_bundle
                    or failure is not abort.failure_result
                    or empty_slot is not composition.initial_slot_identity):
                raise OfferCompositionError("PRE_TICKET_ABORT_MISMATCH")
            arbiter._active = None
            arbiter._admission_driver = None
            arbiter._admission_state = "IDLE"
            return failure
        def abort_attempt():
            if (composition.active_ticket_build_attempt_or_null is not attempt
                    or composition.initial_pending_or_null is not pending
                    or composition.active_initial_ticket_or_null is not None
                    or pending.admission_invocation_id is not None
                    or empty_slot is not composition.initial_slot_identity):
                raise OfferCompositionError("PRE_TICKET_ABORT_MISMATCH")
            composition.initial_invocation_slot = "EMPTY"
            composition.initial_pending_or_null = None
            composition.active_ticket_build_attempt_or_null = None
            arbiter._active = None
            arbiter._admission_driver = None
            arbiter._admission_state = "IDLE"
            object.__setattr__(attempt, "state", "CONSUMED")
            return failure

        composition.active_ticket_build_attempt_or_null = attempt
        composition.initial_invocation_slot = "PENDING_SELECTED"
        composition.initial_pending_or_null = pending
        try:
            invocation_id = arbiter._invocation_id_factory()
            request = _build_initial_request_candidate_v2(pending, invocation_id)
            terminals, terminal_refs = _build_terminal_candidates_v2(composition)
            ticket_capability = object()
            ticket = InitialOfferTicketV2(
                _ISSUER,
                exact_composition=composition,
                exact_caller_port=port,
                exact_arbiter=arbiter,
                exact_pending_invocation=pending,
                exact_owner=pending.owner,
                exact_dispatch_deadline=pending.dispatch_deadline,
                exact_admission_driver_task=driver,
                exact_initial_slot=composition.initial_slot_identity,
                exact_request=request,
                exact_offer_task_or_null=None,
                terminal_candidates=terminals,
                state="ACTIVE",
                issuer_capability=ticket_capability,
            )
            current = _validate_initial_ticket_context_v2(port, pending, attempt)
            if current != (composition, arbiter, driver):
                raise OfferCompositionError("INITIAL_TICKET_STATE_MISMATCH")
            _validate_initial_ticket_candidate_v2(
                attempt, ticket, request, invocation_id,
                terminals, terminal_refs, ticket_capability)
        except Exception:
            return abort_attempt()
        pending.admission_invocation_id = request.invocation_id
        composition.active_initial_ticket_or_null = ticket
        composition.active_ticket_build_attempt_or_null = None
        composition.initial_invocation_slot = "TICKET_ACTIVE"
        arbiter._admission_state = "V2_INITIAL_REGISTERED"
        object.__setattr__(attempt, "state", "CONSUMED")
        return ticket


def _validate_initial_ticket_candidate_v2(
    attempt: InitialTicketBuildAttemptV2,
    ticket: object,
    request: object,
    expected_invocation_id: object,
    terminals: object,
    terminal_refs: tuple[object, ...],
    ticket_capability: object,
) -> None:
    from ai_client.brain.invocation import BrainDispatchResult, BrainInvocationPriority
    from ai_client.brain.model import DecisionOutcome, DecisionStatus
    from ai_client.llm.admission_types import (
        AdmissionRequest, AdmissionStatus, GenerationPriority,
    )

    composition = attempt.exact_composition
    pending = attempt.exact_pending
    if (type(attempt) is not InitialTicketBuildAttemptV2
            or attempt._issuer is not _ISSUER or attempt.state != "ACTIVE"
            or attempt.exact_composition is not composition
            or attempt.exact_arbiter is not composition.exact_arbiter
            or attempt.exact_pending is not pending
            or attempt.exact_driver_task is not asyncio.current_task()
            or attempt.exact_abort_bundle is not composition.prebuilt_pre_ticket_abort_bundle
            or attempt.exact_failure_result is not attempt.exact_abort_bundle.failure_result
            or attempt.exact_empty_slot_identity is not composition.initial_slot_identity):
        raise OfferCompositionError("INITIAL_BUILD_ATTEMPT_MISMATCH")
    expected_priority = (
        GenerationPriority.RESERVATION
        if pending.priority is BrainInvocationPriority.RESERVATION_ACTION
        else GenerationPriority.REACTION
    )
    if (type(request) is not AdmissionRequest
            or type(request.invocation_id) is not str
            or request.invocation_id != expected_invocation_id
            or request.priority is not expected_priority
            or request.invocation_id == ""
            or request.phase != pending.dispatch_deadline.phase
            or request.day != pending.dispatch_deadline.day
            or request.action_generation != pending.dispatch_deadline.action_generation
            or request.mapping_order != pending.dispatch_deadline.mapping_order
            or request.not_after_monotonic != pending.dispatch_deadline.not_after_monotonic):
        raise OfferCompositionError("INITIAL_REQUEST_MISMATCH")
    if request.invocation_id in composition.exact_broker_session._terminal_controls:
        raise OfferCompositionError("INITIAL_REQUEST_ID_REUSED")
    if (type(ticket) is not InitialOfferTicketV2 or ticket._issuer is not _ISSUER
            or ticket.exact_composition is not composition
            or ticket.exact_caller_port is not composition.exact_caller_port
            or ticket.exact_arbiter is not attempt.exact_arbiter
            or ticket.exact_pending_invocation is not pending
            or ticket.exact_owner != pending.owner
            or ticket.exact_dispatch_deadline is not pending.dispatch_deadline
            or ticket.exact_admission_driver_task is not attempt.exact_driver_task
            or ticket.exact_initial_slot is not attempt.exact_empty_slot_identity
            or ticket.exact_request is not request
            or ticket.exact_offer_task_or_null is not None
            or ticket.terminal_candidates is not terminals
            or ticket.state != "ACTIVE"
            or type(ticket_capability) is not object
            or ticket.issuer_capability is not ticket_capability):
        raise OfferCompositionError("INITIAL_TICKET_CANDIDATE_MISMATCH")
    statuses = (
        AdmissionStatus.REPLACED, AdmissionStatus.EXPIRED, AdmissionStatus.OVERLOADED,
        AdmissionStatus.UNAVAILABLE, AdmissionStatus.CANCELLED, AdmissionStatus.POISONED,
    )
    def is_closed_outcome(
        value: object,
        status: DecisionStatus,
        error_type: str | None = None,
    ) -> bool:
        """Compare every DecisionOutcome field; status equality alone is unsafe."""
        return (
            type(value) is DecisionOutcome
            and value.status is status
            and value.request_version is None
            and value.phase is None
            and value.day is None
            and value.option_id is None
            and value.receipt is None
            and value.error_type == error_type
            and value.invocation_started is False
            and value.request_event_id is None
            and value.attempt_action is None
            and value.send_connection_generation is None
            and value.vote_target_player_id is None
            and value.ability_id is None
            and type(value.ability_target_player_ids) is tuple
            and value.ability_target_player_ids == ()
            and value.discussion is None
        )
    if (type(terminal_refs) is not tuple or len(terminal_refs) != 7
            or terminals.preparing_complete is not terminal_refs[0]
            or terminals.stale is not terminal_refs[1]
            or terminals.deadline_suppressed is not terminal_refs[2]
            or terminals.admission_terminals is not terminal_refs[3]
            or terminals.internal_failure is not terminal_refs[4]
            or terminals.cleanup_unknown is not terminal_refs[5]
            or terminals.next_empty_slot_identity is not terminal_refs[6]):
        raise OfferCompositionError("INITIAL_TERMINAL_CANDIDATE_MISMATCH")
    if (type(terminals) is not OfferPreparingTerminalCandidatesV2
            or terminals._issuer is not _ISSUER
            or type(terminals.preparing_complete) is not BrainDispatchResult
            or not is_closed_outcome(
                terminals.preparing_complete.outcome, DecisionStatus.CANCELLED)
            or type(terminals.stale) is not BrainDispatchResult
            or not is_closed_outcome(terminals.stale.outcome, DecisionStatus.STALE)
            or type(terminals.deadline_suppressed) is not BrainDispatchResult
            or not is_closed_outcome(
                terminals.deadline_suppressed.outcome,
                DecisionStatus.DEADLINE_SUPPRESSED)
            or type(terminals.admission_terminals) is not type(MappingProxyType({}))
            or tuple(terminals.admission_terminals) != statuses
            or any(type(terminals.admission_terminals[status]) is not BrainDispatchResult
                   for status in statuses)
            or terminals.internal_failure is not attempt.exact_failure_result
            or not is_closed_outcome(
                terminals.internal_failure.outcome, DecisionStatus.BRAIN_FAILED,
                "OfferPreparingFailureV2")
            or type(terminals.cleanup_unknown) is not BrainDispatchResult
            or not is_closed_outcome(
                terminals.cleanup_unknown.outcome, DecisionStatus.BRAIN_FAILED,
                "OfferCleanupUnknownV2")
            or type(terminals.next_empty_slot_identity) is not object
            or terminals.next_empty_slot_identity is attempt.exact_empty_slot_identity
            or any(result.dispatched_decision is not None for result in (
                terminals.preparing_complete, terminals.stale,
                terminals.deadline_suppressed, terminals.internal_failure,
                terminals.cleanup_unknown, *terminals.admission_terminals.values()))):
        raise OfferCompositionError("INITIAL_TERMINAL_CANDIDATE_MISMATCH")
    expected_outcomes = {
        AdmissionStatus.REPLACED: (DecisionStatus.STALE, None),
        AdmissionStatus.EXPIRED: (DecisionStatus.DEADLINE_SUPPRESSED, None),
        AdmissionStatus.CANCELLED: (DecisionStatus.CANCELLED, None),
        AdmissionStatus.OVERLOADED: (DecisionStatus.BRAIN_FAILED, "AdmissionOverloaded"),
        AdmissionStatus.UNAVAILABLE: (DecisionStatus.BRAIN_FAILED, "AdmissionUnavailable"),
        AdmissionStatus.POISONED: (DecisionStatus.BRAIN_FAILED, "AdmissionPoisoned"),
    }
    if any(not is_closed_outcome(
            terminals.admission_terminals[status].outcome,
            expected_status, expected_error)
           for status, (expected_status, expected_error) in expected_outcomes.items()):
        raise OfferCompositionError("INITIAL_TERMINAL_CANDIDATE_MISMATCH")


def _register_initial_offer_source_v2(
    port: object, ticket: object,
) -> InitialOfferSourceV2:
    """Consume one exact T524 ticket into the private broker source slot."""
    if type(port) is not OfferPreparingCallerPortV2 or type(ticket) is not InitialOfferTicketV2:
        raise OfferCompositionError("OFFER_SOURCE_OWNER_MISMATCH")
    composition = port._composition
    if (type(composition) is not OfferPreparingCompositionV2
            or ticket.exact_composition is not composition
            or ticket.exact_caller_port is not port
            or ticket is not composition.active_initial_ticket_or_null
            or ticket.state != "ACTIVE"
            or composition.lifecycle != "ACTIVE" or composition.flow_state != "IDLE"
            or composition.initial_invocation_slot != "TICKET_ACTIVE"
            or composition.active_offer_source_or_null is not None):
        raise OfferCompositionError("OFFER_SOURCE_OWNER_MISMATCH")
    _validate_initial_ticket_edges_v2(composition)
    source = InitialOfferSourceV2(
        _ISSUER, exact_composition=composition, exact_caller_port=port,
        exact_arbiter=composition.exact_arbiter, exact_request=ticket.exact_request,
        exact_dispatch_deadline=ticket.exact_dispatch_deadline,
        exact_control_lane_or_null=None, exact_result_future_or_null=None,
        exact_client_lease_or_null=None, exact_offer_receipt_or_null=None,
        exact_offer_candidates_or_null=None,
        exact_initial_ticket=ticket, state="SOURCE_REGISTERED")
    _validate_initial_ticket_edges_v2(composition)
    object.__setattr__(ticket, "state", "CONSUMED_SOURCE")
    composition.active_offer_source_or_null = source
    composition.flow_state = "SOURCE_REGISTERED"
    return source


def _prepare_next_idle_generation_v2(port: object) -> None:
    """Allocate the next invocation's abort owner after prior clean retirement."""
    if type(port) is not OfferPreparingCallerPortV2:
        raise OfferCompositionError("COMPOSITION_OWNER_MISMATCH")
    composition = port._composition
    if (composition.lifecycle != "ACTIVE" or composition.flow_state != "IDLE"
            or composition.initial_invocation_slot != "EMPTY"
            or composition.active_initial_ticket_or_null is not None
            or composition.active_offer_source_or_null is not None):
        raise OfferCompositionError("COMPOSITION_NOT_IDLE")
    if port._initial_slot_identity is composition.initial_slot_identity:
        return
    bundle = PreTicketAbortBundleV2(
        _ISSUER, composition.initial_slot_identity, port._abort_failure_result)
    port._initial_slot_identity = composition.initial_slot_identity
    port._abort_bundle = bundle
    composition.prebuilt_pre_ticket_abort_bundle = bundle


def _publish_offer_from_frame_v2(
    session: object, raw: object, lane: object, future: object,
) -> bool:
    """Session-owned OFFER hook. False means the ordinary v1 route owns it."""
    from ai_client.llm.admission_client import (
        BrokerAdmissionSession, _ClientGenerationLease, _ControlLane,
    )
    from ai_client.llm.admission_types import AdmissionResult, AdmissionStatus
    if type(session) is not BrokerAdmissionSession:
        return False
    composition = session._offer_preparing_composition_v2
    if type(composition) is not OfferPreparingCompositionV2:
        return False
    source = composition.active_offer_source_or_null
    if (type(source) is not InitialOfferSourceV2
            or source.state != "ACQUIRE_REGISTERED"
            or source.exact_control_lane_or_null is not lane
            or source.exact_result_future_or_null is not future
            or type(lane) is not _ControlLane
            or session._control_lanes.get(source.exact_request.invocation_id) is not lane
            or session._results.get(source.exact_request.invocation_id) is not future
            or future.done() or lane.lease is not None or lane.disposition is not None
            or not isinstance(raw, dict)
            or set(raw) != {"invocation_id", "protocol", "queue_wait_microseconds", "type"}
            or raw.get("type") != "OFFER"
            or raw.get("invocation_id") != source.exact_request.invocation_id
            or type(raw.get("queue_wait_microseconds")) is not int
            or raw["queue_wait_microseconds"] < 0):
        raise OfferCompositionError("OFFER_FRAME_OWNER_MISMATCH")
    observed = composition.exact_clock_callable()
    if (isinstance(observed, bool) or not isinstance(observed, (int, float))
            or not math.isfinite(observed) or observed < 0):
        raise OfferCompositionError("OFFER_CLOCK_INVALID")
    lease = _ClientGenerationLease(session, source.exact_request.invocation_id, lane)
    result = AdmissionResult(
        AdmissionStatus.OFFERED, lease, raw["queue_wait_microseconds"])
    receipt = BrokerOfferOwnershipReceiptV2(
        _ISSUER, exact_composition=composition, exact_session=session,
        exact_offer_source=source, exact_request=source.exact_request,
        exact_raw_offer_frame_object=raw, exact_result_future=future,
        exact_control_lane=lane, exact_client_lease=lease,
        invocation_id=source.exact_request.invocation_id,
        queue_wait_microseconds=raw["queue_wait_microseconds"],
        offer_observed_monotonic=observed,
        offer_observed_monotonic_us=math.ceil(observed * 1_000_000),
        notification_failure_observation_or_null=None, state="PENDING_NOTIFY",
        issuer_capability=composition.issuer_capability)
    completion = (result, receipt)
    cleanup_sentinel = source.exact_initial_ticket.terminal_candidates.internal_failure
    notification_observation = NotificationFailureObservationV2(_ISSUER)
    candidates = OfferPublishCandidatesV2(
        _ISSUER, exact_client_lease=lease, exact_offer_receipt=receipt,
        exact_admission_result=result, exact_owned_completion_tuple=completion,
        exact_no_offer_cleanup_sentinel=cleanup_sentinel,
        preallocated_notification_failure_observation_slot=notification_observation)
    if (session._results.get(source.exact_request.invocation_id) is not future
            or session._control_lanes.get(source.exact_request.invocation_id) is not lane
            or source.state != "ACQUIRE_REGISTERED"
            or source.exact_offer_receipt_or_null is not None
            or source.exact_client_lease_or_null is not None
            or source.exact_offer_candidates_or_null is not None
            or future.done() or lane.lease is not None or lane.disposition is not None
            or completion[0] is not result or completion[1] is not receipt):
        raise OfferCompositionError("OFFER_CANDIDATE_STALE")
    lane.lease = lease
    object.__setattr__(source, "exact_client_lease_or_null", lease)
    object.__setattr__(source, "exact_offer_receipt_or_null", receipt)
    object.__setattr__(source, "exact_offer_candidates_or_null", candidates)
    object.__setattr__(source, "state", "OFFER_READY_UNNOTIFIED")
    composition.flow_state = "OFFER_READY_UNNOTIFIED"
    try:
        future.set_result(result)
    except BaseException:
        object.__setattr__(notification_observation, "future_done", future.done())
        visible = None
        if future.done() and not future.cancelled():
            try:
                visible = future.result()
            except BaseException:
                visible = None
        object.__setattr__(notification_observation, "exact_result_visible", visible)
        object.__setattr__(
            notification_observation, "session_results_member",
            session._results.get(source.exact_request.invocation_id) is future)
        object.__setattr__(
            receipt, "notification_failure_observation_or_null",
            notification_observation)
        object.__setattr__(receipt, "state", "RETIRED_UNKNOWN")
        object.__setattr__(source, "state", "NOTIFICATION_UNKNOWN")
        object.__setattr__(source.exact_initial_ticket, "state", "RETIRED_UNKNOWN")
        composition.flow_state = "OFFER_NOTIFICATION_UNKNOWN"
        composition.initial_invocation_slot = "CLEANUP_UNKNOWN"
        raise
    object.__setattr__(receipt, "state", "ACTIVE")
    object.__setattr__(source, "state", "OFFER_RESOLVED_REGISTERED")
    composition.flow_state = "OFFER_RESOLVED_REGISTERED"
    return True


async def _owned_acquire_initial_offer_v2(source: object):
    """Register and await the exact session lane without exposing its receipt."""
    from ai_client.llm.admission_client import BrokerAdmissionSession, _ControlLane
    if type(source) is not InitialOfferSourceV2:
        raise OfferCompositionError("OFFER_SOURCE_REQUIRED")
    composition = source.exact_composition
    session = composition.exact_broker_session
    if (type(session) is not BrokerAdmissionSession
            or source is not composition.active_offer_source_or_null
            or source.state != "SOURCE_REGISTERED"):
        raise OfferCompositionError("OFFER_SOURCE_OWNER_MISMATCH")
    request = source.exact_request
    lane = None
    future = None
    try:
        if (request.invocation_id in session._control_lanes
                or request.invocation_id in session._terminal_controls
                or request.invocation_id in session._results):
            raise OfferCompositionError("ACQUIRE_REGISTRY_BUSY")
        lane = _ControlLane(request.invocation_id)
        future = _new_offer_future_v2()
        if (request.invocation_id in session._control_lanes
                or request.invocation_id in session._terminal_controls
                or request.invocation_id in session._results
                or source.state != "SOURCE_REGISTERED"):
            raise OfferCompositionError("ACQUIRE_REGISTRY_BUSY")
        session._control_lanes[request.invocation_id] = lane
        session._results[request.invocation_id] = future
        object.__setattr__(source, "exact_control_lane_or_null", lane)
        object.__setattr__(source, "exact_result_future_or_null", future)
        object.__setattr__(source, "state", "ACQUIRE_REGISTERED")
        composition.flow_state = "ACQUIRE_REGISTERED"
        await session._send("ENQUEUE", request=__import__(
            "ai_client.llm.admission_client", fromlist=["request_to_wire"]
        ).request_to_wire(request))
        result = await asyncio.shield(future)
    except asyncio.CancelledError:
        if not _pre_offer_cleanup_is_owned_v2(source):
            raise
        if lane is not None and lane.lease is None:
            try:
                await session._ordinary_control(lane, "CANCEL")
            except BaseException:
                if _pre_offer_cleanup_is_owned_v2(source):
                    _hold_pre_offer_cleanup_unknown_v2(source)
                raise
        if _pre_offer_cleanup_is_owned_v2(source):
            _retire_pre_offer_source_v2(source, lane, future)
        raise
    except BaseException:
        if _pre_offer_cleanup_is_owned_v2(source):
            _retire_pre_offer_source_v2(source, lane, future)
        raise
    if (composition.lifecycle != "ACTIVE"
            or composition.active_offer_source_or_null is not source
            or source.state not in {"ACQUIRE_REGISTERED", "OFFER_RESOLVED_REGISTERED"}
            or source.exact_initial_ticket.state != "CONSUMED_SOURCE"):
        raise OfferCompositionError("OFFER_OWNER_RETIRED")
    from ai_client.llm.admission_types import AdmissionStatus
    if result.status is not AdmissionStatus.OFFERED:
        if result.status not in {
            AdmissionStatus.REPLACED, AdmissionStatus.EXPIRED,
            AdmissionStatus.OVERLOADED, AdmissionStatus.UNAVAILABLE,
            AdmissionStatus.CANCELLED, AdmissionStatus.POISONED,
        }:
            _hold_pre_offer_cleanup_unknown_v2(source)
            raise OfferCompositionError("PRE_OFFER_TERMINAL_INVALID")
        if not _retire_pre_offer_source_v2(
                source, lane, future, terminal_result=result):
            raise OfferCompositionError("PRE_OFFER_CLEANUP_UNCONFIRMED")
        return result, None
    if (source.state != "OFFER_RESOLVED_REGISTERED"
            or source.exact_offer_receipt_or_null is None
            or result.lease is not source.exact_client_lease_or_null):
        raise OfferCompositionError("OFFER_COMPLETION_MISMATCH")
    session._results.pop(request.invocation_id, None)
    object.__setattr__(source, "state", "OFFERED")
    composition.flow_state = "OFFERED"
    candidates = source.exact_offer_candidates_or_null
    if (type(candidates) is not OfferPublishCandidatesV2
            or candidates.exact_admission_result is not result
            or candidates.exact_offer_receipt is not source.exact_offer_receipt_or_null):
        raise OfferCompositionError("OFFER_COMPLETION_MISMATCH")
    return candidates.exact_owned_completion_tuple


def _new_offer_future_v2():
    return asyncio.get_running_loop().create_future()


def _pre_offer_cleanup_is_owned_v2(source: InitialOfferSourceV2) -> bool:
    composition = source.exact_composition
    ticket = source.exact_initial_ticket
    return (
        type(composition) is OfferPreparingCompositionV2
        and type(ticket) is InitialOfferTicketV2
        and composition.lifecycle == "ACTIVE"
        and composition.active_offer_source_or_null is source
        and composition.active_initial_ticket_or_null is ticket
        and source.state == "ACQUIRE_REGISTERED"
        and ticket.state == "CONSUMED_SOURCE"
        and composition.flow_state == "ACQUIRE_REGISTERED"
        and composition.initial_invocation_slot == "TICKET_ACTIVE"
    )


def _hold_pre_offer_cleanup_unknown_v2(source: InitialOfferSourceV2) -> None:
    composition = source.exact_composition
    ticket = source.exact_initial_ticket
    object.__setattr__(source, "state", "CLEANUP_UNKNOWN")
    object.__setattr__(ticket, "state", "RETIRED_UNKNOWN")
    composition.flow_state = "OFFER_CLEANUP_UNKNOWN"
    composition.initial_invocation_slot = "CLEANUP_UNKNOWN"


def _retire_pre_offer_source_v2(
    source: InitialOfferSourceV2,
    lane: object,
    future: object,
    *,
    terminal_result: object = None,
) -> bool:
    from ai_client.llm.admission_client import _ControlLane
    from ai_client.llm.admission_types import AdmissionResult, AdmissionStatus
    composition = source.exact_composition
    ticket = source.exact_initial_ticket
    session = composition.exact_broker_session
    invocation_id = source.exact_request.invocation_id
    terminal_status = None
    if terminal_result is not None:
        if (type(terminal_result) is not AdmissionResult
                or terminal_result.status not in {
                    AdmissionStatus.REPLACED, AdmissionStatus.EXPIRED,
                    AdmissionStatus.OVERLOADED, AdmissionStatus.UNAVAILABLE,
                    AdmissionStatus.CANCELLED, AdmissionStatus.POISONED,
                }
                or terminal_result.lease is not None
                or terminal_result.queue_wait_microseconds != 0
                or future is None or not future.done() or future.cancelled()
                or future.result() is not terminal_result):
            _hold_pre_offer_cleanup_unknown_v2(source)
            return False
        terminal_status = terminal_result.status
    if (not _pre_offer_cleanup_is_owned_v2(source)
            or type(lane) is not _ControlLane
            or lane.invocation_id != invocation_id
            or lane.callers != 0 or lane.lease is not None
            or session._results.get(invocation_id) is not future
            or session._control_lanes.get(invocation_id) is not lane
            or session._claimed_invocation is not None
            or session._activated_invocation is not None
            or invocation_id in session._call_ordinals
            or invocation_id in session._request_ids
            or (terminal_status is None and (
                lane.disposition is not None
                or invocation_id in session._terminal_controls))
            or (terminal_status is not None and (
                lane.disposition != terminal_status.value
                or invocation_id in session._terminal_controls))):
        _hold_pre_offer_cleanup_unknown_v2(source)
        return False
    session._results.pop(invocation_id, None)
    if terminal_status is None:
        session._control_lanes.pop(invocation_id, None)
    else:
        session._cleanup_control_lane(lane)
    closed = (
        invocation_id not in session._results
        and invocation_id not in session._control_lanes
        and lane.callers == 0 and lane.lease is None
        and session._claimed_invocation is None
        and session._activated_invocation is None
        and invocation_id not in session._call_ordinals
        and invocation_id not in session._request_ids
        and ((terminal_status is None
              and lane.disposition is None
              and invocation_id not in session._terminal_controls)
             or (terminal_status is not None
                 and lane.disposition == terminal_status.value
                 and session._terminal_controls.get(invocation_id) is terminal_status))
    )
    if not closed:
        _hold_pre_offer_cleanup_unknown_v2(source)
        return False
    object.__setattr__(source, "state", "RETIRED_CLEAN")
    object.__setattr__(ticket, "state", "RETIRED_CLEAN")
    composition.active_offer_source_or_null = None
    composition.active_initial_ticket_or_null = None
    composition.initial_pending_or_null = None
    composition.initial_slot_identity = ticket.terminal_candidates.next_empty_slot_identity
    composition.initial_invocation_slot = "EMPTY"
    composition.flow_state = "IDLE"
    candidates = source.exact_offer_candidates_or_null
    if type(candidates) is OfferPublishCandidatesV2:
        for field in (
            "exact_client_lease", "exact_offer_receipt", "exact_admission_result",
            "exact_owned_completion_tuple", "exact_no_offer_cleanup_sentinel",
            "preallocated_notification_failure_observation_slot",
        ):
            object.__setattr__(candidates, field, None)
    for field in (
        "exact_composition", "exact_caller_port", "exact_arbiter", "exact_request",
        "exact_dispatch_deadline", "exact_control_lane_or_null",
        "exact_result_future_or_null", "exact_client_lease_or_null",
        "exact_offer_receipt_or_null", "exact_offer_candidates_or_null",
        "exact_initial_ticket",
    ):
        object.__setattr__(source, field, None)
    for field in (
        "exact_composition", "exact_caller_port", "exact_arbiter",
        "exact_pending_invocation", "exact_dispatch_deadline",
        "exact_admission_driver_task", "exact_initial_slot", "exact_request",
        "exact_offer_task_or_null", "issuer_capability",
    ):
        object.__setattr__(ticket, field, None)
    return True


def _publish_preparing_from_offer_v2(
    port: object, ticket: object, result: object, receipt: object,
):
    """Consume one ACTIVE owned OFFER into the store's PREPARING placeholder."""
    from ai_client.discussion.capture_v2 import StateGenerationLeaseV2
    from ai_client.discussion.authority_capture_bridge_v2 import _phase_identity
    from ai_client.llm.admission_client import _ClientGenerationLease, _ControlLane
    from ai_client.llm.admission_types import AdmissionResult, AdmissionStatus
    from ai_client.discussion.state import DiscussionStateStore
    if (type(port) is not OfferPreparingCallerPortV2
            or type(ticket) is not InitialOfferTicketV2
            or type(receipt) is not BrokerOfferOwnershipReceiptV2
            or type(result) is not AdmissionResult
            or result.status is not AdmissionStatus.OFFERED):
        raise OfferCompositionError("PREPARING_OWNER_MISMATCH")
    composition = port._composition
    source = composition.active_offer_source_or_null
    store = composition.exact_discussion_store
    session = composition.exact_broker_session
    lane = receipt.exact_control_lane
    lease = receipt.exact_client_lease
    pending = ticket.exact_pending_invocation
    _validate_offer_runtime_owner_v2(composition, source, ticket, receipt, result)
    if (type(store) is not DiscussionStateStore
            or source is not receipt.exact_offer_source
            or type(source) is not InitialOfferSourceV2 or source.state != "OFFERED"
            or receipt.state != "ACTIVE" or receipt.exact_composition is not composition
            or receipt.exact_session is not session
            or receipt.exact_request is not ticket.exact_request
            or receipt.exact_result_future is not source.exact_result_future_or_null
            or receipt.exact_raw_offer_frame_object.get("invocation_id") != receipt.invocation_id
            or type(lane) is not _ControlLane or session._control_lanes.get(receipt.invocation_id) is not lane
            or lane.lease is not lease or type(lease) is not _ClientGenerationLease
            or result.lease is not lease or result.queue_wait_microseconds != receipt.queue_wait_microseconds
            or ticket is not source.exact_initial_ticket or ticket.state != "CONSUMED_SOURCE"
            or ticket.exact_admission_driver_task is not asyncio.current_task()
            or composition.flow_state != "OFFERED"
            or composition.initial_invocation_slot != "TICKET_ACTIVE"):
        raise OfferCompositionError("PREPARING_OWNER_MISMATCH")
    deadline = ticket.exact_dispatch_deadline
    material = composition.exact_authority_capture_bridge.prepare_current()
    current_world = composition.exact_world.snapshot()
    transport = composition.exact_world.transport_observations()
    current_deadline = transport.current_deadline
    capture = store._current_capture
    if (capture is None or current_deadline is None
            or material.base_capture_id != capture.capture_id
            or material.base_revision != store._revision
            or material.fact_revision != store._fact_revision
            or material.world_version != current_world.version
            or material.last_applied_sequence != current_world.last_applied_seq
            or capture.trigger.day != deadline.day
            or capture.trigger.phase != deadline.phase
            or material.phase_identity != _phase_identity(
                capture.game_id, deadline.day, deadline.phase)
            or material.action_generation != deadline.action_generation
            or material.connection_generation != deadline.connection_generation
            or deadline.phase != current_deadline.phase or deadline.day != current_deadline.day
            or deadline.mapping_order != current_deadline.mapping_order
            or deadline.action_generation != current_deadline.action_generation
            or deadline.connection_generation != current_deadline.connection_generation
            or current_deadline.local_deadline_monotonic is None
            or deadline.not_after_monotonic > current_deadline.local_deadline_monotonic):
        raise OfferCompositionError("PREPARING_FRESHNESS_MISMATCH")
    now = composition.exact_clock_callable()
    expires_us = math.floor(deadline.not_after_monotonic * 1_000_000)
    if (isinstance(now, bool) or not isinstance(now, (int, float))
            or not math.isfinite(now) or now < receipt.offer_observed_monotonic
            or math.ceil(now * 1_000_000) >= expires_us):
        raise OfferCompositionError("PREPARING_DEADLINE_EXPIRED")
    snapshot = StateGenerationLeaseV2(
        schema_version="aiwolf.state-generation-lease.v2",
        state_lease_id=receipt.invocation_id,
        owner_invocation_id=receipt.invocation_id,
        lease_revision=0, capture_id=None, status="PREPARING",
        base_revision=material.base_revision, world_version=material.world_version,
        last_applied_sequence=material.last_applied_sequence,
        fact_revision=material.fact_revision, phase_identity=material.phase_identity,
        action_generation=material.action_generation,
        connection_generation=material.connection_generation,
        input_sha256=None, catalog_sha256=None, option_catalog_sha256=None,
        recipient_proof_sha256=None, profile_bundle_sha256=None,
        stage_schema_sha256s=(), expires_at_monotonic_us=expires_us,
        broker_lease_ids=(receipt.invocation_id,), accepted_plan_sha256=None,
        stage_projections=(), reserved_attempt=None, attempt_ledger=(),
        recovery_proof_sha256=None)
    store_tuple = (capture, store._revision, store._fact_revision, store._epoch)
    owner = PreparingLeaseOwnerReceiptV2(
        _ISSUER, exact_composition=composition, exact_store=store,
        exact_initial_ticket=ticket, exact_pending=pending,
        exact_driver_task=asyncio.current_task(), exact_offer_receipt=receipt,
        exact_offer_source=source, exact_session=session, exact_control_lane=lane,
        exact_client_lease=lease, exact_request=ticket.exact_request,
        exact_dispatch_deadline=deadline, exact_prepared_material=material,
        exact_hidden_owner_receipt=material._hidden_owner_receipt,
        exact_store_capture_tuple=store_tuple, raw_deadline=deadline.not_after_monotonic,
        expires_at_monotonic_us=expires_us, state="PREPARING")
    preparing_bundle = (snapshot, owner)
    from .reserved_finalize_v2 import _prepare_abort_owned_v2
    preparing_cell = _prepare_abort_owned_v2(owner, preparing_bundle)
    _validate_offer_runtime_owner_v2(composition, source, ticket, receipt, result)
    final_deadline = composition.exact_world.transport_observations().current_deadline
    if ((capture, store._revision, store._fact_revision, store._epoch) != store_tuple
            or composition.exact_world.snapshot() is not current_world
            or final_deadline is not current_deadline
            or receipt.state != "ACTIVE" or source.state != "OFFERED"):
        raise OfferCompositionError("PREPARING_CAS_MISMATCH")
    if (session._results.get(receipt.invocation_id) is not None
            or session._control_lanes.get(receipt.invocation_id) is not lane
            or lane.lease is not lease or lane.disposition is not None
            or lane.callers != 0 or lease._claimed or lease._released or lease._retired
            or session._claimed_invocation is not None
            or session._activated_invocation is not None):
        raise OfferCompositionError("PREPARING_SESSION_MISMATCH")
    store._publish_preparing_lease_v2(
        composition, capture, snapshot, owner, preparing_cell)
    object.__setattr__(receipt, "state", "CONSUMED_PREPARING")
    object.__setattr__(ticket, "state", "CONSUMED_PREPARING")
    object.__setattr__(source, "state", "PREPARING")
    composition.flow_state = "PREPARING"
    composition.initial_invocation_slot = "PREPARING_HELD"
    if (store._preparing_lease_bundle_v2 is not preparing_bundle
            or store._state_generation_lease_v2 is not snapshot
            or store._preparing_lease_owner_receipt_v2 is not owner
            or receipt.state != "CONSUMED_PREPARING"
            or ticket.state != "CONSUMED_PREPARING"
            or source.state != "PREPARING"
            or composition.flow_state != "PREPARING"
            or composition.initial_invocation_slot != "PREPARING_HELD"
            or composition.active_initial_ticket_or_null is not ticket
            or composition.active_offer_source_or_null is not source):
        object.__setattr__(source, "state", "TERMINAL_HELD")
        composition.flow_state = "PREPARING_TERMINAL_HELD"
        composition.initial_invocation_slot = "PREPARING_HELD"
        raise OfferCompositionError("PREPARING_POST_PUBLISH_MISMATCH")
    return snapshot, owner


def _validate_offer_runtime_owner_v2(
    composition: object, source: object, ticket: object,
    receipt: object, result: object,
) -> None:
    from ai_client.llm.admission_client import GENERATION_IPC_PROTOCOL
    _validate_immutable_owner_edges_v2(
        composition.exact_broker_session,
        composition.exact_authority_capture_bridge,
        composition.exact_arbiter,
        composition.exact_clock_callable)
    session = composition.exact_broker_session
    raw = receipt.exact_raw_offer_frame_object
    candidates = source.exact_offer_candidates_or_null
    if (composition.lifecycle != "ACTIVE" or session._closed or session._transport_closed
            or session._reader_task.done() or session._writer.is_closing()
            or source is not composition.active_offer_source_or_null
            or ticket is not composition.active_initial_ticket_or_null
            or receipt.exact_offer_source is not source
            or receipt.exact_composition is not composition
            or not isinstance(raw, dict)
            or set(raw) != {"invocation_id", "protocol", "queue_wait_microseconds", "type"}
            or raw["type"] != "OFFER" or raw["protocol"] != GENERATION_IPC_PROTOCOL
            or raw["invocation_id"] != receipt.invocation_id
            or raw["invocation_id"] != source.exact_request.invocation_id
            or type(raw["queue_wait_microseconds"]) is not int
            or raw["queue_wait_microseconds"] < 0
            or raw["queue_wait_microseconds"] != receipt.queue_wait_microseconds
            or result.queue_wait_microseconds != receipt.queue_wait_microseconds
            or type(candidates) is not OfferPublishCandidatesV2
            or candidates.exact_offer_receipt is not receipt
            or candidates.exact_admission_result is not result
            or candidates.exact_client_lease is not receipt.exact_client_lease
            or candidates.exact_owned_completion_tuple[0] is not result
            or candidates.exact_owned_completion_tuple[1] is not receipt):
        raise OfferCompositionError("PREPARING_OWNER_MISMATCH")


async def _cleanup_offered_source_v2(source: object, terminal: object):
    """One-shot broker cleanup for a failure before PREPARING publication."""
    from ai_client.llm.admission_types import AdmissionStatus
    if type(source) is not InitialOfferSourceV2:
        raise OfferCompositionError("OFFER_CLEANUP_OWNER_MISMATCH")
    composition = source.exact_composition
    receipt = source.exact_offer_receipt_or_null
    ticket = source.exact_initial_ticket
    if (type(receipt) is not BrokerOfferOwnershipReceiptV2
            or receipt.state != "ACTIVE" or source.state != "OFFERED"):
        raise OfferCompositionError("OFFER_CLEANUP_OWNER_MISMATCH")
    lane = receipt.exact_control_lane
    lease = receipt.exact_client_lease
    session = receipt.exact_session
    object.__setattr__(source, "state", "CLEANUP_PENDING")
    composition.flow_state = "OFFER_CLEANUP_PENDING"
    try:
        status = await session._ordinary_control(lane, "CANCEL")
        if status is AdmissionStatus.GRANTED:
            object.__setattr__(source, "state", "CLEANUP_GRANTED")
            composition.flow_state = "OFFER_CLEANUP_GRANTED"
            object.__setattr__(source, "state", "RELEASE_PENDING")
            composition.flow_state = "OFFER_RELEASE_PENDING"
            await lease.release()
            status = lane.disposition
            clean = status in {
                "RELEASED", AdmissionStatus.EXPIRED.value,
                AdmissionStatus.POISONED.value,
            }
            clean = clean and (
                session._results.get(receipt.invocation_id) is None
                and session._control_lanes.get(receipt.invocation_id) is None
                and lane.lease is None and lane.callers == 0
                and lease._claimed is False and lease._released is True
                and lease._retired is (status == AdmissionStatus.EXPIRED.value)
                and session._claimed_invocation is None
                and session._activated_invocation is None
                and receipt.invocation_id not in session._call_ordinals
                and receipt.invocation_id not in session._request_ids
                and ((status == "RELEASED" and
                      receipt.invocation_id not in session._terminal_controls)
                     or (status != "RELEASED" and
                         session._terminal_controls.get(receipt.invocation_id)
                         is AdmissionStatus(status)))
            )
        else:
            clean = status in {
                AdmissionStatus.CANCELLED, AdmissionStatus.EXPIRED,
                AdmissionStatus.POISONED,
            }
            clean = clean and (
                session._results.get(receipt.invocation_id) is None
                and session._control_lanes.get(receipt.invocation_id) is None
                and lane.lease is None and lane.callers == 0
                and lease._claimed is False and lease._released is False
                and lease._retired is True
                and session._claimed_invocation is None
                and session._activated_invocation is None
                and receipt.invocation_id not in session._call_ordinals
                and receipt.invocation_id not in session._request_ids
                and session._terminal_controls.get(receipt.invocation_id) is status
            )
        if not clean:
            raise OfferCompositionError("OFFER_CLEANUP_UNCONFIRMED")
    except BaseException:
        object.__setattr__(receipt, "state", "RETIRED_UNKNOWN")
        object.__setattr__(source, "state", "CLEANUP_UNKNOWN")
        object.__setattr__(ticket, "state", "RETIRED_UNKNOWN")
        composition.flow_state = "OFFER_CLEANUP_UNKNOWN"
        composition.initial_invocation_slot = "CLEANUP_UNKNOWN"
        return ticket.terminal_candidates.cleanup_unknown
    object.__setattr__(receipt, "state", "RETIRED_CLEAN")
    object.__setattr__(source, "state", "RETIRED_CLEAN")
    object.__setattr__(ticket, "state", "RETIRED_CLEAN")
    composition.active_offer_source_or_null = None
    composition.active_initial_ticket_or_null = None
    composition.initial_pending_or_null = None
    composition.initial_slot_identity = ticket.terminal_candidates.next_empty_slot_identity
    composition.initial_invocation_slot = "EMPTY"
    composition.flow_state = "IDLE"
    candidates = source.exact_offer_candidates_or_null
    if type(candidates) is OfferPublishCandidatesV2:
        for field in (
            "exact_client_lease", "exact_offer_receipt", "exact_admission_result",
            "exact_owned_completion_tuple", "exact_no_offer_cleanup_sentinel",
            "preallocated_notification_failure_observation_slot",
        ):
            object.__setattr__(candidates, field, None)
    for field in (
        "exact_raw_offer_frame_object", "exact_result_future", "exact_control_lane",
        "exact_client_lease", "exact_request", "exact_offer_source", "exact_session",
        "exact_composition",
        "issuer_capability",
    ):
        object.__setattr__(receipt, field, None)
    for field in (
        "exact_control_lane_or_null", "exact_result_future_or_null",
        "exact_client_lease_or_null", "exact_offer_receipt_or_null", "exact_request",
        "exact_offer_candidates_or_null",
        "exact_dispatch_deadline", "exact_initial_ticket", "exact_caller_port",
        "exact_arbiter", "exact_composition",
    ):
        object.__setattr__(source, field, None)
    for field in (
        "exact_composition", "exact_caller_port", "exact_arbiter",
        "exact_pending_invocation", "exact_dispatch_deadline",
        "exact_admission_driver_task", "exact_initial_slot", "exact_request",
        "exact_offer_task_or_null", "issuer_capability",
    ):
        object.__setattr__(ticket, field, None)
    return terminal
