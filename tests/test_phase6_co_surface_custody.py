from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from scripts import phase6_quality_probe_v2 as q
from tests.fixtures import phase6_s4_saved_binding as saved
from tests.fixtures.phase6_co_surface_binding import (
    CoSurfaceBindingError, build_co_surface_witness, canonical_sha, canonical_wire,
)
from tests.fixtures.phase6_co_surface_custody import (_accepted_candidate_member, _load_roots, _main_rebuild_witness,
    _validate_main_target_fixture, freeze_co_surface_diagnostic)
from tests.test_phase6_co_surface_binding import _target


def _source_root(tmp_path):
    root = tmp_path / "root"; sources = root / "sources"; sources.mkdir(parents=True)
    documents = {"frozen_source_000": ("fixture.json", {"contract": "frozen"})}
    entries = []
    for source_id, (name, value) in documents.items():
        raw = canonical_wire(value); (sources / source_id).write_bytes(raw)
        entries.append({"source_id": source_id, "original_name": name, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)})
    entries.sort(key=lambda x: x["original_name"])
    root_payload = {"contract": saved.CONTRACT, "files": entries}
    manifest = {**root_payload, "root_snapshot_sha256": saved.canonical_sha(root_payload)}
    manifest["manifest_sha256"] = saved.canonical_sha({"contract": saved.CONTRACT, "root_snapshot_sha256": manifest["root_snapshot_sha256"], "files": entries})
    input_raw, prepared_raw = canonical_wire({"contract": "input"}), canonical_wire({"contract": "prepared"})
    (root / "input.json").write_bytes(input_raw); (root / "prepared.json").write_bytes(prepared_raw)
    safe = {"artifacts": {"input.json": hashlib.sha256(input_raw).hexdigest(), "prepared.json": hashlib.sha256(prepared_raw).hexdigest()}}
    return root, manifest, safe


def test_custody_loader_binds_safe_artifact_and_rehashes(tmp_path):
    root, manifest, safe = _source_root(tmp_path)
    source, prepared, loaded = _load_roots(root, manifest, safe)
    assert source == {"contract": "input"} and prepared == {"contract": "prepared"} and len(loaded) == 1
    (root / "input.json").write_bytes(b"{}")
    with pytest.raises((saved.SavedBindingError, CoSurfaceBindingError)):
        _load_roots(root, manifest, safe)


def test_custody_loader_accepts_actual_t556_public_layout(tmp_path):
    from tests.test_phase6_s4_saved_binding import _public_full_input
    source = _public_full_input(tmp_path)
    (tmp_path / "input.json").write_bytes(saved.canonical_wire(source))
    prepared = saved.prepare_saved_roots(root=tmp_path, source=source,
                                         expected_preflight_sha256=source["preflight"]["preflight_sha256"])
    (tmp_path / "prepared.json").write_bytes(saved.canonical_wire(prepared))
    safe = {"artifacts": {"input.json": hashlib.sha256((tmp_path / "input.json").read_bytes()).hexdigest(),
                          "prepared.json": hashlib.sha256((tmp_path / "prepared.json").read_bytes()).hexdigest()}}
    loaded_source, loaded_prepared, _ = _load_roots(tmp_path, source["source_manifest"], safe)
    assert len(loaded_source["rows"]) == len(loaded_prepared["host_rows"]) == 192
    loaded = saved.validate_source_manifest(root=tmp_path, manifest=source["source_manifest"])
    candidate = next(row for row in source["rows"] if row["condition"] == "candidate" and row["accepted_final_proof"] is not None)
    member, saved_row = _accepted_candidate_member(candidate, loaded)
    assert hashlib.sha256(member).hexdigest() and saved_row["case_id"] == candidate["case_id"]


def test_native_q_prepare_co_fixture_proves_full_catalog_contract():
    fixtures = q.prepare()[0]
    fixture = next(f for f in fixtures if f.catalog.co_options)
    q.verify_bindings(fixture)
    option_id, option_binding = next((bid, b) for bid, b in fixture.bindings.items() if b["source_kind"] == "CO_OPTION")
    role_id, role_binding = next((bid, b) for bid, b in fixture.bindings.items()
                                 if b["source_kind"] == "CLAIMED_ROLE_OPTION" and b["pointer"].startswith(option_binding["pointer"] + "/"))
    option = fixture.source["action_context"]["options"][int(option_binding["pointer"].split("/")[-1])]
    role = option["claimed_role_ids"][int(role_binding["pointer"].split("/")[-1])]
    final = {"decision": "DECLARE", "co_option_id": option_id, "claimed_role_option_id": role_id, "comment": "public synthetic", "fact_ids": []}
    row = {"evaluation_id": "native", "old_blind_id": "blind", "identity": {"case_id": fixture.case.case_id, "seed": 1, "stage": "CO", "ordinal": 1},
           "source": fixture.source, "bindings": fixture.bindings, "catalog": json.loads(q.wire(asdict(fixture.catalog))),
           "accepted_final_bytes": canonical_wire(final), "accepted_plan": {"fact_ids": []},
           "old_row_binding_sha256": "0" * 64,
           "old_selection_envelope": {"binding": {"row_sha256": "0" * 64},
                                      "accepted_plan_sha256": canonical_sha({"fact_ids": []}), "items": []},
           "saved_surface": json.loads(canonical_wire({"kind": "TEXT_AND_STRUCTURED", "text": None, "comment": "public synthetic",
                             "claims": [{"decision": "DECLARE", "option": option["option_id"], "claimed_role": role}]}))}
    witness = build_co_surface_witness(row)
    assert witness["catalog_sha256"] == canonical_sha(row["catalog"])


def test_main_witness_is_independently_rebuilt_and_detects_resolution_tamper():
    row = _target()
    assert _main_rebuild_witness(row) == build_co_surface_witness(row)
    row["bindings"]["q1"]["value_sha256"] = "0" * 64
    with pytest.raises(CoSurfaceBindingError):
        _main_rebuild_witness(row)


def test_main_root_rejects_self_consistent_target_source_mutation():
    fixture = next(f for f in q.prepare()[0] if f.catalog.co_options)
    catalog = json.loads(q.wire(asdict(fixture.catalog)))
    row = {"source": fixture.source, "bindings": fixture.bindings, "catalog": catalog}
    frozen = {"fixture_source_sha256": canonical_sha(fixture.source),
              "fixture_bindings_sha256": canonical_sha(fixture.bindings),
              "fixture_catalog_sha256": canonical_sha(catalog)}
    _validate_main_target_fixture(row, frozen, fixture)
    changed = json.loads(json.dumps(row)); changed["source"]["context"]["player_id"] = "self-consistent-other"
    projection = canonical_sha(changed["source"])
    for binding in changed["bindings"].values(): binding["projection_sha256"] = projection
    with pytest.raises(CoSurfaceBindingError):
        _validate_main_target_fixture(changed, frozen, fixture)


def test_native_public_fact_actor_tamper_is_rejected_by_q_verifier():
    original = next(f for f in q.prepare()[0] if any(b["source_kind"] == "PUBLIC_FACT" and b["actor"] is not None for b in f.bindings.values()))
    bindings = deepcopy(original.bindings)
    fact_id = next(k for k, b in bindings.items() if b["source_kind"] == "PUBLIC_FACT" and b["actor"] is not None)
    bindings[fact_id]["actor"] = "wrong-actor"
    fixture = replace(original, binding_bytes=q.wire(bindings))
    with pytest.raises(ValueError):
        q.verify_bindings(fixture)


def test_saved_row_finite_float_is_parsed_by_existing_saved_parser():
    raw = canonical_wire({"case_id": "x", "seed": 1, "elapsed": 0})
    # JSON finite float is accepted by the existing T556 saved-artifact parser.
    parsed = saved._parse_json_bytes(raw.replace(b'"elapsed":0', b'"elapsed":0.25'))
    assert parsed["elapsed"] == 0.25


def _native_wrapper_bundle(outcome_change=None, response_source="response"):
    final = {"decision": "DECLARE", "co_option_id": "o1", "claimed_role_option_id": "q1", "comment": "x", "fact_ids": []}
    content = json.dumps(final, separators=(",", ":"))
    response = {"choices": [{"message": {"content": content}}], "timings": {"prompt_ms": 1.25}}
    response_raw = json.dumps(response, separators=(",", ":"), allow_nan=False).encode()
    outcome = {"status": "ACCEPTED", "case_id": "G00", "seed": 7, "stage": "message", "ordinal": 1,
               "provider_latency": 0.125, "generation_sent": True, "finish_reason": "stop",
               "prompt_tokens": 100, "completion_tokens": 5, "native_output_tokens": 5}
    if outcome_change: outcome_change(outcome)
    outcome_raw = json.dumps(outcome, separators=(",", ":")).encode()
    saved_row = {"case_id": "G00", "seed": 7, "terminal_stage": "message", "elapsed": 0.75, "final": final}
    saved_raw = json.dumps(saved_row, separators=(",", ":"), allow_nan=False).encode()
    seal = {"actual/G00-7-message-1-response.json": hashlib.sha256(response_raw).hexdigest(),
            "actual/G00-7-message-1-outcome.json": hashlib.sha256(outcome_raw).hexdigest()}
    loaded = {"response": response_raw, "outcome": outcome_raw, "saved": saved_raw,
              "seal": json.dumps(seal, separators=(",", ":")).encode()}
    proof = {"kind": "CANDIDATE_V2", "saved_row_source_id": "saved", "outcome_source_id": "outcome",
             "response_source_id": response_source, "container_seal_source_id": "seal", "terminal_stage": "message", "ordinal": 1,
             "locator": {"source_id": "response", "snapshot_file_sha256": hashlib.sha256(response_raw).hexdigest(),
                         "member_name": "actual/G00-7-message-1-response.json", "json_pointer": "/choices/0/message/content",
                         "encoding": "JSON_STRING_UTF8"}}
    return {"case_id": "G00", "seed": 7, "accepted_final_proof": proof}, loaded, final


def test_native_float_wrappers_and_closed_telemetry_are_accepted():
    row, loaded, final = _native_wrapper_bundle()
    member, saved_row = _accepted_candidate_member(row, loaded)
    assert json.loads(member) == final and saved_row["elapsed"] == 0.75


@pytest.mark.parametrize("change", [
    lambda o: o.update(case_id="other"), lambda o: o.update(extra_private="x"),
    lambda o: o.update(provider_latency=float("inf")), lambda o: o.update(prompt_tokens=True),
])
def test_native_outcome_identity_type_and_unknown_keys_are_rejected(change):
    row, loaded, _ = _native_wrapper_bundle(change)
    with pytest.raises(CoSurfaceBindingError):
        _accepted_candidate_member(row, loaded)


def test_cross_response_and_seal_hash_are_rejected():
    row, loaded, _ = _native_wrapper_bundle(response_source="other")
    loaded["other"] = loaded["response"]
    with pytest.raises(CoSurfaceBindingError): _accepted_candidate_member(row, loaded)
    row, loaded, _ = _native_wrapper_bundle(); seal = json.loads(loaded["seal"]); seal[next(iter(seal))] = "0" * 64
    loaded["seal"] = json.dumps(seal, separators=(",", ":")).encode()
    with pytest.raises(CoSurfaceBindingError): _accepted_candidate_member(row, loaded)


def test_freeze_is_create_only_and_safe(tmp_path):
    manifest = {"manifest_sha256": "1" * 64, "counts": {"target": 5, "prior_ready": 167, "root_gap": 7, "mno": 13, "proved": 5, "target_unknown": 0},
                "rows": [{"reason": "EXACT_CO_SOURCE_BINDING"}] * 5 + [{"reason": "PREVIOUSLY_EVALUATED"}] * 167
                        + [{"reason": "ROOT_BINDING_OUT_OF_SCOPE"}] * 7 + [{"reason": "PUBLIC_SURFACE_NOT_OBSERVED"}] * 13}
    prepared = {"candidate_manifest": manifest, "source_manifest": {"contract": "source"}, "preflight": {"contract": "preflight"},
                "row_inputs": [{"row": i} for i in range(192)], "witnesses": {f"e{i}": {"witness": i} for i in range(5)}}
    pin = {"candidate_manifest_sha256": "1" * 64, "pin_sha256": "2" * 64}
    out = tmp_path / "new-container"
    safe_summary = freeze_co_surface_diagnostic(output_dir=out, prepared=prepared, pin=pin, expected_pin_sha256="2" * 64)
    assert safe_summary["rows"] == 192 and safe_summary["provider_calls"] == 0 and safe_summary["version"] == "V1"
    assert {p.name for p in out.iterdir()} == {"candidate-manifest.json", "main-pin.json", "source-manifest.json",
                                                  "preflight.json", "row-inputs.json", "witness-candidates.json", "safe-summary.json"}
    with pytest.raises((FileExistsError, CoSurfaceBindingError)):
        freeze_co_surface_diagnostic(output_dir=out, prepared=prepared, pin=pin, expected_pin_sha256="2" * 64)
