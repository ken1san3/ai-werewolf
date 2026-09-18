"""Bounded, sequential model comparison using the unchanged Phase 6 fixture."""
from __future__ import annotations

import argparse
import asyncio
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_client._compat import await_with_timeout
from ai_client.brain.controller import _invalid_or_repeated_self_text
from ai_client.llm.decision import parse_llm_output, DecisionValidationError
from scripts.phase6_context_probe import provider_body, wire_bytes
from scripts.phase6_conversation_suite import cases, project, evaluate
from tests.fixtures.phase6_evidence import create_private_evidence_container

PORT = 8082
_RUN_DEADLINE = None
CONTEXT = 8192
REQUEST_SECONDS = 60
MODEL_SECONDS = 1200
LOAD_SECONDS = 180
SAMPLING = dict(temperature=0.2, top_k=40, top_p=0.95, min_p=0.05,
                repeat_penalty=1.0, presence_penalty=0.0, frequency_penalty=0.0,
                seed=4242026, max_tokens=512, cache_prompt=False)
STOCK = Path('C:/AIagent/llama-server.exe')
PROFILES = {
    'qw9': ('C:/models/Qwen3.5-9/Qwen3.5-9B-Q4_K_M.gguf', 'Q4_K_M', STOCK, ['-ngl', '99']),
    'll8': ('C:/models/Llama-3.1-8B-Instruct/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf', 'Q4_K_M', STOCK, ['-ngl', '99']),
    'gm12': ('C:/models/Gemma-3-12B-Instruct/google_gemma-3-12b-it-Q4_K_M.gguf', 'Q4_K_M', STOCK, ['-ngl', '24']),
    'qw35': ('C:/models/Qwen3.6-35B-A3B/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf', 'UD-Q4_K_XL', STOCK, ['-ngl', '99', '--n-cpu-moe', '35']),
    'tb27': ('C:/models/Ternary-Bonsai-2-27B/Ternary-Bonsai-2-27B-PTQ1_0.gguf', 'PTQ1_0', ROOT/'logs/t424-runtime/prism-cuda12/llama-server.exe', ['-ngl', '99']),
}
SOURCES = ('scripts/phase6_model_comparison.py', 'scripts/phase6_conversation_suite.py',
           'scripts/phase6_context_probe.py', 'scripts/monitor_phase6_gpu.py',
           'tests/fixtures/phase6_conversation_cases.py', 'tests/fixtures/phase6_evidence.py')


class StopComparison(RuntimeError):
    """Only a fixed diagnostic code may cross the private evidence boundary."""


def digest(value):
    return hashlib.sha256(wire_bytes(value)).hexdigest()


def file_hash(path):
    with Path(path).open('rb') as f:
        value = hashlib.sha256()
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def file_identity(path):
    path = Path(path)
    stat = path.stat()
    return {'path': str(path.resolve()), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'sha256': file_hash(path)}


def claim_run(out, key):
    with (out/(key+'.claim')).open('x') as f:
        f.write(datetime.now(timezone.utc).isoformat())


def source_identity():
    paths = [ROOT / p for p in SOURCES] + sorted((ROOT/'ai_client').rglob('*.py'))
    return {p.relative_to(ROOT).as_posix(): file_hash(p) for p in paths}


def body_for(projection, model):
    body = provider_body(projection)
    body['model'] = Path(model).name
    body.update(SAMPLING)
    return body


def common_hash(body):
    return digest({k: v for k, v in body.items() if k != 'model'})


def launch_args(key):
    model, _, server, extra = PROFILES[key]
    return [str(server), '-m', model, '--host', '127.0.0.1', '--port', str(PORT),
            '-c', str(CONTEXT), '-np', '1', '--jinja', '--reasoning', 'off',
            '--chat-template-kwargs', '{"enable_thinking":false}', '--fit', 'off', *extra]


def prepare(out):
    out.mkdir(parents=True, exist_ok=True)
    if (out/'plan.json').exists():
        raise StopComparison('PLAN_EXISTS')
    entries = []
    for case in cases():
        p = project(case, 'baseline')
        entries.append({'case_id': case.case_id, 'category': case.category,
                        'common_input_sha256': common_hash(body_for(p, PROFILES['qw9'][0])),
                        'projection_sha256': p.prompt_sha256, 'proxy_units': p.token_proxy_units})
    profiles = {}
    for key, (model, quant, server, _) in PROFILES.items():
        binaries = [server, *sorted(server.parent.glob('*.dll'))] if server.is_file() else []
        profiles[key] = {'model': file_identity(model), 'quantization': quant,
                         'argv': launch_args(key), 'runtime_files': [file_identity(p) for p in binaries],
                         'available': bool(binaries)}
    manifest = {'schema': 1, 'source': source_identity(), 'cases': entries, 'profiles': profiles,
                'sampling': SAMPLING, 'context': CONTEXT, 'request_seconds': REQUEST_SECONDS,
                'model_seconds': MODEL_SECONDS, 'load_seconds': LOAD_SECONDS,
                'max_generations_per_model': len(entries), 'retry': 0, 'repair': 0}
    with (out/'plan.json').open('x', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    print('PLAN_READY', len(entries), len(profiles), flush=True)


async def _request(path, body, timeout, *, transport=None):
    if path not in {'/health', '/props', '/slots', '/apply-template', '/tokenize', '/v1/chat/completions'}:
        raise StopComparison('ENDPOINT_NOT_ALLOWED')
    async def perform():
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                     timeout=timeout, transport=transport) as client:
            async with client.stream('GET' if body is None else 'POST',
                                     f'http://127.0.0.1:{PORT}'+path,
                                     content=None if body is None else wire_bytes(body),
                                     headers={'Content-Type': 'application/json'}) as response:
                if response.status_code != 200:
                    raise StopComparison('HTTP_'+str(response.status_code))
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 262144:
                        raise StopComparison('RESPONSE_TOO_LARGE')
                value = json.loads(data)
                if not isinstance(value, (dict, list)):
                    raise StopComparison('RESPONSE_TYPE')
                return value
    return await await_with_timeout(timeout, perform)


def request(path, body=None, *, timeout=20):
    if _RUN_DEADLINE is not None:
        timeout = min(timeout, _RUN_DEADLINE-time.monotonic())
        if timeout <= 0:
            raise StopComparison('MODEL_TIME_BUDGET')
    return asyncio.run(_request(path, body, timeout))


def port_free():
    with socket.socket() as sock:
        return sock.connect_ex(('127.0.0.1', PORT)) != 0


def count_prompt(body):
    rendered = request('/apply-template', body)['prompt']
    if not isinstance(rendered, str):
        raise StopComparison('TEMPLATE_TYPE')
    tok = request('/tokenize', {'content': rendered, 'add_special': True, 'parse_special': True})['tokens']
    if not isinstance(tok, list) or any(type(t) is not int or t < 0 for t in tok):
        raise StopComparison('TOKEN_TYPE')
    if len(tok) + body['max_tokens'] + 1 > CONTEXT:
        raise StopComparison('CONTEXT_OVERFLOW')
    bare = dict(body)
    bare.pop('response_format')
    bare_rendered = request('/apply-template', bare)['prompt']
    return {'prompt_tokens_actual': len(tok), 'rendered_prompt_sha256': digest(rendered),
            'schema_changes_rendered_prompt': rendered != bare_rendered,
            'remaining_context_tokens': CONTEXT-len(tok)-body['max_tokens']-1}


def runtime():
    props, slots = request('/props'), request('/slots')
    if len(slots) != 1 or slots[0]['is_processing'] or slots[0]['n_ctx'] != CONTEXT:
        raise StopComparison('SLOT_CONTRACT')
    return {'build': props.get('build_info'), 'model_path': props.get('model_path'),
            'template_sha256': digest(props.get('chat_template')),
            'n_ctx': slots[0]['n_ctx'], 'slots': len(slots),
            'generation_settings': props.get('default_generation_settings')}


def fixed_value(value, allowed):
    return value if isinstance(value, str) and value in allowed else 'UNKNOWN'


def safe_runtime(identity):
    settings = identity.get('generation_settings')
    numeric = {}
    if isinstance(settings, dict):
        for key in ('n_ctx', 'n_predict', 'seed', 'temperature', 'top_k', 'top_p', 'min_p',
                    'repeat_penalty', 'presence_penalty', 'frequency_penalty'):
            value = settings.get(key)
            if type(value) in (int, float) and math.isfinite(value):
                numeric[key] = value
    return {'build': fixed_value(identity.get('build'), {'b10697-093adb242', 'b10683-d8f26eec7'}),
            'build_sha256': digest(identity.get('build')),
            'model_path_sha256': digest(identity.get('model_path')),
            'template_sha256': identity['template_sha256'], 'n_ctx': CONTEXT, 'slots': 1,
            'generation_settings_numeric': numeric}


def screen(case, p, raw):
    row = evaluate(case, p, raw)
    row['style_pass'] = None
    row['product_text_guard_rejects'] = None
    try:
        parsed = parse_llm_output(raw, projection=p)
        row['product_text_guard_rejects'] = _invalid_or_repeated_self_text(case.request, parsed.decision)
    except DecisionValidationError:
        pass
    try:
        decision = json.loads(raw).get('decision', {})
        text = decision.get('message') or decision.get('comment') or ''
    except (ValueError, AttributeError):
        text = ''
    if not isinstance(text, str):
        text = ''
    norm = lambda s: ' '.join(s.casefold().split())
    candidate = norm(text).split()
    near = False
    from difflib import SequenceMatcher
    for rec in case.request.history.records:
        peer = norm(getattr(rec, 'message', '') or '').split()
        if getattr(rec, 'player_id', None) == case.request.discussion.player_id:
            continue
        if len(norm(text)) >= 50 and min(len(candidate), len(peer)) >= 8:
            near |= SequenceMatcher(None, candidate, peer, autojunk=False).ratio() >= 0.9
    row.update(near_peer_copy_screen=near, text_chars=len(text),
               incomplete_ending_screen=bool(text and not text.rstrip().rstrip('\"\'”’)]}').endswith(('.', '!', '?'))))
    row['decision_kind'] = fixed_value(row.get('decision_kind'),
        {'none', 'chat', 'vote', 'ability', 'co_declare', 'co_report'})
    row['speech_act'] = fixed_value(row.get('speech_act'),
        {'NONE', 'CLAIM', 'QUESTION', 'ANSWER', 'REBUTTAL', 'OPINION_CHANGE', 'RELATION_HYPOTHESIS'})
    # Screen flags are leads for case-specific review, never semantic PASS.
    return row


def stop_owned(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return None if process is None else process.returncode


def cleanup_owned(monitor, provider):
    result = {}
    remaining = 0
    for name, process in (('monitor', monitor), ('provider', provider)):
        try:
            result[name+'_exit'] = stop_owned(process)
        except Exception:
            result[name+'_cleanup_error'] = 'CLEANUP_FAILED'
            # Try kill even if terminate/wait failed. Still only this handle.
            try:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            except Exception:
                pass
        try:
            remaining += process is not None and process.poll() is None
        except Exception:
            remaining += 1  # Unknown liveness is never certified as zero.
    result['owned_processes_remaining'] = remaining
    return result


def process_memory(process):
    if os.name != 'nt':
        return None
    class MemoryCounters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong)] + [
            (n, ctypes.c_size_t) for n in ('PeakWorkingSetSize', 'WorkingSetSize',
            'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]
    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    get = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
    get.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    get.restype = ctypes.c_int
    if not get(int(process._handle), ctypes.byref(counters), counters.cb):
        return None
    return {n: int(getattr(counters, n)) for n in ('WorkingSetSize', 'PeakWorkingSetSize', 'PrivateUsage')}


def owned_listener(process):
    command = (f"@(Get-NetTCPConnection -State Listen -LocalPort {PORT} -ErrorAction SilentlyContinue).OwningProcess | ConvertTo-Json -Compress")
    result = subprocess.run(['powershell', '-NoProfile', '-Command', command],
        capture_output=True, timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or not result.stdout.strip():
        return False
    value = json.loads(result.stdout.decode('utf-8-sig'))
    return value == process.pid or value == [process.pid]


def attach_performance(result, private):
    samples = []
    path = private/'gpu.jsonl'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                sample = json.loads(line)
            except ValueError:
                continue  # A terminated monitor can leave one incomplete line.
            if sample.get('record') == 'sample':
                samples.append(sample)
    result['gpu_samples'] = len(samples)
    result['gpu_errors'] = sum(s.get('status') != 'OK' for s in samples)
    for row in result['rows']:
        selected = [g for s in samples
                    if row.get('started_at_utc', '~') <= s['observed_at_utc'] <= row.get('ended_at_utc', '')
                    for g in s.get('gpus', [])]
        row['gpu_sample_count'] = len(selected)
        for source, target in [('vram_used_mib', 'gpu_vram_peak_mib'), ('gpu_util_pct', 'gpu_util_peak_pct')]:
            values = [g[source] for g in selected if g.get(source) is not None]
            row[target] = max(values) if values else None
    # Only numeric buffer/layer observations leave the private runtime log.
    log = (private/'server.log').read_text(encoding='utf-8', errors='replace')
    result['runtime_buffer_mib'] = [
        {'device': device, 'kind': kind, 'mib': float(size)}
        for device, kind, size in re.findall(r'(CPU_Mapped|CUDA\d+|CUDA_Host)\s+(model|KV|compute) buffer size\s*=\s*([\d.]+) MiB', log)]
    offload = re.findall(r'offloaded (\d+)/(\d+) layers to GPU', log)
    result['gpu_layers'] = list(map(int, offload[-1])) if offload else None


def run(out, key):
    global _RUN_DEADLINE
    plan = json.loads((out/'plan.json').read_text(encoding='utf-8'))
    profile = plan['profiles'][key]
    if source_identity() != plan['source'] or launch_args(key) != profile['argv']:
        raise StopComparison('FROZEN_SOURCE_CHANGED')
    for identity in [profile['model'], *profile['runtime_files']]:
        if file_identity(identity['path']) != identity:
            raise StopComparison('FROZEN_FILE_CHANGED')
    if not profile['available']:
        raise StopComparison('RUNTIME_UNAVAILABLE')
    if not port_free():
        raise StopComparison('PORT_IN_USE')
    projections = [(c, project(c, 'baseline')) for c in cases()]
    for (case, p), frozen in zip(projections, plan['cases'], strict=True):
        if case.case_id != frozen['case_id'] or common_hash(body_for(p, profile['model']['path'])) != frozen['common_input_sha256']:
            raise StopComparison('FROZEN_INPUT_CHANGED')
    claim_run(out, key)
    private = create_private_evidence_container(ROOT/'logs/phase6-private-evidence',
        evidence_kind='synthetic', task_id='T424'+key.upper(), created_at_utc=datetime.now(timezone.utc))
    (out/(key+'-locator.json')).write_text(json.dumps({'path': str(private)}), encoding='utf-8')
    proc = monitor = None
    started = time.monotonic()
    _RUN_DEADLINE = started+MODEL_SECONDS
    result = {'model_key': key, 'model': Path(profile['model']['path']).name,
              'quantization': profile['quantization'], 'rows': [], 'retry': 0, 'repair': 0,
              'status': 'STARTING', 'clock_domain': 'REAL', 'plan_sha256': file_hash(out/'plan.json')}
    call_started = None
    def save():
        result['real_duration_sec'] = time.monotonic()-started
        (out/(key+'-results.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    try:
        with (private/'server.log').open('xb') as log:
            proc = subprocess.Popen(profile['argv'], cwd=Path(profile['argv'][0]).parent,
                stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result['provider_pid'] = proc.pid
        while True:
            if proc.poll() is not None:
                raise StopComparison('LOAD_EXIT')
            if time.monotonic()-started > LOAD_SECONDS:
                raise StopComparison('LOAD_TIMEOUT')
            try:
                if request('/health', timeout=2).get('status') == 'ok':
                    break
            except (httpx.HTTPError, TimeoutError, StopComparison):
                pass  # Bounded readiness polling, never a generation retry.
            time.sleep(0.25)
        identity = runtime()
        if not owned_listener(proc):
            raise StopComparison('LISTENER_OWNERSHIP')
        if Path(identity['model_path']).resolve() != Path(profile['model']['path']).resolve():
            raise StopComparison('MODEL_IDENTITY')
        (private/'runtime.json').write_text(json.dumps(identity, indent=2), encoding='utf-8')
        result['runtime'] = safe_runtime(identity)
        with (private/'monitor.log').open('xb') as log:
            monitor = subprocess.Popen([sys.executable, str(ROOT/'scripts/monitor_phase6_gpu.py'),
                '--output', str(private/'gpu.jsonl'), '--max-seconds', str(MODEL_SECONDS),
                '--watch-pid', str(os.getpid())], stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with (private/'raw.jsonl').open('x', encoding='utf-8') as rawfile:
            for case, p in projections:
                if time.monotonic()-started >= MODEL_SECONDS-REQUEST_SECONDS-60:
                    raise StopComparison('MODEL_TIME_BUDGET')
                if proc.poll() is not None or runtime() != identity:
                    raise StopComparison('RUNTIME_CHANGED')
                body = body_for(p, profile['model']['path'])
                measurement = count_prompt(body)
                row = {'case_id': case.case_id, 'category': case.category,
                       'common_input_sha256': common_hash(body), 'input_sha256': digest(body),
                       'proxy_units': p.token_proxy_units, **measurement,
                       'hard_pass': None, 'semantic_pass': None, 'style_pass': None,
                       'retry_count': 0, 'repair_count': 0, 'new_provider_calls': 1,
                       'generation_status': 'STARTED'}
                row['started_at_utc'] = datetime.now(timezone.utc).isoformat()
                result['rows'].append(row)
                call_started = None
                save()  # A call is consumed before network dispatch.
                call_started = time.monotonic()
                response = request('/v1/chat/completions', body, timeout=REQUEST_SECONDS)
                choice = response['choices'][0]
                text = choice['message'].get('content') or ''
                if not isinstance(text, str):
                    raise StopComparison('CONTENT_TYPE')
                usage = response.get('usage', {})
                # Reasoning content is not copied or evaluated.
                rawfile.write(json.dumps({'case_id': case.case_id, 'input': body,
                    'final_content': text, 'usage': usage, 'finish_reason': choice.get('finish_reason')}, ensure_ascii=False)+'\n')
                rawfile.flush()
                row.update(screen(case, p, text), generation_status='GENERATED',
                    latency_real_sec=time.monotonic()-call_started, final_output_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    provider_prompt_tokens=usage.get('prompt_tokens') if type(usage.get('prompt_tokens')) is int else None,
                    completion_tokens=usage.get('completion_tokens') if type(usage.get('completion_tokens')) is int else None,
                    finish_reason=fixed_value(choice.get('finish_reason'), {'stop', 'length', 'content_filter', 'tool_calls'}),
                    output_limit_reached=choice.get('finish_reason') == 'length')
                row['ended_at_utc'] = datetime.now(timezone.utc).isoformat()
                row['provider_memory_bytes'] = process_memory(proc)
                row['reasoning_content_present'] = bool(choice['message'].get('reasoning_content'))
                save()
                print(key, case.case_id, row['generation_status'], row.get('speech_act'), flush=True)
                if usage.get('prompt_tokens') != measurement['prompt_tokens_actual']:
                    raise StopComparison('TOKEN_COUNT_MISMATCH')
                if row['reasoning_content_present']:
                    raise StopComparison('THINKING_NOT_DISABLED')
        result['status'] = 'COMPLETE'
    except Exception as error:
        result['status'] = 'STOPPED'
        result['stop_reason'] = str(error) if isinstance(error, StopComparison) else fixed_value(
            type(error).__name__, {'TimeoutError', 'OSError', 'PermissionError', 'ValueError', 'KeyError',
                                  'TypeError', 'JSONDecodeError', 'ConnectError', 'RemoteProtocolError'})
        if result['rows'] and result['rows'][-1]['generation_status'] == 'STARTED':
            result['rows'][-1].update(generation_status='ERROR', failure_reasons=[result['stop_reason']],
                ended_at_utc=datetime.now(timezone.utc).isoformat(),
                latency_real_sec=None if call_started is None else time.monotonic()-call_started)
    finally:
        _RUN_DEADLINE = None
        result.update(cleanup_owned(monitor, proc))
        if result['owned_processes_remaining'] or any(k.endswith('_cleanup_error') for k in result):
            result.update(status='STOPPED', stop_reason='CLEANUP_FAILED')
        try:
            result['source_unchanged'] = source_identity() == plan['source']
            attach_performance(result, private)
        except Exception:
            result['evidence_error'] = 'FINAL_EVIDENCE_FAILED'
            result['status'] = 'STOPPED'
        try:
            save()
        except Exception:
            result['status'] = 'STOPPED'
            result['stop_reason'] = 'RESULT_SAVE_FAILED'
    print(key, result['status'], result.get('stop_reason', ''), flush=True)
    return 0 if result['status'] == 'COMPLETE' else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', action='store_true')
    group.add_argument('--run', choices=PROFILES)
    args = parser.parse_args()
    if args.prepare:
        prepare(args.output)
    else:
        raise SystemExit(run(args.output, args.run))
