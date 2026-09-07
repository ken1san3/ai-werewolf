"""D059 failure/recovery acceptance tests on disposable Git repositories."""
import copy
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from overnight_lib import policy, provider
from overnight_lib.engine import Controller
from overnight_lib.report import report
from autodev_lib.engine import Campaign

AGENT = Path('C:/AIagent/agent')


@pytest.fixture
def short_runs():
    # Native CLIs need short cwd paths even when pytest's source fixture directory is long.
    base=Path.home()/'.aiwolf-test-runs'; base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='r-',dir=base) as directory:
        try: yield Path(directory)/'r'
        finally:
            assert Path(directory).resolve().is_relative_to(base.resolve())


@pytest.fixture
def package(tmp_path,short_runs):
    if not (AGENT / 'lib/task_runner.py').exists(): pytest.skip('optional AIagent v1 installation unavailable')
    repo = tmp_path / 'repo'; repo.mkdir(); (repo / 'tests').mkdir()
    (repo / 'calc.py').write_text('def total(xs): return 0\n')
    (repo / 'tests/test_base.py').write_text('def test_total():\n    from calc import total\n    assert total([1,2]) == 3\n')
    for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture']):
        subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
    template = dict(contract_version=1, task_id='overnight-test', repo_root=str(repo), goal='Implement exact integer sum', risk='red',
                    read_list=['calc.py'], allow_edit=['calc.py'], allow_new=[], protected=['tests/**'], test_paths=['tests'],
                    test_command=['-q'], extra_read_context=[], context='Input is a list of signed integers.',
                    invariants=['Preserve exact integer arithmetic'], acceptance=['returns sum, including empty input'],
                    required_tests=['tests/test_base.py::test_total'], checks=[],
                    design_gate={'decision':'NOT_REQUIRED','reviewer':'Fixture Reviewer','reason':'synthetic arithmetic fixture'},
                    limits=dict(max_file_bytes=262144,max_total_edit_bytes=524288,max_model_output_bytes=131072,max_fixes=0,run_timeout_s=30,max_tokens=1024))
    units = [dict(id='sum', template=template, test_slot='tests/test_unit_sum.py', after_reads=[], runner_launches=2)]
    second = copy.deepcopy(units[0]); second.update(id='product', test_slot='tests/test_unit_product.py', after_reads=['tests/test_unit_sum.py'])
    second['template'].update(task_id='overnight-product', goal='Add product(xs) while retaining total(xs)',
                              context='Integer product of the list; empty product is one.', acceptance=['product is exact and empty product is one'])
    units.append(second)
    read = ['calc.py', 'tests/test_base.py', 'tests/test_unit_sum.py', 'tests/test_unit_product.py']
    m = dict(version=1, issuer='synthetic test authority', repo_root=str(repo), agent_root=str(AGENT),
             not_before=time.time()-1, expires_at=time.time()+1200,
             limits=dict(seconds=1200, upper_calls=14, qwen_calls=8, plan_attempts=2, packet_bytes=262144, output_bytes=1048576),
             providers={'planner':dict(provider='gpt',model='gpt-5.6-sol',executable=sys.executable,cloud_read=read),
                        'reviewer':dict(provider='claude',model='claude-opus-4-8',executable=sys.executable,cloud_read=read)},
             quota_policy=dict(mode='bounded_calls',reserve_percent=20,snapshot=None), units=units)
    source = tmp_path / 'package.json'; source.write_text(json.dumps(m))
    return repo, m, source, short_runs


def raw(value):
    return dict(exit_code=0,error=None,stdout='',stderr='',seconds=.01,result=value,usage={'input_tokens':10,'output_tokens':5})


def cloud(_provider, _config, packet, *_):
    if packet['stage'] == 'reviewer': return raw({'verdict':'APPROVED','reason':'fixed acceptance and allocated tests reviewed'})
    product = packet['unit_id'] == 'product'
    code = ('def test_product():\n    from calc import product\n    assert product([]) == 1\n    assert product([-2,3]) == -6\n' if product else
            'def test_signed_sum():\n    from calc import total\n    assert total([]) == 0\n    assert total([-2,3]) == 1\n')
    node = 'test_product' if product else 'test_signed_sum'
    return raw(dict(action='IMPLEMENT',reason='No new design decision',goal='Implement the fixed arithmetic requirement',
                    context='Retain all preceding behavior and tests',test_code=code,required_tests=[packet['unit']['test_slot']+'::'+node]))


def child_cloud(*_): return raw({'verdict':'APPROVED','reason':'synthetic scoped contract/candidate/completion reviewed'})


def runner(args, directory):
    import task_runner, task_apply, task_state
    if args[0] == 'plan':
        run = task_runner.plan(Path(args[args.index('--contract')+1]), Path(args[args.index('--runs-root')+1]))
        return {'run':str(run)}
    run = Path(args[args.index('--run')+1])
    if args[0] == 'resume':
        contract = json.loads((run / 'contract.json').read_text())
        code = 'def total(xs): return sum(xs)\n'
        if contract['task_id'] == 'overnight-product': code += '\ndef product(xs):\n    result = 1\n    for x in xs: result *= x\n    return result\n'
        reply = {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'files':{'calc.py':code},'notes':''})}}],
                 'usage':{'prompt_tokens':5,'completion_tokens':5},'model':'fixture-qwen'}
        s = task_runner.execute_run(run, lambda _: reply)
    else: s = task_apply.mutate(run, approval=Path(args[args.index('--approval')+1]))
    return {'run':str(run),'phase':s['phase']}


def start(package, **hooks):
    _, m, source, root = package; source.write_text(json.dumps(m))
    return Controller.start(source, root, cloud=hooks.get('cloud',cloud), child_cloud=hooks.get('child_cloud',child_cloud),
                            runner=hooks.get('runner',runner), ready=hooks.get('ready',lambda:True))


def reopen(c, **hooks):
    return Controller(c.path, cloud=hooks.get('cloud',cloud), child_cloud=hooks.get('child_cloud',child_cloud),
                      runner=hooks.get('runner',runner), ready=hooks.get('ready',lambda:True))


def fail_call(*_): pytest.fail('unexpected provider call')


def failure_details(c):
    child=c.s.get('child')
    if not child or not child['path']: return c.s['reason']
    state=json.loads((c.artifact(child['path'])/'state.json').read_text(encoding='utf-8'))
    rs=state.get('job',{}).get('runner_state',{})
    return {'parent':c.s['reason'],'runner_reason':rs.get('reason'),'gates':rs.get('gates')}


def test_design_is_independently_hash_bound(): policy.check_design()


def test_two_units_without_chat_and_readonly_repeat(package):
    c = start(package); s = c.drive()
    assert s['phase'] == 'COMPLETE', s['reason']
    assert s['index'] == 2 and 'def product' in (package[0]/'calc.py').read_text()
    assert s['upper_reserved'] == 14 and s['qwen_reserved'] == 8
    assert sum(a['upper_spent'] for a in s['allocations'].values()) == 10
    before = (c.path/'state.json').read_bytes()
    again = reopen(c, cloud=fail_call, child_cloud=fail_call, runner=fail_call)
    assert again.drive()['phase'] == 'COMPLETE'
    assert (c.path/'state.json').read_bytes() == before
    result = report(again)
    assert result['llm_calls_for_report'] == 0 and result['completed_units'] == 2
    assert result['by_model']['local-qwen']['calls_observed'] >= 2
    contract = s['receipts'][1]['proposal']['packet']['contract']
    assert 'tests/test_unit_sum.py::test_signed_sum' in contract['required_tests']
    assert 'tests/test_unit_sum.py' in contract['read_list']


@pytest.mark.parametrize('change', ['upper','qwen','self','model','unknown','forward','slot','infra','duplicate'])
def test_authority_rejected_before_start(package, change):
    m = package[1]
    if change == 'upper': m['limits']['upper_calls'] = 13
    if change == 'qwen': m['limits']['qwen_calls'] = 7
    if change == 'self': m['providers']['reviewer'] = copy.deepcopy(m['providers']['planner'])
    if change == 'model': m['providers']['reviewer']['model'] = 'claude-nonexistent'
    if change == 'unknown': m['units'][0]['bypass'] = True
    if change == 'forward': m['units'][0]['after_reads'] = ['tests/test_unit_product.py']
    if change == 'slot': m['units'][0]['test_slot'] = 'tests/test_base.py'
    if change == 'infra': m['units'][0]['test_slot'] = 'tests/test_overnight_backdoor.py'
    if change == 'duplicate': m['units'][1]['test_slot'] = m['units'][0]['test_slot']
    with pytest.raises(Exception): start(package, cloud=fail_call)
    assert not package[3].exists()


@pytest.mark.parametrize('mode', ['not_ready','scope','strict','quota'])
def test_no_paid_dispatch_when_unready(package, mode):
    m = package[1]
    if mode == 'scope': m['providers']['planner']['cloud_read'] = []
    if mode == 'strict': m['quota_policy']['mode'] = 'strict_reserve'
    if mode == 'quota':
        snapshot = package[2].parent/'quota.json'
        snapshot.write_text(json.dumps({'gpt':{'observed_at':time.time(),'windows':[{'used_percent':95,'resets_at':time.time()+1000}]}}))
        m['quota_policy']['snapshot'] = str(snapshot)
    c = start(package, cloud=fail_call, ready=lambda:mode!='not_ready'); s = c.drive()
    assert s['phase'] in ('BLOCKED','PAUSED_QUOTA')
    assert not s['calls']


def test_plan_revision_cap(package):
    calls = []
    def revise(*args):
        calls.append(args[2]['stage'])
        return raw({'verdict':'REVISE','reason':'need stronger test'}) if args[2]['stage']=='reviewer' else cloud(*args)
    c = start(package,cloud=revise); s=c.drive()
    assert s['phase']=='BLOCKED' and calls==['planner','reviewer']*2
    assert not (package[0]/'tests/test_unit_sum.py').exists()
    reopen(c)


def test_needs_design_stops_without_game_write(package):
    c=start(package,cloud=lambda *_:raw(dict(action='NEEDS_DESIGN',reason='interface unspecified',goal='',context='',test_code='',required_tests=[])))
    assert c.drive()['phase']=='NEEDS_DESIGN'
    assert 'return 0' in (package[0]/'calc.py').read_text()


def test_unknown_delivery_never_resends(package):
    def interrupt(*_): raise KeyboardInterrupt()
    c=start(package,cloud=interrupt); assert c.drive()['phase']=='PAUSED'
    s=reopen(c,cloud=fail_call).drive()
    assert s['phase']=='UNKNOWN_DELIVERY' and len(s['calls'])==1


@pytest.mark.parametrize('point',['raw','install','child_create','child_complete'])
def test_crash_recovery_reuses_same_evidence(package, monkeypatch, point):
    c=start(package); fired=[]
    if point in ('raw','install'):
        method='write_json' if point=='raw' else 'atomic_bytes'; original=getattr(c.b['task_state'],method)
        def crash(path,value):
            result=original(path,value)
            if not fired and ((point=='raw' and path.name=='raw.json') or (point=='install' and path.name=='test_unit_sum.py')):
                fired.append(1); raise KeyboardInterrupt()
            return result
        with monkeypatch.context() as patch:
            patch.setattr(c.b['task_state'],method,crash)
            assert c.drive()['phase']=='PAUSED'
    elif point=='child_create':
        original=c.mark
        def crash(phase=None,reason=None):
            if not fired and c.s['child'] and c.s['child']['path']:
                fired.append(1); raise KeyboardInterrupt()
            return original(phase,reason)
        with monkeypatch.context() as patch:
            patch.setattr(c,'mark',crash); assert c.drive()['phase']=='PAUSED'
    else:
        original=c.child_outputs
        def crash(*args):
            if not fired: fired.append(1); raise KeyboardInterrupt()
            return original(*args)
        with monkeypatch.context() as patch:
            patch.setattr(c,'child_outputs',crash); assert c.drive()['phase']=='PAUSED'
    calls=[]
    def track(*args): calls.append((args[2]['unit_id'],args[2]['stage'])); return cloud(*args)
    s=reopen(c,cloud=track).drive()
    assert s['phase']=='COMPLETE',s['reason']
    if point=='raw': assert ('sum','planner') not in calls
    assert len(list((c.path/'children/u0/runs').iterdir()))==1


def test_stop_requires_explicit_clear(package):
    c=start(package,cloud=fail_call); c.request_stop()
    assert c.drive()['phase']=='PAUSED'
    assert reopen(c,cloud=fail_call).drive()['phase']=='PAUSED'
    c=reopen(c); c.request_stop(clear=True)
    assert c.drive()['phase']=='COMPLETE'


def test_stop_propagates_to_active_child(package):
    c=start(package)
    def stop(*args):
        c.request_stop(); return child_cloud(*args)
    c.child_hooks['cloud']=stop
    assert c.drive()['phase']=='PAUSED'
    path=c.artifact(c.s['child']['path'])
    assert (path/'stop').exists()
    c=reopen(c); c.request_stop(clear=True)
    assert c.drive()['phase']=='COMPLETE',c.s['reason']


def test_common_lock_excludes_d058_and_parent(package):
    c=start(package)
    with c.b['task_state'].lock(c.lock_path(c.m)):
        with pytest.raises(Exception,match='lock busy'): c.drive()
        with pytest.raises(Exception,match='lock busy'): Controller.start(package[2],package[3])


@pytest.mark.parametrize('mode',['source','test','package','clock','deadline'])
def test_changes_and_time_stop_before_call(package,mode,monkeypatch):
    c=start(package,cloud=fail_call)
    if mode=='source': (package[0]/'calc.py').write_text('manual change')
    if mode=='test': (package[0]/'tests/unexpected.py').write_text('manual change')
    if mode=='package': package[2].write_text('{}')
    if mode=='clock': monkeypatch.setattr('overnight_lib.engine.time.time',lambda:c.s['started_at']-5)
    if mode=='deadline': c.mono_deadline=time.monotonic()-1
    if mode=='package':
        with pytest.raises(Exception,match='package changed'): c.drive()
    else: assert c.drive()['phase'] in ('INVALID','LIMIT_REACHED')


@pytest.mark.parametrize('mode',['counter','expected','event','extra'])
def test_state_tamper_rejected(package,mode):
    c=start(package)
    if mode=='counter': c.s['allocations']['sum']['upper_spent']=1
    if mode=='expected': c.s['expected']['calc.py']='0'*64
    if mode=='event': c.s['events'][0]['index']=1
    if mode=='extra': c.s['backdoor']=True
    c.write()
    with pytest.raises(Exception): reopen(c)


def test_proposal_and_approval_tamper_rejected(package,monkeypatch):
    c=start(package)
    def interrupt(): raise KeyboardInterrupt()
    monkeypatch.setattr(c,'install',interrupt)
    assert c.drive()['phase']=='PAUSED'
    c.s['proposal']['approval']['model']='gpt-6-astra'; c.write()
    with pytest.raises(Exception,match='binding'): reopen(c)


def test_local_cli_no_config_does_not_start(tmp_path,monkeypatch):
    import run_overnight
    monkeypatch.setattr(run_overnight,'ROOT',tmp_path)
    with pytest.raises(ValueError,match='No workpackage'): run_overnight.main([])


def test_no_workspace_write_transport(tmp_path,monkeypatch):
    from autodev_lib import process
    captured=[]
    def execute(argv,*args): captured.append(argv); return dict(exit_code=1,error='fixture',stdout='',stderr='',seconds=0)
    monkeypatch.setattr(process,'execute',execute)
    provider.invoke('gpt',{'executable':sys.executable,'model':'gpt-5.6-sol'},{'stage':'planner','unit':{'test_slot':'tests/test_allocated.py'}},tmp_path,1,10000)
    assert 'read-only' in captured[0] and '--add-dir' not in captured[0]
    assert 'workspace-write' not in captured[0] and 'shell_tool' in captured[0]


@pytest.mark.parametrize('interruptions',[1,2])
def test_runner_relaunches_spend_original_finite_grant(package,interruptions):
    from autodev_lib.engine import Pause
    fired=[]
    def interrupted(args,directory):
        if args[0]=='resume' and len(fired)<interruptions:
            fired.append(1); raise Pause('PAUSED','synthetic runner interruption')
        return runner(args,directory)
    c=start(package,runner=interrupted)
    for _ in range(interruptions):
        assert c.drive()['phase']=='PAUSED'
        c=reopen(c,runner=interrupted)
    state=c.drive()
    assert state['phase']==('COMPLETE' if interruptions==1 else 'BLOCKED'),state['reason']
    assert state['qwen_reserved']==8
    assert len(list((c.path/'children/u0/runs').iterdir()))==1
    assert len(fired)==interruptions


def test_partial_apply_recovers_existing_journal(package,monkeypatch):
    c=start(package)
    import task_apply
    original=task_apply.atomic_bytes; fired=[]
    def interrupt(path,data):
        result=original(path,data)
        if path==package[0]/'calc.py' and not fired:
            fired.append(1); raise KeyboardInterrupt()
        return result
    with monkeypatch.context() as patch:
        patch.setattr(task_apply,'atomic_bytes',interrupt)
        assert c.drive()['phase']=='PAUSED'
    assert 'sum(xs)' in (package[0]/'calc.py').read_text()
    s=reopen(c).drive()
    assert s['phase']=='COMPLETE',s['reason']


@pytest.mark.parametrize('drift',[False,True])
def test_hard_crash_after_child_creation_reuses_discovered_child(package,monkeypatch,drift):
    c=start(package); original=c.mark
    def crash(phase=None,reason=None):
        if c.s['child'] and c.s['child']['path']: raise SystemExit('synthetic hard crash')
        return original(phase,reason)
    with monkeypatch.context() as patch:
        patch.setattr(c,'mark',crash)
        with pytest.raises(SystemExit): c.drive()
    persisted=json.loads((c.path/'state.json').read_text())
    assert persisted['child']['path'] is None
    if drift: (package[0]/'calc.py').write_text('manual bytes after child creation')
    resumed=reopen(c); s=resumed.drive()
    assert s['phase']==('INVALID' if drift else 'COMPLETE'),failure_details(resumed)
    assert len(list((c.path/'children/u0/runs').iterdir()))==1


def test_saved_packet_tamper_rejected(package):
    c=start(package,cloud=lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    assert c.drive()['phase']=='PAUSED'
    (c.path/'calls/u0-a1-planner/packet.json').write_text('{}')
    with pytest.raises(Exception,match='packet changed'): reopen(c)


def test_allocated_test_conflict_after_approval_never_overwritten(package,monkeypatch):
    c=start(package); original=c.install
    def conflict():
        (package[0]/'tests/test_unit_sum.py').write_text('manual bytes')
        return original()
    monkeypatch.setattr(c,'install',conflict)
    assert c.drive()['phase']=='INVALID'
    assert (package[0]/'tests/test_unit_sum.py').read_text()=='manual bytes'


def test_qwen_loss_after_parent_review_prevents_child_cloud(package):
    healthy=[True]
    def losing(*args):
        result=cloud(*args)
        if args[2]['stage']=='reviewer': healthy[0]=False
        return result
    c=start(package,cloud=losing,child_cloud=fail_call,ready=lambda:healthy[0])
    assert c.drive()['phase']=='BLOCKED'
    assert c.s['child'] is None and len(c.s['calls'])==2


def test_busy_resume_does_not_clear_stop_request(package):
    c=start(package); c.request_stop(); assert c.drive()['phase']=='PAUSED'
    with c.b['task_state'].lock(c.lock_path(c.m)):
        with pytest.raises(Exception,match='lock busy'): c.drive(clear_stop=True)
    assert (c.path/'stop').exists()


def test_invalid_semantic_response_is_known_failed_not_unknown(package):
    def invalid(*args):
        result=cloud(*args); result['result']['required_tests'].append('tests/test_base.py::test_total')
        return result
    c=start(package,cloud=invalid)
    assert c.drive()['phase']=='BLOCKED'
    step=c.s['calls']['u0-a1-planner']
    assert step['status']=='FAILED' and step['raw_sha256'] is not None
    again=reopen(c,cloud=fail_call)
    assert again.drive()['phase']=='BLOCKED' and len(again.s['calls'])==1
    assert report(again)['known_total_tokens']==15


def test_long_native_runtime_path_rejected_before_creation(package):
    import os
    if os.name!='nt': pytest.skip('Windows native cwd constraint')
    package[1]['units'][0]['template']['task_id']='x'*64
    with pytest.raises(ValueError,match='runtime path too long'):
        policy.runtime_paths(package[1],package[3]/('long-'*25))
