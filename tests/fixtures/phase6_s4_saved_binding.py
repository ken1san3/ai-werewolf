"""Pure test-only saved-row connector for projected S4."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sys
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.fixtures.phase6_s4_projected_ability_provenance import (
    canonical_sha, canonical_wire, strict_parse, freeze_projected_ability_trust,
    project_selected_abilities, merge_common_provenance_v2,
    decide_projected_ability_s4, ProjectedAbilityInputError,
)
from scripts import phase6_quality_probe_v2 as q

CONTRACT = "S4_PROJECTED_ABILITY_SAVED_BINDING_V1"
PROJECTED_CONTRACT = "S4_PROJECTED_ABILITY_PROVENANCE_V1"
_SOURCE_ID = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")


class SavedBindingError(ValueError):
    DETAILS = {"SHAPE", "JSON", "HASH", "SURFACE", "COUNT", "DUPLICATE", "ROOT", "ANNOTATION", "STATE"}

    def __init__(self, detail: str):
        self.detail = detail if detail in self.DETAILS else "SHAPE"
        super().__init__("SAVED_BINDING_INPUT_INTEGRITY")


def _fail(detail: str) -> None:
    raise SavedBindingError(detail)


def _exact(value: Any, keys: tuple[str, ...], detail: str = "SHAPE") -> dict:
    if type(value) is not dict or len(value) != len(keys) or set(value) != set(keys):
        _fail(detail)
    return value


def _string(value: Any, detail: str = "SHAPE") -> str:
    if type(value) is not str or not value:
        _fail(detail)
    return value


def _digest(value: Any) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        _fail("HASH")
    return value


def validate_source_manifest(*, root: Path, manifest: dict) -> dict[str, bytes]:
    _exact(manifest, ("contract", "files", "root_snapshot_sha256", "manifest_sha256"), "ROOT")
    if manifest["contract"] != CONTRACT or type(manifest["files"]) is not list:
        _fail("ROOT")
    files = manifest["files"]
    ids = []
    previous = None
    loaded = {}
    for entry in files:
        _exact(entry, ("source_id", "original_name", "sha256", "size"), "ROOT")
        source_id = entry["source_id"]
        if type(source_id) is not str or _SOURCE_ID.fullmatch(source_id) is None:
            _fail("ROOT")
        name = _string(entry["original_name"], "ROOT")
        path_name = Path(name)
        if path_name.is_absolute() or ".." in path_name.parts or "\\" in name:
            _fail("ROOT")
        _digest(entry["sha256"])
        if type(entry["size"]) is not int or type(entry["size"]) is bool or entry["size"] < 0:
            _fail("ROOT")
        if previous is not None and name <= previous:
            _fail("ROOT")
        previous = name; ids.append(source_id)
        path = root / "sources" / source_id
        if not path.is_file() or path.is_symlink():
            _fail("ROOT")
        data = path.read_bytes()
        if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            _fail("HASH")
        loaded[source_id] = data
    if len(ids) != len(set(ids)):
        _fail("DUPLICATE")
    root_payload = {"contract": CONTRACT, "files": files}
    if canonical_sha(root_payload) != manifest["root_snapshot_sha256"]:
        _fail("ROOT")
    manifest_payload = {"contract": CONTRACT, "root_snapshot_sha256": manifest["root_snapshot_sha256"], "files": files}
    if canonical_sha(manifest_payload) != manifest["manifest_sha256"]:
        _fail("ROOT")
    return loaded


def _strict_text(raw: str) -> dict:
    if type(raw) is not str:
        _fail("JSON")
    try:
        encoded = raw.encode("utf-8")
        return _parse_json_bytes(encoded)
    except (UnicodeEncodeError, ValueError):
        raise SavedBindingError("JSON") from None


def _parse_json_any(raw: bytes) -> Any:
    if type(raw) is not bytes:
        _fail("JSON")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail("JSON")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: _fail("JSON"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SavedBindingError("JSON") from None
    return value


def _parse_json_bytes(raw: bytes) -> dict:
    value=_parse_json_any(raw)
    if type(value) is not dict: _fail("JSON")
    return value


def _typed_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right): return False
    if type(left) is dict:
        return list(left)==list(right) and all(_typed_equal(left[k],right[k]) for k in left)
    if type(left) is list:
        return len(left)==len(right) and all(_typed_equal(a,b) for a,b in zip(left,right))
    return left==right


def _surface(kind: str, text: str | None, comment: str | None, claims: list[dict]) -> dict:
    return {"kind": kind, "text": text, "comment": comment, "claims": claims}


def extract_baseline_final(*, wrapper_bytes: bytes, snapshot_file_sha256: str,
                           saved_surface: dict) -> dict:
    if hashlib.sha256(wrapper_bytes).hexdigest() != _digest(snapshot_file_sha256):
        _fail("HASH")
    try:
        wrapper = _parse_json_bytes(wrapper_bytes)
    except ValueError:
        raise SavedBindingError("JSON") from None
    _exact(wrapper, ("final_content", "final_output_sha256"), "SHAPE")
    content = wrapper["final_content"]
    if type(content) is not str:
        _fail("SHAPE")
    try:
        member = content.encode("utf-8")
    except UnicodeEncodeError:
        raise SavedBindingError("JSON") from None
    member_sha = hashlib.sha256(member).hexdigest()
    if wrapper["final_output_sha256"] != member_sha:
        _fail("HASH")
    parsed = _strict_text(content)
    decision = parsed.get("decision")
    discussion = parsed.get("discussion")
    if decision is not None and type(decision) is not dict:
        _fail("SHAPE")
    if discussion is not None and type(discussion) is not dict:
        _fail("SHAPE")
    message = None if decision is None else decision.get("message")
    if message is not None and (type(message) is not str or not message):
        _fail("SHAPE")
    co = None if discussion is None else discussion.get("co_judgment")
    if co is not None and type(co) is not dict:
        _fail("SHAPE")
    claims = []
    if co is not None and co.get("decision") == "DECLARE":
        option = _string(co.get("selected_option_id"))
        role = _string(co.get("claimed_role_id"))
        claims.append({"decision": "DECLARE", "option": option, "claimed_role": role})
    kind = "TEXT_AND_STRUCTURED" if message and claims else "TEXT" if message else "STRUCTURED" if claims else "ABSENT"
    projected = _surface(kind, message, None, claims)
    if projected != saved_surface:
        _fail("SURFACE")
    return {"accepted_final_sha256": member_sha, "parsed_final": deepcopy(parsed),
            "member_object_sha256": member_sha, "snapshot_file_sha256": snapshot_file_sha256,
            "json_pointer": "/final_content", "encoding": "JSON_STRING_UTF8"}


def extract_candidate_final(*, response_bytes: bytes, snapshot_file_sha256: str,
                            saved_final: dict, saved_surface: dict) -> dict:
    if hashlib.sha256(response_bytes).hexdigest() != _digest(snapshot_file_sha256):
        _fail("HASH")
    try:
        response = _parse_json_bytes(response_bytes)
        choices = response["choices"]
        if type(choices) is not list or len(choices) != 1:
            _fail("SHAPE")
        message = choices[0]["message"]
        if type(message) is not dict or "content" not in message:
            _fail("SHAPE")
        content = message["content"]
        parsed = _strict_text(content)
    except (KeyError, TypeError, ValueError):
        raise SavedBindingError("JSON") from None
    if not _typed_equal(parsed, saved_final):
        _fail("SHAPE")
    if "message" in parsed:
        text = parsed["message"]
        if type(text) is not str or not text:
            _fail("SHAPE")
        projected = _surface("TEXT", text, None, [])
    elif parsed.get("decision") == "DECLARE":
        option = _string(parsed.get("co_option_id"))
        role = _string(parsed.get("claimed_role_option_id"))
        comment = parsed.get("comment")
        if comment is not None and (type(comment) is not str or not comment):
            _fail("SHAPE")
        claim = {"decision": "DECLARE", "option": option, "claimed_role": role}
        projected = _surface("TEXT_AND_STRUCTURED" if comment else "STRUCTURED", None, comment, [claim])
    else:
        projected = _surface("ABSENT", None, None, [])
    if projected != saved_surface:
        _fail("SURFACE")
    member = content.encode("utf-8")
    member_sha = hashlib.sha256(member).hexdigest()
    return {"accepted_final_sha256": member_sha, "parsed_final": deepcopy(parsed),
            "member_object_sha256": member_sha, "snapshot_file_sha256": snapshot_file_sha256,
            "json_pointer": "/choices/0/message/content", "encoding": "JSON_STRING_UTF8"}


def build_candidate_manifest(*, preflight_sha256: str, rows: list[dict]) -> dict:
    _digest(preflight_sha256)
    if type(rows) is not list or len(rows) != 192:
        _fail("COUNT")
    keys = ("evaluation_id", "old_row_binding_sha256", "source_witness_sha256",
            "projected_row_sha256", "custodian_freeze_sha256", "status")
    seen = set()
    checked = []
    for value in rows:
        row = deepcopy(_exact(value, keys))
        eid = _string(row["evaluation_id"])
        if eid in seen:
            _fail("DUPLICATE")
        seen.add(eid)
        _digest(row["old_row_binding_sha256"]); _digest(row["source_witness_sha256"])
        status = row["status"]
        if status not in ("READY", "MEASUREMENT_NOT_OBSERVED", "SAVED_ACCEPTED_FINAL_UNAVAILABLE", "INPUT_INVALID"):
            _fail("SHAPE")
        ready = status == "READY"
        if ready:
            _digest(row["projected_row_sha256"]); _digest(row["custodian_freeze_sha256"])
        elif row["projected_row_sha256"] is not None or row["custodian_freeze_sha256"] is not None:
            _fail("SHAPE")
        checked.append(row)
    result = {"contract": CONTRACT, "preflight_sha256": preflight_sha256, "rows": checked}
    result["manifest_sha256"] = canonical_sha(result)
    return result


def _validate_candidate_manifest(value: Any) -> dict:
    manifest = _exact(value, ("contract", "preflight_sha256", "rows", "manifest_sha256"), "ROOT")
    if manifest["contract"] != CONTRACT or canonical_sha({k: manifest[k] for k in manifest if k != "manifest_sha256"}) != _digest(manifest["manifest_sha256"]):
        _fail("ROOT")
    if type(manifest["rows"]) is not list or len(manifest["rows"]) != 192:
        _fail("COUNT")
    ids = [row.get("evaluation_id") if type(row) is dict else None for row in manifest["rows"]]
    if any(type(value) is not str or not value for value in ids) or len(ids) != len(set(ids)):
        _fail("DUPLICATE")
    return manifest


def validate_main_pin(*, candidate_manifest: dict, main_pin: dict,
                      expected_pin_sha256: str) -> dict[str, str]:
    candidate_manifest = _validate_candidate_manifest(candidate_manifest)
    _exact(main_pin, ("contract", "task", "preflight_sha256", "candidate_manifest_sha256",
                      "rows", "ready_count", "unavailable_count", "unobserved_count", "pin_sha256"), "ROOT")
    if main_pin["contract"] != CONTRACT or main_pin["task"] != "T556":
        _fail("ROOT")
    if main_pin["preflight_sha256"] != candidate_manifest["preflight_sha256"]:
        _fail("ROOT")
    if main_pin["pin_sha256"] != _digest(expected_pin_sha256):
        _fail("ROOT")
    payload = {k: main_pin[k] for k in main_pin if k != "pin_sha256"}
    if canonical_sha(payload) != main_pin["pin_sha256"] or main_pin["candidate_manifest_sha256"] != candidate_manifest["manifest_sha256"]:
        _fail("ROOT")
    ready = [r for r in candidate_manifest["rows"] if r["status"] == "READY"]
    unavailable = [r for r in candidate_manifest["rows"] if r["status"] in ("SAVED_ACCEPTED_FINAL_UNAVAILABLE", "INPUT_INVALID")]
    unobserved = [r for r in candidate_manifest["rows"] if r["status"] == "MEASUREMENT_NOT_OBSERVED"]
    if (main_pin["ready_count"], main_pin["unavailable_count"], main_pin["unobserved_count"]) != (len(ready), len(unavailable), len(unobserved)):
        _fail("COUNT")
    expected_rows = [{"evaluation_id": r["evaluation_id"], "expected_freeze_sha256": r["custodian_freeze_sha256"]} for r in ready]
    if main_pin["rows"] != expected_rows:
        _fail("ROOT")
    return {r["evaluation_id"]: r["expected_freeze_sha256"] for r in main_pin["rows"]}


def validate_annotations(*, candidate_manifest: dict, annotations: list[dict], packet: dict | None = None) -> dict[str, dict]:
    candidate_manifest = _validate_candidate_manifest(candidate_manifest)
    if type(annotations) is not list or len(annotations) != 192:
        _fail("COUNT")
    expected = {r["evaluation_id"]: r["status"] for r in candidate_manifest["rows"]}
    result = {}
    packet_rows = {} if packet is None else {x["evaluation_id"]: x for x in packet["rows"]}
    keys = ("evaluation_id", "status", "assertion", "cited_provenance_ids", "associations")
    for item in annotations:
        value = deepcopy(_exact(item, keys, "ANNOTATION"))
        eid = _string(value["evaluation_id"], "ANNOTATION")
        if eid in result or eid not in expected:
            _fail("DUPLICATE")
        wanted = "ANNOTATED" if expected[eid] == "READY" else "NOT_REQUIRED" if expected[eid] == "MEASUREMENT_NOT_OBSERVED" else "CONNECTOR_SKIPPED"
        if value["status"] != wanted:
            _fail("ANNOTATION")
        if wanted != "ANNOTATED":
            if value["assertion"] is not None or value["cited_provenance_ids"] != [] or value["associations"] != []:
                _fail("ANNOTATION")
        else:
            if value["assertion"] not in ("NONE", "CLAIMED_RESULT", "EXPLICIT_AUTHORITY_ASSERTION", "UNDECIDABLE"):
                _fail("ANNOTATION")
            if type(value["cited_provenance_ids"]) is not list or type(value["associations"]) is not list:
                _fail("ANNOTATION")
            ids=value["cited_provenance_ids"]
            if any(type(x) is not str or not x for x in ids) or len(ids)!=len(set(ids)): _fail("ANNOTATION")
            seen=set()
            for association in value["associations"]:
                _exact(association,("opaque_provenance_id","target","result"),"ANNOTATION")
                pid=_string(association["opaque_provenance_id"],"ANNOTATION")
                if pid in seen: _fail("ANNOTATION")
                seen.add(pid)
                for atom in (association["target"],association["result"]):
                    _exact(atom,("tag","value"),"ANNOTATION")
                    if atom["tag"]=="VALUE": _string(atom["value"],"ANNOTATION")
                    elif atom["tag"] in ("ABSENT","UNDECIDABLE"):
                        if atom["value"] is not None: _fail("ANNOTATION")
                    else: _fail("ANNOTATION")
            if value["assertion"] in ("NONE","UNDECIDABLE") and (ids or value["associations"]): _fail("ANNOTATION")
            if packet is not None:
                known={x["opaque_provenance_id"] for x in packet_rows[eid]["provenance"]}
                if any(x not in known for x in seen): _fail("ANNOTATION")
                if any(x not in known for x in ids): _fail("ANNOTATION")
                if value["assertion"]=="EXPLICIT_AUTHORITY_ASSERTION":
                    refs={x["opaque_provenance_id"] for x in packet_rows[eid]["provenance"] if x["identity_kind"]=="SOURCE_REF"}
                    if not ids or any(x not in refs for x in ids): _fail("ANNOTATION")
        result[eid] = value
    return result


def bind_saved_disclosures(*, old_envelope: dict, accepted_plan: dict,
                           custodian_freeze: dict) -> list[dict]:
    _exact(old_envelope, ("binding", "accepted_plan_sha256", "items"), "SHAPE")
    if type(old_envelope["items"]) is not list or type(accepted_plan) is not dict:
        _fail("SHAPE")
    disclose_ids = accepted_plan.get("disclose_ids")
    if type(disclose_ids) is not list or any(type(x) is not str or not x for x in disclose_ids) or len(disclose_ids) != len(set(disclose_ids)):
        _fail("SHAPE")
    bindings = custodian_freeze.get("bindings") if type(custodian_freeze) is dict else None
    if type(bindings) is not list:
        _fail("SHAPE")
    by_disclose = {item.get("disclose_id"): item for item in bindings if type(item) is dict}
    if len(by_disclose) != len(bindings):
        _fail("DUPLICATE")
    old = []
    for item in old_envelope["items"]:
        if type(item) is dict and item.get("origin") == "SELECTED_DISCLOSURE":
            old.append(item)
    if [item.get("selected_id") for item in old] != disclose_ids:
        _fail("SHAPE")
    output = []
    for item in old:
        selected_id = item["selected_id"]
        edge = by_disclose.get(selected_id)
        if edge is None:
            _fail("SHAPE")
        output.append({"opaque_provenance_id": _string(item.get("provenance_id")),
                       "old_binding_id": _string(item.get("binding_id")),
                       "disclose_id": selected_id,
                       "new_binding_id": _string(edge.get("binding_id"))})
    return output


def validate_saved_row_input(*, row: dict, old_packet: dict,
                             old_mechanical: dict, old_mapping: dict) -> None:
    keys = ("evaluation_id", "old_blind_id", "condition", "case_id", "seed",
            "pair_key_sha256", "execution", "audit", "conversion", "old_binding",
            "old_envelope", "base_records", "accepted_surface", "accepted_plan",
            "fixture_source_sha256", "fixture_bindings_sha256", "fixture_catalog_sha256",
            "fixture_input_source_id", "accepted_final_proof")
    _exact(row, keys, "SHAPE")
    old_id = _string(row["old_blind_id"])
    def indexed(document):
        values = document.get("rows") if type(document) is dict else None
        if type(values) is not list:
            _fail("SHAPE")
        matches = [item for item in values if type(item) is dict and item.get("blind_id") == old_id]
        if len(matches) != 1:
            _fail("SHAPE")
        return matches[0]
    packet = indexed(old_packet); mechanical = indexed(old_mechanical); mapping = indexed(old_mapping)
    expected_pair = canonical_sha({"case_id": row["case_id"], "seed": row["seed"]})
    if row["pair_key_sha256"] != expected_pair:
        _fail("HASH")
    if any(mapping.get(key) != row[key] for key in ("condition", "case_id", "seed")):
        _fail("SHAPE")
    comparisons = (
        (row["execution"], packet.get("execution")),
        (row["audit"], packet.get("sealed_audit")),
        (row["conversion"], packet.get("conversion_status")),
        (row["accepted_surface"], packet.get("surface")),
        (row["old_binding"], mechanical.get("binding")),
        (row["old_envelope"], mechanical.get("envelope")),
        (row["base_records"], mechanical.get("mechanical")),
        (row["accepted_plan"], mechanical.get("plan_view")),
    )
    if any(left != right for left, right in comparisons):
        _fail("SHAPE")
    for key in ("fixture_source_sha256", "fixture_bindings_sha256", "fixture_catalog_sha256"):
        _digest(row[key])
    _string(row["fixture_input_source_id"])


def connector_unknown(*, evaluation_id: str, reason: str) -> dict:
    if reason not in ("SAVED_ACCEPTED_FINAL_UNAVAILABLE", "SAVED_BASE_BINDING_MISMATCH", "CONNECTOR_INPUT_INTEGRITY"):
        _fail("STATE")
    return {"evaluation_id": _string(evaluation_id), "status": "CONNECTOR_UNKNOWN",
            "connector_reason": reason, "projected_decision": None, "metric_value": "UNKNOWN",
            "measurement_validity": "INVALID", "binding": None,
            "offending_provenance_ids": []}


def validate_blind_packet(packet: dict) -> None:
    _exact(packet, ("contract", "rows"), "SHAPE")
    if packet["contract"] != CONTRACT or type(packet["rows"]) is not list or len(packet["rows"]) != 192:
        _fail("COUNT")
    forbidden = {"condition", "profile", "seed", "model", "run_order", "old_blind_id", "source_path"}
    ids = []
    def walk(value):
        if type(value) is dict:
            if set(value) & forbidden:
                _fail("SHAPE")
            for child in value.values(): walk(child)
        elif type(value) is list:
            for child in value: walk(child)
    for row in packet["rows"]:
        _exact(row, ("evaluation_id", "evaluation_status", "execution", "audit", "conversion",
                     "accepted_surface", "provenance", "sealed_selected_values"), "SHAPE")
        ids.append(_string(row["evaluation_id"]))
        if row["evaluation_status"] not in ("READY", "CONNECTOR_SKIPPED", "MEASUREMENT_NOT_OBSERVED"):
            _fail("SHAPE")
        walk(row)
    if len(ids) != len(set(ids)):
        _fail("DUPLICATE")


def wrap_decision(*, evaluation_id: str, decision: dict) -> dict:
    metric = decision.get("metric_value") if type(decision) is dict else None
    if metric not in ("PASS", "FAIL", "UNKNOWN", "MEASUREMENT_NOT_OBSERVED"):
        _fail("STATE")
    status = "MEASUREMENT_NOT_OBSERVED" if metric == "MEASUREMENT_NOT_OBSERVED" else "DECIDED"
    normalized=json.loads(json.dumps(decision,ensure_ascii=False))
    return {"evaluation_id": _string(evaluation_id), "status": status,
            "connector_reason": "NONE", "projected_decision": normalized,
            "metric_value": metric, "measurement_validity": decision["measurement_validity"],
            "binding": deepcopy(decision["binding"]),
            "offending_provenance_ids": list(decision["offending_provenance_ids"])}


def _load(path: Path) -> dict:
    return strict_parse(path.read_bytes())


def _write_once(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical_wire(value))


def _preflight(value: Any, manifest: dict) -> dict:
    keys = ("contract", "permission_decision_sha256", "base_design_sha256", "amendment_sha256",
            "projected_helper_sha256", "connector_source_sha256", "probe_source_sha256",
            "old_packet_sha256", "old_freeze_sha256", "old_mechanical_sha256",
            "old_mapping_sha256", "source_manifest_sha256", "rows", "pairs",
            "accepted_rows", "unobserved_rows", "preflight_sha256")
    value = _exact(value, keys, "ROOT")
    if value["contract"] != CONTRACT or value["source_manifest_sha256"] != manifest["manifest_sha256"]:
        _fail("ROOT")
    for key in keys[1:12]: _digest(value[key])
    if tuple(value[k] for k in ("rows", "pairs", "accepted_rows", "unobserved_rows")) != (192, 96, 179, 13):
        _fail("COUNT")
    if canonical_sha({k: value[k] for k in value if k != "preflight_sha256"}) != _digest(value["preflight_sha256"]):
        _fail("ROOT")
    return value


def _validate_current_sources(preflight: dict) -> None:
    for key,path in (("connector_source_sha256",Path(__file__)),
                     ("projected_helper_sha256",Path(__file__).with_name("phase6_s4_projected_ability_provenance.py")),
                     ("probe_source_sha256",Path(q.__file__))):
        if hashlib.sha256(path.read_bytes()).hexdigest()!=preflight[key]: _fail("HASH")


def _surface_view(surface: dict) -> dict:
    _exact(surface, ("kind", "text", "comment", "claims"), "SURFACE")
    text = surface["text"]
    if surface["comment"] is not None:
        if text is not None: _fail("SURFACE")
        text = surface["comment"]
    claims = []
    for claim in surface["claims"]:
        _exact(claim, ("decision", "option", "claimed_role"), "SURFACE")
        claims.append({"claim_id": canonical_sha(claim), "explicit_refs": []})
    return {"kind": surface["kind"], "text": text, "claims": claims}


def _projected_row(*, old_binding: dict, old_envelope: dict, base_records: list,
                   plan: dict, surface: dict, final_sha: str, freeze: dict) -> tuple[dict, tuple[dict, ...]]:
    view = _surface_view(surface)
    binding = {"rubric_version": PROJECTED_CONTRACT, "blind_id": old_binding["blind_id"],
        "base_row_sha256": old_binding["row_sha256"],
        "base_selection_envelope_sha256": old_binding["selection_envelope_sha256"],
        "base_catalog_sha256": old_binding["catalog_sha256"],
        "base_canonical_set_sha256": old_binding["canonical_set_sha256"],
        "surface_sha256": canonical_sha(view), "accepted_plan_sha256": canonical_sha(plan),
        "accepted_final_sha256": _digest(final_sha), "custodian_freeze_sha256": freeze["freeze_sha256"],
        "catalog_sha256": canonical_sha({"contract": PROJECTED_CONTRACT,
            "base_catalog_sha256": old_binding["catalog_sha256"],
            "catalog_disclose_ids": freeze["catalog_disclose_ids"]}),
        "canonical_binding_set_sha256": canonical_sha({"contract": PROJECTED_CONTRACT,
            "source_sha256": freeze["source_sha256"], "bindings": freeze["bindings"]})}
    binding["row_sha256"] = canonical_sha({"contract": PROJECTED_CONTRACT,
        **{k: binding[k] for k in ("blind_id", "base_row_sha256", "surface_sha256",
            "accepted_plan_sha256", "accepted_final_sha256", "catalog_sha256",
            "canonical_binding_set_sha256", "custodian_freeze_sha256")}})
    witness = {"contract": PROJECTED_CONTRACT, "blind_id": binding["blind_id"],
        "owner_id": freeze["owner_id"], "public_channel_id": freeze["public_channel_id"],
        "source_sha256": freeze["source_sha256"], "owner_pointer": freeze["owner_pointer"],
        "owner_value_sha256": freeze["owner_value_sha256"],
        **{k: binding[k] for k in ("row_sha256", "surface_sha256", "accepted_plan_sha256",
            "accepted_final_sha256", "catalog_sha256", "canonical_binding_set_sha256",
            "custodian_freeze_sha256")}}
    witness["witness_sha256"] = canonical_sha(witness)
    links = bind_saved_disclosures(old_envelope=old_envelope, accepted_plan=plan,
                                   custodian_freeze=freeze)
    by_id = {x["disclose_id"]: x for x in freeze["bindings"]}
    selections = []
    for link in links:
        edge = by_id[link["disclose_id"]]
        selections.append({"blind_id": binding["blind_id"], "disclose_id": edge["disclose_id"],
            "binding_id": edge["binding_id"], "opaque_provenance_id": link["opaque_provenance_id"],
            **{k: binding[k] for k in ("row_sha256", "surface_sha256", "accepted_plan_sha256",
                "accepted_final_sha256", "custodian_freeze_sha256")},
            "source_identity": {"kind": "PROJECTED_ABILITY", **{k: edge[k] for k in
                ("source_sha256", "pointer", "owner_id", "raw_value_sha256", "projected_value_sha256")}}})
    ep = {"contract": PROJECTED_CONTRACT,
        **{k: binding[k] for k in ("blind_id", "row_sha256", "surface_sha256", "accepted_plan_sha256",
            "accepted_final_sha256", "catalog_sha256", "canonical_binding_set_sha256",
            "custodian_freeze_sha256")}, "witness_sha256": witness["witness_sha256"],
        "selection_payloads": deepcopy(selections)}
    envelope_sha = canonical_sha(ep)
    for selection in selections: selection["selection_envelope_sha256"] = envelope_sha
    row = {"binding": binding, "accepted_plan": deepcopy(plan), "accepted_surface": view,
           "witness": witness, "selections": selections, "custodian_freeze": deepcopy(freeze)}
    projected = project_selected_abilities(row=row, expected_freeze_sha256=freeze["freeze_sha256"])
    merged = merge_common_provenance_v2(base_records=tuple(base_records), projected_records=projected)
    return row, merged


def prepare_saved_roots(*, root: Path, source: dict, expected_preflight_sha256: str) -> dict:
    _exact(source, ("contract", "preflight", "source_manifest", "rows"), "ROOT")
    if source["contract"] != CONTRACT: _fail("ROOT")
    loaded = validate_source_manifest(root=root, manifest=source["source_manifest"])
    preflight = _preflight(source["preflight"], source["source_manifest"])
    if preflight["preflight_sha256"] != _digest(expected_preflight_sha256): _fail("ROOT")
    old_packet = _parse_json_bytes(loaded["old_packet_snapshot"])
    old_mechanical = _parse_json_bytes(loaded["old_mechanical_snapshot"])
    old_mapping = _parse_json_bytes(loaded["old_mapping_snapshot"])
    if (hashlib.sha256(loaded["old_packet_snapshot"]).hexdigest() != preflight["old_packet_sha256"]
            or hashlib.sha256(loaded["old_freeze_snapshot"]).hexdigest() != preflight["old_freeze_sha256"]
            or hashlib.sha256(loaded["old_mechanical_snapshot"]).hexdigest() != preflight["old_mechanical_sha256"]
            or hashlib.sha256(loaded["old_mapping_snapshot"]).hexdigest() != preflight["old_mapping_sha256"]): _fail("HASH")
    names={x["original_name"]:x for x in source["source_manifest"]["files"]}
    for name,key,path in (("tests/fixtures/phase6_s4_saved_binding.py","connector_source_sha256",Path(__file__)),
                          ("tests/fixtures/phase6_s4_projected_ability_provenance.py","projected_helper_sha256",Path(__file__).with_name("phase6_s4_projected_ability_provenance.py")),
                          ("scripts/phase6_quality_probe_v2.py","probe_source_sha256",Path(q.__file__))):
        entry=names.get(name)
        if entry is None or entry["sha256"]!=preflight[key] or hashlib.sha256(path.read_bytes()).hexdigest()!=preflight[key]: _fail("HASH")
    fixtures = {f.case.case_id: f for f in q.prepare()[0]}
    fixture_saved = _parse_json_any(loaded["fixture_input_snapshot"])
    if type(fixture_saved) is not list: _fail("SHAPE")
    saved_by = {x.get("case_id"): x for x in fixture_saved if type(x) is dict}
    rows = source["rows"]
    if type(rows) is not list or len(rows) != 192: _fail("COUNT")
    host_rows=[]; manifest_rows=[]; pairs=set(); accepted=0
    probe_sha = hashlib.sha256(Path(q.__file__).read_bytes()).hexdigest()
    for supplied in rows:
        row = deepcopy(supplied); validate_saved_row_input(row=row, old_packet=old_packet,
            old_mechanical=old_mechanical, old_mapping=old_mapping)
        pairs.add(row["pair_key_sha256"]); fixture=fixtures.get(row["case_id"]); saved=saved_by.get(row["case_id"])
        if fixture is None or saved is None: _fail("SHAPE")
        q.verify_bindings(fixture)
        catalog=json.loads(q.wire(asdict(fixture.catalog)))
        if (fixture.source != saved.get("source") or fixture.bindings != saved.get("bindings")
                or catalog != saved.get("catalog") or row["fixture_source_sha256"] != canonical_sha(fixture.source)
                or row["fixture_bindings_sha256"] != canonical_sha(fixture.bindings)
                or row["fixture_catalog_sha256"] != canonical_sha(catalog)): _fail("HASH")
        status = "MEASUREMENT_NOT_OBSERVED"; projected_row=None; merged=None; connector_reason="NONE"
        witness={"evaluation_id":row["evaluation_id"],"old_blind_id":row["old_blind_id"],
            "old_row_binding_sha256":canonical_sha(row["old_binding"]),
            "old_mechanical_row_sha256":canonical_sha({"binding":row["old_binding"],"envelope":row["old_envelope"],"mechanical":row["base_records"]}),
            "condition_key_sha256":canonical_sha(row["condition"]),"pair_key_sha256":row["pair_key_sha256"],
            "fixture_source_sha256":row["fixture_source_sha256"],"fixture_bindings_sha256":row["fixture_bindings_sha256"],
            "fixture_catalog_sha256":row["fixture_catalog_sha256"],"accepted_final_sha256":None,
            "accepted_final_locator":None,"execution_status":"MEASUREMENT_NOT_OBSERVED"}
        if row["execution"] == {"run_status":"COMPLETE","structural_status":"ACCEPTED"}:
            accepted += 1; proof=row["accepted_final_proof"]
            if type(proof) is not dict: status="SAVED_ACCEPTED_FINAL_UNAVAILABLE"; connector_reason=status
            else:
                loc=proof.get("locator"); sid=loc.get("source_id") if type(loc) is dict else None
                try:
                    if proof.get("kind") == "BASELINE_V1":
                        map_row=next(x for x in old_mapping["rows"] if x.get("blind_id")==row["old_blind_id"])
                        if hashlib.sha256(loaded[sid]).hexdigest()!=map_row.get("source_final_sha256"): _fail("HASH")
                        extracted=extract_baseline_final(wrapper_bytes=loaded[sid], snapshot_file_sha256=loc["snapshot_file_sha256"], saved_surface=row["accepted_surface"])
                    elif proof.get("kind") == "CANDIDATE_V2":
                        saved_row=_parse_json_bytes(loaded[proof["saved_row_source_id"]])
                        outcome=_parse_json_bytes(loaded[proof["outcome_source_id"]])
                        seal=_parse_json_bytes(loaded[proof["container_seal_source_id"]])
                        if (outcome.get("status") != "ACCEPTED" or outcome.get("case_id") != row["case_id"]
                            or outcome.get("seed") != row["seed"] or outcome.get("stage") != proof["terminal_stage"]
                            or outcome.get("ordinal") != proof["ordinal"]): _fail("SHAPE")
                        if (saved_row.get("case_id"),saved_row.get("seed"),saved_row.get("terminal_stage")) != (row["case_id"],row["seed"],proof["terminal_stage"]): _fail("SHAPE")
                        accepted_outcomes=[]
                        prefix=f"actual/{row['case_id']}-{row['seed']}-{proof['terminal_stage']}-"
                        for entry in source["source_manifest"]["files"]:
                            if entry["original_name"].startswith(prefix) and entry["original_name"].endswith("-outcome.json"):
                                candidate_outcome=_parse_json_bytes(loaded[entry["source_id"]])
                                if candidate_outcome.get("status")=="ACCEPTED": accepted_outcomes.append(entry["source_id"])
                        if accepted_outcomes != [proof["outcome_source_id"]]: _fail("DUPLICATE")
                        for proof_id in (proof["outcome_source_id"], proof["response_source_id"]):
                            entry=next(x for x in source["source_manifest"]["files"] if x["source_id"]==proof_id)
                            if seal.get(entry["original_name"]) != entry["sha256"]: _fail("HASH")
                        extracted=extract_candidate_final(response_bytes=loaded[sid], snapshot_file_sha256=loc["snapshot_file_sha256"],
                            saved_final=saved_row["final"], saved_surface=row["accepted_surface"])
                    else: _fail("SHAPE")
                    freeze_fixture=fixture
                    if fixture.channel is None:
                        public_channels=[x["channel_id"] for x in fixture.source["context"]["chat_channels"] if x.get("is_public") is True]
                        if len(public_channels)!=1: _fail("SHAPE")
                        freeze_fixture=replace(fixture,channel=public_channels[0])
                    freeze=freeze_projected_ability_trust(fixture=freeze_fixture, fixture_builder_source_sha256=probe_sha,
                        binding_verifier_source_sha256=probe_sha)
                    projected_row, merged=_projected_row(old_binding=row["old_binding"], old_envelope=row["old_envelope"],
                        base_records=row["base_records"], plan=row["accepted_plan"], surface=row["accepted_surface"],
                        final_sha=extracted["accepted_final_sha256"], freeze=freeze)
                    witness["accepted_final_sha256"]=extracted["accepted_final_sha256"]
                    witness["accepted_final_locator"]={"container_seal_sha256":hashlib.sha256(loaded[proof["container_seal_source_id"]]).hexdigest() if proof["container_seal_source_id"] else hashlib.sha256(loaded[sid]).hexdigest(),**{k:extracted[k] for k in ("snapshot_file_sha256","member_object_sha256","json_pointer","encoding")},"member_name":loc["member_name"]}
                    witness["execution_status"]="ACCEPTED"
                    status="READY"
                except ProjectedAbilityInputError:
                    status="INPUT_INVALID"; connector_reason="SAVED_BASE_BINDING_MISMATCH"; projected_row=None; merged=None; witness["execution_status"]="ACCEPTED"
                except (SavedBindingError, KeyError, StopIteration, TypeError):
                    status="SAVED_ACCEPTED_FINAL_UNAVAILABLE"; connector_reason=status; projected_row=None; merged=None; witness["execution_status"]="ACCEPTED"
        witness_sha=canonical_sha(witness)
        manifest_rows.append({"evaluation_id":row["evaluation_id"],"old_row_binding_sha256":canonical_sha(row["old_binding"]),
            "source_witness_sha256":witness_sha,"projected_row_sha256":canonical_sha(projected_row) if projected_row else None,
            "custodian_freeze_sha256":projected_row["binding"]["custodian_freeze_sha256"] if projected_row else None,"status":status})
        host_rows.append({"evaluation_id":row["evaluation_id"],"pair_key_sha256":row["pair_key_sha256"],
            "execution":row["execution"],"audit":row["audit"],"conversion":row["conversion"],"accepted_surface":row["accepted_surface"],
            "projected_row":projected_row,"merged_records":list(merged) if status=="READY" else None,"witness":witness,"status":status,
            "connector_reason":connector_reason,"old_binding":deepcopy(row["old_binding"])})
        host_rows[-1]["old_envelope"]=deepcopy(row["old_envelope"])
        host_rows[-1]["old_base_records"]=deepcopy(row["base_records"])
    if len(pairs)!=96 or accepted!=179: _fail("COUNT")
    candidate=build_candidate_manifest(preflight_sha256=preflight["preflight_sha256"],rows=manifest_rows)
    _validate_current_sources(preflight)
    return {"contract":CONTRACT,"preflight":deepcopy(preflight),"source_manifest":deepcopy(source["source_manifest"]),
            "preflight_sha256":preflight["preflight_sha256"],"candidate_manifest":candidate,"host_rows":host_rows}


def _validate_prepared(prepared: dict, roots: dict[str,str], *, root: Path,
                       expected_preflight_sha256: str) -> None:
    _exact(prepared,("contract","preflight","source_manifest","preflight_sha256","candidate_manifest","host_rows"),"ROOT")
    manifest=_validate_candidate_manifest(prepared["candidate_manifest"])
    preflight=prepared["preflight"]
    if (prepared["contract"]!=CONTRACT or prepared["preflight_sha256"]!=_digest(expected_preflight_sha256)
        or prepared["preflight_sha256"]!=manifest["preflight_sha256"]
        or canonical_sha({k:preflight[k] for k in preflight if k!="preflight_sha256"})!=prepared["preflight_sha256"]): _fail("ROOT")
    if preflight["source_manifest_sha256"]!=prepared["source_manifest"]["manifest_sha256"]: _fail("ROOT")
    validate_source_manifest(root=root,manifest=prepared["source_manifest"])
    _validate_current_sources(preflight)
    if type(prepared["host_rows"]) is not list or len(prepared["host_rows"])!=192: _fail("COUNT")
    by={x["evaluation_id"]:x for x in manifest["rows"]}
    if len(by)!=192: _fail("DUPLICATE")
    seen=set()
    for host in prepared["host_rows"]:
        eid=_string(host.get("evaluation_id")); meta=by.get(eid)
        if meta is None or eid in seen: _fail("DUPLICATE")
        seen.add(eid)
        mechanical_sha=canonical_sha({"binding":host["old_binding"],"envelope":host["old_envelope"],"mechanical":host["old_base_records"]})
        if (canonical_sha(host["old_binding"])!=meta["old_row_binding_sha256"] or canonical_sha(host["witness"])!=meta["source_witness_sha256"]
            or host["witness"]["old_mechanical_row_sha256"]!=mechanical_sha or host["status"]!=meta["status"]): _fail("ROOT")
        if meta["status"]=="READY":
            row=host["projected_row"]
            if canonical_sha(row)!=meta["projected_row_sha256"] or row["binding"]["custodian_freeze_sha256"]!=roots[eid]: _fail("ROOT")
            if row["accepted_surface"]!=_surface_view(host["accepted_surface"]): _fail("SURFACE")
            projected=project_selected_abilities(row=row,expected_freeze_sha256=roots[eid])
            if tuple(host["merged_records"])!=merge_common_provenance_v2(base_records=tuple(host["old_base_records"]),projected_records=projected): _fail("ROOT")
        elif host["projected_row"] is not None or host["merged_records"] is not None or eid in roots:
            _fail("ROOT")


def seal_saved_packet(*, root: Path, prepared: dict, main_pin: dict, expected_pin_sha256: str,
                      expected_preflight_sha256: str) -> dict:
    _exact(prepared, ("contract","preflight","source_manifest","preflight_sha256","candidate_manifest","host_rows"), "ROOT")
    roots=validate_main_pin(candidate_manifest=prepared["candidate_manifest"], main_pin=main_pin,
                            expected_pin_sha256=expected_pin_sha256)
    _validate_prepared(prepared,roots,root=root,expected_preflight_sha256=expected_preflight_sha256)
    manifest={x["evaluation_id"]:x for x in prepared["candidate_manifest"]["rows"]}
    private=[]
    for host in prepared["host_rows"]:
        item=manifest[host["evaluation_id"]]; status=item["status"]
        if status=="READY":
            if host["projected_row"]["binding"]["custodian_freeze_sha256"] != roots[host["evaluation_id"]]: _fail("ROOT")
            provenance=[]
            selected=[]
            for record in host["merged_records"]:
                if record.get("contract")==PROJECTED_CONTRACT:
                    raw=record["canonical_value"]
                    result=raw["result_id"] if raw["result_id"] is not None else raw["revealed_role_id"]
                    canonical={"target":{"tag":"ABSENT" if raw["target_player_id"] is None else "VALUE","value":raw["target_player_id"]},
                               "result":{"tag":"ABSENT" if result is None else "VALUE","value":result}}
                    provenance.append({"opaque_provenance_id":record["opaque_provenance_id"],"lane":record["lane"],
                        "ref_resolution":record["source_resolution"],"selection":record["selection"],"actor_match":"TRUE",
                        "visibility":record["visibility"],"authority":"AUTHORIZED","canonical_value":canonical,
                        "binding_status":"COMPLETE","identity_kind":"PROJECTED_ABILITY"})
                    selected.append({"opaque_provenance_id":record["opaque_provenance_id"],
                        "target":canonical["target"],"result":canonical["result"]})
                else:
                    provenance.append({"opaque_provenance_id":record["provenance_id"],"lane":record["lane"],
                        "ref_resolution":record["ref_resolution"],"selection":record["selection"],"actor_match":record["actor_match"],
                        "visibility":record["visibility"],"authority":record["authority"],"canonical_value":record["canonical_value"],
                        "binding_status":record["binding_status"],"identity_kind":"SOURCE_REF" if record["origin"]=="EXPLICIT_SURFACE_REF" else None})
        else: provenance=[]; selected=[]
        public={"evaluation_id":host["evaluation_id"],"evaluation_status":"READY" if status=="READY" else
            "MEASUREMENT_NOT_OBSERVED" if status=="MEASUREMENT_NOT_OBSERVED" else "CONNECTOR_SKIPPED",
            "execution":host["execution"],"audit":host["audit"],"conversion":host["conversion"],
            "accepted_surface":host["accepted_surface"],"provenance":provenance,"sealed_selected_values":selected}
        private.append((host["pair_key_sha256"],public))
    rng=secrets.SystemRandom()
    for _ in range(1000):
        rng.shuffle(private)
        if all(private[i][0]!=private[i+1][0] for i in range(191)): break
    else: _fail("STATE")
    packet={"contract":CONTRACT,"rows":[x[1] for x in private]}; validate_blind_packet(packet)
    _validate_prepared(prepared,roots,root=root,expected_preflight_sha256=expected_preflight_sha256)
    return {"contract":CONTRACT,"candidate_manifest":deepcopy(prepared["candidate_manifest"]),
            "pin_sha256":expected_pin_sha256,"packet":packet,"packet_sha256":canonical_sha(packet)}


def _validate_sealed(*, prepared: dict, sealed: dict, expected_pin_sha256: str) -> None:
    _exact(sealed,("contract","candidate_manifest","pin_sha256","packet","packet_sha256"),"ROOT")
    if (sealed["contract"]!=CONTRACT or sealed["candidate_manifest"]!=prepared["candidate_manifest"]
        or sealed["pin_sha256"]!=expected_pin_sha256 or sealed["packet_sha256"]!=canonical_sha(sealed["packet"])): _fail("ROOT")
    validate_blind_packet(sealed["packet"])
    host={x["evaluation_id"]:x for x in prepared["host_rows"]}
    actual={x["evaluation_id"]:x for x in sealed["packet"]["rows"]}
    if set(actual)!=set(host): _fail("ROOT")
    for eid,row in actual.items():
        source=host[eid]; expected_status="READY" if source["status"]=="READY" else "MEASUREMENT_NOT_OBSERVED" if source["status"]=="MEASUREMENT_NOT_OBSERVED" else "CONNECTOR_SKIPPED"
        if row["evaluation_status"]!=expected_status or row["execution"]!=source["execution"] or row["audit"]!=source["audit"] or row["conversion"]!=source["conversion"] or row["accepted_surface"]!=source["accepted_surface"]: _fail("ROOT")
        if expected_status!="READY" and (row["provenance"] or row["sealed_selected_values"]): _fail("ROOT")
        if expected_status=="READY":
            expected_provenance=[]; expected_selected=[]
            for record in source["merged_records"]:
                if record.get("contract")==PROJECTED_CONTRACT:
                    raw=record["canonical_value"]; result=raw["result_id"] if raw["result_id"] is not None else raw["revealed_role_id"]
                    canonical={"target":{"tag":"ABSENT" if raw["target_player_id"] is None else "VALUE","value":raw["target_player_id"]},"result":{"tag":"ABSENT" if result is None else "VALUE","value":result}}
                    expected_provenance.append({"opaque_provenance_id":record["opaque_provenance_id"],"lane":record["lane"],"ref_resolution":record["source_resolution"],"selection":record["selection"],"actor_match":"TRUE","visibility":record["visibility"],"authority":"AUTHORIZED","canonical_value":canonical,"binding_status":"COMPLETE","identity_kind":"PROJECTED_ABILITY"})
                    expected_selected.append({"opaque_provenance_id":record["opaque_provenance_id"],"target":canonical["target"],"result":canonical["result"]})
                else:
                    expected_provenance.append({"opaque_provenance_id":record["provenance_id"],"lane":record["lane"],"ref_resolution":record["ref_resolution"],"selection":record["selection"],"actor_match":record["actor_match"],"visibility":record["visibility"],"authority":record["authority"],"canonical_value":record["canonical_value"],"binding_status":record["binding_status"],"identity_kind":"SOURCE_REF" if record["origin"]=="EXPLICIT_SURFACE_REF" else None})
            if row["provenance"]!=expected_provenance or row["sealed_selected_values"]!=expected_selected: _fail("ROOT")
    order=sealed["packet"]["rows"]
    if any(host[order[i]["evaluation_id"]]["pair_key_sha256"]==host[order[i+1]["evaluation_id"]]["pair_key_sha256"] for i in range(191)): _fail("ROOT")


def finalize_saved_results(*, root: Path, prepared: dict, sealed: dict, main_pin: dict,
                           expected_pin_sha256: str, expected_preflight_sha256: str,
                           annotations: list[dict]) -> dict:
    roots=validate_main_pin(candidate_manifest=prepared["candidate_manifest"],main_pin=main_pin,
                            expected_pin_sha256=expected_pin_sha256)
    _validate_prepared(prepared,roots,root=root,expected_preflight_sha256=expected_preflight_sha256)
    _validate_sealed(prepared=prepared,sealed=sealed,expected_pin_sha256=expected_pin_sha256)
    annotation=validate_annotations(candidate_manifest=prepared["candidate_manifest"],annotations=annotations,
                                    packet=sealed["packet"])
    host={x["evaluation_id"]:x for x in prepared["host_rows"]}; results=[]
    for meta in prepared["candidate_manifest"]["rows"]:
        eid=meta["evaluation_id"]; row=host[eid]; note=annotation[eid]
        if meta["status"] not in ("READY","MEASUREMENT_NOT_OBSERVED"):
            results.append(connector_unknown(evaluation_id=eid,reason=row["connector_reason"])); continue
        if meta["status"]=="MEASUREMENT_NOT_OBSERVED":
            decision=decide_projected_ability_s4(execution=row["execution"],audit=row["audit"],conversion=row["conversion"],
                row=None,expected_freeze_sha256=None,merged_records=(),semantic=None)
        else:
            projected=row["projected_row"]; base_binding=row["old_binding"]
            associations=[{"provenance_id":x["opaque_provenance_id"],"target":x["target"],"result":x["result"]} for x in note["associations"]]
            semantic={"contract":PROJECTED_CONTRACT,"base":{"binding":base_binding,"assertion":note["assertion"],
                "cited_provenance_ids":note["cited_provenance_ids"],"associations":associations},
                "blind_id":projected["binding"]["blind_id"],
                **{k:projected["binding"][k] for k in ("row_sha256","surface_sha256","accepted_final_sha256","custodian_freeze_sha256")},
                "witness_sha256":projected["witness"]["witness_sha256"]}
            decision=decide_projected_ability_s4(execution=row["execution"],audit=row["audit"],conversion=row["conversion"],
                row=projected,expected_freeze_sha256=roots[eid],merged_records=tuple(row["merged_records"]),semantic=semantic)
        results.append(wrap_decision(evaluation_id=eid,decision=decision))
    counts={k:sum(x["metric_value"]==k for x in results) for k in ("PASS","FAIL","UNKNOWN","MEASUREMENT_NOT_OBSERVED")}
    freeze={"contract":CONTRACT,"rows":192,"pairs":96,"candidate_manifest_sha256":prepared["candidate_manifest"]["manifest_sha256"],
        "main_pin_sha256":expected_pin_sha256,"packet_sha256":sealed["packet_sha256"],
        "annotations_sha256":canonical_sha(annotations),"results_sha256":canonical_sha(results),"counts":counts,"provider_calls":0}
    freeze["freeze_sha256"]=canonical_sha(freeze)
    _validate_prepared(prepared,roots,root=root,expected_preflight_sha256=expected_preflight_sha256)
    return {"contract":CONTRACT,"results":results,"freeze":freeze,
            "safe":{"status":"COMPLETE","rows":192,"pairs":96,"counts":counts,"provider_calls":0,
                    "freeze_sha256":freeze["freeze_sha256"]}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare-roots", "seal-packet", "finalize"):
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--expected-preflight-sha256", required=True)
        if name != "prepare-roots": command.add_argument("--expected-pin-sha256", required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve(strict=True)
    input_path = args.input.resolve(strict=True)
    output_path = args.output.resolve(strict=False)
    if root not in input_path.parents or root not in output_path.parents or input_path.is_symlink():
        _fail("ROOT")
    source = _load(input_path)
    if "source_manifest" in source:
        validate_source_manifest(root=root, manifest=source["source_manifest"])
    if args.command == "prepare-roots":
        output = prepare_saved_roots(root=root, source=source,
                                     expected_preflight_sha256=args.expected_preflight_sha256)
    elif args.command == "seal-packet":
        if source["prepared"]["preflight_sha256"] != _digest(args.expected_preflight_sha256): _fail("ROOT")
        output = seal_saved_packet(root=root,prepared=source["prepared"],main_pin=source["main_pin"],
                                   expected_pin_sha256=args.expected_pin_sha256,
                                   expected_preflight_sha256=args.expected_preflight_sha256)
    else:
        if source["prepared"]["preflight_sha256"] != _digest(args.expected_preflight_sha256): _fail("ROOT")
        output = finalize_saved_results(root=root,prepared=source["prepared"],sealed=source["sealed"],main_pin=source["main_pin"],
            expected_pin_sha256=args.expected_pin_sha256,expected_preflight_sha256=args.expected_preflight_sha256,
            annotations=source["annotations"])
    _write_once(output_path, output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("SAVED_BINDING_INPUT_INTEGRITY")
        raise SystemExit(1) from None
