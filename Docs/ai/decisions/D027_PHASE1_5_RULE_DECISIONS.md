# D027 Phase 1.5 の未決ルールに関するユーザー決定

## Status

Accepted (2026-08-30) / ユーザー決定

## Context

Phase 1.5 の Action Resolver は、標準プリセットで使われる範囲だけを実装し、
仕様が決まっていない5点（Q5 / Q16 / Q28 / Q29 / Q30）を推測で埋めずに保留していた。
本決定でこれらを確定し、Phase 1.5 の残りと Phase 1.7 を進められるようにする。

## Decision

### Q5 夜行動が未選択だった場合の既定挙動

Ability ごとに `no_selection: random | skip` を宣言する（必須）。
`random` は有効対象全員から `count` 体をランダムに選び発動、
`skip` は発動させず使用回数も消費しない。
`rules.night_action.no_selection` に値を置くと全能力を一括で上書きし、
`null` のとき各 Ability の宣言に従う。

LLMクライアントは必ず未選択を出すため、フォールバックは省略可能にしない。

### Q16 パン屋の通知の形式

`PUBLIC_NOTIFY { notify_id }` の1種類とする。

- payload に発生源の `player_id` を含めない
- **人数を含めない。** 生存しているパン屋が何人でも、1つの `notify_id` につき
  Dawn ごとに1回だけ発行する。発行回数から生存人数を数えられてはならない
- 表示文は content 側に持つ。コアは通知の有無だけを扱う
- 該当者が0人になった Dawn からは発行されない。止まったこと自体は公開情報である

### Q28 人狼襲撃の `target_decision`

| 値 | 決定規則 |
|---|---|
| `majority` | 提出された襲撃先を多数決。同数は `tie` に従い `game.rng` で選ぶ |
| `random` | 提出内容を使わず、有効対象全員からランダムに選ぶ |

`random` の母集合は Q5 の `no_selection: random` と同じく「有効対象全員」で統一する。
`designated` は指定者の宣言方法が未決のため語彙から外し、指定された場合は起動時に拒否する。

### Q29 死亡連鎖の深さ上限

**上限は設けない。ルールキーも作らない。**

死亡は1人につき1回しか記録されず、死亡済みのプレイヤーは連鎖の対象にならないため、
連鎖の長さはプレイヤー人数で上界が決まる。無限連鎖は構造的に発生しない。
上限を置くと打ち切りが勝敗を変え、どこで打ち切られたかが公開情報から推測できてしまう。

### Q30 `guard.self_guard` の宣言方法

**restriction で表現する。**

```yaml
target:
  selector: alive_all
restrictions:
  - type: no_same_target_consecutive
    enabled_when: rules.guard.consecutive == false
  - type: no_self_target
    enabled_when: rules.guard.self_guard == false
```

selector は「ルールを見ない素の母集合」に固定し、ルール依存の絞り込みは
`restrictions[].enabled_when` に置く。`alive_other` は占い師なども共有しており、
selector にルールを埋めると無関係な役職まで影響を受けるため。
`no_self_target` は `restriction_types.yaml` に登録し、他役職からも再利用する。

## Consequences

- Phase 1.7 の `get_available_actions` は、対象範囲のルール依存を
  restrictions の評価1本で扱える。selector にルール分岐を持ち込まない。
- 役職 YAML に `no_selection` が増える。既存13役職すべてに宣言を足す必要がある。
- 死亡連鎖に関するルールキーは存在しない。新しい Passive を足すときは、
  発火条件が死因で絞られていることを設計時に確認する。

## Verification

TEST_POLICY §4（未選択フォールバック4項目）、§5（公開通知3項目）、
§10（self_guard 3項目）を参照。
