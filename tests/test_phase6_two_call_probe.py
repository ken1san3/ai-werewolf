import copy
import json

import pytest

from ai_client.brain import BrainActionOption
from ai_client.discussion.context import canonical_json_bytes
from ai_client.llm.decision import DecisionValidationError, parse_llm_output
from ai_client.llm.prompt import project_brain_input
from ai_client.network import AbilityAction, CoDeclareAction, VoteAction
from scripts.phase6_context_probe import provider_body, wire_bytes
from scripts import phase6_two_call_probe as probe
from tests.test_phase6_memory_projection import _chats, _with_assessment
from tests.test_phase6_quality_grounding import CONFIG, payload, request_with
from tests import test_phase6_semantic_output as semantic_cases


def projection(kind="chat"):
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
    else:
        request = request_with()
    return project_brain_input(request, config=CONFIG)


def legacy(p, kind):
    value = payload(p, kind="none" if kind == "none" else kind)
    if kind == "vote":
        value["decision"] = {"kind": "vote", "option_id": "action:0",
                             "target_player_id": "opaque-peer"}
        value["discussion"].update(decision_kind="vote", option_id="action:0",
            pre_vote_reassessment={"option_id": "action:0",
                "ranked_target_player_ids": ["opaque-peer"],
                "preferred_target_player_id": "opaque-peer", "evidence": []})
    elif kind == "ability":
        value["decision"] = {"kind": "ability", "option_id": "action:0",
                             "target_player_ids": ["opaque-peer"]}
        value["discussion"].update(decision_kind="ability", option_id="action:0")
    elif kind == "co_declare":
        value["decision"] = {"kind": "co_declare", "option_id": "action:0",
            "claimed_role_id": "claim-a", "comment": "I claim this role."}
        value["discussion"].update(decision_kind="co_declare", option_id="action:0",
            co_judgment={"decision": "DECLARE", "selected_option_id": "action:0",
                         "claimed_role_id": "claim-a"})
    return value


@pytest.mark.parametrize("kind", ["none", "chat", "vote", "ability", "co_declare"])
def test_split_join_round_trip_all_five_actions(kind):
    p = projection("chat" if kind == "none" else kind)
    old = legacy(p, kind)
    plan, text = probe.split_legacy(old, p.decision_schema)
    assert probe.validate_plan(json.dumps(plan), p) == plan
    restored = probe.validate_final(plan, None if text is None else json.dumps(text), p)
    assert restored == old
    assert probe.split_legacy(restored, p.decision_schema) == (plan, text)


def test_bodies_preserve_baseline_messages_and_do_not_mutate():
    p = projection()
    baseline = provider_body(p)
    frozen = wire_bytes(baseline)
    first = probe.plan_body(baseline)
    plan, _ = probe.split_legacy(legacy(p, "chat"), p.decision_schema)
    second = probe.message_body(baseline, plan, p)
    assert wire_bytes(baseline) == frozen
    assert first["messages"][:2] == baseline["messages"] == second["messages"][:2]
    assert first["messages"][2]["content"] == probe.PLAN_INSTRUCTION
    assert first["max_tokens"] == 384 and second["max_tokens"] == 128
    assert second["messages"][2]["content"].endswith(
        canonical_json_bytes(plan).decode("utf-8"))


def test_schema_changes_only_decision_text_fields():
    for kind in ("chat", "co_declare"):
        p = projection(kind)
        old = json.loads(canonical_json_bytes(p.decision_schema))
        new = probe.plan_schema(old)
        assert new["$defs"] == old["$defs"]
        assert new["properties"]["discussion"] == old["properties"]["discussion"]
        field = "message" if kind == "chat" else "comment"
        old_branch = next(x for x in old["properties"]["decision"]["oneOf"]
                          if x["properties"]["kind"]["const"] == kind)
        new_branch = next(x for x in new["properties"]["decision"]["oneOf"]
                          if x["properties"]["kind"]["const"] == kind)
        expected = copy.deepcopy(old_branch)
        del expected["properties"][field]
        expected["required"].remove(field)
        assert new_branch == expected


@pytest.mark.parametrize("mutation", ["unknown_keyword", "remote_ref", "missing_required",
                                       "wrong_signature", "duplicate_signature", "root_shape"])
def test_plan_schema_rejects_unknown_or_changed_schema_shape(mutation):
    schema = json.loads(canonical_json_bytes(projection().decision_schema))
    chat = next(branch for branch in schema["properties"]["decision"]["oneOf"]
                if branch["properties"]["kind"]["const"] == "chat")
    if mutation == "unknown_keyword":
        chat["x-unknown"] = True
    elif mutation == "remote_ref":
        schema["$defs"]["assessment"]["properties"]["confidence"] = {
            "$ref": "https://example.invalid/schema"}
    elif mutation == "missing_required":
        chat["required"].remove("option_id")
    elif mutation == "wrong_signature":
        chat["properties"]["other"] = {"type": "string"}
        chat["required"].append("other")
    elif mutation == "duplicate_signature":
        schema["properties"]["decision"]["oneOf"].append(copy.deepcopy(chat))
    else:
        schema["title"] = "changed"
    with pytest.raises(ValueError, match="SCHEMA_SHAPE_CHANGED"):
        probe.plan_schema(schema)


@pytest.mark.parametrize("act", ["NONE", "CLAIM", "QUESTION", "ANSWER", "REBUTTAL",
                                 "OPINION_CHANGE", "RELATION_HYPOTHESIS"])
def test_all_seven_speech_acts_survive_split_and_join(act):
    request = request_with(chats=_chats(1))
    if act == "OPINION_CHANGE":
        request = _with_assessment(request)
    p = project_brain_input(request, config=CONFIG)
    value = legacy(p, "chat")
    source = json.loads(canonical_json_bytes(
        p.canonical_input["grounding"]["allowed_evidence_refs"][0]))
    speech = {
        "NONE": {"kind": "NONE"},
        "CLAIM": {"kind": "CLAIM", "subject_player_id": "opaque-peer",
                  "topic": "VOTE", "stance": "OPPOSE", "evidence": [source]},
        "QUESTION": {"kind": "QUESTION", "addressee_player_id": "opaque-peer",
                     "subject_player_id": None, "topic": "VOTE", "source": source},
        "ANSWER": {"kind": "ANSWER", "addressee_player_id": "opaque-peer",
                   "in_reply_to": source, "source_interpretation": "QUESTION",
                   "topic": "VOTE", "stance": "OPPOSE", "evidence": []},
        "REBUTTAL": {"kind": "REBUTTAL", "addressee_player_id": "opaque-peer",
                     "in_reply_to": source, "source_interpretation": "CLAIM",
                     "topic": "VOTE", "stance": "OPPOSE", "evidence": []},
        "OPINION_CHANGE": {"kind": "OPINION_CHANGE", "subject_player_id": "opaque-peer",
                           "dimension": "SUSPICION", "prior": 61, "current": 80,
                           "causes": [source]},
        "RELATION_HYPOTHESIS": {"kind": "RELATION_HYPOTHESIS",
                                "source_player_id": "opaque-peer",
                                "target_player_id": "opaque-self", "relation": "ACCUSES",
                                "confidence": 70, "evidence": [source]},
    }[act]
    value["discussion"]["speech_act"] = speech
    plan, text = probe.split_legacy(value, p.decision_schema)
    assert probe.validate_final(plan, json.dumps(text), p) == value


@pytest.mark.parametrize("raw,code", [
    ('{"a":1,"a":2}', "JSON_DUPLICATE_KEY"),
    ('{"a":NaN}', "JSON_SYNTAX"), ('{"a":1e999}', "JSON_SYNTAX"),
    ('[]', "JSON_SCHEMA"), ('', "JSON_SYNTAX")])
def test_strict_json_rejects_non_strict_input(raw, code):
    with pytest.raises(ValueError, match=code):
        probe.strict_json(raw)


def test_placeholder_and_parser_result_never_escape(monkeypatch):
    p = projection()
    plan, _ = probe.split_legacy(legacy(p, "chat"), p.decision_schema)
    seen = []
    def fake(raw, *, projection):
        seen.append(json.loads(raw))
        return {"decision": {"message": "."}}
    monkeypatch.setattr(probe, "parse_llm_output", fake)
    result = probe.validate_plan(json.dumps(plan), p)
    assert seen[0]["decision"]["message"] == "."
    assert result == plan and "message" not in result["decision"]


def test_placeholder_locals_are_cleared_when_parser_raises(monkeypatch):
    p = projection()
    plan, _ = probe.split_legacy(legacy(p, "chat"), p.decision_schema)
    def fail(_raw, *, projection):
        raise DecisionValidationError(semantic_cases.DecisionValidationCode.VALUE_NOT_OFFERED)
    monkeypatch.setattr(probe, "parse_llm_output", fail)
    try:
        probe.validate_plan(json.dumps(plan), p)
    except DecisionValidationError as error:
        frame = error.__traceback__
        while frame and frame.tb_frame.f_code.co_name != "validate_plan":
            frame = frame.tb_next
        assert frame is not None
        assert "checked" not in frame.tb_frame.f_locals
        assert "decision" not in frame.tb_frame.f_locals
        assert "parsed" not in frame.tb_frame.f_locals
    else:
        raise AssertionError("expected validator rejection")


def test_invalid_plan_prevents_message_body_and_wrong_text_is_rejected():
    p = projection()
    baseline = provider_body(p)
    plan, _ = probe.split_legacy(legacy(p, "chat"), p.decision_schema)
    invalid = copy.deepcopy(plan)
    invalid["decision"]["option_id"] = "not-offered"
    with pytest.raises((DecisionValidationError, ValueError)):
        probe.message_body(baseline, invalid, p)
    with pytest.raises(ValueError, match="MESSAGE_SCHEMA_INVALID"):
        probe.validate_final(plan, '{"comment":"wrong"}', p)
    with pytest.raises(ValueError, match="MESSAGE_SCHEMA_INVALID"):
        probe.validate_final(plan, '{"message":"ok","extra":1}', p)


def test_no_message_action_rejects_second_call_and_text():
    p = projection("vote")
    plan, _ = probe.split_legacy(legacy(p, "vote"), p.decision_schema)
    with pytest.raises(ValueError, match="NO_MESSAGE_ACTION"):
        probe.message_body(provider_body(p), plan, p)
    with pytest.raises(ValueError, match="UNEXPECTED_MESSAGE"):
        probe.validate_final(plan, '{"message":"wrong"}', p)


def test_peer_chat_trigger_keeps_required_reaction_authority():
    chats = _chats(1)
    p = project_brain_input(request_with(trigger="PEER_CHAT", chats=chats), config=CONFIG)
    value = legacy(p, "chat")
    value["discussion"]["reaction"] = {
        "trigger": json.loads(canonical_json_bytes(p.discussion_capture.trigger.source)),
        "score": 60, "reason": "DIRECT_MENTION"}
    plan, text = probe.split_legacy(value, p.decision_schema)
    assert probe.validate_plan(json.dumps(plan), p) == plan
    assert probe.validate_final(plan, json.dumps(text), p) == value


@pytest.mark.parametrize("text,valid", [("", False), ("x", True),
    ("x" * 199 + ".", True), ("x" * 200 + ".", False)])
def test_final_message_uses_legacy_text_bounds(text, valid):
    p = projection()
    plan, _ = probe.split_legacy(legacy(p, "chat"), p.decision_schema)
    raw = json.dumps({"message": text})
    if valid:
        assert probe.validate_final(plan, raw, p)["decision"]["message"] == text
    else:
        with pytest.raises((ValueError, DecisionValidationError)):
            probe.validate_final(plan, raw, p)


_AUTHORITY_CASES = ["claim", "question", "answer", "rebuttal", "opinion_change",
    "relation_hypothesis", "assessment_update", "claim_update", "relation_update",
    "strategy_update", "reaction_score", "co_judgment", "pre_vote_reassessment"]


@pytest.mark.parametrize("case", _AUTHORITY_CASES)
def test_authority_matrix_legacy_acceptance_is_identical_through_adapter(case, monkeypatch):
    original = semantic_cases._parse
    measured = []
    def through_adapter(request, value):
        p = project_brain_input(request, config=semantic_cases.LLMBrainConfig())
        old = parse_llm_output(json.dumps(value), projection=p)
        plan, text = probe.split_legacy(value, p.decision_schema)
        assert probe.validate_plan(json.dumps(plan), p) == plan
        joined = probe.validate_final(plan, None if text is None else json.dumps(text), p)
        assert joined == value
        assert parse_llm_output(json.dumps(joined), projection=p) == old
        measured.append(True)
        return original(request, value)
    monkeypatch.setattr(semantic_cases, "_parse", through_adapter)
    semantic_cases.test_p6b_semantic_pass_authority_closed_positive_matrix(case)
    assert measured
