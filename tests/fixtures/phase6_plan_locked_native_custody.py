"""Independent physical-root reconstruction for T573 public/native fixtures."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from tests.fixtures import phase6_plan_locked_rubric as r
from scripts import phase6_resolved_subject_probe as probe
from scripts.phase6_resolved_subject_custody import reconstruct_expected_pin

CONTRACT="PHASE6_PLAN_LOCKED_NATIVE_BINDING_V1"
ROLES=("t550_manifest","t550_seal","saved_rows","saved_requests","saved_outcomes","saved_plans","fixture_inputs","runner_source","probe_source","product_source","profile","config","model_metadata","design_approval","task_packet")

class NativeCustodyError(ValueError):
    def __init__(self): super().__init__("PLAN_LOCKED_NATIVE_CUSTODY_INTEGRITY")

def fail(): raise NativeCustodyError()
def hex64(x): return type(x) is str and len(x)==64 and all(c in "0123456789abcdef" for c in x)
def strict_int(x): return type(x) is int and x>=0

def _plain(root, relative):
    if type(relative) is not str or not relative or "\\" in relative or relative.startswith("/") or any(x in ("",".","..") for x in relative.split("/")): fail()
    root=Path(root).absolute(); path=(root/relative).absolute()
    try:
        if os.path.commonpath((root,path))!=str(root) or path.is_symlink() or not path.is_file(): fail()
        current=path
        while current!=root:
            if current.is_symlink(): fail()
            current=current.parent
    except (OSError,ValueError): raise NativeCustodyError() from None
    return path

def physical_ref(root, role, relative):
    path=_plain(root,relative); raw=path.read_bytes()
    return {"role":role,"relative_name":relative,"sha256":hashlib.sha256(raw).hexdigest(),"size":len(raw)}

def validate_ref(root, value):
    if type(value) is not dict or set(value)!={"role","relative_name","sha256","size"} or type(value["role"]) is not str or not value["role"] or not hex64(value["sha256"]) or not strict_int(value["size"]): fail()
    if physical_ref(root,value["role"],value["relative_name"])!=value: fail()

def build_code_bundle(root, paths):
    roles=("T572_ADAPTER","MAIN_CUSTODY","T567_PROBE","T567_CUSTODY","T570_RUBRIC","T569_DESIGN","T570_REVIEW","T573_DESIGN","T573_REVIEW")
    if type(paths) is not list or len(paths)!=len(roles): fail()
    files=[]
    for role,path in zip(roles,paths):
        ref=physical_ref(root,role,path)
        files.append({"role":role,"repository_path":ref["relative_name"],"sha256":ref["sha256"],"size":ref["size"]})
    payload={"contract":"T573_NATIVE_CODE_BUNDLE_V1","files":files}
    return {**payload,"bundle_sha256":r.digest(payload)}

def validate_code_bundle(root,value):
    if type(value) is not dict or set(value)!={"contract","files","bundle_sha256"} or value["contract"]!="T573_NATIVE_CODE_BUNDLE_V1" or type(value["files"]) is not list: fail()
    rebuilt=build_code_bundle(root,[x.get("repository_path") for x in value["files"]])
    if rebuilt!=value: fail()
    return rebuilt

def build_native_roots(root, *, prepared_files, source_roles, row_bindings, control_locators, results_file,
                       result_bindings, response_bindings, seal_files, measurement_coverage, operator_coverage):
    if type(prepared_files) is not list or len(prepared_files)!=5 or [x.get("role") for x in prepared_files] != ["input","preflight","source_manifest","main_pin","old_t550_seal"]: fail()
    if type(source_roles) is not list or [x.get("role") for x in source_roles]!=list(ROLES): fail()
    for ref in prepared_files+source_roles+[results_file]+seal_files: validate_ref(root,ref)
    if type(row_bindings) is not list or len(row_bindings)!=96: fail()
    identities=set()
    for i,row in enumerate(row_bindings):
        keys={"ordinal","row_id","case_id","seed","population_status","prepared_row_sha256","native_member_sha256","fixture_binding_sha256","result_row_sha256"}
        if type(row) is not dict or set(row)!=keys or row["ordinal"]!=i or type(row["row_id"]) is not str or not row["row_id"] or type(row["case_id"]) is not str or not row["case_id"] or not strict_int(row["seed"]) or row["population_status"] not in ("TARGET","PLAN_NOT_OBSERVED","NOT_MESSAGE_DOMAIN") or not all(hex64(row[k]) for k in ("prepared_row_sha256","native_member_sha256","fixture_binding_sha256","result_row_sha256")): fail()
        identity=(row["case_id"],row["seed"])
        if identity in identities: fail()
        identities.add(identity)
    _locators(control_locators,"CONTROL",69)
    if type(result_bindings) is not list or len(result_bindings)!=96: fail()
    for i,item in enumerate(result_bindings):
        if type(item) is not dict or set(item)!={"ordinal","row_id","result_row_sha256","attempt_member_refs"} or item["ordinal"]!=i or item["row_id"]!=row_bindings[i]["row_id"] or not hex64(item["result_row_sha256"]) or type(item["attempt_member_refs"]) is not list: fail()
        for ref in item["attempt_member_refs"]: validate_ref(root,ref)
    if type(response_bindings) is not list: fail()
    last=(-1,0)
    for item in response_bindings:
        if type(item) is not dict or set(item)!={"row_ordinal","row_id","attempt_ordinal","file","locator"}: fail()
        key=(item["row_ordinal"],item["attempt_ordinal"])
        if key<=last or item["row_id"]!=row_bindings[item["row_ordinal"]]["row_id"]: fail()
        last=key; validate_ref(root,item["file"]); _locators([item["locator"]],"CANDIDATE",1)
        if item["file"]["sha256"]!=item["locator"]["member_sha256"]: fail()
    prepared={"contract":"T568_PREPARED_ROOT_PAYLOAD_V1","input":prepared_files[0],"preflight":prepared_files[1],"source_manifest":prepared_files[2],"main_pin":prepared_files[3],"old_t550_seal":prepared_files[4],"source_roles":source_roles}
    sources={"contract":"T568_SOURCE_ROWS_ROOT_PAYLOAD_V1","rows":row_bindings,"control_locators":control_locators}
    results={"contract":"T568_RESULT_ROWS_ROOT_PAYLOAD_V1","results_file":results_file,"rows":result_bindings}
    responses={"contract":"T568_RESPONSE_MEMBERS_ROOT_PAYLOAD_V1","members":response_bindings}
    expected_measure=[{"relative_name":results_file["relative_name"],"member_sha256":results_file["sha256"]}]
    for row in result_bindings:
        expected_measure += [{"relative_name":x["relative_name"],"member_sha256":x["sha256"]} for x in row["attempt_member_refs"]]
    expected_measure += [{"relative_name":x["file"]["relative_name"],"member_sha256":x["file"]["sha256"]} for x in response_bindings]
    expected_measure=sorted({x["relative_name"]:x for x in expected_measure}.values(),key=lambda x:x["relative_name"].encode())
    expected_operator=sorted(expected_measure+[{"relative_name":x["relative_name"],"member_sha256":x["sha256"]} for x in seal_files[:3]],key=lambda x:x["relative_name"].encode())
    if measurement_coverage!=expected_measure or operator_coverage!=expected_operator: fail()
    for coverage in (measurement_coverage,operator_coverage):
        if type(coverage) is not list or coverage!=sorted(coverage,key=lambda x:x["relative_name"].encode()) or len({x["relative_name"].casefold() for x in coverage})!=len(coverage): fail()
        if any(type(x) is not dict or set(x)!={"relative_name","member_sha256"} or not hex64(x["member_sha256"]) for x in coverage): fail()
    if [x.get("role") for x in seal_files]!=["measurement_seal","runner_safe","operator_safe","operator_seal"]: fail()
    seals={"contract":"T568_PHYSICAL_SEAL_ROOT_PAYLOAD_V1","measurement_seal":seal_files[0],"runner_safe":seal_files[1],"operator_safe":seal_files[2],"operator_seal":seal_files[3],"measurement_coverage":measurement_coverage,"operator_coverage":operator_coverage}
    roots={"contract":"T568_NATIVE_POPULATION_ROOTS_V1","prepared_root_sha256":r.digest(prepared),"source_rows_root_sha256":r.digest(sources),"result_rows_root_sha256":r.digest(results),"response_members_root_sha256":r.digest(responses),"physical_seal_root_sha256":r.digest(seals)}
    return {"payloads":{"prepared":prepared,"sources":sources,"results":results,"responses":responses,"seals":seals},"roots":{**roots,"roots_sha256":r.digest(roots)}}

def _locators(values,condition,count):
    if type(values) is not list or len(values)!=count: fail()
    for value in values:
        keys={"condition","row_id","case_id","seed","ordinal","source_role","member_name","member_sha256","json_pointer","encoding","locator_sha256"}
        if type(value) is not dict or set(value)!=keys or value["condition"]!=condition or not strict_int(value["seed"]) or value["ordinal"] not in (1,2) or value["encoding"]!="JSON_STRING_UTF8" or not hex64(value["member_sha256"]): fail()
        expected=("saved_rows","/final/message",1) if condition=="CONTROL" else ("measurement_response","/choices/0/message/content",value["ordinal"])
        if (value["source_role"],value["json_pointer"],value["ordinal"])!=expected: fail()
        if value["locator_sha256"]!=r.digest({k:value[k] for k in keys-{"locator_sha256"}}): fail()


def hydrate_physical_run(prepared_root, measurement_root, *, source_root=None, expected_input_sha256, expected_preflight_sha256,
                         expected_manifest_sha256, expected_main_pin_sha256, expected_code_sha256,
                         expected_results_sha256, expected_measurement_seal_sha256):
    """Derive normalized rows and physical roots only from frozen physical files."""
    try:
        prepared_root=Path(prepared_root); measurement_root=Path(measurement_root); source_root=prepared_root if source_root is None else Path(source_root)
        input_raw=_plain(prepared_root,"input.json").read_bytes()
        if hashlib.sha256(input_raw).hexdigest()!=expected_input_sha256: fail()
        bundle=probe._run_bundle(probe.parse(input_raw),expected_preflight_sha256,expected_manifest_sha256,expected_main_pin_sha256)
        if reconstruct_expected_pin(source_root,bundle["source_manifest"],expected_manifest_sha256,expected_code_sha256)!=bundle["main_pin"]: fail()
        loaded=probe.validate_source_root(source_root,bundle["source_manifest"],expected_manifest_sha256)
        entries={x["role"]:x for x in bundle["source_manifest"]["files"]}
        saved=probe.parse(loaded[entries["saved_rows"]["source_id"]]); fixture_doc=probe.parse(loaded[entries["fixture_inputs"]["source_id"]])
        native_records={x["run_row"]["row_id"]:x for x in saved["rows"]}
        fixture_records={x["row_id"]:x for x in fixture_doc["fixtures"]}
        if len(native_records)!=96 or len(fixture_records)!=96: fail()
        results_path=_plain(measurement_root,"results.json"); results_raw=results_path.read_bytes()
        if hashlib.sha256(results_raw).hexdigest()!=expected_results_sha256: fail()
        results=probe.parse(results_raw)
        if type(results) is not dict or set(results)!={"contract","rows"} or type(results["rows"]) is not list or len(results["rows"])!=96: fail()
        seal_path=_plain(measurement_root,"seal.json"); seal_raw=seal_path.read_bytes()
        if hashlib.sha256(seal_raw).hexdigest()!=expected_measurement_seal_sha256: fail()
        seal=probe.parse(seal_raw)
        if type(seal) is not dict: fail()
        actual={p.relative_to(measurement_root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in measurement_root.rglob("*") if p.is_file() and p.absolute()!=seal_path}
        if seal!=actual: fail()
        result_map={x.get("row_id"):x for x in results["rows"]}
        if len(result_map)!=96: fail()
        fixtures={f.case.case_id:f for f in probe.q.prepare()[0]}
        normalized=[]; row_bindings=[]; controls=[]; result_bindings=[]; responses=[]
        for ordinal,run_row in enumerate(bundle["rows"]):
            row_id=run_row["row_id"]; native=native_records.get(row_id); result=result_map.get(row_id); fixture_record=fixture_records.get(row_id)
            if native is None or result is None or fixture_record is None: fail()
            value=native["native"]["value"]; fixture=fixtures.get(run_row["case_id"])
            if fixture is None or value["case_id"]!=run_row["case_id"] or type(run_row["seed"]) is not int or type(run_row["seed"]) is bool or value["seed"]!=run_row["seed"]: fail()
            population="TARGET" if run_row["status"] in ("READY","INPUT_NOT_CHANGED") else run_row["status"]
            if population not in ("TARGET","PLAN_NOT_OBSERVED","NOT_MESSAGE_DOMAIN"): fail()
            control_text=run_row["control_text"] if population=="TARGET" else None; control_binding=None
            if control_text is not None:
                loc={"condition":"CONTROL","row_id":row_id,"case_id":run_row["case_id"],"seed":run_row["seed"],"ordinal":1,"source_role":"saved_rows","member_name":native["native"]["original_name"],"member_sha256":native["native"]["member_sha256"],"json_pointer":"/final/message","encoding":"JSON_STRING_UTF8"}; loc["locator_sha256"]=r.digest(loc); controls.append(loc)
                control_binding={"parsed_result_sha256":r.digest({"message":control_text}),"member_sha256":loc["member_sha256"],"locator_sha256":loc["locator_sha256"]}
            attempts=result.get("attempts"); status=result.get("status"); candidate_text=result.get("candidate_text")
            if type(attempts) is not list or status not in r.STATUSES+("PLAN_NOT_OBSERVED","NOT_MESSAGE_DOMAIN","MEASUREMENT_NOT_OBSERVED"): fail()
            refs=[]; candidate=None
            for attempt in attempts:
                if type(attempt) is not dict or set(attempt)!={"ordinal","status"} or attempt["ordinal"] not in (1,2): fail()
                name=f"attempt-{row_id}-{attempt['ordinal']}.json"; ref=physical_ref(measurement_root,"measurement_attempt",name); refs.append(ref)
            if status=="ACCEPTED":
                if not attempts or attempts[-1]["status"]!="ACCEPTED" or type(candidate_text) is not str or not candidate_text: fail()
                terminal=attempts[-1]["ordinal"]; name=f"response-{row_id}-{terminal}.json"; ref=physical_ref(measurement_root,"measurement_response",name)
                response=probe.parse(_plain(measurement_root,name).read_bytes()); content=response["choices"][0]["message"]["content"]
                parsed=probe.q.product.parse_and_validate_generation_v2_candidate_structure("message",content,fixture.catalog)
                if parsed.value["message"]!=candidate_text: fail()
                loc={"condition":"CANDIDATE","row_id":row_id,"case_id":run_row["case_id"],"seed":run_row["seed"],"ordinal":terminal,"source_role":"measurement_response","member_name":name,"member_sha256":ref["sha256"],"json_pointer":"/choices/0/message/content","encoding":"JSON_STRING_UTF8"}; loc["locator_sha256"]=r.digest(loc)
                candidate={"status":status,"candidate_text":candidate_text,"response_content":content,"binding":{"parsed_result_sha256":r.digest({"message":candidate_text}),"member_sha256":ref["sha256"],"locator_sha256":loc["locator_sha256"]}}
                responses.append({"row_ordinal":ordinal,"row_id":row_id,"attempt_ordinal":terminal,"file":ref,"locator":loc})
            else:
                if candidate_text is not None: fail()
                candidate={"status":status,"candidate_text":None,"response_content":None,"binding":None} if population=="TARGET" else None
            row={"row_id":row_id,"case_id":run_row["case_id"],"seed":run_row["seed"],"population_status":population,"plan":run_row["plan"] if population=="TARGET" else None,"control_status":"ACCEPTED" if control_text is not None else "MISSING_RESPONSE","control_text":control_text,"control_binding":control_binding,"candidate":candidate}; row["row_sha256"]=r.digest(row); normalized.append(row)
            row_bindings.append({"ordinal":ordinal,"row_id":row_id,"case_id":row["case_id"],"seed":row["seed"],"population_status":population,"prepared_row_sha256":probe.digest(run_row),"native_member_sha256":native["native"]["member_sha256"],"fixture_binding_sha256":r.digest(fixture_record),"result_row_sha256":r.digest(result)})
            result_bindings.append({"ordinal":ordinal,"row_id":row_id,"result_row_sha256":r.digest(result),"attempt_member_refs":refs})
        return {"normalized_source":{"contract":"RESOLVED_SUBJECT_PACKET_SOURCE_V1","rows":normalized},"derived":{"row_bindings":row_bindings,"control_locators":controls,"result_bindings":result_bindings,"response_bindings":responses},"bundle":bundle,"results":results,"seal":seal}
    except NativeCustodyError: raise
    except Exception: raise NativeCustodyError() from None
