from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest

from server.aiwolf_core import (
    EventBus,
    EventVisibility,
    GameEvent,
    GamePhase,
    GameState,
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
        self, logs_root: Path, *, rng: Random, preset=None, replacement_role_id: str | None = None
    ) -> GameState:
        return GameState.create_from_preset(
            self.content,
            preset or self.preset,
            self.player_configs,
            game_id="test-game",
            logs_root=logs_root,
            rng=rng,
            role_missing_replacement_role_id=replacement_role_id,
        )

    def test_standard_preset_builds_setup_state_and_private_assignments(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT) as temporary_directory:
            game = self.create_game(Path(temporary_directory), rng=Random(17))

            self.assertEqual(game.phase, GamePhase.SETUP)
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
            self.assertEqual([event.sequence for event in game.event_bus.events], list(range(1, 11)))

    def test_public_log_omits_role_assignment_and_private_log_records_it(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT) as temporary_directory:
            root = Path(temporary_directory)
            game = self.create_game(root, rng=Random(23))
            public_entries = _read_jsonl(game.event_log.paths[EventVisibility.PUBLIC])
            private_entries = _read_jsonl(game.event_log.paths[EventVisibility.PRIVATE])

            self.assertEqual([entry["type"] for entry in public_entries], ["GAME_CREATED"])
            self.assertNotIn("role_id", json.dumps(public_entries))
            self.assertEqual(
                [entry["type"] for entry in private_entries],
                ["ROLE_ASSIGNED"] * len(self.player_configs),
            )
            self.assertTrue(all("role_id" in entry["payload"] for entry in private_entries))
            self.assertTrue(game.event_log.paths[EventVisibility.AI].exists())

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

    def test_injected_rng_makes_role_assignments_stable(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT) as first_directory, TemporaryDirectory(
            dir=PROJECT_ROOT
        ) as second_directory:
            first = self.create_game(Path(first_directory), rng=Random(29))
            second = self.create_game(Path(second_directory), rng=Random(29))

            self.assertEqual(
                [(event.type, event.payload) for event in first.event_bus.events],
                [(event.type, event.payload) for event in second.event_bus.events],
            )

    def test_role_missing_records_the_selected_replacement_when_supplied_explicitly(self) -> None:
        missing_preset = replace(self.preset, rules=replace(self.preset.rules, role_missing=True))
        with TemporaryDirectory(dir=PROJECT_ROOT) as temporary_directory:
            game = self.create_game(
                Path(temporary_directory),
                rng=Random(31),
                preset=missing_preset,
                replacement_role_id="villager",
            )

            missing_event = next(
                event for event in game.event_bus.events if event.type == "ROLE_MISSING_APPLIED"
            )
            self.assertEqual(missing_event.visibility, EventVisibility.PRIVATE)
            self.assertEqual(missing_event.payload["replacement_role_id"], "villager")
            self.assertNotEqual(missing_event.payload["missing_role_id"], "villager")
            self.assertEqual(
                Counter(player.role.id for player in game.players.values())["villager"],
                self.preset.role_counts["villager"] + 1,
            )

    def test_role_missing_requires_an_explicit_replacement_until_q25_is_decided(self) -> None:
        missing_preset = replace(self.preset, rules=replace(self.preset.rules, role_missing=True))
        with TemporaryDirectory(dir=PROJECT_ROOT) as temporary_directory:
            root = Path(temporary_directory)
            with self.assertRaisesRegex(ValueError, "requires an explicit replacement role"):
                self.create_game(root, rng=Random(31), preset=missing_preset)
            self.assertFalse((root / "test-game").exists())

    def test_game_core_does_not_call_the_random_module_directly(self) -> None:
        game_source = (PROJECT_ROOT / "server" / "aiwolf_core" / "game.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("random.", game_source)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


if __name__ == "__main__":
    unittest.main()
