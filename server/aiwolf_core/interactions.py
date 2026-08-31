"""Authoritative receipt of player-originated chat and CO operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .events import EventVisibility, GameEvent
from .models import GamePhase
from .state import ActionSpec
from .rejections import ActionRejected

if TYPE_CHECKING:
    from .game import GameState


@dataclass(frozen=True)
class ChatSubmission:
    """A core-authorized chat message for the network delivery boundary."""

    channel_id: str
    message: dict[str, str]


class PlayerInteractions:
    """Validate player communication from core action availability, never network state."""

    def __init__(self, game: GameState) -> None:
        self.game = game

    def submit_chat(self, player_id: str, channel_id: str, message: str) -> ChatSubmission:
        """Accept one authorized chat message and record daytime public activity."""

        if not isinstance(message, str) or not message:
            raise ActionRejected("invalid_message")
        self._require_available(player_id, "chat", channel_id=channel_id)
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
        )

    def declare_co(self, player_id: str, claimed_role_id: str, comment: str) -> None:
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

    def report_co(
        self,
        player_id: str,
        kind: str,
        target_player_id: str,
        claimed_result: str,
    ) -> None:
        """Publish a claim report without inferring or checking its truth."""

        if not isinstance(kind, str) or not kind:
            raise ActionRejected("invalid_report_kind")
        if target_player_id not in self.game.players:
            raise ActionRejected("unknown_target")
        if not isinstance(claimed_result, str) or not claimed_result:
            raise ActionRejected("invalid_claimed_result")
        self._require_available(player_id, "co_report")
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

    def _require_available(
        self, player_id: str, action_type: str, *, channel_id: str | None = None
    ) -> ActionSpec:
        for action in self.game.get_available_actions(player_id):
            if action.type == action_type and (channel_id is None or action.channel == channel_id):
                return action
        raise ActionRejected("action_unavailable")

    def _record_public_activity(self, player_id: str) -> None:
        """Record one public operation for the current day in the single activity source."""

        key = (self.game.day, player_id)
        counts = self.game.public_activity_counts
        counts[key] = counts.get(key, 0) + 1
