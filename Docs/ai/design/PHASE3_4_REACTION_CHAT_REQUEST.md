# Phase 3.4 Reaction・Chat Controller — DESIGN REQUEST

Issued by: Reviewer / Claude（2026-09-05、D051）
For: Detailed Design / Sol
Status: REQUESTED — 成果物 `PHASE3_4_REACTION_CHAT_DESIGN.md` が
Reviewer の `DESIGN REVIEW: APPROVED` を得るまで実装へ渡らない（RUNBOOK §4.3）。
**承認は Claude が付ける**（D053。設計を書いた model と別の model が承認する）。

Request status: CLOSED

**2026-09-05 改訂（Reviewer / Claude）。** 初版は Constraints と Q2 / Q6 が両立せず、
Sol が `DESIGN BLOCKED` で正しく止めた（R-20260905-07）。**原因は Reviewer にある。**
`D055` でユーザーが方針を決めたので、Constraints を改訂し Q8 を追加した。
`PHASE3_4_REACTION_CHAT_DESIGN.md` は `DRAFT` のまま引き継いでよい。
**BLOCKED の指摘そのものは正しかったので、書き直しではなく続きである。**

```
DESIGN: REQUIRED
```

## Reason

判定リストの複数項目に当たる。

- **新しい state machine / lifecycle。** 「いつ喋るか」は phase 内で時間とともに変わる状態である
- **async・concurrency。** 9席が同じ2秒フェーズで同時に喋る
- **timeout。** 締切前の打ち切りを決める
- **実装方法が複数あり、選択を誤ると手戻りが大きい**
- **canonical design が目的だけを定め実装構造を定めていない。**
  ROADMAP §3.4 は「発話間隔の決め方」「反応の起点」「打ち切り位置」を
  詳細設計で決めると明記している

加えて、**承認済み Phase 3.3 設計と正面から衝突する点が1つある。** 下の Q1 に書く。
これを実装者の局所判断に任せると、R-94〜R-123 で4回繰り返した漂流を再現する。

## Design scope

`ROADMAP.md` §3.4 の完了条件5項目それぞれについて、
**どの構造がその条件を満たし、どの観測がその証拠になるかを決める。**
成果物の節は `RUNBOOK.md` §4.2 に従う。

現在の対象物:

| 何 | どこ |
|---|---|
| 完了条件（**canonical**） | `Docs/ai/ROADMAP.md` §3.4 |
| Brain 境界（**承認済み**） | `design/PHASE3_3_BRAIN_INTERFACE_DESIGN.md` |
| Brain 実装 | `ai_client/brain/`（`controller.py` / `coordinator.py`） |
| World 読み取り API | `ai_client/world/`、`design/PHASE3_2_WORLD_STATE_MEMORY_DESIGN.md` |
| 送信 API と締切 | `ai_client/network/client.py`、`design/PHASE3_1_NETWORK_CLIENT_DESIGN.md` |
| 拒否理由の語彙（**実コード**） | `server/aiwolf_core/rejections.py` |
| 完走テストの共有部品 | `tests/fixtures/completion_process.py`、`tests/fixtures/completion_evidence.py` |
| テスト方針 | `Docs/ai/TEST_POLICY.md` |

## Questions Sol must resolve

**それぞれ、採らなかった案と、採らなかった理由を1〜2行で書くこと。**

1. **Phase 3.3 の invocation policy との衝突をどう解くか。これが本件の核である。**
   承認済み 3.3 設計は「`PhaseKey(day, phase)` ごとに Brain 呼び出しは高々一回」
   「一クライアントにつき active invocation は最大一個。queue は設けない」と決めている。
   3.4 は同じ昼フェーズで複数回喋ることを要求する。両立しない。
   - `PhaseBrainCoordinator` を置き換えるのか、その外側に別の呼び出し主体を置くのか、
     3.3 設計の invocation policy を改訂するのか
   - **改訂するなら、3.3 の Acceptance 9 とその検証テストがどうなるかを明記する。**
     承認済み設計を黙って読み替えない
   - `BrainController` の「active invocation 最大一個」は維持するのか。維持する場合、
     喋る機会が重なったときに捨てるのはどちらか

2. **締切余裕を誰がどう測るか。**
   3.3 設計は「`phase_ends_at` を local clock と直接比較しない。送信余裕は 3.4 / 3.5 へ委ねる」
   と明示的に判断を先送りしている。**その宿題がここに来る。**
   - `phase_ends_at`（server timestamp）と local monotonic の対応をどこで取るか。
     Phase 3.1 の `PhaseDeadlineReached` を使うのか、別の導出を置くのか
   - `monotonic_seconds()` は**整数秒**である。2秒フェーズで整数秒の分解能しかない前提で、
     打ち切り位置をどう表現するか
   - 打ち切った席は「喋らなかった」のか「喋れなかった」のか、テストから区別できるか
     （Phase 3.1 の R-123 で同じ取り違えをした）

3. **反応の起点をどのイベントから取るか。固定順を作らない保証は何か。**
   Design invariant 3「Realtime free chat. No fixed speaking order.」が canonical である。
   - World の何を見て「他人が喋った」と判定するか。`HistoryView` か、別の通知か
   - 起点が決定論的だと、9席が同じ入力から同じ順で喋る。
     **どこに非決定性を入れ、それをどう再現可能にするか（seed の所在）**
   - 「偏らない」を**どう観測するか。** 完了条件は「観測できる」であって
     「偏らない」ではない。何を測れば偏りの有無が言えるか。下限・上限を置くか

4. **間隔と上限の単位。**
   - 発話間隔をフェーズ内の経過で決めるか、直前の発話からの経過で決めるか
   - 上限はフェーズごとか日ごとか席ごとか。**生存者数から導出するか固定値か**
   - 1人が黙っても他が進むことを、どの構造が保証するか

5. **CO 宣言の起点。**
   - chat と同じ経路に乗せるか、別の起点にするか
   - 3.3 の `CoDeclareDecision` / `CoReportDecision` はそのまま使えるか

6. **`action.rejected` を握り潰さない構造。**
   - 拒否を受けたとき、その席は再送するのか、諦めるのか、次の機会へ回すのか
   - **拒否理由ごとに扱いを変えるか。** Phase 3.1 の
     `tests/fixtures/completion_evidence.py` にある
     `BOUNDARY_REJECTION_REASONS` / `DEFECT_REJECTION_REASONS` の分類を再利用するか、
     3.4 用に別の分類を置くか。**再利用しないなら理由を書く**
   - 再送するなら、それが 2 の締切打ち切りと矛盾しないこと

7. **完走テストの形。**
   - `tests/fixtures/completion_process.py` の gated clock / `finish_process` /
     stdout-stderr 収集をそのまま使うか
   - 「9人が同時に喋っても締切内に収まる」の**証拠は server 側の何か。**
     Phase 3.1 の「expected はクライアント証拠からではなく server が提示した機会から作る」
     という原則をここでも守ること
   - Reviewer 環境は `device_bash` に約120秒の上限がある。1ファイルの実行時間をその中に収める

8. **`D055` が許した公開契約の拡張そのものを設計する。**
   Q2 と Q6 はこの拡張の上に乗る。**先にこれを決める。**
   - World が保持する transport observation の value 型。
     `action.rejected` は何を保持するか（action / reason / seq のほかに必要なものはあるか）
   - server deadline と local clock の対応を、**どの typed value として Network から World へ渡すか。**
     `monotonic_seconds()` は整数秒である。上位層が「あと何秒か」を導ける形にすること
   - **retention。** 履歴と同じく上限と欠落の表現が要る。
     `WorldSnapshot.complete` / `HistoryRetention` に合わせるか、別に置くか
   - **version 契約。** `WorldSnapshot` は「同じ version なら同じ内容」である。
     新しい情報源を足してもこれを壊さないことを示す
   - **再接続時の置換規則。** `game.state_sync` による再基準化で、
     保持していた observation はどうなるか
   - **既存への影響。** `PHASE3_1_NETWORK_CLIENT_DESIGN.md` /
     `PHASE3_2_WORLD_STATE_MEMORY_DESIGN.md` の Public Interfaces のどこを直すか、
     既存の単体テストと完走テストのどれが影響を受けるかを列挙する
   - Phase 3.5 の締切判断も同じ API を使う。**3.4 だけの都合で形を決めない**

追加で、設計上どちらでもよいと判断した点は**「決めない」と明示する。**

## Constraints

- **`ROADMAP.md` §3.4 を書き換えない**
- **`server/` / `protocol/` / ゲームコア / content を変更しない**
- **`ai_client/network/` と `ai_client/world/` の変更は、`D055` が許した範囲に限る。**
  すなわち transport observation（`action.rejected` の観測と、
  server deadline / local clock の対応）を World の読み取り API へ載せるために
  必要な最小限である。それ以外の理由で両層を変えない
- **World は `NetworkClient.events()` の唯一かつ exclusive な consumer のままである。**
  event fan-out 境界を作らない。Controller が `events()` を読む設計にしない
- **Network に自動再送・自動リトライを入れない**（Phase 3.1 の契約）
- `ai_client/brain/` の変更は、Q1 の答えが要求する範囲に限る。
  **`Brain` protocol（`decide(BrainInput) -> BrainDecision`）は変えない**
- テストがサーバの可否判定を再実装しない。
  行動選択肢は受信した handle の列挙からのみ導く
- 役職名・チャネルID・死因IDをクライアントへ直書きしない
- LLM 無しで完走する。発話内容は Phase 4 の範囲であり、
  3.4 では固定文字列や Dummy の出力でよい
- ローカルLLMを前提にした設計にしない

## Out of scope

- 発言内容の生成、プロンプト、structured output（Phase 4）
- 投票・能力の選択方針（3.5）
- Belief / Suspicion / 信頼度（Phase 6）
- Phase 3.1 / 3.2 / 3.3 の完了証拠の作り直し
- CI の導入・実行環境の変更

## 粒度

**完成コードを書かない。関数内部を1行ずつ指定しない。**
決めるのは「いつ喋るか」を決める構造と、その正しさをどう観測するかであって、
`assertEqual` の並べ方ではない。
判断基準は「Implementer が重要な設計判断をせずに書けるか」だけである。

## この設計が承認されない条件

- **Q1 に答えていない。** 承認済み 3.3 設計の invocation policy との関係が書かれていない、
  または「改訂する」と書きながら 3.3 の Acceptance と既存テストへの影響を書いていない
- 締切余裕の測り方が `phase_ends_at` と local clock の直接比較になっている
- 発話順が固定になる構造、または非決定性の再現手段（seed）が無い
- 「偏らない」の観測方法が決まっていない
- `action.rejected` を受けたときの扱いが決まっていない、または握り潰す形になっている
- `ai_client/network/` または `ai_client/world/` の変更が、
  `D055` が許した transport observation の範囲を超えている
- World 以外が `NetworkClient.events()` を読む形になっている
- 公開契約を拡張しながら、retention / version 契約 / 再接続時の置換規則を決めていない
- 既存の Phase 3.1 / 3.2 設計書とテストへの影響を列挙していない
- `Brain` protocol を変更している
- 「1件でも通る」assert で完了条件を満たしたことにしている
- 関数内部まで書いてある（コードの二重管理）
