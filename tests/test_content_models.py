from __future__ import annotations

import unittest
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch

import yaml

from server.aiwolf_core import (
    AppliedModifier,
    ContentValidationError,
    PlayerRoleState,
    expire_modifiers_at_dawn,
    load_content,
    load_preset,
    resolve_effective_attributes,
    resolve_effective_win_conditions,
)
from server.aiwolf_core.models import (
    AttributeOverrides,
    CORE_DEATH_CAUSE_IDS,
    GamePhase,
    Knowledge,
    Modifier,
    ModifierGrant,
    ModifierWinCondition,
    RulesConfig,
    WinCondition,
)
from server.aiwolf_core.content import (
    _PHASE_IDS,
    _boolean_rule_paths,
    _parse_ability,
    _parse_modifier,
    _parse_passive,
    _parse_role,
    _parse_rules,
    _parse_win_condition,
    _validate_core_death_causes,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = PROJECT_ROOT / "content"
PRESET_PATH = CONTENT_ROOT / "presets" / "standard_9.yaml"


@dataclass(frozen=True)
class NestedBoolRules:
    enabled: bool


@dataclass(frozen=True)
class RulesConfigFixture:
    nested: NestedBoolRules
    name: str


def make_modifier(
    modifier_id: str,
    *,
    overrides: AttributeOverrides | None = None,
    mode: str = "none",
    value: WinCondition | None = None,
    priority: int | None = None,
    duration: str = "permanent",
) -> Modifier:
    return Modifier(
        id=modifier_id,
        name=modifier_id,
        grant=ModifierGrant("in_game", duration),
        win_condition=ModifierWinCondition(mode, value, priority),
        passives=(),
        knowledge=Knowledge({}),
        chat_channels=(),
        overrides=overrides or AttributeOverrides(),
        exclusions=frozenset(),
    )


class ContentLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = load_content(CONTENT_ROOT)

    def test_all_thirteen_roles_are_loaded_from_yaml(self) -> None:
        self.assertEqual(
            set(self.content.roles),
            {
                "villager",
                "seer",
                "medium",
                "guard",
                "baker",
                "nekomata",
                "werewolf",
                "greedy_werewolf",
                "wise_werewolf",
                "madman",
                "fanatic",
                "whispering_madman",
                "fox",
            },
        )
        self.assertEqual(set(self.content.death_causes), {
            "lynched", "attacked", "retaliation", "cursed", "follow_death", "sudden_death", "ability"
        })
        self.assertEqual(set(self.content.death_causes), CORE_DEATH_CAUSE_IDS)

    def test_phase_ids_are_derived_from_the_game_phase_enum(self) -> None:
        self.assertEqual(_PHASE_IDS, frozenset(phase.value for phase in GamePhase))

    def test_content_requires_each_core_death_cause_to_be_registered(self) -> None:
        missing = dict(self.content.death_causes)
        missing.pop("ability")
        with self.assertRaisesRegex(ContentValidationError, "must register every core death cause"):
            _validate_core_death_causes(missing)

    def test_omitted_attributes_are_derived_from_team_content(self) -> None:
        villager = self.content.roles["villager"]
        self.assertEqual(villager.attributes.count_as, "village")
        self.assertEqual(villager.attributes.inspect_result, "not_wolf")
        self.assertEqual(villager.attributes.medium_result, "not_wolf")

    def test_inspection_uses_the_independent_attribute_axis(self) -> None:
        self.assertEqual(self.content.roles["madman"].attributes.inspect_result, "not_wolf")
        self.assertEqual(self.content.roles["werewolf"].attributes.inspect_result, "wolf")

    def test_role_variants_are_data_differences_without_core_branches(self) -> None:
        madman = self.content.roles["madman"]
        fanatic = self.content.roles["fanatic"]
        whispering = self.content.roles["whispering_madman"]
        self.assertEqual(madman.attributes, fanatic.attributes)
        self.assertEqual(madman.attributes, whispering.attributes)
        self.assertNotEqual(madman.knows_teammates, fanatic.knows_teammates)
        self.assertNotEqual(fanatic.chat_channels, whispering.chat_channels)

        wolf = self.content.roles["werewolf"]
        greedy = self.content.roles["greedy_werewolf"]
        wise = self.content.roles["wise_werewolf"]
        self.assertEqual(wolf.attributes, greedy.attributes)
        self.assertEqual(wolf.attributes, wise.attributes)
        self.assertNotEqual(wolf.abilities, greedy.abilities)
        self.assertNotEqual(wolf.abilities, wise.abilities)

    def test_wise_werewolf_declares_inspect_role_after_death_resolution(self) -> None:
        wise_ability = self.content.roles["wise_werewolf"].abilities[0]
        self.assertEqual(
            [(reference.id, reference.priority) for reference in wise_ability.effects],
            [("attack", 50), ("inspect_role", 75)],
        )

    def test_passives_and_medium_declare_effect_priorities(self) -> None:
        nekomata = self.content.roles["nekomata"].passives[0]
        fox = self.content.roles["fox"].passives[0]
        medium = self.content.roles["medium"].abilities[0]

        self.assertEqual([(reference.id, reference.priority) for reference in nekomata.effects], [("kill", 78)])
        self.assertEqual([(reference.id, reference.priority) for reference in fox.effects], [("kill", 40)])
        self.assertEqual([(reference.id, reference.priority) for reference in medium.effects], [("medium_inspect", 45)])

    def test_enabled_when_boolean_paths_are_derived_from_dataclasses(self) -> None:
        self.assertIn("guard.consecutive", _boolean_rule_paths(RulesConfig))
        self.assertIn("shortening.enabled", _boolean_rule_paths(RulesConfig))
        self.assertEqual(_boolean_rule_paths(RulesConfigFixture), frozenset({"nested.enabled"}))

    def test_preset_loads_with_explicit_rules(self) -> None:
        preset = load_preset(PRESET_PATH, self.content)
        self.assertEqual(preset.role_counts["villager"], 3)
        self.assertEqual(preset.rules.win_evaluation_order, ("village", "wolf", "fox"))
        self.assertFalse(preset.rules.guard.consecutive)
        self.assertFalse(preset.rules.role_missing.enabled)
        self.assertEqual(preset.rules.role_missing.replacement_role_id, "villager")
        self.assertFalse(preset.rules.shortening.enabled)
        self.assertEqual(preset.rules.vote_seconds, 60)
        self.assertTrue(preset.rules.vote.abstain.enabled)
        self.assertIsNone(preset.rules.vote.abstain.max_per_player)
        self.assertEqual(preset.rules.vote.reveal, "hidden")

    def test_preset_rejects_a_selected_role_with_unimplemented_runtime_features(self) -> None:
        baker_preset = yaml.safe_load(PRESET_PATH.read_text(encoding="utf-8"))
        baker_preset["roles"] = {"baker": 1}

        with patch("server.aiwolf_core.content._load_yaml", return_value=baker_preset):
            with self.assertRaisesRegex(
                ContentValidationError, "unsupported passive 'public_notify_if_alive'"
            ):
                load_preset("baker_preset.yaml", self.content)

    def test_role_missing_replacement_role_is_selected_from_yaml_rules(self) -> None:
        rules = yaml.safe_load(PRESET_PATH.read_text(encoding="utf-8"))["rules"]
        rules["role_missing"] = {"enabled": True, "replacement_role_id": "baker"}

        parsed = _parse_rules(rules, "test", self.content)

        self.assertTrue(parsed.role_missing.enabled)
        self.assertEqual(parsed.role_missing.replacement_role_id, "baker")

    def test_first_night_seer_is_configured_only_by_the_global_rule(self) -> None:
        preset = load_preset(PRESET_PATH, self.content)
        self.assertEqual(preset.rules.first_night_seer, "random_white")
        self.assertEqual(self.content.roles["seer"].options, {})

    def test_project_declares_the_supported_minimum_python_version(self) -> None:
        metadata = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('requires-python = ">=3.10"', metadata)

    def test_unregistered_effect_and_passive_references_fail_during_load(self) -> None:
        ability = {
            "id": "invalid",
            "timing": "night_action",
            "available_from_night": 1,
            "priority": 50,
            "target": {"selector": "alive_other", "count": 1},
            "uses": {"per_night": 1, "per_game": None},
            "restrictions": [],
            "effects": ["unknown_effect"],
        }
        with self.assertRaisesRegex(ContentValidationError, "unregistered effect"):
            _parse_ability(
                ability,
                "test.ability",
                self.content.effects,
                self.content.action_timings,
                self.content.selectors,
                self.content.restriction_types,
                self.content.death_causes,
            )

        unknown_selector = deepcopy(ability)
        unknown_selector["effects"] = ["inspect"]
        unknown_selector["target"]["selector"] = "unknown_selector"
        with self.assertRaisesRegex(ContentValidationError, "unregistered selector"):
            _parse_ability(
                unknown_selector,
                "test.ability",
                self.content.effects,
                self.content.action_timings,
                self.content.selectors,
                self.content.restriction_types,
                self.content.death_causes,
            )

        unknown_timing = deepcopy(ability)
        unknown_timing["effects"] = ["inspect"]
        unknown_timing["timing"] = "unknown_timing"
        with self.assertRaisesRegex(ContentValidationError, "unregistered action timing"):
            _parse_ability(
                unknown_timing,
                "test.ability",
                self.content.effects,
                self.content.action_timings,
                self.content.selectors,
                self.content.restriction_types,
                self.content.death_causes,
            )

        unknown_restriction = deepcopy(ability)
        unknown_restriction["effects"] = ["inspect"]
        unknown_restriction["restrictions"] = [{"type": "unknown_restriction"}]
        with self.assertRaisesRegex(ContentValidationError, "unregistered restriction type"):
            _parse_ability(
                unknown_restriction,
                "test.ability",
                self.content.effects,
                self.content.action_timings,
                self.content.selectors,
                self.content.restriction_types,
                self.content.death_causes,
            )

        unknown_rule_path = deepcopy(ability)
        unknown_rule_path["effects"] = ["inspect"]
        unknown_rule_path["restrictions"] = [
            {"type": "no_same_target_consecutive", "enabled_when": "rules.gaurd.consecutive == false"}
        ]
        with self.assertRaisesRegex(ContentValidationError, "unknown or non-boolean rule path"):
            _parse_ability(
                unknown_rule_path,
                "test.ability",
                self.content.effects,
                self.content.action_timings,
                self.content.selectors,
                self.content.restriction_types,
                self.content.death_causes,
            )

        passive = {"type": "unknown_passive", "priority": 0, "rules": [{"when": {}}], "effects": []}
        with self.assertRaisesRegex(ContentValidationError, "unregistered passive"):
            _parse_passive(
                passive,
                "test.passive",
                self.content.effects,
                self.content.passives,
                self.content.death_causes,
            )

        unknown_passive_effect = {
            "type": "on_inspected",
            "priority": 40,
            "rules": [{"when": {"event": "inspected"}}],
            "effects": ["unknown_effect"],
        }
        with self.assertRaisesRegex(ContentValidationError, "unregistered effect"):
            _parse_passive(
                unknown_passive_effect,
                "test.passive",
                self.content.effects,
                self.content.passives,
                self.content.death_causes,
            )

        unknown_cause = {
            "type": "retaliate_on_death",
            "priority": 78,
            "rules": [{"when": {"death_cause": "unknown_cause"}}],
            "effects": ["kill"],
        }
        with self.assertRaisesRegex(ContentValidationError, "unknown death cause"):
            _parse_passive(
                unknown_cause,
                "test.passive",
                self.content.effects,
                self.content.passives,
                self.content.death_causes,
            )

        invalid_count_axis = {
            "type": "count_parity",
            "subject": "fox",
            "against": "village",
            "operator": "gte",
        }
        with self.assertRaisesRegex(ContentValidationError, "must be one of"):
            _parse_win_condition(invalid_count_axis, "test.win_condition")

    def test_invalid_rule_values_unknown_keys_and_incomplete_order_fail(self) -> None:
        base = yaml.safe_load(PRESET_PATH.read_text(encoding="utf-8"))["rules"]
        invalid_value = deepcopy(base)
        invalid_value["first_night_seer"] = "invalid"
        invalid_role_missing = deepcopy(base)
        invalid_role_missing["role_missing"]["replacement_role_id"] = "unknown_role"
        unknown_key = deepcopy(base)
        unknown_key["unknown_rule"] = True
        incomplete_order = deepcopy(base)
        incomplete_order["win_evaluation_order"] = ["village", "wolf"]
        for rules in (invalid_value, invalid_role_missing, unknown_key, incomplete_order):
            with self.subTest(rules=rules):
                with self.assertRaises(ContentValidationError):
                    _parse_rules(rules, "test", self.content)

    def test_vote_rules_reject_removed_or_invalid_vote_options(self) -> None:
        base = yaml.safe_load(PRESET_PATH.read_text(encoding="utf-8"))["rules"]
        removed_option = deepcopy(base)
        removed_option["vote"]["skip_lynch_count"] = 1
        invalid_reveal = deepcopy(base)
        invalid_reveal["vote"]["reveal"] = "invalid"
        missing_vote_seconds = deepcopy(base)
        missing_vote_seconds.pop("vote_seconds")
        for rules in (removed_option, invalid_reveal, missing_vote_seconds):
            with self.subTest(rules=rules):
                with self.assertRaises(ContentValidationError):
                    _parse_rules(rules, "test", self.content)

    def test_adding_a_role_yaml_requires_no_python_change(self) -> None:
        role = _parse_role(
            {
                "id": "test_observer",
                "name": "テスト観測者",
                "team": "village",
                "attack_result": "die",
                "tags": [],
                "knows_teammates": False,
                "chat_channels": ["public"],
                "abilities": [],
                "passives": [],
                "options": {},
            },
            "test_observer.yaml",
            self.content.teams,
            self.content.effects,
            self.content.passives,
            self.content.selectors,
            self.content.restriction_types,
            self.content.action_timings,
            self.content.chat_channels,
            self.content.death_causes,
        )
        self.assertEqual(role.id, "test_observer")
        self.assertEqual(role.attributes.inspect_result, "not_wolf")

    def test_role_chat_channels_must_be_registered_content_channels(self) -> None:
        role_data = {
            "id": "invalid_private_channel",
            "name": "無効チャンネル役職",
            "team": "village",
            "attack_result": "die",
            "tags": [],
            "knows_teammates": False,
            "chat_channels": ["private:invalid"],
            "abilities": [],
            "passives": [],
            "options": {},
        }
        with self.assertRaisesRegex(ContentValidationError, "unknown chat channel"):
            _parse_role(
                role_data,
                "invalid_private_channel.yaml",
                self.content.teams,
                self.content.effects,
                self.content.passives,
                self.content.selectors,
                self.content.restriction_types,
                self.content.action_timings,
                self.content.chat_channels,
                self.content.death_causes,
            )

    def test_modifier_schema_parses_without_a_role_specific_branch(self) -> None:
        modifier = _parse_modifier(
            {
                "id": "test_lover",
                "name": "テスト恋人",
                "kind": "modifier",
                "grant": {"timing": "in_game", "duration": "until_next_dawn"},
                "win_condition": {
                    "mode": "override",
                    "value": {"type": "survive_when_others_win", "replaces": True},
                    "priority": 10,
                },
                "passives": [],
                "knowledge": {"knows": "other_holders"},
                "chat_channels": ["lover"],
                "overrides": {"inspect_result": "by_role"},
                "exclusions": [],
            },
            "test_lover.yaml",
            self.content.teams,
            self.content.roles,
            self.content.effects,
            self.content.passives,
            self.content.selectors,
            self.content.restriction_types,
            self.content.chat_channels,
            self.content.death_causes,
        )
        self.assertEqual(modifier.id, "test_lover")
        self.assertEqual(modifier.grant.duration, "until_next_dawn")
        self.assertEqual(modifier.chat_channels, ("lover",))


class ModifierResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = load_content(CONTENT_ROOT)
        self.villager = self.content.roles["villager"]

    def test_no_modifier_matches_the_role_attributes(self) -> None:
        player = PlayerRoleState("alice", self.villager)
        effective = resolve_effective_attributes(player)
        self.assertEqual(effective.team, self.villager.attributes.team)
        self.assertEqual(effective.count_as, self.villager.attributes.count_as)
        self.assertEqual(effective.attack_result, self.villager.attributes.attack_result)
        self.assertEqual(effective.inspect_result, self.villager.attributes.inspect_result)
        self.assertEqual(effective.medium_result, self.villager.attributes.medium_result)

    def test_modifier_overrides_only_its_declared_attribute(self) -> None:
        modifier = make_modifier(
            "test_attack_immunity",
            overrides=AttributeOverrides(attack_result="immune"),
        )
        effective = resolve_effective_attributes(
            PlayerRoleState("alice", self.villager, (AppliedModifier.grant(modifier),))
        )
        self.assertEqual(effective.attack_result, "immune")
        self.assertEqual(effective.team, "village")
        self.assertEqual(effective.count_as, "village")
        self.assertEqual(effective.inspect_result, "not_wolf")

    def test_override_win_condition_replaces_team_win_condition(self) -> None:
        override = WinCondition("survive_when_others_win", {"type": "survive_when_others_win", "replaces": True})
        modifier = make_modifier(
            "test_override",
            mode="override",
            value=override,
            priority=10,
        )
        conditions = resolve_effective_win_conditions(
            PlayerRoleState("alice", self.villager, (AppliedModifier.grant(modifier),)),
            self.content.teams,
        )
        self.assertEqual(conditions, (override,))

    def test_malformed_override_win_condition_raises_without_assertions(self) -> None:
        modifier = make_modifier("invalid_override", mode="override", priority=10)
        player = PlayerRoleState("alice", self.villager, (AppliedModifier.grant(modifier),))
        with self.assertRaisesRegex(ValueError, "missing its value"):
            resolve_effective_win_conditions(player, self.content.teams)

    def test_conflicting_override_priorities_are_rejected_for_one_assignment(self) -> None:
        condition = WinCondition("survive_when_others_win", {"type": "survive_when_others_win", "replaces": True})
        first = make_modifier("first", mode="override", value=condition, priority=5)
        second = make_modifier("second", mode="override", value=condition, priority=5)
        configured_content = replace(self.content, modifiers={"first": first, "second": second})
        with self.assertRaisesRegex(ContentValidationError, "priorities collide"):
            configured_content.validate_modifier_assignment("villager", ["first", "second"])

    def test_conflicting_non_win_overrides_are_rejected_until_precedence_is_defined(self) -> None:
        first = make_modifier("first", overrides=AttributeOverrides(attack_result="immune"))
        second = make_modifier("second", overrides=AttributeOverrides(attack_result="die"))
        player = PlayerRoleState(
            "alice",
            self.villager,
            (AppliedModifier.grant(first), AppliedModifier.grant(second)),
        )
        with self.assertRaisesRegex(ValueError, "same effective attribute"):
            resolve_effective_attributes(player)

    def test_until_next_dawn_modifier_is_removed(self) -> None:
        modifier = make_modifier("short_lived", duration="until_next_dawn")
        player = PlayerRoleState("alice", self.villager, (AppliedModifier.grant(modifier),))
        self.assertEqual(expire_modifiers_at_dawn(player).modifiers, ())


if __name__ == "__main__":
    unittest.main()
