"""Night-action reservation, validation, and effect resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Sequence

from .action_constraints import ACTION_RESTRICTION_DISPATCH_IDS, ActionConstraints
from .clock import timestamp
from .death import DeathResolver
from .events import EventVisibility, GameEvent
from .models import Ability, CoreDeathCause, GamePhase
from .rejections import ActionRejected
from .state import ActionReservation, DeathRequest, ScheduledEffect
from .targets import alive_player, effective_attributes, valid_target_ids

if TYPE_CHECKING:
    from .game import GameState
    from .state import Player


ACTION_EFFECT_DISPATCH_IDS = frozenset(
    {"protect", "inspect", "medium_inspect", "attack", "inspect_role", "kill"}
)
class ActionResolver(ActionConstraints):
    """Own all mutation caused by submitted night abilities."""

    def __init__(self, game: GameState) -> None:
        super().__init__(game)
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
            raise ActionRejected("action_unavailable", "abilities can only be submitted during a night phase")
        if self.game.phase_started_at is None or self.game.phase_ends_at is None:
            raise RuntimeError("night actions require an authoritative deadline")
        if now < self.game.phase_started_at:
            raise ActionRejected(
                "action_unavailable", "abilities cannot be submitted before the phase starts"
            )
        if now >= self.game.phase_ends_at:
            raise ActionRejected(
                "action_deadline_passed", "abilities cannot be submitted after the deadline"
            )
        if self.game.night_actions_resolved:
            raise ActionRejected("action_closed", "night actions have already been resolved")
        try:
            actor = alive_player(self.game, actor_player_id, "action actor")
        except ValueError as error:
            raise ActionRejected("actor_unavailable", str(error)) from error
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
        reservations.extend(self.no_selection_reservations(now))
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

        protectors_by_target: dict[str, set[str]] = {}
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
                if effect_id not in ACTION_EFFECT_DISPATCH_IDS:
                    raise RuntimeError(f"effect '{effect_id}' has no Phase 1.5 implementation")
                group = [effect for effect in effects if effect.effect_id == effect_id]
                if effect_id == "protect":
                    for effect in group:
                        for target_player_id in effect.target_player_ids:
                            protectors_by_target.setdefault(target_player_id, set()).add(
                                effect.actor_player_id
                            )
                elif effect_id == "inspect":
                    for effect in group:
                        self.resolve_inspect(effect, death_requests)
                elif effect_id == "medium_inspect":
                    for effect in group:
                        self.resolve_medium_inspect(effect)
                elif effect_id == "attack":
                    self.resolve_attack_effects(
                        group, protectors_by_target, death_requests, attack_targets_by_actor
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

    def no_selection_reservations(self, now: int) -> list[ActionReservation]:
        """Create server-selected reservations for unsubmitted random abilities."""

        reservations: list[ActionReservation] = []
        submitted_actor_ids = set(self.game.pending_actions)
        for actor_player_id in sorted(self.game.players):
            if actor_player_id in submitted_actor_ids:
                continue
            actor = self.game.players[actor_player_id]
            if not actor.alive:
                continue
            for ability in actor.role.abilities:
                if self.no_selection_behavior(ability) != "random":
                    continue
                try:
                    self.validate_ability_available(actor, ability)
                except ValueError:
                    continue
                target_options = self.random_target_options(actor, ability)
                if not target_options:
                    continue
                selected_targets = self.game.rng.choice(target_options)
                candidates = valid_target_ids(self.game, actor, ability.target)
                self.game.event_bus.publish(
                    GameEvent(
                        type="ACTION_NO_SELECTION_RANDOM_TARGETS_SELECTED",
                        visibility=EventVisibility.SERVER,
                        payload={
                            "actor_player_id": actor.player_id,
                            "ability_id": ability.id,
                            "candidate_player_ids": list(candidates),
                            "selected_player_ids": list(selected_targets),
                        },
                    )
                )
                reservations.append(ActionReservation(actor.player_id, ability.id, selected_targets, now))
                break
        return reservations

    def no_selection_behavior(self, ability: Ability) -> str:
        return self.game.rules.night_action.no_selection or ability.no_selection

    def random_target_options(self, actor: Player, ability: Ability) -> tuple[tuple[str, ...], ...]:
        """Return target combinations satisfying the same restrictions as submission."""

        return self.valid_target_sets(actor, ability)

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
        protectors_by_target: Mapping[str, set[str]],
        death_requests: dict[str, DeathRequest],
        attack_targets_by_actor: dict[str, set[str]],
    ) -> None:
        direct_effects: list[ScheduledEffect] = []
        group_effects: list[ScheduledEffect] = []
        for effect in effects:
            ability = self.ability_for(
                self.game.players[effect.actor_player_id], effect.ability_id
            )
            if ability.resolution == "individual":
                direct_effects.append(effect)
            else:
                group_effects.append(effect)

        for effect in direct_effects:
            for target_player_id in effect.target_player_ids:
                attack_targets_by_actor.setdefault(effect.actor_player_id, set()).add(target_player_id)
                self.request_attack(
                    target_player_id, effect.priority, protectors_by_target, death_requests
                )
        if not group_effects:
            return
        if self.game.rules.wolf_attack.target_decision == "random":
            candidates = tuple(
                sorted(
                    {
                        target_player_id
                        for effect in group_effects
                        for target_player_id in valid_target_ids(
                            self.game,
                            self.game.players[effect.actor_player_id],
                            self.ability_for(
                                self.game.players[effect.actor_player_id], effect.ability_id
                            ).target,
                        )
                    }
                )
            )
            if not candidates:
                return
            target_player_id = self.game.rng.choice(candidates)
            self.game.event_bus.publish(
                GameEvent(
                    type="WOLF_ATTACK_TARGET_SELECTED_RANDOM",
                    visibility=EventVisibility.SERVER,
                    payload={
                        "candidate_player_ids": list(candidates),
                        "selected_player_id": target_player_id,
                    },
                )
            )
            tallies: dict[str, int] = {}
        else:
            tallies = {}
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
            target_player_id, group_effects[0].priority, protectors_by_target, death_requests
        )
        for effect in group_effects:
            attack_targets_by_actor.setdefault(effect.actor_player_id, set()).add(target_player_id)

    def request_attack(
        self,
        target_player_id: str,
        priority: int,
        protectors_by_target: Mapping[str, set[str]],
        death_requests: dict[str, DeathRequest],
    ) -> None:
        target = alive_player(self.game, target_player_id, "attack target")
        if target.player_id in protectors_by_target:
            for protector_player_id in sorted(protectors_by_target[target.player_id]):
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

    def consume_resolved_ability(self, reservation: ActionReservation) -> None:
        player = self.game.players[reservation.actor_player_id]
        ability = self.ability_for(player, reservation.ability_id)
        key = (reservation.actor_player_id, reservation.ability_id)
        self.game.ability_uses_this_night[key] = self.game.ability_uses_this_night.get(key, 0) + 1
        if ability.uses.per_game is not None:
            self.game.ability_uses_per_game[key] = self.game.ability_uses_per_game.get(key, 0) + 1
        self.game.last_resolved_targets[key] = reservation.target_player_ids
