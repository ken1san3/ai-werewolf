# D018 初日占いの設定源をグローバルルールへ統一する

## Status

Accepted (2026-08-29)

## Context

初日占いの挙動が `rules.first_night_seer` と `seer.options.first_night` の2箇所で
宣言され、両者が異なる値を持つ場合の優先順位が定義されていなかった。

## Decision

初日占いは `rules.first_night_seer` を唯一の設定源とする。
標準の `seer` content から `options.first_night` を削除する。

## Why

Night0 の進行規則は全ゲーム共通のルール設定として扱うため、役職定義ではなく
グローバル rules に置くことが一貫する。設定源を1つにして矛盾状態を作らない。

## Consequences

Phase 1.2 以降の Night0 実装は `RulesConfig.first_night_seer` だけを参照する。
DESIGN.md §5 の役職固有 option の説明は Reviewer がこの決定に合わせて更新する。
