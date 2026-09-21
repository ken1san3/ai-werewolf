"""One Brain invocation: capture, run, validate, stale-check, and dispatch."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, replace
import math
import time
import unicodedata
from typing import Any, Callable, Literal

from ai_client.discussion.context import canonical_sha256
from ai_client.discussion.model import (
    DiscussionAbortReason,
    DiscussionCapture,
    DiscussionDeliveryStatus,
    DiscussionDispatchCorrelation,
    DiscussionGenerationAck,
    DiscussionObservationStatus,
    DiscussionProposal,
    DiscussionStatePort,
    DiscussionTerminalReason,
    EvidenceRecordKind,
    EvidenceRef,
    ObservationAck,
)
from ai_client.discussion.state import DiscussionViews
from ai_client.discussion.transaction import (
    AiDiscussionTerminalRecord,
    DiscussionTerminalAuditSink,
    DiscussionTransaction,
    DiscussionTransactionError,
)

from ai_client.network import (
    AbilityAction,
    ActionHandle,
    ChatAction,
    CoDeclareAction,
    CoReportAction,
    DeliveryUnknownError,
    NetworkClient,
    NotDeliveredError,
    SendReceipt,
    StaleActionError,
    VoteAction,
)
from ai_client.world import (
    ChatRecord,
    CoDeclarationRecord,
    Freshness,
    WorldSnapshot,
    WorldState,
)

from .interface import Brain
from .model import (
    AbilityDecision,
    BrainActionContext,
    BrainActionOption,
    BrainInput,
    BrainOutput,
    BrainResult,
    BrainRunConfig,
    BrainDecision,
    ChatDecision,
    CoDeclareDecision,
    CoReportDecision,
    DecisionOutcome,
    DecisionStatus,
    DispatchDeadline,
    NoDecision,
    VoteDecision,
    brain_decision_identity,
)


_DECISION_TYPES = (
    NoDecision,
    ChatDecision,
    VoteDecision,
    AbilityDecision,
    CoDeclareDecision,
    CoReportDecision,
)


def _repeat_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def _last_self_accepted_text(request: BrainInput) -> str | None:
    if not request.history.complete or not request.co.complete:
        return None
    self_view = request.snapshot.self_view
    if self_view is None:
        return None
    self_id = self_view.player_id
    candidates: dict[tuple[str, int], tuple[str | None, str]] = {}
    values: list[tuple[int, str, str]] = []
    records = tuple(request.history.records) + tuple(request.co.declarations)
    for record in records:
        if isinstance(record, ChatRecord):
            kind = record.record_kind
            text = record.message
        elif isinstance(record, CoDeclarationRecord):
            kind = record.record_kind
            text = record.comment
        else:
            continue
        if type(record.order) is not int or record.order < 0:
            return None
        identity = (kind, record.order)
        material = (record.player_id, text)
        prior = candidates.get(identity)
        if prior is not None and prior != material:
            return None
        if prior is None:
            candidates[identity] = material
            if record.player_id != self_id:
                continue
            values.append((record.order, kind, text))
    if not values:
        return None
    latest_order = max(item[0] for item in values)
    latest = [item for item in values if item[0] == latest_order]
    if len(latest) != 1:
        return None
    return latest[0][2]


def _invalid_or_repeated_self_text(
    request: BrainInput, decision: BrainDecision
) -> bool:
    if isinstance(decision, ChatDecision):
        candidate = decision.message
    elif isinstance(decision, CoDeclareDecision):
        candidate = decision.comment
    else:
        return False
    normalized = _repeat_text(candidate)
    if not normalized:
        return True
    previous = _last_self_accepted_text(request)
    if previous is not None and _repeat_text(previous) == normalized:
        return True
    return _cross_player_public_copy(request, normalized)


def _cross_player_public_copy(request: BrainInput, candidate: str) -> bool:
    """Reject only long full copies in this receiver's public retained history."""
    normalized = _repeat_text(candidate).casefold()
    if len(normalized) < 50 or len(normalized.split()) < 8:
        return False
    capture = request.discussion
    if capture is None:
        return False
    public_channels = {
        channel.channel_id for channel in capture.context.chat_channels if channel.is_public
    }
    for record in (*request.history.records, *request.co.declarations):
        if isinstance(record, ChatRecord) and record.channel in public_channels:
            text = record.message
        elif isinstance(record, CoDeclarationRecord):
            text = record.comment
        else:
            continue
        if record.player_id is None or record.player_id == capture.player_id:
            continue
        if _repeat_text(text).casefold() == normalized:
            return True
    return False


class _Invocation:
    def __init__(self, request: BrainInput) -> None:
        self.request = request
        self.brain_task: asyncio.Task[BrainOutput] | None = None
        self.world_task: asyncio.Task[WorldSnapshot] | None = None
        self.cancel_status: DecisionStatus | None = None
        self.terminal = False
        self.discussion_terminal_attempted = False
        self.completed = asyncio.Event()


class _BrainFailure:
    def __init__(self, error: BaseException, status: DecisionStatus) -> None:
        self.error = error
        self.status = status


@dataclass(frozen=True)
class CaptureAttempt:
    status: Literal["CAPTURED", "NETWORK_AHEAD", "STALE"]
    request: BrainInput | None
    after_world_version: int

    def __post_init__(self) -> None:
        if self.status not in {"CAPTURED", "NETWORK_AHEAD", "STALE"}:
            raise ValueError("invalid capture attempt status")
        if isinstance(self.after_world_version, bool) or not isinstance(
            self.after_world_version, int
        ) or self.after_world_version < 0:
            raise ValueError("after_world_version must be a non-negative integer")
        if (self.status == "CAPTURED" and not isinstance(self.request, BrainInput)) or (
            self.status != "CAPTURED" and self.request is not None
        ):
            raise ValueError("only CAPTURED may contain a BrainInput")


@dataclass
class _DiscussionObservationMaterial:
    """The sole unresolved local-send material retained for outer observation."""

    capture: DiscussionCapture
    generation: DiscussionGenerationAck
    proposal: DiscussionProposal
    correlation: DiscussionDispatchCorrelation
    attempt: tuple[
        DiscussionObservationStatus,
        DiscussionTerminalReason,
        EvidenceRef | None,
    ] | None = None


@dataclass(frozen=True)
class _CompletedDiscussionObservation:
    correlation: DiscussionDispatchCorrelation
    status: DiscussionObservationStatus
    reason: DiscussionTerminalReason
    evidence: EvidenceRef | None
    acknowledgement: ObservationAck


class BrainController:
    """Orchestrate one immutable Brain request without owning World/Network loops."""

    def __init__(
        self,
        *,
        world: WorldState,
        sender: NetworkClient,
        brain: Brain,
        config: BrainRunConfig = BrainRunConfig(),
        clock: Callable[[], float] = time.monotonic,
        discussion_state: DiscussionStatePort | None = None,
        discussion_audit: DiscussionTerminalAuditSink | None = None,
    ) -> None:
        if not isinstance(config, BrainRunConfig):
            raise TypeError("config must be BrainRunConfig")
        if not hasattr(brain, "decide") or not callable(brain.decide):
            raise TypeError("brain must provide an async decide(request) method")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if (discussion_state is None) is not (discussion_audit is None):
            raise ValueError(
                "discussion_state and discussion_audit must be supplied together"
            )
        self.world = world
        self.sender = sender
        self.brain = brain
        self.config = config
        self._clock = clock
        self._discussion = (
            None
            if discussion_state is None or discussion_audit is None
            else DiscussionTransaction(state=discussion_state, audit=discussion_audit)
        )
        self._active: _Invocation | None = None
        self._stopping = False
        self._unresponsive = False
        self._last_dispatched_decision: tuple[SendReceipt, BrainDecision] | None = None
        self._discussion_observation: _DiscussionObservationMaterial | None = None
        self._completed_discussion_observation: (
            _CompletedDiscussionObservation | None
        ) = None

    @property
    def active(self) -> bool:
        return self._active is not None

    @property
    def unresponsive(self) -> bool:
        return self._unresponsive

    def capture_input(
        self,
        *,
        allowed_handles: tuple[ActionHandle, ...] | None = None,
        dispatch_deadline: DispatchDeadline | None = None,
    ) -> BrainInput | None:
        """Synchronously capture a consistent, request-local World view."""

        attempt = self._capture_input(
            allowed_handles=allowed_handles,
            dispatch_deadline=dispatch_deadline,
            report_network_ahead=False,
        )
        return attempt.request

    def capture_input_attempt(
        self,
        *,
        allowed_handles: tuple[ActionHandle, ...],
        dispatch_deadline: DispatchDeadline,
    ) -> CaptureAttempt:
        """Capture once while distinguishing the narrow Network-ahead window."""

        if not isinstance(dispatch_deadline, DispatchDeadline):
            raise TypeError("dispatch_deadline must be DispatchDeadline")
        return self._capture_input(
            allowed_handles=allowed_handles,
            dispatch_deadline=dispatch_deadline,
            report_network_ahead=True,
        )

    def _capture_input(
        self,
        *,
        allowed_handles: tuple[ActionHandle, ...] | None,
        dispatch_deadline: DispatchDeadline | None,
        report_network_ahead: bool,
    ) -> CaptureAttempt:
        if dispatch_deadline is not None and not isinstance(dispatch_deadline, DispatchDeadline):
            raise TypeError("dispatch_deadline must be DispatchDeadline when supplied")
        snapshot = self.world.snapshot()
        if not self._snapshot_is_current(snapshot):
            return CaptureAttempt("STALE", None, snapshot.version)
        actions = self.world.current_actions()
        allowed_valid = (
            allowed_handles is None
            or (
                isinstance(allowed_handles, tuple)
                and bool(allowed_handles)
                and all(isinstance(handle, ActionHandle) for handle in allowed_handles)
                and len(set(allowed_handles)) == len(allowed_handles)
            )
        )
        if not allowed_valid:
            return CaptureAttempt("STALE", None, snapshot.version)
        phase = snapshot.phase
        assert phase is not None
        deadline_valid = dispatch_deadline is None or (
            (phase.day, phase.phase) == (dispatch_deadline.day, dispatch_deadline.phase)
            and self._deadline_allows_dispatch(dispatch_deadline)
        )
        if not deadline_valid:
            return CaptureAttempt("STALE", None, snapshot.version)
        if (
            report_network_ahead
            and dispatch_deadline is not None
            and allowed_handles is not None
            and actions.world_version == snapshot.version
            and actions.world_last_applied_seq == snapshot.last_applied_seq
            and actions.network_last_seq > actions.world_last_applied_seq
            and all(
                (handle.phase, handle.day, handle.connection_generation,
                 handle.action_generation)
                == (dispatch_deadline.phase, dispatch_deadline.day,
                    dispatch_deadline.connection_generation,
                    dispatch_deadline.action_generation)
                for handle in allowed_handles
            )
        ):
            return CaptureAttempt("NETWORK_AHEAD", None, snapshot.version)
        if not (
            actions.is_caught_up
            and actions.world_version == snapshot.version
            and actions.world_last_applied_seq == snapshot.last_applied_seq
            and actions.network_last_seq == snapshot.last_applied_seq
        ):
            return CaptureAttempt("STALE", None, snapshot.version)
        if any(not isinstance(action, ActionHandle) for action in actions.actions):
            return CaptureAttempt("STALE", None, snapshot.version)
        captured_actions = tuple(actions.actions)
        if allowed_handles is not None:
            if (
                not isinstance(allowed_handles, tuple)
                or not allowed_handles
                or any(not isinstance(handle, ActionHandle) for handle in allowed_handles)
                or len(set(allowed_handles)) != len(allowed_handles)
                or any(handle not in captured_actions for handle in allowed_handles)
            ):
                return CaptureAttempt("STALE", None, snapshot.version)
            allowed = frozenset(allowed_handles)
            captured_actions = tuple(
                handle for handle in captured_actions if handle in allowed
            )
        history = self.world.history()
        co = self.world.co_for_day(phase.day)
        ability_results = self.world.ability_results()
        options = tuple(
            BrainActionOption(f"action:{index}", action)
            for index, action in enumerate(captured_actions)
        )
        discussion: DiscussionCapture | None = None
        if (
            dispatch_deadline is not None
            and dispatch_deadline.discussion_trigger is not None
        ):
            if self._discussion is None:
                raise RuntimeError("Phase 6 capture requires discussion dependencies")
            views = DiscussionViews(
                snapshot=snapshot,
                history=history,
                co=co,
                ability_results=ability_results,
                transport_observations=self.world.transport_observations(),
            )
            discussion = self._discussion.state.capture(
                views, dispatch_deadline.discussion_trigger
            )
        request = BrainInput(
            snapshot=snapshot,
            action_context=BrainActionContext(
                world_version=actions.world_version,
                world_last_applied_seq=actions.world_last_applied_seq,
                network_last_seq=actions.network_last_seq,
                is_caught_up=actions.is_caught_up,
                options=options,
            ),
            history=history,
            co=co,
            ability_results=ability_results,
            discussion=discussion,
        )
        if discussion is not None and not self._discussion_capture_matches(
            request, dispatch_deadline
        ):
            raise RuntimeError("Phase 6 capture does not match its request")
        return CaptureAttempt("CAPTURED", request, snapshot.version)

    def dispatch_context_is_current(
        self,
        *,
        allowed_handles: tuple[ActionHandle, ...],
        dispatch_deadline: DispatchDeadline,
    ) -> bool:
        """Check the received-handle and deadline context without creating input."""

        if not isinstance(dispatch_deadline, DispatchDeadline):
            raise TypeError("dispatch_deadline must be DispatchDeadline")
        if (
            not isinstance(allowed_handles, tuple)
            or not allowed_handles
            or any(not isinstance(handle, ActionHandle) for handle in allowed_handles)
            or len(set(allowed_handles)) != len(allowed_handles)
        ):
            return False
        snapshot = self.world.snapshot()
        if not self._snapshot_is_current(snapshot):
            return False
        phase = snapshot.phase
        assert phase is not None
        if (phase.day, phase.phase) != (
            dispatch_deadline.day,
            dispatch_deadline.phase,
        ):
            return False
        actions = self.world.current_actions()
        if not (
            actions.is_caught_up
            and actions.world_version == snapshot.version
            and actions.world_last_applied_seq == snapshot.last_applied_seq
            and actions.network_last_seq == snapshot.last_applied_seq
            and all(handle in actions.actions for handle in allowed_handles)
        ):
            return False
        return self._deadline_allows_dispatch(dispatch_deadline)

    async def decide_and_send(
        self,
        request: BrainInput,
        *,
        timeout_seconds: float | None = None,
        dispatch_deadline: DispatchDeadline | None = None,
    ) -> DecisionOutcome:
        if not isinstance(request, BrainInput):
            raise TypeError("request must be BrainInput")
        timeout = self._validate_timeout(timeout_seconds)
        if dispatch_deadline is not None and not isinstance(
            dispatch_deadline, DispatchDeadline
        ):
            raise TypeError("dispatch_deadline must be DispatchDeadline when supplied")
        if self._stopping:
            return self._outcome(request, DecisionStatus.CANCELLED)
        if self._unresponsive:
            return self._outcome(
                request,
                DecisionStatus.BRAIN_FAILED,
                error_type="UnresponsiveBrain",
                started=True,
            )
        if self._active is not None:
            raise RuntimeError("BrainController supports only one active invocation")
        if not self._request_is_current(request):
            return self._outcome(request, DecisionStatus.STALE)
        request = self._prepare_discussion_request(request, dispatch_deadline)

        invocation = _Invocation(request)
        self._active = invocation
        invocation.brain_task = asyncio.create_task(self._run_brain(request))
        deadline = asyncio.get_running_loop().time() + timeout
        invocation.world_task = asyncio.create_task(
            self.world.wait_for_update(request.snapshot.version)
        )
        try:
            result = await self._wait_for_decision(
                invocation,
                deadline,
                dispatch_deadline=dispatch_deadline,
            )
            if isinstance(result, _BrainFailure):
                if request.discussion is not None:
                    return await self._abort_before_result(
                        request, result.error, result.status
                    )
                return self._outcome(
                    request,
                    result.status,
                    error_type=type(result.error).__name__,
                    started=True,
                )
            if isinstance(result, DecisionOutcome):
                if request.discussion is not None:
                    raise DiscussionTransactionError(
                        "contextful Brain ended without a durable generation acknowledgement"
                    )
                return result
            if request.discussion is not None:
                return await self._complete_discussion(
                    request,
                    result,
                    dispatch_deadline=dispatch_deadline,
                    cancel_status=invocation.cancel_status,
                )
            decision = result
            if invocation.cancel_status is not None:
                return self._outcome(request, invocation.cancel_status, started=True)
            outcome = self._validate_and_dispatch(request, decision)
            if outcome is not None:
                return outcome
            if dispatch_deadline is not None and not self._deadline_allows_dispatch(
                dispatch_deadline
            ):
                return self._outcome(
                    request,
                    DecisionStatus.DEADLINE_SUPPRESSED,
                    option_id=getattr(decision, "option_id", None),
                    started=True,
                )
            stale = self._stale_before_send(request, decision.option_id)
            if stale:
                return self._outcome(
                    request,
                    DecisionStatus.STALE,
                    option_id=getattr(decision, "option_id", None),
                    started=True,
                )
            return await self._dispatch(request, decision)
        except asyncio.CancelledError:
            invocation.cancel_status = DecisionStatus.CANCELLED
            invocation.terminal = True
            if request.discussion is not None and invocation.discussion_terminal_attempted:
                # The transaction writer owns its sole submission through
                # durability before re-raising cancellation.  Never attempt a
                # second abort or terminal for this capture.
                return self._outcome(
                    request, DecisionStatus.CANCELLED, started=True
                )
            await self._cancel_invocation_tasks(invocation)
            if request.discussion is not None and invocation.brain_task is not None:
                completed = self._completed_after_cancel(
                    invocation.brain_task,
                    DecisionStatus.CANCELLED,
                    require_discussion_evidence=True,
                )
                if isinstance(completed, _BrainFailure):
                    return await self._abort_before_result(
                        request, completed.error, completed.status
                    )
                if completed is not None:
                    return await self._complete_discussion(
                        request,
                        completed,
                        dispatch_deadline=dispatch_deadline,
                        cancel_status=DecisionStatus.CANCELLED,
                    )
                assert self._discussion is not None
                self._discussion.state.abort(
                    request.discussion.capture_id,
                    f"phase6:{request.discussion.capture_id}",
                    DiscussionAbortReason.AUDIT_FAILED,
                    None,
                )
                raise DiscussionTransactionError(
                    "cancelled contextful Brain produced no durable generation evidence"
                )
            return self._outcome(request, DecisionStatus.CANCELLED, started=True)
        finally:
            invocation.terminal = True
            await self._cleanup_invocation(invocation)
            invocation.completed.set()

    def _prepare_discussion_request(
        self,
        request: BrainInput,
        dispatch_deadline: DispatchDeadline | None,
    ) -> BrainInput:
        trigger = (
            None
            if dispatch_deadline is None
            else dispatch_deadline.discussion_trigger
        )
        if request.discussion is not None:
            if self._discussion is None or not self._discussion_capture_matches(
                request, dispatch_deadline
            ):
                raise DiscussionTransactionError(
                    "existing discussion capture does not match dispatch context"
                )
            return request
        if trigger is None:
            return request
        if self._discussion is None or dispatch_deadline is None:
            raise DiscussionTransactionError(
                "Phase 6 direct capture requires discussion dependencies"
            )
        # The non-admission invocation route deliberately captures here.  All
        # authoritative identities are rechecked immediately before the one
        # capture, and an already-populated request never reaches this branch.
        if not self._request_is_current(request) or not self._deadline_allows_dispatch(
            dispatch_deadline
        ):
            raise DiscussionTransactionError(
                "direct discussion capture is no longer current"
            )
        capture = self._discussion.state.capture(
            DiscussionViews(
                snapshot=request.snapshot,
                history=request.history,
                co=request.co,
                ability_results=request.ability_results,
                transport_observations=self.world.transport_observations(),
            ),
            trigger,
        )
        attached = replace(request, discussion=capture)
        if not self._discussion_capture_matches(attached, dispatch_deadline):
            raise DiscussionTransactionError(
                "direct discussion capture does not match its request"
            )
        return attached

    def _discussion_capture_matches(
        self,
        request: BrainInput,
        dispatch_deadline: DispatchDeadline | None,
    ) -> bool:
        capture = request.discussion
        if (
            capture is None
            or dispatch_deadline is None
            or dispatch_deadline.discussion_trigger is None
            or capture.trigger != dispatch_deadline.discussion_trigger
        ):
            return False
        phase = request.snapshot.phase
        return (
            phase is not None
            and capture.world_version == request.snapshot.version
            and capture.last_applied_seq == request.snapshot.last_applied_seq
            and capture.trigger.phase == phase.phase
            and capture.trigger.day == phase.day
            and capture.state.world_version == request.snapshot.version
            and capture.state.last_applied_seq == request.snapshot.last_applied_seq
            and request.snapshot.self_view is not None
            and capture.player_id == request.snapshot.self_view.player_id
        )

    async def _abort_before_result(
        self,
        request: BrainInput,
        error: BaseException,
        outcome_status: DecisionStatus,
    ) -> DecisionOutcome:
        assert request.discussion is not None
        if self._discussion is None:
            raise DiscussionTransactionError("discussion dependencies are unavailable")
        generation = getattr(error, "discussion_ack", None)
        reason = getattr(error, "discussion_abort_reason", None)
        if not isinstance(generation, DiscussionGenerationAck) or not isinstance(
            reason, DiscussionAbortReason
        ):
            # No durable generation evidence means the audit path itself is
            # untrustworthy.  State may be closed, but no terminal can be
            # manufactured through the failed writer.
            self._discussion.state.abort(
                request.discussion.capture_id,
                f"phase6:{request.discussion.capture_id}",
                DiscussionAbortReason.AUDIT_FAILED,
                None,
            )
            raise DiscussionTransactionError(
                "contextful Brain failed without durable generation evidence"
            ) from error
        if reason not in {
            DiscussionAbortReason.PROMPT_REJECTED,
            DiscussionAbortReason.BACKEND_FAILED,
            DiscussionAbortReason.FINAL_OUTPUT_INVALID,
            DiscussionAbortReason.CANCELLED_BEFORE_RESULT,
        }:
            raise DiscussionTransactionError("invalid pre-result abort reason")
        if not self._generation_matches_capture(
            request.discussion, generation, proposal=None
        ):
            self._discussion.state.abort(
                request.discussion.capture_id,
                f"phase6:{request.discussion.capture_id}",
                DiscussionAbortReason.AUDIT_FAILED,
                None,
            )
            raise DiscussionTransactionError(
                "generation acknowledgement does not match capture"
            )
        abort = self._discussion.state.abort(
            request.discussion.capture_id,
            generation.request_id,
            reason,
            None,
        )
        terminal = self._discussion.terminal_from_abort(
            capture=request.discussion,
            generation=generation,
            abort=abort,
            proposal=None,
        )
        await self._write_discussion_terminal(terminal)
        return self._outcome(
            request,
            outcome_status,
            error_type=type(error).__name__,
            started=True,
        )

    async def _write_discussion_terminal(
        self, record: AiDiscussionTerminalRecord
    ) -> object:
        if self._discussion is None:
            raise DiscussionTransactionError("discussion dependencies are unavailable")
        invocation = self._active
        if invocation is None:
            raise DiscussionTransactionError(
                "discussion terminal has no active controller invocation"
            )
        if invocation.discussion_terminal_attempted:
            raise DiscussionTransactionError(
                "controller attempted more than one discussion terminal"
            )
        invocation.discussion_terminal_attempted = True
        return await self._discussion.write_terminal(record)

    async def _complete_discussion(
        self,
        request: BrainInput,
        output: BrainOutput,
        *,
        dispatch_deadline: DispatchDeadline | None,
        cancel_status: DecisionStatus | None,
    ) -> DecisionOutcome:
        assert request.discussion is not None
        if self._discussion is None:
            raise DiscussionTransactionError("discussion dependencies are unavailable")
        if not isinstance(output, BrainResult):
            self._discussion.state.abort(
                request.discussion.capture_id,
                f"phase6:{request.discussion.capture_id}",
                DiscussionAbortReason.AUDIT_FAILED,
                None,
            )
            raise DiscussionTransactionError(
                "contextful Brain must return a complete BrainResult"
            )
        proposal = output.discussion
        generation = output.audit_ack
        if proposal is None or generation is None:
            raise DiscussionTransactionError("BrainResult discussion fields are incomplete")
        decision = output.decision
        if not self._generation_matches_capture(
            request.discussion, generation, proposal=proposal
        ) or not self._discussion_result_matches_request(
            request, decision, proposal
        ):
            return await self._abort_after_generation(
                request,
                generation,
                proposal,
                DiscussionAbortReason.STAGE_FAILED,
                DecisionStatus.INVALID_DECISION,
            )
        if _invalid_or_repeated_self_text(request, decision):
            return await self._abort_after_generation(
                request,
                generation,
                proposal,
                DiscussionAbortReason.STAGE_FAILED,
                DecisionStatus.INVALID_DECISION,
            )
        try:
            stage = self._discussion.state.stage(
                request.discussion,
                generation.request_id,
                proposal,
                generation,
            )
        except Exception as error:
            return await self._abort_after_generation(
                request,
                generation,
                proposal,
                DiscussionAbortReason.STAGE_FAILED,
                DecisionStatus.BRAIN_FAILED,
                error_type=type(error).__name__,
            )

        after_stage_reason: DiscussionAbortReason | None = None
        outcome_status = DecisionStatus.BRAIN_FAILED
        if self._stopping or cancel_status in {
            DecisionStatus.CANCELLED,
            DecisionStatus.TIMED_OUT,
        }:
            after_stage_reason = DiscussionAbortReason.CANCELLED_AFTER_STAGE
            outcome_status = cancel_status or DecisionStatus.CANCELLED
        elif cancel_status is DecisionStatus.STALE:
            after_stage_reason = DiscussionAbortReason.STALE
            outcome_status = DecisionStatus.STALE
        elif cancel_status is DecisionStatus.DEADLINE_SUPPRESSED:
            after_stage_reason = DiscussionAbortReason.DEADLINE
            outcome_status = DecisionStatus.DEADLINE_SUPPRESSED
        elif cancel_status is not None:
            raise DiscussionTransactionError(
                "unexpected contextful cancellation status"
            )
        elif dispatch_deadline is None or not self._deadline_allows_dispatch(
            dispatch_deadline
        ):
            after_stage_reason = DiscussionAbortReason.DEADLINE
            outcome_status = DecisionStatus.DEADLINE_SUPPRESSED
        elif isinstance(decision, NoDecision):
            if not self._request_is_current(request):
                after_stage_reason = DiscussionAbortReason.STALE
                outcome_status = DecisionStatus.STALE
        elif self._stale_before_send(request, decision.option_id):
            after_stage_reason = DiscussionAbortReason.STALE
            outcome_status = DecisionStatus.STALE
        if after_stage_reason is not None:
            return await self._abort_after_generation(
                request,
                generation,
                proposal,
                after_stage_reason,
                outcome_status,
            )

        try:
            commit = self._discussion.state.commit(stage)
        except Exception as error:
            return await self._abort_after_generation(
                request,
                generation,
                proposal,
                DiscussionAbortReason.REVISION_CONFLICT,
                DecisionStatus.BRAIN_FAILED,
                error_type=type(error).__name__,
            )

        if isinstance(decision, NoDecision):
            delivery = self._discussion.state.finish_no_action(commit)
            terminal = self._discussion.terminal_from_delivery(
                capture=request.discussion,
                generation=generation,
                proposal=proposal,
                delivery=delivery,
            )
            await self._write_discussion_terminal(terminal)
            return self._outcome(
                request, DecisionStatus.NO_DECISION, started=True
            )

        # There is deliberately no await between the successful commit and
        # marking dispatch as started.
        dispatch = self._discussion.state.mark_dispatch_started(
            commit, proposal.decision_kind, decision.option_id
        )
        return await self._dispatch_discussion(
            request,
            decision,
            generation=generation,
            proposal=proposal,
            dispatch=dispatch,
        )

    async def _abort_after_generation(
        self,
        request: BrainInput,
        generation: DiscussionGenerationAck,
        proposal: DiscussionProposal,
        reason: DiscussionAbortReason,
        outcome_status: DecisionStatus,
        *,
        error_type: str | None = None,
    ) -> DecisionOutcome:
        assert request.discussion is not None and self._discussion is not None
        proposal_sha256 = canonical_sha256(proposal)
        abort = self._discussion.state.abort(
            request.discussion.capture_id,
            generation.request_id,
            reason,
            proposal_sha256,
        )
        terminal = self._discussion.terminal_from_abort(
            capture=request.discussion,
            generation=generation,
            abort=abort,
            proposal=proposal,
        )
        await self._write_discussion_terminal(terminal)
        return self._outcome(
            request,
            outcome_status,
            option_id=proposal.option_id,
            error_type=error_type,
            started=True,
        )

    @staticmethod
    def _generation_matches_capture(
        capture: DiscussionCapture,
        generation: DiscussionGenerationAck,
        *,
        proposal: DiscussionProposal | None,
    ) -> bool:
        return (
            generation.capture_id == capture.capture_id
            and generation.request_id == f"phase6:{capture.capture_id}"
            and generation.context_sha256 == capture.context_sha256
            and generation.before_state_sha256 == capture.state_sha256
            and generation.after_state_sha256 is None
            and generation.proposal_sha256
            == (None if proposal is None else canonical_sha256(proposal))
            and generation.durable is True
        )

    def _discussion_result_matches_request(
        self,
        request: BrainInput,
        decision: BrainDecision,
        proposal: DiscussionProposal,
    ) -> bool:
        if proposal.base_revision != request.discussion.base_revision:  # type: ignore[union-attr]
            return False
        if isinstance(decision, CoReportDecision) or not isinstance(
            decision, _DECISION_TYPES
        ):
            return False
        kind, option_id = brain_decision_identity(decision)
        if proposal.decision_kind != kind or proposal.option_id != option_id:
            return False
        if isinstance(decision, NoDecision):
            return True
        if isinstance(decision, VoteDecision) and decision.target_player_id is None:
            return False
        option_map = {
            option.option_id: option
            for option in request.action_context.options
            if isinstance(option, BrainActionOption)
        }
        option = option_map.get(option_id)
        return (
            option is not None
            and len(option_map) == len(request.action_context.options)
            and self._decision_matches_handle(decision, option.handle)
        )

    async def finalize_discussion_observation(
        self,
        *,
        correlation: DiscussionDispatchCorrelation,
        status: DiscussionObservationStatus,
        reason: DiscussionTerminalReason,
        evidence: EvidenceRef | None = None,
    ) -> ObservationAck:
        """Durably finish the sole retained contextful local send.

        The owning :class:`BrainInvocationArbiter` is the only production
        caller.  Validation and the attempt transition are deliberately
        synchronous before the terminal writer's first await.
        """

        self._validate_discussion_observation(
            correlation=correlation,
            status=status,
            reason=reason,
            evidence=evidence,
        )
        key = (status, reason, evidence)
        completed = self._completed_discussion_observation
        if completed is not None and completed.correlation == correlation:
            if (completed.status, completed.reason, completed.evidence) != key:
                raise DiscussionTransactionError(
                    "conflicting duplicate discussion observation"
                )
            return completed.acknowledgement

        material = self._discussion_observation
        if material is None or material.correlation != correlation:
            raise DiscussionTransactionError(
                "observation correlation is not the exact retained local send"
            )
        if material.attempt is not None:
            raise DiscussionTransactionError(
                "discussion observation finalization was already attempted"
            )
        if self._discussion is None:
            raise DiscussionTransactionError("discussion dependencies are unavailable")

        # This request-local ownership transition must precede state mutation,
        # task construction, and the writer's first await.  Failure never
        # re-authorizes the capture for another terminal submission.
        material.attempt = key
        try:
            observation = self._discussion.state.observe_authoritative(
                correlation,
                status,
                evidence,
            )
            terminal = self._discussion.terminal_from_observation(
                capture=material.capture,
                generation=material.generation,
                proposal=material.proposal,
                observation=observation,
                reason=reason,
            )
            await self._discussion.write_terminal(terminal)
        except BaseException:
            self._unresponsive = True
            raise

        self._completed_discussion_observation = _CompletedDiscussionObservation(
            correlation=correlation,
            status=status,
            reason=reason,
            evidence=evidence,
            acknowledgement=observation,
        )
        self._discussion_observation = None
        return observation

    @staticmethod
    def _validate_discussion_observation(
        *,
        correlation: object,
        status: object,
        reason: object,
        evidence: object,
    ) -> None:
        if not isinstance(correlation, DiscussionDispatchCorrelation):
            raise TypeError("correlation must be DiscussionDispatchCorrelation")
        if not isinstance(status, DiscussionObservationStatus):
            raise TypeError("status must be DiscussionObservationStatus")
        if not isinstance(reason, DiscussionTerminalReason):
            raise TypeError("reason must be DiscussionTerminalReason")
        if evidence is not None and not isinstance(evidence, EvidenceRef):
            raise TypeError("evidence must be EvidenceRef when supplied")

        allowed = {
            DiscussionObservationStatus.ACCEPTED: {
                DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED
            },
            DiscussionObservationStatus.REJECTED: {
                DiscussionTerminalReason.AUTHORITATIVE_REJECTED
            },
            DiscussionObservationStatus.RECOVERY_UNKNOWN: {
                DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
                DiscussionTerminalReason.RECOVERY_GAP,
                DiscussionTerminalReason.PHASE_CHANGED,
                DiscussionTerminalReason.OWNER_STOPPED,
            },
        }
        if reason not in allowed[status]:
            raise DiscussionTransactionError(
                "discussion observation status/reason mismatch"
            )
        if status in {
            DiscussionObservationStatus.ACCEPTED,
            DiscussionObservationStatus.REJECTED,
        } and evidence is None:
            raise DiscussionTransactionError(
                "accepted/rejected discussion observation requires evidence"
            )
        if reason is DiscussionTerminalReason.OWNER_STOPPED and evidence is not None:
            raise DiscussionTransactionError("OWNER_STOPPED observation forbids evidence")
        if (
            reason is DiscussionTerminalReason.RECOVERY_GAP
            and evidence is not None
            and evidence.record_kind
            is not EvidenceRecordKind.RESUME_RECOVERY_BARRIER
        ):
            raise DiscussionTransactionError(
                "RECOVERY_GAP evidence must be a recovery barrier"
            )
        if (
            reason is DiscussionTerminalReason.PHASE_CHANGED
            and evidence is not None
            and evidence.record_kind
            not in {EvidenceRecordKind.PHASE_TRANSITION, EvidenceRecordKind.PHASE_TIMING}
        ):
            raise DiscussionTransactionError(
                "PHASE_CHANGED evidence must be a phase observation"
            )

        if status is DiscussionObservationStatus.ACCEPTED:
            expected = {
                "chat": EvidenceRecordKind.CHAT,
                "co_declare": EvidenceRecordKind.CO_DECLARATION,
                "vote": EvidenceRecordKind.ACTION_ACCEPTED,
                "ability": EvidenceRecordKind.ACTION_ACCEPTED,
            }[correlation.action]
            if evidence.record_kind is not expected:  # type: ignore[union-attr]
                raise DiscussionTransactionError(
                    "acceptance evidence kind does not match action"
                )
        elif (
            status is DiscussionObservationStatus.REJECTED
            and evidence.record_kind is not EvidenceRecordKind.ACTION_REJECTION  # type: ignore[union-attr]
        ):
            raise DiscussionTransactionError(
                "rejection evidence must be an action rejection"
            )

    async def stop(self) -> None:
        """Permanently close new invocations and cancel the active Brain task."""

        self._stopping = True
        invocation = self._active
        if invocation is not None:
            invocation.cancel_status = DecisionStatus.CANCELLED
            invocation.terminal = True
            await self._cancel_invocation_tasks(invocation)
            # The decide owner may still be aborting/finalizing its one terminal
            # after the Brain task completes.  Do not clear the transaction's
            # lifetime reservation authority until that ownership is finished.
            await invocation.completed.wait()
        if self._discussion is not None:
            material = self._discussion_observation
            if material is not None and material.attempt is None:
                raise DiscussionTransactionError(
                    "LOCAL_SENT correlation requires outer finalization before close"
                )
            self._discussion.close()

    async def _run_brain(self, request: BrainInput) -> BrainOutput:
        return await self.brain.decide(request)

    async def _wait_for_decision(
        self,
        invocation: _Invocation,
        deadline: float,
        *,
        dispatch_deadline: DispatchDeadline | None,
    ) -> BrainOutput | DecisionOutcome | _BrainFailure:
        assert invocation.brain_task is not None
        assert invocation.world_task is not None
        brain_task = invocation.brain_task
        world_task = invocation.world_task
        while True:
            if dispatch_deadline is not None and not self._deadline_allows_dispatch(
                dispatch_deadline
            ):
                return await self._cancel_for_deadline(invocation, brain_task)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                invocation.cancel_status = DecisionStatus.TIMED_OUT
                invocation.terminal = True
                await self._cancel_brain_task(
                    brain_task,
                    preserve_discussion_evidence=(
                        invocation.request.discussion is not None
                    ),
                )
                completed = self._completed_after_cancel(
                    brain_task,
                    DecisionStatus.TIMED_OUT,
                    require_discussion_evidence=(
                        invocation.request.discussion is not None
                    ),
                )
                if completed is not None:
                    return completed
                return self._outcome(
                    invocation.request, DecisionStatus.TIMED_OUT, started=True
                )
            cutoff_remaining = (
                None
                if dispatch_deadline is None
                else dispatch_deadline.not_after_monotonic - self._clock()
            )
            wait_timeout = (
                remaining
                if cutoff_remaining is None
                else min(remaining, max(0.0, cutoff_remaining))
            )
            done, _pending = await asyncio.wait(
                {brain_task, world_task},
                timeout=wait_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if dispatch_deadline is not None and (
                (
                    not done
                    and cutoff_remaining is not None
                    and cutoff_remaining <= remaining
                )
                or not self._deadline_allows_dispatch(dispatch_deadline)
            ):
                return await self._cancel_for_deadline(invocation, brain_task)
            if not done:
                invocation.cancel_status = DecisionStatus.TIMED_OUT
                invocation.terminal = True
                await self._cancel_brain_task(
                    brain_task,
                    preserve_discussion_evidence=(
                        invocation.request.discussion is not None
                    ),
                )
                completed = self._completed_after_cancel(
                    brain_task,
                    DecisionStatus.TIMED_OUT,
                    require_discussion_evidence=(
                        invocation.request.discussion is not None
                    ),
                )
                if completed is not None:
                    return completed
                return self._outcome(
                    invocation.request, DecisionStatus.TIMED_OUT, started=True
                )
            if world_task in done and brain_task in done:
                if invocation.request.discussion is not None:
                    try:
                        return brain_task.result()
                    except asyncio.CancelledError as error:
                        return _BrainFailure(
                            error,
                            invocation.cancel_status or DecisionStatus.CANCELLED,
                        )
                    except Exception as error:
                        return _BrainFailure(error, DecisionStatus.BRAIN_FAILED)
                try:
                    world_snapshot = world_task.result()
                except Exception:
                    world_snapshot = None
                if world_snapshot is None or not self._snapshot_allows_invocation(
                    invocation.request.snapshot, world_snapshot
                ):
                    invocation.cancel_status = DecisionStatus.STALE
                    invocation.terminal = True
                    await self._cancel_brain_task(
                        brain_task,
                        preserve_discussion_evidence=(
                            invocation.request.discussion is not None
                        ),
                    )
                    return self._outcome(
                        invocation.request, DecisionStatus.STALE, started=True
                    )
            if brain_task in done:
                try:
                    return brain_task.result()
                except asyncio.CancelledError:
                    status = invocation.cancel_status or DecisionStatus.CANCELLED
                    return _BrainFailure(asyncio.CancelledError(), status)
                except Exception as error:
                    return _BrainFailure(error, DecisionStatus.BRAIN_FAILED)
            try:
                world_snapshot = world_task.result()
            except Exception:
                invocation.cancel_status = DecisionStatus.STALE
                invocation.terminal = True
                await self._cancel_brain_task(
                    brain_task,
                    preserve_discussion_evidence=(
                        invocation.request.discussion is not None
                    ),
                )
                completed = self._completed_after_cancel(
                    brain_task,
                    DecisionStatus.STALE,
                    require_discussion_evidence=(
                        invocation.request.discussion is not None
                    ),
                )
                if completed is not None:
                    return completed
                return self._outcome(
                    invocation.request, DecisionStatus.STALE, started=True
                )
            if not self._snapshot_allows_invocation(
                invocation.request.snapshot, world_snapshot
            ):
                invocation.cancel_status = DecisionStatus.STALE
                invocation.terminal = True
                await self._cancel_brain_task(
                    brain_task,
                    preserve_discussion_evidence=(
                        invocation.request.discussion is not None
                    ),
                )
                completed = self._completed_after_cancel(
                    brain_task,
                    DecisionStatus.STALE,
                    require_discussion_evidence=(
                        invocation.request.discussion is not None
                    ),
                )
                if completed is not None:
                    return completed
                return self._outcome(
                    invocation.request, DecisionStatus.STALE, started=True
                )
            world_task = asyncio.create_task(
                self.world.wait_for_update(world_snapshot.version)
            )
            invocation.world_task = world_task

    async def _cancel_for_deadline(
        self,
        invocation: _Invocation,
        brain_task: asyncio.Task[BrainOutput],
    ) -> DecisionOutcome | _BrainFailure:
        invocation.cancel_status = DecisionStatus.DEADLINE_SUPPRESSED
        invocation.terminal = True
        await self._cancel_brain_task(
            brain_task,
            preserve_discussion_evidence=(
                invocation.request.discussion is not None
            ),
        )
        completed = self._completed_after_cancel(
            brain_task,
            DecisionStatus.DEADLINE_SUPPRESSED,
            require_discussion_evidence=(
                invocation.request.discussion is not None
            ),
        )
        if completed is not None:
            return completed
        return self._outcome(
            invocation.request,
            DecisionStatus.DEADLINE_SUPPRESSED,
            started=True,
        )

    def _validate_and_dispatch(
        self, request: BrainInput, decision: object
    ) -> DecisionOutcome | None:
        if not isinstance(decision, _DECISION_TYPES):
            return self._outcome(
                request, DecisionStatus.INVALID_DECISION, started=True
            )
        if isinstance(decision, NoDecision):
            return self._outcome(request, DecisionStatus.NO_DECISION, started=True)
        options = request.action_context.options
        option_map: dict[str, BrainActionOption] = {}
        for option in options:
            if (
                not isinstance(option, BrainActionOption)
                or option.option_id in option_map
                or not isinstance(option.handle, ActionHandle)
            ):
                return self._outcome(
                    request, DecisionStatus.INVALID_DECISION, started=True
                )
            option_map[option.option_id] = option
        option_id = getattr(decision, "option_id", None)
        option = option_map.get(option_id) if isinstance(option_id, str) else None
        if option is None:
            return self._outcome(
                request,
                DecisionStatus.INVALID_DECISION,
                option_id=option_id if isinstance(option_id, str) else None,
                started=True,
            )
        handle = option.handle
        valid = self._decision_matches_handle(decision, handle)
        if not valid:
            return self._outcome(
                request,
                DecisionStatus.INVALID_DECISION,
                option_id=option_id,
                started=True,
            )
        return None

    def _decision_matches_handle(
        self, decision: BrainDecision, handle: ActionHandle
    ) -> bool:
        if isinstance(decision, ChatDecision):
            return (
                isinstance(handle, ChatAction)
                and handle.type == "chat"
                and self._non_empty_string(decision.message)
            )
        if isinstance(decision, VoteDecision):
            if not isinstance(handle, VoteAction) or handle.type != "vote":
                return False
            target = decision.target_player_id
            if target is None:
                return handle.allows_abstain
            return isinstance(target, str) and target in handle.valid_targets
        if isinstance(decision, AbilityDecision):
            if not isinstance(handle, AbilityAction) or handle.type != "ability":
                return False
            targets = decision.target_player_ids
            return (
                isinstance(targets, tuple)
                and all(isinstance(target, str) for target in targets)
                and len(targets) == handle.target_count
                and len(set(targets)) == len(targets)
                and all(target in handle.valid_targets for target in targets)
            )
        if isinstance(decision, CoDeclareDecision):
            return (
                isinstance(handle, CoDeclareAction)
                and handle.type == "co_declare"
                and isinstance(decision.claimed_role_id, str)
                and self._non_empty_string(decision.claimed_role_id)
                and decision.claimed_role_id in handle.claimed_role_ids
                and self._non_empty_string(decision.comment)
            )
        if isinstance(decision, CoReportDecision):
            return (
                isinstance(handle, CoReportAction)
                and handle.type == "co_report"
                and self._non_empty_string(decision.kind)
                and self._non_empty_string(decision.target_player_id)
                and self._non_empty_string(decision.claimed_result)
            )
        return False

    async def _dispatch_discussion(
        self,
        request: BrainInput,
        decision: BrainDecision,
        *,
        generation: DiscussionGenerationAck,
        proposal: DiscussionProposal,
        dispatch: object,
    ) -> DecisionOutcome:
        assert request.discussion is not None and self._discussion is not None
        option = next(
            item
            for item in request.action_context.options
            if item.option_id == getattr(decision, "option_id", None)
        )
        handle = option.handle
        try:
            if isinstance(decision, ChatDecision) and isinstance(handle, ChatAction):
                receipt = await self.sender.send_chat(handle, decision.message)
            elif isinstance(decision, VoteDecision) and isinstance(handle, VoteAction):
                receipt = await self.sender.send_vote(handle, decision.target_player_id)
            elif isinstance(decision, AbilityDecision) and isinstance(handle, AbilityAction):
                receipt = await self.sender.send_ability(
                    handle, decision.target_player_ids
                )
            elif isinstance(decision, CoDeclareDecision) and isinstance(
                handle, CoDeclareAction
            ):
                receipt = await self.sender.send_co_declare(
                    handle, decision.claimed_role_id, decision.comment
                )
            else:
                raise DiscussionTransactionError(
                    "contextful decision/handle identity changed before send"
                )
        except asyncio.CancelledError:
            return await self._finish_discussion_send_failure(
                request,
                decision,
                handle,
                generation=generation,
                proposal=proposal,
                dispatch=dispatch,
                delivery_status=DiscussionDeliveryStatus.DELIVERY_UNKNOWN,
                outcome_status=DecisionStatus.SEND_DELIVERY_UNKNOWN,
                error_type="CancelledError",
            )
        except (StaleActionError, NotDeliveredError) as error:
            return await self._finish_discussion_send_failure(
                request,
                decision,
                handle,
                generation=generation,
                proposal=proposal,
                dispatch=dispatch,
                delivery_status=DiscussionDeliveryStatus.NOT_DELIVERED,
                outcome_status=DecisionStatus.SEND_NOT_DELIVERED,
                error_type=type(error).__name__,
            )
        except DeliveryUnknownError as error:
            return await self._finish_discussion_send_failure(
                request,
                decision,
                handle,
                generation=generation,
                proposal=proposal,
                dispatch=dispatch,
                delivery_status=DiscussionDeliveryStatus.DELIVERY_UNKNOWN,
                outcome_status=DecisionStatus.SEND_DELIVERY_UNKNOWN,
                error_type=type(error).__name__,
                request_event_id=error.request_event_id,
                attempt_action=error.action,
                send_connection_generation=error.connection_generation,
            )
        except Exception as error:
            return await self._finish_discussion_send_failure(
                request,
                decision,
                handle,
                generation=generation,
                proposal=proposal,
                dispatch=dispatch,
                delivery_status=DiscussionDeliveryStatus.DELIVERY_UNKNOWN,
                outcome_status=DecisionStatus.SEND_DELIVERY_UNKNOWN,
                error_type=type(error).__name__,
            )

        if not isinstance(receipt, SendReceipt):
            return await self._finish_discussion_send_failure(
                request,
                decision,
                handle,
                generation=generation,
                proposal=proposal,
                dispatch=dispatch,
                delivery_status=DiscussionDeliveryStatus.DELIVERY_UNKNOWN,
                outcome_status=DecisionStatus.SEND_DELIVERY_UNKNOWN,
                error_type="InvalidSendReceipt",
            )
        delivery = self._discussion.state.finish_dispatch(
            dispatch,
            DiscussionDeliveryStatus.LOCAL_SENT,
            receipt,
        )
        if not isinstance(delivery.correlation, DiscussionDispatchCorrelation):
            raise DiscussionTransactionError(
                "LOCAL_SENT did not return an exact discussion correlation"
            )
        self._retain_discussion_observation(
            capture=request.discussion,
            generation=generation,
            proposal=proposal,
            correlation=delivery.correlation,
        )
        self._last_dispatched_decision = (receipt, decision)
        return self._outcome(
            request,
            DecisionStatus.SENT,
            option_id=getattr(decision, "option_id", None),
            receipt=receipt,
            started=True,
            discussion=delivery.correlation,
        )

    def _retain_discussion_observation(
        self,
        *,
        capture: DiscussionCapture,
        generation: DiscussionGenerationAck,
        proposal: DiscussionProposal,
        correlation: DiscussionDispatchCorrelation,
    ) -> None:
        if self._discussion_observation is not None:
            raise DiscussionTransactionError(
                "a prior local send still requires authoritative observation"
            )
        expected = {
            "capture_id": capture.capture_id,
            "request_id": generation.request_id,
            "context_sha256": capture.context_sha256,
            "before_state_sha256": capture.state_sha256,
            "proposal_sha256": canonical_sha256(proposal),
            "generation_audit_sequence": generation.audit_sequence,
            "base_revision": capture.base_revision,
            "committed_revision": capture.base_revision + 1,
            "action": proposal.decision_kind,
            "option_id": proposal.option_id,
        }
        if any(getattr(correlation, name) != value for name, value in expected.items()):
            raise DiscussionTransactionError(
                "LOCAL_SENT correlation does not match retained discussion material"
            )
        self._completed_discussion_observation = None
        self._discussion_observation = _DiscussionObservationMaterial(
            capture=capture,
            generation=generation,
            proposal=proposal,
            correlation=correlation,
        )

    async def _finish_discussion_send_failure(
        self,
        request: BrainInput,
        decision: BrainDecision,
        handle: ActionHandle,
        *,
        generation: DiscussionGenerationAck,
        proposal: DiscussionProposal,
        dispatch: object,
        delivery_status: DiscussionDeliveryStatus,
        outcome_status: DecisionStatus,
        error_type: str,
        request_event_id: str | None = None,
        attempt_action: str | None = None,
        send_connection_generation: int | None = None,
    ) -> DecisionOutcome:
        assert request.discussion is not None and self._discussion is not None
        delivery = self._discussion.state.finish_dispatch(
            dispatch, delivery_status, None
        )
        terminal = self._discussion.terminal_from_delivery(
            capture=request.discussion,
            generation=generation,
            proposal=proposal,
            delivery=delivery,
        )
        await self._write_discussion_terminal(terminal)
        if outcome_status is DecisionStatus.SEND_DELIVERY_UNKNOWN:
            return self._delivery_unknown_outcome(
                request,
                decision,
                handle,
                error_type=error_type,
                request_event_id=request_event_id,
                attempt_action=attempt_action,
                send_connection_generation=send_connection_generation,
            )
        return self._outcome(
            request,
            outcome_status,
            option_id=getattr(decision, "option_id", None),
            error_type=error_type,
            started=True,
        )

    async def _dispatch(self, request: BrainInput, decision: BrainDecision) -> DecisionOutcome:
        if self._stopping:
            return self._outcome(
                request,
                DecisionStatus.CANCELLED,
                option_id=getattr(decision, "option_id", None),
                started=True,
            )
        option = next(
            option
            for option in request.action_context.options
            if option.option_id == getattr(decision, "option_id", None)
        )
        handle = option.handle
        try:
            if isinstance(decision, ChatDecision) and isinstance(handle, ChatAction):
                receipt = await self.sender.send_chat(handle, decision.message)
            elif isinstance(decision, VoteDecision) and isinstance(handle, VoteAction):
                receipt = await self.sender.send_vote(handle, decision.target_player_id)
            elif isinstance(decision, AbilityDecision) and isinstance(handle, AbilityAction):
                receipt = await self.sender.send_ability(handle, decision.target_player_ids)
            elif isinstance(decision, CoDeclareDecision) and isinstance(handle, CoDeclareAction):
                receipt = await self.sender.send_co_declare(
                    handle, decision.claimed_role_id, decision.comment
                )
            elif isinstance(decision, CoReportDecision) and isinstance(handle, CoReportAction):
                receipt = await self.sender.send_co_report(
                    handle,
                    decision.kind,
                    decision.target_player_id,
                    decision.claimed_result,
                )
            else:
                return self._outcome(
                    request,
                    DecisionStatus.INVALID_DECISION,
                    option_id=getattr(decision, "option_id", None),
                    started=True,
                )
        except StaleActionError as error:
            return self._outcome(
                request,
                DecisionStatus.STALE,
                option_id=getattr(decision, "option_id", None),
                error_type=type(error).__name__,
                started=True,
            )
        except NotDeliveredError as error:
            return self._outcome(
                request,
                DecisionStatus.SEND_NOT_DELIVERED,
                option_id=getattr(decision, "option_id", None),
                error_type=type(error).__name__,
                started=True,
            )
        except DeliveryUnknownError as error:
            return self._delivery_unknown_outcome(
                request,
                decision,
                handle,
                error_type=type(error).__name__,
                request_event_id=error.request_event_id,
                attempt_action=error.action,
                send_connection_generation=error.connection_generation,
            )
        except Exception as error:
            return self._delivery_unknown_outcome(
                request,
                decision,
                handle,
                error_type=type(error).__name__,
            )
        if not isinstance(receipt, SendReceipt):
            return self._delivery_unknown_outcome(
                request,
                decision,
                handle,
                error_type="InvalidSendReceipt",
            )
        self._last_dispatched_decision = (receipt, decision)
        return self._outcome(
            request,
            DecisionStatus.SENT,
            option_id=getattr(decision, "option_id", None),
            receipt=receipt,
            started=True,
        )

    def _delivery_unknown_outcome(
        self,
        request: BrainInput,
        decision: BrainDecision,
        handle: ActionHandle,
        *,
        error_type: str,
        request_event_id: str | None = None,
        attempt_action: str | None = None,
        send_connection_generation: int | None = None,
    ) -> DecisionOutcome:
        action_by_handle = {
            ChatAction: "chat.send",
            VoteAction: "vote.cast",
            AbilityAction: "ability.use",
            CoDeclareAction: "co.declare",
            CoReportAction: "co.report",
        }
        derived_action = next(
            action
            for handle_type, action in action_by_handle.items()
            if isinstance(handle, handle_type)
        )
        if attempt_action is not None and attempt_action != derived_action:
            raise RuntimeError("delivery-unknown action identity does not match the decision")
        return self._outcome(
            request,
            DecisionStatus.SEND_DELIVERY_UNKNOWN,
            option_id=getattr(decision, "option_id", None),
            error_type=error_type,
            started=True,
            request_event_id=request_event_id,
            attempt_action=attempt_action or derived_action,
            send_connection_generation=send_connection_generation,
            vote_target_player_id=(
                decision.target_player_id if isinstance(decision, VoteDecision) else None
            ),
            ability_id=handle.ability_id if isinstance(handle, AbilityAction) else None,
            ability_target_player_ids=(
                decision.target_player_ids
                if isinstance(decision, AbilityDecision)
                else ()
            ),
        )

    def take_dispatched_decision(
        self, receipt: SendReceipt | None
    ) -> BrainDecision | None:
        """Return the matching successful dispatch decision exactly once."""

        dispatched = self._last_dispatched_decision
        if receipt is None or dispatched is None or dispatched[0] != receipt:
            return None
        self._last_dispatched_decision = None
        return dispatched[1]

    def _snapshot_is_current(self, snapshot: WorldSnapshot) -> bool:
        return (
            snapshot.freshness is Freshness.CURRENT
            and snapshot.is_caught_up
            and snapshot.phase is not None
            and snapshot.complete
        )

    def _snapshot_allows_invocation(
        self, initial: WorldSnapshot, current: WorldSnapshot
    ) -> bool:
        return (
            self._snapshot_is_current(current)
            and initial.phase is not None
            and current.phase is not None
            and (initial.phase.day, initial.phase.phase)
            == (current.phase.day, current.phase.phase)
        )

    def _request_is_current(self, request: BrainInput) -> bool:
        if not self._request_matches_capture_contract(request):
            return False
        snapshot = self.world.snapshot()
        if not self._snapshot_allows_invocation(request.snapshot, snapshot):
            return False
        actions = self.world.current_actions()
        return (
            actions.is_caught_up
            and actions.world_version == snapshot.version
            and actions.world_last_applied_seq == snapshot.last_applied_seq
            and actions.network_last_seq == snapshot.last_applied_seq
        )

    def _request_matches_capture_contract(self, request: BrainInput) -> bool:
        snapshot = request.snapshot
        context = request.action_context
        if not self._snapshot_is_current(snapshot):
            return False
        if not (
            context.is_caught_up
            and context.world_version == snapshot.version
            and context.world_last_applied_seq == snapshot.last_applied_seq
            and context.network_last_seq == snapshot.last_applied_seq
        ):
            return False
        option_ids: set[str] = set()
        for index, option in enumerate(context.options):
            if not isinstance(option, BrainActionOption):
                return False
            if option.option_id in option_ids:
                return False
            if option.option_id != f"action:{index}":
                return False
            option_ids.add(option.option_id)
        return True

    def _stale_before_send(self, request: BrainInput, option_id: str) -> bool:
        if self._stopping:
            return True
        snapshot = self.world.snapshot()
        if not self._snapshot_allows_invocation(request.snapshot, snapshot):
            return True
        actions = self.world.current_actions()
        if not (
            actions.is_caught_up
            and actions.world_version == snapshot.version
            and actions.world_last_applied_seq == snapshot.last_applied_seq
            and actions.network_last_seq == snapshot.last_applied_seq
        ):
            return True
        selected = next(
            option
            for option in request.action_context.options
            if option.option_id == option_id
        )
        return selected.handle not in actions.actions

    def _deadline_allows_dispatch(self, deadline: DispatchDeadline) -> bool:
        view = self.world.transport_observations()
        current = view.current_deadline
        return (
            current is not None
            and current.mapping_order == deadline.mapping_order
            and current.phase == deadline.phase
            and current.day == deadline.day
            and current.connection_generation == deadline.connection_generation
            and current.action_generation == deadline.action_generation
            and current.local_deadline_monotonic is not None
            and self._clock() < deadline.not_after_monotonic
        )

    def _validate_timeout(self, timeout_seconds: float | None) -> float:
        timeout = (
            self.config.max_decision_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
            or timeout > self.config.max_decision_seconds
        ):
            raise ValueError(
                "timeout_seconds must be positive, finite, and no greater than max_decision_seconds"
            )
        return float(timeout)

    def _outcome(
        self,
        request: BrainInput,
        status: DecisionStatus,
        *,
        option_id: str | None = None,
        receipt: SendReceipt | None = None,
        error_type: str | None = None,
        started: bool = False,
        request_event_id: str | None = None,
        attempt_action: str | None = None,
        send_connection_generation: int | None = None,
        vote_target_player_id: str | None = None,
        ability_id: str | None = None,
        ability_target_player_ids: tuple[str, ...] = (),
        discussion: DiscussionDispatchCorrelation | None = None,
    ) -> DecisionOutcome:
        phase = request.snapshot.phase
        return DecisionOutcome(
            status=status,
            request_version=request.snapshot.version,
            phase=phase.phase if phase is not None else None,
            day=phase.day if phase is not None else None,
            option_id=option_id,
            receipt=receipt,
            error_type=error_type,
            invocation_started=started,
            request_event_id=request_event_id,
            attempt_action=attempt_action,
            send_connection_generation=send_connection_generation,
            vote_target_player_id=vote_target_player_id,
            ability_id=ability_id,
            ability_target_player_ids=ability_target_player_ids,
            discussion=discussion,
        )

    async def _cancel_invocation_tasks(self, invocation: _Invocation) -> None:
        brain_task = invocation.brain_task
        if brain_task is not None:
            await self._cancel_brain_task(
                brain_task,
                preserve_discussion_evidence=(
                    invocation.request.discussion is not None
                ),
            )
        world_task = invocation.world_task
        if world_task is not None:
            await self._cancel_task_with_grace(world_task)

    async def _cancel_brain_task(
        self,
        task: asyncio.Task[Any],
        *,
        preserve_discussion_evidence: bool = False,
    ) -> None:
        if preserve_discussion_evidence:
            # A contextful Brain owns its generation append through durable
            # acknowledgement.  The legacy grace period cannot be used here:
            # timing out that wait would let the Controller close the request
            # without the generation evidence required to link its terminal.
            if not task.done():
                task.cancel()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    # A further caller cancellation must not abandon an append
                    # which may already be durable.  If the child itself is now
                    # cancelled, ``done`` closes the loop below.
                    continue
                except Exception:
                    break
            self._consume_task(task)
            return
        completed = await self._cancel_task_with_grace(task)
        if not completed:
            self._unresponsive = True

    @staticmethod
    def _completed_after_cancel(
        task: asyncio.Task[Any],
        status: DecisionStatus,
        *,
        require_discussion_evidence: bool = False,
    ) -> BrainOutput | _BrainFailure | None:
        if not task.done():
            return None
        if task.cancelled():
            return (
                _BrainFailure(asyncio.CancelledError(), status)
                if require_discussion_evidence
                else None
            )
        try:
            result = task.result()
        except asyncio.CancelledError:
            return (
                _BrainFailure(asyncio.CancelledError(), status)
                if require_discussion_evidence
                else None
            )
        except Exception as error:
            return _BrainFailure(error, status)
        return result

    async def _cancel_task_with_grace(self, task: asyncio.Task[Any]) -> bool:
        if task.done():
            self._consume_task(task)
            return True
        task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.shield(task), self.config.cancellation_grace_seconds
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:
            self._consume_task(task)
        if task.done():
            self._consume_task(task)
        else:
            task.add_done_callback(self._consume_task)
        return task.done()

    async def _cleanup_invocation(self, invocation: _Invocation) -> None:
        if self._active is invocation:
            self._active = None
        world_task = invocation.world_task
        if world_task is not None and not world_task.done():
            world_task.cancel()
        if world_task is not None:
            self._consume_task(world_task)

    @staticmethod
    def _consume_task(task: asyncio.Future[Any]) -> None:
        with suppress(asyncio.CancelledError, Exception):
            task.result()

    @staticmethod
    def _non_empty_string(value: object) -> bool:
        return isinstance(value, str) and bool(value)
