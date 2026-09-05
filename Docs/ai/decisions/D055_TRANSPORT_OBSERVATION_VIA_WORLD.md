# D055 transport observation は World の読み取り API から渡す

## Status

Accepted（2026-09-05。ユーザー決定）

## Context

Phase 3.4 Reaction・Chat Controller の詳細設計が `DESIGN BLOCKED` で止まった。
`ROADMAP.md` §3.4 の完了条件のうち2つが、3.4 が触ってよい層から満たせないためである。

- **`action.rejected` を観測できない。** `ai_client/world/reducer.py` は
  `action.rejected` を既知 server type の名前として持つだけで `apply_server_event` に分岐が無く、
  `ai_client/world/service.py` は typed `ActionRejected` notice を
  `_KNOWN_NOTICE_TYPES` として version commit だけ行い内容を捨てる。
  `WorldSnapshot` / `HistoryView` / `BrainInput` / `ClientSnapshot` のどこからも
  拒否の action / reason / seq を復元できない。
- **server deadline と local clock の対応が無い。** `ai_client/network/client.py` の
  `_deadline_worker` は `sleep(deadline - server_timestamp)` を待つだけで、
  local monotonic の deadline を値として保持しない。`PhaseDeadlineReached` も
  World が内容を捨てる。`ai_client/world/` に `timestamp` の出現は0件で、
  `PhaseView` は `phase_ends_at`（server clock の整数）しか持たない。

承認済み Phase 3.2 設計は World を `NetworkClient.events()` の唯一かつ exclusive な consumer とし、
上位層は World の snapshot / query / update wait だけを使うと定めている。
承認済み Phase 3.3 設計は `phase_ends_at` を local clock と直接比較せず、
送信余裕の決定を 3.4 / 3.5 へ委ねた。

**この矛盾を作ったのは Reviewer の Phase 3.4 設計依頼である**（R-20260905-07）。
Q2 / Q6 で両方を決めよと要求しながら、Constraints で
`ai_client/network/` と `ai_client/world/` の変更を禁じていた。

## Decision

**transport observation は World の読み取り API から上位層へ渡す。**
完了済み Phase 3.1 / 3.2 の公開契約を、この目的に必要な範囲で拡張してよい。

- World は `NetworkClient.events()` の**唯一かつ exclusive な consumer のままである。**
  event fan-out 境界は作らない。Controller が `events()` を読むことも許さない
- World は次の2つを immutable value として保持し、読み取り API で公開する
  - `action.rejected` の観測（少なくとも action / reason / seq）
  - server deadline と local clock の対応。上位層が
    「締切まであとどれだけか」を local clock で導ける形にする
- Network 側は、World がその対応を作れるだけの値を typed notice へ載せる。
  Network が自動再送・自動リトライを行う変更はしない
- `Brain` protocol（`decide(BrainInput) -> BrainDecision`）は変更しない
- `server/` / `protocol/` / ゲームコア / content は変更しない

範囲・retention・version・再接続時の置換規則は Phase 3.4 の詳細設計で決める。

## Why

- ROADMAP §3.4 の完了条件を縮小しない。機能要件の縮小は最後の手段である
- 上位層から見た情報源を World 一本に保つ。`NetworkClient` に読み取り専用アクセサを
  足す案（採らなかった案）は変更量が最小だが、上位層が World と Network の
  二つの情報源を持つことになり、順序と version の一貫性が World 経由より弱くなる
- event fan-out 境界の新設（採らなかった案）は bounded queue / backpressure / 順序 /
  再接続 / secret 境界を新たに設計する必要があり、blast radius が大きい
- 拒否と締切は「サーバが authoritative である」という Design invariant 1 の帰結そのもので、
  クライアントが観測できないままにしておく理由が無い

## Consequences

- **完了済み Phase 3.1 / 3.2 の設計書と実装に変更が入る。**
  `PHASE3_1_NETWORK_CLIENT_DESIGN.md` と `PHASE3_2_WORLD_STATE_MEMORY_DESIGN.md` の
  Public Interfaces を更新する必要がある。既存の完走テストと単体テストへの影響を
  Phase 3.4 の設計で洗い出す
- `WorldSnapshot` の version 契約に新しい情報源が加わる。
  「同じ version なら同じ内容」を壊さないことを設計で示す必要がある
- retention を持つ以上、履歴と同じく上限と欠落の表現（`complete` 相当）が要る
- Phase 3.5 の投票・能力の締切判断も同じ API を使う。3.4 だけの都合で形を決めない
