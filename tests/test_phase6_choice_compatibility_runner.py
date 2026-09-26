import copy
import json
from types import SimpleNamespace

import pytest

from scripts import phase6_choice_compatibility_runner as runner
from tests.test_phase6_intent_choice_probe import projection
from tests.test_phase6_quality_grounding import payload, request_with

CHOICE = json.dumps(dict(speech_act_kind='NONE', authoritative_fact_ids=[]))


@pytest.fixture
def scene():
    p = projection()
    return SimpleNamespace(case_id='G01-1', request=request_with()), p, json.dumps(payload(p, text='I need public voting reasons before deciding.'))


def sender(replies, calls):
    def dispatch(stage, body):
        calls.append((stage, copy.deepcopy(body)))
        raw, finish = next(replies)
        return raw, dict(consumed=True, finish_reason=finish, status='GENERATED', wire_sha256=runner.r.sha(runner.r.wire_bytes(body)))
    return dispatch


@pytest.mark.parametrize('raw,finish,status', [(CHOICE,'length','CHOICE_LENGTH'),(None,None,'CHOICE_UNKNOWN'),
    ('','stop','CHOICE_EMPTY'),('{','stop','CHOICE_JSON_INVALID'),('{}','stop','CHOICE_SCHEMA_INVALID'),
    ('{"speech_act_kind":"NONE","authoritative_fact_ids":["f999"]}','stop','CHOICE_SCHEMA_INVALID'),
    ('{"speech_act_kind":"NONE","authoritative_fact_ids":["f000","f000"]}','stop','CHOICE_SCHEMA_INVALID'),
    ('{"speech_act_kind":"NONE","speech_act_kind":"ANSWER"}','stop','CHOICE_JSON_INVALID'),
    ('{"speech_act_kind":NaN}','stop','CHOICE_JSON_INVALID')])
def test_bad_choice_single_call_no_final_no_semantic_credit(scene, raw, finish, status):
    case,p,_ = scene; calls=[]
    row, final = runner.process_case(case,p,sender(iter([(raw,finish)]),calls))
    assert row['choice_status'] == status and row['provider_calls'] == len(calls) == 1
    assert final is None and row['final_output_sha256'] is None and not row['structural_pass']
    assert row['content_status']=='UNKNOWN' and row['semantic_outcome']==0


@pytest.mark.parametrize('bad_count',range(4))
@pytest.mark.parametrize('reason',['length','stop'])
def test_output_k_length_and_structural_rejection_with_exact_choice_binding(scene,bad_count,reason):
    case,p,raw=scene; calls=[]
    bad=raw if reason=='length' else '{}'
    replies=[(CHOICE,'stop'),*[(bad,reason)]*bad_count,(raw,'stop')]
    row,final=runner.process_case(case,p,sender(iter(replies),calls))
    assert len(calls)==row['provider_calls']==1+min(3,bad_count+1)
    assert row['status']==('ACCEPTED' if bad_count<3 else 'OUTPUT_K_EXHAUSTED')
    assert final==(raw if bad_count<3 else None)
    assert row['structural_pass']==(bad_count<3)
    assert all(a['choice_sha256']==runner.r.sha(CHOICE.encode()) for a in row['attempts'][1:])
    original=runner.r.baseline_body(p,'gm12',runner.SEED)
    expected=runner.gb.choice_body(original,p); expected['max_tokens']=64
    assert calls[0][1]==expected
    for i,(_,body) in enumerate(calls[1:]):
        expected=runner.gb.output_body(original,json.loads(CHOICE),p)
        expected.update(seed=runner.SEED+1009*i,max_tokens=448)
        assert body==expected


def test_unknown_after_rejected_output_never_accepts_previous(scene):
    case,p,raw=scene; calls=[]
    row,final=runner.process_case(case,p,sender(iter([(CHOICE,'stop'),(raw,'length'),(None,None)]),calls))
    assert row['status']=='OUTPUT_UNKNOWN' and final is None and row['final_output_sha256'] is None
    assert row['provider_calls']==3 and not row['structural_pass']


@pytest.mark.parametrize('stage',['choice','output'])
def test_native_binding_variant_accepts_exact_budget_only(scene,stage):
    _,p,_=scene
    original=runner.r.baseline_body(p,'gm12',runner.SEED)
    body=runner.gb.choice_body(original,p) if stage=='choice' else runner.gb.output_body(original,json.loads(CHOICE),p)
    rendered='\n'.join(m['content'] for m in body['messages'])
    runner.gb.validate_native_rendered(body,stage,rendered)
    with pytest.raises(ValueError):
        runner.gb.validate_native_rendered(body,stage,rendered,budget_variant=runner.VARIANT)
    body['max_tokens']=64 if stage=='choice' else 448
    runner.gb.validate_native_rendered(body,stage,rendered,budget_variant=runner.VARIANT)
    for wrong in [rendered+rendered, rendered[:-10]]:
        with pytest.raises(ValueError): runner.gb.validate_native_rendered(body,stage,wrong,budget_variant=runner.VARIANT)
    with pytest.raises(ValueError): runner.gb.validate_native_rendered(body,stage,rendered)
    with pytest.raises(ValueError): runner.gb.validate_native_rendered(body,stage,rendered,budget_variant='unknown')


def test_shared_durable_cap_is_128_not_legacy_864(tmp_path):
    (tmp_path/'calls').mkdir()
    for n in range(128): runner.r.reserve(tmp_path,str(n),{'seed':n},call_cap=128)
    with pytest.raises(runner.r.Stop,match='CALL_CAP'): runner.r.reserve(tmp_path,'extra',{'seed':128},call_cap=128)
    assert len(list((tmp_path/'calls').iterdir()))==128


def test_stage_budget_checked_before_any_transport(tmp_path,monkeypatch):
    monkeypatch.setattr(runner.r,'execute_call',lambda *a,**k:pytest.fail('sent'))
    with pytest.raises(runner.r.Stop,match='BUDGET_VARIANT'):
        runner.execute_call(tmp_path,tmp_path,'c','choice',{'max_tokens':32})


def test_nonowned_listener_never_launched_or_stopped(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'verify',lambda *a,**k:{'deadline_utc':'2099-01-01T00:00:00+00:00'})
    monkeypatch.setattr(runner.base,'port_free',lambda:False)
    monkeypatch.setattr(runner.subprocess,'Popen',lambda *a,**k:pytest.fail('touched non-owner'))
    with pytest.raises(runner.r.Stop,match='NON_OWNED_LISTENER'):runner.run_block(tmp_path)


@pytest.mark.parametrize('existing',['claim.json','outer'])
def test_one_time_run_claim_blocks_resumption(tmp_path,monkeypatch,existing):
    monkeypatch.setattr(runner,'verify',lambda *a,**k:{})
    (tmp_path/existing).touch()
    with pytest.raises(runner.r.Stop,match='RUN_ALREADY_CLAIMED'):runner.supervised_run(tmp_path)


@pytest.mark.parametrize('failure',['choice_length','all_success','orphan_marker'])
def test_complete_fake_lifecycle_keeps_32_and_cleans_only_objects(tmp_path,monkeypatch,scene,failure):
    case,p,raw=scene
    entries=[(SimpleNamespace(case_id=f'G{(n//2)+1:02}-{n%2+1}',request=case.request),p) for n in range(32)]
    plan={'deadline_utc':'2099-01-01T00:00:00+00:00','argv':['owned-server'],'model':{'path':'model'}}
    runner.r.write(tmp_path/'plan.json',plan); (tmp_path/'calls').mkdir()
    private=tmp_path/'private';private.mkdir()
    monkeypatch.setattr(runner,'verify',lambda *a,**k:plan)
    monkeypatch.setattr(runner.r,'entries',lambda:entries)
    monkeypatch.setattr(runner.base,'cases',lambda:[x[0] for x in entries])
    monkeypatch.setattr(runner.base,'port_free',lambda:True)
    monkeypatch.setattr(runner.base,'create_private_evidence_container',lambda *a,**k:private)
    monkeypatch.setattr(runner.base,'runtime',lambda:{'model_path':'model'})
    monkeypatch.setattr(runner.base,'safe_runtime',lambda x:{'n_ctx':8192})
    monkeypatch.setattr(runner.base,'owned_listener',lambda x:True)
    monkeypatch.setattr(runner.base,'request',lambda *a,**k:{'status':'ok'})
    monkeypatch.setattr(runner.base,'attach_performance',lambda *a:None)
    owned=[]
    def launch(*a,**k):
        obj=SimpleNamespace(poll=lambda:None);owned.append(obj);return obj
    monkeypatch.setattr(runner.subprocess,'Popen',launch)
    def clean(monitor,provider):
        assert [provider,monitor]==owned
        return {'owned_processes_remaining':0}
    monkeypatch.setattr(runner.base,'cleanup_owned',clean)
    def execute(out,secret,key,stage,body,**kwargs):
        kwargs['verify_now']()
        marker=out/'calls'/(secret.name+'-'+key+'-'+stage+'.json')
        runner.r.write(marker,{'wire_sha256':'a'*64},exclusive=True)
        if failure=='orphan_marker': raise OSError('PRIVATE_DETAIL')
        finish='length' if failure=='choice_length' and stage=='choice' else 'stop'
        return CHOICE if stage=='choice' else raw,dict(consumed=True,finish_reason=finish)
    monkeypatch.setattr(runner,'execute_call',execute)
    exitcode=runner.run_block(tmp_path)
    result=runner.r.read(tmp_path/'result.json')
    assert len(result['rows'])==32 and result['owned_processes_remaining']==0
    assert result['provider_calls']==result['durable_call_count']==(64 if failure=='all_success' else 1)
    assert result['choice_compatible']==(failure=='all_success')
    assert exitcode==(2 if failure=='orphan_marker' else 0)
    if failure!='all_success':assert all(x['status']=='NOT_RUN' for x in result['rows'][1:])
    assert 'PRIVATE_DETAIL' not in (tmp_path/'result.json').read_text()
    assert runner.base._RUN_DEADLINE is None


def offline_binding_fixture(tmp_path,monkeypatch):
    evidence=tmp_path/'evidence'; evidence.mkdir()
    frozen=evidence/'freeze.json'; runner.r.write(frozen,{'profiles':{}})
    result=evidence/'result.json'
    runner.r.write(result,{'gate':'OFFLINE_CAPACITY_CANDIDATE','freeze_sha256':runner.base.file_hash(frozen)})
    monkeypatch.setattr(runner,'EVIDENCE',result)
    monkeypatch.setattr(runner,'EVIDENCE_SHA',runner.base.file_hash(result))
    return frozen,result


def test_offline_freeze_is_bound_to_exact_measured_result(tmp_path,monkeypatch):
    frozen,result=offline_binding_fixture(tmp_path,monkeypatch)
    assert runner.offline_evidence()[1]==runner.base.file_hash(frozen)
    frozen.write_text('{"profiles":{"different":true}}',encoding='utf-8')
    with pytest.raises(runner.r.Stop,match='OFFLINE_FREEZE_BINDING'):runner.offline_evidence()
    result.write_text('{}',encoding='utf-8')
    with pytest.raises(runner.r.Stop,match='OFFLINE_BINDING'):runner.offline_evidence()


@pytest.mark.parametrize('operation',['prepare','verify','run'])
def test_changed_offline_freeze_blocks_before_process_or_request(tmp_path,monkeypatch,operation):
    frozen,_=offline_binding_fixture(tmp_path,monkeypatch)
    frozen.write_text('{"changed":true}',encoding='utf-8')
    review=tmp_path/'review.md'; review.write_text('\n`APPROVED`\n',encoding='utf-8')
    monkeypatch.setattr(runner,'REVIEW',review)
    out=tmp_path/'run';out.mkdir();runner.r.write(out/'plan.json',{})
    monkeypatch.setattr(runner.subprocess,'Popen',lambda *a,**k:pytest.fail('process launched'))
    monkeypatch.setattr(runner.base,'request',lambda *a,**k:pytest.fail('provider called'))
    with pytest.raises(runner.r.Stop,match='OFFLINE_FREEZE_BINDING'):
        if operation=='prepare':runner.prepare(out,runner.base.file_hash(review))
        elif operation=='verify':runner.verify(out,files=False)
        else:runner.supervised_run(out)


def test_frozen_sampling_cannot_advertise_different_seed_or_stage_budget():
    value=runner.contract()
    assert value['seed']==4242027 and value['choice']+value['output']==512
    assert 'seed' not in value['sampling'] and 'max_tokens' not in value['sampling']
    assert value['sampling']=={k:v for k,v in runner.base.SAMPLING.items() if k not in ('seed','max_tokens')}
