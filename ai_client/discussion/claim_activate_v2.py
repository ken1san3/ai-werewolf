"""Private, offline RESERVED claim/activation ownership (T536).

No generation or delivery entry point is reachable from this module.
"""
from __future__ import annotations

import asyncio
import math
import weakref
from dataclasses import replace
from threading import get_ident

from ai_client.llm.admission_types import AdmissionStatus
from .offer_composition_v2 import OfferCompositionError
from . import reserved_finalize_v2 as reserved

_ISSUER = object()
_set = object.__setattr__
_TERMINAL = (AdmissionStatus.CANCELLED, AdmissionStatus.EXPIRED,
             AdmissionStatus.POISONED, AdmissionStatus.UNAVAILABLE)
_REASONS = ("ACTIVE_SCOPE_END", "ACTIVE_CANCEL", "ACTIVE_STALE", "ACTIVE_EXPIRED")
_CAPTURE_FIELDS = ("capture_id", "state_lease_id", "base_revision", "world_version", "last_applied_sequence",
    "fact_revision", "phase_identity", "action_generation", "connection_generation", "input_sha256",
    "catalog_sha256", "option_catalog_sha256", "recipient_proof_sha256", "profile_bundle_sha256",
    "stage_schema_sha256s", "expires_at_monotonic_us")


class ClaimActivateError(OfferCompositionError):
    pass


class _Record:
    __slots__ = ("__weakref__",)

    def __init__(self, token, **values):
        if token is not _ISSUER or set(values) != set(type(self).__slots__):
            raise TypeError("private closed claim record")
        for name, value in values.items():
            _set(self, name, value)

    def __setattr__(self, name, value):
        raise TypeError("immutable claim record")

    def __repr__(self):
        return type(self).__name__ + "(<redacted>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("claim ownership cannot be copied or serialized")


class ClaimActivateCallerPortV2(_Record):
    __slots__ = ("exact_composition", "exact_arbiter", "exact_store", "exact_session", "exact_owner_loop")


class ClaimOwnerIdentityV2(_Record):
    __slots__ = ()


class ClaimActivateObservationV2(_Record):
    __slots__ = ("state", "claim_status_or_null", "cleanup_status_or_null", "failure_code_or_null",
                 "expected_reserved_identity", "observed_store_identity_or_null",
                 "observed_lane_disposition_or_null", "observed_claimed_invocation_or_null",
                 "observed_activated_invocation_or_null", "observed_lease_claimed_or_null",
                 "observed_lease_released_or_null", "observed_lease_retired_or_null")


class ClaimActivateOwnerV2(_Record):
    __slots__ = ("identity", "exact_port", "exact_composition", "exact_arbiter", "exact_store",
                 "exact_reserved_cell_weakref", "exact_reserved_identity", "exact_reserved_receipt",
                 "exact_reserved_lease", "exact_capture", "exact_session", "exact_lane",
                 "exact_client_lease", "exact_request", "exact_dispatch_deadline", "exact_owner_loop",
                 "exact_claim_driver_task", "claim_start_gate", "claim_task_or_null",
                 "world_watcher_task_or_null", "cancel_watcher_task_or_null", "deadline_watcher_task_or_null",
                 "hard_deadline_monotonic", "claim_wait_deadline_monotonic", "settlement_deadline_monotonic",
                 "activation_context_or_null", "active_candidate_cell_or_null", "active_receipt_or_null",
                 "active_cleanup_candidates_or_null", "active_cleanup_selection", "cleanup_task_or_null",
                 "observation", "build_origin", "state")


class PrepublicationTaskHoldV2(_Record):
    __slots__ = ("exact_gate", "claim_task_or_null", "world_watcher_task_or_null",
                 "cancel_watcher_task_or_null", "deadline_watcher_task_or_null",
                 "exact_tasks_or_null", "failure_code")


class ActiveCaptureOwnerReceiptV2(_Record):
    __slots__ = ("schema_version", "predecessor_identity", "exact_reserved_receipt", "exact_capture",
                 "exact_lease", "claim_owner_identity", "claim_owner_weakref", "exact_activation_context",
                 "exact_session", "exact_lane", "exact_client_lease", "exact_request", "exact_deadline",
                 "cell_identity", "cell_weakref", "postcheck_observation", "cleanup_selection_slot", "state")


class ActiveInvalidatedOwnerReceiptV2(_Record):
    __slots__ = ("schema_version", "predecessor_identity", "exact_active_lease", "exact_capture",
                 "claim_owner_identity", "invalidated_lease", "reason", "cell_identity", "cell_weakref", "state")


class ActiveCleanupCandidateV2(_Record):
    __slots__ = ("reason", "candidate_cell")


class ActiveCleanupSelectionV2(_Record):
    __slots__ = ("candidates", "selected_reason_or_null", "selected_candidate_identity_or_null", "state")


class ActivePostcheckObservationV2(_Record):
    __slots__ = ("expected_identity", "observed_identity_or_null", "observed_stage_or_null",
                 "first_mismatch_or_null", "state")


def _fail():
    raise ClaimActivateError("CLAIM_ROW_MISMATCH")


def _capture_matches(lease, capture):
    for field in _CAPTURE_FIELDS:
        if getattr(lease, field) != getattr(capture, field):
            _fail()


def _observable_cell(store, cell):
    try:
        return reserved._valid_cell_v2(store, cell)
    except (AttributeError, TypeError, ValueError, KeyError):
        return False


def _new_port_v2(c):
    return ClaimActivateCallerPortV2(_ISSUER, exact_composition=c, exact_arbiter=c.exact_arbiter,
        exact_store=c.exact_discussion_store, exact_session=c.exact_broker_session,
        exact_owner_loop=c.owner_thread_loop)


def _port(port):
    from .offer_composition_v2 import OfferPreparingCompositionV2
    if type(port) is not ClaimActivateCallerPortV2:
        _fail()
    c = port.exact_composition
    if (type(c) is not OfferPreparingCompositionV2 or c.claim_activate_port is not port
            or c.exact_arbiter is not port.exact_arbiter or c.exact_discussion_store is not port.exact_store
            or c.exact_broker_session is not port.exact_session
            or c.owner_thread_loop != (get_ident(), asyncio.get_running_loop())
            or port.exact_owner_loop is not c.owner_thread_loop
            or c.exact_arbiter._offer_preparing_composition_v2 is not c
            or c.exact_broker_session._offer_preparing_composition_v2 is not c
            or c.exact_discussion_store._offer_preparing_composition_v2 is not c):
        _fail()
    return c


def _open_session(session):
    return (not session._closed and not session._transport_closed
            and not session._reader_task.done() and not session._writer.is_closing())


def _registry_absent(session, invocation):
    return (all(invocation not in r for r in (session._results, session._acks, session._attachments))
            and not any(key[0] == invocation for key in session._generations))


def _held_shape(p):
    s, lane, lease, invocation = p.exact_session, p.exact_control_lane, p.exact_client_lease, p.exact_request.invocation_id
    return (_open_session(s) and _registry_absent(s, invocation)
        and all(invocation not in r for r in (s._call_ordinals, s._request_ids, s._terminal_controls))
        and s._control_lanes.get(invocation) is lane and lane.lease is lease
        and lane.disposition is None and type(lane.callers) is int and lane.callers == 0 and lane.abandon_disposition is None
        and lease._claimed is False and lease._released is False and lease._retired is False
        and lease._abandon_disposition is None and lease._session is s and lease._lane is lane
        and lease.invocation_id == invocation and lane.invocation_id == invocation
        and s._claimed_invocation is None and s._activated_invocation is None)


def _broker_shape(owner, kind, status=None, *, activated=False, abandoned=False):
    lane = owner.exact_lane if type(owner) is ClaimActivateOwnerV2 else owner.exact_control_lane
    s, lease, invocation = (owner.exact_session,
        owner.exact_client_lease, owner.exact_request.invocation_id)
    if (not _open_session(s) or not _registry_absent(s, invocation) or type(lane.callers) is not int or lane.callers != 0
            or type(lane.disposition) is not str
            or lease._session is not s or lease._lane is not lane or lease.invocation_id != invocation
            or lane.invocation_id != invocation):
        return False
    if kind == "GRANTED":
        return (lane.disposition == "GRANTED" and lane.lease is lease
            and s._control_lanes.get(invocation) is lane and invocation not in s._terminal_controls
            and lease._claimed is True and lease._released is False and lease._retired is False
            and lease._abandon_disposition is None and lane.abandon_disposition is None
            and s._claimed_invocation == invocation
            and s._activated_invocation == (invocation if activated else None)
            and type(s._call_ordinals.get(invocation)) is int and s._call_ordinals[invocation] == 0
            and type(s._request_ids.get(invocation)) is set and not s._request_ids[invocation])
    if (lane.lease is not None or invocation in s._control_lanes
            or s._claimed_invocation is not None or s._activated_invocation is not None
            or invocation in s._call_ordinals or invocation in s._request_ids):
        return False
    if kind == "TERMINAL":
        if not any(status is value for value in _TERMINAL) or (abandoned and status is AdmissionStatus.UNAVAILABLE):
            return False
        abandon = "ABANDON_ACKNOWLEDGED" if abandoned else None
        return (lane.disposition == status.value and s._terminal_controls.get(invocation) is status
            and lease._claimed is False and lease._released is False and lease._retired is True
            and lease._abandon_disposition == abandon and lane.abandon_disposition == abandon)
    if kind == "RELEASE":
        if type(status) is not str or status not in ("RELEASED", "EXPIRED", "POISONED") or lane.disposition != status:
            return False
        expired = status == "EXPIRED"
        abandon = "ABANDON_ACKNOWLEDGED" if expired else None
        terminal = None if status == "RELEASED" else AdmissionStatus(status)
        return (s._terminal_controls.get(invocation) is terminal
            and (terminal is not None or invocation not in s._terminal_controls)
            and lease._claimed is False and lease._released is True and lease._retired is expired
            and lease._abandon_disposition == abandon and lane.abandon_disposition == abandon)
    return False


def _begin(port):
    try:
        return _begin_contents(port)
    except (AttributeError, TypeError, KeyError, ValueError):
        raise ClaimActivateError("CLAIM_ROW_MISMATCH") from None


def _begin_contents(port):
    c = _port(port)
    if (c.claim_activate_owner_or_null is not None or c.claim_activate_prepublication_hold_or_null is not None
            or c.claim_activate_preclaim_hold_or_null is not None
            or c.claim_activate_outcome_or_null is not None or c.claim_activate_cancel_event.is_set()
            or c.flow_state != "RESERVED" or c.lifecycle != "ACTIVE"
            or c.initial_invocation_slot != "RESERVED_HELD"):
        _fail()
    reserved._validate_published_row_v2(c)
    cell = c.exact_discussion_store._lease_bundle_cell_v2
    if cell.stage != "RESERVED":
        _fail()
    r = cell.exact_tuple[-1]
    p = r.exact_preparing_receipt
    r.exact_capture.__post_init__()
    r.exact_lease.__post_init__()
    _capture_matches(r.exact_lease, r.exact_capture)
    from .offer_composition_v2 import _validate_immutable_owner_edges_v2
    _validate_immutable_owner_edges_v2(c.exact_broker_session, c.exact_authority_capture_bridge,
                                     c.exact_arbiter, c.exact_clock_callable)
    if (not _open_session(c.exact_broker_session) or c.exact_arbiter._active is not None
            or c.exact_arbiter._admission_driver is not None or not p.exact_pending.result.done()
            or p.exact_pending.result.cancelled()
            or p.exact_pending.result.result() is not p.exact_initial_ticket.terminal_candidates.preparing_complete
            or p.exact_client_lease._abandon_disposition is not None
            or p.exact_control_lane.abandon_disposition is not None or not _held_shape(p)):
        _fail()
    return c, cell, r


def _fresh(c, r):
    p = r.exact_preparing_receipt
    d = p.exact_dispatch_deadline
    current = c.exact_world.transport_observations().current_deadline
    task = asyncio.current_task()
    cancelling = (getattr(task, "cancelling", lambda: 0)() or getattr(task, "_must_cancel", False))
    if (cancelling or c.exact_arbiter._stopped or c.claim_activate_cancel_event.is_set()
            or math.ceil(c.exact_clock_callable() * 1_000_000) >= r.exact_lease.expires_at_monotonic_us
            or current is None or any(getattr(current, n) != getattr(d, n) for n in (
                "day", "phase", "mapping_order", "action_generation", "connection_generation"))
            or current.local_deadline_monotonic != d.not_after_monotonic
            or c.exact_clock_callable() >= d.not_after_monotonic):
        return False
    try:
        source = reserved._read_source_v2(p)
    except Exception:
        return False
    return source.stable_fingerprint == r.exact_source_read.stable_fingerprint


def _new_owner(c, cell, r):
    p = r.exact_preparing_receipt
    hard = min(p.exact_dispatch_deadline.not_after_monotonic,
               r.exact_lease.expires_at_monotonic_us / 1_000_000)
    claim_end = hard - p.exact_session._config.cancellation_grace_seconds
    if not c.exact_clock_callable() < claim_end < hard:
        raise ClaimActivateError("CLAIM_DEADLINE_EXPIRED")
    observation = ClaimActivateObservationV2(_ISSUER, **{name:
        ("EMPTY" if name == "state" else cell.bundle_identity if name == "expected_reserved_identity" else None)
        for name in ClaimActivateObservationV2.__slots__})
    values = dict.fromkeys(ClaimActivateOwnerV2.__slots__)
    values.update(identity=ClaimOwnerIdentityV2(_ISSUER), exact_port=c.claim_activate_port,
        exact_composition=c, exact_arbiter=c.exact_arbiter, exact_store=c.exact_discussion_store,
        exact_reserved_cell_weakref=r.cell_weakref, exact_reserved_identity=cell.bundle_identity,
        exact_reserved_receipt=r, exact_reserved_lease=r.exact_lease, exact_capture=r.exact_capture,
        exact_session=p.exact_session, exact_lane=p.exact_control_lane, exact_client_lease=p.exact_client_lease,
        exact_request=p.exact_request, exact_dispatch_deadline=p.exact_dispatch_deadline,
        exact_owner_loop=c.owner_thread_loop, exact_claim_driver_task=asyncio.current_task(),
        claim_start_gate=asyncio.Event(), hard_deadline_monotonic=hard,
        claim_wait_deadline_monotonic=claim_end, settlement_deadline_monotonic=hard,
        observation=observation, build_origin="BUILD_NOT_STARTED", state="TASKS_CREATED_LOCAL")
    return ClaimActivateOwnerV2(_ISSUER, **values)


def _static(owner):
    c = _port(owner.exact_port)
    from .offer_composition_v2 import _validate_immutable_owner_edges_v2
    _validate_immutable_owner_edges_v2(c.exact_broker_session, c.exact_authority_capture_bridge,
                                     c.exact_arbiter, c.exact_clock_callable)
    r = owner.exact_reserved_receipt
    p = r.exact_preparing_receipt
    r.exact_capture.__post_init__()
    r.exact_lease.__post_init__()
    _capture_matches(r.exact_lease, r.exact_capture)
    if (type(owner) is not ClaimActivateOwnerV2 or c.claim_activate_owner_or_null is not owner
            or c.claim_activate_preclaim_hold_or_null is not None
            or type(owner.identity) is not ClaimOwnerIdentityV2
            or owner.exact_composition is not c or owner.exact_arbiter is not c.exact_arbiter
            or owner.exact_store is not c.exact_discussion_store or owner.exact_session is not c.exact_broker_session
            or owner.exact_owner_loop is not c.owner_thread_loop
            or owner.exact_reserved_identity is not r.cell_identity
            or owner.exact_reserved_cell_weakref is not r.cell_weakref
            or owner.exact_reserved_lease is not r.exact_lease or owner.exact_capture is not r.exact_capture
            or owner.exact_lane is not p.exact_control_lane or owner.exact_client_lease is not p.exact_client_lease
            or owner.exact_request is not p.exact_request or owner.exact_dispatch_deadline is not p.exact_dispatch_deadline
            or p.exact_pending.dispatch_deadline is not p.exact_dispatch_deadline
            or p.exact_composition is not c or p.exact_store is not c.exact_discussion_store
            or p.exact_session is not owner.exact_session
            or c.active_initial_ticket_or_null is not p.exact_initial_ticket
            or c.active_offer_source_or_null is not p.exact_offer_source
            or p.exact_offer_source.exact_offer_receipt_or_null is not p.exact_offer_receipt
            or p.exact_offer_receipt.exact_control_lane is not owner.exact_lane
            or p.exact_offer_receipt.exact_client_lease is not owner.exact_client_lease
            or p.exact_offer_receipt.exact_request is not owner.exact_request
            or p.exact_initial_ticket.exact_pending_invocation is not p.exact_pending
            or p.exact_offer_source.exact_initial_ticket is not p.exact_initial_ticket
            or p.exact_offer_receipt.exact_offer_source is not p.exact_offer_source
            or p.exact_offer_receipt.exact_session is not owner.exact_session
            or c.exact_caller_port._composition is not c
            or r.exact_profile_bundle is not c.generation_profile_bundle_v2
            or r.exact_source_read.exact_prepared_material is not p.exact_prepared_material
            or c.initial_pending_or_null is not p.exact_pending
            or c.exact_arbiter._active is not None or c.exact_arbiter._admission_driver is not None
            or c.exact_arbiter._admission_wait_task is not None or c.exact_arbiter._admission_brain_task is not None
            or c.exact_arbiter._admission_lease is not None or c.exact_arbiter._active_task is not None
            or not p.exact_pending.result.done() or p.exact_pending.result.cancelled()
            or p.exact_pending.result.result() is not p.exact_initial_ticket.terminal_candidates.preparing_complete):
        _fail()
    hard = min(owner.exact_dispatch_deadline.not_after_monotonic,
               owner.exact_reserved_lease.expires_at_monotonic_us / 1_000_000)
    if (owner.hard_deadline_monotonic != hard or owner.settlement_deadline_monotonic != hard
            or owner.claim_wait_deadline_monotonic != hard - owner.exact_session._config.cancellation_grace_seconds
            or not isinstance(owner.exact_claim_driver_task, asyncio.Task)
            or owner.exact_claim_driver_task.get_loop() is not asyncio.get_running_loop()):
        _fail()
    pre = p.abort_bundle
    if (pre.state != "RETIRED" or pre.invalidated_receipt.state != "RETIRED"
            or pre.abort_selection.state != "RETIRED_UNSELECTED"
            or r.notification_observation.exact_expected_result is not p.exact_initial_ticket.terminal_candidates.preparing_complete
            or r.postcheck_observation.state != ("RECORDED" if owner.state == "I2_POSTCHECK_UNKNOWN" else "EMPTY")):
        _fail()
    _validate_build_origin(owner)
    return c


def _observe(owner, state, code, claim=None, cleanup=None):
    observation = owner.observation
    if observation.state != "EMPTY":
        _fail()
    cell = owner.exact_store._lease_bundle_cell_v2
    _set(observation, "claim_status_or_null", claim)
    _set(observation, "cleanup_status_or_null", cleanup)
    _set(observation, "failure_code_or_null", code)
    _set(observation, "observed_store_identity_or_null",
         cell.bundle_identity if _observable_cell(owner.exact_store, cell) else None)
    disposition = owner.exact_lane.disposition
    _set(observation, "observed_lane_disposition_or_null", disposition if type(disposition) is str and disposition in (
        "GRANTED", "CANCELLED", "EXPIRED", "POISONED", "UNAVAILABLE", "RELEASED") else None)
    invocation = owner.exact_request.invocation_id
    claimed = owner.exact_session._claimed_invocation
    activated = owner.exact_session._activated_invocation
    _set(observation, "observed_claimed_invocation_or_null", invocation if type(claimed) is str and claimed == invocation else None)
    _set(observation, "observed_activated_invocation_or_null", invocation if type(activated) is str and activated == invocation else None)
    for name in ("claimed", "released", "retired"):
        flag = getattr(owner.exact_client_lease, "_" + name)
        _set(observation, "observed_lease_" + name + "_or_null", flag if type(flag) is bool else None)
    _set(observation, "state", state)


def _unknown(owner, flow, code, *, claim=None, cleanup=None):
    _observe(owner, "POSTCHECK_UNKNOWN" if flow == "ACTIVE_POSTCHECK_UNKNOWN" else
             "CLEANUP_UNKNOWN" if "CLEANUP" in flow else "CLAIM_UNKNOWN", code, claim, cleanup)
    _set(owner, "state", flow)
    owner.exact_composition.flow_state = flow
    return None


def _validate_observation(owner):
    o = owner.observation
    if type(o) is not ClaimActivateObservationV2 or o.expected_reserved_identity is not owner.exact_reserved_identity:
        _fail()
    if o.state == "EMPTY":
        if any(getattr(o, field) is not None for field in ClaimActivateObservationV2.__slots__
               if field not in ("state", "expected_reserved_identity")):
            _fail()
        return
    if not all(getattr(o, name) is None or type(getattr(o, name)) is bool for name in (
            "observed_lease_claimed_or_null", "observed_lease_released_or_null", "observed_lease_retired_or_null")):
        _fail()
    if o.observed_lane_disposition_or_null not in (None, "GRANTED", "CANCELLED", "EXPIRED", "POISONED", "UNAVAILABLE", "RELEASED"):
        _fail()
    if (o.observed_store_identity_or_null is not None
            and type(o.observed_store_identity_or_null) not in tuple(reserved._CELL_IDENTITY_TYPES.values())):
        _fail()
    for name in ("observed_claimed_invocation_or_null", "observed_activated_invocation_or_null"):
        if getattr(o, name) not in (None, owner.exact_request.invocation_id):
            _fail()
    if o.state == "CLAIM_TERMINAL":
        if (not any(o.claim_status_or_null is status for status in _TERMINAL)
                or o.cleanup_status_or_null is not None or o.failure_code_or_null not in (
                    "CLAIM_RETURNED_NON_GRANT", "CLAIM_CANCELLED_NON_GRANT", "CLAIM_CANCELLED_ABANDONED")
                or (o.failure_code_or_null == "CLAIM_CANCELLED_ABANDONED"
                    and o.claim_status_or_null is AdmissionStatus.UNAVAILABLE)):
            _fail()
        if (o.observed_lease_claimed_or_null is not False or o.observed_lease_released_or_null is not False
                or o.observed_lease_retired_or_null is not True or o.observed_claimed_invocation_or_null is not None
                or o.observed_activated_invocation_or_null is not None
                or o.observed_lane_disposition_or_null != o.claim_status_or_null.value):
            _fail()
    elif o.state == "CLAIM_UNKNOWN":
        if (o.cleanup_status_or_null is not None or o.failure_code_or_null not in (
                "CLAIM_TASK_RAISED", "CLAIM_SETTLE_TIMEOUT", "WATCHER_CLEANUP_FAILED", "CLAIM_SHAPE_MISMATCH")
                or (o.claim_status_or_null is not None
                    and not any(o.claim_status_or_null is status for status in _TERMINAL + (AdmissionStatus.GRANTED,)))):
            _fail()
    elif o.state == "CLEANUP_TERMINAL":
        if (o.claim_status_or_null is not AdmissionStatus.GRANTED
                or o.cleanup_status_or_null not in ("RELEASED", "EXPIRED", "POISONED")
                or o.failure_code_or_null not in _REASONS + ("GRANTED_CLEAN_RELEASE",)):
            _fail()
        if (o.observed_lease_claimed_or_null is not False or o.observed_lease_released_or_null is not True
                or o.observed_lease_retired_or_null is not (o.cleanup_status_or_null == "EXPIRED")
                or o.observed_claimed_invocation_or_null is not None or o.observed_activated_invocation_or_null is not None
                or o.observed_lane_disposition_or_null != o.cleanup_status_or_null):
            _fail()
    elif o.state == "CLEANUP_UNKNOWN":
        if (o.claim_status_or_null is not AdmissionStatus.GRANTED
                or o.cleanup_status_or_null not in (None, "UNAVAILABLE")
                or o.failure_code_or_null not in ("RELEASE_START_RAISED", "RELEASE_TASK_RAISED", "RELEASE_TASK_CANCELLED", "RELEASE_TIMEOUT", "RELEASE_UNAVAILABLE", "RELEASE_SHAPE_MISMATCH")):
            _fail()
    elif o.state == "POSTCHECK_UNKNOWN":
        if (o.claim_status_or_null is not AdmissionStatus.GRANTED or o.cleanup_status_or_null is not None
                or o.failure_code_or_null != "ACTIVE_POSTCHECK_MISMATCH"):
            _fail()
    else:
        _fail()


def _reserved_edges(owner):
    c = _static(owner)
    r = owner.exact_reserved_receipt
    p = r.exact_preparing_receipt
    cell = owner.exact_reserved_cell_weakref()
    if (not reserved._valid_cell_v2(owner.exact_store, cell)
            or owner.exact_store._lease_bundle_cell_v2 is not cell or cell.stage != "RESERVED"
            or cell.exact_tuple[-1] is not r or cell.exact_tuple[0] is not owner.exact_reserved_lease
            or cell.exact_tuple[1] is not owner.exact_capture
            or r.state != "RESERVED" or r.abort_bundle.state != "ARMED"
            or r.abort_bundle.invalidated_receipt.state != "ARMED"
            or p.exact_initial_ticket.state != "CONSUMED_RESERVED"
            or p.exact_offer_source.state != "RESERVED" or p.exact_offer_receipt.state != "CONSUMED_RESERVED"
            or c.lifecycle != "ACTIVE" or c.initial_invocation_slot != "RESERVED_HELD"):
        _fail()
    return cell


async def _gated_claim(owner):
    await owner.claim_start_gate.wait()
    return await owner.exact_client_lease.claim()


async def _gated_watch(owner, kind):
    await owner.claim_start_gate.wait()
    if kind == "world":
        return await owner.exact_composition.exact_world.wait_for_update(owner.exact_capture.world_version)
    if kind == "cancel":
        return await owner.exact_composition.claim_activate_cancel_event.wait()
    return await asyncio.sleep(max(0, owner.claim_wait_deadline_monotonic - owner.exact_composition.exact_clock_callable()))


async def _settle(tasks, deadline, clock):
    """Wait without cancellation propagation; retain live tasks on timeout."""
    if not tasks:
        return True
    while True:
        if all(task.done() for task in tasks):
            for task in tasks:
                if not task.cancelled():
                    task.exception()
            return True
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        try:
            _, pending = await asyncio.wait(tasks, timeout=remaining)
            if pending:
                return False
        except asyncio.CancelledError:
            continue


_TASK_SLOTS = ("claim_task_or_null", "world_watcher_task_or_null", "cancel_watcher_task_or_null", "deadline_watcher_task_or_null")


def _prepublication_projection(hold):
    return (hold.claim_task_or_null, hold.world_watcher_task_or_null,
            hold.cancel_watcher_task_or_null, hold.deadline_watcher_task_or_null)


def _publish_claim_tasks(owner, hold):
    for name in _TASK_SLOTS:
        _set(owner, name, getattr(hold, name))
    c = owner.exact_composition
    c.claim_activate_owner_or_null = owner
    _set(owner, "state", "CLAIM_WAIT")
    c.flow_state = "CLAIM_WAIT"
    c.claim_activate_prepublication_hold_or_null = None
    owner.claim_start_gate.set()


async def _create_tasks(owner):
    c = owner.exact_composition
    try:
        hold = PrepublicationTaskHoldV2(_ISSUER, exact_gate=owner.claim_start_gate,
            claim_task_or_null=None, world_watcher_task_or_null=None, cancel_watcher_task_or_null=None,
            deadline_watcher_task_or_null=None, exact_tasks_or_null=None, failure_code="PREPUBLICATION_TASK_UNKNOWN")
    except Exception:
        return False
    c.claim_activate_prepublication_hold_or_null = hold
    coroutine = None
    try:
        for name, kind in zip(_TASK_SLOTS, ("claim", "world", "cancel", "deadline")):
            coroutine = _gated_claim(owner) if kind == "claim" else _gated_watch(owner, kind)
            task = asyncio.create_task(coroutine)
            _set(hold, name, task)
            coroutine = None
        _set(hold, "exact_tasks_or_null", _prepublication_projection(hold))
        _publish_claim_tasks(owner, hold)
        return True
    except BaseException:
        if coroutine is not None:
            coroutine.close()
        c.flow_state = "CLAIM_PREPUBLICATION_UNKNOWN"
        # The shared fixed slots already own every returned task. Cleanup allocation
        # or cancellation failure cannot remove that durable root.
        try:
            for name in _TASK_SLOTS:
                task = getattr(hold, name)
                if task is not None:
                    task.cancel()
            tasks = [getattr(hold, name) for name in _TASK_SLOTS if getattr(hold, name) is not None]
            if not tasks or await _settle(tasks, owner.settlement_deadline_monotonic, c.exact_clock_callable):
                c.claim_activate_prepublication_hold_or_null = None
                c.flow_state = "RESERVED"
        except BaseException:
            pass
        return False


async def _wait_claim(owner):
    claim = owner.claim_task_or_null
    watchers = (owner.world_watcher_task_or_null, owner.cancel_watcher_task_or_null, owner.deadline_watcher_task_or_null)
    interrupted = False
    try:
        await asyncio.wait((claim,) + watchers, return_when=asyncio.FIRST_COMPLETED,
            timeout=max(0, owner.claim_wait_deadline_monotonic - owner.exact_composition.exact_clock_callable()))
        interrupted = any(task.done() and not task.cancelled() for task in watchers)
    except asyncio.CancelledError:
        interrupted = True
        owner.exact_composition.claim_activate_cancel_event.set()
    if not claim.done():
        claim.cancel()
        interrupted = True
    for task in watchers:
        if not task.done():
            task.cancel()
    if not await _settle((claim,) + watchers, owner.settlement_deadline_monotonic, owner.exact_composition.exact_clock_callable):
        _unknown(owner, "CLAIM_RESULT_UNKNOWN", "CLAIM_SETTLE_TIMEOUT")
        return None, True
    if any(not task.cancelled() and task.exception() is not None for task in watchers):
        _unknown(owner, "CLAIM_RESULT_UNKNOWN", "WATCHER_CLEANUP_FAILED")
        return None, True
    for name in ("world_watcher_task_or_null", "cancel_watcher_task_or_null", "deadline_watcher_task_or_null"):
        _set(owner, name, None)
    _set(owner, "state", "CLAIM_DONE_BUILD")
    if claim.cancelled():
        status = next((value for value in _TERMINAL if value.value == owner.exact_lane.disposition), None)
        abandoned = owner.exact_client_lease._abandon_disposition == "ABANDON_ACKNOWLEDGED"
        if status is not None and _broker_shape(owner, "TERMINAL", status, abandoned=abandoned):
            code = "CLAIM_CANCELLED_ABANDONED" if abandoned else "CLAIM_CANCELLED_NON_GRANT"
            _observe(owner, "CLAIM_TERMINAL", code, status)
            owner.exact_composition.flow_state = code + "_TERMINAL"
            return status, True
        _unknown(owner, "CLAIM_RESULT_UNKNOWN", "CLAIM_SHAPE_MISMATCH")
        return None, True
    if claim.exception() is not None:
        _unknown(owner, "CLAIM_RESULT_UNKNOWN", "CLAIM_TASK_RAISED")
        return None, interrupted
    status = claim.result()
    if status is AdmissionStatus.GRANTED:
        if not _broker_shape(owner, "GRANTED"):
            _unknown(owner, "CLAIM_RESULT_UNKNOWN", "CLAIM_SHAPE_MISMATCH", claim=status)
            return None, interrupted
        return status, interrupted
    if _broker_shape(owner, "TERMINAL", status):
        _observe(owner, "CLAIM_TERMINAL", "CLAIM_RETURNED_NON_GRANT", status)
        owner.exact_composition.flow_state = "CLAIM_RETURNED_NON_GRANT_TERMINAL"
        return status, interrupted
    _unknown(owner, "CLAIM_RESULT_UNKNOWN", "CLAIM_SHAPE_MISMATCH")
    return None, interrupted


def _exit_context(owner):
    context = owner.activation_context_or_null
    if context is not None and context._entered:
        context.__exit__(None, None, None)
    if owner.exact_session._activated_invocation is not None:
        _fail()


async def _release_once(owner, reason, active=False):
    flow = "ACTIVE_CLEANUP_UNKNOWN" if active else "CLAIM_CLEANUP_UNKNOWN"
    if owner.cleanup_task_or_null is not None:
        _fail()
    deadline = asyncio.get_running_loop().time() + owner.exact_session._config.shutdown_grace_seconds
    coroutine = None
    try:
        _exit_context(owner)
        coroutine = owner.exact_client_lease.release()
        task = asyncio.create_task(coroutine)
        coroutine = None
        _set(owner, "cleanup_task_or_null", task)
    except BaseException:
        if coroutine is not None:
            coroutine.close()
        _unknown(owner, flow, "RELEASE_START_RAISED", claim=AdmissionStatus.GRANTED)
        return False
    if not await _settle((task,), deadline, asyncio.get_running_loop().time):
        _unknown(owner, flow, "RELEASE_TIMEOUT", claim=AdmissionStatus.GRANTED)
        return False
    if task.cancelled() or task.exception() is not None:
        _unknown(owner, flow, "RELEASE_TASK_CANCELLED" if task.cancelled() else "RELEASE_TASK_RAISED", claim=AdmissionStatus.GRANTED)
        return False
    status = owner.exact_lane.disposition
    if not _broker_shape(owner, "RELEASE", status):
        _unknown(owner, flow, "RELEASE_UNAVAILABLE" if status == "UNAVAILABLE" else "RELEASE_SHAPE_MISMATCH",
            claim=AdmissionStatus.GRANTED, cleanup="UNAVAILABLE" if status == "UNAVAILABLE" else None)
        return False
    _observe(owner, "CLEANUP_TERMINAL", reason, AdmissionStatus.GRANTED, status)
    if not active:
        _set(owner, "state", "CLAIM_DONE_BUILD")
        owner.exact_composition.flow_state = "GRANTED_CLEAN_RELEASE"
    return True


def _validate_active_cas_v2(store, expected, candidate):
    c = store._offer_preparing_composition_v2
    owner = c.claim_activate_owner_or_null
    if (owner is None or _reserved_edges(owner) is not expected or store._lease_bundle_cell_v2 is not expected
            or owner.state != "ACTIVATION_ENTERED_LOCAL" or c.flow_state != "CLAIM_GRANTED_BUILD"
            or owner.exact_claim_driver_task is not asyncio.current_task()
            or not _broker_shape(owner, "GRANTED", activated=True)):
        _fail()
    _validate_observation(owner)
    if owner.observation.state != "EMPTY":
        _fail()
    _validate_active_candidate(owner, candidate)


def _publish_active_states(owner):
    r = owner.exact_reserved_receipt
    p = r.exact_preparing_receipt
    _set(r.abort_bundle, "state", "RETIRED")
    _set(r.abort_bundle.invalidated_receipt, "state", "RETIRED")
    _set(p.exact_initial_ticket, "state", "CONSUMED_ACTIVE")
    _set(p.exact_offer_source, "state", "ACTIVE")
    _set(p.exact_offer_receipt, "state", "CONSUMED_ACTIVE")
    _set(owner.active_receipt_or_null, "state", "ACTIVE")
    _set(owner, "state", "ACTIVE")
    owner.exact_composition.flow_state = "ACTIVE"
    owner.exact_composition.initial_invocation_slot = "ACTIVE_HELD"


def _postpublish_edges(c):
    # Re-read each registration after the cell exchange, before releasing roots.
    _port(c.claim_activate_port)
    from .offer_composition_v2 import _validate_immutable_owner_edges_v2
    _validate_immutable_owner_edges_v2(c.exact_broker_session, c.exact_authority_capture_bridge,
                                     c.exact_arbiter, c.exact_clock_callable)
    if (c.exact_caller_port._composition is not c
            or c.exact_controller is not c.exact_arbiter.controller
            or c.exact_discussion_transaction is not c.exact_controller._discussion):
        _fail()


def _postcheck_active_v2(owner):
    """Fixed-code, allocation-free field comparison after the semantic publish."""
    c = owner.exact_composition
    _postpublish_edges(c)
    _static(owner)
    cell = owner.active_candidate_cell_or_null
    receipt = owner.active_receipt_or_null
    p = owner.exact_reserved_receipt.exact_preparing_receipt
    s = owner.exact_session
    lane = owner.exact_lane
    client_lease = owner.exact_client_lease
    invocation = owner.exact_request.invocation_id
    if (owner.exact_store._lease_bundle_cell_v2 is not cell
            or cell.stage != "ACTIVE" or cell.exact_tuple[-1] is not receipt
            or receipt.cell_identity is not cell.bundle_identity or receipt.cell_weakref() is not cell
            or receipt.exact_lease is not cell.exact_tuple[0] or receipt.exact_capture is not cell.exact_tuple[1]
            or receipt.exact_capture is not owner.exact_capture
            or owner.exact_store._lease_bundle_cells_v2.get(cell.bundle_identity) is not receipt.cell_weakref):
        return "ACTIVE_STORE"
    lease = receipt.exact_lease
    if lease.status != "ACTIVE" or lease.lease_revision != 2:
        return "ACTIVE_LEASE"
    for field in reserved._LEASE_PRESERVED_FIELDS:
        if getattr(lease, field) != getattr(owner.exact_reserved_lease, field):
            return "ACTIVE_LEASE"
    for field in _CAPTURE_FIELDS:
        if getattr(lease, field) != getattr(owner.exact_capture, field):
            return "ACTIVE_CAPTURE"
    if (c.claim_activate_owner_or_null is not owner or receipt.claim_owner_weakref() is not owner
            or receipt.claim_owner_identity is not owner.identity or c.claim_activate_port is not owner.exact_port
            or c.exact_discussion_store is not owner.exact_store or c.exact_broker_session is not s
            or receipt.exact_session is not s or receipt.exact_lane is not lane
            or receipt.exact_client_lease is not client_lease or receipt.exact_request is not owner.exact_request
            or receipt.exact_activation_context is not owner.activation_context_or_null
            or receipt.exact_deadline is not owner.exact_dispatch_deadline
            or receipt.exact_reserved_receipt is not owner.exact_reserved_receipt
            or receipt.predecessor_identity is not owner.exact_reserved_identity
            or c.lifecycle != "ACTIVE" or c.flow_state != "ACTIVE" or c.initial_invocation_slot != "ACTIVE_HELD"
            or owner.state != "ACTIVE" or receipt.state != "ACTIVE"
            or p.exact_initial_ticket.state != "CONSUMED_ACTIVE" or p.exact_offer_source.state != "ACTIVE"
            or p.exact_offer_receipt.state != "CONSUMED_ACTIVE"
            or owner.exact_reserved_receipt.abort_bundle.state != "RETIRED"
            or owner.exact_reserved_receipt.abort_bundle.invalidated_receipt.state != "RETIRED"):
        return "ACTIVE_OWNER"
    slot = receipt.postcheck_observation
    selection = owner.active_cleanup_selection
    if (slot.expected_identity is not cell.bundle_identity or slot.state != "EMPTY"
            or slot.first_mismatch_or_null is not None or slot.observed_identity_or_null is not None
            or slot.observed_stage_or_null is not None or receipt.cleanup_selection_slot is not selection
            or selection.candidates is not owner.active_cleanup_candidates_or_null
            or selection.state != "BOUND_EMPTY" or selection.selected_reason_or_null is not None
            or selection.selected_candidate_identity_or_null is not None):
        return "ACTIVE_CANDIDATE"
    if (lane.lease is not client_lease or lane.callers != 0 or lane.disposition != "GRANTED"
            or lane.abandon_disposition is not None or s._control_lanes.get(invocation) is not lane
            or client_lease._claimed is not True or client_lease._released is not False or client_lease._retired is not False
            or client_lease._abandon_disposition is not None or s._claimed_invocation != invocation
            or s._activated_invocation != invocation or not owner.activation_context_or_null._entered):
        return "ACTIVE_BROKER"
    if s._closed or s._transport_closed or s._reader_task.done() or s._writer.is_closing():
        return "ACTIVE_SESSION"
    if (s._call_ordinals.get(invocation) != 0 or type(s._call_ordinals.get(invocation)) is not int
            or type(s._request_ids.get(invocation)) is not set or s._request_ids[invocation]
            or invocation in s._results or invocation in s._acks or invocation in s._attachments
            or invocation in s._terminal_controls):
        return "ACTIVE_REGISTRY"
    for key in s._generations:
        if key[0] == invocation:
            return "ACTIVE_GENERATION"
    return None


def _record_active_postcheck(owner, code):
    slot = owner.active_receipt_or_null.postcheck_observation
    if slot.state != "EMPTY":
        _fail()
    cell = owner.exact_store._lease_bundle_cell_v2
    if _observable_cell(owner.exact_store, cell):
        _set(slot, "observed_identity_or_null", cell.bundle_identity)
        _set(slot, "observed_stage_or_null", cell.stage)
    else:
        _set(slot, "observed_stage_or_null", "FOREIGN")
    _set(slot, "first_mismatch_or_null", code)
    _set(slot, "state", "RECORDED")


async def _claim_activate_reserved_owned_v2(port):
    c, cell, receipt = _begin(port)
    if not _fresh(c, receipt):
        return _publish_reserved_invalidated_after_claim_v2(port, None)
    try:
        owner = _new_owner(c, cell, receipt)
    except ClaimActivateError as error:
        if error.code == "CLAIM_DEADLINE_EXPIRED":
            return _publish_reserved_invalidated_after_claim_v2(port, None)
        raise
    except Exception:
        return None
    if not await _create_tasks(owner):
        return None
    status, interrupted = await _wait_claim(owner)
    if status is None:
        return None
    if status is not AdmissionStatus.GRANTED:
        return _publish_reserved_invalidated_after_claim_v2(port, owner)
    c.flow_state = "CLAIM_GRANTED_BUILD"
    _set(owner, "state", "CLAIM_GRANTED_BUILD")
    if interrupted or not _fresh(c, receipt):
        if await _release_once(owner, "GRANTED_CLEAN_RELEASE"):
            return _publish_reserved_invalidated_after_claim_v2(port, owner)
        return None
    candidate = None
    try:
        _reserved_edges(owner)
        candidate = _build_active(owner)
        owner.activation_context_or_null.__enter__()
        _set(owner, "build_origin", "BUILD_ENTERED")
        _set(owner, "state", "ACTIVATION_ENTERED_LOCAL")
        owner.exact_store._cas_active_lease_v2(cell, candidate)
    except Exception:
        if candidate is not None and owner.exact_store._lease_bundle_cell_v2 is candidate:
            _set(owner.active_receipt_or_null, "state", "POSTCHECK_UNKNOWN")
            _record_active_postcheck(owner, "ACTIVE_PUBLISH")
            return _unknown(owner, "ACTIVE_POSTCHECK_UNKNOWN", "ACTIVE_POSTCHECK_MISMATCH", claim=AdmissionStatus.GRANTED)
        if await _release_once(owner, "GRANTED_CLEAN_RELEASE"):
            return _publish_reserved_invalidated_after_claim_v2(port, owner)
        return None
    _publish_active_states(owner)
    try:
        mismatch = _postcheck_active_v2(owner)
    except Exception:
        mismatch = "ACTIVE_OWNER"
    if mismatch is not None:
        _record_active_postcheck(owner, mismatch)
        _set(owner.active_receipt_or_null, "state", "POSTCHECK_UNKNOWN")
        return _unknown(owner, "ACTIVE_POSTCHECK_UNKNOWN", "ACTIVE_POSTCHECK_MISMATCH", claim=AdmissionStatus.GRANTED)
    _set(owner, "claim_task_or_null", None)
    return owner.active_receipt_or_null


def _validate_active_candidate(owner, cell, *, held=False):
    from ai_client.llm.admission_client import _LeaseActivation
    recorded = held and owner.state in ("ACTIVE_POSTCHECK_UNKNOWN", "ACTIVE_ABORT_CONFLICT_HOLD", "I3_POSTCHECK_UNKNOWN")
    if (not reserved._valid_cell_v2(owner.exact_store, cell) or cell.stage != "ACTIVE"
            or owner.active_candidate_cell_or_null is not cell):
        _fail()
    lease, capture, receipt = cell.exact_tuple
    _capture_matches(lease, capture)
    selection = owner.active_cleanup_selection
    context = owner.activation_context_or_null
    if (type(context) is not _LeaseActivation or context._session is not owner.exact_session
            or context._invocation_id != owner.exact_request.invocation_id):
        _fail()
    if (type(receipt) is not ActiveCaptureOwnerReceiptV2 or receipt is not owner.active_receipt_or_null
            or receipt.exact_lease is not lease or receipt.exact_capture is not owner.exact_capture
            or capture is not owner.exact_capture or receipt.exact_reserved_receipt is not owner.exact_reserved_receipt
            or receipt.predecessor_identity is not owner.exact_reserved_identity
            or receipt.claim_owner_identity is not owner.identity or receipt.claim_owner_weakref() is not owner
            or receipt.exact_activation_context is not owner.activation_context_or_null
            or receipt.exact_session is not owner.exact_session or receipt.exact_lane is not owner.exact_lane
            or receipt.exact_client_lease is not owner.exact_client_lease or receipt.exact_request is not owner.exact_request
            or receipt.exact_deadline is not owner.exact_dispatch_deadline or lease.status != "ACTIVE"
            or lease.lease_revision != 2 or receipt.cleanup_selection_slot is not selection
            or type(selection) is not ActiveCleanupSelectionV2
            or selection.candidates is not owner.active_cleanup_candidates_or_null
            or type(selection.candidates) is not tuple or len(selection.candidates) != 4):
        _fail()
    if (type(receipt.postcheck_observation) is not ActivePostcheckObservationV2
            or (not recorded and receipt.postcheck_observation.state != "EMPTY")
            or receipt.postcheck_observation.expected_identity is not cell.bundle_identity
            or (not recorded and (receipt.postcheck_observation.observed_identity_or_null is not None
                or receipt.postcheck_observation.observed_stage_or_null is not None
                or receipt.postcheck_observation.first_mismatch_or_null is not None))):
        _fail()
    for field in reserved._LEASE_PRESERVED_FIELDS:
        if getattr(lease, field) != getattr(owner.exact_reserved_lease, field):
            _fail()
    for reason, candidate in zip(_REASONS, selection.candidates):
        if type(candidate) is not ActiveCleanupCandidateV2 or candidate.reason != reason:
            _fail()
        invalid_cell = candidate.candidate_cell
        if not reserved._valid_cell_v2(owner.exact_store, invalid_cell) or invalid_cell.stage != "ACTIVE_INVALIDATED":
            _fail()
        invalid, invalid_capture, invalid_receipt = invalid_cell.exact_tuple
        if (type(invalid_receipt) is not ActiveInvalidatedOwnerReceiptV2
                or invalid_receipt.predecessor_identity is not cell.bundle_identity
                or invalid_receipt.exact_active_lease is not lease or invalid_receipt.exact_capture is not capture
                or invalid_receipt.claim_owner_identity is not owner.identity or invalid_receipt.reason != reason
                or invalid_receipt.invalidated_lease is not invalid or invalid_capture is not capture
                or invalid.status != "INVALIDATED" or invalid.lease_revision != 3
                or invalid_receipt.state != ("PUBLISHED" if held and selection.state == "PUBLISHED"
                    and selection.selected_candidate_identity_or_null is invalid_cell.bundle_identity
                    else "RETIRED" if held and selection.state == "PUBLISHED" else "ARMED")):
            _fail()
        for field in reserved._LEASE_PRESERVED_FIELDS:
            if getattr(invalid, field) != getattr(lease, field):
                _fail()
    if selection.state == "BOUND_EMPTY":
        if selection.selected_reason_or_null is not None or selection.selected_candidate_identity_or_null is not None:
            _fail()
    elif selection.state == "SELECTED" or (held and selection.state in ("CONFLICT_HELD", "PUBLISHED")):
        if selection.selected_reason_or_null not in _REASONS:
            _fail()
        selected = selection.candidates[_REASONS.index(selection.selected_reason_or_null)]
        if selection.selected_candidate_identity_or_null is not selected.candidate_cell.bundle_identity:
            _fail()
    else:
        _fail()


def _freeze_active_candidates(candidates):
    return tuple(candidates)


def _validate_build_origin(owner):
    from ai_client.llm.admission_client import _LeaseActivation
    origin = owner.build_origin
    context = owner.activation_context_or_null
    cell = owner.active_candidate_cell_or_null
    receipt = owner.active_receipt_or_null
    selection = owner.active_cleanup_selection
    candidates = owner.active_cleanup_candidates_or_null
    if origin == "BUILD_NOT_STARTED":
        if any(value is not None for value in (context, cell, receipt, selection, candidates)):
            _fail()
        return
    if origin not in ("BUILD_CONTEXT_ONLY", "BUILD_CELL_BOUND", "BUILD_CANDIDATES_BOUND", "BUILD_ENTERED"):
        _fail()
    if (type(context) is not _LeaseActivation or context._session is not owner.exact_session
            or context._invocation_id != owner.exact_request.invocation_id):
        _fail()
    if origin != "BUILD_ENTERED" and context._entered:
        _fail()
    if origin == "BUILD_CONTEXT_ONLY":
        if any(value is not None for value in (cell, receipt, selection, candidates)):
            _fail()
        return
    if (not reserved._valid_cell_v2(owner.exact_store, cell) or cell.stage != "ACTIVE"
            or type(receipt) is not ActiveCaptureOwnerReceiptV2 or cell.exact_tuple[-1] is not receipt
            or receipt.exact_activation_context is not context or receipt.claim_owner_weakref() is not owner
            or receipt.claim_owner_identity is not owner.identity or receipt.exact_reserved_receipt is not owner.exact_reserved_receipt
            or receipt.exact_capture is not owner.exact_capture or receipt.exact_lease is not cell.exact_tuple[0]
            or receipt.exact_session is not owner.exact_session or receipt.exact_lane is not owner.exact_lane
            or receipt.exact_client_lease is not owner.exact_client_lease or receipt.exact_request is not owner.exact_request
            or receipt.exact_deadline is not owner.exact_dispatch_deadline
            or receipt.cell_identity is not cell.bundle_identity or receipt.cell_weakref() is not cell
            or type(selection) is not ActiveCleanupSelectionV2 or receipt.cleanup_selection_slot is not selection
            or selection.candidates is not candidates):
        _fail()
    if origin == "BUILD_CELL_BOUND":
        if (candidates is not None or selection.state != "BOUND_EMPTY" or receipt.state != "ARMED"
                or selection.selected_reason_or_null is not None or selection.selected_candidate_identity_or_null is not None):
            _fail()
    elif type(candidates) is not tuple or len(candidates) != 4:
        _fail()
    if owner.state in ("ACTIVE", "CLEANUP_PENDING", "ACTIVE_CLEANUP_UNKNOWN", "ACTIVE_POSTCHECK_UNKNOWN",
                        "ACTIVE_ABORT_CONFLICT_HOLD", "I3_POSTCHECK_UNKNOWN") and origin != "BUILD_ENTERED":
        _fail()


def _build_active(owner):
    lease = replace(owner.exact_reserved_lease, status="ACTIVE", lease_revision=2)
    lease.__post_init__()
    context = owner.exact_client_lease.activate()
    _set(owner, "activation_context_or_null", context)
    _set(owner, "build_origin", "BUILD_CONTEXT_ONLY")
    identity = reserved.ActiveBundleIdentityV2(reserved._ISSUER)
    post = ActivePostcheckObservationV2(_ISSUER, expected_identity=identity,
        observed_identity_or_null=None, observed_stage_or_null=None, first_mismatch_or_null=None, state="EMPTY")
    selection = ActiveCleanupSelectionV2(_ISSUER, candidates=None, selected_reason_or_null=None,
        selected_candidate_identity_or_null=None, state="BOUND_EMPTY")
    receipt = ActiveCaptureOwnerReceiptV2(_ISSUER,
        schema_version="aiwolf.active-capture-owner-receipt.v2", predecessor_identity=owner.exact_reserved_identity,
        exact_reserved_receipt=owner.exact_reserved_receipt, exact_capture=owner.exact_capture, exact_lease=lease,
        claim_owner_identity=owner.identity, claim_owner_weakref=weakref.ref(owner), exact_activation_context=context,
        exact_session=owner.exact_session, exact_lane=owner.exact_lane, exact_client_lease=owner.exact_client_lease,
        exact_request=owner.exact_request, exact_deadline=owner.exact_dispatch_deadline,
        cell_identity=None, cell_weakref=None, postcheck_observation=post, cleanup_selection_slot=selection,
        state="ARMED")
    cell = reserved._issue_bundle_cell_v2(owner.exact_store, "ACTIVE", identity, (lease, owner.exact_capture, receipt))
    _set(owner, "active_candidate_cell_or_null", cell)
    _set(owner, "active_receipt_or_null", receipt)
    _set(owner, "active_cleanup_selection", selection)
    _set(owner, "build_origin", "BUILD_CELL_BOUND")
    candidates = []
    invalid_cell = None
    try:
        for reason in _REASONS:
            invalid = replace(lease, status="INVALIDATED", lease_revision=3)
            invalid_receipt = ActiveInvalidatedOwnerReceiptV2(_ISSUER,
                schema_version="aiwolf.active-invalidated-owner-receipt.v2", predecessor_identity=identity,
                exact_active_lease=lease, exact_capture=owner.exact_capture, claim_owner_identity=owner.identity,
                invalidated_lease=invalid, reason=reason, cell_identity=None, cell_weakref=None, state="ARMED")
            invalid_cell = reserved._issue_bundle_cell_v2(owner.exact_store, "ACTIVE_INVALIDATED",
                reserved.ActiveInvalidatedBundleIdentityV2(reserved._ISSUER), (invalid, owner.exact_capture, invalid_receipt))
            candidates.append(ActiveCleanupCandidateV2(_ISSUER, reason=reason, candidate_cell=invalid_cell))
        candidates = _freeze_active_candidates(candidates)
    except BaseException:
        # Tracebacks can retain local prefix cells; remove their temporary weak
        # registry entries before any UNKNOWN publication.
        for item in candidates:
            owner.exact_store._lease_bundle_cells_v2.pop(item.candidate_cell.bundle_identity, None)
        if invalid_cell is not None:
            owner.exact_store._lease_bundle_cells_v2.pop(invalid_cell.bundle_identity, None)
        raise
    _set(selection, "candidates", candidates)
    _set(owner, "active_cleanup_candidates_or_null", candidates)
    _set(owner, "build_origin", "BUILD_CANDIDATES_BOUND")
    _validate_active_candidate(owner, cell)
    return cell


def _validate_i2_entry(owner):
    _reserved_edges(owner)
    _validate_observation(owner)
    o = owner.observation
    task = owner.claim_task_or_null
    if (owner.state != "CLAIM_DONE_BUILD" or o.observed_store_identity_or_null is not owner.exact_reserved_identity
            or task is None or not task.done() or any(getattr(owner, name) is not None for name in (
            "world_watcher_task_or_null", "cancel_watcher_task_or_null", "deadline_watcher_task_or_null"))):
        _fail()
    if o.state == "CLAIM_TERMINAL":
        cancelled = o.failure_code_or_null != "CLAIM_RETURNED_NON_GRANT"
        if (owner.exact_composition.flow_state != o.failure_code_or_null + "_TERMINAL"
                or task.cancelled() is not cancelled
                or (not cancelled and (task.exception() is not None or task.result() is not o.claim_status_or_null))
                or owner.activation_context_or_null is not None or owner.active_candidate_cell_or_null is not None
                or owner.cleanup_task_or_null is not None
                or not _broker_shape(owner, "TERMINAL", o.claim_status_or_null,
                    abandoned=o.failure_code_or_null == "CLAIM_CANCELLED_ABANDONED")):
            _fail()
    elif o.state == "CLEANUP_TERMINAL":
        release = owner.cleanup_task_or_null
        if (owner.exact_composition.flow_state != "GRANTED_CLEAN_RELEASE"
                or o.failure_code_or_null != "GRANTED_CLEAN_RELEASE"
                or task.cancelled() or task.exception() is not None or task.result() is not AdmissionStatus.GRANTED
                or release is None or not release.done() or release.cancelled() or release.exception() is not None
                or (owner.activation_context_or_null is not None and owner.activation_context_or_null._entered)
                or not _broker_shape(owner, "RELEASE", o.cleanup_status_or_null)):
            _fail()
    else:
        _fail()


def _validate_invalidated_cas_v2(store, expected, candidate, owner):
    if (not reserved._valid_cell_v2(store, expected) or not reserved._valid_cell_v2(store, candidate)
            or store._lease_bundle_cell_v2 is not expected):
        _fail()
    if expected.stage == "RESERVED":
        receipt = expected.exact_tuple[-1]
        if owner is None:
            reserved._validate_published_row_v2(store._offer_preparing_composition_v2)
        else:
            _validate_i2_entry(owner)
        reserved._validate_abort_candidate_v2(store, expected, receipt, receipt.abort_bundle)
        if candidate is not receipt.abort_bundle.candidate_cell:
            _fail()
        return
    if expected.stage != "ACTIVE" or owner is None:
        _fail()
    _static(owner)
    _validate_active_candidate(owner, expected)
    _validate_observation(owner)
    selection = owner.active_cleanup_selection
    c = owner.exact_composition
    p = owner.exact_reserved_receipt.exact_preparing_receipt
    if (owner.observation.state != "CLEANUP_TERMINAL" or selection.state != "SELECTED"
            or owner.observation.observed_store_identity_or_null is not expected.bundle_identity
            or c.lifecycle != "ACTIVE" or c.flow_state != "ACTIVE_CLEANUP_PENDING" or c.initial_invocation_slot != "ACTIVE_HELD"
            or p.exact_initial_ticket.state != "CONSUMED_ACTIVE" or p.exact_offer_source.state != "ACTIVE"
            or p.exact_offer_receipt.state != "CONSUMED_ACTIVE"
            or owner.claim_task_or_null is not None
            or any(getattr(owner, name) is not None for name in (
                "world_watcher_task_or_null", "cancel_watcher_task_or_null", "deadline_watcher_task_or_null"))
            or selection.selected_candidate_identity_or_null is not candidate.bundle_identity
            or selection.selected_reason_or_null != owner.observation.failure_code_or_null
            or owner.state != "CLEANUP_PENDING" or owner.active_receipt_or_null.state != "CLEANUP_PENDING"
            or owner.activation_context_or_null._entered
            or not _broker_shape(owner, "RELEASE", owner.observation.cleanup_status_or_null)
            or owner.cleanup_task_or_null is None or not owner.cleanup_task_or_null.done()
            or owner.cleanup_task_or_null.cancelled() or owner.cleanup_task_or_null.exception() is not None):
        _fail()


def _clear_owner(owner):
    for name in ("claim_task_or_null", "world_watcher_task_or_null", "cancel_watcher_task_or_null",
                 "deadline_watcher_task_or_null", "activation_context_or_null", "active_candidate_cell_or_null",
                 "active_receipt_or_null", "active_cleanup_candidates_or_null", "active_cleanup_selection",
                 "cleanup_task_or_null", "observation"):
        _set(owner, name, None)
    _set(owner, "state", "RETIRED")
    owner.exact_composition.claim_activate_owner_or_null = None


def _retire_components(c, p, flow):
    _set(p.exact_initial_ticket, "state", "RETIRED_HELD")
    _set(p.exact_offer_source, "state", "RETIRED_HELD")
    _set(p.exact_offer_receipt, "state", "RETIRED_HELD")
    c.lifecycle = "RETIRED"
    c.flow_state = flow
    c.initial_invocation_slot = "INVALIDATED_HELD"


def _publish_reserved_invalidated_after_claim_v2(port, owner):
    c = _port(port)
    if owner is None:
        _, expected, r = _begin(port)
        outcome = "PRECLAIM_FRESHNESS"
    else:
        if c.claim_activate_owner_or_null is not owner:
            _fail()
        r = owner.exact_reserved_receipt
        expected = owner.exact_reserved_cell_weakref()
        outcome = ("GRANTED_CLEAN_RELEASE" if owner.observation.state == "CLEANUP_TERMINAL"
                   else owner.observation.failure_code_or_null)
    abort = r.abort_bundle
    p = r.exact_preparing_receipt
    try:
        success = c.exact_discussion_store._cas_claim_invalidated_v2(expected, abort.candidate_cell, owner)
    except Exception:
        if c.exact_discussion_store._lease_bundle_cell_v2 is abort.candidate_cell:
            _hold_i2_postcheck(c, r, owner, outcome)
            return None
        success = False
    if not success:
        if owner is not None:
            _set(owner, "state", "CLAIM_ABORT_CONFLICT_HOLD")
        else:
            c.claim_activate_preclaim_hold_or_null = r
            c.claim_activate_outcome_or_null = "PRECLAIM_CONFLICT"
        try:
            reserved._record_conflict(p, abort, c.exact_discussion_store._lease_bundle_cell_v2)
        except (AttributeError, TypeError, KeyError, ValueError):
            pass  # Keep the original graph; a malformed observation is not repaired.
        c.flow_state = "CLAIM_ABORT_CONFLICT_HOLD"
        return None
    _set(abort, "state", "PUBLISHED")
    _set(abort.invalidated_receipt, "state", "PUBLISHED")
    _set(r, "state", "RETIRED")
    _retire_components(c, p, "FINALIZE_RETIRED_HELD")
    c.claim_activate_outcome_or_null = outcome
    # Validate before dropping the sole owner graph. Unknown retains it intact.
    try:
        if owner is None:
            if c.claim_activate_owner_or_null is not None:
                _fail()
        else:
            _static(owner)
        _validate_i2_post(c, r, outcome)
    except Exception:
        _hold_i2_postcheck(c, r, owner, outcome)
        return None
    if owner is not None:
        _clear_owner(owner)
    return abort.invalidated_receipt


def _hold_i2_postcheck(c, r, owner, outcome):
    c.flow_state = "I2_POSTCHECK_UNKNOWN"
    c.claim_activate_outcome_or_null = outcome
    if owner is None:
        c.claim_activate_preclaim_hold_or_null = r
    else:
        _set(owner, "state", "I2_POSTCHECK_UNKNOWN")
    if (type(r.postcheck_observation) is reserved.FinalizePostcheckObservationV2
            and r.postcheck_observation.state == "EMPTY"):
        _record_i2_postcheck(c, r)


def _record_i2_postcheck(c, r):
    slot = r.postcheck_observation
    if slot.state != "EMPTY":
        _fail()
    p = r.exact_preparing_receipt
    cell = c.exact_discussion_store._lease_bundle_cell_v2
    valid = _observable_cell(c.exact_discussion_store, cell)
    if valid:
        _set(slot, "observed_bundle_identity_or_null", cell.bundle_identity)
        _set(slot, "observed_cell_weakref_or_null", c.exact_discussion_store._lease_bundle_cells_v2[cell.bundle_identity])
    _set(slot, "observed_shape_or_null", cell.stage if valid else "FOREIGN")
    _set(slot, "observed_lease", cell.exact_tuple[0] if valid else None)
    _set(slot, "observed_capture", cell.exact_tuple[1] if valid else None)
    _set(slot, "observed_receipt", cell.exact_tuple[-1] if valid else None)
    _set(slot, "observed_composition", c.exact_discussion_store._offer_preparing_composition_v2)
    _set(slot, "observed_ticket", c.active_initial_ticket_or_null)
    _set(slot, "observed_source", c.active_offer_source_or_null)
    _set(slot, "observed_offer_receipt", p.exact_offer_source.exact_offer_receipt_or_null)
    _set(slot, "observed_pending", c.exact_arbiter._active)
    _set(slot, "observed_driver", c.exact_arbiter._admission_driver)
    _set(slot, "observed_session", c.exact_broker_session)
    _set(slot, "observed_lane", p.exact_session._control_lanes.get(p.exact_request.invocation_id))
    _set(slot, "observed_client_lease", p.exact_control_lane.lease)
    _set(slot, "observed_composition_state", c.flow_state)
    _set(slot, "observed_slot_state", c.initial_invocation_slot)
    _set(slot, "observed_ticket_state", p.exact_initial_ticket.state)
    _set(slot, "observed_source_state", p.exact_offer_source.state)
    _set(slot, "observed_offer_receipt_state", p.exact_offer_receipt.state)
    _set(slot, "first_mismatch_or_null", "I2_ROW")
    _set(slot, "state", "RECORDED")


def _validate_preclaim_hold(c):
    r = c.claim_activate_preclaim_hold_or_null
    if (type(r) is not reserved.ReservedCaptureOwnerReceiptV2
            or c.claim_activate_owner_or_null is not None or c.claim_activate_prepublication_hold_or_null is not None
            or c.claim_activate_retire_task_or_null is not None
            or c.flow_state not in ("CLAIM_ABORT_CONFLICT_HOLD", "I2_POSTCHECK_UNKNOWN")):
        _fail()
    p = r.exact_preparing_receipt
    abort = r.abort_bundle
    post = r.postcheck_observation
    if (p.exact_composition is not c or p.exact_store is not c.exact_discussion_store
            or p.exact_session is not c.exact_broker_session or c.active_initial_ticket_or_null is not p.exact_initial_ticket
            or c.active_offer_source_or_null is not p.exact_offer_source
            or p.exact_offer_source.exact_initial_ticket is not p.exact_initial_ticket
            or p.exact_offer_source.exact_offer_receipt_or_null is not p.exact_offer_receipt
            or p.exact_offer_receipt.exact_session is not p.exact_session
            or p.exact_offer_receipt.exact_control_lane is not p.exact_control_lane
            or p.exact_offer_receipt.exact_client_lease is not p.exact_client_lease
            or p.exact_offer_receipt.exact_request is not p.exact_request
            or p.exact_offer_receipt.exact_offer_source is not p.exact_offer_source
            or c.initial_pending_or_null is not p.exact_pending
            or p.exact_pending.dispatch_deadline is not p.exact_dispatch_deadline
            or post.expected_receipt is not r or post.expected_lease is not r.exact_lease
            or post.expected_capture is not r.exact_capture or post.expected_composition is not c
            or post.expected_bundle_identity is not r.cell_identity
            or post.expected_cell_weakref is not r.cell_weakref or r.cell_identity is not r.owner_identity
            or abort.predecessor_identity is not r.cell_identity or abort.exact_capture is not r.exact_capture
            or abort.invalidated_receipt.exact_reserved_lease is not r.exact_lease
            or abort.postcheck_observation is not post or abort.invalidated_receipt.postcheck_observation is not post
            or c.exact_arbiter._active is not None or c.exact_arbiter._admission_driver is not None
            or not _held_shape(p)):
        _fail()
    _capture_matches(r.exact_lease, r.exact_capture)
    for field in reserved._LEASE_PRESERVED_FIELDS:
        if getattr(abort.invalidated_lease, field) != getattr(r.exact_lease, field):
            _fail()
    if c.flow_state == "CLAIM_ABORT_CONFLICT_HOLD":
        conflict = abort.conflict_observation
        if (r.state != "RESERVED" or abort.state != "CONFLICT_HELD"
                or abort.invalidated_receipt.state != "CONFLICT_HELD"
                or conflict.state != "RECORDED" or conflict.exact_composition is not c
                or conflict.expected_bundle_identity is not r.cell_identity
                or conflict.expected_cell_weakref is not r.cell_weakref
                or conflict.expected_stage != "RESERVED" or conflict.purpose != "RESERVED_ABORT"
                or c.claim_activate_outcome_or_null != "PRECLAIM_CONFLICT"):
            _fail()
        current = c.exact_discussion_store._lease_bundle_cell_v2
        if _observable_cell(c.exact_discussion_store, current):
            if (conflict.observed_bundle_identity_or_null is not current.bundle_identity
                    or conflict.observed_cell_weakref_or_null() is not current or conflict.observed_shape_or_null != current.stage):
                _fail()
        elif conflict.observed_bundle_identity_or_null is not None or conflict.observed_shape_or_null not in ("FOREIGN", "null"):
            _fail()
    else:
        if (r.state != "RETIRED" or post.state != "RECORDED" or post.first_mismatch_or_null != "I2_ROW"
                or c.claim_activate_outcome_or_null != "PRECLAIM_FRESHNESS"
                or c.exact_discussion_store._lease_bundle_cell_v2 is not abort.candidate_cell
                or post.observed_bundle_identity_or_null is not abort.candidate_cell.bundle_identity
                or post.observed_cell_weakref_or_null() is not abort.candidate_cell):
            _fail()
    return c.flow_state


def _validate_retired_components(c, p):
    if (c.lifecycle != "RETIRED" or c.initial_invocation_slot != "INVALIDATED_HELD"
            or c.active_initial_ticket_or_null is not p.exact_initial_ticket
            or c.active_offer_source_or_null is not p.exact_offer_source
            or p.exact_offer_source.exact_offer_receipt_or_null is not p.exact_offer_receipt
            or any(component.state != "RETIRED_HELD" for component in (
                p.exact_initial_ticket, p.exact_offer_source, p.exact_offer_receipt))):
        _fail()


def _validate_i2_post(c, r, outcome):
    _postpublish_edges(c)
    store = c.exact_discussion_store
    abort = r.abort_bundle
    p = r.exact_preparing_receipt
    _validate_retired_components(c, p)
    if (store._lease_bundle_cell_v2 is not abort.candidate_cell
            or not reserved._valid_cell_v2(store, abort.candidate_cell)
            or abort.state != "PUBLISHED" or abort.invalidated_receipt.state != "PUBLISHED"
            or r.state != "RETIRED" or c.flow_state != "FINALIZE_RETIRED_HELD"
            or c.exact_arbiter._active is not None or c.exact_arbiter._admission_driver is not None
            or p.exact_pending.result.result() is not p.exact_initial_ticket.terminal_candidates.preparing_complete):
        _fail()
    invalid = abort.invalidated_receipt
    if (abort.candidate_cell.exact_tuple[0] is not abort.invalidated_lease
            or abort.candidate_cell.exact_tuple[-1] is not invalid
            or invalid.invalidated_lease is not abort.invalidated_lease
            or invalid.exact_capture is not r.exact_capture or invalid.exact_reserved_lease is not r.exact_lease
            or invalid.predecessor_identity is not r.cell_identity or abort.predecessor_identity is not r.cell_identity
            or p.abort_bundle.state != "RETIRED" or p.abort_bundle.invalidated_receipt.state != "RETIRED"
            or p.abort_bundle.abort_selection.state != "RETIRED_UNSELECTED"):
        _fail()
    for field in reserved._LEASE_PRESERVED_FIELDS:
        if getattr(abort.invalidated_lease, field) != getattr(r.exact_lease, field):
            _fail()
    for field in ("exact_composition", "exact_store", "exact_initial_ticket", "exact_pending", "exact_driver_task",
                  "exact_offer_source", "exact_offer_receipt", "exact_session", "exact_control_lane",
                  "exact_client_lease", "exact_request", "exact_dispatch_deadline"):
        if getattr(invalid, field) is not getattr(p, field):
            _fail()
    _capture_matches(abort.invalidated_lease, r.exact_capture)
    if outcome == "PRECLAIM_FRESHNESS":
        if not _held_shape(p):
            _fail()
    elif outcome == "GRANTED_CLEAN_RELEASE":
        if not _broker_shape(p, "RELEASE", p.exact_control_lane.disposition):
            _fail()
    elif outcome in ("CLAIM_RETURNED_NON_GRANT", "CLAIM_CANCELLED_NON_GRANT", "CLAIM_CANCELLED_ABANDONED"):
        status = next((value for value in _TERMINAL if value.value == p.exact_control_lane.disposition), None)
        if not _broker_shape(p, "TERMINAL", status, abandoned=outcome == "CLAIM_CANCELLED_ABANDONED"):
            _fail()
    else:
        _fail()


def _validate_unknown_refs(owner):
    from ai_client.llm.admission_client import _LeaseActivation
    state = owner.state
    code = owner.observation.failure_code_or_null
    active = state in ("ACTIVE_POSTCHECK_UNKNOWN", "ACTIVE_CLEANUP_UNKNOWN",
                       "ACTIVE_ABORT_CONFLICT_HOLD", "I3_POSTCHECK_UNKNOWN")
    watchers = (owner.world_watcher_task_or_null, owner.cancel_watcher_task_or_null,
                owner.deadline_watcher_task_or_null)
    preserve_watchers = state == "CLAIM_RESULT_UNKNOWN" and code in (
        "CLAIM_SETTLE_TIMEOUT", "WATCHER_CLEANUP_FAILED")
    for task in watchers:
        if preserve_watchers:
            if not isinstance(task, asyncio.Task) or task.get_loop() is not asyncio.get_running_loop():
                _fail()
            if code == "WATCHER_CLEANUP_FAILED" and not task.done():
                _fail()
        elif task is not None:
            _fail()
    task = owner.claim_task_or_null
    if task is not None and (not isinstance(task, asyncio.Task)
            or task.get_loop() is not asyncio.get_running_loop()):
        _fail()
    if state in ("ACTIVE_CLEANUP_UNKNOWN", "ACTIVE_ABORT_CONFLICT_HOLD", "I3_POSTCHECK_UNKNOWN"):
        if task is not None:
            _fail()
    elif state == "ACTIVE_POSTCHECK_UNKNOWN":
        if task is not None and (not task.done() or task.cancelled() or task.exception() is not None
                or task.result() is not AdmissionStatus.GRANTED):
            _fail()
    elif task is None:
        _fail()
    if state in ("CLAIM_ABORT_CONFLICT_HOLD", "I2_POSTCHECK_UNKNOWN"):
        if not task.done():
            _fail()
        if owner.observation.state == "CLAIM_TERMINAL":
            cancelled = code != "CLAIM_RETURNED_NON_GRANT"
            if task.cancelled() is not cancelled or (not cancelled and (
                    task.exception() is not None or task.result() is not owner.observation.claim_status_or_null)):
                _fail()
        elif task.cancelled() or task.exception() is not None or task.result() is not AdmissionStatus.GRANTED:
            _fail()
    if preserve_watchers and code == "WATCHER_CLEANUP_FAILED" and not any(
            not item.cancelled() and item.exception() is not None for item in watchers):
        _fail()
    context = owner.activation_context_or_null
    cell = owner.active_candidate_cell_or_null
    receipt = owner.active_receipt_or_null
    selection = owner.active_cleanup_selection
    candidates = owner.active_cleanup_candidates_or_null
    if context is not None:
        if (type(context) is not _LeaseActivation or context._session is not owner.exact_session
                or context._invocation_id != owner.exact_request.invocation_id
                or context._entered is not (state == "ACTIVE_POSTCHECK_UNKNOWN")):
            _fail()
    elif active or cell is not None or receipt is not None or selection is not None or candidates is not None:
        _fail()
    if active:
        _validate_active_candidate(owner, cell, held=True)
        expected_selection = ("BOUND_EMPTY" if state == "ACTIVE_POSTCHECK_UNKNOWN" else
            "CONFLICT_HELD" if state == "ACTIVE_ABORT_CONFLICT_HOLD" else
            "PUBLISHED" if state == "I3_POSTCHECK_UNKNOWN" and code != "I3_PUBLISH" else "SELECTED")
        if state == "I3_POSTCHECK_UNKNOWN":
            expected_selection = ("SELECTED" if receipt.postcheck_observation.first_mismatch_or_null == "I3_PUBLISH"
                                  else "PUBLISHED")
        expected_receipt = ("POSTCHECK_UNKNOWN" if state == "ACTIVE_POSTCHECK_UNKNOWN" else
                            "RETIRED" if expected_selection == "PUBLISHED" else "CLEANUP_PENDING")
        if selection.state != expected_selection or receipt.state != expected_receipt:
            _fail()
    elif cell is None:
        if receipt is not None or selection is not None or candidates is not None:
            _fail()
    else:
        # Build failure may retain an incomplete candidate set, but never foreign edges.
        if (not reserved._valid_cell_v2(owner.exact_store, cell) or cell.stage != "ACTIVE"
                or cell.exact_tuple[-1] is not receipt or receipt.exact_activation_context is not context
                or receipt.claim_owner_weakref() is not owner or receipt.claim_owner_identity is not owner.identity
                or receipt.cleanup_selection_slot is not selection or receipt.state != "ARMED"
                or type(selection) is not ActiveCleanupSelectionV2 or selection.state != "BOUND_EMPTY"
                or selection.selected_reason_or_null is not None or selection.selected_candidate_identity_or_null is not None
                or selection.candidates is not candidates):
            _fail()
        if candidates is not None:
            _validate_active_candidate(owner, cell, held=True)
    cleanup = owner.cleanup_task_or_null
    if cleanup is not None and (not isinstance(cleanup, asyncio.Task)
            or cleanup.get_loop() is not asyncio.get_running_loop()):
        _fail()
    if state in ("CLAIM_RESULT_UNKNOWN", "ACTIVE_POSTCHECK_UNKNOWN"):
        if cleanup is not None:
            _fail()
    elif state in ("CLAIM_CLEANUP_UNKNOWN", "ACTIVE_CLEANUP_UNKNOWN"):
        if (code == "RELEASE_START_RAISED") is not (cleanup is None):
            _fail()
        if code == "RELEASE_TIMEOUT" and cleanup.done():
            _fail()
        if code == "RELEASE_TASK_CANCELLED" and (not cleanup.done() or not cleanup.cancelled()):
            _fail()
        if code in ("RELEASE_UNAVAILABLE", "RELEASE_SHAPE_MISMATCH") and (
                not cleanup.done() or cleanup.cancelled() or cleanup.exception() is not None):
            _fail()
        if code == "RELEASE_TASK_RAISED" and (
                not cleanup.done() or cleanup.cancelled() or cleanup.exception() is None):
            _fail()
    elif owner.observation.state == "CLEANUP_TERMINAL":
        if cleanup is None or not cleanup.done() or cleanup.cancelled() or cleanup.exception() is not None:
            _fail()
    elif cleanup is not None or context is not None or cell is not None:
        _fail()


def _validate_claim_row_v2(c):
    try:
        return _validate_claim_row_contents_v2(c)
    except (AttributeError, TypeError, KeyError, ValueError):
        raise ClaimActivateError("CLAIM_ROW_MISMATCH") from None


def _validate_claim_row_contents_v2(c):
    _port(c.claim_activate_port)
    if c.claim_activate_preclaim_hold_or_null is not None:
        return _validate_preclaim_hold(c)
    if c.claim_activate_owner_or_null is None and c.flow_state in ("CLAIM_ABORT_CONFLICT_HOLD", "I2_POSTCHECK_UNKNOWN"):
        _fail()
    owner = c.claim_activate_owner_or_null
    cell = c.exact_discussion_store._lease_bundle_cell_v2
    if owner is None:
        if (c.flow_state == "RESERVED" and c.claim_activate_outcome_or_null is None
                and c.claim_activate_prepublication_hold_or_null is None):
            _begin(c.claim_activate_port)
            return "RESERVED_IDLE"
        hold = c.claim_activate_prepublication_hold_or_null
        if hold is not None:
            if (type(hold) is not PrepublicationTaskHoldV2 or type(hold.exact_gate) is not asyncio.Event or hold.exact_gate.is_set()
                    or hold.failure_code != "PREPUBLICATION_TASK_UNKNOWN"
                    or c.flow_state != "CLAIM_PREPUBLICATION_UNKNOWN"
                    or not reserved._valid_cell_v2(c.exact_discussion_store, cell) or cell.stage != "RESERVED"
                    or c.claim_activate_outcome_or_null is not None
                    or not _held_shape(cell.exact_tuple[-1].exact_preparing_receipt)):
                _fail()
            tasks = _prepublication_projection(hold)
            seen_null = False
            count = 0
            for task in tasks:
                if task is None:
                    seen_null = True
                else:
                    if seen_null or not isinstance(task, asyncio.Task) or task.get_loop() is not asyncio.get_running_loop():
                        _fail()
                    coroutine = task.get_coro()
                    expected_code = _gated_claim.__code__ if count == 0 else _gated_watch.__code__
                    if getattr(coroutine, "cr_code", None) is not expected_code:
                        _fail()
                    if any(task is earlier for earlier in tasks[:count]):
                        _fail()
                    frame = coroutine.cr_frame
                    if frame is not None:
                        local_owner = frame.f_locals.get("owner")
                        if (type(local_owner) is not ClaimActivateOwnerV2
                                or local_owner.exact_composition is not c or local_owner.claim_start_gate is not hold.exact_gate
                                or (count > 0 and frame.f_locals.get("kind") != ("world", "cancel", "deadline")[count - 1])):
                            _fail()
                    count += 1
            if count == 0:
                _fail()
            if hold.exact_tasks_or_null is not None and (count != 4
                    or type(hold.exact_tasks_or_null) is not tuple or len(hold.exact_tasks_or_null) != 4
                    or any(a is not b for a, b in zip(tasks, hold.exact_tasks_or_null))):
                _fail()
            return "PREPUBLICATION_TASK_UNKNOWN"
        if c.claim_activate_outcome_or_null in _REASONS:
            _validate_i3_post(c, cell)
            return "ACTIVE_INVALIDATED"
        if c.claim_activate_outcome_or_null is not None and cell.stage == "RESERVED_INVALIDATED":
            r = cell.exact_tuple[-1].postcheck_observation.expected_receipt
            _validate_i2_post(c, r, c.claim_activate_outcome_or_null)
            return "INVALIDATED"
        _fail()
    _static(owner)
    _validate_observation(owner)
    if (c.claim_activate_prepublication_hold_or_null is not None
            or type(owner.identity) is not ClaimOwnerIdentityV2
            or type(owner.claim_start_gate) is not asyncio.Event or not owner.claim_start_gate.is_set()
            or not isinstance(owner.exact_claim_driver_task, asyncio.Task)
            or owner.exact_claim_driver_task.get_loop() is not asyncio.get_running_loop()
            or owner.hard_deadline_monotonic != min(owner.exact_dispatch_deadline.not_after_monotonic,
                owner.exact_reserved_lease.expires_at_monotonic_us / 1_000_000)
            or owner.hard_deadline_monotonic != owner.settlement_deadline_monotonic
            or owner.claim_wait_deadline_monotonic != owner.hard_deadline_monotonic - owner.exact_session._config.cancellation_grace_seconds):
        _fail()
    if owner.state == "ACTIVE":
        _validate_active_candidate(owner, cell)
        p = owner.exact_reserved_receipt.exact_preparing_receipt
        if (owner.active_receipt_or_null.state != "ACTIVE" or c.flow_state != "ACTIVE"
                or c.lifecycle != "ACTIVE" or c.initial_invocation_slot != "ACTIVE_HELD"
                or not _broker_shape(owner, "GRANTED", activated=True)
                or p.exact_initial_ticket.state != "CONSUMED_ACTIVE" or p.exact_offer_source.state != "ACTIVE"
                or p.exact_offer_receipt.state != "CONSUMED_ACTIVE"
                or owner.exact_reserved_receipt.abort_bundle.state != "RETIRED"
                or owner.exact_reserved_receipt.abort_bundle.invalidated_receipt.state != "RETIRED"
                or not owner.activation_context_or_null._entered or owner.observation.state != "EMPTY"
                or owner.cleanup_task_or_null is not None
                or owner.active_cleanup_selection.state != "BOUND_EMPTY"
                or any(getattr(owner, field) is not None for field in (
                    "world_watcher_task_or_null", "cancel_watcher_task_or_null", "deadline_watcher_task_or_null"))
                or (owner.claim_task_or_null is not None and (not owner.claim_task_or_null.done()
                    or owner.claim_task_or_null.cancelled() or owner.claim_task_or_null.exception() is not None
                    or owner.claim_task_or_null.result() is not AdmissionStatus.GRANTED))):
            _fail()
        return "ACTIVE"
    if owner.state == "CLAIM_WAIT":
        _reserved_edges(owner)
        if (c.flow_state != "CLAIM_WAIT" or owner.observation.state != "EMPTY"
                or owner.claim_task_or_null is None or not owner.claim_start_gate.is_set()
                or owner.activation_context_or_null is not None or owner.active_candidate_cell_or_null is not None
                or owner.cleanup_task_or_null is not None
                or any(not isinstance(getattr(owner, field), asyncio.Task) for field in (
                    "claim_task_or_null", "world_watcher_task_or_null", "cancel_watcher_task_or_null", "deadline_watcher_task_or_null"))
                or owner.exact_lane.callers not in (0, 1)
                or owner.exact_client_lease._session is not owner.exact_session
                or owner.exact_client_lease._lane is not owner.exact_lane):
            _fail()
        return "CLAIM_WAIT"
    if owner.state == "CLAIM_DONE_BUILD":
        _validate_i2_entry(owner)
        return c.flow_state
    if owner.state == "CLAIM_GRANTED_BUILD":
        _reserved_edges(owner)
        task = owner.claim_task_or_null
        if (c.flow_state != "CLAIM_GRANTED_BUILD" or task is None or not task.done() or task.cancelled()
                or task.exception() is not None or task.result() is not AdmissionStatus.GRANTED
                or not _broker_shape(owner, "GRANTED") or owner.observation.state != "EMPTY"
                or any(getattr(owner, field) is not None for field in (
                    "world_watcher_task_or_null", "cancel_watcher_task_or_null", "deadline_watcher_task_or_null"))
                or owner.cleanup_task_or_null is not None):
            _fail()
        if owner.active_candidate_cell_or_null is not None:
            _validate_active_candidate(owner, owner.active_candidate_cell_or_null)
        elif any(getattr(owner, field) is not None for field in (
                "activation_context_or_null", "active_receipt_or_null", "active_cleanup_candidates_or_null", "active_cleanup_selection")):
            _fail()
        return "CLAIM_GRANTED_BUILD"
    if owner.state == "ACTIVATION_ENTERED_LOCAL":
        _validate_active_cas_v2(owner.exact_store, owner.exact_reserved_cell_weakref(), owner.active_candidate_cell_or_null)
        return "ACTIVATION_ENTERED_LOCAL"
    if owner.state == "CLEANUP_PENDING":
        _validate_active_candidate(owner, cell)
        if (c.flow_state != "ACTIVE_CLEANUP_PENDING" or owner.active_receipt_or_null.state != "CLEANUP_PENDING"
                or owner.activation_context_or_null._entered or owner.active_cleanup_selection.state != "SELECTED"
                or owner.claim_task_or_null is not None or owner.cleanup_task_or_null is None
                or owner.observation.state not in ("EMPTY", "CLEANUP_TERMINAL")
                or any(getattr(owner, field) is not None for field in (
                    "world_watcher_task_or_null", "cancel_watcher_task_or_null", "deadline_watcher_task_or_null"))):
            _fail()
        return "ACTIVE_CLEANUP_PENDING"
    if owner.state in ("CLAIM_RESULT_UNKNOWN", "CLAIM_CLEANUP_UNKNOWN", "ACTIVE_CLEANUP_UNKNOWN",
                       "ACTIVE_POSTCHECK_UNKNOWN", "ACTIVE_ABORT_CONFLICT_HOLD", "I3_POSTCHECK_UNKNOWN",
                       "CLAIM_ABORT_CONFLICT_HOLD", "I2_POSTCHECK_UNKNOWN"):
        _validate_unknown_refs(owner)
        if c.flow_state != owner.state:
            _fail()
        expected_observation = {
            "CLAIM_RESULT_UNKNOWN": "CLAIM_UNKNOWN", "CLAIM_CLEANUP_UNKNOWN": "CLEANUP_UNKNOWN",
            "ACTIVE_CLEANUP_UNKNOWN": "CLEANUP_UNKNOWN", "ACTIVE_POSTCHECK_UNKNOWN": "POSTCHECK_UNKNOWN",
            "ACTIVE_ABORT_CONFLICT_HOLD": "CLEANUP_TERMINAL", "I3_POSTCHECK_UNKNOWN": "CLEANUP_TERMINAL",
        }.get(owner.state)
        if expected_observation is not None and owner.observation.state != expected_observation:
            _fail()
        if owner.state in ("CLAIM_RESULT_UNKNOWN", "CLAIM_CLEANUP_UNKNOWN"):
            if (cell is not owner.exact_reserved_cell_weakref() or not reserved._valid_cell_v2(owner.exact_store, cell)
                    or cell.stage != "RESERVED" or owner.claim_task_or_null is None):
                _fail()
        if owner.state == "CLAIM_RESULT_UNKNOWN":
            if (owner.activation_context_or_null is not None or owner.active_candidate_cell_or_null is not None
                    or owner.active_receipt_or_null is not None or owner.active_cleanup_candidates_or_null is not None
                    or owner.active_cleanup_selection is not None or owner.cleanup_task_or_null is not None):
                _fail()
            code = owner.observation.failure_code_or_null
            task = owner.claim_task_or_null
            if code == "CLAIM_TASK_RAISED" and (not task.done() or task.cancelled() or task.exception() is None):
                _fail()
            if code == "CLAIM_SHAPE_MISMATCH" and not task.done():
                _fail()
        if owner.state in ("ACTIVE_POSTCHECK_UNKNOWN", "ACTIVE_CLEANUP_UNKNOWN"):
            if cell is not owner.active_candidate_cell_or_null or cell.stage != "ACTIVE":
                _fail()
        if owner.state == "CLAIM_CLEANUP_UNKNOWN":
            task = owner.claim_task_or_null
            if not task.done() or task.cancelled() or task.exception() is not None or task.result() is not AdmissionStatus.GRANTED:
                _fail()
        if owner.state in ("CLAIM_ABORT_CONFLICT_HOLD", "I2_POSTCHECK_UNKNOWN"):
            if owner.observation.state not in ("CLAIM_TERMINAL", "CLEANUP_TERMINAL") or owner.claim_task_or_null is None:
                _fail()
        if owner.state == "I2_POSTCHECK_UNKNOWN":
            r = owner.exact_reserved_receipt
            if (cell is not r.abort_bundle.candidate_cell or r.postcheck_observation.state != "RECORDED"
                    or r.postcheck_observation.first_mismatch_or_null != "I2_ROW"):
                _fail()
        if owner.state in ("ACTIVE_POSTCHECK_UNKNOWN", "ACTIVE_ABORT_CONFLICT_HOLD", "I3_POSTCHECK_UNKNOWN"):
            post = owner.active_receipt_or_null.postcheck_observation
            if (post.expected_identity is not owner.active_candidate_cell_or_null.bundle_identity
                    or post.state != "RECORDED" or post.first_mismatch_or_null not in (
                        "ACTIVE_STORE", "ACTIVE_LEASE", "ACTIVE_CAPTURE", "ACTIVE_OWNER", "ACTIVE_CANDIDATE",
                        "ACTIVE_BROKER", "ACTIVE_SESSION", "ACTIVE_REGISTRY", "ACTIVE_GENERATION", "ACTIVE_PUBLISH",
                        "ACTIVE_ABORT_CONFLICT", "I3_PUBLISH", "I3_POSTCHECK")
                    or post.observed_stage_or_null not in ("PREPARING", "RESERVED", "ACTIVE", "RESERVED_INVALIDATED",
                        "ACTIVE_INVALIDATED", "PRECAPTURE_INVALIDATED", "FOREIGN")
                    or (post.observed_identity_or_null is not None
                        and type(post.observed_identity_or_null) not in tuple(reserved._CELL_IDENTITY_TYPES.values()))):
                _fail()
        if owner.state == "I3_POSTCHECK_UNKNOWN":
            selection = owner.active_cleanup_selection
            if (not _observable_cell(owner.exact_store, cell) or cell.stage != "ACTIVE_INVALIDATED"
                    or selection.selected_candidate_identity_or_null is not cell.bundle_identity):
                _fail()
        return owner.state
    _fail()


def _validate_i3_post(c, cell):
    _postpublish_edges(c)
    if not reserved._valid_cell_v2(c.exact_discussion_store, cell) or cell.stage != "ACTIVE_INVALIDATED":
        _fail()
    lease, capture, receipt = cell.exact_tuple
    _capture_matches(lease, capture)
    source = c.active_offer_source_or_null
    offer = source.exact_offer_receipt_or_null
    ticket = c.active_initial_ticket_or_null
    if (receipt.state != "PUBLISHED" or c.flow_state != "ACTIVE_RETIRED_HELD"
            or c.lifecycle != "RETIRED" or c.initial_invocation_slot != "INVALIDATED_HELD"
            or receipt.reason != c.claim_activate_outcome_or_null or receipt.reason not in _REASONS
            or source.state != "RETIRED_HELD" or offer.state != "RETIRED_HELD" or ticket.state != "RETIRED_HELD"
            or offer.exact_composition is not c or offer.exact_offer_source is not source
            or source.exact_initial_ticket is not ticket or offer.exact_session is not c.exact_broker_session
            or lease.lease_revision != 3 or lease.status != "INVALIDATED"
            or receipt.exact_active_lease.lease_revision != 2 or receipt.exact_active_lease.status != "ACTIVE"
            or receipt.exact_capture is not capture or not _broker_shape(offer, "RELEASE", offer.exact_control_lane.disposition)):
        _fail()
    for field in reserved._LEASE_PRESERVED_FIELDS:
        if getattr(lease, field) != getattr(receipt.exact_active_lease, field):
            _fail()


async def _retire_active_owned_v2(port):
    c = _port(port)
    owner = c.claim_activate_owner_or_null
    if owner is None or owner.state != "ACTIVE":
        _fail()
    _validate_claim_row_v2(c)
    selection = owner.active_cleanup_selection
    if selection.state != "BOUND_EMPTY":
        _fail()
    if c.claim_activate_cancel_event.is_set() or c.exact_arbiter._stopped:
        reason = "ACTIVE_CANCEL"
    elif c.exact_clock_callable() >= owner.hard_deadline_monotonic:
        reason = "ACTIVE_EXPIRED"
    elif not _fresh(c, owner.exact_reserved_receipt):
        reason = "ACTIVE_STALE"
    else:
        reason = "ACTIVE_SCOPE_END"
    candidate = selection.candidates[_REASONS.index(reason)].candidate_cell
    expected = owner.active_candidate_cell_or_null
    _set(selection, "selected_reason_or_null", reason)
    _set(selection, "selected_candidate_identity_or_null", candidate.bundle_identity)
    _set(selection, "state", "SELECTED")
    _set(owner, "state", "CLEANUP_PENDING")
    _set(owner.active_receipt_or_null, "state", "CLEANUP_PENDING")
    c.flow_state = "ACTIVE_CLEANUP_PENDING"
    if not await _release_once(owner, reason, active=True):
        return None
    try:
        success = owner.exact_store._cas_claim_invalidated_v2(expected, candidate, owner)
    except Exception:
        if owner.exact_store._lease_bundle_cell_v2 is candidate:
            _set(owner, "state", "I3_POSTCHECK_UNKNOWN")
            c.flow_state = "I3_POSTCHECK_UNKNOWN"
            _record_active_postcheck(owner, "I3_PUBLISH")
            return None
        success = False
    if not success:
        _record_active_postcheck(owner, "ACTIVE_ABORT_CONFLICT")
        _set(selection, "state", "CONFLICT_HELD")
        _set(owner, "state", "ACTIVE_ABORT_CONFLICT_HOLD")
        c.flow_state = "ACTIVE_ABORT_CONFLICT_HOLD"
        return None
    _set(selection, "state", "PUBLISHED")
    for item in selection.candidates:
        _set(item.candidate_cell.exact_tuple[-1], "state", "PUBLISHED" if item.candidate_cell is candidate else "RETIRED")
    _set(owner.active_receipt_or_null, "state", "RETIRED")
    _retire_components(c, owner.exact_reserved_receipt.exact_preparing_receipt, "ACTIVE_RETIRED_HELD")
    c.claim_activate_outcome_or_null = reason
    try:
        if owner.exact_store._lease_bundle_cell_v2 is not candidate:
            _fail()
        _static(owner)
        _validate_i3_post(c, candidate)
    except Exception:
        _set(owner, "state", "I3_POSTCHECK_UNKNOWN")
        c.flow_state = "I3_POSTCHECK_UNKNOWN"
        _record_active_postcheck(owner, "I3_POSTCHECK")
        return None
    result = candidate.exact_tuple[-1]
    _set(selection, "candidates", None)
    _clear_owner(owner)
    return result


async def _stop_claim_task(c, owner):
    if owner.state in ("CLAIM_WAIT", "CLAIM_DONE_BUILD", "CLAIM_GRANTED_BUILD"):
        driver = owner.exact_claim_driver_task
        if driver is asyncio.current_task():
            return
        if not await _settle((driver,), owner.settlement_deadline_monotonic, c.exact_clock_callable):
            return
    if c.claim_activate_owner_or_null is owner and owner.state == "ACTIVE":
        await _retire_active_owned_v2(c.claim_activate_port)


async def _stop_claim_activate_owned_v2(c):
    owner = c.claim_activate_owner_or_null
    if owner is None:
        return
    _port(c.claim_activate_port)
    c.claim_activate_cancel_event.set()
    if c.claim_activate_retire_task_or_null is None:
        c.claim_activate_retire_task_or_null = asyncio.create_task(_stop_claim_task(c, owner))
    await asyncio.shield(c.claim_activate_retire_task_or_null)
