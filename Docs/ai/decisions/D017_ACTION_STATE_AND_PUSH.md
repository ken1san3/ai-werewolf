# D017 available_actions はゲームコアが生成し、フェーズ開始時にプッシュする

## Status

Accepted (2026-08-29) — ユーザー設計
「昼、夜の始め、再接続時に死亡者・死亡原因リストを送信（これで生存者も確定できる）」
「能力は夜にしか使わないため、夜の開始時に能力についての情報を送信」

## Context

マスター仕様 §33 は「サーバはAIへ現在可能な操作を具体的に返す」と定めているが、
**その計算をどこが行い、いつ送るか**が書かれていない（旧 `OPEN_QUESTIONS.md` Q23）。

## Decision

### 1. 計算はゲームコアが行う

```python
game.get_available_actions(player_id) -> list[ActionSpec]
```

ネットワーク層はこの結果を整形して送るだけ。判定ロジックを持たない。

理由は送信タイミングとは独立している。同じ問いが2つの形で現れるためである。

```
検証: この行動は合法か？     → validate_action(player, action) -> bool
列挙: 合法な行動を全部挙げよ → get_available_actions(player) -> list
```

**この2つを別々に手書きすると必ずズレる。**
制約は宣言的に1箇所（Ability の `target` / `restrictions` / `uses` /
`available_from_night`）へ持ち、検証と列挙の両方をそこから導く。

プッシュのタイミングが直すのは「陳腐化」であり、
この「不一致」はタイミングでは直らない。両方が必要。

なお、`get_available_actions` は**プレイヤー視点を引数に取る**。
そのプレイヤーが知ってよい情報だけで組み立てること。
人狼の襲撃対象リストは仲間を知っているから作れるリストであり、
狩人の護衛対象リストを同じコードパスで作ると役職情報が漏れる。

### 2. 死亡情報は全量を送る

送信タイミング:

```
昼の開始時    → 全員へ
夜の開始時    → 全員へ
再接続・途中参加時 → その1人へ
```

内容は差分ではなく**その時点の死亡者リスト全量**。

```json
{
  "type": "player.deaths",
  "payload": {
    "deaths": [
      { "player_id": "bob",   "day": 1, "public_cause": "died_in_night" },
      { "player_id": "carol", "day": 2, "public_cause": "lynched" },
      { "player_id": "dave",  "day": 2, "public_cause": "died_in_day" }
    ]
  }
}
```

- 生存者はクライアント側で「プレイヤー一覧 − 死亡者リスト」として導出する
- 全量なので冪等。通常更新と再接続で同じメッセージが使える
- **`public_cause` は D012 の3値のみ。内部死因を絶対に含めない**

この2タイミングで、すべての死亡が過不足なく届く。

```
夜の死亡（襲撃・呪殺・夜の道連れ）      → 昼の開始時に届く
昼の死亡（処刑・処刑時の道連れ・突然死） → 夜の開始時に届く
```

### 3. プレイヤー一覧は別に送る

死亡者リストだけでは生存者を確定できない。母集合が必要。

```
ゲーム開始時       → 全員へ player.list
再接続・途中参加時 → game.state_sync に含める
```

`player.list` は `player_id` と `display_name` のみ。ゲーム中は変化しない。

### 4. 行動情報はそのフェーズの開始時に送る

ユーザーの指摘どおり、D009 の13役職では**能力は夜にしか使わない**。
ただし仕様を「夜のみ」と固定せず、
**「その行動が使えるフェーズの開始時に送る」と一般化する。**

参照実装には昼に使う能力が存在する（独裁者、聖騎士、悪徳政治家など）。
「夜だけ」を前提にすると、そうした役職を追加した時点で設計が壊れる。

```json
{
  "type": "player.action_state",
  "payload": {
    "phase": "night",
    "day": 2,
    "phase_ends_at": 1735000000,
    "actions": [
      {
        "type": "ability",
        "ability_id": "protect",
        "description": "対象1名を人狼の襲撃から護衛する",
        "valid_targets": ["alice", "carol", "dave"],
        "uses_remaining": 1
      },
      { "type": "chat", "channel": "wolf" }
    ]
  }
}
```

強欲な人狼の例（同時噛みの可否が `uses_remaining` として見える）:

```json
{
  "actions": [
    {
      "type": "ability",
      "ability_id": "wolf_attack",
      "valid_targets": ["alice", "bob", "carol"],
      "target_count": 1,
      "uses_remaining": null
    },
    {
      "type": "ability",
      "ability_id": "greedy_double_attack",
      "valid_targets": ["alice", "bob", "carol"],
      "target_count": 2,
      "uses_remaining": 1
    }
  ]
}
```

送信タイミング:

| フェーズ | 送る内容 |
|---|---|
| Night0 / Night | 夜能力、人狼チャット |
| Day | チャット、CO（D007） |
| Vote / Runoff | 投票可能な対象、処刑見送りの可否（D006） |
| 再接続時 | 現在フェーズのもの |

**投票も行動である。** 自己投票の可否や処刑見送りの残回数で
選択肢が変わるため（D006）、Vote フェーズの開始時に送る必要がある。

### 5. 時限で変わるものは「終了時刻」を渡す（Reviewer の前案を撤回）

Reviewer は当初「15秒ルールの解除時に追加プッシュが必要」と述べたが、
**撤回する。**

終了時刻を渡せばクライアント側でタイマーを持てるため、
プッシュは不要でサーバのタイマー管理も減る。

```json
{
  "phase_ends_at": 1735000000,
  "chat_enabled_at": 1734999860
}
```

同じ考え方を延長にも適用する。延長が成立したら `phase_ends_at` を更新して送る。

### 6. 自分の行動で自分の選択肢が変わる場合は、レスポンスに含める

CO回数の消費（D007）や能力の使用回数の消費は、
自分が起こした変化なので**その行動のレスポンスで残数を返せば足りる**。
ブロードキャストしない。

```json
{
  "type": "co.accepted",
  "payload": { "co_remaining_today": 2 }
}
```

## Why

- 死亡情報を全量にしたことで、通常更新と再接続が同じメッセージで済む。
  差分方式にありがちな「取りこぼしで状態がずれる」事故が起きない
- 生存者をクライアントが導出する形にしたため、送信量が小さい
- 行動情報をフェーズ開始時に限ったことで、ブロードキャストは1試合数十回に収まる。
  リアルタイムチャットの量に比べて無視できる
- 時限を終了時刻で表現したことで、サーバがタイマーを持たずに済む
- 「そのフェーズの開始時」と一般化したことで、
  昼に使う能力を後から足しても設計を変えなくてよい

## Consequences

- ネットワーク層に判定ロジックを書いたらレビューで指摘対象
- `get_available_actions` は Phase 1 の時点で用意し、
  Dummy Client のテストもこれを経由させる。
  Phase 2 はこの結果を整形するだけになる
- クライアントは時刻同期をある程度前提にする。
  ずれを許容できるよう、サーバ側でも締切を強制すること
  （クライアントの時刻を信用しない、D001）
- 死亡イベントの送信経路が1つに絞られるため、
  内部死因の漏洩チェックがこの1箇所で済む

## Verification

- `player.deaths` に内部死因（`cursed` 等）が一切現れない
- 昼の開始時の `player.deaths` に前夜の死亡がすべて含まれる
- 夜の開始時の `player.deaths` に処刑と処刑時の道連れが含まれる
- 再接続したクライアントが `game.state_sync` だけで現在状態を復元できる
- Night0 の `player.action_state` に襲撃アクションが含まれない（D015）
- 狩人の `player.action_state` の `valid_targets` に自分が含まれない
- 連続ガード「なし」のとき、前夜の護衛先が `valid_targets` から外れる
- 強欲な人狼が同時噛みを使った後、次の夜の `uses_remaining` が 0 になる
- 死亡プレイヤーの `player.action_state` が空になる
- サーバが `phase_ends_at` を過ぎた行動を拒否する
- ネットワーク層を経由しないコアのテストでも
  `get_available_actions` の結果が同一になる
