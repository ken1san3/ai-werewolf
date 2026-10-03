"""One read-only Codex choice with a validated deterministic fallback."""
import json
import math
from pathlib import Path
import shutil
import subprocess
import time

from .marathon_runtime import read_json, save_json


def series_cost(row, learning=True):
    # Observed game plus conservative reflection and reload allowance.
    game = row['game']['wall_sec']
    return game + (row.get('reflection_estimate_sec', max(120, game * .2)) if learning else 0) + row.get('load_sec', 60)


def validate_choice(choice, rows, remaining):
    if not isinstance(choice, dict) or set(choice) != {'learning', 'control', 'reason'}:
        return False
    learning = choice['learning']
    if not isinstance(learning, list) or not 1 <= len(learning) <= 3 or not isinstance(choice['reason'], str):
        return False
    indexed = {r['id']: r for r in rows if r.get('status') == 'completed' and r.get('game', {}).get('completed')}
    ids, models, total = [], set(), 0
    for selected in learning:
        if not isinstance(selected, dict) or set(selected) != {'setting', 'rate'} or selected['setting'] not in indexed:
            return False
        row = indexed[selected['setting']]
        rate = selected['rate']
        if type(rate) not in (float, int) or not math.isfinite(rate) or not .005 <= rate <= 1 or rate > row['rate']:
            return False
        if row['model'] in models:
            return False
        models.add(row['model'])
        ids.append(row['id'])
        total += series_cost(row) * row['rate'] / rate
    if choice['control'] not in ids:
        return False
    selected = next(s for s in learning if s['setting'] == choice['control'])
    row = indexed[selected['setting']]
    total += series_cost(row, False) * row['rate'] / selected['rate']
    return total * 8 <= remaining * .9


def fallback_choice(rows, remaining):
    ranked = sorted([r for r in rows if r.get('status') == 'completed' and r.get('game', {}).get('completed')],
                    key=lambda r: (-r['scene']['accuracy'], series_cost(r)))
    chosen, seen = [], set()
    for row in ranked:
        if row['model'] in seen:
            continue
        candidate = {'learning': [*chosen, {'setting': row['id'], 'rate': row['rate']}],
                     'control': chosen[0]['setting'] if chosen else row['id'],
                     'reason': '場面正答率を優先。全系列8ゲームを回せる設定の中で、各モデルの最良の思考設定を選択'}
        if validate_choice(candidate, rows, remaining):
            chosen = candidate['learning']
            seen.add(row['model'])
        if len(chosen) == 2:
            return candidate
    if chosen:
        return {'learning': chosen, 'control': chosen[0]['setting'], 'reason': '残り時間で8ゲームを回せる最上位モデルを選択'}
    return {'learning': [], 'control': None, 'reason': '完走済み設定で8ゲーム以上を回せる時間が残っていません'}


def choose_checkpoint(rows, remaining, directory, *, call=None, timeout=300):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / 'choice.json'
    if destination.exists():
        return read_json(destination)
    ids = [r['id'] for r in rows if r.get('status') == 'completed' and r.get('game', {}).get('completed')]
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['learning', 'control', 'reason'],
              'properties': {'learning': {'type': 'array', 'minItems': 1, 'maxItems': 3, 'items': {
                  'type': 'object', 'additionalProperties': False, 'required': ['setting', 'rate'],
                  'properties': {'setting': {'type': 'string', 'enum': ids}, 'rate': {'type': 'number', 'minimum': .005, 'maximum': 1}}}},
                  'control': {'type': 'string', 'enum': ids}, 'reason': {'type': 'string'}}}
    save_json(directory / 'schema.json', schema)
    save_json(directory / 'aggregate.json', {'remaining_sec': remaining, 'settings': rows})
    prompt = ('読み取り専用のモデル選定です。ファイル変更、git操作、他のチャットへの連絡は禁止。'
              'aggregate.jsonを読み、そこにある書き起こしの中身も読み、指定のJSONだけを返してください。'
              '場面テストの正答率を最も重く見る。学習モデル1〜3（各モデルの思考設定も選ぶ）、'
              '対照はそのどれか1つ。時間倍率は前半と同じか遅くする。'
              '全系列は順番に同じ1台のLLMで回す。残り時間で各系列8ゲーム以上が条件。'
              'ゲーム時間に感想戦・読み込み時間を加え、残り時間の10%を余裕に残す。理由は短く日本語で。')
    answer, error = None, None
    started = time.monotonic()
    # Mark before dispatch: even a crash must not trigger a second paid Codex call.
    attempted = directory / 'attempted.json'
    try:
        if attempted.exists():
            raise RuntimeError('前回のCodex呼び出し結果を回収できなかったため基準へ切り替え')
        save_json(attempted, {'timeout': timeout})
        if call:
            answer = call(prompt, schema)
        else:
            codex = shutil.which('codex.cmd') or shutil.which('codex')
            if not codex or not ids:
                raise RuntimeError('Codexまたは完走済み設定がありません')
            # Invoke the installed native CLI directly: timeout kills that process,
            # not just npm's wrapper while leaving inference running underneath.
            package = Path(codex).parent / 'node_modules/@openai/codex'
            candidates = [package / 'node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe',
                          package / 'vendor/x86_64-pc-windows-msvc/bin/codex.exe']
            codex = next((str(p) for p in candidates if p.exists()), codex)
            command = [codex, 'exec', '--sandbox', 'read-only', '--ephemeral', '--ignore-rules', '--ignore-user-config', '-c', 'approval_policy="never"',
                       '--skip-git-repo-check', '--color', 'never', '--cd', str(directory.resolve()),
                       '--output-schema', str((directory / 'schema.json').resolve()),
                       '--output-last-message', str((directory / 'answer.json').resolve()), '-']
            with (directory / 'codex.log').open('w', encoding='utf-8') as log:
                # .cmd uses cmd only for this fixed executable, never for file operations.
                result = subprocess.run(command, input=prompt, text=True, encoding='utf-8',
                                        stdout=log, stderr=log, timeout=timeout,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise RuntimeError(f'Codex終了コード{result.returncode}')
            answer = read_json(directory / 'answer.json')
        if not validate_choice(answer, rows, max(0, remaining - (time.monotonic()-started))):
            raise ValueError('選択肢・時間倍率・8ゲームの条件に合わない回答')
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exception:
        error = str(exception)
    choice = {'source': 'codex' if not error else 'criteria',
              'decision': answer if not error else fallback_choice(rows, max(0, remaining - (time.monotonic()-started))),
              'error': error, 'elapsed_sec': round(time.monotonic()-started, 2)}
    save_json(destination, choice)
    return choice
