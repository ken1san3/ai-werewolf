from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
import json
import unittest

import httpx
import pytest

from ai_client.llm.backend import (
    OpenAICompatibleBackend,
    StructuredLLMBackend,
    _provider_timing_observation,
    _extract_http_error_detail,
)
from ai_client.llm.admission_broker import _structured_response_to_wire
from ai_client.llm.types import (
    GenerationSettings,
    LLMBackendError,
    LLMBackendErrorCode,
    LLMMessage,
    LLMUsage,
    LlamaCppStructuredOutputConfig,
    OpenAICompatibleBackendConfig,
    ProviderTimingFieldState,
    ProviderTimingShapeStatus,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)


def _config(**changes: object) -> OpenAICompatibleBackendConfig:
    values: dict[str, object] = {
        "endpoint": "http://127.0.0.1:8080/v1/chat/completions",
        "model": "local-model",
    }
    values.update(changes)
    return OpenAICompatibleBackendConfig(**values)  # type: ignore[arg-type]


def _request(*, schema: dict[str, object] | None = None) -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        request_id="request-exact-A",
        messages=(
            LLMMessage("system", "return JSON"),
            LLMMessage("user", '{"message":"日本語"}'),
        ),
        output_schema=schema or {"type": "object", "additionalProperties": False},
    )


def _success_payload(content: str = '{"kind":"none"}') -> bytes:
    return json.dumps(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": content, "role": "assistant"},
                }
            ],
            "model": "provider-model",
            "usage": {"completion_tokens": 4, "prompt_tokens": 8},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _expected_provider_payload(
    request: StructuredGenerationRequest,
    config: OpenAICompatibleBackendConfig,
) -> bytes:
    if config.structured_mode == "json_schema":
        response_format: dict[str, object] = {
            "type": "json_schema",
            "json_schema": {
                "name": "aiwolf_brain_decision",
                "strict": True,
                "schema": {
                    key: list(value) if isinstance(value, tuple) else value
                    for key, value in request.output_schema.items()
                },
            },
        }
    else:
        response_format = {"type": "json_object"}
    body = {
        "max_tokens": config.generation.max_output_tokens,
        "messages": [
            {"content": message.content, "role": message.role}
            for message in request.messages
        ],
        "model": config.model,
        "response_format": response_format,
        "stream": False,
        "temperature": config.generation.temperature,
    }
    return json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class _FragmentedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self):  # type: ignore[override]
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _BlockingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def __aiter__(self):  # type: ignore[override]
        self.entered.set()
        await self.release.wait()
        yield _success_payload()

    async def aclose(self) -> None:
        self.closed = True


class _TimingWithUnreadableExtra(Mapping[object, object]):
    def __init__(self) -> None:
        self._required: dict[object, object] = {
            "prompt_n": 8,
            "prompt_ms": 1.2345,
            "predicted_n": 4,
            "predicted_ms": 2.0004,
        }
        self.extra_reads = 0

    def __getitem__(self, key: object) -> object:
        if key == "private-extra-key-sentinel":
            self.extra_reads += 1
            raise AssertionError("adapter inspected an extra timing value")
        return self._required[key]

    def __iter__(self) -> Iterator[object]:
        return iter((*self._required, "private-extra-key-sentinel"))

    def __len__(self) -> int:
        return len(self._required) + 1


class Phase4LLMBackendTests(unittest.IsolatedAsyncioTestCase):
    def _parse_timing(self, timing: object = ...):
        envelope: dict[str, object] = json.loads(_success_payload())
        if timing is not ...:
            envelope["timings"] = timing
        return OpenAICompatibleBackend._parse_response(
            "timing-request",
            json.dumps(
                envelope,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
        )

    def test_provider_timing_diagnostic_absent_and_non_object_are_distinct(self) -> None:
        absent = self._parse_timing()
        self.assertIsNone(absent.provider_timing)
        self.assertEqual(
            absent.provider_timing_diagnostic.status,
            ProviderTimingShapeStatus.ABSENT,
        )
        self.assertEqual(
            {
                absent.provider_timing_diagnostic.prompt_n,
                absent.provider_timing_diagnostic.prompt_ms,
                absent.provider_timing_diagnostic.predicted_n,
                absent.provider_timing_diagnostic.predicted_ms,
            },
            {ProviderTimingFieldState.NOT_OBSERVED},
        )
        for value in (None, "private-type-sentinel", [], 7):
            with self.subTest(value=type(value).__name__):
                response = self._parse_timing(value)
                diagnostic = response.provider_timing_diagnostic
                self.assertEqual(
                    diagnostic.status, ProviderTimingShapeStatus.NOT_OBJECT
                )
                self.assertEqual(diagnostic.extra_key_count, 0)
                self.assertNotIn("private-type-sentinel", repr(diagnostic))

    def test_provider_timing_diagnostic_table_precedence_and_states(self) -> None:
        valid = {
            "prompt_n": 8,
            "prompt_ms": 1.2345,
            "predicted_n": 4,
            "predicted_ms": 2.0004,
        }
        cases = (
            (
                {"private-extra-only": "private-value"},
                ProviderTimingShapeStatus.MISSING_REQUIRED_KEY,
                ProviderTimingFieldState.MISSING,
                1,
            ),
            (
                {
                    "prompt_ms": True,
                    "predicted_n": 4,
                    "predicted_ms": 2.0,
                },
                ProviderTimingShapeStatus.MISSING_REQUIRED_KEY,
                ProviderTimingFieldState.MISSING,
                0,
            ),
            (
                {**valid, "prompt_n": True},
                ProviderTimingShapeStatus.INVALID_REQUIRED_VALUE,
                ProviderTimingFieldState.INVALID_TYPE,
                0,
            ),
            (
                {**valid, "prompt_n": -1, "private-extra": "private-value"},
                ProviderTimingShapeStatus.INVALID_REQUIRED_VALUE,
                ProviderTimingFieldState.INVALID_VALUE,
                1,
            ),
            (
                {**valid, "prompt_n": 1.0},
                ProviderTimingShapeStatus.INVALID_REQUIRED_VALUE,
                ProviderTimingFieldState.INVALID_TYPE,
                0,
            ),
            (
                {**valid, "private-extra": "private-value"},
                ProviderTimingShapeStatus.VALID_WITH_EXTRA,
                ProviderTimingFieldState.VALID,
                1,
            ),
            (
                valid,
                ProviderTimingShapeStatus.VALID_EXACT,
                ProviderTimingFieldState.VALID,
                0,
            ),
        )
        for timing, status, prompt_state, extra_count in cases:
            with self.subTest(status=status, prompt_state=prompt_state):
                response = self._parse_timing(timing)
                diagnostic = response.provider_timing_diagnostic
                self.assertEqual(diagnostic.status, status)
                self.assertEqual(diagnostic.prompt_n, prompt_state)
                self.assertEqual(diagnostic.extra_key_count, extra_count)
                self.assertEqual(
                    response.provider_timing is not None,
                    status
                    in {
                        ProviderTimingShapeStatus.VALID_EXACT,
                        ProviderTimingShapeStatus.VALID_WITH_EXTRA,
                    },
                )
                self.assertNotIn("private-extra", repr(diagnostic))
                self.assertNotIn("private-value", repr(diagnostic))
        exact = self._parse_timing(valid).provider_timing
        self.assertEqual(exact.prompt_microseconds, 1235)
        self.assertEqual(exact.completion_microseconds, 2000)

    def test_provider_timing_exact_and_extra_have_normalized_parity(self) -> None:
        valid = {
            "prompt_n": 8,
            "prompt_ms": 1.2345,
            "predicted_n": 4,
            "predicted_ms": 2.0004,
        }
        exact = self._parse_timing(valid)
        with_extra = self._parse_timing(
            {
                **valid,
                "private-extra-key-sentinel": "private-extra-value-sentinel",
                "second-private-extra": {"nested-private-value": True},
            }
        )

        self.assertEqual(with_extra.provider_timing, exact.provider_timing)
        self.assertEqual(
            exact.provider_timing_diagnostic.status,
            ProviderTimingShapeStatus.VALID_EXACT,
        )
        self.assertEqual(exact.provider_timing_diagnostic.extra_key_count, 0)
        self.assertEqual(
            with_extra.provider_timing_diagnostic.status,
            ProviderTimingShapeStatus.VALID_WITH_EXTRA,
        )
        self.assertEqual(with_extra.provider_timing_diagnostic.extra_key_count, 2)
        self.assertNotIn("private-extra", repr(with_extra.provider_timing_diagnostic))
        self.assertNotIn("nested-private", repr(with_extra.provider_timing_diagnostic))

    def test_provider_timing_does_not_read_extra_member_values(self) -> None:
        timing = _TimingWithUnreadableExtra()

        provider_timing, diagnostic = _provider_timing_observation(
            {"timings": timing}
        )

        self.assertEqual(timing.extra_reads, 0)
        self.assertIsNotNone(provider_timing)
        self.assertEqual(
            diagnostic.status, ProviderTimingShapeStatus.VALID_WITH_EXTRA
        )
        self.assertEqual(diagnostic.extra_key_count, 1)

    def test_provider_timing_diagnostic_each_missing_invalid_and_numeric_case(self) -> None:
        valid: dict[str, object] = {
            "prompt_n": 8,
            "prompt_ms": 1.0,
            "predicted_n": 4,
            "predicted_ms": 2.0,
        }
        state_names = {
            "prompt_n": "prompt_n",
            "prompt_ms": "prompt_ms",
            "predicted_n": "predicted_n",
            "predicted_ms": "predicted_ms",
        }
        for key, attribute in state_names.items():
            missing = dict(valid)
            del missing[key]
            missing_response = self._parse_timing(missing)
            diagnostic = missing_response.provider_timing_diagnostic
            self.assertIsNone(missing_response.provider_timing)
            self.assertEqual(
                diagnostic.status, ProviderTimingShapeStatus.MISSING_REQUIRED_KEY
            )
            self.assertEqual(
                getattr(diagnostic, attribute), ProviderTimingFieldState.MISSING
            )
            for invalid in (True, "private-value", [], {}):
                changed = {**valid, key: invalid}
                invalid_response = self._parse_timing(changed)
                diagnostic = invalid_response.provider_timing_diagnostic
                self.assertIsNone(invalid_response.provider_timing)
                self.assertEqual(
                    getattr(diagnostic, attribute),
                    ProviderTimingFieldState.INVALID_TYPE,
                )
        for key in ("prompt_n", "predicted_n"):
            invalid_response = self._parse_timing(
                {**valid, key: -1, "private-extra": "private-value"}
            )
            diagnostic = invalid_response.provider_timing_diagnostic
            self.assertIsNone(invalid_response.provider_timing)
            self.assertEqual(
                getattr(diagnostic, key), ProviderTimingFieldState.INVALID_VALUE
            )
        for key in ("prompt_ms", "predicted_ms"):
            for invalid in (-1, float("nan"), float("inf"), float("-inf")):
                invalid_response = self._parse_timing(
                    {**valid, key: invalid, "private-extra": "private-value"}
                )
                diagnostic = invalid_response.provider_timing_diagnostic
                self.assertIsNone(invalid_response.provider_timing)
                self.assertEqual(
                    getattr(diagnostic, key), ProviderTimingFieldState.INVALID_VALUE
                )

    def test_diagnostic_is_final_optional_response_field(self) -> None:
        legacy = StructuredGenerationResponse(
            "legacy", '{"kind":"none"}', None, None, LLMUsage(), None
        )
        self.assertIsNone(legacy.provider_timing_diagnostic)

    def test_arbitrary_timing_and_sibling_material_never_reaches_response_wire(self) -> None:
        envelope: dict[str, object] = json.loads(_success_payload("generated-owner-only"))
        envelope["timings"] = {
            "prompt_n": 8,
            "prompt_ms": 1,
            "predicted_n": 4,
            "predicted_ms": 2,
            "private-extra-key-sentinel": "private-extra-value-sentinel",
        }
        envelope["private-sibling-key-sentinel"] = "private-sibling-value-sentinel"
        response = OpenAICompatibleBackend._parse_response(
            "privacy-request",
            json.dumps(envelope, separators=(",", ":")).encode("utf-8"),
        )
        diagnostic_repr = repr(response.provider_timing_diagnostic)
        wire = json.dumps(_structured_response_to_wire(response), sort_keys=True)
        for sentinel in (
            "private-extra-key-sentinel",
            "private-extra-value-sentinel",
            "private-sibling-key-sentinel",
            "private-sibling-value-sentinel",
        ):
            self.assertNotIn(sentinel, diagnostic_repr)
            self.assertNotIn(sentinel, wire)
        self.assertIn("generated-owner-only", wire)
        self.assertNotIn("diagnostic", wire)

    async def test_exact_json_schema_body_headers_identity_and_response(self) -> None:
        captured: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                content=_success_payload(),
            )

        config = _config(
            api_key="private-sentinel",
            generation=GenerationSettings(max_output_tokens=257, temperature=0.75),
        )
        backend = OpenAICompatibleBackend(
            config, transport=httpx.MockTransport(handler)
        )
        self.assertIsInstance(backend, StructuredLLMBackend)
        response = await backend.generate(_request())
        await backend.aclose()

        self.assertEqual(len(captured), 1)
        sent = captured[0]
        self.assertEqual(sent.method, "POST")
        self.assertEqual(str(sent.url), config.endpoint)
        self.assertEqual(sent.headers["authorization"], "Bearer private-sentinel")
        self.assertEqual(sent.headers["accept"], "application/json")
        self.assertEqual(sent.headers["content-type"], "application/json")
        body = json.loads(sent.content)
        self.assertEqual(
            body,
            {
                "max_tokens": 257,
                "messages": [
                    {"content": "return JSON", "role": "system"},
                    {"content": '{"message":"日本語"}', "role": "user"},
                ],
                "model": "local-model",
                "response_format": {
                    "json_schema": {
                        "name": "aiwolf_brain_decision",
                        "schema": {
                            "additionalProperties": False,
                            "type": "object",
                        },
                        "strict": True,
                    },
                    "type": "json_schema",
                },
                "stream": False,
                "temperature": 0.75,
            },
        )
        expected = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(sent.content, expected)
        self.assertEqual(response.request_id, "request-exact-A")
        self.assertEqual(response.text, '{"kind":"none"}')
        self.assertEqual(response.provider_model, "provider-model")
        self.assertEqual(response.finish_reason, "stop")
        self.assertEqual(response.usage.prompt_tokens, 8)
        self.assertEqual(response.usage.completion_tokens, 4)
        self.assertEqual(backend.identity.backend_type, "openai_compatible_http")
        self.assertEqual(backend.identity.endpoint_origin, "http://127.0.0.1:8080")
        self.assertEqual(backend.identity.endpoint_path, "/v1/chat/completions")
        self.assertEqual(backend.identity.config_fingerprint, config.config_fingerprint)
        self.assertNotIn("private-sentinel", repr(backend.identity))

    async def test_llama_cpp_profile_adds_exact_schema_safe_fields(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            envelope = json.loads(_success_payload('{"kind":"none"}'))
            envelope["choices"][0]["message"]["reasoning_content"] = (
                "private reasoning sentinel"
            )
            return httpx.Response(200, json=envelope)

        backend = OpenAICompatibleBackend(
            _config(
                llama_cpp_structured_output=LlamaCppStructuredOutputConfig(),
            ),
            transport=httpx.MockTransport(handler),
        )
        response = await backend.generate(
            _request(
                schema={
                    "type": "object",
                    "properties": {"kind": {"const": "none"}},
                    "required": ["kind"],
                    "additionalProperties": False,
                }
            )
        )
        await backend.aclose()

        self.assertEqual(len(captured), 1)
        body = json.loads(captured[0].content)
        self.assertEqual(body["reasoning_format"], "deepseek")
        self.assertIs(body["chat_template_kwargs"]["enable_thinking"], False)
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertIs(body["response_format"]["json_schema"]["strict"], True)
        self.assertEqual(
            body["response_format"]["json_schema"]["schema"],
            {
                "type": "object",
                "properties": {"kind": {"const": "none"}},
                "required": ["kind"],
                "additionalProperties": False,
            },
        )
        self.assertEqual(response.text, '{"kind":"none"}')
        self.assertNotIn("private reasoning sentinel", repr(response))

    async def test_default_payload_remains_provider_neutral(self) -> None:
        config = _config()
        backend = OpenAICompatibleBackend(
            config,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(500)
            ),
        )
        payload = backend._request_payload(_request())
        await backend.aclose()
        self.assertEqual(payload, _expected_provider_payload(_request(), config))
        body = json.loads(payload)
        self.assertNotIn("reasoning_format", body)
        self.assertNotIn("chat_template_kwargs", body)

    async def test_api_key_is_omitted_and_json_object_is_explicit(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=json.loads(_success_payload()))

        backend = OpenAICompatibleBackend(
            _config(structured_mode="json_object"),
            transport=httpx.MockTransport(handler),
        )
        await backend.generate(_request())
        await backend.aclose()
        self.assertNotIn("authorization", captured[0].headers)
        self.assertEqual(
            json.loads(captured[0].content)["response_format"],
            {"type": "json_object"},
        )
        self.assertEqual(len(captured), 1)

    async def test_complete_request_bound_rejects_before_transport(self) -> None:
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=_success_payload())

        backend = OpenAICompatibleBackend(
            _config(max_request_bytes=1024),
            transport=httpx.MockTransport(handler),
        )
        request = _request(schema={"type": "object", "description": "x" * 1200})
        with self.assertRaises(LLMBackendError) as caught:
            await backend.generate(request)
        await backend.aclose()
        self.assertEqual(caught.exception.code, LLMBackendErrorCode.RESPONSE_TOO_LARGE)
        self.assertEqual(str(caught.exception), "RESPONSE_TOO_LARGE")
        self.assertEqual(calls, 0)

    async def test_complete_request_at_exact_bound_is_admitted_unchanged(self) -> None:
        config = _config(max_request_bytes=1024)
        base_request = _request(schema={"description": "", "type": "object"})
        base_payload = _expected_provider_payload(base_request, config)
        padding = 1024 - len(base_payload)
        self.assertGreater(padding, 0)
        request = _request(
            schema={"description": "x" * padding, "type": "object"}
        )
        expected = _expected_provider_payload(request, config)
        self.assertEqual(len(expected), config.max_request_bytes)
        captured: list[bytes] = []

        def handler(sent: httpx.Request) -> httpx.Response:
            captured.append(sent.content)
            return httpx.Response(200, json=json.loads(_success_payload()))

        backend = OpenAICompatibleBackend(
            config,
            transport=httpx.MockTransport(handler),
        )
        response = await backend.generate(request)
        await backend.aclose()
        self.assertEqual(response.request_id, request.request_id)
        self.assertEqual(captured, [expected])

    async def test_fragmented_response_at_exact_bound_succeeds(self) -> None:
        overhead = len(_success_payload(""))
        payload = _success_payload("x" * (1024 - overhead))
        self.assertEqual(len(payload), 1024)
        stream = _FragmentedStream([payload[:17], payload[17:511], payload[511:]])

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json", "Content-Length": "1024"},
                stream=stream,
            )

        backend = OpenAICompatibleBackend(
            _config(max_response_bytes=1024),
            transport=httpx.MockTransport(handler),
        )
        response = await backend.generate(_request())
        await backend.aclose()
        self.assertEqual(len(response.text), 1024 - overhead)
        self.assertTrue(stream.closed)

    async def test_declared_and_streamed_response_overflow_abort(self) -> None:
        for declared, chunks in (
            ("1025", [b"{}"]),
            (None, [b"x" * 700, b"y" * 325]),
        ):
            stream = _FragmentedStream(chunks)

            def handler(_request: httpx.Request) -> httpx.Response:
                headers = {"Content-Type": "application/json"}
                if declared is not None:
                    headers["Content-Length"] = declared
                return httpx.Response(200, headers=headers, stream=stream)

            backend = OpenAICompatibleBackend(
                _config(max_response_bytes=1024),
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(LLMBackendError) as caught:
                await backend.generate(_request())
            await backend.aclose()
            self.assertEqual(
                caught.exception.code, LLMBackendErrorCode.RESPONSE_TOO_LARGE
            )
            self.assertTrue(stream.closed)

    async def test_http_status_is_sanitized_zero_retry_and_redirect_not_followed(self) -> None:
        for status, retryable in ((302, False), (404, False), (429, True), (503, True)):
            calls = 0

            def handler(_request: httpx.Request) -> httpx.Response:
                nonlocal calls
                calls += 1
                return httpx.Response(
                    status,
                    headers={"Location": "http://127.0.0.1:8080/private"},
                    content=b"body-secret",
                )

            backend = OpenAICompatibleBackend(
                _config(), transport=httpx.MockTransport(handler)
            )
            with self.assertRaises(LLMBackendError) as caught:
                await backend.generate(_request())
            await backend.aclose()
            error = caught.exception
            self.assertEqual(error.code, LLMBackendErrorCode.HTTP_STATUS)
            self.assertEqual(error.http_status, status)
            self.assertEqual(error.retryable, retryable)
            self.assertEqual(str(error), "HTTP_STATUS")
            self.assertNotIn("secret", repr(error.args))
            self.assertEqual(calls, 1)

    async def test_connection_error_is_sanitized_and_zero_retry(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("private-sentinel", request=request)

        backend = OpenAICompatibleBackend(
            _config(api_key="private-sentinel"),
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(LLMBackendError) as caught:
            await backend.generate(_request())
        await backend.aclose()
        self.assertEqual(caught.exception.code, LLMBackendErrorCode.CONNECT_FAILED)
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.args, ("CONNECT_FAILED",))
        self.assertEqual(calls, 1)

    async def test_outer_request_timeout_is_sanitized_and_zero_retry(self) -> None:
        calls = 0

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            await asyncio.sleep(1)
            return httpx.Response(200, content=_success_payload())

        backend = OpenAICompatibleBackend(
            _config(request_timeout_seconds=0.01),
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(LLMBackendError) as caught:
            await backend.generate(_request())
        await backend.aclose()
        self.assertEqual(caught.exception.code, LLMBackendErrorCode.REQUEST_TIMEOUT)
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(calls, 1)

    async def test_each_httpx_timeout_layer_is_sanitized_retryable_and_zero_retry(
        self,
    ) -> None:
        timeout_types = (
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        )
        for timeout_type in timeout_types:
            with self.subTest(timeout_type=timeout_type.__name__):
                calls = 0

                def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    raise timeout_type(
                        "library-private-sentinel",
                        request=request,
                    )

                backend = OpenAICompatibleBackend(
                    _config(api_key="private-sentinel"),
                    transport=httpx.MockTransport(handler),
                )
                with self.assertRaises(LLMBackendError) as caught:
                    await backend.generate(_request())
                await backend.aclose()
                self.assertEqual(
                    caught.exception.code,
                    LLMBackendErrorCode.REQUEST_TIMEOUT,
                )
                self.assertTrue(caught.exception.retryable)
                self.assertIsNone(caught.exception.http_status)
                self.assertEqual(caught.exception.args, ("REQUEST_TIMEOUT",))
                self.assertNotIn("private", repr(caught.exception.args))
                self.assertEqual(calls, 1)

    async def test_transport_receives_all_four_httpx_timeout_layers(self) -> None:
        captured: dict[str, float] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(request.extensions["timeout"])
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                content=_success_payload(),
            )

        backend = OpenAICompatibleBackend(
            _config(
                connect_timeout_seconds=1.1,
                read_timeout_seconds=2.2,
                write_timeout_seconds=3.3,
                pool_timeout_seconds=4.4,
            ),
            transport=httpx.MockTransport(handler),
        )
        await backend.generate(_request())
        await backend.aclose()
        self.assertEqual(
            captured,
            {"connect": 1.1, "read": 2.2, "write": 3.3, "pool": 4.4},
        )

    async def test_encoding_json_content_type_and_envelope_failures(self) -> None:
        cases = (
            ({"Content-Type": "application/json"}, b"\xff", LLMBackendErrorCode.RESPONSE_ENCODING),
            ({"Content-Type": "application/json; charset=latin-1"}, b"{}", LLMBackendErrorCode.RESPONSE_ENCODING),
            ({"Content-Type": "text/plain"}, _success_payload(), LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID),
            ({}, _success_payload(), LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID),
            ({"Content-Type": "application/json"}, b"not-json", LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID),
            ({"Content-Type": "application/json", "Content-Length": "invalid"}, _success_payload(), LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID),
        )
        for headers, content, expected in cases:
            def handler(_request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, headers=headers, content=content)

            backend = OpenAICompatibleBackend(
                _config(), transport=httpx.MockTransport(handler)
            )
            with self.assertRaises(LLMBackendError) as caught:
                await backend.generate(_request())
            await backend.aclose()
            self.assertEqual(caught.exception.code, expected)
            self.assertEqual(caught.exception.args, (expected.value,))

    async def test_invalid_required_envelope_values_are_rejected(self) -> None:
        invalid = (
            None,
            {},
            {"choices": []},
            {"choices": [None]},
            {"choices": [{"message": None}]},
            {"choices": [{"message": {"content": None}}]},
            {"choices": [{"message": {"content": ""}}]},
        )
        for envelope in invalid:
            def handler(_request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json=envelope)

            backend = OpenAICompatibleBackend(
                _config(), transport=httpx.MockTransport(handler)
            )
            with self.assertRaises(LLMBackendError) as caught:
                await backend.generate(_request())
            await backend.aclose()
            self.assertEqual(
                caught.exception.code,
                LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID,
            )

    async def test_cancellation_closes_in_flight_stream_and_leaves_no_request_task(self) -> None:
        stream = _BlockingStream()

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                stream=stream,
            )

        backend = OpenAICompatibleBackend(
            _config(), transport=httpx.MockTransport(handler)
        )
        task = asyncio.create_task(backend.generate(_request()))
        await asyncio.wait_for(stream.entered.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(task.done())
        self.assertTrue(stream.closed)
        await backend.aclose()

    async def test_close_is_idempotent_permanent_and_never_sends(self) -> None:
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=_success_payload())

        backend = OpenAICompatibleBackend(
            _config(), transport=httpx.MockTransport(handler)
        )
        await backend.aclose()
        await backend.aclose()
        with self.assertRaises(LLMBackendError) as caught:
            await backend.generate(_request())
        self.assertEqual(caught.exception.code, LLMBackendErrorCode.CLOSED)
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main()


def test_http_error_detail_extracts_exact_literal_from_llamacpp_body() -> None:
    encoded = b'{"error":{"type":"invalid_request_error","code":400,"message":"invalid grammar"}}'
    assert _extract_http_error_detail(encoded) == "type=invalid_request_error code=400 message=invalid grammar"
    async def exercise() -> None:
        backend = OpenAICompatibleBackend(
            _config(),
            transport=httpx.MockTransport(lambda _request: httpx.Response(400, content=encoded)),
        )
        try:
            with pytest.raises(LLMBackendError) as caught:
                await backend.generate(_request())
            error = caught.value
            assert error.backend_error_detail == "type=invalid_request_error code=400 message=invalid grammar"
            assert error.http_status == 400 and error.retryable is False
        finally:
            await backend.aclose()
    asyncio.run(exercise())


def test_http_error_detail_is_none_for_unexpected_shapes() -> None:
    for encoded in (b"", b"\xff", b"{}", b"[]", b'{"error":null}', b'{"error":{}}', b'{"x":NaN}', b'{"error":{},"error":{}}'):
        assert _extract_http_error_detail(encoded) is None


def test_http_error_detail_is_truncated_to_exact_bound() -> None:
    detail = _extract_http_error_detail(json.dumps({"error": {"message": "x" * 1000}}).encode())
    assert detail is not None and len(detail) == 256


def test_http_error_detail_replaces_control_characters() -> None:
    detail = _extract_http_error_detail(json.dumps({"error": {"message": "a\n\tb\u0000"}}).encode())
    assert detail == "message=a  b " and all(character.isprintable() for character in detail)
