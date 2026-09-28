from __future__ import annotations

import asyncio
import hashlib
import json
import struct
from pathlib import Path

import httpx
import pytest

from ai_client.llm.admission_client import _encode_frame, _structured_request_to_wire
from ai_client.llm.admission_broker import (
    _ProtocolViolation, _structured_request_from_wire, _structured_request_size,
    GenerationAdmissionBroker,
)
from ai_client.llm.backend import OpenAICompatibleBackend
from ai_client.llm.generation_v2 import GenerationSettingsV2, STAGES, stage_seed
from ai_client.llm.types import (
    GenerationSettings, LLMBackendError, LLMBackendErrorCode, LLMMessage,
    LlamaCppStructuredOutputConfig, OpenAICompatibleBackendConfig,
    ProviderQuiescence, StructuredGenerationRequest,
)


def request(**changes):
    args = dict(request_id="capture-stage-1", messages=(LLMMessage("user", "日本語"),),
                output_schema={"type": "object", "properties": {"z": {}, "a": {}}},
                generation_profile="phase6_v2", max_output_tokens=480, seed=123)
    args.update(changes)
    return StructuredGenerationRequest(**args)


def config(**changes):
    args = dict(endpoint="http://127.0.0.1:8080/v1/chat/completions", model="offline",
                generation=GenerationSettings(max_output_tokens=17))
    args.update(changes)
    return OpenAICompatibleBackendConfig(**args)


def body_for(req, cfg=None):
    async def run():
        backend = OpenAICompatibleBackend(cfg or config(), transport=httpx.MockTransport(
            lambda _: pytest.fail("offline payload must not send")))
        try:
            return backend._request_payload(req)
        finally:
            await backend.aclose()
    return asyncio.run(run())


@pytest.mark.parametrize("field,value", [
    ("generation_profile", "unknown"), ("generation_profile", None),
    ("max_output_tokens", None), ("max_output_tokens", 0), ("max_output_tokens", 513),
    ("max_output_tokens", True), ("max_output_tokens", 1.5),
    ("seed", None), ("seed", -1), ("seed", 2**32), ("seed", True), ("seed", 1.0),
])
def test_request_rejects_invalid_stage_settings(field, value):
    with pytest.raises(ValueError):
        request(**{field: value})


@pytest.mark.parametrize("token,seed", [(1, 0), (512, 2**32 - 1)])
def test_stage_extremes_and_wire_identity(token, seed):
    req = request(max_output_tokens=token, seed=seed)
    body = json.loads(body_for(req))
    assert list(body) == ["max_tokens", "messages", "model", "response_format", "seed", "stream", "temperature"]
    assert list(body["messages"][0]) == ["role", "content"]
    assert list(body["response_format"]) == ["type", "json_schema"]
    assert list(body["response_format"]["json_schema"]) == ["name", "schema", "strict"]
    assert list(body["response_format"]["json_schema"]["schema"]["properties"]) == ["z", "a"]
    assert (body["max_tokens"], body["seed"]) == (token, seed)
    assert "request_id" not in body and "generation_profile" not in body


def test_legacy_wire_remains_exact_and_ipc_shape_unchanged():
    req = request(generation_profile="v1", max_output_tokens=None, seed=None)
    expected = {"max_tokens": 17, "messages": [{"content": "日本語", "role": "user"}],
                "model": "offline", "response_format": {"type": "json_schema", "json_schema": {
                    "name": "aiwolf_brain_decision", "strict": True,
                    "schema": {"type": "object", "properties": {"z": {}, "a": {}}}}},
                "stream": False, "temperature": 0.2}
    assert body_for(req) == json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert set(_structured_request_to_wire(req)) == {"messages", "output_schema", "request_id"}
    with pytest.raises(ValueError):
        request(generation_profile="v1")


def test_ipc_serialization_and_broker_roundtrip_preserve_nested_order():
    req = request()
    wire = _structured_request_to_wire(req)
    frame = _encode_frame(65536, "GENERATE", structured_request=wire)
    assert struct.unpack("!I", frame[:4])[0] == len(frame) - 4
    decoded = json.loads(frame[4:])["structured_request"]
    restored = _structured_request_from_wire(decoded)
    assert body_for(restored) == body_for(req)
    assert _structured_request_size(req) == len(json.dumps(wire, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


@pytest.mark.parametrize("mutation", ["missing", "extra", "profile", "overflow"])
def test_broker_rejects_incomplete_or_unknown_extended_contract(mutation):
    value = _structured_request_to_wire(request())
    if mutation == "missing":
        del value["seed"]
    elif mutation == "extra":
        value["provider_permission"] = True
    elif mutation == "profile":
        value["generation_profile"] = "v1"
    else:
        value["max_output_tokens"] = 513
    with pytest.raises(_ProtocolViolation):
        _structured_request_from_wire(value)


@pytest.mark.parametrize("changes", [
    {"structured_mode": "json_object"},
    {"llama_cpp_structured_output": LlamaCppStructuredOutputConfig()},
])
def test_unapproved_provider_shapes_fail_closed(changes):
    with pytest.raises(LLMBackendError) as error:
        body_for(request(), config(**changes))
    assert error.value.code == LLMBackendErrorCode.ADMISSION_UNAVAILABLE
    assert error.value.provider_quiescence == ProviderQuiescence.NOT_STARTED


def test_v2_generate_cannot_reach_transport_even_with_valid_payload():
    async def run():
        calls = []
        backend = OpenAICompatibleBackend(config(), transport=httpx.MockTransport(lambda req: calls.append(req)))
        try:
            with pytest.raises(LLMBackendError) as error:
                await backend.generate(request())
            assert error.value.code == LLMBackendErrorCode.ADMISSION_UNAVAILABLE
            assert error.value.provider_quiescence == ProviderQuiescence.NOT_STARTED
            assert calls == []
        finally:
            await backend.aclose()
    asyncio.run(run())


def test_broker_cannot_start_v2_backend_task():
    class Owner:
        def _require_invocation_owner(self, connection, invocation):
            pass
    with pytest.raises(_ProtocolViolation):
        GenerationAdmissionBroker._generate_locked(Owner(), object(), "i", 1, request())


@pytest.mark.parametrize("stage", STAGES)
def test_unset_budget_and_finite_reservation(stage):
    with pytest.raises(ValueError, match="V2_BUDGET_UNSET"):
        GenerationSettingsV2().for_stage(stage)
    values = {("co" if s == "co_opportunity" else s) + "_max_output_tokens": 512 for s in STAGES}
    settings = GenerationSettingsV2(**values)
    assert settings.for_stage(stage) == 512
    assert settings.reserved_tokens(stage) == (2048 if stage == "chat_plan" else 1024)
    for invalid in (0, 513, True, 1.5, "128"):
        with pytest.raises(ValueError):
            GenerationSettingsV2(**{("co" if stage == "co_opportunity" else stage) + "_max_output_tokens": invalid})


def test_seed_known_vector_and_attempt_limits():
    expected = int.from_bytes(hashlib.sha256(b"cap\x00chat_plan\x00\x00\x00\x00\x01").digest()[:4], "big")
    assert stage_seed("cap", "chat_plan", 1) == expected
    assert stage_seed("cap", "chat_plan", 2) != expected
    for attempt in (0, 3, True, 1.0):
        with pytest.raises(ValueError):
            stage_seed("cap", "chat_plan", attempt)


def test_all_companion_schema_subtrees_survive_actual_wire():
    fixture = json.loads(Path("Docs/ai/design/PHASE6_GENERATION_CONTRACT_V2_SCHEMAS.json").read_text(encoding="utf-8"))
    schemas = fixture["schemas"]
    for schema in schemas.values():
        req = request(output_schema=schema)
        frame = _encode_frame(200000, "GENERATE", structured_request=_structured_request_to_wire(req))
        restored = _structured_request_from_wire(json.loads(frame[4:])["structured_request"])
        body = json.loads(body_for(restored, config(max_request_bytes=200000)))
        actual = body["response_format"]["json_schema"]["schema"]
        encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        assert encode(actual) == encode(schema)
