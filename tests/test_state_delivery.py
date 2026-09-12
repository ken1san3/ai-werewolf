from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import unittest

from websockets.asyncio.client import connect

from server.aiwolf_core import EventVisibility, GameEvent, GamePhase
from server.network import GameRegistry, SessionManager, TickDriver, WebSocketGameServer
from server.network.delivery import EventDeliveryRouter
from server.network.protocol import ProtocolMessageValidator, ProtocolValidationError
from server.network.session import UnaddressableRequest
from tests.test_network_sessions import GAME_ID, client_message, join_message, make_game


def role_player(game, role_id):
    return next(player.player_id for player in game.players.values() if player.role.id == role_id)


class PlayerViewTests(unittest.TestCase):
    def test_snapshot_is_detached_and_has_only_own_private_events(self):
        game = make_game()
        seer = role_player(game, "seer")
        other = role_player(game, "villager")
        game.event_bus.publish(GameEvent("SECRET", EventVisibility.PRIVATE, {"result": "own"}, seer))
        game.event_bus.publish(GameEvent("SECRET", EventVisibility.PRIVATE, {"result": "other"}, other))
        game.event_bus.publish(GameEvent("INTERNAL", EventVisibility.SERVER, {"cause": "cursed"}))
        game.event_bus.publish(GameEvent("THOUGHT", EventVisibility.AI, {"thought": "hidden"}))
        snapshot = game.get_state_sync(seer)
        self.assertEqual(snapshot["self"], {"player_id": seer, "role_id": "seer", "modifier_ids": []})
        self.assertEqual(snapshot["revealed_roles"], [])
        private_results = [entry["payload"]["event_payload"]["result"] for entry in snapshot["history"]
                           if entry["payload"].get("event_type") == "SECRET"]
        self.assertEqual(private_results, ["own"])
        for entry in snapshot["history"]:
            self.assertNotIn("sequence", entry)
            self.assertNotIn("visibility", entry)
            self.assertNotIn(entry["payload"].get("event_type"), {"INTERNAL", "THOUGHT"})
        snapshot["history"].clear()
        snapshot["players"][0]["display_name"] = "mutated"
        self.assertTrue(game.get_state_sync(seer)["history"])
        self.assertNotEqual(game.get_player_list()["players"][0]["display_name"], "mutated")

    def test_deaths_are_full_public_records_for_each_disclosure_mode(self):
        for mode in ("phase", "cause", "none"):
            with self.subTest(mode=mode):
                game = make_game()
                game.rules = replace(game.rules, death=replace(game.rules.death, public_detail=mode))
                game._record_player_death("player-0", "attacked")
                game._enter_phase(GamePhase.DAY, 10)
                game._record_player_death("player-1", "sudden_death")
                deaths = game.get_player_deaths("player-2")["deaths"]
                self.assertEqual([death["player_id"] for death in deaths], ["player-0", "player-1"])
                self.assertTrue(all("cause" not in death and "phase" not in death for death in deaths))
                if mode == "phase":
                    self.assertEqual([death["public_cause"] for death in deaths], ["died_in_night", "died_in_day"])
                elif mode == "cause":
                    self.assertEqual([death["public_cause"] for death in deaths], ["attacked", "sudden_death"])
                else:
                    self.assertTrue(all("public_cause" not in death for death in deaths))

    def test_dead_reconnect_retains_known_information_without_new_private_or_hidden_public_data(self):
        for view_public in (False, True):
            with self.subTest(view_public=view_public):
                game = make_game()
                game.rules = replace(game.rules, graveyard=replace(game.rules.graveyard, view_public=view_public))
                game.event_bus.publish(GameEvent("BEFORE", EventVisibility.PRIVATE, {}, "player-0"))
                before = game.get_state_sync("player-0")
                game._record_player_death("player-0", "attacked")
                game._enter_phase(GamePhase.DAY, 10)
                game.event_bus.publish(GameEvent("AFTER", EventVisibility.PRIVATE, {}, "player-0"))
                game._record_player_death("player-1", "sudden_death")
                snapshot = game.get_state_sync("player-0")
                types = [entry["payload"].get("event_type") for entry in snapshot["history"]]
                self.assertIn("BEFORE", types)
                self.assertNotIn("AFTER", types)
                self.assertEqual(snapshot["action_state"]["actions"], [])
                self.assertEqual(snapshot["revealed_roles"], [])
                if view_public:
                    self.assertEqual(len(snapshot["deaths"]), 2)
                    self.assertEqual(snapshot["action_state"]["phase"], "day")
                else:
                    self.assertEqual(snapshot["history"], before["history"])
                    self.assertEqual(snapshot["deaths"], before["deaths"])
                    self.assertEqual(snapshot["action_state"]["phase"], before["action_state"]["phase"])

    def test_role_reveal_is_only_for_dead_players_when_explicitly_enabled(self):
        game = make_game()
        game.rules = replace(game.rules, graveyard=replace(game.rules.graveyard, reveal_roles=True))
        self.assertEqual(game.get_state_sync("player-0")["revealed_roles"], [])
        game._record_player_death("player-0", "attacked")
        self.assertEqual(game.get_state_sync("player-0")["revealed_roles"], [
            {"player_id": p.player_id, "role_id": p.role.id} for p in game.players.values()
        ])
        self.assertEqual(game.get_state_sync("player-1")["revealed_roles"], [])

    def test_core_chat_history_keeps_only_public_and_authorized_channels(self):
        game = make_game()
        wolf = role_player(game, "werewolf")
        villager = role_player(game, "villager")
        game.submit_chat(game.phase_started_at, wolf, "wolf", "private chat")
        game._enter_phase(GamePhase.DAY, 10)
        game.submit_chat(11, villager, "public", "public chat")
        for player_id, expected in ((wolf, ["private chat", "public chat"]), (villager, ["public chat"])):
            chats = [entry["payload"]["message"]["message"] for entry in game.get_state_sync(player_id)["history"]
                     if entry["type"] == "chat.message"]
            self.assertEqual(chats, expected)


    def test_phase_actions_match_core_including_guard_constraints_and_dead_players(self):
        game = make_game()
        guard = role_player(game, "guard")
        self.assertFalse(any(action.get("ability_id") == "attack" for action in
                             game.get_action_state(role_player(game, "werewolf"))["actions"]))
        for phase in (GamePhase.NIGHT, GamePhase.DAWN, GamePhase.DAY, GamePhase.VOTE, GamePhase.GAME_END):
            game._enter_phase(phase, 10)
            payload = game.get_action_state(guard)
            self.assertEqual(payload["phase"], phase.value)
            self.assertEqual(payload["phase_ends_at"], game.phase_ends_at)
            self.assertEqual([a["type"] for a in payload["actions"]],
                             [a.type for a in game.get_available_actions(guard)])
            for action in payload["actions"]:
                if action.get("ability_id") == "protect":
                    self.assertNotIn(guard, action["valid_targets"])
        game._enter_phase(GamePhase.NIGHT, 20)
        game._record_player_death(guard, "attacked")
        self.assertEqual(game.get_action_state(guard)["actions"], [])


class DeliveryMetadataTests(unittest.TestCase):
    def test_chat_acceptance_is_internal_metadata_and_server_publication_has_none(self):
        game = make_game()
        game.day = 1
        game._enter_phase(GamePhase.DAY, 10)
        submission = game.submit_chat(11, "player-0", "public", "hello")
        router = EventDeliveryRouter(
            {game.game_id: game},
            connected_player_ids=lambda game_id: tuple(game.players),
        )

        router.queue_channel_message(
            game.game_id,
            submission.channel_id,
            submission.message,
            acceptance=submission.acceptance,
        )
        accepted_delivery = router.drain()[0]
        self.assertIs(accepted_delivery.acceptance, submission.acceptance)
        self.assertEqual(
            accepted_delivery.payload,
            {"channel": "public", "message": submission.message},
        )
        self.assertNotIn("accepted_at", json.dumps(accepted_delivery.payload))
        self.assertNotIn("phase_deadline", json.dumps(accepted_delivery.payload))

        router.queue_channel_message(game.game_id, "public", {"text": "server message"})
        server_delivery = router.drain()[0]
        self.assertIsNone(server_delivery.acceptance)
        self.assertEqual(
            server_delivery.payload,
            {"channel": "public", "message": {"text": "server message"}},
        )


class StateSchemaTests(unittest.TestCase):
    def test_all_state_types_are_validated_and_reject_hidden_or_missing_fields(self):
        game = make_game()
        manager = SessionManager(GameRegistry({GAME_ID: game}), clock=lambda: 0)
        context = manager.handle_message(join_message(manager.registry, "player-0")).context
        validator = ProtocolMessageValidator()
        for message_type, payload in (
            ("player.list", game.get_player_list()),
            ("player.deaths", game.get_player_deaths("player-0")),
            ("player.action_state", game.get_action_state("player-0")),
            ("game.state_sync", game.get_state_sync("player-0")),
        ):
            with self.subTest(message_type=message_type):
                message = manager.server_event(context, message_type, payload).as_message()
                validator.validate_server(message)
                with self.assertRaises(ProtocolValidationError):
                    validator.validate_server(dict(message, payload={}))
                with self.assertRaises(ProtocolValidationError):
                    validator.validate_server(dict(message, payload={**payload, "pending_votes": {}}))
        for phase in GamePhase:
            game._enter_phase(phase, 10)
            manager.state_sync(context)

    def test_resume_replays_only_newer_own_events_and_rejects_future_cursor(self):
        game = make_game()
        manager = SessionManager(GameRegistry({GAME_ID: game}), clock=lambda: 0)
        first = manager.handle_message(join_message(manager.registry, "player-0"))
        second = manager.handle_message(join_message(manager.registry, "player-1"))
        own = manager.server_event(first.context, "game.event", {"event_type": "OWN", "event_payload": {}})
        manager.server_event(second.context, "game.event", {"event_type": "OTHER", "event_payload": {}})
        request = client_message("session.resume", {"connection_token": first.context.connection_token, "last_seq": 999})
        with self.assertRaises(UnaddressableRequest):
            manager.handle_message(request)
        self.assertEqual(manager.session_for(GAME_ID).connection_count("player-0"), 1)
        request["payload"]["last_seq"] = 1
        resumed = manager.handle_message(request)
        self.assertEqual([event.as_message() for event in resumed.replay], [own.as_message()])
        self.assertEqual(resumed.reply.seq, own.seq + 1)


class StateWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.game = make_game()
        self.registry = GameRegistry({GAME_ID: self.game})
        self.now = 0
        self.server = WebSocketGameServer(
            self.registry, sessions=SessionManager(self.registry, clock=lambda: self.now),
            ticker=TickDriver(self.registry, clock=lambda: self.now), tick_interval_seconds=0.01,
        )
        listener = await self.server.start("127.0.0.1", 0)
        self.uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        self.validator = ProtocolMessageValidator()

    async def asyncTearDown(self):
        await self.server.close()

    async def receive(self, socket):
        message = json.loads(await asyncio.wait_for(socket.recv(), timeout=2))
        self.validator.validate_server(message)
        return message

    async def join(self, socket, player_id):
        await socket.send(json.dumps(join_message(self.registry, player_id)))
        joined = await self.receive(socket)
        sync = await self.receive(socket)
        self.assertEqual(sync["type"], "game.state_sync")
        return joined, sync

    async def test_join_and_phase_start_deliver_full_state_without_per_action_pushes(self):
        guard = role_player(self.game, "guard")
        wolf = role_player(self.game, "werewolf")
        async with connect(self.uri) as first, connect(self.uri) as second:
            _, guard_sync = await self.join(first, guard)
            _, wolf_sync = await self.join(second, wolf)
            self.assertEqual(guard_sync["payload"]["self"]["role_id"], "guard")
            self.assertEqual(wolf_sync["payload"]["self"]["role_id"], "werewolf")
            self.game.day = 1
            self.game._enter_phase(GamePhase.NIGHT, 10)
            self.now = 11
            for socket, player_id in ((first, guard), (second, wolf)):
                messages = [await self.receive(socket) for _ in range(4)]
                self.assertEqual([m["type"] for m in messages],
                                 ["game.event", "player.list", "player.deaths", "player.action_state"])
                self.assertEqual([m["seq"] for m in messages], [3, 4, 5, 6])
                self.assertEqual(messages[-1]["payload"], self.game.get_action_state(player_id))
            await first.send(json.dumps(client_message("ability.use", {"ability_id": "protect", "target_player_ids": [wolf]})))
            await first.send(json.dumps(client_message("session.ready", {})))
            accepted = await self.receive(first)
            self.assertEqual(accepted["type"], "action.accepted")
            self.assertEqual(accepted["seq"], 7)
            self.assertEqual(
                accepted["payload"],
                {
                    "action": "ability.use",
                    "request_event_id": "123e4567-e89b-12d3-a456-426614174101",
                },
            )
            ready = await self.receive(first)
            self.assertEqual(ready["type"], "session.ready")
            self.assertEqual(ready["seq"], 8)
            self.assertIn(guard, self.game.pending_actions)
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(first.recv(), timeout=0.03)
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(second.recv(), timeout=0.03)

    async def test_resume_snapshot_alone_restores_offline_results_chat_co_and_deaths(self):
        seer = role_player(self.game, "seer")
        villager = role_player(self.game, "villager")
        async with connect(self.uri) as old:
            joined, initial = await self.join(old, seer)
        async def disconnected():
            while self.server.sessions.session_for(GAME_ID).connection_count(seer):
                await asyncio.sleep(0)
        await asyncio.wait_for(disconnected(), timeout=2)
        self.game.rules = replace(self.game.rules, night_action=replace(self.game.rules.night_action, no_selection="skip"))
        self.game.day = 1
        self.game._enter_phase(GamePhase.NIGHT, 10)
        self.game.submit_action(11, seer, "inspect", (villager,))
        self.game.advance_if_due(self.game.phase_ends_at)
        self.game._enter_phase(GamePhase.DAY, self.game.phase_ends_at)
        self.game.declare_co(self.game.phase_started_at, seer, "seer", "my claim")
        self.game.report_co(
            self.game.phase_started_at, seer, "inspect_result", villager, "not_wolf"
        )
        self.game._record_player_death(villager, "sudden_death")
        await self.server.publish_channel_message(GAME_ID, "public", {"text": "offline public"})
        await self.server.publish_channel_message(GAME_ID, "wolf", {"text": "offline wolves"})
        expected = self.game.get_state_sync(seer)
        async with connect(self.uri) as resumed, connect(self.uri) as other:
            await self.join(other, villager)
            await resumed.send(json.dumps(client_message("session.resume", {
                "connection_token": joined["payload"]["connection_token"], "last_seq": initial["seq"],
            })))
            ack = await self.receive(resumed)
            snapshot = await self.receive(resumed)
            self.assertEqual(ack["type"], "session.resumed")
            self.assertEqual(snapshot["type"], "game.state_sync")
            self.assertEqual(snapshot["payload"], expected)
            self.assertEqual(snapshot["seq"], ack["seq"] + 1)
            # Reconstruct from this one message, with no previous client state.
            restored = snapshot["payload"]
            alive = {p["player_id"] for p in restored["players"]} - {d["player_id"] for d in restored["deaths"]}
            self.assertEqual(alive, {p.player_id for p in self.game.players.values() if p.alive})
            event_types = [e["payload"].get("event_type") for e in restored["history"]]
            self.assertIn("INSPECT_RESULT", event_types)
            self.assertIn("CO_DECLARED", event_types)
            self.assertIn("CO_REPORTED", event_types)
            self.assertIn("offline public", json.dumps(restored))
            self.assertNotIn("offline wolves", json.dumps(restored))
            self.assertNotIn("cursed", json.dumps(restored))
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(other.recv(), timeout=0.03)

    async def test_invalid_token_never_receives_a_snapshot(self):
        async with connect(self.uri) as socket:
            await socket.send(json.dumps(client_message("session.resume", {"connection_token": "wrong", "last_seq": 0})))
            await socket.wait_closed()
            self.assertEqual(socket.close_code, 1008)
            self.assertEqual(self.server.sessions.session_for(GAME_ID).connected_player_ids, frozenset())

    async def test_resume_replay_precedes_authentication_and_snapshot(self):
        async with connect(self.uri) as old:
            joined, initial = await self.join(old, "player-0")
            self.game.event_bus.publish(GameEvent("MISSED", EventVisibility.PRIVATE, {"value": "own"}, "player-0"))
            missed = await self.receive(old)
            async with connect(self.uri) as resumed:
                await resumed.send(json.dumps(client_message("session.resume", {
                    "connection_token": joined["payload"]["connection_token"], "last_seq": initial["seq"],
                })))
                replay = await self.receive(resumed)
                ack = await self.receive(resumed)
                sync = await self.receive(resumed)
                self.assertEqual(replay, missed)
                self.assertEqual([m["type"] for m in (ack, replay, sync)],
                                 ["session.resumed", "game.event", "game.state_sync"])
                self.assertEqual([m["seq"] for m in (replay, ack, sync)], [3, 4, 5])
                await old.wait_closed()
                self.assertEqual(old.close_code, 4001)
                self.game.event_bus.publish(GameEvent("AFTER_SYNC", EventVisibility.PUBLIC, {}))
                after = await self.receive(resumed)
                self.assertEqual(after["seq"], 6)
                self.assertEqual(after["payload"]["event_type"], "AFTER_SYNC")

    async def test_ticker_sends_dawn_then_day_and_cumulative_deaths(self):
        async with connect(self.uri) as socket:
            await self.join(socket, "player-0")
            self.now = self.game.phase_ends_at
            messages = []
            while not messages or messages[-1]["type"] != "player.action_state":
                messages.append(await self.receive(socket))
            dawn = messages[-1]
            self.assertEqual(dawn["payload"]["phase"], "dawn")
            self.assertEqual(dawn["payload"]["phase_ends_at"], self.game.phase_ends_at)
            self.now = self.game.phase_ends_at
            messages = []
            while not messages or not (messages[-1]["type"] == "player.action_state"
                                        and messages[-1]["payload"]["phase"] == "day"):
                messages.append(await self.receive(socket))
            self.assertEqual(messages[-2]["type"], "player.deaths")
            self.game._record_player_death("player-1", "sudden_death")
            self.game._record_player_death("player-2", "sudden_death")
            self.now = self.game.phase_ends_at
            messages = []
            while not messages or not (messages[-1]["type"] == "player.action_state"
                                        and messages[-1]["payload"]["phase"] == "vote"):
                messages.append(await self.receive(socket))
            deaths = messages[-2]["payload"]["deaths"]
            self.assertEqual([d["player_id"] for d in deaths], ["player-1", "player-2"])
            self.assertEqual({d["public_cause"] for d in deaths}, {"died_in_day"})
            vote = next(a for a in messages[-1]["payload"]["actions"] if a["type"] == "vote")
            self.assertNotIn("player-1", vote["valid_targets"])
            self.assertNotIn("player-2", vote["valid_targets"])
