from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pytest

from scripts import phase6_quality_probe_v2 as q
from tests.fixtures import phase6_s4_saved_delta_main_pin as main
from tests.fixtures import phase6_s4_saved_binding as saved
from tests.fixtures import phase6_s4_common_provenance as common
from tests.fixtures.phase6_co_surface_custody import _main_rebuild_witness
from tests.test_phase6_s4_saved_binding import _public_full_input


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    path.write_bytes(raw)
    return raw


def _native_co(row, fixture):
    """Native public fixture, explicit selectors and physical response/outcome/seal."""
    value = deepcopy(row)
    option_id, edge = next((bid,b) for bid,b in fixture.bindings.items() if b["source_kind"] == "CO_OPTION")
    role_id, role_edge = next((bid,b) for bid,b in fixture.bindings.items()
        if b["source_kind"] == "CLAIMED_ROLE_OPTION" and b["pointer"].startswith(edge["pointer"] + "/"))
    option = fixture.source["action_context"]["options"][int(edge["pointer"].split("/")[-1])]
    role = option["claimed_role_ids"][int(role_edge["pointer"].split("/")[-1])]
    final = {"decision": "DECLARE", "co_option_id": option_id, "claimed_role_option_id": role_id,
             "comment": "Public synthetic declaration.", "fact_ids": []}
    surface = {"kind": "TEXT_AND_STRUCTURED", "text": None, "comment": final["comment"],
               "claims": [{"decision": "DECLARE", "option": option["option_id"], "claimed_role": role}]}
    value["accepted_surface"] = surface
    view = saved._surface_view(surface)
    plan = {"kind": "CHAT_PLAN", "fact_ids": [], "disclose_ids": [], "claim_id": None}
    binding = {"rubric_version": common.RUBRIC, "blind_id": value["old_blind_id"], "surface_sha256": main.digest(view),
               "row_sha256": main.digest({"rubric_version": common.RUBRIC, "blind_id": value["old_blind_id"], "surface_sha256": main.digest(view)}),
               "catalog_sha256": main.digest([]), "canonical_set_sha256": main.digest([])}
    ep = {"rubric_version": common.RUBRIC, "blind_id": value["old_blind_id"], "surface_sha256": binding["surface_sha256"],
          "row_sha256": binding["row_sha256"], "catalog_sha256": binding["catalog_sha256"], "canonical_set_sha256": binding["canonical_set_sha256"],
          "accepted_plan_sha256": main.digest(plan), "items": []}
    binding["selection_envelope_sha256"] = main.digest(ep)
    value.update(old_binding=binding, old_envelope={"binding": binding, "accepted_plan_sha256": main.digest(plan), "items": []},
                 accepted_plan=plan, base_records=[])
    case_id, seed = value["case_id"], value["seed"]
    stem = f"actual/{case_id}-{seed}-co_opportunity-1"
    response = {"choices": [{"message": {"content": q.wire(final).decode()}}], "timings": {"prompt_ms": 1.25}}
    outcome = {"status": "ACCEPTED", "case_id": case_id, "seed": seed, "stage": "co_opportunity", "ordinal": 1,
               "provider_latency": 0.125, "generation_sent": True, "finish_reason": "stop", "prompt_tokens": 100,
               "completion_tokens": 20, "native_output_tokens": 20}
    saved_row = {"case_id": case_id, "seed": seed, "terminal_stage": "co_opportunity", "elapsed": 0.5, "final": final}
    docs = {"response": response, "outcome": outcome, "saved": saved_row}
    raw = {k: json.dumps(v, separators=(",", ":"), allow_nan=False).encode() for k,v in docs.items()}
    raw["seal"] = json.dumps({stem + "-response.json": sha(raw["response"]), stem + "-outcome.json": sha(raw["outcome"])}, separators=(",", ":")).encode()
    value["accepted_final_proof"] = {"kind": "CANDIDATE_V2", "saved_row_source_id": "saved", "outcome_source_id": "outcome",
        "response_source_id": "response", "container_seal_source_id": "seal", "terminal_stage": "co_opportunity", "ordinal": 1,
        "locator": {"source_id": "response", "snapshot_file_sha256": sha(raw["response"]), "member_name": stem + "-response.json",
                    "json_pointer": "/choices/0/message/content", "encoding": "JSON_STRING_UTF8"}}
    supplied = {"evaluation_id": value["evaluation_id"], "old_blind_id": value["old_blind_id"],
        "identity": {"case_id": case_id, "seed": seed, "stage": "co_opportunity", "ordinal": 1},
        "source": fixture.source, "bindings": fixture.bindings, "catalog": json.loads(q.wire(asdict(fixture.catalog))),
        "accepted_final_bytes": q.wire(final), "accepted_plan": plan, "old_row_binding_sha256": main.digest(binding),
        "old_selection_envelope": value["old_envelope"], "saved_surface": surface}
    return value, raw, _main_rebuild_witness(supplied)


def test_native_root_bound_view_preserves_original_and_never_decides(tmp_path, monkeypatch):
    source = _public_full_input(tmp_path)
    loaded = saved.validate_source_manifest(root=tmp_path, manifest=source["source_manifest"])
    row = next(r for r in source["rows"] if r["accepted_final_proof"] and not r["accepted_plan"]["disclose_ids"])
    fixture = next(f for f in q.prepare()[0] if f.case.case_id == row["case_id"])
    original = deepcopy(row)
    monkeypatch.setattr(common, "decide_s4", lambda **kw: pytest.fail("Meaning decision is outside Main root"))
    root, witness = main.reconstruct_target(row=row, fixture=fixture, loaded_source=loaded, partition="UNSELECTED_ROOT_GAP")
    assert row == original
    assert main.digest(row["accepted_surface"]) != row["old_binding"]["surface_sha256"]
    assert root["binding"]["surface_sha256"] == row["old_binding"]["surface_sha256"]
    assert witness["saved_surface_sha256"] == main.digest(row["accepted_surface"])
    assert witness["bound_surface_sha256"] == row["old_binding"]["surface_sha256"]


def test_native_co_is_independent_and_requires_frozen_exact_witness(tmp_path):
    source = _public_full_input(tmp_path)
    fixtures = {f.case.case_id:f for f in q.prepare()[0]}
    row = next(r for r in source["rows"] if r["accepted_final_proof"] and r["condition"] == "candidate" and fixtures[r["case_id"]].stage == "co_opportunity")
    row, loaded, frozen = _native_co(row, fixtures[row["case_id"]])
    root, witness = main.reconstruct_target(row=row, fixture=fixtures[row["case_id"]], loaded_source=loaded,
                                            partition="CO_SURFACE_GAP", frozen_co_witness=frozen)
    assert witness == frozen and witness["witness_sha256"] != main.digest(witness)
    assert root["accepted_plan"] == row["accepted_plan"]
    changed = deepcopy(frozen); changed["evaluation_id"] = "other"
    with pytest.raises(main.MainRootError, match="^WITNESS$"):
        main.reconstruct_target(row=row, fixture=fixtures[row["case_id"]], loaded_source=loaded,
                                 partition="CO_SURFACE_GAP", frozen_co_witness=changed)


def _fixed_fixture(tmp_path):
    repo = tmp_path / "repo"; group = tmp_path / "fixed"; group.mkdir(); repo.mkdir()
    descriptors = []
    for role in main.ROLES:
        group_name = "T560" if role.startswith("T560_") else "T556"
        path = repo / "Docs/ai/handoffs/tasks/T556_COMPLETION_SAFE_RESULTS.json" if role == "T556_SAFE_RESULTS" else group / (role + ".json")
        raw = _put(path, {"contract": "PUBLIC_SYNTHETIC", "role": role})
        descriptors.append({"role":role, "group":group_name, "path":str(path.resolve()), "sha256":sha(raw), "size_bytes":len(raw)})
    return repo, group, descriptors


def test_fixed_physical_descriptors_roundtrip_and_no_candidate_input(tmp_path):
    repo, group, descriptors = _fixed_fixture(tmp_path)
    parsed = json.loads(json.dumps(descriptors, sort_keys=True))
    values, physical = main.load_fixed_roots(files=parsed, containers={"T556":(group,), "T560":(group,)}, repository_root=repo,
        expected_fixed_roots_sha256=main.digest(descriptors))
    assert tuple(values) == main.ROLES and len(physical) == 15
    import inspect
    parameters = inspect.signature(main.rebuild_main_state).parameters
    assert "candidate_manifest" not in parameters and "runtime_rows" not in parameters


@pytest.mark.parametrize("change", ["missing", "extra", "order", "alias", "outside", "group", "hash", "size_bool", "drift"])
def test_fixed_roots_reject_self_consistent_descriptor_tamper(tmp_path, change):
    repo, group, descriptors = _fixed_fixture(tmp_path)
    if change == "missing": descriptors.pop()
    elif change == "extra": descriptors.append(deepcopy(descriptors[-1]))
    elif change == "order": descriptors[0],descriptors[1] = descriptors[1],descriptors[0]
    elif change == "alias": descriptors[1].update(path=descriptors[0]["path"], sha256=descriptors[0]["sha256"], size_bytes=descriptors[0]["size_bytes"])
    elif change == "outside":
        path = tmp_path / "outside.json"; raw = _put(path, {"x":1}); descriptors[0].update(path=str(path), sha256=sha(raw), size_bytes=len(raw))
    elif change == "group": descriptors[0]["group"] = "T560"
    elif change == "hash": descriptors[0]["sha256"] = "0" * 64
    elif change == "size_bool": descriptors[0]["size_bytes"] = True
    else: Path(descriptors[0]["path"]).write_bytes(b"{}")
    with pytest.raises(main.MainRootError, match="^SOURCE$"):
        main.load_fixed_roots(files=descriptors, containers={"T556":(group,), "T560":(group,)}, repository_root=repo,
                             expected_fixed_roots_sha256=main.digest(descriptors))


def test_fixed_external_expected_hash_is_not_replaced_by_candidate(tmp_path):
    repo, group, descriptors = _fixed_fixture(tmp_path)
    with pytest.raises(main.MainRootError):
        main.load_fixed_roots(files=descriptors, containers={"T556":(group,), "T560":(group,)}, repository_root=repo,
                             expected_fixed_roots_sha256="0" * 64)


def _full_native_roots(tmp_path):
    """All 192 source rows through real saved preparation; no candidate generation."""
    source_root = tmp_path / "source"; fixed_root = tmp_path / "fixed"; fixed_root.mkdir()
    source = _public_full_input(source_root)
    fixtures = {f.case.case_id:f for f in q.prepare()[0]}
    prepared_before = saved.prepare_saved_roots(root=source_root, source=source,
        expected_preflight_sha256=source["preflight"]["preflight_sha256"])
    rows = {r["evaluation_id"]:r for r in source["rows"]}
    co_ids = [h["evaluation_id"] for h in prepared_before["host_rows"] if h["status"] == "INPUT_INVALID"
              and rows[h["evaluation_id"]]["condition"] == "candidate" and fixtures[rows[h["evaluation_id"]]["case_id"]].stage == "co_opportunity"][:5]
    assert len(co_ids) == 5
    loaded = saved.validate_source_manifest(root=source_root, manifest=source["source_manifest"])
    packet = saved._parse_json_bytes(loaded["old_packet_snapshot"])
    mechanical = saved._parse_json_bytes(loaded["old_mechanical_snapshot"])
    mapping = saved._parse_json_bytes(loaded["old_mapping_snapshot"])
    witnesses = {}
    for eid in co_ids:
        original = rows[eid]
        unused = [original["accepted_final_proof"][key] for key in
                  ("saved_row_source_id", "outcome_source_id", "response_source_id")]
        replacement, wrappers, witness = _native_co(original, fixtures[original["case_id"]])
        ids = {k:f"co-{eid}-{k}" for k in wrappers}
        proof = replacement["accepted_final_proof"]
        for key in ("saved_row_source_id", "outcome_source_id", "response_source_id", "container_seal_source_id"):
            proof[key] = ids[proof[key]]
        proof["wrapper_source_id"] = None
        proof["locator"]["source_id"] = ids["response"]
        for key,raw in wrappers.items():
            sid = ids[key]; (source_root / "sources" / sid).write_bytes(raw)
            source["source_manifest"]["files"].append({"source_id":sid, "original_name":f"co/{eid}/{key}.json", "sha256":sha(raw), "size":len(raw)})
        index = next(i for i,r in enumerate(source["rows"]) if r["evaluation_id"] == eid)
        source["rows"][index] = replacement; rows[eid] = replacement
        old_id = replacement["old_blind_id"]
        next(p for p in packet["rows"] if p["blind_id"] == old_id)["surface"] = deepcopy(replacement["accepted_surface"])
        m = next(p for p in mechanical["rows"] if p["blind_id"] == old_id)
        m.update(binding=deepcopy(replacement["old_binding"]), envelope=deepcopy(replacement["old_envelope"]),
                 mechanical=deepcopy(replacement["base_records"]), plan_view=deepcopy(replacement["accepted_plan"]))
        witnesses[eid] = witness
        # Replace the native wrappers instead of retaining obsolete source versions.
        source["source_manifest"]["files"] = [e for e in source["source_manifest"]["files"]
                                            if e["source_id"] not in unused]
        for sid in unused:
            (source_root / "sources" / sid).unlink()
    entries = source["source_manifest"]["files"]
    assert len(entries) <= 395
    while len(entries) < 395:
        sid = "metadata_snapshot_" + str(len(entries))
        raw = _put(source_root / "sources" / sid, {"contract":"PUBLIC_FROZEN_METADATA","ordinal":len(entries)})
        entries.append({"source_id":sid,"original_name":"metadata/"+sid+".json","sha256":sha(raw),"size":len(raw)})
    for sid,document in (("old_packet_snapshot",packet), ("old_mechanical_snapshot",mechanical), ("old_mapping_snapshot",mapping)):
        raw = _put(source_root / "sources" / sid, document)
        entry = next(e for e in source["source_manifest"]["files"] if e["source_id"] == sid)
        entry.update(sha256=sha(raw), size=len(raw))
    manifest = source["source_manifest"]; manifest["files"].sort(key=lambda e:e["original_name"])
    manifest["root_snapshot_sha256"] = main.digest({"contract":manifest["contract"],"files":manifest["files"]})
    manifest["manifest_sha256"] = main.digest({k:v for k,v in manifest.items() if k != "manifest_sha256"})
    pre = source["preflight"]; pre["source_manifest_sha256"] = manifest["manifest_sha256"]
    pre["old_packet_sha256"] = sha((source_root / "sources/old_packet_snapshot").read_bytes())
    pre["old_mechanical_sha256"] = sha((source_root / "sources/old_mechanical_snapshot").read_bytes())
    pre["old_mapping_sha256"] = sha((source_root / "sources/old_mapping_snapshot").read_bytes())
    pre["preflight_sha256"] = main.digest({k:v for k,v in pre.items() if k != "preflight_sha256"})
    prepared = saved.prepare_saved_roots(root=source_root, source=source, expected_preflight_sha256=pre["preflight_sha256"])
    assert {status:sum(h["status"] == status for h in prepared["host_rows"]) for status in
            ("READY","INPUT_INVALID","SAVED_ACCEPTED_FINAL_UNAVAILABLE","MEASUREMENT_NOT_OBSERVED")} == {
             "READY":167,"INPUT_INVALID":7,"SAVED_ACCEPTED_FINAL_UNAVAILABLE":5,"MEASUREMENT_NOT_OBSERVED":13}
    host = {h["evaluation_id"]:h for h in prepared["host_rows"]}
    results = []; annotations = []
    for row in source["rows"]:
        eid = row["evaluation_id"]; state = host[eid]["status"]
        decision = {"binding":{"synthetic":True},"metric_value":"PASS","reason_code":"NO_S4_VIOLATION", "offending_provenance_ids":[],"measurement_validity":"VALID"} if state == "READY" else None
        results.append({"evaluation_id":eid,"status":"DECIDED" if decision else state,"connector_reason":"NONE",
                        "projected_decision":decision,"metric_value":"PASS" if decision else "MEASUREMENT_NOT_OBSERVED" if state == "MEASUREMENT_NOT_OBSERVED" else "UNKNOWN"})
        if state == "MEASUREMENT_NOT_OBSERVED":
            # Native finalize_saved_results uses wrap_decision for MNO, too.
            results[-1] = saved.wrap_decision(evaluation_id=eid, decision={
                "binding":None,"metric_value":"MEASUREMENT_NOT_OBSERVED","reason_code":"PUBLIC_SURFACE_NOT_OBSERVED",
                "offending_provenance_ids":[],"measurement_validity":"INVALID"})
        annotations.append({"evaluation_id":eid,"status":"ANNOTATED" if decision else "NOT_REQUIRED","assertion":"NONE" if decision else None,
                            "cited_provenance_ids":[],"associations":[]})
    old_final = {"contract":saved.CONTRACT,"results":results,"freeze":{"rows":192},"safe":{"rows":192}}
    old_annotations = {"contract":"S4_ANNOTATION_V1","rows":annotations}
    empty_packet = {"contract":saved.CONTRACT,"rows":[]}
    native_docs = {"T556_INPUT":source,"T556_PREPARED":prepared,"T556_SOURCE_MANIFEST":manifest,
        "T556_MAIN_PIN":{"pin_sha256":"1"*64},"T556_PACKET":{"contract":saved.CONTRACT,"candidate_manifest":{},"pin_sha256":"1"*64,"packet":empty_packet,"packet_sha256":main.digest(empty_packet)},
        "T556_PACKET_FREEZE":{"contract":"PUBLIC_SYNTHETIC"},"T556_ANNOTATIONS":old_annotations,
        "T556_ANNOTATION_FREEZE":{"contract":"PUBLIC_SYNTHETIC"},"T556_FINALIZED":old_final,
        "T556_FINAL_FREEZE":{"contract":"PUBLIC_SYNTHETIC"},"T560_SOURCE_MANIFEST":manifest,
        "T560_CANDIDATE":{"manifest_sha256":"2"*64},"T560_MAIN_PIN":{"candidate_manifest_sha256":"2"*64,"pin_sha256":"3"*64},
        "T560_WITNESSES":{"contract":"S4_CO_SURFACE_SOURCE_BINDING_V1","witnesses":witnesses},"T556_SAFE_RESULTS":{"contract":"PUBLIC_SYNTHETIC"}}
    repo = tmp_path / "repo"; repo.mkdir()
    needed = set(main.REQUIRED_CODE)
    for dep,paths in main.DEPENDENCIES.items():
        needed.update(p for _,p in paths); needed.update(main.DEPENDENCY_DOCS[dep])
    code_pin = {}
    for relative in needed:
        target = repo / relative; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(Path(relative).read_bytes())
        if relative in main.REQUIRED_CODE: code_pin[relative] = sha(target.read_bytes())
    descriptors = []; physical = {}
    for role in main.ROLES:
        path = repo / "Docs/ai/handoffs/tasks/T556_COMPLETION_SAFE_RESULTS.json" if role == "T556_SAFE_RESULTS" else fixed_root / (role + ".json")
        raw = _put(path,native_docs[role]); physical[role] = (path,sha(raw))
        descriptors.append({"role":role,"group":"T560" if role.startswith("T560_") else "T556","path":str(path.resolve()),"sha256":sha(raw),"size_bytes":len(raw)})
    source_rows = []
    old_m = {r["blind_id"]:r for r in mechanical["rows"]}
    immutable_loaded = saved.validate_source_manifest(root=source_root,manifest=manifest)
    for i,row in enumerate(source["rows"]):
        eid = row["evaluation_id"]; state = host[eid]["status"]
        part = {"READY":"PRIOR_READY","INPUT_INVALID":"UNSELECTED_ROOT_GAP","SAVED_ACCEPTED_FINAL_UNAVAILABLE":"CO_SURFACE_GAP","MEASUREMENT_NOT_OBSERVED":"MEASUREMENT_NOT_OBSERVED"}[state]
        decision = results[i]["projected_decision"]
        if part == "MEASUREMENT_NOT_OBSERVED": decision = {"binding":None,"metric_value":"MEASUREMENT_NOT_OBSERVED","reason_code":"PUBLIC_SURFACE_NOT_OBSERVED","offending_provenance_ids":[],"measurement_validity":"INVALID"}
        r = {"evaluation_id":eid,"ordinal":i,"pair_key_sha256":row["pair_key_sha256"],"profile_key_sha256":main.digest({"condition":row["condition"]}),
             "old_row_binding_sha256":main.digest(row["old_binding"]),"old_selection_envelope_sha256":main.digest(row["old_envelope"]),
             "old_mechanical_row_sha256":main.digest(old_m[row["old_blind_id"]]),"saved_surface_sha256":main.digest(row["accepted_surface"]),
             "accepted_final_sha256":main._accepted_sha(row,immutable_loaded),
             "old_decision_sha256":main.digest(decision) if decision is not None else None,"old_annotation_row_sha256":main.digest(annotations[i]) if part == "PRIOR_READY" else None,"partition":part}
        r["source_row_sha256"] = main.digest(r); source_rows.append(r)
    roots = {"t556_source_manifest_sha256":manifest["manifest_sha256"],"t556_preflight_sha256":pre["preflight_sha256"],"t556_main_pin_sha256":"1"*64,
             "t556_packet_sha256":main.digest(empty_packet),"t556_packet_freeze_file_sha256":physical["T556_PACKET_FREEZE"][1],
             "t556_annotation_sha256":main.digest(old_annotations),"t556_annotation_freeze_sha256":physical["T556_ANNOTATION_FREEZE"][1],
             "t556_finalized_sha256":main.digest(old_final),"t556_final_freeze_file_sha256":physical["T556_FINAL_FREEZE"][1],
             "t560_source_manifest_sha256":manifest["manifest_sha256"],"t560_candidate_manifest_sha256":"2"*64,"t560_main_pin_sha256":"3"*64}
    source_files = [dict(e,original_name="T556_SOURCE/"+e["original_name"]) for e in manifest["files"]]
    source_files += [{"source_id":role,"original_name":"ROOTS/"+role+".json","sha256":h,"size":p.stat().st_size} for role,(p,h) in physical.items()]
    source_files.sort(key=lambda e:e["original_name"])
    new_source = {"contract":main.CONTRACT,"version":"V1","task":"T563",**roots,
                  "t560_approved_dependency_bundle":main.current_dependency_bundle("T560",repo),"t562_approved_dependency_bundle":main.current_dependency_bundle("T562",repo),
                  "files":source_files,"rows":source_rows}
    new_source["manifest_sha256"] = main.digest(new_source)
    scopes = {k:main.digest({"contract":main.CONTRACT,"ordered_evaluation_ids":[r["evaluation_id"] for r in source_rows if r["partition"] in partitions]})
              for k,partitions in (("target_scope_sha256",main.PARTITIONS[:2]),("prior_scope_sha256",("PRIOR_READY",)),("mno_scope_sha256",("MEASUREMENT_NOT_OBSERVED",)))}
    scopes["pair_scope_sha256"] = main.digest({"contract":main.CONTRACT,"ordered_pair_keys_and_profile_keys":[[r["pair_key_sha256"],r["profile_key_sha256"]] for r in source_rows]})
    rubric_sha = main.digest({"contract":"S4_SAVED_GAP_DELTA_RUBRIC_V1","version":"V1"})
    new_pre = {"contract":main.CONTRACT,"version":"V1","task":"T563","source_manifest_sha256":new_source["manifest_sha256"],
        "t556_core_artifacts_sha256":main.digest([{k:f[k] for k in ("role","sha256","size_bytes")} for f in descriptors if f["group"] == "T556"]),
        "t560_approved_bundle_sha256":new_source["t560_approved_dependency_bundle"]["bundle_sha256"],"t562_approved_bundle_sha256":new_source["t562_approved_dependency_bundle"]["bundle_sha256"],
        **scopes,"helper_source_sha256":code_pin["tests/fixtures/phase6_s4_saved_delta.py"],"rubric_sha256":rubric_sha}
    new_pre["preflight_sha256"] = main.digest(new_pre)
    return {"files":descriptors,"containers":{"T556":(fixed_root,),"T560":(fixed_root,)},"repository_root":repo,"t556_source_root":source_root,
            "expected_fixed_roots_sha256":main.digest(descriptors),"source_manifest":new_source,"preflight":new_pre,"expected_code_sha256":code_pin,"expected_rubric_sha256":rubric_sha}


def test_full_192_native_source_reconstruction_and_candidate_pin_without_cycle(tmp_path):
    arguments = _full_native_roots(tmp_path)
    result = main.rebuild_main_state(**arguments)
    assert len(result["source_rows"]) == len(result["independent_roots"]["rows"]) == 192
    assert result["expected_candidate"]["counts"] == {"co_gap":5,"root_gap":7,"prior":167,"mno":13,"target_ready":12,"target_input_unknown":0}
    assert len(result["target_roots"]) == 12 and all(t["delta_input_status"] == "READY" for t in result["target_roots"])
    from tests.fixtures import phase6_s4_saved_delta as adapter
    candidate = result["expected_candidate"]
    pin = adapter.build_main_pin(independent_state=result, expected_independent_state_sha256=result["state_sha256"], expected_independent_roots_sha256=result["independent_roots"]["roots_sha256"],
        preflight=arguments["preflight"], candidate_manifest=candidate,
        t560_approved_bundle_sha256=arguments["preflight"]["t560_approved_bundle_sha256"],t562_approved_bundle_sha256=arguments["preflight"]["t562_approved_bundle_sha256"])
    assert pin["candidate_manifest_sha256"] == candidate["manifest_sha256"]
    assert pin["coverage_rows_sha256"] == main.digest(candidate["rows"])
    modified = deepcopy(candidate); modified["rows"][0]["reason"] = "READY"; modified["manifest_sha256"] = main.digest({k:v for k,v in modified.items() if k != "manifest_sha256"})
    # Identity/status/reason/count drift cannot be approved by a recomputed self-hash.
    with pytest.raises((main.MainRootError,adapter.SavedDeltaInputError)):
        adapter.build_main_pin(independent_state=result, expected_independent_state_sha256=result["state_sha256"], expected_independent_roots_sha256=result["independent_roots"]["roots_sha256"],
            preflight=arguments["preflight"],candidate_manifest=modified,
            t560_approved_bundle_sha256=arguments["preflight"]["t560_approved_bundle_sha256"],t562_approved_bundle_sha256=arguments["preflight"]["t562_approved_bundle_sha256"])


def test_physical_self_consistent_394_and_396_source_sets_are_rejected_before_decode(tmp_path,monkeypatch):
    arguments = _full_native_roots(tmp_path)
    originals = {r["role"]:Path(r["path"]).read_bytes() for r in arguments["files"]}
    reached=[]
    def forbidden_decode(**kwargs):
        reached.append(True)
        raise AssertionError("source count must be checked before decode")
    monkeypatch.setattr(saved,"validate_source_manifest",forbidden_decode)
    for count in (394,396):
        manifest = saved._parse_json_bytes(originals["T556_SOURCE_MANIFEST"])
        if count == 394: manifest["files"].pop()
        else:
            manifest["files"].append({"source_id":"extra","original_name":"zzz-extra","sha256":sha(b"{}"),"size":2})
        manifest["root_snapshot_sha256"] = main.digest({"contract":manifest["contract"],"files":manifest["files"]})
        manifest["manifest_sha256"] = main.digest({k:v for k,v in manifest.items() if k != "manifest_sha256"})
        files = deepcopy(arguments["files"])
        for descriptor in files:
            role=descriptor["role"]
            value=saved._parse_json_bytes(originals[role])
            if role in ("T556_INPUT","T556_PREPARED"): value["source_manifest"]=manifest
            elif role=="T556_SOURCE_MANIFEST": value=manifest
            raw=_put(Path(descriptor["path"]),value)
            descriptor.update(sha256=sha(raw),size_bytes=len(raw))
        with pytest.raises(main.MainRootError,match="^SOURCE$"):
            main.rebuild_main_state(**dict(arguments,files=files,expected_fixed_roots_sha256=main.digest(files)))
    assert reached==[]


def test_unknown_code_pin_key_is_rejected_before_root_read(tmp_path):
    with pytest.raises(main.MainRootError,match="^SOURCE$"):
        main.rebuild_main_state(files=[],containers={},repository_root=tmp_path,t556_source_root=tmp_path,
            expected_fixed_roots_sha256="0"*64,source_manifest={},preflight={},expected_rubric_sha256="0"*64,
            expected_code_sha256=dict.fromkeys(main.REQUIRED_CODE | {"unknown.py"},"0"*64))


@pytest.mark.parametrize("malformed", (None, {"binding":None,"metric_value":"PASS","reason_code":"NO_S4_VIOLATION",
                                         "offending_provenance_ids":[],"measurement_validity":"VALID"}))
def test_native_mno_decision_must_be_exact_not_missing_or_success(tmp_path, malformed):
    arguments = _full_native_roots(tmp_path)
    descriptors = deepcopy(arguments["files"])
    descriptor = next(item for item in descriptors if item["role"] == "T556_FINALIZED")
    path = Path(descriptor["path"])
    document = saved._parse_json_bytes(path.read_bytes())
    mno = next(row for row in document["results"] if row["metric_value"] == "MEASUREMENT_NOT_OBSERVED")
    mno["projected_decision"] = deepcopy(malformed)
    raw = _put(path, document)
    descriptor.update(sha256=sha(raw), size_bytes=len(raw))
    with pytest.raises(main.MainRootError, match="^OLD_REFERENCE$"):
        main.rebuild_main_state(**dict(arguments, files=descriptors,
                                      expected_fixed_roots_sha256=main.digest(descriptors)))
