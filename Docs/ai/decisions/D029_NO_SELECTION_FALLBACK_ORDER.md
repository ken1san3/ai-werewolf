# D029 未選択フォールバックの能力選択順

## Status

Accepted (2026-08-30) / Phase 1.5 review fix

## Context

`pending_actions` は actor ID をキーにし、提出経路では1人が1晩に保持できる予約を
常に1つにしている。一方、`no_selection: random` のフォールバックは全 Ability を走査して
いたため、YAMLだけで複数の random Ability を持つ役職を追加すると、同じ actor の複数予約を
生成できた（R-20260830-31）。

## Decision

未選択フォールバックも actor ごとに最大1つの予約だけを生成する。
`Role.abilities` の YAML 宣言順に、`random`・使用可能・有効対象ありの Ability を調べ、
**最初の1つ**を選んだ時点で停止する。先行する Ability がその夜に使用不能、または有効対象を
持たない場合だけ、次の宣言へ進む。

Ability の `priority` は effect の解決順であり、フォールバックの選択順には使わない。

## Why

提出経路の1人1予約不変条件を保ちつつ、役職作者が Python を変えず YAML の宣言順だけで
フォールバックの優先順位を指定できる。複数同時行動を許すには、提出API・予約の状態表現・
解決順をまとめて再設計する必要があるため、Phase 1.5 では扱わない。

## Consequences

- 複数の `random` Ability を持つ役職は、最初に有効な宣言だけを自動発動する。
- 同時に複数の能力を自動発動させる MOD は、この方式では表現できない。必要になった時点で
  提出経路を含む設計変更として扱う。

## Verification

`tests/test_action_resolver.py` は、2つの `random` Ability を持つ actor が未選択でも
`attack` の予約・解決だけを生成することを固定する。
