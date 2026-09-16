from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import unicodedata
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsonschema import Draft202012Validator


MAX_RECORDS = 512
MAX_FILE_BYTES = 16 * 1024 * 1024
SHA256_HEX_LENGTH = 64
CHECKLIST_SCHEMA = "aiwolf.phase6-private-review-checklist.v1"
AGGREGATE_SCHEMA = "aiwolf.phase6-private-review-aggregate.v1"
MANIFEST_SCHEMA = "aiwolf.phase6-private-manifest.v1"
GENERATION_SCHEMA = "aiwolf.ai-discussion-generation.v1"
GENERATION_SCHEMAS = frozenset({GENERATION_SCHEMA, "aiwolf.ai-discussion-generation.v2"})
TERMINAL_SCHEMA = "aiwolf.ai-discussion-terminal.v1"
DIMENSIONS = (
    "coherent",
    "source_relevant",
    "objective_consistent",
    "privacy_safe",
    "non_repetitive",
)
CHECKLIST_FIELDS = {"schema_version", "reviewer_task_id", "evidence_manifest_sha256", "records"}
RECORD_FIELDS = {"capture_id", *DIMENSIONS}
AGGREGATE_FIELDS = {
    "schema_version", "reviewer_task_id", "evidence_manifest_sha256",
    "private_checklist_sha256", "population_count", "responsive_population_count",
    "source_relevant_applicable_count", "dimension_counts", "linkage_failure_count",
    "missing_count", "corrupt_count", "duplicate_count",
    "normalization_duplicate_count", "human_quality_pass",
}
_REPARSE_POINT = 0x400

_ID_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r"^[^\ud800-\udfff]+$"}
CHECKLIST_JSON_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": sorted(CHECKLIST_FIELDS),
    "properties": {
        "schema_version": {"const": CHECKLIST_SCHEMA},
        "reviewer_task_id": _ID_SCHEMA,
        "evidence_manifest_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "records": {"type": "array", "maxItems": MAX_RECORDS, "items": {
            "type": "object", "additionalProperties": False, "required": sorted(RECORD_FIELDS),
            "properties": {
                "capture_id": _ID_SCHEMA,
                "coherent": {"enum": ["PASS", "FAIL"]},
                "source_relevant": {"enum": ["PASS", "FAIL", "NOT_APPLICABLE"]},
                "objective_consistent": {"enum": ["PASS", "FAIL"]},
                "privacy_safe": {"enum": ["PASS", "FAIL"]},
                "non_repetitive": {"enum": ["PASS", "FAIL"]},
            },
        }},
    },
}
_COUNT_SCHEMA = {"type": "integer", "minimum": 0}
_DIMENSION_COUNT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["pass", "fail", "not_applicable"],
    "properties": {"pass": _COUNT_SCHEMA, "fail": _COUNT_SCHEMA, "not_applicable": _COUNT_SCHEMA},
}
AGGREGATE_JSON_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": sorted(AGGREGATE_FIELDS),
    "properties": {
        "schema_version": {"const": AGGREGATE_SCHEMA}, "reviewer_task_id": _ID_SCHEMA,
        "evidence_manifest_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "private_checklist_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        **{name: _COUNT_SCHEMA for name in (
            "population_count", "responsive_population_count", "source_relevant_applicable_count",
            "linkage_failure_count", "missing_count", "corrupt_count", "duplicate_count",
            "normalization_duplicate_count",
        )},
        "dimension_counts": {"type": "object", "additionalProperties": False,
            "required": list(DIMENSIONS), "properties": {name: {
                **_DIMENSION_COUNT_SCHEMA,
                "properties": {**_DIMENSION_COUNT_SCHEMA["properties"],
                    "not_applicable": _COUNT_SCHEMA if name == "source_relevant" else {"const": 0, "type": "integer"}},
            } for name in DIMENSIONS}},
        "human_quality_pass": {"type": "boolean"},
    },
}


_CATEGORIES = frozenset({"input", "evidence", "linkage", "review", "platform", "output", "internal"})
_CODES = frozenset({
    "checklist_invalid", "path_invalid", "path_missing", "path_not_private",
    "unsupported_platform", "json_invalid", "schema_closed", "manifest_missing",
    "manifest_hash_mismatch", "artifact_missing", "artifact_hash_mismatch",
    "visibility_mismatch", "population_missing", "population_exceeded",
    "correlation_missing", "correlation_duplicate", "correlation_mismatch",
    "checklist_population_mismatch", "legacy_quality_dimension_failed",
    "aggregate_write_failed", "unexpected_failure",
})


class ReviewFailure(ValueError):
    def __init__(self, category: str, code: str) -> None:
        if category not in _CATEGORIES or code not in _CODES:
            raise ValueError("invalid closed review failure identifier")
        super().__init__(category, code)
        self.category = category
        self.code = code


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReviewFailure("input", "json_invalid")
        value[key] = item
    return value


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA256_HEX_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _bounded_id(value: object) -> bool:
    try:
        return isinstance(value, str) and 0 < len(value) <= 128 and len(value.encode("utf-8", errors="strict")) <= 512
    except UnicodeError:
        return False


def _plain_absolute(path: Path, *, must_exist: bool) -> Path:
    if not path.is_absolute():
        raise ReviewFailure("input", "path_invalid")
    absolute = path.absolute()
    if any(part in {".", ".."} or ":" in part or part.endswith((".", " ")) for part in absolute.parts[1:]):
        raise ReviewFailure("input", "path_invalid")
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if not current.exists():
            if must_exist or current != absolute:
                raise ReviewFailure("input", "path_missing")
            break
        metadata = os.lstat(current)
        if os.path.islink(current) or getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT:
            raise ReviewFailure("input", "path_invalid")
    if must_exist:
        if not absolute.is_file() and not absolute.is_dir():
            raise ReviewFailure("input", "path_invalid")
        if os.name != "nt" and stat.S_IMODE(os.stat(absolute).st_mode) & 0o077:
            raise ReviewFailure("input", "path_not_private")
        if os.name == "nt" and not _windows_private_path(absolute):
            raise ReviewFailure("input", "path_not_private")
    return absolute.resolve(strict=must_exist)


def _windows_private_path(path: Path, handle: object = None) -> bool:
    """Accept only paths owned by this process and granting no untrusted SID access."""
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.GetSecurityInfo.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    advapi.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetAce.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p

    def sid_string(pointer: int) -> str:
        target = ctypes.c_void_p()
        if not pointer or not advapi.ConvertSidToStringSidW(ctypes.c_void_p(pointer), ctypes.byref(target)):
            return ""
        try:
            return ctypes.wstring_at(target.value)
        finally:
            kernel.LocalFree(target)

    def token_sid(info_class: int) -> str:
        token = wintypes.HANDLE()
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            return ""
        try:
            needed = wintypes.DWORD()
            advapi.GetTokenInformation(token, info_class, None, 0, ctypes.byref(needed))
            if not 0 < needed.value <= 65536:
                return ""
            buffer = ctypes.create_string_buffer(needed.value)
            if not advapi.GetTokenInformation(token, info_class, buffer, needed, ctypes.byref(needed)):
                return ""
            return sid_string(ctypes.c_void_p.from_buffer(buffer).value or 0)
        finally:
            kernel.CloseHandle(token)

    owner = ctypes.c_void_p(); dacl = ctypes.c_void_p(); descriptor = ctypes.c_void_p()
    getter = advapi.GetNamedSecurityInfoW if handle is None else advapi.GetSecurityInfo
    result = getter(
        str(path) if handle is None else handle, 1, 0x00000001 | 0x00000004,
        ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor),
    )
    if result != 0 or not owner.value or not dacl.value or not descriptor.value:
        return False
    try:
        control = wintypes.WORD(); revision = wintypes.DWORD()
        if not advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            return False
        if not control.value & 0x0004:  # SE_DACL_PRESENT; protection/inheritance is not required.
            return False
        owner_sid = sid_string(owner.value)
        process_sids = {token_sid(1), token_sid(4)}  # TokenUser, TokenOwner
        if not owner_sid or owner_sid not in process_sids:
            return False
        allowed = {owner_sid, "S-1-3-4", "S-1-5-18", "S-1-5-32-544"}
        class ACL(ctypes.Structure):
            _fields_ = [("revision", ctypes.c_ubyte), ("sbz1", ctypes.c_ubyte),
                        ("size", wintypes.WORD), ("ace_count", wintypes.WORD), ("sbz2", wintypes.WORD)]
        acl = ctypes.cast(dacl, ctypes.POINTER(ACL)).contents
        if acl.size < ctypes.sizeof(ACL) or not 0 < acl.ace_count <= acl.size // 4:
            return False
        acl_start, acl_end = dacl.value, dacl.value + acl.size
        # Object/callback/unknown ACE semantics are deliberately unsupported.
        allow_types = {0}
        deny_types = {1}
        for index in range(acl.ace_count):
            ace = ctypes.c_void_p()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                return False
            if not ace.value or ace.value < acl_start or ace.value + 4 > acl_end:
                return False
            ace_type = ctypes.c_ubyte.from_address(ace.value).value
            ace_size = wintypes.WORD.from_address(ace.value + 2).value
            if ace_size < 8 or ace.value + ace_size > acl_end or ace_type not in allow_types | deny_types:
                return False
            sid_offset = 8
            if ace_type in {5, 6, 11, 12}:
                if ace_size < 12:
                    return False
                flags = wintypes.DWORD.from_address(ace.value + 8).value
                if flags & ~0x3:
                    return False
                sid_offset = 12 + (16 if flags & 0x1 else 0) + (16 if flags & 0x2 else 0)
            if sid_offset + 8 > ace_size:
                return False
            sid_count = ctypes.c_ubyte.from_address(ace.value + sid_offset + 1).value
            if ctypes.c_ubyte.from_address(ace.value + sid_offset).value != 1 or sid_count > 15 or sid_offset + 8 + 4 * sid_count != ace_size:
                return False
            sid = sid_string(ace.value + sid_offset)
            if not sid or (ace_type in allow_types and sid not in allowed):
                return False
        return True
    finally:
        kernel.LocalFree(descriptor)


@contextmanager
def _locked_path(path: Path, *, directory: bool = False, create: bool = False):
    """Pin every ancestor against replacement and validate the opened object itself."""
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        if create:
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ReviewFailure("input", "path_not_private")
            yield fd
        finally:
            os.close(fd)
        return
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
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
            is_dir = not last or directory
            access = 0x00020080  # READ_CONTROL | FILE_READ_ATTRIBUTES
            if last and not is_dir:
                access |= 0x80000000
                if create:
                    access |= 0x40000000 | 0x00010000  # write + handle rename
            handle = kernel.CreateFileW(str(component), access, 3 if is_dir else 1, None,
                1 if create and last else 3, 0x00200000 | (0x02000000 if is_dir else 0), None)
            if handle == ctypes.c_void_p(-1).value:
                error_code = ctypes.get_last_error()
                if error_code == 5:
                    raise ReviewFailure("input", "path_not_private")
                if create:
                    raise ReviewFailure("input", "path_invalid")
                raise ReviewFailure("input", "path_missing" if error_code in {2, 3} else "path_invalid")
            handles.append(handle)
            attributes = (wintypes.DWORD * 2)()
            if not kernel.GetFileInformationByHandleEx(handle, 9, attributes, ctypes.sizeof(attributes)):
                raise ReviewFailure("input", "path_invalid")
            if attributes[0] & _REPARSE_POINT or bool(attributes[0] & 0x10) != is_dir:
                raise ReviewFailure("input", "path_invalid")
            if last and not _windows_private_path(component, handle):
                raise ReviewFailure("input", "path_not_private")
        if directory:
            yield handle
        else:
            fd = msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDWR if create else os.O_RDONLY))
            handles.pop()  # fd now owns the last HANDLE.
            yield fd
    finally:
        if fd is not None:
            os.close(fd)
        for handle in reversed(handles):
            kernel.CloseHandle(handle)


def _read_bytes(path: Path) -> bytes:
    try:
        with _locked_path(path) as descriptor:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or not 0 <= opened.st_size <= MAX_FILE_BYTES:
                raise ReviewFailure("evidence", "artifact_hash_mismatch")
            size = opened.st_size
            chunks: list[bytes] = []
            remaining = size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
                if not chunk:
                    raise ReviewFailure("evidence", "artifact_hash_mismatch")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise ReviewFailure("evidence", "artifact_hash_mismatch")
            payload = b"".join(chunks)
    except ReviewFailure:
        raise
    except PermissionError:
        raise ReviewFailure("input", "path_not_private") from None
    except OSError:
        raise ReviewFailure("evidence", "artifact_missing") from None
    if len(payload) != size:
        raise ReviewFailure("evidence", "artifact_hash_mismatch")
    return payload


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    payload = _read_bytes(path)
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except ReviewFailure:
        raise
    except (UnicodeError, json.JSONDecodeError):
        raise ReviewFailure("input", "json_invalid") from None
    if not isinstance(value, dict):
        raise ReviewFailure("input", "json_invalid")
    return value, payload


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], bytes]:
    payload = _read_bytes(path)
    records: list[dict[str, Any]] = []
    try:
        for line in payload.decode("utf-8").splitlines():
            if not line:
                continue
            value = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
            if not isinstance(value, dict):
                raise ReviewFailure("input", "json_invalid")
            records.append(value)
    except ReviewFailure:
        raise
    except (UnicodeError, json.JSONDecodeError):
        raise ReviewFailure("input", "json_invalid") from None
    return records, payload


def _invalid_constant(value: str) -> None:
    raise ReviewFailure("input", "json_invalid")


def _manifest_artifact(run_dir: Path, entry: object) -> tuple[Path, list[dict[str, Any]], bytes]:
    if not isinstance(entry, dict) or set(entry) != {"path", "bytes", "sha256", "record_count", "terminal"}:
        raise ReviewFailure("input", "schema_closed")
    relative = entry.get("path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or Path(relative).drive:
        raise ReviewFailure("input", "path_invalid")
    target = run_dir / relative
    if any(part in {"", ".", ".."} for part in Path(relative).parts):
        raise ReviewFailure("input", "path_invalid")
    target = _plain_absolute(target, must_exist=True)
    if target.parent != run_dir and run_dir not in target.parents:
        raise ReviewFailure("input", "path_invalid")
    records, payload = _read_jsonl(target)
    if (
        type(entry.get("bytes")) is not int
        or entry["bytes"] != len(payload)
        or not _is_sha256(entry.get("sha256"))
        or entry["sha256"] != hashlib.sha256(payload).hexdigest()
        or type(entry.get("record_count")) is not int
        or entry["record_count"] != len(records)
        or entry.get("terminal") is not True
    ):
        raise ReviewFailure("evidence", "artifact_hash_mismatch")
    return target, records, payload


def _validate_checklist(value: Mapping[str, Any]) -> None:
    if not Draft202012Validator(CHECKLIST_JSON_SCHEMA).is_valid(value):
        raise ReviewFailure("input", "checklist_invalid")
    if not _bounded_id(value.get("reviewer_task_id")) or not _is_sha256(value.get("evidence_manifest_sha256")):
        raise ReviewFailure("input", "checklist_invalid")
    records = value.get("records")
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ReviewFailure("input", "checklist_invalid")
    for record in records:
        if not isinstance(record, dict) or set(record) != RECORD_FIELDS or not _bounded_id(record.get("capture_id")):
            raise ReviewFailure("input", "checklist_invalid")
        for dimension in DIMENSIONS:
            allowed = {"PASS", "FAIL", "NOT_APPLICABLE"} if dimension == "source_relevant" else {"PASS", "FAIL"}
            if type(record.get(dimension)) is not str or record[dimension] not in allowed:
                raise ReviewFailure("input", "checklist_invalid")


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def _population(run_dir: Path, ai_dir: Path, manifest: Mapping[str, Any], server_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    if set(manifest) != {
        "schema", "player_to_opaque_client_id", "shards", "metadata", "terminal",
        "game_id", "accepted_text", "semantic_counts",
    } or manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("terminal") is not True:
        raise ReviewFailure("input", "schema_closed")
    game_id = manifest.get("game_id")
    if not _bounded_id(game_id) or ai_dir.parent.name != game_id or ai_dir.name != "ai":
        raise ReviewFailure("linkage", "correlation_mismatch")
    if server_result.get("success") is not True or server_result.get("game_end") is not True:
        raise ReviewFailure("linkage", "correlation_mismatch")
    mapping = manifest.get("player_to_opaque_client_id")
    shards = manifest.get("shards")
    if not isinstance(mapping, dict) or not isinstance(shards, dict) or len(mapping) != 9 or set(mapping) != set(shards):
        raise ReviewFailure("evidence", "artifact_missing")
    if not all(_bounded_id(k) and _bounded_id(v) for k, v in mapping.items()) or len(set(mapping.values())) != 9:
        raise ReviewFailure("input", "schema_closed")
    accepted_records = _manifest_artifact(ai_dir, manifest.get("accepted_text"))[1]
    # Metadata is a required manifest reference even though human review consumes no metrics.
    _manifest_artifact(ai_dir, manifest.get("metadata"))
    shard_values: dict[str, list[dict[str, Any]]] = {}
    paths = {manifest["accepted_text"]["path"], manifest["metadata"]["path"]}
    if len(paths) != 2:
        raise ReviewFailure("linkage", "correlation_duplicate")
    for player_id, entry in shards.items():
        path, records, _ = _manifest_artifact(ai_dir, entry)
        if entry["path"] in paths:
            raise ReviewFailure("linkage", "correlation_duplicate")
        paths.add(entry["path"])
        if path.parent != ai_dir / mapping[player_id]:
            raise ReviewFailure("linkage", "correlation_mismatch")
        shard_values[player_id] = records
    receipts = server_result.get("accepted_text")
    if not isinstance(receipts, list):
        raise ReviewFailure("evidence", "artifact_missing")
    try:
        from scripts.run_phase5_local_smoke import _phase6_semantic_population
        rebuilt, machine_summary = _phase6_semantic_population(shard_values, receipts)
    except Exception as exc:
        from scripts.run_phase5_local_smoke import Phase6PopulationExceeded
        if isinstance(exc, Phase6PopulationExceeded):
            raise ReviewFailure("evidence", "population_exceeded") from None
        if isinstance(exc, ValueError) and str(exc) == "chat terminal visibility mismatch":
            raise ReviewFailure("evidence", "visibility_mismatch") from None
        if isinstance(exc, ValueError) and str(exc) in {
            "accepted server population missing or ambiguous",
            "accepted text population incomplete",
        }:
            raise ReviewFailure("evidence", "population_missing") from None
        if isinstance(exc, (ImportError, KeyError, TypeError, ValueError, AttributeError)):
            raise ReviewFailure("linkage", "correlation_mismatch") from None
        raise
    if (len(rebuilt) > MAX_RECORDS or rebuilt != accepted_records
            or _canonical_sha256(manifest.get("semantic_counts")) != _canonical_sha256(machine_summary)):
        raise ReviewFailure("linkage", "correlation_mismatch")
    if any(record.get("game_id") != game_id for records in shard_values.values() for record in records):
        raise ReviewFailure("linkage", "correlation_mismatch")
    population: list[dict[str, Any]] = []
    generation_by_digest: dict[str, dict[str, Any]] = {}
    for records in shard_values.values():
        for record in records:
            if record.get("schema_version") in GENERATION_SCHEMAS:
                digest = _canonical_sha256(record)
                if digest in generation_by_digest:
                    raise ReviewFailure("linkage", "correlation_duplicate")
                generation_by_digest[digest] = record
    for row in rebuilt:
        generation = generation_by_digest.get(row.get("generation_sha256"))
        decision = generation.get("decision") if isinstance(generation, dict) else None
        if not isinstance(decision, dict) or not isinstance(decision.get("text"), str):
            raise ReviewFailure("linkage", "correlation_mismatch")
        population.append({**row, "text": decision["text"]})
    return population


def _empty_counts() -> dict[str, dict[str, int]]:
    return {dimension: {"pass": 0, "fail": 0, "not_applicable": 0} for dimension in DIMENSIONS}


def _aggregate(checklist: Mapping[str, Any], checklist_sha: str, population: Sequence[Mapping[str, Any]], failure: str | None = None) -> dict[str, Any]:
    counts = _empty_counts()
    linkage = missing = corrupt = duplicate = normalization_duplicates = 0
    if failure in {"correlation_missing", "correlation_mismatch", "checklist_population_mismatch"}: linkage = 1
    elif failure in {"artifact_missing", "manifest_missing", "population_missing", "path_missing"}: missing = 1
    elif failure in {"correlation_duplicate"}: duplicate = 1
    elif failure is not None: corrupt = 1
    responsive_count = sum(row.get("responsive") is True for row in population)
    applicable_count = sum(row.get("source_relevant_applicable") is True for row in population)
    human_pass = failure is None and bool(population)
    records = checklist.get("records", [])
    if failure is None:
        capture_ids = [row["capture_id"] for row in population]
        checklist_ids = [row.get("capture_id") for row in records]
        if len(checklist_ids) != len(set(checklist_ids)):
            duplicate = 1
            human_pass = False
        elif checklist_ids != capture_ids:
            if set(capture_ids) - set(checklist_ids):
                missing = len(set(capture_ids) - set(checklist_ids))
            else:
                linkage = 1
            human_pass = False
        else:
            normalized_counts = Counter(_normalize_text(str(row["text"])) for row in population)
            previous_by_player: dict[str, str] = {}
            for source, review in zip(population, records, strict=True):
                normalized = _normalize_text(str(source["text"]))
                repeated = previous_by_player.get(str(source["player_id"])) == normalized or normalized_counts[normalized] > 2
                previous_by_player[str(source["player_id"])] = normalized
                expected_non_repetitive = "FAIL" if repeated else "PASS"
                normalization_duplicates += int(repeated)
                if review["non_repetitive"] != expected_non_repetitive:
                    linkage += 1
                    human_pass = False
                for dimension in DIMENSIONS:
                    result = review[dimension]
                    counts[dimension][result.lower()] += 1
                if source["source_relevant_applicable"] != (review["source_relevant"] != "NOT_APPLICABLE"):
                    linkage += 1
                    human_pass = False
            if normalization_duplicates:
                human_pass = False
            human_pass = human_pass and all(
                counts[dimension]["fail"] == 0
                for dimension in ("privacy_safe", "non_repetitive")
            )
    return {
        "schema_version": AGGREGATE_SCHEMA,
        "reviewer_task_id": checklist.get("reviewer_task_id", "UNKNOWN"),
        "evidence_manifest_sha256": checklist.get("evidence_manifest_sha256", "0" * 64),
        "private_checklist_sha256": checklist_sha,
        "population_count": len(population),
        "responsive_population_count": responsive_count,
        "source_relevant_applicable_count": applicable_count,
        "dimension_counts": counts,
        "linkage_failure_count": linkage,
        "missing_count": missing,
        "corrupt_count": corrupt,
        "duplicate_count": duplicate,
        "normalization_duplicate_count": normalization_duplicates,
        "human_quality_pass": human_pass,
    }


def _write_aggregate(path: Path, value: Mapping[str, Any]) -> None:
    if not Draft202012Validator(AGGREGATE_JSON_SCHEMA).is_valid(value) or path.exists():
        raise ReviewFailure("output", "aggregate_write_failed")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    temporary = path.with_name(path.name + ".tmp")
    with _locked_path(path.parent, directory=True), _locked_path(temporary, create=True) as fd:
        if os.write(fd, payload) != len(payload):
            raise ReviewFailure("output", "aggregate_write_failed")
        os.fsync(fd)
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            import msvcrt
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            kernel.SetFileInformationByHandle.restype = wintypes.BOOL
            class RenameInfo(ctypes.Structure):
                _fields_ = [("replace", wintypes.BOOL), ("root", wintypes.HANDLE),
                    ("length", wintypes.DWORD), ("name", wintypes.WCHAR * 1)]
            name = str(path).encode("utf-16-le")
            # FileName is a NUL-terminated WCHAR string; length excludes its terminator.
            buffer = ctypes.create_string_buffer(max(ctypes.sizeof(RenameInfo), RenameInfo.name.offset + len(name) + 2))
            info = RenameInfo.from_buffer(buffer)
            info.replace = False
            info.root = None
            info.length = len(name)
            ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.name.offset, name, len(name))
            if not kernel.SetFileInformationByHandle(msvcrt.get_osfhandle(fd), 3, buffer, len(buffer)):
                raise ReviewFailure("output", "aggregate_write_failed")
            kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
            kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
            resolved = ctypes.create_unicode_buffer(32768)
            length = kernel.GetFinalPathNameByHandleW(msvcrt.get_osfhandle(fd), resolved, len(resolved), 0)
            if not 0 < length < len(resolved):
                raise ReviewFailure("output", "aggregate_write_failed")
            actual = resolved.value.removeprefix("\\\\?\\")
            if actual.casefold() != str(path).casefold():
                raise ReviewFailure("output", "aggregate_write_failed")
            os.lseek(fd, 0, os.SEEK_SET)
            if os.fstat(fd).st_size != len(payload) or os.read(fd, len(payload) + 1) != payload:
                raise ReviewFailure("output", "aggregate_write_failed")
        else:
            os.link(temporary, path, follow_symlinks=False)
            temporary.unlink()


def process(run_dir: Path, checklist_path: Path, output_path: Path) -> dict[str, Any]:
    if os.name != "nt" or sys.version_info < (3, 13):
        raise ReviewFailure("platform", "unsupported_platform")
    run_dir = _plain_absolute(run_dir, must_exist=True)
    if not run_dir.is_dir():
        raise ReviewFailure("input", "path_invalid")
    checklist_path = _plain_absolute(checklist_path, must_exist=True)
    output_path = _plain_absolute(output_path, must_exist=False)
    output_parent = _plain_absolute(output_path.parent, must_exist=True)
    if output_parent == run_dir or run_dir in output_parent.parents:
        raise ReviewFailure("input", "path_invalid")
    with ExitStack() as stack:
        for path in (run_dir, output_parent, checklist_path.parent):
            stack.enter_context(_locked_path(path, directory=True))
        return _process_locked(run_dir, checklist_path, output_path)


def _process_locked(run_dir: Path, checklist_path: Path, output_path: Path) -> dict[str, Any]:
    checklist, checklist_bytes = _read_json(checklist_path)
    _validate_checklist(checklist)
    checklist_sha = hashlib.sha256(checklist_bytes).hexdigest()
    population: list[dict[str, Any]] = []
    failure: ReviewFailure | None = None
    try:
        server_result, _ = _read_json(_plain_absolute(run_dir / "server.result.json", must_exist=True))
        candidates = []
        for index, child in enumerate(run_dir.iterdir()):
            if index >= 128:
                raise ReviewFailure("input", "schema_closed")
            metadata = os.lstat(child)
            if os.path.islink(child) or getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT:
                raise ReviewFailure("input", "schema_closed")
            candidate = child / "ai" / "manifest.json"
            if candidate.exists():
                candidates.append(candidate)
        if len(candidates) != 1:
            raise ReviewFailure("evidence", "manifest_missing")
        manifest_path = _plain_absolute(candidates[0], must_exist=True)
        ai_dir = manifest_path.parent
        manifest, manifest_bytes = _read_json(manifest_path)
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        if checklist["evidence_manifest_sha256"] != manifest_sha:
            raise ReviewFailure("input", "manifest_hash_mismatch")
        population = _population(run_dir, ai_dir, manifest, server_result)
        if not population:
            raise ReviewFailure("evidence", "population_missing")
    except ReviewFailure as exc:
        failure = exc
    except (ValueError, TypeError, KeyError, RecursionError):
        failure = ReviewFailure("input", "schema_closed")
    aggregate = _aggregate(
        checklist, checklist_sha, population,
        failure.code if failure is not None else None,
    )
    _write_aggregate(output_path, aggregate)
    if not aggregate["human_quality_pass"]:
        if failure is not None:
            raise failure
        if any(aggregate[name] for name in (
            "linkage_failure_count", "missing_count", "duplicate_count"
        )):
            raise ReviewFailure("linkage", "checklist_population_mismatch")
        raise ReviewFailure("review", "legacy_quality_dimension_failed")
    return aggregate


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--checklist", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        process(args.run_dir, args.checklist, args.output)
    except ReviewFailure as exc:
        print(
            f"phase6_private_review_failed category={exc.category} code={exc.code}",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "phase6_private_review_failed category=internal code=unexpected_failure",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
