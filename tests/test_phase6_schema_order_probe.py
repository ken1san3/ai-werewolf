import asyncio
from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from scripts import phase6_model_comparison as tool
from scripts import phase6_schema_order_probe as probe
from scripts.phase6_conversation_suite import example


def source_body():
    p = tool.project(tool.cases()[0], 'baseline')
    return tool.body_for(p, tool.PROFILES['qw9'][0])


def assert_order_delta(before, after, path=()):
    if isinstance(before, dict):
        target = (len(path) == 8 and path[:6] == ('response_format', 'json_schema', 'schema',
                  'properties', 'discussion', 'oneOf') and path[-1] == 'properties')
        assert list(after) == (list(probe.PROPOSAL_ORDER) if target else list(before)), path
        for key in before:
            assert_order_delta(before[key], after[key], (*path, key))
    elif isinstance(before, list):
        assert len(before) == len(after)
        for i, (a, b) in enumerate(zip(before, after)):
            assert_order_delta(a, b, (*path, i))
    else:
        assert before == after


def test_32_wire_changes_only_proposal_order_and_never_c2():
    assert len(tool.cases()) == 32
    for case in tool.cases():
        p = tool.project(case, 'baseline')
        body = tool.body_for(p, tool.PROFILES['qw9'][0])
        before = deepcopy(body)
        old = tool.wire_bytes(body)
        payload = probe.ordered_wire_bytes(body)
        assert payload != old and len(payload) == len(old)
        assert json.loads(payload) == body == before
        assert tool.wire_bytes(json.loads(payload)) == old
        assert probe.ordered_wire_bytes(body) == payload
        assert_order_delta(json.loads(old), json.loads(payload))
        none = body['response_format']['json_schema']['schema']['$defs']['speech_act']['oneOf'][0]
        assert none['required'] == ['kind'] and 'reason' not in none['properties']


def test_legal_none_and_non_none_still_meet_schema_and_product_contract():
    from jsonschema import Draft202012Validator
    case = tool.cases()[0]
    p = tool.project(case, 'baseline')
    body = source_body()
    ordered = json.loads(probe.ordered_wire_bytes(body))
    value = example(p.canonical_input)
    for act in (value['discussion']['speech_act'], {'kind': 'NONE'}):
        value['discussion']['speech_act'] = act
        for candidate in (body, ordered):
            schema = candidate['response_format']['json_schema']['schema']
            assert Draft202012Validator(schema).is_valid(value)
        tool.parse_llm_output(json.dumps(value), projection=p)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'duplicate', 'reversed', 'open', 'empty'])
def test_shape_drift_rejected(mutation):
    body = source_body()
    branches = body['response_format']['json_schema']['schema']['properties']['discussion']['oneOf']
    branch = branches[0]
    if mutation == 'missing': del branch['properties']['speech_act']
    if mutation == 'extra': branch['properties']['extra'] = {'type': 'string'}
    if mutation == 'duplicate': branch['required'].append('speech_act')
    if mutation == 'reversed': branch['required'].reverse()
    if mutation == 'open': branch['additionalProperties'] = True
    if mutation == 'empty': branches.clear()
    with pytest.raises(ValueError, match='^SCHEMA_SHAPE_CHANGED$'):
        probe.ordered_wire_bytes(body)


@pytest.mark.parametrize('endpoint', ['/apply-template', '/v1/chat/completions'])
def test_http_sends_exact_candidate_bytes(endpoint):
    body = source_body()
    payload = probe.ordered_wire_bytes(body)
    seen = []
    def handler(request):
        seen.append(request.content)
        return httpx.Response(200, json={})
    transport = httpx.MockTransport(handler)
    asyncio.run(tool._request(endpoint, body, 1, transport=transport, wire_payload=payload))
    asyncio.run(tool._request(endpoint, body, 1, transport=transport))
    c2 = tool.candidate_body(body)
    asyncio.run(tool._request(endpoint, c2, 1, transport=transport))
    assert seen == [payload, tool.wire_bytes(body), tool.wire_bytes(c2)]


@pytest.mark.parametrize('endpoint,body,payload', [('/health', None, b'{}'),
    ('/tokenize', {}, b'{}'), ('/apply-template', {}, b'{"changed":true}'),
    ('/v1/chat/completions', {}, b'PRIVATE_BAD_JSON'), ('/apply-template', {}, bytearray(b'{}'))])
def test_invalid_override_stops_before_transport(endpoint, body, payload):
    def forbidden(_): pytest.fail('transport reached')
    with pytest.raises(tool.StopComparison, match='^WIRE_OVERRIDE_INVALID$'):
        asyncio.run(tool._request(endpoint, body, 1, wire_payload=payload,
                                 transport=httpx.MockTransport(forbidden)))


def test_token_preflight_uses_same_bytes_only_for_schema_template(monkeypatch):
    body = source_body()
    payload = probe.ordered_wire_bytes(body)
    seen = []
    def request(path, body, **kw):
        seen.append((path, body, kw))
        return {'tokens': [1] * 100} if path == '/tokenize' else {'prompt': 'rendered'}
    monkeypatch.setattr(tool, 'request', request)
    assert tool.count_prompt(body, wire_payload=payload)['prompt_tokens_actual'] == 100
    assert seen[0] == ('/apply-template', body, {'wire_payload': payload})
    assert seen[1][0] == '/tokenize' and not seen[1][2]
    assert seen[2][0] == '/apply-template' and not seen[2][2]
    assert 'response_format' not in seen[2][1]


def ready_c1(tmp_path, monkeypatch, *, fail_http=False):
    from tests.test_phase6_model_comparison import fake_ready_run
    case = tool.cases()[0]
    p = tool.project(case, 'baseline')
    raw = json.dumps(example(p.canonical_input))
    response = {'choices': [{'message': {'content': raw}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 50}}
    _, children = fake_ready_run(tmp_path, monkeypatch, response)
    body = tool.body_for(p, tool.PROFILES['qw9'][0])
    payload = probe.ordered_wire_bytes(body)
    plan = json.loads((tmp_path/'plan.json').read_text())
    binding = {'artifacts': tool.BASELINE_FILES, 'runtime': tool.safe_runtime(tool.runtime()), 'directory': 'unused'}
    monkeypatch.setattr(tool, 'baseline_binding', lambda *a: binding)
    plan.update(experiment=tool.C1, experiment_version=1, task_id='T435', baseline=binding)
    plan['cases'][0].update(candidate_input_sha256=tool.digest(body), baseline_input_sha256=tool.digest(body),
        candidate_schema_sha256=tool.digest(body['response_format']['json_schema']['schema']),
        candidate_wire_sha256=hashlib.sha256(payload).hexdigest(), baseline_wire_sha256=tool.digest(body),
        wire_size_bytes=len(payload), serializer_version=1, target_property_order=list(probe.PROPOSAL_ORDER))
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    transport_seen, generation = [], []
    def handler(request):
        if request.url.path in ('/apply-template', '/v1/chat/completions'):
            transport_seen.append((request.url.path, request.content))
        if request.url.path == '/health': value = {'status': 'ok'}
        elif request.url.path == '/apply-template': value = {'prompt': 'rendered'}
        elif request.url.path == '/tokenize': value = {'tokens': [1] * 100}
        else:
            generation.append(True)
            stored = (tmp_path/'private'/(case.case_id+'.request.bin')).read_bytes()
            assert stored == request.content == payload
            consumed = json.loads((tmp_path/'qw9-results.json').read_text())['rows'][0]
            assert consumed['new_provider_calls'] == 1 and consumed['generation_status'] == 'STARTED'
            if fail_http:
                return httpx.Response(503, text='PRIVATE_RESPONSE')
            value = response
        return httpx.Response(200, json=value)
    monkeypatch.setattr(tool, 'request', lambda path, body=None, timeout=20, **kw: asyncio.run(
        tool._request(path, body, timeout, transport=httpx.MockTransport(handler), **kw)))
    # Restore the real preflight after fake_ready_run's intentionally cheap stub.
    monkeypatch.setattr(tool, 'count_prompt', REAL_COUNT_PROMPT)
    return plan, payload, children, transport_seen, generation


REAL_COUNT_PROMPT = tool.count_prompt


def test_private_saved_wire_matches_actual_transport_and_claim(tmp_path, monkeypatch):
    _, payload, children, seen, generation = ready_c1(tmp_path, monkeypatch)
    assert tool.run(tmp_path, 'qw9') == 0
    assert seen[0] == ('/apply-template', payload)
    assert seen[-1] == ('/v1/chat/completions', payload)
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    row = result['rows'][0]
    raw = json.loads((tmp_path/'private/raw.jsonl').read_text())
    assert row['candidate_wire_sha256'] == raw['candidate_wire_sha256'] == hashlib.sha256(payload).hexdigest()
    assert row['input_sha256'] == row['baseline_wire_sha256'] != row['candidate_wire_sha256']
    assert 'candidate_schema_pass' not in row and 'none_reason' not in row
    assert row['structural_pass'] and len(generation) == 1
    assert all(p.poll() is not None for p in children)
    with pytest.raises(FileExistsError): tool.run(tmp_path, 'qw9')
    assert len(generation) == 1


def test_wire_tamper_rejected_even_when_canonical_hash_matches(tmp_path, monkeypatch):
    plan, _, children, _, generation = ready_c1(tmp_path, monkeypatch)
    plan['cases'][0]['candidate_wire_sha256'] = plan['cases'][0]['baseline_wire_sha256']
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    with pytest.raises(tool.StopComparison, match='^FROZEN_WIRE_CHANGED$'): tool.run(tmp_path, 'qw9')
    assert not children and not generation and not (tmp_path/'qw9.claim').exists()


def test_wire_save_failure_never_dispatches_and_cleans_owned(tmp_path, monkeypatch):
    from pathlib import Path
    _, _, children, _, generation = ready_c1(tmp_path, monkeypatch)
    original = Path.open
    def open_file(path, *a, **kw):
        if path.name.endswith('.request.bin'): raise PermissionError('PRIVATE_WIRE_PATH')
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'open', open_file)
    assert tool.run(tmp_path, 'qw9') == 2
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    assert result['stop_reason'] == 'PermissionError' and not result['rows'] and not generation
    assert 'PRIVATE' not in json.dumps(result) and all(p.poll() is not None for p in children)


def test_c1_http_failure_preserves_consumed_attempt_and_cleans_owned(tmp_path, monkeypatch):
    _, _, children, _, generation = ready_c1(tmp_path, monkeypatch, fail_http=True)
    assert tool.run(tmp_path, 'qw9') == 2
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    assert result['stop_reason'] == 'HTTP_503' and len(generation) == 1
    assert result['rows'][0]['generation_status'] == 'ERROR'
    assert result['rows'][0]['new_provider_calls'] == 1
    assert 'PRIVATE' not in json.dumps(result) and all(p.poll() is not None for p in children)
    with pytest.raises(FileExistsError): tool.run(tmp_path, 'qw9')
    assert len(generation) == 1


@pytest.mark.parametrize('change,expected', [('source','FROZEN_SOURCE_CHANGED'),
    ('file','FROZEN_FILE_CHANGED'), ('input','FROZEN_INPUT_CHANGED'), ('runtime','BASELINE_RUNTIME_MISMATCH')])
def test_c1_source_identity_input_runtime_changes_do_not_generate(tmp_path, monkeypatch, change, expected):
    plan, _, children, _, generation = ready_c1(tmp_path, monkeypatch)
    if change == 'source': monkeypatch.setattr(tool, 'source_identity', lambda: {'f':'changed'})
    if change == 'file': monkeypatch.setattr(tool, 'file_identity', lambda path: {'sha256':'changed'})
    if change == 'input':
        plan['cases'][0]['common_input_sha256'] = 'changed'
        (tmp_path/'plan.json').write_text(json.dumps(plan))
    if change == 'runtime':
        runtime = tool.runtime()
        runtime['template_sha256'] = 'changed'
        monkeypatch.setattr(tool, 'runtime', lambda: runtime)
        assert tool.run(tmp_path, 'qw9') == 2
        result = json.loads((tmp_path/'qw9-results.json').read_text())
        assert result['stop_reason'] == expected
        assert all(p.poll() is not None for p in children)
    else:
        with pytest.raises(tool.StopComparison, match=f'^{expected}$'): tool.run(tmp_path, 'qw9')
        assert not children
    assert not generation


def test_prepare_c1_freezes_wire_and_canonical_separately(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, 'file_identity', lambda path: {'path': str(path), 'sha256': 'a'})
    monkeypatch.setattr(tool, 'source_identity', lambda: {'helper': 'hash'})
    monkeypatch.setattr(tool, 'baseline_binding', lambda manifest, source: {'reviewed': True})
    tool.prepare(tmp_path, experiment=tool.C1, baseline_source=tmp_path/'baseline')
    plan = json.loads((tmp_path/'plan.json').read_text())
    assert set(plan['profiles']) == {'qw9'} and plan['task_id'] == 'T435'
    for row in plan['cases']:
        assert row['candidate_input_sha256'] == row['baseline_input_sha256'] == row['baseline_wire_sha256']
        assert row['candidate_wire_sha256'] != row['baseline_wire_sha256']
        assert row['target_property_order'].index('speech_act') == 4


@pytest.mark.parametrize('experiment', [tool.C1, tool.C2])
def test_source_exceptions_are_exact_and_all_current_hashes_remain_frozen(experiment):
    expected = {'scripts/phase6_model_comparison.py', 'scripts/phase6_none_reason_probe.py',
                'tests/test_phase6_none_reason_probe.py', 'scripts/phase6_schema_order_probe.py',
                'tests/test_phase6_schema_order_probe.py'}
    assert tool.EXPERIMENT_SOURCE_DELTA[experiment] == expected
    assert expected <= set(tool.source_identity())
