"""Audited, fail-closed LLM implementation of the existing Brain boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import time
from typing import Protocol
from uuid import uuid4

from ai_client.brain import (
    AbilityDecision,
    BrainDecision,
    BrainInput,
    BrainOutput,
    BrainResult,
    ChatDecision,
    CoDeclareDecision,
    CoReportDecision,
    NoDecision,
    VoteDecision,
)
from ai_client.discussion.context import canonical_sha256
from ai_client.discussion.model import (
    AiDiscussionGenerationStatus,
    DiscussionAbortReason,
    DiscussionGenerationAck,
    DiscussionProposal,
)
from ai_client.discussion.projection import DiscussionPromptConfig
from ai_client.network import AbilityAction

from .backend import StructuredLLMBackend
from .decision import (
    DecisionValidationError,
    parse_llm_decision,
    parse_llm_output,
)
from .prompt import (
    PromptProjectionError,
    build_repair_projection,
    canonical_prompt_json,
    project_brain_input,
)
from .types import (
    AiAuditDecision,
    AiAuditEntry,
    AiAuditError,
    AiAuditRecord,
    AiAuditStatus,
    AiDiscussionGenerationRecord,
    AiDiscussionGenerationRecordV2,
    AuditWriteAck,
    DecisionValidationCode,
    LLMBackendError,
    LLMBackendErrorCode,
    LLMBrainConfig,
    LLMBrainSnapshot,
    LLMClientIdentity,
    LLMInvocationStatus,
    PromptProjection,
    PromptRejectionCode,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    discussion_generation_record_sha256,
)


class AiAuditSink(Protocol):
    async def start(self) -> None: ...
    async def write(self, record: AiAuditEntry) -> AuditWriteAck: ...
    async def aclose(self) -> None: ...


class LLMInvocationError(RuntimeError):
    """A sanitized no-send result from one LLM invocation."""

    def __init__(
        self,
        status: LLMInvocationStatus,
        code: str,
        *,
        discussion_ack: DiscussionGenerationAck | None = None,
        discussion_abort_reason: DiscussionAbortReason | None = None,
    ) -> None:
        if not isinstance(status, LLMInvocationStatus):
            raise TypeError("status must be LLMInvocationStatus")
        if not isinstance(code, str) or not code:
            raise ValueError("code must be a non-empty string")
        self.status = status
        self.code = code
        if discussion_ack is not None and not isinstance(
            discussion_ack, DiscussionGenerationAck
        ):
            raise TypeError("discussion_ack must be DiscussionGenerationAck or None")
        if discussion_abort_reason is not None and not isinstance(
            discussion_abort_reason, DiscussionAbortReason
        ):
            raise TypeError(
                "discussion_abort_reason must be DiscussionAbortReason or None"
            )
        if (discussion_ack is None) is not (discussion_abort_reason is None):
            raise ValueError("discussion acknowledgement and abort reason must be paired")
        self.discussion_ack = discussion_ack
        self.discussion_abort_reason = discussion_abort_reason
        RuntimeError.__init__(self, code)


def uuid4_string() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _AuditFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


class _InvalidOutput(Exception):
    def __init__(
        self,
        code: DecisionValidationCode,
        response: StructuredGenerationResponse,
        acknowledgement: DiscussionGenerationAck | None,
    ) -> None:
        self.code = code
        self.response = response
        self.acknowledgement = acknowledgement


class _CancelledBeforeRepair(asyncio.CancelledError):
    """The first invalid-output row is durable, but repair is now cancelled."""


class LLMBrain:
    """One-at-a-time, zero-fallback Brain backed by structured generation."""

    def __init__(
        self,
        *,
        backend: StructuredLLMBackend,
        audit: AiAuditSink,
        identity: LLMClientIdentity,
        config: LLMBrainConfig = LLMBrainConfig(),
        discussion_config: DiscussionPromptConfig = DiscussionPromptConfig(),
        request_id_factory: Callable[[], str] = uuid4_string,
        clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not hasattr(backend, "generate") or not callable(backend.generate):
            raise TypeError("backend must provide generate")
        if not hasattr(backend, "identity"):
            raise TypeError("backend must provide identity")
        if not hasattr(audit, "write") or not callable(audit.write):
            raise TypeError("audit must provide write")
        if not isinstance(identity, LLMClientIdentity):
            raise TypeError("identity must be LLMClientIdentity")
        if not isinstance(config, LLMBrainConfig):
            raise TypeError("config must be LLMBrainConfig")
        if not isinstance(discussion_config, DiscussionPromptConfig):
            raise TypeError("discussion_config must be DiscussionPromptConfig")
        for name, value in (
            ("request_id_factory", request_id_factory),
            ("clock", clock),
            ("utc_clock", utc_clock),
        ):
            if not callable(value):
                raise TypeError(f"{name} must be callable")
        self._backend = backend
        self._audit = audit
        self._identity = identity
        self._config = config
        self._discussion_config = discussion_config
        self._request_id_factory = request_id_factory
        self._clock = clock
        self._utc_clock = utc_clock
        self._active = False
        self._calls = 0
        self._backend_calls = 0
        self._decisions = 0
        self._explicit_no_decisions = 0
        self._repair_attempts = 0
        self._failures = 0
        self._cancellations = 0
        self._audit_failures = 0
        self._last_status: LLMInvocationStatus | None = None
        self._last_error_code: str | None = None

    def snapshot(self) -> LLMBrainSnapshot:
        return LLMBrainSnapshot(
            active=self._active,
            calls=self._calls,
            backend_calls=self._backend_calls,
            decisions=self._decisions,
            explicit_no_decisions=self._explicit_no_decisions,
            repair_attempts=self._repair_attempts,
            failures=self._failures,
            cancellations=self._cancellations,
            audit_failures=self._audit_failures,
            last_status=self._last_status,
            last_error_code=self._last_error_code,
        )

    async def decide(self, request: BrainInput) -> BrainOutput:
        if not isinstance(request, BrainInput):
            raise TypeError("request must be BrainInput")
        if self._active:
            raise RuntimeError("LLMBrain supports only one active invocation")
        contextful = request.discussion is not None
        if contextful:
            assert request.discussion is not None
            if (
                request.discussion.game_id != self._identity.game_id
                or request.discussion.player_id != self._identity.player_id
            ):
                raise ValueError(
                    "discussion capture does not match the LLM client identity"
                )
            request_id = f"phase6:{request.discussion.capture_id}"
        else:
            request_id = self._request_id_factory()
            if not isinstance(request_id, str) or not request_id:
                raise ValueError("request_id_factory must return a non-empty string")

        self._active = True
        self._calls += 1
        active_projection: PromptProjection | None = None
        active_attempt = 1
        try:
            try:
                projection = project_brain_input(
                    request,
                    config=self._config,
                    discussion_config=self._discussion_config,
                )
                active_projection = projection
            except PromptProjectionError as error:
                active_projection = error.projection
                try:
                    acknowledgement = await self._write_prompt_rejection(
                        request, request_id, error.projection, attempt_ordinal=1,
                        rejection_code=error.code,
                    )
                except _AuditFailure as audit_error:
                    self._fail_audit(audit_error.code)
                    raise LLMInvocationError(
                        LLMInvocationStatus.AUDIT_FAILED, audit_error.code
                    ) from None
                self._fail(LLMInvocationStatus.PROMPT_REJECTED, error.code)
                raise LLMInvocationError(
                    LLMInvocationStatus.PROMPT_REJECTED,
                    error.code,
                    discussion_ack=acknowledgement,
                    discussion_abort_reason=(
                        DiscussionAbortReason.PROMPT_REJECTED
                        if acknowledgement is not None
                        else None
                    ),
                ) from None

            try:
                return await self._attempt(
                    request,
                    request_id,
                    projection,
                    attempt_ordinal=1,
                    repair=False,
                )
            except _InvalidOutput as invalid:
                if self._config.max_schema_repair_attempts == 0:
                    self._fail(LLMInvocationStatus.OUTPUT_INVALID, invalid.code.value)
                    raise LLMInvocationError(
                        LLMInvocationStatus.OUTPUT_INVALID,
                        invalid.code.value,
                        discussion_ack=invalid.acknowledgement,
                        discussion_abort_reason=(
                            DiscussionAbortReason.FINAL_OUTPUT_INVALID
                            if invalid.acknowledgement is not None
                            else None
                        ),
                    ) from None
                self._repair_attempts += 1
                active_attempt = 2
                try:
                    repaired = build_repair_projection(
                        projection,
                        validation_code=invalid.code,
                        invalid_output=invalid.response.text,
                        config=self._config,
                        discussion_config=self._discussion_config,
                    )
                    active_projection = repaired
                except PromptProjectionError as error:
                    active_projection = error.projection
                    try:
                        acknowledgement = await self._write_prompt_rejection(
                            request, request_id, error.projection, attempt_ordinal=2,
                            rejection_code=error.code,
                        )
                    except _AuditFailure as audit_error:
                        self._fail_audit(audit_error.code)
                        raise LLMInvocationError(
                            LLMInvocationStatus.AUDIT_FAILED, audit_error.code
                        ) from None
                    self._fail(LLMInvocationStatus.PROMPT_REJECTED, error.code)
                    raise LLMInvocationError(
                        LLMInvocationStatus.PROMPT_REJECTED,
                        error.code,
                        discussion_ack=acknowledgement,
                        discussion_abort_reason=(
                            DiscussionAbortReason.PROMPT_REJECTED
                            if acknowledgement is not None
                            else None
                        ),
                    ) from None
                try:
                    return await self._attempt(
                        request,
                        request_id,
                        repaired,
                        attempt_ordinal=2,
                        repair=True,
                    )
                except _InvalidOutput as final_invalid:
                    self._fail(
                        LLMInvocationStatus.REPAIR_FAILED,
                        final_invalid.code.value,
                    )
                    raise LLMInvocationError(
                        LLMInvocationStatus.REPAIR_FAILED,
                        final_invalid.code.value,
                        discussion_ack=final_invalid.acknowledgement,
                        discussion_abort_reason=(
                            DiscussionAbortReason.FINAL_OUTPUT_INVALID
                            if final_invalid.acknowledgement is not None
                            else None
                        ),
                    ) from None
        except _AuditFailure as error:
            self._fail_audit(error.code)
            raise LLMInvocationError(
                LLMInvocationStatus.AUDIT_FAILED, error.code
            ) from None
        except asyncio.CancelledError as cancellation:
            self._cancellations += 1
            self._last_status = LLMInvocationStatus.CANCELLED
            self._last_error_code = LLMInvocationStatus.CANCELLED.value
            if not contextful:
                raise
            if isinstance(cancellation, _CancelledBeforeRepair):
                # Attempt one already has its durable OUTPUT_INVALID row.  The
                # final cancellation must therefore use the distinct second
                # ordinal without constructing a repair prompt or calling the
                # backend again.
                active_attempt = 2
            if active_projection is None:
                raise LLMInvocationError(
                    LLMInvocationStatus.AUDIT_FAILED, "AUDIT_FAILED"
                ) from None
            try:
                cancelled_write = asyncio.create_task(
                    self._write_record(
                        request=request,
                        request_id=request_id,
                        projection=active_projection,
                        attempt_ordinal=active_attempt,
                        latency_microseconds=0,
                        status=AiDiscussionGenerationStatus.CANCELLED,
                    )
                )
                acknowledgement = await self._await_owned_generation_write(
                    cancelled_write
                )
            except _AuditFailure as audit_error:
                self._fail_audit(audit_error.code)
                raise LLMInvocationError(
                    LLMInvocationStatus.AUDIT_FAILED, audit_error.code
                ) from None
            if acknowledgement is None:
                self._fail_audit("AUDIT_ACK_INVALID")
                raise LLMInvocationError(
                    LLMInvocationStatus.AUDIT_FAILED, "AUDIT_ACK_INVALID"
                ) from None
            raise LLMInvocationError(
                LLMInvocationStatus.CANCELLED,
                LLMInvocationStatus.CANCELLED.value,
                discussion_ack=acknowledgement,
                discussion_abort_reason=DiscussionAbortReason.CANCELLED_BEFORE_RESULT,
            ) from None
        finally:
            self._active = False

    async def _attempt(
        self,
        request: BrainInput,
        request_id: str,
        projection: PromptProjection,
        *,
        attempt_ordinal: int,
        repair: bool,
    ) -> BrainOutput:
        transport_id = (
            f"{request_id}:attempt:{attempt_ordinal}"
            if request.discussion is not None else request_id
        )
        generated_request = StructuredGenerationRequest(
            request_id=transport_id,
            messages=projection.messages,
            output_schema=projection.decision_schema,
        )
        started = self._clock()
        self._backend_calls += 1
        try:
            response = await self._backend.generate(generated_request)
            if response.request_id != transport_id:
                raise LLMBackendError(
                    LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID
                )
        except asyncio.CancelledError:
            raise
        except LLMBackendError as error:
            elapsed = self._elapsed_microseconds(started)
            acknowledgement = await self._write_record(
                request=request,
                request_id=request_id,
                projection=projection,
                attempt_ordinal=attempt_ordinal,
                latency_microseconds=elapsed,
                status=AiAuditStatus.BACKEND_FAILED,
                backend_error_code=error.code,
            )
            self._fail(LLMInvocationStatus.BACKEND_FAILED, error.code.value)
            raise LLMInvocationError(
                LLMInvocationStatus.BACKEND_FAILED,
                error.code.value,
                discussion_ack=acknowledgement,
                discussion_abort_reason=(
                    DiscussionAbortReason.BACKEND_FAILED
                    if acknowledgement is not None
                    else None
                ),
            ) from None
        except Exception:
            elapsed = self._elapsed_microseconds(started)
            code = LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID
            acknowledgement = await self._write_record(
                request=request,
                request_id=request_id,
                projection=projection,
                attempt_ordinal=attempt_ordinal,
                latency_microseconds=elapsed,
                status=AiAuditStatus.BACKEND_FAILED,
                backend_error_code=code,
            )
            self._fail(LLMInvocationStatus.BACKEND_FAILED, code.value)
            raise LLMInvocationError(
                LLMInvocationStatus.BACKEND_FAILED,
                code.value,
                discussion_ack=acknowledgement,
                discussion_abort_reason=(
                    DiscussionAbortReason.BACKEND_FAILED
                    if acknowledgement is not None
                    else None
                ),
            ) from None

        elapsed = self._elapsed_microseconds(started)
        proposal: DiscussionProposal | None = None
        try:
            if (
                projection.short_chat is not None
                and response.finish_reason == "length"
            ):
                raise DecisionValidationError(DecisionValidationCode.SCHEMA)
            if projection.discussion_capture is None:
                decision = parse_llm_decision(response.text, projection=projection)
            else:
                parsed = parse_llm_output(response.text, projection=projection)
                decision = parsed.decision
                proposal = parsed.proposal
        except DecisionValidationError as error:
            acknowledgement = await self._write_record(
                request=request,
                request_id=request_id,
                projection=projection,
                attempt_ordinal=attempt_ordinal,
                latency_microseconds=elapsed,
                status=(
                    AiAuditStatus.REPAIR_FAILED
                    if repair
                    else AiAuditStatus.OUTPUT_INVALID
                ),
                response=response,
                validation_code=error.code,
            )
            raise _InvalidOutput(error.code, response, acknowledgement) from None

        if repair:
            audit_status = AiAuditStatus.REPAIR_SUCCEEDED
            invocation_status = LLMInvocationStatus.REPAIR_SUCCEEDED
        elif isinstance(decision, NoDecision):
            audit_status = AiAuditStatus.EXPLICIT_NO_DECISION
            invocation_status = LLMInvocationStatus.EXPLICIT_NO_DECISION
        else:
            audit_status = AiAuditStatus.DECISION
            invocation_status = LLMInvocationStatus.DECISION
        acknowledgement = await self._write_record(
            request=request,
            request_id=request_id,
            projection=projection,
            attempt_ordinal=attempt_ordinal,
            latency_microseconds=elapsed,
            status=audit_status,
            response=response,
            decision=self._audit_decision(request, decision),
            proposal=proposal,
        )
        if isinstance(decision, NoDecision):
            self._explicit_no_decisions += 1
        else:
            self._decisions += 1
        self._last_status = invocation_status
        self._last_error_code = None
        if request.discussion is None:
            return decision
        if acknowledgement is None or proposal is None:
            raise _AuditFailure("AUDIT_ACK_INVALID")
        return BrainResult(
            decision=decision,
            discussion=proposal,
            audit_ack=acknowledgement,
        )

    async def _write_prompt_rejection(
        self,
        request: BrainInput,
        request_id: str,
        projection: PromptProjection,
        *,
        attempt_ordinal: int,
        rejection_code: str,
    ) -> DiscussionGenerationAck | None:
        try:
            code = PromptRejectionCode(rejection_code)
        except (TypeError, ValueError):
            raise _AuditFailure("AUDIT_RECORD_INVALID") from None
        return await self._write_record(
            request=request,
            request_id=request_id,
            projection=projection,
            attempt_ordinal=attempt_ordinal,
            latency_microseconds=0,
            status=AiAuditStatus.PROMPT_REJECTED,
            prompt_rejection_code=code,
        )

    async def _write_record(
        self,
        *,
        request: BrainInput,
        request_id: str,
        projection: PromptProjection,
        attempt_ordinal: int,
        latency_microseconds: int,
        status: AiAuditStatus | AiDiscussionGenerationStatus,
        response: StructuredGenerationResponse | None = None,
        backend_error_code: LLMBackendErrorCode | None = None,
        validation_code: DecisionValidationCode | None = None,
        decision: AiAuditDecision | None = None,
        proposal: DiscussionProposal | None = None,
        prompt_rejection_code: PromptRejectionCode | None = None,
    ) -> DiscussionGenerationAck | None:
        response_text = None if response is None else response.text
        response_encoded = None if response_text is None else response_text.encode("utf-8")
        prompt_json = canonical_prompt_json(
            projection.messages, projection.decision_schema
        )
        common = {
            "recorded_at_utc": self._utc_string(),
            "game_id": self._identity.game_id,
            "player_id": self._identity.player_id,
            "request_id": request_id,
            "world_version": request.snapshot.version,
            "backend": self._backend.identity,
            "attempt_ordinal": attempt_ordinal,
            "prompt_sha256": projection.prompt_sha256,
            "prompt_bytes": projection.prompt_bytes,
            "prompt_json": prompt_json,
            "response_sha256": None
            if response_encoded is None
            else hashlib.sha256(response_encoded).hexdigest(),
            "response_bytes": None if response_encoded is None else len(response_encoded),
            "response_text": response_text,
            "latency_microseconds": latency_microseconds,
            "provider_model": None if response is None else response.provider_model,
            "finish_reason": None if response is None else response.finish_reason,
            "prompt_tokens": None if response is None else response.usage.prompt_tokens,
            "completion_tokens": None
            if response is None
            else response.usage.completion_tokens,
            "backend_error_code": backend_error_code,
            "validation_code": validation_code,
            "decision": decision,
        }
        capture = request.discussion
        if capture is None:
            if not isinstance(status, AiAuditStatus):
                raise _AuditFailure("AUDIT_RECORD_INVALID")
            record: AiAuditEntry = AiAuditRecord(
                schema_version="aiwolf.ai-log.v1",
                phase=(
                    None
                    if request.snapshot.phase is None
                    else request.snapshot.phase.phase
                ),
                day=(
                    None if request.snapshot.phase is None else request.snapshot.phase.day
                ),
                status=status,
                **common,
            )
        else:
            generation_status = (
                status
                if isinstance(status, AiDiscussionGenerationStatus)
                else AiDiscussionGenerationStatus(status.value)
            )
            proposal_sha256 = (
                None if proposal is None else canonical_sha256(proposal)
            )
            record = AiDiscussionGenerationRecordV2(
                schema_version="aiwolf.ai-discussion-generation.v2",
                prompt_rejection_code=prompt_rejection_code,
                capture_id=capture.capture_id,
                phase=capture.trigger.phase,
                day=capture.trigger.day,
                status=generation_status,
                context_sha256=capture.context_sha256,
                before_state_sha256=capture.state_sha256,
                after_state_sha256=None,
                base_revision=capture.base_revision,
                proposal=proposal,
                proposal_sha256=proposal_sha256,
                **common,
            )
        try:
            if capture is None:
                acknowledgement = await self._audit.write(record)
            else:
                owned_write = asyncio.create_task(self._audit.write(record))
                acknowledgement, cancellation_observed = (
                    await self._await_owned_audit_write(owned_write)
                )
        except asyncio.CancelledError:
            if capture is not None:
                # Contextful writes are shield-owned above, so cancellation of
                # the writer task itself is an audit-path failure rather than
                # caller cancellation.  Do not manufacture a second generation
                # row for an append whose sink did not return a durable ack.
                raise _AuditFailure("AUDIT_FAILED") from None
            raise
        except AiAuditError as error:
            raise _AuditFailure(error.code.value) from None
        except Exception:
            raise _AuditFailure("AUDIT_FAILED") from None
        if not isinstance(acknowledgement, AuditWriteAck) or acknowledgement.durable is not True:
            raise _AuditFailure("AUDIT_ACK_INVALID")
        if capture is None:
            return None
        assert isinstance(record, AiDiscussionGenerationRecord)
        generation_ack = DiscussionGenerationAck(
            capture_id=capture.capture_id,
            request_id=request_id,
            final_attempt_ordinal=attempt_ordinal,  # type: ignore[arg-type]
            audit_sequence=acknowledgement.sequence,
            generation_record_sha256=discussion_generation_record_sha256(record),
            generation_status=record.status,
            context_sha256=capture.context_sha256,
            before_state_sha256=capture.state_sha256,
            after_state_sha256=None,
            proposal_sha256=record.proposal_sha256,
            durable=True,
        )
        if (
            cancellation_observed
            and record.status is AiDiscussionGenerationStatus.OUTPUT_INVALID
            and attempt_ordinal == 1
        ):
            raise _CancelledBeforeRepair()
        return generation_ack

    def _audit_decision(
        self, request: BrainInput, decision: BrainDecision
    ) -> AiAuditDecision:
        if isinstance(decision, NoDecision):
            return AiAuditDecision(kind="none")
        option_id = decision.option_id
        option = next(
            item for item in request.action_context.options if item.option_id == option_id
        )
        if isinstance(decision, ChatDecision):
            return AiAuditDecision(kind="chat", option_id=option_id, text=decision.message)
        if isinstance(decision, VoteDecision):
            return AiAuditDecision(
                kind="vote",
                option_id=option_id,
                vote_target_player_id=decision.target_player_id,
            )
        if isinstance(decision, AbilityDecision):
            if not isinstance(option.handle, AbilityAction):
                raise RuntimeError("validated ability option mismatch")
            return AiAuditDecision(
                kind="ability",
                option_id=option_id,
                ability_id=option.handle.ability_id,
                ability_target_player_ids=decision.target_player_ids,
            )
        if isinstance(decision, CoDeclareDecision):
            return AiAuditDecision(
                kind="co_declare",
                option_id=option_id,
                claimed_role_id=decision.claimed_role_id,
                text=decision.comment,
            )
        if isinstance(decision, CoReportDecision):
            return AiAuditDecision(
                kind="co_report",
                option_id=option_id,
                report_kind=decision.kind,
                report_target_player_id=decision.target_player_id,
                claimed_result=decision.claimed_result,
            )
        raise TypeError("unsupported BrainDecision")

    def _elapsed_microseconds(self, started: float) -> int:
        return max(0, int((self._clock() - started) * 1_000_000))

    @staticmethod
    async def _await_owned_generation_write(
        task: asyncio.Task[DiscussionGenerationAck | None],
    ) -> DiscussionGenerationAck | None:
        """Finish the one owned cancellation record despite repeated cancels."""

        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        return task.result()

    @staticmethod
    async def _await_owned_audit_write(
        task: asyncio.Task[AuditWriteAck],
    ) -> tuple[AuditWriteAck, bool]:
        """Resolve the sole contextful append even if its caller is cancelled.

        Once admitted to the FIFO sink the row may already be durable.  The
        owning Brain therefore resolves and validates that exact append; its
        durable branch result wins rather than manufacturing a second record
        with the same attempt ordinal.
        """

        cancellation_observed = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancellation_observed = True
                continue
        return task.result(), cancellation_observed

    def _utc_string(self) -> str:
        value = self._utc_clock()
        if not isinstance(value, datetime):
            raise TypeError("utc_clock must return datetime")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")

    def _fail(self, status: LLMInvocationStatus, code: str) -> None:
        self._failures += 1
        self._last_status = status
        self._last_error_code = code

    def _fail_audit(self, code: str) -> None:
        self._audit_failures += 1
        self._fail(LLMInvocationStatus.AUDIT_FAILED, code)
