"""T534: PF3 grammar-domain token path proof runner.

The module is test-only.  It never imports or starts a provider.  A real proof run is
permitted only through the independently reviewed CLI configuration and a separately
owned Tester task.  Unit tests use the pure validators and an injected synthetic child.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from contextlib import ExitStack
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import struct
import subprocess
import sys
import threading
import time
from typing import Any, Iterable, Mapping, Protocol, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import phase6_pf3_counterexample as t527


ROOT = Path(__file__).resolve().parents[1]
DESIGN_SHA256 = "097637b951cab10c75d16bdc5fcc3891e5e6560a1e32463755cdb56e51f5683b"
BUILD_IDENTITY = "10697/093adb242"
SOURCE_REVISION = "093adb242e6d205d06979a390d4f4f690dd87bf1"
SOURCE_ARCHIVE_SHA256 = "a4106fcef27497924fdf5cc1f511c8ccc80e5edaa24e8dd0161cc35eaaf743c0"
SOURCE_ARCHIVE_SIZE = 39_072_439
SOURCE_ROOT_PREFIX = f"llama.cpp-{SOURCE_REVISION}"
EXECUTION_BUNDLE_SHA256 = "4aecedb67096e5c0ca7f48563053a9aa9f2236c1dd6b7f304b102093618d7107"
CANDIDATE_ID = "MESSAGE.CONTROL_NUL.COMPACT"
COMPANION_SCHEMA = ROOT / "Docs/ai/design/PHASE6_GENERATION_CONTRACT_V2_SCHEMAS.json"
HARD_CAP = 512
SCHEMA_LIMIT = 2 * 1024 * 1024
RAW_LIMIT = 16 * 1024
GRAMMAR_LIMIT = 8 * 1024 * 1024
TOKEN_LIMIT = 4096
VOCAB_LIMIT = 262_144
EOG_LIMIT = 256
DETOKENIZED_LIMIT = 64 * 1024
MODULE_LIMIT = 512
MODULE_EVENT_LIMIT = 1024
PRIVATE_EVIDENCE_LIMIT = 32 * 1024 * 1024
CHILD_COMMIT_LIMIT = 2 * 1024 * 1024 * 1024
CHILD_TIMEOUT_SECONDS = 90.0
CHILD_CLEANUP_RESERVE_SECONDS = 20.0
RUN_TIMEOUT_SECONDS = 180.0

APPROVED_HASHES = {
    "model": "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8",
    "llama.dll": "2b84a13dc35361309a4bb9745853b0950c1e8b10a7c2d5e4c7e419b0ab9819a6",
    "llama-common.dll": "32079d938fe545c1d9801b0935cda8468c105cba27795f9ba0ffeddafd2d9ac9",
    "ggml.dll": "097c276b838facce28c3eb6fe3b9657c7ba1e0375e7578f5cb1d2a38da704228",
    "ggml-base.dll": "091def1bb64cb6e8b50118d7e0219dbe79a220998ac3efc5de1a260b6d0cbefc",
    "libomp.dll": "a12116ba72d1d6820407cf30be23da04ce79d6bb8a71a5ee71759c5a1faa6f1c",
    "archive": "1011cb18e52b2a8b0548eed8242299f85f57862fac237c0d258dadbb1ec4fdbc",
    "llama.h": "3d1b18eda626c1b9ecf5bda0798e65b974a8f70f30d56726610633a980cb4160",
    "converter": "4d58b73438e97ac4e2d11dbb0309702d6899088d3c44a75a2dcb9c57697bb288",
    "chat": "abab0d03a111bd198693a518c7cdd07a33326a6a6685cc182a88d4fdae5f61dc",
    "parser": "bd3d8ab38b62f0f1ba19afe7a00b93de48a17ce6e063432a1ce7a477d3fcf0b7",
    "accounting": "c8e4d0095d5c87c17e0e1356574fcaf753f9c04d4a83f4df4778ab4a241f9134",
    "config": "43e509956d96492cace8393bdc3fd598ab2418315ccdfd82e144b876241f3bab",
    "sampler": "f23f8d663932bfd73abac1bd5e99055fc6efb07e21ef27dd7e25af2f2da40bff",
    "schema": "8d3c4be2e2c3c1565afcdbaa73c93a65252f39919a05ae59307b4dbda2b206d5",
}

PRODUCT_CERTIFICATE = {
    "schema_version": "aiwolf.pf3-product-source-certificate.v1",
    "source_hashes": {
        "chat": APPROVED_HASHES["chat"],
        "server_request_parser": APPROVED_HASHES["parser"],
        "server_generation_accounting": APPROVED_HASHES["accounting"],
        "deployment_server_config": APPROVED_HASHES["config"],
        "common_sampler": APPROVED_HASHES["sampler"],
        "generation_contract_schema": APPROVED_HASHES["schema"],
    },
    "closed_facts": {
        "response_type": "json_schema",
        "strict": True,
        "max_tokens": HARD_CAP,
        "n_predict_override": False,
        "grammar_type": "JSON_SCHEMA_GBNF",
        "grammar_root": "root",
        "ignore_eos": False,
        "request_logit_bias_count": 0,
        "base_logit_bias_count": 0,
        "eog_bias_count": 0,
        "grammar_lazy": False,
        "grammar_trigger_count": 0,
        "preserved_token_count": 0,
        "stop_count": 0,
        "prefill_prefix_count": 0,
        "alternate_grammar_count": 0,
        "unknown_hard_mask_count": 0,
        "sample_accounted_before_eog_check": True,
        "limit_stops_before_next_sample": True,
        "relative_sampler_is_not_full_vocab_selectability_proof": True,
        "proof_claim": "GRAMMAR_PATH_HARD_MASK_CLEAR",
    },
}

ABI_KEYS = {
    "llama_model_params_size", "llama_model_params_vocab_only_offset",
    "llama_model_params_n_gpu_layers_offset", "llama_model_params_devices_offset",
    "llama_token_data_size", "llama_token_data_id_offset", "llama_token_data_logit_offset",
    "llama_token_data_p_offset", "llama_token_data_array_size", "llama_token_data_array_data_offset",
    "llama_token_data_array_count_offset", "llama_token_data_array_selected_offset",
    "llama_token_data_array_sorted_offset",
}


class ProofError(RuntimeError):
    def __init__(self, aggregate: str, detail: str):
        super().__init__(detail)
        self.aggregate = aggregate
        self.detail = detail


def failure_observation(error: BaseException) -> dict[str, object]:
    """Closed metadata only; exception text can contain private paths or input."""
    known = {"control timeout", "child deadline", "child start deadline", "spawn deadline",
             "child timeout", "debug owner", "child exit", "control EOF", "ready protocol",
             "stream overflow", "stream read", "reader cleanup", "owned child cleanup",
             "owned child exit unconfirmed", "unhandled child exception", "child RIP event"}
    return {"aggregate": error.aggregate if isinstance(error, ProofError) else "UNKNOWN_RUNTIME_INVALID",
            "code": error.detail if isinstance(error, ProofError) and error.detail in known else "UNCLASSIFIED",
            "exception_kind": "ProofError" if isinstance(error, ProofError) else "OSError" if isinstance(error, OSError) else "OTHER",
            "win32_error": (getattr(error, "winerror", None) or error.errno) if isinstance(error, OSError) else None}


class State(str, Enum):
    NEW = "NEW"
    CLAIMED = "CLAIMED"
    INPUT_BOUND = "INPUT_BOUND"
    TOOL_BOUND = "TOOL_BOUND"
    CHILD_STARTED = "CHILD_STARTED"
    NATIVE_LOADED = "NATIVE_LOADED"
    MODULE_PRE_VERIFIED = "MODULE_PRE_VERIFIED"
    GRAMMAR_EMITTED = "GRAMMAR_EMITTED"
    SAMPLER_CONFIG_VERIFIED = "SAMPLER_CONFIG_VERIFIED"
    PREFIX_VERIFIED = "PREFIX_VERIFIED"
    ROUNDTRIP_VERIFIED = "ROUNDTRIP_VERIFIED"
    EOG_VERIFIED = "EOG_VERIFIED"
    ACCOUNTING_VERIFIED = "ACCOUNTING_VERIFIED"
    MODULE_POST_VERIFIED = "MODULE_POST_VERIFIED"
    CHILD_REAPED = "CHILD_REAPED"
    PRIVATE_SEALED = "PRIVATE_SEALED"
    FAIL_BUDGET_PUBLISHED = "FAIL_BUDGET_PUBLISHED"
    UNKNOWN_SEALED = "UNKNOWN_SEALED"


ORDER = tuple(State)


@dataclass
class StateMachine:
    state: State = State.NEW
    history: list[str] = field(default_factory=lambda: [State.NEW.value])
    failed: str | None = None

    def advance(self, target: State) -> None:
        if self.failed is not None:
            raise ProofError("UNKNOWN_RUNTIME_INVALID", "state advance after failure")
        if self.state in {State.FAIL_BUDGET_PUBLISHED, State.UNKNOWN_SEALED}:
            raise ProofError("UNKNOWN_RUNTIME_INVALID", "terminal state")
        if ORDER.index(target) != ORDER.index(self.state) + 1:
            raise ProofError("UNKNOWN_RUNTIME_INVALID", "non-canonical state transition")
        self.state = target
        self.history.append(target.value)

    def fail(self, aggregate: str) -> None:
        if self.failed is None:
            self.failed = aggregate

    def seal_unknown(self) -> None:
        if self.state not in {State.UNKNOWN_SEALED, State.FAIL_BUDGET_PUBLISHED}:
            self.state = State.UNKNOWN_SEALED
            self.history.append(State.UNKNOWN_SEALED.value)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def product_certificate_bytes() -> bytes:
    return canonical_bytes(PRODUCT_CERTIFICATE) + b"\n"


def product_certificate_sha256() -> str:
    return digest(product_certificate_bytes())


def strict_json(raw: bytes) -> object:
    def pairs(items: Sequence[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    def invalid(_: str) -> object:
        raise ValueError("non-finite JSON")
    return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs,
                      parse_constant=invalid)


def checked_count(value: object, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ProofError("UNKNOWN_RESOURCE_BOUND", name)
    return value


def checked_size(value: object, maximum: int, name: str) -> int:
    return checked_count(value, maximum, name)


@dataclass(frozen=True)
class EffectiveSamplerRecord:
    response_type: str
    strict: bool
    max_tokens: int
    n_predict_override: bool
    grammar_type: str
    grammar_root: str
    grammar_nonempty: bool
    ignore_eos: bool
    request_logit_bias_count: int
    base_logit_bias_count: int
    eog_bias_count: int
    grammar_lazy: bool
    grammar_trigger_count: int
    preserved_token_count: int
    stop_count: int
    prefill_prefix_count: int
    alternate_grammar_count: int
    unknown_hard_mask_count: int
    chain_certificate_sha256: str


def validate_sampler_record(record: EffectiveSamplerRecord) -> None:
    expected = {
        "response_type": "json_schema", "strict": True, "max_tokens": HARD_CAP,
        "n_predict_override": False, "grammar_type": "JSON_SCHEMA_GBNF",
        "grammar_root": "root", "grammar_nonempty": True, "ignore_eos": False,
        "request_logit_bias_count": 0, "base_logit_bias_count": 0,
        "eog_bias_count": 0, "grammar_lazy": False, "grammar_trigger_count": 0,
        "preserved_token_count": 0, "stop_count": 0, "prefill_prefix_count": 0,
        "alternate_grammar_count": 0, "unknown_hard_mask_count": 0,
    }
    for key, value in expected.items():
        if getattr(record, key) != value:
            raise ProofError("UNKNOWN_EFFECTIVE_SAMPLER", key)
    if record.chain_certificate_sha256 != product_certificate_sha256():
        raise ProofError("UNKNOWN_EFFECTIVE_SAMPLER", "chain certificate")


def validate_product_certificate(raw: bytes) -> None:
    if raw != product_certificate_bytes():
        raise ProofError("UNKNOWN_EFFECTIVE_SAMPLER", "product source certificate")


@dataclass(frozen=True)
class ModuleIdentity:
    final_path: str
    volume_serial: int
    file_id: int
    size: int
    sha256: str
    classification: str
    version: str | None = None

    def key(self) -> tuple[object, ...]:
        return self.volume_serial, self.file_id, self.size, self.sha256


@dataclass(frozen=True)
class ModuleEvent:
    ordinal: int
    kind: str
    module: ModuleIdentity | None


def validate_module_sets(pre: Sequence[ModuleIdentity], post: Sequence[ModuleIdentity],
                         events: Sequence[ModuleEvent], approved_non_system: Mapping[str, str]) -> None:
    if len(pre) > MODULE_LIMIT or len(post) > MODULE_LIMIT or len(events) > MODULE_EVENT_LIMIT:
        raise ProofError("UNKNOWN_RESOURCE_BOUND", "module bound")
    if tuple(item.key() for item in pre) != tuple(item.key() for item in post):
        raise ProofError("UNKNOWN_MODULE_SET", "pre/post module drift")
    if (len({item.key() for item in pre}) != len(pre)
            or len({os.path.normcase(item.final_path) for item in pre}) != len(pre)):
        raise ProofError("UNKNOWN_MODULE_SET", "duplicate module")
    def validate_identity(item: ModuleIdentity) -> None:
        if (item.classification not in {"SYSTEM", "NON_SYSTEM"} or item.size < 0
                or item.volume_serial < 0 or item.file_id < 0
                or not re.fullmatch(r"[0-9a-f]{64}", item.sha256) or not Path(item.final_path).is_absolute()):
            raise ProofError("UNKNOWN_MODULE_SET", "module identity shape")
        if item.classification == "SYSTEM" and (not isinstance(item.version, str)
                or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", item.version)):
            raise ProofError("UNKNOWN_MODULE_SET", "system module version")
        if item.classification == "NON_SYSTEM" and item.version is not None:
            raise ProofError("UNKNOWN_MODULE_SET", "non-system module version")
        if item.classification == "NON_SYSTEM":
            name = Path(item.final_path).name.casefold()
            if approved_non_system.get(name) != item.sha256:
                raise ProofError("UNKNOWN_MODULE_SET", "foreign non-system module")
    for item in pre:
        validate_identity(item)
    if tuple(event.ordinal for event in events) != tuple(range(len(events))):
        raise ProofError("UNKNOWN_MODULE_SET", "event ordinal")
    if (not events or events[0].kind != "CREATE_PROCESS"
            or sum(event.kind == "CREATE_PROCESS" for event in events) != 1
            or any(event.kind not in {"CREATE_PROCESS", "LOAD_DLL", "UNLOAD_DLL"} for event in events)):
        raise ProofError("UNKNOWN_MODULE_SET", "event kind")
    if any(event.kind == "UNLOAD_DLL" for event in events):
        raise ProofError("UNKNOWN_MODULE_SET", "module unload")
    if any(event.module is None for event in events):
        raise ProofError("UNKNOWN_MODULE_SET", "event module")
    for event in events:
        validate_identity(event.module)
    loaded = {event.module.key() for event in events if event.kind in {"CREATE_PROCESS", "LOAD_DLL"}
              and event.module is not None}
    if len(loaded) != len(events) or set(item.key() for item in pre) != loaded:
        raise ProofError("UNKNOWN_MODULE_SET", "snapshot/event mismatch")


@dataclass(frozen=True)
class ChildResult:
    nonce: str
    grammar_size: int
    grammar_bytes: bytes
    token_ids: tuple[int, ...]
    prefix_before_ids: tuple[int, ...]
    prefix_after_ids: tuple[int, ...]
    prefix_allowed: tuple[bool, ...]
    roundtrip_exact: bool
    decoder_bytes: bytes
    vocab_size: int
    eog_ids: tuple[int, ...]
    eog_before_ids: tuple[int, ...]
    eog_after_ids: tuple[int, ...]
    eog_allowed: tuple[bool, ...]
    accepted_eog: int | None
    accounted_generated_tokens: int
    calls: Mapping[str, int]
    sampler_record: EffectiveSamplerRecord
    abi: Mapping[str, int]
    member_abi: Mapping[str, object]
    status: str


MEMBER_ABI_RUNTIME_SCHEMA = "aiwolf.pf3-dump-member-abi-runtime.v1"
MEMBER_ABI_RUNTIME_KEYS = {
    "schema_version", "compile_record_sha256", "_MSC_VER", "_MSC_FULL_VER", "_MSVC_LANG",
    "dynamic_crt", "iterator_debug_level", "pmf_mode", "pmf_size", "pmf_alignment",
    "farproc_size", "pointer_size", "pmf_trivially_copyable", "decorated_symbol", "export_kind",
    "export_nonforwarded", "address_in_pinned_module", "address_in_executable_section",
    "getmodule_owner_matches", "virtualquery_allocation_base_matches", "pmf_roundtrip_bytes_match",
    "pmf_roundtrip_pointer_match", "binding_enabled",
}
MEMBER_ABI_TRUE_KEYS = {
    "dynamic_crt", "pmf_trivially_copyable", "export_nonforwarded", "address_in_pinned_module",
    "address_in_executable_section", "getmodule_owner_matches", "virtualquery_allocation_base_matches",
    "pmf_roundtrip_bytes_match", "pmf_roundtrip_pointer_match", "binding_enabled",
}
MEMBER_ABI_MOCK_KEYS = {
    "schema_version", "status", "call_count", "receiver_expected", "receiver_observed",
    "indent_expected", "indent_observed", "returned_utf8", "returned_size", "returned_sha256",
    "returned_exact_match", "destructor_count", "cpp_exception", "seh", "timed_out", "exit_code",
    "mock_dll_sha256", "mock_caller_exe_sha256",
}
MEMBER_ABI_MOCK_RETURNED = '{"receiver":51,"indent":-1}'
MEMBER_ABI_MOCK_RETURNED_SHA256 = hashlib.sha256(MEMBER_ABI_MOCK_RETURNED.encode("utf-8")).hexdigest()


def validate_member_abi_mock_result(value: Mapping[str, object], *, dll_sha256: str,
                                    caller_sha256: str) -> None:
    if set(value) != MEMBER_ABI_MOCK_KEYS:
        raise ProofError("UNKNOWN_ABI_IDENTITY", "member ABI mock shape")
    expected = {
        "schema_version": "aiwolf.pf3-dump-member-abi-mock.v1", "status": "PASS",
        "call_count": 1, "receiver_expected": 51, "receiver_observed": 51,
        "indent_expected": -1, "indent_observed": -1, "returned_utf8": MEMBER_ABI_MOCK_RETURNED,
        "returned_size": len(MEMBER_ABI_MOCK_RETURNED),
        "returned_sha256": MEMBER_ABI_MOCK_RETURNED_SHA256, "returned_exact_match": True,
        "destructor_count": 1, "cpp_exception": False, "seh": False, "timed_out": False,
        "exit_code": 0, "mock_dll_sha256": dll_sha256, "mock_caller_exe_sha256": caller_sha256,
    }
    if dict(value) != expected:
        raise ProofError("UNKNOWN_ABI_IDENTITY", "member ABI mock result")


MEMBER_ABI_SMOKE_CHILD_KEYS = {
    "schema_version", "status", "parse_calls", "dump_calls", "destroy_calls", "input_utf8",
    "output_utf8", "output_exact_match", "output_size", "output_sha256", "compile_record_sha256",
    "runtime_record", "exception", "seh", "timed_out", "exit_code", "cleanup",
}
MEMBER_ABI_FAILURE_CHECKS = {
    "COMPILE_RECORD", "COMPILER_MACROS", "PMF_MODE", "PMF_SHAPE", "SYMBOL", "EXPORT_DIRECT",
    "ADDRESS_MODULE", "ADDRESS_SECTION", "GETMODULE_OWNER", "VIRTUALQUERY_BASE",
    "ROUNDTRIP_BYTES", "ROUNDTRIP_POINTER",
}


def validate_member_abi_runtime_failure(value: Mapping[str, object]) -> None:
    if (set(value) != {"schema_version", "status", "failing_check", "parse_calls", "dump_calls",
                       "emitter_calls"}
            or value.get("schema_version") != "aiwolf.pf3-dump-member-abi-runtime-failure.v1"
            or value.get("status") != "UNKNOWN_ABI_IDENTITY"
            or value.get("failing_check") not in MEMBER_ABI_FAILURE_CHECKS
            or value.get("parse_calls") != 0 or value.get("dump_calls") != 0
            or value.get("emitter_calls") != 0):
        raise ProofError("UNKNOWN_ABI_IDENTITY", "member ABI runtime failure")


def validate_member_abi_smoke_child(value: Mapping[str, object], *, compile_record_sha256: str,
                                    compile_record: Mapping[str, object]) -> None:
    if set(value) != MEMBER_ABI_SMOKE_CHILD_KEYS:
        raise ProofError("UNKNOWN_ABI_IDENTITY", "ABI smoke child shape")
    runtime = value.get("runtime_record")
    if not isinstance(runtime, dict):
        raise ProofError("UNKNOWN_ABI_IDENTITY", "ABI smoke runtime record")
    validate_member_abi_runtime(runtime, expected_compile_record_sha256=compile_record_sha256,
                                expected_symbol=compile_record.get("decorated_symbol"),
                                expected_compile_record=compile_record)
    fixed = '{"pf3":1}'
    expected = {
        "schema_version": "aiwolf.pf3-dump-actual-abi-smoke.v1", "status": "ABI_COMPATIBLE",
        "parse_calls": 1, "dump_calls": 1, "destroy_calls": 1, "input_utf8": fixed,
        "output_utf8": fixed, "output_exact_match": True, "output_size": len(fixed),
        "output_sha256": hashlib.sha256(fixed.encode("utf-8")).hexdigest(),
        "compile_record_sha256": compile_record_sha256, "runtime_record": runtime,
        "exception": False, "seh": False, "timed_out": False, "exit_code": 0,
        "cleanup": "PENDING_POST",
    }
    if dict(value) != expected:
        raise ProofError("UNKNOWN_ABI_IDENTITY", "ABI smoke child result")


def validate_member_abi_runtime(value: Mapping[str, object], *,
                                expected_compile_record_sha256: str | None = None,
                                expected_symbol: str | None = None,
                                expected_compile_record: Mapping[str, object] | None = None,
                                synthetic: bool = False) -> None:
    if synthetic:
        if dict(value) != {"schema_version": MEMBER_ABI_RUNTIME_SCHEMA, "synthetic": True}:
            raise ProofError("UNKNOWN_ABI_IDENTITY", "member ABI synthetic record")
        return
    if set(value) != MEMBER_ABI_RUNTIME_KEYS or value.get("schema_version") != MEMBER_ABI_RUNTIME_SCHEMA:
        raise ProofError("UNKNOWN_ABI_IDENTITY", "member ABI runtime shape")
    if any(value.get(key) is not True for key in MEMBER_ABI_TRUE_KEYS):
        raise ProofError("UNKNOWN_ABI_IDENTITY", "member ABI runtime check")
    for key in ("_MSC_VER", "_MSC_FULL_VER", "_MSVC_LANG", "iterator_debug_level", "pmf_size",
                "pmf_alignment", "farproc_size", "pointer_size"):
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ProofError("UNKNOWN_ABI_IDENTITY", "member ABI runtime integer")
    if (value["pmf_size"] != 8 or value["farproc_size"] != 8 or value["pointer_size"] != 8
            or value["iterator_debug_level"] != 0
            or value.get("pmf_mode") != "MSVC_DEFAULT_BEST_CASE_NO_BASE"
            or value.get("export_kind") != "DIRECT_EXECUTABLE"):
        raise ProofError("UNKNOWN_ABI_IDENTITY", "member ABI runtime literal")
    sha = value.get("compile_record_sha256")
    symbol = value.get("decorated_symbol")
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
        raise ProofError("UNKNOWN_ABI_IDENTITY", "member ABI compile record")
    if not isinstance(symbol, str) or not symbol:
        raise ProofError("UNKNOWN_ABI_IDENTITY", "member ABI symbol")
    if expected_compile_record_sha256 is not None and sha != expected_compile_record_sha256:
        raise ProofError("UNKNOWN_ABI_IDENTITY", "member ABI compile record")
    if expected_symbol is not None and symbol != expected_symbol:
        raise ProofError("UNKNOWN_ABI_IDENTITY", "member ABI symbol")
    if expected_compile_record is not None:
        expected_projection = {
            "_MSC_VER": expected_compile_record.get("_MSC_VER"),
            "_MSC_FULL_VER": expected_compile_record.get("_MSC_FULL_VER"),
            "_MSVC_LANG": expected_compile_record.get("_MSVC_LANG"),
            "dynamic_crt": expected_compile_record.get("dynamic_crt"),
            "iterator_debug_level": expected_compile_record.get("iterator_debug_level"),
            "pmf_mode": expected_compile_record.get("pmf_mode"),
            "pmf_size": expected_compile_record.get("pmf_size"),
            "pmf_alignment": expected_compile_record.get("pmf_alignment"),
            "farproc_size": expected_compile_record.get("farproc_size"),
            "pointer_size": expected_compile_record.get("pointer_size"),
            "pmf_trivially_copyable": expected_compile_record.get("pmf_trivially_copyable"),
            "decorated_symbol": expected_compile_record.get("decorated_symbol"),
            "export_kind": expected_compile_record.get("export_kind"),
        }
        if any(value.get(key) != item for key, item in expected_projection.items()):
            raise ProofError("UNKNOWN_ABI_IDENTITY", "member ABI compile/runtime mismatch")


def validate_child_result(result: ChildResult, *, nonce: str, expected_raw: bytes | None = None,
                          expected_abi: Mapping[str, int] | None = None,
                          expected_member_abi: Mapping[str, object] | None = None) -> dict[str, object]:
    if result.nonce != nonce or result.status != "PROOF_COMPLETE":
        raise ProofError("UNKNOWN_RUNTIME_INVALID", "child result identity")
    checked_size(result.grammar_size, GRAMMAR_LIMIT, "grammar")
    if result.grammar_size == 0 or len(result.grammar_bytes) != result.grammar_size:
        raise ProofError("UNKNOWN_GRAMMAR_EMITTER", "grammar bytes")
    token_count = checked_count(len(result.token_ids), TOKEN_LIMIT, "tokens")
    if (result.prefix_before_ids != result.token_ids or result.prefix_after_ids != result.token_ids
            or len(result.prefix_allowed) != token_count or not all(result.prefix_allowed)):
        raise ProofError("UNKNOWN_PREFIX_REJECTED", "prefix")
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in result.token_ids):
        raise ProofError("UNKNOWN_PREFIX_REJECTED", "token id")
    checked_count(result.vocab_size, VOCAB_LIMIT, "vocab")
    checked_count(len(result.eog_ids), EOG_LIMIT, "eog count")
    checked_size(len(result.decoder_bytes), DETOKENIZED_LIMIT, "decoder bytes")
    if not result.roundtrip_exact or (expected_raw is not None and result.decoder_bytes != expected_raw):
        raise ProofError("UNKNOWN_DECODER_MISMATCH", "roundtrip")
    if (result.eog_before_ids != result.eog_ids or result.eog_after_ids != result.eog_ids
            or len(result.eog_allowed) != len(result.eog_ids)):
        raise ProofError("UNKNOWN_EOG_OR_BUDGET", "eog observations")
    if result.accepted_eog is None or result.accepted_eog not in result.eog_ids:
        raise ProofError("UNKNOWN_EOG_OR_BUDGET", "accepted eog")
    allowed_eog = [item for item, allowed in zip(result.eog_ids, result.eog_allowed) if allowed]
    if not allowed_eog or result.accepted_eog != min(allowed_eog):
        raise ProofError("UNKNOWN_EOG_OR_BUDGET", "accepted eog ordering")
    if set(result.token_ids[:HARD_CAP]).intersection(result.eog_ids):
        raise ProofError("UNKNOWN_EOG_OR_BUDGET", "eog inside budget prefix")
    if token_count < HARD_CAP or result.accounted_generated_tokens != token_count + 1:
        raise ProofError("UNKNOWN_EOG_OR_BUDGET", "accounting")
    validate_sampler_record(result.sampler_record)
    if set(result.abi) != ABI_KEYS or any(isinstance(value, bool) or not isinstance(value, int) or value < 0
                                          for value in result.abi.values()):
        raise ProofError("UNKNOWN_ABI_IDENTITY", "ABI self report")
    if expected_abi is not None and dict(result.abi) != dict(expected_abi):
        raise ProofError("UNKNOWN_ABI_IDENTITY", "ABI layout")
    validate_member_abi_runtime(
        result.member_abi,
        expected_compile_record_sha256=(expected_member_abi or {}).get("compile_record_sha256"),
        expected_symbol=(expected_member_abi or {}).get("decorated_symbol"),
        expected_compile_record=(expected_member_abi or {}).get("compile_record"),
        synthetic=bool(result.member_abi.get("synthetic")),
    )
    expected_calls = {
        "emitter": 1, "tokenize": 2, "detokenize": 2,
        "prefix_apply": token_count, "prefix_accept": token_count,
        "vocab_is_eog": result.vocab_size,
        "eog_apply": len(result.eog_ids), "eog_accept": 1,
        "token_to_piece": 0, "provider": 0, "inference": 0, "server": 0,
        "gpu": 0, "game": 0, "actions": 0,
    }
    for key, value in expected_calls.items():
        if result.calls.get(key) != value:
            raise ProofError("UNKNOWN_RESOURCE_BOUND", f"call count {key}")
    constant_calls = checked_count(result.calls.get("constant_native", -1), 32, "constant calls")
    total = result.vocab_size + 2 * token_count + len(result.eog_ids) + constant_calls + 5
    if total > 270_629:
        raise ProofError("UNKNOWN_RESOURCE_BOUND", "native calls")
    if result.accounted_generated_tokens <= HARD_CAP:
        raise ProofError("UNKNOWN_EOG_OR_BUDGET", "no counterexample")
    return {
        "path_token_count": token_count, "eog_reserve": 1,
        "accounted_generated_tokens": result.accounted_generated_tokens,
        "hard_cap": HARD_CAP, "aggregate": "FAIL_BUDGET", "pf3": "FAIL",
    }


def encode_input_envelope(nonce: str, schema: bytes, raw: bytes,
                          sampler: Mapping[str, object]) -> bytes:
    nonce_raw = nonce.encode("ascii")
    sampler_raw = canonical_bytes(sampler)
    for value, maximum, name in ((schema, SCHEMA_LIMIT, "schema"),
                                 (raw, RAW_LIMIT, "raw"),
                                 (sampler_raw, 64 * 1024, "sampler")):
        checked_size(len(value), maximum, name)
    if not re.fullmatch(rb"[0-9a-f]{32}", nonce_raw):
        raise ProofError("UNKNOWN_INPUT_INVALID", "nonce")
    header = struct.pack("<8sIIII", b"PF3PATH1", len(nonce_raw), len(schema), len(raw), len(sampler_raw))
    return header + nonce_raw + schema + raw + sampler_raw


def build_witness() -> tuple[bytes, bytes]:
    raw = t527.build_candidate(CANDIDATE_ID)
    if len(raw) > RAW_LIMIT or raw != t527.compact({"message": "\0" * 200}):
        raise ProofError("UNKNOWN_INPUT_INVALID", "witness")
    value = t527.strict_parse(raw)
    if value != {"message": "\0" * 200}:
        raise ProofError("UNKNOWN_INPUT_INVALID", "logical witness")
    companion_raw = COMPANION_SCHEMA.read_bytes()
    if digest(companion_raw) != t527.HASHES["schema"]:
        raise ProofError("UNKNOWN_INPUT_INVALID", "companion identity")
    companion = t527.strict_parse(companion_raw)
    try:
        schemas = t527.validate_bindings(companion)
        t527.validate_candidate(CANDIDATE_ID, raw, schemas)
    except Exception as exc:
        raise ProofError("UNKNOWN_INPUT_INVALID", "schema/wire/candidate binding") from exc
    schema = t527.compact(schemas["MESSAGE"])
    if len(schema) > SCHEMA_LIMIT:
        raise ProofError("UNKNOWN_RESOURCE_BOUND", "schema")
    return schema, raw


class ChildBackend(Protocol):
    def start(self, envelope: bytes, nonce: str, deadline: float) -> None: ...
    def wait_ready(self, phase: str, timeout: float) -> None: ...
    def snapshot(self) -> tuple[ModuleIdentity, ...]: ...
    def continue_child(self, phase: str, nonce: str) -> None: ...
    def result(self, timeout: float) -> ChildResult: ...
    def events(self) -> tuple[ModuleEvent, ...]: ...
    def reap(self, timeout: float) -> None: ...
    def close(self) -> None: ...
    def verify_static(self, phase: str) -> None: ...


class SyntheticBackend:
    """Model-free backend for focused tests; never accepted by the real CLI."""
    def __init__(self, result: ChildResult, modules: Sequence[ModuleIdentity] = (),
                 events: Sequence[ModuleEvent] = ()):
        self._result = result
        self._modules = tuple(modules)
        self._events = tuple(events)
        self.started = 0
        self.ready: list[str] = []
        self.reaped = False

    def start(self, envelope: bytes, nonce: str, deadline: float | None = None) -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise ProofError("UNKNOWN_RUNTIME_INVALID", "child start deadline")
        if len(envelope) < 24:
            raise ProofError("UNKNOWN_INPUT_INVALID", "synthetic envelope")
        magic, nonce_size, schema_size, raw_size, sampler_size = struct.unpack("<8sIIII", envelope[:24])
        total = 24 + nonce_size + schema_size + raw_size + sampler_size
        if magic != b"PF3PATH1" or nonce_size != 32 or total != len(envelope):
            raise ProofError("UNKNOWN_INPUT_INVALID", "synthetic envelope")
        cursor = 24
        if envelope[cursor:cursor + nonce_size].decode("ascii") != nonce:
            raise ProofError("UNKNOWN_INPUT_INVALID", "synthetic nonce")
        cursor += nonce_size + schema_size + raw_size
        strict_json(envelope[cursor:cursor + sampler_size])
        if deadline is not None and time.monotonic() >= deadline:
            raise ProofError("UNKNOWN_RUNTIME_INVALID", "child start deadline")
        self.started += 1
    def wait_ready(self, phase: str, timeout: float) -> None: self.ready.append(phase)
    def snapshot(self) -> tuple[ModuleIdentity, ...]: return self._modules
    def continue_child(self, phase: str, nonce: str) -> None: return None
    def result(self, timeout: float) -> ChildResult: return self._result
    def events(self) -> tuple[ModuleEvent, ...]: return self._events
    def reap(self, timeout: float) -> None: self.reaped = True
    def close(self) -> None: return None
    def verify_static(self, phase: str) -> None: return None


def run_backend(backend: ChildBackend, *, nonce: str, schema: bytes, raw: bytes,
                sampler_input: Mapping[str, object], approved_modules: Mapping[str, str],
                expected_abi: Mapping[str, int] | None = None,
                run_deadline: float | None = None,
                observation: dict[str, object] | None = None) -> tuple[dict[str, object], StateMachine]:
    state = StateMachine()
    observation = {} if observation is None else observation
    operation = "START"
    primary_error = None
    child_deadline = time.monotonic() + CHILD_TIMEOUT_SECONDS
    if run_deadline is not None:
        child_deadline = min(child_deadline, run_deadline - CHILD_CLEANUP_RESERVE_SECONDS)
    def remaining() -> float:
        value = child_deadline - time.monotonic()
        if value <= 0:
            raise ProofError("UNKNOWN_RUNTIME_INVALID", "child deadline")
        return value
    envelope = encode_input_envelope(nonce, schema, raw, sampler_input)
    state.advance(State.CLAIMED)
    state.advance(State.INPUT_BOUND)
    state.advance(State.TOOL_BOUND)
    try:
        remaining()
        backend.start(envelope, nonce, child_deadline)
        state.advance(State.CHILD_STARTED)
        operation = "WAIT_READY_PRE"
        backend.wait_ready("PRE", remaining())
        state.advance(State.NATIVE_LOADED)
        operation = "VERIFY_MODULE_PRE"
        backend.verify_static("PRE")
        pre = backend.snapshot()
        validate_module_sets(pre, pre, backend.events(), approved_modules)
        state.advance(State.MODULE_PRE_VERIFIED)
        backend.continue_child("PRE", nonce)
        operation = "WAIT_RESULT"
        result = backend.result(remaining())
        operation = "VALIDATE_RESULT"
        remaining()
        state.advance(State.GRAMMAR_EMITTED)
        validate_sampler_record(result.sampler_record)
        state.advance(State.SAMPLER_CONFIG_VERIFIED)
        if not result.prefix_allowed or not all(result.prefix_allowed):
            raise ProofError("UNKNOWN_PREFIX_REJECTED", "prefix")
        state.advance(State.PREFIX_VERIFIED)
        if not result.roundtrip_exact:
            raise ProofError("UNKNOWN_DECODER_MISMATCH", "roundtrip")
        state.advance(State.ROUNDTRIP_VERIFIED)
        if result.accepted_eog is None:
            raise ProofError("UNKNOWN_EOG_OR_BUDGET", "eog")
        state.advance(State.EOG_VERIFIED)
        summary = validate_child_result(result, nonce=nonce, expected_raw=raw, expected_abi=expected_abi)
        remaining()
        state.advance(State.ACCOUNTING_VERIFIED)
        operation = "WAIT_READY_POST"
        backend.wait_ready("POST", remaining())
        operation = "VERIFY_MODULE_POST"
        backend.verify_static("POST")
        post = backend.snapshot()
        validate_module_sets(pre, post, backend.events(), approved_modules)
        state.advance(State.MODULE_POST_VERIFIED)
        backend.continue_child("POST", nonce)
        operation = "REAP"
        backend.reap(remaining())
        backend.verify_static("REAPED")
        remaining()
        state.advance(State.CHILD_REAPED)
        summary["_private"] = {
            "schema_bytes": schema.decode("utf-8"), "raw_hex": raw.hex(),
            "grammar_hex": result.grammar_bytes.hex(), "grammar_sha256": digest(result.grammar_bytes),
            "grammar_size": result.grammar_size, "token_ids": list(result.token_ids),
            "prefix_before_ids": list(result.prefix_before_ids),
            "prefix_after_ids": list(result.prefix_after_ids),
            "prefix_allowed": list(result.prefix_allowed), "roundtrip_exact": result.roundtrip_exact,
            "decoder_hex": result.decoder_bytes.hex(), "decoder_sha256": digest(result.decoder_bytes),
            "vocab_size": result.vocab_size, "eog_ids": list(result.eog_ids),
            "eog_before_ids": list(result.eog_before_ids), "eog_after_ids": list(result.eog_after_ids),
            "eog_allowed": list(result.eog_allowed), "accepted_eog": result.accepted_eog,
            "accounting_operands": {"path_token_count": len(result.token_ids), "eog_reserve": 1,
                                    "hard_cap": HARD_CAP,
                                    "accounted_generated_tokens": result.accounted_generated_tokens},
            "calls": dict(result.calls),
            "abi": dict(result.abi),
            "module_pre": [item.__dict__ for item in pre],
            "module_post": [item.__dict__ for item in post],
            "module_events": [{"ordinal": item.ordinal, "kind": item.kind,
                               "module": item.module.__dict__ if item.module else None}
                              for item in backend.events()],
            "process_identity": getattr(backend, "process_identity", None),
        }
        return summary, state
    except BaseException as error:
        primary_error = failure_observation(error)
        if isinstance(error, ProofError) and error.detail in {"child deadline", "child start deadline", "spawn deadline"}:
            if hasattr(backend, "timed_out"):
                backend.timed_out = True
        state.fail(primary_error["aggregate"])
        raise
    finally:
        observation.update(last_operation=operation, last_state=state.state.value,
                           state_history=list(state.history), failure=primary_error,
                           cleanup_failure=None)
        try:
            backend.close()
        except BaseException as error:
            observation["cleanup_failure"] = failure_observation(error)
            raise


def parse_child_result(value: Mapping[str, object]) -> ChildResult:
    expected = {"nonce", "grammar_size", "grammar_b64", "token_ids", "prefix_before_ids",
                "prefix_after_ids", "prefix_allowed", "roundtrip_exact", "decoder_b64",
                "vocab_size", "eog_ids", "eog_before_ids", "eog_after_ids", "eog_allowed",
                "accepted_eog", "accounted_generated_tokens",
                "calls", "sampler_record", "abi", "member_abi", "status"}
    if (set(value) != expected or not isinstance(value.get("calls"), dict)
            or not isinstance(value.get("sampler_record"), dict) or not isinstance(value.get("member_abi"), dict)):
        raise ProofError("UNKNOWN_RUNTIME_INVALID", "child result shape")
    try:
        sampler = EffectiveSamplerRecord(**value["sampler_record"])
        grammar_bytes = base64.b64decode(value["grammar_b64"], validate=True)
        decoder_bytes = base64.b64decode(value["decoder_b64"], validate=True)
        if (base64.b64encode(grammar_bytes).decode("ascii") != value["grammar_b64"]
                or base64.b64encode(decoder_bytes).decode("ascii") != value["decoder_b64"]):
            raise ValueError("non-canonical base64")
        return ChildResult(
            nonce=value["nonce"], grammar_size=value["grammar_size"],
            grammar_bytes=grammar_bytes, token_ids=tuple(value["token_ids"]),
            prefix_before_ids=tuple(value["prefix_before_ids"]),
            prefix_after_ids=tuple(value["prefix_after_ids"]),
            prefix_allowed=tuple(value["prefix_allowed"]),
            roundtrip_exact=value["roundtrip_exact"], decoder_bytes=decoder_bytes,
            vocab_size=value["vocab_size"], eog_ids=tuple(value["eog_ids"]),
            eog_before_ids=tuple(value["eog_before_ids"]),
            eog_after_ids=tuple(value["eog_after_ids"]), eog_allowed=tuple(value["eog_allowed"]),
            accepted_eog=value["accepted_eog"],
            accounted_generated_tokens=value["accounted_generated_tokens"],
            calls=dict(value["calls"]), sampler_record=sampler, abi=dict(value["abi"]),
            member_abi=dict(value["member_abi"]), status=value["status"])
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise ProofError("UNKNOWN_RUNTIME_INVALID", "child result shape") from exc


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Offline PF3 grammar token path proof")
    result.add_argument("--config", type=Path, required=True)
    result.add_argument("--private", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    # The real Windows backend is deliberately loaded lazily so imports and focused tests
    # cannot load native code or consume the one-shot measurement permission.
    args = parser().parse_args(argv)
    from scripts.phase6_pf3_token_path_windows import run_real_proof
    return run_real_proof(args, globals())


if __name__ == "__main__":
    raise SystemExit(main())
