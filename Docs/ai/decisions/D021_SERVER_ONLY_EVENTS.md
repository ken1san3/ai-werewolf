# D021 宛先の無い内部イベントは server visibility とする

## Status

Accepted (2026-08-30)

## Context

役職欠けの選択結果はログには残す必要があるが、プレイヤーへ通知してはならない。
recipient を持たない private event では、Phase 2 の送信経路が全員宛てと誤解する余地がある。

## Decision

`EventVisibility.SERVER` を追加する。recipient の無い内部イベントは SERVER を使い、
PRIVATE は常に recipient_player_id を必須とする。SERVER の event はクライアントへ送らず、
ログは private.jsonl へ記録する。

## Consequences

`ROLE_MISSING_APPLIED` は SERVER event となる。Phase 2 のクライアント送信は PUBLIC と
recipient 付き PRIVATE だけを扱えばよい。
