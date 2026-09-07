"""Offline acceptance checks; normal game tests do not require providers."""
import copy
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from autodev_lib import policy,process
from autodev_lib.draft import refine
from autodev_lib.engine import Campaign,Pause

AGENT=Path('C:/AIagent/agent')


@pytest.fixture
def setup(tmp_path):
    if not (AGENT/'lib/task_runner.py').exists():pytest.skip('optional AIagent integration unavailable')
    repo=tmp_path/'repo';repo.mkdir();(repo/'calc.py').write_text('def total(xs): return 0\n')
    (repo/'tests').mkdir();(repo/'tests/test_calc.py').write_text('from calc import total\ndef test_total(): assert total([1,2])==3\n')
    for argv in (['init','-q'],['add','.'],['-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture']):
        subprocess.run(['git','-C',str(repo),*argv],check=True,capture_output=True)
    c=dict(contract_version=1,task_id='autodev-test',repo_root=str(repo),goal='sum',risk='red',read_list=['calc.py'],allow_edit=['calc.py'],allow_new=[],protected=['tests/**'],test_paths=['tests'],test_command=['-q'],extra_read_context=[],context='sum integers',invariants=['exact sum'],acceptance=['returns three'],required_tests=['tests/test_calc.py::test_total'],checks=[],design_gate={'decision':'NOT_REQUIRED','reviewer':'Test Reviewer','reason':'fixture'},limits=dict(max_file_bytes=262144,max_total_edit_bytes=524288,max_model_output_bytes=131072,max_fixes=0,run_timeout_s=30,max_tokens=1024))
    m=dict(version=1,issuer='test authorization',repo_root=str(repo),agent_root=str(AGENT),not_before=time.time()-1,expires_at=time.time()+1000,max_seconds=1000,max_cloud_calls=10,max_local_calls=20,max_attempts=2,max_packet_bytes=131072,max_output_bytes=1048576,
           providers={'gpt':{'model':'gpt-5.6-sol','executable':sys.executable,'cloud_read':['calc.py']}},quota_policy={'mode':'bounded_calls','reserve_percent':20,'snapshot':None},
           jobs=[dict(id='first',kind='task',instruction='Implement exact sum',read_list=['calc.py'],reviewer='gpt',author_model='local-qwen',template=c,draft=True)],apply=True)
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps(m))
    return repo,m,manifest,tmp_path/'campaigns'


def raw(result):return {'exit_code':0,'error':None,'result':result,'usage':{'input_tokens':10,'output_tokens':5},'seconds':.1}


def approved(*args):return raw({'verdict':'APPROVED','reason':'fixture reviewed'})


def draft(packet):return raw({'goal':'Implement exact integer sum','context':'Return sum of all signed integers'})


def v1_fake(args,directory):
    import task_runner,task_apply,task_state
    action=args[0]
    if action=='plan':
        path=task_runner.plan(Path(args[args.index('--contract')+1]),Path(args[args.index('--runs-root')+1]))
        return {'run':str(path)}
    path=Path(args[args.index('--run')+1])
    if action=='resume':
        response={'model':'fixture-qwen','choices':[{'finish_reason':'stop','message':{'content':json.dumps({'files':{'calc.py':'def total(xs): return sum(xs)\n'},'notes':''})}}],'usage':{'prompt_tokens':5,'completion_tokens':5}}
        state=task_runner.execute_run(path,lambda _:response)
    else:state=task_apply.mutate(path,approval=Path(args[args.index('--approval')+1]) if '--approval' in args else None)
    return {'run':str(path),'phase':state['phase']}


def start(setup,**hooks):
    _,m,p,r=setup;p.write_text(json.dumps(m))
    return Campaign.start(p,r,cloud=hooks.get('cloud',approved),local=hooks.get('local',draft),runner=hooks.get('runner',v1_fake))


def test_refine_cannot_change_authority():
    template={'goal':'x','context':'y','invariants':['fixed'],'risk':'red'};before=copy.deepcopy(template)
    result=refine(template,{'goal':'new','context':'more precise'})
    assert template==before and result['invariants']==['fixed'] and result['risk']=='red'
    result['invariants'].append('new');assert template==before


@pytest.mark.parametrize('reply',[{},[],{'goal':'','context':'x'},{'goal':' ','context':'x'},{'goal':'x','context':'y','risk':'green'}])
def test_bad_refine(reply):
    with pytest.raises(ValueError):refine({},reply)


def test_design_bound():policy.check_design()


@pytest.mark.parametrize('field,value',[('max_cloud_calls',0),('max_attempts',4),('max_seconds',float('nan')),('apply','yes'),('version',True)])
def test_invalid_policy(setup,field,value):
    setup[1][field]=value
    with pytest.raises(ValueError):policy.validate(setup[1])


def test_unknown_policy_field(setup):
    setup[1]['bypass']=True
    with pytest.raises(ValueError):policy.validate(setup[1])


def test_red_queue_e2e_and_no_duplicate_resume(setup):
    m=setup[1];second=copy.deepcopy(m['jobs'][0]);second['id']='second';second['kind']='review';second.pop('template');second.pop('draft');m['jobs'].append(second)
    calls=[]
    def cloud(*args):calls.append(args[2]['stage']);return approved()
    c=start(setup,cloud=cloud);s=c.drive()
    assert s['phase']=='COMPLETE',s['reason']
    assert calls==['contract-0','red','completion','review']
    assert s['index']==2 and 'sum(xs)' in (setup[0]/'calc.py').read_text()
    assert Campaign(c.path,cloud=lambda *_:pytest.fail('duplicate call')).drive()['phase']=='COMPLETE'


def test_contract_rework_is_bounded(setup):
    def findings(*_):return raw({'verdict':'FINDINGS','reason':'needs more precision'})
    c=start(setup,cloud=findings);s=c.drive()
    assert s['phase']=='NEEDS_USER' and s['cloud_calls']==2 and s['local_calls']==2
    assert 'return 0' in (setup[0]/'calc.py').read_text()


@pytest.mark.parametrize('mode',['expiry','stop','budget','scope','strict'])
def test_limits_prevent_cloud(setup,mode):
    m=setup[1]
    if mode=='expiry':m['not_before']=time.time()-100;m['expires_at']=time.time()-1
    if mode=='scope':m['providers']['gpt']['cloud_read']=[]
    if mode=='strict':m['quota_policy']['mode']='strict_reserve'
    c=start(setup,cloud=lambda *_:pytest.fail('cloud called'))
    if mode=='stop':(c.path/'stop').write_text('stop')
    if mode=='budget':c.s['cloud_calls']=m['max_cloud_calls'];c.write()
    s=c.drive();assert s['phase'] in ('NEEDS_USER','PAUSED','PAUSED_QUOTA')


def test_unknown_delivery_never_retries(setup):
    def crash(*_):raise KeyboardInterrupt()
    c=start(setup,cloud=crash);assert c.drive()['phase']=='PAUSED'
    s=Campaign(c.path,cloud=lambda *_:pytest.fail('resend'),local=draft).drive()
    assert s['phase']=='UNKNOWN_DELIVERY'


def test_saved_raw_response_recovered(setup,monkeypatch):
    c=start(setup);original=c.b['task_state'].write_json;fired=[]
    def crash(path,value):
        original(path,value)
        if path.name=='raw.json' and 'contract-' in str(path) and not fired:fired.append(1);raise KeyboardInterrupt()
    with monkeypatch.context() as patch:
        patch.setattr(c.b['task_state'],'write_json',crash)
        assert c.drive()['phase']=='PAUSED'
    calls=[]
    def cloud(*args):calls.append(args[2]['stage']);return approved()
    s=Campaign(c.path,cloud=cloud,local=draft,runner=v1_fake).drive()
    assert s['phase']=='COMPLETE',s['reason']
    assert 'contract-0' not in calls


def test_manifest_change_stops(setup):
    c=start(setup);setup[2].write_text('{}')
    assert c.drive()['phase']=='NEEDS_USER'


def test_source_change_after_review_stops(setup):
    def cloud(*_):(setup[0]/'calc.py').write_text('user edit');return approved()
    c=start(setup,cloud=cloud);s=c.drive()
    assert s['phase']=='NEEDS_USER' and 'source changed' in s['reason']
    assert (setup[0]/'calc.py').read_text()=='user edit'


def test_same_model_review_forbidden(setup):
    j=setup[1]['jobs'][0];j['kind']='review';j.pop('template');j.pop('draft');j['author_model']='gpt-5.6-sol'
    c=start(setup,cloud=lambda *_:pytest.fail('self review'))
    assert c.drive()['phase']=='PAUSED_QUOTA'


def test_corrupted_state_rejected(setup):
    c=start(setup);c.s['phase']='MADE_UP';c.write()
    with pytest.raises(ValueError):Campaign(c.path)


def test_shared_campaign_lock(setup):
    c=start(setup);key=policy.digest(str(c.repo.resolve()).casefold().encode())
    with c.b['task_state'].lock(AGENT/'.task-locks'/('autodev-repo-'+key+'.lock')):
        with pytest.raises(Exception,match='lock busy'):c.drive()


def test_process_timeout_and_output_limit(tmp_path):
    first=process.execute([sys.executable,'-c','import time;time.sleep(10)'],tmp_path,tmp_path/'timeout','',.2,10000)
    assert first['error']=='timeout'
    second=process.execute([sys.executable,'-c','print("x"*100000)'],tmp_path,tmp_path/'large','',5,1000)
    assert second['error']=='output limit' and len(second['stdout'])<=1000


def test_dry_run_no_provider_or_source_writes(setup):
    _,_,manifest,_=setup
    result=subprocess.run([sys.executable,str(policy.ROOT/'scripts/autodev.py'),'dry-run','--manifest',str(manifest)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['cloud_calls']==0
    assert 'return 0' in (setup[0]/'calc.py').read_text()


def test_upper_approval_does_not_authorize_modified_contract(setup,monkeypatch):
    c=start(setup);original=c.mark
    def interrupt(phase=None,reason=None):
        original(phase,reason)
        if phase=='RUNNING':raise KeyboardInterrupt()
    with monkeypatch.context() as patch:
        patch.setattr(c,'mark',interrupt)
        assert c.drive()['phase']=='PAUSED'
    c.s['job']['contract']['risk']='green';c.write()
    resumed=Campaign(c.path,cloud=approved,local=draft,runner=v1_fake)
    assert resumed.drive()['phase']=='NEEDS_USER'
    assert 'return 0' in (setup[0]/'calc.py').read_text()


def test_completion_context_drift_stops_next_job(setup):
    repo,m,_,_=setup;(repo/'context.txt').write_text('fixed context')
    m['jobs'][0]['read_list'].append('context.txt');m['providers']['gpt']['cloud_read'].append('context.txt')
    def cloud(*args):
        if args[2]['stage']=='completion':(repo/'context.txt').write_text('later user change')
        return approved()
    s=start(setup,cloud=cloud).drive()
    assert s['phase']=='NEEDS_USER' and s['index']==0


def test_provider_exception_records_unknown_delivery(setup):
    def broken(*args):raise OSError('connection lost after submission')
    s=start(setup,cloud=broken).drive()
    assert s['phase']=='UNKNOWN_DELIVERY' and s['cloud_calls']==1


def test_completion_findings_do_not_advance_or_rollback(setup):
    def cloud(*args):return raw({'verdict':'FINDINGS','reason':'semantic gap'}) if args[2]['stage']=='completion' else approved()
    s=start(setup,cloud=cloud).drive()
    assert s['phase']=='NEEDS_USER' and s['index']==0
    assert 'sum(xs)' in (setup[0]/'calc.py').read_text()


def test_known_quota_exhaustion_in_bounded_mode(setup):
    _,m,p,_=setup;quota=p.parent/'quota.json'
    quota.write_text(json.dumps({'gpt':{'observed_at':time.time(),'windows':[{'duration_minutes':10080,'used_percent':95,'resets_at':time.time()+500}]}}))
    m['quota_policy']['snapshot']=str(quota)
    s=start(setup,cloud=lambda *_:pytest.fail('exhausted provider called')).drive()
    assert s['phase']=='PAUSED_QUOTA' and s['cloud_calls']==0


def test_report_includes_runner_consumption_and_receipts(setup):
    from autodev import report
    c=start(setup);assert c.drive()['phase']=='COMPLETE'
    stats=report(c)
    assert stats['known_input_tokens']==45 and stats['known_output_tokens']==25
    assert stats['completed_jobs']==1 and stats['llm_calls_for_report']==0
    assert 'first' in json.loads((c.path/'handoff.json').read_text())['completed_job_receipts']


def test_review_only_never_changes_canonical_document(setup):
    repo,m,_,_=setup;before=(repo/'calc.py').read_bytes()
    j=m['jobs'][0];j['kind']='review';j.pop('template');j.pop('draft')
    c=start(setup);assert c.drive()['phase']=='COMPLETE'
    assert (repo/'calc.py').read_bytes()==before
    signed=json.loads((c.path/'calls/j0-review/signed-review.json').read_text())
    assert signed['model']=='gpt-5.6-sol' and signed['role']=='Reviewer'


@pytest.mark.parametrize('text',['{"version":1,"version":2}','{"limit":NaN}','{"p":{"model":"a","model":"b"}}'])
def test_duplicate_and_nonfinite_authorization_rejected(text):
    with pytest.raises(ValueError):policy.loads(text)


def test_review_must_bind_design_hash(tmp_path,monkeypatch):
    root=tmp_path/'root';(root/'Docs/ai/infra').mkdir(parents=True)
    (root/'design.md').write_text('Status: APPROVED\n')
    (root/'review.md').write_text('Verdict: APPROVED\nReviewer / Sol\ndesign.md\n')
    meta={'design':'design.md','review':'review.md','design_sha256':policy.digest((root/'design.md').read_bytes()),'review_sha256':policy.digest((root/'review.md').read_bytes())}
    (root/'Docs/ai/infra/autodev.json').write_text(json.dumps(meta))
    monkeypatch.setattr(policy,'ROOT',root)
    with pytest.raises(ValueError):policy.check_design()


def test_launch_keeps_repo_lock_through_first_provider(setup):
    repo,m,p,r=setup;p.write_text(json.dumps(m));attempted=[]
    def cloud(*args):
        if not attempted:
            attempted.append(True)
            with pytest.raises(Exception,match='lock busy'):Campaign.start(p,r)
            assert len(list(r.iterdir()))==1
        return approved()
    c=Campaign.launch(p,r,cloud=cloud,local=draft,runner=v1_fake)
    assert c.s['phase']=='COMPLETE'


def test_provider_stage_guidance_and_tools_disabled(tmp_path,monkeypatch):
    seen=[]
    def execute(argv,cwd,directory,stdin,timeout,cap,extra_output=None):
        seen.append((argv,stdin))
        extra_output.write_text(json.dumps({'verdict':'APPROVED','reason':'contract is adequate'}))
        return {'exit_code':0,'error':None,'stdout':json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}),'stderr':'','seconds':.1}
    monkeypatch.setattr(process,'execute',execute)
    result=process.invoke('gpt',{'executable':sys.executable,'model':'gpt-5.6-sol'},{'stage':'contract-0'},tmp_path,5,10000)
    assert result['result']['verdict']=='APPROVED'
    argv,prompt=seen[0]
    assert 'not the current implementation' in prompt
    assert argv[argv.index('--sandbox')+1]=='read-only' and '--output-schema' in argv
    assert all(feature in argv for feature in process.DISABLED)


def test_kill_fallback_has_finite_waits(monkeypatch):
    waits=[]
    class Proc:
        pid=123
        def poll(self):return None
        def kill(self):pass
        def wait(self,timeout):waits.append(timeout)
    def fail(*args,**kwargs):
        assert kwargs['timeout']==5
        raise subprocess.TimeoutExpired('taskkill',5)
    monkeypatch.setattr(process.os,'name','nt');monkeypatch.setattr(process.subprocess,'run',fail)
    process.kill(Proc());assert waits==[5]


def test_v1_canonical_repo_path_is_not_authority_change(setup):
    repo,m,_,_=setup;alias=repo.parent/'alias';alias.mkdir()
    m['jobs'][0]['template']['repo_root']=str(alias/'..'/repo.name)
    s=start(setup).drive()
    assert s['phase']=='COMPLETE',s['reason']


def test_cli_start_does_not_resume_launch_pause(setup,monkeypatch,capsys):
    import autodev
    c=start(setup)
    c.s['phase']='PAUSED';c.s['resume_phase']='CONTRACT_REVIEW';c.s['reason']='interrupted'
    monkeypatch.setattr(autodev.Campaign,'launch',lambda *_:c)
    monkeypatch.setattr(c,'drive',lambda:pytest.fail('start must not drive twice'))
    monkeypatch.setattr(sys,'argv',['autodev','start','--manifest',str(setup[2])])
    assert autodev.main()==3
    assert json.loads(capsys.readouterr().out)['phase']=='PAUSED'


def test_case_variant_self_review_rejected(setup):
    j=setup[1]['jobs'][0];j['kind']='review';j.pop('template');j.pop('draft');j['author_model']='GPT-5.6-SOL'
    assert start(setup,cloud=lambda *_:pytest.fail('self review')).drive()['phase']=='PAUSED_QUOTA'


def test_partial_apply_timeout_resumes_same_journal(setup,monkeypatch):
    import task_apply,task_state
    c=start(setup,runner=None);c.runner=None
    original=task_apply.atomic_bytes;crashed=[];calls=[]
    def write(path,data):
        original(path,data)
        if path.resolve()==(setup[0]/'calc.py').resolve() and not crashed:
            crashed.append(True);raise OSError('simulated interrupted apply')
    def execute(argv,cwd,directory,stdin,timeout,cap):
        args=argv[2:];calls.append(args[0])
        try:result=v1_fake(args,directory)
        except OSError:return {'exit_code':1,'error':'timeout','stdout':'','stderr':'','seconds':.1}
        return {'exit_code':0,'error':None,'stdout':json.dumps(result),'stderr':'','seconds':.1}
    monkeypatch.setattr(process,'execute',execute);monkeypatch.setattr(task_apply,'atomic_bytes',write)
    state=c.drive();run=state['job']['run']
    assert state['phase']=='PAUSED' and state['resume_phase']=='APPLYING'
    assert task_state.read_state(Path(run))['phase']=='APPLYING'
    assert 'sum(xs)' in (setup[0]/'calc.py').read_text()
    resumed=Campaign(c.path,cloud=approved,local=draft)
    assert resumed.drive()['phase']=='COMPLETE'
    assert task_state.read_state(Path(run))['phase']=='APPLIED'
    assert calls==['plan','resume','apply','apply'] and resumed.s['cloud_calls']==3
