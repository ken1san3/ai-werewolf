# D020 Event の公開範囲を Event Bus の単一入力にする

## Status

Accepted (2026-08-30)

## Context

Phase 1.2 では Event Bus と public / private / ai の JSONL ログを追加する。
ログ出力先を呼び出し側が選ぶと、秘匿イベントが public 経路へ混入しうる。

## Decision

各 `GameEvent` は `public` / `private` / `ai` の公開範囲を1つ持つ。
`EventBus` は一致する公開範囲の購読者にだけ配信し、`JsonlEventLog` は event 自身の公開範囲から
出力先の JSONL を決める。公開範囲をまたぐ購読は持たない。

Event Bus はゲーム内の単調増加 sequence を割り当てる。この sequence は Phase 2 のネットワーク
プロトコルの `seq` を決めるものではない。

## Consequences

- `ROLE_ASSIGNED` と `ROLE_MISSING_APPLIED` は private event として記録する。
- `public.jsonl` へ役職・Modifier・内部死因を含む payload を発行してはならない。
- ログは event を再計算せず、そのまま再生するための順序付き記録となる。
