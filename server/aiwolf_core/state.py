"""State records shared by the authoritative core services.

This module intentionally contains no game-flow logic.  Keeping these records
separate lets phase, voting, action, and death services operate on one
``GameState`` without importing each other through ``game.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, MutableSequence, Protocol, Sequence

from .models import AppliedModifier, GamePhase, Role


class RandomSource(Protocol):
    """The game-owned source for every random selection."""

    def choice(self, sequence: Sequence[str]) -> str:
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
class PhaseActionKind:
    """Phase-only action information, before Phase 1.7 builds ActionSpec."""

    type: str
    ability_id: str | None = None
    channel: str | None = None

    def __post_init__(self) -> None:
        if self.type == "ability" and self.ability_id and self.channel is None:
            return
        if self.type == "chat" and self.channel and self.ability_id is None:
            return
        raise ValueError("an action must be either an ability or a chat action")


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
