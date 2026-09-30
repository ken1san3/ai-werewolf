"""Finite T550 quality measurement; execution requires an independent operator.

No game connection, product commit, repair, HTTP retry, or old native proof.
Raw inputs/results remain in the private container. Public output is aggregate.
"""
from __future__ import annotations

import argparse
from collections import Counter
import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from types import MappingProxyType

import httpx

from scripts import phase6_quality_probe_v2 as q
from scripts import phase6_model_comparison as existing
from tests.fixtures.phase6_evidence import create_private_evidence_container

ROOT = Path(__file__).resolve().parents[1]
PROBE = (('G01-1', 4242027), ('G15-1', 4242027))
SOURCES = ('scripts/phase6_quality_probe_v2.py', 'scripts/phase6_quality_runner_v2.py',
           'scripts/phase6_local_staged_probe.py', 'scripts/phase6_conversation_suite.py',
           'scripts/phase6_harness_v2.py', 'scripts/phase6_model_comparison.py',
           'scripts/phase6_context_probe.py', 'tests/fixtures/phase6_evidence.py',
           'tests/fixtures/phase6_conversation_cases.py', 'ai_client/discussion/generation_v2.py',
           'ai_client/llm/decision.py', 'ai_client/brain/controller.py')


def source_hashes():
    paths = set(SOURCES) | {p.relative_to(ROOT).as_posix() for p in (ROOT / 'ai_client').rglob('*.py')}
    return {p: existing.file_hash(ROOT / p) for p in sorted(paths)}


def write_once(path, value):
    raw = value if isinstance(value, bytes) else q.wire(value)
    with Path(path).open('xb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    return q.digest(raw)


def listener_owners(port):
    """Read both Windows listener tables without a subprocess.

    Wildcard listeners also overlap the exact loopback endpoint. Include them
    so a second bind cannot hide behind a different local-address spelling.
    """
    if os.name != 'nt':
        raise RuntimeError('OWNER_PLATFORM')
    get = ctypes.WinDLL('iphlpapi', use_last_error=True).GetExtendedTcpTable
    get.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.BOOL,
                   wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    get.restype = wintypes.DWORD
    found = []
    for family, width, port_offset, pid_offset in ((2, 24, 8, 20), (23, 56, 20, 52)):
        size = wintypes.ULONG()
        if get(None, ctypes.byref(size), False, family, 3, 0) not in (0, 122):
            raise RuntimeError('OWNER_TABLE')
        buf = ctypes.create_string_buffer(size.value)
        if get(buf, ctypes.byref(size), False, family, 3, 0) != 0:
            raise RuntimeError('OWNER_TABLE')
        raw = buf.raw
        count = int.from_bytes(raw[:4], 'little')
        if 4 + width * count > len(raw):
            raise RuntimeError('OWNER_SHAPE')
        for i in range(count):
            row = raw[4 + width * i:4 + width * (i + 1)]
            actual_port = int.from_bytes(row[port_offset:port_offset + 2], 'big')
            address = row[4:8] if family == 2 else row[:16]
            loop = address in (b'\0' * 4, socket.inet_pton(2, '127.0.0.1')) if family == 2 else address in (b'\0' * 16, socket.inet_pton(23, '::1'))
            if actual_port == port and loop:
                found.append(int.from_bytes(row[pid_offset:pid_offset + 4], 'little'))
    return tuple(sorted(set(found)))


def process_snapshot(process):
    if os.name != 'nt':
        raise RuntimeError('OWNER_PLATFORM')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    handle = wintypes.HANDLE(int(process._handle))
    kernel.GetProcessId.argtypes = [wintypes.HANDLE]
    kernel.GetProcessId.restype = wintypes.DWORD
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    times = [wintypes.FILETIME() for _ in range(4)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        raise RuntimeError('OWNER_PROCESS')
    alive = kernel.WaitForSingleObject(handle, 0) == 258
    if not alive:
        return dict(pid=int(kernel.GetProcessId(handle)), creation=(times[0].dwHighDateTime << 32) | times[0].dwLowDateTime,
                    alive=False, image=None)
    size = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        raise RuntimeError('OWNER_IMAGE')
    return dict(pid=int(kernel.GetProcessId(handle)), creation=(times[0].dwHighDateTime << 32) | times[0].dwLowDateTime,
                alive=True, image=str(Path(buffer.value).resolve()))


class Owner:
    def __init__(self, process, profile, *, snapshot=process_snapshot, listeners=listener_owners):
        self.process, self.profile = process, profile
        self.snapshot, self.listeners = snapshot, listeners
        self.start = snapshot(process)
        self.integrity = True
        self.runtime_identity = None
        self.sources = source_hashes()

    def light(self):
        try:
            now = self.snapshot(self.process)
            if not now['alive'] or now != self.start or self.listeners(existing.PORT) != (now['pid'],):
                raise RuntimeError('OWNER_DRIFT')
        except Exception:
            self.integrity = False
            raise

    def full(self, runtime=None):
        self.light()
        try:
            if Path(self.start['image']).resolve() != Path(self.profile['argv'][0]).resolve():
                raise RuntimeError('IMAGE_DRIFT')
            for identity in (self.profile['model'], *self.profile['runtime_files'], self.profile['config']):
                if existing.file_identity(identity['path']) != identity:
                    raise RuntimeError('FILE_DRIFT')
            if self.sources != source_hashes():
                raise RuntimeError('SOURCE_DRIFT')
            if runtime is not None:
                if Path(runtime['model_path']).resolve() != Path(self.profile['model']['path']).resolve():
                    raise RuntimeError('MODEL_DRIFT')
                if self.runtime_identity is None:
                    self.runtime_identity = json.loads(q.wire(runtime))
                elif runtime != self.runtime_identity:
                    raise RuntimeError('RUNTIME_DRIFT')
        except Exception:
            self.integrity = False
            raise


class TransportRecorder:
    def __init__(self):
        self.connects = 0
        self.fixed = False
        self.connection = None
        self.closed = False
        self.events = []

    def trace(self, name, info):
        if name == 'connection.connect_tcp.started':
            if self.fixed or self.connects:
                raise RuntimeError('RECONNECT_FORBIDDEN')
            self.connects += 1
        if name == 'connection.close.started':
            self.closed = True
        if name.startswith('connection.'):
            self.events.append(name)

    def observe(self, response):
        stream = response.extensions.get('network_stream')
        if stream is None or self.connects != 1 or self.closed:
            raise RuntimeError('CONNECTION_UNOBSERVED')
        if self.connection is None:
            self.connection = stream
        elif stream is not self.connection:
            raise RuntimeError('CONNECTION_DRIFT')
        if response.headers.get('connection', '').lower() == 'close':
            raise RuntimeError('DISCONNECT')


class Runtime:
    def __init__(self, owner):
        self.owner = owner
        self.recorder = TransportRecorder()
        self.client = httpx.Client(trust_env=False, timeout=60, transport=httpx.HTTPTransport(retries=0,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1, keepalive_expiry=None)))
        self.utility_calls = self.generation_calls = 0
        self.token_cache = {}

    def request(self, endpoint, body=None, *, generation=False, readiness=False, timeout=60, wire_payload=None):
        if readiness and (endpoint != '/health' or generation or body is not None):
            raise ValueError('READINESS_ENDPOINT')
        if wire_payload is not None and (body is None or wire_payload != q.wire(body)):
            raise ValueError('REQUEST_WIRE')
        self.owner.light()
        if self.recorder.closed or (generation and (not self.recorder.fixed or self.recorder.connection is None)):
            self.owner.integrity = False
            raise RuntimeError('CONNECTION_NOT_FIXED')
        if generation:
            self.generation_calls += 1
        else:
            self.utility_calls += 1
        try:
            expired = threading.Event()
            def abort_timeout():
                expired.set()
                self.owner.integrity = False
                # Killing only the retained owned process also unblocks a
                # trickling response; httpx's per-read timeout is not total.
                if self.owner.process.poll() is None:
                    self.owner.process.kill()
            watchdog = threading.Timer(min(60, timeout), abort_timeout)
            watchdog.daemon = True
            watchdog.start()
            response = self.client.request('GET' if body is None else 'POST',
                f'http://127.0.0.1:{existing.PORT}{endpoint}', content=None if body is None else (wire_payload if wire_payload is not None else q.wire(body)),
                headers={'Content-Type': 'application/json'}, extensions={'trace': self.recorder.trace}, timeout=min(60, timeout))
            self.recorder.observe(response)
            # Even a known not-ready response fixes the one allowed connection.
            self.recorder.fixed = True
            self.owner.light()
            if expired.is_set():
                raise RuntimeError('REQUEST_TIMEOUT')
            if readiness:
                value = response.json()
                if response.status_code == 200 and value == {'status': 'ok'}:
                    return True
                if response.status_code == 503 and value in (
                        {'status': 'loading model'},
                        {'error': {'code': 503, 'message': 'Loading model', 'type': 'unavailable_error'}}):
                    return False
                raise RuntimeError('READINESS_RESPONSE')
            response.raise_for_status()
            return response.json()
        except Exception:
            self.owner.integrity = False
            self.owner.full()
            raise
        finally:
            if 'watchdog' in locals():
                watchdog.cancel()
                watchdog.join()

    def identity(self):
        props = self.request('/props')
        slots = self.request('/slots')
        if len(slots) != 1 or slots[0]['n_ctx'] != 8192 or slots[0]['is_processing']:
            raise RuntimeError('SLOT_CONTRACT')
        return dict(build=props['build_info'], model_path=props['model_path'],
                    template_sha256=q.digest(props['chat_template']), n_ctx=8192,
                    generation_settings=props['default_generation_settings'])

    def wait_ready(self, deadline, *, clock=time.monotonic, sleep=time.sleep):
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                self.owner.integrity = False
                raise RuntimeError('LOAD_TIMEOUT')
            if self.request('/health', readiness=True, timeout=min(2, remaining)):
                return
            sleep(min(.1, max(0, deadline - clock())))

    def count(self, raw, *, prompt=False):
        key = (raw, prompt)
        if key in self.token_cache:
            return self.token_cache[key]
        data = self.request('/tokenize', dict(content=raw, add_special=prompt, parse_special=True))
        tokens = data['tokens']
        if not isinstance(tokens, list) or any(type(t) is not int or t < 0 for t in tokens):
            raise RuntimeError('TOKEN_TYPE')
        self.token_cache[key] = len(tokens)
        return len(tokens)

    def input_count(self, body):
        rendered = self.request('/apply-template', body)['prompt']
        if not isinstance(rendered, str):
            raise RuntimeError('TEMPLATE_TYPE')
        return self.count(rendered, prompt=True), q.digest(rendered)

    def close(self):
        self.client.close()


def measure(fixtures, rows, runtime, private):
    maxima = {s: 0 for s in q.STAGES}
    witness_sets = {}
    for f in fixtures:
        for stage in ((f.stage, 'message') if f.stage == 'chat_plan' else (f.stage,)):
            values = q.witnesses(stage, f)
            witness_sets[f.case.case_id, stage] = values
            counted = []
            for raw in values:
                count = runtime.count(raw.decode())
                maxima[stage] = max(maxima[stage], count)
                counted.append(dict(raw=raw.decode(), sha256=q.digest(raw), tokens=count))
            write_once(private / f'witness-{f.case.case_id}-{stage}.json', counted)
    budgets = {s: n + 32 for s, n in maxima.items()}
    if any(maxima[s] <= 0 or budgets[s] > 512 for s in q.STAGES):
        raise ValueError('PF3_BUDGET')
    cache = []
    for f, seed in rows:
        stages = [(f.stage, None)]
        if f.stage == 'chat_plan':
            stages += [('message', q.product.parse_and_validate_generation_v2_candidate_structure('chat_plan', raw, f.catalog))
                       for raw in witness_sets[f.case.case_id, 'chat_plan']]
        measured = []
        for stage, candidate in stages:
            for ordinal in ((1,) if candidate is not None else (1, 2)):
                request = q.body(stage, f, derived_seed(seed, stage, ordinal), budgets[stage], candidate)
                tokens, rendered_sha = runtime.input_count(request)
                if tokens + budgets[stage] + 1 > 8192:
                    raise ValueError('PF3_CONTEXT')
                measured.append(dict(stage=stage, ordinal=ordinal,
                    plan_sha256=q.digest(q.thaw(candidate.value)) if candidate else None,
                    wire=q.wire(request).decode(), wire_sha256=q.digest(q.wire(request)),
                    input_tokens=tokens, rendered_sha256=rendered_sha))
        record = dict(case_id=f.case.case_id, seed=seed, projection_sha256=q.digest(f.source_bytes),
            catalog=asdict(f.catalog), bindings=f.bindings, measured=measured)
        write_once(private / f'cache-{f.case.case_id}-{seed}.json', record)
        cache.append(record)
    return budgets, cache


def derived_seed(seed, stage, ordinal):
    if ordinal not in (1, 2) or stage not in q.STAGES or seed not in q.SEEDS:
        raise ValueError('REQUEST_KEY')
    return seed + ordinal * 100003 + q.STAGES.index(stage) * 1000003


@dataclass(frozen=True)
class CachedRequest:
    key: tuple
    wire: bytes
    wire_sha256: str
    input_tokens: int
    rendered_sha256: str


class RequestCache:
    """Frozen non-P exact requests plus one sealed P pair per accepted plan.

    Budget witnesses never delimit the set of accepted runtime plans.
    """
    def __init__(self, records):
        entries = {}
        for row in records:
            for item in row['measured']:
                if item['stage'] == 'message':
                    continue  # Context/budget witnesses, not runtime plan membership.
                key = (row['case_id'], row['seed'], item['stage'], item['ordinal'], None)
                if key in entries:
                    raise ValueError('CACHE_DUPLICATE')
                entries[key] = CachedRequest(key, item['wire'].encode(), item['wire_sha256'],
                                            item['input_tokens'], item['rendered_sha256'])
        self._entries = MappingProxyType(entries)
        self._p_pairs = {}
        self._p_semantic = {}
        self._p_roots = {}
        self._root = self._digest(entries)

    @staticmethod
    def _digest(entries):
        return q.digest([dict(key=list(k), wire_sha256=q.digest(e.wire), expected=e.wire_sha256,
                             tokens=e.input_tokens, rendered=e.rendered_sha256)
                         for k, e in sorted(entries.items())])

    def lookup(self, key):
        if self._digest(self._entries) != self._root:
            raise ValueError('CACHE_DRIFT')
        table = self._p_pairs.get(key[:2], self._entries) if key[2] == 'message' else self._entries
        if key[2] == 'message' and self._digest(table) != self._p_roots.get(key[:2]):
            raise ValueError('CACHE_DRIFT')
        try:
            entry = table[key]
        except KeyError:
            raise ValueError('CACHE_KEY') from None
        if entry.key != key or q.digest(entry.wire) != entry.wire_sha256:
            raise ValueError('CACHE_WIRE')
        body = json.loads(entry.wire)
        if body['seed'] != derived_seed(key[1], key[2], key[3]):
            raise ValueError('CACHE_SEED')
        if key[2] == 'message' and q.digest({k: body[k] for k in ('messages', 'response_format')}) != self._p_semantic[key[:2]]:
            raise ValueError('CACHE_SEMANTIC')
        return entry

    def prepare_message(self, fixture, seed, plan, budget, runtime, private):
        slot = (fixture.case.case_id, seed)
        plan_hash = q.digest(q.thaw(plan.value))
        if slot in self._p_pairs:
            self.lookup((*slot, 'message', 1, plan_hash))
            return
        # Exactly once for this accepted immutable plan, including witness-external
        # legal combinations. The second sample changes only request identity/seed.
        body = q.body('message', fixture, derived_seed(seed, 'message', 1), budget, plan)
        q.verify_presenter(json.loads(body['messages'][1]['content']), plan, fixture)
        tokens, rendered = runtime.input_count(body)
        if tokens + budget + 1 > 8192:
            raise ValueError('P_CONTEXT_PREFLIGHT_MISS')
        semantic = q.digest({k: body[k] for k in ('messages', 'response_format')})
        entries = {}
        for ordinal in (1, 2):
            request = json.loads(q.wire(body))
            request['seed'] = derived_seed(seed, 'message', ordinal)
            raw = q.wire(request)
            key = (*slot, 'message', ordinal, plan_hash)
            entries[key] = CachedRequest(key, raw, q.digest(raw), tokens, rendered)
        write_once(private / f'p-cache-{fixture.case.case_id}-{seed}.json',
                   dict(plan_sha256=plan_hash, semantic_sha256=semantic, entries=[
                       dict(key=list(k), wire=e.wire.decode(), wire_sha256=e.wire_sha256,
                            input_tokens=tokens, rendered_sha256=rendered) for k, e in entries.items()]))
        self._p_semantic[slot] = semantic
        self._p_pairs[slot] = MappingProxyType(entries)
        self._p_roots[slot] = self._digest(entries)


def estimate(attempts, row_times, fixed):
    if len(row_times) != 2 or tuple((r['case_id'], r['seed']) for r in row_times) != PROBE:
        raise ValueError('PROBE_DOMAIN')
    if set(a['stage'] for a in attempts) != {'chat_plan', 'message', 'co_opportunity'}:
        raise ValueError('PROBE_STAGE')
    numeric = [fixed, *(a[k] for a in attempts for k in ('elapsed', 'provider_latency')),
               *(r['elapsed'] for r in row_times)]
    if any(type(n) not in (int, float) or not math.isfinite(n) or n < 0 for n in numeric):
        raise ValueError('PROBE_CLOCK')
    if any(a['status'] != 'ACCEPTED' or a['elapsed'] < a['provider_latency'] for a in attempts):
        raise ValueError('PROBE_FAILURE')
    overhead = max(a['elapsed'] - a['provider_latency'] for a in attempts)
    row_overhead = [r['elapsed'] - sum(a['elapsed'] for a in attempts if a['case_id'] == r['case_id']) for r in row_times]
    if min(row_overhead) < 0:
        raise ValueError('PROBE_CLOCK')
    maxima = {s: max(a['elapsed'] for a in attempts if a['stage'] == s) for s in ('chat_plan', 'message', 'co_opportunity')}
    seconds = fixed + 96 * max(row_overhead) + 2 * (84 * maxima['chat_plan'] + 84 * maxima['message'] + 6 * (60 + overhead) + 6 * maxima['co_opportunity'])
    return dict(status='SMOKE_ESTIMATE_ONLY', seconds=seconds, proceed=seconds <= 3600,
                H_ATTEMPT=overhead, H_ROW=max(row_overhead), H_FIXED=fixed, A_PRE=60 + overhead, **maxima)


def attempt(runtime, fixture, seed, stage, budget, ordinal, private, *, cache, plan=None, clock=time.monotonic):
    started = clock()
    outcome = dict(case_id=fixture.case.case_id, seed=seed, stage=stage, ordinal=ordinal,
                   status='MISSING_RESPONSE', provider_latency=0, generation_sent=False)
    stem = f'{fixture.case.case_id}-{seed}-{stage}-{ordinal}'
    parsed = None
    try:
        if stage == 'message':
            cache.prepare_message(fixture, seed, plan, budget, runtime, private)
        key = (fixture.case.case_id, seed, stage, ordinal, q.digest(q.thaw(plan.value)) if plan else None)
        entry = cache.lookup(key)
        request = json.loads(entry.wire)
        prompt_tokens = entry.input_tokens
        write_once(private / (stem + '-request.json'), entry.wire)
        write_once(private / (stem + '-input-count.json'), {'tokens': prompt_tokens,
                   'rendered_sha256': entry.rendered_sha256, 'wire_sha256': entry.wire_sha256})
        if prompt_tokens + budget + 1 > 8192:
            raise ValueError('CONTEXT_OVERFLOW')
        t = clock()
        before = runtime.generation_calls
        try:
            response = runtime.request('/v1/chat/completions', request, generation=True, wire_payload=entry.wire)
        finally:
            outcome['provider_latency'] = clock() - t
            outcome['generation_sent'] = runtime.generation_calls > before
        write_once(private / (stem + '-response.json'), response)
        choice, usage = response['choices'][0], response['usage']
        outcome.update(finish_reason=choice['finish_reason'], prompt_tokens=usage['prompt_tokens'], completion_tokens=usage['completion_tokens'])
        if (type(usage['prompt_tokens']) is not int or type(usage['completion_tokens']) is not int
                or usage['prompt_tokens'] != prompt_tokens or usage['completion_tokens'] < 0):
            raise ValueError('USAGE_MISMATCH')
        if choice['finish_reason'] == 'length' or usage['completion_tokens'] > budget:
            outcome['status'] = 'LENGTH'
        elif choice['finish_reason'] != 'stop' or choice['message'].get('reasoning_content'):
            outcome['status'] = 'MISSING_RESPONSE'
        else:
            actual_output_tokens = runtime.count(choice['message']['content'])
            outcome['native_output_tokens'] = actual_output_tokens
            if actual_output_tokens > budget:
                outcome['status'] = 'LENGTH'
                write_once(private / (stem + '-outcome.json'), outcome)
                outcome['elapsed'] = clock() - started
                write_once(private / (stem + '-clock.json'), {'elapsed': outcome['elapsed']})
                return None, outcome
            try:
                parsed = q.product.parse_and_validate_generation_v2_candidate_structure(stage, choice['message']['content'], fixture.catalog)
                outcome['status'] = 'ACCEPTED'
            except (ValueError, TypeError):
                outcome['status'] = 'STRUCTURE_INVALID'
            if parsed is not None:
                try:
                    if stage == 'message':
                        q.verify_presenter(q.presenter(plan, fixture), plan, fixture)
                        q.text_guard(parsed.value['message'], fixture)
                    elif stage == 'co_opportunity' and parsed.value['decision'] == 'DECLARE':
                        q.text_guard(parsed.value['comment'], fixture)
                except ValueError:
                    parsed = None
                    outcome['status'] = 'GUARD_REJECT'
    except Exception as error:
        runtime.owner.integrity = False
        outcome['status'] = 'P_CONTEXT_PREFLIGHT_MISS' if str(error) == 'P_CONTEXT_PREFLIGHT_MISS' else 'TRANSPORT_OR_OWNERSHIP'
    write_once(private / (stem + '-outcome.json'), outcome)
    outcome['elapsed'] = clock() - started
    write_once(private / (stem + '-clock.json'), {'elapsed': outcome['elapsed']})
    return parsed, outcome


def run_rows(rows, runtime, budgets, private, *, cache, probe, clock=time.monotonic):
    deadline = clock() + (5400 if not probe else 600)
    attempts, results = [], []
    for fixture, seed in rows:
        begin = clock()
        plan, final = None, None
        stage = fixture.stage
        while stage is not None:
            accepted = None
            for ordinal in (1, 2):
                if clock() + 60 > deadline or not runtime.owner.integrity:
                    return attempts, results, False
                accepted, outcome = attempt(runtime, fixture, seed, stage, budgets[stage], ordinal, private, cache=cache, plan=plan, clock=clock)
                attempts.append(outcome)
                if probe and outcome['status'] != 'ACCEPTED':
                    return attempts, results, False
                if not runtime.owner.integrity:
                    return attempts, results, False
                if accepted is not None:
                    break
            if accepted is None:
                final = None
                break
            if stage == 'chat_plan' and accepted.value['act'] != 'NONE':
                plan, stage = accepted, 'message'
            else:
                final, stage = accepted, None
        row = dict(case_id=fixture.case.case_id, seed=seed, elapsed=clock() - begin,
                   silence=final is None or (final.stage == 'chat_plan' and final.value['act'] == 'NONE'),
                   sample_exhausted=final is None,
                   plan=q.thaw(plan.value) if plan else None,
                   final=q.thaw(final.value) if final else None,
                   terminal_stage=attempts[-1]['stage'],
                   projection_sha256=q.digest(fixture.source_bytes))
        write_once(private / f'row-{fixture.case.case_id}-{seed}.json', row)
        results.append(row)
    return attempts, results, True


DEFAULT_FREEZE = ROOT / 'logs/t507-choice-budget/formal-binary-v2/freeze.json'
CANONICAL_CLAIM = ROOT / 'logs/t550-quality-v2.claim.json'


def claim_experiment(public_path, profile, sources):
    """One durable task claim, independent of caller-selected output spelling."""
    output = Path(public_path).resolve()
    if output == CANONICAL_CLAIM.resolve() or output.exists() or CANONICAL_CLAIM.exists():
        raise ValueError('TASK_ALREADY_CLAIMED')
    record = dict(task='T550', public_path=str(output), source_sha256=q.digest(sources),
                  profile_sha256=q.digest(profile), retry=0)
    write_once(CANONICAL_CLAIM, record)  # Exclusive create is the concurrency gate.
    return record


def load_profile(path):
    frozen = json.loads(Path(path).read_bytes())
    records = frozen['profiles']['qw9']
    argv = existing.launch_args('qw9')
    by_path = {str(Path(r['path']).resolve()): r for r in records}
    if len(by_path) != len(records):
        raise ValueError('PROFILE_DUPLICATE')
    model = by_path[str(Path(argv[argv.index('-m') + 1]).resolve())]
    if str(Path(argv[0]).resolve()) not in by_path:
        raise ValueError('PROFILE_RUNTIME')
    for record in (*records, frozen['config']):
        if existing.file_identity(record['path']) != record:
            raise ValueError('PROFILE_HASH')
    return dict(argv=argv, model=model, runtime_files=[r for r in records if r is not model],
                config=frozen['config'], freeze_sha256=q.digest(Path(path).read_bytes()))


def offline():
    fixtures, rows = q.prepare()
    counts = Counter()
    for fixture in fixtures:
        for stage in ((fixture.stage, 'message') if fixture.stage == 'chat_plan' else (fixture.stage,)):
            counts[stage] += len(q.witnesses(stage, fixture))
    return {'status': 'OFFLINE_PASS', 'rows': len(rows), 'witnesses': dict(counts), 'generation_calls': 0}


def launch(profile, private, label):
    if listener_owners(existing.PORT):
        raise RuntimeError('PORT_OCCUPIED')
    with (private / (label + '-runtime.log')).open('xb') as log:
        process = subprocess.Popen(profile['argv'], cwd=Path(profile['argv'][0]).parent,
            stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    runtime = None
    try:
        limit = time.monotonic() + 180
        while time.monotonic() < limit:
            if process.poll() is not None:
                raise RuntimeError('RUNTIME_EXIT')
            if listener_owners(existing.PORT) == (process.pid,):
                break
            time.sleep(.1)
        else:
            raise RuntimeError('LOAD_TIMEOUT')
        owner = Owner(process, profile)
        owner.full()
        runtime = Runtime(owner)
        runtime.wait_ready(limit)
        identity = runtime.identity()
        owner.full(identity)
        runtime.recorder.fixed = True
        return process, owner, runtime
    except Exception:
        try:
            if runtime is not None:
                runtime.close()
        finally:
            existing.cleanup_owned(None, process)
        raise


def actual_summary(attempts, results, generation_calls=None):
    counts = {s: dict(Counter(a['status'] for a in attempts if a['stage'] == s)) for s in q.STAGES}
    for stage in q.STAGES:
        counts[stage]['sample_exhausted'] = sum(row['sample_exhausted'] for row in results if row['terminal_stage'] == stage)
        counts[stage]['silence'] = sum(row['silence'] for row in results if row['terminal_stage'] == stage)
    return dict(rows=len(results), expected_rows=96, unrun=96 - len(results), counts=counts,
                silence=sum(r['silence'] for r in results), sample_exhausted=sum(r['sample_exhausted'] for r in results),
                actual_generation_calls=(sum(a.get('generation_sent', False) for a in attempts)
                                         if generation_calls is None else generation_calls))


def probe_summary(attempts, results):
    values = actual_summary(attempts, results)
    return {'probe_' + k: v for k, v in values.items() if k not in ('expected_rows', 'unrun', 'actual_generation_calls')}


def run(profile_path, public_path):
    """One invocation owns utility preflight, probe, and the gated one-shot run."""
    public_path = Path(public_path).resolve()
    profile = load_profile(profile_path)
    frozen_sources = source_hashes()
    fixtures, rows = q.prepare()
    claim = claim_experiment(public_path, profile, frozen_sources)
    private = create_private_evidence_container(ROOT / 'logs/phase6-private-evidence',
        evidence_kind='synthetic', task_id='T550', created_at_utc=datetime.now(timezone.utc))
    write_once(private / 'claim.json', claim)
    write_once(private / 'profile.json', profile)
    write_once(private / 'sources.json', frozen_sources)
    write_once(private / 'fixture-inputs.json', [dict(case_id=f.case.case_id,
        source=f.source, projection_sha256=q.digest(f.source_bytes), catalog=asdict(f.catalog), bindings=f.bindings) for f in fixtures])
    process = runtime = owner = None
    summary = dict(task='T550', status='UNKNOWN', run_integrity=False, generation_calls=0,
                   utility_calls=0, runtime_loads=0, rows=0, retry=0,
                   actual_generation_calls=0, probe_generation_calls=0)
    start = time.monotonic()
    probe_attempts, probe_results, actual_attempts, actual_results = [], [], [], []
    phase = 'probe'
    try:
        process, owner, runtime = launch(profile, private, 'probe')
        summary['runtime_loads'] = 1
        identity = owner.runtime_identity
        write_once(private / 'identity.json', identity)
        startup_elapsed = time.monotonic() - start
        budgets, records = measure(fixtures, rows, runtime, private)
        records_bytes = q.wire(records)
        cache_load_start = time.monotonic()
        probe_cache = RequestCache(json.loads(records_bytes))
        cache_load_elapsed = time.monotonic() - cache_load_start
        cache_paths = tuple(sorted(private.glob('cache-*.json')))
        cache_hashes = {p.name: existing.file_hash(p) for p in cache_paths}
        write_once(private / 'measurement.json', {'budgets': budgets, 'cache_hashes': cache_hashes,
            'runtime': identity, 'source': frozen_sources, 'utility_calls': runtime.utility_calls})
        if source_hashes() != frozen_sources:
            raise RuntimeError('SOURCE_DRIFT')
        probe_rows = tuple(next((f, s) for f, s in rows if (f.case.case_id, s) == key) for key in PROBE)
        fixed_start = time.monotonic()
        probe_attempts, probe_results, ok = run_rows(probe_rows, runtime, budgets, private, cache=probe_cache, probe=True)
        probe_end = time.monotonic()
        if not ok:
            summary['status'] = 'PROBE_FAILED'
        else:
            # Include elapsed non-row setup conservatively. End identity and
            # cleanup are measured below before the estimate is finalized.
            owner.full(runtime.identity())
            # Probe must actually include its end checks and cleanup before
            # using H_FIXED. The gated run gets a fresh owned process/client.
            summary['generation_calls'] += runtime.generation_calls
            summary['probe_generation_calls'] = runtime.generation_calls
            summary['utility_calls'] += runtime.utility_calls
            runtime.close()
            runtime = None
            probe_cleanup = existing.cleanup_owned(None, process)
            if probe_cleanup['owned_processes_remaining'] or listener_owners(existing.PORT):
                raise RuntimeError('PROBE_CLEANUP')
            write_once(private / 'probe-cleanup.json', probe_cleanup)
            estimate_value = estimate(probe_attempts, probe_results,
                                      startup_elapsed + cache_load_elapsed + (time.monotonic() - probe_end))
            write_once(private / 'probe-estimate.json', estimate_value)
            summary['estimate_seconds'] = estimate_value['seconds']
            if not estimate_value['proceed']:
                summary['status'] = 'ESTIMATE_STOP'
            else:
                # Distinct durable run directory prevents probe/run key reuse.
                actual = private / 'actual'
                actual.mkdir(mode=0o700)
                write_once(actual / 'claim.json', {'rows': 96, 'retry': 0})
                if source_hashes() != frozen_sources or any(existing.file_hash(private / name) != h for name, h in cache_hashes.items()):
                    raise RuntimeError('CACHE_DRIFT')
                phase = 'actual'
                process, owner, runtime = launch(profile, private, 'actual')
                summary['runtime_loads'] += 1
                if owner.runtime_identity != identity:
                    raise RuntimeError('RUNTIME_DRIFT')
                actual_attempts, actual_results, ok = run_rows(rows, runtime, budgets, actual, cache=RequestCache(json.loads(records_bytes)), probe=False)
                summary['status'] = 'COMPLETE' if ok and len(actual_results) == 96 else 'INCOMPLETE'
        if source_hashes() != frozen_sources or any(existing.file_hash(private / name) != h for name, h in cache_hashes.items()):
            raise RuntimeError('CACHE_DRIFT')
        if runtime is not None:
            owner.full(runtime.identity())
        summary.update(run_integrity=owner.integrity, budgets=budgets)
    except Exception as error:
        summary['status'] = 'UNKNOWN'
        summary['failure_type'] = type(error).__name__
        write_once(private / 'failure.json', {'type': type(error).__name__, 'detail': str(error)})
    finally:
        if runtime is not None:
            summary['generation_calls'] += runtime.generation_calls
            summary[phase + '_generation_calls'] = runtime.generation_calls
            summary['utility_calls'] += runtime.utility_calls
            try:
                runtime.close()
            except Exception:
                summary['run_integrity'] = False
        cleanup = existing.cleanup_owned(None, process)
        try:
            gone = not listener_owners(existing.PORT)
        except Exception:
            gone = False
        summary['cleanup_confirmed'] = cleanup['owned_processes_remaining'] == 0 and gone
        if not summary['cleanup_confirmed']:
            summary['run_integrity'] = False
        if not summary['run_integrity'] and summary['status'] == 'COMPLETE':
            summary['status'] = 'UNKNOWN'
        summary['duration'] = time.monotonic() - start
        summary.update(actual_summary(actual_attempts, actual_results, summary['actual_generation_calls']))
        summary.update(probe_summary(probe_attempts, probe_results))
        write_once(private / 'cleanup.json', cleanup)
        seal = {p.relative_to(private).as_posix(): existing.file_hash(p) for p in sorted(private.rglob('*')) if p.is_file()}
        summary['private_seal_sha256'] = write_once(private / 'seal.json', seal)
        write_once(public_path, summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--offline', action='store_true')
    mode.add_argument('--run', action='store_true')
    parser.add_argument('--profile-freeze', type=Path, default=DEFAULT_FREEZE)
    parser.add_argument('--public', type=Path)
    args = parser.parse_args()
    if args.run and args.public is None:
        parser.error('--public required for --run')
    result = offline() if args.offline else run(args.profile_freeze, args.public)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['status'] in ('OFFLINE_PASS', 'COMPLETE') else 1


if __name__ == '__main__':
    raise SystemExit(main())
