"""Authoritative receipt of player-originated chat and CO operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .clock import timestamp
from .events import EventVisibility, GameEvent
from .models import GamePhase
from .state import ActionSpec
from .rejections import ActionRejected

if TYPE_CHECKING:
    from .game import GameState


@dataclass(frozen=True)
class InteractionAcceptance:
    """Immutable internal receipt evidence for one authorized player action."""

    action: str
    player_id: str
    day: int
    phase: str
    accepted_at: int
    phase_deadline: int


@dataclass(frozen=True)
class ChatSubmission:
    """A core-authorized chat message for the network delivery boundary."""

    channel_id: str
    message: dict[str, str]
    acceptance: InteractionAcceptance


class PlayerInteractions:
    """Validate player communication from core action availability, never network state."""

    def __init__(self, game: GameState) -> None:
        self.game = game

    def submit_chat(
        self, now: int, player_id: str, channel_id: str, message: str
    ) -> ChatSubmission:
        """Accept one authorized chat message and record daytime public activity."""

        if not isinstance(message, str) or not message:
            raise ActionRejected("invalid_message")
        self._require_available(player_id, "chat", channel_id=channel_id)
        accepted_at, deadline = self._require_open_communication_window(now)
        acceptance = self._acceptance("chat.send", player_id, accepted_at, deadline)
        player = self.game.players[player_id]
        channel = self.game.content.chat_channels[channel_id]
        if channel.is_public and self.game.phase is GamePhase.DAY:
            self._record_public_activity(player_id)
        return ChatSubmission(
            channel_id=channel_id,
            message={
                "player_id": player.player_id,
                "display_name": player.display_name,
                "message": message,
            },
            acceptance=acceptance,
        )

    def declare_co(
        self, now: int, player_id: str, claimed_role_id: str, comment: str
    ) -> InteractionAcceptance:
        """Publish a content-known claim without checking whether it is truthful."""

        if not isinstance(claimed_role_id, str) or claimed_role_id not in self.game.content.roles:
            raise ActionRejected("unknown_claimed_role")
        if not isinstance(comment, str) or not comment:
            raise ActionRejected("invalid_comment")
        if not self.game.can_declare_co(player_id):
            raise ActionRejected("co_limit_reached")
        action = self._require_available(player_id, "co_declare")
        if claimed_role_id not in action.claimed_role_ids:
            raise ActionRejected("claim_not_allowed")
        accepted_at, deadline = self._require_open_communication_window(now)
        acceptance = self._acceptance("co.declare", player_id, accepted_at, deadline)
        key = (self.game.day, player_id)
        self.game.co_declaration_counts[key] = self.game.co_declaration_counts.get(key, 0) + 1
        self._record_public_activity(player_id)
        self.game.event_bus.publish(
            GameEvent(
                type="CO_DECLARED",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "player_id": player_id,
                    "claimed_role_id": claimed_role_id,
                    "comment": comment,
                },
            )
        )
        return acceptance

    def report_co(
        self,
        now: int,
        player_id: str,
        kind: str,
        target_player_id: str,
        claimed_result: str,
    ) -> InteractionAcceptance:
        """Publish a claim report without inferring or checking its truth."""

        if not isinstance(kind, str) or not kind:
            raise ActionRejected("invalid_report_kind")
        if target_player_id not in self.game.players:
            raise ActionRejected("unknown_target")
        if not isinstance(claimed_result, str) or not claimed_result:
            raise ActionRejected("invalid_claimed_result")
        self._require_available(player_id, "co_report")
        accepted_at, deadline = self._require_open_communication_window(now)
        acceptance = self._acceptance("co.report", player_id, accepted_at, deadline)
        self._record_public_activity(player_id)
        self.game.event_bus.publish(
            GameEvent(
                type="CO_REPORTED",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "player_id": player_id,
                    "kind": kind,
                    "target_player_id": target_player_id,
                    "claimed_result": claimed_result,
                },
            )
        )
        return acceptance

    def _require_available(
        self, player_id: str, action_type: str, *, channel_id: str | None = None
    ) -> ActionSpec:
        for action in self.game.get_available_actions(player_id):
            if action.type == action_type and (channel_id is None or action.channel == channel_id):
                return action
        raise ActionRejected("action_unavailable")

    def _require_open_communication_window(self, now: int) -> tuple[int, int]:
        received_at = timestamp(now)
        started_at = self.game.phase_started_at
        deadline = self.game.phase_ends_at
        if started_at is None or deadline is None:
            raise RuntimeError("communication actions require an authoritative deadline")
        if received_at < started_at:
            raise ActionRejected("action_unavailable")
        if received_at >= deadline:
            raise ActionRejected("action_deadline_passed")
        return received_at, deadline

    def _acceptance(
        self, action: str, player_id: str, accepted_at: int, deadline: int
    ) -> InteractionAcceptance:
        return InteractionAcceptance(
            action=action,
            player_id=player_id,
            day=self.game.day,
            phase=self.game.phase.value,
            accepted_at=accepted_at,
            phase_deadline=deadline,
        )

    def _record_public_activity(self, player_id: str) -> None:
        """Record one public operation for the current day in the single activity source."""

        key = (self.game.day, player_id)
        counts = self.game.public_activity_counts
        counts[key] = counts.get(key, 0) + 1
