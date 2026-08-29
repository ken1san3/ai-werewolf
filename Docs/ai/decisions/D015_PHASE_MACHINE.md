# D015 フェーズ状態機械 — 初日は夜から始まる

## Status

Accepted (2026-08-29) — ユーザー決定
「初日は夜から始まり、襲撃は不可能（もしくは役職無しのNPCを強制選択）で
最初の動きを人狼チャットで相談する時間とします」

## Decision

### フェーズ遷移

```
Setup
  ↓  役職配布 / 知識の付与 / チャット権限の付与
Night0                    ← 初日の夜。ゲームはここから始まる
  ↓
Dawn                      ← 結果発表 + 15秒ルール
  ↓
Day                       ← 議論（リアルタイムチャット）
  ↓
Vote
  ↓  同数 かつ 決選投票あり
[Runoff]                  ← 条件つき
  ↓
Execution                 ← 処刑 + 死亡解決 + GAME_END_CHECK
  ↓
Night                     ← 夜行動
  ↓
Dawn ...
  ↓
GameEnd
```

条件つきフェーズ（`Night0` / `Runoff`）を持てる構造にすること。
役職追加でフェーズが増えても状態機械が壊れないようにする。

### Night0 の仕様

| 項目 | 挙動 |
|---|---|
| 人狼の襲撃 | **不可**。この夜は襲撃が発生しない |
| 人狼チャット | **可能**。初動の相談時間として機能する |
| 占い | ルール設定 `first_night_seer` に従う（`none` / `free` / `random_white`、D006） |
| 狩人の護衛 | **行わない**。襲撃が無いため意味がない |
| 連続ガードの履歴 | Night0 は履歴に**残さない**（翌夜の護衛先を制限しない） |
| 霊能 | 対象がいないため何も起きない |
| 猫又・パン屋 | 何も起きない |

### 襲撃を封じる方法 — NPC方式ではなく能力の無効化

ユーザーは「襲撃は不可能（もしくは役職無しのNPCを強制選択）」の2案を挙げた。

**Reviewer は能力の無効化を推奨し、これを採用する。**

```yaml
abilities:
  - id: wolf_attack
    timing: night_action
    available_from_night: 1     # Night0 では使用不可
```

NPC方式を採らない理由:

- 非プレイヤーが `players` に混ざると、
  勝利条件のカウント（`count_as`、D003）に例外が必要になる
- ターゲットセレクタ（`alive_other` 等）すべてに NPC 除外が必要になる
- 猫又の道連れ抽選（`alive_all`、D011）に NPC が混入しうる
- 公開情報（プレイヤー一覧・投票UI）から NPC を隠す処理が要る

**1箇所の設定で済むものを、全体に例外を撒く形にしない。**

`available_from_night` は他の役職でも使える汎用の制約とする
（「3日目から使える」役職を後から足せる）。

### Dawn フェーズ

- 前の夜（または処刑）の結果を公開する。公開死因は D012 の3値
- `silence_after_dawn_seconds`（15秒ルール、D006）はこのフェーズの属性
- GAME_END_CHECK は Dawn の**前**（夜の死亡解決直後）に走る。D005 と整合

### GAME_END_CHECK の位置

D005 のとおり「死亡処理がすべて解決した直後に1回」。
本状態機械では2箇所になる。

```
Execution 内: 処刑と道連れの解決後 → GAME_END_CHECK
Night 内:     夜行動と死亡の解決後 → GAME_END_CHECK
```

引き分け判定（生存者0人、D014）も同じ箇所で行う。

## Why

- 初日夜を人狼の相談時間にすることで、
  初日から人狼陣営が連携でき、初日の議論に情報の非対称性が生まれる
- 初日占いを Night0 に置いたことで、`first_night_seer` の3つの設定値
  （none / free / random_white）がすべて同じフェーズで表現できる
- 条件つきフェーズを許す構造にしたため、
  決選投票と Night0 を同じ仕組みで扱える

## Consequences

- ゲームは Night0 から始まるため、「Day 1」の番号付けを明確にすること
  （Night0 → Dawn 1 → Day 1 → ... とする）
- `available_from_night` という汎用制約が Ability に追加される
- Phase 2 で `player.action_state`（マスター仕様 §33）を返す際、
  Night0 では人狼に襲撃アクションが**出ない**こと

## Verification

- ゲーム開始直後のフェーズが Night0 である
- Night0 で人狼が襲撃アクションを実行できない（`action.rejected` になる）
- Night0 で人狼チャットが使える
- `first_night_seer: random_white` のとき、Night0 で占い師にランダムな白判定が渡る
- `first_night_seer: none` のとき、Night0 で占いが行われない
- Night0 で狩人が護衛した相手を、Night1 で連続ガード制限なしに護衛できる
- 投票が同数かつ決選投票ありのとき Runoff フェーズへ入り、
  決選投票なしのときは入らない
