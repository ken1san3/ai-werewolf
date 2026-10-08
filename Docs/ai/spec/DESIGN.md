# AI人狼 初期設計書

対象: Phase 1（ゲームコア）〜 Phase 2（ネットワーク）
最終更新: 2026-08-29

本書は `decisions/` の決定を、実装者が上から読める形にまとめたもの。
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
      selector: alive_all
      count: 1
    uses:
      per_night: 1
      per_game: null
    no_selection: skip             # random | skip
    restrictions:
      - type: no_same_target_consecutive
        enabled_when: rules.guard.consecutive == false
      - type: no_self_target
        enabled_when: rules.guard.self_guard == false
    effects: [protect]
```

`target` / `restrictions` / `uses` / `available_from_night` の宣言が、
行動の**検証**と**選択肢の列挙**の両方の根拠になる。

**対象範囲をルールで切り替える場合は restriction で表現する。**
selector は「ルールを見ない素の母集合」に固定し、ルール依存の絞り込みは
`restrictions[].enabled_when` に置く。狩人の自己護衛は `selector: alive_all` ＋
`no_self_target` で表し、`rules.guard.self_guard: true` のとき制限が外れる。
selector にルール参照を持たせない理由は、`alive_other` を占い師なども共有しており、
1つの selector にルールを埋めると無関係な役職まで影響を受けるためである。
`no_self_target` は `restriction_types.yaml` に登録する。

#### 未選択時の既定挙動

`no_selection` は、解決時刻までに行動が予約されなかった場合の挙動を宣言する。
LLMクライアントは必ず未選択を出すため、宣言は必須とする。

| 値 | 挙動 |
|---|---|
| `random` | **有効対象全員**から `count` 体をランダムに選び、発動させる |
| `skip` | 発動させない。使用回数も消費しない |

`random` の母集合は「提出された候補」ではなく、その時点の有効対象全員である。
`rules.night_action.no_selection` に `random` / `skip` を置くと全能力を一括で上書きし、
`null` のとき各 Ability の宣言に従う。

標準 content は参照実装に合わせ、**`random` を宣言するのは襲撃能力だけ**とする。
占い・護衛・霊能は `skip`。参照実装が未選択時に強制選択するのは襲撃のみで、
占いを自動化すると、沈黙しただけで妖狐を呪殺する事故が起きる
（`spec/JUDGMENT_REFERENCE.md` §5「夜の行動」）。

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
public       全員                      Phase 2
wolf         人狼陣営のうち権限を持つ役職   Phase 2
fox          妖狐                      Phase 2
system       サーバからの通知             Phase 2
lover        恋人                      Phase 8（Modifier と同時）
graveyard    死亡者                    Phase 7
spectator    観戦者                    Phase 7
```

本人だけへの送信はチャネルではなく、イベントの可視性
（`PRIVATE` ＋ `recipient_player_id`、§7.4）で行う。
`private:` のようなチャネルIDは registry に存在しない。

チャネルの宣言は `content/chat_channels.yaml` が持つ。

| フィールド | 意味 |
|---|---|
| `phases` | そのチャネルで発言できるフェーズ |
| `allows_co` | そのチャネルで CO を行えるか |

**コアはチャネルIDを名前で知らない。** 使えるフェーズも CO の可否も、
この宣言からのみ導く。チャネルIDを改名しても Python を変更せずに動くこと（R-20260830-08）。

閲覧権限はサーバが管理する。権限の無いチャネルの内容はクライアントへ送らない。

死亡者には**生存者以上の情報を渡さない**のが既定（`rules.graveyard`、§5）。
`public` の閲覧は継続するが、発言はできず、他プレイヤーの役職も見えない。

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
    self_vote: false
    reveal: hidden                 # hidden | live | after（投票先の公開範囲）
  guard:
    consecutive: false
    self_guard: false              # true で no_self_target 制限が外れる（§4.2）
  night_action:
    no_selection: null             # null=能力ごとの宣言に従う / random | skip=全能力を一括上書き
  medium:
    notify_timing: night           # 霊視できる死因は役職側の宣言（D046）
  wolf_attack:
    target_decision: majority      # majority | random（designated は未定義。§5 参照）
    tie: random
  co:
    max_per_day: 3                 # 1人あたり1日のCO回数上限（null=制限なし）
    allow_villager_claim: false    # 市民を騙れるか
  death:
    public_detail: phase           # phase | cause | none
  sudden_death:
    enabled: false                 # 昼に一度も発言しなかった生存者を Day 終了時に死亡させる
  graveyard:
    view_public: true              # 死亡後も public を閲覧できる
    speak: false                   # 墓場での発言（Phase 7）
    reveal_roles: false            # 死亡者に全員の役職を見せる
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

`wolf_attack.target_decision` の意味:

| 値 | 決定規則 |
|---|---|
| `majority` | 提出された襲撃先を多数決。同数は `tie` に従い `game.rng` で選ぶ |
| `random` | 提出内容を使わず、有効対象全員からランダムに選ぶ |

`designated`（指定者が決める）は、指定者をどう宣言するかが未決のため
**語彙から外し、指定された場合は起動時に拒否する。**

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

### 投票の棄権と集計

投票先は「生存者のうち1人」か「**投票しない**」のいずれか。
「投票しない」は候補ではない。単にその人の票が存在しないことを意味する。

集計は**生存者全員**を対象とし、票を得なかった者も0票として並べる。

```
最多得票者が1人           → その者を処刑
最多得票者が複数           → tie rule に従う
最多得票が0票（誰も投票せず）→ 生存者全員が並ぶ。tie rule に従う
```

`tie_*` が `random` なら生存者全員からランダムに1人、`no_lynch` なら処刑なし。

**最多得票が0票のときは決選投票を行わず、`tie_without_runoff` を直接適用する。**
誰も選ばれていない状態で決選投票をしても同じ状態を繰り返すだけのため。

時間切れで何も選ばなかった場合も「投票しない」と同じ扱いとする。
両者を区別する設定は持たない（`no_selection` は廃止）。

- `abstain.enabled: false` のとき「投票しない」は選択肢に出ない
- `abstain.max_per_player` は1人がゲーム全体で棄権できる回数の上限。
  既定は無制限。使い切ったプレイヤーの選択肢から「投票しない」が外れる
- **未選択者がいることだけを理由に、ラウンド全体を処刑なしにしてはならない**

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

#### 突然死

`rules.sudden_death.enabled: true` のとき、**Day の終了時**に、
その日**公開の操作を一度も行わなかった**生存者を `sudden_death` で死亡させる。
Vote へ入る前に解決するため、突然死した者は投票しない。

公開の操作とは、`public` チャネルへの発言・CO の宣言・CO の報告のいずれかである。
**どれか1つでも行っていれば突然死しない。** 突然死が排除するのは放置プレイヤーであり、
どの手段で参加したかは問わない。

- 判定は Day のみ。Night の沈黙は対象にしない
- 公開死因は他の昼の死亡と同じ `died_in_day` になる（§7.3 の導出に従う）
- 霊能者が突然死者を判定できるかは、役職側の `target.causes` が決める（D046）。
  ルール設定ではない

サーバは**観測できる事実だけ**で判定する。
接続が切れたのか、クライアント側のLLMが応答しなかったのかは区別しない。
どちらも「発言が無かった」として同じに扱う（§9.1）。

**この解決の直後にも勝敗判定を行う。** 判定を走らせる位置は
Execution 内・Night 内・Day 終了時（突然死の解決後）の3箇所になる。

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

**連鎖の深さ上限は設けない。** 死亡は1人につき1回しか記録されず、
死亡済みのプレイヤーは連鎖の対象にならないため、連鎖の長さはプレイヤー人数で
上界が決まる。上限値をルールに置くと、打ち切りが勝敗を変えるうえ、
どこで打ち切られたかが公開情報から推測できてしまう。
新しい Passive を追加するときは、発火条件が死因で絞られていることを確認する。

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

#### 公開通知（`public_notify`）

パン屋のような「生存していることが公開情報になる」能力は、
`PUBLIC_NOTIFY` イベント1種類で表す。

```
PUBLIC_NOTIFY { notify_id }
```

- **payload は `notify_id` のみ。** 発生源の `player_id` を含めない
- **人数を含めない。** 生存しているパン屋が何人でも、1つの `notify_id` につき
  1回だけ発行する。発行回数から生存人数が数えられてはならない
- 表示文（「パンが届けられました」など）は content 側に持ち、コアは通知の有無だけを扱う
- 該当する生存者が0人になった Dawn からは発行されない。
  **止まったこと自体は公開情報**であり、それがこの能力の意味である

複数人が同じ `notify_id` の passive を持つ場合、コアは Dawn ごとに
`notify_id` で重複を排除してから発行する。

---

## 8. 勝利条件と勝敗

```
GameResult
├─ winner_team: TeamId | null
├─ outcome: team_victory | draw
└─ player_results: { player_id: won | lost }
```

`draw` は全員敗北。`player_results` は全員 `lost`。

評価は死亡処理がすべて解決した直後に1回だけ行う。
位置は Execution 内・Night 内・Day 終了時（突然死の解決後、§6.1）の3箇所。

```
1. 生存者0人 → draw
2. rules.win_evaluation_order の順に評価し、最初に成立した陣営を勝者とする
3. 生存している便乗型（survive_when_others_win）を適用する
   - replaces: true  → その陣営を勝者に差し替える（妖狐）
   - replaces: false → winner_team は変えず、その生存者個人のみ won にする
```

条件の参照先は型ごとに異なる。`eliminate_role_tag` は Role の `tags`、
`count_parity` は Role + Modifier の実効属性 `count_as` を見る。
`player_results` も実効属性の `team` で判定するため、勝利陣営の死亡者も `won` になる。

終端結果は最初に成立した1回だけ記録し、`GAME_ENDED` を public に1回だけ発行して
`GameEnd` へ遷移する。

---

## 9. 通信

### 9.1 接続と時刻

**本人確認。** トークンは2種類あり、役割を混ぜてはならない（D048）。

| トークン | いつ | 何を認可するか |
|---|---|---|
| **入室トークン** | ロビー作成時にサーバが席ごとに発行 | その席に着くこと（初回 Join） |
| **接続トークン** | Join 成功時にそのプレイヤーへ private に発行 | 同じ席への再接続 |

`session.join` は入室トークンを受け取り、サーバがトークンから席を引く。
再接続は接続トークンで照合する。**どちらの経路でも `player_id` の
自己申告を信用しない。**

`game.state_sync` は自分の役職・能力結果を含むため、
照合を省くと他プレイヤーの秘匿情報がそのまま漏れる（D001）。
Join も state_sync を返すため、**照合は resume だけでは足りない。**

**時刻源。** サーバの単調時計を唯一の時刻源とする。
サーバは一定間隔（1秒）で全ゲームの `advance_if_due(now)` を呼ぶ。
締切は絶対時刻で保持しているため、tick が遅れても締切は伸びない。
クライアントの時計は表示にのみ使い、判定には使わない。

**切断。** 切断してもゲームは進行し、席も残る。
ゲーム状態はサーバだけが持つため接続に依存しないこと、
および切断で中断する仕様にすると、不利な側が接続を切ることで
ゲームを壊せてしまうことによる。
未選択の能力は各 Ability の `no_selection` に従う（§4.2）。
発言はゼロとして数えるため、`rules.sudden_death.enabled` が true なら突然死しうる。

サーバから見えるのは「その席が黙っている」ことだけで、
クライアント↔サーバの切断と、クライアント↔LLM の障害は区別できない。
規則は観測できる事実だけで書き、原因を推測しない。
AIクライアントはLLMが応答しない場合に代替発言を送らない（黙る）。

### 9.2 共通形

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

**`protocol_version`。** 接続時のハンドシェイクで交換し、
メジャーバージョンが一致しなければ接続を拒否する。
そのうえで全メッセージにも保持する。イベントログがそのまま再生の入力になるため
（D013）、記録された1行だけを見て解釈できる必要がある。

**封筒は strict。** 封筒（`type` / `protocol_version` / `event_id` / `game_id` /
`seq` / `timestamp` / `payload`）に未知のフィールドを許さない。
`payload` の中身は各メッセージ型のスキーマが決める。
外部入力を検証なしに受けないのは D001 の原則そのものであり、
content ローダーが未知キーを全て拒否しているのと同じ扱いにする。
マイナー版で封筒にフィールドを足すときは、版を上げてスキーマを更新する。

**`seq`。** サーバ→クライアントのイベントにのみ付ける。
**プレイヤー単位**で1から単調増加し、欠番を作らない。再接続しても継続する。
クライアント→サーバの要求には付けない。
再接続時はクライアントが受信済みの最終 `seq` を送り、サーバはそれより後を送る。

ゲーム単位にしない理由は、宛先が異なる private イベントが混ざると
各クライアントの受信列に他人宛ての欠番が現れ、
**欠落と他人宛てを区別できなくなる**ためである（D035）。
公開イベントも private イベントも、宛先プレイヤーごとの同じ採番を通す。

`seq` はクライアントの取りこぼし検出のためであり、**順序保証の根拠にはしない。**
権威はサーバの状態であって、クライアントが並べ替えた結果ではない。

Resume の wire 配送は、保持内なら `replay → session.resumed → game.state_sync` の順とする。
replay は元の envelope / `seq` の再送であり、`session.resumed` と全量 sync はその後に
新規採番する。したがって保持内の受信列は checkpoint の次から連続して増加する。
認証前に replay が届く実装は、検証済みの ACK を受けるまで公開・state・checkpoint 更新を
行わず有限 FIFO に保留し、ACK 後に受信順で解放する。保持外では replay を部分返却せず、
`session.resumed` の ACK と authoritative `game.state_sync` で再基準化する。

未認証の接続にはプレイヤー宛ての stream が無く `seq` を振れないため、
Join / Resume に失敗した接続へは拒否イベントを送らず接続を閉じる。

### 9.3 送信タイミング

プッシュは2つだけ。

```
フェーズ開始時   → 全員へ
再接続・途中参加 → その1人へ game.state_sync
```

「途中参加」は**認可済みの席の初回接続**を指す（入室トークンによる Join、§9.1）。
未認可の接続はスナップショットを受け取らない。

時限で変わるものは終了時刻を渡す。クライアント側でタイマーを持つ。

```json
{ "phase_ends_at": 1735000000 }
```

発言解禁時刻は別フィールドで持たない。
夜明け直後の発言禁止は Dawn フェーズそのもので表現し（`decisions/D022`）、
「Day が始まったら話せる」という規則に還元する。

サーバは締切を自ら強制する。クライアントの時刻を信用しない。

### 9.4 主要メッセージ

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

### 9.5 CO

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

サーバは回数制限と市民騙りの可否を管理する。
**CO の内容が真実かどうかは検証しない。** 偽COは正当な操作である。

**CO が行えるのは「その昼に発言できるとき」であり、日数による制限は無い**
（ユーザー確認済み）。したがって CO の列挙は日番号ではなく**発言可否から導く**。
発言が列挙されない状況（死亡、Dawn の沈黙時間、Day 以外のフェーズ）では
CO も列挙しない。両者を別々の条件で書かない。

どのチャネルで CO を行えるかは `chat_channels.yaml` の `allows_co` が宣言する。
コアが特定のチャネルIDを直書きしてはならない（§4.6）。

`rules.co` は受付だけでなく列挙にも効く。`max_per_day` は日ごとのカウンタで、
`allow_villager_claim` は騙り候補の絞り込みに使う。騙れる役職かどうかは
役職 YAML の必須キー `claimable` が宣言し、候補の母集合は**その試合の配役**である
（D044）。コアは役職 ID を知らない。

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
| 初日ランダム白 | `FIRST_NIGHT_INSPECT_TARGET_SELECTED` |
| 投票同数のランダム処刑 | `TIE_RESOLVED_RANDOM` |
| 道連れ抽選 | `PASSIVE_TARGET_SELECTED` |
| 人狼襲撃の同数解決 | `WOLF_ATTACK_TIE_RESOLVED_RANDOM` |
| `wolf_attack.random` の対象選択 | `WOLF_ATTACK_TARGET_SELECTED_RANDOM` |
| 未選択能力の対象選択 | `ACTION_NO_SELECTION_RANDOM_TARGETS_SELECTED` |

**乱数を引く箇所を増やしたら、この表に行を足す。**
候補と選ばれた結果の両方を payload に残す。片方だけでは再生できない。

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
| 狩人 | 毎夜1人を人狼襲撃から護衛。自分を選べるかは `rules.guard.self_guard` |
| パン屋 | 生存していれば毎朝、公開通知を出す。人数は分からない（§7.4） |
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
