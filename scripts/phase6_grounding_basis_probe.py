"""Test-only explicit authoritative basis selection; no product projection changes."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from jsonschema import Draft202012Validator

from ai_client.discussion.context import canonical_json_bytes
from ai_client.llm.types import PromptProjection
from scripts import phase6_grounding_closed_probe as gc2
from scripts import phase6_intent_choice_probe as ic2
from scripts import phase6_stage_control_probe as sc2

CHOICE_TOKENS = 32
OUTPUT_TOKENS = 480
CATALOG_KEY = 'grounding_basis_catalog'
CHOICE_INSTRUCTION = (
    'Using only the existing canonical input, choose the single speech act kind most '
    'appropriate for the later public response and return it exactly as the response '
    'schema requires. Select zero to two authoritative_fact_ids from '
    'grounding_basis_catalog for the later response. Do not generate message text yet.'
)
OUTPUT_INSTRUCTION = (
    'Keep the locked speech act kind unchanged. Using only the existing grounding and '
    'offered actions, return the complete legacy output required by the response schema. '
    'Resolve authoritative_fact_ids through grounding_basis_catalog in the user input '
    'as the selected basis.\nLocked choice:'
)
strict_json = ic2.strict_json


def _invalid():
    raise ValueError('GROUNDING_CATALOG_INVALID')


def _input(projection):
    if not isinstance(projection, PromptProjection):
        _invalid()
    value = json.loads(canonical_json_bytes(projection.canonical_input))
    if CATALOG_KEY in value:
        _invalid()
    return value


def fact_catalog(projection):
    value = _input(projection)
    entries = []

    def add(pointer, item):
        if item is None:
            return
        if type(item) not in (str, int, bool, float):
            _invalid()
        if len(entries) >= 64:
            _invalid()
        entries.append({'id': f'f{len(entries):03}', 'pointer': pointer})

    try:
        current = value['grounding']['current']
        for key in ('day', 'phase'):
            if key in current:
                add('/grounding/current/'+key, current[key])
        for i, player in enumerate(current.get('players', [])):
            prefix = f'/grounding/current/players/{i}'
            if 'alive' in player:
                add(prefix+'/alive', player['alive'])
            death = player.get('death')
            if death is not None:
                for key in ('day', 'public_cause'):
                    if key in death:
                        add(prefix+'/death/'+key, death[key])
        for key in ('alive_player_ids', 'vote_candidate_player_ids'):
            for i, item in enumerate(current.get(key, [])):
                add(f'/grounding/current/{key}/{i}', item)
        for i, record in enumerate(value['grounding']['ability_results']['records']):
            for key in ('event_type', 'target_player_id', 'result_id', 'revealed_role_id'):
                if key in record:
                    add(f'/grounding/ability_results/records/{i}/{key}', record[key])
    except (KeyError, TypeError, AttributeError):
        _invalid()
    # Resolve the exact canonical pointers; never copy a fact value into the catalog.
    for entry in entries:
        resolve_fact(value, entry['pointer'])
    return entries


def resolve_fact(value, pointer):
    try:
        node = value
        for part in pointer.split('/')[1:]:
            node = node[int(part)] if isinstance(node, list) else node[part]
        if node is None or type(node) not in (str, int, bool, float):
            _invalid()
        return node
    except (ValueError, KeyError, IndexError, TypeError):
        _invalid()


def augmented_input(projection):
    value = _input(projection)
    value[CATALOG_KEY] = fact_catalog(projection)
    return value


def validate_augmented(projection, value):
    if canonical_json_bytes(value) != canonical_json_bytes(augmented_input(projection)):
        _invalid()


def augmented_body(baseline, projection):
    body = sc2._baseline(baseline)
    original = _input(projection)
    if body['messages'][1]['content'] != canonical_json_bytes(original).decode('utf-8'):
        raise ValueError('BASELINE_USER_MISMATCH')
    body['messages'][1]['content'] = canonical_json_bytes(augmented_input(projection)).decode('utf-8')
    return body


def choice_schema(projection):
    ids = [item['id'] for item in fact_catalog(projection)]
    array = {'type': 'array', 'items': {'type': 'string'},
             'maxItems': 2 if ids else 0, 'uniqueItems': True}
    if ids:
        array['items']['enum'] = ids
    kinds = ic2.choice_schema(projection.decision_schema)['properties']['speech_act_kind']
    return {'type': 'object', 'properties': {
        'speech_act_kind': deepcopy(kinds), 'authoritative_fact_ids': array},
        'required': ['speech_act_kind', 'authoritative_fact_ids'], 'additionalProperties': False}


def validate_choice(raw, projection):
    value = strict_json(raw)
    if not Draft202012Validator(choice_schema(projection)).is_valid(value):
        raise ValueError('CHOICE_SCHEMA_INVALID')
    return value


def _choice(value, projection):
    return validate_choice(canonical_json_bytes(value).decode('utf-8'), projection)


def choice_body(baseline, projection):
    body = augmented_body(baseline, projection)
    body['messages'][0]['content'] += '\n\n'+CHOICE_INSTRUCTION
    body['response_format']['json_schema']['schema'] = choice_schema(projection)
    body['max_tokens'] = CHOICE_TOKENS
    return body


def output_body(baseline, choice, projection):
    value = _choice(choice, projection)
    body = augmented_body(baseline, projection)
    body['messages'][0]['content'] += '\n\n'+OUTPUT_INSTRUCTION+'\n'+canonical_json_bytes(value).decode('utf-8')
    body['response_format']['json_schema']['schema'] = gc2.candidate_schema(
        projection, {'speech_act_kind': value['speech_act_kind']})
    body['max_tokens'] = OUTPUT_TOKENS
    return body


def validate_final(raw, choice, projection):
    value = _choice(choice, projection)
    return gc2.validate_final(raw, {'speech_act_kind': value['speech_act_kind']}, projection)


def catalog_identity(projection):
    original = _input(projection)
    augmented = augmented_input(projection)
    digest = lambda item: hashlib.sha256(canonical_json_bytes(item)).hexdigest()
    resolved = [{'id': item['id'], 'pointer': item['pointer'],
                 'value': resolve_fact(original, item['pointer'])} for item in augmented[CATALOG_KEY]]
    return {'source_projection_sha256': projection.prompt_sha256,
        'canonical_input_sha256': digest(original), 'augmented_input_sha256': digest(augmented),
        'catalog_sha256': digest(augmented[CATALOG_KEY]), 'resolved_catalog_sha256': digest(resolved),
        'catalog_count': len(resolved)}


def validate_native_rendered(body, stage_name, rendered, *, budget_variant='legacy'):
    value = sc2._baseline(body)
    if stage_name not in ('choice', 'output') or not isinstance(rendered, str):
        raise ValueError('NATIVE_RENDERED_INVALID')
    instruction = CHOICE_INSTRUCTION if stage_name == 'choice' else OUTPUT_INSTRUCTION
    system, user = (item['content'] for item in value['messages'])
    if stage_name == 'choice':
        suffix = '\n\n'+instruction
    else:
        marker = '\n\n'+instruction+'\n'
        raw = system.rsplit(marker, 1)[-1]
        plan = strict_json(raw)
        catalog = strict_json(user)[CATALOG_KEY]
        ids = [item['id'] for item in catalog]
        if (set(plan) != {'speech_act_kind', 'authoritative_fact_ids'}
                or plan['speech_act_kind'] not in ic2._KINDS
                or not isinstance(plan['authoritative_fact_ids'], list)
                or any(type(item) is not str or item not in ids for item in plan['authoritative_fact_ids'])
                or len(plan['authoritative_fact_ids']) > 2
                or len(set(plan['authoritative_fact_ids'])) != len(plan['authoritative_fact_ids'])):
            raise ValueError('NATIVE_BODY_INVALID')
        suffix = marker+canonical_json_bytes(plan).decode('utf-8')
    if budget_variant not in ('legacy', 'choice64_output448'):
        raise ValueError('NATIVE_BODY_INVALID')
    budgets = (CHOICE_TOKENS, OUTPUT_TOKENS) if budget_variant == 'legacy' else (64, 448)
    expected = budgets[0 if stage_name == 'choice' else 1]
    if value.get('max_tokens') != expected or not system.endswith(suffix) or system.count(instruction) != 1:
        raise ValueError('NATIVE_BODY_INVALID')
    encoded = rendered.encode('utf-8')
    if any(encoded.count(item.encode('utf-8')) != 1 for item in (system, user, instruction)):
        raise ValueError('NATIVE_RENDERED_INVALID')
