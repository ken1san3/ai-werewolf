"""Offline-only CO tuple correlation over the unchanged GB1/C5 contract."""
from __future__ import annotations

from copy import deepcopy

from jsonschema import Draft202012Validator

from ai_client.llm.types import PromptProjection
from scripts import phase6_grounding_basis_probe as gb1


MAX_DECLARE_TUPLES = 64


def _invalid():
    raise ValueError('SCHEMA_SHAPE_CHANGED')


def _closed_objects(node):
    if not isinstance(node, dict):
        _invalid()
    if node.get('type') == 'object':
        props, required = node.get('properties'), node.get('required')
        if (not isinstance(props, dict) or not isinstance(required, list)
                or node.get('additionalProperties') is not False
                or any(type(key) is not str for key in required)
                or len(required) != len(set(required)) or set(required) != set(props)):
            _invalid()
    for name in ('$defs', 'properties'):
        for child in node.get(name, {}).values():
            _closed_objects(child)
    for name in ('oneOf', 'anyOf'):
        for child in node.get(name, []):
            _closed_objects(child)
    if 'items' in node:
        _closed_objects(node['items'])


def _candidate(candidate_schema, projection):
    if not isinstance(projection, PromptProjection) or projection.discussion_capture is None:
        _invalid()
    try:
        schema = gb1.ic2._plain(candidate_schema)
        gb1.ic2._schema_contract(schema)
        _closed_objects(schema)
        kind = schema['$defs']['speech_act']['properties']['kind']['const']
        expected = gb1.gc2.candidate_schema(projection, {'speech_act_kind': kind})
        if schema != expected:
            _invalid()
        return schema
    except (ValueError, KeyError, TypeError, AttributeError):
        _invalid()


def co_consistent_schema(candidate_schema, projection):
    """Close only decision/discussion/CO correlation; never choose a tuple."""
    schema = _candidate(candidate_schema, projection)
    if projection.discussion_capture.trigger.kind != 'CO_OPPORTUNITY':
        return deepcopy(schema)
    decisions = schema['properties']['decision']['oneOf']
    discussions = schema['properties']['discussion']['oneOf']
    co = schema['$defs']['co']['oneOf']
    by_kind = {}
    for branch in discussions:
        kind = branch['properties']['decision_kind']['const']
        if kind in by_kind or kind not in ('none', 'co_declare'):
            _invalid()
        by_kind[kind] = branch
    if (not decisions or decisions[0]['properties'] != {'kind': {'const': 'none'}}
            or 'none' not in by_kind or len(co) != len(decisions)):
        _invalid()
    tuples, option_ids = [], set()
    for decision, judgment in zip(decisions[1:], co[1:]):
        props = decision['properties']
        if props['kind'] != {'const': 'co_declare'}:
            _invalid()
        option = props['option_id']['const']
        roles = props['claimed_role_id']['enum']
        if (type(option) is not str or option in option_ids or not roles
                or any(type(role) is not str for role in roles)
                or len(roles) != len(set(roles))):
            _invalid()
        option_ids.add(option)
        if (judgment['properties']['selected_option_id'] != {'const': option}
                or judgment['properties']['claimed_role_id'] != {'enum': roles}):
            _invalid()
        for role in roles:
            tuples.append((option, role, decision, judgment))
            if len(tuples) > MAX_DECLARE_TUPLES:
                raise ValueError('CO_TUPLE_LIMIT')
    if set(by_kind) != ({'none', 'co_declare'} if tuples else {'none'}):
        _invalid()

    def branch(decision, discussion, judgment):
        root = deepcopy({key: value for key, value in schema.items() if key != '$defs'})
        root['properties']['decision'] = deepcopy(decision)
        root['properties']['discussion'] = deepcopy(discussion)
        root['properties']['discussion']['properties']['co_judgment'] = deepcopy(judgment)
        return root

    branches = []
    for status in ('SILENCE', 'DEFER'):
        judgment = deepcopy(co[0])
        judgment['properties']['decision'] = {'const': status}
        branches.append(branch(decisions[0], by_kind['none'], judgment))
    for option, role, decision, judgment in tuples:
        result = branch(decision, by_kind['co_declare'], judgment)
        props = result['properties']
        props['decision']['properties']['claimed_role_id'] = {'const': role}
        props['discussion']['properties']['option_id'] = {'const': option}
        props['discussion']['properties']['co_judgment']['properties']['claimed_role_id'] = {'const': role}
        branches.append(result)
    return {'$defs': deepcopy(schema['$defs']), 'oneOf': branches}


def output_body(baseline, choice, projection):
    body = gb1.output_body(baseline, choice, projection)
    output = body['response_format']['json_schema']
    output['schema'] = co_consistent_schema(output['schema'], projection)
    return body


def validate_final(raw, choice, projection):
    value = gb1.strict_json(raw)
    selected = gb1._choice(choice, projection)
    original = gb1.gc2.candidate_schema(projection, {'speech_act_kind': selected['speech_act_kind']})
    schema = co_consistent_schema(original, projection)
    if not Draft202012Validator(schema).is_valid(value):
        raise ValueError('CO_CONSISTENCY_SCHEMA_INVALID')
    return gb1.validate_final(raw, selected, projection)
