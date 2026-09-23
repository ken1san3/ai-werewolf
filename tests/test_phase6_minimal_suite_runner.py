from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace as NS

import pytest

from scripts import phase6_minimal_suite_runner as r
from scripts import phase6_minimal_suite_adapter as a


def setup_stage(monkeypatch, tmp_path):
    body = dict(model='Qwen.gguf', max_tokens=512, messages=[{'role':'system','content':'minimal'}],
                response_format={'json_schema':{'schema':{'properties':{'grounding':{}}}}})
    suite = NS(case=NS(case_id='G01-1', category='direct_question', request=None), binding=object())
    monkeypatch.setattr(a, 'candidate_body', lambda *args:deepcopy(body))
    monkeypatch.setattr(a, 'candidate_wire', r.wire_bytes)
    def shadow(value):
        value = deepcopy(value)
        del value['response_format']['json_schema']['schema']['properties']['grounding']
        return value
    monkeypatch.setattr(a, 'shadow_without_grounding', shadow)
    monkeypatch.setattr(r.base, '_RUN_DEADLINE', time.monotonic()+1200)
    prompts, calls, saves = [], [], []
    def count(value, *, wire_payload, private_sink):
        assert r.wire_bytes(value) == wire_payload
        prompts.append(value)
        private_sink('minimal')
        return dict(prompt_tokens_actual=10, schema_changes_rendered_prompt=False)
    monkeypatch.setattr(r.base, 'count_prompt', count)
    monkeypatch.setattr(r.probe, 'validate_suite', lambda raw,binding:NS(
        applicability='APPLICABILITY_UNRESOLVED',private_before_sha256='a'*64,private_after_sha256='a'*64))
    candidate = dict(grounding=[], speech_act={'kind':'NONE'}, utterance=None)
    row = {**r.frozen_case(suite), 'call_consumed':0, 'status':'NOT_STARTED', 'raw_sha256':None,
           'mechanical_status':'UNKNOWN', 'applicability':'UNRESOLVED'}
    budget = {'calls':0}
    def save():
        saves.append(deepcopy(row))
        r.durable(tmp_path/'saved.json', row)
    def request(endpoint, sent, **kwargs):
        assert endpoint == '/v1/chat/completions'
        assert (tmp_path/'G01-1.generation.claim').exists()
        assert json.loads((tmp_path/'saved.json').read_text())['call_consumed'] == 1
        assert sent == body and 'grounding' in sent['response_format']['json_schema']['schema']['properties']
        calls.append(endpoint)
        return {'choices':[{'message':{'content':json.dumps(candidate)},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':10,'completion_tokens':20}}
    monkeypatch.setattr(r.base, 'request', request)
    return suite, row, budget, save, prompts, calls, saves, request


def test_stage_claim_precedes_single_dispatch_shadow_is_tokenize_only(monkeypatch, tmp_path):
    suite,row,budget,save,prompts,calls,_,_ = setup_stage(monkeypatch,tmp_path)
    r.stage(suite,row,tmp_path,{'run_id':'run'},save,budget)
    assert len(prompts) == 2 and calls == ['/v1/chat/completions']
    assert row['status'] == 'COMPLETE' and budget['calls'] == 1
    assert row['grounding_prompt_token_delta'] == 0
    assert row['applicability'] == 'UNRESOLVED'
    assert row['private_before_sha256'] == row['private_after_sha256']
    with pytest.raises(r.RunError): r.stage(suite,row,tmp_path,{},save,budget)
    assert len(calls) == 1


@pytest.mark.parametrize('failure', ['http','token','completion','length','json','validator','reasoning','private'])
def test_consumed_failures_never_retry_and_no_private_error_text(monkeypatch,tmp_path,failure):
    suite,row,budget,save,_,calls,_,request = setup_stage(monkeypatch,tmp_path)
    def failing(endpoint,body,**kwargs):
        result = request(endpoint,body,**kwargs)
        if failure == 'http': raise RuntimeError('SECRET PATH AND PAYLOAD')
        if failure == 'token': result['usage']['prompt_tokens'] = 11
        if failure == 'completion': result['usage']['completion_tokens'] = 513
        if failure == 'length': result['choices'][0]['finish_reason'] = 'length'
        if failure == 'reasoning': result['choices'][0]['message']['reasoning_content'] = 'SECRET'
        if failure == 'json': result['choices'][0]['message']['content'] = '{'
        return result
    monkeypatch.setattr(r.base,'request',failing)
    if failure == 'validator':
        def invalid(*args): raise r.probe.ProbeError('BINDING_INVALID')
        monkeypatch.setattr(r.probe,'validate_suite',invalid)
    if failure == 'private':
        original = r.durable
        def write(path,*args,**kwargs):
            if str(path).endswith('.output.bin'): raise PermissionError('SECRET')
            return original(path,*args,**kwargs)
        monkeypatch.setattr(r,'durable',write)
    r.stage(suite,row,tmp_path,{},save,budget)
    assert row['status'] in ('ERROR','OUTPUT_INVALID')
    assert row['call_consumed'] == budget['calls'] == len(calls) == 1
    assert b'SECRET' not in r.wire_bytes(row)
    assert (tmp_path/'G01-1.generation.claim').exists()


def test_marker_fsync_failure_prevents_dispatch_and_preserves_marker(monkeypatch,tmp_path):
    suite,row,budget,save,_,calls,_,_ = setup_stage(monkeypatch,tmp_path)
    original = r.claim
    def badclaim(path,identity):
        original(path,identity)
        raise OSError('disk')
    monkeypatch.setattr(r,'claim',badclaim)
    with pytest.raises(OSError): r.stage(suite,row,tmp_path,{},save,budget)
    assert calls == [] and budget['calls'] == 0
    assert (tmp_path/'G01-1.generation.claim').exists()


@pytest.mark.parametrize('failure',['limit','deadline','request_changed'])
def test_predispatch_gates_make_zero_calls(monkeypatch,tmp_path,failure):
    suite,row,budget,save,_,calls,_,_ = setup_stage(monkeypatch,tmp_path)
    if failure == 'limit': budget['calls'] = 32
    if failure == 'deadline': monkeypatch.setattr(r.base,'_RUN_DEADLINE',time.monotonic()-1)
    if failure == 'request_changed': row['wire_sha256'] = '0'*64
    with pytest.raises(r.RunError): r.stage(suite,row,tmp_path,{},save,budget)
    assert not calls and not (tmp_path/'G01-1.generation.claim').exists()


@pytest.mark.parametrize('name',['minimal-output-v1.run.claim','G01-1.generation.claim'])
def test_exclusive_claim_two_processes_have_one_dispatch(tmp_path,name):
    script = '''import sys,time
from pathlib import Path
from scripts.phase6_minimal_suite_runner import claim, RunError
root=Path(sys.argv[1]); n=sys.argv[2]; marker=sys.argv[3]
(root/('ready'+n)).write_text('1')
until=time.monotonic()+10
while not (root/'release').exists():
    if time.monotonic()>until: raise SystemExit(5)
    time.sleep(.005)
try: claim(root/marker, {'run_id':'same'})
except RunError: raise SystemExit(3)
(root/('dispatch'+n)).write_text('1')
'''
    processes = [subprocess.Popen([sys.executable,'-c',script,str(tmp_path),str(i),name],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) for i in range(2)]
    try:
        deadline=time.monotonic()+10
        while not all((tmp_path/('ready'+str(i))).exists() for i in range(2)):
            assert time.monotonic()<deadline
            time.sleep(.01)
        (tmp_path/'release').write_text('1')
        for proc in processes: proc.communicate(timeout=10)
        assert sorted(p.returncode for p in processes) == [0,3]
        assert sum((tmp_path/('dispatch'+str(i))).exists() for i in range(2)) == 1
    finally:
        for proc in processes:
            if proc.poll() is None: proc.kill(); proc.wait(timeout=5)


def test_listener_requires_original_process_alive_before_and_after(monkeypatch):
    observed=[]
    monkeypatch.setattr(r.base,'owned_listener',lambda p:observed.append(p) or True)
    dead=NS(poll=lambda:0)
    assert not r.owned_listener(dead) and not observed
    values=iter((None,0)); process=NS(poll=lambda:next(values))
    assert not r.owned_listener(process) and observed == [process]


def test_cleanup_only_retained_handles_and_bounded_wait():
    class Process:
        def __init__(self): self.code=None; self.terminated=0; self.killed=0; self.waits=[]
        def poll(self): return self.code
        def terminate(self): self.terminated+=1
        def kill(self): self.killed+=1; self.code=-1
        def wait(self,timeout):
            self.waits.append(timeout)
            if self.code is None: raise subprocess.TimeoutExpired('owned',timeout)
    owned, stranger = Process(),Process()
    result=r.cleanup_owned(None,owned,clock=lambda:0)
    assert owned.terminated == owned.killed == 1 and all(0<t<=25 for t in owned.waits)
    assert stranger.terminated == stranger.killed == 0 and result['owned_processes_remaining']==0


def test_private_rendering_must_include_each_exact_message(monkeypatch,tmp_path):
    def count(body,*,wire_payload,private_sink): private_sink('different')
    monkeypatch.setattr(r.base,'count_prompt',count)
    with pytest.raises(r.RunError,match='TEMPLATE_INVALID'):
        r.measure_prompt({'messages':[{'content':'exact prompt'}]},tmp_path,'G01-1','full')


def test_freeze_uses_only_exact_baseline_manifest_not_annotations(monkeypatch):
    touched=[]
    def hashing(path):
        touched.append(str(path))
        if Path(path).name in r.BASELINE_HASHES: return r.BASELINE_HASHES[Path(path).name]
        if Path(path).name=='T478_GROUNDING_BASIS_QUALITY.md': return 'f4c17f6e148d6df8df215318ba686d81587491269d28a1bc6083f132883ce34e'
        return 'a'*64
    monkeypatch.setattr(r.base,'file_hash',hashing)
    identities=r.source_identity()
    assert r.RUNNER in identities
    assert not any('annotation' in p or 'qw9-results' in p for p in touched)


def test_verify_rejects_changed_static_contract_before_host_access():
    with pytest.raises(r.RunError): r.verify({'experiment':'other'})


@pytest.mark.parametrize('field,value',[('context',4096),('retry',1),('max_provider_calls',33),('request_seconds',9999)])
def test_verify_rejects_budget_and_model_contract_change(field,value):
    plan=dict(experiment=r.EXPERIMENT,task_id=r.TASK,runner=r.RUNNER,version=1,run_id='a'*32,
              sampling=r.base.SAMPLING,context=8192,request_seconds=60,model_seconds=1200,
              load_seconds=180,outer_seconds=1320,max_provider_calls=32,retry=0,repair=0,fallback=0)
    plan[field]=value
    with pytest.raises(r.RunError): r.verify(plan)


@pytest.mark.parametrize('tokens,allowed',[(7679,True),(7680,False),(True,False),(-1,False),(None,False)])
def test_actual_full_context_boundary_precedes_claim(monkeypatch,tmp_path,tokens,allowed):
    suite,row,budget,save,_,calls,_,request=setup_stage(monkeypatch,tmp_path)
    def measurement(body,private,case_id,label):
        return dict(prompt_tokens_actual=tokens if label=='full' else 1,
                    rendered_bytes_sha256='a'*64,schema_changes_rendered_prompt=False)
    monkeypatch.setattr(r,'measure_prompt',measurement)
    def response(*args,**kw):
        value=request(*args,**kw); value['usage']['prompt_tokens']=tokens; return value
    monkeypatch.setattr(r.base,'request',response)
    if allowed:
        r.stage(suite,row,tmp_path,{},save,budget)
        assert row['status']=='COMPLETE' and len(calls)==1
    else:
        with pytest.raises(r.RunError,match='CONTEXT_OVERFLOW' if tokens==7680 else 'TOKEN_MISMATCH'):
            r.stage(suite,row,tmp_path,{},save,budget)
        assert not calls and budget['calls']==0 and not (tmp_path/'G01-1.generation.claim').exists()


def test_native_context_overflow_keeps_safe_enum(monkeypatch,tmp_path):
    def overflow(*args,**kw): raise r.base.StopComparison('CONTEXT_OVERFLOW')
    monkeypatch.setattr(r.base,'count_prompt',overflow)
    with pytest.raises(r.RunError,match='CONTEXT_OVERFLOW'):
        r.measure_prompt({'messages':[]},tmp_path,'G01-1','full')


def fake_lifecycle(monkeypatch,tmp_path):
    private=tmp_path/'private'; private.mkdir()
    out=tmp_path/'out'; out.mkdir()
    binding=NS(authority_without_update={'trigger':'INITIAL_CHAT'},canonical_user_bytes=b'{}',update_requirement=None)
    suites=[NS(case=NS(case_id=cid,category='direct_question'),binding=binding) for cid in r.probe.CASE_IDS]
    plan={'run_id':'a'*32,'argv':['fake-server'], 'cases':[{'case_id':cid,'input_sha256':'a'*64} for cid in r.probe.CASE_IDS]}
    r.durable(out/'plan.json',plan); r.durable(out/'baseline-aggregate.json',r.quality.baseline_bytes())
    monkeypatch.setattr(r,'verify',lambda plan:suites)
    monkeypatch.setattr(r,'host_idle',lambda:None)
    monkeypatch.setattr(r,'create_private_evidence_container',lambda *args,**kwargs:private)
    class Process:
        def __init__(self): self.code=None
        def poll(self): return self.code
        def terminate(self): self.code=0
        def wait(self,timeout): return self.code
    launches=[]
    def launch(*args,**kwargs):
        assert (out/'minimal-output-v1.run.claim').exists()
        assert len(json.loads((out/'results.json').read_text())['rows'])==32
        proc=Process(); launches.append(proc); return proc
    monkeypatch.setattr(r.subprocess,'Popen',launch)
    monkeypatch.setattr(r.base,'request',lambda *args,**kwargs:{'status':'ok'})
    monkeypatch.setattr(r.base,'runtime',lambda:{'constant':True})
    monkeypatch.setattr(r.base,'safe_runtime',lambda ident:{'runtime':'FIXED'})
    monkeypatch.setattr(r,'runtime_matches',lambda ident:True)
    monkeypatch.setattr(r,'owned_listener',lambda proc:proc.poll() is None)
    monkeypatch.setattr(r.base,'port_free',lambda:True)
    monkeypatch.setattr(r.base,'attach_performance',lambda *args:None)
    return out,launches


def test_complete_lifecycle_has_exact32_calls_cleanup_and_one_shot(monkeypatch,tmp_path):
    out,processes=fake_lifecycle(monkeypatch,tmp_path)
    calls=[]
    def stage(suite,row,private,identity,save,budget):
        calls.append(row['case_id']); row.update(status='COMPLETE',call_consumed=1)
        budget['calls']+=1; save()
    monkeypatch.setattr(r,'stage',stage)
    result=r.run(out)
    assert calls==list(r.probe.CASE_IDS) and result['provider_calls']==32
    assert result['status']=='COMPLETE' and result['source_unchanged']
    assert result['owned_processes_remaining']==0 and all(p.poll()==0 for p in processes)
    assert r.base._RUN_DEADLINE is None
    with pytest.raises(r.RunError,match='PLAN_EXISTS'): r.run(out)
    assert len(processes)==2


def test_partial_failure_keeps32_terminal_rows_and_cleans_own_processes(monkeypatch,tmp_path):
    out,processes=fake_lifecycle(monkeypatch,tmp_path)
    def stage(suite,row,private,identity,save,budget):
        if budget['calls']==3: raise r.RunError('MODEL_TIME_BUDGET')
        row.update(status='COMPLETE',call_consumed=1); budget['calls']+=1; save()
    monkeypatch.setattr(r,'stage',stage)
    result=r.run(out)
    assert len(result['rows'])==32 and result['provider_calls']==3
    assert sum(row['status']=='NOT_STARTED' for row in result['rows'])==29
    assert all(row['error']=='MODEL_TIME_BUDGET' for row in result['rows'][3:])
    assert result['status']=='STOPPED' and result['owned_processes_remaining']==0
    assert all(p.poll()==0 for p in processes)


@pytest.mark.parametrize('field',['head','source','approvals','external','argv','cases','baseline_sha256'])
def test_freeze_mutation_rejected_before_dispatch(monkeypatch,field):
    monkeypatch.setattr(r,'git_head',lambda **kw:'head')
    monkeypatch.setattr(r,'source_identity',lambda:{'source':'hash'})
    monkeypatch.setattr(r,'approval_identity',lambda:{'approval':'hash'})
    monkeypatch.setattr(r,'external_identity',lambda:{'external':'hash'})
    monkeypatch.setattr(r.base,'launch_args',lambda key:['launch'])
    monkeypatch.setattr(r,'suite_cases',lambda:())
    plan=dict(experiment=r.EXPERIMENT,task_id=r.TASK,runner=r.RUNNER,version=1,run_id='a'*32,
              sampling=r.base.SAMPLING,context=8192,request_seconds=60,model_seconds=1200,
              load_seconds=180,outer_seconds=1320,max_provider_calls=32,retry=0,repair=0,fallback=0,
              head='head',source={'source':'hash'},approvals={'approval':'hash'},external={'external':'hash'},
              argv=['launch'],cases=[],baseline_sha256=r.probe.sha256(r.quality.baseline_bytes()),
              baseline_artifacts=r.BASELINE_HASHES,legacy_instruction_present=True)
    assert r.verify(plan)==()
    plan[field]='changed'
    with pytest.raises(r.RunError): r.verify(plan)
