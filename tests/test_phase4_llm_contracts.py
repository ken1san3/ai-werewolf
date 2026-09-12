from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import subprocess
import sys

import pytest

from ai_client.llm.config import LocalLLMSettings
from ai_client.llm.types import (
    AiAuditDecision,
    AiAuditError,
    AiAuditErrorCode,
    AiAuditRecord,
    AiAuditStatus,
    AiAuditWriterConfig,
    AuditWriteAck,
    BackendIdentity,
    DecisionValidationCode,
    GenerationSettings,
    LLMBackendError,
    LLMBackendErrorCode,
    LLMBrainConfig,
    LLMMessage,
    PromptProjection,
    LLMUsage,
    OpenAICompatibleBackendConfig,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    serialize_ai_audit,
)


def _hash_text(text: str) -> tuple[str, int]:
    encoded = text.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def _record(
    *,
    status: AiAuditStatus = AiAuditStatus.DECISION,
    response_text: str | None = '{"kind":"chat"}',
    backend_error_code: LLMBackendErrorCode | None = None,
    validation_code: DecisionValidationCode | None = None,
    decision: AiAuditDecision | None = None,
) -> AiAuditRecord:
    prompt = '{"messages":[{"content":"日本語","role":"user"}],"output_schema":{}}'
    prompt_hash, prompt_bytes = _hash_text(prompt)
    response_hash = response_bytes = None
    if response_text is not None:
        response_hash, response_bytes = _hash_text(response_text)
    if decision is None and status is AiAuditStatus.DECISION:
        decision = AiAuditDecision(kind="chat", option_id="opaque-O", text="hello")
    return AiAuditRecord(
        schema_version="aiwolf.ai-log.v1",
        recorded_at_utc="2026-09-11T01:02:03.123456Z",
        game_id="game-A",
        player_id="player-A",
        request_id="request-A",
        phase="DAY",
        day=1,
        world_version=9,
        backend=BackendIdentity(
            backend_type="openai_compatible_http",
            endpoint_origin="http://127.0.0.1:8080",
            endpoint_path="/v1/chat/completions",
            model="configured-model",
            config_fingerprint="a" * 64,
        ),
        attempt_ordinal=1,
        prompt_sha256=prompt_hash,
        prompt_bytes=prompt_bytes,
        prompt_json=prompt,
        response_sha256=response_hash,
        response_bytes=response_bytes,
        response_text=response_text,
        latency_microseconds=100,
        provider_model="provider-model" if response_text is not None else None,
        finish_reason="stop" if response_text is not None else None,
        prompt_tokens=8 if response_text is not None else None,
        completion_tokens=4 if response_text is not None else None,
        status=status,
        backend_error_code=backend_error_code,
        validation_code=validation_code,
        decision=decision,
    )


def test_contract_modules_import_standalone_without_optional_runtime() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ai_client.llm.types; import ai_client.llm.config",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_request_deep_copies_and_freezes_json_contract() -> None:
    source = {"type": "object", "nested": {"enum": ["opaque-A"]}}
    request = StructuredGenerationRequest(
        request_id="request-A",
        messages=[LLMMessage("system", "strict")],  # type: ignore[arg-type]
        output_schema=source,
    )
    source["type"] = "changed"
    assert request.output_schema["type"] == "object"
    assert request.output_schema["nested"]["enum"] == ("opaque-A",)  # type: ignore[index]
    with pytest.raises(TypeError):
        request.output_schema["new"] = 1  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        request.request_id = "changed"  # type: ignore[misc]
    assert not hasattr(request, "temperature")
    assert not hasattr(request, "max_output_tokens")


def test_prompt_projection_verifies_complete_messages_and_schema_hash() -> None:
    messages = (LLMMessage("system", "strict"), LLMMessage("user", "{}"))
    schema = {"type": "object"}
    canonical = json.dumps(
        {
            "messages": [
                {"role": "system", "content": "strict"},
                {"role": "user", "content": "{}"},
            ],
            "output_schema": schema,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    projection = PromptProjection(
        messages=messages,
        decision_schema=schema,
        canonical_input={"phase": "DAY"},
        prompt_bytes=len(canonical),
        prompt_sha256=hashlib.sha256(canonical).hexdigest(),
        included_history_records=0,
        omitted_history_records=0,
    )
    assert projection.prompt_bytes == len(canonical)
    with pytest.raises(ValueError, match="prompt hash"):
        replace(projection, prompt_bytes=len(canonical) + 1)


@pytest.mark.parametrize("role", ["", "tool", 1, None])
def test_message_rejects_invalid_roles(role: object) -> None:
    with pytest.raises(ValueError):
        LLMMessage(role, "x")  # type: ignore[arg-type]


def test_usage_and_response_exact_value_validation() -> None:
    assert LLMUsage(0, None).prompt_tokens == 0
    for value in (-1, True, 1.0, "1"):
        with pytest.raises(ValueError):
            LLMUsage(prompt_tokens=value)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        StructuredGenerationResponse("r", "", None, None, LLMUsage())
    response = StructuredGenerationResponse("r", "{}", None, None, LLMUsage())
    assert response.text == "{}"


@pytest.mark.parametrize("value", [0, 513, True, 1.5, "12"])
def test_generation_token_bounds_are_exact(value: object) -> None:
    with pytest.raises(ValueError):
        GenerationSettings(max_output_tokens=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [1, 512])
def test_generation_token_boundaries_are_accepted(value: int) -> None:
    assert GenerationSettings(max_output_tokens=value).max_output_tokens == value


@pytest.mark.parametrize("value", [-0.1, 2.1, True, float("inf"), float("nan"), "0.2"])
def test_generation_temperature_bounds_are_exact(value: object) -> None:
    with pytest.raises(ValueError):
        GenerationSettings(temperature=value)  # type: ignore[arg-type]
    assert GenerationSettings(temperature=0).temperature == 0
    assert GenerationSettings(temperature=2).temperature == 2


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:8080/v1/chat/completions",
        "http://192.168.1.5:8080/v1/chat/completions",
        "http://127.0.0.1/v1/chat/completions",
        "http://user:secret@127.0.0.1:8080/v1/chat/completions",
        "http://127.0.0.1:8080/v1/chat/completions?q=secret",
        "http://127.0.0.1:8080/v1/chat/completions#fragment",
        "http://127.0.0.1:8080/v1/chat/completions?",
        "http://127.0.0.1:8080/v1/chat/completions#",
        "http://127.0.0.1:8080/v1/chat/completions?#",
        "http://127.0.0.1:8080/v1/models",
        "http://127.0.0.1:99999/v1/chat/completions",
    ],
)
def test_backend_config_rejects_unsafe_endpoint(endpoint: str) -> None:
    with pytest.raises(ValueError, match="loopback HTTP"):
        OpenAICompatibleBackendConfig(endpoint=endpoint, model="m")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:8080/v1/chat/completions",
        "http://localhost:8080/v1/chat/completions",
        "http://[::1]:8080/v1/chat/completions",
    ],
)
def test_backend_config_accepts_only_declared_loopback_forms(endpoint: str) -> None:
    assert OpenAICompatibleBackendConfig(endpoint=endpoint, model="m").endpoint == endpoint


def test_backend_config_secret_is_not_repr_compare_or_fingerprint_input() -> None:
    first = OpenAICompatibleBackendConfig(
        endpoint="http://127.0.0.1:8080/v1/chat/completions",
        model="model-A",
        api_key="sentinel-one",
    )
    second = replace(first, api_key="sentinel-two")
    changed = replace(first, model="model-B")
    assert "sentinel" not in repr(first)
    assert first == second
    assert first.config_fingerprint == second.config_fingerprint
    assert first.config_fingerprint != changed.config_fingerprint
    assert len(first.config_fingerprint) == 64


@pytest.mark.parametrize(
    "field,value",
    [
        ("connect_timeout_seconds", 0),
        ("read_timeout_seconds", True),
        ("write_timeout_seconds", float("inf")),
        ("pool_timeout_seconds", "1"),
        ("request_timeout_seconds", -1),
        ("max_request_bytes", 1023),
        ("max_response_bytes", True),
        ("structured_mode", "auto"),
    ],
)
def test_backend_config_rejects_invalid_bounds_and_coercion(
    field: str, value: object
) -> None:
    kwargs = {
        "endpoint": "http://127.0.0.1:8080/v1/chat/completions",
        "model": "m",
        field: value,
    }
    with pytest.raises((ValueError, TypeError)):
        OpenAICompatibleBackendConfig(**kwargs)  # type: ignore[arg-type]


def test_backend_byte_lower_bound_is_accepted_for_both_directions() -> None:
    config = OpenAICompatibleBackendConfig(
        endpoint="http://127.0.0.1:8080/v1/chat/completions",
        model="m",
        max_request_bytes=1024,
        max_response_bytes=1024,
    )
    assert config.max_request_bytes == 1024
    assert config.max_response_bytes == 1024


def test_local_settings_environment_defaults_and_exact_backend_conversion() -> None:
    generation = GenerationSettings(max_output_tokens=256, temperature=0.5)
    settings = LocalLLMSettings(
        model="model-A",
        api_key="secret-A",
        generation=generation,
        structured_mode="json_object",
    )
    backend = settings.backend_config()
    assert backend.generation is generation
    assert backend.endpoint == settings.endpoint
    assert backend.model == settings.model
    assert backend.api_key == "secret-A"
    assert backend.structured_mode == "json_object"
    assert "secret-A" not in repr(settings)
    assert LocalLLMSettings.from_env({"AIWOLF_LLM_MODEL": "env-model"}).model == "env-model"
    configured = LocalLLMSettings.from_env(
        {
            "AIWOLF_LLM_ENDPOINT": "http://localhost:9000/v1/chat/completions",
            "AIWOLF_LLM_MODEL": "env-model",
            "AIWOLF_LLM_API_KEY": "env-secret",
        }
    )
    assert configured.endpoint.startswith("http://localhost:9000/")
    assert configured.api_key == "env-secret"
    assert "env-secret" not in repr(configured)
    with pytest.raises(ValueError, match="model"):
        LocalLLMSettings.from_env({})


def test_brain_config_rejects_unbounded_or_coerced_values() -> None:
    assert LLMBrainConfig(max_schema_repair_attempts=0).max_schema_repair_attempts == 0
    for field, value in (
        ("max_prompt_bytes", 1023),
        ("max_history_records", -1),
        ("max_human_text_chars", True),
        ("max_generated_text_chars", 0),
        ("max_generated_text_chars", 241),
        ("max_repair_excerpt_chars", -1),
        ("max_schema_repair_attempts", 2),
    ):
        with pytest.raises(ValueError):
            LLMBrainConfig(**{field: value})  # type: ignore[arg-type]


def test_backend_error_is_stable_and_sanitized() -> None:
    error = LLMBackendError(
        LLMBackendErrorCode.HTTP_STATUS, http_status=503, retryable=True
    )
    assert str(error) == "HTTP_STATUS"
    assert error.args == ("HTTP_STATUS",)
    assert error.http_status == 503
    with pytest.raises(ValueError):
        LLMBackendError(LLMBackendErrorCode.HTTP_STATUS)
    with pytest.raises(ValueError):
        LLMBackendError(LLMBackendErrorCode.CONNECT_FAILED, http_status=500)


@pytest.mark.parametrize("value", [0, -1, 65, True, 1.0, "1"])
def test_audit_queue_capacity_exact_bounds(value: object) -> None:
    with pytest.raises(ValueError, match=r"^queue_capacity must be an int in \[1, 64\]$"):
        AiAuditWriterConfig(queue_capacity=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [1, 64])
def test_audit_queue_capacity_boundaries_are_accepted(value: int) -> None:
    assert AiAuditWriterConfig(queue_capacity=value).queue_capacity == value


@pytest.mark.parametrize("value", [0, -1, 1_048_577, True, 1.0, "1"])
def test_audit_record_size_exact_bounds(value: object) -> None:
    with pytest.raises(
        ValueError, match=r"^max_record_bytes must be an int in \[1, 1048576\]$"
    ):
        AiAuditWriterConfig(max_record_bytes=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [1, 1_048_576])
def test_audit_record_size_boundaries_are_accepted(value: int) -> None:
    assert AiAuditWriterConfig(max_record_bytes=value).max_record_bytes == value


def test_minimum_record_bound_still_rejects_a_larger_serialized_record() -> None:
    config = AiAuditWriterConfig(max_record_bytes=1)
    with pytest.raises(AiAuditError) as caught:
        serialize_ai_audit(_record(), config.max_record_bytes)
    assert caught.value.code is AiAuditErrorCode.RECORD_INVALID


def test_audit_error_and_ack_are_exact_public_contracts() -> None:
    terminal = AiAuditErrorCode.WRITE_FAILED
    error = AiAuditError(AiAuditErrorCode.SINK_FAILED, terminal_code=terminal)
    assert error.code is AiAuditErrorCode.SINK_FAILED
    assert error.terminal_code is terminal
    assert str(error) == "SINK_FAILED"
    assert error.args == ("SINK_FAILED",)
    with pytest.raises(ValueError, match="^invalid audit terminal_code$"):
        AiAuditError(AiAuditErrorCode.SINK_FAILED)
    with pytest.raises(ValueError, match="^invalid audit terminal_code$"):
        AiAuditError(AiAuditErrorCode.CLOSED, terminal_code=terminal)
    with pytest.raises(ValueError, match="^invalid audit terminal_code$"):
        AiAuditError(
            AiAuditErrorCode.CLOSED, terminal_code=AiAuditErrorCode.CLOSED
        )
    assert AuditWriteAck(1) == AuditWriteAck(sequence=1, durable=True)
    for sequence in (0, -1, True, 1.0, "1"):
        with pytest.raises(ValueError, match="^sequence must be a positive int$"):
            AuditWriteAck(sequence)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="^durable must be True$"):
        AuditWriteAck(1, durable=False)  # type: ignore[arg-type]


def test_audit_decision_closed_shapes_and_opaque_ids() -> None:
    decisions = (
        AiAuditDecision(kind="none"),
        AiAuditDecision(kind="chat", option_id="O/Exact", text="x"),
        AiAuditDecision(kind="vote", option_id="O", vote_target_player_id=None),
        AiAuditDecision(
            kind="ability",
            option_id="O",
            ability_id="ability:Exact",
            ability_target_player_ids=["P-A", "p-a"],  # type: ignore[arg-type]
        ),
        AiAuditDecision(
            kind="co_declare", option_id="O", claimed_role_id="Role-ID", text="x"
        ),
        AiAuditDecision(
            kind="co_report",
            option_id="O",
            report_kind="K",
            report_target_player_id="P",
            claimed_result="R",
        ),
    )
    assert decisions[3].ability_target_player_ids == ("P-A", "p-a")
    with pytest.raises(ValueError, match="fields do not match"):
        AiAuditDecision(kind="none", option_id="O")
    with pytest.raises(ValueError, match="fields do not match"):
        AiAuditDecision(kind="chat", option_id="O")
    with pytest.raises(ValueError, match="at most 240"):
        AiAuditDecision(kind="chat", option_id="O", text="x" * 241)
    with pytest.raises(ValueError, match="unique"):
        AiAuditDecision(
            kind="ability",
            option_id="O",
            ability_id="A",
            ability_target_player_ids=("P", "P"),
        )


def test_audit_record_and_serialization_are_exact_and_deterministic() -> None:
    record = _record()
    first = serialize_ai_audit(record)
    second = serialize_ai_audit(record)
    assert first == second
    assert first.endswith(b"\n") and not first.endswith(b"\n\n")
    value = json.loads(first)
    assert value["schema_version"] == "aiwolf.ai-log.v1"
    assert value["status"] == "DECISION"
    assert value["decision"]["ability_target_player_ids"] == []
    assert value["backend_error_code"] is None
    assert list(value) == sorted(value)
    with pytest.raises(AiAuditError) as caught:
        serialize_ai_audit(record, max_record_bytes=len(first) - 1)
    assert caught.value.code is AiAuditErrorCode.RECORD_INVALID
    assert str(caught.value) == "RECORD_INVALID"


def test_audit_record_accepts_each_status_shape() -> None:
    _record(status=AiAuditStatus.DECISION)
    _record(
        status=AiAuditStatus.EXPLICIT_NO_DECISION,
        decision=AiAuditDecision(kind="none"),
    )
    _record(
        status=AiAuditStatus.REPAIR_SUCCEEDED,
        decision=AiAuditDecision(kind="none"),
    )
    _record(
        status=AiAuditStatus.OUTPUT_INVALID,
        validation_code=DecisionValidationCode.SCHEMA,
    )
    _record(
        status=AiAuditStatus.REPAIR_FAILED,
        validation_code=DecisionValidationCode.JSON_SYNTAX,
    )
    _record(
        status=AiAuditStatus.BACKEND_FAILED,
        response_text=None,
        backend_error_code=LLMBackendErrorCode.CONNECT_FAILED,
    )
    _record(status=AiAuditStatus.PROMPT_REJECTED, response_text=None)


def test_audit_record_rejects_hash_and_cross_field_mismatches() -> None:
    record = _record()
    for changes in (
        {"schema_version": "other"},
        {"recorded_at_utc": "2026-09-11T01:02:03+00:00"},
        {"day": True},
        {"world_version": -1},
        {"attempt_ordinal": True},
        {"prompt_bytes": record.prompt_bytes + 1},
        {"response_sha256": "A" * 64},
        {"status": AiAuditStatus.BACKEND_FAILED},
        {"status": AiAuditStatus.OUTPUT_INVALID},
        {"decision": None},
    ):
        with pytest.raises((ValueError, TypeError)):
            replace(record, **changes)


def test_serialize_preserves_unicode_and_never_contains_an_api_key_field() -> None:
    payload = serialize_ai_audit(_record())
    assert "日本語".encode() in payload
    assert b"api_key" not in payload
    assert b"authorization" not in payload.lower()
