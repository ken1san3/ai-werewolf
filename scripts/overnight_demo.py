"""Create a fresh two-unit arithmetic fixture. Does not call models or edit the game."""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from autodev_lib.policy import ROOT, Error
from overnight_lib.policy import validate


def create(root, agent, planner_exe, reviewer_exe):
    root = Path(root).absolute(); agent = Path(agent).resolve(strict=True)
    if root.resolve().is_relative_to(ROOT) or root.resolve().is_relative_to(agent):
        raise Error('demo must be outside the game and AIagent')
    if root.exists(): raise Error('demo directory already exists; use a new empty destination')
    root.mkdir(parents=True); root = root.resolve(strict=True)
    repo = root / 'repo'; repo.mkdir(); (repo / 'tests').mkdir()
    (repo / 'calc.py').write_text('def total(xs):\n    return 0\n', encoding='utf-8', newline='\n')
    (repo / 'tests/test_baseline.py').write_text('def test_total():\n    from calc import total\n    assert total([1, 2]) == 3\n', encoding='utf-8', newline='\n')
    for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=AIwolf fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'synthetic baseline']):
        subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, timeout=15)
    c = dict(contract_version=1, task_id='demo-sum', repo_root=str(repo), goal='Implement total(xs): exact integer sum, including empty input returning zero.',
             risk='red', read_list=['calc.py'], allow_edit=['calc.py'], allow_new=[], protected=['tests/**'], test_paths=['tests'], test_command=['-q'],
             extra_read_context=[], context='Synthetic infrastructure fixture only. xs is a finite list of signed integers. No input validation is required.',
             invariants=['Exact integer arithmetic', 'Do not mutate xs'], acceptance=['Correct sum for empty, positive, negative and mixed integer lists'],
             required_tests=['tests/test_baseline.py::test_total'], checks=[],
             design_gate={'decision':'NOT_REQUIRED','reviewer':'D059 independently approved synthetic verification scope','reason':'Arithmetic fixture has fixed signature and semantics; no game design decision.'},
             limits=dict(max_file_bytes=262144,max_total_edit_bytes=524288,max_model_output_bytes=131072,max_fixes=1,run_timeout_s=300,max_tokens=2048))
    units = [dict(id='sum',template=c,test_slot='tests/test_sum_acceptance.py',after_reads=[],runner_launches=2)]
    second = copy.deepcopy(units[0]); second.update(id='product',test_slot='tests/test_product_acceptance.py',after_reads=['tests/test_sum_acceptance.py'])
    second['template'].update(task_id='demo-product',goal='Add product(xs): exact integer product; empty input returns one. Preserve total(xs).',
                              context='xs is a finite list of signed integers. No input validation is required. total(xs) must keep working.',
                              acceptance=['Exact product for empty, zero-containing, positive, negative and mixed integer lists','Existing total behavior remains correct'])
    units.append(second)
    read = ['calc.py','tests/test_baseline.py','tests/test_sum_acceptance.py','tests/test_product_acceptance.py']
    now = time.time()
    m = dict(version=1,issuer='User-authorized D059 infrastructure verification: newly generated arithmetic files only',
             repo_root=str(repo),agent_root=str(agent),not_before=now-5,expires_at=now+3600,
             limits=dict(seconds=3600,upper_calls=14,qwen_calls=16,plan_attempts=2,packet_bytes=262144,output_bytes=1048576),
             providers={'planner':dict(provider='gpt',model='gpt-5.6-sol',executable=str(planner_exe),cloud_read=read),
                        'reviewer':dict(provider='claude',model='claude-opus-4-8',executable=str(reviewer_exe),cloud_read=read)},
             quota_policy=dict(mode='bounded_calls',reserve_percent=20,snapshot=None),units=units)
    validate(m)
    source = root / 'workpackage.json'; source.write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return source, Path.home() / '.aiwolf-runs'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'AIwolf/fixtures'/('overnight-'+uuid.uuid4().hex))
    parser.add_argument('--agent-root',type=Path,default=Path('C:/AIagent/agent'))
    parser.add_argument('--planner-exe',type=Path,default=Path.home()/'AppData/Roaming/npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe')
    parser.add_argument('--reviewer-exe',type=Path,default=Path.home()/'.local/bin/claude.exe')
    args=parser.parse_args(); source, runs = create(args.root,args.agent_root,args.planner_exe,args.reviewer_exe)
    print(json.dumps({'package':str(source),'runs_root':str(runs),'provider_calls':0,
                      'next_command':'python scripts/run_overnight.py start --package "'+str(source)+'" --runs-root "'+str(runs)+'"'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    try: main()
    except Exception as e: print(str(e),file=sys.stderr); raise SystemExit(4)
