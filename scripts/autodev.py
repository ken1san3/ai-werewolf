#!/usr/bin/env python3
"""Run a finite authorized Qwen-first queue, with automatic upper-model reviews."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

from autodev_lib.policy import Error, validate, input_paths, check_design,loads
from autodev_lib.engine import Campaign


def report(c):
    known_input=known_output=unknown=0
    measured=[(step.get('usage'),step['provider']) for step in c.s['steps'].values()]
    jobs=[json.loads((c.path/'jobs'/(job_id+'.json')).read_text(encoding='utf-8')) for job_id in c.s['receipts']]
    jobs.append(c.s['job'])
    for job in jobs:
        if job.get('run'):
            rs=c.b['task_state'].read_state(Path(job['run']))
            measured.extend((call.get('usage'),'qwen') for call in rs.get('usage',[]))
    for usage,provider in measured:
        if not isinstance(usage,dict):usage={}
        inp=usage.get('input_tokens',usage.get('prompt_tokens'))
        out=usage.get('output_tokens',usage.get('completion_tokens'))
        if provider=='claude' and type(inp) is int:
            for key in ('cache_creation_input_tokens','cache_read_input_tokens'):
                if type(usage.get(key)) is int:inp+=usage[key]
        if type(inp) is int and inp>=0:known_input+=inp
        else:unknown+=1
        if type(out) is int and out>=0:known_output+=out
        else:unknown+=1
    completed=c.s['index']
    return {'phase':c.s['phase'],'completed_jobs':completed,'cloud_calls_reserved':c.s['cloud_calls'],
            'local_calls_reserved':c.s['local_calls'],'known_input_tokens':known_input,'known_output_tokens':known_output,
            'unknown_token_fields':unknown,'tokens_per_completed_job':(known_input+known_output)/completed if completed and not unknown else None,
            'llm_calls_for_report':0,'runner_usage':'Includes linked v1 run measurements; local reservations are not measured calls.',
            'scope':'orchestrator and linked v1 calls, including failed/retried attempts; no API dollar estimate','meaning':'scoped job review, not canonical game Phase approval'}


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['start','resume','status','stop','report','doctor','dry-run'])
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--campaign',type=Path)
    parser.add_argument('--runs-root',type=Path,default=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'AIwolf'/'autodev')
    args=parser.parse_args()
    try:
        if args.action in ('doctor','dry-run','start'):
            if not args.manifest or args.campaign:raise Error('--manifest required; no --campaign')
            if args.action in ('doctor','dry-run'):
                m=loads(args.manifest.read_text(encoding='utf-8'));validate(m);check_design()
                plan=[]
                for j in m['jobs']:
                    stages=['independent_review'] if j['kind']=='review' else ['qwen_draft','upper_contract','qwen_runner','risk_review','apply','upper_completion']
                    plan.append({'id':j['id'],'stages':stages,'input_paths':input_paths(j)})
                print(json.dumps({'jobs':plan,'cloud_calls':0,'source_writes':0,'note':'static plan only; runtime gate/quota may pause'},ensure_ascii=False,indent=2));return 0
            c=Campaign.launch(args.manifest,args.runs_root)
        else:
            if not args.campaign or args.manifest:raise Error('--campaign required; no --manifest')
            c=Campaign(args.campaign)
        if args.action in ('start','resume'):
            state=c.s if args.action=='start' else c.drive();print(json.dumps({'campaign':str(c.path),'phase':state['phase'],'reason':state['reason'],'handoff':str(c.path/'handoff.json')},ensure_ascii=False))
            return 0 if state['phase']=='COMPLETE' else 4 if state['phase']=='INVALID' else 3
        if args.action=='stop':(c.path/'stop').write_text('operator requested stop\n',encoding='utf-8')
        print(json.dumps(report(c) if args.action=='report' else {'campaign':str(c.path),'phase':c.s['phase'],'reason':c.s['reason'],'job_index':c.s['index']},ensure_ascii=False,indent=2))
        return 0
    except Exception as error:
        print(str(error),file=sys.stderr)
        return 5 if 'lock busy' in str(error) else 4


if __name__=='__main__':raise SystemExit(main())
