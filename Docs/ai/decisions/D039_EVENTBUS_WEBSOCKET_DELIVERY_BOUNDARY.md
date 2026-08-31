# D039 EventBus と WebSocket の送信境界

## Status

Accepted (2026-08-31) / Phase 2.3 implementation

## Context

`EventBus` は core 内で `PUBLIC` / `PRIVATE` / `AI` / `SERVER` の可視性を分けるが、
WebSocket は player ごとの単一の送信列を持つ。接続層が core event をそのまま
broadcast すると、recipient 付き private event や内部死因を含む server event が
別の player へ漏れる。ChatChannel の閲覧権限も role 名や固定 channel ID ではなく、
content と実効 role state から決める必要がある。

## Decision

- `EventDeliveryRouter` は `EventBus.PUBLIC` と `EventBus.PRIVATE` にだけ subscribe する。
  `AI` と `SERVER` は WebSocket の subscriber を持たず、内部 event は wire payload に
  変換しない。
- core event は `game.event` の `{event_type, event_payload}` に包み、EventBus の
  `visibility`、`recipient_player_id`、core sequence は送らない。全ての envelope と
  player ごとの network `seq` は `SessionManager` が発行する。
- public event の recipient は `GameState.can_view_public_events`、private event の
  recipient は `GameState.can_receive_private_events` を通す。死亡者は設定により public
  を読めるが、新しい direct private event は受け取らない。
- chat output は `chat.message` とし、recipient 集合を
  `GameState.chat_channel_recipient_ids` から得る。全 Role に宣言された channel は
  content 上の public channel として扱い、死亡後の閲覧も `rules.graveyard.view_public` に
  従う。channel ID の名前は core / network に直書きしない。
- Router は event 発行時点で接続済み player だけを対象にする。未接続 player への
  蓄積・再送は Phase 2.5 の state sync / replay 範囲とし、この Phase では行わない。
- chat の送信者認可、発言可能 phase、CO / Vote / Ability の入力検証は Phase 2.4 の責務で
  あり、出力 Router はすでに受理済みの payload の宛先選択だけを担う。

## Why

visibility の切替と socket 送信を別 API にせず、Router が購読する visibility 自体を
2種類に限定すると、内部 event が broadcast path へ入る経路を構造的に作れない。
generic な `game.event` は新しい core event 名を増やしても network の dispatch を
変更せず、type-specific schema が payload の最低限の形だけを保証する。

## Consequences

- public と private の双方が同一 player sequence を通るため、各 client は欠番なしで
  取りこぼしを検出できる（D035）。
- `graveyard.reveal_roles` を用いる state projection と graveyard / spectator の送信は
  Phase 2.5 / 7 まで実装しない。Phase 2.3 は public event payload と private routing を
  死亡者へ広げない。
- `WebSocketGameServer.publish_channel_message` は server 内部の出力 API であり、外部
  client request ではない。Phase 2.4 は認可済み chat input からこの経路を呼ぶ。
