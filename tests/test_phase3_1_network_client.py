from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from ai_client.network import (
    ChatAction,
    ClientExitReason,
    ClientLifecycle,
    FileCredentialStore,
    NetworkClient,
    NetworkClientConfig,
    ProtocolMessageValidator,
    ProtocolValidationError,
    ReconnectPolicy,
    SequenceGapDetected,
    SequenceGapRecovered,
    SessionCheckpoint,
)


def server_event(message_type: str, game_id: str, seq: int, payload: dict[str, object]) -> str:
    return json.dumps(
        {
            "type": message_type,
            "protocol_version": "1.0",
            "event_id": str(uuid4()),
            "game_id": game_id,
            "seq": seq,
            "timestamp": 100,
            "payload": payload,
        }
    )


def state_sync_payload() -> dict[str, object]:
    return {
        "players": [{"player_id": "p0", "display_name": "P0"}],
        "deaths": [],
        "action_state": {
            "phase": "day",
            "day": 0,
            "phase_ends_at": None,
            "actions": [
                {"type": "chat", "channel": "public"},
                {
                    "type": "vote",
                    "valid_targets": ["p0"],
                    "target_count": 1,
                    "allows_abstain": True,
                },
            ],
        },
        "self": {"player_id": "p0", "role_id": "villager", "modifier_ids": []},
        "revealed_roles": [],
        "history": [],
    }


class MemoryStore:
    def __init__(self, checkpoint: SessionCheckpoint | None = None) -> None:
        self.checkpoint = checkpoint
        self.saved: list[SessionCheckpoint] = []

    async def load(self) -> SessionCheckpoint | None:
        return self.checkpoint

    async def save(self, checkpoint: SessionCheckpoint) -> None:
        self.checkpoint = checkpoint
        self.saved.append(checkpoint)


class FakeSocket:
    def __init__(self, incoming: list[str]) -> None:
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        for message in incoming:
            self.incoming.put_nowait(message)
        self.sent: list[dict[str, object]] = []
        self.closed = False

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str | None:
        return await self.incoming.get()

    async def close(self) -> None:
        self.closed = True
        if self.incoming.empty():
            self.incoming.put_nowait(None)

    async def wait_closed(self) -> None:
        return None


class NetworkClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_typed_send_uses_the_current_received_action_handle(self) -> None:
        game_id = "game-1"
        socket = FakeSocket([
            server_event("session.joined", game_id, 1, {
                "player_id": "p0",
                "connection_token": "connection-token",
            }),
            server_event("session.ready", game_id, 2, {
                "player_id": "p0",
                "ready": True,
            }),
            server_event("game.state_sync", game_id, 3, state_sync_payload()),
        ])
        client = NetworkClient(
            NetworkClientConfig("ws://fake", game_id, "entry-token"),
            MemoryStore(),
            connector=lambda _uri: socket,
        )
        run_task = asyncio.create_task(client.run())
        for _ in range(100):
            if client.lifecycle is ClientLifecycle.CONNECTED:
                break
            await asyncio.sleep(0.01)
        else:
            self.fail("client did not synchronize")

        action = next(item for item in client.snapshot().actions if isinstance(item, ChatAction))
        receipt = await client.send_chat(action, "hello")
        self.assertTrue(receipt.sent)
        self.assertEqual(socket.sent[-1]["type"], "chat.send")
        self.assertEqual(socket.sent[-1]["payload"], {
            "channel_id": "public",
            "message": "hello",
        })

        socket.incoming.put_nowait(server_event("game.event", game_id, 4, {
            "event_type": "GAME_ENDED",
            "event_payload": {},
        }))
        self.assertEqual((await run_task).reason, ClientExitReason.GAME_ENDED)

    async def test_join_sync_and_game_end_persist_every_server_event(self) -> None:
        game_id = "game-1"
        socket = FakeSocket(
            [
                server_event("session.joined", game_id, 1, {
                    "player_id": "p0",
                    "connection_token": "connection-token",
                }),
                server_event("session.ready", game_id, 2, {
                    "player_id": "p0",
                    "ready": True,
                }),
                server_event("game.state_sync", game_id, 3, state_sync_payload()),
                server_event("game.event", game_id, 4, {
                    "event_type": "GAME_ENDED",
                    "event_payload": {},
                }),
            ]
        )
        store = MemoryStore()
        client = NetworkClient(
            NetworkClientConfig("ws://fake", game_id, "entry-token"),
            store,
            connector=lambda _uri: socket,
        )

        result = await client.run()

        self.assertEqual(result.reason, ClientExitReason.GAME_ENDED)
        self.assertEqual(client.lifecycle, ClientLifecycle.ENDED)
        self.assertEqual(client.snapshot().last_seq, 4)
        self.assertEqual(client.snapshot().player_id, "p0")
        self.assertEqual([checkpoint.last_seq for checkpoint in store.saved], [1, 2, 3, 4])
        self.assertEqual([message["type"] for message in socket.sent], [
            "session.join", "session.ready",
        ])

    async def test_resume_uses_checkpoint_and_accepts_sync_barrier_seq_jump(self) -> None:
        game_id = "game-1"
        socket = FakeSocket([
            server_event("session.resumed", game_id, 6, {
                "player_id": "p0",
                "last_seq": 5,
            }),
            server_event("game.state_sync", game_id, 10, state_sync_payload()),
            server_event("game.event", game_id, 11, {
                "event_type": "GAME_ENDED",
                "event_payload": {},
            }),
        ])
        store = MemoryStore(SessionCheckpoint("connection-token", 5))
        client = NetworkClient(
            NetworkClientConfig("ws://fake", game_id, "entry-token"),
            store,
            connector=lambda _uri: socket,
        )

        result = await client.run()

        self.assertEqual(result.reason, ClientExitReason.GAME_ENDED)
        self.assertEqual(socket.sent[0]["type"], "session.resume")
        self.assertEqual(socket.sent[0]["payload"]["last_seq"], 5)
        self.assertEqual(client.snapshot().last_seq, 11)

    async def test_action_rejection_is_published_without_retry(self) -> None:
        game_id = "game-1"
        socket = FakeSocket([
            server_event("session.joined", game_id, 1, {
                "player_id": "p0",
                "connection_token": "connection-token",
            }),
            server_event("session.ready", game_id, 2, {
                "player_id": "p0",
                "ready": True,
            }),
            server_event("game.state_sync", game_id, 3, state_sync_payload()),
            server_event("action.rejected", game_id, 4, {
                "action": "vote.cast",
                "reason": "stale_action",
            }),
            server_event("game.event", game_id, 5, {
                "event_type": "GAME_ENDED",
                "event_payload": {},
            }),
        ])
        client = NetworkClient(
            NetworkClientConfig("ws://fake", game_id, "entry-token"),
            MemoryStore(),
            connector=lambda _uri: socket,
        )

        result = await client.run()
        events = []
        while not client._events.empty():  # noqa: SLF001 - drain public stream in test
            item = client._events.get_nowait()  # noqa: SLF001
            if item.__class__.__name__ != "object":
                events.append(item)

        self.assertEqual(result.reason, ClientExitReason.GAME_ENDED)
        self.assertEqual(len([event for event in events if event.__class__.__name__ == "ActionRejected"]), 1)
        self.assertEqual([message["type"] for message in socket.sent], [
            "session.join", "session.ready",
        ])

    async def test_file_store_round_trips_and_replaces_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seat.json"
            store = FileCredentialStore(path)
            await store.save(SessionCheckpoint("token", 7))
            self.assertEqual(await store.load(), SessionCheckpoint("token", 7))
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    async def test_gap_is_observed_and_sync_barrier_recovers_it(self) -> None:
        game_id = "game-1"
        first = FakeSocket([
            server_event("session.joined", game_id, 1, {
                "player_id": "p0",
                "connection_token": "connection-token",
            }),
            server_event("session.ready", game_id, 2, {
                "player_id": "p0",
                "ready": True,
            }),
            server_event("game.state_sync", game_id, 3, state_sync_payload()),
            server_event("game.event", game_id, 5, {
                "event_type": "PUBLIC_EVENT",
                "event_payload": {},
            }),
        ])
        second = FakeSocket([
            server_event("session.resumed", game_id, 4, {
                "player_id": "p0",
                "last_seq": 3,
            }),
            server_event("game.state_sync", game_id, 8, state_sync_payload()),
            server_event("game.event", game_id, 9, {
                "event_type": "GAME_ENDED",
                "event_payload": {},
            }),
        ])
        sockets = iter((first, second))
        store = MemoryStore()
        client = NetworkClient(
            NetworkClientConfig("ws://fake", game_id, "entry-token"),
            store,
            reconnect_policy=ReconnectPolicy(
                initial_delay_seconds=0,
                max_delay_seconds=0,
                max_disconnected_seconds=2,
            ),
            connector=lambda _uri: next(sockets),
        )
        observed: list[object] = []

        run_task = asyncio.create_task(client.run())
        async for event in client.events():
            observed.append(event)
        result = await run_task

        self.assertEqual(result.reason, ClientExitReason.GAME_ENDED)
        self.assertTrue(any(isinstance(event, SequenceGapDetected) for event in observed))
        self.assertTrue(any(isinstance(event, SequenceGapRecovered) for event in observed))
        self.assertEqual(second.sent[0]["type"], "session.resume")
        self.assertEqual(second.sent[0]["payload"]["last_seq"], 3)

    async def test_protocol_major_mismatch_fails_before_state_or_checkpoint_update(self) -> None:
        game_id = "game-1"
        raw = json.loads(server_event("session.joined", game_id, 1, {
            "player_id": "p0",
            "connection_token": "connection-token",
        }))
        raw["protocol_version"] = "2.0"
        socket = FakeSocket([json.dumps(raw)])
        store = MemoryStore()
        client = NetworkClient(
            NetworkClientConfig("ws://fake", game_id, "entry-token"),
            store,
            connector=lambda _uri: socket,
        )

        result = await client.run()

        self.assertEqual(result.reason, ClientExitReason.INCOMPATIBLE_PROTOCOL)
        self.assertEqual(store.saved, [])
        self.assertEqual(client.snapshot().player_id, None)

    async def test_action_rejected_payload_is_strictly_validated(self) -> None:
        raw = json.loads(server_event("action.rejected", "game-1", 1, {
            "action": "chat.send",
        }))

        with self.assertRaises(ProtocolValidationError):
            ProtocolMessageValidator().decode_server(json.dumps(raw))


if __name__ == "__main__":
    unittest.main()
