"""Validated environment configuration for the local Phase 4 LLM."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Literal, Mapping

from .types import (
    AiAuditWriterConfig,
    GenerationSettings,
    LlamaCppStructuredOutputConfig,
    LLMBrainConfig,
    OpenAICompatibleBackendConfig,
    _validate_endpoint,
    _require_non_empty_string,
    _require_optional_non_empty_string,
    _require_positive_number,
)


@dataclass(frozen=True)
class LocalLLMSettings:
    endpoint: str = "http://127.0.0.1:8080/v1/chat/completions"
    model: str = ""
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
    brain: LLMBrainConfig = LLMBrainConfig()
    audit: AiAuditWriterConfig = AiAuditWriterConfig()

    def __post_init__(self) -> None:
        _validate_endpoint(self.endpoint)
        _require_non_empty_string("model", self.model)
        _require_optional_non_empty_string("api_key", self.api_key)
        if not isinstance(self.generation, GenerationSettings):
            raise TypeError("generation must be GenerationSettings")
        if not isinstance(self.brain, LLMBrainConfig):
            raise TypeError("brain must be LLMBrainConfig")
        if not isinstance(self.audit, AiAuditWriterConfig):
            raise TypeError("audit must be AiAuditWriterConfig")
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

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] = os.environ
    ) -> LocalLLMSettings:
        if not isinstance(environ, Mapping):
            raise TypeError("environ must be a mapping")
        endpoint = environ.get(
            "AIWOLF_LLM_ENDPOINT",
            "http://127.0.0.1:8080/v1/chat/completions",
        )
        model = environ.get("AIWOLF_LLM_MODEL", "")
        api_key = environ.get("AIWOLF_LLM_API_KEY")
        return cls(endpoint=endpoint, model=model, api_key=api_key)

    def backend_config(self) -> OpenAICompatibleBackendConfig:
        return OpenAICompatibleBackendConfig(
            endpoint=self.endpoint,
            model=self.model,
            api_key=self.api_key,
            generation=self.generation,
            connect_timeout_seconds=self.connect_timeout_seconds,
            read_timeout_seconds=self.read_timeout_seconds,
            write_timeout_seconds=self.write_timeout_seconds,
            pool_timeout_seconds=self.pool_timeout_seconds,
            request_timeout_seconds=self.request_timeout_seconds,
            max_request_bytes=self.max_request_bytes,
            max_response_bytes=self.max_response_bytes,
            structured_mode=self.structured_mode,
            llama_cpp_structured_output=self.llama_cpp_structured_output,
        )
