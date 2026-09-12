"""Bounded arbitration for the process-wide :class:`BrainController`."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import IntEnum
import math
import time
from typing import TYPE_CHECKING, Callable, Literal
from uuid import uuid4

from ai_client.network import (
    AbilityAction,
    ActionHandle,
    ChatAction,
    CoDeclareAction,
    CoReportAction,
    VoteAction,
)

from .controller import BrainController
from .model import (
    BrainDecision,
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


@dataclass(frozen=True)
class _ReplacementTransition:
    replacement: _PendingInvocation
    offer_task: asyncio.Task[AdmissionResult]


@dataclass(frozen=True)
class _AttachedSuccessor:
    pending: _PendingInvocation
    handle: SuccessorReservation


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

    async def stop(self) -> None:
        """Permanently stop new work, pending grants, and the owned controller."""

        if self._admission is None:
            await self._stop_direct()
            return
        async with self._lock:
            self._stopped = True
            pending = tuple(self._pending.values())
            self._pending.clear()
            for invocation in pending:
                self._finish(invocation, self._cancelled_result())
            if self._suspended_reaction is not None:
                suspended = self._suspended_reaction
                self._suspended_reaction = None
                suspended.cancel_requested = True
                self._finish(suspended, self._cancelled_result())
            if self._active is not None:
                self._active.cancel_requested = True
            if self._attached_successor is not None:
                self._attached_successor.pending.cancel_requested = True
            driver = self._admission_driver
            self._admission_changed.set()
        await self.controller.stop()
        if driver is not None and driver is not asyncio.current_task():
            await asyncio.gather(driver, return_exceptions=True)

    async def _invoke_direct(
        self, pending: _PendingInvocation
    ) -> BrainDispatchResult:
        async with self._lock:
            if self._stopped:
                raise RuntimeError("BrainInvocationArbiter is stopped")
            if pending.owner in self._pending:
                raise RuntimeError(
                    f"owner {pending.owner!r} already has a pending invocation"
                )
            self._pending[pending.owner] = pending
            self._grant_next_locked()

        try:
            return await asyncio.shield(pending.result)
        except asyncio.CancelledError:
            async with self._lock:
                if self._pending.get(pending.owner) is pending:
                    del self._pending[pending.owner]
                    pending.result.cancel()
                elif pending.result.done():
                    self._consume_future(pending.result)
                else:
                    pending.result.add_done_callback(self._consume_future)
            raise

    async def _stop_direct(self) -> None:
        async with self._lock:
            self._stopped = True
            pending = tuple(self._pending.values())
            self._pending.clear()
            active_task = self._active_task
            for invocation in pending:
                if not invocation.result.done():
                    invocation.result.set_result(self._cancelled_result())
        await self.controller.stop()
        if active_task is not None and active_task is not asyncio.current_task():
            await asyncio.gather(active_task, return_exceptions=True)

    def _grant_next_locked(self) -> None:
        if (
            self._admission is not None
            or self._stopped
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
        async with self._lock:
            if not pending.result.done():
                if error is None:
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

    async def _execute_direct(
        self, pending: _PendingInvocation
    ) -> BrainDispatchResult:
        remaining = pending.dispatch_deadline.not_after_monotonic - self._now()
        if remaining <= 0:
            return self._deadline_suppressed_result()
        request = self.controller.capture_input(
            allowed_handles=pending.allowed_handles
        )
        if request is None:
            return self._stale_result()
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
            return await asyncio.shield(pending.result)
        except asyncio.CancelledError:
            await self._cancel_admitted_caller(pending)
            raise

    async def _cancel_admitted_caller(self, pending: _PendingInvocation) -> None:
        must_wait = False
        async with self._lock:
            pending.cancel_requested = True
            if self._pending.get(pending.owner) is pending:
                del self._pending[pending.owner]
                self._finish(pending, self._cancelled_result())
            elif self._suspended_reaction is pending:
                self._suspended_reaction = None
                self._finish(pending, self._cancelled_result())
            else:
                must_wait = not pending.result.done()
            self._admission_changed.set()
        if must_wait:
            try:
                await asyncio.shield(pending.result)
            except (asyncio.CancelledError, Exception):
                pass

    async def _drive_admission(self) -> None:
        try:
            while True:
                async with self._lock:
                    if self._active is not None:
                        raise RuntimeError("admission driver retained an active invocation")
                    if not self._pending:
                        self._admission_state = "IDLE"
                        return
                    pending = self._select_pending_locked()
                    del self._pending[pending.owner]
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
                if self._pending and not self._stopped:
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

    async def _run_admitted_inner(
        self,
        pending: _PendingInvocation,
        *,
        offer_task: asyncio.Task[AdmissionResult] | None,
        successor_handle: SuccessorReservation | None,
    ) -> None:
        if pending.cancel_requested or self._stopped:
            if offer_task is not None:
                await self._cancel_offer_wait(
                    pending, offer_task, successor_handle=successor_handle
                )
            self._finish(pending, self._cancelled_result())
            return
        if not self._context_is_current(pending):
            self._finish(pending, self._current_context_terminal(pending))
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

        request = self.controller.capture_input(
            allowed_handles=pending.allowed_handles,
            dispatch_deadline=pending.dispatch_deadline,
        )
        if request is None:
            await self._cancel_offered(pending, lease)
            self._finish(pending, self._offered_context_terminal(pending))
            return

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
        release_error: Exception | None = None
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
        finally:
            self._admission_brain_task = None
            async with self._lock:
                self._admission_state = "RELEASING"
            try:
                await lease.release()
            except Exception as error:
                release_error = error
            self._admission_lease = None
        if execution_error is not None:
            self._finish_error(pending, execution_error)
        elif release_error is not None:
            self._finish(pending, self._admission_failure(release_error))
        else:
            assert result is not None
            self._finish(pending, result)
        await self._run_attached_successor()

    async def _run_replacement(
        self,
        suspended: _PendingInvocation,
        transition: _ReplacementTransition,
    ) -> None:
        replacement = transition.replacement
        async with self._lock:
            self._active = replacement
            self._admission_state = "WAITING_ADMISSION"
        await self._run_admitted(replacement, offer_task=transition.offer_task)
        async with self._lock:
            resume = self._suspended_reaction is suspended
            if resume:
                self._suspended_reaction = None
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
            if offer_task.done():
                return offer_task.result()

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
                elif brain_task.done():
                    self._admission_state = "RELEASING"
                    return brain_task.result()
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
