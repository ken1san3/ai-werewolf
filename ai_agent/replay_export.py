"""Copy a recorded game's public replay inputs, dropping every private field.

Rows are rebuilt by the same allow-list that the replay loader uses, so a
field is written only when the replay itself would show it.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from .replay_data import SETTING_KEYS, _names, _normalize, _read_json


def _public_rows(record):
    rows = []
    for event in _normalize(record.get('rows', []), _names(), lambda: record.get('roles', {})):
        if event['kind'] == 'chat':
            rows.append({'t': event['t'], 'kind': 'chat', 'channel': 'public', 'message': event['payload']})
        else:
            # GAME_ENDED keeps the role assignment that the replay reveals at the end.
            rows.append({'t': event['t'], 'kind': event['kind'], 'payload': event['payload']})
    return rows


def _started_at(source, checks, rows):
    """Keep the game's start time, which a Git checkout cannot carry as a file time."""
    saved = source / 'checks.json'
    if not saved.is_file():
        return None
    wall = checks.get('wall_sec')
    duration = wall if isinstance(wall, (int, float)) else max((row['t'] for row in rows), default=0)
    return datetime.fromtimestamp(saved.stat().st_mtime - duration).astimezone().isoformat(timespec='seconds')


def export_public_game(source, destination):
    source, destination = Path(source), Path(destination)
    record = _read_json(source / 'server_record.json')
    if not isinstance(record, dict):
        raise ValueError('server_record.jsonを読み取れません。')
    rows = _public_rows(record)
    if not any(row['kind'] == 'GAME_ENDED' for row in rows):
        raise ValueError('終了していないゲームは書き出しません（役職が公開されていないため）。')
    checks = _read_json(source / 'checks.json') if (source / 'checks.json').is_file() else {}
    settings = checks.get('settings') if isinstance(checks.get('settings'), dict) else {}
    public_checks = {'settings': {key: settings.get(key, checks.get(key)) for key in SETTING_KEYS
                                  if settings.get(key, checks.get(key)) is not None}}
    for key in ('completed', 'crashes', 'wall_sec'):
        if key in checks:
            public_checks[key] = checks[key]
    started = _started_at(source, checks, rows)
    if started:
        public_checks['started_at'] = started
    destination.mkdir(parents=True, exist_ok=True)
    for name, value in (('server_record.json', {'rows': rows}), ('checks.json', public_checks)):
        (destination / name).write_text(json.dumps(value, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return {'rows': len(rows), 'chat': sum(row['kind'] == 'chat' for row in rows)}


def main():
    parser = argparse.ArgumentParser(description='記録済みゲームから、リプレイで公開される情報だけを書き出します。')
    parser.add_argument('source', type=Path, help='games/ などのゲーム記録フォルダ')
    parser.add_argument('destination', type=Path, help='書き出し先のフォルダ')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        result = export_public_game(args.source, args.destination)
    except (OSError, ValueError) as error:
        parser.exit(1, f'書き出しに失敗しました: {error}\n')
    print(f"公開イベント {result['rows']}件（公開チャット {result['chat']}件）を書き出しました: {args.destination}")


if __name__ == '__main__':
    main()
