"""Authoritative phase transitions and day time controls."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from .actions import ActionResolver
from .clock import timestamp
from .events import EventVisibility, GameEvent
from .models import GamePhase, RulesConfig
from .targets import passives_for
from .wins import WinEvaluator

if TYPE_CHECKING:
    from .game import GameState


DAWN_PASSIVE_DISPATCH_IDS = frozenset({"public_notify_if_alive"})
DAWN_PASSIVE_EFFECT_DISPATCH_IDS = frozenset({"public_notify"})
TICK_NOOP_PHASES = frozenset({GamePhase.SETUP, GamePhase.GAME_END})


class PhaseManager:
    """Mutate phase/timing state without owning vote or action resolution logic."""

    def __init__(self, game: GameState) -> None:
        self.game = game

    def start(self, now: int) -> GamePhase:
        """Enter the mandatory first-night phase at a logical server time."""

        if self.game.phase is not GamePhase.SETUP:
            raise ValueError("only a setup game can be started")
        self.enter(GamePhase.NIGHT0, now)
        return self.game.phase

    def advance(self, now: int, *, game_ended: bool = False) -> GamePhase:
        """Advance one non-vote phase after its deadline or manual result."""

        now = timestamp(now)
        if self.game.phase is GamePhase.GAME_END:
            raise ValueError("a finished game cannot advance")
        if self.game.phase_started_at is None or now < self.game.phase_started_at:
            raise ValueError("phase cannot advance before it starts")
        if self.game.phase_ends_at is not None and now < self.game.phase_ends_at:
            raise ValueError("phase cannot advance before its deadline")
        if self.game.phase in {GamePhase.VOTE, GamePhase.RUNOFF}:
            raise ValueError("vote phases must advance through resolve_votes")
        if game_ended and self.game.phase not in {GamePhase.EXECUTION, GamePhase.NIGHT}:
            raise ValueError("game_ended is only valid after death resolution")
        if self.game.phase in {GamePhase.NIGHT0, GamePhase.NIGHT}:
            ActionResolver(self.game).resolve(now)
            game_ended = WinEvaluator(self.game).evaluate_and_record() is not None or game_ended
        else:
            game_ended = self.game.game_result is not None or game_ended
        self.enter(self.next_phase(game_ended=game_ended), now)
        return self.game.phase

    def advance_if_due(self, now: int, *, game_ended: bool = False) -> bool:
        """Advance one core-owned tick step when the current phase is ready."""

        now = timestamp(now)
        if self.game.phase in {GamePhase.VOTE, GamePhase.RUNOFF}:
            if self.game.phase_ends_at is None:
                raise RuntimeError("vote phases must have an authoritative deadline")
            if now < self.game.phase_ends_at:
                return False
            from .voting import VoteResolver

            VoteResolver(self.game).resolve(now)
            return True
        if self.game.phase is GamePhase.EXECUTION:
            self.advance(now, game_ended=game_ended)
            return True
        if self.game.phase_ends_at is None:
            if self.game.phase not in TICK_NOOP_PHASES:
                raise RuntimeError(
                    f"phase '{self.game.phase.value}' has no core tick progression strategy"
                )
            return False
        if now < self.game.phase_ends_at:
            return False
        self.advance(now, game_ended=game_ended)
        return True

    def approve_extension(self, now: int, approver_player_ids: Iterable[str]) -> bool:
        """Extend Day when the configured alive-player quorum approves."""

        now = timestamp(now)
        if self.game.phase is not GamePhase.DAY:
            raise ValueError("time extensions are only available during the day phase")
        if self.game.phase_started_at is None or self.game.phase_ends_at is None:
            raise RuntimeError("the day phase must have a deadline")
        if now < self.game.phase_started_at:
            raise ValueError("time extension cannot be approved before the day starts")
        if now >= self.game.phase_ends_at or self.game.extensions_used >= self.game.rules.extension.max_count:
            return False
        if not self.has_alive_approval(approver_player_ids, self.game.rules.extension.approval):
            return False

        self.game.extensions_used += 1
        self.game.phase_ends_at += self.game.rules.extension.seconds_per_extension
        self.game.event_bus.publish(
            GameEvent(
                type="DAY_EXTENDED",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "day": self.game.day,
                    "phase": self.game.phase.value,
                    "phase_ends_at": self.game.phase_ends_at,
                    "extensions_used": self.game.extensions_used,
                },
            )
        )
        return True

    def approve_shortening(self, now: int, approver_player_ids: Iterable[str]) -> bool:
        """Set the current Day deadline to now when its quorum approves."""

        now = timestamp(now)
        if self.game.phase is not GamePhase.DAY:
            raise ValueError("time shortening is only available during the day phase")
        if self.game.phase_started_at is None or self.game.phase_ends_at is None:
            raise RuntimeError("the day phase must have a deadline")
        if now < self.game.phase_started_at:
            raise ValueError("time shortening cannot be approved before the day starts")
        if now >= self.game.phase_ends_at or not self.game.rules.shortening.enabled:
            return False
        if not self.has_alive_approval(approver_player_ids, self.game.rules.shortening.approval):
            return False

        self.game.phase_ends_at = now
        self.game.event_bus.publish(
            GameEvent(
                type="DAY_SHORTENED",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "day": self.game.day,
                    "phase": self.game.phase.value,
                    "phase_ends_at": self.game.phase_ends_at,
                },
            )
        )
        return True

    def enter(self, phase: GamePhase, now: int) -> None:
        """Apply phase-boundary resets and emit the one public phase event."""

        now = timestamp(now)
        if phase is GamePhase.DAWN:
            self.game.day += 1
        if phase in {GamePhase.NIGHT0, GamePhase.NIGHT}:
            self.game.pending_actions.clear()
            self.game.ability_uses_this_night.clear()
            self.game.night_actions_resolved = False
        if phase is GamePhase.VOTE:
            self.game.pending_votes.clear()
            self.game.runoff_candidate_player_ids = ()
        elif phase is GamePhase.RUNOFF:
            self.game.pending_votes.clear()
        self.game.phase = phase
        self.game.phase_started_at = now
        duration = phase_duration(phase, self.game.rules)
        self.game.phase_ends_at = now + duration if duration is not None else None
        self.game.event_bus.publish(
            GameEvent(
                type="PHASE_STARTED",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "phase": self.game.phase.value,
                    "day": self.game.day,
                    "phase_ends_at": self.game.phase_ends_at,
                },
            )
        )
        if phase is GamePhase.DAWN:
            self.emit_dawn_public_notifications()
            for event in self.game.queued_dawn_notifications:
                self.game.event_bus.publish(event)
            self.game.queued_dawn_notifications.clear()

    def emit_dawn_public_notifications(self) -> None:
        """Publish each content-declared alive notification once per Dawn."""

        notify_ids: set[str] = set()
        for player in self.game.players.values():
            if not player.alive:
                continue
            for passive in passives_for(player):
                if passive.type not in DAWN_PASSIVE_DISPATCH_IDS:
                    continue
                for rule in passive.rules:
                    when = rule.get("when", {})
                    if when.get("event") != "dawn" or when.get("actor_alive") is not True:
                        continue
                    if any(
                        effect.id not in DAWN_PASSIVE_EFFECT_DISPATCH_IDS
                        for effect in passive.effects
                    ):
                        raise RuntimeError(
                            f"passive '{passive.type}' has no Phase 1.5 implementation"
                        )
                    notify_ids.add(rule["notify_id"])
        for notify_id in sorted(notify_ids):
            self.game.event_bus.publish(
                GameEvent(
                    type="PUBLIC_NOTIFY",
                    visibility=EventVisibility.PUBLIC,
                    payload={"notify_id": notify_id},
                )
            )

    def next_phase(self, *, game_ended: bool) -> GamePhase:
        """Return the legal successor after non-vote work has completed."""

        if self.game.phase is GamePhase.SETUP:
            return GamePhase.NIGHT0
        if self.game.phase is GamePhase.NIGHT0:
            return GamePhase.DAWN
        if self.game.phase is GamePhase.DAWN:
            return GamePhase.DAY
        if self.game.phase is GamePhase.DAY:
            return GamePhase.VOTE
        if self.game.phase in {GamePhase.VOTE, GamePhase.RUNOFF}:
            raise RuntimeError("vote phases must transition through resolve_votes")
        if self.game.phase is GamePhase.EXECUTION:
            return GamePhase.GAME_END if game_ended else GamePhase.NIGHT
        if self.game.phase is GamePhase.NIGHT:
            return GamePhase.GAME_END if game_ended else GamePhase.DAWN
        raise RuntimeError(f"unsupported phase transition from '{self.game.phase.value}'")

    def has_alive_approval(self, approver_player_ids: Iterable[str], approval: str) -> bool:
        approvers = tuple(approver_player_ids)
        if len(approvers) != len(set(approvers)):
            raise ValueError("time-change approvers must be unique")
        alive_player_ids = {
            player_id for player_id, player in self.game.players.items() if player.alive
        }
        if set(approvers) - alive_player_ids:
            raise ValueError("time-change approvers must be alive players")
        return len(approvers) >= approval_count(approval, len(alive_player_ids))


def phase_duration(phase: GamePhase, rules: RulesConfig) -> int | None:
    """Return the content-configured duration for deadline-driven phases."""

    if phase in {GamePhase.NIGHT0, GamePhase.NIGHT}:
        return rules.night_seconds
    if phase is GamePhase.DAWN:
        return rules.silence_after_dawn_seconds
    if phase is GamePhase.DAY:
        return rules.day_seconds
    if phase in {GamePhase.VOTE, GamePhase.RUNOFF}:
        return rules.vote_seconds
    return None


def approval_count(approval: str, alive_player_count: int) -> int:
    """Calculate a configured all/majority quorum from current living players."""

    if approval == "all":
        return alive_player_count
    if approval == "majority":
        return (alive_player_count // 2) + 1
    raise ValueError(f"unsupported extension approval rule '{approval}'")
