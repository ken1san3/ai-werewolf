"""Closed terminal audit records and the single Phase 6 transaction helper.

This module stays below ``ai_client.brain`` and deliberately knows nothing about
LLM implementations.  The shared audit sink is supplied as a tiny structural
port so the Brain controller can use the same FIFO writer without importing the
higher ``ai_client.llm`` package.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Literal, Protocol

from .context import canonical_json_bytes, canonical_sha256
from .model import (
    AbortAck,
    AiDiscussionGenerationStatus,
    DeliveryAck,
    DiscussionAbortReason,
    DiscussionCapture,
    DiscussionDeliveryStatus,
    DiscussionGenerationAck,
    DiscussionProposal,
    DiscussionStatePort,
    DiscussionTerminalReason,
    DiscussionTerminalStatus,
    DiscussionValidationError,
    EvidenceRef,
    ObservationAck,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_MAX_TERMINAL_BYTES = 16 * 1024
_MAX_TERMINAL_RESERVATIONS = 512
_TERMINAL_RESERVATION_CONFIG_ERROR = (
    "max_terminal_reservations must be an int in [1, 512]"
)

_SUCCESSFUL_GENERATION_STATUSES = frozenset(
    {
        AiDiscussionGenerationStatus.DECISION,
        AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
        AiDiscussionGenerationStatus.REPAIR_SUCCEEDED,
    }
)
_PRE_RESULT_GENERATION_STATUSES = {
    DiscussionTerminalReason.PROMPT_REJECTED: frozenset(
        {AiDiscussionGenerationStatus.PROMPT_REJECTED}
    ),
    DiscussionTerminalReason.BACKEND_FAILED: frozenset(
        {AiDiscussionGenerationStatus.BACKEND_FAILED}
    ),
    DiscussionTerminalReason.FINAL_OUTPUT_INVALID: frozenset(
        {
            AiDiscussionGenerationStatus.OUTPUT_INVALID,
            AiDiscussionGenerationStatus.REPAIR_FAILED,
        }
    ),
    DiscussionTerminalReason.CANCELLED_BEFORE_RESULT: frozenset(
        {AiDiscussionGenerationStatus.CANCELLED}
    ),
}


class DiscussionTransactionError(DiscussionValidationError):
    """A serial Phase 6 transaction or durable terminal write failed closed."""


class DiscussionTerminalAuditSink(Protocol):
    async def write(self, record: object) -> object: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_id(name: str, value: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 128
        or len(value.encode("utf-8")) > 512
    ):
        raise DiscussionTransactionError(f"{name} must be a bounded identifier")


def _require_hash(name: str, value: object) -> None:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise DiscussionTransactionError(f"{name} must be lowercase SHA-256")


def _require_int(name: str, value: object, *, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise DiscussionTransactionError(f"{name} must be an integer >= {minimum}")


def _require_utc_timestamp(value: object) -> None:
    if type(value) is not str or _RFC3339_RE.fullmatch(value) is None:
        raise DiscussionTransactionError("recorded_at_utc must be normalized UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise DiscussionTransactionError(
            "recorded_at_utc must be a real RFC3339 UTC timestamp"
        ) from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise DiscussionTransactionError("recorded_at_utc must be normalized UTC")


def _require_equal(name: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise DiscussionTransactionError(f"{name} mismatch")


@dataclass(frozen=True)
class AiDiscussionTerminalRecord:
    schema_version: Literal["aiwolf.ai-discussion-terminal.v1"]
    recorded_at_utc: str
    game_id: str
    player_id: str
    request_id: str
    capture_id: str
    phase: str
    day: int
    final_attempt_ordinal: Literal[1, 2]
    generation_audit_sequence: int
    generation_record_sha256: str
    context_sha256: str
    before_state_sha256: str
    after_state_sha256: str | None
    proposal_sha256: str | None
    base_revision: int
    committed_revision: int | None
    decision_kind: Literal["none", "chat", "vote", "ability", "co_declare"] | None
    option_id: str | None
    status: DiscussionTerminalStatus
    reason: DiscussionTerminalReason
    request_event_id: str | None
    send_connection_generation: int | None
    authoritative_evidence: EvidenceRef | None

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.ai-discussion-terminal.v1":
            raise DiscussionTransactionError("invalid terminal schema_version")
        _require_utc_timestamp(self.recorded_at_utc)
        for name in ("game_id", "player_id", "phase"):
            _require_id(name, getattr(self, name))
        _require_hash("capture_id", self.capture_id)
        if self.request_id != f"phase6:{self.capture_id}":
            raise DiscussionTransactionError("terminal request_id must derive from capture_id")
        _require_int("day", self.day)
        if type(self.final_attempt_ordinal) is not int or self.final_attempt_ordinal not in {1, 2}:
            raise DiscussionTransactionError("final_attempt_ordinal must be 1 or 2")
        _require_int("generation_audit_sequence", self.generation_audit_sequence, minimum=1)
        _require_hash("generation_record_sha256", self.generation_record_sha256)
        _require_hash("context_sha256", self.context_sha256)
        _require_hash("before_state_sha256", self.before_state_sha256)
        _require_int("base_revision", self.base_revision)
        if not isinstance(self.status, DiscussionTerminalStatus):
            raise DiscussionTransactionError("terminal status is not closed")
        if not isinstance(self.reason, DiscussionTerminalReason):
            raise DiscussionTransactionError("terminal reason is not closed")

        allowed_reasons = {
            DiscussionTerminalStatus.ABORTED: {
                DiscussionTerminalReason.PROMPT_REJECTED,
                DiscussionTerminalReason.BACKEND_FAILED,
                DiscussionTerminalReason.FINAL_OUTPUT_INVALID,
                DiscussionTerminalReason.CANCELLED_BEFORE_RESULT,
                DiscussionTerminalReason.STAGE_FAILED,
                DiscussionTerminalReason.CANCELLED_AFTER_STAGE,
                DiscussionTerminalReason.STALE,
                DiscussionTerminalReason.DEADLINE,
                DiscussionTerminalReason.REVISION_CONFLICT,
            },
            DiscussionTerminalStatus.NO_ACTION: {
                DiscussionTerminalReason.EXPLICIT_NO_DECISION
            },
            DiscussionTerminalStatus.NOT_DELIVERED: {
                DiscussionTerminalReason.SEND_NOT_DELIVERED
            },
            DiscussionTerminalStatus.DELIVERY_UNKNOWN: {
                DiscussionTerminalReason.SEND_DELIVERY_UNKNOWN
            },
            DiscussionTerminalStatus.ACCEPTED: {
                DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED
            },
            DiscussionTerminalStatus.REJECTED: {
                DiscussionTerminalReason.AUTHORITATIVE_REJECTED
            },
            DiscussionTerminalStatus.RECOVERY_UNKNOWN: {
                DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
                DiscussionTerminalReason.RECOVERY_GAP,
                DiscussionTerminalReason.PHASE_CHANGED,
                DiscussionTerminalReason.OWNER_STOPPED,
            },
        }
        if self.reason not in allowed_reasons[self.status]:
            raise DiscussionTransactionError("terminal status/reason mismatch")

        before_abort_reasons = {
            DiscussionTerminalReason.PROMPT_REJECTED,
            DiscussionTerminalReason.BACKEND_FAILED,
            DiscussionTerminalReason.FINAL_OUTPUT_INVALID,
            DiscussionTerminalReason.CANCELLED_BEFORE_RESULT,
        }
        if self.status is DiscussionTerminalStatus.ABORTED:
            if self.after_state_sha256 is not None or self.committed_revision is not None:
                raise DiscussionTransactionError("aborted terminal cannot claim a commit")
            if self.reason in before_abort_reasons:
                if any(
                    value is not None
                    for value in (
                        self.proposal_sha256,
                        self.decision_kind,
                        self.option_id,
                    )
                ):
                    raise DiscussionTransactionError(
                        "pre-result abort cannot carry proposal identity"
                    )
            else:
                _require_hash("proposal_sha256", self.proposal_sha256)
                self._validate_decision_identity()
        else:
            _require_hash("after_state_sha256", self.after_state_sha256)
            _require_int("committed_revision", self.committed_revision, minimum=1)
            if self.committed_revision != self.base_revision + 1:
                raise DiscussionTransactionError(
                    "committed_revision must equal base_revision + 1"
                )
            _require_hash("proposal_sha256", self.proposal_sha256)
            self._validate_decision_identity()

        if self.status is DiscussionTerminalStatus.NO_ACTION:
            if self.decision_kind != "none" or self.option_id is not None:
                raise DiscussionTransactionError(
                    "NO_ACTION terminal requires the none decision identity"
                )
        elif self.status is not DiscussionTerminalStatus.ABORTED:
            if self.decision_kind == "none" or self.option_id is None:
                raise DiscussionTransactionError(
                    "action terminal requires a non-none decision identity"
                )

        receipt_present = self.request_event_id is not None
        if receipt_present != (self.send_connection_generation is not None):
            raise DiscussionTransactionError("terminal receipt fields are indivisible")
        if receipt_present:
            _require_id("request_event_id", self.request_event_id)
            _require_int("send_connection_generation", self.send_connection_generation)
        needs_receipt = self.status in {
            DiscussionTerminalStatus.ACCEPTED,
            DiscussionTerminalStatus.REJECTED,
            DiscussionTerminalStatus.RECOVERY_UNKNOWN,
        }
        if receipt_present is not needs_receipt:
            raise DiscussionTransactionError("terminal receipt nullability mismatch")
        if self.status in {
            DiscussionTerminalStatus.ACCEPTED,
            DiscussionTerminalStatus.REJECTED,
        }:
            if not isinstance(self.authoritative_evidence, EvidenceRef):
                raise DiscussionTransactionError(
                    "accepted/rejected terminal requires evidence"
                )
        elif self.authoritative_evidence is not None:
            if self.status is not DiscussionTerminalStatus.RECOVERY_UNKNOWN or not isinstance(
                self.authoritative_evidence, EvidenceRef
            ):
                raise DiscussionTransactionError("terminal evidence nullability mismatch")
        if len(canonical_json_bytes(self)) > _MAX_TERMINAL_BYTES:
            raise DiscussionTransactionError("terminal record exceeds 16 KiB")

    def _validate_decision_identity(self) -> None:
        if self.decision_kind not in {"none", "chat", "vote", "ability", "co_declare"}:
            raise DiscussionTransactionError("terminal decision_kind is not closed")
        if self.decision_kind == "none":
            if self.option_id is not None:
                raise DiscussionTransactionError("none terminal requires null option_id")
        else:
            _require_id("option_id", self.option_id)


class DiscussionTransaction:
    """Serially bind one state port to the already-shared private audit writer."""

    def __init__(
        self,
        *,
        state: DiscussionStatePort,
        audit: DiscussionTerminalAuditSink,
        utc_clock: Callable[[], datetime] = _utc_now,
        max_terminal_reservations: int = _MAX_TERMINAL_RESERVATIONS,
    ) -> None:
        if not all(hasattr(state, name) for name in (
            "capture", "stage", "commit", "abort", "finish_no_action",
            "mark_dispatch_started", "finish_dispatch", "observe_authoritative",
        )):
            raise TypeError("state must implement DiscussionStatePort")
        if not hasattr(audit, "write") or not callable(audit.write):
            raise TypeError("audit must provide async write(record)")
        if not callable(utc_clock):
            raise TypeError("utc_clock must be callable")
        if (
            type(max_terminal_reservations) is not int
            or not 1 <= max_terminal_reservations <= _MAX_TERMINAL_RESERVATIONS
        ):
            raise ValueError(_TERMINAL_RESERVATION_CONFIG_ERROR)
        self.state = state
        self.audit = audit
        self._utc_clock = utc_clock
        self._max_terminal_reservations = max_terminal_reservations
        self._terminal_reservations: set[bytes] = set()
        self._closed = False

    def utc_string(self) -> str:
        value = self._utc_clock()
        if not isinstance(value, datetime):
            raise DiscussionTransactionError("utc_clock must return datetime")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")

    async def write_terminal(self, record: AiDiscussionTerminalRecord) -> object:
        if not isinstance(record, AiDiscussionTerminalRecord):
            raise TypeError("record must be AiDiscussionTerminalRecord")
        if self._closed:
            raise DiscussionTransactionError("discussion transaction is closed")
        capture_key = bytes.fromhex(record.capture_id)
        if len(capture_key) != 32:
            # The record constructor already owns the public digest validation;
            # keep this internal representation boundary fail-closed as well.
            raise DiscussionTransactionError("terminal capture_id must decode to 32 bytes")
        if capture_key in self._terminal_reservations:
            raise DiscussionTransactionError("duplicate terminal submission")
        if len(self._terminal_reservations) >= self._max_terminal_reservations:
            raise DiscussionTransactionError("terminal reservation capacity exhausted")
        # Reserve synchronously before task construction or the first await.  The
        # key is never removed while open because the row may already be durable
        # even when its acknowledgement or caller later fails.
        self._terminal_reservations.add(capture_key)
        write_task = asyncio.ensure_future(self.audit.write(record))
        cancellation: asyncio.CancelledError | None = None
        try:
            while True:
                try:
                    acknowledgement = await asyncio.shield(write_task)
                    break
                except asyncio.CancelledError as error:
                    # Complete and validate this sole sink submission before
                    # propagating cancellation.  ``shield`` prevents the audit
                    # task itself from being cancelled.
                    if write_task.cancelled():
                        raise DiscussionTransactionError(
                            "terminal audit write was cancelled"
                        ) from error
                    cancellation = error
                    if write_task.done():
                        acknowledgement = write_task.result()
                        break
        except Exception as error:
            raise DiscussionTransactionError("terminal audit write failed") from error
        if (
            type(getattr(acknowledgement, "sequence", None)) is not int
            or acknowledgement.sequence < 1
            or getattr(acknowledgement, "durable", None) is not True
        ):
            raise DiscussionTransactionError("terminal audit acknowledgement is invalid")
        if cancellation is not None:
            raise cancellation
        return acknowledgement

    def close(self) -> None:
        """Irreversibly release the bounded ledger after its owner has stopped."""

        self._closed = True
        self._terminal_reservations.clear()

    @staticmethod
    def _validate_capture_generation(
        capture: DiscussionCapture,
        generation: DiscussionGenerationAck,
    ) -> None:
        if not isinstance(capture, DiscussionCapture):
            raise TypeError("capture must be DiscussionCapture")
        if not isinstance(generation, DiscussionGenerationAck):
            raise TypeError("generation must be DiscussionGenerationAck")
        expected = {
            "capture_id": capture.capture_id,
            "request_id": f"phase6:{capture.capture_id}",
            "context_sha256": capture.context_sha256,
            "before_state_sha256": capture.state_sha256,
            "after_state_sha256": None,
        }
        for name, value in expected.items():
            _require_equal(f"generation.{name}", getattr(generation, name), value)

    @staticmethod
    def _validate_proposal(
        capture: DiscussionCapture,
        generation: DiscussionGenerationAck,
        proposal: DiscussionProposal,
    ) -> str:
        if not isinstance(proposal, DiscussionProposal):
            raise TypeError("proposal must be DiscussionProposal")
        _require_equal("proposal.base_revision", proposal.base_revision, capture.base_revision)
        proposal_sha256 = canonical_sha256(proposal)
        _require_equal(
            "generation.proposal_sha256",
            generation.proposal_sha256,
            proposal_sha256,
        )
        if generation.generation_status not in _SUCCESSFUL_GENERATION_STATUSES:
            raise DiscussionTransactionError(
                "proposal terminal requires a successful generation acknowledgement"
            )
        if (
            generation.generation_status is AiDiscussionGenerationStatus.DECISION
            and proposal.decision_kind == "none"
        ):
            raise DiscussionTransactionError("DECISION generation requires an action proposal")
        if (
            generation.generation_status
            is AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION
            and proposal.decision_kind != "none"
        ):
            raise DiscussionTransactionError(
                "EXPLICIT_NO_DECISION generation requires a none proposal"
            )
        return proposal_sha256

    @staticmethod
    def _validate_abort_ack(
        capture: DiscussionCapture,
        generation: DiscussionGenerationAck,
        abort: AbortAck,
        proposal: DiscussionProposal | None,
    ) -> None:
        if not isinstance(abort, AbortAck):
            raise TypeError("abort must be AbortAck")
        expected = {
            "capture_id": capture.capture_id,
            "request_id": generation.request_id,
            "base_revision": capture.base_revision,
            "context_sha256": capture.context_sha256,
            "before_state_sha256": capture.state_sha256,
            "after_state_sha256": None,
        }
        for name, value in expected.items():
            _require_equal(f"abort.{name}", getattr(abort, name), value)
        if abort.reason is DiscussionAbortReason.AUDIT_FAILED:
            raise DiscussionTransactionError(
                "AUDIT_FAILED cannot manufacture a durable terminal record"
            )
        terminal_reason = DiscussionTerminalReason(abort.reason.value)
        expected_generation_statuses = _PRE_RESULT_GENERATION_STATUSES.get(
            terminal_reason
        )
        if expected_generation_statuses is not None:
            if proposal is not None or abort.proposal_sha256 is not None:
                raise DiscussionTransactionError(
                    "pre-result abort cannot carry a proposal"
                )
            if generation.proposal_sha256 is not None:
                raise DiscussionTransactionError(
                    "pre-result generation cannot carry a proposal hash"
                )
            if generation.generation_status not in expected_generation_statuses:
                raise DiscussionTransactionError(
                    "abort reason does not match generation status"
                )
            return
        if proposal is None:
            raise DiscussionTransactionError("post-result abort requires proposal")
        proposal_sha256 = DiscussionTransaction._validate_proposal(
            capture, generation, proposal
        )
        _require_equal("abort.proposal_sha256", abort.proposal_sha256, proposal_sha256)

    def terminal_from_abort(
        self,
        *,
        capture: DiscussionCapture,
        generation: DiscussionGenerationAck,
        abort: AbortAck,
        proposal: DiscussionProposal | None,
    ) -> AiDiscussionTerminalRecord:
        self._validate_capture_generation(capture, generation)
        self._validate_abort_ack(capture, generation, abort, proposal)
        return self._terminal(
            capture=capture,
            generation=generation,
            after_state_sha256=None,
            proposal_sha256=abort.proposal_sha256,
            committed_revision=None,
            decision_kind=None if proposal is None else proposal.decision_kind,
            option_id=None if proposal is None else proposal.option_id,
            status=DiscussionTerminalStatus.ABORTED,
            reason=DiscussionTerminalReason(abort.reason.value),
        )

    def terminal_from_delivery(
        self,
        *,
        capture: DiscussionCapture,
        generation: DiscussionGenerationAck,
        proposal: DiscussionProposal,
        delivery: DeliveryAck,
    ) -> AiDiscussionTerminalRecord:
        self._validate_capture_generation(capture, generation)
        proposal_sha256 = self._validate_proposal(capture, generation, proposal)
        if not isinstance(delivery, DeliveryAck):
            raise TypeError("delivery must be DeliveryAck")
        expected = {
            "capture_id": capture.capture_id,
            "request_id": generation.request_id,
            "base_revision": capture.base_revision,
            "context_sha256": capture.context_sha256,
            "before_state_sha256": capture.state_sha256,
            "proposal_sha256": proposal_sha256,
        }
        for name, value in expected.items():
            _require_equal(f"delivery.{name}", getattr(delivery, name), value)
        mapping = {
            DiscussionDeliveryStatus.NO_ACTION: (
                DiscussionTerminalStatus.NO_ACTION,
                DiscussionTerminalReason.EXPLICIT_NO_DECISION,
            ),
            DiscussionDeliveryStatus.NOT_DELIVERED: (
                DiscussionTerminalStatus.NOT_DELIVERED,
                DiscussionTerminalReason.SEND_NOT_DELIVERED,
            ),
            DiscussionDeliveryStatus.DELIVERY_UNKNOWN: (
                DiscussionTerminalStatus.DELIVERY_UNKNOWN,
                DiscussionTerminalReason.SEND_DELIVERY_UNKNOWN,
            ),
        }
        if delivery.status not in mapping:
            raise DiscussionTransactionError("LOCAL_SENT is not terminal before observation")
        if delivery.status is DiscussionDeliveryStatus.NO_ACTION:
            if proposal.decision_kind != "none":
                raise DiscussionTransactionError(
                    "NO_ACTION delivery requires a none proposal"
                )
        elif (
            proposal.decision_kind == "none"
            or delivery.action != proposal.decision_kind
            or delivery.option_id != proposal.option_id
        ):
            raise DiscussionTransactionError(
                "delivery identity does not match proposal"
            )
        status, reason = mapping[delivery.status]
        return self._terminal(
            capture=capture,
            generation=generation,
            after_state_sha256=delivery.after_state_sha256,
            proposal_sha256=delivery.proposal_sha256,
            committed_revision=delivery.committed_revision,
            decision_kind=proposal.decision_kind,
            option_id=proposal.option_id,
            status=status,
            reason=reason,
        )

    def terminal_from_observation(
        self,
        *,
        capture: DiscussionCapture,
        generation: DiscussionGenerationAck,
        proposal: DiscussionProposal,
        observation: ObservationAck,
        reason: DiscussionTerminalReason,
    ) -> AiDiscussionTerminalRecord:
        self._validate_capture_generation(capture, generation)
        proposal_sha256 = self._validate_proposal(capture, generation, proposal)
        if not isinstance(observation, ObservationAck):
            raise TypeError("observation must be ObservationAck")
        expected = {
            "capture_id": capture.capture_id,
            "request_id": generation.request_id,
            "base_revision": capture.base_revision,
            "context_sha256": capture.context_sha256,
            "before_state_sha256": capture.state_sha256,
            "proposal_sha256": proposal_sha256,
            "generation_audit_sequence": generation.audit_sequence,
            "action": proposal.decision_kind,
            "option_id": proposal.option_id,
        }
        for name, value in expected.items():
            _require_equal(f"observation.{name}", getattr(observation, name), value)
        status = DiscussionTerminalStatus(observation.status.value)
        return self._terminal(
            capture=capture,
            generation=generation,
            after_state_sha256=observation.after_state_sha256,
            proposal_sha256=observation.proposal_sha256,
            committed_revision=observation.committed_revision,
            decision_kind=proposal.decision_kind,
            option_id=proposal.option_id,
            status=status,
            reason=reason,
            request_event_id=observation.request_event_id,
            send_connection_generation=observation.send_connection_generation,
            authoritative_evidence=observation.authoritative_evidence,
        )

    def _terminal(
        self,
        *,
        capture: DiscussionCapture,
        generation: DiscussionGenerationAck,
        after_state_sha256: str | None,
        proposal_sha256: str | None,
        committed_revision: int | None,
        decision_kind: str | None,
        option_id: str | None,
        status: DiscussionTerminalStatus,
        reason: DiscussionTerminalReason,
        request_event_id: str | None = None,
        send_connection_generation: int | None = None,
        authoritative_evidence: EvidenceRef | None = None,
    ) -> AiDiscussionTerminalRecord:
        self._validate_capture_generation(capture, generation)
        _require_equal(
            "generation.proposal_sha256",
            generation.proposal_sha256,
            proposal_sha256,
        )
        return AiDiscussionTerminalRecord(
            schema_version="aiwolf.ai-discussion-terminal.v1",
            recorded_at_utc=self.utc_string(),
            game_id=capture.game_id,
            player_id=capture.player_id,
            request_id=generation.request_id,
            capture_id=capture.capture_id,
            phase=capture.trigger.phase,
            day=capture.trigger.day,
            final_attempt_ordinal=generation.final_attempt_ordinal,
            generation_audit_sequence=generation.audit_sequence,
            generation_record_sha256=generation.generation_record_sha256,
            context_sha256=capture.context_sha256,
            before_state_sha256=capture.state_sha256,
            after_state_sha256=after_state_sha256,
            proposal_sha256=proposal_sha256,
            base_revision=capture.base_revision,
            committed_revision=committed_revision,
            decision_kind=decision_kind,  # type: ignore[arg-type]
            option_id=option_id,
            status=status,
            reason=reason,
            request_event_id=request_event_id,
            send_connection_generation=send_connection_generation,
            authoritative_evidence=authoritative_evidence,
        )
