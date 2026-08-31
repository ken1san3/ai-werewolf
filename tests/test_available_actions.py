from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from random import Random
import unittest

from server.aiwolf_core import (
    AppliedModifier,
    ActionSpec,
    EventBus,
    EventVisibility,
    GamePhase,
    GameState,
    InMemoryEventSink,
    Player,
    load_content,
    load_preset,
)
from server.aiwolf_core.models import (
    AttributeOverrides,
    Knowledge,
    Modifier,
    ModifierGrant,
    ModifierWinCondition,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = PROJECT_ROOT / "content"
PRESET_PATH = CONTENT_ROOT / "presets" / "standard_9.yaml"


class AvailableActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = load_content(CONTENT_ROOT)
        self.preset = load_preset(PRESET_PATH, self.content)

    def test_co_action_specs_report_invalid_fields_without_claiming_the_type_is_unknown(self) -> None:
        with self.assertRaisesRegex(ValueError, "CO declaration action requires"):
            ActionSpec(type="co_declare")
        with self.assertRaisesRegex(ValueError, "CO report action requires"):
            ActionSpec(type="co_report", channel="public")
        with self.assertRaisesRegex(ValueError, "unsupported action type"):
            ActionSpec(type="unknown")

    def make_game(self, roles: dict[str, str], *, rules=None, phase=GamePhase.NIGHT, content=None) -> GameState:
        content = self.content if content is None else content
        sink = InMemoryEventSink()
        event_bus = EventBus()
        for visibility in EventVisibility:
            event_bus.subscribe(visibility, sink.record)
        game = GameState(
            game_id="available-actions-test",
            content=content,
            rules=rules or replace(self.preset.rules, night_seconds=10),
            players={
                player_id: Player(player_id, player_id, content.roles[role_id])
                for player_id, role_id in roles.items()
            },
            rng=Random(7),
            event_bus=event_bus,
            event_sink=sink,
            preset_role_ids=frozenset(roles.values()),
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
            {"villager": "villager", "other": "seer"}, phase=GamePhase.DAY
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

    def test_modifier_chat_channels_are_enumerated_without_duplicates(self) -> None:
        modifier = Modifier(
            id="test_lover_channel",
            name="test_lover_channel",
            grant=ModifierGrant("in_game", "permanent"),
            win_condition=ModifierWinCondition("none", None, None),
            passives=(),
            knowledge=Knowledge({}),
            chat_channels=("public", "lover"),
            overrides=AttributeOverrides(),
            exclusions=frozenset(),
        )
        game = self.make_game({"villager": "villager", "seer": "seer"})
        game.players["villager"] = replace(
            game.players["villager"], modifiers=(AppliedModifier.grant(modifier),)
        )

        self.assertEqual(
            [(action.type, action.channel) for action in game.get_available_actions("villager")],
            [("chat", "lover")],
        )

        game._enter_phase(GamePhase.DAY, 110)
        self.assertEqual(
            [(action.type, action.channel) for action in game.get_available_actions("villager")],
            [("chat", "public"), ("co_declare", None), ("co_report", None)],
        )

    def test_co_actions_follow_public_chat_action_availability(self) -> None:
        night_public_content = replace(
            self.content,
            chat_channels={
                **self.content.chat_channels,
                "public": replace(self.content.chat_channels["public"], phases=("night",)),
            },
        )

        night_game = self.make_game(
            {"villager": "villager", "seer": "seer"}, content=night_public_content, phase=GamePhase.NIGHT
        )
        self.assertEqual(
            [(action.type, action.channel) for action in night_game.get_available_actions("villager")],
            [("chat", "public"), ("co_declare", None), ("co_report", None)],
        )

        day_game = self.make_game(
            {"villager": "villager", "seer": "seer"}, content=night_public_content, phase=GamePhase.DAY
        )
        self.assertEqual(day_game.get_available_actions("villager"), [])

    def test_co_actions_follow_renamed_content_channel(self) -> None:
        town_square = replace(self.content.chat_channels["public"], id="town_square")
        renamed_content = replace(
            self.content,
            chat_channels={
                channel_id: channel
                for channel_id, channel in self.content.chat_channels.items()
                if channel_id != "public"
            }
            | {"town_square": town_square},
            roles={
                role_id: replace(
                    role,
                    chat_channels=tuple(
                        "town_square" if channel_id == "public" else channel_id
                        for channel_id in role.chat_channels
                    ),
                )
                for role_id, role in self.content.roles.items()
            },
        )
        game = self.make_game(
            {"villager": "villager", "seer": "seer"}, content=renamed_content, phase=GamePhase.DAY
        )

        self.assertEqual(
            [(action.type, action.channel) for action in game.get_available_actions("villager")],
            [("chat", "town_square"), ("co_declare", None), ("co_report", None)],
        )


if __name__ == "__main__":
    unittest.main()
