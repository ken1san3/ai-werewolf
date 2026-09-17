from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import fields, replace
from datetime import datetime, timezone
from functools import wraps
import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from ai_client.brain import (
    AbilityDecision,
    BrainActionContext,
    BrainActionOption,
    BrainController,
    BrainInput,
    BrainInvocationArbiter,
    BrainInvocationPriority,
    BrainResult,
    BrainRunConfig,
    ChatDecision,
    CoDeclareDecision,
    DecisionStatus,
    DispatchDeadline,
    NoDecision,
    VoteDecision,
)
from ai_client.discussion.context import (
    AuthorizedChatChannelContext,
    AuthorizedDiscussionContext,
    CountParityWinCondition,
    canonical_json_bytes,
    canonical_sha256,
)
from ai_client.discussion.model import (
    AbortAck,
    AiDiscussionGenerationStatus,
    CommitAck,
    DeliveryAck,
    DispatchAck,
    DiscussionAbortReason,
    DiscussionCapture,
    DiscussionDeliveryStatus,
    DiscussionDispatchCorrelation,
    DiscussionGenerationAck,
    DiscussionObservationStatus,
    DiscussionProposal,
    DiscussionProvenance,
    DiscussionStateSnapshot,
    DiscussionTerminalReason,
    DiscussionTerminalStatus,
    DiscussionTrigger,
    DiscussionValidationError,
    EvidenceRecordKind,
    EvidenceRef,
    EvidenceVisibility,
    ObservationAck,
    SpeechActKind,
    SpeechActNone,
    StageAck,
)
from ai_client.discussion.transaction import (
    AiDiscussionTerminalRecord,
    DiscussionTransaction,
    DiscussionTransactionError,
)
from ai_client.discussion.projection import DiscussionPromptConfig
from ai_client.llm.types import (
    AiAuditDecision,
    AiDiscussionGenerationRecord,
    AuditWriteAck,
    BackendIdentity,
    discussion_generation_record_sha256,
    serialize_ai_audit,
)
from ai_client.llm.audit import JsonlAiAuditSink
from ai_client.llm.admission_types import (
    AdmissionRequest,
    AdmissionResult,
    AdmissionStatus,
)
from ai_client.llm.brain import LLMBrain, LLMInvocationError
from ai_client.llm.types import (
    LLMBrainConfig,
    LLMClientIdentity,
    LLMInvocationStatus,
    LLMUsage,
    StructuredGenerationResponse,
)
from ai_client.network import (
    AbilityAction,
    ChatAction,
    CoDeclareAction,
    SendReceipt,
    VoteAction,
)
from ai_client.world import (
    AbilityResultView,
    ChatRecord,
    CoDeclarationRecord,
    CoView,
    CurrentActionsView,
    CurrentPhaseDeadline,
    Freshness,
    HistoryView,
    PhaseView,
    PlayerView,
    SelfView,
    TransportObservationView,
    WorldSnapshot,
)


H_GENERATION = "b" * 64
H_AFTER = "c" * 64
NOW = datetime(2026, 9, 13, 1, 2, 3, 456789, tzinfo=timezone.utc)


def async_test(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


def _capture(*, ordinal: int = 1) -> DiscussionCapture:
    context = AuthorizedDiscussionContext(
        schema_version="aiwolf.discussion-context.v1",
        game_id="opaque-game",
        player_id="opaque-self",
        role_id="opaque-role",
        modifier_ids=(),
        content_manifest_sha256="d" * 64,
        team="opaque-team",
        count_as="opaque-count",
        attack_result="opaque-attack",
        inspect_result="opaque-inspect",
        medium_result="opaque-medium",
        win_conditions=(
            CountParityWinCondition(
                type="count_parity",
                subject="opaque-count",
                against="opaque-other-count",
                operator="gte",
            ),
        ),
        abilities=(),
        passives=(),
        chat_channels=(AuthorizedChatChannelContext("opaque-channel", True),),
        knows_teammates=False,
        authorized_known_player_ids=(),
        known_players_complete=False,
    )
    context_sha256 = canonical_sha256(context)
    provenance = DiscussionProvenance(
        schema_version="aiwolf.discussion-provenance.v1",
        history_complete=True,
        co_complete=True,
        ability_results_complete=True,
        world_history_complete=True,
        world_history_dropped_count=0,
        world_history_dropped_through_order=None,
        remembered_after_world_eviction_count=0,
        known_unmodeled_event_count=0,
        unknown_event_count=0,
        malformed_event_count=0,
        model_state_reset=False,
        reset_reason=None,
    )
    state = DiscussionStateSnapshot(
        schema_version="aiwolf.discussion-state.v1",
        game_id="opaque-game",
        player_id="opaque-self",
        context_sha256=context_sha256,
        epoch=0,
        revision=0,
        fact_revision=0,
        world_version=ordinal,
        last_applied_seq=ordinal,
        phase="opaque-phase",
        day=1,
        assessments=(),
        claims=(),
        relations=(),
        strategy=None,
        important_events=(),
        recent_semantic_turns=(),
        provenance=provenance,
    )
    trigger = DiscussionTrigger(
        owner="reaction_chat",
        kind="INITIAL_CHAT",
        day=1,
        phase="opaque-phase",
        connection_generation=1,
        action_generation=ordinal,
        mapping_order=0,
        source=None,
    )
    material = {
        "schema_version": "aiwolf.discussion-capture.v1",
        "capture_ordinal": ordinal,
        "game_id": "opaque-game",
        "player_id": "opaque-self",
        "context_sha256": context_sha256,
        "state_sha256": canonical_sha256(state),
        "epoch": 0,
        "base_revision": 0,
        "fact_revision": 0,
        "world_version": ordinal,
        "last_applied_seq": ordinal,
        "trigger": trigger,
        "context": context,
        "state": state,
        "evidence": (),
    }
    return DiscussionCapture(capture_id=canonical_sha256(material), **material)


def _proposal(
    capture: DiscussionCapture,
    kind: str = "none",
    option_id: str | None = None,
) -> DiscussionProposal:
    return DiscussionProposal(
        schema_version="aiwolf.discussion-proposal.v1",
        base_revision=capture.base_revision,
        decision_kind=kind,  # type: ignore[arg-type]
        option_id=option_id,
        speech_act=SpeechActNone(SpeechActKind.NONE),
        reaction=None,
        assessment_updates=(),
        claim_updates=(),
        relation_updates=(),
        strategy_update=None,
        co_judgment=None,
        pre_vote_reassessment=None,
    )


def _generation(
    capture: DiscussionCapture,
    proposal: DiscussionProposal | None,
    *,
    status: AiDiscussionGenerationStatus,
    sequence: int = 7,
    ordinal: int = 1,
    generation_hash: str = H_GENERATION,
) -> DiscussionGenerationAck:
    return DiscussionGenerationAck(
        capture_id=capture.capture_id,
        request_id=f"phase6:{capture.capture_id}",
        final_attempt_ordinal=ordinal,  # type: ignore[arg-type]
        audit_sequence=sequence,
        generation_record_sha256=generation_hash,
        generation_status=status,
        context_sha256=capture.context_sha256,
        before_state_sha256=capture.state_sha256,
        after_state_sha256=None,
        proposal_sha256=None if proposal is None else canonical_sha256(proposal),
        durable=True,
    )


def _abort(
    capture: DiscussionCapture,
    reason: DiscussionAbortReason,
    proposal: DiscussionProposal | None = None,
) -> AbortAck:
    after_stage = reason in {
        DiscussionAbortReason.CANCELLED_AFTER_STAGE,
        DiscussionAbortReason.STALE,
        DiscussionAbortReason.DEADLINE,
        DiscussionAbortReason.REVISION_CONFLICT,
    }
    return AbortAck(
        capture_id=capture.capture_id,
        request_id=f"phase6:{capture.capture_id}",
        base_revision=capture.base_revision,
        context_sha256=capture.context_sha256,
        before_state_sha256=capture.state_sha256,
        after_state_sha256=None,
        proposal_sha256=None if proposal is None else canonical_sha256(proposal),
        reason=reason,
        stage_existed=after_stage,
        aborted=True,
    )


def _delivery(
    capture: DiscussionCapture,
    proposal: DiscussionProposal,
    status: DiscussionDeliveryStatus,
) -> DeliveryAck:
    no_action = status is DiscussionDeliveryStatus.NO_ACTION
    return DeliveryAck(
        capture_id=capture.capture_id,
        request_id=f"phase6:{capture.capture_id}",
        base_revision=capture.base_revision,
        committed_revision=capture.base_revision + 1,
        context_sha256=capture.context_sha256,
        before_state_sha256=capture.state_sha256,
        after_state_sha256=H_AFTER,
        proposal_sha256=canonical_sha256(proposal),
        status=status,
        action=None if no_action else proposal.decision_kind,  # type: ignore[arg-type]
        option_id=None if no_action else proposal.option_id,
        request_event_id=None,
        send_connection_generation=None,
        correlation=None,
    )


def _observation(
    capture: DiscussionCapture,
    proposal: DiscussionProposal,
    generation: DiscussionGenerationAck,
    status: DiscussionObservationStatus,
    evidence: EvidenceRef | None,
) -> ObservationAck:
    return ObservationAck(
        capture_id=capture.capture_id,
        request_id=f"phase6:{capture.capture_id}",
        base_revision=capture.base_revision,
        committed_revision=capture.base_revision + 1,
        context_sha256=capture.context_sha256,
        before_state_sha256=capture.state_sha256,
        after_state_sha256=H_AFTER,
        proposal_sha256=canonical_sha256(proposal),
        generation_audit_sequence=generation.audit_sequence,
        action=proposal.decision_kind,  # type: ignore[arg-type]
        option_id=proposal.option_id,  # type: ignore[arg-type]
        request_event_id="opaque-request-event",
        send_connection_generation=4,
        status=status,
        authoritative_evidence=evidence,
    )


def _transaction(
    audit: object, *, max_terminal_reservations: int = 512
) -> DiscussionTransaction:
    return DiscussionTransaction(
        state=_UnusedState(),  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        utc_clock=lambda: NOW,
        max_terminal_reservations=max_terminal_reservations,
    )


def _terminal_for_ordinal(
    transaction: DiscussionTransaction,
    ordinal: int,
) -> AiDiscussionTerminalRecord:
    capture = _capture(ordinal=ordinal)
    proposal = _proposal(capture)
    generation = _generation(
        capture,
        proposal,
        status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        sequence=ordinal,
    )
    return transaction.terminal_from_delivery(
        capture=capture,
        generation=generation,
        proposal=proposal,
        delivery=_delivery(capture, proposal, DiscussionDeliveryStatus.NO_ACTION),
    )


class _UnusedState:
    def capture(self, *args: object) -> None: ...
    def stage(self, *args: object) -> None: ...
    def commit(self, *args: object) -> None: ...
    def abort(self, *args: object) -> None: ...
    def finish_no_action(self, *args: object) -> None: ...
    def mark_dispatch_started(self, *args: object) -> None: ...
    def finish_dispatch(self, *args: object) -> None: ...
    def observe_authoritative(self, *args: object) -> None: ...


class _AuditSink:
    def __init__(self) -> None:
        self.records: list[AiDiscussionTerminalRecord] = []

    async def write(self, record: object) -> AuditWriteAck:
        assert isinstance(record, AiDiscussionTerminalRecord)
        self.records.append(record)
        return AuditWriteAck(sequence=len(self.records))


class _BlockingBackend:
    def __init__(self) -> None:
        self.identity = BackendIdentity(
            "opaque-backend",
            "http://127.0.0.1:1",
            "/v1/chat/completions",
            "opaque-model",
            "e" * 64,
        )
        self.entered = asyncio.Event()

    async def generate(self, request: object) -> object:
        self.entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        return None


class _GatedGenerationAudit:
    def __init__(self) -> None:
        self.records: list[object] = []
        self.cancelled_write_started = asyncio.Event()
        self.release_cancelled_write = asyncio.Event()
        self.calls = 0

    async def start(self) -> None:
        return None

    async def write(self, record: object) -> AuditWriteAck:
        self.calls += 1
        if (
            isinstance(record, AiDiscussionGenerationRecord)
            and record.status is AiDiscussionGenerationStatus.CANCELLED
        ):
            self.cancelled_write_started.set()
            await self.release_cancelled_write.wait()
        self.records.append(record)
        return AuditWriteAck(sequence=len(self.records))

    async def aclose(self) -> None:
        return None


def _action(kind: str, capture: DiscussionCapture) -> object:
    common = {
        "connection_generation": capture.trigger.connection_generation,
        "action_generation": capture.trigger.action_generation,
        "phase": capture.trigger.phase,
        "day": capture.trigger.day,
        "type": kind,
    }
    if kind == "chat":
        return ChatAction(**common, channel="opaque-channel")
    if kind == "vote":
        return VoteAction(
            **common,
            valid_targets=("opaque-peer",),
            target_count=1,
            allows_abstain=False,
        )
    if kind == "ability":
        return AbilityAction(
            **common,
            ability_id="opaque-ability",
            description=None,
            valid_targets=("opaque-peer",),
            target_count=1,
            uses_remaining=1,
        )
    if kind == "co_declare":
        return CoDeclareAction(**common, claimed_role_ids=("opaque-claim",))
    raise AssertionError(f"unsupported action fixture: {kind}")


def _decision(kind: str) -> object:
    if kind == "none":
        return NoDecision()
    if kind == "chat":
        return ChatDecision("action:0", "opaque speech")
    if kind == "vote":
        return VoteDecision("action:0", "opaque-peer")
    if kind == "ability":
        return AbilityDecision("action:0", ("opaque-peer",))
    if kind == "co_declare":
        return CoDeclareDecision("action:0", "opaque-claim", "opaque comment")
    raise AssertionError(f"unsupported decision fixture: {kind}")


class _ControllerWorld:
    def __init__(self, capture: DiscussionCapture, action: object | None) -> None:
        self.capture = capture
        self._snapshot = WorldSnapshot(
            version=capture.world_version,
            freshness=Freshness.CURRENT,
            is_caught_up=True,
            last_applied_seq=capture.last_applied_seq,
            players=(
                PlayerView("opaque-peer", "opaque peer"),
                PlayerView(capture.player_id, "opaque self"),
            ),
            alive_player_ids=("opaque-peer", capture.player_id),
            phase=PhaseView(capture.trigger.phase, capture.trigger.day),
            self_view=SelfView(capture.player_id, "opaque-role", ()),
        )
        self._actions = CurrentActionsView(
            world_version=capture.world_version,
            world_last_applied_seq=capture.last_applied_seq,
            network_last_seq=capture.last_applied_seq,
            is_caught_up=True,
            actions=() if action is None else (action,),
        )
        self._update = asyncio.Event()

    def snapshot(self) -> WorldSnapshot:
        return self._snapshot

    def current_actions(self) -> CurrentActionsView:
        return self._actions

    def history(self) -> HistoryView:
        return HistoryView((), True, self._snapshot.history_retention)

    def co_for_day(self, day: int) -> CoView:
        return CoView((), (), True, self._snapshot.history_retention)

    def ability_results(self) -> AbilityResultView:
        return AbilityResultView((), True, self._snapshot.history_retention)

    def transport_observations(self) -> TransportObservationView:
        trigger = self.capture.trigger
        return TransportObservationView(
            world_version=self._snapshot.version,
            first_retained_order=None,
            last_order=None,
            gap_before_first=False,
            observations=(),
            current_deadline=CurrentPhaseDeadline(
                mapping_order=trigger.mapping_order,
                phase=trigger.phase,
                day=trigger.day,
                connection_generation=trigger.connection_generation,
                action_generation=trigger.action_generation,
                local_deadline_monotonic=20.0,
            ),
        )

    async def wait_for_update(self, after_version: int) -> WorldSnapshot:
        await self._update.wait()
        return self._snapshot


class _ControllerSender:
    def __init__(self, events: list[str] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.events = events
        self.error: BaseException | None = None
        self.receipt = SendReceipt("opaque-request-event", 1)

    async def _send(self, name: str, *args: Any) -> SendReceipt:
        self.calls.append((name, args))
        if self.events is not None:
            self.events.append("sender-await")
        if self.error is not None:
            raise self.error
        return self.receipt

    async def send_chat(self, *args: Any) -> SendReceipt:
        return await self._send("chat", *args)

    async def send_vote(self, *args: Any) -> SendReceipt:
        return await self._send("vote", *args)

    async def send_ability(self, *args: Any) -> SendReceipt:
        return await self._send("ability", *args)

    async def send_co_declare(self, *args: Any) -> SendReceipt:
        return await self._send("co_declare", *args)


class _ControllerBrain:
    def __init__(self, output: object) -> None:
        self.output = output
        self.calls: list[BrainInput] = []

    async def decide(self, request: BrainInput) -> object:
        self.calls.append(request)
        return self.output


class _ControllerDiscussionState:
    def __init__(
        self,
        capture: DiscussionCapture,
        *,
        events: list[str] | None = None,
    ) -> None:
        self.capture_value = capture
        self.events = events
        self.calls: list[str] = []
        self.generation: DiscussionGenerationAck | None = None
        self.proposal: DiscussionProposal | None = None
        self.commit_ack: CommitAck | None = None
        self.dispatch_ack: DispatchAck | None = None
        self.observation_ack: ObservationAck | None = None

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if self.events is not None and name in {"commit", "dispatch-start"}:
            self.events.append(name)

    def capture(self, views: object, trigger: DiscussionTrigger) -> DiscussionCapture:
        self._record("capture")
        assert trigger == self.capture_value.trigger
        return self.capture_value

    def stage(
        self,
        capture: DiscussionCapture,
        request_id: str,
        proposal: DiscussionProposal,
        generation_ack: DiscussionGenerationAck,
    ) -> StageAck:
        self._record("stage")
        assert capture == self.capture_value
        assert request_id == generation_ack.request_id
        self.generation = generation_ack
        self.proposal = proposal
        return StageAck(
            capture_id=capture.capture_id,
            request_id=request_id,
            base_revision=capture.base_revision,
            context_sha256=capture.context_sha256,
            before_state_sha256=capture.state_sha256,
            after_state_sha256=None,
            proposal_sha256=canonical_sha256(proposal),
            staged=True,
        )

    def commit(self, stage_ack: StageAck) -> CommitAck:
        self._record("commit")
        self.commit_ack = CommitAck(
            capture_id=stage_ack.capture_id,
            request_id=stage_ack.request_id,
            base_revision=stage_ack.base_revision,
            committed_revision=stage_ack.base_revision + 1,
            context_sha256=stage_ack.context_sha256,
            before_state_sha256=stage_ack.before_state_sha256,
            after_state_sha256=H_AFTER,
            proposal_sha256=stage_ack.proposal_sha256,
            committed=True,
        )
        return self.commit_ack

    def abort(
        self,
        capture_id: str,
        request_id: str,
        reason: DiscussionAbortReason,
        proposal_sha256: str | None = None,
    ) -> AbortAck:
        self._record("abort")
        after_stage = reason in {
            DiscussionAbortReason.CANCELLED_AFTER_STAGE,
            DiscussionAbortReason.STALE,
            DiscussionAbortReason.DEADLINE,
            DiscussionAbortReason.REVISION_CONFLICT,
        }
        return AbortAck(
            capture_id=capture_id,
            request_id=request_id,
            base_revision=self.capture_value.base_revision,
            context_sha256=self.capture_value.context_sha256,
            before_state_sha256=self.capture_value.state_sha256,
            after_state_sha256=None,
            proposal_sha256=proposal_sha256,
            reason=reason,
            stage_existed=after_stage,
            aborted=True,
        )

    def finish_no_action(self, commit_ack: CommitAck) -> DeliveryAck:
        self._record("finish-no-action")
        return DeliveryAck(
            capture_id=commit_ack.capture_id,
            request_id=commit_ack.request_id,
            base_revision=commit_ack.base_revision,
            committed_revision=commit_ack.committed_revision,
            context_sha256=commit_ack.context_sha256,
            before_state_sha256=commit_ack.before_state_sha256,
            after_state_sha256=commit_ack.after_state_sha256,
            proposal_sha256=commit_ack.proposal_sha256,
            status=DiscussionDeliveryStatus.NO_ACTION,
            action=None,
            option_id=None,
            request_event_id=None,
            send_connection_generation=None,
            correlation=None,
        )

    def mark_dispatch_started(
        self, commit_ack: CommitAck, action: str, option_id: str
    ) -> DispatchAck:
        self._record("dispatch-start")
        self.dispatch_ack = DispatchAck(
            capture_id=commit_ack.capture_id,
            request_id=commit_ack.request_id,
            base_revision=commit_ack.base_revision,
            committed_revision=commit_ack.committed_revision,
            context_sha256=commit_ack.context_sha256,
            before_state_sha256=commit_ack.before_state_sha256,
            after_state_sha256=commit_ack.after_state_sha256,
            proposal_sha256=commit_ack.proposal_sha256,
            action=action,  # type: ignore[arg-type]
            option_id=option_id,
        )
        return self.dispatch_ack

    def finish_dispatch(
        self,
        dispatch_ack: DispatchAck,
        status: DiscussionDeliveryStatus,
        receipt: object = None,
    ) -> DeliveryAck:
        self._record("finish-dispatch")
        correlation = None
        request_event_id = None
        connection_generation = None
        if status is DiscussionDeliveryStatus.LOCAL_SENT:
            assert isinstance(receipt, SendReceipt)
            request_event_id = receipt.event_id
            connection_generation = receipt.connection_generation
            assert self.generation is not None
            correlation = DiscussionDispatchCorrelation(
                capture_id=dispatch_ack.capture_id,
                request_id=dispatch_ack.request_id,
                context_sha256=dispatch_ack.context_sha256,
                before_state_sha256=dispatch_ack.before_state_sha256,
                after_state_sha256=dispatch_ack.after_state_sha256,
                proposal_sha256=dispatch_ack.proposal_sha256,
                generation_audit_sequence=self.generation.audit_sequence,
                base_revision=dispatch_ack.base_revision,
                committed_revision=dispatch_ack.committed_revision,
                action=dispatch_ack.action,
                option_id=dispatch_ack.option_id,
                request_event_id=request_event_id,
                send_connection_generation=connection_generation,
            )
        return DeliveryAck(
            capture_id=dispatch_ack.capture_id,
            request_id=dispatch_ack.request_id,
            base_revision=dispatch_ack.base_revision,
            committed_revision=dispatch_ack.committed_revision,
            context_sha256=dispatch_ack.context_sha256,
            before_state_sha256=dispatch_ack.before_state_sha256,
            after_state_sha256=dispatch_ack.after_state_sha256,
            proposal_sha256=dispatch_ack.proposal_sha256,
            status=status,
            action=dispatch_ack.action,
            option_id=dispatch_ack.option_id,
            request_event_id=request_event_id,
            send_connection_generation=connection_generation,
            correlation=correlation,
        )

    def observe_authoritative(
        self,
        correlation: DiscussionDispatchCorrelation,
        status: DiscussionObservationStatus,
        evidence: EvidenceRef | None = None,
    ) -> ObservationAck:
        self._record("observe")
        acknowledgement = ObservationAck(
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
        if self.observation_ack is not None:
            if self.observation_ack != acknowledgement:
                raise DiscussionValidationError(
                    "conflicting duplicate authoritative observation"
                )
            return self.observation_ack
        self.observation_ack = acknowledgement
        return acknowledgement


def _controller_case(
    kind: str,
    *,
    handle_kind: str | None = None,
    events: list[str] | None = None,
    contextful: bool = True,
    audit_override: object | None = None,
    controller_type: type[BrainController] = BrainController,
) -> tuple[
    BrainController,
    BrainInput,
    DispatchDeadline | None,
    _ControllerDiscussionState | None,
    _AuditSink | None,
    _ControllerSender,
]:
    capture = _capture()
    actual_handle_kind = handle_kind or ("chat" if kind == "none" else kind)
    handle = _action(actual_handle_kind, capture)
    option_id = None if kind == "none" else "action:0"
    proposal = _proposal(capture, kind, option_id)
    status = (
        AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION
        if kind == "none"
        else AiDiscussionGenerationStatus.DECISION
    )
    generation = _generation(capture, proposal, status=status)
    decision = _decision(kind)
    result = BrainResult(decision, proposal, generation)
    world = _ControllerWorld(capture, handle)
    sender = _ControllerSender(events)
    state = _ControllerDiscussionState(capture, events=events) if contextful else None
    audit = (audit_override or _AuditSink()) if contextful else None
    controller = controller_type(
        world=world,  # type: ignore[arg-type]
        sender=sender,  # type: ignore[arg-type]
        brain=_ControllerBrain(result if contextful else decision),  # type: ignore[arg-type]
        config=BrainRunConfig(
            max_decision_seconds=1.0,
            cancellation_grace_seconds=0.1,
        ),
        clock=lambda: 10.0,
        discussion_state=state,
        discussion_audit=audit,  # type: ignore[arg-type]
    )
    request = BrainInput(
        snapshot=world.snapshot(),
        action_context=BrainActionContext(
            world_version=capture.world_version,
            world_last_applied_seq=capture.last_applied_seq,
            network_last_seq=capture.last_applied_seq,
            is_caught_up=True,
            options=(BrainActionOption("action:0", handle),),
        ),
        history=world.history(),
        co=world.co_for_day(1),
        ability_results=world.ability_results(),
        discussion=capture if contextful else None,
    )
    deadline = None
    if contextful:
        trigger = capture.trigger
        deadline = DispatchDeadline(
            mapping_order=trigger.mapping_order,
            phase=trigger.phase,
            day=trigger.day,
            connection_generation=trigger.connection_generation,
            action_generation=trigger.action_generation,
            not_after_monotonic=20.0,
            discussion_trigger=trigger,
        )
    return controller, request, deadline, state, audit, sender


def _generation_record(
    capture: DiscussionCapture,
    proposal: DiscussionProposal,
) -> AiDiscussionGenerationRecord:
    prompt = '{"opaque":"prompt"}'
    response = '{"opaque":"response"}'
    prompt_bytes = prompt.encode("utf-8")
    response_bytes = response.encode("utf-8")
    import hashlib

    return AiDiscussionGenerationRecord(
        schema_version="aiwolf.ai-discussion-generation.v1",
        recorded_at_utc="2026-09-13T01:02:03.456789Z",
        game_id=capture.game_id,
        player_id=capture.player_id,
        request_id=f"phase6:{capture.capture_id}",
        capture_id=capture.capture_id,
        phase=capture.trigger.phase,
        day=capture.trigger.day,
        world_version=capture.world_version,
        backend=BackendIdentity(
            backend_type="opaque-backend",
            endpoint_origin="http://127.0.0.1:1",
            endpoint_path="/v1/chat/completions",
            model="opaque-model",
            config_fingerprint="e" * 64,
        ),
        attempt_ordinal=1,
        prompt_sha256=hashlib.sha256(prompt_bytes).hexdigest(),
        prompt_bytes=len(prompt_bytes),
        prompt_json=prompt,
        response_sha256=hashlib.sha256(response_bytes).hexdigest(),
        response_bytes=len(response_bytes),
        response_text=response,
        latency_microseconds=12,
        provider_model="opaque-provider-model",
        finish_reason="opaque-finish",
        prompt_tokens=3,
        completion_tokens=4,
        status=AiDiscussionGenerationStatus.DECISION,
        backend_error_code=None,
        validation_code=None,
        decision=AiAuditDecision(
            kind="chat",
            option_id=proposal.option_id,
            text="opaque speech",
        ),
        context_sha256=capture.context_sha256,
        before_state_sha256=capture.state_sha256,
        after_state_sha256=None,
        base_revision=capture.base_revision,
        proposal=proposal,
        proposal_sha256=canonical_sha256(proposal),
    )


async def _p6bc_direct_chat(
    *,
    kind: str = "chat",
    audit_override: object | None = None,
    controller_type: type[BrainController] = BrainController,
) -> tuple[
    BrainInvocationArbiter,
    BrainController,
    _ControllerDiscussionState,
    object,
    DiscussionDispatchCorrelation,
]:
    controller, request, deadline, state, audit, _sender = _controller_case(
        kind,
        audit_override=audit_override,
        controller_type=controller_type,
    )
    assert deadline is not None and state is not None and audit is not None
    handle = request.action_context.options[0].handle
    arbiter = BrainInvocationArbiter(controller=controller, clock=lambda: 10.0)
    owner = "reaction_chat" if kind in {"chat", "co_declare"} else "vote_ability"
    result = await arbiter.invoke(
        owner=owner,  # type: ignore[arg-type]
        priority=(
            BrainInvocationPriority.REACTION
            if owner == "reaction_chat"
            else BrainInvocationPriority.RESERVATION_ACTION
        ),
        allowed_handles=(handle,),
        timeout_seconds=1.0,
        dispatch_deadline=deadline,
    )
    assert result.outcome.status is DecisionStatus.SENT
    assert result.outcome.discussion is not None
    return arbiter, controller, state, audit, result.outcome.discussion


def _chat_acceptance_evidence(order: int = 21) -> EvidenceRef:
    return EvidenceRef(
        EvidenceRecordKind.CHAT,
        order,
        EvidenceVisibility.PUBLIC,
    )


class _ReportedUnresponsiveController(BrainController):
    """Inject the public pre-existing-unresponsive health boundary."""

    @property
    def unresponsive(self) -> bool:
        return True


@async_test
async def test_p6bc_outer_finalizer_accepted_and_rejected_link_one_exact_terminal() -> None:
    vectors = (
        (
            "chat",
            "reaction_chat",
            DiscussionObservationStatus.ACCEPTED,
            DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            _chat_acceptance_evidence(),
            DiscussionTerminalStatus.ACCEPTED,
        ),
        (
            "co_declare",
            "reaction_chat",
            DiscussionObservationStatus.ACCEPTED,
            DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            EvidenceRef(
                EvidenceRecordKind.CO_DECLARATION,
                22,
                EvidenceVisibility.PUBLIC,
            ),
            DiscussionTerminalStatus.ACCEPTED,
        ),
        (
            "vote",
            "vote_ability",
            DiscussionObservationStatus.ACCEPTED,
            DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            EvidenceRef(
                EvidenceRecordKind.ACTION_ACCEPTED,
                23,
                EvidenceVisibility.AUTHORIZED_PRIVATE,
            ),
            DiscussionTerminalStatus.ACCEPTED,
        ),
        (
            "ability",
            "vote_ability",
            DiscussionObservationStatus.ACCEPTED,
            DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            EvidenceRef(
                EvidenceRecordKind.ACTION_ACCEPTED,
                24,
                EvidenceVisibility.AUTHORIZED_PRIVATE,
            ),
            DiscussionTerminalStatus.ACCEPTED,
        ),
        (
            "chat",
            "reaction_chat",
            DiscussionObservationStatus.REJECTED,
            DiscussionTerminalReason.AUTHORITATIVE_REJECTED,
            EvidenceRef(
                EvidenceRecordKind.ACTION_REJECTION,
                25,
                EvidenceVisibility.AUTHORIZED_PRIVATE,
            ),
            DiscussionTerminalStatus.REJECTED,
        ),
    )
    for kind, owner, status, reason, evidence, terminal_status in vectors:
        arbiter, _controller, state, audit, correlation = await _p6bc_direct_chat(
            kind=kind
        )
        acknowledgement = await arbiter.finalize_discussion_observation(
            owner=owner,  # type: ignore[arg-type]
            correlation=correlation,
            status=status,
            reason=reason,
            evidence=evidence,
        )
        assert acknowledgement.status is status
        assert acknowledgement.authoritative_evidence == evidence
        assert state.calls.count("observe") == 1
        assert len(audit.records) == 1  # type: ignore[attr-defined]
        terminal = audit.records[0]  # type: ignore[attr-defined]
        assert terminal.status is terminal_status
        assert terminal.reason is reason
        assert terminal.capture_id == correlation.capture_id
        assert terminal.request_event_id == correlation.request_event_id
        assert terminal.authoritative_evidence == evidence
        await arbiter.stop()


@async_test
async def test_p6bc_outer_finalizer_recovery_reason_and_evidence_matrix() -> None:
    recovery_barrier = EvidenceRef(
        EvidenceRecordKind.RESUME_RECOVERY_BARRIER,
        31,
        EvidenceVisibility.AUTHORIZED_PRIVATE,
    )
    phase_transition = EvidenceRef(
        EvidenceRecordKind.PHASE_TRANSITION,
        32,
        EvidenceVisibility.PUBLIC,
    )
    valid = (
        (DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS, None),
        (DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS, recovery_barrier),
        (DiscussionTerminalReason.RECOVERY_GAP, None),
        (DiscussionTerminalReason.RECOVERY_GAP, recovery_barrier),
        (DiscussionTerminalReason.PHASE_CHANGED, None),
        (DiscussionTerminalReason.PHASE_CHANGED, phase_transition),
        (DiscussionTerminalReason.OWNER_STOPPED, None),
    )
    for reason, evidence in valid:
        arbiter, _controller, state, audit, correlation = await _p6bc_direct_chat()
        ack = await arbiter.finalize_discussion_observation(
            owner="reaction_chat",
            correlation=correlation,
            status=DiscussionObservationStatus.RECOVERY_UNKNOWN,
            reason=reason,
            evidence=evidence,
        )
        assert ack.status is DiscussionObservationStatus.RECOVERY_UNKNOWN
        assert ack.authoritative_evidence == evidence
        assert state.calls.count("observe") == 1
        assert len(audit.records) == 1  # type: ignore[attr-defined]
        assert audit.records[0].reason is reason  # type: ignore[attr-defined]
        await arbiter.stop()

    arbiter, _controller, state, audit, correlation = await _p6bc_direct_chat()
    rejected = (
        (
            DiscussionObservationStatus.ACCEPTED,
            DiscussionTerminalReason.AUTHORITATIVE_REJECTED,
            _chat_acceptance_evidence(40),
        ),
        (
            DiscussionObservationStatus.REJECTED,
            DiscussionTerminalReason.AUTHORITATIVE_REJECTED,
            None,
        ),
        (
            DiscussionObservationStatus.RECOVERY_UNKNOWN,
            DiscussionTerminalReason.OWNER_STOPPED,
            recovery_barrier,
        ),
        (
            DiscussionObservationStatus.RECOVERY_UNKNOWN,
            DiscussionTerminalReason.RECOVERY_GAP,
            _chat_acceptance_evidence(41),
        ),
        (
            DiscussionObservationStatus.RECOVERY_UNKNOWN,
            DiscussionTerminalReason.PHASE_CHANGED,
            recovery_barrier,
        ),
    )
    for status, reason, evidence in rejected:
        with pytest.raises(DiscussionTransactionError):
            await arbiter.finalize_discussion_observation(
                owner="reaction_chat",
                correlation=correlation,
                status=status,
                reason=reason,
                evidence=evidence,
            )
    assert state.calls.count("observe") == 0
    assert audit.records == []  # type: ignore[attr-defined]
    await arbiter.stop()
    assert state.calls.count("observe") == 1

    action_mismatches = (
        (
            "chat",
            "reaction_chat",
            EvidenceRef(
                EvidenceRecordKind.ACTION_ACCEPTED,
                42,
                EvidenceVisibility.AUTHORIZED_PRIVATE,
            ),
        ),
        (
            "co_declare",
            "reaction_chat",
            _chat_acceptance_evidence(43),
        ),
        (
            "vote",
            "vote_ability",
            _chat_acceptance_evidence(44),
        ),
        (
            "ability",
            "vote_ability",
            EvidenceRef(
                EvidenceRecordKind.CO_DECLARATION,
                45,
                EvidenceVisibility.PUBLIC,
            ),
        ),
    )
    for kind, owner, wrong_evidence in action_mismatches:
        arbiter, _controller, state, audit, correlation = await _p6bc_direct_chat(
            kind=kind
        )
        with pytest.raises(DiscussionTransactionError):
            await arbiter.finalize_discussion_observation(
                owner=owner,  # type: ignore[arg-type]
                correlation=correlation,
                status=DiscussionObservationStatus.ACCEPTED,
                reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
                evidence=wrong_evidence,
            )
        assert state.calls.count("observe") == 0
        assert audit.records == []  # type: ignore[attr-defined]
        await arbiter.stop()
        assert state.calls.count("observe") == 1

    # The invalid provisional task is serialized, but it is not a durable
    # attempt.  A concurrent first valid fact must retry selection after the
    # Controller-only precondition rejection resets the gate.
    arbiter, controller, state, audit, correlation = await _p6bc_direct_chat()
    original_finalizer = controller.finalize_discussion_observation
    invalid_entered = asyncio.Event()
    release_invalid = asyncio.Event()

    async def delayed_invalid(**kwargs: object) -> ObservationAck:
        if kwargs["reason"] is DiscussionTerminalReason.AUTHORITATIVE_REJECTED:
            invalid_entered.set()
            await release_invalid.wait()
        return await original_finalizer(**kwargs)  # type: ignore[arg-type]

    controller.finalize_discussion_observation = (  # type: ignore[method-assign]
        delayed_invalid
    )
    invalid = asyncio.create_task(
        arbiter.finalize_discussion_observation(
            owner="reaction_chat",
            correlation=correlation,
            status=DiscussionObservationStatus.ACCEPTED,
            reason=DiscussionTerminalReason.AUTHORITATIVE_REJECTED,
            evidence=_chat_acceptance_evidence(46),
        )
    )
    await asyncio.wait_for(invalid_entered.wait(), 1.0)
    valid_evidence = _chat_acceptance_evidence(47)
    valid_call = asyncio.create_task(
        arbiter.finalize_discussion_observation(
            owner="reaction_chat",
            correlation=correlation,
            status=DiscussionObservationStatus.ACCEPTED,
            reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            evidence=valid_evidence,
        )
    )
    await asyncio.sleep(0)
    assert not valid_call.done()
    valid_call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await valid_call
    release_invalid.set()
    with pytest.raises(DiscussionTransactionError):
        await invalid
    acknowledgement = await asyncio.wait_for(
        arbiter.finalize_discussion_observation(
            owner="reaction_chat",
            correlation=correlation,
            status=DiscussionObservationStatus.ACCEPTED,
            reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            evidence=valid_evidence,
        ),
        1.0,
    )
    assert acknowledgement.authoritative_evidence is valid_evidence
    assert state.calls.count("observe") == 1
    assert len(audit.records) == 1  # type: ignore[attr-defined]
    await arbiter.stop()

    # Stop uses the same retry rule: after a blocked invalid provisional is
    # rejected without work, stop selects the one OWNER_STOPPED terminal.
    arbiter, controller, state, audit, correlation = await _p6bc_direct_chat()
    original_finalizer = controller.finalize_discussion_observation
    invalid_entered = asyncio.Event()
    release_invalid = asyncio.Event()

    async def delayed_invalid_for_stop(**kwargs: object) -> ObservationAck:
        if kwargs["reason"] is DiscussionTerminalReason.AUTHORITATIVE_REJECTED:
            invalid_entered.set()
            await release_invalid.wait()
        return await original_finalizer(**kwargs)  # type: ignore[arg-type]

    controller.finalize_discussion_observation = (  # type: ignore[method-assign]
        delayed_invalid_for_stop
    )
    invalid = asyncio.create_task(
        arbiter.finalize_discussion_observation(
            owner="reaction_chat",
            correlation=correlation,
            status=DiscussionObservationStatus.ACCEPTED,
            reason=DiscussionTerminalReason.AUTHORITATIVE_REJECTED,
            evidence=_chat_acceptance_evidence(48),
        )
    )
    await asyncio.wait_for(invalid_entered.wait(), 1.0)
    stopping = asyncio.create_task(arbiter.stop())
    await asyncio.sleep(0)
    assert not stopping.done()
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    release_invalid.set()
    with pytest.raises(DiscussionTransactionError):
        await invalid
    await asyncio.wait_for(arbiter.stop(), 1.0)
    assert state.calls.count("observe") == 1
    assert len(audit.records) == 1  # type: ignore[attr-defined]
    assert audit.records[0].reason is DiscussionTerminalReason.OWNER_STOPPED  # type: ignore[attr-defined]

    # A controller already reporting unresponsive is never treated as a
    # resettable request-local precondition, even when validation itself has
    # not reached state/audit.
    arbiter, _controller, state, audit, correlation = await _p6bc_direct_chat(
        controller_type=_ReportedUnresponsiveController
    )
    with pytest.raises(DiscussionTransactionError):
        await arbiter.finalize_discussion_observation(
            owner="reaction_chat",
            correlation=correlation,
            status=DiscussionObservationStatus.ACCEPTED,
            reason=DiscussionTerminalReason.AUTHORITATIVE_REJECTED,
            evidence=_chat_acceptance_evidence(49),
        )
    with pytest.raises(RuntimeError, match="BrainInvocationArbiter is poisoned"):
        await arbiter.finalize_discussion_observation(
            owner="reaction_chat",
            correlation=correlation,
            status=DiscussionObservationStatus.ACCEPTED,
            reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            evidence=_chat_acceptance_evidence(50),
        )
    assert state.calls.count("observe") == 0
    assert audit.records == []  # type: ignore[attr-defined]
    with pytest.raises(DiscussionTransactionError):
        await arbiter.stop()


@async_test
async def test_p6bc_outer_finalizer_duplicate_is_idempotent_and_conflict_is_rejected() -> None:
    arbiter, _controller, state, audit, correlation = await _p6bc_direct_chat()
    evidence = _chat_acceptance_evidence(51)
    first = await arbiter.finalize_discussion_observation(
        owner="reaction_chat",
        correlation=correlation,
        status=DiscussionObservationStatus.ACCEPTED,
        reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
        evidence=evidence,
    )
    duplicate = await arbiter.finalize_discussion_observation(
        owner="reaction_chat",
        correlation=correlation,
        status=DiscussionObservationStatus.ACCEPTED,
        reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
        evidence=evidence,
    )
    assert duplicate is first
    assert state.calls.count("observe") == 1
    assert len(audit.records) == 1  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="conflicting discussion observation"):
        await arbiter.finalize_discussion_observation(
            owner="reaction_chat",
            correlation=correlation,
            status=DiscussionObservationStatus.RECOVERY_UNKNOWN,
            reason=DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
        )
    with pytest.raises(ValueError, match="owner does not match"):
        await arbiter.finalize_discussion_observation(
            owner="vote_ability",
            correlation=correlation,
            status=DiscussionObservationStatus.ACCEPTED,
            reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            evidence=evidence,
        )
    changed_correlation = replace(
        correlation,
        request_event_id="opaque-different-request-event",
    )
    with pytest.raises(RuntimeError, match="does not match the registered owner"):
        await arbiter.finalize_discussion_observation(
            owner="reaction_chat",
            correlation=changed_correlation,
            status=DiscussionObservationStatus.ACCEPTED,
            reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            evidence=evidence,
        )
    assert state.calls.count("observe") == 1
    assert len(audit.records) == 1  # type: ignore[attr-defined]
    await arbiter.stop()


@async_test
async def test_p6bc_outer_finalizer_cancellation_and_audit_failure_never_retry() -> None:
    class BlockingAudit:
        def __init__(self) -> None:
            self.records: list[object] = []
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def write(self, record: object) -> AuditWriteAck:
            self.calls += 1
            self.records.append(record)
            self.started.set()
            await self.release.wait()
            return AuditWriteAck(sequence=1)

    blocking = BlockingAudit()
    arbiter, _controller, state, _audit, correlation = await _p6bc_direct_chat(
        audit_override=blocking
    )
    evidence = _chat_acceptance_evidence(61)
    caller = asyncio.create_task(
        arbiter.finalize_discussion_observation(
            owner="reaction_chat",
            correlation=correlation,
            status=DiscussionObservationStatus.ACCEPTED,
            reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            evidence=evidence,
        )
    )
    await asyncio.wait_for(blocking.started.wait(), 1.0)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert blocking.calls == 1
    blocking.release.set()
    for _ in range(100):
        if len(blocking.records) == 1 and state.observation_ack is not None:
            await asyncio.sleep(0)
            break
        await asyncio.sleep(0)
    duplicate = await arbiter.finalize_discussion_observation(
        owner="reaction_chat",
        correlation=correlation,
        status=DiscussionObservationStatus.ACCEPTED,
        reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
        evidence=evidence,
    )
    assert duplicate == state.observation_ack
    assert blocking.calls == 1
    await arbiter.stop()

    class FailingAudit:
        def __init__(self) -> None:
            self.calls = 0

        async def write(self, record: object) -> object:
            self.calls += 1
            raise RuntimeError("opaque sink failure")

    failing = FailingAudit()
    arbiter, controller, state, _audit, correlation = await _p6bc_direct_chat(
        audit_override=failing
    )
    with pytest.raises(DiscussionTransactionError, match="terminal audit write failed"):
        await arbiter.finalize_discussion_observation(
            owner="reaction_chat",
            correlation=correlation,
            status=DiscussionObservationStatus.ACCEPTED,
            reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            evidence=_chat_acceptance_evidence(62),
        )
    with pytest.raises(RuntimeError, match="BrainInvocationArbiter is poisoned"):
        await arbiter.finalize_discussion_observation(
            owner="reaction_chat",
            correlation=correlation,
            status=DiscussionObservationStatus.ACCEPTED,
            reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            evidence=_chat_acceptance_evidence(62),
        )
    assert failing.calls == 1
    assert state.calls.count("observe") == 1
    assert controller.unresponsive
    with pytest.raises(DiscussionTransactionError, match="terminal audit write failed"):
        await asyncio.wait_for(arbiter.stop(), 1.0)
    with pytest.raises(DiscussionTransactionError, match="terminal audit write failed"):
        await asyncio.wait_for(arbiter.stop(), 1.0)
    assert failing.calls == 1

    for failure_point in ("state", "terminal"):
        arbiter, controller, state, audit, correlation = await _p6bc_direct_chat()
        original_observe = state.observe_authoritative
        if failure_point == "state":

            def fail_observation(*_args: object, **_kwargs: object) -> ObservationAck:
                state.calls.append("observe")
                raise RuntimeError("opaque state observation failure")

            state.observe_authoritative = fail_observation  # type: ignore[method-assign]
        else:

            def invalid_observation(*args: object, **kwargs: object) -> ObservationAck:
                acknowledgement = original_observe(  # type: ignore[arg-type]
                    *args, **kwargs
                )
                return replace(
                    acknowledgement,
                    capture_id="f" * 64,
                )

            state.observe_authoritative = invalid_observation  # type: ignore[method-assign]
        with pytest.raises(Exception) as first_failure:
            await arbiter.finalize_discussion_observation(
                owner="reaction_chat",
                correlation=correlation,
                status=DiscussionObservationStatus.ACCEPTED,
                reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
                evidence=_chat_acceptance_evidence(63),
            )
        assert controller.unresponsive
        assert state.calls.count("observe") == 1
        assert audit.records == []  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError, match="BrainInvocationArbiter is poisoned"):
            await arbiter.finalize_discussion_observation(
                owner="reaction_chat",
                correlation=correlation,
                status=DiscussionObservationStatus.ACCEPTED,
                reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
                evidence=_chat_acceptance_evidence(63),
            )
        with pytest.raises(type(first_failure.value)) as stopped:
            await asyncio.wait_for(arbiter.stop(), 1.0)
        assert stopped.value is first_failure.value


@async_test
async def test_p6bc_stop_finalizes_owner_stopped_before_transaction_close() -> None:
    class BlockingAudit:
        def __init__(self) -> None:
            self.records: list[object] = []
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def write(self, record: object) -> AuditWriteAck:
            self.records.append(record)
            self.started.set()
            await self.release.wait()
            return AuditWriteAck(sequence=1)

    audit = BlockingAudit()
    arbiter, controller, state, _audit, _correlation = await _p6bc_direct_chat(
        audit_override=audit
    )
    stop_entered = asyncio.Event()
    original_stop = controller.stop

    async def observed_stop() -> None:
        stop_entered.set()
        await original_stop()

    controller.stop = observed_stop  # type: ignore[method-assign]
    stopping = asyncio.create_task(arbiter.stop())
    await asyncio.wait_for(audit.started.wait(), 1.0)
    assert not stop_entered.is_set()
    assert not stopping.done()
    audit.release.set()
    await asyncio.wait_for(stopping, 1.0)
    assert stop_entered.is_set()
    assert state.calls.count("observe") == 1
    assert len(audit.records) == 1
    terminal = audit.records[0]
    assert isinstance(terminal, AiDiscussionTerminalRecord)
    assert terminal.status is DiscussionTerminalStatus.RECOVERY_UNKNOWN
    assert terminal.reason is DiscussionTerminalReason.OWNER_STOPPED
    assert terminal.authoritative_evidence is None


@async_test
async def test_p6bc_context_free_dispatch_has_no_observation_gate() -> None:
    controller, request, _deadline, state, audit, _sender = _controller_case(
        "chat", contextful=False
    )
    assert state is None and audit is None
    handle = request.action_context.options[0].handle
    arbiter = BrainInvocationArbiter(controller=controller, clock=lambda: 10.0)
    first = await arbiter.invoke(
        owner="reaction_chat",
        priority=BrainInvocationPriority.REACTION,
        allowed_handles=(handle,),
        timeout_seconds=1.0,
        dispatch_deadline=DispatchDeadline(0, "opaque-phase", 1, 1, 1, 20.0),
    )
    second = await arbiter.invoke(
        owner="reaction_chat",
        priority=BrainInvocationPriority.REACTION,
        allowed_handles=(handle,),
        timeout_seconds=1.0,
        dispatch_deadline=DispatchDeadline(0, "opaque-phase", 1, 1, 1, 20.0),
    )
    assert first.outcome.status is DecisionStatus.SENT
    assert second.outcome.status is DecisionStatus.SENT
    assert first.outcome.discussion is None
    assert second.outcome.discussion is None
    await arbiter.stop()


@async_test
async def test_p6b_controller_all_five_identities_stage_commit_and_finalize_exactly_once() -> None:
    for kind in ("none", "chat", "vote", "ability", "co_declare"):
        controller, request, deadline, state, audit, sender = _controller_case(kind)
        assert deadline is not None and state is not None and audit is not None
        outcome = await controller.decide_and_send(
            request,
            dispatch_deadline=deadline,
        )
        assert state.calls[:2] == ["stage", "commit"]
        if kind == "none":
            assert outcome.status is DecisionStatus.NO_DECISION
            assert state.calls == ["stage", "commit", "finish-no-action"]
            assert sender.calls == []
            assert len(audit.records) == 1
            assert audit.records[0].decision_kind == "none"
            assert audit.records[0].status is DiscussionTerminalStatus.NO_ACTION
        else:
            assert outcome.status is DecisionStatus.SENT
            assert state.calls == [
                "stage",
                "commit",
                "dispatch-start",
                "finish-dispatch",
            ]
            assert len(sender.calls) == 1
            assert sender.calls[0][0] == kind
            assert audit.records == []
            assert outcome.discussion is not None
            assert outcome.discussion.action == kind
            assert outcome.discussion.option_id == "action:0"
            assert outcome.discussion.proposal_sha256 == canonical_sha256(
                state.proposal
            )


@async_test
async def test_p6b_identity_handle_audit_hash_mismatch_has_zero_stage_commit_send() -> None:
    controller, request, deadline, state, audit, sender = _controller_case(
        "chat", handle_kind="vote"
    )
    assert deadline is not None and state is not None and audit is not None
    outcome = await controller.decide_and_send(
        request,
        dispatch_deadline=deadline,
    )
    assert outcome.status is DecisionStatus.INVALID_DECISION
    assert state.calls == ["abort"]
    assert not {"stage", "commit", "dispatch-start"}.intersection(state.calls)
    assert sender.calls == []
    assert len(audit.records) == 1
    assert audit.records[0].reason is DiscussionTerminalReason.STAGE_FAILED

    # A generation/capture hash mutation remains individually well-typed but is
    # rejected before stage/commit/send.  It cannot be linked into a false
    # terminal because the generation evidence itself is inconsistent.
    controller, request, deadline, state, audit, sender = _controller_case("chat")
    assert deadline is not None and state is not None and audit is not None
    result = controller.brain.output  # type: ignore[attr-defined]
    assert isinstance(result, BrainResult) and result.audit_ack is not None
    object.__setattr__(result.audit_ack, "context_sha256", "f" * 64)
    with pytest.raises(DiscussionTransactionError):
        await controller.decide_and_send(request, dispatch_deadline=deadline)
    assert state.calls == ["abort"]
    assert sender.calls == []
    assert audit.records == []


@pytest.mark.parametrize(
    ("kind", "message", "history", "declarations"),
    (
        (
            "chat",
            "\u3000ｏｐａｑｕｅ\n speech\t",
            (),
            (CoDeclarationRecord(9, 1, "opaque-phase", "opaque-self", "opaque-claim", "opaque speech"),),
        ),
        (
            "co_declare",
            "opaque\u00a0comment",
            (ChatRecord(8, 1, "opaque-phase", "opaque-channel", "opaque-self", "opaque self", "opaque comment"),),
            (),
        ),
        ("chat", "\u3000\n\t", (), ()),
    ),
)
@async_test
async def test_p6b_immediate_self_repeat_aborts_before_stage_and_send(
    kind: str,
    message: str,
    history: tuple[ChatRecord, ...],
    declarations: tuple[CoDeclarationRecord, ...],
) -> None:
    controller, request, deadline, state, audit, sender = _controller_case(kind)
    assert deadline is not None and state is not None and audit is not None
    decision = (
        ChatDecision("action:0", message)
        if kind == "chat"
        else CoDeclareDecision("action:0", "opaque-claim", message)
    )
    result = controller.brain.output  # type: ignore[attr-defined]
    assert isinstance(result, BrainResult)
    controller.brain.output = replace(result, decision=decision)  # type: ignore[attr-defined]
    retention = request.snapshot.history_retention
    request = replace(
        request,
        history=HistoryView(history, True, retention),
        co=CoView(declarations, (), True, retention),
    )
    before_revision = request.discussion.state.revision  # type: ignore[union-attr]

    outcome = await controller.decide_and_send(request, dispatch_deadline=deadline)

    assert outcome.status is DecisionStatus.INVALID_DECISION
    assert state.calls == ["abort"]
    assert sender.calls == []
    assert len(audit.records) == 1
    assert audit.records[0].reason is DiscussionTerminalReason.STAGE_FAILED
    assert audit.records[0].proposal_sha256 == canonical_sha256(result.discussion)
    assert request.discussion.state.revision == before_revision  # type: ignore[union-attr]


@async_test
async def test_p6b_repeat_check_does_not_guess_across_incomplete_or_ambiguous_views() -> None:
    for incomplete, tied, conflicted in (
        (True, False, False),
        (False, True, False),
        (False, False, True),
    ):
        controller, request, deadline, state, audit, sender = _controller_case("chat")
        assert deadline is not None and state is not None and audit is not None
        retention = request.snapshot.history_retention
        history: tuple[ChatRecord, ...] = (
            ChatRecord(8, 1, "opaque-phase", "opaque-channel", "opaque-self", "opaque self", "opaque speech"),
        )
        if conflicted:
            history += (
                ChatRecord(8, 1, "opaque-phase", "opaque-channel", "opaque-peer", "opaque peer", "different"),
            )
        declarations = (
            CoDeclarationRecord(8, 1, "opaque-phase", "opaque-self", "opaque-claim", "opaque speech"),
        ) if tied else ()
        request = replace(
            request,
            history=HistoryView(history, not incomplete, retention),
            co=CoView(declarations, (), True, retention),
        )

        outcome = await controller.decide_and_send(request, dispatch_deadline=deadline)

        assert outcome.status is DecisionStatus.SENT
        assert state.calls == ["stage", "commit", "dispatch-start", "finish-dispatch"]
        assert len(sender.calls) == 1
        assert audit.records == []


@pytest.mark.parametrize(
    ("message", "history"),
    (
        (
            "opaque speech",
            (ChatRecord(8, 1, "opaque-phase", "opaque-channel", "opaque-peer", "opaque peer", "opaque speech"),),
        ),
        (
            "opaque speech",
            (
                ChatRecord(7, 1, "opaque-phase", "opaque-channel", "opaque-self", "opaque self", "opaque speech"),
                ChatRecord(8, 1, "opaque-phase", "opaque-channel", "opaque-self", "opaque self", "different latest"),
            ),
        ),
        (
            "Opaque speech",
            (ChatRecord(8, 1, "opaque-phase", "opaque-channel", "opaque-self", "opaque self", "opaque speech"),),
        ),
        (
            "opaque speech!",
            (ChatRecord(8, 1, "opaque-phase", "opaque-channel", "opaque-self", "opaque self", "opaque speech"),),
        ),
    ),
)
@async_test
async def test_p6b_repeat_check_preserves_distinct_player_recency_case_and_punctuation(
    message: str, history: tuple[ChatRecord, ...]
) -> None:
    controller, request, deadline, state, audit, sender = _controller_case("chat")
    assert deadline is not None and state is not None and audit is not None
    result = controller.brain.output  # type: ignore[attr-defined]
    assert isinstance(result, BrainResult)
    controller.brain.output = replace(  # type: ignore[attr-defined]
        result, decision=ChatDecision("action:0", message)
    )
    retention = request.snapshot.history_retention
    request = replace(
        request,
        history=HistoryView(history, True, retention),
        co=CoView((), (), True, retention),
    )

    outcome = await controller.decide_and_send(request, dispatch_deadline=deadline)

    assert outcome.status is DecisionStatus.SENT
    assert state.calls == ["stage", "commit", "dispatch-start", "finish-dispatch"]
    assert len(sender.calls) == 1
    assert audit.records == []


@async_test
async def test_p6b_no_decision_commits_none_turn_then_finishes_without_send() -> None:
    controller, request, deadline, state, audit, sender = _controller_case("none")
    assert deadline is not None and state is not None and audit is not None
    outcome = await controller.decide_and_send(request, dispatch_deadline=deadline)
    assert outcome.status is DecisionStatus.NO_DECISION
    assert outcome.discussion is None
    assert state.calls == ["stage", "commit", "finish-no-action"]
    assert state.proposal is not None
    assert (state.proposal.decision_kind, state.proposal.option_id) == ("none", None)
    assert state.commit_ack is not None
    assert state.commit_ack.proposal_sha256 == canonical_sha256(state.proposal)
    assert sender.calls == []
    assert len(audit.records) == 1
    assert (audit.records[0].status, audit.records[0].reason) == (
        DiscussionTerminalStatus.NO_ACTION,
        DiscussionTerminalReason.EXPLICIT_NO_DECISION,
    )


@async_test
async def test_p6b_commit_and_dispatch_start_are_contiguous_before_sender_await() -> None:
    events: list[str] = []
    controller, request, deadline, state, audit, sender = _controller_case(
        "chat", events=events
    )
    assert deadline is not None and state is not None and audit is not None
    outcome = await controller.decide_and_send(request, dispatch_deadline=deadline)
    assert outcome.status is DecisionStatus.SENT
    assert events == ["commit", "dispatch-start", "sender-await"]
    assert state.calls == [
        "stage",
        "commit",
        "dispatch-start",
        "finish-dispatch",
    ]
    assert len(sender.calls) == 1
    assert audit.records == []


@async_test
async def test_p6b_context_free_raw_path_has_no_discussion_state_or_terminal() -> None:
    controller, request, deadline, state, audit, sender = _controller_case(
        "chat", contextful=False
    )
    assert deadline is None and state is None and audit is None
    outcome = await controller.decide_and_send(request)
    assert outcome.status is DecisionStatus.SENT
    assert outcome.discussion is None
    assert len(sender.calls) == 1
    assert controller.take_dispatched_decision(outcome.receipt) == ChatDecision(
        "action:0", "opaque speech"
    )
    assert controller.take_dispatched_decision(outcome.receipt) is None


@pytest.mark.parametrize("kind", ("none", "chat"))
@async_test
async def test_p6b_admitted_arbiter_forwards_discussion_trigger_and_captures_once(
    kind: str,
) -> None:
    class ImmediateLease:
        def __init__(self, invocation_id: str) -> None:
            self._invocation_id = invocation_id
            self.claimed = False
            self.released = False

        @property
        def invocation_id(self) -> str:
            return self._invocation_id

        async def claim(self) -> AdmissionStatus:
            self.claimed = True
            return AdmissionStatus.GRANTED

        def activate(self):
            assert self.claimed and not self.released
            return nullcontext()

        async def release(self) -> None:
            assert self.claimed and not self.released
            self.released = True

    class ImmediateAdmission:
        def __init__(self) -> None:
            self.requests: list[AdmissionRequest] = []
            self.lease: ImmediateLease | None = None

        async def acquire(self, request: AdmissionRequest) -> AdmissionResult:
            self.requests.append(request)
            self.lease = ImmediateLease(request.invocation_id)
            return AdmissionResult(AdmissionStatus.OFFERED, self.lease, 0)

        async def cancel(self, invocation_id: str) -> AdmissionStatus:
            raise AssertionError("admitted success must not cancel")

        async def replace_waiting(
            self, old_invocation_id: str, replacement: AdmissionRequest
        ) -> AdmissionResult:
            raise AssertionError("single admitted invocation must not replace")

        async def reserve_successor(
            self, active_invocation_id: str, successor: AdmissionRequest
        ) -> object:
            raise AssertionError("single admitted invocation has no successor")

        async def cancel_successor(
            self, successor_invocation_id: str
        ) -> AdmissionStatus:
            raise AssertionError("single admitted invocation has no successor")

        async def aclose(self) -> None:
            return None

    controller, request, deadline, state, audit, sender = _controller_case(kind)
    assert deadline is not None and deadline.discussion_trigger is not None
    assert state is not None and audit is not None
    assert isinstance(controller.brain, _ControllerBrain)
    handle = request.action_context.options[0].handle
    admission = ImmediateAdmission()
    starts: list[str] = []
    arbiter = BrainInvocationArbiter(
        controller=controller,
        admission=admission,  # type: ignore[arg-type]
        invocation_id_factory=lambda: "opaque-admitted-invocation",
        clock=lambda: 10.0,
    )
    result = await asyncio.wait_for(
        arbiter.invoke(
            owner="reaction_chat",
            priority=BrainInvocationPriority.REACTION,
            allowed_handles=(handle,),
            timeout_seconds=1.0,
            dispatch_deadline=deadline,
            on_brain_start=lambda: starts.append("started"),
        ),
        2.0,
    )

    assert starts == ["started"]
    assert len(admission.requests) == 1
    assert admission.requests[0].invocation_id == "opaque-admitted-invocation"
    assert admission.lease is not None and admission.lease.released is True
    assert state.calls.count("capture") == 1
    assert len(controller.brain.calls) == 1
    assert controller.brain.calls[0].discussion == state.capture_value
    assert (
        controller.brain.calls[0].discussion.trigger
        == deadline.discussion_trigger
    )
    if kind == "none":
        assert result.outcome.status is DecisionStatus.NO_DECISION
        assert result.dispatched_decision is None
        assert result.outcome.discussion is None
        assert state.calls == ["capture", "stage", "commit", "finish-no-action"]
        assert sender.calls == []
        assert len(audit.records) == 1
        assert audit.records[0].status is DiscussionTerminalStatus.NO_ACTION
    else:
        assert result.outcome.status is DecisionStatus.SENT
        assert result.dispatched_decision == ChatDecision(
            "action:0", "opaque speech"
        )
        assert result.outcome.discussion is not None
        assert result.outcome.discussion.capture_id == state.capture_value.capture_id
        assert state.calls == [
            "capture",
            "stage",
            "commit",
            "dispatch-start",
            "finish-dispatch",
        ]
        assert len(sender.calls) == 1
        assert audit.records == []
    await arbiter.stop()


def test_p6b_generation_record_ack_and_proposal_hash_linkage_mutation_matrix() -> None:
    capture = _capture()
    proposal = _proposal(capture, "chat", "opaque-chat-option")
    record = _generation_record(capture, proposal)
    record_hash = discussion_generation_record_sha256(record)
    assert serialize_ai_audit(record) == canonical_json_bytes(record) + b"\n"
    assert record_hash != discussion_generation_record_sha256(
        replace(record, provider_model="other-opaque-provider")
    )

    generation = _generation(
        capture,
        proposal,
        status=AiDiscussionGenerationStatus.DECISION,
        generation_hash=record_hash,
    )
    terminal = _transaction(_AuditSink()).terminal_from_delivery(
        capture=capture,
        generation=generation,
        proposal=proposal,
        delivery=_delivery(
            capture, proposal, DiscussionDeliveryStatus.NOT_DELIVERED
        ),
    )
    assert terminal.generation_record_sha256 == record_hash
    assert terminal.generation_audit_sequence == generation.audit_sequence
    assert terminal.proposal_sha256 == canonical_sha256(proposal)
    assert serialize_ai_audit(terminal) == canonical_json_bytes(terminal) + b"\n"

    valid_but_wrong_generations = (
        replace(generation, context_sha256="f" * 64),
        replace(generation, before_state_sha256="f" * 64),
        replace(generation, proposal_sha256="f" * 64),
        replace(
            generation,
            generation_status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        ),
    )
    for mutated in valid_but_wrong_generations:
        with pytest.raises(DiscussionTransactionError):
            _transaction(_AuditSink()).terminal_from_delivery(
                capture=capture,
                generation=mutated,
                proposal=proposal,
                delivery=_delivery(
                    capture, proposal, DiscussionDeliveryStatus.NOT_DELIVERED
                ),
            )

    changed_proposal = _proposal(capture, "chat", "other-chat-option")
    with pytest.raises(DiscussionTransactionError):
        _transaction(_AuditSink()).terminal_from_delivery(
            capture=capture,
            generation=generation,
            proposal=changed_proposal,
            delivery=_delivery(
                capture, changed_proposal, DiscussionDeliveryStatus.NOT_DELIVERED
            ),
        )


def test_p6b_terminal_status_reason_nullability_redaction_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _capture()
    tx = _transaction(_AuditSink())

    before_vectors = (
        (
            DiscussionAbortReason.PROMPT_REJECTED,
            AiDiscussionGenerationStatus.PROMPT_REJECTED,
        ),
        (
            DiscussionAbortReason.BACKEND_FAILED,
            AiDiscussionGenerationStatus.BACKEND_FAILED,
        ),
        (
            DiscussionAbortReason.FINAL_OUTPUT_INVALID,
            AiDiscussionGenerationStatus.OUTPUT_INVALID,
        ),
        (
            DiscussionAbortReason.CANCELLED_BEFORE_RESULT,
            AiDiscussionGenerationStatus.CANCELLED,
        ),
    )
    records: list[AiDiscussionTerminalRecord] = []
    for reason, generation_status in before_vectors:
        records.append(
            tx.terminal_from_abort(
                capture=capture,
                generation=_generation(
                    capture, None, status=generation_status
                ),
                abort=_abort(capture, reason),
                proposal=None,
            )
        )

    action = _proposal(capture, "chat", "opaque-chat-option")
    action_generation = _generation(
        capture, action, status=AiDiscussionGenerationStatus.DECISION
    )
    for reason in (
        DiscussionAbortReason.STAGE_FAILED,
        DiscussionAbortReason.CANCELLED_AFTER_STAGE,
        DiscussionAbortReason.STALE,
        DiscussionAbortReason.DEADLINE,
        DiscussionAbortReason.REVISION_CONFLICT,
    ):
        records.append(
            tx.terminal_from_abort(
                capture=capture,
                generation=action_generation,
                abort=_abort(capture, reason, action),
                proposal=action,
            )
        )
    for delivery_status in (
        DiscussionDeliveryStatus.NOT_DELIVERED,
        DiscussionDeliveryStatus.DELIVERY_UNKNOWN,
    ):
        records.append(
            tx.terminal_from_delivery(
                capture=capture,
                generation=action_generation,
                proposal=action,
                delivery=_delivery(capture, action, delivery_status),
            )
        )

    none = _proposal(capture)
    records.append(
        tx.terminal_from_delivery(
            capture=capture,
            generation=_generation(
                capture,
                none,
                status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
            ),
            proposal=none,
            delivery=_delivery(capture, none, DiscussionDeliveryStatus.NO_ACTION),
        )
    )

    accepted_evidence = EvidenceRef(
        EvidenceRecordKind.ACTION_ACCEPTED,
        9,
        EvidenceVisibility.AUTHORIZED_PRIVATE,
    )
    rejected_evidence = EvidenceRef(
        EvidenceRecordKind.ACTION_REJECTION,
        10,
        EvidenceVisibility.AUTHORIZED_PRIVATE,
    )
    observation_vectors = (
        (
            DiscussionObservationStatus.ACCEPTED,
            DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
            accepted_evidence,
        ),
        (
            DiscussionObservationStatus.REJECTED,
            DiscussionTerminalReason.AUTHORITATIVE_REJECTED,
            rejected_evidence,
        ),
        (
            DiscussionObservationStatus.RECOVERY_UNKNOWN,
            DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
            None,
        ),
        (
            DiscussionObservationStatus.RECOVERY_UNKNOWN,
            DiscussionTerminalReason.RECOVERY_GAP,
            accepted_evidence,
        ),
        (
            DiscussionObservationStatus.RECOVERY_UNKNOWN,
            DiscussionTerminalReason.PHASE_CHANGED,
            None,
        ),
        (
            DiscussionObservationStatus.RECOVERY_UNKNOWN,
            DiscussionTerminalReason.OWNER_STOPPED,
            None,
        ),
    )
    for status, reason, evidence in observation_vectors:
        records.append(
            tx.terminal_from_observation(
                capture=capture,
                generation=action_generation,
                proposal=action,
                observation=_observation(
                    capture, action, action_generation, status, evidence
                ),
                reason=reason,
            )
        )

    forbidden_names = {
        "prompt_json",
        "response_text",
        "proposal",
        "private_evidence_body",
        "player_mapping",
    }
    assert forbidden_names.isdisjoint(item.name for item in fields(AiDiscussionTerminalRecord))
    assert all(len(canonical_json_bytes(record)) <= 16 * 1024 for record in records)
    assert b"private-body-sentinel" not in b"".join(
        canonical_json_bytes(record) for record in records
    )

    no_action_record = records[-len(observation_vectors) - 1]
    with pytest.raises(DiscussionTransactionError):
        replace(
            no_action_record,
            status=DiscussionTerminalStatus.NOT_DELIVERED,
            reason=DiscussionTerminalReason.SEND_NOT_DELIVERED,
        )
    with pytest.raises(DiscussionTransactionError):
        replace(no_action_record, recorded_at_utc="2026-02-30T01:02:03Z")
    with pytest.raises(DiscussionTransactionError):
        replace(no_action_record, request_event_id="unexpected-receipt")
    with pytest.raises(DiscussionTransactionError):
        replace(no_action_record, authoritative_evidence=accepted_evidence)

    import ai_client.discussion.transaction as transaction_module

    encoded_size = len(canonical_json_bytes(no_action_record))
    monkeypatch.setattr(transaction_module, "_MAX_TERMINAL_BYTES", encoded_size - 1)
    with pytest.raises(DiscussionTransactionError, match="16 KiB"):
        replace(no_action_record)


@async_test
async def test_p6b_before_result_failure_and_cancel_abort_then_write_one_terminal() -> None:
    vectors = (
        (
            DiscussionAbortReason.PROMPT_REJECTED,
            AiDiscussionGenerationStatus.PROMPT_REJECTED,
        ),
        (
            DiscussionAbortReason.BACKEND_FAILED,
            AiDiscussionGenerationStatus.BACKEND_FAILED,
        ),
        (
            DiscussionAbortReason.FINAL_OUTPUT_INVALID,
            AiDiscussionGenerationStatus.REPAIR_FAILED,
        ),
        (
            DiscussionAbortReason.CANCELLED_BEFORE_RESULT,
            AiDiscussionGenerationStatus.CANCELLED,
        ),
    )
    for index, (reason, status) in enumerate(vectors, start=1):
        capture = _capture(ordinal=index)
        generation = _generation(
            capture,
            None,
            status=status,
            ordinal=2 if status is AiDiscussionGenerationStatus.REPAIR_FAILED else 1,
        )
        sink = _AuditSink()
        tx = _transaction(sink)
        record = tx.terminal_from_abort(
            capture=capture,
            generation=generation,
            abort=_abort(capture, reason),
            proposal=None,
        )
        acknowledgement = await tx.write_terminal(record)
        assert acknowledgement.durable is True
        assert sink.records == [record]

        wrong_status = replace(
            generation,
            generation_status=(
                AiDiscussionGenerationStatus.CANCELLED
                if status is not AiDiscussionGenerationStatus.CANCELLED
                else AiDiscussionGenerationStatus.PROMPT_REJECTED
            ),
        )
        with pytest.raises(DiscussionTransactionError):
            tx.terminal_from_abort(
                capture=capture,
                generation=wrong_status,
                abort=_abort(capture, reason),
                proposal=None,
            )

    # Regression for the cancellation race: a second cancel while the owned
    # CANCELLED-generation append is waiting must not cancel or duplicate it.
    _controller, request, _deadline, _state, _audit, _sender = _controller_case(
        "chat"
    )
    assert request.discussion is not None
    backend = _BlockingBackend()
    gated_audit = _GatedGenerationAudit()
    llm = LLMBrain(
        backend=backend,  # type: ignore[arg-type]
        audit=gated_audit,
        identity=LLMClientIdentity(request.discussion.game_id, request.discussion.player_id),
        utc_clock=lambda: NOW,
    )
    task = asyncio.create_task(llm.decide(request))
    await asyncio.wait_for(backend.entered.wait(), 1.0)
    task.cancel()
    await asyncio.wait_for(gated_audit.cancelled_write_started.wait(), 1.0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    gated_audit.release_cancelled_write.set()
    with pytest.raises(LLMInvocationError) as caught:
        await asyncio.wait_for(asyncio.shield(task), 2.0)
    assert caught.value.status is LLMInvocationStatus.CANCELLED
    generation = caught.value.discussion_ack
    assert generation is not None
    assert generation.generation_status is AiDiscussionGenerationStatus.CANCELLED
    assert generation.audit_sequence == 1
    assert len(gated_audit.records) == 1
    assert isinstance(gated_audit.records[0], AiDiscussionGenerationRecord)
    assert gated_audit.records[0].status is AiDiscussionGenerationStatus.CANCELLED

    state = _ControllerDiscussionState(request.discussion)
    transaction = DiscussionTransaction(
        state=state,
        audit=gated_audit,
        utc_clock=lambda: NOW,
    )
    abort = state.abort(
        request.discussion.capture_id,
        generation.request_id,
        DiscussionAbortReason.CANCELLED_BEFORE_RESULT,
        None,
    )
    terminal = transaction.terminal_from_abort(
        capture=request.discussion,
        generation=generation,
        abort=abort,
        proposal=None,
    )
    terminal_ack = await asyncio.wait_for(transaction.write_terminal(terminal), 1.0)
    assert terminal_ack.sequence == 2
    assert len(gated_audit.records) == 2
    assert gated_audit.records[1] == terminal
    assert terminal.generation_record_sha256 == generation.generation_record_sha256


@pytest.mark.parametrize("case", ("decision", "prompt_rejected"))
@async_test
async def test_p6b_delayed_durable_generation_branch_wins_repeated_cancellation(
    case: str,
) -> None:
    class ResponseBackend:
        def __init__(self, text: str) -> None:
            self.text = text
            self.calls = 0
            self.identity = BackendIdentity(
                "opaque-backend",
                "http://127.0.0.1:1",
                "/v1/chat/completions",
                "opaque-model",
                "e" * 64,
            )

        async def generate(self, request: object) -> StructuredGenerationResponse:
            self.calls += 1
            return StructuredGenerationResponse(
                request.request_id,  # type: ignore[attr-defined]
                self.text,
                "opaque-provider-model",
                "stop",
                LLMUsage(3, 4),
            )

        async def aclose(self) -> None:
            return None

    class DelayedAckAudit:
        def __init__(self) -> None:
            self.records: list[object] = []
            self.row_captured = asyncio.Event()
            self.release_ack = asyncio.Event()
            self.calls = 0

        async def start(self) -> None:
            return None

        async def write(self, record: object) -> AuditWriteAck:
            self.calls += 1
            self.records.append(record)
            self.row_captured.set()
            await self.release_ack.wait()
            return AuditWriteAck(sequence=1)

        async def aclose(self) -> None:
            return None

    _controller, request, _deadline, _state, _audit, _sender = _controller_case(
        "chat"
    )
    assert request.discussion is not None
    payload = {
        "decision": {
            "kind": "chat",
            "option_id": "action:0",
            "message": "opaque speech",
        },
        "discussion": {
            "schema_version": "aiwolf.discussion-proposal.v1",
            "base_revision": request.discussion.base_revision,
            "decision_kind": "chat",
            "option_id": "action:0",
            "speech_act": {"kind": "NONE"},
            "reaction": None,
            "assessment_updates": [],
            "claim_updates": [],
            "relation_updates": [],
            "strategy_update": None,
            "co_judgment": None,
            "pre_vote_reassessment": None,
        },
    }
    backend = ResponseBackend(json.dumps(payload))
    audit = DelayedAckAudit()
    kwargs: dict[str, object] = {}
    if case == "prompt_rejected":
        kwargs["discussion_config"] = DiscussionPromptConfig(max_prompt_bytes=1)
    brain = LLMBrain(
        backend=backend,  # type: ignore[arg-type]
        audit=audit,
        identity=LLMClientIdentity(request.discussion.game_id, request.discussion.player_id),
        utc_clock=lambda: NOW,
        config=LLMBrainConfig(),
        **kwargs,
    )

    task = asyncio.create_task(brain.decide(request))
    await asyncio.wait_for(audit.row_captured.wait(), 1.0)
    assert len(audit.records) == 1
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    audit.release_ack.set()

    if case == "decision":
        result = await asyncio.wait_for(asyncio.shield(task), 2.0)
        assert isinstance(result, BrainResult)
        assert result.audit_ack is not None
        acknowledgement = result.audit_ack
        expected_status = AiDiscussionGenerationStatus.DECISION
        assert backend.calls == 1
    else:
        with pytest.raises(LLMInvocationError) as caught:
            await asyncio.wait_for(asyncio.shield(task), 2.0)
        assert caught.value.status is LLMInvocationStatus.PROMPT_REJECTED
        assert caught.value.discussion_ack is not None
        acknowledgement = caught.value.discussion_ack
        expected_status = AiDiscussionGenerationStatus.PROMPT_REJECTED
        assert backend.calls == 0

    assert audit.calls == 1
    assert len(audit.records) == 1
    assert isinstance(audit.records[0], AiDiscussionGenerationRecord)
    assert audit.records[0].status is expected_status
    assert audit.records[0].schema_version == "aiwolf.ai-discussion-generation.v2"
    assert audit.records[0].prompt_rejection_code == (
        "PROMPT_TOO_LARGE" if case == "prompt_rejected" else None
    )
    assert acknowledgement.generation_status is expected_status
    assert acknowledgement.audit_sequence == 1
    assert not any(
        isinstance(record, AiDiscussionGenerationRecord)
        and record.status is AiDiscussionGenerationStatus.CANCELLED
        for record in audit.records
    )


@pytest.mark.parametrize("case", ("success", "fragment", "bad_envelope", "repair_rejected", "unknown_rejection"))
@async_test
async def test_contextful_repair_uses_distinct_transport_ids_and_preserves_logical_identity(case):
    import time
    from ai_client.llm.admission_broker import GenerationAdmissionBroker
    from ai_client.llm.admission_client import BrokerAdmissionSession, BrokeredStructuredLLMBackend
    from ai_client.llm.admission_types import AdmissionCredentials, AdmissionRequest, AdmissionStatus, GenerationPriority
    from ai_client.llm.prompt import PromptProjectionError, project_brain_input
    from ai_client.llm.types import AiDiscussionGenerationRecordV2, LLMBackendError, LLMBackendErrorCode

    _, request, _, _, _, _ = _controller_case("chat")
    capture = request.discussion
    assert capture is not None
    logical = "phase6:" + capture.capture_id
    proposal = _proposal(capture, "chat", "action:0")
    payload = json.dumps({
        "decision": {"kind": "chat", "option_id": "action:0", "message": "opaque speech"},
        "discussion": json.loads(canonical_json_bytes(proposal)),
    })

    class Responses:
        identity = BackendIdentity("opaque-backend", "http://127.0.0.1:1", "/v1/chat/completions", "opaque-model", "e" * 64)

        def __init__(self):
            self.requests = []

        async def generate(self, sent):
            self.requests.append(sent)
            first = "{}"
            if case == "fragment":
                invalid = json.loads(payload)
                invalid["decision"]["message"] = "x" * 200
                first = json.dumps(invalid)
            return StructuredGenerationResponse(sent.request_id, first if len(self.requests) == 1 else payload, "opaque-model", "stop", LLMUsage(3, 4))

        async def aclose(self):
            pass

    backend = Responses()
    token = "1" * 64
    broker = GenerationAdmissionBroker({"opaque-client": token}, backend, fairness_seed="repair-identity")
    await broker.start()
    session = None
    lease = None
    try:
        session = await BrokerAdmissionSession.connect(broker.ready.host, broker.ready.port, AdmissionCredentials("opaque-client", token))
        admitted = await asyncio.wait_for(session.acquire(AdmissionRequest("repair-invocation", GenerationPriority.REACTION, capture.trigger.phase, capture.trigger.day, 1, 1, time.monotonic() + 10.0)), 2.0)
        lease = admitted.lease
        assert lease is not None
        assert await asyncio.wait_for(lease.claim(), 2.0) is AdmissionStatus.GRANTED
        proxy = BrokeredStructuredLLMBackend(session)

        class CheckedResponse:
            identity = proxy.identity

            async def generate(self, sent):
                response = await proxy.generate(sent)
                return replace(response, request_id=logical) if case == "bad_envelope" else response

            async def aclose(self):
                pass

        audit = _GatedGenerationAudit()
        brain = LLMBrain(backend=CheckedResponse(), audit=audit, identity=LLMClientIdentity(capture.game_id, capture.player_id), utc_clock=lambda: NOW)
        context = nullcontext()
        if case in {"repair_rejected", "unknown_rejection"}:
            projection = project_brain_input(request, config=LLMBrainConfig())
            code = "PROMPT_TOO_LARGE" if case == "repair_rejected" else "private-exception-sentinel"
            context = patch("ai_client.llm.brain.build_repair_projection", side_effect=PromptProjectionError(code, projection=projection))
        with lease.activate(), context:
            if case in {"success", "fragment"}:
                result = await asyncio.wait_for(brain.decide(request), 5.0)
                assert isinstance(result, BrainResult)
                assert [sent.request_id for sent in backend.requests] == [logical + ":attempt:1", logical + ":attempt:2"]
                assert [row.request_id for row in audit.records] == [logical, logical]
                assert [row.attempt_ordinal for row in audit.records] == [1, 2]
                assert [row.status.value for row in audit.records] == ["OUTPUT_INVALID", "REPAIR_SUCCEEDED"]
                if case == "fragment":
                    assert audit.records[0].validation_code == "TEXT_BOUND"
                assert session._call_ordinals["repair-invocation"] == 2
                assert all(isinstance(row, AiDiscussionGenerationRecordV2) for row in audit.records)
                transaction = DiscussionTransaction(state=_UnusedState(), audit=audit, utc_clock=lambda: NOW)
                terminal = transaction.terminal_from_abort(capture=capture, generation=result.audit_ack, abort=_abort(capture, DiscussionAbortReason.STAGE_FAILED, result.discussion), proposal=result.discussion)
                assert terminal.request_id == logical
                assert terminal.final_attempt_ordinal == 2
                assert terminal.generation_record_sha256 == canonical_sha256(audit.records[1])
                with pytest.raises(LLMBackendError) as extra:
                    await proxy.generate(backend.requests[-1])
                assert extra.value.code is LLMBackendErrorCode.ADMISSION_PROTOCOL
                assert len(backend.requests) == 2
            else:
                with pytest.raises(LLMInvocationError) as caught:
                    await asyncio.wait_for(brain.decide(request), 5.0)
                expected = {"bad_envelope": LLMInvocationStatus.BACKEND_FAILED, "repair_rejected": LLMInvocationStatus.PROMPT_REJECTED, "unknown_rejection": LLMInvocationStatus.AUDIT_FAILED}[case]
                assert caught.value.status is expected
                assert "private-exception-sentinel" not in str(caught.value)
                assert len(backend.requests) == 1
                if case == "repair_rejected":
                    assert audit.records[-1].attempt_ordinal == 2
                    assert audit.records[-1].prompt_rejection_code == "PROMPT_TOO_LARGE"
                    assert audit.records[-1].validation_code is None
                    assert audit.records[-1].request_id == logical
                elif case == "bad_envelope":
                    assert caught.value.code == "RESPONSE_ENVELOPE_INVALID"
        await asyncio.wait_for(lease.release(), 2.0)
        lease = None
        assert broker.snapshot.claimed is None
        assert broker.snapshot.pending_total == 0
    finally:
        if lease is not None:
            await asyncio.wait_for(lease.release(), 2.0)
        if session is not None:
            await session.aclose()
        await broker.aclose()


@pytest.mark.parametrize("repair_limit", (0, 1))
@async_test
async def test_p6b_output_invalid_append_cancellation_stops_before_repair_and_links_terminal(
    repair_limit: int,
) -> None:
    class InvalidBackend:
        def __init__(self) -> None:
            self.calls = 0
            self.identity = BackendIdentity(
                "opaque-backend",
                "http://127.0.0.1:1",
                "/v1/chat/completions",
                "opaque-model",
                "e" * 64,
            )

        async def generate(self, request: object) -> StructuredGenerationResponse:
            self.calls += 1
            return StructuredGenerationResponse(
                request.request_id,  # type: ignore[attr-defined]
                "{}",
                "opaque-provider-model",
                "stop",
                LLMUsage(3, 4),
            )

        async def aclose(self) -> None:
            return None

    class GatedOutputInvalidAudit:
        def __init__(self) -> None:
            self.records: list[object] = []
            self.output_invalid_captured = asyncio.Event()
            self.release_output_invalid_ack = asyncio.Event()
            self.calls = 0

        async def start(self) -> None:
            return None

        async def write(self, record: object) -> AuditWriteAck:
            self.calls += 1
            sequence = self.calls
            self.records.append(record)
            if (
                isinstance(record, AiDiscussionGenerationRecord)
                and record.status is AiDiscussionGenerationStatus.OUTPUT_INVALID
            ):
                self.output_invalid_captured.set()
                await self.release_output_invalid_ack.wait()
            return AuditWriteAck(sequence=sequence)

        async def aclose(self) -> None:
            return None

    controller, request, deadline, state, _audit, sender = _controller_case("chat")
    assert request.discussion is not None and deadline is not None and state is not None
    backend = InvalidBackend()
    audit = GatedOutputInvalidAudit()
    brain = LLMBrain(
        backend=backend,  # type: ignore[arg-type]
        audit=audit,
        identity=LLMClientIdentity(request.discussion.game_id, request.discussion.player_id),
        config=LLMBrainConfig(max_schema_repair_attempts=repair_limit),
        utc_clock=lambda: NOW,
    )
    controller.brain = brain
    assert controller._discussion is not None
    controller._discussion.audit = audit

    task = asyncio.create_task(
        controller.decide_and_send(request, dispatch_deadline=deadline)
    )
    await asyncio.wait_for(audit.output_invalid_captured.wait(), 1.0)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    audit.release_output_invalid_ack.set()

    outcome = await asyncio.wait_for(asyncio.shield(task), 2.0)
    assert outcome.status is DecisionStatus.CANCELLED
    assert backend.calls == 1
    assert brain.snapshot().backend_calls == 1
    assert brain.snapshot().repair_attempts == 0
    assert state.calls == ["abort"]
    assert sender.calls == []
    assert audit.calls == 3
    assert len(audit.records) == 3
    invalid, cancelled, terminal = audit.records
    assert isinstance(invalid, AiDiscussionGenerationRecord)
    assert invalid.attempt_ordinal == 1
    assert invalid.status is AiDiscussionGenerationStatus.OUTPUT_INVALID
    assert isinstance(cancelled, AiDiscussionGenerationRecord)
    assert cancelled.attempt_ordinal == 2
    assert cancelled.status is AiDiscussionGenerationStatus.CANCELLED
    assert isinstance(terminal, AiDiscussionTerminalRecord)
    assert terminal.final_attempt_ordinal == 2
    assert terminal.generation_audit_sequence == 2
    assert terminal.generation_record_sha256 == discussion_generation_record_sha256(
        cancelled
    )
    assert terminal.status is DiscussionTerminalStatus.ABORTED
    assert terminal.reason is DiscussionTerminalReason.CANCELLED_BEFORE_RESULT


@async_test
async def test_p6b_output_invalid_generation_sink_failure_remains_ackless() -> None:
    class InvalidBackend:
        def __init__(self) -> None:
            self.calls = 0
            self.identity = BackendIdentity(
                "opaque-backend",
                "http://127.0.0.1:1",
                "/v1/chat/completions",
                "opaque-model",
                "e" * 64,
            )

        async def generate(self, request: object) -> StructuredGenerationResponse:
            self.calls += 1
            return StructuredGenerationResponse(
                request.request_id,  # type: ignore[attr-defined]
                "{}",
                "opaque-provider-model",
                "stop",
                LLMUsage(3, 4),
            )

        async def aclose(self) -> None:
            return None

    class FailingGenerationAudit:
        def __init__(self) -> None:
            self.calls = 0

        async def start(self) -> None:
            return None

        async def write(self, record: object) -> AuditWriteAck:
            self.calls += 1
            raise OSError("private sink detail")

        async def aclose(self) -> None:
            return None

    _controller, request, _deadline, _state, _audit, _sender = _controller_case(
        "chat"
    )
    assert request.discussion is not None
    backend = InvalidBackend()
    audit = FailingGenerationAudit()
    brain = LLMBrain(
        backend=backend,  # type: ignore[arg-type]
        audit=audit,
        identity=LLMClientIdentity(request.discussion.game_id, request.discussion.player_id),
        utc_clock=lambda: NOW,
    )
    with pytest.raises(LLMInvocationError) as caught:
        await brain.decide(request)
    assert caught.value.status is LLMInvocationStatus.AUDIT_FAILED
    assert caught.value.discussion_ack is None
    assert caught.value.discussion_abort_reason is None
    assert backend.calls == 1
    assert audit.calls == 1
    assert brain.snapshot().repair_attempts == 0


@pytest.mark.parametrize(
    ("trigger_case", "expected_outcome", "expected_reason"),
    (
        (
            "caller_cancel",
            DecisionStatus.CANCELLED,
            DiscussionTerminalReason.CANCELLED_AFTER_STAGE,
        ),
        (
            "brain_timeout",
            DecisionStatus.TIMED_OUT,
            DiscussionTerminalReason.CANCELLED_AFTER_STAGE,
        ),
        (
            "dispatch_deadline",
            DecisionStatus.DEADLINE_SUPPRESSED,
            DiscussionTerminalReason.DEADLINE,
        ),
        ("stale_world", DecisionStatus.STALE, DiscussionTerminalReason.STALE),
        (
            "controller_stop",
            DecisionStatus.CANCELLED,
            DiscussionTerminalReason.CANCELLED_AFTER_STAGE,
        ),
    ),
)
@async_test
async def test_p6b_controller_waits_past_legacy_grace_for_durable_generation_then_aborts(
    trigger_case: str,
    expected_outcome: DecisionStatus,
    expected_reason: DiscussionTerminalReason,
) -> None:
    class ResponseBackend:
        def __init__(self, text: str) -> None:
            self.text = text
            self.identity = BackendIdentity(
                "opaque-backend",
                "http://127.0.0.1:1",
                "/v1/chat/completions",
                "opaque-model",
                "e" * 64,
            )

        async def generate(self, request: object) -> StructuredGenerationResponse:
            return StructuredGenerationResponse(
                request.request_id,  # type: ignore[attr-defined]
                self.text,
                "opaque-provider-model",
                "stop",
                LLMUsage(3, 4),
            )

        async def aclose(self) -> None:
            return None

    class DelayedGenerationAudit:
        def __init__(self) -> None:
            self.records: list[object] = []
            self.generation_captured = asyncio.Event()
            self.release_generation_ack = asyncio.Event()

        async def start(self) -> None:
            return None

        async def write(self, record: object) -> AuditWriteAck:
            self.records.append(record)
            sequence = len(self.records)
            if isinstance(record, AiDiscussionGenerationRecord):
                self.generation_captured.set()
                await self.release_generation_ack.wait()
            return AuditWriteAck(sequence=sequence)

        async def aclose(self) -> None:
            return None

    async def wait_for_cancel_status(
        controller: BrainController, expected: DecisionStatus
    ) -> None:
        while controller._active is not None:  # type: ignore[attr-defined]
            if controller._active.cancel_status is expected:  # type: ignore[attr-defined]
                return
            await asyncio.sleep(0)
        raise AssertionError(f"controller ended before recording {expected.value}")

    controller, request, deadline, state, _audit, sender = _controller_case("chat")
    assert request.discussion is not None
    assert deadline is not None and state is not None
    payload = {
        "decision": {
            "kind": "chat",
            "option_id": "action:0",
            "message": "opaque speech",
        },
        "discussion": {
            "schema_version": "aiwolf.discussion-proposal.v1",
            "base_revision": request.discussion.base_revision,
            "decision_kind": "chat",
            "option_id": "action:0",
            "speech_act": {"kind": "NONE"},
            "reaction": None,
            "assessment_updates": [],
            "claim_updates": [],
            "relation_updates": [],
            "strategy_update": None,
            "co_judgment": None,
            "pre_vote_reassessment": None,
        },
    }
    audit = DelayedGenerationAudit()
    controller.brain = LLMBrain(
        backend=ResponseBackend(json.dumps(payload)),  # type: ignore[arg-type]
        audit=audit,
        identity=LLMClientIdentity(request.discussion.game_id, request.discussion.player_id),
        utc_clock=lambda: NOW,
    )
    controller.config = BrainRunConfig(
        max_decision_seconds=0.05 if trigger_case == "brain_timeout" else 1.0,
        cancellation_grace_seconds=0.005,
    )
    assert controller._discussion is not None
    controller._discussion.audit = audit
    clock = [10.0]
    controller._clock = lambda: clock[0]
    world = controller.world
    task = asyncio.create_task(
        controller.decide_and_send(
            request,
            timeout_seconds=(0.05 if trigger_case == "brain_timeout" else None),
            dispatch_deadline=deadline,
        )
    )
    await asyncio.wait_for(audit.generation_captured.wait(), 1.0)
    assert len(audit.records) == 1
    assert isinstance(audit.records[0], AiDiscussionGenerationRecord)
    assert audit.records[0].status is AiDiscussionGenerationStatus.DECISION

    stop_task: asyncio.Task[None] | None = None
    if trigger_case == "caller_cancel":
        task.cancel()
    elif trigger_case == "dispatch_deadline":
        clock[0] = deadline.not_after_monotonic
        world._update.set()  # type: ignore[attr-defined]
    elif trigger_case == "stale_world":
        world._snapshot = replace(  # type: ignore[attr-defined]
            world._snapshot,  # type: ignore[attr-defined]
            freshness=Freshness.STALE,
        )
        world._update.set()  # type: ignore[attr-defined]
    elif trigger_case == "controller_stop":
        stop_task = asyncio.create_task(controller.stop())
    elif trigger_case != "brain_timeout":
        raise AssertionError(f"unsupported trigger case: {trigger_case}")

    await asyncio.wait_for(wait_for_cancel_status(controller, expected_outcome), 1.0)
    # Hold the ACK for four times the legacy grace.  A contextful controller
    # must retain ownership instead of reporting an ackless outcome.
    await asyncio.sleep(0.02)
    assert not task.done()
    assert controller.unresponsive is False
    if stop_task is not None:
        assert not stop_task.done()
        assert controller._discussion is not None
        assert controller._discussion._closed is False
    if trigger_case == "caller_cancel":
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()

    audit.release_generation_ack.set()
    outcome = await asyncio.wait_for(asyncio.shield(task), 2.0)
    if stop_task is not None:
        await asyncio.wait_for(asyncio.shield(stop_task), 2.0)
        assert controller._discussion is not None
        assert controller._discussion._closed is True

    assert outcome.status is expected_outcome
    assert state.calls == ["stage", "abort"]
    assert sender.calls == []
    assert len(audit.records) == 2
    generation, terminal = audit.records
    assert isinstance(generation, AiDiscussionGenerationRecord)
    assert isinstance(terminal, AiDiscussionTerminalRecord)
    assert terminal.status is DiscussionTerminalStatus.ABORTED
    assert terminal.reason is expected_reason
    assert terminal.generation_audit_sequence == 1
    assert terminal.generation_record_sha256 == discussion_generation_record_sha256(
        generation
    )
    assert not any(
        isinstance(record, AiDiscussionGenerationRecord)
        and record.status is AiDiscussionGenerationStatus.CANCELLED
        for record in audit.records
    )


@async_test
async def test_p6b_context_free_cancellation_keeps_bounded_legacy_grace() -> None:
    class ResistantBrain:
        def __init__(self) -> None:
            self.release = asyncio.Event()
            self.finished = asyncio.Event()

        async def decide(self, request: BrainInput) -> object:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                await self.release.wait()
            finally:
                self.finished.set()
            return ChatDecision("action:0", "opaque speech")

    controller, request, deadline, state, audit, sender = _controller_case(
        "chat", contextful=False
    )
    assert deadline is None and state is None and audit is None
    brain = ResistantBrain()
    controller.brain = brain  # type: ignore[assignment]
    controller.config = BrainRunConfig(
        max_decision_seconds=0.01,
        cancellation_grace_seconds=0.0,
    )
    outcome = await asyncio.wait_for(
        controller.decide_and_send(request, timeout_seconds=0.01), 1.0
    )
    assert outcome.status is DecisionStatus.TIMED_OUT
    assert controller.unresponsive is True
    assert sender.calls == []
    brain.release.set()
    await asyncio.wait_for(brain.finished.wait(), 1.0)


def test_p6b_after_stage_stale_deadline_cancel_revision_failures_abort_once() -> None:
    capture = _capture()
    proposal = _proposal(capture, "vote", "opaque-vote-option")
    generation = _generation(
        capture, proposal, status=AiDiscussionGenerationStatus.DECISION
    )
    for reason in (
        DiscussionAbortReason.CANCELLED_AFTER_STAGE,
        DiscussionAbortReason.STALE,
        DiscussionAbortReason.DEADLINE,
        DiscussionAbortReason.REVISION_CONFLICT,
    ):
        record = _transaction(_AuditSink()).terminal_from_abort(
            capture=capture,
            generation=generation,
            abort=_abort(capture, reason, proposal),
            proposal=proposal,
        )
        assert record.status is DiscussionTerminalStatus.ABORTED
        assert record.reason.value == reason.value
        assert record.proposal_sha256 == generation.proposal_sha256
        assert record.after_state_sha256 is None
        assert record.committed_revision is None


def test_p6b_not_delivered_unknown_and_local_sent_correlation_paths_are_exact() -> None:
    capture = _capture()
    proposal = _proposal(capture, "ability", "opaque-ability-option")
    generation = _generation(
        capture, proposal, status=AiDiscussionGenerationStatus.DECISION
    )
    tx = _transaction(_AuditSink())
    not_delivered = tx.terminal_from_delivery(
        capture=capture,
        generation=generation,
        proposal=proposal,
        delivery=_delivery(
            capture, proposal, DiscussionDeliveryStatus.NOT_DELIVERED
        ),
    )
    unknown = tx.terminal_from_delivery(
        capture=capture,
        generation=generation,
        proposal=proposal,
        delivery=_delivery(
            capture, proposal, DiscussionDeliveryStatus.DELIVERY_UNKNOWN
        ),
    )
    assert (not_delivered.status, not_delivered.reason) == (
        DiscussionTerminalStatus.NOT_DELIVERED,
        DiscussionTerminalReason.SEND_NOT_DELIVERED,
    )
    assert (unknown.status, unknown.reason) == (
        DiscussionTerminalStatus.DELIVERY_UNKNOWN,
        DiscussionTerminalReason.SEND_DELIVERY_UNKNOWN,
    )
    assert not_delivered.request_event_id is None
    assert unknown.send_connection_generation is None

    # A LOCAL_SENT DeliveryAck also needs its byte-equal populated correlation;
    # an incomplete receipt mutation is rejected by the closed acknowledgement.
    with pytest.raises(DiscussionValidationError):
        replace(
            _delivery(capture, proposal, DiscussionDeliveryStatus.NOT_DELIVERED),
            status=DiscussionDeliveryStatus.LOCAL_SENT,
            request_event_id="opaque-request-event",
            send_connection_generation=4,
        )


@async_test
async def test_p6b_repair_links_only_final_generation_to_one_terminal() -> None:
    capture = _capture()
    proposal = _proposal(capture)
    initial = _generation(
        capture,
        None,
        status=AiDiscussionGenerationStatus.OUTPUT_INVALID,
        sequence=11,
        ordinal=1,
        generation_hash="1" * 64,
    )
    final = _generation(
        capture,
        proposal,
        status=AiDiscussionGenerationStatus.REPAIR_SUCCEEDED,
        sequence=12,
        ordinal=2,
        generation_hash="2" * 64,
    )
    assert initial.audit_sequence != final.audit_sequence
    sink = _AuditSink()
    tx = _transaction(sink)
    record = tx.terminal_from_delivery(
        capture=capture,
        generation=final,
        proposal=proposal,
        delivery=_delivery(capture, proposal, DiscussionDeliveryStatus.NO_ACTION),
    )
    await tx.write_terminal(record)
    assert record.final_attempt_ordinal == 2
    assert record.generation_audit_sequence == 12
    assert record.generation_record_sha256 == "2" * 64
    assert sink.records == [record]
    with pytest.raises(DiscussionTransactionError, match="duplicate"):
        await tx.write_terminal(record)
    assert sink.records == [record]


@async_test
async def test_p6b_audit_sink_failure_is_the_only_no_terminal_exception() -> None:
    class FailingSink:
        def __init__(self) -> None:
            self.calls = 0

        async def write(self, record: object) -> AuditWriteAck:
            self.calls += 1
            raise OSError("private sink detail")

    capture = _capture()
    proposal = _proposal(capture)
    generation = _generation(
        capture,
        proposal,
        status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
    )
    sink = FailingSink()
    tx = _transaction(sink)
    record = tx.terminal_from_delivery(
        capture=capture,
        generation=generation,
        proposal=proposal,
        delivery=_delivery(capture, proposal, DiscussionDeliveryStatus.NO_ACTION),
    )
    with pytest.raises(DiscussionTransactionError, match="audit write failed"):
        await tx.write_terminal(record)
    with pytest.raises(DiscussionTransactionError, match="duplicate"):
        await tx.write_terminal(record)
    assert sink.calls == 1

    audit_failed_abort = AbortAck(
        capture_id=capture.capture_id,
        request_id=f"phase6:{capture.capture_id}",
        base_revision=capture.base_revision,
        context_sha256=capture.context_sha256,
        before_state_sha256=capture.state_sha256,
        after_state_sha256=None,
        proposal_sha256=None,
        reason=DiscussionAbortReason.AUDIT_FAILED,
        stage_existed=False,
        aborted=True,
    )
    with pytest.raises(DiscussionTransactionError, match="cannot manufacture"):
        _transaction(_AuditSink()).terminal_from_abort(
            capture=capture,
            generation=_generation(
                capture,
                None,
                status=AiDiscussionGenerationStatus.BACKEND_FAILED,
            ),
            abort=audit_failed_abort,
            proposal=None,
        )


@async_test
async def test_p6b_terminal_ledger_capacity_511_512_513_fails_closed() -> None:
    for invalid in (0, -1, True, 513):
        with pytest.raises(
            ValueError,
            match=r"^max_terminal_reservations must be an int in \[1, 512\]$",
        ):
            DiscussionTransaction(
                state=_UnusedState(),  # type: ignore[arg-type]
                audit=_AuditSink(),
                max_terminal_reservations=invalid,  # type: ignore[arg-type]
            )

    sink = _AuditSink()
    transaction = _transaction(sink)
    for ordinal in range(1, 513):
        acknowledgement = await transaction.write_terminal(
            _terminal_for_ordinal(transaction, ordinal)
        )
        assert acknowledgement.sequence == ordinal
        if ordinal == 511:
            assert len(transaction._terminal_reservations) == 511
    assert len(transaction._terminal_reservations) == 512
    assert len(sink.records) == 512
    with pytest.raises(
        DiscussionTransactionError,
        match=r"^terminal reservation capacity exhausted$",
    ):
        await transaction.write_terminal(_terminal_for_ordinal(transaction, 513))
    assert len(transaction._terminal_reservations) == 512
    assert len(sink.records) == 512

    lower_sink = _AuditSink()
    lower = _transaction(lower_sink, max_terminal_reservations=2)
    assert len(lower._terminal_reservations) == 0
    await lower.write_terminal(_terminal_for_ordinal(lower, 1))
    assert len(lower._terminal_reservations) == 1
    await lower.write_terminal(_terminal_for_ordinal(lower, 2))
    assert len(lower._terminal_reservations) == 2
    with pytest.raises(
        DiscussionTransactionError,
        match=r"^terminal reservation capacity exhausted$",
    ):
        await lower.write_terminal(_terminal_for_ordinal(lower, 3))
    assert len(lower._terminal_reservations) == 2
    assert len(lower_sink.records) == 2


@async_test
async def test_p6b_terminal_ledger_duplicate_precedes_capacity_inflight_and_durable() -> None:
    class GatedSink:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def write(self, record: object) -> AuditWriteAck:
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return AuditWriteAck(sequence=self.calls)

    sink = GatedSink()
    transaction = _transaction(sink, max_terminal_reservations=1)
    record = _terminal_for_ordinal(transaction, 1)
    conflicting = replace(
        record,
        recorded_at_utc="2026-09-13T01:02:03.456788Z",
    )
    first = asyncio.create_task(transaction.write_terminal(record))
    await asyncio.wait_for(sink.started.wait(), 1.0)
    for duplicate in (record, conflicting):
        with pytest.raises(
            DiscussionTransactionError,
            match=r"^duplicate terminal submission$",
        ):
            await transaction.write_terminal(duplicate)
    with pytest.raises(
        DiscussionTransactionError,
        match=r"^terminal reservation capacity exhausted$",
    ):
        await transaction.write_terminal(_terminal_for_ordinal(transaction, 2))
    assert sink.calls == 1

    sink.release.set()
    acknowledgement = await first
    assert acknowledgement.sequence == 1
    for duplicate in (record, conflicting):
        with pytest.raises(
            DiscussionTransactionError,
            match=r"^duplicate terminal submission$",
        ):
            await transaction.write_terminal(duplicate)
    assert sink.calls == 1


@async_test
async def test_p6b_terminal_ledger_cancel_and_failure_keep_reservation() -> None:
    class GatedSink:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def write(self, record: object) -> AuditWriteAck:
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return AuditWriteAck(sequence=1)

    gated = GatedSink()
    cancelled_transaction = _transaction(gated)
    cancelled_record = _terminal_for_ordinal(cancelled_transaction, 1)
    writer = asyncio.create_task(
        cancelled_transaction.write_terminal(cancelled_record)
    )
    await asyncio.wait_for(gated.started.wait(), 1.0)
    writer.cancel()
    await asyncio.sleep(0)
    writer.cancel()
    await asyncio.sleep(0)
    assert not writer.done()
    gated.release.set()
    with pytest.raises(asyncio.CancelledError):
        await writer
    with pytest.raises(
        DiscussionTransactionError,
        match=r"^duplicate terminal submission$",
    ):
        await cancelled_transaction.write_terminal(cancelled_record)
    assert gated.calls == 1

    class FailureSink:
        def __init__(self, mode: str) -> None:
            self.mode = mode
            self.calls = 0

        async def write(self, record: object) -> object:
            self.calls += 1
            if self.mode == "exception":
                raise OSError("private sink detail")
            if self.mode == "cancelled":
                raise asyncio.CancelledError
            return object()

    for offset, mode in enumerate(("exception", "cancelled", "invalid"), start=2):
        sink = FailureSink(mode)
        transaction = _transaction(sink)
        record = _terminal_for_ordinal(transaction, offset)
        with pytest.raises(DiscussionTransactionError):
            await transaction.write_terminal(record)
        assert len(transaction._terminal_reservations) == 1
        with pytest.raises(
            DiscussionTransactionError,
            match=r"^duplicate terminal submission$",
        ):
            await transaction.write_terminal(record)
        assert sink.calls == 1


@async_test
async def test_p6b_terminal_ledger_close_is_irreversible_for_direct_helpers() -> None:
    class GatedSink:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def write(self, record: object) -> AuditWriteAck:
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return AuditWriteAck(sequence=41)

    sink = GatedSink()
    transaction = _transaction(sink)
    old_record = _terminal_for_ordinal(transaction, 1)
    writer = asyncio.create_task(transaction.write_terminal(old_record))
    await asyncio.wait_for(sink.started.wait(), 1.0)
    assert len(transaction._terminal_reservations) == 1

    transaction.close()
    transaction.close()
    assert transaction._closed is True
    assert transaction._terminal_reservations == set()
    assert not writer.done()
    sink.release.set()
    acknowledgement = await writer
    assert acknowledgement.sequence == 41

    for record in (old_record, _terminal_for_ordinal(transaction, 2)):
        with pytest.raises(
            DiscussionTransactionError,
            match=r"^discussion transaction is closed$",
        ):
            await transaction.write_terminal(record)
    assert sink.calls == 1


@async_test
async def test_p6b_terminal_ledger_stores_only_digest_keys() -> None:
    sink = _AuditSink()
    transaction = _transaction(sink)
    records = tuple(_terminal_for_ordinal(transaction, ordinal) for ordinal in range(1, 4))
    for record in records:
        await transaction.write_terminal(record)

    ledger = transaction._terminal_reservations
    assert type(ledger) is set
    assert len(ledger) == 3
    assert all(type(key) is bytes and len(key) == 32 for key in ledger)
    assert ledger == {bytes.fromhex(record.capture_id) for record in records}
    assert not any(isinstance(item, AiDiscussionTerminalRecord) for item in ledger)
    assert not hasattr(transaction, "_terminal_records")
    assert all(b"opaque" not in key for key in ledger)


@async_test
async def test_p6b_terminal_writer_is_concurrent_and_cancel_safe_exactly_once() -> None:
    class GatedSink:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def write(self, record: object) -> AuditWriteAck:
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return AuditWriteAck(sequence=23)

    capture = _capture()
    proposal = _proposal(capture)
    generation = _generation(
        capture,
        proposal,
        status=AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
    )
    sink = GatedSink()
    tx = _transaction(sink)
    record = tx.terminal_from_delivery(
        capture=capture,
        generation=generation,
        proposal=proposal,
        delivery=_delivery(capture, proposal, DiscussionDeliveryStatus.NO_ACTION),
    )
    first = asyncio.create_task(tx.write_terminal(record))
    await sink.started.wait()
    with pytest.raises(DiscussionTransactionError, match="duplicate"):
        await tx.write_terminal(record)
    first.cancel()
    await asyncio.sleep(0)
    assert not first.done()
    sink.release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert sink.calls == 1
    with pytest.raises(DiscussionTransactionError, match="duplicate"):
        await tx.write_terminal(record)


@async_test
async def test_p6b_controller_double_cancel_during_terminal_write_keeps_one_durable_terminal() -> None:
    class GatedTerminalSink:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.records: list[AiDiscussionTerminalRecord] = []
            self.calls = 0

        async def write(self, record: object) -> AuditWriteAck:
            assert isinstance(record, AiDiscussionTerminalRecord)
            self.calls += 1
            self.started.set()
            await self.release.wait()
            self.records.append(record)
            return AuditWriteAck(sequence=31)

    controller, request, deadline, state, _audit, sender = _controller_case("none")
    assert deadline is not None and state is not None
    gated = GatedTerminalSink()
    assert controller._discussion is not None
    controller._discussion.audit = gated
    task = asyncio.create_task(
        controller.decide_and_send(request, dispatch_deadline=deadline)
    )
    await asyncio.wait_for(gated.started.wait(), 1.0)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    gated.release.set()
    outcome = await asyncio.wait_for(asyncio.shield(task), 2.0)
    assert outcome.status is DecisionStatus.CANCELLED
    assert state.calls == ["stage", "commit", "finish-no-action"]
    assert sender.calls == []
    assert gated.calls == 1
    assert len(gated.records) == 1
    assert gated.records[0].status is DiscussionTerminalStatus.NO_ACTION


@async_test
async def test_p6b_generation_and_terminal_share_one_fifo_writer(
) -> None:
    capture = _capture()
    proposal = _proposal(capture, "chat", "opaque-chat-option")
    generation_record = _generation_record(capture, proposal)
    class MemoryHandle(io.BytesIO):
        def fileno(self) -> int:
            return 0

    handle = MemoryHandle()
    with (
        patch("ai_client.llm.audit._open_binary_append", return_value=handle),
        patch("ai_client.llm.audit.os.fsync", return_value=None),
    ):
        sink = JsonlAiAuditSink(Path("unused-ai.jsonl"))
        await sink.start()
        generation_write = await sink.write(generation_record)
        generation = _generation(
            capture,
            proposal,
            status=AiDiscussionGenerationStatus.DECISION,
            sequence=generation_write.sequence,
            generation_hash=discussion_generation_record_sha256(generation_record),
        )
        tx = DiscussionTransaction(
            state=_UnusedState(),  # type: ignore[arg-type]
            audit=sink,
            utc_clock=lambda: NOW,
        )
        terminal = tx.terminal_from_delivery(
            capture=capture,
            generation=generation,
            proposal=proposal,
            delivery=_delivery(
                capture, proposal, DiscussionDeliveryStatus.NOT_DELIVERED
            ),
        )
        terminal_write = await tx.write_terminal(terminal)
        payload = handle.getvalue()
        await sink.aclose()

        assert (generation_write.sequence, terminal_write.sequence) == (1, 2)
        assert payload == serialize_ai_audit(
            generation_record
        ) + serialize_ai_audit(terminal)
