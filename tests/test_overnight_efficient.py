"""D060 local planning and exactly two real approval dispatches per unit."""
import copy
import json
import sys
import hashlib

import pytest

from tests.test_overnight_controller import package, short_runs, cloud, raw, runner
from overnight_lib import policy
from overnight_lib.efficient import EfficientController
from overnight_lib.report import report
from overnight_lib import provider


def efficient(package, **hooks):
    repo,m,source,root=package
    m['version']=2;m['limits'].update(plan_attempts=1,upper_calls=4,qwen_calls=10)
    m['providers']['planner'].update(provider='qwen',model='local-qwen',executable=sys.executable)
    source.write_text(json.dumps(m))
    def calls(provider,config,packet,*args):
        if packet['stage']=='final':return raw({'verdict':'APPROVED','reason':'fixture candidate and gates reviewed'})
        return cloud(provider,config,packet,*args)
    return EfficientController.start(source,root,cloud=hooks.get('cloud',calls),
                                     runner=hooks.get('runner',runner),ready=hooks.get('ready',lambda:True))


def test_two_units_two_upper_calls_each_and_reload(package):
    c=efficient(package);c.drive()
    assert c.s['phase']=='COMPLETE',c.s['reason']
    loaded=EfficientController(c.path)
    assert loaded.s['index']==2
    measured=report(loaded)
    assert measured['upper_dispatch_intents']==4
    assert measured['by_model']['local-qwen']['calls_observed']>=4
    assert [u['cloud_calls'] for u in measured['units']]==[2,2]
    assert all(u['outcome']=='completed' for u in measured['units'])
    assert all((c.artifact(r['child']['root'])/'post.json').is_file() for r in c.s['receipts'])


def test_final_rejection_never_applies(package):
    def reject(provider,config,packet,*args):
        if packet['stage']=='final':return raw({'verdict':'BLOCKED','reason':'fixture rejection'})
        return cloud(provider,config,packet,*args)
    c=efficient(package,cloud=reject);c.drive()
    assert c.s['phase']=='BLOCKED'
    assert (package[0]/'calc.py').read_text()=='def total(xs): return 0\n'
    assert report(c)['upper_dispatch_intents']==2


def test_plan_rejection_uses_one_upper_no_implementation(package):
    def reject(provider,config,packet,*args):
        if packet['stage']=='reviewer':return raw({'verdict':'REVISE','reason':'fixture incomplete tests'})
        return cloud(provider,config,packet,*args)
    c=efficient(package,cloud=reject);c.drive()
    assert c.s['phase']=='BLOCKED' and c.s['child'] is None
    assert report(c)['upper_dispatch_intents']==1


def test_no_qwen_means_no_cloud(package):
    c=efficient(package,ready=lambda:False);c.drive()
    assert c.s['phase']=='BLOCKED' and c.s['calls']=={}


def test_local_plan_unknown_delivery_not_replayed(package):
    calls=[]
    def die(*args):calls.append(1);raise RuntimeError('lost response')
    c=efficient(package,cloud=die);c.drive()
    assert c.s['phase']=='UNKNOWN_DELIVERY'
    EfficientController(c.path,cloud=die).drive()
    assert calls==[1] and report(c)['upper_dispatch_intents']==0


def test_v2_rejects_upper_planner_and_extra_review_attempt(package):
    c=efficient(package);m=copy.deepcopy(c.m)
    m['providers']['planner'].update(provider='gpt',model='gpt-5.6-sol')
    with pytest.raises(policy.Error):policy.validate(m)
    m=copy.deepcopy(c.m);m['limits']['plan_attempts']=2
    with pytest.raises(policy.Error):policy.validate(m)


def test_plan_evidence_tamper_rejected(package):
    c=efficient(package)
    # stop after test approval, before child implementation
    c.run_child=lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    c.drive();assert c.s['phase']=='PAUSED'
    p=c.path/'calls/u0-a1-reviewer/raw.json';value=json.loads(p.read_text());value['result']['reason']='tampered'
    p.write_text(json.dumps(value))
    with pytest.raises(policy.Error):EfficientController(c.path)


def approval_copy(tmp_path,monkeypatch):
    original=policy.ROOT
    root=tmp_path/'approval-copy'
    meta=json.loads((original/'Docs/ai/infra/efficient.json').read_bytes())
    for name in [meta['design'],meta['review']]:
        dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((original/name).read_bytes())
    dest=root/'Docs/ai/infra/efficient.json';dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(meta));monkeypatch.setattr(policy,'ROOT',root)
    return root,meta


def test_tampered_release_blocks_recover_pointer(package,tmp_path,monkeypatch):
    c=efficient(package);root,meta=approval_copy(tmp_path,monkeypatch)
    (root/meta['design']).write_text('Status: APPROVED\nchanged')
    pointers=[]
    with pytest.raises(policy.Error):
        EfficientController.launch(package[2],package[3],on_created=pointers.append,prepare_only=True)
    assert pointers==[]


@pytest.mark.parametrize('remove',['Reviewer / Sol','design_hash'])
def test_unlinked_approved_review_rejected(package,tmp_path,monkeypatch,remove):
    c=efficient(package);root,meta=approval_copy(tmp_path,monkeypatch)
    if remove=='design_hash':remove=meta['design_sha256']
    p=root/meta['review'];p.write_text(p.read_text().replace(remove,'unrelated'))
    meta['review_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
    (root/'Docs/ai/infra/efficient.json').write_text(json.dumps(meta))
    with pytest.raises(policy.Error):policy.check_version(c.m)


def test_truncated_qwen_json_never_reaches_upper(package,monkeypatch):
    c=efficient(package)
    result=cloud(None,None,{'stage':'planner','unit_id':'sum','unit':c.m['units'][0]})['result']
    response={'choices':[{'finish_reason':'length','message':{'content':json.dumps(result)}}], 'usage':{'prompt_tokens':2,'completion_tokens':3}}
    monkeypatch.setattr(provider.process,'execute',lambda *_args,**_kw: {'exit_code':0,'error':None,'stdout':json.dumps(response),'stderr':'','seconds':.1})
    c.cloud=provider.invoke;c.drive()
    assert c.s['phase']=='BLOCKED' and report(c)['upper_dispatch_intents']==0
    assert not (package[0]/c.m['units'][0]['test_slot']).exists()


def test_partial_apply_resumes_without_second_final_call(package,monkeypatch):
    c=efficient(package)
    import task_apply
    original=task_apply.atomic_bytes;interrupted=[]
    def crash(path,data):
        original(path,data)
        if Path(path)==package[0]/'calc.py' and not interrupted:
            interrupted.append(True);raise KeyboardInterrupt()
    from pathlib import Path
    monkeypatch.setattr(task_apply,'atomic_bytes',crash)
    c.drive();assert c.s['phase']=='PAUSED',c.s['reason']
    assert report(c)['upper_dispatch_intents']==2
    monkeypatch.setattr(task_apply,'atomic_bytes',original)
    resumed=EfficientController(c.path,cloud=c.cloud,runner=runner,ready=lambda:True)
    resumed.drive();assert resumed.s['phase']=='COMPLETE',resumed.s['reason']
    assert report(resumed)['upper_dispatch_intents']==4


def test_final_unknown_is_not_resent(package):
    dispatched=[]
    def lose(provider,config,packet,*args):
        if packet['stage']=='final':dispatched.append(1);raise RuntimeError('lost final')
        return cloud(provider,config,packet,*args)
    c=efficient(package,cloud=lose);c.drive()
    assert c.s['phase']=='UNKNOWN_DELIVERY'
    EfficientController(c.path,cloud=lose,runner=runner).drive()
    assert dispatched==[1] and (package[0]/'calc.py').read_text()=='def total(xs): return 0\n'


def test_post_failure_never_claims_completion(package,monkeypatch):
    c=efficient(package)
    monkeypatch.setattr(provider.process,'execute',lambda *_a,**_k: {'exit_code':1,'error':None,'stdout':'','stderr':'post failure','seconds':.1})
    c.drive()
    assert c.s['phase']=='BLOCKED' and c.s['index']==0
    assert report(c)['upper_dispatch_intents']==2


def test_final_and_post_evidence_tamper_rejected(package):
    c=efficient(package);c.drive();assert c.s['phase']=='COMPLETE'
    path=c.artifact(c.s['receipts'][0]['child']['root']+'/final/raw.json')
    raw=path.read_bytes();value=json.loads(raw);value['result']['reason']='tampered';path.write_text(json.dumps(value))
    with pytest.raises(policy.Error):EfficientController(c.path)
    path.write_bytes(raw)
    path=c.artifact(c.s['receipts'][0]['child']['root']+'/post-request.json')
    value=json.loads(path.read_bytes());value['scope']=[];path.write_text(json.dumps(value))
    with pytest.raises(policy.Error):EfficientController(c.path)


@pytest.mark.parametrize('boundary',['request','result'])
def test_post_crash_reuses_immutable_request_and_result(package,monkeypatch,boundary):
    c=efficient(package);interrupted=[]
    writer=c.b['task_state'].write_json;execute=provider.process.execute
    if boundary=='request':
        def crash(path,value):
            writer(path,value)
            if str(path).endswith('post-request.json') and not interrupted:
                interrupted.append(True);raise KeyboardInterrupt()
        monkeypatch.setattr(c.b['task_state'],'write_json',crash)
    else:
        def crash(*args,**kwargs):
            result=execute(*args,**kwargs)
            if not interrupted:
                interrupted.append(True);raise KeyboardInterrupt()
            return result
        monkeypatch.setattr(provider.process,'execute',crash)
    c.drive();assert c.s['phase']=='PAUSED',c.s['reason']
    request=c.artifact('children/u0/post-request.json').read_bytes()
    result_path=c.artifact('children/u0/post-result.json')
    result=result_path.read_bytes() if result_path.exists() else None
    monkeypatch.setattr(c.b['task_state'],'write_json',writer)
    monkeypatch.setattr(provider.process,'execute',execute)
    resumed=EfficientController(c.path,cloud=c.cloud,runner=runner,ready=lambda:True)
    resumed.drive();assert resumed.s['phase']=='COMPLETE',resumed.s['reason']
    assert c.artifact('children/u0/post-request.json').read_bytes()==request
    if result is not None:assert result_path.read_bytes()==result
    assert report(resumed)['upper_dispatch_intents']==4


def test_final_quota_pause_has_no_delivery_intent(package,monkeypatch):
    def calls(p,config,packet,*args):
        if packet['stage']=='final':return raw({'verdict':'APPROVED','reason':'fixture'})
        result=cloud(p,config,packet,*args)
        if packet['stage']=='reviewer' and packet['unit_id']=='sum':
            monkeypatch.setattr(provider,'quota',lambda *_a,**_k:False)
        return result
    c=efficient(package,cloud=calls);c.drive()
    assert c.s['phase']=='PAUSED_QUOTA'
    assert not c.artifact('children/u0/final/intent.json').exists()
    monkeypatch.setattr(provider,'quota',lambda *_a,**_k:True)
    resumed=EfficientController(c.path,cloud=calls,runner=runner,ready=lambda:True)
    resumed.drive();assert resumed.s['phase']=='COMPLETE',resumed.s['reason']


@pytest.mark.parametrize('completed', [False, True])
def test_post_raw_omission_is_rejected(package, completed):
    c = efficient(package)
    if not completed:
        original = c.child_outputs
        def interrupt(*args):
            raise KeyboardInterrupt()
        c.child_outputs = interrupt
    c.drive()
    assert c.s['phase'] == ('COMPLETE' if completed else 'PAUSED')
    path = c.artifact('children/u0/post.json')
    post = json.loads(path.read_bytes())
    name = next(n for n in post['evidence'] if n.endswith('/probe.json'))
    c.artifact('children/u0/' + name).unlink()
    del post['evidence'][name]
    path.write_text(json.dumps(post))
    if completed:
        with pytest.raises(policy.Error, match='completed post evidence changed'):
            EfficientController(c.path)
    else:
        resumed = EfficientController(c.path, cloud=c.cloud, runner=runner, ready=lambda: True)
        resumed.drive()
        assert resumed.s['phase'] == 'INVALID'
        assert 'mandatory raw' in resumed.s['reason']


@pytest.mark.parametrize('phase', ['ESCALATED', 'ROLLING_BACK', 'ROLLED_BACK'])
def test_failed_child_is_reportable_without_dispatch(package, phase):
    c = efficient(package)
    original = c._final
    c._final = lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
    c.drive()
    assert c.s['phase'] == 'PAUSED'
    from pathlib import Path
    state_path = c.artifact(c.s['child']['path']) / 'state.json'
    state = json.loads(state_path.read_bytes())
    state['phase'] = phase
    state_path.write_text(json.dumps(state))
    def forbidden(*args):
        pytest.fail('terminal child dispatched work')
    loaded = EfficientController(c.path, cloud=forbidden, runner=forbidden, ready=lambda: True)
    assert report(loaded)['upper_dispatch_intents'] == 1
    loaded.drive()
    assert loaded.s['phase'] == 'BLOCKED'
    assert report(EfficientController(c.path))['units'][0]['outcome'] == 'BLOCKED'
