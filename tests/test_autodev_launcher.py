import argparse
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_autodev as launcher


def result(code=0, data=None):
    return subprocess.CompletedProcess([], code, json.dumps(data or {}), '')


def test_doctor_failure_never_starts():
    calls = []
    def call(*args):
        calls.append(args[0]); return result(4)
    assert launcher.run('manifest', Path('unused'), call=call) == 4
    assert calls == ['doctor']


def test_start_then_report_preserves_primary_failure(tmp_path):
    calls = []
    def call(*args):
        calls.append(args[0])
        return result(3, {'campaign': str(tmp_path)}) if args[0] == 'start' else result()
    assert launcher.run('manifest', tmp_path, call=call) == 3
    assert calls == ['doctor', 'start', 'report']


@pytest.mark.parametrize('phase, expected, code', [('PAUSED', ['status','resume','report'], 0),
    ('COMPLETE', ['status','report'], 0), ('UNKNOWN_DELIVERY', ['status','report'], 3),
    ('NEEDS_USER', ['status','report'], 3), ('INVALID', ['status','report'], 4)])
def test_resume_or_terminal_report(tmp_path, phase, expected, code):
    calls = []
    def call(*args):
        calls.append(args[0]); return result(data={'phase': phase})
    assert launcher.run('campaign', tmp_path, call=call) == code
    assert calls == expected


def test_unknown_state_and_missing_start_receipt_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        launcher.run('campaign', tmp_path, call=lambda *args: result(data={'phase':'invented'}))
    calls = []
    def call(*args):
        calls.append(args[0]); return result()
    assert launcher.run('manifest', tmp_path, call=call) == 4
    assert calls == ['doctor', 'start']


def test_report_failure_not_hidden(tmp_path):
    assert launcher.run('campaign', tmp_path, call=lambda *args:
        result(4) if args[0]=='report' else result(data={'phase':'COMPLETE'})) == 4


@pytest.mark.parametrize('kind,stage', [('manifest','doctor'), ('campaign','status')])
def test_check_does_not_execute(tmp_path,kind,stage):
    calls=[]
    def call(*args):
        calls.append(args[0]); return result()
    assert launcher.run(kind,tmp_path,check=True,call=call)==0
    assert calls==[stage]


def test_local_config_exact_keys_and_repo_relative_paths(tmp_path, monkeypatch):
    config=tmp_path/'config.json'; manifest=tmp_path/'日本語 space.json';manifest.write_text('{}')
    monkeypatch.setattr(launcher,'ROOT',tmp_path);monkeypatch.setattr(launcher,'CONFIG',config)
    args=argparse.Namespace(manifest=None,campaign=None)
    for data in ({}, {'manifest':str(manifest),'campaign':str(tmp_path)}, {'manifest':None}):
        config.write_text(json.dumps(data))
        with pytest.raises(ValueError):launcher.target(args)
    config.write_text(json.dumps({'manifest':manifest.name}))
    assert launcher.target(args)==('manifest',manifest)


def test_subprocess_uses_argument_array(monkeypatch):
    class Child:
        stdout=io.StringIO('{}\n')
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def wait(self): return 0
    def popen(argv, **kwargs):
        assert isinstance(argv,list) and argv[-1]=='C:/path with space/a&b.json'
        assert not kwargs.get('shell',False)
        assert kwargs.get('stderr') is None
        return Child()
    monkeypatch.setattr(launcher.subprocess,'Popen',popen)
    launcher.invoke('doctor','--manifest','C:/path with space/a&b.json')


def test_receipt_visible_before_child_exit(tmp_path,monkeypatch):
    scripts=tmp_path/'scripts';scripts.mkdir()
    (scripts/'autodev.py').write_text(
        "import time\nfrom pathlib import Path\nprint('campaign-receipt',flush=True)\n"
        "deadline=time.monotonic()+3\n"
        "while not Path('ack').exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        "raise SystemExit(0 if Path('ack').exists() else 9)\n")
    class Sink(io.StringIO):
        def write(self,text):
            if 'campaign-receipt' in text: (tmp_path/'ack').touch()
            return super().write(text)
    monkeypatch.setattr(launcher,'ROOT',tmp_path)
    monkeypatch.setattr(launcher.sys,'stdout',Sink())
    outcome=launcher.invoke('start')
    assert outcome.returncode==0 and 'campaign-receipt' in outcome.stdout
