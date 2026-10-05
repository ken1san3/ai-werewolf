"""Read-only replay data extracted strictly from public recording fields."""
from __future__ import annotations

import ast
import json
import math
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
PHASES = {'setup', 'night0', 'dawn', 'day', 'vote', 'runoff', 'execution', 'night', 'ended'}
RESULTS = {'won', 'lost', 'draw'}
PUBLIC_CAUSES = {'lynched', 'sudden_death', 'died_in_night', 'died_in_day'}
PUBLIC_KINDS = {'GAME_CREATED', 'PHASE_STARTED', 'CO_DECLARED', 'CO_REPORTED', 'PLAYER_DIED',
                'VOTE_RESOLVED', 'GAME_ENDED', 'DAY_EXTENDED', 'DAY_SHORTENED',
                'PUBLIC_NOTIFY', 'TIE_RESOLVED_RANDOM'}
TIMESTAMP = re.compile(r'^\[(\d+(?:\.\d+)?)\s*s\]\s*(.*)$')
CHAT_LINE = re.compile(r'^(?:CHAT\s+)?(player-\d+):\s*(.*)$')
EVENT_LINE = re.compile(r'^([A-Z][A-Z_]+):\s*(\{.*\})\s*$')
SETTING_KEYS = ('seed', 'clock_rate', 'day_seconds', 'vote_seconds', 'night_seconds',
                'silence_after_dawn_seconds')


def _number(value, *, positive=False):
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number if number > 0 or (not positive and number == 0) else None


def _is_one_of(value, allowed):
    return isinstance(value, str) and value in allowed


def _id(value):
    return value if isinstance(value, str) and value and len(value) <= 128 and not any(ord(char) < 32 for char in value) else None


def _day(value):
    return value if type(value) is int and value >= 0 else None


def _read_json(path):
    with path.open('r', encoding='utf-8-sig') as stream:
        return json.load(stream)


def _names():
    """Names come directly from safe YAML, never from a private-state helper."""
    names = {}
    for path in sorted((ROOT / 'content' / 'roles').glob('*.yaml')):
        try:
            definition = yaml.safe_load(path.read_text(encoding='utf-8-sig'))
            if isinstance(definition, dict) and _id(definition.get('id')) and isinstance(definition.get('name'), str):
                names[definition['id']] = definition['name']
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
    return names


def _visibility_is_public(value):
    return isinstance(value, dict) and value.get('visibility', 'public') == 'public' and not value.get('recipient_player_id')


def _player_ids(value):
    return [player for player in value if _id(player)] if isinstance(value, list) else []


def _role_results(source, results, names):
    output = {}
    if not isinstance(source, dict):
        return output
    for player, value in source.items():
        if not _id(player):
            continue
        role = value.get('role') if isinstance(value, dict) else value
        name = names.get(role) if isinstance(role, str) else None
        if name is None and isinstance(role, str) and role in names.values():
            name = role
        if name is None:
            continue
        result = results.get(player, value.get('result') if isinstance(value, dict) else None)
        output[player] = {'role': name, 'result': result if _is_one_of(result, RESULTS) else 'unknown'}
    return output


def _public_payload(kind, payload, names, ending_roles):
    """Each kind explicitly constructs its payload; source dictionaries never pass through."""
    if not _visibility_is_public(payload):
        return None
    output = {}
    if _day(payload.get('day')) is not None:
        output['day'] = payload['day']
    if _is_one_of(payload.get('phase'), PHASES):
        output['phase'] = payload['phase']
    if kind == 'GAME_CREATED':
        players = payload.get('players', [])
        if not isinstance(players, list):
            return None
        output['players'] = [{'player_id': player['player_id']} for player in players
                             if isinstance(player, dict) and _id(player.get('player_id'))]
        return output if output['players'] else None
    if kind == 'PHASE_STARTED':
        return output if 'phase' in output else None
    if kind in {'CO_DECLARED', 'CO_REPORTED', 'PLAYER_DIED'}:
        player = _id(payload.get('player_id'))
        if player is None:
            return None
        output['player_id'] = player
        if kind == 'CO_DECLARED':
            claimed = payload.get('claimed_role_id')
            if not isinstance(claimed, str) or claimed not in names:
                return None
            output['claimed_role_id'] = claimed
            if isinstance(payload.get('comment'), str):
                output['comment'] = payload['comment']
        elif kind == 'CO_REPORTED':
            for key in ('kind', 'claimed_result'):
                if not isinstance(payload.get(key), str):
                    return None
                output[key] = payload[key]
            target = _id(payload.get('target_player_id'))
            if target is None:
                return None
            output['target_player_id'] = target
        elif _is_one_of(payload.get('public_cause'), PUBLIC_CAUSES):
            output['public_cause'] = payload['public_cause']
        return output
    if kind == 'VOTE_RESOLVED':
        if not _is_one_of(payload.get('result'), {'lynch', 'no_lynch', 'runoff'}) or not isinstance(payload.get('tallies'), dict):
            return None
        output.update(result=payload['result'],
                      tallies={player: count for player, count in payload['tallies'].items()
                               if _id(player) and type(count) is int and count >= 0},
                      lynched_player_id=_id(payload.get('lynched_player_id')),
                      runoff_candidate_player_ids=_player_ids(payload.get('runoff_candidate_player_ids', [])))
        return output
    if kind == 'GAME_ENDED':
        if not _is_one_of(payload.get('outcome'), {'team_victory', 'draw'}):
            return None
        results = payload.get('player_results', {})
        results = {player: result for player, result in results.items()
                   if _id(player) and _is_one_of(result, RESULTS)} if isinstance(results, dict) else {}
        output.update(outcome=payload['outcome'], winner_team=_id(payload.get('winner_team')), player_results=results)
        # The top-level true assignment is accessed exclusively at this event.
        assignments = ending_roles() if ending_roles is not None else payload.get('roles', {})
        if not assignments:
            assignments = payload.get('roles', {})
        output['roles'] = _role_results(assignments, results, names)
        return output
    if kind in {'DAY_EXTENDED', 'DAY_SHORTENED'}:
        if kind == 'DAY_EXTENDED' and _day(payload.get('extensions_used')) is not None:
            output['extensions_used'] = payload['extensions_used']
        return output if 'phase' in output else None
    if kind == 'PUBLIC_NOTIFY':
        notify = _id(payload.get('notify_id'))
        return {'notify_id': notify, **output} if notify else None
    if kind == 'TIE_RESOLVED_RANDOM':
        selected = _id(payload.get('selected_player_id'))
        if selected is None:
            return None
        return {**output, 'selected_player_id': selected,
                'candidate_player_ids': _player_ids(payload.get('candidate_player_ids', []))}
    return None


def _normalize(rows, names, ending_roles=None):
    events = []
    for row in rows:
        if not _visibility_is_public(row) or row.get('channel', 'public') != 'public':
            continue
        t = _number(row.get('t'))
        if t is None:
            continue
        kind = row.get('kind')
        if kind == 'chat':
            if row.get('channel') != 'public':
                continue
            message = row.get('message')
            if not _visibility_is_public(message):
                continue
            player, text = _id(message.get('player_id')), message.get('message')
            if player is None or not isinstance(text, str) or not text.strip():
                continue
            payload = {'player_id': player, 'message': text}
        elif _is_one_of(kind, PUBLIC_KINDS):
            payload = _public_payload(kind, row.get('payload'), names, ending_roles)
            if payload is None:
                continue
        else:
            continue
        events.append({'t': t, 'kind': kind, 'payload': payload})
    return events


def _transcript_rows(text):
    rows, synthetic = [], False
    for line in text.splitlines():
        matched = TIMESTAMP.match(line.strip())
        body = matched[2] if matched else line.strip()
        t = float(matched[1]) if matched else float(len(rows))
        chat = CHAT_LINE.match(body)
        event = EVENT_LINE.match(body)
        if chat and chat[2].strip():
            rows.append({'t': t, 'kind': 'chat', 'channel': 'public',
                         'message': {'player_id': chat[1], 'message': chat[2]}})
        elif event and event[1] in PUBLIC_KINDS:
            try:
                payload = json.loads(event[2])
            except (ValueError, RecursionError):
                continue
            rows.append({'t': t, 'kind': event[1], 'payload': payload})
        else:
            continue
        synthetic |= matched is None
    return rows, synthetic


def _log_rows(text):
    rows, synthetic = [], False
    for line in text.splitlines():
        matched = TIMESTAMP.match(line.strip())
        body = matched[2] if matched else line.strip()
        t = float(matched[1]) if matched else float(len(rows))
        if body.startswith('PHASE_STARTED '):
            try:
                payload = ast.literal_eval(body[len('PHASE_STARTED '):])
            except (ValueError, SyntaxError, RecursionError, MemoryError):
                continue
            rows.append({'t': t, 'kind': 'PHASE_STARTED', 'payload': payload})
        elif body.startswith('CHAT '):
            chat = CHAT_LINE.match(body)
            if chat is None or not chat[2].strip():
                continue
            rows.append({'t': t, 'kind': 'chat', 'channel': 'public',
                         'message': {'player_id': chat[1], 'message': chat[2]}})
        else:
            continue
        synthetic |= matched is None
    return rows, synthetic


def _checks(directory, warnings):
    """Only the explicitly permitted completion and timing metadata is retained."""
    path = directory / 'checks.json'
    if not path.is_file():
        return {}, None, None, None
    try:
        data = _read_json(path)
        if not isinstance(data, dict):
            raise ValueError('invalid checks')
    except (OSError, UnicodeError, ValueError, RecursionError):
        warnings.append('checks.jsonの時間・完了情報を読み取れません。')
        return {}, None, None, None
    source = data.get('settings', {})
    source = source if isinstance(source, dict) else {}
    settings = {}
    for key in SETTING_KEYS:
        value = source.get(key, data.get(key))
        if key == 'seed':
            if type(value) is int:
                settings[key] = value
        elif _number(value) is not None and (key != 'clock_rate' or value > 0):
            settings[key] = value
    completed = data.get('completed') if type(data.get('completed')) is bool else None
    crashes = data.get('crashes') if type(data.get('crashes')) is int and data['crashes'] >= 0 else None
    return settings, completed, crashes, _number(data.get('wall_sec'))


def _phase_ends(events, settings, *, synthetic=False):
    phases = [event for event in events if event['kind'] == 'PHASE_STARTED']
    seconds = {'night0': 'night_seconds', 'dawn': 'silence_after_dawn_seconds', 'day': 'day_seconds',
               'vote': 'vote_seconds', 'runoff': 'vote_seconds', 'night': 'night_seconds'}
    rate = _number(settings.get('clock_rate'), positive=True)
    for index, event in enumerate(phases):
        if synthetic:
            event['payload']['end_t'] = None
            continue
        end = phases[index + 1]['t'] if index + 1 < len(phases) else None
        if end is None:
            duration = _number(settings.get(seconds.get(event['payload']['phase'])))
            if duration is not None and rate is not None:
                end = event['t'] + duration / rate
        event['payload']['end_t'] = end


def load_replay(directory: Path) -> dict:
    """Read public rows, an old public transcript, or public progress-log lines."""
    directory = Path(directory)
    output = {'version': 1, 'title': directory.name, 'players': [], 'events': [], 'duration': 0.0,
              'mode': 'none', 'warning': '', 'completed': False, 'days': 0, 'public_messages': 0,
              'winner': None, 'status': '失敗', 'reason': '再生できる公開記録がありません。',
              'synthetic_timing': False, 'settings': {}}
    names, warnings, errors = _names(), [], []
    settings, checked_completed, crashes, wall = _checks(directory, warnings)
    output['settings'] = settings
    if 'seed' in settings:
        output['seed'] = settings['seed']
    if crashes is not None:
        output['crashes'] = crashes
    if wall is not None:
        output['wall_sec'] = wall
    sources = [('server_record', directory / 'server_record.json'),
               ('transcript', directory / 'transcript.md'),
               ('log', directory.parent / (directory.name + '.log'))]
    for mode, path in sources:
        if not path.is_file():
            continue
        try:
            if mode == 'server_record':
                record = _read_json(path)
                if not isinstance(record, dict) or not isinstance(record.get('rows'), list):
                    raise ValueError('invalid public rows')
                events = _normalize(record['rows'], names, lambda: record.get('roles', {}))
                synthetic = False
            else:
                rows, synthetic = (_transcript_rows if mode == 'transcript' else _log_rows)(path.read_text(encoding='utf-8-sig'))
                events = _normalize(rows, names)
            if not events:
                errors.append(path.name + 'に読み取れる公開イベントがありません。')
                continue
        except (OSError, UnicodeError, ValueError, RecursionError):
            errors.append(path.name + 'を読み取れません。')
            continue
        if synthetic:
            for index, event in enumerate(events):
                event['t'] = float(index)
            warnings.append('時刻の記録がないため、公開イベント順を仮に1秒間隔で再生します。実際の時間とは異なります。')
        if mode == 'log':
            warnings.append('生死・正式CO・票数は進行ログに未記録です。')
        events.sort(key=lambda event: event['t'])
        output.update(mode=mode, events=events, synthetic_timing=synthetic)
        break
    if not output['events']:
        output['reason'] = ' '.join(errors) if errors else output['reason']
        output['warning'] = ' '.join(warnings)
        return output
    if errors:
        warnings.extend(errors)
    players, day = set(), 0
    ending = None
    for event in output['events']:
        payload = event['payload']
        if _day(payload.get('day')) is not None:
            day = payload['day']
        payload['day'] = day
        output['days'] = max(output['days'], day)
        if event['kind'] == 'GAME_CREATED':
            players.update(player['player_id'] for player in payload['players'])
        for key in ('player_id', 'target_player_id', 'lynched_player_id', 'selected_player_id'):
            if _id(payload.get(key)):
                players.add(payload[key])
        if event['kind'] == 'VOTE_RESOLVED':
            players.update(payload['tallies'])
            players.update(payload['runoff_candidate_player_ids'])
        elif event['kind'] == 'TIE_RESOLVED_RANDOM':
            players.update(payload['candidate_player_ids'])
        elif event['kind'] == 'GAME_ENDED':
            ending = payload
            players.update(payload['player_results'])
            players.update(payload['roles'])
    _phase_ends(output['events'], settings, synthetic=output['synthetic_timing'])
    output['players'] = [{'id': player} for player in sorted(players)]
    output['public_messages'] = sum(event['kind'] == 'chat' for event in output['events'])
    output['duration'] = max(event['t'] for event in output['events'])
    if wall is not None and not output['synthetic_timing']:
        output['duration'] = max(output['duration'], wall)
    output['completed'] = ending is not None or checked_completed is True
    output['winner'] = ending['winner_team'] if ending else None
    if output['completed']:
        output.update(status='完走', reason='')
        if ending is None or not ending['roles']:
            warnings.append('終了時の役職は未記録です。')
        if ending is None:
            warnings.append('終了イベントが未記録のため勝敗は表示できません。')
    else:
        output.update(status='途中', reason='終了イベントが未記録の途中ゲームです。')
    if crashes:
        output.update(status='失敗', reason=f'エージェントの失敗が{crashes}件記録されています。公開された進行は再生できます。')
    output['warning'] = ' '.join(warnings)
    return output
