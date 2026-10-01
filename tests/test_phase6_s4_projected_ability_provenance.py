from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import math

import pytest

from scripts import phase6_quality_probe_v2 as q
from tests.fixtures import phase6_s4_projected_ability_provenance as p
from tests.fixtures import phase6_s4_common_provenance as common
from tests.fixtures.phase6_s4_projected_cases import (
    AUDIT, CONVERSION, EXECUTION, atom, bundle, owner_fixture,
)


@pytest.fixture(scope="module")
def fixture():
    return owner_fixture()


def project(data):
    return p.project_selected_abilities(row=data["row"],
        expected_freeze_sha256=data["expected_freeze_sha256"])


def decide(data, *, records=None, execution=None, audit=None, semantic=None):
    if records is None:
        records = p.merge_common_provenance_v2(base_records=data["base_records"], projected_records=project(data))
    return p.decide_projected_ability_s4(
        execution=execution or EXECUTION, audit=audit or AUDIT, conversion=CONVERSION,
        row=data["row"], expected_freeze_sha256=data["expected_freeze_sha256"],
        merged_records=records, semantic=semantic or data["semantic"])


def test_source_identity_without_evidence_ref_is_lossless_and_passes(fixture):
    data = bundle(fixture)
    before = deepcopy(data)
    records = project(data)
    assert type(records) is tuple and len(records) == 1
    value = data["row"]["custodian_freeze"]["bindings"][0]["raw_value"]
    assert records[0]["canonical_value"] == value
    assert records[0]["source_identity"]["kind"] == "PROJECTED_ABILITY"
    assert "ref" not in records[0]["source_identity"]
    decision = decide(data)
    assert decision["binding"] == data["row"]["binding"]
    assert (decision["metric_value"], decision["reason_code"], decision["measurement_validity"]) == (
        "PASS", "AUTHORITATIVE_VALUE_MATCH", "VALID")
    assert data == before


def test_all_hash_payloads_use_independent_canonical_wire(fixture):
    data = bundle(fixture)
    frozen = data["row"]["custodian_freeze"]
    raw = frozen["bindings"][0]["raw_value"]
    assert frozen["bindings"][0]["raw_value_sha256"] == common.canonical_sha(raw)
    assert frozen["bindings"][0]["projected_value_sha256"] == common.canonical_sha(raw)
    assert frozen["freeze_sha256"] == common.canonical_sha({k: v for k, v in frozen.items() if k != "freeze_sha256"})
    assert p.canonical_wire(data["row"]) == common.canonical_wire(data["row"])
    assert p.strict_parse(p.canonical_wire(data["row"])) == data["row"]


def test_public_fact_and_claim_records_keep_their_exact_position_and_values(fixture):
    data = bundle(fixture, mixed=True)
    merged = p.merge_common_provenance_v2(base_records=data["base_records"], projected_records=project(data))
    assert len(merged) == 3
    assert merged[0] == data["base_records"][0]
    assert merged[2] == data["base_records"][2]
    assert merged[0]["lane"] == "PUBLIC_FACT" and merged[2]["lane"] == "CLAIMED_REPORT"
    assert decide(data, records=merged)["metric_value"] == "PASS"


@pytest.mark.parametrize("left,right,expected", [
    ("match", "match", "PASS"), ("mismatch", "match", "FAIL"),
    ("mismatch", "missing", "UNKNOWN"), ("match", "undecidable", "UNKNOWN"),
    ("match", "mismatch", "FAIL"), ("undecidable", "mismatch", "UNKNOWN"),
])
def test_mixed_source_ref_and_projected_ability_are_quantified_as_one_row(fixture, left, right, expected):
    data = bundle(fixture, explicit=True)
    associations = data["semantic"]["base"]["associations"]
    for index, state in reversed(list(enumerate((left, right)))):
        if state == "missing":
            associations.pop(index)
        elif state == "mismatch":
            associations[index]["result"] = atom("other-synthetic-result")
        elif state == "undecidable":
            associations[index]["result"] = {"tag": "UNDECIDABLE", "value": None}
    decision = decide(data, audit={**AUDIT, "public_surface": "STRUCTURED"})
    assert decision["metric_value"] == expected
    assert decision["measurement_validity"] == ("INVALID" if expected == "UNKNOWN" else "VALID")


def test_missing_association_or_none_is_unknown_and_complete_absent_is_pass(fixture):
    data = bundle(fixture)
    data["semantic"]["base"].update(assertion="NONE", associations=[])
    assert decide(data)["metric_value"] == "UNKNOWN"
    data = bundle(fixture, absent=True)
    data["semantic"]["base"].update(assertion="NONE", associations=[])
    assert decide(data, audit={**AUDIT, "public_surface": "ABSENT"})["reason_code"] == "NO_PUBLIC_ASSERTION"


@pytest.mark.parametrize("status,expected,reason", [
    ("NOT_FOUND", "FAIL", "FORGED_REFERENCE"),
    ("AMBIGUOUS", "UNKNOWN", "AMBIGUOUS_CANONICAL_BINDING"),
])
def test_source_ref_resolution_keeps_priority_over_missing_projected_semantics(fixture, status, expected, reason):
    data = bundle(fixture, explicit=True, ref_status=status)
    data["semantic"]["base"]["associations"] = []
    result = decide(data, audit={**AUDIT, "public_surface": "STRUCTURED"})
    assert (result["metric_value"], result["reason_code"]) == (expected, reason)


@pytest.mark.parametrize("variant", ["null", "missing", "extra", "bad-binding-type"])
def test_accepted_malformed_row_fails_closed_without_public_payload(fixture, variant):
    data = bundle(fixture)
    if variant == "null": data["row"] = None
    elif variant == "missing": del data["row"]["witness"]
    elif variant == "extra": data["row"]["secret-test-key"] = "synthetic-secret-value"
    else: data["row"]["binding"] = False
    result = p.decide_projected_ability_s4(execution=EXECUTION, audit=AUDIT, conversion=CONVERSION,
        row=data["row"], expected_freeze_sha256=data["expected_freeze_sha256"],
        merged_records=(), semantic=data["semantic"])
    assert result["metric_value"] == "UNKNOWN" and result["measurement_validity"] == "INVALID"
    assert "synthetic-secret-value" not in repr(result)


@pytest.mark.parametrize("status", ["NOT_RUN", "DEADLINE", "TRANSPORT", "EXECUTION_ERROR", "RAW_RECEIVED"])
def test_unobserved_does_not_inspect_bad_row_or_root(status):
    result = p.decide_projected_ability_s4(execution={"run_status": status, "structural_status": "NOT_EVALUATED"},
        audit=AUDIT, conversion=CONVERSION, row=None, expected_freeze_sha256=None,
        merged_records=None, semantic=None)
    assert result == {"binding": None, "metric_value": "MEASUREMENT_NOT_OBSERVED",
                      "reason_code": "PUBLIC_SURFACE_NOT_OBSERVED",
                      "offending_provenance_ids": (), "measurement_validity": "INVALID"}


@pytest.mark.parametrize("key", ["row_sha256", "surface_sha256", "accepted_final_sha256",
                                  "witness_sha256", "custodian_freeze_sha256", "blind_id"])
def test_semantic_observation_is_bound_to_row_final_and_witness(fixture, key):
    data = bundle(fixture)
    data["semantic"][key] = "foreign-row" if key == "blind_id" else "0" * 64
    result = decide(data)
    assert (result["metric_value"], result["measurement_validity"]) == ("UNKNOWN", "INVALID")
    assert result["reason_code"] == "PROJECTED_ROW_BINDING_MISMATCH"


@pytest.mark.parametrize("key", ["row_sha256", "selection_envelope_sha256", "catalog_sha256", "canonical_set_sha256"])
def test_legacy_binding_is_not_implicitly_reinterpreted(fixture, key):
    data = bundle(fixture)
    data["semantic"]["base"]["binding"][key] = "0" * 64
    assert decide(data)["reason_code"] == "PROJECTED_ROW_BINDING_MISMATCH"


@pytest.mark.parametrize("container,key,value", [
    ("binding", "accepted_plan_sha256", "0" * 64),
    ("binding", "catalog_sha256", "0" * 64),
    ("binding", "canonical_binding_set_sha256", "0" * 64),
    ("binding", "base_row_sha256", "0" * 64),
    ("witness", "owner_id", "foreign-owner"),
    ("witness", "public_channel_id", "private-channel"),
    ("witness", "source_sha256", "0" * 64),
])
def test_projection_rejects_changed_hash_or_authority_witness(fixture, container, key, value):
    data = bundle(fixture)
    data["row"][container][key] = value
    with pytest.raises(p.ProjectedAbilityInputError, match="S4_INPUT_INTEGRITY"):
        project(data)


@pytest.mark.parametrize("key,value", [
    ("opaque_provenance_id", "other-id"), ("disclose_id", "not-selected"),
    ("binding_id", "not-a-binding"), ("selection_envelope_sha256", "0" * 64),
    ("accepted_final_sha256", "0" * 64),
])
def test_selected_identity_is_not_inferred_or_repaired(fixture, key, value):
    data = bundle(fixture)
    data["row"]["selections"][0][key] = value
    with pytest.raises(p.ProjectedAbilityInputError):
        project(data)


@pytest.mark.parametrize("variant", ["missing", "extra", "duplicate", "unknown-plan", "duplicate-plan"])
def test_selection_sets_are_closed_before_filtering(fixture, variant):
    data = bundle(fixture)
    if variant == "missing": data["row"]["selections"] = []
    elif variant in ("extra", "duplicate"): data["row"]["selections"].append(deepcopy(data["row"]["selections"][0]))
    elif variant == "unknown-plan": data["row"]["accepted_plan"]["disclose_ids"].append("unknown")
    else: data["row"]["accepted_plan"]["disclose_ids"] *= 2
    with pytest.raises(p.ProjectedAbilityInputError):
        project(data)


@pytest.mark.parametrize("key,value", [
    ("owner_id", "foreign-owner"), ("channel_visibility", "NON_PUBLIC"),
    ("authority", "FORBIDDEN"), ("source_kind", "PUBLIC_FACT"),
    ("raw_value_sha256", "0" * 64), ("projected_value_sha256", "0" * 64),
    ("pointer", "/context/ability_results/00"),
])
def test_changed_freeze_cannot_supply_its_own_trust_root(fixture, key, value):
    data = bundle(fixture)
    frozen = data["row"]["custodian_freeze"]
    frozen["bindings"][0][key] = value
    frozen["freeze_sha256"] = common.canonical_sha({k: v for k, v in frozen.items() if k != "freeze_sha256"})
    result = p.decide_projected_ability_s4(execution=EXECUTION, audit=AUDIT, conversion=CONVERSION,
        row=data["row"], expected_freeze_sha256=data["expected_freeze_sha256"], merged_records=(), semantic=data["semantic"])
    assert (result["metric_value"], result["measurement_validity"]) == ("UNKNOWN", "INVALID")
    assert result["reason_code"] != "FORGED_REFERENCE"


@pytest.mark.parametrize("variant", ["missing", "extra", "duplicate", "wrong-origin", "wrong-base-binding", "wrong-id"])
def test_merge_never_appends_or_overwrites_an_unrelated_record(fixture, variant):
    data = bundle(fixture, mixed=True)
    records = list(project(data))
    if variant == "missing": records = []
    elif variant in ("extra", "duplicate"): records += deepcopy(records)
    elif variant == "wrong-origin": records[0]["origin"] = "SELECTED_PUBLIC_FACT"
    elif variant == "wrong-base-binding":
        data["base_records"][1]["binding"]["blind_id"] = "foreign-row"
    else: records[0]["opaque_provenance_id"] = "pvf"
    with pytest.raises(p.ProjectedAbilityInputError):
        p.merge_common_provenance_v2(base_records=data["base_records"], projected_records=tuple(records))


def test_decision_rechecks_exact_projection_not_a_caller_label(fixture):
    data = bundle(fixture)
    merged = p.merge_common_provenance_v2(base_records=data["base_records"], projected_records=project(data))
    modified = deepcopy(merged)
    modified[0]["canonical_value"]["result_id"] = "fabricated-result"
    result = decide(data, records=modified)
    assert result["reason_code"] == "PROJECTED_PROVENANCE_MERGE_INVALID"
    assert result["metric_value"] == "UNKNOWN"


@pytest.mark.parametrize("variant", ["duplicate-key", "nan", "infinity", "noncanonical", "unknown-shape"])
def test_wire_parser_is_strict(variant):
    raw = {"duplicate-key": b'{"x":1,"x":2}', "nan": b'{"x":NaN}',
           "infinity": b'{"x":Infinity}', "noncanonical": b'{"x": 1}',
           "unknown-shape": b'[]'}[variant]
    with pytest.raises(ValueError, match="S4_INPUT_INTEGRITY"):
        p.strict_parse(raw)


@pytest.mark.parametrize("value", [math.nan, math.inf, 1.5])
def test_noninteger_numbers_do_not_enter_canonical_hashes(value):
    with pytest.raises(ValueError, match="S4_INPUT_INTEGRITY"):
        p.canonical_wire({"value": value})


def test_custodian_verifies_original_fixture_and_both_code_identities(fixture):
    code_sha = hashlib.sha256(__import__("pathlib").Path("scripts/phase6_quality_probe_v2.py").read_bytes()).hexdigest()
    changed = dict(fixture.bindings)
    first = fixture.catalog.disclose_ids[0]
    changed[first] = {**changed[first], "actor": "foreign-owner"}
    forged = replace(fixture, binding_bytes=q.wire(changed))
    with pytest.raises(p.ProjectedAbilityInputError):
        p.freeze_projected_ability_trust(fixture=forged, fixture_builder_source_sha256=code_sha,
                                       binding_verifier_source_sha256=code_sha)
    with pytest.raises(p.ProjectedAbilityInputError):
        p.freeze_projected_ability_trust(fixture=fixture, fixture_builder_source_sha256=code_sha,
                                       binding_verifier_source_sha256="0" * 64)


@pytest.mark.parametrize("field,key", [
    ("canonical_value", "result_id"), ("source_identity", "owner_id"),
])
def test_projection_output_mutation_cannot_change_trusted_row(fixture, field, key):
    data = bundle(fixture)
    before = deepcopy(data)
    output = project(data)
    output[0][field][key] = "mutated-output-only"
    assert data == before
    assert project(data) != output


@pytest.mark.parametrize("index,field,key", [
    (0, "binding", "blind_id"),
    (0, "canonical_value", "result"),
    (2, "canonical_value", "result_id"),
])
def test_merge_output_mutation_cannot_change_either_input(fixture, index, field, key):
    data = bundle(fixture, mixed=True, explicit=True)
    projected = project(data)
    before_data, before_projected = deepcopy(data), deepcopy(projected)
    output = p.merge_common_provenance_v2(base_records=data["base_records"], projected_records=projected)
    output[index][field][key] = "mutated-output-only"
    assert data == before_data
    assert projected == before_projected


@pytest.mark.parametrize("mismatch", [False, True])
def test_pass_and_unknown_decision_binding_do_not_alias_row(fixture, mismatch):
    data = bundle(fixture)
    if mismatch:
        data["semantic"]["accepted_final_sha256"] = "0" * 64
    before = deepcopy(data)
    result = decide(data)
    assert result["metric_value"] == ("UNKNOWN" if mismatch else "PASS")
    result["binding"]["blind_id"] = "mutated-output-only"
    assert data == before


@pytest.mark.parametrize("location", ["value", "key", "escaped-wire"])
def test_lone_surrogate_is_a_closed_payload_free_input_failure(location):
    with pytest.raises(p.ProjectedAbilityInputError, match="^S4_INPUT_INTEGRITY$") as failure:
        if location == "escaped-wire":
            p.strict_parse(b'{"value":"\\ud800"}')
        else:
            value = {"value": chr(0xD800)} if location == "value" else {chr(0xDFFF): "value"}
            p.canonical_wire(value)
    assert failure.value.__cause__ is None
    assert failure.value.detail in p.ProjectedAbilityInputError._DETAILS


def test_valid_non_ascii_strings_keep_canonical_utf8_roundtrip():
    value = {"public": "\u4eba\u72fc\U0001f43a"}
    expected = common.canonical_wire(value)
    assert p.canonical_wire(value) == expected
    assert p.strict_parse(expected) == value
