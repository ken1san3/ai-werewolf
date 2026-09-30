"""T550 test-only fixture adapter. No provider, game, or state mutation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any

from ai_client.discussion import generation_v2 as product
from ai_client.brain.controller import _repeat_text, _last_self_accepted_text, _cross_player_public_copy
from ai_client.llm.decision import _validate_generated_text
from scripts import phase6_local_staged_probe as old
from scripts.phase6_conversation_suite import cases, project
from scripts.phase6_harness_v2 import expected_acts

SEEDS = (4242027, 4242028, 4242029)
STAGES = ('chat_plan', 'message', 'pre_vote', 'co_opportunity')
P_KEYS = ('schema_version', 'self_player', 'current', 'public_co', 'recent_chat',
          'plan', 'selected_public_facts', 'selected_disclosures', 'selected_claims')


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return sha256(value if isinstance(value, bytes) else wire(value)).hexdigest()


def thaw(value):
    if hasattr(value, 'items'):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class Fixture:
    case: Any
    projection: Any
    source_bytes: bytes
    catalog: product.GenerationCatalogV2
    binding_bytes: bytes
    stage: str
    channel: str | None
    schema_bytes: tuple[tuple[str, bytes], ...] = ()

    @property
    def source(self):
        return json.loads(self.source_bytes)

    @property
    def bindings(self):
        return json.loads(self.binding_bytes)


def build_fixture(case):
    """Only the fixed suite factory supplies projection authority."""
    projection = project(case, 'baseline')
    source = old._source(projection)
    legacy = old.catalog(projection)  # Existing capture/pointer validation, not its plan schema.
    owner = source['context']['player_id']
    trigger = source['capture']['trigger']['kind']
    stage = {'INITIAL_CHAT': 'chat_plan', 'PEER_CHAT': 'chat_plan',
             'PRE_VOTE': 'pre_vote', 'CO_OPPORTUNITY': 'co_opportunity'}[trigger]
    options = source['action_context']['options']
    channel = None
    if stage == 'chat_plan':
        chat_options = [o for o in options if o.get('action_kind') == 'chat']
        if len(chat_options) != 1:
            raise ValueError('CHAT_OPTION')
        channel = chat_options[0]['channel']
        if not any(c['channel_id'] == channel and c['is_public'] is True
                   for c in source['context']['chat_channels']):
            raise ValueError('PRIVATE_RECIPIENT_UNPROVEN')
    bindings = {}
    source_hash = digest(source)

    def bind(prefix, path, kind, actor=None, source_channel=None, authority='PUBLIC'):
        ident = prefix + f'{sum(k.startswith(prefix) for k in bindings):03}'
        value = old.resolve(source, path)
        if any(e['pointer'] == path and k.startswith(prefix) for k, e in bindings.items()):
            raise ValueError('DUPLICATE_BINDING')
        bindings[ident] = dict(projection_sha256=source_hash, pointer=path,
            source_kind=kind, actor=actor, channel=source_channel, authority=authority,
            value_sha256=digest(value))
        return ident

    players = {}
    for i, player in enumerate(source['grounding']['current']['players']):
        original = player['player_id']
        if original in players:
            raise ValueError('DUPLICATE_PLAYER')
        players[original] = bind('p', f'/grounding/current/players/{i}/player_id', 'PLAYER', original)
    if owner not in players:
        raise ValueError('SELF_MISSING')
    public_channels = {c['channel_id'] for c in source['context']['chat_channels'] if c['is_public']}
    eligible = []
    for i, rec in enumerate(source['memory']['records']):
        if (rec['source']['record_kind'] == 'chat' and rec['source']['visibility'] == 'PUBLIC'
                and rec['channel_id'] in public_channels and rec['channel_id'] == channel
                and rec['actor_player_ids'] != [owner]):
            eligible.append((i, rec))
    for i, rec in old._recent_chats(eligible, source['capture']['trigger']['source']):
        if len(rec['actor_player_ids']) != 1:
            raise ValueError('REPLY_ACTOR')
        bind('r', f'/memory/records/{i}', 'CHAT', rec['actor_player_ids'][0], channel)
    for e in legacy['public_facts']:
        bind('f', e['pointer'], 'PUBLIC_FACT', e['actor_player_id'])
    # A public chat is an observed utterance, never an asserted objective truth.
    for i, rec in enumerate(source['memory']['records']):
        if (rec['source']['visibility'] == 'PUBLIC' and rec['channel_id'] in public_channels
                and (channel is None or rec['channel_id'] == channel)):
            bind('c', f'/memory/records/{i}', 'UTTERANCE', rec['actor_player_ids'], rec['channel_id'])
    for e in legacy['owner_ability']:
        bind('d', e['pointer'], 'ABILITY_RESULT', owner, channel, 'INTENTIONAL_OWNER_ABILITY')
    bases = []
    for i, assessment in enumerate(source['state']['assessments']):
        for dimension in ('suspicion', 'credibility'):
            prior = assessment[dimension]
            ident = bind('u', f'/state/assessments/{i}/{dimension}', 'OPINION_BASIS',
                         assessment['player_id'], channel, 'SUBJECTIVE_PRIOR')
            bases.append(product.OpinionBasisV2(ident, players[assessment['player_id']],
                dimension.upper(), prior, tuple(x for x in (0, 25, 50, 75, 100) if x != prior)))
    votes, cos = [], []
    for i, option in enumerate(options):
        if option.get('action_kind') == 'vote':
            ident = bind('o', f'/action_context/options/{i}', 'VOTE_OPTION', owner)
            votes.append(product.VoteOptionV2(ident, tuple(players[p] for p in option['valid_targets']),
                                              option['allows_abstain']))
        elif option['type'] == 'co_declare':
            ident = bind('o', f'/action_context/options/{i}', 'CO_OPTION', owner)
            roles = tuple(bind('q', f'/action_context/options/{i}/claimed_role_ids/{j}',
                               'CLAIMED_ROLE_OPTION', owner) for j in range(len(option['claimed_role_ids'])))
            cos.append(product.CoOptionV2(ident, roles))
    ids = lambda prefix: tuple(k for k in bindings if k.startswith(prefix))
    catalog = product.GenerationCatalogV2(ids('r'), ids('p'), tuple(v for k, v in players.items() if k != owner),
        ids('f'), ids('d'), ids('c'), ids('k'), tuple(bases), tuple(votes), tuple(cos), ())
    schemas = tuple((s, wire(product.build_generation_v2_schema(s, catalog)))
                    for s in ((stage, 'message') if stage == 'chat_plan' else (stage,)))
    fixture = Fixture(case, projection, wire(source), catalog, wire(bindings), stage, channel, schemas)
    verify_bindings(fixture)
    return fixture


def verify_bindings(fixture):
    source = fixture.source
    if fixture.source_bytes != wire(old._source(fixture.projection)):
        raise ValueError('PROJECTION_BINDING')
    legacy = old.catalog(fixture.projection)
    public_paths = {e['pointer']: e['actor_player_id'] for e in legacy['public_facts']}
    disclosure_paths = {e['pointer']: e['actor_player_id'] for e in legacy['owner_ability']}
    for ident, edge in fixture.bindings.items():
        if edge['projection_sha256'] != digest(source) or digest(old.resolve(source, edge['pointer'])) != edge['value_sha256']:
            raise ValueError('PROJECTION_BINDING')
        if ident.startswith('f') and (edge['authority'] != 'PUBLIC' or edge['pointer'] not in public_paths
                or edge['actor'] != public_paths[edge['pointer']]):
            raise ValueError('FACT_AUTHORITY')
        if ident.startswith('d') and (edge['authority'] != 'INTENTIONAL_OWNER_ABILITY'
                or edge['pointer'] not in disclosure_paths or edge['actor'] != disclosure_paths[edge['pointer']]):
            raise ValueError('DISCLOSURE_AUTHORITY')
        if ident.startswith(('r', 'c')):
            rec = old.resolve(source, edge['pointer'])
            actors = rec['actor_player_ids']
            expected_actor = actors[0] if ident.startswith('r') and len(actors) == 1 else actors
            if (rec['source']['visibility'] != 'PUBLIC' or edge['authority'] != 'PUBLIC'
                    or edge['actor'] != expected_actor or edge['channel'] != rec['channel_id']
                    or (fixture.channel is not None and edge['channel'] != fixture.channel)):
                raise ValueError('CHAT_AUTHORITY')


def selected(fixture, ident):
    edge = fixture.bindings[ident]
    value = old.resolve(fixture.source, edge['pointer'])
    if edge['source_kind'] in ('CHAT', 'UTTERANCE'):
        value = {k: value[k] for k in ('source', 'actor_player_ids', 'channel_id', 'day', 'phase', 'text_excerpt', 'text_truncated')}
    return {'id': ident, 'value': value, 'provenance': edge}


def presenter(candidate, fixture):
    if not isinstance(candidate, product.ParsedGenerationV2Candidate) or candidate.stage != 'chat_plan':
        raise ValueError('V2_PLAN_REQUIRED')
    plan = thaw(candidate.value)
    product.parse_and_validate_generation_v2_candidate_structure('chat_plan', wire(plan), fixture.catalog)
    source = fixture.source
    if not any(c['channel_id'] == fixture.channel and c['is_public'] is True for c in source['context']['chat_channels']):
        raise ValueError('PRIVATE_RECIPIENT_UNPROVEN')
    verify_bindings(fixture)
    recent = [(i, r) for i, r in enumerate(source['memory']['records'])
              if r['source']['record_kind'] == 'chat' and r['source']['visibility'] == 'PUBLIC'
              and r['channel_id'] == fixture.channel]
    reply = selected(fixture, plan['reply_to']) if plan['reply_to'] else None
    recent = old._recent_chats(recent, reply['value']['source'] if reply else None)
    surface = {k: plan[k] for k in ('act', 'subject_player_id', 'topic', 'stance', 'opinion_current')}
    surface['subject'] = selected(fixture, plan['subject_player_id']) if plan['subject_player_id'] else None
    surface['reply'] = reply
    surface['opinion_prior'] = selected(fixture, plan['opinion_basis_id']) if plan['opinion_basis_id'] else None
    output = dict(schema_version='t550.p-input.v2', self_player=source['context']['player_id'],
        current=source['grounding']['current'], public_co=source['grounding']['self_co']['records'],
        recent_chat=[{k: r[k] for k in ('source', 'actor_player_ids', 'channel_id', 'day', 'phase', 'text_excerpt', 'text_truncated')}
                     for _, r in recent], plan=surface,
        selected_public_facts=[selected(fixture, x) for x in plan['fact_ids']],
        selected_disclosures=[selected(fixture, x) for x in plan['disclose_ids']],
        selected_claims=[selected(fixture, plan['claim_id'])] if plan['claim_id'] else [])
    return output


def verify_presenter(value, candidate, fixture):
    # Reconstruct from the same exact bound projection/selection; every leaf,
    # including empty containers and provenance, must agree, with no extra keys.
    expected = presenter(candidate, fixture)
    if tuple(value) != P_KEYS or old.leaves(value) != old.leaves(expected):
        raise ValueError('P_PROVENANCE')


def text_guard(text, fixture):
    _validate_generated_text(text, projection=fixture.projection, char_limit=200)
    previous = _last_self_accepted_text(fixture.case.request)
    if (not _repeat_text(text) or (previous is not None and _repeat_text(text) == _repeat_text(previous))
            or _cross_player_public_copy(fixture.case.request, text)):
        raise ValueError('TEXT_GUARD')


def stress_texts(fixture):
    texts = [r['text_excerpt'] for r in fixture.source['memory']['records']
             if r['source']['record_kind'] == 'chat' and r['source']['visibility'] == 'PUBLIC' and r['text_excerpt']]
    texts += ['a', 'あ', '漢', '"', '\\', '\n', '\t', digest(fixture.source_bytes), '\U0001f600a']
    return tuple(dict.fromkeys((s * (200 // len(s) + 1))[:200] for s in texts))


def witnesses(stage, fixture):
    schema = json.loads(dict(fixture.schema_bytes)[stage])
    texts = stress_texts(fixture)

    def deref(node):
        while '$ref' in node:
            node = schema['$defs'][node['$ref'].split('/')[-1]]
        return node

    def values(node):
        node = deref(node)
        if 'not' in node:
            return []
        if 'const' in node:
            return [node['const']]
        if 'enum' in node:
            return node['enum']
        if node.get('type') == 'string':
            return list(texts)
        if node.get('type') == 'array':
            count = node['maxItems']
            items = values(node['items']) if count else []
            if not items:
                return [[]] if node.get('minItems', 0) == 0 else []
            if node.get('uniqueItems'):
                count = min(count, len(items))
            identity = next((k for k in ('player_id', 'claim_id') if isinstance(items[0], dict) and k in items[0]), None)
            if identity:
                count = min(count, len({item[identity] for item in items}))
                rows = []
                for i, item in enumerate(items):
                    row = [item]
                    for other in items[i + 1:] + items[:i]:
                        if len(row) >= count:
                            break
                        if other[identity] not in {x[identity] for x in row}:
                            row.append(other)
                    rows.append(row)
                return rows
            return [[items[(i + j) % len(items)] for j in range(count)] for i in range(len(items))]
        if node.get('type') == 'object':
            options = {k: values(v) for k, v in node['properties'].items()}
            if any(not v for v in options.values()):
                return []
            # Rotate each independent position through every value, without a
            # Cartesian product. Each position is fully covered at least once.
            return [{k: v[i % len(v)] for k, v in options.items()}
                    for i in range(max(map(len, options.values())))]
        raise ValueError('WITNESS_SCHEMA')

    result = {}
    for branch in schema.get('oneOf', [schema]):
        rows = values(branch)
        if not rows:
            raise ValueError('WITNESS_MISSING')
        for row in rows:
            raw = wire(row)
            product.parse_and_validate_generation_v2_candidate_structure(stage, raw, fixture.catalog)
            result[raw] = None
    return tuple(result)


def body(stage, fixture, seed, budget, candidate=None):
    schema = json.loads(dict(fixture.schema_bytes)[stage])
    payload = presenter(candidate, fixture) if stage == 'message' else {
        'projection': fixture.source, 'catalog': asdict(fixture.catalog),
        'bindings': {k: {'pointer': v['pointer'], 'authority': v['authority']} for k, v in fixture.bindings.items()}}
    system = ('Select only values offered in the schema and bound catalog. Return JSON only. '
              'Use the conversation evidence and respond to the current trigger. '
              'For message, follow the selected plan in one or two short English sentences.')
    return dict(messages=[dict(role='system', content=system), dict(role='user', content=wire(payload).decode())],
        response_format={'type': 'json_schema', 'json_schema': {'name': stage, 'strict': True, 'schema': schema}},
        temperature=0.7, top_p=0.9, seed=seed, max_tokens=budget, stream=False,
        chat_template_kwargs={'enable_thinking': False})


def pf1(raw, fixture):
    request = json.loads(raw)
    schema = request['response_format']['json_schema']['schema']
    expected = product.build_generation_v2_schema(fixture.stage, fixture.catalog)
    if wire(schema) != wire(expected):
        raise ValueError('PF1_WIRE_SCHEMA')
    if fixture.stage == 'chat_plan':
        for branch in schema['oneOf']:
            if list(branch['properties'])[:2] != ['reply_to', 'act']:
                raise ValueError('PF1_ORDER')
    return True


def pf2(fixture):
    verify_bindings(fixture)
    source = fixture.source
    selectors = []
    for act in expected_acts(fixture.case.case_id):
        if act in ('ANSWER', 'REBUTTAL'):
            candidates = [ident for ident in fixture.catalog.reply_ids
                          if selected(fixture, ident)['value']['source'] == source['capture']['trigger']['source']]
            valid = len(candidates) == 1
        elif act == 'QUESTION':
            valid = bool(fixture.catalog.player_ids)
        elif act == 'OPINION_CHANGE':
            valid = bool(fixture.catalog.opinion_bases and fixture.catalog.fact_ids)
        elif act == 'CLAIM':
            valid = bool(fixture.catalog.utterance_claim_ids)
        else:
            valid = act == 'NONE'
        selectors.append(valid)
    # Expected acts are alternative accepted acts; each required selector within
    # an alternative is bound above, rather than guessing semantic sufficiency.
    if selectors and not any(selectors):
        raise ValueError('PF2_SELECTOR')
    if fixture.stage == 'pre_vote' and not fixture.catalog.vote_options:
        raise ValueError('PF2_VOTE')
    if fixture.stage == 'co_opportunity' and not fixture.catalog.co_options:
        raise ValueError('PF2_CO')
    return True


def prepare():
    fixtures = tuple(build_fixture(c) for c in cases())
    if len(fixtures) != 32 or len({f.case.case_id for f in fixtures}) != 32:
        raise ValueError('SUITE_DOMAIN')
    rows = tuple((f, seed) for seed in SEEDS for f in fixtures)
    for f in fixtures:
        pf1(wire(body(f.stage, f, SEEDS[0], 1)), f)
        pf2(f)
        if f.case.case_id.startswith(('G01-', 'G16-')) and not f.catalog.reply_ids:
            raise ValueError('PF2_REPLY')
        if f.case.case_id == 'G04-1' and {b.prior for b in f.catalog.opinion_bases} != {80, 20}:
            raise ValueError('PF2_BASIS')
        if f.stage == 'pre_vote' and not f.catalog.vote_options:
            raise ValueError('PF2_VOTE')
        if f.stage == 'co_opportunity' and not f.catalog.co_options:
            raise ValueError('PF2_CO')
    return fixtures, rows
