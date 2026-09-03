# F007 Network / World State の復旧境界

## Attempt

2026-09-03 Independent Deep Reviewer / Sol。HEAD `2fb517c`、Windows / Python 3.13。
`1892496..2fb517c` を、既存レビューと比較する前に独立して調査した。
production / tests のファイルは変更せず、`python -c` 内のインメモリ入力と localhost の
実 WebSocketGameServer で以下を再現した。修正はしていない。

## Problem

### R-20260903-03: 配送順の既存レビューへの異議

wire `[4, 3, 5]` と `_resume_ack_sequence` は独立に確認した。
しかし Accepted D047:30 と Phase3.1 handoff:24 は replay → ACK → sync を明示している。
新clientはその順序を INVALID_SERVER_MESSAGE にするので、採番説明だけでなく
既存の配送契約との相互運用上の差分である。新IDを作らずR-03へ統合した。

DESIGN CONCERN
Problem:
Accepted D047 の replay → ACK → sync と、現実装の ACK → replay → sync が矛盾する。
Why implementation cannot safely resolve locally:
認証境界・wire順・checkpoint・欠番回復の複数責務にまたがる契約変更であり、
現実装に合わせて canonical を追認する権限は Implementer に無い。
Relevant canonical sources:
`Docs/ai/decisions/D047_PLAYER_STATE_DELIVERY.md` /
`Docs/ai/spec/DESIGN.md` §9.2 /
`Docs/ai/design/PHASE3_1_NETWORK_CLIENT_DESIGN.md`
Recommendation:
Return to Primary Design Gate

### R-20260903-04: 不正回復 sync の後の lifecycle

使用部品:
`tests.test_phase3_1_network_client.FakeSocket` / `MemoryStore` /
`server_event` / `state_sync_payload` と実 `NetworkClient` / `WorldState`。

再現手順:

1. game_id=`game-1`。1本目の FakeSocket に `session.joined seq=1`
   （player_id=p0 / connection_token=token）と正常 `game.state_sync seq=2` を入れる。
   sync は `state_sync_payload()` の action_state.day を1にする。
2. NetworkClient と WorldState を別 task で起動し、world が CURRENT になるまで待つ。
3. 2本目には `session.resumed seq=3`（player_id=p0 / last_seq=2）と sync seq=4 を入れる。
   sync は同 helper の day=99、revealed_roles を
   `[{"player_id":"missing","role_id":"opaque"}]` にする。schema-valid だが world では不正。
4. connector は `lambda uri: next(iter_of_two_sockets)`。
   ReconnectPolicy(initial_delay_seconds=0, jitter_ratio=0) とし、1本目の
   `incoming.put_nowait(None)` で切断する。
5. connection_generation=2 / CONNECTED を待ち、world が queue を drain した後に観測する。
   最後は `await client.stop()` と両 task の gather で清掃する。

実測:

```text
generation=2
network day=99 / world day=1
freshness=CURRENT / is_caught_up=True / malformed_event_count=1
CurrentActionsView: world_last_applied_seq=4 / network_last_seq=4
ChatAction(day=99), VoteAction(day=99) が公開される
```

純粋な `_consume()` 再現でも、不正 sync 直後は STALE だが、その後
`LifecycleChanged(SYNCHRONIZING, CONNECTED)` を渡すと CURRENT になる。
`_has_sync` が以前の接続の成功を保持し、reducer の拒否が seq catch-up を止めないため。

### R-20260903-05: 終了をまたぐ Resume

実サーバ再現に使用した部品:
`tests.test_network_sessions.make_game`（standard_9 / Random(0) / started_at=0）、
`join_message` / `GAME_ID`、GameRegistry、SessionManager、TickDriver、WebSocketGameServer。

再現手順:

1. SessionManager(replay_history_limit=4096)、
   WebSocketGameServer(ticker=TickDriver(registry, clock=lambda:0),
   tick_interval_seconds=3600, max_pending_messages=4096) を localhost port=0 で起動。
2. raw WebSocket で player-0 を join。ACK と初回 sync の2通を読み、
   connection_token と sync.seq（2）を SessionCheckpoint に保存する。
3. retained case は元接続を維持して終了までの wire を読まない。
   sync-only case はここで socket を閉じ、server._contexts が空になってから進める。
4. 最大200回、game_result が有るまで、server._dispatch_lock 内で
   `game.advance_if_due(game.phase_ends_at if game.phase_ends_at is not None else game.phase_started_at)`
   と `server._flush_outbound_deliveries()` を呼ぶ。再現の時間短縮用であり、
   完走 acceptance test の server-tick-only 証拠としては使用しない。
5. retained case も元 socket を閉じる。同 checkpoint の MemoryStore、entry_token=None で
   **新しい** NetworkClient + WorldState を起動する。
6. raw socket と client connector の `connect()` は max_queue=None / close_timeout=0.1 とする。
   受信未消費によるテスト自身の close handshake 待ちを排除するためである。
7. 終了または CONNECTED と queue drain を観測し、client.stop / task gather / server.close で清掃。

実測:

```text
retained:
  exit=ClientExitReason.GAME_ENDED / lifecycle=ENDED / freshness=ENDED
  self_present=False / phase=night, day=5 / last_seq=113
  authoritative full sync の phase=game_end
disconnected-before-end:
  task.done=False / lifecycle=CONNECTED / freshness=CURRENT
  self_present=True / phase=game_end, day=5, phase_ends_at=None / last_seq=5
```

FakeSocket に ACK → GAME_ENDED → sync を流した最小再現でも、全量 sync 1通を未消費で
正常終了した。ACK → 終了済み sync だけなら CONNECTED のまま終了しなかった。
実サーバでは未接続席へ envelope を発行しないので、後者は通常の切断中終了でも起きる。

## Result

全件 `python -m pytest -q --tb=short` は exit 0:
`266 passed, 4 warnings, 592 subtests passed in 167.71s (0:02:47)`。
既存テストの緑と上記の不具合再現は両立する。sandbox 内の先行実行は中断しており
合否資料に使わず、subprocess を許可した全件再実行の生の最終行と exit code を採用した。
R-04 / R-05 は REVIEW_INBOX へ起票。実装変更・テスト一時書換え・protocol変更は無し。

## Do Not Repeat

- 不正 sync の単体テストを `_consume(sync)` 直後で止めない。CONNECTED 通知まで検証する。
- GAME_ENDED まで到達しただけで復元成功と判定しない。self / history / full-sync commit を検証する。
- sync history に終了があるだけで network task が終わると仮定しない。
- R-05 の終了責務、R-03 の ACK 配送契約は Primary Design Gate へ返す。
  Reviewer / Implementer の局所判断で protocol や承認済み設計を追認変更しない。
