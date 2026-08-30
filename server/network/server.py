"""Async WebSocket server for the Phase 2.2 session boundary."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve

from .session import ConnectionContext, GameRegistry, SessionManager, TickDriver, UnaddressableRequest


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
                    reply, context = self.sessions.handle_json(raw_message, context)
                except UnaddressableRequest:
                    await websocket.close(code=1008, reason="invalid request")
                    return
                await websocket.send(
                    json.dumps(reply.as_message(), ensure_ascii=False, separators=(",", ":"))
                )
        finally:
            self.sessions.disconnect(context)

    async def _tick_forever(self) -> None:
        while True:
            await asyncio.sleep(self._tick_interval_seconds)
            self.ticker.advance_once()
