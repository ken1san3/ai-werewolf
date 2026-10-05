"""Record-based scene scoring, game measurements and bounded lesson notes."""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
from dataclasses import asdict
import json
from pathlib import Path
import re
import time

from jsonschema import Draft202012Validator
import yaml

from server.aiwolf_core import load_content, load_preset
from .claims import self_claims
from .prompts import messages
from .state import PlayerState
from .strategy import vote_pressure
from .marathon_runtime import ROOT, read_json, save_json
from .rule_facts import rule_facts


def content_and_preset():
    content = load_content(ROOT / 'content')
    return content, load_preset(ROOT / 'content/presets/standard_9.yaml', content)


def snapshot(record, player_id, at, content):
    """Replay public events plus this recipient's private results, never future rows."""
    role_id = record['roles'][player_id]
    role = content.roles[role_id]
    state = PlayerState(player_id, role_id=role_id, players=list(record['roles']), alive=set(record['roles']))
    if role.knows_teammates:
        # The assignment's declared teammate tags are the server's delivery rule.
        team = content.teams[role.attributes.team]
        state.teammates = [p for p, r in record['roles'].items() if p != player_id
                          and content.roles[r].attributes.team == team.id
                          and content.roles[r].tags & team.teammate_tags]
    timeline = [(r['t'], 0, r) for r in record['rows'] if r['t'] <= at]
    timeline += [(r['t'], 1, r) for r in record['private_results'] if r['player_id'] == player_id and r['t'] <= at]
    if role.knows_teammates:
        timeline += [(r['t'], 2, r) for r in record['private_messages'] if r['t'] <= at]
    for _, order, row in sorted(timeline, key=lambda x: (x[0], x[1])):
        if order == 1:
            state._visible('game.event', row)
        elif row['kind'] == 'chat':
            state._visible('chat.message', {'channel': row['channel'], 'message': row['message']})
        else:
            state._visible('game.event', {'visibility': 'public', 'event_type': row['kind'], 'event_payload': row['payload']})
    # Historic accepted abilities have no receipt time: only include earlier nights.
    state.own_actions = [{k: v for k, v in a.items() if k != 'player_id'}
                         for a in record.get('accepted_abilities', []) if a['player_id'] == player_id and a['day'] < state.day]
    return state


def scene_prompt(scene):
    content, preset = content_and_preset()
    state = PlayerState(**{**scene['state'], 'alive': set(scene['state']['alive'])})
    choices = '\n'.join(f'{key}: {value}' for key, value in scene['options'].items())
    return messages(state, content.roles, dict(preset.role_counts),
                    scene['question'] + '\n選択肢:\n' + choices + '\n{"answer":"選択肢ID"}だけを返してください。', rules=preset.rules)


def grade_scene(scene, answer):
    try:
        parsed = json.loads(answer) if isinstance(answer, str) else answer
        return isinstance(parsed, dict) and set(parsed) == {'answer'} and parsed['answer'] in scene['correct']
    except (ValueError, TypeError):
        return False


async def solve_scenes(llm, scenes, destination, *, repeats=5):
    results = read_json(destination) if Path(destination).exists() else []
    done = {(r['scene'], r['repeat']) for r in results}
    for scene in scenes:
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['answer'],
                  'properties': {'answer': {'type': 'string', 'enum': list(scene['options'])}}}
        for repeat in range(repeats):
            if (scene['id'], repeat) in done:
                continue
            answer = await llm.complete(lambda: scene_prompt(scene), player_id=scene['state']['player_id'],
                                        purpose='scene', seed=10000 + repeat, schema=schema, max_tokens=64)
            results.append({'scene': scene['id'], 'repeat': repeat, 'answer': answer,
                            'correct': grade_scene(scene, answer), 'call': llm.calls[-1] if llm.calls else {}})
            save_json(destination, results)
    return {'correct': sum(r['correct'] for r in results), 'total': len(results),
            'accuracy': sum(r['correct'] for r in results) / len(results) if results else 0}


def game_metrics(record, checks, content):
    roles = record['roles']
    names = [r.name for r in content.roles.values()]
    day, phase, day_start, vote_start = 0, '', {}, {}
    co, pressure, suspects, lynches = [], {}, defaultdict(set), []
    for row in record['rows']:
        data, kind = row.get('payload', {}), row['kind']
        if kind == 'PHASE_STARTED':
            day, phase = data['day'], data['phase']
            if phase == 'day':
                day_start[day] = row['t']
            if phase == 'vote':
                vote_start[day] = row['t']
        if kind == 'CO_DECLARED':
            co.append({'player': data['player_id'], 'role': data['claimed_role_id'], 'day': day, 't': row['t']})
        if kind == 'chat':
            author, text = row['message']['player_id'], row['message']['message']
            for name in self_claims(author, text, names):
                role_id = next(r.id for r in content.roles.values() if r.name == name)
                co.append({'player': author, 'role': role_id, 'day': day, 't': row['t']})
            if phase == 'day':
                for target in roles:
                    if target != author and (vote_pressure(target, text) or re.search(re.escape(target) + r'\s*(?:さん)?(?:は|が|を).{0,20}(?:怪しい|疑わしい|疑って|疑う|黒い|人狼候補)', text)):
                        suspects[day].add(target)
                        pressure.setdefault((day, target), row['t'])
        if kind == 'VOTE_RESOLVED' and data.get('lynched_player_id'):
            lynches.append({'day': day, 'player': data['lynched_player_id']})
    rate = checks.get('settings', {}).get('clock_rate', 1)
    early = []
    for player, role in roles.items():
        if role in {'seer', 'medium'}:
            claimed = next((c for c in co if c['player'] == player and c['role'] == role and c['day'] == 1), None)
            early.append({'player': player, 'role': role, 'co_day1': claimed is not None,
                          'seconds': round((claimed['t'] - day_start[1]) * rate, 2) if claimed else None})
    guards = []
    for (d, player), at in pressure.items():
        if roles[player] != 'guard':
            continue
        claimed = next((c for c in co if c['player'] == player and c['role'] == 'guard' and c['t'] >= at and c['t'] < vote_start.get(d, float('inf'))), None)
        # An earlier true CO still answers the pressure; no need to repeat it.
        already = any(c['player'] == player and c['role'] == 'guard' and c['t'] <= at for c in co)
        guards.append({'day': d, 'player': player, 'co_before_vote': claimed is not None or already,
                       'lynched_without_co': any(l['day'] == d and l['player'] == player for l in lynches) and not (claimed or already)})
    votes = record.get('accepted_votes', [])
    concentration, relevant = [], []
    wolf_mate_votes = 0
    for d in sorted({v['day'] for v in votes}):
        selected = [v for v in votes if v['day'] == d]
        tally = Counter(v['target_player_id'] for v in selected)
        concentration.append({'day': d, 'share': max(tally.values()) / len(selected), 'votes': len(selected)})
        relevant.append({'day': d, 'share': sum(v['target_player_id'] in suspects[d] for v in selected) / len(selected)})
        for v in selected:
            a, b = content.roles[roles[v['player_id']]], content.roles[roles[v['target_player_id']]]
            wolf_mate_votes += int(a.knows_teammates and b.knows_teammates and a.attributes.team == b.attributes.team)
    helpers = [{'player': p, 'first_day': min((c['day'] for c in co if c['player'] == p and content.roles[c['role']].attributes.team == 'village'), default=None)}
               for p, r in roles.items() if content.roles[r].attributes.team == 'wolf' and not content.roles[r].knows_teammates]
    executions = sum(content.roles[roles[l['player']]].knows_teammates for l in lynches)
    return {'early_co': early, 'helper_fake_co': helpers, 'guard_pressure': guards,
            'guard_lynched_without_co': sum(g['lynched_without_co'] for g in guards),
            'vote_concentration': concentration, 'votes_for_suspects': relevant, 'wolf_mate_votes': wolf_mate_votes,
            'wolf_execution_share': executions / len(lynches) if lynches else None,
            'winner': checks.get('winner'), 'wall_sec': checks.get('wall_sec'),
            'llm_calls': len(checks.get('llm_calls', [])), 'generation': checks.get('generation_sec'),
            'wait': checks.get('wait_sec'), 'votes_available': bool(votes),
            'Q': {k: v for k, v in checks.get('metrics', {}).items() if k.startswith('Q')},
            'G': {k: v for k, v in checks.get('metrics', {}).items() if k.startswith('G')}}


def reflection_rules(content=None, preset=None):
    """YAML mechanics, with neutral descriptions; no strategy recommendations."""
    from .prompts import rule_explanation
    if content is None or preset is None:
        content, preset = content_and_preset()
    learning = yaml.safe_load((ROOT / 'content/ai_strategies_learning.yaml').read_text(encoding='utf-8'))
    roles = {}
    for path in sorted((ROOT / 'content/roles').glob('*.yaml')):
        definition = yaml.safe_load(path.read_text(encoding='utf-8'))
        role_id = definition['id']
        definition['description'] = learning.get('role_descriptions', {}).get(role_id, definition.get('description', ''))
        roles[role_id] = definition
    # Factor identical top-level values, without dropping any role mechanics.
    common = {}
    for key in {key for role in roles.values() for key in role} - {'id'}:
        if not all(key in role for role in roles.values()):
            continue
        values = Counter(json.dumps(role[key], ensure_ascii=False, sort_keys=True) for role in roles.values() if key in role)
        value, count = values.most_common(1)[0]
        if count > 1:
            common[key] = json.loads(value)
    overrides = {key: {field: value for field, value in role.items() if field not in common or value != common[field]}
                 for key, role in roles.items()}
    return {'役職の読み方': '各役職は共通項目に個別項目を上書きしたYAML定義。省略項目は共通項目を使う。',
            '役職の共通項目': common, '全役職の個別項目': overrides,
            'プリセットの役職人数': dict(preset.role_counts), 'プリセットのルール': asdict(preset.rules),
            'ルールの説明': rule_explanation(preset.rules),
            '夜番号の読み方': '初夜は第0夜(day=0)。初日の昼と投票の後が第1夜(day=1)。'
                             'available_from_nightは能力を使える最小の夜番号で、1なら初夜には使えない。'
                             '昼の役職COは能力の実行や結果の受信とは別で、結果がまだなくてもできる。',
            '照合の注意': 'ゲーム中の参加者の主張はルールではない。本人が受信した結果だけが本人の既知の結果。'
                         '他人のCOや公開結果の真偽は確定しない。能力結果は対象と判定項目に関する情報で、'
                         '別の相手・別の日の結果や、判定が示していない役職・所属・COの真偽を一律に確定させない。'
                         '正当な騙りは許されるが、誤った能力やルールを真実の教訓にしない。'}


def reflection_input(record, decisions, player, checks, content):
    """Post-game roles/votes are supplied only here, never in live-agent input."""
    summary = [{k: v for k, v in row.items() if k in {'kind', 'payload'}} for row in record['rows']
               if row['kind'] in {'PHASE_STARTED', 'CO_DECLARED', 'PLAYER_DIED', 'VOTE_RESOLVED', 'GAME_ENDED'}]
    day = 0
    spoken_co = []
    role_names = [r.name for r in content.roles.values()]
    for row in record['rows']:
        if row['kind'] == 'PHASE_STARTED':
            day = row['payload']['day']
        if row['kind'] == 'chat':
            claimed = self_claims(row['message']['player_id'], row['message']['message'], role_names)
            if claimed:
                spoken_co.append({'day': day, 'player': row['message']['player_id'], '公称': claimed})
    own = [d for d in decisions if d['player_id'] == player]
    own_speech = [r['message'] for r in record['rows'] if r['kind'] == 'chat' and r['message']['player_id'] == player]
    others = [r['message'] for r in record['rows'] if r['kind'] == 'chat' and r['message']['player_id'] != player
              and (player in r['message']['message'] or re.search('CO|占い|霊能|護衛|投票|処刑|黒|白', r['message']['message']))]
    role = content.roles[record['roles'][player]]
    learning = yaml.safe_load((ROOT / 'content/ai_strategies_learning.yaml').read_text(encoding='utf-8'))
    _, preset = content_and_preset()
    return {'本人': player, '本人の役職': role.id,
            '本人のルール': learning.get('role_descriptions', {}).get(role.id, role.description),
            '全役職とプリセットのルール': {'ルールの事実': rule_facts(content, preset)},
            '本人の能力': [{'id': a.id, 'available_from_night': a.available_from_night, '対象': a.target.selector} for a in role.abilities],
            '本人が受信した結果': [r for r in record.get('private_results', []) if r['player_id'] == player],
            '全員の公開された役職': {p: content.roles[r].name for p, r in record['roles'].items()},
            '勝敗': checks.get('winner'), '日別の記録': summary, '発言でのCO': spoken_co, '投票者と投票先': record.get('accepted_votes', []),
            '本人の発言': own_speech, '本人の判断': own, '他人の発言': others}


CONTEXT_TOKENS = 8192
RECORD_FIELDS = {'日別の記録', '発言でのCO', '投票者と投票先', '本人の発言', '本人の判断',
                 '他人の発言', '本人が受信した結果'}
ID_PATTERN = re.compile(r'player[\s_-]*\d+|プレイヤー[\s-]*\d+', re.I)
LESSON_ITEM = {'type': 'object', 'additionalProperties': False, 'required': ['scene', 'action', 'why'],
               'properties': {key: {'type': 'string', 'minLength': 1} for key in ['scene', 'action', 'why']}}
LESSON_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['role', 'general'],
                 'properties': {key: {'type': 'array', 'maxItems': maximum, 'items': LESSON_ITEM}
                                for key, maximum in [('role', 3), ('general', 1)]}}


async def _prompt_tokens(llm, prompt, request_options=None):
    if hasattr(llm, 'count_tokens'):
        return await llm.count_tokens(prompt, request_options=request_options)
    options = {'chat_template_kwargs': {'enable_thinking': False}, **getattr(llm, 'request_options', {}),
               **(request_options or {})}
    response = await llm.client.post(llm.url.replace('/v1/chat/completions', '/apply-template'),
                                     json={'messages': prompt, **options})
    response.raise_for_status()
    response = await llm.client.post(llm.url.replace('/v1/chat/completions', '/tokenize'),
                                     json={'content': response.json()['prompt'], 'add_special': True})
    response.raise_for_status()
    return len(response.json()['tokens'])


async def bounded_prompt(llm, data, instruction, reserve=2048, *, request_options=None):
    """Only sample game records. Rules, candidates and opinions are mandatory."""
    data = copy.deepcopy(data)
    limit = min(CONTEXT_TOKENS, getattr(llm, 'context_limit', 0) or CONTEXT_TOKENS)
    while True:
        prompt = [{'role': 'system', 'content': instruction},
                  {'role': 'user', 'content': json.dumps(data, ensure_ascii=False, separators=(',', ':'))}]
        if await _prompt_tokens(llm, prompt, request_options) + reserve + 32 <= limit:
            return prompt
        fields = [key for key in RECORD_FIELDS if isinstance(data.get(key), list) and data[key]]
        if not fields:
            raise ValueError('必須のルール・候補・意見と出力上限が8192トークンに収まりません')
        key = max(fields, key=lambda field: len(json.dumps(data[field], ensure_ascii=False)))
        values = data[key]
        if len(values) > 2:
            sampled = values[::2]
            data[key] = [*sampled, values[-1]] if len(values) % 2 == 0 else sampled
        else:
            data[key] = values[-1:] if len(values) == 2 else []
        data['ゲーム記録の一部は間引かれている'] = True


def clean_lessons(values, maximum=None):
    if not isinstance(values, list):
        return []
    kept = []
    for text in values:
        if isinstance(text, dict) and set(text) == {'scene', 'action', 'why'} and all(isinstance(text[key], str) and text[key].strip() for key in text):
            text = '→'.join(text[key].strip() for key in ['scene', 'action', 'why'])
        if isinstance(text, str) and text.strip() and not ID_PATTERN.search(text) and text.strip() not in kept:
            kept.append(text.strip())
    return kept if maximum is None else kept[:maximum]


def _note_item(item):
    item = {'text': item} if isinstance(item, str) else dict(item)
    games = sorted({n for n in item.get('games', []) if type(n) is int and n > 0})
    return {**item, 'games': games, 'support_games': len(games),
            'last_game': max(games, default=item.get('last_game', 0))}


def merge_candidates(notes, reflections, record, game_number):
    pools = {key: [_note_item(item) for item in value] for key, value in notes.items()}
    for player, answer in reflections.items():
        for key, maximum in [(record['roles'][player], 3), ('general', 1)]:
            for text in clean_lessons(answer.get('general' if key == 'general' else 'role'), maximum):
                pools.setdefault(key, []).append(_note_item({'text': text, 'games': [game_number]}))
    return pools


def _canonical(text):
    return re.sub(r'[\s。、「」]', '', text)


def consolidate_notes(pools, groups, game_number, *, maximum=8):
    """Groups cite source indices; game metadata is never supplied by the LLM."""
    result = {}
    for role, candidates in pools.items():
        candidates = [_note_item(item) for item in candidates]
        grouped, used = [], set()
        for group in groups.get(role, []) if isinstance(groups, dict) else []:
            indices = group.get('indices', []) if isinstance(group, dict) else []
            if not indices or any(type(i) is not int or not 0 <= i < len(candidates) or i in used for i in indices) or len(set(indices)) != len(indices):
                continue
            winner = max((candidates[i] for i in indices), key=lambda item: (item['support_games'], item['last_game']))
            text = clean_lessons([group.get('text', winner['text'])], 1)
            if not text:
                continue
            used.update(indices)
            games = sorted({n for i in indices for n in candidates[i]['games']})
            grouped.append(_note_item({'text': text[0], 'games': games}))
        grouped += [dict(item) for i, item in enumerate(candidates) if i not in used]
        by_text = {}
        for item in grouped:
            if not clean_lessons([item['text']], 1):
                continue
            canonical = _canonical(item['text'])
            if canonical in by_text:
                item = _note_item({**item, 'games': sorted(set(item['games']) | set(by_text[canonical]['games']))})
            by_text[canonical] = item
        ordered = sorted(by_text.values(), key=lambda item: (-item['support_games'], -item['last_game']))
        result[role] = ordered if maximum is None else ordered[:maximum]
    return result


def lesson_texts(notes):
    """Live callers choose whole lessons by tokens; no character truncation."""
    content, _ = content_and_preset()
    return {role: [_note_item(item) for item in [*notes.get(role, []), *notes.get('general', [])]
                   if clean_lessons([item.get('text') if isinstance(item, dict) else item], 1)]
            for role in content.roles}


class _StageFailed(Exception):
    pass


def _append_record(path, row):
    rows = read_json(path) if path.exists() else []
    rows.append(row)
    save_json(path, rows)


def _strict_json(raw):
    def unique_keys(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('JSONに重複キーがあります: ' + key)
            value[key] = item
        return value
    def invalid_constant(value):
        raise ValueError('JSONの数値が不正です: ' + value)
    return json.loads(raw, object_pairs_hook=unique_keys, parse_constant=invalid_constant)


def _coverage(rows, expected):
    actual = [row['id'] for row in rows]
    if len(actual) != len(expected) or set(actual) != set(expected) or len(set(actual)) != len(actual):
        raise ValueError('照合・議論の対象IDが全件を一度ずつ含んでいません')
    if any(not row['reason'].strip() for row in rows):
        raise ValueError('理由が空です')


def _validate_lessons(answer):
    for values in answer.values():
        if any(not all(value[key].strip() for key in ('scene', 'action', 'why')) for value in values):
            raise ValueError('教訓の場面・行動・理由は空にしてはいけません')


def _forced_options(thinking):
    return {'chat_template_kwargs': {'enable_thinking': True}, 'reasoning_budget_tokens': thinking}


async def _prepared_prompt(llm, data, instruction, max_tokens, forced):
    budget = forced if type(forced) is int else 512
    budgets = (budget, budget * 3 // 4, 256) if forced else (0,)
    error = None
    for thinking in budgets:
        options = _forced_options(thinking) if forced else {'chat_template_kwargs': {'enable_thinking': False},
                                                          'reasoning_budget_tokens': 0}
        try:
            prompt = await bounded_prompt(llm, data, instruction, reserve=thinking + max_tokens,
                                          request_options=options)
            return prompt, thinking, options
        except ValueError as exception:
            error = exception
    raise error


async def _json_call(llm, data, instruction, schema, destination, *, phase, player, number,
                     batch=0, max_tokens=1536, forced=False, validate=None, candidate_ids=()):
    for attempt in (1, 2):
        raw = None
        try:
            prompt, thinking, options = await _prepared_prompt(llm, data, instruction, max_tokens, forced)
            if phase == 'rule_check':
                options['temperature'] = 0
            request = {'player_id': player, 'purpose': phase, 'seed': number + attempt - 1,
                       'schema': schema, 'max_tokens': max_tokens}
            request.update(thinking_tokens=thinking, request_options=options)
            fitted = json.loads(prompt[1]['content'])
            _append_record(destination / 'requests.json', {'phase': phase, 'player': player, 'batch': batch,
                           'attempt': attempt, 'thinking_tokens': thinking, 'max_tokens': max_tokens,
                           'input_tokens': await _prompt_tokens(llm, prompt, options), 'candidate_ids': list(candidate_ids),
                           'game_record_counts': {key: len(fitted[key]) for key in RECORD_FIELDS if key in fitted},
                           'first_round_opinion_ids': {person: [item['id'] if isinstance(item, dict) else item[0]
                                                               for item in opinions]
                                                       for person, opinions in fitted.get('全員の1巡目の意見', {}).items()}})
            started = time.monotonic()
            raw = await llm.complete(lambda: prompt, **request)
            _append_record(destination / 'timings.json', {'phase': phase, 'player': player, 'batch': batch,
                           'attempt': attempt, 'wall_sec': round(time.monotonic() - started, 3)})
            answer = _strict_json(raw)
            Draft202012Validator(schema).validate(answer)
            if validate:
                validate(answer)
            return answer
        except Exception as exception:
            _append_record(destination / 'failures.json', {'phase': phase, 'player': player, 'batch': batch,
                           'attempt': attempt, 'candidate_ids': list(candidate_ids), 'error': str(exception),
                           'error_type': type(exception).__name__, 'answer': raw})
            # Only an invalid returned JSON gets one retry. Input/HTTP failures
            # cannot be fixed by asking for another JSON.
            if raw is None or attempt == 2:
                raise _StageFailed(str(exception)) from exception
            instruction += '\n前の出力はJSONの形式または対象のcoverageが不正でした。指定schemaと全対象IDを厳密に守ってください。'


def _indexed(pools, prefix='candidate'):
    return [{'id': f'{prefix}:{role}:{index}', 'role': role, 'index': index, **_note_item(item)}
            for role, rows in pools.items() for index, item in enumerate(rows)]


def _output_tokens(count):
    return min(2048, 256 + 160 * count)


async def _batches(llm, candidates, data_for, instruction):
    """Grow each batch until its mandatory input/output no longer fits."""
    batches, current = [], []
    for candidate in candidates:
        trial = [*current, candidate]
        fits = len(trial) <= 11
        if fits:
            try:
                await _prepared_prompt(llm, data_for(trial), instruction, _output_tokens(len(trial)), True)
            except ValueError:
                fits = False
        if not fits and current:
            batches.append(current)
            current = [candidate]
        else:
            current = trial
    if current:
        batches.append(current)
    return batches


def _opinion_schema(ids):
    item = {'type': 'object', 'additionalProperties': False, 'required': ['id', 'stance', 'reason', 'revision'],
            'properties': {'id': {'type': 'string', 'enum': ids},
                           'stance': {'type': 'string', 'enum': ['賛成', '反対', '直す']},
                           'reason': {'type': 'string', 'minLength': 1},
                           'revision': {'anyOf': [LESSON_ITEM, {'type': 'null'}]}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['opinions'],
            'properties': {'opinions': {'type': 'array', 'minItems': len(ids), 'maxItems': len(ids), 'items': item}}}


def _validate_opinions(answer, ids):
    _coverage(answer['opinions'], ids)
    for opinion in answer['opinions']:
        if opinion['stance'] == '直す' and not clean_lessons([opinion['revision']], 1):
            raise ValueError('直す意見にはIDを含めない具体的な修正教訓が必要です')
        if opinion['revision'] is not None and not clean_lessons([opinion['revision']], 1):
            raise ValueError('修正教訓に参加者IDまたは空欄があります')


async def _discuss(llm, pools, roles, rules, destination, number, reflection_inputs):
    candidates = _indexed(pools)
    rounds = {1: {}, 2: {}}
    instruction = ('終了したゲームの教訓候補について本人の役職の視点で議論します。全候補へ賛成/反対/直すと理由を返す。'
                   '直すならrevisionにscene/action/whyを示す。他はnullでもよい。教訓本文に参加者IDを入れない。'
                   '2巡目は全員の1巡目の意見を読んで再検討する。戦略の良し悪しとルール矛盾を分ける。'
                   '理由は要点一つだけを短い日本語の文で書く。修正が不要ならrevisionはnull。'
                   '思考の下書きは本文に入れず、指定JSONのみ。')
    selected = candidates
    for round_number in (1, 2):
        if round_number == 2:
            selected = [candidate for candidate in candidates if len({
                (opinion['stance'], _canonical('→'.join(opinion['revision'].values()))
                 if opinion['revision'] else '')
                for opinions in rounds[1].values() for opinion in opinions if opinion['id'] == candidate['id']}) > 1]
        if not selected:
            break
        def data_for(batch, player=next(iter(roles))):
            data = {**reflection_inputs[player], '全役職とプリセットのルール': rules,
                    '参加者と役職': roles, '巡目': round_number,
                    '候補': [{key: candidate[key] for key in ('id', 'role', 'text')} for candidate in batch]}
            if round_number == 2:
                wanted = {candidate['id'] for candidate in batch}
                data['意見の配列の順序'] = ['候補ID', '賛成/反対/直す', '理由', '修正教訓またはnull']
                data['全員の1巡目の意見'] = {person: [[opinion['id'], opinion['stance'], opinion['reason'], opinion['revision']]
                                                             for opinion in opinions if opinion['id'] in wanted]
                                         for person, opinions in rounds[1].items()}
            return data
        for player in roles:
            rounds[round_number][player] = []
            # One call per participant and round, covering every selected lesson.
            for batch_number, batch in enumerate([selected]):
                ids = [candidate['id'] for candidate in batch]
                answer = await _json_call(llm, data_for(batch, player), instruction, _opinion_schema(ids), destination,
                                          phase=f'discussion_{round_number}', player=player, number=number,
                                          batch=batch_number, max_tokens=160 + 85 * len(batch), forced=True,
                                          validate=lambda answer: _validate_opinions(answer, ids), candidate_ids=ids)
                rounds[round_number][player].extend(answer['opinions'])
                save_json(destination / 'discussion.json', {str(key): value for key, value in rounds.items()})
            _coverage(rounds[round_number][player], [candidate['id'] for candidate in selected])
    return rounds


async def _summarize(llm, pools, rounds, rules, destination, number, *, phase='summary'):
    candidates = _indexed(pools)
    instruction = ('全員の2巡の議論から教訓をまとめる。groupsの各候補IDにgroup_id/ relation/ lessonを返す。'
                   'group_idは同じ役職の代表候補ID。代表自身も自分のIDを指定し、各候補は一つの代表にだけ割り当てる。'
                   '重複はsame、対立の修正はconflict。代表のlessonにscene/action/whyを示し、他の候補のlessonはnullでよい。'
                   '参加者IDを教訓に入れない。ルールに反する教訓を正当化しない。'
                   '教訓本文は日本語で書く。思考の下書きは本文に入れず、指定JSONのみ。')
    def data_for(batch):
        ids = {candidate['id'] for candidate in batch}
        return {'全役職とプリセットのルール': rules, '候補': batch,
                '2巡の議論': {str(key): {player: [opinion for opinion in opinions if opinion['id'] in ids]
                                       for player, opinions in values.items()} for key, values in rounds.items()}}
    groups = defaultdict(list)
    for batch_number, batch in enumerate(await _batches(llm, candidates, data_for, instruction)):
        ids = [candidate['id'] for candidate in batch]
        by_id = {candidate['id']: candidate for candidate in batch}
        assignments = {candidate_id: {
            'type': 'object', 'additionalProperties': False, 'required': ['group_id', 'relation', 'lesson'],
            'properties': {'group_id': {'type': 'string', 'enum': [other for other in ids
                                                     if by_id[other]['role'] == by_id[candidate_id]['role']]},
                           'relation': {'type': 'string', 'enum': ['same', 'conflict']},
                           'lesson': {'anyOf': [LESSON_ITEM, {'type': 'null'}]}}} for candidate_id in ids}
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['groups'],
                  'properties': {'groups': {'type': 'object', 'additionalProperties': False,
                                            'required': ids, 'properties': assignments}}}
        def validate(answer):
            for group_id in {item['group_id'] for item in answer['groups'].values()}:
                representative = answer['groups'][group_id]
                if representative['group_id'] != group_id:
                    raise ValueError('まとめの代表が自分のグループに含まれていません')
                if not clean_lessons([representative['lesson']], 1):
                    raise ValueError('代表の教訓が空または参加者IDを含みます')
        answer = await _json_call(llm, data_for(batch), instruction, schema, destination, phase=phase,
                                  player='notes', number=number, batch=batch_number, forced=True,
                                  max_tokens=_output_tokens(len(batch)), validate=validate, candidate_ids=ids)
        for group_id in dict.fromkeys(item['group_id'] for item in answer['groups'].values()):
            group = answer['groups'][group_id]
            sources = [by_id[source] for source, item in answer['groups'].items() if item['group_id'] == group_id]
            relation = 'conflict' if any(item['group_id'] == group_id and item['relation'] == 'conflict'
                                         for item in answer['groups'].values()) else 'same'
            groups[sources[0]['role']].append({'indices': [source['index'] for source in sources],
                                              'relation': relation, 'text': clean_lessons([group['lesson']], 1)[0]})
    save_json(destination / ('merge.json' if phase == 'summary' else 'deduplication.json'), dict(groups))
    return consolidate_notes(pools, groups, number, maximum=None)


async def _check_notes(llm, candidates, rules, destination, number, *, prefix=''):
    facts = rules.get('ルールの事実', rule_facts()) if isinstance(rules, dict) else rules
    instruction = ('教訓の原文を番号付きルール事実に照合し、指定JSONのみ日本語で返す。'
                   'まず場面と理由を読む。受信したとされる結果の時期・対象や、禁止/不可能/確定という'
                   'ルール主張をrule_claimsに最大3点抽出する。行動が合法でも、場面や理由の誤りは棄却対象。'
                   '事実一覧の正しい文章への置き換えで原文の誤りを隠さない。'
                   '次にreasonで「原文の主張Aに対し、事実nはBなので一致/反対」と短く説明する。'
                   '最後に反対だった番号だけをcontradicting_factsに最大3件入れる。'
                   '支持や参照の番号は入れず、矛盾がなければ空配列。'
                   '名乗り自体をルール違反とする主張、騙り禁止の主張、能力が使えない初夜に本物の'
                   '結果を受信した前提、占い結果だけでCOの検証が不要という主張は、該当事実と反対になる。'
                   'ただし戦略の選択、疑い、投票誘導、嘘の名乗りは自由。'
                   '行動欄は次の実行可能な機会の方針であり、場面の時刻に実行済みとは限らない。'
                   '「初夜、守護対象未定→占いCO者を守る」は次の夜の護衛計画で、初夜実行の主張ではない。'
                   '「初夜に護衛成功を名乗る人を偽と疑う」は事実に沿う推測で、矛盾番号は空配列。'
                   '思考の下書きはJSONに混ぜない。')
    ids = [fact['id'] for fact in facts]
    schema = {'type': 'object', 'additionalProperties': False,
              'required': ['rule_claims', 'reason', 'contradicting_facts'], 'properties': {
                  'rule_claims': {'type': 'array', 'maxItems': 3, 'items': {'type': 'string', 'minLength': 1}},
                  'reason': {'type': 'string', 'minLength': 1},
                  'contradicting_facts': {'type': 'array', 'maxItems': 3,
                                        'items': {'type': 'integer', 'enum': ids}}}}
    save_json(destination / f'{prefix}rule_facts.json', facts)
    checked = []
    for batch_number, candidate in enumerate(candidates):
        parts = candidate['text'].split('→', 2)
        target = {key: candidate[key] for key in ('id', 'role', 'text')}
        data = {'ルールの事実': facts, '照合対象': [target]}
        if len(parts) == 3:
            target.update(dict(zip(('観察した場面', '次の行動の方針', '理由'), parts)))
            target['行動方針の予定時期'] = ('能力の行動方針は能力が使える夜になってからの予定であり、'
                                         '場面と同じ時刻に実行済みだとは限らない。'
                                         '場面や理由の受信済み結果・ルール断定は別に照合する。')
        else:
            target['text'] = candidate['text']
        try:
            answer = await _json_call(llm, data,
                                      instruction, schema, destination, phase='rule_check',
                                      player='notes', number=number, batch=batch_number, forced=768,
                                      max_tokens=320, candidate_ids=[candidate['id']])
            answer['contradicting_facts'] = list(dict.fromkeys(answer['contradicting_facts']))
            outcome = {**answer, 'status': '矛盾' if answer['contradicting_facts'] else '合う'}
        except _StageFailed as exception:
            outcome = {'status': None, 'reason': '照合失敗: ' + str(exception),
                       'rule_claims': [], 'contradicting_facts': []}
        checked.append({**candidate, **outcome})
    # Failed validation is distinct from the model's legitimate uncertainty.
    contradicted = {_canonical(item['text']) for item in checked if item['status'] == '矛盾'}
    accepted = defaultdict(list)
    for item in checked:
        item['adopted'] = item['status'] in {'合う', '確かめられない'}
        if not clean_lessons([item['text']], 1):
            item['adopted'], item['rejection_reason'] = False, '空の教訓または参加者IDを含む教訓'
        elif _canonical(item['text']) in contradicted:
            item['adopted'], item['rejection_reason'] = False, 'ルールと矛盾する教訓'
        elif not item['adopted']:
            item['rejection_reason'] = item['reason']
        if item['adopted']:
            accepted[item['role']].append({key: value for key, value in item.items()
                                          if key not in {'id', 'role', 'index', 'origin', 'status', 'reason', 'adopted'}} |
                                         {'rule_check': {key: item[key] for key in
                                                        ('status', 'reason', 'rule_claims', 'contradicting_facts')}})
    result = consolidate_notes(accepted, {}, number)
    kept = {role: {_canonical(item['text']) for item in rows} for role, rows in result.items()}
    for item in checked:
        if item['adopted'] and _canonical(item['text']) not in kept.get(item['role'], set()):
            item['adopted'], item['rejection_reason'] = False, '役職別・全員向けの8件上限'
    for role in {item['role'] for item in candidates}:
        result.setdefault(role, [])
    save_json(destination / f'{prefix}rule_checks.json', checked)
    save_json(destination / f'{prefix}rejected.json', [item for item in checked if not item['adopted']])
    return result


async def revalidate_notes(llm, notes, destination, number):
    """Check every existing note; a JSON destination preserves the old file."""
    target = Path(destination)
    final = target if target.suffix.lower() == '.json' else target / 'notes.json'
    directory = final.parent
    directory.mkdir(parents=True, exist_ok=True)
    prefix = final.stem + '.' if final.name != 'notes.json' else ''
    pools = {key: [_note_item(item) for item in value] for key, value in notes.items()}
    candidates = _indexed(pools, 'old')
    for candidate in candidates:
        candidate['origin'] = 'old'
    updated = await _check_notes(llm, candidates, rule_facts(), directory, number, prefix=prefix)
    save_json(final, updated)
    return updated


async def reflect_game(llm, game_dir, notes, history_dir, number):
    started = time.monotonic()
    content, preset = content_and_preset()
    record, checks = read_json(Path(game_dir) / 'server_record.json'), read_json(Path(game_dir) / 'checks.json')
    decisions = read_json(Path(game_dir) / 'decisions.json')
    destination = Path(history_dir) / f'game_{number:04d}'
    destination.mkdir(parents=True, exist_ok=True)
    final = destination / 'notes.json'
    if final.exists():
        return read_json(final)
    rules = {'ルールの事実': rule_facts(content, preset)}
    reflections, fallback = {}, False
    instruction = ('終了した人狼ゲームを本人の視点で振り返る。roleに最大3つ、generalに最大1つの教訓。'
                   'scene(場面)/action(行動)/why(理由)を具体的に書く。各項目の文字数制限はない。'
                   '今回の出来事の報告ではなく、次回同じ役職になったときに使う場面と行動に一般化する。'
                   '参加者IDを教訓に含めず、日本語で書く。参加者の主張と提示されたルールを区別する。'
                   '思考の下書きは本文に入れず、指定JSONのみ返す。')
    for player in record['roles']:
        path = destination / f'{player}.json'
        if path.exists():
            try:
                saved = read_json(path)
                if not isinstance(saved, dict) or set(saved) != {'role', 'general'}:
                    raise ValueError('保存された振り返りのJSON形式が不正です')
                for key, maximum in [('role', 3), ('general', 1)]:
                    if not isinstance(saved[key], list) or len(saved[key]) > maximum or any(not clean_lessons([item], 1) for item in saved[key]):
                        raise ValueError('保存された振り返りの教訓形式が不正です')
                reflections[player] = {key: clean_lessons(saved[key], maximum)
                                       for key, maximum in [('role', 3), ('general', 1)]}
            except Exception as exception:
                fallback = True
                reflections[player] = {'role': [], 'general': []}
                _append_record(destination / 'failures.json', {'phase': 'reflection', 'player': player, 'attempt': 0,
                               'error': str(exception), 'error_type': type(exception).__name__, 'answer': None})
            continue
        try:
            answer = await _json_call(llm, reflection_input(record, decisions, player, checks, content), instruction,
                                      LESSON_SCHEMA, destination, phase='reflection', player=player, number=number,
                                      validate=_validate_lessons)
            reflections[player] = {key: clean_lessons(answer[key], maximum)
                                   for key, maximum in [('role', 3), ('general', 1)]}
            save_json(path, reflections[player])
        except _StageFailed:
            fallback = True
            reflections[player] = {'role': [], 'general': []}
    # Existing notes are checked, never discussed again. Canonical duplicates
    # are collapsed before the semantic merge and the nine-person discussion.
    pools = consolidate_notes(merge_candidates({}, reflections, record, number), {}, number, maximum=None)
    original_pools = pools
    proposed = pools
    if not fallback and any(pools.values()):
        phase = 'deduplication'
        try:
            pools = await _summarize(llm, pools, {1: {}, 2: {}}, rules, destination, number, phase='deduplication')
            proposed = pools
            phase = 'discussion'
            reflection_inputs = {player: reflection_input(record, decisions, player, checks, content)
                                 for player in record['roles']}
            rounds = await _discuss(llm, pools, record['roles'], rules, destination, number, reflection_inputs)
            phase = 'summary'
            proposed = await _summarize(llm, pools, rounds, rules, destination, number)
        except _StageFailed:
            fallback = True
            proposed = original_pools
        except Exception as exception:
            fallback = True
            proposed = original_pools
            _append_record(destination / 'failures.json', {'phase': phase, 'attempt': 0,
                           'error': str(exception), 'error_type': type(exception).__name__, 'answer': None})
    # Even a successful summary cannot exempt an old note from rule checking.
    candidates = _indexed(proposed, 'fallback' if fallback else 'summary')
    for candidate in candidates:
        candidate['origin'] = 'fallback' if fallback else 'summary'
    old = _indexed(notes, 'old')
    for candidate in old:
        candidate['origin'] = 'old'
    candidates.extend(old)
    discussion = read_json(destination / 'discussion.json') if (destination / 'discussion.json').exists() else {}
    expected_ids = {candidate['id'] for candidate in _indexed(pools)}
    coverage = {key: {player: sorted({opinion['id'] for opinion in opinions})
                      for player, opinions in values.items()} for key, values in discussion.items()}
    second_ids = {opinion['id'] for opinions in discussion.get('2', {}).values() for opinion in opinions}
    discussion_complete = (not expected_ids or (
        set(coverage.get('1', {})) == set(record['roles']) and
        all(set(ids) == expected_ids for ids in coverage['1'].values()) and
        (not second_ids or (set(coverage.get('2', {})) == set(record['roles']) and
                           all(set(ids) == second_ids for ids in coverage['2'].values())))))
    save_json(destination / 'stages.json', {'fallback_rule_check_only': fallback,
              'discussion_complete': discussion_complete, 'discussion_coverage': coverage,
              'players': list(record['roles']), 'candidate_ids': [candidate['id'] for candidate in _indexed(pools)],
              'check_ids': [candidate['id'] for candidate in candidates]})
    updated = await _check_notes(llm, candidates, rules, destination, number)
    elapsed = round(time.monotonic() - started, 3)
    timings = read_json(destination / 'timings.json') if (destination / 'timings.json').exists() else []
    save_json(destination / 'timing_summary.json', {'wall_sec': elapsed, 'within_30_minutes': elapsed <= 1800,
              'calls': len(timings), 'phase_sec': {phase: round(sum(row['wall_sec'] for row in timings
                                                                 if row['phase'] == phase), 3)
                                                for phase in dict.fromkeys(row['phase'] for row in timings)}})
    save_json(final, updated)
    return updated
