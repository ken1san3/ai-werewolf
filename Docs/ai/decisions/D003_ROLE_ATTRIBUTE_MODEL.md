# D003 役職属性を5軸へ分離する

## Status

Accepted (2026-08-29) — ユーザー決定「分離します」
Supersedes: 初版の「team / alignment の2分離」案（人狼ジャッジメント調査により不足と判明）

## Context

マスター仕様は役職に `team` しか持たせていない（§7 / §9 / §31）。
参照実装である人狼ジャッジメント（`spec/JUDGMENT_REFERENCE.md`）を調べたところ、
**軸は2つではなく5つ**必要だと判明した。

反例:

- **大狼**: 占い結果=人狼でない / 霊能結果=人狼である
  → 占い結果と霊能結果は1つの `alignment` にまとめられない
- **狂人**: 勝利陣営=人狼陣営 / 陣営カウント=市民として数える
  → 勝利陣営と人数カウントは別物
- **妖狐**: 陣営カウント=数えない / 襲撃=死なない
  → カウントは2値ではない
- **狐憑き**: 全属性が「役職に準ずる」
  → 属性は固定値だけでなく元役職への委譲も表現する必要がある

## Decision

役職定義に以下を**独立したフィールド**として持たせる。

```yaml
id: madman
name: 狂人
team: wolf                 # 勝利条件の所属
count_as: village          # 人数カウント上の扱い: village | wolf | none | by_role
attack_result: die         # die | immune | by_role
inspect_result: not_wolf   # wolf | not_wolf | by_role （+ 別途 on_inspect の効果）
medium_result: not_wolf    # wolf | not_wolf | <任意タグ> | by_role
knows_teammates: false
chat_channels: [public]
```

規則:

1. `count_as` 省略時は `team` と同値。`inspect_result` / `medium_result` 省略時は
   `team` から導出（wolf陣営→wolf、それ以外→not_wolf）。
   標準役職の記述量を増やさないため。
2. **占い・霊能の Effect は `team` を参照してはならない。**
   必ず `inspect_result` / `medium_result` を参照する。
3. **勝利条件の人数計算は `count_as` を参照する。`team` を数えてはならない。**
4. 呪殺（妖狐が占われて死ぬ）は `inspect_result` の値ではなく、
   `on_inspected` の Passive / Effect として表現する。
   「占われると死ぬ」と「占い結果が白」は独立した性質である。
5. `by_role` は憑依系役職（狐憑き等）のための委譲。Phase 1 では
   値として定義だけしておき、解決は Phase 8 で実装してよい。
6. `medium_result` は真偽2値ではなく**タグ文字列**とする
   （人狼ジャッジメントには「大狼」「子狐」「悪魔の眷属」判定が存在する）。

## Why

- 狂人が白判定になる標準ルールを、コア改修なしに表現できる
- 大狼・妖狐・子狐・背徳者を役職YAMLの追加だけで足せる
- 「勝利陣営」と「人数カウント」を分けたことで、
  狂人がいても市民が勝てるようになる（`decisions/D005`）
- 参照実装のデータモデルと一致するため、
  人狼ジャッジメントの役職を後から機械的に移植できる

## Consequences

- 役職YAMLのフィールドが増える（省略時デフォルトで緩和）
- レビュー時の必須チェック項目:
  - 占い/霊能のEffectが `team` を見ていないか
  - 勝利判定が `team` の人数を数えていないか
- マスター仕様 §32 の抽象化リストは不足している。本Decisionを正とする
  （仕様書本体は書き換えない）

## Verification

Phase 1 で以下のテストを必須とする。

- 狂人を占って「人狼でない」が返る
- 人狼を占って「人狼である」が返る
- 属性省略の役職が `team` から正しく導出される
- （役職追加後）大狼が 占い=人狼でない / 霊能=人狼である になる
- （役職追加後）狂人が生存していても全人狼死亡で市民陣営が勝利する
