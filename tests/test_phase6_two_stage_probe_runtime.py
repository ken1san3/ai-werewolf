"""Frozen P2 mock observations and shared two-stage ownership regressions."""
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import phase6_two_call_runner as runner
from scripts import phase6_two_stage_probe_runtime as runtime
from tests.test_phase6_two_call_runner import ready_run


GOLDEN = Path(__file__).parent/'fixtures/phase6_p2_runtime_golden.json'
SCENARIOS = ('chat', 'co_declare', 'none', 'vote', 'ability', 'bad_plan',
             'bad_message', 'load_exit', 'transport', 'cleanup')
VOLATILE = {'started_at_utc', 'ended_at_utc', 'latency_real_sec', 'real_duration_sec'}


def normalize(value):
    if isinstance(value, dict):
        return {key: normalize(child) for key, child in value.items() if key not in VOLATILE}
    if isinstance(value, list):
        return [normalize(child) for child in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(normalize(value), sort_keys=True,
        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def observation(monkeypatch, path, scenario):
    kind = scenario if scenario in SCENARIOS[:5] else 'chat'
    calls, children, payloads, private = ready_run(monkeypatch, path, kind=kind,
        plan_failure=scenario == 'bad_plan', text_failure=scenario == 'bad_message')
    if scenario == 'load_exit':
        old = runner.subprocess.Popen
        def launch(*args, **kwargs):
            child = old(*args, **kwargs)
            child.returncode = 3
            return child
        monkeypatch.setattr(runner.subprocess, 'Popen', launch)
    elif scenario == 'transport':
        old = runner.base.request
        def request(endpoint, *args, **kwargs):
            if endpoint == '/v1/chat/completions':
                raise TimeoutError()
            return old(endpoint, *args, **kwargs)
        monkeypatch.setattr(runner.base, 'request', request)
    elif scenario == 'cleanup':
        old = runner.base.cleanup_owned
        def cleanup(*args):
            value = old(*args)
            value['provider_cleanup_error'] = 'CLEANUP_FAILED'
            return value
        monkeypatch.setattr(runner.base, 'cleanup_owned', cleanup)
    code = runner.run(path)
    result = json.loads((path/'qw9-results.json').read_text())
    files = {}
    for file in sorted(private.glob('*')):
        if file.suffix == '.bin':
            files[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
        elif file.suffix == '.json':
            files[file.name] = digest(json.loads(file.read_text()))
        elif file.suffix == '.jsonl':
            files[file.name] = digest([json.loads(line) for line in file.read_text().splitlines()])
    return {'exit': code, 'result_sha256': digest(result), 'private_sha256': files,
            'transport_sha256': digest([(stage, payload.hex()) for stage, payload in payloads]),
            'calls': len(calls), 'child_exits': [child.poll() for child in children],
            'status': result['status'], 'row_status': result['rows'][0]['status'],
            'stop_reason': result.get('stop_reason')}


@pytest.mark.parametrize('scenario', SCENARIOS)
def test_p2_extraction_preserves_frozen_mock_observations(monkeypatch, tmp_path, scenario):
    golden = json.loads(GOLDEN.read_text())
    assert golden['source_sha256'] == 'b92a70de01f1432c771369c1e929ca7b1cf197e2f4b56321b6a17167a0cd9312'
    assert observation(monkeypatch, tmp_path, scenario) == golden['scenarios'][scenario]


@pytest.mark.parametrize('contract,name,remaining,allowed', [
    (runtime.P2_CONTRACT, 'plan', 120, False),
    (runtime.P2_CONTRACT, 'message', 120, False),
    (runtime.P2_CONTRACT, 'plan', 120.001, True),
    (runtime.P2_CONTRACT, 'message', 120.001, True),
    (runtime.IC2_CONTRACT, 'choice', 240, True),
    (runtime.IC2_CONTRACT, 'choice', 239.999, False),
    (runtime.IC2_CONTRACT, 'output', 120, True),
    (runtime.IC2_CONTRACT, 'output', 119.999, False)])
def test_exact_real_reserve_policy_before_artifacts(monkeypatch, tmp_path, contract, name, remaining, allowed):
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: 100.0)
    monkeypatch.setattr(runtime.base, '_RUN_DEADLINE', 100.0+remaining)
    first, second = [stage.name for stage in contract.stages]
    spec = next(stage for stage in contract.stages if stage.name == name)
    row = {'case_id':'G01-1', first+'_provider_calls':0, second+'_provider_calls':0,
           'new_provider_calls':0, 'completion_tokens':0, 'provider_prompt_tokens':0}
    if name == second:
        row[first+'_provider_calls'] = 1
        row[first] = {'status':'GENERATED'}
    body = {'max_tokens':spec.max_tokens, 'messages':[],
            'response_format':{'json_schema':{'schema':{}}}}
    calls = []
    monkeypatch.setattr(runtime.base, 'count_prompt', lambda *a, **k: {'prompt_tokens_actual':100})
    def request(*args, **kwargs):
        calls.append(1)
        return {'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':100,'completion_tokens':2}}
    monkeypatch.setattr(runtime.base, 'request', request)
    budget = {'calls':0}
    with (tmp_path/'raw.jsonl').open('w') as raw:
        if allowed:
            assert runtime.stage(name,body,row,tmp_path,raw,lambda:None,budget,contract=contract) == '{}'
            assert budget['calls'] == row[name+'_provider_calls'] == 1
        else:
            with pytest.raises(runtime.base.StopComparison, match='MODEL_TIME_BUDGET'):
                runtime.stage(name,body,row,tmp_path,raw,lambda:None,budget,contract=contract)
            assert calls == [] and budget['calls'] == row[name+'_provider_calls'] == 0
            assert not list(tmp_path.glob('*.request.bin')) and not list(tmp_path.glob('*.consumed.json'))


@pytest.mark.parametrize('change', ['stage_name','duplicate','tokens','reserve','policy_int','policy',
    'calls','request','load','model','cleanup','outer','profile','config','tokenizer','stage_list'])
def test_contract_invalid_before_any_claim_or_launch(monkeypatch, tmp_path, change):
    original = runtime.IC2_CONTRACT
    first, second = original.stages
    changes = {
        'stage_name': {'stages':(replace(first,name='plan'),second)},
        'duplicate': {'stages':(first,first)},
        'tokens': {'stages':(replace(first,max_tokens=33),second)},
        'reserve': {'stages':(replace(first,reserve_seconds=0),second)},
        'policy_int': {'stages':(replace(first,reject_when_remaining_equal=0),second)},
        'policy': {'stages':(replace(first,reject_when_remaining_equal=True),second)},
        'calls': {'max_provider_calls':65}, 'request': {'request_seconds':61},
        'load': {'load_seconds':181}, 'model': {'model_seconds':1201},
        'cleanup': {'cleanup_seconds':26}, 'outer': {'outer_seconds':1321},
        'profile': {'profile':'ll8'}, 'config': {'config_sha256':'changed'},
        'tokenizer': {'tokenizer_sha256':'changed'}, 'stage_list': {'stages':[first,second]}}
    contract = replace(original, **changes[change])
    monkeypatch.setattr(runtime.base,'claim_run',lambda *a:pytest.fail('no claim'))
    monkeypatch.setattr(runtime.subprocess,'Popen',lambda *a,**k:pytest.fail('no process'))
    with pytest.raises(runtime.base.StopComparison,match='STAGE_CONTRACT'):
        runtime.run_two_stage_probe(tmp_path,contract=contract,callbacks=None)


def test_output_cannot_dispatch_without_generated_choice(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.base, '_RUN_DEADLINE', runtime.time.monotonic()+1200)
    row = {'choice_provider_calls':0,'output_provider_calls':0}
    with pytest.raises(runtime.base.StopComparison,match='STAGE_ORDER'):
        runtime.stage('output',{'max_tokens':480},row,tmp_path,None,lambda:None,
                      {'calls':0},contract=runtime.IC2_CONTRACT)
    assert not list(tmp_path.iterdir())


def test_private_container_failure_still_saves_denominator_and_cleanup(monkeypatch, tmp_path):
    _, children, _, _ = ready_run(monkeypatch, tmp_path)
    def private(*args, **kwargs):
        raise PermissionError('private path detail')
    monkeypatch.setattr(runtime.base, 'create_private_evidence_container', private)
    assert runner.run(tmp_path) == 2
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    assert len(result['rows']) == 1 and result['new_provider_calls'] == 0
    assert result['owned_processes_remaining'] == 0 and children == []
    assert result['error_kind'] == 'PermissionError'
    assert 'private path' not in json.dumps(result)


def test_case_callbacks_only_use_dispatch_capability():
    import ast
    import inspect
    from scripts import phase6_intent_choice_runner as ic2
    forbidden = {'subprocess','private','rawfile','save','budget','Popen','request','claim_run','cleanup_owned','write_json'}
    for callback in (runner.process_case,ic2.process_case):
        tree = ast.parse(inspect.getsource(callback))
        names = {node.id for node in ast.walk(tree) if isinstance(node,ast.Name)}
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node,ast.Attribute)}
        assert not (names|attributes)&forbidden
    context = runtime.CaseContext(lambda *args: args)
    assert [name for name in dir(context) if not name.startswith('_')] == ['dispatch']
