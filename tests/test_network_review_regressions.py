from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from server.aiwolf_core import EventVisibility, GameEvent
from server.network import GameRegistry, SessionManager, TickDriver, WebSocketGameServer
from tests.test_network_sessions import GAME_ID, client_message, join_message, make_game


async def receive(socket):
    return json.loads(await asyncio.wait_for(socket.recv(), timeout=2))


async def join(socket, registry, player_id, *, game_id=GAME_ID):
    await socket.send(json.dumps(join_message(registry, player_id, game_id=game_id)))
    return await receive(socket), await receive(socket)


class EntryTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_join_requires_single_use_entry_credentials_and_resume_requires_connection_credentials(self):
        game = make_game()
        registry = GameRegistry({GAME_ID: game, "other-game": make_game("other-game")})
        server = WebSocketGameServer(registry, tick_interval_seconds=3600)
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        entry = registry.entry_tokens_for(GAME_ID)["player-0"]

        async def rejected(message):
            async with connect(uri) as socket:
                await socket.send(json.dumps(message))
                with self.assertRaises(ConnectionClosed):
                    await receive(socket)
                self.assertEqual(socket.close_code, 1008)

        try:
            for payload in ({}, {"player_id": "player-0"}, {"entry_token": "wrong"},
                            {"entry_token": entry, "player_id": "player-1"}):
                await rejected(client_message("session.join", payload))
                self.assertFalse(server.sessions.session_for(GAME_ID).has_joined("player-0"))
            await rejected(client_message("session.join", {"entry_token": entry}, game_id="other-game"))
            async with connect(uri) as socket:
                joined, sync = await join(socket, registry, "player-0")
                connection = joined["payload"]["connection_token"]
                self.assertNotEqual(entry, connection)
                self.assertEqual(sync["payload"]["self"]["player_id"], "player-0")
                self.assertEqual(sync["payload"]["self"]["role_id"], game.players["player-0"].role.id)
                self.assertNotIn(entry, json.dumps([joined, sync]))
                self.assertNotIn(entry, repr(game.event_bus.events))
                await rejected(join_message(registry, "player-0"))
                await rejected(client_message("session.resume", {"connection_token": entry, "last_seq": sync["seq"]}))
                await rejected(client_message("session.join", {"entry_token": connection}))
                async with connect(uri) as replacement:
                    await replacement.send(json.dumps(client_message("session.resume", {
                        "connection_token": connection, "last_seq": sync["seq"],
                    })))
                    self.assertEqual((await receive(replacement))["type"], "session.resumed")
                    restored = await receive(replacement)
                    self.assertEqual(restored["payload"], sync["payload"])
                    await socket.wait_closed()
                    self.assertEqual(socket.close_code, 4001)
        finally:
            await server.close()

    def test_registry_issues_distinct_credentials_and_exposes_a_read_only_launcher_map(self):
        registry = GameRegistry({GAME_ID: make_game(), "other": make_game("other")})
        entries = [token for game_id in registry.games for token in registry.entry_tokens_for(game_id).values()]
        self.assertEqual(len(entries), len(set(entries)))
        self.assertTrue(all(len(token) >= 43 for token in entries))
        with self.assertRaises(TypeError):
            registry.entry_tokens_for(GAME_ID)["player-0"] = "changed"
        with self.assertRaises(ValueError):
            GameRegistry({GAME_ID: make_game()}, entry_token_factory=lambda: "duplicate")


class ReplayRetentionTests(unittest.TestCase):
    def setUp(self):
        self.game = make_game()
        self.registry = GameRegistry({GAME_ID: self.game})
        self.manager = SessionManager(self.registry, clock=lambda: 0, replay_history_limit=3)
        self.joined = self.manager.handle_message(join_message(self.registry, "player-0"))
        self.sync = self.manager.state_sync(self.joined.context)

    def resume(self, last_seq):
        result = self.manager.handle_message(client_message("session.resume", {
            "connection_token": self.joined.context.connection_token, "last_seq": last_seq,
        }))
        return result, self.manager.state_sync(result.context)

    def test_reconnects_do_not_accumulate_snapshots_or_acknowledged_history(self):
        last_seq = self.sync.seq
        original = self.sync.payload
        for _ in range(30):
            resumed, sync = self.resume(last_seq)
            self.assertEqual(resumed.replay, ())
            self.assertEqual(sync.payload, original)
            self.assertEqual(list(self.manager.session_for(GAME_ID)._history_by_player["player-0"]), [])
            last_seq = sync.seq
        # If the previous snapshot was lost, issue a new one rather than replaying it.
        resumed, sync = self.resume(0)
        self.assertEqual(resumed.replay, ())
        self.assertEqual(sync.payload, original)

    def test_recent_replay_is_preserved_and_expired_cursor_recovers_from_fresh_snapshot(self):
        replies = []
        for index in range(10):
            event = GameEvent("OBSERVED", EventVisibility.PUBLIC, {"index": index})
            self.game.event_bus.publish(event)
            replies.append(self.manager.server_event(self.joined.context, "game.event", {
                "event_type": event.type, "event_payload": dict(event.payload),
            }))
        history = self.manager.session_for(GAME_ID)._history_by_player["player-0"]
        self.assertEqual(list(history), replies[-3:])
        resumed, sync = self.resume(replies[-4].seq)
        self.assertEqual(list(resumed.replay), replies[-3:])
        self.assertEqual(sync.payload, self.game.get_state_sync("player-0"))
        resumed, sync = self.resume(0)
        self.assertEqual(resumed.replay, ())
        observed = [entry["payload"]["event_payload"]["index"] for entry in sync.payload["history"]
                    if entry["payload"].get("event_type") == "OBSERVED"]
        self.assertEqual(observed, list(range(10)))


class SlowConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_outbox_closes_only_the_slow_connection_and_can_restore_with_sync(self):
        game = make_game()
        registry = GameRegistry({GAME_ID: game})
        server = WebSocketGameServer(registry, tick_interval_seconds=3600, max_pending_messages=2)
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        original_send = server._send_reply
        blocked = asyncio.Event()
        try:
            async with connect(uri) as slow, connect(uri) as fast:
                joined, sync = await join(slow, registry, "player-0")
                slow_connection = next(iter(server._connections.values()))
                await join(fast, registry, "player-1")

                async def slow_send(socket, reply):
                    if socket is slow_connection:
                        blocked.set()
                        await asyncio.Event().wait()
                    await original_send(socket, reply)

                with patch.object(server, "_send_reply", slow_send):
                    for index in range(4):
                        await server.publish_channel_message(GAME_ID, "public", {"index": index})
                        self.assertEqual((await receive(fast))["payload"]["message"]["index"], index)
                        if index == 0:
                            await asyncio.wait_for(blocked.wait(), 1)
                    await slow.wait_closed()
                    self.assertEqual(slow.close_code, 1013)
                    self.assertEqual(server.sessions.session_for(GAME_ID).connection_count("player-1"), 1)
                # The next snapshot replaces queued/lost messages without retaining copies.
                async with connect(uri) as replacement:
                    await replacement.send(json.dumps(client_message("session.resume", {
                        "connection_token": joined["payload"]["connection_token"], "last_seq": 0,
                    })))
                    self.assertEqual((await receive(replacement))["type"], "session.resumed")
                    restored = await receive(replacement)
                    chats = [entry["payload"]["message"]["index"] for entry in restored["payload"]["history"]
                             if entry["type"] == "chat.message"]
                    self.assertEqual(chats, list(range(4)))
        finally:
            await server.close()

    async def test_one_second_send_delay_does_not_block_either_games_ticks_or_other_connections(self):
        first, second = make_game(), make_game("game-b")
        registry = GameRegistry({GAME_ID: first, "game-b": second})
        server = WebSocketGameServer(registry, ticker=TickDriver(registry, clock=lambda: 0),
                                     tick_interval_seconds=0.01)
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        original_send = server._send_reply
        blocked = asyncio.Event()

        async def slow_send(socket, reply):
            if reply.type == "session.joined" and reply.game_id == GAME_ID and reply.payload["player_id"] == "player-0":
                blocked.set()
                await asyncio.sleep(1)
            await original_send(socket, reply)

        try:
            with patch.object(server, "_send_reply", slow_send), \
                 patch.object(first, "advance_if_due", wraps=first.advance_if_due) as first_tick, \
                 patch.object(second, "advance_if_due", wraps=second.advance_if_due) as second_tick:
                async with connect(uri) as slow, connect(uri) as same_game, connect(uri) as other_game:
                    await slow.send(json.dumps(join_message(registry, "player-0")))
                    await asyncio.wait_for(blocked.wait(), 1)
                    counts = (first_tick.call_count, second_tick.call_count)
                    for socket, game_id in ((same_game, GAME_ID), (other_game, "game-b")):
                        await asyncio.wait_for(join(socket, registry, "player-1", game_id=game_id), 0.3)
                        await socket.send(json.dumps(client_message("session.ready", {}, game_id=game_id)))
                        ready = await asyncio.wait_for(receive(socket), 0.3)
                        self.assertEqual(ready["type"], "session.ready")
                    await asyncio.sleep(0.5)
                    self.assertGreaterEqual(first_tick.call_count - counts[0], 20)
                    self.assertGreaterEqual(second_tick.call_count - counts[1], 20)
                    self.assertEqual((await receive(slow))["seq"], 1)
                    self.assertEqual((await receive(slow))["seq"], 2)
        finally:
            await server.close()

    async def test_pending_send_is_cancelled_on_resume_with_authentication_before_replay(self):
        game = make_game()
        registry = GameRegistry({GAME_ID: game})
        server = WebSocketGameServer(registry, ticker=TickDriver(registry, clock=lambda: 0), tick_interval_seconds=0.01)
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        blocked = asyncio.Event()
        original_send = server._send_reply
        try:
            async with connect(uri) as old:
                joined, sync = await join(old, registry, "player-0")
                old_connection = next(iter(server._connections.values()))

                async def slow_send(socket, reply):
                    if socket is old_connection and reply.type == "game.event":
                        blocked.set()
                        await asyncio.Event().wait()
                    await original_send(socket, reply)

                with patch.object(server, "_send_reply", slow_send):
                    game.event_bus.publish(GameEvent("QUEUED", EventVisibility.PUBLIC, {}))
                    await asyncio.wait_for(blocked.wait(), 1)
                    async with connect(uri) as replacement:
                        await replacement.send(json.dumps(client_message("session.resume", {
                            "connection_token": joined["payload"]["connection_token"], "last_seq": sync["seq"],
                        })))
                        messages = [await receive(replacement) for _ in range(3)]
                        self.assertEqual([m["type"] for m in messages], ["game.event", "session.resumed", "game.state_sync"])
                        self.assertEqual([m["seq"] for m in messages], [3, 4, 5])
                        await old.wait_closed()
                        self.assertEqual(old.close_code, 4001)
        finally:
            await asyncio.wait_for(server.close(), 2)
        self.assertFalse(server._writers)
        self.assertFalse(server._outboxes)
