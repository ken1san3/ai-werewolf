"""Player-visible state and history, independent of connections and transports."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, Mapping

from .events import EventVisibility, GameEvent
from .state import ActionSpec, Player

if TYPE_CHECKING:
    from .game import GameState


class PlayerViews:
    """Remember only information authorized at publication time, even while offline."""

    def __init__(self, game: GameState) -> None:
        self.game = game
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._deaths: dict[str, list[dict[str, Any]]] = {}
        self._phase: dict[str, dict[str, Any]] = {}
        game.event_bus.subscribe(EventVisibility.PUBLIC, self._record_event)
        game.event_bus.subscribe(EventVisibility.PRIVATE, self._record_event)

    def player_list(self) -> dict[str, Any]:
        return {"players": [
            {"player_id": player.player_id, "display_name": player.display_name}
            for player in self.game.players.values()
        ]}

    def deaths(self, player_id: str) -> dict[str, Any]:
        self._player(player_id)
        return {"deaths": deepcopy(self._deaths.get(player_id, []))}

    def action_state(self, player_id: str) -> dict[str, Any]:
        self._player(player_id)
        if self.game.can_view_public_events(player_id):
            phase = self._current_phase()
        else:
            # A disabled graveyard must not learn subsequent phases on reconnect.
            phase = self._phase.get(player_id)
            if phase is None:
                raise RuntimeError("player has no visible phase state")
        return {**deepcopy(phase), "actions": [
            action_payload(action) for action in self.game.get_available_actions(player_id)
        ]}

    def state_sync(self, player_id: str) -> dict[str, Any]:
        player = self._player(player_id)
        own_state = {
            "player_id": player_id,
            "role_id": player.role.id,
            "modifier_ids": [modifier.definition.id for modifier in player.modifiers],
        }
        revealed_roles = []
        if not player.alive and self.game.rules.graveyard.reveal_roles:
            revealed_roles = [
                {"player_id": other.player_id, "role_id": other.role.id}
                for other in self.game.players.values()
            ]
        return {
            **self.player_list(),
            **self.deaths(player_id),
            "action_state": self.action_state(player_id),
            "self": own_state,
            "revealed_roles": revealed_roles,
            "history": deepcopy(self._history.get(player_id, [])),
        }

    def record_chat(self, channel_id: str, message: Mapping[str, Any]) -> None:
        for player_id in self.game.chat_channel_recipient_ids(channel_id):
            self._append(player_id, "chat.message", {"channel": channel_id, "message": dict(message)})

    def _record_event(self, event: GameEvent) -> None:
        if event.visibility is EventVisibility.PUBLIC:
            recipients = [
                player_id for player_id in self.game.players
                if self.game.can_view_public_events(player_id)
            ]
        elif event.visibility is EventVisibility.PRIVATE:
            recipient = event.recipient_player_id
            recipients = (
                [recipient]
                if recipient is not None and self.game.can_receive_private_events(recipient)
                else []
            )
        else:
            return
        for player_id in recipients:
            self._append(player_id, "game.event", {
                "event_type": event.type, "event_payload": dict(event.payload),
            })
            if event.visibility is EventVisibility.PUBLIC:
                if event.type == "PLAYER_DIED":
                    self._deaths.setdefault(player_id, []).append(deepcopy(dict(event.payload)))
                if event.type in {"PHASE_STARTED", "DAY_EXTENDED", "DAY_SHORTENED"}:
                    self._phase[player_id] = self._current_phase()

    def _append(self, player_id: str, message_type: str, payload: Mapping[str, Any]) -> None:
        self._history.setdefault(player_id, []).append({
            "type": message_type, "payload": deepcopy(dict(payload)),
        })

    def _current_phase(self) -> dict[str, Any]:
        return {
            "phase": self.game.phase.value,
            "day": self.game.day,
            "phase_ends_at": self.game.phase_ends_at,
        }

    def _player(self, player_id: str) -> Player:
        try:
            return self.game.players[player_id]
        except KeyError as error:
            raise ValueError(f"unknown player '{player_id}'") from error


def action_payload(action: ActionSpec) -> dict[str, Any]:
    """Serialize only the fields belonging to the core-declared action kind."""

    payload: dict[str, Any] = {"type": action.type}
    if action.type == "ability":
        payload.update(
            ability_id=action.ability_id, description=action.description,
            valid_targets=list(action.valid_targets), target_count=action.target_count,
            uses_remaining=action.uses_remaining,
        )
    elif action.type == "chat":
        payload["channel"] = action.channel
    elif action.type == "vote":
        payload.update(valid_targets=list(action.valid_targets), target_count=action.target_count,
                       allows_abstain=action.allows_abstain)
    elif action.type == "co_declare":
        payload["claimed_role_ids"] = list(action.claimed_role_ids)
    elif action.type != "co_report":
        raise ValueError(f"unsupported action type '{action.type}'")
    return payload
