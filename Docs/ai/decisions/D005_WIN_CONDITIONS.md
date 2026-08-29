# D005 勝利条件の定義

Status: Accepted (2026-08-29) / 参照実装準拠

## Context

マスター仕様 §10 は村の勝利条件を `eliminate_team: wolf` としているが、
狂人が `team: wolf` であるため、**狂人が生存している限り市民陣営が勝てない**。

参照実装の勝利条件は、市民が「全人狼死亡」、人狼が「市民の数 ≤ 人狼の数」であり、
前者は役職タグの全滅、後者は `count_as` の人数比較で、型が違う。

## Decision

勝利条件を3つの型（`eliminate_role_tag` / `count_parity` / `survive_when_others_win`）で
表現し、判定は死亡処理がすべて解決した直後に1回だけ行う。
詳細は `spec/DESIGN.md` §4.5 §8。

## Why

- 狂人がいても市民が勝てる
- 妖狐・第三陣営を後から追加できる
- 判定タイミングを1箇所に固定することで、最終日の挙動が実装者によってブレない

## Consequences

役職に `tags` が増える。WinCondition 評価器は3型すべてを受け付ける必要がある。

## Verification

`Docs/ai/TEST_POLICY.md` を参照。
