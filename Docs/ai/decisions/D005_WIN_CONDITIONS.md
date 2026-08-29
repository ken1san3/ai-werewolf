# D005 勝利条件の定義

## Status

Accepted (2026-08-29) — 人狼ジャッジメント準拠（`D004`）

## Context

マスター仕様 §10 は村の勝利条件を `eliminate_team: target: wolf` としている。
しかし狂人は `team: wolf`（§31で明記）なので、
**狂人が生存している限り市民陣営が勝てない**という不具合になる。
`SPEC_REVIEW.md` A-2 / `OPEN_QUESTIONS.md` Q2。

参照実装の勝利条件:

| 陣営 | 条件 |
|---|---|
| 市民陣営 | 全人狼死亡時 |
| 人狼陣営 | 「市民」の数を「人狼」の数以下にする |
| 妖狐陣営 | 市民陣営か人狼陣営が勝利した時に生存していると、その陣営に代わって勝利 |

## Decision

勝利条件を3種類の型で表現する。

### 1. 役職の全滅（市民陣営）

```yaml
win_conditions:
  - type: eliminate_role_tag
    tag: werewolf
```

役職に `tags: [werewolf]` を持たせ、**陣営ではなくタグの全滅**で判定する。
狂人は `team: wolf` だが `werewolf` タグを持たないため、
狂人が生存していても市民陣営が勝利する。

### 2. 人数比較（人狼陣営）

```yaml
win_conditions:
  - type: count_parity
    subject: wolf       # count_as == wolf の生存数
    against: village    # count_as == village の生存数
    operator: gte       # subject >= against で成立
```

**`count_as`（D003）を数える。`team` を数えない。**
狂人は `count_as: village` なので市民側として数えられる。

### 3. 他陣営の勝利に便乗（妖狐陣営）

```yaml
win_conditions:
  - type: survive_when_others_win
    replaces: true      # 成立した陣営の勝利を奪う
```

### 判定タイミング（OPEN_QUESTIONS Q4 の回答）

`GAME_END_CHECK` は、**そのフェーズの死亡処理がすべて解決した直後に1回だけ**評価する。

```
処刑解決 → 全死亡処理完了 → GAME_END_CHECK
夜行動解決 → 全死亡処理完了 → GAME_END_CHECK
```

- 処刑と襲撃で判定ロジックを変えない。
- 後追い死亡（背徳者等）・呪殺も「死亡処理」に含め、
  すべて解決してから1回だけ判定する。
- 評価順は「役職全滅 → 人数比較」の順とし、
  成立した陣営に対して最後に「便乗」型を適用する。

## Why

- 狂人がいても市民が勝てる（仕様のバグを解消）
- 妖狐・第三陣営を後から追加できる
- 判定タイミングを1箇所に固定することで、
  最終日の「処刑で狼が死んだ」ケースの挙動が実装者によってブレない

## Consequences

- 役職に `tags` フィールドが増える
- Phase 1 の WinCondition 評価器は3型すべてを受け付ける必要がある
  （便乗型は妖狐追加まで未使用でよいが、APIは用意する）

## Verification

- 狂人が生存していても全人狼死亡で市民陣営が勝利する
- 狂人1・人狼1・市民1 の状態で人狼陣営が勝利する（1 >= 2 は偽 → 勝利しない）
- 人狼1・市民1 で人狼陣営が勝利する
- 最終日の処刑で最後の人狼が死亡した場合、市民陣営が勝利する（同時成立しない）
