# D022 Phase 1.3 の論理時刻と遷移の境界

## Status

Accepted (2026-08-30)

## Context

Phase 1.3 はリアルタイムのタイマーを動かさず、論理時刻でゲーム進行を検証する。
content の時間設定は夜・夜明け直後の発言禁止・昼に加えて、投票・決選投票に
共通の制限時間を持つ。Execution は解決直後に次フェーズへ進むため、制限時間を
持たない。

## Decision

- `GameState` は `started_at` と `advance_phase(now)` の論理時刻を受け取り、
  `phase_ends_at` をサーバ側の締切として検証する。
- Night0 / Night は `rules.night_seconds`、Dawn は
  `rules.silence_after_dawn_seconds`、Day は `rules.day_seconds` を使う。
  Dawn は発言禁止時間そのものとし、Day 開始時から content が許可するチャットを使える。
- Vote / Runoff は `rules.vote_seconds` を使い、`resolve_votes(now)` はサーバ側の
  締切以後だけ集計する。Execution は時間設定がないため `phase_ends_at` を持たない。
  Phase 1.4 の投票結果と Phase 1.6 の勝敗結果が、それぞれ遷移を明示して進める。
- Day の延長は `rules.extension` の alive-player quorum と回数上限を満たすと、
  `seconds_per_extension` を現在の締切へ加算する。
- Day の時短は `rules.shortening.enabled` と alive-player quorum を満たすと、
  締切を合意時刻へ引き下げる。
- Phase 1.3 は Effect を解決せず、role content の `available_from_night` と
  chat channel 宣言だけから、開始フェーズに利用可能な action を列挙する。

## Consequences

テストは実時間を待たず、任意の整数時刻で Night0 から GameEnd まで遷移できる。
Vote の集計は期限時刻で状態機械へ結果を渡し、夜行動の予約・解決と勝敗判定は
後続 Phase の責務である。
