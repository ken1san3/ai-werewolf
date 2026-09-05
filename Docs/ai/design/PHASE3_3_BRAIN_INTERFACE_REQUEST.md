# Phase 3.3 Brain Interface と Dummy Brain — DESIGN REQUEST

Issued by: Reviewer / Claude（2026-09-04、D051）
For: Detailed Design / Sol
Status: REQUESTED — 成果物 `PHASE3_3_BRAIN_INTERFACE_DESIGN.md` が
Reviewer の `DESIGN REVIEW: APPROVED` を得るまで実装へ渡らない（RUNBOOK §4.3）。
**承認は Claude が付ける**（D053。設計を書いた model と別の model が承認する）。

Request status: OPEN

```
DESIGN: REQUIRED
```

## Reason

D051 の「必要」側の列挙に、次のとおり複数当たる。

- **新しい module の新設。** `ai_client/` には今 `network/` と `world/` しか無い。
  production 側に判断を持つ層はまだ1つも無い
- **public API の新設。** 3.4 / 3.5 と Phase 4 の LLM Brain が同じ境界へ入る
- **複数 component の責務分担。** World State（読み取り）↔ Brain（判断）↔
  Controller（送信）↔ Network Client（typed send）の間で、誰が何を持つかを決める
- **async / concurrency。** Brain の応答が遅れたとき、締切をまたいだときの扱い
- **実装方法が複数あり、選択を誤ると手戻りが大きい。** 3.4 / 3.5 と Phase 4 が
  この境界の上に乗る
- **canonical が目的だけを定め、実装構造を定めていない。**
  `ROADMAP.md` §3.3 は WHAT / Scope / Completion Criteria だけである

## Design scope

`ROADMAP.md` §3.3 の「含む」を実装可能な構造へ落とす。
成果物の節は `RUNBOOK.md` §4.2 の12項目に従う。

## Relevant canonical sources

| 何を見るか | どこ |
|---|---|
| サブPhaseの境界と完了条件（**canonical**） | `Docs/ai/ROADMAP.md` §3.3 |
| 上位が読む世界像の API（**実物**） | `ai_client/world/service.py`: `snapshot()` / `current_actions()` / `revealed_role()` / `history()` / `co_for_day()` / `ability_results()` / `wait_for_update()` |
| 承認済み World State 設計 | `design/PHASE3_2_WORLD_STATE_MEMORY_DESIGN.md` の Public Interfaces |
| 送信 API（**実物**） | `ai_client/network/client.py`: `send_chat` / `send_vote` / `send_ability` / `send_co_declare` / `send_co_report` |
| 承認済み Network Client 設計 | `design/PHASE3_1_NETWORK_CLIENT_DESIGN.md`（Addendum B を含む） |
| 3.4 / 3.5 の境界 | `Docs/ai/ROADMAP.md` §3.4 §3.5 |
| 設計の粒度と門 | `decisions/D051_IMPLEMENTATION_DESIGN_GATE.md` |

矛盾したときの優先順位: canonical source → 実コード → schema → tests。

## Questions Sol must resolve

`ROADMAP.md` §3.3 が「詳細設計で決定する事項」として明示的に委ねた4点。
**それぞれ、採らなかった案と、採らなかった理由を1〜2行で書くこと。**

1. **Brain の入出力の型と、`WorldSnapshot.version` の渡し方。**
   世界像は 3.2 の読み取り API から、行動の選択肢は `current_actions()` の handle から渡す。
   - Brain は `WorldState` そのものを受け取るのか、読み取り結果の値だけを受け取るのか
   - `snapshot` と `current_actions()` は別 API である。両方を渡すとき、
     **どの version の世界像に対する選択肢なのか**をどう表現するか
   - Brain が返す判断の型。**handle をそのまま返すのか、識別子を返すのか**
   - `Freshness` が `CURRENT` でないとき、Brain へ何を渡すか（または呼ばないか）

2. **同期か非同期か。応答の締切と、締切を過ぎたときの既定行動。**
   Phase 4 の LLM Brain は数百ミリ秒〜秒単位かかる。3.3 の境界がそれを吸収する。
   - `async def` にするか。するなら Controller はどこで待つか
   - 締切の起点と長さ。`phase_ends_at` との関係
   - 締切を過ぎた / 例外が出た / 何も返さなかったときの既定行動を**誰が決めるか**
   - Brain の遅延が World State の取り込みや Network Client の受信を詰まらせない構造

3. **1フェーズあたりの呼び出し回数。**
   - フェーズ開始時に1回か、状態が変わるたびか、Controller が必要になったときか
   - 同じフェーズで複数回呼ぶ場合、前回の判断との関係（上書きか、追加か）
   - 呼び出しの起点を `wait_for_update(after_version)` に置くか、別の trigger にするか

4. **差し替えの単位。**
   - プロセス起動時に固定するか、実行中に差し替えられるか
   - 差し替えの入口（構築時の引数、factory、設定ファイルのいずれか）
   - Dummy Brain と将来の LLM Brain が**同じ型**であることをどう保証するか

追加で、設計上どちらでもよいと判断した点は**「決めない」と明示する。**

## Constraints

- **`server.aiwolf_core` / `server.network` を import しない**（Design invariant 2 / 10）
- **役職名・チャネルID・死因IDをクライアントへ直書きしない**（Design invariant 4）。
  値は受信内容から取る
- **サーバの可否判定を再実装しない。** 行動の選択肢は受信した列挙をそのまま使う
- **Brain は handle を自作しない。** 送信は Controller が行う
- **推論しない。** Belief / Suspicion / 信頼度 / ライン / 反応スコアは Phase 6
- **LLM を前提にしない。** Dummy Brain だけで完走する。Phase 4 の LLM Brain が
  同じ境界へ入れることは設計で示すが、backend / プロンプトは 3.3 で作らない
- 同じ seed と同じ入力から同じ判断が出る（決定論）。
  module-level の `random` を直接呼ばない
- 9プロセス同時稼働の前提を壊さない（RTX 3070 Ti / 8GB。Design invariant 9）
- `ai_client/network/` と `ai_client/world/` を変更しない。
  必要になったら**設計へ書かず Reviewer へ報告する**
- サーバ・schema・ゲームコア・content を変更しない

## Out of scope

- 発言の間隔・反応の起点・CO の発火点（3.4）
- 投票先と能力対象の選択方針（3.5）
- LLM backend、プロンプト整形、structured output（Phase 4）
- Belief / Suspicion / Strategy / 議論品質の評価（Phase 6）
- 9体同時稼働の LLM queue（Phase 5）
- 完走テストの検証コントラクト（`PHASE3_1_COMPLETION_EVIDENCE_DESIGN.md` の範囲）

## 粒度

**完成コードを書かない。関数内部を1行ずつ指定しない。**
シグネチャは公開 API に限る。内部ヘルパーの列挙は不要。
判断基準は「Implementer が重要な設計判断をせずに書けるか」だけである。

## この設計が承認されない条件

- 上の4点のいずれかが未決のまま残っている
- Brain が `current_actions()` の列挙に無い行動を作れる形になっている
- Brain の遅延が World State の取り込みまたは Network Client の受信を詰まらせる
- Phase 4 の LLM Brain が同じ境界へ入れることが示されていない
- 3.4 / 3.5 / Phase 6 を先取りしている
- 役職名・チャネルID・死因IDが client 側の literal になっている
- `ai_client/network/` または `ai_client/world/` の変更を前提にしている
- 関数内部まで書いてある（コードの二重管理）
