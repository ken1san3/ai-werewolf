"""Finite test-only P2 runner: locked plan followed by optional text, never a game."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
from scripts import phase6_two_call_probe as probe
from scripts.phase6_context_probe import wire_bytes

P2 = 'two_call_v1'
TASK = 'T454'
TOKENIZER = Path('C:/AIagent/llama-tokenize.exe')
TOKENIZER_SHA = 'a0fbd34a8a3f25fc0f41cbac1ec67e8395a5ef940db33bd07307b5e3dc8cd6a1'
CONFIG = Path('C:/AIagent/agent/config.toml')
CONFIG_SHA = '43e509956d96492cace8393bdc3fd598ab2418315ccdfd82e144b876241f3bab'
EXTRA = ('scripts/phase6_two_call_probe.py', 'tests/test_phase6_two_call_probe.py',
         'scripts/phase6_two_call_runner.py', 'tests/test_phase6_two_call_runner.py',
         'scripts/phase6_probe_outer.py', 'tests/test_phase6_probe_outer.py')
base.EXPERIMENT_SOURCE_DELTA[P2] = base.EXPERIMENT_SOURCE_DELTA[base.K1] | frozenset(EXTRA)


def sources():
    return {**base.source_identity(base.K1), **{p: base.file_hash(ROOT/p) for p in EXTRA}}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def fixed_contract():
    return {'schema': 1, 'experiment': P2, 'experiment_version': 1, 'task_id': TASK,
            'sampling': base.SAMPLING, 'context': base.CONTEXT,
            'request_seconds': base.REQUEST_SECONDS, 'model_seconds': base.MODEL_SECONDS,
            'load_seconds': base.LOAD_SECONDS, 'max_generations_per_model': 32,
            'max_provider_calls': 64, 'plan_tokens': probe.PLAN_TOKENS,
            'message_tokens': probe.MESSAGE_TOKENS, 'retry': 0, 'repair': 0,
            'plan_instruction_sha256': base.digest(probe.PLAN_INSTRUCTION),
            'message_instruction_sha256': base.digest(probe.MESSAGE_INSTRUCTION),
            'config_sha256': CONFIG_SHA,
            'tokenizer_argv': [str(TOKENIZER), '-m', base.PROFILES['qw9'][0],
                               '--stdin', '--ids', '--no-bos', '--no-escape']}


def write_json(path, value, *, exclusive=False):
    with Path(path).open('x' if exclusive else 'w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def entry(case, projection):
    original = base.body_for(projection, base.PROFILES['qw9'][0])
    body = probe.plan_body(original)
    return {'case_id': case.case_id, 'category': case.category,
            'baseline_common_input_sha256': base.common_hash(original),
            'baseline_input_sha256': base.digest(original),
            'baseline_messages_sha256': base.digest(original['messages']),
            'projection_sha256': projection.prompt_sha256,
            'plan_input_sha256': base.digest(body),
            'plan_wire_sha256': hashlib.sha256(wire_bytes(body)).hexdigest(),
            'plan_schema_sha256': base.digest(body['response_format']['json_schema']['schema']),
            'plan_messages_sha256': base.digest(body['messages']),
            'plan_wire_bytes': len(wire_bytes(body))}


def prepare(out, baseline_source):
    out.mkdir(parents=True, exist_ok=True)
    if (out/'plan.json').exists():
        raise base.StopComparison('PLAN_EXISTS')
    if base.file_hash(CONFIG) != CONFIG_SHA or base.file_hash(TOKENIZER) != TOKENIZER_SHA:
        raise base.StopComparison('FROZEN_FILE_CHANGED')
    model, quant, server, _ = base.PROFILES['qw9']
    profile = {'model': base.file_identity(model), 'quantization': quant,
               'argv': base.launch_args('qw9'),
               'runtime_files': [base.file_identity(p) for p in [server, *sorted(server.parent.glob('*.dll'))]],
               'available': server.is_file()}
    plan = {**fixed_contract(),
            'source': sources(), 'cases': [entry(c, base.project(c, 'baseline')) for c in base.cases()],
            'profiles': {'qw9': profile}, 'tokenizer': base.file_identity(TOKENIZER)}
    plan['baseline'] = base.baseline_binding(plan, baseline_source)
    write_json(out/'plan.json', plan, exclusive=True)
    print('PLAN_READY', len(plan['cases']), flush=True)


def verify(plan):
    # Canonical bytes distinguish bool/int and numeric representations as well as values.
    if any(wire_bytes(plan.get(key)) != wire_bytes(value) for key, value in fixed_contract().items()):
        raise base.StopComparison('PLAN_CONTRACT_CHANGED')
    if sources() != plan['source'] or base.file_hash(CONFIG) != plan['config_sha256'] or plan['config_sha256'] != CONFIG_SHA:
        raise base.StopComparison('FROZEN_SOURCE_CHANGED')
    if base.baseline_binding(plan, plan['baseline']['directory']) != plan['baseline']:
        raise base.StopComparison('BASELINE_MISMATCH')
    profile = plan['profiles']['qw9']
    if profile['argv'] != base.launch_args('qw9') or not profile['available']:
        raise base.StopComparison('RUNTIME_UNAVAILABLE')
    for item in [profile['model'], *profile['runtime_files'], plan['tokenizer']]:
        if base.file_identity(item['path']) != item:
            raise base.StopComparison('FROZEN_FILE_CHANGED')
    projections = [(c, base.project(c, 'baseline')) for c in base.cases()]
    if [entry(c, p) for c, p in projections] != plan['cases'] or len(projections) != 32:
        raise base.StopComparison('FROZEN_INPUT_CHANGED')
    if not base.port_free():
        raise base.StopComparison('PORT_IN_USE')
    return profile, projections


def plan_binding(case, projection, body, raw, value):
    return {'case_id': case.case_id, 'projection_sha256': projection.prompt_sha256,
            'plan_wire_sha256': hashlib.sha256(wire_bytes(body)).hexdigest(),
            'plan_raw_sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest(),
            'canonical_plan_sha256': base.digest(value)}


def locked_message(case, projection, baseline, plan_body, raw, value, binding):
    if plan_binding(case, projection, plan_body, raw, value) != binding:
        raise base.StopComparison('PLAN_BINDING_MISMATCH')
    # Revalidate the original raw and bind its exact parsed value, not a caller's replacement.
    if probe.validate_plan(raw, projection) != value:
        raise base.StopComparison('PLAN_BINDING_MISMATCH')
    return probe.message_body(baseline, value, projection)


def stage(stage_name, body, row, private, rawfile, save, budget):
    """One dispatch. The durable consumed record always precedes the HTTP call."""
    if base._RUN_DEADLINE is None or time.monotonic() >= base._RUN_DEADLINE-120:
        raise base.StopComparison('MODEL_TIME_BUDGET')
    if stage_name not in ('plan', 'message') or body['max_tokens'] != {'plan': 384, 'message': 128}[stage_name]:
        raise base.StopComparison('STAGE_CONTRACT')
    if row[stage_name+'_provider_calls'] or budget['calls'] >= 64:
        raise base.StopComparison('CALL_BUDGET')
    payload = wire_bytes(body)
    path = private/(row['case_id']+'.'+stage_name+'.request.bin')
    with path.open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    if base.file_hash(path) != hashlib.sha256(payload).hexdigest():
        raise base.StopComparison('WIRE_SAVE_MISMATCH')
    measured = base.count_prompt(body, wire_payload=payload)
    data = {'input_sha256': base.digest(body), 'wire_sha256': hashlib.sha256(payload).hexdigest(),
            'wire_bytes': len(payload), 'messages_sha256': base.digest(body['messages']),
            'schema_sha256': base.digest(body['response_format']['json_schema']['schema']),
            'max_tokens': body['max_tokens'], **measured, 'status': 'STARTED', 'started_at_utc': stamp()}
    # Immutable per-call consumption survives an interrupted result snapshot.
    # Exclusive creation avoids replacing files observed by Windows scanners.
    write_json(private/(row['case_id']+'.'+stage_name+'.consumed.json'),
               {'case_id': row['case_id'], 'stage': stage_name, **data}, exclusive=True)
    row[stage_name] = data
    row['status'] = stage_name.upper()+'_STARTED'
    row[stage_name+'_provider_calls'] = 1
    row['new_provider_calls'] += 1
    budget['calls'] += 1
    save()
    started = time.monotonic()
    try:
        response = base.request('/v1/chat/completions', body, timeout=base.REQUEST_SECONDS, wire_payload=payload)
        choice = response['choices'][0]
        reasoning = choice['message'].get('reasoning_content')
        if reasoning is not None and (type(reasoning) is not str or reasoning != ''):
            raise base.StopComparison('THINKING_NOT_DISABLED')
        text = choice['message'].get('content')
        if type(text) is not str:
            raise base.StopComparison('CONTENT_TYPE')
        usage = response.get('usage', {})
        prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
        finish = base.fixed_value(choice.get('finish_reason'), {'stop', 'length', 'content_filter', 'tool_calls'})
        record = {'case_id': row['case_id'], 'stage': stage_name, 'input': body,
                  'final_content': text, 'usage': usage, 'finish_reason': finish,
                  'wire_sha256': data['wire_sha256']}
        rawfile.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+'\n')
        rawfile.flush()
        os.fsync(rawfile.fileno())
        data.update(final_output_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(),
                    provider_prompt_tokens=prompt if type(prompt) is int else None,
                    completion_tokens=completion if type(completion) is int else None,
                    finish_reason=finish)
        if type(prompt) is not int or prompt != measured['prompt_tokens_actual']:
            raise base.StopComparison('TOKEN_COUNT_MISMATCH')
        if type(completion) is not int or not 0 <= completion <= body['max_tokens']:
            raise base.StopComparison('COMPLETION_BUDGET')
        row['completion_tokens'] += completion
        row['provider_prompt_tokens'] += prompt
        if row['completion_tokens'] > 512:
            raise base.StopComparison('COMPLETION_BUDGET')
        data['status'] = 'GENERATED' if finish == 'stop' else 'LENGTH' if finish == 'length' else 'ERROR'
        return text if finish == 'stop' else None
    except Exception:
        data['status'] = 'ERROR'
        raise
    finally:
        data.update(ended_at_utc=stamp(), latency_real_sec=time.monotonic()-started)
        save()


def run_case(case, projection, row, private, rawfile, save, budget):
    baseline = base.body_for(projection, base.PROFILES['qw9'][0])
    body = probe.plan_body(baseline)
    raw = stage('plan', body, row, private, rawfile, save, budget)
    if raw is None:
        row.update(status='PLAN_ERROR', generation_status='ERROR')
        return
    try:
        value = probe.validate_plan(raw, projection)
    except (ValueError, base.DecisionValidationError):
        row.update(status='PLAN_INVALID', generation_status='INVALID')
        return
    row['status'] = 'PLAN_VALID'
    binding = plan_binding(case, projection, body, raw, value)
    row['binding'] = binding
    text = None
    if value['decision']['kind'] in ('chat', 'co_declare'):
        next_body = locked_message(case, projection, baseline, body, raw, value, binding)
        text = stage('message', next_body, row, private, rawfile, save, budget)
        if text is None:
            row.update(status='MESSAGE_ERROR', generation_status='ERROR')
            return
    try:
        final = probe.validate_final(value, text, projection)
    except (ValueError, base.DecisionValidationError):
        row.update(status='MESSAGE_INVALID' if text is not None else 'PLAN_INVALID', generation_status='INVALID')
        return
    final_raw = wire_bytes(final).decode('utf-8')
    rawfile.write(json.dumps({'case_id': case.case_id, 'stage': 'final',
                             'final_content': final_raw, 'binding': binding}, ensure_ascii=False)+'\n')
    rawfile.flush()
    os.fsync(rawfile.fileno())
    row.update(base.screen(case, projection, final_raw),
               status='COMPLETE' if text is not None else 'COMPLETE_NO_MESSAGE',
               generation_status='GENERATED', final_output_sha256=hashlib.sha256(final_raw.encode()).hexdigest())


def run(out):
    plan = json.loads((out/'plan.json').read_text(encoding='utf-8'))
    profile, projections = verify(plan)
    if (out/'qw9-results.json').exists():
        raise base.StopComparison('RESULT_EXISTS')
    base.claim_run(out, 'qw9')
    private = base.create_private_evidence_container(ROOT/'logs/phase6-private-evidence',
        evidence_kind='synthetic', task_id=TASK+'QW9', created_at_utc=datetime.now(timezone.utc))
    write_json(out/'qw9-locator.json', {'path': str(private)}, exclusive=True)
    result = {'experiment': P2, 'model_key': 'qw9', 'status': 'STARTING', 'clock_domain': 'REAL',
              'plan_sha256': base.file_hash(out/'plan.json'), 'retry': 0, 'repair': 0,
              'max_provider_calls': 64, 'baseline_artifacts': plan['baseline']['artifacts'], 'rows': []}
    for c, p in projections:
        result['rows'].append({'case_id': c.case_id, 'category': c.category,
            'baseline_input_sha256': base.digest(base.body_for(p, profile['model']['path'])),
            'projection_sha256': p.prompt_sha256, 'status': 'PLAN_NOT_STARTED',
            'generation_status': 'NOT_STARTED', 'plan_provider_calls': 0, 'message_provider_calls': 0,
            'new_provider_calls': 0, 'retry_count': 0, 'repair_count': 0,
            'completion_tokens': 0, 'provider_prompt_tokens': 0,
            'hard_pass': None, 'semantic_pass': None, 'style_pass': None})
    started = time.monotonic()
    base._RUN_DEADLINE = started+base.MODEL_SECONDS
    proc = monitor = None
    budget = {'calls': 0}
    def save():
        result.update(real_duration_sec=time.monotonic()-started, new_provider_calls=budget['calls'])
        write_json(out/'qw9-results.json', result)
    try:
        save()
        with (private/'server.log').open('xb') as log:
            proc = subprocess.Popen(profile['argv'], cwd=Path(profile['argv'][0]).parent,
                stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result['provider_pid'] = proc.pid
        while True:
            if proc.poll() is not None:
                raise base.StopComparison('LOAD_EXIT')
            if time.monotonic()-started > base.LOAD_SECONDS:
                raise base.StopComparison('LOAD_TIMEOUT')
            try:
                if base.request('/health', timeout=2).get('status') == 'ok':
                    break
            except (base.httpx.HTTPError, TimeoutError, base.StopComparison):
                pass
            time.sleep(0.25)
        identity = base.runtime()
        if not base.owned_listener(proc):
            raise base.StopComparison('LISTENER_OWNERSHIP')
        if Path(identity['model_path']).resolve() != Path(profile['model']['path']).resolve():
            raise base.StopComparison('MODEL_IDENTITY')
        write_json(private/'runtime.json', identity, exclusive=True)
        result['runtime'] = base.safe_runtime(identity)
        if result['runtime'] != plan['baseline']['runtime']:
            raise base.StopComparison('BASELINE_RUNTIME_MISMATCH')
        with (private/'monitor.log').open('xb') as log:
            monitor = subprocess.Popen([sys.executable, str(ROOT/'scripts/monitor_phase6_gpu.py'),
                '--output', str(private/'gpu.jsonl'), '--max-seconds', str(base.MODEL_SECONDS),
                '--watch-pid', str(os.getpid())], stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with (private/'raw.jsonl').open('x', encoding='utf-8') as rawfile:
            for (case, projection), row in zip(projections, result['rows'], strict=True):
                if proc.poll() is not None or base.runtime() != identity:
                    raise base.StopComparison('RUNTIME_CHANGED')
                row['started_at_utc'] = stamp()
                case_start = time.monotonic()
                try:
                    run_case(case, projection, row, private, rawfile, save, budget)
                except (base.httpx.HTTPError, TimeoutError):
                    row.update(status='MESSAGE_ERROR' if row['message_provider_calls'] else 'PLAN_ERROR',
                               generation_status='ERROR', error_kind='TRANSPORT_ERROR')
                finally:
                    row.update(ended_at_utc=stamp(), latency_real_sec=time.monotonic()-case_start)
                    save()
                print(case.case_id, row['status'], flush=True)
        result['status'] = 'COMPLETE'
    except Exception as error:
        result['status'] = 'STOPPED'
        # All StopComparison instances in this runner/shared transport contain fixed codes only.
        result['stop_reason'] = str(error) if isinstance(error, base.StopComparison) else 'RUN_ERROR'
        result['error_kind'] = base.fixed_value(type(error).__name__,
            {'PermissionError', 'FileExistsError', 'FileNotFoundError', 'OSError', 'ValueError',
             'KeyError', 'TypeError', 'AssertionError', 'StopComparison'})
        if isinstance(error, OSError):
            result['os_error_number'] = error.winerror if hasattr(error, 'winerror') else error.errno
        for row in result['rows']:
            if row['status'] in ('PLAN_STARTED', 'MESSAGE_STARTED'):
                row.update(status='PLAN_ERROR' if row['status'] == 'PLAN_STARTED' else 'MESSAGE_ERROR',
                           generation_status='ERROR')
    finally:
        base._RUN_DEADLINE = None
        result.update(base.cleanup_owned(monitor, proc))
        if result['owned_processes_remaining'] or any(k.endswith('_cleanup_error') for k in result):
            result.update(status='STOPPED', stop_reason='CLEANUP_FAILED')
        try:
            result['source_unchanged'] = sources() == plan['source']
            result['config_unchanged'] = base.file_hash(CONFIG) == plan['config_sha256']
            base.attach_performance(result, private)
            if not result['source_unchanged'] or not result['config_unchanged']:
                result.update(status='STOPPED', stop_reason='FROZEN_SOURCE_CHANGED')
        except Exception:
            result.update(status='STOPPED', stop_reason='FINAL_EVIDENCE_FAILED')
        save()
    print('qw9', result['status'], result.get('stop_reason', ''), flush=True)
    return 0 if result['status'] == 'COMPLETE' else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--run', choices=['qw9'])
    parser.add_argument('--baseline-source', type=Path)
    args = parser.parse_args()
    if args.prepare:
        prepare(args.output, args.baseline_source)
    else:
        if args.baseline_source is not None:
            parser.error('baseline source is prepare-only')
        raise SystemExit(run(args.output))
