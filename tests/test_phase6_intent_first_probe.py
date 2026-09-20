from copy import deepcopy
import hashlib
import json

from jsonschema import Draft202012Validator
import pytest

from ai_client.llm.prompt import LLMBrainConfig, project_brain_input
from ai_client.discussion.model import EvidenceRecordKind
from scripts import phase6_intent_first_probe as probe
from scripts import phase6_model_comparison as tool
from scripts.phase6_conversation_suite import example
from tests.test_phase6_semantic_output import (
    ChatRecord, CoDeclarationRecord, CoReportRecord,
    _captured_ref, _payload, _ref_json, _request,
)


def _body(projection):
    return probe.candidate_body(tool.body_for(projection, tool.PROFILES["qw9"][0]))


def test_all_32_schema_leafs_roundtrip_and_declared_wire_order():
    seen_acts = set()
    for case in tool.cases():
        projection = tool.project(case, "baseline")
        baseline = tool.body_for(projection, tool.PROFILES["qw9"][0])
        before = deepcopy(baseline)
        body = _body(projection)
        schema = body["response_format"]["json_schema"]["schema"]
        assert baseline == before
        assert schema["$defs"] == baseline["response_format"]["json_schema"]["schema"]["$defs"]
        assert all(list(branch["properties"]) == list(probe.ROOT_ORDER) for branch in schema["oneOf"])
        for branch in schema["oneOf"]:
            intent = branch["properties"]["intent"]
            assert list(intent["properties"]) == ["discussion", "decision"]
            assert list(intent["properties"]["discussion"]["properties"]) == list(probe.DISCUSSION_FIELDS)
            assert list(branch["properties"]["updates"]["properties"]) == sorted(probe.UPDATE_FIELDS)
        wire = probe.intent_first_wire_bytes(body)
        assert wire == probe.intent_first_wire_bytes(body)
        assert json.loads(wire) == body
        value = example(projection.canonical_input)
        if value is None:
            continue
        candidate = probe.legacy_to_candidate(value, projection.decision_schema)
        assert Draft202012Validator(schema).is_valid(candidate)
        assert probe.candidate_to_legacy(candidate, schema) == value
        seen_acts.add(value["discussion"]["speech_act"]["kind"])
    assert seen_acts == {"QUESTION", "ANSWER", "REBUTTAL"}


@pytest.mark.parametrize("kind", ["none", "chat", "vote", "ability", "co_declare"])
def test_all_five_actions_preserve_legacy_parser_acceptance(kind):
    request = _request(kind)
    projection = project_brain_input(request, config=LLMBrainConfig())
    legacy = _payload(request, kind)
    body = _body(projection)
    candidate = probe.legacy_to_candidate(legacy, projection.decision_schema)
    schema = body["response_format"]["json_schema"]["schema"]
    assert probe.candidate_to_legacy(candidate, schema) == legacy
    tool.parse_llm_output(json.dumps(legacy), projection=projection)
    assert probe.legacy_to_candidate(
        probe.candidate_to_legacy(candidate, schema), projection.decision_schema
    ) == candidate


def test_assessment_row_is_legacy_compatible_and_records_order_and_hash():
    case = tool.cases()[0]
    projection = tool.project(case, "baseline")
    legacy = example(projection.canonical_input)
    body = _body(projection)
    candidate = probe.legacy_to_candidate(legacy, projection.decision_schema)
    row = probe.assess_candidate(case, projection, body, json.dumps(candidate))
    assert row["candidate_schema_pass"] and row["legacy_contract_pass"]
    assert row["adapter_status"] == "FIELD_MOVE_COMPLETE" and row["root_order_pass"]
    assert row["adapted_output_sha256"] == hashlib.sha256(tool.wire_bytes(legacy)).hexdigest()


@pytest.mark.parametrize("act", ["NONE", "CLAIM", "QUESTION", "ANSWER", "REBUTTAL",
                                  "OPINION_CHANGE", "RELATION_HYPOTHESIS"])
def test_all_seven_speech_acts_preserve_schema_acceptance(act):
    request = _request("chat")
    projection = project_brain_input(request, config=LLMBrainConfig())
    legacy = _payload(request, "chat")
    ref = {"record_kind": "chat", "order": 1, "visibility": "PUBLIC"}
    acts = {
        "NONE": {"kind": "NONE"},
        "CLAIM": {"kind": "CLAIM", "subject_player_id": "peer", "topic": "ALIGNMENT", "stance": "UNCERTAIN", "evidence": []},
        "QUESTION": {"kind": "QUESTION", "addressee_player_id": "peer", "subject_player_id": None, "topic": "ALIGNMENT", "source": None},
        "ANSWER": {"kind": "ANSWER", "addressee_player_id": "peer", "in_reply_to": ref, "source_interpretation": "QUESTION", "topic": "ALIGNMENT", "stance": "UNCERTAIN", "evidence": []},
        "REBUTTAL": {"kind": "REBUTTAL", "addressee_player_id": "peer", "in_reply_to": ref, "source_interpretation": "CLAIM", "topic": "ALIGNMENT", "stance": "OPPOSE", "evidence": []},
        "OPINION_CHANGE": {"kind": "OPINION_CHANGE", "subject_player_id": "peer", "dimension": "SUSPICION", "prior": 50, "current": 60, "causes": [ref]},
        "RELATION_HYPOTHESIS": {"kind": "RELATION_HYPOTHESIS", "source_player_id": "peer", "target_player_id": "self", "relation": "SUPPORTS", "confidence": 50, "evidence": []},
    }
    legacy["discussion"]["speech_act"] = acts[act]
    baseline_schema = tool.body_for(projection, tool.PROFILES["qw9"][0])["response_format"]["json_schema"]["schema"]
    baseline_valid = Draft202012Validator(baseline_schema).is_valid(legacy)
    assert baseline_valid
    candidate = probe.legacy_to_candidate(legacy, projection.decision_schema)
    schema = _body(projection)["response_format"]["json_schema"]["schema"]
    assert Draft202012Validator(schema).is_valid(candidate)
    assert probe.candidate_to_legacy(candidate, schema) == legacy


@pytest.mark.parametrize("raw", [
    '{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}', '{"a":1e999}', '{',
])
def test_strict_json_rejects_duplicate_syntax_and_nonfinite(raw):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        probe.strict_json(raw)


@pytest.mark.parametrize("mutation", [
    "missing_updates", "null_array", "extra", "intent_text", "wrong_text_field", "no_text_body",
])
def test_candidate_closed_shape_rejections_never_reach_legacy(mutation, monkeypatch):
    request = _request("chat" if mutation != "no_text_body" else "vote")
    projection = project_brain_input(request, config=LLMBrainConfig())
    legacy = _payload(request, "chat" if mutation != "no_text_body" else "vote")
    body = _body(projection)
    value = probe.legacy_to_candidate(legacy, projection.decision_schema)
    if mutation == "missing_updates": del value["updates"]
    if mutation == "null_array": value["updates"]["claim_updates"] = None
    if mutation == "extra": value["updates"]["PRIVATE_UNUSED_FIELD"] = "PRIVATE_VALUE"
    if mutation == "intent_text": value["intent"]["decision"]["message"] = value["realization"]["message"]
    if mutation == "wrong_text_field": value["realization"] = {"comment": "wrong"}
    if mutation == "no_text_body": value["realization"] = {"message": "wrong"}
    monkeypatch.setattr(tool, "screen", lambda *_: pytest.fail("legacy screen called"))
    row = probe.assess_candidate(None, projection, body, json.dumps(value))
    assert not row["candidate_schema_pass"] and row["legacy_contract_pass"] is None
    assert row["adapter_status"] == "NOT_APPLIED" and row["adapted_output_sha256"] is None
    assert "PRIVATE" not in json.dumps(row)


@pytest.mark.parametrize("mutation", ["unknown_kind", "duplicate_signature", "remote_ref", "discussion_extra"])
def test_shape_drift_fails_closed(mutation):
    projection = tool.project(tool.cases()[0], "baseline")
    body = tool.body_for(projection, tool.PROFILES["qw9"][0])
    schema = body["response_format"]["json_schema"]["schema"]
    if mutation == "unknown_kind": schema["properties"]["decision"]["oneOf"][1]["properties"]["kind"] = {"const": "unknown"}
    if mutation == "duplicate_signature": schema["properties"]["decision"]["oneOf"].append(deepcopy(schema["properties"]["decision"]["oneOf"][1]))
    if mutation == "remote_ref": schema["$defs"]["bad"] = {"$ref": "https://example.invalid/schema"}
    if mutation == "discussion_extra": schema["properties"]["discussion"]["oneOf"][0]["properties"]["extra"] = {"type": "null"}
    with pytest.raises(ValueError, match="^SCHEMA_SHAPE_CHANGED$"):
        probe.candidate_body(body)


def test_co_report_context_free_stays_out_of_candidate():
    request = _request("co_report")
    projection = project_brain_input(request, config=LLMBrainConfig())
    with pytest.raises(ValueError, match="LEGACY_SCHEMA_INVALID"):
        probe.legacy_to_candidate(_payload(request, "co_report"), projection.decision_schema)


@pytest.mark.parametrize("mutation", [
        "option", "target", "role", "ability_target", "reaction", "update_count", "text_length",
])
def test_candidate_path_matches_legacy_schema_rejection(mutation):
    kind = {"role": "co_declare", "ability_target": "ability"}.get(mutation, "chat")
    request = _request(kind, peer=mutation == "reaction")
    projection = project_brain_input(request, config=LLMBrainConfig())
    legacy = _payload(request, kind)
    candidate = probe.legacy_to_candidate(legacy, projection.decision_schema)
    if mutation == "option": candidate["intent"]["decision"]["option_id"] = "not-offered"
    if mutation == "target": candidate["intent"]["discussion"]["speech_act"] = {
        "kind": "CLAIM", "subject_player_id": "dead-or-unknown", "topic": "ALIGNMENT",
        "stance": "UNCERTAIN", "evidence": []}
    if mutation == "role": candidate["intent"]["decision"]["claimed_role_id"] = "not-offered"
    if mutation == "ability_target": candidate["intent"]["decision"]["target_player_ids"] = ["self"]
    if mutation == "reaction": candidate["intent"]["discussion"]["reaction"]["trigger"]["order"] += 1
    if mutation == "update_count": candidate["updates"]["assessment_updates"] = [
        {"target_player_id": "peer", "suspicion": 1, "credibility": 1,
         "confidence": 1, "evidence": []} for _ in range(5)]
    if mutation == "text_length": candidate["realization"]["message"] = "x" * 10_000
    schema = _body(projection)["response_format"]["json_schema"]["schema"]
    assert not Draft202012Validator(schema).is_valid(candidate)
    with pytest.raises(ValueError, match="CANDIDATE_SCHEMA_INVALID"):
        probe.candidate_to_legacy(candidate, schema)


@pytest.mark.parametrize("mutation", [
    "visibility", "claim_actor", "duplicate_update", "utf8_bytes",
])
def test_candidate_path_matches_legacy_semantic_rejection(mutation):
    request = _request("chat", peer=True, private=mutation == "visibility")
    projection = project_brain_input(request, config=LLMBrainConfig())
    legacy = _payload(request, "chat")
    ref = _ref_json(_captured_ref(request, 1, EvidenceRecordKind.CHAT))
    if mutation == "visibility":
        ref["visibility"] = "PUBLIC"
        legacy["discussion"]["assessment_updates"] = [{
            "target_player_id": "peer", "suspicion": 1, "credibility": 1,
            "confidence": 1, "evidence": [ref]}]
    if mutation == "claim_actor":
        legacy["discussion"]["claim_updates"] = [{
            "claim": ref, "speaker_player_id": "self", "verdict": "UNVERIFIED",
            "confidence": 1, "evidence": []}]
    if mutation == "duplicate_update":
        update = {"target_player_id": "peer", "suspicion": 1, "credibility": 1,
                  "confidence": 1, "evidence": []}
        legacy["discussion"]["assessment_updates"] = [update, deepcopy(update)]
    if mutation == "utf8_bytes":
        legacy["decision"]["message"] = "😀" * 200
    baseline_schema = tool.body_for(projection, tool.PROFILES["qw9"][0])["response_format"]["json_schema"]["schema"]
    assert Draft202012Validator(baseline_schema).is_valid(legacy)
    candidate = probe.legacy_to_candidate(legacy, projection.decision_schema)
    candidate_schema = _body(projection)["response_format"]["json_schema"]["schema"]
    assert Draft202012Validator(candidate_schema).is_valid(candidate)
    adapted = probe.candidate_to_legacy(candidate, candidate_schema)
    assert adapted == legacy
    with pytest.raises(tool.DecisionValidationError):
        tool.parse_llm_output(json.dumps(legacy), projection=projection)
    with pytest.raises(tool.DecisionValidationError):
        tool.parse_llm_output(json.dumps(adapted), projection=projection)


@pytest.mark.parametrize("which", ["legacy_top", "decision_union", "proposal_branch", "candidate_top", "candidate_branch"])
def test_unknown_schema_keywords_fail_closed(which):
    projection = tool.project(tool.cases()[0], "baseline")
    baseline = tool.body_for(projection, tool.PROFILES["qw9"][0])
    if which.startswith("candidate"):
        body = _body(projection)
        schema = body["response_format"]["json_schema"]["schema"]
        target = schema if which == "candidate_top" else schema["oneOf"][0]
        target["unknown_keyword"] = True
        with pytest.raises(ValueError, match="^SCHEMA_SHAPE_CHANGED$"):
            probe.candidate_to_legacy(
                probe.legacy_to_candidate(example(projection.canonical_input), projection.decision_schema),
                schema,
            )
        with pytest.raises(ValueError, match="^SCHEMA_SHAPE_CHANGED$"):
            probe.intent_first_wire_bytes(body)
    else:
        schema = baseline["response_format"]["json_schema"]["schema"]
        target = {"legacy_top": schema,
                  "decision_union": schema["properties"]["decision"],
                  "proposal_branch": schema["properties"]["discussion"]["oneOf"][0]}[which]
        target["unknown_keyword"] = True
        with pytest.raises(ValueError, match="^SCHEMA_SHAPE_CHANGED$"):
            probe.candidate_body(baseline)


def test_wire_canonicalizes_body_and_restores_all_declared_wrapper_orders():
    projection = tool.project(tool.cases()[0], "baseline")
    body = _body(projection)
    body = json.loads(json.dumps(body, sort_keys=True))
    encoded = probe.intent_first_wire_bytes(body)
    value = json.loads(encoded, object_pairs_hook=dict)
    for branch in value["response_format"]["json_schema"]["schema"]["oneOf"]:
        assert list(branch["properties"]) == list(probe.ROOT_ORDER)
        intent = branch["properties"]["intent"]
        assert list(intent["properties"]) == ["discussion", "decision"]
        assert list(intent["properties"]["discussion"]["properties"]) == list(probe.DISCUSSION_FIELDS)
        assert list(intent["properties"]["decision"]["properties"]) == sorted(intent["properties"]["decision"]["properties"])
        assert list(branch["properties"]["realization"]["properties"]) == sorted(branch["properties"]["realization"]["properties"])
        assert list(branch["properties"]["updates"]["properties"]) == sorted(probe.UPDATE_FIELDS)


def _authority_case(case):
    public = ChatRecord(1, 1, "day", "public", "peer", "peer", "source")
    kind, kw = "chat", {"records": (public,)}
    if case in {"claim", "strategy_update"}: kind = "ability"
    if case in {"answer", "relation_hypothesis", "relation_update"}: kw = {"peer": True}
    if case == "reaction_score": kw = {"peer": True, "private": True}
    if case == "opinion_change": kw = {"peer": True, "prior_assessment": True}
    if case == "rebuttal": kw = {"peer": True, "records": (
        CoDeclarationRecord(1, 1, "day", "peer", "claim-role", "claim"),
        ChatRecord(2, 1, "day", "public", "peer", "peer", "trigger"))}
    if case == "claim_update": kw = {"peer": True, "records": (
        CoReportRecord(1, 1, "day", "peer", "inspect", "self", "claim"),
        ChatRecord(2, 1, "day", "public", "peer", "peer", "trigger"))}
    if case == "assessment_update": kind = "none"
    if case == "co_judgment": kind = "co_declare"
    if case == "pre_vote_reassessment": kind = "vote"
    request = _request(kind, **kw)
    value = _payload(request, kind)
    ref = _ref_json(_captured_ref(request, 1, EvidenceRecordKind.CO_DECLARATION
        if case == "rebuttal" else EvidenceRecordKind.CO_REPORT
        if case == "claim_update" else EvidenceRecordKind.CHAT)) if case not in {"co_judgment"} else None
    acts = {
        "claim": {"kind": "CLAIM", "subject_player_id": "peer", "topic": "ALIGNMENT", "stance": "OPPOSE", "evidence": [ref]},
        "question": {"kind": "QUESTION", "addressee_player_id": "peer", "subject_player_id": "self", "topic": "STRATEGY", "source": ref},
        "answer": {"kind": "ANSWER", "addressee_player_id": "peer", "in_reply_to": ref, "source_interpretation": "QUESTION", "topic": "VOTE", "stance": "UNCERTAIN", "evidence": [ref]},
        "rebuttal": {"kind": "REBUTTAL", "addressee_player_id": "peer", "in_reply_to": ref, "source_interpretation": "CLAIM", "topic": "ROLE_CLAIM", "stance": "OPPOSE", "evidence": [ref]},
        "opinion_change": {"kind": "OPINION_CHANGE", "subject_player_id": "peer", "dimension": "SUSPICION", "prior": 25, "current": 70, "causes": [ref]},
        "relation_hypothesis": {"kind": "RELATION_HYPOTHESIS", "source_player_id": "self", "target_player_id": "peer", "relation": "SUPPORTS", "confidence": 64, "evidence": [ref]},
    }
    if case in acts: value["discussion"]["speech_act"] = acts[case]
    if case == "assessment_update": value["discussion"]["assessment_updates"] = [{"target_player_id": "peer", "suspicion": 61, "credibility": 39, "confidence": 80, "evidence": [ref]}]
    if case == "claim_update": value["discussion"]["claim_updates"] = [{"claim": ref, "speaker_player_id": "peer", "verdict": "CONTRADICTED", "confidence": 77, "evidence": [ref]}]
    if case == "relation_update": value["discussion"]["relation_updates"] = [{"source_player_id": "self", "target_player_id": "peer", "relation": "ACCUSES", "confidence": 66, "evidence": [ref], "provenance": "PUBLIC_INFERENCE"}]
    if case == "strategy_update": value["discussion"]["strategy_update"] = {"scope": "PHASE", "mode": "TEST_CLAIM", "focus_player_ids": ["peer"], "evidence": [ref]}
    if case == "pre_vote_reassessment": value["discussion"]["pre_vote_reassessment"]["evidence"] = [ref]
    return request, value


@pytest.mark.parametrize("case", ["claim", "question", "answer", "rebuttal", "opinion_change",
    "relation_hypothesis", "assessment_update", "claim_update", "relation_update", "strategy_update",
    "reaction_score", "co_judgment", "pre_vote_reassessment"])
def test_authority_closed_positive_matrix_matches_through_candidate(case):
    request, legacy = _authority_case(case)
    projection = project_brain_input(request, config=LLMBrainConfig())
    old = tool.parse_llm_output(json.dumps(legacy), projection=projection)
    candidate = probe.legacy_to_candidate(legacy, projection.decision_schema)
    adapted = probe.candidate_to_legacy(candidate, _body(projection)["response_format"]["json_schema"]["schema"])
    new = tool.parse_llm_output(json.dumps(adapted), projection=projection)
    assert adapted == legacy and new == old


@pytest.mark.parametrize("case", ["visibility", "actor", "prior", "new_cause", "co", "pre_vote", "proposal_bytes"])
def test_representative_authority_rejections_match_through_candidate(case):
    base = {"visibility": "reaction_score", "actor": "claim_update", "prior": "opinion_change",
            "new_cause": "opinion_change", "co": "co_judgment", "pre_vote": "pre_vote_reassessment",
            "proposal_bytes": "answer"}[case]
    request, legacy = _authority_case(base)
    if case == "visibility": legacy["discussion"]["reaction"]["trigger"]["visibility"] = "PUBLIC"
    if case == "actor": legacy["discussion"]["claim_updates"][0]["speaker_player_id"] = "self"
    if case == "prior": legacy["discussion"]["speech_act"]["prior"] = 26
    if case == "new_cause": legacy["discussion"]["speech_act"]["causes"] = []
    if case == "co": legacy["discussion"]["co_judgment"] = {"decision": "SILENCE", "selected_option_id": None, "claimed_role_id": None}
    if case == "pre_vote": legacy["discussion"]["pre_vote_reassessment"]["ranked_target_player_ids"] = []
    if case == "proposal_bytes": legacy["decision"]["message"] = "😀" * 200
    projection = project_brain_input(request, config=LLMBrainConfig())
    with pytest.raises(tool.DecisionValidationError):
        tool.parse_llm_output(json.dumps(legacy), projection=projection)
    schema = tool.body_for(projection, tool.PROFILES["qw9"][0])["response_format"]["json_schema"]["schema"]
    if not Draft202012Validator(schema).is_valid(legacy):
        decision, discussion = deepcopy(legacy["decision"]), deepcopy(legacy["discussion"])
        text = probe.TEXT_FIELD.get(decision["kind"])
        candidate = {"intent": {"discussion": discussion, "decision": decision},
                     "realization": {}, "updates": {}}
        if text: candidate["realization"][text] = candidate["intent"]["decision"].pop(text)
        for key in probe.UPDATE_FIELDS:
            candidate["updates"][key] = candidate["intent"]["discussion"].pop(key)
        candidate_schema = _body(projection)["response_format"]["json_schema"]["schema"]
        assert not Draft202012Validator(candidate_schema).is_valid(candidate)
        with pytest.raises(ValueError, match="CANDIDATE_SCHEMA_INVALID"):
            probe.candidate_to_legacy(candidate, candidate_schema)
        return
    candidate = probe.legacy_to_candidate(legacy, projection.decision_schema)
    adapted = probe.candidate_to_legacy(candidate, _body(projection)["response_format"]["json_schema"]["schema"])
    with pytest.raises(tool.DecisionValidationError):
        tool.parse_llm_output(json.dumps(adapted), projection=projection)


def test_candidate_body_preserves_alive_dead_and_owner_only_ability_grounding():
    from ai_client.world import AbilityResultRecord
    from tests.test_phase6_quality_grounding import CONFIG, request_with

    private_result = AbilityResultRecord(
        3, 1, "opaque-phase", "inspect_result", "opaque-peer", "owner-only-result"
    )
    owner_projection = project_brain_input(
        request_with(results=(private_result,), dead=True), config=CONFIG
    )
    baseline = tool.body_for(owner_projection, tool.PROFILES["qw9"][0])
    candidate = probe.candidate_body(baseline)
    assert candidate["messages"] == baseline["messages"]
    assert candidate["messages"] == json.loads(tool.wire_bytes(baseline))["messages"]
    user = candidate["messages"][1]["content"]
    assert '"alive_player_ids":["opaque-self"]' in user
    assert '"result_id":"owner-only-result"' in user
    assert '"target_player_id":"opaque-peer"' in user

    other_projection = project_brain_input(request_with(dead=True), config=CONFIG)
    other_baseline = tool.body_for(other_projection, tool.PROFILES["qw9"][0])
    other_candidate = probe.candidate_body(other_baseline)
    assert other_candidate["messages"] == other_baseline["messages"]
    assert "owner-only-result" not in json.dumps(other_candidate["messages"])
