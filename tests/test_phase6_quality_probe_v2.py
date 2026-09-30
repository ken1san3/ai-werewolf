from dataclasses import replace
import json
from types import SimpleNamespace
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

import pytest

from scripts import phase6_quality_probe_v2 as q
from scripts import phase6_quality_runner_v2 as r


@pytest.fixture(scope='module')
def suite():
    return q.prepare()


def test_suite_original_96_domain_and_prior(suite):
    fs, rows = suite
    assert len(rows) == len({(f.case.case_id, s) for f, s in rows}) == 96
    assert sum(f.stage == 'chat_plan' for f in fs) == 28
    assert [f.case.case_id for f in fs if f.stage == 'pre_vote'] == ['G13-1', 'G13-2']
    assert [f.case.case_id for f in fs if f.stage == 'co_opportunity'] == ['G15-1', 'G15-2']
    f = next(f for f in fs if f.case.case_id == 'G04-1')
    assert {b.prior for b in f.catalog.opinion_bases} == {80, 20}
    assert all(b.allowed_current == (0, 25, 50, 75, 100) for b in f.catalog.opinion_bases)


def test_all_maximal_witnesses_use_real_product_validator(suite):
    fs, _ = suite
    prepared = r.prepare_witnesses(fs)
    for f in fs:
        for stage in ((f.stage, 'message') if f.stage == 'chat_plan' else (f.stage,)):
            raws = prepared[f.case.case_id, stage]
            assert raws and len(raws) == len(set(raws))
            for raw in raws:
                parsed = q.product.parse_and_validate_generation_v2_candidate_structure(stage, raw, f.catalog)
                assert q.wire(q.thaw(parsed.value)) == raw
                if stage == 'message':
                    assert len(parsed.value['message']) == 200
                    assert len(parsed.value['message'].encode()) <= 600


def test_pf1_actual_bytes_reject_sorting(suite):
    f = suite[0][0]
    body = q.body('chat_plan', f, 4242027, 100)
    assert q.pf1(q.wire(body), f)
    with pytest.raises(ValueError, match='PF1'):
        q.pf1(json.dumps(body, sort_keys=True).encode(), f)


@pytest.mark.parametrize('key', ['role', 'team', 'count_as', 'abilities', 'win_conditions', 'state', 'other_channel'])
def test_presenter_unknown_field_rejected(suite, key):
    f = suite[0][0]
    plan = q.product.parse_and_validate_generation_v2_candidate_structure('chat_plan', q.witnesses('chat_plan', f)[0], f.catalog)
    p = q.presenter(plan, f)
    assert tuple(p) == q.P_KEYS
    q.verify_presenter(p, plan, f)
    p['current'][key] = 'foreign'
    with pytest.raises(ValueError, match='PROVENANCE'):
        q.verify_presenter(p, plan, f)


def test_presenter_selected_disclosure_and_legacy_rejection(suite):
    f = next(f for f in suite[0] if f.catalog.disclose_ids and f.stage == 'chat_plan')
    plan = next(q.product.parse_and_validate_generation_v2_candidate_structure('chat_plan', raw, f.catalog)
                for raw in q.witnesses('chat_plan', f) if not json.loads(raw)['disclose_ids'])
    p = q.presenter(plan, f)
    assert p['selected_disclosures'] == []
    p['selected_disclosures'] = [q.selected(f, f.catalog.disclose_ids[0])]
    with pytest.raises(ValueError, match='PROVENANCE'):
        q.verify_presenter(p, plan, f)
    with pytest.raises(ValueError, match='V2_PLAN'):
        q.presenter({'decision': {}}, f)
    with pytest.raises(ValueError, match='PRIVATE_RECIPIENT'):
        q.presenter(plan, replace(f, channel='foreign'))


def test_foreign_projection_binding_rejected(suite):
    a, b = suite[0][:2]
    with pytest.raises(ValueError, match='PROJECTION_BINDING'):
        q.verify_bindings(replace(a, binding_bytes=b.binding_bytes))


def probe_times(overhead=0):
    attempts = [dict(case_id='G01-1', stage='chat_plan', status='ACCEPTED', elapsed=1 + overhead, provider_latency=1),
                dict(case_id='G01-1', stage='message', status='ACCEPTED', elapsed=2 + overhead, provider_latency=2),
                dict(case_id='G15-1', stage='co_opportunity', status='ACCEPTED', elapsed=3 + overhead, provider_latency=3)]
    rows = [dict(case_id='G01-1', seed=4242027, elapsed=4 + 2 * overhead),
            dict(case_id='G15-1', seed=4242027, elapsed=4 + overhead)]
    return attempts, rows


def test_full_attempt_estimate_includes_second_sample_nonprovider_cost():
    a, rows = probe_times()
    value = r.estimate(a, rows, 5)
    assert value['seconds'] == 5 + 96 + 2 * (84 + 168 + 360 + 18)
    assert value['A_PRE'] == 60
    a, rows = probe_times(10)
    slow = r.estimate(a, rows, 5)
    assert slow['H_ATTEMPT'] == 10 and slow['A_PRE'] == 70
    assert slow['seconds'] - value['seconds'] == 2 * (84 + 84 + 6 + 6) * 10
    assert not slow['proceed']


@pytest.mark.parametrize('status', ['LENGTH', 'GUARD_REJECT', 'STRUCTURE_INVALID', 'TRANSPORT_OR_OWNERSHIP', 'MISSING_RESPONSE'])
def test_probe_any_failure_stops_estimate(status):
    a, rows = probe_times()
    a[0]['status'] = status
    with pytest.raises(ValueError):
        r.estimate(a, rows, 0)


@pytest.mark.parametrize('mutate', ['pid', 'creation', 'alive', 'missing', 'second'])
def test_owner_light_drift_fails_closed(monkeypatch, mutate):
    monkeypatch.setattr(r, 'source_hashes', lambda: {})
    snapshot = dict(pid=12, creation=3, alive=True, image='test.exe')
    owners = [12]
    owner = r.Owner(object(), {}, snapshot=lambda p: dict(snapshot), listeners=lambda p: tuple(owners))
    owner.light()
    if mutate == 'missing':
        owners.clear()
    elif mutate == 'second':
        owners.append(13)
    elif mutate == 'alive':
        snapshot['alive'] = False
    else:
        snapshot[mutate] += 1
    with pytest.raises(RuntimeError):
        owner.light()
    assert not owner.integrity


def test_transport_requires_actual_connection_and_rejects_reconnect():
    rec = r.TransportRecorder()
    response = SimpleNamespace(extensions={}, headers={})
    with pytest.raises(RuntimeError):
        rec.observe(response)
    rec.trace('connection.connect_tcp.started', {})
    response.extensions['network_stream'] = object()
    rec.observe(response)
    rec.fixed = True
    rec.observe(response)
    with pytest.raises(RuntimeError, match='RECONNECT'):
        rec.trace('connection.connect_tcp.started', {})
    response.extensions['network_stream'] = object()
    with pytest.raises(RuntimeError, match='DRIFT'):
        rec.observe(response)


def test_real_http_transport_events_and_windows_listener_table():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'{}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert r.listener_owners(server.server_port) == (os.getpid(),)
        rec = r.TransportRecorder()
        with httpx.Client(trust_env=False) as client:
            for _ in range(2):
                response = client.get(f'http://127.0.0.1:{server.server_port}/', extensions={'trace': rec.trace})
                rec.observe(response)
                rec.fixed = True
        assert rec.connects == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class FakeRuntime:
    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.owner = SimpleNamespace(integrity=True)

    @property
    def generation_calls(self):
        return self.calls

    def request(self, *args, **kwargs):
        self.calls += 1
        return self.response

    def input_count(self, body):
        return 1, 'a' * 64

    def count(self, text):
        return 1


def request_cache(fixture, seed=4242027, budget=100):
    measured = []
    for ordinal in (1, 2):
        body = q.body(fixture.stage, fixture, r.derived_seed(seed, fixture.stage, ordinal), budget)
        measured.append(dict(stage=fixture.stage, ordinal=ordinal, wire=q.wire(body).decode(),
            wire_sha256=q.digest(q.wire(body)), input_tokens=1, rendered_sha256='a' * 64))
    return r.RequestCache([dict(case_id=fixture.case.case_id, seed=seed, measured=measured)])


@pytest.mark.parametrize('finish,content,status', [('length', 'not JSON', 'LENGTH'),
    ('stop', 'not JSON', 'STRUCTURE_INVALID'), ('content_filter', '{}', 'MISSING_RESPONSE')])
def test_attempt_length_not_parsed_and_two_samples_exhaust(suite, tmp_path, finish, content, status):
    runtime = FakeRuntime({'choices': [{'finish_reason': finish, 'message': {'content': content}}],
                           'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})
    f = suite[0][0]
    attempts, rows, ok = r.run_rows([(f, 4242027)], runtime, {f.stage: 100}, tmp_path, cache=request_cache(f), probe=False)
    assert ok and runtime.calls == 2
    assert [a['status'] for a in attempts] == [status, status]
    assert rows[0]['silence'] and rows[0]['sample_exhausted']


def test_probe_stops_after_first_bad_attempt(suite, tmp_path):
    runtime = FakeRuntime({'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}],
                           'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})
    f = suite[0][0]
    attempts, rows, ok = r.run_rows([(f, 4242027)], runtime, {f.stage: 100}, tmp_path, cache=request_cache(f), probe=True)
    assert not ok and runtime.calls == 1 and len(attempts) == 1


def test_attempt_clock_includes_preparation_and_durable_recording(suite, tmp_path, monkeypatch):
    now = [0.0]
    original = r.write_once
    def delayed(*args):
        result = original(*args)
        now[0] += 4
        return result
    monkeypatch.setattr(r, 'write_once', delayed)
    runtime = FakeRuntime({'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}],
                           'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})
    _, outcome = r.attempt(runtime, suite[0][0], 4242027, 'chat_plan', 100, 1, tmp_path, cache=request_cache(suite[0][0]), clock=lambda: now[0])
    assert outcome['elapsed'] == 16 and outcome['provider_latency'] == 0


@pytest.mark.parametrize('fault', ['budget', 'context'])
def test_pf3_budget_and_context_fail_before_generation(suite, tmp_path, monkeypatch, fault):
    fixtures = tuple(next(f for f in suite[0] if f.stage == stage) for stage in ('chat_plan', 'pre_vote', 'co_opportunity'))
    original = q.witnesses
    # The complete finite enumeration is tested above; here each real stage
    # supplies a valid specimen to isolate the token-bound failure.
    samples = {(f.case.case_id, stage): original(stage, f)[:1] for f in fixtures
               for stage in ((f.stage, 'message') if f.stage == 'chat_plan' else (f.stage,))}
    monkeypatch.setattr(q, 'witnesses', lambda stage, f: samples[f.case.case_id, stage])
    runtime = SimpleNamespace(count=lambda raw: 481 if fault == 'budget' else 1,
                              input_count=lambda body: (8191, 'a' * 64))
    with pytest.raises(ValueError, match='PF3_' + fault.upper()):
        r.measure(fixtures, [(f, 4242027) for f in fixtures], runtime, tmp_path,
                  witness_sets=r.prepare_witnesses(fixtures))


@pytest.mark.parametrize('fault', ['runtime', 'file', 'source'])
def test_full_identity_drift_is_not_replaced_by_light_check(monkeypatch, fault):
    hashes = {'source': 'first'}
    monkeypatch.setattr(r, 'source_hashes', lambda: dict(hashes))
    snap = dict(pid=12, creation=3, alive=True, image=str(r.ROOT / 'runtime.exe'))
    identity = dict(path=str(r.ROOT / 'model.gguf'), sha256='fixed')
    profile = dict(argv=[snap['image']], model=identity, runtime_files=[], config=identity)
    monkeypatch.setattr(r.existing, 'file_identity', lambda path: dict(identity))
    owner = r.Owner(object(), profile, snapshot=lambda p: dict(snap), listeners=lambda p: (12,))
    runtime = dict(model_path=identity['path'], build='first')
    owner.full(runtime)
    if fault == 'runtime':
        runtime['build'] = 'second'
    elif fault == 'source':
        hashes['source'] = 'second'
    else:
        monkeypatch.setattr(r.existing, 'file_identity', lambda path: {**identity, 'sha256': 'second'})
    with pytest.raises(RuntimeError):
        owner.full(runtime)
    assert not owner.integrity


def test_guard_rejection_and_successful_two_stage_boundary(suite, tmp_path):
    f = suite[0][0]
    raw = next(raw for raw in q.witnesses('chat_plan', f) if json.loads(raw)['act'] == 'ANSWER')
    class SequenceRuntime(FakeRuntime):
        def request(self, *args, **kwargs):
            self.calls += 1
            content = raw.decode() if self.calls == 1 else q.wire({'message': 'I will reconsider that evidence.'}).decode()
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
                    'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}
    runtime = SequenceRuntime(None)
    attempts, results, ok = r.run_rows([(f, 4242027)], runtime, {'chat_plan': 100, 'message': 100}, tmp_path, cache=request_cache(f), probe=False)
    assert ok and runtime.calls == 2 and not results[0]['silence']
    assert [a['stage'] for a in attempts] == ['chat_plan', 'message']
    assert results[0]['plan']['act'] == 'ANSWER'


def test_actual_owned_process_snapshot_uses_retained_handle():
    import subprocess
    import sys
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'],
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        before = r.process_snapshot(process)
        assert before['alive'] and before['pid'] == process.pid and before['creation'] > 0
        assert r.process_snapshot(process) == before
        process.terminate()
        process.wait(timeout=5)
        after = r.process_snapshot(process)
        assert not after['alive'] and after['creation'] == before['creation']
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize('close_fails', [False, True])
def test_start_identity_failure_always_cleans_owned_process(tmp_path, monkeypatch, close_fails):
    events = []
    process = SimpleNamespace(pid=91, poll=lambda: None)
    table_reads = [(), (91,)]
    monkeypatch.setattr(r, 'listener_owners', lambda port: table_reads.pop(0))
    monkeypatch.setattr(r.subprocess, 'Popen', lambda *a, **k: process)
    monkeypatch.setattr(r, 'Owner', lambda *a: SimpleNamespace(full=lambda *a: None))
    def close():
        events.append('close')
        if close_fails:
            raise RuntimeError('CLOSE_ERROR')
    def identity():
        raise RuntimeError('IDENTITY_ERROR')
    monkeypatch.setattr(r, 'Runtime', lambda owner: SimpleNamespace(identity=identity, close=close, wait_ready=lambda deadline: None))
    monkeypatch.setattr(r.existing, 'cleanup_owned', lambda monitor, p: events.append(('cleanup', p is process)))
    with pytest.raises(RuntimeError):
        r.launch({'argv': [str(tmp_path / 'fake.exe')]}, tmp_path, 'test')
    assert events == ['close', ('cleanup', True)]


def test_readiness_503_then_ready_on_one_real_connection(monkeypatch):
    observed = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_GET(self):
            observed.append(self.connection.fileno())
            ready = len(observed) == 3
            raw = q.wire({'status': 'ok' if ready else 'loading model'})
            self.send_response(200 if ready else 503)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(r.existing, 'PORT', server.server_port)
    owner = SimpleNamespace(integrity=True, light=lambda: None, full=lambda: None)
    runtime = r.Runtime(owner)
    try:
        runtime.wait_ready(r.time.monotonic() + 5)
        assert owner.integrity and runtime.generation_calls == 0
        assert runtime.utility_calls == 3 and runtime.recorder.connects == 1
        assert len(set(observed)) == 1 and runtime.recorder.fixed
    finally:
        runtime.close()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('alias', ['other.json', 'result.txt', 'same', 'absolute'])
def test_canonical_task_claim_survives_output_alias_and_unknown(tmp_path, monkeypatch, alias):
    canonical = tmp_path / 'canonical.claim'
    monkeypatch.setattr(r, 'CANONICAL_CLAIM', canonical)
    output = tmp_path / 'result.json'
    claim = r.claim_experiment(output, {'profile': 'fixed'}, {'source': 'fixed'})
    original = canonical.read_bytes()
    # A private claim remains even when generation never starts and no public
    # result exists; neither different output spelling nor UNKNOWN permits retry.
    r.write_once(tmp_path / 'private-claim.json', claim)
    candidate = output if alias == 'same' else output.resolve() if alias == 'absolute' else tmp_path / alias
    with pytest.raises(ValueError, match='TASK_ALREADY_CLAIMED'):
        r.claim_experiment(candidate, {'profile': 'fixed'}, {'source': 'fixed'})
    assert canonical.read_bytes() == original


@pytest.mark.parametrize('stage', ['chat_plan', 'pre_vote', 'co_opportunity'])
def test_exact_T_cache_survives_builder_change_and_binds_seed(suite, tmp_path, monkeypatch, stage):
    f = next(f for f in suite[0] if f.stage == stage)
    cache = request_cache(f)
    expected = cache.lookup((f.case.case_id, 4242027, stage, 1, None)).wire
    monkeypatch.setattr(q, 'body', lambda *a, **k: (_ for _ in ()).throw(AssertionError('REBUILD')))
    class Recording(FakeRuntime):
        def request(self, endpoint, body, **kwargs):
            assert kwargs['wire_payload'] == expected == q.wire(body)
            return super().request(endpoint, body, **kwargs)
    runtime = Recording({'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}],
                         'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})
    _, result = r.attempt(runtime, f, 4242027, stage, 100, 1, tmp_path, cache=cache)
    assert result['status'] == 'LENGTH' and runtime.calls == 1


@pytest.mark.parametrize('field', ['wire', 'wire_sha256', 'key'])
def test_exact_cache_one_bit_mutation_rejected(suite, field):
    f = suite[0][0]
    cache = request_cache(f)
    key = (f.case.case_id, 4242027, 'chat_plan', 1, None)
    entry = cache.lookup(key)
    old = getattr(entry, field)
    new = old + b' ' if field == 'wire' else '0' * 64 if field == 'wire_sha256' else (*old[:3], 2, None)
    object.__setattr__(entry, field, new)
    with pytest.raises(ValueError):
        cache.lookup(key)


def test_legal_witness_external_P_plan_is_cached_once_for_two_samples(suite, tmp_path, monkeypatch):
    f = suite[0][0]
    values = [json.loads(raw) for raw in q.witnesses('chat_plan', f)]
    plan_value = dict(next(v for v in values if v['act'] == 'ANSWER'))
    plan_value['fact_ids'] = []  # Legal non-maximal shape, outside the budget witnesses.
    assert plan_value not in values
    plan = q.product.parse_and_validate_generation_v2_candidate_structure('chat_plan', q.wire(plan_value), f.catalog)
    cache = request_cache(f)
    builds, counts = [], []
    original = q.body
    def build(*args, **kwargs):
        builds.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(q, 'body', build)
    runtime = FakeRuntime({'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}],
                           'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})
    runtime.input_count = lambda body: (counts.append(body) or (1, 'a' * 64))
    for ordinal in (1, 2):
        _, result = r.attempt(runtime, f, 4242027, 'message', 100, ordinal, tmp_path, cache=cache, plan=plan)
        assert result['status'] == 'LENGTH'
    assert builds == ['message'] and len(counts) == 1 and runtime.calls == 2
    keys = [(f.case.case_id, 4242027, 'message', i, q.digest(plan_value)) for i in (1, 2)]
    bodies = [json.loads(cache.lookup(k).wire) for k in keys]
    assert bodies[0]['messages'] == bodies[1]['messages']
    assert bodies[0]['response_format'] == bodies[1]['response_format']
    assert bodies[0]['seed'] != bodies[1]['seed']


def test_P_context_miss_stops_before_any_generation(suite, tmp_path):
    f = suite[0][0]
    plan = q.product.parse_and_validate_generation_v2_candidate_structure('chat_plan', q.witnesses('chat_plan', f)[0], f.catalog)
    runtime = FakeRuntime(None)
    runtime.input_count = lambda body: (8192, 'a' * 64)
    _, result = r.attempt(runtime, f, 4242027, 'message', 100, 1, tmp_path, cache=request_cache(f), plan=plan)
    assert result['status'] == 'P_CONTEXT_PREFLIGHT_MISS'
    assert not runtime.owner.integrity and runtime.generation_calls == 0


def test_readiness_deadline_is_bounded_without_generation():
    now, calls = [0.0], []
    runtime = object.__new__(r.Runtime)
    runtime.owner = SimpleNamespace(integrity=True)
    runtime.request = lambda *a, **k: (calls.append(k) or False)
    def sleep(delay):
        now[0] += delay
    with pytest.raises(RuntimeError, match='LOAD_TIMEOUT'):
        runtime.wait_ready(.2, clock=lambda: now[0], sleep=sleep)
    assert len(calls) == 2 and all(c['readiness'] for c in calls)
    assert not runtime.owner.integrity


@pytest.mark.parametrize('actual_rows', [0, 1, 17, 96])
def test_actual_and_probe_summary_denominators_are_separate(actual_rows):
    probe_rows = [dict(terminal_stage='message', silence=True, sample_exhausted=True)] * 2
    actual = [dict(terminal_stage='message', silence=False, sample_exhausted=False)] * actual_rows
    attempts = [dict(stage='message', status='ACCEPTED', generation_sent=True)] * actual_rows
    value = r.actual_summary(attempts, actual)
    value.update(r.probe_summary([], probe_rows))
    assert value['rows'] + value['unrun'] == 96
    assert value['rows'] == actual_rows and value['silence'] == 0
    assert value['probe_rows'] == 2 and value['probe_silence'] == 2


@pytest.mark.parametrize('scenario,expected', [('probe_failure', 0), ('estimate_stop', 0),
    ('actual_start_failure', 0), ('cache_failure_after_claim', 0), ('partial_actual', 17), ('complete', 96)])
def test_run_never_reports_probe_rows_as_actual(suite, tmp_path, monkeypatch, scenario, expected):
    private = tmp_path / 'private'
    private.mkdir()
    monkeypatch.setattr(r, 'CANONICAL_CLAIM', tmp_path / 'canonical.claim')
    monkeypatch.setattr(r, 'load_profile', lambda p: {})
    reads = []
    def sources():
        reads.append(None)
        return {'v': 'changed' if scenario == 'cache_failure_after_claim' and len(reads) >= 3 else 'fixed'}
    monkeypatch.setattr(r, 'source_hashes', sources)
    monkeypatch.setattr(q, 'prepare', lambda: suite)
    monkeypatch.setattr(r, 'prepare_witnesses', lambda fixtures: {})
    monkeypatch.setattr(r, 'create_private_evidence_container', lambda *a, **k: private)
    monkeypatch.setattr(r, 'listener_owners', lambda port: ())
    monkeypatch.setattr(r.existing, 'cleanup_owned', lambda *a: {'owned_processes_remaining': 0})
    def launch(profile, private, label):
        if label == 'actual' and scenario == 'actual_start_failure':
            raise RuntimeError('ACTUAL_START')
        owner = SimpleNamespace(integrity=True, runtime_identity={'identity': 'fixed'}, full=lambda *a: None)
        runtime = SimpleNamespace(owner=owner, identity=lambda: owner.runtime_identity,
                                  generation_calls=0, utility_calls=0, close=lambda: None)
        return object(), owner, runtime
    monkeypatch.setattr(r, 'launch', launch)
    monkeypatch.setattr(r, 'measure', lambda *a, **k: ({stage: 100 for stage in q.STAGES}, []))
    monkeypatch.setattr(r, 'estimate', lambda *a: {'seconds': 4000 if scenario == 'estimate_stop' else 1000,
                                                  'proceed': scenario != 'estimate_stop'})
    def run_rows(rows, runtime, *args, probe, cache):
        assert isinstance(cache, r.RequestCache)
        n = 0 if probe and scenario == 'probe_failure' else 2 if probe else expected
        results = [dict(case_id=f.case.case_id, seed=seed, elapsed=1, terminal_stage=f.stage,
                        silence=False, sample_exhausted=False) for f, seed in list(rows)[:n]]
        attempts = [dict(stage=row['terminal_stage'], status='ACCEPTED', generation_sent=True) for row in results]
        runtime.generation_calls += n
        return attempts, results, n == (2 if probe else 96)
    monkeypatch.setattr(r, 'run_rows', run_rows)
    value = r.run(tmp_path / 'profile', tmp_path / 'public.json')
    assert value['rows'] == expected and value['unrun'] == 96 - expected
    assert value['actual_generation_calls'] == expected
    assert value['probe_rows'] == (0 if scenario == 'probe_failure' else 2)
    if scenario in ('actual_start_failure', 'cache_failure_after_claim'):
        assert (private / 'actual' / 'claim.json').exists()
        assert value['status'] == 'UNKNOWN'


def test_prepared_witnesses_are_exact_immutable_bytes(suite, monkeypatch):
    fixtures = tuple(next(f for f in suite[0] if f.stage == stage)
                     for stage in ('chat_plan', 'pre_vote', 'co_opportunity'))
    original, observed = q.witnesses, {}
    def record(stage, fixture):
        value = original(stage, fixture)
        observed[fixture.case.case_id, stage] = value
        return value
    monkeypatch.setattr(q, 'witnesses', record)
    prepared = r.prepare_witnesses(fixtures)
    assert len(prepared) == 4
    assert all(prepared[k] is value for k, value in observed.items())
    with pytest.raises(TypeError):
        prepared[('foreign', 'message')] = ()
    class FirstCount(Exception):
        pass
    class Counter:
        def count(self, raw):
            assert raw.encode() == prepared[fixtures[0].case.case_id, 'chat_plan'][0]
            raise FirstCount
    monkeypatch.setattr(q, 'witnesses', lambda *a: (_ for _ in ()).throw(AssertionError('CONNECTED_CPU_REBUILD')))
    with pytest.raises(FirstCount):
        r.measure(fixtures, (), Counter(), None, witness_sets=prepared)


@pytest.mark.parametrize('late_cpu,expected', [(True, 'RECONNECT_FORBIDDEN'), (False, 'COUNT_OK')])
def test_idle_close_negative_and_prepared_witness_control(suite, monkeypatch, late_cpu, expected):
    # The delayed source simulates the measured >1s product witness validation.
    # The HTTP idle timeout stays identical in both arms; reconnect stays forbidden.
    f = suite[0][0]
    values = {stage: q.witnesses(stage, f) for stage in ('chat_plan', 'message')}
    started = threading.Event()
    seen = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def setup(self):
            super().setup()
            self.connection.settimeout(.2)
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.reply({'tokens': [1]})
        def do_GET(self):
            self.reply({'status': 'ok'})
        def reply(self, value):
            seen.append(self.path)
            raw = q.wire(value)
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            started.set()
        def log_message(self, *args):
            pass
    def cpu(stage, fixture):
        if stage == 'chat_plan':
            r.time.sleep(.35)
        return values[stage]
    monkeypatch.setattr(q, 'witnesses', cpu)
    prepared = None if late_cpu else r.prepare_witnesses((f,))
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(r.existing, 'PORT', server.server_port)
    owner = SimpleNamespace(integrity=True, light=lambda: None, full=lambda *a: None)
    runtime = r.Runtime(owner)
    class CountFinished(Exception):
        pass
    original_count = runtime.count
    def count(raw):
        original_count(raw)
        raise CountFinished
    runtime.count = count
    try:
        runtime.wait_ready(r.time.monotonic() + 5)
        assert started.wait(1)
        if late_cpu:
            prepared = r.prepare_witnesses((f,))
        try:
            r.measure((f,), (), runtime, None, witness_sets=prepared)
        except CountFinished:
            status = 'COUNT_OK'
        except RuntimeError as error:
            status = str(error)
        assert status == expected
        assert runtime.generation_calls == 0 and runtime.recorder.connects == 1
        assert seen == (['/health'] if late_cpu else ['/health', '/tokenize'])
        assert owner.integrity is not late_cpu
    finally:
        runtime.close()
        server.shutdown()
        server.server_close()
        thread.join()
