"""Shared authoritative validation and enumeration for role abilities."""

from __future__ import annotations

from itertools import combinations
from typing import TYPE_CHECKING, Sequence

from .models import Ability, GamePhase
from .rejections import ActionRejected
from .targets import night_number, valid_target_ids

if TYPE_CHECKING:
    from .game import GameState
    from .state import Player


ACTION_RESTRICTION_DISPATCH_IDS = frozenset(
    {"no_same_target_consecutive", "no_self_target"}
)


class ActionConstraints:
    """Evaluate one content-declared Ability against authoritative game state."""

    def __init__(self, game: GameState) -> None:
        self.game = game

    @staticmethod
    def ability_for(player: Player, ability_id: str) -> Ability:
        for ability in player.role.abilities:
            if ability.id == ability_id:
                return ability
        raise ActionRejected(
            "unknown_ability", f"player '{player.player_id}' does not have ability '{ability_id}'"
        )

    def validate_ability_available(self, actor: Player, ability: Ability) -> None:
        self.validate_ability_timing(actor, ability)
        self.validate_ability_uses(actor, ability)

    def validate_ability_timing(self, actor: Player, ability: Ability) -> None:
        timing = self.game.content.action_timings[ability.timing]
        if self.game.phase.value not in timing.phases:
            raise ActionRejected(
                "action_unavailable",
                f"ability '{ability.id}' is unavailable during phase '{self.game.phase.value}'"
            )
        current_night = night_number(self.game)
        if current_night is None or ability.available_from_night > current_night:
            raise ActionRejected(
                "action_unavailable", f"ability '{ability.id}' is unavailable on this night"
            )
        if self.game.phase is GamePhase.NIGHT0 and "inspect" in {effect.id for effect in ability.effects}:
            if self.game.rules.first_night_seer != "free":
                raise ActionRejected(
                    "action_unavailable",
                    "the first-night inspection is not player-selected by the current rules",
                )

    def validate_ability_uses(self, actor: Player, ability: Ability) -> None:
        if self.uses_remaining(actor, ability) == 0:
            key = (actor.player_id, ability.id)
            if (
                ability.uses.per_game is not None
                and self.game.ability_uses_per_game.get(key, 0) >= ability.uses.per_game
            ):
                raise ActionRejected(
                    "ability_uses_exhausted", f"ability '{ability.id}' has no remaining game uses"
                )
            raise ActionRejected(
                "ability_uses_exhausted",
                f"ability '{ability.id}' has no remaining uses this night",
            )

    def uses_remaining(self, actor: Player, ability: Ability) -> int | None:
        """Return the limiting remaining use count, or None when unlimited."""

        key = (actor.player_id, ability.id)
        remaining = []
        if ability.uses.per_game is not None:
            remaining.append(ability.uses.per_game - self.game.ability_uses_per_game.get(key, 0))
        if ability.uses.per_night is not None:
            remaining.append(
                ability.uses.per_night - self.game.ability_uses_this_night.get(key, 0)
            )
        return min(remaining) if remaining else None

    def validate_targets(
        self, actor: Player, ability: Ability, target_player_ids: Sequence[str]
    ) -> tuple[str, ...]:
        if isinstance(target_player_ids, str):
            raise TypeError("action targets must be a sequence of player ids")
        targets = tuple(target_player_ids)
        if any(not isinstance(player_id, str) for player_id in targets):
            raise TypeError("action target ids must be strings")
        if len(targets) != ability.target.count:
            raise ActionRejected(
                "invalid_target", f"ability '{ability.id}' requires exactly {ability.target.count} target(s)"
            )
        if len(targets) != len(set(targets)):
            raise ActionRejected("invalid_target", "action targets must be unique")
        invalid = set(targets) - set(valid_target_ids(self.game, actor, ability.target))
        if invalid:
            raise ActionRejected(
                "invalid_target",
                f"ability '{ability.id}' has invalid target(s): {', '.join(sorted(invalid))}"
            )
        return targets

    def validate_restrictions(self, actor: Player, ability: Ability, targets: tuple[str, ...]) -> None:
        for restriction in ability.restrictions:
            if restriction.type not in ACTION_RESTRICTION_DISPATCH_IDS:
                raise RuntimeError(
                    f"restriction '{restriction.type}' has no Phase 1.5 implementation"
                )
            if not self.enabled_when(restriction.enabled_when):
                continue
            if restriction.type == "no_same_target_consecutive":
                if self.game.last_resolved_targets.get((actor.player_id, ability.id)) == targets:
                    raise ActionRejected(
                        "invalid_target",
                        f"ability '{ability.id}' cannot target the same player consecutively",
                    )
                continue
            if restriction.type == "no_self_target":
                if actor.player_id in targets:
                    raise ActionRejected(
                        "invalid_target", f"ability '{ability.id}' cannot target the actor"
                    )
                continue
            raise RuntimeError(f"restriction '{restriction.type}' has no Phase 1.5 implementation")

    def valid_target_sets(self, actor: Player, ability: Ability) -> tuple[tuple[str, ...], ...]:
        """Return target combinations accepted by the same restriction checks as submission."""

        options: list[tuple[str, ...]] = []
        for targets in combinations(valid_target_ids(self.game, actor, ability.target), ability.target.count):
            try:
                self.validate_restrictions(actor, ability, targets)
            except ValueError:
                continue
            options.append(targets)
        return tuple(options)

    def available_target_ids(self, actor: Player, ability: Ability) -> tuple[str, ...]:
        """Project accepted target combinations to target ids in selector order."""

        valid_sets = self.valid_target_sets(actor, ability)
        valid_ids = {player_id for targets in valid_sets for player_id in targets}
        return tuple(
            player_id
            for player_id in valid_target_ids(self.game, actor, ability.target)
            if player_id in valid_ids
        )

    def enabled_when(self, expression: str | None) -> bool:
        if expression is None:
            return True
        path, expected = expression.removeprefix("rules.").split(" == ", maxsplit=1)
        value: object = self.game.rules
        for attribute in path.split("."):
            value = getattr(value, attribute)
        return value is (expected == "true")
