from __future__ import annotations

import json
from random import Random
from pathlib import Path
import unittest

from websockets.asyncio.client import connect

from server.aiwolf_core import GameState, InMemoryEventSink, PlayerConfig, load_content, load_preset
from server.network import GameRegistry, SessionManager, TickDriver, WebSocketGameServer
from server.network.protocol import ProtocolMessageValidator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GAME_ID = "123e4567-e89b-12d3-a456-426614174100"


def make_game() -> GameState:
    content = load_content(PROJECT_ROOT / "content")
    preset = load_preset(PROJECT_ROOT / "content" / "presets" / "standard_9.yaml", content)
    players = [
        PlayerConfig(player_id=f"player-{index}", display_name=f"Player {index}")
        for index in range(sum(preset.role_counts.values()))
    ]
    return GameState.create_from_preset(
        content,
        preset,
        players,
        game_id=GAME_ID,
        event_sink=InMemoryEventSink(),
        rng=Random(0),
        started_at=0,
    )


def client_message(
    message_type: str,
    payload: dict[str, object],
    *,
    protocol_version: str = "1.0",
    game_id: str = GAME_ID,
) -> dict[str, object]:
    return {
        "type": message_type,
        "protocol_version": protocol_version,
        "event_id": "123e4567-e89b-12d3-a456-426614174101",
        "game_id": game_id,
        "timestamp": 0,
        "payload": payload,
    }


class SessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = make_game()
        self.registry = GameRegistry({self.game.game_id: self.game})
        tokens = iter(("token-for-player-0", "token-for-player-1"))
        self.manager = SessionManager(
            self.registry,
            clock=lambda: 12,
            token_factory=lambda: next(tokens),
            event_id_factory=lambda: "123e4567-e89b-12d3-a456-426614174102",
        )
        self.validator = ProtocolMessageValidator()

    def test_join_issues_token_only_to_the_joined_session_and_ready_marks_seat(self) -> None:
        reply, context = self.manager.handle_message(
            client_message("session.join", {"player_id": "player-0"})
        )

        self.assertIsNotNone(context)
        self.assertEqual(reply.type, "session.joined")
        self.assertEqual(reply.seq, 1)
        self.assertEqual(reply.payload["player_id"], "player-0")
        self.assertEqual(reply.payload["connection_token"], "token-for-player-0")
        self.validator.validate_server(reply.as_message())
        session = self.manager.session_for(GAME_ID)
        self.assertEqual(session.connected_player_ids, {"player-0"})
        self.assertEqual(session.ready_player_ids, frozenset())

        ready_reply, ready_context = self.manager.handle_message(
            client_message("session.ready", {}), context
        )

        self.assertIs(ready_context, context)
        self.assertEqual(ready_reply.type, "session.ready")
        self.assertEqual(ready_reply.seq, 2)
        self.assertEqual(ready_reply.payload, {"player_id": "player-0", "ready": True})
        self.assertEqual(session.ready_player_ids, {"player-0"})
        self.validator.validate_server(ready_reply.as_message())

    def test_resume_verifies_token_without_accepting_player_id_claim(self) -> None:
        joined_reply, joined_context = self.manager.handle_message(
            client_message("session.join", {"player_id": "player-0"})
        )
        self.manager.disconnect(joined_context)

        resumed_reply, resumed_context = self.manager.handle_message(
            client_message(
                "session.resume",
                {"connection_token": joined_reply.payload["connection_token"], "last_seq": 1},
            )
        )

        self.assertIsNotNone(resumed_context)
        self.assertEqual(resumed_context.player_id, "player-0")
        self.assertEqual(resumed_reply.type, "session.resumed")
        self.assertEqual(resumed_reply.payload, {"player_id": "player-0", "last_seq": 1})

        invalid_claim_reply, invalid_claim_context = self.manager.handle_message(
            client_message(
                "session.resume",
                {
                    "connection_token": joined_reply.payload["connection_token"],
                    "last_seq": 1,
                    "player_id": "player-1",
                },
            )
        )
        self.assertIsNone(invalid_claim_context)
        self.assertEqual(invalid_claim_reply.type, "action.rejected")
        self.assertEqual(invalid_claim_reply.payload["reason"], "invalid_message")

        invalid_token_reply, invalid_token_context = self.manager.handle_message(
            client_message("session.resume", {"connection_token": "wrong", "last_seq": 2})
        )
        self.assertIsNone(invalid_token_context)
        self.assertEqual(invalid_token_reply.type, "action.rejected")
        self.assertEqual(invalid_token_reply.payload["reason"], "invalid_connection_token")

    def test_major_protocol_mismatch_and_unknown_player_are_rejected_without_token(self) -> None:
        mismatch_reply, mismatch_context = self.manager.handle_message(
            client_message("session.join", {"player_id": "player-0"}, protocol_version="2.0")
        )
        self.assertIsNone(mismatch_context)
        self.assertEqual(mismatch_reply.payload["reason"], "unsupported_protocol_version")
        self.assertFalse(self.manager.session_for(GAME_ID).has_joined("player-0"))

        unknown_reply, unknown_context = self.manager.handle_message(
            client_message("session.join", {"player_id": "not-a-player"})
        )
        self.assertIsNone(unknown_context)
        self.assertEqual(unknown_reply.payload["reason"], "unknown_player")

    def test_disconnect_keeps_the_game_and_seat_intact(self) -> None:
        _, context = self.manager.handle_message(
            client_message("session.join", {"player_id": "player-0"})
        )
        phase_before = self.game.phase

        self.manager.disconnect(context)

        self.assertIn("player-0", self.game.players)
        self.assertEqual(self.game.phase, phase_before)
        self.assertEqual(self.manager.session_for(GAME_ID).connected_player_ids, frozenset())


class TickDriverTests(unittest.TestCase):
    def test_tick_uses_one_server_clock_value_for_every_registered_game(self) -> None:
        class RecordingGame:
            def __init__(self, game_id: str) -> None:
                self.game_id = game_id
                self.players: dict[str, object] = {}
                self.received_times: list[int] = []

            def advance_if_due(self, now: int) -> bool:
                self.received_times.append(now)
                return self.game_id == "game-a"

        first = RecordingGame("game-a")
        second = RecordingGame("game-b")
        ticker = TickDriver(GameRegistry({"game-a": first, "game-b": second}), clock=lambda: 77)

        self.assertEqual(ticker.advance_once(), {"game-a": True, "game-b": False})
        self.assertEqual(first.received_times, [77])
        self.assertEqual(second.received_times, [77])


class WebSocketGameServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_join_sends_the_token_only_on_that_connection(self) -> None:
        game = make_game()
        registry = GameRegistry({game.game_id: game})
        server = WebSocketGameServer(registry, tick_interval_seconds=3600)
        listener = await server.start("127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        uri = f"ws://127.0.0.1:{port}"
        try:
            async with connect(uri) as joined_socket, connect(uri) as other_socket:
                await joined_socket.send(
                    json.dumps(client_message("session.join", {"player_id": "player-0"}))
                )
                joined = json.loads(await joined_socket.recv())
                self.assertEqual(joined["type"], "session.joined")
                self.assertIn("connection_token", joined["payload"])

                await other_socket.send(json.dumps(client_message("session.ready", {})))
                unauthenticated = json.loads(await other_socket.recv())
                self.assertEqual(unauthenticated["type"], "action.rejected")
                self.assertNotIn("connection_token", unauthenticated["payload"])
        finally:
            await server.close()


if __name__ == "__main__":
    unittest.main()
