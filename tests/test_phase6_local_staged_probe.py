from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_client.discussion.context import canonical_json_bytes
from ai_client.llm.prompt import project_brain_input
from ai_client.world import AbilityResultRecord, CoDeclarationRecord
from scripts import phase6_local_staged_probe as probe
from scripts import phase6_recovery_runner as recovery
from tests.test_phase6_two_call_probe import projection, legacy
from tests.test_phase6_quality_grounding import CONFIG, request_with, payload
from tests.test_phase6_memory_projection import _chats, _with_assessment
from tests import test_phase6_semantic_output as semantic


def fixture_legacy(p, kind):
    """Finite public witnesses, never used as a provider fallback."""
    v = probe.plain(p.canonical_input)
    options = [x for x in v['action_context']['options'] if x['action_kind'] == kind]
    option = options[0] if options else None
    output = payload(p, kind=kind, text='The public claims need further discussion.')
    d = output['discussion']
    d.update(decision_kind=kind, option_id=None if kind == 'none' else option['option_id'])
    if p.discussion_capture.trigger.kind == 'PEER_CHAT':
        d['reaction'] = dict(trigger=probe.plain(p.discussion_capture.trigger.source),
                             reason='DIRECT_QUESTION', score=50)
    if kind == 'chat':
        output['decision']['option_id'] = option['option_id']
    elif kind == 'co_declare':
        role = option['claimed_role_ids'][0]
        output['decision'] = dict(kind=kind, option_id=option['option_id'], claimed_role_id=role,
                                  comment='I choose to make this public claim.')
        d['co_judgment'] = dict(decision='DECLARE', selected_option_id=option['option_id'], claimed_role_id=role)
    elif kind in ('vote', 'ability'):
        target = option['valid_targets'][0]
        output['decision'] = dict(kind=kind, option_id=option['option_id'])
        output['decision']['target_player_id' if kind == 'vote' else 'target_player_ids'] = target if kind == 'vote' else [target]
        if kind == 'vote':
            d['pre_vote_reassessment'] = dict(option_id=option['option_id'], ranked_target_player_ids=[target],
                preferred_target_player_id=target, evidence=[])
    if kind == 'none' and p.discussion_capture.trigger.kind == 'CO_OPPORTUNITY':
        d['co_judgment'] = dict(decision='SILENCE', selected_option_id=None, claimed_role_id=None)
    return output


@pytest.mark.parametrize('kind', ['none', 'chat', 'vote', 'ability', 'co_declare'])
def test_roundtrip_all_actions_and_trigger_controls(kind):
    p = projection('chat' if kind == 'none' else kind)
    original = legacy(p, kind)
    plan, text = probe.from_legacy(original, p)
    assert probe.validate_plan(json.dumps(plan), p) == plan
    final = probe.validate_final(plan, None if text is None else json.dumps(text), p)
    assert final == original
    assert probe.from_legacy(final, p) == (plan, text)


@pytest.mark.parametrize('case,p', recovery.entries(), ids=lambda x: getattr(x, 'case_id', 'projection'))
def test_fixed_suite_offered_branches_and_public_provenance(case, p):
    frozen = canonical_json_bytes(p.canonical_input)
    schema = probe.plan_schema(p)
    for branch in probe.p2._decision_branches(probe.plain(p.decision_schema)):
        old = fixture_legacy(p, branch)
        plan, text = probe.from_legacy(old, p)
        assert Draft202012Validator(schema).is_valid(plan)
        assert probe.validate_final(plan, None if text is None else json.dumps(text), p) == old
        if text:
            view, provenance = probe.presenter_input(plan, p)
            assert view['current'] == probe.plain(p.canonical_input['grounding']['current'])
            assert not view['intentional_disclosures']
            assert set(provenance) == set(probe.leaves(view))
            assert all(set(x) == {'source', 'origin', 'lane', 'owner_player_id', 'owner_visibility', 'channel'}
                       for x in provenance.values())
            assert all(x['owner_player_id'] == p.discussion_capture.player_id for x in provenance.values())
            assert all(x['lane'] in {'PUBLIC', 'MODEL_SELECTION'} for x in provenance.values())
            assert '/context/player_id' in {x['source'] for x in provenance.values()}
            assert not any(x['source'].startswith('/context/') and x['source'] != '/context/player_id'
                           for x in provenance.values())
    assert canonical_json_bytes(p.canonical_input) == frozen


def speech_fixture(kind):
    request = request_with(chats=_chats(1))
    if kind == 'OPINION_CHANGE':
        request = _with_assessment(request)
    p = project_brain_input(request, config=CONFIG)
    old = payload(p)
    ref = probe.plain(p.canonical_input['grounding']['allowed_evidence_refs'][0])
    choices = {
        'NONE': dict(kind=kind),
        'CLAIM': dict(kind=kind, subject_player_id='opaque-peer', topic='VOTE', stance='OPPOSE', evidence=[ref]),
        'QUESTION': dict(kind=kind, addressee_player_id='opaque-peer', subject_player_id=None, topic='VOTE', source=ref),
        'ANSWER': dict(kind=kind, addressee_player_id='opaque-peer', in_reply_to=ref, source_interpretation='QUESTION', topic='VOTE', stance='OPPOSE', evidence=[ref]),
        'REBUTTAL': dict(kind=kind, addressee_player_id='opaque-peer', in_reply_to=ref, source_interpretation='CLAIM', topic='VOTE', stance='OPPOSE', evidence=[ref]),
        'OPINION_CHANGE': dict(kind=kind, subject_player_id='opaque-peer', dimension='SUSPICION', prior=61, current=80, causes=[ref]),
        'RELATION_HYPOTHESIS': dict(kind=kind, source_player_id='opaque-self', target_player_id='opaque-peer', relation='SUPPORTS', confidence=45, evidence=[ref]),
    }
    old['discussion']['speech_act'] = choices[kind]
    return p, old, ref


@pytest.mark.parametrize('kind', list(probe.ACT_FIELDS))
def test_all_speech_acts_keep_control_scalars_and_resolve_refs(kind):
    p, old, ref = speech_fixture(kind)
    choice = old['discussion']['speech_act']
    plan, text = probe.from_legacy(old, p)
    view, _ = probe.presenter_input(plan, p)
    assert view['plan']['speech_act'] == {k: choice[k] for k in probe.ACT_FIELDS[kind]}
    assert probe.validate_final(plan, json.dumps(text), p) == old
    assert len(view['selected_evidence']) == (0 if kind == 'NONE' else 1)
    if kind != 'NONE':
        invalid = deepcopy(plan)
        key = {'CLAIM':'evidence', 'QUESTION':'source', 'ANSWER':'in_reply_to', 'REBUTTAL':'in_reply_to',
               'OPINION_CHANGE':'causes', 'RELATION_HYPOTHESIS':'evidence'}[kind]
        target = invalid['discussion']['speech_act'][key]
        (target[0] if isinstance(target, list) else target)['order'] = 999999
        with pytest.raises(ValueError):
            probe.validate_plan(json.dumps(invalid), p)


@pytest.mark.parametrize('count', [0, 1, 8])
def test_ability_catalog_zero_single_maximum_and_explicit_disclosure(count):
    results = tuple(AbilityResultRecord(i+1, 1, 'opaque-phase', 'inspect_result', 'opaque-peer', 'not_wolf')
                    for i in range(count))
    p = project_brain_input(request_with(results=results), config=CONFIG)
    cat = probe.catalog(p)
    # Projection retention may itself restrict the offered records.
    offered = len(p.canonical_input['grounding']['ability_results']['records'])
    assert len(cat['owner_ability']) == offered
    schema = probe.plan_schema(p)['properties']['disclose_fact_ids']
    assert schema['maxItems'] == min(1, offered)
    assert ('enum' in schema['items']) == bool(offered)
    plan, _ = probe.from_legacy(payload(p), p)
    empty, _ = probe.presenter_input(plan, p)
    assert empty['intentional_disclosures'] == []
    if offered:
        plan['disclose_fact_ids'] = ['a000']
        view, provenance = probe.presenter_input(plan, p)
        assert view['intentional_disclosures'][0]['value'] == probe.plain(p.canonical_input['grounding']['ability_results']['records'][0])
        edges = [edge for path, edge in provenance.items() if path.startswith('/intentional_disclosures/0/value/')]
        assert len(edges) == len(probe.ABILITY_KEYS)
        assert all(edge['lane'] == 'INTENTIONAL_OWNER_ABILITY' and edge['owner_visibility'] == 'AUTHORIZED_PRIVATE' for edge in edges)
    plan['disclose_fact_ids'] = ['a999']
    with pytest.raises(ValueError):
        probe.validate_plan(json.dumps(plan), p)


def test_private_channel_without_recipient_proof_fails_closed():
    request = semantic._request('chat', private=True, peer=True)
    p = project_brain_input(request, config=CONFIG)
    old = semantic._payload(request, 'chat')
    plan, text = probe.from_legacy(old, p)
    with pytest.raises(ValueError, match='PRIVATE_RECIPIENT_UNPROVEN'):
        probe.presenter_input(plan, p)
    with pytest.raises(ValueError, match='PRIVATE_RECIPIENT_UNPROVEN'):
        probe.validate_final(plan, json.dumps(text), p)


def test_canonical_public_chats_are_present_and_private_chats_never_ambient():
    public = _chats(8)
    private = tuple(replace(chat, order=chat.order+10) for chat in _chats(2, private=True))
    p = project_brain_input(request_with(chats=(*public, *private)), config=CONFIG)
    plan, _ = probe.from_legacy(payload(p), p)
    view, provenance = probe.presenter_input(plan, p)
    expected = [r for r in probe.plain(p.canonical_input)['memory']['records']
                if r['source']['record_kind'] == 'chat' and r['source']['visibility'] == 'PUBLIC'][-6:]
    assert expected
    assert [r['ref'] for r in view['public_chat']] == [r['source'] for r in expected]
    assert [r['text_excerpt'] for r in view['public_chat']] == [r['text_excerpt'] for r in expected]
    edges = [edge for path, edge in provenance.items() if path.startswith('/public_chat/')]
    assert edges and all(e['owner_visibility'] == 'PUBLIC' and e['channel'] == 'opaque-public' for e in edges)
    assert not any(e['owner_visibility'] == 'PRIVATE' for e in provenance.values())


def test_reply_is_retained_inside_six_chat_bound_in_canonical_order():
    eligible = [(i, {'source': {'record_kind': 'chat', 'order': i+1, 'visibility': 'PUBLIC'}}) for i in range(8)]
    oldest = eligible[0][1]['source']
    assert [item[1]['source']['order'] for item in probe._recent_chats(eligible, oldest)] == [1, 4, 5, 6, 7, 8]
    assert [item[1]['source']['order'] for item in probe._recent_chats(eligible, eligible[-1][1]['source'])] == [3, 4, 5, 6, 7, 8]


def test_explicit_owner_ability_reference_uses_disclosure_without_private_excerpt():
    p = project_brain_input(request_with(results=(AbilityResultRecord(3, 1, 'opaque-phase', 'inspect_result', 'opaque-peer', 'not_wolf'),)), config=CONFIG)
    ref = next(r for r in probe.plain(p.canonical_input)['grounding']['allowed_evidence_refs'] if r['record_kind'] == 'ability_result')
    old = payload(p)
    old['discussion']['speech_act'] = dict(kind='CLAIM', subject_player_id='opaque-peer', topic='VOTE', stance='OPPOSE', evidence=[ref])
    plan, text = probe.from_legacy(old, p, disclose_fact_ids=['a000'])
    view, provenance = probe.presenter_input(plan, p)
    assert view['intentional_disclosures'][0]['value']['result_id'] == 'not_wolf'
    assert view['selected_evidence'] == []
    assert not any(e['source'].startswith('/memory/') for e in provenance.values())
    assert probe.validate_final(plan, json.dumps(text), p) == old
    plan['disclose_fact_ids'] = []
    with pytest.raises(ValueError, match='REF_NOT_PRESENTABLE'):
        probe.presenter_input(plan, p)


def test_body_contract_and_independent_presenter_system():
    p = projection()
    body = recovery.baseline_body(p, 'qw9', 4242027)
    before = deepcopy(body)
    plan, _ = probe.from_legacy(legacy(p, 'chat'), p)
    first = probe.plan_body(body, p)
    second = probe.message_body(body, plan, p)
    assert first['messages'][:2] == before['messages']
    assert first['messages'][2]['content'] == probe.T_CONTROL+'\n'+canonical_json_bytes(probe.catalog(p)).decode()
    assert second['messages'][0] == dict(role='system', content=probe.P_SYSTEM)
    assert len(second['messages']) == 2
    assert second['messages'][1]['content'] == canonical_json_bytes(probe.presenter_input(plan, p)[0]).decode()
    assert first['max_tokens'] == 384 and second['max_tokens'] == 128
    assert body == before
    assert p.canonical_input['context']['role_id'] not in second['messages'][1]['content']
    assert p.canonical_input['context']['team'] not in second['messages'][1]['content']


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'null', 'updates', 'selection_duplicate', 'role', 'option', 'target', 'actor', 'prior'])
def test_structural_and_semantic_contract_rejects(mutation):
    p = projection('co_declare' if mutation == 'role' else 'vote' if mutation == 'target' else 'chat')
    kind = 'co_declare' if mutation == 'role' else 'vote' if mutation == 'target' else 'chat'
    plan, _ = probe.from_legacy(legacy(p, kind), p)
    if mutation == 'missing': del plan['updates_mode']
    elif mutation == 'extra': plan['extra'] = 1
    elif mutation == 'null': plan['public_fact_ids'] = None
    elif mutation == 'updates': plan['discussion']['assessment_updates'] = []
    elif mutation == 'selection_duplicate': plan['public_fact_ids'] = ['p000', 'p000']
    elif mutation == 'role': plan['decision']['claimed_role_id'] = 'not-offered'
    elif mutation == 'option': plan['decision']['option_id'] = 'not-offered'
    elif mutation == 'target': plan['decision']['target_player_id'] = 'opaque-self'
    elif mutation == 'actor': plan['discussion']['speech_act'] = dict(kind='CLAIM', subject_player_id='unknown', topic='VOTE', stance='SUPPORT', evidence=[])
    elif mutation == 'prior': plan['discussion']['base_revision'] += 1
    with pytest.raises(ValueError): probe.validate_plan(json.dumps(plan), p)


@pytest.mark.parametrize('raw', ['{}{}', '[]', '{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', 'null'])
def test_strict_json_rejects(raw):
    with pytest.raises(ValueError): probe.validate_plan(raw, projection())


def test_nontext_selection_and_illegal_none_stay_invalid():
    p = projection('vote')
    plan, _ = probe.from_legacy(legacy(p, 'vote'), p)
    plan['public_fact_ids'] = ['p000']
    with pytest.raises(ValueError, match='NON_TEXT_SELECTION'): probe.validate_plan(json.dumps(plan), p)
    with pytest.raises(ValueError): probe.from_legacy(fixture_legacy(p, 'none'), p)


@pytest.mark.parametrize('text', ['x'*201, 'x'*200, '😀'*199+'.'])
def test_text_bound_and_incomplete_ending_not_clipped(text):
    p = projection()
    plan, _ = probe.from_legacy(legacy(p, 'chat'), p)
    with pytest.raises(ValueError): probe.validate_final(plan, json.dumps({'message':text}), p)


def test_authoritative_dead_and_own_co_are_kept_without_private_attributes():
    p = project_brain_input(request_with(dead=True, claims=(CoDeclarationRecord(2,1,'opaque-phase','opaque-self','claim-a','Earlier claim.'),)), config=CONFIG)
    plan, _ = probe.from_legacy(payload(p), p)
    view, _ = probe.presenter_input(plan, p)
    assert view['current']['alive_player_ids'] == ['opaque-self']
    assert view['current']['players'][0]['death']['public_cause'] == 'executed'
    assert view['public_co'][0]['claimed_role_id'] == 'claim-a'
    assert 'role_id' not in view['current']['players'][0]


def test_updated_subjective_state_is_not_silently_emptied():
    p = projection()
    value = legacy(p, 'chat')
    value['discussion']['strategy_update'] = dict(mode='WAIT', focus_player_ids=[], evidence=[])
    with pytest.raises(ValueError): probe.from_legacy(value, p)


def test_official_converter_all_fixed_cases_and_zero_catalog():
    path = Path('logs/t444-intent-first/json_schema_to_grammar.py')
    assert recovery.base.file_hash(path) == 'ee451dc460aa31185226e58988626f64e75ab735169fa3e484fcf16889475ae3'
    spec = importlib.util.spec_from_file_location('t510_converter', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for case, p in recovery.entries():
        schema = probe.plan_schema(p)
        converter = module.SchemaConverter(prop_order={}, allow_fetch=False, dotall=False, raw_pattern=False)
        converter.visit(converter.resolve_refs(schema, 'stdin'), '')
        grammar = converter.format_grammar()
        assert 'root ::=' in grammar
        if not probe.catalog(p)['owner_ability']:
            assert schema['properties']['disclose_fact_ids']['maxItems'] == 0
