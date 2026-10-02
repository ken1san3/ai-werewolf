"""Pure test-only S4_UNSELECTED_ABILITY_ROOT_V2 helper."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts import phase6_quality_probe_v2 as q
from tests.fixtures import phase6_s4_common_provenance as common


CONTRACT = "S4_UNSELECTED_ABILITY_ROOT_V2"
VALUE_KEYS = (
    "target_player_id", "result_id", "revealed_role_id", "event_type",
    "day", "phase", "order",
)


class UnselectedAbilityInputError(ValueError):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__("UNSELECTED_ABILITY_INPUT_INVALID")


def _fail(detail: str) -> None:
    raise UnselectedAbilityInputError(detail)


def _exact(value: Any, keys: tuple[str, ...], detail: str) -> dict:
    if type(value) is not dict or len(value) != len(keys) or set(value) != set(keys):
        _fail(detail)
    return value


def _string(value: Any, detail: str) -> str:
    if type(value) is not str or not value:
        _fail(detail)
    return value


def _enum(value: Any, allowed: tuple[str, ...], detail: str) -> str:
    if type(value) is not str or value not in allowed:
        _fail(detail)
    return value


def _digest(value: Any) -> str:
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        _fail("DIGEST")
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
                _fail("WIRE")
            _walk(item)
        return
    _fail("WIRE")


def canonical_wire(value: Any) -> bytes:
    _walk(value)
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise UnselectedAbilityInputError("WIRE") from error


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(canonical_wire(value)).hexdigest()


def strict_parse(raw: bytes) -> dict:
    if type(raw) is not bytes:
        _fail("RAW")

    def pairs(items: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                _fail("DUPLICATE")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(UnselectedAbilityInputError("NONFINITE")),
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise UnselectedAbilityInputError("JSON") from error
    _walk(value)
    if type(value) is not dict or canonical_wire(value) != raw:
        _fail("WIRE")
    return value


def _resolve(source: Any, pointer: str) -> Any:
    _string(pointer, "POINTER")
    if not pointer.startswith("/"):
        _fail("POINTER")
    current = source
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        if type(current) is dict and token in current:
            current = current[token]
        elif type(current) is list and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            _fail("POINTER")
    return current


def _ability_value(value: Any) -> dict:
    value = _exact(value, VALUE_KEYS, "ABILITY")
    for key in ("target_player_id", "result_id", "revealed_role_id", "event_type", "phase"):
        if value[key] is not None and (type(value[key]) is not str or not value[key]):
            _fail("ABILITY")
    for key in ("day", "order"):
        if value[key] is not None and (type(value[key]) is not int or value[key] < 0):
            _fail("ABILITY")
    return deepcopy(value)


def _source_channel(value: Any) -> tuple[str, str | None]:
    value = _exact(value, ("tag", "value"), "CHANNEL")
    if value["tag"] == "ABSENT" and value["value"] is None:
        return "ABSENT", None
    if value["tag"] == "VALUE":
        return "VALUE", _string(value["value"], "CHANNEL")
    _fail("CHANNEL")


def _inventory_binding(value: Any) -> dict:
    keys = (
        "binding_id", "disclose_id", "source_kind", "source_sha256",
        "raw_value_sha256", "pointer", "owner_id", "source_channel",
        "channel_visibility", "source_authority", "raw_value",
    )
    value = _exact(value, keys, "INVENTORY")
    for key in ("binding_id", "disclose_id", "pointer", "owner_id"):
        _string(value[key], "INVENTORY")
    if value["source_kind"] != "ABILITY_RESULT":
        _fail("INVENTORY")
    _digest(value["source_sha256"]); _digest(value["raw_value_sha256"])
    tag, _ = _source_channel(value["source_channel"])
    visibility = value["channel_visibility"]
    if ((tag == "ABSENT" and visibility != "NOT_APPLICABLE")
            or (tag == "VALUE" and visibility not in ("PUBLIC", "PRIVATE"))):
        _fail("CHANNEL")
    if value["source_authority"] != "INTENTIONAL_OWNER_ABILITY":
        _fail("AUTHORITY")
    raw = _ability_value(value["raw_value"])
    if canonical_sha(raw) != value["raw_value_sha256"]:
        _fail("ABILITY")
    return value


def _freeze_payload(value: dict) -> dict:
    return {key: value[key] for key in value if key != "freeze_sha256"}


def _freeze(value: Any, expected: str | None = None) -> dict:
    keys = (
        "contract", "fixture_builder_source_sha256", "binding_verifier_source_sha256",
        "source_sha256", "source_projection_sha256", "owner_pointer", "owner_id",
        "owner_value_sha256", "catalog_disclose_ids", "legacy_owner_ability_pointers",
        "inventory_bindings", "freeze_sha256",
    )
    value = _exact(value, keys, "FREEZE")
    if value["contract"] != CONTRACT or value["owner_pointer"] != "/context/player_id":
        _fail("FREEZE")
    for key in keys[1:5] + ("owner_value_sha256", "freeze_sha256"):
        _digest(value[key])
    _string(value["owner_id"], "OWNER")
    for key in ("catalog_disclose_ids", "legacy_owner_ability_pointers", "inventory_bindings"):
        if type(value[key]) is not list:
            _fail("FREEZE")
    for sequence in (value["catalog_disclose_ids"], value["legacy_owner_ability_pointers"]):
        if any(type(item) is not str or not item for item in sequence) or len(sequence) != len(set(sequence)):
            _fail("FREEZE")
    for binding in value["inventory_bindings"]:
        _inventory_binding(binding)
    for field in ("binding_id", "disclose_id", "pointer"):
        values = [item[field] for item in value["inventory_bindings"]]
        if len(values) != len(set(values)):
            _fail("FREEZE")
    if [item["disclose_id"] for item in value["inventory_bindings"]] != value["catalog_disclose_ids"]:
        _fail("FREEZE")
    if [item["pointer"] for item in value["inventory_bindings"]] != value["legacy_owner_ability_pointers"]:
        _fail("FREEZE")
    if canonical_sha(_freeze_payload(value)) != value["freeze_sha256"]:
        _fail("FREEZE")
    if expected is not None and value["freeze_sha256"] != _digest(expected):
        _fail("FREEZE")
    return value


def freeze_unselected_ability_inventory_v2(*, fixture: Any,
                                            fixture_builder_source_sha256: str,
                                            binding_verifier_source_sha256: str) -> dict:
    builder = _digest(fixture_builder_source_sha256)
    verifier = _digest(binding_verifier_source_sha256)
    probe_sha = hashlib.sha256(Path(q.__file__).read_bytes()).hexdigest()
    if builder != probe_sha or verifier != probe_sha:
        _fail("SOURCE_CODE")
    try:
        q.verify_bindings(fixture)
        source = fixture.source
        bindings = fixture.bindings
        disclose_ids = list(fixture.catalog.disclose_ids)
    except Exception as error:
        raise UnselectedAbilityInputError("FIXTURE") from error
    if type(source) is not dict or type(bindings) is not dict:
        _fail("FIXTURE")
    if any(type(item) is not str or not item for item in disclose_ids) or len(disclose_ids) != len(set(disclose_ids)):
        _fail("CATALOG")
    source_sha = canonical_sha(source)
    owner = _string(_resolve(source, "/context/player_id"), "OWNER")
    channels = source.get("context", {}).get("chat_channels")
    if type(channels) is not list:
        _fail("CHANNEL")
    inventory = []
    pointers = []
    for disclose_id in disclose_ids:
        edge = bindings.get(disclose_id)
        edge_keys = ("projection_sha256", "pointer", "source_kind", "actor", "channel", "authority", "value_sha256")
        edge = _exact(edge, edge_keys, "BINDING")
        if (edge["projection_sha256"] != source_sha or edge["source_kind"] != "ABILITY_RESULT"
                or edge["actor"] != owner or edge["authority"] != "INTENTIONAL_OWNER_ABILITY"):
            _fail("BINDING")
        raw = _ability_value(_resolve(source, edge["pointer"]))
        if edge["value_sha256"] != canonical_sha(raw):
            _fail("BINDING")
        if edge["channel"] is None:
            channel = {"tag": "ABSENT", "value": None}
            visibility = "NOT_APPLICABLE"
        else:
            channel_id = _string(edge["channel"], "CHANNEL")
            matches = [item for item in channels if type(item) is dict and item.get("channel_id") == channel_id]
            if len(matches) != 1 or type(matches[0].get("is_public")) is not bool:
                _fail("CHANNEL")
            channel = {"tag": "VALUE", "value": channel_id}
            visibility = "PUBLIC" if matches[0]["is_public"] else "PRIVATE"
        pointers.append(edge["pointer"])
        inventory.append({
            "binding_id": disclose_id, "disclose_id": disclose_id,
            "source_kind": "ABILITY_RESULT", "source_sha256": source_sha,
            "raw_value_sha256": canonical_sha(raw), "pointer": edge["pointer"],
            "owner_id": owner, "source_channel": channel,
            "channel_visibility": visibility,
            "source_authority": "INTENTIONAL_OWNER_ABILITY", "raw_value": raw,
        })
    freeze = {
        "contract": CONTRACT, "fixture_builder_source_sha256": builder,
        "binding_verifier_source_sha256": verifier, "source_sha256": source_sha,
        "source_projection_sha256": source_sha, "owner_pointer": "/context/player_id",
        "owner_id": owner, "owner_value_sha256": canonical_sha(owner),
        "catalog_disclose_ids": disclose_ids,
        "legacy_owner_ability_pointers": pointers, "inventory_bindings": inventory,
    }
    freeze["freeze_sha256"] = canonical_sha(freeze)
    return deepcopy(_freeze(freeze, freeze["freeze_sha256"]))


_BINDING_KEYS = (
    "rubric_version", "blind_id", "base_row_sha256", "base_selection_envelope_sha256",
    "base_catalog_sha256", "base_canonical_set_sha256", "surface_sha256",
    "accepted_plan_sha256", "accepted_final_sha256", "inventory_freeze_sha256",
    "inventory_set_sha256", "row_sha256",
)


def _binding(value: Any) -> dict:
    value = _exact(value, _BINDING_KEYS, "ROW")
    if value["rubric_version"] != CONTRACT:
        _fail("ROW")
    _string(value["blind_id"], "ROW")
    for key in _BINDING_KEYS[2:]:
        _digest(value[key])
    return value


def _base_matches(binding: dict, base: dict) -> bool:
    return (
        base.get("rubric_version") == common.RUBRIC
        and base.get("blind_id") == binding["blind_id"]
        and base.get("row_sha256") == binding["base_row_sha256"]
        and base.get("selection_envelope_sha256") == binding["base_selection_envelope_sha256"]
        and base.get("catalog_sha256") == binding["base_catalog_sha256"]
        and base.get("canonical_set_sha256") == binding["base_canonical_set_sha256"]
        and base.get("surface_sha256") == binding["surface_sha256"]
    )


def build_unselected_ability_row_v2(*, old_binding: dict, accepted_plan: dict,
                                    accepted_surface: dict, accepted_final_sha256: str,
                                    inventory_freeze: dict) -> dict:
    common._row_binding(old_binding)
    freeze = _freeze(deepcopy(inventory_freeze))
    final_sha = _digest(accepted_final_sha256)
    surface_sha = canonical_sha(accepted_surface)
    if old_binding["surface_sha256"] != surface_sha:
        _fail("ROW")
    inventory_set_sha = canonical_sha({
        "contract": CONTRACT, "source_sha256": freeze["source_sha256"],
        "inventory_bindings": freeze["inventory_bindings"],
    })
    binding = {
        "rubric_version": CONTRACT, "blind_id": old_binding["blind_id"],
        "base_row_sha256": old_binding["row_sha256"],
        "base_selection_envelope_sha256": old_binding["selection_envelope_sha256"],
        "base_catalog_sha256": old_binding["catalog_sha256"],
        "base_canonical_set_sha256": old_binding["canonical_set_sha256"],
        "surface_sha256": surface_sha, "accepted_plan_sha256": canonical_sha(accepted_plan),
        "accepted_final_sha256": final_sha, "inventory_freeze_sha256": freeze["freeze_sha256"],
        "inventory_set_sha256": inventory_set_sha,
    }
    binding["row_sha256"] = canonical_sha({"contract": CONTRACT, **binding})
    witness = {
        "contract": CONTRACT, "blind_id": binding["blind_id"], "owner_id": freeze["owner_id"],
        "owner_pointer": freeze["owner_pointer"], "source_sha256": freeze["source_sha256"],
        "owner_value_sha256": freeze["owner_value_sha256"],
        **{key: binding[key] for key in (
            "row_sha256", "surface_sha256", "accepted_plan_sha256", "accepted_final_sha256",
            "inventory_freeze_sha256", "inventory_set_sha256",
        )},
    }
    witness["witness_sha256"] = canonical_sha(witness)
    return {"binding": binding, "accepted_plan": deepcopy(accepted_plan),
            "accepted_surface": deepcopy(accepted_surface), "witness": witness,
            "inventory_freeze": deepcopy(freeze)}


def build_unselected_semantic_v2(*, row: dict, base: dict,
                                 base_annotation_freeze_sha256: str) -> dict:
    binding = _binding(row["binding"])
    witness = row["witness"]
    common._row_binding(base.get("binding"))
    if not _base_matches(binding, base["binding"]):
        _fail("ROW")
    return {
        "contract": CONTRACT, "base": deepcopy(base), "blind_id": binding["blind_id"],
        "row_sha256": binding["row_sha256"], "surface_sha256": binding["surface_sha256"],
        "accepted_final_sha256": binding["accepted_final_sha256"],
        "witness_sha256": witness["witness_sha256"],
        "inventory_freeze_sha256": binding["inventory_freeze_sha256"],
        "base_binding_sha256": canonical_sha(base["binding"]),
        "base_annotation_freeze_sha256": _digest(base_annotation_freeze_sha256),
    }


def _plan(value: Any) -> list[str]:
    value = _exact(value, ("kind", "fact_ids", "disclose_ids", "claim_id"), "SELECTION")
    if value["kind"] != "CHAT_PLAN":
        _fail("SELECTION")
    for field in ("fact_ids", "disclose_ids"):
        if type(value[field]) is not list or any(type(item) is not str or not item for item in value[field]):
            _fail("SELECTION")
        if len(value[field]) != len(set(value[field])):
            _fail("SELECTION")
    if value["claim_id"] is not None:
        _string(value["claim_id"], "SELECTION")
    return value["disclose_ids"]


def _validate_row(row: Any, expected_freeze_sha256: str) -> tuple[dict, dict]:
    row = _exact(row, ("binding", "accepted_plan", "accepted_surface", "witness", "inventory_freeze"), "ROW")
    binding = _binding(row["binding"])
    freeze = _freeze(row["inventory_freeze"], expected_freeze_sha256)
    if binding["inventory_freeze_sha256"] != freeze["freeze_sha256"]:
        _fail("FREEZE")
    inventory_set_sha = canonical_sha({
        "contract": CONTRACT, "source_sha256": freeze["source_sha256"],
        "inventory_bindings": freeze["inventory_bindings"],
    })
    payload = {"contract": CONTRACT, **{key: binding[key] for key in _BINDING_KEYS if key != "row_sha256"}}
    if (binding["inventory_set_sha256"] != inventory_set_sha
            or binding["surface_sha256"] != canonical_sha(row["accepted_surface"])
            or binding["accepted_plan_sha256"] != canonical_sha(row["accepted_plan"])
            or binding["row_sha256"] != canonical_sha(payload)):
        _fail("ROW")
    witness_keys = (
        "contract", "blind_id", "owner_id", "owner_pointer", "source_sha256",
        "owner_value_sha256", "row_sha256", "surface_sha256", "accepted_plan_sha256",
        "accepted_final_sha256", "inventory_freeze_sha256", "inventory_set_sha256",
        "witness_sha256",
    )
    witness = _exact(row["witness"], witness_keys, "ROW")
    expected = {
        "contract": CONTRACT, "blind_id": binding["blind_id"], "owner_id": freeze["owner_id"],
        "owner_pointer": freeze["owner_pointer"], "source_sha256": freeze["source_sha256"],
        "owner_value_sha256": freeze["owner_value_sha256"],
        **{key: binding[key] for key in (
            "row_sha256", "surface_sha256", "accepted_plan_sha256", "accepted_final_sha256",
            "inventory_freeze_sha256", "inventory_set_sha256",
        )},
    }
    if any(witness[key] != value for key, value in expected.items()):
        _fail("ROW")
    if witness["witness_sha256"] != canonical_sha({key: witness[key] for key in witness if key != "witness_sha256"}):
        _fail("ROW")
    return binding, freeze


def validate_unselected_ability_root_v2(*, row: dict, expected_freeze_sha256: str,
                                        base_records: tuple[dict, ...]) -> tuple[dict, ...]:
    binding, freeze = _validate_row(row, expected_freeze_sha256)
    disclosures = _plan(row["accepted_plan"])
    catalog = freeze["catalog_disclose_ids"]
    if any(item not in catalog for item in disclosures):
        _fail("SELECTION")
    inventory_ids = {item["disclose_id"] for item in freeze["inventory_bindings"]}
    selected = [item for item in disclosures if item in inventory_ids]
    if selected:
        _fail("SELECTED")
    if type(base_records) is not tuple:
        _fail("SELECTION")
    base_binding = None
    ids = []
    for record in base_records:
        if type(record) is not dict:
            _fail("SELECTION")
        candidate = record.get("binding")
        if base_binding is None:
            base_binding = candidate
        if candidate != base_binding:
            _fail("SELECTION")
        common._mechanical(record, candidate)
        if record["origin"] == "SELECTED_DISCLOSURE":
            _fail("SELECTION")
        ids.append(record["provenance_id"])
    if len(ids) != len(set(ids)):
        _fail("SELECTION")
    if base_binding is not None and not _base_matches(binding, base_binding):
        _fail("ROW")
    return tuple(deepcopy(base_records))


def _semantic(value: Any, row: dict, binding: dict) -> dict:
    keys = (
        "contract", "base", "blind_id", "row_sha256", "surface_sha256",
        "accepted_final_sha256", "witness_sha256", "inventory_freeze_sha256",
        "base_binding_sha256", "base_annotation_freeze_sha256",
    )
    value = _exact(value, keys, "SEMANTIC")
    if value["contract"] != CONTRACT or value["blind_id"] != binding["blind_id"]:
        _fail("SEMANTIC")
    for key in keys[3:]:
        _digest(value[key])
    expected = {
        "row_sha256": binding["row_sha256"], "surface_sha256": binding["surface_sha256"],
        "accepted_final_sha256": binding["accepted_final_sha256"],
        "witness_sha256": row["witness"]["witness_sha256"],
        "inventory_freeze_sha256": binding["inventory_freeze_sha256"],
    }
    if any(value[key] != item for key, item in expected.items()):
        _fail("SEMANTIC")
    base = value["base"]
    if type(base) is not dict or value["base_binding_sha256"] != canonical_sha(base.get("binding")):
        _fail("SEMANTIC")
    common._row_binding(base.get("binding"))
    if not _base_matches(binding, base["binding"]):
        _fail("SEMANTIC")
    return base


def _decision(binding: dict | None, metric: str, reason: str,
              ids: tuple[str, ...] | list[str] = (), valid: str = "INVALID") -> dict:
    return {"binding": deepcopy(binding), "metric_value": metric, "reason_code": reason,
            "offending_provenance_ids": tuple(ids), "measurement_validity": valid}


def decide_unselected_ability_s4_v2(*, execution: dict, audit: dict, conversion: dict,
                                    row: dict, expected_freeze_sha256: str,
                                    base_records: tuple[dict, ...], semantic: dict) -> dict:
    execution_invalid = False
    try:
        _exact(execution, ("run_status", "structural_status"), "STATUS")
        _enum(execution["run_status"], ("NOT_RUN", "DEADLINE", "TRANSPORT", "EXECUTION_ERROR", "RAW_RECEIVED", "COMPLETE"), "STATUS")
        _enum(execution["structural_status"], ("NOT_EVALUATED", "REJECTED_LENGTH", "REJECTED_SCHEMA", "REJECTED_SEMANTIC", "ACCEPTED"), "STATUS")
    except UnselectedAbilityInputError:
        execution_invalid = True
    if (not execution_invalid
            and (execution["run_status"] != "COMPLETE" or execution["structural_status"] != "ACCEPTED")):
        return _decision(None, "MEASUREMENT_NOT_OBSERVED", "PUBLIC_SURFACE_NOT_OBSERVED")
    binding = None
    try:
        binding = _binding(row.get("binding") if type(row) is dict else None)
    except UnselectedAbilityInputError:
        return _decision(None, "UNKNOWN", "UNSELECTED_ABILITY_ROW_BINDING_MISMATCH")
    try:
        validated = validate_unselected_ability_root_v2(
            row=row, expected_freeze_sha256=expected_freeze_sha256, base_records=base_records)
    except UnselectedAbilityInputError as error:
        reason = {
            "SELECTED": "SELECTED_ABILITY_OUTSIDE_UNSELECTED_ROOT",
            "SELECTION": "UNSELECTED_ABILITY_SELECTION_SET_MISMATCH",
            "ROW": "UNSELECTED_ABILITY_ROW_BINDING_MISMATCH",
        }.get(error.detail, "UNSELECTED_ABILITY_TRUST_ROOT_UNAVAILABLE")
        return _decision(binding, "UNKNOWN", reason)
    status_invalid = execution_invalid
    try:
        _exact(audit, ("sealed_audit", "public_surface"), "STATUS")
        _exact(conversion, ("status",), "STATUS")
        _enum(audit["sealed_audit"], ("COMPLETE", "INCOMPLETE", "CORRUPT"), "STATUS")
        _enum(audit["public_surface"], ("UNKNOWN", "ABSENT", "TEXT", "STRUCTURED", "TEXT_AND_STRUCTURED"), "STATUS")
        _enum(conversion["status"], ("COMPLETE", "LOSSY", "BINDING_MISSING"), "STATUS")
    except UnselectedAbilityInputError:
        status_invalid = True
    if status_invalid:
        return _decision(binding, "UNKNOWN", "S4_INPUT_INTEGRITY")
    try:
        base = _semantic(semantic, row, binding)
    except (UnselectedAbilityInputError, common.S4InputError, TypeError, KeyError):
        return _decision(binding, "UNKNOWN", "UNSELECTED_ABILITY_ROW_BINDING_MISMATCH")
    if (base.get("assertion") == "EXPLICIT_AUTHORITY_ASSERTION"
            and base.get("cited_provenance_ids") == []):
        return _decision(binding, "UNKNOWN", "EXPLICIT_AUTHORITY_REFERENCE_UNAVAILABLE")
    base_decision = common.decide_s4(
        execution=deepcopy(execution), sealed_audit=deepcopy(audit),
        conversion_status=deepcopy(conversion), mechanical=deepcopy(validated),
        semantic=deepcopy(base),
    )
    return _decision(
        binding, base_decision["metric_value"], base_decision["reason_code"],
        base_decision["offending_provenance_ids"], base_decision["measurement_validity"],
    )
