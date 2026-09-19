import asyncio
from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from scripts import phase6_model_comparison as tool
from scripts import phase6_kind_first_probe as probe
from scripts.phase6_conversation_suite import example
from tests.test_phase6_none_reason_probe import baseline


def source_body():
    return tool.body_for(tool.project(tool.cases()[0], 'baseline'), tool.PROFILES['qw9'][0])


def assert_delta(before, after, path=()):
    if isinstance(before, dict):
        target = (len(path) == 8 and path[:6] == ('response_format', 'json_schema', 'schema',
                  '$defs', 'speech_act', 'oneOf') and path[-1] == 'properties')
        assert list(after) == (['kind', *sorted(set(before) - {'kind'})] if target else list(before)), path
        for key in before:
            assert_delta(before[key], after[key], (*path, key))
    elif isinstance(before, list):
        assert len(before) == len(after)
        for i, (a, b) in enumerate(zip(before, after)):
            assert_delta(a, b, (*path, i))
    else:
        assert before == after


def test_all_32_only_branch_internal_order_changes():
    assert len(tool.cases()) == 32
    for case in tool.cases():
        p = tool.project(case, 'baseline')
        body = tool.body_for(p, tool.PROFILES['qw9'][0])
        before = deepcopy(body)
        old, new = tool.wire_bytes(body), probe.kind_first_wire_bytes(body)
        assert body == before == json.loads(new)
        assert old != new and len(old) == len(new)
        assert tool.wire_bytes(json.loads(new)) == old
        assert probe.kind_first_wire_bytes(body) == new
        assert_delta(json.loads(old), json.loads(new))
        schema = json.loads(new)['response_format']['json_schema']['schema']
        assert schema['$defs']['speech_act']['oneOf'][0]['properties'] == {'kind': {'const': 'NONE'}}
        assert list(schema['properties']['discussion']['oneOf'][0]['properties']).index('speech_act') == 10


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'extra', 'open', 'branch_order', 'unknown', 'empty', 'required_order'])
def test_shape_drift_rejected(mutation):
    body = source_body()
    branches = body['response_format']['json_schema']['schema']['$defs']['speech_act']['oneOf']
    b = branches[2]
    if mutation == 'missing': del b['properties']['kind']
    if mutation == 'duplicate': b['required'].append('kind')
    if mutation == 'extra': b['properties']['reason'] = {'type': 'string'}
    if mutation == 'open': b['additionalProperties'] = True
    if mutation == 'branch_order': branches.reverse()
    if mutation == 'unknown': b['properties']['kind']['const'] = 'UNKNOWN'
    if mutation == 'empty': branches.clear()
    if mutation == 'required_order': b['required'].reverse()
    with pytest.raises(ValueError, match='^SCHEMA_SHAPE_CHANGED$'):
        probe.kind_first_wire_bytes(body)


def ready_k1(tmp_path, monkeypatch, *, fail_http=False):
    from tests.test_phase6_model_comparison import fake_ready_run
    case = tool.cases()[0]
    p = tool.project(case, 'baseline')
    response = {'choices': [{'message': {'content': json.dumps(example(p.canonical_input))}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 50}}
    real_count_prompt = tool.count_prompt
    _, children = fake_ready_run(tmp_path, monkeypatch, response)
    monkeypatch.setattr(tool, 'source_identity', lambda *args: {'f': 'hash'})
    body = tool.body_for(p, tool.PROFILES['qw9'][0])
    payload = probe.kind_first_wire_bytes(body)
    plan = json.loads((tmp_path/'plan.json').read_text())
    plan['source'] = {'f': 'hash'}
    binding = {'artifacts': tool.BASELINE_FILES, 'runtime': tool.safe_runtime(tool.runtime()), 'directory': 'unused'}
    monkeypatch.setattr(tool, 'baseline_binding', lambda *a: binding)
    plan.update(experiment=tool.K1, experiment_version=1, task_id='T439', baseline=binding)
    plan['cases'][0].update(candidate_input_sha256=tool.digest(body), baseline_input_sha256=tool.digest(body),
        candidate_schema_sha256=tool.digest(body['response_format']['json_schema']['schema']),
        candidate_wire_sha256=hashlib.sha256(payload).hexdigest(), baseline_wire_sha256=tool.digest(body),
        wire_size_bytes=len(payload), serializer_version=1, target_property_order=probe.TARGET_ORDER)
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    seen, generation = [], []
    def handler(request):
        if request.url.path in ('/apply-template', '/v1/chat/completions'):
            seen.append((request.url.path, request.content))
        if request.url.path == '/health': value = {'status': 'ok'}
        elif request.url.path == '/apply-template': value = {'prompt': 'rendered'}
        elif request.url.path == '/tokenize': value = {'tokens': [1] * 100}
        else:
            generation.append(True)
            assert (tmp_path/'private'/(case.case_id+'.request.bin')).read_bytes() == request.content == payload
            consumed = json.loads((tmp_path/'qw9-results.json').read_text())['rows'][0]
            assert consumed['new_provider_calls'] == 1 and consumed['generation_status'] == 'STARTED'
            if fail_http: return httpx.Response(503, text='PRIVATE_RESPONSE')
            value = response
        return httpx.Response(200, json=value)
    monkeypatch.setattr(tool, 'request', lambda path, body=None, timeout=20, **kw: asyncio.run(
        tool._request(path, body, timeout, transport=httpx.MockTransport(handler), **kw)))
    monkeypatch.setattr(tool, 'count_prompt', real_count_prompt)
    return plan, payload, children, seen, generation


def test_saved_wire_template_transport_match_and_no_retry(tmp_path, monkeypatch):
    _, payload, children, seen, generation = ready_k1(tmp_path, monkeypatch)
    assert tool.run(tmp_path, 'qw9') == 0
    assert seen[0] == ('/apply-template', payload)
    assert seen[-1] == ('/v1/chat/completions', payload)
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    row = result['rows'][0]
    raw = json.loads((tmp_path/'private/raw.jsonl').read_text())
    assert row['candidate_wire_sha256'] == raw['candidate_wire_sha256'] == hashlib.sha256(payload).hexdigest()
    assert row['input_sha256'] == row['baseline_wire_sha256'] != row['candidate_wire_sha256']
    assert 'none_reason' not in row and row['structural_pass']
    assert result['source_unchanged'] and all(p.poll() is not None for p in children)
    with pytest.raises(FileExistsError): tool.run(tmp_path, 'qw9')
    assert len(generation) == 1


@pytest.mark.parametrize('change,expected', [('wire','FROZEN_WIRE_CHANGED'), ('order','FROZEN_WIRE_CHANGED'),
    ('source','FROZEN_SOURCE_CHANGED'), ('input','FROZEN_INPUT_CHANGED'), ('file','FROZEN_FILE_CHANGED'),
    ('profile','EXPERIMENT_INVALID'), ('experiment','EXPERIMENT_INVALID')])
def test_prelaunch_changes_reject_without_generation(tmp_path, monkeypatch, change, expected):
    plan, _, children, _, generation = ready_k1(tmp_path, monkeypatch)
    if change == 'wire': plan['cases'][0]['candidate_wire_sha256'] = plan['cases'][0]['baseline_wire_sha256']
    if change == 'order': plan['cases'][0]['target_property_order'] = {}
    if change == 'source': monkeypatch.setattr(tool, 'source_identity', lambda *args: {'f': 'changed'})
    if change == 'input': plan['cases'][0]['common_input_sha256'] = 'changed'
    if change == 'file': monkeypatch.setattr(tool, 'file_identity', lambda path: {'sha256': 'changed'})
    if change == 'experiment': plan['experiment'] = 'unknown'
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    with pytest.raises(tool.StopComparison, match=f'^{expected}$'):
        tool.run(tmp_path, 'll8' if change == 'profile' else 'qw9')
    assert not children and not generation and not (tmp_path/'qw9.claim').exists()


@pytest.mark.parametrize('failure', ['save', 'http', 'runtime'])
def test_owned_cleanup_and_consumption_on_failures(tmp_path, monkeypatch, failure):
    from pathlib import Path
    _, _, children, _, generation = ready_k1(tmp_path, monkeypatch, fail_http=failure == 'http')
    if failure == 'save':
        original = Path.open
        def open_file(path, *a, **kw):
            if path.name.endswith('.request.bin'): raise PermissionError('PRIVATE_WIRE_PATH')
            return original(path, *a, **kw)
        monkeypatch.setattr(Path, 'open', open_file)
    if failure == 'runtime':
        runtime = tool.runtime(); runtime['template_sha256'] = 'changed'
        monkeypatch.setattr(tool, 'runtime', lambda: runtime)
    assert tool.run(tmp_path, 'qw9') == 2
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    assert result['stop_reason'] == {'save':'PermissionError','http':'HTTP_503','runtime':'BASELINE_RUNTIME_MISMATCH'}[failure]
    assert 'PRIVATE' not in json.dumps(result) and all(p.poll() is not None for p in children)
    assert len(generation) == (1 if failure == 'http' else 0)
    if failure == 'http': assert result['rows'][0]['generation_status'] == 'ERROR'
    with pytest.raises(FileExistsError): tool.run(tmp_path, 'qw9')


def test_prepare_and_experiment_scoped_sources(tmp_path, monkeypatch):
    old = tool.source_identity()
    current = tool.source_identity(tool.K1)
    assert set(current) - set(old) == set(tool.K1_SOURCES)
    assert tool.EXPERIMENT_SOURCE_DELTA[tool.K1] == tool.EXPERIMENT_SOURCE_DELTA[tool.C1] | set(tool.K1_SOURCES)
    assert len(tool.EXPERIMENT_SOURCE_DELTA[tool.C1]) == len(tool.EXPERIMENT_SOURCE_DELTA[tool.C2]) == 5
    assert len(tool.EXPERIMENT_SOURCE_DELTA[tool.K1]) == 7
    monkeypatch.setattr(tool, 'file_identity', lambda path: {'path': str(path), 'sha256': 'a'})
    monkeypatch.setattr(tool, 'baseline_binding', lambda manifest, source: {'reviewed': True})
    tool.prepare(tmp_path, experiment=tool.K1, baseline_source=tmp_path/'baseline')
    plan = json.loads((tmp_path/'plan.json').read_text())
    assert plan['source'] == current and plan['task_id'] == 'T439' and set(plan['profiles']) == {'qw9'}
    for row in plan['cases']:
        assert row['candidate_input_sha256'] == row['baseline_input_sha256'] == row['baseline_wire_sha256']
        assert row['candidate_wire_sha256'] != row['baseline_wire_sha256']
        assert row['target_property_order'] == probe.TARGET_ORDER


@pytest.mark.parametrize('path', tool.K1_SOURCES)
def test_new_helper_test_bytes_are_frozen(monkeypatch, path):
    original = tool.file_hash
    before = tool.source_identity(tool.K1)
    monkeypatch.setattr(tool, 'file_hash', lambda p: 'changed' if p == tool.ROOT/path else original(p))
    assert tool.source_identity(tool.K1)[path] != before[path]
    assert path not in tool.source_identity()


@pytest.mark.parametrize('mutation', [None, 'artifact', 'unknown_source', 'unevaluated', 'missing', 'input'])
def test_k1_baseline_binding_and_quality_are_pinned(baseline, mutation):
    current, files, path, write = baseline
    current['experiment'] = tool.K1
    current['source'].update({name: 'new' for name in tool.K1_SOURCES})
    annotation = files['qw9-annotations.json']
    annotation.update(evaluated=32, unevaluated=0)
    for row in annotation['rows']:
        row.update(hard_pass=True, reason_codes=[])
    annotation['rows'][0].update(hard_pass=False, reason_codes=['STATE_CONTRADICTION', 'ACT_TEXT_MISMATCH'])
    if mutation == 'unknown_source': current['source']['unapproved.py'] = 'new'
    if mutation == 'unevaluated': annotation['unevaluated'] = 1
    if mutation == 'missing': annotation['rows'].pop()
    if mutation == 'input': current['cases'][0]['baseline_input_sha256'] = 'wrong'
    write()
    if mutation == 'artifact': (path/'qw9-annotations.json').write_text('{}')
    if mutation:
        with pytest.raises(tool.StopComparison, match='^BASELINE_MISMATCH$'):
            tool.baseline_binding(current, path)
    else:
        binding = tool.baseline_binding(current, path)
        assert binding['source_delta_paths'] == sorted(tool.EXPERIMENT_SOURCE_DELTA[tool.K1])
        assert binding['baseline_quality'] == dict(hard_fail=1, act_text_mismatch=1,
            fabricated_evidence=0, secret_disclosure=0, state_contradiction=1,
            ability_contradiction=0, unevaluated=0)
