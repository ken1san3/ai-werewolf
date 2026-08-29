# D019 Passive の effect priority は Passive 共通値を既定にする

## Status

Accepted (2026-08-30)

## Context

Passive は複数の effect を発火できるが、Ability と異なり解決 priority を宣言できなかった。
このため、猫又の道連れ（78）と妖狐の呪殺（40）を content だけで表現できなかった。

## Decision

Passive は `priority` を1つ持ち、`effects` は `EffectReference` として扱う。
文字列の effect 参照は Passive の `priority` を継承し、必要なら effect ごとに
`{ id, priority }` を書いて上書きできる。

## Why

Passive の各 rule は同じ発火イベントに属するため、通常は Passive 単位の priority で十分である。
一方で、effect ごとの上書きを許すことで Ability と同じ宣言形式を保ち、将来の複合 Passive にも対応できる。

## Consequences

すべての Passive content は priority を明示する。Dawn 通知のように夜の相互作用に属さない
effect は、そのイベント内での順序として `0` を宣言する。
