"""Offline saved-control calibration contract for T575.

The helper deliberately has no provider/runtime entry point.  It rebuilds every
row from the frozen T567 physical bundle before creating the evaluator view.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from functools import lru_cache
import json
import os

from scripts import phase6_resolved_subject_probe as probe
from scripts.phase6_resolved_subject_custody import reconstruct_expected_pin
from tests.fixtures import phase6_plan_locked_rubric as rubric


AUTHORITY_CONTRACT = "PHASE6_SAVED_CONTROL_AUTHORITY_PIN_V1"
SOURCE_ROLES_CONTRACT = "PHASE6_SAVED_CONTROL_SOURCE_ROLES_ROOT_V1"
MEMBERS_CONTRACT = "PHASE6_SAVED_CONTROL_MEMBERS_ROOT_V1"
INTENT_CONTRACT = "PHASE6_SAVED_CONTROL_CHOSEN_INTENT_ROOT_V1"
FIXTURE_CONTRACT = "PHASE6_SAVED_CONTROL_FIXTURE_BINDINGS_ROOT_V1"
POPULATION_CONTRACT = "PHASE6_SAVED_CONTROL_POPULATION_ROOT_V1"
CODE_BUNDLE_CONTRACT = "PHASE6_SAVED_CONTROL_CODE_BUNDLE_V1"
SOURCE_PACKET_CONTRACT = "PHASE6_SAVED_CONTROL_SOURCE_PACKET_V1"
MAPPING_CONTRACT = "PHASE6_SAVED_CONTROL_PRIVATE_MAPPING_V1"
PACKET_FREEZE_CONTRACT = "PHASE6_SAVED_CONTROL_PACKET_FREEZE_V1"
ANNOTATION_FREEZE_CONTRACT = "PHASE6_SAVED_CONTROL_ANNOTATION_FREEZE_V1"
SUMMARY_CONTRACT = "PHASE6_SAVED_CONTROL_SUMMARY_V1"
MODE = "SAVED_CONTROL_WITH_NOT_MEASURED"
POPULATIONS = ("TARGET", "PLAN_NOT_OBSERVED", "NOT_MESSAGE_DOMAIN")

CODE_FILES = (
    ("SAVED_CONTROL_HELPER", "tests/fixtures/phase6_saved_control_plan_locked.py"),
    ("MAIN_CUSTODY", "tests/fixtures/phase6_saved_control_plan_locked_custody.py"),
    ("T567_PROBE", "scripts/phase6_resolved_subject_probe.py"),
    ("T567_CUSTODY", "scripts/phase6_resolved_subject_custody.py"),
    ("T570_RUBRIC", "tests/fixtures/phase6_plan_locked_rubric.py"),
    ("T574_DESIGN", "Docs/ai/design/PHASE6_SAVED_CONTROL_PLAN_LOCKED_DESIGN.md"),
    ("T574_REVIEW", "Docs/ai/handoffs/tasks/T574_SAVED_CONTROL_PLAN_LOCKED_DESIGN_REVIEW.md"),
)


class SavedControlInputError(ValueError):
    def __init__(self):
        super().__init__("SAVED_CONTROL_INPUT_INTEGRITY")


def fail():
    raise SavedControlInputError()


def digest(value):
    try:
        return rubric.digest(value)
    except rubric.RubricInputError:
        fail()


def _hex(value):
    return rubric.hex64(value)


def _strict_int(value, minimum=0):
    return type(value) is int and value >= minimum


def _read_json(path: Path):
    try:
        return rubric.parse(path.read_bytes())
    except (OSError, rubric.RubricInputError):
        fail()


def _safe_path(root: Path, relative):
    if type(relative) is not str or not relative or "\\" in relative:
        fail()
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        fail()
    path = root.joinpath(*pure.parts)
    try:
        resolved = path.resolve(strict=True)
        base = root.resolve(strict=True)
        metadata = os.lstat(path)
    except OSError:
        fail()
    if resolved != path.absolute() or base not in resolved.parents or path.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400 or not path.is_file():
        fail()
    return path


def code_bundle(repository_root: Path | None = None):
    root = Path(repository_root) if repository_root is not None else Path(__file__).resolve().parents[2]
    files = []
    for role, name in CODE_FILES:
        path = _safe_path(root, name)
        raw = path.read_bytes()
        files.append({"role": role, "repository_path": name, "sha256": digest(raw), "size": len(raw)})
    payload = {"contract": CODE_BUNDLE_CONTRACT, "files": files}
    return {**payload, "bundle_sha256": digest(payload)}


def _physical_ref(role, path: Path, root: Path):
    raw = path.read_bytes()
    return {"role": role, "relative_name": path.relative_to(root).as_posix(), "sha256": digest(raw), "size": len(raw)}


def _load_locator(owned_root: Path, prepared_locator):
    root = Path(owned_root).absolute()
    locator_path = Path(prepared_locator).absolute()
    if locator_path.parent != root:
        fail()
    locator = _read_json(locator_path)
    keys = {"contract", "input", "preflight", "source_manifest", "main_pin", "old_t550_seal", "source_root"}
    if type(locator) is not dict or set(locator) != keys or locator["contract"] != "PHASE6_SAVED_CONTROL_PREPARED_LOCATOR_V1":
        fail()
    paths = {key: _safe_path(root, locator[key]) for key in ("input", "preflight", "source_manifest", "main_pin", "old_t550_seal")}
    source_root = root.joinpath(*PurePosixPath(locator["source_root"]).parts)
    try:
        if source_root.resolve(strict=True) != source_root.absolute() or not source_root.is_dir():
            fail()
    except OSError:
        fail()
    return root, locator_path, paths, source_root


def _locator(row, native):
    value = {"condition": "CONTROL", "row_id": row["row_id"], "case_id": row["case_id"], "seed": row["seed"],
             "ordinal": 1, "source_role": "saved_rows", "member_name": native["original_name"],
             "member_sha256": native["member_sha256"], "json_pointer": "/final/message", "encoding": "JSON_STRING_UTF8"}
    return {**value, "locator_sha256": digest(value)}


@lru_cache(maxsize=1)
def _current_fixture_bundle():
    fixtures = probe.q.prepare()[0]
    native = probe.parse(probe.q.wire([dict(case_id=f.case.case_id, source=f.source,
        projection_sha256=probe.q.digest(f.source_bytes), catalog=asdict(f.catalog), bindings=f.bindings) for f in fixtures]))
    return fixtures, native


def _reconstruct_physical(owned_root, prepared_locator, *, expected_prepared_locator_file_sha256,
                          expected_input_file_sha256, expected_preflight_file_sha256,
                          expected_source_manifest_file_sha256, expected_main_pin_file_sha256,
                          expected_old_t550_seal_file_sha256, expected_code_bundle_sha256):
    expected = (expected_prepared_locator_file_sha256, expected_input_file_sha256, expected_preflight_file_sha256,
                expected_source_manifest_file_sha256, expected_main_pin_file_sha256,
                expected_old_t550_seal_file_sha256, expected_code_bundle_sha256)
    if not all(_hex(x) for x in expected):
        fail()
    root, locator_path, paths, source_root = _load_locator(Path(owned_root), prepared_locator)
    physical = [locator_path, paths["input"], paths["preflight"], paths["source_manifest"], paths["main_pin"], paths["old_t550_seal"]]
    if tuple(digest(path.read_bytes()) for path in physical) != expected[:6]:
        fail()
    current_bundle = code_bundle()
    if current_bundle["bundle_sha256"] != expected_code_bundle_sha256:
        fail()
    bundle = _read_json(paths["input"])
    preflight = _read_json(paths["preflight"])
    manifest = _read_json(paths["source_manifest"])
    main_pin = _read_json(paths["main_pin"])
    old_seal = _read_json(paths["old_t550_seal"])
    if bundle.get("preflight") != preflight or bundle.get("source_manifest") != manifest or bundle.get("main_pin") != main_pin:
        fail()
    try:
        probe._run_bundle(bundle, preflight["preflight_sha256"], manifest["manifest_sha256"], main_pin["pin_sha256"])
        loaded = probe.validate_source_root(source_root, manifest, manifest["manifest_sha256"])
        probe.validate_identity_roles(source_root, manifest, manifest["manifest_sha256"], preflight)
        rebuilt_pin = reconstruct_expected_pin(source_root, manifest, manifest["manifest_sha256"], digest(Path(probe.__file__).read_bytes()))
    except (probe.ResolvedSubjectError, KeyError, TypeError):
        fail()
    if rebuilt_pin != main_pin:
        fail()
    entries = {entry["role"]: entry for entry in manifest["files"]}
    if tuple(entries) != tuple(entry["role"] for entry in manifest["files"]) or set(entries) != set(probe.SOURCE_ROLES):
        fail()
    if loaded[entries["t550_seal"]["source_id"]] != paths["old_t550_seal"].read_bytes() or old_seal != probe.parse(loaded[entries["t550_seal"]["source_id"]]):
        fail()
    saved_doc = probe.parse(loaded[entries["saved_rows"]["source_id"]])
    plan_doc = probe.parse(loaded[entries["saved_plans"]["source_id"]])
    fixture_doc = probe.parse(loaded[entries["fixture_inputs"]["source_id"]])
    manifest_doc = probe.parse(loaded[entries["t550_manifest"]["source_id"]])
    if (set(saved_doc) != {"contract", "rows"} or set(plan_doc) != {"contract", "plans"}
            or set(fixture_doc) != {"contract", "source", "fixtures"} or set(manifest_doc) != {"contract", "members"}):
        fail()
    saved = {item["run_row"]["row_id"]: item for item in saved_doc["rows"]}
    plans = {item["row_id"]: item for item in plan_doc["plans"]}
    fixtures = {item["row_id"]: item for item in fixture_doc["fixtures"]}
    members = {item["original_name"]: item["sha256"] for item in manifest_doc["members"]}
    if any(len(x) != len(y) for x, y in ((saved, saved_doc["rows"]), (plans, plan_doc["plans"]), (fixtures, fixture_doc["fixtures"]), (members, manifest_doc["members"]))):
        fail()
    native_fixtures = probe.parse(fixture_doc["source"]["wire"].encode())
    current_fixtures, current_native = _current_fixture_bundle()
    if native_fixtures != current_native or len(native_fixtures) != 32:
        fail()

    source_roles = []
    for role in probe.SOURCE_ROLES:
        entry = entries[role]
        source_roles.append({"role": role, "source_id": entry["source_id"], "original_name": entry["original_name"],
                             "sha256": entry["sha256"], "size": entry["size"]})
    roles_payload = {"contract": SOURCE_ROLES_CONTRACT,
        "prepared_input": _physical_ref("prepared_input", paths["input"], root),
        "preflight": _physical_ref("preflight", paths["preflight"], root),
        "source_manifest": _physical_ref("source_manifest", paths["source_manifest"], root),
        "main_pin": _physical_ref("main_pin", paths["main_pin"], root),
        "old_t550_seal": _physical_ref("old_t550_seal", paths["old_t550_seal"], root), "roles": source_roles}

    row_bindings = []; member_bindings = []; intent_bindings = []; fixture_bindings = []; semantic = []
    for ordinal, row in enumerate(bundle["rows"]):
        if type(row) is not dict or row.get("row_id") not in saved or row.get("row_id") not in fixtures:
            fail()
        row_id = row["row_id"]; record = saved[row_id]; native = record["native"]; value = native["value"]
        if record["run_row"] != row or not _strict_int(row.get("seed")) or type(row.get("question_opportunity")) is not bool:
            fail()
        fixture = fixtures[row_id]
        index = fixture.get("fixture_index")
        if not _strict_int(index) or index >= len(current_fixtures) or fixture.get("case_id") != row.get("case_id") or fixture.get("seed") != row.get("seed"):
            fail()
        native_fixture = current_fixtures[index]
        if native_fixture.case.case_id != row["case_id"] or fixture.get("projection_sha256") != probe.q.digest(native_fixture.source_bytes):
            fail()
        status = "TARGET" if row.get("status") in ("READY", "INPUT_NOT_CHANGED") else row.get("status")
        if status not in POPULATIONS:
            fail()
        final = value.get("final")
        text = final.get("message") if type(final) is dict and set(final) == {"message"} else None
        control_locator = _locator(row, native) if status == "TARGET" and type(text) is str and text else None
        if status != "TARGET" and control_locator is not None:
            fail()
        parsed_final_sha = digest(text) if control_locator is not None else None
        member_binding = {"ordinal": ordinal, "row_id": row_id, "saved_rows_source_id": entries["saved_rows"]["source_id"],
            "original_name": native["original_name"], "manifest_member_sha256": members.get(native["original_name"]),
            "old_seal_member_sha256": old_seal.get(native["original_name"]), "embedded_member_sha256": native["member_sha256"],
            "parsed_final_sha256": parsed_final_sha, "control_locator": control_locator}
        if not all(member_binding[k] == native["member_sha256"] for k in ("manifest_member_sha256", "old_seal_member_sha256", "embedded_member_sha256")):
            fail()
        fixture_binding = {"ordinal": ordinal, "row_id": row_id, "case_id": row["case_id"], "seed": row["seed"],
            "fixture_index": index, "fixture_source_id": entries["fixture_inputs"]["source_id"],
            "fixture_original_name": fixture_doc["source"]["original_name"],
            "fixture_member_sha256": fixture_doc["source"]["member_sha256"], "fixture_binding_sha256": digest(fixture),
            "population_status": status, "question_opportunity": row["question_opportunity"]}
        if status == "TARGET":
            plan = plans.get(row_id)
            if plan is None or plan.get("plan") != row.get("plan") or plan.get("original_name") != native["original_name"]:
                fail()
            chosen = probe.chosen_intent(native_fixture, row["plan"], f"case_{ordinal:032x}")
            intent_binding = {"ordinal": ordinal, "row_id": row_id, "saved_plan_original_name": plan["original_name"],
                "saved_plan_member_sha256": plan["member_sha256"], "plan_pointer": "/plan", "plan_value_sha256": digest(plan["plan"]),
                "reply_source_sha256": digest(chosen["reply"]) if chosen["reply"] is not None else None,
                "public_trigger_source_sha256": digest(chosen["public_trigger"]),
                "current_public_state_source_sha256": digest(chosen["current_public_state"]),
                "chosen_intent_observation_sha256": chosen["observation_sha256"]}
        else:
            chosen = None
            intent_binding = {"ordinal": ordinal, "row_id": row_id, "saved_plan_original_name": None,
                "saved_plan_member_sha256": None, "plan_pointer": None, "plan_value_sha256": None,
                "reply_source_sha256": None, "public_trigger_source_sha256": None,
                "current_public_state_source_sha256": None, "chosen_intent_observation_sha256": None}
        row_binding = {"ordinal": ordinal, "row_id": row_id, "case_id": row["case_id"], "seed": row["seed"],
            "population_status": status, "prepared_row_sha256": digest(row), "native_member_sha256": native["member_sha256"],
            "fixture_binding_sha256": digest(fixture_binding), "chosen_intent_binding_sha256": digest(intent_binding),
            "control_locator": control_locator}
        row_binding["row_binding_sha256"] = digest(row_binding)
        row_bindings.append(row_binding); member_bindings.append(member_binding); intent_bindings.append(intent_binding); fixture_bindings.append(fixture_binding)
        semantic.append({"ordinal": ordinal, "row_id": row_id, "population_status": status, "chosen_intent": chosen,
                         "question_opportunity": row["question_opportunity"], "control_text": text,
                         "control_locator": control_locator, "row_binding_sha256": row_binding["row_binding_sha256"]})
    if [x["population_status"] for x in row_bindings].count("TARGET") != 77 or sum(x["control_locator"] is not None for x in row_bindings) != 69 or sum(x["question_opportunity"] for x in fixture_bindings) != 54:
        fail()
    members_payload = {"contract": MEMBERS_CONTRACT, "members": member_bindings}
    intents_payload = {"contract": INTENT_CONTRACT, "bindings": intent_bindings}
    fixtures_payload = {"contract": FIXTURE_CONTRACT, "bindings": fixture_bindings}
    roots = {"source_roles_root_sha256": digest(roles_payload), "saved_control_members_root_sha256": digest(members_payload),
             "chosen_intent_bindings_root_sha256": digest(intents_payload), "fixture_bindings_root_sha256": digest(fixtures_payload)}
    population = {"contract": POPULATION_CONTRACT, "rows": row_bindings, "rows_sha256": digest(row_bindings), **roots}
    result = {"root": root, "locator_path": locator_path, "paths": paths, "source_root": source_root, "bundle": bundle,
              "preflight": preflight, "manifest": manifest, "main_pin": main_pin, "old_seal": old_seal,
              "roles_payload": roles_payload, "members_payload": members_payload, "intents_payload": intents_payload,
              "fixtures_payload": fixtures_payload, "population": population, "semantic": semantic,
              "code_bundle": current_bundle}
    return result


def _validate_authority(pin, physical, expected_pin):
    keys = {"contract", "prepared_locator_file_sha256", "input_file_sha256", "preflight_file_sha256",
            "source_manifest_file_sha256", "main_pin_file_sha256", "old_t550_seal_file_sha256",
            "source_roles_root_sha256", "saved_control_members_root_sha256", "chosen_intent_bindings_root_sha256",
            "fixture_bindings_root_sha256", "code_bundle_sha256", "expected_population_root_sha256", "pin_sha256"}
    if type(pin) is not dict or set(pin) != keys or pin["contract"] != AUTHORITY_CONTRACT or pin["pin_sha256"] != expected_pin:
        fail()
    if digest({k: pin[k] for k in pin if k != "pin_sha256"}) != expected_pin:
        fail()
    expected = {"prepared_locator_file_sha256": digest(physical["locator_path"].read_bytes()),
        "input_file_sha256": digest(physical["paths"]["input"].read_bytes()), "preflight_file_sha256": digest(physical["paths"]["preflight"].read_bytes()),
        "source_manifest_file_sha256": digest(physical["paths"]["source_manifest"].read_bytes()), "main_pin_file_sha256": digest(physical["paths"]["main_pin"].read_bytes()),
        "old_t550_seal_file_sha256": digest(physical["paths"]["old_t550_seal"].read_bytes()),
        "source_roles_root_sha256": digest(physical["roles_payload"]), "saved_control_members_root_sha256": digest(physical["members_payload"]),
        "chosen_intent_bindings_root_sha256": digest(physical["intents_payload"]), "fixture_bindings_root_sha256": digest(physical["fixtures_payload"]),
        "code_bundle_sha256": physical["code_bundle"]["bundle_sha256"], "expected_population_root_sha256": digest(physical["population"])}
    if any(pin[k] != value for k, value in expected.items()):
        fail()


def build_saved_control_packet(owned_root, prepared_locator, authority_pin, *, expected_authority_pin_sha256,
        expected_source_roles_root_sha256, expected_saved_control_members_root_sha256,
        expected_chosen_intent_bindings_root_sha256, expected_fixture_bindings_root_sha256,
        expected_population_root_sha256, expected_code_bundle_sha256, expected_rubric_sha256, rng):
    for value in (expected_authority_pin_sha256, expected_source_roles_root_sha256, expected_saved_control_members_root_sha256,
                  expected_chosen_intent_bindings_root_sha256, expected_fixture_bindings_root_sha256,
                  expected_population_root_sha256, expected_code_bundle_sha256, expected_rubric_sha256):
        if not _hex(value): fail()
    physical = _reconstruct_physical(owned_root, prepared_locator,
        expected_prepared_locator_file_sha256=authority_pin.get("prepared_locator_file_sha256"),
        expected_input_file_sha256=authority_pin.get("input_file_sha256"), expected_preflight_file_sha256=authority_pin.get("preflight_file_sha256"),
        expected_source_manifest_file_sha256=authority_pin.get("source_manifest_file_sha256"), expected_main_pin_file_sha256=authority_pin.get("main_pin_file_sha256"),
        expected_old_t550_seal_file_sha256=authority_pin.get("old_t550_seal_file_sha256"), expected_code_bundle_sha256=expected_code_bundle_sha256)
    _validate_authority(authority_pin, physical, expected_authority_pin_sha256)
    roots = (digest(physical["roles_payload"]), digest(physical["members_payload"]), digest(physical["intents_payload"]), digest(physical["fixtures_payload"]), digest(physical["population"]))
    if roots != (expected_source_roles_root_sha256, expected_saved_control_members_root_sha256,
                 expected_chosen_intent_bindings_root_sha256, expected_fixture_bindings_root_sha256, expected_population_root_sha256):
        fail()
    rows = deepcopy(physical["semantic"])
    try:
        rng.shuffle(rows)
    except Exception:
        fail()
    public_rows = []; mapping_rows = []; binding_rows = []
    for blind_ordinal, row in enumerate(rows):
        blind_id = f"blind_{rng.getrandbits(128):032x}"
        control_on_a = bool(rng.getrandbits(1))
        a_condition, b_condition = (("CONTROL", "NOT_MEASURED") if control_on_a else ("NOT_MEASURED", "CONTROL"))
        observation = None
        if row["population_status"] == "TARGET" and row["control_locator"] is not None:
            binding = {"parsed_result_sha256": digest(row["control_text"]), "member_sha256": row["control_locator"]["member_sha256"],
                       "locator_sha256": row["control_locator"]["locator_sha256"]}
            body = {"result_status": "ACCEPTED", "text_observation": "OBSERVED", "text": row["control_text"], "text_binding": binding}
            observation = {**body, "observation_sha256": digest(body)}
        sides = {"CONTROL": observation, "NOT_MEASURED": None}
        public_rows.append({"blind_id": blind_id, "population_status": row["population_status"],
            "side_observations": [{"side": "A", "observation": sides[a_condition]}, {"side": "B", "observation": sides[b_condition]}],
            "chosen_intent": row["chosen_intent"], "question_opportunity": row["question_opportunity"]})
        mrow = {"blind_id": blind_id, "A_condition": a_condition, "B_condition": b_condition}
        mrow["condition_permutation_sha256"] = digest(mrow); mapping_rows.append(mrow)
        binding_rows.append({"blind_ordinal": blind_ordinal, "blind_id": blind_id, "source_ordinal": row["ordinal"],
                             "row_id": row["row_id"], "row_binding_sha256": row["row_binding_sha256"]})
    packet_binding = {"contract": "PHASE6_SAVED_CONTROL_PACKET_BINDING_V1", "authority_pin_sha256": expected_authority_pin_sha256,
        "source_manifest_sha256": physical["manifest"]["manifest_sha256"], "population_sha256": expected_population_root_sha256,
        "code_bundle_sha256": expected_code_bundle_sha256, "rows": binding_rows}
    packet_binding["packet_binding_sha256"] = digest({k: packet_binding[k] for k in packet_binding if k != "packet_binding_sha256"})
    source_packet = {"contract": SOURCE_PACKET_CONTRACT, "mode": MODE, "rubric_sha256": expected_rubric_sha256,
        "population_sha256": expected_population_root_sha256, "packet_binding_sha256": packet_binding["packet_binding_sha256"], "rows": public_rows}
    view = {"contract": rubric.CONTRACT, "rubric_sha256": expected_rubric_sha256, "population_sha256": expected_population_root_sha256,
            "packet_binding_sha256": packet_binding["packet_binding_sha256"], "rows": deepcopy(public_rows)}
    mapping = {"contract": MAPPING_CONTRACT, "source_packet_sha256": digest(source_packet), "rows": mapping_rows}
    mapping["mapping_sha256"] = digest({k: mapping[k] for k in mapping if k != "mapping_sha256"})
    rubric.validate_packet(view, expected_packet_sha256=digest(view), expected_rubric_sha256=expected_rubric_sha256,
                           expected_population_sha256=expected_population_root_sha256, expected_binding_sha256=packet_binding["packet_binding_sha256"])
    validate_saved_control_mapping(source_packet, mapping, expected_source_packet_sha256=digest(source_packet), expected_mapping_sha256=mapping["mapping_sha256"])
    return source_packet, view, mapping, packet_binding


def validate_saved_control_mapping(source_packet, mapping, *, expected_source_packet_sha256, expected_mapping_sha256):
    if not _hex(expected_source_packet_sha256) or not _hex(expected_mapping_sha256) or digest(source_packet) != expected_source_packet_sha256:
        fail()
    if type(source_packet) is not dict or set(source_packet) != {"contract", "mode", "rubric_sha256", "population_sha256", "packet_binding_sha256", "rows"} or source_packet["contract"] != SOURCE_PACKET_CONTRACT or source_packet["mode"] != MODE:
        fail()
    if type(mapping) is not dict or set(mapping) != {"contract", "source_packet_sha256", "rows", "mapping_sha256"} or mapping["contract"] != MAPPING_CONTRACT:
        fail()
    if mapping["source_packet_sha256"] != expected_source_packet_sha256 or mapping["mapping_sha256"] != expected_mapping_sha256 or digest({k: mapping[k] for k in mapping if k != "mapping_sha256"}) != expected_mapping_sha256:
        fail()
    rows = mapping["rows"]; packet_rows = source_packet["rows"]
    if type(rows) is not list or len(rows) != 96 or type(packet_rows) is not list or len(packet_rows) != 96:
        fail()
    ids = []
    for prow, mrow in zip(packet_rows, rows):
        if type(mrow) is not dict or set(mrow) != {"blind_id", "A_condition", "B_condition", "condition_permutation_sha256"} or mrow["blind_id"] != prow.get("blind_id"):
            fail()
        if mrow["blind_id"] in ids or {mrow["A_condition"], mrow["B_condition"]} != {"CONTROL", "NOT_MEASURED"}:
            fail()
        ids.append(mrow["blind_id"])
        if mrow["condition_permutation_sha256"] != digest({k: mrow[k] for k in ("blind_id", "A_condition", "B_condition")}):
            fail()
    return deepcopy(mapping)


def _validate_packet_inputs(source_packet, view, mapping, packet_binding, *, expected_authority_pin_sha256,
        expected_source_manifest_sha256, expected_population_sha256, expected_mapping_sha256,
        expected_rubric_sha256, expected_source_packet_sha256, expected_packet_binding_sha256,
        expected_semantic_packet_view_sha256):
    values = (expected_authority_pin_sha256, expected_source_manifest_sha256, expected_population_sha256,
              expected_mapping_sha256, expected_rubric_sha256, expected_source_packet_sha256,
              expected_packet_binding_sha256, expected_semantic_packet_view_sha256)
    if not all(_hex(x) for x in values): fail()
    if type(packet_binding) is not dict or packet_binding.get("packet_binding_sha256") != expected_packet_binding_sha256:
        fail()
    if digest({k: packet_binding[k] for k in packet_binding if k != "packet_binding_sha256"}) != expected_packet_binding_sha256:
        fail()
    if (packet_binding.get("authority_pin_sha256") != expected_authority_pin_sha256
            or packet_binding.get("source_manifest_sha256") != expected_source_manifest_sha256
            or packet_binding.get("population_sha256") != expected_population_sha256): fail()
    if digest(source_packet) != expected_source_packet_sha256 or digest(view) != expected_semantic_packet_view_sha256:
        fail()
    if source_packet.get("rows") != view.get("rows") or source_packet.get("rubric_sha256") != expected_rubric_sha256 or source_packet.get("population_sha256") != expected_population_sha256 or source_packet.get("packet_binding_sha256") != expected_packet_binding_sha256:
        fail()
    rubric.validate_packet(view, expected_packet_sha256=expected_semantic_packet_view_sha256,
        expected_rubric_sha256=expected_rubric_sha256, expected_population_sha256=expected_population_sha256,
        expected_binding_sha256=expected_packet_binding_sha256)
    validate_saved_control_mapping(source_packet, mapping, expected_source_packet_sha256=expected_source_packet_sha256,
                                   expected_mapping_sha256=expected_mapping_sha256)
    for row, mrow in zip(view["rows"], mapping["rows"]):
        for side_name, condition in (("A", mrow["A_condition"]), ("B", mrow["B_condition"])):
            observation = row["side_observations"][0 if side_name == "A" else 1]["observation"]
            if condition == "NOT_MEASURED" and observation is not None: fail()
    return rubric.validate_packet(view, expected_packet_sha256=expected_semantic_packet_view_sha256,
        expected_rubric_sha256=expected_rubric_sha256, expected_population_sha256=expected_population_sha256,
        expected_binding_sha256=expected_packet_binding_sha256)


def freeze_saved_control_packet(source_packet, semantic_packet_view, mapping, packet_binding, **expected):
    data = _validate_packet_inputs(source_packet, semantic_packet_view, mapping, packet_binding, **expected)
    bodies = data["bodies"]; a = sum(x[0] for x in bodies); b = sum(x[1] for x in bodies)
    payload = {"contract": PACKET_FREEZE_CONTRACT, "version": 1, "mode": MODE,
        "source_manifest_sha256": expected["expected_source_manifest_sha256"], "authority_pin_sha256": expected["expected_authority_pin_sha256"],
        "population_sha256": expected["expected_population_sha256"], "mapping_sha256": expected["expected_mapping_sha256"],
        "rubric_sha256": expected["expected_rubric_sha256"], "source_packet_sha256": expected["expected_source_packet_sha256"],
        "packet_binding_sha256": expected["expected_packet_binding_sha256"], "semantic_packet_view_sha256": expected["expected_semantic_packet_view_sha256"],
        "rows": 96, "target_rows": 77, "non_target_rows": 19, "control_observed_rows": 69, "control_missing_rows": 8,
        "not_measured_observed_rows": 0, "side_a_body_rows": a, "side_b_body_rows": b, "both_body_rows": bodies.count((True, True)),
        "side_a_only_body_rows": bodies.count((True, False)), "side_b_only_body_rows": bodies.count((False, True)),
        "neither_body_rows": bodies.count((False, False)), "question_opportunities": 54,
        "question_target_rows": data["question_counts"]["TARGET"], "question_plan_missing_rows": data["question_counts"]["PLAN_NOT_OBSERVED"],
        "question_non_message_rows": data["question_counts"]["NOT_MESSAGE_DOMAIN"]}
    if payload["both_body_rows"] != 0 or payload["side_a_only_body_rows"] + payload["side_b_only_body_rows"] != 69 or payload["neither_body_rows"] != 8:
        fail()
    return {**payload, "freeze_sha256": digest(payload)}


def _annotation_counts(annotation):
    pairs = {metric: [f"{value}:{reason}" for value, reason in (("PASS", rubric.POSITIVE[metric]), ("FAIL", rubric.NEGATIVE[metric]),
        ("UNKNOWN", "CONTEXT_INSUFFICIENT"), ("MEASUREMENT_NOT_OBSERVED", "OBSERVATION_MISSING"), ("NOT_APPLICABLE", "METRIC_NOT_APPLICABLE"))] for metric in rubric.METRICS}
    counts = {side: {metric: {pair: 0 for pair in pairs[metric]} for metric in rubric.METRICS} for side in ("A", "B")}
    for row in annotation["rows"]:
        for metric in row["metrics"]:
            for side, key in (("A", "side_a"), ("B", "side_b")):
                item = metric[key]; counts[side][metric["metric"]][f'{item["value"]}:{item["reason"]}'] += 1
    return counts


def _annotation_expected(packet_freeze, **expected):
    rebuilt = freeze_saved_control_packet(expected.pop("source_packet"), expected.pop("semantic_packet_view"),
        expected.pop("mapping"), expected.pop("packet_binding"), **expected)
    if rebuilt != packet_freeze or packet_freeze.get("freeze_sha256") != expected.pop("expected_packet_freeze_sha256", None):
        fail()


def freeze_saved_control_annotation(annotation, source_packet, semantic_packet_view, mapping, packet_freeze, *, evaluator_identity,
        expected_evaluator_identity_sha256, expected_packet_freeze_sha256, expected_authority_pin_sha256,
        expected_source_manifest_sha256, expected_population_sha256, expected_mapping_sha256, expected_rubric_sha256,
        expected_source_packet_sha256, expected_packet_binding_sha256, expected_semantic_packet_view_sha256):
    packet_binding = {"contract": "PHASE6_SAVED_CONTROL_PACKET_BINDING_V1", "authority_pin_sha256": expected_authority_pin_sha256,
        "source_manifest_sha256": expected_source_manifest_sha256, "population_sha256": expected_population_sha256,
        "code_bundle_sha256": packet_freeze.get("code_bundle_sha256", "0" * 64), "rows": []}
    # The original binding object is intentionally not reconstructed from caller rows.
    # Its externally pinned digest and authority fields are checked through the packet freeze below.
    expected_freeze = freeze_saved_control_packet_from_frozen_inputs(source_packet, semantic_packet_view, mapping, packet_freeze,
        expected_packet_freeze_sha256=expected_packet_freeze_sha256, expected_authority_pin_sha256=expected_authority_pin_sha256,
        expected_source_manifest_sha256=expected_source_manifest_sha256, expected_population_sha256=expected_population_sha256,
        expected_mapping_sha256=expected_mapping_sha256, expected_rubric_sha256=expected_rubric_sha256,
        expected_source_packet_sha256=expected_source_packet_sha256, expected_packet_binding_sha256=expected_packet_binding_sha256,
        expected_semantic_packet_view_sha256=expected_semantic_packet_view_sha256)
    try:
        rubric.validate_annotation(annotation, semantic_packet_view, expected_freeze, evaluator_identity=evaluator_identity,
                                   expected_evaluator_identity_sha256=expected_evaluator_identity_sha256)
    except rubric.RubricInputError:
        fail()
    _validate_annotation_mapping(annotation, semantic_packet_view, mapping)
    counts = _annotation_counts(annotation)
    payload = {"contract": ANNOTATION_FREEZE_CONTRACT, "version": 1, "mode": MODE,
        "source_packet_sha256": expected_source_packet_sha256, "semantic_packet_view_sha256": expected_semantic_packet_view_sha256,
        "rubric_sha256": expected_rubric_sha256, "packet_freeze_sha256": expected_packet_freeze_sha256,
        "authority_pin_sha256": expected_authority_pin_sha256, "source_manifest_sha256": expected_source_manifest_sha256,
        "annotation_sha256": digest(annotation), "annotation_rows_sha256": digest(annotation["rows"]),
        "evaluator_identity_sha256": expected_evaluator_identity_sha256, "rows": 96, "annotated_rows": 77, "not_required_rows": 19,
        "side_a_metric_value_reason_counts": counts["A"], "side_b_metric_value_reason_counts": counts["B"],
        "both_body_rows": 0, "side_a_only_body_rows": packet_freeze["side_a_only_body_rows"],
        "side_b_only_body_rows": packet_freeze["side_b_only_body_rows"], "neither_body_rows": 8,
        "question_opportunities": 54, "fresh_independent_evaluator": True}
    return {**payload, "freeze_sha256": digest(payload)}


def freeze_saved_control_packet_from_frozen_inputs(source_packet, view, mapping, freeze, *, expected_packet_freeze_sha256, **expected):
    if freeze.get("freeze_sha256") != expected_packet_freeze_sha256 or freeze.get("authority_pin_sha256") != expected["expected_authority_pin_sha256"]:
        fail()
    if freeze.get("source_manifest_sha256") != expected["expected_source_manifest_sha256"] or freeze.get("population_sha256") != expected["expected_population_sha256"]:
        fail()
    if freeze.get("mapping_sha256") != expected["expected_mapping_sha256"] or freeze.get("rubric_sha256") != expected["expected_rubric_sha256"]:
        fail()
    if freeze.get("source_packet_sha256") != expected["expected_source_packet_sha256"] or freeze.get("packet_binding_sha256") != expected["expected_packet_binding_sha256"] or freeze.get("semantic_packet_view_sha256") != expected["expected_semantic_packet_view_sha256"]:
        fail()
    validate_saved_control_mapping(source_packet, mapping, expected_source_packet_sha256=expected["expected_source_packet_sha256"], expected_mapping_sha256=expected["expected_mapping_sha256"])
    try:
        data = rubric.validate_packet(view, expected_packet_sha256=expected["expected_semantic_packet_view_sha256"],
            expected_rubric_sha256=expected["expected_rubric_sha256"], expected_population_sha256=expected["expected_population_sha256"],
            expected_binding_sha256=expected["expected_packet_binding_sha256"])
    except rubric.RubricInputError: fail()
    if source_packet.get("rows") != view.get("rows") or digest(source_packet) != expected["expected_source_packet_sha256"]:
        fail()
    rebuilt = {k: freeze[k] for k in freeze if k != "freeze_sha256"}
    if digest(rebuilt) != expected_packet_freeze_sha256:
        fail()
    if data["bodies"].count((True, True)) != 0 or sum(x[0] + x[1] for x in data["bodies"]) != 69:
        fail()
    return deepcopy(freeze)


def _validate_annotation_mapping(annotation, view, mapping):
    for prow, arow, mrow in zip(view["rows"], annotation["rows"], mapping["rows"]):
        if prow["population_status"] != "TARGET": continue
        for index, condition in enumerate((mrow["A_condition"], mrow["B_condition"])):
            observed = prow["side_observations"][index]["observation"] is not None
            side_key = "side_a" if index == 0 else "side_b"
            values = [metric[side_key]["value"] for metric in arow["metrics"]]
            if condition == "NOT_MEASURED" and (observed or any(value != "MEASUREMENT_NOT_OBSERVED" for value in values)):
                fail()
            if not observed and any(value != "MEASUREMENT_NOT_OBSERVED" for value in values):
                fail()


def unblind_saved_control(annotation, source_packet, semantic_packet_view, mapping, packet_freeze, annotation_freeze, *, evaluator_identity,
        expected_evaluator_identity_sha256, expected_annotation_freeze_sha256, expected_packet_freeze_sha256,
        expected_authority_pin_sha256, expected_source_manifest_sha256, expected_population_sha256,
        expected_mapping_sha256, expected_rubric_sha256, expected_source_packet_sha256,
        expected_packet_binding_sha256, expected_semantic_packet_view_sha256):
    kwargs = dict(evaluator_identity=evaluator_identity, expected_evaluator_identity_sha256=expected_evaluator_identity_sha256,
        expected_packet_freeze_sha256=expected_packet_freeze_sha256, expected_authority_pin_sha256=expected_authority_pin_sha256,
        expected_source_manifest_sha256=expected_source_manifest_sha256, expected_population_sha256=expected_population_sha256,
        expected_mapping_sha256=expected_mapping_sha256, expected_rubric_sha256=expected_rubric_sha256,
        expected_source_packet_sha256=expected_source_packet_sha256, expected_packet_binding_sha256=expected_packet_binding_sha256,
        expected_semantic_packet_view_sha256=expected_semantic_packet_view_sha256)
    rebuilt = freeze_saved_control_annotation(annotation, source_packet, semantic_packet_view, mapping, packet_freeze, **kwargs)
    if rebuilt != annotation_freeze or rebuilt["freeze_sha256"] != expected_annotation_freeze_sha256:
        fail()
    counts = {metric: {value: 0 for value in rubric.VALUES} for metric in rubric.METRICS}
    question = {value: 0 for value in ("PASS", "FAIL", "UNKNOWN", "MEASUREMENT_NOT_OBSERVED")}
    for prow, arow, mrow in zip(semantic_packet_view["rows"], annotation["rows"], mapping["rows"]):
        if prow["population_status"] != "TARGET":
            if prow["question_opportunity"]:
                question["MEASUREMENT_NOT_OBSERVED"] += 1
            continue
        index = 0 if mrow["A_condition"] == "CONTROL" else 1
        key = "side_a" if index == 0 else "side_b"
        for metric in arow["metrics"]:
            value = metric[key]["value"]; counts[metric["metric"]][value] += 1
            if metric["metric"] == "BODY_ANSWER" and prow["question_opportunity"]:
                if value == "NOT_APPLICABLE": fail()
                question[value] += 1
    if sum(question.values()) != 54:
        fail()
    return {"contract": SUMMARY_CONTRACT, "rows": 96, "target_rows": 77, "non_target_rows": 19,
            "control_observed_rows": 69, "control_missing_rows": 8, "control_metric_counts": counts,
            "question_denominator": 54, "question_target_denominator": packet_freeze["question_target_rows"],
            "question_counts": question}
