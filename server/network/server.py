"""Async WebSocket server for authenticated, visibility-filtered game events."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any, Mapping

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .delivery import EventDeliveryRouter, OutboundDelivery
from .protocol import ProtocolValidationError
from .session import ConnectionContext, GameRegistry, SessionManager, TickDriver, UnaddressableRequest


LOGGER = logging.getLogger(__name__)


class WebSocketGameServer:
    """Host authenticated sessions while the ticker advances games every second."""

    def __init__(
        self,
        registry: GameRegistry,
        *,
        sessions: SessionManager | None = None,
        ticker: TickDriver | None = None,
        tick_interval_seconds: float = 1.0,
    ) -> None:
        if tick_interval_seconds <= 0:
            raise ValueError("tick_interval_seconds must be positive")
        self.sessions = sessions if sessions is not None else SessionManager(registry)
        self.ticker = ticker if ticker is not None else TickDriver(registry)
        self._tick_interval_seconds = tick_interval_seconds
        self._server: Server | None = None
        self._tick_task: asyncio.Task[None] | None = None
        self._connections: dict[str, ServerConnection] = {}
        self._contexts: dict[str, ConnectionContext] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._delivery_router = EventDeliveryRouter(
            registry.games,
            connected_player_ids=lambda game_id: self.sessions.session_for(
                game_id
            ).connected_player_ids,
        )

    async def start(self, host: str, port: int) -> Server:
        if self._server is not None:
            raise RuntimeError("WebSocket server is already started")
        self._server = await serve(self._handle_connection, host, port)
        self._tick_task = asyncio.create_task(self._tick_forever())
        return self._server

    async def close(self) -> None:
        if self._tick_task is not None:
            self._tick_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._tick_task
            self._tick_task = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        context: ConnectionContext | None = None
        try:
            async for raw_message in websocket:
                try:
                    if context is None:
                        result = self.sessions.handle_json(raw_message, None)
                        context = result.context
                        if result.reply is not None:
                            await self._send_reply(websocket, result.reply)
                        if context is not None:
                            self._register_connection(context, websocket)
                    else:
                        lock = self._send_locks[context.connection_id]
                        async with lock:
                            result = self.sessions.handle_json(raw_message, context)
                            if result.reply is not None:
                                await self._send_reply(websocket, result.reply)
                except UnaddressableRequest:
                    await websocket.close(code=1008, reason="invalid request")
                    return
                except ProtocolValidationError:
                    LOGGER.exception("server generated an invalid protocol message")
                    await websocket.close(code=1011, reason="server protocol error")
                    return
                await self._close_replaced_connections(result.replaced_connection_ids)
                for channel_id, message in result.channel_messages:
                    await self.publish_channel_message(context.game_id, channel_id, message)
                await self._flush_outbound_deliveries()
        except ConnectionClosed:
            pass
        finally:
            self._unregister_connection(context)

    async def publish_channel_message(
        self, game_id: str, channel_id: str, message: Mapping[str, Any]
    ) -> None:
        """Deliver an already-accepted chat payload through its content channel."""

        self._delivery_router.queue_channel_message(game_id, channel_id, message)
        await self._flush_outbound_deliveries()

    def _register_connection(
        self, context: ConnectionContext, websocket: ServerConnection
    ) -> None:
        self._connections[context.connection_id] = websocket
        self._contexts[context.connection_id] = context
        self._send_locks[context.connection_id] = asyncio.Lock()

    def _unregister_connection(self, context: ConnectionContext | None) -> None:
        if context is None:
            return
        self._connections.pop(context.connection_id, None)
        self._contexts.pop(context.connection_id, None)
        self._send_locks.pop(context.connection_id, None)
        self.sessions.disconnect(context)

    async def _send_reply(self, websocket: ServerConnection, reply: Any) -> None:
        await websocket.send(
            json.dumps(reply.as_message(), ensure_ascii=False, separators=(",", ":"))
        )

    async def _close_replaced_connections(self, connection_ids: tuple[str, ...]) -> None:
        for connection_id in connection_ids:
            previous = self._connections.pop(connection_id, None)
            previous_context = self._contexts.pop(connection_id, None)
            self._send_locks.pop(connection_id, None)
            self.sessions.disconnect(previous_context)
            if previous is not None:
                await previous.close(code=4001, reason="session resumed elsewhere")

    async def _flush_outbound_deliveries(self) -> None:
        for delivery in self._delivery_router.drain():
            for context in tuple(self._contexts.values()):
                if (
                    context.game_id == delivery.game_id
                    and context.player_id in delivery.recipient_player_ids
                ):
                    await self._deliver(context, delivery)

    async def _deliver(self, context: ConnectionContext, delivery: OutboundDelivery) -> None:
        websocket = self._connections.get(context.connection_id)
        lock = self._send_locks.get(context.connection_id)
        if websocket is None or lock is None:
            return
        try:
            async with lock:
                if self._contexts.get(context.connection_id) != context:
                    return
                reply = self.sessions.server_event(
                    context, delivery.message_type, delivery.payload
                )
                await self._send_reply(websocket, reply)
        except ProtocolValidationError:
            LOGGER.exception("server generated an invalid protocol message")
            self._unregister_connection(context)
            await websocket.close(code=1011, reason="server protocol error")
        except ConnectionClosed:
            self._unregister_connection(context)

    async def _tick_forever(self) -> None:
        while True:
            await asyncio.sleep(self._tick_interval_seconds)
            try:
                self.ticker.advance_once()
                await self._flush_outbound_deliveries()
            except Exception:
                LOGGER.exception("unexpected error in game tick")
