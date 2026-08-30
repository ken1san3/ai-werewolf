# D033 WebSocket Session 境界と接続トークン

## Status

Accepted (2026-08-30) / Phase 2.2 implementation

## Context

D028 は Join 時の接続トークン発行、トークンによる再接続照合、サーバ単調時計の
1秒 tick を決めた。一方、ゲームコアの EventBus sequence はログ用であり、
WebSocket の接続管理や direct reply を担わない。

## Decision

- `server.network` をゲームコアから独立させ、`GameRegistry` / `SessionManager` /
  `WebSocketGameServer` に接続状態だけを持たせる。切断はゲームの Player や進行を
  変更しない。
- 初回 `session.join` は事前登録済みかつ未接続の `player_id` を1回だけ束縛し、
  `secrets.token_urlsafe(32)` の不透明な bearer token をその接続だけへ返す。
  `session.resume` は token と `last_seq` のみを受け取り、`player_id` を受け取らない。
- Session の direct reply はゲームごとの network `seq` を1から欠番なく発行する。
  core EventBus の `sequence` とは別であり、Phase 2.3 以降は同じ network sequencer を
  経由して core event を WebSocket event に変換する。
- Session 型（Join / Resume / Ready）を protocol schema の `$defs` に置き、受信と送信の
  両方で versioned schema を検証する。未実装の状態再送は Phase 2.5 に委ねる。

## Consequences

- token は JSONL game event に書き込まず、broadcast 経路にも載せない。
- 本番で `GameState` を作る側は `started_at=monotonic_seconds()` を渡し、ticker と
  同じ単調時計の座標系を使う。テストは同じ注入時計を使える。
- 初回 Join の認可方式は未決であり Q37 として残す。Phase 2.2 はリモート公開用の
  ロビー・認証を実装しない。
- 依存 `websockets`、`jsonschema`、`referencing` は network server の runtime dependency
  とする。
