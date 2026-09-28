"""Frozen, model-neutral contracts for the Phase 4 LLM boundary.

This module deliberately performs no I/O.  It is shared by the HTTP backend,
prompt/decision layer, and durable audit sink, so validation here is strict and
all public error text is stable and safe to expose in diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import ClassVar, Literal, Mapping, TypeAlias
from urllib.parse import urlsplit

from ai_client.discussion.context import canonical_json_bytes, canonical_sha256
from ai_client.discussion.model import (
    AiDiscussionGenerationStatus,
    DiscussionCapture,
    DiscussionProposal,
)
from ai_client.discussion.transaction import AiDiscussionTerminalRecord

LLMRole: TypeAlias = Literal["system", "user", "assistant"]
AuditDecisionKind: TypeAlias = Literal[
    "none", "chat", "vote", "ability", "co_declare", "co_report"
]

_LLM_ROLES = frozenset({"system", "user", "assistant"})
_AUDIT_DECISION_KINDS = frozenset(
    {"none", "chat", "vote", "ability", "co_declare", "co_report"}
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


def _require_non_empty_string(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _require_optional_non_empty_string(name: str, value: object) -> None:
    if value is not None:
        _require_non_empty_string(name, value)


def _require_non_negative_int(name: str, value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative int")


def _require_optional_non_negative_int(name: str, value: object) -> None:
    if value is not None:
        _require_non_negative_int(name, value)


def _require_sha256(name: str, value: object) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex string")


def _freeze_json(value: object, *, name: str) -> object:
    """Copy a JSON value into recursively immutable containers."""

    if isinstance(value, Mapping):
        copied: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{name} must contain only string object keys")
            copied[key] = _freeze_json(child, name=name)
        return MappingProxyType(copied)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(child, name=name) for child in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"{name} must contain only finite JSON values")


def _freeze_non_empty_mapping(value: object, *, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"{name} must be a non-empty mapping")
    frozen = _freeze_json(value, name=name)
    assert isinstance(frozen, Mapping)
    return frozen


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(child) for child in value]
    return value


def _freeze_messages(value: object) -> tuple[LLMMessage, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError("messages must be a non-empty sequence of LLMMessage")
    try:
        messages = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError("messages must be a non-empty sequence of LLMMessage") from exc
    if not messages or any(not isinstance(message, LLMMessage) for message in messages):
        raise ValueError("messages must be a non-empty sequence of LLMMessage")
    return messages


def _validate_endpoint(endpoint: object) -> None:
    _require_non_empty_string("endpoint", endpoint)
    assert isinstance(endpoint, str)
    if (
        any(character.isspace() for character in endpoint)
        or "?" in endpoint
        or "#" in endpoint
    ):
        raise ValueError("endpoint must be a loopback HTTP /v1/chat/completions URL")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError(
            "endpoint must be a loopback HTTP /v1/chat/completions URL"
        ) from exc
    if (
        parsed.scheme.lower() != "http"
        or parsed.username is not None
        or parsed.password is not None
        or hostname is None
        or hostname.lower() not in {"127.0.0.1", "::1", "localhost"}
        or port is None
        or parsed.path != "/v1/chat/completions"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("endpoint must be a loopback HTTP /v1/chat/completions URL")


def _require_positive_number(name: str, value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be a finite positive number")


def _require_bounded_int(name: str, value: object, lower: int, upper: int) -> None:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{name} must be an int in [{lower}, {upper}]")


@dataclass(frozen=True)
class LLMMessage:
    role: LLMRole
    content: str

    def __post_init__(self) -> None:
        if self.role not in _LLM_ROLES:
            raise ValueError("role must be system, user, or assistant")
        _require_non_empty_string("content", self.content)


@dataclass(frozen=True)
class StructuredGenerationRequest:
    request_id: str
    messages: tuple[LLMMessage, ...]
    output_schema: Mapping[str, object]
    generation_profile: Literal["v1", "phase6_v2"] = "v1"
    max_output_tokens: int | None = None
    seed: int | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string("request_id", self.request_id)
        if self.generation_profile not in ("v1", "phase6_v2"):
            raise ValueError("invalid generation profile")
        if self.generation_profile == "v1":
            if self.max_output_tokens is not None or self.seed is not None:
                raise ValueError("v1 does not accept per-request generation settings")
        else:
            _require_bounded_int("max_output_tokens", self.max_output_tokens, 1, 512)
            _require_bounded_int("seed", self.seed, 0, 2**32 - 1)
        object.__setattr__(self, "messages", _freeze_messages(self.messages))
        object.__setattr__(
            self,
            "output_schema",
            _freeze_non_empty_mapping(self.output_schema, name="output_schema"),
        )


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    def __post_init__(self) -> None:
        _require_optional_non_negative_int("prompt_tokens", self.prompt_tokens)
        _require_optional_non_negative_int(
            "completion_tokens", self.completion_tokens
        )


@dataclass(frozen=True)
class ProviderTiming:
    """Optional, model-neutral provider timing normalized to microseconds."""

    prompt_tokens: int
    prompt_microseconds: int
    completion_tokens: int
    completion_microseconds: int

    def __post_init__(self) -> None:
        for name in (
            "prompt_tokens",
            "prompt_microseconds",
            "completion_tokens",
            "completion_microseconds",
        ):
            _require_non_negative_int(name, getattr(self, name))


class ProviderTimingShapeStatus(str, Enum):
    """Sanitized shape classification for an optional provider timing object."""

    ABSENT = "ABSENT"
    NOT_OBJECT = "NOT_OBJECT"
    MISSING_REQUIRED_KEY = "MISSING_REQUIRED_KEY"
    INVALID_REQUIRED_VALUE = "INVALID_REQUIRED_VALUE"
    VALID_EXACT = "VALID_EXACT"
    VALID_WITH_EXTRA = "VALID_WITH_EXTRA"


class ProviderTimingFieldState(str, Enum):
    """Value-free validity state for one fixed provider timing field."""

    NOT_OBSERVED = "NOT_OBSERVED"
    MISSING = "MISSING"
    INVALID_TYPE = "INVALID_TYPE"
    INVALID_VALUE = "INVALID_VALUE"
    VALID = "VALID"


@dataclass(frozen=True)
class ProviderTimingDiagnostic:
    """Fixed-schema, value-free observation of the provider timing shape."""

    status: ProviderTimingShapeStatus
    prompt_n: ProviderTimingFieldState
    prompt_ms: ProviderTimingFieldState
    predicted_n: ProviderTimingFieldState
    predicted_ms: ProviderTimingFieldState
    extra_key_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.status, ProviderTimingShapeStatus):
            raise TypeError("status must be ProviderTimingShapeStatus")
        for name in ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms"):
            if not isinstance(getattr(self, name), ProviderTimingFieldState):
                raise TypeError(f"{name} must be ProviderTimingFieldState")
        _require_non_negative_int("extra_key_count", self.extra_key_count)
        states = (self.prompt_n, self.prompt_ms, self.predicted_n, self.predicted_ms)
        if self.status in {
            ProviderTimingShapeStatus.ABSENT,
            ProviderTimingShapeStatus.NOT_OBJECT,
        }:
            if (
                any(state is not ProviderTimingFieldState.NOT_OBSERVED for state in states)
                or self.extra_key_count != 0
            ):
                raise ValueError("unobserved timing shapes require unobserved fields and no extras")
        elif any(state is ProviderTimingFieldState.NOT_OBSERVED for state in states):
            raise ValueError("timing objects cannot contain unobserved field states")
        elif self.status is ProviderTimingShapeStatus.MISSING_REQUIRED_KEY:
            if ProviderTimingFieldState.MISSING not in states:
                raise ValueError("missing timing status requires a missing field")
        elif self.status is ProviderTimingShapeStatus.INVALID_REQUIRED_VALUE:
            if ProviderTimingFieldState.MISSING in states or not any(
                state
                in {
                    ProviderTimingFieldState.INVALID_TYPE,
                    ProviderTimingFieldState.INVALID_VALUE,
                }
                for state in states
            ):
                raise ValueError("invalid timing status requires a present invalid field")
        elif any(state is not ProviderTimingFieldState.VALID for state in states):
            raise ValueError("valid timing status requires all fields to be valid")
        elif (
            self.status is ProviderTimingShapeStatus.VALID_EXACT
            and self.extra_key_count != 0
        ):
            raise ValueError("exact timing status forbids extra keys")
        elif (
            self.status is ProviderTimingShapeStatus.VALID_WITH_EXTRA
            and self.extra_key_count == 0
        ):
            raise ValueError("extra timing status requires at least one extra key")


@dataclass(frozen=True)
class StructuredGenerationResponse:
    request_id: str
    text: str
    provider_model: str | None
    finish_reason: str | None
    usage: LLMUsage
    provider_timing: ProviderTiming | None = None
    provider_timing_diagnostic: ProviderTimingDiagnostic | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string("request_id", self.request_id)
        _require_non_empty_string("text", self.text)
        _require_optional_non_empty_string("provider_model", self.provider_model)
        _require_optional_non_empty_string("finish_reason", self.finish_reason)
        if not isinstance(self.usage, LLMUsage):
            raise TypeError("usage must be LLMUsage")
        if self.provider_timing is not None and not isinstance(
            self.provider_timing, ProviderTiming
        ):
            raise TypeError("provider_timing must be ProviderTiming or None")
        if self.provider_timing_diagnostic is not None and not isinstance(
            self.provider_timing_diagnostic, ProviderTimingDiagnostic
        ):
            raise TypeError(
                "provider_timing_diagnostic must be ProviderTimingDiagnostic or None"
            )


@dataclass(frozen=True)
class BackendIdentity:
    backend_type: str
    endpoint_origin: str
    endpoint_path: str
    model: str
    config_fingerprint: str

    def __post_init__(self) -> None:
        for name in ("backend_type", "endpoint_origin", "endpoint_path", "model"):
            _require_non_empty_string(name, getattr(self, name))
        _require_sha256("config_fingerprint", self.config_fingerprint)


class LLMBackendErrorCode(str, Enum):
    CONNECT_FAILED = "CONNECT_FAILED"
    REQUEST_TIMEOUT = "REQUEST_TIMEOUT"
    HTTP_STATUS = "HTTP_STATUS"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    RESPONSE_ENCODING = "RESPONSE_ENCODING"
    RESPONSE_ENVELOPE_INVALID = "RESPONSE_ENVELOPE_INVALID"
    CLOSED = "CLOSED"
    ADMISSION_UNAVAILABLE = "ADMISSION_UNAVAILABLE"
    ADMISSION_OVERLOADED = "ADMISSION_OVERLOADED"
    ADMISSION_EXPIRED = "ADMISSION_EXPIRED"
    ADMISSION_POISONED = "ADMISSION_POISONED"
    ADMISSION_PROTOCOL = "ADMISSION_PROTOCOL"


class ProviderQuiescence(str, Enum):
    """What one backend terminal proves about provider-side work."""

    NOT_STARTED = "NOT_STARTED"
    PROVEN_TERMINAL = "PROVEN_TERMINAL"
    UNKNOWN = "UNKNOWN"


class LLMBackendError(RuntimeError):
    """Sanitized backend error whose message contains only its stable code."""

    def __init__(
        self,
        code: LLMBackendErrorCode,
        *,
        http_status: int | None = None,
        retryable: bool = False,
        provider_quiescence: ProviderQuiescence = ProviderQuiescence.UNKNOWN,
        backend_error_detail: str | None = None,
    ) -> None:
        if not isinstance(code, LLMBackendErrorCode):
            raise TypeError("code must be LLMBackendErrorCode")
        if http_status is not None and (
            type(http_status) is not int or not 100 <= http_status <= 599
        ):
            raise ValueError("http_status must be an HTTP status int or None")
        if (code is LLMBackendErrorCode.HTTP_STATUS) != (http_status is not None):
            raise ValueError("http_status is required only for HTTP_STATUS")
        if type(retryable) is not bool:
            raise ValueError("retryable must be a bool")
        if not isinstance(provider_quiescence, ProviderQuiescence):
            raise TypeError("provider_quiescence must be ProviderQuiescence")
        if backend_error_detail is not None and type(backend_error_detail) is not str:
            raise TypeError("backend_error_detail must be str or None")
        if backend_error_detail is not None and (
            not backend_error_detail
            or len(backend_error_detail) > 256
            or not all(character.isprintable() for character in backend_error_detail)
        ):
            raise ValueError("backend_error_detail must be a printable string in [1, 256]")
        if backend_error_detail is not None and code is not LLMBackendErrorCode.HTTP_STATUS:
            raise ValueError("backend_error_detail is allowed only for HTTP_STATUS")
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        self.provider_quiescence = provider_quiescence
        self.backend_error_detail = backend_error_detail
        RuntimeError.__init__(self, code.value)


@dataclass(frozen=True)
class GenerationSettings:
    max_output_tokens: int = 128
    temperature: float = 0.2

    def __post_init__(self) -> None:
        _require_bounded_int("max_output_tokens", self.max_output_tokens, 1, 512)
        if (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not math.isfinite(self.temperature)
            or not 0 <= self.temperature <= 2
        ):
            raise ValueError("temperature must be a finite number in [0, 2]")


@dataclass(frozen=True)
class LlamaCppStructuredOutputConfig:
    """Exact llama.cpp profile for schema-constrained Qwen reasoning output."""

    reasoning_format: Literal["deepseek"] = "deepseek"
    enable_thinking: Literal[False] = False

    def __post_init__(self) -> None:
        if (
            type(self.reasoning_format) is not str
            or self.reasoning_format != "deepseek"
        ):
            raise ValueError("reasoning_format must be deepseek")
        if (
            type(self.enable_thinking) is not bool
            or self.enable_thinking is not False
        ):
            raise ValueError("enable_thinking must be the bool False")


@dataclass(frozen=True)
class OpenAICompatibleBackendConfig:
    endpoint: str
    model: str
    api_key: str | None = field(default=None, repr=False, compare=False)
    generation: GenerationSettings = GenerationSettings()
    connect_timeout_seconds: float = 1.0
    read_timeout_seconds: float = 3.5
    write_timeout_seconds: float = 1.0
    pool_timeout_seconds: float = 1.0
    request_timeout_seconds: float = 4.0
    max_request_bytes: int = 65536
    max_response_bytes: int = 65536
    structured_mode: Literal["json_schema", "json_object"] = "json_schema"
    llama_cpp_structured_output: LlamaCppStructuredOutputConfig | None = None

    def __post_init__(self) -> None:
        _validate_endpoint(self.endpoint)
        _require_non_empty_string("model", self.model)
        _require_optional_non_empty_string("api_key", self.api_key)
        if not isinstance(self.generation, GenerationSettings):
            raise TypeError("generation must be GenerationSettings")
        for name in (
            "connect_timeout_seconds",
            "read_timeout_seconds",
            "write_timeout_seconds",
            "pool_timeout_seconds",
            "request_timeout_seconds",
        ):
            _require_positive_number(name, getattr(self, name))
        for name in ("max_request_bytes", "max_response_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value < 1024:
                raise ValueError(f"{name} must be an int >= 1024")
        if self.structured_mode not in {"json_schema", "json_object"}:
            raise ValueError("structured_mode must be json_schema or json_object")
        if self.llama_cpp_structured_output is not None and not isinstance(
            self.llama_cpp_structured_output, LlamaCppStructuredOutputConfig
        ):
            raise TypeError(
                "llama_cpp_structured_output must be "
                "LlamaCppStructuredOutputConfig or None"
            )

    @property
    def config_fingerprint(self) -> str:
        """Return a deterministic SHA-256 of every non-secret effective field."""

        value = {
            "connect_timeout_seconds": self.connect_timeout_seconds,
            "endpoint": self.endpoint,
            "generation": {
                "max_output_tokens": self.generation.max_output_tokens,
                "temperature": self.generation.temperature,
            },
            "max_request_bytes": self.max_request_bytes,
            "max_response_bytes": self.max_response_bytes,
            "model": self.model,
            "pool_timeout_seconds": self.pool_timeout_seconds,
            "read_timeout_seconds": self.read_timeout_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
            "structured_mode": self.structured_mode,
            "write_timeout_seconds": self.write_timeout_seconds,
        }
        if self.llama_cpp_structured_output is not None:
            value["llama_cpp_structured_output"] = {
                "enable_thinking": self.llama_cpp_structured_output.enable_thinking,
                "reasoning_format": self.llama_cpp_structured_output.reasoning_format,
            }
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ShortChatConfig:
    """Explicit Phase 5 short-output profile; absent means Phase 4 behavior."""

    target_min_text_tokens: Literal[5] = 5
    target_max_text_tokens: Literal[30] = 30
    max_text_chars: int = 80
    max_text_utf8_bytes: int = 96
    max_output_tokens: Literal[96] = 96

    def __post_init__(self) -> None:
        if (
            type(self.target_min_text_tokens) is not int
            or self.target_min_text_tokens != 5
        ):
            raise ValueError("target_min_text_tokens must be 5")
        if (
            type(self.target_max_text_tokens) is not int
            or self.target_max_text_tokens != 30
        ):
            raise ValueError("target_max_text_tokens must be 30")
        _require_bounded_int("max_text_chars", self.max_text_chars, 1, 80)
        _require_bounded_int(
            "max_text_utf8_bytes", self.max_text_utf8_bytes, 1, 96
        )
        if type(self.max_output_tokens) is not int or self.max_output_tokens != 96:
            raise ValueError("max_output_tokens must be 96")


@dataclass(frozen=True)
class DiscussionChatConfig:
    """Adjustable Phase 6 discussion-output profile."""

    target_min_text_tokens: int = 20
    target_max_text_tokens: int = 120
    max_text_chars: int = 200
    max_text_utf8_bytes: int = 600

    def __post_init__(self) -> None:
        _require_bounded_int(
            "target_min_text_tokens", self.target_min_text_tokens, 1, 512
        )
        _require_bounded_int(
            "target_max_text_tokens", self.target_max_text_tokens, 1, 512
        )
        _require_bounded_int("max_text_chars", self.max_text_chars, 1, 240)
        _require_bounded_int(
            "max_text_utf8_bytes", self.max_text_utf8_bytes, 1, 960
        )
        if self.target_min_text_tokens > self.target_max_text_tokens:
            raise ValueError(
                "target_min_text_tokens must not exceed target_max_text_tokens"
            )
        if self.max_text_chars > self.max_text_utf8_bytes:
            raise ValueError("max_text_chars must not exceed max_text_utf8_bytes")


ChatOutputProfile: TypeAlias = ShortChatConfig | DiscussionChatConfig


@dataclass(frozen=True)
class LLMBrainConfig:
    max_prompt_bytes: int = 32768
    max_history_records: int = 32
    max_human_text_chars: int = 512
    max_generated_text_chars: int = 240
    max_repair_excerpt_chars: int = 1024
    max_schema_repair_attempts: int = 1
    short_chat: ChatOutputProfile | None = None

    def __post_init__(self) -> None:
        if type(self.max_prompt_bytes) is not int or self.max_prompt_bytes < 1024:
            raise ValueError("max_prompt_bytes must be an int >= 1024")
        if type(self.max_history_records) is not int or self.max_history_records < 0:
            raise ValueError("max_history_records must be a non-negative int")
        if type(self.max_human_text_chars) is not int or self.max_human_text_chars < 1:
            raise ValueError("max_human_text_chars must be a positive int")
        _require_bounded_int(
            "max_generated_text_chars", self.max_generated_text_chars, 1, 240
        )
        if (
            type(self.max_repair_excerpt_chars) is not int
            or self.max_repair_excerpt_chars < 0
        ):
            raise ValueError("max_repair_excerpt_chars must be a non-negative int")
        _require_bounded_int(
            "max_schema_repair_attempts", self.max_schema_repair_attempts, 0, 1
        )
        if self.short_chat is not None and not isinstance(
            self.short_chat, (ShortChatConfig, DiscussionChatConfig)
        ):
            raise TypeError("short_chat must be ChatOutputProfile or None")


@dataclass(frozen=True)
class PromptProjection:
    messages: tuple[LLMMessage, ...]
    decision_schema: Mapping[str, object]
    canonical_input: Mapping[str, object]
    prompt_bytes: int
    prompt_sha256: str
    included_history_records: int
    omitted_history_records: int
    short_chat: ChatOutputProfile | None = None
    token_proxy_units: int | None = None
    discussion_capture: DiscussionCapture | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", _freeze_messages(self.messages))
        object.__setattr__(
            self,
            "decision_schema",
            _freeze_non_empty_mapping(self.decision_schema, name="decision_schema"),
        )
        object.__setattr__(
            self,
            "canonical_input",
            _freeze_non_empty_mapping(self.canonical_input, name="canonical_input"),
        )
        _require_non_negative_int("prompt_bytes", self.prompt_bytes)
        _require_sha256("prompt_sha256", self.prompt_sha256)
        _require_non_negative_int(
            "included_history_records", self.included_history_records
        )
        _require_non_negative_int(
            "omitted_history_records", self.omitted_history_records
        )
        if self.short_chat is not None and not isinstance(
            self.short_chat, (ShortChatConfig, DiscussionChatConfig)
        ):
            raise TypeError("short_chat must be ChatOutputProfile or None")
        if self.token_proxy_units is not None:
            _require_non_negative_int("token_proxy_units", self.token_proxy_units)
        if self.discussion_capture is not None and not isinstance(
            self.discussion_capture, DiscussionCapture
        ):
            raise TypeError("discussion_capture must be DiscussionCapture or None")
        canonical_user_json = json.dumps(
            _plain_json(self.canonical_input),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if (
            len(self.messages) < 2
            or self.messages[1].role != "user"
            or self.messages[1].content != canonical_user_json
        ):
            raise ValueError(
                "canonical_input must byte-match the emitted canonical user message"
            )
        canonical_contract = {
            "messages": [
                {"role": message.role, "content": message.content}
                for message in self.messages
            ],
            "output_schema": _plain_json(self.decision_schema),
        }
        encoded = json.dumps(
            canonical_contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if (
            len(encoded) != self.prompt_bytes
            or hashlib.sha256(encoded).hexdigest() != self.prompt_sha256
        ):
            raise ValueError("prompt hash or byte count does not match messages and schema")


class DecisionValidationCode(str, Enum):
    JSON_SYNTAX = "JSON_SYNTAX"
    JSON_DUPLICATE_KEY = "JSON_DUPLICATE_KEY"
    SCHEMA = "SCHEMA"
    OPTION_NOT_OFFERED = "OPTION_NOT_OFFERED"
    VALUE_NOT_OFFERED = "VALUE_NOT_OFFERED"
    TEXT_BOUND = "TEXT_BOUND"


@dataclass(frozen=True)
class LLMClientIdentity:
    game_id: str
    player_id: str

    def __post_init__(self) -> None:
        _require_non_empty_string("game_id", self.game_id)
        _require_non_empty_string("player_id", self.player_id)


class LLMInvocationStatus(str, Enum):
    DECISION = "DECISION"
    EXPLICIT_NO_DECISION = "EXPLICIT_NO_DECISION"
    REPAIR_SUCCEEDED = "REPAIR_SUCCEEDED"
    PROMPT_REJECTED = "PROMPT_REJECTED"
    BACKEND_FAILED = "BACKEND_FAILED"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    REPAIR_FAILED = "REPAIR_FAILED"
    AUDIT_FAILED = "AUDIT_FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class LLMBrainSnapshot:
    active: bool
    calls: int
    backend_calls: int
    decisions: int
    explicit_no_decisions: int
    repair_attempts: int
    failures: int
    cancellations: int
    audit_failures: int
    last_status: LLMInvocationStatus | None
    last_error_code: str | None

    def __post_init__(self) -> None:
        if type(self.active) is not bool:
            raise ValueError("active must be a bool")
        for name in (
            "calls",
            "backend_calls",
            "decisions",
            "explicit_no_decisions",
            "repair_attempts",
            "failures",
            "cancellations",
            "audit_failures",
        ):
            _require_non_negative_int(name, getattr(self, name))
        if self.last_status is not None and not isinstance(
            self.last_status, LLMInvocationStatus
        ):
            raise TypeError("last_status must be LLMInvocationStatus or None")
        _require_optional_non_empty_string("last_error_code", self.last_error_code)


class AiAuditStatus(str, Enum):
    PROMPT_REJECTED = "PROMPT_REJECTED"
    BACKEND_FAILED = "BACKEND_FAILED"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    DECISION = "DECISION"
    EXPLICIT_NO_DECISION = "EXPLICIT_NO_DECISION"
    REPAIR_SUCCEEDED = "REPAIR_SUCCEEDED"
    REPAIR_FAILED = "REPAIR_FAILED"


@dataclass(frozen=True)
class AiAuditDecision:
    kind: AuditDecisionKind
    option_id: str | None = None
    text: str | None = None
    vote_target_player_id: str | None = None
    ability_id: str | None = None
    ability_target_player_ids: tuple[str, ...] = ()
    claimed_role_id: str | None = None
    report_kind: str | None = None
    report_target_player_id: str | None = None
    claimed_result: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in _AUDIT_DECISION_KINDS:
            raise ValueError("invalid audit decision kind")
        if isinstance(self.ability_target_player_ids, (str, bytes)):
            raise ValueError("ability_target_player_ids must contain identifiers")
        try:
            targets = tuple(self.ability_target_player_ids)
        except TypeError as exc:
            raise ValueError(
                "ability_target_player_ids must contain identifiers"
            ) from exc
        object.__setattr__(self, "ability_target_player_ids", targets)

        for name in (
            "option_id",
            "text",
            "vote_target_player_id",
            "ability_id",
            "claimed_role_id",
            "report_kind",
            "report_target_player_id",
            "claimed_result",
        ):
            _require_optional_non_empty_string(name, getattr(self, name))
        if any(not isinstance(target, str) or not target for target in targets):
            raise ValueError("ability_target_player_ids must contain identifiers")
        if len(set(targets)) != len(targets):
            raise ValueError("ability_target_player_ids must be unique")
        if self.text is not None and len(self.text) > 240:
            raise ValueError("text must contain at most 240 characters")

        supplied = {
            "option_id": self.option_id is not None,
            "text": self.text is not None,
            "vote_target_player_id": self.vote_target_player_id is not None,
            "ability_id": self.ability_id is not None,
            "ability_target_player_ids": bool(targets),
            "claimed_role_id": self.claimed_role_id is not None,
            "report_kind": self.report_kind is not None,
            "report_target_player_id": self.report_target_player_id is not None,
            "claimed_result": self.claimed_result is not None,
        }
        allowed: dict[str, frozenset[str]] = {
            "none": frozenset(),
            "chat": frozenset({"option_id", "text"}),
            "vote": frozenset({"option_id", "vote_target_player_id"}),
            "ability": frozenset(
                {"option_id", "ability_id", "ability_target_player_ids"}
            ),
            "co_declare": frozenset({"option_id", "claimed_role_id", "text"}),
            "co_report": frozenset(
                {
                    "option_id",
                    "report_kind",
                    "report_target_player_id",
                    "claimed_result",
                }
            ),
        }
        required: dict[str, frozenset[str]] = {
            "none": frozenset(),
            "chat": frozenset({"option_id", "text"}),
            "vote": frozenset({"option_id"}),
            "ability": frozenset({"option_id", "ability_id"}),
            "co_declare": frozenset({"option_id", "claimed_role_id", "text"}),
            "co_report": frozenset(
                {
                    "option_id",
                    "report_kind",
                    "report_target_player_id",
                    "claimed_result",
                }
            ),
        }
        present = frozenset(name for name, is_present in supplied.items() if is_present)
        if not required[self.kind] <= present or not present <= allowed[self.kind]:
            raise ValueError("fields do not match audit decision kind")


@dataclass(frozen=True)
class AiAuditRecord:
    schema_version: Literal["aiwolf.ai-log.v1"]
    recorded_at_utc: str
    game_id: str
    player_id: str
    request_id: str
    phase: str | None
    day: int | None
    world_version: int
    backend: BackendIdentity
    attempt_ordinal: Literal[1, 2]
    prompt_sha256: str
    prompt_bytes: int
    prompt_json: str
    response_sha256: str | None
    response_bytes: int | None
    response_text: str | None
    latency_microseconds: int
    provider_model: str | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    status: AiAuditStatus
    backend_error_code: LLMBackendErrorCode | None
    validation_code: DecisionValidationCode | None
    decision: AiAuditDecision | None

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.ai-log.v1":
            raise ValueError("schema_version must be aiwolf.ai-log.v1")
        _validate_utc_rfc3339(self.recorded_at_utc)
        for name in ("game_id", "player_id", "request_id"):
            _require_non_empty_string(name, getattr(self, name))
        _require_optional_non_empty_string("phase", self.phase)
        _require_optional_non_negative_int("day", self.day)
        _require_non_negative_int("world_version", self.world_version)
        if not isinstance(self.backend, BackendIdentity):
            raise TypeError("backend must be BackendIdentity")
        if type(self.attempt_ordinal) is not int or self.attempt_ordinal not in {1, 2}:
            raise ValueError("attempt_ordinal must be 1 or 2")
        _require_sha256("prompt_sha256", self.prompt_sha256)
        _require_non_negative_int("prompt_bytes", self.prompt_bytes)
        _require_non_empty_string("prompt_json", self.prompt_json)
        prompt = self.prompt_json.encode("utf-8")
        if len(prompt) != self.prompt_bytes or hashlib.sha256(prompt).hexdigest() != self.prompt_sha256:
            raise ValueError("prompt hash or byte count does not match prompt_json")

        response_values = (
            self.response_sha256,
            self.response_bytes,
            self.response_text,
        )
        response_present = all(value is not None for value in response_values)
        if response_present:
            _require_sha256("response_sha256", self.response_sha256)
            _require_non_negative_int("response_bytes", self.response_bytes)
            _require_non_empty_string("response_text", self.response_text)
            assert self.response_text is not None
            response = self.response_text.encode("utf-8")
            if (
                len(response) != self.response_bytes
                or hashlib.sha256(response).hexdigest() != self.response_sha256
            ):
                raise ValueError(
                    "response hash or byte count does not match response_text"
                )
        elif any(value is not None for value in response_values):
            raise ValueError("response hash, byte count, and text must be all present or all null")

        _require_non_negative_int("latency_microseconds", self.latency_microseconds)
        _require_optional_non_empty_string("provider_model", self.provider_model)
        _require_optional_non_empty_string("finish_reason", self.finish_reason)
        _require_optional_non_negative_int("prompt_tokens", self.prompt_tokens)
        _require_optional_non_negative_int(
            "completion_tokens", self.completion_tokens
        )
        if not isinstance(self.status, AiAuditStatus):
            raise TypeError("status must be AiAuditStatus")
        if self.backend_error_code is not None and not isinstance(
            self.backend_error_code, LLMBackendErrorCode
        ):
            raise TypeError("backend_error_code must be LLMBackendErrorCode or None")
        if self.validation_code is not None and not isinstance(
            self.validation_code, DecisionValidationCode
        ):
            raise TypeError("validation_code must be DecisionValidationCode or None")
        if self.decision is not None and not isinstance(self.decision, AiAuditDecision):
            raise TypeError("decision must be AiAuditDecision or None")

        if (self.backend_error_code is not None) != (
            self.status is AiAuditStatus.BACKEND_FAILED
        ):
            raise ValueError("backend_error_code does not match status")
        if (self.validation_code is not None) != (
            self.status in {AiAuditStatus.OUTPUT_INVALID, AiAuditStatus.REPAIR_FAILED}
        ):
            raise ValueError("validation_code does not match status")
        decision_statuses = {
            AiAuditStatus.DECISION,
            AiAuditStatus.EXPLICIT_NO_DECISION,
            AiAuditStatus.REPAIR_SUCCEEDED,
        }
        if (self.decision is not None) != (self.status in decision_statuses):
            raise ValueError("decision does not match status")
        if self.status is AiAuditStatus.DECISION and self.decision is not None:
            if self.decision.kind == "none":
                raise ValueError("DECISION requires an action decision")
        if self.status is AiAuditStatus.EXPLICIT_NO_DECISION and self.decision is not None:
            if self.decision.kind != "none":
                raise ValueError("EXPLICIT_NO_DECISION requires a none decision")

        response_statuses = {
            AiAuditStatus.OUTPUT_INVALID,
            AiAuditStatus.DECISION,
            AiAuditStatus.EXPLICIT_NO_DECISION,
            AiAuditStatus.REPAIR_SUCCEEDED,
            AiAuditStatus.REPAIR_FAILED,
        }
        if response_present != (self.status in response_statuses):
            raise ValueError("response fields do not match status")
        if not response_present and any(
            value is not None
            for value in (
                self.provider_model,
                self.finish_reason,
                self.prompt_tokens,
                self.completion_tokens,
            )
        ):
            raise ValueError("provider metadata requires response text")


@dataclass(frozen=True)
class AiDiscussionGenerationRecord:
    """Durable private Phase 6 generation evidence before any state mutation."""

    _schema: ClassVar[str] = "aiwolf.ai-discussion-generation.v1"
    schema_version: Literal["aiwolf.ai-discussion-generation.v1"]
    recorded_at_utc: str
    game_id: str
    player_id: str
    request_id: str
    capture_id: str
    phase: str
    day: int
    world_version: int
    backend: BackendIdentity
    attempt_ordinal: Literal[1, 2]
    prompt_sha256: str
    prompt_bytes: int
    prompt_json: str
    response_sha256: str | None
    response_bytes: int | None
    response_text: str | None
    latency_microseconds: int
    provider_model: str | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    status: AiDiscussionGenerationStatus
    backend_error_code: LLMBackendErrorCode | None
    validation_code: DecisionValidationCode | None
    decision: AiAuditDecision | None
    context_sha256: str
    before_state_sha256: str
    after_state_sha256: Literal[None]
    base_revision: int
    proposal: DiscussionProposal | None
    proposal_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self._schema:
            raise ValueError("invalid discussion generation schema_version")
        _validate_utc_rfc3339(self.recorded_at_utc)
        for name in ("game_id", "player_id", "phase"):
            _require_non_empty_string(name, getattr(self, name))
        _require_sha256("capture_id", self.capture_id)
        if self.request_id != f"phase6:{self.capture_id}":
            raise ValueError("request_id must derive from capture_id")
        _require_non_negative_int("day", self.day)
        _require_non_negative_int("world_version", self.world_version)
        if not isinstance(self.backend, BackendIdentity):
            raise TypeError("backend must be BackendIdentity")
        if type(self.attempt_ordinal) is not int or self.attempt_ordinal not in {1, 2}:
            raise ValueError("attempt_ordinal must be 1 or 2")
        _require_sha256("prompt_sha256", self.prompt_sha256)
        _require_non_negative_int("prompt_bytes", self.prompt_bytes)
        _require_non_empty_string("prompt_json", self.prompt_json)
        prompt = self.prompt_json.encode("utf-8")
        if (
            len(prompt) != self.prompt_bytes
            or hashlib.sha256(prompt).hexdigest() != self.prompt_sha256
        ):
            raise ValueError("prompt hash or byte count does not match prompt_json")

        response_values = (
            self.response_sha256,
            self.response_bytes,
            self.response_text,
        )
        response_present = all(value is not None for value in response_values)
        if response_present:
            _require_sha256("response_sha256", self.response_sha256)
            _require_non_negative_int("response_bytes", self.response_bytes)
            _require_non_empty_string("response_text", self.response_text)
            assert self.response_text is not None
            response = self.response_text.encode("utf-8")
            if (
                len(response) != self.response_bytes
                or hashlib.sha256(response).hexdigest() != self.response_sha256
            ):
                raise ValueError(
                    "response hash or byte count does not match response_text"
                )
        elif any(value is not None for value in response_values):
            raise ValueError(
                "response hash, byte count, and text must be all present or all null"
            )

        _require_non_negative_int("latency_microseconds", self.latency_microseconds)
        _require_optional_non_empty_string("provider_model", self.provider_model)
        _require_optional_non_empty_string("finish_reason", self.finish_reason)
        _require_optional_non_negative_int("prompt_tokens", self.prompt_tokens)
        _require_optional_non_negative_int(
            "completion_tokens", self.completion_tokens
        )
        if not isinstance(self.status, AiDiscussionGenerationStatus):
            raise TypeError("status must be AiDiscussionGenerationStatus")
        if self.backend_error_code is not None and not isinstance(
            self.backend_error_code, LLMBackendErrorCode
        ):
            raise TypeError("backend_error_code must be LLMBackendErrorCode or None")
        if self.validation_code is not None and not isinstance(
            self.validation_code, DecisionValidationCode
        ):
            raise TypeError("validation_code must be DecisionValidationCode or None")
        if self.decision is not None and not isinstance(self.decision, AiAuditDecision):
            raise TypeError("decision must be AiAuditDecision or None")
        for name in ("context_sha256", "before_state_sha256"):
            _require_sha256(name, getattr(self, name))
        if self.after_state_sha256 is not None:
            raise ValueError("generation after_state_sha256 must be null")
        _require_non_negative_int("base_revision", self.base_revision)
        if self.proposal is not None and not isinstance(
            self.proposal, DiscussionProposal
        ):
            raise TypeError("proposal must be DiscussionProposal or None")
        if self.proposal is not None and self.proposal.base_revision != self.base_revision:
            raise ValueError("proposal base_revision does not match generation")

        if (self.backend_error_code is not None) != (
            self.status is AiDiscussionGenerationStatus.BACKEND_FAILED
        ):
            raise ValueError("backend_error_code does not match status")
        if (self.validation_code is not None) != (
            self.status
            in {
                AiDiscussionGenerationStatus.OUTPUT_INVALID,
                AiDiscussionGenerationStatus.REPAIR_FAILED,
            }
        ):
            raise ValueError("validation_code does not match status")
        response_statuses = {
            AiDiscussionGenerationStatus.OUTPUT_INVALID,
            AiDiscussionGenerationStatus.DECISION,
            AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
            AiDiscussionGenerationStatus.REPAIR_SUCCEEDED,
            AiDiscussionGenerationStatus.REPAIR_FAILED,
        }
        if response_present != (self.status in response_statuses):
            raise ValueError("response fields do not match status")
        if not response_present and any(
            value is not None
            for value in (
                self.provider_model,
                self.finish_reason,
                self.prompt_tokens,
                self.completion_tokens,
            )
        ):
            raise ValueError("provider metadata requires response text")

        success_statuses = {
            AiDiscussionGenerationStatus.DECISION,
            AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
            AiDiscussionGenerationStatus.REPAIR_SUCCEEDED,
        }
        trio_present = (
            self.decision is not None
            and self.proposal is not None
            and self.proposal_sha256 is not None
        )
        if trio_present != (self.status in success_statuses):
            raise ValueError("decision/proposal/digest do not match status")
        if any(
            value is not None
            for value in (self.decision, self.proposal, self.proposal_sha256)
        ) and not trio_present:
            raise ValueError("decision/proposal/digest are indivisible")
        if trio_present:
            assert self.decision is not None
            assert self.proposal is not None
            _require_sha256("proposal_sha256", self.proposal_sha256)
            if self.proposal_sha256 != canonical_sha256(self.proposal):
                raise ValueError("proposal_sha256 does not bind proposal")
            if (
                self.decision.kind != self.proposal.decision_kind
                or self.decision.option_id != self.proposal.option_id
                or self.decision.kind == "co_report"
            ):
                raise ValueError("audit decision does not match proposal identity")
            if (
                self.status is AiDiscussionGenerationStatus.DECISION
                and self.decision.kind == "none"
            ):
                raise ValueError("DECISION requires an action identity")
            if (
                self.status is AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION
                and self.decision.kind != "none"
            ):
                raise ValueError("EXPLICIT_NO_DECISION requires none/null")


class PromptRejectionCode(str, Enum):
    PROMPT_INVALID = "PROMPT_INVALID"
    PROMPT_TOO_LARGE = "PROMPT_TOO_LARGE"


@dataclass(frozen=True)
class AiDiscussionGenerationRecordV2(AiDiscussionGenerationRecord):
    """New writes carry a bounded rejection reason; v1 bytes stay readable."""

    _schema: ClassVar[str] = "aiwolf.ai-discussion-generation.v2"
    schema_version: Literal["aiwolf.ai-discussion-generation.v2"]
    prompt_rejection_code: PromptRejectionCode | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.prompt_rejection_code is not None and not isinstance(
            self.prompt_rejection_code, PromptRejectionCode
        ):
            raise TypeError("prompt_rejection_code must be PromptRejectionCode or None")
        if (self.prompt_rejection_code is not None) != (
            self.status is AiDiscussionGenerationStatus.PROMPT_REJECTED
        ):
            raise ValueError("prompt_rejection_code does not match status")


AiAuditEntry: TypeAlias = (
    AiAuditRecord | AiDiscussionGenerationRecord | AiDiscussionTerminalRecord
)


def discussion_generation_record_sha256(
    record: AiDiscussionGenerationRecord,
) -> str:
    if not isinstance(record, AiDiscussionGenerationRecord):
        raise TypeError("record must be AiDiscussionGenerationRecord")
    return canonical_sha256(record)


def _validate_utc_rfc3339(value: object) -> None:
    _require_non_empty_string("recorded_at_utc", value)
    assert isinstance(value, str)
    if _UTC_RFC3339_RE.fullmatch(value) is None:
        raise ValueError("recorded_at_utc must be normalized UTC RFC3339 with Z")
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(
            "recorded_at_utc must be normalized UTC RFC3339 with Z"
        ) from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("recorded_at_utc must be normalized UTC RFC3339 with Z")


@dataclass(frozen=True)
class AiAuditWriterConfig:
    queue_capacity: int = 16
    max_record_bytes: int = 131072

    def __post_init__(self) -> None:
        if type(self.queue_capacity) is not int or not 1 <= self.queue_capacity <= 64:
            raise ValueError("queue_capacity must be an int in [1, 64]")
        if (
            type(self.max_record_bytes) is not int
            or not 1 <= self.max_record_bytes <= 1_048_576
        ):
            raise ValueError("max_record_bytes must be an int in [1, 1048576]")


class AiAuditErrorCode(str, Enum):
    RECORD_INVALID = "RECORD_INVALID"
    NOT_STARTED = "NOT_STARTED"
    CLOSED = "CLOSED"
    OPEN_FAILED = "OPEN_FAILED"
    WRITE_FAILED = "WRITE_FAILED"
    FLUSH_FAILED = "FLUSH_FAILED"
    FSYNC_FAILED = "FSYNC_FAILED"
    CLOSE_FAILED = "CLOSE_FAILED"
    SINK_FAILED = "SINK_FAILED"


_AUDIT_TERMINAL_CODES = frozenset(
    {
        AiAuditErrorCode.OPEN_FAILED,
        AiAuditErrorCode.WRITE_FAILED,
        AiAuditErrorCode.FLUSH_FAILED,
        AiAuditErrorCode.FSYNC_FAILED,
        AiAuditErrorCode.CLOSE_FAILED,
    }
)


class AiAuditError(RuntimeError):
    def __init__(
        self,
        code: AiAuditErrorCode,
        *,
        terminal_code: AiAuditErrorCode | None = None,
    ) -> None:
        if not isinstance(code, AiAuditErrorCode):
            raise TypeError("code must be AiAuditErrorCode")
        if code is AiAuditErrorCode.SINK_FAILED:
            valid_terminal = terminal_code in _AUDIT_TERMINAL_CODES
        else:
            valid_terminal = terminal_code is None
        if not valid_terminal:
            raise ValueError("invalid audit terminal_code")
        self.code = code
        self.terminal_code = terminal_code
        RuntimeError.__init__(self, code.value)


@dataclass(frozen=True)
class AuditWriteAck:
    sequence: int
    durable: Literal[True] = True

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("sequence must be a positive int")
        if self.durable is not True:
            raise ValueError("durable must be True")


def serialize_ai_audit(
    record: AiAuditEntry,
    max_record_bytes: int = AiAuditWriterConfig().max_record_bytes,
) -> bytes:
    """Serialize one validated record to its sole deterministic queue representation."""

    if not isinstance(
        record,
        (AiAuditRecord, AiDiscussionGenerationRecord, AiDiscussionTerminalRecord),
    ):
        raise AiAuditError(AiAuditErrorCode.RECORD_INVALID)
    if type(max_record_bytes) is not int or not 1 <= max_record_bytes <= 1_048_576:
        raise AiAuditError(AiAuditErrorCode.RECORD_INVALID)

    if not isinstance(record, AiAuditRecord):
        try:
            payload = canonical_json_bytes(record) + b"\n"
        except (TypeError, ValueError, UnicodeError):
            raise AiAuditError(AiAuditErrorCode.RECORD_INVALID) from None
        effective_limit = min(
            max_record_bytes,
            16 * 1024
            if isinstance(record, AiDiscussionTerminalRecord)
            else max_record_bytes,
        )
        if len(payload) > effective_limit:
            raise AiAuditError(AiAuditErrorCode.RECORD_INVALID)
        return payload

    decision = None
    if record.decision is not None:
        decision = {
            "ability_id": record.decision.ability_id,
            "ability_target_player_ids": list(
                record.decision.ability_target_player_ids
            ),
            "claimed_result": record.decision.claimed_result,
            "claimed_role_id": record.decision.claimed_role_id,
            "kind": record.decision.kind,
            "option_id": record.decision.option_id,
            "report_kind": record.decision.report_kind,
            "report_target_player_id": record.decision.report_target_player_id,
            "text": record.decision.text,
            "vote_target_player_id": record.decision.vote_target_player_id,
        }
    value = {
        "attempt_ordinal": record.attempt_ordinal,
        "backend": {
            "backend_type": record.backend.backend_type,
            "config_fingerprint": record.backend.config_fingerprint,
            "endpoint_origin": record.backend.endpoint_origin,
            "endpoint_path": record.backend.endpoint_path,
            "model": record.backend.model,
        },
        "backend_error_code": (
            None
            if record.backend_error_code is None
            else record.backend_error_code.value
        ),
        "completion_tokens": record.completion_tokens,
        "day": record.day,
        "decision": decision,
        "finish_reason": record.finish_reason,
        "game_id": record.game_id,
        "latency_microseconds": record.latency_microseconds,
        "phase": record.phase,
        "player_id": record.player_id,
        "prompt_bytes": record.prompt_bytes,
        "prompt_json": record.prompt_json,
        "prompt_sha256": record.prompt_sha256,
        "prompt_tokens": record.prompt_tokens,
        "provider_model": record.provider_model,
        "recorded_at_utc": record.recorded_at_utc,
        "request_id": record.request_id,
        "response_bytes": record.response_bytes,
        "response_sha256": record.response_sha256,
        "response_text": record.response_text,
        "schema_version": record.schema_version,
        "status": record.status.value,
        "validation_code": (
            None if record.validation_code is None else record.validation_code.value
        ),
        "world_version": record.world_version,
    }
    try:
        payload = (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeError):
        raise AiAuditError(AiAuditErrorCode.RECORD_INVALID) from None
    if len(payload) > max_record_bytes:
        raise AiAuditError(AiAuditErrorCode.RECORD_INVALID)
    return payload
