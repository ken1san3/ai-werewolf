from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Sequence, TypeVar
import unittest

from server.aiwolf_core import (
    GamePhase,
    GameState,
    InMemoryEventSink,
    PlayerConfig,
    load_content,
    load_preset,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = PROJECT_ROOT / "content"
PRESET_PATH = CONTENT_ROOT / "presets" / "standard_9.yaml"

RandomChoice = TypeVar("RandomChoice")


class FirstChoiceRandom:
    """Keep role assignment and content-declared random choices reproducible."""

    def choice(self, sequence: Sequence[RandomChoice]) -> RandomChoice:
        return sequence[0]

    def shuffle(self, sequence: list[str]) -> None:
        return None


class PhaseOneCompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = load_content(CONTENT_ROOT)
        self.standard_preset = load_preset(PRESET_PATH, self.content)

    def test_standard_nine_player_game_completes_with_dummy_operations(self) -> None:
        game = self.create_game("standard-nine", self.standard_preset.role_counts)

        self.assertEqual(
            Counter(player.role.id for player in game.players.values()),
            Counter(self.standard_preset.role_counts),
        )
        self.run_dummy_game(game)
        self.assertEqual(game.game_result.winner_team, "village")

    def test_every_role_can_complete_in_one_content_only_configuration(self) -> None:
        role_counts = {role_id: 1 for role_id in self.content.roles}
        game = self.create_game("all-roles", role_counts)

        self.assertEqual({player.role.id for player in game.players.values()}, set(self.content.roles))
        self.run_dummy_game(game)
        event_types = {event.type for event in game.event_bus.events}
        self.assertTrue(
            {
                "INSPECT_RESULT",
                "MEDIUM_RESULT",
                "GUARD_SUCCEEDED",
                "INSPECT_DEAD_ROLE_RESULT",
            }.issubset(event_types)
        )
        self.assertNotIn("ACTION_NO_SELECTION_RANDOM_TARGETS_SELECTED", event_types)

    def test_fox_nekomata_and_madman_variants_complete(self) -> None:
        cases = {
            "fox": ({"werewolf": 1, "fox": 1, "villager": 1}, "fox"),
            "nekomata": ({"nekomata": 1, "werewolf": 1, "villager": 1}, "village"),
            "madman-variants": (
                {
                    "werewolf": 1,
                    "madman": 1,
                    "fanatic": 1,
                    "whispering_madman": 1,
                    "villager": 1,
                },
                "village",
            ),
        }
        for name, (role_counts, expected_winner) in cases.items():
            with self.subTest(name=name):
                game = self.create_game(name, role_counts)
                if name == "madman-variants":
                    variants = {player.role.id: player.role for player in game.players.values()}
                    self.assertFalse(variants["madman"].knows_teammates)
                    self.assertTrue(variants["fanatic"].knows_teammates)
                    self.assertEqual(variants["fanatic"].chat_channels, ("public",))
                    self.assertEqual(variants["whispering_madman"].chat_channels, ("public", "wolf"))

                self.run_dummy_game(game)
                self.assertEqual(game.game_result.winner_team, expected_winner)

    def create_game(self, game_id: str, role_counts: dict[str, int]) -> GameState:
        player_configs = tuple(
            PlayerConfig(f"player-{index}", f"Player {index}")
            for index in range(1, sum(role_counts.values()) + 1)
        )
        preset = replace(
            self.standard_preset,
            role_counts=role_counts,
            rules=replace(
                self.standard_preset.rules,
                night_seconds=1,
                silence_after_dawn_seconds=1,
                day_seconds=1,
                vote_seconds=1,
            ),
        )
        return GameState.create_from_preset(
            self.content,
            preset,
            player_configs,
            game_id=game_id,
            rng=FirstChoiceRandom(),
            event_sink=InMemoryEventSink(),
            started_at=100,
        )

    def run_dummy_game(self, game: GameState) -> None:
        for _ in range(100):
            if game.phase is GamePhase.GAME_END:
                self.assertIsNotNone(game.game_result)
                self.assertEqual(
                    len([event for event in game.event_bus.events if event.type == "GAME_ENDED"]), 1
                )
                return
            if game.phase in {GamePhase.VOTE, GamePhase.RUNOFF}:
                self.submit_dummy_votes(game)
                if game.phase_ends_at is None:
                    self.fail("vote phase has no deadline")
                game.resolve_votes(game.phase_ends_at)
                continue

            if game.phase in {GamePhase.NIGHT0, GamePhase.NIGHT}:
                self.submit_dummy_night_actions(game)

            now = game.phase_started_at if game.phase is GamePhase.EXECUTION else game.phase_ends_at
            if now is None:
                self.fail(f"phase '{game.phase.value}' has no advance time")
            game.advance_phase(now)
        self.fail("dummy game did not finish within 100 phase transitions")

    def submit_dummy_votes(self, game: GameState) -> None:
        target_player_id = self.dummy_vote_target(game)
        for player_id, player in game.players.items():
            if not player.alive:
                continue
            vote_action = next(
                action for action in game.get_available_actions(player_id) if action.type == "vote"
            )
            selected_target = (
                target_player_id
                if target_player_id in vote_action.valid_targets
                else vote_action.valid_targets[0]
            )
            game.submit_vote(player_id, selected_target)

    def submit_dummy_night_actions(self, game: GameState) -> None:
        """Submit the first selectable ability for every actor's one-action reservation."""

        now = game.phase_started_at
        if now is None:
            self.fail("night phase has no start time")
        for player_id, player in game.players.items():
            if not player.alive:
                continue
            for action in game.get_available_actions(player_id):
                if (
                    action.type != "ability"
                    or action.ability_id is None
                    or action.target_count is None
                    or action.uses_remaining == 0
                    or len(action.valid_targets) < action.target_count
                ):
                    continue
                target_player_ids = self.dummy_ability_targets(
                    game, action.ability_id, action.valid_targets, action.target_count
                )
                game.submit_action(
                    now,
                    player_id,
                    action.ability_id,
                    target_player_ids,
                )
                break

    @staticmethod
    def dummy_ability_targets(
        game: GameState, ability_id: str, valid_targets: tuple[str, ...], target_count: int
    ) -> tuple[str, ...]:
        """Choose legal targets while covering both attack and guard resolution paths."""

        if ability_id == "protect" and game.day == 1:
            return valid_targets[-target_count:]
        return valid_targets[:target_count]

    @staticmethod
    def dummy_vote_target(game: GameState) -> str:
        candidates = (
            game.runoff_candidate_player_ids
            if game.phase is GamePhase.RUNOFF
            else tuple(
                player_id
                for player_id, player in game.players.items()
                if player.alive and "werewolf" in player.role.tags
            )
        )
        if not candidates:
            raise AssertionError("dummy voter has no legal target")
        return candidates[0]


if __name__ == "__main__":
    unittest.main()
