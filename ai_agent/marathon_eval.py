"""Record-based scene scoring, game measurements and bounded lesson notes."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import re

from server.aiwolf_core import load_content, load_preset
from .claims import self_claims
from .prompts import messages
from .state import PlayerState
from .strategy import vote_pressure
from .marathon_runtime import ROOT, read_json, save_json


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
    import yaml
    from .prompts import rule_explanation
    learning = yaml.safe_load((ROOT / 'content/ai_strategies_learning.yaml').read_text(encoding='utf-8'))
    _, preset = content_and_preset()
    return {'本人': player, '本人の役職': role.id,
            '本人のルール': learning.get('role_descriptions', {}).get(role.id, role.description),
            'ゲームのルール': rule_explanation(preset.rules),
            '本人の能力': [{'id': a.id, 'available_from_night': a.available_from_night, '対象': a.target.selector} for a in role.abilities],
            '本人が受信した結果': [r for r in record.get('private_results', []) if r['player_id'] == player],
            '全員の公開された役職': {p: content.roles[r].name for p, r in record['roles'].items()},
            '勝敗': checks.get('winner'), '日別の記録': summary, '発言でのCO': spoken_co, '投票者と投票先': record.get('accepted_votes', []),
            '本人の発言': own_speech, '本人の判断': own, '他人の発言': others}


async def bounded_prompt(llm, data, instruction, reserve=2048):
    """Use the loaded model's tokenizer, trimming whole records until <=8K."""
    import copy
    data = copy.deepcopy(data)
    while True:
        prompt = [{'role': 'system', 'content': instruction}, {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}]
        # Applying the actual chat template includes special tokens and role wrappers.
        response = await llm.client.post(llm.url.replace('/v1/chat/completions', '/apply-template'), json={'messages': prompt})
        response.raise_for_status()
        formatted = response.json()['prompt']
        response = await llm.client.post(llm.url.replace('/v1/chat/completions', '/tokenize'), json={'content': formatted, 'add_special': True})
        response.raise_for_status()
        if len(response.json()['tokens']) + reserve <= 8192:
            return prompt
        fields = [k for k, v in data.items() if isinstance(v, list) and v]
        if not fields:
            raise ValueError('必須の感想戦情報が8192トークンに収まりません')
        key = max(fields, key=lambda k: len(json.dumps(data[k], ensure_ascii=False)))
        # Retain coverage from beginning to end rather than losing all early days.
        values = data[key]
        data[key] = values[::2] if len(values) > 1 else []


LESSON_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['role', 'general'],
                 'properties': {k: {'type': 'array', 'maxItems': maximum, 'items': {
                     'type': 'object', 'additionalProperties': False, 'required': ['scene', 'action', 'why'],
                     'properties': {field: {'type': 'string', 'maxLength': 25} for field in ['scene', 'action', 'why']}}}
                                for k, maximum in [('role', 3), ('general', 1)]}}


def clean_lessons(values, maximum):
    if not isinstance(values, list):
        return []
    kept = []
    for text in values:
        if isinstance(text, dict) and set(text) == {'scene', 'action', 'why'} and all(isinstance(text[k], str) for k in text):
            text = '→'.join(text[k].strip() for k in ['scene', 'action', 'why'])
        if isinstance(text, str) and len(text.strip()) <= 80 and text.count('→') == 2 and all(p.strip() for p in text.split('→')) and not re.search(r'player[\s_-]*\d+|プレイヤー[\s-]*\d+', text, re.I):
            if text.strip() not in kept:
                kept.append(text.strip())
    return kept[:maximum]


def merge_candidates(notes, reflections, record, game_number):
    pools = {key: [dict(item) for item in value] for key, value in notes.items()}
    for player, answer in reflections.items():
        for key, maximum in [(record['roles'][player], 3), ('general', 1)]:
            for text in clean_lessons(answer.get('general' if key == 'general' else 'role'), maximum):
                pools.setdefault(key, []).append({'text': text, 'games': [game_number], 'last_game': game_number})
    return pools


def consolidate_notes(pools, groups, game_number):
    """The LLM proposes source-index groups; metadata always comes from evidence."""
    result = {}
    for role, candidates in pools.items():
        grouped, used = [], set()
        for group in groups.get(role, []) if isinstance(groups, dict) else []:
            if not isinstance(group, dict):
                continue
            indices = group.get('indices', [])
            if not isinstance(indices, list) or not indices or any(type(i) is not int or not 0 <= i < len(candidates) or i in used for i in indices):
                continue
            winner = max((candidates[i] for i in indices), key=lambda c: (len(set(c['games'])), max(c['games'])))
            text = clean_lessons([group.get('text', winner['text'])], 1)
            if not text:
                continue
            used.update(indices)
            if group.get('relation') == 'conflict':
                games, chosen_text = sorted(set(winner['games'])), winner['text']
            else:
                games = sorted({n for i in indices for n in candidates[i]['games']})
                chosen_text = text[0]
            grouped.append({'text': chosen_text, 'games': games, 'support_games': len(games), 'last_game': max(games)})
        # Malformed or omitted proposals cannot silently erase existing lessons.
        grouped += [dict(c) for i, c in enumerate(candidates) if i not in used]
        by_text = {}
        for item in grouped:
            if not clean_lessons([item['text']], 1):
                continue
            canonical = re.sub(r'[\s。、「」]', '', item['text'])
            if canonical in by_text:
                item['games'] = sorted(set(item['games']) | set(by_text[canonical]['games']))
            item['support_games'], item['last_game'] = len(item['games']), max(item['games'])
            by_text[canonical] = item
        result[role] = sorted(by_text.values(), key=lambda x: (-x['support_games'], -x['last_game']))[:8]
    return result


def lesson_texts(notes):
    content, _ = content_and_preset()
    return {role: '\n'.join(item['text'] for item in [*notes.get(role, []), *notes.get('general', [])])[:800]
            for role in content.roles}


async def reflect_game(llm, game_dir, notes, history_dir, number):
    content, _ = content_and_preset()
    record, checks = read_json(Path(game_dir) / 'server_record.json'), read_json(Path(game_dir) / 'checks.json')
    decisions = read_json(Path(game_dir) / 'decisions.json')
    destination = Path(history_dir) / f'game_{number:04d}'
    destination.mkdir(parents=True, exist_ok=True)
    final = destination / 'notes.json'
    if final.exists():
        return read_json(final)
    reflections = {}
    for player in record['roles']:
        path = destination / f'{player}.json'
        if path.exists():
            reflections[player] = read_json(path)
            continue
        instruction = ('終了した人狼ゲームを本人の視点で振り返ります。短い理由だけを記録し、長い思考過程は出しません。'
                       '次に同じ役職になったときの教訓をroleに最大3つ、全員向けをgeneralに最大1つ。'
                       '各教訓はscene(どんな場面)、action(どうする)、why(なぜ)の3項目、各25字以内。参加者IDを含めない。'
                       '例:{"role":[{"scene":"疑われた時","action":"根拠を問う","why":"推理の誤りを確かめる"}],"general":[]}。指定JSONだけ。')
        prompt = await bounded_prompt(llm, reflection_input(record, decisions, player, checks, content), instruction,
                                      reserve=llm.thinking_tokens + 896)
        answer = json.loads(await llm.complete(lambda: prompt, player_id=player, purpose='reflection', seed=number,
                                               schema=LESSON_SCHEMA, max_tokens=768))
        reflections[player] = {k: clean_lessons(answer.get(k), n) for k, n in [('role', 3), ('general', 1)]}
        save_json(path, reflections[player])
    pools = merge_candidates(notes, reflections, record, number)
    instruction = ('役職別の教訓をまとめます。重複は同じindicesの組へ統合し、矛盾は支持したゲーム数が多い根拠の内容に揃えてください。'
                   '支持が同数なら新しい根拠を優先。各役職とgeneralそれぞれ最大8項目、各80字以内、IDなし。'
                   '根拠のindexは候補に表示した番号です。返すJSONは役職ID:{indices:根拠のindex配列,relation:sameまたはconflict}の配列。'
                   '同内容の統合はsame、矛盾する選択肢はconflictにする。')
    indexed_pools = {role: [{'index': i, **item} for i, item in enumerate(rows)] for role, rows in pools.items()}
    prompt = await bounded_prompt(llm, indexed_pools, instruction, reserve=llm.thinking_tokens + 2400)
    schema = {'type': 'object', 'additionalProperties': False, 'required': list(pools), 'properties': {
        role: {'type': 'array', 'maxItems': 8, 'items': {'type': 'object', 'additionalProperties': False,
              'required': ['indices', 'relation'], 'properties': {
              'relation': {'type': 'string', 'enum': ['same', 'conflict']},
              'indices': {'type': 'array', 'minItems': 1, 'items': {'type': 'integer', 'minimum': 0, 'maximum': len(rows)-1}}}}}
        for role, rows in pools.items() if rows}}
    schema['required'] = list(schema['properties'])
    answer = json.loads(await llm.complete(lambda: prompt, player_id='notes', purpose='notes', seed=number,
                                           schema=schema, max_tokens=2200))
    save_json(destination / 'merge.json', answer)
    updated = consolidate_notes(pools, answer, number)
    save_json(final, updated)
    return updated
