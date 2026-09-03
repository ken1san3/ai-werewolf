"""Async WebSocket server for authenticated, visibility-filtered game events."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any, Coroutine, Mapping

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .delivery import EventDeliveryRouter, OutboundDelivery
from .protocol import ProtocolValidationError
from .session import ConnectionContext, GameRegistry, ServerReply, SessionManager, TickDriver, UnaddressableRequest


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
        max_pending_messages: int = 256,
    ) -> None:
        if tick_interval_seconds <= 0:
            raise ValueError("tick_interval_seconds must be positive")
        if max_pending_messages < 2:
            raise ValueError("max_pending_messages must accommodate the acknowledgement and state sync")
        self.sessions = sessions if sessions is not None else SessionManager(registry)
        self.ticker = ticker if ticker is not None else TickDriver(registry)
        self._tick_interval_seconds = tick_interval_seconds
        self._server: Server | None = None
        self._tick_task: asyncio.Task[None] | None = None
        self._connections: dict[str, ServerConnection] = {}
        self._contexts: dict[str, ConnectionContext] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._outboxes: dict[str, asyncio.Queue[ServerReply]] = {}
        self._writers: dict[str, asyncio.Task[None]] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._max_pending_messages = max_pending_messages
        self._dispatch_lock = asyncio.Lock()
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
            for context in tuple(self._contexts.values()):
                self._unregister_connection(context)
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        tasks = tuple(self._background_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        context: ConnectionContext | None = None
        try:
            async for raw_message in websocket:
                try:
                    async with self._dispatch_lock:
                        # Only synchronous state capture and queueing happen under this lock.
                        self._flush_outbound_deliveries()
                        if context is None:
                            result = self.sessions.handle_json(raw_message, None)
                            context = result.context
                            sync = self.sessions.state_sync(context) if context is not None else None
                            self._close_replaced_connections(result.replaced_connection_ids)
                            if context is not None:
                                self._register_connection(context, websocket)
                            if result.reply is not None:
                                self._enqueue(context, result.reply)
                            for reply in result.replay:
                                self._enqueue(context, reply)
                            if sync is not None:
                                self._enqueue(context, sync)
                        else:
                            if context.connection_id not in self._contexts:
                                raise UnaddressableRequest("session_replaced")
                            result = self.sessions.handle_json(raw_message, context)
                            if result.reply is not None:
                                self._enqueue(context, result.reply)
                        for channel_id, message in result.channel_messages:
                            self._delivery_router.queue_channel_message(context.game_id, channel_id, message)
                        self._flush_outbound_deliveries()
                except UnaddressableRequest:
                    await websocket.close(code=1008, reason="invalid request")
                    return
                except ProtocolValidationError:
                    LOGGER.exception("server generated an invalid protocol message")
                    await websocket.close(code=1011, reason="server protocol error")
                    return
        except ConnectionClosed:
            pass
        finally:
            self._unregister_connection(context)

    async def publish_channel_message(
        self, game_id: str, channel_id: str, message: Mapping[str, Any]
    ) -> None:
        """Deliver an already-accepted chat payload through its content channel."""

        async with self._dispatch_lock:
            self.sessions.registry.get(game_id).record_channel_message(channel_id, message)
            self._delivery_router.queue_channel_message(game_id, channel_id, message)
            self._flush_outbound_deliveries()

    def _register_connection(
        self, context: ConnectionContext, websocket: ServerConnection
    ) -> None:
        self._connections[context.connection_id] = websocket
        self._contexts[context.connection_id] = context
        self._send_locks[context.connection_id] = asyncio.Lock()
        self._outboxes[context.connection_id] = asyncio.Queue(maxsize=self._max_pending_messages)
        self._writers[context.connection_id] = self._start_task(self._send_forever(context, websocket))

    def _unregister_connection(self, context: ConnectionContext | None) -> None:
        if context is None:
            return
        self._connections.pop(context.connection_id, None)
        self._contexts.pop(context.connection_id, None)
        self._send_locks.pop(context.connection_id, None)
        self._outboxes.pop(context.connection_id, None)
        writer = self._writers.pop(context.connection_id, None)
        if writer is not None and writer is not asyncio.current_task():
            writer.cancel()
        self.sessions.disconnect(context)

    def _start_task(self, coroutine: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            LOGGER.error("network background task failed", exc_info=(type(error), error, error.__traceback__))

    def _schedule_close(self, websocket: ServerConnection, code: int, reason: str) -> None:
        self._start_task(websocket.close(code=code, reason=reason))

    def _enqueue(self, context: ConnectionContext, reply: ServerReply) -> None:
        queue = self._outboxes.get(context.connection_id)
        if queue is None:
            return
        try:
            queue.put_nowait(reply)
        except asyncio.QueueFull:
            websocket = self._connections.get(context.connection_id)
            self._unregister_connection(context)
            if websocket is not None:
                self._schedule_close(websocket, 1013, "outbound queue full; resume required")

    async def _send_forever(self, context: ConnectionContext, websocket: ServerConnection) -> None:
        queue = self._outboxes[context.connection_id]
        lock = self._send_locks[context.connection_id]
        try:
            while True:
                reply = await queue.get()
                async with lock:
                    await self._send_reply(websocket, reply)
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("outbound writer failed")
            self._schedule_close(websocket, 1011, "server send error")
        finally:
            self._unregister_connection(context)

    async def _send_reply(self, websocket: ServerConnection, reply: Any) -> None:
        await websocket.send(
            json.dumps(reply.as_message(), ensure_ascii=False, separators=(",", ":"))
        )

    def _close_replaced_connections(self, connection_ids: tuple[str, ...]) -> None:
        for connection_id in connection_ids:
            previous = self._connections.get(connection_id)
            self._unregister_connection(self._contexts.get(connection_id))
            if previous is not None:
                self._schedule_close(previous, 4001, "session resumed elsewhere")

    def _flush_outbound_deliveries(self) -> None:
        for delivery in self._delivery_router.drain():
            for context in tuple(self._contexts.values()):
                if (
                    context.game_id == delivery.game_id
                    and context.player_id in delivery.recipient_player_ids
                ):
                    self._deliver(context, delivery)

    def _deliver(self, context: ConnectionContext, delivery: OutboundDelivery) -> None:
        websocket = self._connections.get(context.connection_id)
        if websocket is None:
            return
        try:
            reply = self.sessions.server_event(context, delivery.message_type, delivery.payload)
            self._enqueue(context, reply)
        except ProtocolValidationError:
            LOGGER.exception("server generated an invalid protocol message")
            self._unregister_connection(context)
            self._schedule_close(websocket, 1011, "server protocol error")

    async def _tick_forever(self) -> None:
        while True:
            await asyncio.sleep(self._tick_interval_seconds)
            try:
                async with self._dispatch_lock:
                    self.ticker.advance_once()
                    self._flush_outbound_deliveries()
            except Exception:
                LOGGER.exception("unexpected error in game tick")
