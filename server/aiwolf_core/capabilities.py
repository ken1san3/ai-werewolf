"""Runtime capabilities implemented by this build of the game core.

Content registries declare vocabulary.  These sets declare the subset whose
semantics the current core can execute, so a preset cannot start with a role
that would otherwise be ignored or fail only during resolution.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from .models import Ability, Modifier, Passive, Role


IMPLEMENTED_EFFECT_IDS = frozenset(
    {"attack", "inspect", "medium_inspect", "protect", "kill", "inspect_role"}
)
IMPLEMENTED_PASSIVE_IDS = frozenset({"on_inspected", "retaliate_on_death"})
IMPLEMENTED_SELECTOR_IDS = frozenset(
    {
        "alive_all",
        "alive_by_tag",
        "alive_other",
        "alive_without_tag",
        "unexamined_dead_by_cause",
    }
)
IMPLEMENTED_RESTRICTION_TYPE_IDS = frozenset({"no_same_target_consecutive"})


def unsupported_runtime_references(
    roles: Iterable[Role], modifiers: Iterable[Modifier] = ()
) -> tuple[str, ...]:
    """Return deterministic descriptions for content this core cannot execute.

    Phase 1 presets select only roles, so ``load_preset`` currently supplies
    that concrete role set. When Phase 3 adds preset-selected modifiers, its
    loader path must pass those selected modifier definitions through this
    argument before a game may start.
    """

    errors: list[str] = []
    for role in roles:
        _collect_role_errors(role, errors)
    for modifier in modifiers:
        for passive in modifier.passives:
            _collect_passive_errors(f"modifier '{modifier.id}'", passive, errors)
    return tuple(errors)


def _collect_role_errors(role: Role, errors: list[str]) -> None:
    source = f"role '{role.id}'"
    for ability in role.abilities:
        _collect_ability_errors(source, ability, errors)
    for passive in role.passives:
        _collect_passive_errors(source, passive, errors)


def _collect_ability_errors(source: str, ability: Ability, errors: list[str]) -> None:
    if ability.target.selector not in IMPLEMENTED_SELECTOR_IDS:
        errors.append(f"{source} ability '{ability.id}' uses unsupported selector '{ability.target.selector}'")
    for restriction in ability.restrictions:
        if restriction.type not in IMPLEMENTED_RESTRICTION_TYPE_IDS:
            errors.append(
                f"{source} ability '{ability.id}' uses unsupported restriction '{restriction.type}'"
            )
    for effect in ability.effects:
        if effect.id not in IMPLEMENTED_EFFECT_IDS:
            errors.append(f"{source} ability '{ability.id}' uses unsupported effect '{effect.id}'")


def _collect_passive_errors(source: str, passive: Passive, errors: list[str]) -> None:
    if passive.type not in IMPLEMENTED_PASSIVE_IDS:
        errors.append(f"{source} uses unsupported passive '{passive.type}'")
    for effect in passive.effects:
        if effect.id not in IMPLEMENTED_EFFECT_IDS:
            errors.append(f"{source} passive '{passive.type}' uses unsupported effect '{effect.id}'")
    for rule in passive.rules:
        target = rule.get("target")
        if not isinstance(target, Mapping):
            continue
        selector = target.get("selector")
        if isinstance(selector, str) and selector not in IMPLEMENTED_SELECTOR_IDS:
            errors.append(
                f"{source} passive '{passive.type}' uses unsupported selector '{selector}'"
            )
