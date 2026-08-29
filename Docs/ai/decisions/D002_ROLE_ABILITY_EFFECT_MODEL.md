# D002 Role / Team / Ability / Passive / Effect / WinCondition の分離

## Status

Accepted (2026-08-29) — マスター仕様 §7 / §8 / §32 / §37.2 より

## Context

役職ごとの処理をゲームコアへ書くと `if role == "seer"` の分岐が増殖し、
役職追加・MOD対応が不可能になる。

## Decision

以下を別概念として実装する。

```
Role            プレイヤーに割り当てる役職（能力の集合）
Team            勝利所属
Knowledge       開始時・途中で知る情報（例: 狼仲間を知るか）
Ability         自発的に使う能力
Passive         イベントで自動発動する効果
Effect          ゲーム状態への最小処理（Kill / Protect / Inspect ...）
WinCondition    勝利判定
ChatPermission  参加できるチャットチャネル
```

役職定義はYAML等の外部データから読み込む。ゲームコアに役職名を書かない。
Ability / Effect は priority を持ち、夜の解決順を設定可能にする。

## Why

- 「狼陣営だが狼を知らない」「村陣営だが狼チャットが見える」などを
  コア改修なしで表現できる
- MOD / オリジナル役職を後から追加できる
- AIクライアント側も役職名に依存せず、説明文と available_actions で動ける

## Consequences

- 初期の抽象化コストが上がる
- 単純な役職でも Ability / Effect を経由するため、実装がやや冗長になる
- 代わりに Phase 8（役職拡張 / MOD）のコストが大きく下がる

## Related

`Alignment`（占い・霊能での見え方）を追加することが決定済み。
`decisions/D003_TEAM_ALIGNMENT_SEPARATION.md` を参照。
