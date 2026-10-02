from __future__ import annotations

import hashlib
import json
import math
import secrets
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from tests.fixtures.phase6_s4_unselected_ability_root import (build_unselected_semantic_v2,
    decide_unselected_ability_s4_v2, validate_unselected_ability_root_v2, UnselectedAbilityInputError)

CONTRACT = "S4_SAVED_GAP_DELTA_V1"
PACKET_CONTRACT = "S4_SAVED_GAP_DELTA_BLIND_V1"
ANNOTATION_CONTRACT = "S4_SAVED_GAP_DELTA_ANNOTATION_V1"
AGGREGATE_CONTRACT = "S4_SAVED_GAP_DELTA_AGGREGATE_V1"
PARTITIONS = ("CO_SURFACE_GAP", "UNSELECTED_ROOT_GAP", "PRIOR_READY", "MEASUREMENT_NOT_OBSERVED")
METRICS = ("PASS", "FAIL", "UNKNOWN", "MEASUREMENT_NOT_OBSERVED")
ASSERTIONS = ("NONE", "CLAIMED_RESULT", "EXPLICIT_AUTHORITY_ASSERTION", "UNDECIDABLE")

T560_FILES = (
    ("HELPER", "tests/fixtures/phase6_co_surface_binding.py"),
    ("CUSTODY", "tests/fixtures/phase6_co_surface_custody.py"),
    ("HELPER_TEST", "tests/test_phase6_co_surface_binding.py"),
    ("CUSTODY_TEST", "tests/test_phase6_co_surface_custody.py"),
)
T562_FILES = (
    ("ROOT_HELPER", "tests/fixtures/phase6_s4_unselected_ability_root.py"),
    ("ROOT_TEST", "tests/test_phase6_s4_unselected_ability_root.py"),
    ("COMMON_DECISION", "tests/fixtures/phase6_s4_common_provenance.py"),
    ("COMMON_DECISION_TEST", "tests/test_phase6_s4_common_provenance.py"),
)
DEPENDENCY_METADATA = {
    "T560": ("Docs/ai/design/PHASE6_CO_SURFACE_BINDING_DESIGN.md", "Docs/ai/handoffs/tasks/T560_CO_SURFACE_BINDING_TOOL_REVIEW.md"),
    "T562": ("Docs/ai/design/PHASE6_UNSELECTED_ABILITY_ROOT_DESIGN.md", "Docs/ai/handoffs/tasks/T562_UNSELECTED_ABILITY_ROOT_TOOL_REVIEW.md"),
}


class SavedDeltaInputError(ValueError):
    pass


def _fail(code: str) -> None:
    raise SavedDeltaInputError(code)


def _exact(value: Any, keys: tuple[str, ...], code: str) -> dict:
    if type(value) is not dict or len(value) != len(keys) or set(value) != set(keys):
        _fail(code)
    return value


def _digest(value: Any) -> str:
    if type(value) is not str or len(value) != 64:
        _fail("DIGEST")
    try:
        int(value, 16)
    except ValueError:
        _fail("DIGEST")
    if value != value.lower():
        _fail("DIGEST")
    return value


def _walk(value: Any) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            _fail("NONFINITE")
        return
    if type(value) in (list, tuple):
        for item in value:
            _walk(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                _fail("WIRE")
            _walk(item)
        return
    _fail("WIRE")


def canonical_wire(value: Any) -> bytes:
    _walk(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(canonical_wire(value)).hexdigest()


def strict_parse(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail("DUPLICATE_KEY")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: _fail("NONFINITE"))
    except (UnicodeError, json.JSONDecodeError):
        _fail("WIRE")
    _walk(value)
    if type(value) is not dict:
        _fail("WIRE")
    return value


def _validate_delta_binding(value: Any, row: dict) -> dict:
    keys=("contract","evaluation_id","delta_blind_id","partition","source_row_sha256","old_row_binding_sha256","old_selection_envelope_sha256",
          "old_mechanical_row_sha256","accepted_final_sha256","saved_surface_sha256","delta_surface_sha256","base_provenance_sha256",
          "t562_row_sha256","t562_witness_sha256","t562_inventory_freeze_sha256","surface_witness_sha256","main_pin_sha256","binding_sha256")
    value=_exact(value,keys,"BINDING")
    if value["contract"]!=CONTRACT or value["evaluation_id"]!=row["evaluation_id"] or value["partition"]!=row["partition"] or value["source_row_sha256"]!=row["source_row_sha256"]: _fail("BINDING")
    for key in keys[4:-1]: _digest(value[key])
    if value["binding_sha256"]!=canonical_sha({k:value[k] for k in value if k!="binding_sha256"}): _fail("BINDING")
    return value

def _pin_binding(value: dict, pin_sha256: str) -> dict:
    result=deepcopy(value); result["main_pin_sha256"]=_digest(pin_sha256)
    result["binding_sha256"]=canonical_sha({k:result[k] for k in result if k!="binding_sha256"})
    return result


def project_blind_base_provenance(base_records: Any) -> list[dict]:
    """Project the exact privacy-safe structural view, preserving envelope order."""
    if type(base_records) not in (list,tuple): _fail("BASE_PROVENANCE")
    result=[]; seen=set(); common_binding=None
    record_keys=("binding","provenance_id","lane","origin","ref_resolution","selection","actor_match","visibility","authority","canonical_value","binding_status")
    public_keys=("provenance_id","lane","ref_resolution","selection","actor_match","visibility","authority","binding_status","identity_kind")
    enums={
        "lane":{"PUBLIC_FACT","AUTHORITATIVE_ABILITY","CLAIMED_REPORT","FORGED_REFERENCE"},
        "origin":{"EXPLICIT_SURFACE_REF","SELECTED_PUBLIC_FACT","SELECTED_DISCLOSURE","SELECTED_CLAIM"},
        "ref_resolution":{"RESOLVED","NOT_FOUND","AMBIGUOUS","NOT_APPLICABLE"},
        "selection":{"SELECTED","NOT_SELECTED","NOT_APPLICABLE","UNKNOWN"},
        "actor_match":{"TRUE","FALSE","UNKNOWN"},"visibility":{"PUBLIC","NON_PUBLIC","UNKNOWN"},
        "authority":{"AUTHORIZED","FORBIDDEN","UNKNOWN"},"binding_status":{"COMPLETE","MISSING","CORRUPT"},
    }
    for record in base_records:
        record=_exact(record,record_keys,"BASE_PROVENANCE")
        if type(record["binding"]) is not dict: _fail("BASE_PROVENANCE")
        if common_binding is None: common_binding=record["binding"]
        elif record["binding"]!=common_binding: _fail("BASE_PROVENANCE")
        pid=record["provenance_id"]
        if type(pid) is not str or not pid or pid in seen: _fail("BASE_PROVENANCE")
        seen.add(pid)
        for key,allowed in enums.items():
            if record[key] not in allowed: _fail("BASE_PROVENANCE")
        public={"provenance_id":pid,"lane":record["lane"],"ref_resolution":record["ref_resolution"],
                "selection":record["selection"],"actor_match":record["actor_match"],"visibility":record["visibility"],
                "authority":record["authority"],"binding_status":record["binding_status"],
                "identity_kind":"SOURCE_REF" if record["origin"]=="EXPLICIT_SURFACE_REF" else None}
        _exact(public,public_keys,"BASE_PROVENANCE"); result.append(public)
    return result


def validate_dependency_bundle(bundle: Any, *, dependency: str, repository_root: Path,
                               expected_bundle_sha256: str) -> dict:
    expected = T560_FILES if dependency == "T560" else T562_FILES if dependency == "T562" else None
    if expected is None:
        _fail("DEPENDENCY")
    bundle = _exact(bundle, ("contract", "design_path", "design_sha256", "tool_review_path",
                             "tool_review_sha256", "review_scope_sha256", "files", "bundle_sha256"), "DEPENDENCY")
    if bundle["contract"] != f"{dependency}_APPROVED_DEPENDENCY_BUNDLE_V1":
        _fail("DEPENDENCY")
    design_path, review_path = DEPENDENCY_METADATA[dependency]
    if bundle["design_path"] != design_path or bundle["tool_review_path"] != review_path:
        _fail("DEPENDENCY")
    _digest(bundle["design_sha256"]); _digest(bundle["tool_review_sha256"])
    files = bundle["files"]
    if type(files) is not list or len(files) != len(expected):
        _fail("DEPENDENCY")
    for item, (role, path) in zip(files, expected):
        _exact(item, ("role", "repository_path", "sha256", "size_bytes"), "DEPENDENCY")
        if item["role"] != role or item["repository_path"] != path or type(item["size_bytes"]) is not int or item["size_bytes"] < 0:
            _fail("DEPENDENCY")
        _digest(item["sha256"])
        physical=(repository_root/item["repository_path"]).resolve(strict=True)
        if repository_root.resolve(strict=True) not in physical.parents or physical.is_symlink(): _fail("DEPENDENCY")
        raw=physical.read_bytes()
        if len(raw)!=item["size_bytes"] or hashlib.sha256(raw).hexdigest()!=item["sha256"]: _fail("DEPENDENCY")
    for path_key, sha_key in (("design_path","design_sha256"),("tool_review_path","tool_review_sha256")):
        physical=(repository_root/bundle[path_key]).resolve(strict=True)
        if repository_root.resolve(strict=True) not in physical.parents or physical.is_symlink() or hashlib.sha256(physical.read_bytes()).hexdigest()!=bundle[sha_key]: _fail("DEPENDENCY")
    scope = {key: bundle[key] for key in ("contract", "design_path", "design_sha256", "tool_review_path", "tool_review_sha256", "files")}
    if bundle["review_scope_sha256"] != canonical_sha(scope):
        _fail("DEPENDENCY")
    if bundle["bundle_sha256"] != canonical_sha({**scope, "review_scope_sha256": bundle["review_scope_sha256"]}):
        _fail("DEPENDENCY")
    if bundle["bundle_sha256"] != _digest(expected_bundle_sha256): _fail("DEPENDENCY")
    return deepcopy(bundle)


def validate_source_manifest(source: Any, *, repository_root: Path,
                             source_root: Path, expected_t560_bundle_sha256: str, expected_t562_bundle_sha256: str) -> dict:
    source = _exact(source, ("contract", "version", "task", "t556_source_manifest_sha256","t556_preflight_sha256","t556_main_pin_sha256",
                             "t556_packet_sha256","t556_packet_freeze_file_sha256","t556_annotation_sha256","t556_annotation_freeze_sha256",
                             "t556_finalized_sha256","t556_final_freeze_file_sha256","t560_source_manifest_sha256","t560_candidate_manifest_sha256","t560_main_pin_sha256",
                             "t560_approved_dependency_bundle", "t562_approved_dependency_bundle",
                             "files", "rows", "manifest_sha256"), "SOURCE")
    if source["contract"] != CONTRACT or source["version"] != "V1" or source["task"] != "T563":
        _fail("SOURCE")
    for key in ("t556_source_manifest_sha256","t556_preflight_sha256","t556_main_pin_sha256","t556_packet_sha256",
                "t556_packet_freeze_file_sha256","t556_annotation_sha256","t556_annotation_freeze_sha256",
                "t556_finalized_sha256","t556_final_freeze_file_sha256","t560_source_manifest_sha256",
                "t560_candidate_manifest_sha256","t560_main_pin_sha256"):
        _digest(source[key])
    t560 = validate_dependency_bundle(source["t560_approved_dependency_bundle"], dependency="T560",repository_root=repository_root,expected_bundle_sha256=expected_t560_bundle_sha256)
    t562 = validate_dependency_bundle(source["t562_approved_dependency_bundle"], dependency="T562",repository_root=repository_root,expected_bundle_sha256=expected_t562_bundle_sha256)
    files=source["files"]
    if type(files) is not list or not files: _fail("SOURCE_FILE")
    previous=None; ids=set(); source_root=source_root.resolve(strict=True)
    for item in files:
        _exact(item,("source_id","original_name","sha256","size"),"SOURCE_FILE")
        sid=item["source_id"]
        if type(sid) is not str or not sid or "/" in sid or "\\" in sid or sid in ids: _fail("SOURCE_FILE")
        ids.add(sid)
        if type(item["original_name"]) is not str or not item["original_name"] or previous is not None and item["original_name"]<=previous: _fail("SOURCE_FILE")
        previous=item["original_name"]; _digest(item["sha256"])
        if type(item["size"]) is not int or type(item["size"]) is bool or item["size"]<0: _fail("SOURCE_FILE")
        path=(source_root/sid).resolve(strict=True)
        if source_root not in path.parents or path.is_symlink(): _fail("SOURCE_FILE")
        raw=path.read_bytes()
        if len(raw)!=item["size"] or hashlib.sha256(raw).hexdigest()!=item["sha256"]: _fail("SOURCE_FILE")
    rows = source["rows"]
    if type(rows) is not list or len(rows) != 192:
        _fail("COUNT")
    seen = set(); counts = {key: 0 for key in PARTITIONS}; pairs: dict[str, set[str]] = {}
    for ordinal, row in enumerate(rows):
        _exact(row, ("evaluation_id", "ordinal", "pair_key_sha256", "profile_key_sha256","old_row_binding_sha256",
                     "old_selection_envelope_sha256","old_mechanical_row_sha256","saved_surface_sha256","accepted_final_sha256",
                     "old_decision_sha256","old_annotation_row_sha256","partition","source_row_sha256"), "ROW")
        eid = row["evaluation_id"]
        if type(eid) is not str or not eid or eid in seen or row["ordinal"] != ordinal or row["partition"] not in PARTITIONS:
            _fail("ROW")
        seen.add(eid); counts[row["partition"]] += 1
        pair = _digest(row["pair_key_sha256"]); profile = _digest(row["profile_key_sha256"]); _digest(row["source_row_sha256"])
        pairs.setdefault(pair, set()).add(profile)
        for key in ("old_row_binding_sha256","old_selection_envelope_sha256","old_mechanical_row_sha256","saved_surface_sha256","accepted_final_sha256"):
            if row[key] is not None: _digest(row[key])
        for key in ("old_decision_sha256","old_annotation_row_sha256"):
            if row[key] is not None: _digest(row[key])
        if row["source_row_sha256"]!=canonical_sha({k:row[k] for k in row if k!="source_row_sha256"}): _fail("ROW")
        if row["partition"]=="PRIOR_READY" and (row["old_decision_sha256"] is None or row["old_annotation_row_sha256"] is None): _fail("ROW")
        if row["partition"]=="MEASUREMENT_NOT_OBSERVED" and row["old_decision_sha256"] is None: _fail("ROW")
    if counts != {"CO_SURFACE_GAP": 5, "UNSELECTED_ROOT_GAP": 7, "PRIOR_READY": 167, "MEASUREMENT_NOT_OBSERVED": 13}:
        _fail("COUNT")
    if len(pairs) != 96 or any(len(items) != 2 for items in pairs.values()):
        _fail("PAIR")
    if source["manifest_sha256"] != canonical_sha({key: source[key] for key in source if key != "manifest_sha256"}):
        _fail("SOURCE")
    source = deepcopy(source); source["t560_approved_dependency_bundle"] = t560; source["t562_approved_dependency_bundle"] = t562
    return source


def build_preflight(*, source_manifest: dict, t556_core_artifacts_sha256: str,
                    helper_source_sha256: str, rubric_sha256: str, repository_root: Path, source_root: Path,
                    expected_t560_bundle_sha256: str, expected_t562_bundle_sha256: str) -> dict:
    source = validate_source_manifest(source_manifest,repository_root=repository_root,source_root=source_root,expected_t560_bundle_sha256=expected_t560_bundle_sha256,expected_t562_bundle_sha256=expected_t562_bundle_sha256)
    target = [r["evaluation_id"] for r in source["rows"] if r["partition"] in PARTITIONS[:2]]
    prior = [r["evaluation_id"] for r in source["rows"] if r["partition"] == PARTITIONS[2]]
    mno = [r["evaluation_id"] for r in source["rows"] if r["partition"] == PARTITIONS[3]]
    pairs = [[r["pair_key_sha256"], r["profile_key_sha256"]] for r in source["rows"]]
    out = {"contract": CONTRACT, "version": "V1", "task": "T563", "source_manifest_sha256": source["manifest_sha256"],
           "t556_core_artifacts_sha256": _digest(t556_core_artifacts_sha256),
           "t560_approved_bundle_sha256": source["t560_approved_dependency_bundle"]["bundle_sha256"],
           "t562_approved_bundle_sha256": source["t562_approved_dependency_bundle"]["bundle_sha256"],
           "target_scope_sha256": canonical_sha({"contract": CONTRACT, "ordered_evaluation_ids": target}),
           "prior_scope_sha256": canonical_sha({"contract": CONTRACT, "ordered_evaluation_ids": prior}),
           "mno_scope_sha256": canonical_sha({"contract": CONTRACT, "ordered_evaluation_ids": mno}),
           "pair_scope_sha256": canonical_sha({"contract": CONTRACT, "ordered_pair_keys_and_profile_keys": pairs}),
           "helper_source_sha256": _digest(helper_source_sha256), "rubric_sha256": _digest(rubric_sha256)}
    out["preflight_sha256"] = canonical_sha(out)
    return out


def _runtime_map(source_manifest: dict, runtime_rows: list[dict]) -> dict[str,dict]:
    if type(runtime_rows) is not list or len(runtime_rows)!=192: _fail("RUNTIME")
    result={}
    source={x["evaluation_id"]:x for x in source_manifest["rows"]}
    for value in runtime_rows:
        value=_exact(value,("evaluation_id","binding","ready_input","prior_reference","mno_reference"),"RUNTIME")
        eid=value["evaluation_id"]
        if eid in result or eid not in source: _fail("RUNTIME")
        row=source[eid]; partition=row["partition"]
        if partition in PARTITIONS[:2]:
            if value["prior_reference"] is not None or value["mno_reference"] is not None: _fail("RUNTIME")
            if value["ready_input"] is not None:
                binding=_validate_delta_binding(value["binding"],row)
                ready=_exact(value["ready_input"],("accepted_surface","provenance","t562_call"),"READY")
                call=_exact(ready["t562_call"],("execution","audit","conversion","row","expected_freeze_sha256","base_records","semantic"),"READY")
                t562_row=call["row"]; base_records=call["base_records"]
                if type(base_records) not in (list,tuple): _fail("READY")
                try:
                    validate_unselected_ability_root_v2(row=t562_row,expected_freeze_sha256=call["expected_freeze_sha256"],base_records=tuple(base_records))
                except UnselectedAbilityInputError:
                    _fail("READY")
                projected=project_blind_base_provenance(base_records)
                semantic=call["semantic"]; base=semantic.get("base") if type(semantic) is dict else None
                t562_binding=t562_row.get("binding") if type(t562_row) is dict else None
                relations=(
                    binding["old_row_binding_sha256"]==row["old_row_binding_sha256"],
                    binding["old_selection_envelope_sha256"]==row["old_selection_envelope_sha256"],
                    binding["old_mechanical_row_sha256"]==row["old_mechanical_row_sha256"],
                    binding["accepted_final_sha256"]==row["accepted_final_sha256"],
                    binding["saved_surface_sha256"]==row["saved_surface_sha256"],
                    binding["delta_surface_sha256"]==canonical_sha(ready["accepted_surface"]),
                    ready["accepted_surface"]==t562_row.get("accepted_surface"),
                    binding["t562_row_sha256"]==t562_binding.get("row_sha256"),
                    binding["t562_witness_sha256"]==t562_row.get("witness",{}).get("witness_sha256"),
                    binding["t562_inventory_freeze_sha256"]==call["expected_freeze_sha256"],
                    binding["base_provenance_sha256"]==canonical_sha(projected),
                    ready["provenance"]==projected,
                    type(base) is dict and semantic.get("base_binding_sha256")==canonical_sha(base.get("binding")),
                    not base_records or base.get("binding")==base_records[0].get("binding"),
                )
                if not all(relations): _fail("READY")
        elif partition==PARTITIONS[2]:
            if value["binding"] is not None or value["ready_input"] is not None or value["mno_reference"] is not None: _fail("RUNTIME")
            ref=_exact(value["prior_reference"],("evaluation_id","source_row_sha256","old_row_binding_sha256","old_annotation_row_sha256","old_decision_sha256","t556_annotation_sha256","t556_finalized_sha256","t556_final_freeze_file_sha256","decision","finalized_row","reference_sha256"),"PRIOR")
            for key in ("source_row_sha256","old_row_binding_sha256","old_annotation_row_sha256","old_decision_sha256","t556_annotation_sha256","t556_finalized_sha256","t556_final_freeze_file_sha256"): _digest(ref[key])
            if ref["evaluation_id"]!=eid or ref["source_row_sha256"]!=row["source_row_sha256"] or ref["old_row_binding_sha256"]!=row["old_row_binding_sha256"] or ref["old_decision_sha256"]!=row["old_decision_sha256"] or ref["old_annotation_row_sha256"]!=row["old_annotation_row_sha256"]: _fail("PRIOR")
            if ref["reference_sha256"]!=canonical_sha({k:ref[k] for k in ref if k not in ("reference_sha256","finalized_row")}): _fail("PRIOR")
            if type(ref["finalized_row"]) is not dict or ref["finalized_row"].get("evaluation_id")!=eid or ref["finalized_row"].get("projected_decision")!=ref["decision"]: _fail("PRIOR")
        else:
            if value["binding"] is not None or value["ready_input"] is not None or value["prior_reference"] is not None: _fail("RUNTIME")
            ref=_exact(value["mno_reference"],("evaluation_id","source_row_sha256","old_row_binding_sha256","old_decision_sha256","t556_finalized_sha256","t556_final_freeze_file_sha256","decision","finalized_row","reference_sha256"),"MNO")
            for key in ("source_row_sha256","old_row_binding_sha256","old_decision_sha256","t556_finalized_sha256","t556_final_freeze_file_sha256"): _digest(ref[key])
            if ref["evaluation_id"]!=eid or ref["source_row_sha256"]!=row["source_row_sha256"] or ref["old_row_binding_sha256"]!=row["old_row_binding_sha256"] or ref["old_decision_sha256"]!=row["old_decision_sha256"]: _fail("MNO")
            if ref["reference_sha256"]!=canonical_sha({k:ref[k] for k in ref if k not in ("reference_sha256","finalized_row")}): _fail("MNO")
            if type(ref["finalized_row"]) is not dict or ref["finalized_row"].get("evaluation_id")!=eid or ref["finalized_row"].get("projected_decision")!=ref["decision"]: _fail("MNO")
        result[eid]=deepcopy(value)
    return result


def validate_old_artifacts(*, source_manifest: dict, runtime_rows: list[dict],
                           finalized_artifact: dict, annotation_artifact: dict) -> None:
    _exact(finalized_artifact,("contract","freeze","results","safe"),"PRIOR")
    _exact(annotation_artifact,("contract","rows"),"PRIOR")
    if hashlib.sha256(canonical_wire(finalized_artifact)).hexdigest()!=source_manifest["t556_finalized_sha256"] or hashlib.sha256(canonical_wire(annotation_artifact)).hexdigest()!=source_manifest["t556_annotation_sha256"]: _fail("PRIOR")
    if type(finalized_artifact["results"]) is not list or len(finalized_artifact["results"])!=192: _fail("COUNT")
    if type(annotation_artifact["rows"]) is not list or len(annotation_artifact["rows"])!=192: _fail("COUNT")
    final={x.get("evaluation_id"):x for x in finalized_artifact["results"] if type(x) is dict}
    notes={x.get("evaluation_id"):x for x in annotation_artifact["rows"] if type(x) is dict}
    runtime={x["evaluation_id"]:x for x in runtime_rows}
    if len(final)!=192 or len(notes)!=192: _fail("PRIOR")
    for row in source_manifest["rows"]:
        eid=row["evaluation_id"]
        if row["partition"]=="PRIOR_READY":
            ref=runtime[eid]["prior_reference"]
            if final[eid]!=ref["finalized_row"] or canonical_sha(notes[eid])!=ref["old_annotation_row_sha256"]: _fail("PRIOR")
        elif row["partition"]=="MEASUREMENT_NOT_OBSERVED" and final[eid]!=runtime[eid]["mno_reference"]["finalized_row"]: _fail("MNO")


def build_candidate_manifest(*, source_manifest: dict, preflight: dict, runtime_rows: list[dict]) -> dict:
    runtime=_runtime_map(source_manifest,runtime_rows)
    rows=[]; counts={"co_gap":0,"root_gap":0,"prior":0,"mno":0,"target_ready":0,"target_input_unknown":0}
    for row in source_manifest["rows"]:
        partition=row["partition"]
        if partition in PARTITIONS[:2]:
            private=runtime[row["evaluation_id"]]; ready=private["ready_input"] is not None; status="READY" if ready else "INPUT_UNKNOWN"
            reason="READY" if ready else "UNSELECTED_ROOT_UNAVAILABLE"
            root_sha=canonical_sha(private["ready_input"]["t562_call"]["row"]) if ready else None
            witness_sha=private["binding"]["surface_witness_sha256"] if ready else None
            counts["co_gap" if partition==PARTITIONS[0] else "root_gap"]+=1; counts["target_ready" if ready else "target_input_unknown"]+=1
        elif partition==PARTITIONS[2]:
            status="PREVIOUSLY_EVALUATED"; reason=status; root_sha=witness_sha=None; counts["prior"]+=1
        else:
            status="MEASUREMENT_NOT_OBSERVED"; reason="PUBLIC_SURFACE_NOT_OBSERVED"; root_sha=witness_sha=None; counts["mno"]+=1
        item={"evaluation_id":row["evaluation_id"],"ordinal":row["ordinal"],"partition":partition,"source_row_sha256":row["source_row_sha256"],
              "delta_input_status":status,"delta_root_sha256":root_sha,"surface_witness_sha256":witness_sha,"reason":reason}
        rows.append(item)
    result={"contract":CONTRACT,"version":"V1","source_manifest_sha256":source_manifest["manifest_sha256"],"preflight_sha256":preflight["preflight_sha256"],
            "target_scope_sha256":preflight["target_scope_sha256"],"prior_scope_sha256":preflight["prior_scope_sha256"],"mno_scope_sha256":preflight["mno_scope_sha256"],"pair_scope_sha256":preflight["pair_scope_sha256"],"rows":rows,"counts":counts}
    result["manifest_sha256"]=canonical_sha(result)
    return result


def build_main_pin(*, independent_state: dict, expected_independent_state_sha256: str,
                   expected_independent_roots_sha256: str,
                   preflight: dict, candidate_manifest: dict,
                   t560_approved_bundle_sha256: str, t562_approved_bundle_sha256: str) -> dict:
    independent_state=_exact(independent_state,("contract","fixed_roots_sha256","source_rows","expected_candidate","independent_roots","target_roots","state_sha256"),"MAIN_PIN")
    if independent_state["contract"]!="S4_SAVED_DELTA_MAIN_STATE_V1" or independent_state["state_sha256"]!=_digest(expected_independent_state_sha256) or independent_state["state_sha256"]!=canonical_sha({k:independent_state[k] for k in independent_state if k!="state_sha256"}): _fail("MAIN_PIN")
    _digest(independent_state["fixed_roots_sha256"])
    if independent_state["expected_candidate"]!=candidate_manifest: _fail("MAIN_PIN")
    if type(independent_state["source_rows"]) is not list or len(independent_state["source_rows"])!=192 or type(independent_state["target_roots"]) is not list: _fail("MAIN_PIN")
    if [(x.get("evaluation_id"),x.get("source_row_sha256")) for x in independent_state["source_rows"]] != [(x.get("evaluation_id"),x.get("source_row_sha256")) for x in candidate_manifest["rows"]]: _fail("MAIN_PIN")
    expected_targets=[x for x in candidate_manifest["rows"] if x["partition"] in PARTITIONS[:2]]
    target_roots=independent_state["target_roots"]
    if len(target_roots)!=12: _fail("MAIN_PIN")
    source_by_id={row["evaluation_id"]:row for row in independent_state["source_rows"]}
    root_witness_keys=("contract","evaluation_id","old_row_binding_sha256","saved_surface_sha256","bound_surface_sha256","accepted_final_sha256","witness_sha256")
    co_witness_keys=("contract","evaluation_id","old_blind_id","row_identity_sha256","source_sha256","bindings_sha256","catalog_sha256","accepted_final_sha256","accepted_plan_sha256","old_row_binding_sha256","old_selection_envelope_sha256","saved_surface_sha256","raw_selection","option_resolution","role_resolution","catalog_membership_sha256","fact_binding_set_sha256","claim_projection_sha256","witness_sha256")
    for root,candidate in zip(target_roots,expected_targets):
        root=_exact(root,("evaluation_id","delta_input_status","t562_row","surface_witness"),"MAIN_PIN")
        if root["evaluation_id"]!=candidate["evaluation_id"] or root["delta_input_status"]!=candidate["delta_input_status"]: _fail("MAIN_PIN")
        if root["delta_input_status"]=="READY":
            if type(root["t562_row"]) is not dict or type(root["surface_witness"]) is not dict: _fail("MAIN_PIN")
            if canonical_sha(root["t562_row"])!=candidate["delta_root_sha256"]: _fail("MAIN_PIN")
            witness=root["surface_witness"]
            keys=co_witness_keys if candidate["partition"]=="CO_SURFACE_GAP" else root_witness_keys
            _exact(witness,keys,"MAIN_PIN")
            if witness["evaluation_id"]!=candidate["evaluation_id"] or witness["witness_sha256"]!=_digest(candidate["surface_witness_sha256"]): _fail("MAIN_PIN")
            if witness["witness_sha256"]!=canonical_sha({key:witness[key] for key in witness if key!="witness_sha256"}): _fail("MAIN_PIN")
            source_row=source_by_id.get(candidate["evaluation_id"])
            if source_row is None or witness["old_row_binding_sha256"]!=source_row["old_row_binding_sha256"] or witness["saved_surface_sha256"]!=source_row["saved_surface_sha256"] or witness["accepted_final_sha256"]!=source_row["accepted_final_sha256"]: _fail("MAIN_PIN")
            if candidate["partition"]=="CO_SURFACE_GAP":
                if witness["contract"]!="S4_CO_SURFACE_SOURCE_BINDING_V1" or witness["old_selection_envelope_sha256"]!=source_row["old_selection_envelope_sha256"]: _fail("MAIN_PIN")
            else:
                if witness["contract"]!="S4_EXISTING_SURFACE_WITNESS_V1" or witness["bound_surface_sha256"]!=root["t562_row"].get("binding",{}).get("surface_sha256"): _fail("MAIN_PIN")
        elif root["delta_input_status"]=="INPUT_UNKNOWN":
            if root["t562_row"] is not None or root["surface_witness"] is not None or candidate["delta_root_sha256"] is not None or candidate["surface_witness_sha256"] is not None: _fail("MAIN_PIN")
        else: _fail("MAIN_PIN")
    independent_roots=independent_state["independent_roots"]
    independent_roots=_exact(independent_roots,("contract","rows","roots_sha256"),"MAIN_PIN")
    if independent_roots["contract"]!="S4_SAVED_DELTA_MAIN_ROOTS_V1" or independent_roots["roots_sha256"]!=_digest(expected_independent_roots_sha256) or independent_roots["roots_sha256"]!=canonical_sha({"contract":independent_roots["contract"],"rows":independent_roots["rows"]}): _fail("MAIN_PIN")
    expected=deepcopy(independent_roots["rows"])
    if type(expected) is not list or len(expected)!=192: _fail("MAIN_PIN")
    for row in expected: _exact(row,("evaluation_id","expected_source_row_sha256","expected_delta_root_sha256","expected_surface_witness_sha256"),"MAIN_PIN")
    candidate_expected=[{"evaluation_id":x["evaluation_id"],"expected_source_row_sha256":x["source_row_sha256"],"expected_delta_root_sha256":x["delta_root_sha256"],"expected_surface_witness_sha256":x["surface_witness_sha256"]} for x in candidate_manifest["rows"]]
    if expected!=candidate_expected: _fail("MAIN_PIN")
    result={"contract":CONTRACT,"version":"V1","task":"T563","source_manifest_sha256":candidate_manifest["source_manifest_sha256"],
            "preflight_sha256":candidate_manifest["preflight_sha256"],"candidate_manifest_sha256":candidate_manifest["manifest_sha256"],
            "target_scope_sha256":candidate_manifest["target_scope_sha256"],"prior_scope_sha256":candidate_manifest["prior_scope_sha256"],
            "mno_scope_sha256":candidate_manifest["mno_scope_sha256"],"pair_scope_sha256":candidate_manifest["pair_scope_sha256"],
            "t560_approved_bundle_sha256":_digest(t560_approved_bundle_sha256),"t562_approved_bundle_sha256":_digest(t562_approved_bundle_sha256),
            "coverage_rows_sha256":canonical_sha(candidate_manifest["rows"]),"rows":deepcopy(expected)}
    result["pin_sha256"]=canonical_sha(result)
    return result


def _validate_pin(candidate: dict, pin: Any, expected_pin_sha256: str) -> None:
    keys=("contract","version","task","source_manifest_sha256","preflight_sha256","candidate_manifest_sha256","target_scope_sha256","prior_scope_sha256","mno_scope_sha256","pair_scope_sha256","t560_approved_bundle_sha256","t562_approved_bundle_sha256","coverage_rows_sha256","rows","pin_sha256")
    _exact(pin,keys,"MAIN_PIN")
    if pin["pin_sha256"]!=_digest(expected_pin_sha256) or canonical_sha({k:pin[k] for k in pin if k!="pin_sha256"})!=pin["pin_sha256"]: _fail("MAIN_PIN")
    scalar=(pin["source_manifest_sha256"]==candidate["source_manifest_sha256"] and pin["preflight_sha256"]==candidate["preflight_sha256"] and
            pin["candidate_manifest_sha256"]==candidate["manifest_sha256"] and pin["target_scope_sha256"]==candidate["target_scope_sha256"] and
            pin["prior_scope_sha256"]==candidate["prior_scope_sha256"] and pin["mno_scope_sha256"]==candidate["mno_scope_sha256"] and
            pin["pair_scope_sha256"]==candidate["pair_scope_sha256"] and pin["coverage_rows_sha256"]==canonical_sha(candidate["rows"]))
    if not scalar: _fail("MAIN_PIN")


def _validate_runtime_main_roots(runtime: dict[str,dict], pin: dict) -> None:
    roots={row["evaluation_id"]:row for row in pin["rows"]}
    if len(roots)!=len(pin["rows"]): _fail("MAIN_PIN")
    for evaluation_id,private in runtime.items():
        ready=private["ready_input"]
        expected=roots.get(evaluation_id)
        if expected is None: _fail("MAIN_PIN")
        expected_root=expected["expected_delta_root_sha256"]
        if ready is None:
            if expected_root is not None: _fail("MAIN_PIN")
            continue
        row=ready["t562_call"]["row"]
        if expected_root is None or canonical_sha(row)!=_digest(expected_root): _fail("MAIN_PIN")
        # The native T562 validation in _runtime_map binds the private base binding
        # and its exact public reprojection to this now Main-pinned row.
        if project_blind_base_provenance(ready["t562_call"]["base_records"])!=ready["provenance"]: _fail("MAIN_PIN")


def seal_blind_packet(*, source_manifest: dict, preflight: dict, candidate_manifest: dict, main_pin: dict,
                      runtime_rows: list[dict], expected_pin_sha256: str, repository_root: Path, source_root: Path,
                      expected_t560_bundle_sha256: str, expected_t562_bundle_sha256: str,
                      blind_ids: list[str] | None=None) -> dict:
    source = validate_source_manifest(source_manifest,repository_root=repository_root,source_root=source_root,expected_t560_bundle_sha256=expected_t560_bundle_sha256,expected_t562_bundle_sha256=expected_t562_bundle_sha256)
    if preflight["source_manifest_sha256"] != source["manifest_sha256"]:
        _fail("ROOT")
    _validate_pin(candidate_manifest,main_pin,expected_pin_sha256)
    runtime=_runtime_map(source,runtime_rows)
    _validate_runtime_main_roots(runtime,main_pin)
    target = [r for r in source["rows"] if r["partition"] in PARTITIONS[:2]]
    if blind_ids is None:
        blind_ids=[secrets.token_hex(16) for _ in range(12)]
    if type(blind_ids) is not list or len(blind_ids) != 12 or len(set(blind_ids)) != 12 or any(type(x) is not str or not x for x in blind_ids):
        _fail("BLIND")
    entries=[]; unknown=[]
    for row, blind_id in zip(target, blind_ids):
        private=runtime[row["evaluation_id"]]; ready = private["ready_input"]
        if ready is None:
            unknown.append({"evaluation_id":row["evaluation_id"],"reason":"UNSELECTED_ROOT_UNAVAILABLE"})
            continue
        _exact(ready, ("accepted_surface", "provenance", "t562_call"), "READY")
        call=ready["t562_call"]; base_records=call.get("base_records") if type(call) is dict else None
        projected=project_blind_base_provenance(base_records)
        if ready["provenance"]!=projected or private["binding"]["base_provenance_sha256"]!=canonical_sha(projected): _fail("BASE_PROVENANCE")
        entries.append(({"delta_blind_id":blind_id,"accepted_surface":deepcopy(ready["accepted_surface"]),"provenance":projected},
                        {"delta_blind_id":blind_id,"evaluation_id":row["evaluation_id"],"binding_sha256":_pin_binding(private["binding"],expected_pin_sha256)["binding_sha256"]}))
    secrets.SystemRandom().shuffle(entries)
    packet_rows=[entry[0] for entry in entries]; mapping=[entry[1] for entry in entries]
    packet_binding_sha = canonical_sha(mapping)
    packet={"contract":PACKET_CONTRACT,"rubric_sha256":preflight["rubric_sha256"],"target_scope_sha256":preflight["target_scope_sha256"],
            "packet_binding_sha256":packet_binding_sha,"rows":packet_rows}
    packet_sha=canonical_sha(packet); mapping_sha=canonical_sha(mapping)
    freeze={"contract":CONTRACT,"version":"V1","source_manifest_sha256":source["manifest_sha256"],"preflight_sha256":preflight["preflight_sha256"],"candidate_manifest_sha256":candidate_manifest["manifest_sha256"],
            "main_pin_sha256":expected_pin_sha256,"mapping_sha256":mapping_sha,"rubric_sha256":preflight["rubric_sha256"],"packet_sha256":packet_sha,
            "packet_binding_sha256":packet_binding_sha,"target_scope_sha256":preflight["target_scope_sha256"],"packet_rows":len(packet_rows),"input_unknown_rows":len(unknown)}
    freeze["freeze_sha256"]=canonical_sha(freeze)
    return {"packet":packet,"mapping":mapping,"input_unknown":unknown,"packet_freeze":freeze}


def freeze_annotations(*, sealed: dict, annotations: dict, fresh_independent_evaluator: bool) -> dict:
    packet=sealed["packet"]; rows=annotations.get("rows") if type(annotations) is dict else None
    if fresh_independent_evaluator is not True or type(rows) is not list or len(rows)!=len(packet["rows"]): _fail("ANNOTATION")
    ids=[x["delta_blind_id"] for x in packet["rows"]]; counts={key:0 for key in ASSERTIONS}
    for expected,row in zip(ids,rows):
        _exact(row,("delta_blind_id","status","assertion","cited_provenance_ids","associations"),"ANNOTATION")
        if row["delta_blind_id"]!=expected or row["status"]!="ANNOTATED" or row["assertion"] not in ASSERTIONS: _fail("ANNOTATION")
        if type(row["cited_provenance_ids"]) is not list or len(row["cited_provenance_ids"])!=len(set(row["cited_provenance_ids"])): _fail("ANNOTATION")
        allowed={p["provenance_id"] for p in packet["rows"][ids.index(expected)]["provenance"]}
        source_refs={p["provenance_id"] for p in packet["rows"][ids.index(expected)]["provenance"] if p["identity_kind"]=="SOURCE_REF"}
        if not set(row["cited_provenance_ids"])<=allowed: _fail("ANNOTATION")
        if not set(row["cited_provenance_ids"])<=source_refs: _fail("ANNOTATION")
        if type(row["associations"]) is not list or row["assertion"] in ("NONE","UNDECIDABLE") and row["associations"]: _fail("ANNOTATION")
        association_ids=[]
        for association in row["associations"]:
            _exact(association,("provenance_id","target","result"),"ANNOTATION")
            for atom in (association["target"],association["result"]):
                _exact(atom,("tag","value"),"ANNOTATION")
                if atom["tag"]=="VALUE":
                    if type(atom["value"]) is not str or not atom["value"]: _fail("ANNOTATION")
                elif atom["tag"] in ("ABSENT","UNDECIDABLE"):
                    if atom["value"] is not None: _fail("ANNOTATION")
                else: _fail("ANNOTATION")
            association_ids.append(association["provenance_id"])
        if len(association_ids)!=len(set(association_ids)) or not set(association_ids)<=allowed: _fail("ANNOTATION")
        counts[row["assertion"]]+=1
    envelope={"contract":ANNOTATION_CONTRACT,"packet_sha256":sealed["packet_freeze"]["packet_sha256"],"rubric_sha256":packet["rubric_sha256"],
              "packet_freeze_sha256":sealed["packet_freeze"]["freeze_sha256"],"rows":deepcopy(rows)}
    freeze={"contract":CONTRACT,"version":"V1","packet_sha256":envelope["packet_sha256"],"rubric_sha256":envelope["rubric_sha256"],
            "packet_freeze_sha256":envelope["packet_freeze_sha256"],"annotation_sha256":canonical_sha(envelope),"annotation_rows_sha256":canonical_sha(rows),
            "rows":len(rows),"assertion_counts":counts,"fresh_independent_evaluator":True}
    freeze["freeze_sha256"]=canonical_sha(freeze)
    return {"annotations":envelope,"annotation_freeze":freeze}


def finalize(*, source_manifest: dict, runtime_rows: list[dict], finalized_artifact: dict, annotation_artifact: dict,
             preflight: dict, candidate_manifest: dict, main_pin: dict, sealed: dict, frozen_annotations: dict,
             expected_pin_sha256: str, repository_root: Path, source_root: Path, expected_t560_bundle_sha256: str,
             expected_t562_bundle_sha256: str, decider: Callable[...,dict]=decide_unselected_ability_s4_v2) -> dict:
    source=validate_source_manifest(source_manifest,repository_root=repository_root,source_root=source_root,expected_t560_bundle_sha256=expected_t560_bundle_sha256,expected_t562_bundle_sha256=expected_t562_bundle_sha256)
    runtime=_runtime_map(source,runtime_rows)
    validate_old_artifacts(source_manifest=source,runtime_rows=runtime_rows,finalized_artifact=finalized_artifact,annotation_artifact=annotation_artifact)
    _validate_pin(candidate_manifest,main_pin,expected_pin_sha256)
    _validate_runtime_main_roots(runtime,main_pin)
    if main_pin.get("pin_sha256")!=expected_pin_sha256 or sealed["packet_freeze"]["main_pin_sha256"]!=expected_pin_sha256: _fail("ROOT")
    if frozen_annotations["annotation_freeze"]["annotation_sha256"]!=canonical_sha(frozen_annotations["annotations"]): _fail("ANNOTATION")
    mapping={x["evaluation_id"]:x["delta_blind_id"] for x in sealed["mapping"]}; notes={x["delta_blind_id"]:x for x in frozen_annotations["annotations"]["rows"]}
    results=[]; wrappers=[]; calls=0
    for row in source["rows"]:
        partition=row["partition"]
        if partition in PARTITIONS[:2]:
            private=runtime[row["evaluation_id"]]
            if private["ready_input"] is None:
                decision={"binding":None,"metric_value":"UNKNOWN","reason_code":"UNSELECTED_ROOT_UNAVAILABLE","offending_provenance_ids":[],"measurement_validity":"INVALID"}
                input_decision={**decision,"decision_origin":"INPUT_UNKNOWN"}
                wrapper={"delta_binding":None,"t562_decision":None,"input_unknown_decision":input_decision}
            else:
                note=notes[mapping[row["evaluation_id"]]]; call=deepcopy(private["ready_input"]["t562_call"])
                projected=project_blind_base_provenance(call.get("base_records"))
                packet_row=next((item for item in sealed["packet"]["rows"] if item["delta_blind_id"]==mapping[row["evaluation_id"]]),None)
                if packet_row is None or packet_row["provenance"]!=projected or private["ready_input"]["provenance"]!=projected or private["binding"]["base_provenance_sha256"]!=canonical_sha(projected): _fail("BASE_PROVENANCE")
                semantic=call.get("semantic")
                if type(semantic) is not dict or type(semantic.get("base")) is not dict: _fail("SEMANTIC")
                base=deepcopy(semantic["base"]); base["assertion"]=note["assertion"]
                base["cited_provenance_ids"]=deepcopy(note["cited_provenance_ids"])
                base["associations"]=[{"provenance_id":x["provenance_id"],"target":deepcopy(x["target"]),"result":deepcopy(x["result"])} for x in note["associations"]]
                call["semantic"]=build_unselected_semantic_v2(row=call["row"],base=base,base_annotation_freeze_sha256=frozen_annotations["annotation_freeze"]["freeze_sha256"])
                call["base_records"]=tuple(call["base_records"])
                decision=decider(**call); calls+=1
                wrapper={"delta_binding":_pin_binding(private["binding"],expected_pin_sha256),"t562_decision":deepcopy(decision),"input_unknown_decision":None}
            wrapper["decision_sha256"]=canonical_sha(wrapper); wrappers.append(wrapper)
            decision_sha=wrapper["decision_sha256"]
        elif partition=="PRIOR_READY":
            ref=runtime[row["evaluation_id"]]["prior_reference"]; decision=deepcopy(ref["decision"])
            if ref["old_decision_sha256"]!=canonical_sha(decision): _fail("PRIOR")
            decision_sha=ref["reference_sha256"]
        else:
            ref=runtime[row["evaluation_id"]]["mno_reference"]; decision=deepcopy(ref["decision"])
            if decision!={"binding":None,"metric_value":"MEASUREMENT_NOT_OBSERVED","reason_code":"PUBLIC_SURFACE_NOT_OBSERVED","offending_provenance_ids":[],"measurement_validity":"INVALID"} or ref["old_decision_sha256"]!=canonical_sha(decision): _fail("MNO")
            decision_sha=ref["reference_sha256"]
        if decision["metric_value"] not in METRICS: _fail("DECISION")
        results.append({"evaluation_id":row["evaluation_id"],"ordinal":row["ordinal"],"pair_key_sha256":row["pair_key_sha256"],"profile_key_sha256":row["profile_key_sha256"],
                        "source_row_sha256":row["source_row_sha256"],"origin":"FRESH_DELTA" if partition in PARTITIONS[:2] else "PRIOR_REFERENCE" if partition=="PRIOR_READY" else "MNO_REFERENCE",
                        "decision_sha256":decision_sha,"decision":{key:deepcopy(decision[key]) for key in ("binding","metric_value","reason_code","offending_provenance_ids","measurement_validity")}})
    if calls != len(sealed["packet"]["rows"]): _fail("DECISION_CALLS")
    counts={key:sum(r["decision"]["metric_value"]==key for r in results) for key in METRICS}; pair_counts={}
    grouped={}
    for row in results: grouped.setdefault(row["pair_key_sha256"],[]).append(row)
    if len(grouped)!=96 or any(len(v)!=2 for v in grouped.values()): _fail("PAIR")
    for pair in grouped.values():
        pair.sort(key=lambda x:x["profile_key_sha256"]); key="__".join(x["decision"]["metric_value"] for x in pair); pair_counts[key]=pair_counts.get(key,0)+1
    if [x["evaluation_id"] for x in results]!=[x["evaluation_id"] for x in source["rows"]] or {k:sum(x["origin"]==k for x in results) for k in ("FRESH_DELTA","PRIOR_REFERENCE","MNO_REFERENCE")}!={"FRESH_DELTA":12,"PRIOR_REFERENCE":167,"MNO_REFERENCE":13}: _fail("AGGREGATE")
    aggregate={"contract":AGGREGATE_CONTRACT,"version":"V1","source_manifest_sha256":source["manifest_sha256"],"preflight_sha256":preflight["preflight_sha256"],"candidate_manifest_sha256":candidate_manifest["manifest_sha256"],
               "main_pin_sha256":expected_pin_sha256,"packet_freeze_sha256":sealed["packet_freeze"]["freeze_sha256"],"annotation_freeze_sha256":frozen_annotations["annotation_freeze"]["freeze_sha256"],
               "rows":results,"counts":counts,"pair_counts":pair_counts,"pairs":96}
    aggregate["aggregate_sha256"]=canonical_sha(aggregate)
    return {"decision_wrappers":wrappers,"aggregate":aggregate}


def build_final_freeze(*, source_manifest: dict, preflight: dict, candidate_manifest: dict, sealed: dict,
                       frozen_annotations: dict, aggregate: dict, expected_pin_sha256: str,
                       mapping_sha256: str, helper_source_sha256: str, repository_root: Path, source_root: Path,
                       expected_t560_bundle_sha256: str, expected_t562_bundle_sha256: str) -> dict:
    source=validate_source_manifest(source_manifest,repository_root=repository_root,source_root=source_root,expected_t560_bundle_sha256=expected_t560_bundle_sha256,expected_t562_bundle_sha256=expected_t562_bundle_sha256)
    if aggregate.get("aggregate_sha256")!=canonical_sha({k:v for k,v in aggregate.items() if k!="aggregate_sha256"}): _fail("AGGREGATE")
    if len(aggregate.get("rows",()))!=192 or aggregate.get("pairs")!=96 or sum(aggregate.get("counts",{}).values())!=192: _fail("COUNT")
    freeze={"contract":CONTRACT,"version":"V1","task":"T563","source_manifest_sha256":source["manifest_sha256"],
            "preflight_sha256":preflight["preflight_sha256"],"candidate_manifest_sha256":candidate_manifest["manifest_sha256"],"main_pin_sha256":_digest(expected_pin_sha256),
            "mapping_sha256":_digest(mapping_sha256),"rubric_sha256":preflight["rubric_sha256"],
            "packet_sha256":sealed["packet_freeze"]["packet_sha256"],"packet_freeze_sha256":sealed["packet_freeze"]["freeze_sha256"],
            "annotation_sha256":frozen_annotations["annotation_freeze"]["annotation_sha256"],
            "annotation_freeze_sha256":frozen_annotations["annotation_freeze"]["freeze_sha256"],"aggregate_sha256":aggregate["aggregate_sha256"],
            "helper_source_sha256":_digest(helper_source_sha256),"t560_approved_bundle_sha256":source["t560_approved_dependency_bundle"]["bundle_sha256"],
            "t562_approved_bundle_sha256":source["t562_approved_dependency_bundle"]["bundle_sha256"],"rows":192,"pairs":96,
            "counts":deepcopy(aggregate["counts"]),"provider_calls":0,"old_artifacts_modified":False}
    freeze["freeze_sha256"]=canonical_sha(freeze)
    safe={"contract":CONTRACT,"version":"V1","status":"COMPLETE","rows":192,"pairs":96,"counts":deepcopy(aggregate["counts"]),
          "pair_counts":deepcopy(aggregate["pair_counts"]),"partition_counts":{"CO_SURFACE_GAP":5,"UNSELECTED_ROOT_GAP":7,"PRIOR_READY":167,"MEASUREMENT_NOT_OBSERVED":13},
          "fresh_annotation_rows":frozen_annotations["annotation_freeze"]["rows"],"input_unknown_rows":sealed["packet_freeze"]["input_unknown_rows"],
          "provider_calls":0,"freeze_sha256":freeze["freeze_sha256"]}
    return {"final_freeze":freeze,"safe_summary":safe}
