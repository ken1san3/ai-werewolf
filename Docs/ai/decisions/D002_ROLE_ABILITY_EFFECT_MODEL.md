# D002 Role / Team / Ability / Passive / Effect / WinCondition の分離

Status: Accepted (2026-08-29) / 出典: マスター仕様 §7 §8 §32 §37.2

## Context

役職ごとの処理をゲームコアに書くと分岐が増殖し、役職追加とMOD対応が不可能になる。

## Decision

Role / Team / Knowledge / Ability / Passive / Effect / WinCondition / ChatPermission を
別概念として実装し、役職定義は外部データから読む。
詳細は `spec/DESIGN.md` §4。

## Why

「狼陣営だが狼を知らない」「村陣営だが狼チャットが見える」といった組み合わせを
コア改修なしに表現でき、AIクライアントも役職名に依存せず動ける。

## Consequences

初期の抽象化コストが上がる代わりに、Phase 8（役職拡張 / MOD）のコストが下がる。

## Related

属性の詳細は D003、Modifier は D008。
