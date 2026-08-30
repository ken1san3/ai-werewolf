from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Sequence, TypeVar
import unittest

from server.aiwolf_core import (
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


RandomChoice = TypeVar("RandomChoice")


class FirstChoiceRandom:
    def __init__(self) -> None:
        self.choice_inputs: list[tuple[object, ...]] = []

    def choice(self, sequence: Sequence[RandomChoice]) -> RandomChoice:
        choices = tuple(sequence)
        self.choice_inputs.append(choices)
        return choices[0]

    def shuffle(self, sequence: list[str]) -> None:
        return None


class ActionResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = load_content(CONTENT_ROOT)
        self.preset = load_preset(PRESET_PATH, self.content)

    def make_game(self, roles: dict[str, str], *, rules=None) -> GameState:
        sink = InMemoryEventSink()
        game = GameState(
            game_id="action-test",
            content=self.content,
            rules=rules or replace(self.preset.rules, night_seconds=10),
            players={
                player_id: Player(player_id, player_id, self.content.roles[role_id])
                for player_id, role_id in roles.items()
            },
            rng=FirstChoiceRandom(),
            event_bus=self._event_bus(sink),
            event_sink=sink,
        )
        game.day = 1
        game._enter_phase(GamePhase.NIGHT, 100)
        return game

    @staticmethod
    def _event_bus(sink: InMemoryEventSink):
        from server.aiwolf_core import EventBus

        event_bus = EventBus()
        for visibility in EventVisibility:
            event_bus.subscribe(visibility, sink.record)
        return event_bus

    def test_last_action_reservation_replaces_the_prior_target_and_consumes_on_resolution(self) -> None:
        game = self.make_game({"seer": "seer", "first": "villager", "last": "villager"})

        game.submit_action(101, "seer", "inspect", ("first",))
        game.submit_action(102, "seer", "inspect", ("last",))
        self.assertEqual(game.pending_actions["seer"].target_player_ids, ("last",))

        game.resolve_pending_actions(110)

        results = [event for event in game.event_bus.events if event.type == "INSPECT_RESULT"]
        self.assertEqual([event.payload["target_player_id"] for event in results], ["last"])
        self.assertEqual(game.last_resolved_targets[("seer", "inspect")], ("last",))
        self.assertEqual(game.pending_actions, {})
        self.assertEqual(
            len([event for event in game.event_bus.events if event.type == "ACTION_SUBMITTED"]), 2
        )

    def test_action_submission_enforces_night_deadline_availability_and_targets(self) -> None:
        game = self.make_game({"guard": "guard", "wolf": "werewolf", "target": "villager"})
        with self.assertRaisesRegex(ValueError, "exactly 1 target"):
            game.submit_action(101, "guard", "protect", ())
        with self.assertRaisesRegex(ValueError, "cannot target the actor"):
            game.submit_action(101, "guard", "protect", ("guard",))
        with self.assertRaisesRegex(ValueError, "after the deadline"):
            game.submit_action(110, "guard", "protect", ("target",))
        game.resolve_pending_actions(110)
        with self.assertRaisesRegex(ValueError, "already been resolved"):
            game.submit_action(109, "guard", "protect", ("target",))

    def test_inspection_precedes_attack_and_curses_the_fox_even_when_the_seer_dies(self) -> None:
        game = self.make_game({"seer": "seer", "fox": "fox", "wolf": "werewolf"})
        game.submit_action(101, "seer", "inspect", ("fox",))
        game.submit_action(101, "wolf", "attack", ("seer",))

        game.resolve_pending_actions(110)

        inspect = next(event for event in game.event_bus.events if event.type == "INSPECT_RESULT")
        self.assertEqual(inspect.visibility, EventVisibility.PRIVATE)
        self.assertEqual(inspect.payload, {"target_player_id": "fox", "result": "not_wolf"})
        self.assertFalse(game.players["seer"].alive)
        self.assertFalse(game.players["fox"].alive)
        self.assertEqual(game.death_records["seer"].cause, "attacked")
        self.assertEqual(game.death_records["fox"].cause, "cursed")

    def test_guard_blocks_attack_but_not_a_double_attack_on_another_target(self) -> None:
        game = self.make_game(
            {
                "guard": "guard",
                "protected": "villager",
                "other": "villager",
                "greedy": "greedy_werewolf",
            }
        )
        game.submit_action(101, "guard", "protect", ("protected",))
        game.submit_action(101, "greedy", "double_attack", ("protected", "other"))

        game.resolve_pending_actions(110)

        self.assertTrue(game.players["protected"].alive)
        self.assertFalse(game.players["other"].alive)
        guard_events = [event for event in game.event_bus.events if event.type == "GUARD_SUCCEEDED"]
        self.assertEqual([event.recipient_player_id for event in guard_events], ["guard"])
        self.assertEqual(game.ability_uses_per_game[("greedy", "double_attack")], 1)

    def test_guarded_and_fox_attack_have_identical_public_event_sequences(self) -> None:
        guarded = self.make_game(
            {"guard": "guard", "target": "villager", "wolf": "werewolf"}
        )
        guarded.submit_action(101, "guard", "protect", ("target",))
        guarded.submit_action(101, "wolf", "attack", ("target",))
        guarded.resolve_pending_actions(110)

        fox_immune = self.make_game({"target": "fox", "wolf": "werewolf"})
        fox_immune.submit_action(101, "wolf", "attack", ("target",))
        fox_immune.resolve_pending_actions(110)

        def public_events(game: GameState) -> list[tuple[str, dict]]:
            return [
                (event.type, dict(event.payload))
                for event in game.event_bus.events
                if event.visibility is EventVisibility.PUBLIC
            ]

        self.assertEqual(public_events(guarded), public_events(fox_immune))

    def test_guard_consecutive_rule_is_derived_from_the_content_restriction(self) -> None:
        disabled_game = self.make_game({"guard": "guard", "target": "villager"})
        disabled_game.submit_action(101, "guard", "protect", ("target",))
        disabled_game.resolve_pending_actions(110)
        disabled_game._enter_phase(GamePhase.NIGHT, 120)
        with self.assertRaisesRegex(ValueError, "consecutively"):
            disabled_game.submit_action(121, "guard", "protect", ("target",))

        enabled_game = self.make_game(
            {"guard": "guard", "target": "villager"},
            rules=replace(
                self.preset.rules,
                night_seconds=10,
                guard=replace(self.preset.rules.guard, consecutive=True),
            ),
        )
        enabled_game.submit_action(101, "guard", "protect", ("target",))
        enabled_game.resolve_pending_actions(110)
        enabled_game._enter_phase(GamePhase.NIGHT, 120)
        enabled_game.submit_action(121, "guard", "protect", ("target",))

    def test_self_guard_is_derived_from_the_content_restriction(self) -> None:
        disabled_game = self.make_game({"guard": "guard", "target": "villager"})
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
        enabled_game.submit_action(101, "guard", "protect", ("guard",))
        self.assertEqual(enabled_game.pending_actions["guard"].target_player_ids, ("guard",))

    def test_no_selection_random_wolf_attack_uses_every_valid_target_and_records_the_result(self) -> None:
        game = self.make_game({"wolf": "werewolf", "first": "villager", "second": "villager"})

        game.resolve_pending_actions(110)

        selected = next(
            event
            for event in game.event_bus.events
            if event.type == "ACTION_NO_SELECTION_RANDOM_TARGETS_SELECTED"
        )
        self.assertEqual(
            selected.payload,
            {
                "actor_player_id": "wolf",
                "ability_id": "attack",
                "candidate_player_ids": ["first", "second"],
                "selected_player_ids": ["first"],
            },
        )
        self.assertEqual(game.rng.choice_inputs, [(('first',), ('second',))])
        self.assertFalse(game.players["first"].alive)
        self.assertEqual(game.ability_uses_this_night[("wolf", "attack")], 1)

    def test_standard_content_declares_random_no_selection_for_attacks_only(self) -> None:
        random_abilities = {
            (role_id, ability.id)
            for role_id, role in self.content.roles.items()
            for ability in role.abilities
            if ability.no_selection == "random"
        }

        self.assertEqual(
            random_abilities,
            {
                ("werewolf", "attack"),
                ("wise_werewolf", "attack"),
                ("greedy_werewolf", "attack"),
            },
        )

    def test_no_selection_fallback_chooses_only_the_first_eligible_random_ability(self) -> None:
        game = self.make_game(
            {"greedy": "greedy_werewolf", "first": "villager", "second": "villager", "third": "villager"}
        )
        attack, double_attack = game.players["greedy"].role.abilities
        game.players["greedy"] = replace(
            game.players["greedy"],
            role=replace(
                game.players["greedy"].role,
                abilities=(
                    replace(attack, no_selection="random"),
                    replace(double_attack, no_selection="random"),
                ),
            ),
        )

        game.resolve_pending_actions(110)

        selected = [
            event
            for event in game.event_bus.events
            if event.type == "ACTION_NO_SELECTION_RANDOM_TARGETS_SELECTED"
            and event.payload["actor_player_id"] == "greedy"
        ]
        resolved = [
            event
            for event in game.event_bus.events
            if event.type == "ACTION_RESOLVED" and event.payload["actor_player_id"] == "greedy"
        ]
        self.assertEqual([event.payload["ability_id"] for event in selected], ["attack"])
        self.assertEqual([event.payload["ability_id"] for event in resolved], ["attack"])

    def test_no_selection_skip_does_not_resolve_or_consume_an_ability(self) -> None:
        game = self.make_game({"medium": "medium", "dead": "werewolf", "other": "villager"})
        game._record_player_death("dead", "lynched")

        game.resolve_pending_actions(110)

        self.assertFalse(any(event.type == "MEDIUM_RESULT" for event in game.event_bus.events))
        self.assertNotIn(("medium", "medium_inspect"), game.ability_uses_this_night)

    def test_global_no_selection_rule_overrides_ability_declarations(self) -> None:
        random_rules = replace(
            self.preset.rules,
            night_seconds=10,
            night_action=replace(self.preset.rules.night_action, no_selection="random"),
        )
        random_game = self.make_game(
            {"medium": "medium", "dead": "werewolf", "other": "villager"},
            rules=random_rules,
        )
        random_game._record_player_death("dead", "lynched")
        random_game.resolve_pending_actions(110)
        self.assertTrue(any(event.type == "MEDIUM_RESULT" for event in random_game.event_bus.events))

        skip_rules = replace(
            self.preset.rules,
            night_seconds=10,
            night_action=replace(self.preset.rules.night_action, no_selection="skip"),
        )
        skip_game = self.make_game({"seer": "seer", "target": "villager"}, rules=skip_rules)
        skip_game.resolve_pending_actions(110)
        self.assertFalse(any(event.type == "INSPECT_RESULT" for event in skip_game.event_bus.events))
        self.assertNotIn(("seer", "inspect"), skip_game.ability_uses_this_night)

    def test_all_unselected_actions_resolve_before_the_night_phase_advances(self) -> None:
        game = self.make_game(
            {"wolf": "werewolf", "first": "villager", "second": "villager", "third": "villager"}
        )

        self.assertEqual(game.advance_phase(110), GamePhase.DAWN)
        self.assertTrue(game.night_actions_resolved)
        self.assertTrue(
            any(
                event.type == "ACTION_NO_SELECTION_RANDOM_TARGETS_SELECTED"
                for event in game.event_bus.events
            )
        )

    def test_random_wolf_attack_ignores_submitted_targets_and_records_its_selection(self) -> None:
        rules = replace(
            self.preset.rules,
            night_seconds=10,
            wolf_attack=replace(self.preset.rules.wolf_attack, target_decision="random"),
        )
        game = self.make_game(
            {"first_wolf": "werewolf", "second_wolf": "werewolf", "first": "villager", "submitted": "villager"},
            rules=rules,
        )
        game.submit_action(101, "first_wolf", "attack", ("submitted",))
        game.submit_action(101, "second_wolf", "attack", ("submitted",))

        game.resolve_pending_actions(110)

        self.assertFalse(game.players["first"].alive)
        self.assertTrue(game.players["submitted"].alive)
        selected = next(
            event
            for event in game.event_bus.events
            if event.type == "WOLF_ATTACK_TARGET_SELECTED_RANDOM"
        )
        self.assertEqual(
            selected.payload,
            {"candidate_player_ids": ["first", "submitted"], "selected_player_id": "first"},
        )

    def test_living_bakers_emit_one_public_notification_per_dawn_without_identity(self) -> None:
        game = self.make_game(
            {"first_baker": "baker", "second_baker": "baker", "wolf": "werewolf"}
        )

        game._enter_phase(GamePhase.DAWN, 110)
        game._enter_phase(GamePhase.DAWN, 120)

        notifications = [event for event in game.event_bus.events if event.type == "PUBLIC_NOTIFY"]
        self.assertEqual(len(notifications), 2)
        self.assertTrue(all(event.visibility is EventVisibility.PUBLIC for event in notifications))
        self.assertTrue(all(event.payload == {"notify_id": "baker_alive"} for event in notifications))

        game._record_player_death("first_baker", "attacked")
        game._record_player_death("second_baker", "attacked")
        game._enter_phase(GamePhase.DAWN, 130)

        self.assertEqual(len([event for event in game.event_bus.events if event.type == "PUBLIC_NOTIFY"]), 2)

    def test_replacing_a_greedy_double_attack_does_not_consume_its_game_use(self) -> None:
        game = self.make_game({"greedy": "greedy_werewolf", "first": "villager", "last": "villager"})
        game.submit_action(101, "greedy", "double_attack", ("first", "last"))
        game.submit_action(102, "greedy", "attack", ("last",))

        game.resolve_pending_actions(110)

        self.assertNotIn(("greedy", "double_attack"), game.ability_uses_per_game)
        self.assertTrue(game.players["last"].alive is False)
        self.assertTrue(game.players["first"].alive)

    def test_wise_werewolf_learns_the_role_only_after_its_attack_kills(self) -> None:
        game = self.make_game({"wise": "wise_werewolf", "target": "villager"})
        game.submit_action(101, "wise", "attack", ("target",))

        game.resolve_pending_actions(110)

        result = next(
            event for event in game.event_bus.events if event.type == "INSPECT_DEAD_ROLE_RESULT"
        )
        self.assertEqual(result.visibility, EventVisibility.PRIVATE)
        self.assertEqual(result.recipient_player_id, "wise")
        self.assertEqual(result.payload, {"target_player_id": "target", "role_id": "villager"})

    def test_wise_werewolf_follows_the_group_attack_target_not_its_own_vote(self) -> None:
        game = self.make_game(
            {
                "wise": "wise_werewolf",
                "first_wolf": "werewolf",
                "second_wolf": "werewolf",
                "own_vote": "villager",
                "group_target": "villager",
            }
        )
        game.submit_action(101, "wise", "attack", ("own_vote",))
        game.submit_action(101, "first_wolf", "attack", ("group_target",))
        game.submit_action(101, "second_wolf", "attack", ("group_target",))

        game.resolve_pending_actions(110)

        result = next(
            event for event in game.event_bus.events if event.type == "INSPECT_DEAD_ROLE_RESULT"
        )
        self.assertEqual(result.payload["target_player_id"], "group_target")

    def test_medium_inspects_one_eligible_unexamined_death_privately(self) -> None:
        game = self.make_game({"medium": "medium", "dead": "werewolf", "other": "villager"})
        game._record_player_death("dead", "lynched")
        game.submit_action(101, "medium", "medium_inspect", ("dead",))

        game.resolve_pending_actions(110)

        result = next(event for event in game.event_bus.events if event.type == "MEDIUM_RESULT")
        self.assertEqual(result.visibility, EventVisibility.PRIVATE)
        self.assertEqual(result.recipient_player_id, "medium")
        self.assertEqual(result.payload, {"target_player_id": "dead", "result": "wolf"})
        self.assertIn(("medium", "dead"), game.medium_examined_deaths)

    def test_medium_dawn_notification_is_held_until_dawn(self) -> None:
        rules = replace(
            self.preset.rules,
            night_seconds=10,
            night_action=replace(self.preset.rules.night_action, no_selection="skip"),
            medium=replace(self.preset.rules.medium, notify_timing="dawn"),
        )
        game = self.make_game(
            {"medium": "medium", "dead": "werewolf", "wolf": "werewolf", "other": "villager"},
            rules=rules,
        )
        game._record_player_death("dead", "lynched")
        game.submit_action(101, "medium", "medium_inspect", ("dead",))
        game.resolve_pending_actions(110)
        self.assertFalse(any(event.type == "MEDIUM_RESULT" for event in game.event_bus.events))

        game.advance_phase(110)

        result = next(event for event in game.event_bus.events if event.type == "MEDIUM_RESULT")
        self.assertEqual(result.recipient_player_id, "medium")

    def test_nekomata_retaliation_uses_declared_target_pool_and_cause(self) -> None:
        game = self.make_game({"cat": "nekomata", "wolf": "werewolf", "other": "villager"})

        game._record_player_death("cat", "attacked")

        self.assertFalse(game.players["cat"].alive)
        self.assertFalse(game.players["wolf"].alive)
        self.assertTrue(game.players["other"].alive)
        self.assertEqual(game.death_records["wolf"].cause, "retaliation")
        random_event = next(
            event for event in game.event_bus.events if event.type == "PASSIVE_TARGET_SELECTED"
        )
        self.assertEqual(random_event.payload["candidate_player_ids"], ["wolf"])
        self.assertEqual(random_event.payload["selected_player_ids"], ["wolf"])

    def test_night_death_events_mask_internal_causes_from_public_visibility(self) -> None:
        game = self.make_game({"seer": "seer", "fox": "fox", "wolf": "werewolf"})
        game.submit_action(101, "seer", "inspect", ("fox",))
        game.submit_action(101, "wolf", "attack", ("seer",))
        game.resolve_pending_actions(110)

        public_events = [event for event in game.event_bus.events if event.visibility is EventVisibility.PUBLIC]
        public_payload = repr([event.payload for event in public_events])
        self.assertNotIn("attacked", public_payload)
        self.assertNotIn("cursed", public_payload)
        public_deaths = [event for event in public_events if event.type == "PLAYER_DIED"]
        self.assertEqual({event.payload["public_cause"] for event in public_deaths}, {"died_in_night"})

    def test_random_white_first_night_inspection_is_server_selected_and_private(self) -> None:
        rules = replace(self.preset.rules, first_night_seer="random_white", night_seconds=10)
        sink = InMemoryEventSink()
        game = GameState(
            game_id="first-night",
            content=self.content,
            rules=rules,
            players={
                "seer": Player("seer", "seer", self.content.roles["seer"]),
                "white": Player("white", "white", self.content.roles["villager"]),
                "wolf": Player("wolf", "wolf", self.content.roles["werewolf"]),
            },
            rng=FirstChoiceRandom(),
            event_bus=self._event_bus(sink),
            event_sink=sink,
        )
        game.start(100)
        with self.assertRaisesRegex(ValueError, "not player-selected"):
            game.submit_action(101, "seer", "inspect", ("white",))

        game.resolve_pending_actions(110)

        selected = next(
            event
            for event in game.event_bus.events
            if event.type == "FIRST_NIGHT_INSPECT_TARGET_SELECTED"
        )
        self.assertEqual(selected.visibility, EventVisibility.SERVER)
        self.assertEqual(selected.payload["target_player_id"], "white")
        result = next(event for event in game.event_bus.events if event.type == "INSPECT_RESULT")
        self.assertEqual(result.recipient_player_id, "seer")
        self.assertNotIn("cause", result.payload)

    def test_no_first_night_inspection_does_not_create_a_result(self) -> None:
        rules = replace(self.preset.rules, first_night_seer="none", night_seconds=10)
        sink = InMemoryEventSink()
        game = GameState(
            game_id="first-night-none",
            content=self.content,
            rules=rules,
            players={
                "seer": Player("seer", "seer", self.content.roles["seer"]),
                "white": Player("white", "white", self.content.roles["villager"]),
            },
            rng=FirstChoiceRandom(),
            event_bus=self._event_bus(sink),
            event_sink=sink,
        )
        game.start(100)
        game.resolve_pending_actions(110)

        self.assertFalse(any(event.type == "INSPECT_RESULT" for event in game.event_bus.events))


if __name__ == "__main__":
    unittest.main()
