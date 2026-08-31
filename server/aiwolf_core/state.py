"""State records shared by the authoritative core services.

This module intentionally contains no game-flow logic.  Keeping these records
separate lets phase, voting, action, and death services operate on one
``GameState`` without importing each other through ``game.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, MutableSequence, Protocol, Sequence, TypeVar

from .models import AppliedModifier, GamePhase, Role


RandomChoice = TypeVar("RandomChoice")


class RandomSource(Protocol):
    """The game-owned source for every random selection."""

    def choice(self, sequence: Sequence[RandomChoice]) -> RandomChoice:
        ...

    def shuffle(self, sequence: MutableSequence[str]) -> None:
        ...


class VoteResultKind(str, Enum):
    """The three possible outcomes of a completed vote round."""

    LYNCH = "lynch"
    NO_LYNCH = "no_lynch"
    RUNOFF = "runoff"


@dataclass(frozen=True)
class VoteResult:
    """Resolved votes, without exposing a voter-to-target mapping publicly."""

    kind: VoteResultKind
    tallies: Mapping[str, int]
    lynched_player_id: str | None = None
    runoff_candidate_player_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, VoteResultKind):
            raise TypeError("vote result kind must be a VoteResultKind")
        if self.kind is VoteResultKind.LYNCH:
            if not self.lynched_player_id or self.runoff_candidate_player_ids:
                raise ValueError("a lynch result requires exactly one lynched player")
            return
        if self.kind is VoteResultKind.NO_LYNCH:
            if self.lynched_player_id is not None or self.runoff_candidate_player_ids:
                raise ValueError("a no-lynch result cannot have candidates")
            return
        if self.kind is VoteResultKind.RUNOFF:
            if self.lynched_player_id is not None or len(self.runoff_candidate_player_ids) < 2:
                raise ValueError("a runoff result requires at least two candidates")
            return
        raise ValueError(f"unsupported vote result kind '{self.kind}'")


@dataclass(frozen=True)
class GameResult:
    """The terminal outcome, including every player's final result."""

    winner_team: str | None
    outcome: str
    player_results: Mapping[str, str]

    def __post_init__(self) -> None:
        if self.outcome not in {"team_victory", "draw"}:
            raise ValueError(f"unsupported game outcome '{self.outcome}'")
        if self.outcome == "draw":
            if self.winner_team is not None:
                raise ValueError("a draw cannot have a winner team")
            if any(result != "lost" for result in self.player_results.values()):
                raise ValueError("every player must lose in a draw")
            return
        if not self.winner_team:
            raise ValueError("a team victory requires a winner team")
        if any(result not in {"won", "lost"} for result in self.player_results.values()):
            raise ValueError("player results must be won or lost")


@dataclass(frozen=True)
class ActionSpec:
    """One player-visible action with only the information needed to select it."""

    type: str
    ability_id: str | None = None
    channel: str | None = None
    description: str | None = None
    valid_targets: tuple[str, ...] = ()
    claimed_role_ids: tuple[str, ...] = ()
    target_count: int | None = None
    uses_remaining: int | None = None
    allows_abstain: bool = False

    def __post_init__(self) -> None:
        if len(self.valid_targets) != len(set(self.valid_targets)) or any(
            not player_id for player_id in self.valid_targets
        ):
            raise ValueError("valid targets must be unique non-empty player ids")
        if len(self.claimed_role_ids) != len(set(self.claimed_role_ids)) or any(
            not role_id for role_id in self.claimed_role_ids
        ):
            raise ValueError("claimed role ids must be unique non-empty role ids")
        if self.type == "ability":
            if (
                not self.ability_id
                or self.channel is not None
                or self.claimed_role_ids
                or self.target_count is None
                or self.target_count < 1
                or self.uses_remaining is not None
                and self.uses_remaining < 0
                or self.allows_abstain
            ):
                raise ValueError("an ability action requires valid ability fields")
            return
        if self.type == "chat":
            if (
                self.channel
                and not self.ability_id
                and not self.valid_targets
                and not self.claimed_role_ids
                and self.target_count is None
            ):
                return
            raise ValueError("a chat action requires only a channel")
        if self.type == "vote":
            if (
                self.ability_id is None
                and self.channel is None
                and self.description is None
                and not self.claimed_role_ids
                and self.target_count == 1
                and self.uses_remaining is None
            ):
                return
            raise ValueError("a vote action requires vote target fields")
        if self.type == "co_declare":
            if (
                self.ability_id is None
                and self.channel is None
                and self.description is None
                and not self.valid_targets
                and self.claimed_role_ids
                and self.target_count is None
                and self.uses_remaining is None
                and not self.allows_abstain
            ):
                return
        if self.type == "co_report":
            if (
                self.ability_id is None
                and self.channel is None
                and self.description is None
                and not self.valid_targets
                and not self.claimed_role_ids
                and self.target_count is None
                and self.uses_remaining is None
                and not self.allows_abstain
            ):
                return
        raise ValueError(f"unsupported action type '{self.type}'")


@dataclass(frozen=True)
class PlayerConfig:
    player_id: str
    display_name: str


@dataclass(frozen=True)
class Player:
    player_id: str
    display_name: str
    role: Role
    modifiers: tuple[AppliedModifier, ...] = ()
    alive: bool = True


@dataclass(frozen=True)
class ActionReservation:
    """One server-private ability reservation, replaced by the actor's next one."""

    actor_player_id: str
    ability_id: str
    target_player_ids: tuple[str, ...]
    submitted_at: int


@dataclass(frozen=True)
class DeathRecord:
    """Server-side death data used by effects and passives, never sent as-is to clients."""

    player_id: str
    cause: str
    phase: GamePhase
    day: int


@dataclass(frozen=True)
class ScheduledEffect:
    priority: int
    effect_id: str
    actor_player_id: str
    ability_id: str
    target_player_ids: tuple[str, ...]
    death_cause: str | None = None


@dataclass(frozen=True)
class DeathRequest:
    player_id: str
    cause: str
    priority: int
