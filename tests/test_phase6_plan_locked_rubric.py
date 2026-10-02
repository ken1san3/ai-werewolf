from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from tests.fixtures import phase6_plan_locked_rubric as r


H = "1" * 64


def _intent(i):
    value={"case_alias":f"case_{i:032x}","act":"QUESTION" if i<45 else "NONE","subject":"p1","reply":None,
           "public_trigger":{"kind":"INITIAL_CHAT","day":1,"phase":"DAY","source":None},
           "current_public_state":{"alive_player_ids":["p1","p2"],"day":1,"phase":"DAY","players":[{"player_id":"p1","alive":True,"death":None},{"player_id":"p2","alive":True,"death":None}],"vote_candidate_player_ids":["p2"]}}
    value["observation_sha256"]=r.digest(value); return value


def _message(i, observed):
    value={"result_status":"ACCEPTED" if observed else "MISSING_RESPONSE","text_observation":"OBSERVED" if observed else "NOT_OBSERVED","text":f"message {i}" if observed else None,
           "text_binding":{"parsed_result_sha256":H,"member_sha256":"2"*64,"locator_sha256":"3"*64} if observed else None}
    value["observation_sha256"]=r.digest(value); return value


def bundle():
    rows=[]; mappings=[]
    for i in range(96):
        status="TARGET" if i<77 else "PLAN_NOT_OBSERVED" if i<84 else "NOT_MESSAGE_DOMAIN"
        question=i<45 or 77<=i<81 or 84<=i<89
        if status=="TARGET":
            control=i<69; candidate=i<60 or 69<=i<73
            observations=[_message(i,control),_message(i,candidate)]
            if i%2: observations.reverse(); acond="CANDIDATE"
            else: acond="CONTROL"
            sides=[{"side":"A","observation":observations[0]},{"side":"B","observation":observations[1]}]
            chosen=_intent(i)
        else:
            sides=[{"side":"A","observation":None},{"side":"B","observation":None}]; chosen=None; acond="CONTROL"
        blind=f"blind_{i:03}"
        rows.append({"blind_id":blind,"population_status":status,"side_observations":sides,"chosen_intent":chosen,"question_opportunity":question})
        m={"blind_id":blind,"A_condition":acond,"B_condition":"CONTROL" if acond=="CANDIDATE" else "CANDIDATE"}
        m["condition_permutation_sha256"]=r.digest(m); mappings.append(m)
    packet={"contract":r.CONTRACT,"rubric_sha256":"4"*64,"population_sha256":"5"*64,"packet_binding_sha256":"6"*64,"rows":rows}
    mapping={"contract":r.CONTRACT,"rows":mappings}
    freeze=r.make_packet_freeze(packet,mapping,source_manifest_sha256="7"*64,t564_final_freeze_sha256="8"*64,expected_packet_sha256=r.digest(packet),expected_rubric_sha256="4"*64,expected_population_sha256="5"*64,expected_binding_sha256="6"*64,expected_mapping_sha256=r.digest(mapping))
    annotation={"contract":r.ANNOTATION_CONTRACT,"packet_sha256":r.digest(packet),"rubric_sha256":"4"*64,"packet_freeze_sha256":freeze["freeze_sha256"],"rows":[]}
    for row in rows:
        if row["population_status"]!="TARGET": annotation["rows"].append({"blind_id":row["blind_id"],"status":"NOT_REQUIRED","metrics":[]}); continue
        metrics=[]
        for metric in r.METRICS:
            sides=[]
            for source in row["side_observations"]:
                if source["observation"] is None or source["observation"]["text_observation"]=="NOT_OBSERVED": value="MEASUREMENT_NOT_OBSERVED"
                elif metric=="BODY_ANSWER" and not row["question_opportunity"]: value="NOT_APPLICABLE"
                elif metric=="COPY_INDEPENDENCE" and int(row["blind_id"][-3:])==0: value="UNKNOWN"
                else: value="PASS"
                reason={"PASS":r.POSITIVE[metric],"UNKNOWN":"CONTEXT_INSUFFICIENT","MEASUREMENT_NOT_OBSERVED":"OBSERVATION_MISSING","NOT_APPLICABLE":"METRIC_NOT_APPLICABLE"}[value]
                sides.append({"value":value,"reason":reason,"cited_observation_paths":[] if value in ("MEASUREMENT_NOT_OBSERVED","NOT_APPLICABLE") else ["current_public_state"]})
            metrics.append({"metric":metric,"side_a":sides[0],"side_b":sides[1]})
        annotation["rows"].append({"blind_id":row["blind_id"],"status":"ANNOTATED","metrics":metrics})
    return packet,mapping,freeze,annotation


def proof(packet,mapping,freeze):
    return {"evaluator_identity":"fresh-reviewer","expected_evaluator_identity_sha256":r.digest("fresh-reviewer"),"expected_packet_freeze_sha256":freeze["freeze_sha256"],"source_manifest_sha256":"7"*64,"t564_final_freeze_sha256":"8"*64,"expected_packet_sha256":r.digest(packet),"expected_rubric_sha256":"4"*64,"expected_population_sha256":"5"*64,"expected_binding_sha256":"6"*64,"expected_mapping_sha256":r.digest(mapping)}


def test_full_96_freeze_annotation_and_unblind():
    packet,mapping,freeze,annotation=bundle()
    assert (freeze["target_rows"],freeze["non_target_rows"],freeze["question_opportunities"])==(77,19,54)
    assert freeze["both_body_rows"]+freeze["side_a_only_body_rows"]+freeze["side_b_only_body_rows"]+freeze["neither_body_rows"]==77
    kwargs=proof(packet,mapping,freeze)
    af=r.freeze_annotation(annotation,packet,freeze,mapping,**kwargs)
    result=r.unblind(annotation,packet,freeze,af,mapping,expected_annotation_freeze_sha256=af["freeze_sha256"],**kwargs)
    assert result["control_observed_rows"]==69 and result["control_missing_rows"]==8
    assert sum(result["body_partition"].values())==77
    assert all(sum(x.values())==54 for x in result["question_counts"].values())
    # Candidate-only rows cannot be classified as improvements.
    assert result["body_partition"]["candidate_only"]==4


@pytest.mark.parametrize("mutation",[
    lambda p: p["rows"].append(deepcopy(p["rows"][0])),
    lambda p: p["rows"][0].update(blind_id=p["rows"][1]["blind_id"]),
    lambda p: p["rows"][0]["chosen_intent"].update(observation_sha256=H),
    lambda p: p["rows"][0]["side_observations"][0]["observation"].update(text_observation="NOT_OBSERVED"),
    lambda p: p["rows"][0].update(question_opportunity=None),
])
def test_packet_strict_negatives(mutation):
    packet,mapping,_,_=bundle(); mutation(packet)
    with pytest.raises(r.RubricInputError):
        r.validate_packet(packet,expected_packet_sha256=r.digest(packet),expected_rubric_sha256="4"*64,expected_population_sha256="5"*64,expected_binding_sha256="6"*64)


def test_mapping_and_annotation_negatives():
    packet,mapping,freeze,annotation=bundle()
    bad=deepcopy(mapping); bad["rows"][0]["blind_id"]="cross"
    with pytest.raises(r.RubricInputError): r.validate_mapping(bad,packet)
    bad=deepcopy(annotation); bad["rows"][0]["metrics"][0]["side_a"]["reason"]="FREE_TEXT"
    with pytest.raises(r.RubricInputError): r.validate_annotation(bad,packet,freeze,evaluator_identity="fresh-reviewer",expected_evaluator_identity_sha256=r.digest("fresh-reviewer"))
    bad=deepcopy(annotation); bad["rows"][0]["metrics"][0]["side_a"]={"value":"PASS","reason":r.POSITIVE[r.METRICS[0]],"cited_observation_paths":["private"]}
    with pytest.raises(r.RubricInputError): r.validate_annotation(bad,packet,freeze,evaluator_identity="fresh-reviewer",expected_evaluator_identity_sha256=r.digest("fresh-reviewer"))


def test_external_freeze_roots_reject_self_consistent_tamper():
    packet,mapping,freeze,annotation=bundle(); kwargs=proof(packet,mapping,freeze)
    for field in ("source_manifest_sha256","mapping_sha256","side_a_only_body_rows","question_target_rows"):
        bad=deepcopy(freeze); bad[field]="9"*64 if field.endswith("sha256") else bad[field]+1
        payload={k:v for k,v in bad.items() if k!="freeze_sha256"}; bad["freeze_sha256"]=r.digest(payload)
        with pytest.raises(r.RubricInputError): r.freeze_annotation(annotation,packet,bad,mapping,**kwargs)


@pytest.mark.parametrize("path,value",[("alias","case_"+"G"*32),("trigger_phase",""),("state_phase","")])
def test_chosen_intent_exact_strings(path,value):
    packet,_,_,_=bundle(); intent=packet["rows"][0]["chosen_intent"]
    if path=="alias": intent["case_alias"]=value
    elif path=="trigger_phase": intent["public_trigger"]["phase"]=value
    else: intent["current_public_state"]["phase"]=value
    intent["observation_sha256"]=r.digest({k:intent[k] for k in ("case_alias","act","subject","reply","public_trigger","current_public_state")})
    with pytest.raises(r.RubricInputError): r.validate_intent(intent)


def test_missing_unknown_and_na_remain_distinct():
    packet,mapping,freeze,annotation=bundle()
    missing=annotation["rows"][60]["metrics"][0]["side_a"]
    assert missing["value"] in ("PASS","MEASUREMENT_NOT_OBSERVED")
    values={side["value"] for metric in annotation["rows"][0]["metrics"] for side in (metric["side_a"],metric["side_b"])}
    assert "UNKNOWN" in values
    assert any("NOT_APPLICABLE" in (m["side_a"]["value"],m["side_b"]["value"]) for m in annotation["rows"][70]["metrics"])


def test_strict_json_and_create_only():
    with pytest.raises(r.RubricInputError): r.parse('{"x":1,"x":2}')
    with pytest.raises(r.RubricInputError): r.parse('{"x":NaN}')
    with pytest.raises(r.RubricInputError): r.wire("\ud800")
    target=Path(".tmp")/f"t570-{uuid4().hex}.json"
    try:
        r.write_once(target,{"ok":True})
        with pytest.raises(r.RubricInputError): r.write_once(target,{"ok":False})
    finally:
        target.unlink(missing_ok=True)
