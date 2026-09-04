from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

from ai_client.network import (
    AbilityAction,
    ChatAction,
    ClientExitReason,
    ClientLifecycle,
    CoDeclareAction,
    CoReportAction,
    CredentialError,
    DeliveryUnknownError,
    FileCredentialStore,
    GameEnded,
    NetworkClient,
    NetworkClientConfig,
    NotDeliveredError,
    PhaseDeadlineReached,
    ProtocolMessageValidator,
    ProtocolValidationError,
    ReconnectPolicy,
    ServerEvent,
    SequenceGapDetected,
    SequenceGapRecovered,
    SessionCheckpoint,
    StaleActionError,
    VoteAction,
)
from ai_client.network.client import _TransientFailure


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


def rich_state_sync_payload() -> dict[str, object]:
    payload = state_sync_payload()
    players = payload["players"]
    assert isinstance(players, list)
    players.append({"player_id": "p1", "display_name": "P1"})
    action_state = payload["action_state"]
    assert isinstance(action_state, dict)
    actions = action_state["actions"]
    assert isinstance(actions, list)
    actions.extend(
        [
            {
                "type": "ability",
                "ability_id": "inspect",
                "description": "Inspect one player",
                "valid_targets": ["p1"],
                "target_count": 1,
                "uses_remaining": 1,
            },
            {"type": "co_declare", "claimed_role_ids": ["seer", "villager"]},
            {"type": "co_report"},
        ]
    )
    return payload


def action_state_payload(
    *, phase: str = "day", day: int = 0, phase_ends_at: int | None = None
) -> dict[str, object]:
    return {
        "phase": phase,
        "day": day,
        "phase_ends_at": phase_ends_at,
        "actions": [{"type": "chat", "channel": "public"}],
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


class GateSleep:
    def __init__(self) -> None:
        self.calls: list[tuple[float, asyncio.Event]] = []
        self.cancelled: list[float] = []

    async def __call__(self, seconds: float) -> None:
        gate = asyncio.Event()
        self.calls.append((seconds, gate))
        try:
            await gate.wait()
        except asyncio.CancelledError:
            self.cancelled.append(seconds)
            raise


class BlockingSendSocket(FakeSocket):
    def __init__(self, incoming: list[str]) -> None:
        super().__init__(incoming)
        self.send_started = asyncio.Event()
        self.send_release = asyncio.Event()

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        if message["type"] in {"session.join", "session.resume", "session.ready"}:
            return
        self.send_started.set()
        await self.send_release.wait()


class NetworkClientTests(unittest.IsolatedAsyncioTestCase):
    def test_resume_buffer_and_shutdown_timeout_configuration_is_strict(self) -> None:
        for value in (0, -1, True, 1.5):
            with self.subTest(resume_replay_capacity=value):
                with self.assertRaises(ValueError):
                    NetworkClientConfig(
                        "ws://fake",
                        "game-1",
                        "entry-token",
                        resume_replay_capacity=value,
                    )
        for value in (0, -1, True, float("nan"), float("inf")):
            with self.subTest(shutdown_timeout_seconds=value):
                with self.assertRaises(ValueError):
                    NetworkClientConfig(
                        "ws://fake",
                        "game-1",
                        "entry-token",
                        shutdown_timeout_seconds=value,
                    )

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

    async def test_state_updates_replace_each_view_and_snapshots_are_immutable(self) -> None:
        game_id = "game-1"
        sync = state_sync_payload()
        socket = FakeSocket([
            server_event("session.joined", game_id, 1, {
                "player_id": "p0",
                "connection_token": "connection-token",
            }),
            server_event("session.ready", game_id, 2, {
                "player_id": "p0",
                "ready": True,
            }),
            server_event("game.state_sync", game_id, 3, sync),
            server_event("player.list", game_id, 4, {
                "players": [{"player_id": "p1", "display_name": "P1"}],
            }),
            server_event("player.deaths", game_id, 5, {
                "deaths": [{"player_id": "p1", "day": 1, "public_cause": "vote"}],
            }),
            server_event("player.action_state", game_id, 6, action_state_payload(phase="vote", day=1)),
            server_event("game.event", game_id, 7, {
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
        snapshot = client.snapshot()

        self.assertEqual(result.reason, ClientExitReason.GAME_ENDED)
        self.assertEqual(snapshot.players, ({"player_id": "p1", "display_name": "P1"},))
        self.assertEqual(snapshot.deaths[0]["public_cause"], "vote")
        self.assertEqual(snapshot.action_state["phase"], "vote")
        self.assertEqual(snapshot.state_sync["players"][0]["player_id"], "p0")
        with self.assertRaises(TypeError):
            snapshot.state_sync["players"] = ()  # type: ignore[index]
        with self.assertRaises(TypeError):
            snapshot.players[0]["display_name"] = "changed"  # type: ignore[index]
        with self.assertRaises(TypeError):
            snapshot.action_state["phase"] = "night"  # type: ignore[index]

    async def test_action_state_update_invalidates_old_handle(self) -> None:
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
        old_action: ChatAction | None = None
        observed_update = False

        run_task = asyncio.create_task(client.run())
        async for event in client.events():
            if isinstance(event, ServerEvent) and event.type == "game.state_sync":
                old_action = next(
                    action for action in client.snapshot().actions if isinstance(action, ChatAction)
                )
                socket.incoming.put_nowait(
                    server_event(
                        "player.action_state", game_id, 4, action_state_payload(phase="vote", day=1)
                    )
                )
            elif isinstance(event, ServerEvent) and event.type == "player.action_state":
                assert old_action is not None
                with self.assertRaises(StaleActionError):
                    await client.send_chat(old_action, "old")
                observed_update = True
                socket.incoming.put_nowait(server_event("game.event", game_id, 5, {
                    "event_type": "GAME_ENDED",
                    "event_payload": {},
                }))
        result = await run_task

        self.assertTrue(observed_update)
        self.assertEqual(result.reason, ClientExitReason.GAME_ENDED)

    async def test_deadline_notice_replaces_and_cancels_stale_timer(self) -> None:
        game_id = "game-1"
        initial_sync = state_sync_payload()
        initial_sync["action_state"]["phase_ends_at"] = 101  # type: ignore[index]
        socket = FakeSocket([
            server_event("session.joined", game_id, 1, {
                "player_id": "p0",
                "connection_token": "connection-token",
            }),
            server_event("session.ready", game_id, 2, {
                "player_id": "p0",
                "ready": True,
            }),
            server_event("game.state_sync", game_id, 3, initial_sync),
        ])
        deadline_sleep = GateSleep()
        client = NetworkClient(
            NetworkClientConfig("ws://fake", game_id, "entry-token"),
            MemoryStore(),
            connector=lambda _uri: socket,
            sleep=deadline_sleep,
        )
        observed_deadline = False

        run_task = asyncio.create_task(client.run())
        async for event in client.events():
            if isinstance(event, ServerEvent) and event.type == "game.state_sync":
                await asyncio.sleep(0)
                self.assertEqual(len(deadline_sleep.calls), 1)
                socket.incoming.put_nowait(server_event(
                    "player.action_state", game_id, 4, action_state_payload()
                ))
            elif isinstance(event, ServerEvent) and event.type == "player.action_state" and event.seq == 4:
                await asyncio.sleep(0)
                self.assertEqual(deadline_sleep.cancelled, [1.0])
                updated = action_state_payload(phase="vote", day=1, phase_ends_at=102)
                socket.incoming.put_nowait(server_event("player.action_state", game_id, 5, updated))
            elif isinstance(event, ServerEvent) and event.type == "player.action_state" and event.seq == 5:
                for _ in range(100):
                    if len(deadline_sleep.calls) == 2:
                        break
                    await asyncio.sleep(0)
                else:
                    self.fail("replacement deadline timer was not scheduled")
                deadline_sleep.calls[-1][1].set()
            elif isinstance(event, PhaseDeadlineReached):
                observed_deadline = True
                socket.incoming.put_nowait(server_event("game.event", game_id, 6, {
                    "event_type": "GAME_ENDED",
                    "event_payload": {},
                }))
        result = await run_task

        self.assertTrue(observed_deadline)
        self.assertEqual(result.reason, ClientExitReason.GAME_ENDED)

    async def test_typed_send_uses_action_enumerations_for_all_gameplay_commands(self) -> None:
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
            server_event("game.state_sync", game_id, 3, rich_state_sync_payload()),
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

        actions = client.snapshot().actions
        chat = next(action for action in actions if isinstance(action, ChatAction))
        vote = next(action for action in actions if isinstance(action, VoteAction))
        ability = next(action for action in actions if isinstance(action, AbilityAction))
        co_declare = next(action for action in actions if isinstance(action, CoDeclareAction))
        co_report = next(action for action in actions if isinstance(action, CoReportAction))
        await client.send_vote(vote, None)
        await client.send_ability(ability, ["p1"])
        await client.send_co_declare(co_declare, "seer", "claim")
        await client.send_co_report(co_report, "inspect", "p1", "wolf")
        await client.send_chat(chat, "hello")

        with self.assertRaises(ValueError):
            await client.send_vote(vote, "unknown")
        with self.assertRaises(ValueError):
            await client.send_ability(ability, ["p1", "p1"])
        with self.assertRaises(ValueError):
            await client.send_co_declare(co_declare, "medium", "claim")
        self.assertFalse(hasattr(client, "send_raw"))

        socket.incoming.put_nowait(server_event("game.event", game_id, 4, {
            "event_type": "GAME_ENDED",
            "event_payload": {},
        }))
        self.assertEqual((await run_task).reason, ClientExitReason.GAME_ENDED)
        self.assertEqual(
            [message["type"] for message in socket.sent],
            ["session.join", "session.ready", "vote.cast", "ability.use", "co.declare", "co.report", "chat.send"],
        )
        self.assertEqual(socket.sent[2]["payload"], {"target_player_id": None})
        self.assertEqual(socket.sent[3]["payload"], {
            "ability_id": "inspect",
            "target_player_ids": ["p1"],
        })

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
        events = []
        while not client._events.empty():  # noqa: SLF001 - drain public stream in test
            item = client._events.get_nowait()  # noqa: SLF001
            if item.__class__.__name__ != "object":
                events.append(item)
        self.assertFalse(any(isinstance(event, SequenceGapDetected) for event in events))
        self.assertFalse(any(isinstance(event, SequenceGapRecovered) for event in events))

    async def test_resume_retention_gap_waits_for_sync_before_accepting_incremental_events(self) -> None:
        game_id = "game-1"
        socket = FakeSocket([
            server_event("session.resumed", game_id, 9, {
                "player_id": "p0",
                "last_seq": 3,
            }),
            server_event("game.state_sync", game_id, 10, state_sync_payload()),
            server_event("game.event", game_id, 11, {
                "event_type": "GAME_ENDED",
                "event_payload": {},
            }),
        ])
        store = MemoryStore(SessionCheckpoint("connection-token", 3))
        client = NetworkClient(
            NetworkClientConfig("ws://fake", game_id, "entry-token"),
            store,
            connector=lambda _uri: socket,
        )
        observed: list[object] = []

        run_task = asyncio.create_task(client.run())
        async for event in client.events():
            observed.append(event)
        result = await run_task

        self.assertEqual(result.reason, ClientExitReason.GAME_ENDED)
        self.assertEqual(socket.sent[0]["type"], "session.resume")
        self.assertEqual(socket.sent[0]["payload"]["last_seq"], 3)
        self.assertEqual(client.snapshot().last_seq, 11)
        self.assertEqual([checkpoint.last_seq for checkpoint in store.saved], [10, 11])
        self.assertEqual(
            len([event for event in observed if isinstance(event, SequenceGapDetected)]),
            1,
        )
        self.assertEqual(
            len([event for event in observed if isinstance(event, SequenceGapRecovered)]),
            1,
        )
        self.assertEqual(
            [event.type for event in observed if getattr(event, "type", None) == "session.resumed"],
            ["session.resumed"],
        )

    async def test_resume_retention_gap_ignores_incremental_event_until_sync_barrier(self) -> None:
        game_id = "game-1"
        socket = FakeSocket([
            server_event("session.resumed", game_id, 9, {
                "player_id": "p0",
                "last_seq": 3,
            }),
            server_event("game.event", game_id, 10, {
                "event_type": "SHOULD_NOT_BE_APPLIED",
                "event_payload": {},
            }),
            server_event("game.state_sync", game_id, 11, state_sync_payload()),
            server_event("game.event", game_id, 12, {
                "event_type": "GAME_ENDED",
                "event_payload": {},
            }),
        ])
        store = MemoryStore(SessionCheckpoint("connection-token", 3))
        client = NetworkClient(
            NetworkClientConfig("ws://fake", game_id, "entry-token"),
            store,
            connector=lambda _uri: socket,
        )
        observed: list[object] = []

        run_task = asyncio.create_task(client.run())
        async for event in client.events():
            observed.append(event)
        result = await run_task

        self.assertEqual(result.reason, ClientExitReason.GAME_ENDED)
        self.assertNotIn(
            "SHOULD_NOT_BE_APPLIED",
            [
                event.payload["event_type"]
                for event in observed
                if isinstance(event, ServerEvent) and event.type == "game.event"
            ],
        )
        self.assertEqual([checkpoint.last_seq for checkpoint in store.saved], [11, 12])
        self.assertEqual(client.snapshot().last_seq, 12)

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
        with TemporaryDirectory() as directory:
            path = Path(directory) / "seat.json"
            store = FileCredentialStore(path)
            await store.save(SessionCheckpoint("token", 7))
            self.assertEqual(await store.load(), SessionCheckpoint("token", 7))
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    async def test_credential_corruption_is_fatal_before_connecting(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "seat.json"
            path.write_text("{}", encoding="utf-8")
            connector_called = False

            def connector(_uri: str) -> FakeSocket:
                nonlocal connector_called
                connector_called = True
                return FakeSocket([])

            client = NetworkClient(
                NetworkClientConfig("ws://fake", "game-1", "entry-token"),
                FileCredentialStore(path),
                connector=connector,
            )

            result = await client.run()
            self.assertEqual(result.reason, ClientExitReason.CREDENTIAL_INVALID)
            self.assertFalse(connector_called)

    async def test_credential_save_failure_is_fatal_without_publishing_event(self) -> None:
        class FailingStore:
            async def load(self) -> SessionCheckpoint | None:
                return None

            async def save(self, checkpoint: SessionCheckpoint) -> None:
                raise CredentialError("disk full")

        socket = FakeSocket([
            server_event("session.joined", "game-1", 1, {
                "player_id": "p0",
                "connection_token": "connection-token",
            }),
        ])
        client = NetworkClient(
            NetworkClientConfig("ws://fake", "game-1", "entry-token"),
            FailingStore(),
            connector=lambda _uri: socket,
        )

        result = await client.run()
        events = []
        while not client._events.empty():  # noqa: SLF001 - drain public stream in test
            item = client._events.get_nowait()  # noqa: SLF001
            if item.__class__.__name__ != "object":
                events.append(item)

        self.assertEqual(result.reason, ClientExitReason.CREDENTIAL_SAVE_FAILED)
        self.assertFalse(any(isinstance(event, ServerEvent) for event in events))

    async def test_protocol_rejects_unknown_fields_and_invalid_json(self) -> None:
        validator = ProtocolMessageValidator()
        raw = json.loads(server_event("session.joined", "game-1", 1, {
            "player_id": "p0",
            "connection_token": "connection-token",
        }))
        raw["unexpected"] = True
        with self.assertRaises(ProtocolValidationError):
            validator.decode_server(json.dumps(raw))
        with self.assertRaises(ProtocolValidationError):
            validator.decode_server("not-json")

    async def test_protocol_accepts_each_declared_server_message_type(self) -> None:
        validator = ProtocolMessageValidator()
        payloads = {
            "session.joined": {
                "player_id": "p0",
                "connection_token": "connection-token",
            },
            "session.resumed": {"player_id": "p0", "last_seq": 0},
            "session.ready": {"player_id": "p0", "ready": True},
            "game.state_sync": state_sync_payload(),
            "player.list": {"players": [{"player_id": "p0", "display_name": "P0"}]},
            "player.deaths": {"deaths": []},
            "player.action_state": action_state_payload(),
            "game.event": {"event_type": "PUBLIC_EVENT", "event_payload": {}},
            "chat.message": {"channel": "public", "message": {"text": "hello"}},
            "action.rejected": {"action": "vote.cast", "reason": "stale_action"},
        }
        for message_type, payload in payloads.items():
            with self.subTest(message_type=message_type):
                self.assertEqual(
                    validator.decode_server(server_event(message_type, "game-1", 1, payload))["type"],
                    message_type,
                )

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
        self.assertEqual(
            len([event for event in observed if isinstance(event, SequenceGapDetected)]),
            1,
        )
        self.assertEqual(
            len([event for event in observed if isinstance(event, SequenceGapRecovered)]),
            1,
        )
        self.assertEqual(second.sent[0]["type"], "session.resume")
        self.assertEqual(second.sent[0]["payload"]["last_seq"], 3)

    async def test_resume_replay_precedes_authentication_and_replay_stays_contiguous(self) -> None:
        game_id = "game-1"
        socket = FakeSocket([
            server_event("game.event", game_id, 4, {
                "event_type": "REPLAYED_EVENT",
                "event_payload": {},
            }),
            server_event("session.resumed", game_id, 5, {
                "player_id": "p0",
                "last_seq": 3,
            }),
            server_event("game.state_sync", game_id, 6, state_sync_payload()),
            server_event("game.event", game_id, 7, {
                "event_type": "GAME_ENDED",
                "event_payload": {},
            }),
        ])
        store = MemoryStore(SessionCheckpoint("connection-token", 3))
        client = NetworkClient(
            NetworkClientConfig("ws://fake", game_id, "entry-token"),
            store,
            connector=lambda _uri: socket,
        )
        observed: list[object] = []

        run_task = asyncio.create_task(client.run())
        async for event in client.events():
            observed.append(event)
        result = await run_task

        self.assertEqual(result.reason, ClientExitReason.GAME_ENDED)
        self.assertFalse(any(isinstance(event, SequenceGapDetected) for event in observed))
        self.assertFalse(any(isinstance(event, SequenceGapRecovered) for event in observed))
        self.assertEqual(
            [event.type for event in observed if isinstance(event, ServerEvent)],
            ["game.event", "session.resumed", "game.state_sync", "game.event"],
        )
        self.assertEqual(client.snapshot().last_seq, 7)

    async def test_resume_terminal_replay_waits_for_sync_and_points_to_sync(self) -> None:
        game_id = "game-1"
        socket = FakeSocket([
            server_event("game.event", game_id, 4, {
                "event_type": "GAME_ENDED",
                "event_payload": {"outcome": "village"},
            }),
            server_event("session.resumed", game_id, 5, {
                "player_id": "p0",
                "last_seq": 3,
            }),
            server_event("game.state_sync", game_id, 6, state_sync_payload()),
        ])
        client = NetworkClient(
            NetworkClientConfig("ws://fake", game_id, "entry-token"),
            MemoryStore(SessionCheckpoint("connection-token", 3)),
            connector=lambda _uri: socket,
        )
        observed: list[object] = []
        run_task = asyncio.create_task(client.run())
        async for event in client.events():
            observed.append(event)
        result = await run_task

        self.assertEqual(result.reason, ClientExitReason.GAME_ENDED)
        ended = [event for event in observed if isinstance(event, GameEnded)]
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0].event.type, "game.state_sync")
        self.assertEqual(ended[0].event.seq, 6)
        self.assertEqual(client.snapshot().last_seq, 6)

    async def test_resume_replay_buffer_overrun_is_fatal_before_authentication(self) -> None:
        socket = FakeSocket([
            server_event("game.event", "game-1", 1, {
                "event_type": "PUBLIC_NOTIFY",
                "event_payload": {"notify_id": "first"},
            }),
            server_event("game.event", "game-1", 2, {
                "event_type": "PUBLIC_NOTIFY",
                "event_payload": {"notify_id": "second"},
            }),
        ])
        store = MemoryStore(SessionCheckpoint("connection-token", 0))
        client = NetworkClient(
            NetworkClientConfig(
                "ws://fake",
                "game-1",
                "entry-token",
                resume_replay_capacity=1,
            ),
            store,
            connector=lambda _uri: socket,
        )

        result = await client.run()

        self.assertEqual(result.reason, ClientExitReason.RESUME_BUFFER_OVERRUN)
        self.assertEqual(store.saved, [])
        self.assertEqual(client.snapshot().last_seq, 0)
        self.assertIsNone(client.snapshot().player_id)

    async def test_resume_sync_history_terminal_ends_after_sync_commit(self) -> None:
        game_id = "game-1"
        payload = state_sync_payload()
        payload["history"] = [{
            "type": "game.event",
            "payload": {"event_type": "GAME_ENDED", "event_payload": {}},
        }]
        socket = FakeSocket([
            server_event("session.resumed", game_id, 4, {
                "player_id": "p0",
                "last_seq": 3,
            }),
            server_event("game.state_sync", game_id, 5, payload),
        ])
        client = NetworkClient(
            NetworkClientConfig("ws://fake", game_id, "entry-token"),
            MemoryStore(SessionCheckpoint("connection-token", 3)),
            connector=lambda _uri: socket,
        )
        observed: list[object] = []
        run_task = asyncio.create_task(client.run())
        async for event in client.events():
            observed.append(event)
        result = await run_task

        self.assertEqual(result.reason, ClientExitReason.GAME_ENDED)
        ended = [event for event in observed if isinstance(event, GameEnded)]
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0].event.type, "game.state_sync")
        self.assertEqual(ended[0].event.seq, 5)
        self.assertEqual(client.snapshot().last_seq, 5)

    async def test_stale_duplicate_does_not_move_state_or_checkpoint_backwards(self) -> None:
        game_id = "game-1"
        socket = FakeSocket([
            server_event("session.joined", game_id, 1, {
                "player_id": "p0",
                "connection_token": "connection-token",
            }),
            server_event("game.event", game_id, 1, {
                "event_type": "DUPLICATE_EVENT",
                "event_payload": {},
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
        ])
        store = MemoryStore()
        client = NetworkClient(
            NetworkClientConfig("ws://fake", game_id, "entry-token"),
            store,
            connector=lambda _uri: socket,
        )

        result = await client.run()

        self.assertEqual(result.reason, ClientExitReason.GAME_ENDED)
        self.assertEqual(client.snapshot().last_seq, 4)
        self.assertEqual([checkpoint.last_seq for checkpoint in store.saved], [1, 2, 3, 4])
        self.assertEqual(store.checkpoint, SessionCheckpoint("connection-token", 4))

    async def test_send_boundary_reports_not_delivered_and_delivery_unknown(self) -> None:
        game_id = "game-1"
        socket = BlockingSendSocket([
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
            NetworkClientConfig(
                "ws://fake", game_id, "entry-token", outbound_command_capacity=1
            ),
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

        first = asyncio.create_task(client.send_chat(action, "first"))
        await asyncio.wait_for(socket.send_started.wait(), timeout=1)
        second = asyncio.create_task(client.send_chat(action, "second"))
        for _ in range(100):
            if client._outbound is not None and client._outbound.qsize() == 1:  # noqa: SLF001
                break
            await asyncio.sleep(0)
        else:
            self.fail("second command was not queued")
        with self.assertRaises(NotDeliveredError):
            await client.send_chat(action, "third")

        await client.stop()
        self.assertEqual((await run_task).reason, ClientExitReason.STOPPED)
        with self.assertRaises(DeliveryUnknownError):
            await first
        with self.assertRaises(NotDeliveredError):
            await second

    async def test_consumer_overrun_has_an_explicit_fatal_exit(self) -> None:
        socket = FakeSocket([
            server_event("session.joined", "game-1", 1, {
                "player_id": "p0",
                "connection_token": "connection-token",
            }),
        ])
        client = NetworkClient(
            NetworkClientConfig("ws://fake", "game-1", "entry-token", inbound_event_capacity=1),
            MemoryStore(),
            connector=lambda _uri: socket,
        )

        result = await client.run()

        self.assertEqual(result.reason, ClientExitReason.CONSUMER_OVERRUN)

    async def test_stop_cleans_up_socket_sender_and_deadline_task(self) -> None:
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

        await client.stop()
        result = await run_task

        self.assertEqual(result.reason, ClientExitReason.STOPPED)
        self.assertTrue(socket.closed)
        self.assertIsNone(client._socket)  # noqa: SLF001
        self.assertIsNone(client._sender_task)  # noqa: SLF001
        self.assertIsNone(client._deadline_task)  # noqa: SLF001

    async def test_stop_interrupts_a_connect_attempt_without_releasing_connector(self) -> None:
        started = asyncio.Event()
        connector_cancelled = False
        never_release = asyncio.Event()

        async def connector(_uri: str) -> FakeSocket:
            nonlocal connector_cancelled
            started.set()
            try:
                await never_release.wait()
            except asyncio.CancelledError:
                connector_cancelled = True
                raise
            return FakeSocket([])

        client = NetworkClient(
            NetworkClientConfig("ws://fake", "game-1", "entry-token"),
            MemoryStore(),
            connector=connector,
        )
        run_task = asyncio.create_task(client.run())
        await asyncio.wait_for(started.wait(), timeout=1)

        await client.stop()
        result = await asyncio.wait_for(run_task, timeout=1)

        self.assertEqual(result.reason, ClientExitReason.STOPPED)
        self.assertTrue(connector_cancelled)
        self.assertIsNone(client._socket)  # noqa: SLF001
        self.assertIsNone(client._sender_task)  # noqa: SLF001
        self.assertIsNone(client._deadline_task)  # noqa: SLF001

    async def test_stop_race_closes_socket_when_connector_resolves_during_cancel(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        socket = FakeSocket([])

        async def connector(_uri: str) -> FakeSocket:
            started.set()
            await release.wait()
            return socket

        client = NetworkClient(
            NetworkClientConfig("ws://fake", "game-1", "entry-token"),
            MemoryStore(),
            connector=connector,
        )
        run_task = asyncio.create_task(client.run())
        await asyncio.wait_for(started.wait(), timeout=1)

        await client.stop()
        release.set()
        result = await asyncio.wait_for(run_task, timeout=1)

        self.assertEqual(result.reason, ClientExitReason.STOPPED)
        self.assertEqual(socket.sent, [])
        self.assertTrue(socket.closed)
        self.assertIsNone(client._sender_task)  # noqa: SLF001

    async def test_stop_branch_closes_connector_result_after_stop_is_observed(self) -> None:
        """The stop branch itself must close a connector that ignores cancellation."""

        started = asyncio.Event()
        cancelled = asyncio.Event()
        release = asyncio.Event()
        socket = FakeSocket([])

        async def connector(_uri: str) -> FakeSocket:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                # Model a connector that completes after cancellation was
                # requested; this distinguishes the stop branch from its old
                # result-discarding implementation.
                cancelled.set()
                await release.wait()
            return socket

        client = NetworkClient(
            NetworkClientConfig("ws://fake", "game-1", "entry-token"),
            MemoryStore(),
            connector=connector,
        )
        open_task = asyncio.create_task(client._open_socket_or_stop())  # noqa: SLF001
        await asyncio.wait_for(started.wait(), timeout=1)
        client._stop_requested = True  # noqa: SLF001
        client._stop_event.set()  # noqa: SLF001
        await asyncio.wait_for(cancelled.wait(), timeout=1)
        release.set()

        with self.assertRaises(_TransientFailure):
            await asyncio.wait_for(open_task, timeout=1)
        self.assertTrue(socket.closed)

    async def test_connect_timeout_closes_socket_when_connector_resolves_during_cancel(self) -> None:
        started = asyncio.Event()
        socket = FakeSocket([])

        async def connector(_uri: str) -> FakeSocket:
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return socket
            raise AssertionError("connector must be cancelled")

        client = NetworkClient(
            NetworkClientConfig(
                "ws://fake",
                "game-1",
                "entry-token",
                connect_timeout_seconds=0.01,
            ),
            MemoryStore(),
            connector=connector,
        )
        open_task = asyncio.create_task(client._open_socket_or_stop())  # noqa: SLF001
        await asyncio.wait_for(started.wait(), timeout=1)

        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(open_task, timeout=1)

        self.assertTrue(socket.closed)

    async def test_stop_interrupts_reconnect_backoff_without_releasing_sleep(self) -> None:
        class ClosingAfterSyncSocket(FakeSocket):
            async def recv(self) -> str | None:
                if not self.incoming.empty():
                    return await self.incoming.get()
                raise ConnectionClosed(Close(1013, "try again"), None)

        socket = ClosingAfterSyncSocket([
            server_event("session.joined", "game-1", 1, {
                "player_id": "p0",
                "connection_token": "connection-token",
            }),
            server_event("session.ready", "game-1", 2, {
                "player_id": "p0",
                "ready": True,
            }),
            server_event("game.state_sync", "game-1", 3, state_sync_payload()),
        ])
        backoff = GateSleep()
        client = NetworkClient(
            NetworkClientConfig("ws://fake", "game-1", "entry-token"),
            MemoryStore(),
            connector=lambda _uri: socket,
            sleep=backoff,
            reconnect_policy=ReconnectPolicy(
                initial_delay_seconds=5,
                max_delay_seconds=5,
                jitter_ratio=0,
                max_disconnected_seconds=10,
            ),
        )
        run_task = asyncio.create_task(client.run())
        for _ in range(100):
            if backoff.calls:
                break
            await asyncio.sleep(0.01)
        else:
            self.fail("client did not enter reconnect backoff")

        await client.stop()
        result = await asyncio.wait_for(run_task, timeout=1)

        self.assertEqual(result.reason, ClientExitReason.STOPPED)
        self.assertEqual(result.lifecycle, ClientLifecycle.ENDED)
        self.assertEqual(backoff.cancelled, [5])
        self.assertTrue(socket.closed)
        self.assertIsNone(client._sender_task)  # noqa: SLF001
        self.assertIsNone(client._deadline_task)  # noqa: SLF001

    async def test_run_cancellation_finishes_lifecycle_after_cleanup(self) -> None:
        socket = FakeSocket([
            server_event("session.joined", "game-1", 1, {
                "player_id": "p0",
                "connection_token": "connection-token",
            }),
            server_event("session.ready", "game-1", 2, {
                "player_id": "p0",
                "ready": True,
            }),
            server_event("game.state_sync", "game-1", 3, state_sync_payload()),
        ])
        client = NetworkClient(
            NetworkClientConfig("ws://fake", "game-1", "entry-token"),
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

        run_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await run_task

        self.assertEqual(client.lifecycle, ClientLifecycle.ENDED)
        self.assertTrue(socket.closed)
        self.assertIsNone(client._socket)  # noqa: SLF001
        self.assertIsNone(client._sender_task)  # noqa: SLF001
        self.assertIsNone(client._deadline_task)  # noqa: SLF001

    async def test_sync_timeout_is_a_generation_deadline_not_an_idle_recv_timeout(self) -> None:
        class DripSocket(FakeSocket):
            def __init__(self, initial_messages: list[str], next_seq: int) -> None:
                super().__init__(initial_messages)
                self.next_seq = next_seq
                self.initial_messages = 0

            async def recv(self) -> str:
                if self.initial_messages < 2:
                    self.initial_messages += 1
                    return await self.incoming.get()  # type: ignore[return-value]
                await asyncio.sleep(0.005)
                self.next_seq += 1
                return server_event("player.list", "game-1", self.next_seq, {
                    "players": [{"player_id": "p0", "display_name": "P0"}],
                })

        sockets: list[DripSocket] = []
        store = MemoryStore()

        def connector(_uri: str) -> DripSocket:
            checkpoint = store.checkpoint
            if checkpoint is None:
                initial_messages = [
                    server_event("session.joined", "game-1", 1, {
                        "player_id": "p0",
                        "connection_token": "connection-token",
                    }),
                    server_event("session.ready", "game-1", 2, {
                        "player_id": "p0",
                        "ready": True,
                    }),
                ]
                next_seq = 2
            else:
                resumed_seq = checkpoint.last_seq + 1
                initial_messages = [
                    server_event("session.resumed", "game-1", resumed_seq, {
                        "player_id": "p0",
                        "last_seq": checkpoint.last_seq,
                    }),
                    server_event("session.ready", "game-1", resumed_seq + 1, {
                        "player_id": "p0",
                        "ready": True,
                    }),
                ]
                next_seq = resumed_seq + 1
            socket = DripSocket(initial_messages, next_seq)
            sockets.append(socket)
            return socket

        async def instant_sleep(_seconds: float) -> None:
            await asyncio.sleep(0)

        client = NetworkClient(
            NetworkClientConfig(
                "ws://fake", "game-1", "entry-token", sync_timeout_seconds=0.03
            ),
            store,
            connector=connector,
            sleep=instant_sleep,
            reconnect_policy=ReconnectPolicy(
                initial_delay_seconds=0,
                max_delay_seconds=0,
                jitter_ratio=0,
                max_disconnected_seconds=0.02,
            ),
        )

        result = await asyncio.wait_for(client.run(), timeout=1)

        self.assertEqual(result.reason, ClientExitReason.RECONNECT_EXHAUSTED)
        self.assertGreaterEqual(len(sockets), 1)
        self.assertTrue(all(socket.closed for socket in sockets))

    async def test_resume_requires_authentication_ack_before_accepting_sync(self) -> None:
        socket = FakeSocket([
            server_event("game.state_sync", "game-1", 1, state_sync_payload()),
            server_event("game.event", "game-1", 2, {
                "event_type": "GAME_ENDED",
                "event_payload": {},
            }),
        ])
        store = MemoryStore(SessionCheckpoint("connection-token", 0))
        client = NetworkClient(
            NetworkClientConfig("ws://fake", "game-1", "entry-token"),
            store,
            connector=lambda _uri: socket,
        )

        result = await client.run()

        self.assertEqual(result.reason, ClientExitReason.INVALID_SERVER_MESSAGE)
        self.assertEqual(store.saved, [])
        self.assertEqual(client.snapshot().last_seq, 0)
        self.assertIsNone(client.snapshot().player_id)

    async def test_resume_rejects_sync_before_authentication(self) -> None:
        for message in (
            server_event("game.state_sync", "game-1", 1, state_sync_payload()),
            server_event("session.ready", "game-1", 1, {"player_id": "p0", "ready": True}),
        ):
            with self.subTest(message_type=json.loads(message)["type"]):
                socket = FakeSocket([message])
                store = MemoryStore(SessionCheckpoint("connection-token", 0))
                client = NetworkClient(
                    NetworkClientConfig("ws://fake", "game-1", "entry-token"),
                    store,
                    connector=lambda _uri, socket=socket: socket,
                )

                result = await client.run()

                self.assertEqual(result.reason, ClientExitReason.INVALID_SERVER_MESSAGE)
                self.assertEqual(store.saved, [])
                self.assertEqual(client.snapshot().last_seq, 0)
                self.assertIsNone(client.snapshot().player_id)

    async def test_resume_ack_payload_must_match_requested_checkpoint(self) -> None:
        socket = FakeSocket([
            server_event("session.resumed", "game-1", 1, {
                "player_id": "p0",
                "last_seq": 2,
            }),
        ])
        store = MemoryStore(SessionCheckpoint("connection-token", 3))
        client = NetworkClient(
            NetworkClientConfig("ws://fake", "game-1", "entry-token"),
            store,
            connector=lambda _uri: socket,
        )

        result = await client.run()

        self.assertEqual(result.reason, ClientExitReason.INVALID_SERVER_MESSAGE)
        self.assertEqual(store.saved, [])
        self.assertEqual(client.snapshot().last_seq, 3)

    async def test_mismatched_or_duplicate_authentication_ack_is_fatal(self) -> None:
        cases = (
            (
                SessionCheckpoint("connection-token", 0),
                [server_event("session.joined", "game-1", 1, {
                    "player_id": "p0",
                    "connection_token": "new-token",
                })],
            ),
            (
                None,
                [
                    server_event("session.joined", "game-1", 1, {
                        "player_id": "p0",
                        "connection_token": "connection-token",
                    }),
                    server_event("session.joined", "game-1", 2, {
                        "player_id": "p0",
                        "connection_token": "different-token",
                    }),
                ],
            ),
        )
        for checkpoint, incoming in cases:
            with self.subTest(checkpoint=checkpoint):
                socket = FakeSocket(incoming)
                store = MemoryStore(checkpoint)
                client = NetworkClient(
                    NetworkClientConfig("ws://fake", "game-1", "entry-token"),
                    store,
                    connector=lambda _uri, socket=socket: socket,
                )

                result = await client.run()

                self.assertEqual(result.reason, ClientExitReason.INVALID_SERVER_MESSAGE)

    async def test_reconnect_exhaustion_uses_disconnected_budget(self) -> None:
        clock_value = 0.0
        connector_calls = 0

        def clock() -> float:
            nonlocal clock_value
            clock_value += 1.0
            return clock_value

        def connector(_uri: str) -> FakeSocket:
            nonlocal connector_calls
            connector_calls += 1
            raise OSError("offline")

        async def instant_sleep(_seconds: float) -> None:
            await asyncio.sleep(0)

        client = NetworkClient(
            NetworkClientConfig("ws://fake", "game-1", "entry-token"),
            MemoryStore(),
            connector=connector,
            clock=clock,
            sleep=instant_sleep,
            reconnect_policy=ReconnectPolicy(
                initial_delay_seconds=0,
                max_delay_seconds=0,
                jitter_ratio=0,
                max_disconnected_seconds=0.5,
            ),
        )

        result = await client.run()

        self.assertEqual(result.reason, ClientExitReason.RECONNECT_EXHAUSTED)
        self.assertGreaterEqual(connector_calls, 2)

    async def test_close_1013_reconnects_with_resume(self) -> None:
        class ClosingSocket(FakeSocket):
            async def recv(self) -> str:
                raise ConnectionClosed(Close(1013, "try again"), None)

        game_id = "game-1"
        first = ClosingSocket([])
        second = FakeSocket([
            server_event("session.resumed", game_id, 1, {
                "player_id": "p0",
                "last_seq": 0,
            }),
            server_event("game.state_sync", game_id, 2, state_sync_payload()),
            server_event("game.event", game_id, 3, {
                "event_type": "GAME_ENDED",
                "event_payload": {},
            }),
        ])
        sockets = iter((first, second))
        client = NetworkClient(
            NetworkClientConfig("ws://fake", game_id, "entry-token"),
            MemoryStore(SessionCheckpoint("connection-token", 0)),
            connector=lambda _uri: next(sockets),
            reconnect_policy=ReconnectPolicy(
                initial_delay_seconds=0,
                max_delay_seconds=0,
                jitter_ratio=0,
                max_disconnected_seconds=2,
            ),
        )

        result = await client.run()

        self.assertEqual(result.reason, ClientExitReason.GAME_ENDED)
        self.assertEqual(second.sent[0]["type"], "session.resume")
        self.assertEqual(second.sent[0]["payload"]["last_seq"], 0)

    async def test_close_4001_is_fatal_without_reconnect(self) -> None:
        class ClosingSocket(FakeSocket):
            async def recv(self) -> str:
                raise ConnectionClosed(Close(4001, "replaced"), None)

        sockets = [ClosingSocket([])]
        client = NetworkClient(
            NetworkClientConfig("ws://fake", "game-1", "entry-token"),
            MemoryStore(SessionCheckpoint("connection-token", 0)),
            connector=lambda _uri: sockets.pop(),
        )

        result = await client.run()

        self.assertEqual(result.reason, ClientExitReason.AUTHENTICATION_FAILED)
        self.assertEqual(sockets, [])

    async def test_resume_authentication_failure_does_not_fallback_to_join(self) -> None:
        class ClosingSocket(FakeSocket):
            async def recv(self) -> str:
                raise ConnectionClosed(Close(1008, "invalid resume"), None)

        socket = ClosingSocket([])
        client = NetworkClient(
            NetworkClientConfig("ws://fake", "game-1", "entry-token"),
            MemoryStore(SessionCheckpoint("connection-token", 4)),
            connector=lambda _uri: socket,
        )

        result = await client.run()

        self.assertEqual(result.reason, ClientExitReason.AUTHENTICATION_FAILED)
        self.assertEqual([message["type"] for message in socket.sent], [
            "session.resume", "session.ready",
        ])

    async def test_reconnect_delay_has_injected_jitter_and_cap(self) -> None:
        class MaximumRandom:
            def uniform(self, _left: float, right: float) -> float:
                return right

        client = NetworkClient(
            NetworkClientConfig("ws://fake", "game-1", "entry-token"),
            MemoryStore(),
            random_source=MaximumRandom(),  # type: ignore[arg-type]
            reconnect_policy=ReconnectPolicy(
                initial_delay_seconds=1,
                multiplier=2,
                max_delay_seconds=3,
                jitter_ratio=0.2,
                max_disconnected_seconds=10,
            ),
        )

        self.assertEqual(client._retry_delay(0), 1.2)  # noqa: SLF001
        self.assertEqual(client._retry_delay(1), 2.4)  # noqa: SLF001
        self.assertAlmostEqual(client._retry_delay(2), 3.6)  # noqa: SLF001
        self.assertAlmostEqual(client._retry_delay(3), 3.6)  # noqa: SLF001

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
