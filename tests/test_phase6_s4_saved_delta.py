from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from tests.fixtures import phase6_s4_saved_delta as subject
from tests.fixtures.phase6_s4_unselected_ability_root import decide_unselected_ability_s4_v2
from tests.test_phase6_s4_unselected_ability_root import prepared as real_unselected_case


D = "a" * 64

def _json_value(value):
    if isinstance(value,tuple): return [_json_value(x) for x in value]
    if isinstance(value,list): return [_json_value(x) for x in value]
    if isinstance(value,dict): return {k:_json_value(v) for k,v in value.items()}
    return value


def _bundle(dep: str) -> dict:
    files = subject.T560_FILES if dep == "T560" else subject.T562_FILES
    design,review=subject.DEPENDENCY_METADATA[dep]
    def entry(role,path):
        raw=Path(path).read_bytes(); return {"role":role,"repository_path":path,"sha256":hashlib.sha256(raw).hexdigest(),"size_bytes":len(raw)}
    out={"contract":f"{dep}_APPROVED_DEPENDENCY_BUNDLE_V1","design_path":design,"design_sha256":hashlib.sha256(Path(design).read_bytes()).hexdigest(),
         "tool_review_path":review,"tool_review_sha256":hashlib.sha256(Path(review).read_bytes()).hexdigest(),"review_scope_sha256":"",
         "files":[entry(role,path) for role,path in files],"bundle_sha256":""}
    scope={key:out[key] for key in ("contract","design_path","design_sha256","tool_review_path","tool_review_sha256","files")}
    out["review_scope_sha256"]=subject.canonical_sha(scope)
    out["bundle_sha256"]=subject.canonical_sha({**scope,"review_scope_sha256":out["review_scope_sha256"]})
    return out


def _decision(metric="PASS"):
    return {"binding":{"fixed":True},"metric_value":metric,"reason_code":"S4_PASS","offending_provenance_ids":[],"measurement_validity":"VALID"}


def _surface_witness(*, evaluation_id: str, partition: str, bound_surface_sha256: str) -> dict:
    if partition == "CO_SURFACE_GAP":
        payload={"contract":"S4_CO_SURFACE_SOURCE_BINDING_V1","evaluation_id":evaluation_id,"old_blind_id":f"old-{evaluation_id}",
                 "row_identity_sha256":D,"source_sha256":D,"bindings_sha256":D,"catalog_sha256":D,"accepted_final_sha256":D,
                 "accepted_plan_sha256":D,"old_row_binding_sha256":D,"old_selection_envelope_sha256":D,"saved_surface_sha256":D,
                 "raw_selection":{},"option_resolution":{},"role_resolution":{},"catalog_membership_sha256":D,
                 "fact_binding_set_sha256":D,"claim_projection_sha256":D}
    else:
        payload={"contract":"S4_EXISTING_SURFACE_WITNESS_V1","evaluation_id":evaluation_id,"old_row_binding_sha256":D,
                 "saved_surface_sha256":D,"bound_surface_sha256":bound_surface_sha256,"accepted_final_sha256":D}
    return {**payload,"witness_sha256":subject.canonical_sha(payload)}


def _source() -> dict:
    rows=[]; runtime=[]; final_rows=[]; annotation_rows=[]
    freeze,root_row,mechanical,semantic=real_unselected_case(assertion="NONE")
    prior=_decision("UNKNOWN")
    mno={"binding":None,"metric_value":"MEASUREMENT_NOT_OBSERVED","reason_code":"PUBLIC_SURFACE_NOT_OBSERVED","offending_provenance_ids":[],"measurement_validity":"INVALID"}
    for i in range(192):
        partition="CO_SURFACE_GAP" if i<5 else "UNSELECTED_ROOT_GAP" if i<12 else "PRIOR_READY" if i<179 else "MEASUREMENT_NOT_OBSERVED"
        ready={"accepted_surface":deepcopy(root_row["accepted_surface"]),"provenance":[],"t562_call":{"execution":{"run_status":"COMPLETE","structural_status":"ACCEPTED"},"audit":{"sealed_audit":"COMPLETE","public_surface":"TEXT"},"conversion":{"status":"COMPLETE"},"row":_json_value(root_row),"expected_freeze_sha256":freeze["freeze_sha256"],"base_records":_json_value(mechanical),"semantic":_json_value(semantic)}} if i<12 else None
        eid=f"e{i:03}"; old_decision=prior if partition=="PRIOR_READY" else mno if partition=="MEASUREMENT_NOT_OBSERVED" else None
        note={"evaluation_id":eid,"status":"ANNOTATED" if partition=="PRIOR_READY" else "NOT_REQUIRED","assertion":"NONE" if partition=="PRIOR_READY" else None,"cited_provenance_ids":[],"associations":[]}
        source_row={"evaluation_id":eid,"ordinal":i,"pair_key_sha256":subject.canonical_sha(f"pair{i//2}"),"profile_key_sha256":subject.canonical_sha(f"profile{i%2}"),
                    "old_row_binding_sha256":D,"old_selection_envelope_sha256":D,"old_mechanical_row_sha256":D,"saved_surface_sha256":D,"accepted_final_sha256":D,
                    "old_decision_sha256":subject.canonical_sha(old_decision) if old_decision else None,"old_annotation_row_sha256":subject.canonical_sha(note) if partition=="PRIOR_READY" else None,
                    "partition":partition}
        source_row["source_row_sha256"]=subject.canonical_sha(source_row); source_row_sha=source_row["source_row_sha256"]
        rows.append(source_row)
        binding=None
        if i<12:
            surface_witness=_surface_witness(evaluation_id=eid,partition=partition,bound_surface_sha256=root_row["binding"]["surface_sha256"])
            binding={"contract":subject.CONTRACT,"evaluation_id":eid,"delta_blind_id":f"blind-{i}","partition":partition,"source_row_sha256":source_row_sha,
                     "old_row_binding_sha256":D,"old_selection_envelope_sha256":D,"old_mechanical_row_sha256":D,"accepted_final_sha256":D,"saved_surface_sha256":D,
                     "delta_surface_sha256":subject.canonical_sha(ready["accepted_surface"]),"base_provenance_sha256":subject.canonical_sha([]),"t562_row_sha256":root_row["binding"]["row_sha256"],"t562_witness_sha256":root_row["witness"]["witness_sha256"],
                     "t562_inventory_freeze_sha256":freeze["freeze_sha256"],"surface_witness_sha256":surface_witness["witness_sha256"],"main_pin_sha256":D}
            binding["binding_sha256"]=subject.canonical_sha(binding)
        finalized={"evaluation_id":eid,"status":"MEASUREMENT_NOT_OBSERVED" if partition=="MEASUREMENT_NOT_OBSERVED" else "DECIDED","connector_reason":"NONE","projected_decision":deepcopy(old_decision),"metric_value":old_decision["metric_value"] if old_decision else "UNKNOWN","measurement_validity":old_decision["measurement_validity"] if old_decision else "INVALID","binding":deepcopy(old_decision["binding"]) if old_decision else None,"offending_provenance_ids":[]} if old_decision else {"evaluation_id":eid,"status":"CONNECTOR_UNKNOWN","connector_reason":"TEST_TARGET","projected_decision":None,"metric_value":"UNKNOWN","measurement_validity":"INVALID","binding":None,"offending_provenance_ids":[]}
        final_rows.append(finalized); annotation_rows.append(note)
        runtime.append({"evaluation_id":eid,"binding":binding,"ready_input":ready,
                        "prior_reference":{"evaluation_id":eid,"source_row_sha256":source_row_sha,"old_row_binding_sha256":D,"old_annotation_row_sha256":subject.canonical_sha(note),"old_decision_sha256":subject.canonical_sha(prior),"t556_annotation_sha256":"","t556_finalized_sha256":"","t556_final_freeze_file_sha256":D,"decision":deepcopy(prior),"finalized_row":finalized,"reference_sha256":""} if partition=="PRIOR_READY" else None,
                        "mno_reference":{"evaluation_id":eid,"source_row_sha256":source_row_sha,"old_row_binding_sha256":D,"old_decision_sha256":subject.canonical_sha(mno),"t556_finalized_sha256":"","t556_final_freeze_file_sha256":D,"decision":deepcopy(mno),"finalized_row":finalized,"reference_sha256":""} if partition=="MEASUREMENT_NOT_OBSERVED" else None})
    snapshot=Path("tests/fixtures/phase6_s4_saved_delta.py").read_bytes()
    finalized_artifact={"contract":"S4_PROJECTED_ABILITY_SAVED_BINDING_V1","freeze":{"rows":192},"results":final_rows,"safe":{"rows":192}}
    annotation_artifact={"contract":"S4_ANNOTATION_V1","rows":annotation_rows}
    finalized_sha=subject.canonical_sha(finalized_artifact); annotation_sha=subject.canonical_sha(annotation_artifact)
    for item in runtime:
        if item["prior_reference"] is not None:
            ref=item["prior_reference"]; ref["t556_annotation_sha256"]=annotation_sha; ref["t556_finalized_sha256"]=finalized_sha; ref["reference_sha256"]=subject.canonical_sha({k:ref[k] for k in ref if k not in ("reference_sha256","finalized_row")})
        if item["mno_reference"] is not None:
            ref=item["mno_reference"]; ref["t556_finalized_sha256"]=finalized_sha; ref["reference_sha256"]=subject.canonical_sha({k:ref[k] for k in ref if k not in ("reference_sha256","finalized_row")})
    source={"contract":subject.CONTRACT,"version":"V1","task":"T563",
            "t556_source_manifest_sha256":D,"t556_preflight_sha256":D,"t556_main_pin_sha256":D,"t556_packet_sha256":D,
            "t556_packet_freeze_file_sha256":D,"t556_annotation_sha256":annotation_sha,"t556_annotation_freeze_sha256":D,
            "t556_finalized_sha256":finalized_sha,"t556_final_freeze_file_sha256":D,"t560_source_manifest_sha256":D,
            "t560_candidate_manifest_sha256":D,"t560_main_pin_sha256":D,
            "t560_approved_dependency_bundle":_bundle("T560"),"t562_approved_dependency_bundle":_bundle("T562"),
            "files":[{"source_id":"phase6_s4_saved_delta.py","original_name":"helper-snapshot.py","sha256":hashlib.sha256(snapshot).hexdigest(),"size":len(snapshot)}],"rows":rows}
    source["manifest_sha256"]=subject.canonical_sha(source)
    return source,runtime,finalized_artifact,annotation_artifact


def _sealed(source,runtime):
    b560=source["t560_approved_dependency_bundle"]["bundle_sha256"]; b562=source["t562_approved_dependency_bundle"]["bundle_sha256"]
    rubric={"contract":"S4_SAVED_GAP_DELTA_RUBRIC_V1","version":"V1"}
    pre=subject.build_preflight(source_manifest=source,t556_core_artifacts_sha256=D,helper_source_sha256=D,rubric_sha256=subject.canonical_sha(rubric),repository_root=Path.cwd(),source_root=Path("tests/fixtures"),expected_t560_bundle_sha256=b560,expected_t562_bundle_sha256=b562)
    candidate=subject.build_candidate_manifest(source_manifest=source,preflight=pre,runtime_rows=runtime)
    by={x["evaluation_id"]:x for x in runtime}; roots=[]
    for row in source["rows"]:
        private=by[row["evaluation_id"]]; ready=row["partition"] in subject.PARTITIONS[:2] and private["ready_input"] is not None
        roots.append({"evaluation_id":row["evaluation_id"],"expected_source_row_sha256":row["source_row_sha256"],"expected_delta_root_sha256":subject.canonical_sha(private["ready_input"]["t562_call"]["row"]) if ready else None,"expected_surface_witness_sha256":private["binding"]["surface_witness_sha256"] if ready else None})
    independent={"contract":"S4_SAVED_DELTA_MAIN_ROOTS_V1","rows":roots}; independent["roots_sha256"]=subject.canonical_sha(independent)
    target_roots=[]
    for row in candidate["rows"][:12]:
        private=by[row["evaluation_id"]]
        t562_row=deepcopy(private["ready_input"]["t562_call"]["row"]) if private["ready_input"] else None
        target_roots.append({"evaluation_id":row["evaluation_id"],"delta_input_status":row["delta_input_status"],
                             "t562_row":t562_row,
                             "surface_witness":_surface_witness(evaluation_id=row["evaluation_id"],partition=row["partition"],bound_surface_sha256=t562_row["binding"]["surface_sha256"]) if t562_row else None})
    state={"contract":"S4_SAVED_DELTA_MAIN_STATE_V1","fixed_roots_sha256":D,"source_rows":deepcopy(source["rows"]),"expected_candidate":deepcopy(candidate),"independent_roots":independent,"target_roots":target_roots}
    state["state_sha256"]=subject.canonical_sha(state)
    pin=subject.build_main_pin(independent_state=state,expected_independent_state_sha256=state["state_sha256"],expected_independent_roots_sha256=independent["roots_sha256"],preflight=pre,candidate_manifest=candidate,t560_approved_bundle_sha256=b560,t562_approved_bundle_sha256=b562)
    sealed=subject.seal_blind_packet(source_manifest=source,runtime_rows=runtime,preflight=pre,candidate_manifest=candidate,main_pin=pin,blind_ids=[f"blind-{i}" for i in range(12)],expected_pin_sha256=pin["pin_sha256"],repository_root=Path.cwd(),source_root=Path("tests/fixtures"),expected_t560_bundle_sha256=b560,expected_t562_bundle_sha256=b562)
    notes={"rows":[{"delta_blind_id":row["delta_blind_id"],"status":"ANNOTATED","assertion":"NONE","cited_provenance_ids":[],"associations":[]} for row in sealed["packet"]["rows"]]}
    frozen=subject.freeze_annotations(sealed=sealed,annotations=notes,fresh_independent_evaluator=True)
    return pre,candidate,pin,sealed,frozen,rubric,state


def test_exact_bundles_blind_fresh_and_full_aggregate():
    source,runtime,old_final,old_annotations=_source(); pre,candidate,pin,sealed,frozen,_,_=_sealed(source,runtime); calls=[]
    def decide(**kwargs):
        calls.append(kwargs)
        assert kwargs["semantic"]["base"]["assertion"]=="NONE"
        return decide_unselected_ability_s4_v2(**kwargs)
    finalized=subject.finalize(source_manifest=source,runtime_rows=runtime,finalized_artifact=old_final,annotation_artifact=old_annotations,preflight=pre,candidate_manifest=candidate,main_pin=pin,sealed=sealed,
                               frozen_annotations=frozen,expected_pin_sha256=pin["pin_sha256"],repository_root=Path.cwd(),source_root=Path("tests/fixtures"),expected_t560_bundle_sha256=source["t560_approved_dependency_bundle"]["bundle_sha256"],expected_t562_bundle_sha256=source["t562_approved_dependency_bundle"]["bundle_sha256"],decider=decide)
    aggregate=finalized["aggregate"]
    assert len(calls)==12
    assert len(finalized["decision_wrappers"])==12 and sum(x["t562_decision"] is not None for x in finalized["decision_wrappers"])==12
    assert len(aggregate["rows"])==192 and aggregate["pairs"]==96
    assert aggregate["counts"]=={"PASS":12,"FAIL":0,"UNKNOWN":167,"MEASUREMENT_NOT_OBSERVED":13}
    assert sum(aggregate["pair_counts"].values())==96
    assert set(sealed["packet"]["rows"][0])=={"delta_blind_id","accepted_surface","provenance"}
    final=subject.build_final_freeze(source_manifest=source,preflight=pre,candidate_manifest=candidate,sealed=sealed,frozen_annotations=frozen,
                                     aggregate=aggregate,expected_pin_sha256=pin["pin_sha256"],mapping_sha256=subject.canonical_sha(sealed["mapping"]),helper_source_sha256=D,repository_root=Path.cwd(),source_root=Path("tests/fixtures"),expected_t560_bundle_sha256=source["t560_approved_dependency_bundle"]["bundle_sha256"],expected_t562_bundle_sha256=source["t562_approved_dependency_bundle"]["bundle_sha256"])
    assert final["safe_summary"]["provider_calls"]==0
    assert "rows" not in final["safe_summary"]["counts"]


@pytest.mark.parametrize("mutation",["missing","extra","reorder","duplicate","bytes","review"])
def test_dependency_bundle_rejects_nonexact_set(mutation):
    bundle=_bundle("T560")
    if mutation=="missing": bundle["files"].pop()
    elif mutation=="extra": bundle["files"].append(deepcopy(bundle["files"][0]))
    elif mutation=="reorder": bundle["files"][0],bundle["files"][1]=bundle["files"][1],bundle["files"][0]
    elif mutation=="duplicate": bundle["files"][1]=deepcopy(bundle["files"][0])
    elif mutation=="bytes": bundle["files"][0]["sha256"]="b"*64
    else: bundle["tool_review_sha256"]="b"*64
    with pytest.raises(subject.SavedDeltaInputError): subject.validate_dependency_bundle(bundle,dependency="T560",repository_root=Path.cwd(),expected_bundle_sha256=_bundle("T560")["bundle_sha256"])


def test_partition_reference_annotation_and_wire_negatives():
    source,runtime,_,_=_source()
    broken=deepcopy(source); broken["rows"][0]["partition"]="PRIOR_READY"
    broken["manifest_sha256"]=subject.canonical_sha({k:v for k,v in broken.items() if k!="manifest_sha256"})
    with pytest.raises(subject.SavedDeltaInputError): subject.validate_source_manifest(broken,repository_root=Path.cwd(),source_root=Path("tests/fixtures"),expected_t560_bundle_sha256=source["t560_approved_dependency_bundle"]["bundle_sha256"],expected_t562_bundle_sha256=source["t562_approved_dependency_bundle"]["bundle_sha256"])
    _,_,_,sealed,_,_,_=_sealed(source,runtime)
    notes={"rows":[{"delta_blind_id":row["delta_blind_id"],"status":"ANNOTATED","assertion":"NONE","cited_provenance_ids":[],"associations":[]} for row in sealed["packet"]["rows"]]}
    notes["rows"][0]["delta_blind_id"]="foreign"
    with pytest.raises(subject.SavedDeltaInputError): subject.freeze_annotations(sealed=sealed,annotations=notes,fresh_independent_evaluator=True)
    with pytest.raises(subject.SavedDeltaInputError): subject.strict_parse(b'{"x":1,"x":2}')
    with pytest.raises(subject.SavedDeltaInputError): subject.strict_parse(b'{"x":NaN}')


def _base_record(pid="pv1",origin="EXPLICIT_SURFACE_REF"):
    return {"binding":{"private":"retained"},"provenance_id":pid,"lane":"AUTHORITATIVE_ABILITY","origin":origin,
            "ref_resolution":"RESOLVED","selection":"NOT_APPLICABLE" if origin=="EXPLICIT_SURFACE_REF" else "SELECTED",
            "actor_match":"TRUE","visibility":"PUBLIC","authority":"AUTHORIZED",
            "canonical_value":{"target":{"tag":"VALUE","value":"private-target"},"result":{"tag":"VALUE","value":"private-result"}},
            "binding_status":"COMPLETE"}


def test_blind_base_projection_is_exact_ordered_and_private_free():
    records=[_base_record(),_base_record("pv2","SELECTED_PUBLIC_FACT")]
    projected=subject.project_blind_base_provenance(records)
    assert [x["provenance_id"] for x in projected]==["pv1","pv2"]
    assert [x["identity_kind"] for x in projected]==["SOURCE_REF",None]
    forbidden={"binding","origin","source_kind","canonical_value","owner","inventory","condition"}
    assert all(not forbidden.intersection(row) for row in projected)
    duplicate=deepcopy(records); duplicate[1]["provenance_id"]="pv1"
    with pytest.raises(subject.SavedDeltaInputError): subject.project_blind_base_provenance(duplicate)
    illegal=deepcopy(records); illegal[0]["authority"]="PRIVATE_OWNER"
    with pytest.raises(subject.SavedDeltaInputError): subject.project_blind_base_provenance(illegal)
    cross_binding=deepcopy(records); cross_binding[1]["binding"]={"private":"other"}
    with pytest.raises(subject.SavedDeltaInputError): subject.project_blind_base_provenance(cross_binding)


def test_blind_packet_shuffle_mapping_and_annotation_source_ref_rules(monkeypatch):
    source,runtime,_,_=_source()
    class ReverseRandom:
        def shuffle(self,values): values.reverse()
    monkeypatch.setattr(subject.secrets,"SystemRandom",lambda:ReverseRandom())
    _,_,_,sealed,_,_,_=_sealed(source,runtime)
    assert [x["delta_blind_id"] for x in sealed["packet"]["rows"]]==[x["delta_blind_id"] for x in sealed["mapping"]]
    assert sealed["packet"]["rows"][0]["delta_blind_id"]=="blind-11"

    # cited IDs may only name structural SOURCE_REF records; ref-less authority stays explicit and UNKNOWN downstream.
    row=sealed["packet"]["rows"][0]
    row["provenance"]=subject.project_blind_base_provenance([_base_record("ref"),_base_record("selected","SELECTED_PUBLIC_FACT")])
    notes={"rows":[{"delta_blind_id":item["delta_blind_id"],"status":"ANNOTATED","assertion":"NONE","cited_provenance_ids":[],"associations":[]} for item in sealed["packet"]["rows"]]}
    notes["rows"][0].update(assertion="EXPLICIT_AUTHORITY_ASSERTION",cited_provenance_ids=[])
    subject.freeze_annotations(sealed=sealed,annotations=notes,fresh_independent_evaluator=True)
    bad=deepcopy(notes); bad["rows"][0]["cited_provenance_ids"]=["selected"]
    with pytest.raises(subject.SavedDeltaInputError): subject.freeze_annotations(sealed=sealed,annotations=bad,fresh_independent_evaluator=True)


@pytest.mark.parametrize("tamper",("cross_row","reference","delta_surface"))
def test_ready_binding_rejects_self_consistent_cross_links(tamper):
    source,runtime,_,_=_source(); broken=deepcopy(runtime)
    if tamper=="cross_row":
        broken[0]["binding"],broken[1]["binding"]=broken[1]["binding"],broken[0]["binding"]
    else:
        key="accepted_final_sha256" if tamper=="reference" else "delta_surface_sha256"
        broken[0]["binding"][key]="b"*64
        broken[0]["binding"]["binding_sha256"]=subject.canonical_sha({k:v for k,v in broken[0]["binding"].items() if k!="binding_sha256"})
    with pytest.raises(subject.SavedDeltaInputError): subject.build_candidate_manifest(source_manifest=source,preflight={"preflight_sha256":D,"target_scope_sha256":D,"prior_scope_sha256":D,"mno_scope_sha256":D,"pair_scope_sha256":D},runtime_rows=broken)


def test_seal_rejects_self_consistent_runtime_row_not_in_main_pin():
    source,runtime,_,_=_source(); pre,candidate,pin,_,_,_,_=_sealed(source,runtime)
    broken=deepcopy(runtime); private=broken[0]; row=private["ready_input"]["t562_call"]["row"]
    row["accepted_plan"]["claim_id"]="self-consistent-tamper"
    row["binding"]["accepted_plan_sha256"]=subject.canonical_sha(row["accepted_plan"])
    payload={"contract":"S4_UNSELECTED_ABILITY_ROOT_V2",**{k:row["binding"][k] for k in row["binding"] if k!="row_sha256"}}
    row["binding"]["row_sha256"]=subject.canonical_sha(payload)
    row["witness"]["accepted_plan_sha256"]=row["binding"]["accepted_plan_sha256"]
    row["witness"]["row_sha256"]=row["binding"]["row_sha256"]
    row["witness"]["witness_sha256"]=subject.canonical_sha({k:v for k,v in row["witness"].items() if k!="witness_sha256"})
    private["binding"]["t562_row_sha256"]=row["binding"]["row_sha256"]
    private["binding"]["t562_witness_sha256"]=row["witness"]["witness_sha256"]
    private["binding"]["binding_sha256"]=subject.canonical_sha({k:v for k,v in private["binding"].items() if k!="binding_sha256"})
    with pytest.raises(subject.SavedDeltaInputError):
        subject.seal_blind_packet(source_manifest=source,preflight=pre,candidate_manifest=candidate,main_pin=pin,runtime_rows=broken,
            blind_ids=[f"new-{i}" for i in range(12)],expected_pin_sha256=pin["pin_sha256"],repository_root=Path.cwd(),source_root=Path("tests/fixtures"),
            expected_t560_bundle_sha256=source["t560_approved_dependency_bundle"]["bundle_sha256"],expected_t562_bundle_sha256=source["t562_approved_dependency_bundle"]["bundle_sha256"])


def test_filling_binding_main_pin_does_not_change_candidate_root():
    source,runtime,_,_=_source(); pre,candidate,_,_,_,_,_=_sealed(source,runtime)
    changed=deepcopy(runtime)
    for item in changed[:12]:
        binding=item["binding"]; binding["main_pin_sha256"]="b"*64; binding["binding_sha256"]=subject.canonical_sha({k:v for k,v in binding.items() if k!="binding_sha256"})
    rebuilt=subject.build_candidate_manifest(source_manifest=source,preflight=pre,runtime_rows=changed)
    assert rebuilt["rows"]==candidate["rows"]
    assert rebuilt["manifest_sha256"]==candidate["manifest_sha256"]


def test_canonical_sorted_object_roundtrip_keeps_exact_contracts():
    source,runtime,_,_=_source()
    parsed=subject.strict_parse(subject.canonical_wire(source))
    checked=subject.validate_source_manifest(parsed,repository_root=Path.cwd(),source_root=Path("tests/fixtures"),
        expected_t560_bundle_sha256=source["t560_approved_dependency_bundle"]["bundle_sha256"],expected_t562_bundle_sha256=source["t562_approved_dependency_bundle"]["bundle_sha256"])
    pre,candidate,_,sealed,frozen,_,_=_sealed(checked,runtime)
    assert subject.strict_parse(subject.canonical_wire(pre))["preflight_sha256"]==pre["preflight_sha256"]
    assert subject.strict_parse(subject.canonical_wire(candidate))["manifest_sha256"]==candidate["manifest_sha256"]
    assert subject.strict_parse(subject.canonical_wire(sealed["packet"]))==sealed["packet"]
    assert subject.strict_parse(subject.canonical_wire(frozen["annotations"]))==frozen["annotations"]


@pytest.mark.parametrize("change",("cross_row","content"))
def test_main_target_roots_reject_cross_row_and_content_tamper(change):
    source,runtime,_,_=_source(); pre,candidate,_,_,_,_,state=_sealed(source,runtime)
    bad=deepcopy(state)
    if change=="cross_row": bad["target_roots"][0],bad["target_roots"][1]=bad["target_roots"][1],bad["target_roots"][0]
    else: bad["target_roots"][0]["surface_witness"]["evaluation_id"]="foreign"
    bad["state_sha256"]=subject.canonical_sha({k:v for k,v in bad.items() if k!="state_sha256"})
    with pytest.raises(subject.SavedDeltaInputError):
        subject.build_main_pin(independent_state=bad,expected_independent_state_sha256=bad["state_sha256"],expected_independent_roots_sha256=bad["independent_roots"]["roots_sha256"],preflight=pre,candidate_manifest=candidate,t560_approved_bundle_sha256=source["t560_approved_dependency_bundle"]["bundle_sha256"],t562_approved_bundle_sha256=source["t562_approved_dependency_bundle"]["bundle_sha256"])

