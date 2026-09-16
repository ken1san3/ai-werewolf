from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import datetime, timezone
import time
from typing import Any, Callable
import unittest
from dataclasses import fields
from ai_client.llm.types import AiDiscussionGenerationRecordV2


def test_http_error_detail_does_not_enter_brain_generation_v2() -> None:
    assert "backend_error_detail" not in {field.name for field in fields(AiDiscussionGenerationRecordV2)}

from ai_client.brain import (
    BrainDispatchResult,
    BrainController,
    BrainInvocationArbiter,
    BrainInvocationPriority,
    BrainRunConfig,
    ChatDecision,
    DecisionOutcome,
    DecisionStatus,
    DispatchDeadline,
    VoteDecision,
)
from ai_client.discussion.model import (
    DiscussionDispatchCorrelation,
    DiscussionObservationStatus,
    DiscussionTerminalReason,
    EvidenceRecordKind,
    EvidenceRef,
    EvidenceVisibility,
    ObservationAck,
)
from ai_client.llm.admission_types import (
    AdmissionCredentials,
    AdmissionRequest,
    AdmissionResult,
    AdmissionStatus,
    GenerationBrokerConfig,
    GenerationPriority,
)
from ai_client.llm.admission_broker import GenerationAdmissionBroker
from ai_client.llm.admission_client import (
    BrokerAdmissionSession,
    BrokeredStructuredLLMBackend,
)
from ai_client.llm import (
    AuditWriteAck,
    BackendIdentity,
    LLMBrain,
    LLMClientIdentity,
    LLMUsage,
    StructuredGenerationResponse,
)
from ai_client.network import ChatAction, SendReceipt, VoteAction
from ai_client.network import PhaseTimingMapped
from ai_client.reaction_chat.frequency import SpeakingProfile
from ai_client.world import (
    ActionAcceptedObservation,
    AbilityResultView,
    CoView,
    CurrentActionsView,
    CurrentPhaseDeadline,
    Freshness,
    HistoryView,
    PhaseView,
    PlayerView,
    ResumeRecoveryBarrier,
    SelfView,
    TransportObservationView,
    WorldSnapshot,
)
from tests.test_phase5_speaking_frequency import (
    _event as _frequency_event,
    _make_stack as _make_frequency_stack,
    _sync_payload as _frequency_sync_payload,
    _wait_until as _wait_for_frequency,
)
from tests.test_phase5_generation_admission import _FakeBackend, _token


class _World:
    def __init__(self, actions: tuple[object, ...]) -> None:
        self._snapshot = WorldSnapshot(
            version=1,
            freshness=Freshness.CURRENT,
            is_caught_up=True,
            last_applied_seq=10,
            phase=PhaseView(phase="day", day=1, phase_ends_at=100),
            players=(PlayerView("p1", "Self"), PlayerView("p2", "Peer")),
            alive_player_ids=("p1", "p2"),
            self_view=SelfView("p1", "role:villager", ()),
        )
        self._actions = CurrentActionsView(
            world_version=1,
            world_last_applied_seq=10,
            network_last_seq=10,
            is_caught_up=True,
            actions=actions,
        )
        self.deadline = CurrentPhaseDeadline(1, "day", 1, 1, 1, 100.0)
        self._update = asyncio.Event()

    def snapshot(self) -> WorldSnapshot:
        return self._snapshot

    def current_actions(self) -> CurrentActionsView:
        return self._actions

    def history(self) -> HistoryView:
        return HistoryView((), True, self._snapshot.history_retention)

    def co_for_day(self, _day: int) -> CoView:
        return CoView((), (), True, self._snapshot.history_retention)

    def ability_results(self) -> AbilityResultView:
        return AbilityResultView((), True, self._snapshot.history_retention)

    def transport_observations(self) -> TransportObservationView:
        return TransportObservationView(
            world_version=self._snapshot.version,
            first_retained_order=None,
            last_order=None,
            gap_before_first=False,
            observations=(),
            current_deadline=self.deadline,
        )

    async def wait_for_update(self, after_version: int) -> WorldSnapshot:
        while self._snapshot.version <= after_version:
            event = self._update
            await event.wait()
        return self._snapshot

    def update(
        self,
        *,
        actions: tuple[object, ...] | None = None,
        phase: str | None = None,
        mapping_order: int | None = None,
    ) -> None:
        next_version = self._snapshot.version + 1
        phase_view = self._snapshot.phase
        assert phase_view is not None
        self._snapshot = replace(
            self._snapshot,
            version=next_version,
            phase=replace(phase_view, phase=phase or phase_view.phase),
        )
        self._actions = replace(
            self._actions,
            world_version=next_version,
            world_last_applied_seq=self._snapshot.last_applied_seq,
            network_last_seq=self._snapshot.last_applied_seq,
            actions=self._actions.actions if actions is None else actions,
        )
        if mapping_order is not None:
            self.deadline = replace(self.deadline, mapping_order=mapping_order)
        event = self._update
        self._update = asyncio.Event()
        event.set()


class _Sender:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.calls: list[str] = []

    async def _send(self, action: str, *_args: Any) -> SendReceipt:
        self.order.append(f"send:{action}")
        self.calls.append(action)
        return SendReceipt(f"request-{len(self.calls)}", 1)

    async def send_chat(self, *args: Any) -> SendReceipt:
        return await self._send("chat", *args)

    async def send_vote(self, *args: Any) -> SendReceipt:
        return await self._send("vote", *args)

    async def send_ability(self, *args: Any) -> SendReceipt:
        return await self._send("ability", *args)

    async def send_co_declare(self, *args: Any) -> SendReceipt:
        return await self._send("co_declare", *args)

    async def send_co_report(self, *args: Any) -> SendReceipt:
        return await self._send("co_report", *args)


class _Brain:
    def __init__(self, order: list[str], *, block_first: bool = False) -> None:
        self.order = order
        self.block_first = block_first
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[str] = []

    async def decide(self, request):
        handle = request.action_context.options[0].handle
        kind = "vote" if isinstance(handle, VoteAction) else "chat"
        self.calls.append(kind)
        self.order.append(f"brain:{kind}")
        self.started.set()
        if self.block_first and len(self.calls) == 1:
            await self.release.wait()
        if kind == "vote":
            return VoteDecision("action:0", "p2")
        return ChatDecision("action:0", "hello")


class _Activation(AbstractContextManager[None]):
    def __init__(self, lease: _Lease) -> None:
        self.lease = lease

    def __enter__(self) -> None:
        if not self.lease.claimed or self.lease.active:
            raise RuntimeError("invalid fake lease activation")
        self.lease.active = True
        self.lease.admission.order.append(f"activate:{self.lease.invocation_id}")
        return None

    def __exit__(self, *_args: object) -> None:
        self.lease.active = False
        self.lease.admission.order.append(f"deactivate:{self.lease.invocation_id}")
        return None


class _Lease:
    def __init__(self, admission: _Admission, invocation_id: str) -> None:
        self.admission = admission
        self._invocation_id = invocation_id
        self.claimed = False
        self.active = False
        self.released = False

    @property
    def invocation_id(self) -> str:
        return self._invocation_id

    async def claim(self) -> AdmissionStatus:
        self.admission.order.append(f"claim:{self.invocation_id}")
        self.claimed = True
        return AdmissionStatus.GRANTED

    def activate(self) -> AbstractContextManager[None]:
        return _Activation(self)

    async def release(self) -> None:
        if not self.claimed or self.active or self.released:
            raise RuntimeError("invalid fake lease release")
        self.released = True
        self.admission.order.append(f"release:{self.invocation_id}")
        self.admission.release(self.invocation_id)


class _Successor:
    def __init__(self, admission: _Admission, request: AdmissionRequest) -> None:
        self.admission = admission
        self.request = request
        self.future: asyncio.Future[AdmissionResult] = (
            asyncio.get_running_loop().create_future()
        )
        self.waited = False

    @property
    def invocation_id(self) -> str:
        return self.request.invocation_id

    async def wait_offer(self) -> AdmissionResult:
        if self.waited:
            raise RuntimeError("successor result is single-use")
        self.waited = True
        return await asyncio.shield(self.future)

    async def cancel(self) -> AdmissionStatus:
        self.admission.cancelled_successors.append(self.invocation_id)
        if not self.future.done():
            self.future.set_result(
                AdmissionResult(AdmissionStatus.CANCELLED, None, 0)
            )
        return AdmissionStatus.CANCELLED


class _Admission:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.requests: list[AdmissionRequest] = []
        self.results: dict[str, asyncio.Future[AdmissionResult]] = {}
        self.leases: dict[str, _Lease] = {}
        self.replacements: list[tuple[str, str]] = []
        self.successors: dict[str, _Successor] = {}
        self.active_successors: dict[str, _Successor] = {}
        self.cancelled: list[str] = []
        self.cancelled_successors: list[str] = []
        self.close_calls = 0

    async def acquire(self, request: AdmissionRequest) -> AdmissionResult:
        self.requests.append(request)
        future = asyncio.get_running_loop().create_future()
        self.results[request.invocation_id] = future
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            self.cancelled.append(request.invocation_id)
            if not future.done():
                future.set_result(
                    AdmissionResult(AdmissionStatus.CANCELLED, None, 0)
                )
            raise

    async def replace_waiting(
        self, old_invocation_id: str, replacement: AdmissionRequest
    ) -> AdmissionResult:
        self.requests.append(replacement)
        self.replacements.append((old_invocation_id, replacement.invocation_id))
        old = self.results[old_invocation_id]
        if not old.done():
            old.set_result(AdmissionResult(AdmissionStatus.REPLACED, None, 0))
        future = asyncio.get_running_loop().create_future()
        self.results[replacement.invocation_id] = future
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            self.cancelled.append(replacement.invocation_id)
            if not future.done():
                future.set_result(
                    AdmissionResult(AdmissionStatus.CANCELLED, None, 0)
                )
            raise

    async def reserve_successor(
        self, active_invocation_id: str, successor: AdmissionRequest
    ) -> _Successor:
        self.requests.append(successor)
        handle = _Successor(self, successor)
        self.successors[successor.invocation_id] = handle
        self.active_successors[active_invocation_id] = handle
        return handle

    async def cancel_successor(
        self, successor_invocation_id: str
    ) -> AdmissionStatus:
        return await self.successors[successor_invocation_id].cancel()

    async def cancel(self, invocation_id: str) -> AdmissionStatus:
        self.cancelled.append(invocation_id)
        return AdmissionStatus.CANCELLED

    async def aclose(self) -> None:
        self.close_calls += 1

    def offer(self, invocation_id: str) -> None:
        lease = _Lease(self, invocation_id)
        self.leases[invocation_id] = lease
        self.results[invocation_id].set_result(
            AdmissionResult(AdmissionStatus.OFFERED, lease, 1)
        )

    def terminal(self, invocation_id: str, status: AdmissionStatus) -> None:
        self.results[invocation_id].set_result(AdmissionResult(status, None, 0))

    def release(self, invocation_id: str) -> None:
        successor = self.active_successors.pop(invocation_id, None)
        if successor is None or successor.future.done():
            return
        lease = _Lease(self, successor.invocation_id)
        self.leases[successor.invocation_id] = lease
        successor.future.set_result(
            AdmissionResult(AdmissionStatus.OFFERED, lease, 1)
        )


class _ControlledLease(_Lease):
    def __init__(self, admission: _ControlledAdmission, invocation_id: str) -> None:
        super().__init__(admission, invocation_id)
        self.admission = admission

    async def release(self) -> None:
        if not self.claimed or self.active or self.released:
            raise RuntimeError("invalid controlled lease release")
        self.admission.release_attempts.append(self.invocation_id)
        self.admission.order.append(f"release-start:{self.invocation_id}")
        self.admission.release_started.set()
        await self.admission.release_continue.wait()
        if self.admission.fail_release:
            self.admission.order.append(f"release-failed:{self.invocation_id}")
            raise RuntimeError("opaque parent release failure")
        self.released = True
        self.admission.order.append(f"release:{self.invocation_id}")
        self.admission.release(self.invocation_id)


class _ControlledSuccessor(_Successor):
    def __init__(
        self, admission: _ControlledAdmission, request: AdmissionRequest
    ) -> None:
        super().__init__(admission, request)
        self.admission = admission

    async def cancel(self) -> AdmissionStatus:
        self.admission.successor_cancel_attempts.append(self.invocation_id)
        self.admission.order.append(f"successor-cancel-start:{self.invocation_id}")
        self.admission.successor_cancel_started.set()
        await self.admission.successor_cancel_continue.wait()
        self.admission.cancelled_successors.append(self.invocation_id)
        if not self.future.done():
            self.future.set_result(
                AdmissionResult(self.admission.successor_status, None, 0)
            )
        if self.admission.fail_successor_cancel:
            self.admission.order.append(
                f"successor-cancel-failed:{self.invocation_id}"
            )
            raise RuntimeError("opaque successor cancellation failure")
        self.admission.order.append(f"successor-cancelled:{self.invocation_id}")
        return self.admission.successor_status


class _ControlledAdmission(_Admission):
    def __init__(self, order: list[str]) -> None:
        super().__init__(order)
        self.release_started = asyncio.Event()
        self.release_continue = asyncio.Event()
        self.release_continue.set()
        self.successor_cancel_started = asyncio.Event()
        self.successor_cancel_continue = asyncio.Event()
        self.successor_cancel_continue.set()
        self.fail_release = False
        self.fail_successor_cancel = False
        self.successor_status = AdmissionStatus.CANCELLED
        self.release_attempts: list[str] = []
        self.successor_cancel_attempts: list[str] = []

    async def reserve_successor(
        self, active_invocation_id: str, successor: AdmissionRequest
    ) -> _Successor:
        self.requests.append(successor)
        handle = _ControlledSuccessor(self, successor)
        self.successors[successor.invocation_id] = handle
        self.active_successors[active_invocation_id] = handle
        return handle

    def offer(self, invocation_id: str) -> None:
        lease = _ControlledLease(self, invocation_id)
        self.leases[invocation_id] = lease
        self.results[invocation_id].set_result(
            AdmissionResult(AdmissionStatus.OFFERED, lease, 1)
        )

    async def aclose(self) -> None:
        await super().aclose()
        self.order.append("admission-close")


class _FailingAdmission(_Admission):
    async def acquire(self, request: AdmissionRequest) -> AdmissionResult:
        self.requests.append(request)
        raise RuntimeError("private admission failure")


class _MissingCancelAdmission:
    async def acquire(self, request: AdmissionRequest) -> AdmissionResult:
        raise NotImplementedError

    async def replace_waiting(
        self, old_invocation_id: str, replacement: AdmissionRequest
    ) -> AdmissionResult:
        raise NotImplementedError

    async def reserve_successor(
        self, active_invocation_id: str, successor: AdmissionRequest
    ) -> _Successor:
        raise NotImplementedError

    async def cancel_successor(
        self, successor_invocation_id: str
    ) -> AdmissionStatus:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class _RepairBackend:
    def __init__(self, is_active: Callable[[], bool]) -> None:
        self.identity = BackendIdentity(
            "fake", "http://127.0.0.1:1", "/v1/chat/completions", "test", "0" * 64
        )
        self.is_active = is_active
        self.requests: list[object] = []
        self.outputs = [
            "not json",
            '{"kind":"chat","option_id":"action:0","message":"fixed"}',
        ]

    async def generate(self, request: object) -> StructuredGenerationResponse:
        if not self.is_active():
            raise AssertionError("backend call occurred outside the active lease")
        self.requests.append(request)
        return StructuredGenerationResponse(
            request.request_id,  # type: ignore[attr-defined]
            self.outputs.pop(0),
            "test",
            "stop",
            LLMUsage(3, 2),
        )

    async def aclose(self) -> None:
        return None


class _Audit:
    def __init__(self) -> None:
        self.records: list[object] = []

    async def start(self) -> None:
        return None

    async def write(self, record: object) -> AuditWriteAck:
        self.records.append(record)
        return AuditWriteAck(len(self.records))

    async def aclose(self) -> None:
        return None


def _chat() -> ChatAction:
    return ChatAction(1, 1, "day", 1, "chat", "public")


def _vote() -> VoteAction:
    return VoteAction(1, 1, "day", 1, "vote", ("p2",), 1, False)


async def _wait_until(predicate: Callable[[], bool]) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


async def _assert_no_live_seam_task() -> None:
    await asyncio.sleep(0)
    current = asyncio.current_task()
    live = sorted(
        task.get_name()
        for task in asyncio.all_tasks()
        if task is not current
        and not task.done()
        and (
            task.get_name().startswith("aiwolf-brain-")
            or task.get_name().startswith("aiwolf-discussion-")
        )
    )
    assert live == []


class _SeamControllerHarness:
    """Public-surface BrainController double for Arbiter seam ordering tests."""

    def __init__(
        self,
        *,
        actions: tuple[object, ...] = (_chat(), _vote()),
        block_controller: bool = False,
        clock: Callable[[], float] = time.monotonic,
        send_connection_generation: int = 1,
    ) -> None:
        self.order: list[str] = []
        self.world = _World(actions)
        self.controller_started = asyncio.Event()
        self.controller_release = asyncio.Event()
        if not block_controller:
            self.controller_release.set()
        self.finalizer_started = asyncio.Event()
        self.finalizer_release = asyncio.Event()
        self.finalizer_release.set()
        self.finalizer_calls: list[
            tuple[
                DiscussionDispatchCorrelation,
                DiscussionObservationStatus,
                DiscussionTerminalReason,
                EvidenceRef | None,
            ]
        ] = []
        self.controller_stop_calls = 0
        self.controller_calls = 0
        self.fail_finalizer = False
        self.correlations: list[DiscussionDispatchCorrelation] = []
        self.after_local_send: Callable[[], None] | None = None
        self._last_decision: object | None = None
        controller = BrainController(
            world=self.world,  # type: ignore[arg-type]
            sender=_Sender(self.order),  # type: ignore[arg-type]
            brain=_Brain(self.order),
            config=BrainRunConfig(max_decision_seconds=1.0),
            clock=clock,
        )
        self.controller = controller

        async def decide_and_send(request: object, **_kwargs: object) -> DecisionOutcome:
            self.controller_calls += 1
            self.controller_started.set()
            while not self.controller_release.is_set():
                try:
                    await asyncio.shield(self.controller_release.wait())
                except asyncio.CancelledError:
                    # The real BrainController owns a post-send result through
                    # its cancellation boundary.  This double lets the Arbiter
                    # exercise that exact already-local-sent race.
                    continue
            option = request.action_context.options[0]  # type: ignore[attr-defined]
            action = "vote" if isinstance(option.handle, VoteAction) else "chat"
            ordinal = self.controller_calls
            capture_id = f"{1000 + ordinal:064x}"
            correlation = DiscussionDispatchCorrelation(
                capture_id=capture_id,
                request_id=f"phase6:{capture_id}",
                context_sha256="a" * 64,
                before_state_sha256="b" * 64,
                after_state_sha256="c" * 64,
                proposal_sha256="d" * 64,
                generation_audit_sequence=ordinal,
                base_revision=ordinal - 1,
                committed_revision=ordinal,
                action=action,  # type: ignore[arg-type]
                option_id="action:0",
                request_event_id=f"opaque-request-{ordinal}",
                send_connection_generation=send_connection_generation,
            )
            self.correlations.append(correlation)
            receipt = SendReceipt(correlation.request_event_id, 1)
            self._last_decision = (
                VoteDecision("action:0", "p2")
                if action == "vote"
                else ChatDecision("action:0", "opaque chat")
            )
            self.order.append(f"controller-sent:{action}")
            if self.after_local_send is not None:
                self.after_local_send()
            return DecisionOutcome(
                status=DecisionStatus.SENT,
                option_id="action:0",
                receipt=receipt,
                invocation_started=True,
                discussion=correlation,
            )

        def take_dispatched_decision(receipt: SendReceipt | None) -> object:
            assert receipt is not None
            decision = self._last_decision
            self._last_decision = None
            return decision

        async def finalize_discussion_observation(
            *,
            correlation: DiscussionDispatchCorrelation,
            status: DiscussionObservationStatus,
            reason: DiscussionTerminalReason,
            evidence: EvidenceRef | None = None,
        ) -> ObservationAck:
            self.finalizer_calls.append((correlation, status, reason, evidence))
            self.order.append("finalizer-start")
            self.finalizer_started.set()
            await self.finalizer_release.wait()
            if self.fail_finalizer:
                raise RuntimeError("opaque finalizer failure")
            self.order.append("finalizer-durable")
            return ObservationAck(
                capture_id=correlation.capture_id,
                request_id=correlation.request_id,
                base_revision=correlation.base_revision,
                committed_revision=correlation.committed_revision,
                context_sha256=correlation.context_sha256,
                before_state_sha256=correlation.before_state_sha256,
                after_state_sha256=correlation.after_state_sha256,
                proposal_sha256=correlation.proposal_sha256,
                generation_audit_sequence=correlation.generation_audit_sequence,
                action=correlation.action,
                option_id=correlation.option_id,
                request_event_id=correlation.request_event_id,
                send_connection_generation=correlation.send_connection_generation,
                status=status,
                authoritative_evidence=evidence,
            )

        async def stop() -> None:
            self.controller_stop_calls += 1
            self.order.append("controller-stop")

        controller.decide_and_send = decide_and_send  # type: ignore[method-assign]
        controller.take_dispatched_decision = take_dispatched_decision  # type: ignore[method-assign]
        controller.finalize_discussion_observation = (  # type: ignore[method-assign]
            finalize_discussion_observation
        )
        controller.stop = stop  # type: ignore[method-assign]


def _seam_accepted(action: str, order: int) -> EvidenceRef:
    kind = {
        "chat": EvidenceRecordKind.CHAT,
        "co_declare": EvidenceRecordKind.CO_DECLARATION,
        "vote": EvidenceRecordKind.ACTION_ACCEPTED,
        "ability": EvidenceRecordKind.ACTION_ACCEPTED,
    }[action]
    return EvidenceRef(
        kind,
        order,
        (
            EvidenceVisibility.AUTHORIZED_PRIVATE
            if action in {"vote", "ability"}
            else EvidenceVisibility.PUBLIC
        ),
    )


async def _finalize_seam_result(
    arbiter: BrainInvocationArbiter,
    result: BrainDispatchResult,
    *,
    owner: str,
    order: int,
) -> ObservationAck:
    correlation = result.outcome.discussion
    assert correlation is not None
    return await arbiter.finalize_discussion_observation(
        owner=owner,  # type: ignore[arg-type]
        correlation=correlation,
        status=DiscussionObservationStatus.ACCEPTED,
        reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
        evidence=_seam_accepted(correlation.action, order),
    )


def _seam_stack(
    *,
    admission: _Admission | None = None,
    block_controller: bool = False,
    now: list[float] | None = None,
    send_connection_generation: int = 1,
    ids: tuple[str, ...] = ("i1", "i2", "i3", "i4", "i5", "i6"),
) -> tuple[_SeamControllerHarness, _Admission, BrainInvocationArbiter]:
    clock = (lambda: now[0]) if now is not None else time.monotonic
    harness = _SeamControllerHarness(
        block_controller=block_controller,
        clock=clock,
        send_connection_generation=send_connection_generation,
    )
    selected_admission = admission or _Admission(harness.order)
    if admission is not None:
        harness.order = selected_admission.order
    id_values = iter(ids)
    arbiter = BrainInvocationArbiter(
        controller=harness.controller,
        admission=selected_admission,
        invocation_id_factory=lambda: next(id_values),
        clock=clock,
    )
    return harness, selected_admission, arbiter


def _start_seam_invocation(
    arbiter: BrainInvocationArbiter,
    *,
    owner: str,
    deadline: DispatchDeadline,
) -> asyncio.Task[BrainDispatchResult]:
    return asyncio.create_task(
        arbiter.invoke(
            owner=owner,  # type: ignore[arg-type]
            priority=(
                BrainInvocationPriority.REACTION
                if owner == "reaction_chat"
                else BrainInvocationPriority.RESERVATION_ACTION
            ),
            allowed_handles=((_chat(),) if owner == "reaction_chat" else (_vote(),)),
            timeout_seconds=1.0,
            dispatch_deadline=deadline,
        )
    )


class Phase5BrainAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def _make(
        self,
        *,
        actions: tuple[object, ...] | None = None,
        brain: _Brain | None = None,
        admission: _Admission | None = None,
        now: list[float] | None = None,
        ids: tuple[str, ...] = ("i1", "i2", "i3", "i4"),
    ):
        order: list[str] = []
        world = _World(actions or (_chat(), _vote()))
        selected_brain = brain or _Brain(order)
        sender = _Sender(order)
        controller = BrainController(
            world=world,  # type: ignore[arg-type]
            sender=sender,  # type: ignore[arg-type]
            brain=selected_brain,
            config=BrainRunConfig(max_decision_seconds=1.0),
            clock=(lambda: now[0]) if now is not None else time.monotonic,
        )
        selected_admission = admission or _Admission(order)
        id_values = iter(ids)
        arbiter = BrainInvocationArbiter(
            controller=controller,
            admission=selected_admission,
            invocation_id_factory=lambda: next(id_values),
            clock=(lambda: now[0]) if now is not None else time.monotonic,
        )
        return (
            arbiter,
            controller,
            world,
            sender,
            selected_brain,
            selected_admission,
            order,
        )

    @staticmethod
    def _deadline(cutoff: float | None = None) -> DispatchDeadline:
        return DispatchDeadline(
            1,
            "day",
            1,
            1,
            1,
            cutoff if cutoff is not None else time.monotonic() + 10,
        )

    async def test_structural_admission_requires_public_cancel(self) -> None:
        order: list[str] = []
        controller = BrainController(
            world=_World((_chat(),)),  # type: ignore[arg-type]
            sender=_Sender(order),  # type: ignore[arg-type]
            brain=_Brain(order),
            config=BrainRunConfig(max_decision_seconds=1.0),
        )
        with self.assertRaisesRegex(
            TypeError, "^admission must implement GenerationAdmission$"
        ):
            BrainInvocationArbiter(
                controller=controller,
                admission=_MissingCancelAdmission(),  # type: ignore[arg-type]
            )

    async def test_grant_claim_activate_callback_and_brain_are_exactly_ordered(self) -> None:
        arbiter, controller, _world, sender, brain, admission, order = self._make()
        original = controller.decide_and_send

        async def observed(*args, **kwargs):
            order.append("controller")
            return await original(*args, **kwargs)

        controller.decide_and_send = observed  # type: ignore[method-assign]
        callback_count = 0

        def commit() -> None:
            nonlocal callback_count
            callback_count += 1
            order.append("commit")

        task = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(_chat(),),
                timeout_seconds=1.0,
                dispatch_deadline=self._deadline(),
                on_brain_start=commit,
            )
        )
        await _wait_until(lambda: bool(admission.requests))
        self.assertIs(admission.requests[0].priority, GenerationPriority.REACTION)
        admission.offer("i1")
        result = await task
        self.assertEqual(result.outcome.status, DecisionStatus.SENT)
        self.assertEqual(sender.calls, ["chat"])
        self.assertEqual(brain.calls, ["chat"])
        self.assertEqual(callback_count, 1)
        self.assertLess(order.index("claim:i1"), order.index("commit"))
        self.assertLess(order.index("commit"), order.index("controller"))
        self.assertLess(order.index("controller"), order.index("brain:chat"))
        self.assertTrue(admission.leases["i1"].released)
        await arbiter.stop()

    async def test_one_commit_wraps_both_llm_repair_ordinals_in_one_lease(self) -> None:
        order: list[str] = []
        world = _World((_chat(),))
        sender = _Sender(order)
        admission = _Admission(order)
        backend = _RepairBackend(
            lambda: any(lease.active for lease in admission.leases.values())
        )
        audit = _Audit()
        brain = LLMBrain(
            backend=backend,  # type: ignore[arg-type]
            audit=audit,  # type: ignore[arg-type]
            identity=LLMClientIdentity("game-1", "p1"),
            request_id_factory=lambda: "request-1",
            utc_clock=lambda: datetime(2026, 9, 11, tzinfo=timezone.utc),
        )
        controller = BrainController(
            world=world,  # type: ignore[arg-type]
            sender=sender,  # type: ignore[arg-type]
            brain=brain,
            config=BrainRunConfig(max_decision_seconds=1.0),
        )
        arbiter = BrainInvocationArbiter(
            controller=controller,
            admission=admission,
            invocation_id_factory=lambda: "repair-invocation",
        )
        commits = 0

        def commit() -> None:
            nonlocal commits
            commits += 1

        task = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(_chat(),),
                timeout_seconds=1.0,
                dispatch_deadline=self._deadline(),
                on_brain_start=commit,
            )
        )
        await _wait_until(lambda: bool(admission.requests))
        admission.offer("repair-invocation")
        result = await task
        self.assertEqual(result.outcome.status, DecisionStatus.SENT)
        self.assertEqual(commits, 1)
        self.assertEqual(len(backend.requests), 2)
        self.assertEqual(len(audit.records), 2)
        self.assertEqual(sender.calls, ["chat"])
        self.assertTrue(admission.leases["repair-invocation"].released)
        await arbiter.stop()

    async def test_waiting_reaction_is_replaced_then_resumed_with_fresh_identity(self) -> None:
        arbiter, _controller, _world, sender, brain, admission, _order = self._make()
        commits = 0

        def commit() -> None:
            nonlocal commits
            commits += 1

        reaction = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(_chat(),),
                timeout_seconds=1.0,
                dispatch_deadline=self._deadline(),
                on_brain_start=commit,
            )
        )
        await _wait_until(lambda: len(admission.requests) == 1)
        reservation = asyncio.create_task(
            arbiter.invoke(
                owner="vote_ability",
                priority=BrainInvocationPriority.RESERVATION_ACTION,
                allowed_handles=(_vote(),),
                timeout_seconds=1.0,
                dispatch_deadline=self._deadline(),
            )
        )
        await _wait_until(lambda: bool(admission.replacements))
        self.assertEqual(admission.replacements, [("i1", "i2")])
        self.assertEqual(commits, 0)
        self.assertEqual(brain.calls, [])
        admission.offer("i2")
        self.assertEqual((await reservation).outcome.status, DecisionStatus.SENT)
        await _wait_until(lambda: len(admission.requests) == 3)
        self.assertEqual(commits, 0)
        self.assertEqual([request.invocation_id for request in admission.requests], ["i1", "i2", "i3"])
        admission.offer("i3")
        self.assertEqual((await reaction).outcome.status, DecisionStatus.SENT)
        self.assertEqual(commits, 1)
        self.assertEqual(brain.calls, ["vote", "chat"])
        self.assertEqual(sender.calls, ["vote", "chat"])
        await arbiter.stop()

    async def test_claimed_reaction_is_non_preemptive_and_reservation_is_successor(self) -> None:
        order: list[str] = []
        brain = _Brain(order, block_first=True)
        arbiter, _controller, _world, _sender, _brain, admission, order = self._make(
            brain=brain
        )
        reaction = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(_chat(),),
                timeout_seconds=1.0,
                dispatch_deadline=self._deadline(),
            )
        )
        await _wait_until(lambda: len(admission.requests) == 1)
        admission.offer("i1")
        await brain.started.wait()
        reservation = asyncio.create_task(
            arbiter.invoke(
                owner="vote_ability",
                priority=BrainInvocationPriority.RESERVATION_ACTION,
                allowed_handles=(_vote(),),
                timeout_seconds=1.0,
                dispatch_deadline=self._deadline(),
            )
        )
        await _wait_until(lambda: "i2" in admission.successors)
        self.assertEqual(brain.calls, ["chat"])
        brain.release.set()
        first, second = await asyncio.gather(reaction, reservation)
        self.assertEqual(first.outcome.status, DecisionStatus.SENT)
        self.assertEqual(second.outcome.status, DecisionStatus.SENT)
        self.assertEqual(brain.calls, ["chat", "vote"])
        self.assertIsNot(admission.leases["i1"], admission.leases["i2"])
        self.assertLess(order.index("release:i1"), order.index("claim:i2"))
        await arbiter.stop()

    async def test_wait_cancel_stale_deadline_and_stop_commit_zero(self) -> None:
        cases = (
            "cancel",
            "phase_stale",
            "handle_stale",
            "mapping_stale",
            "deadline",
            "stop",
        )
        for case in cases:
            with self.subTest(case=case):
                now = [1.0]
                arbiter, _controller, world, sender, brain, admission, _order = self._make(
                    now=now
                )
                commits = 0

                def commit() -> None:
                    nonlocal commits
                    commits += 1

                task = asyncio.create_task(
                    arbiter.invoke(
                        owner="reaction_chat",
                        priority=BrainInvocationPriority.REACTION,
                        allowed_handles=(_chat(),),
                        timeout_seconds=1.0,
                        dispatch_deadline=self._deadline(5.0),
                        on_brain_start=commit,
                    )
                )
                await _wait_until(lambda: bool(admission.requests))
                if case == "cancel":
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                elif case == "phase_stale":
                    world.update(phase="vote")
                    self.assertEqual((await task).outcome.status, DecisionStatus.STALE)
                elif case == "handle_stale":
                    world.update(actions=(_vote(),))
                    self.assertEqual((await task).outcome.status, DecisionStatus.STALE)
                elif case == "mapping_stale":
                    world.update(mapping_order=2)
                    self.assertEqual((await task).outcome.status, DecisionStatus.STALE)
                elif case == "deadline":
                    now[0] = 5.0
                    world.update()
                    self.assertEqual(
                        (await task).outcome.status,
                        DecisionStatus.DEADLINE_SUPPRESSED,
                    )
                else:
                    await arbiter.stop()
                    self.assertEqual((await task).outcome.status, DecisionStatus.CANCELLED)
                self.assertEqual(commits, 0)
                self.assertEqual(brain.calls, [])
                self.assertEqual(sender.calls, [])
                await arbiter.stop()

    async def test_offered_context_status_preserves_deadline_precedence(
        self,
    ) -> None:
        cases = (
            ("mapping_replace", DecisionStatus.DEADLINE_SUPPRESSED),
            ("mapping_clear", DecisionStatus.DEADLINE_SUPPRESSED),
            ("cutoff", DecisionStatus.DEADLINE_SUPPRESSED),
            ("phase_stale", DecisionStatus.STALE),
            ("handle_stale", DecisionStatus.STALE),
            ("action_stale", DecisionStatus.STALE),
        )
        for case, expected in cases:
            with self.subTest(case=case):
                now = [1.0]
                arbiter, _controller, world, sender, brain, admission, _order = (
                    self._make(actions=(_chat(),), now=now)
                )
                task = asyncio.create_task(
                    arbiter.invoke(
                        owner="reaction_chat",
                        priority=BrainInvocationPriority.REACTION,
                        allowed_handles=(_chat(),),
                        timeout_seconds=1.0,
                        dispatch_deadline=self._deadline(5.0),
                    )
                )
                await _wait_until(lambda: bool(admission.requests))
                admission.offer("i1")
                if case == "mapping_replace":
                    world.update(mapping_order=2)
                elif case == "mapping_clear":
                    world.deadline = None  # type: ignore[assignment]
                    world.update()
                elif case == "cutoff":
                    now[0] = 5.0
                    world.update()
                elif case == "phase_stale":
                    world.update(phase="vote")
                elif case == "handle_stale":
                    world.update(actions=(_vote(),))
                else:
                    world.update(
                        actions=(
                            ChatAction(1, 2, "day", 1, "chat", "public"),
                        )
                    )

                result = await asyncio.wait_for(task, timeout=1.0)
                self.assertEqual(result.outcome.status, expected)
                self.assertEqual(admission.cancelled, ["i1"])
                self.assertFalse(admission.leases["i1"].released)
                self.assertEqual(brain.calls, [])
                self.assertEqual(sender.calls, [])
                self.assertIsNone(arbiter._admission_lease)
                self.assertEqual(arbiter._pending, {})
                await arbiter.stop()

    async def test_world_updates_progress_while_admission_waits(self) -> None:
        arbiter, _controller, world, _sender, brain, admission, _order = self._make()
        task = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(_chat(),),
                timeout_seconds=1.0,
                dispatch_deadline=self._deadline(),
            )
        )
        await _wait_until(lambda: bool(admission.requests))
        world.update()
        await asyncio.sleep(0)
        self.assertEqual(world.snapshot().version, 2)
        self.assertFalse(task.done())
        admission.offer("i1")
        self.assertEqual((await task).outcome.status, DecisionStatus.SENT)
        self.assertEqual(brain.calls, ["chat"])
        await arbiter.stop()

    async def test_admission_terminal_statuses_fail_closed_without_brain(self) -> None:
        cases = (
            (AdmissionStatus.EXPIRED, DecisionStatus.DEADLINE_SUPPRESSED),
            (AdmissionStatus.CANCELLED, DecisionStatus.CANCELLED),
            (AdmissionStatus.OVERLOADED, DecisionStatus.BRAIN_FAILED),
            (AdmissionStatus.UNAVAILABLE, DecisionStatus.BRAIN_FAILED),
            (AdmissionStatus.POISONED, DecisionStatus.BRAIN_FAILED),
        )
        for terminal, expected in cases:
            with self.subTest(terminal=terminal):
                arbiter, _controller, _world, sender, brain, admission, _order = self._make()
                task = asyncio.create_task(
                    arbiter.invoke(
                        owner="reaction_chat",
                        priority=BrainInvocationPriority.REACTION,
                        allowed_handles=(_chat(),),
                        timeout_seconds=1.0,
                        dispatch_deadline=self._deadline(),
                    )
                )
                await _wait_until(lambda: bool(admission.requests))
                admission.terminal("i1", terminal)
                self.assertEqual((await task).outcome.status, expected)
                self.assertEqual(brain.calls, [])
                self.assertEqual(sender.calls, [])
                await arbiter.stop()

    async def test_admission_session_failure_is_sanitized_and_starts_no_brain(self) -> None:
        failing = _FailingAdmission([])
        arbiter, _controller, _world, sender, brain, admission, _order = self._make(
            admission=failing
        )
        result = await arbiter.invoke(
            owner="reaction_chat",
            priority=BrainInvocationPriority.REACTION,
            allowed_handles=(_chat(),),
            timeout_seconds=1.0,
            dispatch_deadline=self._deadline(),
        )
        self.assertIs(admission, failing)
        self.assertEqual(result.outcome.status, DecisionStatus.BRAIN_FAILED)
        self.assertEqual(result.outcome.error_type, "RuntimeError")
        self.assertNotIn("private admission failure", repr(result))
        self.assertEqual(brain.calls, [])
        self.assertEqual(sender.calls, [])
        await arbiter.stop()

    async def test_callback_exception_releases_lease_without_brain_or_send(self) -> None:
        arbiter, _controller, _world, sender, brain, admission, _order = self._make()

        def fail_commit() -> None:
            raise RuntimeError("counter commit failed")

        task = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(_chat(),),
                timeout_seconds=1.0,
                dispatch_deadline=self._deadline(),
                on_brain_start=fail_commit,
            )
        )
        await _wait_until(lambda: bool(admission.requests))
        admission.offer("i1")
        with self.assertRaisesRegex(RuntimeError, "^counter commit failed$"):
            await task
        self.assertTrue(admission.leases["i1"].released)
        self.assertEqual(brain.calls, [])
        self.assertEqual(sender.calls, [])
        await arbiter.stop()

    async def test_production_reaction_counters_commit_once_only_after_claim(self) -> None:
        reaction, _world, _source, brain, sender = _make_frequency_stack(
            SpeakingProfile()
        )
        admission = _Admission([])
        arbiter = BrainInvocationArbiter(
            controller=reaction.invoker.controller,
            admission=admission,
            invocation_id_factory=lambda: "reaction-granted",
        )
        reaction.invoker = arbiter
        reaction.start()
        try:
            await _wait_for_frequency(lambda: bool(admission.requests))
            waiting = reaction.snapshot()
            self.assertEqual(waiting.chat_brain_invocations, 0)
            self.assertEqual(
                waiting.frequency_state.committed_brain_invocations,  # type: ignore[union-attr]
                0,
            )
            self.assertEqual(brain.requests, [])
            admission.offer("reaction-granted")
            await _wait_for_frequency(lambda: reaction.snapshot().accepted_count == 1)
            granted = reaction.snapshot()
            self.assertEqual(granted.chat_brain_invocations, 1)
            self.assertEqual(
                granted.frequency_state.committed_brain_invocations,  # type: ignore[union-attr]
                1,
            )
            self.assertEqual(len(brain.requests), 1)
            self.assertEqual(len(sender.calls), 1)
        finally:
            await reaction.stop()
            await arbiter.stop()

    async def test_production_reaction_counters_stay_zero_before_claim(self) -> None:
        cases = (
            "caller_cancel",
            "stop",
            "phase_stale",
            "handle_stale",
            "mapping_stale",
        )
        for case in cases:
            with self.subTest(case=case):
                reaction, world, source, brain, sender = _make_frequency_stack(
                    SpeakingProfile()
                )
                admission = _Admission([])
                arbiter = BrainInvocationArbiter(
                    controller=reaction.invoker.controller,
                    admission=admission,
                    invocation_id_factory=lambda: f"reaction-{case}",
                )
                reaction.invoker = arbiter
                reaction.start()
                try:
                    await _wait_for_frequency(lambda: bool(admission.requests))
                    if case == "caller_cancel":
                        await reaction.stop()
                    elif case == "stop":
                        await arbiter.stop()
                        await _wait_for_frequency(
                            lambda: bool(reaction.snapshot().outcomes)
                        )
                    elif case == "phase_stale":
                        source.replace_phase(
                            seq=2,
                            day=2,
                            action_generation=4,
                            chat=False,
                        )
                        world._consume(  # noqa: SLF001 - production integration fixture
                            _frequency_event(
                                "game.state_sync",
                                2,
                                _frequency_sync_payload(day=2, chat=False),
                                day=2,
                            )
                        )
                        await _wait_for_frequency(
                            lambda: bool(reaction.snapshot().outcomes)
                        )
                    elif case == "handle_stale":
                        source._snapshot = replace(  # noqa: SLF001 - typed integration fixture
                            source.snapshot(), actions=()
                        )
                        world._commit()  # noqa: SLF001 - wake production arbiter
                        await _wait_for_frequency(
                            lambda: bool(reaction.snapshot().outcomes)
                        )
                    else:
                        mapped_at = time.monotonic()
                        world._consume(  # noqa: SLF001 - production integration fixture
                            PhaseTimingMapped(
                                "day",
                                1,
                                source.snapshot().last_seq,
                                1,
                                3,
                                102,
                                112,
                                mapped_at,
                                mapped_at + 10,
                            )
                        )
                        await _wait_for_frequency(
                            lambda: bool(reaction.snapshot().outcomes)
                        )
                    snapshot = reaction.snapshot()
                    self.assertEqual(snapshot.chat_brain_invocations, 0)
                    self.assertEqual(
                        snapshot.frequency_state.committed_brain_invocations,  # type: ignore[union-attr]
                        0,
                    )
                    self.assertEqual(brain.requests, [])
                    self.assertEqual(sender.calls, [])
                finally:
                    await reaction.stop()
                    await arbiter.stop()

    async def test_production_reaction_replacement_commits_neither_counter(self) -> None:
        reaction, world, source, _brain, _sender = _make_frequency_stack(
            SpeakingProfile()
        )
        admission = _Admission([])
        ids = iter(("reaction-old", "reservation", "reaction-fresh"))
        arbiter = BrainInvocationArbiter(
            controller=reaction.invoker.controller,
            admission=admission,
            invocation_id_factory=lambda: next(ids),
        )
        reaction.invoker = arbiter
        reaction.start()
        try:
            await _wait_for_frequency(lambda: len(admission.requests) == 1)
            chat = world.current_actions().actions[0]
            vote = VoteAction(1, 3, "day", 1, "vote", ("p0",), 1, False)
            source._snapshot = replace(  # noqa: SLF001 - typed integration fixture
                source.snapshot(), actions=(chat, vote)
            )
            current_deadline = world.transport_observations().current_deadline
            assert current_deadline is not None
            assert current_deadline.local_deadline_monotonic is not None
            reservation = asyncio.create_task(
                arbiter.invoke(
                    owner="vote_ability",
                    priority=BrainInvocationPriority.RESERVATION_ACTION,
                    allowed_handles=(vote,),
                    timeout_seconds=1.0,
                    dispatch_deadline=DispatchDeadline(
                        current_deadline.mapping_order,
                        current_deadline.phase,
                        current_deadline.day,
                        current_deadline.connection_generation,
                        current_deadline.action_generation,
                        current_deadline.local_deadline_monotonic - 0.01,
                    ),
                )
            )
            await _wait_for_frequency(lambda: bool(admission.replacements))
            replaced = reaction.snapshot()
            self.assertEqual(replaced.chat_brain_invocations, 0)
            self.assertEqual(
                replaced.frequency_state.committed_brain_invocations,  # type: ignore[union-attr]
                0,
            )
            admission.offer("reservation")
            self.assertEqual(
                (await reservation).outcome.status,
                DecisionStatus.INVALID_DECISION,
            )
            await _wait_for_frequency(lambda: len(admission.requests) == 3)
            resumed_wait = reaction.snapshot()
            self.assertEqual(resumed_wait.chat_brain_invocations, 0)
            self.assertEqual(
                resumed_wait.frequency_state.committed_brain_invocations,  # type: ignore[union-attr]
                0,
            )
        finally:
            await reaction.stop()
            await arbiter.stop()

    async def test_cancelled_attached_successor_never_starts(self) -> None:
        order: list[str] = []
        brain = _Brain(order, block_first=True)
        arbiter, _controller, _world, _sender, _brain, admission, _order = self._make(
            brain=brain
        )
        reaction = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(_chat(),),
                timeout_seconds=1.0,
                dispatch_deadline=self._deadline(),
            )
        )
        await _wait_until(lambda: bool(admission.requests))
        admission.offer("i1")
        await brain.started.wait()
        reservation = asyncio.create_task(
            arbiter.invoke(
                owner="vote_ability",
                priority=BrainInvocationPriority.RESERVATION_ACTION,
                allowed_handles=(_vote(),),
                timeout_seconds=1.0,
                dispatch_deadline=self._deadline(),
            )
        )
        await _wait_until(lambda: "i2" in admission.successors)
        reservation.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await reservation
        self.assertEqual(admission.cancelled_successors, ["i2"])
        brain.release.set()
        self.assertEqual((await reaction).outcome.status, DecisionStatus.SENT)
        self.assertEqual(brain.calls, ["chat"])
        await arbiter.stop()

    async def test_active_mapping_replacement_cancels_blocking_brain_without_send(
        self,
    ) -> None:
        order: list[str] = []
        brain = _Brain(order, block_first=True)
        arbiter, _controller, world, sender, _brain, admission, _order = self._make(
            actions=(_chat(),), brain=brain
        )
        task = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(_chat(),),
                timeout_seconds=1.0,
                dispatch_deadline=self._deadline(),
            )
        )
        await _wait_until(lambda: bool(admission.requests))
        admission.offer("i1")
        await brain.started.wait()
        world.update(mapping_order=2)
        result = await asyncio.wait_for(task, timeout=0.5)
        self.assertEqual(result.outcome.status, DecisionStatus.DEADLINE_SUPPRESSED)
        self.assertEqual(sender.calls, [])
        self.assertTrue(admission.leases["i1"].released)
        await arbiter.stop()

    async def test_cutoff_wakes_active_watcher_without_world_update(self) -> None:
        order: list[str] = []
        brain = _Brain(order, block_first=True)
        arbiter, _controller, _world, sender, _brain, admission, _order = self._make(
            actions=(_chat(),), brain=brain
        )
        task = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(_chat(),),
                timeout_seconds=1.0,
                dispatch_deadline=self._deadline(time.monotonic() + 0.5),
            )
        )
        await _wait_until(lambda: bool(admission.requests))
        admission.offer("i1")
        await brain.started.wait()
        result = await asyncio.wait_for(task, timeout=1.0)
        self.assertEqual(result.outcome.status, DecisionStatus.DEADLINE_SUPPRESSED)
        self.assertEqual(sender.calls, [])
        self.assertTrue(admission.leases["i1"].released)
        await arbiter.stop()

    async def test_completed_claim_terminal_releases_without_starting_brain(self) -> None:
        for boundary in ("stale", "cancel", "watcher_error"):
            with self.subTest(boundary=boundary):
                backend = _FakeBackend()
                broker = GenerationAdmissionBroker(
                    {"opaque-a": _token(1)}, backend, fairness_seed="claim-terminal"
                )
                await broker.start()
                session = await BrokerAdmissionSession.connect(
                    broker.ready.host, broker.ready.port,
                    AdmissionCredentials("opaque-a", _token(1)),
                )
                arbiter, _controller, world, sender, brain, _, _ = self._make(
                    actions=(_chat(),), admission=session,
                )
                frames = []
                original_send = session._send
                original_await_claim = arbiter._await_claim
                original_cancel_watchers = arbiter._cancel_watchers

                async def observed_send(message_type, **fields):
                    frames.append(message_type)
                    await original_send(message_type, **fields)

                async def observe_boundary(pending, claim):
                    if boundary == "watcher_error":
                        # Watcher fails first; CLAIM completes during watcher cleanup.
                        async def broken_watcher(_version):
                            raise RuntimeError("fixed watcher failure")

                        async def settle_claim_before_cleanup(*watchers):
                            self.assertEqual(await asyncio.shield(claim), AdmissionStatus.GRANTED)
                            await original_cancel_watchers(*watchers)

                        world.wait_for_update = broken_watcher
                        arbiter._cancel_watchers = settle_claim_before_cleanup
                    else:
                        self.assertEqual(await asyncio.shield(claim), AdmissionStatus.GRANTED)
                        if boundary == "stale":
                            world.update(phase="night")
                        else:
                            pending.cancel_requested = True
                    return await original_await_claim(pending, claim)

                session._send = observed_send
                arbiter._await_claim = observe_boundary
                try:
                    result = await asyncio.wait_for(arbiter.invoke(
                        owner="reaction_chat", priority=BrainInvocationPriority.REACTION,
                        allowed_handles=(_chat(),), timeout_seconds=1.0,
                        dispatch_deadline=self._deadline(time.monotonic() + 5.0),
                    ), timeout=3.0)
                    self.assertEqual(result.outcome.status,
                                     DecisionStatus.CANCELLED if boundary == "cancel" else DecisionStatus.STALE)
                    self.assertEqual(brain.calls, [])
                    self.assertEqual(sender.calls, [])
                    self.assertEqual(backend.calls, [])
                    self.assertEqual(frames.count("RELEASE"), 1)
                    self.assertNotIn("ABANDON", frames)
                    self.assertIsNone(broker.snapshot.claimed)
                    self.assertEqual(broker.snapshot.pending_total, 0)
                    self.assertIsNone(session._claimed_invocation)
                finally:
                    arbiter._cancel_watchers = original_cancel_watchers
                    await arbiter.stop()
                    await session.aclose()
                    await broker.aclose()

    async def test_production_loopback_mapping_stale_abandons_before_provider_release(
        self,
    ) -> None:
        gate = asyncio.Event()
        backend = _FakeBackend([gate])
        config = GenerationBrokerConfig(
            authentication_timeout_seconds=0.5,
            cancellation_grace_seconds=0.5,
            provider_drain_grace_seconds=1.5,
            shutdown_grace_seconds=2.0,
        )
        broker = GenerationAdmissionBroker(
            {"opaque-a": _token(1)},
            backend,
            fairness_seed="t048-loopback",
            config=config,
            backend_request_timeout_seconds=1.0,
        )
        await broker.start()
        ready = broker.ready
        session = await BrokerAdmissionSession.connect(
            ready.host,
            ready.port,
            AdmissionCredentials("opaque-a", _token(1)),
            config=config,
        )
        frames: list[str] = []
        original_send = session._send

        async def observed_send(message_type: str, **fields: object) -> None:
            frames.append(message_type)
            await original_send(message_type, **fields)

        session._send = observed_send  # type: ignore[method-assign]
        world = _World((_chat(),))
        sender = _Sender([])
        audit = _Audit()
        brain = LLMBrain(
            backend=BrokeredStructuredLLMBackend(session),
            audit=audit,  # type: ignore[arg-type]
            identity=LLMClientIdentity("game-1", "p1"),
            request_id_factory=lambda: "active-stale-request",
            utc_clock=lambda: datetime(2026, 9, 11, tzinfo=timezone.utc),
        )
        controller = BrainController(
            world=world,  # type: ignore[arg-type]
            sender=sender,  # type: ignore[arg-type]
            brain=brain,
            config=BrainRunConfig(
                max_decision_seconds=1.0,
                cancellation_grace_seconds=0.5,
            ),
        )
        arbiter = BrainInvocationArbiter(
            controller=controller,
            admission=session,
            invocation_id_factory=lambda: "active-stale-loopback",
        )
        try:
            invocation = asyncio.create_task(
                arbiter.invoke(
                    owner="reaction_chat",
                    priority=BrainInvocationPriority.REACTION,
                    allowed_handles=(_chat(),),
                    timeout_seconds=1.0,
                    dispatch_deadline=self._deadline(time.monotonic() + 5.0),
                )
            )
            await asyncio.wait_for(backend.started.wait(), timeout=2.0)
            stale_started = time.monotonic()
            world.update(mapping_order=2)
            result = await asyncio.wait_for(invocation, timeout=2.0)
            self.assertLess(time.monotonic() - stale_started, 0.5)
            self.assertEqual(
                result.outcome.status, DecisionStatus.DEADLINE_SUPPRESSED
            )
            self.assertEqual(sender.calls, [])
            self.assertIn("ABANDON", frames)
            self.assertNotIn("RELEASE", frames)
            self.assertTrue(broker.snapshot.draining)
            self.assertEqual(session._claimed_invocation, None)
            self.assertEqual(session._call_ordinals, {})
            self.assertEqual(session._request_ids, {})
            self.assertEqual(session._generations, {})
            self.assertFalse(session._closed)
            gate.set()
            await _wait_until(lambda: broker.snapshot.pending_total == 0)
            self.assertFalse(broker.snapshot.poisoned)
        finally:
            gate.set()
            await arbiter.stop()
            await session.aclose()
            await broker.aclose()

    async def test_production_loopback_abandon_preserves_attached_successor(
        self,
    ) -> None:
        gate = asyncio.Event()
        backend = _FakeBackend([gate])
        config = GenerationBrokerConfig(
            authentication_timeout_seconds=0.5,
            cancellation_grace_seconds=0.5,
            provider_drain_grace_seconds=1.5,
            shutdown_grace_seconds=2.0,
        )
        broker = GenerationAdmissionBroker(
            {"opaque-a": _token(1)},
            backend,
            fairness_seed="t048-successor",
            config=config,
            backend_request_timeout_seconds=1.0,
        )
        await broker.start()
        ready = broker.ready
        session = await BrokerAdmissionSession.connect(
            ready.host,
            ready.port,
            AdmissionCredentials("opaque-a", _token(1)),
            config=config,
        )
        frames: list[str] = []
        original_send = session._send

        async def observed_send(message_type: str, **fields: object) -> None:
            frames.append(message_type)
            await original_send(message_type, **fields)

        session._send = observed_send  # type: ignore[method-assign]
        world = _World((_chat(), _vote()))
        sender = _Sender([])
        brain = LLMBrain(
            backend=BrokeredStructuredLLMBackend(session),
            audit=_Audit(),  # type: ignore[arg-type]
            identity=LLMClientIdentity("game-1", "p1"),
            request_id_factory=iter(("reaction-request", "successor-request")).__next__,
            utc_clock=lambda: datetime(2026, 9, 11, tzinfo=timezone.utc),
        )
        controller = BrainController(
            world=world,  # type: ignore[arg-type]
            sender=sender,  # type: ignore[arg-type]
            brain=brain,
            config=BrainRunConfig(
                max_decision_seconds=1.0,
                cancellation_grace_seconds=0.5,
            ),
        )
        invocation_ids = iter(("active-reaction", "attached-reservation"))
        arbiter = BrainInvocationArbiter(
            controller=controller,
            admission=session,
            invocation_id_factory=invocation_ids.__next__,
        )
        try:
            reaction = asyncio.create_task(
                arbiter.invoke(
                    owner="reaction_chat",
                    priority=BrainInvocationPriority.REACTION,
                    allowed_handles=(_chat(),),
                    timeout_seconds=1.0,
                    dispatch_deadline=self._deadline(time.monotonic() + 5.0),
                )
            )
            await asyncio.wait_for(backend.started.wait(), timeout=2.0)

            world.deadline = replace(world.deadline, mapping_order=2)
            reservation = asyncio.create_task(
                arbiter.invoke(
                    owner="vote_ability",
                    priority=BrainInvocationPriority.RESERVATION_ACTION,
                    allowed_handles=(_vote(),),
                    timeout_seconds=1.0,
                    dispatch_deadline=DispatchDeadline(
                        2, "day", 1, 1, 1, time.monotonic() + 5.0
                    ),
                )
            )
            await _wait_until(lambda: arbiter._attached_successor is not None)
            world.update(mapping_order=2)
            stale_started = time.monotonic()
            reaction_result = await asyncio.wait_for(reaction, timeout=2.0)
            self.assertLess(time.monotonic() - stale_started, 0.5)
            self.assertEqual(
                reaction_result.outcome.status,
                DecisionStatus.DEADLINE_SUPPRESSED,
            )
            self.assertTrue(broker.snapshot.draining)
            self.assertFalse(reservation.done())
            self.assertEqual(frames.count("ABANDON"), 1)
            self.assertNotIn("RELEASE", frames)
            self.assertFalse(session._closed)

            gate.set()
            reservation_result = await asyncio.wait_for(reservation, timeout=2.0)
            self.assertEqual(
                reservation_result.outcome.status, DecisionStatus.NO_DECISION
            )
            self.assertEqual(len(backend.calls), 2)
            self.assertEqual(sender.calls, [])
            self.assertFalse(broker.snapshot.poisoned)
            self.assertEqual(broker.snapshot.pending_total, 0)
        finally:
            gate.set()
            await arbiter.stop()
            await session.aclose()
            await broker.aclose()

    async def test_no_admission_keeps_direct_path_and_public_callback(self) -> None:
        order: list[str] = []
        world = _World((_chat(),))
        brain = _Brain(order)
        sender = _Sender(order)
        controller = BrainController(
            world=world,  # type: ignore[arg-type]
            sender=sender,  # type: ignore[arg-type]
            brain=brain,
            config=BrainRunConfig(max_decision_seconds=1.0),
        )
        arbiter = BrainInvocationArbiter(controller=controller)
        commits = 0

        def commit() -> None:
            nonlocal commits
            commits += 1

        result = await arbiter.invoke(
            owner="reaction_chat",
            priority=BrainInvocationPriority.REACTION,
            allowed_handles=(_chat(),),
            timeout_seconds=1.0,
            dispatch_deadline=self._deadline(),
            on_brain_start=commit,
        )
        self.assertEqual(result.outcome.status, DecisionStatus.SENT)
        self.assertEqual(commits, 1)
        self.assertEqual(brain.calls, ["chat"])
        await arbiter.stop()

    async def test_p6bc_arbiter_holds_other_owner_without_holding_provider_lease(
        self,
    ) -> None:
        harness, admission, arbiter = _seam_stack()
        deadline = self._deadline()
        reaction = _start_seam_invocation(
            arbiter, owner="reaction_chat", deadline=deadline
        )
        await _wait_until(lambda: len(admission.requests) == 1)
        admission.offer("i1")
        reaction_result = await asyncio.wait_for(reaction, timeout=2.0)
        self.assertTrue(admission.leases["i1"].released)
        self.assertFalse(admission.leases["i1"].active)

        vote = _start_seam_invocation(
            arbiter, owner="vote_ability", deadline=deadline
        )
        await asyncio.sleep(0)
        self.assertEqual(len(admission.requests), 1)
        self.assertEqual(harness.controller_calls, 1)
        self.assertFalse(vote.done())
        with self.assertRaisesRegex(
            RuntimeError, "already has a pending invocation"
        ):
            await arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(_chat(),),
                timeout_seconds=1.0,
                dispatch_deadline=deadline,
            )
        self.assertEqual(len(admission.requests), 1)

        await _finalize_seam_result(
            arbiter, reaction_result, owner="reaction_chat", order=31
        )
        await _wait_until(lambda: len(admission.requests) == 2)
        self.assertEqual(admission.requests[1].invocation_id, "i2")
        admission.offer("i2")
        vote_result = await asyncio.wait_for(vote, timeout=2.0)
        await _finalize_seam_result(
            arbiter, vote_result, owner="vote_ability", order=32
        )
        self.assertEqual(harness.controller_calls, 2)
        await arbiter.stop()

        # The context-free direct path still permits its historical one active
        # plus one same-owner pending slot.  If the active call becomes a
        # contextful gate, that already-pending same owner is rejected instead
        # of being stranded behind its own observation.
        harness = _SeamControllerHarness(block_controller=True)
        arbiter = BrainInvocationArbiter(controller=harness.controller)
        active = _start_seam_invocation(
            arbiter, owner="reaction_chat", deadline=self._deadline()
        )
        await asyncio.wait_for(harness.controller_started.wait(), timeout=2.0)
        same_owner_waiter = _start_seam_invocation(
            arbiter, owner="reaction_chat", deadline=self._deadline()
        )
        await asyncio.sleep(0)
        self.assertFalse(same_owner_waiter.done())
        harness.controller_release.set()
        active_result = await asyncio.wait_for(active, timeout=2.0)
        with self.assertRaisesRegex(RuntimeError, "cannot wait behind"):
            await asyncio.wait_for(same_owner_waiter, timeout=2.0)
        await _finalize_seam_result(
            arbiter, active_result, owner="reaction_chat", order=35
        )
        self.assertEqual(harness.controller_calls, 1)
        await arbiter.stop()

    async def test_p6bc_arbiter_expired_waiter_resumes_without_brain_start(
        self,
    ) -> None:
        now = [1.0]
        harness, admission, arbiter = _seam_stack(now=now)
        reaction_deadline = DispatchDeadline(1, "day", 1, 1, 1, 50.0)
        reaction = _start_seam_invocation(
            arbiter, owner="reaction_chat", deadline=reaction_deadline
        )
        await _wait_until(lambda: len(admission.requests) == 1)
        admission.offer("i1")
        reaction_result = await asyncio.wait_for(reaction, timeout=2.0)

        vote = _start_seam_invocation(
            arbiter,
            owner="vote_ability",
            deadline=DispatchDeadline(1, "day", 1, 1, 1, 5.0),
        )
        await asyncio.sleep(0)
        self.assertFalse(vote.done())
        now[0] = 5.0
        await _finalize_seam_result(
            arbiter, reaction_result, owner="reaction_chat", order=33
        )
        vote_result = await asyncio.wait_for(vote, timeout=2.0)
        self.assertEqual(
            vote_result.outcome.status, DecisionStatus.DEADLINE_SUPPRESSED
        )
        self.assertEqual(len(admission.requests), 1)
        self.assertEqual(harness.controller_calls, 1)
        await arbiter.stop()

    async def test_p6bc_contextful_sent_registers_gate_before_cleanup_and_publication(
        self,
    ) -> None:
        order: list[str] = []
        admission = _ControlledAdmission(order)
        admission.release_continue.clear()
        harness, admission, arbiter = _seam_stack(admission=admission)
        reaction = _start_seam_invocation(
            arbiter, owner="reaction_chat", deadline=self._deadline()
        )
        await _wait_until(lambda: len(admission.requests) == 1)
        admission.offer("i1")
        await asyncio.wait_for(admission.release_started.wait(), timeout=2.0)

        self.assertFalse(reaction.done())
        self.assertFalse(admission.leases["i1"].active)
        self.assertFalse(admission.leases["i1"].released)

        vote = _start_seam_invocation(
            arbiter, owner="vote_ability", deadline=self._deadline()
        )
        await asyncio.sleep(0)
        self.assertEqual(len(admission.requests), 1)
        self.assertEqual(harness.controller_calls, 1)
        vote.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await vote

        admission.release_continue.set()
        reaction_result = await asyncio.wait_for(reaction, timeout=2.0)
        self.assertTrue(admission.leases["i1"].released)
        await _finalize_seam_result(
            arbiter, reaction_result, owner="reaction_chat", order=34
        )
        self.assertEqual(len(admission.requests), 1)
        await arbiter.stop()

    async def test_p6bc_parent_release_failure_recovers_once_poisons_and_closes_resources(
        self,
    ) -> None:
        admission = _ControlledAdmission([])
        admission.fail_release = True
        harness, admission, arbiter = _seam_stack(admission=admission)
        reaction = _start_seam_invocation(
            arbiter, owner="reaction_chat", deadline=self._deadline()
        )
        await _wait_until(lambda: len(admission.requests) == 1)
        admission.offer("i1")

        with self.assertRaisesRegex(
            RuntimeError, "BrainInvocationArbiter is poisoned"
        ):
            await asyncio.wait_for(reaction, timeout=2.0)
        with self.assertRaisesRegex(RuntimeError, "opaque parent release failure"):
            await asyncio.wait_for(arbiter.stop(), timeout=2.0)
        await _assert_no_live_seam_task()

        self.assertEqual(admission.release_attempts, ["i1"])
        self.assertEqual(admission.close_calls, 1)
        self.assertEqual(harness.controller_stop_calls, 1)
        self.assertEqual(len(harness.finalizer_calls), 1)
        correlation, status, reason, evidence = harness.finalizer_calls[0]
        self.assertIs(correlation, harness.correlations[0])
        self.assertIs(status, DiscussionObservationStatus.RECOVERY_UNKNOWN)
        self.assertIs(reason, DiscussionTerminalReason.OWNER_STOPPED)
        self.assertIsNone(evidence)
        self.assertLess(
            admission.order.index("release-failed:i1"),
            admission.order.index("finalizer-start"),
        )
        self.assertLess(
            admission.order.index("finalizer-durable"),
            admission.order.index("admission-close"),
        )
        self.assertLess(
            admission.order.index("admission-close"),
            admission.order.index("controller-stop"),
        )
        with self.assertRaisesRegex(
            RuntimeError, "BrainInvocationArbiter is poisoned"
        ):
            await arbiter.invoke(
                owner="vote_ability",
                priority=BrainInvocationPriority.RESERVATION_ACTION,
                allowed_handles=(_vote(),),
                timeout_seconds=1.0,
                dispatch_deadline=self._deadline(),
            )
        with self.assertRaisesRegex(
            RuntimeError, "BrainInvocationArbiter is poisoned"
        ):
            await arbiter.finalize_discussion_observation(
                owner="reaction_chat",
                correlation=harness.correlations[0],
                status=DiscussionObservationStatus.RECOVERY_UNKNOWN,
                reason=DiscussionTerminalReason.OWNER_STOPPED,
            )

    async def test_p6bc_successor_cancel_failure_recovers_once_poisons_and_closes_resources(
        self,
    ) -> None:
        admission = _ControlledAdmission([])
        admission.fail_successor_cancel = True
        harness, admission, arbiter = _seam_stack(
            admission=admission, block_controller=True
        )
        reaction = _start_seam_invocation(
            arbiter, owner="reaction_chat", deadline=self._deadline()
        )
        await _wait_until(lambda: len(admission.requests) == 1)
        admission.offer("i1")
        await asyncio.wait_for(harness.controller_started.wait(), timeout=2.0)
        vote = _start_seam_invocation(
            arbiter, owner="vote_ability", deadline=self._deadline()
        )
        await _wait_until(lambda: "i2" in admission.successors)
        successor = admission.successors["i2"]

        harness.controller_release.set()
        for task in (reaction, vote):
            with self.assertRaisesRegex(
                RuntimeError, "BrainInvocationArbiter is poisoned"
            ):
                await asyncio.wait_for(task, timeout=2.0)
        with self.assertRaisesRegex(
            RuntimeError, "opaque successor cancellation failure"
        ):
            await asyncio.wait_for(arbiter.stop(), timeout=2.0)
        await _assert_no_live_seam_task()

        self.assertEqual(admission.successor_cancel_attempts, ["i2"])
        self.assertEqual(admission.release_attempts, ["i1"])
        self.assertLess(
            admission.order.index("successor-cancel-failed:i2"),
            admission.order.index("release-start:i1"),
        )
        self.assertNotIn("i2", admission.leases)
        self.assertFalse(successor.waited)
        self.assertEqual(len(admission.requests), 2)
        self.assertEqual(harness.controller_calls, 1)
        self.assertEqual(len(harness.finalizer_calls), 1)
        self.assertIs(
            harness.finalizer_calls[0][1],
            DiscussionObservationStatus.RECOVERY_UNKNOWN,
        )
        self.assertIs(
            harness.finalizer_calls[0][2], DiscussionTerminalReason.OWNER_STOPPED
        )
        self.assertIsNone(harness.finalizer_calls[0][3])
        self.assertEqual(admission.close_calls, 1)
        self.assertEqual(harness.controller_stop_calls, 1)
        self.assertLess(
            admission.order.index("finalizer-durable"),
            admission.order.index("admission-close"),
        )
        self.assertLess(
            admission.order.index("admission-close"),
            admission.order.index("controller-stop"),
        )

    async def test_p6bc_invoke_cancel_before_consumption_direct_and_admitted_recovers_once(
        self,
    ) -> None:
        for path, timing in (
            ("direct", "before-send-completion"),
            ("direct", "after-send-completion"),
            ("admitted", "before-send-completion"),
            ("admitted", "after-send-during-cleanup"),
        ):
            with self.subTest(path=path, timing=timing):
                if path == "direct":
                    harness = _SeamControllerHarness(block_controller=True)
                    admission = None
                    arbiter = BrainInvocationArbiter(controller=harness.controller)
                elif timing == "after-send-during-cleanup":
                    controlled = _ControlledAdmission([])
                    controlled.release_continue.clear()
                    harness, admission, arbiter = _seam_stack(
                        admission=controlled
                    )
                else:
                    harness, admission, arbiter = _seam_stack(
                        block_controller=True
                    )

                invocation = _start_seam_invocation(
                    arbiter, owner="reaction_chat", deadline=self._deadline()
                )
                if admission is not None:
                    await _wait_until(lambda: len(admission.requests) == 1)
                    admission.offer("i1")
                await asyncio.wait_for(
                    harness.controller_started.wait(), timeout=2.0
                )

                if path == "direct" and timing == "after-send-completion":
                    def cancel_after_local_send() -> None:
                        invocation.cancel()
                        invocation.cancel()

                    harness.after_local_send = cancel_after_local_send
                    harness.controller_release.set()
                elif timing == "after-send-during-cleanup":
                    assert isinstance(admission, _ControlledAdmission)
                    await asyncio.wait_for(
                        admission.release_started.wait(), timeout=2.0
                    )
                    invocation.cancel()
                    invocation.cancel()
                    admission.release_continue.set()
                else:
                    invocation.cancel()
                    await asyncio.sleep(0)
                    invocation.cancel()
                    harness.controller_release.set()

                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(invocation, timeout=2.0)
                await _wait_until(lambda: len(harness.finalizer_calls) == 1)
                self.assertEqual(len(harness.finalizer_calls), 1)
                self.assertIs(
                    harness.finalizer_calls[0][1],
                    DiscussionObservationStatus.RECOVERY_UNKNOWN,
                )
                self.assertIs(
                    harness.finalizer_calls[0][2],
                    DiscussionTerminalReason.OWNER_STOPPED,
                )
                self.assertIsNone(harness.finalizer_calls[0][3])
                await asyncio.wait_for(arbiter.stop(), timeout=2.0)
                self.assertEqual(harness.controller_stop_calls, 1)

    async def test_p6bc_invoke_cancel_after_consumption_leaves_feature_owner(
        self,
    ) -> None:
        for path in ("direct", "admitted"):
            with self.subTest(path=path):
                harness = _SeamControllerHarness()
                admission: _Admission | None = None
                if path == "direct":
                    arbiter = BrainInvocationArbiter(controller=harness.controller)
                else:
                    admission = _Admission(harness.order)
                    ids = iter(("i1",))
                    arbiter = BrainInvocationArbiter(
                        controller=harness.controller,
                        admission=admission,
                        invocation_id_factory=lambda: next(ids),
                    )
                consumed = asyncio.Event()
                feature_release = asyncio.Event()
                returned: list[BrainDispatchResult] = []

                async def feature_owner() -> None:
                    result = await arbiter.invoke(
                        owner="reaction_chat",
                        priority=BrainInvocationPriority.REACTION,
                        allowed_handles=(_chat(),),
                        timeout_seconds=1.0,
                        dispatch_deadline=self._deadline(),
                    )
                    returned.append(result)
                    consumed.set()
                    await feature_release.wait()

                feature = asyncio.create_task(feature_owner())
                if admission is not None:
                    await _wait_until(lambda: len(admission.requests) == 1)
                    admission.offer("i1")
                await asyncio.wait_for(consumed.wait(), timeout=2.0)
                self.assertEqual(len(returned), 1)
                feature.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(feature, timeout=2.0)
                await asyncio.sleep(0)
                self.assertEqual(harness.finalizer_calls, [])
                await _finalize_seam_result(
                    arbiter, returned[0], owner="reaction_chat", order=40
                )
                self.assertEqual(len(harness.finalizer_calls), 1)
                self.assertIs(
                    harness.finalizer_calls[0][1],
                    DiscussionObservationStatus.ACCEPTED,
                )
                await arbiter.stop()

    async def test_p6bc_gate_parks_ordinary_attached_and_replacement_with_fresh_identity(
        self,
    ) -> None:
        with self.subTest(topology="ordinary-pending"):
            harness, admission, arbiter = _seam_stack()
            reaction = _start_seam_invocation(
                arbiter, owner="reaction_chat", deadline=self._deadline()
            )
            await _wait_until(lambda: len(admission.requests) == 1)
            admission.offer("i1")
            reaction_result = await asyncio.wait_for(reaction, timeout=2.0)
            vote = _start_seam_invocation(
                arbiter, owner="vote_ability", deadline=self._deadline()
            )
            await asyncio.sleep(0)
            self.assertFalse(vote.done())
            self.assertEqual([item.invocation_id for item in admission.requests], ["i1"])
            await _finalize_seam_result(
                arbiter, reaction_result, owner="reaction_chat", order=51
            )
            await _wait_until(lambda: len(admission.requests) == 2)
            self.assertEqual(admission.requests[-1].invocation_id, "i2")
            self.assertIs(
                admission.requests[-1].priority, GenerationPriority.RESERVATION
            )
            admission.offer("i2")
            vote_result = await asyncio.wait_for(vote, timeout=2.0)
            await _finalize_seam_result(
                arbiter, vote_result, owner="vote_ability", order=52
            )
            await arbiter.stop()

        with self.subTest(topology="attached-successor"):
            harness, admission, arbiter = _seam_stack(block_controller=True)
            reaction = _start_seam_invocation(
                arbiter, owner="reaction_chat", deadline=self._deadline()
            )
            await _wait_until(lambda: len(admission.requests) == 1)
            admission.offer("i1")
            await asyncio.wait_for(harness.controller_started.wait(), timeout=2.0)
            vote = _start_seam_invocation(
                arbiter, owner="vote_ability", deadline=self._deadline()
            )
            await _wait_until(lambda: "i2" in admission.successors)
            successor = admission.successors["i2"]
            harness.controller_release.set()
            reaction_result = await asyncio.wait_for(reaction, timeout=2.0)
            self.assertEqual(admission.cancelled_successors, ["i2"])
            self.assertNotIn("i2", admission.leases)
            self.assertFalse(successor.waited)
            self.assertFalse(vote.done())
            self.assertEqual(
                [item.invocation_id for item in admission.requests], ["i1", "i2"]
            )
            await _finalize_seam_result(
                arbiter, reaction_result, owner="reaction_chat", order=53
            )
            await _wait_until(lambda: len(admission.requests) == 3)
            self.assertEqual(admission.requests[-1].invocation_id, "i3")
            self.assertNotEqual(
                admission.requests[-1].invocation_id, successor.invocation_id
            )
            admission.offer("i3")
            vote_result = await asyncio.wait_for(vote, timeout=2.0)
            await _finalize_seam_result(
                arbiter, vote_result, owner="vote_ability", order=54
            )
            await arbiter.stop()

        with self.subTest(topology="replacement-suspended"):
            harness, admission, arbiter = _seam_stack()
            reaction = _start_seam_invocation(
                arbiter, owner="reaction_chat", deadline=self._deadline()
            )
            await _wait_until(lambda: len(admission.requests) == 1)
            vote = _start_seam_invocation(
                arbiter, owner="vote_ability", deadline=self._deadline()
            )
            await _wait_until(lambda: admission.replacements == [("i1", "i2")])
            self.assertFalse(reaction.done())
            admission.offer("i2")
            vote_result = await asyncio.wait_for(vote, timeout=2.0)
            self.assertFalse(reaction.done())
            self.assertEqual(
                [item.invocation_id for item in admission.requests], ["i1", "i2"]
            )
            await _finalize_seam_result(
                arbiter, vote_result, owner="vote_ability", order=55
            )
            await _wait_until(lambda: len(admission.requests) == 3)
            self.assertEqual(admission.requests[-1].invocation_id, "i3")
            self.assertNotEqual(admission.requests[-1].invocation_id, "i1")
            self.assertIs(admission.requests[-1].priority, GenerationPriority.REACTION)
            admission.offer("i3")
            reaction_result = await asyncio.wait_for(reaction, timeout=2.0)
            await _finalize_seam_result(
                arbiter, reaction_result, owner="reaction_chat", order=56
            )
            await arbiter.stop()

    async def test_p6bc_gated_parked_cancel_or_expiry_never_acquires_or_starts(
        self,
    ) -> None:
        with self.subTest(terminal="caller-cancelled"):
            harness, admission, arbiter = _seam_stack()
            reaction = _start_seam_invocation(
                arbiter, owner="reaction_chat", deadline=self._deadline()
            )
            await _wait_until(lambda: len(admission.requests) == 1)
            admission.offer("i1")
            reaction_result = await asyncio.wait_for(reaction, timeout=2.0)
            vote = _start_seam_invocation(
                arbiter, owner="vote_ability", deadline=self._deadline()
            )
            await asyncio.sleep(0)
            vote.cancel()
            vote.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(vote, timeout=2.0)
            await _finalize_seam_result(
                arbiter, reaction_result, owner="reaction_chat", order=61
            )
            await asyncio.sleep(0)
            self.assertEqual(len(admission.requests), 1)
            self.assertEqual(harness.controller_calls, 1)
            await arbiter.stop()

        with self.subTest(terminal="expired"):
            now = [1.0]
            harness, admission, arbiter = _seam_stack(now=now)
            reaction = _start_seam_invocation(
                arbiter,
                owner="reaction_chat",
                deadline=DispatchDeadline(1, "day", 1, 1, 1, 50.0),
            )
            await _wait_until(lambda: len(admission.requests) == 1)
            admission.offer("i1")
            reaction_result = await asyncio.wait_for(reaction, timeout=2.0)
            vote = _start_seam_invocation(
                arbiter,
                owner="vote_ability",
                deadline=DispatchDeadline(1, "day", 1, 1, 1, 3.0),
            )
            await asyncio.sleep(0)
            self.assertFalse(vote.done())
            now[0] = 3.0
            await _finalize_seam_result(
                arbiter, reaction_result, owner="reaction_chat", order=62
            )
            vote_result = await asyncio.wait_for(vote, timeout=2.0)
            self.assertEqual(
                vote_result.outcome.status, DecisionStatus.DEADLINE_SUPPRESSED
            )
            self.assertEqual(len(admission.requests), 1)
            self.assertEqual(harness.controller_calls, 1)
            await arbiter.stop()

    async def test_p6bc_attached_successor_cancel_precedes_release_without_offer_lane_or_lease(
        self,
    ) -> None:
        admission = _ControlledAdmission([])
        admission.successor_cancel_continue.clear()
        harness, admission, arbiter = _seam_stack(
            admission=admission, block_controller=True
        )
        reaction = _start_seam_invocation(
            arbiter, owner="reaction_chat", deadline=self._deadline()
        )
        await _wait_until(lambda: len(admission.requests) == 1)
        admission.offer("i1")
        await asyncio.wait_for(harness.controller_started.wait(), timeout=2.0)
        vote = _start_seam_invocation(
            arbiter, owner="vote_ability", deadline=self._deadline()
        )
        await _wait_until(lambda: "i2" in admission.successors)
        successor = admission.successors["i2"]

        harness.controller_release.set()
        await asyncio.wait_for(
            admission.successor_cancel_started.wait(), timeout=2.0
        )
        self.assertEqual(admission.successor_cancel_attempts, ["i2"])
        self.assertEqual(admission.release_attempts, [])
        self.assertFalse(reaction.done())
        self.assertFalse(vote.done())
        self.assertFalse(successor.future.done())
        self.assertNotIn("i2", admission.leases)

        admission.successor_cancel_continue.set()
        reaction_result = await asyncio.wait_for(reaction, timeout=2.0)
        self.assertEqual(admission.release_attempts, ["i1"])
        self.assertLess(
            admission.order.index("successor-cancelled:i2"),
            admission.order.index("release-start:i1"),
        )
        self.assertFalse(successor.waited)
        self.assertNotIn("i2", admission.leases)
        self.assertFalse(vote.done())

        vote.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(vote, timeout=2.0)
        await _finalize_seam_result(
            arbiter, reaction_result, owner="reaction_chat", order=63
        )
        self.assertEqual(len(admission.requests), 2)
        self.assertEqual(harness.controller_calls, 1)
        await arbiter.stop()

    async def test_p6bc_vote_ability_n_to_n_plus_one_accepts_and_older_is_not_a_match(
        self,
    ) -> None:
        send_generation = 7
        cases = (
            ("same-generation", "vote.cast", "exact", 7, True),
            ("later-generation", "vote.cast", "exact", 8, True),
            ("older-generation", "vote.cast", "exact", 6, False),
            ("wrong-wire-action", "ability.use", "exact", 7, False),
            ("wrong-request", "vote.cast", "different", 7, False),
        )
        for label, action, request_case, observed_generation, expected in cases:
            with self.subTest(case=label):
                harness, admission, arbiter = _seam_stack(
                    send_connection_generation=send_generation
                )
                vote = _start_seam_invocation(
                    arbiter, owner="vote_ability", deadline=self._deadline()
                )
                await _wait_until(lambda: len(admission.requests) == 1)
                admission.offer("i1")
                result = await asyncio.wait_for(vote, timeout=2.0)
                correlation = result.outcome.discussion
                assert correlation is not None
                request_event_id = (
                    correlation.request_event_id
                    if request_case == "exact"
                    else "opaque-different-request"
                )
                observation = ActionAcceptedObservation(
                    order=70 + observed_generation,
                    world_version=2,
                    action=action,
                    request_event_id=request_event_id,
                    seq=20,
                    observation_connection_generation=observed_generation,
                    observed_at_monotonic=2.0,
                )
                # Matching belongs to the future feature owner and occurs
                # before this seam.  This literal contract oracle decides only
                # whether that owner is permitted to call the public finalizer.
                is_exact_candidate = (
                    observation.action == "vote.cast"
                    and observation.request_event_id
                    == correlation.request_event_id
                    and observation.observation_connection_generation
                    >= correlation.send_connection_generation
                )
                self.assertIs(is_exact_candidate, expected)
                if expected:
                    evidence = EvidenceRef(
                        EvidenceRecordKind.ACTION_ACCEPTED,
                        observation.order,
                        EvidenceVisibility.AUTHORIZED_PRIVATE,
                    )
                    ack = await arbiter.finalize_discussion_observation(
                        owner="vote_ability",
                        correlation=correlation,
                        status=DiscussionObservationStatus.ACCEPTED,
                        reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
                        evidence=evidence,
                    )
                    self.assertIs(ack.authoritative_evidence, evidence)
                    self.assertEqual(len(harness.finalizer_calls), 1)
                else:
                    await asyncio.sleep(0)
                    self.assertEqual(harness.finalizer_calls, [])
                await arbiter.stop()
                if not expected:
                    self.assertEqual(len(harness.finalizer_calls), 1)
                    self.assertIs(
                        harness.finalizer_calls[0][2],
                        DiscussionTerminalReason.OWNER_STOPPED,
                    )

    async def test_p6bc_contiguous_recovery_without_response_is_ambiguous_with_barrier(
        self,
    ) -> None:
        cases = (
            (
                "later-contiguous",
                True,
                False,
                8,
                DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
            ),
            (
                "later-gap-or-floor",
                False,
                True,
                8,
                DiscussionTerminalReason.RECOVERY_GAP,
            ),
        )
        for label, contiguous, gap, generation, expected_reason in cases:
            with self.subTest(case=label):
                harness, admission, arbiter = _seam_stack(
                    send_connection_generation=7
                )
                vote = _start_seam_invocation(
                    arbiter, owner="vote_ability", deadline=self._deadline()
                )
                await _wait_until(lambda: len(admission.requests) == 1)
                admission.offer("i1")
                result = await asyncio.wait_for(vote, timeout=2.0)
                correlation = result.outcome.discussion
                assert correlation is not None
                barrier = ResumeRecoveryBarrier(
                    order=81,
                    world_version=3,
                    connection_generation=generation,
                    requested_last_seq=20,
                    replay_first_seq=21,
                    replay_last_seq=21,
                    replay_contiguous=contiguous,
                    replay_gap_or_floor=gap,
                    resumed_seq=21,
                    state_sync_seq=21,
                    complete=True,
                )
                self.assertGreater(
                    barrier.connection_generation,
                    correlation.send_connection_generation,
                )
                evidence = EvidenceRef(
                    EvidenceRecordKind.RESUME_RECOVERY_BARRIER,
                    barrier.order,
                    EvidenceVisibility.AUTHORIZED_PRIVATE,
                )
                ack = await arbiter.finalize_discussion_observation(
                    owner="vote_ability",
                    correlation=correlation,
                    status=DiscussionObservationStatus.RECOVERY_UNKNOWN,
                    reason=expected_reason,
                    evidence=evidence,
                )
                self.assertIs(ack.status, DiscussionObservationStatus.RECOVERY_UNKNOWN)
                self.assertIs(ack.authoritative_evidence, evidence)
                self.assertIs(harness.finalizer_calls[0][2], expected_reason)
                await arbiter.stop()

        harness, admission, arbiter = _seam_stack(
            send_connection_generation=7
        )
        vote = _start_seam_invocation(
            arbiter, owner="vote_ability", deadline=self._deadline()
        )
        await _wait_until(lambda: len(admission.requests) == 1)
        admission.offer("i1")
        result = await asyncio.wait_for(vote, timeout=2.0)
        correlation = result.outcome.discussion
        assert correlation is not None
        nonterminal = ResumeRecoveryBarrier(
            order=82,
            world_version=3,
            connection_generation=correlation.send_connection_generation,
            requested_last_seq=20,
            replay_first_seq=21,
            replay_last_seq=21,
            replay_contiguous=True,
            replay_gap_or_floor=False,
            resumed_seq=21,
            state_sync_seq=21,
            complete=True,
        )
        self.assertFalse(
            nonterminal.connection_generation
            > correlation.send_connection_generation
        )
        self.assertEqual(harness.finalizer_calls, [])
        await arbiter.stop()
        self.assertIs(
            harness.finalizer_calls[0][2], DiscussionTerminalReason.OWNER_STOPPED
        )
