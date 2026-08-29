"""Initial game state construction and role assignment for Phase 1.2."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from random import Random
from typing import MutableSequence, Protocol, Sequence

from .content import ContentPack, Preset
from .events import EventBus, EventVisibility, GameEvent, JsonlEventLog
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
    """Authoritative mutable state; phase transitions are added in Phase 1.3."""

    game_id: str
    content: ContentPack
    rules: RulesConfig
    players: dict[str, Player]
    rng: RandomSource
    event_bus: EventBus
    event_log: JsonlEventLog
    day: int = 0
    phase: GamePhase = GamePhase.SETUP
    pending_actions: dict[str, object] = field(default_factory=dict)

    @classmethod
    def create_from_preset(
        cls,
        content: ContentPack,
        preset: Preset,
        player_configs: Sequence[PlayerConfig],
        *,
        game_id: str,
        logs_root: str | Path,
        rng: RandomSource | None = None,
        role_missing_replacement_role_id: str | None = None,
    ) -> "GameState":
        """Build the Setup state and record all random assignment outcomes."""

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
        _role_missing_candidates(content, preset.rules, role_ids, role_missing_replacement_role_id)

        event_bus = EventBus()
        event_log = JsonlEventLog(logs_root, game_id)
        for visibility in EventVisibility:
            event_bus.subscribe(visibility, event_log.record)
        state = cls(
            game_id=game_id,
            content=content,
            rules=preset.rules,
            players={},
            rng=assigned_rng,
            event_bus=event_bus,
            event_log=event_log,
        )
        state._record_game_created(player_configs)
        state._apply_role_missing(role_ids, role_missing_replacement_role_id)
        state.rng.shuffle(role_ids)
        state._assign_roles(player_configs, role_ids)
        return state

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
        self, role_ids: list[str], replacement_role_id: str | None
    ) -> None:
        candidates = _role_missing_candidates(
            self.content, self.rules, role_ids, replacement_role_id
        )
        if not candidates or replacement_role_id is None:
            return
        missing_role_id = self.rng.choice(candidates)
        role_ids[role_ids.index(missing_role_id)] = replacement_role_id
        self.event_bus.publish(
            GameEvent(
                type="ROLE_MISSING_APPLIED",
                visibility=EventVisibility.PRIVATE,
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


def _role_missing_candidates(
    content: ContentPack,
    rules: RulesConfig,
    role_ids: Sequence[str],
    replacement_role_id: str | None,
) -> list[str]:
    if not rules.role_missing:
        return []
    if replacement_role_id is None:
        raise ValueError("role_missing requires an explicit replacement role until Q25 is decided")
    if replacement_role_id not in content.roles:
        raise ValueError(f"role_missing replacement role is not registered: '{replacement_role_id}'")
    candidates = [role_id for role_id in role_ids if role_id != replacement_role_id]
    if not candidates:
        raise ValueError("role_missing requires at least one non-replacement role card")
    return candidates
