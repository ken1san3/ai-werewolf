"""T512 offline preflight. No provider, process control, or product-v2 execution."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPEC = ROOT / 'tests/fixtures/phase6_harness_v2_contract.json'
SPEC_SHA = '73f53b1f07624736081a618d32a6cce23dabb4793a4da60fd5f45c6b5311c790'  # Filled from the reviewed closed fixture; never learned at runtime.
SCHEMAS = ROOT / 'Docs/ai/design/PHASE6_GENERATION_CONTRACT_V2_SCHEMAS.json'
SCHEMAS_SHA = '8d3c4be2e2c3c1565afcdbaa73c93a65252f39919a05ae59307b4dbda2b206d5'
BASE = ROOT / 'logs/phase6-private-evidence'
LOCATORS = ROOT / 'logs/t512-harness-locators'
CASE_IDS = tuple(f'G{i:02}-{j}' for i in range(1, 17) for j in (1, 2))
SEEDS = (4242027, 4242028, 4242029)
PROFILES = ('PRODUCT_V1', 'I1', 'P2_PLAN', 'MINIMAL', 'T510_PLAN',
            'K1_KIND_FIRST', 'WP2_CHAT_PLAN_FIXTURE', 'GB1', 'T506_CHOICE32')
HISTORICAL_SHA = dict(zip(PROFILES[:6], (
    'f91e9b53902f3be4a972f01f329b1064d80e98146583cbd2b43bd11a510ae45e',
    '10c77f8b52a471eeae814fed47a71c58cd7a8d16d0618ae177ad3fa7e7f52b11',
    '0589603f8d927a6bd43a9f01897d464cc2dd10cf9ee6ffc67ac0fc1fcc7dad68',
    '88c1cc6c46c6b9fefbbad8e1915e1864db93b6372e60712bb4403b890df7bff5',
    'c4d5537f9d8c47abcfa339b6bc22026c902422d34fae609e578e392e711b2ff0',
    'fe09db0d2459f0a7ffd83b54229b7caa5c1d89cf5081f91ca560ea4b6e508819')))
KINDS = ('NONE', 'CLAIM', 'QUESTION', 'ANSWER', 'REBUTTAL', 'OPINION_CHANGE', 'RELATION_HYPOTHESIS')
SAVED_RESULT = 'logs/t507-choice-budget/formal-binary-v2/result.json'
SAVED_FREEZE = 'logs/t507-choice-budget/formal-binary-v2/freeze.json'
SAVED_SHA = ('79e8bb4aca7e48a1cd523258f0ced6b7c48b043464a4b1f6b2452a69ba11c511',
             '1279d36e20954deadf3d7e0996024e0162f0effbbe178aa4dd52a6526ead9bc5')
WITNESS_SHA = 'f3a1627db619a9e3c36c5da4fc6aaa5dbe5ea9c3c054a2613f5bfe0e41234b4d'
CONFIG = Path('C:/AIagent/agent/config.toml')
PRIVATE_HELPERS = {
    'tests/fixtures/phase6_evidence.py': (4301, '57222b0b37d6a63510c9b0e46a6d6c69e0db2c7ede44c61a527f51085cdff6fc'),
    'scripts/phase6_private_review.py': (36923, '6f5c803686e207eabef1174c33286bf33d713dd596b58d7ae093cb9eee929963'),
}


class IntegrityError(ValueError):
    """Only closed codes cross the public boundary."""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise IntegrityError('JSON_DUPLICATE_KEY')
            result[key] = value
        return result
    def bad(_):
        raise IntegrityError('JSON_NONFINITE')
    try:
        value = json.loads(raw.decode('utf-8') if isinstance(raw, bytes) else raw,
                           object_pairs_hook=pairs, parse_constant=bad)
        def finite(node):
            if isinstance(node, float) and not math.isfinite(node):
                raise IntegrityError('JSON_NONFINITE')
            if isinstance(node, dict):
                for child in node.values(): finite(child)
            elif isinstance(node, list):
                for child in node: finite(child)
        finite(value)
        return value
    except (UnicodeError, json.JSONDecodeError):
        raise IntegrityError('JSON_INVALID') from None


def pointer(value, path):
    if path == '':
        return value
    if not isinstance(path, str) or not path.startswith('/'):
        raise IntegrityError('POINTER_INVALID')
    try:
        for part in path[1:].split('/'):
            if re.search(r'~(?![01])', part):
                raise IntegrityError('POINTER_INVALID')
            key = part.replace('~1', '/').replace('~0', '~')
            value = value[int(key)] if isinstance(value, list) and re.fullmatch(r'0|[1-9][0-9]*', key) else value[key]
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        raise IntegrityError('POINTER_INVALID') from None


def result(status, reason, **extra):
    return dict(status=status, reason=reason, **extra)


def aggregate_preflight(*groups):
    rows = [row for group in groups for row in (group if isinstance(group, list) else [group])]
    if any(row.get('status') == 'FAIL' for row in rows):
        return 'FAIL'
    return 'PASS' if rows and all(row.get('status') == 'PASS' for row in rows) else 'UNKNOWN'


def pf1_target(profile):
    if profile == 'WP2_CHAT_PLAN_FIXTURE':
        return dict(schema_pointer='/schemas/chat_plan', reachable_routes=[''], leading_selector='reply_to',
                    act_field='act', profile=profile, expected_sha256=SCHEMAS_SHA, branch_count=8)
    if profile not in HISTORICAL_SHA:
        raise IntegrityError('PROFILE_INVALID')
    if profile == 'I1':
        routes = [f'/oneOf/{i}/properties/intent/properties/discussion/properties/speech_act' for i in range(2)]
    elif profile == 'MINIMAL':
        routes = ['/properties/speech_act']
    else:
        routes = [f'/properties/discussion/oneOf/{i}/properties/speech_act' for i in range(2)]
    return dict(schema_pointer='/response_format/json_schema/schema', reachable_routes=routes,
                leading_selector='kind', act_field='kind', profile=profile,
                expected_sha256=HISTORICAL_SHA[profile], branch_count=7)


def analyze_pf1(request_bytes, target):
    """Resolve all reachable speech-act routes without treating definitions as roots."""
    unknown = lambda why: result('UNKNOWN', why, rows=[], partition={})
    try:
        document = strict_json(request_bytes)
    except IntegrityError:
        return result('FAIL', 'REQUEST_JSON_INVALID', rows=[], partition={})
    if digest(request_bytes) != target['expected_sha256']:
        return unknown('REQUEST_HASH_DRIFT')
    try:
        schema = pointer(document, target['schema_pointer'])
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            return unknown('UNKNOWN_UNSUPPORTED_SCHEMA')
        forbidden = {'allOf', 'if', 'then', 'else', 'patternProperties', 'dependentSchemas',
                     '$dynamicRef', '$recursiveRef', 'discriminator'}
        supported = {'$schema','$defs','$ref','type','properties','required','additionalProperties',
                     'const','enum','oneOf','anyOf','items','minItems','maxItems','uniqueItems',
                     'minLength','maxLength','minimum','maximum','description','title'}
        found, visits = [], []

        def resolve(node, scope, seen):
            if not isinstance(node, dict):
                raise IntegrityError('UNKNOWN_UNSUPPORTED_SCHEMA')
            if '$defs' in node:
                scope = node
            while '$ref' in node:
                ref = node['$ref']
                identity = (id(scope), ref)
                if set(node) != {'$ref'} or not isinstance(ref, str) or not ref.startswith('#/') or identity in seen:
                    raise IntegrityError('UNKNOWN_UNSUPPORTED_SCHEMA')
                seen = seen | {identity}
                node = pointer(scope, ref[1:])
                if '$defs' in node:
                    scope = node
            if forbidden.intersection(node):
                raise IntegrityError('UNKNOWN_UNSUPPORTED_SCHEMA')
            if set(node) - supported:
                raise IntegrityError('UNKNOWN_UNSUPPORTED_SCHEMA')
            return node, scope, seen

        def walk(node, scope, path, seen):
            node, scope, seen = resolve(node, scope, seen)
            visits.append((id(node), id(scope), path))
            # Only the designated semantic field is a target. Still inspect all reachable schemas.
            if path.endswith('/properties/speech_act') or (path == '' and target['profile'] == 'WP2_CHAT_PLAN_FIXTURE'):
                found.append((path, node, scope))
            for key, child in node.get('properties', {}).items():
                walk(child, scope, path + '/properties/' + key, seen)
            for key in ('oneOf', 'anyOf'):
                for i, child in enumerate(node.get(key, [])):
                    walk(child, scope, path + f'/{key}/{i}', seen)
            if isinstance(node.get('items'), dict):
                walk(node['items'], scope, path + '/items', seen)

        walk(schema, schema, '', frozenset())
        if [path for path, _, _ in found] != target['reachable_routes']:
            return unknown('REACHABLE_ROUTE_DRIFT')
        target_identities = {(id(node), id(scope)) for _, node, scope in found}
        actual_routes = [path for identity, scope_id, path in visits if (identity, scope_id) in target_identities]
        if actual_routes != target['reachable_routes']:
            return unknown('REACHABLE_ROUTE_DRIFT')

        def values(node, scope):
            node, scope, _ = resolve(node, scope, frozenset())
            if len(set(node).intersection({'const','enum','oneOf','anyOf'})) > 1:
                raise IntegrityError('UNKNOWN_UNSUPPORTED_SCHEMA')
            if 'const' in node:
                if not Draft202012Validator(node).is_valid(node['const']):
                    raise IntegrityError('UNKNOWN_UNSUPPORTED_SCHEMA')
                return [node['const']]
            if 'enum' in node:
                if not node['enum'] or any(not Draft202012Validator(node).is_valid(value) for value in node['enum']):
                    raise IntegrityError('UNKNOWN_UNSUPPORTED_SCHEMA')
                return node['enum']
            if node.get('type') == 'null':
                return [None]
            # Non-selector first fields may have large values. Keep a structural descriptor.
            if 'type' in node:
                return [{'type': node['type']}]
            raise IntegrityError('UNKNOWN_UNSUPPORTED_SCHEMA')

        rows, partition = [], {}
        for route, node, scope in found:
            branches = node.get('oneOf')
            if not isinstance(branches, list) or len(branches) != target['branch_count']:
                return unknown('BRANCH_COUNT_DRIFT')
            for index, branch in enumerate(branches):
                branch, branch_scope, _ = resolve(branch, scope, frozenset())
                props = branch.get('properties', {})
                required = branch.get('required')
                if (branch.get('type') != 'object' or not isinstance(props, dict) or not props or
                    not isinstance(required, list) or any(not isinstance(k, str) for k in required) or
                    len(required) != len(set(required)) or branch.get('additionalProperties') is not False or set(required) != set(props)):
                    return unknown('UNKNOWN_UNSUPPORTED_SCHEMA')
                keys = list(props)
                selector, act = target['leading_selector'], target['act_field']
                if not keys or selector not in props or act not in props:
                    return unknown('SELECTOR_MISSING')
                acts = values(props[act], branch_scope)
                if not acts or any(x not in KINDS for x in acts):
                    return unknown('ACT_SET_UNKNOWN')
                lead_values = values(props[keys[0]], branch_scope)
                selector_values = values(props[selector], branch_scope)
                row = dict(route=route, branch_path=route + f'/oneOf/{index}', leading_key=keys[0],
                           leading_values=lead_values, leading_selector=selector, act_field=act,
                           selector_position=keys.index(selector), act_position=keys.index(act),
                           selector_values=selector_values, reachable_acts=acts, support='SUPPORTED')
                rows.append(row)
                for value in lead_values:
                    key = keys[0] + ':' + canonical(value).decode('utf-8')
                    partition.setdefault(key, set()).update(acts)
        partition = {key: sorted(value) for key, value in partition.items()}
        if target['profile'] == 'WP2_CHAT_PLAN_FIXTURE':
            expected = {'reply_to:"r000"': ['ANSWER', 'CLAIM', 'OPINION_CHANGE', 'QUESTION', 'REBUTTAL'],
                        'reply_to:"r001"': ['ANSWER', 'CLAIM', 'OPINION_CHANGE', 'QUESTION', 'REBUTTAL'],
                        'reply_to:null': ['CLAIM', 'NONE', 'OPINION_CHANGE', 'QUESTION']}
            ok = partition == expected and all(x['selector_position'] == 0 and x['act_position'] == 1 for x in rows)
        else:
            expected = {'kind:' + json.dumps(kind): [kind] for kind in KINDS}
            ok = partition == expected and all(x['selector_position'] == 0 for x in rows)
        return result('PASS' if ok else 'FAIL', 'PASS_DESIGN_FIXTURE' if ok and target['profile'] == 'WP2_CHAT_PLAN_FIXTURE'
                      else 'SELECTOR_SPACE_CLEAR' if ok else 'FIRST_KEY_PARTITION_TRAP', rows=rows, partition=partition)
    except (IntegrityError, KeyError, TypeError, RecursionError):
        return unknown('UNKNOWN_UNSUPPORTED_SCHEMA')


def expectation_key(case_id):
    group = int(case_id[1:3])
    if group in (1, 2, 7, 16):
        return 'PF2_TRIGGER_REPLY_' + case_id
    if group == 3:
        return 'PF2_QUESTION_SUBJECT_' + case_id
    return {'G04-1': 'PF2_OPINION_BASIS_G04_1', 'G04-2': 'PF2_ALTERNATIVES_G04_2'}.get(
        case_id, 'PF2_NONE_NO_CANDIDATE' if group == 14 else 'PF2_NO_EXPECTED_ACT')


def expected_acts(case_id):
    group = int(case_id[1:3])
    if group in (1, 16): return ['ANSWER']
    if group == 2: return ['REBUTTAL']
    if group == 7: return ['ANSWER', 'REBUTTAL']
    if group == 3: return ['QUESTION']
    if case_id == 'G04-1': return ['OPINION_CHANGE']
    if case_id == 'G04-2': return ['CLAIM', 'NONE', 'QUESTION']
    return ['NONE'] if group == 14 else []


def expectation(case_id, profile):
    acts = expected_acts(case_id)
    alternatives = []
    for act in acts:
        selector = {'ANSWER': ('REPLY', 'TRIGGER_SOURCE'), 'REBUTTAL': ('REPLY', 'TRIGGER_SOURCE'),
                    'QUESTION': ('PLAYER', 'CANONICAL_PLAYER'), 'OPINION_CHANGE': ('OPINION', 'CANONICAL_OPINION'),
                    'CLAIM': ('CLAIM', 'CANONICAL_CLAIM')}.get(act)
        alternatives.append(dict(act=act, required=[] if selector is None else [
            dict(inventory_kind=selector[0], selector=selector[1])]))
    return dict(version='phase6-harness-v2.expectation.v1', case_id=case_id, profile=profile,
                expected_acts=acts, alternatives=alternatives)


ITEM_KEYS = {'id', 'inventory_kind', 'projection_sha256', 'actor_player_id', 'output_channel_id',
             'source_ref', 'source_pointer', 'source_kind', 'read_authority', 'disclosure_eligibility', 'binding_sha256'}


def bind_item(value, kind, id_, path, source_kind, channel, ref=None, *, allowed=True):
    item = dict(id=id_, inventory_kind=kind, projection_sha256=digest(canonical(value)),
                actor_player_id=value['context']['player_id'], output_channel_id=channel,
                source_ref=ref, source_pointer=path, source_kind=source_kind,
                read_authority='AUTHORIZED' if allowed else 'UNKNOWN', disclosure_eligibility='PUBLIC' if allowed else 'UNKNOWN')
    item['binding_sha256'] = digest(canonical(item))
    return item


def inventory_for(value, profile, facts):
    channels = [c['channel_id'] for c in value['context']['chat_channels'] if c.get('is_public') is True]
    channel = channels[0] if len(channels) == 1 else None
    items = []
    for fact in facts:
        # GB1's fact pointers are not a reply catalog; ability facts need a separate proven disclosure lane.
        public = fact['pointer'].startswith('/grounding/current/') and channel is not None
        items.append(bind_item(value, 'FACT', fact['id'], fact['pointer'], 'AUTHORITATIVE_FACT', channel, allowed=public))
    if profile == 'PRODUCT_V1':
        for i, record in enumerate(value['memory']['records']):
            ref = record['source']
            if ref.get('record_kind') == 'chat' and ref.get('visibility') == 'PUBLIC' and record['channel_id'] == channel and record['day'] == value['grounding']['current']['day']:
                if ref in value['grounding']['allowed_evidence_refs']:
                    items.append(bind_item(value, 'REPLY', f'r{i:03}', f'/memory/records/{i}', 'TRIGGER_CHAT' if ref == value['capture']['trigger']['source'] else 'RECENT_CHAT', channel, ref))
        for i, player in enumerate(value['grounding']['current']['players']):
            items.append(bind_item(value, 'PLAYER', f'p{i:03}', f'/grounding/current/players/{i}', 'CANONICAL_PLAYER', channel))
        for i, assessment in enumerate(value['state']['assessments']):
            items.append(bind_item(value, 'OPINION', f'u{i:03}', f'/state/assessments/{i}', 'CANONICAL_OPINION', channel))
    return dict(canonical_input=value, projection_sha256=digest(canonical(value)),
                actor_player_id=value['context']['player_id'], output_channel_id=channel, items=items)


def check_pf2(expect, inventory):
    """Verify same-snapshot candidate bindings; never infer references from text."""
    base = dict(candidate_presence_authority_only=True, required_count=0, present_count=0)
    try:
        if not isinstance(expect, dict) or expect.get('case_id') not in CASE_IDS or expect.get('profile') not in ('PRODUCT_V1','GB1'):
            return result('UNKNOWN', 'EXPECTATION_DRIFT', **base)
        if expect != expectation(expect['case_id'], expect['profile']):
            return result('UNKNOWN', 'EXPECTATION_DRIFT', **base)
        value, items = inventory['canonical_input'], inventory['items']
        sha = digest(canonical(value))
        actor = value['context']['player_id']
        channel = inventory['output_channel_id']
        if sha != inventory['projection_sha256'] or actor != inventory['actor_player_id'] or channel not in [c['channel_id'] for c in value['context']['chat_channels'] if c.get('is_public') is True]:
            return result('UNKNOWN', 'PROJECTION_BINDING_DRIFT', **base)
        seen_ids, seen_paths = set(), set()
        trusted = []
        unresolved = False
        for item in items:
            if set(item) != ITEM_KEYS:
                return result('UNKNOWN', 'INVENTORY_SHAPE', **base)
            unhashed = {k: v for k, v in item.items() if k != 'binding_sha256'}
            if digest(canonical(unhashed)) != item['binding_sha256']:
                return result('UNKNOWN', 'INVENTORY_BINDING_DRIFT', **base)
            if item['id'] in seen_ids or item['source_pointer'] in seen_paths:
                return result('UNKNOWN', 'DUPLICATE_INVENTORY', **base)
            seen_ids.add(item['id']); seen_paths.add(item['source_pointer'])
            if (item['projection_sha256'], item['actor_player_id'], item['output_channel_id']) != (sha, actor, channel):
                return result('UNKNOWN', 'INVENTORY_BINDING_DRIFT', **base)
            node = pointer(value, item['source_pointer'])
            if item['read_authority'] == 'FORBIDDEN' or item['disclosure_eligibility'] == 'FORBIDDEN':
                return result('FAIL', 'FORBIDDEN_CANDIDATE', **base)
            leaf_contract = {'REPLY': ('r', None), 'FACT': ('f', 'AUTHORITATIVE_FACT'),
                             'PLAYER': ('p', 'CANONICAL_PLAYER'), 'OPINION': ('u', 'CANONICAL_OPINION')}
            kind, path = item['inventory_kind'], item['source_pointer']
            if kind not in leaf_contract:
                return result('UNKNOWN', 'AUTHORITY_UNPROVEN', **base)
            prefix, source_kind = leaf_contract[kind]
            if (not isinstance(item['id'], str) or re.fullmatch(prefix + '[0-9]{3}', item['id']) is None or
                (kind == 'FACT' and int(item['id'][1:]) >= 64) or
                (source_kind is not None and item['source_kind'] != source_kind) or
                (kind != 'REPLY' and item['source_ref'] is not None)):
                return result('UNKNOWN', 'INVENTORY_BINDING_DRIFT', **base)
            if item['read_authority'] != 'AUTHORIZED' or item['disclosure_eligibility'] != 'PUBLIC':
                unresolved = True
                continue
            if kind == 'REPLY':
                ref = item['source_ref']
                if not isinstance(ref, dict) or ref.get('visibility') != 'PUBLIC':
                    return result('FAIL', 'FORBIDDEN_CANDIDATE', **base)
                if not re.fullmatch(r'/memory/records/[0-9]+', path) or ref != node['source'] or ref not in value['grounding']['allowed_evidence_refs'] or node['channel_id'] != channel or node['day'] != value['grounding']['current']['day'] or ref.get('record_kind') != 'chat' or len(node['actor_player_ids']) != 1:
                    return result('UNKNOWN', 'REPLY_BINDING_DRIFT', **base)
                trigger = ref == value['capture']['trigger']['source']
                if item['source_kind'] != ('TRIGGER_CHAT' if trigger else 'RECENT_CHAT'):
                    return result('UNKNOWN', 'REPLY_BINDING_DRIFT', **base)
            elif kind == 'FACT':
                if not path.startswith('/grounding/current/') or type(node) not in (str, int, bool, float):
                    return result('UNKNOWN', 'AUTHORITY_UNPROVEN', **base)
            elif kind == 'PLAYER':
                if not re.fullmatch(r'/grounding/current/players/[0-9]+', path) or not isinstance(node, dict) or 'player_id' not in node or int(path.rsplit('/', 1)[1]) != int(item['id'][1:]):
                    return result('UNKNOWN', 'AUTHORITY_UNPROVEN', **base)
            elif kind == 'OPINION':
                if not re.fullmatch(r'/state/assessments/[0-9]+', path) or not isinstance(node, dict) or int(path.rsplit('/', 1)[1]) != int(item['id'][1:]):
                    return result('UNKNOWN', 'AUTHORITY_UNPROVEN', **base)
            else:
                unresolved = True
                continue
            trusted.append(item)
        if not expect['expected_acts']:
            return result('PASS', 'NOT_APPLICABLE', **base)
        for alternative in expect['alternatives']:
            matched = []
            for requirement in alternative['required']:
                found = [item for item in trusted if item['inventory_kind'] == requirement['inventory_kind'] and
                         (requirement['selector'] != 'TRIGGER_SOURCE' or item['source_kind'] == 'TRIGGER_CHAT')]
                matched.append(bool(found))
            base = dict(base, required_count=len(matched), present_count=sum(matched))
            if all(matched):
                return result('PASS', 'CANDIDATES_PRESENT', **base)
        return result('UNKNOWN' if unresolved else 'FAIL', 'AUTHORITY_UNPROVEN' if unresolved else
                      'MISSING_REPLY_CANDIDATE' if any(x in ('ANSWER', 'REBUTTAL') for x in expect['expected_acts']) else 'MISSING_CANDIDATE', **base)
    except (KeyError, TypeError, IntegrityError):
        return result('UNKNOWN', 'INVENTORY_SHAPE', **base)


def check_pf3(contract, schema, evidence):
    """Only exhaustive finite evidence can pass. A witness can disprove a budget."""
    from jsonschema import Draft202012Validator
    mode = contract.get('mode')
    base = dict(mode=mode, max_tokens=contract.get('max_tokens'), observed_tokens=None)
    if mode == 'UNBOUNDED_OR_UNPROVEN':
        return result('UNKNOWN', 'TOKEN_MAXIMUM_UNPROVEN', **base)
    try:
        if type(contract['max_tokens']) is not int or contract['max_tokens'] <= 0:
            raise IntegrityError('CONTRACT_INVALID')
        if evidence['identity'] != contract['identity'] or evidence['evidence_sha256'] != contract['evidence_sha256']:
            raise IntegrityError('TOKEN_IDENTITY_DRIFT')
        source_raw = evidence['source_bytes'].encode('utf-8')
        freeze_raw = evidence['freeze_bytes'].encode('utf-8')
        if digest(source_raw) != SAVED_SHA[0] or digest(freeze_raw) != SAVED_SHA[1] or contract['evidence_sha256'] != SAVED_SHA[0]:
            raise IntegrityError('TOKEN_IDENTITY_DRIFT')
        source = strict_json(source_raw)
        if source['provider_calls'] != 0 or source['inference_calls'] != 0:
            raise IntegrityError('TOKEN_IDENTITY_DRIFT')
        rows = evidence['rows']
        observed = []
        for row in rows:
            raw = row['raw'].encode('utf-8')
            if digest(raw) != row['raw_sha256'] or row['identity'] != contract['identity'] or type(row['tokens']) is not int or row['tokens'] < 0:
                raise IntegrityError('TOKEN_IDENTITY_DRIFT')
            Draft202012Validator(schema).validate(strict_json(raw))
            matched = [r for r in source['rows'] if r['model'] == contract['identity']['model'] and r['raw_sha256'] == row['raw_sha256']]
            if len(matched) != 1 or matched[0]['status'] != 'KNOWN' or matched[0]['tokens'] != row['tokens'] or matched[0]['identity_sha256'] != contract['identity']['native_identity_sha256'] or contract['identity']['freeze_sha256'] != digest(freeze_raw):
                raise IntegrityError('TOKEN_IDENTITY_DRIFT')
            observed.append(row['tokens'])
        if not observed:
            raise IntegrityError('TOKEN_EVIDENCE_MISSING')
        base['observed_tokens'] = max(observed)
        if mode == 'LEGAL_COUNTEREXAMPLE':
            return result('FAIL' if max(observed) > contract['max_tokens'] else 'UNKNOWN',
                          'FAIL_BUDGET' if max(observed) > contract['max_tokens'] else 'TOKEN_MAXIMUM_UNPROVEN', **base)
        # Exhaustive proof includes exact *wire bytes*, not merely equivalent JSON values.
        # JSON Schema permits arbitrary whitespace even for enum-only values. No serializer
        # is provider-enforced in T512; therefore this mode cannot be certified here.
        return result('UNKNOWN', 'UNKNOWN_EXHAUSTIVE_WIRE_ENUMERATOR_UNAVAILABLE', **base)
    except Exception:
        return result('UNKNOWN', 'TOKEN_EVIDENCE_INVALID', **base)


@dataclass(frozen=True)
class LeaseIdentity:
    host_id: str
    pid: int
    creation_time_ns: int
    executable_sha256: str
    nonce128: str
    manifest_sha256: str


@dataclass(frozen=True)
class ProcessObservation:
    status: str
    host_id: str | None
    pid: int | None
    creation_time_ns: int | None
    executable_sha256: str | None
    alive: bool | None


@dataclass(frozen=True)
class ListenerObservation:
    status: str
    endpoint: str | None
    listener_count: int | None
    owner_pid: int | None
    owner_creation_time_ns: int | None
    owner_executable_sha256: str | None


@dataclass(frozen=True)
class TransportObservation:
    event: str
    connection_id: str | None
    reconnect_count: int | None
    owner_pid: int | None
    owner_creation_time_ns: int | None
    response_complete: bool | None


@dataclass(frozen=True)
class TransportState:
    state: str = 'INIT'
    reason: str = 'NOT_OBSERVED'
    connection_id: str | None = None
    endpoint: str | None = None
    client_creations: int = 0
    lease_identity: LeaseIdentity | None = None


def valid_lease(lease):
    return (isinstance(lease, LeaseIdentity) and isinstance(lease.host_id, str) and bool(lease.host_id) and
            type(lease.pid) is int and lease.pid > 0 and type(lease.creation_time_ns) is int and lease.creation_time_ns > 0 and
            all(isinstance(x, str) and re.fullmatch('[a-f0-9]{64}', x) is not None for x in (lease.executable_sha256, lease.manifest_sha256)) and
            isinstance(lease.nonce128, str) and re.fullmatch('[a-f0-9]{32}', lease.nonce128) is not None)


def valid_observations(process, listener):
    statuses = ('OBSERVED','MISSING','ACCESS_DENIED','AMBIGUOUS')
    return (isinstance(process, ProcessObservation) and isinstance(listener, ListenerObservation) and
            process.status in statuses and listener.status in statuses and
            (process.alive is None or type(process.alive) is bool) and
            all(x is None or type(x) is int and x > 0 for x in (process.pid,process.creation_time_ns,listener.owner_pid,listener.owner_creation_time_ns)) and
            (listener.listener_count is None or type(listener.listener_count) is int and listener.listener_count >= 0) and
            (process.host_id is None or isinstance(process.host_id,str) and bool(process.host_id)) and
            (listener.endpoint is None or isinstance(listener.endpoint,str) and bool(listener.endpoint)) and
            all(x is None or isinstance(x,str) and re.fullmatch('[a-f0-9]{64}',x) is not None for x in (process.executable_sha256,listener.owner_executable_sha256)))


def evaluate_owner(lease, process, listener):
    valid_sha = lambda x: isinstance(x, str) and re.fullmatch('[a-f0-9]{64}', x) is not None
    if not valid_lease(lease):
        return result('UNKNOWN', 'LEASE_IDENTITY_INVALID')
    if not valid_observations(process, listener):
        return result('UNKNOWN', 'OWNER_NOT_OBSERVED')
    if process.status != 'OBSERVED' or listener.status != 'OBSERVED' or any(x is None for x in (
        process.host_id, process.pid, process.creation_time_ns, process.executable_sha256, process.alive,
        listener.endpoint, listener.listener_count, listener.owner_pid, listener.owner_creation_time_ns, listener.owner_executable_sha256)):
        return result('UNKNOWN', 'OWNER_NOT_OBSERVED')
    if (any(type(x) is not int or x <= 0 for x in (process.pid, process.creation_time_ns, listener.owner_pid, listener.owner_creation_time_ns)) or
        type(process.alive) is not bool or type(listener.listener_count) is not int or listener.listener_count < 0 or
        not isinstance(listener.endpoint, str) or not listener.endpoint or not valid_sha(process.executable_sha256) or not valid_sha(listener.owner_executable_sha256)):
        return result('UNKNOWN', 'OWNER_NOT_OBSERVED')
    if (process.host_id, process.pid, process.creation_time_ns, process.executable_sha256) != (
        lease.host_id, lease.pid, lease.creation_time_ns, lease.executable_sha256):
        return result('FAIL', 'PID_REUSE_OR_OWNER_DRIFT')
    if process.alive is not True or listener.listener_count != 1 or (listener.owner_pid, listener.owner_creation_time_ns, listener.owner_executable_sha256) != (lease.pid, lease.creation_time_ns, lease.executable_sha256):
        return result('FAIL', 'LISTENER_OR_PROCESS_DRIFT')
    return result('PASS', 'OWNED')


def transition_transport(state, lease, process, listener, event):
    if (not valid_lease(lease) or not valid_observations(process,listener) or not isinstance(state, TransportState) or
        not isinstance(event, TransportObservation) or state.state not in ('INIT','READY','CONNECTED','STOP_REQUIRED','CLOSED','REJECTED','UNKNOWN') or
        type(state.client_creations) is not int or state.client_creations not in (0,1) or
        state.lease_identity is not None and not valid_lease(state.lease_identity) or
        event.event not in ('OPEN','RESPONSE','DISCONNECT','RECONNECT_ATTEMPT','CLOSE')):
        return TransportState('UNKNOWN', 'TRANSPORT_NOT_OBSERVED')
    if state.state in ('REJECTED', 'CLOSED', 'UNKNOWN'):
        return state
    if state.lease_identity is not None and state.lease_identity != lease:
        return replace(state, state='REJECTED', reason='LEASE_IDENTITY_DRIFT')
    if event.event == 'RECONNECT_ATTEMPT':
        return replace(state, state='REJECTED', reason='IMPLICIT_RECONNECT')
    if event.event == 'DISCONNECT':
        return replace(state, state='STOP_REQUIRED', reason='DISCONNECTED')
    if event.event == 'CLOSE':
        cleanup = evaluate_cleanup(lease, process, listener)
        return replace(state, state='CLOSED' if cleanup['status'] == 'PASS' else 'UNKNOWN', reason=cleanup['reason'])
    if state.state == 'STOP_REQUIRED':
        return replace(state, state='REJECTED', reason='REOPEN_FORBIDDEN')
    owner = evaluate_owner(lease, process, listener)
    if owner['status'] != 'PASS':
        return replace(state, state='UNKNOWN' if owner['status'] == 'UNKNOWN' else 'STOP_REQUIRED', reason=owner['reason'])
    if state.endpoint is not None and state.endpoint != listener.endpoint:
        return replace(state, state='STOP_REQUIRED', reason='ENDPOINT_DRIFT')
    if any(x is None for x in (event.connection_id, event.reconnect_count, event.owner_pid, event.owner_creation_time_ns, event.response_complete)):
        return replace(state, state='UNKNOWN', reason='TRANSPORT_NOT_OBSERVED')
    if not isinstance(event.connection_id, str) or not event.connection_id or type(event.reconnect_count) is not int or event.reconnect_count < 0 or type(event.owner_pid) is not int or type(event.owner_creation_time_ns) is not int or type(event.response_complete) is not bool:
        return replace(state, state='UNKNOWN', reason='TRANSPORT_NOT_OBSERVED')
    if event.reconnect_count != 0 or (event.owner_pid, event.owner_creation_time_ns) != (lease.pid, lease.creation_time_ns):
        return replace(state, state='REJECTED', reason='IMPLICIT_RECONNECT')
    if event.event == 'OPEN' and state.state in ('INIT', 'READY') and state.client_creations == 0:
        return TransportState('CONNECTED', 'OPEN', event.connection_id, listener.endpoint, 1, lease)
    if event.event == 'RESPONSE' and state.state == 'CONNECTED' and event.connection_id == state.connection_id and event.response_complete is True:
        return state
    return replace(state, state='REJECTED', reason='IMPLICIT_RECONNECT' if event.event in ('OPEN', 'RESPONSE') else 'TRANSPORT_EVENT_INVALID')


def evaluate_cleanup(lease, process_after, listener_after):
    if not valid_lease(lease) or not valid_observations(process_after,listener_after):
        return result('UNKNOWN', 'CLEANUP_NOT_CONFIRMED')
    if (process_after.host_id, process_after.pid, process_after.creation_time_ns, process_after.executable_sha256) != (lease.host_id, lease.pid, lease.creation_time_ns, lease.executable_sha256):
        return result('UNKNOWN', 'CLEANUP_NOT_CONFIRMED')
    if process_after.status == 'MISSING' and process_after.alive is False and listener_after.status == 'OBSERVED' and type(listener_after.listener_count) is int and listener_after.listener_count == 0 and isinstance(listener_after.endpoint, str) and listener_after.endpoint:
        return result('PASS', 'ABSENCE_OBSERVED')
    return result('UNKNOWN' if process_after.status in ('ACCESS_DENIED', 'AMBIGUOUS') or listener_after.status != 'OBSERVED' else 'FAIL', 'CLEANUP_NOT_CONFIRMED')


def expected_spec():
    return dict(version='phase6-harness-v2.prepare-spec.v1', suite_id='phase6-synthetic-32-v1',
                case_ids=list(CASE_IDS), seeds=list(SEEDS), profiles=list(PROFILES),
                pf1_targets=[dict(profile=x, registry_key='PF1_' + x + '_G01') for x in PROFILES[:7]],
                pf2_expectations=[dict(case_id=c, profile=p, registry_key=expectation_key(c)) for p in ('PRODUCT_V1', 'GB1') for c in CASE_IDS])


def validate_spec(raw):
    spec = strict_json(raw)
    if digest(raw) != SPEC_SHA or spec != expected_spec():
        raise IntegrityError('SPEC_INVALID')
    return spec


def build_artifacts(spec):
    """Closed pure registry. Each eligible projection is built exactly once."""
    from scripts import phase6_recovery_runner as r
    from scripts import phase6_intent_first_probe as i1, phase6_two_call_probe as p2
    from scripts import phase6_minimal_suite_adapter as minimal, phase6_local_staged_probe as staged
    from scripts import phase6_kind_first_probe as k1, phase6_choice_budget_probe as budget
    from scripts.phase6_context_probe import wire_bytes
    cases = r.base.cases()
    if tuple(c.case_id for c in cases) != CASE_IDS or any(list(c.expected_acts) != expected_acts(c.case_id) for c in cases):
        raise IntegrityError('CASE_METADATA_DRIFT')
    artifacts = []
    first = None
    for profile in ('PRODUCT_V1', 'GB1'):
        for case in cases:
            for seed in SEEDS:
                p = r.base.project(case, 'baseline')
                value = strict_json(r.canonical_json_bytes(p.canonical_input))
                b = r.baseline_body(p, 'qw9', seed)
                facts = r.gb.fact_catalog(p)
                body = b if profile == 'PRODUCT_V1' else r.gb.choice_body(b, p)
                artifacts.append(dict(profile=profile, case_id=case.case_id, seed=seed,
                                      request=wire_bytes(body).decode('utf-8'), input=value,
                                      inventory=inventory_for(value, profile, facts)))
                if profile == 'PRODUCT_V1' and case.case_id == 'G01-1' and seed == SEEDS[0]:
                    first = (case, p, b)
    case, p, b = first
    extra = [('I1', i1.intent_first_wire_bytes(i1.candidate_body(b))),
             ('P2_PLAN', wire_bytes(p2.plan_body(b))),
             ('MINIMAL', minimal.candidate_wire(minimal.candidate_body(minimal.bind_case(case, p), 'Qwen3.5-9B-Q4_K_M.gguf'))),
             ('T510_PLAN', wire_bytes(staged.plan_body(b, p))),
             ('K1_KIND_FIRST', k1.kind_first_wire_bytes(b))]
    for profile, raw in extra:
        artifacts.append(dict(profile=profile, case_id=case.case_id, seed=SEEDS[0], request=raw.decode('utf-8')))
    schema_bytes = SCHEMAS.read_bytes()
    if digest(schema_bytes) != SCHEMAS_SHA:
        raise IntegrityError('SCHEMA_FIXTURE_DRIFT')
    artifacts.append(dict(profile='WP2_CHAT_PLAN_FIXTURE', case_id=None, seed=None, request=schema_bytes.decode('utf-8')))
    saved = [(ROOT / path).read_bytes() for path in (SAVED_RESULT, SAVED_FREEZE)]
    if tuple(map(digest, saved)) != SAVED_SHA:
        raise IntegrityError('SAVED_TOKEN_EVIDENCE_DRIFT')
    result_data, freeze = map(strict_json, saved)
    rows = result_data.get('rows', [])
    row = next(x for x in rows if x['model'] == 'gm12' and x['raw_sha256'] == WITNESS_SHA)
    witness = next(x for x in budget.fixtures() if x['raw_sha256'] == WITNESS_SHA)
    if row['status'] != 'KNOWN' or row['tokens'] != 37 or result_data['provider_calls'] != 0 or result_data['inference_calls'] != 0:
        raise IntegrityError('SAVED_TOKEN_EVIDENCE_DRIFT')
    identity = dict(model='gm12', native_identity_sha256=row['identity_sha256'], freeze_sha256=SAVED_SHA[1])
    artifacts.append(dict(profile='T506_CHOICE32', case_id=case.case_id, seed=SEEDS[0],
                         schema=r.gb.choice_schema(p), contract=dict(stage_id='T506_CHOICE32', mode='LEGAL_COUNTEREXAMPLE',
                         max_tokens=32, identity=identity, evidence_sha256=SAVED_SHA[0]),
                         evidence=dict(identity=identity, evidence_sha256=SAVED_SHA[0], source_bytes=saved[0].decode('utf-8'),
                         freeze_bytes=saved[1].decode('utf-8'), rows=[dict(
                         raw=witness['raw'], raw_sha256=WITNESS_SHA, tokens=37, identity=identity)])))
    return artifacts


def plain_path(path, *, missing_leaf=False):
    absolute = Path(path).absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        if part in ('.', '..') or ':' in part or part.endswith((' ', '.')):
            raise IntegrityError('PATH_INVALID')
        current /= part
        try:
            stat = os.lstat(current)
        except FileNotFoundError:
            if missing_leaf and current == absolute:
                break
            raise IntegrityError('PATH_MISSING') from None
        if current.is_symlink() or getattr(stat, 'st_file_attributes', 0) & 0x400:
            raise IntegrityError('REPARSE_PATH')
    return absolute.resolve(strict=not missing_leaf)


def acl_observation(path):
    try:
        verify_private_helpers()
        from scripts.phase6_private_review import _windows_private_path
        return os.name == 'nt' and _windows_private_path(Path(path))
    except Exception:
        return False


def verify_private_helpers():
    for relative, expected in PRIVATE_HELPERS.items():
        raw = plain_path(ROOT / relative).read_bytes()
        if (len(raw), digest(raw)) != expected:
            raise IntegrityError('PRIVATE_HELPER_SOURCE_DRIFT')


def write_exclusive(path, raw):
    path = Path(path)
    plain_path(path, missing_leaf=True)
    if path.exists():
        raise IntegrityError('EXISTING_OUTPUT')
    partial = path.with_name(path.name + '.partial')
    with partial.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    # Windows rename never replaces an existing destination. Use a hard-link on POSIX.
    if os.name == 'nt':
        partial.rename(path)
    else:
        os.link(partial, path); partial.unlink()


def freeze_sources(spec_sha):
    # Freeze before building as well as afterwards. Fixed roots cover lazy imports too.
    paths = {Path(__file__).resolve(), SPEC, SCHEMAS,
             ROOT / 'tests/fixtures/phase6_evidence.py', ROOT / 'scripts/phase6_private_review.py'}
    for directory in ('ai_client', 'server', 'protocol', 'scripts', 'tests', 'content', 'config'):
        base = ROOT / directory
        if base.exists():
            paths.update(p for p in base.rglob('*') if p.is_file() and p.suffix in ('.py', '.yaml', '.yml', '.json', '.toml'))
    files = []
    for path in sorted(paths):
        raw = plain_path(path).read_bytes()
        files.append(dict(path=path.relative_to(ROOT).as_posix(), size=len(raw), sha256=digest(raw)))
    return dict(version=1, input_spec_sha256=spec_sha, config_sha256=digest(plain_path(CONFIG).read_bytes()), files=files)


def prepare_cache(spec_bytes, task_id, private_factory=None, acl_observer=None):
    spec = validate_spec(spec_bytes)
    if not re.fullmatch('[A-Z][A-Z0-9_-]{8,31}', task_id):
        raise IntegrityError('TASK_ID_INVALID')
    verify_private_helpers()
    if private_factory is None:
        from tests.fixtures.phase6_evidence import create_private_evidence_container
        private_factory = create_private_evidence_container
    observer = acl_observer or acl_observation
    parent = BASE / 'synthetic'
    plain_path(BASE)
    if parent.exists() and (plain_path(parent).parent != BASE.resolve() or observer(parent) is not True):
        raise IntegrityError('PRIVATE_ACL_UNVERIFIED')
    cache = Path(private_factory(BASE, evidence_kind='synthetic', task_id=task_id, created_at_utc=datetime.now(timezone.utc)))
    if plain_path(cache).parent != plain_path(parent) or observer(parent) is not True or observer(cache) is not True:
        raise IntegrityError('PRIVATE_ACL_UNVERIFIED')
    if any(cache.iterdir()):
        raise IntegrityError('EXISTING_CACHE')
    freeze = freeze_sources(digest(spec_bytes))
    artifacts = build_artifacts(spec)
    if freeze_sources(digest(spec_bytes)) != freeze:
        raise IntegrityError('FROZEN_SOURCE_CHANGED')
    freeze_bytes = canonical(freeze)
    write_exclusive(cache / 'source.json', freeze_bytes)
    write_exclusive(cache / 'spec.json', spec_bytes)
    index = []
    for i, artifact in enumerate(artifacts):
        raw, name = canonical(artifact), f'a{i:03}.json'
        write_exclusive(cache / name, raw)
        index.append(dict(name=name, sha256=digest(raw), size=len(raw), profile=artifact['profile'],
                          case_id=artifact['case_id'], seed=artifact['seed']))
    if observer(cache) is not True:
        raise IntegrityError('PRIVATE_ACL_UNVERIFIED')
    manifest = dict(version=1, input_spec_sha256=digest(spec_bytes), source_freeze_sha256=digest(freeze_bytes), artifacts=index)
    raw = canonical(manifest)
    write_exclusive(cache / 'manifest.json', raw)
    if not re.fullmatch('[A-Za-z0-9_-]{32,55}', cache.name):
        raise IntegrityError('LOCATOR_INVALID')
    LOCATORS.mkdir(exist_ok=True)
    plain_path(LOCATORS)
    locator = dict(locator_id=cache.name, manifest_sha256=digest(raw), source_freeze_sha256=digest(freeze_bytes))
    write_exclusive(LOCATORS / (cache.name + '.json'), canonical(locator))
    return locator


def verify_cache(cache_dir, expected_spec_sha256):
    cache = plain_path(cache_dir)
    if cache.parent != plain_path(BASE / 'synthetic') or not acl_observation(cache.parent) or not acl_observation(cache):
        raise IntegrityError('PRIVATE_ACL_UNVERIFIED')
    manifest_raw = plain_path(cache / 'manifest.json').read_bytes()
    manifest = strict_json(manifest_raw)
    if set(manifest) != {'version', 'input_spec_sha256', 'source_freeze_sha256', 'artifacts'} or manifest['version'] != 1:
        raise IntegrityError('MANIFEST_SHAPE')
    spec_raw = plain_path(cache / 'spec.json').read_bytes()
    validate_spec(spec_raw)
    if expected_spec_sha256 != SPEC_SHA or manifest['input_spec_sha256'] != digest(spec_raw):
        raise IntegrityError('SPEC_DRIFT')
    freeze_raw = plain_path(cache / 'source.json').read_bytes()
    freeze = strict_json(freeze_raw)
    if digest(freeze_raw) != manifest['source_freeze_sha256'] or freeze['input_spec_sha256'] != expected_spec_sha256:
        raise IntegrityError('SOURCE_FREEZE_DRIFT')
    if freeze != freeze_sources(expected_spec_sha256):
        raise IntegrityError('FROZEN_SOURCE_CHANGED')
    for entry in freeze['files']:
        path = plain_path(ROOT / entry['path'])
        if ROOT not in path.parents or path.suffix not in ('.py', '.yaml', '.yml', '.json', '.toml'):
            raise IntegrityError('SOURCE_PATH_INVALID')
        raw = path.read_bytes()
        if len(raw) != entry['size'] or digest(raw) != entry['sha256']:
            raise IntegrityError('FROZEN_SOURCE_CHANGED')
    expected = [(p, c, s) for p in ('PRODUCT_V1', 'GB1') for c in CASE_IDS for s in SEEDS]
    expected += [(p, 'G01-1', SEEDS[0]) for p in PROFILES[1:6]]
    expected += [('WP2_CHAT_PLAN_FIXTURE', None, None), ('T506_CHOICE32', 'G01-1', SEEDS[0])]
    if [(a['profile'], a['case_id'], a['seed']) for a in manifest['artifacts']] != expected:
        raise IntegrityError('ARTIFACT_DOMAIN_DRIFT')
    artifacts = []
    for i, entry in enumerate(manifest['artifacts']):
        if entry['name'] != f'a{i:03}.json':
            raise IntegrityError('ARTIFACT_PATH_INVALID')
        raw = plain_path(cache / entry['name']).read_bytes()
        if len(raw) != entry['size'] or digest(raw) != entry['sha256']:
            raise IntegrityError('CACHE_ARTIFACT_DRIFT')
        artifact = strict_json(raw)
        if (artifact['profile'], artifact['case_id'], artifact['seed']) != expected[i]:
            raise IntegrityError('ARTIFACT_DOMAIN_DRIFT')
        artifacts.append(artifact)
    return dict(manifest_sha256=digest(manifest_raw), source_freeze_sha256=digest(freeze_raw), artifacts=artifacts)


REPORT_FIELDS = {'version', 'status', 'locator_id', 'manifest_sha256', 'source_freeze_sha256',
                 'provider_calls', 'inference_calls', 'pf1', 'pf2', 'pf3', 'pf4', 'errors', 'started_at_utc', 'ended_at_utc'}
GATE_FIELDS = dict(pf1={'fixture_id','status','reason','branch_count','leading_selector','act_field'},
                   pf2={'case_id','profile','status','reason','required_count','present_count'},
                   pf3={'stage_id','status','mode','max_tokens','observed_tokens','evidence_sha256'},
                   pf4={'status','reason'})


def public_report(value):
    if set(value) != REPORT_FIELDS or any(type(value[k]) is not int or value[k] != 0 for k in ('provider_calls','inference_calls')):
        raise IntegrityError('PUBLIC_REPORT_FIELD')
    for gate, fields in GATE_FIELDS.items():
        if not isinstance(value[gate], list) or any(set(row) != fields for row in value[gate]):
            raise IntegrityError('PUBLIC_REPORT_FIELD')
    if value['version'] != 'phase6-harness-v2.preflight.v1' or value['status'] not in ('PASS','FAIL','UNKNOWN') or value['errors'] != []:
        raise IntegrityError('PUBLIC_REPORT_FIELD')
    if not re.fullmatch('[A-Za-z0-9_-]{32,55}', value['locator_id']):
        raise IntegrityError('PUBLIC_REPORT_FIELD')
    for key in ('manifest_sha256','source_freeze_sha256'):
        if not re.fullmatch('[0-9a-f]{64}', value[key]):
            raise IntegrityError('PUBLIC_REPORT_FIELD')
    for key in ('started_at_utc', 'ended_at_utc'):
        if not isinstance(value[key], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00', value[key]):
            raise IntegrityError('PUBLIC_REPORT_FIELD')
    reasons = {'REQUEST_JSON_INVALID','REQUEST_HASH_DRIFT','REACHABLE_ROUTE_DRIFT','BRANCH_COUNT_DRIFT',
        'UNKNOWN_UNSUPPORTED_SCHEMA','ACT_SET_UNKNOWN','SELECTOR_MISSING','PASS_DESIGN_FIXTURE','SELECTOR_SPACE_CLEAR',
        'FIRST_KEY_PARTITION_TRAP','EXPECTATION_DRIFT','PROJECTION_BINDING_DRIFT','INVENTORY_SHAPE','INVENTORY_BINDING_DRIFT',
        'DUPLICATE_INVENTORY','FORBIDDEN_CANDIDATE','REPLY_BINDING_DRIFT','AUTHORITY_UNPROVEN','NOT_APPLICABLE',
        'CANDIDATES_PRESENT','MISSING_REPLY_CANDIDATE','MISSING_CANDIDATE','PROVIDER_NOT_AUTHORIZED'}
    for gate in ('pf1','pf2','pf4'):
        for row in value[gate]:
            if row['status'] not in ('PASS','FAIL','UNKNOWN','NOT_RUN') or row['reason'] not in reasons:
                raise IntegrityError('PUBLIC_REPORT_FIELD')
    for row in value['pf1']:
        if row['fixture_id'] not in PROFILES[:7] or row['leading_selector'] not in ('kind','reply_to') or row['act_field'] not in ('kind','act') or type(row['branch_count']) is not int or not 0 <= row['branch_count'] <= 64:
            raise IntegrityError('PUBLIC_REPORT_FIELD')
    for row in value['pf2']:
        if row['case_id'] not in CASE_IDS or row['profile'] not in ('PRODUCT_V1','GB1') or any(type(row[k]) is not int or not 0 <= row[k] <= 64 for k in ('required_count','present_count')):
            raise IntegrityError('PUBLIC_REPORT_FIELD')
    for row in value['pf3']:
        if row['stage_id'] not in ('T506_CHOICE32','WP2_chat_plan','WP2_message','WP2_pre_vote','WP2_co_opportunity','WP2_ability') or row['status'] not in ('PASS','FAIL','UNKNOWN') or row['mode'] not in ('LEGAL_COUNTEREXAMPLE','FINITE_EXHAUSTIVE','UNBOUNDED_OR_UNPROVEN') or row['evidence_sha256'] not in (SAVED_SHA[0], SCHEMAS_SHA) or any(row[k] is not None and (type(row[k]) is not int or row[k] < 0) for k in ('max_tokens','observed_tokens')):
            raise IntegrityError('PUBLIC_REPORT_FIELD')
    expected_stages = ('T506_CHOICE32','WP2_chat_plan','WP2_message','WP2_pre_vote','WP2_co_opportunity','WP2_ability')
    if (Counter(row['fixture_id'] for row in value['pf1']) != Counter(PROFILES[:7]) or
        Counter((row['case_id'],row['profile']) for row in value['pf2']) != Counter({(c,p):3 for c in CASE_IDS for p in ('PRODUCT_V1','GB1')}) or
        Counter(row['stage_id'] for row in value['pf3']) != Counter(expected_stages) or
        value['pf4'] != [{'status':'NOT_RUN','reason':'PROVIDER_NOT_AUTHORIZED'}] or
        value['status'] != aggregate_preflight(value['pf1'],value['pf2'],value['pf3'],value['pf4'])):
        raise IntegrityError('PUBLIC_REPORT_FIELD')
    pairs = {
        'pf1': {'PASS': {'PASS_DESIGN_FIXTURE','SELECTOR_SPACE_CLEAR'}, 'FAIL': {'REQUEST_JSON_INVALID','FIRST_KEY_PARTITION_TRAP'},
                'UNKNOWN': {'REQUEST_HASH_DRIFT','REACHABLE_ROUTE_DRIFT','BRANCH_COUNT_DRIFT','UNKNOWN_UNSUPPORTED_SCHEMA','ACT_SET_UNKNOWN','SELECTOR_MISSING'}},
        'pf2': {'PASS': {'NOT_APPLICABLE','CANDIDATES_PRESENT'}, 'FAIL': {'FORBIDDEN_CANDIDATE','MISSING_REPLY_CANDIDATE','MISSING_CANDIDATE'},
                'UNKNOWN': {'EXPECTATION_DRIFT','PROJECTION_BINDING_DRIFT','INVENTORY_SHAPE','INVENTORY_BINDING_DRIFT','DUPLICATE_INVENTORY','REPLY_BINDING_DRIFT','AUTHORITY_UNPROVEN'}},
    }
    for gate in pairs:
        for row in value[gate]:
            if row['reason'] not in pairs[gate].get(row['status'],set()):
                raise IntegrityError('PUBLIC_REPORT_FIELD')
    for row in value['pf3']:
        if row['stage_id'] == 'T506_CHOICE32':
            if row['status'] not in ('FAIL','UNKNOWN') or row['mode'] != 'LEGAL_COUNTEREXAMPLE' or row['max_tokens'] != 32 or row['evidence_sha256'] != SAVED_SHA[0]:
                raise IntegrityError('PUBLIC_REPORT_FIELD')
        elif row['status'] != 'UNKNOWN' or row['mode'] != 'UNBOUNDED_OR_UNPROVEN' or row['max_tokens'] is not None or row['observed_tokens'] is not None or row['evidence_sha256'] != SCHEMAS_SHA:
            raise IntegrityError('PUBLIC_REPORT_FIELD')
    # Roundtrip deliberately creates a fresh public-only object.
    return strict_json(canonical({key: value[key] for key in sorted(REPORT_FIELDS)}))


def preflight(verified, locator_id):
    started = datetime.now(timezone.utc).isoformat()
    artifacts = verified['artifacts']
    pf1, pf2, pf3 = [], [], []
    details = []
    for profile in PROFILES[:7]:
        artifact = next(a for a in artifacts if a['profile'] == profile)
        target = pf1_target(profile)
        checked = analyze_pf1(artifact['request'].encode('utf-8'), target)
        details.append(checked)
        pf1.append(dict(fixture_id=profile, status=checked['status'], reason=checked['reason'],
                        branch_count=len(checked['rows']), leading_selector=target['leading_selector'], act_field=target['act_field']))
    for artifact in artifacts:
        if artifact['profile'] in ('PRODUCT_V1', 'GB1'):
            checked = check_pf2(expectation(artifact['case_id'], artifact['profile']), artifact['inventory'])
            pf2.append({**dict(case_id=artifact['case_id'], profile=artifact['profile']),
                        **{k: checked[k] for k in ('status','reason','required_count','present_count')}})
    token = next(a for a in artifacts if a['profile'] == 'T506_CHOICE32')
    checked = check_pf3(token['contract'], token['schema'], token['evidence'])
    pf3.append(dict(stage_id='T506_CHOICE32', **{k: checked[k] for k in ('status','mode','max_tokens','observed_tokens')}, evidence_sha256=SAVED_SHA[0]))
    fixture = strict_json(next(a['request'] for a in artifacts if a['profile'] == 'WP2_CHAT_PLAN_FIXTURE'))
    for name in fixture['schemas']:
        pf3.append(dict(stage_id='WP2_' + name, status='UNKNOWN', mode='UNBOUNDED_OR_UNPROVEN', max_tokens=None,
                        observed_tokens=None, evidence_sha256=SCHEMAS_SHA))
    pf4 = [dict(status='NOT_RUN', reason='PROVIDER_NOT_AUTHORIZED')]
    report = dict(version='phase6-harness-v2.preflight.v1', status=aggregate_preflight(pf1, pf2, pf3, pf4),
                  locator_id=locator_id, manifest_sha256=verified['manifest_sha256'], source_freeze_sha256=verified['source_freeze_sha256'],
                  provider_calls=0, inference_calls=0, pf1=pf1, pf2=pf2, pf3=pf3,
                  pf4=pf4, errors=[], started_at_utc=started,
                  ended_at_utc=datetime.now(timezone.utc).isoformat())
    return public_report(report)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('probe', 'run'):
        print('COMMAND_DISABLED_BY_AUTHORITY')
        return 4
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    prepare = subs.add_parser('prepare'); prepare.add_argument('--spec', required=True); prepare.add_argument('--task-id', required=True)
    check = subs.add_parser('preflight'); check.add_argument('--locator', required=True); check.add_argument('--out', required=True)
    show = subs.add_parser('report'); show.add_argument('--preflight', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            if plain_path(args.spec) != SPEC.resolve():
                raise IntegrityError('SPEC_PATH_INVALID')
            print(json.dumps(prepare_cache(SPEC.read_bytes(), args.task_id)))
            return 0
        if args.command == 'preflight':
            if not re.fullmatch('[A-Za-z0-9_-]{32,55}', args.locator):
                raise IntegrityError('LOCATOR_INVALID')
            locator = strict_json(plain_path(LOCATORS / (args.locator + '.json')).read_bytes())
            if set(locator) != {'locator_id','manifest_sha256','source_freeze_sha256'} or locator['locator_id'] != args.locator:
                raise IntegrityError('LOCATOR_INVALID')
            verified = verify_cache(BASE / 'synthetic' / locator['locator_id'], SPEC_SHA)
            if any(verified[key] != locator[key] for key in ('manifest_sha256','source_freeze_sha256')):
                raise IntegrityError('LOCATOR_DRIFT')
            report = preflight(verified, args.locator)
            out = plain_path(args.out, missing_leaf=True)
            if ROOT not in out.parents or out.suffix != '.json':
                raise IntegrityError('PUBLIC_OUTPUT_PATH_INVALID')
            write_exclusive(out, canonical(report))
        else:
            report = public_report(strict_json(plain_path(args.preflight).read_bytes()))
        print(json.dumps(report, ensure_ascii=True, allow_nan=False))
        return {'PASS': 0, 'FAIL': 2, 'UNKNOWN': 3}[report['status']]
    except Exception:
        # No free-form exception/path/payload crosses the boundary.
        print('INPUT_OR_INTEGRITY_ERROR')
        return 5


if __name__ == '__main__':
    raise SystemExit(main())
