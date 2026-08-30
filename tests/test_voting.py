from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from server.aiwolf_core import (
    EventVisibility,
    GamePhase,
    GameState,
    InMemoryEventSink,
    PlayerConfig,
    VoteResultKind,
    load_content,
    load_preset,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = PROJECT_ROOT / "content"
PRESET_PATH = CONTENT_ROOT / "presets" / "standard_9.yaml"


class FirstChoiceRandom:
    """Records tie choices while keeping role dealing deterministic for tests."""

    def __init__(self) -> None:
        self.choice_inputs: list[tuple[str, ...]] = []

    def choice(self, sequence: tuple[str, ...] | list[str]) -> str:
        selected = tuple(sequence)
        self.choice_inputs.append(selected)
        return selected[0]

    def shuffle(self, sequence: list[str]) -> None:
        return None


class VotingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = load_content(CONTENT_ROOT)
        self.preset = load_preset(PRESET_PATH, self.content)
        self.player_configs = tuple(
            PlayerConfig(f"player-{index}", f"Player {index}") for index in range(1, 10)
        )

    def create_game(self, *, rules=None, rng=None) -> GameState:
        preset = replace(
            self.preset,
            rules=rules
            or replace(
                self.preset.rules,
                night_seconds=1,
                silence_after_dawn_seconds=1,
                day_seconds=10,
            ),
        )
        game = GameState.create_from_preset(
            self.content,
            preset,
            self.player_configs,
            game_id="voting-test",
            rng=rng or FirstChoiceRandom(),
            event_sink=InMemoryEventSink(),
            started_at=100,
        )
        self._advance_to_vote(game)
        return game

    def test_vote_reservation_replaces_the_previous_target_and_lynches(self) -> None:
        game = self.create_game()

        game.submit_vote("player-1", "player-2")
        game.submit_vote("player-1", "player-3")
        game.submit_vote("player-2", "player-3")
        result = game.resolve_votes(112)

        self.assertEqual(result.kind, VoteResultKind.LYNCH)
        self.assertEqual(result.lynched_player_id, "player-3")
        self.assertEqual(dict(result.tallies), {"player-3": 2})
        self.assertEqual(game.phase, GamePhase.EXECUTION)
        self.assertFalse(game.players["player-3"].alive)
        self.assertEqual(game.pending_votes, {})

        submitted = [event for event in game.event_bus.events if event.type == "VOTE_SUBMITTED"]
        self.assertEqual(len(submitted), 3)
        self.assertTrue(all(event.visibility is EventVisibility.SERVER for event in submitted))
        public_death = next(
            event
            for event in game.event_bus.events
            if event.type == "PLAYER_DIED" and event.visibility is EventVisibility.PUBLIC
        )
        self.assertEqual(public_death.payload["public_cause"], "lynched")
        self.assertNotIn("cause", public_death.payload)

    def test_tied_first_vote_enters_runoff_and_uses_each_runoff_tie_policy(self) -> None:
        for tie_rule, expected_kind in (
            ("no_lynch", VoteResultKind.NO_LYNCH),
            ("random", VoteResultKind.LYNCH),
        ):
            with self.subTest(tie_rule=tie_rule):
                game = self.create_game(rules=self._rules(runoff=True, tie_after_runoff=tie_rule))
                self._submit_tie(game)

                first_result = game.resolve_votes(112)

                self.assertEqual(first_result.kind, VoteResultKind.RUNOFF)
                self.assertEqual(first_result.runoff_candidate_player_ids, ("player-3", "player-4"))
                self.assertEqual(game.phase, GamePhase.RUNOFF)
                with self.assertRaisesRegex(ValueError, "runoff candidate"):
                    game.submit_vote("player-1", "player-5")

                self._submit_tie(game)
                runoff_result = game.resolve_votes(112)

                self.assertEqual(runoff_result.kind, expected_kind)
                self.assertEqual(game.phase, GamePhase.EXECUTION)
                if tie_rule == "random":
                    self.assertFalse(game.players["player-3"].alive)
                else:
                    self.assertTrue(all(player.alive for player in game.players.values()))

    def test_tie_without_runoff_uses_each_configured_policy(self) -> None:
        for tie_rule, expected_kind in (
            ("no_lynch", VoteResultKind.NO_LYNCH),
            ("random", VoteResultKind.LYNCH),
        ):
            with self.subTest(tie_rule=tie_rule):
                rng = FirstChoiceRandom()
                game = self.create_game(
                    rules=self._rules(runoff=False, tie_without_runoff=tie_rule), rng=rng
                )
                self._submit_tie(game)

                result = game.resolve_votes(112)

                self.assertEqual(result.kind, expected_kind)
                if tie_rule == "random":
                    self.assertEqual(result.lynched_player_id, "player-3")
                    self.assertFalse(game.players["player-3"].alive)
                    random_event = next(
                        event for event in game.event_bus.events if event.type == "TIE_RESOLVED_RANDOM"
                    )
                    self.assertEqual(random_event.payload["selected_player_id"], "player-3")
                    self.assertEqual(rng.choice_inputs[-1], ("player-3", "player-4"))
                else:
                    self.assertTrue(all(player.alive for player in game.players.values()))

    def test_no_selection_uses_the_configured_invalid_or_skip_policy(self) -> None:
        invalid_game = self.create_game(rules=self._rules(no_selection="invalid_vote"))
        invalid_game.submit_vote("player-1", "player-3")
        invalid_game.submit_vote("player-2", "player-3")
        invalid_result = invalid_game.resolve_votes(112)
        self.assertEqual(invalid_result.kind, VoteResultKind.LYNCH)

        skip_game = self.create_game(rules=self._rules(no_selection="skip_lynch"))
        skip_game.submit_vote("player-1", "player-3")
        skip_game.submit_vote("player-2", "player-3")
        skip_result = skip_game.resolve_votes(112)
        self.assertEqual(skip_result.kind, VoteResultKind.NO_LYNCH)
        resolved_event = next(event for event in skip_game.event_bus.events if event.type == "VOTE_RESOLVED")
        self.assertNotIn("voter_player_id", resolved_event.payload)

    def test_self_vote_rule_rejects_or_accepts_the_voter_as_configured(self) -> None:
        disabled_game = self.create_game(rules=self._rules(self_vote=False))
        with self.assertRaisesRegex(ValueError, "self-voting"):
            disabled_game.submit_vote("player-1", "player-1")

        enabled_game = self.create_game(rules=self._rules(self_vote=True))
        enabled_game.submit_vote("player-1", "player-1")
        enabled_game.submit_vote("player-2", "player-1")
        result = enabled_game.resolve_votes(112)
        self.assertEqual(result.lynched_player_id, "player-1")

    def test_votes_reject_dead_or_unknown_players_and_wrong_phases(self) -> None:
        game = self.create_game()
        with self.assertRaisesRegex(ValueError, "resolve_votes"):
            game.advance_phase(112)
        with self.assertRaisesRegex(ValueError, "unknown voter"):
            game.submit_vote("missing", "player-1")
        with self.assertRaisesRegex(ValueError, "unknown vote target"):
            game.submit_vote("player-1", "missing")
        game.submit_vote("player-1", "player-3")
        game.submit_vote("player-2", "player-3")
        game.resolve_votes(112)
        with self.assertRaisesRegex(ValueError, "only be submitted"):
            game.submit_vote("player-1", "player-2")
        with self.assertRaisesRegex(ValueError, "only be resolved"):
            game.resolve_votes(112)

        game.advance_phase(112)
        game.advance_phase(113)
        game.advance_phase(114)
        game.advance_phase(124)
        with self.assertRaisesRegex(ValueError, "voter 'player-3' must be alive"):
            game.submit_vote("player-3", "player-1")
        with self.assertRaisesRegex(ValueError, "vote target 'player-3' must be alive"):
            game.submit_vote("player-1", "player-3")

    def _rules(self, **vote_values: object):
        vote = replace(self.preset.rules.vote, **vote_values)
        return replace(
            self.preset.rules,
            night_seconds=1,
            silence_after_dawn_seconds=1,
            day_seconds=10,
            vote=vote,
        )

    @staticmethod
    def _submit_tie(game: GameState) -> None:
        for voter_player_id, target_player_id in (
            ("player-1", "player-3"),
            ("player-2", "player-3"),
            ("player-5", "player-4"),
            ("player-6", "player-4"),
        ):
            game.submit_vote(voter_player_id, target_player_id)

    @staticmethod
    def _advance_to_vote(game: GameState) -> None:
        game.advance_phase(game.phase_ends_at)
        game.advance_phase(game.phase_ends_at)
        game.advance_phase(game.phase_ends_at)


if __name__ == "__main__":
    unittest.main()
