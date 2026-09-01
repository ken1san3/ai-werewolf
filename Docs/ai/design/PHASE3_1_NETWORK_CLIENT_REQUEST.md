# Phase 3.1 Network Client — DESIGN REQUEST

Issued by: Reviewer / Design Gate（2026-09-01、D051）
For: Detailed Design
Status: REQUESTED — 成果物 `PHASE3_1_NETWORK_CLIENT_DESIGN.md` が
Reviewer の `DESIGN REVIEW: APPROVED` を得るまで実装へ渡らない（RUNBOOK §4.3）。

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
