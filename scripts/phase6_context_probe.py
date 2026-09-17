"""Same-server tokenizer measurements; never generates or changes a running server."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_client.discussion.context import canonical_json_bytes
from ai_client.discussion.projection import _repair_message, token_proxy_units
from ai_client.llm.types import DecisionValidationCode


def wire_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def request(path, body=None, *, timeout=20):
    # Deliberately local and nonconfigurable: no prompt may leave this host.
    if path not in {'/props', '/slots', '/apply-template', '/tokenize', '/v1/chat/completions'}:
        raise ValueError('unsupported local endpoint')
    req = urllib.request.Request('http://127.0.0.1:8080' + path,
        data=None if body is None else wire_bytes(body),
        headers={'Content-Type': 'application/json'})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise RuntimeError('provider redirect forbidden')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=timeout) as response:
        raw = response.read(65537)
        if len(raw) > 65536: raise RuntimeError('provider response byte limit exceeded')
        return json.loads(raw)


def tokens(text, *, special=True):
    value = request('/tokenize', {'content': text, 'add_special': special,
                                 'parse_special': special})['tokens']
    if not isinstance(value, list) or any(type(n) is not int or n < 0 for n in value):
        raise RuntimeError('invalid tokenizer response')
    return len(value)


def template(body):
    return request('/apply-template', body)['prompt']


def provider_body(projection):
    # Matches OpenAICompatibleBackend._request_payload and the Phase 6 profile.
    return {'model': 'Qwen3.5-9B-Q4_K_M.gguf', 'messages': [
        {'role': m.role, 'content': m.content} for m in projection.messages],
        'response_format': {'type': 'json_schema', 'json_schema': {
            'name': 'aiwolf_brain_decision', 'strict': True,
            'schema': json.loads(canonical_json_bytes(projection.decision_schema))}},
        'stream': False, 'temperature': 0.2, 'max_tokens': 512,
        'reasoning_format': 'deepseek', 'chat_template_kwargs': {'enable_thinking': False}}


def runtime():
    props, slots = request('/props'), request('/slots')
    if props['build_info'] != 'b10697-093adb242':
        raise RuntimeError('unverified provider build; stop')
    if Path(props['model_path']).name != 'Qwen3.5-9B-Q4_K_M.gguf':
        raise RuntimeError('unexpected model; stop')
    if not slots or any(s['is_processing'] for s in slots):
        raise RuntimeError('provider busy or slots unavailable; stop')
    limits = {s['n_ctx'] for s in slots}
    if len(limits) != 1 or next(iter(limits)) != props['default_generation_settings']['n_ctx']:
        raise RuntimeError('ambiguous context limit; stop')
    return {'build': props['build_info'], 'model_path': props['model_path'],
            'context_per_slot': next(iter(limits)), 'slots': len(slots),
            'template_sha256': hashlib.sha256(props['chat_template'].encode()).hexdigest()}


def gguf_metadata(path):
    """Read scalar metadata only. Tensor data and vocabulary are never loaded."""
    sizes = {0: 'B', 1: 'b', 2: 'H', 3: 'h', 4: 'I', 5: 'i', 6: 'f',
             7: '?', 10: 'Q', 11: 'q', 12: 'd'}
    with open(path, 'rb') as f:
        def number(fmt):
            return struct.unpack('<'+fmt, f.read(struct.calcsize('<'+fmt)))[0]
        def string(keep):
            n = number('Q')
            if keep:
                return f.read(n).decode('utf-8')
            f.seek(n, 1)
        def value(kind, keep=False):
            if kind == 8:
                return string(keep)
            if kind == 9:
                child, count = number('I'), number('Q')
                if child in sizes:
                    f.seek(struct.calcsize('<'+sizes[child])*count, 1)
                else:
                    for _ in range(count): value(child)
                return None
            return number(sizes[kind])
        if f.read(4) != b'GGUF': raise ValueError('not GGUF')
        version, tensors, count = number('I'), number('Q'), number('Q')
        result = {'gguf_version': version, 'tensor_count': tensors}
        for _ in range(count):
            key, kind = string(True), number('I')
            keep = key in {'general.name', 'general.architecture'} or key.endswith('.context_length')
            v = value(kind, keep)
            if keep: result[key] = v
        return result


def measure_body(body, context_limit, *, proxy=None):
    full = template(body)
    bare = copy.deepcopy(body); bare.pop('response_format', None)
    without_schema = template(bare)
    no_prefix = copy.deepcopy(body); no_prefix['add_generation_prompt'] = False
    prompt_tokens = tokens(full)
    # Exercise the empty mandatory repair datum for every code/flag. A repair is
    # a NEW request, never original prompt + two independent generated outputs.
    repair_counts = []
    for code in DecisionValidationCode:
        for truncated in (False, True):
            repair = _repair_message(validation_code=code.value, excerpt='',
                original_scalars=sys.maxsize, original_utf8_bytes=sys.maxsize,
                excerpt_truncated=truncated, output_sha256='f'*64)
            repaired = copy.deepcopy(body)
            repaired['messages'].append({'role': 'user', 'content': repair})
            repair_counts.append(tokens(template(repaired)))
    max_repair = max(repair_counts)
    # Count exact payload, template and special tokens. One context token is
    # kept free for llama.cpp's strict input/context boundary (not a heuristic).
    margin = 1
    worst = max(prompt_tokens, max_repair) + body['max_tokens'] + margin
    if proxy is None:
        proxy = token_proxy_units(json.dumps({'messages': body['messages'],
            'output_schema': body['response_format']['json_schema']['schema']},
            ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    return {'proxy_units': proxy, 'messages_content_tokens': sum(tokens(m['content'], special=False) for m in body['messages']),
        'messages_json_tokens': tokens(canonical_json_bytes(body['messages']).decode(), special=False),
        'base_prompt_tokens': prompt_tokens, 'schema_template_overhead': prompt_tokens-tokens(without_schema),
        'schema_changes_rendered_prompt': full != without_schema,
        'generation_prefix_overhead': prompt_tokens-tokens(template(no_prefix)),
        'provider_request_tokens_diagnostic_only': tokens(wire_bytes(body).decode(), special=False),
        'provider_request_bytes': len(wire_bytes(body)),
        'max_generation_tokens': body['max_tokens'], 'repair_prompt_tokens': max_repair,
        'repair_generation_reserve': body['max_tokens'], 'repair_excerpt_policy': 'empty mandatory datum; nonempty requires recount',
        'safety_margin': margin, 'worst_case_total': worst, 'remaining_tokens': context_limit-worst,
        'proxy_actual_ratio': round(proxy/prompt_tokens, 4),
        'input_hash': hashlib.sha256(wire_bytes(body)).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-output', type=Path, required=True)
    args = parser.parse_args()
    meta = runtime(); meta['model_metadata'] = gguf_metadata(meta['model_path'])
    args.runtime_output.parent.mkdir(parents=True, exist_ok=True)
    args.runtime_output.write_text(json.dumps(meta, indent=2), encoding='utf-8')
    print(json.dumps(meta))
