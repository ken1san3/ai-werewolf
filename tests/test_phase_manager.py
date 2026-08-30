from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from random import Random
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


class PhaseManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = load_content(CONTENT_ROOT)
        self.preset = load_preset(PRESET_PATH, self.content)
        self.player_configs = tuple(
            PlayerConfig(f"player-{index}", f"Player {index}") for index in range(1, 10)
        )

    def create_game(self, *, content=None, rules=None, started_at: int = 100) -> GameState:
        selected_content = content or self.content
        preset = replace(self.preset, rules=rules or self.preset.rules)
        return GameState.create_from_preset(
            selected_content,
            preset,
            self.player_configs,
            game_id="phase-test",
            rng=Random(41),
            event_sink=InMemoryEventSink(),
            started_at=started_at,
        )

    def test_phase_machine_runs_from_night0_through_a_full_cycle_to_game_end(self) -> None:
        rules = replace(
            self.preset.rules,
            night_seconds=10,
            silence_after_dawn_seconds=3,
            day_seconds=20,
        )
        game = self.create_game(rules=rules)

        self.assertEqual(game.phase, GamePhase.NIGHT0)
        self.assertEqual(game.day, 0)
        self.assertEqual(game.phase_ends_at, 110)
        self.assertFalse(game.advance_if_due(109))

        self.assertEqual(game.advance_phase(110), GamePhase.DAWN)
        self.assertEqual((game.day, game.phase_ends_at), (1, 113))
        self.assertEqual(game.advance_phase(113), GamePhase.DAY)
        self.assertEqual((game.day, game.phase_ends_at), (1, 133))
        self.assertEqual(game.advance_phase(133), GamePhase.VOTE)
        self.assertIsNone(game.phase_ends_at)
        self.assertEqual(game.resolve_votes(133).kind.value, "no_lynch")
        self.assertEqual(game.phase, GamePhase.EXECUTION)
        self.assertEqual(game.advance_phase(133), GamePhase.NIGHT)
        self.assertEqual((game.day, game.phase_ends_at), (1, 143))
        self.assertEqual(game.advance_phase(143, game_ended=True), GamePhase.GAME_END)

        phase_events = [event for event in game.event_bus.events if event.type == "PHASE_STARTED"]
        self.assertEqual(
            [event.payload["phase"] for event in phase_events],
            ["night0", "dawn", "day", "vote", "execution", "night", "game_end"],
        )

    def test_runoff_is_selected_only_when_configured_and_the_vote_ties(self) -> None:
        runoff_rules = replace(
            self.preset.rules,
            night_seconds=1,
            silence_after_dawn_seconds=1,
            day_seconds=1,
            vote=replace(self.preset.rules.vote, runoff=True),
        )
        runoff_game = self.create_game(rules=runoff_rules)
        self._advance_to_vote(runoff_game)
        with self.assertRaisesRegex(ValueError, "requires a phase with a deadline"):
            runoff_game.advance_if_due(103)
        self._submit_tie(runoff_game)
        self.assertEqual(runoff_game.resolve_votes(103).kind.value, "runoff")
        self.assertEqual(runoff_game.phase, GamePhase.RUNOFF)
        self.assertEqual(runoff_game.resolve_votes(103).kind.value, "no_lynch")
        self.assertEqual(runoff_game.phase, GamePhase.EXECUTION)

        no_runoff_rules = replace(runoff_rules, vote=replace(runoff_rules.vote, runoff=False))
        no_runoff_game = self.create_game(rules=no_runoff_rules)
        self._advance_to_vote(no_runoff_game)
        self._submit_tie(no_runoff_game)
        self.assertEqual(no_runoff_game.resolve_votes(103).kind.value, "no_lynch")
        self.assertEqual(no_runoff_game.phase, GamePhase.EXECUTION)

    def test_available_actions_follow_generic_night_number_and_chat_rules(self) -> None:
        rules = replace(
            self.preset.rules,
            first_night_seer="free",
            night_seconds=1,
            silence_after_dawn_seconds=1,
            day_seconds=1,
        )
        game = self.create_game(rules=rules)
        wolf_player = self._player_with_role(game, "werewolf")
        seer_player = self._player_with_role(game, "seer")
        guard_player = self._player_with_role(game, "guard")

        self.assertEqual(
            self._action_tuples(game._phase_action_kinds(wolf_player)),
            (("chat", None, "wolf"),),
        )
        self.assertIn(
            ("ability", "inspect", None),
            self._action_tuples(game._phase_action_kinds(seer_player)),
        )
        self.assertNotIn(
            ("ability", "protect", None),
            self._action_tuples(game._phase_action_kinds(guard_player)),
        )

        self._advance_to_vote(game)
        game.resolve_votes(103)
        self.assertEqual(game.advance_phase(103), GamePhase.NIGHT)
        self.assertIn(
            ("ability", "attack", None),
            self._action_tuples(game._phase_action_kinds(wolf_player)),
        )
        self.assertIn(
            ("ability", "protect", None),
            self._action_tuples(game._phase_action_kinds(guard_player)),
        )
        self.assertFalse(hasattr(game, "get_available_actions"))

    def test_chat_channel_phase_declarations_allow_content_only_channel_renames(self) -> None:
        renamed_timings = {
            ("after_dark" if timing_id == "night_action" else timing_id): replace(
                timing,
                id="after_dark" if timing_id == "night_action" else timing.id,
            )
            for timing_id, timing in self.content.action_timings.items()
        }
        renamed_channels = {
            ("day_chat" if channel_id == "public" else channel_id): replace(
                channel,
                id="day_chat" if channel_id == "public" else channel.id,
            )
            for channel_id, channel in self.content.chat_channels.items()
        }
        renamed_roles = {
            role_id: replace(
                role,
                chat_channels=tuple(
                    "day_chat" if channel_id == "public" else channel_id
                    for channel_id in role.chat_channels
                ),
                abilities=tuple(
                    replace(
                        ability,
                        timing="after_dark" if ability.timing == "night_action" else ability.timing,
                    )
                    for ability in role.abilities
                ),
            )
            for role_id, role in self.content.roles.items()
        }
        renamed_content = replace(
            self.content,
            action_timings=renamed_timings,
            chat_channels=renamed_channels,
            roles=renamed_roles,
        )
        rules = replace(
            self.preset.rules,
            night_seconds=1,
            silence_after_dawn_seconds=1,
            day_seconds=1,
        )
        game = self.create_game(content=renamed_content, rules=rules)
        self._advance_to_day(game)

        player_id = next(iter(game.players))
        self.assertIn(
            ("chat", None, "day_chat"),
            self._action_tuples(game._phase_action_kinds(player_id)),
        )
        game.advance_phase(103)
        game.resolve_votes(103)
        game.advance_phase(103)
        wolf_player = self._player_with_role(game, "werewolf")
        self.assertIn(
            ("ability", "attack", None),
            self._action_tuples(game._phase_action_kinds(wolf_player)),
        )

    def test_day_extension_uses_the_configured_quorum_limit_and_duration(self) -> None:
        majority_rules = replace(
            self.preset.rules,
            night_seconds=10,
            silence_after_dawn_seconds=2,
            day_seconds=100,
            extension=replace(
                self.preset.rules.extension,
                max_count=1,
                seconds_per_extension=20,
                approval="majority",
            ),
        )
        majority_game = self.create_game(rules=majority_rules)
        self._advance_to_day(majority_game)
        player_ids = tuple(majority_game.players)
        self.assertFalse(majority_game.approve_day_extension(150, player_ids[:4]))
        self.assertTrue(majority_game.approve_day_extension(150, player_ids[:5]))
        self.assertEqual((majority_game.phase_ends_at, majority_game.extensions_used), (232, 1))
        self.assertFalse(majority_game.approve_day_extension(150, player_ids[:5]))

        all_rules = replace(
            majority_rules,
            extension=replace(majority_rules.extension, approval="all"),
            shortening=replace(
                majority_rules.shortening,
                enabled=True,
                approval="all",
            ),
        )
        all_game = self.create_game(rules=all_rules)
        self._advance_to_day(all_game)
        all_player_ids = tuple(all_game.players)
        self.assertFalse(all_game.approve_day_extension(150, all_player_ids[:-1]))
        self.assertTrue(all_game.approve_day_extension(150, all_player_ids))
        self.assertFalse(all_game.approve_day_shortening(150, all_player_ids[:-1]))
        self.assertTrue(all_game.approve_day_shortening(150, all_player_ids))
        self.assertEqual(all_game.advance_phase(150), GamePhase.VOTE)

        shortening_rules = replace(
            majority_rules,
            shortening=replace(
                majority_rules.shortening,
                enabled=True,
                approval="majority",
            ),
        )
        shortening_game = self.create_game(rules=shortening_rules)
        self._advance_to_day(shortening_game)
        shortening_player_ids = tuple(shortening_game.players)
        self.assertFalse(shortening_game.approve_day_shortening(150, shortening_player_ids[:4]))
        self.assertTrue(shortening_game.approve_day_shortening(150, shortening_player_ids[:5]))
        self.assertEqual(shortening_game.phase_ends_at, 150)
        self.assertEqual(shortening_game.advance_phase(150), GamePhase.VOTE)

    @staticmethod
    def _player_with_role(game: GameState, role_id: str) -> str:
        return next(player_id for player_id, player in game.players.items() if player.role.id == role_id)

    @staticmethod
    def _action_tuples(actions: object) -> tuple[tuple[str, str | None, str | None], ...]:
        return tuple((action.type, action.ability_id, action.channel) for action in actions)

    @staticmethod
    def _advance_to_day(game: GameState) -> None:
        game.advance_phase(game.phase_ends_at)
        game.advance_phase(game.phase_ends_at)

    @classmethod
    def _advance_to_vote(cls, game: GameState) -> None:
        cls._advance_to_day(game)
        game.advance_phase(game.phase_ends_at)

    @staticmethod
    def _submit_tie(game: GameState) -> None:
        for voter_player_id, target_player_id in (
            ("player-1", "player-3"),
            ("player-2", "player-3"),
            ("player-5", "player-4"),
            ("player-6", "player-4"),
        ):
            game.submit_vote(voter_player_id, target_player_id)


if __name__ == "__main__":
    unittest.main()
