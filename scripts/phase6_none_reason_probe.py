"""Pure C2 experiment adapter; never installed in the product provider/parser."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

REASONS = ('NO_NEW_INFORMATION', 'WAITING_FOR_OTHERS', 'DELIBERATE_SILENCE',
           'INDEPENDENT_STATEMENT')
BASE_NONE = {'type': 'object', 'properties': {'kind': {'const': 'NONE'}},
             'required': ['kind'], 'additionalProperties': False}


def candidate_body(baseline_body):
    body = deepcopy(baseline_body)
    try:
        schema = body['response_format']['json_schema']['schema']
        branches = schema['$defs']['speech_act']['oneOf']
        matches = [b for b in branches if b.get('properties', {}).get('kind', {}).get('const') == 'NONE']
        if len(matches) != 1 or matches[0] != BASE_NONE:
            raise ValueError
        branch = matches[0]
        branch['properties']['reason'] = {'type': 'string', 'enum': list(REASONS)}
        branch['required'].append('reason')
        validate_schema(schema)
    except (KeyError, TypeError, ValueError, SchemaError):
        raise ValueError('SCHEMA_SHAPE_CHANGED') from None
    return body


def validate_schema(schema):
    def local_only(value):
        if isinstance(value, dict):
            if '$ref' in value and not value['$ref'].startswith('#/$defs/'):
                raise ValueError('SCHEMA_SHAPE_CHANGED')
            for item in value.values():
                local_only(item)
        elif isinstance(value, list):
            for item in value:
                local_only(item)
    local_only(schema)
    Draft202012Validator.check_schema(schema)


def _strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    def nonfinite(_):
        raise ValueError
    def finite_float(text):
        number = float(text)
        if not math.isfinite(number):
            raise ValueError
        return number
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite, parse_float=finite_float)


def assess_candidate(case, baseline_projection, body, raw):
    # Lazy import avoids a runner/helper import cycle. No process/network work.
    from scripts.phase6_model_comparison import screen, wire_bytes
    row = {'candidate_schema_pass': False, 'legacy_contract_pass': None,
           'adapter_status': 'NOT_APPLIED', 'none_reason': None,
           'adapted_output_sha256': None, 'structural_pass': False,
           'hard_pass': False, 'semantic_pass': None, 'style_pass': None,
           'failure_reasons': ['CANDIDATE_SCHEMA_INVALID'], 'screen_flags': [],
           'decision_kind': 'UNKNOWN', 'speech_act': 'UNKNOWN',
           'manual_text_review_required': True}
    try:
        value = _strict_json(raw)
        schema = body['response_format']['json_schema']['schema']
        if not Draft202012Validator(schema).is_valid(value):
            return row
    except (ValueError, TypeError, RecursionError):
        return row
    adapted = deepcopy(value)
    act = adapted['discussion']['speech_act']
    reason = act.pop('reason') if act['kind'] == 'NONE' else None
    adapted_bytes = wire_bytes(adapted)
    result = screen(case, baseline_projection, adapted_bytes.decode('utf-8'))
    result.update(candidate_schema_pass=True, legacy_contract_pass=result['structural_pass'],
                  adapter_status='NONE_REASON_REMOVED' if reason is not None else 'UNCHANGED_NON_NONE',
                  none_reason=reason, adapted_output_sha256=hashlib.sha256(adapted_bytes).hexdigest())
    return result
