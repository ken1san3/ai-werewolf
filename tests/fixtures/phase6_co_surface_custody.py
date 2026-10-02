"""Source-root custody adapter for the T560 CO diagnostic."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts import phase6_quality_probe_v2 as q
from tests.fixtures import phase6_s4_saved_binding as saved
from tests.fixtures.phase6_co_surface_binding import (
    CONTRACT, CoSurfaceBindingError, build_candidate_manifest, build_co_surface_witness,
    build_main_pin, canonical_sha, canonical_wire, diagnose_co_surface_row, strict_parse,
    write_create_only,
)


def _fail() -> None:
    raise CoSurfaceBindingError("INPUT_INTEGRITY")


def _file_sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_artifact_sha(safe: dict, name: str) -> str:
    try:
        value = safe["artifacts"][name]
    except (KeyError, TypeError):
        _fail()
    if type(value) is not str or len(value) != 64:
        _fail()
    return value


def _load_roots(root: Path, manifest: dict, safe: dict) -> tuple[dict, dict, dict[str, bytes]]:
    loaded = saved.validate_source_manifest(root=root, manifest=manifest)
    try:
        input_raw, prepared_raw = (root / "input.json").read_bytes(), (root / "prepared.json").read_bytes()
    except OSError:
        _fail()
    if _file_sha(input_raw) != _safe_artifact_sha(safe, "input.json") or _file_sha(prepared_raw) != _safe_artifact_sha(safe, "prepared.json"):
        _fail()
    return strict_parse(input_raw), strict_parse(prepared_raw), loaded


def _accepted_candidate_member(row: dict, loaded: dict[str, bytes]) -> tuple[bytes, dict]:
    proof = row.get("accepted_final_proof")
    if type(proof) is not dict or proof.get("kind") != "CANDIDATE_V2":
        _fail()
    try:
        response_raw = loaded[proof["response_source_id"]]
        saved_row = saved._parse_json_bytes(loaded[proof["saved_row_source_id"]])
        outcome_raw = loaded[proof["outcome_source_id"]]
        outcome = saved._parse_json_bytes(outcome_raw)
        seal = saved._parse_json_bytes(loaded[proof["container_seal_source_id"]])
        locator = proof["locator"]
    except (KeyError, TypeError, CoSurfaceBindingError, saved.SavedBindingError):
        _fail()
    if (_file_sha(response_raw) != locator.get("snapshot_file_sha256") or locator.get("source_id") != proof["response_source_id"]
            or locator.get("json_pointer") != "/choices/0/message/content" or locator.get("encoding") != "JSON_STRING_UTF8"):
        _fail()
    try:
        response = saved._parse_json_bytes(response_raw)
    except saved.SavedBindingError:
        _fail()
    try:
        content = response["choices"][0]["message"]["content"]
        member = content.encode("utf-8")
    except (KeyError, IndexError, TypeError, UnicodeEncodeError):
        _fail()
    parsed = strict_parse(member)
    if parsed != saved_row.get("final"):
        _fail()
    required = {"status": "ACCEPTED", "case_id": row["case_id"], "seed": row["seed"],
                "stage": proof["terminal_stage"], "ordinal": proof["ordinal"]}
    allowed = set(required) | {"provider_latency", "generation_sent", "finish_reason", "prompt_tokens",
                               "completion_tokens", "native_output_tokens"}
    if set(outcome) - allowed or any(outcome.get(key) != value for key, value in required.items()):
        _fail()
    if "provider_latency" in outcome and (type(outcome["provider_latency"]) not in (int, float)
            or type(outcome["provider_latency"]) is bool or not math.isfinite(outcome["provider_latency"])
            or outcome["provider_latency"] < 0): _fail()
    if "generation_sent" in outcome and type(outcome["generation_sent"]) is not bool: _fail()
    if "finish_reason" in outcome and (type(outcome["finish_reason"]) is not str or not outcome["finish_reason"]): _fail()
    for key in ("prompt_tokens", "completion_tokens", "native_output_tokens"):
        if key in outcome and (type(outcome[key]) is not int or type(outcome[key]) is bool or outcome[key] < 0): _fail()
    if (seal.get(proof["locator"]["member_name"]) != _file_sha(response_raw)
            or seal.get(proof["locator"]["member_name"].replace("-response.json", "-outcome.json")) != _file_sha(outcome_raw)):
        _fail()
    return member, saved_row


def _main_resolve(source: Any, pointer: str) -> Any:
    if type(pointer) is not str or not pointer.startswith("/"):
        _fail()
    value = source
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if type(value) is dict and token in value:
            value = value[token]
        elif type(value) is list and token.isdigit() and str(int(token)) == token and int(token) < len(value):
            value = value[int(token)]
        else:
            _fail()
    return value


def _main_rebuild_witness(row: dict[str, Any]) -> dict[str, Any]:
    """Independent witness projection from frozen primitives; does not call candidate helper."""
    final = strict_parse(row["accepted_final_bytes"])
    if set(final) != {"decision", "co_option_id", "claimed_role_option_id", "comment", "fact_ids"} or final["decision"] != "DECLARE":
        _fail()
    option_id, role_id, comment, facts = final["co_option_id"], final["claimed_role_option_id"], final["comment"], final["fact_ids"]
    if any(type(x) is not str or not x for x in (option_id, role_id, comment)) or type(facts) is not list:
        _fail()
    source, bindings, catalog = row["source"], row["bindings"], row["catalog"]
    owner = _main_resolve(source, "/context/player_id")
    def edge(binding_id, kind, actor_check):
        try: binding = bindings[binding_id]
        except (KeyError, TypeError): _fail()
        if (set(binding) != {"projection_sha256", "pointer", "source_kind", "actor", "channel", "authority", "value_sha256"}
                or binding["source_kind"] != kind or binding["channel"] is not None or binding["authority"] != "PUBLIC"
                or (actor_check and binding["actor"] != owner) or binding["projection_sha256"] != canonical_sha(source)):
            _fail()
        value = _main_resolve(source, binding["pointer"])
        if binding["value_sha256"] != canonical_sha(value): _fail()
        return binding, value
    option_edge, option = edge(option_id, "CO_OPTION", True)
    role_edge, role = edge(role_id, "CLAIMED_ROLE_OPTION", True)
    co_options = catalog.get("co_options")
    matches = [x for x in co_options if type(x) is dict and x.get("option_id") == option_id] if type(co_options) is list else []
    if (len(matches) != 1 or role_id not in matches[0].get("claimed_role_option_ids", [])
            or type(option) is not dict or role not in option.get("claimed_role_ids", [])):
        _fail()
    fact_rows = []
    for fact_id in facts:
        binding, value = edge(fact_id, "PUBLIC_FACT", False)
        fact_rows.append({"fact_id": fact_id, "binding_sha256": canonical_sha(binding), "resolved_value_sha256": canonical_sha(value)})
    claim = {"decision": "DECLARE", "option": option["option_id"], "claimed_role": role}
    surface = {"kind": "TEXT_AND_STRUCTURED", "text": None, "comment": comment, "claims": [claim]}
    if surface != row["saved_surface"]: _fail()
    def resolution(bid, kind, binding, value):
        return {"binding_id": bid, "source_kind": kind, "projection_sha256": binding["projection_sha256"],
                "value_sha256": binding["value_sha256"], "pointer": binding["pointer"], "actor_id": binding["actor"],
                "channel": None, "authority": "PUBLIC", "resolved_value_sha256": canonical_sha(value)}
    payload = {"contract": CONTRACT, "evaluation_id": row["evaluation_id"], "old_blind_id": row["old_blind_id"],
        "row_identity_sha256": canonical_sha(row["identity"]), "source_sha256": canonical_sha(source),
        "bindings_sha256": canonical_sha(bindings), "catalog_sha256": canonical_sha(catalog),
        "accepted_final_sha256": _file_sha(row["accepted_final_bytes"]), "accepted_plan_sha256": canonical_sha(row["accepted_plan"]),
        "old_row_binding_sha256": row["old_row_binding_sha256"],
        "old_selection_envelope_sha256": canonical_sha(row["old_selection_envelope"]), "saved_surface_sha256": canonical_sha(surface),
        "raw_selection": {"decision": "DECLARE", "option_binding_id": option_id, "role_binding_id": role_id,
                          "comment_sha256": canonical_sha(comment), "fact_ids": deepcopy(facts)},
        "option_resolution": resolution(option_id, "CO_OPTION", option_edge, option),
        "role_resolution": resolution(role_id, "CLAIMED_ROLE_OPTION", role_edge, role),
        "catalog_membership_sha256": canonical_sha({"option_binding_id": option_id, "role_binding_id": role_id,
            "co_option_index": co_options.index(matches[0]), "role_index": matches[0]["claimed_role_option_ids"].index(role_id)}),
        "fact_binding_set_sha256": canonical_sha(fact_rows), "claim_projection_sha256": canonical_sha(claim)}
    return {**payload, "witness_sha256": canonical_sha(payload)}


def _validate_main_target_fixture(row: dict, frozen: dict, fixture: Any) -> None:
    q.verify_bindings(fixture)
    native_catalog = json.loads(q.wire(asdict(fixture.catalog)))
    if (row.get("source") != fixture.source or row.get("bindings") != fixture.bindings
            or row.get("catalog") != native_catalog
            or canonical_sha(fixture.source) != frozen["fixture_source_sha256"]
            or canonical_sha(fixture.bindings) != frozen["fixture_bindings_sha256"]
            or canonical_sha(native_catalog) != frozen["fixture_catalog_sha256"]):
        _fail()


def prepare_co_surface_custody(*, root: Path, source_manifest: dict, safe_results: dict,
                               target_ids: list[str], target_rows: dict[str, dict]) -> dict:
    """Load frozen T556 roots and build a create-only candidate in T556 row order."""
    code_hashes = {"custody": _file_sha(Path(__file__).read_bytes()),
                   "helper": _file_sha(Path(__file__).with_name("phase6_co_surface_binding.py").read_bytes())}
    source, prepared, _ = _load_roots(root, source_manifest, safe_results)
    if (type(target_ids) is not list or len(target_ids) != 5 or len(set(target_ids)) != 5
            or type(target_rows) is not dict or set(target_rows) != set(target_ids)):
        _fail()
    if source.get("source_manifest") != source_manifest or prepared.get("source_manifest") != source_manifest:
        _fail()
    rows = source.get("rows"); hosts = prepared.get("host_rows")
    if type(rows) is not list or type(hosts) is not list or len(rows) != 192 or len(hosts) != 192:
        _fail()
    host_by = {x.get("evaluation_id"): x for x in hosts if type(x) is dict}
    if len(host_by) != 192:
        _fail()
    # Reuse the approved T556 row/source verifier rather than accepting caller roots.
    loaded = saved.validate_source_manifest(root=root, manifest=source_manifest)
    old_packet = strict_parse(loaded["old_packet_snapshot"])
    old_mechanical = strict_parse(loaded["old_mechanical_snapshot"])
    old_mapping = strict_parse(loaded["old_mapping_snapshot"])
    fixtures = {fixture.case.case_id: fixture for fixture in q.prepare()[0]}
    manifest_rows = []
    for row in rows:
        saved.validate_saved_row_input(row=row, old_packet=old_packet, old_mechanical=old_mechanical, old_mapping=old_mapping)
        eid = row["evaluation_id"]; host = host_by.get(eid)
        if host is None or host.get("old_binding") != row["old_binding"] or host.get("old_envelope") != row["old_envelope"]:
            _fail()
        if row["execution"] != {"run_status": "COMPLETE", "structural_status": "ACCEPTED"}:
            partition, witness, reason = "MEASUREMENT_NOT_OBSERVED", None, None
        elif eid in target_rows:
            fixture = fixtures.get(row["case_id"])
            if fixture is None:
                _fail()
            q.verify_bindings(fixture)
            supplied = deepcopy(target_rows[eid])
            member, saved_row = _accepted_candidate_member(row, loaded)
            supplied["accepted_final_bytes"] = member
            supplied["identity"] = {"case_id": row["case_id"], "seed": row["seed"],
                                    "stage": row["accepted_final_proof"]["terminal_stage"],
                                    "ordinal": row["accepted_final_proof"]["ordinal"]}
            if (canonical_sha(supplied["source"]) != row["fixture_source_sha256"]
                    or canonical_sha(supplied["bindings"]) != row["fixture_bindings_sha256"]
                    or canonical_sha(supplied["catalog"]) != row["fixture_catalog_sha256"]
                    or supplied["accepted_plan"] != row["accepted_plan"]
                    or supplied["saved_surface"] != row["accepted_surface"]
                    or supplied["old_selection_envelope"] != row["old_envelope"]):
                _fail()
            if saved_row.get("case_id") != row["case_id"] or saved_row.get("seed") != row["seed"]:
                _fail()
            witness, reason = diagnose_co_surface_row(supplied); partition = "TARGET_SURFACE_GAP"
        elif host.get("connector_reason") == "SAVED_BASE_BINDING_MISMATCH":
            partition, witness, reason = "ROOT_GAP", None, None
        else:
            partition, witness, reason = "PRIOR_READY", None, None
        manifest_rows.append({"evaluation_id": eid, "old_row_binding_sha256": canonical_sha(row["old_binding"]),
                              "saved_surface_sha256": canonical_sha(row["accepted_surface"]), "partition": partition,
                              "witness": witness, "failure_reason": reason})
    candidate = build_candidate_manifest(manifest_rows, source_manifest_sha256=source_manifest["manifest_sha256"],
                                         preflight_sha256=source["preflight"]["preflight_sha256"], ordered_target_ids=target_ids)
    saved.validate_source_manifest(root=root, manifest=source_manifest)
    if code_hashes != {"custody": _file_sha(Path(__file__).read_bytes()),
                       "helper": _file_sha(Path(__file__).with_name("phase6_co_surface_binding.py").read_bytes())}:
        _fail()
    return {"contract": CONTRACT, "candidate_manifest": candidate, "manifest_rows": deepcopy(manifest_rows),
            "witnesses": {row["evaluation_id"]: row["witness"] for row in manifest_rows if row["witness"] is not None},
            "code_hashes": code_hashes,
            "source_manifest": deepcopy(source_manifest), "preflight": deepcopy(source["preflight"]),
            "row_inputs": deepcopy(source["rows"]),
            "source_manifest_sha256": source_manifest["manifest_sha256"],
            "preflight_sha256": source["preflight"]["preflight_sha256"]}


def derive_main_pin_from_frozen(*, root: Path, source_manifest: dict, safe_results: dict,
                                target_ids: list[str], target_rows: dict[str, dict],
                                prepared_co: dict, candidate: dict) -> dict:
    """Independently reload roots and derive expected witnesses; candidate is comparison-only."""
    source, t556_prepared, loaded = _load_roots(root, source_manifest, safe_results)
    current_code = {"custody": _file_sha(Path(__file__).read_bytes()),
                    "helper": _file_sha(Path(__file__).with_name("phase6_co_surface_binding.py").read_bytes())}
    if (prepared_co.get("candidate_manifest") != candidate or set(prepared_co.get("witnesses", {})) - set(target_ids)
            or prepared_co.get("code_hashes") != current_code):
        _fail()
    hosts = {row["evaluation_id"]: row for row in t556_prepared["host_rows"]}
    fixtures = {fixture.case.case_id: fixture for fixture in q.prepare()[0]}
    independent_rows = []
    for frozen in source["rows"]:
        eid = frozen["evaluation_id"]; host = hosts.get(eid)
        if host is None: _fail()
        witness = reason = None
        if frozen["execution"] != {"run_status": "COMPLETE", "structural_status": "ACCEPTED"}:
            partition = "MEASUREMENT_NOT_OBSERVED"
        elif eid in target_ids:
            row = deepcopy(target_rows[eid]); member, saved_row = _accepted_candidate_member(frozen, loaded)
            fixture = fixtures.get(frozen["case_id"])
            if fixture is None: _fail()
            _validate_main_target_fixture(row, frozen, fixture)
            row["accepted_final_bytes"] = member
            row["identity"] = {"case_id": frozen["case_id"], "seed": frozen["seed"],
                               "stage": frozen["accepted_final_proof"]["terminal_stage"],
                               "ordinal": frozen["accepted_final_proof"]["ordinal"]}
            if (row["accepted_plan"] != frozen["accepted_plan"] or row["saved_surface"] != frozen["accepted_surface"]
                    or row["old_selection_envelope"] != frozen["old_envelope"]
                    or saved_row.get("case_id") != frozen["case_id"] or saved_row.get("seed") != frozen["seed"]): _fail()
            # Reuse only the normative closed validator/reason classifier. Expected
            # witness bytes are rebuilt below by a separate projection.
            normative, reason = diagnose_co_surface_row(row)
            if normative is not None:
                try: witness = _main_rebuild_witness(row)
                except CoSurfaceBindingError: witness, reason = None, "INPUT_INTEGRITY"
            partition = "TARGET_SURFACE_GAP"
        elif host.get("connector_reason") == "SAVED_BASE_BINDING_MISMATCH":
            partition = "ROOT_GAP"
        else:
            partition = "PRIOR_READY"
        independent_rows.append({"evaluation_id": eid, "old_row_binding_sha256": canonical_sha(frozen["old_binding"]),
            "saved_surface_sha256": canonical_sha(frozen["accepted_surface"]), "partition": partition,
            "witness": witness, "failure_reason": reason})
    rebuilt = build_candidate_manifest(independent_rows, source_manifest_sha256=source_manifest["manifest_sha256"],
                                       preflight_sha256=source["preflight"]["preflight_sha256"], ordered_target_ids=target_ids)
    if rebuilt != candidate: _fail()
    pin = build_main_pin(candidate=candidate, source_manifest_sha256=source_manifest["manifest_sha256"],
                         preflight_sha256=source["preflight"]["preflight_sha256"], ordered_target_ids=target_ids,
                         independently_derived_rows=independent_rows)
    saved.validate_source_manifest(root=root, manifest=source_manifest)
    if current_code != {"custody": _file_sha(Path(__file__).read_bytes()),
                        "helper": _file_sha(Path(__file__).with_name("phase6_co_surface_binding.py").read_bytes())}:
        _fail()
    return pin


def freeze_co_surface_diagnostic(*, output_dir: Path, prepared: dict, pin: dict, expected_pin_sha256: str) -> dict:
    if pin.get("pin_sha256") != expected_pin_sha256 or pin.get("candidate_manifest_sha256") != prepared["candidate_manifest"]["manifest_sha256"]:
        _fail()
    reason_counts: dict[str, int] = {}
    for row in prepared["candidate_manifest"]["rows"]:
        reason_counts[row["reason"]] = reason_counts.get(row["reason"], 0) + 1
    safe = {"contract": CONTRACT, "version": "V1", "rows": 192,
            "counts": deepcopy(prepared["candidate_manifest"]["counts"]), "reason_counts": reason_counts,
            "candidate_manifest_sha256": prepared["candidate_manifest"]["manifest_sha256"], "pin_sha256": expected_pin_sha256,
            "provider_calls": 0}
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    write_create_only(output_dir / "candidate-manifest.json", prepared["candidate_manifest"])
    write_create_only(output_dir / "main-pin.json", pin)
    write_create_only(output_dir / "source-manifest.json", prepared["source_manifest"])
    write_create_only(output_dir / "preflight.json", prepared["preflight"])
    write_create_only(output_dir / "row-inputs.json", {"contract": CONTRACT, "rows": prepared["row_inputs"]})
    write_create_only(output_dir / "witness-candidates.json", {"contract": CONTRACT, "witnesses": prepared["witnesses"]})
    write_create_only(output_dir / "safe-summary.json", safe)
    return safe
