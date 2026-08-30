"""Authoritative game state and compatibility entry points for core services."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import Iterable, Sequence

from .actions import ActionResolver
from .available_actions import ActionAvailability
from .content import ContentPack, Preset
from .death import DeathResolver, public_death_cause
from .events import EventBus, EventSink, EventVisibility, GameEvent, InMemoryEventSink, JsonlEventLog
from .models import GamePhase, RulesConfig
from .phase import PhaseManager
from .state import (
    ActionSpec,
    ActionReservation,
    DeathRecord,
    GameResult,
    Player,
    PlayerConfig,
    RandomSource,
    VoteResult,
    VoteResultKind,
)
from .voting import VoteResolver


class _UnspecifiedLogsRoot:
    """Distinguish an omitted event destination from explicit in-memory logging."""


_UNSPECIFIED_LOGS_ROOT = _UnspecifiedLogsRoot()


@dataclass
class GameState:
    """Authoritative mutable state shared by focused game-core services.

    ``PhaseManager``, ``VoteResolver``, ``ActionResolver``, and
    ``DeathResolver`` own transitions and resolution. The forwarding methods
    below preserve the stable core API while keeping their logic out of this
    state container.
    """

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
    extensions_used: int = 0
    pending_actions: dict[str, ActionReservation] = field(default_factory=dict)
    ability_uses_per_game: dict[tuple[str, str], int] = field(default_factory=dict)
    ability_uses_this_night: dict[tuple[str, str], int] = field(default_factory=dict)
    last_resolved_targets: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    death_records: dict[str, DeathRecord] = field(default_factory=dict)
    medium_examined_deaths: set[tuple[str, str]] = field(default_factory=set)
    queued_dawn_notifications: list[GameEvent] = field(default_factory=list)
    night_actions_resolved: bool = False
    pending_votes: dict[str, str | None] = field(default_factory=dict)
    abstentions_used: dict[str, int] = field(default_factory=dict)
    runoff_candidate_player_ids: tuple[str, ...] = ()
    last_vote_result: VoteResult | None = None
    game_result: GameResult | None = None

    @classmethod
    def create_from_preset(
        cls,
        content: ContentPack,
        preset: Preset,
        player_configs: Sequence[PlayerConfig],
        *,
        game_id: str,
        logs_root: str | Path | None | _UnspecifiedLogsRoot = _UNSPECIFIED_LOGS_ROOT,
        rng: RandomSource | None = None,
        event_sink: EventSink | None = None,
        started_at: int = 0,
    ) -> "GameState":
        """Build initial state, record assignments, and enter Night0."""

        if not game_id:
            raise ValueError("game_id must not be empty")
        player_ids = [player.player_id for player in player_configs]
        if len(player_ids) != len(set(player_ids)):
            raise ValueError("player_ids must be unique")
        if any(not player_id for player_id in player_ids):
            raise ValueError("player_id must not be empty")
        role_ids = _role_cards(content, preset)
        if len(role_ids) != len(player_configs):
            raise ValueError(
                "preset role count must equal player count: "
                f"{len(role_ids)} roles for {len(player_configs)} players"
            )

        event_bus = EventBus()
        if event_sink is not None:
            if logs_root is not _UNSPECIFIED_LOGS_ROOT and logs_root is not None:
                raise ValueError("logs_root and event_sink cannot both be provided")
            sink = event_sink
        elif logs_root is _UNSPECIFIED_LOGS_ROOT:
            raise ValueError("an event_sink or explicit logs_root must be provided")
        elif logs_root is None:
            sink = InMemoryEventSink()
        else:
            sink = JsonlEventLog(logs_root, game_id)
        for visibility in EventVisibility:
            event_bus.subscribe(visibility, sink.record)

        state = cls(
            game_id=game_id,
            content=content,
            rules=preset.rules,
            players={},
            rng=rng if rng is not None else Random(),
            event_bus=event_bus,
            event_sink=sink,
        )
        state._record_game_created(player_configs)
        state._apply_role_missing(role_ids, _role_missing_candidates(preset.rules, role_ids))
        state.rng.shuffle(role_ids)
        state._assign_roles(player_configs, role_ids)
        state.start(started_at)
        return state

    # Stable core API: each operation is implemented by a dedicated service.
    def start(self, now: int) -> GamePhase:
        return PhaseManager(self).start(now)

    def advance_phase(self, now: int, *, game_ended: bool = False) -> GamePhase:
        return PhaseManager(self).advance(now, game_ended=game_ended)

    def advance_if_due(self, now: int, *, game_ended: bool = False) -> bool:
        return PhaseManager(self).advance_if_due(now, game_ended=game_ended)

    def approve_day_extension(self, now: int, approver_player_ids: Iterable[str]) -> bool:
        return PhaseManager(self).approve_extension(now, approver_player_ids)

    def approve_day_shortening(self, now: int, approver_player_ids: Iterable[str]) -> bool:
        return PhaseManager(self).approve_shortening(now, approver_player_ids)

    def submit_action(
        self,
        now: int,
        actor_player_id: str,
        ability_id: str,
        target_player_ids: Sequence[str],
    ) -> None:
        ActionResolver(self).submit(now, actor_player_id, ability_id, target_player_ids)

    def resolve_pending_actions(self, now: int) -> None:
        ActionResolver(self).resolve(now)

    def get_available_actions(self, player_id: str) -> list[ActionSpec]:
        """Return only the action choices the named player may currently know about."""

        return ActionAvailability(self).get(player_id)

    def submit_vote(self, voter_player_id: str, target_player_id: str | None) -> None:
        VoteResolver(self).submit(voter_player_id, target_player_id)

    def resolve_votes(self, now: int) -> VoteResult:
        return VoteResolver(self).resolve(now)

    # Temporary private compatibility helpers used by the Phase 1 test surface.
    def _enter_phase(self, phase: GamePhase, now: int) -> None:
        PhaseManager(self).enter(phase, now)

    def _record_player_death(
        self,
        player_id: str | None,
        internal_cause: str,
        *,
        collect_passive_effects: bool = False,
    ):
        return DeathResolver(self).record(
            player_id, internal_cause, collect_passive_effects=collect_passive_effects
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

    def _apply_role_missing(self, role_ids: list[str], candidates: Sequence[str]) -> None:
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
