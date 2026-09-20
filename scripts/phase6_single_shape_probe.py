"""Test-only single-shape speech act; strict lossless mapping to legacy output."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from scripts.phase6_context_probe import wire_bytes

ACT_FIELDS = {
    'NONE': ('kind',),
    'CLAIM': ('kind', 'subject_player_id', 'topic', 'stance', 'evidence'),
    'QUESTION': ('kind', 'addressee_player_id', 'subject_player_id', 'topic', 'source'),
    'ANSWER': ('kind', 'addressee_player_id', 'in_reply_to', 'source_interpretation', 'topic', 'stance', 'evidence'),
    'REBUTTAL': ('kind', 'addressee_player_id', 'in_reply_to', 'source_interpretation', 'topic', 'stance', 'evidence'),
    'OPINION_CHANGE': ('kind', 'subject_player_id', 'dimension', 'prior', 'current', 'causes'),
    'RELATION_HYPOTHESIS': ('kind', 'source_player_id', 'target_player_id', 'relation', 'confidence', 'evidence'),
}
FIELDS = ('kind', *sorted(set().union(*map(set, ACT_FIELDS.values())) - {'kind'}))
DECISION_FIELDS = {'none': {'kind'}, 'chat': {'kind', 'option_id', 'message'},
    'vote': {'kind', 'option_id', 'target_player_id'},
    'ability': {'kind', 'option_id', 'target_player_ids'},
    'co_declare': {'kind', 'option_id', 'claimed_role_id', 'comment'}}
PROPOSAL_FIELDS = {'schema_version', 'base_revision', 'decision_kind', 'option_id', 'speech_act',
    'reaction', 'assessment_updates', 'claim_updates', 'relation_updates', 'strategy_update',
    'co_judgment', 'pre_vote_reassessment'}


def plain(value):
    if isinstance(value, Mapping): return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [plain(v) for v in value]
    return value


def _shape():
    raise ValueError('SCHEMA_SHAPE_CHANGED')


def _closed(schema, fields):
    if (set(schema) != {'type', 'properties', 'required', 'additionalProperties'}
            or schema['type'] != 'object' or schema['additionalProperties'] is not False
            or set(schema['properties']) != set(fields)
            or len(schema['required']) != len(fields) or set(schema['required']) != set(fields)):
        _shape()


def _local(value):
    if isinstance(value, dict):
        if '$ref' in value and (not isinstance(value['$ref'], str) or not value['$ref'].startswith('#/$defs/')):
            _shape()
        for child in value.values(): _local(child)
    elif isinstance(value, list):
        for child in value: _local(child)


def _legacy(schema):
    schema = plain(schema)
    try:
        _local(schema)
        Draft202012Validator.check_schema(schema)
        if set(schema) != {'$defs', 'type', 'properties', 'required', 'additionalProperties'}: _shape()
        _closed({k: v for k, v in schema.items() if k != '$defs'}, {'decision', 'discussion'})
        decisions, proposals = schema['properties']['decision'], schema['properties']['discussion']
        if set(decisions) != {'oneOf'} or set(proposals) != {'oneOf'}: _shape()
        signatures = set()
        for branch in decisions['oneOf']:
            kind = branch['properties']['kind']['const']
            if kind not in DECISION_FIELDS: _shape()
            _closed(branch, DECISION_FIELDS[kind])
            option = None if kind == 'none' else branch['properties']['option_id']['const']
            signature = (kind, option)
            if signature in signatures: _shape()
            signatures.add(signature)
        if not signatures: _shape()
        for branch in proposals['oneOf']:
            _closed(branch, PROPOSAL_FIELDS)
            if branch['properties']['decision_kind'].get('const') not in DECISION_FIELDS: _shape()
        matched = set()
        for kind, option in signatures:
            matches = [i for i, branch in enumerate(proposals['oneOf'])
                if branch['properties']['decision_kind'].get('const') == kind
                and Draft202012Validator(branch['properties']['option_id']).is_valid(option)]
            if len(matches) != 1: _shape()
            matched.update(matches)
        if matched != set(range(len(proposals['oneOf']))): _shape()
        act = schema['$defs']['speech_act']
        if set(act) != {'oneOf'} or len(act['oneOf']) != len(ACT_FIELDS): _shape()
        branches = {}
        for branch in act['oneOf']:
            kind = branch['properties']['kind']['const']
            if kind not in ACT_FIELDS or kind in branches: _shape()
            _closed(branch, ACT_FIELDS[kind])
            if branch['properties']['kind'] != {'const': kind}: _shape()
            branches[kind] = branch
        if list(branches) != list(ACT_FIELDS): _shape()
        return schema, branches
    except (KeyError, TypeError, SchemaError):
        _shape()


def candidate_schema(legacy_schema):
    schema, branches = _legacy(legacy_schema)
    props = {'kind': {'enum': list(ACT_FIELDS)}}
    for field in FIELDS[1:]:
        leaves = []
        for kind, branch in branches.items():
            if field not in branch['properties']: continue
            leaf = deepcopy(branch['properties'][field])
            if kind == 'QUESTION' and field in {'subject_player_id', 'source'}:
                if set(leaf) != {'anyOf'} or len(leaf['anyOf']) != 2 or leaf['anyOf'].count({'type': 'null'}) != 1:
                    _shape()
                leaf = next(v for v in leaf['anyOf'] if v != {'type': 'null'})
            elif 'anyOf' in leaf or leaf.get('type') == 'null': _shape()
            leaves.append((kind, leaf))
        if field == 'source_interpretation':
            if leaves != [('ANSWER', {'const': 'QUESTION'}), ('REBUTTAL', {'const': 'CLAIM'})]: _shape()
            leaf = {'enum': ['QUESTION', 'CLAIM']}
        else:
            leaf = leaves[0][1]
            if any(v != leaf for _, v in leaves): _shape()
        props[field] = {'anyOf': [deepcopy(leaf), {'type': 'null'}]}
    schema['$defs']['speech_act'] = {'type': 'object', 'properties': props,
        'required': list(FIELDS), 'additionalProperties': False}
    Draft202012Validator.check_schema(schema)
    return schema


def candidate_body(baseline):
    body = json.loads(wire_bytes(baseline))
    body['response_format']['json_schema']['schema'] = candidate_schema(body['response_format']['json_schema']['schema'])
    return body


def single_shape_wire_bytes(body):
    body = json.loads(wire_bytes(body))
    act = body['response_format']['json_schema']['schema']['$defs']['speech_act']
    _closed(act, FIELDS)
    act['properties'] = {field: act['properties'][field] for field in FIELDS}
    return json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: raise ValueError('DUPLICATE_JSON_KEY')
            value[key] = item
        return value
    def reject(_): raise ValueError('NONFINITE_JSON_NUMBER')
    def finite(text):
        result = float(text)
        if not math.isfinite(result): reject(text)
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject, parse_float=finite)


def candidate_to_legacy(value, legacy_schema):
    schema, branches = _legacy(legacy_schema)
    if not Draft202012Validator(candidate_schema(schema)).is_valid(value):
        raise ValueError('CANDIDATE_SCHEMA_INVALID')
    act = value['discussion']['speech_act']
    kind = act['kind']
    fields = ACT_FIELDS[kind]
    if any(act[field] is not None for field in FIELDS if field not in fields):
        raise ValueError('SINGLE_SHAPE_INVALID')
    compact = {field: deepcopy(act[field]) for field in fields}
    check = {'$defs': schema['$defs'], **branches[kind]}
    if not Draft202012Validator(check).is_valid(compact):
        raise ValueError('SINGLE_SHAPE_INVALID')
    result = deepcopy(value)
    result['discussion']['speech_act'] = compact
    return result


def legacy_to_candidate(value, legacy_schema):
    schema, _ = _legacy(legacy_schema)
    if not Draft202012Validator(schema).is_valid(value): raise ValueError('LEGACY_SCHEMA_INVALID')
    result = deepcopy(value)
    act = result['discussion']['speech_act']
    result['discussion']['speech_act'] = {field: deepcopy(act[field]) if field in act else None for field in FIELDS}
    return result


def assess_candidate(case, projection, body, raw):
    from scripts.phase6_model_comparison import screen
    row = dict(candidate_schema_pass=False, single_shape_pass=None, legacy_contract_pass=None,
        adapter_status='NOT_APPLIED', adapted_output_sha256=None, act_order_pass=False,
        structural_pass=False, hard_pass=False, semantic_pass=None, style_pass=None,
        failure_reasons=['CANDIDATE_SCHEMA_INVALID'], screen_flags=[], decision_kind='UNKNOWN',
        speech_act='UNKNOWN', manual_text_review_required=True)
    try:
        schema = candidate_schema(projection.decision_schema)
        if body['response_format']['json_schema']['schema'] != schema: _shape()
        value = strict_json(raw)
        if not Draft202012Validator(schema).is_valid(value): return row
        row.update(candidate_schema_pass=True, single_shape_pass=False, failure_reasons=['SINGLE_SHAPE_INVALID'])
        adapted = candidate_to_legacy(value, projection.decision_schema)
    except (ValueError, KeyError, TypeError, SchemaError, RecursionError):
        return row
    payload = wire_bytes(adapted)
    result = screen(case, projection, payload.decode())
    result.update(candidate_schema_pass=True, single_shape_pass=True,
        legacy_contract_pass=result['structural_pass'], adapter_status='INACTIVE_NULLS_REMOVED',
        adapted_output_sha256=hashlib.sha256(payload).hexdigest(),
        act_order_pass=list(value['discussion']['speech_act']) == list(FIELDS))
    return result
