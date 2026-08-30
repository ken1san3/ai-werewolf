# D038 コアに閉じた tick 進行ディスパッチ

## Status

Accepted (2026-08-31) / R-20260831-54 の修正

## Context

`TickDriver` は `advance_if_due` だけを呼ぶが、Vote / Runoff は `resolve_votes`、
Execution は次フェーズへの遷移を別途必要とした。その知識を network に置くと、
R-20260831-53 と同じくゲームコアのフェーズ定義が接続層へ漏れる。

## Decision

- `GameState.advance_if_due` はコアの tick 用 API とし、期限到達した Vote / Runoff を
  `VoteResolver` で解決する。
- 締切なしで自動的に完了すべき Execution は、同じ API から次フェーズへ遷移する。
- network ticker は全ゲームへこの API を1回呼ぶだけで、`GamePhase`、投票、Execution の
  分岐を持たない。終端など進行不要な締切なしフェーズの no-op は D037 に従う。
- no-op にも進行戦略にも登録されていない締切なしフェーズは、コアがエラーにして
  無言の停止を防ぐ。

## Consequences

- サーバの ticker だけで、投票・決選投票・Execution を含むゲーム進行を完走できる。
- 新しい締切なし中間フェーズが tick 駆動を要するなら、進行戦略をコアへ追加し、
  network の変更は不要とする。
