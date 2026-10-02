"""Pure test-only diagnostics for source-bound CO public surfaces."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

CONTRACT = "S4_CO_SURFACE_SOURCE_BINDING_V1"
PARTITIONS = ("TARGET_SURFACE_GAP", "PRIOR_READY", "ROOT_GAP", "MEASUREMENT_NOT_OBSERVED")


class CoSurfaceBindingError(ValueError):
    """Closed, payload-free integrity failure."""

    def __init__(self, detail: str = "INPUT_INTEGRITY"):
        self.detail = detail if detail in {
            "INPUT_INTEGRITY", "JSON", "HASH", "SHAPE", "DUPLICATE", "SOURCE", "PIN", "CREATE_ONLY",
            "CO_SOURCE_BINDING_MISSING", "CO_CATALOG_MEMBERSHIP_INVALID", "CO_SURFACE_MISMATCH"
        } else "INPUT_INTEGRITY"
        super().__init__("CO_SURFACE_BINDING_INPUT_INTEGRITY")


def _fail(detail: str = "INPUT_INTEGRITY") -> None:
    raise CoSurfaceBindingError(detail)


def _exact(value: Any, keys: tuple[str, ...], detail: str = "SHAPE") -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(keys) or len(value) != len(keys):
        _fail(detail)
    return value


def _string(value: Any) -> str:
    if type(value) is not str or not value:
        _fail("SHAPE")
    return value


def _digest(value: Any) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        _fail("HASH")
    return value


def _walk(value: Any) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is list:
        for item in value:
            _walk(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                _fail("SHAPE")
            _walk(item)
        return
    _fail("SHAPE")


def canonical_wire(value: Any) -> bytes:
    _walk(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (UnicodeEncodeError, ValueError):
        raise CoSurfaceBindingError("JSON") from None


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(canonical_wire(value)).hexdigest()


def strict_parse(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes:
        _fail("JSON")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                _fail("DUPLICATE")
            out[key] = value
        return out

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: _fail("JSON"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise CoSurfaceBindingError("JSON") from None
    _walk(value)
    if type(value) is not dict:
        _fail("JSON")
    return value


def _typed_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(_typed_equal(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(_typed_equal(a, b) for a, b in zip(left, right))
    return left == right


def _resolve(source: Any, pointer: str) -> Any:
    if type(pointer) is not str or not pointer.startswith("/"):
        _fail("SOURCE")
    current = source
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if type(current) is dict and token in current:
            current = current[token]
        elif type(current) is list and token.isdigit() and str(int(token)) == token and int(token) < len(current):
            current = current[int(token)]
        else:
            _fail("SOURCE")
    return current


def _binding(bindings: dict[str, Any], binding_id: str, kind: str, source: dict,
             expected_actor: str | None | object = ...) -> tuple[dict, Any]:
    if type(bindings) is not dict or binding_id not in bindings:
        _fail("CO_SOURCE_BINDING_MISSING")
    b = _exact(bindings[binding_id], ("projection_sha256", "pointer", "source_kind", "actor", "channel", "authority", "value_sha256"), "SOURCE")
    if (b["source_kind"] != kind or (expected_actor is not ... and b["actor"] != expected_actor)
            or b["channel"] is not None or b["authority"] != "PUBLIC"):
        _fail("CO_SOURCE_BINDING_MISSING")
    if b["projection_sha256"] != canonical_sha(source):
        _fail("CO_SOURCE_BINDING_MISSING")
    value = _resolve(source, b["pointer"])
    if b["value_sha256"] != canonical_sha(value):
        _fail("CO_SOURCE_BINDING_MISSING")
    return b, value


def build_co_surface_witness(row: dict[str, Any]) -> dict[str, Any]:
    """Validate one target row and return an immutable diagnostic witness."""
    keys = ("evaluation_id", "old_blind_id", "identity", "source", "bindings", "catalog", "accepted_final_bytes",
            "accepted_plan", "old_row_binding_sha256", "old_selection_envelope", "saved_surface")
    row = _exact(row, keys)
    identity = _exact(row["identity"], ("case_id", "seed", "stage", "ordinal"))
    if type(identity["seed"]) is not int or type(identity["seed"]) is bool or type(identity["ordinal"]) is not int or type(identity["ordinal"]) is bool:
        _fail("SHAPE")
    for key in ("case_id", "stage"):
        _string(identity[key])
    source = row["source"]
    if type(source) is not dict:
        _fail("SOURCE")
    owner = _resolve(source, "/context/player_id")
    _string(owner)
    final = strict_parse(row["accepted_final_bytes"])
    _exact(final, ("decision", "co_option_id", "claimed_role_option_id", "comment", "fact_ids"))
    if final["decision"] != "DECLARE":
        _fail("SHAPE")
    option_id, role_id, comment = (_string(final[k]) for k in ("co_option_id", "claimed_role_option_id", "comment"))
    facts = final["fact_ids"]
    if type(facts) is not list or len(facts) > 2 or any(type(x) is not str or not x for x in facts) or len(facts) != len(set(facts)):
        _fail("SHAPE")
    option_binding, option = _binding(row["bindings"], option_id, "CO_OPTION", source, owner)
    if type(option) is not dict or option.get("type") != "co_declare" or type(option.get("option_id")) is not str or not option["option_id"]:
        _fail("CO_SOURCE_BINDING_MISSING")
    roles = option.get("claimed_role_ids")
    if type(roles) is not list or not roles or any(type(x) is not str or not x for x in roles) or len(roles) != len(set(roles)):
        _fail("CO_SOURCE_BINDING_MISSING")
    role_binding, role = _binding(row["bindings"], role_id, "CLAIMED_ROLE_OPTION", source, owner)
    if role_binding["pointer"] not in tuple(f"{option_binding['pointer']}/claimed_role_ids/{i}" for i in range(len(roles))):
        _fail("CO_SOURCE_BINDING_MISSING")
    if type(role) is not str or role not in roles:
        _fail("CO_SOURCE_BINDING_MISSING")
    catalog = _exact(row["catalog"], ("reply_ids", "player_ids", "peer_player_ids", "fact_ids", "disclose_ids",
                                                "utterance_claim_ids", "observed_claim_ids", "opinion_bases",
                                                "vote_options", "co_options", "ability_options"))
    for name in ("reply_ids", "player_ids", "peer_player_ids", "fact_ids", "disclose_ids", "utterance_claim_ids", "observed_claim_ids"):
        values = catalog[name]
        if type(values) is not list or any(type(x) is not str or not x for x in values) or len(values) != len(set(values)):
            _fail("CO_CATALOG_MEMBERSHIP_INVALID")
    if any(type(catalog[name]) is not list for name in ("opinion_bases", "vote_options", "co_options", "ability_options")):
        _fail("CO_CATALOG_MEMBERSHIP_INVALID")
    option_ids = []
    for entry in catalog["co_options"]:
        if type(entry) is not dict or set(entry) != {"option_id", "claimed_role_option_ids"}:
            _fail("CO_CATALOG_MEMBERSHIP_INVALID")
        oid, rids = entry["option_id"], entry["claimed_role_option_ids"]
        if type(oid) is not str or not oid or type(rids) is not list or any(type(x) is not str or not x for x in rids) or len(rids) != len(set(rids)):
            _fail("CO_CATALOG_MEMBERSHIP_INVALID")
        option_ids.append(oid)
    if len(option_ids) != len(set(option_ids)):
        _fail("CO_CATALOG_MEMBERSHIP_INVALID")
    option_matches = [x for x in catalog["co_options"] if type(x) is dict and x.get("option_id") == option_id]
    if len(option_matches) != 1 or set(option_matches[0]) != {"option_id", "claimed_role_option_ids"}:
        _fail("CO_CATALOG_MEMBERSHIP_INVALID")
    catalog_roles = option_matches[0]["claimed_role_option_ids"]
    if type(catalog_roles) is not list or catalog_roles.count(role_id) != 1:
        _fail("CO_CATALOG_MEMBERSHIP_INVALID")
    plan_facts = row["accepted_plan"].get("fact_ids") if type(row["accepted_plan"]) is dict else None
    if type(plan_facts) is not list or not _typed_equal(plan_facts, facts):
        _fail("CO_SOURCE_BINDING_MISSING")
    envelope = row["old_selection_envelope"]
    if type(envelope) is not dict or set(envelope) != {"binding", "accepted_plan_sha256", "items"}:
        _fail("SHAPE")
    if envelope["accepted_plan_sha256"] != canonical_sha(row["accepted_plan"]) or type(envelope["binding"]) is not dict:
        _fail("SHAPE")
    items = envelope["items"]
    if type(items) is not list:
        _fail("SHAPE")
    selected_facts = []
    for item in items:
        if type(item) is not dict or set(item) != {"provenance_id", "origin", "selected_id", "explicit_ref", "binding_id"}:
            _fail("SHAPE")
        if item["origin"] == "SELECTED_PUBLIC_FACT":
            selected_facts.append(item["selected_id"])
    if not _typed_equal(selected_facts, facts):
        _fail("CO_SOURCE_BINDING_MISSING")
    fact_rows = []
    for fact_id in facts:
        if fact_id not in catalog["fact_ids"]:
            _fail("CO_CATALOG_MEMBERSHIP_INVALID")
        binding, value = _binding(row["bindings"], fact_id, "PUBLIC_FACT", source)
        fact_rows.append({"fact_id": fact_id, "binding_sha256": canonical_sha(binding), "resolved_value_sha256": canonical_sha(value)})
    claim = {"decision": "DECLARE", "option": option["option_id"], "claimed_role": role}
    surface = {"kind": "TEXT_AND_STRUCTURED", "text": None, "comment": comment, "claims": [claim]}
    if not _typed_equal(row["saved_surface"], surface):
        _fail("CO_SURFACE_MISMATCH")
    _digest(row["old_row_binding_sha256"])
    raw_selection = {"decision": "DECLARE", "option_binding_id": option_id, "role_binding_id": role_id,
                     "comment_sha256": canonical_sha(comment), "fact_ids": deepcopy(facts)}
    resolution = lambda bid, kind, binding, value: {
        "binding_id": bid, "source_kind": kind, "projection_sha256": binding["projection_sha256"],
        "value_sha256": binding["value_sha256"], "pointer": binding["pointer"], "actor_id": binding["actor"],
        "channel": None, "authority": "PUBLIC", "resolved_value_sha256": canonical_sha(value),
    }
    payload = {
        "contract": CONTRACT, "evaluation_id": _string(row["evaluation_id"]), "old_blind_id": _string(row["old_blind_id"]),
        "row_identity_sha256": canonical_sha(identity), "source_sha256": canonical_sha(source),
        "bindings_sha256": canonical_sha(row["bindings"]), "catalog_sha256": canonical_sha(catalog),
        "accepted_final_sha256": hashlib.sha256(row["accepted_final_bytes"]).hexdigest(),
        "accepted_plan_sha256": canonical_sha(row["accepted_plan"]), "old_row_binding_sha256": row["old_row_binding_sha256"],
        "old_selection_envelope_sha256": canonical_sha(envelope), "saved_surface_sha256": canonical_sha(surface),
        "raw_selection": raw_selection,
        "option_resolution": resolution(option_id, "CO_OPTION", option_binding, option),
        "role_resolution": resolution(role_id, "CLAIMED_ROLE_OPTION", role_binding, role),
        "catalog_membership_sha256": canonical_sha({"option_binding_id": option_id, "role_binding_id": role_id,
                                                       "co_option_index": catalog["co_options"].index(option_matches[0]),
                                                       "role_index": catalog_roles.index(role_id)}),
        "fact_binding_set_sha256": canonical_sha(fact_rows), "claim_projection_sha256": canonical_sha(claim),
    }
    return deepcopy({**payload, "witness_sha256": canonical_sha(payload)})


def diagnose_co_surface_row(row: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Turn a target-local failure into its closed safe reason."""
    try:
        return build_co_surface_witness(row), None
    except CoSurfaceBindingError as error:
        if error.detail in {"CO_SOURCE_BINDING_MISSING", "CO_CATALOG_MEMBERSHIP_INVALID", "CO_SURFACE_MISMATCH"}:
            return None, error.detail
        return None, "INPUT_INTEGRITY"


def build_candidate_manifest(rows: list[dict[str, Any]], *, source_manifest_sha256: str,
                             preflight_sha256: str, ordered_target_ids: list[str]) -> dict[str, Any]:
    if type(rows) is not list or len(rows) != 192 or type(ordered_target_ids) is not list or len(ordered_target_ids) != 5:
        _fail("SHAPE")
    ids, out, counts = [], [], {"target": 0, "prior_ready": 0, "root_gap": 0, "mno": 0, "proved": 0, "target_unknown": 0}
    for row in rows:
        _exact(row, ("evaluation_id", "old_row_binding_sha256", "saved_surface_sha256", "partition", "witness", "failure_reason"))
        eid, partition = _string(row["evaluation_id"]), row["partition"]
        if partition not in PARTITIONS or eid in ids:
            _fail("DUPLICATE")
        ids.append(eid); _digest(row["old_row_binding_sha256"]); _digest(row["saved_surface_sha256"])
        witness = row["witness"]
        if partition == "TARGET_SURFACE_GAP":
            counts["target"] += 1
            if witness is None:
                reason = row["failure_reason"]
                if reason not in {"INPUT_INTEGRITY", "CO_SOURCE_BINDING_MISSING", "CO_CATALOG_MEMBERSHIP_INVALID", "CO_SURFACE_MISMATCH"}:
                    _fail("SHAPE")
                status, witness_sha = "UNKNOWN", None; counts["target_unknown"] += 1
            else:
                if witness["evaluation_id"] != eid or canonical_sha({k: v for k, v in witness.items() if k != "witness_sha256"}) != witness["witness_sha256"]:
                    _fail("HASH")
                status, reason, witness_sha = "WITNESS_PROVED", "EXACT_CO_SOURCE_BINDING", witness["witness_sha256"]; counts["proved"] += 1
        elif partition == "PRIOR_READY":
            status, reason, witness_sha = "NOT_APPLICABLE", "PREVIOUSLY_EVALUATED", None; counts["prior_ready"] += 1
        elif partition == "ROOT_GAP":
            status, reason, witness_sha = "NOT_APPLICABLE", "ROOT_BINDING_OUT_OF_SCOPE", None; counts["root_gap"] += 1
        else:
            status, reason, witness_sha = "MEASUREMENT_NOT_OBSERVED", "PUBLIC_SURFACE_NOT_OBSERVED", None; counts["mno"] += 1
        if partition != "TARGET_SURFACE_GAP" and witness is not None:
            _fail("SHAPE")
        if partition == "TARGET_SURFACE_GAP" and (witness is None) != (row["failure_reason"] is not None):
            _fail("SHAPE")
        if partition != "TARGET_SURFACE_GAP" and row["failure_reason"] is not None:
            _fail("SHAPE")
        out.append({"evaluation_id": eid, "old_row_binding_sha256": row["old_row_binding_sha256"],
                    "saved_surface_sha256": row["saved_surface_sha256"], "partition": partition,
                    "status": status, "reason": reason, "witness_sha256": witness_sha})
    if counts != {"target": 5, "prior_ready": 167, "root_gap": 7, "mno": 13, "proved": counts["proved"], "target_unknown": counts["target_unknown"]} or counts["proved"] + counts["target_unknown"] != 5:
        _fail("SHAPE")
    if [r["evaluation_id"] for r in out if r["partition"] == "TARGET_SURFACE_GAP"] != ordered_target_ids or len(set(ordered_target_ids)) != 5:
        _fail("SHAPE")
    payload = {"contract": CONTRACT, "version": "V1", "source_manifest_sha256": _digest(source_manifest_sha256),
               "preflight_sha256": _digest(preflight_sha256),
               "target_scope_sha256": canonical_sha({"contract": CONTRACT, "ordered_evaluation_ids": ordered_target_ids}),
               "rows": out, "counts": counts}
    return deepcopy({**payload, "manifest_sha256": canonical_sha(payload)})


def build_main_pin(*, candidate: dict[str, Any], source_manifest_sha256: str, preflight_sha256: str,
                   ordered_target_ids: list[str], independently_derived_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the pin from separately loaded frozen rows, then compare the candidate."""
    rebuilt = build_candidate_manifest(independently_derived_rows, source_manifest_sha256=source_manifest_sha256,
                                       preflight_sha256=preflight_sha256, ordered_target_ids=ordered_target_ids)
    if not _typed_equal(rebuilt, candidate):
        _fail("PIN")
    payload = {"contract": CONTRACT, "task": "T559", "source_manifest_sha256": _digest(source_manifest_sha256),
               "preflight_sha256": _digest(preflight_sha256), "candidate_manifest_sha256": rebuilt["manifest_sha256"],
               "target_scope_sha256": rebuilt["target_scope_sha256"], "coverage_rows_sha256": canonical_sha(rebuilt["rows"]),
               "rows": [{"evaluation_id": r["evaluation_id"], "expected_witness_sha256": r["witness_sha256"]}
                        for r in rebuilt["rows"] if r["status"] == "WITNESS_PROVED"],
               "counts": deepcopy(rebuilt["counts"])}
    return deepcopy({**payload, "pin_sha256": canonical_sha(payload)})


def verify_main_pin(candidate: dict[str, Any], pin: dict[str, Any], expected_pin_sha256: str) -> None:
    if (canonical_sha({k: v for k, v in pin.items() if k != "pin_sha256"}) != pin.get("pin_sha256")
            or pin.get("candidate_manifest_sha256") != candidate.get("manifest_sha256")
            or pin.get("pin_sha256") != _digest(expected_pin_sha256)):
        _fail("PIN")


def write_create_only(path: Path, value: dict[str, Any]) -> None:
    try:
        with Path(path).open("xb") as stream:
            stream.write(canonical_wire(value))
    except (FileExistsError, OSError):
        raise CoSurfaceBindingError("CREATE_ONLY") from None
