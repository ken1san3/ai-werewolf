import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from scripts import phase6_model_comparison as tool
from scripts.phase6_conversation_suite import example


def test_all_models_receive_identical_32_inputs_except_model_id():
    for case in tool.cases():
        projection = tool.project(case, 'baseline')
        bodies = [tool.body_for(projection, p[0]) for p in tool.PROFILES.values()]
        assert len({tool.common_hash(b) for b in bodies}) == 1
        for body in bodies:
            assert body['messages'] == [{'role': m.role, 'content': m.content} for m in projection.messages]
            assert body['response_format'] == tool.provider_body(projection)['response_format']
            assert body['chat_template_kwargs'] == {'enable_thinking': False}
            assert body['max_tokens'] == 512 and body['temperature'] == 0.2
        assert all(b['model'].endswith('.gguf') for b in bodies)


@pytest.mark.parametrize('code', [302, 400, 503])
def test_http_does_not_redirect_retry_or_expose_response_body(code):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(code, headers={'Location': 'https://example.invalid/'}, text='private body')
    with pytest.raises(tool.StopComparison, match=f'^HTTP_{code}$'):
        asyncio.run(tool._request('/health', None, 1, transport=httpx.MockTransport(handler)))
    assert len(seen) == 1
    assert str(seen[0].url) == 'http://127.0.0.1:8082/health'


def test_total_request_timeout_cancels_transport():
    ended = []
    async def handler(request):
        try:
            await asyncio.sleep(10)
        finally:
            ended.append(True)
    with pytest.raises(TimeoutError):
        asyncio.run(tool._request('/health', None, 0.01, transport=httpx.MockTransport(handler)))
    assert ended == [True]


def test_transport_response_limit_and_endpoint_allowlist():
    with pytest.raises(tool.StopComparison, match='RESPONSE_TOO_LARGE'):
        asyncio.run(tool._request('/health', None, 1, transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=b' ' * 262145))))
    with pytest.raises(tool.StopComparison, match='ENDPOINT_NOT_ALLOWED'):
        asyncio.run(tool._request('/completion', {}, 1))


@pytest.mark.parametrize('count,passes', [(7679, True), (7680, False), (8192, False)])
def test_actual_context_output_reservation_boundary(monkeypatch, count, passes):
    def fake_request(path, body):
        return {'prompt': 'native template'} if path == '/apply-template' else {'tokens': [1] * count}
    monkeypatch.setattr(tool, 'request', fake_request)
    body = {'response_format': {'type': 'json_schema'}, 'max_tokens': 512}
    if passes:
        result = tool.count_prompt(body)
        assert result['remaining_context_tokens'] == 0
        assert result['schema_changes_rendered_prompt'] is False
    else:
        with pytest.raises(tool.StopComparison, match='CONTEXT_OVERFLOW'):
            tool.count_prompt(body)


def test_claim_is_exclusive_and_retains_first_attempt(tmp_path):
    tool.claim_run(tmp_path, 'qw9')
    original = (tmp_path/'qw9.claim').read_bytes()
    with pytest.raises(FileExistsError):
        tool.claim_run(tmp_path, 'qw9')
    assert (tmp_path/'qw9.claim').read_bytes() == original


def test_near_copy_is_screened_without_pretending_to_judge_semantics():
    case = next(c for c in tool.cases() if c.case_id == 'G12-1')
    p = tool.project(case, 'baseline')
    value = example(p.canonical_input)
    peer = case.request.history.records[0].message
    value['decision']['message'] = peer.replace('compare', 'examine')
    result = tool.screen(case, p, json.dumps(value))
    assert result['structural_pass']
    assert result['near_peer_copy_screen'] is True
    assert result['exact_long_copy'] is False
    assert result['product_text_guard_rejects'] is False
    assert result['hard_pass'] is None and result['semantic_pass'] is None
    assert result['style_pass'] is None


def test_exact_peer_copy_and_short_agreement_are_distinguished():
    case = next(c for c in tool.cases() if c.case_id == 'G12-1')
    p = tool.project(case, 'baseline')
    value = example(p.canonical_input)
    value['decision']['message'] = case.request.history.records[0].message
    result = tool.screen(case, p, json.dumps(value))
    assert result['product_text_guard_rejects'] is True
    value['decision']['message'] = 'I agree.'
    result = tool.screen(case, p, json.dumps(value))
    assert result['product_text_guard_rejects'] is False
    assert result['near_peer_copy_screen'] is False


def test_cleanup_only_operates_on_supplied_process_object():
    owned = Mock(returncode=0)
    owned.poll.return_value = None
    other = Mock()
    assert tool.stop_owned(owned) == 0
    owned.terminate.assert_called_once_with()
    owned.wait.assert_called_once_with(timeout=15)
    owned.kill.assert_not_called()
    assert not other.mock_calls


def test_load_failure_is_preserved_and_cannot_be_retried(tmp_path, monkeypatch):
    case = tool.cases()[0]
    p = tool.project(case, 'baseline')
    identity = {'path': tool.PROFILES['qw9'][0], 'size': 1, 'mtime_ns': 1, 'sha256': 'a'}
    monkeypatch.setattr(tool, 'file_identity', lambda path: identity)
    monkeypatch.setattr(tool, 'source_identity', lambda: {'f': 'hash'})
    monkeypatch.setattr(tool, 'cases', lambda: (case,))
    monkeypatch.setattr(tool, 'port_free', lambda: True)
    private = tmp_path/'private'
    private.mkdir()
    monkeypatch.setattr(tool, 'create_private_evidence_container', lambda *a, **kw: private)
    launch = Mock(side_effect=OSError('private path must not leave this exception'))
    monkeypatch.setattr(tool.subprocess, 'Popen', launch)
    plan = {'source': {'f': 'hash'}, 'profiles': {'qw9': {'model': identity,
        'quantization': 'Q4_K_M', 'argv': tool.launch_args('qw9'), 'runtime_files': [], 'available': True}},
        'cases': [{'case_id': case.case_id, 'common_input_sha256': tool.common_hash(tool.body_for(p, identity['path']))}]}
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    assert tool.run(tmp_path, 'qw9') == 2
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    assert result['status'] == 'STOPPED' and result['stop_reason'] == 'OSError'
    assert result['owned_processes_remaining'] == 0 and result['rows'] == []
    assert 'private path' not in json.dumps(result)
    with pytest.raises(FileExistsError):
        tool.run(tmp_path, 'qw9')
    assert launch.call_count == 1


def test_safe_runtime_excludes_untrusted_strings():
    raw = {'model_path': 'PRIVATE_MODEL_PATH', 'build': 'PRIVATE_BUILD',
           'template_sha256': 'f'*64, 'generation_settings': {
               'n_ctx': 8192, 'temperature': 0.2, 'seed': 'PRIVATE_SEED',
               'antiprompt': ['PRIVATE_PROMPT'], 'top_p': float('nan')}}
    safe = tool.safe_runtime(raw)
    assert 'PRIVATE' not in json.dumps(safe)
    assert safe['build'] == 'UNKNOWN'
    assert safe['generation_settings_numeric'] == {'n_ctx': 8192, 'temperature': 0.2}


def test_cleanup_failure_of_monitor_cannot_skip_provider_cleanup():
    monitor, provider = Mock(), Mock(returncode=0)
    monitor.poll.return_value = None
    monitor.terminate.side_effect = OSError('private detail')
    monitor.kill.side_effect = OSError('private detail')
    provider.poll.side_effect = [None, 0]
    result = tool.cleanup_owned(monitor, provider)
    provider.terminate.assert_called_once_with()
    provider.wait.assert_called_once_with(timeout=15)
    assert result['monitor_cleanup_error'] == 'CLEANUP_FAILED'
    assert result['owned_processes_remaining'] == 1
    assert 'private detail' not in json.dumps(result)


def fake_ready_run(tmp_path, monkeypatch, response):
    case = tool.cases()[0]
    p = tool.project(case, 'baseline')
    identity = {'path': tool.PROFILES['qw9'][0], 'size': 1, 'mtime_ns': 1, 'sha256': 'a'}
    monkeypatch.setattr(tool, 'file_identity', lambda path: identity)
    monkeypatch.setattr(tool, 'source_identity', lambda: {'f': 'hash'})
    monkeypatch.setattr(tool, 'cases', lambda: (case,))
    monkeypatch.setattr(tool, 'port_free', lambda: True)
    monkeypatch.setattr(tool, 'owned_listener', lambda proc: True)
    monkeypatch.setattr(tool, 'process_memory', lambda proc: None)
    monkeypatch.setattr(tool, 'count_prompt', lambda body: {'prompt_tokens_actual': 100})
    monkeypatch.setattr(tool, 'runtime', lambda: {'build': 'PRIVATE_BUILD', 'model_path': identity['path'],
        'template_sha256': 'f'*64, 'generation_settings': {'antiprompt': 'PRIVATE_PROMPT'}})
    private = tmp_path/'private'
    private.mkdir()
    monkeypatch.setattr(tool, 'create_private_evidence_container', lambda *a, **kw: private)
    children = []
    class Child:
        pid = 123
        returncode = None
        def poll(self): return self.returncode
        def terminate(self): self.returncode = 0
        def kill(self): self.returncode = -1
        def wait(self, **kw): return self.returncode
    def launch(*a, **kw):
        children.append(Child())
        return children[-1]
    monkeypatch.setattr(tool.subprocess, 'Popen', launch)
    calls = []
    def request(path, body=None, **kw):
        if path == '/health': return {'status': 'ok'}
        calls.append(path)
        return response
    monkeypatch.setattr(tool, 'request', request)
    plan = {'source': {'f': 'hash'}, 'profiles': {'qw9': {'model': identity,
        'quantization': 'Q4_K_M', 'argv': tool.launch_args('qw9'), 'runtime_files': [], 'available': True}},
        'cases': [{'case_id': case.case_id, 'common_input_sha256': tool.common_hash(tool.body_for(p, identity['path']))}]}
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    return calls, children


def test_raw_model_fields_cannot_escape_to_results_or_stdout(tmp_path, monkeypatch, capsys):
    raw = json.dumps({'decision': {'kind': 'PRIVATE_KIND'},
                      'discussion': {'speech_act': {'kind': 'PRIVATE_ACT'}}})
    calls, children = fake_ready_run(tmp_path, monkeypatch, {
        'choices': [{'message': {'content': raw}, 'finish_reason': 'PRIVATE_FINISH'}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 'PRIVATE_USAGE'}})
    assert tool.run(tmp_path, 'qw9') == 0
    stored = (tmp_path/'qw9-results.json').read_text()
    assert 'PRIVATE' not in stored and 'PRIVATE' not in capsys.readouterr().out
    row = json.loads(stored)['rows'][0]
    assert row['speech_act'] == row['decision_kind'] == row['finish_reason'] == 'UNKNOWN'
    assert row['completion_tokens'] is None
    assert 'PRIVATE_ACT' in (tmp_path/'private/raw.jsonl').read_text()
    assert len(calls) == 1 and all(p.poll() is not None for p in children)


def test_save_before_dispatch_failure_still_cleans_both_children(tmp_path, monkeypatch):
    calls, children = fake_ready_run(tmp_path, monkeypatch, None)
    original = Path.write_text
    failed = []
    def write(path, value, *args, **kwargs):
        if path.name == 'qw9-results.json' and not failed:
            failed.append(True)
            raise PermissionError('private output path')
        return original(path, value, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', write)
    assert tool.run(tmp_path, 'qw9') == 2
    result = json.loads((tmp_path/'qw9-results.json').read_text())
    assert result['stop_reason'] == 'PermissionError'
    assert result['rows'][0]['latency_real_sec'] is None
    assert result['rows'][0]['generation_status'] == 'ERROR'
    assert not calls and all(p.poll() is not None for p in children)
    assert result['owned_processes_remaining'] == 0
