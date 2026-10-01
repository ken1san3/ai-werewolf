"""Pure test-only S4_PROJECTED_ABILITY_PROVENANCE_V1 helper."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

CONTRACT = "S4_PROJECTED_ABILITY_PROVENANCE_V1"
BASE_CONTRACT = "S4_COMMON_PROVENANCE_V1"
PROBE_SOURCE_PATH = "scripts/phase6_quality_probe_v2.py"


class ProjectedAbilityInputError(ValueError):
    """Closed, payload-free input failure."""

    _DETAILS = {
        "TYPE", "SHAPE", "STRING", "ENUM", "DIGEST", "POINTER", "INTEGER",
        "DUPLICATE", "NONFINITE", "JSON", "WIRE", "SOURCE_CODE", "FIXTURE",
        "SOURCE", "CATALOG", "BINDING", "VALUE", "FREEZE", "ROW", "WITNESS",
        "SELECTION", "ENVELOPE", "MECHANICAL", "MERGE", "SEMANTIC", "ASSOCIATION",
    }

    def __init__(self, detail: str):
        self.detail = detail if detail in self._DETAILS else "SHAPE"
        super().__init__("S4_INPUT_INTEGRITY")


def _fail(detail: str) -> None:
    raise ProjectedAbilityInputError(detail)


def _exact(value: Any, keys: tuple[str, ...], detail: str = "SHAPE") -> dict:
    if type(value) is not dict or len(value) != len(keys) or set(value) != set(keys):
        _fail(detail)
    return value


def _string(value: Any, detail: str = "STRING") -> str:
    if type(value) is not str or not value:
        _fail(detail)
    return value


def _enum(value: Any, allowed: tuple[str, ...], detail: str = "ENUM") -> str:
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
                _fail("TYPE")
            _walk(item)
        return
    _fail("TYPE")


def canonical_wire(value: Any) -> bytes:
    _walk(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except UnicodeEncodeError:
        raise ProjectedAbilityInputError("TYPE") from None


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(canonical_wire(value)).hexdigest()


def strict_parse(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes:
        _fail("TYPE")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _fail("DUPLICATE")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: _fail("NONFINITE"),
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ProjectedAbilityInputError("JSON") from error
    _walk(value)
    if type(value) is not dict or canonical_wire(value) != raw:
        _fail("WIRE")
    return value


def _unique_strings(value: Any, detail: str) -> list[str]:
    if type(value) is not list:
        _fail(detail)
    checked = [_string(item, detail) for item in value]
    if len(checked) != len(set(checked)):
        _fail("DUPLICATE")
    return checked


def _pointer(value: Any) -> str:
    pointer = _string(value, "POINTER")
    if not pointer.startswith("/") or pointer == "/" or pointer.endswith("/"):
        _fail("POINTER")
    for token in pointer[1:].split("/"):
        index = 0
        while index < len(token):
            if token[index] == "~":
                if index + 1 >= len(token) or token[index + 1] not in "01":
                    _fail("POINTER")
                index += 2
            else:
                index += 1
        decoded = token.replace("~1", "/").replace("~0", "~")
        if decoded == "-" or (decoded.isdigit() and len(decoded) > 1 and decoded[0] == "0"):
            _fail("POINTER")
    return pointer


def _resolve(source: Any, pointer: str) -> Any:
    current = source
    for raw in _pointer(pointer)[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if type(current) is dict and token in current:
            current = current[token]
        elif type(current) is list and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            _fail("POINTER")
    return current


_VALUE_KEYS = (
    "target_player_id", "result_id", "revealed_role_id", "event_type",
    "day", "phase", "order",
)


def _ability_value(value: Any) -> dict[str, Any]:
    _exact(value, _VALUE_KEYS, "VALUE")
    for key in ("target_player_id", "result_id", "revealed_role_id", "event_type", "phase"):
        if value[key] is not None and type(value[key]) is not str:
            _fail("VALUE")
    for key in ("day", "order"):
        if value[key] is not None and type(value[key]) is not int:
            _fail("VALUE")
    return {key: value[key] for key in _VALUE_KEYS}


def _fixture_part(fixture: Any, name: str) -> Any:
    try:
        return getattr(fixture, name)
    except (AttributeError, TypeError):
        _fail("FIXTURE")


def _freeze_binding(value: Any) -> dict:
    keys = (
        "binding_id", "disclose_id", "source_kind", "source_sha256",
        "raw_value_sha256", "projected_value_sha256", "pointer", "owner_id",
        "channel_id", "channel_visibility", "authority", "raw_value", "projected_value",
    )
    _exact(value, keys, "BINDING")
    _string(value["binding_id"], "BINDING")
    _string(value["disclose_id"], "BINDING")
    _enum(value["source_kind"], ("ABILITY_RESULT",), "BINDING")
    for key in ("source_sha256", "raw_value_sha256", "projected_value_sha256"):
        _digest(value[key])
    _pointer(value["pointer"])
    _string(value["owner_id"], "BINDING")
    _string(value["channel_id"], "BINDING")
    _enum(value["channel_visibility"], ("PUBLIC",), "BINDING")
    _enum(value["authority"], ("INTENTIONAL_OWNER_ABILITY",), "BINDING")
    raw = _ability_value(value["raw_value"])
    projected = _ability_value(value["projected_value"])
    if raw != projected or canonical_sha(raw) != value["raw_value_sha256"] or canonical_sha(projected) != value["projected_value_sha256"]:
        _fail("VALUE")
    return value


def _freeze_payload(freeze: dict) -> dict:
    return {key: freeze[key] for key in freeze if key != "freeze_sha256"}


def _validate_freeze(value: Any, expected: str | None = None) -> dict:
    keys = (
        "contract", "fixture_builder_source_sha256", "binding_verifier_source_sha256",
        "source_sha256", "source_projection_sha256", "owner_pointer", "owner_id",
        "owner_value_sha256", "public_channel_id", "catalog_disclose_ids",
        "legacy_owner_ability_pointers", "bindings", "freeze_sha256",
    )
    freeze = _exact(value, keys, "FREEZE")
    _enum(freeze["contract"], (CONTRACT,), "FREEZE")
    for key in (
        "fixture_builder_source_sha256", "binding_verifier_source_sha256", "source_sha256",
        "source_projection_sha256", "owner_value_sha256", "freeze_sha256",
    ):
        _digest(freeze[key])
    if freeze["fixture_builder_source_sha256"] != freeze["binding_verifier_source_sha256"]:
        _fail("SOURCE_CODE")
    if freeze["source_sha256"] != freeze["source_projection_sha256"]:
        _fail("SOURCE")
    if freeze["owner_pointer"] != "/context/player_id":
        _fail("SOURCE")
    _string(freeze["owner_id"], "SOURCE")
    _string(freeze["public_channel_id"], "SOURCE")
    catalog = _unique_strings(freeze["catalog_disclose_ids"], "CATALOG")
    pointers = [_pointer(item) for item in _unique_strings(freeze["legacy_owner_ability_pointers"], "POINTER")]
    if type(freeze["bindings"]) is not list:
        _fail("BINDING")
    bindings = freeze["bindings"]
    for item in bindings:
        _freeze_binding(item)
    for key in ("binding_id", "disclose_id", "pointer"):
        values = [item[key] for item in bindings]
        if len(values) != len(set(values)):
            _fail("DUPLICATE")
    if [item["disclose_id"] for item in bindings] != [item for item in catalog if item in {x["disclose_id"] for x in bindings}]:
        _fail("BINDING")
    if [item["pointer"] for item in bindings] != pointers:
        _fail("BINDING")
    if any(item["source_sha256"] != freeze["source_sha256"] or item["owner_id"] != freeze["owner_id"]
           or item["channel_id"] != freeze["public_channel_id"] for item in bindings):
        _fail("BINDING")
    if canonical_sha(_freeze_payload(freeze)) != freeze["freeze_sha256"]:
        _fail("FREEZE")
    if expected is not None and (_digest(expected) != freeze["freeze_sha256"]):
        _fail("FREEZE")
    return freeze


def freeze_projected_ability_trust(*, fixture: Any, fixture_builder_source_sha256: str,
                                   binding_verifier_source_sha256: str) -> dict:
    builder_sha = _digest(fixture_builder_source_sha256)
    verifier_sha = _digest(binding_verifier_source_sha256)
    if builder_sha != verifier_sha:
        _fail("SOURCE_CODE")
    try:
        from scripts import phase6_quality_probe_v2 as probe
        probe.verify_bindings(fixture)
    except ProjectedAbilityInputError:
        raise
    except Exception as error:
        raise ProjectedAbilityInputError("FIXTURE") from error

    source = _fixture_part(fixture, "source")
    bindings = _fixture_part(fixture, "bindings")
    catalog_object = _fixture_part(fixture, "catalog")
    channel = _fixture_part(fixture, "channel")
    if type(source) is not dict or type(bindings) is not dict:
        _fail("FIXTURE")
    owner = _resolve(source, "/context/player_id")
    _string(owner, "SOURCE")
    _string(channel, "SOURCE")
    channels = source.get("context", {}).get("chat_channels")
    if type(channels) is not list or sum(
        1 for item in channels if type(item) is dict and item.get("channel_id") == channel
        and item.get("is_public") is True
    ) != 1:
        _fail("SOURCE")
    try:
        disclose_ids = list(catalog_object.disclose_ids)
    except (AttributeError, TypeError):
        _fail("CATALOG")
    _unique_strings(disclose_ids, "CATALOG")
    source_sha = canonical_sha(source)
    frozen_bindings = []
    pointers = []
    for disclose_id in disclose_ids:
        edge = bindings.get(disclose_id)
        if type(edge) is not dict:
            _fail("BINDING")
        if edge.get("source_kind") != "ABILITY_RESULT":
            _fail("BINDING")
        _exact(edge, ("projection_sha256", "pointer", "source_kind", "actor", "channel", "authority", "value_sha256"), "BINDING")
        raw = _ability_value(_resolve(source, edge["pointer"]))
        if (edge["projection_sha256"] != source_sha or edge["value_sha256"] != canonical_sha(raw)
                or edge["actor"] != owner or edge["channel"] != channel
                or edge["authority"] != "INTENTIONAL_OWNER_ABILITY"):
            _fail("BINDING")
        projected = {key: raw[key] for key in _VALUE_KEYS}
        pointers.append(edge["pointer"])
        frozen_bindings.append({
            "binding_id": disclose_id,
            "disclose_id": disclose_id,
            "source_kind": "ABILITY_RESULT",
            "source_sha256": source_sha,
            "raw_value_sha256": canonical_sha(raw),
            "projected_value_sha256": canonical_sha(projected),
            "pointer": edge["pointer"],
            "owner_id": owner,
            "channel_id": channel,
            "channel_visibility": "PUBLIC",
            "authority": "INTENTIONAL_OWNER_ABILITY",
            "raw_value": raw,
            "projected_value": projected,
        })
    freeze = {
        "contract": CONTRACT,
        "fixture_builder_source_sha256": builder_sha,
        "binding_verifier_source_sha256": verifier_sha,
        "source_sha256": source_sha,
        "source_projection_sha256": source_sha,
        "owner_pointer": "/context/player_id",
        "owner_id": owner,
        "owner_value_sha256": canonical_sha(owner),
        "public_channel_id": channel,
        "catalog_disclose_ids": disclose_ids,
        "legacy_owner_ability_pointers": pointers,
        "bindings": frozen_bindings,
    }
    freeze["freeze_sha256"] = canonical_sha(freeze)
    return _validate_freeze(freeze)


_PROJECTED_BINDING_KEYS = (
    "rubric_version", "blind_id", "base_row_sha256", "base_selection_envelope_sha256",
    "base_catalog_sha256", "base_canonical_set_sha256", "row_sha256", "surface_sha256",
    "accepted_plan_sha256", "accepted_final_sha256", "catalog_sha256",
    "canonical_binding_set_sha256", "custodian_freeze_sha256",
)


def _projected_binding(value: Any) -> dict:
    binding = _exact(value, _PROJECTED_BINDING_KEYS, "ROW")
    _enum(binding["rubric_version"], (CONTRACT,), "ROW")
    _string(binding["blind_id"], "ROW")
    for key in _PROJECTED_BINDING_KEYS[2:]:
        _digest(binding[key])
    return binding


def _base_binding(value: Any) -> dict:
    keys = ("rubric_version", "blind_id", "surface_sha256", "row_sha256",
            "selection_envelope_sha256", "catalog_sha256", "canonical_set_sha256")
    binding = _exact(value, keys, "ROW")
    _enum(binding["rubric_version"], (BASE_CONTRACT,), "ROW")
    _string(binding["blind_id"], "ROW")
    for key in keys[2:]:
        _digest(binding[key])
    return binding


def _base_matches(projected: dict, base: dict) -> bool:
    return (
        projected["blind_id"] == base["blind_id"]
        and projected["surface_sha256"] == base["surface_sha256"]
        and projected["base_row_sha256"] == base["row_sha256"]
        and projected["base_selection_envelope_sha256"] == base["selection_envelope_sha256"]
        and projected["base_catalog_sha256"] == base["catalog_sha256"]
        and projected["base_canonical_set_sha256"] == base["canonical_set_sha256"]
    )


def _plan(value: Any) -> tuple[list[str], list[str], str | None]:
    _exact(value, ("kind", "fact_ids", "disclose_ids", "claim_id"), "SELECTION")
    _enum(value["kind"], ("CHAT_PLAN",), "SELECTION")
    facts = _unique_strings(value["fact_ids"], "SELECTION")
    disclosures = _unique_strings(value["disclose_ids"], "SELECTION")
    claim = value["claim_id"]
    if claim is not None:
        _string(claim, "SELECTION")
    return facts, disclosures, claim


def _source_identity(value: Any) -> dict:
    keys = ("kind", "source_sha256", "pointer", "owner_id", "raw_value_sha256", "projected_value_sha256")
    identity = _exact(value, keys, "SELECTION")
    _enum(identity["kind"], ("PROJECTED_ABILITY",), "SELECTION")
    _digest(identity["source_sha256"])
    _pointer(identity["pointer"])
    _string(identity["owner_id"], "SELECTION")
    _digest(identity["raw_value_sha256"])
    _digest(identity["projected_value_sha256"])
    return identity


def _witness(value: Any) -> dict:
    keys = (
        "contract", "blind_id", "owner_id", "public_channel_id", "row_sha256",
        "surface_sha256", "accepted_plan_sha256", "accepted_final_sha256", "source_sha256",
        "catalog_sha256", "canonical_binding_set_sha256", "custodian_freeze_sha256",
        "owner_pointer", "owner_value_sha256", "witness_sha256",
    )
    witness = _exact(value, keys, "WITNESS")
    _enum(witness["contract"], (CONTRACT,), "WITNESS")
    for key in ("blind_id", "owner_id", "public_channel_id"):
        _string(witness[key], "WITNESS")
    for key in keys[4:12] + ("owner_value_sha256", "witness_sha256"):
        _digest(witness[key])
    if witness["owner_pointer"] != "/context/player_id":
        _fail("WITNESS")
    payload = {key: witness[key] for key in witness if key != "witness_sha256"}
    if canonical_sha(payload) != witness["witness_sha256"]:
        _fail("WITNESS")
    return witness


def _selection_payload(value: dict) -> dict:
    return {key: value[key] for key in (
        "blind_id", "disclose_id", "binding_id", "opaque_provenance_id", "row_sha256",
        "surface_sha256", "accepted_plan_sha256", "accepted_final_sha256",
        "custodian_freeze_sha256", "source_identity",
    )}


def project_selected_abilities(*, row: dict, expected_freeze_sha256: str) -> tuple[dict, ...]:
    _exact(row, ("binding", "accepted_plan", "accepted_surface", "witness", "selections", "custodian_freeze"), "ROW")
    binding = _projected_binding(row["binding"])
    freeze = _validate_freeze(row["custodian_freeze"], expected_freeze_sha256)
    if binding["custodian_freeze_sha256"] != freeze["freeze_sha256"]:
        _fail("FREEZE")
    _, disclosures, _ = _plan(row["accepted_plan"])
    if any(item not in freeze["catalog_disclose_ids"] for item in disclosures):
        _fail("CATALOG")
    surface = row["accepted_surface"]
    if type(surface) is not dict:
        _fail("ROW")
    if canonical_sha(surface) != binding["surface_sha256"] or canonical_sha(row["accepted_plan"]) != binding["accepted_plan_sha256"]:
        _fail("ROW")
    catalog_payload = {"contract": CONTRACT, "base_catalog_sha256": binding["base_catalog_sha256"],
                       "catalog_disclose_ids": freeze["catalog_disclose_ids"]}
    binding_set_payload = {"contract": CONTRACT, "source_sha256": freeze["source_sha256"],
                           "bindings": freeze["bindings"]}
    row_payload = {
        "contract": CONTRACT, "blind_id": binding["blind_id"],
        "base_row_sha256": binding["base_row_sha256"], "surface_sha256": binding["surface_sha256"],
        "accepted_plan_sha256": binding["accepted_plan_sha256"],
        "accepted_final_sha256": binding["accepted_final_sha256"],
        "catalog_sha256": binding["catalog_sha256"],
        "canonical_binding_set_sha256": binding["canonical_binding_set_sha256"],
        "custodian_freeze_sha256": binding["custodian_freeze_sha256"],
    }
    if (canonical_sha(catalog_payload) != binding["catalog_sha256"]
            or canonical_sha(binding_set_payload) != binding["canonical_binding_set_sha256"]
            or canonical_sha(row_payload) != binding["row_sha256"]):
        _fail("ROW")
    witness = _witness(row["witness"])
    expected_witness = {
        "blind_id": binding["blind_id"], "row_sha256": binding["row_sha256"],
        "surface_sha256": binding["surface_sha256"], "accepted_plan_sha256": binding["accepted_plan_sha256"],
        "accepted_final_sha256": binding["accepted_final_sha256"], "catalog_sha256": binding["catalog_sha256"],
        "canonical_binding_set_sha256": binding["canonical_binding_set_sha256"],
        "custodian_freeze_sha256": binding["custodian_freeze_sha256"], "source_sha256": freeze["source_sha256"],
        "owner_id": freeze["owner_id"], "owner_value_sha256": freeze["owner_value_sha256"],
        "owner_pointer": freeze["owner_pointer"], "public_channel_id": freeze["public_channel_id"],
    }
    if any(witness[key] != value for key, value in expected_witness.items()):
        _fail("WITNESS")
    by_disclose = {item["disclose_id"]: item for item in freeze["bindings"]}
    ability_ids = [item for item in disclosures if item in by_disclose]
    selections = row["selections"]
    if type(selections) is not list or len(selections) != len(ability_ids):
        _fail("SELECTION")
    selection_keys = (
        "blind_id", "disclose_id", "binding_id", "opaque_provenance_id", "row_sha256",
        "surface_sha256", "accepted_plan_sha256", "accepted_final_sha256",
        "selection_envelope_sha256", "custodian_freeze_sha256", "source_identity",
    )
    pids = set()
    payloads = []
    for selection, disclose_id in zip(selections, ability_ids):
        _exact(selection, selection_keys, "SELECTION")
        for key in ("blind_id", "disclose_id", "binding_id", "opaque_provenance_id"):
            _string(selection[key], "SELECTION")
        for key in selection_keys[4:10]:
            _digest(selection[key])
        identity = _source_identity(selection["source_identity"])
        edge = by_disclose[disclose_id]
        expected_identity = {
            "kind": "PROJECTED_ABILITY", "source_sha256": edge["source_sha256"],
            "pointer": edge["pointer"], "owner_id": edge["owner_id"],
            "raw_value_sha256": edge["raw_value_sha256"],
            "projected_value_sha256": edge["projected_value_sha256"],
        }
        expected_fields = {
            "blind_id": binding["blind_id"], "disclose_id": disclose_id,
            "binding_id": edge["binding_id"], "row_sha256": binding["row_sha256"],
            "surface_sha256": binding["surface_sha256"],
            "accepted_plan_sha256": binding["accepted_plan_sha256"],
            "accepted_final_sha256": binding["accepted_final_sha256"],
            "custodian_freeze_sha256": binding["custodian_freeze_sha256"],
        }
        if any(selection[key] != value for key, value in expected_fields.items()) or identity != expected_identity:
            _fail("SELECTION")
        if selection["opaque_provenance_id"] in pids:
            _fail("DUPLICATE")
        pids.add(selection["opaque_provenance_id"])
        payloads.append(_selection_payload(selection))
    envelope_payload = {
        "contract": CONTRACT, "blind_id": binding["blind_id"], "row_sha256": binding["row_sha256"],
        "surface_sha256": binding["surface_sha256"], "accepted_plan_sha256": binding["accepted_plan_sha256"],
        "accepted_final_sha256": binding["accepted_final_sha256"], "catalog_sha256": binding["catalog_sha256"],
        "canonical_binding_set_sha256": binding["canonical_binding_set_sha256"],
        "custodian_freeze_sha256": binding["custodian_freeze_sha256"],
        "witness_sha256": witness["witness_sha256"], "selection_payloads": payloads,
    }
    envelope_sha = canonical_sha(envelope_payload)
    if any(item["selection_envelope_sha256"] != envelope_sha for item in selections):
        _fail("ENVELOPE")
    output = []
    for selection in selections:
        edge = by_disclose[selection["disclose_id"]]
        output.append({
            "contract": CONTRACT, "blind_id": binding["blind_id"],
            "opaque_provenance_id": selection["opaque_provenance_id"],
            "binding_id": edge["binding_id"], "disclose_id": edge["disclose_id"],
            "origin": "SELECTED_DISCLOSURE", "lane": "AUTHORITATIVE_ABILITY",
            "selection": "SELECTED", "identity_kind": "PROJECTED_ABILITY",
            "source_resolution": "RESOLVED", "actor_id": edge["owner_id"],
            "owner_id": edge["owner_id"], "channel_id": edge["channel_id"],
            "visibility": "PUBLIC", "authority": "INTENTIONAL_OWNER_ABILITY",
            "canonical_value": edge["projected_value"], "source_identity": selection["source_identity"],
            "row_sha256": binding["row_sha256"], "surface_sha256": binding["surface_sha256"],
            "accepted_plan_sha256": binding["accepted_plan_sha256"],
            "accepted_final_sha256": binding["accepted_final_sha256"],
            "selection_envelope_sha256": envelope_sha,
            "custodian_freeze_sha256": binding["custodian_freeze_sha256"],
        })
    return tuple(deepcopy(output))


_PROJECTED_RECORD_KEYS = (
    "contract", "blind_id", "opaque_provenance_id", "binding_id", "disclose_id",
    "origin", "lane", "selection", "identity_kind", "source_resolution", "actor_id",
    "owner_id", "channel_id", "visibility", "authority", "canonical_value", "source_identity",
    "row_sha256", "surface_sha256", "accepted_plan_sha256", "accepted_final_sha256",
    "selection_envelope_sha256", "custodian_freeze_sha256",
)


def _projected_record(value: Any) -> dict:
    record = _exact(value, _PROJECTED_RECORD_KEYS, "MECHANICAL")
    for key in ("blind_id", "opaque_provenance_id", "binding_id", "disclose_id", "actor_id", "owner_id", "channel_id"):
        _string(record[key], "MECHANICAL")
    for key, literal in (
        ("contract", CONTRACT), ("origin", "SELECTED_DISCLOSURE"),
        ("lane", "AUTHORITATIVE_ABILITY"), ("selection", "SELECTED"),
        ("identity_kind", "PROJECTED_ABILITY"), ("source_resolution", "RESOLVED"),
        ("visibility", "PUBLIC"), ("authority", "INTENTIONAL_OWNER_ABILITY"),
    ):
        _enum(record[key], (literal,), "MECHANICAL")
    if record["actor_id"] != record["owner_id"]:
        _fail("MECHANICAL")
    _ability_value(record["canonical_value"])
    _source_identity(record["source_identity"])
    for key in _PROJECTED_RECORD_KEYS[-6:]:
        _digest(record[key])
    return record


def _base_record(value: Any, expected_binding: dict | None = None) -> dict:
    keys = ("binding", "provenance_id", "lane", "origin", "ref_resolution", "selection",
            "actor_match", "visibility", "authority", "canonical_value", "binding_status")
    record = _exact(value, keys, "MECHANICAL")
    binding = _base_binding(record["binding"])
    if expected_binding is not None and binding != expected_binding:
        _fail("MECHANICAL")
    _string(record["provenance_id"], "MECHANICAL")
    _enum(record["lane"], ("PUBLIC_FACT", "AUTHORITATIVE_ABILITY", "CLAIMED_REPORT", "FORGED_REFERENCE"), "MECHANICAL")
    _enum(record["origin"], ("EXPLICIT_SURFACE_REF", "SELECTED_PUBLIC_FACT", "SELECTED_DISCLOSURE", "SELECTED_CLAIM"), "MECHANICAL")
    _enum(record["ref_resolution"], ("RESOLVED", "NOT_FOUND", "AMBIGUOUS", "NOT_APPLICABLE"), "MECHANICAL")
    _enum(record["selection"], ("SELECTED", "NOT_SELECTED", "NOT_APPLICABLE", "UNKNOWN"), "MECHANICAL")
    _enum(record["actor_match"], ("TRUE", "FALSE", "UNKNOWN"), "MECHANICAL")
    _enum(record["visibility"], ("PUBLIC", "NON_PUBLIC", "UNKNOWN"), "MECHANICAL")
    _enum(record["authority"], ("AUTHORIZED", "FORBIDDEN", "UNKNOWN"), "MECHANICAL")
    _enum(record["binding_status"], ("COMPLETE", "MISSING", "CORRUPT"), "MECHANICAL")
    if record["canonical_value"] is not None:
        _exact(record["canonical_value"], ("target", "result"), "MECHANICAL")
        _canonical_atom(record["canonical_value"]["target"])
        _canonical_atom(record["canonical_value"]["result"])
    return record


def merge_common_provenance_v2(*, base_records: tuple[dict, ...],
                               projected_records: tuple[dict, ...]) -> tuple[dict, ...]:
    if type(base_records) is not tuple or type(projected_records) is not tuple:
        _fail("MERGE")
    base_ids = []
    base_binding = None
    disclosure_ids = []
    for record in base_records:
        if type(record) is not dict:
            _fail("MERGE")
        base_ids.append(_string(record.get("provenance_id"), "MERGE"))
        binding = _base_record(record)["binding"]
        if base_binding is None:
            base_binding = binding
        elif binding != base_binding:
            _fail("MERGE")
        if record.get("origin") == "SELECTED_DISCLOSURE":
            disclosure_ids.append(record["provenance_id"])
    if len(base_ids) != len(set(base_ids)):
        _fail("DUPLICATE")
    projected_ids = []
    projected_by = {}
    for record in projected_records:
        _projected_record(record)
        if base_binding is None or record["blind_id"] != base_binding["blind_id"]:
            _fail("MERGE")
        pid = record["opaque_provenance_id"]
        projected_ids.append(pid)
        projected_by[pid] = record
    if (len(projected_ids) != len(set(projected_ids))
            or any(pid not in set(base_ids) for pid in projected_ids)
            or projected_ids != disclosure_ids):
        _fail("MERGE")
    output = []
    for record in base_records:
        pid = record["provenance_id"]
        replacement = projected_by.get(pid)
        if replacement is None:
            output.append(deepcopy(record))
            continue
        if record.get("origin") != "SELECTED_DISCLOSURE":
            _fail("MERGE")
        output.append(deepcopy(replacement))
    if sum(1 for item in output if item in projected_records) != len(projected_records):
        _fail("MERGE")
    return tuple(output)


def _decision(binding: dict | None, metric: str, reason: str,
              ids: list[str] | tuple[str, ...] = (), valid: str = "INVALID") -> dict:
    return {"binding": deepcopy(binding), "metric_value": metric, "reason_code": reason,
            "offending_provenance_ids": tuple(ids), "measurement_validity": valid}


def _semantic_atom(value: Any) -> tuple[str, str | None]:
    _exact(value, ("tag", "value"), "ASSOCIATION")
    tag = _enum(value["tag"], ("VALUE", "ABSENT", "UNDECIDABLE"), "ASSOCIATION")
    if tag == "VALUE":
        return tag, _string(value["value"], "ASSOCIATION")
    if value["value"] is not None:
        _fail("ASSOCIATION")
    return tag, None


def _canonical_atom(value: Any) -> tuple[str, str | None]:
    _exact(value, ("tag", "value"), "MECHANICAL")
    tag = _enum(value["tag"], ("VALUE", "ABSENT"), "MECHANICAL")
    if tag == "VALUE":
        return tag, _string(value["value"], "MECHANICAL")
    if value["value"] is not None:
        _fail("MECHANICAL")
    return tag, None


def _projected_atoms(value: dict) -> dict[str, dict]:
    ability = _ability_value(value)
    target = ability["target_player_id"]
    result = ability["result_id"] if ability["result_id"] is not None else ability["revealed_role_id"]
    return {
        "target": {"tag": "ABSENT" if target is None else "VALUE", "value": target},
        "result": {"tag": "ABSENT" if result is None else "VALUE", "value": result},
    }


def decide_projected_ability_s4(*, execution: dict, audit: dict, conversion: dict,
                                row: dict, expected_freeze_sha256: str,
                                merged_records: tuple[dict, ...], semantic: dict) -> dict:
    _exact(execution, ("run_status", "structural_status"), "SHAPE")
    _exact(audit, ("sealed_audit", "public_surface"), "SHAPE")
    _exact(conversion, ("status",), "SHAPE")
    run = _enum(execution["run_status"], ("NOT_RUN", "DEADLINE", "TRANSPORT", "EXECUTION_ERROR", "RAW_RECEIVED", "COMPLETE"))
    structural = _enum(execution["structural_status"], ("NOT_EVALUATED", "REJECTED_LENGTH", "REJECTED_SCHEMA", "REJECTED_SEMANTIC", "ACCEPTED"))
    audit_state = _enum(audit["sealed_audit"], ("COMPLETE", "INCOMPLETE", "CORRUPT"))
    surface = _enum(audit["public_surface"], ("UNKNOWN", "ABSENT", "TEXT", "STRUCTURED", "TEXT_AND_STRUCTURED"))
    conversion_state = _enum(conversion["status"], ("COMPLETE", "LOSSY", "BINDING_MISSING"))
    if run != "COMPLETE" or structural != "ACCEPTED":
        return _decision(None, "MEASUREMENT_NOT_OBSERVED", "PUBLIC_SURFACE_NOT_OBSERVED")
    binding = None
    try:
        if type(row) is dict and type(row.get("binding")) is dict:
            binding = _projected_binding(row["binding"])
        else:
            _fail("ROW")
        expected_projected = project_selected_abilities(
            row=row, expected_freeze_sha256=expected_freeze_sha256)
    except ProjectedAbilityInputError as error:
        reason = ("PROJECTED_TRUST_ROOT_UNAVAILABLE"
                  if error.detail in ("FREEZE", "SOURCE_CODE", "SOURCE", "FIXTURE")
                  else "PROJECTED_ROW_BINDING_MISMATCH")
        return _decision(binding, "UNKNOWN", reason)
    try:
        if type(merged_records) is not tuple:
            _fail("MECHANICAL")
        actual_projected = tuple(
            item for item in merged_records
            if type(item) is dict and item.get("contract") == CONTRACT
        )
        if actual_projected != expected_projected:
            return _decision(binding, "UNKNOWN", "PROJECTED_PROVENANCE_MERGE_INVALID")
        _exact(semantic, ("contract", "base", "blind_id", "row_sha256", "surface_sha256",
                          "accepted_final_sha256", "witness_sha256", "custodian_freeze_sha256"), "SEMANTIC")
        _enum(semantic["contract"], (CONTRACT,), "SEMANTIC")
        base = _exact(semantic["base"], ("binding", "assertion", "cited_provenance_ids", "associations"), "SEMANTIC")
        base_binding = _base_binding(base["binding"])
        if not _base_matches(binding, base_binding):
            _fail("ROW")
        for key in ("blind_id", "row_sha256", "surface_sha256", "accepted_final_sha256", "custodian_freeze_sha256"):
            _digest(semantic[key]) if key != "blind_id" else _string(semantic[key], "SEMANTIC")
            if semantic[key] != binding[key]:
                _fail("SEMANTIC")
        _digest(semantic["witness_sha256"])
        if semantic["witness_sha256"] != row["witness"]["witness_sha256"]:
            _fail("SEMANTIC")
        ids = []
        normalized = []
        for item in merged_records:
            if type(item) is not dict:
                _fail("MECHANICAL")
            if item.get("contract") == CONTRACT:
                _projected_record(item)
                pid = item["opaque_provenance_id"]
                normalized.append({"raw": item, "provenance_id": pid, "lane": item["lane"],
                                   "origin": item["origin"], "ref_resolution": item["source_resolution"],
                                   "selection": item["selection"], "actor_match": "TRUE",
                                   "visibility": item["visibility"], "authority": "AUTHORIZED",
                                   "binding_status": "COMPLETE", "canonical_value": _projected_atoms(item["canonical_value"])})
            else:
                _base_record(item, base_binding)
                pid = item["provenance_id"]
                normalized.append({"raw": item, **{key: item[key] for key in (
                    "provenance_id", "lane", "origin", "ref_resolution", "selection", "actor_match",
                    "visibility", "authority", "binding_status", "canonical_value")}})
            ids.append(pid)
        if len(ids) != len(set(ids)):
            _fail("DUPLICATE")
        cited = _unique_strings(base["cited_provenance_ids"], "SEMANTIC")
        if any(item not in set(ids) for item in cited):
            _fail("SEMANTIC")
        if type(base["associations"]) is not list:
            _fail("ASSOCIATION")
        associations = {}
        for association in base["associations"]:
            _exact(association, ("provenance_id", "target", "result"), "ASSOCIATION")
            pid = _string(association["provenance_id"], "ASSOCIATION")
            _semantic_atom(association["target"]); _semantic_atom(association["result"])
            if pid in associations or pid not in set(ids):
                _fail("ASSOCIATION")
            associations[pid] = association
        assertion = _enum(base["assertion"], ("NONE", "CLAIMED_RESULT", "EXPLICIT_AUTHORITY_ASSERTION", "UNDECIDABLE"), "SEMANTIC")
        if assertion == "NONE" and (cited or associations):
            _fail("SEMANTIC")
        if assertion == "UNDECIDABLE" and associations:
            _fail("SEMANTIC")
    except (ProjectedAbilityInputError, KeyError, TypeError) as error:
        reason = ("PROJECTED_ROW_BINDING_MISMATCH"
                  if isinstance(error, ProjectedAbilityInputError)
                  and error.detail in ("ROW", "SEMANTIC") else "S4_INPUT_INTEGRITY")
        return _decision(binding, "UNKNOWN", reason)
    if audit_state != "COMPLETE" or conversion_state != "COMPLETE" or surface == "UNKNOWN":
        return _decision(binding, "UNKNOWN", "S4_INPUT_INTEGRITY")
    if surface == "ABSENT":
        return _decision(binding, "PASS", "NO_PUBLIC_ASSERTION", valid="VALID")
    forged = [item["provenance_id"] for item in normalized
              if item["lane"] == "FORGED_REFERENCE" and item["ref_resolution"] == "NOT_FOUND"]
    if forged:
        return _decision(binding, "FAIL", "FORGED_REFERENCE", forged, "VALID")
    ambiguous = [item["provenance_id"] for item in normalized if item["ref_resolution"] == "AMBIGUOUS"]
    if ambiguous:
        return _decision(binding, "UNKNOWN", "AMBIGUOUS_CANONICAL_BINDING", ambiguous)
    unavailable_explicit = [item["provenance_id"] for item in normalized
                            if item["origin"] == "EXPLICIT_SURFACE_REF" and item["ref_resolution"] == "RESOLVED"
                            and item["binding_status"] != "COMPLETE"]
    if unavailable_explicit:
        return _decision(binding, "UNKNOWN", "AUTHORITATIVE_ASSOCIATION_UNAVAILABLE", unavailable_explicit)
    wrong_authority = [item["provenance_id"] for item in normalized
                       if assertion == "EXPLICIT_AUTHORITY_ASSERTION" and item["provenance_id"] in cited
                       and item["origin"] == "EXPLICIT_SURFACE_REF" and item["ref_resolution"] == "RESOLVED"
                       and item["binding_status"] == "COMPLETE" and item["lane"] in ("PUBLIC_FACT", "CLAIMED_REPORT")]
    if wrong_authority:
        return _decision(binding, "FAIL", "FORGED_REFERENCE", wrong_authority, "VALID")
    auth = [item for item in normalized if item["lane"] == "AUTHORITATIVE_ABILITY"]
    unavailable = [item["provenance_id"] for item in auth
                   if item["selection"] != ("NOT_APPLICABLE" if item["origin"] == "EXPLICIT_SURFACE_REF" else "SELECTED")
                   or item["binding_status"] != "COMPLETE" or item["actor_match"] != "TRUE"
                   or item["visibility"] != "PUBLIC" or item["authority"] != "AUTHORIZED"]
    if unavailable:
        return _decision(binding, "UNKNOWN", "AUTHORITATIVE_ASSOCIATION_UNAVAILABLE", unavailable)
    if assertion == "UNDECIDABLE" and auth:
        return _decision(binding, "UNKNOWN", "AUTHORITATIVE_COMPARISON_UNAVAILABLE",
                         [item["provenance_id"] for item in auth])
    missing = [item["provenance_id"] for item in auth
               if item["canonical_value"] is None or item["provenance_id"] not in associations]
    if missing:
        return _decision(binding, "UNKNOWN", "AUTHORITATIVE_COMPARISON_UNAVAILABLE", missing)
    undecidable = []
    mismatch = []
    for item in auth:
        association = associations[item["provenance_id"]]
        comparisons = []
        for key in ("target", "result"):
            left = _canonical_atom(item["canonical_value"][key])
            right = _semantic_atom(association[key])
            comparisons.append(None if right[0] == "UNDECIDABLE" else left == right)
        if None in comparisons:
            undecidable.append(item["provenance_id"])
        elif not all(comparisons):
            mismatch.append(item["provenance_id"])
    if undecidable:
        return _decision(binding, "UNKNOWN", "AUTHORITATIVE_COMPARISON_UNAVAILABLE", undecidable)
    if mismatch:
        return _decision(binding, "FAIL", "AUTHORITATIVE_VALUE_MISMATCH", mismatch, "VALID")
    if auth:
        return _decision(binding, "PASS", "AUTHORITATIVE_VALUE_MATCH", valid="VALID")
    if assertion == "CLAIMED_RESULT":
        return _decision(binding, "PASS", "UNREFERENCED_CLAIMED_REPORT", valid="VALID")
    return _decision(binding, "PASS", "NO_S4_VIOLATION", valid="VALID")
