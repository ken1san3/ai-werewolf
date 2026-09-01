# D050 Phase 2 の別プロセス完走検証

## Status

Accepted (2026-09-01、Phase 2.6)

## Context

Phase 2 の完了条件は、複数 Dummy Client が別プロセスから接続し、テストドライバが
`advance_phase` / `resolve_votes` で進行を代行せずに1ゲームを完走することである。
また R-20260901-75 により、単回使用の入室トークンを消費した後は、接続側が
接続トークンを保持しなければ同じ席へ復帰できない。

## Decision

- Phase 2.6 の完走検証は、テストプロセス内の実 WebSocket サーバと、OS の別プロセスで
  起動する9個の Dummy Client で行う。
- Dummy Client は通信プロトコルだけを使い、`game.state_sync` と
  `player.action_state` が列挙した選択肢から投票・能力を送る。ゲームコアを import しない。
- 進行は `WebSocketGameServer` の tick task だけが `advance_if_due` を呼んで行う。
  テストドライバによる `advance_phase` / `resolve_votes` の呼出しは失敗させる。
- 実時間で検証し、テスト用 preset は既存ルールオブジェクトの期間だけを1秒へ置換する。
  時刻やフェーズをテストドライバから進めない。
- 起動側が席ごとの資格情報ファイルを所有する。Dummy Client は Join で得た
  `connection_token` と最後に受信した `seq` を原子的に更新し、再起動したプロセスは
  同じファイルを受け取って Resume する。資格情報は一時ディレクトリだけに置く。

## Consequences

- Phase 3 の AI Client / Brain は先取りしない。Dummy Client は Phase 2 の境界検証専用。
- 9クライアントすべてが別 PID であり、全員が `GAME_ENDED` を受信したことを確認する。
- R-75 の保管責任を、プロセス再起動を含む実行テストで固定する。
