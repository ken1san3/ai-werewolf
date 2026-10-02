"""Test-only lossless adapter from resolved-subject results to the T570 blind packet."""
from __future__ import annotations

import hashlib
import secrets
from copy import deepcopy
from pathlib import Path

from scripts import phase6_resolved_subject_probe as probe
from scripts.phase6_minimal_suite_quality import QUESTION_IDS
from tests.fixtures import phase6_plan_locked_rubric as rubric
from tests.fixtures import phase6_plan_locked_native_custody as custody

CONTRACT = "PHASE6_PLAN_LOCKED_PACKET_BINDING_V1"
INPUT_CONTRACT = "RESOLVED_SUBJECT_PACKET_SOURCE_V1"
FREEZE_V2 = "PHASE6_PLAN_LOCKED_NATIVE_PACKET_FREEZE_V2"
ANNOTATION_FREEZE_V2 = "PHASE6_PLAN_LOCKED_NATIVE_ANNOTATION_FREEZE_V2"


class PacketBindingError(ValueError):
    def __init__(self): super().__init__("PLAN_LOCKED_PACKET_BINDING_INTEGRITY")


def _fail(): raise PacketBindingError()
def _hex(value): return type(value) is str and len(value)==64 and all(c in "0123456789abcdef" for c in value)


def _binding(value):
    if type(value) is not dict or set(value)!={"parsed_result_sha256","member_sha256","locator_sha256"} or not all(_hex(x) for x in value.values()): _fail()
    return deepcopy(value)


def _message(status, text, binding):
    if status not in rubric.STATUSES: _fail()
    observed=type(text) is str and bool(text)
    if observed != (binding is not None): _fail()
    if status in rubric.MISSING and observed or status=="ACCEPTED" and not observed: _fail()
    payload={"result_status":status,"text_observation":"OBSERVED" if observed else "NOT_OBSERVED","text":text,"text_binding":None if binding is None else _binding(binding)}
    value={**payload,"observation_sha256":rubric.digest(payload)}
    rubric.validate_message(value); return value


def _candidate(value, fixture):
    if type(value) is not dict or set(value)!={"status","candidate_text","response_content","binding"}: _fail()
    status=value["status"]
    if status=="ACCEPTED":
        if type(value["response_content"]) is not str or type(value["candidate_text"]) is not str or not value["candidate_text"]: _fail()
        try: parsed=probe.q.product.parse_and_validate_generation_v2_candidate_structure("message",value["response_content"],fixture.catalog)
        except Exception: raise PacketBindingError() from None
        try: parsed_message=parsed.value["message"]
        except (KeyError,TypeError): raise PacketBindingError() from None
        if parsed_message!=value["candidate_text"]: _fail()
    elif value["candidate_text"] is not None or value["response_content"] is not None: _fail()
    return _message(status,value["candidate_text"],value["binding"])


def source_roots(rows):
    """Canonical normalized roots; the caller must pin the returned values independently."""
    if type(rows) is not list: _fail()
    source=rubric.digest([{k:row.get(k) for k in ("row_id","case_id","seed","population_status","plan","control_status","control_text","control_binding")} for row in rows])
    custody=rubric.digest([row.get("row_sha256") for row in rows])
    results=rubric.digest([None if row.get("candidate") is None else {k:row["candidate"].get(k) for k in ("status","candidate_text")} for row in rows])
    responses=rubric.digest([None if row.get("candidate") is None else {k:row["candidate"].get(k) for k in ("response_content","binding")} for row in rows])
    measurement=rubric.digest({"source_sha256":source,"custody_sha256":custody,"result_sha256":results,"response_seal_sha256":responses})
    return (source,custody,results,responses,measurement)


def validate_authority_pin(value, expected_sha256, evidence=None):
    keys={"contract","prepared_locator_file_sha256","input_file_sha256","preflight_file_sha256","source_manifest_file_sha256","main_pin_file_sha256","old_t550_seal_file_sha256","measurement_results_file_sha256","measurement_seal_file_sha256","runner_safe_file_sha256","operator_safe_file_sha256","operator_seal_file_sha256","source_roles_root_sha256","response_members_root_sha256","code_bundle_sha256","expected_population_root_sha256","pin_sha256"}
    if type(value) is not dict or set(value)!=keys or value["contract"]!=custody.CONTRACT or not all(_hex(value[k]) for k in keys-{"contract"}): _fail()
    if value["pin_sha256"]!=rubric.digest({k:value[k] for k in keys-{"pin_sha256"}}) or value["pin_sha256"]!=expected_sha256: _fail()
    if evidence is not None:
        if type(evidence) is not dict or set(evidence)!=keys-{"contract","pin_sha256"} or any(value[k]!=evidence[k] for k in evidence): _fail()


def validate_physical_custody(root, value, expected_roots_sha256, measurement_root=None):
    if type(value) is not dict or set(value)!={"payloads","roots"}: _fail()
    roots=value["roots"]
    if type(roots) is not dict or roots.get("roots_sha256")!=expected_roots_sha256 or roots.get("roots_sha256")!=rubric.digest({k:roots[k] for k in roots if k!="roots_sha256"}): _fail()
    payloads=value["payloads"]
    if type(payloads) is not dict or set(payloads)!={"prepared","sources","results","responses","seals"}: _fail()
    checks={"prepared_root_sha256":"prepared","source_rows_root_sha256":"sources","result_rows_root_sha256":"results","response_members_root_sha256":"responses","physical_seal_root_sha256":"seals"}
    if any(roots[k]!=rubric.digest(payloads[v]) for k,v in checks.items()): _fail()
    refs=[]
    p=payloads["prepared"]; refs += [p[x] for x in ("input","preflight","source_manifest","main_pin","old_t550_seal")]+p["source_roles"]
    refs.append(payloads["results"]["results_file"])
    for row in payloads["results"]["rows"]: refs += row["attempt_member_refs"]
    for item in payloads["responses"]["members"]: refs.append(item["file"])
    s=payloads["seals"]; refs += [s[x] for x in ("measurement_seal","runner_safe","operator_safe","operator_seal")]
    try:
        for ref in refs:
            base=measurement_root if measurement_root is not None and ref["role"] in ("results_file","measurement_attempt","measurement_response","measurement_seal","runner_safe","operator_safe","operator_seal") else root
            custody.validate_ref(base,ref)
    except custody.NativeCustodyError: raise PacketBindingError() from None
    return deepcopy(value)


def authority_evidence_from_custody(prepared_locator_ref,native_custody,code_bundle):
    if type(prepared_locator_ref) is not dict or not _hex(prepared_locator_ref.get("sha256")) or type(code_bundle) is not dict or not _hex(code_bundle.get("bundle_sha256")): _fail()
    p=native_custody["payloads"]["prepared"]; s=native_custody["payloads"]["seals"]
    return {"prepared_locator_file_sha256":prepared_locator_ref["sha256"],"input_file_sha256":p["input"]["sha256"],"preflight_file_sha256":p["preflight"]["sha256"],"source_manifest_file_sha256":p["source_manifest"]["sha256"],"main_pin_file_sha256":p["main_pin"]["sha256"],"old_t550_seal_file_sha256":p["old_t550_seal"]["sha256"],"measurement_results_file_sha256":native_custody["payloads"]["results"]["results_file"]["sha256"],"measurement_seal_file_sha256":s["measurement_seal"]["sha256"],"runner_safe_file_sha256":s["runner_safe"]["sha256"],"operator_safe_file_sha256":s["operator_safe"]["sha256"],"operator_seal_file_sha256":s["operator_seal"]["sha256"],"source_roles_root_sha256":rubric.digest(p["source_roles"]),"response_members_root_sha256":native_custody["roots"]["response_members_root_sha256"],"code_bundle_sha256":code_bundle["bundle_sha256"],"expected_population_root_sha256":native_custody["roots"]["roots_sha256"]}


def build_packet(source, fixtures, *, expected_source_sha256, expected_custody_sha256, expected_result_sha256,
                 expected_response_seal_sha256, expected_measurement_seal_sha256, rubric_sha256,
                 population_sha256, packet_binding_sha256, entropy=None):
    keys={"contract","source_sha256","custody_sha256","result_sha256","response_seal_sha256","measurement_seal_sha256","rows"}
    if type(source) is not dict or set(source)!=keys or source["contract"]!=INPUT_CONTRACT: _fail()
    expected=(expected_source_sha256,expected_custody_sha256,expected_result_sha256,expected_response_seal_sha256,expected_measurement_seal_sha256)
    actual=tuple(source[k] for k in ("source_sha256","custody_sha256","result_sha256","response_seal_sha256","measurement_seal_sha256"))
    if not all(_hex(x) for x in expected+(rubric_sha256,population_sha256,packet_binding_sha256)) or actual!=expected: _fail()
    if type(source["rows"]) is not list or len(source["rows"])!=96 or source_roots(source["rows"])!=expected: _fail()
    fixture_map={f.case.case_id:f for f in fixtures}
    if len(fixture_map)!=len(fixtures): _fail()
    random_bytes=secrets.token_bytes(4096) if entropy is None else entropy
    if type(random_bytes) is not bytes or len(random_bytes)<4096: _fail()
    rows=[]; mapping=[]; seen=set()
    counts={"TARGET":0,"PLAN_NOT_OBSERVED":0,"NOT_MESSAGE_DOMAIN":0}; control_count=0
    for index,source_row in enumerate(source["rows"]):
        rowkeys={"row_id","case_id","seed","population_status","plan","control_status","control_text","control_binding","candidate","row_sha256"}
        if type(source_row) is not dict or set(source_row)!=rowkeys or source_row["row_id"] in seen: _fail()
        seen.add(source_row["row_id"])
        payload={k:source_row[k] for k in rowkeys-{"row_sha256"}}
        if source_row["row_sha256"]!=rubric.digest(payload) or source_row["population_status"] not in counts: _fail()
        status=source_row["population_status"]; counts[status]+=1
        target=status=="TARGET"
        if target:
            fixture=fixture_map.get(source_row["case_id"])
            if fixture is None or type(source_row["plan"]) is not dict: _fail()
            alias="case_"+hashlib.sha256((source_row["row_id"]+random_bytes[index:index+32].hex()).encode()).hexdigest()[:32]
            intent=probe.chosen_intent(fixture,source_row["plan"],alias); rubric.validate_intent(intent)
            control=_message(source_row["control_status"],source_row["control_text"],source_row["control_binding"])
            candidate=_candidate(source_row["candidate"],fixture)
            control_count+=control["text_observation"]=="OBSERVED"
            pair=[("CONTROL",control),("CANDIDATE",candidate)]
            if random_bytes[1024+index]&1: pair.reverse()
            observations=[{"side":"A","observation":pair[0][1]},{"side":"B","observation":pair[1][1]}]
        else:
            if any(source_row[k] is not None for k in ("plan","control_text","control_binding","candidate")): _fail()
            alias=None; intent=None; observations=[{"side":"A","observation":None},{"side":"B","observation":None}]; pair=[("CONTROL",None),("CANDIDATE",None)]
        blind_id="blind_"+hashlib.sha256(random_bytes[2048+index:2080+index]+source_row["row_id"].encode()).hexdigest()[:32]
        row={"blind_id":blind_id,"population_status":status,"side_observations":observations,"chosen_intent":intent,"question_opportunity":source_row["case_id"] in QUESTION_IDS}
        rows.append(row)
        m={"blind_id":blind_id,"A_condition":pair[0][0],"B_condition":pair[1][0]}; m["condition_permutation_sha256"]=rubric.digest(m)
        mapping.append({**m,"row_id":source_row["row_id"],"case_id":source_row["case_id"],"seed":source_row["seed"]})
    if counts!={"TARGET":77,"PLAN_NOT_OBSERVED":7,"NOT_MESSAGE_DOMAIN":12} or control_count!=69: _fail()
    order=sorted(range(96),key=lambda i:hashlib.sha256(random_bytes[3000+i:3032+i]+rows[i]["blind_id"].encode()).digest())
    packet={"contract":rubric.CONTRACT,"rubric_sha256":rubric_sha256,"population_sha256":population_sha256,"packet_binding_sha256":packet_binding_sha256,"rows":[rows[i] for i in order]}
    private={"contract":CONTRACT,"rows":[mapping[i] for i in order]}
    evaluator_mapping={"contract":rubric.CONTRACT,"rows":[{k:x[k] for k in ("blind_id","A_condition","B_condition","condition_permutation_sha256")} for x in private["rows"]]}
    rubric.validate_packet(packet,expected_packet_sha256=rubric.digest(packet),expected_rubric_sha256=rubric_sha256,expected_population_sha256=population_sha256,expected_binding_sha256=packet_binding_sha256)
    rubric.validate_mapping(evaluator_mapping,packet)
    return {"packet":packet,"private_mapping":private,"evaluator_mapping":evaluator_mapping,"packet_sha256":rubric.digest(packet),"mapping_sha256":rubric.digest(evaluator_mapping),"source_roots_sha256":rubric.digest(list(expected))}


def build_packet_from_physical(source,fixtures,*,physical_root,native_custody,authority_pin,expected_authority_pin_sha256,expected_native_population_roots_sha256,entropy,**kwargs):
    validate_authority_pin(authority_pin,expected_authority_pin_sha256)
    validate_physical_custody(physical_root,native_custody,expected_native_population_roots_sha256)
    if authority_pin["expected_population_root_sha256"]!=expected_native_population_roots_sha256: _fail()
    binding=rubric.digest({"native_authority_pin_sha256":expected_authority_pin_sha256,"native_population_roots_sha256":expected_native_population_roots_sha256,"row_sha256":[x.get("row_sha256") for x in source.get("rows",[])],"entropy_sha256":hashlib.sha256(entropy).hexdigest()})
    kwargs["packet_binding_sha256"]=binding
    result=build_packet(source,fixtures,entropy=entropy,**kwargs)
    result["native_authority_pin_sha256"]=expected_authority_pin_sha256
    result["native_population_roots_sha256"]=expected_native_population_roots_sha256
    return result


def build_packet_from_hydrated_run(*,prepared_root,measurement_root,source_root=None,code_root,native_custody,authority_pin,prepared_locator_ref,code_bundle,
                                   expected_authority_pin_sha256,expected_native_population_roots_sha256,
                                   expected_input_sha256,expected_preflight_sha256,expected_manifest_sha256,
                                   expected_main_pin_sha256,expected_code_sha256,expected_results_sha256,
                                   expected_measurement_seal_sha256,fixtures,entropy,rubric_sha256,population_sha256):
    """Production adapter entry: physical files are the only source of normalized rows."""
    try:
        custody.validate_ref(prepared_root,prepared_locator_ref)
        custody.validate_code_bundle(code_root,code_bundle)
    except custody.NativeCustodyError: raise PacketBindingError() from None
    authority_evidence=authority_evidence_from_custody(prepared_locator_ref,native_custody,code_bundle)
    validate_authority_pin(authority_pin,expected_authority_pin_sha256,authority_evidence)
    validate_physical_custody(prepared_root,native_custody,expected_native_population_roots_sha256,measurement_root)
    try:
        hydrated=custody.hydrate_physical_run(prepared_root,measurement_root,source_root=source_root,expected_input_sha256=expected_input_sha256,
            expected_preflight_sha256=expected_preflight_sha256,expected_manifest_sha256=expected_manifest_sha256,
            expected_main_pin_sha256=expected_main_pin_sha256,expected_code_sha256=expected_code_sha256,
            expected_results_sha256=expected_results_sha256,expected_measurement_seal_sha256=expected_measurement_seal_sha256)
    except custody.NativeCustodyError: raise PacketBindingError() from None
    payloads=native_custody["payloads"]
    derived=hydrated["derived"]
    if payloads["sources"]["rows"]!=derived["row_bindings"] or payloads["sources"]["control_locators"]!=derived["control_locators"] or payloads["results"]["rows"]!=derived["result_bindings"] or payloads["responses"]["members"]!=derived["response_bindings"]: _fail()
    source=hydrated["normalized_source"]; roots=source_roots(source["rows"])
    controls={x["row_id"]:x for x in derived["control_locators"]}; responses={x["row_id"]:x for x in derived["response_bindings"]}
    for row,binding in zip(source["rows"],derived["row_bindings"]):
        if (row.get("row_id"),row.get("case_id"),row.get("seed"),row.get("population_status"))!=(binding["row_id"],binding["case_id"],binding["seed"],binding["population_status"]): _fail()
        control=controls.get(row["row_id"])
        if (control is None)!=(row.get("control_text") is None): _fail()
        if control is not None and (row.get("control_binding",{}).get("member_sha256")!=control["member_sha256"] or row["control_binding"].get("locator_sha256")!=control["locator_sha256"] or row["control_binding"].get("parsed_result_sha256")!=rubric.digest({"message":row["control_text"]})): _fail()
        response=responses.get(row["row_id"]); candidate=row.get("candidate")
        accepted=type(candidate) is dict and candidate.get("status")=="ACCEPTED"
        if (response is not None)!=accepted: _fail()
        if accepted and (candidate["binding"].get("member_sha256")!=response["file"]["sha256"] or candidate["binding"].get("locator_sha256")!=response["locator"]["locator_sha256"] or candidate["binding"].get("parsed_result_sha256")!=rubric.digest({"message":candidate["candidate_text"]})): _fail()
    source.update(source_sha256=roots[0],custody_sha256=roots[1],result_sha256=roots[2],response_seal_sha256=roots[3],measurement_seal_sha256=roots[4])
    binding=rubric.digest({"native_authority_pin_sha256":expected_authority_pin_sha256,"native_population_roots_sha256":expected_native_population_roots_sha256,"row_sha256":[x["row_sha256"] for x in source["rows"]],"entropy_sha256":hashlib.sha256(entropy).hexdigest()})
    return build_packet(source,fixtures,expected_source_sha256=roots[0],expected_custody_sha256=roots[1],expected_result_sha256=roots[2],expected_response_seal_sha256=roots[3],expected_measurement_seal_sha256=roots[4],rubric_sha256=rubric_sha256,population_sha256=population_sha256,packet_binding_sha256=binding,entropy=entropy)


def make_native_packet_freeze_v2(packet,mapping,*,source_manifest_sha256,native_authority_pin_sha256,native_population_roots_sha256,expected_population_sha256,expected_mapping_sha256,expected_rubric_sha256,expected_packet_sha256,expected_packet_binding_sha256):
    data=rubric.validate_packet(packet,expected_packet_sha256=expected_packet_sha256,expected_rubric_sha256=expected_rubric_sha256,expected_population_sha256=expected_population_sha256,expected_binding_sha256=expected_packet_binding_sha256)
    rubric.validate_mapping(mapping,packet)
    if rubric.digest(mapping)!=expected_mapping_sha256 or not all(_hex(x) for x in (source_manifest_sha256,native_authority_pin_sha256,native_population_roots_sha256)): _fail()
    bodies=data["bodies"]; both=bodies.count((True,True)); ao=bodies.count((True,False)); bo=bodies.count((False,True)); neither=bodies.count((False,False))
    payload={"contract":FREEZE_V2,"version":"V2","source_manifest_sha256":source_manifest_sha256,"native_authority_pin_sha256":native_authority_pin_sha256,"native_population_roots_sha256":native_population_roots_sha256,"population_sha256":expected_population_sha256,"mapping_sha256":expected_mapping_sha256,"rubric_sha256":expected_rubric_sha256,"packet_sha256":expected_packet_sha256,"packet_binding_sha256":expected_packet_binding_sha256,"rows":96,"target_rows":77,"nontarget_rows":19,"side_a_body_rows":sum(x[0] for x in bodies),"side_b_body_rows":sum(x[1] for x in bodies),"both_body_rows":both,"side_a_only_body_rows":ao,"side_b_only_body_rows":bo,"neither_body_rows":neither,"question_opportunities":54,"question_target_rows":data["question_counts"]["TARGET"],"question_plan_missing_rows":data["question_counts"]["PLAN_NOT_OBSERVED"],"question_non_message_rows":data["question_counts"]["NOT_MESSAGE_DOMAIN"]}
    return {**payload,"freeze_sha256":rubric.digest(payload)}


def validate_native_packet_freeze_v2(packet,mapping,freeze,*,expected_packet_freeze_sha256,expected_source_manifest_sha256,expected_native_authority_pin_sha256,expected_native_population_roots_sha256,expected_population_sha256,expected_mapping_sha256,expected_rubric_sha256,expected_packet_sha256,expected_packet_binding_sha256):
    built=make_native_packet_freeze_v2(packet,mapping,source_manifest_sha256=expected_source_manifest_sha256,native_authority_pin_sha256=expected_native_authority_pin_sha256,native_population_roots_sha256=expected_native_population_roots_sha256,expected_population_sha256=expected_population_sha256,expected_mapping_sha256=expected_mapping_sha256,expected_rubric_sha256=expected_rubric_sha256,expected_packet_sha256=expected_packet_sha256,expected_packet_binding_sha256=expected_packet_binding_sha256)
    if freeze!=built or freeze["freeze_sha256"]!=expected_packet_freeze_sha256: _fail()
    return deepcopy(built)


def freeze_annotation_v2(annotation,packet,mapping,packet_freeze_v2,*,evaluator_identity,expected_evaluator_identity_sha256,**proof):
    validate_native_packet_freeze_v2(packet,mapping,packet_freeze_v2,**proof)
    rubric.validate_annotation(annotation,packet,packet_freeze_v2,evaluator_identity=evaluator_identity,expected_evaluator_identity_sha256=expected_evaluator_identity_sha256)
    pairs={m:[f"{v}:{reason}" for v,reason in (("PASS",rubric.POSITIVE[m]),("FAIL",rubric.NEGATIVE[m]),("UNKNOWN","CONTEXT_INSUFFICIENT"),("MEASUREMENT_NOT_OBSERVED","OBSERVATION_MISSING"),("NOT_APPLICABLE","METRIC_NOT_APPLICABLE"))] for m in rubric.METRICS}
    counts={s:{m:{p:0 for p in pairs[m]} for m in rubric.METRICS} for s in ("A","B")}
    for row in annotation["rows"]:
        for metric in row["metrics"]:
            for side,key in (("A","side_a"),("B","side_b")):
                x=metric[key]; counts[side][metric["metric"]][f'{x["value"]}:{x["reason"]}']+=1
    payload={"contract":ANNOTATION_FREEZE_V2,"version":"V2","packet_sha256":rubric.digest(packet),"rubric_sha256":packet["rubric_sha256"],"packet_freeze_sha256":packet_freeze_v2["freeze_sha256"],"native_authority_pin_sha256":packet_freeze_v2["native_authority_pin_sha256"],"native_population_roots_sha256":packet_freeze_v2["native_population_roots_sha256"],"source_manifest_sha256":packet_freeze_v2["source_manifest_sha256"],"annotation_sha256":rubric.digest(annotation),"annotation_rows_sha256":rubric.digest(annotation["rows"]),"evaluator_identity_sha256":expected_evaluator_identity_sha256,"rows":96,"annotated_rows":77,"not_required_rows":19,"side_a_metric_value_reason_counts":counts["A"],"side_b_metric_value_reason_counts":counts["B"],"both_body_rows":packet_freeze_v2["both_body_rows"],"side_a_only_body_rows":packet_freeze_v2["side_a_only_body_rows"],"side_b_only_body_rows":packet_freeze_v2["side_b_only_body_rows"],"neither_body_rows":packet_freeze_v2["neither_body_rows"],"question_opportunities":54,"fresh_independent_evaluator":True}
    return {**payload,"freeze_sha256":rubric.digest(payload)}


def unblind_v2(annotation,packet,mapping,packet_freeze_v2,annotation_freeze_v2,*,evaluator_identity,expected_evaluator_identity_sha256,expected_annotation_freeze_sha256,**proof):
    validate_native_packet_freeze_v2(packet,mapping,packet_freeze_v2,**proof)
    built=freeze_annotation_v2(annotation,packet,mapping,packet_freeze_v2,evaluator_identity=evaluator_identity,expected_evaluator_identity_sha256=expected_evaluator_identity_sha256,**proof)
    if annotation_freeze_v2!=built or built["freeze_sha256"]!=expected_annotation_freeze_sha256: _fail()
    condition_counts={c:{m:{v:0 for v in rubric.VALUES} for m in rubric.METRICS} for c in ("CONTROL","CANDIDATE")}
    pairs={m:{k:0 for k in ("IMPROVED","REGRESSED","UNCHANGED","COMPARISON_UNKNOWN","NOT_APPLICABLE")} for m in rubric.METRICS}
    question={c:{v:0 for v in ("PASS","FAIL","UNKNOWN","MEASUREMENT_NOT_OBSERVED")} for c in ("CONTROL","CANDIDATE")}; body={"both":0,"control_only":0,"candidate_only":0,"neither":0}; control=0
    for prow,arow,mrow in zip(packet["rows"],annotation["rows"],mapping["rows"]):
        if prow["population_status"]!="TARGET":
            if prow["question_opportunity"]:
                for c in question: question[c]["MEASUREMENT_NOT_OBSERVED"]+=1
            continue
        index={mrow["A_condition"]:0,mrow["B_condition"]:1}
        observed={c:prow["side_observations"][i]["observation"] is not None and prow["side_observations"][i]["observation"]["text_observation"]=="OBSERVED" for c,i in index.items()}
        control+=observed["CONTROL"]; body["both" if all(observed.values()) else "control_only" if observed["CONTROL"] else "candidate_only" if observed["CANDIDATE"] else "neither"]+=1
        for metric in arow["metrics"]:
            values={c:metric["side_a" if i==0 else "side_b"]["value"] for c,i in index.items()}
            for c,v in values.items(): condition_counts[c][metric["metric"]][v]+=1
            if metric["metric"]=="BODY_ANSWER" and prow["question_opportunity"]:
                for c,v in values.items():
                    if v=="NOT_APPLICABLE": _fail()
                    question[c][v]+=1
            outcome="NOT_APPLICABLE" if "NOT_APPLICABLE" in values.values() else "COMPARISON_UNKNOWN" if any(v in ("UNKNOWN","MEASUREMENT_NOT_OBSERVED") for v in values.values()) else "IMPROVED" if values=={"CONTROL":"FAIL","CANDIDATE":"PASS"} else "REGRESSED" if values=={"CONTROL":"PASS","CANDIDATE":"FAIL"} else "UNCHANGED"
            pairs[metric["metric"]][outcome]+=1
    if control!=69 or sum(body.values())!=77 or any(sum(x.values())!=54 for x in question.values()): _fail()
    return {"contract":rubric.CONTRACT,"rows":96,"target_rows":77,"non_target_rows":19,"control_observed_rows":69,"control_missing_rows":8,"body_partition":body,"condition_metric_counts":condition_counts,"pair_outcome_counts":pairs,"question_denominator":54,"question_counts":question}


def write_once(path, value):
    try:
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with Path(path).open("xb") as stream: stream.write(rubric.wire(value))
    except OSError: raise PacketBindingError() from None
