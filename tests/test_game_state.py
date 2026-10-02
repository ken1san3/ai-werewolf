from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from random import Random
from shutil import copytree
from tempfile import TemporaryDirectory
import unittest

from server.aiwolf_core import (
    EventBus,
    EventSink,
    EventVisibility,
    GameEvent,
    GamePhase,
    GameState,
    InMemoryEventSink,
    JsonlEventLog,
    PlayerConfig,
    load_content,
    load_preset,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = PROJECT_ROOT / "content"
PRESET_PATH = CONTENT_ROOT / "presets" / "standard_9.yaml"


class GameStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = load_content(CONTENT_ROOT)
        self.preset = load_preset(PRESET_PATH, self.content)
        self.player_configs = tuple(
            PlayerConfig(f"player-{index}", f"Player {index}") for index in range(1, 10)
        )

    def create_game(
        self,
        *,
        rng: Random,
        preset=None,
        logs_root: Path | None = None,
        event_sink: EventSink | None = None,
    ) -> GameState:
        return GameState.create_from_preset(
            self.content,
            preset or self.preset,
            self.player_configs,
            game_id="test-game",
            rng=rng,
            logs_root=logs_root,
            event_sink=event_sink,
        )

    def test_standard_preset_builds_setup_state_and_private_assignments(self) -> None:
        sink = InMemoryEventSink()
        game = self.create_game(rng=Random(17), event_sink=sink)

        self.assertEqual(game.phase, GamePhase.NIGHT0)
        self.assertEqual(game.day, 0)
        self.assertEqual(game.pending_actions, {})
        self.assertEqual(set(game.players), {player.player_id for player in self.player_configs})
        self.assertTrue(all(player.alive for player in game.players.values()))
        self.assertTrue(all(player.modifiers == () for player in game.players.values()))
        self.assertEqual(
            Counter(player.role.id for player in game.players.values()),
            Counter(self.preset.role_counts),
        )

        assignments = [event for event in game.event_bus.events if event.type == "ROLE_ASSIGNED"]
        self.assertEqual(len(assignments), len(self.player_configs))
        self.assertTrue(all(event.visibility is EventVisibility.PRIVATE for event in assignments))
        self.assertEqual([event.sequence for event in game.event_bus.events], list(range(1, 12)))
        self.assertEqual(sink.events, list(game.event_bus.events))

    def test_event_destination_must_be_explicit(self) -> None:
        with self.assertRaisesRegex(ValueError, "event_sink or explicit logs_root"):
            GameState.create_from_preset(
                self.content,
                self.preset,
                self.player_configs,
                game_id="missing-event-destination",
                rng=Random(19),
            )

        game = self.create_game(rng=Random(19), logs_root=None)
        self.assertIsInstance(game.event_sink, InMemoryEventSink)

    def test_initial_teammates_are_private_and_only_include_team_core_members(self) -> None:
        counts = {role_id: 1 for role_id in self.content.roles}
        counts["fox"] = 2
        game = GameState.create_from_preset(
            self.content, replace(self.preset, role_counts=counts),
            [PlayerConfig(f"p-{i}", f"Player {i}") for i in range(sum(counts.values()))],
            game_id="all-teammates", event_sink=InMemoryEventSink(), rng=Random(31),
        )
        wolves = {p.player_id for p in game.players.values()
                  if p.role.id in {"werewolf", "greedy_werewolf", "wise_werewolf"}}
        foxes = {p.player_id for p in game.players.values() if p.role.id == "fox"}
        for player in game.players.values():
            if player.role.id in {"werewolf", "greedy_werewolf", "wise_werewolf",
                                  "fanatic", "whispering_madman"}:
                expected = wolves - {player.player_id}
            elif player.role.id == "fox":
                expected = foxes - {player.player_id}
            else:
                expected = set()
            with self.subTest(role_id=player.role.id, player_id=player.player_id):
                assignments = [e for e in game.event_bus.events
                               if e.type == "ROLE_ASSIGNED" and e.recipient_player_id == player.player_id]
                self.assertEqual(len(assignments), 1)
                self.assertIs(assignments[0].visibility, EventVisibility.PRIVATE)
                payload = assignments[0].payload
                self.assertEqual(set(payload["teammate_player_ids"]), expected)
                self.assertEqual(set(payload), {"player_id", "role_id", "modifier_ids", "teammate_player_ids"})
                history = [e["payload"]["event_payload"] for e in game.get_state_sync(player.player_id)["history"]
                           if e["payload"].get("event_type") == "ROLE_ASSIGNED"]
                self.assertEqual(history, [dict(payload)])
        public = [dict(e.payload) for e in game.event_bus.events if e.visibility is EventVisibility.PUBLIC]
        self.assertNotIn("teammate_player_ids", json.dumps(public))

    def test_teammates_follow_renamed_yaml_roles_tags_and_team(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "content"
            copytree(CONTENT_ROOT, root)
            for path in (root / "roles").glob("*.yaml"):
                path.write_text(path.read_text(encoding="utf-8")
                                .replace("werewolf", "predator").replace("team: wolf", "team: pack"),
                                encoding="utf-8")
            path = root / "teams.yaml"
            path.write_text(path.read_text(encoding="utf-8")
                            .replace("werewolf", "predator").replace("id: wolf", "id: pack"), encoding="utf-8")
            content = load_content(root)
        counts = {"predator": 2, "fanatic": 1, "villager": 2}
        game = GameState.create_from_preset(
            content, replace(self.preset, role_counts=counts),
            [PlayerConfig(f"p-{i}", str(i)) for i in range(5)],
            game_id="renamed-teammates", event_sink=InMemoryEventSink(), rng=Random(32),
        )
        wolves = {p.player_id for p in game.players.values() if p.role.id == "predator"}
        for event in game.event_bus.events:
            if event.type == "ROLE_ASSIGNED":
                player = game.players[event.recipient_player_id]
                expected = wolves - {player.player_id} if player.role.id != "villager" else set()
                self.assertEqual(set(event.payload["teammate_player_ids"]), expected)

    def test_initial_teammates_exclude_missing_role_and_allow_no_other_members(self) -> None:
        class MissingWolfRandom(Random):
            def choice(self, sequence):
                return "werewolf"

        rules = replace(self.preset.rules, role_missing=replace(self.preset.rules.role_missing, enabled=True))
        counts = dict(self.preset.role_counts)
        counts["fanatic"] = counts.pop("madman")
        game = self.create_game(rng=MissingWolfRandom(33), preset=replace(self.preset, rules=rules, role_counts=counts))
        wolf = next(p.player_id for p in game.players.values() if p.role.id == "werewolf")
        for event in game.event_bus.events:
            if event.type == "ROLE_ASSIGNED":
                role = game.players[event.recipient_player_id].role.id
                self.assertEqual(event.payload["teammate_player_ids"], [wolf] if role == "fanatic" else [])

    def test_direct_game_state_construction_requires_preset_role_ids(self) -> None:
        sink = InMemoryEventSink()
        with self.assertRaisesRegex(TypeError, "preset_role_ids"):
            GameState(
                game_id="missing-preset-roles",
                content=self.content,
                rules=self.preset.rules,
                players={},
                rng=Random(29),
                event_bus=EventBus(),
                event_sink=sink,
            )

    def test_public_log_omits_role_assignment_and_private_log_records_it(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            game = self.create_game(logs_root=root, rng=Random(23))
            self.assertIsInstance(game.event_sink, JsonlEventLog)
            event_log = game.event_sink
            public_entries = _read_jsonl(event_log.paths[EventVisibility.PUBLIC])
            private_entries = _read_jsonl(event_log.paths[EventVisibility.PRIVATE])

            self.assertEqual(
                [entry["type"] for entry in public_entries], ["GAME_CREATED", "PHASE_STARTED"]
            )
            self.assertNotIn("role_id", json.dumps(public_entries))
            self.assertEqual(
                [entry["type"] for entry in private_entries],
                ["ROLE_ASSIGNED"] * len(self.player_configs),
            )
            self.assertTrue(all("role_id" in entry["payload"] for entry in private_entries))
            self.assertTrue(event_log.paths[EventVisibility.AI].exists())

    def test_event_bus_keeps_private_events_off_public_subscriptions(self) -> None:
        bus = EventBus()
        public_events: list[GameEvent] = []
        private_events: list[GameEvent] = []
        bus.subscribe(EventVisibility.PUBLIC, public_events.append)
        bus.subscribe(EventVisibility.PRIVATE, private_events.append)

        bus.publish(
            GameEvent(
                type="ROLE_ASSIGNED",
                visibility=EventVisibility.PRIVATE,
                recipient_player_id="player-1",
                payload={"role_id": "seer"},
            )
        )

        self.assertEqual(public_events, [])
        self.assertEqual([event.type for event in private_events], ["ROLE_ASSIGNED"])

    def test_private_events_require_a_recipient(self) -> None:
        with self.assertRaisesRegex(ValueError, "private events require a recipient"):
            GameEvent(
                type="ROLE_ASSIGNED",
                visibility=EventVisibility.PRIVATE,
                payload={"role_id": "seer"},
            )

    def test_server_events_are_not_delivered_to_client_visibility_subscribers(self) -> None:
        bus = EventBus()
        public_events: list[GameEvent] = []
        private_events: list[GameEvent] = []
        server_events: list[GameEvent] = []
        bus.subscribe(EventVisibility.PUBLIC, public_events.append)
        bus.subscribe(EventVisibility.PRIVATE, private_events.append)
        bus.subscribe(EventVisibility.SERVER, server_events.append)

        bus.publish(
            GameEvent(
                type="ROLE_MISSING_APPLIED",
                visibility=EventVisibility.SERVER,
                payload={"missing_role_id": "seer"},
            )
        )

        self.assertEqual(public_events, [])
        self.assertEqual(private_events, [])
        self.assertEqual([event.type for event in server_events], ["ROLE_MISSING_APPLIED"])

    def test_event_bus_snapshots_nested_payloads(self) -> None:
        bus = EventBus()
        payload = {"players": [{"player_id": "player-1"}]}
        recorded = bus.publish(
            GameEvent(type="GAME_CREATED", visibility=EventVisibility.PUBLIC, payload=payload)
        )
        payload["players"][0]["player_id"] = "changed"

        self.assertEqual(recorded.payload["players"][0]["player_id"], "player-1")
        self.assertEqual(bus.events[0].payload["players"][0]["player_id"], "player-1")

    def test_injected_rng_makes_role_assignments_stable(self) -> None:
        first = self.create_game(rng=Random(29))
        second = self.create_game(rng=Random(29))

        self.assertEqual(
            [(event.type, event.payload) for event in first.event_bus.events],
            [(event.type, event.payload) for event in second.event_bus.events],
        )

    def test_role_missing_records_the_content_configured_replacement(self) -> None:
        missing_preset = replace(
            self.preset,
            rules=replace(
                self.preset.rules,
                role_missing=replace(self.preset.rules.role_missing, enabled=True),
            ),
        )
        game = self.create_game(rng=Random(31), preset=missing_preset)

        missing_event = next(
            event for event in game.event_bus.events if event.type == "ROLE_MISSING_APPLIED"
        )
        self.assertEqual(missing_event.visibility, EventVisibility.SERVER)
        self.assertEqual(missing_event.payload["replacement_role_id"], "villager")
        self.assertNotEqual(missing_event.payload["missing_role_id"], "villager")
        self.assertEqual(
            Counter(player.role.id for player in game.players.values())["villager"],
            self.preset.role_counts["villager"] + 1,
        )

    def test_game_core_does_not_call_the_random_module_directly(self) -> None:
        core_root = PROJECT_ROOT / "server" / "aiwolf_core"
        for source_path in sorted(core_root.rglob("*.py")):
            with self.subTest(module=source_path.relative_to(core_root)):
                self.assertNotIn("random.", source_path.read_text(encoding="utf-8"))

    def test_game_core_does_not_hardcode_content_ids(self) -> None:
        core_root = PROJECT_ROOT / "server" / "aiwolf_core"
        content_ids = (
            set(self.content.roles)
            | {tag for role in self.content.roles.values() for tag in role.tags}
            | set(self.content.teams)
            | set(self.content.chat_channels)
            | set(self.content.death_causes)
        )
        allowed_by_module = {
            "content.py": {
                "graveyard",
                "guard",
                "medium",
                "none",
                "public",
                "sudden_death",
                "village",
                "wolf",
            },
            "events.py": {"public"},
            "models.py": set(self.content.death_causes),
            "state.py": {"ability"},
            "available_actions.py": {"ability"},
            "views.py": {"ability"},  # ActionSpec type vocabulary, not a death cause.
        }
        for source_path in sorted(core_root.rglob("*.py")):
            source = source_path.read_text(encoding="utf-8")
            allowed = allowed_by_module.get(source_path.name, set())
            for content_id in sorted(content_ids - allowed):
                with self.subTest(module=source_path.relative_to(core_root), content_id=content_id):
                    self.assertNotIn(f'"{content_id}"', source)
                    self.assertNotIn(f"'{content_id}'", source)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


if __name__ == "__main__":
    unittest.main()
