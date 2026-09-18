"""Bounded OpenAI-compatible HTTP backend for the Phase 4 Brain boundary."""

from __future__ import annotations

import asyncio
from ai_client._compat import await_with_timeout
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import math
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx

from .types import (
    BackendIdentity,
    LLMBackendError,
    LLMBackendErrorCode,
    LLMUsage,
    OpenAICompatibleBackendConfig,
    ProviderQuiescence,
    ProviderTiming,
    ProviderTimingDiagnostic,
    ProviderTimingFieldState,
    ProviderTimingShapeStatus,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)


@runtime_checkable
class StructuredLLMBackend(Protocol):
    """Model-neutral structured generation resource."""

    @property
    def identity(self) -> BackendIdentity: ...

    async def generate(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse: ...

    async def aclose(self) -> None: ...


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(child) for child in value]
    return value


def _backend_error(
    code: LLMBackendErrorCode,
    *,
    http_status: int | None = None,
    retryable: bool = False,
    provider_quiescence: ProviderQuiescence = ProviderQuiescence.UNKNOWN,
    backend_error_detail: str | None = None,
) -> LLMBackendError:
    return LLMBackendError(
        code,
        http_status=http_status,
        retryable=retryable,
        provider_quiescence=provider_quiescence,
        backend_error_detail=backend_error_detail,
    )


def _extract_http_error_detail(encoded: bytes) -> str | None:
    """Extract a bounded diagnostic from one fully drained HTTP error body."""
    if not isinstance(encoded, bytes):
        raise TypeError("encoded must be bytes")

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            encoded.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("error"), dict):
        return None
    error = value["error"]
    parts: list[str] = []
    for key in ("type", "code", "message"):
        item = error.get(key)
        if key == "code":
            valid = (type(item) is int) or (isinstance(item, str) and bool(item))
        else:
            valid = isinstance(item, str) and bool(item)
        if valid:
            cleaned = "".join(character if character.isprintable() else " " for character in str(item))
            parts.append(f"{key}={cleaned}")
    detail = " ".join(parts)[:256]
    return detail or None


def _is_retryable_status(status: int) -> bool:
    return status in {408, 425, 429} or 500 <= status <= 599


def _optional_non_empty_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_token_count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


_INVALID_JSON_NUMBER = object()
_TIMING_KEYS = frozenset(
    {"prompt_n", "prompt_ms", "predicted_n", "predicted_ms"}
)


def _invalid_json_constant(_value: str) -> object:
    return _INVALID_JSON_NUMBER


def _contains_invalid_json_number(value: object) -> bool:
    if value is _INVALID_JSON_NUMBER:
        return True
    if isinstance(value, float) and not math.isfinite(value):
        return True
    if isinstance(value, Mapping):
        return any(_contains_invalid_json_number(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_invalid_json_number(child) for child in value)
    return False


def _milliseconds_to_microseconds(value: object) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        return None
    if isinstance(value, int):
        return value * 1000
    try:
        converted = (Decimal(str(value)) * Decimal(1000)).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError):
        return None
    return int(converted)


def _token_field_state(value: object) -> ProviderTimingFieldState:
    if type(value) is not int:
        return ProviderTimingFieldState.INVALID_TYPE
    if value < 0:
        return ProviderTimingFieldState.INVALID_VALUE
    return ProviderTimingFieldState.VALID


def _duration_field_state(value: object) -> ProviderTimingFieldState:
    if value is _INVALID_JSON_NUMBER:
        return ProviderTimingFieldState.INVALID_VALUE
    if type(value) not in {int, float}:
        return ProviderTimingFieldState.INVALID_TYPE
    if _milliseconds_to_microseconds(value) is None:
        return ProviderTimingFieldState.INVALID_VALUE
    return ProviderTimingFieldState.VALID


def _provider_timing_observation(
    envelope: Mapping[object, object],
) -> tuple[ProviderTiming | None, ProviderTimingDiagnostic]:
    not_observed = ProviderTimingFieldState.NOT_OBSERVED
    if "timings" not in envelope:
        return None, ProviderTimingDiagnostic(
            ProviderTimingShapeStatus.ABSENT,
            not_observed,
            not_observed,
            not_observed,
            not_observed,
            0,
        )
    value = envelope["timings"]
    if not isinstance(value, Mapping):
        return None, ProviderTimingDiagnostic(
            ProviderTimingShapeStatus.NOT_OBJECT,
            not_observed,
            not_observed,
            not_observed,
            not_observed,
            0,
        )

    states: dict[str, ProviderTimingFieldState] = {}
    for key in _TIMING_KEYS:
        if key not in value:
            states[key] = ProviderTimingFieldState.MISSING
        elif key in {"prompt_n", "predicted_n"}:
            states[key] = _token_field_state(value[key])
        else:
            states[key] = _duration_field_state(value[key])
    extra_key_count = sum(key not in _TIMING_KEYS for key in value)
    if ProviderTimingFieldState.MISSING in states.values():
        status = ProviderTimingShapeStatus.MISSING_REQUIRED_KEY
    elif any(state is not ProviderTimingFieldState.VALID for state in states.values()):
        status = ProviderTimingShapeStatus.INVALID_REQUIRED_VALUE
    elif extra_key_count:
        status = ProviderTimingShapeStatus.VALID_WITH_EXTRA
    else:
        status = ProviderTimingShapeStatus.VALID_EXACT
    diagnostic = ProviderTimingDiagnostic(
        status=status,
        prompt_n=states["prompt_n"],
        prompt_ms=states["prompt_ms"],
        predicted_n=states["predicted_n"],
        predicted_ms=states["predicted_ms"],
        extra_key_count=extra_key_count,
    )
    if status not in {
        ProviderTimingShapeStatus.VALID_EXACT,
        ProviderTimingShapeStatus.VALID_WITH_EXTRA,
    }:
        return None, diagnostic

    prompt_tokens = value["prompt_n"]
    completion_tokens = value["predicted_n"]
    prompt_microseconds = _milliseconds_to_microseconds(value["prompt_ms"])
    completion_microseconds = _milliseconds_to_microseconds(value["predicted_ms"])
    assert type(prompt_tokens) is int
    assert type(completion_tokens) is int
    assert prompt_microseconds is not None
    assert completion_microseconds is not None
    return ProviderTiming(
        prompt_tokens=prompt_tokens,
        prompt_microseconds=prompt_microseconds,
        completion_tokens=completion_tokens,
        completion_microseconds=completion_microseconds,
    ), diagnostic


class OpenAICompatibleBackend:
    """One-client, zero-retry adapter for a loopback chat-completions endpoint."""

    def __init__(
        self,
        config: OpenAICompatibleBackendConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not isinstance(config, OpenAICompatibleBackendConfig):
            raise TypeError("config must be OpenAICompatibleBackendConfig")
        self._config = config
        from ai_client.game_time import GameTime
        self._game_time = GameTime.from_env()
        parsed = urlsplit(config.endpoint)
        self._identity = BackendIdentity(
            backend_type="openai_compatible_http",
            endpoint_origin=f"{parsed.scheme.lower()}://{parsed.netloc}",
            endpoint_path=parsed.path,
            model=config.model,
            config_fingerprint=config.config_fingerprint,
        )
        timeout = httpx.Timeout(
            connect=config.connect_timeout_seconds,
            read=config.read_timeout_seconds,
            write=config.write_timeout_seconds,
            pool=config.pool_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._closed = False
        self._close_lock = asyncio.Lock()

    @property
    def identity(self) -> BackendIdentity:
        return self._identity

    def _request_payload(self, request: StructuredGenerationRequest) -> bytes:
        if self._config.structured_mode == "json_schema":
            response_format: dict[str, object] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "aiwolf_brain_decision",
                    "strict": True,
                    "schema": _plain_json(request.output_schema),
                },
            }
        else:
            response_format = {"type": "json_object"}

        body = {
            "max_tokens": self._config.generation.max_output_tokens,
            "messages": [
                {"content": message.content, "role": message.role}
                for message in request.messages
            ],
            "model": self._config.model,
            "response_format": response_format,
            "stream": False,
            "temperature": self._config.generation.temperature,
        }
        if self._config.llama_cpp_structured_output is not None:
            profile = self._config.llama_cpp_structured_output
            body["reasoning_format"] = profile.reasoning_format
            body["chat_template_kwargs"] = {
                "enable_thinking": profile.enable_thinking,
            }
        try:
            payload = json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except UnicodeError:
            raise _backend_error(
                LLMBackendErrorCode.RESPONSE_ENCODING,
                provider_quiescence=ProviderQuiescence.NOT_STARTED,
            ) from None
        except (TypeError, ValueError):
            raise _backend_error(
                LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID,
                provider_quiescence=ProviderQuiescence.NOT_STARTED,
            ) from None
        if len(payload) > self._config.max_request_bytes:
            raise _backend_error(
                LLMBackendErrorCode.RESPONSE_TOO_LARGE,
                provider_quiescence=ProviderQuiescence.NOT_STARTED,
            )
        return payload

    async def generate(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse:
        if not isinstance(request, StructuredGenerationRequest):
            raise TypeError("request must be StructuredGenerationRequest")
        if self._closed or self._client.is_closed:
            raise _backend_error(
                LLMBackendErrorCode.CLOSED,
                provider_quiescence=ProviderQuiescence.NOT_STARTED,
            )

        payload = self._request_payload(request)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._config.api_key is not None:
            headers["Authorization"] = f"Bearer {self._config.api_key}"

        try:
            async def _read_response():
                async with self._client.stream(
                    "POST",
                    self._config.endpoint,
                    content=payload,
                    headers=headers,
                ) as response:
                    invalid_content_length = False
                    try:
                        declared_length = self._parse_content_length(
                            response.headers.get("content-length")
                        )
                    except LLMBackendError:
                        invalid_content_length = True
                        declared_length = None
                    if (
                        declared_length is not None
                        and declared_length > self._config.max_response_bytes
                    ):
                        raise _backend_error(LLMBackendErrorCode.RESPONSE_TOO_LARGE)

                    chunks: list[bytes] = []
                    accumulated = 0
                    async for chunk in response.aiter_bytes():
                        accumulated += len(chunk)
                        if accumulated > self._config.max_response_bytes:
                            raise _backend_error(
                                LLMBackendErrorCode.RESPONSE_TOO_LARGE
                            )
                        chunks.append(chunk)
                    encoded = b"".join(chunks)

                    # Only a fully drained response can prove provider terminality.
                    if not 200 <= response.status_code <= 299:
                        raise _backend_error(
                            LLMBackendErrorCode.HTTP_STATUS,
                            http_status=response.status_code,
                            retryable=_is_retryable_status(response.status_code),
                            provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
                            backend_error_detail=_extract_http_error_detail(encoded),
                        )
                    if invalid_content_length:
                        raise _backend_error(
                            LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID,
                            provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
                        )
                    self._validate_content_type(response.headers.get("content-type"))
                return encoded
            encoded = await await_with_timeout(self._game_time.real_budget(self._config.request_timeout_seconds), _read_response)
        except asyncio.CancelledError:
            raise
        except LLMBackendError:
            raise
        except (httpx.ConnectTimeout, httpx.PoolTimeout):
            raise _backend_error(
                LLMBackendErrorCode.REQUEST_TIMEOUT,
                retryable=True,
                provider_quiescence=ProviderQuiescence.NOT_STARTED,
            ) from None
        except (TimeoutError, httpx.TimeoutException):
            raise _backend_error(
                LLMBackendErrorCode.REQUEST_TIMEOUT,
                retryable=True,
                provider_quiescence=ProviderQuiescence.UNKNOWN,
            ) from None
        except httpx.DecodingError:
            raise _backend_error(
                LLMBackendErrorCode.RESPONSE_ENCODING,
                provider_quiescence=ProviderQuiescence.UNKNOWN,
            ) from None
        except httpx.ConnectError:
            raise _backend_error(
                LLMBackendErrorCode.CONNECT_FAILED,
                retryable=True,
                provider_quiescence=ProviderQuiescence.NOT_STARTED,
            ) from None
        except httpx.RequestError:
            raise _backend_error(
                LLMBackendErrorCode.CONNECT_FAILED,
                retryable=True,
                provider_quiescence=ProviderQuiescence.UNKNOWN,
            ) from None

        return self._parse_response(request.request_id, encoded)

    @staticmethod
    def _validate_content_type(value: str | None) -> None:
        if value is None:
            raise _backend_error(
                LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID,
                provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
            )
        segments = [segment.strip() for segment in value.split(";")]
        media_type = segments[0].lower()
        if media_type != "application/json" and not (
            media_type.startswith("application/") and media_type.endswith("+json")
        ):
            raise _backend_error(
                LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID,
                provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
            )
        for parameter in segments[1:]:
            name, separator, raw_value = parameter.partition("=")
            if name.strip().lower() != "charset":
                continue
            if not separator:
                raise _backend_error(
                    LLMBackendErrorCode.RESPONSE_ENCODING,
                    provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
                )
            charset = raw_value.strip().strip('"').lower()
            if charset not in {"utf-8", "utf8"}:
                raise _backend_error(
                    LLMBackendErrorCode.RESPONSE_ENCODING,
                    provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
                )

    @staticmethod
    def _parse_content_length(value: str | None) -> int | None:
        if value is None:
            return None
        if not value.isascii() or not value.isdecimal():
            raise _backend_error(LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID)
        return int(value)

    @staticmethod
    def _parse_response(
        request_id: str, encoded: bytes
    ) -> StructuredGenerationResponse:
        try:
            text = encoded.decode("utf-8")
        except UnicodeDecodeError:
            raise _backend_error(
                LLMBackendErrorCode.RESPONSE_ENCODING,
                provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
            ) from None
        try:
            envelope = json.loads(text, parse_constant=_invalid_json_constant)
        except (json.JSONDecodeError, RecursionError, ValueError):
            raise _backend_error(
                LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID,
                provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
            ) from None
        if not isinstance(envelope, Mapping):
            raise _backend_error(
                LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID,
                provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
            )
        non_timing_envelope = {
            key: value for key, value in envelope.items() if key != "timings"
        }
        if _contains_invalid_json_number(non_timing_envelope):
            raise _backend_error(
                LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID,
                provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
            )
        choices = envelope.get("choices")
        if not isinstance(choices, list) or not choices:
            raise _backend_error(
                LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID,
                provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
            )
        first_choice = choices[0]
        if not isinstance(first_choice, Mapping):
            raise _backend_error(
                LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID,
                provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
            )
        message = first_choice.get("message")
        if not isinstance(message, Mapping):
            raise _backend_error(
                LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID,
                provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
            )
        content = message.get("content")
        if not isinstance(content, str) or not content:
            raise _backend_error(
                LLMBackendErrorCode.RESPONSE_ENVELOPE_INVALID,
                provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
            )

        usage = envelope.get("usage")
        if isinstance(usage, Mapping):
            prompt_tokens = _optional_token_count(usage.get("prompt_tokens"))
            completion_tokens = _optional_token_count(
                usage.get("completion_tokens")
            )
        else:
            prompt_tokens = completion_tokens = None
        provider_timing, provider_timing_diagnostic = _provider_timing_observation(
            envelope
        )
        return StructuredGenerationResponse(
            request_id=request_id,
            text=content,
            provider_model=_optional_non_empty_string(envelope.get("model")),
            finish_reason=_optional_non_empty_string(
                first_choice.get("finish_reason")
            ),
            usage=LLMUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
            provider_timing=provider_timing,
            provider_timing_diagnostic=provider_timing_diagnostic,
        )

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                await self._client.aclose()
            except asyncio.CancelledError:
                raise
            except Exception:
                raise _backend_error(
                    LLMBackendErrorCode.CONNECT_FAILED,
                    retryable=True,
                ) from None
