"""Resumable serial local-model experiment, independent of the Codex session."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import csv
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from .marathon_checkpoint import choose_checkpoint, series_cost
from .marathon_eval import content_and_preset, game_metrics, lesson_texts, reflect_game, solve_scenes
from .marathon_runtime import Awake, MODEL_MAP, ModelServer, ROOT, check_resources, make_llm, powershell, process_inventory, read_json, save_json, settings

LOG = logging.getLogger(__name__)


def failure_count(units, key):
    count = 0
    for unit in reversed(units):
        if unit['key'] != key:
            continue
        if unit['status'] == 'completed':
            break
        if unit['status'] == 'failed':
            count += 1
    return count


def affordable(remaining, estimate):
    return remaining > estimate * 1.2 + 60


def note_snapshot(history, number):
    """Prefer a checked revision within the same completed game's history."""
    directory = Path(history) / f'game_{number:04d}'
    versions = []
    for path in directory.glob('notes.v*.json'):
        version = path.name[len('notes.v'):-len('.json')]
        if version.isdigit():
            versions.append((int(version), path))
    return max(versions)[1] if versions else directory / 'notes.json'


def extend_deadline(directory, hours, *, now=None):
    """Back up state, then extend its deadline without launching any process."""
    if not math.isfinite(hours) or hours <= 0:
        raise ValueError('延長する時間は正の有限値を指定してください')
    path = Path(directory) / 'state.json'
    state = read_json(path)
    if state['phase'] != 'B' or state.get('finished'):
        raise ValueError('中断中の後半（段階B）の締切だけを延長できます')
    stamp = time.time_ns()
    backup = path.with_name(f'state.before_extend_{stamp}.json')
    # Exclusive creation preserves every previous backup as well as the bytes.
    with backup.open('xb') as file:
        file.write(path.read_bytes())
    current = time.time() if now is None else now
    state['deadline'] = max(state['deadline'], current) + hours * 3600
    save_json(path, state)
    return {'backup': str(backup.resolve()), 'deadline': state['deadline']}


class LocalBackend:
    def __init__(self, directory, port):
        self.directory, self.port = Path(directory), port
        self.server = ModelServer(directory, port)
        self.deadline = float('inf')
        self.game_owner_path = self.directory / 'game_owner.json'

    def recover(self):
        if self.game_owner_path.exists():
            owner = read_json(self.game_owner_path)
            if owner:
                raw = powershell(f'Get-CimInstance Win32_Process -Filter "ProcessId = {int(owner["pid"])}" | Select-Object ProcessId,CreationDate,ExecutablePath | ConvertTo-Json -Compress')
                current = json.loads(raw) if raw else None
                if current and current['CreationDate'] == owner['created'] and current['ExecutablePath'] == owner['path']:
                    powershell(f'Stop-Process -Id {int(owner["pid"])} -Force -ErrorAction Stop')
            save_json(self.game_owner_path, {})
        self.server.recover()

    def prepare(self, setting):
        load = self.server.start(MODEL_MAP[setting['model']])
        async def smoke():
            llm = make_llm(setting, self.port)
            try:
                answer = await llm.complete(lambda: [{'role': 'user', 'content': '日本語で「準備完了」とだけ答えてください。'}],
                                            player_id='smoke', purpose='smoke', seed=1, max_tokens=32)
                if not answer:
                    raise RuntimeError('試しの呼び出しが空です')
                return llm.calls[-1]
            finally:
                await llm.close()
        async def bounded_smoke():
            return await asyncio.wait_for(smoke(), min(300, self.deadline - time.time()))
        stat = asyncio.run(bounded_smoke())
        save_json(self.directory / ('smoke_' + setting['id'].replace('/', '_') + '.json'), stat)
        return load

    def scenes(self, setting, destination, scenes, repeats=5):
        async def work():
            llm = make_llm(setting, self.port)
            try:
                return await asyncio.wait_for(solve_scenes(llm, scenes, destination, repeats=repeats),
                                              min(self.deadline - time.time(), max(300, setting.get('expected_scene_sec', len(scenes)*repeats*5/setting['rate']) * 2.5)))
            finally:
                await llm.close()
        return asyncio.run(work())

    def game(self, setting, seed, destination, timeout, *, learning_profile=False, lessons=None, phases=None):
        timeout = min(timeout, self.deadline - time.time())
        if timeout <= 30:
            raise TimeoutError('ゲームを開始できる時間が残っていません')
        request_path = destination.parent / (destination.name + '_request.json')
        request = {'setting': setting, 'port': self.port, 'seed': seed, 'output': str(destination.resolve()),
                   'timeout': timeout - 15, 'learning_profile': learning_profile, 'lessons': lessons or {}}
        request.update(phases or {})
        save_json(request_path, request)
        with (destination.parent / (destination.name + '.log')).open('a', encoding='utf-8') as log:
            proc = subprocess.Popen([sys.executable, '-m', 'ai_agent.marathon_worker', str(request_path.resolve())],
                                    cwd=ROOT, stdout=log, stderr=log,
                                    env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            started, next_health, bad_health = time.monotonic(), time.monotonic() + 30, 0
            try:
                raw = powershell(f'Get-CimInstance Win32_Process -Filter "ProcessId = {proc.pid}" | Select-Object ProcessId,CreationDate,ExecutablePath | ConvertTo-Json -Compress')
                owner = json.loads(raw) if raw else None
                if owner:
                    save_json(self.game_owner_path, {'pid': owner['ProcessId'], 'created': owner['CreationDate'], 'path': owner['ExecutablePath']})
                while proc.poll() is None:
                    if time.monotonic() - started >= timeout:
                        raise TimeoutError('ゲームの上限時間を超えました')
                    if time.monotonic() >= next_health:
                        bad_health = 0 if self.server.healthy() else bad_health + 1
                        next_health = time.monotonic() + 15
                        if bad_health >= 2:
                            raise RuntimeError('LLMのヘルスチェックが連続して失敗しました')
                    time.sleep(1)
                if proc.returncode:
                    raise RuntimeError(f'ゲーム子プロセス終了コード{proc.returncode}')
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=10)
                save_json(self.game_owner_path, {})
        checks = read_json(destination / 'checks.json')
        if not checks['completed'] or checks['crashes']:
            raise RuntimeError('ゲームが完走しませんでした')
        return {'completed': True, 'wall_sec': checks['wall_sec'], 'public_messages': checks['public_messages'],
                'llm_calls': len(checks['llm_calls']), 'metrics': checks['metrics'],
                'mechanical': checks['mechanical_conditions'], 'transcript': str((destination / 'transcript.md').resolve()),
                'experiment': read_json(destination / 'experiment_metrics.json')}

    def reflect(self, setting, game_dir, notes, history, number):
        async def work():
            llm = make_llm(setting, self.port)
            destination = Path(history) / f'game_{number:04d}'
            try:
                # A failed or unexpectedly slow reflection must not stop the run.
                return await asyncio.wait_for(reflect_game(llm, game_dir, notes, history, number), 1800)
            except Exception as exception:
                LOG.exception('感想戦を飛ばして、直前の照合済みノートを維持します')
                save_json(destination / 'pipeline_failure.json', {
                    'stage': 'reflection_pipeline', 'error': f'{type(exception).__name__}: {exception}',
                    'at': time.time(), 'fallback': 'previous_checked_notes',
                })
                if not (destination / 'notes.json').exists():
                    save_json(destination / 'notes.json', notes)
                return notes
            finally:
                save_json(destination / 'calls.json', llm.calls)
                await llm.close()
        return asyncio.run(work())

    def failed(self):
        self.server.stop()

    def close(self):
        self.server.stop()


class Marathon:
    def __init__(self, directory, backend, *, hours=38, now=time.time, configurations=None, checkpoint=choose_checkpoint):
        self.directory, self.backend, self.now = Path(directory), backend, now
        self.path, self.checkpoint = self.directory / 'state.json', checkpoint
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.state = read_json(self.path)
        else:
            self.state = {'version': 1, 'started': now(), 'deadline': now() + hours * 3600,
                          'phase': 'A', 'settings': configurations or settings(), 'a': [], 'units': [],
                          'series': [], 'round': 1, 'active': None, 'finished': False, 'failures': []}
            self.save()

        if hasattr(backend, 'deadline'):
            backend.deadline = self.state['deadline']
        self.state.setdefault('game_paths', {})

    def save(self):
        self.state['updated'] = self.now()
        save_json(self.path, self.state)

    def remaining(self):
        return max(0, self.state['deadline'] - self.now())

    def unit(self, key, setting, function):
        LOG.info('開始: %s (%s)', key, setting['id'])
        # On resume a finished worker's artifacts are recovered by the caller.
        self.state['active'] = {'key': key, 'setting': setting['id'], 'started': self.now()}
        self.save()
        try:
            value = function()
            self.state['units'].append({'key': key, 'status': 'completed'})
            LOG.info('完了: %s', key)
            return value
        except Exception as exception:
            error = f'{type(exception).__name__}: {exception}'
            LOG.exception('失敗: %s', key)
            self.state['units'].append({'key': key, 'status': 'failed'})
            self.state['failures'].append({'key': key, 'error': error, 'at': self.now()})
            try:
                self.backend.failed()
            except Exception:
                LOG.exception('LLM停止の再試行は次の項目で行います')
            return None
        finally:
            self.state['active'] = None
            self.save()

    def existing_game(self, destination):
        required = ['checks.json', 'server_record.json', 'decisions.json', 'transcript.md']
        if all((destination / name).exists() for name in required):
            try:
                checks = read_json(destination / 'checks.json')
                record = read_json(destination / 'server_record.json')
                read_json(destination / 'decisions.json')
            except (OSError, ValueError):
                return None
            if checks['completed'] and not checks['crashes']:
                metrics_path = destination / 'experiment_metrics.json'
                if not metrics_path.exists():
                    content, _ = content_and_preset()
                    save_json(metrics_path, game_metrics(record, checks, content))
                return {'completed': True, 'wall_sec': checks['wall_sec'], 'public_messages': checks['public_messages'],
                        'llm_calls': len(checks['llm_calls']), 'metrics': checks['metrics'],
                        'mechanical': checks['mechanical_conditions'], 'transcript': str((destination / 'transcript.md').resolve()),
                        'experiment': read_json(metrics_path)}
        return None

    def play(self, key, setting, seed, estimate, *, learning_profile=False, lessons=None):
        previous = Path(self.state['game_paths'].get(key, self.directory / 'games' / key))
        recovered = self.existing_game(previous)
        if recovered:
            return recovered
        attempt = sum(u['key'] == key and u['status'] == 'failed' for u in self.state['units'])
        destination = self.directory / 'games' / (key if attempt == 0 else f'{key}_retry{attempt}')
        interrupted = (destination.exists()
                       or destination.with_name(destination.name + '_request.json').exists()
                       or destination.with_name(destination.name + '.log').exists())
        if interrupted:
            recovered = self.existing_game(destination)
            if recovered:
                return recovered
            # Keep interrupted, untracked records; choose a fresh path, never delete.
            destination = self.directory / 'games' / (destination.name + '_' + datetime.now().strftime('%H%M%S_%f'))
        self.state['game_paths'][key] = str(destination.resolve())
        self.save()
        return self.backend.game(setting, seed, destination, min(estimate * 2.5 + 30, self.remaining()),
                                 learning_profile=learning_profile, lessons=lessons)

    def run_a(self, *, reserve_b=True):
        reserve = 12 * 3600 if reserve_b else 0
        if hasattr(self.backend, 'deadline'):
            self.backend.deadline = self.state['deadline'] - reserve
        scenes = read_json(ROOT / 'content/marathon_scenes.json')
        done = {r['id'] for r in self.state['a']}
        for index, setting in enumerate(self.state['settings']):
            if setting['id'] in done:
                continue
            estimate = setting.get('expected_game_sec', 750/setting['rate']) + setting.get('expected_scene_sec', len(scenes)*5*5/setting['rate']) + 90
            pending = self.state['settings'][index+1:]
            evaluated = {r['model'] for r in self.state['a'] if r.get('status') == 'completed'} | {setting['model']}
            reserve_models = {}
            for future in pending:
                if future['model'] not in evaluated:
                    cost = future.get('expected_game_sec', 750/future['rate']) + future.get('expected_scene_sec', 300/future['rate']) + 90
                    reserve_models[future['model']] = min(cost, reserve_models.get(future['model'], float('inf')))
            if not setting.get('supported', True) or self.remaining() - estimate - sum(reserve_models.values()) < reserve:
                LOG.info('前半を見送り: %s（後半と未評価モデルの時間を確保）', setting['id'])
                self.state['a'].append({**setting, 'status': 'skipped', 'reason': '未評価モデルの最速設定と後半12時間を残すため、または試運転で未対応'})
                self.save()
                continue
            key = 'A_' + setting['id'].replace('/', '_')
            def work():
                load = self.backend.prepare(setting)
                score = self.backend.scenes(setting, self.directory / 'scenes' / f'{key}.json', scenes)
                game = self.play(key, setting, 1, 750 / setting['rate'])
                return {**setting, 'status': 'completed', 'load_sec': load, 'scene': score, 'game': game}
            row = self.unit(key, setting, work)
            if row is None and failure_count(self.state['units'], key) < 2 and self.remaining() > reserve + estimate:
                row = self.unit(key, setting, work)
            self.state['a'].append(row or {**setting, 'status': 'failed'})
            self.save()
        self.state['phase'] = 'checkpoint'
        if hasattr(self.backend, 'deadline'):
            self.backend.deadline = self.state['deadline']
        self.save()

    def pick(self):
        choice = self.checkpoint(self.state['a'], self.remaining(), self.directory / 'checkpoint',
                                 timeout=min(300, max(1, int(self.remaining()))))
        self.state['choice'] = choice
        LOG.info('後半の選定: %s %s', choice['source'], choice['decision'])
        decision = choice['decision']
        selected = {s['id']: s for s in self.state['settings']}
        for item in decision['learning']:
            setting = {**selected[item['setting']], 'rate': item['rate']}
            row = next(r for r in self.state['a'] if r['id'] == setting['id'])
            estimate = series_cost(row) * row['rate'] / setting['rate']
            self.state['series'].append({'id': 'learn_' + setting['id'].replace('/', '_'), 'setting': setting,
                                        'learning': True, 'notes': {}, 'estimate': estimate, 'disabled': False})
            if item['setting'] == decision['control']:
                self.state['series'].append({'id': 'control_' + setting['id'].replace('/', '_'), 'setting': setting,
                                            'learning': False, 'notes': {}, 'estimate': series_cost(row, False) * row['rate'] / setting['rate'], 'disabled': False})
        self.state['phase'] = 'B'
        self.save()

    def run_b(self, *, max_rounds=None):
        # A paused run can receive a checked revision without rewriting state.json.
        # Select the newest completed game first so old revisions never undo learning.
        changed = False
        for series in self.state['series']:
            if not series['learning']:
                continue
            completed = list((self.directory / 'results').glob(f'B_{series["id"]}_*.json'))
            numbers = [int(path.stem.rsplit('_', 1)[-1]) for path in completed
                       if path.stem.rsplit('_', 1)[-1].isdigit()]
            if numbers:
                path = note_snapshot(self.directory / 'notes' / series['id'], max(numbers))
                if path.exists():
                    current = read_json(path)
                    if current != series['notes']:
                        series['notes'] = current
                        changed = True
        if changed:
            self.save()
        while self.remaining() > 60 and (max_rounds is None or self.state['round'] <= max_rounds):
            number, progressed = self.state['round'], False
            for series in self.state['series']:
                if series['disabled']:
                    continue
                key = f'B_{series["id"]}_{number:04d}'
                final = self.directory / 'results' / f'{key}.json'
                if final.exists():
                    notes_path = note_snapshot(self.directory / 'notes' / series['id'], number)
                    if series['learning'] and notes_path.exists():
                        series['notes'] = read_json(notes_path)
                        self.save()
                    continue
                if not affordable(self.remaining(), series['estimate']):
                    continue
                progressed = True
                def work():
                    started = self.now()
                    self.backend.prepare(series['setting'])
                    game = self.play(key, series['setting'], number, series['estimate'], learning_profile=True,
                                     lessons=lesson_texts(series['notes']) if series['learning'] else {})
                    game_dir = Path(game['transcript']).parent
                    if series['learning']:
                        # Snapshot commit first; a crash before state save is idempotent.
                        series['notes'] = self.backend.reflect(series['setting'], game_dir, series['notes'],
                                                               self.directory / 'notes' / series['id'], number)
                    result = {'series': series['id'], 'round': number, 'learning': series['learning'], 'game': game}
                    series['estimate'] = max(series['estimate'], (self.now()-started)*1.15)
                    save_json(final, result)
                    return result
                failure_key = 'series_' + series['id']
                for attempt in range(2):
                    result = self.unit(key, series['setting'], work)
                    self.state['units'].append({'key': failure_key, 'status': 'completed' if result else 'failed'})
                    if result:
                        break
                    if failure_count(self.state['units'], failure_key) >= 2:
                        series['disabled'] = True
                        break
                    if not affordable(self.remaining(), series['estimate']):
                        break
                self.save()
            if not progressed:
                break
            self.state['round'] += 1
            self.save()

    def begin_b(self, hours):
        """Give a separately launched second half its own deadline, once only."""
        if self.state.get('b_started') or self.state['phase'] in {'B', 'finished'}:
            return
        if self.state['phase'] != 'checkpoint' or not self.state.get('phase_a_only'):
            raise ValueError('前半のみの実行が終わってから --begin-b を指定してください')
        self.state['deadline'] = self.now() + hours * 3600
        self.state['phase_a_only'] = False
        self.state['b_started'] = self.now()
        if hasattr(self.backend, 'deadline'):
            self.backend.deadline = self.state['deadline']
        self.save()

    def run(self, *, max_rounds=None, phase_a_only=False):
        try:
            if self.state['finished']:
                return
            if phase_a_only:
                if self.state['phase'] not in {'A', 'checkpoint'}:
                    raise ValueError('後半に入った実験は --phase-a-only で再開できません')
                self.state['phase_a_only'] = True
                self.save()
            if self.state['phase'] == 'A':
                self.run_a(reserve_b=not self.state.get('phase_a_only'))
            if self.state.get('phase_a_only'):
                LOG.info('前半終了。LLMを停止して後半の開始を待ちます')
                return
            if self.state['phase'] == 'checkpoint':
                self.pick()
            self.run_b(max_rounds=max_rounds)
            self.state['finished'] = True
            self.state['phase'] = 'finished'
            self.save()
        finally:
            try:
                self.backend.close()
            finally:
                self.report()

    def report(self):
        rows = []
        for row in self.state['a']:
            if row.get('game'):
                rows.append({'phase': 'A', 'series': row['id'], 'round': 1, 'accuracy': row['scene']['accuracy'],
                             'rate': row['rate'], **row['game']})
        for path in sorted((self.directory / 'results').glob('*.json')):
            result = read_json(path)
            rows.append({'phase': 'B', 'series': result['series'], 'round': result['round'], 'accuracy': '', **result['game']})
        with (self.directory / 'aggregate.csv').open('w', encoding='utf-8-sig', newline='') as file:
            fields = ['phase', 'series', 'round', 'accuracy', 'rate', 'completed', 'wall_sec', 'public_messages', 'llm_calls', 'metrics', 'mechanical', 'experiment', 'transcript']
            writer = csv.DictWriter(file, fields, extrasaction='ignore')
            writer.writeheader()
            for row in rows:
                writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v for k, v in row.items()})
        lines = ['# Stage 3 実験結果', f'状態: {self.state["phase"]}。開始: {datetime.fromtimestamp(self.state["started"])}、締切: {datetime.fromtimestamp(self.state["deadline"])}。',
                 '## 前半', '|設定|正答率|完走|発言|呼出|秒|倍率|Q/G|追加指標|書き起こし|', '|---|---:|---|---:|---:|---:|---:|---|---|---|']
        for row in self.state['a']:
            game = row.get('game', {})
            lines.append(f'|{row["id"]}|{row.get("scene", {}).get("accuracy", "—")}|{row["status"]}|{game.get("public_messages", "—")}|{game.get("llm_calls", "—")}|{game.get("wall_sec", "—")}|{row["rate"]}|{json.dumps(game.get("metrics", {}), ensure_ascii=False)}|{json.dumps(game.get("experiment", {}), ensure_ascii=False)}|{game.get("transcript", "—")}|')
        lines += ['## 区切りの判断', '```json', json.dumps(self.state.get('choice'), ensure_ascii=False, indent=2), '```', '## 後半の推移',
                  '|系列|巡|発言|呼出|秒|Q/G|追加指標|書き起こし|', '|---|---:|---:|---:|---:|---|---|---|']
        for row in rows:
            if row['phase'] == 'B':
                lines.append(f'|{row["series"]}|{row["round"]}|{row["public_messages"]}|{row["llm_calls"]}|{row["wall_sec"]}|{json.dumps(row["metrics"], ensure_ascii=False)}|{json.dumps(row["experiment"], ensure_ascii=False)}|{row["transcript"]}|')
        lines += ['## 前半・後半と学習・対照の比較']
        for series in self.state['series']:
            values = [r for r in rows if r['phase'] == 'B' and r['series'] == series['id']]
            baseline = next((r for r in self.state['a'] if r['id'] == series['setting']['id']), {})
            avg = {k: sum(r[k] for r in values) / len(values) for k in ['wall_sec', 'public_messages', 'llm_calls']} if values else {}
            lines.append(f'- {series["id"]}: {len(values)}ゲーム。後半平均 {json.dumps(avg, ensure_ascii=False)}。前半 {json.dumps({k: baseline.get("game", {}).get(k) for k in avg}, ensure_ascii=False)}。')
        qwen = next((r for r in self.state['a'] if r['id'] == 'qwen35-9b/off' and r.get('game')), None)
        reference = ROOT / 'games/20261003_164356_163908_seed1/checks.json'
        if qwen and reference.exists():
            old = read_json(reference)
            lines.append('## 以前の短いゲームとの比較')
            for key, previous in [('public_messages', old['public_messages']), ('llm_calls', len(old['llm_calls']))]:
                current = qwen['game'][key]
                lines.append(f'- {key}: 以前{previous}、今回{current}。' + ('20%以上の差あり。' if abs(current-previous) > previous*.2 else '20%以内。'))
            lines.append(f'- 以前Q/G: {json.dumps(old["metrics"], ensure_ascii=False)}、今回: {json.dumps(qwen["game"]["metrics"], ensure_ascii=False)}')
        lines += ['## 最終の教訓ノート', '```json', json.dumps({s['id']: s['notes'] for s in self.state['series'] if s['learning']}, ensure_ascii=False, indent=2), '```',
                  '## 失敗', '```json', json.dumps(self.state['failures'], ensure_ascii=False, indent=2), '```',
                  '自己開示の戦略的合否と曖昧なQ3/Q4は候補計測です。モデルの自己ラベルを合否に使っていません。個人の投票は実験の記録と終了後の感想戦だけに使います。']
        (self.directory / 'REPORT.md').write_text('\n'.join('\n' + line + '\n' if line.startswith('#') else line for line in lines) + '\n', encoding='utf-8')
        LOG.info('報告を保存: %s', self.directory / 'REPORT.md')


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description='ローカルモデルの性能評価と感想戦の実験')
    parser.add_argument('--hours', type=float, default=38)
    parser.add_argument('--run', type=Path, help='状態を保存する場所。同じ指定で再開')
    parser.add_argument('--port', type=int, default=8091)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--phase-a-only', action='store_true', help='前半のみ実行し、後半の開始を待つ')
    parser.add_argument('--shortest-first', action='store_true', help='前半をゲームの見込み時間が短い順に実行')
    parser.add_argument('--begin-b', action='store_true', help='前半のみの完了後、後半の締切を --hours で新しく設定')
    parser.add_argument('--extend-hours', type=float, help='状態をバックアップして締切を延長するだけ。起動はしない')
    args = parser.parse_args()
    candidates = sorted((ROOT / 'runs').glob('*/state.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    unfinished = next((p.parent for p in candidates if not read_json(p).get('finished')), None)
    directory = args.run or (candidates[0].parent if args.status and candidates else unfinished or ROOT / 'runs' / datetime.now().strftime('%Y%m%d_%H%M%S'))
    if args.status:
        if not (directory / 'state.json').exists():
            print('実験の状態ファイルはありません')
            return
        state = read_json(directory / 'state.json')
        print(json.dumps({'場所': str(directory.resolve()), '段階': state['phase'], '前半': len(state['a']),
                          '巡': state['round'], '実行中': state['active'], '失敗': len(state['failures']),
                          '残り時間': round(max(0, state['deadline'] - time.time()) / 3600, 2)}, ensure_ascii=False, indent=2))
        return
    if args.hours <= 0:
        parser.error('--hours は正の時間を指定してください')
    if args.phase_a_only and args.begin_b:
        parser.error('--phase-a-only と --begin-b は同時に指定できません')
    if args.extend_hours is not None and (args.status or args.begin_b or args.phase_a_only):
        parser.error('--extend-hours は起動・状況表示の指定と組み合わせないでください')
    directory.mkdir(parents=True, exist_ok=True)
    import msvcrt
    with (directory / 'runner.lock').open('a+b') as lock:
        lock.seek(0)
        if lock.read(1) == b'':
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit('同じ実験ランナーが既に動いています')
        if args.extend_hours is not None:
            result = extend_deadline(directory, args.extend_hours)
            print(json.dumps({'バックアップ': result['backup'],
                              '締切': datetime.fromtimestamp(result['deadline']).isoformat(),
                              '実験は起動していない': True}, ensure_ascii=False, indent=2))
            return
        logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(directory / 'run.log', encoding='utf-8'), logging.StreamHandler()],
                            format='%(asctime)s %(levelname)s %(message)s')
        backend = LocalBackend(directory, args.port)
        try:
            backend.recover()
            if process_inventory():
                raise RuntimeError('別のllama-serverが稼働中です（8090も停止してください）')
            LOG.info('出発前チェック: %s', check_resources())
            runner = Marathon(directory, backend, hours=args.hours)
            if args.shortest_first and runner.state['phase'] == 'A':
                runner.state['settings'].sort(key=lambda s: s.get('expected_game_sec', 750/s['rate']))
                runner.save()
            if args.begin_b:
                runner.begin_b(args.hours)
            with Awake():
                runner.run(phase_a_only=args.phase_a_only)
        except KeyboardInterrupt:
            LOG.info('中断しました。同じ --run 指定で再開できます')
        except Exception:
            LOG.exception('起動・再開できません')
            raise
        finally:
            backend.close()


if __name__ == '__main__':
    main()
