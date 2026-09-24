import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from scripts import phase6_recovery_runner as r
from tests.test_phase6_intent_choice_probe import projection
from tests.test_phase6_quality_grounding import payload, request_with


@pytest.fixture
def fixture():
    p = projection()
    case = SimpleNamespace(case_id='G01-1', request=request_with())
    return case, p, json.dumps(payload(p, text='I need public voting reasons before deciding.'))


def dispatcher(outputs, calls):
    def send(stage, body):
        calls.append((stage, copy.deepcopy(body)))
        return next(outputs), dict(status='GENERATED', wire_sha256=r.sha(r.wire_bytes(body)),
                                  consumed=True, raw_sha256='0'*64)
    return send


CHOICE = json.dumps({'speech_act_kind': 'NONE', 'authoritative_fact_ids': []})


@pytest.mark.parametrize('arm,count', [('baseline', 1), ('gb1', 2), ('filter', 2)])
def test_calls_and_early_stop(fixture, arm, count):
    case, p, raw = fixture
    calls = []
    output = iter([raw] if arm == 'baseline' else [CHOICE, raw])
    row, final = r.process_case(case, p, 'qw9', arm, r.SEEDS[0], dispatcher(output, calls))
    assert row['status'] == 'ACCEPTED'
    assert row['structural_pass'] is True and row['accepted_attempt'] == 0
    assert row['provider_calls'] == row['new_provider_calls'] == len(calls) == count
    assert final == raw


def test_filter_invalid_then_success_retains_both_and_seed(fixture):
    case, p, raw = fixture
    calls = []
    row, final = r.process_case(case, p, 'qw9', 'filter', r.SEEDS[1],
        dispatcher(iter([CHOICE, '{}', raw]), calls))
    assert len(calls) == 3 and row['accepted_attempt'] == 1
    assert calls[-1][1]['seed'] == r.SEEDS[1]+1009
    assert row['attempts'][1]['reject_code'] == 'STRUCTURAL_INVALID'
    assert row['attempts'][2]['filter_pass'] is True
    assert final == raw


def test_filter_exhaustion_does_not_accept_last_value(fixture):
    case, p, _ = fixture
    calls = []
    row, final = r.process_case(case, p, 'qw9', 'filter', r.SEEDS[0],
        dispatcher(iter([CHOICE, '{}', '{}', '{}']), calls))
    assert row['status'] == 'FILTER_EXHAUSTED'
    assert row['structural_pass'] is False and row['accepted_attempt'] is None
    assert row['provider_calls'] == 4
    assert [b['seed'] for _, b in calls] == [r.SEEDS[0], r.SEEDS[0], r.SEEDS[0]+1009, r.SEEDS[0]+2018]
    assert final == '{}'


@pytest.mark.parametrize('rejected_count', [1, 2])
def test_filter_error_after_readable_rejection_never_retains_acceptance(fixture, rejected_count):
    _, p, _ = fixture
    case = r.base.cases()[22]
    raw = json.dumps(payload(p, text=case.request.history.records[-1].message))
    calls = []
    row, final = r.process_case(case, p, 'qw9', 'filter', r.SEEDS[0],
        dispatcher(iter([CHOICE, *([raw]*rejected_count), None]), calls))
    assert row['status'] == 'OUTPUT_ERROR'
    assert row['structural_pass'] is False and row['filter_pass'] is False
    assert row['accepted_attempt'] is None
    assert row['provider_calls'] == len(calls) == rejected_count+2
    assert row['attempts'][1]['structural_pass'] is True
    assert row['attempts'][1]['reject_code'] == 'EXACT_TEXT_GUARD'
    assert final == raw and row['final_output_sha256'] == r.sha(raw.encode('utf-8'))


@pytest.mark.parametrize('raw', [None, '{}', 'null', '{"speech_act_kind":"NONE","authoritative_fact_ids":["f999"]}'])
def test_choice_failure_never_resampled(fixture, raw):
    case, p, _ = fixture
    calls = []
    row, final = r.process_case(case, p, 'qw9', 'filter', r.SEEDS[0], dispatcher(iter([raw]), calls))
    assert len(calls) == 1 and final is None
    assert row['status'] in ('CHOICE_ERROR', 'CHOICE_INVALID')


def test_no_legal_grounding_never_calls_output(fixture, monkeypatch):
    case, p, _ = fixture
    def unavailable(*args):
        raise r.gb.gc2.NoLegalGrounding()
    monkeypatch.setattr(r.gb, 'output_body', unavailable)
    calls = []
    row, _ = r.process_case(case, p, 'qw9', 'filter', r.SEEDS[0], dispatcher(iter([CHOICE]), calls))
    assert len(calls) == 1 and row['status'] == 'NO_LEGAL_GROUNDING'


def test_replay_first_attempt_zero_new_calls_and_exact_binding(fixture):
    case, p, raw = fixture
    body = r.baseline_body(p, 'qw9', r.SEEDS[0])
    choice = r.gb.validate_choice(CHOICE, p)
    replay = {stage: (text, dict(status='GENERATED', consumed=True,
               wire_sha256=r.sha(r.wire_bytes(request)))) for stage, text, request in (
        ('choice', CHOICE, r.gb.choice_body(body, p)), ('output0', raw, r.gb.output_body(body, choice, p)))}
    def forbidden(*args):
        pytest.fail('replay must not send provider request')
    row, final = r.process_case(case, p, 'qw9', 'filter', r.SEEDS[0], forbidden, replay=replay)
    assert row['new_provider_calls'] == 0 and row['provider_calls'] == 2 and final == raw
    replay['output0'][1]['wire_sha256'] = '0'*64
    with pytest.raises(r.Stop, match='REPLAY_BINDING'):
        r.process_case(case, p, 'qw9', 'filter', r.SEEDS[0], forbidden, replay=replay)


def test_replay_invalid_first_only_sends_changed_seed(fixture):
    case, p, raw = fixture
    body = r.baseline_body(p, 'qw9', r.SEEDS[0])
    choice = r.gb.validate_choice(CHOICE, p)
    replay = {stage: (text, dict(status='GENERATED', consumed=True,
               wire_sha256=r.sha(r.wire_bytes(request)))) for stage, text, request in (
        ('choice', CHOICE, r.gb.choice_body(body, p)), ('output0', '{}', r.gb.output_body(body, choice, p)))}
    calls = []
    row, final = r.process_case(case, p, 'qw9', 'filter', r.SEEDS[0], dispatcher(iter([raw]), calls), replay=replay)
    assert len(calls) == row['new_provider_calls'] == 1
    assert row['provider_calls'] == 3 and calls[0][0] == 'output1' and final == raw


def test_existing_long_copy_guard_not_semantic_classifier(fixture):
    _, p, _ = fixture
    peer = r.base.cases()[22]
    case = SimpleNamespace(case_id=peer.case_id, request=peer.request)
    text = peer.request.history.records[-1].message
    result = r.mechanical(case, p, json.dumps(payload(p, text=text)))
    assert result == dict(structural_pass=True, filter_pass=False, reject_code='EXACT_TEXT_GUARD')
    for text in ('I agree.', 'I am the werewolf.', 'Player-5 is alive.'):
        # This layer does not infer semantic secrets, lies or game truth from text.
        assert r.mechanical(case, p, json.dumps(payload(p, text=text)))['filter_pass']


@pytest.mark.parametrize('raw', ['{}', '{"decision":NaN}', '{"x":Infinity}', '{"x":1,"x":2}'])
def test_strict_validation_never_repairs(fixture, raw):
    case, p, _ = fixture
    assert r.mechanical(case, p, raw)['structural_pass'] is False


def test_durable_reservation_duplicate_and_cap(tmp_path, monkeypatch):
    (tmp_path/'calls').mkdir()
    r.reserve(tmp_path, 'first', {'seed': 1})
    assert len(list((tmp_path/'calls').iterdir())) == 1
    with pytest.raises(r.Stop, match='DUPLICATE_DISPATCH'):
        r.reserve(tmp_path, 'second', {'seed': 1})
    monkeypatch.setattr(r, 'MAX_CALLS', 1)
    with pytest.raises(r.Stop, match='CALL_CAP'):
        r.reserve(tmp_path, 'third', {'seed': 2})


def test_lease_rejects_concurrent_block_and_cleans_only_own_lock(tmp_path):
    with r.lease(tmp_path):
        with pytest.raises(FileExistsError):
            with r.lease(tmp_path):
                pytest.fail('concurrent block entered')
        assert (tmp_path/'active-block.lock').exists()
    assert not (tmp_path/'active-block.lock').exists()


def test_dispatch_marker_precedes_http_and_transport_error_is_consumed(fixture, tmp_path, monkeypatch):
    _, p, raw = fixture
    (tmp_path/'calls').mkdir()
    private = tmp_path/'private'; private.mkdir()
    monkeypatch.setattr(r.base, 'count_prompt', lambda *a, **k: {'prompt_tokens_actual': 3})
    def send(*args, **kwargs):
        assert len(list((tmp_path/'calls').glob('*.json'))) == 1
        assert (private/'case.output0.consumed.json').exists()
        raise httpx.ReadTimeout('PRIVATE_PAYLOAD_MUST_NOT_ESCAPE')
    monkeypatch.setattr(r.base, 'request', send)
    text, meta = r.execute_call(tmp_path, private, 'case', 'output0', r.baseline_body(p, 'qw9', r.SEEDS[0]),
        remaining=lambda: 61, verify_now=lambda: None)
    assert text is None and meta['status'] == 'TRANSPORT_ERROR' and meta['consumed']
    assert 'PRIVATE_PAYLOAD' not in json.dumps(meta)


def test_deadline_and_preflight_failure_never_dispatch(fixture, tmp_path, monkeypatch):
    _, p, _ = fixture
    body = r.baseline_body(p, 'qw9', r.SEEDS[0])
    (tmp_path/'calls').mkdir()
    def forbidden(*args, **kwargs):
        pytest.fail('must not perform HTTP')
    monkeypatch.setattr(r.base, 'request', forbidden)
    with pytest.raises(r.Stop, match='DEADLINE'):
        r.execute_call(tmp_path, tmp_path, 'case', 'output0', body, remaining=lambda: 59, verify_now=lambda: None)
    def changed():
        raise r.Stop('FROZEN_SOURCE_CHANGED')
    with pytest.raises(r.Stop, match='FROZEN_SOURCE_CHANGED'):
        r.execute_call(tmp_path, tmp_path, 'case', 'output0', body, remaining=lambda: 90, verify_now=changed)
    assert not list((tmp_path/'calls').iterdir())


def test_nonowned_listener_never_launches_or_kills(tmp_path, monkeypatch):
    monkeypatch.setattr(r, 'verify', lambda *a, **k: {'deadline_utc': '2099-01-01T00:00:00+00:00'})
    monkeypatch.setattr(r.base, 'port_free', lambda: False)
    monkeypatch.setattr(r.subprocess, 'Popen', lambda *a, **k: pytest.fail('non-owned process touched'))
    with pytest.raises(r.Stop, match='NON_OWNED_LISTENER'):
        r.run_block(tmp_path, 'qw9', 'baseline', r.SEEDS[0])


def test_models_share_contract_with_explicit_profile_differences(fixture):
    _, p, _ = fixture
    a, b = [r.baseline_body(p, m, r.SEEDS[0]) for m in ('qw9', 'gm12')]
    a.pop('model'); b.pop('model')
    assert a == b
    assert r.base.launch_args('qw9')[-1] == '99'
    assert r.base.launch_args('gm12')[-1] == '24'


@pytest.mark.parametrize('transport_error', [False, True])
def test_complete_owned_lifecycle_with_immutable_projection(fixture, tmp_path, monkeypatch, transport_error):
    case, p, raw = fixture
    model = r.base.PROFILES['qw9'][0]
    profile = {'argv': [model], 'model': {'path': model}}
    plan = {'deadline_utc': '2099-01-01T00:00:00+00:00', 'profiles': {'qw9': profile}}
    r.write(tmp_path/'plan.json', plan)
    (tmp_path/'calls').mkdir()
    private = tmp_path/'private'; private.mkdir()
    monkeypatch.setattr(r, 'verify', lambda *a, **k: plan)
    monkeypatch.setattr(r, 'entries', lambda: [(case, p)])
    monkeypatch.setattr(r.base, 'cases', lambda: [case])
    monkeypatch.setattr(r.base, 'port_free', lambda: True)
    monkeypatch.setattr(r.base, 'create_private_evidence_container', lambda *a, **k: private)
    processes = []
    class Process:
        pid = 100
        returncode = None
        def poll(self): return self.returncode
        def terminate(self): self.returncode = 0
        def kill(self): self.returncode = 2
        def wait(self, **kw): return self.returncode
    def launch(*args, **kwargs):
        proc = Process(); processes.append(proc); return proc
    monkeypatch.setattr(r.subprocess, 'Popen', launch)
    monkeypatch.setattr(r.base, 'owned_listener', lambda proc: proc in processes)
    monkeypatch.setattr(r.base, 'runtime', lambda: {'model_path': model})
    monkeypatch.setattr(r.base, 'safe_runtime', lambda x: {'runtime_test': True})
    monkeypatch.setattr(r.base, 'attach_performance', lambda *args: None)
    monkeypatch.setattr(r.base, 'count_prompt', lambda *a, **k: {'prompt_tokens_actual': 3})
    def request(path, *args, **kwargs):
        if path == '/health': return {'status': 'ok'}
        if transport_error: raise httpx.ReadTimeout('private')
        return {'choices': [{'message': {'content': raw}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 3, 'completion_tokens': 10}}
    monkeypatch.setattr(r.base, 'request', request)
    assert r.run_block(tmp_path, 'qw9', 'baseline', r.SEEDS[0]) == 0
    result = r.read(tmp_path/r.block_name('qw9', 'baseline', r.SEEDS[0])/'result.json')
    assert result['status'] == 'COMPLETE' and result['integrity'] is True
    assert result['rows'][0]['status'] == ('OUTPUT_ERROR' if transport_error else 'ACCEPTED')
    assert result['durable_call_count'] == result['provider_calls'] == 1
    assert len(processes) == 2 and all(proc.poll() == 0 for proc in processes)
    assert result['owned_processes_remaining'] == 0
    assert r.read(private/(case.case_id+'.projection.json'))['capture']
    assert r.base._RUN_DEADLINE is None
