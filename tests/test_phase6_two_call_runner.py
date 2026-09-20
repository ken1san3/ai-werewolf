"""Offline P2 dispatch, immutable binding, accounting and public evidence tests."""
import copy
import io
import json
import time
from types import SimpleNamespace

import pytest
import httpx
import asyncio

from scripts import phase6_two_call_runner as runner


def row():
    return dict(case_id='G01-1', status='PLAN_NOT_STARTED', plan_provider_calls=0,
                message_provider_calls=0, new_provider_calls=0, completion_tokens=0,
                provider_prompt_tokens=0)


def stage_setup(monkeypatch, tmp_path, *, finish='stop', reasoning=None, usage=None):
    monkeypatch.setattr(runner.base, '_RUN_DEADLINE', time.monotonic()+1200)
    body = dict(max_tokens=384, messages=[{'role': 'user', 'content': 'private prompt'}],
                response_format={'json_schema': {'schema': {'type': 'object'}}})
    record, budget, saved, seen = row(), {'calls': 0}, [], []
    raw = io.StringIO()
    # StringIO has no durable fd; the real runner uses a private filesystem stream.
    raw = (tmp_path/'raw.jsonl').open('w+', encoding='utf-8')
    def save():
        saved.append(copy.deepcopy(record))
    def count(body, **options):
        seen.append(('count', options['wire_payload']))
        return {'prompt_tokens_actual': 123, 'remaining_context_tokens': 7684}
    def request(path, body, **options):
        assert saved[-1]['plan_provider_calls'] == 1
        assert saved[-1]['status'] == 'PLAN_STARTED'
        seen.append(('generate', options['wire_payload']))
        return {'choices': [{'message': {'content': '{"private":"output"}', 'reasoning_content': reasoning},
                             'finish_reason': finish}],
                'usage': {'prompt_tokens': 123, 'completion_tokens': 10} if usage is None else usage}
    monkeypatch.setattr(runner.base, 'count_prompt', count)
    monkeypatch.setattr(runner.base, 'request', request)
    return body, record, budget, raw, save, saved, seen


def test_stage_wire_durable_consumption_and_safe_row(monkeypatch, tmp_path):
    body, record, budget, raw, save, saved, seen = stage_setup(monkeypatch, tmp_path)
    try:
        output = runner.stage('plan', body, record, tmp_path, raw, save, budget)
        assert output == '{"private":"output"}'
        assert seen[0][1] == seen[1][1] == (tmp_path/'G01-1.plan.request.bin').read_bytes()
        assert record['completion_tokens'] == 10 and budget['calls'] == 1
        assert 'private' not in json.dumps(record)
        assert record['plan']['status'] == 'GENERATED'
        consumed = json.loads((tmp_path/'G01-1.plan.consumed.json').read_text())
        assert consumed['status'] == 'STARTED' and consumed['wire_sha256'] == record['plan']['wire_sha256']
    finally:
        raw.close()


@pytest.mark.parametrize('reasoning', ['secret reasoning', {}, [], 0, False, ['secret']])
def test_any_nonempty_or_nonstr_reasoning_rejected_without_saving(monkeypatch, tmp_path, reasoning):
    body, record, budget, raw, save, _, _ = stage_setup(monkeypatch, tmp_path, reasoning=reasoning)
    try:
        with pytest.raises(runner.base.StopComparison, match='THINKING_NOT_DISABLED'):
            runner.stage('plan', body, record, tmp_path, raw, save, budget)
        raw.seek(0)
        assert raw.read() == ''
        assert 'secret' not in json.dumps(record)
        assert budget['calls'] == 1
    finally:
        raw.close()


@pytest.mark.parametrize('finish', ['length', 'content_filter', 'tool_calls', 'unexpected'])
def test_nonstop_is_case_failure_without_retry(monkeypatch, tmp_path, finish):
    body, record, budget, raw, save, _, _ = stage_setup(monkeypatch, tmp_path, finish=finish)
    try:
        assert runner.stage('plan', body, record, tmp_path, raw, save, budget) is None
        assert budget['calls'] == 1
    finally:
        raw.close()


@pytest.mark.parametrize('usage,code', [({'prompt_tokens': 124, 'completion_tokens': 10}, 'TOKEN_COUNT_MISMATCH'),
    ({'prompt_tokens': True, 'completion_tokens': 10}, 'TOKEN_COUNT_MISMATCH'),
    ({'prompt_tokens': 123, 'completion_tokens': 385}, 'COMPLETION_BUDGET'),
    ({'prompt_tokens': 123, 'completion_tokens': -1}, 'COMPLETION_BUDGET'),
    ({'prompt_tokens': 123, 'completion_tokens': True}, 'COMPLETION_BUDGET')])
def test_usage_contract(monkeypatch, tmp_path, usage, code):
    body, record, budget, raw, save, _, _ = stage_setup(monkeypatch, tmp_path, usage=usage)
    try:
        with pytest.raises(runner.base.StopComparison, match=code):
            runner.stage('plan', body, record, tmp_path, raw, save, budget)
    finally:
        raw.close()


@pytest.mark.parametrize('failure', ['time', 'count', 'used', 'save', 'request'])
def test_dispatch_failure_consumption(monkeypatch, tmp_path, failure):
    body, record, budget, raw, save, _, seen = stage_setup(monkeypatch, tmp_path)
    if failure == 'time':
        monkeypatch.setattr(runner.base, '_RUN_DEADLINE', time.monotonic()+119)
    elif failure == 'count':
        budget['calls'] = 64
    elif failure == 'used':
        record['plan_provider_calls'] = 1
    elif failure == 'save':
        def save():
            raise OSError('private detail')
    else:
        monkeypatch.setattr(runner.base, 'request', lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    try:
        with pytest.raises((runner.base.StopComparison, OSError, TimeoutError)):
            runner.stage('plan', body, record, tmp_path, raw, save, budget)
        assert not any(name == 'generate' for name, _ in seen)
        assert budget['calls'] == (64 if failure == 'count' else 1 if failure in ('save', 'request') else 0)
    finally:
        raw.close()


@pytest.mark.parametrize('part', ['case', 'projection', 'wire', 'raw', 'plan'])
def test_locked_message_rejects_cross_case_and_changed_plan(monkeypatch, part):
    c, p = SimpleNamespace(case_id='G01-1'), SimpleNamespace(prompt_sha256='a'*64)
    body, value, raw = {'body': 1}, {'decision': {'kind': 'chat'}}, '{"raw":1}'
    binding = runner.plan_binding(c, p, body, raw, value)
    if part == 'case': c.case_id = 'G02-1'
    if part == 'projection': p.prompt_sha256 = 'b'*64
    if part == 'wire': body['body'] = 2
    if part == 'raw': raw += ' '
    if part == 'plan': value['decision']['kind'] = 'none'
    monkeypatch.setattr(runner.probe, 'message_body', lambda *a: pytest.fail('no message body'))
    with pytest.raises(runner.base.StopComparison, match='PLAN_BINDING_MISMATCH'):
        runner.locked_message(c, p, {}, body, raw, value, binding)


@pytest.mark.parametrize('plan_outcome', ['invalid', 'length'])
def test_invalid_plan_never_dispatches_message(monkeypatch, tmp_path, plan_outcome):
    calls = []
    monkeypatch.setattr(runner.base, 'body_for', lambda *a: {})
    monkeypatch.setattr(runner.probe, 'plan_body', lambda *a: {})
    def stage(name, *args):
        calls.append(name)
        return None if plan_outcome == 'length' else '{}'
    monkeypatch.setattr(runner, 'stage', stage)
    monkeypatch.setattr(runner.probe, 'validate_plan', lambda *a: (_ for _ in ()).throw(ValueError('invalid')))
    record = row()
    runner.run_case(SimpleNamespace(case_id='G01-1'), None, record, tmp_path, None, lambda: None, {'calls': 0})
    assert calls == ['plan']
    assert record['status'] == ('PLAN_ERROR' if plan_outcome == 'length' else 'PLAN_INVALID')


def test_p2_source_boundary():
    assert not any('single_shape' in p or 'intent_first' in p for p in runner.EXTRA)
    assert runner.probe.PLAN_TOKENS + runner.probe.MESSAGE_TOKENS == 512
    assert runner.base.EXPERIMENT_SOURCE_DELTA[runner.P2] == runner.base.EXPERIMENT_SOURCE_DELTA[runner.base.K1] | frozenset(runner.EXTRA)


def ready_run(monkeypatch, tmp_path, *, kind='chat', plan_failure=False, text_failure=False):
    from tests.test_phase6_two_call_probe import projection, legacy
    p = projection('chat' if kind == 'none' else kind)
    original = legacy(p, kind)
    plan_value, text = runner.probe.split_legacy(original, p.decision_schema)
    identity = {'build': 'b10697-093adb242', 'model_path': runner.base.PROFILES['qw9'][0],
                'template_sha256': 'a'*64, 'generation_settings': {}}
    profile = {'model': {'path': identity['model_path']}, 'argv': runner.base.launch_args('qw9')}
    case = SimpleNamespace(case_id='G01-1', category='fixed')
    freeze = {'profiles': {'qw9': profile}, 'source': {}, 'config_sha256': runner.CONFIG_SHA,
              'baseline': {'artifacts': runner.base.BASELINE_FILES, 'runtime': runner.base.safe_runtime(identity)}}
    (tmp_path/'plan.json').write_text(json.dumps(freeze), encoding='utf-8')
    monkeypatch.setattr(runner, 'verify', lambda plan: (profile, [(case, p)]))
    monkeypatch.setattr(runner, 'sources', lambda: {})
    original_hash = runner.base.file_hash
    monkeypatch.setattr(runner.base, 'file_hash', lambda path: runner.CONFIG_SHA if path == runner.CONFIG else original_hash(path))
    private = tmp_path/'private'
    private.mkdir()
    monkeypatch.setattr(runner.base, 'create_private_evidence_container', lambda *a, **k: private)
    monkeypatch.setattr(runner.base, 'runtime', lambda: identity)
    monkeypatch.setattr(runner.base, 'owned_listener', lambda proc: True)
    monkeypatch.setattr(runner.base, 'screen', lambda *a: {'structural_pass': True, 'speech_act': 'NONE'})
    children = []
    class Child:
        pid = 123
        returncode = None
        def poll(self): return self.returncode
        def terminate(self): self.returncode = 0
        def kill(self): self.returncode = -1
        def wait(self, **kw): return self.returncode
    def launch(*args, **kw):
        children.append(Child())
        return children[-1]
    monkeypatch.setattr(runner.subprocess, 'Popen', launch)
    calls, payloads = [], []
    def handler(request):
        path = request.url.path
        if path == '/health': return httpx.Response(200, json={'status': 'ok'})
        if path == '/apply-template':
            payloads.append(('template', request.content))
            return httpx.Response(200, json={'prompt': 'fixed rendered prompt'})
        if path == '/tokenize': return httpx.Response(200, json={'tokens': [1]*100})
        assert path == '/v1/chat/completions'
        calls.append(path)
        payloads.append(('generation', request.content))
        durable = json.loads((tmp_path/'qw9-results.json').read_text())
        assert durable['new_provider_calls'] == len(calls)
        content = json.dumps(plan_value if len(calls) == 1 else text)
        if (len(calls) == 1 and plan_failure) or (len(calls) == 2 and text_failure):
            content = '{}'
        return httpx.Response(200, json={'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50}})
    transport = httpx.MockTransport(handler)
    def request(path, body=None, timeout=20, **kw):
        return asyncio.run(runner.base._request(path, body, timeout, transport=transport, **kw))
    monkeypatch.setattr(runner.base, 'request', request)
    return calls, children, payloads, private


@pytest.mark.parametrize('kind,count', [('chat', 2), ('co_declare', 2), ('none', 1), ('vote', 1), ('ability', 1)])
def test_full_runner_all_actions_one_decision_and_exact_call_count(monkeypatch, tmp_path, kind, count):
    calls, children, payloads, private = ready_run(monkeypatch, tmp_path, kind=kind)
    assert runner.run(tmp_path) == 0
    assert len(calls) == count
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    assert result['new_provider_calls'] == count
    assert result['owned_processes_remaining'] == 0 and all(c.poll() == 0 for c in children)
    assert result['rows'][0]['completion_tokens'] == count*50
    assert result['rows'][0]['status'] == ('COMPLETE' if count == 2 else 'COMPLETE_NO_MESSAGE')
    for stage_name in ('plan', 'message')[:count]:
        payload = (private/f'G01-1.{stage_name}.request.bin').read_bytes()
        assert ('template', payload) in payloads and ('generation', payload) in payloads
    with pytest.raises(runner.base.StopComparison, match='RESULT_EXISTS'):
        runner.run(tmp_path)
    assert len(calls) == count


@pytest.mark.parametrize('stage_name,count,status', [('plan', 1, 'PLAN_INVALID'), ('message', 2, 'MESSAGE_INVALID')])
def test_full_invalid_case_keeps_denominator_and_no_partial_final(monkeypatch, tmp_path, stage_name, count, status):
    calls, children, _, private = ready_run(monkeypatch, tmp_path, plan_failure=stage_name == 'plan', text_failure=stage_name == 'message')
    assert runner.run(tmp_path) == 0
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    assert len(result['rows']) == 1 and len(calls) == count
    assert result['rows'][0]['status'] == status
    assert 'final_output_sha256' not in result['rows'][0]
    assert all(json.loads(line)['stage'] != 'final' for line in (private/'raw.jsonl').read_text().splitlines())
    assert all(c.poll() == 0 for c in children)


@pytest.mark.parametrize('failure', ['claim', 'load', 'private_write', 'dispatch', 'cleanup'])
def test_full_failures_never_repeat_calls_and_preserve_cleanup(monkeypatch, tmp_path, failure):
    calls, children, _, private = ready_run(monkeypatch, tmp_path)
    if failure == 'claim':
        (tmp_path/'qw9.claim').write_text('already consumed')
        with pytest.raises(FileExistsError): runner.run(tmp_path)
        assert not children and not calls
        return
    if failure == 'load':
        old = runner.subprocess.Popen
        def launch(*a, **k):
            child = old(*a, **k); child.returncode = 3; return child
        monkeypatch.setattr(runner.subprocess, 'Popen', launch)
    elif failure == 'private_write':
        (private/'G01-1.plan.request.bin').write_bytes(b'existing')
    elif failure == 'dispatch':
        old = runner.base.request
        def request(path, *a, **k):
            if path == '/v1/chat/completions': raise TimeoutError()
            return old(path, *a, **k)
        monkeypatch.setattr(runner.base, 'request', request)
    else:
        old = runner.base.cleanup_owned
        def cleanup(*a):
            data = old(*a); data['provider_cleanup_error'] = 'CLEANUP_FAILED'; return data
        monkeypatch.setattr(runner.base, 'cleanup_owned', cleanup)
    exit_code = runner.run(tmp_path)
    assert exit_code == (0 if failure == 'dispatch' else 2)
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    assert len(result['rows']) == 1 and all(c.poll() is not None for c in children)
    assert result['new_provider_calls'] == (1 if failure == 'dispatch' else 2 if failure == 'cleanup' else 0)


@pytest.mark.parametrize('change', ['source', 'config', 'case', 'projection', 'wire', 'instruction', 'tokens', 'calls', 'port'])
def test_frozen_drift_fails_before_any_provider_or_claim(monkeypatch, change):
    projections = [(c, runner.base.project(c, 'baseline')) for c in runner.base.cases()]
    identity = {'path': 'fixture', 'sha256': 'a'}
    profile = {'model': identity, 'runtime_files': [], 'argv': runner.base.launch_args('qw9'), 'available': True}
    binding = {'directory': 'baseline'}
    plan = {**runner.fixed_contract(),
            'source': {'stable': 'a'}, 'config_sha256': runner.CONFIG_SHA,
            'baseline': binding, 'profiles': {'qw9': profile}, 'tokenizer': identity,
            'cases': [runner.entry(c, p) for c, p in projections]}
    monkeypatch.setattr(runner, 'sources', lambda: {'stable': 'a'})
    monkeypatch.setattr(runner.base, 'file_hash', lambda path: runner.CONFIG_SHA)
    monkeypatch.setattr(runner.base, 'file_identity', lambda path: identity)
    monkeypatch.setattr(runner.base, 'baseline_binding', lambda *a: binding)
    monkeypatch.setattr(runner.base, 'port_free', lambda: change != 'port')
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch'))
    if change == 'source': plan['source']['stable'] = 'b'
    if change == 'config': plan['config_sha256'] = 'b'
    if change == 'case': plan['cases'][0]['case_id'] = 'G99-1'
    if change == 'projection': plan['cases'][0]['projection_sha256'] = 'b'
    if change == 'wire': plan['cases'][0]['plan_wire_sha256'] = 'b'
    if change == 'instruction': plan['message_instruction_sha256'] = 'b'
    if change == 'tokens': plan['plan_tokens'] = 512
    if change == 'calls': plan['max_provider_calls'] = 65
    with pytest.raises(runner.base.StopComparison):
        runner.verify(plan)


def test_all_32_baseline_prefixes_and_fixed_stage_budget():
    for case in runner.base.cases():
        p = runner.base.project(case, 'baseline')
        baseline = runner.base.body_for(p, runner.base.PROFILES['qw9'][0])
        candidate = runner.probe.plan_body(baseline)
        assert runner.base.digest(candidate['messages'][:2]) == runner.base.digest(baseline['messages'])
        assert candidate['max_tokens'] == 384
        for key in baseline:
            if key not in ('messages', 'response_format', 'max_tokens'):
                assert candidate[key] == baseline[key]


def test_outer_dispatches_only_fixed_p2_runner(monkeypatch, tmp_path):
    from scripts import phase6_probe_outer as outer
    from tests.fixtures import phase6_evidence
    (tmp_path/'plan.json').write_text(json.dumps({'experiment': runner.P2, 'task_id': runner.TASK}))
    monkeypatch.setattr(outer.sys, 'argv', ['outer', '--output', str(tmp_path)])
    monkeypatch.setattr(phase6_evidence, 'create_private_evidence_container', lambda *a, **k: tmp_path)
    seen = []
    def supervise(command, *a, **kw):
        seen.append(command)
        return {'exit_code': 0, 'outer_timeout': False, 'ownership_complete': True, 'owned_alive_after': 0}
    monkeypatch.setattr(outer, 'supervise', supervise)
    assert outer.main() == 0
    assert len(seen) == 1
    assert seen[0] == [outer.sys.executable, str(outer.ROOT/'scripts/phase6_two_call_runner.py'),
                       '--output', str(tmp_path.resolve()), '--run', 'qw9']


@pytest.mark.parametrize('field', list(runner.fixed_contract()))
@pytest.mark.parametrize('mutation', ['missing', 'changed', 'wrong_type'])
def test_every_declared_fixed_condition_rejected_before_claim_or_provider(monkeypatch, tmp_path, field, mutation):
    plan = copy.deepcopy(runner.fixed_contract())
    if mutation == 'missing':
        del plan[field]
    elif mutation == 'changed':
        value = plan[field]
        if type(value) is int: plan[field] = value+1
        elif type(value) is str: plan[field] = value+'-changed'
        elif type(value) is list: plan[field] = value+['--changed']
        else: plan[field]['seed'] += 1
    else:
        plan[field] = False if plan[field] == 0 else None
    (tmp_path/'plan.json').write_text(json.dumps(plan), encoding='utf-8')
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('no provider'))
    monkeypatch.setattr(runner.base, 'claim_run', lambda *a: pytest.fail('no claim'))
    with pytest.raises(runner.base.StopComparison, match='PLAN_CONTRACT_CHANGED'):
        runner.run(tmp_path)
