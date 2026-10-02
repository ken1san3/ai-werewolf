from __future__ import annotations

import json
from pathlib import Path

from tests.fixtures.phase6_s4_saved_delta import SavedDeltaInputError, canonical_sha, strict_parse

ARTIFACT_ORDER=("source-manifest.json","preflight.json","candidate-manifest.json","main-pin.json","mapping.json",
                "blind-packet.json","rubric.json","packet-freeze.json","annotations.json","annotation-freeze.json",
                "aggregate.json","final-freeze.json","safe-summary.json")

def _self_hash(value: dict, field: str) -> bool:
    return type(value) is dict and value.get(field)==canonical_sha({k:v for k,v in value.items() if k!=field})

def _keys(value, keys):
    if type(value) is not dict or len(value)!=len(keys) or set(value)!=set(keys): raise SavedDeltaInputError("CUSTODY")

def validate_artifact_set(artifacts: dict, *, decision_wrappers: list[dict]) -> None:
    if type(artifacts) is not dict or tuple(artifacts)!=ARTIFACT_ORDER: raise SavedDeltaInputError("CUSTODY")
    source=artifacts["source-manifest.json"]; pre=artifacts["preflight.json"]; candidate=artifacts["candidate-manifest.json"]; pin=artifacts["main-pin.json"]
    mapping=artifacts["mapping.json"]; packet=artifacts["blind-packet.json"]; rubric=artifacts["rubric.json"]; packet_freeze=artifacts["packet-freeze.json"]
    annotations=artifacts["annotations.json"]; annotation_freeze=artifacts["annotation-freeze.json"]; aggregate=artifacts["aggregate.json"]; final=artifacts["final-freeze.json"]; safe=artifacts["safe-summary.json"]
    _keys(source,("contract","version","task","t556_source_manifest_sha256","t556_preflight_sha256","t556_main_pin_sha256","t556_packet_sha256","t556_packet_freeze_file_sha256","t556_annotation_sha256","t556_annotation_freeze_sha256","t556_finalized_sha256","t556_final_freeze_file_sha256","t560_source_manifest_sha256","t560_candidate_manifest_sha256","t560_main_pin_sha256","t560_approved_dependency_bundle","t562_approved_dependency_bundle","files","rows","manifest_sha256"))
    _keys(pre,("contract","version","task","source_manifest_sha256","t556_core_artifacts_sha256","t560_approved_bundle_sha256","t562_approved_bundle_sha256","target_scope_sha256","prior_scope_sha256","mno_scope_sha256","pair_scope_sha256","helper_source_sha256","rubric_sha256","preflight_sha256"))
    _keys(candidate,("contract","version","source_manifest_sha256","preflight_sha256","target_scope_sha256","prior_scope_sha256","mno_scope_sha256","pair_scope_sha256","rows","counts","manifest_sha256"))
    _keys(pin,("contract","version","task","source_manifest_sha256","preflight_sha256","candidate_manifest_sha256","target_scope_sha256","prior_scope_sha256","mno_scope_sha256","pair_scope_sha256","t560_approved_bundle_sha256","t562_approved_bundle_sha256","coverage_rows_sha256","rows","pin_sha256"))
    _keys(packet,("contract","rubric_sha256","target_scope_sha256","packet_binding_sha256","rows")); _keys(rubric,("contract","version"))
    _keys(packet_freeze,("contract","version","source_manifest_sha256","preflight_sha256","candidate_manifest_sha256","main_pin_sha256","mapping_sha256","rubric_sha256","packet_sha256","packet_binding_sha256","target_scope_sha256","packet_rows","input_unknown_rows","freeze_sha256"))
    _keys(annotations,("contract","packet_sha256","rubric_sha256","packet_freeze_sha256","rows"))
    _keys(annotation_freeze,("contract","version","packet_sha256","rubric_sha256","packet_freeze_sha256","annotation_sha256","annotation_rows_sha256","rows","assertion_counts","fresh_independent_evaluator","freeze_sha256"))
    _keys(aggregate,("contract","version","source_manifest_sha256","preflight_sha256","candidate_manifest_sha256","main_pin_sha256","packet_freeze_sha256","annotation_freeze_sha256","rows","counts","pair_counts","pairs","aggregate_sha256"))
    _keys(final,("contract","version","task","source_manifest_sha256","preflight_sha256","candidate_manifest_sha256","main_pin_sha256","mapping_sha256","rubric_sha256","packet_sha256","packet_freeze_sha256","annotation_sha256","annotation_freeze_sha256","aggregate_sha256","helper_source_sha256","t560_approved_bundle_sha256","t562_approved_bundle_sha256","rows","pairs","counts","provider_calls","old_artifacts_modified","freeze_sha256"))
    _keys(safe,("contract","version","status","rows","pairs","counts","pair_counts","partition_counts","fresh_annotation_rows","input_unknown_rows","provider_calls","freeze_sha256"))
    if type(source["rows"]) is not list or type(candidate["rows"]) is not list or type(pin["rows"]) is not list or type(aggregate["rows"]) is not list or not all(len(x)==192 for x in (source["rows"],candidate["rows"],pin["rows"],aggregate["rows"])): raise SavedDeltaInputError("CUSTODY")
    for row in source["rows"]: _keys(row,("evaluation_id","ordinal","pair_key_sha256","profile_key_sha256","old_row_binding_sha256","old_selection_envelope_sha256","old_mechanical_row_sha256","saved_surface_sha256","accepted_final_sha256","old_decision_sha256","old_annotation_row_sha256","partition","source_row_sha256"))
    for row in candidate["rows"]: _keys(row,("evaluation_id","ordinal","partition","source_row_sha256","delta_input_status","delta_root_sha256","surface_witness_sha256","reason"))
    _keys(candidate["counts"],("co_gap","root_gap","prior","mno","target_ready","target_input_unknown"))
    for row in pin["rows"]: _keys(row,("evaluation_id","expected_source_row_sha256","expected_delta_root_sha256","expected_surface_witness_sha256"))
    for row in packet["rows"]: _keys(row,("delta_blind_id","accepted_surface","provenance"))
    for row in annotations["rows"]: _keys(row,("delta_blind_id","status","assertion","cited_provenance_ids","associations"))
    for row in aggregate["rows"]:
        _keys(row,("evaluation_id","ordinal","pair_key_sha256","profile_key_sha256","source_row_sha256","origin","decision_sha256","decision"))
        _keys(row["decision"],("binding","metric_value","reason_code","offending_provenance_ids","measurement_validity"))
    _keys(aggregate["counts"],("PASS","FAIL","UNKNOWN","MEASUREMENT_NOT_OBSERVED"))
    source_ids=[x.get("evaluation_id") for x in source["rows"]]; candidate_ids=[x.get("evaluation_id") for x in candidate["rows"]]; aggregate_ids=[x.get("evaluation_id") for x in aggregate["rows"]]
    if source_ids!=candidate_ids or source_ids!=aggregate_ids or len(set(source_ids))!=192: raise SavedDeltaInputError("CUSTODY")
    partitions={key:sum(x.get("partition")==key for x in candidate["rows"]) for key in ("CO_SURFACE_GAP","UNSELECTED_ROOT_GAP","PRIOR_READY","MEASUREMENT_NOT_OBSERVED")}
    if partitions!={"CO_SURFACE_GAP":5,"UNSELECTED_ROOT_GAP":7,"PRIOR_READY":167,"MEASUREMENT_NOT_OBSERVED":13} or candidate["counts"].get("prior")!=167 or candidate["counts"].get("mno")!=13: raise SavedDeltaInputError("CUSTODY")
    if type(mapping) is not list or len(mapping)!=len(packet["rows"]) or packet_freeze["packet_rows"]!=len(packet["rows"]) or annotation_freeze["rows"]!=len(annotations["rows"]): raise SavedDeltaInputError("CUSTODY")
    if type(decision_wrappers) is not list or len(decision_wrappers)!=12: raise SavedDeltaInputError("CUSTODY")
    fresh=[x for x in aggregate["rows"] if x.get("origin")=="FRESH_DELTA"]
    if len(fresh)!=12: raise SavedDeltaInputError("CUSTODY")
    for wrapper,row in zip(decision_wrappers,fresh):
        _keys(wrapper,("delta_binding","t562_decision","input_unknown_decision","decision_sha256"))
        if (wrapper["t562_decision"] is None)==(wrapper["input_unknown_decision"] is None) or wrapper["decision_sha256"]!=canonical_sha({k:v for k,v in wrapper.items() if k!="decision_sha256"}) or row["decision_sha256"]!=wrapper["decision_sha256"]: raise SavedDeltaInputError("CUSTODY")
    if sum(aggregate["counts"].values())!=192 or sum(aggregate["pair_counts"].values())!=96 or aggregate["pairs"]!=96: raise SavedDeltaInputError("CUSTODY")
    for value,field in ((source,"manifest_sha256"),(pre,"preflight_sha256"),(candidate,"manifest_sha256"),(pin,"pin_sha256"),(packet_freeze,"freeze_sha256"),(annotation_freeze,"freeze_sha256"),(aggregate,"aggregate_sha256"),(final,"freeze_sha256")):
        if not _self_hash(value,field): raise SavedDeltaInputError("CUSTODY")
    checks=(pre.get("source_manifest_sha256")==source["manifest_sha256"],candidate.get("source_manifest_sha256")==source["manifest_sha256"],candidate.get("preflight_sha256")==pre["preflight_sha256"],
            pin.get("candidate_manifest_sha256")==candidate["manifest_sha256"],pin.get("source_manifest_sha256")==source["manifest_sha256"],
            packet_freeze.get("candidate_manifest_sha256")==candidate["manifest_sha256"],packet_freeze.get("main_pin_sha256")==pin["pin_sha256"],
            packet_freeze.get("mapping_sha256")==canonical_sha(mapping),packet_freeze.get("packet_sha256")==canonical_sha(packet),packet_freeze.get("rubric_sha256")==canonical_sha(rubric),
            annotation_freeze.get("annotation_sha256")==canonical_sha(annotations),annotation_freeze.get("packet_freeze_sha256")==packet_freeze["freeze_sha256"],
            aggregate.get("candidate_manifest_sha256")==candidate["manifest_sha256"],aggregate.get("main_pin_sha256")==pin["pin_sha256"],aggregate.get("packet_freeze_sha256")==packet_freeze["freeze_sha256"],aggregate.get("annotation_freeze_sha256")==annotation_freeze["freeze_sha256"],
            final.get("aggregate_sha256")==aggregate["aggregate_sha256"],final.get("candidate_manifest_sha256")==candidate["manifest_sha256"],final.get("counts")==aggregate.get("counts"),safe.get("freeze_sha256")==final["freeze_sha256"],safe.get("counts")==final.get("counts"))
    if not all(checks): raise SavedDeltaInputError("CUSTODY")


def write_once(*, root: Path, destination: Path, payload: dict, expected_source_sha256: str,
               source_payload: dict) -> dict:
    root=root.resolve(strict=True); destination=destination.resolve(strict=False)
    if root not in destination.parents or destination.exists() or destination.is_symlink():
        raise SavedDeltaInputError("CUSTODY")
    if canonical_sha(source_payload)!=expected_source_sha256:
        raise SavedDeltaInputError("SOURCE_CHANGED")
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open("xb") as stream: stream.write(raw)
    try:
        saved=json.loads(destination.read_bytes().decode("utf-8"),parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    except (UnicodeError,json.JSONDecodeError,ValueError):
        raise SavedDeltaInputError("CUSTODY") from None
    if canonical_sha(saved)!=canonical_sha(payload) or canonical_sha(source_payload)!=expected_source_sha256:
        raise SavedDeltaInputError("CUSTODY")
    return {"artifact_sha256":canonical_sha(saved),"size_bytes":len(raw),"source_sha256":expected_source_sha256}


def freeze_container(*, root: Path, output_dir: Path, artifacts: dict[str,dict],
                     source_payload: dict, expected_source_sha256: str,
                     code_files: tuple[Path,...], expected_code_sha256: tuple[str,...],
                     decision_wrappers: list[dict]) -> dict:
    root=root.resolve(strict=True); output_dir=output_dir.resolve(strict=False)
    if root not in output_dir.parents: raise SavedDeltaInputError("CUSTODY")
    validate_artifact_set(artifacts,decision_wrappers=decision_wrappers)
    if len(code_files)!=len(expected_code_sha256): raise SavedDeltaInputError("CUSTODY")
    def code_hashes():
        import hashlib
        values=[]
        for path in code_files:
            resolved=path.resolve(strict=True)
            if root not in resolved.parents or resolved.is_symlink(): raise SavedDeltaInputError("CUSTODY")
            values.append(hashlib.sha256(resolved.read_bytes()).hexdigest())
        return tuple(values)
    if canonical_sha(source_payload)!=expected_source_sha256 or code_hashes()!=expected_code_sha256: raise SavedDeltaInputError("SOURCE_CHANGED")
    if output_dir.exists() or output_dir.is_symlink(): raise SavedDeltaInputError("CUSTODY")
    output_dir.mkdir(parents=False)
    evidence={}
    for name in ARTIFACT_ORDER:
        evidence[name]=write_once(root=root,destination=output_dir/name,payload=artifacts[name],expected_source_sha256=expected_source_sha256,source_payload=source_payload)
    if canonical_sha(source_payload)!=expected_source_sha256 or code_hashes()!=expected_code_sha256: raise SavedDeltaInputError("SOURCE_CHANGED")
    safe=artifacts["safe-summary.json"]
    forbidden=("evaluation_id","delta_blind_id","pair_key_sha256","profile_key_sha256","condition","model","seed","accepted_surface")
    raw=json.dumps(safe,ensure_ascii=False,sort_keys=True)
    if any(word in raw for word in forbidden): raise SavedDeltaInputError("CUSTODY")
    return {"status":"COMPLETE","artifacts":evidence,"source_sha256":expected_source_sha256,"code_sha256":list(expected_code_sha256)}
