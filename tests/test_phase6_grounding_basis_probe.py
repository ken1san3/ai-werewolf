import copy
import json
from dataclasses import replace

import pytest

from ai_client.discussion.context import canonical_json_bytes
from scripts import phase6_grounding_basis_probe as probe
from scripts import phase6_model_comparison as base
from scripts.phase6_context_probe import provider_body, wire_bytes
from tests.test_phase6_intent_choice_probe import KINDS, projection


@pytest.fixture(scope='module')
def cases():
    return [(c, base.project(c, 'baseline')) for c in base.cases()]


def test_all_cases_catalog_is_exact_atomic_projection_without_mutation(cases):
    assert len(cases) == 32
    for _, p in cases:
        frozen = canonical_json_bytes(p.canonical_input)
        catalog = probe.fact_catalog(p)
        augmented = probe.augmented_input(p)
        assert 0 < len(catalog) <= 64
        assert [x['id'] for x in catalog] == [f'f{i:03}' for i in range(len(catalog))]
        assert len({x['pointer'] for x in catalog}) == len(catalog)
        for item in catalog:
            assert set(item) == {'id', 'pointer'}
            assert item['pointer'].startswith(('/grounding/current/', '/grounding/ability_results/records/'))
            assert probe.resolve_fact(augmented, item['pointer']) is not None
        assert augmented.pop(probe.CATALOG_KEY) == catalog
        assert canonical_json_bytes(augmented) == frozen == canonical_json_bytes(p.canonical_input)


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('count', [0, 1, 2])
def test_all_kinds_including_none_allow_same_basis_choices(kind, count):
    p = projection()
    value = {'speech_act_kind': kind, 'authoritative_fact_ids': [x['id'] for x in probe.fact_catalog(p)[:count]]}
    assert probe.validate_choice(json.dumps(value), p) == value


@pytest.mark.parametrize('raw', [
    '{}', '[]', 'null', '{"speech_act_kind":"NONE"}',
    '{"speech_act_kind":"NONE","authoritative_fact_ids":null}',
    '{"speech_act_kind":"NONE","authoritative_fact_ids":["f000","f000"]}',
    '{"speech_act_kind":"NONE","authoritative_fact_ids":["f000","f001","f002"]}',
    '{"speech_act_kind":"NONE","authoritative_fact_ids":["f999"]}',
    '{"speech_act_kind":"NONE","authoritative_fact_ids":[0]}',
    '{"speech_act_kind":"NONE","authoritative_fact_ids":[],"extra":0}',
    '{"speech_act_kind":"NONE","authoritative_fact_ids":[],"speech_act_kind":"ANSWER"}',
    '{"speech_act_kind":NaN,"authoritative_fact_ids":[]}',
    '{"speech_act_kind":Infinity,"authoritative_fact_ids":[]}',
    '{"speech_act_kind":1e999,"authoritative_fact_ids":[]}',
])
def test_strict_choice_rejects_invalid_without_repair(raw):
    with pytest.raises(ValueError):
        probe.validate_choice(raw, projection())


@pytest.mark.parametrize('mutation', ['order', 'duplicate', 'unknown', 'private', 'index', 'value', 'extra'])
def test_augmented_input_and_catalog_tampering_fail_closed(mutation):
    p = projection(); value = probe.augmented_input(p); catalog = value[probe.CATALOG_KEY]
    if mutation == 'order': catalog.reverse()
    elif mutation == 'duplicate': catalog.append(copy.deepcopy(catalog[0]))
    elif mutation == 'unknown': catalog[0]['id'] = 'unknown'
    elif mutation == 'private': catalog[0]['pointer'] = '/state/assessments/0'
    elif mutation == 'index': catalog[0]['pointer'] = '/grounding/current/players/999/alive'
    elif mutation == 'value': value['grounding']['current']['day'] = 999
    else: catalog[0]['value'] = 'copied'
    with pytest.raises(ValueError, match='GROUNDING_CATALOG_INVALID'):
        probe.validate_augmented(p, value)


def test_empty_catalog_and_maximum_do_not_change_kind_contract(monkeypatch):
    p = projection(); original = json.loads(canonical_json_bytes(p.canonical_input))
    original['grounding']['current'] = {}
    original['grounding']['ability_results']['records'] = []
    monkeypatch.setattr(probe, '_input', lambda _p: copy.deepcopy(original))
    empty = p
    assert probe.fact_catalog(empty) == []
    for kind in KINDS:
        assert probe.validate_choice(json.dumps({'speech_act_kind':kind,'authoritative_fact_ids':[]}), empty)
    original['grounding']['current']['alive_player_ids'] = ['opaque'] * 64
    maximum = p
    assert len(probe.fact_catalog(maximum)) == 64
    assert probe.fact_catalog(maximum)[-1]['id'] == 'f063'
    original['grounding']['current']['alive_player_ids'].append('overflow')
    with pytest.raises(ValueError, match='GROUNDING_CATALOG_INVALID'):
        probe.fact_catalog(p)


@pytest.mark.parametrize('bad', [{}, [], ['value']])
def test_non_atomic_fact_rejected(bad, monkeypatch):
    p=projection(); value=json.loads(canonical_json_bytes(p.canonical_input))
    value['grounding']['current']['day']=bad
    monkeypatch.setattr(probe, '_input', lambda _p: copy.deepcopy(value))
    with pytest.raises(ValueError, match='GROUNDING_CATALOG_INVALID'):
        probe.fact_catalog(p)


def test_all_224_kind_locked_schemas_equal_existing_c5(cases):
    no_legal = 0
    for _, p in cases:
        original = base.body_for(p, base.PROFILES['qw9'][0]); frozen = wire_bytes(original)
        first = probe.choice_body(original, p)
        for kind in KINDS:
            value={'speech_act_kind':kind,'authoritative_fact_ids':['f000','f001']}
            try:
                body = probe.output_body(original, value, p)
            except probe.gc2.NoLegalGrounding:
                no_legal += 1
                with pytest.raises(probe.gc2.NoLegalGrounding):
                    probe.gc2.candidate_schema(p, {'speech_act_kind':kind})
                continue
            assert body['response_format']['json_schema']['schema'] == probe.gc2.candidate_schema(p, {'speech_act_kind':kind})
            assert [m['role'] for m in body['messages']] == ['system','user']
            assert first['messages'][1] == body['messages'][1]
            assert body['messages'][0]['content'] == original['messages'][0]['content']+'\n\n'+probe.OUTPUT_INSTRUCTION+'\n'+canonical_json_bytes(value).decode()
            assert wire_bytes(original) == frozen
        probe.validate_augmented(p, json.loads(first['messages'][1]['content']))
    assert no_legal == 3


@pytest.mark.parametrize('stage',['choice','output'])
def test_native_exact_bytes_and_mutation_rejection(stage):
    p=projection(); original=provider_body(p)
    value={'speech_act_kind':'NONE','authoritative_fact_ids':['f000']}
    body=probe.choice_body(original,p) if stage=='choice' else probe.output_body(original,value,p)
    rendered=''.join(x['content'] for x in body['messages'])
    probe.validate_native_rendered(body,stage,rendered)
    for changed in ('missing',rendered+body['messages'][1]['content']):
        with pytest.raises(ValueError): probe.validate_native_rendered(body,stage,changed)
    bad=copy.deepcopy(body); bad['max_tokens']+=1
    with pytest.raises(ValueError): probe.validate_native_rendered(bad,stage,rendered)
