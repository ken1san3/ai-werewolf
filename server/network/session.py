"""Authenticated sessions, logical ticker, and server-event sequencing."""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from ..aiwolf_core.clock import timestamp

from .protocol import ProtocolMessageValidator, ProtocolValidationError


PROTOCOL_VERSION = "1.0"
Clock = Callable[[], int]
TokenFactory = Callable[[], str]
EventIdFactory = Callable[[], str]


class SessionGame(Protocol):
    """The narrow game-state surface required by the network boundary."""

    game_id: str
    players: Mapping[str, object]

    def advance_if_due(self, now: int) -> bool:
        ...


class UnaddressableRequest(ValueError):
    """A malformed request has no known game to receive a rejection event."""


@dataclass(frozen=True)
class ConnectionContext:
    """The authenticated seat bound to one live WebSocket connection."""

    connection_id: str
    game_id: str
    player_id: str
    connection_token: str


@dataclass(frozen=True)
class ServerReply:
    """One server event ready to serialize to a single WebSocket connection."""

    type: str
    game_id: str
    seq: int
    timestamp: int
    payload: Mapping[str, Any]
    event_id: str

    def as_message(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "protocol_version": PROTOCOL_VERSION,
            "event_id": self.event_id,
            "game_id": self.game_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
        }


def monotonic_seconds() -> int:
    """Return the server's integer monotonic clock for core deadlines and events."""

    return time.monotonic_ns() // 1_000_000_000


class GameRegistry:
    """Own the server-side set of games without making games connection-dependent."""

    def __init__(self, games: Mapping[str, SessionGame]) -> None:
        self._games = dict(games)
        for game_id, game in self._games.items():
            if game_id != game.game_id:
                raise ValueError("game registry key must match game.game_id")

    @property
    def games(self) -> Mapping[str, SessionGame]:
        return self._games

    def get(self, game_id: str) -> SessionGame:
        try:
            return self._games[game_id]
        except KeyError as error:
            raise UnaddressableRequest("unknown_game") from error

    def advance_if_due(self, now: int) -> Mapping[str, bool]:
        return {game_id: game.advance_if_due(now) for game_id, game in self._games.items()}


class _GameSession:
    """Per-game token store and outbound event sequence counter."""

    def __init__(
        self,
        game: SessionGame,
        *,
        clock: Clock,
        token_factory: TokenFactory,
        event_id_factory: EventIdFactory,
    ) -> None:
        self.game = game
        self._clock = clock
        self._token_factory = token_factory
        self._event_id_factory = event_id_factory
        self._tokens_by_player: dict[str, str] = {}
        self._players_by_token: dict[str, str] = {}
        self._connections: dict[str, set[str]] = {}
        self._ready_player_ids: set[str] = set()
        self._next_seq = 1

    @property
    def ready_player_ids(self) -> frozenset[str]:
        return frozenset(self._ready_player_ids)

    @property
    def connected_player_ids(self) -> frozenset[str]:
        return frozenset(player_id for player_id, connections in self._connections.items() if connections)

    def join(self, player_id: str) -> tuple[ConnectionContext, ServerReply]:
        token = self._new_token()
        self._tokens_by_player[player_id] = token
        self._players_by_token[token] = player_id
        context = self._connect(player_id, token)
        return context, self._reply(
            "session.joined",
            {"player_id": player_id, "connection_token": token},
        )

    def resume(
        self, connection_token: str, last_seq: int
    ) -> tuple[ConnectionContext | None, ServerReply]:
        player_id = self._players_by_token.get(connection_token)
        if player_id is None:
            return None, self._reply(
                "action.rejected",
                {"action": "session.resume", "reason": "invalid_connection_token"},
            )
        context = self._connect(player_id, connection_token)
        return context, self._reply(
            "session.resumed",
            {"player_id": player_id, "last_seq": last_seq},
        )

    def ready(self, context: ConnectionContext) -> ServerReply:
        self._ready_player_ids.add(context.player_id)
        return self._reply("session.ready", {"player_id": context.player_id, "ready": True})

    def has_joined(self, player_id: str) -> bool:
        return player_id in self._tokens_by_player

    def disconnect(self, context: ConnectionContext) -> None:
        connections = self._connections.get(context.player_id)
        if connections is None:
            return
        connections.discard(context.connection_id)
        if not connections:
            del self._connections[context.player_id]

    def rejected(self, action: str, reason: str) -> ServerReply:
        return self._reply("action.rejected", {"action": action, "reason": reason})

    def _connect(self, player_id: str, connection_token: str) -> ConnectionContext:
        context = ConnectionContext(
            connection_id=str(uuid4()),
            game_id=self.game.game_id,
            player_id=player_id,
            connection_token=connection_token,
        )
        self._connections.setdefault(player_id, set()).add(context.connection_id)
        return context

    def _new_token(self) -> str:
        token = self._token_factory()
        if not isinstance(token, str) or not token:
            raise ValueError("connection token factory must return a non-empty string")
        if token in self._players_by_token:
            raise ValueError("connection token factory returned a duplicate token")
        return token

    def _reply(self, message_type: str, payload: Mapping[str, Any]) -> ServerReply:
        reply = ServerReply(
            type=message_type,
            game_id=self.game.game_id,
            seq=self._next_seq,
            timestamp=timestamp(self._clock()),
            payload=payload,
            event_id=self._event_id_factory(),
        )
        self._next_seq += 1
        return reply

class SessionManager:
    """Handle Join, Ready, Resume, and disconnect without touching game rules."""

    def __init__(
        self,
        registry: GameRegistry,
        *,
        clock: Clock = monotonic_seconds,
        token_factory: TokenFactory | None = None,
        event_id_factory: EventIdFactory | None = None,
        validator: ProtocolMessageValidator | None = None,
    ) -> None:
        self.registry = registry
        self._validator = validator if validator is not None else ProtocolMessageValidator()
        make_token = token_factory if token_factory is not None else lambda: secrets.token_urlsafe(32)
        make_event_id = event_id_factory if event_id_factory is not None else lambda: str(uuid4())
        self._sessions = {
            game_id: _GameSession(
                game,
                clock=clock,
                token_factory=make_token,
                event_id_factory=make_event_id,
            )
            for game_id, game in registry.games.items()
        }

    def session_for(self, game_id: str) -> _GameSession:
        try:
            return self._sessions[game_id]
        except KeyError as error:
            raise UnaddressableRequest("unknown_game") from error

    def handle_json(
        self, raw_message: str, context: ConnectionContext | None = None
    ) -> tuple[ServerReply, ConnectionContext | None]:
        if not isinstance(raw_message, str):
            raise UnaddressableRequest("binary_messages_not_supported")
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError as error:
            raise UnaddressableRequest("invalid_json") from error
        if not isinstance(message, dict):
            raise UnaddressableRequest("message_must_be_an_object")
        return self.handle_message(message, context)

    def handle_message(
        self, message: Mapping[str, Any], context: ConnectionContext | None = None
    ) -> tuple[ServerReply, ConnectionContext | None]:
        game_id = message.get("game_id")
        if not isinstance(game_id, str):
            raise UnaddressableRequest("missing_game_id")
        session = self.session_for(game_id)
        action = message.get("type") if isinstance(message.get("type"), str) else "request"
        try:
            self._validator.validate_client(message)
        except ProtocolValidationError:
            return self._validated_reply(session.rejected(action, "invalid_message")), context

        if not _same_major_version(message["protocol_version"], PROTOCOL_VERSION):
            return self._validated_reply(
                session.rejected(action, "unsupported_protocol_version")
            ), context

        message_type = message["type"]
        if message_type == "session.join":
            if context is not None:
                return self._validated_reply(session.rejected(message_type, "already_authenticated")), context
            player_id = message["payload"]["player_id"]
            if player_id not in session.game.players:
                return self._validated_reply(session.rejected(message_type, "unknown_player")), context
            if session.has_joined(player_id):
                return self._validated_reply(session.rejected(message_type, "already_joined")), context
            new_context, reply = session.join(player_id)
            return self._validated_reply(reply), new_context

        if message_type == "session.resume":
            if context is not None:
                return self._validated_reply(session.rejected(message_type, "already_authenticated")), context
            new_context, reply = session.resume(
                message["payload"]["connection_token"], message["payload"]["last_seq"]
            )
            return self._validated_reply(reply), new_context

        if message_type == "session.ready":
            if context is None:
                return self._validated_reply(session.rejected(message_type, "not_authenticated")), context
            if context.game_id != game_id:
                own_session = self.session_for(context.game_id)
                return self._validated_reply(
                    own_session.rejected(message_type, "game_mismatch")
                ), context
            return self._validated_reply(session.ready(context)), context

        return self._validated_reply(session.rejected(message_type, "unsupported_action")), context

    def disconnect(self, context: ConnectionContext | None) -> None:
        if context is not None:
            self.session_for(context.game_id).disconnect(context)

    def _validated_reply(self, reply: ServerReply) -> ServerReply:
        self._validator.validate_server(reply.as_message())
        return reply


class TickDriver:
    """Drive every authoritative game from one injected monotonic server clock."""

    def __init__(self, registry: GameRegistry, *, clock: Clock = monotonic_seconds) -> None:
        self._registry = registry
        self._clock = clock

    def advance_once(self) -> Mapping[str, bool]:
        return self._registry.advance_if_due(timestamp(self._clock()))


def _same_major_version(client_version: str, server_version: str) -> bool:
    return client_version.partition(".")[0] == server_version.partition(".")[0]
