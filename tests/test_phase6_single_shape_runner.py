import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from scripts import phase6_single_shape_probe as probe
from scripts import phase6_model_comparison as tool
from scripts.phase6_conversation_suite import example
from tests.test_phase6_none_reason_probe import baseline


def ready_shape(tmp_path, monkeypatch, *, fail_http=False):
    from tests.test_phase6_model_comparison import fake_ready_run
    case = tool.cases()[0]
    projection = tool.project(case, 'baseline')
    value = probe.legacy_to_candidate(example(projection.canonical_input), projection.decision_schema)
    response = {'choices': [{'message': {'content': json.dumps(value)}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 50}}
    real_count = tool.count_prompt
    _, children = fake_ready_run(tmp_path, monkeypatch, response)
    monkeypatch.setattr(tool, 'source_identity', lambda *a: {'f': 'hash'})
    baseline_body = tool.body_for(projection, tool.PROFILES['qw9'][0])
    body = tool.experiment_body(projection, tool.PROFILES['qw9'][0], tool.S1)
    payload = tool.experiment_wire(body, tool.S1)
    plan = json.loads((tmp_path/'plan.json').read_text())
    binding = {'artifacts': tool.BASELINE_FILES, 'runtime': tool.safe_runtime(tool.runtime()), 'directory': 'unused'}
    monkeypatch.setattr(tool, 'baseline_binding', lambda *a: binding)
    plan.update(experiment=tool.S1, experiment_version=1, task_id='T451', baseline=binding)
    plan['cases'][0].update(common_input_sha256=tool.common_hash(body),
        candidate_input_sha256=tool.digest(body), baseline_input_sha256=tool.digest(baseline_body),
        candidate_schema_sha256=tool.digest(body['response_format']['json_schema']['schema']),
        candidate_wire_sha256=hashlib.sha256(payload).hexdigest(), baseline_wire_sha256=tool.digest(baseline_body),
        wire_size_bytes=len(payload), serializer_version=1, target_property_order=tool.wire_target(tool.S1),
        **tool.intent_metrics(baseline_body, body))
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    seen, generation = [], []
    def handler(request):
        if request.url.path in ('/apply-template', '/v1/chat/completions'):
            seen.append((request.url.path, request.content))
        if request.url.path == '/health': value = {'status': 'ok'}
        elif request.url.path == '/apply-template': value = {'prompt': 'rendered'}
        elif request.url.path == '/tokenize': value = {'tokens': [1]*100}
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
    monkeypatch.setattr(tool, 'count_prompt', real_count)
    return plan, payload, children, seen, generation


def test_candidate_wire_is_saved_then_used_for_template_and_generation_once(tmp_path, monkeypatch):
    plan, payload, children, seen, generation = ready_shape(tmp_path, monkeypatch)
    assert tool.run(tmp_path, 'qw9') == 0
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    row = result['rows'][0]
    raw = json.loads((tmp_path/'private/raw.jsonl').read_text())
    assert seen[0] == ('/apply-template', payload)
    assert seen[-1] == ('/v1/chat/completions', payload)
    assert row['candidate_wire_sha256'] == raw['candidate_wire_sha256'] == hashlib.sha256(payload).hexdigest()
    assert row['candidate_input_sha256'] == row['input_sha256'] != row['baseline_input_sha256']
    assert row['baseline_wire_sha256'] == plan['cases'][0]['baseline_input_sha256']
    assert row['candidate_schema_pass'] and row['legacy_contract_pass'] and row['structural_pass']
    assert row['act_order_pass'] and row['adapter_status'] == 'INACTIVE_NULLS_REMOVED'
    assert 'proxy_units' not in row
    assert row['baseline_projection_proxy_units'] > 0 and row['candidate_prompt_schema_proxy_units'] > 0
    assert row['hard_pass'] is None and row['semantic_pass'] is None and row['style_pass'] is None
    assert result['source_unchanged'] and all(p.poll() is not None for p in children)
    with pytest.raises(FileExistsError): tool.run(tmp_path, 'qw9')
    assert len(generation) == 1


@pytest.mark.parametrize('change,expected', [('wire','FROZEN_WIRE_CHANGED'), ('order','FROZEN_WIRE_CHANGED'),
    ('source','FROZEN_SOURCE_CHANGED'), ('input','FROZEN_INPUT_CHANGED'), ('file','FROZEN_FILE_CHANGED'),
    ('messages','FROZEN_INPUT_CHANGED'), ('proxy','FROZEN_INPUT_CHANGED'),
    ('profile','EXPERIMENT_INVALID'), ('experiment','EXPERIMENT_INVALID')])
def test_drift_fails_before_claim_or_launch(tmp_path, monkeypatch, change, expected):
    plan, _, children, _, generation = ready_shape(tmp_path, monkeypatch)
    if change == 'wire': plan['cases'][0]['candidate_wire_sha256'] = 'wrong'
    if change == 'order': plan['cases'][0]['target_property_order'] = {}
    if change == 'source': monkeypatch.setattr(tool, 'source_identity', lambda *a: {'f':'changed'})
    if change == 'input': plan['cases'][0]['common_input_sha256'] = 'wrong'
    if change == 'file': monkeypatch.setattr(tool, 'file_identity', lambda path: {'sha256':'changed'})
    if change == 'messages': plan['cases'][0]['messages_sha256'] = 'wrong'
    if change == 'proxy': plan['cases'][0]['candidate_prompt_schema_proxy_units'] += 1
    if change == 'experiment': plan['experiment'] = 'unknown'
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    with pytest.raises(tool.StopComparison, match=f'^{expected}$'):
        tool.run(tmp_path, 'll8' if change == 'profile' else 'qw9')
    assert not children and not generation and not (tmp_path/'qw9.claim').exists()


@pytest.mark.parametrize('failure', ['save', 'http', 'runtime', 'token_count'])
def test_finite_failure_cleanup_and_no_retry(tmp_path, monkeypatch, failure):
    _, _, children, _, generation = ready_shape(tmp_path, monkeypatch, fail_http=failure == 'http')
    if failure == 'save':
        original = Path.open
        def open_file(path, *a, **kw):
            if path.name.endswith('.request.bin'): raise PermissionError('PRIVATE_WIRE_PATH')
            return original(path, *a, **kw)
        monkeypatch.setattr(Path, 'open', open_file)
    if failure == 'runtime':
        identity = tool.runtime(); identity['template_sha256'] = 'wrong'
        monkeypatch.setattr(tool, 'runtime', lambda: identity)
    if failure == 'token_count':
        monkeypatch.setattr(tool, 'count_prompt', lambda *a, **kw: {'prompt_tokens_actual':101})
    assert tool.run(tmp_path, 'qw9') == 2
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    assert result['stop_reason'] == {'save':'PermissionError','http':'HTTP_503',
        'runtime':'BASELINE_RUNTIME_MISMATCH','token_count':'TOKEN_COUNT_MISMATCH'}[failure]
    assert 'PRIVATE' not in json.dumps(result) and all(p.poll() is not None for p in children)
    assert len(generation) == (1 if failure in ('http','token_count') else 0)
    with pytest.raises(FileExistsError): tool.run(tmp_path, 'qw9')


def test_prepare_only_candidate_and_exact_scoped_sources(tmp_path, monkeypatch):
    current = tool.source_identity(tool.S1)
    baseline_sources = tool.source_identity()
    assert set(current) - set(baseline_sources) == set(tool.S1_SOURCES)
    assert set(tool.S1_SOURCES) <= tool.EXPERIMENT_SOURCE_DELTA[tool.S1]
    assert tool.EXPERIMENT_SOURCE_DELTA[tool.K1] == tool.EXPERIMENT_SOURCE_DELTA[tool.C1] | set(tool.K1_SOURCES)
    monkeypatch.setattr(tool, 'file_identity', lambda path: {'path':str(path),'sha256':'a'})
    monkeypatch.setattr(tool, 'baseline_binding', lambda *a: {'reviewed':True})
    tool.prepare(tmp_path, experiment=tool.S1, baseline_source=tmp_path/'baseline')
    plan = json.loads((tmp_path/'plan.json').read_text())
    assert plan['source'] == current and plan['task_id'] == 'T451' and set(plan['profiles']) == {'qw9'}
    assert len(plan['cases']) == 32 and plan['retry'] == plan['repair'] == 0
    for row in plan['cases']:
        assert row['candidate_input_sha256'] != row['baseline_input_sha256'] == row['baseline_wire_sha256']
        assert row['candidate_wire_sha256'] != row['baseline_wire_sha256']
        assert row['target_property_order'] == tool.wire_target(tool.S1)


@pytest.mark.parametrize('mutation', [None, 'artifact', 'unknown_source', 'unevaluated', 'missing', 'input'])
def test_baseline_binding_reuses_reviewed_annotations_only(baseline, mutation):
    current, files, path, write = baseline
    current['experiment'] = tool.S1
    current['source'].update({name:'new' for name in tool.S1_SOURCES})
    annotation = files['qw9-annotations.json']
    annotation.update(evaluated=32, unevaluated=0)
    for row in annotation['rows']: row.update(hard_pass=True, reason_codes=[])
    if mutation == 'unknown_source': current['source']['unexpected.py'] = 'new'
    if mutation == 'unevaluated': annotation['unevaluated'] = 1
    if mutation == 'missing': annotation['rows'].pop()
    if mutation == 'input': current['cases'][0]['baseline_input_sha256'] = 'wrong'
    write()
    if mutation == 'artifact': (path/'qw9-annotations.json').write_text('{}')
    if mutation:
        with pytest.raises(tool.StopComparison, match='^BASELINE_MISMATCH$'): tool.baseline_binding(current,path)
    else:
        binding = tool.baseline_binding(current,path)
        assert binding['baseline_quality']['hard_fail'] == binding['baseline_quality']['fabricated_evidence'] == 0


def test_candidate_metrics_do_not_allow_prompt_or_sampling_drift():
    p = tool.project(tool.cases()[0], 'baseline')
    baseline_body = tool.body_for(p, tool.PROFILES['qw9'][0])
    candidate = tool.experiment_body(p, tool.PROFILES['qw9'][0], tool.S1)
    for key in ('messages','max_tokens','model'):
        changed = deepcopy(candidate); changed[key] = 'PRIVATE_CHANGED'
        with pytest.raises(tool.StopComparison, match='^BASELINE_INPUT_CHANGED$'):
            tool.intent_metrics(baseline_body, changed)
