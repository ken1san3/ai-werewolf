"""Shared player, target, and attribute helpers for core services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Sequence

from .models import Passive, PlayerRoleState, TargetSpec, resolve_effective_attributes
from .state import Player

if TYPE_CHECKING:
    from .game import GameState


def alive_player(game: GameState, player_id: str, description: str) -> Player:
    """Return a living player or raise a validation error with context."""

    try:
        player = game.players[player_id]
    except KeyError as error:
        raise ValueError(f"unknown {description} '{player_id}'") from error
    if not player.alive:
        raise ValueError(f"{description} '{player_id}' must be alive")
    return player


def effective_attributes(player: Player):
    """Resolve a player's role plus modifiers without leaking that detail to callers."""

    return resolve_effective_attributes(
        PlayerRoleState(player.player_id, player.role, player.modifiers)
    )


def passives_for(player: Player) -> tuple[Passive, ...]:
    """Return role and modifier passives in their declared order."""

    return tuple(player.role.passives) + tuple(
        passive for modifier in player.modifiers for passive in modifier.definition.passives
    )


def valid_target_ids(game: GameState, actor: Player, target: TargetSpec) -> tuple[str, ...]:
    """Resolve a content-declared target selector against authoritative state."""

    if target.selector == "alive_all":
        return tuple(player_id for player_id, player in game.players.items() if player.alive)
    if target.selector == "alive_other":
        return tuple(
            player_id
            for player_id, player in game.players.items()
            if player.alive and player_id != actor.player_id
        )
    if target.selector in {"alive_by_tag", "alive_without_tag"}:
        tag = target.options.get("tag")
        if not isinstance(tag, str):
            raise RuntimeError(f"selector '{target.selector}' requires a string tag")
        include_tag = target.selector == "alive_by_tag"
        return tuple(
            player_id
            for player_id, player in game.players.items()
            if player.alive and ((tag in player.role.tags) == include_tag)
        )
    if target.selector == "unexamined_dead_by_cause":
        causes = target.options.get("causes")
        if not isinstance(causes, Sequence) or isinstance(causes, str):
            raise RuntimeError("unexamined_dead_by_cause requires a causes sequence")
        allowed_causes = set(causes)
        return tuple(
            player_id
            for player_id, death in game.death_records.items()
            if death.cause in allowed_causes
            and (actor.player_id, player_id) not in game.medium_examined_deaths
        )
    raise RuntimeError(f"selector '{target.selector}' has no Phase 1.5 implementation")


def night_number(game: GameState) -> int | None:
    """Translate the state machine's night phases to their content night number."""

    if game.phase.value == "night0":
        return 0
    if game.phase.value == "night":
        return game.day
    return None
