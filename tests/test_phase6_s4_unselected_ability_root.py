from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import math
from pathlib import Path

import pytest

from scripts import phase6_quality_probe_v2 as q
from tests.fixtures import phase6_s4_common_provenance as common
from tests.fixtures.phase6_s4_unselected_ability_root import (
    CONTRACT, UnselectedAbilityInputError, build_unselected_ability_row_v2,
    build_unselected_semantic_v2, canonical_sha, canonical_wire,
    decide_unselected_ability_s4_v2, freeze_unselected_ability_inventory_v2,
    strict_parse, validate_unselected_ability_root_v2,
)


EXEC = {"run_status": "COMPLETE", "structural_status": "ACCEPTED"}
AUDIT = {"sealed_audit": "COMPLETE", "public_surface": "TEXT"}
CONV = {"status": "COMPLETE"}
DIGEST = "a" * 64


def atom(value):
    return {"tag": "ABSENT", "value": None} if value is None else {"tag": "VALUE", "value": value}


def native_fixture():
    return next(item for item in q.prepare()[0] if item.case.case_id == "G15-1")


def native_freeze():
    probe_sha = hashlib.sha256(Path(q.__file__).read_bytes()).hexdigest()
    return freeze_unselected_ability_inventory_v2(
        fixture=native_fixture(), fixture_builder_source_sha256=probe_sha,
        binding_verifier_source_sha256=probe_sha,
    )


def base_case(*, surface=None, plan=None, explicit=False):
    identity = {"record_kind": "ability_result", "order": 1, "visibility": "PUBLIC"}
    surface = surface or {"kind": "TEXT", "text": "discussion", "claims": []}
    raw = common.canonical_wire(surface)
    plan = plan or {"kind": "CHAT_PLAN", "fact_ids": [], "disclose_ids": [], "claim_id": None}
    selected_id = plan["disclose_ids"][0] if plan["disclose_ids"] else "d1"
    edge = {"binding_id": "b1", "selected_id": selected_id, "source_kind": "ABILITY_RESULT",
            "canonical_identity": identity, "actor_id": "player-6", "visibility": "PUBLIC",
            "authority": "INTENTIONAL_OWNER_ABILITY", "target": atom("player-4"),
            "result": atom("wolf")}
    bindings = (edge,)
    records = ({"identity": identity, "actor_id": "player-6", "target": atom("player-4"),
                "result": atom("wolf")},) if explicit or plan["disclose_ids"] else ()
    items = ([{"provenance_id": "pv1", "origin": "EXPLICIT_SURFACE_REF", "selected_id": None,
               "explicit_ref": identity, "binding_id": None}] if explicit else
             [{"provenance_id": "pv1", "origin": "SELECTED_DISCLOSURE", "selected_id": selected_id,
               "explicit_ref": None, "binding_id": "b1"}] if plan["disclose_ids"] else [])
    surface_sha = hashlib.sha256(raw).hexdigest()
    row_sha = common.canonical_sha({"rubric_version": common.RUBRIC, "blind_id": "blind1",
                                    "surface_sha256": surface_sha})
    catalog_sha = common.canonical_sha(sorted(bindings, key=lambda item: item["binding_id"]))
    canonical_set_sha = common.canonical_sha(sorted(
        records, key=lambda item: (item["identity"]["record_kind"], item["identity"]["order"],
                                   item["identity"]["visibility"])))
    payload = {"rubric_version": common.RUBRIC, "blind_id": "blind1", "surface_sha256": surface_sha,
               "row_sha256": row_sha, "catalog_sha256": catalog_sha,
               "canonical_set_sha256": canonical_set_sha,
               "accepted_plan_sha256": common.canonical_sha(plan), "items": items}
    binding = {"rubric_version": common.RUBRIC, "blind_id": "blind1", "surface_sha256": surface_sha,
               "row_sha256": row_sha, "selection_envelope_sha256": common.canonical_sha(payload),
               "catalog_sha256": catalog_sha, "canonical_set_sha256": canonical_set_sha}
    envelope = {"binding": binding, "accepted_plan_sha256": common.canonical_sha(plan), "items": items}
    mechanical = common.project_common_provenance(
        binding=binding, accepted_surface=raw, accepted_plan=plan, envelope=envelope,
        bindings=bindings, canonical_records=records,
    )
    return surface, plan, binding, mechanical


def prepared(*, assertion="NONE", cited=None, associations=None, surface=None, plan=None, explicit=False):
    surface, plan, old_binding, mechanical = base_case(
        surface=surface, plan=plan, explicit=explicit)
    freeze = native_freeze()
    row = build_unselected_ability_row_v2(
        old_binding=old_binding, accepted_plan=plan, accepted_surface=surface,
        accepted_final_sha256="b" * 64, inventory_freeze=freeze,
    )
    base = {"binding": old_binding, "assertion": assertion,
            "cited_provenance_ids": [] if cited is None else cited,
            "associations": [] if associations is None else associations}
    semantic = build_unselected_semantic_v2(
        row=row, base=base, base_annotation_freeze_sha256="c" * 64)
    return freeze, row, mechanical, semantic


def decide(freeze, row, mechanical, semantic, *, execution=EXEC, audit=AUDIT):
    return decide_unselected_ability_s4_v2(
        execution=execution, audit=audit, conversion=CONV, row=row,
        expected_freeze_sha256=freeze["freeze_sha256"],
        base_records=mechanical, semantic=semantic,
    )


def test_native_null_channel_seven_key_inventory_selection_zero_passes_without_projection():
    fixture = native_fixture()
    assert fixture.bindings["d000"]["channel"] is None
    raw = fixture.source["grounding"]["ability_results"]["records"][0]
    assert set(raw) == {"target_player_id", "result_id", "revealed_role_id", "event_type",
                        "day", "phase", "order"}
    freeze, row, mechanical, semantic = prepared(assertion="NONE")
    inventory = freeze["inventory_bindings"][0]
    assert inventory["source_channel"] == {"tag": "ABSENT", "value": None}
    assert inventory["channel_visibility"] == "NOT_APPLICABLE"
    before = deepcopy(mechanical)
    validated = validate_unselected_ability_root_v2(
        row=row, expected_freeze_sha256=freeze["freeze_sha256"], base_records=mechanical)
    assert validated == mechanical == before
    result = decide(freeze, row, mechanical, semantic)
    assert (result["metric_value"], result["reason_code"], result["measurement_validity"]) == (
        "PASS", "NO_S4_VIOLATION", "VALID")
    assert result["binding"] == row["binding"]


def test_unreferenced_claim_uses_common_decision_as_only_pass_authority():
    freeze, row, mechanical, semantic = prepared(assertion="CLAIMED_RESULT")
    result = decide(freeze, row, mechanical, semantic)
    assert (result["metric_value"], result["reason_code"]) == (
        "PASS", "UNREFERENCED_CLAIMED_REPORT")


def test_nonnull_publication_destination_uses_only_actual_source_visibility():
    fixture = next(item for item in q.prepare()[0] if item.case.case_id == "G04-1")
    probe_sha = hashlib.sha256(Path(q.__file__).read_bytes()).hexdigest()
    freeze = freeze_unselected_ability_inventory_v2(
        fixture=fixture, fixture_builder_source_sha256=probe_sha,
        binding_verifier_source_sha256=probe_sha)
    binding = freeze["inventory_bindings"][0]
    assert binding["source_channel"] == {"tag": "VALUE", "value": fixture.channel}
    assert binding["channel_visibility"] == "PUBLIC"


def test_malformed_native_source_is_rejected_without_channel_or_value_completion():
    fixture = native_fixture()
    source = fixture.source
    del source["grounding"]["ability_results"]["records"][0]["result_id"]
    malformed = replace(fixture, source_bytes=q.wire(source))
    probe_sha = hashlib.sha256(Path(q.__file__).read_bytes()).hexdigest()
    with pytest.raises(UnselectedAbilityInputError):
        freeze_unselected_ability_inventory_v2(
            fixture=malformed, fixture_builder_source_sha256=probe_sha,
            binding_verifier_source_sha256=probe_sha)


def test_selected_ability_is_outside_and_never_projected():
    plan = {"kind": "CHAT_PLAN", "fact_ids": [], "disclose_ids": ["d000"], "claim_id": None}
    freeze, row, mechanical, semantic = prepared(plan=plan)
    result = decide(freeze, row, mechanical, semantic)
    assert (result["metric_value"], result["reason_code"]) == (
        "UNKNOWN", "SELECTED_ABILITY_OUTSIDE_UNSELECTED_ROOT")
    assert result["binding"] == row["binding"] and result["offending_provenance_ids"] == ()


def test_explicit_authority_without_ref_is_dedicated_unknown():
    freeze, row, mechanical, semantic = prepared(assertion="EXPLICIT_AUTHORITY_ASSERTION")
    result = decide(freeze, row, mechanical, semantic)
    assert (result["metric_value"], result["reason_code"]) == (
        "UNKNOWN", "EXPLICIT_AUTHORITY_REFERENCE_UNAVAILABLE")


def test_explicit_source_ref_match_and_mismatch_preserve_common_semantics():
    ref = {"record_kind": "ability_result", "order": 1, "visibility": "PUBLIC"}
    surface = {"kind": "STRUCTURED", "text": None,
               "claims": [{"claim_id": "c1", "explicit_refs": [ref]}]}
    association = {"provenance_id": "pv1", "target": atom("player-4"), "result": atom("wolf")}
    freeze, row, mechanical, semantic = prepared(
        surface=surface, explicit=True, assertion="EXPLICIT_AUTHORITY_ASSERTION",
        cited=["pv1"], associations=[association])
    match = decide(freeze, row, mechanical, semantic, audit={**AUDIT, "public_surface": "STRUCTURED"})
    changed = deepcopy(semantic); changed["base"]["associations"][0]["result"] = atom("human")
    mismatch = decide(freeze, row, mechanical, changed, audit={**AUDIT, "public_surface": "STRUCTURED"})
    assert (match["metric_value"], match["reason_code"]) == ("PASS", "AUTHORITATIVE_VALUE_MATCH")
    assert (mismatch["metric_value"], mismatch["reason_code"]) == ("FAIL", "AUTHORITATIVE_VALUE_MISMATCH")


@pytest.mark.parametrize("mutation", ["owner", "pointer", "channel", "hash"])
def test_inventory_integrity_failures_are_unknown_before_semantics(mutation):
    freeze, row, mechanical, semantic = prepared(assertion="CLAIMED_RESULT")
    broken = deepcopy(row)
    item = broken["inventory_freeze"]["inventory_bindings"][0]
    if mutation == "owner":
        item["owner_id"] = "other"
    elif mutation == "pointer":
        item["pointer"] = "/bad"
    elif mutation == "channel":
        item["source_channel"] = {"tag": "VALUE", "value": "public"}
    else:
        item["raw_value_sha256"] = "0" * 64
    result = decide(freeze, broken, mechanical, semantic)
    assert (result["metric_value"], result["reason_code"]) == (
        "UNKNOWN", "UNSELECTED_ABILITY_TRUST_ROOT_UNAVAILABLE")


def test_row_and_semantic_cross_hash_failures_keep_parsed_new_binding():
    freeze, row, mechanical, semantic = prepared()
    broken_row = deepcopy(row); broken_row["witness"]["source_sha256"] = "0" * 64
    row_result = decide(freeze, broken_row, mechanical, semantic)
    broken_semantic = deepcopy(semantic); broken_semantic["witness_sha256"] = "0" * 64
    semantic_result = decide(freeze, row, mechanical, broken_semantic)
    assert row_result["reason_code"] == "UNSELECTED_ABILITY_ROW_BINDING_MISMATCH"
    assert semantic_result["reason_code"] == "UNSELECTED_ABILITY_ROW_BINDING_MISMATCH"
    assert row_result["binding"] == semantic_result["binding"] == row["binding"]


def test_mno_does_not_read_malformed_inputs_and_has_null_binding():
    result = decide_unselected_ability_s4_v2(
        execution={"run_status": "NOT_RUN", "structural_status": "NOT_EVALUATED"},
        audit={"sealed_audit": "CORRUPT", "public_surface": "UNKNOWN"},
        conversion={"status": "BINDING_MISSING"}, row={"bad": math.nan},
        expected_freeze_sha256="bad", base_records=({"bad": object()},), semantic={"bad": object()})
    assert result == {"binding": None, "metric_value": "MEASUREMENT_NOT_OBSERVED",
                      "reason_code": "PUBLIC_SURFACE_NOT_OBSERVED",
                      "offending_provenance_ids": (), "measurement_validity": "INVALID"}


@pytest.mark.parametrize(
    ("audit", "conversion"),
    [
        ({"sealed_audit": "COMPLETE"}, CONV),
        ({"sealed_audit": "COMPLETE", "public_surface": "TEXT", "extra": 1}, CONV),
        ({"sealed_audit": "COMPLETE", "public_surface": "BAD"}, CONV),
        (AUDIT, {}),
        (AUDIT, {"status": "COMPLETE", "extra": 1}),
        (AUDIT, {"status": "BAD"}),
        ({"sealed_audit": "COMPLETE", "public_surface": "UNKNOWN"}, CONV),
    ],
)
def test_accepted_status_integrity_failures_are_s4_input_with_parsed_binding(audit, conversion):
    freeze, row, mechanical, semantic = prepared()
    result = decide_unselected_ability_s4_v2(
        execution=EXEC, audit=audit, conversion=conversion, row=row,
        expected_freeze_sha256=freeze["freeze_sha256"],
        base_records=mechanical, semantic=semantic)
    assert (result["metric_value"], result["reason_code"], result["measurement_validity"]) == (
        "UNKNOWN", "S4_INPUT_INTEGRITY", "INVALID")
    assert result["binding"] == row["binding"]


def test_strict_parser_rejects_duplicate_nonfinite_and_noncanonical_wire():
    with pytest.raises(UnselectedAbilityInputError):
        strict_parse(b'{"x":1,"x":2}')
    with pytest.raises(UnselectedAbilityInputError):
        canonical_wire({"x": math.nan})
    with pytest.raises(UnselectedAbilityInputError):
        strict_parse(b'{"x": 1}')


def test_outputs_are_deep_copied():
    freeze, row, mechanical, semantic = prepared()
    result = decide(freeze, row, mechanical, semantic)
    result["binding"]["blind_id"] = "changed"
    assert row["binding"]["blind_id"] == "blind1"
