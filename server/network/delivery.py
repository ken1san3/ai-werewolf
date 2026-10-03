"""Visibility-safe conversion from core events to recipient-specific server events.

The router never serializes a core event itself.  It only records the
recipient set while the EventBus publishes a public or private event; the
WebSocket server later asks :class:`SessionManager` to add its per-player
envelope and sequence number.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Collection, Mapping, Protocol, runtime_checkable

from ..aiwolf_core.events import EventBus, EventVisibility, GameEvent
from ..aiwolf_core.interactions import InteractionAcceptance


ConnectedPlayers = Callable[[str], Collection[str]]


@runtime_checkable
class DeliveryGame(Protocol):
    """Core read surface needed to select outbound message recipients."""

    game_id: str
    players: Mapping[str, object]

    def can_view_public_events(self, player_id: str) -> bool:
        ...

    def can_receive_private_events(self, player_id: str) -> bool:
        ...

    def chat_channel_recipient_ids(self, channel_id: str) -> tuple[str, ...]:
        ...

    def get_player_list(self) -> Mapping[str, Any]:
        ...

    def get_player_deaths(self, player_id: str) -> Mapping[str, Any]:
        ...

    def get_action_state(self, player_id: str) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class OutboundDelivery:
    """One already-authorized payload for zero or more current player streams."""

    game_id: str
    recipient_player_ids: tuple[str, ...]
    message_type: str
    payload: Mapping[str, Any]
    acceptance: InteractionAcceptance | None = None


class EventDeliveryRouter:
    """Subscribe only PUBLIC and PRIVATE core events and queue safe wire payloads."""

    def __init__(
        self,
        games: Mapping[str, object],
        *,
        connected_player_ids: ConnectedPlayers,
    ) -> None:
        self._games = dict(games)
        self._connected_player_ids = connected_player_ids
        self._pending: list[OutboundDelivery] = []
        for game_id, game in self._games.items():
            event_bus = getattr(game, "event_bus", None)
            if isinstance(event_bus, EventBus):
                event_bus.subscribe(
                    EventVisibility.PUBLIC,
                    lambda event, game_id=game_id: self._queue_core_event(game_id, event),
                )
                event_bus.subscribe(
                    EventVisibility.PRIVATE,
                    lambda event, game_id=game_id: self._queue_core_event(game_id, event),
                )

    def queue_channel_message(
        self,
        game_id: str,
        channel_id: str,
        message: Mapping[str, Any],
        *,
        acceptance: InteractionAcceptance | None = None,
    ) -> None:
        """Queue an already-authorized chat message for content-declared viewers.

        Phase 2.4 decides whether a player may submit a message.  This method
        intentionally owns only outbound recipient filtering.
        """

        game = self._game(game_id)
        connected = frozenset(self._connected_player_ids(game_id))
        recipients = tuple(
            player_id
            for player_id in game.chat_channel_recipient_ids(channel_id)
            if player_id in connected
        )
        if not recipients:
            return
        self._pending.append(
            OutboundDelivery(
                game_id=game_id,
                recipient_player_ids=recipients,
                message_type="chat.message",
                payload={"channel": channel_id, "message": deepcopy(dict(message))},
                acceptance=acceptance,
            )
        )

    def drain(self) -> tuple[OutboundDelivery, ...]:
        """Return queued deliveries in core event order and clear the queue."""

        pending = tuple(self._pending)
        self._pending.clear()
        return pending

    def _queue_core_event(self, game_id: str, event: GameEvent) -> None:
        game = self._game(game_id)
        connected = frozenset(self._connected_player_ids(game_id))
        if event.visibility is EventVisibility.PUBLIC:
            recipients = tuple(
                player_id
                for player_id in game.players
                if player_id in connected and game.can_view_public_events(player_id)
            )
        elif event.visibility is EventVisibility.PRIVATE:
            recipient = event.recipient_player_id
            recipients = (
                (recipient,)
                if recipient in connected and game.can_receive_private_events(recipient)
                else ()
            )
        else:
            # Defensive guard: only PUBLIC and PRIVATE subscribers are registered.
            return
        if not recipients:
            return
        self._pending.append(
            OutboundDelivery(
                game_id=game_id,
                recipient_player_ids=recipients,
                message_type="game.event",
                payload={
                    "visibility": event.visibility.value,
                    "event_type": event.type,
                    "event_payload": deepcopy(dict(event.payload)),
                },
            )
        )
        if event.visibility is EventVisibility.PUBLIC and event.type == "PHASE_STARTED":
            self._pending.append(OutboundDelivery(
                game_id, recipients, "player.list", deepcopy(dict(game.get_player_list()))
            ))
            for player_id in recipients:
                self._pending.append(OutboundDelivery(
                    game_id, (player_id,), "player.deaths",
                    deepcopy(dict(game.get_player_deaths(player_id))),
                ))
                self._pending.append(OutboundDelivery(
                    game_id, (player_id,), "player.action_state",
                    deepcopy(dict(game.get_action_state(player_id))),
                ))

    def _game(self, game_id: str) -> DeliveryGame:
        try:
            game = self._games[game_id]
        except KeyError as error:
            raise ValueError(f"unknown game '{game_id}'") from error
        if not isinstance(game, DeliveryGame):
            raise TypeError(f"game '{game_id}' does not expose the delivery API")
        return game
