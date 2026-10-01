from __future__ import annotations

import hashlib
import math

import pytest

from tests.fixtures.phase6_s4_common_provenance import (
    RUBRIC, S4InputError, canonical_sha, canonical_wire, decide_s4,
    project_common_provenance, strict_parse,
)


def atom(value):
    return {"tag": "ABSENT", "value": None} if value is None else {"tag": "VALUE", "value": value}


def identity(order=1):
    return {"record_kind": "ability_result", "order": order, "visibility": "PUBLIC"}


def inputs(*, surface=None, plan=None, items=None, binding_changes=None, records=None, extra_bindings=(), bindings_override=None):
    surface = surface or {"kind": "TEXT", "text": "result", "claims": []}
    raw = canonical_wire(surface)
    plan = plan or {"kind": "CHAT_PLAN", "fact_ids": [], "disclose_ids": ["d1"], "claim_id": None}
    edge = {"binding_id": "b1", "selected_id": "d1", "source_kind": "ABILITY_RESULT",
            "canonical_identity": identity(), "actor_id": "p1", "visibility": "PUBLIC",
            "authority": "INTENTIONAL_OWNER_ABILITY", "target": atom("p2"), "result": atom("wolf")}
    if binding_changes: edge.update(binding_changes)
    bindings = tuple(bindings_override) if bindings_override is not None else (edge, *extra_bindings)
    records = tuple(records if records is not None else [
        {"identity": identity(), "actor_id": "p1", "target": atom("p2"), "result": atom("wolf")}
    ])
    default_items = [{"provenance_id": "pv1", "origin": "SELECTED_DISCLOSURE",
                      "selected_id": "d1", "explicit_ref": None, "binding_id": "b1"}]
    items = default_items if items is None else items
    surface_sha = hashlib.sha256(raw).hexdigest()
    row_sha = canonical_sha({"rubric_version": RUBRIC, "blind_id": "blind1", "surface_sha256": surface_sha})
    catalog_sha = canonical_sha(sorted(bindings, key=lambda x: x["binding_id"]))
    record_sha = canonical_sha(sorted(records, key=lambda x: (x["identity"]["record_kind"], x["identity"]["order"], x["identity"]["visibility"])))
    plan_sha = canonical_sha(plan)
    payload = {"rubric_version": RUBRIC, "blind_id": "blind1", "surface_sha256": surface_sha,
               "row_sha256": row_sha, "catalog_sha256": catalog_sha,
               "canonical_set_sha256": record_sha, "accepted_plan_sha256": plan_sha, "items": items}
    row = {"rubric_version": RUBRIC, "blind_id": "blind1", "surface_sha256": surface_sha,
           "row_sha256": row_sha, "selection_envelope_sha256": canonical_sha(payload),
           "catalog_sha256": catalog_sha, "canonical_set_sha256": record_sha}
    envelope = {"binding": row, "accepted_plan_sha256": plan_sha, "items": items}
    return dict(binding=row, accepted_surface=raw, accepted_plan=plan, envelope=envelope,
                bindings=bindings, canonical_records=records)


def semantic(binding, *, assertion="CLAIMED_RESULT", target="p2", result="wolf", associations=True):
    association = {"provenance_id": "pv1", "target": atom(target), "result": atom(result)}
    return {"binding": binding, "assertion": assertion, "cited_provenance_ids": [],
            "associations": [association] if associations else []}


EXEC = {"run_status": "COMPLETE", "structural_status": "ACCEPTED"}
AUDIT = {"sealed_audit": "COMPLETE", "public_surface": "TEXT"}
CONV = {"status": "COMPLETE"}


def project(data=None):
    data = data or inputs()
    return data, project_common_provenance(**data)


def test_canonical_wire_and_strict_parse_reject_noncanonical_inputs():
    assert canonical_wire({"あ": 1, "a": 2}) == b'{"a":2,"\xe3\x81\x82":1}'
    with pytest.raises(S4InputError, match="S4_INPUT_INTEGRITY"):
        canonical_wire({"x": math.nan})
    with pytest.raises(S4InputError, match="S4_INPUT_INTEGRITY"):
        strict_parse(b'{"kind":"TEXT","kind":"TEXT","text":"x","claims":[]}')
    with pytest.raises(S4InputError, match="S4_INPUT_INTEGRITY"):
        strict_parse(b'{"kind": "TEXT", "text":"x","claims":[]}')


def test_selected_disclosure_projects_and_matching_value_passes():
    data, mechanical = project()
    assert mechanical[0]["lane"] == "AUTHORITATIVE_ABILITY"
    decision = decide_s4(execution=EXEC, sealed_audit=AUDIT, conversion_status=CONV,
                         mechanical=mechanical, semantic=semantic(data["binding"]))
    assert (decision["metric_value"], decision["reason_code"]) == ("PASS", "AUTHORITATIVE_VALUE_MATCH")


def test_value_mismatch_and_missing_semantic_are_not_pass():
    data, mechanical = project()
    mismatch = decide_s4(execution=EXEC, sealed_audit=AUDIT, conversion_status=CONV,
                         mechanical=mechanical, semantic=semantic(data["binding"], result="human"))
    missing = decide_s4(execution=EXEC, sealed_audit=AUDIT, conversion_status=CONV,
                        mechanical=mechanical, semantic=semantic(data["binding"], associations=False))
    assert mismatch["reason_code"] == "AUTHORITATIVE_VALUE_MISMATCH"
    assert (missing["metric_value"], missing["measurement_validity"]) == ("UNKNOWN", "INVALID")


@pytest.mark.parametrize("changes", [
    {"actor_id": "other"}, {"visibility": "NON_PUBLIC"}, {"authority": "FORBIDDEN"},
])
def test_invalid_authoritative_boundary_is_unknown_not_s4_fail(changes):
    data, mechanical = project(inputs(binding_changes=changes))
    decision = decide_s4(execution=EXEC, sealed_audit=AUDIT, conversion_status=CONV,
                         mechanical=mechanical, semantic=semantic(data["binding"]))
    assert decision["reason_code"] == "AUTHORITATIVE_ASSOCIATION_UNAVAILABLE"
    assert decision["metric_value"] == "UNKNOWN"


def test_ambiguous_is_unknown_and_not_found_explicit_ref_is_forged():
    duplicate = {"identity": identity(), "actor_id": "p1", "target": atom("p2"), "result": atom("wolf")}
    data, mechanical = project(inputs(records=[duplicate, duplicate]))
    decision = decide_s4(execution=EXEC, sealed_audit=AUDIT, conversion_status=CONV,
                         mechanical=mechanical, semantic=semantic(data["binding"]))
    assert decision["reason_code"] == "AMBIGUOUS_CANONICAL_BINDING"
    surface = {"kind": "STRUCTURED", "text": None,
               "claims": [{"claim_id": "c", "explicit_refs": [identity(9)]}]}
    items = [{"provenance_id": "pvx", "origin": "EXPLICIT_SURFACE_REF", "selected_id": None,
              "explicit_ref": identity(9), "binding_id": None}]
    data, mechanical = project(inputs(surface=surface, plan={"kind":"CHAT_PLAN","fact_ids":[],"disclose_ids":[],"claim_id":None}, items=items))
    sem = {"binding": data["binding"], "assertion": "EXPLICIT_AUTHORITY_ASSERTION",
           "cited_provenance_ids": ["pvx"], "associations": []}
    assert decide_s4(execution=EXEC, sealed_audit={"sealed_audit":"COMPLETE","public_surface":"STRUCTURED"}, conversion_status=CONV, mechanical=mechanical, semantic=sem)["reason_code"] == "FORGED_REFERENCE"


@pytest.mark.parametrize("mutator", ["unselected", "text_ref", "wrong_lane", "order", "cross_row"])
def test_integrity_rejects_selection_ref_and_hash_injection(mutator):
    data = inputs()
    d2={"binding_id":"b2","selected_id":"d2","source_kind":"ABILITY_RESULT","canonical_identity":identity(2),"actor_id":"p1","visibility":"PUBLIC","authority":"INTENTIONAL_OWNER_ABILITY","target":atom("p3"),"result":atom("human")}
    if mutator == "unselected": data = inputs(items=[{"provenance_id":"pv1","origin":"SELECTED_DISCLOSURE","selected_id":"d2","explicit_ref":None,"binding_id":"b2"}],extra_bindings=(d2,))
    elif mutator == "text_ref": data = inputs(items=[{"provenance_id":"pv1","origin":"EXPLICIT_SURFACE_REF","selected_id":None,"explicit_ref":identity(),"binding_id":None}])
    elif mutator == "wrong_lane": data = inputs(binding_changes={"source_kind":"PUBLIC_FACT"})
    elif mutator == "order":
        plan={"kind":"CHAT_PLAN","fact_ids":[],"disclose_ids":["d1","d2"],"claim_id":None}
        data=inputs(plan=plan,items=[{"provenance_id":"p2","origin":"SELECTED_DISCLOSURE","selected_id":"d2","explicit_ref":None,"binding_id":"b2"},{"provenance_id":"p1","origin":"SELECTED_DISCLOSURE","selected_id":"d1","explicit_ref":None,"binding_id":"b1"}],extra_bindings=(d2,))
    else:
        data=inputs();data["binding"]={**data["binding"],"blind_id":"other"}
    with pytest.raises(S4InputError, match="S4_INPUT_INTEGRITY"):
        project_common_provenance(**data)


def test_rejected_absent_and_claimed_report():
    data, mechanical = project()
    sem=semantic(data["binding"])
    rejected=decide_s4(execution={"run_status":"RAW_RECEIVED","structural_status":"REJECTED_SCHEMA"},sealed_audit=AUDIT,conversion_status=CONV,mechanical=mechanical,semantic=sem)
    assert rejected["metric_value"] == "MEASUREMENT_NOT_OBSERVED"
    absent_data=inputs(surface={"kind":"ABSENT","text":None,"claims":[]},plan={"kind":"CHAT_PLAN","fact_ids":[],"disclose_ids":[],"claim_id":None},items=[],records=[])
    absent_mechanical=project_common_provenance(**absent_data)
    absent_sem={"binding":absent_data["binding"],"assertion":"NONE","cited_provenance_ids":[],"associations":[]}
    absent=decide_s4(execution=EXEC,sealed_audit={"sealed_audit":"COMPLETE","public_surface":"ABSENT"},conversion_status=CONV,mechanical=absent_mechanical,semantic=absent_sem)
    assert absent["metric_value"] == "PASS"
    plan={"kind":"CHAT_PLAN","fact_ids":[],"disclose_ids":[],"claim_id":"c1"}
    claim_binding={"source_kind":"CLAIM","selected_id":"c1","binding_id":"b1","canonical_identity":None,"actor_id":None,"visibility":"PUBLIC","authority":"PUBLIC"}
    claim_items=[{"provenance_id":"pv1","origin":"SELECTED_CLAIM","selected_id":"c1","explicit_ref":None,"binding_id":"b1"}]
    data, mechanical=project(inputs(plan=plan,binding_changes=claim_binding,records=[],items=claim_items))
    decision=decide_s4(execution=EXEC,sealed_audit=AUDIT,conversion_status=CONV,mechanical=mechanical,semantic=semantic(data["binding"],associations=False))
    assert (decision["metric_value"],decision["reason_code"]) == ("PASS","UNREFERENCED_CLAIMED_REPORT")


def test_semantic_sentinels_duplicates_and_cross_row_are_unknown():
    data, mechanical=project();sem=semantic(data["binding"])
    sem["associations"][0]["result"]={"tag":"UNDECIDABLE","value":None}
    assert decide_s4(execution=EXEC,sealed_audit=AUDIT,conversion_status=CONV,mechanical=mechanical,semantic=sem)["metric_value"]=="UNKNOWN"
    duplicate=semantic(data["binding"]);duplicate["associations"]*=2
    assert decide_s4(execution=EXEC,sealed_audit=AUDIT,conversion_status=CONV,mechanical=mechanical,semantic=duplicate)["reason_code"]=="S4_INPUT_INTEGRITY"
    crossed=semantic({**data["binding"],"blind_id":"other"})
    assert decide_s4(execution=EXEC,sealed_audit=AUDIT,conversion_status=CONV,mechanical=mechanical,semantic=crossed)["reason_code"]=="S4_INPUT_INTEGRITY"


def test_empty_mechanical_unreferenced_claim_is_pass_but_absent_integrity_is_checked():
    data=inputs(surface={"kind":"TEXT","text":"I claim a result","claims":[]},plan={"kind":"CHAT_PLAN","fact_ids":[],"disclose_ids":[],"claim_id":None},items=[],records=[])
    mechanical=project_common_provenance(**data)
    sem={"binding":data["binding"],"assertion":"CLAIMED_RESULT","cited_provenance_ids":[],"associations":[]}
    assert decide_s4(execution=EXEC,sealed_audit=AUDIT,conversion_status=CONV,mechanical=mechanical,semantic=sem)["reason_code"]=="UNREFERENCED_CLAIMED_REPORT"
    bad=decide_s4(execution=EXEC,sealed_audit={"sealed_audit":"COMPLETE","public_surface":"ABSENT"},conversion_status=CONV,mechanical=(),semantic=None)
    assert (bad["metric_value"],bad["reason_code"]) == ("UNKNOWN","S4_INPUT_INTEGRITY")


@pytest.mark.parametrize("mutation", ["extra","duplicate","not_selected","bad_value"])
def test_decide_validates_mechanical_closed_shape_and_ids(mutation):
    data,mechanical=project();records=[dict(mechanical[0])]
    if mutation=="extra":records[0]["extra"]=1
    elif mutation=="duplicate":records.append(dict(records[0]))
    elif mutation=="not_selected":records[0]["selection"]="NOT_SELECTED"
    else:records[0]["canonical_value"]={"target":atom("p2")}
    decision=decide_s4(execution=EXEC,sealed_audit=AUDIT,conversion_status=CONV,mechanical=tuple(records),semantic=semantic(data["binding"]))
    expected="AUTHORITATIVE_ASSOCIATION_UNAVAILABLE" if mutation=="not_selected" else "S4_INPUT_INTEGRITY"
    assert decision["reason_code"]==expected
    assert decision["metric_value"]=="UNKNOWN"


def test_unknown_authority_stays_unknown_and_nested_plan_id_is_safe_error():
    data,mechanical=project(inputs(binding_changes={"authority":"UNKNOWN"}))
    assert mechanical[0]["authority"]=="UNKNOWN"
    with pytest.raises(S4InputError,match="S4_INPUT_INTEGRITY"):
        project_common_provenance(**inputs(plan={"kind":"CHAT_PLAN","fact_ids":[{"bad":1}],"disclose_ids":[],"claim_id":None},items=[]))


def test_all_undecidable_authoritative_ids_are_reported_in_envelope_order():
    data,mechanical=project();second=dict(mechanical[0]);second["provenance_id"]="pv2"
    assocs=[]
    for pid in ("pv1","pv2"):
        assocs.append({"provenance_id":pid,"target":{"tag":"UNDECIDABLE","value":None},"result":{"tag":"UNDECIDABLE","value":None}})
    sem={"binding":data["binding"],"assertion":"CLAIMED_RESULT","cited_provenance_ids":[],"associations":assocs}
    decision=decide_s4(execution=EXEC,sealed_audit=AUDIT,conversion_status=CONV,mechanical=(mechanical[0],second),semantic=sem)
    assert decision["offending_provenance_ids"] == ("pv1","pv2")


def test_multiple_ref_failure_precedence_and_order_are_deterministic():
    surface={"kind":"STRUCTURED","text":None,"claims":[{"claim_id":"c","explicit_refs":[identity(9),identity(1)]}]}
    items=[
        {"provenance_id":"p9","origin":"EXPLICIT_SURFACE_REF","selected_id":None,"explicit_ref":identity(9),"binding_id":None},
        {"provenance_id":"p1","origin":"EXPLICIT_SURFACE_REF","selected_id":None,"explicit_ref":identity(1),"binding_id":None},
    ]
    rec={"identity":identity(1),"actor_id":"p1","target":atom("p2"),"result":atom("wolf")}
    data,mechanical=project(inputs(surface=surface,plan={"kind":"CHAT_PLAN","fact_ids":[],"disclose_ids":[],"claim_id":None},items=items,records=[rec,rec]))
    sem={"binding":data["binding"],"assertion":"EXPLICIT_AUTHORITY_ASSERTION","cited_provenance_ids":["p9","p1"],"associations":[]}
    decision=decide_s4(execution=EXEC,sealed_audit={"sealed_audit":"COMPLETE","public_surface":"STRUCTURED"},conversion_status=CONV,mechanical=mechanical,semantic=sem)
    assert decision["reason_code"]=="FORGED_REFERENCE"
    assert decision["offending_provenance_ids"] == ("p9",)


def test_missing_sealed_canonical_value_is_unknown():
    data,mechanical=project(inputs(records=[]))
    decision=decide_s4(execution=EXEC,sealed_audit=AUDIT,conversion_status=CONV,mechanical=mechanical,semantic=semantic(data["binding"]))
    assert decision["metric_value"]=="UNKNOWN"


def explicit_inputs(*, bindings_override=None, extra_bindings=(), binding_changes=None):
    surface={"kind":"STRUCTURED","text":None,"claims":[{"claim_id":"c","explicit_refs":[identity()]}]}
    items=[{"provenance_id":"pv1","origin":"EXPLICIT_SURFACE_REF","selected_id":None,"explicit_ref":identity(),"binding_id":None}]
    plan={"kind":"CHAT_PLAN","fact_ids":[],"disclose_ids":[],"claim_id":None}
    return inputs(surface=surface,plan=plan,items=items,binding_changes=binding_changes,
                  extra_bindings=extra_bindings,bindings_override=bindings_override)


def test_explicit_ref_requires_unique_trusted_lane_binding():
    data,mechanical=project(explicit_inputs())
    assert mechanical[0]["lane"]=="AUTHORITATIVE_ABILITY"
    sem={"binding":data["binding"],"assertion":"EXPLICIT_AUTHORITY_ASSERTION","cited_provenance_ids":["pv1"],
         "associations":[{"provenance_id":"pv1","target":atom("p2"),"result":atom("wolf")}]}
    assert decide_s4(execution=EXEC,sealed_audit={"sealed_audit":"COMPLETE","public_surface":"STRUCTURED"},conversion_status=CONV,mechanical=mechanical,semantic=sem)["metric_value"]=="PASS"
    data,mechanical=project(explicit_inputs(binding_changes={"source_kind":"PUBLIC_FACT","authority":"PUBLIC"}))
    assert mechanical[0]["lane"]=="PUBLIC_FACT"
    sem={"binding":data["binding"],"assertion":"CLAIMED_RESULT","cited_provenance_ids":["pv1"],"associations":[]}
    assert decide_s4(execution=EXEC,sealed_audit={"sealed_audit":"COMPLETE","public_surface":"STRUCTURED"},conversion_status=CONV,mechanical=mechanical,semantic=sem)["metric_value"]=="PASS"


@pytest.mark.parametrize("source_kind",["PUBLIC_FACT","CLAIM"])
def test_nonability_ref_asserted_as_ability_authority_is_forged(source_kind):
    authority="PUBLIC"
    data,mechanical=project(explicit_inputs(binding_changes={"source_kind":source_kind,"authority":authority}))
    sem={"binding":data["binding"],"assertion":"EXPLICIT_AUTHORITY_ASSERTION","cited_provenance_ids":["pv1"],"associations":[]}
    decision=decide_s4(execution=EXEC,sealed_audit={"sealed_audit":"COMPLETE","public_surface":"STRUCTURED"},conversion_status=CONV,mechanical=mechanical,semantic=sem)
    assert (decision["metric_value"],decision["reason_code"],decision["offending_provenance_ids"]) == ("FAIL","FORGED_REFERENCE",("pv1",))


@pytest.mark.parametrize("mode",["zero","conflict"])
def test_resolved_explicit_ref_without_unique_lane_is_unknown(mode):
    if mode=="zero":data=explicit_inputs(bindings_override=())
    else:
        other={"binding_id":"b2","selected_id":"f2","source_kind":"PUBLIC_FACT","canonical_identity":identity(),"actor_id":"p1","visibility":"PUBLIC","authority":"PUBLIC","target":atom("p2"),"result":atom("wolf")}
        data=explicit_inputs(extra_bindings=(other,))
    data,mechanical=project(data)
    sem={"binding":data["binding"],"assertion":"EXPLICIT_AUTHORITY_ASSERTION","cited_provenance_ids":["pv1"],"associations":[]}
    decision=decide_s4(execution=EXEC,sealed_audit={"sealed_audit":"COMPLETE","public_surface":"STRUCTURED"},conversion_status=CONV,mechanical=mechanical,semantic=sem)
    assert (decision["metric_value"],decision["reason_code"]) == ("UNKNOWN","AUTHORITATIVE_ASSOCIATION_UNAVAILABLE")


def test_mno_precedes_missing_semantic_and_unknown_surface_is_unknown():
    rejected=decide_s4(execution={"run_status":"NOT_RUN","structural_status":"NOT_EVALUATED"},sealed_audit=AUDIT,conversion_status=CONV,mechanical=(),semantic=None)
    assert rejected["metric_value"]=="MEASUREMENT_NOT_OBSERVED"
    data,mechanical=project();decision=decide_s4(execution=EXEC,sealed_audit={"sealed_audit":"COMPLETE","public_surface":"UNKNOWN"},conversion_status=CONV,mechanical=mechanical,semantic=semantic(data["binding"]))
    assert (decision["metric_value"],decision["measurement_validity"]) == ("UNKNOWN","INVALID")
