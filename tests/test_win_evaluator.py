from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from random import Random
import unittest

from server.aiwolf_core import (
    EventBus,
    EventVisibility,
    GamePhase,
    GameState,
    InMemoryEventSink,
    Player,
    load_content,
    load_preset,
)
from server.aiwolf_core.models import CoreDeathCause, WinCondition
from server.aiwolf_core.wins import WinEvaluator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = PROJECT_ROOT / "content"
PRESET_PATH = CONTENT_ROOT / "presets" / "standard_9.yaml"


class FirstChoiceRandom:
    def choice(self, sequence: list[str]) -> str:
        return sequence[0]

    def shuffle(self, sequence: list[str]) -> None:
        return None


class WinEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = load_content(CONTENT_ROOT)
        self.preset = load_preset(PRESET_PATH, self.content)

    def make_game(self, roles: dict[str, str], *, content=None, rules=None, rng=None) -> GameState:
        sink = InMemoryEventSink()
        event_bus = EventBus()
        for visibility in EventVisibility:
            event_bus.subscribe(visibility, sink.record)
        return GameState(
            game_id="win-test",
            content=content or self.content,
            rules=rules or self.preset.rules,
            players={
                player_id: Player(player_id, player_id, self.content.roles[role_id])
                for player_id, role_id in roles.items()
            },
            rng=rng or Random(4),
            event_bus=event_bus,
            event_sink=sink,
            day=1,
        )

    def test_village_wins_after_all_werewolves_die_even_if_madman_lives(self) -> None:
        game = self.make_game({"villager": "villager", "madman": "madman", "wolf": "werewolf"})
        game.players["wolf"] = replace(game.players["wolf"], alive=False)

        result = WinEvaluator(game).evaluate()

        self.assertEqual(result.winner_team, "village")
        self.assertEqual(result.player_results, {"villager": "won", "madman": "lost", "wolf": "lost"})

    def test_madman_counts_as_village_and_prevents_wolf_parity(self) -> None:
        game = self.make_game({"wolf": "werewolf", "madman": "madman", "villager": "villager"})

        self.assertIsNone(WinEvaluator(game).evaluate())

    def test_wolf_wins_at_count_parity(self) -> None:
        game = self.make_game({"wolf": "werewolf", "villager": "villager"})

        result = WinEvaluator(game).evaluate()

        self.assertEqual((result.winner_team, result.outcome), ("wolf", "team_victory"))
        self.assertEqual(result.player_results, {"wolf": "won", "villager": "lost"})

    def test_execution_evaluates_and_ends_after_the_last_werewolf_is_lynched(self) -> None:
        game = self.make_game({"villager": "villager", "wolf": "werewolf"})
        game._enter_phase(GamePhase.VOTE, 100)
        game.submit_vote("villager", "wolf")

        game.resolve_votes(160)

        self.assertEqual(game.phase, GamePhase.GAME_END)
        self.assertEqual(game.game_result.winner_team, "village")
        ended_events = [event for event in game.event_bus.events if event.type == "GAME_ENDED"]
        self.assertEqual(len(ended_events), 1)
        self.assertEqual(ended_events[0].visibility, EventVisibility.PUBLIC)

    def test_nekomata_execution_chain_can_produce_a_draw_with_all_players_lost(self) -> None:
        game = self.make_game({"cat": "nekomata", "wolf": "werewolf"})
        game._enter_phase(GamePhase.EXECUTION, 100)
        game._record_player_death("cat", CoreDeathCause.LYNCHED.value)

        result = WinEvaluator(game).evaluate()

        self.assertEqual(result.outcome, "draw")
        self.assertIsNone(result.winner_team)
        self.assertEqual(result.player_results, {"cat": "lost", "wolf": "lost"})

    def test_vote_resolution_ends_with_village_victory_after_nekomata_kills_last_wolf(self) -> None:
        game = self.make_game(
            {"cat": "nekomata", "wolf": "werewolf", "villager": "villager"},
            rng=FirstChoiceRandom(),
        )
        game._enter_phase(GamePhase.VOTE, 100)
        game.submit_vote("cat", "wolf")
        game.submit_vote("wolf", "cat")
        game.submit_vote("villager", "cat")

        game.resolve_votes(160)

        self.assertEqual(game.phase, GamePhase.GAME_END)
        self.assertEqual(game.game_result.winner_team, "village")
        self.assertEqual(
            game.game_result.player_results,
            {"cat": "won", "wolf": "lost", "villager": "won"},
        )
        self.assertEqual(len([event for event in game.event_bus.events if event.type == "GAME_ENDED"]), 1)

    def test_draw_precedes_any_other_condition_when_everyone_is_dead(self) -> None:
        game = self.make_game({"wolf": "werewolf", "villager": "villager"})
        for player_id, player in game.players.items():
            game.players[player_id] = replace(player, alive=False)

        result = WinEvaluator(game).evaluate()

        self.assertEqual((result.winner_team, result.outcome), (None, "draw"))
        self.assertTrue(all(value == "lost" for value in result.player_results.values()))

    def test_win_evaluation_order_selects_the_first_matching_team(self) -> None:
        shared_condition = WinCondition(
            "count_parity",
            {"type": "count_parity", "subject": "village", "against": "wolf", "operator": "gte"},
        )
        competing_condition = WinCondition(
            "count_parity",
            {"type": "count_parity", "subject": "wolf", "against": "village", "operator": "gte"},
        )
        teams = dict(self.content.teams)
        teams["village"] = replace(teams["village"], win_conditions=(shared_condition,))
        teams["wolf"] = replace(teams["wolf"], win_conditions=(competing_condition,))
        content = replace(self.content, teams=teams)
        first_village = self.make_game(
            {"wolf": "werewolf", "villager": "villager"},
            content=content,
            rules=replace(self.preset.rules, win_evaluation_order=("village", "wolf", "fox")),
        )
        first_wolf = self.make_game(
            {"wolf": "werewolf", "villager": "villager"},
            content=content,
            rules=replace(self.preset.rules, win_evaluation_order=("wolf", "village", "fox")),
        )

        self.assertEqual(WinEvaluator(first_village).evaluate().winner_team, "village")
        self.assertEqual(WinEvaluator(first_wolf).evaluate().winner_team, "wolf")

    def test_living_fox_replaces_a_wolf_victory(self) -> None:
        game = self.make_game({"wolf": "werewolf", "fox": "fox"})

        result = WinEvaluator(game).evaluate()

        self.assertEqual(result.winner_team, "fox")
        self.assertEqual(result.player_results, {"wolf": "lost", "fox": "won"})

    def test_night_resolution_evaluates_and_ends_once(self) -> None:
        game = self.make_game(
            {"wolf": "werewolf", "villager": "villager"},
            rules=replace(self.preset.rules, night_seconds=10),
        )
        game._enter_phase(GamePhase.NIGHT, 100)
        game.submit_action(101, "wolf", "attack", ("villager",))

        game.advance_phase(110)

        self.assertEqual(game.phase, GamePhase.GAME_END)
        self.assertEqual(game.game_result.winner_team, "wolf")
        self.assertEqual(len([event for event in game.event_bus.events if event.type == "GAME_ENDED"]), 1)


if __name__ == "__main__":
    unittest.main()
