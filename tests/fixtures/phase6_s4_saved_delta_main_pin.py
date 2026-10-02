"""Main-only reconstruction from physical frozen roots, never candidate/runtime rows."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from functools import wraps
import hashlib
import json
from pathlib import Path

from scripts import phase6_quality_probe_v2 as q
from tests.fixtures import phase6_s4_saved_binding as saved
from tests.fixtures import phase6_co_surface_custody as co
from tests.fixtures import phase6_s4_unselected_ability_root as unselected

CONTRACT = "S4_SAVED_GAP_DELTA_V1"
ROLES = ("T556_INPUT", "T556_PREPARED", "T556_SOURCE_MANIFEST", "T556_MAIN_PIN",
         "T556_PACKET", "T556_PACKET_FREEZE", "T556_ANNOTATIONS", "T556_ANNOTATION_FREEZE",
         "T556_FINALIZED", "T556_FINAL_FREEZE", "T560_SOURCE_MANIFEST", "T560_CANDIDATE",
         "T560_MAIN_PIN", "T560_WITNESSES", "T556_SAFE_RESULTS")
PARTITIONS = ("CO_SURFACE_GAP", "UNSELECTED_ROOT_GAP", "PRIOR_READY", "MEASUREMENT_NOT_OBSERVED")
DEPENDENCIES = {
    "T560": (("HELPER", "tests/fixtures/phase6_co_surface_binding.py"),
             ("CUSTODY", "tests/fixtures/phase6_co_surface_custody.py"),
             ("HELPER_TEST", "tests/test_phase6_co_surface_binding.py"),
             ("CUSTODY_TEST", "tests/test_phase6_co_surface_custody.py")),
    "T562": (("ROOT_HELPER", "tests/fixtures/phase6_s4_unselected_ability_root.py"),
             ("ROOT_TEST", "tests/test_phase6_s4_unselected_ability_root.py"),
             ("COMMON_DECISION", "tests/fixtures/phase6_s4_common_provenance.py"),
             ("COMMON_DECISION_TEST", "tests/test_phase6_s4_common_provenance.py"))}
DEPENDENCY_DOCS = {
    "T560": ("Docs/ai/design/PHASE6_CO_SURFACE_BINDING_DESIGN.md", "Docs/ai/handoffs/tasks/T560_CO_SURFACE_BINDING_TOOL_REVIEW.md"),
    "T562": ("Docs/ai/design/PHASE6_UNSELECTED_ABILITY_ROOT_DESIGN.md", "Docs/ai/handoffs/tasks/T562_UNSELECTED_ABILITY_ROOT_TOOL_REVIEW.md")}
REQUIRED_CODE = {"tests/fixtures/phase6_s4_saved_delta_main_pin.py", "tests/fixtures/phase6_s4_saved_delta.py",
                 "scripts/phase6_quality_probe_v2.py", "tests/fixtures/phase6_s4_saved_binding.py",
                 "tests/fixtures/phase6_co_surface_binding.py", "tests/fixtures/phase6_co_surface_custody.py",
                 "tests/fixtures/phase6_s4_unselected_ability_root.py", "tests/fixtures/phase6_s4_common_provenance.py"}


class MainRootError(ValueError):
    def __init__(self, reason="SOURCE"):
        self.reason = reason if reason in {"SOURCE", "OLD_REFERENCE", "CATALOG", "PARTITION", "WITNESS", "ROOT", "PIN"} else "SOURCE"
        super().__init__(self.reason)


def _need(value, reason="SOURCE"):
    if not value:
        raise MainRootError(reason)


def _keys(value, keys, reason="SOURCE"):
    _need(type(value) is dict and len(value) == len(keys) and set(value) == set(keys), reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return saved.canonical_sha(value)


def _hex(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _closed_errors(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (MainRootError, unselected.UnselectedAbilityInputError):
            raise
        except Exception:
            raise MainRootError("SOURCE") from None
    return call


def _file(root, path):
    original = Path(path)
    _need(original.is_absolute() and not original.is_symlink())
    resolved = original.resolve(strict=True)
    _need(root.resolve(strict=True) in resolved.parents and resolved.is_file())
    # A symlinked parent also changes the physical container association.
    _need(not any(p.is_symlink() for p in (original, *original.parents)))
    return resolved


@_closed_errors
def load_fixed_roots(*, files, containers, repository_root, expected_fixed_roots_sha256):
    _need(type(files) is list and len(files) == len(ROLES))
    _need(type(containers) is dict and set(containers) == {"T556", "T560"})
    _need(_hex(expected_fixed_roots_sha256) and digest(files) == expected_fixed_roots_sha256)
    loaded = {}; physical = {}; paths = set()
    for descriptor, role in zip(files, ROLES):
        _keys(descriptor, ("role", "group", "path", "sha256", "size_bytes"))
        group = "T560" if role.startswith("T560_") else "T556"
        _need(descriptor["role"] == role and descriptor["group"] == group)
        _need(type(descriptor["path"]) is str and _hex(descriptor["sha256"]))
        _need(type(descriptor["size_bytes"]) is int and descriptor["size_bytes"] >= 0)
        roots = containers[group]
        _need(type(roots) in (list, tuple) and roots and all(type(p) in (str, Path) or isinstance(p, Path) for p in roots))
        if role == "T556_SAFE_RESULTS":
            path = _file(repository_root, descriptor["path"])
            _need(path == (repository_root / "Docs/ai/handoffs/tasks/T556_COMPLETION_SAFE_RESULTS.json").resolve())
        else:
            candidates = []
            for root in roots:
                try:
                    candidates.append(_file(Path(root), descriptor["path"]))
                except MainRootError:
                    pass
            _need(len(set(candidates)) == 1)
            path = candidates[0]
        identity = str(path).casefold()
        _need(identity not in paths); paths.add(identity)
        raw = path.read_bytes()
        _need(len(raw) == descriptor["size_bytes"] and _sha(raw) == descriptor["sha256"])
        loaded[role] = saved._parse_json_bytes(raw)
        physical[role] = (path, _sha(raw))
    return loaded, physical


def _index(rows, key, count):
    _need(type(rows) is list and len(rows) == count)
    result = {}
    for row in rows:
        _need(type(row) is dict and type(row.get(key)) is str and row[key] and row[key] not in result)
        result[row[key]] = row
    return result


def existing_surface_witness(row, final_sha):
    surface = row["accepted_surface"]
    view = saved._surface_view(surface)
    bound_sha = digest(view)
    _need(bound_sha == row["old_binding"]["surface_sha256"], "WITNESS")
    value = {"contract": "S4_EXISTING_SURFACE_WITNESS_V1", "evaluation_id": row["evaluation_id"],
             "old_row_binding_sha256": digest(row["old_binding"]), "saved_surface_sha256": digest(surface),
             "bound_surface_sha256": bound_sha, "accepted_final_sha256": final_sha}
    return {**value, "witness_sha256": digest(value)}


@_closed_errors
def reconstruct_target(*, row, fixture, loaded_source, partition, frozen_co_witness=None):
    """Only explicit frozen refs/structure; no meaning evaluation or ref inference."""
    _need(partition in PARTITIONS[:2], "PARTITION")
    q.verify_bindings(fixture)
    catalog = json.loads(q.wire(asdict(fixture.catalog)))
    _need(digest(fixture.source) == row["fixture_source_sha256"]
          and digest(fixture.bindings) == row["fixture_bindings_sha256"]
          and digest(catalog) == row["fixture_catalog_sha256"], "CATALOG")
    proof = row["accepted_final_proof"]
    if proof["kind"] == "BASELINE_V1":
        sid = proof["locator"]["source_id"]
        extracted = saved.extract_baseline_final(wrapper_bytes=loaded_source[sid],
            snapshot_file_sha256=proof["locator"]["snapshot_file_sha256"], saved_surface=row["accepted_surface"])
        raw_member = saved._parse_json_bytes(loaded_source[sid])["final_content"].encode()
        final_sha = extracted["accepted_final_sha256"]
    else:
        raw_member, _ = co._accepted_candidate_member(row, loaded_source)
        final_sha = _sha(raw_member)
    surface = saved._surface_view(row["accepted_surface"])
    _need(digest(surface) == row["old_binding"]["surface_sha256"], "ROOT")
    source_code = _sha(Path(q.__file__).read_bytes())
    freeze = unselected.freeze_unselected_ability_inventory_v2(fixture=fixture,
        fixture_builder_source_sha256=source_code, binding_verifier_source_sha256=source_code)
    target_row = unselected.build_unselected_ability_row_v2(old_binding=row["old_binding"],
        accepted_plan=row["accepted_plan"], accepted_surface=surface, accepted_final_sha256=final_sha, inventory_freeze=freeze)
    unselected.validate_unselected_ability_root_v2(row=target_row, expected_freeze_sha256=freeze["freeze_sha256"],
                                                 base_records=tuple(row["base_records"]))
    if partition == "CO_SURFACE_GAP":
        supplied = {"evaluation_id": row["evaluation_id"], "old_blind_id": row["old_blind_id"],
            "identity": {"case_id": row["case_id"], "seed": row["seed"], "stage": proof["terminal_stage"], "ordinal": proof["ordinal"]},
            "source": fixture.source, "bindings": fixture.bindings, "catalog": catalog,
            "accepted_final_bytes": raw_member, "accepted_plan": row["accepted_plan"],
            "old_row_binding_sha256": digest(row["old_binding"]), "old_selection_envelope": row["old_envelope"],
            "saved_surface": row["accepted_surface"]}
        witness = co._main_rebuild_witness(supplied)
        _need(witness == frozen_co_witness and witness["contract"] == "S4_CO_SURFACE_SOURCE_BINDING_V1", "WITNESS")
    else:
        witness = existing_surface_witness(row, final_sha)
    return target_row, witness


def _accepted_sha(row, loaded):
    if row["execution"] != {"run_status": "COMPLETE", "structural_status": "ACCEPTED"}:
        return None
    proof = row["accepted_final_proof"]
    if proof["kind"] == "BASELINE_V1":
        sid = proof["locator"]["source_id"]
        extracted = saved.extract_baseline_final(wrapper_bytes=loaded[sid],
            snapshot_file_sha256=proof["locator"]["snapshot_file_sha256"], saved_surface=row["accepted_surface"])
        return extracted["accepted_final_sha256"]
    else:
        _need(proof["kind"] == "CANDIDATE_V2")
        member, _ = co._accepted_candidate_member(row, loaded)
        return _sha(member)


@_closed_errors
def current_dependency_bundle(dependency, repository_root):
    _need(dependency in DEPENDENCIES)
    design, review = DEPENDENCY_DOCS[dependency]
    files = []
    for role, path in DEPENDENCIES[dependency]:
        raw = _file(repository_root, str(repository_root / path)).read_bytes()
        files.append({"role": role, "repository_path": path, "sha256": _sha(raw), "size_bytes": len(raw)})
    value = {"contract": dependency + "_APPROVED_DEPENDENCY_BUNDLE_V1", "design_path": design,
             "design_sha256": _sha(_file(repository_root, str(repository_root / design)).read_bytes()),
             "tool_review_path": review, "tool_review_sha256": _sha(_file(repository_root, str(repository_root / review)).read_bytes()),
             "files": files}
    scope = digest(value)
    return {**value, "review_scope_sha256": scope, "bundle_sha256": digest({**value, "review_scope_sha256": scope})}


def _compare_fixed_source(source_manifest, documents, physical, source_rows, repository_root, loaded_source, old_manifest):
    expected = {
        "t556_source_manifest_sha256": old_manifest["manifest_sha256"],
        "t556_preflight_sha256": documents["T556_INPUT"]["preflight"]["preflight_sha256"],
        "t556_main_pin_sha256": documents["T556_MAIN_PIN"]["pin_sha256"],
        "t556_packet_sha256": documents["T556_PACKET"]["packet_sha256"],
        "t556_packet_freeze_file_sha256": physical["T556_PACKET_FREEZE"][1],
        "t556_annotation_sha256": digest(documents["T556_ANNOTATIONS"]),
        "t556_annotation_freeze_sha256": physical["T556_ANNOTATION_FREEZE"][1],
        "t556_finalized_sha256": digest(documents["T556_FINALIZED"]),
        "t556_final_freeze_file_sha256": physical["T556_FINAL_FREEZE"][1],
        "t560_source_manifest_sha256": documents["T560_SOURCE_MANIFEST"]["manifest_sha256"],
        "t560_candidate_manifest_sha256": documents["T560_CANDIDATE"]["manifest_sha256"],
        "t560_main_pin_sha256": documents["T560_MAIN_PIN"]["pin_sha256"]}
    keys = ("contract", "version", "task", *expected, "t560_approved_dependency_bundle", "t562_approved_dependency_bundle", "files", "rows", "manifest_sha256")
    _keys(source_manifest, keys, "PIN")
    _need(source_manifest["contract"] == CONTRACT and source_manifest["version"] == "V1" and source_manifest["task"] == "T563", "PIN")
    _need(all(source_manifest[k] == v for k, v in expected.items()), "PIN")
    _need(source_manifest["rows"] == source_rows and source_manifest["manifest_sha256"] == digest({k:v for k,v in source_manifest.items() if k != "manifest_sha256"}), "PIN")
    for dependency in DEPENDENCIES:
        _need(source_manifest[dependency.lower() + "_approved_dependency_bundle"] == current_dependency_bundle(dependency, repository_root), "PIN")
    # Every old snapshot and every fixed root is represented once under an unambiguous identity.
    expected_files = [dict(e, original_name="T556_SOURCE/" + e["original_name"]) for e in old_manifest["files"]]
    expected_files += [{"source_id": role, "original_name": "ROOTS/" + role + ".json", "sha256": sha,
                       "size": path.stat().st_size} for role,(path,sha) in physical.items()]
    expected_files.sort(key=lambda item:item["original_name"])
    _need(source_manifest["files"] == expected_files, "PIN")
    _need(len(loaded_source) == len(old_manifest["files"]))


def _compare_preflight(preflight, source_manifest, source_rows, files, repository_root, rubric_sha256, scopes):
    expected = {"contract": CONTRACT, "version": "V1", "task": "T563", "source_manifest_sha256": source_manifest["manifest_sha256"],
                "t556_core_artifacts_sha256": digest([{k:f[k] for k in ("role", "sha256", "size_bytes")} for f in files if f["group"] == "T556"]),
                "t560_approved_bundle_sha256": current_dependency_bundle("T560", repository_root)["bundle_sha256"],
                "t562_approved_bundle_sha256": current_dependency_bundle("T562", repository_root)["bundle_sha256"],
                **scopes, "helper_source_sha256": _sha((repository_root / "tests/fixtures/phase6_s4_saved_delta.py").read_bytes()),
                "rubric_sha256": rubric_sha256}
    expected["preflight_sha256"] = digest(expected)
    _need(preflight == expected, "PIN")


@_closed_errors
def rebuild_main_state(*, files, containers, repository_root, t556_source_root,
                       expected_fixed_roots_sha256, source_manifest, preflight,
                       expected_code_sha256, expected_rubric_sha256):
    """source_manifest/preflight are comparison-only; candidate/runtime are not inputs."""
    start_code = {}
    _need(type(expected_code_sha256) is dict and REQUIRED_CODE == set(expected_code_sha256))
    _need(_hex(expected_rubric_sha256), "PIN")
    for path, sha in expected_code_sha256.items():
        resolved = _file(repository_root, str(repository_root / path))
        _need(_hex(sha) and _sha(resolved.read_bytes()) == sha)
        start_code[path] = sha
    documents, physical = load_fixed_roots(files=files, containers=containers, repository_root=repository_root,
                                           expected_fixed_roots_sha256=expected_fixed_roots_sha256)
    sealed = documents["T556_PACKET"]
    _keys(sealed, ("contract", "candidate_manifest", "pin_sha256", "packet", "packet_sha256"))
    _need(sealed["packet_sha256"] == digest(sealed["packet"]))
    original = documents["T556_INPUT"]; prepared = documents["T556_PREPARED"]
    old_manifest = documents["T556_SOURCE_MANIFEST"]
    _need(original["source_manifest"] == old_manifest and prepared["source_manifest"] == old_manifest)
    _need(type(old_manifest.get("files")) is list and len(old_manifest["files"]) == 395)
    loaded = saved.validate_source_manifest(root=t556_source_root, manifest=old_manifest)
    old_packet = saved._parse_json_bytes(loaded["old_packet_snapshot"])
    old_mechanical = saved._parse_json_bytes(loaded["old_mechanical_snapshot"])
    old_mapping = saved._parse_json_bytes(loaded["old_mapping_snapshot"])
    mechanics = _index(old_mechanical["rows"], "blind_id", 192)
    hosts = _index(prepared["host_rows"], "evaluation_id", 192)
    finals = _index(documents["T556_FINALIZED"]["results"], "evaluation_id", 192)
    notes = _index(documents["T556_ANNOTATIONS"]["rows"], "evaluation_id", 192)
    input_rows = original["rows"]
    _index(input_rows, "evaluation_id", 192)
    _need([r["evaluation_id"] for r in input_rows] == [r["evaluation_id"] for r in prepared["host_rows"]])
    witnesses = documents["T560_WITNESSES"]["witnesses"]
    _need(type(witnesses) is dict and len(witnesses) == 5, "WITNESS")
    co_candidate = documents["T560_CANDIDATE"]; co_pin = documents["T560_MAIN_PIN"]
    _need(co_pin["candidate_manifest_sha256"] == co_candidate["manifest_sha256"]
          and documents["T560_SOURCE_MANIFEST"] == old_manifest, "WITNESS")
    fixtures = {f.case.case_id: f for f in q.prepare()[0]}
    source_rows = []; coverage = []; targets = []; counts = {p: 0 for p in PARTITIONS}
    for ordinal, row in enumerate(input_rows):
        saved.validate_saved_row_input(row=row, old_packet=old_packet, old_mechanical=old_mechanical, old_mapping=old_mapping)
        eid = row["evaluation_id"]; host = hosts[eid]; final = finals[eid]; note = notes[eid]
        if row["execution"] != {"run_status": "COMPLETE", "structural_status": "ACCEPTED"}:
            partition = "MEASUREMENT_NOT_OBSERVED"
        elif host["status"] == "SAVED_ACCEPTED_FINAL_UNAVAILABLE":
            partition = "CO_SURFACE_GAP"
        elif host["status"] == "INPUT_INVALID":
            partition = "UNSELECTED_ROOT_GAP"
        else:
            _need(host["status"] == "READY", "PARTITION"); partition = "PRIOR_READY"
        counts[partition] += 1
        old_decision = final["projected_decision"]
        if partition == "MEASUREMENT_NOT_OBSERVED":
            expected_mno = {"binding": None, "metric_value": "MEASUREMENT_NOT_OBSERVED", "reason_code": "PUBLIC_SURFACE_NOT_OBSERVED",
                            "offending_provenance_ids": [], "measurement_validity": "INVALID"}
            _need(final["metric_value"] == "MEASUREMENT_NOT_OBSERVED" and old_decision == expected_mno, "OLD_REFERENCE")
        elif partition == "PRIOR_READY":
            _need(type(old_decision) is dict and note["status"] == "ANNOTATED", "OLD_REFERENCE")
        else:
            _need(old_decision is None, "OLD_REFERENCE")
        final_sha = _accepted_sha(row, loaded)
        src = {"evaluation_id": eid, "ordinal": ordinal, "pair_key_sha256": row["pair_key_sha256"],
            "profile_key_sha256": digest({"condition": row["condition"]}), "old_row_binding_sha256": digest(row["old_binding"]),
            "old_selection_envelope_sha256": digest(row["old_envelope"]), "old_mechanical_row_sha256": digest(mechanics[row["old_blind_id"]]),
            "saved_surface_sha256": digest(row["accepted_surface"]), "accepted_final_sha256": final_sha,
            "old_decision_sha256": digest(old_decision) if old_decision is not None else None,
            "old_annotation_row_sha256": digest(note) if partition == "PRIOR_READY" else None, "partition": partition}
        src["source_row_sha256"] = digest(src); source_rows.append(src)
        target_row = witness = None
        if partition in PARTITIONS[:2]:
            try:
                target_row, witness = reconstruct_target(row=row, fixture=fixtures[row["case_id"]], loaded_source=loaded,
                    partition=partition, frozen_co_witness=witnesses.get(eid))
                status = reason = "READY"
            except unselected.UnselectedAbilityInputError:
                status = "INPUT_UNKNOWN"; reason = "UNSELECTED_ROOT_UNAVAILABLE"
            targets.append({"evaluation_id": eid, "delta_input_status": status, "t562_row": target_row, "surface_witness": witness})
        elif partition == "PRIOR_READY":
            status = reason = "PREVIOUSLY_EVALUATED"
        else:
            status = "MEASUREMENT_NOT_OBSERVED"; reason = "PUBLIC_SURFACE_NOT_OBSERVED"
        coverage.append({"evaluation_id": eid, "ordinal": ordinal, "partition": partition,
            "source_row_sha256": src["source_row_sha256"], "delta_input_status": status,
            "delta_root_sha256": digest(target_row) if target_row is not None else None,
            "surface_witness_sha256": witness["witness_sha256"] if witness is not None else None, "reason": reason})
    _need(counts == dict(zip(PARTITIONS, (5, 7, 167, 13))), "PARTITION")
    _compare_fixed_source(source_manifest, documents, physical, source_rows, repository_root, loaded, old_manifest)
    scopes = {
        "target_scope_sha256": digest({"contract": CONTRACT, "ordered_evaluation_ids": [r["evaluation_id"] for r in source_rows if r["partition"] in PARTITIONS[:2]]}),
        "prior_scope_sha256": digest({"contract": CONTRACT, "ordered_evaluation_ids": [r["evaluation_id"] for r in source_rows if r["partition"] == PARTITIONS[2]]}),
        "mno_scope_sha256": digest({"contract": CONTRACT, "ordered_evaluation_ids": [r["evaluation_id"] for r in source_rows if r["partition"] == PARTITIONS[3]]}),
        "pair_scope_sha256": digest({"contract": CONTRACT, "ordered_pair_keys_and_profile_keys": [[r["pair_key_sha256"], r["profile_key_sha256"]] for r in source_rows]})}
    _compare_preflight(preflight, source_manifest, source_rows, files, repository_root, expected_rubric_sha256, scopes)
    count = {"co_gap": 5, "root_gap": 7, "prior": 167, "mno": 13,
             "target_ready": sum(t["delta_input_status"] == "READY" for t in targets),
             "target_input_unknown": sum(t["delta_input_status"] == "INPUT_UNKNOWN" for t in targets)}
    expected_candidate = {"contract": CONTRACT, "version": "V1", "source_manifest_sha256": source_manifest["manifest_sha256"],
                          "preflight_sha256": preflight["preflight_sha256"], **scopes, "rows": coverage, "counts": count}
    expected_candidate["manifest_sha256"] = digest(expected_candidate)
    independent = {"contract": "S4_SAVED_DELTA_MAIN_ROOTS_V1", "rows": [
        {"evaluation_id": r["evaluation_id"], "expected_source_row_sha256": r["source_row_sha256"],
         "expected_delta_root_sha256": r["delta_root_sha256"], "expected_surface_witness_sha256": r["surface_witness_sha256"]} for r in coverage]}
    independent["roots_sha256"] = digest(independent)
    result = {"contract": "S4_SAVED_DELTA_MAIN_STATE_V1", "fixed_roots_sha256": expected_fixed_roots_sha256,
              "source_rows": source_rows, "expected_candidate": expected_candidate, "independent_roots": independent, "target_roots": targets}
    result["state_sha256"] = digest(result)
    # Re-read bytes, rather than comparing only immutable in-memory dictionaries.
    _need(all(_sha(path.read_bytes()) == sha for path, sha in physical.values()))
    saved.validate_source_manifest(root=t556_source_root, manifest=old_manifest)
    _need(all(_sha((repository_root / path).read_bytes()) == sha for path, sha in start_code.items()))
    return deepcopy(result)
