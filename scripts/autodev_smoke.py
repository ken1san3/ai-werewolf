"""Explicit real-provider smoke on generated fixture data only; never reads game files."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from autodev_lib.engine import Campaign
from autodev import report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--codex',type=Path,required=True)
    p.add_argument('--agent',type=Path,default=Path('C:/AIagent/agent'))
    args=p.parse_args()
    root=args.root.absolute()/('smoke-'+uuid.uuid4().hex[:12]);repo=root/'fixture';(repo/'tests').mkdir(parents=True)
    original='def total(values): return 0\n';(repo/'calc.py').write_text(original,encoding='utf-8')
    (repo/'tests/test_calc.py').write_text('from calc import total\ndef test_empty(): assert total([])==0\ndef test_mixed(): assert total([1,-4,6])==3\n',encoding='utf-8')
    for command in (['init','-q'],['add','.'],['-c','user.name=Autodev smoke','-c','user.email=smoke@example.invalid','commit','-qm','generated fixture']):
        subprocess.run(['git','-C',str(repo),*command],check=True,capture_output=True)
    c=dict(contract_version=1,task_id='integer-sum',repo_root=str(repo),goal='Implement exact integer sum',risk='red',read_list=['calc.py','tests/test_calc.py'],allow_edit=['calc.py'],allow_new=[],protected=['tests/**'],test_paths=['tests'],test_command=['-q'],extra_read_context=[],context='Pure total(values) returns sum, including empty and signed integers.',invariants=['Return exact sum of all integer values'],acceptance=['empty and signed integers pass protected tests'],required_tests=['tests/test_calc.py::test_empty','tests/test_calc.py::test_mixed'],checks=[],design_gate={'decision':'NOT_REQUIRED','reviewer':'GPT-6 Astra / smoke fixture author','reason':'bounded pure sum fixture'},limits=dict(max_file_bytes=262144,max_total_edit_bytes=524288,max_model_output_bytes=131072,max_fixes=1,run_timeout_s=90,max_tokens=1024))
    m=dict(version=1,issuer='User requested unattended development infrastructure; generated fixture smoke only',repo_root=str(repo),agent_root=str(args.agent.absolute()),not_before=time.time()-1,expires_at=time.time()+600,max_seconds=600,max_cloud_calls=3,max_local_calls=8,max_attempts=1,max_packet_bytes=32768,max_output_bytes=1048576,
           providers={'gpt':{'model':'gpt-5.6-sol','executable':str(args.codex.absolute()),'cloud_read':['calc.py','tests/test_calc.py']}},quota_policy={'mode':'bounded_calls','reserve_percent':0,'snapshot':None},
           jobs=[dict(id='sum',kind='task',instruction='Review/implement the bounded integer sum fixture. This is a tiny infrastructure smoke, not a game feature. Approve if the exact stage contract is met; no unrelated scope or stylistic requirements.',read_list=['calc.py','tests/test_calc.py'],reviewer='gpt',author_model='local-qwen',template=c,draft=True)],apply=True)
    manifest=root/'manifest.json';manifest.write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8')
    campaign=Campaign.launch(manifest,root/'campaigns')
    state=campaign.s
    print(json.dumps({'phase':state['phase'],'reason':state['reason'],'report':report(campaign)},ensure_ascii=False),flush=True)
    return 0 if state['phase']=='COMPLETE' else 3


if __name__=='__main__':raise SystemExit(main())
