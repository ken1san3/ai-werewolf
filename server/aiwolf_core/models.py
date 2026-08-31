"""Data-only domain models for roles, content, and effective attributes.

The game engine is deliberately absent from this module.  It establishes the
content contracts that later phases use to validate and resolve play.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping, Sequence


ATTRIBUTE_NAMES = (
    "team",
    "count_as",
    "attack_result",
    "inspect_result",
    "medium_result",
)


class GamePhase(str, Enum):
    """The single source of truth for game-phase IDs used by code and content."""

    SETUP = "setup"
    NIGHT0 = "night0"
    DAWN = "dawn"
    DAY = "day"
    VOTE = "vote"
    RUNOFF = "runoff"
    EXECUTION = "execution"
    NIGHT = "night"
    GAME_END = "game_end"


class CoreDeathCause(str, Enum):
    """The seven death-cause IDs the game core is allowed to reference by name."""

    LYNCHED = "lynched"
    ATTACKED = "attacked"
    RETALIATION = "retaliation"
    CURSED = "cursed"
    FOLLOW_DEATH = "follow_death"
    SUDDEN_DEATH = "sudden_death"
    ABILITY = "ability"


CORE_DEATH_CAUSE_IDS = frozenset(cause.value for cause in CoreDeathCause)


@dataclass(frozen=True)
class WinCondition:
    """A declarative team or modifier win condition."""

    type: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class Team:
    id: str
    name: str
    default_count_as: str
    default_inspect_result: str
    default_medium_result: str
    win_conditions: tuple[WinCondition, ...]


@dataclass(frozen=True)
class Effect:
    """Name registration for a future state-changing effect implementation."""

    id: str
    name: str


@dataclass(frozen=True)
class EffectReference:
    """A registered effect scheduled at an explicit resolution priority."""

    id: str
    priority: int


@dataclass(frozen=True)
class PassiveDefinition:
    """Name registration for a future passive implementation."""

    id: str
    name: str


@dataclass(frozen=True)
class TargetSelector:
    id: str
    name: str


@dataclass(frozen=True)
class RestrictionType:
    id: str
    name: str


@dataclass(frozen=True)
class ChatChannel:
    id: str
    name: str
    phases: tuple[str, ...]
    allows_co: bool
    is_public: bool


@dataclass(frozen=True)
class ActionTiming:
    id: str
    name: str
    phases: tuple[str, ...]


@dataclass(frozen=True)
class DeathCause:
    id: str
    name: str


@dataclass(frozen=True)
class TargetSpec:
    selector: str
    count: int
    options: Mapping[str, Any]


@dataclass(frozen=True)
class Uses:
    per_night: int | None
    per_game: int | None


@dataclass(frozen=True)
class Restriction:
    type: str
    enabled_when: str | None
    options: Mapping[str, Any]


@dataclass(frozen=True)
class Ability:
    id: str
    timing: str
    available_from_night: int
    priority: int
    target: TargetSpec
    uses: Uses
    no_selection: str
    restrictions: tuple[Restriction, ...]
    effects: tuple[EffectReference, ...]
    description: str | None = None


@dataclass(frozen=True)
class Passive:
    """A registered passive type with one or more declarative trigger rules."""

    type: str
    priority: int
    rules: tuple[Mapping[str, Any], ...]
    effects: tuple[EffectReference, ...]


@dataclass(frozen=True)
class RoleAttributes:
    team: str
    count_as: str
    attack_result: str
    inspect_result: str
    medium_result: str


@dataclass(frozen=True)
class RoleOption:
    type: str
    values: tuple[Any, ...]
    default: Any


@dataclass(frozen=True)
class Knowledge:
    """Knowledge declarations independent from role attributes and channels."""

    declarations: Mapping[str, Any]

    @property
    def knows_teammates(self) -> bool:
        return self.declarations.get("knows_teammates") is True


@dataclass(frozen=True)
class Role:
    id: str
    name: str
    claimable: bool
    attributes: RoleAttributes
    tags: frozenset[str]
    knowledge: Knowledge
    chat_channels: tuple[str, ...]
    abilities: tuple[Ability, ...]
    passives: tuple[Passive, ...]
    options: Mapping[str, RoleOption]

    @property
    def knows_teammates(self) -> bool:
        """Compatibility view for the §4.1 YAML declaration."""

        return self.knowledge.knows_teammates


@dataclass(frozen=True)
class ModifierGrant:
    timing: str
    duration: str
    duration_nights: int | None = None


@dataclass(frozen=True)
class ModifierWinCondition:
    mode: str
    value: WinCondition | None
    priority: int | None


@dataclass(frozen=True)
class AttributeOverrides:
    team: str | None = None
    count_as: str | None = None
    attack_result: str | None = None
    inspect_result: str | None = None
    medium_result: str | None = None

    def non_null(self) -> Mapping[str, str]:
        return {
            attribute: value
            for attribute, value in (
                ("team", self.team),
                ("count_as", self.count_as),
                ("attack_result", self.attack_result),
                ("inspect_result", self.inspect_result),
                ("medium_result", self.medium_result),
            )
            if value is not None and value != "by_role"
        }


@dataclass(frozen=True)
class Modifier:
    id: str
    name: str
    grant: ModifierGrant
    win_condition: ModifierWinCondition
    passives: tuple[Passive, ...]
    knowledge: Knowledge
    chat_channels: tuple[str, ...]
    overrides: AttributeOverrides
    exclusions: frozenset[str]


@dataclass(frozen=True)
class EffectiveAttributes:
    """The five attributes after applying a player's modifiers."""

    team: str
    count_as: str
    attack_result: str
    inspect_result: str
    medium_result: str


@dataclass(frozen=True)
class AppliedModifier:
    """A modifier attached to a player and its remaining duration state."""

    definition: Modifier
    remaining_nights: int | None = None

    @classmethod
    def grant(cls, modifier: Modifier) -> "AppliedModifier":
        if modifier.grant.duration == "n_nights":
            return cls(modifier, modifier.grant.duration_nights)
        return cls(modifier)


@dataclass(frozen=True)
class PlayerRoleState:
    """Minimal player role state needed for attribute resolution in Phase 1.1."""

    player_id: str
    role: Role
    modifiers: tuple[AppliedModifier, ...] = ()


@dataclass(frozen=True)
class AbstainRules:
    enabled: bool
    max_per_player: int | None


@dataclass(frozen=True)
class VoteRules:
    runoff: bool
    tie_after_runoff: str
    tie_without_runoff: str
    abstain: AbstainRules
    self_vote: bool
    reveal: str


@dataclass(frozen=True)
class GuardRules:
    consecutive: bool
    self_guard: bool


@dataclass(frozen=True)
class NightActionRules:
    no_selection: str | None


@dataclass(frozen=True)
class MediumRules:
    notify_timing: str
    targets: tuple[str, ...]


@dataclass(frozen=True)
class WolfAttackRules:
    target_decision: str
    tie: str


@dataclass(frozen=True)
class CoRules:
    max_per_day: int | None
    allow_villager_claim: bool


@dataclass(frozen=True)
class SuddenDeathRules:
    enabled: bool


@dataclass(frozen=True)
class DeathRules:
    public_detail: str


@dataclass(frozen=True)
class GraveyardRules:
    view_public: bool
    speak: bool
    reveal_roles: bool


@dataclass(frozen=True)
class RoleMissingRules:
    enabled: bool
    replacement_role_id: str


@dataclass(frozen=True)
class ExtensionRules:
    max_count: int
    seconds_per_extension: int
    approval: str


@dataclass(frozen=True)
class ShorteningRules:
    enabled: bool
    approval: str


@dataclass(frozen=True)
class RulesConfig:
    first_night_seer: str
    vote: VoteRules
    guard: GuardRules
    night_action: NightActionRules
    medium: MediumRules
    wolf_attack: WolfAttackRules
    co: CoRules
    sudden_death: SuddenDeathRules
    death: DeathRules
    graveyard: GraveyardRules
    role_missing: RoleMissingRules
    day_seconds: int
    vote_seconds: int
    night_seconds: int
    silence_after_dawn_seconds: int
    extension: ExtensionRules
    shortening: ShorteningRules
    win_evaluation_order: tuple[str, ...]


def validate_modifier_combination(
    role: Role, modifiers: Sequence[AppliedModifier | Modifier]
) -> None:
    """Check one player's modifier set before it is applied.

    Override win conditions require a unique priority.  Until Phase 8 defines
    precedence for competing non-win attributes, conflicting declarations are
    rejected rather than resolved by an incidental attachment order.
    """

    definitions = [
        modifier.definition if isinstance(modifier, AppliedModifier) else modifier
        for modifier in modifiers
    ]
    ids = [modifier.id for modifier in definitions]
    if len(ids) != len(set(ids)):
        raise ValueError("a player cannot hold the same modifier more than once")

    excluded = [modifier.id for modifier in definitions if role.id in modifier.exclusions]
    if excluded:
        raise ValueError(
            f"role '{role.id}' cannot receive modifier(s): {', '.join(excluded)}"
        )

    priorities: set[int] = set()
    overridden_attributes: set[str] = set()
    for modifier in definitions:
        for attribute in modifier.overrides.non_null():
            if attribute in overridden_attributes:
                raise ValueError(
                    "multiple modifiers override the same effective attribute: "
                    f"{attribute}"
                )
            overridden_attributes.add(attribute)
        condition = modifier.win_condition
        if condition.mode != "override":
            continue
        if condition.priority is None:
            raise ValueError(f"modifier '{modifier.id}' requires an override priority")
        if condition.priority in priorities:
            raise ValueError(
                "override win condition priorities collide for one player: "
                f"{condition.priority}"
            )
        priorities.add(condition.priority)


def resolve_effective_attributes(player: PlayerRoleState) -> EffectiveAttributes:
    """Resolve the five role axes through the player's attached modifiers."""

    validate_modifier_combination(player.role, player.modifiers)
    values: dict[str, str] = {
        "team": player.role.attributes.team,
        "count_as": player.role.attributes.count_as,
        "attack_result": player.role.attributes.attack_result,
        "inspect_result": player.role.attributes.inspect_result,
        "medium_result": player.role.attributes.medium_result,
    }
    for applied in player.modifiers:
        values.update(applied.definition.overrides.non_null())
    return EffectiveAttributes(**values)


def resolve_effective_win_conditions(
    player: PlayerRoleState, teams: Mapping[str, Team]
) -> tuple[WinCondition, ...]:
    """Resolve team conditions plus a modifier's declared win-condition mode."""

    validate_modifier_combination(player.role, player.modifiers)
    attributes = resolve_effective_attributes(player)
    try:
        base = teams[attributes.team].win_conditions
    except KeyError as error:
        raise ValueError(f"unknown effective team '{attributes.team}'") from error

    definitions = [applied.definition for applied in player.modifiers]
    overrides = [
        modifier.win_condition
        for modifier in definitions
        if modifier.win_condition.mode == "override"
    ]
    if overrides:
        winner = max(overrides, key=lambda condition: condition.priority or 0)
        if winner.value is None:
            raise ValueError("override win condition is missing its value")
        return (winner.value,)

    additions = [
        modifier.win_condition.value
        for modifier in definitions
        if modifier.win_condition.mode == "add" and modifier.win_condition.value is not None
    ]
    return tuple(base) + tuple(additions)


def expire_modifiers_at_dawn(player: PlayerRoleState) -> PlayerRoleState:
    """Return player state after the next dawn removes/decrements durations."""

    remaining: list[AppliedModifier] = []
    for applied in player.modifiers:
        grant = applied.definition.grant
        if grant.duration == "until_next_dawn":
            continue
        if grant.duration == "n_nights":
            nights = applied.remaining_nights
            if nights is None:
                raise ValueError(
                    f"modifier '{applied.definition.id}' is missing its remaining nights"
                )
            if nights <= 1:
                continue
            remaining.append(replace(applied, remaining_nights=nights - 1))
            continue
        remaining.append(applied)
    return replace(player, modifiers=tuple(remaining))
