"""Canonical, receiver-verifiable Phase 6 discussion context.

The bootstrap validator consumes protocol-independent JSON-shaped data.  It
does not import the game server or content implementation.  A pending value is
single-use: binding to the first authoritative ``SelfView`` clears its only
reference to the full manifest and returns the small player-specific context.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Literal, Mapping, TypeAlias

from ai_client.world import Freshness, WorldSnapshot

from .model import (
    MAX_ID_SCALARS,
    MAX_ID_UTF8_BYTES,
    DiscussionValidationError,
    _require_exact_bool as _model_require_exact_bool,
    _require_hash as _model_require_hash,
    _require_id as _model_require_id,
    _require_int as _model_require_int,
    _require_text as _model_require_text,
)


MANIFEST_SCHEMA_VERSION = "aiwolf.content-manifest.v1"
CONTEXT_SCHEMA_VERSION = "aiwolf.discussion-context.v1"
BOOTSTRAP_SCHEMA_VERSION = "aiwolf.discussion-bootstrap.v1"

MAX_ROOT_ENTRIES = 64
MAX_MODIFIERS = 8
MAX_WIN_CONDITIONS = 8
MAX_ABILITIES = 16
MAX_PASSIVES = 16
MAX_CHAT_CHANNELS = 8
MAX_EFFECTS_PER_CAPABILITY = 8
MAX_MANIFEST_BYTES = 64 * 1024
MAX_CONTEXT_BYTES = 8 * 1024
MAX_BOOTSTRAP_BYTES = 80 * 1024


class DiscussionContextError(DiscussionValidationError):
    """Fail-closed ``DISCUSSION_CONTEXT_INVALID`` validation error."""

    code = "DISCUSSION_CONTEXT_INVALID"


def _as_context_error(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except DiscussionContextError:
        raise
    except DiscussionValidationError as error:
        raise DiscussionContextError(str(error)) from error


def _require_exact_bool(name: str, value: object) -> None:
    _as_context_error(_model_require_exact_bool, name, value)


def _require_hash(name: str, value: object) -> str:
    return _as_context_error(_model_require_hash, name, value)


def _require_id(name: str, value: object) -> str:
    return _as_context_error(_model_require_id, name, value)


def _require_int(
    name: str,
    value: object,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> None:
    _as_context_error(
        _model_require_int,
        name,
        value,
        minimum=minimum,
        maximum=maximum,
    )


def _require_text(name: str, value: object, *, allow_empty: bool = False) -> str:
    return _as_context_error(
        _model_require_text,
        name,
        value,
        allow_empty=allow_empty,
    )


def _canonical_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _canonical_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, Mapping):
        items: list[tuple[str, object]] = []
        for key, item in value.items():
            if type(key) is not str:
                raise DiscussionContextError("canonical mapping keys must be exact strings")
            _require_text("canonical mapping key", key, allow_empty=True)
            items.append((key, _canonical_value(item)))
        return {key: item for key, item in sorted(items, key=lambda pair: pair[0])}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_canonical_value(item) for item in value]
        return sorted(normalized, key=_canonical_json_from_normalized)
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is str:
        _require_text("canonical string", value, allow_empty=True)
        return value
    raise DiscussionContextError(
        f"unsupported canonical value type: {type(value).__name__}"
    )


def _canonical_json_from_normalized(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_bytes(value: object) -> bytes:
    """Return the design's deterministic UTF-8 JSON representation."""

    return _canonical_json_from_normalized(_canonical_value(value))


def canonical_sha256(value: object) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def _bounded_id_tuple(
    owner: object,
    field_name: str,
    *,
    maximum: int,
) -> tuple[str, ...]:
    raw = getattr(owner, field_name)
    if isinstance(raw, (str, bytes)):
        raise DiscussionContextError(f"{field_name} must be a collection")
    try:
        values = tuple(raw)
    except TypeError as error:
        raise DiscussionContextError(f"{field_name} must be a collection") from error
    if len(values) > maximum:
        raise DiscussionContextError(f"{field_name} must contain at most {maximum} values")
    for index, value in enumerate(values):
        _require_id(f"{field_name}[{index}]", value)
    if len(values) != len(set(values)):
        raise DiscussionContextError(f"{field_name} must be unique")
    if values != tuple(sorted(values)):
        raise DiscussionContextError(f"{field_name} must use Unicode-code-point order")
    object.__setattr__(owner, field_name, values)
    return values


@dataclass(frozen=True)
class EliminateRoleTagWinCondition:
    type: Literal["eliminate_role_tag"]
    tag: str

    def __post_init__(self) -> None:
        if self.type != "eliminate_role_tag":
            raise DiscussionContextError("invalid eliminate-role-tag discriminator")
        _require_id("tag", self.tag)


@dataclass(frozen=True)
class CountParityWinCondition:
    type: Literal["count_parity"]
    subject: str
    against: str
    operator: Literal["gte", "gt", "eq", "lte", "lt"]

    def __post_init__(self) -> None:
        if self.type != "count_parity":
            raise DiscussionContextError("invalid count-parity discriminator")
        _require_id("subject", self.subject)
        _require_id("against", self.against)
        if self.operator not in {"gte", "gt", "eq", "lte", "lt"}:
            raise DiscussionContextError("invalid count-parity operator")


@dataclass(frozen=True)
class SurviveWhenOthersWinCondition:
    type: Literal["survive_when_others_win"]
    replaces: bool

    def __post_init__(self) -> None:
        if self.type != "survive_when_others_win":
            raise DiscussionContextError("invalid survive-win discriminator")
        _require_exact_bool("replaces", self.replaces)


DiscussionWinCondition: TypeAlias = (
    EliminateRoleTagWinCondition
    | CountParityWinCondition
    | SurviveWhenOthersWinCondition
)


@dataclass(frozen=True)
class DiscussionAbilityContext:
    ability_id: str
    timing: str
    available_from_night: int
    priority: int
    resolution: str
    target_selector: str
    target_count: int
    uses_per_night: int | None
    uses_per_game: int | None
    no_selection: str
    effect_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("ability_id", "timing", "resolution", "target_selector", "no_selection"):
            _require_id(name, getattr(self, name))
        for name in ("available_from_night", "priority", "target_count"):
            _require_int(name, getattr(self, name))
        for name in ("uses_per_night", "uses_per_game"):
            value = getattr(self, name)
            if value is not None:
                _require_int(name, value)
        _bounded_id_tuple(self, "effect_ids", maximum=MAX_EFFECTS_PER_CAPABILITY)


@dataclass(frozen=True)
class DiscussionPassiveContext:
    type: str
    priority: int
    effect_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_id("type", self.type)
        _require_int("priority", self.priority)
        _bounded_id_tuple(self, "effect_ids", maximum=MAX_EFFECTS_PER_CAPABILITY)


@dataclass(frozen=True)
class AuthorizedChatChannelContext:
    channel_id: str
    is_public: bool

    def __post_init__(self) -> None:
        _require_id("channel_id", self.channel_id)
        _require_exact_bool("is_public", self.is_public)


@dataclass(frozen=True)
class AuthorizedDiscussionContext:
    schema_version: Literal["aiwolf.discussion-context.v1"]
    game_id: str
    player_id: str
    role_id: str
    modifier_ids: tuple[str, ...]
    content_manifest_sha256: str
    team: str
    count_as: str
    attack_result: str
    inspect_result: str
    medium_result: str
    win_conditions: tuple[DiscussionWinCondition, ...]
    abilities: tuple[DiscussionAbilityContext, ...]
    passives: tuple[DiscussionPassiveContext, ...]
    chat_channels: tuple[AuthorizedChatChannelContext, ...]
    knows_teammates: bool
    authorized_known_player_ids: tuple[str, ...]
    known_players_complete: bool

    def __post_init__(self) -> None:
        if self.schema_version != CONTEXT_SCHEMA_VERSION:
            raise DiscussionContextError("invalid AuthorizedDiscussionContext schema_version")
        for name in (
            "game_id",
            "player_id",
            "role_id",
            "team",
            "count_as",
            "attack_result",
            "inspect_result",
            "medium_result",
        ):
            _require_id(name, getattr(self, name))
        _require_hash("content_manifest_sha256", self.content_manifest_sha256)
        _bounded_id_tuple(self, "modifier_ids", maximum=MAX_MODIFIERS)
        _require_exact_bool("knows_teammates", self.knows_teammates)
        _require_exact_bool("known_players_complete", self.known_players_complete)
        known = _bounded_id_tuple(
            self, "authorized_known_player_ids", maximum=MAX_ROOT_ENTRIES
        )
        if known or self.known_players_complete:
            raise DiscussionContextError(
                "Phase 6 requires empty authorized_known_player_ids and known_players_complete=false"
            )

        wins = tuple(self.win_conditions)
        abilities = tuple(self.abilities)
        passives = tuple(self.passives)
        channels = tuple(self.chat_channels)
        typed = (
            ("win_conditions", wins, MAX_WIN_CONDITIONS, (EliminateRoleTagWinCondition, CountParityWinCondition, SurviveWhenOthersWinCondition)),
            ("abilities", abilities, MAX_ABILITIES, (DiscussionAbilityContext,)),
            ("passives", passives, MAX_PASSIVES, (DiscussionPassiveContext,)),
            ("chat_channels", channels, MAX_CHAT_CHANNELS, (AuthorizedChatChannelContext,)),
        )
        for name, values, maximum, allowed in typed:
            if len(values) > maximum or any(not isinstance(value, allowed) for value in values):
                raise DiscussionContextError(f"invalid {name}")
            object.__setattr__(self, name, values)
        if wins != tuple(sorted(wins, key=canonical_json_bytes)):
            raise DiscussionContextError("win_conditions must use canonical-byte order")
        if abilities != tuple(sorted(abilities, key=lambda item: item.ability_id)):
            raise DiscussionContextError("abilities must be ordered by ability_id")
        if len({item.ability_id for item in abilities}) != len(abilities):
            raise DiscussionContextError("ability IDs must be unique")
        if passives != tuple(sorted(passives, key=canonical_json_bytes)):
            raise DiscussionContextError("passives must use canonical-byte order")
        channel_ids = tuple(item.channel_id for item in channels)
        if channel_ids != tuple(sorted(channel_ids)) or len(channel_ids) != len(set(channel_ids)):
            raise DiscussionContextError("chat channels must be sorted and unique")
        if len(canonical_json_bytes(self)) > MAX_CONTEXT_BYTES:
            raise DiscussionContextError("authorized discussion context exceeds 8 KiB")


@dataclass(frozen=True)
class BoundDiscussionContext:
    manifest_sha256: str
    context_sha256: str
    context: AuthorizedDiscussionContext

    def __post_init__(self) -> None:
        _require_hash("manifest_sha256", self.manifest_sha256)
        _require_hash("context_sha256", self.context_sha256)
        if not isinstance(self.context, AuthorizedDiscussionContext):
            raise DiscussionContextError("context must be AuthorizedDiscussionContext")
        if self.context.content_manifest_sha256 != self.manifest_sha256:
            raise DiscussionContextError("bound manifest hash mismatch")
        if canonical_sha256(self.context) != self.context_sha256:
            raise DiscussionContextError("bound context hash mismatch")


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DiscussionContextError(f"{path} must be an object")
    if any(type(key) is not str for key in value):
        raise DiscussionContextError(f"{path} keys must be exact strings")
    return value


def _keys(value: Mapping[str, Any], required: set[str], path: str) -> None:
    actual = set(value)
    if actual != required:
        missing = sorted(required - actual)
        extra = sorted(actual - required)
        raise DiscussionContextError(f"{path} keys mismatch: missing={missing}, extra={extra}")


def _sequence(value: object, path: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise DiscussionContextError(f"{path} must be an array")
    return tuple(value)


def _optional_int(path: str, value: object) -> None:
    if value is not None:
        _require_int(path, value)


def _optional_text(path: str, value: object) -> None:
    if value is not None:
        _require_text(path, value)


def _id_array(
    value: object,
    path: str,
    *,
    maximum: int = MAX_ROOT_ENTRIES,
    sorted_unique: bool = False,
) -> tuple[str, ...]:
    values = _sequence(value, path)
    if len(values) > maximum:
        raise DiscussionContextError(f"{path} contains too many values")
    for index, item in enumerate(values):
        _require_id(f"{path}[{index}]", item)
    if len(values) != len(set(values)):
        raise DiscussionContextError(f"{path} contains duplicate IDs")
    if sorted_unique and values != tuple(sorted(values)):
        raise DiscussionContextError(f"{path} is not canonically ordered")
    return values


def _validate_effect_reference(value: object, path: str) -> None:
    data = _mapping(value, path)
    _keys(data, {"id", "priority"}, path)
    _require_id(f"{path}.id", data["id"])
    _require_int(f"{path}.priority", data["priority"])


def _validate_win_condition(value: object, path: str) -> None:
    data = _mapping(value, path)
    _keys(data, {"type", "data"}, path)
    condition_type = _require_id(f"{path}.type", data["type"])
    body = _mapping(data["data"], f"{path}.data")
    if condition_type == "eliminate_role_tag":
        _keys(body, {"type", "tag"}, f"{path}.data")
        _require_id(f"{path}.data.tag", body["tag"])
    elif condition_type == "count_parity":
        _keys(body, {"type", "subject", "against", "operator"}, f"{path}.data")
        _require_id(f"{path}.data.subject", body["subject"])
        _require_id(f"{path}.data.against", body["against"])
        if body["operator"] not in {"gte", "gt", "eq", "lte", "lt"}:
            raise DiscussionContextError(f"{path}.data.operator is invalid")
    elif condition_type == "survive_when_others_win":
        _keys(body, {"type", "replaces"}, f"{path}.data")
        _require_exact_bool(f"{path}.data.replaces", body["replaces"])
    else:
        raise DiscussionContextError(f"{path}.type is unsupported")
    if body["type"] != condition_type:
        raise DiscussionContextError(f"{path} type/data discriminator mismatch")


def _validate_ability(value: object, path: str) -> None:
    data = _mapping(value, path)
    _keys(
        data,
        {
            "id",
            "timing",
            "available_from_night",
            "priority",
            "resolution",
            "target",
            "uses",
            "no_selection",
            "restrictions",
            "effects",
            "description",
        },
        path,
    )
    for name in ("id", "timing", "resolution", "no_selection"):
        _require_id(f"{path}.{name}", data[name])
    _require_int(f"{path}.available_from_night", data["available_from_night"])
    _require_int(f"{path}.priority", data["priority"])
    _optional_text(f"{path}.description", data["description"])

    target = _mapping(data["target"], f"{path}.target")
    _keys(target, {"selector", "count", "options"}, f"{path}.target")
    _require_id(f"{path}.target.selector", target["selector"])
    _require_int(f"{path}.target.count", target["count"])
    _mapping(target["options"], f"{path}.target.options")

    uses = _mapping(data["uses"], f"{path}.uses")
    _keys(uses, {"per_night", "per_game"}, f"{path}.uses")
    _optional_int(f"{path}.uses.per_night", uses["per_night"])
    _optional_int(f"{path}.uses.per_game", uses["per_game"])

    restrictions = _sequence(data["restrictions"], f"{path}.restrictions")
    if len(restrictions) > MAX_ROOT_ENTRIES:
        raise DiscussionContextError(f"{path}.restrictions contains too many values")
    for index, item in enumerate(restrictions):
        item_path = f"{path}.restrictions[{index}]"
        restriction = _mapping(item, item_path)
        _keys(restriction, {"type", "enabled_when", "options"}, item_path)
        _require_id(f"{item_path}.type", restriction["type"])
        _optional_text(f"{item_path}.enabled_when", restriction["enabled_when"])
        _mapping(restriction["options"], f"{item_path}.options")

    effects = _sequence(data["effects"], f"{path}.effects")
    if len(effects) > MAX_EFFECTS_PER_CAPABILITY:
        raise DiscussionContextError(f"{path}.effects contains too many values")
    for index, item in enumerate(effects):
        _validate_effect_reference(item, f"{path}.effects[{index}]")


def _validate_passive(value: object, path: str) -> None:
    data = _mapping(value, path)
    _keys(data, {"type", "priority", "rules", "effects"}, path)
    _require_id(f"{path}.type", data["type"])
    _require_int(f"{path}.priority", data["priority"])
    rules = _sequence(data["rules"], f"{path}.rules")
    if len(rules) > MAX_ROOT_ENTRIES:
        raise DiscussionContextError(f"{path}.rules contains too many values")
    for index, rule in enumerate(rules):
        _mapping(rule, f"{path}.rules[{index}]")
    effects = _sequence(data["effects"], f"{path}.effects")
    if len(effects) > MAX_EFFECTS_PER_CAPABILITY:
        raise DiscussionContextError(f"{path}.effects contains too many values")
    for index, item in enumerate(effects):
        _validate_effect_reference(item, f"{path}.effects[{index}]")


def _validate_knowledge(value: object, path: str) -> None:
    data = _mapping(value, path)
    _keys(data, {"declarations"}, path)
    declarations = _mapping(data["declarations"], f"{path}.declarations")
    _keys(declarations, {"knows_teammates"}, f"{path}.declarations")
    _require_exact_bool(f"{path}.declarations.knows_teammates", declarations["knows_teammates"])


def _validate_role(value: object, path: str) -> None:
    data = _mapping(value, path)
    _keys(
        data,
        {
            "id",
            "name",
            "claimable",
            "attributes",
            "tags",
            "knowledge",
            "chat_channels",
            "abilities",
            "passives",
            "options",
        },
        path,
    )
    _require_id(f"{path}.id", data["id"])
    _require_text(f"{path}.name", data["name"])
    _require_exact_bool(f"{path}.claimable", data["claimable"])
    attributes = _mapping(data["attributes"], f"{path}.attributes")
    attribute_names = {"team", "count_as", "attack_result", "inspect_result", "medium_result"}
    _keys(attributes, attribute_names, f"{path}.attributes")
    for name in attribute_names:
        _require_id(f"{path}.attributes.{name}", attributes[name])
    _id_array(data["tags"], f"{path}.tags", sorted_unique=True)
    _validate_knowledge(data["knowledge"], f"{path}.knowledge")
    _id_array(data["chat_channels"], f"{path}.chat_channels")
    abilities = _sequence(data["abilities"], f"{path}.abilities")
    if len(abilities) > MAX_ABILITIES:
        raise DiscussionContextError(f"{path}.abilities contains too many values")
    for index, ability in enumerate(abilities):
        _validate_ability(ability, f"{path}.abilities[{index}]")
    passives = _sequence(data["passives"], f"{path}.passives")
    if len(passives) > MAX_PASSIVES:
        raise DiscussionContextError(f"{path}.passives contains too many values")
    for index, passive in enumerate(passives):
        _validate_passive(passive, f"{path}.passives[{index}]")
    options = _mapping(data["options"], f"{path}.options")
    if len(options) > MAX_ROOT_ENTRIES:
        raise DiscussionContextError(f"{path}.options contains too many values")
    for option_id, value in options.items():
        _require_id(f"{path}.options key", option_id)
        option = _mapping(value, f"{path}.options.{option_id}")
        _keys(option, {"type", "values", "default"}, f"{path}.options.{option_id}")
        _require_id(f"{path}.options.{option_id}.type", option["type"])
        _sequence(option["values"], f"{path}.options.{option_id}.values")


def _validate_modifier(value: object, path: str) -> None:
    data = _mapping(value, path)
    _keys(
        data,
        {
            "id",
            "name",
            "grant",
            "win_condition",
            "passives",
            "knowledge",
            "chat_channels",
            "overrides",
            "exclusions",
        },
        path,
    )
    _require_id(f"{path}.id", data["id"])
    _require_text(f"{path}.name", data["name"])
    grant = _mapping(data["grant"], f"{path}.grant")
    _keys(grant, {"timing", "duration", "duration_nights"}, f"{path}.grant")
    _require_id(f"{path}.grant.timing", grant["timing"])
    _require_id(f"{path}.grant.duration", grant["duration"])
    _optional_int(f"{path}.grant.duration_nights", grant["duration_nights"])
    win = _mapping(data["win_condition"], f"{path}.win_condition")
    _keys(win, {"mode", "value", "priority"}, f"{path}.win_condition")
    mode = _require_id(f"{path}.win_condition.mode", win["mode"])
    if mode == "none":
        if win["value"] is not None or win["priority"] is not None:
            raise DiscussionContextError(
                f"{path}.win_condition value/priority are forbidden for mode none"
            )
    elif mode == "add":
        if win["value"] is None or win["priority"] is not None:
            raise DiscussionContextError(
                f"{path}.win_condition add requires value and forbids priority"
            )
        _validate_win_condition(win["value"], f"{path}.win_condition.value")
    elif mode == "override":
        if win["value"] is None:
            raise DiscussionContextError(
                f"{path}.win_condition override requires value"
            )
        _validate_win_condition(win["value"], f"{path}.win_condition.value")
        if type(win["priority"]) is not int:
            raise DiscussionContextError(
                f"{path}.win_condition.priority must be an exact integer"
            )
    else:
        raise DiscussionContextError(f"{path}.win_condition.mode is invalid")
    passives = _sequence(data["passives"], f"{path}.passives")
    if len(passives) > MAX_PASSIVES:
        raise DiscussionContextError(f"{path}.passives contains too many values")
    for index, passive in enumerate(passives):
        _validate_passive(passive, f"{path}.passives[{index}]")
    _validate_knowledge(data["knowledge"], f"{path}.knowledge")
    _id_array(data["chat_channels"], f"{path}.chat_channels")
    overrides = _mapping(data["overrides"], f"{path}.overrides")
    override_names = {"team", "count_as", "attack_result", "inspect_result", "medium_result"}
    _keys(overrides, override_names, f"{path}.overrides")
    for name in override_names:
        if overrides[name] is not None:
            _require_id(f"{path}.overrides.{name}", overrides[name])
    _id_array(data["exclusions"], f"{path}.exclusions", sorted_unique=True)


_CONTENT_ROOTS = {
    "teams",
    "roles",
    "effects",
    "passives",
    "selectors",
    "restriction_types",
    "action_timings",
    "chat_channels",
    "death_causes",
    "modifiers",
}


def _validate_named_root_entry(value: object, path: str) -> None:
    data = _mapping(value, path)
    _keys(data, {"id", "name"}, path)
    _require_id(f"{path}.id", data["id"])
    _require_text(f"{path}.name", data["name"])


def _validate_content_pack(value: object, path: str) -> Mapping[str, Any]:
    pack = _mapping(value, path)
    _keys(pack, _CONTENT_ROOTS, path)
    roots: dict[str, Mapping[str, Any]] = {}
    for root_name in sorted(_CONTENT_ROOTS):
        root = _mapping(pack[root_name], f"{path}.{root_name}")
        if len(root) > MAX_ROOT_ENTRIES:
            raise DiscussionContextError(f"{path}.{root_name} exceeds 64 entries")
        for key in root:
            _require_id(f"{path}.{root_name} key", key)
        roots[root_name] = root

    for key, value in roots["teams"].items():
        item_path = f"{path}.teams.{key}"
        data = _mapping(value, item_path)
        _keys(
            data,
            {
                "id",
                "name",
                "default_count_as",
                "default_inspect_result",
                "default_medium_result",
                "win_conditions",
            },
            item_path,
        )
        if data["id"] != key:
            raise DiscussionContextError(f"{item_path}.id does not match map key")
        _require_text(f"{item_path}.name", data["name"])
        for name in ("default_count_as", "default_inspect_result", "default_medium_result"):
            _require_id(f"{item_path}.{name}", data[name])
        wins = _sequence(data["win_conditions"], f"{item_path}.win_conditions")
        if not wins or len(wins) > MAX_WIN_CONDITIONS:
            raise DiscussionContextError(f"{item_path}.win_conditions has invalid count")
        for index, win in enumerate(wins):
            _validate_win_condition(win, f"{item_path}.win_conditions[{index}]")

    for key, value in roots["roles"].items():
        item_path = f"{path}.roles.{key}"
        _validate_role(value, item_path)
        if _mapping(value, item_path)["id"] != key:
            raise DiscussionContextError(f"{item_path}.id does not match map key")

    for root_name in ("effects", "passives", "selectors", "restriction_types", "death_causes"):
        for key, value in roots[root_name].items():
            item_path = f"{path}.{root_name}.{key}"
            _validate_named_root_entry(value, item_path)
            if _mapping(value, item_path)["id"] != key:
                raise DiscussionContextError(f"{item_path}.id does not match map key")

    for key, value in roots["action_timings"].items():
        item_path = f"{path}.action_timings.{key}"
        data = _mapping(value, item_path)
        _keys(data, {"id", "name", "phases"}, item_path)
        if data["id"] != key:
            raise DiscussionContextError(f"{item_path}.id does not match map key")
        _require_text(f"{item_path}.name", data["name"])
        _id_array(data["phases"], f"{item_path}.phases")

    for key, value in roots["chat_channels"].items():
        item_path = f"{path}.chat_channels.{key}"
        data = _mapping(value, item_path)
        _keys(data, {"id", "name", "phases", "allows_co", "is_public"}, item_path)
        if data["id"] != key:
            raise DiscussionContextError(f"{item_path}.id does not match map key")
        _require_text(f"{item_path}.name", data["name"])
        _id_array(data["phases"], f"{item_path}.phases")
        _require_exact_bool(f"{item_path}.allows_co", data["allows_co"])
        _require_exact_bool(f"{item_path}.is_public", data["is_public"])

    for key, value in roots["modifiers"].items():
        item_path = f"{path}.modifiers.{key}"
        _validate_modifier(value, item_path)
        if _mapping(value, item_path)["id"] != key:
            raise DiscussionContextError(f"{item_path}.id does not match map key")

    _validate_content_references(roots, path)
    return pack


def _validate_content_references(roots: Mapping[str, Mapping[str, Any]], path: str) -> None:
    for role_id, raw_role in roots["roles"].items():
        role = _mapping(raw_role, f"{path}.roles.{role_id}")
        attributes = _mapping(role["attributes"], f"{path}.roles.{role_id}.attributes")
        if attributes["team"] not in roots["teams"]:
            raise DiscussionContextError(f"role {role_id!r} references an unknown team")
        for channel_id in role["chat_channels"]:
            if channel_id not in roots["chat_channels"]:
                raise DiscussionContextError(f"role {role_id!r} references an unknown channel")
        for ability in role["abilities"]:
            ability_data = _mapping(ability, "ability")
            if ability_data["timing"] not in roots["action_timings"]:
                raise DiscussionContextError(f"role {role_id!r} references an unknown timing")
            target = _mapping(ability_data["target"], "ability.target")
            if target["selector"] not in roots["selectors"]:
                raise DiscussionContextError(f"role {role_id!r} references an unknown selector")
            for restriction in ability_data["restrictions"]:
                restriction_data = _mapping(restriction, "ability.restriction")
                if restriction_data["type"] not in roots["restriction_types"]:
                    raise DiscussionContextError(f"role {role_id!r} references an unknown restriction")
            for effect in ability_data["effects"]:
                if _mapping(effect, "ability.effect")["id"] not in roots["effects"]:
                    raise DiscussionContextError(f"role {role_id!r} references an unknown effect")
        for passive in role["passives"]:
            passive_data = _mapping(passive, "passive")
            if passive_data["type"] not in roots["passives"]:
                raise DiscussionContextError(f"role {role_id!r} references an unknown passive")
            for effect in passive_data["effects"]:
                if _mapping(effect, "passive.effect")["id"] not in roots["effects"]:
                    raise DiscussionContextError(f"role {role_id!r} references an unknown effect")

    for modifier_id, raw_modifier in roots["modifiers"].items():
        modifier = _mapping(raw_modifier, f"{path}.modifiers.{modifier_id}")
        for role_id in modifier["exclusions"]:
            if role_id not in roots["roles"]:
                raise DiscussionContextError(f"modifier {modifier_id!r} excludes an unknown role")
        for channel_id in modifier["chat_channels"]:
            if channel_id not in roots["chat_channels"]:
                raise DiscussionContextError(f"modifier {modifier_id!r} references an unknown channel")
        for passive in modifier["passives"]:
            passive_data = _mapping(passive, "modifier.passive")
            if passive_data["type"] not in roots["passives"]:
                raise DiscussionContextError(f"modifier {modifier_id!r} references an unknown passive")
            for effect in passive_data["effects"]:
                if _mapping(effect, "modifier.passive.effect")["id"] not in roots["effects"]:
                    raise DiscussionContextError(
                        f"modifier {modifier_id!r} references an unknown effect"
                    )


def _validate_rules(value: object, path: str) -> None:
    rules = _mapping(value, path)
    required = {
        "first_night_seer",
        "vote",
        "guard",
        "night_action",
        "medium",
        "wolf_attack",
        "co",
        "sudden_death",
        "death",
        "graveyard",
        "role_missing",
        "day_seconds",
        "vote_seconds",
        "night_seconds",
        "silence_after_dawn_seconds",
        "extension",
        "shortening",
        "win_evaluation_order",
    }
    _keys(rules, required, path)
    _require_id(f"{path}.first_night_seer", rules["first_night_seer"])
    for name in ("day_seconds", "vote_seconds", "night_seconds", "silence_after_dawn_seconds"):
        _require_int(f"{path}.{name}", rules[name])

    nested: tuple[tuple[str, set[str]], ...] = (
        ("vote", {"runoff", "tie_after_runoff", "tie_without_runoff", "abstain", "self_vote", "reveal"}),
        ("guard", {"consecutive", "self_guard"}),
        ("night_action", {"no_selection"}),
        ("medium", {"notify_timing"}),
        ("wolf_attack", {"target_decision", "tie"}),
        ("co", {"max_per_day", "allow_villager_claim"}),
        ("sudden_death", {"enabled"}),
        ("death", {"public_detail"}),
        ("graveyard", {"view_public", "speak", "reveal_roles"}),
        ("role_missing", {"enabled", "replacement_role_id"}),
        ("extension", {"max_count", "seconds_per_extension", "approval"}),
        ("shortening", {"enabled", "approval"}),
    )
    objects: dict[str, Mapping[str, Any]] = {}
    for name, keys in nested:
        item = _mapping(rules[name], f"{path}.{name}")
        _keys(item, keys, f"{path}.{name}")
        objects[name] = item
    abstain = _mapping(objects["vote"]["abstain"], f"{path}.vote.abstain")
    _keys(abstain, {"enabled", "max_per_player"}, f"{path}.vote.abstain")

    bool_fields = (
        (objects["vote"], "runoff"),
        (objects["vote"], "self_vote"),
        (abstain, "enabled"),
        (objects["guard"], "consecutive"),
        (objects["guard"], "self_guard"),
        (objects["co"], "allow_villager_claim"),
        (objects["sudden_death"], "enabled"),
        (objects["graveyard"], "view_public"),
        (objects["graveyard"], "speak"),
        (objects["graveyard"], "reveal_roles"),
        (objects["role_missing"], "enabled"),
        (objects["shortening"], "enabled"),
    )
    for owner, name in bool_fields:
        _require_exact_bool(f"{path}.{name}", owner[name])
    for owner, name in (
        (abstain, "max_per_player"),
        (objects["co"], "max_per_day"),
    ):
        _optional_int(f"{path}.{name}", owner[name])
    _optional_text(f"{path}.night_action.no_selection", objects["night_action"]["no_selection"])
    for owner, name in (
        (objects["vote"], "tie_after_runoff"),
        (objects["vote"], "tie_without_runoff"),
        (objects["vote"], "reveal"),
        (objects["medium"], "notify_timing"),
        (objects["wolf_attack"], "target_decision"),
        (objects["wolf_attack"], "tie"),
        (objects["death"], "public_detail"),
        (objects["role_missing"], "replacement_role_id"),
        (objects["extension"], "approval"),
        (objects["shortening"], "approval"),
    ):
        _require_id(f"{path}.{name}", owner[name])
    for name in ("max_count", "seconds_per_extension"):
        _require_int(f"{path}.extension.{name}", objects["extension"][name])
    _id_array(rules["win_evaluation_order"], f"{path}.win_evaluation_order")


def _validate_preset(value: object, path: str, roles: Mapping[str, Any]) -> None:
    preset = _mapping(value, path)
    _keys(preset, {"name", "rules", "role_counts"}, path)
    _require_text(f"{path}.name", preset["name"])
    _validate_rules(preset["rules"], f"{path}.rules")
    counts = _mapping(preset["role_counts"], f"{path}.role_counts")
    if len(counts) > MAX_ROOT_ENTRIES:
        raise DiscussionContextError(f"{path}.role_counts exceeds 64 entries")
    for role_id, count in counts.items():
        _require_id(f"{path}.role_counts key", role_id)
        _require_int(f"{path}.role_counts.{role_id}", count)
        if role_id not in roles:
            raise DiscussionContextError(f"preset references unknown role {role_id!r}")


def _validate_manifest(value: object) -> Mapping[str, Any]:
    manifest = _mapping(value, "manifest_material")
    _keys(manifest, {"schema_version", "content_pack", "effective_preset"}, "manifest_material")
    if manifest["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise DiscussionContextError("invalid content manifest schema_version")
    pack = _validate_content_pack(manifest["content_pack"], "manifest_material.content_pack")
    roles = _mapping(pack["roles"], "manifest_material.content_pack.roles")
    _validate_preset(manifest["effective_preset"], "manifest_material.effective_preset", roles)
    if len(canonical_json_bytes(manifest)) > MAX_MANIFEST_BYTES:
        raise DiscussionContextError("manifest material exceeds 64 KiB")
    return manifest


def _parse_context_win(value: object, path: str) -> DiscussionWinCondition:
    data = _mapping(value, path)
    condition_type = data.get("type")
    if condition_type == "eliminate_role_tag":
        _keys(data, {"type", "tag"}, path)
        return EliminateRoleTagWinCondition(type=condition_type, tag=data["tag"])
    if condition_type == "count_parity":
        _keys(data, {"type", "subject", "against", "operator"}, path)
        return CountParityWinCondition(
            type=condition_type,
            subject=data["subject"],
            against=data["against"],
            operator=data["operator"],
        )
    if condition_type == "survive_when_others_win":
        _keys(data, {"type", "replaces"}, path)
        return SurviveWhenOthersWinCondition(
            type=condition_type,
            replaces=data["replaces"],
        )
    raise DiscussionContextError(f"{path}.type is unsupported")


def _parse_context_ability(value: object, path: str) -> DiscussionAbilityContext:
    data = _mapping(value, path)
    names = {item.name for item in fields(DiscussionAbilityContext)}
    _keys(data, names, path)
    return DiscussionAbilityContext(**{name: data[name] for name in names})


def _parse_context_passive(value: object, path: str) -> DiscussionPassiveContext:
    data = _mapping(value, path)
    names = {item.name for item in fields(DiscussionPassiveContext)}
    _keys(data, names, path)
    return DiscussionPassiveContext(**{name: data[name] for name in names})


def _parse_context_channel(value: object, path: str) -> AuthorizedChatChannelContext:
    data = _mapping(value, path)
    _keys(data, {"channel_id", "is_public"}, path)
    return AuthorizedChatChannelContext(
        channel_id=data["channel_id"],
        is_public=data["is_public"],
    )


def parse_authorized_discussion_context(value: object) -> AuthorizedDiscussionContext:
    """Parse a closed JSON-shaped player context into immutable values."""

    data = _mapping(value, "context_payload")
    names = {item.name for item in fields(AuthorizedDiscussionContext)}
    _keys(data, names, "context_payload")
    wins = tuple(
        _parse_context_win(item, f"context_payload.win_conditions[{index}]")
        for index, item in enumerate(_sequence(data["win_conditions"], "context_payload.win_conditions"))
    )
    abilities = tuple(
        _parse_context_ability(item, f"context_payload.abilities[{index}]")
        for index, item in enumerate(_sequence(data["abilities"], "context_payload.abilities"))
    )
    passives = tuple(
        _parse_context_passive(item, f"context_payload.passives[{index}]")
        for index, item in enumerate(_sequence(data["passives"], "context_payload.passives"))
    )
    channels = tuple(
        _parse_context_channel(item, f"context_payload.chat_channels[{index}]")
        for index, item in enumerate(_sequence(data["chat_channels"], "context_payload.chat_channels"))
    )
    return AuthorizedDiscussionContext(
        schema_version=data["schema_version"],
        game_id=data["game_id"],
        player_id=data["player_id"],
        role_id=data["role_id"],
        modifier_ids=tuple(_sequence(data["modifier_ids"], "context_payload.modifier_ids")),
        content_manifest_sha256=data["content_manifest_sha256"],
        team=data["team"],
        count_as=data["count_as"],
        attack_result=data["attack_result"],
        inspect_result=data["inspect_result"],
        medium_result=data["medium_result"],
        win_conditions=wins,
        abilities=abilities,
        passives=passives,
        chat_channels=channels,
        knows_teammates=data["knows_teammates"],
        authorized_known_player_ids=tuple(
            _sequence(
                data["authorized_known_player_ids"],
                "context_payload.authorized_known_player_ids",
            )
        ),
        known_players_complete=data["known_players_complete"],
    )


def _context_win_from_manifest(value: object) -> DiscussionWinCondition:
    data = _mapping(value, "manifest win condition")
    body = _mapping(data["data"], "manifest win condition data")
    return _parse_context_win(body, "manifest win condition data")


def _context_ability_from_manifest(value: object) -> DiscussionAbilityContext:
    data = _mapping(value, "manifest ability")
    target = _mapping(data["target"], "manifest ability target")
    uses = _mapping(data["uses"], "manifest ability uses")
    effect_ids = tuple(
        sorted(_mapping(item, "manifest ability effect")["id"] for item in data["effects"])
    )
    return DiscussionAbilityContext(
        ability_id=data["id"],
        timing=data["timing"],
        available_from_night=data["available_from_night"],
        priority=data["priority"],
        resolution=data["resolution"],
        target_selector=target["selector"],
        target_count=target["count"],
        uses_per_night=uses["per_night"],
        uses_per_game=uses["per_game"],
        no_selection=data["no_selection"],
        effect_ids=effect_ids,
    )


def _context_passive_from_manifest(value: object) -> DiscussionPassiveContext:
    data = _mapping(value, "manifest passive")
    effect_ids = tuple(
        sorted(_mapping(item, "manifest passive effect")["id"] for item in data["effects"])
    )
    return DiscussionPassiveContext(
        type=data["type"],
        priority=data["priority"],
        effect_ids=effect_ids,
    )


def _effective_win_conditions(
    roots: Mapping[str, Any],
    role: Mapping[str, Any],
    modifiers: tuple[Mapping[str, Any], ...],
    effective_team: str,
) -> tuple[DiscussionWinCondition, ...]:
    team = _mapping(_mapping(roots["teams"], "teams")[effective_team], "team")
    base = tuple(_context_win_from_manifest(item) for item in team["win_conditions"])
    overrides: list[tuple[int, DiscussionWinCondition]] = []
    additions: list[DiscussionWinCondition] = []
    for modifier in modifiers:
        win = _mapping(modifier["win_condition"], "modifier.win_condition")
        if win["mode"] == "override" and win["value"] is not None:
            priority = win["priority"]
            if type(priority) is not int:
                raise DiscussionContextError("override win condition requires integer priority")
            overrides.append((priority, _context_win_from_manifest(win["value"])))
        elif win["mode"] == "add" and win["value"] is not None:
            additions.append(_context_win_from_manifest(win["value"]))
        elif win["mode"] not in {"none", "add", "override"}:
            raise DiscussionContextError("unknown modifier win-condition mode")
    if overrides:
        priorities = [priority for priority, _ in overrides]
        if len(priorities) != len(set(priorities)):
            raise DiscussionContextError("override win-condition priorities collide")
        values = (max(overrides, key=lambda pair: pair[0])[1],)
    else:
        values = base + tuple(additions)
    return tuple(sorted(values, key=canonical_json_bytes))


def _validate_context_against_manifest(
    context: AuthorizedDiscussionContext,
    manifest: Mapping[str, Any],
) -> None:
    pack = _mapping(manifest["content_pack"], "manifest.content_pack")
    roles = _mapping(pack["roles"], "manifest.content_pack.roles")
    modifiers_root = _mapping(pack["modifiers"], "manifest.content_pack.modifiers")
    channels_root = _mapping(pack["chat_channels"], "manifest.content_pack.chat_channels")
    if context.role_id not in roles:
        raise DiscussionContextError("context references an unknown role")
    try:
        modifiers = tuple(
            _mapping(modifiers_root[modifier_id], f"modifier {modifier_id}")
            for modifier_id in context.modifier_ids
        )
    except KeyError as error:
        raise DiscussionContextError("context references an unknown modifier") from error
    role = _mapping(roles[context.role_id], f"role {context.role_id}")
    if any(context.role_id in modifier["exclusions"] for modifier in modifiers):
        raise DiscussionContextError("context uses a modifier excluded for its role")

    attributes = dict(_mapping(role["attributes"], "role.attributes"))
    overridden_attributes: set[str] = set()
    for modifier in modifiers:
        for name, value in _mapping(modifier["overrides"], "modifier.overrides").items():
            if value is not None and value != "by_role":
                if name in overridden_attributes:
                    raise DiscussionContextError(
                        "multiple selected modifiers override the same effective "
                        f"attribute: {name}"
                    )
                overridden_attributes.add(name)
                attributes[name] = value
    expected_attributes = {
        "team": context.team,
        "count_as": context.count_as,
        "attack_result": context.attack_result,
        "inspect_result": context.inspect_result,
        "medium_result": context.medium_result,
    }
    if attributes != expected_attributes:
        raise DiscussionContextError("context effective attributes do not match manifest")
    teams = _mapping(pack["teams"], "manifest.content_pack.teams")
    if context.team not in teams:
        raise DiscussionContextError("context effective team is unknown")
    expected_wins = _effective_win_conditions(pack, role, modifiers, context.team)
    if context.win_conditions != expected_wins:
        raise DiscussionContextError("context win conditions do not match manifest")

    expected_abilities = tuple(
        sorted(
            (_context_ability_from_manifest(item) for item in role["abilities"]),
            key=lambda item: item.ability_id,
        )
    )
    if context.abilities != expected_abilities:
        raise DiscussionContextError("context abilities do not match manifest")
    passive_values = list(role["passives"])
    for modifier in modifiers:
        passive_values.extend(modifier["passives"])
    expected_passives = tuple(
        sorted(
            (_context_passive_from_manifest(item) for item in passive_values),
            key=canonical_json_bytes,
        )
    )
    if context.passives != expected_passives:
        raise DiscussionContextError("context passives do not match manifest")

    authorized_ids = set(role["chat_channels"])
    for modifier in modifiers:
        authorized_ids.update(modifier["chat_channels"])
    descriptor_ids = {descriptor.channel_id for descriptor in context.chat_channels}
    if descriptor_ids != authorized_ids:
        raise DiscussionContextError("context authorized channel set does not match manifest")
    for descriptor in context.chat_channels:
        try:
            channel = _mapping(channels_root[descriptor.channel_id], "manifest channel")
        except KeyError as error:
            raise DiscussionContextError("context references an unknown chat channel") from error
        if descriptor.is_public is not channel["is_public"]:
            raise DiscussionContextError("context chat visibility bit does not match manifest")

    declarations = _mapping(role["knowledge"], "role.knowledge")["declarations"]
    expected_knows = _mapping(declarations, "role.knowledge.declarations")["knows_teammates"]
    for modifier in modifiers:
        modifier_declarations = _mapping(
            _mapping(modifier["knowledge"], "modifier.knowledge")["declarations"],
            "modifier.knowledge.declarations",
        )
        expected_knows = expected_knows or modifier_declarations["knows_teammates"]
    if context.knows_teammates is not expected_knows:
        raise DiscussionContextError("context knowledge declaration does not match manifest")


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_deep_freeze(item) for item in value)
    return value


_BOOTSTRAP_RECEIPT_TOKEN = object()
_EMPTY_AUTHORITY_PROOF_REFS = MappingProxyType({})


class _BootstrapValidationReceiptV2:
    __slots__ = (
        "_token", "_pending", "_manifest_material", "_manifest_bytes",
        "_manifest_sha256", "_context", "_context_sha256",
    )

    def __init__(self, token: object, pending: object) -> None:
        if token is not _BOOTSTRAP_RECEIPT_TOKEN:
            raise TypeError("bootstrap validation receipt is opaque")
        object.__setattr__(self, "_token", token)
        object.__setattr__(self, "_pending", pending)
        object.__setattr__(self, "_manifest_material", pending._manifest_material)
        object.__setattr__(self, "_manifest_bytes", pending._manifest_bytes)
        object.__setattr__(self, "_manifest_sha256", pending.manifest_sha256)
        object.__setattr__(self, "_context", pending.context)
        object.__setattr__(self, "_context_sha256", pending.context_sha256)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("bootstrap validation receipt is immutable")


class AuthorityPendingProofV2:
    __slots__ = ("_state", "_secret_refs", "_proof_identity", "_issuer_capability")
    def __init__(self, token: object, refs: Mapping[str, object]) -> None:
        if token is not _BOOTSTRAP_RECEIPT_TOKEN:
            raise TypeError("authority pending proof is opaque")
        self._state = "ACTIVE"
        self._secret_refs = MappingProxyType(dict(refs))
        self._proof_identity = object()
        self._issuer_capability = object()
    def __repr__(self) -> str: return "AuthorityPendingProofV2(<opaque>)"


def _retire_pending_proof_v2(proof: AuthorityPendingProofV2) -> None:
    proof._state = "RETIRED"
    proof._secret_refs = _EMPTY_AUTHORITY_PROOF_REFS


def _revalidate_authority_pending_v2(
    pending: "PendingDiscussionContext",
    snapshot: WorldSnapshot,
    registration: object,
) -> AuthorityPendingProofV2:
    from .authority_capture_bridge_v2 import (
        AuthorityOwnerRegistrationV2, _validate_owner_registration_v2,
    )
    if type(pending) is not PendingDiscussionContext:
        raise DiscussionContextError("pending discussion context is required")
    receipt = pending._bootstrap_validation_receipt
    if (type(receipt) is not _BootstrapValidationReceiptV2
            or receipt._pending is not pending
            or receipt._manifest_material is not pending._manifest_material
            or receipt._manifest_bytes is not pending._manifest_bytes
            or receipt._manifest_sha256 != pending.manifest_sha256
            or receipt._context is not pending.context
            or receipt._context_sha256 != pending.context_sha256):
        raise DiscussionContextError("bootstrap validation route is not proven")
    material = pending._manifest_material
    retained = pending._manifest_bytes
    if material is None or retained is None:
        raise DiscussionContextError("manifest material has already been disposed")
    canonical = canonical_json_bytes(material)
    if canonical != retained or sha256(canonical).hexdigest() != pending.manifest_sha256:
        raise DiscussionContextError("retained manifest material mismatch")
    if pending._active_authority_proof_v2 is not None:
        raise DiscussionContextError("authority pending proof already exists")
    if type(registration) is not AuthorityOwnerRegistrationV2 or registration.lifecycle != "ACTIVE":
        raise DiscussionContextError("authority owner registration is invalid")
    _validate_owner_registration_v2(registration)
    if registration.exact_runtime_source._pending is not pending:
        raise DiscussionContextError("pending context is not owned by the runtime source")
    world = registration.exact_world
    network = registration.exact_network_client.snapshot()
    authority_runtime = world._inbound_authority
    authority = world.inbound_authority_snapshot()
    if (world.snapshot() is not snapshot or authority is not world.inbound_authority_snapshot()
            or authority_runtime._authority_owner_registration_v2 is not registration
            or authority_runtime._composition_capability is not registration.exact_claim_capability
            or network.player_id is None or authority.readiness_status not in {"PENDING_SYNC", "READY"}
            or authority.game_id != registration.exact_network_client.config.game_id
            or authority.player_id != network.player_id
            or authority.connection_generation != network.connection_generation):
        raise DiscussionContextError("authority owner snapshot mismatch")
    if (pending.context.game_id != registration.exact_network_client.config.game_id
            or pending.context.player_id != network.player_id):
        raise DiscussionContextError("runtime game/player identity does not match context")
    if pending.context.content_manifest_sha256 != pending.manifest_sha256:
        raise DiscussionContextError("context manifest hash mismatch")
    if canonical_sha256(pending.context) != pending.context_sha256:
        raise DiscussionContextError("context_sha256 mismatch")
    _validate_context_against_manifest(pending.context, material)
    validate_context_snapshot(pending.context, snapshot)
    proof = AuthorityPendingProofV2(_BOOTSTRAP_RECEIPT_TOKEN, {
        "pending": pending, "bootstrap_receipt": receipt,
        "manifest_material": material, "manifest_bytes": retained,
        "manifest_sha256": pending.manifest_sha256, "context": pending.context,
        "context_sha256": pending.context_sha256, "world_snapshot": snapshot,
        "authority_snapshot": authority, "registration": registration,
        "authenticated_player_id": network.player_id,
        "connection_generation": network.connection_generation,
        "readiness_status": authority.readiness_status,
    })
    pending._active_authority_proof_v2 = proof
    return proof


class PendingDiscussionContext:
    """Validated, single-use bootstrap material awaiting the first CURRENT sync."""

    __slots__ = (
        "_manifest_material",
        "_manifest_bytes",
        "manifest_sha256",
        "context_sha256",
        "context",
        "_bootstrap_validation_receipt",
        "_active_authority_proof_v2",
    )

    def __init__(
        self,
        *,
        manifest_material: Mapping[str, Any],
        manifest_bytes: bytes,
        manifest_sha256: str,
        context_sha256: str,
        context: AuthorizedDiscussionContext,
        _bootstrap_validation_receipt: object | None = None,
    ) -> None:
        self._manifest_material: Mapping[str, Any] | None = manifest_material
        self._manifest_bytes: bytes | None = manifest_bytes
        self.manifest_sha256 = manifest_sha256
        self.context_sha256 = context_sha256
        self.context = context
        self._bootstrap_validation_receipt = _bootstrap_validation_receipt
        self._active_authority_proof_v2 = None

    def __repr__(self) -> str:
        state = "pending" if self._manifest_material is not None else "disposed"
        return (
            "PendingDiscussionContext("
            f"player_id={self.context.player_id!r}, state={state!r}, manifest=<redacted>)"
        )

    @property
    def manifest_is_retained(self) -> bool:
        return self._manifest_material is not None

    def canonical_manifest_bytes(self) -> bytes:
        if self._manifest_bytes is None:
            raise DiscussionContextError("manifest material has already been disposed")
        return self._manifest_bytes

    def discard_manifest(self) -> None:
        proof = self._active_authority_proof_v2
        if isinstance(proof, AuthorityPendingProofV2):
            _retire_pending_proof_v2(proof)
            self._active_authority_proof_v2 = None
        receipt = self._bootstrap_validation_receipt
        if isinstance(receipt, _BootstrapValidationReceiptV2):
            object.__setattr__(receipt, "_manifest_material", None)
            object.__setattr__(receipt, "_manifest_bytes", None)
        self._manifest_material = None
        self._manifest_bytes = None

    def bind(self, snapshot: WorldSnapshot) -> BoundDiscussionContext:
        if self._manifest_material is None:
            raise DiscussionContextError("pending context has already been consumed")
        validate_context_snapshot(self.context, snapshot)
        bound = BoundDiscussionContext(
            manifest_sha256=self.manifest_sha256,
            context_sha256=self.context_sha256,
            context=self.context,
        )
        self.discard_manifest()
        return bound


def validate_discussion_bootstrap(
    value: object,
    *,
    network_game_id: str,
    player_id: str,
) -> PendingDiscussionContext:
    """Validate a complete envelope before network/backend/audit construction."""

    _require_id("network_game_id", network_game_id)
    _require_id("player_id", player_id)
    envelope = _mapping(value, "discussion bootstrap")
    _keys(
        envelope,
        {
            "schema_version",
            "manifest_material",
            "manifest_sha256",
            "context_payload",
            "context_sha256",
        },
        "discussion bootstrap",
    )
    if envelope["schema_version"] != BOOTSTRAP_SCHEMA_VERSION:
        raise DiscussionContextError("invalid discussion bootstrap schema_version")
    envelope_bytes = canonical_json_bytes(envelope)
    if len(envelope_bytes) > MAX_BOOTSTRAP_BYTES:
        raise DiscussionContextError("discussion bootstrap exceeds 80 KiB")
    manifest = _validate_manifest(envelope["manifest_material"])
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_hash = _require_hash("manifest_sha256", envelope["manifest_sha256"])
    if sha256(manifest_bytes).hexdigest() != manifest_hash:
        raise DiscussionContextError("manifest_sha256 mismatch")
    context = parse_authorized_discussion_context(envelope["context_payload"])
    context_hash = _require_hash("context_sha256", envelope["context_sha256"])
    if canonical_sha256(context) != context_hash:
        raise DiscussionContextError("context_sha256 mismatch")
    if context.content_manifest_sha256 != manifest_hash:
        raise DiscussionContextError("context manifest hash mismatch")
    if context.game_id != network_game_id or context.player_id != player_id:
        raise DiscussionContextError("runtime game/player identity does not match context")
    _validate_context_against_manifest(context, manifest)
    frozen_manifest = _deep_freeze(_canonical_value(manifest))
    assert isinstance(frozen_manifest, Mapping)
    pending = PendingDiscussionContext(
        manifest_material=frozen_manifest,
        manifest_bytes=manifest_bytes,
        manifest_sha256=manifest_hash,
        context_sha256=context_hash,
        context=context,
    )
    pending._bootstrap_validation_receipt = _BootstrapValidationReceiptV2(
        _BOOTSTRAP_RECEIPT_TOKEN, pending)
    return pending


def validate_context_snapshot(
    context: AuthorizedDiscussionContext,
    snapshot: WorldSnapshot,
) -> None:
    if not isinstance(snapshot, WorldSnapshot) or snapshot.freshness is not Freshness.CURRENT:
        raise DiscussionContextError("discussion context requires an authoritative CURRENT snapshot")
    self_view = snapshot.self_view
    if self_view is None:
        raise DiscussionContextError("CURRENT snapshot is missing SelfView")
    modifiers = tuple(self_view.modifier_ids)
    if len(modifiers) != len(set(modifiers)):
        raise DiscussionContextError("SelfView modifier IDs are not unique")
    if (
        self_view.player_id != context.player_id
        or self_view.role_id != context.role_id
        or tuple(sorted(modifiers)) != context.modifier_ids
    ):
        raise DiscussionContextError("SelfView identity/role/modifiers do not match context")


def validate_bound_context_snapshot(
    bound: BoundDiscussionContext,
    snapshot: WorldSnapshot,
) -> None:
    if canonical_sha256(bound.context) != bound.context_sha256:
        raise DiscussionContextError("bound context was mutated")
    validate_context_snapshot(bound.context, snapshot)


def channel_is_public(context: AuthorizedDiscussionContext, channel_id: str) -> bool:
    """Look up only the sealed owner descriptor; never infer an opaque ID."""

    _require_id("channel_id", channel_id)
    for descriptor in context.chat_channels:
        if descriptor.channel_id == channel_id:
            return descriptor.is_public
    raise DiscussionContextError("chat channel is absent from the sealed context")
