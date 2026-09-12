from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import json
import math
import unittest

import httpx
import pytest

from ai_client.brain import BrainActionContext, BrainActionOption, BrainInput
from ai_client.llm.backend import OpenAICompatibleBackend
from ai_client.llm.brain import LLMBrain, LLMInvocationError
from ai_client.llm.decision import DecisionValidationError, parse_llm_decision
from ai_client.llm.prompt import build_repair_projection, project_brain_input
from ai_client.llm.types import (
    AiAuditStatus,
    AuditWriteAck,
    BackendIdentity,
    DecisionValidationCode,
    GenerationSettings,
    LLMBackendError,
    LLMBackendErrorCode,
    LLMBrainConfig,
    LLMClientIdentity,
    LLMInvocationStatus,
    LLMMessage,
    LLMUsage,
    OpenAICompatibleBackendConfig,
    ProviderQuiescence,
    ProviderTiming,
    ProviderTimingShapeStatus,
    ShortChatConfig,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)
from ai_client.network import ChatAction, CoDeclareAction
from ai_client.world import (
    AbilityResultView,
    CoView,
    Freshness,
    HistoryRetention,
    HistoryView,
    PhaseView,
    SelfView,
    WorldSnapshot,
)


def _retention() -> HistoryRetention:
    return HistoryRetention(
        total_seen=0,
        retained_count=0,
        retained_bytes=0,
        dropped_count=0,
        dropped_through_order=None,
        first_retained_order=None,
        last_order=None,
        max_history_records=1024,
        max_history_bytes=2 * 1024 * 1024,
        complete=True,
    )


def _brain_input() -> BrainInput:
    options = (
        BrainActionOption(
            "chat-option",
            ChatAction(1, 2, "day", 1, "chat", "public"),
        ),
        BrainActionOption(
            "co-option",
            CoDeclareAction(1, 2, "day", 1, "co_declare", ("role:claim",)),
        ),
    )
    return BrainInput(
        snapshot=WorldSnapshot(
            version=1,
            freshness=Freshness.CURRENT,
            is_caught_up=True,
            last_applied_seq=2,
            phase=PhaseView("day", 1, 999.0),
            self_view=SelfView("player-self", "role:opaque"),
            history_retention=_retention(),
        ),
        action_context=BrainActionContext(1, 2, 2, True, options),
        history=HistoryView((), True, _retention()),
        co=CoView((), (), True, _retention()),
        ability_results=AbilityResultView((), True, _retention()),
    )


def _branch(projection: object, kind: str) -> object:
    for branch in projection.decision_schema["oneOf"]:
        if branch["properties"]["kind"]["const"] == kind:
            return branch
    raise AssertionError(f"missing {kind} branch")


def _parse_chat(projection: object, message: str) -> object:
    return parse_llm_decision(
        json.dumps(
            {"kind": "chat", "message": message, "option_id": "chat-option"},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        projection=projection,
    )


def _parse_comment(projection: object, comment: str) -> object:
    return parse_llm_decision(
        json.dumps(
            {
                "claimed_role_id": "role:claim",
                "comment": comment,
                "kind": "co_declare",
                "option_id": "co-option",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        projection=projection,
    )


def test_short_chat_config_is_frozen_explicit_and_tightenable() -> None:
    profile = ShortChatConfig()
    assert profile == ShortChatConfig(
        target_min_text_tokens=5,
        target_max_text_tokens=30,
        max_text_chars=80,
        max_text_utf8_bytes=96,
        max_output_tokens=96,
    )
    assert ShortChatConfig(max_text_chars=1, max_text_utf8_bytes=1).max_text_chars == 1
    assert LLMBrainConfig().short_chat is None
    assert LLMBrainConfig(short_chat=profile).short_chat is profile
    assert GenerationSettings(max_output_tokens=profile.max_output_tokens).max_output_tokens == 96
    with pytest.raises(FrozenInstanceError):
        profile.max_text_chars = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "field,value",
    [
        ("target_min_text_tokens", 4),
        ("target_min_text_tokens", True),
        ("target_max_text_tokens", 31),
        ("target_max_text_tokens", True),
        ("max_text_chars", 0),
        ("max_text_chars", 81),
        ("max_text_chars", True),
        ("max_text_utf8_bytes", 0),
        ("max_text_utf8_bytes", 97),
        ("max_text_utf8_bytes", True),
        ("max_output_tokens", 95),
        ("max_output_tokens", True),
    ],
)
def test_short_chat_config_rejects_relaxed_or_coerced_bounds(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError):
        ShortChatConfig(**{field: value})  # type: ignore[arg-type]


def test_short_profile_is_additive_and_freezes_prompt_and_schema_guidance() -> None:
    phase4 = project_brain_input(_brain_input(), config=LLMBrainConfig())
    short = project_brain_input(
        _brain_input(), config=LLMBrainConfig(short_chat=ShortChatConfig())
    )

    assert phase4.short_chat is None
    assert "5-30 model tokens" not in phase4.messages[0].content
    assert _branch(phase4, "chat")["properties"]["message"]["maxLength"] == 240

    assert short.short_chat == ShortChatConfig()
    assert "one short utterance" in short.messages[0].content
    assert "5-30 model tokens" in short.messages[0].content
    assert "80 Unicode code points" in short.messages[0].content
    assert "96 UTF-8 bytes" in short.messages[0].content
    assert _branch(short, "chat")["properties"]["message"] == {
        "maxLength": 80,
        "minLength": 1,
        "type": "string",
    }
    assert _branch(short, "co_declare")["properties"]["comment"] == {
        "maxLength": 80,
        "minLength": 1,
        "type": "string",
    }


def test_short_parser_enforces_char_and_utf8_bounds_without_truncation() -> None:
    projection = project_brain_input(
        _brain_input(), config=LLMBrainConfig(short_chat=ShortChatConfig())
    )
    ascii_at_bound = "x" * 80
    unicode_at_bound = "狼" * 32
    assert len(unicode_at_bound.encode("utf-8")) == 96
    assert _parse_chat(projection, ascii_at_bound).message == ascii_at_bound
    assert _parse_chat(projection, unicode_at_bound).message == unicode_at_bound
    assert _parse_comment(projection, unicode_at_bound).comment == unicode_at_bound

    for invalid in ("", "x" * 81, "狼" * 33, "\ud800"):
        with pytest.raises(DecisionValidationError) as caught:
            _parse_chat(projection, invalid)
        assert caught.value.code is DecisionValidationCode.TEXT_BOUND
    with pytest.raises(DecisionValidationError) as caught:
        _parse_comment(projection, "狼" * 33)
    assert caught.value.code is DecisionValidationCode.TEXT_BOUND


def test_phase4_profile_retains_240_character_behavior_and_no_byte_cap() -> None:
    projection = project_brain_input(_brain_input(), config=LLMBrainConfig())
    value = "狼" * 80
    assert len(value.encode("utf-8")) == 240
    assert _parse_chat(projection, value).message == value


def test_repair_projection_retains_the_exact_short_profile_and_schema() -> None:
    profile = ShortChatConfig(max_text_chars=20, max_text_utf8_bytes=24)
    projection = project_brain_input(
        _brain_input(), config=LLMBrainConfig(short_chat=profile)
    )
    repaired = build_repair_projection(
        projection,
        validation_code=DecisionValidationCode.TEXT_BOUND,
        invalid_output='{"kind":"chat"}',
        config=LLMBrainConfig(short_chat=profile),
    )
    assert repaired.short_chat is profile
    assert repaired.decision_schema == projection.decision_schema
    assert repaired.messages[:2] == projection.messages
    with pytest.raises(DecisionValidationError) as caught:
        _parse_chat(repaired, "x" * 21)
    assert caught.value.code is DecisionValidationCode.TEXT_BOUND


def test_provider_timing_contract_rejects_coercion_and_is_frozen() -> None:
    timing = ProviderTiming(3, 4, 5, 6)
    assert timing.completion_microseconds == 6
    with pytest.raises(FrozenInstanceError):
        timing.prompt_tokens = 7  # type: ignore[misc]
    for field, value in (
        ("prompt_tokens", -1),
        ("prompt_microseconds", True),
        ("completion_tokens", 1.0),
        ("completion_microseconds", "1"),
    ):
        values = {
            "prompt_tokens": 1,
            "prompt_microseconds": 2,
            "completion_tokens": 3,
            "completion_microseconds": 4,
            field: value,
        }
        with pytest.raises(ValueError):
            ProviderTiming(**values)  # type: ignore[arg-type]


def _backend_config(**changes: object) -> OpenAICompatibleBackendConfig:
    values: dict[str, object] = {
        "endpoint": "http://127.0.0.1:8080/v1/chat/completions",
        "model": "model-neutral",
    }
    values.update(changes)
    return OpenAICompatibleBackendConfig(**values)  # type: ignore[arg-type]


def _request() -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        request_id="request-short",
        messages=(LLMMessage("system", "strict"), LLMMessage("user", "{}")),
        output_schema={"type": "object"},
    )


def _response_payload(*, timings: object = None, include_timings: bool = True) -> bytes:
    value: dict[str, object] = {
        "choices": [
            {"finish_reason": "stop", "message": {"content": '{"kind":"none"}'}}
        ],
        "model": "provider-model",
        "usage": {"completion_tokens": 4, "prompt_tokens": 8},
    }
    if include_timings:
        value["timings"] = timings
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=True,
    ).encode("utf-8")


class _FailingStream(httpx.AsyncByteStream):
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.closed = False

    async def __aiter__(self):  # type: ignore[override]
        yield b'{"partial":'
        raise self.error

    async def aclose(self) -> None:
        self.closed = True


class _OverflowStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):  # type: ignore[override]
        yield b"x" * 1025

    async def aclose(self) -> None:
        self.closed = True


class Phase5BackendEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_provider_timing_is_normalized_round_half_up(self) -> None:
        payload = _response_payload(
            timings={
                "prompt_n": 8,
                "prompt_ms": 1.2345,
                "predicted_n": 4,
                "predicted_ms": 0.0005,
            }
        )

        sent_bodies: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent_bodies.append(json.loads(request.content))
            return httpx.Response(
                200, headers={"Content-Type": "application/json"}, content=payload
            )

        profile = ShortChatConfig()
        backend = OpenAICompatibleBackend(
            _backend_config(
                generation=GenerationSettings(
                    max_output_tokens=profile.max_output_tokens
                )
            ),
            transport=httpx.MockTransport(handler),
        )
        response = await backend.generate(_request())
        await backend.aclose()
        self.assertEqual(response.provider_timing, ProviderTiming(8, 1235, 4, 1))
        self.assertEqual(sent_bodies[0]["max_tokens"], 96)

    async def test_absent_or_malformed_optional_timing_never_fails_generation(self) -> None:
        malformed_values = (
            None,
            {},
            {"prompt_n": 8, "prompt_ms": 1, "predicted_n": 4},
            {
                "prompt_n": True,
                "prompt_ms": 1,
                "predicted_n": 4,
                "predicted_ms": 2,
            },
            {"prompt_n": 8, "prompt_ms": -1, "predicted_n": 4, "predicted_ms": 2},
            {"prompt_n": 8, "prompt_ms": math.nan, "predicted_n": 4, "predicted_ms": 2},
            {"prompt_n": 8, "prompt_ms": 1, "predicted_n": 4, "predicted_ms": math.inf},
        )
        cases = [(False, None)] + [(True, value) for value in malformed_values]
        for include_timings, timings in cases:
            with self.subTest(include_timings=include_timings, timings=timings):
                payload = _response_payload(
                    timings=timings, include_timings=include_timings
                )

                def handler(_request: httpx.Request) -> httpx.Response:
                    return httpx.Response(
                        200,
                        headers={"Content-Type": "application/json"},
                        content=payload,
                    )

                backend = OpenAICompatibleBackend(
                    _backend_config(), transport=httpx.MockTransport(handler)
                )
                response = await backend.generate(_request())
                await backend.aclose()
                self.assertIsNone(response.provider_timing)

    async def test_valid_provider_timing_with_extra_members_is_accepted(self) -> None:
        payload = _response_payload(
            timings={
                "prompt_n": 8,
                "prompt_ms": 1.2345,
                "predicted_n": 4,
                "predicted_ms": 0.0005,
                "private-extra-key-sentinel": "private-extra-value-sentinel",
            }
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"Content-Type": "application/json"}, content=payload
            )

        backend = OpenAICompatibleBackend(
            _backend_config(), transport=httpx.MockTransport(handler)
        )
        response = await backend.generate(_request())
        await backend.aclose()

        self.assertEqual(response.provider_timing, ProviderTiming(8, 1235, 4, 1))
        self.assertEqual(
            response.provider_timing_diagnostic.status,
            ProviderTimingShapeStatus.VALID_WITH_EXTRA,
        )
        self.assertEqual(response.provider_timing_diagnostic.extra_key_count, 1)
        self.assertNotIn("private-extra", repr(response.provider_timing_diagnostic))

    async def test_pre_send_closed_connect_and_pool_failures_are_not_started(self) -> None:
        calls = 0

        def should_not_send(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200)

        backend = OpenAICompatibleBackend(
            _backend_config(max_request_bytes=1024),
            transport=httpx.MockTransport(should_not_send),
        )
        oversized = StructuredGenerationRequest(
            request_id="oversized",
            messages=(LLMMessage("system", "x" * 2000),),
            output_schema={"type": "object"},
        )
        with self.assertRaises(LLMBackendError) as caught:
            await backend.generate(oversized)
        self.assertIs(
            caught.exception.provider_quiescence, ProviderQuiescence.NOT_STARTED
        )
        self.assertEqual(calls, 0)
        await backend.aclose()
        with self.assertRaises(LLMBackendError) as caught:
            await backend.generate(_request())
        self.assertIs(
            caught.exception.provider_quiescence, ProviderQuiescence.NOT_STARTED
        )

        for error_type in (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            with self.subTest(error_type=error_type.__name__):
                def fail(request: httpx.Request) -> httpx.Response:
                    raise error_type("private", request=request)

                backend = OpenAICompatibleBackend(
                    _backend_config(), transport=httpx.MockTransport(fail)
                )
                with self.assertRaises(LLMBackendError) as caught:
                    await backend.generate(_request())
                await backend.aclose()
                self.assertIs(
                    caught.exception.provider_quiescence,
                    ProviderQuiescence.NOT_STARTED,
                )

    async def test_complete_http_and_envelope_errors_are_proven_terminal(self) -> None:
        cases = (
            (
                503,
                {"Content-Type": "text/plain"},
                b"complete-private-body",
                LLMBackendErrorCode.HTTP_STATUS,
            ),
            (
                200,
                {"Content-Type": "application/json"},
                b"not-json",
                LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID,
            ),
            (
                200,
                {"Content-Type": "application/json"},
                b"\xff",
                LLMBackendErrorCode.RESPONSE_ENCODING,
            ),
            (
                200,
                {"Content-Type": "text/plain"},
                _response_payload(include_timings=False),
                LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID,
            ),
        )
        for status, headers, body, code in cases:
            with self.subTest(status=status, code=code):
                def handler(_request: httpx.Request) -> httpx.Response:
                    return httpx.Response(status, headers=headers, content=body)

                backend = OpenAICompatibleBackend(
                    _backend_config(), transport=httpx.MockTransport(handler)
                )
                with self.assertRaises(LLMBackendError) as caught:
                    await backend.generate(_request())
                await backend.aclose()
                self.assertIs(caught.exception.code, code)
                self.assertIs(
                    caught.exception.provider_quiescence,
                    ProviderQuiescence.PROVEN_TERMINAL,
                )

    async def test_timeout_partial_read_write_decode_and_size_are_unknown(self) -> None:
        async def timeout_handler(_request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(1)
            return httpx.Response(200)

        backend = OpenAICompatibleBackend(
            _backend_config(request_timeout_seconds=0.01),
            transport=httpx.MockTransport(timeout_handler),
        )
        with self.assertRaises(LLMBackendError) as caught:
            await backend.generate(_request())
        await backend.aclose()
        self.assertIs(caught.exception.provider_quiescence, ProviderQuiescence.UNKNOWN)

        for error_type in (
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.ReadError,
            httpx.WriteError,
            httpx.DecodingError,
        ):
            with self.subTest(error_type=error_type.__name__):
                def fail(request: httpx.Request) -> httpx.Response:
                    raise error_type("private", request=request)

                backend = OpenAICompatibleBackend(
                    _backend_config(), transport=httpx.MockTransport(fail)
                )
                with self.assertRaises(LLMBackendError) as caught:
                    await backend.generate(_request())
                await backend.aclose()
                self.assertIs(
                    caught.exception.provider_quiescence,
                    ProviderQuiescence.UNKNOWN,
                )

        request = httpx.Request("POST", "http://127.0.0.1/")
        stream = _FailingStream(httpx.ReadError("private", request=request))

        def partial(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                stream=stream,
            )

        backend = OpenAICompatibleBackend(
            _backend_config(), transport=httpx.MockTransport(partial)
        )
        with self.assertRaises(LLMBackendError) as caught:
            await backend.generate(_request())
        await backend.aclose()
        self.assertIs(caught.exception.provider_quiescence, ProviderQuiescence.UNKNOWN)
        self.assertTrue(stream.closed)

        overflow = _OverflowStream()

        def too_large(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                stream=overflow,
            )

        backend = OpenAICompatibleBackend(
            _backend_config(max_response_bytes=1024),
            transport=httpx.MockTransport(too_large),
        )
        with self.assertRaises(LLMBackendError) as caught:
            await backend.generate(_request())
        await backend.aclose()
        self.assertIs(caught.exception.code, LLMBackendErrorCode.RESPONSE_TOO_LARGE)
        self.assertIs(caught.exception.provider_quiescence, ProviderQuiescence.UNKNOWN)
        self.assertTrue(overflow.closed)

        declared_overflow = _OverflowStream()

        def declared_too_large(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={
                    "Content-Length": "1025",
                    "Content-Type": "application/json",
                },
                stream=declared_overflow,
            )

        backend = OpenAICompatibleBackend(
            _backend_config(max_response_bytes=1024),
            transport=httpx.MockTransport(declared_too_large),
        )
        with self.assertRaises(LLMBackendError) as caught:
            await backend.generate(_request())
        await backend.aclose()
        self.assertIs(caught.exception.code, LLMBackendErrorCode.RESPONSE_TOO_LARGE)
        self.assertIs(caught.exception.provider_quiescence, ProviderQuiescence.UNKNOWN)
        self.assertTrue(declared_overflow.closed)


def test_backend_error_default_is_deliberately_unknown_and_sanitized() -> None:
    error = LLMBackendError(LLMBackendErrorCode.CONNECT_FAILED)
    assert error.provider_quiescence is ProviderQuiescence.UNKNOWN
    assert error.args == ("CONNECT_FAILED",)
    assert "UNKNOWN" not in str(error)
    with pytest.raises(TypeError):
        LLMBackendError(
            LLMBackendErrorCode.CONNECT_FAILED,
            provider_quiescence="PROVEN_TERMINAL",  # type: ignore[arg-type]
        )


class _ScriptedBackend:
    def __init__(self, outputs: list[str | tuple[str, str | None]]) -> None:
        self.outputs = list(outputs)
        self.requests: list[StructuredGenerationRequest] = []
        self.identity = BackendIdentity(
            "test",
            "http://127.0.0.1:1",
            "/v1/chat/completions",
            "model-neutral",
            "0" * 64,
        )

    async def generate(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse:
        self.requests.append(request)
        value = self.outputs.pop(0)
        if isinstance(value, tuple):
            text, finish_reason = value
        else:
            text, finish_reason = value, "stop"
        return StructuredGenerationResponse(
            request.request_id,
            text,
            "model-neutral",
            finish_reason,
            LLMUsage(3, 2),
        )

    async def aclose(self) -> None:
        return None


class _MemoryAudit:
    def __init__(self) -> None:
        self.records: list[object] = []

    async def write(self, record: object) -> AuditWriteAck:
        self.records.append(record)
        return AuditWriteAck(len(self.records))


class Phase5ShortProfileBrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_length_marked_first_attempt_repairs_to_non_length_decision(self) -> None:
        first = json.dumps(
            {
                "kind": "chat",
                "message": "complete but length-marked",
                "option_id": "chat-option",
            }
        )
        repaired = json.dumps(
            {"kind": "chat", "message": "repair exact", "option_id": "chat-option"}
        )
        backend = _ScriptedBackend([(first, "length"), (repaired, "stop")])
        audit = _MemoryAudit()
        brain = LLMBrain(
            backend=backend,
            audit=audit,
            identity=LLMClientIdentity("game", "player-self"),
            config=LLMBrainConfig(short_chat=ShortChatConfig()),
            request_id_factory=lambda: "request-length-repaired",
        )

        decision = await brain.decide(_brain_input())

        self.assertEqual(decision.message, "repair exact")
        self.assertEqual(len(backend.requests), 2)
        self.assertEqual(len(audit.records), 2)
        initial, repair = audit.records
        self.assertIs(initial.status, AiAuditStatus.OUTPUT_INVALID)
        self.assertIs(initial.validation_code, DecisionValidationCode.SCHEMA)
        self.assertEqual(initial.response_text, first)
        self.assertEqual(initial.finish_reason, "length")
        self.assertIsNone(initial.decision)
        self.assertIs(repair.status, AiAuditStatus.REPAIR_SUCCEEDED)
        self.assertEqual(repair.response_text, repaired)
        self.assertEqual(repair.finish_reason, "stop")
        self.assertEqual(repair.decision.text, "repair exact")
        snapshot = brain.snapshot()
        self.assertEqual(snapshot.backend_calls, 2)
        self.assertEqual(snapshot.repair_attempts, 1)
        self.assertEqual(snapshot.decisions, 1)
        self.assertIs(snapshot.last_status, LLMInvocationStatus.REPAIR_SUCCEEDED)

    async def test_length_marked_repair_is_terminal_with_exact_audit(self) -> None:
        first = json.dumps(
            {"kind": "chat", "message": "first length", "option_id": "chat-option"}
        )
        second = json.dumps(
            {"kind": "chat", "message": "second length", "option_id": "chat-option"}
        )
        backend = _ScriptedBackend([(first, "length"), (second, "length")])
        audit = _MemoryAudit()
        brain = LLMBrain(
            backend=backend,
            audit=audit,
            identity=LLMClientIdentity("game", "player-self"),
            config=LLMBrainConfig(short_chat=ShortChatConfig()),
            request_id_factory=lambda: "request-length-terminal",
        )

        with self.assertRaises(LLMInvocationError) as caught:
            await brain.decide(_brain_input())

        self.assertIs(caught.exception.status, LLMInvocationStatus.REPAIR_FAILED)
        self.assertEqual(caught.exception.code, DecisionValidationCode.SCHEMA.value)
        self.assertEqual(len(backend.requests), 2)
        self.assertEqual(len(audit.records), 2)
        initial, repair = audit.records
        self.assertIs(initial.status, AiAuditStatus.OUTPUT_INVALID)
        self.assertIs(initial.validation_code, DecisionValidationCode.SCHEMA)
        self.assertEqual(initial.response_text, first)
        self.assertEqual(initial.finish_reason, "length")
        self.assertIsNone(initial.decision)
        self.assertIs(repair.status, AiAuditStatus.REPAIR_FAILED)
        self.assertIs(repair.validation_code, DecisionValidationCode.SCHEMA)
        self.assertEqual(repair.response_text, second)
        self.assertEqual(repair.finish_reason, "length")
        self.assertIsNone(repair.decision)
        snapshot = brain.snapshot()
        self.assertEqual(snapshot.backend_calls, 2)
        self.assertEqual(snapshot.repair_attempts, 1)
        self.assertEqual(snapshot.decisions, 0)
        self.assertEqual(snapshot.failures, 1)
        self.assertIs(snapshot.last_status, LLMInvocationStatus.REPAIR_FAILED)
        self.assertEqual(snapshot.last_error_code, DecisionValidationCode.SCHEMA.value)

    async def test_phase4_profile_still_accepts_complete_length_marked_json(self) -> None:
        text = json.dumps(
            {
                "kind": "chat",
                "message": "phase four unchanged",
                "option_id": "chat-option",
            }
        )
        backend = _ScriptedBackend([(text, "length")])
        audit = _MemoryAudit()
        brain = LLMBrain(
            backend=backend,
            audit=audit,
            identity=LLMClientIdentity("game", "player-self"),
            config=LLMBrainConfig(short_chat=None),
            request_id_factory=lambda: "request-phase4-length",
        )

        decision = await brain.decide(_brain_input())

        self.assertEqual(decision.message, "phase four unchanged")
        self.assertEqual(len(backend.requests), 1)
        self.assertEqual(len(audit.records), 1)
        self.assertIs(audit.records[0].status, AiAuditStatus.DECISION)
        self.assertEqual(audit.records[0].response_text, text)
        self.assertEqual(audit.records[0].finish_reason, "length")
        self.assertIsNone(audit.records[0].validation_code)
        self.assertEqual(brain.snapshot().repair_attempts, 0)

    async def test_over_bound_text_uses_only_the_existing_single_repair(self) -> None:
        invalid = json.dumps(
            {"kind": "chat", "message": "x" * 81, "option_id": "chat-option"}
        )
        valid = json.dumps(
            {"kind": "chat", "message": "kept exact", "option_id": "chat-option"}
        )
        backend = _ScriptedBackend([invalid, valid])
        audit = _MemoryAudit()
        brain = LLMBrain(
            backend=backend,
            audit=audit,
            identity=LLMClientIdentity("game", "player-self"),
            config=LLMBrainConfig(short_chat=ShortChatConfig()),
            request_id_factory=lambda: "request-short-profile",
        )

        decision = await brain.decide(_brain_input())

        self.assertEqual(decision.message, "kept exact")
        self.assertEqual(len(backend.requests), 2)
        self.assertEqual(len(audit.records), 2)
        self.assertIs(audit.records[0].status, AiAuditStatus.OUTPUT_INVALID)
        self.assertIs(
            audit.records[0].validation_code, DecisionValidationCode.TEXT_BOUND
        )
        self.assertIs(audit.records[1].status, AiAuditStatus.REPAIR_SUCCEEDED)

        backend = _ScriptedBackend([invalid, invalid])
        audit = _MemoryAudit()
        brain = LLMBrain(
            backend=backend,
            audit=audit,
            identity=LLMClientIdentity("game", "player-self"),
            config=LLMBrainConfig(short_chat=ShortChatConfig()),
            request_id_factory=lambda: "request-short-profile-fail",
        )
        with self.assertRaises(LLMInvocationError):
            await brain.decide(_brain_input())
        self.assertEqual(len(backend.requests), 2)
        self.assertEqual(len(audit.records), 2)
        self.assertIs(audit.records[-1].status, AiAuditStatus.REPAIR_FAILED)
