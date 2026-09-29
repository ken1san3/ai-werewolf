"""Bounded arbitration for the process-wide :class:`BrainController`."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import IntEnum
import math
import time
from typing import TYPE_CHECKING, Any, Callable, Literal
from uuid import uuid4

from ai_client.network import (
    AbilityAction,
    ActionHandle,
    ChatAction,
    CoDeclareAction,
    CoReportAction,
    VoteAction,
)
from ai_client.discussion.model import (
    DiscussionDispatchCorrelation,
    DiscussionObservationStatus,
    DiscussionTerminalReason,
    EvidenceRef,
    ObservationAck,
)

from .controller import BrainController
from .model import (
    BrainDecision,
    BrainInput,
    DecisionOutcome,
    DecisionStatus,
    DispatchDeadline,
)

if TYPE_CHECKING:
    from ai_client.llm.admission_types import (
        AdmissionRequest,
        AdmissionResult,
        AdmissionStatus,
        GenerationAdmission,
        GenerationLease,
        SuccessorReservation,
    )


BrainInvocationOwner = Literal["vote_ability", "reaction_chat"]


class BrainInvocationPriority(IntEnum):
    """Priority used only when choosing the next non-preemptive grant."""

    RESERVATION_ACTION = 0
    REACTION = 1


@dataclass(frozen=True)
class BrainDispatchResult:
    """One arbiter result with the exact decision used for a successful send."""

    outcome: DecisionOutcome
    dispatched_decision: BrainDecision | None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, DecisionOutcome):
            raise TypeError("outcome must be DecisionOutcome")
        if self.outcome.status is DecisionStatus.SENT:
            if self.outcome.receipt is None or self.dispatched_decision is None:
                raise ValueError("SENT requires a receipt and dispatched_decision")
        elif self.dispatched_decision is not None:
            raise ValueError("dispatched_decision is only valid for SENT")


@dataclass
class _PendingInvocation:
    owner: BrainInvocationOwner
    priority: BrainInvocationPriority
    allowed_handles: tuple[ActionHandle, ...]
    timeout_seconds: float
    dispatch_deadline: DispatchDeadline
    on_brain_start: Callable[[], None] | None
    result: asyncio.Future[BrainDispatchResult]
    admission_invocation_id: str | None = None
    cancel_requested: bool = False
    result_consumed: bool = False
    parked_terminal: BrainDispatchResult | None = None
    execution_complete: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(frozen=True)
class _ReplacementTransition:
    replacement: _PendingInvocation
    offer_task: asyncio.Task[AdmissionResult]


@dataclass(frozen=True)
class _AttachedSuccessor:
    pending: _PendingInvocation
    handle: SuccessorReservation


@dataclass
class _ObservationGate:
    owner: BrainInvocationOwner
    correlation: DiscussionDispatchCorrelation
    pending: _PendingInvocation
    attempt: tuple[
        DiscussionObservationStatus,
        DiscussionTerminalReason,
        EvidenceRef | None,
    ] | None = None
    task: asyncio.Task[ObservationAck] | None = None
    controller_was_unresponsive: bool | None = None


@dataclass(frozen=True)
class _CompletedObservation:
    owner: BrainInvocationOwner
    correlation: DiscussionDispatchCorrelation
    status: DiscussionObservationStatus
    reason: DiscussionTerminalReason
    evidence: EvidenceRef | None
    acknowledgement: ObservationAck


def _uuid4_string() -> str:
    return str(uuid4())


class BrainInvocationArbiter:
    """Serialize feature owners and optionally acquire one shared generation lease."""

    def __init__(
        self,
        *,
        controller: BrainController,
        clock: Callable[[], float] = time.monotonic,
        admission: GenerationAdmission | None = None,
        invocation_id_factory: Callable[[], str] = _uuid4_string,
    ) -> None:
        if not isinstance(controller, BrainController):
            raise TypeError("controller must be BrainController")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if admission is not None:
            from ai_client.llm.admission_types import GenerationAdmission

            if not isinstance(admission, GenerationAdmission):
                raise TypeError("admission must implement GenerationAdmission")
        if not callable(invocation_id_factory):
            raise TypeError("invocation_id_factory must be callable")
        self._offer_preparing_composition_v2 = None
        self._offer_preparing_mode_v2 = False
        self.controller = controller
        self._clock = clock
        self._admission = admission
        self._invocation_id_factory = invocation_id_factory
        self._lock = asyncio.Lock()
        self._pending: dict[BrainInvocationOwner, _PendingInvocation] = {}
        self._active: _PendingInvocation | None = None
        self._active_task: asyncio.Task[None] | None = None
        self._stopped = False

        # These values are used only by the admission-enabled path.  The direct
        # Phase 3/4 path below retains its original one-active/one-pending flow.
        self._admission_driver: asyncio.Task[None] | None = None
        self._admission_changed = asyncio.Event()
        self._admission_state = "IDLE"
        self._admission_wait_task: asyncio.Task[object] | None = None
        self._admission_brain_task: asyncio.Task[BrainDispatchResult] | None = None
        self._admission_lease: GenerationLease | None = None
        self._suspended_reaction: _PendingInvocation | None = None
        self._attached_successor: _AttachedSuccessor | None = None
        self._observation_gate: _ObservationGate | None = None
        self._completed_observation: _CompletedObservation | None = None
        self._poisoned = False
        self._fatal_error: BaseException | None = None
        self._poison_shutdown_task: asyncio.Task[None] | None = None
        self._controller_stop_task: asyncio.Task[None] | None = None
        self._admission_closed = False
        self._stop_task: asyncio.Task[None] | None = None

    async def invoke(
        self,
        *,
        owner: BrainInvocationOwner,
        priority: BrainInvocationPriority,
        allowed_handles: tuple[ActionHandle, ...],
        timeout_seconds: float,
        dispatch_deadline: DispatchDeadline,
        on_brain_start: Callable[[], None] | None = None,
    ) -> BrainDispatchResult:
        """Register one bounded request and await its exact terminal result."""

        if self._offer_preparing_mode_v2 or self._offer_preparing_composition_v2 is not None:
            from ai_client.discussion.offer_composition_v2 import OfferCompositionError
            raise OfferCompositionError("INITIAL_TICKET_NOT_CONNECTED")

        self._validate_call(
            owner=owner,
            priority=priority,
            allowed_handles=allowed_handles,
            timeout_seconds=timeout_seconds,
            dispatch_deadline=dispatch_deadline,
            on_brain_start=on_brain_start,
        )
        pending = _PendingInvocation(
            owner=owner,
            priority=priority,
            allowed_handles=allowed_handles,
            timeout_seconds=float(timeout_seconds),
            dispatch_deadline=dispatch_deadline,
            on_brain_start=on_brain_start,
            result=asyncio.get_running_loop().create_future(),
        )
        if self._admission is None:
            return await self._invoke_direct(pending)
        return await self._invoke_admitted(pending)

    async def _run_offer_preparing_offline_v2(
        self, pending: _PendingInvocation,
    ) -> BrainDispatchResult:
        """Private T525 driver; normal invoke and feature paths never call it."""
        from ai_client.discussion.offer_composition_v2 import (
            OfferCompositionError,
            _issue_initial_ticket_owned_v2,
            _owned_acquire_initial_offer_v2,
            _cleanup_offered_source_v2,
            _publish_preparing_from_offer_v2,
            _prepare_next_idle_generation_v2,
            _register_initial_offer_source_v2,
        )
        composition = self._offer_preparing_composition_v2
        if (not self._offer_preparing_mode_v2 or composition is None
                or composition.exact_arbiter is not self
                or type(pending) is not _PendingInvocation):
            raise OfferCompositionError("OFFER_PREPARING_DRIVER_MISMATCH")
        current = asyncio.current_task()
        _prepare_next_idle_generation_v2(composition.exact_caller_port)
        async with self._lock:
            if (current is None or self._admission_driver is not None
                    or self._active is not None or self._pending
                    or self._admission_state != "IDLE"):
                raise OfferCompositionError("OFFER_PREPARING_DRIVER_BUSY")
            self._active = pending
            self._admission_driver = current
            self._admission_state = "WAITING_ADMISSION"
            pending.execution_complete.clear()
        source = None
        try:
            ticket = await _issue_initial_ticket_owned_v2(
                composition.exact_caller_port, pending)
            if not hasattr(ticket, "exact_request"):
                self._finish(pending, ticket)
                return ticket
            source = _register_initial_offer_source_v2(
                composition.exact_caller_port, ticket)
            offer_task = asyncio.create_task(
                _owned_acquire_initial_offer_v2(source),
                name=f"aiwolf-v2-owned-offer-{ticket.exact_request.invocation_id}")
            object.__setattr__(ticket, "exact_offer_task_or_null", offer_task)
            self._admission_wait_task = offer_task
            self._admission_state = "V2_WAITING_OFFER"
            result, receipt = await offer_task
            self._admission_wait_task = None
            from ai_client.llm.admission_types import AdmissionStatus
            if result.status is not AdmissionStatus.OFFERED:
                terminal = ticket.terminal_candidates.admission_terminals[result.status]
                self._finish(pending, terminal)
                return terminal
            self._admission_state = "V2_PREPARING"
            _publish_preparing_from_offer_v2(
                composition.exact_caller_port, ticket, result, receipt)
            self._admission_state = "V2_PREPARING_HELD"
            terminal = ticket.terminal_candidates.preparing_complete
            try:
                self._finish(pending, terminal)
            except BaseException:
                object.__setattr__(source, "state", "TERMINAL_HELD")
                composition.flow_state = "PREPARING_TERMINAL_HELD"
                composition.initial_invocation_slot = "PREPARING_HELD"
                await asyncio.Future()
            return terminal
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                if composition.flow_state == "IDLE":
                    self._finish(pending, BrainInvocationArbiter._cancelled_result())
                raise
            if composition.flow_state == "OFFER_NOTIFICATION_UNKNOWN":
                # The wakeup shape is unknown.  Retain the exact active owner;
                # only existing runtime cancellation may end this wait.
                await asyncio.Future()
            if composition.flow_state == "PREPARING_TERMINAL_HELD":
                await asyncio.Future()
            code = error.code if type(error) is OfferCompositionError else None
            if composition.flow_state == "OFFER_CLEANUP_UNKNOWN":
                ticket = composition.active_initial_ticket_or_null
                terminal = (
                    ticket.terminal_candidates.cleanup_unknown
                    if ticket is not None else
                    composition.prebuilt_pre_ticket_abort_bundle.failure_result)
            elif code == "PREPARING_DEADLINE_EXPIRED":
                terminal = ticket.terminal_candidates.deadline_suppressed
            elif code in {
                "PREPARING_FRESHNESS_MISMATCH", "PREPARING_CAS_MISMATCH",
                "PREPARING_SESSION_MISMATCH", "PREPARING_OWNER_MISMATCH",
            }:
                terminal = ticket.terminal_candidates.stale
            else:
                terminal = composition.prebuilt_pre_ticket_abort_bundle.failure_result
            if (source is not None and getattr(source, "state", None) == "OFFERED"
                    and getattr(source, "exact_offer_receipt_or_null", None) is not None):
                terminal = await _cleanup_offered_source_v2(source, terminal)
            self._finish(pending, terminal)
            return terminal
        finally:
            async with self._lock:
                owner_held = composition.flow_state in {
                    "OFFER_NOTIFICATION_UNKNOWN", "PREPARING_TERMINAL_HELD",
                }
                if self._active is pending and not owner_held:
                    self._active = None
                if (not owner_held and self._admission_wait_task is not None
                        and self._admission_wait_task.done()):
                    self._admission_wait_task = None
                if not owner_held:
                    pending.execution_complete.set()
                if self._admission_driver is current and not owner_held:
                    self._admission_driver = None
                if composition.flow_state == "IDLE":
                    self._admission_state = "IDLE"
                elif composition.flow_state == "OFFER_CLEANUP_UNKNOWN":
                    self._admission_state = "V2_CLEANUP_UNKNOWN"
                elif composition.flow_state == "OFFER_NOTIFICATION_UNKNOWN":
                    self._admission_state = "V2_NOTIFICATION_UNKNOWN"
                elif composition.flow_state in {"PREPARING", "PREPARING_TERMINAL_HELD"}:
                    self._admission_state = "V2_PREPARING_HELD"

    async def stop(self) -> None:
        """Permanently stop new work, pending grants, and the owned controller."""

        if self._offer_preparing_composition_v2 is not None:
            from ai_client.discussion.offer_composition_v2 import _retire_offer_composition_v2
            _retire_offer_composition_v2(self)

        # There is deliberately no await between selecting and retaining this
        # task.  Event-loop execution therefore installs the sole stop owner
        # before caller cancellation can be delivered.
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(
                self._stop_owned(), name="aiwolf-brain-arbiter-stop"
            )
        task = self._stop_task
        await asyncio.shield(task)

    async def finalize_discussion_observation(
        self,
        *,
        owner: BrainInvocationOwner,
        correlation: DiscussionDispatchCorrelation,
        status: DiscussionObservationStatus,
        reason: DiscussionTerminalReason,
        evidence: EvidenceRef | None = None,
    ) -> ObservationAck:
        """Durably close the exact contextful local send owned by a feature."""

        self._validate_observation_call(
            owner=owner,
            correlation=correlation,
            status=status,
            reason=reason,
            evidence=evidence,
        )
        # Retain an Arbiter-owned wrapper before this public coroutine's first
        # await.  Cancellation can discard only the caller's wait, never the
        # sole gate selection or Controller delegate.
        owned = asyncio.create_task(
            self._finalize_discussion_observation_owned(
                owner=owner,
                correlation=correlation,
                status=status,
                reason=reason,
                evidence=evidence,
            ),
            name="aiwolf-discussion-observation-request",
        )
        owned.add_done_callback(self._consume_task)
        return await asyncio.shield(owned)

    async def _finalize_discussion_observation_owned(
        self,
        *,
        owner: BrainInvocationOwner,
        correlation: DiscussionDispatchCorrelation,
        status: DiscussionObservationStatus,
        reason: DiscussionTerminalReason,
        evidence: EvidenceRef | None,
    ) -> ObservationAck:
        key = (status, reason, evidence)
        while True:
            prior_conflict: asyncio.Task[ObservationAck] | None = None
            async with self._lock:
                if self._poisoned:
                    raise RuntimeError("BrainInvocationArbiter is poisoned")
                completed = self._completed_observation
                if completed is not None and completed.correlation == correlation:
                    if completed.owner != owner or (
                        completed.status,
                        completed.reason,
                        completed.evidence,
                    ) != key:
                        raise RuntimeError("conflicting discussion observation")
                    return completed.acknowledgement
                gate = self._observation_gate
                if (
                    gate is None
                    or gate.owner != owner
                    or gate.correlation != correlation
                ):
                    raise RuntimeError(
                        "discussion observation does not match the registered owner"
                    )
                if gate.attempt is not None and gate.attempt != key:
                    prior_conflict = gate.task
                    if prior_conflict is None:
                        raise RuntimeError(
                            "discussion observation task is unavailable"
                        )
                else:
                    task = self._select_finalization_locked(
                        gate,
                        status=status,
                        reason=reason,
                        evidence=evidence,
                    )
            if prior_conflict is None:
                return await asyncio.shield(task)
            # A differently keyed call that reached Controller validation first
            # may still prove to be a non-consuming precondition rejection.
            # Serialize behind it once; the next locked pass either selects the
            # now-empty gate or observes its completed/poisoned outcome.
            try:
                await asyncio.shield(prior_conflict)
            except BaseException:
                pass

    async def _invoke_direct(
        self, pending: _PendingInvocation
    ) -> BrainDispatchResult:
        async with self._lock:
            if self._poisoned:
                raise RuntimeError("BrainInvocationArbiter is poisoned")
            if self._stopped:
                raise RuntimeError("BrainInvocationArbiter is stopped")
            if pending.owner in self._pending or (
                self._observation_gate is not None
                and self._observation_gate.owner == pending.owner
            ):
                raise RuntimeError(
                    f"owner {pending.owner!r} already has a pending invocation"
                )
            self._pending[pending.owner] = pending
            self._grant_next_locked()

        try:
            result = await asyncio.shield(pending.result)
            # This is the exact no-await ownership-transfer boundary.  Merely
            # publishing the Future does not transfer a local-send correlation.
            pending.result_consumed = True
            return result
        except asyncio.CancelledError:
            cleanup = asyncio.create_task(
                self._cancel_direct_caller(pending),
                name="aiwolf-brain-direct-owner-loss",
            )
            await self._wait_task_ignoring_cancellation(cleanup)
            raise

    async def _cancel_direct_caller(self, pending: _PendingInvocation) -> None:
        async with self._lock:
            pending.cancel_requested = True
            if self._pending.get(pending.owner) is pending:
                del self._pending[pending.owner]
                self._finish(pending, self._cancelled_result())
                pending.execution_complete.set()
            self._admission_changed.set()
        await pending.execution_complete.wait()
        await self._recover_unconsumed_local_send(pending)

    def _grant_next_locked(self) -> None:
        if (
            self._admission is not None
            or self._stopped
            or self._poisoned
            or self._observation_gate is not None
            or self._active is not None
            or not self._pending
        ):
            return
        pending = self._select_pending_locked()
        del self._pending[pending.owner]
        self._active = pending
        self._active_task = asyncio.create_task(self._run_granted_direct(pending))

    async def _run_granted_direct(self, pending: _PendingInvocation) -> None:
        result: BrainDispatchResult | None = None
        error: BaseException | None = None
        try:
            result = await self._execute_direct(pending)
        except BaseException as caught:  # propagate the exact failure to this owner
            error = caught
        recover = False
        async with self._lock:
            if error is None and result is not None:
                self._register_observation_gate_locked(pending, result)
            if not pending.result.done():
                if pending.cancel_requested:
                    pending.result.set_result(self._cancelled_result())
                elif error is None:
                    assert result is not None
                    pending.result.set_result(result)
                elif isinstance(error, asyncio.CancelledError):
                    pending.result.cancel()
                else:
                    pending.result.set_exception(error)
            if self._active is pending:
                self._active = None
                self._active_task = None
            self._grant_next_locked()
            pending.execution_complete.set()
            recover = (
                pending.cancel_requested
                and not pending.result_consumed
                and self._gate_belongs_to_locked(pending)
            )
        if recover:
            await self._recover_unconsumed_local_send(pending)

    async def _execute_direct(
        self, pending: _PendingInvocation
    ) -> BrainDispatchResult:
        remaining = pending.dispatch_deadline.not_after_monotonic - self._now()
        if remaining <= 0:
            return self._deadline_suppressed_result()
        captured = await self._capture_with_one_catchup(pending)
        if isinstance(captured, BrainDispatchResult):
            return captured
        request = captured
        remaining = pending.dispatch_deadline.not_after_monotonic - self._now()
        if remaining <= 0:
            return self._deadline_suppressed_result()
        timeout = min(
            pending.timeout_seconds,
            remaining,
            self.controller.config.max_decision_seconds,
        )
        if timeout <= 0:
            return self._deadline_suppressed_result()
        if pending.on_brain_start is not None:
            pending.on_brain_start()
        return await self._controller_result(pending, request, timeout)

    async def _invoke_admitted(
        self, pending: _PendingInvocation
    ) -> BrainDispatchResult:
        async with self._lock:
            if self._poisoned:
                raise RuntimeError("BrainInvocationArbiter is poisoned")
            if self._stopped:
                raise RuntimeError("BrainInvocationArbiter is stopped")
            if self._owner_is_occupied_locked(pending.owner):
                raise RuntimeError(
                    f"owner {pending.owner!r} already has a pending invocation"
                )
            self._pending[pending.owner] = pending
            if self._admission_driver is None or self._admission_driver.done():
                self._admission_driver = asyncio.create_task(
                    self._drive_admission(), name="aiwolf-brain-admission"
                )
            self._admission_changed.set()
        try:
            result = await asyncio.shield(pending.result)
            # See the direct path: this assignment is intentionally adjacent
            # to the shield return and has no intervening await.
            pending.result_consumed = True
            return result
        except asyncio.CancelledError:
            cleanup = asyncio.create_task(
                self._cancel_admitted_caller(pending),
                name="aiwolf-brain-admitted-owner-loss",
            )
            await self._wait_task_ignoring_cancellation(cleanup)
            raise

    async def _cancel_admitted_caller(self, pending: _PendingInvocation) -> None:
        async with self._lock:
            pending.cancel_requested = True
            if self._pending.get(pending.owner) is pending:
                del self._pending[pending.owner]
                self._finish(pending, self._cancelled_result())
                pending.execution_complete.set()
            elif self._suspended_reaction is pending:
                self._suspended_reaction = None
                self._finish(pending, self._cancelled_result())
                pending.execution_complete.set()
            self._admission_changed.set()
        await pending.execution_complete.wait()
        await self._recover_unconsumed_local_send(pending)

    async def _drive_admission(self) -> None:
        try:
            while True:
                async with self._lock:
                    if self._active is not None:
                        raise RuntimeError("admission driver retained an active invocation")
                    if self._poisoned or self._stopped:
                        self._admission_state = "IDLE"
                        return
                    if self._observation_gate is not None:
                        self._admission_state = "OBSERVATION_GATED"
                        return
                    if not self._pending:
                        self._admission_state = "IDLE"
                        return
                    pending = self._select_pending_locked()
                    del self._pending[pending.owner]
                    pending.execution_complete.clear()
                    self._active = pending
                    self._admission_state = "WAITING_ADMISSION"
                    self._admission_changed.clear()
                await self._run_admitted(pending)
        finally:
            async with self._lock:
                if self._admission_driver is asyncio.current_task():
                    self._admission_driver = None
                if self._active is None:
                    self._admission_state = "IDLE"
                if (
                    self._pending
                    and not self._stopped
                    and not self._poisoned
                    and self._observation_gate is None
                ):
                    self._admission_driver = asyncio.create_task(
                        self._drive_admission(), name="aiwolf-brain-admission"
                    )

    async def _run_admitted(
        self,
        pending: _PendingInvocation,
        *,
        offer_task: asyncio.Task[AdmissionResult] | None = None,
        successor_handle: SuccessorReservation | None = None,
    ) -> None:
        try:
            await self._run_admitted_inner(
                pending,
                offer_task=offer_task,
                successor_handle=successor_handle,
            )
        except asyncio.CancelledError:
            self._finish(pending, self._cancelled_result())
        except BaseException as error:
            self._finish_error(pending, error)
        finally:
            async with self._lock:
                if self._active is pending:
                    self._active = None
                if self._admission_wait_task is offer_task:
                    self._admission_wait_task = None
                pending.execution_complete.set()

    async def _run_admitted_inner(
        self,
        pending: _PendingInvocation,
        *,
        offer_task: asyncio.Task[AdmissionResult] | None,
        successor_handle: SuccessorReservation | None,
    ) -> None:
        if pending.parked_terminal is not None:
            terminal = pending.parked_terminal
            pending.parked_terminal = None
            self._finish(pending, terminal)
            return
        if pending.cancel_requested or self._stopped:
            if offer_task is not None:
                await self._cancel_offer_wait(
                    pending, offer_task, successor_handle=successor_handle
                )
            self._finish(pending, self._cancelled_result())
            return
        initial_capture = await self._await_readiness_with_one_catchup(
            pending, allow_replacement=True
        )
        if isinstance(initial_capture, _ReplacementTransition):
            await self._run_replacement(pending, initial_capture)
            return
        if isinstance(initial_capture, BrainDispatchResult):
            self._finish(pending, initial_capture)
            return

        if offer_task is None:
            request = self._new_admission_request(pending)
            assert self._admission is not None
            offer_task = asyncio.create_task(
                self._admission.acquire(request),
                name=f"aiwolf-admission-acquire-{request.invocation_id}",
            )
        self._admission_wait_task = offer_task
        try:
            offered = await self._await_offer(
                pending,
                offer_task,
                successor_handle=successor_handle,
            )
        except Exception as error:
            self._finish(pending, self._admission_failure(error))
            return
        self._admission_wait_task = None
        if isinstance(offered, _ReplacementTransition):
            await self._run_replacement(pending, offered)
            return
        if isinstance(offered, BrainDispatchResult):
            self._finish(pending, offered)
            return
        from ai_client.llm.admission_types import AdmissionResult, AdmissionStatus

        if not isinstance(offered, AdmissionResult):
            raise RuntimeError("admission returned an invalid result")
        if offered.status is not AdmissionStatus.OFFERED:
            self._finish(pending, self._admission_terminal(offered.status))
            return
        lease = offered.lease
        if lease is None or lease.invocation_id != pending.admission_invocation_id:
            raise RuntimeError("admission offer has an invalid lease")

        captured = await self._capture_with_one_catchup(
            pending, allow_replacement=True
        )
        if isinstance(captured, _ReplacementTransition):
            await self._run_replacement(pending, captured)
            return
        if isinstance(captured, BrainDispatchResult):
            await self._cancel_offered(pending, lease)
            terminal = (
                self._offered_context_terminal(pending)
                if captured.outcome.status is DecisionStatus.STALE
                else captured
            )
            self._finish(pending, terminal)
            return
        request = captured

        transition = await self._replace_before_claim_if_needed(pending)
        if transition is not None:
            await self._run_replacement(pending, transition)
            return

        async with self._lock:
            self._admission_state = "CLAIMING"
        claim = asyncio.create_task(
            lease.claim(),
            name=f"aiwolf-admission-claim-{pending.admission_invocation_id}",
        )
        self._admission_wait_task = claim
        try:
            claim_result = await self._await_claim(pending, claim)
        except Exception as error:
            self._finish(pending, self._admission_failure(error))
            return
        self._admission_wait_task = None
        if isinstance(claim_result, BrainDispatchResult):
            # A completed GRANTED task cannot be cancelled. Retain its cleanup
            # ownership even when the waiter returns a stale/cancel terminal.
            if (
                claim.done() and not claim.cancelled() and claim.exception() is None
                and claim.result() is AdmissionStatus.GRANTED
            ):
                self._admission_lease = lease
                await lease.release()
                self._admission_lease = None
            self._finish(pending, claim_result)
            return
        if claim_result is not AdmissionStatus.GRANTED:
            self._finish(pending, self._admission_terminal(claim_result))
            return

        self._admission_lease = lease
        if pending.cancel_requested or self._stopped or not self._context_is_current(pending):
            terminal = (
                self._cancelled_result()
                if pending.cancel_requested or self._stopped
                else self._current_context_terminal(pending)
            )
            await lease.release()
            self._admission_lease = None
            self._finish(pending, terminal)
            return

        await self._attach_pending_reservation(pending)
        result: BrainDispatchResult | None = None
        execution_error: BaseException | None = None
        release_error: BaseException | None = None
        try:
            with lease.activate():
                brain_task = asyncio.create_task(
                    self._start_controller(pending, request),
                    name=f"aiwolf-brain-{pending.admission_invocation_id}",
                )
                self._admission_brain_task = brain_task
                result = await self._monitor_active_brain(pending, brain_task)
        except BaseException as error:
            execution_error = error
        self._admission_brain_task = None

        gate_registered = False
        attached_for_gate: _AttachedSuccessor | None = None
        suspended_for_gate: _PendingInvocation | None = None
        async with self._lock:
            gate_registered = self._gate_belongs_to_locked(pending)
            self._admission_state = "RELEASING"
            if gate_registered:
                # The handle is removed from the runnable topology before its
                # one cancellation, but remains broker-attached until that
                # acknowledgement.  Parent release therefore cannot promote it.
                attached_for_gate = self._attached_successor
                self._attached_successor = None
                if self._suspended_reaction is not None:
                    suspended_for_gate = self._suspended_reaction
                    self._suspended_reaction = None

        successor_status: AdmissionStatus | None = None
        successor_error: BaseException | None = None
        if attached_for_gate is not None:
            try:
                successor_status = await attached_for_gate.handle.cancel()
                if successor_status not in {
                    AdmissionStatus.CANCELLED,
                    AdmissionStatus.EXPIRED,
                }:
                    raise RuntimeError(
                        "successor cancellation returned an invalid terminal status"
                    )
            except BaseException as error:
                successor_error = error
        try:
            await lease.release()
        except BaseException as error:
            release_error = error
        self._admission_lease = None

        if gate_registered and (
            successor_error is not None or release_error is not None
        ):
            await self._poison_after_post_send_cleanup_failure(
                pending,
                attached=attached_for_gate,
                suspended=suspended_for_gate,
                failure=(
                    successor_error
                    if successor_error is not None
                    else release_error
                ),
            )
            return

        if gate_registered:
            await self._park_gate_cleanup_waiters(
                attached=attached_for_gate,
                successor_status=successor_status,
                suspended=suspended_for_gate,
            )
            if execution_error is not None:
                # A registered local send is the result; replacing it with a
                # correlation-less error would strand terminal ownership.
                await self._poison_after_post_send_cleanup_failure(
                    pending,
                    attached=None,
                    suspended=None,
                    failure=execution_error,
                )
                return
            assert result is not None
            if pending.cancel_requested or self._stopped:
                self._finish(pending, self._cancelled_result())
                await self._recover_unconsumed_local_send(pending)
            else:
                self._finish(pending, result)
            return

        if execution_error is not None:
            self._finish_error(pending, execution_error)
        elif release_error is not None:
            self._finish(pending, self._admission_failure(release_error))
        else:
            assert result is not None
            self._finish(pending, result)
        await self._run_attached_successor()

    async def _await_readiness_with_one_catchup(
        self,
        pending: _PendingInvocation,
        *,
        allow_replacement: bool = False,
    ) -> BrainDispatchResult | _ReplacementTransition | None:
        result = await self._capture_with_one_catchup(
            pending, allow_replacement=allow_replacement, readiness_only=True
        )
        assert not isinstance(result, BrainInput)
        return result

    async def _capture_with_one_catchup(
        self,
        pending: _PendingInvocation,
        *,
        allow_replacement: bool = False,
        readiness_only: bool = False,
    ) -> BrainInput | BrainDispatchResult | _ReplacementTransition | None:
        if pending.cancel_requested or self._stopped:
            return self._cancelled_result()
        remaining = pending.dispatch_deadline.not_after_monotonic - self._now()
        if remaining <= 0:
            return self._deadline_suppressed_result()
        capture = (self.controller.capture_readiness if readiness_only
                   else self.controller.capture_input_attempt)
        attempt = capture(
            allowed_handles=pending.allowed_handles,
            dispatch_deadline=pending.dispatch_deadline,
        )
        if attempt.status == "READY":
            return None
        if attempt.status == "CAPTURED":
            assert attempt.request is not None
            return attempt.request
        if attempt.status != "NETWORK_AHEAD":
            return self._current_context_terminal(pending)

        world_task = asyncio.create_task(
            self.controller.world.wait_for_update(attempt.after_world_version),
            name="aiwolf-brain-capture-catchup-world",
        )
        changed_task = asyncio.create_task(
            self._admission_changed.wait(),
            name="aiwolf-brain-capture-catchup-state",
        )
        try:
            done, _ = await asyncio.wait(
                {world_task, changed_task},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            await self._cancel_watchers(world_task, changed_task)
        if not done or self._now() >= pending.dispatch_deadline.not_after_monotonic:
            return self._deadline_suppressed_result()

        async with self._lock:
            poisoned = self._poisoned
            fatal = self._fatal_error
            cancelled = pending.cancel_requested or self._stopped
            active = self._active is pending
            self._admission_changed.clear()
        if poisoned:
            if fatal is not None:
                raise fatal
            raise RuntimeError("BrainInvocationArbiter is poisoned")
        if cancelled:
            return self._cancelled_result()
        if not active:
            return self._stale_result()
        if allow_replacement:
            transition = await self._replace_before_claim_if_needed(pending)
            if transition is not None:
                return transition
        if world_task not in done:
            return self._current_context_terminal(pending)
        try:
            world_task.result()
        except Exception:
            return self._stale_result()
        second = capture(
            allowed_handles=pending.allowed_handles,
            dispatch_deadline=pending.dispatch_deadline,
        )
        if second.status == "READY":
            return None
        if second.status == "CAPTURED":
            assert second.request is not None
            return second.request
        return self._current_context_terminal(pending)

    async def _run_replacement(
        self,
        suspended: _PendingInvocation,
        transition: _ReplacementTransition,
    ) -> None:
        replacement = transition.replacement
        async with self._lock:
            replacement.execution_complete.clear()
            self._active = replacement
            self._admission_state = "WAITING_ADMISSION"
        await self._run_admitted(replacement, offer_task=transition.offer_task)
        async with self._lock:
            resume = self._suspended_reaction is suspended
            if resume:
                self._suspended_reaction = None
                if self._observation_gate is not None:
                    suspended.admission_invocation_id = None
                    self._pending[suspended.owner] = suspended
                    self._active = None
                    self._admission_state = "OBSERVATION_GATED"
                    resume = False
                else:
                    suspended.execution_complete.clear()
                    self._active = suspended
                    self._admission_state = "WAITING_ADMISSION"
        if not resume:
            return
        if suspended.cancel_requested or self._stopped:
            self._finish(suspended, self._cancelled_result())
            return
        await self._run_admitted(suspended)

    async def _await_offer(
        self,
        pending: _PendingInvocation,
        offer_task: asyncio.Task[AdmissionResult],
        *,
        successor_handle: SuccessorReservation | None,
    ) -> AdmissionResult | BrainDispatchResult | _ReplacementTransition:
        after_version = self.controller.world.snapshot().version
        while True:
            if pending.cancel_requested or self._stopped:
                await self._cancel_offer_wait(
                    pending, offer_task, successor_handle=successor_handle
                )
                return self._cancelled_result()
            if successor_handle is None:
                transition = await self._replace_before_claim_if_needed(pending)
                if transition is not None:
                    offer_task.add_done_callback(self._consume_task)
                    return transition
            if offer_task.done():
                return offer_task.result()
            if not self._context_is_current(pending):
                offered_before_claim = self._completed_as_offer(offer_task)
                await self._cancel_offer_wait(
                    pending, offer_task, successor_handle=successor_handle
                )
                return (
                    self._offered_context_terminal(pending)
                    if offered_before_claim
                    else self._current_context_terminal(pending)
                )
            world_task = asyncio.create_task(
                self.controller.world.wait_for_update(after_version)
            )
            changed_task = asyncio.create_task(self._admission_changed.wait())
            done, _ = await asyncio.wait(
                {offer_task, world_task, changed_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            await self._cancel_watchers(world_task, changed_task)
            if world_task in done:
                try:
                    after_version = world_task.result().version
                except Exception:
                    await self._cancel_offer_wait(
                        pending, offer_task, successor_handle=successor_handle
                    )
                    return self._stale_result()
            if changed_task in done:
                async with self._lock:
                    self._admission_changed.clear()

    async def _await_claim(
        self,
        pending: _PendingInvocation,
        claim_task: asyncio.Task[AdmissionStatus],
    ) -> AdmissionStatus | BrainDispatchResult:
        after_version = self.controller.world.snapshot().version
        while True:
            if pending.cancel_requested or self._stopped:
                claim_task.cancel()
                await asyncio.gather(claim_task, return_exceptions=True)
                return self._cancelled_result()
            if not self._context_is_current(pending):
                claim_task.cancel()
                await asyncio.gather(claim_task, return_exceptions=True)
                return self._current_context_terminal(pending)
            if claim_task.done():
                return claim_task.result()
            world_task = asyncio.create_task(
                self.controller.world.wait_for_update(after_version)
            )
            changed_task = asyncio.create_task(self._admission_changed.wait())
            done, _ = await asyncio.wait(
                {claim_task, world_task, changed_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            await self._cancel_watchers(world_task, changed_task)
            if world_task in done:
                try:
                    after_version = world_task.result().version
                except Exception:
                    claim_task.cancel()
                    await asyncio.gather(claim_task, return_exceptions=True)
                    return self._stale_result()
            if changed_task in done:
                async with self._lock:
                    self._admission_changed.clear()

    async def _monitor_active_brain(
        self,
        pending: _PendingInvocation,
        brain_task: asyncio.Task[BrainDispatchResult],
    ) -> BrainDispatchResult:
        while True:
            to_attach: _PendingInvocation | None = None
            cancel_attached = False
            async with self._lock:
                if brain_task.done():
                    self._admission_state = "RELEASING"
                    result = brain_task.result()
                    self._register_observation_gate_locked(pending, result)
                    return result
                if (
                    pending.owner == "reaction_chat"
                    and self._attached_successor is None
                    and "vote_ability" in self._pending
                ):
                    to_attach = self._pending.pop("vote_ability")
                elif (
                    self._attached_successor is not None
                    and (
                        self._attached_successor.pending.cancel_requested
                        or self._stopped
                    )
                ):
                    cancel_attached = True
                cancel_brain = pending.cancel_requested or self._stopped
                self._admission_changed.clear()
            if to_attach is not None:
                await self._attach_reservation(pending, to_attach)
                continue
            if cancel_attached:
                await self._cancel_attached_successor()
                continue
            if cancel_brain:
                brain_task.cancel()
                return await brain_task
            changed_task = asyncio.create_task(self._admission_changed.wait())
            done, _ = await asyncio.wait(
                {brain_task, changed_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if changed_task not in done:
                changed_task.cancel()
                await asyncio.gather(changed_task, return_exceptions=True)

    async def _attach_pending_reservation(
        self, active: _PendingInvocation
    ) -> None:
        if active.owner != "reaction_chat":
            return
        async with self._lock:
            pending = self._pending.pop("vote_ability", None)
        if pending is not None:
            await self._attach_reservation(active, pending)

    async def _attach_reservation(
        self,
        active: _PendingInvocation,
        reservation: _PendingInvocation,
    ) -> None:
        if reservation.cancel_requested or self._stopped:
            self._finish(reservation, self._cancelled_result())
            return
        if not self._context_is_current(reservation):
            self._finish(reservation, self._current_context_terminal(reservation))
            return
        active_id = active.admission_invocation_id
        if active_id is None:
            raise RuntimeError("active admission invocation is unavailable")
        request = self._new_admission_request(reservation)
        assert self._admission is not None
        try:
            handle = await self._admission.reserve_successor(active_id, request)
        except Exception as error:
            self._finish(reservation, self._admission_failure(error))
            return
        if handle.invocation_id != request.invocation_id:
            self._finish_error(
                reservation, RuntimeError("successor handle identity mismatch")
            )
            return
        self._attached_successor = _AttachedSuccessor(reservation, handle)
        if reservation.cancel_requested or self._stopped:
            await self._cancel_attached_successor()

    async def _run_attached_successor(self) -> None:
        attached = self._attached_successor
        self._attached_successor = None
        if attached is None:
            return
        pending = attached.pending
        if pending.cancel_requested or self._stopped:
            try:
                await attached.handle.cancel()
            except Exception:
                pass
            self._finish(pending, self._cancelled_result())
            return
        async with self._lock:
            pending.execution_complete.clear()
            self._active = pending
            self._admission_state = "WAITING_ADMISSION"
        offer_task = asyncio.create_task(
            attached.handle.wait_offer(),
            name=f"aiwolf-admission-successor-{attached.handle.invocation_id}",
        )
        await self._run_admitted(
            pending,
            offer_task=offer_task,
            successor_handle=attached.handle,
        )

    async def _cancel_attached_successor(self) -> None:
        attached = self._attached_successor
        if attached is None:
            return
        self._attached_successor = None
        try:
            await attached.handle.cancel()
        except Exception:
            pass
        self._finish(attached.pending, self._cancelled_result())
        attached.pending.execution_complete.set()

    async def _replace_before_claim_if_needed(
        self, pending: _PendingInvocation
    ) -> _ReplacementTransition | None:
        if pending.owner != "reaction_chat" or self._admission_state != "WAITING_ADMISSION":
            return None
        async with self._lock:
            replacement = self._pending.pop("vote_ability", None)
            if replacement is None:
                return None
            if replacement.cancel_requested or self._stopped:
                self._finish(replacement, self._cancelled_result())
                return None
            self._suspended_reaction = pending
            self._active = replacement
            self._admission_changed.clear()
        request = self._new_admission_request(replacement)
        old_id = pending.admission_invocation_id
        if old_id is None:
            raise RuntimeError("waiting admission identity is unavailable")
        assert self._admission is not None
        task = asyncio.create_task(
            self._admission.replace_waiting(old_id, request),
            name=f"aiwolf-admission-replace-{request.invocation_id}",
        )
        return _ReplacementTransition(replacement, task)

    async def _cancel_offer_wait(
        self,
        pending: _PendingInvocation,
        offer_task: asyncio.Task[AdmissionResult],
        *,
        successor_handle: SuccessorReservation | None,
    ) -> None:
        if successor_handle is not None:
            if not offer_task.done():
                offer_task.cancel()
                await asyncio.gather(offer_task, return_exceptions=True)
            try:
                await successor_handle.cancel()
            except Exception:
                pass
            return
        if not offer_task.done():
            offer_task.cancel()
            await asyncio.gather(offer_task, return_exceptions=True)
            return
        try:
            offered = offer_task.result()
        except (asyncio.CancelledError, Exception):
            return
        from ai_client.llm.admission_types import AdmissionStatus

        if offered.status is AdmissionStatus.OFFERED:
            assert offered.lease is not None
            await self._cancel_offered(pending, offered.lease)

    @staticmethod
    def _completed_as_offer(offer_task: asyncio.Task[AdmissionResult]) -> bool:
        if not offer_task.done() or offer_task.cancelled():
            return False
        try:
            offered = offer_task.result()
        except Exception:
            return False
        from ai_client.llm.admission_types import AdmissionResult, AdmissionStatus

        return (
            isinstance(offered, AdmissionResult)
            and offered.status is AdmissionStatus.OFFERED
        )

    async def _cancel_offered(
        self,
        pending: _PendingInvocation,
        lease: GenerationLease,
    ) -> None:
        invocation_id = pending.admission_invocation_id
        if invocation_id is None:
            return
        assert self._admission is not None
        from ai_client.llm.admission_types import AdmissionStatus

        status = await self._admission.cancel(invocation_id)
        if not isinstance(status, AdmissionStatus):
            raise RuntimeError("admission cancel returned an invalid status")
        if status is AdmissionStatus.GRANTED:
            await lease.release()

    async def _start_controller(
        self,
        pending: _PendingInvocation,
        request: object,
    ) -> BrainDispatchResult:
        if not self._context_is_current(pending):
            return self._current_context_terminal(pending)
        remaining = pending.dispatch_deadline.not_after_monotonic - self._now()
        if remaining <= 0:
            return self._deadline_suppressed_result()
        timeout = min(
            pending.timeout_seconds,
            remaining,
            self.controller.config.max_decision_seconds,
        )
        if timeout <= 0:
            return self._deadline_suppressed_result()
        if pending.on_brain_start is not None:
            pending.on_brain_start()
        if self._admission is not None:
            self._admission_state = "ACTIVE_BRAIN"
        return await self._controller_result(pending, request, timeout)

    async def _controller_result(
        self,
        pending: _PendingInvocation,
        request: object,
        timeout: float,
    ) -> BrainDispatchResult:
        outcome = await self.controller.decide_and_send(
            request,  # type: ignore[arg-type]
            timeout_seconds=timeout,
            dispatch_deadline=pending.dispatch_deadline,
        )
        if outcome.status is not DecisionStatus.SENT:
            return BrainDispatchResult(outcome, None)
        decision = self.controller.take_dispatched_decision(outcome.receipt)
        if decision is None:
            raise RuntimeError("SENT dispatch decision is unavailable")
        return BrainDispatchResult(outcome, decision)

    def _register_observation_gate_locked(
        self,
        pending: _PendingInvocation,
        result: BrainDispatchResult,
    ) -> None:
        correlation = result.outcome.discussion
        if result.outcome.status is not DecisionStatus.SENT:
            if correlation is not None:
                raise RuntimeError("non-SENT result cannot register discussion observation")
            return
        if correlation is None:
            return
        expected_owner = (
            "reaction_chat"
            if correlation.action in {"chat", "co_declare"}
            else "vote_ability"
        )
        if pending.owner != expected_owner:
            raise RuntimeError("discussion correlation action does not match owner")
        if self._observation_gate is not None:
            raise RuntimeError("a discussion observation gate is already registered")
        self._completed_observation = None
        self._observation_gate = _ObservationGate(
            owner=pending.owner,
            correlation=correlation,
            pending=pending,
        )
        same_owner_waiter = self._pending.pop(pending.owner, None)
        if same_owner_waiter is not None:
            self._finish_error(
                same_owner_waiter,
                RuntimeError(
                    f"owner {pending.owner!r} cannot wait behind its observation gate"
                ),
            )
            same_owner_waiter.execution_complete.set()
        self._admission_state = "OBSERVATION_GATED"

    def _gate_belongs_to_locked(self, pending: _PendingInvocation) -> bool:
        gate = self._observation_gate
        return gate is not None and gate.pending is pending

    def _select_finalization_locked(
        self,
        gate: _ObservationGate,
        *,
        status: DiscussionObservationStatus,
        reason: DiscussionTerminalReason,
        evidence: EvidenceRef | None,
    ) -> asyncio.Task[ObservationAck]:
        key = (status, reason, evidence)
        if gate.attempt is None:
            gate.attempt = key
            gate.controller_was_unresponsive = self.controller.unresponsive
            gate.task = asyncio.create_task(
                self._run_observation_finalization(
                    gate,
                    status=status,
                    reason=reason,
                    evidence=evidence,
                ),
                name="aiwolf-discussion-observation-finalize",
            )
        elif gate.attempt != key:
            raise RuntimeError("conflicting discussion observation")
        if gate.task is None:  # pragma: no cover - state-machine defense
            raise RuntimeError("discussion observation task is unavailable")
        return gate.task

    async def _run_observation_finalization(
        self,
        gate: _ObservationGate,
        *,
        status: DiscussionObservationStatus,
        reason: DiscussionTerminalReason,
        evidence: EvidenceRef | None,
    ) -> ObservationAck:
        try:
            acknowledgement = await self.controller.finalize_discussion_observation(
                correlation=gate.correlation,
                status=status,
                reason=reason,
                evidence=evidence,
            )
        except BaseException as error:
            async with self._lock:
                if (
                    gate.controller_was_unresponsive is False
                    and not self.controller.unresponsive
                ):
                    # Controller validation and retained-material checks occur
                    # before its attempt/state/audit boundary.  Restore only
                    # this provisional Arbiter selection so a corrected fact
                    # can use the still-unconsumed public seam.
                    if (
                        self._observation_gate is gate
                        and gate.task is asyncio.current_task()
                    ):
                        gate.attempt = None
                        gate.task = None
                        gate.controller_was_unresponsive = None
                else:
                    if self._fatal_error is None:
                        self._fatal_error = error
                    self._mark_poisoned_locked()
                    self._ensure_poison_shutdown_locked(asyncio.current_task())
            raise

        async with self._lock:
            if self._observation_gate is not gate:
                raise RuntimeError("discussion observation gate ownership changed")
            self._completed_observation = _CompletedObservation(
                owner=gate.owner,
                correlation=gate.correlation,
                status=status,
                reason=reason,
                evidence=evidence,
                acknowledgement=acknowledgement,
            )
            self._observation_gate = None
            self._admission_state = "IDLE"
            if not self._poisoned and not self._stopped:
                self._resume_after_observation_locked()
            self._admission_changed.set()
        return acknowledgement

    async def _recover_unconsumed_local_send(
        self, pending: _PendingInvocation
    ) -> None:
        owned = asyncio.create_task(
            self._recover_unconsumed_local_send_owned(pending),
            name="aiwolf-discussion-owner-loss-recovery",
        )
        try:
            await self._wait_task_ignoring_cancellation(owned)
        except BaseException:
            # The finalizer itself records poison and owns shutdown.  The
            # cancelled feature has no second terminal or useful result path.
            return

    async def _recover_unconsumed_local_send_owned(
        self, pending: _PendingInvocation
    ) -> None:
        async with self._lock:
            gate = self._observation_gate
            if (
                gate is None
                or gate.pending is not pending
                or pending.result_consumed
            ):
                return
            task = self._select_finalization_locked(
                gate,
                status=DiscussionObservationStatus.RECOVERY_UNKNOWN,
                reason=DiscussionTerminalReason.OWNER_STOPPED,
                evidence=None,
            )
        await self._wait_task_ignoring_cancellation(task)

    async def _park_gate_cleanup_waiters(
        self,
        *,
        attached: _AttachedSuccessor | None,
        successor_status: AdmissionStatus | None,
        suspended: _PendingInvocation | None,
    ) -> None:
        from ai_client.llm.admission_types import AdmissionStatus

        async with self._lock:
            for item, terminal in (
                (
                    None if attached is None else attached.pending,
                    (
                        self._deadline_suppressed_result()
                        if successor_status is AdmissionStatus.EXPIRED
                        else None
                    ),
                ),
                (suspended, None),
            ):
                if item is None:
                    continue
                item.admission_invocation_id = None
                if item.cancel_requested or self._stopped:
                    self._finish(item, self._cancelled_result())
                    item.execution_complete.set()
                    continue
                item.parked_terminal = terminal
                if item.owner in self._pending:
                    raise RuntimeError("gate parking found duplicate owner")
                self._pending[item.owner] = item
            self._admission_changed.set()

    def _resume_after_observation_locked(self) -> None:
        for owner, pending in tuple(self._pending.items()):
            if pending.cancel_requested:
                del self._pending[owner]
                self._finish(pending, self._cancelled_result())
                pending.execution_complete.set()
            elif pending.parked_terminal is not None:
                del self._pending[owner]
                terminal = pending.parked_terminal
                pending.parked_terminal = None
                self._finish(pending, terminal)
                pending.execution_complete.set()
        if self._admission is None:
            self._grant_next_locked()
        elif self._pending and (
            self._admission_driver is None or self._admission_driver.done()
        ):
            self._admission_driver = asyncio.create_task(
                self._drive_admission(), name="aiwolf-brain-admission"
            )

    async def _poison_after_post_send_cleanup_failure(
        self,
        pending: _PendingInvocation,
        *,
        attached: _AttachedSuccessor | None,
        suspended: _PendingInvocation | None,
        failure: BaseException | None,
    ) -> None:
        async with self._lock:
            gate = self._observation_gate
            if gate is None or gate.pending is not pending:
                raise RuntimeError("post-send cleanup lost its observation gate")
            terminal_task = self._select_finalization_locked(
                gate,
                status=DiscussionObservationStatus.RECOVERY_UNKNOWN,
                reason=DiscussionTerminalReason.OWNER_STOPPED,
                evidence=None,
            )
            if failure is not None and self._fatal_error is None:
                self._fatal_error = failure
            self._mark_poisoned_locked(
                additional=tuple(
                    item
                    for item in (
                        None if attached is None else attached.pending,
                        suspended,
                    )
                    if item is not None
                )
            )
            shutdown = self._ensure_poison_shutdown_locked(terminal_task)
        await asyncio.shield(shutdown)

    def _mark_poisoned_locked(
        self, *, additional: tuple[_PendingInvocation, ...] = ()
    ) -> None:
        self._poisoned = True
        error = RuntimeError("BrainInvocationArbiter is poisoned")
        invocations: list[_PendingInvocation] = list(self._pending.values())
        self._pending.clear()
        if self._suspended_reaction is not None:
            invocations.append(self._suspended_reaction)
            self._suspended_reaction = None
        if self._attached_successor is not None:
            invocations.append(self._attached_successor.pending)
            self._attached_successor = None
        invocations.extend(additional)
        if self._active is not None:
            invocations.append(self._active)
        seen: set[int] = set()
        for pending in invocations:
            identity = id(pending)
            if identity in seen:
                continue
            seen.add(identity)
            pending.cancel_requested = True
            self._finish_error(pending, error)
            pending.execution_complete.set()
        self._admission_changed.set()

    def _ensure_poison_shutdown_locked(
        self, terminal_task: asyncio.Task[ObservationAck] | None
    ) -> asyncio.Task[None]:
        if self._poison_shutdown_task is None:
            self._poison_shutdown_task = asyncio.create_task(
                self._poison_shutdown(terminal_task),
                name="aiwolf-brain-arbiter-poison-shutdown",
            )
        return self._poison_shutdown_task

    async def _poison_shutdown(
        self, terminal_task: asyncio.Task[ObservationAck] | None
    ) -> None:
        if terminal_task is not None:
            try:
                await self._wait_task_ignoring_cancellation(terminal_task)
            except BaseException:
                pass
        if self._admission is not None:
            should_close = False
            async with self._lock:
                if not self._admission_closed:
                    self._admission_closed = True
                    should_close = True
            if should_close:
                try:
                    await self._admission.aclose()
                except BaseException as error:
                    async with self._lock:
                        if self._fatal_error is None:
                            self._fatal_error = error
        try:
            await self._ensure_controller_stopped()
        except BaseException as error:
            async with self._lock:
                if self._fatal_error is None:
                    self._fatal_error = error

    async def _stop_owned(self) -> None:
        async with self._lock:
            self._stopped = True
            pending = tuple(self._pending.values())
            self._pending.clear()
            for invocation in pending:
                invocation.cancel_requested = True
                self._finish(invocation, self._cancelled_result())
                invocation.execution_complete.set()
            if self._suspended_reaction is not None:
                suspended = self._suspended_reaction
                self._suspended_reaction = None
                suspended.cancel_requested = True
                self._finish(suspended, self._cancelled_result())
                suspended.execution_complete.set()
            if self._active is not None:
                self._active.cancel_requested = True
            if self._attached_successor is not None:
                self._attached_successor.pending.cancel_requested = True
            active_task = self._active_task
            driver = self._admission_driver
            poison_shutdown = self._poison_shutdown_task
            self._admission_changed.set()

        if poison_shutdown is not None:
            await asyncio.shield(poison_shutdown)
        for task in (active_task, driver):
            if task is not None and task is not asyncio.current_task():
                await asyncio.gather(task, return_exceptions=True)
        if poison_shutdown is not None:
            if self._fatal_error is not None:
                raise self._fatal_error
            return

        finalization_error: BaseException | None = None
        while True:
            async with self._lock:
                gate = self._observation_gate
                if gate is None:
                    finalization = None
                elif gate.task is None:
                    finalization = self._select_finalization_locked(
                        gate,
                        status=DiscussionObservationStatus.RECOVERY_UNKNOWN,
                        reason=DiscussionTerminalReason.OWNER_STOPPED,
                        evidence=None,
                    )
                else:
                    finalization = gate.task
            if finalization is None:
                break
            try:
                await self._wait_task_ignoring_cancellation(finalization)
            except BaseException as error:
                async with self._lock:
                    retry_after_precondition = (
                        not self._poisoned
                        and self._observation_gate is gate
                        and gate.task is None
                        and gate.attempt is None
                    )
                if retry_after_precondition:
                    continue
                finalization_error = error
            break
        async with self._lock:
            late_poison_shutdown = self._poison_shutdown_task
        if late_poison_shutdown is not None:
            await asyncio.shield(late_poison_shutdown)
        else:
            await self._ensure_controller_stopped()
        if finalization_error is not None:
            raise finalization_error
        if self._fatal_error is not None:
            raise self._fatal_error

    async def _ensure_controller_stopped(self) -> None:
        async with self._lock:
            if self._controller_stop_task is None:
                self._controller_stop_task = asyncio.create_task(
                    self.controller.stop(), name="aiwolf-brain-controller-stop"
                )
            task = self._controller_stop_task
        await asyncio.shield(task)

    @staticmethod
    async def _wait_event_ignoring_cancellation(event: asyncio.Event) -> None:
        task = asyncio.create_task(event.wait())
        await BrainInvocationArbiter._wait_task_ignoring_cancellation(task)

    @staticmethod
    async def _wait_task_ignoring_cancellation(task: asyncio.Task[Any]) -> Any:
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                if task.done():
                    return task.result()

    @staticmethod
    def _validate_observation_call(
        *,
        owner: object,
        correlation: object,
        status: object,
        reason: object,
        evidence: object,
    ) -> None:
        if owner not in {"reaction_chat", "vote_ability"}:
            raise ValueError("owner must be 'vote_ability' or 'reaction_chat'")
        if not isinstance(correlation, DiscussionDispatchCorrelation):
            raise TypeError("correlation must be DiscussionDispatchCorrelation")
        if not isinstance(status, DiscussionObservationStatus):
            raise TypeError("status must be DiscussionObservationStatus")
        if not isinstance(reason, DiscussionTerminalReason):
            raise TypeError("reason must be DiscussionTerminalReason")
        if evidence is not None and not isinstance(evidence, EvidenceRef):
            raise TypeError("evidence must be EvidenceRef when supplied")
        expected_owner = (
            "reaction_chat"
            if correlation.action in {"chat", "co_declare"}
            else "vote_ability"
        )
        if owner != expected_owner:
            raise ValueError("owner does not match discussion correlation action")

    def _new_admission_request(
        self, pending: _PendingInvocation
    ) -> AdmissionRequest:
        from ai_client.llm.admission_types import AdmissionRequest, GenerationPriority

        invocation_id = self._invocation_id_factory()
        request = AdmissionRequest(
            invocation_id=invocation_id,
            priority=(
                GenerationPriority.RESERVATION
                if pending.owner == "vote_ability"
                else GenerationPriority.REACTION
            ),
            phase=pending.dispatch_deadline.phase,
            day=pending.dispatch_deadline.day,
            action_generation=pending.dispatch_deadline.action_generation,
            mapping_order=pending.dispatch_deadline.mapping_order,
            not_after_monotonic=pending.dispatch_deadline.not_after_monotonic,
        )
        pending.admission_invocation_id = request.invocation_id
        return request

    def _context_is_current(self, pending: _PendingInvocation) -> bool:
        return self.controller.dispatch_context_is_current(
            allowed_handles=pending.allowed_handles,
            dispatch_deadline=pending.dispatch_deadline,
        )

    def _current_context_terminal(
        self, pending: _PendingInvocation
    ) -> BrainDispatchResult:
        if self._now() >= pending.dispatch_deadline.not_after_monotonic:
            return self._deadline_suppressed_result()
        return self._stale_result()

    def _offered_context_terminal(
        self, pending: _PendingInvocation
    ) -> BrainDispatchResult:
        deadline = pending.dispatch_deadline
        current = self.controller.world.transport_observations().current_deadline
        if (
            current is None
            or current.mapping_order != deadline.mapping_order
            or current.phase != deadline.phase
            or current.day != deadline.day
            or current.connection_generation != deadline.connection_generation
            or current.action_generation != deadline.action_generation
            or current.local_deadline_monotonic is None
            or self._now() >= deadline.not_after_monotonic
        ):
            return self._deadline_suppressed_result()
        return self._stale_result()

    @staticmethod
    async def _cancel_watchers(*tasks: asyncio.Task[object]) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _owner_is_occupied_locked(self, owner: BrainInvocationOwner) -> bool:
        return (
            owner in self._pending
            or (self._active is not None and self._active.owner == owner)
            or (
                self._observation_gate is not None
                and self._observation_gate.owner == owner
            )
            or (
                self._suspended_reaction is not None
                and self._suspended_reaction.owner == owner
            )
            or (
                self._attached_successor is not None
                and self._attached_successor.pending.owner == owner
            )
        )

    def _select_pending_locked(self) -> _PendingInvocation:
        return min(
            self._pending.values(),
            key=lambda invocation: (
                invocation.priority,
                0 if invocation.owner == "vote_ability" else 1,
            ),
        )

    @staticmethod
    def _admission_terminal(status: AdmissionStatus) -> BrainDispatchResult:
        from ai_client.llm.admission_types import AdmissionStatus

        if status is AdmissionStatus.EXPIRED:
            return BrainInvocationArbiter._deadline_suppressed_result()
        if status is AdmissionStatus.CANCELLED:
            return BrainInvocationArbiter._cancelled_result()
        if status is AdmissionStatus.REPLACED:
            return BrainInvocationArbiter._stale_result()
        if status in {
            AdmissionStatus.OVERLOADED,
            AdmissionStatus.UNAVAILABLE,
            AdmissionStatus.POISONED,
        }:
            return BrainDispatchResult(
                DecisionOutcome(
                    status=DecisionStatus.BRAIN_FAILED,
                    error_type=f"Admission{status.value.title()}",
                ),
                None,
            )
        raise RuntimeError(f"unexpected admission terminal {status.value}")

    @staticmethod
    def _admission_failure(error: Exception) -> BrainDispatchResult:
        """Convert a session/IPC failure without exposing its message or payload."""

        return BrainDispatchResult(
            DecisionOutcome(
                status=DecisionStatus.BRAIN_FAILED,
                error_type=type(error).__name__,
            ),
            None,
        )

    @staticmethod
    def _finish(
        pending: _PendingInvocation, result: BrainDispatchResult
    ) -> None:
        if not pending.result.done():
            pending.result.set_result(result)

    @staticmethod
    def _finish_error(pending: _PendingInvocation, error: BaseException) -> None:
        if pending.result.done():
            return
        if isinstance(error, asyncio.CancelledError):
            pending.result.set_result(BrainInvocationArbiter._cancelled_result())
        else:
            pending.result.set_exception(error)

    def _validate_call(
        self,
        *,
        owner: object,
        priority: object,
        allowed_handles: object,
        timeout_seconds: object,
        dispatch_deadline: object,
        on_brain_start: object,
    ) -> None:
        expected_priority = {
            "vote_ability": BrainInvocationPriority.RESERVATION_ACTION,
            "reaction_chat": BrainInvocationPriority.REACTION,
        }
        if owner not in expected_priority:
            raise ValueError("owner must be 'vote_ability' or 'reaction_chat'")
        if not isinstance(priority, BrainInvocationPriority):
            raise TypeError("priority must be BrainInvocationPriority")
        if priority is not expected_priority[owner]:
            raise ValueError("priority does not match owner")
        if (
            not isinstance(allowed_handles, tuple)
            or not allowed_handles
            or any(not isinstance(handle, ActionHandle) for handle in allowed_handles)
            or len(set(allowed_handles)) != len(allowed_handles)
        ):
            raise ValueError("allowed_handles must be a non-empty unique ActionHandle tuple")
        if owner == "vote_ability":
            valid_family = all(
                isinstance(handle, (VoteAction, AbilityAction))
                for handle in allowed_handles
            )
        else:
            valid_family = all(
                isinstance(handle, (ChatAction, CoDeclareAction, CoReportAction))
                for handle in allowed_handles
            )
        if not valid_family:
            raise ValueError("allowed_handles do not match owner")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        if not isinstance(dispatch_deadline, DispatchDeadline):
            raise TypeError("dispatch_deadline must be DispatchDeadline")
        if on_brain_start is not None and not callable(on_brain_start):
            raise TypeError("on_brain_start must be callable when supplied")

    def _now(self) -> float:
        value = self._clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("clock must return a finite non-negative number")
        return float(value)

    @staticmethod
    def _deadline_suppressed_result() -> BrainDispatchResult:
        return BrainDispatchResult(
            DecisionOutcome(status=DecisionStatus.DEADLINE_SUPPRESSED),
            None,
        )

    @staticmethod
    def _stale_result() -> BrainDispatchResult:
        return BrainDispatchResult(
            DecisionOutcome(status=DecisionStatus.STALE),
            None,
        )

    @staticmethod
    def _cancelled_result() -> BrainDispatchResult:
        return BrainDispatchResult(
            DecisionOutcome(status=DecisionStatus.CANCELLED),
            None,
        )

    @staticmethod
    def _consume_future(future: asyncio.Future[BrainDispatchResult]) -> None:
        if future.cancelled():
            return
        try:
            future.exception()
        except Exception:
            pass

    @staticmethod
    def _consume_task(task: asyncio.Future[object]) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except Exception:
            pass
