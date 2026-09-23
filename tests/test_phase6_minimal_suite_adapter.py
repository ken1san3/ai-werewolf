from __future__ import annotations

from collections import Counter
from copy import copy, deepcopy
from dataclasses import replace
import json

import pytest

from ai_client.llm.prompt import project_brain_input
from ai_client.llm.types import DiscussionChatConfig, LLMBrainConfig, LLMMessage
from scripts import phase6_minimal_output_probe as probe
from scripts import phase6_minimal_suite_adapter as adapter
from tests.fixtures.phase6_conversation_cases import cases


CONFIG = LLMBrainConfig(short_chat=DiscussionChatConfig())


def suite():
    return tuple(adapter.bind_case(case, project_brain_input(case.request, config=CONFIG), ordinal=index)
                 for index, case in enumerate(cases(), 1))


def changed_projection(projection, canonical):
    changed = copy(projection)
    object.__setattr__(changed, "canonical_input", canonical)
    object.__setattr__(changed, "messages", (
        projection.messages[0], LLMMessage("user", probe.canonical_bytes(canonical).decode())))
    return changed


def test_fixed_32_are_total_ordered_and_all_applicability_unknown():
    rows = suite()
    assert [row.case.case_id for row in rows] == list(probe.CASE_IDS)
    assert [row.public_metadata.ordinal for row in rows] == list(range(1, 33))
    assert set(Counter(row.public_metadata.category for row in rows).values()) == {2}
    assert Counter(row.public_metadata.trigger for row in rows) == {
        "INITIAL_CHAT": 4, "PEER_CHAT": 24, "CO_OPPORTUNITY": 2, "PRE_VOTE": 2,
    }
    assert sum(row.public_metadata.is_fixed_question_case for row in rows) == 18
    assert {row.case.case_id for row in rows if row.public_metadata.is_legal_none_control} == {"G14-1", "G14-2"}
    assert all(row.binding.update_requirement is None for row in rows)
    assert all(row.public_metadata.unresolved_reason == "PRIVATE_UPDATE_REQUIREMENT_UNKNOWN" for row in rows)


def test_suite_case_api_is_read_only_and_public_metadata_is_safe():
    row = suite()[0]
    assert row.case.case_id == row.public_metadata.case_id
    assert row.projection.messages[1].content.encode() == row.binding.canonical_user_bytes
    assert set(row.public_metadata.__dict__) == {
        "case_id", "ordinal", "category", "trigger", "expected_acts", "hard_rule_ids",
        "is_fixed_question_case", "is_legal_none_control", "update_requirement", "unresolved_reason",
    }
    with pytest.raises(TypeError):
        row.schema["properties"]["schema_version"] = {}
    assert "role" not in row.public_metadata.__dict__
    assert "semantic_rule" not in row.public_metadata.__dict__


def test_authority_and_canonical_user_have_distinct_bound_identities():
    row = suite()[0]
    assert row.binding.authority_bytes != row.binding.canonical_user_bytes
    assert probe.sha256(row.binding.authority_bytes) == row.binding.authority_sha256
    assert probe.sha256(row.binding.canonical_user_bytes) == row.binding.canonical_user_sha256
    assert json.loads(row.binding.canonical_user_bytes) == probe.plain(row.projection.canonical_input)
    assert set(probe.plain(row.binding.authority_without_update)) == probe.SUITE_HOST_KEYS


def test_full_request_descriptor_and_allowed_decision_mismatch_fail_closed():
    case = cases()[0]
    projection = project_brain_input(case.request, config=CONFIG)
    canonical = deepcopy(probe.plain(projection.canonical_input))
    canonical["action_context"]["options"][0]["channel"] = "other"
    changed = changed_projection(projection, canonical)
    with pytest.raises(probe.ProbeError, match="BINDING_INVALID"):
        adapter.bind_case(case, changed)
    canonical = deepcopy(probe.plain(projection.canonical_input))
    canonical["grounding"]["allowed_decisions"].pop()
    changed = changed_projection(projection, canonical)
    with pytest.raises(probe.ProbeError, match="BINDING_INVALID"):
        adapter.bind_case(case, changed)


def test_candidate_body_is_exact_three_messages_and_fixed_generation_contract():
    row = suite()[0]
    body = adapter.candidate_body(row, "model.gguf")
    assert len(body["messages"]) == 3
    assert body["messages"][:2] == [{"role": x.role, "content": x.content} for x in row.projection.messages]
    assert body["messages"][2] == {"role": "system", "content": adapter.MINIMAL_V1_INSTRUCTION}
    assert adapter.MINIMAL_V1_INSTRUCTION_SHA256 == "f2c53c05b509d667c1dfe13dab2e4374585f7d000b53c76f0fa517394100aab3"
    assert body["response_format"]["json_schema"]["schema"] == probe.plain(row.schema)
    assert (body["temperature"], body["top_k"], body["top_p"], body["min_p"]) == (0.2, 40, 0.95, 0.05)
    assert (body["seed"], body["max_tokens"], body["cache_prompt"]) == (4242026, 512, False)
    assert adapter.candidate_wire(body) == probe.canonical_bytes(body)


def test_shadow_only_removes_grounding_contract_and_does_not_mutate_body():
    body = adapter.candidate_body(suite()[0], "model.gguf")
    before = adapter.candidate_wire(body)
    shadow = adapter.shadow_without_grounding(body)
    schema = shadow["response_format"]["json_schema"]["schema"]
    assert "grounding" not in schema["properties"]
    assert "grounding" not in schema["required"]
    assert "Grounding mirrors" not in shadow["messages"][-1]["content"]
    assert adapter.candidate_wire(body) == before
    assert set(shadow) == set(body)


def test_suite_schema_has_exact_minimal_keys_and_seven_speech_kinds():
    schema = probe.plain(suite()[0].schema)
    assert set(schema["properties"]) == {
        "schema_version", "decision", "speech_act", "grounding", "utterance", "trigger_detail",
    }
    speech = schema["$defs"]["speech_act"]
    assert len(speech["oneOf"]) == 7


def test_suite_authority_rejects_invented_update_field_and_bad_tristate():
    row = suite()[0]
    authority = probe.plain(row.binding.authority_without_update)
    authority["requires_private_update"] = False
    with pytest.raises(probe.ProbeError, match="BINDING_INVALID"):
        probe.bind_suite(authority, row.binding.canonical_user_bytes, row.binding.private_bytes,
                         update_requirement=None)
    authority.pop("requires_private_update")
    with pytest.raises(probe.ProbeError, match="BINDING_INVALID"):
        probe.bind_suite(authority, row.binding.canonical_user_bytes, row.binding.private_bytes,
                         update_requirement=0)


@pytest.mark.parametrize("index,field,bad", [
    (0, "channel", "private"),
    (24, "valid_targets", ["player-2", "player-4"]),
    (28, "claimed_role_ids", ["villager"]),
])
def test_chat_vote_and_co_full_descriptors_must_match_request(index, field, bad):
    case = cases()[index]
    projection = project_brain_input(case.request, config=CONFIG)
    canonical = deepcopy(probe.plain(projection.canonical_input))
    canonical["action_context"]["options"][0][field] = bad
    with pytest.raises(probe.ProbeError, match="BINDING_INVALID"):
        adapter.bind_case(case, changed_projection(projection, canonical))


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_full_descriptor_rejects_missing_and_extra_fields(change):
    case = cases()[0]
    projection = project_brain_input(case.request, config=CONFIG)
    canonical = deepcopy(probe.plain(projection.canonical_input))
    option = canonical["action_context"]["options"][0]
    if change == "missing": option.pop("channel")
    else: option["invented"] = "value"
    with pytest.raises(probe.ProbeError, match="BINDING_INVALID"):
        adapter.bind_case(case, changed_projection(projection, canonical))


@pytest.mark.parametrize("change", ["unknown-player", "unknown-actor", "bad-ref"])
def test_player_actor_and_reference_authority_fail_closed(change):
    case = cases()[0]
    projection = project_brain_input(case.request, config=CONFIG)
    canonical = deepcopy(probe.plain(projection.canonical_input))
    if change == "unknown-player":
        canonical["grounding"]["current"]["alive_player_ids"][0] = "unknown-player"
    elif change == "unknown-actor":
        canonical["memory"]["records"][0]["actor_player_ids"] = ["unknown-player"]
    else:
        canonical["memory"]["records"][0]["source"]["order"] = 999
    with pytest.raises(probe.ProbeError, match="BINDING_INVALID"):
        adapter.bind_case(case, changed_projection(projection, canonical))


def test_canonical_user_message_bytes_are_not_repaired():
    case = cases()[0]
    projection = copy(project_brain_input(case.request, config=CONFIG))
    messages = list(projection.messages)
    messages[1] = LLMMessage("user", messages[1].content + " ")
    object.__setattr__(projection, "messages", tuple(messages))
    with pytest.raises(probe.ProbeError, match="BINDING_INVALID"):
        adapter.bind_case(case, projection)


@pytest.mark.parametrize("change", ["trigger", "base_revision", "context_sha256", "player_id"])
def test_canonical_capture_and_actor_must_match_projection_sidecar(change):
    case = cases()[0]
    projection = project_brain_input(case.request, config=CONFIG)
    canonical = deepcopy(probe.plain(projection.canonical_input))
    if change == "trigger": canonical["capture"]["trigger"]["mapping_order"] += 1
    elif change == "base_revision": canonical["capture"]["base_revision"] += 1
    elif change == "context_sha256": canonical["capture"]["context_sha256"] = "0" * 64
    else: canonical["context"]["player_id"] = "player-2"
    with pytest.raises(probe.ProbeError, match="BINDING_INVALID"):
        adapter.bind_case(case, changed_projection(projection, canonical))


def test_short_chat_limits_are_bound_instead_of_hardcoded_silently():
    case = cases()[0]
    projection = project_brain_input(case.request, config=CONFIG)
    changed = replace(projection, short_chat=DiscussionChatConfig(max_text_chars=201))
    with pytest.raises(probe.ProbeError, match="BINDING_INVALID"):
        adapter.bind_case(case, changed)


def test_authority_players_include_dead_players_for_historical_evidence():
    row = suite()[12]  # G07-1 contains a dead player and historical public records.
    assert "player-5" in row.binding.authority_without_update["current_player_ids"]
    assert "player-5" not in row.projection.canonical_input["grounding"]["current"]["alive_player_ids"]


def test_binding_does_not_stage_commit_or_observe_state(monkeypatch):
    rows = [(case, project_brain_input(case.request, config=CONFIG)) for case in cases()]
    def forbidden(*_args, **_kwargs):
        raise AssertionError("state mutation called")
    from ai_client.discussion.state import DiscussionStateStore
    for name in ("stage", "commit", "observe_authoritative"):
        monkeypatch.setattr(DiscussionStateStore, name, forbidden)
    assert len([adapter.bind_case(case, projection) for case, projection in rows]) == 32


def test_schema_constraint_mismatch_is_rejected(monkeypatch):
    case = cases()[24]
    projection = project_brain_input(case.request, config=CONFIG)
    original = probe.output_schema_suite
    def changed(authority):
        schema = original(authority)
        vote = next(branch for branch in schema["properties"]["decision"]["oneOf"]
                    if branch["properties"]["kind"].get("const") == "vote")
        vote["properties"]["target_player_id"]["enum"] = ["player-2"]
        return schema
    monkeypatch.setattr(probe, "output_schema_suite", changed)
    with pytest.raises(probe.ProbeError, match="BINDING_INVALID"):
        adapter.bind_case(case, projection)


@pytest.mark.parametrize("change", ["missing-count", "unknown-target"])
def test_suite_core_rejects_invalid_ability_descriptor(change):
    row = suite()[0]
    authority = probe.plain(row.binding.authority_without_update)
    authority["trigger"] = "ABILITY"
    authority["reaction_source"] = None
    authority["offered_options"] = [{
        "action_kind": "ability", "option_id": "ability:0",
        "valid_targets": ["player-2"], "target_count": 1,
    }]
    if change == "missing-count": authority["offered_options"][0].pop("target_count")
    else: authority["offered_options"][0]["valid_targets"] = ["unknown-player"]
    with pytest.raises(probe.ProbeError, match="BINDING_INVALID"):
        probe.output_schema_suite(authority)
