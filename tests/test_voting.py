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
    public_death_cause,
)
from server.aiwolf_core.models import CoreDeathCause


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
                night_action=replace(self.preset.rules.night_action, no_selection="skip"),
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
        result = self._resolve_votes(game)

        self.assertEqual(result.kind, VoteResultKind.LYNCH)
        self.assertEqual(result.lynched_player_id, "player-3")
        self.assertEqual(result.tallies["player-3"], 2)
        self.assertEqual(set(result.tallies), set(game.players))
        self.assertEqual(result.tallies["player-1"], 0)
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

                first_result = self._resolve_votes(game)

                self.assertEqual(first_result.kind, VoteResultKind.RUNOFF)
                self.assertEqual(first_result.runoff_candidate_player_ids, ("player-3", "player-4"))
                self.assertEqual(game.phase, GamePhase.RUNOFF)
                with self.assertRaisesRegex(ValueError, "runoff candidate"):
                    game.submit_vote("player-1", "player-5")

                self._submit_tie(game)
                runoff_result = self._resolve_votes(game)

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

                result = self._resolve_votes(game)

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

    def test_self_vote_rule_rejects_or_accepts_the_voter_as_configured(self) -> None:
        disabled_game = self.create_game(rules=self._rules(self_vote=False))
        with self.assertRaisesRegex(ValueError, "self-voting"):
            disabled_game.submit_vote("player-1", "player-1")

        enabled_game = self.create_game(rules=self._rules(self_vote=True))
        enabled_game.submit_vote("player-1", "player-1")
        enabled_game.submit_vote("player-2", "player-1")
        result = self._resolve_votes(enabled_game)
        self.assertEqual(result.lynched_player_id, "player-1")

    def test_votes_reject_dead_or_unknown_players_and_wrong_phases(self) -> None:
        game = self.create_game()
        with self.assertRaisesRegex(ValueError, "resolve_votes"):
            game.advance_phase(game.phase_ends_at)
        with self.assertRaisesRegex(ValueError, "unknown voter"):
            game.submit_vote("missing", "player-1")
        with self.assertRaisesRegex(ValueError, "unknown vote target"):
            game.submit_vote("player-1", "missing")
        game.submit_vote("player-1", "player-3")
        game.submit_vote("player-2", "player-3")
        self._resolve_votes(game)
        with self.assertRaisesRegex(ValueError, "only be submitted"):
            game.submit_vote("player-1", "player-2")
        with self.assertRaisesRegex(ValueError, "only be resolved"):
            self._resolve_votes(game)

        game.advance_phase(game.phase_started_at)
        game.advance_phase(game.phase_ends_at)
        game.advance_phase(game.phase_ends_at)
        game.advance_phase(game.phase_ends_at)
        with self.assertRaisesRegex(ValueError, "voter 'player-3' must be alive"):
            game.submit_vote("player-3", "player-1")
        with self.assertRaisesRegex(ValueError, "vote target 'player-3' must be alive"):
            game.submit_vote("player-1", "player-3")

    def test_missing_vote_is_no_vote_and_does_not_cancel_the_round(self) -> None:
        game = self.create_game()
        for voter_player_id in tuple(game.players)[:-1]:
            game.submit_vote(voter_player_id, "player-9")

        result = self._resolve_votes(game)

        self.assertEqual(result.kind, VoteResultKind.LYNCH)
        self.assertEqual(result.lynched_player_id, "player-9")

    def test_all_no_votes_use_the_initial_tie_policy_without_runoff(self) -> None:
        rng = FirstChoiceRandom()
        game = self.create_game(
            rules=self._rules(runoff=True, tie_without_runoff="random"), rng=rng
        )

        result = self._resolve_votes(game)

        self.assertEqual(result.kind, VoteResultKind.LYNCH)
        self.assertEqual(result.lynched_player_id, "player-1")
        self.assertEqual(game.phase, GamePhase.EXECUTION)
        self.assertEqual(set(result.tallies.values()), {0})
        self.assertEqual(rng.choice_inputs[-1], tuple(game.players))

    def test_abstention_rules_validate_and_consume_the_explicit_choice(self) -> None:
        disabled_game = self.create_game(
            rules=self._rules(abstain=replace(self.preset.rules.vote.abstain, enabled=False))
        )
        with self.assertRaisesRegex(ValueError, "abstaining is disabled"):
            disabled_game.submit_vote("player-1", None)

        limited_game = self.create_game(
            rules=self._rules(
                abstain=replace(self.preset.rules.vote.abstain, enabled=True, max_per_player=1)
            )
        )
        limited_game.submit_vote("player-1", None)
        self._resolve_votes(limited_game)
        self.assertEqual(limited_game.abstentions_used, {"player-1": 1})
        self._advance_execution_to_vote(limited_game)
        with self.assertRaisesRegex(ValueError, "abstention limit"):
            limited_game.submit_vote("player-1", None)

    def test_vote_reveal_modes_publish_only_the_configured_information(self) -> None:
        hidden_game = self.create_game(rules=self._rules(reveal="hidden"))
        hidden_game.submit_vote("player-1", "player-3")
        self.assertFalse(
            any(event.type == "VOTE_REVEALED_LIVE" for event in hidden_game.event_bus.events)
        )
        self._resolve_votes(hidden_game)
        self.assertFalse(
            any(event.type == "VOTES_REVEALED_AFTER" for event in hidden_game.event_bus.events)
        )

        live_game = self.create_game(rules=self._rules(reveal="live"))
        live_game.submit_vote("player-1", "player-3")
        live_reveal = next(
            event for event in live_game.event_bus.events if event.type == "VOTE_REVEALED_LIVE"
        )
        self.assertEqual(live_reveal.visibility, EventVisibility.PUBLIC)
        self.assertEqual(
            live_reveal.payload,
            {
                "day": 1,
                "phase": "vote",
                "voter_player_id": "player-1",
                "target_player_id": "player-3",
            },
        )

        after_game = self.create_game(rules=self._rules(reveal="after"))
        after_game.submit_vote("player-1", "player-3")
        self.assertFalse(
            any(event.type == "VOTES_REVEALED_AFTER" for event in after_game.event_bus.events)
        )
        self._resolve_votes(after_game)
        after_reveal = next(
            event for event in after_game.event_bus.events if event.type == "VOTES_REVEALED_AFTER"
        )
        self.assertEqual(after_reveal.visibility, EventVisibility.PUBLIC)
        self.assertEqual(
            after_reveal.payload["votes"],
            [{"voter_player_id": "player-1", "target_player_id": "player-3"}],
        )

    def test_public_death_cause_masks_non_lynch_deaths_by_phase(self) -> None:
        self.assertEqual(
            public_death_cause(CoreDeathCause.ATTACKED.value, GamePhase.NIGHT), "died_in_night"
        )
        self.assertEqual(
            public_death_cause(CoreDeathCause.ABILITY.value, GamePhase.DAY), "died_in_day"
        )
        self.assertEqual(
            public_death_cause(CoreDeathCause.LYNCHED.value, GamePhase.EXECUTION), "lynched"
        )

    def _rules(self, **vote_values: object):
        vote = replace(self.preset.rules.vote, **vote_values)
        return replace(
            self.preset.rules,
            night_seconds=1,
            silence_after_dawn_seconds=1,
            day_seconds=10,
            vote_seconds=10,
            vote=vote,
            night_action=replace(self.preset.rules.night_action, no_selection="skip"),
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

    @staticmethod
    def _advance_execution_to_vote(game: GameState) -> None:
        game.advance_phase(game.phase_started_at)
        game.advance_phase(game.phase_ends_at)
        game.advance_phase(game.phase_ends_at)
        game.advance_phase(game.phase_ends_at)

    @staticmethod
    def _resolve_votes(game: GameState):
        return game.resolve_votes(game.phase_ends_at)


if __name__ == "__main__":
    unittest.main()
