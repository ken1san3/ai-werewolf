"""Build local, public-only replays without changing game records."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import webbrowser

import yaml

from .replay_data import load_replay
from .replay_ui import render_replay


ROOT = Path(__file__).resolve().parents[1]
PLAYER_ID = re.compile(r'(?<![A-Za-z0-9_-])player-\d+(?![A-Za-z0-9_-])')


def replace_player_ids(text, names):
    return PLAYER_ID.sub(lambda match: names.get(match.group(), match.group()), text)


def discover_games(root):
    root = Path(root)
    games = set()
    for base in [root / 'games', *(root / 'runs').glob('*/games')]:
        games.update(path for path in base.glob('*') if path.is_dir())
        # Interrupted workers can have only their public progress log, with no
        # saved record directory yet. Never read the sibling request JSON.
        games.update(path.with_suffix('') for path in base.glob('*.log')
                     if path.name.startswith(('A_', 'B_')) and not path.with_suffix('').is_dir())
    return sorted(games, key=lambda path: str(path).lower())


def _read_names(path):
    names = yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}
    if not isinstance(names, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                          or not PLAYER_ID.fullmatch(key) or not value.strip()
                                          for key, value in names.items()):
        raise ValueError('名前の設定は player-0: アオイ の形式で書いてください。')
    return names


def _team_names(root):
    path = root / 'content/teams.yaml'
    if not path.exists():
        path = ROOT / 'content/teams.yaml'
    content = yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}
    return {team['id']: team['name'] for team in content.get('teams', [])}


def _source_files(directory):
    return [path for path in [directory / 'server_record.json', directory / 'transcript.md',
                             directory / 'checks.json', directory.with_name(directory.name + '.log')]
            if path.is_file()]


def _fingerprint(directory, shared):
    digest = hashlib.sha256(shared)
    for path in _source_files(directory):
        digest.update(path.name.encode('utf-8'))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _game_date(directory, data):
    dated = re.match(r'(\d{8})_(\d{6})', directory.name)
    if dated:
        try:
            return datetime.strptime(''.join(dated.groups()), '%Y%m%d%H%M%S').timestamp()
        except ValueError:
            pass
    files = _source_files(directory)
    saved = next((path for path in files if path.name == 'checks.json'), None)
    if saved is not None:
        # Exported samples carry their start time because a Git checkout
        # does not keep the original file times.
        try:
            started = json.loads(saved.read_text(encoding='utf-8-sig')).get('started_at')
            if isinstance(started, str):
                return datetime.fromisoformat(started).timestamp()
        except (OSError, ValueError, AttributeError):
            pass
    # Saved files are written after the game. Subtract the recorded duration
    # instead of displaying their save time as the start of the game.
    saved = next((path for path in files if path.name == 'checks.json'), None)
    if saved is not None:
        return saved.stat().st_mtime - data['duration']
    if directory.exists():
        return directory.stat().st_ctime
    return files[0].stat().st_ctime if files else 0


def _series(directory, data):
    name = directory.name
    mode_names = {'off': '思考なし', 'on': '思考あり', 'low': '推論弱', 'high': '推論強'}
    a = re.match(r'A_(.+?)_(off|on|low|high)(?:$|_)', name)
    b = re.match(r'B_(learn|control)_(.+?)_(off|on|low|high)_(\d+)', name)
    if b:
        kind, model, mode, number = b.groups()
        return f'{kind} · {model} · {mode_names[mode]}', f'{int(number)}巡（seed {int(number)}）'
    seed = data.get('seed')
    if seed is None:
        match = re.search(r'seed(\d+)', name)
        seed = int(match.group(1)) if match else None
    label = f'seed {seed}' if seed is not None else '不明'
    return (f'前半 · {a[1]} · {mode_names[a[2]]}' if a else '単発'), label


def _json_script(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')


def render_index(matches):
    data = _json_script([{key: value for key, value in row.items() if key != 'html_path'} for row in matches])
    return INDEX_TEMPLATE.replace('__MATCHES__', data)


def build_replays(root=ROOT, output=None, names_path=None):
    root = Path(root).resolve()
    output = Path(output) if output else root / 'runs/replays'
    output.mkdir(parents=True, exist_ok=True)
    if names_path is None:
        names_path = root / 'content/replay_names.yaml'
        if not names_path.exists():
            names_path = ROOT / 'content/replay_names.yaml'
    names_path = Path(names_path)
    names = _read_names(names_path)
    teams = _team_names(root)
    shared = hashlib.sha256()
    for path in [Path(__file__), Path(__file__).with_name('replay_data.py'),
                 Path(__file__).with_name('replay_ui.py'), names_path,
                 root / 'content/chat_channels.yaml', root / 'content/teams.yaml',
                 *sorted((root / 'content/roles').glob('*.yaml'))]:
        if path.exists():
            shared.update(path.read_bytes())
    manifest_path = output / '.manifest.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        manifest = {}
    generated = unchanged = 0
    matches, current = [], {}
    for directory in discover_games(root):
        identity = directory.relative_to(root).as_posix()
        key = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]
        filename = f'game_{key}.html'
        destination = output / filename
        fingerprint = _fingerprint(directory, shared.digest())
        cached = manifest.get(identity, {})
        if cached.get('fingerprint') == fingerprint and destination.is_file():
            metadata = cached['metadata']
            unchanged += 1
        else:
            data = load_replay(directory)
            data['title'] = directory.name
            for player in data['players']:
                player['name'] = names.get(player['id'], player['id'])
            series, seed = _series(directory, data)
            date = _game_date(directory, data)
            experiment = directory.parent.parent.name if directory.parent.name == 'games' and directory.parent != root / 'games' else '単発ゲーム'
            metadata = {'id': key, 'title': directory.name,
                        'date': datetime.fromtimestamp(date).strftime('%Y-%m-%d %H:%M'), 'date_sort': date,
                        'experiment': experiment, 'series': series, 'seed': seed,
                        'winner': teams.get(data.get('winner'), '引き分け' if data.get('winner') == 'draw'
                                            else data.get('winner') or '未確定'),
                        'days': data['days'], 'public_messages': data['public_messages'],
                        'duration': data['duration'], 'status': data['status'], 'reason': data.get('reason', ''),
                        'mode': data.get('mode', 'none'), 'html': filename, 'playable': bool(data['events'])}
            data['subtitle'] = f"{experiment} / {series} / {seed}"
            data['team_names'] = teams
            destination.write_text(render_replay(data), encoding='utf-8')
            generated += 1
        metadata = {**metadata, 'html_path': str(destination.resolve())}
        matches.append(metadata)
        current[identity] = {'fingerprint': fingerprint, 'metadata': metadata}
    matches.sort(key=lambda row: row['date_sort'], reverse=True)
    index = output / 'index.html'
    index.write_text(render_index(matches), encoding='utf-8')
    manifest_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'total': len(matches), 'playable': sum(row['playable'] for row in matches),
            'partial': sum(row['status'] == '途中' for row in matches),
            'unavailable': sum(not row['playable'] for row in matches), 'generated': generated,
            'unchanged': unchanged, 'index': index.resolve(), 'matches': matches}


def main():
    parser = argparse.ArgumentParser(description='公開情報だけの試合リプレイを作り、ブラウザで一覧を開きます。')
    parser.add_argument('--root', type=Path, default=ROOT,
                        help='games/ と runs/*/games/ を探すフォルダ（例: samples）')
    parser.add_argument('--output', type=Path, help='HTMLの出力先（既定: <root>/runs/replays）')
    parser.add_argument('--no-open', action='store_true', help='ブラウザを開かない')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        result = build_replays(args.root, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f'リプレイの作成に失敗しました: {error}\n')
    print(f"試合 {result['total']}件 / 再生可能 {result['playable']}件 / 途中 {result['partial']}件 / 再生不可 {result['unavailable']}件")
    print(f"作成 {result['generated']}件 / 更新不要 {result['unchanged']}件\n一覧: {result['index']}")
    if not args.no_open and not webbrowser.open(result['index'].as_uri()):
        print('ブラウザを自動で開けませんでした。一覧のHTMLを直接開いてください。')


INDEX_TEMPLATE = r'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AIwolf 試合のリプレイ</title><style>
:root{color-scheme:dark;--bg:#10151d;--card:#19222e;--line:#2c394b;--ink:#eaf0f8;--muted:#a1b1c8;--accent:#8fd4c6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,"Yu Gothic",sans-serif}
main{max-width:1500px;margin:auto;padding:36px 24px}header{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap}
.eyebrow{color:var(--accent);font-size:12px;letter-spacing:.15em}h1{font-size:30px;letter-spacing:.03em;margin:4px 0 8px}p{color:var(--muted);margin:0 0 24px}
.filters{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0}label{color:var(--muted);font-size:12px;display:flex;flex-direction:column;gap:5px}
input,select{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);padding:9px 12px;border-radius:8px;max-width:100%}input{min-width:260px}
.table{overflow:auto;border:1px solid var(--line);border-radius:12px;background:var(--card)}table{width:100%;border-collapse:collapse;white-space:nowrap}
th,td{padding:14px 13px;border-bottom:1px solid var(--line);text-align:left}th{position:sticky;top:0;background:#1e2a38;font-weight:500}
th button{font:inherit;border:0;background:transparent;color:var(--muted);cursor:pointer;padding:0}th button:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:4px}
tbody tr:hover{background:#223142;cursor:pointer}td a{color:var(--ink);text-decoration:none}.muted{color:var(--muted)}.status{border-radius:20px;padding:3px 9px;font-size:12px;background:#2b3d43;color:#a7e0cc}
.partial{background:#453c26;color:#f0d69d}.failed{background:#4a2c35;color:#ffc1c1}.reason{max-width:300px;white-space:normal;font-size:12px;color:var(--muted)}#empty{padding:30px;display:none}#count{color:var(--accent);font-size:14px}
@media(max-width:650px){main{padding:22px 12px}h1{font-size:25px}.filters label{flex:1;min-width:130px}.filters label:last-child{width:100%}input{min-width:0;width:100%}th,td{padding:12px 10px}}
</style></head><body><main><header><div><div class="eyebrow">AIWOLF / REPLAY LIBRARY</div><h1>試合のリプレイ</h1></div><div id="count"></div></header>
<p>公開された会話と出来事を、試合ごとに振り返れます。列名で並べ替え、行を選ぶと再生画面が開きます。</p>
<div class="filters"><label>実験<select id="experiment"><option value="">すべて</option></select></label><label>系列<select id="series"><option value="">すべて</option></select></label><label>勝者<select id="winner"><option value="">すべて</option></select></label><label>文字検索<input id="search" type="search" placeholder="モデル・試合名・seed など"></label></div>
<div class="table"><table><thead><tr id="columns"></tr></thead><tbody id="rows"></tbody></table><div id="empty">条件に合う試合はありません。</div></div>
</main><script id="matches" type="application/json">__MATCHES__</script><script>
'use strict';
const matches=JSON.parse(document.getElementById('matches').textContent);
const columns=[['date','日時'],['experiment','実験名'],['series','系列'],['seed','seed / 巡'],['winner','勝者'],['days','日数'],['public_messages','公開発言'],['duration','試合時間'],['status','状態']];
let sort='date_sort',descending=true;
for(const id of ['experiment','series','winner']){const select=document.getElementById(id);for(const value of [...new Set(matches.map(x=>x[id]))].sort()){const option=document.createElement('option');option.value=value;option.textContent=value;select.append(option)}select.addEventListener('change',draw)}
const headings=[];
for(const [key,label] of columns){const th=document.createElement('th'),button=document.createElement('button');button.textContent=label+' ↕';button.type='button';button.addEventListener('click',()=>{const chosen=key==='date'?'date_sort':key;descending=sort===chosen?!descending:false;sort=chosen;draw()});th.append(button);document.getElementById('columns').append(th);headings.push({th,key,label,button})}
document.getElementById('search').addEventListener('input',draw);
function duration(sec){sec=Math.round(sec);return sec>=3600?Math.floor(sec/3600)+'時間 '+Math.floor(sec%3600/60)+'分':Math.floor(sec/60)+'分 '+sec%60+'秒'}
function draw(){const query=document.getElementById('search').value.trim().toLocaleLowerCase();const shown=matches.filter(row=>['experiment','series','winner'].every(id=>!document.getElementById(id).value||row[id]===document.getElementById(id).value)&&(!query||[row.title,row.experiment,row.series,row.seed,row.winner,row.status,row.date].join(' ').toLocaleLowerCase().includes(query))).sort((a,b)=>{const x=a[sort],y=b[sort];const order=typeof x==='number'?x-y:String(x).localeCompare(String(y),'ja');return descending?-order:order});
for(const heading of headings){const active=(heading.key==='date'?'date_sort':heading.key)===sort;heading.th.setAttribute('aria-sort',active?(descending?'descending':'ascending'):'none');heading.button.textContent=heading.label+(active?(descending?' ↓':' ↑'):' ↕')}
const body=document.getElementById('rows');body.replaceChildren();for(const row of shown){const tr=document.createElement('tr');tr.title=row.title;for(const [key] of columns){const td=document.createElement('td');if(key==='date'){const a=document.createElement('a');a.href=row.html;a.textContent=row.date;a.setAttribute('aria-label',row.title+'のリプレイを開く');td.append(a)}else if(key==='status'){const badge=document.createElement('span');badge.className='status'+(row.status==='途中'?' partial':row.status==='失敗'?' failed':'');badge.textContent=row.status;td.append(badge);if(!row.playable||row.reason){const p=document.createElement('div');p.className='reason';p.textContent=(row.playable?'':'再生不可: ')+(row.reason||'');td.append(p)}}else{td.textContent=key==='duration'?duration(row.duration):row[key]}tr.append(td)}tr.addEventListener('click',event=>{if(!event.target.closest('a'))location.href=row.html});body.append(tr)}document.getElementById('empty').style.display=shown.length?'none':'block';document.getElementById('count').textContent=shown.length+' / '+matches.length+' 試合';}
draw();
</script></body></html>'''


if __name__ == '__main__':
    main()
