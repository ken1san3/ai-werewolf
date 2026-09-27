"""T510 test-only plan/presenter boundary; no game dispatch or semantic inference."""
from __future__ import annotations

from copy import deepcopy
import json

from jsonschema import Draft202012Validator

from ai_client.discussion.context import canonical_json_bytes
from ai_client.discussion.model import EvidenceRecordKind
from ai_client.llm.decision import DecisionValidationError
from ai_client.llm.types import PromptProjection
from scripts import phase6_two_call_probe as p2

PLAN_TOKENS, MESSAGE_TOKENS = 384, 128
T_CONTROL = ('Return a plan only. Do not write the message or comment text. Select every '
             'required control value from the offered schema and catalog. Set updates_mode '
             'to NO_CHANGE. Select public_fact_ids and disclose_fact_ids explicitly; an '
             'evidence reference alone does not select disclosure.')
P_SYSTEM = ('Follow the selected action and speech act. If a reply excerpt is provided, '
            'respond to it. Write one or two short English sentences in the single text '
            'field required by the response schema.')
UPDATES = {'assessment_updates': [], 'claim_updates': [], 'relation_updates': [],
           'strategy_update': None}
EXTRA = ('updates_mode', 'public_fact_ids', 'disclose_fact_ids')
ACT_FIELDS = {
    'NONE': ('kind',),
    'CLAIM': ('kind', 'subject_player_id', 'topic', 'stance'),
    'QUESTION': ('kind', 'addressee_player_id', 'subject_player_id', 'topic'),
    'ANSWER': ('kind', 'addressee_player_id', 'topic', 'stance'),
    'REBUTTAL': ('kind', 'addressee_player_id', 'topic', 'stance'),
    'OPINION_CHANGE': ('kind', 'subject_player_id', 'dimension', 'prior', 'current'),
    'RELATION_HYPOTHESIS': ('kind', 'source_player_id', 'target_player_id', 'relation', 'confidence'),
}
ABILITY_KEYS = {'order', 'day', 'phase', 'event_type', 'target_player_id', 'result_id', 'revealed_role_id'}
CURRENT_KEYS = {'day', 'phase', 'players', 'alive_player_ids', 'vote_candidate_player_ids'}


def plain(value):
    return json.loads(canonical_json_bytes(value))


def _source(projection):
    if not isinstance(projection, PromptProjection) or projection.discussion_capture is None:
        raise ValueError('CAPTURE_REQUIRED')
    value = plain(projection.canonical_input)
    capture = projection.discussion_capture
    if (value['context']['player_id'] != capture.player_id
            or value['capture']['capture_id'] != capture.capture_id
            or value['capture']['context_sha256'] != capture.context_sha256
            or value['context'] != plain(capture.context)):
        raise ValueError('CAPTURE_BINDING')
    return value


def _exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('SOURCE_SHAPE')


def resolve(value, pointer):
    # Only host-authored catalog paths enter this resolver; no network or inference.
    node = value
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('POINTER')
    try:
        for part in pointer[1:].split('/'):
            if isinstance(node, list):
                if not part.isdecimal() or str(int(part)) != part:
                    raise ValueError('POINTER')
                node = node[int(part)]
            else:
                node = node[part]
    except (KeyError, IndexError, TypeError):
        raise ValueError('POINTER') from None
    return deepcopy(node)


def catalog(projection):
    value = _source(projection)
    owner = projection.discussion_capture.player_id
    records = value['memory']['records']
    captured = {canonical_json_bytes(x.source): plain(x) for x in projection.discussion_capture.evidence}
    evidence = []
    seen = set()
    if len(records) > 24:
        raise ValueError('CATALOG_BOUND')
    for index, record in enumerate(records):
        ref = record['source']
        key = canonical_json_bytes(ref)
        original = captured.get(key)
        fields = ('actor_player_ids', 'channel_id', 'day', 'phase')
        if original is None or key in seen or any(record[k] != original[k] for k in fields):
            raise ValueError('EVIDENCE_BINDING')
        seen.add(key)
        evidence.append(dict(id=f'e{index:03}', pointer=f'/memory/records/{index}/source',
                             ref=deepcopy(ref), **{k: deepcopy(record[k]) for k in fields}))
    current = value['grounding']['current']
    _exact(current, CURRENT_KEYS)
    facts = []
    def fact(path, actor=None):
        if resolve(value, path) is not None:
            facts.append(dict(id=f'p{len(facts):03}', pointer=path,
                              actor_player_id=actor, visibility='PUBLIC'))
    for name in ('day', 'phase'):
        fact('/grounding/current/'+name)
    for index, player in enumerate(current['players']):
        _exact(player, ('player_id', 'alive', 'death'))
        path = f'/grounding/current/players/{index}'
        fact(path+'/alive', player['player_id'])
        if player['death'] is not None:
            _exact(player['death'], ('day', 'public_cause'))
            for name in ('day', 'public_cause'):
                fact(path+'/death/'+name, player['player_id'])
    for name in ('alive_player_ids', 'vote_candidate_player_ids'):
        for index in range(len(current[name])):
            fact(f'/grounding/current/{name}/{index}')
    for index, record in enumerate(value['grounding']['self_co']['records']):
        _co_shape(record)
        fact(f'/grounding/self_co/records/{index}', owner)
    ability = value['grounding']['ability_results']['records']
    if len(facts) > 256 or len(ability) > 8:
        raise ValueError('CATALOG_BOUND')
    for record in ability:
        _exact(record, ABILITY_KEYS)
    return dict(schema_version='t510.catalog.v1', evidence=evidence, public_facts=facts,
                owner_ability=[dict(id=f'a{i:03}', pointer=f'/grounding/ability_results/records/{i}',
                    actor_player_id=owner, visibility='AUTHORIZED_PRIVATE') for i in range(len(ability))])


def _co_shape(record):
    common = {'order', 'day', 'phase', 'record_kind'}
    variants = {'co_declaration': {'claimed_role_id', 'comment'},
                'co_report': {'kind', 'target_player_id', 'claimed_result'}}
    kind = record.get('record_kind')
    if kind not in variants:
        raise ValueError('CO_SHAPE')
    _exact(record, common | variants[kind])


def _selection_schema(entries, maximum):
    items = {'type': 'string'}
    if entries:
        items['enum'] = [x['id'] for x in entries]
    return dict(type='array', minItems=0, maxItems=min(maximum, len(entries)),
                uniqueItems=True, items=items)


def plan_schema(projection):
    schema = p2.plan_schema(projection.decision_schema)
    cat = catalog(projection)
    try:
        branches = schema['properties']['discussion']['oneOf']
        for branch in branches:
            for key in UPDATES:
                if branch['required'].count(key) != 1 or key not in branch['properties']:
                    raise ValueError('SCHEMA_SHAPE_CHANGED')
                branch['required'].remove(key)
                del branch['properties'][key]
        schema['properties'].update(updates_mode={'const': 'NO_CHANGE'},
            public_fact_ids=_selection_schema(cat['public_facts'], 2),
            disclose_fact_ids=_selection_schema(cat['owner_ability'], 1))
        schema['required'].extend(EXTRA)
    except (KeyError, TypeError):
        raise ValueError('SCHEMA_SHAPE_CHANGED') from None
    Draft202012Validator.check_schema(schema)
    return schema


def _expand(plan):
    legacy = deepcopy(plan)
    for key in EXTRA:
        del legacy[key]
    legacy['discussion'].update(deepcopy(UPDATES))
    return legacy


def validate_plan(raw, projection):
    value = p2.strict_json(raw)
    if not Draft202012Validator(plan_schema(projection)).is_valid(value):
        raise ValueError('T_SCHEMA_INVALID')
    if value['decision']['kind'] not in p2._TEXT_FIELDS and (value['public_fact_ids'] or value['disclose_fact_ids']):
        raise ValueError('NON_TEXT_SELECTION')
    try:
        p2.validate_plan(p2._json_text(_expand(value)), projection)
    except DecisionValidationError as error:
        raise ValueError('T_SEMANTIC_'+error.code.value) from None
    return value


def from_legacy(value, projection, *, public_fact_ids=(), disclose_fact_ids=()):
    plan, text = p2.split_legacy(value, projection.decision_schema)
    for key, expected in UPDATES.items():
        if plan['discussion'].pop(key) != expected:
            raise ValueError('NONEMPTY_UPDATE')
    plan.update(updates_mode='NO_CHANGE', public_fact_ids=list(public_fact_ids),
                disclose_fact_ids=list(disclose_fact_ids))
    return validate_plan(p2._json_text(plan), projection), text


def plan_body(baseline, projection):
    body, schema = p2._body_copy(baseline)
    if schema != plain(projection.decision_schema):
        raise ValueError('BODY_PROJECTION_BINDING')
    body['messages'].append(dict(role='user', content=T_CONTROL+'\n'+canonical_json_bytes(catalog(projection)).decode('utf-8')))
    body['response_format']['json_schema']['schema'] = plan_schema(projection)
    body['max_tokens'] = PLAN_TOKENS
    return body


def leaves(value, path=''):
    """Include empty containers: absence of data is still an explicit P field."""
    if isinstance(value, dict) and value:
        return {p: v for key, item in value.items() for p, v in leaves(item, path+'/'+key).items()}
    if isinstance(value, list) and value:
        return {p: v for i, item in enumerate(value) for p, v in leaves(item, path+'/'+str(i)).items()}
    return {path: value}


def _recent_chats(eligible, reply):
    eligible = sorted(eligible, key=lambda pair: pair[1]['source']['order'])
    recent = eligible[-6:]
    if reply is not None:
        reply_entry = next((pair for pair in eligible if pair[1]['source'] == reply), None)
        if reply_entry is not None and reply_entry not in recent:
            recent = [reply_entry, *recent[-5:]]
    return sorted(recent, key=lambda pair: pair[1]['source']['order'])


def presenter_input(plan, projection):
    plan = validate_plan(p2._json_text(plan), projection)
    value, cat = _source(projection), catalog(projection)
    decision, discussion = plan['decision'], plan['discussion']
    kind = decision['kind']
    if kind not in p2._TEXT_FIELDS:
        raise ValueError('NO_MESSAGE_ACTION')
    option = next((o for o in value['action_context']['options'] if o['option_id'] == decision['option_id']), None)
    if option is None or option['action_kind'] != kind:
        raise ValueError('OPTION_BINDING')
    channel = option.get('channel') if kind == 'chat' else None
    # Current canonical channels do not contain sealed recipient intersections.
    # A private channel must therefore fail closed, never invent recipient authority.
    if kind == 'chat' and not any(c['channel_id'] == channel and c['is_public'] is True
                                  for c in value['context']['chat_channels']):
        raise ValueError('PRIVATE_RECIPIENT_UNPROVEN')
    public_channels = {c['channel_id'] for c in value['context']['chat_channels'] if c['is_public']}
    provenance = {}
    owner = projection.discussion_capture.player_id
    def track(path, source, item, *, origin='CANONICAL', lane='PUBLIC', visibility='PUBLIC', source_channel=None):
        for suffix, leaf in leaves(item).items():
            destination = path+suffix
            if destination in provenance:
                raise ValueError('PROVENANCE_DUPLICATE')
            provenance[destination] = dict(source=source+suffix, origin=origin, lane=lane,
                owner_player_id=owner, owner_visibility=visibility, channel=source_channel)
        return item
    def copy(path, source, lane='PUBLIC'):
        visibility, source_channel = 'PUBLIC', None
        if source.startswith('/memory/records/'):
            record = value['memory']['records'][int(source.split('/')[3])]
            if not public(record):
                raise ValueError('PROVENANCE_PRIVATE')
            visibility, source_channel = record['source']['visibility'], record['channel_id']
        elif source.startswith('/grounding/ability_results/records/'):
            if lane != 'INTENTIONAL_OWNER_ABILITY' or source not in {
                    e['pointer'] for e in cat['owner_ability'] if e['id'] in plan['disclose_fact_ids']}:
                raise ValueError('PROVENANCE_DISCLOSURE')
            visibility = 'AUTHORIZED_PRIVATE'
        elif not (source == '/context/player_id' or source == '/grounding/current'
                  or source.startswith('/grounding/current/')
                  or source == '/grounding/self_co/records'
                  or source.startswith('/grounding/self_co/records/')):
            raise ValueError('PROVENANCE_SOURCE')
        return track(path, source, resolve(value, source), lane=lane,
                     visibility=visibility, source_channel=source_channel)
    def control(path, source):
        return track(path, source, resolve(plan, source), origin='PLAN',
                     lane='MODEL_SELECTION', visibility='MODEL_SELECTION', source_channel=channel)
    def constant(path, item):
        return track(path, 'constant:'+path, item, origin='CONSTANT')
    def selected(name, key, lane):
        entries = {x['id']: x for x in cat[key]}
        items = []
        selection = 'public_fact_ids' if name == 'public_facts' else 'disclose_fact_ids'
        for i, ident in enumerate(plan[selection]):
            entry = entries[ident]
            cat_index = next(j for j, e in enumerate(cat[key]) if e['id'] == ident)
            items.append(dict(id=control(f'/{name}/{i}/id', f'/{selection}/{i}'),
                pointer=track(f'/{name}/{i}/pointer', f'/{key}/{cat_index}/pointer',
                              entry['pointer'], origin='CATALOG'),
                value=copy(f'/{name}/{i}/value', entry['pointer'], lane)))
        return items if items else constant('/'+name, [])
    facts = selected('public_facts', 'public_facts', 'PUBLIC')
    disclosed = selected('intentional_disclosures', 'owner_ability', 'INTENTIONAL_OWNER_ABILITY')
    records = value['memory']['records']
    def public(record):
        return (record['source']['visibility'] == 'PUBLIC' and
                (record['channel_id'] is None or record['channel_id'] in public_channels) and
                (channel is None or record['channel_id'] is None or record['channel_id'] == channel))
    speech = discussion['speech_act']
    eligible = [(i, rec) for i, rec in enumerate(records)
                if rec['source']['record_kind'] == EvidenceRecordKind.CHAT.value and public(rec)]
    reply = speech.get('in_reply_to') or speech.get('source')
    if reply is None and discussion['reaction'] is not None:
        reply = discussion['reaction']['trigger']
    eligible = _recent_chats(eligible, reply)
    chats = []
    for j, (i, rec) in enumerate(eligible):
        source = f'/memory/records/{i}'
        chats.append(dict(ref=copy(f'/public_chat/{j}/ref', source+'/source'),
            **{k:copy(f'/public_chat/{j}/{k}', source+'/'+k) for k in
               ('actor_player_ids', 'channel_id', 'day', 'phase', 'text_excerpt', 'text_truncated')}))
    if not chats:
        constant('/public_chat', [])
    refs = []
    for key in ('source', 'in_reply_to', 'evidence', 'causes'):
        item = speech.get(key)
        if item is not None:
            refs.extend((key, ref) for ref in (item if isinstance(item, list) else [item]))
    if discussion['reaction'] is not None:
        refs.append(('trigger', discussion['reaction']['trigger']))
    selected_refs, seen = [], set()
    for use, ref in refs:
        encoded = canonical_json_bytes(ref)
        if encoded in seen:
            continue
        seen.add(encoded)
        matches = [(i, rec) for i, rec in enumerate(records) if rec['source'] == ref]
        if len(matches) != 1:
            raise ValueError('REF_BINDING')
        i, rec = matches[0]
        if not public(rec):
            # Only explicitly selected authoritative owner ability can replace
            # access to its private record; never forward a private text excerpt.
            if use in ('source', 'in_reply_to', 'trigger') or not any(
                    x['value']['order'] == ref['order'] and ref['record_kind'] == EvidenceRecordKind.ABILITY_RESULT.value
                    for x in disclosed):
                raise ValueError('REF_NOT_PRESENTABLE')
            continue
        j = len(selected_refs)
        selected_refs.append(dict(use=constant(f'/selected_evidence/{j}/use', use), ref=copy(f'/selected_evidence/{j}/ref', f'/memory/records/{i}/source'),
            **{k:copy(f'/selected_evidence/{j}/{k}', f'/memory/records/{i}/{k}') for k in
               ('actor_player_ids', 'channel_id', 'text_excerpt', 'text_truncated')}))
    # Scalars are already strictly parsed model choices, not inferred metadata.
    if not selected_refs:
        constant('/selected_evidence', [])
    option_index = value['action_context']['options'].index(option)
    plan_view = dict(action_kind=control('/plan/action_kind', '/decision/kind'),
        option_id=control('/plan/option_id', '/decision/option_id'),
        channel=constant('/plan/channel', None) if kind != 'chat' else
            track('/plan/channel', f'/action_context/options/{option_index}/channel', channel),
        claimed_role_id=control('/plan/claimed_role_id', '/decision/claimed_role_id') if kind == 'co_declare' else constant('/plan/claimed_role_id', None),
        co_judgment=control('/plan/co_judgment', '/discussion/co_judgment'),
        speech_act={key:control('/plan/speech_act/'+key, '/discussion/speech_act/'+key) for key in ACT_FIELDS[speech['kind']]},
        reaction=control('/plan/reaction', '/discussion/reaction') if discussion['reaction'] is None else
            {key:control('/plan/reaction/'+key, '/discussion/reaction/'+key) for key in ('reason', 'score')})
    output = dict(schema_version=constant('/schema_version', 't510.p-input.v1'),
        self_player_id=copy('/self_player_id', '/context/player_id'),
        current=copy('/current', '/grounding/current'),
        public_co=copy('/public_co', '/grounding/self_co/records'), public_chat=chats,
        plan=plan_view, selected_evidence=selected_refs, public_facts=facts, intentional_disclosures=disclosed)
    if set(leaves(output)) != set(provenance):
        raise ValueError('PROVENANCE_COVERAGE')
    roots = {'CANONICAL': value, 'PLAN': plan, 'CATALOG': cat}
    for path, edge in provenance.items():
        if edge['origin'] != 'CONSTANT' and canonical_json_bytes(resolve(roots[edge['origin']], edge['source'])) != canonical_json_bytes(resolve(output, path)):
            raise ValueError('PROVENANCE_VALUE')
    return output, provenance


def message_body(baseline, plan, projection):
    body, schema = p2._body_copy(baseline)
    if schema != plain(projection.decision_schema):
        raise ValueError('BODY_PROJECTION_BINDING')
    output, _ = presenter_input(plan, projection)
    body['messages'] = [dict(role='system', content=P_SYSTEM),
                        dict(role='user', content=canonical_json_bytes(output).decode('utf-8'))]
    body['response_format']['json_schema']['schema'] = p2._text_schema(schema, plan['decision']['kind'])
    body['max_tokens'] = MESSAGE_TOKENS
    return body


def validate_final(plan, text_or_none, projection):
    plan = validate_plan(p2._json_text(plan), projection)
    if plan['decision']['kind'] in p2._TEXT_FIELDS:
        presenter_input(plan, projection)  # Missing authority cannot be bypassed at assembly.
    try:
        return p2.validate_final(_expand(plan), text_or_none, projection)
    except DecisionValidationError as error:
        raise ValueError('P_SEMANTIC_'+error.code.value) from None
