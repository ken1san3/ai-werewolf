# D036 game_id は不透明な非空文字列

## Status

Accepted (2026-08-31) / R-20260830-50 の修正

## Context

ゲームコアは `game_id` に任意の非空文字列を許す一方、protocol schema は UUID format を
要求していた。この差により、コアで作れるゲームが network の送信時 schema 検証で失敗し、
接続だけが異常終了する。

## Decision

- `game_id` は authoritative game creator が選ぶ不透明な非空文字列とする。protocol schema
  は UUID format を要求しない。`event_id` は引き続き UUID である。
- Session は client request と generated server event の両方を schema 検証する。生成した
  server event の検証失敗は server bug として記録し、その接続だけを 1011 で閉じる。
- schema error は `best_match` で原因に近い検証エラーを返す。

## Consequences

- 既存コアの非UUID game ID をそのまま network に登録できる。
- `game_id` の内部構造を client が解釈・検証する必要はない。
