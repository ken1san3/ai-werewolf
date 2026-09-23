"""Private, hash-bound persistence for one derived-grounding validation result."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

from scripts import phase6_minimal_output_probe as probe


VERSION = "derived-grounding-private-snapshot.v1"
PURPOSES = frozenset(("UTTERANCE", "OPINION_CURRENT", "REACTION", "PRE_VOTE"))


class SnapshotError(ValueError):
    """Fixed failure for private snapshot write/read/binding failures."""

    def __init__(self):
        super().__init__("PRIVATE_EVIDENCE_ERROR")


@dataclass(frozen=True)
class PrivateSnapshotLocatorV1:
    path: Path
    content_sha256: str


def _fail():
    raise SnapshotError()


def _exclusive(path: Path, raw: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != raw:
            _fail()
    except FileExistsError:
        raise
    except (OSError, SnapshotError):
        _fail()


def _probe_value(value: probe.ProbeResult) -> dict:
    if not isinstance(value, probe.ProbeResult):
        _fail()
    return {
        "applicability": value.applicability,
        "raw_sha256": value.raw_sha256,
        "input_sha256": value.input_sha256,
        "private_before_sha256": value.private_before_sha256,
        "private_after_sha256": value.private_after_sha256,
        "semantic_status": value.semantic_status,
    }


def _payload(case_id: str, raw_sha256: str, input_sha256: str, schema_sha256: str,
             validation: probe.ValidationResultV1) -> dict:
    # input_sha256 binds the complete provider request; ProbeResult separately binds
    # the canonical user bytes. They are intentionally different identities.
    if (case_id not in probe.CASE_IDS or not isinstance(validation, probe.ValidationResultV1)
            or validation.probe_result.raw_sha256 != raw_sha256):
        _fail()
    if any(type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
           for value in (raw_sha256, input_sha256, schema_sha256)):
        _fail()
    derived, seen = [], set()
    for item in validation.derived_grounding:
        if (not isinstance(item, probe.DerivedGroundingItemV1) or item.purpose not in PURPOSES
                or (item.purpose, item.ref_key) in seen):
            _fail()
        seen.add((item.purpose, item.ref_key))
        try:
            ref = probe._strict_json(item.canonical_ref_value)
        except Exception:
            _fail()
        if probe._ref_key(ref) != item.ref_key or probe.canonical_bytes(ref) != item.canonical_ref_value:
            _fail()
        derived.append({"purpose": item.purpose, "ref_key": list(item.ref_key), "canonical_ref_value": ref})
    return {
        "version": VERSION,
        "case_id": case_id,
        "raw_sha256": raw_sha256,
        "input_sha256": input_sha256,
        "schema_sha256": schema_sha256,
        "probe_result": _probe_value(validation.probe_result),
        "derived_grounding": derived,
    }


def write_validation_snapshot(private_dir: Path, *, case_id: str, raw_sha256: str,
                              input_sha256: str, schema_sha256: str,
                              validation: probe.ValidationResultV1) -> PrivateSnapshotLocatorV1:
    """Create one immutable snapshot and its private-only content-hash locator."""
    private_dir = Path(private_dir)
    payload = _payload(case_id, raw_sha256, input_sha256, schema_sha256, validation)
    raw = probe.canonical_bytes(payload)
    content_sha256 = hashlib.sha256(raw).hexdigest()
    path = private_dir / f"{case_id}.validation-snapshot.json"
    locator_path = private_dir / f"{case_id}.validation-snapshot.locator.json"
    try:
        _exclusive(path, raw)
        _exclusive(locator_path, probe.canonical_bytes({
            "version": VERSION,
            "case_id": case_id,
            "filename": path.name,
            "content_sha256": content_sha256,
        }))
    except FileExistsError:
        _fail()
    return PrivateSnapshotLocatorV1(path, content_sha256)


def read_validation_snapshot(private_dir: Path, *, case_id: str, raw_sha256: str,
                             input_sha256: str, schema_sha256: str,
                             expected_probe_result: probe.ProbeResult) -> probe.ValidationResultV1:
    """Verify every binding and rehydrate; never derive refs from candidate raw again."""
    if case_id not in probe.CASE_IDS:
        _fail()
    private_dir = Path(private_dir)
    try:
        locator_raw = (private_dir / f"{case_id}.validation-snapshot.locator.json").read_bytes()
        locator = probe._strict_json(locator_raw)
        expected_locator_keys = {"version", "case_id", "filename", "content_sha256"}
        if (set(locator) != expected_locator_keys or locator["version"] != VERSION
                or locator["case_id"] != case_id
                or locator["filename"] != f"{case_id}.validation-snapshot.json"):
            _fail()
        snapshot_raw = (private_dir / locator["filename"]).read_bytes()
        if hashlib.sha256(snapshot_raw).hexdigest() != locator["content_sha256"]:
            _fail()
        value = probe._strict_json(snapshot_raw)
        if probe.canonical_bytes(value) != snapshot_raw:
            _fail()
    except Exception:
        _fail()
    expected_keys = {"version", "case_id", "raw_sha256", "input_sha256", "schema_sha256",
                     "probe_result", "derived_grounding"}
    if (set(value) != expected_keys or value["version"] != VERSION or value["case_id"] != case_id
            or value["raw_sha256"] != raw_sha256 or value["input_sha256"] != input_sha256
            or value["schema_sha256"] != schema_sha256
            or value["probe_result"] != _probe_value(expected_probe_result)
            or type(value["derived_grounding"]) is not list):
        _fail()
    items = []
    for encoded in value["derived_grounding"]:
        if type(encoded) is not dict or set(encoded) != {"purpose", "ref_key", "canonical_ref_value"}:
            _fail()
        ref = encoded["canonical_ref_value"]
        key = tuple(encoded["ref_key"]) if type(encoded["ref_key"]) is list else ()
        try:
            canonical = probe.canonical_bytes(ref)
            if probe._ref_key(ref) != key:
                _fail()
        except Exception:
            _fail()
        items.append(probe.DerivedGroundingItemV1(encoded["purpose"], key, canonical))
    result = probe.ValidationResultV1(expected_probe_result, tuple(items))
    if _payload(case_id, raw_sha256, input_sha256, schema_sha256, result) != value:
        _fail()
    return result
