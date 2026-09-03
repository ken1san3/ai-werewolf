# Phase 3.1 Network Client — DESIGN REQUEST

Issued by: Reviewer / Design Gate（2026-09-01、D051）
For: Detailed Design
Status: REQUESTED — 成果物 `PHASE3_1_NETWORK_CLIENT_DESIGN.md` が
Reviewer の `DESIGN REVIEW: APPROVED` を得るまで実装へ渡らない（RUNBOOK §4.3）。
Request status: OPEN

```
DESIGN: REQUIRED
```

## Reason

`server/` 側に前例の無い新しい subsystem（AIクライアントのプロセス基盤）を新設する。
`DESIGN.md` §9 は「サーバが何を送り、何を受け取るか」を定めているが、
**クライアント側の構造・lifecycle・失敗時の振る舞いは一切定めていない。**

D051 の判定条件のうち、次に当たる。

- 新しい module の新設
- 新しい state machine（接続 lifecycle）
- async / concurrency
- timeout / reconnect
- network protocol との複雑な相互作用
- public API の新設（3.2〜3.5 が全部この上に載る）
- 実装方法が複数あり、選択を誤ると手戻りが 3.2〜3.5 へ広がる

`tests/fixtures/network_dummy_client.py` は Phase 2 の境界検証専用であり、
World State も再接続制御も持たない。**設計の出発点にはできるが、到達点ではない。**

## Design scope

`ROADMAP.md` §3.1 の「含む」を、実装可能な構造へ落とす。

1. **Purpose** — このモジュールが引き受ける責務を1段落で
2. **Files / modules** — 新設するパッケージとファイル。`server/` との配置関係
3. **Responsibilities** — 各モジュールが持つもの・持たないもの
4. **Public interfaces** — 3.2〜3.5 が呼ぶ API の形。受信状態の読み出し口と送信口
5. **Data flow** — 受信メッセージが状態へ入り、上位へ出るまで。送信要求の逆向き
6. **State / lifecycle** — 接続の状態遷移図（未接続 / Join / 接続中 / 切断 /
   Resume / 終了）と、各遷移の契機
7. **Main control flow** — 通常運転1周分
8. **Failure handling** — 下の「決めること」5点を含む
9. **Concurrency assumptions** — 何が同時に走り、何が走らないか
10. **Explicitly out of scope**
11. **Acceptance criteria** — `ROADMAP.md` §3.1 の完了条件を満たす形で
12. **Required tests** — LLM 無しで走るもの

## Relevant canonical sources

| 何を見るか | どこ |
|---|---|
| サブPhaseの境界と完了条件（**canonical**） | `Docs/ai/ROADMAP.md` §3.1 |
| 接続・時刻・切断・共通形・送信タイミング・主要メッセージ・CO | `Docs/ai/spec/DESIGN.md` §2 §9.1〜§9.5 |
| 通信の source of truth | `protocol/aiwolf-v1.schema.json` |
| 入室トークンと接続トークンの役割分離 | `decisions/D048_SEAT_ENTRY_TOKEN.md` |
| サーバ側の送信キューと replay 保持上限 | `decisions/D049_NETWORK_SEND_AND_REPLAY_RETENTION.md` |
| 別プロセス完走の既存条件 | `decisions/D050_PHASE2_SEPARATE_PROCESS_COMPLETION.md` |
| Phase 2 が到達した境界と「Do Not Repeat」 | `handoffs/PHASE2_HANDOFF.md` |
| 既存クライアントの実物 | `tests/fixtures/network_dummy_client.py` |
| サーバ側の対向実装 | `server/network/` |

矛盾したときの優先順位: canonical source → 実コード → schema → tests。

## Constraints

- **ゲームコアを import しない。** 可否判定をクライアントへ複製しない。
  送る要求は、受信した `player.action_state` の列挙からのみ組み立てる
- 役職名・チャネルIDをクライアントへ直書きしない（Design invariant 4 / DESIGN §9.5）
- サーバの単調時計が唯一の時刻源。クライアントの時計は表示にのみ使う（§9.1）
- `seq` は取りこぼし検出のためであり、**順序保証の根拠にしない**（§9.2）
- 封筒は strict。未知フィールドを受けない（§9.2）
- 入室トークンは Join で消費される。プロセス再起動には保存済み接続トークンが要る
- 切断してもゲームは進む。席は残る。未選択の能力は `no_selection` に従う（§9.1）
- LLM が応答しないとき代替発言を送らない（黙る）（§9.1）
- LLM 無しでテストが完走すること（Design invariant 10）
- RTX 3070 Ti / 8GB。9プロセスが同時に動く前提を壊さない（Design invariant 9）

## Out of scope

- World State の構造化と記憶（3.2）
- Brain interface と Dummy Brain（3.3）
- 発言内容の生成と反応制御（3.4）
- 投票・能力の選択方針（3.5）
- LLM backend、structured output（Phase 4）
- 再接続 UI、観戦、リプレイ再生（将来候補）
- サーバ側の変更。必要になったら**設計へ書かず Reviewer へ報告する**

## Questions Sol must resolve

`ROADMAP.md` §3.1 が「詳細設計で決定する事項」として明示的に委ねた5点。
**それぞれ、採らなかった案と、採らなかった理由を1〜2行で書くこと。**

1. **`seq` 欠番時の回復方式。** 検出したあと何をするか。
   `game.state_sync` を要求するのか、接続を張り直すのか、他か。
   サーバの replay 保持には上限がある（D049）。上限を越えていた場合はどうなるか
2. **接続トークンの保存責務。** クライアント本体が永続化するのか、起動側が持つのか。
   D050 では起動側が持っていた。本番クライアントで同じにするなら、その理由
3. **receive / send の asyncio 構造。** 単一タスクか、受信と送信を分けるか。
   上位（Brain）が遅いとき受信が詰まらないことを、どう保証するか
4. **`action.rejected` の責務境界。** クライアント内で吸収して再試行するのか、
   上位へ上げるのか、両方か。`reason` の粒度は「AIが理解できる」ことになっている（§9.4）
5. **reconnect backoff と終了条件。** 間隔、上限、諦めたときのプロセスの終わり方。
   「黙る」（§9.1）と「終了する」の境界

追加で、設計上どちらでもよいと判断した点は
**「決めない」と明示する。** Implementer に暗黙の判断を残さないこと。

## 粒度

**完成コードを書かない。関数内部を1行ずつ指定しない。**
判断基準は「Implementer が重要な設計判断をせずに書けるか」だけであり、
それ以上細かいものはコードの二重管理になる。
シグネチャは公開 API に限る。内部ヘルパーの列挙は不要。

## この設計が承認されない条件

- canonical design / accepted decision / protocol・schema と矛盾している
- 上の5点のいずれかが未決のまま残っている
- 責務分離、state / lifecycle、failure handling、concurrency 前提のいずれかが曖昧
- 3.2〜3.5 や Phase 4 を先取りしている
- 関数内部まで書いてある（コードの二重管理）


---

## Addendum B — Resume の配送契約と、終了をまたぐ復旧（2026-09-03、R-20260903-03 / 05）

Issued by: Reviewer / Claude（Primary Design Gate、D051）
For: Detailed Design / Sol
**この Addendum は本 REQUEST の一部である。** `PHASE3_1_NETWORK_CLIENT_DESIGN.md` は
`DRAFT` へ戻した。新しいファイルを作らず同じ `_DESIGN.md` を改訂し `Status: IN_REVIEW` にする。
改訂後の `APPROVED` は Claude が付ける（D053）。

### 判定

```
DESIGN: REQUIRED
```

**Accepted decision の変更にあたるため、Implementer にも Reviewer にも局所判断の権限が無い。**

### B1. Resume の配送順（R-20260903-03）

**現状は canonical と矛盾している。**

`D047_PLAYER_STATE_DELIVERY.md`（Accepted）:

```
  検証済みの送信イベントをプレイヤー単位で保存し、Resume は `last_seq` より後のものを
  元の envelope・seq のまま再送する。**その後に resume 応答、新しい全量 sync を送る。**
```

`handoffs/PHASE3_1_HANDOFF.md`:

```
  Resume replay が ACK より先に届く経路を維持し、ACK の欠落・不一致・重複・payload 不整合を
  `INVALID_SERVER_MESSAGE` として拒否する。
```

canonical は **replay → ACK → sync** である。ところが commit `f8c6668` で
`server/network/server.py` は **ACK → replay → sync** へ変え、
`ai_client/network/client.py` には `_resume_ack_sequence` の2段判定が入り、
`tests/test_state_delivery.py` と `tests/test_network_review_regressions.py` は
wire `[4, 3, 5]` を明示的に固定するよう書き換えられた。

**この配送順の変更を指示したのは Reviewer / Claude である（R-20260903-01 の Required 1）。
D047 を確認せずに書いた。判断を誤った。** Luna は指示どおりに実装している。

決めること。**採らなかった案と理由を1〜2行で書くこと。**

- **A案（既定）: canonical へ戻す。** サーバの enqueue 順を replay → ACK → sync に戻し、
  **クライアント側だけで直す。** 認証前に届いた replay を上位 consumer へ公開せず、
  ACK を受理して検証したあとに順序どおり適用・公開する。
  `_resume_ack_sequence` の特例と wire の seq 非単調は不要になる
- **B案: D047 を supersede する。** 新しい decision（`decisions/` の次番）を書き、
  ACK 先行に変える理由、`seq` の採番順と配送順が別物であること、
  欠番検出への影響を明記する。`DESIGN.md` §9.2 と handoff も同時に直す

**A案を既定とする。** B案を採るなら、A案では解決できない理由を示すこと。
どちらでも、決着後に `DESIGN.md` §9.2 の「単調増加」の意味を明確にすること。

### B2. 終了をまたぐ復旧（R-20260903-05）

`ai_client/network/client.py:672` は top-level の `game.event` が `GAME_ENDED` の
ときだけ `_GameEndedSignal` を上げる。`ai_client/world/service.py:207` も同じである。
Reviewer が実コードで確認した。結果、Sol が実サーバで再現した2つが起きる。

1. 終了が retained replay に含まれると、**そのあとに来る全量 sync を読む前に終了する。**
   新プロセスの world は `self=None` / `phase=night` のまま。
   D047 の「sync を現在地として既存状態を置換」に到達しない
2. 切断中に終了した場合、`GAME_ENDED` は全量 sync の `history` の中にしか現れない。
   どちらの層もそれを終了と解釈せず、**CONNECTED / CURRENT のまま待ち続ける**

決めること。**採らなかった案と理由を1〜2行で書くこと。**

- 全量 sync の commit と終了通知の**順序と所有者**。
  「baseline を確定してから終了する」を誰が保証するか
- replay 中の `GAME_ENDED` を受けたとき、後続の ACK / sync をどこまで読んでから終了するか。
  読み切る前に socket が閉じられた場合はどうするか
- sync の `history` に終了がある場合の終了判定を、どちらの層の責務にするか。
  **公開権限は変えない**（`history` は既に本人へ許可された内容である）
- 有限時間で終了することの保証。待ち続けないための上限をどこに置くか
- `WorldState` の `Freshness.ENDED` と `NetworkClient` の `ClientExitReason.GAME_ENDED` の関係

### Out of scope

- protocol schema・ゲームコア・content の変更
- 公開権限の変更（誰に何を送るか）
- Phase 3.1 完走テストの検証コントラクト（R-20260903-02 / 06。別の設計で扱う）
- `WorldState` の recovery sync 拒否後の lifecycle（R-20260903-04。承認済み Addendum A の
  範囲内なので Implementer が直す）

### この設計が承認されない条件

- A案 / B案のどちらを採るかが決まっていない
- B案なのに新しい decision が無い、または D047 を supersede すると書いていない
- B2 の「誰が終了を宣言するか」が層をまたいで曖昧なまま
- sync-only 経路が有限時間で終了する保証が無い
- 公開権限を変えている
