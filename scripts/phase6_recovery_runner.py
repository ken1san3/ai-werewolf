"""T506 finite, test-only paired experiment. Never sends game actions."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase6_model_comparison as base
from scripts import phase6_grounding_basis_probe as gb
from scripts.phase6_context_probe import wire_bytes
from scripts.phase6_probe_outer import supervise
from ai_client.discussion.context import canonical_json_bytes

SEEDS = (4242027, 4242028, 4242029)
ARMS = ('baseline', 'gb1', 'filter')
DESIGN = 'Docs/ai/design/PHASE6_QUALITY_RECOVERY_PROGRAM_DESIGN.md'
DESIGN_SHA = '0e1557fe1fc1aa00d23d81bc52016e5defbccb4dc70c6769da97a2f0636b1b3d'
REVIEW = 'Docs/ai/handoffs/tasks/T506_QUALITY_RECOVERY_DESIGN_REVIEW.md'
REVIEW_SHA = '11e20119e51c2de10fdd6650b823b2b3c065509160a0c820ebd2ac9a4f2ec331'
CONFIG = Path('C:/AIagent/agent/config.toml')
MAX_CALLS = 864
K = 3


class Stop(RuntimeError):
    """Fixed codes only; arbitrary exception messages never leave private storage."""


def stamp():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value, *, exclusive=False):
    with Path(path).open('x' if exclusive else 'w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, allow_nan=False, indent=2)
        f.flush()
        os.fsync(f.fileno())


def write_bytes(path, value):
    with Path(path).open('xb') as f:
        f.write(value)
        f.flush()
        os.fsync(f.fileno())


def read(path):
    return gb.strict_json(Path(path).read_text(encoding='utf-8'))


def contract():
    return dict(experiment='t506_recovery_v1', seeds=list(SEEDS), k=K,
                maximum_calls=MAX_CALLS, context=8192, request_seconds=60,
                block_seconds=1200, load_seconds=180, program_seconds=21600,
                repair=0, transport_retry=0, seed_stride=1009,
                sampling={k: v for k, v in base.SAMPLING.items() if k != 'seed'},
                filter_contract='legacy_strict_and_existing_exact_text_guard',
                bootstrap_seed=20260924, bootstrap_replicates=100000)


def sources():
    # Metadata hashing only, including transitive synthetic fixture dependencies.
    paths = set()
    for directory in ('ai_client', 'scripts', 'tests', 'protocol', 'content'):
        for p in (ROOT/directory).rglob('*'):
            if p.suffix in ('.py', '.json', '.yaml', '.yml') and p.is_file():
                paths.add(p)
    paths.add(ROOT/'pyproject.toml')
    return {p.relative_to(ROOT).as_posix(): base.file_hash(p) for p in sorted(paths)}


def entries():
    return [(c, base.project(c, 'baseline')) for c in base.cases()]


def input_identity():
    return [{'case_id': c.case_id, 'projection_sha256': p.prompt_sha256,
             **gb.catalog_identity(p)} for c, p in entries()]


def prepare(out, started):
    if base.file_hash(ROOT/DESIGN) != DESIGN_SHA or base.file_hash(ROOT/REVIEW) != REVIEW_SHA:
        raise Stop('DESIGN_BINDING')
    began = datetime.fromisoformat(started)
    now = datetime.now(timezone.utc)
    if began.tzinfo is None or began > now or now >= began+timedelta(hours=6):
        raise Stop('PROGRAM_TIME')
    out.mkdir(parents=True, exist_ok=True)
    if (out/'plan.json').exists():
        raise Stop('PLAN_EXISTS')
    profiles = {}
    for key in ('qw9', 'gm12'):
        model, quant, server, _ = base.PROFILES[key]
        profiles[key] = dict(model=base.file_identity(model), quantization=quant,
            argv=base.launch_args(key), runtime_files=[base.file_identity(p) for p in
                [server, *sorted(server.parent.glob('*.dll'))]])
    plan = dict(contract=contract(), source=sources(), cases=input_identity(), profiles=profiles,
        config=base.file_identity(CONFIG), design_sha256=DESIGN_SHA, review_sha256=REVIEW_SHA,
        started_at_utc=began.isoformat(), deadline_utc=(began+timedelta(hours=6)).isoformat(),
        rows=[dict(group=group, seed=seed, case_id=c.case_id, status='NOT_RUN')
              for group in ('qw9-baseline', 'qw9-gb1', 'qw9-filter', 'gm12-selected')
              for seed in SEEDS for c in base.cases()])
    write(out/'plan.json', plan, exclusive=True)
    write(out/'freeze.json', {'plan_sha256': base.file_hash(out/'plan.json')}, exclusive=True)
    (out/'calls').mkdir(exist_ok=False)
    print('PLAN_FROZEN', len(plan['rows']), flush=True)


def verify(out, model, *, files=True):
    plan = read(out/'plan.json')
    if (base.file_hash(out/'plan.json') != read(out/'freeze.json')['plan_sha256']
            or wire_bytes(plan['contract']) != wire_bytes(contract())
            or base.file_hash(ROOT/DESIGN) != DESIGN_SHA
            or base.file_hash(ROOT/REVIEW) != REVIEW_SHA
            or plan['source'] != sources()
            or plan['cases'] != input_identity()
            or base.file_identity(CONFIG) != plan['config']):
        raise Stop('FROZEN_SOURCE_CHANGED')
    profile = plan['profiles'][model]
    if profile['argv'] != base.launch_args(model):
        raise Stop('PROFILE_DRIFT')
    if files and any(base.file_identity(item['path']) != item
                     for item in [profile['model'], *profile['runtime_files']]):
        raise Stop('MODEL_RUNTIME_DRIFT')
    return plan


@contextmanager
def lease(out):
    path = out/'active-block.lock'
    write(path, {'pid': os.getpid(), 'started_at_utc': stamp()}, exclusive=True)
    try:
        yield
    finally:
        path.unlink()


def reserve(out, key, body):
    """Single lease serializes calls. Exclusive markers survive uncertain dispatch."""
    existing = list((out/'calls').glob('*.json'))
    if len(existing) >= MAX_CALLS:
        raise Stop('CALL_CAP')
    digest = sha(wire_bytes(body))
    if any(read(p)['wire_sha256'] == digest for p in existing):
        raise Stop('DUPLICATE_DISPATCH')
    marker = out/'calls'/(key+'.json')
    write(marker, dict(key=key, wire_sha256=digest, started_at_utc=stamp()), exclusive=True)
    return digest


def baseline_body(p, model, seed):
    body = base.body_for(p, base.PROFILES[model][0])
    body['seed'] = seed
    return body


def mechanical(case, p, raw, choice=None):
    try:
        if choice is not None:
            gb.validate_final(raw, choice, p)
        parsed = base.parse_llm_output(raw, projection=p)
    except (ValueError, base.DecisionValidationError):
        return {'structural_pass': False, 'filter_pass': False, 'reject_code': 'STRUCTURAL_INVALID'}
    guard = base._invalid_or_repeated_self_text(case.request, parsed.decision)
    return {'structural_pass': True, 'filter_pass': not guard,
            'reject_code': 'EXACT_TEXT_GUARD' if guard else 'NONE'}


def process_case(case, p, model, arm, seed, dispatch, *, replay=None):
    """Pure finite control flow; dispatch owns transport/evidence, never state/send."""
    row = dict(case_id=case.case_id, seed=seed, status='NOT_RUN', attempts=[],
        structural_pass=False, filter_pass=False, provider_calls=0, new_provider_calls=0,
        accepted_attempt=None, final_output_sha256=None)
    body = baseline_body(p, model, seed)
    final = None
    choice = None

    def call(stage, request):
        if replay is not None and stage in ('choice', 'output0'):
            item = replay.get(stage)
            if item is None:
                return None
            text, meta = item
            if meta['wire_sha256'] != sha(wire_bytes(request)):
                raise Stop('REPLAY_BINDING')
            meta = dict(meta, reused=True)
        else:
            text, meta = dispatch(stage, request)
            row['new_provider_calls'] += int(meta.get('consumed', False))
        row['provider_calls'] += int(meta.get('consumed', False))
        row['attempts'].append(dict(meta, stage=stage))
        return text

    if arm != 'baseline':
        raw = call('choice', gb.choice_body(body, p))
        if raw is None:
            row['status'] = 'CHOICE_ERROR'
            return row, None
        try:
            choice = gb.validate_choice(raw, p)
        except (ValueError, base.DecisionValidationError):
            row['status'] = 'CHOICE_INVALID'
            return row, None
        row.update(selected_kind=choice['speech_act_kind'], selected_fact_count=len(choice['authoritative_fact_ids']))
        try:
            body = gb.output_body(body, choice, p)
        except gb.gc2.NoLegalGrounding:
            row['status'] = 'NO_LEGAL_GROUNDING'
            return row, None
    for attempt in range(K if arm == 'filter' else 1):
        body['seed'] = seed + attempt*1009
        raw = call('output'+str(attempt), body)
        if raw is None:
            # Keep the last rejected raw for diagnosis, never its acceptance flags.
            row.update(status='OUTPUT_ERROR', structural_pass=False, filter_pass=False)
            return row, final
        final = raw
        checked = mechanical(case, p, raw, choice)
        row['attempts'][-1].update(checked)
        row.update(checked, final_output_sha256=sha(raw.encode('utf-8')))
        accepted = checked['filter_pass'] if arm == 'filter' else checked['structural_pass']
        if accepted:
            row.update(status='ACCEPTED', accepted_attempt=attempt)
            return row, final
        row['status'] = 'FILTER_EXHAUSTED' if arm == 'filter' else 'OUTPUT_INVALID'
    # An exhausted filter has no accepted output; last raw is retained for diagnosis.
    row['structural_pass'] = False
    return row, final


def block_name(model, arm, seed):
    if model not in ('qw9', 'gm12') or arm not in ARMS or seed not in SEEDS:
        raise Stop('BLOCK_KEY')
    return f'{model}-{arm}-{seed}'


def replay_for(out, seed, case):
    source = out/block_name('qw9', 'gb1', seed)
    seal = read(source/'seal.json')
    if base.file_hash(source/'result.json') != seal['result_sha256']:
        raise Stop('REPLAY_SOURCE_CHANGED')
    measured = read(source/'result.json')
    if not measured.get('integrity'):
        raise Stop('REPLAY_SOURCE_INVALID')
    row = next(r for r in measured['rows'] if r['case_id'] == case.case_id)
    private = Path(read(source/'locator.json')['path'])
    replay = {}
    for stage in ('choice', 'output0'):
        meta = next((a for a in row['attempts'] if a['stage'] == stage), None)
        if meta is None:
            continue
        request_path = private/(case.case_id+'.'+stage+'.request.bin')
        if base.file_hash(request_path) != meta['wire_sha256']:
            raise Stop('REPLAY_WIRE_CHANGED')
        text = None
        if meta['status'] == 'GENERATED':
            response_path = private/(case.case_id+'.'+stage+'.response.json')
            if base.file_hash(response_path) != meta['response_sha256']:
                raise Stop('REPLAY_RAW_CHANGED')
            response = read(response_path)
            text = response['choices'][0]['message']['content']
            if sha(text.encode('utf-8')) != meta['raw_sha256']:
                raise Stop('REPLAY_RAW_CHANGED')
        replay[stage] = (text, meta)
    return replay, seal['result_sha256']


def execute_call(out, private, key, stage, body, *, remaining, verify_now):
    if remaining() < base.REQUEST_SECONDS:
        raise Stop('DEADLINE')
    verify_now()
    payload = wire_bytes(body)
    write_bytes(private/(key+'.'+stage+'.request.bin'), payload)
    def sink(rendered):
        if 'grounding_basis_catalog' in body['messages'][1]['content']:
            gb.validate_native_rendered(body, 'choice' if stage == 'choice' else 'output', rendered)
        write_bytes(private/(key+'.'+stage+'.rendered.bin'), rendered.encode('utf-8'))
    measured = base.count_prompt(body, wire_payload=payload, private_sink=sink)
    if remaining() < base.REQUEST_SECONDS:
        raise Stop('DEADLINE')
    digest = reserve(out, private.name+'-'+key+'-'+stage, body)
    meta = dict(stage=stage, wire_sha256=digest, consumed=True, reused=False,
                status='STARTED', max_tokens=body['max_tokens'], started_at_utc=stamp(), **measured)
    # Durable private and public call markers precede dispatch; no retry on uncertainty.
    write(private/(key+'.'+stage+'.consumed.json'), meta, exclusive=True)
    started = time.monotonic()
    text = None
    try:
        response = base.request('/v1/chat/completions', body, timeout=60, wire_payload=payload)
        response_path = private/(key+'.'+stage+'.response.json')
        write(response_path, response, exclusive=True)
        meta['response_sha256'] = base.file_hash(response_path)
        item = response['choices'][0]
        content = item['message'].get('content')
        reason = item['message'].get('reasoning_content')
        usage = response['usage']
        if reason not in (None, '') or type(content) is not str:
            raise Stop('RESPONSE_CONTRACT')
        prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
        if type(prompt) is not int or prompt != measured['prompt_tokens_actual']:
            raise Stop('TOKEN_MISMATCH')
        if type(completion) is not int or not 0 <= completion <= body['max_tokens']:
            raise Stop('TOKEN_BUDGET')
        finish = base.fixed_value(item.get('finish_reason'), {'stop', 'length', 'content_filter'})
        meta.update(provider_prompt_tokens=prompt, completion_tokens=completion,
            finish_reason=finish, raw_sha256=sha(content.encode('utf-8')),
            status='GENERATED' if finish == 'stop' else 'LENGTH' if finish == 'length' else 'ERROR')
        text = content if finish == 'stop' else None
    except (base.httpx.HTTPError, TimeoutError):
        meta.update(status='TRANSPORT_ERROR')
    except Exception:
        meta['status'] = 'ERROR'
        raise
    finally:
        meta.update(ended_at_utc=stamp(), latency_real_sec=time.monotonic()-started)
        write(private/(key+'.'+stage+'.outcome.json'), meta, exclusive=True)
    return text, meta


def run_block(out, model, arm, seed):
    name = block_name(model, arm, seed)
    target = out/name
    target.mkdir(exist_ok=True)
    plan = verify(out, model)
    deadline = datetime.fromisoformat(plan['deadline_utc']).timestamp()
    if deadline-time.time() <= 60:
        raise Stop('DEADLINE')
    if not base.port_free():
        raise Stop('NON_OWNED_LISTENER')
    profile = plan['profiles'][model]
    result = dict(model=model, arm=arm, seed=seed, status='STARTING', integrity=False,
        plan_sha256=base.file_hash(out/'plan.json'), rows=[dict(case_id=c.case_id, seed=seed,
        status='NOT_RUN', structural_pass=False, provider_calls=0, attempts=[],
        final_output_sha256=None) for c in base.cases()])
    proc = monitor = private = None
    started = time.monotonic()
    end = min(started+1200, started+deadline-time.time())
    def save():
        result.update(real_duration_sec=time.monotonic()-started,
            provider_calls=sum(r.get('new_provider_calls', 0) for r in result['rows']),
            effective_provider_calls=sum(r.get('provider_calls', 0) for r in result['rows']))
        write(target/'result.json', result)
    with lease(out):
        write(target/'claim.json', {'started_at_utc': stamp()}, exclusive=True)
        base._RUN_DEADLINE = end
        try:
            save()
            private = base.create_private_evidence_container(ROOT/'logs/phase6-private-evidence',
                evidence_kind='synthetic', task_id='T506'+model.upper()+arm.upper()+str(seed),
                created_at_utc=datetime.now(timezone.utc))
            write(target/'locator.json', {'path': str(private)}, exclusive=True)
            with (private/'server.log').open('xb') as log:
                proc = subprocess.Popen(profile['argv'], cwd=Path(profile['argv'][0]).parent,
                    stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            while True:
                if proc.poll() is not None:
                    raise Stop('LOAD_EXIT')
                if time.monotonic()-started > 180:
                    raise Stop('LOAD_TIMEOUT')
                try:
                    if base.request('/health', timeout=2).get('status') == 'ok':
                        break
                except (base.httpx.HTTPError, TimeoutError, base.StopComparison):
                    pass
                time.sleep(.25)
            if proc.poll() is not None or not base.owned_listener(proc) or proc.poll() is not None:
                raise Stop('OWNERSHIP')
            identity = base.runtime()
            if Path(identity['model_path']).resolve() != Path(profile['model']['path']).resolve():
                raise Stop('MODEL_IDENTITY')
            write(private/'runtime.json', identity, exclusive=True)
            public_identity = base.safe_runtime(identity)
            runtime_path = out/(model+'-runtime.json')
            if runtime_path.exists():
                if read(runtime_path) != public_identity:
                    raise Stop('RUNTIME_DRIFT')
            else:
                write(runtime_path, public_identity, exclusive=True)
            result['runtime'] = public_identity
            with (private/'monitor.log').open('xb') as log:
                monitor = subprocess.Popen([sys.executable, str(ROOT/'scripts/monitor_phase6_gpu.py'),
                    '--output', str(private/'gpu.jsonl'), '--max-seconds', '1200', '--watch-pid', str(os.getpid())],
                    stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            def verify_now():
                verify(out, model, files=False)
                if proc.poll() is not None or base.runtime() != identity:
                    raise Stop('RUNTIME_DRIFT')
            for index, (case, p) in enumerate(entries()):
                if end-time.monotonic() < 60:
                    result['status'] = 'DEADLINE'
                    break
                began = stamp()
                write_bytes(private/(case.case_id+'.projection.json'), canonical_json_bytes(p.canonical_input))
                replay = None
                if arm == 'filter' and model == 'qw9':
                    replay, result['replay_result_sha256'] = replay_for(out, seed, case)
                dispatch = lambda stage, body: execute_call(out, private, case.case_id, stage, body,
                    remaining=lambda: end-time.monotonic(), verify_now=verify_now)
                row, final = process_case(case, p, model, arm, seed, dispatch, replay=replay)
                row.update(started_at_utc=began, ended_at_utc=stamp())
                write(private/(case.case_id+'.final.json'), {'final_content': final,
                    'final_output_sha256': row['final_output_sha256']}, exclusive=True)
                result['rows'][index] = row
                save()
                print(name, case.case_id, row['status'], flush=True)
            else:
                result['status'] = 'COMPLETE'
            verify(out, model)
            result['integrity'] = True
        except Exception as error:
            result.update(status='STOPPED', stop_reason=str(error) if type(error) is Stop else 'EXECUTION_ERROR')
            # Debug is retained privately; no traceback or free-form error is printed.
            if private is not None:
                import traceback
                write(private/'failure.json', {'exception': type(error).__name__, 'traceback': traceback.format_exc()}, exclusive=True)
        finally:
            base._RUN_DEADLINE = None
            result.update(base.cleanup_owned(monitor, proc))
            if result['owned_processes_remaining'] or any(k.endswith('_cleanup_error') for k in result):
                result.update(status='STOPPED', stop_reason='CLEANUP_FAILED', integrity=False)
            if private is not None:
                # Recover attempted rows even when a request/validator failed before
                # process_case could return. Consumption must not look like NOT_RUN.
                for row in result['rows']:
                    if row['status'] != 'NOT_RUN':
                        continue
                    consumed = sorted(private.glob(row['case_id']+'.*.consumed.json'))
                    if consumed:
                        row.update(status='ERROR', attempts=[], new_provider_calls=len(consumed),
                                   provider_calls=len(consumed))
                        for path in consumed:
                            final_path = path.with_name(path.name.replace('.consumed.json', '.outcome.json'))
                            row['attempts'].append(read(final_path if final_path.exists() else path))
                if (private/'server.log').exists():
                    base.attach_performance(result, private)
                result['private_artifacts'] = {p.name: base.file_hash(p) for p in private.iterdir() if p.is_file()}
            # Count durable reservations even if dispatch/result recording was interrupted.
            result['durable_call_count'] = sum(1 for p in (out/'calls').glob('*.json')
                                             if private is not None and p.name.startswith(private.name+'-'))
            save()
            write(target/'seal.json', {'result_sha256': base.file_hash(target/'result.json')}, exclusive=True)
    return 0 if result['integrity'] else 2


def supervised_block(out, model, arm, seed):
    name = block_name(model, arm, seed)
    target = out/name
    if (target/'claim.json').exists() or (target/'outer').exists():
        raise Stop('BLOCK_ALREADY_CLAIMED')
    plan = verify(out, model)
    remaining = datetime.fromisoformat(plan['deadline_utc']).timestamp()-time.time()
    if remaining <= 60:
        raise Stop('DEADLINE')
    target.mkdir(exist_ok=True)
    private = base.create_private_evidence_container(ROOT/'logs/phase6-private-evidence',
        evidence_kind='synthetic', task_id='T506OUTER', created_at_utc=datetime.now(timezone.utc))
    write(target/'outer-locator.json', {'path': str(private)}, exclusive=True)
    result = supervise([sys.executable, str(Path(__file__).resolve()), '--output', str(out.resolve()),
                       '--block', model, arm, str(seed)], target/'outer', raw_directory=private,
                       limit_seconds=min(1320, remaining))
    if (result.get('error_kind') or result['exit_code'] != 0 or result['outer_timeout']
            or not result['ownership_complete'] or result['owned_alive_after'] != 0):
        raise Stop('BLOCK_STOPPED')
    print(name, 'SEALED', flush=True)


def run_group(out, model):
    arms = ARMS if model == 'qw9' else (read(out/'selection.json')['arm'],)
    if model == 'gm12':
        selected = read(out/'selection.json')
        for path, digest in selected['inputs'].items():
            if base.file_hash(out/path) != digest:
                raise Stop('SELECTION_DRIFT')
    for arm in arms:
        for seed in SEEDS:
            supervised_block(out, model, arm, seed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--run', choices=('qw9', 'gm12'))
    mode.add_argument('--block', nargs=3)
    parser.add_argument('--started-at')
    args = parser.parse_args()
    try:
        if args.prepare:
            prepare(args.output, args.started_at)
        elif args.block:
            model, arm, seed = args.block
            return run_block(args.output, model, arm, int(seed))
        else:
            run_group(args.output, args.run)
    except Exception as error:
        print('STOPPED', str(error) if type(error) is Stop else 'COMMAND_ERROR', flush=True)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
