# D032 プロトコル封筒の方向境界と strict header

## Status

Accepted (2026-08-30) / R-20260830-42・43・45 の修正

## Context

Phase 2.1 の共通 schema が、`action.rejected` だけを client request から除外していた。
この方法では server 専用の型を増やすたびに除外を足す必要があり、漏れた型を
client request として受け入れてしまう。また、封筒の未知 header を許していたため、
外部入力の境界が content ローダーより緩かった。

## Decision

- 共通封筒 schema は **型名で送信方向を決めない**。方向は `seq` の有無だけで表し、
  client request は `seq` を持たず、server event は正の `seq` を必須とする。
- 各メッセージ型の送受信可否と payload は、Phase 2.4 / 2.5 で追加する型別 schema が
  宣言する。共通 schema は未登録型の方向を推測しない。
- 共通封筒、client request、server event はいずれも strict とし、定義されない header を
  拒否する。payload の未知キー可否は型別 schema の責務とする。
- `timestamp` は送信者が生成する。client request の値は診断用ローカル時刻であり、
  server はゲーム状態・締切の判定に使わない。server event の値だけが server-authoritative
  な時刻である。

## Consequences

- Phase 2.2 の Session は受信時に `client_request`、送信時に `server_event` を選んで
  検証する。型名だけで共通 schema の向きを判定しない。
- `action.rejected` の payload は今期の型別例外として server event 内で検証する。
  他の型別 payload と方向の宣言は、それぞれの受付・状態配信を実装する Phase 2.4 / 2.5
  で追加する。
- 封筒の header を変更する場合は `protocol_version` を更新する（DESIGN §9.2）。
