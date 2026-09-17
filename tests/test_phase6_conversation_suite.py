"""Offline checks for finite synthetic fixtures, strict parsing and safe counts."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path

import pytest

from scripts.phase6_conversation_suite import cases, project, example, evaluate
from scripts.phase6_context_probe import provider_body, wire_bytes
from ai_client.discussion.context import canonical_json_bytes
from ai_client.llm.backend import OpenAICompatibleBackend
from ai_client.llm.types import (OpenAICompatibleBackendConfig, GenerationSettings,
    LlamaCppStructuredOutputConfig, StructuredGenerationRequest)
from tests.test_phase6_quality_grounding import payload


def test_suite_coverage_determinism_and_projected_critical_facts():
    first, second = cases(), cases()
    assert len(first) == 32 and len({c.category for c in first}) == 16
    assert len({c.case_id for c in first}) == 32
    for a,b in zip(first,second):
        p,q=project(a,'baseline'),project(b,'baseline')
        assert p.prompt_sha256 == q.prompt_sha256
        assert a.request.snapshot.self_view.player_id == 'player-6'
        assert len(a.request.snapshot.players) == 9
        if a.case_id.startswith(('G04','G05','G06','G15')):
            assert p.canonical_input['grounding']['ability_results']['records']
        else:
            assert not p.canonical_input['grounding']['ability_results']['records']
        if a.case_id.startswith('G10'):
            assert p.canonical_input['grounding']['self_co']['records'][0]['claimed_role_id']=='seer'
        if a.case_id.startswith('G07'):
            assert 'player-2' in p.canonical_input['grounding']['current']['alive_player_ids']
            assert 'player-5' not in p.canonical_input['grounding']['current']['alive_player_ids']
        if a.case_id == 'G04-1':
            assert p.canonical_input['state']['assessments'][0]['suspicion']==80
        if a.case_id == 'G04-2': assert not p.canonical_input['state']['assessments']
        if a.request.discussion.trigger.source:
            assert any(r['source']['order']==a.request.discussion.trigger.source.order
                       for r in p.canonical_input['memory']['records'])


def test_candidate_changes_only_system_and_valid_examples():
    for c in cases():
        a,b=project(c,'baseline'),project(c,'candidate')
        assert a.messages[1:]==b.messages[1:]
        assert a.canonical_input==b.canonical_input
        assert a.decision_schema==b.decision_schema
        assert b.prompt_bytes+454<=32768
        if c.request.discussion.trigger.kind in ('PRE_VOTE','CO_OPPORTUNITY'):
            assert a==b


def test_structure_reference_has_all_acts_and_no_speech_example():
    from scripts.phase6_conversation_suite import structure_reference
    import jsonschema
    for c in cases():
        a,b=project(c,'baseline'),project(c,'structure')
        assert a.messages[1:]==b.messages[1:] and a.decision_schema==b.decision_schema
        assert a.canonical_input==b.canonical_input and b.prompt_bytes+454<=32768
        fragment=structure_reference(a)
        jsonschema.Draft202012Validator.check_schema(fragment)
        assert set(x['properties']['kind']['const'] for x in fragment['$defs']['speech_act']['oneOf'])=={
            'NONE','CLAIM','QUESTION','ANSWER','REBUTTAL','OPINION_CHANGE','RELATION_HYPOTHESIS'}
        original=json.loads(canonical_json_bytes(a.decision_schema))
        assert all(value==original['$defs'][key] for key,value in fragment['$defs'].items())
        assert 'message' not in json.dumps(fragment) and 'comment' not in json.dumps(fragment)
        item=example(a.canonical_input)
        if item:jsonschema.validate(item['discussion']['speech_act'],fragment)
        if c.request.discussion.trigger.kind in ('PRE_VOTE','CO_OPPORTUNITY'):assert a==b
        else:assert a.messages[0]!=b.messages[0]


def test_unknown_probe_variant_is_not_silently_a_different_experiment():
    with pytest.raises(ValueError,match='unknown variant'):project(cases()[0],'typo')


@pytest.mark.parametrize('changed',[False,True],ids=['reuse','changed_bytes'])
def test_structure_preparation_reuses_and_locks_historical_baseline(monkeypatch,tmp_path,changed):
    import hashlib
    from scripts import phase6_conversation_suite as m
    case=cases()[0]; p=project(case,'baseline')
    baseline={'input_hash':hashlib.sha256(wire_bytes(provider_body(p))).hexdigest(),
        'remaining_tokens':100,'provider_request_bytes':100}
    if changed:baseline['input_hash']='0'*64
    source=tmp_path/'prior';source.mkdir();meta={'context_per_slot':8192}
    (source/'plan.json').write_text(json.dumps({'runtime':meta,'cases':[{'case_id':case.case_id,'variants':{'baseline':baseline}}]}))
    for name in ('baseline-results.json','baseline-locator.json','baseline-manual.json'):
        (source/name).write_text('{}')
    calls=[]
    monkeypatch.setattr(m,'runtime',lambda:meta)
    monkeypatch.setattr(m,'cases',lambda:(case,))
    monkeypatch.setattr(m,'measure_body',lambda *a,**kw: calls.append('count') or dict(baseline))
    out=tmp_path/'new'
    if changed:
        with pytest.raises(RuntimeError,match='baseline bytes changed'):
            m.prepare(out,candidate_variant='structure',baseline_source=source)
        assert not calls and not out.exists()
    else:
        m.prepare(out,candidate_variant='structure',baseline_source=source)
        assert calls==['count'] and (out/'baseline.claim').exists()
        assert (out/'baseline-results.json').read_bytes()==(source/'baseline-results.json').read_bytes()


def test_exact_payload_matches_real_backend():
    async def check():
        backend=OpenAICompatibleBackend(OpenAICompatibleBackendConfig(
            endpoint='http://127.0.0.1:8080/v1/chat/completions',model='Qwen3.5-9B-Q4_K_M.gguf',
            generation=GenerationSettings(max_output_tokens=512),
            llama_cpp_structured_output=LlamaCppStructuredOutputConfig()))
        try:
            for c in (cases()[0],cases()[24],cases()[28]):
                p=project(c,'baseline')
                request=StructuredGenerationRequest('synthetic',p.messages,p.decision_schema)
                assert json.loads(backend._request_payload(request))==provider_body(p)
        finally: await backend.aclose()
    asyncio.run(check())


def test_evaluation_never_infers_text_truth_from_valid_structure():
    c=cases()[26];p=project(c,'baseline')
    value=payload(p,text='I have no new information.')
    result=evaluate(c,p,json.dumps(value))
    assert result['structural_pass']
    assert result['hard_pass'] is None and result['semantic_pass'] is None
    assert result['manual_text_review_required']
    value['discussion']['option_id']='made-up'
    bad=evaluate(c,p,json.dumps(value))
    assert not bad['structural_pass'] and bad['hard_pass'] is False


@pytest.mark.parametrize('raw',['null','[]','"x"','{','{"decision":null,"discussion":null}'])
def test_invalid_output_is_not_a_harness_crash(raw):
    c=cases()[0]
    assert evaluate(c,project(c,'baseline'),raw)['hard_pass'] is False


def test_copy_guard_does_not_reject_short_agreement():
    c=cases()[22];p=project(c,'baseline')
    text=c.request.history.records[-1].message
    assert evaluate(c,p,json.dumps(payload(p,text=text)))['exact_long_copy']
    assert not evaluate(c,p,json.dumps(payload(p,text='I agree.')))['exact_long_copy']


def test_vote_targets_and_no_privacy_leak_from_other_cases():
    c=cases()[24];p=project(c,'baseline')
    option=p.canonical_input['action_context']['options'][0]
    assert set(option['valid_targets'])=={'player-2','player-4','player-7'}
    assert not p.canonical_input['grounding']['ability_results']['records']


def test_context_measurements_are_provider_identity_bound(monkeypatch):
    from scripts import phase6_context_probe as m
    props={'build_info':'b10697-093adb242','model_path':'Qwen3.5-9B-Q4_K_M.gguf',
        'default_generation_settings':{'n_ctx':8192},'chat_template':'x'}
    monkeypatch.setattr(m,'request',lambda path: props if path=='/props' else [{'n_ctx':4096,'is_processing':False}])
    with pytest.raises(RuntimeError,match='ambiguous'):m.runtime()
    props['build_info']='different'
    with pytest.raises(RuntimeError,match='unverified'):m.runtime()


@pytest.mark.parametrize('bad',[None,'123',[1,-1],[True],{'tokens':[]}])
def test_bad_tokenizer_count_fails_closed(monkeypatch,bad):
    from scripts import phase6_context_probe as m
    monkeypatch.setattr(m,'request',lambda *a,**k:{'tokens':bad})
    with pytest.raises(RuntimeError,match='invalid tokenizer'):m.tokens('x')


def test_safe_aggregate_excludes_reused_cost_and_preserves_unknown():
    from scripts.phase6_suite_report import summarize
    new={'case_id':'G01-1','new_provider_calls':1,'provider_prompt_tokens':100,'completion_tokens':20,
         'hard_pass':None,'semantic_pass':None,'grounding_result':'NOT_APPLICABLE'}
    result=summarize([new,{**new,'new_provider_calls':0}])
    assert result['prompt_tokens_new_calls']==100 and result['completion_tokens_new_calls']==20
    assert result['hard']=={'UNKNOWN':2} and result['semantic']=={'UNKNOWN':2}


def test_measurement_counts_concrete_requests_and_reserves_output(monkeypatch):
    from scripts import phase6_context_probe as m
    monkeypatch.setattr(m,'template',lambda body:'x'*(20 if len(body['messages'])>1 else 10))
    monkeypatch.setattr(m,'tokens',lambda text,**kw:len(text))
    body={'messages':[{'role':'user','content':'hi'}], 'max_tokens':512,
        'response_format':{'json_schema':{'schema':{}}}}
    result=m.measure_body(body,533)
    assert result['base_prompt_tokens']==10 and result['repair_prompt_tokens']==20
    assert result['worst_case_total']==533 and result['remaining_tokens']==0


@pytest.mark.parametrize('raw',[b'{}',b'x'*65537,b'\xff'],ids=['valid','oversize','invalid_utf8'])
def test_local_http_is_bounded_and_never_uses_proxy_or_redirect(monkeypatch,raw):
    from scripts import phase6_context_probe as m
    from types import SimpleNamespace
    handlers=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):
            assert size==65537
            return raw
    def build(*items):
        handlers.extend(items)
        return SimpleNamespace(open=lambda req,timeout:Response())
    monkeypatch.setattr(m.urllib.request,'build_opener',build)
    if raw==b'{}':assert m.request('/props')=={}
    else:
        with pytest.raises((RuntimeError,UnicodeDecodeError)):m.request('/props')
    assert handlers[0].proxies=={}
    with pytest.raises(RuntimeError,match='redirect forbidden'):
        handlers[1].redirect_request(None,None,307,'',{},'https://elsewhere.invalid')
    with pytest.raises(ValueError,match='unsupported'):m.request('/unexpected')
