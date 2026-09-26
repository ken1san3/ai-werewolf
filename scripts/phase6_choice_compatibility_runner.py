"""T508: one Gemma 32-case run, choice64/output448, strict private evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import phase6_recovery_runner as r
from scripts import phase6_choice_budget_probe as budget

base, gb = r.base, r.gb
SEED, K, MAX_CALLS = 4242027, 3, 128
ARM = 'filter'
EVIDENCE = ROOT/'logs/t507-choice-budget/formal-binary-v2/result.json'
EVIDENCE_SHA = '79e8bb4aca7e48a1cd523258f0ced6b7c48b043464a4b1f6b2452a69ba11c511'
REVIEW = ROOT/'Docs/ai/handoffs/tasks/T508_GEMMA_CHOICE_TOOL_REVIEW.md'
VARIANT = 'choice64_output448'


def contract():
    return dict(task='T508', model='gm12', seed=SEED, choice=64, output=448, context=8192,
                k=K, maximum_calls=MAX_CALLS, row_tokens_max=1408, retry=0, repair=0,
                block_seconds=1200, outer_seconds=1320, request_seconds=60,
                stop_on_first_choice_failure=True,
                sampling={k:v for k,v in base.SAMPLING.items() if k not in ('seed', 'max_tokens')},
                user_authorization='2026-09-27_T508_EXPLICIT_PERMISSION')


def bindings():
    return {str(p.relative_to(ROOT)): base.file_hash(p) for p in (budget.DESIGN, budget.REVIEW, REVIEW)}


def offline_evidence():
    result_bytes = EVIDENCE.read_bytes()
    if r.sha(result_bytes) != EVIDENCE_SHA:
        raise r.Stop('OFFLINE_BINDING')
    result = gb.strict_json(result_bytes.decode('utf-8'))
    frozen_bytes = (EVIDENCE.parent/'freeze.json').read_bytes()
    if (result['gate'] != 'OFFLINE_CAPACITY_CANDIDATE'
            or r.sha(frozen_bytes) != result['freeze_sha256']):
        raise r.Stop('OFFLINE_FREEZE_BINDING')
    return gb.strict_json(frozen_bytes.decode('utf-8')), result['freeze_sha256']


def prepare(out, review_sha):
    if base.file_hash(budget.DESIGN) != budget.DESIGN_SHA or base.file_hash(budget.REVIEW) != budget.REVIEW_SHA:
        raise r.Stop('DESIGN_BINDING')
    if base.file_hash(REVIEW) != review_sha or '\n`APPROVED`\n' not in REVIEW.read_text(encoding='utf-8'):
        raise r.Stop('TOOL_REVIEW_BINDING')
    frozen, offline_freeze_sha = offline_evidence()
    if not base.port_free():
        raise r.Stop('NON_OWNED_LISTENER')
    model, quant, server, _ = base.PROFILES['gm12']
    identities = [base.file_identity(p) for p in [Path(model), server, *sorted(server.parent.glob('*.dll'))]]
    if identities != frozen['profiles']['gm12'] or base.file_identity(r.CONFIG) != frozen['config']:
        raise r.Stop('MODEL_CONFIG_RUNTIME_DRIFT')
    now = datetime.now(timezone.utc)
    plan = dict(contract=contract(), bindings=bindings(), source=r.sources(), cases=r.input_identity(),
        model=identities[0], runtime_files=identities[1:], argv=base.launch_args('gm12'),
        config=base.file_identity(r.CONFIG), quantization=quant, offline_sha256=EVIDENCE_SHA,
        offline_freeze_sha256=offline_freeze_sha,
        started_at_utc=now.isoformat(), deadline_utc=(now+timedelta(hours=6)).isoformat())
    out.mkdir(parents=True, exist_ok=False)
    r.write(out/'plan.json', plan, exclusive=True)
    r.write(out/'freeze.json', {'plan_sha256':base.file_hash(out/'plan.json')}, exclusive=True)
    (out/'calls').mkdir()
    print('T508_PLAN_FROZEN', flush=True)


def verify(out, *, files=True):
    plan = r.read(out/'plan.json')
    _, offline_freeze_sha = offline_evidence()
    if (base.file_hash(out/'plan.json') != r.read(out/'freeze.json')['plan_sha256']
            or plan['contract'] != contract() or plan['bindings'] != bindings()
            or plan['source'] != r.sources() or plan['cases'] != r.input_identity()
            or plan['argv'] != base.launch_args('gm12') or plan['config'] != base.file_identity(r.CONFIG)
            or base.file_hash(EVIDENCE) != plan['offline_sha256']
            or offline_freeze_sha != plan['offline_freeze_sha256']):
        raise r.Stop('FROZEN_SOURCE_CHANGED')
    if files and any(base.file_identity(x['path']) != x for x in [plan['model'], *plan['runtime_files']]):
        raise r.Stop('MODEL_RUNTIME_DRIFT')
    return plan


def empty_row(case):
    return dict(case_id=case.case_id, seed=SEED, status='NOT_RUN', choice_status='CHOICE_NOT_RUN',
        structural_pass=False, filter_pass=False, provider_calls=0, new_provider_calls=0,
        attempts=[], accepted_attempt=None, final_output_sha256=None, content_status='UNKNOWN',
        semantic_outcome=0)


def process_case(case, p, dispatch):
    row = empty_row(case)
    body = r.baseline_body(p, 'gm12', SEED)
    def call(stage, request):
        text, meta = dispatch(stage, request)
        row['provider_calls'] += int(meta.get('consumed', False))
        row['new_provider_calls'] = row['provider_calls']
        row['attempts'].append(dict(meta, stage=stage))
        return text, meta.get('finish_reason')
    choice_body = gb.choice_body(body, p)
    choice_body['max_tokens'] = 64
    raw, finish = call('choice', choice_body)
    if finish == 'length':
        status = 'CHOICE_LENGTH'
    elif raw is None or finish != 'stop':
        status = 'CHOICE_UNKNOWN'
    elif raw == '':
        status = 'CHOICE_EMPTY'
    else:
        try:
            gb.strict_json(raw)
        except ValueError:
            status = 'CHOICE_JSON_INVALID'
        else:
            try:
                choice = gb.validate_choice(raw, p)
            except ValueError:
                status = 'CHOICE_SCHEMA_INVALID'
            else:
                status = 'CHOICE_ACCEPTED'
    row.update(status=status, choice_status=status)
    if status != 'CHOICE_ACCEPTED':
        return row, None
    row.update(choice_sha256=r.sha(raw.encode('utf-8')), selected_kind=choice['speech_act_kind'],
               selected_fact_count=len(choice['authoritative_fact_ids']))
    try:
        body = gb.output_body(body, choice, p)
    except gb.gc2.NoLegalGrounding:
        row['status'] = 'NO_LEGAL_GROUNDING'
        return row, None
    body['max_tokens'] = 448
    for attempt in range(K):
        body['seed'] = SEED+1009*attempt
        raw, finish = call('output'+str(attempt), body)
        row['attempts'][-1]['choice_sha256'] = row['choice_sha256']
        if finish == 'length':
            checked = dict(structural_pass=False, filter_pass=False, reject_code='OUTPUT_LENGTH')
        elif raw is None or finish != 'stop':
            row.update(status='OUTPUT_UNKNOWN', structural_pass=False, filter_pass=False)
            return row, None
        else:
            checked = r.mechanical(case, p, raw, choice)
        row['attempts'][-1].update(checked)
        if checked['filter_pass']:
            row.update(checked, status='ACCEPTED', accepted_attempt=attempt, content_status='GENERATED',
                       final_output_sha256=r.sha(raw.encode('utf-8')))
            return row, raw
    row.update(status='OUTPUT_K_EXHAUSTED', structural_pass=False, filter_pass=False)
    return row, None


def execute_call(out, private, key, stage, body, **kwargs):
    expected = 64 if stage == 'choice' else 448
    if body['max_tokens'] != expected:
        raise r.Stop('BUDGET_VARIANT')
    return r.execute_call(out, private, key, stage, body, budget_variant=VARIANT, call_cap=MAX_CALLS, **kwargs)


def run_block(out):
    plan = verify(out)
    if datetime.now(timezone.utc) >= datetime.fromisoformat(plan['deadline_utc'])-timedelta(seconds=60):
        raise r.Stop('DEADLINE')
    if not base.port_free():
        raise r.Stop('NON_OWNED_LISTENER')
    result = dict(model='gm12', arm=ARM, seed=SEED, status='STARTING', integrity=False,
        plan_sha256=base.file_hash(out/'plan.json'), rows=[empty_row(c) for c in base.cases()])
    proc = monitor = private = None
    started = time.monotonic()
    end = started+1200
    def save():
        result.update(real_duration_sec=time.monotonic()-started, clock_domain='REAL',
                      provider_calls=sum(x['provider_calls'] for x in result['rows']))
        r.write(out/'result.json', result)
    with r.lease(out):
        r.write(out/'claim.json', {'started_at_utc':r.stamp()}, exclusive=True)
        base._RUN_DEADLINE = end
        try:
            save()
            private = base.create_private_evidence_container(ROOT/'logs/phase6-private-evidence',
                evidence_kind='synthetic', task_id='T508GEMMA', created_at_utc=datetime.now(timezone.utc))
            r.write(out/'locator.json', {'path':str(private)}, exclusive=True)
            with (private/'server.log').open('xb') as log:
                proc = subprocess.Popen(plan['argv'], cwd=Path(plan['argv'][0]).parent,
                    stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            while True:
                if proc.poll() is not None:
                    raise r.Stop('LOAD_EXIT')
                if time.monotonic()-started > 180:
                    raise r.Stop('LOAD_TIMEOUT')
                try:
                    if base.request('/health', timeout=2).get('status') == 'ok':
                        break
                except (base.httpx.HTTPError, TimeoutError, base.StopComparison):
                    pass
                time.sleep(.25)
            if proc.poll() is not None or not base.owned_listener(proc) or proc.poll() is not None:
                raise r.Stop('OWNERSHIP')
            identity = base.runtime()
            if Path(identity['model_path']).resolve() != Path(plan['model']['path']).resolve():
                raise r.Stop('MODEL_IDENTITY')
            r.write(private/'runtime.json', identity, exclusive=True)
            result['runtime'] = base.safe_runtime(identity)
            with (private/'monitor.log').open('xb') as log:
                monitor = subprocess.Popen([sys.executable, str(ROOT/'scripts/monitor_phase6_gpu.py'),
                    '--output', str(private/'gpu.jsonl'), '--max-seconds', '1200', '--watch-pid', str(os.getpid())],
                    stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            def verify_now():
                verify(out, files=False)
                if proc.poll() is not None or not base.owned_listener(proc) or proc.poll() is not None or base.runtime() != identity:
                    raise r.Stop('RUNTIME_OWNERSHIP_DRIFT')
            for index, (case, p) in enumerate(r.entries()):
                if end-time.monotonic() < 60:
                    result['status'] = 'DEADLINE'
                    break
                r.write_bytes(private/(case.case_id+'.projection.json'), r.canonical_json_bytes(p.canonical_input))
                dispatch = lambda stage, body: execute_call(out, private, case.case_id, stage, body,
                    remaining=lambda:end-time.monotonic(), verify_now=verify_now)
                row, final = process_case(case, p, dispatch)
                r.write(private/(case.case_id+'.final.json'), {'final_content':final,
                    'final_output_sha256':row['final_output_sha256']}, exclusive=True)
                result['rows'][index] = row
                save()
                print('T508', case.case_id, row['status'], flush=True)
                if row['choice_status'] != 'CHOICE_ACCEPTED':
                    result['status'] = 'CHOICE_STOP'
                    break
            else:
                result['status'] = 'COMPLETE'
            verify(out)
            result['integrity'] = True
        except Exception as error:
            result.update(status='STOPPED', stop_reason=str(error) if type(error) is r.Stop else 'EXECUTION_ERROR')
            if private is not None:
                import traceback
                r.write(private/'failure.json', {'kind':type(error).__name__, 'traceback':traceback.format_exc()}, exclusive=True)
        finally:
            base._RUN_DEADLINE = None
            result.update(base.cleanup_owned(monitor, proc))
            if result['owned_processes_remaining'] or any(k.endswith('_cleanup_error') for k in result):
                result.update(status='STOPPED', stop_reason='CLEANUP_FAILED', integrity=False)
            if private is not None:
                for row in result['rows']:
                    if row['status'] != 'NOT_RUN':
                        continue
                    prefix = private.name+'-'+row['case_id']+'-'
                    markers = sorted((out/'calls').glob(prefix+'*.json'))
                    if markers:
                        row.update(status='ERROR', choice_status='CHOICE_UNKNOWN', provider_calls=len(markers),
                                   new_provider_calls=len(markers))
                        for marker in markers:
                            stage = marker.stem[len(prefix):]
                            outcome = private/(row['case_id']+'.'+stage+'.outcome.json')
                            consumed = private/(row['case_id']+'.'+stage+'.consumed.json')
                            item = r.read(outcome if outcome.exists() else consumed if consumed.exists() else marker)
                            row['attempts'].append(dict(item, stage=stage, consumed=True))
                if (private/'server.log').exists():
                    base.attach_performance(result, private)
                result['private_artifacts'] = {p.name:base.file_hash(p) for p in private.iterdir() if p.is_file()}
            result['durable_call_count'] = len(list((out/'calls').glob('*.json')))
            if sum(x['provider_calls'] for x in result['rows']) != result['durable_call_count']:
                result.update(status='STOPPED', stop_reason='CALL_ACCOUNTING', integrity=False)
            result['choice_compatible'] = result['integrity'] and all(x['choice_status'] == 'CHOICE_ACCEPTED' for x in result['rows'])
            save()
            r.write(out/'seal.json', {'result_sha256':base.file_hash(out/'result.json')}, exclusive=True)
    return 0 if result['integrity'] else 2


def supervised_run(out):
    verify(out)
    if (out/'claim.json').exists() or (out/'outer').exists():
        raise r.Stop('RUN_ALREADY_CLAIMED')
    private = base.create_private_evidence_container(ROOT/'logs/phase6-private-evidence',
        evidence_kind='synthetic', task_id='T508OUTER', created_at_utc=datetime.now(timezone.utc))
    r.write(out/'outer-locator.json', {'path':str(private)}, exclusive=True)
    result = r.supervise([sys.executable, str(Path(__file__).resolve()), '--output', str(out.resolve()), '--block'],
        out/'outer', raw_directory=private, limit_seconds=1320)
    if (result.get('error_kind') or result['exit_code'] != 0 or result['outer_timeout']
            or not result['ownership_complete'] or result['owned_alive_after'] != 0):
        raise r.Stop('OUTER_STOPPED')
    print('T508_SEALED', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--run', action='store_true')
    mode.add_argument('--block', action='store_true')
    parser.add_argument('--review-sha')
    args = parser.parse_args()
    try:
        if args.prepare:
            prepare(args.output, args.review_sha)
        elif args.block:
            return run_block(args.output)
        else:
            supervised_run(args.output)
    except Exception as error:
        print('T508_STOPPED', str(error) if type(error) is r.Stop else 'COMMAND_ERROR', flush=True)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
