"""GB1 native prompt evidence and fail-before-consumption boundaries; no server."""
import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import phase6_model_comparison as base
from scripts import phase6_two_stage_probe_runtime as runtime
from tests.test_phase6_intent_choice_probe import projection


@pytest.fixture(scope="module")
def canonical():
    p = projection('chat')
    return p, base.body_for(p, base.PROFILES['qw9'][0])


def setup_stage(monkeypatch, canonical, name, failure=None):
    from scripts import phase6_grounding_basis_probe as probe
    p, original = canonical
    body = (probe.choice_body(original, p) if name == 'choice' else
            probe.output_body(original, {'speech_act_kind':'NONE','authoritative_fact_ids':['f000']}, p))
    row = {'case_id':'G01-1', 'choice_provider_calls':0, 'output_provider_calls':0,
           'new_provider_calls':0, 'completion_tokens':0, 'provider_prompt_tokens':0}
    if name == 'output':
        row.update(choice_provider_calls=1, choice={'status':'GENERATED'})
    monkeypatch.setattr(base, '_RUN_DEADLINE', runtime.time.monotonic()+1000)
    endpoints = []
    expected = '\n'.join(item['content'] for item in body['messages'])
    def request(endpoint, data=None, **kwargs):
        endpoints.append((endpoint, data, kwargs))
        if endpoint == '/apply-template':
            if failure == 'http':
                raise TimeoutError('PRIVATE_EXCEPTION')
            rendered = expected
            if failure == 'missing_user': rendered = body['messages'][0]['content']
            if failure == 'duplicate_control': rendered += probe.CHOICE_INSTRUCTION if name == 'choice' else probe.OUTPUT_INSTRUCTION
            if failure == 'type' or (failure == 'bare_type' and 'response_format' not in data): rendered = None
            return {'prompt':rendered}
        if endpoint == '/tokenize':
            return {'tokens': [1]*(8192 if failure == 'overflow' else 100) if failure != 'token_type' else [True]}
        assert endpoint == '/v1/chat/completions'
        return {'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':100,'completion_tokens':2}}
    monkeypatch.setattr(base, 'request', request)
    return body, row, endpoints, expected


@pytest.mark.parametrize('name', ['choice','output'])
def test_exact_native_evidence_preserves_wire_and_distinct_digest_domains(monkeypatch,tmp_path,canonical,name):
    body,row,endpoints,rendered = setup_stage(monkeypatch,canonical,name)
    budget = {'calls':0}
    with (tmp_path/'raw.jsonl').open('w',encoding='utf-8') as raw:
        assert runtime.stage(name,body,row,tmp_path,raw,lambda:None,budget,contract=runtime.GB1_CONTRACT) == '{}'
    assert [e[0] for e in endpoints] == ['/apply-template','/tokenize','/apply-template','/v1/chat/completions']
    saved = (tmp_path/f'G01-1.{name}.rendered.bin').read_bytes()
    assert saved == rendered.encode('utf-8')
    stage = row[name]
    assert stage['rendered_prompt_sha256'] == base.digest(rendered)
    assert stage['rendered_prompt_utf8_sha256'] == hashlib.sha256(saved).hexdigest()
    assert stage['rendered_prompt_utf8_sha256'] != stage['rendered_prompt_sha256']
    assert stage['rendered_prompt_utf8_bytes'] == len(saved)
    wire = (tmp_path/f'G01-1.{name}.request.bin').read_bytes()
    assert wire == endpoints[0][2]['wire_payload'] == endpoints[-1][2]['wire_payload']
    consumed=json.loads((tmp_path/f'G01-1.{name}.consumed.json').read_text(encoding='utf-8'))
    assert consumed['rendered_prompt_utf8_sha256'] == stage['rendered_prompt_utf8_sha256']
    assert row[name+'_provider_calls'] == budget['calls'] == 1
    assert rendered not in json.dumps(row)
    assert body['messages'][1]['content'] not in json.dumps(row)


@pytest.mark.parametrize('name', ['choice','output'])
@pytest.mark.parametrize('failure', ['http','missing_user','duplicate_control','type','bare_type','token_type','overflow','save','readback','sink_not_called'])
def test_native_failure_consumes_no_generation_and_leaks_no_private_detail(monkeypatch,tmp_path,canonical,name,failure):
    body,row,endpoints,_ = setup_stage(monkeypatch,canonical,name,failure)
    if failure == 'save':
        old=Path.open
        def denied(path,*args,**kwargs):
            if path.name.endswith('.rendered.bin'): raise PermissionError('PRIVATE_PATH')
            return old(path,*args,**kwargs)
        monkeypatch.setattr(Path,'open',denied)
    if failure == 'readback':
        old=Path.read_bytes
        monkeypatch.setattr(Path,'read_bytes',lambda path:b'corrupted' if path.name.endswith('.rendered.bin') else old(path))
    if failure == 'sink_not_called':
        monkeypatch.setattr(base,'count_prompt',lambda *a,**k:{'prompt_tokens_actual':100})
    budget={'calls':0}
    reason='CONTEXT_OVERFLOW' if failure=='overflow' else 'TEMPLATE_INVALID'
    with pytest.raises(base.StopComparison,match='^'+reason+'$'):
        runtime.stage(name,body,row,tmp_path,io.StringIO(),lambda:None,budget,contract=runtime.GB1_CONTRACT)
    assert budget['calls']==row[name+'_provider_calls']==0
    assert not list(tmp_path.glob('*.consumed.json'))
    assert not any(endpoint=='/v1/chat/completions' for endpoint,_,_ in endpoints)
    assert row['status']==name.upper()+'_NOT_STARTED'
    assert row['error_kind']==reason
    assert 'PRIVATE' not in json.dumps(row)


def test_optional_sink_default_keeps_exact_old_result_and_three_requests(monkeypatch):
    body={'max_tokens':32,'response_format':{},'messages':[]}
    events=[]
    def request(endpoint,data,**kwargs):
        events.append((endpoint,data,kwargs))
        return {'tokens':[1,2]} if endpoint=='/tokenize' else {'prompt':'native ü'}
    monkeypatch.setattr(base,'request',request)
    old=base.count_prompt(body,wire_payload=b'wire')
    first=list(events);events.clear();received=[]
    new=base.count_prompt(body,wire_payload=b'wire',private_sink=received.append)
    assert old==new=={'prompt_tokens_actual':2,'rendered_prompt_sha256':base.digest('native ü'),
        'schema_changes_rendered_prompt':False,'remaining_context_tokens':8157}
    assert events==first and len(events)==3 and received==['native ü']


@pytest.mark.parametrize('change', [{'task':'T470'},{'max_provider_calls':65},{'experiment':'unknown'},
    {'stages':(runtime.StageContract('choice',33,240,False),runtime.GB1_CONTRACT.stages[1])}])
def test_sc2_contract_identity_cannot_be_overridden(change):
    with pytest.raises(base.StopComparison,match='STAGE_CONTRACT'):
        runtime.validate_contract(replace(runtime.GB1_CONTRACT,**change))
