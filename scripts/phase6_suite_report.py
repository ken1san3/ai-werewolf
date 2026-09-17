"""Combine immutable suite measurements with explicit final-output annotations."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.phase6_conversation_suite import cases, example, project


def summarize(rows):
    new=[r for r in rows if r.get('new_provider_calls') == 1]
    return {'cases':len(rows),'new_provider_calls':len(new),'reused_cases':len(rows)-len(new),
        'structural_pass':sum(r.get('structural_pass') is True for r in rows),
        'hard':dict(Counter('PASS' if r.get('hard_pass') is True else 'FAIL' if r.get('hard_pass') is False else 'UNKNOWN' for r in rows)),
        'semantic':dict(Counter('PASS' if r.get('semantic_pass') is True else 'FAIL' if r.get('semantic_pass') is False else 'UNKNOWN' for r in rows)),
        'speech_acts':dict(Counter(r.get('speech_act') for r in rows)),
        'secrecy_violations':sum(r.get('secrecy_violation') is True for r in rows),
        'ability_result_observation':dict(Counter(r['grounding_result'] for r in rows if r['case_id'].startswith(('G04','G05','G06')))),
        'exact_long_peer_copy':sum(r.get('exact_long_copy') is True for r in rows),
        'example_text_copy_new_generations':sum(r.get('example_text_copy') is True for r in new),
        'prompt_tokens_new_calls':sum(r.get('provider_prompt_tokens') or 0 for r in new),
        'completion_tokens_new_calls':sum(r.get('completion_tokens') or 0 for r in new)}


def report(suite, output):
    combined={}; known={c.case_id:c for c in cases()}
    for variant in ('baseline','candidate'):
        results_path=suite/(variant+'-results.json'); manual_path=suite/(variant+'-manual.json')
        measured=json.loads(results_path.read_text(encoding='utf-8'))
        manual=json.loads(manual_path.read_text(encoding='utf-8'))
        private=Path(json.loads((suite/(variant+'-locator.json')).read_text())['path'])
        raw_path=private/'raw.jsonl'
        raw={r['case_id']:r for r in (json.loads(line) for line in raw_path.read_text(encoding='utf-8').splitlines())}
        rows=[]
        if set(manual['rows']) != {r['case_id'] for r in measured['rows']}:raise ValueError('annotation coverage mismatch')
        for original in measured['rows']:
            row=dict(original);cid=row['case_id'];note=manual['rows'][cid]
            if not row.get('structural_pass') and note['hard_pass'] is True:raise ValueError('invalid structure cannot pass hard')
            if row['generation_status']=='REUSED_BASELINE':
                source=next(r for r in combined['baseline']['rows'] if r['case_id']==cid)
                if source['input_hash']!=row['input_hash']:raise ValueError('reused hash mismatch')
                output_hash=source['final_output_sha256'];copied=source['example_text_copy']
            else:
                text=raw[cid]['final_content'];output_hash=hashlib.sha256(text.encode()).hexdigest()
                try:value=json.loads(text);spoken=value.get('decision',{}).get('message')
                except (ValueError,AttributeError):spoken=None
                sample=example(project(known[cid],'baseline').canonical_input)
                copied=bool(variant=='candidate' and sample and spoken==sample['decision']['message'])
            row.update(note);row.update(final_output_sha256=output_hash,example_text_copy=copied,
                manual_text_review_required=False)
            rows.append(row)
        combined[variant]={'rows':rows,'summary':summarize(rows),
            'measurement_sha256':hashlib.sha256(results_path.read_bytes()).hexdigest(),
            'annotations_sha256':hashlib.sha256(manual_path.read_bytes()).hexdigest(),
            'private_raw_sha256':hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            'duration_real':measured['duration_real']}
    combined['verdict']='CANDIDATE_NOT_ADOPTED; NO_GAME; product unchanged'
    basic_groups={'QUESTION':['G03-1','G03-2'], 'ANSWER':['G01-1','G01-2'],
                  'REBUTTAL':['G02-1','G02-2'], 'OPINION_CHANGE':['G04-1']}
    combined['basic_act_cases']={}
    for act,ids in basic_groups.items():
        metric={'case_ids':ids}
        for variant in ('baseline','candidate'):
            selected=[r for r in combined[variant]['rows'] if r['case_id'] in ids]
            metric[variant+'_label_correct']=sum(r['speech_act']==act for r in selected)
            metric[variant+'_fully_correct']=sum(r['speech_act']==act and r['semantic_pass'] is True for r in selected)
            if act=='ANSWER':metric[variant+'_content_answers_question']=sum(r.get('content_answers_question') is True for r in selected)
        combined['basic_act_cases'][act]=metric
    combined['limitations']=['one generation per distinct case/variant; no statistical generalization',
        'manual semantic judgments preserve UNKNOWN; no chain of thought',
        'non-disclosure is not a grounding failure; grounding capability may be unobserved',
        'candidate complete examples can induce imitation; act frequency alone is insufficient']
    output.write_text(json.dumps(combined,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:combined[k]['summary'] for k in ('baseline','candidate')},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();report(args.suite,args.output)
