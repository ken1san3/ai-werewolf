# AI人狼 初期設計書

対象: Phase 1（ゲームコア）〜 Phase 2（ネットワーク）
最終更新: 2026-08-29

本書は D001〜D017 の決定を、実装者が上から読める形にまとめたもの。
**判断の理由は `Docs/ai/decisions/` にある。本書は結論だけを述べる。**
恒久的な規約と禁止事項は `AGENTS.md`、検証項目は `Docs/ai/TEST_POLICY.md`。

---

## 1. 目的

複数のAIエージェントが、リアルタイムチャットで議論・推理・欺瞞・投票・能力使用を行う
**汎用人狼ゲーム基盤**。人間参加、複数PC接続、異種LLM対戦、MOD役職追加まで拡張できること。

ルールと役職仕様の参照実装は人狼ジャッジメント（`spec/JUDGMENT_REFERENCE.md`）。

---

## 2. システム構成

```
Werewolf Server            ゲーム状態・ルール・役職・進行・勝敗・公開範囲
      │
   WebSocket (JSON)
      │
AI Client / Human Client   推論・UI。正しいゲーム状態は持たない
      │
   LLM Server              全エージェントで共有（RTX 3070 Ti / 8GB 前提）
```

サーバはゲーム状態の唯一の保持者であり、クライアントから来た操作をすべて検証する。
クライアントには、そのクライアントが知ってよい情報だけを送る。

---

## 3. 用語

| 用語 | 意味 |
|---|---|
| Role | プレイヤーに配られる役職 |
| Modifier | 役職の上に重ねて付与される状態（恋人・狐憑きなど） |
| Team | 勝利判定上の所属 |
| Ability | 自発的に使う能力 |
| Passive | イベントに応じて自動発動する効果 |
| Effect | ゲーム状態への最小処理 |
| WinCondition | 勝利判定 |
| ChatChannel | 参加できるチャットの単位 |
| DeathCause | 死因（内部） |
| 実効属性 | Role の属性に Modifier を重ねた結果 |

---

## 4. データモデル

### 4.1 役職の属性（5軸）

役職は5つの独立した軸を持つ。

```yaml
id: madman
name: 狂人
team: wolf                 # 勝利判定上の所属
count_as: village          # 人数カウント上の扱い: village | wolf | none | by_role
attack_result: die         # 襲撃を受けたとき: die | immune | by_role
inspect_result: not_wolf   # 占い結果: wolf | not_wolf | by_role
medium_result: not_wolf    # 霊能結果: 任意のタグ文字列 | by_role
tags: []                   # 勝利条件などが参照する分類
knows_teammates: false
chat_channels: [public]
```

- `count_as` / `inspect_result` / `medium_result` は省略時 `team` から導出する。
- 占い・霊能は `inspect_result` / `medium_result` を参照する。
- 勝利条件の人数計算は `count_as` を参照する。
- `medium_result` はタグ文字列（`wolf` / `not_wolf` / `大狼` / `子狐` など）。
- `by_role` は Modifier が元役職へ委譲するための値。

呪殺のような「占われると死ぬ」性質は `inspect_result` の値ではなく、
`on_inspected` の Passive として表現する。判定結果と副作用は別物である。

### 4.2 Ability

```yaml
abilities:
  - id: protect
    timing: night_action
    available_from_night: 1
    priority: 30
    target:
      selector: alive_other
      count: 1
    uses:
      per_night: 1
      per_game: null
    restrictions:
      - type: no_same_target_consecutive
        enabled_when: rules.guard.consecutive == false
    effects: [protect]
```

`target` / `restrictions` / `uses` / `available_from_night` の宣言が、
行動の**検証**と**選択肢の列挙**の両方の根拠になる。

`selector` / `restrictions[].type` / `effects[]` は、いずれも content の registry
（`selectors.yaml` / `restriction_types.yaml` / `effects.yaml`）に登録された
IDでなければならない。未登録の参照は起動時にエラーとする。

`enabled_when` は `rules.<path> == true|false` の形に限る。
参照先は `RulesConfig` の bool フィールドであることを起動時に検証する。

### effect の解決 priority

1つの Ability が、異なる priority の effect を持つことがある。
賢狼は襲撃（50）と役職確認（75）を同じ能力から発生させる。

```yaml
effects:
  - id: attack
  - id: inspect_role
    priority: 75
```

省略形の `effects: [protect]` は、その Ability の `priority` を継承する。
priority を明示した effect だけが別枠で解決される。

### 4.3 Passive

Passive は「条件 → 効果」の組を複数持つ。

```yaml
passives:
  - type: retaliate_on_death
    rules:
      - when: { death_cause: attacked }
        target: { selector: alive_by_tag, tag: werewolf, count: 1, pick: random }
        death_cause: retaliation
      - when: { death_cause: lynched }
        target: { selector: alive_all, count: 1, pick: random }
        death_cause: retaliation
```

単一ルールは要素数1のリストとして扱う。

### 4.4 Modifier

役職の上に重ねて付与される状態。プレイヤーは Role を1つ、Modifier を0個以上持つ。

```yaml
id: lover
name: 恋人
kind: modifier
grant:
  timing: in_game          # assignment | in_game
  duration: permanent      # permanent | until_next_dawn | n_nights
win_condition:
  mode: override           # override | add | none
  value: { type: survive_together, with: modifier_holders(lover) }
passives:
  - type: follow_death
    trigger: other_holder_died
knowledge:
  knows: other_holders
chat_channels: [lover]
overrides: {}
exclusions: []
```

- 上書きの無い属性は元役職の値を使う。これが `by_role` の実体である。
- `override` を持つ Modifier が勝利条件を置き換える。競合時は `priority` で決め、
  同値なら起動時エラーとする。
- 付与できない役職は `exclusions` で宣言する。

判定・勝敗・襲撃はすべて **Role と Modifier を合成した実効属性**を経由する。

```python
resolve_effective_attributes(player) -> EffectiveAttributes
```

役職そのものが入れ替わる機構（変化系・怪盗の交換）は Role Replacement と呼び、
Modifier とは別に Phase 8 で設計する。

### 4.5 WinCondition

3つの型を持つ。

```yaml
# 市民陣営: 特定タグの役職が全滅
- type: eliminate_role_tag
  tag: werewolf

# 人狼陣営: count_as の人数比較
- type: count_parity
  subject: wolf
  against: village
  operator: gte

# 妖狐陣営: 他陣営の勝利に便乗
- type: survive_when_others_win
  replaces: true
```

### 4.6 ChatChannel

```
public              全員
wolf                人狼陣営のうち権限を持つ役職
fox                 妖狐
system              サーバからの通知
private:<player_id> 本人のみ
graveyard           死亡者（Phase 7）
spectator           観戦者（Phase 7）
```

閲覧権限はサーバが管理する。権限の無いチャネルの内容はクライアントへ送らない。

---

## 5. ルール設定

グローバル設定と役職固有オプションの2層。

```yaml
# content/presets/standard_9.yaml
rules:
  first_night_seer: random_white   # none | free | random_white
  vote:
    runoff: true
    tie_after_runoff: no_lynch     # no_lynch | random
    tie_without_runoff: no_lynch   # no_lynch | random
    abstain:
      enabled: true                # 「投票しない」を選べるか
      max_per_player: null         # null=無制限 / 整数=1人あたりゲーム全体の上限
    no_selection: invalid_vote     # 時間切れ未選択の扱い: invalid_vote | abstain
    self_vote: false
    reveal: hidden                 # hidden | live | after（投票先の公開範囲）
  guard:
    consecutive: false
    self_guard: false
  medium:
    notify_timing: night
    targets: [lynched, sudden_death]
  wolf_attack:
    target_decision: majority      # majority | designated | random
    tie: random
  co:
    max_per_day: 3
    allow_from_day2: true
    allow_villager_claim: false
  death:
    public_detail: phase           # phase | cause | none
  role_missing:
    enabled: false
    replacement_role_id: villager   # 欠けた役職を何に置き換えるか
  day_seconds: 180
  vote_seconds: 60
  night_seconds: 60
  silence_after_dawn_seconds: 15
  extension:
    max_count: 0
    seconds_per_extension: 120
    approval: majority             # all | majority
  shortening:
    enabled: false                 # 時短。生存者の合意で締切を早める
    approval: majority             # all | majority
  win_evaluation_order: [village, wolf, fox]

roles:
  villager: 3
  werewolf: 2
  seer: 1
  guard: 1
  madman: 1
  medium: 1
```

役職固有オプションは役職定義側に持たせる。
ただし**同じ挙動をグローバルと役職の両方に置かない。**
初日占いはゲーム進行の規則なのでグローバル `rules.first_night_seer` を唯一の設定源とし、
役職側には持たせない（`decisions/D018`）。

```yaml
id: seer
options:
  first_night:
    type: enum
    values: [none, free, random_white]
    default: random_white
```

### 投票の棄権

プレイヤーは投票先として**人を選ぶか、「投票しない」を選ぶ**。
「投票しない」は集計上ひとつの候補として数え、**最多得票なら処刑なし**とする。
単に集計から除くのではない（除くだけなら無効票と区別がつかない）。

- `abstain.enabled: false` のとき「投票しない」は選択肢に出ない
- `abstain.max_per_player` は1人がゲーム全体で棄権できる回数の上限。
  既定は無制限。使い切ったプレイヤーの選択肢から「投票しない」が外れる
- `no_selection` は**時間切れで何も選ばなかった場合**の扱いであり、
  棄権の選択とは別物。`invalid_vote` は集計から除外、`abstain` は棄権票として数える
- **未選択者がいることだけを理由に、ラウンド全体を処刑なしにしてはならない**

同数時の扱いは `tie_after_runoff` / `tie_without_runoff` の
`no_lynch` / `random` で切り替える。

役職欠け（`role_missing`）は bool ではなくオブジェクトとして持つ。
置換先の Role をコアに埋め込むと「役職追加に Python の変更は要らない」という
不変条件に反するため、content 側で指定する。

プリセットの値は初期値であって仕様ではない。
起動時に設定スキーマを検証し、未知のキー・不正値・順序未定義はエラーとする。

---

## 6. ゲーム進行

### 6.1 フェーズ

```
Setup
  ↓
Night0        初日の夜。ゲームはここから始まる
  ↓
Dawn          結果発表。この間は全チャネルで発言できない（15秒ルールの実体）
  ↓
Day           議論（リアルタイムチャット）
  ↓
Vote
  ↓  同数 かつ runoff: true
[Runoff]      条件つき
  ↓
Execution     処刑 + 死亡解決 + 勝敗判定
  ↓
Night         夜行動
  ↓
Dawn ...
  ↓
GameEnd
```

`Night0` と `Runoff` は条件つきフェーズ。同じ仕組みで表現する。
日番号は Night0 → Dawn 1 → Day 1 の順に振る。

### 6.2 Night0

| 項目 | 挙動 |
|---|---|
| 人狼の襲撃 | 不可（`available_from_night: 1`） |
| 人狼チャット | 可能。初動の相談時間 |
| 占い | `first_night_seer` に従う |
| 狩人 | 行動しない。連続ガードの履歴にも残さない |
| 霊能・猫又・パン屋 | 何も起きない |

### 6.3 行動の予約と解決

```
1. 受理    クライアントが行動を送る → accepted / rejected を本人へ返す
           この時点ではゲーム状態を変えない
2. 上書き  締切まで送り直せる。最後のものが有効
3. 解決    締切（夜明け・投票締切）にまとめて解決する
           使用回数の消費はここで起きる
4. 配信    次のフェーズ開始時に一斉送信
```

公開される行動は即時に配信する。

| 行動 | 反映 |
|---|---|
| チャット（public / wolf / fox） | 即時に配信 |
| CO | 即時に配信。残回数は本人宛の ack に含める |
| 投票 | 予約。締切で確定（`vote.見せる` 設定なら投票先は即時配信） |
| 夜の能力 | 予約。夜明けに解決 |

ゲームコアは `submit_action`（予約）と `resolve_pending_actions`（解決）を分けて持つ。
予約は最後の1つだけを保持し、履歴はログに残す。

---

## 7. 夜の解決

### 7.1 解決順

| priority | 処理 |
|---|---|
| 30 | Protect |
| 40 | Inspect（呪殺の死亡フラグもここ） |
| 45 | MediumInspect（霊能。前日以前の死亡者が対象） |
| 50 | Attack |
| 70 | DeathResolve |
| 75 | InspectDeadRole |
| 78 | Retaliation |
| 80 | FollowDeath |
| 90 | GameEndCheck |

値は設定可能。上表は既定値。

確定している相互作用:

- 護衛は人狼の襲撃のみを対象とする。呪殺は防げない。
- 占いは襲撃より先に解決する。**占い師が襲撃された夜でも呪殺は発生する。**
- 同一人物に複数の死亡要因が来た場合、priority が先のものを内部死因とする。
- 強欲な人狼の2人襲撃は、対象ごとに独立に評価する。

### 7.2 DeathCause

```
lynched        処刑
attacked       襲撃
retaliation    道連れ
cursed         呪殺
follow_death   後追い
sudden_death   突然死
ability        その他の能力による死亡
```

上記7つは**コアの語彙**であり、コアが名前で参照してよい唯一の死因である。
`content/death_causes.yaml` はこの7つを必ず宣言し、追加の死因を宣言できる。
起動時に7つが揃っていることを検証する。
コアが参照する死因IDは1箇所（定数）に集約し、発生箇所へ文字列を散らさない。
`PLAYER_DIED` は死因を必ず持つ。死因は Passive / Ability の条件として参照できる。

死亡の連鎖は、発火条件を死因で絞ることで停止する
（道連れは `attacked` と `lynched` でのみ発動し、`retaliation` では発動しない）。
安全弁として連鎖の深さ上限を持ち、上限到達時は警告ログを出して打ち切る。

### 7.3 公開死因

クライアントへ送る死因は3値のみ。**死亡解決時のフェーズから導出する。**

| public_cause | 該当 |
|---|---|
| `lynched` | 処刑 |
| `died_in_day` | 昼の、処刑以外の死亡 |
| `died_in_night` | 夜の死亡（理由を問わずすべて同一） |

```
public_cause =
    lynched         if cause == lynched
    died_in_day     if 死亡解決時のフェーズが昼
    died_in_night   if 死亡解決時のフェーズが夜
```

昼夜の分類はフェーズIDから一意に決まる。

| 分類 | フェーズ |
|---|---|
| 夜 | `night0` / `night` |
| 昼 | `dawn` / `day` / `vote` / `runoff` / `execution` |

**この導出は関数1つに閉じ込める。** 死亡が発生する箇所ごとに公開死因を書かない。
すべての死亡は単一の入口（内部死因を受け取り、内部イベントと公開イベントの
両方を発行する処理）を通す。書き分けが分散した時点で、
新しい死因の追加が漏洩になる（D012 の対応表方式を避けた理由と同じ）。

内部死因ごとの対応表は持たない。フェーズから導出することで、
content が新しい死因を追加しても自動的にマスクされる。

`rules.death.public_detail` を `cause` にすると内部死因が公開される。
デバッグ・観戦・検証用。

### 7.4 能力結果の通知

| 状況 | 通知 |
|---|---|
| 占いの判定結果 | 占い師へ private |
| 妖狐を占って呪殺した | 占い師へは「人狼でない」のみ。呪殺したことは伝えない |
| 霊能の判定結果 | 霊能者へ private |
| 護衛が襲撃を防いだ | 狩人へ private |
| 護衛先が襲撃されなかった | 通知しない |
| 賢狼の役職情報 | 襲撃が成立し対象が死亡した場合のみ、賢狼へ private |
| 襲撃が防がれた | 通知しない。朝の結果から推測する |

護衛成功・妖狐への襲撃・襲撃無効化は、村側からはすべて
「夜に誰も死ななかった」と同一に見える。

---

## 8. 勝利条件と勝敗

```
GameResult
├─ winner_team: TeamId | null
├─ outcome: team_victory | draw
└─ player_results: { player_id: won | lost }
```

`draw` は全員敗北。`player_results` は全員 `lost`。

評価は死亡処理がすべて解決した直後に1回だけ行う（Execution 内と Night 内の2箇所）。

```
1. 生存者0人 → draw
2. rules.win_evaluation_order の順に評価し、最初に成立した陣営を勝者とする
3. 便乗型（妖狐）を適用し、成立していれば勝者を差し替える
```

---

## 9. 通信

### 9.1 共通形

```json
{
  "type": "chat.message",
  "protocol_version": "1.0",
  "event_id": "uuid",
  "game_id": "uuid",
  "seq": 42,
  "timestamp": 0,
  "payload": {}
}
```

### 9.2 送信タイミング

プッシュは2つだけ。

```
フェーズ開始時   → 全員へ
再接続・途中参加 → その1人へ game.state_sync
```

時限で変わるものは終了時刻を渡す。クライアント側でタイマーを持つ。

```json
{ "phase_ends_at": 1735000000 }
```

発言解禁時刻は別フィールドで持たない。
夜明け直後の発言禁止は Dawn フェーズそのもので表現し（`decisions/D022`）、
「Day が始まったら話せる」という規則に還元する。

サーバは締切を自ら強制する。クライアントの時刻を信用しない。

### 9.3 主要メッセージ

```json
{ "type": "player.list",
  "payload": { "players": [{ "player_id": "alice", "display_name": "Alice" }] } }
```

```json
{ "type": "player.deaths",
  "payload": { "deaths": [
    { "player_id": "bob", "day": 1, "public_cause": "died_in_night" },
    { "player_id": "carol", "day": 2, "public_cause": "lynched" }
  ] } }
```

死亡情報は差分ではなく全量。生存者はクライアント側で
「プレイヤー一覧 − 死亡者リスト」として導出する。
昼の開始時に前夜の死亡が、夜の開始時に処刑と昼の死亡が届く。

```json
{ "type": "player.action_state",
  "payload": {
    "phase": "night",
    "day": 2,
    "phase_ends_at": 1735000000,
    "actions": [
      { "type": "ability", "ability_id": "protect",
        "description": "対象1名を人狼の襲撃から護衛する",
        "valid_targets": ["alice", "carol", "dave"],
        "target_count": 1, "uses_remaining": 1 },
      { "type": "chat", "channel": "wolf" }
    ]
  } }
```

内容はゲームコアが生成する。

```python
game.get_available_actions(player_id) -> list[ActionSpec]
```

同じ制約宣言（4.2）から、行動の検証と選択肢の列挙の両方を導く。
`get_available_actions` はプレイヤー視点を引数に取り、
そのプレイヤーが知ってよい情報だけで組み立てる。

```json
{ "type": "action.rejected",
  "payload": { "action": "ability.use", "reason": "invalid_target" } }
```

拒否理由はAIが理解できる粒度にする。

### 9.4 CO

CO は自由発言ではなくシステム操作。

```json
{ "type": "co.declare",
  "payload": { "claimed_role_id": "seer", "comment": "ボブ白" } }
```

```json
{ "type": "co.report",
  "payload": { "kind": "inspect_result",
               "target_player_id": "bob", "claimed_result": "not_wolf" } }
```

サーバは回数制限・2日目の可否・市民騙りの可否を管理する。
**CO の内容が真実かどうかは検証しない。** 偽COは正当な操作である。

---

## 10. ログ

```
logs/<game_id>/
├─ public.jsonl     公開イベント
├─ private.jsonl    役職・秘密チャット・能力使用・内部死因
└─ ai.jsonl         LLM の入出力・レイテンシ・内部判断（Phase 4 以降）
```

イベント自体が公開範囲を持ち、ログ出力側が振り分ける。

リプレイは**記録済みイベントの再生**であり、再計算ではない。
このため、ランダムな選択は結果をイベントとして記録する。

| ランダム要素 | イベント |
|---|---|
| 役職配布 | `ROLE_ASSIGNED` |
| 役職欠け | `ROLE_MISSING_APPLIED` |
| 初日ランダム白 | `INITIAL_INSPECT_GRANTED` |
| 投票同数のランダム処刑 | `TIE_RESOLVED_RANDOM` |
| 道連れ抽選 | `RETALIATION_TARGET_SELECTED` |

乱数は `game.rng` 経由に統一し、テストでは注入可能にする。
試合の完全再現は目標にしない。

保存項目:

```
game_id / 開始・終了日時 / ルール設定の実効値 / player config
役職配布結果 / 全公開チャット / 秘密チャット / 投票 / 能力使用と結果
ゲームイベント / 死因（内部と公開） / 勝敗と各プレイヤーの勝敗
AI内部判断・LLM入出力・レイテンシ
```

---

## 11. 初期実装役職

13種。すべて content の YAML だけで定義する。

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
| `fox` | 妖狐 | fox | none | immune | not_wolf | not_wolf |

| 役職 | 能力 |
|---|---|
| 市民 | なし |
| 占い師 | 毎夜1人を占い「人狼か否か」を判定。妖狐を占うと呪殺 |
| 霊能者 | 毎夜、処刑・突然死した人を判定 |
| 狩人 | 毎夜1人を人狼襲撃から護衛。自分は選べない |
| パン屋 | 生存していれば毎朝、公開通知を出す |
| 猫又 | 襲撃されると人狼から1人、処刑されると生存者から1人をランダムに道連れ |
| 人狼 | 毎夜1人を襲撃。仲間を認識。人狼チャット |
| 強欲な人狼 | ゲーム中一度だけ、夜に2人を襲撃できる |
| 賢狼 | 襲撃が成立し対象が死亡した場合、その役職を知る |
| 狂人 | 仲間を知らない。人狼チャットなし |
| 狂信者 | 仲間を知る。人狼チャットなし |
| 囁く狂人 | 仲間を知る。人狼チャットあり |
| 妖狐 | 襲撃で死なない。占われると呪殺。妖狐チャット |

狂人・狂信者・囁く狂人は5軸がすべて同一で、
`knows_teammates` と `chat_channels` だけが異なる。
人狼・強欲な人狼・賢狼は5軸が同一で Ability だけが異なる。
この2組を分岐なしに表現できることが、設計が機能している証拠になる。

---

## 12. 拡張の指針

1. 役職・陣営・Modifier・死因は content の追加だけで足せる。
2. 未実装の Effect / Passive を参照する定義は、起動時に「未対応」としてエラーにする。
3. 役職IDと表示名を分離し、日本語名は content 側に置く。
4. `available_from_night` のような制約は、特定役職専用にせず汎用の形で持つ。
5. `winner_team` は将来複数勝者（恋人陣営）へ広げる可能性がある。

将来の設計対象:

```
Role Replacement（変化系・怪盗の交換）
具体的な Modifier（恋人 / 狐憑き / 手玉 / 呪い）
graveyard / spectator チャネルの送信
再接続UI・観戦・リプレイ再生
```
