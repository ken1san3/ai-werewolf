"""Main-side, offline reconstruction of the T567 saved-row pin."""
from __future__ import annotations

from pathlib import Path
from dataclasses import asdict

from scripts.phase6_resolved_subject_probe import CONTRACT, SOURCE_ROLES, ResolvedSubjectError, digest, parse, validate_source_root
from scripts import phase6_quality_probe_v2 as q


def _fail():
    raise ResolvedSubjectError("PIN")


def reconstruct_expected_pin(root: Path, manifest: dict, expected_manifest_sha256: str, expected_code_sha256: str):
    """Rebuild roots by cross-linking every saved-run normalized snapshot role."""
    loaded = validate_source_root(root, manifest, expected_manifest_sha256)
    entries = {entry["role"]: entry for entry in manifest["files"]}
    if set(entries) != set(SOURCE_ROLES): _fail()
    saved = parse(loaded[entries["saved_rows"]["source_id"]])
    if type(saved) is not dict or set(saved) != {"contract", "rows"} or saved["contract"] != CONTRACT: _fail()
    saved_row_records = saved["rows"]
    if type(saved_row_records) is not list or len(saved_row_records) != 96: _fail()
    rows = []
    native_by_row = {}
    for record in saved_row_records:
        if type(record) is not dict or set(record) != {"run_row", "native"}: _fail()
        run_row, native = record["run_row"], record["native"]
        if type(run_row) is not dict or type(native) is not dict or set(native) != {"original_name", "member_sha256", "value"}: _fail()
        value = native["value"]
        if type(value) is not dict or set(value) != {"case_id", "elapsed", "final", "plan", "projection_sha256", "sample_exhausted", "seed", "silence", "terminal_stage"}: _fail()
        if value["case_id"] != run_row.get("case_id") or value["seed"] != run_row.get("seed") or digest(value) != native["member_sha256"]: _fail()
        if run_row.get("status") in ("READY", "INPUT_NOT_CHANGED"):
            if value["plan"] != run_row.get("plan"): _fail()
            final = value["final"]
            control = None if final is None else final.get("message") if type(final) is dict and set(final) == {"message"} else _fail()
            if control != run_row.get("control_text"): _fail()
        elif run_row.get("plan") is not None or run_row.get("control_text") is not None:
            _fail()
        rows.append(run_row); native_by_row[run_row.get("row_id")] = native
    ids = [row.get("row_id") for row in rows]
    if any(type(item) is not str or not item for item in ids) or len(ids) != len(set(ids)): _fail()
    statuses = [row.get("status") for row in rows]
    counts = {"rows": 96, "eligible": sum(item in ("READY", "INPUT_NOT_CHANGED") for item in statuses),
              "plan_missing": statuses.count("PLAN_NOT_OBSERVED"), "non_chat": statuses.count("NOT_MESSAGE_DOMAIN")}
    if counts != {"rows": 96, "eligible": 77, "plan_missing": 7, "non_chat": 12}: _fail()
    def collection(role, key):
        value = parse(loaded[entries[role]["source_id"]])
        if type(value) is not dict or set(value) != {"contract", key} or value["contract"] != CONTRACT or type(value[key]) is not list: _fail()
        return value[key]
    request_records = collection("saved_requests", "requests")
    plan_records = collection("saved_plans", "plans")
    outcome_records = collection("saved_outcomes", "outcomes")
    fixture_document = parse(loaded[entries["fixture_inputs"]["source_id"]])
    if type(fixture_document) is not dict or set(fixture_document) != {"contract", "source", "fixtures"} or fixture_document["contract"] != CONTRACT: _fail()
    fixture_source = fixture_document["source"]
    if type(fixture_source) is not dict or set(fixture_source) != {"original_name", "member_sha256", "wire"} or type(fixture_source["wire"]) is not str: _fail()
    fixture_native = parse(fixture_source["wire"].encode())
    if type(fixture_native) is not list or len(fixture_native) != 32: _fail()
    current_fixtures = q.prepare()[0]
    current_native = parse(q.wire([dict(case_id=f.case.case_id, source=f.source, projection_sha256=q.digest(f.source_bytes),
                           catalog=asdict(f.catalog), bindings=f.bindings) for f in current_fixtures]))
    if digest(current_native) != digest(fixture_native): _fail()
    fixture_records = fixture_document["fixtures"]
    if type(fixture_records) is not list: _fail()
    plans = {item.get("row_id"): item for item in plan_records}
    outcomes = {(item.get("row_id"), item.get("ordinal")): item for item in outcome_records}
    fixtures = {item.get("row_id"): item for item in fixture_records}
    if any(len(mapping) != len(values) for mapping, values in ((plans, plan_records), (outcomes, outcome_records), (fixtures, fixture_records))): _fail()
    tmanifest = parse(loaded[entries["t550_manifest"]["source_id"]]); tseal = parse(loaded[entries["t550_seal"]["source_id"]])
    if type(tmanifest) is not dict or set(tmanifest) != {"contract", "members"} or tmanifest["contract"] != CONTRACT or type(tmanifest["members"]) is not list: _fail()
    if type(tseal) is not dict or not tseal or any(type(k) is not str or type(v) is not str or len(v) != 64 for k, v in tseal.items()): _fail()
    member_map = {}
    for item in tmanifest["members"]:
        if type(item) is not dict or set(item) != {"original_name", "sha256"} or type(item["original_name"]) is not str or item["original_name"] in member_map: _fail()
        member_map[item["original_name"]] = item["sha256"]
    if any(tseal.get(name) != sha for name, sha in member_map.items()): _fail()
    if (digest(fixture_source["wire"].encode()) != fixture_source["member_sha256"]
            or member_map.get(fixture_source["original_name"]) != fixture_source["member_sha256"]): _fail()
    for row_id, native in native_by_row.items():
        if member_map.get(native["original_name"]) != native["member_sha256"]: _fail()
    requests = []; controls = []; request_keys = {}
    for item in request_records:
        if type(item) is not dict or set(item) != {"row_id", "ordinal", "original_name", "member_sha256", "wire"}: _fail()
        key = (item["row_id"], item["ordinal"])
        if key in request_keys or item["ordinal"] not in (1, 2) or type(item["wire"]) is not str: _fail()
        if member_map.get(item["original_name"]) != item["member_sha256"] or digest(item["wire"].encode()) != item["member_sha256"]: _fail()
        request_keys[key] = item
    row_ids = set(ids)
    if set(plans) != {row["row_id"] for row in rows if row["status"] in ("READY", "INPUT_NOT_CHANGED")} or set(outcomes) != set(request_keys) or set(fixtures) != row_ids: _fail()
    for row in rows:
        row_id = row["row_id"]
        fixture = fixtures[row_id]
        if type(fixture) is not dict or set(fixture) != {"row_id", "case_id", "seed", "fixture_index", "projection_sha256", "source_sha256", "bindings_sha256", "catalog_sha256"}: _fail()
        index = fixture["fixture_index"]
        if type(index) is not int or not 0 <= index < 32: _fail()
        native_fixture = fixture_native[index]
        if (type(native_fixture) is not dict or set(native_fixture) != {"case_id", "source", "projection_sha256", "catalog", "bindings"}
                or fixture["case_id"] != row.get("case_id") or fixture["seed"] != row.get("seed")
                or native_by_row[row_id]["value"]["projection_sha256"] != fixture["projection_sha256"]
                or native_fixture["case_id"] != fixture["case_id"] or native_fixture["projection_sha256"] != fixture["projection_sha256"]
                or digest(native_fixture["source"]) != fixture["source_sha256"]
                or digest(native_fixture["bindings"]) != fixture["bindings_sha256"]
                or digest(native_fixture["catalog"]) != fixture["catalog_sha256"]): _fail()
        if row.get("status") in ("READY", "INPUT_NOT_CHANGED"):
            if type(row.get("control_ordinal1")) is not str: _fail()
            plan = plans[row_id]
            if type(plan) is not dict or set(plan) != {"row_id", "plan", "plan_sha256", "original_name", "member_sha256"}: _fail()
            native = native_by_row[row_id]
            if (plan["plan"] != row.get("plan") or digest(plan["plan"]) != plan["plan_sha256"]
                    or plan["original_name"] != native["original_name"] or plan["member_sha256"] != native["member_sha256"]
                    or member_map.get(plan["original_name"]) != plan["member_sha256"]): _fail()
            if (row_id, 1) not in request_keys or request_keys[row_id, 1]["wire"] != row["control_ordinal1"]: _fail()
            requests.append(digest(row["control_ordinal1"].encode()))
            if row.get("saved_ordinal2") is not None:
                if type(row["saved_ordinal2"]) is not str: _fail()
                if (row_id, 2) not in request_keys or request_keys[row_id, 2]["wire"] != row["saved_ordinal2"]: _fail()
                requests.append(digest(row["saved_ordinal2"].encode()))
            for key in tuple(k for k in outcomes if k[0] == row_id):
                outcome = outcomes[key]
                if type(outcome) is not dict or set(outcome) != {"row_id", "ordinal", "status", "original_name", "member_sha256", "value"}: _fail()
                if (outcome["ordinal"] != key[1] or type(outcome["value"]) is not dict
                        or outcome["status"] not in ("ACCEPTED", "GUARD_REJECT")
                        or outcome["value"].get("status") != outcome["status"]
                        or digest(outcome["value"]) != outcome["member_sha256"]
                        or member_map.get(outcome["original_name"]) != outcome["member_sha256"]): _fail()
            if row.get("control_text") is not None: controls.append(digest(row["control_text"].encode()))
    if len(requests) != 88: _fail()
    if len(request_keys) != 88: _fail()
    counts["saved_requests"] = 88
    payload = {"contract": CONTRACT, "source_sha256": expected_manifest_sha256,
               "code_sha256": expected_code_sha256, "rows_sha256": digest(rows),
               "saved_request_sha256": digest(requests), "control_observation_sha256": digest(controls),
               "counts": counts}
    return {**payload, "pin_sha256": digest(payload)}
