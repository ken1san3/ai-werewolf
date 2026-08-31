# D047 プレイヤー視点の状態配信と再接続

## Status

Accepted (2026-08-31、Phase 2.5 implementation)

## Context

D017 はフェーズ開始時の全量状態配信、D028 は認証済みの本人に対する状態復元、
D035 はプレイヤーごとの連続した sequence を定めた。D039 の未接続中の蓄積を実装する。

## Decision

- コアの `PlayerViews` が PUBLIC / PRIVATE のみを購読し、発行時点の core 閲覧権限で
  プレイヤーごとの履歴・公開死亡情報・最後に閲覧できたフェーズを保存する。
  接続状態に依存しない。受理済みチャットも同じ core のチャネル閲覧権限を通して保存する。
- `GameState` に player list / deaths / action state / state sync の参照 API を置く。
  ActionSpec を型ごとに直列化するだけとし、行動可否の再実装はしない。
- `PHASE_STARTED` の購読中に状態を捕捉し、既存の game event に続けて `player.list` /
  `player.deaths` / 本人だけの `player.action_state` を送る。毎 tick や予約直後には送らない。
  死亡通知は既存の公開イベントをそのまま全量リストへ蓄積し、内部死因を再導出しない。
- Join / Resume 成功時は、その接続だけへ `game.state_sync` を送る。内容は players /
  deaths / action_state / self（本人の role・modifier ID）/ revealed_roles / history。
  history は許可済みの game event と chat message の本文で、別イベントの再受信を
  前提にせず CO・能力結果・公開経過・チャットを復元できる。クライアントは sync を
  現在地として既存状態を置換し、history を既存履歴へ追記して二重適用しない。
- 死亡後は新しい private 情報を蓄積しない。公開閲覧が無効ならフェーズ・公開履歴も
  最後に許可されたものを保持する。既知の本人情報は復元できる。
  `graveyard.reveal_roles` が有効な死亡者だけに全員の role ID を含める。
- 検証済みの送信イベントをプレイヤー単位で保存し、Resume は `last_seq` より後のものを
  元の envelope・seq のまま再送する。その後に resume 応答、新しい全量 sync を送る。
  サーバが未発行の seq を申告した Resume は接続置換前に拒否する。
- WebSocket サーバ内の要求処理・tick・送信キュー排出を同じ async lock で直列化し、
  再送・sync・後続イベントの順序とスナップショットの一貫性を維持する。

## Consequences

- 4種類の状態 payload を protocol schema の `$defs` へ追加した。既存の封筒・版は変更しない。
- 切断中に未送信だった情報は core の全量 sync に含まれ、切断直前の受信漏れは replay で補う。
- 履歴はゲームのメモリ内に保持する。プロセス再起動からの復元・履歴圧縮・再接続UIは対象外。
- DESIGN は変更しない。Phase 2.6 の別プロセス Dummy Client 完走と Phase 2 handoff は次の作業。
