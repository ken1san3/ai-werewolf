import copy
import json

import pytest

from ai_client.brain import BrainActionOption
from ai_client.discussion.context import canonical_json_bytes
from ai_client.llm.decision import DecisionValidationError, parse_llm_output
from ai_client.llm.prompt import project_brain_input
from ai_client.llm.types import LLMBrainConfig
from ai_client.network import AbilityAction, CoDeclareAction, VoteAction
from scripts.phase6_context_probe import provider_body, wire_bytes
from scripts import phase6_intent_choice_probe as probe
from tests import test_phase6_semantic_output as semantic_cases
from tests.test_phase6_memory_projection import _chats
from tests.test_phase6_quality_grounding import CONFIG, payload, request_with


KINDS = ("NONE", "CLAIM", "QUESTION", "ANSWER", "REBUTTAL", "OPINION_CHANGE",
         "RELATION_HYPOTHESIS")


def projection(kind="chat", *, peer=False):
    common = dict(connection_generation=1, action_generation=1,
                  phase="opaque-phase", day=1)
    if kind == "vote":
        handle = VoteAction(**common, type="vote", valid_targets=("opaque-peer",),
                            target_count=1, allows_abstain=False)
        request = request_with(trigger="PRE_VOTE", options=(BrainActionOption("action:0", handle),))
    elif kind == "ability":
        handle = AbilityAction(**common, type="ability", ability_id="inspect",
            description=None, valid_targets=("opaque-peer",), target_count=1, uses_remaining=1)
        request = request_with(trigger="ABILITY", options=(BrainActionOption("action:0", handle),))
    elif kind == "co_declare":
        handle = CoDeclareAction(**common, type="co_declare", claimed_role_ids=("claim-a",))
        request = request_with(trigger="CO_OPPORTUNITY", options=(BrainActionOption("action:0", handle),))
    elif peer:
        request = request_with(trigger="PEER_CHAT", chats=_chats(1))
    else:
        request = request_with()
    return project_brain_input(request, config=CONFIG)


def test_choice_schema_is_symmetric_and_preserves_original_order():
    p = projection()
    schema = probe.choice_schema(p.decision_schema)
    assert schema == {"type": "object", "properties": {"speech_act_kind": {
        "type": "string", "enum": list(KINDS)}}, "required": ["speech_act_kind"],
        "additionalProperties": False}


@pytest.mark.parametrize("mutation", ["unknown_keyword", "remote_ref", "missing_branch",
                                       "duplicate_kind", "open_branch", "wrong_fields"])
def test_choice_schema_rejects_unknown_or_changed_legacy_shape(mutation):
    schema = json.loads(canonical_json_bytes(projection().decision_schema))
    branches = schema["$defs"]["speech_act"]["oneOf"]
    if mutation == "unknown_keyword":
        branches[0]["title"] = "changed"
    elif mutation == "remote_ref":
        schema["$defs"]["assessment"]["properties"]["confidence"] = {
            "$ref": "https://example.invalid/schema"}
    elif mutation == "missing_branch":
        branches.pop()
    elif mutation == "duplicate_kind":
        branches[-1]["properties"]["kind"]["const"] = "NONE"
    elif mutation == "open_branch":
        branches[0]["additionalProperties"] = True
    else:
        branches[1]["properties"]["extra"] = {"type": "string"}
        branches[1]["required"].append("extra")
    with pytest.raises(ValueError, match="SCHEMA_SHAPE_CHANGED"):
        probe.choice_schema(schema)


@pytest.mark.parametrize("kind", KINDS)
def test_all_choices_validate(kind):
    p = projection()
    choice = {"speech_act_kind": kind}
    assert probe.validate_choice(json.dumps(choice), p) == choice


@pytest.mark.parametrize("raw,code", [('', "JSON_SYNTAX"), ('[]', "JSON_SCHEMA"),
    ('{"speech_act_kind":"NONE","speech_act_kind":"CLAIM"}', "JSON_DUPLICATE_KEY"),
    ('{"speech_act_kind":NaN}', "JSON_SYNTAX"), ('{"x":1e999}', "JSON_SYNTAX"),
    ('{}', "CHOICE_SCHEMA_INVALID"), ('{"speech_act_kind":"OTHER"}', "CHOICE_SCHEMA_INVALID"),
    ('{"speech_act_kind":"NONE","extra":1}', "CHOICE_SCHEMA_INVALID")])
def test_choice_strict_negative(raw, code):
    with pytest.raises(ValueError, match=code):
        probe.validate_choice(raw, projection())


def test_bodies_preserve_original_messages_grounding_and_body():
    p = projection()
    baseline = provider_body(p)
    frozen = wire_bytes(baseline)
    first = probe.choice_body(baseline)
    second = probe.output_body(baseline, {"speech_act_kind": "CLAIM"}, p)
    assert wire_bytes(baseline) == frozen
    assert first["messages"][:2] == baseline["messages"] == second["messages"][:2]
    assert first["max_tokens"] == 32 and second["max_tokens"] == 480
    assert first["messages"][2]["content"] == probe.CHOICE_INSTRUCTION
    assert second["messages"][2]["content"].endswith('{"speech_act_kind":"CLAIM"}')
    assert p.canonical_input["grounding"] == projection().canonical_input["grounding"]


@pytest.mark.parametrize("kind", KINDS)
def test_locked_schema_changes_only_speech_act_definition(kind):
    p = projection()
    old = json.loads(canonical_json_bytes(p.decision_schema))
    locked = probe.locked_schema(old, {"speech_act_kind": kind})
    expected = copy.deepcopy(old)
    branch = next(item for item in old["$defs"]["speech_act"]["oneOf"]
                  if item["properties"]["kind"]["const"] == kind)
    expected["$defs"]["speech_act"] = branch
    assert locked == expected


def test_static_32_by_7_locked_schema_construction():
    projections = [projection(("chat", "vote", "ability", "co_declare")[i % 4],
                              peer=(i % 5 == 1)) for i in range(32)]
    built = [probe.locked_schema(p.decision_schema, {"speech_act_kind": kind})
             for p in projections for kind in KINDS]
    assert len(built) == 224


def test_all_five_actions_and_triggers_accept_matching_none():
    cases = [(projection(), "none"), (projection(peer=True), "chat"),
             (projection("co_declare"), "co_declare"),
             (projection("vote"), "vote"), (projection("ability"), "ability")]
    for p, action in cases:
        value = payload(p, kind="none" if action == "none" else action)
        if action == "vote":
            value["decision"] = {"kind": "vote", "option_id": "action:0",
                                 "target_player_id": "opaque-peer"}
            value["discussion"].update(decision_kind="vote", option_id="action:0",
                pre_vote_reassessment={"option_id": "action:0",
                    "ranked_target_player_ids": ["opaque-peer"],
                    "preferred_target_player_id": "opaque-peer", "evidence": []})
        elif action == "ability":
            value["decision"] = {"kind": "ability", "option_id": "action:0",
                                 "target_player_ids": ["opaque-peer"]}
            value["discussion"].update(decision_kind="ability", option_id="action:0")
        elif action == "co_declare":
            value["decision"] = {"kind": "co_declare", "option_id": "action:0",
                "claimed_role_id": "claim-a", "comment": "claim"}
            value["discussion"].update(decision_kind="co_declare", option_id="action:0",
                co_judgment={"decision": "DECLARE", "selected_option_id": "action:0",
                             "claimed_role_id": "claim-a"})
        if p.discussion_capture.trigger.kind == "PEER_CHAT":
            value["discussion"]["reaction"] = {"trigger": json.loads(canonical_json_bytes(
                p.discussion_capture.trigger.source)), "score": 60, "reason": "DIRECT_MENTION"}
        raw = json.dumps(value)
        assert probe.validate_final(raw, {"speech_act_kind": "NONE"}, p) == value


_AUTHORITY = ["claim", "question", "answer", "rebuttal", "opinion_change",
    "relation_hypothesis", "assessment_update", "claim_update", "relation_update",
    "strategy_update", "reaction_score", "co_judgment", "pre_vote_reassessment"]


@pytest.mark.parametrize("case", _AUTHORITY)
def test_authority_matrix_matches_legacy_parser(case, monkeypatch):
    original = semantic_cases._parse
    measured = []
    def locked_path(request, value):
        p = project_brain_input(request, config=LLMBrainConfig())
        choice = {"speech_act_kind": value["discussion"]["speech_act"]["kind"]}
        old = parse_llm_output(json.dumps(value), projection=p)
        final = probe.validate_final(json.dumps(value), choice, p)
        assert final == value
        assert parse_llm_output(json.dumps(final), projection=p) == old
        measured.append(True)
        return original(request, value)
    monkeypatch.setattr(semantic_cases, "_parse", locked_path)
    semantic_cases.test_p6b_semantic_pass_authority_closed_positive_matrix(case)
    assert measured


@pytest.mark.parametrize("mutation", ["target", "ref", "visibility", "actor", "update",
                                       "text", "proposal_bound", "different_kind"])
def test_representative_invalid_output_rejected_by_both_paths(mutation):
    request = semantic_cases._request("chat", peer=True)
    p = project_brain_input(request, config=LLMBrainConfig())
    value = semantic_cases._payload(request, "chat")
    if mutation == "target":
        value["decision"]["option_id"] = "missing"
    elif mutation == "ref":
        value["discussion"]["reaction"]["trigger"]["order"] = 999
    elif mutation == "visibility":
        value["discussion"]["reaction"]["trigger"]["visibility"] = "AUTHORIZED_PRIVATE"
    elif mutation == "actor":
        value["discussion"]["speech_act"] = {"kind": "QUESTION",
            "addressee_player_id": "missing", "subject_player_id": None,
            "topic": "VOTE", "source": None}
    elif mutation == "update":
        value["discussion"]["assessment_updates"] = [{"target_player_id": "missing",
            "suspicion": 50, "credibility": 50, "confidence": 50, "evidence": []}]
    elif mutation == "text":
        value["decision"]["message"] = "x" * 201
    elif mutation == "proposal_bound":
        value["discussion"]["assessment_updates"] = [copy.deepcopy({
            "target_player_id": "peer", "suspicion": 50, "credibility": 50,
            "confidence": 50, "evidence": []}) for _ in range(5)]
    else:
        value["discussion"]["speech_act"] = {"kind": "CLAIM",
            "subject_player_id": "peer", "topic": "VOTE", "stance": "OPPOSE", "evidence": []}
    raw = json.dumps(value)
    choice = {"speech_act_kind": "NONE"}
    if mutation == "different_kind":
        assert parse_llm_output(raw, projection=p)
    else:
        with pytest.raises((ValueError, DecisionValidationError)):
            parse_llm_output(raw, projection=p)
    with pytest.raises((ValueError, DecisionValidationError)):
        probe.validate_final(raw, choice, p)


def test_validate_final_passes_original_raw_to_legacy_parser(monkeypatch):
    p = projection()
    value = payload(p, kind="none")
    raw = json.dumps(value, indent=2)
    seen = []
    monkeypatch.setattr(probe, "parse_llm_output",
                        lambda text, *, projection: seen.append(text))
    assert probe.validate_final(raw, {"speech_act_kind": "NONE"}, p) == value
    assert seen == [raw]


def test_representative_authority_negative_suites_match_locked_path(monkeypatch):
    original = semantic_cases.parse_llm_output
    counts = {"accept": 0, "reject": 0}
    def compare(raw, *, projection):
        try:
            value = probe.strict_json(raw)
            kind = value["discussion"]["speech_act"]["kind"]
            choice = {"speech_act_kind": kind}
        except (ValueError, KeyError, TypeError):
            choice = {"speech_act_kind": "NONE"}
        try:
            result = original(raw, projection=projection)
        except DecisionValidationError:
            with pytest.raises((ValueError, DecisionValidationError)):
                probe.validate_final(raw, choice, projection)
            counts["reject"] += 1
            raise
        assert probe.validate_final(raw, choice, projection) == json.loads(raw)
        counts["accept"] += 1
        return result
    monkeypatch.setattr(semantic_cases, "parse_llm_output", compare)
    semantic_cases.test_p6b_visibility_matrix_rejects_upgrade_downgrade_and_public_inference_misuse()
    semantic_cases.test_p6b_peer_actor_addressee_and_claim_speaker_binding_is_mechanical()
    semantic_cases.test_p6b_identity_nullability_option_handle_family_and_base_revision_mutations_fail()
    semantic_cases.test_p6b_opinion_change_requires_exact_prior_and_new_evidence()
    semantic_cases.test_p6b_trigger_specific_reaction_co_and_pre_vote_semantics_are_exact()
    assert counts["accept"] and counts["reject"] >= 10
