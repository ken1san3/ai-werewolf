# Phase 3.2 World State・Memory — DESIGN REQUEST

Issued by: Reviewer / Claude（2026-09-02、D051）
For: Detailed Design / Sol
Status: REQUESTED — 成果物 `PHASE3_2_WORLD_STATE_MEMORY_DESIGN.md` が
Reviewer の `DESIGN REVIEW: APPROVED` を得るまで実装へ渡らない（RUNBOOK §4.3）。
**承認は Claude が付ける**（D053。設計を書いた model と別の model が承認する）。

```
DESIGN: REQUIRED
```

## Reason

`ai_client` に新しい module を足し、**3.3〜3.5 が使う public API を新設する。**
D051 の判定条件のうち、次に当たる。

- 新しい module の新設
- 新しい state 構造（世界像）と記憶の上限
- 複数 component 間の責務分担（Network Client ↔ World State ↔ Brain / Controller）
- async / concurrency（`ClientEvent` の bounded queue を誰がどの task で消費するか）
- 実装方法が複数あり、選択を誤ると 3.3〜3.5 へ手戻りが広がる
- Phase の中核となる新機能

`ROADMAP.md` §3.2 は目的とスコープを定めたが、**構造は定めていない。**

## Design scope

`ROADMAP.md` §3.2 の「含む」を、実装可能な構造へ落とす。成果物の節は
`RUNBOOK.md` §4.2 の12項目に従う。

1. **Purpose** — この module が引き受ける責務を1段落で
2. **Files / modules** — `ai_client/` 配下の配置。`network/` との境界
3. **Responsibilities** — 何を持ち、何を持たないか
4. **Public interfaces** — 3.3〜3.5 が呼ぶ読み取り API の形
5. **Data flow** — `ClientEvent` が世界像へ入り、上位が読むまで
6. **State / lifecycle** — 全量 sync による再基準化と、増分適用の関係
7. **Main control flow** — 通常運転1周分
8. **Failure handling** — 未知イベント種別、履歴上限到達、取り込みの遅延
9. **Concurrency assumptions** — 取り込み task と読み取りの関係。lock を持つのか
10. **Explicitly out of scope**
11. **Acceptance criteria** — `ROADMAP.md` §3.2 の完了条件を満たす形で
12. **Required tests** — LLM 無しで走るもの

## Relevant canonical sources

| 何を見るか | どこ |
|---|---|
| サブPhaseの境界と完了条件（**canonical**） | `Docs/ai/ROADMAP.md` §3.2 |
| 上流が渡すもの（`ClientEvent` / `ClientSnapshot` / action handle） | `Docs/ai/design/PHASE3_1_NETWORK_CLIENT_DESIGN.md` の Public Interfaces |
| 実装された上流 | `ai_client/network/` |
| サーバが送る内容 | `Docs/ai/spec/DESIGN.md` §9.3 §9.4、`protocol/aiwolf-v1.schema.json` |
| 欠番・再接続で何が起きるか | `decisions/D049`、`handoffs/PHASE3_1_HANDOFF.md` |

矛盾したときの優先順位: canonical source → 実コード → schema → tests。

## Constraints

- **`server.aiwolf_core` / `server.network` を import しない。**
- 役職名・チャネルID・死因IDをクライアントへ直書きしない。値は受信内容から取る
- **サーバの可否判定を再実装しない。** 行動選択肢は受信した列挙をそのまま持つ
- **推論しない。** Belief / Suspicion / 信頼度 / ライン / 反応スコアは Phase 6
- Network Client の受信を詰まらせない。取り込みが遅れても受信と再接続は続く
- 読み取り API から返る値を書き換えても内部状態が変わらない
- LLM 無しでテストが完走する
- 9プロセス同時稼働の前提を壊さない（RTX 3070 Ti / 8GB。Design invariant 9）

## Out of scope

- Brain interface と Dummy Brain（3.3）
- 発言生成・反応制御（3.4）、投票と能力の選択方針（3.5）
- Belief / Suspicion / Strategy / 重要イベント記憶の評価（Phase 6）
- LLM backend、プロンプト整形、structured output（Phase 4）
- `ai_client/network/` の変更。必要になったら**設計へ書かず Reviewer へ報告する**
- サーバ・schema・ゲームコア・content の変更

## Questions Sol must resolve

`ROADMAP.md` §3.2 が「詳細設計で決定する事項」として明示的に委ねた5点。
**それぞれ、採らなかった案と、採らなかった理由を1〜2行で書くこと。**

1. **世界像の内部表現と、読み取り API の形。**
   3.3〜3.5 は「いま誰が生きているか」「その日の CO は何か」を頻繁に引く。
   毎回作り直すのか、保持して差分更新するのか
2. **履歴の保持単位と上限、上限到達時の捨て方。**
   何を最初に捨てるか。捨てたことを上位が知れるか
3. **全量 sync と増分イベントの適用を1つの経路にするか分けるか。**
   §3.2 の完了条件は「sync だけで構築でき、増分で同じ状態へ到達する」を求めている。
   2経路にすると等価性の担保が実装依存になる
4. **取り込みを Network Client と同じ task で回すか分けるか。**
   `ClientEvent` は bounded queue で、消費が遅れると `CONSUMER_OVERRUN` で
   client が明示終了する（3.1 の設計）。その予算をどう守るか
5. **未知イベント種別を受けたときの扱い。**
   3.1 は schema 検証済みのものだけを渡す。将来 protocol の minor で
   イベントが増えたとき、世界像は無視するのか、記録だけするのか

追加で、設計上どちらでもよいと判断した点は**「決めない」と明示する。**

## 粒度

**完成コードを書かない。関数内部を1行ずつ指定しない。**
シグネチャは公開 API に限る。内部ヘルパーの列挙は不要。
判断基準は「Implementer が重要な設計判断をせずに書けるか」だけである。

## この設計が承認されない条件

- canonical design / accepted decision / protocol・schema と矛盾している
- 上の5点のいずれかが未決のまま残っている
- 責務分離、state / lifecycle、failure handling、concurrency 前提のいずれかが曖昧
- Phase 6 の推論、または 3.3〜3.5 を先取りしている
- 関数内部まで書いてある（コードの二重管理）

---

## Addendum A — 開示済み role の public representation（2026-09-02、R-20260902-124）

Issued by: Reviewer / Claude。**この Addendum は本 REQUEST の一部である。**
`PHASE3_2_WORLD_STATE_MEMORY_DESIGN.md` は `DRAFT` へ戻した。
Sol はここに答えて同じ `_DESIGN.md` を改訂する（新しいファイルを作らない）。
改訂後の `Status: APPROVED` は Claude が付ける（D053）。

### 何が起きているか（Reviewer が実コードで再現した）

`ai_client/world/reducer.py` の `_apply_state_sync()` は
`revealed_roles = payload.get("revealed_roles")` を取得し、
`_valid_state_sync_shape()` は `revealed_roles` を必須 field として shape 検証する。
**しかしその値はどこにも保存されない。**

```
  入力: revealed_roles = [{"player_id": "p1", "role_id": "werewolf"}]
  結果: PlayerView      の field = player_id / display_name / alive / death
        WorldSnapshot   の field = version / freshness / is_caught_up / last_applied_seq /
                                   players / alive_player_ids / deaths / phase / self_view /
                                   history_retention / unknown_event_count /
                                   known_unmodeled_event_count / malformed_event_count
        reducer の属性に reveal を含むものは無い
```

`model.py` の `AbilityResultRecord.revealed_role_id` は占い・霊能の結果であって、
`graveyard.reveal_roles` による墓場開示とは別物である。混同しないこと。

これは**未許可情報の追加ではない。** `server/aiwolf_core/views.py` は
`not player.alive and self.game.rules.graveyard.reveal_roles` のときにだけ
全員の role ID を本人へ送る。D047 は state sync の6要素に `revealed_roles` を含める。
protocol schema も `game.state_sync` の必須 field として検証する。
**サーバが開示を許可した本人視点の事実を、World State 境界で失っている。**
ROADMAP §3.2 は上位層に raw payload を触らせないと定めているので、3.3〜3.5 から回避できない。

### Addendum A で決めること

**それぞれ、採らなかった案と、採らなかった理由を1〜2行で書くこと。**

A1. **置き場所。** `PlayerView` の optional field、独立した immutable な
    view / map、履歴 record のどれにするか。
    3.3〜3.5 が「p1 の開示された役職は何か」を引く形を示すこと

A2. **sync の置換規則。** 全量 sync は再基準化である。
    - 次の sync が `revealed_roles: []` を運んできたとき、以前の開示値は消えるか残るか
    - 同じ sync が同じ player を2回運んだときはどうするか
    - `players` に居ない player ID が `revealed_roles` に現れたときはどうするか
    - 増分イベントで開示が増えることはあるか。無いなら「無い」と明記する

A3. **不変性と読み取り API。** 返した値を外から書き換えても内部が変わらないこと。
    `WorldSnapshot.version` 契約との関係（開示の変化は version を進めるか）

A4. **役職名を直書きしないこと。** role ID は受信値をそのまま持つ。
    クライアント側で役職の意味を解釈しない（Design invariant 4）

A5. **Required tests。** 最低限、次を名前付きで固定する。
    - 非空 `revealed_roles` を含む sync から、raw payload を読まずに
      player ID と role ID を取得できる
    - 続く非開示 sync で以前の開示値が残らない
    - 返却値が immutable である

### Addendum A の Out of scope

- サーバ・protocol schema・`server/aiwolf_core/views.py` の変更
- 開示条件そのもの（`graveyard.reveal_roles` の意味）の再定義
- 開示された役職からの推論（Phase 6）
- Phase 3.2 の他の設計判断の見直し。**A1〜A5 以外は既存の承認内容を維持する**
