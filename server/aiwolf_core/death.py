"""Death recording, public masking, and death-triggered passive resolution."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Mapping

from .events import EventVisibility, GameEvent
from .models import CoreDeathCause, GamePhase, TargetSpec
from .state import DeathRecord, ScheduledEffect
from .targets import alive_player, passives_for, valid_target_ids

if TYPE_CHECKING:
    from .game import GameState
    from .state import Player


PASSIVE_DISPATCH_IDS = frozenset({"on_inspected", "retaliate_on_death"})
PASSIVE_EFFECT_DISPATCH_IDS = frozenset({"kill"})


class DeathResolver:
    """Apply every death through the one internal and public event boundary."""

    def __init__(self, game: GameState) -> None:
        self.game = game

    def record(
        self,
        player_id: str | None,
        internal_cause: str,
        *,
        collect_passive_effects: bool = False,
    ) -> list[ScheduledEffect]:
        """Record one death, publish masked visibility-specific events, and run passives."""

        if player_id is None:
            raise RuntimeError("a death event must identify a player")
        player = alive_player(self.game, player_id, "dying player")
        if internal_cause not in self.game.content.death_causes:
            raise RuntimeError(f"content must register death cause '{internal_cause}'")
        phase_at_death = self.game.phase
        self.game.players[player.player_id] = replace(player, alive=False)
        self.game.death_records[player.player_id] = DeathRecord(
            player_id=player.player_id,
            cause=internal_cause,
            phase=phase_at_death,
            day=self.game.day,
        )
        self.game.event_bus.publish(
            GameEvent(
                type="PLAYER_DIED",
                visibility=EventVisibility.SERVER,
                payload={
                    "player_id": player.player_id,
                    "cause": internal_cause,
                    "day": self.game.day,
                    "phase": phase_at_death.value,
                },
            )
        )
        payload: dict[str, str | int] = {"player_id": player.player_id, "day": self.game.day}
        if self.game.rules.death.public_detail == "phase":
            payload["public_cause"] = public_death_cause(internal_cause, phase_at_death)
        elif self.game.rules.death.public_detail == "cause":
            payload["public_cause"] = internal_cause
        self.game.event_bus.publish(
            GameEvent(type="PLAYER_DIED", visibility=EventVisibility.PUBLIC, payload=payload)
        )
        if collect_passive_effects:
            return self.scheduled_passive_effects(player, internal_cause)
        self.resolve_passives_immediately(player, internal_cause)
        return []

    def on_inspected_kill_priority(self, player: Player) -> int | None:
        """Return the first kill priority declared for an inspected-player passive."""

        priorities: list[int] = []
        for passive in passives_for(player):
            if passive.type != "on_inspected":
                continue
            self._assert_passive_supported(passive.type, passive.effects)
            priorities.extend(
                effect.priority
                for rule in passive.rules
                if rule.get("when", {}).get("event") == "inspected"
                for effect in passive.effects
                if effect.id == "kill"
            )
        return min(priorities, default=None)

    def scheduled_passive_effects(
        self, player: Player, internal_cause: str
    ) -> list[ScheduledEffect]:
        """Select targets and schedule passive deaths for the action priority queue."""

        scheduled: list[ScheduledEffect] = []
        for passive in passives_for(player):
            if passive.type != "retaliate_on_death":
                continue
            self._assert_passive_supported(passive.type, passive.effects)
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
                candidates = list(valid_target_ids(self.game, player, target_spec))
                if len(candidates) < target_spec.count:
                    continue
                if target.get("pick") != "random":
                    raise RuntimeError("retaliate_on_death requires pick: random")
                selected: list[str] = []
                for _ in range(target_spec.count):
                    picked = self.game.rng.choice(candidates)
                    candidates.remove(picked)
                    selected.append(picked)
                self.game.event_bus.publish(
                    GameEvent(
                        type="PASSIVE_TARGET_SELECTED",
                        visibility=EventVisibility.SERVER,
                        payload={
                            "passive_type": passive.type,
                            "source_player_id": player.player_id,
                            "candidate_player_ids": list(valid_target_ids(self.game, player, target_spec)),
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
                        ScheduledEffect(
                            priority=effect.priority,
                            effect_id=effect.id,
                            actor_player_id=player.player_id,
                            ability_id=f"passive:{passive.type}",
                            target_player_ids=tuple(selected),
                            death_cause=death_cause,
                        )
                    )
        return scheduled

    def resolve_passives_immediately(self, player: Player, internal_cause: str) -> None:
        """Resolve non-queued death passives, preserving their declared priority."""

        scheduled = self.scheduled_passive_effects(player, internal_cause)
        for effect in sorted(scheduled, key=lambda item: item.priority):
            self.resolve_kill_effect(effect)

    def resolve_kill_effect(self, effect: ScheduledEffect) -> None:
        """Apply a scheduled kill while retaining the next passive chain."""

        if effect.death_cause is None:
            raise RuntimeError("a kill effect must declare its internal death cause")
        for target_player_id in effect.target_player_ids:
            if self.game.players[target_player_id].alive:
                self.record(target_player_id, effect.death_cause)

    @staticmethod
    def _assert_passive_supported(passive_type: str, effects: tuple) -> None:
        if passive_type not in PASSIVE_DISPATCH_IDS:
            raise RuntimeError(f"passive '{passive_type}' has no Phase 1.5 implementation")
        unsupported_effects = [
            effect.id for effect in effects if effect.id not in PASSIVE_EFFECT_DISPATCH_IDS
        ]
        if unsupported_effects:
            raise RuntimeError(
                f"passive effect '{unsupported_effects[0]}' has no Phase 1.5 implementation"
            )


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
