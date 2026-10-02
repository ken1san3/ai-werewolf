"""Strict YAML loading and validation for game content.

Only names and declarations are loaded here.  Effects and passives are not
executed in Phase 1.1, but every reference must already be registered so that
content never silently relies on an unavailable future implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from functools import cache
from pathlib import Path
from typing import Any, Iterable, Mapping, get_type_hints
import re

import yaml

from .capabilities import unsupported_runtime_references
from .models import (
    ATTRIBUTE_NAMES,
    ActionTiming,
    AbstainRules,
    Ability,
    AppliedModifier,
    AttributeOverrides,
    ChatChannel,
    CoRules,
    CORE_DEATH_CAUSE_IDS,
    DeathCause,
    DeathRules,
    Effect,
    EffectReference,
    ExtensionRules,
    GamePhase,
    GraveyardRules,
    GuardRules,
    Knowledge,
    MediumRules,
    NightActionRules,
    Modifier,
    ModifierGrant,
    ModifierWinCondition,
    Passive,
    PassiveDefinition,
    Restriction,
    RestrictionType,
    Role,
    RoleAttributes,
    RoleMissingRules,
    RoleOption,
    RulesConfig,
    ShorteningRules,
    SuddenDeathRules,
    TargetSpec,
    TargetSelector,
    Team,
    Uses,
    VoteRules,
    WinCondition,
    WolfAttackRules,
    validate_modifier_combination,
)


class ContentValidationError(ValueError):
    """Raised when a content pack or preset violates its declarative schema."""


@dataclass(frozen=True)
class ContentPack:
    teams: Mapping[str, Team]
    roles: Mapping[str, Role]
    effects: Mapping[str, Effect]
    passives: Mapping[str, PassiveDefinition]
    selectors: Mapping[str, TargetSelector]
    restriction_types: Mapping[str, RestrictionType]
    action_timings: Mapping[str, ActionTiming]
    chat_channels: Mapping[str, ChatChannel]
    death_causes: Mapping[str, DeathCause]
    modifiers: Mapping[str, Modifier]

    def validate_modifier_assignment(self, role_id: str, modifier_ids: Iterable[str]) -> None:
        """Validate an initial or in-game modifier assignment before storing it."""

        try:
            role = self.roles[role_id]
        except KeyError as error:
            raise ContentValidationError(f"unknown role '{role_id}'") from error
        definitions: list[Modifier] = []
        for modifier_id in modifier_ids:
            try:
                definitions.append(self.modifiers[modifier_id])
            except KeyError as error:
                raise ContentValidationError(f"unknown modifier '{modifier_id}'") from error
        try:
            validate_modifier_combination(role, definitions)
        except ValueError as error:
            raise ContentValidationError(str(error)) from error


@dataclass(frozen=True)
class Preset:
    name: str
    rules: RulesConfig
    role_counts: Mapping[str, int]


# These are domain values, not rule defaults.  The allowed set is fixed by the
# DESIGN.md schema; concrete selections always come from YAML.
_COUNT_AS_VALUES = frozenset({"village", "wolf", "none", "by_role"})
_ATTACK_RESULT_VALUES = frozenset({"die", "immune", "by_role"})
_INSPECT_RESULT_VALUES = frozenset({"wolf", "not_wolf", "by_role"})
_WIN_CONDITION_TYPES = frozenset(
    {"eliminate_role_tag", "count_parity", "survive_when_others_win"}
)
_ENABLED_WHEN_PATTERN = re.compile(
    r"^rules\.(?P<path>[a-z_]+(?:\.[a-z_]+)*)\s*==\s*(?P<value>true|false)$"
)
_PHASE_IDS = frozenset(phase.value for phase in GamePhase)


@cache
def _boolean_rule_paths(rules_type: type[Any]) -> frozenset[str]:
    """Derive every nested bool path from the RulesConfig dataclass schema."""

    paths: set[str] = set()

    def visit(data_class: type[Any], prefix: str = "") -> None:
        for field in fields(data_class):
            path = f"{prefix}.{field.name}" if prefix else field.name
            field_type = get_type_hints(data_class)[field.name]
            if field_type is bool:
                paths.add(path)
            elif is_dataclass(field_type):
                visit(field_type, path)

    visit(rules_type)
    return frozenset(paths)


def load_content(content_root: str | Path) -> ContentPack:
    """Load a complete content pack from a directory of YAML declarations."""

    root = Path(content_root)
    if not root.is_dir():
        raise ContentValidationError(f"content root does not exist: {root}")

    teams = _index_models(
        (_parse_team(item, f"teams.yaml.teams[{index}]") for index, item in enumerate(_list_at(root / "teams.yaml", "teams"))),
        "team",
    )
    effects = _indexed(
        (_parse_named(item, "effect", f"effects.yaml.effects[{index}]") for index, item in enumerate(_list_at(root / "effects.yaml", "effects"))),
        "effect",
    )
    passive_definitions = _indexed(
        (
            _parse_named(item, "passive", f"passives.yaml.passives[{index}]")
            for index, item in enumerate(_list_at(root / "passives.yaml", "passives"))
        ),
        "passive",
    )
    selector_definitions = _indexed(
        (
            _parse_named(item, "selector", f"selectors.yaml.selectors[{index}]")
            for index, item in enumerate(_list_at(root / "selectors.yaml", "selectors"))
        ),
        "selector",
    )
    restriction_definitions = _indexed(
        (
            _parse_named(
                item,
                "restriction type",
                f"restriction_types.yaml.restriction_types[{index}]",
            )
            for index, item in enumerate(
                _list_at(root / "restriction_types.yaml", "restriction_types")
            )
        ),
        "restriction type",
    )
    action_timings = _index_models(
        (
            _parse_action_timing(item, f"timings.yaml.timings[{index}]")
            for index, item in enumerate(_list_at(root / "timings.yaml", "timings"))
        ),
        "action timing",
    )
    channels = _index_models(
        (
            _parse_chat_channel(item, f"chat_channels.yaml.chat_channels[{index}]")
            for index, item in enumerate(_list_at(root / "chat_channels.yaml", "chat_channels"))
        ),
        "chat channel",
    )
    death_causes = _indexed(
        (
            _parse_named(item, "death cause", f"death_causes.yaml.death_causes[{index}]")
            for index, item in enumerate(_list_at(root / "death_causes.yaml", "death_causes"))
        ),
        "death cause",
    )

    typed_effects = {identifier: Effect(**record) for identifier, record in effects.items()}
    typed_passives = {
        identifier: PassiveDefinition(**record) for identifier, record in passive_definitions.items()
    }
    typed_selectors = {
        identifier: TargetSelector(**record) for identifier, record in selector_definitions.items()
    }
    typed_restrictions = {
        identifier: RestrictionType(**record)
        for identifier, record in restriction_definitions.items()
    }
    typed_causes = {
        identifier: DeathCause(**record) for identifier, record in death_causes.items()
    }
    _validate_core_death_causes(typed_causes)

    roles: dict[str, Role] = {}
    role_dir = root / "roles"
    if not role_dir.is_dir():
        raise ContentValidationError(f"roles directory does not exist: {role_dir}")
    role_files = sorted(role_dir.glob("*.yaml"))
    if not role_files:
        raise ContentValidationError("content must define at least one role")
    for path in role_files:
        role = _parse_role(
            _load_yaml(path),
            path.relative_to(root).as_posix(),
            teams,
            typed_effects,
            typed_passives,
            typed_selectors,
            typed_restrictions,
            action_timings,
            channels,
            typed_causes,
        )
        if role.id in roles:
            raise ContentValidationError(f"duplicate role id '{role.id}'")
        roles[role.id] = role

    modifiers: dict[str, Modifier] = {}
    modifier_dir = root / "modifiers"
    if modifier_dir.exists() and not modifier_dir.is_dir():
        raise ContentValidationError(f"modifiers path is not a directory: {modifier_dir}")
    if modifier_dir.is_dir():
        for path in sorted(modifier_dir.glob("*.yaml")):
            modifier = _parse_modifier(
                _load_yaml(path),
                path.relative_to(root).as_posix(),
                teams,
                roles,
                typed_effects,
                typed_passives,
                typed_selectors,
                typed_restrictions,
                channels,
                typed_causes,
            )
            if modifier.id in modifiers:
                raise ContentValidationError(f"duplicate modifier id '{modifier.id}'")
            modifiers[modifier.id] = modifier

    return ContentPack(
        teams=teams,
        roles=roles,
        effects=typed_effects,
        passives=typed_passives,
        selectors=typed_selectors,
        restriction_types=typed_restrictions,
        action_timings=action_timings,
        chat_channels=channels,
        death_causes=typed_causes,
        modifiers=modifiers,
    )


def _validate_core_death_causes(death_causes: Mapping[str, DeathCause]) -> None:
    missing = CORE_DEATH_CAUSE_IDS - set(death_causes)
    if missing:
        raise ContentValidationError(
            "content must register every core death cause: " + ", ".join(sorted(missing))
        )


def load_preset(path: str | Path, content: ContentPack) -> Preset:
    """Load one preset and validate every global rule value strictly."""

    preset_path = Path(path)
    data = _mapping(_load_yaml(preset_path), str(preset_path))
    _keys(data, required={"rules", "roles"}, optional=set(), path=str(preset_path))
    role_counts_raw = _mapping(data["roles"], f"{preset_path}.roles")
    role_counts: dict[str, int] = {}
    for role_id, count in role_counts_raw.items():
        _identifier(role_id, f"{preset_path}.roles key")
        _integer(count, f"{preset_path}.roles.{role_id}", minimum=1)
        if role_id not in content.roles:
            raise ContentValidationError(
                f"{preset_path}.roles references unknown role '{role_id}'"
            )
        role_counts[role_id] = count
    rules = _parse_rules(
        _mapping(data["rules"], f"{preset_path}.rules"), str(preset_path), content
    )
    _validate_preset_runtime_capabilities(preset_path, content, role_counts, rules)
    return Preset(
        name=preset_path.stem,
        rules=rules,
        role_counts=role_counts,
    )


def _validate_preset_runtime_capabilities(
    preset_path: Path,
    content: ContentPack,
    role_counts: Mapping[str, int],
    rules: RulesConfig,
) -> None:
    """Reject a startable preset whose selected roles lack core semantics.

    Content packs may contain roles reserved for a later phase (for example the
    baker), so the capability check is performed when a concrete preset selects
    roles to start.  This keeps unselected expansion content loadable while
    ensuring an actual game never silently ignores a declaration.
    """

    selected_role_ids = set(role_counts)
    if rules.role_missing.enabled:
        selected_role_ids.add(rules.role_missing.replacement_role_id)
    errors = unsupported_runtime_references(
        content.roles[role_id] for role_id in sorted(selected_role_ids)
    )
    if errors:
        raise ContentValidationError(
            f"{preset_path} references runtime features not implemented by this core: "
            + "; ".join(errors)
        )


def _parse_team(data: Any, path: str) -> Team:
    mapping = _mapping(data, path)
    _keys(
        mapping,
        required={"id", "name", "default_attributes", "win_conditions"},
        optional={"teammate_tags"},
        path=path,
    )
    defaults = _mapping(mapping["default_attributes"], f"{path}.default_attributes")
    _keys(
        defaults,
        required={"count_as", "inspect_result", "medium_result"},
        optional=set(),
        path=f"{path}.default_attributes",
    )
    _count_as(defaults["count_as"], f"{path}.default_attributes.count_as", allow_by_role=False)
    _inspect_result(
        defaults["inspect_result"], f"{path}.default_attributes.inspect_result", allow_by_role=False
    )
    _non_empty_string(defaults["medium_result"], f"{path}.default_attributes.medium_result")
    conditions = tuple(
        _parse_win_condition(item, f"{path}.win_conditions[{index}]")
        for index, item in enumerate(_list(mapping["win_conditions"], f"{path}.win_conditions"))
    )
    if not conditions:
        raise ContentValidationError(f"{path}.win_conditions must not be empty")
    return Team(
        id=_identifier(mapping["id"], f"{path}.id"),
        name=_non_empty_string(mapping["name"], f"{path}.name"),
        default_count_as=defaults["count_as"],
        default_inspect_result=defaults["inspect_result"],
        default_medium_result=defaults["medium_result"],
        win_conditions=conditions,
        teammate_tags=frozenset(
            _identifier(tag, f"{path}.teammate_tags")
            for tag in _list(mapping.get("teammate_tags", []), f"{path}.teammate_tags")
        ),
    )


def _parse_named(data: Any, kind: str, path: str) -> dict[str, str]:
    mapping = _mapping(data, path)
    _keys(mapping, required={"id", "name"}, optional=set(), path=path)
    return {
        "id": _identifier(mapping["id"], f"{path}.id"),
        "name": _non_empty_string(mapping["name"], f"{path}.name"),
    }


def _parse_action_timing(data: Any, path: str) -> ActionTiming:
    mapping = _mapping(data, path)
    _keys(mapping, required={"id", "name", "phases"}, optional=set(), path=path)
    return ActionTiming(
        id=_identifier(mapping["id"], f"{path}.id"),
        name=_non_empty_string(mapping["name"], f"{path}.name"),
        phases=_phase_ids(mapping["phases"], f"{path}.phases", allow_empty=False),
    )


def _parse_chat_channel(data: Any, path: str) -> ChatChannel:
    mapping = _mapping(data, path)
    _keys(
        mapping,
        required={"id", "name", "phases", "allows_co", "public"},
        optional=set(),
        path=path,
    )
    return ChatChannel(
        id=_identifier(mapping["id"], f"{path}.id"),
        name=_non_empty_string(mapping["name"], f"{path}.name"),
        phases=_phase_ids(mapping["phases"], f"{path}.phases", allow_empty=True),
        allows_co=_boolean(mapping["allows_co"], f"{path}.allows_co"),
        is_public=_boolean(mapping["public"], f"{path}.public"),
    )


def _parse_role(
    data: Any,
    path: str,
    teams: Mapping[str, Team],
    effects: Mapping[str, Effect],
    passive_definitions: Mapping[str, PassiveDefinition],
    selectors: Mapping[str, TargetSelector],
    restriction_types: Mapping[str, RestrictionType],
    action_timings: Mapping[str, ActionTiming],
    channels: Mapping[str, ChatChannel],
    death_causes: Mapping[str, DeathCause],
) -> Role:
    mapping = _mapping(data, path)
    _keys(
        mapping,
        required={
            "id",
            "name",
            "claimable",
            "team",
            "attack_result",
            "tags",
            "knows_teammates",
            "chat_channels",
            "abilities",
            "passives",
            "options",
        },
        optional={"count_as", "inspect_result", "medium_result"},
        path=path,
    )
    team_id = _team_id(mapping["team"], f"{path}.team", teams)
    team = teams[team_id]
    attributes = RoleAttributes(
        team=team_id,
        count_as=mapping.get("count_as", team.default_count_as),
        attack_result=mapping["attack_result"],
        inspect_result=mapping.get("inspect_result", team.default_inspect_result),
        medium_result=mapping.get("medium_result", team.default_medium_result),
    )
    _validate_attributes(attributes, f"{path}.attributes", teams, allow_by_role=False)
    abilities = tuple(
        _parse_ability(
            item,
            f"{path}.abilities[{index}]",
            effects,
            action_timings,
            selectors,
            restriction_types,
            death_causes,
        )
        for index, item in enumerate(_list(mapping["abilities"], f"{path}.abilities"))
    )
    ids = [ability.id for ability in abilities]
    if len(ids) != len(set(ids)):
        raise ContentValidationError(f"{path}.abilities contains duplicate ability ids")
    passives = tuple(
        _parse_passive(item, f"{path}.passives[{index}]", effects, passive_definitions, death_causes)
        for index, item in enumerate(_list(mapping["passives"], f"{path}.passives"))
    )
    return Role(
        id=_identifier(mapping["id"], f"{path}.id"),
        name=_non_empty_string(mapping["name"], f"{path}.name"),
        claimable=_boolean(mapping["claimable"], f"{path}.claimable"),
        attributes=attributes,
        tags=frozenset(_identifier(tag, f"{path}.tags") for tag in _list(mapping["tags"], f"{path}.tags")),
        knowledge=Knowledge(
            {"knows_teammates": _boolean(mapping["knows_teammates"], f"{path}.knows_teammates")}
        ),
        chat_channels=_channel_ids(mapping["chat_channels"], f"{path}.chat_channels", channels),
        abilities=abilities,
        passives=passives,
        options=_parse_options(mapping["options"], f"{path}.options"),
    )


def _parse_modifier(
    data: Any,
    path: str,
    teams: Mapping[str, Team],
    roles: Mapping[str, Role],
    effects: Mapping[str, Effect],
    passive_definitions: Mapping[str, PassiveDefinition],
    selectors: Mapping[str, TargetSelector],
    restriction_types: Mapping[str, RestrictionType],
    channels: Mapping[str, ChatChannel],
    death_causes: Mapping[str, DeathCause],
) -> Modifier:
    mapping = _mapping(data, path)
    _keys(
        mapping,
        required={
            "id",
            "name",
            "kind",
            "grant",
            "win_condition",
            "passives",
            "knowledge",
            "chat_channels",
            "overrides",
            "exclusions",
        },
        optional=set(),
        path=path,
    )
    if mapping["kind"] != "modifier":
        raise ContentValidationError(f"{path}.kind must be 'modifier'")
    grant = _parse_modifier_grant(mapping["grant"], f"{path}.grant")
    win_condition = _parse_modifier_win_condition(mapping["win_condition"], f"{path}.win_condition")
    overrides = _parse_overrides(mapping["overrides"], f"{path}.overrides", teams)
    exclusions = frozenset(
        _identifier(role_id, f"{path}.exclusions")
        for role_id in _list(mapping["exclusions"], f"{path}.exclusions")
    )
    unknown_exclusions = exclusions.difference(roles)
    if unknown_exclusions:
        raise ContentValidationError(
            f"{path}.exclusions references unknown role(s): {', '.join(sorted(unknown_exclusions))}"
        )
    return Modifier(
        id=_identifier(mapping["id"], f"{path}.id"),
        name=_non_empty_string(mapping["name"], f"{path}.name"),
        grant=grant,
        win_condition=win_condition,
        passives=tuple(
            _parse_passive(item, f"{path}.passives[{index}]", effects, passive_definitions, death_causes)
            for index, item in enumerate(_list(mapping["passives"], f"{path}.passives"))
        ),
        knowledge=Knowledge(_mapping(mapping["knowledge"], f"{path}.knowledge")),
        chat_channels=_channel_ids(mapping["chat_channels"], f"{path}.chat_channels", channels),
        overrides=overrides,
        exclusions=exclusions,
    )


def _parse_ability(
    data: Any,
    path: str,
    effects: Mapping[str, Effect],
    action_timings: Mapping[str, ActionTiming],
    selectors: Mapping[str, TargetSelector],
    restriction_types: Mapping[str, RestrictionType],
    death_causes: Mapping[str, DeathCause],
) -> Ability:
    mapping = _mapping(data, path)
    _keys(
        mapping,
        required={
            "id",
            "timing",
            "available_from_night",
            "priority",
            "resolution",
            "target",
            "uses",
            "no_selection",
            "restrictions",
        },
        optional={"description", "effects"},
        path=path,
    )
    target_mapping = _mapping(mapping["target"], f"{path}.target")
    _keys(target_mapping, required={"selector", "count"}, optional=set(target_mapping) - {"selector", "count"}, path=f"{path}.target")
    uses_mapping = _mapping(mapping["uses"], f"{path}.uses")
    _keys(uses_mapping, required={"per_night", "per_game"}, optional=set(), path=f"{path}.uses")
    ability_priority = _integer(mapping["priority"], f"{path}.priority", minimum=0)
    target_count = _integer(target_mapping["count"], f"{path}.target.count", minimum=1)
    resolution = mapping["resolution"]
    _one_of(resolution, {"group", "individual"}, f"{path}.resolution")
    effect_references = _parse_effect_references(
        mapping.get("effects", []),
        f"{path}.effects",
        effects,
        ability_priority,
    )
    _validate_death_cause_references(target_mapping, f"{path}.target", death_causes)
    selector = _registered_id(
        target_mapping["selector"],
        f"{path}.target.selector",
        selectors,
        "selector",
    )
    restrictions = tuple(
        _parse_restriction(item, f"{path}.restrictions[{index}]", restriction_types)
        for index, item in enumerate(_list(mapping["restrictions"], f"{path}.restrictions"))
    )
    no_selection = mapping["no_selection"]
    _one_of(no_selection, {"random", "skip"}, f"{path}.no_selection")
    if resolution == "group" and target_count != 1:
        raise ContentValidationError(f"{path}.resolution 'group' requires target.count to be 1")
    return Ability(
        id=_identifier(mapping["id"], f"{path}.id"),
        timing=_registered_id(mapping["timing"], f"{path}.timing", action_timings, "action timing"),
        available_from_night=_integer(mapping["available_from_night"], f"{path}.available_from_night", minimum=0),
        priority=ability_priority,
        resolution=resolution,
        target=TargetSpec(
            selector=selector,
            count=target_count,
            options={key: value for key, value in target_mapping.items() if key not in {"selector", "count"}},
        ),
        uses=Uses(
            per_night=_optional_integer(uses_mapping["per_night"], f"{path}.uses.per_night", minimum=0),
            per_game=_optional_integer(uses_mapping["per_game"], f"{path}.uses.per_game", minimum=0),
        ),
        no_selection=no_selection,
        restrictions=restrictions,
        effects=effect_references,
        description=_optional_string(mapping.get("description"), f"{path}.description"),
    )


def _parse_restriction(
    data: Any, path: str, restriction_types: Mapping[str, RestrictionType]
) -> Restriction:
    mapping = _mapping(data, path)
    _keys(mapping, required={"type"}, optional=set(mapping) - {"type"}, path=path)
    return Restriction(
        type=_registered_id(mapping["type"], f"{path}.type", restriction_types, "restriction type"),
        enabled_when=_validate_enabled_when(mapping.get("enabled_when"), f"{path}.enabled_when"),
        options={key: value for key, value in mapping.items() if key not in {"type", "enabled_when"}},
    )


def _parse_passive(
    data: Any,
    path: str,
    effects: Mapping[str, Effect],
    passive_definitions: Mapping[str, PassiveDefinition],
    death_causes: Mapping[str, DeathCause],
) -> Passive:
    mapping = _mapping(data, path)
    _keys(mapping, required={"type", "priority", "rules"}, optional={"effects"}, path=path)
    passive_type = _identifier(mapping["type"], f"{path}.type")
    if passive_type not in passive_definitions:
        raise ContentValidationError(f"{path}.type references unregistered passive '{passive_type}'")
    passive_priority = _integer(mapping["priority"], f"{path}.priority", minimum=0)
    rules = tuple(
        _mapping(rule, f"{path}.rules[{index}]")
        for index, rule in enumerate(_list(mapping["rules"], f"{path}.rules"))
    )
    if not rules:
        raise ContentValidationError(f"{path}.rules must contain at least one rule")
    for index, rule in enumerate(rules):
        _validate_death_cause_references(rule, f"{path}.rules[{index}]", death_causes)
    effect_references = _parse_effect_references(
        mapping.get("effects", []),
        f"{path}.effects",
        effects,
        passive_priority,
    )
    if passive_type == "public_notify_if_alive":
        _validate_public_notify_if_alive(rules, effect_references, path)
    return Passive(
        type=passive_type,
        priority=passive_priority,
        rules=rules,
        effects=effect_references,
    )


def _validate_public_notify_if_alive(
    rules: tuple[Mapping[str, Any], ...], effects: tuple[EffectReference, ...], path: str
) -> None:
    if {effect.id for effect in effects} != {"public_notify"}:
        raise ContentValidationError(f"{path}.effects must contain only public_notify")
    for index, rule in enumerate(rules):
        rule_path = f"{path}.rules[{index}]"
        _keys(rule, required={"when", "notify_id"}, optional=set(), path=rule_path)
        when = _mapping(rule["when"], f"{rule_path}.when")
        _keys(when, required={"event", "actor_alive"}, optional=set(), path=f"{rule_path}.when")
        if when["event"] != "dawn":
            raise ContentValidationError(f"{rule_path}.when.event must be dawn")
        if _boolean(when["actor_alive"], f"{rule_path}.when.actor_alive") is not True:
            raise ContentValidationError(f"{rule_path}.when.actor_alive must be true")
        _identifier(rule["notify_id"], f"{rule_path}.notify_id")


def _parse_options(data: Any, path: str) -> Mapping[str, RoleOption]:
    mapping = _mapping(data, path)
    options: dict[str, RoleOption] = {}
    for identifier, option in mapping.items():
        option_path = f"{path}.{identifier}"
        _identifier(identifier, f"{path} key")
        option_mapping = _mapping(option, option_path)
        _keys(option_mapping, required={"type", "values", "default"}, optional=set(), path=option_path)
        if option_mapping["type"] != "enum":
            raise ContentValidationError(f"{option_path}.type must be 'enum' in Phase 1.1")
        values = tuple(_list(option_mapping["values"], f"{option_path}.values"))
        if not values:
            raise ContentValidationError(f"{option_path}.values must not be empty")
        if option_mapping["default"] not in values:
            raise ContentValidationError(f"{option_path}.default must be one of values")
        options[identifier] = RoleOption("enum", values, option_mapping["default"])
    return options


def _parse_modifier_grant(data: Any, path: str) -> ModifierGrant:
    mapping = _mapping(data, path)
    _keys(mapping, required={"timing", "duration"}, optional={"duration_nights"}, path=path)
    timing = mapping["timing"]
    duration = mapping["duration"]
    if timing not in {"assignment", "in_game"}:
        raise ContentValidationError(f"{path}.timing must be assignment or in_game")
    if duration not in {"permanent", "until_next_dawn", "n_nights"}:
        raise ContentValidationError(f"{path}.duration is invalid")
    duration_nights = mapping.get("duration_nights")
    if duration == "n_nights":
        _integer(duration_nights, f"{path}.duration_nights", minimum=1)
    elif duration_nights is not None:
        raise ContentValidationError(f"{path}.duration_nights is only allowed for n_nights")
    return ModifierGrant(timing, duration, duration_nights)


def _parse_modifier_win_condition(data: Any, path: str) -> ModifierWinCondition:
    mapping = _mapping(data, path)
    _keys(mapping, required={"mode"}, optional={"value", "priority"}, path=path)
    mode = mapping["mode"]
    if mode not in {"override", "add", "none"}:
        raise ContentValidationError(f"{path}.mode is invalid")
    value = mapping.get("value")
    priority = mapping.get("priority")
    if mode == "none":
        if value is not None or priority is not None:
            raise ContentValidationError(f"{path}.value and priority are forbidden for mode none")
        return ModifierWinCondition(mode, None, None)
    if value is None:
        raise ContentValidationError(f"{path}.value is required for mode {mode}")
    condition = _parse_win_condition(value, f"{path}.value")
    if mode == "override":
        _integer(priority, f"{path}.priority")
    elif priority is not None:
        raise ContentValidationError(f"{path}.priority is only valid for override")
    return ModifierWinCondition(mode, condition, priority)


def _parse_overrides(
    data: Any, path: str, teams: Mapping[str, Team]
) -> AttributeOverrides:
    mapping = _mapping(data, path)
    _keys(mapping, required=set(), optional=set(ATTRIBUTE_NAMES), path=path)
    values = {attribute: mapping.get(attribute) for attribute in ATTRIBUTE_NAMES}
    if values["team"] is not None and values["team"] != "by_role":
        _team_id(values["team"], f"{path}.team", teams)
    if values["count_as"] is not None:
        _count_as(values["count_as"], f"{path}.count_as", allow_by_role=True)
    if values["attack_result"] is not None:
        _attack_result(values["attack_result"], f"{path}.attack_result", allow_by_role=True)
    if values["inspect_result"] is not None:
        _inspect_result(values["inspect_result"], f"{path}.inspect_result", allow_by_role=True)
    if values["medium_result"] is not None:
        _non_empty_string(values["medium_result"], f"{path}.medium_result")
    return AttributeOverrides(**values)


def _parse_win_condition(data: Any, path: str) -> WinCondition:
    mapping = _mapping(data, path)
    condition_type = mapping.get("type")
    if condition_type not in _WIN_CONDITION_TYPES:
        raise ContentValidationError(f"{path}.type is an unknown win condition")
    if condition_type == "eliminate_role_tag":
        _keys(mapping, required={"type", "tag"}, optional=set(), path=path)
        _identifier(mapping["tag"], f"{path}.tag")
    elif condition_type == "count_parity":
        _keys(mapping, required={"type", "subject", "against", "operator"}, optional=set(), path=path)
        _count_as(mapping["subject"], f"{path}.subject", allow_by_role=False)
        _count_as(mapping["against"], f"{path}.against", allow_by_role=False)
        if mapping["operator"] not in {"gte", "gt", "eq", "lte", "lt"}:
            raise ContentValidationError(f"{path}.operator is invalid")
    else:
        _keys(mapping, required={"type", "replaces"}, optional=set(), path=path)
        _boolean(mapping["replaces"], f"{path}.replaces")
    return WinCondition(condition_type, dict(mapping))


def _parse_rules(data: Mapping[str, Any], path: str, content: ContentPack) -> RulesConfig:
    _keys(
        data,
        required={
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
        },
        optional=set(),
        path=f"{path}.rules",
    )
    if data["first_night_seer"] not in {"none", "free", "random_white"}:
        raise ContentValidationError(f"{path}.rules.first_night_seer is invalid")
    vote = _parse_vote_rules(_mapping(data["vote"], f"{path}.rules.vote"), f"{path}.rules.vote")
    guard = _parse_guard_rules(_mapping(data["guard"], f"{path}.rules.guard"), f"{path}.rules.guard")
    night_action = _parse_night_action_rules(
        _mapping(data["night_action"], f"{path}.rules.night_action"),
        f"{path}.rules.night_action",
    )
    medium = _parse_medium_rules(_mapping(data["medium"], f"{path}.rules.medium"), f"{path}.rules.medium")
    wolf_attack = _parse_wolf_attack_rules(
        _mapping(data["wolf_attack"], f"{path}.rules.wolf_attack"), f"{path}.rules.wolf_attack"
    )
    co = _parse_co_rules(_mapping(data["co"], f"{path}.rules.co"), f"{path}.rules.co")
    sudden_death = _parse_sudden_death_rules(
        _mapping(data["sudden_death"], f"{path}.rules.sudden_death"),
        f"{path}.rules.sudden_death",
    )
    death = _parse_death_rules(_mapping(data["death"], f"{path}.rules.death"), f"{path}.rules.death")
    graveyard = _parse_graveyard_rules(
        _mapping(data["graveyard"], f"{path}.rules.graveyard"),
        f"{path}.rules.graveyard",
    )
    role_missing = _parse_role_missing_rules(
        _mapping(data["role_missing"], f"{path}.rules.role_missing"),
        f"{path}.rules.role_missing",
        content,
    )
    extension = _parse_extension_rules(
        _mapping(data["extension"], f"{path}.rules.extension"), f"{path}.rules.extension"
    )
    shortening = _parse_shortening_rules(
        _mapping(data["shortening"], f"{path}.rules.shortening"), f"{path}.rules.shortening"
    )
    order = tuple(_list(data["win_evaluation_order"], f"{path}.rules.win_evaluation_order"))
    if not order:
        raise ContentValidationError(f"{path}.rules.win_evaluation_order must not be empty")
    if any(not isinstance(team_id, str) for team_id in order):
        raise ContentValidationError(f"{path}.rules.win_evaluation_order must contain team ids")
    if len(order) != len(set(order)):
        raise ContentValidationError(f"{path}.rules.win_evaluation_order contains duplicates")
    if set(order) != set(content.teams):
        raise ContentValidationError(
            f"{path}.rules.win_evaluation_order must define every content team exactly once"
        )
    return RulesConfig(
        first_night_seer=data["first_night_seer"],
        vote=vote,
        guard=guard,
        night_action=night_action,
        medium=medium,
        wolf_attack=wolf_attack,
        co=co,
        sudden_death=sudden_death,
        death=death,
        graveyard=graveyard,
        role_missing=role_missing,
        day_seconds=_integer(data["day_seconds"], f"{path}.rules.day_seconds", minimum=1),
        vote_seconds=_integer(data["vote_seconds"], f"{path}.rules.vote_seconds", minimum=1),
        night_seconds=_integer(data["night_seconds"], f"{path}.rules.night_seconds", minimum=1),
        silence_after_dawn_seconds=_integer(
            data["silence_after_dawn_seconds"], f"{path}.rules.silence_after_dawn_seconds", minimum=0
        ),
        extension=extension,
        shortening=shortening,
        win_evaluation_order=order,
    )


def _parse_role_missing_rules(
    data: Mapping[str, Any], path: str, content: ContentPack
) -> RoleMissingRules:
    _keys(data, required={"enabled", "replacement_role_id"}, optional=set(), path=path)
    replacement_role_id = _registered_id(
        data["replacement_role_id"],
        f"{path}.replacement_role_id",
        content.roles,
        "role",
    )
    return RoleMissingRules(
        enabled=_boolean(data["enabled"], f"{path}.enabled"),
        replacement_role_id=replacement_role_id,
    )


def _parse_vote_rules(data: Mapping[str, Any], path: str) -> VoteRules:
    _keys(
        data,
        required={"runoff", "tie_after_runoff", "tie_without_runoff", "abstain", "self_vote", "reveal"},
        optional=set(),
        path=path,
    )
    _one_of(data["tie_after_runoff"], {"no_lynch", "random"}, f"{path}.tie_after_runoff")
    _one_of(data["tie_without_runoff"], {"no_lynch", "random"}, f"{path}.tie_without_runoff")
    abstain = _parse_abstain_rules(_mapping(data["abstain"], f"{path}.abstain"), f"{path}.abstain")
    _one_of(data["reveal"], {"hidden", "live", "after"}, f"{path}.reveal")
    return VoteRules(
        runoff=_boolean(data["runoff"], f"{path}.runoff"),
        tie_after_runoff=data["tie_after_runoff"],
        tie_without_runoff=data["tie_without_runoff"],
        abstain=abstain,
        self_vote=_boolean(data["self_vote"], f"{path}.self_vote"),
        reveal=data["reveal"],
    )


def _parse_abstain_rules(data: Mapping[str, Any], path: str) -> AbstainRules:
    _keys(data, required={"enabled", "max_per_player"}, optional=set(), path=path)
    return AbstainRules(
        enabled=_boolean(data["enabled"], f"{path}.enabled"),
        max_per_player=_optional_integer(data["max_per_player"], f"{path}.max_per_player", minimum=0),
    )


def _parse_guard_rules(data: Mapping[str, Any], path: str) -> GuardRules:
    _keys(data, required={"consecutive", "self_guard"}, optional=set(), path=path)
    return GuardRules(
        consecutive=_boolean(data["consecutive"], f"{path}.consecutive"),
        self_guard=_boolean(data["self_guard"], f"{path}.self_guard"),
    )


def _parse_night_action_rules(data: Mapping[str, Any], path: str) -> NightActionRules:
    _keys(data, required={"no_selection"}, optional=set(), path=path)
    no_selection = data["no_selection"]
    if no_selection is not None:
        _one_of(no_selection, {"random", "skip"}, f"{path}.no_selection")
    return NightActionRules(no_selection)


def _parse_medium_rules(data: Mapping[str, Any], path: str) -> MediumRules:
    _keys(data, required={"notify_timing"}, optional=set(), path=path)
    _one_of(data["notify_timing"], {"night", "dawn"}, f"{path}.notify_timing")
    return MediumRules(data["notify_timing"])


def _parse_wolf_attack_rules(data: Mapping[str, Any], path: str) -> WolfAttackRules:
    _keys(data, required={"target_decision", "tie"}, optional=set(), path=path)
    _one_of(data["target_decision"], {"majority", "random"}, f"{path}.target_decision")
    _one_of(data["tie"], {"random"}, f"{path}.tie")
    return WolfAttackRules(data["target_decision"], data["tie"])


def _parse_co_rules(data: Mapping[str, Any], path: str) -> CoRules:
    _keys(data, required={"max_per_day", "allow_villager_claim"}, optional=set(), path=path)
    return CoRules(
        max_per_day=_optional_integer(data["max_per_day"], f"{path}.max_per_day", minimum=1),
        allow_villager_claim=_boolean(data["allow_villager_claim"], f"{path}.allow_villager_claim"),
    )


def _parse_sudden_death_rules(data: Mapping[str, Any], path: str) -> SuddenDeathRules:
    _keys(data, required={"enabled"}, optional=set(), path=path)
    return SuddenDeathRules(enabled=_boolean(data["enabled"], f"{path}.enabled"))


def _parse_death_rules(data: Mapping[str, Any], path: str) -> DeathRules:
    _keys(data, required={"public_detail"}, optional=set(), path=path)
    _one_of(data["public_detail"], {"phase", "cause", "none"}, f"{path}.public_detail")
    return DeathRules(data["public_detail"])


def _parse_graveyard_rules(data: Mapping[str, Any], path: str) -> GraveyardRules:
    _keys(data, required={"view_public", "speak", "reveal_roles"}, optional=set(), path=path)
    return GraveyardRules(
        view_public=_boolean(data["view_public"], f"{path}.view_public"),
        speak=_boolean(data["speak"], f"{path}.speak"),
        reveal_roles=_boolean(data["reveal_roles"], f"{path}.reveal_roles"),
    )


def _parse_extension_rules(data: Mapping[str, Any], path: str) -> ExtensionRules:
    _keys(data, required={"max_count", "seconds_per_extension", "approval"}, optional=set(), path=path)
    _one_of(data["approval"], {"all", "majority"}, f"{path}.approval")
    return ExtensionRules(
        max_count=_integer(data["max_count"], f"{path}.max_count", minimum=0),
        seconds_per_extension=_integer(data["seconds_per_extension"], f"{path}.seconds_per_extension", minimum=1),
        approval=data["approval"],
    )


def _parse_shortening_rules(data: Mapping[str, Any], path: str) -> ShorteningRules:
    _keys(data, required={"enabled", "approval"}, optional=set(), path=path)
    _one_of(data["approval"], {"all", "majority"}, f"{path}.approval")
    return ShorteningRules(
        enabled=_boolean(data["enabled"], f"{path}.enabled"),
        approval=data["approval"],
    )


def _validate_attributes(
    attributes: RoleAttributes, path: str, teams: Mapping[str, Team], *, allow_by_role: bool
) -> None:
    _team_id(attributes.team, f"{path}.team", teams)
    _count_as(attributes.count_as, f"{path}.count_as", allow_by_role)
    _attack_result(attributes.attack_result, f"{path}.attack_result", allow_by_role)
    _inspect_result(attributes.inspect_result, f"{path}.inspect_result", allow_by_role)
    _non_empty_string(attributes.medium_result, f"{path}.medium_result")


def _count_as(value: Any, path: str, allow_by_role: bool) -> None:
    allowed = _COUNT_AS_VALUES if allow_by_role else _COUNT_AS_VALUES - {"by_role"}
    _one_of(value, allowed, path)


def _attack_result(value: Any, path: str, allow_by_role: bool) -> None:
    allowed = _ATTACK_RESULT_VALUES if allow_by_role else _ATTACK_RESULT_VALUES - {"by_role"}
    _one_of(value, allowed, path)


def _inspect_result(value: Any, path: str, allow_by_role: bool) -> None:
    allowed = _INSPECT_RESULT_VALUES if allow_by_role else _INSPECT_RESULT_VALUES - {"by_role"}
    _one_of(value, allowed, path)


def _channel_ids(value: Any, path: str, channels: Mapping[str, ChatChannel]) -> tuple[str, ...]:
    ids = tuple(_list(value, path))
    if len(ids) != len(set(ids)):
        raise ContentValidationError(f"{path} contains duplicates")
    for identifier in ids:
        _identifier(identifier, path)
        if identifier not in channels:
            raise ContentValidationError(f"{path} references unknown chat channel '{identifier}'")
    return ids


def _phase_ids(value: Any, path: str, *, allow_empty: bool) -> tuple[str, ...]:
    phases = tuple(_list(value, path))
    if not phases and not allow_empty:
        raise ContentValidationError(f"{path} must not be empty")
    if len(phases) != len(set(phases)):
        raise ContentValidationError(f"{path} contains duplicates")
    for index, phase_id in enumerate(phases):
        _identifier(phase_id, f"{path}[{index}]")
        if phase_id not in _PHASE_IDS:
            raise ContentValidationError(f"{path}[{index}] references unknown game phase '{phase_id}'")
    return phases


def _registered_ids(
    value: Any, path: str, registry: Mapping[str, Any], kind: str
) -> tuple[str, ...]:
    ids = tuple(_list(value, path))
    for identifier in ids:
        _identifier(identifier, path)
        if identifier not in registry:
            raise ContentValidationError(f"{path} references unregistered {kind} '{identifier}'")
    return ids


def _registered_id(value: Any, path: str, registry: Mapping[str, Any], kind: str) -> str:
    identifier = _identifier(value, path)
    if identifier not in registry:
        raise ContentValidationError(f"{path} references unregistered {kind} '{identifier}'")
    return identifier


def _parse_effect_references(
    value: Any,
    path: str,
    effects: Mapping[str, Effect],
    default_priority: int,
) -> tuple[EffectReference, ...]:
    references: list[EffectReference] = []
    for index, item in enumerate(_list(value, path)):
        item_path = f"{path}[{index}]"
        if isinstance(item, str):
            effect_id = _registered_id(item, item_path, effects, "effect")
            priority = default_priority
        else:
            mapping = _mapping(item, item_path)
            _keys(mapping, required={"id"}, optional={"priority"}, path=item_path)
            effect_id = _registered_id(mapping["id"], f"{item_path}.id", effects, "effect")
            priority = _integer(
                mapping.get("priority", default_priority),
                f"{item_path}.priority",
                minimum=0,
            )
        references.append(EffectReference(effect_id, priority))
    effect_ids = [reference.id for reference in references]
    if len(effect_ids) != len(set(effect_ids)):
        raise ContentValidationError(f"{path} contains duplicate effect references")
    return tuple(references)


def _validate_enabled_when(value: Any, path: str) -> str | None:
    expression = _optional_string(value, path)
    if expression is None:
        return None
    match = _ENABLED_WHEN_PATTERN.fullmatch(expression)
    if match is None:
        raise ContentValidationError(
            f"{path} must compare a boolean RulesConfig path to true or false"
        )
    if match.group("path") not in _boolean_rule_paths(RulesConfig):
        raise ContentValidationError(
            f"{path} references unknown or non-boolean rule path 'rules.{match.group('path')}'"
        )
    return expression


def _validate_death_cause_references(
    value: Any, path: str, death_causes: Mapping[str, DeathCause]
) -> None:
    """Validate generic ability/passive declarations that name death causes."""

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "death_cause":
                identifier = _identifier(child, child_path)
                if identifier not in death_causes:
                    raise ContentValidationError(
                        f"{child_path} references unknown death cause '{identifier}'"
                    )
            elif key == "causes":
                for index, identifier in enumerate(_list(child, child_path)):
                    identifier = _identifier(identifier, f"{child_path}[{index}]")
                    if identifier not in death_causes:
                        raise ContentValidationError(
                            f"{child_path}[{index}] references unknown death cause '{identifier}'"
                        )
            else:
                _validate_death_cause_references(child, child_path, death_causes)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_death_cause_references(child, f"{path}[{index}]", death_causes)


def _list_at(path: Path, field: str) -> list[Any]:
    data = _mapping(_load_yaml(path), path.as_posix())
    _keys(data, required={field}, optional=set(), path=path.as_posix())
    return _list(data[field], f"{path.as_posix()}.{field}")


def _indexed(records: Iterable[dict[str, str]], kind: str) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for record in records:
        identifier = record["id"]
        if identifier in indexed:
            raise ContentValidationError(f"duplicate {kind} id '{identifier}'")
        indexed[identifier] = record
    if not indexed:
        raise ContentValidationError(f"content must define at least one {kind}")
    return indexed


def _index_models(records: Iterable[Any], kind: str) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    for record in records:
        identifier = record.id
        if identifier in indexed:
            raise ContentValidationError(f"duplicate {kind} id '{identifier}'")
        indexed[identifier] = record
    if not indexed:
        raise ContentValidationError(f"content must define at least one {kind}")
    return indexed


def _load_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ContentValidationError(f"required content file does not exist: {path}")
    try:
        with path.open(encoding="utf-8") as source:
            return yaml.safe_load(source)
    except yaml.YAMLError as error:
        raise ContentValidationError(f"invalid YAML in {path}: {error}") from error


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ContentValidationError(f"{path} must be a mapping")
    if not all(isinstance(key, str) for key in value):
        raise ContentValidationError(f"{path} must use string keys")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContentValidationError(f"{path} must be a list")
    return value


def _keys(
    mapping: Mapping[str, Any], *, required: set[str], optional: set[str], path: str
) -> None:
    missing = required.difference(mapping)
    unknown = set(mapping).difference(required | optional)
    if missing:
        raise ContentValidationError(f"{path} is missing required key(s): {', '.join(sorted(missing))}")
    if unknown:
        raise ContentValidationError(f"{path} has unknown key(s): {', '.join(sorted(unknown))}")


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContentValidationError(f"{path} must be a non-empty string")
    return value


def _non_empty_string(value: Any, path: str) -> str:
    return _identifier(value, path)


def _optional_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _non_empty_string(value, path)


def _team_id(value: Any, path: str, teams: Mapping[str, Team]) -> str:
    identifier = _identifier(value, path)
    if identifier not in teams:
        raise ContentValidationError(f"{path} references unknown team '{identifier}'")
    return identifier


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ContentValidationError(f"{path} must be a boolean")
    return value


def _integer(value: Any, path: str, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContentValidationError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise ContentValidationError(f"{path} must be at least {minimum}")
    return value


def _optional_integer(value: Any, path: str, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    return _integer(value, path, minimum)


def _one_of(value: Any, allowed: set[str] | frozenset[str], path: str) -> None:
    if value not in allowed:
        raise ContentValidationError(f"{path} must be one of: {', '.join(sorted(allowed))}")
