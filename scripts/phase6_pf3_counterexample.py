"""T527: test-only canonical encode診断。実native実行には別Tester gateが必要。"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import stat
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from jsonschema import Draft202012Validator
from ai_client.discussion.generation_v2 import (
    AbilityOptionV2, CoOptionV2, GenerationCatalogV2, OpinionBasisV2, VoteOptionV2,
    build_generation_v2_schema, parse_and_validate_generation_v2_candidate_structure,
)
from ai_client.llm.backend import OpenAICompatibleBackend
from ai_client.llm.types import (GenerationSettings, LLMMessage,
    OpenAICompatibleBackendConfig, StructuredGenerationRequest)
from scripts.phase6_private_review import _plain_absolute, _locked_path, _read_bytes

ROOT = Path(__file__).resolve().parents[1]
HASHES = {
    "schema": "8d3c4be2e2c3c1565afcdbaa73c93a65252f39919a05ae59307b4dbda2b206d5",
    "model": "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8",
    "llama-tokenize.exe": "a0fbd34a8a3f25fc0f41cbac1ec67e8395a5ef940db33bd07307b5e3dc8cd6a1",
    "llama-common.dll": "32079d938fe545c1d9801b0935cda8468c105cba27795f9ba0ffeddafd2d9ac9",
    "llama.dll": "2b84a13dc35361309a4bb9745853b0950c1e8b10a7c2d5e4c7e419b0ab9819a6",
    "ggml.dll": "097c276b838facce28c3eb6fe3b9657c7ba1e0375e7578f5cb1d2a38da704228",
    "ggml-base.dll": "091def1bb64b6e8b50118d7e0219dbe79a220998ac3efc5de1a260b6d0cbefc",
    "libomp.dll": "a12116ba72d1d6820407cf30be23da04ce79d6bb8a71a5ee71759c5a1faa6f1c",
    "config": "43e509956d96492cace8393bdc3fd598ab2418315ccdfd82e144b876241f3bab",
    "converter": "4d58b73438e97ac4e2d11dbb0309702d6899088d3c44a75a2dcb9c57697bb288",
    "archive": "1011cb18e52b2a8b0548eed8242299f85f57862fac237c0d258dadbb1ec4fdbc",
}
STAGES = {"MESSAGE": "message", "CO_DECLARE": "co_opportunity"}
PAYLOADS = {"CONTROL_NUL": "\0", "QUOTE": '"', "REVERSE_SOLIDUS": "\\",
            "NON_ASCII_RAW": "漢", "NON_ASCII_ESC_LOWER": "\uffff", "NON_ASCII_ESC_UPPER": "\uffff"}
SERIALIZATIONS = ("COMPACT", "MAX_SPACE_RULE")
SPACE = b"\n\n" + b"\t" * 20
LOCAL = {"TOKENIZE_TIMEOUT_REAPED", "TOKENIZE_NONZERO_REAPED",
         "TOKENIZE_STDOUT_INVALID", "TOKEN_COUNT_SHAPE_INVALID"}


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def catalog():
    return GenerationCatalogV2(
        reply_ids=("r000", "r001"), player_ids=("p000", "p001", "p002"),
        peer_player_ids=("p001", "p002"), fact_ids=("f000", "f001", "f002"),
        disclose_ids=("d000",), utterance_claim_ids=("c000", "c001"), observed_claim_ids=("k000", "k001"),
        opinion_bases=(OpinionBasisV2("u000", "p001", "SUSPICION", 50, (0, 25, 75, 100), ("f000",)),),
        vote_options=(VoteOptionV2("o000", ("p001", "p002"), False), VoteOptionV2("o001", ("p002",), True)),
        co_options=(CoOptionV2("o100", ("q000",)),),
        ability_options=(AbilityOptionV2("o200", 0, (), False), AbilityOptionV2("o201", 2, ("p001", "p002"), True)),
    )


def logical(stage, payload):
    text = PAYLOADS[payload] * 200
    if stage == "MESSAGE":
        return {"message": text}
    if stage == "CO_DECLARE":
        return {"decision": "DECLARE", "co_option_id": "o100", "claimed_role_option_id": "q000",
                "comment": text, "fact_ids": ["f000", "f001"]}
    raise ValueError("fixture stage")


def candidate_ids():
    return tuple(f"{stage}.{payload}.{serialization}" for stage in STAGES
                 for payload in PAYLOADS for serialization in SERIALIZATIONS)


def build_candidate(candidate_id):
    if candidate_id not in candidate_ids():
        raise ValueError("fixture ID")
    stage, payload, serialization = candidate_id.split(".")
    space = SPACE if serialization == "MAX_SPACE_RULE" else b""
    def encode(value):
        if isinstance(value, dict):
            return b"{" + space + (b"," + space).join(
                compact(key) + space + b":" + space + encode(item) for key, item in value.items()) + space + b"}"
        if isinstance(value, list):
            return b"[" + space + (b"," + space).join(encode(item) for item in value) + space + b"]"
        raw = json.dumps(value, ensure_ascii=payload.startswith("NON_ASCII_ESC"), separators=(",", ":")).encode("utf-8")
        if payload == "NON_ASCII_ESC_UPPER":
            raw = raw.replace(b"\\uffff", b"\\uFFFF")
        return raw
    return encode(logical(stage, payload))


def strict_parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("nonfinite JSON")
    return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs, parse_constant=invalid)


def schema_span(raw):
    """全JSONをstrict検査し、唯一のresponse schema value spanを走査する。"""
    strict_parse(raw)
    text = raw.decode("utf-8")
    decoder = json.JSONDecoder()
    found = []
    target = ("response_format", "json_schema", "schema")
    def skip(i):
        while i < len(text) and text[i] in " \r\n\t":
            i += 1
        return i
    def scan(i, path):
        i = skip(i)
        start = i
        if text[i] == "{":
            i = skip(i + 1)
            while text[i] != "}":
                key, i = decoder.raw_decode(text, i)
                if not isinstance(key, str) or text[skip(i)] != ":":
                    raise ValueError("wire type")
                i = skip(scan(skip(i) + 1, path + (key,)))
                if text[i] == "}":
                    break
                if text[i] != ",":
                    raise ValueError("wire syntax")
                i = skip(i + 1)
            i += 1
        elif text[i] == "[":
            i = skip(i + 1)
            n = 0
            while text[i] != "]":
                i = skip(scan(i, path + (n,)))
                n += 1
                if text[i] == "]":
                    break
                if text[i] != ",":
                    raise ValueError("wire syntax")
                i = skip(i + 1)
            i += 1
        else:
            _, i = decoder.raw_decode(text, i)
        if path == target:
            found.append(text[start:i].encode("utf-8"))
        return i
    scan(0, ())
    if len(found) != 1 or not isinstance(strict_parse(found[0]), dict):
        raise ValueError("wire schema path")
    return found[0]


async def offline_wire(schema):
    def blocked(_):
        raise AssertionError("provider forbidden")
    backend = OpenAICompatibleBackend(OpenAICompatibleBackendConfig(
        endpoint="http://127.0.0.1:1/v1/chat/completions", model="offline",
        generation=GenerationSettings(max_output_tokens=512), max_request_bytes=200000),
        transport=httpx.MockTransport(blocked))
    try:
        return backend._request_payload(StructuredGenerationRequest(
            request_id="T527-offline", messages=(LLMMessage("user", "synthetic"),),
            output_schema=schema, generation_profile="phase6_v2", max_output_tokens=512, seed=0))
    finally:
        await backend.aclose()


def validate_bindings(companion):
    schemas = {}
    for stage, factory_stage in STAGES.items():
        schema = build_generation_v2_schema(factory_stage, catalog())
        expected = companion["schemas"][factory_stage]
        wire = strict_parse(schema_span(asyncio.run(offline_wire(schema))))
        if compact(schema) != compact(expected) or compact(schema) != compact(wire):
            raise ValueError("schema wire identity")
        schemas[stage] = schema
    return schemas


def validate_candidate(candidate_id, raw, schemas):
    stage, payload, _ = candidate_id.split(".")
    if raw != build_candidate(candidate_id):
        raise ValueError("fixture bytes")
    value = strict_parse(raw)
    Draft202012Validator(schemas[stage]).validate(value)
    parse_and_validate_generation_v2_candidate_structure(STAGES[stage], raw, catalog())
    if compact(value) != compact(logical(stage, payload)):
        raise ValueError("fixture logical")


def descriptor_bytes(fd, *, limit=None):
    os.lseek(fd, 0, os.SEEK_SET)
    size = os.fstat(fd).st_size
    if limit is not None and size > limit:
        raise ValueError("artifact too large")
    chunks = []
    for chunk in iter(lambda: os.read(fd, 1024 * 1024), b""):
        chunks.append(chunk)
    raw = b"".join(chunks)
    if len(raw) != size:
        raise ValueError("artifact size changed")
    return raw


def descriptor_hash(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    size = os.fstat(fd).st_size
    count = 0
    h = hashlib.sha256()
    for chunk in iter(lambda: os.read(fd, 1024 * 1024), b""):
        h.update(chunk)
        count += len(chunk)
    if size != count:
        raise ValueError("artifact size changed")
    return h.hexdigest()


def file_identity(fd):
    info = os.fstat(fd)
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size}


def rename_open_file(fd, path):
    # phase6_private_review._write_aggregateと同じhandle-based rename契約。
    if os.name != "nt":
        raise RuntimeError("Windows evidence handles required")
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    class RenameInfo(ctypes.Structure):
        _fields_ = [("replace", wintypes.BOOL), ("root", wintypes.HANDLE),
                    ("length", wintypes.DWORD), ("name", wintypes.WCHAR * 1)]
    name = str(path).encode("utf-16-le")
    buffer = ctypes.create_string_buffer(max(ctypes.sizeof(RenameInfo), RenameInfo.name.offset + len(name) + 2))
    info = RenameInfo.from_buffer(buffer)
    info.replace, info.root, info.length = False, None, len(name)
    ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.name.offset, name, len(name))
    if not kernel.SetFileInformationByHandle(msvcrt.get_osfhandle(fd), 3, buffer, len(buffer)):
        raise OSError("atomic handle rename")
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    resolved = ctypes.create_unicode_buffer(32768)
    length = kernel.GetFinalPathNameByHandleW(msvcrt.get_osfhandle(fd), resolved, len(resolved), 0)
    if not 0 < length < len(resolved) or resolved.value.removeprefix("\\\\?\\").casefold() != str(path).casefold():
        raise OSError("atomic rename identity")


class PrivateEvidence:
    """既存ACL/ancestor lockを保持し、作成した全file handleをsealまで所有する。"""
    def __init__(self, directory, stack):
        self.directory = directory
        self.stack = stack
        self.files = {}

    def write(self, name, raw, *, claim=False):
        if not re.fullmatch(r"(?:claim|detail|manifest|seal|row-[0-9]{2})\.json", name) or name in self.files:
            raise ValueError("closed evidence name")
        path = self.directory / name
        temporary = path if claim else self.directory / (name + ".partial")
        fd = self.stack.enter_context(_locked_path(temporary, create=True))
        if os.write(fd, raw) != len(raw):
            raise OSError("short private write")
        os.fsync(fd)
        if not claim:
            rename_open_file(fd, path)
        if descriptor_bytes(fd) != raw:
            raise OSError("private write verification")
        self.files[name] = fd

    def read(self, name):
        return descriptor_bytes(self.files[name])

    def names(self):
        if set(path.name for path in self.directory.iterdir()) != set(self.files):
            raise ValueError("unexpected evidence file")
        return sorted(self.files)


@contextmanager
def claim_private(directory, run_id, source_hash):
    with ExitStack() as stack:
        directory = _plain_absolute(Path(directory), must_exist=True)
        stack.enter_context(_locked_path(directory, directory=True))
        # Exclusive claim is created BEFORE any hashing/spawn; never removed on failure.
        if any(directory.iterdir()):
            raise ValueError("container already used")
        store = PrivateEvidence(directory, stack)
        info = directory.stat()
        store.write("claim.json", compact({"run_id": run_id, "runner_sha256": source_hash,
            "container_identity": {"device": info.st_dev, "inode": info.st_ino}}), claim=True)
        yield store


@contextmanager
def pinned_static_file(path):
    """公開static入力専用。private ACL要件を持つ既存helperは変更しない。"""
    if os.name != "nt":
        raise RuntimeError("Windows static pins required")
    from ctypes import wintypes
    import msvcrt
    path = Path(path)
    if not path.is_absolute() or any(part in {".", ".."} or ":" in part or part.endswith((".", " "))
                                    for part in path.parts[1:]):
        raise ValueError("static absolute path")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    handles = []
    fd = None
    try:
        current = Path(path.anchor)
        components = [current]
        for part in path.parts[1:]:
            current /= part
            components.append(current)
        for index, component in enumerate(components):
            last = index == len(components) - 1
            handle = kernel.CreateFileW(str(component), 0x80000000 if last else 0x80,
                1 if last else 3, None, 3, 0x00200000 | (0 if last else 0x02000000), None)
            if handle == ctypes.c_void_p(-1).value:
                raise OSError("static pin unavailable")
            handles.append(handle)
            attributes = (wintypes.DWORD * 2)()
            if not kernel.GetFileInformationByHandleEx(handle, 9, attributes, ctypes.sizeof(attributes)):
                raise OSError("static handle metadata")
            if attributes[0] & 0x400 or bool(attributes[0] & 0x10) == last:
                raise ValueError("static reparse/type")
            resolved = ctypes.create_unicode_buffer(32768)
            length = kernel.GetFinalPathNameByHandleW(handle, resolved, len(resolved), 0)
            if not 0 < length < len(resolved):
                raise OSError("static final path")
            final = Path(resolved.value.removeprefix("\\\\?\\"))
            if os.path.normcase(str(final)) != os.path.normcase(str(component)):
                raise ValueError("static final path mismatch")
        fd = msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_RDONLY)
        handles.pop()
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("static regular file required")
        yield {"path": final, "fd": fd, "file_identity": file_identity(fd)}
    finally:
        if fd is not None:
            os.close(fd)
        for handle in reversed(handles):
            kernel.CloseHandle(handle)


class StaticArtifacts:
    def __init__(self, pins):
        self.pins = pins
        self.paths = {key: str(pin["path"]) for key, pin in pins.items()}
        self.file_identities = {key: pin["file_identity"] for key, pin in pins.items()}

    def identity(self):
        observed = {}
        for key, pin in self.pins.items():
            if file_identity(pin["fd"]) != pin["file_identity"]:
                raise ValueError("static file identity drift")
            observed[key] = descriptor_hash(pin["fd"])
        return observed

    def read(self, key):
        return descriptor_bytes(self.pins[key]["fd"], limit=16 * 1024 * 1024)


@contextmanager
def pin_artifacts(paths):
    if set(paths) != set(HASHES):
        raise ValueError("identity keys")
    with ExitStack() as stack:
        pins = {key: stack.enter_context(pinned_static_file(path)) for key, path in paths.items()}
        artifacts = StaticArtifacts(pins)
        native = Path(artifacts.paths["llama-tokenize.exe"]).parent
        if any(Path(artifacts.paths[key]).parent != native or Path(artifacts.paths[key]).name != key
               for key in HASHES if key.endswith((".exe", ".dll"))):
            raise ValueError("native directory")
        yield artifacts


def command(paths):
    return [str(paths["llama-tokenize.exe"]), "-m", str(paths["model"]), "--stdin", "--ids",
            "--no-bos", "--no-escape", "--no-parse-special", "--offline", "-ngl", "0", "--device", "none"]


def creation_identity(process, executable):
    if os.name != "nt":
        raise RuntimeError("Windows process handle required")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    values = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(int(process._handle), *(ctypes.byref(v) for v in values)):
        raise RuntimeError("creation identity unavailable")
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    image = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(image))
    if not kernel.QueryFullProcessImageNameW(int(process._handle), 0, image, ctypes.byref(size)):
        raise RuntimeError("process image identity unavailable")
    if os.path.normcase(str(Path(image.value))) != os.path.normcase(str(Path(executable))):
        raise RuntimeError("process image mismatch")
    return {"pid": process.pid, "handle": int(process._handle), "image_path": image.value,
            "creation_time": (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime}


def token_ids(stdout):
    if not re.fullmatch(rb"\[(?:0|[1-9][0-9]*)(?:, (?:0|[1-9][0-9]*))*\]\r?\n", stdout):
        raise ValueError("TOKENIZE_STDOUT_INVALID")
    ids = json.loads(stdout.decode("ascii"))
    if any(value > 2147483647 for value in ids):
        raise ValueError("TOKEN_COUNT_SHAPE_INVALID")
    return ids


def public_row(candidate_id):
    return dict(candidate_id=candidate_id, logical_schema_status="NOT_RUN",
        grammar_membership_status="UNPROVEN", runtime_loaded_modules_status="UNPROVEN",
        raw_byte_count=len(build_candidate(candidate_id)), canonical_encode_token_count=None,
        status="NOT_RUN", reason="NOT_RUN")


PUBLIC_KEYS = {"task", "run_id", "runner_sha256", "tokenizer_sha256", "schema_sha256", "model_sha256",
    "private_manifest_sha256", "provider_count", "inference_count", "server_count", "rows", "status", "pf3",
    "max_children", "child_timeout_seconds", "run_timeout_seconds", "started_at", "ended_at"}
PUBLIC_ROW_KEYS = set(public_row(candidate_ids()[0]))


def public_bytes(report):
    if set(report) != PUBLIC_KEYS or report["pf3"] != "UNKNOWN":
        raise ValueError("closed public report")
    if len(report["rows"]) != 24 or [r["candidate_id"] for r in report["rows"]] != list(candidate_ids()):
        raise ValueError("closed public population")
    if any(set(row) != PUBLIC_ROW_KEYS for row in report["rows"]):
        raise ValueError("closed public row")
    return compact(report)


def source_digest():
    return digest(Path(__file__).read_bytes())


def validate_private_row(item, snapshots, paths, source_hash):
    cid = item["candidate_id"]
    raw = build_candidate(cid)
    if (item["argv"] != command(paths) or item["stdin_sha256"] != digest(raw)
            or item["stdin_byte_count"] != len(raw) or item["raw_hex"] != raw.hex()
            or item["runner_sha256"] != source_hash
            or item["approved_runner_sha256"] != source_hash
            or item["runner_start_sha256"] != source_hash or item["runner_end_sha256"] != source_hash
            or item["cleanup_result"] != "REAPED"):
        raise ValueError("row execution binding")
    for stream in ("stdout", "stderr"):
        value = bytes.fromhex(item[stream + "_hex"])
        if item[stream + "_sha256"] != digest(value) or item[stream + "_byte_count"] != len(value):
            raise ValueError("row stream binding")
    for phase in ("PRE", "POST"):
        reference = item[phase.lower() + "_identity"]
        snapshot = snapshots[reference["index"]]
        if (snapshot["candidate_id"] != cid or snapshot["phase"] != phase
                or digest(compact(snapshot)) != reference["sha256"] or snapshot["hashes"] != HASHES):
            raise ValueError("row identity reference")
    if os.path.normcase(item["process_identity"]["image_path"]) != os.path.normcase(paths["llama-tokenize.exe"]):
        raise ValueError("row process image binding")


def seal_report(store, detail, report):
    # The complete public projection (except the manifest digest itself) is durable.
    detail["public_report_core"] = dict(report)
    store.write("detail.json", compact(detail))
    manifest = {"files": [{"name": name, "sha256": digest(store.read(name))}
                          for name in store.names()]}
    manifest_raw = compact(manifest)
    store.write("manifest.json", manifest_raw)
    manifest_hash = digest(manifest_raw)
    store.write("seal.json", compact({"manifest_sha256": manifest_hash}))
    for entry in manifest["files"]:
        if digest(store.read(entry["name"])) != entry["sha256"]:
            raise ValueError("manifest finalization")
    if store.read("manifest.json") != manifest_raw or strict_parse(store.read("seal.json")) != {"manifest_sha256": manifest_hash}:
        raise ValueError("seal identity")
    if set(store.names()) != {entry["name"] for entry in manifest["files"]} | {"manifest.json", "seal.json"}:
        raise ValueError("manifest membership")
    report["private_manifest_sha256"] = manifest_hash
    public_bytes(report)
    return report


def _run_claimed(artifacts, store, run_id, approved_hash, *, popen, owner, clock, source_reader):
    started = datetime.now(timezone.utc).isoformat()
    deadline = clock() + 900
    paths = artifacts.paths
    rows = [public_row(cid) for cid in candidate_ids()]
    detail = dict(paths=dict(paths), states=["NEW"], identities=[], candidates=[],
                  approved_runner_sha256=approved_hash, runner_start_sha256=source_reader(),
                  build_identity="10697/093adb242", static_file_identities=artifacts.file_identities,
                  max_output_tokens="UNSET", provider_gate="CLOSED",
                  fixture_manifest=[dict(candidate_id=cid, stage=cid.split(".")[0],
                      payload_class=cid.split(".")[1], serialization=cid.split(".")[2],
                      logical_length=200, raw_byte_count=len(build_candidate(cid))) for cid in candidate_ids()],
                  actual_grammar_sha256=None, provider_legal_raw_status="UNPROVEN",
                  roundtrip_status="NOT_AVAILABLE", minimum_generation_token_count=None,
                  token_lower_bound_status="UNPROVEN", pf3_evidence_status="UNPROVEN")
    aggregate = "UNKNOWN_INPUT_INVALID"
    phase = "runtime"
    active_row = None
    def check(label, candidate_id=None):
        snapshot = {"index": len(detail["identities"]), "phase": label, "candidate_id": candidate_id,
                    "hashes": None, "file_identities": artifacts.file_identities}
        detail["identities"].append(snapshot)
        snapshot["hashes"] = artifacts.identity()
        if snapshot["hashes"] != HASHES:
            raise ValueError("static identity")
        return {"index": snapshot["index"], "sha256": digest(compact(snapshot))}
    try:
        check("INITIAL")
        if detail["runner_start_sha256"] != approved_hash:
            raise ValueError("runner start identity")
        phase = "input"
        schemas = validate_bindings(strict_parse(artifacts.read("schema")))
        detail["states"] += ["INPUT_BOUND", "CANDIDATES_BUILT"]
        for row in rows:
            validate_candidate(row["candidate_id"], build_candidate(row["candidate_id"]), schemas)
            row["logical_schema_status"] = "VALID"
        detail["states"] += ["LOGICAL_VALIDATED", "DIAGNOSTIC_RUNNING"]
        aggregate = "UNKNOWN_NO_FIXED_CANDIDATE"
        for index, row in enumerate(rows):
            phase = "runtime"
            if clock() >= deadline or source_reader() != approved_hash:
                raise RuntimeError("run deadline/source drift")
            cid = row["candidate_id"]
            pre = check("PRE", cid)
            raw = build_candidate(cid)
            argv = command(paths)
            item = dict(candidate_id=cid, raw_hex=raw.hex(), runner_sha256=approved_hash,
                approved_runner_sha256=approved_hash, runner_start_sha256=source_reader(),
                argv=list(argv), stdin_sha256=digest(raw), stdin_byte_count=len(raw), pre_identity=pre,
                logical_length=200, stage=cid.split(".")[0], payload_class=cid.split(".")[1],
                serialization=cid.split(".")[2], raw_byte_count=len(raw), actual_grammar_sha256=None,
                grammar_membership_status="UNPROVEN", provider_legal_raw_status="UNPROVEN",
                runtime_loaded_modules_status="UNPROVEN", roundtrip_status="NOT_AVAILABLE",
                minimum_generation_token_count=None, token_lower_bound_status="UNPROVEN",
                pf3_evidence_status="UNPROVEN", cleanup_result="UNPROVEN")
            detail["candidates"].append(item)
            process = None
            active_row = row
            try:
                if item["runner_start_sha256"] != approved_hash:
                    raise RuntimeError("runner pre-spawn drift")
                process = popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    cwd=str(Path(paths["llama-tokenize.exe"]).parent),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                item["process_identity"] = owner(process, paths["llama-tokenize.exe"])
                try:
                    stdout, stderr = process.communicate(input=raw, timeout=min(30, max(0, deadline - clock())))
                    reason = "TOKENIZE_NONZERO_REAPED" if process.returncode != 0 else None
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                    reason = "TOKENIZE_TIMEOUT_REAPED"
                process.wait(timeout=5)
                if process.poll() is None:
                    raise RuntimeError("child remains")
                item["returncode"] = process.returncode
                for stream, value in (("stdout", stdout), ("stderr", stderr)):
                    item[stream + "_hex"] = value.hex()
                    item[stream + "_sha256"] = digest(value)
                    item[stream + "_byte_count"] = len(value)
                if reason is None:
                    try:
                        ids = token_ids(stdout)
                        item["token_ids"] = ids
                        row["canonical_encode_token_count"] = len(ids)
                    except ValueError as exc:
                        reason = str(exc)
                row.update(status="UNKNOWN" if reason else "DIAGNOSTIC_COMPLETE",
                    reason=reason or ("CANONICAL_ENCODE_EXCEEDS_512_DIAGNOSTIC" if len(ids) >= 513
                                      else "CANONICAL_ENCODE_WITHIN_512_DIAGNOSTIC"))
            finally:
                try:
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                    if process is not None and process.poll() is None:
                        raise RuntimeError("cleanup unproven")
                    if process is not None:
                        item["cleanup_result"] = "REAPED"
                finally:
                    item["post_identity"] = check("POST", cid)
                    item["runner_end_sha256"] = source_reader()
                    if item["runner_end_sha256"] != approved_hash:
                        raise RuntimeError("runner post-reap drift")
            phase = "evidence"
            item["row"] = dict(row)
            validate_private_row(item, detail["identities"], paths, approved_hash)
            store.write(f"row-{index:02}.json", compact(item))
            active_row = None
        detail["states"].append("DIAGNOSTIC_COMPLETE")
        if any(row["reason"] in LOCAL for row in rows):
            aggregate = "UNKNOWN_DIAGNOSTIC_INCOMPLETE"
        elif any(row["canonical_encode_token_count"] >= 513 for row in rows):
            aggregate = "UNKNOWN_DIAGNOSTIC_CANDIDATE_FOUND"
    except Exception:
        aggregate = {"input": "UNKNOWN_INPUT_INVALID", "runtime": "UNKNOWN_RUNTIME_INVALID",
                     "evidence": "UNKNOWN_EVIDENCE_INVALID"}[phase]
        if active_row is not None:
            active_row.update(status="UNKNOWN", reason=aggregate)
    finally:
        try:
            check("FINAL")
        except Exception:
            aggregate = "UNKNOWN_RUNTIME_INVALID"
        detail["runner_end_sha256"] = source_reader()
        if detail["runner_end_sha256"] != approved_hash or clock() >= deadline:
            aggregate = "UNKNOWN_RUNTIME_INVALID"
        if aggregate.endswith("_INVALID"):
            for row in rows:
                if row["status"] != "NOT_RUN":
                    row.update(status="UNKNOWN", canonical_encode_token_count=None, reason=aggregate)
        detail["states"].append("UNKNOWN_SEALED")
        detail["rows"], detail["aggregate"] = rows, aggregate
    report = dict(task="T527", run_id=run_id, runner_sha256=approved_hash,
        tokenizer_sha256=HASHES["llama-tokenize.exe"], schema_sha256=HASHES["schema"], model_sha256=HASHES["model"],
        provider_count=0, inference_count=0, server_count=0, rows=rows, status=aggregate,
        pf3="UNKNOWN", max_children=24, child_timeout_seconds=30, run_timeout_seconds=900,
        started_at=started, ended_at=datetime.now(timezone.utc).isoformat())
    try:
        return seal_report(store, detail, report)
    except Exception:
        return {"task": "T527", "run_id": run_id, "status": "UNKNOWN_EVIDENCE_INVALID", "pf3": "UNKNOWN"}


def run(paths, private_dir, run_id, *, approved_runner_sha256, popen=subprocess.Popen,
        owner=creation_identity, clock=time.monotonic, private_claim=claim_private,
        artifact_pin=pin_artifacts, source_reader=source_digest):
    """実測packetが渡すreview済SHAを要求。依存注入はoffline tests専用。"""
    minimal = {"task": "T527", "run_id": run_id, "status": "UNKNOWN_INPUT_INVALID", "pf3": "UNKNOWN"}
    if not re.fullmatch(r"T527-[0-9]{8}-[0-9]{6}", run_id):
        return minimal
    if not re.fullmatch("[0-9a-f]{64}", approved_runner_sha256) or source_reader() != approved_runner_sha256:
        return minimal
    try:
        with private_claim(private_dir, run_id, approved_runner_sha256) as store:
            minimal["status"] = "UNKNOWN_RUNTIME_INVALID"
            with artifact_pin(paths) as artifacts:
                return _run_claimed(artifacts, store, run_id, approved_runner_sha256,
                    popen=popen, owner=owner, clock=clock, source_reader=source_reader)
    except Exception:
        return minimal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-config", type=Path, required=True)
    args = parser.parse_args()
    try:
        path = _plain_absolute(args.private_config, must_exist=True)
        config = strict_parse(_read_bytes(path))
        if set(config) != {"paths", "private_dir", "run_id", "approved_runner_sha256"}:
            raise ValueError("config")
        report = run(config["paths"], config["private_dir"], config["run_id"],
                     approved_runner_sha256=config["approved_runner_sha256"])
    except Exception:
        report = {"task": "T527", "status": "UNKNOWN_INPUT_INVALID", "pf3": "UNKNOWN"}
    print((public_bytes(report) if "rows" in report else compact(report)).decode("utf-8"))
    return 0 if report.get("status") in {"UNKNOWN_NO_FIXED_CANDIDATE", "UNKNOWN_DIAGNOSTIC_CANDIDATE_FOUND"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
