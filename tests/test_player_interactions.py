from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from random import Random
import unittest

from server.aiwolf_core import (
    ActionRejected,
    EventBus,
    EventVisibility,
    GamePhase,
    GameState,
    InMemoryEventSink,
    Player,
    load_content,
    load_preset,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = PROJECT_ROOT / "content"
PRESET_PATH = CONTENT_ROOT / "presets" / "standard_9.yaml"


class PlayerInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = load_content(CONTENT_ROOT)
        self.preset = load_preset(PRESET_PATH, self.content)

    def make_day_game(self, roles: dict[str, str], *, sudden_death: bool = False) -> GameState:
        sink = InMemoryEventSink()
        event_bus = EventBus()
        for visibility in EventVisibility:
            event_bus.subscribe(visibility, sink.record)
        rules = replace(
            self.preset.rules,
            day_seconds=10,
            sudden_death=replace(self.preset.rules.sudden_death, enabled=sudden_death),
        )
        game = GameState(
            game_id="interaction-test",
            content=self.content,
            rules=rules,
            players={
                player_id: Player(player_id, player_id.title(), self.content.roles[role_id])
                for player_id, role_id in roles.items()
            },
            rng=Random(7),
            event_bus=event_bus,
            event_sink=sink,
        )
        game.day = 1
        game._enter_phase(GamePhase.DAY, 100)
        return game

    def test_false_co_is_public_but_quota_removes_declaration_action_and_rejects_more(self) -> None:
        game = self.make_day_game({"wolf": "werewolf", "villager": "villager"})

        for number in range(1, 4):
            game.declare_co("wolf", "seer", f"claim {number}")
        declarations = [event for event in game.event_bus.events if event.type == "CO_DECLARED"]
        self.assertEqual(len(declarations), 3)
        self.assertEqual(declarations[0].payload["claimed_role_id"], "seer")
        self.assertNotIn(
            "co_declare", [action.type for action in game.get_available_actions("wolf")]
        )
        self.assertIn("co_report", [action.type for action in game.get_available_actions("wolf")])
        with self.assertRaisesRegex(ActionRejected, "co_limit_reached"):
            game.declare_co("wolf", "seer", "one too many")

    def test_dead_players_cannot_co_and_reports_do_not_validate_claim_truth(self) -> None:
        game = self.make_day_game({"wolf": "werewolf", "target": "villager"})

        game.report_co("wolf", "inspect_result", "target", "not_wolf")
        report = next(event for event in game.event_bus.events if event.type == "CO_REPORTED")
        self.assertEqual(report.payload["claimed_result"], "not_wolf")
        game._record_player_death("wolf", "lynched")
        with self.assertRaisesRegex(ActionRejected, "action_unavailable"):
            game.declare_co("wolf", "seer", "too late")

    def test_sudden_death_runs_before_vote_and_evaluates_the_win_immediately(self) -> None:
        game = self.make_day_game(
            {"wolf": "werewolf", "speaker": "seer", "silent": "villager"}, sudden_death=True
        )
        game.submit_chat("wolf", "I spoke")
        game.declare_co("speaker", "seer", "I am the seer")

        self.assertTrue(game.advance_if_due(110))

        self.assertEqual(game.phase, GamePhase.GAME_END)
        self.assertEqual(set(game.death_records), {"silent"})
        self.assertEqual(game.death_records["silent"].cause, "sudden_death")
        self.assertIsNotNone(game.game_result)
        self.assertEqual(game.game_result.winner_team, "wolf")
        self.assertNotIn(
            GamePhase.VOTE.value,
            [event.payload["phase"] for event in game.event_bus.events if event.type == "PHASE_STARTED"],
        )

    def test_disabled_sudden_death_leaves_silent_players_alive_and_enters_vote(self) -> None:
        game = self.make_day_game({"wolf": "werewolf", "silent": "villager"})

        self.assertTrue(game.advance_if_due(110))

        self.assertEqual(game.phase, GamePhase.VOTE)
        self.assertTrue(all(player.alive for player in game.players.values()))
        self.assertEqual(game.death_records, {})


if __name__ == "__main__":
    unittest.main()
