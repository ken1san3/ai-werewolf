"""GC2 replay integrity and finite one-output ownership tests, no provider."""
import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from scripts import phase6_grounding_closed_runner as runner
from scripts import phase6_grounding_closed_replay as replay
from scripts import phase6_intent_choice_runner as ic2
from scripts import phase6_two_stage_probe_runtime as runtime
from tests.test_phase6_intent_choice_runner import ready as ready_ic2
from tests.test_phase6_intent_choice_probe import projection
from scripts.phase6_context_probe import wire_bytes


def ready(monkeypatch, path, *, rows=1, failure=None):
    calls, children, payloads, private = ready_ic2(monkeypatch, path, rows=rows, failure=failure)
    plan = json.loads((path/'plan.json').read_text())
    plan['replay'] = {'artifacts':{}}
    (path/'plan.json').write_text(json.dumps(plan))
    profile, pairs = ic2.verify(plan)
    wrapped = [(c, runner.ReplayedProjection(p, {'speech_act_kind':'NONE'},
        {'choice_raw_sha256':'a'*64, 'choice_wire_sha256':'b'*64})) for c,p in pairs]
    monkeypatch.setattr(runner, 'verify', lambda plan: (profile, wrapped))
    monkeypatch.setattr(runner, 'final_source', lambda plan: {'source_unchanged':True, 'config_unchanged':True})
    return calls, children, payloads, private


def test_reused_choice_never_dispatches_or_enters_new_usage(monkeypatch, tmp_path):
    calls, children, payloads, private = ready(monkeypatch,tmp_path,rows=32)
    assert runner.run(tmp_path) == 0
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    assert calls == ['output']*32
    assert result['new_provider_calls'] == result['new_output_calls'] == 32
    assert result['reused_provider_calls'] == result['reused_choice_count'] == 32
    assert len(result['rows']) == 32
    assert all(row['choice_provider_calls'] == 0 and row['choice']['status'] == 'REUSED' for row in result['rows'])
    assert all(row['completion_tokens'] == 200 and row['provider_prompt_tokens'] == 100 for row in result['rows'])
    assert not list(private.glob('*.choice.*'))
    assert result['owned_processes_remaining'] == 0 and all(p.poll() == 0 for p in children)
    assert all(row['status'] == 'COMPLETE' for row in result['rows'])
    with pytest.raises(runtime.base.StopComparison,match='RESULT_EXISTS'):
        runner.run(tmp_path)
    assert len(calls) == 32


@pytest.mark.parametrize('failure,status', [('output','OUTPUT_INVALID'),('output_length','OUTPUT_ERROR'),
    ('kind','OUTPUT_INVALID'),('null_discussion','OUTPUT_INVALID'),('timeout','OUTPUT_ERROR')])
def test_bad_output_never_retries_or_creates_final(monkeypatch,tmp_path,failure,status):
    calls,children,_,private = ready(monkeypatch,tmp_path,failure=failure)
    assert runner.run(tmp_path) == 0
    result=json.loads((tmp_path/'qw9-results.json').read_text())
    assert calls == ['output'] and result['rows'][0]['status'] == status
    assert all(json.loads(line)['stage'] != 'final' for line in (private/'raw.jsonl').read_text().splitlines())
    assert all(child.poll() == 0 for child in children)
    assert 'private detail' not in json.dumps(result)


def test_no_legal_grounding_preserves_all_rows_without_dispatch(monkeypatch,tmp_path):
    calls,children,_,private=ready(monkeypatch,tmp_path,rows=32)
    def fail(*args): raise runner.probe.NoLegalGrounding()
    monkeypatch.setattr(runner.probe,'output_body',fail)
    assert runner.run(tmp_path) == 0
    result=json.loads((tmp_path/'qw9-results.json').read_text())
    assert len(result['rows']) == 32 and calls == []
    assert all(row['status'] == 'NO_LEGAL_GROUNDING' for row in result['rows'])
    assert not list(private.glob('*.request.bin'))
    assert result['new_provider_calls'] == 0 and result['owned_processes_remaining'] == 0


def test_preflight_mismatch_happens_before_claim_private_or_process(monkeypatch,tmp_path):
    (tmp_path/'plan.json').write_text('{}')
    def fail(*args): raise runtime.base.StopComparison('REPLAY_BINDING_MISMATCH')
    monkeypatch.setattr(runner,'verify',fail)
    monkeypatch.setattr(runtime.base,'claim_run',lambda *a:pytest.fail('claim'))
    monkeypatch.setattr(runtime.subprocess,'Popen',lambda *a,**k:pytest.fail('process'))
    with pytest.raises(runtime.base.StopComparison,match='REPLAY_BINDING_MISMATCH'):
        runner.run(tmp_path)
    assert list(tmp_path.iterdir()) == [tmp_path/'plan.json']


@pytest.fixture(scope='module')
def replay_fixture():
    p=projection();case=SimpleNamespace(case_id='G01-1',category='fixed')
    original=ic2.base.body_for(p,ic2.base.PROFILES['qw9'][0]);body=ic2.probe.choice_body(original)
    raw='{"speech_act_kind":"NONE"}';choice=ic2.probe.validate_choice(raw,p)
    wire=wire_bytes(body);sha=hashlib.sha256(wire).hexdigest();rawsha=hashlib.sha256(raw.encode()).hexdigest()
    binding=ic2.choice_binding(case,p,original,body,raw,choice)
    row={'case_id':case.case_id,'selected_kind':'NONE','baseline_input_sha256':ic2.base.digest(original),
        'projection_sha256':p.prompt_sha256,'binding':binding,
        'choice':{'wire_sha256':sha,'input_sha256':ic2.base.digest(body),'status':'GENERATED',
                  'final_output_sha256':rawsha,'finish_reason':'stop'}}
    safe={key:row[key] for key in ('case_id','selected_kind','baseline_input_sha256','projection_sha256')}
    safe.update(choice_wire_sha256=sha,choice_output_sha256=rawsha)
    record={'case_id':case.case_id,'stage':'choice','input':body,'wire_sha256':sha,'final_content':raw,'finish_reason':'stop'}
    consumed={'case_id':case.case_id,'stage':'choice','wire_sha256':sha,'input_sha256':ic2.base.digest(body),
              'max_tokens':32,'status':'STARTED'}
    return case,p,ic2.entry(case,p),row,safe,record,wire,consumed


def test_replay_exact_positive(replay_fixture):
    choice,binding=replay.bind_case(*replay_fixture)
    assert choice == {'speech_act_kind':'NONE'} and binding == replay_fixture[3]['binding']


@pytest.mark.parametrize('target,field',[(2,'projection_sha256'),(3,'case_id'),(4,'selected_kind'),
    (4,'baseline_input_sha256'),(4,'choice_output_sha256'),(5,'stage'),(5,'case_id'),
    (5,'final_content'),(5,'wire_sha256'),(7,'max_tokens'),(7,'input_sha256'),(7,'status')])
def test_replay_rejects_each_binding_mismatch(replay_fixture,target,field):
    args=list(replay_fixture)
    args[target]=copy.deepcopy(args[target]);args[target][field]='CORRUPTED'
    with pytest.raises((runtime.base.StopComparison,ValueError)):
        replay.bind_case(*args)


def test_replay_rejects_wire_bytes_even_if_json_equivalent(replay_fixture):
    args=list(replay_fixture);args[6]=args[6]+b' '
    with pytest.raises(runtime.base.StopComparison,match='REPLAY_BINDING_MISMATCH'):
        replay.bind_case(*args)


@pytest.mark.parametrize('change',[{'max_provider_calls':33},{'request_seconds':61},{'stages':runtime.IC2_CONTRACT.stages}])
def test_gc2_contract_rejects_expansion_before_claim(tmp_path,change):
    with pytest.raises(runtime.base.StopComparison,match='STAGE_CONTRACT'):
        runtime.run_replayed_choice_output_probe(tmp_path,contract=replace(runtime.GC2_CONTRACT,**change),callbacks=None)


def test_fixed_entrypoints_cannot_exchange_contracts(tmp_path):
    with pytest.raises(runtime.base.StopComparison,match='STAGE_CONTRACT'):
        runtime.run_two_stage_probe(tmp_path,contract=runtime.GC2_CONTRACT,callbacks=None)
    with pytest.raises(runtime.base.StopComparison,match='STAGE_CONTRACT'):
        runtime.run_replayed_choice_output_probe(tmp_path,contract=runtime.IC2_CONTRACT,callbacks=None)

@pytest.mark.parametrize('field',list(runner.fixed_contract()))
def test_gc2_fixed_contract_rejected_before_replay_or_claim(monkeypatch,tmp_path,field):
    plan=copy.deepcopy(runner.fixed_contract());plan.pop(field)
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    monkeypatch.setattr(runner,'construct_replay',lambda *a:pytest.fail('no private replay'))
    monkeypatch.setattr(runtime.base,'claim_run',lambda *a:pytest.fail('no claim'))
    with pytest.raises(runtime.base.StopComparison,match='PLAN_CONTRACT_CHANGED'):
        runner.run(tmp_path)


@pytest.mark.parametrize('problem',['missing','extra','duplicate','order'])
def test_replay_case_set_rejected_before_private_read(monkeypatch,problem):
    data={'plan':{'cases':[],'baseline':{'runtime':{}}},
          'result':{'plan_sha256':replay.ARTIFACTS['plan'][1],'status':'COMPLETE','source_unchanged':True,
                    'config_unchanged':True,'owned_processes_remaining':0,'new_provider_calls':64,'runtime':{},'rows':[]},
          'safe':{'integrity':{'result_sha256':replay.ARTIFACTS['result'][1],
                    'binding_safe_sha256':replay.ARTIFACTS['binding'][1]},'binding':{'consumed_matches':64},'cases':[]},
          'binding':{'consumed_matches':64}}
    pairs=[(SimpleNamespace(case_id=f'G{i:02d}-1'),None) for i in range(1,33)]
    rows=[{'case_id':c.case_id} for c,_ in pairs]
    data['plan']['cases']=copy.deepcopy(rows);data['result']['rows']=copy.deepcopy(rows);data['safe']['cases']=copy.deepcopy(rows)
    if problem=='missing':data['result']['rows'].pop()
    elif problem=='extra':data['result']['rows'].append({'case_id':'extra'})
    elif problem=='duplicate':data['result']['rows'][1]=data['result']['rows'][0]
    else:data['result']['rows'].reverse()
    monkeypatch.setattr(replay,'load',lambda *a:pytest.fail('no private read'))
    with pytest.raises(runtime.base.StopComparison,match='REPLAY_BINDING_MISMATCH'):
        replay.replay_cases(pairs,data)
