from __future__ import annotations

import json
from copy import deepcopy

import pytest

from tests.fixtures.phase6_co_surface_binding import (
    CoSurfaceBindingError, build_candidate_manifest, build_co_surface_witness,
    build_main_pin, canonical_sha, canonical_wire, diagnose_co_surface_row, strict_parse, verify_main_pin,
    write_create_only,
)

D = "0" * 64


def _target(eid="e-target-0"):
    source = {"context": {"player_id": "p1"}, "action_context": {"options": [
        {"type": "co_declare", "option_id": "PUBLIC_OPTION", "claimed_role_ids": ["PUBLIC_ROLE"]}
    ]}, "facts": [{"kind": "DAY", "value": 1, "actor": "p2"}]}
    projection = canonical_sha(source)
    option = source["action_context"]["options"][0]
    bindings = {
        "o1": {"projection_sha256": projection, "pointer": "/action_context/options/0", "source_kind": "CO_OPTION",
               "actor": "p1", "channel": None, "authority": "PUBLIC", "value_sha256": canonical_sha(option)},
        "q1": {"projection_sha256": projection, "pointer": "/action_context/options/0/claimed_role_ids/0", "source_kind": "CLAIMED_ROLE_OPTION",
               "actor": "p1", "channel": None, "authority": "PUBLIC", "value_sha256": canonical_sha("PUBLIC_ROLE")},
        "f1": {"projection_sha256": projection, "pointer": "/facts/0", "source_kind": "PUBLIC_FACT",
               "actor": "p2", "channel": None, "authority": "PUBLIC", "value_sha256": canonical_sha(source["facts"][0])},
    }
    final = {"decision": "DECLARE", "co_option_id": "o1", "claimed_role_option_id": "q1", "comment": "synthetic", "fact_ids": ["f1"]}
    plan = {"fact_ids": ["f1"]}
    envelope = {"binding": {"row_sha256": D}, "accepted_plan_sha256": canonical_sha(plan),
                "items": [{"provenance_id": "pv1", "origin": "SELECTED_PUBLIC_FACT", "selected_id": "f1",
                           "explicit_ref": None, "binding_id": "bf1"}]}
    return {"evaluation_id": eid, "old_blind_id": "blind", "identity": {"case_id": "G00", "seed": 1, "stage": "CO", "ordinal": 1},
            "source": source, "bindings": bindings, "catalog": {"reply_ids": [], "player_ids": ["p1", "p2"], "peer_player_ids": ["p2"],
                "fact_ids": ["f1"], "disclose_ids": [], "utterance_claim_ids": [], "observed_claim_ids": [],
                "opinion_bases": [], "vote_options": [], "co_options": [{"option_id": "o1", "claimed_role_option_ids": ["q1"]}],
                "ability_options": []},
            "accepted_final_bytes": canonical_wire(final), "accepted_plan": plan,
            "old_row_binding_sha256": D, "old_selection_envelope": envelope,
            "saved_surface": {"kind": "TEXT_AND_STRUCTURED", "text": None, "comment": "synthetic",
                              "claims": [{"decision": "DECLARE", "option": "PUBLIC_OPTION", "claimed_role": "PUBLIC_ROLE"}]}}


def _manifest_rows():
    target_ids = [f"e-target-{i}" for i in range(5)]
    rows = []
    for i, eid in enumerate(target_ids):
        witness = build_co_surface_witness(_target(eid))
        rows.append({"evaluation_id": eid, "old_row_binding_sha256": D, "saved_surface_sha256": witness["saved_surface_sha256"],
                     "partition": "TARGET_SURFACE_GAP", "witness": witness, "failure_reason": None})
    for partition, count, prefix in (("PRIOR_READY", 167, "ready"), ("ROOT_GAP", 7, "root"), ("MEASUREMENT_NOT_OBSERVED", 13, "mno")):
        for i in range(count):
            rows.append({"evaluation_id": f"e-{prefix}-{i}", "old_row_binding_sha256": D, "saved_surface_sha256": D,
                         "partition": partition, "witness": None, "failure_reason": None})
    return rows, target_ids


def test_witness_native_five_keys_and_fact_preserved():
    witness = build_co_surface_witness(_target())
    assert witness["raw_selection"]["fact_ids"] == ["f1"]
    assert witness["option_resolution"]["actor_id"] == "p1"
    assert witness["option_resolution"]["authority"] == "PUBLIC"
    assert witness["claim_projection_sha256"] == canonical_sha({"decision": "DECLARE", "option": "PUBLIC_OPTION", "claimed_role": "PUBLIC_ROLE"})


def test_empty_fact_ids_is_legal():
    row = _target(); final = json.loads(row["accepted_final_bytes"]); final["fact_ids"] = []
    row["accepted_final_bytes"] = canonical_wire(final); row["accepted_plan"] = {"fact_ids": []}
    row["old_selection_envelope"] = {"binding": {"row_sha256": D}, "accepted_plan_sha256": canonical_sha(row["accepted_plan"]), "items": []}
    assert build_co_surface_witness(row)["raw_selection"]["fact_ids"] == []


@pytest.mark.parametrize("mutate", [
    lambda r: r["bindings"].pop("o1"),
    lambda r: r["bindings"]["o1"].update(source_kind="PUBLIC_FACT"),
    lambda r: r["bindings"]["o1"].update(actor="p2"),
    lambda r: r["bindings"]["o1"].update(authority="UNKNOWN"),
    lambda r: r["bindings"]["o1"].update(channel="public"),
    lambda r: r["bindings"]["o1"].update(pointer="/action_context/options/1"),
    lambda r: r["bindings"]["o1"].update(value_sha256=D),
    lambda r: r["bindings"]["q1"].update(pointer="/facts/0"),
    lambda r: r["catalog"]["co_options"][0].update(claimed_role_option_ids=["other"]),
    lambda r: r["saved_surface"]["claims"][0].update(option="wrong"),
    lambda r: r["bindings"]["f1"].update(authority="FORBIDDEN"),
])
def test_source_surface_and_catalog_tamper_is_closed(mutate):
    row = _target(); mutate(row)
    with pytest.raises(CoSurfaceBindingError) as error:
        build_co_surface_witness(row)
    assert str(error.value) == "CO_SURFACE_BINDING_INPUT_INTEGRITY"


@pytest.mark.parametrize("change", [
    lambda x: x.pop("fact_ids"), lambda x: x.update(extra=1), lambda x: x.update(comment=None),
    lambda x: x.update(comment=""), lambda x: x.update(fact_ids=["f1", "f1"]),
    lambda x: x.update(fact_ids=["f1", "f2", "f3"]), lambda x: x.update(co_option_id=True),
])
def test_native_shape_rejects_missing_extra_null_duplicates_and_types(change):
    row = _target(); final = json.loads(row["accepted_final_bytes"]); change(final)
    row["accepted_final_bytes"] = canonical_wire(final)
    with pytest.raises(CoSurfaceBindingError):
        build_co_surface_witness(row)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'[]'])
def test_strict_json_rejects_duplicate_nonfinite_and_nonobject(raw):
    with pytest.raises(CoSurfaceBindingError):
        strict_parse(raw)


def test_manifest_partitions_all_192_and_main_pin():
    rows, target_ids = _manifest_rows()
    manifest = build_candidate_manifest(rows, source_manifest_sha256=D, preflight_sha256=D, ordered_target_ids=target_ids)
    assert manifest["counts"] == {"target": 5, "prior_ready": 167, "root_gap": 7, "mno": 13, "proved": 5, "target_unknown": 0}
    assert [r["status"] for r in manifest["rows"]].count("NOT_APPLICABLE") == 174
    assert [r["status"] for r in manifest["rows"]].count("MEASUREMENT_NOT_OBSERVED") == 13
    pin = build_main_pin(candidate=manifest, source_manifest_sha256=D, preflight_sha256=D,
                         ordered_target_ids=target_ids, independently_derived_rows=deepcopy(rows))
    verify_main_pin(manifest, pin, pin["pin_sha256"])


def test_manifest_allows_target_unknown_without_turning_it_into_pass():
    rows, target_ids = _manifest_rows(); rows[0]["witness"] = None; rows[0]["failure_reason"] = "CO_SURFACE_MISMATCH"
    manifest = build_candidate_manifest(rows, source_manifest_sha256=D, preflight_sha256=D, ordered_target_ids=target_ids)
    assert manifest["counts"]["target_unknown"] == 1
    assert manifest["rows"][0]["status"] == "UNKNOWN"


@pytest.mark.parametrize("mutation", ["duplicate", "wrong_partition", "wrong_scope", "cross_row_witness"])
def test_manifest_rejects_partition_scope_and_cross_row_tamper(mutation):
    rows, target_ids = _manifest_rows()
    if mutation == "duplicate": rows[1]["evaluation_id"] = rows[0]["evaluation_id"]
    elif mutation == "wrong_partition": rows[5]["partition"] = "TARGET_SURFACE_GAP"
    elif mutation == "wrong_scope": target_ids = list(reversed(target_ids))
    else: rows[0]["witness"]["evaluation_id"] = "other"
    with pytest.raises(CoSurfaceBindingError):
        build_candidate_manifest(rows, source_manifest_sha256=D, preflight_sha256=D, ordered_target_ids=target_ids)


def test_main_pin_is_external_and_detects_candidate_or_pin_tamper():
    rows, target_ids = _manifest_rows(); candidate = build_candidate_manifest(rows, source_manifest_sha256=D, preflight_sha256=D, ordered_target_ids=target_ids)
    pin = build_main_pin(candidate=candidate, source_manifest_sha256=D, preflight_sha256=D,
                         ordered_target_ids=target_ids, independently_derived_rows=deepcopy(rows))
    with pytest.raises(CoSurfaceBindingError): verify_main_pin(candidate, pin, "1" * 64)
    changed = deepcopy(pin); changed["counts"]["proved"] = 4
    with pytest.raises(CoSurfaceBindingError): verify_main_pin(candidate, changed, pin["pin_sha256"])


@pytest.mark.parametrize(("mutation", "reason"), [
    (lambda r: r["bindings"].pop("o1"), "CO_SOURCE_BINDING_MISSING"),
    (lambda r: r["catalog"]["co_options"][0].update(claimed_role_option_ids=[]), "CO_CATALOG_MEMBERSHIP_INVALID"),
    (lambda r: r["saved_surface"]["claims"][0].update(option="different"), "CO_SURFACE_MISMATCH"),
])
def test_target_failure_has_closed_reason_precedence(mutation, reason):
    row = _target(); mutation(row)
    witness, actual = diagnose_co_surface_row(row)
    assert witness is None and actual == reason


def test_public_fact_actor_is_not_forced_to_owner():
    row = _target()
    assert row["bindings"]["f1"]["actor"] == "p2"
    assert build_co_surface_witness(row)["fact_binding_set_sha256"]


def test_plan_and_explicit_envelope_fact_order_are_exact():
    row = _target(); row["accepted_plan"]["fact_ids"] = []
    assert diagnose_co_surface_row(row)[1] == "CO_SOURCE_BINDING_MISSING"


def test_full_catalog_rejects_unselected_duplicate_option_and_fact():
    row = _target(); row["catalog"]["co_options"].append(deepcopy(row["catalog"]["co_options"][0]))
    assert diagnose_co_surface_row(row)[1] == "CO_CATALOG_MEMBERSHIP_INVALID"


@pytest.mark.parametrize("mutation", [
    lambda e: e.pop("items"), lambda e: e.update(extra=[]),
    lambda e: e["items"][0].update(origin="SELECTED_FACT"),
    lambda e: e["items"].append({"provenance_id": "pv2", "origin": "SELECTED_PUBLIC_FACT", "selected_id": "other", "explicit_ref": None, "binding_id": "bf2"}),
    lambda e: e.update(accepted_plan_sha256="1" * 64),
])
def test_native_old_envelope_shape_origin_order_and_plan_hash_are_strict(mutation):
    row = _target(); mutation(row["old_selection_envelope"])
    assert diagnose_co_surface_row(row)[0] is None
    row = _target(); row["catalog"]["fact_ids"].append("f1")
    assert diagnose_co_surface_row(row)[1] == "CO_CATALOG_MEMBERSHIP_INVALID"
    row = _target(); row["old_selection_envelope"]["items"][0]["selected_id"] = "other"
    assert diagnose_co_surface_row(row)[1] == "CO_SOURCE_BINDING_MISSING"


def test_returned_values_are_detached_from_inputs():
    row = _target(); witness = build_co_surface_witness(row); row["catalog"]["fact_ids"].clear()
    assert witness["raw_selection"]["fact_ids"] == ["f1"]
    rows, target_ids = _manifest_rows(); manifest = build_candidate_manifest(rows, source_manifest_sha256=D, preflight_sha256=D, ordered_target_ids=target_ids)
    rows[0]["witness"]["raw_selection"]["fact_ids"].clear()
    assert manifest["rows"][0]["witness_sha256"] is not None


def test_canonical_json_object_key_order_does_not_change_surface_value():
    row = _target()
    row["saved_surface"] = json.loads(canonical_wire(row["saved_surface"]))
    assert build_co_surface_witness(row)["saved_surface_sha256"] == canonical_sha(row["saved_surface"])


def test_object_order_fix_keeps_strict_scalar_types_and_array_order():
    row = _target(); row["saved_surface"]["claims"][0]["decision"] = True
    assert diagnose_co_surface_row(row)[1] == "CO_SURFACE_MISMATCH"
    row = _target(); row["saved_surface"]["claims"] = list(reversed(row["saved_surface"]["claims"] + [{"decision": "DECLARE", "option": "x", "claimed_role": "y"}]))
    assert diagnose_co_surface_row(row)[1] == "CO_SURFACE_MISMATCH"


def test_create_only_and_payload_free_error(tmp_path):
    path = tmp_path / "diagnostic.json"; write_create_only(path, {"contract": "x"})
    secret = "PRIVATE-PATH-VALUE"
    with pytest.raises(CoSurfaceBindingError) as error: write_create_only(path, {"secret": secret})
    assert secret not in str(error.value)
