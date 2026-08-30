# D030 available_actions のサービス境界と ActionSpec

## Status

Accepted (2026-08-30) / Phase 1.7 implementation shape

## Context

Phase 1.3 の `_phase_action_kinds` は phase と content timing だけを見た内部用の暫定APIで、
対象、restriction、使用回数を含めなかった。このままクライアント用の選択肢と送信時の
検証を別々に書くと、自己護衛や連続護衛のような制約がずれる（D017）。

## Decision

- `GameState.get_available_actions(player_id)` は `ActionAvailability` 専用サービスへ委譲する。
  状態コンテナは API の入口だけを持ち、列挙ロジックを持たない。
- `ActionSpec` は player-view の不変レコードとし、Ability には `ability_id`、content の
  `description`、`valid_targets`、`target_count`、`uses_remaining` を入れる。Chat は
  `channel`、Vote は `valid_targets` と明示的棄権可否、CO は `co_declare` / `co_report` の
  操作種別として表す。
- Ability の timing / available-from / 使用回数 / target / restriction は
  `ActionConstraints` に集約し、`ActionResolver` と `ActionAvailability` が同じ評価を使う。
  `valid_targets` は、restriction を通る target 組み合わせに含まれる player ID だけを
  selector の順で返す。最終的な組み合わせは従来どおりサーバの `submit_action` が検証する。
- 使用回数が0の Ability は、次の選択肢更新で残回数を伝えるため `uses_remaining: 0` として
  ActionSpec に残す。送信時は使用不能として拒否する。
- CO の受付・回数管理・真偽を問わない内容検証は Phase 2.4 の責務のままとする。
  Phase 1.7 は公開チャネルへの発言が列挙されたときだけ、CO 操作種別を列挙する。

## Why

制約の唯一の評価源を保つため。クライアント用の表示に役職名や秘密の内部状態を渡さず、
既存の server-authoritative な `submit_action` / `submit_vote` と同じ条件から選択肢を作れる。
また、使用済みの一回限り能力を消すのではなく残回数0を返すことで、AI と UI のどちらも
能力が消費済みである理由を解釈できる。

## Consequences

- `_phase_action_kinds` と `PhaseActionKind` は削除する。
- Phase 2 の `player.action_state` は ActionSpec をシリアライズするだけでよく、ネットワーク層に
  行動可否の判断を複製しない。
- CO の具体的な入力フィールドと `rules.co.max_per_day` /
  `rules.co.allow_villager_claim` の受付制約は Phase 2.4 まで実行しない。
  日数制限は設けないため、そのルール設定は持たない。

## Verification

`tests/test_available_actions.py` が Night0 の襲撃非表示、自己護衛・連続護衛の対象列挙、
使用済み強欲な人狼の残回数0、Day / Vote / 死亡者の player-view を検証する。
