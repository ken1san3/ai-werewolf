from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import secrets
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from tests.fixtures import phase6_s4_saved_binding as saved
from tests.fixtures import phase6_s4_common_provenance as common
from scripts import phase6_quality_probe_v2 as q


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


BASE_FINAL = {"decision": {"message": "hello"}, "discussion": {"co_judgment": None}}
BASE_SURFACE = {"kind": "TEXT", "text": "hello", "comment": None, "claims": []}
CAND_FINAL = {"message": "hello"}


def baseline_wrapper(content=None):
    text = json.dumps(BASE_FINAL, ensure_ascii=False, separators=(",", ":")) if content is None else content
    return wire({"final_content": text, "final_output_sha256": hashlib.sha256(text.encode()).hexdigest()})


def candidate_response(content=None):
    text = json.dumps(CAND_FINAL, ensure_ascii=False, separators=(",", ":")) if content is None else content
    return wire({"choices": [{"message": {"role": "assistant", "content": text}}]})


def test_baseline_member_hash_and_surface():
    raw = baseline_wrapper()
    result = saved.extract_baseline_final(wrapper_bytes=raw,
        snapshot_file_sha256=hashlib.sha256(raw).hexdigest(), saved_surface=BASE_SURFACE)
    assert result["accepted_final_sha256"] == hashlib.sha256(
        json.dumps(BASE_FINAL, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def test_candidate_member_hash_and_surface():
    raw = candidate_response()
    result = saved.extract_candidate_final(response_bytes=raw,
        snapshot_file_sha256=hashlib.sha256(raw).hexdigest(), saved_final=CAND_FINAL,
        saved_surface=BASE_SURFACE)
    assert result["json_pointer"] == "/choices/0/message/content"


@pytest.mark.parametrize("content", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_inner_json_rejects_duplicate_and_nonfinite(content):
    raw = candidate_response(content)
    with pytest.raises(saved.SavedBindingError):
        saved.extract_candidate_final(response_bytes=raw,
            snapshot_file_sha256=hashlib.sha256(raw).hexdigest(), saved_final={"x": 2},
            saved_surface={"kind": "ABSENT", "text": None, "comment": None, "claims": []})


def test_baseline_outer_exact_and_member_digest():
    raw = wire({"final_content": "{}", "final_output_sha256": "0" * 64, "extra": 1})
    with pytest.raises(saved.SavedBindingError):
        saved.extract_baseline_final(wrapper_bytes=raw,
            snapshot_file_sha256=hashlib.sha256(raw).hexdigest(), saved_surface={})


def rows():
    digest = "1" * 64
    output = []
    for index in range(192):
        status = "MEASUREMENT_NOT_OBSERVED" if index < 13 else "READY" if index < 190 else "INPUT_INVALID"
        output.append({"evaluation_id": f"e{index}", "old_row_binding_sha256": digest,
            "source_witness_sha256": digest,
            "projected_row_sha256": digest if status == "READY" else None,
            "custodian_freeze_sha256": digest if status == "READY" else None,
            "status": status})
    return output


def manifest():
    return saved.build_candidate_manifest(preflight_sha256="2" * 64, rows=rows())


def pin(candidate):
    ready = [r for r in candidate["rows"] if r["status"] == "READY"]
    value = {"contract": saved.CONTRACT, "task": "T556",
        "preflight_sha256": candidate["preflight_sha256"],
        "candidate_manifest_sha256": candidate["manifest_sha256"],
        "rows": [{"evaluation_id": r["evaluation_id"],
                  "expected_freeze_sha256": r["custodian_freeze_sha256"]} for r in ready],
        "ready_count": len(ready), "unavailable_count": 2, "unobserved_count": 13}
    value["pin_sha256"] = saved.canonical_sha(value)
    return value


def test_manifest_and_separate_pin():
    candidate = manifest(); root = pin(candidate)
    resolved = saved.validate_main_pin(candidate_manifest=candidate, main_pin=root,
                                       expected_pin_sha256=root["pin_sha256"])
    assert len(resolved) == 177


@pytest.mark.parametrize("mutation", ["root", "count", "duplicate"])
def test_pin_and_manifest_tamper_rejected(mutation):
    candidate = manifest(); root = pin(candidate)
    if mutation == "root": root["pin_sha256"] = "0" * 64
    elif mutation == "count": root["ready_count"] += 1
    else: candidate["rows"][1]["evaluation_id"] = candidate["rows"][0]["evaluation_id"]
    with pytest.raises(saved.SavedBindingError):
        saved.validate_main_pin(candidate_manifest=candidate, main_pin=root,
                                expected_pin_sha256=pin(manifest())["pin_sha256"])


def annotations(candidate):
    result = []
    for row in candidate["rows"]:
        status = "ANNOTATED" if row["status"] == "READY" else "NOT_REQUIRED" if row["status"] == "MEASUREMENT_NOT_OBSERVED" else "CONNECTOR_SKIPPED"
        result.append({"evaluation_id": row["evaluation_id"], "status": status,
            "assertion": "NONE" if status == "ANNOTATED" else None,
            "cited_provenance_ids": [], "associations": []})
    return result


def test_annotation_partition_keeps_192_rows():
    candidate = manifest()
    result = saved.validate_annotations(candidate_manifest=candidate,
                                        annotations=annotations(candidate))
    assert len(result) == 192


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "wrong-status"])
def test_annotation_set_is_closed(mutation):
    candidate = manifest(); values = annotations(candidate)
    if mutation == "missing": values.pop()
    elif mutation == "extra": values.append(deepcopy(values[-1]))
    elif mutation == "duplicate": values[1]["evaluation_id"] = values[0]["evaluation_id"]
    else: values[-1]["status"] = "ANNOTATED"
    with pytest.raises(saved.SavedBindingError):
        saved.validate_annotations(candidate_manifest=candidate, annotations=values)


def test_connector_unknown_is_outside_projected_decision():
    result = saved.connector_unknown(evaluation_id="e", reason="CONNECTOR_INPUT_INTEGRITY")
    assert result["projected_decision"] is None
    assert result["metric_value"] == "UNKNOWN"


def test_public_result_does_not_alias_decision():
    decision = {"binding": {"x": []}, "metric_value": "PASS", "reason_code": "NO_S4_VIOLATION",
        "offending_provenance_ids": (), "measurement_validity": "VALID"}
    result = saved.wrap_decision(evaluation_id="e", decision=decision)
    result["binding"]["x"].append(1)
    assert decision["binding"]["x"] == []


def test_random_old_binding_id_is_preserved_while_new_binding_is_explicit():
    result = saved.bind_saved_disclosures(
        old_envelope={"binding": {}, "accepted_plan_sha256": "x", "items": [
            {"provenance_id": "pv-random", "origin": "SELECTED_DISCLOSURE",
             "selected_id": "d000", "explicit_ref": None, "binding_id": "bind-random"}]},
        accepted_plan={"disclose_ids": ["d000"]},
        custodian_freeze={"bindings": [{"disclose_id": "d000", "binding_id": "d000"}]})
    assert result == [{"opaque_provenance_id": "pv-random", "old_binding_id": "bind-random",
                       "disclose_id": "d000", "new_binding_id": "d000"}]


def test_random_old_binding_cannot_replace_selected_id():
    with pytest.raises(saved.SavedBindingError):
        saved.bind_saved_disclosures(
            old_envelope={"binding": {}, "accepted_plan_sha256": "x", "items": [
                {"provenance_id": "pv-random", "origin": "SELECTED_DISCLOSURE",
                 "selected_id": "bind-random", "explicit_ref": None, "binding_id": "bind-random"}]},
            accepted_plan={"disclose_ids": ["d000"]},
            custodian_freeze={"bindings": [{"disclose_id": "d000", "binding_id": "d000"}]})


def _public_full_input(root: Path):
    source_dir=root/"sources"; source_dir.mkdir(parents=True)
    entries=[]
    def add(source_id,name,value):
        raw=value if type(value) is bytes else saved.canonical_wire(value)
        (source_dir/source_id).write_bytes(raw)
        entries.append({"source_id":source_id,"original_name":name,"sha256":hashlib.sha256(raw).hexdigest(),"size":len(raw)})
        return source_id,raw
    fixtures=list(q.prepare()[0]); fixture_rows=[{"case_id":f.case.case_id,"source":f.source,"bindings":f.bindings,
        "catalog":json.loads(q.wire(asdict(f.catalog))),"projection_sha256":q.digest(f.source_bytes)} for f in fixtures]
    add("fixture_input_snapshot","fixture-inputs.json",fixture_rows)
    old_packet=[]; old_mechanical=[]; old_mapping=[]; input_rows=[]; seal={}
    accepted_index=0
    for ci,fixture in enumerate(fixtures):
      for si,seed in enumerate((4242027,4242028,4242029)):
       for condition in ("baseline","candidate"):
        ordinal=ci*6+si*2+(condition=="candidate"); old_id=f"old-{ordinal:03d}"; eid=f"eval-{ordinal:03d}"
        accepted=ordinal>=13
        surface={"kind":"TEXT","text":"hello","comment":None,"claims":[]}; view={"kind":"TEXT","text":"hello","claims":[]}
        disclose=[]
        if accepted and condition=="candidate" and fixture.catalog.disclose_ids and accepted_index==0:
            disclose=[fixture.catalog.disclose_ids[0]]
        if accepted: accepted_index+=1
        plan={"kind":"CHAT_PLAN","fact_ids":[],"disclose_ids":disclose,"claim_id":None}
        bindings=[]; items=[]
        for di,did in enumerate(disclose):
            edge=fixture.bindings[did]; val=q.selected(fixture,did)["value"]; bid=f"bind-{ordinal}-{di}"
            atom=lambda x:{"tag":"ABSENT","value":None} if x is None else {"tag":"VALUE","value":x}
            bindings.append({"binding_id":bid,"selected_id":did,"source_kind":"ABILITY_RESULT","canonical_identity":None,
                "actor_id":edge["actor"],"visibility":"PUBLIC","authority":edge["authority"],
                "target":atom(val.get("target_player_id")),"result":atom(val.get("result_id"))})
            items.append({"provenance_id":f"pv-{ordinal}-{di}","origin":"SELECTED_DISCLOSURE","selected_id":did,"explicit_ref":None,"binding_id":bid})
        raw_surface=common.canonical_wire(view); surface_sha=hashlib.sha256(raw_surface).hexdigest()
        binding={"rubric_version":common.RUBRIC,"blind_id":old_id,"surface_sha256":surface_sha,
            "row_sha256":common.canonical_sha({"rubric_version":common.RUBRIC,"blind_id":old_id,"surface_sha256":surface_sha}),
            "catalog_sha256":common.canonical_sha(sorted(bindings,key=lambda x:x["binding_id"])),
            "canonical_set_sha256":common.canonical_sha([])}
        payload={"rubric_version":common.RUBRIC,"blind_id":old_id,"surface_sha256":surface_sha,"row_sha256":binding["row_sha256"],
            "catalog_sha256":binding["catalog_sha256"],"canonical_set_sha256":binding["canonical_set_sha256"],
            "accepted_plan_sha256":common.canonical_sha(plan),"items":items}
        binding["selection_envelope_sha256"]=common.canonical_sha(payload)
        envelope={"binding":binding,"accepted_plan_sha256":payload["accepted_plan_sha256"],"items":items}
        records=list(common.project_common_provenance(binding=binding,accepted_surface=raw_surface,accepted_plan=plan,
            envelope=envelope,bindings=tuple(bindings),canonical_records=()))
        execution={"run_status":"COMPLETE","structural_status":"ACCEPTED"} if accepted else {"run_status":"NOT_RUN","structural_status":"NOT_EVALUATED"}
        audit={"sealed_audit":"COMPLETE","public_surface":"TEXT" if accepted else "UNKNOWN"}; conversion={"status":"COMPLETE"}
        old_packet.append({"blind_id":old_id,"execution":execution,"sealed_audit":audit,"conversion_status":conversion,"surface":surface})
        old_mechanical.append({"blind_id":old_id,"execution":execution,"sealed_audit":audit,"conversion_status":conversion,
            "binding":binding,"envelope":envelope,"mechanical":records,"plan_view":plan})
        proof=None; source_final="0"*64
        if condition=="baseline":
            text=json.dumps(BASE_FINAL,ensure_ascii=False,separators=(",",":")); wrapper={"final_content":text,"final_output_sha256":hashlib.sha256(text.encode()).hexdigest()}
            sid=f"baseline_source_{ordinal:03d}"; _,raw=add(sid,f"baseline/{seed}/{fixture.case.case_id}.final.json",wrapper); source_final=hashlib.sha256(raw).hexdigest()
            if accepted: proof={"kind":"BASELINE_V1","saved_row_source_id":"old_mechanical_snapshot","wrapper_source_id":sid,
                "outcome_source_id":None,"response_source_id":None,"container_seal_source_id":None,"terminal_stage":None,"ordinal":None,
                "locator":{"source_id":sid,"snapshot_file_sha256":source_final,"member_name":f"baseline/{seed}/{fixture.case.case_id}.final.json","json_pointer":"/final_content","encoding":"JSON_STRING_UTF8"}}
        else:
            saved_row={"case_id":fixture.case.case_id,"seed":seed,"terminal_stage":"chat_plan","final":CAND_FINAL}
            rsid=f"saved_row_source_{ordinal:03d}"; add(rsid,f"actual/row-{ordinal}.json",saved_row)
            if accepted:
                outname=f"actual/{fixture.case.case_id}-{seed}-chat_plan-1-outcome.json"; respname=outname.replace("-outcome.json","-response.json")
                osid=f"outcome_source_{ordinal:03d}"; psid=f"response_source_{ordinal:03d}"
                _,oraw=add(osid,outname,{"status":"ACCEPTED","case_id":fixture.case.case_id,"seed":seed,"stage":"chat_plan","ordinal":1})
                _,praw=add(psid,respname,json.loads(candidate_response()))
                seal[outname]=hashlib.sha256(oraw).hexdigest(); seal[respname]=hashlib.sha256(praw).hexdigest()
                proof={"kind":"CANDIDATE_V2","saved_row_source_id":rsid,"wrapper_source_id":None,"outcome_source_id":osid,
                    "response_source_id":psid,"container_seal_source_id":"container_seal_snapshot","terminal_stage":"chat_plan","ordinal":1,
                    "locator":{"source_id":psid,"snapshot_file_sha256":hashlib.sha256(praw).hexdigest(),"member_name":respname,
                        "json_pointer":"/choices/0/message/content","encoding":"JSON_STRING_UTF8"}}
        old_mapping.append({"blind_id":old_id,"condition":condition,"case_id":fixture.case.case_id,"seed":seed,"source_final_sha256":source_final})
        input_rows.append({"evaluation_id":eid,"old_blind_id":old_id,"condition":condition,"case_id":fixture.case.case_id,"seed":seed,
            "pair_key_sha256":common.canonical_sha({"case_id":fixture.case.case_id,"seed":seed}),"execution":execution,"audit":audit,
            "conversion":conversion,"old_binding":binding,"old_envelope":envelope,"base_records":records,"accepted_surface":surface,
            "accepted_plan":plan,"fixture_source_sha256":common.canonical_sha(fixture.source),
            "fixture_bindings_sha256":common.canonical_sha(fixture.bindings),
            "fixture_catalog_sha256":common.canonical_sha(json.loads(q.wire(asdict(fixture.catalog)))),
            "fixture_input_source_id":"fixture_input_snapshot","accepted_final_proof":proof})
    add("container_seal_snapshot","seal.json",seal)
    old_docs=(("old_packet_snapshot","old/blind-packet.json",{"rows":old_packet}),
              ("old_freeze_snapshot","old/freeze.json",{}),("old_mechanical_snapshot","old/custodian-mechanical.json",{"rows":old_mechanical}),
              ("old_mapping_snapshot","old/secret-mapping.json",{"rows":old_mapping}))
    old_hash={}
    for sid,name,obj in old_docs:
        _,raw=add(sid,name,obj); old_hash[sid]=hashlib.sha256(raw).hexdigest()
    add("old_source_manifest_snapshot","old/source-hashes.json",{})
    files=(("connector_code_snapshot","tests/fixtures/phase6_s4_saved_binding.py",Path(saved.__file__)),
           ("projected_code_snapshot","tests/fixtures/phase6_s4_projected_ability_provenance.py",Path(saved.__file__).with_name("phase6_s4_projected_ability_provenance.py")),
           ("probe_code_snapshot","scripts/phase6_quality_probe_v2.py",Path(q.__file__)))
    code_hash={}
    for sid,name,path in files:
        _,raw=add(sid,name,path.read_bytes()); code_hash[name]=hashlib.sha256(raw).hexdigest()
    entries.sort(key=lambda x:x["original_name"]); manifest={"contract":saved.CONTRACT,"files":entries}
    manifest["root_snapshot_sha256"]=common.canonical_sha(manifest); manifest["manifest_sha256"]=common.canonical_sha(manifest)
    preflight={"contract":saved.CONTRACT,"permission_decision_sha256":"1"*64,"base_design_sha256":"2"*64,"amendment_sha256":"3"*64,
        "projected_helper_sha256":code_hash["tests/fixtures/phase6_s4_projected_ability_provenance.py"],
        "connector_source_sha256":code_hash["tests/fixtures/phase6_s4_saved_binding.py"],"probe_source_sha256":code_hash["scripts/phase6_quality_probe_v2.py"],
        "old_packet_sha256":old_hash["old_packet_snapshot"],"old_freeze_sha256":old_hash["old_freeze_snapshot"],
        "old_mechanical_sha256":old_hash["old_mechanical_snapshot"],"old_mapping_sha256":old_hash["old_mapping_snapshot"],
        "source_manifest_sha256":manifest["manifest_sha256"],"rows":192,"pairs":96,"accepted_rows":179,"unobserved_rows":13}
    preflight["preflight_sha256"]=common.canonical_sha(preflight)
    return {"contract":saved.CONTRACT,"preflight":preflight,"source_manifest":manifest,"rows":input_rows}


def test_public_full_ready_prepare_seal_finalize():
    test_root=Path(".tmp")/("t556-public-"+secrets.token_hex(8)); test_root.mkdir(parents=True)
    source=_public_full_input(test_root)
    input_path=test_root/"input.json"; input_path.write_bytes(saved.canonical_wire(source)); prepared_path=test_root/"prepared.json"
    assert saved.main(["prepare-roots","--root",str(test_root),"--input",str(input_path),"--output",str(prepared_path),
        "--expected-preflight-sha256",source["preflight"]["preflight_sha256"]]) == 0
    prepared=saved.strict_parse(prepared_path.read_bytes())
    assert sum(x["status"]=="READY" for x in prepared["candidate_manifest"]["rows"]) == 167
    first_source=source["source_manifest"]["files"][0]["source_id"]; source_path=test_root/"sources"/first_source
    original=source_path.read_bytes(); source_path.write_bytes(original+b"x")
    with pytest.raises(saved.SavedBindingError):
        saved.prepare_saved_roots(root=test_root,source=source,expected_preflight_sha256=source["preflight"]["preflight_sha256"])
    source_path.write_bytes(original)
    ready=[x for x in prepared["candidate_manifest"]["rows"] if x["status"]=="READY"]
    pin_value={"contract":saved.CONTRACT,"task":"T556","preflight_sha256":source["preflight"]["preflight_sha256"],
        "candidate_manifest_sha256":prepared["candidate_manifest"]["manifest_sha256"],
        "rows":[{"evaluation_id":x["evaluation_id"],"expected_freeze_sha256":x["custodian_freeze_sha256"]} for x in ready],
        "ready_count":167,"unavailable_count":12,"unobserved_count":13}
    pin_value["pin_sha256"]=saved.canonical_sha(pin_value)
    source_path.write_bytes(original+b"x")
    with pytest.raises(saved.SavedBindingError):
        saved.seal_saved_packet(root=test_root,prepared=prepared,main_pin=pin_value,
            expected_pin_sha256=pin_value["pin_sha256"],expected_preflight_sha256=source["preflight"]["preflight_sha256"])
    source_path.write_bytes(original)
    seal_input=test_root/"seal-input.json"; sealed_path=test_root/"sealed.json"
    seal_input.write_bytes(saved.canonical_wire({"prepared":prepared,"main_pin":pin_value}))
    assert saved.main(["seal-packet","--root",str(test_root),"--input",str(seal_input),"--output",str(sealed_path),
        "--expected-preflight-sha256",source["preflight"]["preflight_sha256"],"--expected-pin-sha256",pin_value["pin_sha256"]]) == 0
    sealed=saved.strict_parse(sealed_path.read_bytes())
    assert len(sealed["packet"]["rows"]) == 192
    notes=[]
    for row in prepared["candidate_manifest"]["rows"]:
        notes.append({"evaluation_id":row["evaluation_id"],"status":"ANNOTATED" if row["status"]=="READY" else "NOT_REQUIRED" if row["status"]=="MEASUREMENT_NOT_OBSERVED" else "CONNECTOR_SKIPPED",
            "assertion":"NONE" if row["status"]=="READY" else None,"cited_provenance_ids":[],"associations":[]})
    finalize_input=test_root/"finalize-input.json"; final_path=test_root/"final.json"
    finalize_input.write_bytes(saved.canonical_wire({"prepared":prepared,"sealed":sealed,"main_pin":pin_value,"annotations":notes}))
    assert saved.main(["finalize","--root",str(test_root),"--input",str(finalize_input),"--output",str(final_path),
        "--expected-preflight-sha256",source["preflight"]["preflight_sha256"],"--expected-pin-sha256",pin_value["pin_sha256"]]) == 0
    final=saved.strict_parse(final_path.read_bytes())
    assert sum(final["safe"]["counts"].values()) == 192
    assert final["safe"]["counts"]["MEASUREMENT_NOT_OBSERVED"] == 13
    tampered=deepcopy(prepared); next(x for x in tampered["host_rows"] if x["status"]=="READY")["accepted_surface"]["text"]="tampered"
    with pytest.raises(saved.SavedBindingError):
        saved.seal_saved_packet(root=test_root,prepared=tampered,main_pin=pin_value,expected_pin_sha256=pin_value["pin_sha256"],
            expected_preflight_sha256=source["preflight"]["preflight_sha256"])
    bad_packet=deepcopy(sealed); target=next(x for x in bad_packet["packet"]["rows"] if x["evaluation_status"]=="READY")
    target["accepted_surface"]["text"]="tampered"; bad_packet["packet_sha256"]=saved.canonical_sha(bad_packet["packet"])
    with pytest.raises(saved.SavedBindingError):
        saved.finalize_saved_results(root=test_root,prepared=prepared,sealed=bad_packet,main_pin=pin_value,
            expected_pin_sha256=pin_value["pin_sha256"],expected_preflight_sha256=source["preflight"]["preflight_sha256"],annotations=notes)
    bad_notes=deepcopy(notes); note=next(x for x in bad_notes if x["status"]=="ANNOTATED")
    note["assertion"]="EXPLICIT_AUTHORITY_ASSERTION"; note["cited_provenance_ids"]=["missing-ref"]
    with pytest.raises(saved.SavedBindingError):
        saved.finalize_saved_results(root=test_root,prepared=prepared,sealed=sealed,main_pin=pin_value,
            expected_pin_sha256=pin_value["pin_sha256"],expected_preflight_sha256=source["preflight"]["preflight_sha256"],annotations=bad_notes)
    source_path.write_bytes(original+b"x")
    with pytest.raises(saved.SavedBindingError):
        saved.finalize_saved_results(root=test_root,prepared=prepared,sealed=sealed,main_pin=pin_value,
            expected_pin_sha256=pin_value["pin_sha256"],expected_preflight_sha256=source["preflight"]["preflight_sha256"],annotations=notes)
    source_path.write_bytes(original)
    with pytest.raises(FileExistsError):
        saved.main(["finalize","--root",str(test_root),"--input",str(finalize_input),"--output",str(final_path),
            "--expected-preflight-sha256",source["preflight"]["preflight_sha256"],"--expected-pin-sha256",pin_value["pin_sha256"]])


def test_cli_bad_path_has_fixed_payload_free_error():
    secret="PRIVATE_SECRET_PATH_TOKEN"
    result=subprocess.run([sys.executable,"tests/fixtures/phase6_s4_saved_binding.py","prepare-roots",
        "--root",secret,"--input",secret,"--output",secret,"--expected-preflight-sha256","0"*64],
        text=True,capture_output=True,check=False)
    assert result.returncode == 1
    assert result.stdout.strip() == "SAVED_BINDING_INPUT_INTEGRITY"
    assert result.stderr == "" and secret not in result.stdout
