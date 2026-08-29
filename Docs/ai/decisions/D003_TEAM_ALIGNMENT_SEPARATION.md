# D003 team（勝利陣営）と alignment（判定陣営）を分離する

## Status

Accepted (2026-08-29) — ユーザー決定

## Context

マスター仕様は役職に `team` しか持たせていない（§7 / §9 / §31）。
しかし「勝利上どちらか」と「占い・霊能でどう見えるか」は別物である。

| 役職 | 勝利 | 占い判定 |
|---|---|---|
| 人狼 | wolf | 黒（wolf） |
| 狂人 | wolf | 白（village） |
| 妖狐 | fox | 白（village） |
| 村人・占い師等 | village | 白（village） |

`team` 一本で実装すると狂人が黒判定になり、妖狐追加時にコアの作り直しが発生する。
`SPEC_REVIEW.md` A-1 / `OPEN_QUESTIONS.md` Q1。

## Decision

役職定義に **`team` と `alignment` を別フィールドとして持たせる。**

```yaml
id: madman
name: 狂人
team: wolf           # 勝利判定に使う
alignment: village   # 占い・霊能など「見え方」に使う
knows_wolves: false
chat_channels: [public]
```

- `alignment` 省略時は `team` と同値とみなす（標準役職の記述量を増やさないため）。
- 占い・霊能の Effect は `team` を参照してはならない。必ず `alignment` を参照する。
- 「見え方」を変える能力（呪殺耐性・偽判定・判定反転など）は
  `alignment` に対する Effect / Passive として表現する。
- 勝利条件の評価は `team`（および D004 のタグ）のみを参照する。

## Why

- 狂人が白判定になるという標準ルールを、コア改修なしに表現できる
- 妖狐・背徳者・第三陣営を後から追加できる（マスター仕様 §9 の要求）
- 「判定を偽装する役職」を Effect として追加できる余地が残る

## Consequences

- 役職YAMLのフィールドが1つ増える
- 実装時、占い/霊能系Effectのレビュー観点として
  「`team` を見ていないか」が必須チェックになる
- `Alignment` をマスター仕様 §32 の抽象化リストへ追加する必要がある
  （仕様書本体は変更せず、本Decisionを正とする）

## Verification

- 狂人を占って白判定が返るテスト
- 人狼を占って黒判定が返るテスト
- `alignment` 未指定の役職が `team` と同じ判定になるテスト
