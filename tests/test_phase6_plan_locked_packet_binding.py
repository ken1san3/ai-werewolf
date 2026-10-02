from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import phase6_quality_probe_v2 as q
from scripts import phase6_plan_locked_packet_binding as b
from tests.fixtures import phase6_plan_locked_native_custody as c
from tests.fixtures import phase6_plan_locked_rubric as rubric
from tests.test_phase6_plan_locked_rubric import bundle as rubric_bundle
from tests.test_phase6_resolved_subject_probe import test_owned_runtime_full96_native_fixture_lifecycle as _run_t567_fixture
from scripts import phase6_resolved_subject_probe as probe

H=[str(i)*64 for i in range(1,9)]


def _binding(n=1):
    return {"parsed_result_sha256":H[n],"member_sha256":H[n+1],"locator_sha256":H[n+2]}


def native_bundle():
    fixtures=q.prepare()[0]; rows=[]; target_index=0
    for fixture in fixtures:
        for seed in (4242027,4242028,4242029):
            chat=fixture.stage=="chat_plan"
            target=chat and target_index<77
            status="TARGET" if target else "PLAN_NOT_OBSERVED" if chat else "NOT_MESSAGE_DOMAIN"
            if chat: target_index+=1
            plan={"act":"NONE","subject_player_id":None,"reply_to":None} if target else None
            control_observed=target and len([x for x in rows if x["population_status"]=="TARGET"])<69
            candidate_observed=target and len([x for x in rows if x["population_status"]=="TARGET"])%3!=0
            candidate={"status":"ACCEPTED" if candidate_observed else "MISSING_RESPONSE","candidate_text":"candidate text" if candidate_observed else None,
                       "response_content":rubric.wire({"message":"candidate text"}).decode() if candidate_observed else None,"binding":_binding(3) if candidate_observed else None} if target else None
            if candidate_observed: candidate["binding"]["parsed_result_sha256"]=rubric.digest({"message":"candidate text"})
            row={"row_id":f"row-{fixture.case.case_id}-{seed}","case_id":fixture.case.case_id,"seed":seed,"population_status":status,"plan":plan,
                 "control_status":"ACCEPTED" if control_observed else "MISSING_RESPONSE","control_text":"control text" if control_observed else None,
                 "control_binding":_binding(0) if control_observed else None,"candidate":candidate}
            if control_observed: row["control_binding"]["parsed_result_sha256"]=rubric.digest({"message":"control text"})
            row["row_sha256"]=rubric.digest(row); rows.append(row)
    roots=b.source_roots(rows)
    source={"contract":b.INPUT_CONTRACT,"source_sha256":roots[0],"custody_sha256":roots[1],"result_sha256":roots[2],"response_seal_sha256":roots[3],"measurement_seal_sha256":roots[4],"rows":rows}
    return source,fixtures


def build(source=None,fixtures=None,**changes):
    if source is None: source,fixtures=native_bundle()
    roots=b.source_roots(source["rows"])
    kwargs={"expected_source_sha256":roots[0],"expected_custody_sha256":roots[1],"expected_result_sha256":roots[2],"expected_response_seal_sha256":roots[3],"expected_measurement_seal_sha256":roots[4],"rubric_sha256":H[6],"population_sha256":H[7],"packet_binding_sha256":"a"*64,"entropy":bytes(range(256))*16}
    kwargs.update(changes); return b.build_packet(source,fixtures,**kwargs)


def test_native_96_lossless_packet_and_private_mapping():
    result=build(); packet=result["packet"]
    counts={k:sum(x["population_status"]==k for x in packet["rows"]) for k in ("TARGET","PLAN_NOT_OBSERVED","NOT_MESSAGE_DOMAIN")}
    assert counts=={"TARGET":77,"PLAN_NOT_OBSERVED":7,"NOT_MESSAGE_DOMAIN":12}
    assert sum(x["question_opportunity"] for x in packet["rows"])==54
    assert sum(s["observation"] is not None and s["observation"]["text_observation"]=="OBSERVED" for row in packet["rows"] for s in row["side_observations"])>69
    assert len(result["private_mapping"]["rows"])==96
    assert all("case_id" not in row and "seed" not in row for row in packet["rows"])


@pytest.mark.parametrize("field",["expected_source_sha256","expected_custody_sha256","expected_result_sha256","expected_response_seal_sha256","expected_measurement_seal_sha256"])
def test_every_external_root_is_required(field):
    with pytest.raises(b.PacketBindingError): build(**{field:"f"*64})


def test_candidate_response_and_row_binding_tamper_rejected():
    source,fixtures=native_bundle(); row=next(x for x in source["rows"] if x["candidate"] and x["candidate"]["status"]=="ACCEPTED")
    row["candidate"]["candidate_text"]="different"; row["row_sha256"]=rubric.digest({k:v for k,v in row.items() if k!="row_sha256"})
    with pytest.raises(b.PacketBindingError): build(source,fixtures)
    source,fixtures=native_bundle(); source["rows"][0]["row_id"]=source["rows"][1]["row_id"]
    source["rows"][0]["row_sha256"]=rubric.digest({k:v for k,v in source["rows"][0].items() if k!="row_sha256"})
    with pytest.raises(b.PacketBindingError): build(source,fixtures)


def test_status_text_and_shape_are_closed():
    source,fixtures=native_bundle(); row=source["rows"][0]
    row["control_status"]="MISSING_RESPONSE"; row["row_sha256"]=rubric.digest({k:v for k,v in row.items() if k!="row_sha256"})
    with pytest.raises(b.PacketBindingError): build(source,fixtures)
    source,fixtures=native_bundle(); source["rows"][0]["extra"]=None
    with pytest.raises(b.PacketBindingError): build(source,fixtures)


def test_duplicate_nonfinite_and_create_only():
    with pytest.raises(rubric.RubricInputError): rubric.parse('{"x":1,"x":2}')
    with pytest.raises(rubric.RubricInputError): rubric.parse('{"x":NaN}')
    target=Path(".tmp")/f"t572-{uuid4().hex}.json"
    try:
        b.write_once(target,{"ok":True})
        with pytest.raises(b.PacketBindingError): b.write_once(target,{"ok":False})
    finally: target.unlink(missing_ok=True)


def _physical(root,source):
    root.mkdir(parents=True); prepared=[]
    for role in ("input","preflight","source_manifest","main_pin","old_t550_seal"):
        name=f"{role}.json"; (root/name).write_bytes(rubric.wire({"role":role})); prepared.append(c.physical_ref(root,role,name))
    roles=[]
    for role in c.ROLES:
        name=f"roles/{role}.json"; (root/"roles").mkdir(exist_ok=True); (root/name).write_bytes(rubric.wire({"role":role})); roles.append(c.physical_ref(root,role,name))
    results_name="measurement/results.json"; (root/"measurement").mkdir(); (root/results_name).write_bytes(rubric.wire({"rows":96})); results=c.physical_ref(root,"results_file",results_name)
    row_bindings=[]; controls=[]; result_bindings=[]; responses=[]
    for i,row in enumerate(source["rows"]):
        row_bindings.append({"ordinal":i,"row_id":row["row_id"],"case_id":row["case_id"],"seed":row["seed"],"population_status":row["population_status"],"prepared_row_sha256":row["row_sha256"],"native_member_sha256":H[1],"fixture_binding_sha256":H[2],"result_row_sha256":H[3]})
        if row["control_text"]:
            loc={"condition":"CONTROL","row_id":row["row_id"],"case_id":row["case_id"],"seed":row["seed"],"ordinal":1,"source_role":"saved_rows","member_name":f"native/{i}.json","member_sha256":H[1],"json_pointer":"/final/message","encoding":"JSON_STRING_UTF8"}; loc["locator_sha256"]=rubric.digest(loc); controls.append(loc)
        refs=[]
        if row["candidate"] and row["candidate"]["status"]=="ACCEPTED":
            name=f"measurement/response-{row['row_id']}-1.json"; (root/name).write_bytes(row["candidate"]["response_content"].encode()); ref=c.physical_ref(root,"measurement_response",name); refs=[ref]
            loc={"condition":"CANDIDATE","row_id":row["row_id"],"case_id":row["case_id"],"seed":row["seed"],"ordinal":1,"source_role":"measurement_response","member_name":name,"member_sha256":ref["sha256"],"json_pointer":"/choices/0/message/content","encoding":"JSON_STRING_UTF8"}; loc["locator_sha256"]=rubric.digest(loc)
            responses.append({"row_ordinal":i,"row_id":row["row_id"],"attempt_ordinal":1,"file":ref,"locator":loc})
        result_bindings.append({"ordinal":i,"row_id":row["row_id"],"result_row_sha256":H[3],"attempt_member_refs":refs})
    seals=[]
    for role in ("measurement_seal","runner_safe","operator_safe","operator_seal"):
        name=f"measurement/{role}.json"; (root/name).write_bytes(rubric.wire({"role":role})); seals.append(c.physical_ref(root,role,name))
    measure=[{"relative_name":results["relative_name"],"member_sha256":results["sha256"]}]
    measure += [{"relative_name":x["file"]["relative_name"],"member_sha256":x["file"]["sha256"]} for x in responses]
    measure=sorted(measure,key=lambda x:x["relative_name"].encode())
    operator=sorted(measure+[{"relative_name":x["relative_name"],"member_sha256":x["sha256"]} for x in seals[:3]],key=lambda x:x["relative_name"].encode())
    return c.build_native_roots(root,prepared_files=prepared,source_roles=roles,row_bindings=row_bindings,control_locators=controls,results_file=results,result_bindings=result_bindings,response_bindings=responses,seal_files=seals,measurement_coverage=measure,operator_coverage=operator)


def _authority(roots):
    keys=("prepared_locator_file_sha256","input_file_sha256","preflight_file_sha256","source_manifest_file_sha256","main_pin_file_sha256","old_t550_seal_file_sha256","measurement_results_file_sha256","measurement_seal_file_sha256","runner_safe_file_sha256","operator_safe_file_sha256","operator_seal_file_sha256","source_roles_root_sha256","response_members_root_sha256","code_bundle_sha256")
    value={"contract":c.CONTRACT,**{k:H[1] for k in keys},"expected_population_root_sha256":roots["roots"]["roots_sha256"]}; value["pin_sha256"]=rubric.digest(value); return value


def test_physical_96_root_to_packet_and_tamper_rejection(monkeypatch):
    source,fixtures=native_bundle(); root=Path(".tmp")/f"t572-native-{uuid4().hex}"
    try:
        native=_physical(root,source)
        control_by={x["row_id"]:x for x in native["payloads"]["sources"]["control_locators"]}; response_by={x["row_id"]:x for x in native["payloads"]["responses"]["members"]}
        for row in source["rows"]:
            if row["row_id"] in control_by:
                loc=control_by[row["row_id"]]; row["control_binding"].update(member_sha256=loc["member_sha256"],locator_sha256=loc["locator_sha256"])
            if row["row_id"] in response_by:
                item=response_by[row["row_id"]]; row["candidate"]["binding"].update(member_sha256=item["file"]["sha256"],locator_sha256=item["locator"]["locator_sha256"])
            row["row_sha256"]=rubric.digest({k:v for k,v in row.items() if k!="row_sha256"})
        locator_name="locator.json"; (root/locator_name).write_bytes(b"locator"); locator=c.physical_ref(root,"prepared_locator",locator_name)
        code_paths=[]
        for i in range(9):
            name=f"code/{i}.txt"; (root/"code").mkdir(exist_ok=True); (root/name).write_bytes(f"code-{i}".encode()); code_paths.append(name)
        code=c.build_code_bundle(root,code_paths)
        evidence=b.authority_evidence_from_custody(locator,native,code); authority={"contract":c.CONTRACT,**evidence}; authority["pin_sha256"]=rubric.digest(authority)
        hydrated={"normalized_source":{"contract":b.INPUT_CONTRACT,"rows":source["rows"]},"derived":{"row_bindings":native["payloads"]["sources"]["rows"],"control_locators":native["payloads"]["sources"]["control_locators"],"result_bindings":native["payloads"]["results"]["rows"],"response_bindings":native["payloads"]["responses"]["members"]}}
        monkeypatch.setattr(c,"hydrate_physical_run",lambda *a,**k:deepcopy(hydrated))
        common=dict(prepared_root=root,measurement_root=root,code_root=root,native_custody=native,prepared_locator_ref=locator,code_bundle=code,expected_native_population_roots_sha256=native["roots"]["roots_sha256"],expected_input_sha256=H[1],expected_preflight_sha256=H[1],expected_manifest_sha256=H[1],expected_main_pin_sha256=H[1],expected_code_sha256=H[1],expected_results_sha256=H[1],expected_measurement_seal_sha256=H[1],fixtures=fixtures,entropy=bytes(range(256))*16,rubric_sha256=H[6],population_sha256=H[7])
        result=b.build_packet_from_hydrated_run(authority_pin=authority,expected_authority_pin_sha256=authority["pin_sha256"],**common)
        assert len(result["packet"]["rows"])==96
        bad=deepcopy(hydrated); bad["normalized_source"]["rows"][0]["control_text"]="changed"; bad["normalized_source"]["rows"][0]["row_sha256"]=rubric.digest({k:v for k,v in bad["normalized_source"]["rows"][0].items() if k!="row_sha256"})
        monkeypatch.setattr(c,"hydrate_physical_run",lambda *a,**k:deepcopy(bad))
        with pytest.raises(b.PacketBindingError): b.build_packet_from_hydrated_run(authority_pin=authority,expected_authority_pin_sha256=authority["pin_sha256"],**common)
        monkeypatch.setattr(c,"hydrate_physical_run",lambda *a,**k:deepcopy(hydrated))
        for field in evidence:
            badpin=deepcopy(authority); badpin[field]="f"*64; badpin["pin_sha256"]=rubric.digest({k:v for k,v in badpin.items() if k!="pin_sha256"})
            with pytest.raises(b.PacketBindingError): b.build_packet_from_hydrated_run(authority_pin=badpin,expected_authority_pin_sha256=badpin["pin_sha256"],**common)
        victim=next(root.rglob("runner_safe.json")); victim.write_text("tampered")
        with pytest.raises(b.PacketBindingError): b.validate_physical_custody(root,native,native["roots"]["roots_sha256"])
    finally:
        import shutil; shutil.rmtree(root,ignore_errors=True)


def test_v2_freeze_annotation_unblind_and_external_root_tamper():
    packet,mapping,_,annotation=rubric_bundle()
    kwargs={"expected_source_manifest_sha256":H[1],"expected_native_authority_pin_sha256":H[2],"expected_native_population_roots_sha256":H[3],"expected_population_sha256":packet["population_sha256"],"expected_mapping_sha256":rubric.digest(mapping),"expected_rubric_sha256":packet["rubric_sha256"],"expected_packet_sha256":rubric.digest(packet),"expected_packet_binding_sha256":packet["packet_binding_sha256"]}
    freeze=b.make_native_packet_freeze_v2(packet,mapping,source_manifest_sha256=H[1],native_authority_pin_sha256=H[2],native_population_roots_sha256=H[3],**{k:v for k,v in kwargs.items() if k not in ("expected_source_manifest_sha256","expected_native_authority_pin_sha256","expected_native_population_roots_sha256")})
    annotation["packet_freeze_sha256"]=freeze["freeze_sha256"]
    proof={"expected_packet_freeze_sha256":freeze["freeze_sha256"],**kwargs}
    identity="fresh-reviewer"; identity_sha=rubric.digest(identity)
    af=b.freeze_annotation_v2(annotation,packet,mapping,freeze,evaluator_identity=identity,expected_evaluator_identity_sha256=identity_sha,**proof)
    result=b.unblind_v2(annotation,packet,mapping,freeze,af,evaluator_identity=identity,expected_evaluator_identity_sha256=identity_sha,expected_annotation_freeze_sha256=af["freeze_sha256"],**proof)
    assert result["rows"]==96 and result["control_observed_rows"]==69 and result["question_denominator"]==54
    bad=deepcopy(freeze); bad["native_population_roots_sha256"]="f"*64; bad["freeze_sha256"]=rubric.digest({k:v for k,v in bad.items() if k!="freeze_sha256"})
    with pytest.raises(b.PacketBindingError): b.freeze_annotation_v2(annotation,packet,mapping,bad,evaluator_identity=identity,expected_evaluator_identity_sha256=identity_sha,**proof)


def test_actual_t567_public_physical_hydration(monkeypatch):
    base=Path(".tmp")/f"t572-hydrate-{uuid4().hex}"; base.mkdir(parents=True)
    try:
        _run_t567_fixture(monkeypatch,base)
        run=base/"T568-public-synthetic"; bundle=probe.parse((run/"input.json").read_bytes()); _materialize_native_roles(run,bundle)
        input_raw=(run/"input.json").read_bytes(); bundle=probe.parse(input_raw)
        measurement=run/"measurement"; result_raw=(measurement/"results.json").read_bytes()
        seal={p.relative_to(measurement).as_posix():probe.digest(p.read_bytes()) for p in measurement.rglob("*") if p.is_file() and p.name!="seal.json"}; (measurement/"seal.json").write_bytes(probe.wire(seal)); seal_raw=(measurement/"seal.json").read_bytes()
        hydrated=c.hydrate_physical_run(run,measurement,source_root=run/"source",expected_input_sha256=probe.digest(input_raw),expected_preflight_sha256=bundle["preflight"]["preflight_sha256"],expected_manifest_sha256=bundle["source_manifest"]["manifest_sha256"],expected_main_pin_sha256=bundle["main_pin"]["pin_sha256"],expected_code_sha256=bundle["preflight"]["code_sha256"],expected_results_sha256=probe.digest(result_raw),expected_measurement_seal_sha256=probe.digest(seal_raw))
        assert len(hydrated["normalized_source"]["rows"])==96
        assert sum(x["population_status"]=="TARGET" for x in hydrated["normalized_source"]["rows"])==77
        assert len(hydrated["derived"]["control_locators"])==69
        original=(measurement/"results.json").read_bytes(); (measurement/"results.json").write_bytes(original+b" ")
        with pytest.raises(c.NativeCustodyError): c.hydrate_physical_run(run,measurement,source_root=run/"source",expected_input_sha256=probe.digest(input_raw),expected_preflight_sha256=bundle["preflight"]["preflight_sha256"],expected_manifest_sha256=bundle["source_manifest"]["manifest_sha256"],expected_main_pin_sha256=bundle["main_pin"]["pin_sha256"],expected_code_sha256=bundle["preflight"]["code_sha256"],expected_results_sha256=probe.digest(result_raw),expected_measurement_seal_sha256=probe.digest(seal_raw))
    finally:
        import shutil; shutil.rmtree(base,ignore_errors=True)


def _materialize_native_roles(run,bundle):
    source=run/"source"; manifest=bundle["source_manifest"]; entries={x["role"]:x for x in manifest["files"]}; fixtures=q.prepare()[0]; fixture_index={f.case.case_id:i for i,f in enumerate(fixtures)}
    native_fixtures=probe.parse(q.wire([dict(case_id=f.case.case_id,source=f.source,projection_sha256=q.digest(f.source_bytes),catalog=probe.asdict(f.catalog),bindings=f.bindings) for f in fixtures]))
    members={}; saved=[]; requests=[]; plans=[]; outcomes=[]; fixture_records=[]
    for row in bundle["rows"]:
        index=fixture_index[row["case_id"]]; fixture=fixtures[index]; projection=q.digest(fixture.source_bytes)
        value={"case_id":row["case_id"],"elapsed":1.0,"final":None if row["control_text"] is None else {"message":row["control_text"]},"plan":row["plan"],"projection_sha256":projection,"sample_exhausted":False,"seed":row["seed"],"silence":False,"terminal_stage":"message"}
        if row["status"]=="NOT_MESSAGE_DOMAIN": value["final"]={"decision":"DECLARE"}
        name=f"actual/row-{row['case_id']}-{row['seed']}.json"; sha=probe.digest(value); members[name]=sha
        saved.append({"run_row":row,"native":{"original_name":name,"member_sha256":sha,"value":value}})
        fixture_records.append({"row_id":row["row_id"],"case_id":row["case_id"],"seed":row["seed"],"fixture_index":index,"projection_sha256":projection,"source_sha256":probe.digest(native_fixtures[index]["source"]),"bindings_sha256":probe.digest(native_fixtures[index]["bindings"]),"catalog_sha256":probe.digest(native_fixtures[index]["catalog"])})
        if row["status"] in ("READY","INPUT_NOT_CHANGED"):
            plans.append({"row_id":row["row_id"],"plan":row["plan"],"plan_sha256":probe.digest(row["plan"]),"original_name":name,"member_sha256":sha})
            for ordinal,field in ((1,"control_ordinal1"),(2,"saved_ordinal2")):
                if row[field] is None: continue
                reqname=f"actual/{row['row_id']}-message-{ordinal}-request.json"; reqsha=probe.digest(row[field].encode()); members[reqname]=reqsha
                requests.append({"row_id":row["row_id"],"ordinal":ordinal,"original_name":reqname,"member_sha256":reqsha,"wire":row[field]})
                outname=f"actual/{row['row_id']}-message-{ordinal}-outcome.json"; out={"status":"ACCEPTED"}; outsha=probe.digest(out); members[outname]=outsha
                outcomes.append({"row_id":row["row_id"],"ordinal":ordinal,"status":"ACCEPTED","original_name":outname,"member_sha256":outsha,"value":out})
    fixture_wire=probe.wire(native_fixtures).decode(); members["fixture-inputs.json"]=probe.digest(fixture_wire.encode())
    docs={"saved_rows":{"contract":probe.CONTRACT,"rows":saved},"saved_requests":{"contract":probe.CONTRACT,"requests":requests},"saved_plans":{"contract":probe.CONTRACT,"plans":plans},"saved_outcomes":{"contract":probe.CONTRACT,"outcomes":outcomes},"fixture_inputs":{"contract":probe.CONTRACT,"source":{"original_name":"fixture-inputs.json","member_sha256":members["fixture-inputs.json"],"wire":fixture_wire},"fixtures":fixture_records},"t550_manifest":{"contract":probe.CONTRACT,"members":[{"original_name":k,"sha256":members[k]} for k in sorted(members)]},"t550_seal":dict(sorted(members.items()))}
    for role,value in docs.items():
        raw=probe.wire(value); path=source/"sources"/entries[role]["source_id"]; path.write_bytes(raw); entries[role].update(sha256=probe.digest(raw),size=len(raw))
    manifest["manifest_sha256"]=probe.digest({"contract":probe.CONTRACT,"files":manifest["files"]})
    preflight=bundle["preflight"]; preflight["source_manifest_sha256"]=manifest["manifest_sha256"]; preflight["old_seal_sha256"]=probe.digest((source/"sources"/entries["t550_seal"]["source_id"]).read_bytes()); preflight["preflight_sha256"]=probe.digest({k:v for k,v in preflight.items() if k!="preflight_sha256"})
    bundle["main_pin"]=probe.build_main_pin(bundle["rows"],manifest["manifest_sha256"],preflight["code_sha256"])
    (run/"input.json").write_bytes(probe.wire(bundle))
