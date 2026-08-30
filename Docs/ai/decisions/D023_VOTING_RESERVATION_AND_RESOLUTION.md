# D023 投票予約と確定の境界

Status: Accepted (2026-08-30) / Phase 1.4 implementation shape

## Context

Vote / Runoff には時間設定がなく、D022 は投票結果が明示的にフェーズを進めると定める。
また、投票は締切までの予約であり、履歴をログへ残しつつ最後の予約だけを集計する。

## Decision

- `GameState.submit_vote` は Vote / Runoff 中の生存者だけを受け付け、最後の投票先を
  上書き保存する。Runoff の投票先は、初回同数で得た候補者だけに限定する。
- `resolve_votes(now)` はサーバ側の締切で呼ぶ。`VoteResult` は `lynch` / `no_lynch` /
  `runoff` の3値を返し、Runoff または Execution へ遷移させる。Vote / Runoff を
  `advance_phase` で直接通過させない。
- 未選択は `invalid_vote` なら集計から除外し、`skip_lynch` なら当該ラウンドを処刑見送りにする。
- 初回同数かつ `runoff: true` は Runoff、その他の同数は該当する tie rule に従う。
  `random` で選んだ処刑対象は `TIE_RESOLVED_RANDOM` として記録する。
- 投票予約は `VOTE_SUBMITTED` の server event、確定した集計は `VOTE_RESOLVED` の public
  event とする。public event は個々の voter-to-target 対応を含めない。
- 処刑は `PLAYER_DIED` を server event（内部 `cause: lynched`）と public event
  （`public_cause: lynched`）に分け、内部死因をクライアントへ出さない。

## Deferred

- `skip_lynch_count` の処刑見送り選択・回数消費は Q26 の決定待ち。
- 個別投票先の即時／締切後公開は Q27 のスキーマ決定待ち。

## Verification

`tests/test_voting.py` で予約上書き、自己投票、未選択、決選投票、同数4パターン、
ランダム結果イベント、処刑イベントの公開範囲を検証する。
