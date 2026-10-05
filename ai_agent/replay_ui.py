"""Self-contained, public-event replay page. No runtime network dependencies."""
import html
import json
import re
from urllib.parse import urlsplit


def render_replay(data, index_href='index.html') -> str:
    """Render a portable HTML page; reveal true roles only after GAME_ENDED."""
    title = str(data.get('title') or '人狼ゲームのリプレイ')
    index_href = str(index_href)
    if urlsplit(index_href).scheme or index_href.startswith(('//', '\\')):
        index_href = 'index.html'
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
    for source, escaped in [('<', '\\u003c'), ('>', '\\u003e'), ('&', '\\u0026'),
                            ('\u2028', '\\u2028'), ('\u2029', '\\u2029')]:
        encoded = encoded.replace(source, escaped)
    values = {'__REPLAY_TITLE__': html.escape(title), '__REPLAY_INDEX__': html.escape(index_href, quote=True),
              '__REPLAY_DATA__': encoded}
    return re.sub(r'__REPLAY_TITLE__|__REPLAY_INDEX__|__REPLAY_DATA__', lambda match: values[match[0]], _PAGE)


_PAGE = r'''<!doctype html>
<html lang="ja" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>__REPLAY_TITLE__ · 人狼リプレイ</title>
<style>
:root{--bg:#101619;--panel:#182125;--raised:#202c31;--hover:#29373d;--text:#edf3ee;--muted:#9eafb4;--line:#304047;--accent:#a6d6ac;--accent-soft:#203a2b;--danger:#e4a39a;--shadow:0 12px 44px #0003;--radius:18px;--header:90px;--footer:132px;--font:"Yu Gothic UI","Hiragino Kaku Gothic ProN",Meiryo,system-ui,sans-serif}
:root[data-theme="light"]{--bg:#f0f3f1;--panel:#fff;--raised:#f2f6f3;--hover:#e5ece7;--text:#21302b;--muted:#65756e;--line:#d8e2dc;--accent:#427c4c;--accent-soft:#e5f1e7;--danger:#b45e53;--shadow:0 12px 44px #233e2b0a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px;line-height:1.65}button,input,select{font:inherit}button,a,input,select{-webkit-tap-highlight-color:transparent}button,select{color:var(--text)}button{cursor:pointer}button:disabled,input:disabled,select:disabled{opacity:.4;cursor:default}a{color:inherit}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid var(--accent);outline-offset:3px}button{border:1px solid var(--line);background:var(--raised);border-radius:10px;padding:8px 12px;transition:background .15s,border-color .15s}button:hover:not(:disabled){background:var(--hover);border-color:var(--muted)}button[aria-pressed="true"]{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.app{max-width:1540px;margin:auto;height:100dvh;min-height:540px;display:flex;flex-direction:column;padding:0 26px}.topbar{min-height:var(--header);display:flex;align-items:center;justify-content:space-between;gap:20px;border-bottom:1px solid var(--line)}.identity{min-width:0;display:flex;gap:20px;align-items:center}.back{display:flex;align-items:center;gap:7px;text-decoration:none;color:var(--muted);white-space:nowrap;font-size:13px;padding:8px 0}.back:hover{color:var(--accent)}.heading{min-width:0;border-left:1px solid var(--line);padding-left:20px}.eyebrow{font-size:10px;letter-spacing:.17em;font-weight:700;color:var(--accent);margin-bottom:3px}.heading h1{font-size:18px;font-weight:650;margin:0;white-space:nowrap;text-overflow:ellipsis;overflow:hidden;max-width:740px}.metadata{color:var(--muted);font-size:11px;margin-top:2px}.top-actions{display:flex;gap:8px;align-items:center}.top-actions button{white-space:nowrap;font-size:12px}.theme-button{width:38px;padding:8px 0;font-size:16px!important}.record-badge{color:var(--muted);font-size:11px;border:1px solid var(--line);border-radius:99px;padding:5px 10px;white-space:nowrap}
.warning{margin-top:14px;padding:10px 15px;border:1px solid #bda97855;border-radius:10px;color:var(--text);background:#bda9780c;font-size:12px;white-space:pre-wrap}.workspace{display:grid;grid-template-columns:300px minmax(0,1fr);gap:22px;flex:1;min-height:0;padding:20px 0}.sidebar{display:flex;flex-direction:column;gap:15px;min-height:0}.section-heading{display:flex;justify-content:space-between;align-items:center;gap:8px}.section-heading h2{font-size:13px;font-weight:650;margin:0}.section-heading span{font-size:11px;color:var(--muted)}.phase-card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px;display:flex;justify-content:space-between;align-items:center;gap:15px}.phase-kicker{color:var(--muted);font-size:10px;letter-spacing:.1em}.phase-title{font-size:19px;font-weight:650;margin-top:2px}.timer{text-align:right;font-variant-numeric:tabular-nums}.timer-label{color:var(--muted);font-size:10px}.timer-value{font-size:21px;font-weight:600;letter-spacing:.04em}.phase-card.day{border-color:#bca47180}.phase-card.night{border-color:#869edc70}.players{display:grid;grid-template-columns:1fr;gap:7px;overflow:auto;min-height:0;padding:1px 4px 5px 1px}.seat{text-align:left;display:grid;grid-template-columns:36px minmax(0,1fr) auto;align-items:center;gap:11px;padding:9px 11px;border-radius:12px;background:var(--panel);min-height:61px;position:relative}.seat:before{content:"";position:absolute;left:0;top:14px;bottom:14px;width:3px;background:var(--seat);border-radius:4px}.avatar{display:flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:11px;background:var(--seat-bg);color:var(--seat);flex-shrink:0}.avatar svg{width:23px;height:23px;overflow:visible}.seat-name{font-size:12px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.seat-note{font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.seat-status{text-align:right;color:var(--muted);font-size:10px}.alive-dot{display:inline-block;width:5px;height:5px;border-radius:50%;background:var(--accent);margin-right:4px}.seat.dead{opacity:.55}.seat.dead .avatar{filter:grayscale(.85)}.seat.dead .seat-name{text-decoration:line-through;text-decoration-color:var(--muted)}.seat.dead .alive-dot{background:var(--muted)}.claim{display:inline-block;color:var(--text);background:var(--raised);padding:1px 6px;margin-top:3px;border-radius:5px;font-size:10px;border:1px solid var(--line)}.votes{font-size:10px;color:var(--seat);margin-top:2px}.players-help{color:var(--muted);font-size:10px;text-align:center;margin-top:auto}.reset-filter{font-size:10px;padding:4px 8px}
.conversation{min-height:0;display:flex;flex-direction:column;background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow)}.conversation-head{padding:16px 20px 13px;display:flex;align-items:center;justify-content:space-between;gap:12px}.conversation-head h2{margin:0;font-size:15px;font-weight:650}.flow-count{font-size:10px;color:var(--muted);margin-left:8px}.flow-tools{display:flex;gap:8px;align-items:center}.flow-tools select,.filters select{background:var(--raised);border:1px solid var(--line);border-radius:8px;padding:6px 9px;font-size:11px;max-width:210px}.follow{font-size:10px;padding:5px 9px;white-space:nowrap}.filters{display:flex;gap:9px;padding:0 20px 13px;border-bottom:1px solid var(--line)}.search-wrap{position:relative;flex:1;min-width:0}.search-wrap svg{width:15px;height:15px;position:absolute;left:10px;top:10px;color:var(--muted)}.search-wrap input{width:100%;min-width:0;color:var(--text);background:var(--raised);border:1px solid var(--line);border-radius:9px;padding:7px 10px 7px 32px;font-size:12px}.search-wrap input::placeholder{color:var(--muted)}.stream{overflow:auto;flex:1;min-height:0;padding:18px 25px 25px;scrollbar-width:thin;scrollbar-color:var(--line) transparent}.stream:empty:after{content:"会話が始まるのを待っています";color:var(--muted)}.message{display:grid;grid-template-columns:34px minmax(0,1fr);gap:12px;padding:10px 0 13px}.message .avatar{margin-top:2px}.message-top{display:flex;align-items:center;gap:9px;margin-bottom:4px}.author{color:var(--seat);font-weight:650;font-size:12px}.timestamp{color:var(--muted);font-size:10px;font-variant-numeric:tabular-nums}.message-body{margin:0;font-size:13px;line-height:1.9;overflow-wrap:anywhere;white-space:pre-wrap}.system{padding:10px 12px;margin:8px 0;border-radius:10px;background:var(--raised);font-size:11px;color:var(--muted);border-left:2px solid var(--line)}.system-title{color:var(--text);font-size:11px;font-weight:600}.system .timestamp{margin-left:9px}.system-body{margin:4px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}.phase-divider{display:flex;align-items:center;gap:13px;color:var(--muted);font-size:10px;margin:18px 0 13px}.phase-divider:before,.phase-divider:after{content:"";height:1px;flex:1;background:var(--line)}.tallies{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.tally{border:1px solid var(--line);border-radius:6px;padding:2px 7px;font-size:10px;color:var(--text)}.empty{min-height:170px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:25px;color:var(--muted)}.empty-mark{font-size:30px;color:var(--accent);margin-bottom:8px}.empty h3{font-size:14px;color:var(--text);margin:0 0 7px}.empty p{font-size:12px;max-width:510px;margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.end-card{border:1px solid var(--accent);background:var(--accent-soft);border-radius:14px;padding:20px;margin:18px 0 10px}.end-eyebrow{font-size:10px;color:var(--accent);letter-spacing:.12em}.end-card h3{font-size:21px;font-weight:650;margin:4px 0 14px}.end-players{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.end-player{padding:10px;background:var(--panel);border:1px solid var(--line);border-radius:9px;font-size:11px;min-width:0}.end-name{font-weight:650;color:var(--seat);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.end-role{color:var(--text);margin-top:3px}.end-result{font-size:10px;color:var(--muted)}.end-result.won{color:var(--accent)}.end-card .system-body{font-size:11px;color:var(--muted);margin-top:10px}
.transport{border-top:1px solid var(--line);padding:17px 0 18px;flex-shrink:0;min-height:var(--footer)}.timeline{display:flex;align-items:center;gap:16px}.time{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums;min-width:47px}.time.current{color:var(--text)}.time.total{text-align:right}.track{position:relative;flex:1;min-width:0;padding-top:7px}.phase-ticks{position:absolute;left:0;right:0;top:0;height:6px;pointer-events:none}.tick{position:absolute;top:0;width:1px;height:4px;background:var(--muted);opacity:.55}.timeline input{display:block;width:100%;margin:0;height:16px;cursor:pointer;accent-color:var(--accent)}.transport-bottom{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-top:13px}.playback{display:flex;align-items:center;gap:8px}.playback button{width:36px;height:34px;padding:0;font-size:16px}.playback .play{background:var(--accent);border-color:var(--accent);color:var(--bg);font-size:15px;width:45px}.playback .play:hover{background:var(--accent);filter:brightness(1.08)}.speed-control{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:11px;margin-left:7px}.speed-control input{width:95px;height:18px;accent-color:var(--accent);cursor:pointer}.speed-control output{color:var(--text);width:29px;font-variant-numeric:tabular-nums}.transport-note{font-size:10px;color:var(--muted);text-align:right}.keyboard-hint{color:var(--muted);font-size:10px}.keyboard-hint kbd{border:1px solid var(--line);padding:1px 5px;border-radius:4px;font:inherit}
@media(min-width:1250px){.workspace{grid-template-columns:320px minmax(0,1fr);gap:27px}.seat{min-height:66px;padding:10px 13px}.players{gap:8px}.stream{padding-left:32px;padding-right:32px}}
@media(max-width:900px){.app{padding:0 17px}.workspace{grid-template-columns:245px minmax(0,1fr);gap:14px}.heading{padding-left:15px}.identity{gap:15px}.record-badge{display:none}.heading h1{max-width:430px;font-size:16px}.stream{padding:12px 17px}.conversation-head{padding:13px 15px}.filters{padding:0 15px 12px}.flow-tools select{max-width:130px}.end-players{grid-template-columns:repeat(2,minmax(0,1fr))}.seat{gap:8px;padding:8px 9px}}
@media(max-width:680px){:root{--header:76px;--footer:133px}.app{padding:0 12px;min-height:500px}.topbar{gap:8px}.identity{gap:10px}.back{font-size:0;gap:0;padding:8px 2px}.back span{font-size:20px}.heading{padding-left:10px}.heading h1{font-size:14px;max-width:calc(100vw - 180px)}.eyebrow{font-size:8px}.metadata{font-size:9px}.top-actions{gap:5px}.top-actions button{font-size:10px;padding:6px 9px}.theme-button{width:30px;font-size:13px!important}.workspace{display:flex;flex-direction:column;gap:12px;padding:12px 0;overflow:hidden}.sidebar{display:grid;grid-template-columns:1fr;gap:8px;flex-shrink:0}.phase-card{padding:8px 12px;border-radius:12px}.phase-title{font-size:15px;margin-top:0}.phase-kicker{font-size:8px}.timer-value{font-size:17px}.timer-label{font-size:8px}.sidebar .section-heading{display:none}.players{display:flex;overflow-x:auto;overflow-y:hidden;gap:7px;padding:0 1px 3px}.seat{width:126px;min-width:126px;min-height:54px;grid-template-columns:27px minmax(0,1fr);padding:7px 8px;gap:7px;border-radius:10px}.seat .avatar{width:26px;height:26px;border-radius:8px}.seat .avatar svg{width:19px;height:19px}.seat-name{font-size:10px}.seat-note{font-size:9px}.seat-status{display:none}.claim{font-size:8px;padding:0 4px;margin-top:1px}.players-help{display:none}.conversation{flex:1;border-radius:13px}.conversation-head{padding:11px 12px 9px}.conversation-head h2{font-size:12px}.flow-count{font-size:9px;margin-left:4px}.flow-tools{gap:5px}.flow-tools select{max-width:110px;font-size:10px;padding:5px 6px}.follow{font-size:9px;padding:5px 6px}.filters{padding:0 12px 10px;gap:6px}.filters select{font-size:10px;max-width:108px;padding:6px}.search-wrap input{font-size:11px}.stream{padding:9px 13px 17px}.message{grid-template-columns:27px minmax(0,1fr);gap:9px;padding:7px 0 10px}.message .avatar{width:27px;height:27px;border-radius:9px}.message .avatar svg{width:19px;height:19px}.message-body{font-size:12px}.author{font-size:11px}.timestamp{font-size:9px}.system{font-size:10px;padding:8px 10px}.end-card{padding:14px}.end-card h3{font-size:17px}.end-players{grid-template-columns:repeat(2,minmax(0,1fr))}.transport{padding:12px 0 13px}.timeline{gap:9px}.transport-bottom{gap:8px;margin-top:12px;flex-wrap:wrap}.playback{gap:5px}.playback button{width:30px;height:31px;font-size:14px}.playback .play{width:39px}.speed-control{gap:5px;margin-left:3px;font-size:10px}.speed-control input{width:70px}.keyboard-hint{display:none}.transport-note{font-size:9px;flex:1}.time{font-size:10px;min-width:41px}}
.metadata{white-space:nowrap;text-overflow:ellipsis;overflow:hidden;max-width:740px}.participants{flex-shrink:0;padding-top:16px}.participants .section-heading{margin-bottom:9px}.participants .players-help{margin:0;text-align:right}.workspace{display:flex;flex-direction:column;padding:15px 0;gap:0}.conversation{flex:1}.players{grid-template-columns:repeat(9,minmax(0,1fr));overflow:visible;gap:9px;padding:1px}.seat{grid-template-columns:27px minmax(0,1fr);gap:5px 8px;min-height:102px;padding:10px;align-content:center}.seat .avatar{width:27px;height:27px;border-radius:8px}.seat .avatar svg{width:20px;height:20px}.seat-name{font-size:11px}.seat-note{font-size:9px}.seat-status{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;gap:3px;font-size:9px;border-top:1px solid var(--line);padding-top:5px;margin-top:2px}.votes{font-size:9px;margin:0}.claim{font-size:9px;padding:0 4px;margin-top:3px}.no-claim{color:var(--muted);border-color:transparent;background:transparent;padding-left:0}.seat.unknown .alive-dot{background:var(--muted)}.conversation-head .phase-card{padding:0;border:0;background:transparent;gap:15px;border-radius:0}.conversation-head .phase-card.day .phase-title{color:#bda978}.conversation-head .phase-card.night .phase-title{color:#aab6e9}.conversation-head .phase-kicker{font-size:9px}.conversation-head .phase-title{font-size:14px;margin:0}.conversation-head .timer-value{font-size:15px}.conversation-head .timer-label{font-size:8px}.conversation-head .timer{border-left:1px solid var(--line);padding-left:15px}
@media(max-width:1100px){.players{gap:6px}.seat{padding:8px 7px;gap:5px}.seat-name{font-size:10px}.seat-note{font-size:8px}.seat-status{font-size:8px}.votes{font-size:8px}.claim{font-size:8px}.conversation-head .phase-card{gap:10px}.conversation-head .timer{padding-left:10px}}
@media(max-width:900px){.players{display:flex;overflow-x:auto;overflow-y:hidden;padding-bottom:4px;gap:7px}.seat{width:126px;min-width:126px;min-height:94px;padding:8px 9px;gap:5px 8px}.seat-name{font-size:11px}.seat-note{font-size:9px}.seat-status,.votes{font-size:9px}.claim{font-size:9px}.participants{padding-top:12px}.workspace{padding-top:12px}.metadata{max-width:430px}}
@media(max-width:680px){.participants{padding-top:9px}.participants .section-heading{margin-bottom:6px}.participants .section-heading h2{font-size:11px}.participants .players-help{display:none}.seat{width:116px;min-width:116px;min-height:89px;padding:7px 8px}.seat-status{display:flex;font-size:8px;padding-top:4px}.seat-note{font-size:8px}.claim,.votes{font-size:8px}.conversation-head>div:first-child{display:none}.conversation-head .phase-title{font-size:12px}.conversation-head .timer-value{font-size:13px}.conversation-head .phase-kicker,.conversation-head .timer-label{font-size:8px}.conversation-head .phase-card{gap:9px}.conversation-head .timer{padding-left:9px}.workspace{padding-top:9px;padding-bottom:10px}.metadata{max-width:calc(100vw - 180px)}}
:root[data-theme="light"] .conversation-head .phase-card.day .phase-title{color:#8c6a2e}:root[data-theme="light"] .conversation-head .phase-card.night .phase-title{color:#536ba0}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body>
<div class="app">
  <header class="topbar">
    <div class="identity">
      <a class="back" href="__REPLAY_INDEX__"><span aria-hidden="true">←</span> 一覧へ戻る</a>
      <div class="heading"><div class="eyebrow">公開記録のリプレイ</div><h1>__REPLAY_TITLE__</h1><div class="metadata" id="metadata"></div></div>
    </div>
    <div class="top-actions"><span class="record-badge" id="record-badge"></span><button id="name-toggle" type="button" aria-pressed="true" title="参加者の名前とIDの表示を切り替える">名前表示</button><button id="theme-toggle" class="theme-button" type="button" aria-label="明るい配色に切り替える" title="配色を切り替える">☼</button></div>
  </header>
  <div class="warning" id="warning" hidden></div>
  <section class="participants" aria-label="この時点の参加者一覧">
    <div class="section-heading"><h2>参加者 <span id="alive-count"></span></h2><span class="players-help">参加者を押すと、その人の発言に絞り込めます</span><button class="reset-filter" id="clear-filter" hidden>絞り込みを解除</button></div>
    <div class="players" id="players"></div>
  </section>
  <main class="workspace">
    <section class="conversation" aria-label="公開された会話とゲームの通知">
      <div class="conversation-head"><div><h2>会話の流れ <span class="flow-count" id="flow-count"></span></h2></div><div class="phase-card" id="phase-card"><div><div class="phase-kicker" id="day-label">ゲーム開始前</div><div class="phase-title" id="phase-title">待機</div></div><div class="timer"><div class="timer-label">フェーズ残り</div><div class="timer-value" id="phase-timer">—</div></div></div><div class="flow-tools"><select id="phase-jump" aria-label="日・フェーズへ移動"><option value="">場面へ移動</option></select><button class="follow" id="follow" aria-pressed="true" title="新しいイベントへ自動スクロール">最新を追う</button></div></div>
      <div class="filters"><div class="search-wrap"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M15.5 15.5L21 21" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg><input id="search" type="search" placeholder="発言・名前・IDを検索" aria-label="会話と通知を検索" autocomplete="off"></div><select id="player-filter" aria-label="参加者で絞り込む"><option value="">全員の発言</option></select></div>
      <div class="stream" id="stream" role="log" aria-live="off" tabindex="0"></div>
    </section>
  </main>
  <footer class="transport" aria-label="リプレイ操作">
    <div class="timeline"><span class="time current" id="current-time">00:00</span><div class="track"><div class="phase-ticks" id="phase-ticks" aria-hidden="true"></div><input id="seek" type="range" min="0" max="1" step="0.01" value="0" aria-label="再生時刻を移動"></div><span class="time total" id="total-time">00:00</span></div>
    <div class="transport-bottom"><div class="playback"><button id="restart" title="最初へ戻る" aria-label="最初へ戻る">↤</button><button id="previous" title="前のイベント" aria-label="前のイベント">‹</button><button class="play" id="play" title="再生 / 一時停止（スペース）" aria-label="再生">▶</button><button id="next" title="次のイベント" aria-label="次のイベント">›</button><button id="finish" title="記録の最後へ" aria-label="記録の最後へ">↦</button><label class="speed-control">速度 <input id="speed" type="range" min="1" max="50" step="1" value="10" list="speed-labels" aria-label="再生速度 1倍から50倍"><output id="speed-output" for="speed">10×</output></label><datalist id="speed-labels"><option value="1" label="1×"><option value="2" label="2×"><option value="5" label="5×"><option value="10" label="10×"><option value="20" label="20×"><option value="30" label="30×"><option value="50" label="50×"></datalist></div><div class="keyboard-hint"><kbd>Space</kbd> 再生 <span> · </span><kbd>←</kbd> <kbd>→</kbd> 5秒移動</div><div class="transport-note" id="transport-note">公開された情報だけを再生します</div></div>
  </footer>
</div>
<script id="replay-data" type="application/json">__REPLAY_DATA__</script>
<script>
(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('replay-data').textContent);
  const $ = id => document.getElementById(id);
  const node = (tag, className, text) => { const item = document.createElement(tag); if (className) item.className = className; if (text !== undefined) item.textContent = String(text); return item; };
  const numeric = value => typeof value === 'number' && Number.isFinite(value);
  const finite = (value, fallback=0) => numeric(value) ? value : fallback;
  const players = Array.isArray(data.players) ? data.players.filter(player => player && typeof player.id === 'string') : [];
  const byId = new Map(players.map((player, seat) => [player.id, {...player, seat}]));
  const events = (Array.isArray(data.events) ? data.events : []).filter(event => event && numeric(event.t) && event.t >= 0)
    .map((event, order) => ({...event, payload:event.payload || {}, order})).sort((a,b) => a.t-b.t || a.order-b.order);
  const duration = Math.max(0, finite(data.duration), events.length ? events[events.length-1].t : 0);
  const palette = ['#dfb878','#9dcfa8','#aab6e9','#e0a49b','#94cbd8','#ceb1df','#c2ca8a','#e6beaa','#9ab4ca'];
  const lightPalette = ['#8c6129','#3d7448','#6573aa','#9d554b','#357d8c','#7e569a','#6f762e','#a06543','#516f8b'];
  const phases = {setup:'準備',night0:'初夜',dawn:'夜明け',day:'昼の議論',vote:'投票',runoff:'決選投票',execution:'処刑',night:'夜',ended:'ゲーム終了',game_end:'ゲーム終了'};
  const roleNames = {villager:'市民',werewolf:'人狼',seer:'占い師',medium:'霊能者',guard:'狩人',madman:'狂人',fox:'妖狐',baker:'パン屋',nekomata:'猫又',fanatic:'狂信者',greedy_werewolf:'強欲な人狼',wise_werewolf:'賢狼',whispering_madman:'囁く狂人'};
  const teamNames = {village:'市民陣営',wolf:'人狼陣営',fox:'妖狐陣営',...(data.team_names || {})};
  const causeNames = {lynched:'処刑',died_in_night:'夜の死亡',died_in_day:'昼の死亡',sudden_death:'突然死'};
  const iconShapes = [
    ['path',{d:'M12 3L21 8V16L12 21L3 16V8Z'}], ['path',{d:'M12 3L21 12L12 21L3 12Z'}],
    ['path',{d:'M12 3L22 20H2Z'}], ['rect',{x:'5',y:'5',width:'14',height:'14',rx:'3'}],
    ['path',{d:'M12 2L15 8L22 9L17 14L18 21L12 18L6 21L7 14L2 9L9 8Z'}],
    ['path',{d:'M4 20C4 5 12 3 21 3C21 12 19 20 4 20Z M4 20L15 9'}],
    ['path',{d:'M18 4A9 9 0 1 0 20 18A8 8 0 0 1 18 4Z'}],
    ['path',{d:'M12 2L15 8L22 12L15 16L12 22L9 16L2 12L9 8Z'}],
    ['circle',{cx:'12',cy:'12',r:'8'}]
  ];
  let time = 0, playing = false, speed = 10, named = true, follow = true, playerFilter = '', query = '';
  let lastFrame = null, frameId = null, lastCutoff = -1, manualCutoff = null, currentPhase = null, currentEnded = false;
  const formatTime = value => { const seconds = Math.max(0, Math.floor(value)); const h = Math.floor(seconds/3600), m=Math.floor(seconds/60)%60, s=seconds%60; return (h ? h+':' : '') + String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0'); };
  const personName = id => named && byId.has(id) ? String(byId.get(id).name || id) : String(id || '不明な参加者');
  const escapeRegExp = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const ids = [...byId.keys()].sort((a,b) => b.length-a.length);
  const idPattern = ids.length ? new RegExp('(^|[^A-Za-z0-9_-])('+ids.map(escapeRegExp).join('|')+')(?=$|[^A-Za-z0-9_-])','g') : null;
  const bodyText = value => { const text = String(value == null ? '' : value); return named && idPattern ? text.replace(idPattern, (_,before,id) => before+personName(id)) : text; };
  function colorize(element, id) { const seat = byId.has(id) ? byId.get(id).seat : 0; const colors=document.documentElement.dataset.theme==='light'?lightPalette:palette; const color = colors[seat % colors.length]; element.style.setProperty('--seat', color); element.style.setProperty('--seat-bg', color+'18'); }
  function avatar(id) {
    const holder = node('span','avatar'), seat = byId.has(id) ? byId.get(id).seat : 0;
    const svg = document.createElementNS('http://www.w3.org/2000/svg','svg'); svg.setAttribute('viewBox','0 0 24 24'); svg.setAttribute('aria-hidden','true');
    const [tag, attributes] = iconShapes[seat % iconShapes.length], shape = document.createElementNS(svg.namespaceURI,tag);
    for (const [key,value] of Object.entries(attributes)) shape.setAttribute(key,value);
    shape.setAttribute('fill','none'); shape.setAttribute('stroke','currentColor'); shape.setAttribute('stroke-width','1.6'); shape.setAttribute('stroke-linejoin','round');
    svg.append(shape); holder.append(svg); return holder;
  }
  function cutoffAt(value) { let low=0, high=events.length; while (low<high) { const mid=(low+high)>>1; if (events[mid].t <= value) low=mid+1; else high=mid; } return low; }
  function boardAt(cutoff) {
    const alive = new Set(players.map(player=>player.id)), claims = new Map();
    let phase=null, end=null, tallies={};
    for (let index=0; index<cutoff; index++) {
      const event=events[index], payload=event.payload;
      if (event.kind === 'PHASE_STARTED') phase={phase:payload.phase,day:payload.day,end_t:payload.end_t};
      else if (event.kind === 'CO_DECLARED') claims.set(payload.player_id, payload.claimed_role_name || roleNames[payload.claimed_role_id] || payload.claimed_role_id || '役職不明');
      else if (event.kind === 'PLAYER_DIED') alive.delete(payload.player_id);
      else if (event.kind === 'VOTE_RESOLVED') tallies=payload.tallies || {};
      else if (event.kind === 'GAME_ENDED') end=payload;
      else if (phase && (event.kind === 'DAY_EXTENDED' || event.kind === 'DAY_SHORTENED') && numeric(payload.end_t)) phase={...phase,end_t:payload.end_t};
    }
    return {alive,claims,phase,end,tallies};
  }
  function setPlayerFilter(id) { playerFilter=id; $('player-filter').value=id; $('clear-filter').hidden=!id; render(true); }
  function renderPlayers(board) {
    const fragment=document.createDocumentFragment();
    for (const player of players) {
      const live=board.alive.has(player.id), unknown=data.mode==='log', seat=node('button','seat'+(unknown?' unknown':live?'':' dead')); seat.type='button'; colorize(seat,player.id);
      seat.setAttribute('aria-pressed',String(playerFilter===player.id)); seat.title=personName(player.id)+'の発言に絞り込む';
      seat.append(avatar(player.id)); const description=node('div'); description.append(node('div','seat-name',personName(player.id)));
      description.append(node('div','seat-note',named ? player.id : String(player.name || '参加者')));
      description.append(node('span','claim'+(board.claims.has(player.id)?'':' no-claim'),board.claims.has(player.id)?board.claims.get(player.id)+' CO':unknown?'CO未記録':'正式COなし'));
      seat.append(description); const status=node('div','seat-status'), state=node('div'); state.append(node('span','alive-dot'),document.createTextNode(unknown?'生死不明':live?'生存':'死亡')); status.append(state);
      if (numeric(board.tallies[player.id]) && board.tallies[player.id]>0) status.append(node('div','votes','直近 '+board.tallies[player.id]+'票'));
      seat.append(status); seat.addEventListener('click',()=>setPlayerFilter(playerFilter===player.id?'':player.id)); fragment.append(seat);
    }
    const scroll=$('players').scrollLeft; $('players').replaceChildren(fragment); $('players').scrollLeft=scroll;
    $('alive-count').textContent=data.mode==='log'?players.length+'人 · 生死未記録':board.alive.size+' / '+players.length+' 生存';
  }
  function eventText(event) {
    const p=event.payload, name=personName(p.player_id), phase=phases[p.phase] || p.phase || '';
    switch (event.kind) {
      case 'chat': case 'CHAT': case 'CHAT_MESSAGE': return {title:name,text:bodyText(p.message),player:p.player_id,chat:true};
      case 'PHASE_STARTED': return {title:(finite(p.day)>0?'第'+p.day+'日 · ':'')+phase,text:'',divider:true};
      case 'CO_DECLARED': return {title:name+'が'+(p.claimed_role_name || roleNames[p.claimed_role_id] || p.claimed_role_id || '役職')+'をCO',text:bodyText(p.comment),player:p.player_id};
      case 'CO_REPORTED': return {title:name+'が結果を公表',text:personName(p.target_player_id)+'について「'+({wolf:'人狼',not_wolf:'人狼ではない'}[p.claimed_result] || p.claimed_result)+'」と主張',player:p.player_id};
      case 'PLAYER_DIED': return {title:name+'が死亡',text:causeNames[p.public_cause] || '公開された死亡の通知',player:p.player_id};
      case 'VOTE_RESOLVED': return {title:'第'+p.day+'日 · '+(phase || '投票')+'の結果',text:p.lynched_player_id?personName(p.lynched_player_id)+'を処刑':(Array.isArray(p.runoff_candidate_player_ids)&&p.runoff_candidate_player_ids.length?'決選投票: '+p.runoff_candidate_player_ids.map(personName).join('、'):'この投票での処刑はありません'),vote:true};
      case 'GAME_ENDED': return {title:'ゲーム終了',text:'',end:true};
      case 'DAY_EXTENDED': return {title:'昼の議論が延長されました',text:''};
      case 'DAY_SHORTENED': return {title:'昼の議論が短縮されました',text:''};
      case 'VOTE_TIED': case 'TIE': return {title:'投票が同数になりました',text:'次の公開通知を確認します'};
      case 'TIE_RESOLVED_RANDOM': return {title:'同票を抽選で決定',text:personName(p.selected_player_id)+'が処刑対象に選ばれました'};
      case 'GAME_CREATED': return {title:'ゲームの記録が始まりました',text:''};
      default: return {title:'ゲームの通知',text:bodyText(p.public_message || p.message || p.comment || '')};
    }
  }
  function endCard(payload,eventTime) {
    const card=node('section','end-card'); card.append(node('div','end-eyebrow','終了時の公開結果 · '+formatTime(eventTime)));
    const winner=payload.winner_team, label=teamNames[winner] || (winner ? String(winner) : '勝敗の記録なし');
    card.append(node('h3','',winner?label+'の勝利':payload.outcome==='draw'?'引き分け':'ゲーム終了'));
    const list=node('div','end-players'), roles=payload.roles && typeof payload.roles==='object' ? payload.roles : {};
    for (const player of players) {
      const result=roles[player.id] || {}, item=node('div','end-player'); colorize(item,player.id); item.append(node('div','end-name',personName(player.id)));
      item.append(node('div','end-role',result.role || '役職の記録なし'));
      item.append(node('div','end-result'+(result.result==='won'?' won':''),result.result==='won'?'勝利':result.result==='lost'?'敗北':'判定の記録なし')); list.append(item);
    }
    card.append(list); return card;
  }
  function emptyState(title,reason) { const empty=node('div','empty'); empty.append(node('div','empty-mark','◷'),node('h3','',title),node('p','',reason)); return empty; }
  function renderStream(cutoff) {
    const fragment=document.createDocumentFragment(), needle=query.trim().toLocaleLowerCase(); let shown=0;
    for (let index=0; index<cutoff; index++) {
      const event=events[index], info=eventText(event), p=event.payload;
      if (playerFilter && info.player && info.player!==playerFilter) continue;
      if (needle && ![info.title,info.text,p.player_id || '',byId.get(p.player_id)?.name || '',event.kind].join(' ').toLocaleLowerCase().includes(needle)) continue;
      shown++;
      if (info.end) { fragment.append(endCard(p,event.t)); continue; }
      if (info.divider) { fragment.append(node('div','phase-divider',info.title+' · '+formatTime(event.t))); continue; }
      if (info.chat) {
        const item=node('article','message'); colorize(item,p.player_id); item.append(avatar(p.player_id));
        const content=node('div'), top=node('div','message-top'); top.append(node('span','author',info.title),node('time','timestamp',formatTime(event.t)));
        content.append(top,node('p','message-body',info.text)); item.append(content); fragment.append(item);
      } else {
        const item=node('article','system'), top=node('div'); top.append(node('span','system-title',info.title),node('time','timestamp',formatTime(event.t))); item.append(top);
        if (info.text) item.append(node('p','system-body',info.text));
        if (info.vote && p.tallies && typeof p.tallies==='object') {
          const tallies=node('div','tallies');
          for (const [id,votes] of Object.entries(p.tallies).sort((a,b)=>finite(b[1])-finite(a[1]))) if (numeric(votes)&&votes>0) tallies.append(node('span','tally',personName(id)+' '+votes+'票'));
          item.append(tallies);
        }
        fragment.append(item);
      }
    }
    if (!shown) fragment.append(emptyState(events.length?(query||playerFilter?'該当する発言はありません':'まだ公開された会話はありません'):'記録を再生できません',events.length?(query||playerFilter?'検索語や参加者の絞り込みを変更してください。':'再生ボタンを押すか、次のイベントへ進んでください。'):String(data.reason || 'この記録には再生できる公開イベントがありません。')));
    const stream=$('stream'), scroll=stream.scrollTop; stream.replaceChildren(fragment); $('flow-count').textContent=shown+'件';
    if (follow) stream.scrollTop=stream.scrollHeight; else stream.scrollTop=scroll;
  }
  function timer() {
    $('current-time').textContent=formatTime(time); $('seek').value=String(time);
    const remaining=!currentEnded && !data.synthetic_timing && currentPhase && numeric(currentPhase.end_t) ? Math.max(0,currentPhase.end_t-time) : null;
    $('phase-timer').textContent=remaining===null?'—':formatTime(Math.ceil(remaining));
    $('seek').setAttribute('aria-valuetext',formatTime(time)+' / '+formatTime(duration));
  }
  function render(force=false) {
    const cutoff=manualCutoff===null?cutoffAt(time):manualCutoff;
    if (force || cutoff!==lastCutoff) {
      const board=boardAt(cutoff); currentPhase=board.phase; currentEnded=Boolean(board.end); renderPlayers(board); renderStream(cutoff); lastCutoff=cutoff;
      $('day-label').textContent=board.phase?(finite(board.phase.day)>0?'第'+board.phase.day+'日':'第0日'):'ゲーム開始前';
      $('phase-title').textContent=board.end?'ゲーム終了':board.phase?(phases[board.phase.phase] || board.phase.phase || '進行中'):'待機';
      $('phase-card').className='phase-card'+(board.phase?.phase==='day'?' day':board.phase?.phase==='night'||board.phase?.phase==='night0'?' night':'');
      $('previous').disabled=cutoff===0; $('next').disabled=cutoff>=events.length;
      $('record-badge').textContent=board.end?'終了結果を表示中':playing?'再生中':'一時停止';
    }
    timer();
  }
  function setPlaying(value) {
    playing=value; lastFrame=null; if(frameId!==null){cancelAnimationFrame(frameId);frameId=null;} $('play').textContent=playing?'Ⅱ':'▶'; $('play').setAttribute('aria-label',playing?'一時停止':'再生');
    $('record-badge').textContent=currentEnded?'終了結果を表示中':playing?'再生中':'一時停止';
    if (playing) { manualCutoff=null; frameId=requestAnimationFrame(frame); }
  }
  function seek(value,cutoff=null) { time=Math.max(0,Math.min(duration,finite(value))); manualCutoff=cutoff; lastFrame=null; render(true); }
  function frame(now) {
    if (!playing) return;
    if (lastFrame!==null) time=Math.min(duration,time+(now-lastFrame)/1000*speed);
    lastFrame=now; render();
    if (time>=duration) setPlaying(false); else frameId=requestAnimationFrame(frame);
  }
  $('play').addEventListener('click',()=>{ if (!playing&&time>=duration&&duration>0) seek(0,0); setPlaying(!playing); });
  $('restart').addEventListener('click',()=>{ setPlaying(false);seek(0,0); });
  $('finish').addEventListener('click',()=>{ setPlaying(false);seek(duration); });
  $('previous').addEventListener('click',()=>{ setPlaying(false);const index=Math.max(-1,lastCutoff-2);seek(index<0?0:events[index].t,index+1); });
  $('next').addEventListener('click',()=>{ setPlaying(false);const index=Math.min(events.length-1,lastCutoff);if(index>=0)seek(events[index].t,index+1); });
  $('seek').addEventListener('input',event=>seek(Number(event.target.value)));
  $('speed').addEventListener('input',event=>{speed=Number(event.target.value);$('speed-output').textContent=speed+'×';});
  $('phase-jump').addEventListener('change',event=>{if(event.target.value!==''){seek(Number(event.target.value));event.target.value='';}});
  $('search').addEventListener('input',event=>{query=event.target.value;render(true);});
  $('player-filter').addEventListener('change',event=>setPlayerFilter(event.target.value));
  $('clear-filter').addEventListener('click',()=>setPlayerFilter(''));
  $('follow').addEventListener('click',()=>{follow=!follow;$('follow').setAttribute('aria-pressed',String(follow));if(follow)$('stream').scrollTop=$('stream').scrollHeight;});
  $('name-toggle').addEventListener('click',()=>{named=!named;$('name-toggle').textContent=named?'名前表示':'ID表示';$('name-toggle').setAttribute('aria-pressed',String(named));updateFilterNames();render(true);});
  function updateFilterNames(){for(const option of $('player-filter').options)if(option.value)option.textContent=personName(option.value);}
  function setTheme(theme){document.documentElement.dataset.theme=theme;$('theme-toggle').textContent=theme==='dark'?'☼':'☾';$('theme-toggle').setAttribute('aria-label',theme==='dark'?'明るい配色に切り替える':'暗い配色に切り替える');if(lastCutoff>=0)render(true);}
  try{setTheme(localStorage.getItem('aiwolf-replay-theme') || (matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'));}catch(_){setTheme('dark');}
  $('theme-toggle').addEventListener('click',()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';setTheme(theme);try{localStorage.setItem('aiwolf-replay-theme',theme);}catch(_){}});
  document.addEventListener('keydown',event=>{if(event.target.closest('input,select,textarea,button,a'))return;if(event.code==='Space'){event.preventDefault();$('play').click();}else if(event.code==='ArrowLeft'||event.code==='ArrowRight'){event.preventDefault();seek(time+(event.code==='ArrowLeft'?-5:5));}});
  for(const player of players){const option=node('option','',personName(player.id));option.value=player.id;$('player-filter').append(option);}
  for(const event of events)if(event.kind==='PHASE_STARTED'){
    const p=event.payload,label=(finite(p.day)>0?'第'+p.day+'日 · ':'')+(phases[p.phase]||p.phase||'')+' · '+formatTime(event.t),option=node('option','',label);option.value=String(event.t);$('phase-jump').append(option);
    if(duration>0){const tick=node('span','tick');tick.style.left=(event.t/duration*100)+'%';tick.title=label;$('phase-ticks').append(tick);}
  }
  $('metadata').textContent=(data.subtitle?String(data.subtitle)+' · ':'')+players.length+'人 · 公開'+finite(data.public_messages,events.filter(event=>event.kind==='chat').length)+'発言 · '+formatTime(duration);
  const warnings=[];if(data.warning)warnings.push(String(data.warning));if(data.synthetic_timing)warnings.push('この記録の再生時刻は目安です。');if(data.completed===false&&events.length)warnings.push('この記録は途中までです。最後に届いた公開イベントまで再生します。');
  if(warnings.length){$('warning').textContent=warnings.join('\n');$('warning').hidden=false;}
  $('total-time').textContent=formatTime(duration);$('seek').max=String(Math.max(duration,1));
  $('play').disabled=duration<=0;$('seek').disabled=duration<=0;$('restart').disabled=events.length===0;$('finish').disabled=events.length===0;$('phase-jump').disabled=events.length===0;
  if(!events.length)$('transport-note').textContent='再生できる公開イベントがありません';
  render(true);
})();
</script>
</body>
</html>
'''
