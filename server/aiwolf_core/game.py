"""Authoritative game state, phase progression, and role assignment."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from random import Random
from typing import Iterable, Mapping, MutableSequence, Protocol, Sequence

from .content import ContentPack, Preset
from .events import (
    EventBus,
    EventSink,
    EventVisibility,
    GameEvent,
    InMemoryEventSink,
    JsonlEventLog,
)
from .models import (
    Ability,
    AppliedModifier,
    CoreDeathCause,
    EffectReference,
    GamePhase,
    Passive,
    PlayerRoleState,
    Role,
    RulesConfig,
    TargetSpec,
    resolve_effective_attributes,
)


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
class _PhaseActionKind:
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


class _UnspecifiedLogsRoot:
    """Distinguish an omitted event destination from explicit in-memory logging."""


_UNSPECIFIED_LOGS_ROOT = _UnspecifiedLogsRoot()


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
class _ScheduledEffect:
    priority: int
    effect_id: str
    actor_player_id: str
    ability_id: str
    target_player_ids: tuple[str, ...]
    death_cause: str | None = None


@dataclass(frozen=True)
class _DeathRequest:
    player_id: str
    cause: str
    priority: int


@dataclass
class GameState:
    """Authoritative mutable state, including server-private night-action reservations."""

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

    def advance_phase(self, now: int, *, game_ended: bool = False) -> GamePhase:
        """Advance one legal phase transition after its deadline or manual result.

        Vote aggregation and winner evaluation are deliberately outside this phase.
        Vote / Runoff transitions belong to ``resolve_votes``; Phase 1.6 will
        provide ``game_ended`` when it hands control back after death resolution.
        """

        now = _timestamp(now)
        if self.phase is GamePhase.GAME_END:
            raise ValueError("a finished game cannot advance")
        if self.phase_started_at is None or now < self.phase_started_at:
            raise ValueError("phase cannot advance before it starts")
        if self.phase_ends_at is not None and now < self.phase_ends_at:
            raise ValueError("phase cannot advance before its deadline")
        if self.phase in {GamePhase.VOTE, GamePhase.RUNOFF}:
            raise ValueError("vote phases must advance through resolve_votes")
        if game_ended and self.phase not in {GamePhase.EXECUTION, GamePhase.NIGHT}:
            raise ValueError("game_ended is only valid after death resolution")
        if self.phase in {GamePhase.NIGHT0, GamePhase.NIGHT}:
            self.resolve_pending_actions(now)

        next_phase = self._next_phase(game_ended=game_ended)
        self._enter_phase(next_phase, now)
        return self.phase

    def advance_if_due(self, now: int, *, game_ended: bool = False) -> bool:
        """Advance only a deadline-driven phase once its authoritative time has passed."""

        now = _timestamp(now)
        if self.phase_ends_at is None:
            raise ValueError("advance_if_due requires a phase with a deadline")
        if now < self.phase_ends_at:
            return False
        self.advance_phase(now, game_ended=game_ended)
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

        if not self._has_alive_approval(approver_player_ids, self.rules.extension.approval):
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

    def approve_day_shortening(self, now: int, approver_player_ids: Iterable[str]) -> bool:
        """Set the Day deadline to now when the configured quorum approves shortening."""

        now = _timestamp(now)
        if self.phase is not GamePhase.DAY:
            raise ValueError("time shortening is only available during the day phase")
        if self.phase_started_at is None or self.phase_ends_at is None:
            raise RuntimeError("the day phase must have a deadline")
        if now < self.phase_started_at:
            raise ValueError("time shortening cannot be approved before the day starts")
        if now >= self.phase_ends_at or not self.rules.shortening.enabled:
            return False
        if not self._has_alive_approval(approver_player_ids, self.rules.shortening.approval):
            return False

        self.phase_ends_at = now
        self.event_bus.publish(
            GameEvent(
                type="DAY_SHORTENED",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "day": self.day,
                    "phase": self.phase.value,
                    "phase_ends_at": self.phase_ends_at,
                },
            )
        )
        return True

    def submit_action(
        self,
        now: int,
        actor_player_id: str,
        ability_id: str,
        target_player_ids: Sequence[str],
    ) -> None:
        """Reserve an ability without changing game state until the night resolves."""

        now = _timestamp(now)
        if self.phase not in {GamePhase.NIGHT0, GamePhase.NIGHT}:
            raise ValueError("abilities can only be submitted during a night phase")
        if self.phase_started_at is None or self.phase_ends_at is None:
            raise RuntimeError("night actions require an authoritative deadline")
        if now < self.phase_started_at:
            raise ValueError("abilities cannot be submitted before the phase starts")
        if now >= self.phase_ends_at:
            raise ValueError("abilities cannot be submitted after the deadline")
        if self.night_actions_resolved:
            raise ValueError("night actions have already been resolved")
        actor = self._alive_player(actor_player_id, "action actor")
        ability = self._ability_for(actor, ability_id)
        self._validate_ability_available(actor, ability)
        targets = self._validate_action_targets(actor, ability, target_player_ids)
        self._validate_action_restrictions(actor, ability, targets)

        reservation = ActionReservation(actor.player_id, ability.id, targets, now)
        self.pending_actions[actor.player_id] = reservation
        self.event_bus.publish(
            GameEvent(
                type="ACTION_SUBMITTED",
                visibility=EventVisibility.SERVER,
                payload={
                    "day": self.day,
                    "phase": self.phase.value,
                    "actor_player_id": actor.player_id,
                    "ability_id": ability.id,
                    "target_player_ids": list(targets),
                    "submitted_at": now,
                },
            )
        )

    def resolve_pending_actions(self, now: int) -> None:
        """Resolve the final reservation for each actor at a night deadline exactly once."""

        if self.phase not in {GamePhase.NIGHT0, GamePhase.NIGHT}:
            raise ValueError("actions can only be resolved during a night phase")
        now = _timestamp(now)
        if self.phase_started_at is None or self.phase_ends_at is None:
            raise RuntimeError("night actions require an authoritative deadline")
        if now < self.phase_ends_at:
            raise ValueError("actions cannot be resolved before their deadline")
        if self.night_actions_resolved:
            return

        reservations = list(self.pending_actions.values())
        reservations.extend(self._first_night_reservations(now))
        scheduled: list[_ScheduledEffect] = []
        for reservation in sorted(reservations, key=lambda item: item.actor_player_id):
            actor = self._alive_player(reservation.actor_player_id, "action actor")
            ability = self._ability_for(actor, reservation.ability_id)
            self._consume_resolved_ability(reservation)
            for effect in ability.effects:
                scheduled.append(
                    _ScheduledEffect(
                        priority=effect.priority,
                        effect_id=effect.id,
                        actor_player_id=reservation.actor_player_id,
                        ability_id=ability.id,
                        target_player_ids=reservation.target_player_ids,
                    )
                )
            self.event_bus.publish(
                GameEvent(
                    type="ACTION_RESOLVED",
                    visibility=EventVisibility.SERVER,
                    payload={
                        "day": self.day,
                        "phase": self.phase.value,
                        "actor_player_id": reservation.actor_player_id,
                        "ability_id": ability.id,
                        "target_player_ids": list(reservation.target_player_ids),
                    },
                )
            )

        protected_player_ids: set[str] = set()
        death_requests: dict[str, _DeathRequest] = {}
        attack_targets_by_actor: dict[str, set[str]] = {}
        pending_priorities = {70, *(effect.priority for effect in scheduled)}
        processed_priorities: set[int] = set()
        while pending_priorities:
            priority = min(pending_priorities)
            pending_priorities.remove(priority)
            processed_priorities.add(priority)
            effects = sorted(
                (effect for effect in scheduled if effect.priority == priority),
                key=lambda effect: (effect.effect_id, effect.actor_player_id, effect.ability_id),
            )
            for effect_id in sorted({effect.effect_id for effect in effects}):
                group = [effect for effect in effects if effect.effect_id == effect_id]
                if effect_id == "protect":
                    protected_player_ids.update(
                        target_player_id
                        for effect in group
                        for target_player_id in effect.target_player_ids
                    )
                elif effect_id == "inspect":
                    for effect in group:
                        self._resolve_inspect(effect, death_requests)
                elif effect_id == "medium_inspect":
                    for effect in group:
                        self._resolve_medium_inspect(effect)
                elif effect_id == "attack":
                    self._resolve_attack_effects(
                        group,
                        protected_player_ids,
                        death_requests,
                        attack_targets_by_actor,
                    )
                elif effect_id == "inspect_role":
                    for effect in group:
                        self._resolve_inspect_dead_role(effect, attack_targets_by_actor)
                elif effect_id == "kill":
                    for effect in group:
                        self._resolve_kill_effect(effect)
                else:
                    raise RuntimeError(f"effect '{effect_id}' has no Phase 1.5 implementation")

            if priority == 70:
                scheduled.extend(self._resolve_death_requests(death_requests))
                pending_priorities.update(
                    effect.priority
                    for effect in scheduled
                    if effect.priority not in processed_priorities
                )

        self.pending_actions.clear()
        self.night_actions_resolved = True

    def _first_night_reservations(self, now: int) -> list[ActionReservation]:
        if self.phase is not GamePhase.NIGHT0 or self.rules.first_night_seer != "random_white":
            return []

        reservations: list[ActionReservation] = []
        for actor in self.players.values():
            if not actor.alive:
                continue
            for ability in actor.role.abilities:
                if "inspect" not in {effect.id for effect in ability.effects}:
                    continue
                if ability.available_from_night > 0:
                    continue
                candidates = [
                    player_id
                    for player_id in self._valid_target_ids(actor, ability.target)
                    if self._effective_attributes(self.players[player_id]).inspect_result == "not_wolf"
                ]
                if not candidates:
                    continue
                target_player_id = self.rng.choice(candidates)
                reservations.append(
                    ActionReservation(actor.player_id, ability.id, (target_player_id,), now)
                )
                self.event_bus.publish(
                    GameEvent(
                        type="FIRST_NIGHT_INSPECT_TARGET_SELECTED",
                        visibility=EventVisibility.SERVER,
                        payload={
                            "actor_player_id": actor.player_id,
                            "target_player_id": target_player_id,
                        },
                    )
                )
        return reservations

    def _resolve_inspect(
        self, effect: _ScheduledEffect, death_requests: dict[str, _DeathRequest]
    ) -> None:
        for target_player_id in effect.target_player_ids:
            target = self._alive_player(target_player_id, "inspect target")
            self.event_bus.publish(
                GameEvent(
                    type="INSPECT_RESULT",
                    visibility=EventVisibility.PRIVATE,
                    recipient_player_id=effect.actor_player_id,
                    payload={
                        "target_player_id": target.player_id,
                        "result": self._effective_attributes(target).inspect_result,
                    },
                )
            )
            death_priority = self._on_inspected_kill_priority(target)
            if death_priority is not None:
                self._request_death(
                    death_requests,
                    target.player_id,
                    CoreDeathCause.CURSED.value,
                    death_priority,
                )

    def _resolve_medium_inspect(self, effect: _ScheduledEffect) -> None:
        for target_player_id in effect.target_player_ids:
            target = self.players[target_player_id]
            self.medium_examined_deaths.add((effect.actor_player_id, target_player_id))
            event = GameEvent(
                type="MEDIUM_RESULT",
                visibility=EventVisibility.PRIVATE,
                recipient_player_id=effect.actor_player_id,
                payload={
                    "target_player_id": target_player_id,
                    "result": self._effective_attributes(target).medium_result,
                },
            )
            if self.rules.medium.notify_timing == "night":
                self.event_bus.publish(event)
            else:
                self.queued_dawn_notifications.append(event)

    def _resolve_attack_effects(
        self,
        effects: Sequence[_ScheduledEffect],
        protected_player_ids: set[str],
        death_requests: dict[str, _DeathRequest],
        attack_targets_by_actor: dict[str, set[str]],
    ) -> None:
        direct_effects: list[_ScheduledEffect] = []
        group_effects: list[_ScheduledEffect] = []
        for effect in effects:
            actor = self.players[effect.actor_player_id]
            if len(effect.target_player_ids) > 1 or "werewolf" not in actor.role.tags:
                direct_effects.append(effect)
            else:
                group_effects.append(effect)

        for effect in direct_effects:
            for target_player_id in effect.target_player_ids:
                attack_targets_by_actor.setdefault(effect.actor_player_id, set()).add(target_player_id)
                self._request_attack(
                    target_player_id, effect.priority, protected_player_ids, death_requests
                )
        if not group_effects:
            return
        if self.rules.wolf_attack.target_decision != "majority":
            raise NotImplementedError(
                "wolf attack target_decision requires the Q28 decision before it can resolve"
            )
        tallies: dict[str, int] = {}
        for effect in group_effects:
            target_player_id = effect.target_player_ids[0]
            tallies[target_player_id] = tallies.get(target_player_id, 0) + 1
        highest = max(tallies.values())
        candidates = tuple(sorted(target for target, count in tallies.items() if count == highest))
        target_player_id = candidates[0] if len(candidates) == 1 else self.rng.choice(candidates)
        if len(candidates) > 1:
            self.event_bus.publish(
                GameEvent(
                    type="WOLF_ATTACK_TIE_RESOLVED_RANDOM",
                    visibility=EventVisibility.SERVER,
                    payload={
                        "candidate_player_ids": list(candidates),
                        "selected_player_id": target_player_id,
                    },
                )
            )
        self.event_bus.publish(
            GameEvent(
                type="WOLF_ATTACK_TARGET_RESOLVED",
                visibility=EventVisibility.SERVER,
                payload={
                    "target_player_id": target_player_id,
                    "tallies": dict(sorted(tallies.items())),
                },
            )
        )
        self._request_attack(
            target_player_id,
            group_effects[0].priority,
            protected_player_ids,
            death_requests,
        )
        for effect in group_effects:
            attack_targets_by_actor.setdefault(effect.actor_player_id, set()).add(target_player_id)

    def _request_attack(
        self,
        target_player_id: str,
        priority: int,
        protected_player_ids: set[str],
        death_requests: dict[str, _DeathRequest],
    ) -> None:
        target = self._alive_player(target_player_id, "attack target")
        if target.player_id in protected_player_ids:
            for protector_player_id, reservation in self.pending_actions.items():
                if target.player_id in reservation.target_player_ids:
                    ability = self._ability_for(self.players[protector_player_id], reservation.ability_id)
                    if "protect" in {effect.id for effect in ability.effects}:
                        self.event_bus.publish(
                            GameEvent(
                                type="GUARD_SUCCEEDED",
                                visibility=EventVisibility.PRIVATE,
                                recipient_player_id=protector_player_id,
                                payload={"target_player_id": target.player_id},
                            )
                        )
            return
        if self._effective_attributes(target).attack_result != "die":
            return
        self._request_death(death_requests, target.player_id, CoreDeathCause.ATTACKED.value, priority)

    def _resolve_inspect_dead_role(
        self, effect: _ScheduledEffect, attack_targets_by_actor: Mapping[str, set[str]]
    ) -> None:
        for target_player_id in sorted(
            attack_targets_by_actor.get(effect.actor_player_id, set(effect.target_player_ids))
        ):
            death = self.death_records.get(target_player_id)
            if death is None or death.cause != CoreDeathCause.ATTACKED.value:
                continue
            self.event_bus.publish(
                GameEvent(
                    type="INSPECT_DEAD_ROLE_RESULT",
                    visibility=EventVisibility.PRIVATE,
                    recipient_player_id=effect.actor_player_id,
                    payload={"target_player_id": target_player_id, "role_id": self.players[target_player_id].role.id},
                )
            )

    def _resolve_kill_effect(self, effect: _ScheduledEffect) -> None:
        if effect.death_cause is None:
            raise RuntimeError("a kill effect must declare its internal death cause")
        for target_player_id in effect.target_player_ids:
            if self.players[target_player_id].alive:
                self._record_player_death(target_player_id, effect.death_cause)

    def _request_death(
        self,
        requests: dict[str, _DeathRequest],
        player_id: str,
        cause: str,
        priority: int,
    ) -> None:
        previous = requests.get(player_id)
        if previous is None or priority < previous.priority:
            requests[player_id] = _DeathRequest(player_id, cause, priority)

    def _resolve_death_requests(
        self, requests: Mapping[str, _DeathRequest]
    ) -> list[_ScheduledEffect]:
        scheduled: list[_ScheduledEffect] = []
        for request in sorted(requests.values(), key=lambda item: (item.priority, item.player_id)):
            if self.players[request.player_id].alive:
                scheduled.extend(
                    self._record_player_death(
                        request.player_id, request.cause, collect_passive_effects=True
                    )
                )
        return scheduled

    def submit_vote(self, voter_player_id: str, target_player_id: str | None) -> None:
        """Reserve or replace one alive player's vote for the current vote round.

        Reservations stay server-private until ``resolve_votes`` is called at the
        authoritative vote deadline.  Every submission remains in the server
        event log even though only the last target participates in the tally.
        """

        if self.phase not in {GamePhase.VOTE, GamePhase.RUNOFF}:
            raise ValueError("votes can only be submitted during vote or runoff phases")
        voter = self._alive_player(voter_player_id, "voter")
        if target_player_id is None:
            self._validate_abstention(voter.player_id)
        else:
            target = self._alive_player(target_player_id, "vote target")
            if not self.rules.vote.self_vote and voter.player_id == target.player_id:
                raise ValueError("self-voting is disabled by the current rules")
            if (
                self.phase is GamePhase.RUNOFF
                and target.player_id not in self.runoff_candidate_player_ids
            ):
                raise ValueError("runoff votes must target a runoff candidate")

        self.pending_votes[voter.player_id] = target_player_id
        self.event_bus.publish(
            GameEvent(
                type="VOTE_SUBMITTED",
                visibility=EventVisibility.SERVER,
                payload={
                    "day": self.day,
                    "phase": self.phase.value,
                    "voter_player_id": voter.player_id,
                    "target_player_id": target_player_id,
                },
            )
        )
        if self.rules.vote.reveal == "live":
            self._record_live_vote_reveal(voter.player_id, target_player_id)

    def resolve_votes(self, now: int) -> VoteResult:
        """Confirm the current vote round and enter Runoff or Execution.

        ``now`` is the authoritative deadline time supplied by the caller.  Vote
        and Runoff use ``rules.vote_seconds``; their scheduler invokes this
        method rather than ``advance_if_due`` because it must also tally votes.
        """

        if self.phase not in {GamePhase.VOTE, GamePhase.RUNOFF}:
            raise ValueError("votes can only be resolved during vote or runoff phases")
        now = _timestamp(now)
        if self.phase_started_at is None or self.phase_ends_at is None:
            raise RuntimeError("vote phases must have an authoritative deadline")
        if now < self.phase_ends_at:
            raise ValueError("votes cannot be resolved before their deadline")
        if now < self.phase_started_at:
            raise ValueError("votes cannot be resolved before the phase starts")

        alive_player_ids = tuple(
            player_id for player_id, player in self.players.items() if player.alive
        )
        final_votes = {
            player_id: self.pending_votes[player_id]
            for player_id in alive_player_ids
            if player_id in self.pending_votes
        }
        self._consume_abstentions(final_votes)
        tallies = _vote_tallies(final_votes, alive_player_ids)
        if not alive_player_ids:
            result = VoteResult(kind=VoteResultKind.NO_LYNCH, tallies=tallies)
        else:
            result = self._resolve_vote_tally(tallies)

        self.last_vote_result = result
        if self.rules.vote.reveal == "after":
            self._record_after_vote_reveal(final_votes)
        self.pending_votes.clear()
        self._record_vote_result(result)
        if result.kind is VoteResultKind.RUNOFF:
            self.runoff_candidate_player_ids = result.runoff_candidate_player_ids
            self._enter_phase(GamePhase.RUNOFF, now)
            return result

        self.runoff_candidate_player_ids = ()
        # Execution is a day-classified death-resolution phase.  Enter it before
        # recording the lynch so public_death_cause derives the correct masking.
        self._enter_phase(GamePhase.EXECUTION, now)
        if result.kind is VoteResultKind.LYNCH:
            self._record_player_death(result.lynched_player_id, CoreDeathCause.LYNCHED.value)
        return result

    def _resolve_vote_tally(self, tallies: Mapping[str, int]) -> VoteResult:
        highest_vote_count = max(tallies.values())
        tied_player_ids = tuple(
            player_id for player_id, count in tallies.items() if count == highest_vote_count
        )
        if highest_vote_count == 0 and self.phase is GamePhase.VOTE:
            return self._resolve_tie(
                tied_player_ids, self.rules.vote.tie_without_runoff, tallies
            )
        if len(tied_player_ids) == 1:
            return VoteResult(
                kind=VoteResultKind.LYNCH,
                tallies=dict(tallies),
                lynched_player_id=tied_player_ids[0],
            )
        if self.phase is GamePhase.VOTE and self.rules.vote.runoff:
            return VoteResult(
                kind=VoteResultKind.RUNOFF,
                tallies=dict(tallies),
                runoff_candidate_player_ids=tied_player_ids,
            )

        tie_rule = (
            self.rules.vote.tie_after_runoff
            if self.phase is GamePhase.RUNOFF
            else self.rules.vote.tie_without_runoff
        )
        return self._resolve_tie(tied_player_ids, tie_rule, tallies)

    def _resolve_tie(
        self, tied_player_ids: tuple[str, ...], tie_rule: str, tallies: Mapping[str, int]
    ) -> VoteResult:
        if tie_rule == "no_lynch":
            return VoteResult(kind=VoteResultKind.NO_LYNCH, tallies=dict(tallies))
        selected_player_id = self.rng.choice(tied_player_ids)
        self.event_bus.publish(
            GameEvent(
                type="TIE_RESOLVED_RANDOM",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "day": self.day,
                    "phase": self.phase.value,
                    "candidate_player_ids": list(tied_player_ids),
                    "selected_player_id": selected_player_id,
                },
            )
        )
        return VoteResult(
            kind=VoteResultKind.LYNCH,
            tallies=dict(tallies),
            lynched_player_id=selected_player_id,
        )

    def _record_vote_result(self, result: VoteResult) -> None:
        self.event_bus.publish(
            GameEvent(
                type="VOTE_RESOLVED",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "day": self.day,
                    "phase": self.phase.value,
                    "result": result.kind.value,
                    "tallies": dict(result.tallies),
                    "lynched_player_id": result.lynched_player_id,
                    "runoff_candidate_player_ids": list(result.runoff_candidate_player_ids),
                },
            )
        )

    def _record_live_vote_reveal(self, voter_player_id: str, target_player_id: str | None) -> None:
        self.event_bus.publish(
            GameEvent(
                type="VOTE_REVEALED_LIVE",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "day": self.day,
                    "phase": self.phase.value,
                    "voter_player_id": voter_player_id,
                    "target_player_id": target_player_id,
                },
            )
        )

    def _record_after_vote_reveal(self, final_votes: Mapping[str, str | None]) -> None:
        self.event_bus.publish(
            GameEvent(
                type="VOTES_REVEALED_AFTER",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "day": self.day,
                    "phase": self.phase.value,
                    "votes": [
                        {"voter_player_id": voter_player_id, "target_player_id": target_player_id}
                        for voter_player_id, target_player_id in sorted(final_votes.items())
                    ],
                },
            )
        )

    def _record_player_death(
        self,
        player_id: str | None,
        internal_cause: str,
        *,
        collect_passive_effects: bool = False,
    ) -> list[_ScheduledEffect]:
        if player_id is None:
            raise RuntimeError("a death event must identify a player")
        player = self._alive_player(player_id, "dying player")
        if internal_cause not in self.content.death_causes:
            raise RuntimeError(f"content must register death cause '{internal_cause}'")
        phase_at_death = self.phase
        self.players[player.player_id] = replace(player, alive=False)
        self.death_records[player.player_id] = DeathRecord(
            player_id=player.player_id,
            cause=internal_cause,
            phase=phase_at_death,
            day=self.day,
        )
        self.event_bus.publish(
            GameEvent(
                type="PLAYER_DIED",
                visibility=EventVisibility.SERVER,
                payload={
                    "player_id": player.player_id,
                    "cause": internal_cause,
                    "day": self.day,
                    "phase": phase_at_death.value,
                },
            )
        )
        payload: dict[str, str | int] = {"player_id": player.player_id, "day": self.day}
        if self.rules.death.public_detail == "phase":
            payload["public_cause"] = public_death_cause(internal_cause, phase_at_death)
        elif self.rules.death.public_detail == "cause":
            payload["public_cause"] = internal_cause
        self.event_bus.publish(
            GameEvent(
                type="PLAYER_DIED",
                visibility=EventVisibility.PUBLIC,
                payload=payload,
            )
        )
        if collect_passive_effects:
            return self._scheduled_death_passive_effects(player, internal_cause)
        self._resolve_death_passives_immediately(player, internal_cause)
        return []

    def _validate_abstention(self, voter_player_id: str) -> None:
        abstain = self.rules.vote.abstain
        if not abstain.enabled:
            raise ValueError("abstaining is disabled by the current rules")
        if (
            abstain.max_per_player is not None
            and self.abstentions_used.get(voter_player_id, 0) >= abstain.max_per_player
        ):
            raise ValueError("the abstention limit has been reached")

    def _consume_abstentions(self, final_votes: Mapping[str, str | None]) -> None:
        for voter_player_id, target_player_id in final_votes.items():
            if target_player_id is None:
                self.abstentions_used[voter_player_id] = (
                    self.abstentions_used.get(voter_player_id, 0) + 1
                )

    def _ability_for(self, player: Player, ability_id: str) -> Ability:
        for ability in player.role.abilities:
            if ability.id == ability_id:
                return ability
        raise ValueError(f"player '{player.player_id}' does not have ability '{ability_id}'")

    def _validate_ability_available(self, actor: Player, ability: Ability) -> None:
        timing = self.content.action_timings[ability.timing]
        if self.phase.value not in timing.phases:
            raise ValueError(f"ability '{ability.id}' is unavailable during phase '{self.phase.value}'")
        night_number = _night_number(self.phase, self.day)
        if night_number is None or ability.available_from_night > night_number:
            raise ValueError(f"ability '{ability.id}' is unavailable on this night")
        if self.phase is GamePhase.NIGHT0 and "inspect" in {effect.id for effect in ability.effects}:
            if self.rules.first_night_seer != "free":
                raise ValueError("the first-night inspection is not player-selected by the current rules")
        key = (actor.player_id, ability.id)
        if ability.uses.per_game is not None and self.ability_uses_per_game.get(key, 0) >= ability.uses.per_game:
            raise ValueError(f"ability '{ability.id}' has no remaining game uses")
        if ability.uses.per_night is not None and self.ability_uses_this_night.get(key, 0) >= ability.uses.per_night:
            raise ValueError(f"ability '{ability.id}' has no remaining uses this night")

    def _validate_action_targets(
        self, actor: Player, ability: Ability, target_player_ids: Sequence[str]
    ) -> tuple[str, ...]:
        if isinstance(target_player_ids, str):
            raise TypeError("action targets must be a sequence of player ids")
        targets = tuple(target_player_ids)
        if any(not isinstance(player_id, str) for player_id in targets):
            raise TypeError("action target ids must be strings")
        if len(targets) != ability.target.count:
            raise ValueError(f"ability '{ability.id}' requires exactly {ability.target.count} target(s)")
        if len(targets) != len(set(targets)):
            raise ValueError("action targets must be unique")
        valid_target_ids = set(self._valid_target_ids(actor, ability.target))
        invalid = set(targets) - valid_target_ids
        if invalid:
            raise ValueError(
                f"ability '{ability.id}' has invalid target(s): {', '.join(sorted(invalid))}"
            )
        return targets

    def _validate_action_restrictions(
        self, actor: Player, ability: Ability, targets: tuple[str, ...]
    ) -> None:
        for restriction in ability.restrictions:
            if not self._enabled_when(restriction.enabled_when):
                continue
            if restriction.type == "no_same_target_consecutive":
                if self.last_resolved_targets.get((actor.player_id, ability.id)) == targets:
                    raise ValueError(f"ability '{ability.id}' cannot target the same player consecutively")
                continue
            raise RuntimeError(f"restriction '{restriction.type}' has no Phase 1.5 implementation")

    def _valid_target_ids(self, actor: Player, target: TargetSpec) -> tuple[str, ...]:
        if target.selector == "alive_all":
            return tuple(player_id for player_id, player in self.players.items() if player.alive)
        if target.selector == "alive_other":
            return tuple(
                player_id
                for player_id, player in self.players.items()
                if player.alive and player_id != actor.player_id
            )
        if target.selector in {"alive_by_tag", "alive_without_tag"}:
            tag = target.options.get("tag")
            if not isinstance(tag, str):
                raise RuntimeError(f"selector '{target.selector}' requires a string tag")
            include_tag = target.selector == "alive_by_tag"
            return tuple(
                player_id
                for player_id, player in self.players.items()
                if player.alive and ((tag in player.role.tags) == include_tag)
            )
        if target.selector == "unexamined_dead_by_cause":
            causes = target.options.get("causes")
            if not isinstance(causes, Sequence) or isinstance(causes, str):
                raise RuntimeError("unexamined_dead_by_cause requires a causes sequence")
            allowed_causes = set(causes)
            return tuple(
                player_id
                for player_id, death in self.death_records.items()
                if death.cause in allowed_causes
                and (actor.player_id, player_id) not in self.medium_examined_deaths
            )
        raise RuntimeError(f"selector '{target.selector}' has no Phase 1.5 implementation")

    def _enabled_when(self, expression: str | None) -> bool:
        if expression is None:
            return True
        path, expected = expression.removeprefix("rules.").split(" == ", maxsplit=1)
        value: object = self.rules
        for attribute in path.split("."):
            value = getattr(value, attribute)
        return value is (expected == "true")

    def _consume_resolved_ability(self, reservation: ActionReservation) -> None:
        player = self.players[reservation.actor_player_id]
        ability = self._ability_for(player, reservation.ability_id)
        key = (reservation.actor_player_id, reservation.ability_id)
        self.ability_uses_this_night[key] = self.ability_uses_this_night.get(key, 0) + 1
        if ability.uses.per_game is not None:
            self.ability_uses_per_game[key] = self.ability_uses_per_game.get(key, 0) + 1
        self.last_resolved_targets[key] = reservation.target_player_ids

    @staticmethod
    def _effective_attributes(player: Player):
        return resolve_effective_attributes(
            PlayerRoleState(player.player_id, player.role, player.modifiers)
        )

    @staticmethod
    def _passives_for(player: Player) -> tuple[Passive, ...]:
        return tuple(player.role.passives) + tuple(
            passive for modifier in player.modifiers for passive in modifier.definition.passives
        )

    def _on_inspected_kill_priority(self, player: Player) -> int | None:
        priorities = [
            effect.priority
            for passive in self._passives_for(player)
            if passive.type == "on_inspected"
            and any(rule.get("when", {}).get("event") == "inspected" for rule in passive.rules)
            for effect in passive.effects
            if effect.id == "kill"
        ]
        return min(priorities, default=None)

    def _scheduled_death_passive_effects(
        self, player: Player, internal_cause: str
    ) -> list[_ScheduledEffect]:
        scheduled: list[_ScheduledEffect] = []
        for passive in self._passives_for(player):
            if passive.type != "retaliate_on_death":
                continue
            for rule in passive.rules:
                if rule.get("when", {}).get("death_cause") != internal_cause:
                    continue
                target = rule.get("target")
                death_cause = rule.get("death_cause")
                if not isinstance(target, Mapping) or not isinstance(death_cause, str):
                    raise RuntimeError("retaliate_on_death requires target and death_cause declarations")
                target_spec = TargetSpec(
                    selector=target["selector"],
                    count=target["count"],
                    options={key: value for key, value in target.items() if key not in {"selector", "count"}},
                )
                candidates = list(self._valid_target_ids(player, target_spec))
                if len(candidates) < target_spec.count:
                    continue
                if target.get("pick") != "random":
                    raise RuntimeError("retaliate_on_death requires pick: random")
                selected: list[str] = []
                for _ in range(target_spec.count):
                    picked = self.rng.choice(candidates)
                    candidates.remove(picked)
                    selected.append(picked)
                self.event_bus.publish(
                    GameEvent(
                        type="PASSIVE_TARGET_SELECTED",
                        visibility=EventVisibility.SERVER,
                        payload={
                            "passive_type": passive.type,
                            "source_player_id": player.player_id,
                            "candidate_player_ids": list(self._valid_target_ids(player, target_spec)),
                            "selected_player_ids": selected,
                        },
                    )
                )
                for effect in passive.effects:
                    if effect.id != "kill":
                        raise RuntimeError(
                            f"passive effect '{effect.id}' has no Phase 1.5 implementation"
                        )
                    scheduled.append(
                        _ScheduledEffect(
                            priority=effect.priority,
                            effect_id=effect.id,
                            actor_player_id=player.player_id,
                            ability_id=f"passive:{passive.type}",
                            target_player_ids=tuple(selected),
                            death_cause=death_cause,
                        )
                    )
        return scheduled

    def _resolve_death_passives_immediately(self, player: Player, internal_cause: str) -> None:
        scheduled = self._scheduled_death_passive_effects(player, internal_cause)
        for effect in sorted(scheduled, key=lambda item: item.priority):
            self._resolve_kill_effect(effect)

    def _alive_player(self, player_id: str, description: str) -> Player:
        try:
            player = self.players[player_id]
        except KeyError as error:
            raise ValueError(f"unknown {description} '{player_id}'") from error
        if not player.alive:
            raise ValueError(f"{description} '{player_id}' must be alive")
        return player

    def _phase_action_kinds(self, player_id: str) -> tuple[_PhaseActionKind, ...]:
        """Return only the Phase 1.3 action kinds allowed by content declarations."""

        try:
            player = self.players[player_id]
        except KeyError as error:
            raise ValueError(f"unknown player '{player_id}'") from error
        if not player.alive:
            return ()

        phase_id = self.phase.value
        night_number = _night_number(self.phase, self.day)
        actions: list[_PhaseActionKind] = []
        for ability in player.role.abilities:
            timing = self.content.action_timings[ability.timing]
            if phase_id not in timing.phases:
                continue
            if night_number is not None and ability.available_from_night > night_number:
                continue
            actions.append(_PhaseActionKind(type="ability", ability_id=ability.id))
        for channel_id in player.role.chat_channels:
            channel = self.content.chat_channels[channel_id]
            if phase_id in channel.phases:
                actions.append(_PhaseActionKind(type="chat", channel=channel_id))
        return tuple(actions)

    def _has_alive_approval(self, approver_player_ids: Iterable[str], approval: str) -> bool:
        approvers = tuple(approver_player_ids)
        if len(approvers) != len(set(approvers)):
            raise ValueError("time-change approvers must be unique")
        alive_player_ids = {player_id for player_id, player in self.players.items() if player.alive}
        unknown = set(approvers) - alive_player_ids
        if unknown:
            raise ValueError("time-change approvers must be alive players")
        required = _extension_approval_count(approval, len(alive_player_ids))
        return len(approvers) >= required

    def _next_phase(self, *, game_ended: bool) -> GamePhase:
        if self.phase is GamePhase.SETUP:
            return GamePhase.NIGHT0
        if self.phase is GamePhase.NIGHT0:
            return GamePhase.DAWN
        if self.phase is GamePhase.DAWN:
            return GamePhase.DAY
        if self.phase is GamePhase.DAY:
            return GamePhase.VOTE
        if self.phase in {GamePhase.VOTE, GamePhase.RUNOFF}:
            raise RuntimeError("vote phases must transition through resolve_votes")
        if self.phase is GamePhase.EXECUTION:
            return GamePhase.GAME_END if game_ended else GamePhase.NIGHT
        if self.phase is GamePhase.NIGHT:
            return GamePhase.GAME_END if game_ended else GamePhase.DAWN
        raise RuntimeError(f"unsupported phase transition from '{self.phase.value}'")

    def _enter_phase(self, phase: GamePhase, now: int) -> None:
        now = _timestamp(now)
        if phase is GamePhase.DAWN:
            self.day += 1
        if phase in {GamePhase.NIGHT0, GamePhase.NIGHT}:
            self.pending_actions.clear()
            self.ability_uses_this_night.clear()
            self.night_actions_resolved = False
        if phase is GamePhase.VOTE:
            self.pending_votes.clear()
            self.runoff_candidate_player_ids = ()
        elif phase is GamePhase.RUNOFF:
            self.pending_votes.clear()
        self.phase = phase
        self.phase_started_at = now
        duration = _phase_duration(phase, self.rules)
        self.phase_ends_at = now + duration if duration is not None else None
        self.event_bus.publish(
            GameEvent(
                type="PHASE_STARTED",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "phase": self.phase.value,
                    "day": self.day,
                    "phase_ends_at": self.phase_ends_at,
                },
            )
        )
        if phase is GamePhase.DAWN:
            for event in self.queued_dawn_notifications:
                self.event_bus.publish(event)
            self.queued_dawn_notifications.clear()

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


def _vote_tallies(
    pending_votes: Mapping[str, str | None], alive_player_ids: Sequence[str]
) -> dict[str, int]:
    """Count final reservations, including every alive player with a zero tally."""

    alive_player_id_set = set(alive_player_ids)
    tallies = {player_id: 0 for player_id in sorted(alive_player_ids)}
    for voter_player_id in alive_player_ids:
        target_player_id = pending_votes.get(voter_player_id)
        if target_player_id not in alive_player_id_set:
            continue
        tallies[target_player_id] += 1
    return tallies


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
    if phase in {GamePhase.VOTE, GamePhase.RUNOFF}:
        return rules.vote_seconds
    return None


def public_death_cause(internal_cause: str, phase: GamePhase) -> str:
    """Mask an internal cause using the fixed daytime/nighttime phase classification."""

    if internal_cause == CoreDeathCause.LYNCHED.value:
        return CoreDeathCause.LYNCHED.value
    if phase in {
        GamePhase.DAWN,
        GamePhase.DAY,
        GamePhase.VOTE,
        GamePhase.RUNOFF,
        GamePhase.EXECUTION,
    }:
        return "died_in_day"
    if phase in {GamePhase.NIGHT0, GamePhase.NIGHT}:
        return "died_in_night"
    raise ValueError(f"phase '{phase.value}' cannot publish a death cause")


def _night_number(phase: GamePhase, day: int) -> int | None:
    if phase is GamePhase.NIGHT0:
        return 0
    if phase is GamePhase.NIGHT:
        return day
    return None


def _extension_approval_count(approval: str, alive_player_count: int) -> int:
    if approval == "all":
        return alive_player_count
    if approval == "majority":
        return (alive_player_count // 2) + 1
    raise ValueError(f"unsupported extension approval rule '{approval}'")
