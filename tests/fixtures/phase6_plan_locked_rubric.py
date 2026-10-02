"""Pure offline contract for PHASE6_PLAN_LOCKED_RUBRIC_V1."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path

CONTRACT = "PHASE6_PLAN_LOCKED_RUBRIC_V1"
ANNOTATION_CONTRACT = "PHASE6_PLAN_LOCKED_ANNOTATION_V1"
METRICS = ("ACT_MESSAGE_ALIGNMENT", "BODY_ANSWER", "PUBLIC_SOURCE_CONSISTENCY", "COPY_INDEPENDENCE", "TEXT_COMPLETENESS")
VALUES = ("PASS", "FAIL", "UNKNOWN", "MEASUREMENT_NOT_OBSERVED", "NOT_APPLICABLE")
PATHS = ("subject", "reply", "public_trigger", "current_public_state")
STATUSES = ("ACCEPTED", "GUARD_REJECT", "STRUCTURE_INVALID", "LENGTH", "MISSING_RESPONSE",
            "TRANSPORT_GENERATION_LOST", "TRANSPORT_OR_OWNERSHIP", "CONTEXT_INVALID", "INPUT_NOT_CHANGED")
MISSING = {"MISSING_RESPONSE", "TRANSPORT_GENERATION_LOST", "TRANSPORT_OR_OWNERSHIP", "CONTEXT_INVALID", "INPUT_NOT_CHANGED"}
POSITIVE = {"ACT_MESSAGE_ALIGNMENT":"ALIGNED", "BODY_ANSWER":"ANSWERED", "PUBLIC_SOURCE_CONSISTENCY":"SOURCE_CONSISTENT",
            "COPY_INDEPENDENCE":"INDEPENDENT", "TEXT_COMPLETENESS":"COMPLETE"}
NEGATIVE = {"ACT_MESSAGE_ALIGNMENT":"CONTRADICTED", "BODY_ANSWER":"NOT_ANSWERED", "PUBLIC_SOURCE_CONSISTENCY":"SOURCE_CONTRADICTION",
            "COPY_INDEPENDENCE":"OVERLAPPING_COPY", "TEXT_COMPLETENESS":"INCOMPLETE"}


class RubricInputError(ValueError):
    def __init__(self): super().__init__("PLAN_LOCKED_INPUT_INTEGRITY")


def fail(): raise RubricInputError()


def walk(value):
    if value is None or type(value) in (str, bool, int): return
    if type(value) is float and math.isfinite(value): return
    if type(value) is list:
        for item in value: walk(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str: fail()
            walk(item)
        return
    fail()


def wire(value):
    walk(value)
    try: return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (UnicodeError, ValueError): raise RubricInputError() from None


def digest(value): return hashlib.sha256(value if type(value) is bytes else wire(value)).hexdigest()


def parse(raw):
    def pairs(items):
        out={}
        for key,value in items:
            if key in out: fail()
            out[key]=value
        return out
    try: value=json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: fail())
    except (UnicodeError, json.JSONDecodeError): raise RubricInputError() from None
    walk(value); return value


def hex64(value): return type(value) is str and len(value)==64 and all(c in "0123456789abcdef" for c in value)
def sint(value): return type(value) is int and value >= 0
def ids(value): return type(value) is list and all(type(x) is str and x for x in value) and len(value)==len(set(value))


def validate_intent(value):
    keys={"case_alias","act","subject","reply","public_trigger","current_public_state","observation_sha256"}
    if type(value) is not dict or set(value)!=keys: fail()
    if type(value["case_alias"]) is not str or not value["case_alias"].startswith("case_") or len(value["case_alias"])!=37 or any(c not in "0123456789abcdef" for c in value["case_alias"][5:]): fail()
    if value["act"] not in ("ANSWER","REBUTTAL","QUESTION","CLAIM","OPINION_CHANGE","NONE"): fail()
    if not (value["subject"] is None or type(value["subject"]) is str and value["subject"]): fail()
    reply=value["reply"]
    if reply is not None:
        if type(reply) is not dict or set(reply)!={"source","actor_player_ids","channel_id","day","phase","text_excerpt","text_truncated"}: fail()
        if type(reply["source"]) is not dict or set(reply["source"])!={"record_kind","order","visibility"}: fail()
        if reply["source"]["record_kind"]!="chat" or reply["source"]["visibility"]!="PUBLIC" or not sint(reply["source"]["order"]): fail()
        if not ids(reply["actor_player_ids"]) or type(reply["channel_id"]) is not str or not reply["channel_id"] or not sint(reply["day"]) or type(reply["phase"]) is not str or not reply["phase"] or type(reply["text_excerpt"]) is not str or type(reply["text_truncated"]) is not bool: fail()
    trigger=value["public_trigger"]
    if type(trigger) is not dict or set(trigger)!={"kind","day","phase","source"} or trigger["kind"] not in ("INITIAL_CHAT","PEER_CHAT") or not sint(trigger["day"]) or type(trigger["phase"]) is not str or not trigger["phase"]: fail()
    if trigger["source"] is not None:
        if type(trigger["source"]) is not dict or set(trigger["source"])!={"record_kind","order","visibility"}: fail()
        if trigger["source"]["record_kind"]!="chat" or trigger["source"]["visibility"]!="PUBLIC" or not sint(trigger["source"]["order"]): fail()
    state=value["current_public_state"]
    if type(state) is not dict or set(state)!={"alive_player_ids","day","phase","players","vote_candidate_player_ids"} or not ids(state["alive_player_ids"]) or not ids(state["vote_candidate_player_ids"]) or not sint(state["day"]) or type(state["phase"]) is not str or not state["phase"] or type(state["players"]) is not list: fail()
    player_ids=[]
    for player in state["players"]:
        if type(player) is not dict or set(player)!={"player_id","alive","death"} or type(player["player_id"]) is not str or not player["player_id"] or type(player["alive"]) is not bool: fail()
        death=player["death"]
        if death is not None and (type(death) is not dict or set(death)!={"day","public_cause"} or not sint(death["day"]) or type(death["public_cause"]) is not str or not death["public_cause"]): fail()
        player_ids.append(player["player_id"])
    if len(player_ids)!=len(set(player_ids)): fail()
    payload={k:value[k] for k in ("case_alias","act","subject","reply","public_trigger","current_public_state")}
    if value["observation_sha256"]!=digest(payload): fail()


def validate_message(value):
    if value is None: return False
    if type(value) is not dict or set(value)!={"result_status","text_observation","text","text_binding","observation_sha256"}: fail()
    status=value["result_status"]; observed=value["text_observation"]=="OBSERVED"
    if status not in STATUSES or value["text_observation"] not in ("OBSERVED","NOT_OBSERVED"): fail()
    if observed:
        if type(value["text"]) is not str or not value["text"] or type(value["text_binding"]) is not dict or set(value["text_binding"])!={"parsed_result_sha256","member_sha256","locator_sha256"} or not all(hex64(x) for x in value["text_binding"].values()): fail()
        if status in MISSING: fail()
    elif value["text"] is not None or value["text_binding"] is not None or status=="ACCEPTED": fail()
    payload={k:value[k] for k in ("result_status","text_observation","text","text_binding")}
    if value["observation_sha256"]!=digest(payload): fail()
    return observed


def validate_packet(packet, *, expected_packet_sha256, expected_rubric_sha256, expected_population_sha256, expected_binding_sha256):
    if type(packet) is not dict or set(packet)!={"contract","rubric_sha256","population_sha256","packet_binding_sha256","rows"} or packet["contract"]!=CONTRACT: fail()
    if digest(packet)!=expected_packet_sha256 or packet["rubric_sha256"]!=expected_rubric_sha256 or packet["population_sha256"]!=expected_population_sha256 or packet["packet_binding_sha256"]!=expected_binding_sha256: fail()
    rows=packet["rows"]
    if type(rows) is not list or len(rows)!=96: fail()
    blind=[]; aliases=[]; counts={"TARGET":0,"PLAN_NOT_OBSERVED":0,"NOT_MESSAGE_DOMAIN":0}; qcounts={k:0 for k in counts}; bodies=[]
    for row in rows:
        if type(row) is not dict or set(row)!={"blind_id","population_status","side_observations","chosen_intent","question_opportunity"}: fail()
        if type(row["blind_id"]) is not str or not row["blind_id"] or row["blind_id"] in blind or row["population_status"] not in counts or type(row["question_opportunity"]) is not bool: fail()
        blind.append(row["blind_id"]); counts[row["population_status"]]+=1; qcounts[row["population_status"]]+=row["question_opportunity"]
        sides=row["side_observations"]
        if type(sides) is not list or len(sides)!=2 or [s.get("side") for s in sides] != ["A","B"]: fail()
        observed=[]
        for side in sides:
            if type(side) is not dict or set(side)!={"side","observation"}: fail()
            observed.append(validate_message(side["observation"]))
        if row["population_status"]=="TARGET":
            if row["chosen_intent"] is None: fail()
            validate_intent(row["chosen_intent"])
            if row["chosen_intent"]["case_alias"] in aliases: fail()
            aliases.append(row["chosen_intent"]["case_alias"]); bodies.append(tuple(observed))
        elif row["chosen_intent"] is not None or any(side["observation"] is not None for side in sides): fail()
    if counts!={"TARGET":77,"PLAN_NOT_OBSERVED":7,"NOT_MESSAGE_DOMAIN":12} or sum(qcounts.values())!=54: fail()
    return {"counts":counts,"question_counts":qcounts,"bodies":bodies,"blind_ids":blind}


def validate_mapping(mapping, packet):
    if type(mapping) is not dict or set(mapping)!={"contract","rows"} or mapping["contract"]!=CONTRACT or type(mapping["rows"]) is not list or len(mapping["rows"])!=96: fail()
    pids=[r["blind_id"] for r in packet["rows"]]
    for expected,row in zip(pids,mapping["rows"]):
        if type(row) is not dict or set(row)!={"blind_id","A_condition","B_condition","condition_permutation_sha256"} or row["blind_id"]!=expected or {row["A_condition"],row["B_condition"]}!={"CONTROL","CANDIDATE"}: fail()
        if row["condition_permutation_sha256"]!=digest({k:row[k] for k in ("blind_id","A_condition","B_condition")}): fail()


def make_packet_freeze(packet, mapping, *, source_manifest_sha256, t564_final_freeze_sha256, expected_packet_sha256, expected_rubric_sha256, expected_population_sha256, expected_binding_sha256, expected_mapping_sha256):
    if not all(hex64(x) for x in (source_manifest_sha256,t564_final_freeze_sha256,expected_packet_sha256,expected_rubric_sha256,expected_population_sha256,expected_binding_sha256,expected_mapping_sha256)): fail()
    data=validate_packet(packet, expected_packet_sha256=expected_packet_sha256, expected_rubric_sha256=expected_rubric_sha256, expected_population_sha256=expected_population_sha256, expected_binding_sha256=expected_binding_sha256)
    validate_mapping(mapping,packet)
    if digest(mapping)!=expected_mapping_sha256: fail()
    a=sum(x[0] for x in data["bodies"]); b=sum(x[1] for x in data["bodies"]); both=data["bodies"].count((True,True)); ao=data["bodies"].count((True,False)); bo=data["bodies"].count((False,True)); neither=data["bodies"].count((False,False))
    payload={"contract":CONTRACT,"version":1,"source_manifest_sha256":source_manifest_sha256,"t564_final_freeze_sha256":t564_final_freeze_sha256,
        "population_sha256":expected_population_sha256,"mapping_sha256":expected_mapping_sha256,"rubric_sha256":expected_rubric_sha256,
        "packet_sha256":expected_packet_sha256,"packet_binding_sha256":expected_binding_sha256,"rows":96,"target_rows":77,"non_target_rows":19,
        "side_a_body_rows":a,"side_b_body_rows":b,"both_body_rows":both,"side_a_only_body_rows":ao,"side_b_only_body_rows":bo,"neither_body_rows":neither,
        "question_opportunities":54,"question_target_rows":data["question_counts"]["TARGET"],"question_plan_missing_rows":data["question_counts"]["PLAN_NOT_OBSERVED"],"question_non_message_rows":data["question_counts"]["NOT_MESSAGE_DOMAIN"]}
    return {**payload,"freeze_sha256":digest(payload)}


def validate_packet_freeze(freeze, packet, mapping, *, expected_freeze_sha256, source_manifest_sha256, t564_final_freeze_sha256, expected_packet_sha256, expected_rubric_sha256, expected_population_sha256, expected_binding_sha256, expected_mapping_sha256):
    rebuilt=make_packet_freeze(packet,mapping,source_manifest_sha256=source_manifest_sha256,t564_final_freeze_sha256=t564_final_freeze_sha256,expected_packet_sha256=expected_packet_sha256,expected_rubric_sha256=expected_rubric_sha256,expected_population_sha256=expected_population_sha256,expected_binding_sha256=expected_binding_sha256,expected_mapping_sha256=expected_mapping_sha256)
    if freeze!=rebuilt or freeze.get("freeze_sha256")!=expected_freeze_sha256: fail()
    return rebuilt


def validate_annotation(annotation, packet, freeze, *, evaluator_identity, expected_evaluator_identity_sha256):
    if type(evaluator_identity) is not str or not evaluator_identity or digest(evaluator_identity)!=expected_evaluator_identity_sha256: fail()
    if type(annotation) is not dict or set(annotation)!={"contract","packet_sha256","rubric_sha256","packet_freeze_sha256","rows"} or annotation["contract"]!=ANNOTATION_CONTRACT: fail()
    if annotation["packet_sha256"]!=digest(packet) or annotation["rubric_sha256"]!=packet["rubric_sha256"] or annotation["packet_freeze_sha256"]!=freeze["freeze_sha256"]: fail()
    if type(annotation["rows"]) is not list or len(annotation["rows"])!=96: fail()
    for prow,arow in zip(packet["rows"],annotation["rows"]):
        if type(arow) is not dict or set(arow)!={"blind_id","status","metrics"} or arow["blind_id"]!=prow["blind_id"]: fail()
        target=prow["population_status"]=="TARGET"
        if arow["status"] not in ("ANNOTATED","NOT_REQUIRED") or target != (arow["status"]=="ANNOTATED") or (not target and arow["metrics"]!=[]): fail()
        if not target: continue
        if type(arow["metrics"]) is not list or [m.get("metric") for m in arow["metrics"]]!=list(METRICS): fail()
        for metric in arow["metrics"]:
            if type(metric) is not dict or set(metric)!={"metric","side_a","side_b"}: fail()
            for side,side_packet in ((metric["side_a"],prow["side_observations"][0]),(metric["side_b"],prow["side_observations"][1])):
                if type(side) is not dict or set(side)!={"value","reason","cited_observation_paths"} or side["value"] not in VALUES or not ids(side["cited_observation_paths"]) or any(p not in PATHS for p in side["cited_observation_paths"]): fail()
                allowed={"PASS":POSITIVE[metric["metric"]],"FAIL":NEGATIVE[metric["metric"]],"UNKNOWN":"CONTEXT_INSUFFICIENT","MEASUREMENT_NOT_OBSERVED":"OBSERVATION_MISSING","NOT_APPLICABLE":"METRIC_NOT_APPLICABLE"}
                if side["reason"]!=allowed[side["value"]]: fail()
                observed=side_packet["observation"] is not None and side_packet["observation"]["text_observation"]=="OBSERVED"
                if not observed and side["value"]!="MEASUREMENT_NOT_OBSERVED": fail()
                if metric["metric"]=="BODY_ANSWER" and not prow["question_opportunity"] and side["value"] not in ("NOT_APPLICABLE","MEASUREMENT_NOT_OBSERVED"): fail()
    return deepcopy(annotation)


def freeze_annotation(annotation, packet, packet_freeze, mapping, *, evaluator_identity, expected_evaluator_identity_sha256, expected_packet_freeze_sha256, source_manifest_sha256, t564_final_freeze_sha256, expected_packet_sha256, expected_rubric_sha256, expected_population_sha256, expected_binding_sha256, expected_mapping_sha256):
    validate_packet_freeze(packet_freeze,packet,mapping,expected_freeze_sha256=expected_packet_freeze_sha256,source_manifest_sha256=source_manifest_sha256,t564_final_freeze_sha256=t564_final_freeze_sha256,expected_packet_sha256=expected_packet_sha256,expected_rubric_sha256=expected_rubric_sha256,expected_population_sha256=expected_population_sha256,expected_binding_sha256=expected_binding_sha256,expected_mapping_sha256=expected_mapping_sha256)
    validate_annotation(annotation,packet,packet_freeze,evaluator_identity=evaluator_identity,expected_evaluator_identity_sha256=expected_evaluator_identity_sha256)
    pairs={metric:[f"{value}:{reason}" for value,reason in (("PASS",POSITIVE[metric]),("FAIL",NEGATIVE[metric]),("UNKNOWN","CONTEXT_INSUFFICIENT"),("MEASUREMENT_NOT_OBSERVED","OBSERVATION_MISSING"),("NOT_APPLICABLE","METRIC_NOT_APPLICABLE"))] for metric in METRICS}
    counts={side:{metric:{pair:0 for pair in pairs[metric]} for metric in METRICS} for side in ("A","B")}
    for row in annotation["rows"]:
        for metric in row["metrics"]:
            counts["A"][metric["metric"]][f'{metric["side_a"]["value"]}:{metric["side_a"]["reason"]}']+=1
            counts["B"][metric["metric"]][f'{metric["side_b"]["value"]}:{metric["side_b"]["reason"]}']+=1
    payload={"contract":ANNOTATION_CONTRACT,"version":1,"packet_sha256":digest(packet),"rubric_sha256":packet["rubric_sha256"],"packet_freeze_sha256":packet_freeze["freeze_sha256"],
        "annotation_sha256":digest(annotation),"annotation_rows_sha256":digest(annotation["rows"]),"rows":96,"annotated_rows":77,"not_required_rows":19,
        "side_a_metric_value_reason_counts":counts["A"],"side_b_metric_value_reason_counts":counts["B"],
        "both_body_rows":packet_freeze["both_body_rows"],"side_a_only_body_rows":packet_freeze["side_a_only_body_rows"],"side_b_only_body_rows":packet_freeze["side_b_only_body_rows"],"neither_body_rows":packet_freeze["neither_body_rows"],"fresh_independent_evaluator":True}
    return {**payload,"freeze_sha256":digest(payload)}


def unblind(annotation, packet, packet_freeze, annotation_freeze, mapping, *, evaluator_identity, expected_evaluator_identity_sha256, expected_annotation_freeze_sha256, expected_packet_freeze_sha256, source_manifest_sha256, t564_final_freeze_sha256, expected_packet_sha256, expected_rubric_sha256, expected_population_sha256, expected_binding_sha256, expected_mapping_sha256):
    validate_packet_freeze(packet_freeze,packet,mapping,expected_freeze_sha256=expected_packet_freeze_sha256,source_manifest_sha256=source_manifest_sha256,t564_final_freeze_sha256=t564_final_freeze_sha256,expected_packet_sha256=expected_packet_sha256,expected_rubric_sha256=expected_rubric_sha256,expected_population_sha256=expected_population_sha256,expected_binding_sha256=expected_binding_sha256,expected_mapping_sha256=expected_mapping_sha256)
    validate_annotation(annotation,packet,packet_freeze,evaluator_identity=evaluator_identity,expected_evaluator_identity_sha256=expected_evaluator_identity_sha256)
    expected=freeze_annotation(annotation,packet,packet_freeze,mapping,evaluator_identity=evaluator_identity,expected_evaluator_identity_sha256=expected_evaluator_identity_sha256,expected_packet_freeze_sha256=expected_packet_freeze_sha256,source_manifest_sha256=source_manifest_sha256,t564_final_freeze_sha256=t564_final_freeze_sha256,expected_packet_sha256=expected_packet_sha256,expected_rubric_sha256=expected_rubric_sha256,expected_population_sha256=expected_population_sha256,expected_binding_sha256=expected_binding_sha256,expected_mapping_sha256=expected_mapping_sha256)
    if annotation_freeze!=expected or annotation_freeze.get("freeze_sha256")!=expected_annotation_freeze_sha256: fail()
    condition_counts={condition:{metric:{value:0 for value in VALUES} for metric in METRICS} for condition in ("CONTROL","CANDIDATE")}
    pair_counts={metric:{key:0 for key in ("IMPROVED","REGRESSED","UNCHANGED","COMPARISON_UNKNOWN","NOT_APPLICABLE")} for metric in METRICS}
    body={"both":0,"control_only":0,"candidate_only":0,"neither":0}; control_observed=0
    question={condition:{value:0 for value in ("PASS","FAIL","UNKNOWN","MEASUREMENT_NOT_OBSERVED")} for condition in ("CONTROL","CANDIDATE")}
    for prow,arow,mrow in zip(packet["rows"],annotation["rows"],mapping["rows"]):
        if prow["population_status"]!="TARGET":
            if prow["question_opportunity"]:
                for condition in question: question[condition]["MEASUREMENT_NOT_OBSERVED"]+=1
            continue
        side_by_condition={mrow["A_condition"]:0,mrow["B_condition"]:1}
        observed={condition: prow["side_observations"][index]["observation"] is not None and prow["side_observations"][index]["observation"]["text_observation"]=="OBSERVED" for condition,index in side_by_condition.items()}
        control_observed+=observed["CONTROL"]
        body["both" if all(observed.values()) else "control_only" if observed["CONTROL"] else "candidate_only" if observed["CANDIDATE"] else "neither"]+=1
        for metric in arow["metrics"]:
            values={condition:metric["side_a" if index==0 else "side_b"]["value"] for condition,index in side_by_condition.items()}
            for condition,value in values.items(): condition_counts[condition][metric["metric"]][value]+=1
            if metric["metric"]=="BODY_ANSWER" and prow["question_opportunity"]:
                for condition,value in values.items():
                    if value=="NOT_APPLICABLE": fail()
                    question[condition][value]+=1
            pair="NOT_APPLICABLE" if "NOT_APPLICABLE" in values.values() else "COMPARISON_UNKNOWN" if any(v in ("UNKNOWN","MEASUREMENT_NOT_OBSERVED") for v in values.values()) else "IMPROVED" if values=={"CONTROL":"FAIL","CANDIDATE":"PASS"} else "REGRESSED" if values=={"CONTROL":"PASS","CANDIDATE":"FAIL"} else "UNCHANGED"
            pair_counts[metric["metric"]][pair]+=1
    expected_body={"both":packet_freeze["both_body_rows"],"control_only":0,"candidate_only":0,"neither":packet_freeze["neither_body_rows"]}
    # A-only/B-only are mapped to condition labels row by row; their sum must be preserved.
    if control_observed!=69 or sum(body.values())!=77 or body["both"]!=expected_body["both"] or body["neither"]!=expected_body["neither"] or body["control_only"]+body["candidate_only"]!=packet_freeze["side_a_only_body_rows"]+packet_freeze["side_b_only_body_rows"]: fail()
    if any(sum(counts.values())!=54 for counts in question.values()): fail()
    return {"contract":CONTRACT,"rows":96,"target_rows":77,"non_target_rows":19,"control_observed_rows":control_observed,"control_missing_rows":8,"body_partition":body,"condition_metric_counts":condition_counts,"pair_outcome_counts":pair_counts,"question_denominator":54,"question_counts":question}


def write_once(path,value):
    try:
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with Path(path).open("xb") as stream: stream.write(wire(value))
    except OSError: raise RubricInputError() from None
