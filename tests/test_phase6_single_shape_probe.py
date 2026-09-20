from copy import deepcopy
import hashlib
import json

import pytest
from jsonschema import Draft202012Validator
from ai_client.llm.prompt import LLMBrainConfig, project_brain_input
from ai_client.discussion.model import EvidenceRecordKind
from scripts import phase6_single_shape_probe as probe
from scripts import phase6_model_comparison as tool
from scripts.phase6_conversation_suite import example
from tests.test_phase6_semantic_output import (
    ChatRecord, CoDeclarationRecord, CoReportRecord, _captured_ref, _payload, _ref_json, _request,
)


def setup(kind='chat'):
    request = _request(kind)
    projection = project_brain_input(request, config=LLMBrainConfig())
    legacy = _payload(request, kind)
    return projection, legacy


def test_all_32_schema_only_diff_wire_order_roundtrip():
    for case in tool.cases():
        p = tool.project(case, 'baseline')
        old = tool.body_for(p, tool.PROFILES['qw9'][0])
        body = probe.candidate_body(old)
        schema = body['response_format']['json_schema']['schema']
        restored = deepcopy(body)
        restored['response_format']['json_schema']['schema']['$defs']['speech_act'] = old['response_format']['json_schema']['schema']['$defs']['speech_act']
        assert restored == old
        wire = probe.single_shape_wire_bytes(body)
        assert json.loads(wire) == body
        assert list(json.loads(wire)['response_format']['json_schema']['schema']['$defs']['speech_act']['properties']) == list(probe.FIELDS)
        value = example(p.canonical_input)
        if value is not None:
            candidate = probe.legacy_to_candidate(value, p.decision_schema)
            assert Draft202012Validator(schema).is_valid(candidate)
            assert probe.candidate_to_legacy(candidate, p.decision_schema) == value


@pytest.mark.parametrize('kind', list(probe.DECISION_FIELDS))
def test_five_actions_both_roundtrips_and_parser_equality(kind):
    p, legacy = setup(kind)
    candidate = probe.legacy_to_candidate(legacy, p.decision_schema)
    restored = probe.candidate_to_legacy(candidate, p.decision_schema)
    assert restored == legacy
    assert probe.legacy_to_candidate(restored, p.decision_schema) == candidate
    assert tool.parse_llm_output(json.dumps(restored), projection=p) == tool.parse_llm_output(json.dumps(legacy), projection=p)


@pytest.mark.parametrize('case', ['claim', 'question', 'answer', 'rebuttal', 'opinion_change',
    'relation_hypothesis', 'assessment_update', 'claim_update', 'relation_update',
    'strategy_update', 'reaction_score', 'co_judgment', 'pre_vote_reassessment'])
def test_authority_positive_matrix_and_every_field_null_policy(case):
    request, legacy = _authority_case(case)
    p = project_brain_input(request, config=LLMBrainConfig())
    candidate = probe.legacy_to_candidate(legacy, p.decision_schema)
    restored = probe.candidate_to_legacy(candidate, p.decision_schema)
    assert restored == legacy
    assert probe.legacy_to_candidate(restored, p.decision_schema) == candidate
    assert tool.parse_llm_output(json.dumps(restored), projection=p) == tool.parse_llm_output(json.dumps(legacy), projection=p)
    act = candidate['discussion']['speech_act']
    for field in probe.FIELDS[1:]:
        if field not in probe.ACT_FIELDS[act['kind']]:
            changed = deepcopy(candidate); changed['discussion']['speech_act'][field] = 'not-null'
            with pytest.raises(ValueError): probe.candidate_to_legacy(changed, p.decision_schema)
        elif not (act['kind'] == 'QUESTION' and field in {'subject_player_id', 'source'}):
            changed = deepcopy(candidate); changed['discussion']['speech_act'][field] = None
            with pytest.raises(ValueError): probe.candidate_to_legacy(changed, p.decision_schema)


def test_nullable_question_keys_remain_and_none_is_legal():
    request, legacy = _authority_case('question')
    p = project_brain_input(request, config=LLMBrainConfig())
    legacy['discussion']['speech_act'].update(subject_player_id=None, source=None)
    c = probe.legacy_to_candidate(legacy, p.decision_schema)
    assert probe.candidate_to_legacy(c, p.decision_schema) == legacy
    p, legacy = setup('none'); c = probe.legacy_to_candidate(legacy, p.decision_schema)
    assert all(c['discussion']['speech_act'][f] is None for f in probe.FIELDS[1:])
    assert probe.candidate_to_legacy(c, p.decision_schema) == legacy


@pytest.mark.parametrize('case,wrong', [('answer','CLAIM'), ('rebuttal','QUESTION'), ('answer','invented')])
def test_interpretation_mismatch_rejected_without_repair(case, wrong):
    request, legacy = _authority_case(case); p = project_brain_input(request, config=LLMBrainConfig())
    c = probe.legacy_to_candidate(legacy, p.decision_schema)
    c['discussion']['speech_act']['source_interpretation'] = wrong
    with pytest.raises(ValueError): probe.candidate_to_legacy(c, p.decision_schema)


@pytest.mark.parametrize('raw', ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}', '{"a":1e999}', '{'])
def test_strict_json(raw):
    with pytest.raises(ValueError): probe.strict_json(raw)


@pytest.mark.parametrize('mutation', ['missing','extra','unknown','extra_null','duplicate','const','nullable','remote','open','top_keyword','decision_keyword','proposal_keyword','proposal_duplicate','proposal_missing'])
def test_unknown_shapes_fail_closed(mutation):
    p, _ = setup(); schema = probe.plain(p.decision_schema)
    acts = schema['$defs']['speech_act']['oneOf']
    if mutation == 'missing': acts.pop()
    if mutation == 'extra': acts.append(deepcopy(acts[0]))
    if mutation == 'unknown': acts[0]['properties']['kind']['const'] = 'UNKNOWN'
    if mutation == 'extra_null': acts[0]['properties']['invented'] = {'type':'null'}
    if mutation == 'duplicate': acts[-1] = deepcopy(acts[0])
    if mutation == 'const': acts[3]['properties']['source_interpretation'] = {'const':'UNKNOWN'}
    if mutation == 'nullable': acts[1]['properties']['topic'] = {'anyOf':[{'type':'null'}, {'type':'string'}]}
    if mutation == 'remote': acts[1]['properties']['topic'] = {'$ref':'https://invalid.example/schema'}
    if mutation == 'open': acts[0]['additionalProperties'] = True
    if mutation == 'top_keyword': schema['title'] = 'unknown'
    if mutation == 'decision_keyword': schema['properties']['decision']['oneOf'][0]['title'] = 'unknown'
    if mutation == 'proposal_duplicate': schema['properties']['discussion']['oneOf'].append(deepcopy(schema['properties']['discussion']['oneOf'][0]))
    if mutation == 'proposal_missing': schema['properties']['discussion']['oneOf'].clear()
    if mutation == 'proposal_keyword': schema['properties']['discussion']['oneOf'][0]['title'] = 'unknown'
    with pytest.raises(ValueError, match='SCHEMA_SHAPE_CHANGED'): probe.candidate_schema(schema)


@pytest.mark.parametrize('mutation', ['missing','extra','unknown','inactive','null_array','schema'])
def test_rejection_records_stage_and_never_calls_legacy_screen(monkeypatch, mutation):
    p, legacy = setup(); c = probe.legacy_to_candidate(legacy, p.decision_schema)
    body = probe.candidate_body(tool.body_for(p, tool.PROFILES['qw9'][0]))
    act = c['discussion']['speech_act']
    if mutation == 'missing': act.pop('source')
    if mutation == 'extra': act['unknown'] = None
    if mutation == 'unknown': act['kind'] = 'UNKNOWN'
    if mutation == 'inactive': act['topic'] = 'VOTE'
    if mutation == 'null_array': act.update(kind='CLAIM',subject_player_id='peer',topic='VOTE',stance='UNCERTAIN',evidence=None)
    if mutation == 'schema': body['response_format']['json_schema']['schema']['$defs']['speech_act']['additionalProperties'] = True
    monkeypatch.setattr(tool, 'screen', lambda *a: pytest.fail('invalid reached legacy'))
    row = probe.assess_candidate(tool.cases()[0], p, body, json.dumps(c))
    assert row['legacy_contract_pass'] is None and row['adapter_status'] == 'NOT_APPLIED'


@pytest.mark.parametrize('mutation', ['ref','visibility','actor','prior','new_cause','co','pre_vote','bytes','role','target','option','duplicate_update'])
def test_legacy_negative_acceptance_preserved(mutation):
    name = {'actor':'claim_update','prior':'opinion_change','new_cause':'opinion_change','co':'co_judgment',
        'pre_vote':'pre_vote_reassessment','role':'co_judgment','target':'pre_vote_reassessment',
        'duplicate_update':'assessment_update'}.get(mutation,'answer')
    request, value = _authority_case(name); p = project_brain_input(request, config=LLMBrainConfig())
    if mutation == 'ref': value['discussion']['speech_act']['in_reply_to']['order'] = 999
    if mutation == 'visibility': value['discussion']['speech_act']['in_reply_to']['visibility'] = 'PRIVATE'
    if mutation == 'actor': value['discussion']['claim_updates'][0]['speaker_player_id'] = 'self'
    if mutation == 'prior': value['discussion']['speech_act']['prior'] = 26
    if mutation == 'new_cause': value['discussion']['speech_act']['causes'] = []
    if mutation == 'co': value['discussion']['co_judgment'] = {'decision':'SILENCE','selected_option_id':None,'claimed_role_id':None}
    if mutation == 'pre_vote': value['discussion']['pre_vote_reassessment']['ranked_target_player_ids'] = []
    if mutation == 'bytes': value['decision']['message'] = '😀'*200
    if mutation == 'role': value['decision']['claimed_role_id'] = 'invented'
    if mutation == 'target': value['decision']['target_player_id'] = 'dead-or-unknown'
    if mutation == 'option': value['decision']['option_id'] = 'invented'
    if mutation == 'duplicate_update': value['discussion']['assessment_updates'] *= 2
    with pytest.raises(tool.DecisionValidationError): tool.parse_llm_output(json.dumps(value), projection=p)
    # Build the deliberately invalid counterpart without treating inverse validation as success.
    candidate = deepcopy(value); act = candidate['discussion']['speech_act']
    candidate['discussion']['speech_act'] = {field: act.get(field) for field in probe.FIELDS}
    try: adapted = probe.candidate_to_legacy(candidate, p.decision_schema)
    except ValueError: return
    assert adapted == value
    with pytest.raises(tool.DecisionValidationError): tool.parse_llm_output(json.dumps(adapted), projection=p)


def test_alive_dead_owner_only_results_and_text_not_modified():
    from ai_client.world import AbilityResultRecord
    from tests.test_phase6_quality_grounding import CONFIG, request_with
    result = AbilityResultRecord(3, 1, 'opaque-phase', 'inspect_result', 'opaque-peer', 'owner-only-result')
    owner = project_brain_input(request_with(results=(result,), dead=True), config=CONFIG)
    other = project_brain_input(request_with(results=(), dead=True), config=CONFIG)
    for p in (owner, other):
        old = tool.body_for(p, tool.PROFILES['qw9'][0]); body = probe.candidate_body(old)
        assert body['messages'] == old['messages']
    assert 'owner-only-result' in json.dumps(probe.candidate_body(tool.body_for(owner, 'qw9'))['messages'])
    assert 'owner-only-result' not in json.dumps(probe.candidate_body(tool.body_for(other, 'qw9'))['messages'])


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
