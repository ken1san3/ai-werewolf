"""Finite synthetic Phase 6 capability baseline. One generation per case; no repair/game."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from ai_client.discussion.context import canonical_json_bytes
from ai_client.llm.prompt import project_brain_input, canonical_prompt_json
from ai_client.llm.types import LLMBrainConfig, DiscussionChatConfig, LLMMessage
from ai_client.llm.decision import parse_llm_output, DecisionValidationError
from scripts.phase6_context_probe import provider_body, request, runtime, template, tokens, measure_body, wire_bytes
from tests.fixtures.phase6_conversation_cases import cases
from tests.fixtures.phase6_evidence import create_private_evidence_container

CONFIG = LLMBrainConfig(short_chat=DiscussionChatConfig())


def structure_reference(projection):
    """Expose the existing speech-act shapes, without any suggested speech text."""
    schema = json.loads(canonical_json_bytes(projection.decision_schema))
    definitions = schema['$defs']
    selected = {}
    def include(name):
        if name in selected: return
        selected[name] = definitions[name]
        def visit(value):
            if isinstance(value, dict):
                if '$ref' in value: include(value['$ref'].removeprefix('#/$defs/'))
                for item in value.values(): visit(item)
            elif isinstance(value, list):
                for item in value: visit(item)
        visit(definitions[name])
    include('speech_act')
    return {'$ref':'#/$defs/speech_act', '$defs':selected}


def example(value):
    """T405 request-local method, evaluated only here, never installed in product."""
    trigger = value['capture']['trigger']
    if trigger['kind'] not in ('INITIAL_CHAT', 'PEER_CHAT'): return None
    option = next((x for x in value['grounding']['allowed_decisions'] if x['kind'] == 'chat'), None)
    peers = [p for p in value['grounding']['current']['alive_player_ids'] if p != value['context']['player_id']]
    if option is None or not peers: return None
    act = {'kind': 'QUESTION', 'addressee_player_id': peers[0], 'subject_player_id': None,
           'topic': 'STRATEGY', 'source': None}
    text, reaction = 'What evidence would help you choose a vote?', None
    if trigger['kind'] == 'PEER_CHAT':
        source = trigger['source']
        event = next((r for r in value['memory']['records'] if r['source'] == source), None)
        if event is None or source['visibility'] != 'PUBLIC' or len(event['actor_player_ids']) != 1: return None
        peer = event['actor_player_ids'][0]
        if peer not in peers: return None
        question = (event['text_excerpt'] or '').rstrip().endswith('?')
        act = {'kind': 'ANSWER' if question else 'REBUTTAL', 'addressee_player_id': peer,
            'in_reply_to': json.loads(canonical_json_bytes(source)),
            'source_interpretation': 'QUESTION' if question else 'CLAIM', 'topic': 'STRATEGY',
            'stance': 'UNCERTAIN' if question else 'OPPOSE', 'evidence': []}
        text = ('I need specific public evidence before choosing a vote.' if question else
                'I disagree: that statement alone does not justify a vote.')
        reaction = {'trigger': json.loads(canonical_json_bytes(source)), 'score': 50, 'reason': 'OTHER_AUTHORIZED'}
    return {'decision': {'kind':'chat', 'option_id':option['option_id'], 'message':text},
        'discussion': {'schema_version':'aiwolf.discussion-proposal.v1',
            'base_revision':value['capture']['base_revision'], 'decision_kind':'chat', 'option_id':option['option_id'],
            'speech_act':act, 'reaction':reaction, 'assessment_updates':[], 'claim_updates':[],
            'relation_updates':[], 'strategy_update':None, 'co_judgment':None, 'pre_vote_reassessment':None}}


def project(case, variant):
    p = project_brain_input(case.request, config=CONFIG)
    if variant == 'baseline': return p
    if variant not in ('candidate', 'structure'): raise ValueError('unknown variant')
    if variant == 'structure':
        if p.discussion_capture.trigger.kind not in ('INITIAL_CHAT','PEER_CHAT'): return p
        system = p.messages[0].content + (
            '\nSpeech-act structure reference only, not a response or a chosen act. '
            'Write your own message about the current conversation and choose its matching act. '
            'This fragment describes discussion.speech_act; still return the complete response. '
            'Every EvidenceRef must come from grounding.allowed_evidence_refs. '
            'Use the actual speaker/source for replies and actual prior assessment for changes. '
            'NONE remains valid when its meaning fits.\n') + json.dumps(structure_reference(p), separators=(',', ':'))
        return with_system(p, system)
    item = example(p.canonical_input)
    if item is None: return p
    # Validate the example against the unchanged schema AND semantic validator.
    parse_llm_output(json.dumps(item), projection=p)
    system = p.messages[0].content + (' Format example, not a suggested statement or belief. Choose your own text and '
        'matching act; do not copy the example. NONE remains valid when no act fits.\n') + json.dumps(item, separators=(',', ':'))
    return with_system(p, system)


def with_system(p, system):
    messages = (LLMMessage('system', system), *p.messages[1:])
    serialized = canonical_prompt_json(messages, p.decision_schema)
    if len(serialized.encode()) + 454 > 32768: raise ValueError('byte budget exceeded')
    # This test adapter holds the baseline memory/state fixed. Actual-token
    # preflight below is mandatory before any direct provider request.
    return replace(p, messages=messages, prompt_bytes=len(serialized.encode()),
        prompt_sha256=hashlib.sha256(serialized.encode()).hexdigest(), token_proxy_units=None)


def evaluate(case, projection, raw):
    errors, flags = [], []
    try:
        parsed = parse_llm_output(raw, projection=projection)
    except DecisionValidationError as e:
        parsed = None; errors.append('OUTPUT_INVALID:'+e.code.value)
    try: value = json.loads(raw)
    except (ValueError, TypeError): value = {}
    decision = value.get('decision', {}) if isinstance(value, dict) else {}
    discussion = value.get('discussion', {}) if isinstance(value, dict) else {}
    if not isinstance(decision,dict): decision = {}
    if not isinstance(discussion,dict): discussion = {}
    act = discussion.get('speech_act', {})
    act = act.get('kind') if isinstance(act,dict) else None
    text = decision.get('message') or decision.get('comment') or ''
    if not isinstance(text,str): text = ''
    norm = lambda s: ' '.join(s.casefold().split())
    copy_found = any(len(norm(text)) >= 50 and len(norm(text).split()) >= 8 and
        norm(text) == norm(getattr(r, 'message', '') or getattr(r, 'comment', '') or '')
        and getattr(r, 'player_id', None) != 'player-6' for r in case.request.history.records)
    if copy_found: flags.append('EXACT_LONG_COPY')
    if re.match(r"(?i)^I(?: am|'m)\b", text): flags.append('INTRO_OPENING')
    if text.rstrip().endswith('?') and act != 'QUESTION': flags.append('QUESTION_LABEL_MISMATCH')
    if case.expected_acts and act not in case.expected_acts: flags.append('EXPECTED_ACT_NOT_OBSERVED')
    # Free text can negate, quote, bluff, or disclose strategically. Regex absence
    # is not proof of semantic truth. Independent fields remain UNKNOWN until
    # the finite final-output review, never silently promoted to PASS.
    return {'structural_pass': parsed is not None, 'hard_pass': False if errors else None,
        'semantic_pass': None, 'failure_reasons': errors, 'screen_flags': flags,
        'decision_kind': decision.get('kind'), 'speech_act': act,
        'exact_long_copy': copy_found, 'manual_text_review_required': True}


def prepare(out, *, candidate_variant='candidate', baseline_source=None):
    meta = runtime(); manifest = {'runtime': meta, 'cases': [], 'generation_calls': 0,
                                 'task_id':'T408' if candidate_variant=='structure' else 'T406'}
    prior = None
    if baseline_source is not None:
        prior = json.loads((baseline_source/'plan.json').read_text(encoding='utf-8'))
        if prior['runtime'] != meta: raise RuntimeError('baseline runtime differs')
    for case in cases():
        variants = {}
        for variant in ('baseline', candidate_variant):
            p = project(case, variant); body = provider_body(p)
            if variant=='baseline' and prior:
                measurement = next(c for c in prior['cases'] if c['case_id']==case.case_id)['variants']['baseline']
                if measurement['input_hash'] != hashlib.sha256(wire_bytes(body)).hexdigest():
                    raise RuntimeError('baseline bytes changed')
            else:
                measurement = measure_body(body, meta['context_per_slot'], proxy=p.token_proxy_units)
            if measurement['remaining_tokens'] < 0: raise RuntimeError('actual context overflow')
            if measurement['provider_request_bytes'] > 65536: raise RuntimeError('request byte overflow')
            variants[variant] = measurement
        manifest['cases'].append({'case_id':case.case_id, 'category':case.category, 'role':case.role,
            'trigger':case.request.discussion.trigger.kind, 'expected_hard_constraints':case.hard_rules,
            'expected_semantic_properties':case.semantic_rule, 'expected_acts':case.expected_acts,
            'variants':variants})
    out.mkdir(parents=True, exist_ok=True)
    with (out/'plan.json').open('x', encoding='utf-8') as f: json.dump(manifest,f,indent=2)
    if baseline_source:
        for name in ('baseline-results.json','baseline-locator.json','baseline-manual.json'):
            with (out/name).open('xb') as f: f.write((baseline_source/name).read_bytes())
        (out/'baseline.claim').write_text('Historical baseline reuse only; no generation authorized.',encoding='utf-8')
    print('Prepared 32 cases; no generation. Baseline reused:',bool(baseline_source))


def run(out, variant):
    manifest = json.loads((out/'plan.json').read_text(encoding='utf-8'))
    if runtime() != manifest['runtime']: raise RuntimeError('provider identity/runtime changed')
    projections = [(case, project(case, variant)) for case in cases()]
    for (case,p), entry in zip(projections, manifest['cases'], strict=True):
        body = provider_body(p); measured = entry['variants'][variant]
        if case.case_id != entry['case_id'] or hashlib.sha256(wire_bytes(body)).hexdigest() != measured['input_hash']:
            raise RuntimeError('frozen input mismatch')
    baseline_rows = {}
    if variant != 'baseline':
        baseline_rows = {r['case_id']:r for r in json.loads((out/'baseline-results.json').read_text())['rows']}
        if len(baseline_rows) != 32 or any(r['generation_status'] != 'GENERATED' for r in baseline_rows.values()):
            raise RuntimeError('complete baseline required before candidate')
    # Exclusive claim consumes this variant even on interruption: no auto retry.
    with (out/(variant+'.claim')).open('x') as f: f.write(datetime.now(timezone.utc).isoformat())
    private = create_private_evidence_container(ROOT/'logs/phase6-private-evidence',
        evidence_kind='synthetic', task_id=manifest.get('task_id','T406')+variant.upper(), created_at_utc=datetime.now(timezone.utc))
    (out/(variant+'-locator.json')).write_text(json.dumps({'path':str(private)}), encoding='utf-8')
    monitor_log = (private/'monitor-stdio.txt').open('x')
    monitor = subprocess.Popen([sys.executable, str(ROOT/'scripts/monitor_phase6_gpu.py'),
        '--output',str(private/'gpu.jsonl'), '--max-seconds','1800','--watch-pid',str(os.getpid())],
        stdout=monitor_log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    rows=[]; started=time.monotonic()
    try:
        with (private/'raw.jsonl').open('x',encoding='utf-8') as rawfile:
            for (case,p),entry in zip(projections,manifest['cases'],strict=True):
                measured=entry['variants'][variant]; body=provider_body(p)
                if runtime() != manifest['runtime']: raise RuntimeError('provider changed; stop')
                row={k:entry[k] for k in ('case_id','category','role','trigger','expected_hard_constraints','expected_semantic_properties')}
                row.update(input_hash=measured['input_hash'], prompt_tokens_actual=measured['base_prompt_tokens'])
                t=time.monotonic()
                reused = baseline_rows.get(case.case_id)
                if reused and reused['input_hash'] == row['input_hash']:
                    row = dict(reused); row['generation_status'] = 'REUSED_BASELINE'
                    row['reused_from'] = 'baseline'; row['new_provider_calls'] = 0
                    rows.append(row)
                    (out/(variant+'-results.json')).write_text(json.dumps({'variant':variant,'rows':rows,
                        'duration_real':time.monotonic()-started},indent=2),encoding='utf-8')
                    print(case.case_id,'REUSED_BASELINE',flush=True); continue
                row['new_provider_calls'] = 1
                try:
                    result=request('/v1/chat/completions',body,timeout=45)
                    message=result['choices'][0]['message']; text=message.get('content') or ''
                    row.update(evaluate(case,p,text)); row['generation_status']='GENERATED'
                    row['completion_tokens']=result.get('usage',{}).get('completion_tokens')
                    row['provider_prompt_tokens']=result.get('usage',{}).get('prompt_tokens')
                    row['finish_reason']=result['choices'][0].get('finish_reason')
                    # Preserve final outputs only. Do not request/store reasoning.
                    rawfile.write(json.dumps({'case_id':case.case_id,'input':body,'final_content':text,
                        'usage':result.get('usage'), 'finish_reason':row['finish_reason']},ensure_ascii=False)+'\n');rawfile.flush()
                except Exception as error:
                    row.update(generation_status='ERROR',hard_pass=None,semantic_pass=None,
                        decision_kind=None,speech_act=None,completion_tokens=None,
                        failure_reasons=[type(error).__name__])
                row['latency_real']=time.monotonic()-t; rows.append(row)
                (out/(variant+'-results.json')).write_text(json.dumps({'variant':variant,'rows':rows,
                    'duration_real':time.monotonic()-started},indent=2),encoding='utf-8')
                print(case.case_id,row['generation_status'],row.get('speech_act'),flush=True)
                if row['generation_status']=='ERROR': break
                if row['provider_prompt_tokens'] != row['prompt_tokens_actual']:
                    raise RuntimeError('tokenizer/generation mismatch; stop')
    finally:
        monitor.terminate();monitor.wait(timeout=10);monitor_log.close()
    print('Finished',len(rows),'cases; no retry. Final-text semantic review pending.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--run',choices=('baseline','candidate','structure'))
    parser.add_argument('--candidate-variant',choices=('candidate','structure'),default='candidate')
    parser.add_argument('--reuse-baseline',type=Path)
    args=parser.parse_args()
    if args.prepare == bool(args.run): parser.error('choose exactly one of --prepare or --run')
    if args.prepare: prepare(args.output,candidate_variant=args.candidate_variant,baseline_source=args.reuse_baseline)
    else: run(args.output,args.run)
