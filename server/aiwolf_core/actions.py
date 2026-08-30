"""Night-action reservation, validation, and effect resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Sequence

from .capabilities import (
    IMPLEMENTED_EFFECT_IDS,
    IMPLEMENTED_RESTRICTION_TYPE_IDS,
)
from .clock import timestamp
from .death import DeathResolver
from .events import EventVisibility, GameEvent
from .models import Ability, CoreDeathCause, GamePhase, TargetSpec
from .state import ActionReservation, DeathRequest, PhaseActionKind, ScheduledEffect
from .targets import alive_player, effective_attributes, night_number, valid_target_ids

if TYPE_CHECKING:
    from .game import GameState
    from .state import Player


class ActionResolver:
    """Own all mutation caused by submitted night abilities."""

    def __init__(self, game: GameState) -> None:
        self.game = game
        self.deaths = DeathResolver(game)

    def submit(
        self,
        now: int,
        actor_player_id: str,
        ability_id: str,
        target_player_ids: Sequence[str],
    ) -> None:
        """Reserve an ability without changing state until the night resolves."""

        now = timestamp(now)
        if self.game.phase not in {GamePhase.NIGHT0, GamePhase.NIGHT}:
            raise ValueError("abilities can only be submitted during a night phase")
        if self.game.phase_started_at is None or self.game.phase_ends_at is None:
            raise RuntimeError("night actions require an authoritative deadline")
        if now < self.game.phase_started_at:
            raise ValueError("abilities cannot be submitted before the phase starts")
        if now >= self.game.phase_ends_at:
            raise ValueError("abilities cannot be submitted after the deadline")
        if self.game.night_actions_resolved:
            raise ValueError("night actions have already been resolved")
        actor = alive_player(self.game, actor_player_id, "action actor")
        ability = self.ability_for(actor, ability_id)
        self.validate_ability_available(actor, ability)
        targets = self.validate_targets(actor, ability, target_player_ids)
        self.validate_restrictions(actor, ability, targets)

        reservation = ActionReservation(actor.player_id, ability.id, targets, now)
        self.game.pending_actions[actor.player_id] = reservation
        self.game.event_bus.publish(
            GameEvent(
                type="ACTION_SUBMITTED",
                visibility=EventVisibility.SERVER,
                payload={
                    "day": self.game.day,
                    "phase": self.game.phase.value,
                    "actor_player_id": actor.player_id,
                    "ability_id": ability.id,
                    "target_player_ids": list(targets),
                    "submitted_at": now,
                },
            )
        )

    def resolve(self, now: int) -> None:
        """Resolve final reservations at a night deadline exactly once."""

        if self.game.phase not in {GamePhase.NIGHT0, GamePhase.NIGHT}:
            raise ValueError("actions can only be resolved during a night phase")
        now = timestamp(now)
        if self.game.phase_started_at is None or self.game.phase_ends_at is None:
            raise RuntimeError("night actions require an authoritative deadline")
        if now < self.game.phase_ends_at:
            raise ValueError("actions cannot be resolved before their deadline")
        if self.game.night_actions_resolved:
            return

        reservations = list(self.game.pending_actions.values())
        reservations.extend(self.first_night_reservations(now))
        scheduled: list[ScheduledEffect] = []
        for reservation in sorted(reservations, key=lambda item: item.actor_player_id):
            actor = alive_player(self.game, reservation.actor_player_id, "action actor")
            ability = self.ability_for(actor, reservation.ability_id)
            self.consume_resolved_ability(reservation)
            for effect in ability.effects:
                scheduled.append(
                    ScheduledEffect(
                        priority=effect.priority,
                        effect_id=effect.id,
                        actor_player_id=reservation.actor_player_id,
                        ability_id=ability.id,
                        target_player_ids=reservation.target_player_ids,
                    )
                )
            self.game.event_bus.publish(
                GameEvent(
                    type="ACTION_RESOLVED",
                    visibility=EventVisibility.SERVER,
                    payload={
                        "day": self.game.day,
                        "phase": self.game.phase.value,
                        "actor_player_id": reservation.actor_player_id,
                        "ability_id": ability.id,
                        "target_player_ids": list(reservation.target_player_ids),
                    },
                )
            )

        protected_player_ids: set[str] = set()
        death_requests: dict[str, DeathRequest] = {}
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
                if effect_id not in IMPLEMENTED_EFFECT_IDS:
                    raise RuntimeError(f"effect '{effect_id}' has no Phase 1.5 implementation")
                group = [effect for effect in effects if effect.effect_id == effect_id]
                if effect_id == "protect":
                    protected_player_ids.update(
                        target_player_id
                        for effect in group
                        for target_player_id in effect.target_player_ids
                    )
                elif effect_id == "inspect":
                    for effect in group:
                        self.resolve_inspect(effect, death_requests)
                elif effect_id == "medium_inspect":
                    for effect in group:
                        self.resolve_medium_inspect(effect)
                elif effect_id == "attack":
                    self.resolve_attack_effects(
                        group, protected_player_ids, death_requests, attack_targets_by_actor
                    )
                elif effect_id == "inspect_role":
                    for effect in group:
                        self.resolve_inspect_dead_role(effect, attack_targets_by_actor)
                elif effect_id == "kill":
                    for effect in group:
                        self.deaths.resolve_kill_effect(effect)
                else:
                    raise RuntimeError(f"effect '{effect_id}' has no Phase 1.5 implementation")

            if priority == 70:
                scheduled.extend(self.resolve_death_requests(death_requests))
                pending_priorities.update(
                    effect.priority for effect in scheduled if effect.priority not in processed_priorities
                )

        self.game.pending_actions.clear()
        self.game.night_actions_resolved = True

    def first_night_reservations(self, now: int) -> list[ActionReservation]:
        if self.game.phase is not GamePhase.NIGHT0 or self.game.rules.first_night_seer != "random_white":
            return []

        reservations: list[ActionReservation] = []
        for actor in self.game.players.values():
            if not actor.alive:
                continue
            for ability in actor.role.abilities:
                if "inspect" not in {effect.id for effect in ability.effects}:
                    continue
                if ability.available_from_night > 0:
                    continue
                candidates = [
                    player_id
                    for player_id in valid_target_ids(self.game, actor, ability.target)
                    if effective_attributes(self.game.players[player_id]).inspect_result == "not_wolf"
                ]
                if not candidates:
                    continue
                target_player_id = self.game.rng.choice(candidates)
                reservations.append(ActionReservation(actor.player_id, ability.id, (target_player_id,), now))
                self.game.event_bus.publish(
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

    def resolve_inspect(
        self, effect: ScheduledEffect, death_requests: dict[str, DeathRequest]
    ) -> None:
        for target_player_id in effect.target_player_ids:
            target = alive_player(self.game, target_player_id, "inspect target")
            self.game.event_bus.publish(
                GameEvent(
                    type="INSPECT_RESULT",
                    visibility=EventVisibility.PRIVATE,
                    recipient_player_id=effect.actor_player_id,
                    payload={
                        "target_player_id": target.player_id,
                        "result": effective_attributes(target).inspect_result,
                    },
                )
            )
            death_priority = self.deaths.on_inspected_kill_priority(target)
            if death_priority is not None:
                self.request_death(
                    death_requests, target.player_id, CoreDeathCause.CURSED.value, death_priority
                )

    def resolve_medium_inspect(self, effect: ScheduledEffect) -> None:
        for target_player_id in effect.target_player_ids:
            target = self.game.players[target_player_id]
            self.game.medium_examined_deaths.add((effect.actor_player_id, target_player_id))
            event = GameEvent(
                type="MEDIUM_RESULT",
                visibility=EventVisibility.PRIVATE,
                recipient_player_id=effect.actor_player_id,
                payload={
                    "target_player_id": target_player_id,
                    "result": effective_attributes(target).medium_result,
                },
            )
            if self.game.rules.medium.notify_timing == "night":
                self.game.event_bus.publish(event)
            else:
                self.game.queued_dawn_notifications.append(event)

    def resolve_attack_effects(
        self,
        effects: Sequence[ScheduledEffect],
        protected_player_ids: set[str],
        death_requests: dict[str, DeathRequest],
        attack_targets_by_actor: dict[str, set[str]],
    ) -> None:
        direct_effects: list[ScheduledEffect] = []
        group_effects: list[ScheduledEffect] = []
        for effect in effects:
            actor = self.game.players[effect.actor_player_id]
            if len(effect.target_player_ids) > 1 or "werewolf" not in actor.role.tags:
                direct_effects.append(effect)
            else:
                group_effects.append(effect)

        for effect in direct_effects:
            for target_player_id in effect.target_player_ids:
                attack_targets_by_actor.setdefault(effect.actor_player_id, set()).add(target_player_id)
                self.request_attack(
                    target_player_id, effect.priority, protected_player_ids, death_requests
                )
        if not group_effects:
            return
        if self.game.rules.wolf_attack.target_decision != "majority":
            raise NotImplementedError(
                "wolf attack target_decision requires the Q28 decision before it can resolve"
            )
        tallies: dict[str, int] = {}
        for effect in group_effects:
            target_player_id = effect.target_player_ids[0]
            tallies[target_player_id] = tallies.get(target_player_id, 0) + 1
        highest = max(tallies.values())
        candidates = tuple(sorted(target for target, count in tallies.items() if count == highest))
        target_player_id = candidates[0] if len(candidates) == 1 else self.game.rng.choice(candidates)
        if len(candidates) > 1:
            self.game.event_bus.publish(
                GameEvent(
                    type="WOLF_ATTACK_TIE_RESOLVED_RANDOM",
                    visibility=EventVisibility.SERVER,
                    payload={
                        "candidate_player_ids": list(candidates),
                        "selected_player_id": target_player_id,
                    },
                )
            )
        self.game.event_bus.publish(
            GameEvent(
                type="WOLF_ATTACK_TARGET_RESOLVED",
                visibility=EventVisibility.SERVER,
                payload={"target_player_id": target_player_id, "tallies": dict(sorted(tallies.items()))},
            )
        )
        self.request_attack(
            target_player_id, group_effects[0].priority, protected_player_ids, death_requests
        )
        for effect in group_effects:
            attack_targets_by_actor.setdefault(effect.actor_player_id, set()).add(target_player_id)

    def request_attack(
        self,
        target_player_id: str,
        priority: int,
        protected_player_ids: set[str],
        death_requests: dict[str, DeathRequest],
    ) -> None:
        target = alive_player(self.game, target_player_id, "attack target")
        if target.player_id in protected_player_ids:
            for protector_player_id, reservation in self.game.pending_actions.items():
                if target.player_id not in reservation.target_player_ids:
                    continue
                ability = self.ability_for(self.game.players[protector_player_id], reservation.ability_id)
                if "protect" in {effect.id for effect in ability.effects}:
                    self.game.event_bus.publish(
                        GameEvent(
                            type="GUARD_SUCCEEDED",
                            visibility=EventVisibility.PRIVATE,
                            recipient_player_id=protector_player_id,
                            payload={"target_player_id": target.player_id},
                        )
                    )
            return
        if effective_attributes(target).attack_result != "die":
            return
        self.request_death(death_requests, target.player_id, CoreDeathCause.ATTACKED.value, priority)

    def resolve_inspect_dead_role(
        self, effect: ScheduledEffect, attack_targets_by_actor: Mapping[str, set[str]]
    ) -> None:
        for target_player_id in sorted(
            attack_targets_by_actor.get(effect.actor_player_id, set(effect.target_player_ids))
        ):
            death = self.game.death_records.get(target_player_id)
            if death is None or death.cause != CoreDeathCause.ATTACKED.value:
                continue
            self.game.event_bus.publish(
                GameEvent(
                    type="INSPECT_DEAD_ROLE_RESULT",
                    visibility=EventVisibility.PRIVATE,
                    recipient_player_id=effect.actor_player_id,
                    payload={
                        "target_player_id": target_player_id,
                        "role_id": self.game.players[target_player_id].role.id,
                    },
                )
            )

    @staticmethod
    def request_death(
        requests: dict[str, DeathRequest], player_id: str, cause: str, priority: int
    ) -> None:
        previous = requests.get(player_id)
        if previous is None or priority < previous.priority:
            requests[player_id] = DeathRequest(player_id, cause, priority)

    def resolve_death_requests(
        self, requests: Mapping[str, DeathRequest]
    ) -> list[ScheduledEffect]:
        scheduled: list[ScheduledEffect] = []
        for request in sorted(requests.values(), key=lambda item: (item.priority, item.player_id)):
            if self.game.players[request.player_id].alive:
                scheduled.extend(
                    self.deaths.record(
                        request.player_id, request.cause, collect_passive_effects=True
                    )
                )
        return scheduled

    def ability_for(self, player: Player, ability_id: str) -> Ability:
        for ability in player.role.abilities:
            if ability.id == ability_id:
                return ability
        raise ValueError(f"player '{player.player_id}' does not have ability '{ability_id}'")

    def validate_ability_available(self, actor: Player, ability: Ability) -> None:
        timing = self.game.content.action_timings[ability.timing]
        if self.game.phase.value not in timing.phases:
            raise ValueError(
                f"ability '{ability.id}' is unavailable during phase '{self.game.phase.value}'"
            )
        current_night = night_number(self.game)
        if current_night is None or ability.available_from_night > current_night:
            raise ValueError(f"ability '{ability.id}' is unavailable on this night")
        if self.game.phase is GamePhase.NIGHT0 and "inspect" in {effect.id for effect in ability.effects}:
            if self.game.rules.first_night_seer != "free":
                raise ValueError("the first-night inspection is not player-selected by the current rules")
        key = (actor.player_id, ability.id)
        if (
            ability.uses.per_game is not None
            and self.game.ability_uses_per_game.get(key, 0) >= ability.uses.per_game
        ):
            raise ValueError(f"ability '{ability.id}' has no remaining game uses")
        if (
            ability.uses.per_night is not None
            and self.game.ability_uses_this_night.get(key, 0) >= ability.uses.per_night
        ):
            raise ValueError(f"ability '{ability.id}' has no remaining uses this night")

    def validate_targets(
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
        invalid = set(targets) - set(valid_target_ids(self.game, actor, ability.target))
        if invalid:
            raise ValueError(
                f"ability '{ability.id}' has invalid target(s): {', '.join(sorted(invalid))}"
            )
        return targets

    def validate_restrictions(
        self, actor: Player, ability: Ability, targets: tuple[str, ...]
    ) -> None:
        for restriction in ability.restrictions:
            if restriction.type not in IMPLEMENTED_RESTRICTION_TYPE_IDS:
                raise RuntimeError(
                    f"restriction '{restriction.type}' has no Phase 1.5 implementation"
                )
            if not self.enabled_when(restriction.enabled_when):
                continue
            if restriction.type == "no_same_target_consecutive":
                if self.game.last_resolved_targets.get((actor.player_id, ability.id)) == targets:
                    raise ValueError(f"ability '{ability.id}' cannot target the same player consecutively")
                continue
            raise RuntimeError(f"restriction '{restriction.type}' has no Phase 1.5 implementation")

    def enabled_when(self, expression: str | None) -> bool:
        if expression is None:
            return True
        path, expected = expression.removeprefix("rules.").split(" == ", maxsplit=1)
        value: object = self.game.rules
        for attribute in path.split("."):
            value = getattr(value, attribute)
        return value is (expected == "true")

    def consume_resolved_ability(self, reservation: ActionReservation) -> None:
        player = self.game.players[reservation.actor_player_id]
        ability = self.ability_for(player, reservation.ability_id)
        key = (reservation.actor_player_id, reservation.ability_id)
        self.game.ability_uses_this_night[key] = self.game.ability_uses_this_night.get(key, 0) + 1
        if ability.uses.per_game is not None:
            self.game.ability_uses_per_game[key] = self.game.ability_uses_per_game.get(key, 0) + 1
        self.game.last_resolved_targets[key] = reservation.target_player_ids

    def phase_action_kinds(self, player_id: str) -> tuple[PhaseActionKind, ...]:
        """Return Phase 1.3 action kinds allowed by content declarations."""

        try:
            player = self.game.players[player_id]
        except KeyError as error:
            raise ValueError(f"unknown player '{player_id}'") from error
        if not player.alive:
            return ()
        phase_id = self.game.phase.value
        current_night = night_number(self.game)
        actions: list[PhaseActionKind] = []
        for ability in player.role.abilities:
            timing = self.game.content.action_timings[ability.timing]
            if phase_id not in timing.phases:
                continue
            if current_night is not None and ability.available_from_night > current_night:
                continue
            actions.append(PhaseActionKind(type="ability", ability_id=ability.id))
        for channel_id in player.role.chat_channels:
            channel = self.game.content.chat_channels[channel_id]
            if phase_id in channel.phases:
                actions.append(PhaseActionKind(type="chat", channel=channel_id))
        return tuple(actions)
