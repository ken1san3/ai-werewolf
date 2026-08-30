# D037 締切なしフェーズの tick 契約

## Status

Accepted (2026-08-31) / R-20260831-53 の修正。D035 のネットワーク層による
`GAME_END` 除外を置換する。

## Context

D035 の tick 障害隔離では、決着済みゲームをネットワーク層で `GamePhase.GAME_END`
として除外していた。しかし接続状態だけを持つ `server.network` がゲームコアの
フェーズ列挙値を知ると、将来の締切なし終端フェーズの追加時に除外漏れが生じる。

## Decision

- `GameState.advance_if_due` は締切なしフェーズでは例外でなく `False` を返す。
  `False` は「この tick では進行しなかった」を表す。
- `TickDriver` は登録された全ゲームに `advance_if_due` を呼ぶ。ネットワーク層は
  `GamePhase` や終端フェーズの判定を持たない。
- 手動の `advance_phase` は従来どおりコアの遷移規則を検証し、決着済みゲームを
  進めようとすればエラーにする。

## Consequences

- `GAME_END` と将来追加される締切なし終端フェーズは、ネットワーク変更なしで
  ticker の no-op になる。
- 締切なしの中間フェーズも ticker では no-op であり、対応するコア操作が遷移を
  明示的に完了させる。
