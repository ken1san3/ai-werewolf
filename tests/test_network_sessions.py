from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from random import Random
from pathlib import Path
import unittest
from unittest.mock import patch

from websockets.asyncio.client import connect

from server.aiwolf_core import (
    ActionRejected,
    ChatSubmission,
    EventVisibility,
    GamePhase,
    GameState,
    GameEvent,
    InMemoryEventSink,
    InteractionAcceptance,
    PlayerConfig,
    load_content,
    load_preset,
)
from server.network import GameRegistry, SessionManager, TickDriver, WebSocketGameServer
from server.network.protocol import (
    PROTOCOL_VERSION,
    ProtocolMessageValidator,
    ProtocolValidationError,
)
from server.network.session import UnaddressableRequest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GAME_ID = "123e4567-e89b-12d3-a456-426614174100"
SCHEMA_PATH = PROJECT_ROOT / "protocol" / "aiwolf-v1.2.schema.json"
NON_CANONICAL_REQUEST_IDS = (
    "123e4567e89b12d3a456426614174101",
    "{123e4567-e89b-12d3-a456-426614174101}",
    "urn:uuid:123e4567-e89b-12d3-a456-426614174101",
    "123E4567-E89B-12D3-A456-426614174101",
    "bad",
)


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
    protocol_version: str = PROTOCOL_VERSION,
    game_id: str = GAME_ID,
    event_id: str = "123e4567-e89b-12d3-a456-426614174101",
) -> dict[str, object]:
    return {
        "type": message_type,
        "protocol_version": protocol_version,
        "event_id": event_id,
        "game_id": game_id,
        "timestamp": 0,
        "payload": payload,
    }


def join_message(
    registry, player_id, *, game_id=GAME_ID, protocol_version=PROTOCOL_VERSION
):
    return client_message("session.join", {"entry_token": registry.entry_tokens_for(game_id)[player_id]},
                          game_id=game_id, protocol_version=protocol_version)


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

    @staticmethod
    def communication_message(action: str) -> dict[str, object]:
        payloads = {
            "chat.send": {"channel_id": "public", "message": "hello"},
            "co.declare": {"claimed_role_id": "seer", "comment": "claim"},
            "co.report": {
                "kind": "inspect_result",
                "target_player_id": "player-1",
                "claimed_result": "not_wolf",
            },
        }
        return client_message(action, payloads[action])

    def test_join_issues_token_only_to_the_joined_session_and_ready_marks_seat(self) -> None:
        result = self.manager.handle_message(
            join_message(self.registry, "player-0")
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
            join_message(self.registry, "player-0")
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

    def test_non_exact_protocol_versions_and_unknown_player_are_rejected_without_token(self) -> None:
        for version in ("1.0", "1.1", "2.0"):
            with self.subTest(version=version), self.assertRaisesRegex(
                UnaddressableRequest, "unsupported_protocol_version"
            ):
                self.manager.handle_message(
                    join_message(self.registry, "player-0", protocol_version=version)
                )
        self.assertFalse(self.manager.session_for(GAME_ID).has_joined("player-0"))

        with self.assertRaises(UnaddressableRequest):
            self.manager.handle_message(
                client_message("session.join", {"entry_token": "unknown-entry"})
            )

    def test_authenticated_version_mismatch_is_rejected_before_core_mutation(self) -> None:
        self.game._enter_phase(GamePhase.VOTE, 1)
        joined = self.manager.handle_message(join_message(self.registry, "player-0"))

        result = self.manager.handle_message(
            client_message(
                "vote.cast",
                {"target_player_id": "player-1"},
                protocol_version="1.0",
            ),
            joined.context,
        )

        self.assertEqual(result.reply.type, "action.rejected")
        self.assertEqual(result.reply.payload["reason"], "unsupported_protocol_version")
        self.assertEqual(
            result.reply.payload["request_event_id"],
            "123e4567-e89b-12d3-a456-426614174101",
        )
        self.assertEqual(self.game.pending_votes, {})

    def test_noncanonical_request_uuids_are_nullable_retained_rejections(self) -> None:
        joined = self.manager.handle_message(join_message(self.registry, "player-0"))
        replies = []

        for event_id in NON_CANONICAL_REQUEST_IDS:
            with self.subTest(event_id=event_id):
                result = self.manager.handle_message(
                    client_message(
                        "vote.cast",
                        {"target_player_id": "player-1"},
                        event_id=event_id,
                    ),
                    joined.context,
                )
                self.assertEqual(result.reply.type, "action.rejected")
                self.assertEqual(result.reply.payload["reason"], "invalid_message")
                self.assertIsNone(result.reply.payload["request_event_id"])
                self.validator.validate_server(result.reply.as_message())
                replies.append(result.reply)

        self.assertEqual(
            [reply.seq for reply in replies],
            list(range(joined.reply.seq + 1, joined.reply.seq + 1 + len(replies))),
        )
        self.assertEqual(self.game.pending_votes, {})
        self.manager.disconnect(joined.context)
        resumed = self.manager.handle_message(
            client_message(
                "session.resume",
                {
                    "connection_token": joined.reply.payload["connection_token"],
                    "last_seq": joined.reply.seq,
                },
            )
        )

        self.assertEqual(resumed.replay, tuple(replies))

    def test_disconnect_keeps_the_game_and_seat_intact(self) -> None:
        result = self.manager.handle_message(
            join_message(self.registry, "player-0")
        )
        context = result.context
        phase_before = self.game.phase

        self.manager.disconnect(context)

        self.assertIn("player-0", self.game.players)
        self.assertEqual(self.game.phase, phase_before)
        self.assertEqual(self.manager.session_for(GAME_ID).connected_player_ids, frozenset())

    def test_sequences_are_contiguous_per_player_and_continue_across_reconnection(self) -> None:
        first = self.manager.handle_message(join_message(self.registry, "player-0"))
        first_ready = self.manager.handle_message(client_message("session.ready", {}), first.context)
        second = self.manager.handle_message(join_message(self.registry, "player-1"))
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

    def test_action_results_are_retained_and_replayed_with_original_request_ids(self) -> None:
        self.game._enter_phase(GamePhase.VOTE, 1)
        joined = self.manager.handle_message(join_message(self.registry, "player-0"))
        request_id = "123e4567-e89b-12d3-a456-426614174199"
        accepted = self.manager.handle_message(
            client_message(
                "vote.cast",
                {"target_player_id": "player-1"},
                event_id=request_id,
            ),
            joined.context,
        )
        rejected_request_id = "123e4567-e89b-12d3-a456-426614174177"
        rejected = self.manager.handle_message(
            client_message(
                "vote.cast",
                {"target_player_id": "player-0"},
                event_id=rejected_request_id,
            ),
            joined.context,
        )
        self.manager.disconnect(joined.context)

        resumed = self.manager.handle_message(
            client_message(
                "session.resume",
                {
                    "connection_token": joined.reply.payload["connection_token"],
                    "last_seq": joined.reply.seq,
                },
            )
        )

        self.assertEqual(accepted.reply.seq, joined.reply.seq + 1)
        self.assertEqual(accepted.reply.payload["request_event_id"], request_id)
        self.assertEqual(rejected.reply.type, "action.rejected")
        self.assertEqual(rejected.reply.seq, accepted.reply.seq + 1)
        self.assertEqual(
            rejected.reply.payload["request_event_id"], rejected_request_id
        )
        self.assertEqual(resumed.replay, (accepted.reply, rejected.reply))
        self.assertEqual(resumed.reply.seq, rejected.reply.seq + 1)

    def test_non_uuid_game_id_is_valid_for_core_and_protocol_messages(self) -> None:
        game = make_game("standard-nine")
        manager = SessionManager(
            GameRegistry({game.game_id: game}),
            clock=lambda: 12,
            token_factory=lambda: "non-uuid-game-token",
        )

        result = manager.handle_message(
            join_message(manager.registry, "player-0", game_id=game.game_id)
        )

        self.assertEqual(result.reply.game_id, "standard-nine")
        ProtocolMessageValidator().validate_server(result.reply.as_message())

    def test_authenticated_actions_delegate_to_core_and_return_rejections_only_on_failure(self) -> None:
        self.game._enter_phase(GamePhase.DAY, 1)
        joined = self.manager.handle_message(join_message(self.registry, "player-0"))

        chat = self.manager.handle_message(
            client_message("chat.send", {"channel_id": "public", "message": "hello"}), joined.context
        )
        self.assertIsNone(chat.reply)
        self.assertEqual(
            chat.channel_messages,
            (
                ChatSubmission(
                    "public",
                    {"player_id": "player-0", "display_name": "Player 0", "message": "hello"},
                    InteractionAcceptance(
                        "chat.send", "player-0", self.game.day, "day", 12, self.game.phase_ends_at
                    ),
                ),
            ),
        )
        self.assertEqual(
            self.game.public_activity_counts[(self.game.day, "player-0")], 1
        )

        self.game._record_player_death("player-0", "lynched")
        rejected = self.manager.handle_message(
            client_message("co.declare", {"claimed_role_id": "seer", "comment": "too late"}),
            joined.context,
        )
        self.assertIsNotNone(rejected.reply)
        self.assertEqual(rejected.reply.type, "action.rejected")
        self.assertEqual(rejected.reply.payload["reason"], "action_unavailable")
        self.assertEqual(
            rejected.reply.payload["request_event_id"],
            "123e4567-e89b-12d3-a456-426614174101",
        )

    def test_authenticated_dispatch_samples_one_receipt_time_for_core_actions(self) -> None:
        class CountingClock:
            def __init__(self) -> None:
                self.value = 37
                self.calls: list[int] = []

            def __call__(self) -> int:
                self.calls.append(self.value)
                return self.value

        game = make_game()
        registry = GameRegistry({game.game_id: game})
        clock = CountingClock()
        manager = SessionManager(registry, clock=clock)
        context = manager.handle_message(join_message(registry, "player-0")).context
        clock.calls.clear()
        acceptance = InteractionAcceptance("chat.send", "player-0", 1, "day", 37, 40)

        with patch.object(
            game,
            "submit_chat",
            return_value=ChatSubmission(
                "public",
                {"player_id": "player-0", "display_name": "Player 0", "message": "hello"},
                acceptance,
            ),
        ) as submit_chat:
            result = manager.handle_message(self.communication_message("chat.send"), context)
            self.assertEqual(result.channel_messages[0].acceptance, acceptance)
            submit_chat.assert_called_once_with(37, "player-0", "public", "hello")

        with patch.object(game, "declare_co", return_value=acceptance) as declare_co:
            manager.handle_message(self.communication_message("co.declare"), context)
            declare_co.assert_called_once_with(37, "player-0", "seer", "claim")

        with patch.object(game, "report_co", return_value=acceptance) as report_co:
            manager.handle_message(self.communication_message("co.report"), context)
            report_co.assert_called_once_with(
                37, "player-0", "inspect_result", "player-1", "not_wolf"
            )

        with patch.object(game, "submit_action") as submit_action:
            accepted = manager.handle_message(
                client_message(
                    "ability.use", {"ability_id": "inspect", "target_player_ids": ["player-1"]}
                ),
                context,
            )
            submit_action.assert_called_once_with(37, "player-0", "inspect", ("player-1",))
            self.assertEqual(accepted.reply.type, "action.accepted")

        self.assertEqual(clock.calls, [37, 37, 37, 37, 37])

    def test_core_owns_deadline_rejection_and_reply_clock_is_not_authorization(self) -> None:
        class SequenceClock:
            def __init__(self) -> None:
                self.values = iter((1, 110, 777))
                self.calls: list[int] = []

            def __call__(self) -> int:
                value = next(self.values)
                self.calls.append(value)
                return value

        game = make_game()
        registry = GameRegistry({game.game_id: game})
        clock = SequenceClock()
        manager = SessionManager(registry, clock=clock)
        context = manager.handle_message(join_message(registry, "player-0")).context
        with patch.object(
            game, "submit_chat", side_effect=ActionRejected("action_deadline_passed")
        ) as submit_chat:
            result = manager.handle_message(self.communication_message("chat.send"), context)

        submit_chat.assert_called_once_with(110, "player-0", "public", "hello")
        self.assertEqual(clock.calls, [1, 110, 777])
        self.assertEqual(result.reply.type, "action.rejected")
        self.assertEqual(result.reply.payload["reason"], "action_deadline_passed")
        self.assertEqual(
            result.reply.payload["request_event_id"],
            "123e4567-e89b-12d3-a456-426614174101",
        )
        self.assertEqual(result.reply.timestamp, 777)

    def test_real_core_rejects_communication_at_exact_deadline_without_delivery(self) -> None:
        for action in ("chat.send", "co.declare", "co.report"):
            with self.subTest(action=action):
                game = make_game()
                game.day = 1
                game._enter_phase(GamePhase.DAY, 100)
                deadline = game.phase_ends_at
                registry = GameRegistry({game.game_id: game})
                manager = SessionManager(registry, clock=lambda: deadline)
                context = manager.handle_message(join_message(registry, "player-0")).context
                before = (
                    dict(game.public_activity_counts),
                    dict(game.co_declaration_counts),
                    len(game.event_bus.events),
                    game.get_state_sync("player-0")["history"],
                )

                result = manager.handle_message(self.communication_message(action), context)

                self.assertEqual(result.reply.payload["reason"], "action_deadline_passed")
                self.assertEqual(result.channel_messages, ())
                self.assertEqual(
                    (
                        dict(game.public_activity_counts),
                        dict(game.co_declaration_counts),
                        len(game.event_bus.events),
                        game.get_state_sync("player-0")["history"],
                    ),
                    before,
                )

    def test_session_preserves_unavailability_in_setup_and_dawn(self) -> None:
        for phase in (GamePhase.SETUP, GamePhase.DAWN):
            for action in ("chat.send", "co.declare", "co.report"):
                game = make_game()
                game.day = 1
                game._enter_phase(phase, 100)
                times = (101,)
                if phase is GamePhase.DAWN:
                    self.assertIsNotNone(game.phase_ends_at)
                    times = (
                        game.phase_ends_at - 1,
                        game.phase_ends_at,
                        game.phase_ends_at + 1,
                    )
                for now in times:
                    with self.subTest(phase=phase.value, action=action, now=now):
                        registry = GameRegistry({game.game_id: game})
                        manager = SessionManager(registry, clock=lambda now=now: now)
                        context = manager.handle_message(
                            join_message(registry, "player-0")
                        ).context
                        result = manager.handle_message(
                            self.communication_message(action), context
                        )
                        self.assertEqual(result.reply.type, "action.rejected")
                        self.assertEqual(result.reply.payload["reason"], "action_unavailable")
                        self.assertEqual(result.channel_messages, ())

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
            join_message(self.registry, wolf_player_id)
        )
        ability = self.manager.handle_message(
            client_message(
                "ability.use", {"ability_id": "attack", "target_player_ids": [target_player_id]}
            ),
            joined.context,
        )
        self.assertEqual(ability.reply.type, "action.accepted")
        self.assertEqual(
            ability.reply.payload,
            {
                "action": "ability.use",
                "request_event_id": "123e4567-e89b-12d3-a456-426614174101",
            },
        )
        self.assertIn(wolf_player_id, self.game.pending_actions)

        self.game._enter_phase(GamePhase.VOTE, 2)
        vote = self.manager.handle_message(
            client_message("vote.cast", {"target_player_id": target_player_id}), joined.context
        )
        self.assertEqual(vote.reply.type, "action.accepted")
        self.assertEqual(
            vote.reply.payload,
            {
                "action": "vote.cast",
                "request_event_id": "123e4567-e89b-12d3-a456-426614174101",
            },
        )
        self.assertEqual(self.game.pending_votes[wolf_player_id], target_player_id)

    def test_vote_and_ability_rejections_expose_safe_distinct_reason_codes(self) -> None:
        player_id = next(
            player_id for player_id, player in self.game.players.items() if not player.role.abilities
        )
        self.game._enter_phase(GamePhase.VOTE, 1)
        joined = self.manager.handle_message(
            join_message(self.registry, player_id)
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
            all(
                set(reply.payload) == {"action", "reason", "request_event_id"}
                for reply in replies
            )
        )
        self.assertTrue(
            all(
                reply.payload["request_event_id"]
                == "123e4567-e89b-12d3-a456-426614174101"
                for reply in replies
            )
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
            "protocol_version": PROTOCOL_VERSION,
            "event_id": "123e4567-e89b-12d3-a456-426614174102",
            "game_id": GAME_ID,
            "seq": 1,
            "timestamp": 0,
            "payload": {"event_type": "PHASE_STARTED", "event_payload": {}, "visibility": "public"},
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
            client_message("chat.send", {"channel_id": "public", "message": "hello"})
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
            validator.validate_client(client_message("chat.send", {"message": "missing channel"}))
        with self.assertRaises(ProtocolValidationError):
            validator.validate_client(
                client_message("chat.send", {"channel": "public", "channel_id": "public", "message": "extra"})
            )

    def test_runtime_validator_rejects_declared_opposite_direction_types(self) -> None:
        validator = ProtocolMessageValidator()
        server_only = client_message(
            "action.accepted",
            {
                "action": "vote.cast",
                "request_event_id": "123e4567-e89b-12d3-a456-426614174101",
            },
        )
        client_only = client_message(
            "vote.cast", {"target_player_id": "player-1"}
        )
        client_only["seq"] = 1

        with self.assertRaisesRegex(ProtocolValidationError, "server-only"):
            validator.validate_client(server_only)
        with self.assertRaisesRegex(ProtocolValidationError, "client-only"):
            validator.validate_server(client_only)


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
    async def _join(self, socket, registry, player_id: str) -> dict[str, object]:
        await socket.send(json.dumps(join_message(registry, player_id)))
        joined = json.loads(await socket.recv())
        sync = json.loads(await socket.recv())
        self.assertEqual(sync["type"], "game.state_sync")
        self.assertEqual(sync["seq"], joined["seq"] + 1)
        ProtocolMessageValidator().validate_server(sync)
        return joined

    async def test_legacy_join_is_closed_before_player_state_or_token_issue(self) -> None:
        game = make_game()
        registry = GameRegistry({game.game_id: game})
        server = WebSocketGameServer(registry, tick_interval_seconds=3600)
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        try:
            async with connect(uri) as socket:
                await socket.send(
                    json.dumps(
                        join_message(registry, "player-0", protocol_version="1.0")
                    )
                )
                await socket.wait_closed()
                self.assertEqual(socket.close_code, 1008)
            self.assertFalse(server.sessions.session_for(GAME_ID).has_joined("player-0"))
        finally:
            await server.close()

    async def test_noncanonical_request_ids_receive_replayable_rejections_not_1011(self) -> None:
        game = make_game()
        registry = GameRegistry({game.game_id: game})
        server = WebSocketGameServer(registry, tick_interval_seconds=3600)
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        rejected_messages = []
        try:
            async with connect(uri) as socket:
                joined = await self._join(socket, registry, "player-0")
                for event_id in NON_CANONICAL_REQUEST_IDS:
                    await socket.send(
                        json.dumps(
                            client_message(
                                "vote.cast",
                                {"target_player_id": "player-1"},
                                event_id=event_id,
                            )
                        )
                    )
                    rejected = json.loads(await socket.recv())
                    ProtocolMessageValidator().validate_server(rejected)
                    self.assertEqual(rejected["type"], "action.rejected")
                    self.assertEqual(rejected["payload"]["reason"], "invalid_message")
                    self.assertIsNone(rejected["payload"]["request_event_id"])
                    rejected_messages.append(rejected)
                await socket.send(json.dumps(client_message("session.ready", {})))
                ready = json.loads(await socket.recv())
                self.assertEqual(ready["type"], "session.ready")

            async with connect(uri) as resumed_socket:
                await resumed_socket.send(
                    json.dumps(
                        client_message(
                            "session.resume",
                            {
                                "connection_token": joined["payload"]["connection_token"],
                                "last_seq": 2,
                            },
                        )
                    )
                )
                replay = [
                    json.loads(await resumed_socket.recv())
                    for _ in range(len(rejected_messages))
                ]
                self.assertEqual(replay, rejected_messages)
                self.assertEqual(
                    json.loads(await resumed_socket.recv())["type"], "session.ready"
                )
                self.assertEqual(
                    json.loads(await resumed_socket.recv())["type"], "session.resumed"
                )
                self.assertEqual(
                    json.loads(await resumed_socket.recv())["type"], "game.state_sync"
                )
        finally:
            await server.close()

    async def test_dispatch_lock_orders_deadline_equality_before_or_after_tick(self) -> None:
        async def exercise(*, request_first: bool):
            class ControlledWebSocket:
                def __init__(self, request_release: asyncio.Event) -> None:
                    self._request_release = request_release
                    self.request_yielded = asyncio.Event()
                    self.sent: list[str] = []
                    self.closed = False

                async def __aiter__(self):
                    yield json.dumps(join_message(registry, "player-0"))
                    await self._request_release.wait()
                    self.request_yielded.set()
                    yield json.dumps(
                        SessionManagerTests.communication_message("chat.send")
                    )

                async def send(self, message: str) -> None:
                    self.sent.append(message)

                async def close(self, *, code: int, reason: str) -> None:
                    self.closed = True

            game = make_game()
            game.day = 1
            game._enter_phase(GamePhase.DAY, 100)
            deadline = game.phase_ends_at
            self.assertIsNotNone(deadline)
            assert deadline is not None
            registry = GameRegistry({game.game_id: game})
            sessions = SessionManager(registry, clock=lambda: deadline)
            ticker = TickDriver(registry, clock=lambda: deadline)
            server = WebSocketGameServer(
                registry,
                sessions=sessions,
                ticker=ticker,
                tick_interval_seconds=3600,
            )
            request_release = asyncio.Event()
            tick_release = asyncio.Event()
            tick_sleep_returned = asyncio.Event()
            join_seen = asyncio.Event()
            request_seen = asyncio.Event()
            tick_seen = asyncio.Event()
            action_results = []
            action_phases = []
            tick_results = []
            tick_phases = []
            websocket = ControlledWebSocket(request_release)
            original_handle_json = sessions.handle_json
            original_advance_once = ticker.advance_once

            def checked_handle_json(raw_message, context=None):
                message_type = json.loads(raw_message)["type"]
                result = original_handle_json(raw_message, context)
                self.assertTrue(server._dispatch_lock.locked())  # noqa: SLF001
                if message_type == "session.join":
                    join_seen.set()
                elif message_type == "chat.send":
                    action_results.append(result)
                    action_phases.append(game.phase)
                    request_seen.set()
                return result

            def checked_advance_once():
                self.assertTrue(server._dispatch_lock.locked())  # noqa: SLF001
                result = original_advance_once()
                tick_results.append(result)
                tick_phases.append(game.phase)
                tick_seen.set()
                return result

            async def controlled_tick_wait(_delay: float) -> None:
                await tick_release.wait()
                tick_sleep_returned.set()
                tick_release.clear()

            with patch.object(
                sessions, "handle_json", side_effect=checked_handle_json
            ), patch.object(
                ticker, "advance_once", side_effect=checked_advance_once
            ), patch(
                "server.network.server.asyncio.sleep", new=controlled_tick_wait
            ):
                handler_task = asyncio.create_task(server._handle_connection(websocket))  # noqa: SLF001
                await join_seen.wait()
                await server._dispatch_lock.acquire()  # noqa: SLF001
                ticker_task = asyncio.create_task(server._tick_forever())  # noqa: SLF001
                if request_first:
                    request_release.set()
                    await websocket.request_yielded.wait()
                    tick_release.set()
                    await tick_sleep_returned.wait()
                else:
                    tick_release.set()
                    await tick_sleep_returned.wait()
                    request_release.set()
                    await websocket.request_yielded.wait()
                self.assertFalse(request_seen.is_set())
                self.assertFalse(tick_seen.is_set())
                server._dispatch_lock.release()  # noqa: SLF001
                await request_seen.wait()
                await tick_seen.wait()
                await handler_task
                ticker_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await ticker_task
                await server.close()

            self.assertEqual(len(action_results), 1)
            self.assertEqual(len(tick_results), 1)
            return action_results[0], action_phases[0], tick_results[0], tick_phases[0]

        request_result, request_phase, tick_result, tick_phase = await exercise(
            request_first=True
        )
        self.assertEqual(request_phase, GamePhase.DAY)
        self.assertEqual(request_result.reply.payload["reason"], "action_deadline_passed")
        self.assertTrue(tick_result[GAME_ID])
        self.assertNotEqual(tick_phase, GamePhase.DAY)

        request_result, request_phase, tick_result, tick_phase = await exercise(
            request_first=False
        )
        self.assertTrue(tick_result[GAME_ID])
        self.assertNotEqual(tick_phase, GamePhase.DAY)
        self.assertEqual(request_phase, tick_phase)
        self.assertEqual(request_result.reply.payload["reason"], "action_unavailable")

    async def test_vote_request_and_tick_have_deterministic_dispatch_lock_order(self) -> None:
        async def exercise(*, request_first: bool):
            game = make_game()
            game.rules = replace(
                game.rules, vote=replace(game.rules.vote, reveal="live")
            )
            game.day = 1
            game._enter_phase(GamePhase.VOTE, 100)
            deadline = game.phase_ends_at
            self.assertIsNotNone(deadline)
            assert deadline is not None
            registry = GameRegistry({game.game_id: game})
            sessions = SessionManager(registry, clock=lambda: deadline - 1)
            ticker = TickDriver(registry, clock=lambda: deadline)
            server = WebSocketGameServer(
                registry,
                sessions=sessions,
                ticker=ticker,
                tick_interval_seconds=3600,
            )
            context = sessions.handle_message(join_message(registry, "player-0")).context
            request_id = "123e4567-e89b-12d3-a456-426614174188"
            request_observations = []
            tick_observations = []
            core_evidence = []
            original_submit_vote = game.submit_vote

            def observed_submit_vote(now, voter_player_id, target_player_id):
                acceptance = original_submit_vote(now, voter_player_id, target_player_id)
                core_evidence.append((target_player_id, request_id, acceptance))
                return acceptance

            async def dispatch_request():
                async with server._dispatch_lock:  # noqa: SLF001
                    self.assertTrue(server._dispatch_lock.locked())  # noqa: SLF001
                    result = sessions.handle_message(
                        client_message(
                            "vote.cast",
                            {"target_player_id": "player-1"},
                            event_id=request_id,
                        ),
                        context,
                    )
                    request_observations.append(
                        (result, game.phase, dict(game.pending_votes))
                    )

            async def dispatch_tick():
                async with server._dispatch_lock:  # noqa: SLF001
                    self.assertTrue(server._dispatch_lock.locked())  # noqa: SLF001
                    tick_observations.append((ticker.advance_once(), game.phase))

            with patch.object(game, "submit_vote", side_effect=observed_submit_vote):
                first = dispatch_request if request_first else dispatch_tick
                second = dispatch_tick if request_first else dispatch_request
                first_task = asyncio.create_task(first())
                await asyncio.sleep(0)
                second_task = asyncio.create_task(second())
                await asyncio.gather(first_task, second_task)
            await server.close()

            event_types = [event.type for event in game.event_bus.events]
            return (
                request_observations[0],
                tick_observations[0],
                tuple(core_evidence),
                dict(game.pending_votes),
                event_types,
                deadline,
            )

        request, tick, evidence, pending, event_types, deadline = await exercise(
            request_first=True
        )
        result, request_phase, pending_during_request = request
        self.assertEqual(request_phase, GamePhase.VOTE)
        self.assertEqual(pending_during_request, {"player-0": "player-1"})
        self.assertEqual(result.reply.type, "action.accepted")
        self.assertEqual(
            result.reply.payload["request_event_id"],
            "123e4567-e89b-12d3-a456-426614174188",
        )
        self.assertEqual(len(evidence), 1)
        target, evidence_request_id, acceptance = evidence[0]
        self.assertEqual(target, "player-1")
        self.assertEqual(evidence_request_id, result.reply.payload["request_event_id"])
        self.assertEqual(acceptance.accepted_at, deadline - 1)
        self.assertEqual(acceptance.phase_deadline, deadline)
        self.assertEqual(pending, {})
        self.assertIn("VOTE_SUBMITTED", event_types)
        self.assertIn("VOTE_REVEALED_LIVE", event_types)
        self.assertTrue(tick[0][GAME_ID])

        request, tick, evidence, pending, event_types, _ = await exercise(
            request_first=False
        )
        result, request_phase, pending_during_request = request
        self.assertNotEqual(request_phase, GamePhase.VOTE)
        self.assertEqual(pending_during_request, {})
        self.assertEqual(result.reply.type, "action.rejected")
        self.assertEqual(result.reply.payload["reason"], "action_unavailable")
        self.assertEqual(evidence, ())
        self.assertEqual(pending, {})
        self.assertNotIn("VOTE_SUBMITTED", event_types)
        self.assertNotIn("VOTE_REVEALED_LIVE", event_types)
        self.assertTrue(tick[0][GAME_ID])

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
                self.assertEqual((await self._join(first, registry, "player-0"))["seq"], 1)
                self.assertEqual((await self._join(second, registry, "player-1"))["seq"], 1)
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
                self.assertEqual([first_public["seq"], first_private["seq"]], [3, 4])
                self.assertEqual(second_public["seq"], 3)
                self.assertEqual(first_public["payload"], {"event_type": "TEST_PUBLIC", "event_payload": {"safe": "yes"}, "visibility": "public"})
                self.assertEqual(first_private["payload"], {"event_type": "TEST_PRIVATE", "event_payload": {"secret": "player-0-only"}, "visibility": "private"})
                self.assertEqual(first_private["payload"]["visibility"], "private")
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
                await self._join(permitted, registry, authorized[0])
                await self._join(blocked, registry, unauthorized)
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
        server = WebSocketGameServer(
            registry,
            sessions=SessionManager(registry, clock=lambda: 2),
            tick_interval_seconds=3600,
        )
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        try:
            async with connect(uri) as sender, connect(uri) as recipient:
                await self._join(sender, registry, "player-0")
                await self._join(recipient, registry, "player-1")
                await sender.send(
                    json.dumps(client_message("chat.send", {"channel_id": "public", "message": "hello"}))
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
                await self._join(dead_socket, registry, "player-0")
                await self._join(live_socket, registry, "player-1")
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
                await self._join(dead_socket, registry, "player-0")
                await self._join(live_socket, registry, "player-1")
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
                    json.dumps(join_message(registry, "player-0"))
                )
                joined = json.loads(await joined_socket.recv())
                self.assertEqual(joined["type"], "session.joined")
                self.assertIn("connection_token", joined["payload"])
                sync = json.loads(await joined_socket.recv())
                self.assertEqual(sync["type"], "game.state_sync")

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
                                    "last_seq": sync["seq"],
                                },
                            )
                        )
                    )
                    resumed = json.loads(await resuming_socket.recv())
                    self.assertEqual(resumed["type"], "session.resumed")
                    self.assertEqual(resumed["seq"], 3)
                    self.assertEqual(json.loads(await resuming_socket.recv())["type"], "game.state_sync")
                    await joined_socket.wait_closed()
                    self.assertEqual(joined_socket.close_code, 4001)
                    self.assertEqual(server.sessions.session_for(GAME_ID).connection_count("player-0"), 1)
        finally:
            await server.close()

    async def test_websocket_resume_enqueues_retained_replay_before_authentication(self) -> None:
        game = make_game()
        registry = GameRegistry({game.game_id: game})
        server = WebSocketGameServer(registry, tick_interval_seconds=3600)
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        try:
            async with connect(uri) as joined_socket:
                await joined_socket.send(json.dumps(join_message(registry, "player-0")))
                joined = json.loads(await joined_socket.recv())
                sync = json.loads(await joined_socket.recv())
                await joined_socket.send(json.dumps(client_message("session.ready", {})))
                ready = json.loads(await joined_socket.recv())
                self.assertEqual(ready["type"], "session.ready")

            async with connect(uri) as resumed_socket:
                await resumed_socket.send(
                    json.dumps(
                        client_message(
                            "session.resume",
                            {
                                "connection_token": joined["payload"]["connection_token"],
                                "last_seq": sync["seq"],
                            },
                        )
                    )
                )
                replayed = json.loads(await resumed_socket.recv())
                self.assertEqual(replayed["type"], "session.ready")
                self.assertEqual(replayed["seq"], ready["seq"])
                first = json.loads(await resumed_socket.recv())
                self.assertEqual(first["type"], "session.resumed")
                self.assertEqual(first["seq"], ready["seq"] + 1)
                self.assertEqual(
                    json.loads(await resumed_socket.recv())["type"], "game.state_sync"
                )
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
                        json.dumps(join_message(registry, "player-0"))
                    )
                    await socket.wait_closed()
                self.assertEqual(socket.close_code, 1011)
                self.assertEqual(sessions.session_for(GAME_ID).connection_count("player-0"), 0)
        finally:
            await server.close()


if __name__ == "__main__":
    unittest.main()
