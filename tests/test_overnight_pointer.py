"""Pointer failures must never multiply an authorized run or dispatch twice."""
import json
from pathlib import Path

import pytest

from tests.test_overnight_controller import package, short_runs, start
from overnight_lib.engine import Controller
from overnight_lib.pointer import find_run, write_pointer, source_from_temps
from overnight_lib.policy import Error


def test_replace_retry_and_failed_tmp(tmp_path):
    run = tmp_path / 'run'; run.mkdir()
    local = tmp_path / 'overnight.local.json'; local.write_text('{"package":"x"}')
    calls = []; sleeps = []
    def fail(src, dst):
        calls.append((src, dst)); raise PermissionError('locked')
    with pytest.raises(Error, match='LOCAL_POINTER_UPDATE_FAILED'):
        write_pointer(local, run, replace=fail, sleep=sleeps.append)
    assert len(calls) == 4 and sleeps == [.1, .2, .1*3]
    assert json.loads(local.read_text()) == {'package':'x'}
    assert json.loads(calls[-1][0].read_text()) == {'run':str(run.resolve())}


def test_transient_replace_succeeds(tmp_path):
    import os
    run=tmp_path/'run';run.mkdir();local=tmp_path/'local.json';calls=[]
    def transient(src,dst):
        calls.append(1)
        if len(calls)<3: raise PermissionError('temporary')
        os.replace(src,dst)
    write_pointer(local,run,replace=transient,sleep=lambda _:None)
    assert len(calls)==3 and json.loads(local.read_text())['run']==str(run.resolve())
    assert not list(tmp_path.glob('*.tmp'))


def test_restart_adopts_orphan_with_failed_pointer(package):
    repo,m,source,root=package
    local=source.parent/'overnight.local.json';local.write_text(json.dumps({'package':str(source)}))
    def broken(path):
        def fail(*_): raise PermissionError('locked')
        write_pointer(local,path,replace=fail,sleep=lambda _:None)
    with pytest.raises(Error,match='LOCAL_POINTER_UPDATE_FAILED'):
        Controller.launch(source,root,on_created=broken,local_pointer=local,ready=lambda:True)
    paths=list(root.glob('overnight-*'));assert len(paths)==1
    assert Controller(paths[0]).s['calls']=={}
    recovered=Controller.launch(source,root,on_created=lambda p:write_pointer(local,p),
                                local_pointer=local,prepare_only=True,ready=lambda:True)
    assert recovered.path==paths[0].resolve()
    assert len(list(root.glob('overnight-*')))==1 and recovered.s['calls']=={}
    assert Path(json.loads(local.read_text())['run'])==recovered.path


def test_orphan_without_tmp_is_detected(package):
    c=start(package);_,_,source,root=package
    found=find_run(Controller,source,root)
    assert found.path==c.path and found.s['calls']=={}


def test_duplicate_matching_runs_refused(package):
    start(package);start(package);_,_,source,root=package
    with pytest.raises(Error,match='AMBIGUOUS'):find_run(Controller,source,root)


def test_matching_state_tamper_refused(package):
    c=start(package);_,_,source,root=package
    state=json.loads((c.path/'state.json').read_text());state['deadline']+=1
    (c.path/'state.json').write_text(json.dumps(state))
    with pytest.raises(Error):find_run(Controller,source,root)


def test_terminal_run_is_reused(package):
    c=start(package);c.mark('BLOCKED','fixture');_,_,source,root=package
    found=Controller.launch(source,root,prepare_only=True)
    assert found.path==c.path and found.s['phase']=='BLOCKED'
    assert len(list(root.glob('overnight-*')))==1


def test_recover_does_not_create(package):
    _,_,source,root=package
    with pytest.raises(Error,match='NOT_FOUND'):Controller.launch(source,root,prepare_only=True)
    assert not root.exists()


def test_invalid_tmp_fails_closed(package):
    _,_,source,root=package;local=source.parent/'local.json'
    local.with_name(local.name+'.x.tmp').write_text('{')
    with pytest.raises(Error,match='RECOVERY_INVALID'):find_run(Controller,source,root,local)


def test_incomplete_run_fails_closed(package):
    _,_,source,root=package;(root/('overnight-'+'a'*32)).mkdir(parents=True)
    with pytest.raises(Error,match='incomplete'):find_run(Controller,source,root)


def test_missing_pointer_uses_verified_tmp(package):
    c=start(package);repo,_,source,root=package
    local=repo/'overnight.local.json'
    local.with_name(local.name+'.x.tmp').write_text(json.dumps({'run':str(c.path)}))
    assert source_from_temps(local,root,lambda _:Controller)==source.resolve()
    assert not local.exists() and c.s['calls']=={}
