# D007 CO（カミングアウト）をシステム操作として扱う

## Status

Accepted (2026-08-29) — ユーザー決定

## Context

参照実装では CO は自由発言ではなく**専用のシステム操作**で、
「市民騙り できない/できる」「CO回数制限 1日3回/制限なし」
「2日目のCO できない/できる」という設定を持つ
（`spec/JUDGMENT_REFERENCE.md` §4）。

マスター仕様には CO の概念が**一切存在しない**（`SPEC_REVIEW.md` H-1）。

## Decision

CO をプロトコルの一級イベントとして追加する。

Client → Server

```json
{
  "type": "co.declare",
  "payload": {
    "claimed_role_id": "seer",
    "comment": "ボブ白"
  }
}
```

Server → Clients

```json
{
  "type": "co.declared",
  "payload": {
    "player_id": "alice",
    "claimed_role_id": "seer",
    "comment": "ボブ白"
  }
}
```

サーバが検証・管理するもの:

- CO回数制限（`rules.co.max_per_day`）
- 2日目のCO可否（`rules.co.allow_from_day2`）
- 市民騙りの可否（`rules.co.allow_villager_claim`）
- 各プレイヤーの現在のCO内容（公開情報）

**サーバは CO の真偽を検証しない。** 嘘のCOは正当な操作である。
偽COを弾いてはならない。

占い結果などの「結果報告」も、CO と同じ経路で構造化して送れるようにする。

```json
{
  "type": "co.report",
  "payload": {
    "kind": "inspect_result",
    "target_player_id": "bob",
    "claimed_result": "not_wolf"
  }
}
```

これも真偽は検証しない（人狼が偽の占い結果を出せる必要がある）。

## Why

- サーバがCO回数制限を管理できる（クライアント任せにできない）
- AIが「誰が何をCOしたか」を構造化データで受け取れる。
  毎回チャットログから読み取らせるより正確で、トークンも減る
  （マスター仕様 §16「LLMへ全履歴を丸投げしない」と整合）
- 人間クライアントのUIでもCO一覧を表示できる
- マスター仕様 §34「AI出力はStructured Outputを優先」と整合

## Consequences

- プロトコルにイベントが2種類増える
- AI Client の WorldState に `claimed_roles` を持つ根拠ができる
  （マスター仕様 §15 に既に記載あり）
- **偽情報をサーバが素通しする設計**であることを、
  レビュー時に「バリデーション漏れ」と誤認しないよう注意する
- Phase 1（ゲームコア）では不要。Phase 2 のプロトコル設計に含める

## Verification

- CO回数制限を超えた `co.declare` が `action.rejected` になる
- 2日目のCO禁止設定で、2日目の `co.declare` が拒否される
- 人狼が実際とは異なる役職をCOでき、サーバが拒否しない
- 死亡プレイヤーがCOできない
