"""Async WebSocket server for the Phase 2.2 session boundary."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

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
                    result = self.sessions.handle_json(raw_message, context)
                except UnaddressableRequest:
                    await websocket.close(code=1008, reason="invalid request")
                    return
                except ProtocolValidationError:
                    LOGGER.exception("server generated an invalid protocol message")
                    await websocket.close(code=1011, reason="server protocol error")
                    return
                context = result.context
                if context is not None:
                    self._connections[context.connection_id] = websocket
                await self._close_replaced_connections(result.replaced_connection_ids)
                await websocket.send(
                    json.dumps(result.reply.as_message(), ensure_ascii=False, separators=(",", ":"))
                )
        except ConnectionClosed:
            pass
        finally:
            if context is not None:
                self._connections.pop(context.connection_id, None)
            self.sessions.disconnect(context)

    async def _close_replaced_connections(self, connection_ids: tuple[str, ...]) -> None:
        for connection_id in connection_ids:
            previous = self._connections.pop(connection_id, None)
            if previous is not None:
                await previous.close(code=4001, reason="session resumed elsewhere")

    async def _tick_forever(self) -> None:
        while True:
            await asyncio.sleep(self._tick_interval_seconds)
            try:
                self.ticker.advance_once()
            except Exception:
                LOGGER.exception("unexpected error in game tick")
