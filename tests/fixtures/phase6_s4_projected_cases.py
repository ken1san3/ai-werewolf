"""Public synthetic inputs for the projected ability contract; no saved evidence."""
from __future__ import annotations

import hashlib
from pathlib import Path

from scripts import phase6_quality_probe_v2 as q
from tests.fixtures import phase6_s4_common_provenance as common
from tests.fixtures import phase6_s4_projected_ability_provenance as projected


EXECUTION = {"run_status": "COMPLETE", "structural_status": "ACCEPTED"}
AUDIT = {"sealed_audit": "COMPLETE", "public_surface": "TEXT"}
CONVERSION = {"status": "COMPLETE"}


def atom(value):
    return {"tag": "ABSENT", "value": None} if value is None else {"tag": "VALUE", "value": value}


def owner_fixture():
    for case in q.cases():
        fixture = q.build_fixture(case)
        if fixture.stage == "chat_plan" and fixture.catalog.disclose_ids:
            return fixture
    raise AssertionError("public owner-ability case missing")


def bundle(fixture, *, explicit=False, mixed=False, absent=False, ref_status="RESOLVED"):
    """Build hashes independently from the normative payloads, not helper builders."""
    code_sha = hashlib.sha256(Path("scripts/phase6_quality_probe_v2.py").read_bytes()).hexdigest()
    freeze = projected.freeze_projected_ability_trust(
        fixture=fixture, fixture_builder_source_sha256=code_sha,
        binding_verifier_source_sha256=code_sha)
    trusted = freeze["bindings"][0]
    value = trusted["projected_value"]
    owner = freeze["owner_id"]
    plan = {"kind": "CHAT_PLAN", "fact_ids": ["fact"] if mixed else [],
            "disclose_ids": [trusted["disclose_id"]], "claim_id": "claim" if mixed else None}
    identity = {"record_kind": "ability_result", "order": 100, "visibility": "PUBLIC"}
    surface = {"kind": "ABSENT" if absent else "STRUCTURED" if explicit else "TEXT",
               "text": None if absent or explicit else "A public synthetic result.",
               "claims": [{"claim_id": "surface-claim", "explicit_refs": [identity]}] if explicit and not absent else []}
    raw_surface = common.canonical_wire(surface)
    surface_sha = hashlib.sha256(raw_surface).hexdigest()
    base_bindings = [{"binding_id": trusted["binding_id"], "selected_id": trusted["disclose_id"],
        "source_kind": "ABILITY_RESULT", "canonical_identity": None, "actor_id": owner,
        "visibility": "PUBLIC", "authority": "INTENTIONAL_OWNER_ABILITY",
        "target": atom(value["target_player_id"]), "result": atom(value["result_id"])}]
    if mixed:
        base_bindings.extend([
            {"binding_id": "bf", "selected_id": "fact", "source_kind": "PUBLIC_FACT",
             "canonical_identity": None, "actor_id": owner, "visibility": "PUBLIC",
             "authority": "PUBLIC", "target": atom(None), "result": atom(None)},
            {"binding_id": "bc", "selected_id": "claim", "source_kind": "CLAIM",
             "canonical_identity": None, "actor_id": owner, "visibility": "PUBLIC",
             "authority": "PUBLIC", "target": atom(None), "result": atom(None)}])
    if explicit:
        # A public synthetic canonical record needs its own exact trusted edge.
        # A resolved identity alone does not grant AUTHORITATIVE_ABILITY.
        base_bindings.append({"binding_id": "legacy-b", "selected_id": "legacy-d",
            "source_kind": "ABILITY_RESULT", "canonical_identity": identity,
            "actor_id": owner, "visibility": "PUBLIC", "authority": "INTENTIONAL_OWNER_ABILITY",
            "target": atom("player-x"), "result": atom("human")})
    canonical = [{"identity": identity, "actor_id": owner,
                  "target": atom("player-x"), "result": atom("human")}] if explicit else []
    if ref_status == "NOT_FOUND":
        canonical = []
    elif ref_status == "AMBIGUOUS":
        canonical = canonical + canonical
    items = []
    if explicit and not absent:
        items.append({"provenance_id": "pv0", "origin": "EXPLICIT_SURFACE_REF",
                      "selected_id": None, "explicit_ref": identity, "binding_id": None})
    if mixed:
        items.append({"provenance_id": "pvf", "origin": "SELECTED_PUBLIC_FACT",
                      "selected_id": "fact", "explicit_ref": None, "binding_id": "bf"})
    items.append({"provenance_id": "pv1", "origin": "SELECTED_DISCLOSURE",
                  "selected_id": trusted["disclose_id"], "explicit_ref": None,
                  "binding_id": trusted["binding_id"]})
    if mixed:
        items.append({"provenance_id": "pvc", "origin": "SELECTED_CLAIM",
                      "selected_id": "claim", "explicit_ref": None, "binding_id": "bc"})
    base = {"rubric_version": common.RUBRIC, "blind_id": "synthetic-row",
            "surface_sha256": surface_sha,
            "row_sha256": common.canonical_sha({"rubric_version": common.RUBRIC,
                "blind_id": "synthetic-row", "surface_sha256": surface_sha}),
            "catalog_sha256": common.canonical_sha(sorted(base_bindings, key=lambda x: x["binding_id"])),
            "canonical_set_sha256": common.canonical_sha(canonical)}
    plan_sha = common.canonical_sha(plan)
    base["selection_envelope_sha256"] = common.canonical_sha({
        "rubric_version": common.RUBRIC, "blind_id": base["blind_id"],
        "surface_sha256": surface_sha, "row_sha256": base["row_sha256"],
        "catalog_sha256": base["catalog_sha256"], "canonical_set_sha256": base["canonical_set_sha256"],
        "accepted_plan_sha256": plan_sha, "items": items})
    base_records = common.project_common_provenance(binding=base, accepted_surface=raw_surface,
        accepted_plan=plan, envelope={"binding": base, "accepted_plan_sha256": plan_sha, "items": items},
        bindings=tuple(base_bindings), canonical_records=tuple(canonical))
    final_sha = hashlib.sha256(b'{"message":"A public synthetic result."}').hexdigest()
    binding = {"rubric_version": projected.CONTRACT, "blind_id": base["blind_id"],
        "base_row_sha256": base["row_sha256"], "base_selection_envelope_sha256": base["selection_envelope_sha256"],
        "base_catalog_sha256": base["catalog_sha256"], "base_canonical_set_sha256": base["canonical_set_sha256"],
        "surface_sha256": surface_sha, "accepted_plan_sha256": plan_sha,
        "accepted_final_sha256": final_sha, "custodian_freeze_sha256": freeze["freeze_sha256"],
        "catalog_sha256": common.canonical_sha({"contract": projected.CONTRACT,
            "base_catalog_sha256": base["catalog_sha256"], "catalog_disclose_ids": freeze["catalog_disclose_ids"]}),
        "canonical_binding_set_sha256": common.canonical_sha({"contract": projected.CONTRACT,
            "source_sha256": freeze["source_sha256"], "bindings": freeze["bindings"]})}
    binding["row_sha256"] = common.canonical_sha({"contract": projected.CONTRACT,
        **{k: binding[k] for k in ("blind_id", "base_row_sha256", "surface_sha256", "accepted_plan_sha256",
            "accepted_final_sha256", "catalog_sha256", "canonical_binding_set_sha256", "custodian_freeze_sha256")}})
    witness = {"contract": projected.CONTRACT, "blind_id": binding["blind_id"], "owner_id": owner,
        "public_channel_id": freeze["public_channel_id"], "source_sha256": freeze["source_sha256"],
        "owner_pointer": freeze["owner_pointer"], "owner_value_sha256": freeze["owner_value_sha256"],
        **{k: binding[k] for k in ("row_sha256", "surface_sha256", "accepted_plan_sha256",
            "accepted_final_sha256", "catalog_sha256", "canonical_binding_set_sha256", "custodian_freeze_sha256")}}
    witness["witness_sha256"] = common.canonical_sha(witness)
    selection = {"blind_id": binding["blind_id"], "disclose_id": trusted["disclose_id"],
        "binding_id": trusted["binding_id"], "opaque_provenance_id": "pv1",
        **{k: binding[k] for k in ("row_sha256", "surface_sha256", "accepted_plan_sha256",
            "accepted_final_sha256", "custodian_freeze_sha256")},
        "source_identity": {"kind": "PROJECTED_ABILITY", **{k: trusted[k] for k in
            ("source_sha256", "pointer", "owner_id", "raw_value_sha256", "projected_value_sha256")}}}
    envelope = {"contract": projected.CONTRACT,
        **{k: binding[k] for k in ("blind_id", "row_sha256", "surface_sha256", "accepted_plan_sha256",
            "accepted_final_sha256", "catalog_sha256", "canonical_binding_set_sha256", "custodian_freeze_sha256")},
        "witness_sha256": witness["witness_sha256"], "selection_payloads": [selection.copy()]}
    selection["selection_envelope_sha256"] = common.canonical_sha(envelope)
    row = {"binding": binding, "accepted_plan": plan, "accepted_surface": surface,
           "witness": witness, "selections": [selection], "custodian_freeze": freeze}
    associations = [{"provenance_id": "pv1", "target": atom(value["target_player_id"]),
                     "result": atom(value["result_id"])}]
    if explicit and not absent:
        associations.insert(0, {"provenance_id": "pv0", "target": atom("player-x"), "result": atom("human")})
    semantic = {"contract": projected.CONTRACT,
        "base": {"binding": base, "assertion": "CLAIMED_RESULT", "cited_provenance_ids": [],
                 "associations": associations}, "blind_id": binding["blind_id"],
        **{k: binding[k] for k in ("row_sha256", "surface_sha256", "accepted_final_sha256", "custodian_freeze_sha256")},
        "witness_sha256": witness["witness_sha256"]}
    return {"row": row, "base_records": base_records, "semantic": semantic,
            "expected_freeze_sha256": freeze["freeze_sha256"]}
