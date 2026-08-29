# D006 ルールは全てオプション化する（ハードコード禁止）

## Status

Accepted (2026-08-29) — ユーザー決定
「全部対応できるような柔軟性を持たせる」「両対応可能に」「全部オプションで変更可能に」

## Context

参照実装（人狼ジャッジメント）のルール設定は、
グローバル設定と役職固有ルールの2層で数十項目ある（`spec/JUDGMENT_REFERENCE.md` §4）。

ユーザーの意向は「特定の既定値に決め打ちせず、全ての選択肢に対応する」。

## Decision

### 1. ルールは2層構造の設定として持つ

```yaml
# content/presets/standard_9.yaml
rules:
  first_night_seer: random_white   # none | free | random_white
  vote:
    runoff: true                   # 決選投票
    tie_after_runoff: no_lynch     # no_lynch | random
    tie_without_runoff: no_lynch   # no_lynch | random
    skip_lynch_count: 0            # 処刑見送り可能回数
    no_selection: invalid_vote     # invalid_vote | skip_lynch
    self_vote: false
  guard:
    consecutive: false             # 連続ガード
    self_guard: false
  medium:
    notify_timing: night           # night | next_morning
    targets: [lynched, sudden_death]
  wolf_attack:
    target_decision: majority      # majority | designated | random
    tie: random
  role_missing: false              # 役職欠け
  day_seconds: 180
  night_seconds: 60
  silence_after_dawn_seconds: 15   # 15秒ルール（0で無効）
  extension:
    max_count: 0
    seconds_per_extension: 120
    approval: majority             # all | majority

roles:
  villager: 3
  werewolf: 2
  seer: 1
  guard: 1
  madman: 1
  medium: 1
```

### 2. 役職固有オプションは役職定義側に持たせる

```yaml
id: seer
options:
  first_night:
    type: enum
    values: [none, free, random_white]
    default: random_white
```

**ゲームコアはオプションの意味を知らない。**
オプションを解釈するのは、その役職の Ability / Effect である。

### 3. 分岐はコアに書かない

`if rules.guard.consecutive:` のような分岐をゲームコアへ置かない。
ターゲット選択の制約は `restrictions` として宣言的に表現し、
Ability の target selector が評価する。

```yaml
restrictions:
  - type: no_same_target_consecutive
    enabled_by: rules.guard.consecutive == false
```

### 4. プリセットとして配布する

- `content/presets/standard_9.yaml` — 標準9人村
- 将来 `judgment_compatible.yaml` などを追加可能にする

**プリセットの値は「初期値」であって仕様ではない。**
変更してもテスト以外が壊れない構造にすること。

### 5. テストは設定を明示する

既定値に依存したテストを書かない。
各テストは必要なルール設定を明示的に組み立てる。
「連続ガードあり」「なし」の両方のテストを書く。

## Why

- ユーザーが特定ルールに決め打ちしないことを明示的に要求している
- 参照実装が全項目を設定にしている以上、後から足すと分岐が散らばる
- ルールを設定にしておくと、AIの戦略比較実験で条件を変えられる

## Consequences

- Phase 1 の投票実装は「多数決だけ」では済まない。
  決選投票・処刑見送り・無効票・自己投票をすべて扱う必要がある
  （旧 `OPEN_QUESTIONS.md` Q10 はこれで解決）
- `VoteResult` は「処刑あり / 処刑なし / 決選投票へ」の3値を返す
- 設定スキーマのバリデーションが必要（未知キー・不正値を起動時に弾く）
- 設定項目が増えるほどテストの組み合わせが増える。
  重要な組み合わせだけを選ぶ判断が要る

## Verification

- 同一のゲームコアで、連続ガード「あり」「なし」両方のテストが通る
- 初日占い none / free / random_white の3通りが動く
- 投票同数で「決選投票 → 再同数 → 処刑なし」と
  「決選投票なし → ランダム処刑」の両方が動く
- 設定ファイルの不正値が起動時にエラーになる
