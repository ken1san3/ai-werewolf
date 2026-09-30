"""Private PREPARING to RESERVED offline finalization.

This module does not expose a public authority constructor and never calls the
broker, provider, projector, or delivery paths.
"""

from __future__ import annotations

import asyncio
import math
import weakref
from dataclasses import fields, replace
from types import MappingProxyType

from ai_client.network.types import AbilityAction, CoDeclareAction, VoteAction

from .authority_capture_bridge_v2 import OwnedFinalCaptureSourceV2, PublicChannelAuthorityV2
from .capture_v2 import (
    StageSchemaHashV2, StructuralPublicChannelCandidateV2,
    canonical_sha256_v2, make_capture_v2, recipient_proof_sha256_v2,
)
from .generation_v2 import (
    AbilityOptionV2, CoOptionV2, GenerationCatalogV2, OpinionBasisV2,
    VoteOptionV2, build_generation_v2_schema,
)
from .offer_composition_v2 import (
    OfferCompositionError, OfferPreparingCallerPortV2,
    PreparingLeaseOwnerReceiptV2,
)

_ISSUER = object()
_LEASE_PRESERVED_FIELDS = (
    "schema_version", "state_lease_id", "owner_invocation_id", "capture_id", "base_revision",
    "world_version", "last_applied_sequence", "fact_revision", "phase_identity", "action_generation",
    "connection_generation", "input_sha256", "catalog_sha256", "option_catalog_sha256",
    "recipient_proof_sha256", "profile_bundle_sha256", "stage_schema_sha256s",
    "expires_at_monotonic_us", "broker_lease_ids", "accepted_plan_sha256", "stage_projections",
    "reserved_attempt", "attempt_ledger", "recovery_proof_sha256",
)


class ReservedFinalizeError(OfferCompositionError):
    pass


class ReservedCaptureOwnerReceiptV2:
    __slots__ = ("exact_preparing_receipt", "exact_source_read", "exact_capture",
                 "exact_lease", "exact_profile_bundle", "exact_runtime_authorities",
                 "catalog_binding", "option_catalog_binding", "state", "_issuer",
                 "owner_identity", "abort_bundle", "notification_observation",
                 "postcheck_observation", "stage_schemas", "exact_input_projection", "cell_identity", "cell_weakref", "__weakref__")
    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER:
            raise TypeError("reserved receipt is opaque")
        for key, value in values.items():
            object.__setattr__(self, key, value)
        object.__setattr__(self, "_issuer", _ISSUER)
    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("reserved receipt is immutable")
    def __repr__(self) -> str:
        return "ReservedCaptureOwnerReceiptV2(<redacted>)"
    def __reduce_ex__(self, _protocol: int):
        raise TypeError("reserved receipt cannot be serialized")


class _PrivateRecord:
    __slots__ = ("__weakref__",)

    def __init__(self, token, **values):
        if token is not _ISSUER:
            raise TypeError("private finalization record")
        if set(values) != set(type(self).__slots__):
            raise TypeError("closed finalization record shape")
        for name, value in values.items():
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise TypeError("immutable finalization record")

    def __repr__(self):
        return type(self).__name__ + "(<redacted>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("finalization record cannot be copied or serialized")


class PreparingBundleIdentityV2(_PrivateRecord):
    __slots__ = ()


class ReservedBundleIdentityV2(_PrivateRecord):
    __slots__ = ()


class PreCaptureInvalidatedBundleIdentityV2(_PrivateRecord):
    __slots__ = ()


class ReservedInvalidatedBundleIdentityV2(_PrivateRecord):
    __slots__ = ()


class LeaseBundleCellV2(_PrivateRecord):
    __slots__ = ("stage", "bundle_identity", "exact_tuple")


_CELL_IDENTITY_TYPES = MappingProxyType({
    "PREPARING": PreparingBundleIdentityV2, "RESERVED": ReservedBundleIdentityV2,
    "PRECAPTURE_INVALIDATED": PreCaptureInvalidatedBundleIdentityV2,
    "RESERVED_INVALIDATED": ReservedInvalidatedBundleIdentityV2,
})


def _issue_bundle_cell_v2(store, stage, identity, exact_tuple):
    store._check_owner()
    registry = store._lease_bundle_cells_v2
    if identity in registry:
        raise ReservedFinalizeError("FINALIZE_CELL_IDENTITY_REUSED")
    allowed = {
        "PREPARING": PreparingBundleIdentityV2, "RESERVED": ReservedBundleIdentityV2,
        "PRECAPTURE_INVALIDATED": PreCaptureInvalidatedBundleIdentityV2,
        "RESERVED_INVALIDATED": ReservedInvalidatedBundleIdentityV2,
    }
    if (type(identity) is not allowed.get(stage) or type(exact_tuple) is not tuple
            or not store._known_generation_bundle_v2(exact_tuple)):
        raise ReservedFinalizeError("FINALIZE_CELL_SHAPE_MISMATCH")
    cell = LeaseBundleCellV2(_ISSUER, stage=stage, bundle_identity=identity, exact_tuple=exact_tuple)
    cell_ref = weakref.ref(cell)
    registry[identity] = cell_ref
    object.__setattr__(exact_tuple[-1], "cell_identity", identity)
    object.__setattr__(exact_tuple[-1], "cell_weakref", cell_ref)
    return cell


def _valid_cell_v2(store, cell):
    if type(cell) is not LeaseBundleCellV2:
        return False
    return (type(cell.bundle_identity) is _CELL_IDENTITY_TYPES.get(cell.stage)
            and store._lease_bundle_cells_v2.get(cell.bundle_identity) is not None
            and store._lease_bundle_cells_v2[cell.bundle_identity]() is cell
            and type(cell.exact_tuple) is tuple
            and store._known_generation_bundle_v2(cell.exact_tuple)
            and cell.exact_tuple[-1].cell_identity is cell.bundle_identity
            and cell.exact_tuple[-1].cell_weakref() is cell)


def _owned_cell(owner):
    cell = owner.cell_weakref()
    if cell is None or cell.bundle_identity is not owner.cell_identity:
        raise ReservedFinalizeError("FINALIZE_CELL_OWNER_MISMATCH")
    return cell


def _owned_tuple(owner):
    return _owned_cell(owner).exact_tuple


class FinalizeTerminalMapV2(_PrivateRecord):
    __slots__ = ("INTERNAL_BUILD", "STALE_FRESHNESS", "DEADLINE_EXPIRED", "CANCEL_REQUESTED")


class FinalizeAbortSelectionV2(_PrivateRecord):
    __slots__ = ("exact_composition", "exact_pending", "exact_terminal_map",
                 "exact_preparing_identity", "exact_preparing_receipt_weakref_or_null",
                 "exact_abort_bundle_weakref_or_null", "selected_class_or_null",
                 "exact_selected_result_or_null", "state")


class FinalizeConflictObservationV2(_PrivateRecord):
    __slots__ = ("purpose", "exact_composition", "expected_bundle_identity",
                 "expected_cell_weakref", "expected_stage", "observed_bundle_identity_or_null",
                 "observed_cell_weakref_or_null", "observed_shape_or_null", "state")


class FinalizeNotificationObservationSlotV2(_PrivateRecord):
    __slots__ = ("purpose", "exact_observation", "exact_pending", "exact_future",
                 "exact_session", "exact_expected_result", "state")


_POSTCHECK_FIELDS = (
    "lease", "capture", "receipt", "composition", "ticket", "source",
    "offer_receipt", "pending", "driver", "session", "lane", "client_lease",
    "composition_state", "slot_state", "ticket_state", "source_state", "offer_receipt_state",
)
_POSTCHECK_CODES = (
    "LEASE", "CAPTURE", "RECEIPT", "COMPOSITION", "TICKET", "SOURCE",
    "OFFER_RECEIPT", "PENDING", "DRIVER", "SESSION", "LANE", "CLIENT_LEASE",
    "COMPOSITION", "SLOT", "TICKET", "SOURCE", "OFFER_RECEIPT",
)


class FinalizePostcheckObservationV2(_PrivateRecord):
    __slots__ = tuple("expected_" + name for name in _POSTCHECK_FIELDS) + tuple(
        "observed_" + name for name in _POSTCHECK_FIELDS) + ("first_mismatch_or_null", "state",
        "expected_bundle_identity", "expected_cell_weakref", "expected_stage",
        "observed_bundle_identity_or_null", "observed_cell_weakref_or_null", "observed_shape_or_null")


_POSTCHECK_EXPECTED = tuple("expected_" + name for name in _POSTCHECK_FIELDS)
_POSTCHECK_OBSERVED = tuple("observed_" + name for name in _POSTCHECK_FIELDS)


class PreCaptureInvalidatedOwnerReceiptV2(_PrivateRecord):
    __slots__ = ("schema_version", "predecessor_identity", "exact_composition",
                 "exact_store", "exact_initial_ticket", "exact_pending", "exact_driver_task",
                 "exact_offer_source", "exact_offer_receipt", "exact_session",
                 "exact_control_lane", "exact_client_lease", "exact_request",
                 "exact_dispatch_deadline", "exact_preparing_lease", "invalidated_lease",
                 "terminal_map", "abort_selection", "abort_notification_observation",
                 "reason", "state", "cell_identity", "cell_weakref")


class PreCaptureFinalizeAbortBundleV2(_PrivateRecord):
    __slots__ = ("predecessor_identity", "invalidated_lease", "invalidated_receipt",
                 "terminal_map", "abort_selection", "conflict_observation",
                 "abort_notification_observation", "candidate_cell", "state")


class ReservedInvalidatedOwnerReceiptV2(_PrivateRecord):
    __slots__ = ("schema_version", "predecessor_identity", "exact_composition",
                 "exact_store", "exact_initial_ticket", "exact_pending", "exact_driver_task",
                 "exact_offer_source", "exact_offer_receipt", "exact_session",
                 "exact_control_lane", "exact_client_lease", "exact_request",
                 "exact_dispatch_deadline", "exact_capture", "exact_reserved_lease",
                 "invalidated_lease", "reserved_notification_observation",
                 "postcheck_observation", "reason", "state", "cell_identity", "cell_weakref")


class ReservedFinalizeAbortBundleV2(_PrivateRecord):
    __slots__ = ("predecessor_identity", "exact_capture", "invalidated_lease",
                 "invalidated_receipt", "conflict_observation", "reserved_notification_observation",
                 "postcheck_observation", "candidate_cell", "state")


def _notification_slot(owner, purpose, result):
    from .offer_composition_v2 import NotificationFailureObservationV2, _ISSUER as token
    return FinalizeNotificationObservationSlotV2(
        _ISSUER, purpose=purpose, exact_observation=NotificationFailureObservationV2(token),
        exact_pending=owner.exact_pending, exact_future=owner.exact_pending.result,
        exact_session=owner.exact_session, exact_expected_result=result, state="EMPTY")


def _owner_fields(owner):
    return {name: getattr(owner, name) for name in (
        "exact_composition", "exact_store", "exact_initial_ticket", "exact_pending",
        "exact_driver_task", "exact_offer_source", "exact_offer_receipt", "exact_session",
        "exact_control_lane", "exact_client_lease", "exact_request", "exact_dispatch_deadline")}


def _prepare_abort_owned_v2(owner, preparing_bundle):
    """All allocation, weak binding and failure candidates precede PREPARING."""
    identity = PreparingBundleIdentityV2(_ISSUER)
    preparing_cell = _issue_bundle_cell_v2(owner.exact_store, "PREPARING", identity, preparing_bundle)
    terms = owner.exact_initial_ticket.terminal_candidates
    terminals = FinalizeTerminalMapV2(_ISSUER, INTERNAL_BUILD=terms.internal_failure,
        STALE_FRESHNESS=terms.stale, DEADLINE_EXPIRED=terms.deadline_suppressed,
        CANCEL_REQUESTED=terms.preparing_complete)
    selection = FinalizeAbortSelectionV2(_ISSUER,
        exact_composition=owner.exact_composition, exact_pending=owner.exact_pending,
        exact_terminal_map=terminals, exact_preparing_identity=identity,
        exact_preparing_receipt_weakref_or_null=None,
        exact_abort_bundle_weakref_or_null=None, selected_class_or_null=None,
        exact_selected_result_or_null=None, state="ALLOCATED")
    notification = _notification_slot(owner, "ABORT_TERMINAL", None)
    conflict = FinalizeConflictObservationV2(_ISSUER, purpose="PRECAPTURE_ABORT",
        exact_composition=owner.exact_composition, expected_bundle_identity=identity,
        expected_cell_weakref=owner.cell_weakref, expected_stage="PREPARING",
        observed_bundle_identity_or_null=None, observed_cell_weakref_or_null=None,
        observed_shape_or_null=None, state="EMPTY")
    invalid = replace(preparing_bundle[0], status="INVALIDATED", lease_revision=1)
    receipt = PreCaptureInvalidatedOwnerReceiptV2(_ISSUER, **_owner_fields(owner),
        schema_version="aiwolf.pre-capture-invalidated-owner-receipt.v2",
        predecessor_identity=identity, exact_preparing_lease=preparing_bundle[0],
        invalidated_lease=invalid, terminal_map=terminals, abort_selection=selection,
        abort_notification_observation=notification, reason="FINALIZE_ABORT", state="ARMED", cell_identity=None, cell_weakref=None)
    invalid_cell = _issue_bundle_cell_v2(owner.exact_store, "PRECAPTURE_INVALIDATED",
        PreCaptureInvalidatedBundleIdentityV2(_ISSUER), (invalid, receipt))
    bundle = PreCaptureFinalizeAbortBundleV2(_ISSUER, predecessor_identity=identity,
        invalidated_lease=invalid, invalidated_receipt=receipt, terminal_map=terminals,
        abort_selection=selection, conflict_observation=conflict,
        abort_notification_observation=notification, candidate_cell=invalid_cell, state="ARMED")
    object.__setattr__(selection, "exact_preparing_receipt_weakref_or_null", weakref.ref(owner))
    object.__setattr__(selection, "exact_abort_bundle_weakref_or_null", weakref.ref(bundle))
    object.__setattr__(selection, "state", "BOUND_EMPTY")
    object.__setattr__(owner, "preparing_identity", identity)
    object.__setattr__(owner, "abort_bundle", bundle)
    return preparing_cell


def _select_abort(owner, failure):
    bundle = owner.abort_bundle
    selection = bundle.abort_selection
    if (type(selection) is not FinalizeAbortSelectionV2 or selection.state != "BOUND_EMPTY"
            or selection.exact_composition is not owner.exact_composition
            or selection.exact_pending is not owner.exact_pending
            or selection.exact_preparing_receipt_weakref_or_null() is not owner
            or selection.exact_abort_bundle_weakref_or_null() is not bundle
            or selection.exact_preparing_identity is not owner.preparing_identity
            or selection.exact_terminal_map is not bundle.terminal_map
            or failure not in FinalizeTerminalMapV2.__slots__
            or selection.selected_class_or_null is not None
            or selection.exact_selected_result_or_null is not None):
        raise ReservedFinalizeError("FINALIZE_SELECTION_MISMATCH")
    result = getattr(bundle.terminal_map, failure)
    object.__setattr__(selection, "selected_class_or_null", failure)
    object.__setattr__(selection, "exact_selected_result_or_null", result)
    object.__setattr__(bundle.abort_notification_observation, "exact_expected_result", result)
    object.__setattr__(selection, "state", "SELECTED_UNPUBLISHED")
    return result


def _record_conflict(owner, bundle, observed):
    slot = bundle.conflict_observation
    if slot.state != "EMPTY":
        return
    shape = "FOREIGN"
    if observed is None:
        shape = "null"
    elif _valid_cell_v2(owner.exact_store, observed):
        shape = observed.stage
        object.__setattr__(slot, "observed_bundle_identity_or_null", observed.bundle_identity)
        object.__setattr__(slot, "observed_cell_weakref_or_null",
                           owner.exact_store._lease_bundle_cells_v2[observed.bundle_identity])
    object.__setattr__(slot, "observed_shape_or_null", shape)
    object.__setattr__(slot, "state", "RECORDED")
    object.__setattr__(bundle, "state", "CONFLICT_HELD")
    object.__setattr__(bundle.invalidated_receipt, "state", "CONFLICT_HELD")
    c = owner.exact_composition
    if type(bundle) is PreCaptureFinalizeAbortBundleV2:
        object.__setattr__(bundle.abort_selection, "state", "CONFLICT_HELD")
        c.flow_state = "FINALIZE_CONFLICT_HELD"
        c.initial_invocation_slot = "CONFLICT_HELD"
    else:
        c.flow_state = "RESERVED_POSTCHECK_UNKNOWN"


def _row_error():
    raise ReservedFinalizeError("FINALIZE_ROW_MISMATCH")


def _check_notification_row(slot, owner, purpose, result, recorded):
    from .offer_composition_v2 import NotificationFailureObservationV2
    if (type(slot) is not FinalizeNotificationObservationSlotV2
            or slot.purpose != purpose or slot.exact_pending is not owner.exact_pending
            or slot.exact_future is not owner.exact_pending.result
            or slot.exact_session is not owner.exact_session
            or slot.exact_expected_result is not result
            or type(slot.exact_observation) is not NotificationFailureObservationV2
            or slot.state != ("RECORDED" if recorded else "EMPTY")):
        _row_error()
    observation = slot.exact_observation
    if not recorded:
        if (observation.future_done is not None or observation.exact_result_visible is not None
                or observation.session_results_member is not None):
            _row_error()
    elif (type(observation.future_done) is not bool
            or type(observation.session_results_member) is not bool):
        _row_error()
    else:
        visible = None
        if slot.exact_future.done() and not slot.exact_future.cancelled():
            try:
                visible = slot.exact_future.result()
            except BaseException:
                pass
        if (observation.future_done is not slot.exact_future.done()
                or observation.exact_result_visible is not visible
                or observation.session_results_member is not (
                    owner.exact_request.invocation_id in owner.exact_session._results)):
            _row_error()


def _check_empty_conflict(slot, owner, identity, stage):
    if (type(slot) is not FinalizeConflictObservationV2
            or slot.exact_composition is not owner.exact_composition
            or slot.expected_bundle_identity is not identity or slot.expected_stage != stage
            or slot.state != "EMPTY" or slot.observed_bundle_identity_or_null is not None
            or slot.observed_cell_weakref_or_null is not None or slot.observed_shape_or_null is not None):
        _row_error()


def _validate_published_row_v2(c, expected_owner=None, *, selecting=False):
    try:
        c.exact_discussion_store._check_owner()
        return _validate_published_row_contents_v2(c, expected_owner, selecting=selecting)
    except (AttributeError, TypeError, KeyError, ValueError):
        raise ReservedFinalizeError("FINALIZE_ROW_MISMATCH") from None


def _validate_published_row_contents_v2(c, expected_owner=None, *, selecting=False):
    """Read-only closed-row check; a mismatch never repairs any owner or state."""
    store = c.exact_discussion_store
    cell = store._lease_bundle_cell_v2
    flow = c.flow_state
    conflict = flow == "FINALIZE_CONFLICT_HELD"
    if conflict:
        if type(expected_owner) is not PreparingLeaseOwnerReceiptV2:
            _row_error()
        owner = expected_owner
        receipt = owner
        stage = "PREPARING"
    else:
        if not _valid_cell_v2(store, cell):
            _row_error()
        stage = cell.stage
        receipt = cell.exact_tuple[-1]
        if type(receipt) is PreparingLeaseOwnerReceiptV2:
            owner = receipt
        elif type(receipt) is ReservedCaptureOwnerReceiptV2:
            owner = receipt.exact_preparing_receipt
        elif type(receipt) is PreCaptureInvalidatedOwnerReceiptV2:
            owner = receipt.abort_selection.exact_preparing_receipt_weakref_or_null()
        elif type(receipt) is ReservedInvalidatedOwnerReceiptV2:
            owner = receipt.postcheck_observation.expected_receipt.exact_preparing_receipt
        else:
            _row_error()
    if (type(owner) is not PreparingLeaseOwnerReceiptV2
            or (expected_owner is not None and owner is not expected_owner)
            or owner.exact_composition is not c or owner.exact_store is not store
            or store._offer_preparing_composition_v2 is not c
            or c.exact_caller_port._composition is not c
            or c.exact_arbiter._offer_preparing_composition_v2 is not c
            or c.exact_broker_session is not owner.exact_session
            or owner.exact_session._offer_preparing_composition_v2 is not c
            or c.initial_pending_or_null is not owner.exact_pending
            or owner.exact_pending.dispatch_deadline is not owner.exact_dispatch_deadline
            or owner.exact_initial_ticket.exact_pending_invocation is not owner.exact_pending
            or owner.exact_offer_source.exact_initial_ticket is not owner.exact_initial_ticket
            or owner.exact_offer_receipt.exact_offer_source is not owner.exact_offer_source
            or owner.exact_offer_receipt.exact_session is not owner.exact_session
            or owner.exact_offer_receipt.exact_control_lane is not owner.exact_control_lane
            or owner.exact_offer_receipt.exact_client_lease is not owner.exact_client_lease
            or owner.exact_offer_receipt.exact_request is not owner.exact_request):
        _row_error()
    if type(receipt) in (PreCaptureInvalidatedOwnerReceiptV2, ReservedInvalidatedOwnerReceiptV2):
        for name in ("exact_composition", "exact_store", "exact_initial_ticket", "exact_pending",
                     "exact_driver_task", "exact_offer_source", "exact_offer_receipt", "exact_session",
                     "exact_control_lane", "exact_client_lease", "exact_request", "exact_dispatch_deadline"):
            if getattr(receipt, name) is not getattr(owner, name):
                _row_error()
    session, lane, lease = owner.exact_session, owner.exact_control_lane, owner.exact_client_lease
    invocation_id = owner.exact_request.invocation_id
    # Finalization precedes claim, activation, attachment and generation. No
    # target-invocation work may already exist in these session registries.
    if (any(invocation_id in registry for registry in (
                session._results, session._acks, session._call_ordinals,
                session._request_ids, session._terminal_controls, session._attachments))
            or any(key[0] == invocation_id for key in session._generations)
            or session._control_lanes.get(invocation_id) is not lane
            or lane.lease is not lease or lane.disposition is not None or lane.callers != 0
            or lease._claimed or lease._released or lease._retired
            or session._claimed_invocation is not None or session._activated_invocation is not None):
        _row_error()
    pre = owner.abort_bundle
    selection = pre.abort_selection
    if (type(pre) is not PreCaptureFinalizeAbortBundleV2
            or owner.cell_identity is not owner.preparing_identity
            or pre.invalidated_receipt.exact_preparing_lease.status != "PREPARING"
            or pre.invalidated_receipt.exact_preparing_lease.lease_revision != 0
            or pre.predecessor_identity is not owner.preparing_identity
            or pre.invalidated_receipt.predecessor_identity is not owner.preparing_identity
            or selection.exact_preparing_identity is not owner.preparing_identity
            or selection.exact_composition is not c or selection.exact_pending is not owner.exact_pending
            or selection.exact_terminal_map is not pre.terminal_map
            or selection.exact_preparing_receipt_weakref_or_null() is not owner
            or selection.exact_abort_bundle_weakref_or_null() is not pre
            or pre.invalidated_receipt.abort_selection is not selection):
        _row_error()
    if (pre.invalidated_receipt.abort_notification_observation is not pre.abort_notification_observation
            or pre.invalidated_receipt.terminal_map is not pre.terminal_map
            or pre.conflict_observation.expected_cell_weakref is not owner.cell_weakref):
        _row_error()
    terms = owner.exact_initial_ticket.terminal_candidates
    if (pre.terminal_map.INTERNAL_BUILD is not terms.internal_failure
            or pre.terminal_map.STALE_FRESHNESS is not terms.stale
            or pre.terminal_map.DEADLINE_EXPIRED is not terms.deadline_suppressed
            or pre.terminal_map.CANCEL_REQUESTED is not terms.preparing_complete):
        _row_error()
    retired = flow == "FINALIZE_RETIRED_HELD"
    reserved = stage in {"RESERVED", "RESERVED_INVALIDATED"}
    if retired:
        if c.lifecycle != "RETIRED" or stage not in {"PRECAPTURE_INVALIDATED", "RESERVED_INVALIDATED"}:
            _row_error()
        slot_state, ticket_state, source_state, offer_state = (
            "INVALIDATED_HELD", "RETIRED_HELD", "RETIRED_HELD", "RETIRED_HELD")
    elif flow == "PREPARING" and stage == "PREPARING":
        slot_state, ticket_state, source_state, offer_state = (
            "PREPARING_HELD", "CONSUMED_PREPARING", "PREPARING", "CONSUMED_PREPARING")
    elif flow in {"RESERVED", "RESERVED_NOTIFICATION_UNKNOWN", "RESERVED_POSTCHECK_UNKNOWN"} and stage == "RESERVED":
        slot_state, ticket_state, source_state, offer_state = (
            "RESERVED_HELD", "CONSUMED_RESERVED", "RESERVED", "CONSUMED_RESERVED")
    elif flow in {"FINALIZE_FAILED_HELD", "FINALIZE_NOTIFICATION_UNKNOWN"} and stage == "PRECAPTURE_INVALIDATED":
        slot_state, ticket_state, source_state, offer_state = (
            "INVALIDATED_HELD", "CONSUMED_INVALIDATED", "INVALIDATED_HELD", "CONSUMED_INVALIDATED")
    elif conflict:
        slot_state, ticket_state, source_state, offer_state = (
            "CONFLICT_HELD", "CONSUMED_PREPARING", "PREPARING", "CONSUMED_PREPARING")
    else:
        _row_error()
    if not retired and c.lifecycle != "ACTIVE":
        _row_error()
    post_unknown = flow == "RESERVED_POSTCHECK_UNKNOWN"
    if (c.initial_invocation_slot != slot_state
            or owner.exact_initial_ticket.state != ticket_state
            or owner.exact_offer_source.state != source_state
            or owner.exact_offer_receipt.state != offer_state):
        _row_error()
    if not post_unknown and (c.active_initial_ticket_or_null is not owner.exact_initial_ticket
            or c.active_offer_source_or_null is not owner.exact_offer_source
            or owner.exact_offer_source.exact_offer_receipt_or_null is not owner.exact_offer_receipt):
        _row_error()
    selected = selection.selected_class_or_null
    selected_result = selection.exact_selected_result_or_null
    if selected is None:
        if selected_result is not None:
            _row_error()
    elif selected not in FinalizeTerminalMapV2.__slots__ or selected_result is not getattr(pre.terminal_map, selected):
        _row_error()
    if conflict:
        if (pre.state != "CONFLICT_HELD" or pre.invalidated_receipt.state != "CONFLICT_HELD"
                or selection.state != "CONFLICT_HELD" or pre.conflict_observation.state != "RECORDED"
                or pre.conflict_observation.observed_shape_or_null is None
                or pre.conflict_observation.expected_bundle_identity is not owner.preparing_identity
                or pre.conflict_observation.expected_stage != "PREPARING"
                or pre.conflict_observation.exact_composition is not c):
            _row_error()
        observation = pre.conflict_observation
        if observation.observed_shape_or_null == "null":
            if cell is not None or observation.observed_cell_weakref_or_null is not None:
                _row_error()
        elif observation.observed_shape_or_null == "FOREIGN":
            if cell is None or _valid_cell_v2(store, cell) or observation.observed_cell_weakref_or_null is not None:
                _row_error()
        elif (not _valid_cell_v2(store, cell) or observation.observed_cell_weakref_or_null is None
                or observation.observed_cell_weakref_or_null() is not cell
                or observation.observed_bundle_identity_or_null is not cell.bundle_identity
                or observation.observed_shape_or_null != cell.stage):
            _row_error()
    else:
        _check_empty_conflict(pre.conflict_observation, owner, owner.preparing_identity, "PREPARING")
        expected_pre_state = "RETIRED" if reserved else ("PUBLISHED" if stage == "PRECAPTURE_INVALIDATED" else "ARMED")
        if pre.state != expected_pre_state or pre.invalidated_receipt.state != expected_pre_state:
            _row_error()
    if stage == "PREPARING" and not conflict:
        if selection.state != ("SELECTED_UNPUBLISHED" if selecting else "BOUND_EMPTY"):
            _row_error()
        if not selecting and selected is not None:
            _row_error()
    elif reserved:
        if selection.state != "RETIRED_UNSELECTED" or selected is not None:
            _row_error()
    elif stage == "PRECAPTURE_INVALIDATED":
        allowed_selection = ("RETIRED_UNSELECTED", "DELIVERED", "PUBLISHED") if retired else (
            ("NOTIFICATION_UNKNOWN",) if flow == "FINALIZE_NOTIFICATION_UNKNOWN" else ("PUBLISHED", "DELIVERED"))
        if selection.state not in allowed_selection:
            _row_error()
    _check_notification_row(pre.abort_notification_observation, owner, "ABORT_TERMINAL", selected_result,
                            flow == "FINALIZE_NOTIFICATION_UNKNOWN")
    result = terms.preparing_complete if reserved else selected_result
    if reserved:
        reserved_receipt = receipt if stage == "RESERVED" else receipt.postcheck_observation.expected_receipt
        abort = reserved_receipt.abort_bundle
        if (type(reserved_receipt) is not ReservedCaptureOwnerReceiptV2
                or abort.predecessor_identity is not reserved_receipt.owner_identity
                or abort.invalidated_receipt.predecessor_identity is not reserved_receipt.owner_identity
                or abort.state != ("PUBLISHED" if retired else "ARMED")
                or abort.invalidated_receipt.state != abort.state):
            _row_error()
        if (abort.reserved_notification_observation is not reserved_receipt.notification_observation
                or abort.invalidated_receipt.reserved_notification_observation is not reserved_receipt.notification_observation
                or abort.postcheck_observation is not reserved_receipt.postcheck_observation
                or abort.invalidated_receipt.postcheck_observation is not reserved_receipt.postcheck_observation
                or abort.conflict_observation.expected_cell_weakref is not reserved_receipt.cell_weakref):
            _row_error()
        _check_empty_conflict(abort.conflict_observation, owner, reserved_receipt.owner_identity, "RESERVED")
        _check_notification_row(reserved_receipt.notification_observation, owner, "RESERVED_SUCCESS", result,
                                flow == "RESERVED_NOTIFICATION_UNKNOWN")
        post = reserved_receipt.postcheck_observation
        if post.state != ("RECORDED" if post_unknown else "EMPTY"):
            _row_error()
        if post_unknown:
            if (post.first_mismatch_or_null is None
                    or post.observed_cell_weakref_or_null is None
                    or post.observed_cell_weakref_or_null() is not cell
                    or post.observed_ticket is not c.active_initial_ticket_or_null
                    or post.observed_source is not c.active_offer_source_or_null
                    or post.observed_offer_receipt is not owner.exact_offer_source.exact_offer_receipt_or_null):
                _row_error()
            first = None
            current_values = _postcheck_values(owner, cell.exact_tuple)
            for index, value in enumerate(current_values):
                recorded = getattr(post, _POSTCHECK_OBSERVED[index])
                expected = getattr(post, _POSTCHECK_EXPECTED[index])
                if _POSTCHECK_FIELDS[index] == "composition_state":
                    value = "RESERVED"
                if (recorded is not value) if index < 12 else (recorded != value):
                    _row_error()
                differs = (recorded is not expected) if index < 12 else (recorded != expected)
                if first is None and differs:
                    first = _POSTCHECK_CODES[index]
            if first != post.first_mismatch_or_null:
                _row_error()
        elif (post.first_mismatch_or_null is not None or post.observed_shape_or_null is not None
                or post.observed_cell_weakref_or_null is not None or post.observed_bundle_identity_or_null is not None
                or any(getattr(post, name) is not None for name in _POSTCHECK_OBSERVED)):
            _row_error()
    pending = owner.exact_pending
    delivered = pending.result.done() and not pending.result.cancelled()
    if delivered:
        try:
            delivered = pending.result.result() is result
        except BaseException:
            delivered = False
    unknown = flow in {"FINALIZE_NOTIFICATION_UNKNOWN", "RESERVED_NOTIFICATION_UNKNOWN", "RESERVED_POSTCHECK_UNKNOWN"}
    if (not delivered and not unknown and not retired and pending.result.done()):
        _row_error()
    if delivered and not unknown:
        if not ((c.exact_arbiter._active is pending and c.exact_arbiter._admission_driver is owner.exact_driver_task)
                or (c.exact_arbiter._active is None and c.exact_arbiter._admission_driver is None)):
            _row_error()
    elif (c.exact_arbiter._active is not pending or c.exact_arbiter._admission_driver is not owner.exact_driver_task):
        _row_error()
    return flow


def _abort_preparing_owned_v2(owner, failure=None, *, retire=False):
    bundle = owner.abort_bundle
    c = owner.exact_composition
    current_cell = owner.exact_store._lease_bundle_cell_v2
    current = None if type(current_cell) is not LeaseBundleCellV2 else current_cell.exact_tuple
    if current is bundle.candidate_cell.exact_tuple and bundle.state == "PUBLISHED":
        return None
    if owner.cell_weakref() is None or current_cell is not owner.cell_weakref():
        _record_conflict(owner, bundle, current_cell)
        return None
    try:
        _validate_published_row_v2(c, owner)
    except ReservedFinalizeError:
        return None
    if not retire:
        _select_abort(owner, failure)
    try:
        published = owner.exact_store._cas_invalidated_lease_v2(current_cell, owner, bundle)
    except ReservedFinalizeError:
        return None
    if not published:
        _record_conflict(owner, bundle, owner.exact_store._lease_bundle_cell_v2)
        return None
    object.__setattr__(bundle, "state", "PUBLISHED")
    object.__setattr__(bundle.invalidated_receipt, "state", "PUBLISHED")
    object.__setattr__(bundle.abort_selection, "state", "RETIRED_UNSELECTED" if retire else "PUBLISHED")
    object.__setattr__(owner.exact_initial_ticket, "state", "RETIRED_HELD" if retire else "CONSUMED_INVALIDATED")
    object.__setattr__(owner.exact_offer_source, "state", "RETIRED_HELD" if retire else "INVALIDATED_HELD")
    object.__setattr__(owner.exact_offer_receipt, "state", "RETIRED_HELD" if retire else "CONSUMED_INVALIDATED")
    c.flow_state = "FINALIZE_RETIRED_HELD" if retire else "FINALIZE_FAILED_HELD"
    c.initial_invocation_slot = "INVALIDATED_HELD"
    return bundle.abort_selection.exact_selected_result_or_null


def _record_notification_failure(slot):
    obs = slot.exact_observation
    if (slot.state != "EMPTY" or obs.future_done is not None
            or obs.exact_result_visible is not None or obs.session_results_member is not None
            or slot.exact_pending.result is not slot.exact_future):
        raise ReservedFinalizeError("FINALIZE_NOTIFICATION_SLOT_MISMATCH")
    future = slot.exact_future
    done = future.done()
    visible = None
    if done and not future.cancelled():
        try:
            visible = future.result()
        except BaseException:
            pass
    object.__setattr__(obs, "future_done", done)
    object.__setattr__(obs, "exact_result_visible", visible)
    object.__setattr__(obs, "session_results_member",
                       slot.exact_pending.admission_invocation_id in slot.exact_session._results)
    object.__setattr__(slot, "state", "RECORDED")


def _notify_finalized_owned_v2(owner, reserved_receipt=None):
    c = owner.exact_composition
    try:
        _validate_published_row_v2(c, owner)
    except ReservedFinalizeError:
        return None
    if owner.exact_pending.result.done():
        return None
    if reserved_receipt is None:
        selection = owner.abort_bundle.abort_selection
        if selection.state != "PUBLISHED":
            return None
        slot = owner.abort_bundle.abort_notification_observation
        result = selection.exact_selected_result_or_null
    else:
        if c.flow_state != "RESERVED":
            return None
        slot = reserved_receipt.notification_observation
        result = slot.exact_expected_result
    if (type(slot) is not FinalizeNotificationObservationSlotV2 or slot.state != "EMPTY"
            or slot.exact_pending is not owner.exact_pending
            or slot.exact_future is not owner.exact_pending.result
            or slot.exact_session is not owner.exact_session
            or slot.exact_expected_result is not result
            or slot.purpose != ("ABORT_TERMINAL" if reserved_receipt is None else "RESERVED_SUCCESS")):
        raise ReservedFinalizeError("FINALIZE_NOTIFICATION_SLOT_MISMATCH")
    try:
        c.exact_arbiter._finish(owner.exact_pending, result)
        delivered = (slot.exact_future.done() and not slot.exact_future.cancelled()
                     and slot.exact_future.result() is result)
    except BaseException:
        delivered = False
    if not delivered:
        _record_notification_failure(slot)
        if reserved_receipt is None:
            object.__setattr__(selection, "state", "NOTIFICATION_UNKNOWN")
            c.flow_state = "FINALIZE_NOTIFICATION_UNKNOWN"
        else:
            c.flow_state = "RESERVED_NOTIFICATION_UNKNOWN"
        return None
    if reserved_receipt is None:
        object.__setattr__(selection, "state", "DELIVERED")
    return result


def _retire_finalized_owned_v2(c):
    current = c.exact_discussion_store._preparing_lease_bundle_v2
    if current is not None or c.flow_state in {
            "PREPARING", "RESERVED", "FINALIZE_FAILED_HELD", "FINALIZE_RETIRED_HELD",
            "FINALIZE_NOTIFICATION_UNKNOWN", "RESERVED_NOTIFICATION_UNKNOWN",
            "RESERVED_POSTCHECK_UNKNOWN", "FINALIZE_CONFLICT_HELD"}:
        try:
            row = _validate_published_row_v2(c)
        except ReservedFinalizeError:
            return True
        if row in {"FINALIZE_RETIRED_HELD", "FINALIZE_NOTIFICATION_UNKNOWN",
                   "RESERVED_NOTIFICATION_UNKNOWN", "RESERVED_POSTCHECK_UNKNOWN", "FINALIZE_CONFLICT_HELD"}:
            return True
    if current is None:
        return False
    owner = current[-1]
    if type(owner) is PreparingLeaseOwnerReceiptV2:
        _abort_preparing_owned_v2(owner, retire=True)
    elif type(owner) is ReservedCaptureOwnerReceiptV2:
        previous = owner.exact_preparing_receipt
        bundle = owner.abort_bundle
        try:
            published = c.exact_discussion_store._cas_invalidated_lease_v2(c.exact_discussion_store._lease_bundle_cell_v2, owner, bundle)
        except ReservedFinalizeError:
            return True
        if not published:
            _record_conflict(previous, bundle, c.exact_discussion_store._lease_bundle_cell_v2)
            return True
        object.__setattr__(bundle, "state", "PUBLISHED")
        object.__setattr__(bundle.invalidated_receipt, "state", "PUBLISHED")
        object.__setattr__(previous.abort_bundle, "state", "RETIRED")
        object.__setattr__(previous.abort_bundle.invalidated_receipt, "state", "RETIRED")
        object.__setattr__(previous.abort_bundle.abort_selection, "state", "RETIRED_UNSELECTED")
        owner = previous
        for component in (owner.exact_initial_ticket, owner.exact_offer_source, owner.exact_offer_receipt):
            object.__setattr__(component, "state", "RETIRED_HELD")
        c.flow_state = "FINALIZE_RETIRED_HELD"
        c.initial_invocation_slot = "INVALIDATED_HELD"
    elif type(owner) in (PreCaptureInvalidatedOwnerReceiptV2, ReservedInvalidatedOwnerReceiptV2):
        for component in (owner.exact_initial_ticket, owner.exact_offer_source, owner.exact_offer_receipt):
            object.__setattr__(component, "state", "RETIRED_HELD")
        c.flow_state = "FINALIZE_RETIRED_HELD"
        c.initial_invocation_slot = "INVALIDATED_HELD"
    else:
        return False
    c.lifecycle = "RETIRED"
    return True


def _freeze(value):
    if isinstance(value, (dict, MappingProxyType)):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(child) for child in value)
    return value


def _plain(value: object) -> object:
    if hasattr(value, "__dataclass_fields__"):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, MappingProxyType):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, dict):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(child) for child in value]
    if hasattr(value, "value") and type(value.value) is str:
        return value.value
    return value


def _stages(trigger_kind: str) -> tuple[str, ...]:
    return {
        "INITIAL_CHAT": ("chat_plan", "message"),
        "PEER_CHAT": ("chat_plan", "message"),
        "PRE_VOTE": ("pre_vote",),
        "CO_OPPORTUNITY": ("co_opportunity",),
        "ABILITY": ("ability",),
    }[trigger_kind]


def _compile_catalog(source: OwnedFinalCaptureSourceV2):
    if type(source) is not OwnedFinalCaptureSourceV2 or source._origin_ref() is not source:
        raise ReservedFinalizeError("FINALIZE_SOURCE_OWNER_MISMATCH")
    if source.exact_prepared_material._origin_ref() is not source.exact_prepared_material:
        raise ReservedFinalizeError("FINALIZE_SOURCE_OWNER_MISMATCH")
    return _compile_catalog_records(source)


def _compile_catalog_records(source):
    """Compile exact owned snapshots; never infer a missing reference."""
    from ai_client.network.types import ChatAction
    from .authority_capture_bridge_v2 import _subject_binding, _sidecar_projection

    def require(condition):
        if not condition:
            raise ReservedFinalizeError("CATALOG_UNREPRESENTABLE")

    def sha(value):
        return canonical_sha256_v2(_plain(value))

    material = source.exact_prepared_material
    capture = source.exact_capture_read.capture
    state = source.exact_capture_read.state
    inbound = source.exact_inbound_read
    world = inbound.world_snapshot
    context = source.exact_bridge._receipt.exact_context_object
    require(context is capture.context)
    require(sha(state) == capture.state_sha256)
    require(len(context.abilities) <= 16)

    def root(kind, projection):
        is_context = kind == "VALIDATED_CONTEXT"
        has_capture = kind in {"CAPTURE_EVIDENCE", "DISCUSSION_STATE", "PREPARED_ACTION", "PREPARED_DISCLOSURE"}
        return dict(root_kind=kind, root_sha256=sha(projection),
            world_version=None if is_context else capture.world_version,
            last_applied_sequence=None if is_context else capture.last_applied_seq,
            capture_revision=capture.base_revision if has_capture else None,
            fact_revision=capture.fact_revision if has_capture else None,
            authority_revision=material.authority_revision if kind.startswith("PREPARED_") else None)

    all_players = tuple(world.players)
    player_projection = tuple(dict(player_id=p.player_id, display_name=p.display_name,
        alive=p.alive, death_or_null=_plain(p.death)) for p in all_players)
    world_root = root("WORLD_PLAYERS", player_projection)
    capture_root = root("CAPTURE_EVIDENCE", dict(evidence=_plain(capture.evidence),
        base_capture_id=capture.capture_id, base_revision=capture.base_revision,
        fact_revision=capture.fact_revision))
    state_root = root("DISCUSSION_STATE", state)
    action_root = root("PREPARED_ACTION", dict(action_bindings=tuple(
        _plain(b._projection()) for b in material.action_bindings),
        action_catalog_sha256=material.action_catalog_sha256,
        prepared_material_sha256=material.prepared_material_sha256,
        authority_snapshot_sha256=material.authority_snapshot_sha256))
    disclosure_root = root("PREPARED_DISCLOSURE", dict(disclosure_candidates=tuple(
        _plain(d._projection()) for d in material.disclosure_candidates),
        disclosure_catalog_sha256=material.disclosure_catalog_sha256,
        prepared_material_sha256=material.prepared_material_sha256))
    context_root = root("VALIDATED_CONTEXT", dict(context_sha256=capture.context_sha256,
        abilities=_plain(context.abilities)))
    require(0 < len(all_players) <= 32)
    require(len({p.player_id for p in all_players}) == len(all_players))
    require(sum(p.player_id == capture.player_id for p in all_players) == 1)
    players = []
    for i, (original_index, p) in enumerate(sorted(enumerate(all_players), key=lambda x: x[1].player_id)):
        players.append(dict(short_id=f"p{i:03d}", sorted_player_index=i,
            source_player_index=original_index, real_player_id=p.player_id,
            display_name=p.display_name, alive=p.alive,
            death_projection_or_null=_plain(p.death), world_root=world_root))
    player_map = {p["real_player_id"]: p["short_id"] for p in players}
    player_entries = {p["real_player_id"]: p for p in players}

    # Owner read/prepare validated every retained primary, parent and phase source.
    # An absent sidecar is distinct from a present malformed or ambiguous sidecar.
    def history_binding(ref):
        matches = tuple(s for s in inbound.ability_sidecars
            if s.identity == (ref.record_kind.value, ref.order))
        if not matches:
            return "ABSENT", None
        require(len(matches) == 1)
        sidecar = matches[0]
        records = tuple(r for r in inbound.ability_records if r.order == ref.order)
        require(ref.record_kind.value == "ability_result" and len(records) == 1)
        require(sidecar.subject_kind == "ABILITY_RESULT")
        require(sidecar.typed_value_sha256 == sha(records[0]))
        require(sidecar.source.connection_generation == material.connection_generation)
        require(sidecar.source.player_id == capture.player_id and sidecar.source.game_id == capture.game_id)
        require(sidecar.source.seq <= capture.last_applied_seq)
        for source_ref in (sidecar.source, sidecar.parent_source, sidecar.phase_source):
            if source_ref is not None:
                resolved = inbound.resolved_sources.get((source_ref.event_id, source_ref.source_path))
                require(resolved is not None and resolved.source_sha256 == source_ref.source_sha256)
        return "EXACT_ONE_MATCH", _plain(_subject_binding(sidecar))

    events = tuple(capture.evidence)
    require(len({e.source for e in events}) == len(events))
    start = max(0, len(events) - 48)
    facts = []
    for i, event in enumerate(events[start:]):
        outcome, binding = history_binding(event.source)
        facts.append(dict(short_id=f"f{i:03d}", capture_evidence_index=start+i,
            important_event_projection=_plain(event), important_event_sha256=sha(event),
            evidence_ref=_plain(event.source), provenance_lookup_outcome=outcome,
            source_ref_or_null=binding, capture_root=capture_root))
    replies = []
    if capture.trigger.kind == "PEER_CHAT":
        matched = [f for f in facts if f["evidence_ref"] == _plain(capture.trigger.source)]
        require(len(matched) == 1)
        fact = matched[0]
        replies.append(dict(short_id="r000", fact_short_id=fact["short_id"],
            **{key: fact[key] for key in ("capture_evidence_index", "evidence_ref",
               "provenance_lookup_outcome", "source_ref_or_null", "capture_root")}))
    claims = []
    require(len({c.claim for c in state.claims}) == len(state.claims))
    for i, claim in enumerate(state.claims[:32]):
        outcome, binding = history_binding(claim.claim)
        claims.append(dict(short_id=f"k{i:03d}", state_claim_index=i,
            claim_assessment_projection=_plain(claim), claim_assessment_sha256=sha(claim),
            claim_ref=_plain(claim.claim), provenance_lookup_outcome=outcome,
            source_ref_or_null=binding, state_root=state_root))
    opinions = []; opinion_entries = []
    require(len(state.assessments) <= 32)
    require(len({a.player_id for a in state.assessments}) == len(state.assessments))
    for i, assessment in enumerate(state.assessments):
        require(assessment.player_id in player_map)
        prior_facts = tuple(f["short_id"] for f in facts
            if any(_plain(e) == f["evidence_ref"] for e in assessment.evidence))
        for dimension, prior in (("SUSPICION", assessment.suspicion), ("CREDIBILITY", assessment.credibility)):
            require(type(prior) is int and prior in (0, 25, 50, 75, 100))
            current = tuple(score for score in (0, 25, 50, 75, 100) if score != prior)
            basis = OpinionBasisV2(f"u{len(opinions):03d}", player_map[assessment.player_id],
                dimension, prior, current, prior_facts)
            opinions.append(basis)
            opinion_entries.append(dict(basis_id=basis.basis_id, state_assessment_index=i,
                subject_player_short_id=basis.subject_player_id, dimension=dimension,
                prior=prior, allowed_current=current, prior_fact_ids=prior_facts,
                player_binding_sha256=sha(player_entries[assessment.player_id]),
                player_assessment_projection=_plain(assessment),
                player_assessment_sha256=sha(assessment), state_root=state_root))

    actions = tuple(inbound.current_actions_view.actions)
    require(len(actions) == len(material.action_bindings) <= 32)
    require(len(inbound.action_sidecars) == len(actions))
    vote = []; co = []; ability = []; roles = []; abilities = []; action_entries = []
    trigger = capture.trigger.kind
    chats = []
    def targets(action):
        require(len(set(action.valid_targets)) == len(action.valid_targets))
        require(all(t in player_map for t in action.valid_targets))
        return tuple(player_map[t] for t in action.valid_targets)

    for i, (action, binding) in enumerate(zip(actions, material.action_bindings)):
        option = f"o{i:03d}"
        require(binding.option_id == option and binding.action_sha256 == sha(action))
        require(binding.action_generation == material.action_generation == action.action_generation)
        require(binding.connection_generation == material.connection_generation == action.connection_generation)
        require(binding.phase_identity == material.phase_identity)
        matches = tuple(s for s in inbound.action_sidecars if s.identity == (action.action_generation, i))
        require(len(matches) == 1)
        sidecar = matches[0]
        require(sidecar.subject_kind == "ACTION_HANDLE" and sidecar.typed_value_sha256 == sha(action))
        require(_plain(binding.source) == _plain(_sidecar_projection(sidecar)))
        action_source = _plain(_subject_binding(sidecar))
        action_entries.append(dict(option_id=option, action_received_index=i,
            action_kind=binding.action_kind, action_generation=action.action_generation,
            connection_generation=action.connection_generation, phase_identity=binding.phase_identity,
            typed_action_sha256=sha(action), provenance_lookup_outcome="EXACT_ONE_MATCH",
            action_binding_projection=_plain(binding._projection()), action_source_binding=action_source,
            channel_authority_projection_or_null=None if binding.channel_authority is None else _plain(binding.channel_authority._projection()),
            prepared_action_root=action_root))
        if type(action) is ChatAction:
            chats.append(binding)
        elif type(action) is VoteAction:
            require(action.target_count == 1)
            target_ids = targets(action)
            if trigger == "PRE_VOTE":
                vote.append(VoteOptionV2(option, target_ids, action.allows_abstain))
        elif type(action) is CoDeclareAction:
            require(len(set(action.claimed_role_ids)) == len(action.claimed_role_ids))
            if trigger == "CO_OPPORTUNITY":
                role_ids = []
                for j, role in enumerate(action.claimed_role_ids):
                    role_id = f"q{len(roles):03d}"
                    role_ids.append(role_id)
                    roles.append(dict(role_short_id=role_id, option_id=option,
                        action_received_index=i, role_index=j, real_role_id=role,
                        typed_action_sha256=sha(action), provenance_lookup_outcome="EXACT_ONE_MATCH",
                        action_source_binding=action_source, prepared_action_root=action_root))
                require(len(roles) <= 32)
                co.append(CoOptionV2(option, tuple(role_ids)))
        elif type(action) is AbilityAction:
            matched = [(j, c) for j, c in enumerate(context.abilities) if c.ability_id == action.ability_id]
            require(len(matched) == 1)
            j, ability_context = matched[0]
            require(ability_context.target_count == action.target_count)
            require(ability_context.no_selection in ("skip", "random"))
            target_ids = targets(action)
            if trigger == "ABILITY":
                allows_none = ability_context.no_selection == "skip"
                ability.append(AbilityOptionV2(option, action.target_count, target_ids, allows_none))
                abilities.append(dict(option_id=option, action_received_index=i,
                    ability_context_index=j, real_ability_id=action.ability_id,
                    context_ability_projection=_plain(ability_context), context_ability_sha256=sha(ability_context),
                    typed_action_sha256=sha(action), target_count=action.target_count,
                    real_valid_target_ids=action.valid_targets, valid_target_short_ids=target_ids,
                    allows_none=allows_none, provenance_lookup_outcome="EXACT_ONE_MATCH",
                    action_source_binding=action_source, prepared_action_root=action_root, context_root=context_root))
        else:
            require(False)
    if trigger in ("INITIAL_CHAT", "PEER_CHAT"):
        require(len(chats) == 1 and type(chats[0].channel_authority) is PublicChannelAuthorityV2)
        require(any(chats[0].channel_authority is a for a in material.public_channel_authorities))
    elif trigger == "PRE_VOTE": require(bool(vote))
    elif trigger == "CO_OPPORTUNITY": require(bool(co))
    elif trigger == "ABILITY": require(bool(ability))
    else: require(False)

    disclosures = []
    require(len(material.disclosure_candidates) <= 16)
    for i, candidate in enumerate(material.disclosure_candidates):
        require(candidate.candidate_id == f"d{i:03d}")
        require(candidate.authority_sha256 == sha(candidate.authority._projection()))
        disclosures.append(dict(disclose_id=candidate.candidate_id, disclosure_index=i,
            candidate_projection=_plain(candidate._projection()),
            disclosure_authority_sha256=candidate.authority_sha256,
            provenance_lookup_outcome="EXACT_ONE_MATCH",
            authority_source_binding_projection=_plain(candidate.authority.source),
            prepared_disclosure_root=disclosure_root))
    catalog = GenerationCatalogV2(tuple(r["short_id"] for r in replies),
        tuple(p["short_id"] for p in players),
        tuple(p["short_id"] for p in players if p["real_player_id"] != capture.player_id),
        tuple(f["short_id"] for f in facts), tuple(d["disclose_id"] for d in disclosures), (),
        tuple(c["short_id"] for c in claims), tuple(opinions), tuple(vote), tuple(co), tuple(ability))
    def window(total, start, count, limit):
        return dict(source_count=total, selected_start=start, selected_count=count, limit=limit)
    catalog_binding = dict(schema_version="aiwolf.final-catalog-binding.v2",
        generation_catalog=_plain(catalog), player_window=window(len(players), 0, len(players), 32),
        fact_window=window(len(events), start, len(facts), 48),
        observed_claim_window=window(len(state.claims), 0, len(claims), 32),
        player_entries=tuple(players), reply_entries=tuple(replies), fact_entries=tuple(facts),
        observed_claim_entries=tuple(claims), utterance_claim_entries=(), opinion_entries=tuple(opinion_entries))
    option_binding = dict(schema_version="aiwolf.final-option-catalog.v2",
        action_catalog_sha256=material.action_catalog_sha256,
        disclosure_catalog_sha256=material.disclosure_catalog_sha256,
        all_action_bindings=tuple(action_entries),
        selected_vote_co_ability_options=dict(vote=_plain(vote), co=_plain(co), ability=_plain(ability)),
        role_id_bindings=tuple(roles), ability_id_bindings=tuple(abilities), disclosure_bindings=tuple(disclosures))
    return catalog, catalog_binding, option_binding


def _read_source_v2(owner):
    from .authority_capture_bridge_v2 import AuthorityCaptureBridgeError
    try:
        return owner.exact_composition.exact_authority_capture_bridge._read_final_capture_source_v2(
            owner.exact_prepared_material, owner.exact_hidden_owner_receipt)
    except AuthorityCaptureBridgeError:
        raise ReservedFinalizeError("FINALIZE_STALE_FRESHNESS") from None


def _build_reserved_candidate_v2(port, owner):
    composition = port._composition
    preparing = _owned_tuple(owner)[0]
    source1 = _read_source_v2(owner)
    catalog, catalog_binding, option_binding = _compile_catalog(source1)
    catalog_hash = canonical_sha256_v2(catalog_binding)
    option_hash = canonical_sha256_v2(option_binding)
    profile = composition.generation_profile_bundle_v2
    profile_hash = canonical_sha256_v2(profile)
    schemas = tuple((stage, build_generation_v2_schema(stage, catalog))
                    for stage in _stages(source1.exact_capture_read.capture.trigger.kind))
    stage_hashes = tuple(StageSchemaHashV2(stage, canonical_sha256_v2(schema))
                         for stage, schema in schemas)
    runtime_authorities = tuple(owner.exact_prepared_material.public_channel_authorities)
    candidates = tuple(StructuralPublicChannelCandidateV2(**dict(item._projection()))
                       for item in runtime_authorities)
    recipient_hash = recipient_proof_sha256_v2(
        source1.exact_capture_read.capture.trigger.kind, candidates)
    base = source1.exact_capture_read.capture
    input_material = {
        "schema_version": "aiwolf.generation-input-base.v2",
        "base_capture_id": base.capture_id, "game_id": base.game_id,
        "player_id": base.player_id, "context_sha256": base.context_sha256,
        "state_sha256": base.state_sha256, "base_revision": base.base_revision,
        "fact_revision": base.fact_revision, "world_version": base.world_version,
        "last_applied_sequence": base.last_applied_seq,
        "phase_identity": owner.exact_prepared_material.phase_identity,
        "action_generation": owner.exact_prepared_material.action_generation,
        "connection_generation": owner.exact_prepared_material.connection_generation,
        "trigger": _plain(base.trigger),
        "prepared_material_sha256": owner.exact_prepared_material.prepared_material_sha256,
        "catalog_sha256": catalog_hash, "option_catalog_sha256": option_hash,
        "recipient_proof_sha256": recipient_hash,
        "profile_bundle_sha256": profile_hash,
        "stage_schema_sha256s": _plain(stage_hashes),
    }
    input_hash = canonical_sha256_v2(input_material)
    capture = make_capture_v2(
        base_capture_id=base.capture_id, game_id=base.game_id, player_id=base.player_id,
        base_revision=base.base_revision, trigger=base.trigger,
        world_version=base.world_version, last_applied_sequence=base.last_applied_seq,
        fact_revision=base.fact_revision,
        phase_identity=owner.exact_prepared_material.phase_identity,
        action_generation=owner.exact_prepared_material.action_generation,
        connection_generation=owner.exact_prepared_material.connection_generation,
        channel_authorities=candidates, recipient_proof_sha256=recipient_hash,
        catalog_sha256=catalog_hash, option_catalog_sha256=option_hash,
        input_sha256=input_hash, profile_bundle_sha256=profile_hash,
        stage_schema_sha256s=stage_hashes, state_lease_id=preparing.state_lease_id,
        expires_at_monotonic_us=preparing.expires_at_monotonic_us)
    lease = replace(preparing, lease_revision=1, capture_id=capture.capture_id,
                    status="RESERVED", input_sha256=input_hash,
                    catalog_sha256=catalog_hash, option_catalog_sha256=option_hash,
                    recipient_proof_sha256=recipient_hash,
                    profile_bundle_sha256=profile_hash,
                    stage_schema_sha256s=stage_hashes)
    identity = ReservedBundleIdentityV2(_ISSUER)
    notification = _notification_slot(owner, "RESERVED_SUCCESS",
                                      owner.exact_initial_ticket.terminal_candidates.preparing_complete)
    postcheck = FinalizePostcheckObservationV2(_ISSUER,
        **{name: ("EMPTY" if name == "state" else None)
           for name in FinalizePostcheckObservationV2.__slots__})
    receipt = ReservedCaptureOwnerReceiptV2(
        _ISSUER, exact_preparing_receipt=owner, exact_source_read=source1,
        exact_capture=capture, exact_lease=lease, exact_profile_bundle=profile,
        exact_runtime_authorities=runtime_authorities,
        catalog_binding=_freeze(catalog_binding),
        option_catalog_binding=_freeze(option_binding), state="RESERVED",
        owner_identity=identity, notification_observation=notification,
        postcheck_observation=postcheck, stage_schemas=_freeze(schemas),
        exact_input_projection=_freeze(input_material))
    bundle = (lease, capture, receipt)
    reserved_cell = _issue_bundle_cell_v2(owner.exact_store, "RESERVED", identity, bundle)
    conflict = FinalizeConflictObservationV2(_ISSUER, purpose="RESERVED_ABORT",
        exact_composition=composition, expected_bundle_identity=identity,
        expected_cell_weakref=receipt.cell_weakref, expected_stage="RESERVED",
        observed_bundle_identity_or_null=None, observed_cell_weakref_or_null=None,
        observed_shape_or_null=None, state="EMPTY")
    invalid = replace(lease, status="INVALIDATED", lease_revision=2)
    invalid_owner = ReservedInvalidatedOwnerReceiptV2(_ISSUER, **_owner_fields(owner),
        schema_version="aiwolf.reserved-invalidated-owner-receipt.v2",
        predecessor_identity=identity, exact_capture=capture, exact_reserved_lease=lease,
        invalidated_lease=invalid, reserved_notification_observation=notification,
        postcheck_observation=postcheck, reason="RESERVED_RETIRE", state="ARMED", cell_identity=None, cell_weakref=None)
    invalid_cell = _issue_bundle_cell_v2(owner.exact_store, "RESERVED_INVALIDATED",
        ReservedInvalidatedBundleIdentityV2(_ISSUER), (invalid, capture, invalid_owner))
    abort = ReservedFinalizeAbortBundleV2(_ISSUER, predecessor_identity=identity,
        exact_capture=capture, invalidated_lease=invalid, invalidated_receipt=invalid_owner,
        conflict_observation=conflict, reserved_notification_observation=notification,
        postcheck_observation=postcheck, candidate_cell=invalid_cell, state="ARMED")
    object.__setattr__(receipt, "abort_bundle", abort)
    for name, value in zip(_POSTCHECK_EXPECTED, _postcheck_values(owner, bundle, expected=True)):
        object.__setattr__(postcheck, name, value)
    object.__setattr__(postcheck, "expected_bundle_identity", identity)
    object.__setattr__(postcheck, "expected_cell_weakref", receipt.cell_weakref)
    object.__setattr__(postcheck, "expected_stage", "RESERVED")
    source2 = _read_source_v2(owner)
    if source2.stable_fingerprint != source1.stable_fingerprint:
        raise ReservedFinalizeError("FINALIZE_STALE_FRESHNESS")
    return reserved_cell


def _validate_preparing_owner_v2(port, owner):
    if type(port) is not OfferPreparingCallerPortV2 or type(owner) is not PreparingLeaseOwnerReceiptV2:
        raise ReservedFinalizeError("FINALIZE_OWNER_MISMATCH")
    c = port._composition
    task = asyncio.current_task()
    pending = owner.exact_pending
    if (c is not owner.exact_composition or c.exact_caller_port is not port
            or c.lifecycle != "ACTIVE" or c.flow_state != "PREPARING"
            or c.initial_invocation_slot != "PREPARING_HELD"
            or c.active_initial_ticket_or_null is not owner.exact_initial_ticket
            or c.active_offer_source_or_null is not owner.exact_offer_source
            or c.initial_pending_or_null is not pending
            or task is not owner.exact_driver_task
            or c.exact_arbiter._admission_driver is not task
            or c.exact_arbiter._active is not pending or pending.result.done()
            or pending.dispatch_deadline is not owner.exact_dispatch_deadline
            or owner.exact_initial_ticket.state != "CONSUMED_PREPARING"
            or owner.exact_offer_source.state != "PREPARING"
            or owner.exact_offer_receipt.state != "CONSUMED_PREPARING"
            or owner.exact_offer_source.exact_offer_receipt_or_null is not owner.exact_offer_receipt
            or owner.exact_store._preparing_lease_bundle_v2 is not _owned_tuple(owner)
            or owner.exact_store._lease_bundle_cell_v2 is not owner.cell_weakref()
            or not _valid_cell_v2(owner.exact_store, owner.cell_weakref())
            or _owned_tuple(owner)[1] is not owner
            or _owned_tuple(owner)[0].status != "PREPARING"
            or _owned_tuple(owner)[0].lease_revision != 0):
        raise ReservedFinalizeError("FINALIZE_STALE_FRESHNESS")
    session = owner.exact_session
    lane = owner.exact_control_lane
    lease = owner.exact_client_lease
    invocation = _owned_tuple(owner)[0].state_lease_id
    if (c.exact_broker_session is not session or session._closed or session._transport_closed
            or session._control_lanes.get(invocation) is not lane
            or lane.lease is not lease or lane.disposition is not None or lane.callers != 0
            or lease._claimed or lease._released or lease._retired
            or session._claimed_invocation is not None or session._activated_invocation is not None
            or invocation in session._results
            or c.generation_profile_bundle_v2.generation_config_fingerprint != session.identity.config_fingerprint):
        raise ReservedFinalizeError("FINALIZE_STALE_FRESHNESS")
    store = owner.exact_store
    original = owner.exact_store_capture_tuple
    if (store._closed or store._current_capture is not original[0]
            or store._revision != original[1] or store._fact_revision != original[2]
            or store._epoch != original[3] or store._staged is not None
            or store._committed is not None or store._dispatch is not None or store._delivery is not None
            or store._observation is not None):
        raise ReservedFinalizeError("FINALIZE_STALE_FRESHNESS")
    deadline = c.exact_world.transport_observations().current_deadline
    expected = owner.exact_dispatch_deadline
    if (deadline is None or deadline.day != expected.day or deadline.phase != expected.phase
            or deadline.mapping_order != expected.mapping_order
            or deadline.action_generation != expected.action_generation
            or deadline.connection_generation != expected.connection_generation
            or deadline.local_deadline_monotonic is None
            or expected.not_after_monotonic > deadline.local_deadline_monotonic):
        raise ReservedFinalizeError("FINALIZE_STALE_FRESHNESS")
    if pending.cancel_requested or (task is not None and getattr(task, "cancelling", lambda: 0)()):
        raise ReservedFinalizeError("FINALIZE_CANCEL_REQUESTED")
    if math.ceil(c.exact_clock_callable() * 1_000_000) >= owner.expires_at_monotonic_us:
        raise ReservedFinalizeError("FINALIZE_DEADLINE_EXPIRED")


def _validate_reserved_candidate_v2(port, owner, cell):
    _validate_preparing_owner_v2(port, owner)
    if not _valid_cell_v2(owner.exact_store, cell) or cell.stage != "RESERVED":
        raise ReservedFinalizeError("FINALIZE_CELL_OWNER_MISMATCH")
    bundle = cell.exact_tuple
    lease, capture, receipt = bundle
    if (type(receipt) is not ReservedCaptureOwnerReceiptV2 or receipt.cell_weakref() is not cell
            or receipt.exact_preparing_receipt is not owner or receipt.exact_lease is not lease
            or receipt.exact_capture is not capture or lease.lease_revision != 1
            or lease.capture_id != capture.capture_id
            or receipt.exact_profile_bundle is not port._composition.generation_profile_bundle_v2
            or port.generation_profile_bundle_v2 is not receipt.exact_profile_bundle
            or receipt.abort_bundle.predecessor_identity is not receipt.owner_identity
            or receipt.abort_bundle.state != "ARMED"):
        raise ReservedFinalizeError("FINALIZE_CANDIDATE_MISMATCH")
    for name in ("input_sha256", "catalog_sha256", "option_catalog_sha256",
                 "recipient_proof_sha256", "profile_bundle_sha256", "stage_schema_sha256s"):
        if getattr(lease, name) != getattr(capture, name):
            raise ReservedFinalizeError("FINALIZE_CANDIDATE_MISMATCH")
    if (canonical_sha256_v2(receipt.catalog_binding) != capture.catalog_sha256
            or canonical_sha256_v2(receipt.option_catalog_binding) != capture.option_catalog_sha256
            or canonical_sha256_v2(receipt.exact_profile_bundle) != capture.profile_bundle_sha256
            or canonical_sha256_v2(receipt.exact_input_projection) != capture.input_sha256
            or tuple(StageSchemaHashV2(stage, canonical_sha256_v2(schema))
                     for stage, schema in receipt.stage_schemas) != capture.stage_schema_sha256s
            or tuple(stage for stage, _ in receipt.stage_schemas) != _stages(capture.trigger.kind)):
        raise ReservedFinalizeError("FINALIZE_CANDIDATE_MISMATCH")
    capture.__post_init__()
    lease.__post_init__()
    if len(receipt.exact_runtime_authorities) != len(capture.channel_authorities):
        raise ReservedFinalizeError("FINALIZE_CANDIDATE_MISMATCH")
    for runtime, projection, original in zip(receipt.exact_runtime_authorities,
            capture.channel_authorities, owner.exact_prepared_material.public_channel_authorities):
        if runtime is not original or _plain(projection) != dict(runtime._projection()):
            raise ReservedFinalizeError("FINALIZE_CANDIDATE_MISMATCH")


def _validate_abort_candidate_v2(store, expected_cell, owner, bundle):
    if (not _valid_cell_v2(store, expected_cell) or owner.cell_weakref() is not expected_cell
            or not _valid_cell_v2(store, bundle.candidate_cell)):
        raise ReservedFinalizeError("FINALIZE_ABORT_OWNER_MISMATCH")
    expected = expected_cell.exact_tuple
    preparing_owner = owner.exact_preparing_receipt if type(owner) is ReservedCaptureOwnerReceiptV2 else owner
    _validate_published_row_v2(preparing_owner.exact_composition, preparing_owner,
        selecting=type(owner) is PreparingLeaseOwnerReceiptV2 and bundle.abort_selection.state == "SELECTED_UNPUBLISHED")
    reserved = type(owner) is ReservedCaptureOwnerReceiptV2
    if not reserved and type(owner) is not PreparingLeaseOwnerReceiptV2:
        raise ReservedFinalizeError("FINALIZE_ABORT_OWNER_MISMATCH")
    preparing = owner.exact_preparing_receipt if reserved else owner
    identity = owner.owner_identity if reserved else owner.preparing_identity
    if (_owned_tuple(owner) is not expected or owner.abort_bundle is not bundle
            or preparing.exact_store is not store or bundle.state != "ARMED"
            or bundle.predecessor_identity is not identity
            or bundle.invalidated_receipt.predecessor_identity is not identity
            or bundle.invalidated_receipt.state != "ARMED"
            or bundle.candidate_cell.exact_tuple[0] is not bundle.invalidated_lease
            or bundle.candidate_cell.exact_tuple[-1] is not bundle.invalidated_receipt
            or bundle.invalidated_receipt.invalidated_lease is not bundle.invalidated_lease
            or bundle.invalidated_lease.status != "INVALIDATED"
            or bundle.invalidated_lease.lease_revision != (2 if reserved else 1)):
        raise ReservedFinalizeError("FINALIZE_ABORT_OWNER_MISMATCH")
    if reserved:
        if (type(bundle) is not ReservedFinalizeAbortBundleV2
                or bundle.exact_capture is not owner.exact_capture
                or bundle.candidate_cell.exact_tuple[1] is not owner.exact_capture
                or bundle.invalidated_receipt.exact_reserved_lease is not owner.exact_lease):
            raise ReservedFinalizeError("FINALIZE_ABORT_OWNER_MISMATCH")
    else:
        selection = bundle.abort_selection
        if (type(bundle) is not PreCaptureFinalizeAbortBundleV2
                or selection.exact_preparing_receipt_weakref_or_null() is not owner
                or selection.exact_abort_bundle_weakref_or_null() is not bundle
                or selection.exact_terminal_map is not bundle.terminal_map
                or selection.state not in {"BOUND_EMPTY", "SELECTED_UNPUBLISHED"}
                or (selection.state == "SELECTED_UNPUBLISHED"
                    and selection.exact_selected_result_or_null is not getattr(
                        bundle.terminal_map, selection.selected_class_or_null, None))):
            raise ReservedFinalizeError("FINALIZE_ABORT_OWNER_MISMATCH")
    original_lease = expected[0]
    for name in _LEASE_PRESERVED_FIELDS:
        if getattr(original_lease, name) != getattr(bundle.invalidated_lease, name):
            raise ReservedFinalizeError("FINALIZE_ABORT_OWNER_MISMATCH")
    for name in ("exact_composition", "exact_store", "exact_initial_ticket", "exact_pending",
                 "exact_driver_task", "exact_offer_source", "exact_offer_receipt", "exact_session",
                 "exact_control_lane", "exact_client_lease", "exact_request", "exact_dispatch_deadline"):
        if getattr(bundle.invalidated_receipt, name) is not getattr(preparing, name):
            raise ReservedFinalizeError("FINALIZE_ABORT_OWNER_MISMATCH")


def _postcheck_values(owner, bundle, *, expected=False):
    c = owner.exact_composition
    active = bundle if expected else owner.exact_store._preparing_lease_bundle_v2
    return (
        active[0], active[1], active[2], c,
        owner.exact_initial_ticket if expected else c.active_initial_ticket_or_null,
        owner.exact_offer_source if expected else c.active_offer_source_or_null,
        owner.exact_offer_receipt if expected else owner.exact_offer_source.exact_offer_receipt_or_null,
        owner.exact_pending if expected else c.exact_arbiter._active,
        owner.exact_driver_task if expected else c.exact_arbiter._admission_driver,
        owner.exact_session if expected else c.exact_broker_session,
        owner.exact_control_lane if expected else owner.exact_session._control_lanes.get(owner.exact_request.invocation_id),
        owner.exact_client_lease if expected else owner.exact_control_lane.lease,
        "RESERVED" if expected else c.flow_state,
        "RESERVED_HELD" if expected else c.initial_invocation_slot,
        "CONSUMED_RESERVED" if expected else owner.exact_initial_ticket.state,
        "RESERVED" if expected else owner.exact_offer_source.state,
        "CONSUMED_RESERVED" if expected else owner.exact_offer_receipt.state,
    )


def _postcheck_reserved_v2(owner, receipt):
    slot = receipt.postcheck_observation
    if slot.state != "EMPTY":
        raise ReservedFinalizeError("FINALIZE_POSTCHECK_SLOT_MISMATCH")
    for name in _POSTCHECK_OBSERVED:
        if getattr(slot, name) is not None:
            raise ReservedFinalizeError("FINALIZE_POSTCHECK_SLOT_MISMATCH")
    if slot.first_mismatch_or_null is not None:
        raise ReservedFinalizeError("FINALIZE_POSTCHECK_SLOT_MISMATCH")
    c = owner.exact_composition
    active = owner.exact_store._preparing_lease_bundle_v2
    # The slots were allocated before CAS. No observation tuple/dict is built here.
    active_cell = owner.exact_store._lease_bundle_cell_v2
    valid = _valid_cell_v2(owner.exact_store, active_cell)
    if valid:
        object.__setattr__(slot, "observed_bundle_identity_or_null", active_cell.bundle_identity)
        object.__setattr__(slot, "observed_cell_weakref_or_null", owner.exact_store._lease_bundle_cells_v2[active_cell.bundle_identity])
    object.__setattr__(slot, "observed_shape_or_null", active_cell.stage if valid else "FOREIGN")
    object.__setattr__(slot, "observed_lease", active[0] if type(active) is tuple and len(active) else None)
    object.__setattr__(slot, "observed_capture", active[1] if type(active) is tuple and len(active) == 3 else None)
    object.__setattr__(slot, "observed_receipt", active[-1] if type(active) is tuple and len(active) else None)
    object.__setattr__(slot, "observed_composition", owner.exact_store._offer_preparing_composition_v2)
    object.__setattr__(slot, "observed_ticket", c.active_initial_ticket_or_null)
    object.__setattr__(slot, "observed_source", c.active_offer_source_or_null)
    object.__setattr__(slot, "observed_offer_receipt", owner.exact_offer_source.exact_offer_receipt_or_null)
    object.__setattr__(slot, "observed_pending", c.exact_arbiter._active)
    object.__setattr__(slot, "observed_driver", c.exact_arbiter._admission_driver)
    object.__setattr__(slot, "observed_session", c.exact_broker_session)
    object.__setattr__(slot, "observed_lane", owner.exact_session._control_lanes.get(owner.exact_request.invocation_id))
    object.__setattr__(slot, "observed_client_lease", owner.exact_control_lane.lease)
    object.__setattr__(slot, "observed_composition_state", c.flow_state)
    object.__setattr__(slot, "observed_slot_state", c.initial_invocation_slot)
    object.__setattr__(slot, "observed_ticket_state", owner.exact_initial_ticket.state)
    object.__setattr__(slot, "observed_source_state", owner.exact_offer_source.state)
    object.__setattr__(slot, "observed_offer_receipt_state", owner.exact_offer_receipt.state)
    mismatch = None if (valid and slot.expected_cell_weakref() is active_cell
        and active_cell.bundle_identity is slot.expected_bundle_identity
        and active_cell.stage == slot.expected_stage) else "STORE_TUPLE"
    for index in range(len(_POSTCHECK_FIELDS)):
        expected = getattr(slot, _POSTCHECK_EXPECTED[index])
        observed = getattr(slot, _POSTCHECK_OBSERVED[index])
        different = observed is not expected if index < 12 else observed != expected
        if different and mismatch is None:
            mismatch = _POSTCHECK_CODES[index]
    if mismatch is not None:
        object.__setattr__(slot, "first_mismatch_or_null", mismatch)
        object.__setattr__(slot, "state", "RECORDED")
        c.flow_state = "RESERVED_POSTCHECK_UNKNOWN"
    else:
        for name in _POSTCHECK_OBSERVED:
            object.__setattr__(slot, name, None)
        object.__setattr__(slot, "observed_bundle_identity_or_null", None)
        object.__setattr__(slot, "observed_cell_weakref_or_null", None)
        object.__setattr__(slot, "observed_shape_or_null", None)


def _finalize_preparing_owned_v2(port, owner):
    if type(port) is not OfferPreparingCallerPortV2 or type(owner) is not PreparingLeaseOwnerReceiptV2:
        raise ReservedFinalizeError("FINALIZE_OWNER_MISMATCH")
    if (port._composition is owner.exact_composition and owner.exact_composition.lifecycle == "RETIRED"
            and owner.exact_store._lease_bundle_cell_v2 is owner.abort_bundle.candidate_cell
            and owner.abort_bundle.state == "PUBLISHED"):
        return None
    if port._composition is not owner.exact_composition or owner.abort_bundle.state != "ARMED":
        raise ReservedFinalizeError("FINALIZE_OWNER_MISMATCH")
    if asyncio.current_task() is not owner.exact_driver_task:
        raise ReservedFinalizeError("FINALIZE_OWNER_MISMATCH")
    c = owner.exact_composition
    try:
        _validate_preparing_owner_v2(port, owner)
        candidate_cell = _build_reserved_candidate_v2(port, owner)
        owner.exact_store._cas_reserved_lease_v2(c, _owned_cell(owner), candidate_cell)
        bundle = candidate_cell.exact_tuple
    except BaseException as error:
        try:
            expired = math.ceil(c.exact_clock_callable() * 1_000_000) >= owner.expires_at_monotonic_us
        except BaseException:
            expired = False
        if owner.exact_pending.cancel_requested or isinstance(error, asyncio.CancelledError):
            failure = "CANCEL_REQUESTED"
        elif expired:
            failure = "DEADLINE_EXPIRED"
        elif getattr(error, "code", "") in {
                "FINALIZE_STALE_FRESHNESS", "FINAL_SOURCE_STALE", "FINAL_SOURCE_OWNER_MISMATCH",
                "RESERVED_CAS_MISMATCH", "FINALIZE_OWNER_MISMATCH"}:
            failure = "STALE_FRESHNESS"
        else:
            failure = "INTERNAL_BUILD"
        _abort_preparing_owned_v2(owner, failure)
        return None
    lease, capture, receipt = bundle
    if (c.lifecycle == "RETIRED"
            and owner.exact_store._lease_bundle_cell_v2 is receipt.abort_bundle.candidate_cell
            and receipt.abort_bundle.state == "PUBLISHED"):
        return None
    object.__setattr__(owner.abort_bundle, "state", "RETIRED")
    object.__setattr__(owner.abort_bundle.invalidated_receipt, "state", "RETIRED")
    object.__setattr__(owner.abort_bundle.abort_selection, "state", "RETIRED_UNSELECTED")
    object.__setattr__(owner.exact_initial_ticket, "state", "CONSUMED_RESERVED")
    object.__setattr__(owner.exact_offer_source, "state", "RESERVED")
    object.__setattr__(owner.exact_offer_receipt, "state", "CONSUMED_RESERVED")
    c.flow_state = "RESERVED"
    c.initial_invocation_slot = "RESERVED_HELD"
    _postcheck_reserved_v2(owner, receipt)
    return bundle
