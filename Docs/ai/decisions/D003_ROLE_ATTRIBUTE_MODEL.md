# D003 役職属性を5軸へ分離する

Status: Accepted (2026-08-29) / ユーザー決定「分離します」
Supersedes: 初版の「team / alignment の2分離」案

## Context

マスター仕様は役職に `team` しか持たせていない。
参照実装（`spec/JUDGMENT_REFERENCE.md`）を調べたところ、軸は2つでは足りなかった。

| 反例 | 示すこと |
|---|---|
| 大狼: 占い=人狼でない / 霊能=人狼である | 占い結果と霊能結果は独立 |
| 狂人: 勝利=人狼陣営 / カウント=市民 | 勝利陣営と人数カウントは独立 |
| 妖狐: カウント=数えない | カウントは2値ではない |
| 狐憑き: 全属性が「役職に準ずる」 | 属性の委譲が必要 |

## Decision

`team` / `count_as` / `attack_result` / `inspect_result` / `medium_result` の5軸を
独立したフィールドとして持つ。詳細は `spec/DESIGN.md` §4.1。

## Why

- 狂人が白判定になる標準ルールをコア改修なしに表現できる
- 大狼・妖狐・子狐・背徳者を役職YAMLの追加だけで足せる
- 参照実装のデータモデルと一致するため、役職を機械的に移植できる

## Consequences

役職YAMLのフィールドが増える（省略時デフォルトで緩和）。
マスター仕様 §32 の抽象化リストは不足しており、本Decisionを正とする。

## Verification

`Docs/ai/TEST_POLICY.md` を参照。
