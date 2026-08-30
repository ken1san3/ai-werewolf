# D035 tick 隔離・プレイヤー単位 sequence・接続置換

## Status

Accepted (2026-08-31) / R-20260830-48・49・51 の修正

## Context

D033 は Session direct reply をゲーム単位で採番していた。宛先が異なる private reply が
混在すると、各プレイヤーの受信列には他人宛ての欠番が現れ、取りこぼし検出と再接続の
`last_seq` が機能しない。また、決着済みゲームの tick 例外と、同一 token の接続並立が
他ゲームの進行・private 配信の境界を壊す。

## Decision

- tick は各ゲームの `advance_if_due` 例外を記録してそのゲームだけを `False` とする。
  `GAME_END` をネットワーク層で除外する部分は D037 により置換され、締切なしフェーズの
  no-op 判定はゲームコアが担う。ticker task 自身にも最終的な例外捕捉を置く。
- server event の `seq` はゲーム単位ではなく**プレイヤー単位**で1から連続して発行し、
  再接続しても継続する。未認証接続にはプレイヤー宛ての stream が無いため、Join / Resume
  に失敗した接続は protocol rejection を送らず policy close する。
- 同一 token の Resume は古い WebSocket を close code 4001 で閉じ、新しい接続へ置き換える。
  1プレイヤーにつき生きた接続は常に1つとする。

## Consequences

- Phase 2.3 の public / private 配信は、宛先プレイヤーごとの同じ sequencer を必ず使う。
- DESIGN §9.2 と D031 の採番単位は Reviewer が本決定に合わせて更新する。
- 切断はゲームの席・ready 状態・進行を変更しない。
