"""Offline IC2 binding, two-call accounting, freeze and failure transitions."""
import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from scripts import phase6_intent_choice_runner as runner
from scripts import phase6_two_stage_probe_runtime as runtime
from tests.test_phase6_intent_choice_probe import projection
from tests.test_phase6_two_call_probe import legacy


def ready(monkeypatch, path, *, kind='chat', rows=1, failure=None):
    p = projection('chat' if kind == 'none' else kind)
    value = legacy(p, kind)
    cases = [(SimpleNamespace(case_id=f'G{i:02d}-1', category='fixed'), p) for i in range(1, rows+1)]
    identity = {'build': 'b10697-093adb242', 'model_path': runner.base.PROFILES['qw9'][0],
                'template_sha256': 'a'*64, 'generation_settings': {}}
    profile = {'model': {'path': identity['model_path']}, 'argv': runner.base.launch_args('qw9')}
    plan = {'source': {}, 'config_sha256': runner.CONFIG_SHA,
            'baseline': {'artifacts': runner.base.BASELINE_FILES, 'runtime': runner.base.safe_runtime(identity)}}
    (path/'plan.json').write_text(json.dumps(plan))
    monkeypatch.setattr(runner, 'verify', lambda plan: (profile, cases))
    monkeypatch.setattr(runner, 'sources', lambda: {})
    old_hash = runner.base.file_hash
    monkeypatch.setattr(runner.base, 'file_hash', lambda path: runner.CONFIG_SHA if path == runner.CONFIG else old_hash(path))
    private = path/'private'
    private.mkdir()
    monkeypatch.setattr(runner.base, 'create_private_evidence_container', lambda *a, **k: private)
    monkeypatch.setattr(runner.base, 'runtime', lambda: identity)
    monkeypatch.setattr(runner.base, 'owned_listener', lambda proc: True)
    monkeypatch.setattr(runner.base, 'screen', lambda *a: {'structural_pass': True, 'speech_act': 'NONE'})
    children, calls, payloads = [], [], []
    class Child:
        pid = 321
        returncode = None
        def poll(self): return self.returncode
        def terminate(self): self.returncode = 0
        def kill(self): self.returncode = -1
        def wait(self, **kwargs): return self.returncode
    def launch(*args, **kwargs):
        child = Child()
        if failure == 'load': child.returncode = 3
        children.append(child)
        return child
    monkeypatch.setattr(runtime.subprocess, 'Popen', launch)
    def handle(request):
        endpoint = request.url.path
        if endpoint == '/health': return httpx.Response(200, json={'status': 'ok'})
        if endpoint == '/apply-template':
            payloads.append(('template', request.content))
            return httpx.Response(200, json={'prompt': 'rendered fixture'})
        if endpoint == '/tokenize': return httpx.Response(200, json={'tokens': [1]*100})
        assert endpoint == '/v1/chat/completions'
        body = json.loads(request.content)
        stage = 'choice' if body['max_tokens'] == 32 else 'output'
        calls.append(stage)
        payloads.append(('generation', request.content))
        snapshot = json.loads((path/'qw9-results.json').read_text())
        assert snapshot['new_provider_calls'] == len(calls)
        assert len(snapshot['rows']) == rows
        if failure == 'timeout': raise httpx.ReadTimeout('private detail')
        text = json.dumps({'speech_act_kind': 'NONE'} if stage == 'choice' else value)
        if failure == stage: text = '{}'
        if failure == 'null_discussion' and stage == 'output': text = '{"discussion":null}'
        if failure == 'kind' and stage == 'output':
            wrong = copy.deepcopy(value); wrong['discussion']['speech_act']['kind'] = 'ANSWER'
            text = json.dumps(wrong)
        if failure == 'reserve' and stage == 'choice':
            monkeypatch.setattr(runner.base, '_RUN_DEADLINE', runtime.time.monotonic()+119)
        return httpx.Response(200, json={'choices': [{'message': {'content': text},
            'finish_reason': 'length' if failure == stage+'_length' else 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 12 if stage == 'choice' else 200}})
    transport = httpx.MockTransport(handle)
    def request(endpoint, body=None, timeout=20, **kwargs):
        return asyncio.run(runner.base._request(endpoint, body, timeout, transport=transport, **kwargs))
    monkeypatch.setattr(runner.base, 'request', request)
    return calls, children, payloads, private


@pytest.mark.parametrize('kind', ['chat', 'none', 'vote', 'ability', 'co_declare'])
def test_every_action_keeps_two_calls_and_full_raw(monkeypatch, tmp_path, kind):
    calls, children, payloads, private = ready(monkeypatch, tmp_path, kind=kind)
    assert runner.run(tmp_path) == 0
    assert calls == ['choice', 'output']
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    row = result['rows'][0]
    assert row['completion_tokens'] == 212
    assert all(row[key] for key in ('choice_schema_pass', 'candidate_schema_pass', 'kind_match_pass', 'legacy_validator_pass'))
    raw = [json.loads(line) for line in (private/'raw.jsonl').read_text().splitlines()]
    assert raw[-1]['final_content'] == raw[-2]['final_content']
    assert raw[-1]['binding'] == row['binding']
    for stage in calls:
        wire = (private/f'G01-1.{stage}.request.bin').read_bytes()
        assert ('template', wire) in payloads and ('generation', wire) in payloads
    assert result['owned_processes_remaining'] == 0 and all(child.poll() == 0 for child in children)
    with pytest.raises(runner.base.StopComparison, match='RESULT_EXISTS'):
        runner.run(tmp_path)


@pytest.mark.parametrize('failure,count,status', [('choice',1,'CHOICE_INVALID'),
    ('choice_length',1,'CHOICE_ERROR'), ('output',2,'OUTPUT_INVALID'),
    ('output_length',2,'OUTPUT_ERROR'), ('kind',2,'OUTPUT_INVALID'),
    ('null_discussion',2,'OUTPUT_INVALID'), ('timeout',1,'CHOICE_ERROR')])
def test_invalid_keeps_denominator_without_repair_or_final(monkeypatch, tmp_path, failure, count, status):
    calls, children, _, private = ready(monkeypatch, tmp_path, failure=failure)
    assert runner.run(tmp_path) == 0
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    assert len(calls) == count and len(result['rows']) == 1
    assert result['rows'][0]['status'] == status
    assert 'final_output_sha256' not in result['rows'][0]
    assert all(json.loads(line)['stage'] != 'final' for line in (private/'raw.jsonl').read_text().splitlines())
    assert 'private detail' not in json.dumps(result)
    assert all(child.poll() == 0 for child in children)


def test_output_reserve_stops_before_any_output_consumption_with_all_32_rows(monkeypatch, tmp_path):
    calls, children, _, private = ready(monkeypatch, tmp_path, rows=32, failure='reserve')
    assert runner.run(tmp_path) == 2
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    assert result['stop_reason'] == 'MODEL_TIME_BUDGET'
    assert len(result['rows']) == 32 and calls == ['choice']
    first = result['rows'][0]
    assert first['status'] == 'OUTPUT_NOT_STARTED' and first['error_kind'] == 'MODEL_TIME_BUDGET'
    assert first['output_provider_calls'] == 0
    assert not list(private.glob('*.output.*'))
    assert sum(row['new_provider_calls'] for row in result['rows']) == 1
    assert all(child.poll() == 0 for child in children)


def test_32_valid_choices_never_skip_second_call(monkeypatch, tmp_path):
    calls, _, _, _ = ready(monkeypatch, tmp_path, kind='none', rows=32)
    assert runner.run(tmp_path) == 0
    assert calls == ['choice', 'output']*32
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    assert result['new_provider_calls'] == 64


@pytest.mark.parametrize('field', ['case_id', 'projection_sha256', 'baseline_input_sha256',
    'baseline_messages_sha256', 'choice_wire_sha256', 'choice_raw_sha256',
    'canonical_choice_sha256', 'selected_kind', 'branch_sha256', 'schema_sha256',
    'input_sha256', 'messages_sha256', 'wire_sha256'])
def test_every_output_binding_component_must_match(field):
    p = projection(); case = SimpleNamespace(case_id='G01-1')
    baseline = runner.base.body_for(p, runner.base.PROFILES['qw9'][0])
    body = runner.probe.choice_body(baseline)
    choice = {'speech_act_kind': 'NONE'}; raw = json.dumps(choice)
    binding = runner.choice_binding(case, p, baseline, body, raw, choice)
    binding[field] += '-changed'
    with pytest.raises(runner.base.StopComparison, match='CHOICE_BINDING_MISMATCH'):
        runner.locked_output(case, p, baseline, body, raw, choice, binding)


@pytest.mark.parametrize('field', list(runner.fixed_contract()))
@pytest.mark.parametrize('mutation', ['missing', 'changed', 'wrong_type'])
def test_declared_fixed_condition_rejects_before_claim(monkeypatch, tmp_path, field, mutation):
    plan = copy.deepcopy(runner.fixed_contract())
    if mutation == 'missing': del plan[field]
    elif mutation == 'wrong_type': plan[field] = None
    else:
        value = plan[field]
        if type(value) is int: plan[field] = value+1
        elif type(value) is str: plan[field] = value+'-changed'
        elif type(value) is list: plan[field] = value+['changed']
        else: plan[field]['unexpected'] = True
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    monkeypatch.setattr(runner.base, 'claim_run', lambda *a: pytest.fail('no claim'))
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *a, **k: pytest.fail('no provider'))
    with pytest.raises(runner.base.StopComparison, match='PLAN_CONTRACT_CHANGED'):
        runner.run(tmp_path)


def test_ic2_source_and_import_boundary():
    import ast
    source = (runner.ROOT/'scripts/phase6_intent_choice_runner.py').read_text()
    imports = [ast.unparse(node) for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any('two_call_' in name or 'single_shape' in name or 'intent_first' in name for name in imports)
    assert not any('two_call_probe' in path or 'single_shape' in path or 'intent_first' in path for path in runner.EXTRA)


@pytest.mark.parametrize('change', ['source','config','case','projection','choice_wire','locked_schema','port'])
def test_frozen_source_and_input_drift_stops_before_claim(monkeypatch, tmp_path, change):
    projections = [(case,runner.base.project(case,'baseline')) for case in runner.base.cases()]
    identity = {'path':'fixture','sha256':'a'}
    profile = {'model':identity,'runtime_files':[], 'argv':runner.base.launch_args('qw9'),'available':True}
    binding = {'directory':'baseline'}
    plan = {**runner.fixed_contract(), 'source':{'stable':'a'}, 'baseline':binding,
            'profiles':{'qw9':profile}, 'tokenizer':identity,
            'cases':[runner.entry(case,p) for case,p in projections]}
    monkeypatch.setattr(runner,'sources',lambda:{'stable':'a'})
    monkeypatch.setattr(runner.base,'file_hash',lambda path:runner.CONFIG_SHA)
    monkeypatch.setattr(runner.base,'file_identity',lambda path:identity)
    monkeypatch.setattr(runner.base,'baseline_binding',lambda *a:binding)
    monkeypatch.setattr(runner.base,'port_free',lambda:change!='port')
    monkeypatch.setattr(runner.base,'claim_run',lambda *a:pytest.fail('no claim'))
    monkeypatch.setattr(runtime.subprocess,'Popen',lambda *a,**k:pytest.fail('no provider'))
    if change=='source': plan['source']['stable']='b'
    if change=='config': plan['config_sha256']='b'
    if change=='case': plan['cases'][0]['case_id']='G99-1'
    if change=='projection': plan['cases'][0]['projection_sha256']='b'
    if change=='choice_wire': plan['cases'][0]['choice_wire_sha256']='b'
    if change=='locked_schema': plan['cases'][0]['locked_outputs']['ANSWER']['schema_sha256']='b'
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    with pytest.raises(runner.base.StopComparison):
        runner.run(tmp_path)


def test_outer_fixed_ic2_route(monkeypatch, tmp_path):
    from scripts import phase6_probe_outer as outer
    from tests.fixtures import phase6_evidence
    (tmp_path/'plan.json').write_text(json.dumps({'experiment': runner.IC2, 'task_id': runner.TASK}))
    monkeypatch.setattr(outer.sys, 'argv', ['outer', '--output', str(tmp_path)])
    monkeypatch.setattr(phase6_evidence, 'create_private_evidence_container', lambda *a, **k: tmp_path)
    commands = []
    def supervise(command, *args, **kwargs):
        commands.append(command)
        return {'exit_code':0, 'outer_timeout':False, 'ownership_complete':True, 'owned_alive_after':0}
    monkeypatch.setattr(outer, 'supervise', supervise)
    assert outer.main() == 0
    assert commands == [[outer.sys.executable, str(outer.ROOT/'scripts/phase6_intent_choice_runner.py'),
                         '--output', str(tmp_path.resolve()), '--run', 'qw9']]
