from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from random import Random
from shutil import copytree
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from server.aiwolf_core import (
    ActionRejected,
    EventBus,
    EventVisibility,
    GamePhase,
    GameState,
    InMemoryEventSink,
    Player,
    PlayerConfig,
    load_content,
    load_preset,
)
from server.aiwolf_core.available_actions import ActionAvailability


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = PROJECT_ROOT / "content"
PRESET_PATH = CONTENT_ROOT / "presets" / "standard_9.yaml"


class PlayerInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = load_content(CONTENT_ROOT)
        self.preset = load_preset(PRESET_PATH, self.content)

    def make_day_game(
        self,
        roles: dict[str, str],
        *,
        sudden_death: bool = False,
        allow_villager_claim: bool | None = None,
        content=None,
    ) -> GameState:
        content = self.content if content is None else content
        sink = InMemoryEventSink()
        event_bus = EventBus()
        for visibility in EventVisibility:
            event_bus.subscribe(visibility, sink.record)
        rules = replace(
            self.preset.rules,
            day_seconds=10,
            sudden_death=replace(self.preset.rules.sudden_death, enabled=sudden_death),
        )
        if allow_villager_claim is not None:
            rules = replace(
                rules,
                co=replace(rules.co, allow_villager_claim=allow_villager_claim),
            )
        game = GameState(
            game_id="interaction-test",
            content=content,
            rules=rules,
            players={
                player_id: Player(player_id, player_id.title(), content.roles[role_id])
                for player_id, role_id in roles.items()
            },
            rng=Random(7),
            event_bus=event_bus,
            event_sink=sink,
            preset_role_ids=frozenset(roles.values()),
        )
        game.day = 1
        game._enter_phase(GamePhase.DAY, 100)
        return game

    def make_standard_day_game(self, *, preset=None, rng_seed: int = 0) -> GameState:
        preset = self.preset if preset is None else preset
        player_configs = tuple(
            PlayerConfig(f"player-{index}", f"Player {index}")
            for index in range(sum(preset.role_counts.values()))
        )
        game = GameState.create_from_preset(
            self.content,
            preset,
            player_configs,
            game_id="standard-claim-test",
            event_sink=InMemoryEventSink(),
            rng=Random(rng_seed),
            started_at=0,
        )
        game._enter_phase(GamePhase.DAY, 100)
        return game

    def invoke_communication(self, name: str, game: GameState, now: object):
        if name == "chat.send":
            return game.submit_chat(now, "wolf", "public", "I spoke")
        if name == "co.declare":
            return game.declare_co(now, "wolf", "seer", "I am the seer")
        if name == "co.report":
            return game.report_co(now, "wolf", "inspect_result", "target", "not_wolf")
        raise AssertionError(f"unknown test operation: {name}")

    @staticmethod
    def communication_mutations(game: GameState):
        return (
            dict(game.public_activity_counts),
            dict(game.co_declaration_counts),
            tuple((event.type, dict(event.payload)) for event in game.event_bus.events),
            tuple(
                (player_id, game.get_state_sync(player_id)["history"])
                for player_id in game.players
            ),
        )

    def test_communication_boundaries_return_frozen_authorization_evidence(self) -> None:
        for action in ("chat.send", "co.declare", "co.report"):
            with self.subTest(action=action, boundary="before"):
                game = self.make_day_game(
                    {"wolf": "werewolf", "seer": "seer", "target": "villager"}
                )
                result = self.invoke_communication(action, game, 109)
                acceptance = result.acceptance if action == "chat.send" else result
                self.assertEqual(
                    (
                        acceptance.action,
                        acceptance.player_id,
                        acceptance.day,
                        acceptance.phase,
                        acceptance.accepted_at,
                        acceptance.phase_deadline,
                    ),
                    (action, "wolf", 1, "day", 109, 110),
                )
                with self.assertRaises(FrozenInstanceError):
                    acceptance.accepted_at = 108

            for now in (110, 111):
                with self.subTest(action=action, now=now):
                    game = self.make_day_game(
                        {"wolf": "werewolf", "seer": "seer", "target": "villager"}
                    )
                    before = self.communication_mutations(game)
                    with self.assertRaises(ActionRejected) as raised:
                        self.invoke_communication(action, game, now)
                    self.assertEqual(raised.exception.reason, "action_deadline_passed")
                    self.assertEqual(self.communication_mutations(game), before)

    def test_unavailable_communication_never_interprets_a_deadline(self) -> None:
        roles = {"wolf": "werewolf", "seer": "seer", "target": "villager"}
        for phase in (GamePhase.SETUP, GamePhase.DAWN):
            for action in ("chat.send", "co.declare", "co.report"):
                game = self.make_day_game(roles)
                game._enter_phase(phase, 100)
                times = (101,)
                if phase is GamePhase.DAWN:
                    self.assertIsNotNone(game.phase_ends_at)
                    times = (
                        game.phase_ends_at - 1,
                        game.phase_ends_at,
                        game.phase_ends_at + 1,
                    )
                for now in times:
                    with self.subTest(phase=phase.value, action=action, now=now):
                        before = self.communication_mutations(game)
                        with self.assertRaises(ActionRejected) as raised:
                            self.invoke_communication(action, game, now)
                        self.assertEqual(raised.exception.reason, "action_unavailable")
                        self.assertEqual(self.communication_mutations(game), before)

    def test_offered_communication_requires_a_complete_authoritative_window(self) -> None:
        roles = {"wolf": "werewolf", "seer": "seer", "target": "villager"}
        for action in ("chat.send", "co.declare", "co.report"):
            for missing_field in ("phase_started_at", "phase_ends_at"):
                with self.subTest(action=action, missing=missing_field):
                    game = self.make_day_game(roles)
                    setattr(game, missing_field, None)
                    before = self.communication_mutations(game)
                    with self.assertRaisesRegex(
                        RuntimeError, "communication actions require an authoritative deadline"
                    ):
                        self.invoke_communication(action, game, 101)
                    self.assertEqual(self.communication_mutations(game), before)

    def test_offered_communication_validates_receipt_time_without_mutation(self) -> None:
        roles = {"wolf": "werewolf", "seer": "seer", "target": "villager"}
        for action in ("chat.send", "co.declare", "co.report"):
            with self.subTest(action=action, now="before start"):
                game = self.make_day_game(roles)
                before = self.communication_mutations(game)
                with self.assertRaises(ActionRejected) as raised:
                    self.invoke_communication(action, game, 99)
                self.assertEqual(raised.exception.reason, "action_unavailable")
                self.assertEqual(self.communication_mutations(game), before)
            for now in (True, 101.0, "101"):
                with self.subTest(action=action, invalid_now=now):
                    game = self.make_day_game(roles)
                    before = self.communication_mutations(game)
                    with self.assertRaises(TypeError):
                        self.invoke_communication(action, game, now)
                    self.assertEqual(self.communication_mutations(game), before)

    def test_semantic_rejections_precede_communication_deadline_checks(self) -> None:
        cases = (
            ("chat.send", lambda game: game.submit_chat(110, "wolf", "public", ""), "invalid_message"),
            (
                "co.declare",
                lambda game: game.declare_co(110, "wolf", "missing-role", "claim"),
                "unknown_claimed_role",
            ),
            (
                "co.report",
                lambda game: game.report_co(
                    110, "wolf", "inspect_result", "missing-player", "not_wolf"
                ),
                "unknown_target",
            ),
        )
        for action, operation, expected_reason in cases:
            with self.subTest(action=action):
                game = self.make_day_game(
                    {"wolf": "werewolf", "seer": "seer", "target": "villager"}
                )
                before = self.communication_mutations(game)
                with self.assertRaises(ActionRejected) as raised:
                    operation(game)
                self.assertEqual(raised.exception.reason, expected_reason)
                self.assertEqual(self.communication_mutations(game), before)

    def test_receipt_order_uses_the_deadline_snapshot_at_authorization(self) -> None:
        roles = {"wolf": "werewolf", "seer": "seer", "target": "villager"}

        request_first = self.make_day_game(roles)
        accepted_before_tick = request_first.submit_chat(109, "wolf", "public", "before tick")
        self.assertTrue(request_first.advance_if_due(110))
        self.assertEqual(accepted_before_tick.acceptance.phase_deadline, 110)
        self.assertNotEqual(request_first.phase, GamePhase.DAY)

        tick_first = self.make_day_game(roles)
        self.assertTrue(tick_first.advance_if_due(110))
        with self.assertRaisesRegex(ActionRejected, "action_unavailable"):
            tick_first.submit_chat(110, "wolf", "public", "after tick")

        extension_first = self.make_day_game(roles)
        extension_first.rules = replace(
            extension_first.rules,
            extension=replace(extension_first.rules.extension, max_count=1),
        )
        self.assertTrue(
            extension_first.approve_day_extension(105, tuple(extension_first.players))
        )
        accepted_after_extension = extension_first.submit_chat(
            110, "wolf", "public", "after extension"
        )
        self.assertEqual(
            accepted_after_extension.acceptance.phase_deadline,
            extension_first.phase_ends_at,
        )
        self.assertGreater(extension_first.phase_ends_at, 110)

        acceptance_first = self.make_day_game(roles)
        acceptance_first.rules = replace(
            acceptance_first.rules,
            extension=replace(acceptance_first.rules.extension, max_count=1),
            shortening=replace(acceptance_first.rules.shortening, enabled=True),
        )
        accepted_before_extension = acceptance_first.submit_chat(
            104, "wolf", "public", "before extension"
        )
        self.assertTrue(
            acceptance_first.approve_day_extension(105, tuple(acceptance_first.players))
        )
        self.assertEqual(accepted_before_extension.acceptance.phase_deadline, 110)
        self.assertGreater(acceptance_first.phase_ends_at, 110)

        shortening_first = self.make_day_game(roles)
        shortening_first.rules = replace(
            shortening_first.rules,
            shortening=replace(shortening_first.rules.shortening, enabled=True),
        )
        self.assertTrue(
            shortening_first.approve_day_shortening(105, tuple(shortening_first.players))
        )
        with self.assertRaisesRegex(ActionRejected, "action_deadline_passed"):
            shortening_first.submit_chat(105, "wolf", "public", "after shortening")

        acceptance_before_shortening = self.make_day_game(roles)
        acceptance_before_shortening.rules = replace(
            acceptance_before_shortening.rules,
            shortening=replace(acceptance_before_shortening.rules.shortening, enabled=True),
        )
        accepted = acceptance_before_shortening.submit_chat(
            104, "wolf", "public", "before shortening"
        )
        self.assertTrue(
            acceptance_before_shortening.approve_day_shortening(
                105, tuple(acceptance_before_shortening.players)
            )
        )
        self.assertEqual(accepted.acceptance.phase_deadline, 110)
        self.assertEqual(acceptance_before_shortening.phase_ends_at, 105)

    def test_false_co_is_public_but_quota_removes_declaration_action_and_rejects_more(self) -> None:
        game = self.make_day_game({"wolf": "werewolf", "seer": "seer", "villager": "villager"})

        for number in range(1, 4):
            game.declare_co(101, "wolf", "seer", f"claim {number}")
        declarations = [event for event in game.event_bus.events if event.type == "CO_DECLARED"]
        self.assertEqual(len(declarations), 3)
        self.assertEqual(declarations[0].payload["claimed_role_id"], "seer")
        self.assertNotIn(
            "co_declare", [action.type for action in game.get_available_actions("wolf")]
        )
        self.assertIn("co_report", [action.type for action in game.get_available_actions("wolf")])
        with self.assertRaisesRegex(ActionRejected, "co_limit_reached"):
            game.declare_co(101, "wolf", "seer", "one too many")

    def test_dead_players_cannot_co_and_reports_do_not_validate_claim_truth(self) -> None:
        game = self.make_day_game({"wolf": "werewolf", "target": "villager"})

        game.report_co(101, "wolf", "inspect_result", "target", "not_wolf")
        report = next(event for event in game.event_bus.events if event.type == "CO_REPORTED")
        self.assertEqual(report.payload["claimed_result"], "not_wolf")
        game._record_player_death("wolf", "lynched")
        with self.assertRaisesRegex(ActionRejected, "action_unavailable"):
            game.declare_co(101, "wolf", "seer", "too late")

    def test_claimable_roles_are_enumerated_and_enforced_from_content(self) -> None:
        game = self.make_day_game(
            {"wolf": "werewolf", "villager": "villager"}, allow_villager_claim=False
        )
        declaration = next(action for action in game.get_available_actions("wolf") if action.type == "co_declare")
        self.assertNotIn("villager", declaration.claimed_role_ids)
        self.assertIn("werewolf", declaration.claimed_role_ids)
        with self.assertRaisesRegex(ActionRejected, "claim_not_allowed"):
            game.declare_co(101, "wolf", "villager", "I am a villager")

        permitted = self.make_day_game(
            {"wolf": "werewolf", "villager": "villager"}, allow_villager_claim=True
        )
        declaration = next(action for action in permitted.get_available_actions("wolf") if action.type == "co_declare")
        self.assertIn("villager", declaration.claimed_role_ids)
        permitted.declare_co(101, "wolf", "villager", "I am a villager")

    def test_claim_candidates_are_limited_to_the_preset_role_distribution(self) -> None:
        game = self.make_standard_day_game()
        player_id = next(iter(game.players))
        declaration = next(
            action for action in game.get_available_actions(player_id) if action.type == "co_declare"
        )
        expected = tuple(
            role_id
            for role_id in sorted(self.preset.role_counts)
            if self.content.roles[role_id].claimable
        )
        self.assertEqual(declaration.claimed_role_ids, expected)

        missing_role_id = next(
            role_id
            for role_id, role in self.content.roles.items()
            if role_id not in self.preset.role_counts and role.claimable
        )
        with self.assertRaisesRegex(ActionRejected, "claim_not_allowed"):
            game.declare_co(101, player_id, missing_role_id, "not in this game")

        with TemporaryDirectory() as temporary_directory:
            extended_root = Path(temporary_directory) / "content"
            copytree(CONTENT_ROOT, extended_root)
            added_role_path = extended_root / "roles" / "modded_seer.yaml"
            added_role_path.write_text(
                (CONTENT_ROOT / "roles" / "seer.yaml")
                .read_text(encoding="utf-8")
                .replace("id: seer", "id: modded_seer", 1),
                encoding="utf-8",
            )
            game.content = load_content(extended_root)
            unchanged = next(
                action for action in game.get_available_actions(player_id) if action.type == "co_declare"
            )
            self.assertEqual(unchanged.claimed_role_ids, expected)

    def test_role_missing_keeps_the_preset_co_candidates(self) -> None:
        missing_preset = replace(
            self.preset,
            rules=replace(
                self.preset.rules,
                role_missing=replace(self.preset.rules.role_missing, enabled=True),
            ),
        )
        expected = tuple(
            role_id
            for role_id in sorted(missing_preset.role_counts)
            if self.content.roles[role_id].claimable
        )

        for seed in (1, 5, 11):
            with self.subTest(seed=seed):
                game = self.make_standard_day_game(preset=missing_preset, rng_seed=seed)
                player_id = next(iter(game.players))
                declaration = next(
                    action
                    for action in game.get_available_actions(player_id)
                    if action.type == "co_declare"
                )
                missing_event = next(
                    event
                    for event in game.event_bus.events
                    if event.type == "ROLE_MISSING_APPLIED"
                )

                self.assertEqual(declaration.claimed_role_ids, expected)
                self.assertIn(
                    missing_event.payload["missing_role_id"], declaration.claimed_role_ids
                )

    def test_co_receipt_uses_the_claim_candidates_from_available_actions(self) -> None:
        game = self.make_day_game({"wolf": "werewolf", "seer": "seer"})
        original_phase_actions = ActionAvailability.phase_actions

        def without_seer(availability, player, chat_actions):
            return [
                replace(
                    action,
                    claimed_role_ids=tuple(
                        role_id for role_id in action.claimed_role_ids if role_id != "seer"
                    ),
                )
                if action.type == "co_declare"
                else action
                for action in original_phase_actions(availability, player, chat_actions)
            ]

        with patch.object(ActionAvailability, "phase_actions", without_seer):
            with self.assertRaisesRegex(ActionRejected, "claim_not_allowed"):
                game.declare_co(101, "wolf", "seer", "filtered by enumeration")

    def test_renamed_unclaimable_role_needs_no_python_change(self) -> None:
        townie = replace(self.content.roles["villager"], id="townie")
        renamed_content = replace(
            self.content,
            roles={role_id: role for role_id, role in self.content.roles.items() if role_id != "villager"}
            | {"townie": townie},
        )
        game = self.make_day_game(
            {"wolf": "werewolf", "townie": "townie"},
            allow_villager_claim=False,
            content=renamed_content,
        )

        declaration = next(action for action in game.get_available_actions("wolf") if action.type == "co_declare")
        self.assertNotIn("townie", declaration.claimed_role_ids)
        with self.assertRaisesRegex(ActionRejected, "claim_not_allowed"):
            game.declare_co(101, "wolf", "townie", "I am a townie")

    def test_chat_requires_an_enumerated_channel(self) -> None:
        game = self.make_day_game({"wolf": "werewolf", "villager": "villager"})

        with self.assertRaisesRegex(ActionRejected, "action_unavailable"):
            game.submit_chat(101, "wolf", "wolf", "wolves only")
        submission = game.submit_chat(101, "wolf", "public", "I spoke")
        self.assertEqual(submission.channel_id, "public")

    def test_sudden_death_runs_before_vote_and_evaluates_the_win_immediately(self) -> None:
        game = self.make_day_game(
            {"wolf": "werewolf", "speaker": "seer", "silent": "villager"}, sudden_death=True
        )
        game.submit_chat(101, "wolf", "public", "I spoke")
        game.declare_co(101, "speaker", "seer", "I am the seer")

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

    def test_each_public_operation_records_activity_for_sudden_death(self) -> None:
        operations = {
            "chat": lambda game: game.submit_chat(101, "reporter", "public", "I spoke"),
            "co_declare": lambda game: game.declare_co(101, "reporter", "seer", "I am the seer"),
            "co_report": lambda game: game.report_co(
                101, "reporter", "inspect_result", "silent", "not_wolf"
            ),
        }

        for operation_name, operation in operations.items():
            with self.subTest(operation=operation_name):
                game = self.make_day_game(
                    {"reporter": "werewolf", "seer": "seer", "silent": "villager"}, sudden_death=True
                )

                operation(game)

                self.assertEqual(game.public_activity_counts[(1, "reporter")], 1)
                self.assertTrue(game.advance_if_due(110))
                self.assertTrue(game.players["reporter"].alive)
                self.assertFalse(game.players["silent"].alive)

    def test_disabled_sudden_death_leaves_silent_players_alive_and_enters_vote(self) -> None:
        game = self.make_day_game({"wolf": "werewolf", "silent": "villager"})

        self.assertTrue(game.advance_if_due(110))

        self.assertEqual(game.phase, GamePhase.VOTE)
        self.assertTrue(all(player.alive for player in game.players.values()))
        self.assertEqual(game.death_records, {})

if __name__ == "__main__":
    unittest.main()
