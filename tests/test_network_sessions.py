from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from random import Random
from pathlib import Path
import unittest

from websockets.asyncio.client import connect

from server.aiwolf_core import (
    EventVisibility,
    GamePhase,
    GameState,
    GameEvent,
    InMemoryEventSink,
    PlayerConfig,
    load_content,
    load_preset,
)
from server.network import GameRegistry, SessionManager, TickDriver, WebSocketGameServer
from server.network.protocol import ProtocolMessageValidator, ProtocolValidationError
from server.network.session import UnaddressableRequest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GAME_ID = "123e4567-e89b-12d3-a456-426614174100"
SCHEMA_PATH = PROJECT_ROOT / "protocol" / "aiwolf-v1.schema.json"


def make_game(game_id: str = GAME_ID, *, rules=None) -> GameState:
    content = load_content(PROJECT_ROOT / "content")
    preset = load_preset(PROJECT_ROOT / "content" / "presets" / "standard_9.yaml", content)
    if rules is not None:
        preset = replace(preset, rules=rules)
    players = [
        PlayerConfig(player_id=f"player-{index}", display_name=f"Player {index}")
        for index in range(sum(preset.role_counts.values()))
    ]
    return GameState.create_from_preset(
        content,
        preset,
        players,
        game_id=game_id,
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
        result = self.manager.handle_message(
            client_message("session.join", {"player_id": "player-0"})
        )
        reply, context = result.reply, result.context

        self.assertIsNotNone(context)
        self.assertEqual(reply.type, "session.joined")
        self.assertEqual(reply.seq, 1)
        self.assertEqual(reply.payload["player_id"], "player-0")
        self.assertEqual(reply.payload["connection_token"], "token-for-player-0")
        self.validator.validate_server(reply.as_message())
        session = self.manager.session_for(GAME_ID)
        self.assertEqual(session.connected_player_ids, {"player-0"})
        self.assertEqual(session.ready_player_ids, frozenset())

        ready_result = self.manager.handle_message(
            client_message("session.ready", {}), context
        )
        ready_reply, ready_context = ready_result.reply, ready_result.context

        self.assertIs(ready_context, context)
        self.assertEqual(ready_reply.type, "session.ready")
        self.assertEqual(ready_reply.seq, 2)
        self.assertEqual(ready_reply.payload, {"player_id": "player-0", "ready": True})
        self.assertEqual(session.ready_player_ids, {"player-0"})
        self.validator.validate_server(ready_reply.as_message())

    def test_resume_verifies_token_without_accepting_player_id_claim(self) -> None:
        joined_result = self.manager.handle_message(
            client_message("session.join", {"player_id": "player-0"})
        )
        joined_reply, joined_context = joined_result.reply, joined_result.context
        self.manager.disconnect(joined_context)

        resumed_result = self.manager.handle_message(
            client_message(
                "session.resume",
                {"connection_token": joined_reply.payload["connection_token"], "last_seq": 1},
            )
        )
        resumed_reply, resumed_context = resumed_result.reply, resumed_result.context

        self.assertIsNotNone(resumed_context)
        self.assertEqual(resumed_context.player_id, "player-0")
        self.assertEqual(resumed_reply.type, "session.resumed")
        self.assertEqual(resumed_reply.payload, {"player_id": "player-0", "last_seq": 1})

        with self.assertRaises(UnaddressableRequest):
            self.manager.handle_message(
                client_message(
                    "session.resume",
                    {
                        "connection_token": joined_reply.payload["connection_token"],
                        "last_seq": 1,
                        "player_id": "player-1",
                    },
                )
            )
        with self.assertRaises(UnaddressableRequest):
            self.manager.handle_message(
                client_message("session.resume", {"connection_token": "wrong", "last_seq": 2})
            )

    def test_major_protocol_mismatch_and_unknown_player_are_rejected_without_token(self) -> None:
        with self.assertRaises(UnaddressableRequest):
            self.manager.handle_message(
                client_message("session.join", {"player_id": "player-0"}, protocol_version="2.0")
            )
        self.assertFalse(self.manager.session_for(GAME_ID).has_joined("player-0"))

        with self.assertRaises(UnaddressableRequest):
            self.manager.handle_message(
                client_message("session.join", {"player_id": "not-a-player"})
            )

    def test_disconnect_keeps_the_game_and_seat_intact(self) -> None:
        result = self.manager.handle_message(
            client_message("session.join", {"player_id": "player-0"})
        )
        context = result.context
        phase_before = self.game.phase

        self.manager.disconnect(context)

        self.assertIn("player-0", self.game.players)
        self.assertEqual(self.game.phase, phase_before)
        self.assertEqual(self.manager.session_for(GAME_ID).connected_player_ids, frozenset())

    def test_sequences_are_contiguous_per_player_and_continue_across_reconnection(self) -> None:
        first = self.manager.handle_message(client_message("session.join", {"player_id": "player-0"}))
        first_ready = self.manager.handle_message(client_message("session.ready", {}), first.context)
        second = self.manager.handle_message(client_message("session.join", {"player_id": "player-1"}))
        second_ready = self.manager.handle_message(client_message("session.ready", {}), second.context)

        self.assertEqual([first.reply.seq, first_ready.reply.seq], [1, 2])
        self.assertEqual([second.reply.seq, second_ready.reply.seq], [1, 2])

        replacement = self.manager.handle_message(
            client_message(
                "session.resume",
                {"connection_token": first.reply.payload["connection_token"], "last_seq": 2},
            )
        )
        self.assertEqual(replacement.reply.seq, 3)
        self.assertEqual(replacement.replaced_connection_ids, (first.context.connection_id,))
        self.assertEqual(self.manager.session_for(GAME_ID).connection_count("player-0"), 1)

    def test_non_uuid_game_id_is_valid_for_core_and_protocol_messages(self) -> None:
        game = make_game("standard-nine")
        manager = SessionManager(
            GameRegistry({game.game_id: game}),
            clock=lambda: 12,
            token_factory=lambda: "non-uuid-game-token",
        )

        result = manager.handle_message(
            client_message("session.join", {"player_id": "player-0"}, game_id=game.game_id)
        )

        self.assertEqual(result.reply.game_id, "standard-nine")
        ProtocolMessageValidator().validate_server(result.reply.as_message())

    def test_authenticated_actions_delegate_to_core_and_return_rejections_only_on_failure(self) -> None:
        self.game._enter_phase(GamePhase.DAY, 1)
        joined = self.manager.handle_message(client_message("session.join", {"player_id": "player-0"}))

        chat = self.manager.handle_message(
            client_message("chat.send", {"message": "hello"}), joined.context
        )
        self.assertIsNone(chat.reply)
        self.assertEqual(
            chat.channel_messages,
            ((
                "public",
                {"player_id": "player-0", "display_name": "Player 0", "message": "hello"},
            ),),
        )
        self.assertEqual(
            self.game.public_chat_counts[(self.game.day, "player-0")], 1
        )

        self.game._record_player_death("player-0", "lynched")
        rejected = self.manager.handle_message(
            client_message("co.declare", {"claimed_role_id": "seer", "comment": "too late"}),
            joined.context,
        )
        self.assertIsNotNone(rejected.reply)
        self.assertEqual(rejected.reply.type, "action.rejected")
        self.assertEqual(rejected.reply.payload["reason"], "action_unavailable")

    def test_ability_and_vote_requests_are_accepted_by_the_same_core_methods(self) -> None:
        wolf_player_id = next(
            player_id for player_id, player in self.game.players.items() if player.role.id == "werewolf"
        )
        target_player_id = next(
            player_id
            for player_id, player in self.game.players.items()
            if player_id != wolf_player_id and player.role.id != "werewolf"
        )
        self.game.day = 1
        self.game._enter_phase(GamePhase.NIGHT, 1)
        joined = self.manager.handle_message(
            client_message("session.join", {"player_id": wolf_player_id})
        )
        ability = self.manager.handle_message(
            client_message(
                "ability.use", {"ability_id": "attack", "target_player_ids": [target_player_id]}
            ),
            joined.context,
        )
        self.assertIsNone(ability.reply)
        self.assertIn(wolf_player_id, self.game.pending_actions)

        self.game._enter_phase(GamePhase.VOTE, 2)
        vote = self.manager.handle_message(
            client_message("vote.cast", {"target_player_id": target_player_id}), joined.context
        )
        self.assertIsNone(vote.reply)
        self.assertEqual(self.game.pending_votes[wolf_player_id], target_player_id)

    def test_vote_and_ability_rejections_expose_safe_distinct_reason_codes(self) -> None:
        player_id = next(
            player_id for player_id, player in self.game.players.items() if not player.role.abilities
        )
        self.game._enter_phase(GamePhase.VOTE, 1)
        joined = self.manager.handle_message(
            client_message("session.join", {"player_id": player_id})
        )

        self_vote = self.manager.handle_message(
            client_message("vote.cast", {"target_player_id": player_id}), joined.context
        )
        unknown_target = self.manager.handle_message(
            client_message("vote.cast", {"target_player_id": "missing-player"}), joined.context
        )
        self.game.day = 1
        self.game._enter_phase(GamePhase.NIGHT, 2)
        unknown_ability = self.manager.handle_message(
            client_message("ability.use", {"ability_id": "inspect", "target_player_ids": ["player-0"]}),
            joined.context,
        )

        replies = (self_vote.reply, unknown_target.reply, unknown_ability.reply)
        self.assertTrue(all(reply is not None and reply.type == "action.rejected" for reply in replies))
        self.assertEqual(
            tuple(reply.payload["reason"] for reply in replies),
            ("self_vote_disabled", "unknown_target", "unknown_ability"),
        )
        self.assertTrue(
            all(set(reply.payload) == {"action", "reason"} for reply in replies)
        )


class TickDriverTests(unittest.TestCase):
    def test_tick_uses_one_server_clock_value_for_every_registered_game(self) -> None:
        class RecordingGame:
            def __init__(self, game_id: str, *, deadline_free: bool = False, broken: bool = False) -> None:
                self.game_id = game_id
                self.players: dict[str, object] = {}
                self.received_times: list[int] = []
                self.deadline_free = deadline_free
                self.broken = broken

            def advance_if_due(self, now: int) -> bool:
                self.received_times.append(now)
                if self.broken:
                    raise RuntimeError("broken game")
                if self.deadline_free:
                    return False
                return self.game_id == "game-a"

        first = RecordingGame("game-a")
        second = RecordingGame("game-b")
        finished = RecordingGame("finished", deadline_free=True)
        broken = RecordingGame("broken", broken=True)
        ticker = TickDriver(
            GameRegistry({"game-a": first, "finished": finished, "broken": broken, "game-b": second}),
            clock=lambda: 77,
        )

        with self.assertLogs("server.network.session", level="ERROR"):
            self.assertEqual(
                ticker.advance_once(),
                {"game-a": True, "finished": False, "broken": False, "game-b": False},
            )
        self.assertEqual(first.received_times, [77])
        self.assertEqual(second.received_times, [77])
        self.assertEqual(finished.received_times, [77])
        self.assertEqual(broken.received_times, [77])


class ProtocolMessageValidatorTests(unittest.TestCase):
    def test_type_specific_validators_are_derived_from_schema_definitions(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        schema = deepcopy(schema)
        schema["$defs"]["test_request"] = {
            "allOf": [
                {"$ref": "#/$defs/client_request"},
                {
                    "properties": {
                        "type": {"const": "session.test"},
                        "payload": {
                            "type": "object",
                            "required": ["value"],
                            "properties": {"value": {"type": "string", "minLength": 1}},
                            "additionalProperties": False,
                        },
                    }
                },
            ]
        }
        validator = ProtocolMessageValidator(schema=schema)
        with self.assertRaises(ProtocolValidationError):
            validator.validate_client(client_message("session.test", {}))
        validator.validate_client(client_message("session.test", {"value": "ok"}))

    def test_delivery_message_types_receive_schema_specific_validation(self) -> None:
        validator = ProtocolMessageValidator()
        event = {
            "type": "game.event",
            "protocol_version": "1.0",
            "event_id": "123e4567-e89b-12d3-a456-426614174102",
            "game_id": GAME_ID,
            "seq": 1,
            "timestamp": 0,
            "payload": {"event_type": "PHASE_STARTED", "event_payload": {}},
        }
        validator.validate_server(event)
        with self.assertRaises(ProtocolValidationError):
            validator.validate_server(dict(event, payload={"event_type": "PHASE_STARTED"}))

        chat = dict(event, type="chat.message", payload={"channel": "wolf", "message": {"text": "hi"}})
        validator.validate_server(chat)
        with self.assertRaises(ProtocolValidationError):
            validator.validate_server(dict(chat, payload={"channel": "wolf"}))

    def test_player_action_requests_have_strict_type_specific_payloads(self) -> None:
        validator = ProtocolMessageValidator()
        validator.validate_client(
            client_message("chat.send", {"message": "hello"})
        )
        validator.validate_client(
            client_message("vote.cast", {"target_player_id": None})
        )
        validator.validate_client(
            client_message("ability.use", {"ability_id": "inspect", "target_player_ids": ["player-1"]})
        )
        validator.validate_client(
            client_message("co.declare", {"claimed_role_id": "seer", "comment": "CO"})
        )
        validator.validate_client(
            client_message(
                "co.report",
                {"kind": "inspect_result", "target_player_id": "player-1", "claimed_result": "white"},
            )
        )
        with self.assertRaises(ProtocolValidationError):
            validator.validate_client(client_message("chat.send", {"channel": "public", "message": "extra"}))


class ChatChannelRecipientTests(unittest.TestCase):
    def test_public_channel_recipients_do_not_depend_on_every_role_declaration(self) -> None:
        game = make_game()
        game.players["player-0"] = replace(game.players["player-0"], alive=False)
        expected = tuple(game.players)
        self.assertEqual(game.chat_channel_recipient_ids("public"), expected)

        silent_role = replace(
            game.content.roles["villager"],
            id="silent_observer",
            chat_channels=(),
        )
        game.content = replace(
            game.content,
            roles={**game.content.roles, silent_role.id: silent_role},
        )

        self.assertEqual(game.chat_channel_recipient_ids("public"), expected)


class WebSocketGameServerTests(unittest.IsolatedAsyncioTestCase):
    async def _join(self, socket, player_id: str) -> dict[str, object]:
        await socket.send(json.dumps(client_message("session.join", {"player_id": player_id})))
        return json.loads(await socket.recv())

    async def test_event_delivery_separates_public_private_and_server_visibility(self) -> None:
        game = make_game()
        registry = GameRegistry({game.game_id: game})
        server = WebSocketGameServer(
            registry,
            ticker=TickDriver(registry, clock=lambda: 0),
            tick_interval_seconds=0.001,
        )
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        try:
            async with connect(uri) as first, connect(uri) as second:
                self.assertEqual((await self._join(first, "player-0"))["seq"], 1)
                self.assertEqual((await self._join(second, "player-1"))["seq"], 1)
                game.event_bus.publish(
                    GameEvent("TEST_PUBLIC", EventVisibility.PUBLIC, {"safe": "yes"})
                )
                game.event_bus.publish(
                    GameEvent(
                        "TEST_PRIVATE",
                        EventVisibility.PRIVATE,
                        {"secret": "player-0-only"},
                        recipient_player_id="player-0",
                    )
                )
                game.event_bus.publish(
                    GameEvent("INTERNAL", EventVisibility.SERVER, {"cause": "attacked"})
                )

                first_public = json.loads(await first.recv())
                first_private = json.loads(await first.recv())
                second_public = json.loads(await second.recv())
                self.assertEqual([first_public["seq"], first_private["seq"]], [2, 3])
                self.assertEqual(second_public["seq"], 2)
                self.assertEqual(first_public["payload"], {"event_type": "TEST_PUBLIC", "event_payload": {"safe": "yes"}})
                self.assertEqual(first_private["payload"], {"event_type": "TEST_PRIVATE", "event_payload": {"secret": "player-0-only"}})
                self.assertNotIn("visibility", first_private["payload"])
                self.assertNotIn("recipient_player_id", first_private["payload"])
                self.assertEqual(second_public["payload"], first_public["payload"])
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(second.recv(), timeout=0.05)
        finally:
            await server.close()

    async def test_channel_delivery_uses_content_authorized_recipients(self) -> None:
        game = make_game()
        authorized = game.chat_channel_recipient_ids("wolf")
        unauthorized = next(player_id for player_id in game.players if player_id not in authorized)
        registry = GameRegistry({game.game_id: game})
        server = WebSocketGameServer(registry, tick_interval_seconds=3600)
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        try:
            async with connect(uri) as permitted, connect(uri) as blocked:
                await self._join(permitted, authorized[0])
                await self._join(blocked, unauthorized)
                await server.publish_channel_message(
                    game.game_id, "wolf", {"text": "wolves only"}
                )
                received = json.loads(await permitted.recv())
                self.assertEqual(received["type"], "chat.message")
                self.assertEqual(
                    received["payload"],
                    {"channel": "wolf", "message": {"text": "wolves only"}},
                )
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(blocked.recv(), timeout=0.05)
        finally:
            await server.close()

    async def test_authenticated_chat_request_is_authorized_by_core_and_delivered_to_channel(self) -> None:
        game = make_game()
        game._enter_phase(GamePhase.DAY, 1)
        registry = GameRegistry({game.game_id: game})
        server = WebSocketGameServer(registry, tick_interval_seconds=3600)
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        try:
            async with connect(uri) as sender, connect(uri) as recipient:
                await self._join(sender, "player-0")
                await self._join(recipient, "player-1")
                await sender.send(
                    json.dumps(client_message("chat.send", {"message": "hello"}))
                )
                sender_message = json.loads(await sender.recv())
                recipient_message = json.loads(await recipient.recv())
                self.assertEqual(sender_message["type"], "chat.message")
                self.assertEqual(sender_message["type"], recipient_message["type"])
                self.assertEqual(sender_message["seq"], recipient_message["seq"])
                self.assertEqual(sender_message["payload"], recipient_message["payload"])
                self.assertEqual(
                    sender_message["payload"],
                    {
                        "channel": "public",
                        "message": {
                            "player_id": "player-0",
                            "display_name": "Player 0",
                            "message": "hello",
                        },
                    },
                )
        finally:
            await server.close()

    async def test_dead_player_receives_only_public_events_by_default(self) -> None:
        game = make_game()
        registry = GameRegistry({game.game_id: game})
        server = WebSocketGameServer(
            registry,
            ticker=TickDriver(registry, clock=lambda: 0),
            tick_interval_seconds=0.001,
        )
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        try:
            async with connect(uri) as dead_socket, connect(uri) as live_socket:
                await self._join(dead_socket, "player-0")
                await self._join(live_socket, "player-1")
                game._record_player_death("player-0", "attacked")
                dead_event = json.loads(await dead_socket.recv())
                live_event = json.loads(await live_socket.recv())
                self.assertEqual(dead_event["payload"], live_event["payload"])
                self.assertEqual(dead_event["payload"]["event_type"], "PLAYER_DIED")
                self.assertNotIn("cause", dead_event["payload"]["event_payload"])
                self.assertNotIn("role_id", dead_event["payload"]["event_payload"])
                game.event_bus.publish(
                    GameEvent(
                        "POST_DEATH_PRIVATE",
                        EventVisibility.PRIVATE,
                        {"secret": "not-for-the-dead"},
                        recipient_player_id="player-0",
                    )
                )
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(dead_socket.recv(), timeout=0.05)
        finally:
            await server.close()

    async def test_dead_player_without_public_view_cannot_receive_public_events(self) -> None:
        content = load_content(PROJECT_ROOT / "content")
        preset = load_preset(PROJECT_ROOT / "content" / "presets" / "standard_9.yaml", content)
        rules = replace(
            preset.rules,
            graveyard=replace(preset.rules.graveyard, view_public=False),
        )
        game = make_game(rules=rules)
        registry = GameRegistry({game.game_id: game})
        server = WebSocketGameServer(
            registry,
            ticker=TickDriver(registry, clock=lambda: 0),
            tick_interval_seconds=0.001,
        )
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        try:
            async with connect(uri) as dead_socket, connect(uri) as live_socket:
                await self._join(dead_socket, "player-0")
                await self._join(live_socket, "player-1")
                game._record_player_death("player-0", "attacked")
                received = json.loads(await live_socket.recv())
                self.assertEqual(received["type"], "game.event")
                self.assertEqual(received["payload"]["event_type"], "PLAYER_DIED")
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(dead_socket.recv(), timeout=0.05)
        finally:
            await server.close()
    async def test_server_ticker_completes_a_standard_game_without_manual_phase_calls(self) -> None:
        content = load_content(PROJECT_ROOT / "content")
        preset = load_preset(PROJECT_ROOT / "content" / "presets" / "standard_9.yaml", content)
        rules = replace(
            preset.rules,
            night_seconds=1,
            silence_after_dawn_seconds=1,
            day_seconds=1,
            vote_seconds=1,
        )
        game = make_game(rules=rules)
        registry = GameRegistry({game.game_id: game})
        current_time = 0

        def tick_clock() -> int:
            nonlocal current_time
            current_time += 1
            return current_time

        server = WebSocketGameServer(
            registry,
            ticker=TickDriver(registry, clock=tick_clock),
            tick_interval_seconds=0.001,
        )
        listener = await server.start("127.0.0.1", 0)
        try:
            with self.assertNoLogs("server.network.session", level="ERROR"):
                for _ in range(40):
                    if game.game_result is not None:
                        break
                    await asyncio.sleep(0.01)
            self.assertIsNotNone(listener)
            self.assertIsNotNone(game.game_result)
        finally:
            await server.close()

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
                await other_socket.wait_closed()
                self.assertEqual(other_socket.close_code, 1008)

                async with connect(uri) as resuming_socket:
                    await resuming_socket.send(
                        json.dumps(
                            client_message(
                                "session.resume",
                                {
                                    "connection_token": joined["payload"]["connection_token"],
                                    "last_seq": 1,
                                },
                            )
                        )
                    )
                    resumed = json.loads(await resuming_socket.recv())
                    self.assertEqual(resumed["type"], "session.resumed")
                    self.assertEqual(resumed["seq"], 2)
                    await joined_socket.wait_closed()
                    self.assertEqual(joined_socket.close_code, 4001)
                    self.assertEqual(server.sessions.session_for(GAME_ID).connection_count("player-0"), 1)
        finally:
            await server.close()

    async def test_outbound_schema_failure_is_logged_and_closes_only_that_connection(self) -> None:
        class OutboundFailingValidator:
            def validate_client(self, message: object) -> None:
                return None

            def validate_server(self, message: object) -> None:
                raise ProtocolValidationError("forced outbound validation failure")

        game = make_game()
        registry = GameRegistry({game.game_id: game})
        sessions = SessionManager(
            registry,
            token_factory=lambda: "token-for-player-0",
            validator=OutboundFailingValidator(),
        )
        server = WebSocketGameServer(registry, sessions=sessions, tick_interval_seconds=3600)
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        try:
            async with connect(uri) as socket:
                with self.assertLogs("server.network.server", level="ERROR"):
                    await socket.send(
                        json.dumps(client_message("session.join", {"player_id": "player-0"}))
                    )
                    await socket.wait_closed()
                self.assertEqual(socket.close_code, 1011)
                self.assertEqual(sessions.session_for(GAME_ID).connection_count("player-0"), 0)
        finally:
            await server.close()


if __name__ == "__main__":
    unittest.main()
