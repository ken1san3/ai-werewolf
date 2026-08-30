# D023 投票予約と確定の境界

Status: Accepted (2026-08-30) / Phase 1.4 implementation shape

## Context

Vote / Runoff は `rules.vote_seconds` の共通締切を持つ。投票は締切までの予約であり、
履歴をログへ残しつつ最後の予約だけを集計する。

## Decision

- `GameState.submit_vote` は Vote / Runoff 中の生存者だけを受け付け、最後の投票先を
  上書き保存する。Runoff の投票先は、初回同数で得た候補者だけに限定する。
- `resolve_votes(now)` はサーバ側の締切以後にだけ呼ぶ。`VoteResult` は `lynch` /
  `no_lynch` / `runoff` の3値を返し、Runoff または Execution へ遷移させる。
  Vote / Runoff を `advance_phase` で直接通過させない。
- 棄権は `target_player_id: null` の明示的な選択で、`vote.abstain.enabled` と
  `max_per_player` を検証して締切時に消費する。時間切れ未選択は票を持たず、棄権回数も
  消費しない。集計は全生存者をゼロ票から含める。
- 初回の全員ゼロ票は Runoff に入らず `tie_without_runoff` に従う。その他の初回同数かつ
  `runoff: true` は Runoff、その他の同数は該当する tie rule に従う。
  `random` で選んだ処刑対象は `TIE_RESOLVED_RANDOM` として記録する。
- 投票予約は `VOTE_SUBMITTED` の server event、確定した集計は `VOTE_RESOLVED` の public
  event とする。public event は個々の voter-to-target 対応を含めない。
- `vote.reveal` は `hidden` / `live` / `after` のいずれかとする。`live` は予約時の
  `VOTE_REVEALED_LIVE`、`after` は締切時の `VOTES_REVEALED_AFTER` で対応を公開し、
  `hidden` は公開しない。
- 処刑は `PLAYER_DIED` を server event（内部 `cause: lynched`）と public event
  （`public_cause: lynched`）に分け、内部死因をクライアントへ出さない。

## Verification

`tests/test_voting.py` で予約上書き、自己投票、未選択・棄権、決選投票、同数4パターン、
締切、公開方式、ランダム結果イベント、処刑イベントの公開範囲を検証する。
