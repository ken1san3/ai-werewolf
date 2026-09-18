from copy import deepcopy
import hashlib
import json

import pytest

from scripts import phase6_model_comparison as tool
from scripts import phase6_none_reason_probe as probe
from scripts.phase6_conversation_suite import example, canonical_json_bytes


@pytest.fixture
def sample():
    case = tool.cases()[0]
    p = tool.project(case, 'baseline')
    body = probe.candidate_body(tool.body_for(p, tool.PROFILES['qw9'][0]))
    value = example(p.canonical_input)
    value['discussion']['speech_act'] = {'kind': 'NONE', 'reason': 'INDEPENDENT_STATEMENT'}
    return case, p, body, value


def test_all_32_bodies_change_only_none_reason_and_preserve_order():
    assert len(tool.cases()) == 32
    for case in tool.cases():
        p = tool.project(case, 'baseline')
        original = tool.body_for(p, tool.PROFILES['qw9'][0])
        original_wire = tool.wire_bytes(original)
        candidate = probe.candidate_body(original)
        branches = candidate['response_format']['json_schema']['schema']['$defs']['speech_act']['oneOf']
        none = next(b for b in branches if b['properties']['kind'].get('const') == 'NONE')
        assert none['required'] == ['kind', 'reason']
        assert list(none['properties']) == ['kind', 'reason']
        del none['properties']['reason']
        none['required'].remove('reason')
        assert tool.wire_bytes(candidate) == original_wire
        assert tool.wire_bytes(original) == original_wire
        assert tool.wire_bytes(tool.experiment_body(p, tool.PROFILES['qw9'][0], 'baseline')) == original_wire


@pytest.mark.parametrize('reason', probe.REASONS)
@pytest.mark.parametrize('silent', [False, True])
def test_legal_none_reason_adapts_only_after_strict_validation(sample, reason, silent):
    case, p, body, value = sample
    value['discussion']['speech_act']['reason'] = reason
    if silent:
        value['decision'] = {'kind': 'none'}
        value['discussion'].update(decision_kind='none', option_id=None)
    raw = json.dumps(value)
    with pytest.raises(tool.DecisionValidationError):
        tool.parse_llm_output(raw, projection=p)
    before = deepcopy((body, value)), canonical_json_bytes(p.canonical_input)
    row = probe.assess_candidate(case, p, body, raw)
    assert row['candidate_schema_pass'] and row['legacy_contract_pass'] and row['structural_pass']
    assert row['adapter_status'] == 'NONE_REASON_REMOVED' and row['none_reason'] == reason
    expected = deepcopy(value)
    del expected['discussion']['speech_act']['reason']
    assert row['adapted_output_sha256'] == hashlib.sha256(tool.wire_bytes(expected)).hexdigest()
    assert row['semantic_pass'] is None and row['style_pass'] is None
    assert ((body, value), canonical_json_bytes(p.canonical_input)) == before


@pytest.mark.parametrize('mutation', ['missing', 'unknown', 'null', 'number', 'extra', 'elsewhere',
                                     'non_none_reason', 'bad_json', 'duplicate', 'nan', 'overflow'])
def test_invalid_candidate_never_reaches_legacy_parser(sample, mutation, monkeypatch):
    case, p, body, value = sample
    act = value['discussion']['speech_act']
    if mutation == 'missing': del act['reason']
    if mutation == 'unknown': act['reason'] = 'PRIVATE_ARBITRARY_TEXT'
    if mutation == 'null': act['reason'] = None
    if mutation == 'number': act['reason'] = 42
    if mutation == 'extra': act['secret'] = 'PRIVATE_ARBITRARY_TEXT'
    if mutation == 'elsewhere': value['reason'] = act.pop('reason')
    if mutation == 'non_none_reason':
        value = example(p.canonical_input)
        value['discussion']['speech_act']['reason'] = probe.REASONS[0]
    raw = json.dumps(value)
    if mutation == 'bad_json': raw = '{'
    if mutation == 'duplicate': raw = raw.replace('"kind": "NONE"', '"kind": "NONE", "kind": "NONE"')
    if mutation == 'nan': raw = raw.replace('"base_revision": 0', '"base_revision": NaN')
    if mutation == 'overflow': raw = raw.replace('"base_revision": 0', '"base_revision": 1e999')
    def forbidden(*_):
        pytest.fail('invalid candidate passed to old parser')
    monkeypatch.setattr(tool, 'screen', forbidden)
    row = probe.assess_candidate(case, p, body, raw)
    assert row['candidate_schema_pass'] is False and row['legacy_contract_pass'] is None
    assert row['adapter_status'] == 'NOT_APPLIED' and row['adapted_output_sha256'] is None
    assert 'PRIVATE' not in json.dumps(row)


def test_non_none_preserved_and_semantic_contract_not_weakened(sample):
    case, p, body, _ = sample
    value = example(p.canonical_input)
    row = probe.assess_candidate(case, p, body, json.dumps(value))
    assert row['structural_pass'] and row['adapter_status'] == 'UNCHANGED_NON_NONE'
    assert row['adapted_output_sha256'] == hashlib.sha256(tool.wire_bytes(value)).hexdigest()
    # Each branch is schema-valid, but the paired decision kinds disagree.
    value['decision'] = {'kind': 'none'}
    row = probe.assess_candidate(case, p, body, json.dumps(value))
    assert row['candidate_schema_pass'] and row['legacy_contract_pass'] is False
    assert row['structural_pass'] is False


@pytest.mark.parametrize('mutation', ['duplicate_branch', 'changed_none', 'remote_ref'])
def test_schema_drift_fails_closed(sample, mutation):
    _, p, _, _ = sample
    body = tool.body_for(p, tool.PROFILES['qw9'][0])
    schema = body['response_format']['json_schema']['schema']
    branches = schema['$defs']['speech_act']['oneOf']
    if mutation == 'duplicate_branch': branches.append(deepcopy(branches[0]))
    if mutation == 'changed_none': branches[0]['additionalProperties'] = True
    if mutation == 'remote_ref': schema['$defs']['speech_act']['oneOf'].append({'$ref': 'https://example.invalid/'})
    with pytest.raises(ValueError, match='^SCHEMA_SHAPE_CHANGED$'):
        probe.candidate_body(body)


@pytest.fixture
def baseline(tmp_path, monkeypatch):
    settings = dict(sampling=tool.SAMPLING, context=8192, request_seconds=60, model_seconds=1200,
                    load_seconds=180, max_generations_per_model=32, retry=0, repair=0)
    old = dict(settings, source={'stable.py': 'a', 'scripts/phase6_model_comparison.py': 'old'},
               profiles={'qw9': {'identity': 'model-runtime-argv'}, 'll8': {}}, cases=[])
    current = dict(settings, source={'stable.py': 'a', 'scripts/phase6_model_comparison.py': 'new',
                                    'scripts/phase6_none_reason_probe.py': 'new'},
                   profiles={'qw9': old['profiles']['qw9']}, cases=[])
    result = dict(status='COMPLETE', source_unchanged=True, owned_processes_remaining=0, rows=[], runtime={})
    annotation = {'rows': []}
    for i in range(32):
        cid = f'case-{i}'
        old['cases'].append(dict(case_id=cid, common_input_sha256=f'common-{i}'))
        current['cases'].append(dict(case_id=cid, baseline_common_input_sha256=f'common-{i}', baseline_input_sha256=f'input-{i}'))
        row = dict(case_id=cid, common_input_sha256=f'common-{i}', input_sha256=f'input-{i}',
                   final_output_sha256=f'output-{i}', generation_status='GENERATED')
        result['rows'].append(row)
        annotation['rows'].append(deepcopy(row))
    files = dict(zip(tool.BASELINE_FILES, (old, result, annotation)))
    def write():
        (tmp_path/'plan.json').write_text(json.dumps(old))
        result['plan_sha256'] = tool.file_hash(tmp_path/'plan.json')
        for name, content in files.items():
            (tmp_path/name).write_text(json.dumps(content))
        monkeypatch.setattr(tool, 'BASELINE_FILES', {n: tool.file_hash(tmp_path/n) for n in files})
    write()
    return current, files, tmp_path, write


def test_baseline_binding_uses_pinned_artifacts_without_private_raw(baseline):
    current, _, path, _ = baseline
    binding = tool.baseline_binding(current, path)
    assert binding['artifacts'] == tool.BASELINE_FILES
    (path/'qw9-results.json').write_text('{}')
    with pytest.raises(tool.StopComparison, match='^BASELINE_MISMATCH$'):
        tool.baseline_binding(current, path)


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'output_hash', 'input_hash', 'common_hash',
                                     'source', 'model_runtime', 'sampling', 'stopped', 'cleanup'])
def test_baseline_mismatch_stops_before_network(baseline, mutation):
    current, files, path, write = baseline
    result, annotation = files['qw9-results.json'], files['qw9-annotations.json']
    if mutation == 'missing': annotation['rows'].pop()
    if mutation == 'duplicate': annotation['rows'][1] = deepcopy(annotation['rows'][0])
    if mutation == 'output_hash': annotation['rows'][0]['final_output_sha256'] = 'different'
    if mutation == 'input_hash': annotation['rows'][0]['input_sha256'] = 'different'
    if mutation == 'common_hash': current['cases'][0]['baseline_common_input_sha256'] = 'different'
    if mutation == 'source': current['source']['stable.py'] = 'different'
    if mutation == 'model_runtime': current['profiles'] = {'qw9': {'identity': 'different'}}
    if mutation == 'sampling': current['sampling'] = {'temperature': 1}
    if mutation == 'stopped': result['status'] = 'STOPPED'
    if mutation == 'cleanup': result['owned_processes_remaining'] = 1
    write()
    with pytest.raises(tool.StopComparison, match='^BASELINE_MISMATCH$'):
        tool.baseline_binding(current, path)


@pytest.mark.parametrize('experiment,key', [('unknown', 'qw9'), (tool.C2, 'll8')])
def test_wrong_experiment_or_model_rejected_before_process(tmp_path, experiment, key, monkeypatch):
    (tmp_path/'plan.json').write_text(json.dumps({'experiment': experiment}))
    monkeypatch.setattr(tool.subprocess, 'Popen', lambda *a, **kw: pytest.fail('launched'))
    with pytest.raises(tool.StopComparison, match='^EXPERIMENT_INVALID$'):
        tool.run(tmp_path, key)


def test_prepare_c2_freezes_only_qw9_and_full_candidate_inputs(tmp_path, monkeypatch):
    models = []
    def identity(path):
        models.append(str(path))
        return {'path': str(path), 'sha256': 'a'}
    monkeypatch.setattr(tool, 'file_identity', identity)
    monkeypatch.setattr(tool, 'source_identity', lambda: {'helper': 'hash'})
    monkeypatch.setattr(tool, 'baseline_binding', lambda manifest, source: {'reviewed': True})
    tool.prepare(tmp_path, experiment=tool.C2, baseline_source=tmp_path/'baseline')
    plan = json.loads((tmp_path/'plan.json').read_text())
    assert set(plan['profiles']) == {'qw9'} and plan['task_id'] == 'T433'
    assert len(plan['cases']) == 32
    assert all(c['candidate_input_sha256'] != c['baseline_input_sha256'] for c in plan['cases'])
    assert [m for m in models if m.endswith('.gguf')] == [tool.PROFILES['qw9'][0]]


def test_c2_mock_run_keeps_raw_and_separates_adapter_results(tmp_path, monkeypatch, sample):
    from tests.test_phase6_model_comparison import fake_ready_run
    case, p, body, value = sample
    raw = json.dumps(value)
    calls, children = fake_ready_run(tmp_path, monkeypatch, {
        'choices': [{'message': {'content': raw}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 50}})
    plan = json.loads((tmp_path/'plan.json').read_text())
    binding = {'artifacts': tool.BASELINE_FILES, 'runtime': tool.safe_runtime(tool.runtime()), 'directory': 'unused'}
    monkeypatch.setattr(tool, 'baseline_binding', lambda *a: binding)
    plan.update(experiment=tool.C2, experiment_version=1, task_id='T433', baseline=binding)
    plan['cases'][0].update(common_input_sha256=tool.common_hash(body), candidate_input_sha256=tool.digest(body),
                           candidate_schema_sha256=tool.digest(body['response_format']['json_schema']['schema']),
                           baseline_input_sha256=tool.digest(tool.body_for(p, tool.PROFILES['qw9'][0])))
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    assert tool.run(tmp_path, 'qw9') == 0
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    row = result['rows'][0]
    assert row['candidate_schema_pass'] and row['legacy_contract_pass']
    assert row['none_reason'] == 'INDEPENDENT_STATEMENT'
    original = json.loads((tmp_path/'private/raw.jsonl').read_text())
    assert original['final_content'] == raw and original['input'] == body
    assert row['final_output_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
    assert len(calls) == 1 and result['owned_processes_remaining'] == 0
    assert all(child.poll() is not None for child in children)
    with pytest.raises(FileExistsError):
        tool.run(tmp_path, 'qw9')
