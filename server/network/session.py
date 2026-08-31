"""Authenticated sessions, logical ticker, and server-event sequencing."""

from __future__ import annotations

import json
import logging
import secrets
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from ..aiwolf_core.clock import timestamp
from ..aiwolf_core.rejections import ActionRejected

from .protocol import ProtocolMessageValidator, ProtocolValidationError


PROTOCOL_VERSION = "1.0"
Clock = Callable[[], int]
TokenFactory = Callable[[], str]
EventIdFactory = Callable[[], str]
LOGGER = logging.getLogger(__name__)


class SessionGame(Protocol):
    """The narrow game-state surface required by the network boundary."""

    game_id: str
    players: Mapping[str, object]

    def advance_if_due(self, now: int) -> bool:
        ...

    def get_state_sync(self, player_id: str) -> Mapping[str, Any]:
        ...

    def record_channel_message(self, channel_id: str, message: Mapping[str, Any]) -> None:
        ...

    def submit_chat(self, player_id: str, channel_id: str, message: str) -> Any:
        ...

    def submit_vote(self, voter_player_id: str, target_player_id: str | None) -> None:
        ...

    def submit_action(
        self, now: int, actor_player_id: str, ability_id: str, target_player_ids: tuple[str, ...]
    ) -> None:
        ...

    def declare_co(self, player_id: str, claimed_role_id: str, comment: str) -> None:
        ...

    def report_co(
        self, player_id: str, kind: str, target_player_id: str, claimed_result: str
    ) -> None:
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


@dataclass(frozen=True)
class SessionResult:
    """Optional direct reply and accepted chat outputs for one authenticated request."""

    reply: ServerReply | None
    context: ConnectionContext | None
    replaced_connection_ids: tuple[str, ...] = ()
    channel_messages: tuple[tuple[str, Mapping[str, Any]], ...] = ()
    replay: tuple[ServerReply, ...] = ()


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

class _GameSession:
    """Per-game token store and per-player outbound event sequence counters."""

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
        self._next_seq_by_player: dict[str, int] = {}
        self._history_by_player: dict[str, list[ServerReply]] = {}

    @property
    def ready_player_ids(self) -> frozenset[str]:
        return frozenset(self._ready_player_ids)

    @property
    def connected_player_ids(self) -> frozenset[str]:
        return frozenset(player_id for player_id, connections in self._connections.items() if connections)

    def connection_count(self, player_id: str) -> int:
        return len(self._connections.get(player_id, ()))

    def join(self, player_id: str) -> tuple[ConnectionContext, ServerReply]:
        token = self._new_token()
        self._tokens_by_player[player_id] = token
        self._players_by_token[token] = player_id
        context = self._connect(player_id, token)
        return context, self._reply(
            player_id,
            "session.joined",
            {"player_id": player_id, "connection_token": token},
        )

    def resume(
        self, connection_token: str, last_seq: int
    ) -> tuple[ConnectionContext, ServerReply, tuple[str, ...], tuple[ServerReply, ...]]:
        player_id = self._players_by_token.get(connection_token)
        if player_id is None:
            raise UnaddressableRequest("invalid_connection_token")
        if last_seq >= self._next_seq_by_player.get(player_id, 1):
            raise UnaddressableRequest("invalid_last_seq")
        replay = tuple(
            deepcopy(reply) for reply in self._history_by_player.get(player_id, ())
            if reply.seq > last_seq
        )
        replaced_connection_ids = tuple(self._connections.pop(player_id, ()))
        context = self._connect(player_id, connection_token)
        return context, self._reply(
            player_id,
            "session.resumed",
            {"player_id": player_id, "last_seq": last_seq},
        ), replaced_connection_ids, replay

    def ready(self, context: ConnectionContext) -> ServerReply:
        self._ready_player_ids.add(context.player_id)
        return self._reply(
            context.player_id,
            "session.ready",
            {"player_id": context.player_id, "ready": True},
        )

    def has_joined(self, player_id: str) -> bool:
        return player_id in self._tokens_by_player

    def disconnect(self, context: ConnectionContext) -> None:
        connections = self._connections.get(context.player_id)
        if connections is None:
            return
        connections.discard(context.connection_id)
        if not connections:
            del self._connections[context.player_id]

    def rejected(self, context: ConnectionContext, action: str, reason: str) -> ServerReply:
        return self._reply(
            context.player_id,
            "action.rejected",
            {"action": action, "reason": reason},
        )

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

    def _reply(
        self, player_id: str, message_type: str, payload: Mapping[str, Any]
    ) -> ServerReply:
        next_seq = self._next_seq_by_player.get(player_id, 1)
        reply = ServerReply(
            type=message_type,
            game_id=self.game.game_id,
            seq=next_seq,
            timestamp=timestamp(self._clock()),
            payload=deepcopy(dict(payload)),
            event_id=self._event_id_factory(),
        )
        self._next_seq_by_player[player_id] = next_seq + 1
        return reply

class SessionManager:
    """Authenticate requests and delegate every gameplay decision to the core."""

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
        self._clock = clock
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
    ) -> SessionResult:
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
    ) -> SessionResult:
        action = message.get("type") if isinstance(message.get("type"), str) else "request"
        if context is None:
            game_id = message.get("game_id")
            if not isinstance(game_id, str):
                raise UnaddressableRequest("missing_game_id")
            session = self.session_for(game_id)
            try:
                self._validator.validate_client(message)
            except ProtocolValidationError as error:
                raise UnaddressableRequest("invalid_message") from error
            if not _same_major_version(message["protocol_version"], PROTOCOL_VERSION):
                raise UnaddressableRequest("unsupported_protocol_version")
            message_type = message["type"]
            if message_type == "session.join":
                player_id = message["payload"]["player_id"]
                if player_id not in session.game.players:
                    raise UnaddressableRequest("unknown_player")
                if session.has_joined(player_id):
                    raise UnaddressableRequest("already_joined")
                new_context, reply = session.join(player_id)
                return self._result(reply, new_context)
            if message_type == "session.resume":
                new_context, reply, replaced_connection_ids, replay = session.resume(
                    message["payload"]["connection_token"], message["payload"]["last_seq"]
                )
                return self._result(reply, new_context, replaced_connection_ids, replay=replay)
            raise UnaddressableRequest("not_authenticated")

        session = self.session_for(context.game_id)
        try:
            self._validator.validate_client(message)
        except ProtocolValidationError:
            return self._result(session.rejected(context, action, "invalid_message"), context)

        if message["game_id"] != context.game_id:
            return self._result(session.rejected(context, action, "game_mismatch"), context)
        if not _same_major_version(message["protocol_version"], PROTOCOL_VERSION):
            return self._result(
                session.rejected(context, action, "unsupported_protocol_version"), context
            )

        message_type = message["type"]
        if message_type == "session.ready":
            return self._result(session.ready(context), context)
        try:
            channel_messages = self._dispatch_game_action(
                session.game, context.player_id, message_type, message["payload"]
            )
        except ActionRejected as error:
            return self._result(session.rejected(context, message_type, error.reason), context)
        except ValueError:
            return self._result(session.rejected(context, message_type, "invalid_action"), context)
        if channel_messages is None:
            return self._result(None, context)
        return self._result(None, context, channel_messages=channel_messages)

    def disconnect(self, context: ConnectionContext | None) -> None:
        if context is not None:
            self.session_for(context.game_id).disconnect(context)

    def server_event(
        self, context: ConnectionContext, message_type: str, payload: Mapping[str, Any]
    ) -> ServerReply:
        """Envelope a pre-filtered outbound event on the player's sequence stream."""

        session = self.session_for(context.game_id)
        return self._validated_reply(session._reply(context.player_id, message_type, payload), context)

    def state_sync(self, context: ConnectionContext) -> ServerReply:
        game = self.registry.get(context.game_id)
        return self.server_event(context, "game.state_sync", game.get_state_sync(context.player_id))

    def _result(
        self,
        reply: ServerReply | None,
        context: ConnectionContext | None,
        replaced_connection_ids: tuple[str, ...] = (),
        channel_messages: tuple[tuple[str, Mapping[str, Any]], ...] = (),
        replay: tuple[ServerReply, ...] = (),
    ) -> SessionResult:
        validated_reply = self._validated_reply(reply, context) if reply is not None else None
        return SessionResult(validated_reply, context, replaced_connection_ids, channel_messages, replay)

    def _dispatch_game_action(
        self,
        game: SessionGame,
        player_id: str,
        message_type: str,
        raw_payload: object,
    ) -> tuple[tuple[str, Mapping[str, Any]], ...] | None:
        """Route typed protocol data while leaving action legality to the core service."""

        if not isinstance(raw_payload, Mapping):
            raise ActionRejected("invalid_message")
        if message_type == "chat.send":
            submission = game.submit_chat(player_id, raw_payload["channel_id"], raw_payload["message"])
            return ((submission.channel_id, submission.message),)
        if message_type == "vote.cast":
            game.submit_vote(player_id, raw_payload["target_player_id"])
            return None
        if message_type == "ability.use":
            game.submit_action(
                timestamp(self._clock()),
                player_id,
                raw_payload["ability_id"],
                tuple(raw_payload["target_player_ids"]),
            )
            return None
        if message_type == "co.declare":
            game.declare_co(player_id, raw_payload["claimed_role_id"], raw_payload["comment"])
            return None
        if message_type == "co.report":
            game.report_co(
                player_id,
                raw_payload["kind"],
                raw_payload["target_player_id"],
                raw_payload["claimed_result"],
            )
            return None
        raise ActionRejected("unsupported_action")

    def _validated_reply(
        self, reply: ServerReply, context: ConnectionContext | None
    ) -> ServerReply:
        try:
            self._validator.validate_server(reply.as_message())
        except ProtocolValidationError:
            self.disconnect(context)
            raise
        if context is not None:
            session = self.session_for(context.game_id)
            session._history_by_player.setdefault(context.player_id, []).append(deepcopy(reply))
        return reply


class TickDriver:
    """Drive every authoritative game from one injected monotonic server clock."""

    def __init__(
        self,
        registry: GameRegistry,
        *,
        clock: Clock = monotonic_seconds,
        logger: logging.Logger = LOGGER,
    ) -> None:
        self._registry = registry
        self._clock = clock
        self._logger = logger

    def advance_once(self) -> Mapping[str, bool]:
        now = timestamp(self._clock())
        results = {game_id: False for game_id in self._registry.games}
        for game_id, game in self._registry.games.items():
            try:
                results[game_id] = game.advance_if_due(now)
            except Exception:
                self._logger.exception("tick failed for game '%s'", game_id)
        return results


def _same_major_version(client_version: str, server_version: str) -> bool:
    return client_version.partition(".")[0] == server_version.partition(".")[0]
