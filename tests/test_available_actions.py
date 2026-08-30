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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = PROJECT_ROOT / "content"
PRESET_PATH = CONTENT_ROOT / "presets" / "standard_9.yaml"


class AvailableActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = load_content(CONTENT_ROOT)
        self.preset = load_preset(PRESET_PATH, self.content)

    def make_game(self, roles: dict[str, str], *, rules=None, phase=GamePhase.NIGHT) -> GameState:
        sink = InMemoryEventSink()
        event_bus = EventBus()
        for visibility in EventVisibility:
            event_bus.subscribe(visibility, sink.record)
        game = GameState(
            game_id="available-actions-test",
            content=self.content,
            rules=rules or replace(self.preset.rules, night_seconds=10),
            players={
                player_id: Player(player_id, player_id, self.content.roles[role_id])
                for player_id, role_id in roles.items()
            },
            rng=Random(7),
            event_bus=event_bus,
            event_sink=sink,
        )
        game.day = 1
        game._enter_phase(phase, 100)
        return game

    @staticmethod
    def ability(game: GameState, player_id: str, ability_id: str):
        return next(
            action
            for action in game.get_available_actions(player_id)
            if action.type == "ability" and action.ability_id == ability_id
        )

    def test_night0_hides_wolf_attack_and_non_free_first_night_inspection(self) -> None:
        game = self.make_game({"wolf": "werewolf", "seer": "seer"}, phase=GamePhase.NIGHT0)

        self.assertEqual(
            [(action.type, action.ability_id, action.channel) for action in game.get_available_actions("wolf")],
            [("chat", None, "wolf")],
        )
        self.assertFalse(
            any(action.ability_id == "inspect" for action in game.get_available_actions("seer"))
        )

        free_game = self.make_game(
            {"wolf": "werewolf", "seer": "seer"},
            rules=replace(self.preset.rules, first_night_seer="free", night_seconds=10),
            phase=GamePhase.NIGHT0,
        )
        self.assertEqual(self.ability(free_game, "seer", "inspect").valid_targets, ("wolf",))

    def test_guard_target_enumeration_and_submission_share_self_guard_restriction(self) -> None:
        disabled_game = self.make_game({"guard": "guard", "target": "villager"})
        disabled_guard = self.ability(disabled_game, "guard", "protect")
        self.assertEqual(disabled_guard.valid_targets, ("target",))
        with self.assertRaisesRegex(ValueError, "cannot target the actor"):
            disabled_game.submit_action(101, "guard", "protect", ("guard",))

        enabled_game = self.make_game(
            {"guard": "guard", "target": "villager"},
            rules=replace(
                self.preset.rules,
                night_seconds=10,
                guard=replace(self.preset.rules.guard, self_guard=True),
            ),
        )
        enabled_guard = self.ability(enabled_game, "guard", "protect")
        self.assertEqual(enabled_guard.valid_targets, ("guard", "target"))
        enabled_game.submit_action(101, "guard", "protect", ("guard",))

    def test_consecutive_guard_restriction_removes_the_previous_target_from_the_list(self) -> None:
        game = self.make_game({"guard": "guard", "previous": "villager", "other": "villager"})
        game.submit_action(101, "guard", "protect", ("previous",))
        game.resolve_pending_actions(110)
        game._enter_phase(GamePhase.NIGHT, 120)

        self.assertEqual(self.ability(game, "guard", "protect").valid_targets, ("other",))

        enabled_game = self.make_game(
            {"guard": "guard", "previous": "villager", "other": "villager"},
            rules=replace(
                self.preset.rules,
                night_seconds=10,
                guard=replace(self.preset.rules.guard, consecutive=True),
            ),
        )
        enabled_game.submit_action(101, "guard", "protect", ("previous",))
        enabled_game.resolve_pending_actions(110)
        enabled_game._enter_phase(GamePhase.NIGHT, 120)
        self.assertEqual(
            self.ability(enabled_game, "guard", "protect").valid_targets,
            ("previous", "other"),
        )

    def test_exhausted_per_game_ability_reports_zero_remaining_and_stays_rejected(self) -> None:
        game = self.make_game(
            {"greedy": "greedy_werewolf", "first": "villager", "second": "villager", "third": "villager"}
        )
        game.submit_action(101, "greedy", "double_attack", ("first", "second"))
        game.resolve_pending_actions(110)
        game._enter_phase(GamePhase.NIGHT, 120)

        double_attack = self.ability(game, "greedy", "double_attack")
        self.assertEqual(double_attack.uses_remaining, 0)
        with self.assertRaisesRegex(ValueError, "no remaining game uses"):
            game.submit_action(121, "greedy", "double_attack", ("first", "second"))

    def test_day_vote_and_dead_player_actions_are_player_specific(self) -> None:
        game = self.make_game(
            {"villager": "villager", "other": "villager"}, phase=GamePhase.DAY
        )
        day_actions = game.get_available_actions("villager")
        self.assertEqual(
            [(action.type, action.channel) for action in day_actions],
            [("chat", "public"), ("co_declare", None), ("co_report", None)],
        )

        game._enter_phase(GamePhase.VOTE, 110)
        vote = next(action for action in game.get_available_actions("villager") if action.type == "vote")
        self.assertEqual(vote.valid_targets, ("other",))
        self.assertTrue(vote.allows_abstain)

        game._record_player_death("villager", "lynched")
        self.assertEqual(game.get_available_actions("villager"), [])


if __name__ == "__main__":
    unittest.main()
