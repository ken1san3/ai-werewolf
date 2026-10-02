from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import random
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import phase6_resolved_subject_probe as probe
from scripts.phase6_resolved_subject_probe import build_main_pin
from tests.fixtures import phase6_plan_locked_rubric as rubric
from tests.fixtures import phase6_saved_control_plan_locked as saved
from tests.fixtures.phase6_saved_control_plan_locked_custody import reconstruct_saved_control_authority


RUBRIC_SHA = "4" * 64
EVALUATOR = "fresh-synthetic-evaluator"


_NATIVE_CACHE = None


def _native():
    global _NATIVE_CACHE
    if _NATIVE_CACHE is None:
        fixtures = probe.q.prepare()[0]
        native = probe.parse(probe.q.wire([dict(case_id=f.case.case_id, source=f.source,
            projection_sha256=probe.q.digest(f.source_bytes), catalog=asdict(f.catalog), bindings=f.bindings) for f in fixtures]))
        _NATIVE_CACHE = (fixtures, native)
    return _NATIVE_CACHE


@pytest.fixture
def workdir():
    path = Path(".t575-work") / uuid4().hex
    path.mkdir(parents=True)
    return path


@pytest.fixture(scope="module")
def base_bundle():
    path = Path(".t575-work") / ("base-" + uuid4().hex)
    path.mkdir(parents=True)
    return _packet_bundle(path)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(probe.wire(value) if not isinstance(value, bytes) else value)


def _public_fixture(workdir):
    owned = workdir / "owned"; source_root = owned / "source"; sources = source_root / "sources"
    sources.mkdir(parents=True)
    fixtures, native_fixtures = _native()
    rows=[]; fixture_indexes={}; eligible=missing=nonchat=0
    for fixture_index, fixture in enumerate(fixtures):
        candidates = ([probe.q.product.parse_and_validate_generation_v2_candidate_structure("chat_plan", raw, fixture.catalog)
                       for raw in probe.q.witnesses("chat_plan", fixture)] if fixture.stage == "chat_plan" else [])
        for seed in probe.q.SEEDS:
            if fixture.stage != "chat_plan":
                row={"row_id":f"nonchat-{nonchat}","case_id":fixture.case.case_id,"seed":seed,"status":"NOT_MESSAGE_DOMAIN",
                     "plan":None,"control_ordinal1":None,"saved_ordinal2":None,"control_text":None,
                     "question_opportunity":nonchat < 5}; nonchat+=1
            elif missing < 7:
                row={"row_id":f"missing-{missing}","case_id":fixture.case.case_id,"seed":seed,"status":"PLAN_NOT_OBSERVED",
                     "plan":None,"control_ordinal1":None,"saved_ordinal2":None,"control_text":None,
                     "question_opportunity":missing < 4}; missing+=1
            else:
                candidate=next(value for value in candidates if value.value["subject_player_id"] is not None)
                plan={key:list(value) if type(value) is tuple else value for key,value in candidate.value.items()}
                row={"row_id":f"eligible-{eligible}","case_id":fixture.case.case_id,"seed":seed,"status":"READY","plan":plan,
                     "control_ordinal1":"{}","saved_ordinal2":"{}" if eligible<11 else None,
                     "control_text":f"public synthetic control {eligible}" if eligible<69 else None,
                     "question_opportunity":eligible < 45}; eligible+=1
            rows.append(row); fixture_indexes[row["row_id"]]=fixture_index
    assert (len(rows),eligible,missing,nonchat,sum(r["question_opportunity"] for r in rows))==(96,77,7,12,54)
    # Freeze the exact JSON representation before any pin/member digest is made.
    rows = rubric.parse(rubric.wire(rows))

    files=[]
    for index,role in enumerate(sorted(probe.SOURCE_ROLES)):
        source_id=f"source_{index:02d}"; raw=("public-synthetic:"+role).encode(); _write(sources/source_id,raw)
        files.append({"source_id":source_id,"role":role,"original_name":role+".json","sha256":probe.digest(raw),"size":len(raw)})
    entries={entry["role"]:entry for entry in files}
    members={}; requests=[]; plans=[]; outcomes=[]; fixture_rows=[]; saved_records=[]
    for row in rows:
        rid=row["row_id"]; index=fixture_indexes[rid]; native_fixture=native_fixtures[index]
        fixture_rows.append({"row_id":rid,"case_id":row["case_id"],"seed":row["seed"],"fixture_index":index,
            "projection_sha256":native_fixture["projection_sha256"],"source_sha256":probe.digest(native_fixture["source"]),
            "bindings_sha256":probe.digest(native_fixture["bindings"]),"catalog_sha256":probe.digest(native_fixture["catalog"])})
        value={"case_id":row["case_id"],"elapsed":1.0,
            "final":({"decision":"DECLARE","co_option_id":"co1"} if row["status"]=="NOT_MESSAGE_DOMAIN" else
                     None if row["control_text"] is None else {"message":row["control_text"]}),
            "plan":row["plan"],"projection_sha256":native_fixture["projection_sha256"],"sample_exhausted":False,
            "seed":row["seed"],"silence":False,"terminal_stage":"message"}
        name=f"actual/{rid}.json"; sha=probe.digest(value); members[name]=sha
        saved_records.append({"run_row":row,"native":{"original_name":name,"member_sha256":sha,"value":value}})
        if row["status"]=="READY":
            plans.append({"row_id":rid,"plan":row["plan"],"plan_sha256":probe.digest(row["plan"]),"original_name":name,"member_sha256":sha})
            for ordinal,field in ((1,"control_ordinal1"),(2,"saved_ordinal2")):
                if row[field] is None: continue
                request_name=f"actual/{rid}-request-{ordinal}.json"; request_sha=probe.digest(row[field].encode()); members[request_name]=request_sha
                requests.append({"row_id":rid,"ordinal":ordinal,"original_name":request_name,"member_sha256":request_sha,"wire":row[field]})
                outcome_name=f"actual/{rid}-outcome-{ordinal}.json"; outcome={"status":"ACCEPTED"}; outcome_sha=probe.digest(outcome); members[outcome_name]=outcome_sha
                outcomes.append({"row_id":rid,"ordinal":ordinal,"status":"ACCEPTED","original_name":outcome_name,"member_sha256":outcome_sha,"value":outcome})
    fixture_wire=probe.wire(native_fixtures).decode(); fixture_name="fixture-inputs.json"; members[fixture_name]=probe.digest(fixture_wire.encode())
    docs={
        "saved_rows":{"contract":probe.CONTRACT,"rows":saved_records},
        "saved_requests":{"contract":probe.CONTRACT,"requests":requests},
        "saved_plans":{"contract":probe.CONTRACT,"plans":plans},
        "saved_outcomes":{"contract":probe.CONTRACT,"outcomes":outcomes},
        "fixture_inputs":{"contract":probe.CONTRACT,"source":{"original_name":fixture_name,"member_sha256":members[fixture_name],"wire":fixture_wire},"fixtures":fixture_rows},
        "t550_manifest":{"contract":probe.CONTRACT,"members":[{"original_name":key,"sha256":members[key]} for key in sorted(members)]},
        "t550_seal":dict(sorted(members.items())),
    }
    identity={"profile":b"public-profile","config":b"public-config","model_metadata":b"public-model",
              "design_approval":b"public-design","task_packet":b"public-task",
              "runner_source":probe.Path(probe.runner.__file__).read_bytes(),
              "probe_source":probe.Path(probe.q.__file__).read_bytes(),"product_source":probe.Path(probe.q.product.__file__).read_bytes()}
    for role,entry in entries.items():
        raw=probe.wire(docs[role]) if role in docs else identity[role]
        _write(sources/entry["source_id"],raw); entry.update(sha256=probe.digest(raw),size=len(raw))
    manifest_payload={"contract":probe.CONTRACT,"files":files}; manifest={**manifest_payload,"manifest_sha256":probe.digest(manifest_payload)}
    code_sha=probe.digest(probe.Path(probe.__file__).read_bytes())
    preflight={"contract":probe.CONTRACT,"rows":96,"eligible":77,"plan_missing":7,"non_chat":12,"saved_requests":88,
        "ordinal1":77,"ordinal2":11,"max_generation_calls":154,"budget":237,"context":8192,
        "source_manifest_sha256":manifest["manifest_sha256"],"code_sha256":code_sha,
        "profile_sha256":probe.digest(identity["profile"]),"config_sha256":probe.digest(identity["config"]),
        "model_sha256":probe.digest(identity["model_metadata"]),"runtime_identity_sha256":"9"*64,
        "old_run_id":"PUBLIC_OLD","old_seal_sha256":entries["t550_seal"]["sha256"],"new_run_id":"PUBLIC_NEW",
        "design_sha256":probe.digest(identity["design_approval"]),"approval_bundle_sha256":probe.digest({"design_sha256":probe.digest(identity["design_approval"]),"task_packet_sha256":probe.digest(identity["task_packet"])})}
    preflight["preflight_sha256"]=probe.digest(preflight)
    pin=build_main_pin(rows,manifest["manifest_sha256"],code_sha)
    bundle={"contract":probe.CONTRACT,"preflight":preflight,"source_manifest":manifest,"main_pin":pin,"rows":rows}
    paths={"input":owned/"input.json","preflight":owned/"preflight.json","source_manifest":owned/"source-manifest.json",
           "main_pin":owned/"main-pin.json","old_t550_seal":owned/"old-seal.json"}
    for key,value in (("input",bundle),("preflight",preflight),("source_manifest",manifest),("main_pin",pin),("old_t550_seal",docs["t550_seal"])): _write(paths[key],value)
    locator={"contract":"PHASE6_SAVED_CONTROL_PREPARED_LOCATOR_V1","input":"input.json","preflight":"preflight.json",
             "source_manifest":"source-manifest.json","main_pin":"main-pin.json","old_t550_seal":"old-seal.json","source_root":"source"}
    locator_path=owned/"locator.json"; _write(locator_path,locator)
    code_bundle=saved.code_bundle()
    external={"expected_prepared_locator_file_sha256":saved.digest(locator_path.read_bytes()),
        "expected_input_file_sha256":saved.digest(paths["input"].read_bytes()),"expected_preflight_file_sha256":saved.digest(paths["preflight"].read_bytes()),
        "expected_source_manifest_file_sha256":saved.digest(paths["source_manifest"].read_bytes()),"expected_main_pin_file_sha256":saved.digest(paths["main_pin"].read_bytes()),
        "expected_old_t550_seal_file_sha256":saved.digest(paths["old_t550_seal"].read_bytes()),"expected_code_bundle_sha256":code_bundle["bundle_sha256"]}
    authority,population=reconstruct_saved_control_authority(owned,locator_path,**external)
    return owned,locator_path,authority,population,manifest,external


def _packet_bundle(workdir):
    owned,locator,authority,population,manifest,external=_public_fixture(workdir)
    packet,view,mapping,binding=saved.build_saved_control_packet(owned,locator,authority,
        expected_authority_pin_sha256=authority["pin_sha256"],expected_source_roles_root_sha256=authority["source_roles_root_sha256"],
        expected_saved_control_members_root_sha256=authority["saved_control_members_root_sha256"],
        expected_chosen_intent_bindings_root_sha256=authority["chosen_intent_bindings_root_sha256"],
        expected_fixture_bindings_root_sha256=authority["fixture_bindings_root_sha256"],
        expected_population_root_sha256=authority["expected_population_root_sha256"],expected_code_bundle_sha256=external["expected_code_bundle_sha256"],
        expected_rubric_sha256=RUBRIC_SHA,rng=random.Random(575))
    expected={"expected_authority_pin_sha256":authority["pin_sha256"],"expected_source_manifest_sha256":manifest["manifest_sha256"],
        "expected_population_sha256":authority["expected_population_root_sha256"],"expected_mapping_sha256":mapping["mapping_sha256"],
        "expected_rubric_sha256":RUBRIC_SHA,"expected_source_packet_sha256":saved.digest(packet),
        "expected_packet_binding_sha256":binding["packet_binding_sha256"],"expected_semantic_packet_view_sha256":saved.digest(view)}
    freeze=saved.freeze_saved_control_packet(packet,view,mapping,binding,**expected)
    return packet,view,mapping,binding,freeze,expected,authority,population,external,owned,locator


def _annotation(view,freeze):
    result={"contract":rubric.ANNOTATION_CONTRACT,"packet_sha256":saved.digest(view),"rubric_sha256":RUBRIC_SHA,
            "packet_freeze_sha256":freeze["freeze_sha256"],"rows":[]}
    for row in view["rows"]:
        if row["population_status"]!="TARGET":
            result["rows"].append({"blind_id":row["blind_id"],"status":"NOT_REQUIRED","metrics":[]}); continue
        metrics=[]
        for metric in rubric.METRICS:
            sides=[]
            for source in row["side_observations"]:
                if source["observation"] is None: value="MEASUREMENT_NOT_OBSERVED"
                elif metric=="BODY_ANSWER" and not row["question_opportunity"]: value="NOT_APPLICABLE"
                elif metric=="COPY_INDEPENDENCE" and row["blind_id"].endswith("0"): value="UNKNOWN"
                else: value="PASS"
                reason={"PASS":rubric.POSITIVE[metric],"UNKNOWN":"CONTEXT_INSUFFICIENT","MEASUREMENT_NOT_OBSERVED":"OBSERVATION_MISSING","NOT_APPLICABLE":"METRIC_NOT_APPLICABLE"}[value]
                sides.append({"value":value,"reason":reason,"cited_observation_paths":[] if value in ("MEASUREMENT_NOT_OBSERVED","NOT_APPLICABLE") else ["current_public_state"]})
            metrics.append({"metric":metric,"side_a":sides[0],"side_b":sides[1]})
        result["rows"].append({"blind_id":row["blind_id"],"status":"ANNOTATED","metrics":metrics})
    return result


def test_public_native_96_end_to_end(base_bundle):
    packet,view,mapping,binding,freeze,expected,authority,population,_,_,_= base_bundle
    assert (len(population["rows"]),freeze["target_rows"],freeze["non_target_rows"],freeze["control_observed_rows"],freeze["control_missing_rows"],freeze["question_opportunities"])==(96,77,19,69,8,54)
    assert packet["rows"]==view["rows"] and "mode" not in view and all("condition" not in str(row) for row in view["rows"])
    annotation=_annotation(view,freeze); identity_sha=saved.digest(EVALUATOR)
    af=saved.freeze_saved_control_annotation(annotation,packet,view,mapping,freeze,evaluator_identity=EVALUATOR,
        expected_evaluator_identity_sha256=identity_sha,expected_packet_freeze_sha256=freeze["freeze_sha256"],**expected)
    summary=saved.unblind_saved_control(annotation,packet,view,mapping,freeze,af,evaluator_identity=EVALUATOR,
        expected_evaluator_identity_sha256=identity_sha,expected_annotation_freeze_sha256=af["freeze_sha256"],
        expected_packet_freeze_sha256=freeze["freeze_sha256"],**expected)
    assert summary["question_denominator"]==54 and sum(summary["question_counts"].values())==54
    assert "pair_outcome_counts" not in summary and "candidate" not in json.dumps(summary).lower()


def test_physical_external_pin_rejects_self_consistent_member_change(base_bundle,workdir):
    *_,external,base_owned,_=base_bundle
    owned=workdir/"owned"; shutil.copytree(base_owned,owned); locator=owned/"locator.json"
    source_manifest=json.loads((owned/"source-manifest.json").read_text())
    saved_entry=next(x for x in source_manifest["files"] if x["role"]=="saved_rows")
    path=owned/"source"/"sources"/saved_entry["source_id"]; doc=json.loads(path.read_text())
    doc["rows"][0]["native"]["value"]["elapsed"]=2.0
    raw=rubric.wire(doc); path.write_bytes(raw); saved_entry.update(sha256=saved.digest(raw),size=len(raw))
    source_manifest["manifest_sha256"]=saved.digest({"contract":probe.CONTRACT,"files":source_manifest["files"]})
    _write(owned/"source-manifest.json",source_manifest)
    with pytest.raises(saved.SavedControlInputError): reconstruct_saved_control_authority(owned,locator,**external)


@pytest.mark.parametrize("kind",("mapping_condition","mapping_order","view","packet","annotation_packet","identity","mno_pass"))
def test_packet_mapping_annotation_external_negatives(base_bundle,kind):
    packet,view,mapping,binding,freeze,expected,*_= base_bundle
    annotation=_annotation(view,freeze); identity_sha=saved.digest(EVALUATOR)
    if kind.startswith("mapping"):
        bad=deepcopy(mapping)
        if kind=="mapping_condition": bad["rows"][0].update(A_condition="CONTROL",B_condition="CONTROL")
        else: bad["rows"][0],bad["rows"][1]=bad["rows"][1],bad["rows"][0]
        payload={k:bad[k] for k in bad if k!="mapping_sha256"}; bad["mapping_sha256"]=saved.digest(payload)
        with pytest.raises(saved.SavedControlInputError): saved.validate_saved_control_mapping(packet,bad,expected_source_packet_sha256=saved.digest(packet),expected_mapping_sha256=mapping["mapping_sha256"])
    elif kind in ("view","packet"):
        bad_view=deepcopy(view); bad_packet=deepcopy(packet)
        target=bad_view if kind=="view" else bad_packet; target["rows"][0]["question_opportunity"]=not target["rows"][0]["question_opportunity"]
        with pytest.raises(saved.SavedControlInputError): saved.freeze_saved_control_packet_from_frozen_inputs(bad_packet,bad_view,mapping,freeze,expected_packet_freeze_sha256=freeze["freeze_sha256"],**expected)
    else:
        bad=deepcopy(annotation); evaluator=EVALUATOR; evaluator_sha=identity_sha
        if kind=="annotation_packet": bad["packet_sha256"]=saved.digest(packet)
        elif kind=="identity": evaluator="same-author"
        else:
            prow=next(i for i,row in enumerate(view["rows"]) if row["population_status"]=="TARGET")
            mrow=mapping["rows"][prow]; key="side_a" if mrow["A_condition"]=="NOT_MEASURED" else "side_b"
            metric_row=bad["rows"][prow]["metrics"][0]; metric=metric_row[key]
            metric.update(value="PASS",reason=rubric.POSITIVE[metric_row["metric"]],cited_observation_paths=["current_public_state"])
        with pytest.raises((saved.SavedControlInputError,rubric.RubricInputError)):
            saved.freeze_saved_control_annotation(bad,packet,view,mapping,freeze,evaluator_identity=evaluator,
                expected_evaluator_identity_sha256=evaluator_sha,expected_packet_freeze_sha256=freeze["freeze_sha256"],**expected)


def test_strict_mapping_and_no_measured_body_or_nonfinite(base_bundle):
    packet,view,mapping,binding,freeze,expected,*_= base_bundle
    bad=deepcopy(packet); row=next(i for i,m in enumerate(mapping["rows"]) if m["A_condition"]=="NOT_MEASURED")
    bad["rows"][row]["side_observations"][0]["observation"]=deepcopy(view["rows"][row]["side_observations"][1]["observation"])
    with pytest.raises(saved.SavedControlInputError): saved.freeze_saved_control_packet_from_frozen_inputs(bad,view,mapping,freeze,expected_packet_freeze_sha256=freeze["freeze_sha256"],**expected)
    with pytest.raises(rubric.RubricInputError): rubric.wire(float("nan"))
