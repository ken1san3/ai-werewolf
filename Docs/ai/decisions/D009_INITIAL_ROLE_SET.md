# D009 初期実装役職を13種に確定する

## Status

Accepted (2026-08-29) — ユーザー決定

## Context

参照実装（人狼ジャッジメント）は100種類以上の役職を持つが、全部は実装しない。
「他の役職も次回以降実装できるような余裕を持たせる設計」が要件。

## Decision

### 初期実装する13役職

属性は参照実装の公式ヘルプ準拠（`spec/JUDGMENT_REFERENCE.md`）。
5軸は `decisions/D003`。

| id | 名称 | team | count_as | attack | inspect | medium |
|---|---|---|---|---|---|---|
| `villager` | 市民 | village | village | die | not_wolf | not_wolf |
| `seer` | 占い師 | village | village | die | not_wolf | not_wolf |
| `medium` | 霊能者 | village | village | die | not_wolf | not_wolf |
| `guard` | 狩人 | village | village | die | not_wolf | not_wolf |
| `baker` | パン屋 | village | village | die | not_wolf | not_wolf |
| `nekomata` | 猫又 | village | village | die | not_wolf | not_wolf |
| `werewolf` | 人狼 | wolf | wolf | immune | wolf | wolf |
| `greedy_werewolf` | 強欲な人狼 | wolf | wolf | immune | wolf | wolf |
| `wise_werewolf` | 賢狼 | wolf | wolf | immune | wolf | wolf |
| `madman` | 狂人 | wolf | village | die | not_wolf | not_wolf |
| `fanatic` | 狂信者 | wolf | village | die | not_wolf | not_wolf |
| `whispering_madman` | 囁く狂人 | wolf | village | die | not_wolf | not_wolf |
| `fox` | 妖狐 | fox | **none** | **immune** | not_wolf | not_wolf |

### 各役職の能力（公式説明文より）

| 役職 | 能力 |
|---|---|
| 市民 | なし |
| 占い師 | 毎夜1人を占い「人狼か否か」を判定。妖狐を占うと呪殺 |
| 霊能者 | 毎夜、処刑・突然死した人を判定 |
| 狩人 | 毎夜1人を人狼襲撃から護衛。自分は選べない |
| パン屋 | 生存していれば毎朝パンを焼く。死亡すると以降焼かれない |
| 猫又 | **襲撃されると人狼の中からランダム1人を道連れ。処刑されると生存者の中からランダム1人を道連れ** |
| 人狼 | 毎夜1人を襲撃。仲間を認識。秘密会話 |
| 強欲な人狼 | **ゲーム中一度だけ、夜に2人を襲撃できる** |
| 賢狼 | **襲撃した人の役職を知る** |
| 狂人 | 誰が人狼か知らない。秘密会話なし |
| 狂信者 | 誰が人狼か知っている。秘密会話なし |
| 囁く狂人 | 誰が人狼か知っている。**人狼と秘密会話ができる** |
| 妖狐 | 襲撃で死なない。占われると呪殺。仲間の妖狐と秘密会話 |

### この役職セットが検証するもの

意図せずとも、13役職はアーキテクチャの主要な軸をほぼ網羅している。
**Phase 1 の完了判定はこの13役職が動くことで行う。**

| 検証対象 | 役職の組 |
|---|---|
| 知識とチャット権限が Team と独立している | 狂人 / 狂信者 / 囁く狂人（属性同一・知識とチャットのみ相違） |
| 同一属性で Ability だけが違う | 人狼 / 強欲な人狼 / 賢狼 |
| 第三陣営・count_as: none・襲撃耐性 | 妖狐 |
| 死亡トリガーと死因による分岐 | 猫又 |
| 生存条件つきの毎朝の公開通知 | パン屋 |
| 陣営カウントと勝利陣営の乖離 | 狂人系3種 |

**狂人 / 狂信者 / 囁く狂人を `if` 分岐なしで表現できれば、設計は正しい。**
この3つは5軸すべてが同一で、`knows_teammates` と `chat_channels` だけが違う。

## エンジンへの新規要求

この13役職から、マスター仕様に無い機能要求が判明した。

### 1. 死因（death cause）の区別が必須

猫又は「襲撃された場合」と「処刑された場合」で道連れ対象が違う。
マスター仕様の Effect 一覧には死因の概念がない。

```
DeathCause: lynched | attacked | cursed | follow_death | sudden_death | ability
```

`PLAYER_DIED` イベントは死因を必ず含めること。

### 2. 使用回数に `per_game` が必要

強欲な人狼は「ゲーム中一度だけ」2人襲撃できる。
マスター仕様 §7.2 の `uses: per_night: 1` では表現できない。

```yaml
uses:
  per_night: 1
  per_game: 1
```

### 3. Ability のターゲット数が可変

強欲な人狼の襲撃は対象2人。target selector に `count` が必要。

```yaml
target:
  selector: alive_non_team
  count: 2
```

### 4. RandomTarget Effect

猫又の道連れは対象をランダムに選ぶ。
選択元プールをフィルタで指定できること（「人狼の中から」「生存者の中から」）。

### 5. InspectRole（役職そのものの取得）

賢狼は襲撃対象の**役職**を知る。占い師の「陣営」とは別の Effect。

### 6. 条件つきの毎朝公開通知

パン屋は「生存していれば毎朝」公開イベントを出す。
Passive + 生存条件 + PublicNotify で表現する。

### 7. 死亡の連鎖

猫又の道連れで死んだプレイヤーがさらに何かを誘発しうる。
`decisions/D005` の「全死亡処理が解決した直後に GAME_END_CHECK を1回」で
整合するが、**連鎖の停止条件と無限ループ防止**を実装すること。

## 拡張余地の確保

「次回以降に他の役職を実装できる」ための条件。

1. 役職は content の YAML 追加だけで足せること。
   **Python コードの変更を伴う役職追加は失敗**とみなす。
2. 未実装の Effect / Passive を参照する役職YAMLは、
   起動時に「未対応」と明示してエラーにする（黙って無視しない）。
3. `decisions/D008` の Modifier 機構を Phase 1 で用意しておく
   （恋人・狐憑きは Phase 8 だが、口だけ作る）。
4. `medium_result` はタグ文字列（大狼・子狐・悪魔の眷属のため）。bool にしない。
5. 役職IDと表示名を分離し、日本語名を content 側に置く。

## Consequences

- Phase 1 のテストは13役職すべてを対象にする
- 標準9人村プリセットはこの13役職のうち6種を使う。
  残り7種は別プリセットまたはテスト専用構成で検証する
- 死因の追加により `PLAYER_DIED` イベントのペイロードが変わる。
  Phase 2 のプロトコル設計前に確定していてよかった

## Verification

- 13役職すべてが content の YAML だけで定義できている
- 狂人 / 狂信者 / 囁く狂人がゲームコアの分岐なしで区別される
- 猫又が襲撃されたとき人狼が1人死に、処刑されたとき生存者が1人死ぬ
- 猫又が狩人に護衛された場合、道連れが発動しない
- 強欲な人狼の2人襲撃が1ゲームに1回だけ使える
- 賢狼が襲撃対象の役職を受け取り、他のプレイヤーは受け取らない
- パン屋が死んだ翌朝からパンの通知が止まる
- 妖狐を襲撃しても誰も死なない
- 妖狐を占うと妖狐が死ぬ
- 妖狐生存中に人狼が勝利条件を満たすと妖狐が勝つ
