"""Synchronous ownership lifecycle for fixed, test-only probes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable

from scripts import phase6_model_comparison as base
from scripts.phase6_context_probe import wire_bytes

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path('C:/AIagent/agent/config.toml')
CONFIG_SHA = '43e509956d96492cace8393bdc3fd598ab2418315ccdfd82e144b876241f3bab'
TOKENIZER = Path('C:/AIagent/llama-tokenize.exe')
TOKENIZER_SHA = 'a0fbd34a8a3f25fc0f41cbac1ec67e8395a5ef940db33bd07307b5e3dc8cd6a1'


@dataclass(frozen=True)
class StageContract:
    name: str
    max_tokens: int
    reserve_seconds: int
    reject_when_remaining_equal: bool


@dataclass(frozen=True)
class TwoStageContract:
    experiment: str
    task: str
    stages: tuple[StageContract, StageContract]
    max_provider_calls: int = 64
    request_seconds: int = 60
    load_seconds: int = 180
    model_seconds: int = 1200
    cleanup_seconds: int = 25
    outer_seconds: int = 1320
    profile: str = 'qw9'
    model: str = base.PROFILES['qw9'][0]
    config_sha256: str = CONFIG_SHA
    tokenizer_sha256: str = TOKENIZER_SHA


P2_CONTRACT = TwoStageContract('two_call_v1', 'T454', (
    StageContract('plan', 384, 120, True), StageContract('message', 128, 120, True)))
IC2_CONTRACT = TwoStageContract('intent_choice_v1', 'T458', (
    StageContract('choice', 32, 240, False), StageContract('output', 480, 120, False)))
SC2_CONTRACT = TwoStageContract('stage_control_v1', 'T471', (
    StageContract('choice', 32, 240, False), StageContract('output', 480, 120, False)))


GB1_CONTRACT = TwoStageContract('grounding_basis_v1', 'T480', (
    StageContract('choice', 32, 240, False), StageContract('output', 480, 120, False)))

@dataclass(frozen=True)
class ReplayedChoiceContract(TwoStageContract):
    stages: tuple[StageContract, ...] = (StageContract('output', 480, 120, False),)
    max_provider_calls: int = 32


GC2_CONTRACT = ReplayedChoiceContract('grounding_closed_v1', 'T462')


def validate_contract(contract):
    if (type(contract) not in (TwoStageContract, ReplayedChoiceContract) or type(contract.stages) is not tuple
            or any(type(stage) is not StageContract for stage in contract.stages)):
        raise base.StopComparison('STAGE_CONTRACT')
    expected = {'two_call_v1': P2_CONTRACT, 'intent_choice_v1': IC2_CONTRACT, 'grounding_closed_v1': GC2_CONTRACT, 'stage_control_v1': SC2_CONTRACT, 'grounding_basis_v1': GB1_CONTRACT}.get(contract.experiment)
    # Canonical bytes also reject int/bool coercion, wrong number types and reordered stages.
    if expected is None or type(contract) is not type(expected) or wire_bytes(asdict(contract)) != wire_bytes(asdict(expected)):
        raise base.StopComparison('STAGE_CONTRACT')
    if (base.REQUEST_SECONDS, base.LOAD_SECONDS, base.MODEL_SECONDS) != (60, 180, 1200):
        raise base.StopComparison('STAGE_CONTRACT')


@dataclass(frozen=True)
class TwoStageCallbacks:
    verify_source_and_inputs: Callable
    initial_row: Callable
    process_case: Callable
    verify_final_source: Callable


@dataclass(frozen=True)
class CaseOutcome:
    final_raw: str
    binding: dict
    status: str = 'COMPLETE'


class CaseContext:
    """Case code receives a dispatch capability, never paths or process handles."""
    __slots__ = ('__dispatch',)

    def __init__(self, dispatch):
        self.__dispatch = dispatch

    def dispatch(self, stage_name, body):
        return self.__dispatch(stage_name, body)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value, *, exclusive=False):
    with Path(path).open('x' if exclusive else 'w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def stage(stage_name, body, row, private, rawfile, save, budget, *, contract):
    """One dispatch. The durable consumed record always precedes the HTTP call."""
    validate_contract(contract)
    found = [item for item in contract.stages if item.name == stage_name]
    if len(found) != 1 or wire_bytes(body.get('max_tokens')) != wire_bytes(found[0].max_tokens):
        raise base.StopComparison('STAGE_CONTRACT')
    spec = found[0]
    remaining = None if base._RUN_DEADLINE is None else base._RUN_DEADLINE-time.monotonic()
    if (remaining is None or remaining < spec.reserve_seconds
            or (spec.reject_when_remaining_equal and remaining == spec.reserve_seconds)):
        raise base.StopComparison('MODEL_TIME_BUDGET')
    first, second = contract.stages[0].name, contract.stages[-1].name
    if len(contract.stages) == 2 and stage_name == second and (row[first+'_provider_calls'] != 1
            or row.get(first, {}).get('status') != 'GENERATED'):
        raise base.StopComparison('STAGE_ORDER')
    if row[stage_name+'_provider_calls'] or budget['calls'] >= contract.max_provider_calls:
        raise base.StopComparison('CALL_BUDGET')
    payload = wire_bytes(body)
    path = private/(row['case_id']+'.'+stage_name+'.request.bin')
    with path.open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    if base.file_hash(path) != hashlib.sha256(payload).hexdigest():
        raise base.StopComparison('WIRE_SAVE_MISMATCH')
    if contract in (SC2_CONTRACT, GB1_CONTRACT):
        # Keep native content private; legacy digest and byte digest are different domains.
        rendered_metadata = {}
        def private_sink(rendered):
            if contract == GB1_CONTRACT:
                from scripts.phase6_grounding_basis_probe import validate_native_rendered
            else:
                from scripts.phase6_stage_control_probe import validate_native_rendered
            validate_native_rendered(body, stage_name, rendered)
            encoded = rendered.encode('utf-8')
            rendered_path = private/(row['case_id']+'.'+stage_name+'.rendered.bin')
            with rendered_path.open('xb') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            saved = rendered_path.read_bytes()
            expected = hashlib.sha256(encoded).hexdigest()
            if saved != encoded or hashlib.sha256(saved).hexdigest() != expected:
                raise base.StopComparison('TEMPLATE_INVALID')
            rendered_metadata.update(rendered_prompt_utf8_sha256=expected,
                rendered_prompt_utf8_bytes=len(encoded))
        try:
            measured = base.count_prompt(body, wire_payload=payload, private_sink=private_sink)
            if set(rendered_metadata) != {'rendered_prompt_utf8_sha256', 'rendered_prompt_utf8_bytes'}:
                raise base.StopComparison('TEMPLATE_INVALID')
        except Exception as error:
            reason = ('CONTEXT_OVERFLOW' if isinstance(error, base.StopComparison)
                      and str(error) == 'CONTEXT_OVERFLOW' else 'TEMPLATE_INVALID')
            row.update(status=stage_name.upper()+'_NOT_STARTED',
                       generation_status='ERROR', error_kind=reason)
            raise base.StopComparison(reason) from None
        measured = {**measured, **rendered_metadata}
    else:
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


def record_final(case, projection, row, rawfile, outcome):
    if type(outcome) is not CaseOutcome:
        raise base.StopComparison('CASE_OUTCOME')
    final_raw = outcome.final_raw
    rawfile.write(json.dumps({'case_id': case.case_id, 'stage': 'final',
                             'final_content': final_raw, 'binding': outcome.binding}, ensure_ascii=False)+'\n')
    rawfile.flush()
    os.fsync(rawfile.fileno())
    row.update(base.screen(case, projection, final_raw), status=outcome.status,
               generation_status='GENERATED', final_output_sha256=hashlib.sha256(final_raw.encode()).hexdigest())


def run_two_stage_probe(out, *, contract, callbacks):
    validate_contract(contract)
    if type(contract) is not TwoStageContract:
        raise base.StopComparison('STAGE_CONTRACT')
    return _run_lifecycle(out, contract=contract, callbacks=callbacks)


def run_replayed_choice_output_probe(out, *, contract, callbacks):
    validate_contract(contract)
    if type(contract) is not ReplayedChoiceContract:
        raise base.StopComparison('STAGE_CONTRACT')
    return _run_lifecycle(out, contract=contract, callbacks=callbacks)


def _run_lifecycle(out, *, contract, callbacks):
    validate_contract(contract)
    first, second = contract.stages[0].name, contract.stages[-1].name
    plan = json.loads((out/'plan.json').read_text(encoding='utf-8'))
    profile, projections = callbacks.verify_source_and_inputs(plan)
    if (out/'qw9-results.json').exists():
        raise base.StopComparison('RESULT_EXISTS')
    private = None
    result = {'experiment': contract.experiment, 'model_key': 'qw9', 'status': 'STARTING', 'clock_domain': 'REAL',
              'plan_sha256': base.file_hash(out/'plan.json'), 'retry': 0, 'repair': 0,
              'max_provider_calls': contract.max_provider_calls, 'baseline_artifacts': plan['baseline']['artifacts'],
              'rows': [callbacks.initial_row(c, p) for c, p in projections]}
    if contract == SC2_CONTRACT:
        result.update(task_id='T471', runner='scripts/phase6_stage_control_runner.py',
                      reused_choice_count=0, reused_provider_calls=0)
    if contract == GB1_CONTRACT:
        result.update(task_id='T480', runner='scripts/phase6_grounding_basis_runner.py',
                      reused_choice_count=0, reused_provider_calls=0)
    if contract == GC2_CONTRACT:
        result.update(reused_choice_count=32, reused_provider_calls=32, replay_artifacts=plan['replay']['artifacts'])
    base.claim_run(out, 'qw9')
    started = time.monotonic()
    base._RUN_DEADLINE = started+base.MODEL_SECONDS
    proc = monitor = None
    budget = {'calls': 0}
    def save():
        result.update(real_duration_sec=time.monotonic()-started, new_provider_calls=budget['calls'])
        if contract == GC2_CONTRACT:
            result['new_output_calls'] = budget['calls']
        write_json(out/'qw9-results.json', result)
    try:
        save()
        private = base.create_private_evidence_container(ROOT/'logs/phase6-private-evidence',
            evidence_kind='synthetic', task_id=contract.task+'QW9', created_at_utc=datetime.now(timezone.utc))
        write_json(out/'qw9-locator.json', {'path': str(private)}, exclusive=True)
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
                    context = CaseContext(lambda name, body: stage(name, body, row, private,
                        rawfile, save, budget, contract=contract))
                    outcome = callbacks.process_case(context, case, projection, row)
                    if outcome is not None:
                        record_final(case, projection.original if contract == GC2_CONTRACT else projection, row, rawfile, outcome)
                except (base.httpx.HTTPError, TimeoutError):
                    row.update(status=(second if row[second+'_provider_calls'] else first).upper()+'_ERROR',
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
            if row['status'] in (first.upper()+'_STARTED', second.upper()+'_STARTED'):
                row.update(status=row['status'].removesuffix('_STARTED')+'_ERROR',
                           generation_status='ERROR')
    finally:
        base._RUN_DEADLINE = None
        result.update(base.cleanup_owned(monitor, proc))
        if result['owned_processes_remaining'] or any(k.endswith('_cleanup_error') for k in result):
            result.update(status='STOPPED', stop_reason='CLEANUP_FAILED')
        try:
            result.update(callbacks.verify_final_source(plan))
            if private is not None and (private/'server.log').exists():
                base.attach_performance(result, private)
            if not result['source_unchanged'] or not result['config_unchanged']:
                result.update(status='STOPPED', stop_reason='FROZEN_SOURCE_CHANGED')
        except Exception:
            result.update(status='STOPPED', stop_reason='FINAL_EVIDENCE_FAILED')
        save()
    print('qw9', result['status'], result.get('stop_reason', ''), flush=True)
    return 0 if result['status'] == 'COMPLETE' else 2
