"""Authoritative game state, phase progression, and role assignment."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from random import Random
from typing import Iterable, MutableSequence, Protocol, Sequence

from .content import ContentPack, Preset
from .events import (
    EventBus,
    EventSink,
    EventVisibility,
    GameEvent,
    InMemoryEventSink,
    JsonlEventLog,
)
from .models import AppliedModifier, Role, RulesConfig


class RandomSource(Protocol):
    """The game-owned source for every random selection."""

    def choice(self, sequence: Sequence[str]) -> str:
        ...

    def shuffle(self, sequence: MutableSequence[str]) -> None:
        ...


class GamePhase(str, Enum):
    SETUP = "setup"
    NIGHT0 = "night0"
    DAWN = "dawn"
    DAY = "day"
    VOTE = "vote"
    RUNOFF = "runoff"
    EXECUTION = "execution"
    NIGHT = "night"
    GAME_END = "game_end"


@dataclass(frozen=True)
class ActionSpec:
    """One currently available action without resolving its target or effect."""

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


@dataclass
class GameState:
    """Authoritative mutable state; action resolution is added in Phase 1.5."""

    game_id: str
    content: ContentPack
    rules: RulesConfig
    players: dict[str, Player]
    rng: RandomSource
    event_bus: EventBus
    event_sink: EventSink
    day: int = 0
    phase: GamePhase = GamePhase.SETUP
    phase_started_at: int | None = None
    phase_ends_at: int | None = None
    chat_enabled_at: int | None = None
    extensions_used: int = 0
    pending_actions: dict[str, object] = field(default_factory=dict)

    @classmethod
    def create_from_preset(
        cls,
        content: ContentPack,
        preset: Preset,
        player_configs: Sequence[PlayerConfig],
        *,
        game_id: str,
        logs_root: str | Path | None = None,
        rng: RandomSource | None = None,
        event_sink: EventSink | None = None,
        started_at: int = 0,
    ) -> "GameState":
        """Build the initial Night0 state and record all assignment outcomes."""

        if not game_id:
            raise ValueError("game_id must not be empty")
        player_ids = [player.player_id for player in player_configs]
        if len(player_ids) != len(set(player_ids)):
            raise ValueError("player_ids must be unique")
        if any(not player_id for player_id in player_ids):
            raise ValueError("player_id must not be empty")

        assigned_rng = rng if rng is not None else Random()
        role_ids = _role_cards(content, preset)
        if len(role_ids) != len(player_configs):
            raise ValueError(
                "preset role count must equal player count: "
                f"{len(role_ids)} roles for {len(player_configs)} players"
            )
        role_missing_candidates = _role_missing_candidates(preset.rules, role_ids)

        event_bus = EventBus()
        sink = event_sink
        if sink is None:
            sink = JsonlEventLog(logs_root, game_id) if logs_root is not None else InMemoryEventSink()
        for visibility in EventVisibility:
            event_bus.subscribe(visibility, sink.record)
        state = cls(
            game_id=game_id,
            content=content,
            rules=preset.rules,
            players={},
            rng=assigned_rng,
            event_bus=event_bus,
            event_sink=sink,
        )
        state._record_game_created(player_configs)
        state._apply_role_missing(role_ids, role_missing_candidates)
        state.rng.shuffle(role_ids)
        state._assign_roles(player_configs, role_ids)
        state.start(started_at)
        return state

    def start(self, now: int) -> GamePhase:
        """Enter the mandatory first-night phase at a caller-supplied logical time."""

        if self.phase is not GamePhase.SETUP:
            raise ValueError("only a setup game can be started")
        self._enter_phase(GamePhase.NIGHT0, now)
        return self.phase

    def advance_phase(
        self, now: int, *, vote_tied: bool = False, game_ended: bool = False
    ) -> GamePhase:
        """Advance one legal phase transition after its deadline or manual result.

        Vote aggregation and winner evaluation are deliberately outside this phase.
        Their future resolvers provide ``vote_tied`` and ``game_ended`` when they
        hand control back to the state machine.
        """

        now = _timestamp(now)
        if self.phase is GamePhase.GAME_END:
            raise ValueError("a finished game cannot advance")
        if self.phase_started_at is None or now < self.phase_started_at:
            raise ValueError("phase cannot advance before it starts")
        if self.phase_ends_at is not None and now < self.phase_ends_at:
            raise ValueError("phase cannot advance before its deadline")
        if vote_tied and self.phase is not GamePhase.VOTE:
            raise ValueError("vote_tied is only valid when leaving the vote phase")
        if game_ended and self.phase not in {GamePhase.EXECUTION, GamePhase.NIGHT}:
            raise ValueError("game_ended is only valid after death resolution")

        next_phase = self._next_phase(vote_tied=vote_tied, game_ended=game_ended)
        self._enter_phase(next_phase, now)
        return self.phase

    def advance_if_due(
        self, now: int, *, vote_tied: bool = False, game_ended: bool = False
    ) -> bool:
        """Advance a timed phase only once its authoritative deadline has passed."""

        now = _timestamp(now)
        if self.phase_ends_at is None or now < self.phase_ends_at:
            return False
        self.advance_phase(now, vote_tied=vote_tied, game_ended=game_ended)
        return True

    def approve_day_extension(self, now: int, approver_player_ids: Iterable[str]) -> bool:
        """Extend the current Day when the configured alive-player quorum approves."""

        now = _timestamp(now)
        if self.phase is not GamePhase.DAY:
            raise ValueError("time extensions are only available during the day phase")
        if self.phase_started_at is None or self.phase_ends_at is None:
            raise RuntimeError("the day phase must have a deadline")
        if now < self.phase_started_at:
            raise ValueError("time extension cannot be approved before the day starts")
        if now >= self.phase_ends_at or self.extensions_used >= self.rules.extension.max_count:
            return False

        approvers = tuple(approver_player_ids)
        if len(approvers) != len(set(approvers)):
            raise ValueError("extension approvers must be unique")
        alive_player_ids = {player_id for player_id, player in self.players.items() if player.alive}
        unknown = set(approvers) - alive_player_ids
        if unknown:
            raise ValueError("extension approvers must be alive players")

        required = _extension_approval_count(self.rules.extension.approval, len(alive_player_ids))
        if len(approvers) < required:
            return False

        self.extensions_used += 1
        self.phase_ends_at += self.rules.extension.seconds_per_extension
        self.event_bus.publish(
            GameEvent(
                type="DAY_EXTENDED",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "day": self.day,
                    "phase": self.phase.value,
                    "phase_ends_at": self.phase_ends_at,
                    "extensions_used": self.extensions_used,
                },
            )
        )
        return True

    def get_available_actions(self, player_id: str) -> tuple[ActionSpec, ...]:
        """List phase-legal actions from one player's permitted role declarations."""

        try:
            player = self.players[player_id]
        except KeyError as error:
            raise ValueError(f"unknown player '{player_id}'") from error
        if not player.alive:
            return ()

        actions: list[ActionSpec] = []
        if self.phase in {GamePhase.NIGHT0, GamePhase.NIGHT}:
            night_number = 0 if self.phase is GamePhase.NIGHT0 else self.day
            actions.extend(
                ActionSpec(type="ability", ability_id=ability.id)
                for ability in player.role.abilities
                if ability.timing == "night_action" and ability.available_from_night <= night_number
            )
            actions.extend(
                ActionSpec(type="chat", channel=channel)
                for channel in player.role.chat_channels
                if channel not in {"public", "system"}
            )
        elif self.phase is GamePhase.DAY and "public" in player.role.chat_channels:
            actions.append(ActionSpec(type="chat", channel="public"))
        return tuple(actions)

    def _next_phase(self, *, vote_tied: bool, game_ended: bool) -> GamePhase:
        if self.phase is GamePhase.SETUP:
            return GamePhase.NIGHT0
        if self.phase is GamePhase.NIGHT0:
            return GamePhase.DAWN
        if self.phase is GamePhase.DAWN:
            return GamePhase.DAY
        if self.phase is GamePhase.DAY:
            return GamePhase.VOTE
        if self.phase is GamePhase.VOTE:
            if vote_tied and self.rules.vote.runoff:
                return GamePhase.RUNOFF
            return GamePhase.EXECUTION
        if self.phase is GamePhase.RUNOFF:
            return GamePhase.EXECUTION
        if self.phase is GamePhase.EXECUTION:
            return GamePhase.GAME_END if game_ended else GamePhase.NIGHT
        if self.phase is GamePhase.NIGHT:
            return GamePhase.GAME_END if game_ended else GamePhase.DAWN
        raise RuntimeError(f"unsupported phase transition from '{self.phase.value}'")

    def _enter_phase(self, phase: GamePhase, now: int) -> None:
        now = _timestamp(now)
        if phase is GamePhase.DAWN:
            self.day += 1
        self.phase = phase
        self.phase_started_at = now
        duration = _phase_duration(phase, self.rules)
        self.phase_ends_at = now + duration if duration is not None else None
        self.chat_enabled_at = _chat_enabled_at(phase, now, duration)
        self.event_bus.publish(
            GameEvent(
                type="PHASE_STARTED",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "phase": self.phase.value,
                    "day": self.day,
                    "phase_ends_at": self.phase_ends_at,
                    "chat_enabled_at": self.chat_enabled_at,
                },
            )
        )

    def _record_game_created(self, player_configs: Sequence[PlayerConfig]) -> None:
        self.event_bus.publish(
            GameEvent(
                type="GAME_CREATED",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "game_id": self.game_id,
                    "day": self.day,
                    "phase": self.phase.value,
                    "players": [
                        {"player_id": player.player_id, "display_name": player.display_name}
                        for player in player_configs
                    ],
                },
            )
        )

    def _apply_role_missing(
        self, role_ids: list[str], candidates: Sequence[str]
    ) -> None:
        if not candidates:
            return
        missing_role_id = self.rng.choice(candidates)
        replacement_role_id = self.rules.role_missing.replacement_role_id
        role_ids[role_ids.index(missing_role_id)] = replacement_role_id
        self.event_bus.publish(
            GameEvent(
                type="ROLE_MISSING_APPLIED",
                visibility=EventVisibility.SERVER,
                payload={
                    "missing_role_id": missing_role_id,
                    "replacement_role_id": replacement_role_id,
                },
            )
        )

    def _assign_roles(self, player_configs: Sequence[PlayerConfig], role_ids: Sequence[str]) -> None:
        for player_config, role_id in zip(player_configs, role_ids):
            player = Player(
                player_id=player_config.player_id,
                display_name=player_config.display_name,
                role=self.content.roles[role_id],
            )
            self.players[player.player_id] = player
            self.event_bus.publish(
                GameEvent(
                    type="ROLE_ASSIGNED",
                    visibility=EventVisibility.PRIVATE,
                    recipient_player_id=player.player_id,
                    payload={"player_id": player.player_id, "role_id": role_id, "modifier_ids": []},
                )
            )


def _role_cards(content: ContentPack, preset: Preset) -> list[str]:
    role_ids: list[str] = []
    for role_id, count in preset.role_counts.items():
        if role_id not in content.roles:
            raise ValueError(f"preset references unregistered role '{role_id}'")
        role_ids.extend([role_id] * count)
    return role_ids


def _role_missing_candidates(rules: RulesConfig, role_ids: Sequence[str]) -> list[str]:
    if not rules.role_missing.enabled:
        return []
    candidates = [
        role_id for role_id in role_ids if role_id != rules.role_missing.replacement_role_id
    ]
    if not candidates:
        raise ValueError("role_missing requires at least one non-replacement role card")
    return candidates


def _timestamp(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("logical time must be an integer")
    return value


def _phase_duration(phase: GamePhase, rules: RulesConfig) -> int | None:
    if phase in {GamePhase.NIGHT0, GamePhase.NIGHT}:
        return rules.night_seconds
    if phase is GamePhase.DAWN:
        return rules.silence_after_dawn_seconds
    if phase is GamePhase.DAY:
        return rules.day_seconds
    return None


def _chat_enabled_at(phase: GamePhase, now: int, duration: int | None) -> int | None:
    if phase is GamePhase.DAY:
        return now
    if phase is GamePhase.DAWN:
        if duration is None:
            raise RuntimeError("the dawn phase must have a duration")
        return now + duration
    return None


def _extension_approval_count(approval: str, alive_player_count: int) -> int:
    if approval == "all":
        return alive_player_count
    if approval == "majority":
        return (alive_player_count // 2) + 1
    raise ValueError(f"unsupported extension approval rule '{approval}'")
