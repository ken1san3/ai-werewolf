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
    for f in fs:
        for stage in ((f.stage, 'message') if f.stage == 'chat_plan' else (f.stage,)):
            raws = q.witnesses(stage, f)
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

    def request(self, *args, **kwargs):
        self.calls += 1
        return self.response

    def input_count(self, body):
        return 1, 'a' * 64

    def count(self, text):
        return 1


@pytest.mark.parametrize('finish,content,status', [('length', 'not JSON', 'LENGTH'),
    ('stop', 'not JSON', 'STRUCTURE_INVALID'), ('content_filter', '{}', 'MISSING_RESPONSE')])
def test_attempt_length_not_parsed_and_two_samples_exhaust(suite, tmp_path, finish, content, status):
    runtime = FakeRuntime({'choices': [{'finish_reason': finish, 'message': {'content': content}}],
                           'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})
    f = suite[0][0]
    attempts, rows, ok = r.run_rows([(f, 4242027)], runtime, {f.stage: 100}, tmp_path, probe=False)
    assert ok and runtime.calls == 2
    assert [a['status'] for a in attempts] == [status, status]
    assert rows[0]['silence'] and rows[0]['sample_exhausted']


def test_probe_stops_after_first_bad_attempt(suite, tmp_path):
    runtime = FakeRuntime({'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}],
                           'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})
    f = suite[0][0]
    attempts, rows, ok = r.run_rows([(f, 4242027)], runtime, {f.stage: 100}, tmp_path, probe=True)
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
    _, outcome = r.attempt(runtime, suite[0][0], 4242027, 'chat_plan', 100, 1, tmp_path, clock=lambda: now[0])
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
        r.measure(fixtures, [(f, 4242027) for f in fixtures], runtime, tmp_path)


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
    attempts, results, ok = r.run_rows([(f, 4242027)], runtime, {'chat_plan': 100, 'message': 100}, tmp_path, probe=False)
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
    monkeypatch.setattr(r, 'Runtime', lambda owner: SimpleNamespace(identity=identity, close=close))
    monkeypatch.setattr(r.existing, 'cleanup_owned', lambda monitor, p: events.append(('cleanup', p is process)))
    with pytest.raises(RuntimeError):
        r.launch({'argv': [str(tmp_path / 'fake.exe')]}, tmp_path, 'test')
    assert events == ['close', ('cleanup', True)]
