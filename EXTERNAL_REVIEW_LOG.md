# 外部独立レビュー記録

実施: 外部独立レビュー session（Claude Opus 5）
性質: **非 canonical の外部レビュー入力。** `CURRENT_STATE.md` / `TASKS.md` / 承認済み design /
packet / handoff のいずれもこの文書によって上書きされない。D068 の定めるとおり
「有用な入力であって canonical authority ではない」。採否は Main Integrator とユーザーに属する。

**本ファイルは追記型。以後の外部レビューはすべてここに追記する。** 過去の記載は改変しない。

## 索引

| # | 日付 | 対象 | 判定 |
|---|---|---|---|
| [R01](#r01--harness--phase-6-構造レビュー) | 2026-09-14 | Astra 向け harness と Phase 6 継続運用の全体構造 | CHANGES REQUIRED |
| [R02](#r02--完了見積もり19–23-時の検証) | 2026-09-14 | 「本日 19〜23 時完了」見積もりの妥当性 | 前提が成立しない |
| [R03](#r03--phase-6-master-test-plan-freeze-候補) | 2026-09-14 | Phase 6 Master Test Plan `P6-PLAN-20260914-R0` | CHANGES REQUIRED（F1/F2 必須） |
| [R04](#r04--master-test-plan-r1--t296-実装の再レビュー) | 2026-09-14 | Master Test Plan `R1` + T296 実装 + R03 disposition | CHANGES REQUIRED（G1 のみ。F1–F7 は全て解消） |
| [R05](#r05--t297-計測と-master-runt299--t300の実行内容確認) | 2026-09-14 | T297 計測 + Master Run T299 / T300 の実行内容 | 実行は健全。報告と実測到達点に差（H1） |
| [R06](#r06--4-件の-fail-の内容特定) | 2026-09-14 | Stage A の 4 FAIL の内容特定 | 全件 test 期待値の drift。製品の誤動作なし |
| [R07](#r07--t301-修正--t302-レビュー--t303-master-run-の実行内容確認) | 2026-09-14 | T301 修正 / T302 レビュー / T303 Master Run | 実行は健全。**Stage A 完了**。残るは Stage B のみ |
| [R08](#r08--テスト内容のレビュー何を実際に検証しているか) | 2026-09-14 | テスト内容（890 ID が何を検証しているか） | 内容の質は高い。指摘 6 件はいずれも注記で足りる |
| [R09](#r09--stage-b-失敗の根本原因特定) | 2026-09-15 | Stage B（実 LLM 一回実行）の失敗 | **根本原因特定**: read_timeout 3.5s に対し 512 token |
| [R10](#r10--2-回目-stage-bt316のテスト結果レビュー) | 2026-09-15 | 2 回目 Stage B（T316）の結果 | R09 修正は有効。今回は製品の失敗。原因を 3 つに分離 |
| [R11](#r11--stage-b-テストケース内容のレビュー) | 2026-09-15 | Stage B テストケース内容（B01–B11）+ R10 修正の test 変化 | oracle 設計は良質。指摘は N1 一点 |
| [R12](#r12--3-回目-stage-bt330のテスト結果レビュー) | 2026-09-15 | 3 回目 Stage B（T330）の結果 | R10 修正は有効。全 107 call が HTTP 400、原因は provider 側が最有力 |
| [R13](#r13--p1p2-診断保全実装t332t336のレビュー) | 2026-09-15 | P1/P2 診断保全の設計・実装・独立検証（T332–T336） | 実装は良質。全体回帰 FAIL は差分と無関係と実測確定。P2 は未稼働 |
| [R14](#r14--r13-指摘に対する修正t337t340のレビュー) | 2026-09-15 | R13 指摘に対する test 修正・計画・独立検証（T337–T340） | **APPROVE**。固定 6 module が初めて全緑。mutation test で oracle も確認。残る gate は Q1 一点 |
| [R15](#r15--provider-実体c-aiagentの確認と-r12-仮説の撤回) | 2026-09-15 | `C:\AIagent` の provider 実体と実 command line | **R12 の「provider 構成差」仮説を撤回**。T316 と T330 の構成は同一。次の一手は provider への 1 要求 |
| [R16](#r16--根本原因特定と-sampler-修正t344t355--d074のレビュー) | 2026-09-15 | 根本原因特定と sampler 修正（T344–T355 / D074） | **APPROVE**。因果連鎖を exact source で独立確認。P1 が初回で効いた。残る問題は T316 の 34 件成功が未説明（U1） |
| [R17](#r17--r16-採否と修正後-stage-b-準備t356t361のレビュー) | 2026-09-15 | R16 採否と修正後 Stage B 準備（T356–T361） | 採否と preflight は良質。Q1 は閉じた。**probe に control が無く、PASS しても因果を確定できない（V1）** |
| [R18](#r18--修正版-stage-br7--t362t369の結果レビュー) | 2026-09-16 | 修正版 Stage B（R7 / T362–T369）の結果 | **HTTP 400 は解消**。B08 は単位取り違えの偽陽性（W1）。schema 適合率は 400 除去後も不変で F-A が本丸と確定（W2） |
| [R19](#r19--r18-w2-の切り分け実施f-a-の正体) | 2026-09-16 | R18-W2 の切り分け実施（保存原本、HTTP 0 回） | **送った schema の違反は 22 件中 0**。grammar は効いている。失敗の 68% は送っていない制約による棄却（X1） |

---

# R01 — harness / Phase 6 構造レビュー

日付: 2026-09-14
対象: Main Integrator authority model、責務分離、stale state / duplicate authority、
安全機構の簡略化、scaffolding、Tester/Reviewer independence、BLOCKED / Human Gate、
evidence retention、platform boundary、token 計測、停止・再開設計、product/test semantics
判定: **CHANGES REQUIRED**

全文（HIGH 5 / MEDIUM 7 / LOW 4、観点別総括、検証範囲）は
**`EXTERNAL_REVIEW_2026-09-14_PHASE6_HARNESS.md`** に保存済み。ここには要約のみ置く。

| ID | 指摘 | 深刻度 | 現況 |
|---|---|---|---|
| H1 | Phase 6 全成果が未 commit・バックアップなしの単一障害点 | HIGH | **解消**（R03 時点。D:\AIwolf_backup\20260914T041928Z_full） |
| H2 | `.phase4-real-*` の実 LLM transcript が untracked かつ gitignore 外 | HIGH | 未解消（backup 済みだが commit 経路は開いたまま） |
| H3 | 実原本と合成 test 入力が同一 prefix で混在し provenance 不能 | HIGH | 対処方針確定（game/ と synthetic/ の path 分離） |
| H4 | CI が Phase 6 を実行不能。未検証の弱い POSIX 分岐が残存 | HIGH | 一部着手（`windows_private` marker 追加）。POSIX 分岐は未決 |
| H5 | `AGENTS.md` が 7997/8000 文字で残り 3 文字 | HIGH | 未解消 |
| M1 | packet 先行作成により board に無い orphan task が発生 | MEDIUM | 対処済み（`OPERATIONS.md` に packet/board 同時作成を追記） |
| M2 | `Review required` が機械検証されず、履歴圧縮で gate 痕跡が消える | MEDIUM | 対処済み（`OPERATIONS.md` に照合義務を追記） |
| M3 | Main が worker 責務を吸収し、独立性が容量制約で削られている | MEDIUM | 対処済み（`AGENTS.md` に「代替せず BLOCKED」を追記） |
| M4 | helper が expected visibility を導出。private CHAT の母数が存在しない | MEDIUM | 計画反映済み（A13 / A20） |
| M5 | 「このまま続行」の解釈が D071 の明示境界を越えて J まで拡大 | MEDIUM | 対処済み（`OPERATIONS.md` に P6-I の明示承認要件を追記） |
| M6 | D068 の 3 ラウンド規則が objective 細分化で回避されている | MEDIUM | 対処済み（`AGENTS.md` に task 名変更で回数リセットしない旨を追記） |
| M7 | 512 件上限が未証明。超過時は J ではなく実ゲーム本体が FAIL する | MEDIUM | 計画反映済み（A19 / B11）。T289 が単位不一致を確認 |

### R01 の訂正

R01 の時点で「24 witness の tokenizer 実測は 7 タスクで 0 回成功」と述べたが、
**直後に T281 → T283 が成功しており、この指摘は無効**である。
T283 は 2026-09-14T03:23:37 UTC に 24/24 の content token を実測した（下表）。

| witness | compact | indented |
| --- | ---: | ---: |
| baseline none | 78 | 144 |
| peer ANSWER | 157 | 300 |
| OPINION_CHANGE | 186 | 331 |
| REBUTTAL | 208 | 372 |
| strategy + assessment | 232 | 406 |
| nine-seat pre-vote | 368 | 510 |

当時の whole-response 上限は 96 token。**最小応答 78 すら余裕がなく、Phase 6 の中核機能は
すべて length-finish で拒否される設定だった。** これが R01 で指摘した
「測定対象の妥当性より測定精度の最適化を先に進めた」ことの実害である。

---

# R02 — 完了見積もり（19〜23 時）の検証

日付: 2026-09-14（13:00 時点の観測）
対象: 「本日 19〜23 時完了、残り 6〜10 時間」という見積もりの確度
判定: **確度が低いのではなく、前提が成立していない**

### 当時の停止状態

`HEAD = 71607b1`（2 日前）、変更 348 件、バックアップ未実施。
`CURRENT_STATE.md:73`「別媒体 backup の存在/対象は未確認、**ユーザーへ保存先確認中**」。
T290 の Dependencies が「backup snapshot 取得後」。**critical path はユーザー待ちで停止していた。**

### 見積もりに入っていなかった硬いゲート

1. **`OPERATIONS.md` が新規 acceptance raw の生成を保留していた**
   （T288 の設計・独立承認・実装まで）。実モデル実行の手前に 5 ゲートが新規挿入されていた。
2. **独立 session が確保できず 5 タスクが BLOCKED**（T272 / T273 / T290 / T284 / T285）。
   `AGENTS.md` の「新規独立 session を確保できなければ BLOCKED」が正しく働いた結果だが、
   host thread limit がそのまま critical path の停止要因になっていた。時間では解消しない。
3. **token 予算が CHANGES_REQUIRED**（T286「数値 B の結論は UNKNOWN で妥当」）。
4. **512 件境界が未証明**。T289 が `run_phase5_local_smoke.py:2075-2076` で 513 件到達時に
   例外となり manifest publish に進まず machine semantic PASS が false になることを確認。
   **J で気づくのではなく一回限りの実ゲーム本体が落ちる。**

### 実測ベースの所要時間

| 項目 | 実測 | Phase 6 での見込み |
|---|---|---|
| 実 exact-9B ゲーム（T154, Phase 5） | **450.5 秒**、60 秒 day、provider 呼出 128 回、accepted chat 48、offer queue 最大待ち **15.6 秒**、出力 14〜18 文字 | 180 秒 day（3×）+ responsive chat + pre-vote 再評価 + semantic JSON（出力 20〜40×）→ 20〜40 分 |
| fixture 完走（T261, モデル無し） | 818.3 秒 | 参考値 |
| accepted text 件数（T264, fixture） | 67 件 | 180 秒 day では増加。512 への余裕は未証明 |

### 過去 15 時間の実績

| 期間 | 実働 | 成果 |
|---|---|---|
| 09-13 21:00 → 09-14 02:45（約 5.75h） | retention 危機の収拾 + P6-F closure | subphase 1 個 closed |
| 09-14 10:12 → 13:00（約 2.8h） | 14 タスク実行 | G 実装・試験済みだが**未承認**、外部監査で 8 タスク追加 |

**実働 約 8.5 時間で closed した subphase は F の 1 個だけ。**
直近 30 タスクのうち使える結果を返さなかったものが 9 件（約 30%）
（T255 / T257 / T265 / T270 / T274 / T277 が CANCELLED、T252 が DONE/FAIL、T262 / T286 が CHANGES_REQUIRED）。

残りは G 承認 + H + I + J + 監査対応 5 ゲート + 予算鎖で、**I と J は一度も実行されたことがない**。
1 subphase あたり実績 3.5〜10 時間・成功率 7 割を外挿すると **14〜40 時間**。

### 結論

19〜23 時が成立するには、次の 7 つが同時に成立する必要があった。

- 今すぐバックアップ先が確定して T290 の鎖が動く
- 監査対応 5 ゲート連続でノーミス
- 独立 session が 10〜13 本、直列で確保できる
- tokenizer 実測が成功し数値選択が独立承認される
- H（製品 6,124 行 + harness 8,139 行）が一発 APPROVED
- 実ゲームが 512 件に達せず、queue 待ちが deadline に収まり、responsive chat が 1 件以上成立
- J が全 accepted text を一発 PASS

**推奨は descope**。今日は「G 承認 + H 承認」まで、あるいは「バックアップ + 監査対応 + G 承認」まで。
P6-I は一回限り・再実行不可・ユーザー承認必須であり、深夜に疲労下で踏むゲートではない。

---

# R03 — Phase 6 Master Test Plan（FREEZE 候補）

日付: 2026-09-14
対象: `Docs/ai/PHASE6_MASTER_TEST_PLAN.md`（Plan ID `P6-PLAN-20260914-R0`）、
`Docs/ai/PHASE6_MASTER_TEST_CATALOG.csv`（884 行）、
`Docs/ai/decisions/D073_FROZEN_MASTER_TEST_PLAN_OPERATION.md`、
`Docs/ai/handoffs/tasks/T293_PHASE6_REBASELINE_IMPLEMENTATION.md`、現行 source
判定: **CHANGES REQUIRED（F1 / F2 が FREEZE の必須前提）**

方式変更そのもの（テストケースを逐次作成せず一度に全部作成し FREEZE する）は**正しい**。
計画の記述品質も高い。判定を CHANGES REQUIRED にしたのは、FREEZE の前提が現時点で
未成立だからであって、方式や計画の書き方の問題ではない。

## R03-1. 検証できたこと（計画の自認リスクを 1 つ解消）

計画は「pytest collection は未実行。計画レビュー時に対象が一致しない場合、FREEZE 前に
一覧を一括確定する」と留保していた。実測した。

```
pytest --collect-only          : 1,207 items
  → parametrize を畳んだ関数単位 :   905
catalog (CSV)                  :   884 nodes
  catalog にあって収集されない  :     0
  収集されて catalog に無い      :    21
```

21 件はすべて旧 Phase の completion（Phase 2 / 3.1 / 3.2 / 3.3 / 3.4 / 3.5 / 4 / 5）で、
計画が「旧Phaseの重いcompletion21定義は対象外」と明記した集合と完全一致する。

**884 + 21 = 905。算術が閉じている。catalog に幽霊 node は 0 件。**

marker の分割も健全だった。

```
-m completion       :    22  (旧 21 + A12)
-m "not completion" : 1,185
                      -----
                      1,207  ← 重複なし・和が全体
```

**この数字を FREEZE の根拠に使ってよい。** 計画が留保していた最大の不確実性はこれで消える。

group 別内訳: A01=19 / A02=43 / A03=27 / A04=48 / A05=11 / A06=11 / A07=32 / A08=8 /
A09=18 / A10=38 / A11=21 / A12=1 / A13=24 / A14=4 / A15=579。
classification は deterministic 834 / synthetic 50、real LLM games は全行 0。

## R03-2. 方式そのものの評価

**「一度全部作る + FREEZE」はこの状況では正しい選択である。**

逐次作成方式が壊れていた理由は「テストが足りない」ではなく、
**失敗が新しいテストと新しい監査層を生む経路になっていた**こと。FREEZE はその経路に上限を与える。
D073 の次の 3 行が根本原因への直接の対処になっている。

> FREEZE後にTest ID、調査、追加review、provenance層を担当者判断で増やさない。
> 証拠管理の完全性だけをblockerにしない。
> Reviewerは凍結Test IDへのPASS/FAIL/BLOCKEDを判定し、新しいcritical pathを生成しない。

T272 / T273 / T294 / T295 を CANCELLED にして「レビューのレビュー」の連鎖を切ったのも正しい。
Stage B が「同一の実 LLM 1 ゲームから B01–B11 を評価。項目別のゲームを作らない」となっている点も良い。

一括方式固有のリスクは 3 つあり、うち 2 つは計画が既に手当てしている。

| リスク | 手当て |
|---|---|
| 本物の欠陥を「計画外」として落とす | DF-xxx + YES 候補の限定列挙。妥当 |
| 一括実行で最初の失敗の情報量が多すぎ triage が重い | 全 ID 集計 → 一括 triage → repair 最大 2 cycle。妥当 |
| **計画時の想像で書いた項目が実装と食い違う** | **未手当て（F1）** |

## R03-3. 指摘

| # | 内容 | 深刻度 | FREEZE 前 |
|---|---|---|---|
| F1 | 実装未完了のまま Plan が先行。A16–A20 の node が実在しない | HIGH | **必須** |
| F2 | vote/night は 45、かつ `Literal` 固定で増やせない | HIGH | **必須** |
| F3 | A15 の 579 件を 1 判定単位にすると blocker 判定が曖昧 | MEDIUM | 必須 |
| F4 | 1,185 件が 900 秒に収まるか未検証 | MEDIUM | 必須 |
| F5 | B02 単独 PASS の合格線をユーザーが承認すべき | MEDIUM | 必須 |
| F6 | 実 LLM 2 回上限 vs「成立するまで増やす」の整合確認 | LOW | 望ましい |
| F7 | CSV の blocker 列が展開されていない | LOW | 不要 |

### F1【HIGH】実装が未完了のまま Plan が先行し、D073 が定めた順序と逆転している

D073 の固定順序は **`実装完了 → Master Test Plan 作成・レビュー → FREEZE`**。しかし実際は、

- T293（D072 の 5 修正一括実装）は **CANCELLED / STOPPED / INCOMPLETE**
- 初回 focused が **180 PASS / 19 FAIL / 35 ERROR**、その後 completion 誤起動で強制停止
- source は**途中実装のまま凍結**（handoff が「完成・PASS・統合可能とは判定しない」と明記）
- `git diff --check` が末尾空行 1 件を未修正で報告

**FREEZE 対象のコードが「途中の状態」である。** 884 項目の期待値が今のコードに対して
妥当かを誰も確認していない。19 FAIL / 35 ERROR も未分類のまま。

さらに **A16–A20 は「予定 node 名」であって実在しない。** 計画自身が
「未定義のままFREEZEしない」と書いているが、実装が止まっているため定義できる状態にない。

**最小修正案**: FREEZE の前提条件を §8 に 4 段で明示する。

```
(1) T293 相当の実装を完成させる（新 packet）
(2) A16–A20 の node が実在し collection されることを --collect-only で確認
(3) catalog を再生成し 905 との差分を再照合
(4) ここで初めて FREEZE 宣言
```

現 Plan は「FREEZE 候補」と正しく名乗っているので、これは Plan の欠陥ではなく順序の宣言漏れ。

### F2【HIGH】vote/night は 60 ではなく 45。しかも `Literal` 固定

計画が A17 で「現runnerのPhase6はday180だがvote/night45を継承している。D072原指示の60との
違いを含める」と指摘しているのは正しい。**誤っていたのは外部レビュー側（本 session）が
`PHASE6_REDIRECTION_INSTRUCTION.md` §2.1 に書いた「vote 60 / night 60 据置き」である。**

```python
# scripts/run_phase5_local_smoke.py:101-103
day_seconds:   Literal[60, 180] = 60
vote_seconds:  Literal[45]      = 45
night_seconds: Literal[45]      = 45
# :126
PHASE6_GAME_PLAN = replace(GAME_PLAN, day_seconds=180)
```

`content/presets/standard_9.yaml` は 180/60/60 だが、runner の `GAME_PLAN` が実行経路で勝つ
（:2284-2286）。**実効値は 180/45/45。** 指示文は preset を見て書かれた誤りであり、訂正済み。

これは A17 の「設定比較」だけでは済まない。

- `vote_seconds` / `night_seconds` が `Literal[45]` → **増やすには型変更が必要**
- `day_seconds` も `Literal[60, 180]` → **180 を超えるには型変更が必要**

D072 の「プレイが成立するまで増やす」を満たすには、`ShortChatConfig` と同じ構造
（`Literal` 固定 + `__post_init__` の ValueError）をここでも外す必要がある。
計画が「今はコードを変更しない」としているのは正しいが、**A17 の範囲を「比較」から
「Phase 6 用の可変 plan 値が全経路に伝播することの確認」に確定してから FREEZE すべき**。
でないと「増やしたいのに型が拒否する」ことが Stage B の直前に判明する。

### F3【MEDIUM】A15 が 579 件で 1 group。blocker 判定の粒度が足りない

`P6-A15 / 579` の blocker 列は「条件付き：重大回帰YES、文書/運用便利さのみNO」。
579 件を 1 つの判定単位にすると、1 件 FAIL したときに group 全体の blocker 値が曖昧になる。

CSV は node 単位（884 行）なのでデータ構造は足りている。運用として
**「group 判定 = 子の最悪値」ではなく「FAIL した子ごとに blocker YES/NO を記録する」**と
§2 に明記する。これがないと triage 時に「579 件のうち 1 件落ちた。これは blocker か」を
判断する根拠が Plan 内にない。

### F4【MEDIUM】Stage A の 900 秒上限が未検証

「通常batchは有限900秒を候補上限」とあるが、**1,185 件が 900 秒に収まる実測がない。**
超えた場合は batch 分割が必要で、FREEZE 後の「担当者判断で増やさない」と判断が衝突しうる。

参考値: T278 の G focused 37 件が外側 38.5 秒。`test_phase3_*` の重いものを含むと幅が大きい。
**FREEZE 前に batch 構成と上限を確定し、分割するなら分割単位も凍結対象に含める。**

### F5【MEDIUM】B02 単独 PASS が通る構造

- B02「他人の発言を受けた accepted responsive chat ≥ 1」→ blocker **YES**
- B03/B04/B05（質問応答 / 反論 / 判断更新）→ **3 つ全部 FAIL のときだけ YES**

D072 の完了条件 2 と 3 は別条件なので形式的には整合する。しかし
**「前の発言を受けてはいるが、質問応答でも反論でも意見変更でもない発言」が 1 件あれば
B02 は PASS する。** Phase 6 の目的（議論が成立する）から見るとかなり弱い合格線。

計画自身が §5 末尾で「新指示を根拠に、旧3品質dimensionやpre-vote件数を暗黙の全項目必須へ
戻さない」と書いており、意図的にこの線を引いていることは明らか。判断としては妥当だが、
**FREEZE レビューでユーザーが明示承認すべき一点**（計画も「FREEZEレビューでこのblocker列を
先に承認する」と書いている）。

### F6【LOW】実 LLM 回数がユーザー指示と食い違う可能性

- D073 / Plan: 「実 LLM は 1 回を基準。修正確認に不可欠なら追加 1 回まで。
  **2 回後に blocker が残れば停止**。無条件の調整 5run や項目別 run は予定しない」
- ユーザー指示: 「プレイが成立するまで増やして」

**計画側の設計（2 回上限 + 停止 + 報告）の方が健全**である。無制限の調整ループは元の増殖と
同じ形になるし、外部レビュー側が `PHASE6_REDIRECTION_INSTRUCTION.md` §2.1 に書いた
「調整 5 回」より規律がある。ただし整合はユーザーが決める点。
2 回で成立しなければ止めて報告、でよいか確認する。

### F7【LOW】CSV の blocker 列が全 884 行同一文字列

`Phase completion blocker` が全行 "Master Planのgroup欄を継承"。CSV 単体では blocker で
ソート・集計できない。triage を CSV 上で行うなら group の値を各行へ展開しておくと楽。
運用に支障が出てから直せば十分。

## R03-4. 結論

**F1 と F2 を解消すれば FREEZE してよい計画。** 残りは §2 / §5 / §8 への追記で閉じる。

方式変更そのものは、直近 2 日間で観測した問題への正しい対処である。特に
「証拠管理の完全性だけを blocker にしない」「Reviewer は新しい critical path を生成しない」の
2 行は、R01 で指摘した増殖経路を直接塞いでいる。計画の書き方も、未達・未分類・UNKNOWN を
隠さない従来の規律を保っている。

## R03-5. 検証した内容と検証していない内容

**検証した**: `pytest --collect-only -q -p no:cacheprovider`（全体 / `-m completion` /
`-m "not completion"`）、CSV と collection の集合差分、group 別件数、
`run_phase5_local_smoke.py` の `GAME_PLAN` / `PHASE6_GAME_PLAN` 実値、
`content/presets/standard_9.yaml` の秒数、D073 / Master Plan / T293 handoff / TASKS の読取り。

**検証していない**: pytest の実行（collection のみ）、T293 の 19 FAIL / 35 ERROR の内容、
A16–A20 の実装可否、900 秒上限の実測、実 provider / model / GPU、private raw 原本の内容。

---

# R04 — Master Test Plan R1 / T296 実装の再レビュー

日付: 2026-09-14
対象: `Docs/ai/PHASE6_MASTER_TEST_PLAN.md`（`P6-PLAN-20260914-R1`）、
`PHASE6_MASTER_TEST_CATALOG.csv`（890 行）、`PHASE6_MASTER_TEST_BATCHES.csv`（9 batch）、
`handoffs/EXTERNAL_REVIEW_R03_DISPOSITION.md`、T296 の実装 14 file、D073 追記
判定: **CHANGES REQUIRED（新規 G1 のみ。R03 の F1–F7 は全て解消）**

## R04-1. R03 指摘の解消状況

| # | R03 指摘 | 状態 | 検証 |
|---|---|---|---|
| F1 | 実装未完了のまま Plan が先行、A16–A20 の node が実在しない | **解消** | 予定 9 node すべて実在を確認（下表）。§8 に FREEZE 前提 4 段を明記 |
| F2 | vote/night = 45、型で固定 | **解消**（一部は指摘側の誤り） | `GamePlan` の 3 field が `int` 化、`PHASE6_GAME_PLAN` が 180/60/60 に |
| F3 | A15 の blocker 判定が group 単位で粗い | **解消** | §2 に「group 最悪値で子全件を決めない」「FAIL 子 ID ごとに blocker 記録」 |
| F4 | 1,185 件 900 秒が未検証 | **解消** | 単一 batch 案を撤回。8 通常 batch × 900 秒 + fixture 1 件 1260 秒に固定、batch CSV 化 |
| F5 | B02 単独 PASS が通る | **解消** | 合格式を明記: `B02 == PASS AND (B03 OR B04 OR B05 == PASS)`、BLOCKED 伝播も定義 |
| F6 | 実 LLM 回数がユーザー指示と食い違う | **解消** | 「1 回基準 + 必要なら追加 1 回、事前に有限回数を確定、2 回後に blocker が残れば停止」 |
| F7 | CSV の blocker 列が展開されていない | **解消** | 予定 blocker を各行へ展開（YES 853 / NO 37） |

### F2 についての自己訂正

R03 の F2 で本 session は次のように書いた。

> `ShortChatConfig` と同じ構造（`Literal` 固定 + `__post_init__` の ValueError）をここでも外す必要がある

**この機構の説明は誤りである。** `GamePlan` に `__post_init__` は存在せず、`Literal` は静的注釈
だけで実行時に ValueError を発生させない。Main の disposition が
「GamePlanに__post_init__はなく、Literalが実行時ValueErrorを発生させるという説明は不採用」と
判断したのは正しい。`ShortChatConfig` には実際に `__post_init__` の ValueError があり、
本 session はその 2 つを混同した。

**実効値が 180/45/45 であって D072 の 180/60/60 ではないという指摘自体は正しく、採用された。**
現在は次のとおり修正済みである。

```
# scripts/run_phase5_local_smoke.py
day_seconds: int = 60 / vote_seconds: int = 45 / night_seconds: int = 45   # Phase 5 は不変
PHASE6_GAME_PLAN = replace(GAME_PLAN, day_seconds=180, vote_seconds=60, night_seconds=60)
```

`ai_client/llm/types.py` も `ShortChatConfig`（Phase 5、`Literal` 固定のまま）を保持したうえで
`DiscussionChatConfig`（Phase 6、20/120/200/600 の可変 int）を新設し、
`ChatOutputProfile: TypeAlias = ShortChatConfig | DiscussionChatConfig` で切り替える構造になった。
旧 profile を壊さずに新 profile を足すという R03 の意図どおりの実装である。

## R04-2. 独立に再現した計測

disposition と R1 §1 が報告する数値を、本 session が同じコマンドで独立に実行して照合した。
**全項目が完全一致した。**

| 指標 | Main 報告 | 本 session 実測 | 一致 |
|---|---|---|---|
| collect-only items | 1,227 | **1,227** | ○ |
| 基底 node（parametrize 畳み込み） | 911 | **911** | ○ |
| catalog 行数 / 一意 node | 890 | **890 / 890** | ○ |
| `-m completion` | 22 | **22** | ○ |
| `-m "not completion"` | 1,205 | **1,205** | ○ |
| 22 + 1,205 | 1,227 | **1,227** | ○ |
| catalog 890 + 旧 completion 21 | 911 | **911** | ○ |
| catalog にあって収集されない | 0 | **0** | ○ |
| 収集されて catalog 外 | 21（全て旧 completion） | **21、旧 completion 以外 0** | ○ |
| blocker 予定 | YES 853 / NO 37 | **YES 853 / NO 37** | ○ |

group 別: A01=19 / A02=45 / A03=27 / A04=48 / A05=11 / A06=12 / A07=32 / A08=8 / A09=18 /
A10=38 / A11=23 / A12=1 / A13=24 / A14=5 / A15=579。
R0 比の増分は A02 +2 / A06 +1 / A11 +2 / A14 +1 = **新規 6 基底 node**、884 + 6 = 890 で整合。

**batch CSV**（`PHASE6_MASTER_TEST_BATCHES.csv`）も検証した。

- 9 batch（A-B01〜A-B08 が `not completion` 各 900 秒、A-B09 が `completion` 1260 秒）
- **catalog 47 module = batch 47 module、重複 0、欠落 0**
- `member_base_nodes` 合計 = **890** = catalog 行数
- A-B03 の宣言 87 は、`test_phase6_semantic_completion.py` の catalog 25 行のうち
  completion marker の 1 件が `-m "not completion"` で除外されるため正しい。
  A-B09 は exact node `::test_p6f_nine_client_semantic_completion` を直接指定しており二重起動しない

**A16–A20 の予定 node**（9 件）はすべて実在を確認した。

| 予定 node | 実在 file |
|---|---|
| `test_phase6_discussion_profile_defaults_bounds_and_runtime_types` | `tests/test_phase5_short_chat.py` |
| `test_phase6_discussion_profile_prompt_parser_boundary` | `tests/test_phase6_semantic_output.py` |
| `test_phase6_bootstrap_effective_profile_and_rejects_malformed` | `tests/test_phase5_local_smoke.py` |
| `test_phase6_evidence_path_partition_and_legacy_compatibility` | `tests/test_phase6_evidence_retention.py` |
| `test_phase6_runner_rejects_invalid_game_output_path` | `tests/test_phase5_local_smoke.py` |
| `test_phase6_population_limit_and_summary_stop` | `tests/test_phase6_semantic_completion.py` |
| `test_phase6_zero_pre_vote_is_diagnostic` | `tests/test_phase6_semantic_completion.py` |
| `test_private_review_closed_diagnostic_is_one_line` | `tests/test_phase6_private_review.py` |
| `test_private_review_each_dimension_isolated_fail` | `tests/test_phase6_private_review.py` |

**F1 の前提（実装完成 → collection → catalog/batch 確定）は成立している。**

## R04-3. 新規指摘

| # | 内容 | 深刻度 | FREEZE 前 |
|---|---|---|---|
| G1 | 512 token 予算は 96 byte text の corpus で測った値。新 profile は 600 byte を許す | HIGH | **必須** |
| G2 | 可変と称する上限の余裕が +20% しかない（200 → 240 が天井） | MEDIUM | 望ましい |
| G3 | `max_text_chars` と `max_generated_text_chars` の相互検証がない | MEDIUM | 望ましい |
| G4 | Stage A が 14 file の未実行コードに対する初回実行になる | MEDIUM | 判断のみ |
| G5 | T296 の 4 file を Main 自身が補完実装した | LOW | 判断のみ |

### G1【HIGH】512 の根拠となった計測は 96 byte text で作られている

512 という whole-response 上限は T283 の実測（最大 510）に基づいて選ばれた。
しかしその corpus は T281 が作ったもので、T281 packet は witness を
**「80文字かつ96 UTF-8 bytesのANSWER」** と明記している。**旧 profile の上限で作られた corpus である。**

T283 の実測値（抜粋）:

| witness | compact | indented |
| --- | ---: | ---: |
| ANSWER 96 UTF8 bytes | 215 | 379 |
| nine-seat pre-vote | 368 | **510** |

新 `DiscussionChatConfig` は `max_text_utf8_bytes = 600` を既定にする。**text 部分が 6.25 倍になる。**
日本語 UTF-8 を BPE で概ね 1 token / 1.5〜3 byte とすると、増分 504 byte は
おおよそ **+170〜330 token**。最大 witness（nine-seat pre-vote 510 indented）に単純加算すると
**680〜840 token** となり、**512 を超える可能性が高い。**

R1 §5 は「総ゲームtoken数を512とは扱わない。各request上限512」と書き、disposition も
「token510の代表content計測だけで全応答512以内を保証しない」と留保している。
**留保は正しいが、計測をやり直していない。** 現状は

- 512 を選んだ根拠 = 96 byte text の corpus
- 実際に流す text = 600 byte

という不整合を抱えたまま FREEZE しようとしている。

**failure mode**: Stage B の実 LLM 1 回で、pre-vote 再評価や長い REBUTTAL が
length-finish で拒否され、B02 / B03–B05 / B06 がまとめて FAIL する。
design §11 と D073 により **その 1 回は自動再実行できない**。
B11 は「provider 上限 512、length 拒否」を検査対象にしているが、
**それは失敗を検出するだけで、失敗を防がない。**

**最小修正案（FREEZE 前、オフライン、GPU 不要）**:
T281 の corpus 生成と T283 の counter は既に存在し、T283 の実測は外側 2.475 秒で完了している。
**同じ手順を `max_text_chars=200 / max_text_utf8_bytes=600` の witness で 1 回やり直す。**
結果に応じて次のいずれかを FREEZE 前に確定する。

1. 最大値が 512 以内 → 512 のまま。根拠が新 profile に対して成立したことを記録
2. 超過 → `GenerationSettings` の検証範囲 1–512 を引き上げて上限を再選択（product 決定）
3. 超過し、上限を上げない → `max_text_utf8_bytes` を 512 に収まる値へ下げる（product 決定）

これは Stage A の新規項目 1 件（例: A21）として計画へ入れるか、A16 の入力 matrix へ統合する。
**FREEZE 後は項目を増やせないため、この判断は FREEZE 前にしかできない。**

### G2【MEDIUM】「増やす」余地が +20% しかない

`DiscussionChatConfig.__post_init__` は `max_text_chars` を **1–240**、
`max_text_utf8_bytes` を **1–960** に拘束する。さらに
`LLMBrainConfig.max_generated_text_chars` も **1–240** で拘束される。
Phase 6 既定は 200 chars なので、**天井は 240、余裕は +20%**。

ユーザー指示は「プレイが成立するまで増やす」であり、200 で不足した場合に
240 まで上げてなお不足すれば、**FREEZE 後に禁止されているコード変更が必要**になる。

**最小修正案**: 240 を選んだ根拠を Plan に明記するか、上限を（例えば 512 chars へ）
先に広げておく。既定値 200 は変えなくてよい。上限だけ広げれば FREEZE 後の調整余地が生まれる。
G1 の再計測と同じ packet で判断できる。

### G3【MEDIUM】profile と brain config の相互検証がない

`DiscussionChatConfig.max_text_chars`（≤240）と `LLMBrainConfig.max_generated_text_chars`（≤240）は
それぞれ個別に検証されるが、**両者の関係は検証されない。**
`max_text_chars=200` かつ `max_generated_text_chars=100` という矛盾した組合せが構築できる。

A16 の入力 matrix は「200/201chars、600/601bytes、bool、min>max、各設定の 0/1/上端/上端+1」で、
`DiscussionChatConfig` 内部の `min>max` は覆うが **この cross-field は覆わない。**

**最小修正案**: A16 の matrix に `max_text_chars > max_generated_text_chars` の 1 行を足す。
実装側に検証を足すかは Architect 判断でよい。

### G4【MEDIUM】Stage A が未実行コード 14 file に対する初回実行になる

T296 の実装は `py_compile` / AST / `collect-only` のみで、**assert は 1 件も実行されていない**
（disposition が「AST/組込みcompileは13 Python fileで成功。これはテストassertを実行した証拠ではない」と明記）。
加えて T293 の **19 FAIL / 35 ERROR は一件ずつの分類が未確認のまま保持**されている。

D073 の固定順序（実装完了 → 計画 → FREEZE → 全計画テスト）に忠実であり、
**これは欠陥ではなく設計どおり**である。ただし帰結として、Stage A は
「一度も走っていない実装 14 file に対する 890 node の初回一括実行」になる。
FAIL が大量に出た場合、`repair 最大 2 cycle` の予算が 1 巡目で消える。

**これは指摘というより、FREEZE 時に引き受ける risk の明示を求めるもの。**
Plan §6 に「Stage A 初回の FAIL 件数が一定を超えた場合の扱い」を 1 行決めておくと、
2 cycle を使い切ったときに新しい判断を即席で作らずに済む。
逐次実行へ戻す提案ではない。逐次実行こそが R01 で指摘した増殖の原因だった。

### G5【LOW】T296 の一部を Main 自身が補完実装した

disposition に「所有解放後、Mainは次の予定範囲だけを補完した」とあり、
A16 matrix / A18 base 配置 / A19 の 511・512・513 件 test / closed 診断の path 分類の
**4 file を Main が実装**している。Main 自身が
「Mainの実装・照合は独立Reviewer/Testerを代替しない」と明記しており、
R01-M3 で指摘した「Main が worker 責務を吸収する」パターンの再発ではあるが、
**今回は独立 Reviewer を省略していない**点で M3 とは質が違う。

**最小修正案**: 計画レビューの packet に「Main が補完実装した 4 file を明示的に review 対象に含む」
と 1 行書く。`logs/r03-plan-revision/final-source-hashes.json` と `scoped-code.diff` があるので
対象の特定はできる。

## R04-4. 結論

**R03 の F1–F7 は全て解消されており、R1 の数値はすべて独立に再現できた。**
計画としての品質は高く、batch 固定・blocker 展開・合格式の明示・有限回数の事前確定は、
いずれも R01 で指摘した増殖経路を塞ぐ方向に効いている。
disposition が指摘の一部を根拠付きで **不採用** にしている点（F2 の機構説明）も健全であり、
実際にそちらが正しかった。

**FREEZE 前に必要なのは G1 のみ。** 512 の根拠を新 profile の text 長で測り直し、
その結果で上限を確定する。オフライン・GPU 不要・既存ツールで数秒の作業である。
G2 / G3 は同じ packet で一緒に片付けられる。G4 / G5 は判断の明示だけでよい。

G1 を放置して FREEZE すると、**唯一の実 LLM ゲームが length 拒否で落ちる** 経路が開いたまま残る。
その 1 回は再実行できない。

## R04-5. 検証した内容と検証していない内容

**検証した**: `pytest --collect-only -q -p no:cacheprovider`（全体 / `-m completion` /
`-m "not completion"`）、catalog 890 行との集合差分、group 別件数、blocker 列の展開、
batch CSV の module 集合・重複・`member_base_nodes` 合計、A16–A20 予定 node 9 件の実在、
`GamePlan` / `PHASE6_GAME_PLAN` / `DiscussionChatConfig` / `ShortChatConfig` /
`LLMBrainConfig` の実コード、R1 §1/§2/§3/§5/§6/§8、disposition、D073 追記。

**検証していない**: pytest の実行（collection のみ。assert は 1 件も実行していない）、
T293 の 19 FAIL / 35 ERROR の内容、T296 実装の正しさ、900 秒上限の実測、
G1 の token 再計測（本 session は corpus を生成していない。上記の 680〜840 token は
byte/token 比からの概算であり実測ではない）、実 provider / model / GPU、private raw 原本。

---

# R05 — T297 計測と Master Run（T299 / T300）の実行内容確認

日付: 2026-09-14
対象: `T297_PHASE6_G1_PROFILE_COUNT.md` と `logs/t297-g1/summary.json`、
`T297_G1_INDEPENDENT_REVIEW.md`、`T298_PHASE6_MASTER_PLAN_REVIEW.md`、
`T299_PHASE6_FROZEN_MASTER_RUN.md` / `T299_MAIN_STOP_REPORT.md`、
`T300_MASTER_RUN_REPORT.md` と `logs/t300-master-run/results-by-test-id.csv`、
`EXTERNAL_REVIEW_R03_DISPOSITION.md` の R04 確認節
判定: **実行内容は健全。ただし報告の見え方と実測の到達点が食い違っている（H1）**

## R05-1. 本 session の誤りの確認と訂正

Main の disposition が R04 の 2 点を不採用としている。**両方とも Main が正しい。**

### (a) G1 の 680〜840 token 試算は成立しない

R04-G1 で「nine-seat pre-vote 510 に本文増分 +170〜330 を加えると 680〜840」と試算した。
**この加算は無効である。** `logs/t297-g1/summary.json` を実際に読むと、

```
nine_seat_pre_vote  indented  text_chars=0  text_utf8_bytes=0  content_tokens=510
```

**pre-vote は本文を 1 文字も持たない。** 510 は 9 seat 分の構造そのものの大きさであり、
text 長を変えても増減しない。本 session は「最大値 = 最大 witness」と「本文が伸びる witness」を
混同した。disposition の「510 へ本文増分を加える 680–840 の推計は成立しない」は正しい。

### (b) `test_phase6_semantic_completion.py` の catalog 行数は 25 ではなく 24

R04 で「catalog 25 行」と書いたが、これは `semantic_completion` を部分文字列一致で数えた結果、
`tests/test_phase3_2_completion.py::...::test_semantic_completion_guard_rejects_dropped_history_record`
という**別 module の test 名**を巻き込んだ誤りである。正しくは **24 行（A11 23 + A12 1）**。
A-B03 = 87 と総数 890 の結論は変わらない。disposition の指摘どおり。

### G1 の中核は妥当であり、T297 で解消された

一方で「旧 corpus は 80 文字 / 96 bytes の本文で作られており、新 profile の
200 文字 / 600 bytes に対する 512 の十分性を確認できない」という中核は妥当であり、
disposition 自身も「G1 の中核は妥当」「G1 は解消済みにしない」と認めたうえで T297 を実施している。

## R05-2. T297 の実行内容（独立照合）

`logs/t297-g1/summary.json` を本 session が直接読み、handoff の表と全件照合した。**完全一致。**

| 指標 | handoff | summary.json 実測 |
|---|---|---|
| 文字列数 | 24 | **24** |
| count_status / content_budget_status | PASS | **PASS / PASS** |
| 600 bytes 本文を持つ行 | 8 | **8** |
| 512 超過 | 0 | **0** |
| 新境界本文の最大 | 482 | **482**（rebuttal indented、余裕 30） |
| 全体最大 | 510 | **510**（nine_seat_pre_vote indented、**余裕 2**） |
| provider 非 content 費用 | UNKNOWN | **UNKNOWN** |

新境界 4 形状の実測: rebuttal 318/482、opinion_change 296/441、
relation_hypothesis 318/463、answer 313/477。いずれも 200 文字 / 600 bytes。

T283 の同形状（本文 1 byte）は REBUTTAL 208/372。600 bytes 化による増分は **+110 token**、
すなわち **約 1.8 文字 / token**。日本語 BPE として妥当な比率であり、
「反復文だから不当に効率が良い」という疑いは当たらない（BPE の merge は文脈非依存のため、
反復しても 1 出現あたりの token 数は変わらない）。

`T297_G1_INDEPENDENT_REVIEW.md` は fresh Reviewer による read-only 確認で **APPROVED**、
かつ承認境界を「当該 24 文字列の content 計測だけ」に限定し、
provider 非 content 費用 UNKNOWN と pre-vote 余裕 2 token を明記している。適切である。

**R04-G1 は解消と判定してよい。**

## R05-3. Master Run の実行内容 — 報告と実測の差

### 何が実行されたか

| Run | 範囲 | 結果 | 逸脱 |
|---|---|---|---|
| **T299** | A-B01〜A-B08 完走、A-B09 は Main が interrupt | **1,205 case = 1,197 PASS / 8 FAIL / ERROR 0 / skip 0**。catalog 換算 **885 PASS / 4 FAIL / 1 BLOCKED**（890） | A-B01 初回の引数分割で exit 4 → 同 batch を 1 回再起動。途中 hash checkpoint 省略 |
| **T300** | 9 batch 試行 | **682 PASS / 3 FAIL / 205 BLOCKED**（890）。A12 は **PASS**（pytest 908.20 秒） | A-B02 / A-B03 が異常終了。Tester が Ctrl+C 送信を自己申告。途中 hash 省略 |

### H1【MEDIUM-HIGH】実測は「205 BLOCKED」ではなく「4 FAIL / 0 BLOCKED」に到達している

T300 の報告は「205 BLOCKED / 完全 baseline 未達」で終わっており、board の Notes も
「890ID=682PASS/3FAIL/205BLOCKED」である。**これは T300 単独の姿であって、到達した知見ではない。**

T300 で失われた 205 ID は **A-B02（118）+ A-B03（87）** のちょうど全量であり、
その 2 batch は **T299 では正常に完走している**。

```
T299 A-B02: exit 0, 255 passed, 29.82s
T299 A-B03: exit 1, 159 passed / 1 failed, 13.86s
```

そして Main 自身が「source 191 件が T299 実行前 hash と開始前一致、終了後も一致」を確認している。
**両 Run は hash 同一の source に対する測定である。**

合成すると:

| | T299 | T300 | 合成 |
|---|---|---|---|
| A-B01〜A-B08（890 中 889 ID） | 885 PASS / 4 FAIL | A-B02/A-B03 欠測 | **885 PASS / 4 FAIL** |
| A-B09（A12、1 ID） | BLOCKED（interrupt） | **PASS**（908.20 秒） | **PASS** |
| 合計 | — | — | **886 PASS / 4 FAIL / 0 BLOCKED** |

**Stage A の全 890 ID は、hash 同一 source 上で少なくとも一度は実測されている。**
未取得なのは「単一 Run による形式的 baseline artifact」であって、**結果そのものではない。**

Main が 2 つの Run を合算して「Master Run 完遂」と呼ばないのは規律として正しい。
しかし **Repair Plan の対象が 205 ID であるかのように見える現在の記述は、実態より 50 倍大きい。**
実際に直す対象は **4 ID** である。

**最小修正案**: T300 報告か CURRENT_STATE に「暫定合成所見」を 1 節設け、
「hash 同一 source 上で 886 PASS / 4 FAIL / 0 BLOCKED。ただし単一 Run baseline は未取得」と
明記する。形式的 baseline の再取得を要求するかは別判断でよいが、
**Repair Plan は 4 ID を対象に組むべきで、205 ID の再測定を前提にすべきではない。**

### H2【MEDIUM】A-B02 の終了コードは原因不明ではない

RC-03 は「Ctrl+C 送信とその 2 batch の終了との因果関係は UNKNOWN」としている。
しかし記録された exit code は **`-1073741510` = `0xC000013A` = `STATUS_CONTROL_C_EXIT`** である。
これは Windows が Ctrl+C による終了に対してのみ返す値で、**機構は特定できている。**

未確定なのは「誰が / なぜ送ったか」であって、「何が起きたか」ではない。
Tester 自身が Ctrl+C 送信を申告していることと合わせれば、
**RC-03 は「原因未確定」ではなく「操作起因と特定済み、意図は未確認」と書ける。**
製品欠陥の候補から外してよい。RC-03 を製品原因群と同列に置くと Repair Plan が膨らむ。

### H3【MEDIUM】P6-A11-017 が実質的に最優先の FAIL

T299 が検出した A11 の 1 件は
`tests/test_phase6_semantic_completion.py::test_p6f_parent_delivers_only_owner_envelope_after_relay_removal`
であり、**relay 撤去後に所有者の envelope だけを配送するという privacy / delivery 境界の回帰**である。

これは T300 では A-B03 ごと BLOCKED になったため **1 回しか測定されていない。**
残る 3 FAIL（A13）は private review processor の診断コード周りで、性質が違う。

**Repair Plan では A11-017 を先頭に置くべきである。** 唯一 privacy 境界に触れており、
再現性の確認（もう一度 A-B03 を走らせる）だけでも先に済ませる価値がある。
A-B03 は T299 で **13.86 秒**しかかかっていない。

### H4【LOW-MEDIUM】A13-007 / A13-009 は test 期待値の drift である可能性が高い

```
P6-A13-007  expected: exit1, code=schema_closed   observed: exit1, code=checklist_invalid（5 case）
P6-A13-009  expected: exit1, code=schema_closed   observed: exit1, code=json_invalid（1 case）
```

**拒否そのものは両方 exit 1 で成立している。** 食い違っているのは reason code の粒度だけで、
実装は test の期待（総称 `schema_closed`）より**具体的な code を返している**。
診断可能性の観点では実装側が望ましい挙動であり、**test の期待値を直すのが自然**である。

なおこの reason code 機構は R01 の P0-1 として本 session が提案したものである。
提案した機能が test 期待値との drift を生んだ形であり、その点は記録しておく。

### H5【MEDIUM】A13-017 は実装修正ではなく設計判断が要る

```
P6-A13-017  expected: unmatched 入力を exit1 で拒否   observed: exit0（後続 assert 未評価）
```

選択設計 §11 は fail aggregate を書く場合として
「Oversize (>512), unreadable, malformed, hash-mismatched, or zero populations」を列挙しており、
**unmatched は含まれていない。** したがって

- exit 0 + `human_quality_pass=false` の fail aggregate が正しい（= test が誤り）
- unmatched は §11 の列挙外なので exit 1 で拒否すべき（= 実装が誤り）

のどちらが正かは **設計文の解釈問題**であり、Implementer が選ぶべきではない。
報告も「後続の linkage_failure_count assertion の実測結果はない」としており、
**aggregate が正しく失敗を記録したかは未確認**である。

**最小修正案**: Repair Plan で Architect に 1 点だけ判断させる。
あわせて「exit 0 でも `human_quality_pass=false` なら wrapper が成功と誤読しない」ことを
確認する assert を A13 に含めるかを決める（FREEZE 後の増設になるため計画側の判断が要る）。

### H6【LOW】F4 は実測で解消された。ただし A12 の余裕は 24%

T299 の A-B01〜A-B08 は **08:12:28.7 → 08:14:58.4、合計 149.7 秒**。最長 batch は A-B02 の 29.82 秒。
**900 秒上限に対して約 30 倍の余裕**があり、R03-F4 の懸念は実測で解消した。

一方 A-B09（A12）は **908.20 秒 / 内側上限 1200 秒**で、余裕は 24%。
`Phase6SemanticBackend` の合成 fixture でこの時間なので、
**実 provider を使う Stage B の 1200 秒上限は別途検討対象**である（R02 で指摘した所要時間の件）。

### H7【LOW】G1 の残存リスク — pre-vote の余裕 2 token

T297 と独立 Reviewer が明記しているとおり、`nine_seat_pre_vote` indented の 510 は
512 に対して **余裕 2 token** であり、しかもこの形状は本文を持たないため
**profile を絞っても縮まない。** provider 側の非 content 費用（BOS/EOS、chat template、
reasoning token 等）は UNKNOWN のままである。

実 model が nine-seat pre-vote を indented（pretty-print）で出力した場合、
2 token の余裕は実質ゼロに近い。compact なら 368 で余裕 144。

**最小修正案（判断のみ）**: prompt が JSON の整形を指定しているか確認し、
指定がなければ compact を明示するか、この形状だけ超過し得ることを Stage B の
既知リスクとして B10 / B11 に記録しておく。新規 test の増設は不要。

## R05-4. 結論

**T297 は妥当に実行され、独立レビューも適切な境界で APPROVED している。R04-G1 は解消。**
本 session の G1 試算（680〜840）と catalog 25 行は誤りで、Main の訂正が正しい。

**Master Run については、実行そのものに製品側の問題は見えない。**
T299 は 1,205 case を 2.5 分で完走し 99.3% PASS、T300 は A12 を PASS させた。
失われた 205 ID は Ctrl+C による操作起因で、機構は exit code から特定できる。

**最大の論点は H1 である。** 報告の見え方は「205 BLOCKED / baseline 未達」だが、
hash 同一 source 上での実測到達点は **886 PASS / 4 FAIL / 0 BLOCKED** である。
Repair Plan を 205 ID の再取得から始めると、R01 で指摘した「証拠形式の完全性を
critical path に据える」パターンに戻る。**直すべきは 4 ID で、うち 1 件（A11-017）が
privacy 境界、1 件（A13-017）が設計判断、2 件（A13-007/009）が test 期待値の drift である。**

形式的な単一 Run baseline を取り直すかは、4 ID の修正後に
「Targeted Retest + Full Regression」として一度で済ませる方が安い。
A-B02 と A-B03 は合わせて 43.7 秒である。

## R05-5. 検証した内容と検証していない内容

**検証した**: `logs/t297-g1/summary.json` の全 24 行（witness / encoding / chars / bytes /
tokens / fits）と handoff 表の照合、最大値・余裕の再計算、
`logs/t300-master-run/results-by-test-id.csv` の 890 行の result 集計と group 別内訳、
3 FAIL の expected / observed / exception、T299 handoff の batch 別 exit / 件数 / 時刻、
`-1073741510` の 16 進変換、T299 Main 停止報告の catalog 換算、
`PHASE6_MASTER_TEST_BATCHES.csv` の A-B02 / A-B03 member 数との照合、
T297 独立レビュー、disposition の R04 確認節。

**検証していない**: pytest / counter / processor の再実行、T297 corpus の private 原本、
T300 の private raw、A11-017 の失敗内容（node 名のみ確認、assertion 詳細は未読）、
T298 計画レビューの全文、実 provider / model / GPU、
provider 非 content 費用、Stage B。

---

# R06 — 4 件の FAIL の内容特定

日付: 2026-09-14
対象: T299 / T300 が保存した JUnit raw（`A-B03.junit.xml` / `A-B04.junit.xml`）、
該当 test source、`scripts/run_phase5_local_smoke.py`、`scripts/phase6_private_review.py`
判定: **4 件すべて T296 が追加した新挙動と、更新されていない test 期待値の drift。
製品の誤動作は 1 件も確認されない。ただし A11-017 は privacy assertion を未実行にしている**

## R06-1. 根本原因は 1 つ

T296 が 2 つの新しい挙動を実装した。

1. `run_phase5_local_smoke.py:3081-3088` の **broker ready fingerprint gate**
2. `phase6_private_review.py` の **細分化された reason code taxonomy**

どちらも妥当な追加である。しかし **対応する test の期待値が旧契約のまま残った。**
4 件の FAIL はすべてこの drift であり、製品が誤った結果を出したものはない。

## R06-2. P6-A11-017 — broker fingerprint gate による早期停止

node: `tests/test_phase6_semantic_completion.py::test_p6f_parent_delivers_only_owner_envelope_after_relay_removal`
失敗行: `tests/test_phase6_semantic_completion.py:517` `assert envelopes == {}`
観測: `envelopes` に 9 人分が残存（`Left contains 9 more items`）

### 機構

production は client 起動時に relay を drain する。

```
# run_phase5_local_smoke.py:3102
"discussion_envelope": envelopes.pop(player_id)
```

`envelopes` が空にならない = **client loop に到達していない**。
その手前に T296 が追加した gate がある。

```
# run_phase5_local_smoke.py:3081-3088
if config.phase6 and not phase6_fixture:
    backend_identity = ready_broker.get("backend_identity")
    if (not isinstance(backend_identity, Mapping)
        or backend_identity.get("config_fingerprint") != config.settings.backend_config().config_fingerprint):
        raise RuntimeError("broker ready fingerprint mismatch")
```

この test の fake は `broker.ready.json` に対して
`{"host": "127.0.0.1", "port": 1, "config": {}}` を返す。**`backend_identity` を持たない。**
したがって `RuntimeError("broker ready fingerprint mismatch")` が client 起動前に発生し、
`_run_game` が cleanup して戻り、relay は drain されない。

これは disposition が T296 の作業として明記した内容と一致する。

> 同じfake経路でbroker ready fingerprint不一致ならclient開始前に止まることも確認対象に含めた

**gate 自体は正しく働いている。** 旧 test の fake が新しい前提を満たしていない。

### R05-H3 の訂正

R05 で本件を「privacy / delivery 境界の回帰」と書いたが、**不正確である。**
配送が誤っているという証拠はない。実際には

```
assert events[:3] == ["server", "relay-removed", "broker"]   ← PASS
assert envelopes == {}                                        ← FAIL（ここで停止）
assert len(launched) == 11                                    ← 未評価
  bootstrap["discussion_envelope"] == originals[owner]        ← 未評価
  他プレイヤーの sentinel が含まれないこと                     ← 未評価
  result に sentinel / context_payload が無いこと              ← 未評価
```

**per-owner の分離を検証する assertion は 1 つも実行されていない。**

したがって正しい表現は「privacy 境界が壊れた」ではなく
**「privacy 境界が現在まったく検証されていない」**である。
A-B03 は T300 では BLOCKED だったため、この test は 2 Run を通じて 1 回しか走っておらず、
その 1 回も分離 assertion に到達していない。

**修正案**: test の fake `read()` に `backend_identity` を足して gate を通す
（`config.settings.backend_config().config_fingerprint` と一致する値を返す）。
1 行で済む。その後に分離 assertion が初めて評価される。
**gate を弱めたり `phase6_fixture=True` で迂回したりしない。**

## R06-3. P6-A13-007 / A13-009 — reason code の粒度

```
A13-007  test_private_review_closed_checklist_types_and_enums[None/True/1/UNKNOWN/NOT_APPLICABLE]
  expected: stderr = "phase6_private_review_failed category=input code=schema_closed\n"
  actual  : stderr = "phase6_private_review_failed category=input code=checklist_invalid\n"

A13-009  test_private_review_duplicate_json_key_is_corrupt
  expected: code=schema_closed
  actual  : code=json_invalid
```

両方とも **`returncode == 1`、`stdout == ""`、stderr は 1 行、private 本文なし**。
fail-closed と redaction の契約は成立している。**違うのは code のラベルだけ。**

実装は test の総称 `schema_closed` より**具体的な code** を返している。
診断可能性の観点では実装側が望ましい（R01-P0-1 が求めたのはまさにこれである）。

**修正案**: test 期待値を実装に合わせる。`checklist_invalid` / `json_invalid` を正とする。

## R06-4. P6-A13-017 — 分類先カウンタの不一致。**T300 の記録が raw と食い違う**

node: `test_private_review_population_failures_are_redacted[unmatched-linkage_failure_count]`

### T300 の結果 CSV の記述

```
expected: unmatched入力をexit1で拒否
observed: exit0でassert不成立（1case）。後続aggregate assertは未評価
```

### 保存 raw（T299 と T300 の両 JUnit が同一）

```
result = _invoke(...)
assert result.returncode == 1 and sentinel not in result.stdout + result.stderr   ← PASS
aggregate = json.loads(output.read_text())
>       assert aggregate[expected] == 1 and sentinel not in output.read_text()
E       assert (0 == 1)
tests\test_phase6_private_review.py:479: AssertionError
```

**exit code は 1 で、拒否は正しく行われている。fail aggregate も書かれている。**
失敗しているのはその次の行で、`linkage_failure_count` が 1 ではなく **0** だったこと。
CSV が言う「exit0」も「後続 aggregate assert は未評価」も、**raw と逆である。**

### 機構

mutation は `accepted-text.jsonl` と `server.result.json` の `request_event_id` を
sentinel へ書き換え、manifest hash を更新する。shard 側の generation / terminal は旧 ID のまま。

collector がこれを population の不完全として検出し、`_population` が

```
"accepted server population missing or ambiguous" / "accepted text population incomplete"
  → ReviewFailure("evidence", "population_missing")
```

を送出する。`_aggregate` の対応表は

```
{"correlation_missing","correlation_mismatch","checklist_population_mismatch"} → linkage
{"artifact_missing","manifest_missing","population_missing","path_missing"}    → missing
{"correlation_duplicate"}                                                      → duplicate
それ以外                                                                        → corrupt
```

なので **`missing_count = 1` / `linkage_failure_count = 0`** になる。
test は `("unmatched", "linkage_failure_count")` を期待している。

つまり **「unmatched を linkage と数えるか missing と数えるか」という taxonomy の不一致**であり、
A13-007 / A13-009 とまったく同じ class である。

### R05-H5 の訂正

R05 で「§11 の fail aggregate 列挙に unmatched が無いので exit 1 と exit 0 のどちらが正かを
Architect が決める必要がある」と書いたが、**前提が誤っていた。**
実測では exit 1 で拒否されており、fail-open は起きていない。
決めるべきは exit code ではなく **どのカウンタへ分類するか**である。
判断は必要だが、深刻度は R05 の記述より低い。

**修正案**: `unmatched` を `missing` と数えるのが正しいなら test の parametrize を
`("unmatched", "missing_count")` に直す。`linkage` が正しいなら `_population` の
該当分岐を `correlation_mismatch` にする。**どちらでも fail-closed は変わらない。**

## R06-5. 新規指摘

| # | 内容 | 深刻度 |
|---|---|---|
| I1 | T300 の結果 CSV が A13-017 を raw と逆に記述している | MEDIUM |
| I2 | A11-017 の privacy 分離 assertion は 2 Run を通じて一度も実行されていない | MEDIUM |
| I3 | reason code taxonomy が test と実装で別々に決められた | LOW |

### I1【MEDIUM】triage 記録が保存 raw と矛盾している

`logs/t300-master-run/results-by-test-id.csv` の A13-017 行は、
exit 0 で失敗し後続 assert は未評価、と記述している。保存 JUnit は exit 1 で通過し、
後続 assert が失敗したことを示す。**両 Run の JUnit が一致しているので、記録側の誤りである。**

この行だけを読むと「processor が unmatched population を受理する（fail-open）」という
深刻な欠陥に見える。実際は分類先カウンタの相違にすぎない。
**Repair Plan が誤った severity で組まれる。**

**最小修正案**: 当該行の expected / observed を保存 JUnit の文言へ訂正する。
他の 2 行（A13-007 / A13-009）は raw と一致しており訂正不要。

### I2【MEDIUM】privacy 分離は現在まったく検証されていない

R06-2 のとおり、`test_p6f_parent_delivers_only_owner_envelope_after_relay_removal` の
分離 assertion 4 種は未実行である。この test は
**9 client それぞれが自分の envelope だけを受け取ること**を保証する唯一の node であり、
Phase 6 完了条件 4（private 情報漏洩がない）に直結する。

現状は「壊れている」ではなく「**未確認**」。だが未確認のまま Stage B（実 model）へ進むと、
実ゲームで初めて分離が試されることになる。

**最小修正案**: R06-2 の 1 行修正を Repair Plan の先頭に置き、修正後に A-B03 を再実行する。
A-B03 は T299 で **13.86 秒**である。

### I3【LOW】taxonomy が二重に決められた

A13 の 3 件はすべて「test が想定した code / counter」と「実装が返す code / counter」の相違である。
T296 は test と実装の両方を同一 task で書いており、**その内部で taxonomy が一致していなかった。**
Master Plan A20 の入力欄は「closed codes 全件」とだけ書かれ、**具体的な code 一覧を固定していない。**

**最小修正案**: Repair 時に code / counter の一覧を 1 箇所（fixture JSON か plan の表）に literal で
固定し、test と実装の両方がそこを参照する。新しい framework は作らない。

## R06-6. 結論

**4 件はすべて test 期待値の更新漏れであり、製品の誤動作は 1 件も確認されない。**
修正はいずれも小さい。

| ID | 直す対象 | 規模 |
|---|---|---|
| A11-017 | test の fake broker readiness に `backend_identity` を足す | 1 行 |
| A13-007 | test 期待値を `checklist_invalid` へ | 1 行 |
| A13-009 | test 期待値を `json_invalid` へ | 1 行 |
| A13-017 | parametrize を `missing_count` へ（または実装の分岐を `correlation_mismatch` へ） | 1 行 + 判断 1 件 |

**ただし A11-017 の修正は「FAIL を消す」作業ではなく「未実行だった privacy 検証を初めて走らせる」
作業である。** 修正後に分離 assertion が初めて評価されるため、そこで新たな FAIL が出る可能性がある。
Repair Plan ではこれを「1 件の修正」ではなく「1 件の修正 + 未知の検証」として扱うべきである。

R05-H3（privacy 境界の回帰）と R05-H5（fail-open の疑い）は、
いずれも保存 raw を読む前の推定であり、本節で訂正した。

## R06-7. 検証した内容と検証していない内容

**検証した**: `logs/phase6-private-evidence/synthetic/T299-.../A-B03.junit.xml` と
`A-B04.junit.xml`、`.../T300-.../A-B04.junit.xml` の failure 本文と traceback、
`tests/test_phase6_semantic_completion.py` の該当 test 全文、
`tests/test_phase6_private_review.py:479 / 499 / 526` の assertion、
`run_phase5_local_smoke.py:3060-3110`（relay consume / fingerprint gate / client 起動）、
`_consume_phase6_relay`、`phase6_private_review.py` の `_population` の
`ReviewFailure` 分岐と `_aggregate` の failure→counter 対応、
`logs/t300-master-run/results-by-test-id.csv` の該当 3 行。

**検証していない**: 修正の実施、test の再実行、`_phase6_semantic_population` 内部で
実際にどの ValueError が送出されたか（分岐からの推定であり、例外文字列の実測はしていない）、
A11-017 の分離 assertion が修正後に PASS するかどうか、
`_run_game` が RuntimeError をどの経路で捕捉して戻ったかの実測、実 provider / model / GPU。

---

# R07 — T301 修正 / T302 レビュー / T303 Master Run の実行内容確認

日付: 2026-09-14
対象: `logs/t301-repair/source-delta.json`、修正後の 2 test file、
`T302_MASTER_RUN_FINAL_REVIEW.md`、`T303_REPAIRED_MASTER_RUN.md` / `T303_MAIN_RESULT.md`、
`logs/t303-master-run/` の集計・checkpoint・process 記録、
private container `T303-20260914T093555284506Z` の JUnit raw
判定: **実行は健全。Stage A は正式に完了。R06-I2 が解消。残るは Stage B のみ**

## R07-1. 修正内容（R06 の推奨と完全一致）

`source-delta.json` の `changed` は 3 件で、うち 1 件は本 session の `EXTERNAL_REVIEW_LOG.md`。
**製品側の変更は 0 件、test 2 file の 4 箇所のみ。**

| ID | 修正 | 場所 |
|---|---|---|
| A11-017 | fake broker readiness に `backend_identity.config_fingerprint` を追加 | `test_phase6_semantic_completion.py:514` |
| A13-007 | 期待 code を `checklist_invalid` へ | `test_phase6_private_review.py:499` |
| A13-009 | 期待 code を `json_invalid` へ | `test_phase6_private_review.py:526` |
| A13-017 | parametrize を `("unmatched", "missing_count")` へ | `test_phase6_private_review.py:452` |

### A11-017 の修正は gate を迂回していない

```
"backend_identity": {
    "config_fingerprint": config.settings.backend_config().config_fingerprint,
},
```

**実 fingerprint を計算して渡している。** `phase6_fixture=True` による迂回でも、
gate 側の緩和でもない。`run_phase5_local_smoke.py:3081-3088` の検証はそのまま有効で、
test が新しい前提を満たす形に直された。R06 の推奨どおりである。

### 製品 source は不変（独立照合）

`logs/t303-master-run/frozen.json` の source 191 件について、本 session が
現在の working tree の bytes を SHA-256 で再計算して照合した。

```
製品 / script source（ai_client, server, scripts, content, protocol）: 104 件  不一致 0
tests:                                                                 73 件  不一致 0
合計                                                                   191 件  不一致 0
```

**製品 / schema / fixture JSON / config / validation は一切変更されていない。**

## R07-2. R06-I2 の解消 — privacy 分離が初めて検証され PASS した

R06 で最大の懸念として挙げたのは、A11-017 の修正が「FAIL を消す作業」ではなく
「未実行だった privacy 検証を初めて走らせる作業」であり、そこで新たな FAIL が
出る可能性がある、という点だった。

private JUnit 実物を直接読んだ結果:

```
test_p6f_parent_delivers_only_owner_envelope_after_relay_removal
  file = A-B03.junit.xml   time = 0.072   結果 = PASS
```

test が PASS したということは、以下の assertion **すべてが実行されて成立した**ことを意味する。

- `assert envelopes == {}`（relay の drain）
- `assert len(launched) == 11`
- 各 client の `bootstrap["discussion_envelope"] == originals[owner]`
- 他プレイヤーの `private_transport_sentinel` が encode 済み bootstrap に含まれないこと
- `"discussion" not in json.dumps(item["arguments"])`
- `"owner-only-sentinel" not in json.dumps(item["environ"])`
- `result` に sentinel と `context_payload` が含まれないこと

**9 client の envelope 分離は、これが初めての実測検証であり、PASS した。**
Phase 6 完了条件 4（private 情報漏洩がない）の deterministic 側の根拠が初めて成立した。

## R07-3. T303 Master Run の実測（独立照合）

### 結果

`logs/t303-master-run/results-by-test-id.csv` を本 session が集計した。

```
rows: 890    result: {'PASS': 890}
A01=19 A02=45 A03=27 A04=48 A05=11 A06=12 A07=32 A08=8 A09=18
A10=38 A11=23 A12=1 A13=24 A14=5 A15=579   （全 PASS）
```

**890 ID すべて PASS。FAIL 0、BLOCKED 0。** catalog の group 別件数と完全一致する。

`junit-mapping-check.json`:

```
catalog_rows=890  result_rows=890  junit_cases=1206
pass_ids=890  fail_ids=0  blocked_ids=0
unattributed_or_ambiguous_cases=0
all_catalog_ids_unique=True  all_junit_cases_owned_once=True
```

private JUnit 実物の集計（本 session が直接 parse）:

| batch | tests | failures | errors | skipped | time (s) |
|---|---:|---:|---:|---:|---:|
| A-B01 | 212 | 0 | 0 | 0 | 1.932 |
| A-B02 | 255 | 0 | 0 | 0 | 28.692 |
| A-B03 | 288 | 0 | 0 | 0 | 12.757 |
| A-B04 | 68 | 0 | 0 | 0 | 27.455 |
| A-B05 | 794 | 0 | 0 | 0 | 5.578 |
| A-B06 | 282 | 0 | 0 | 0 | 17.625 |
| A-B07 | 318 | 0 | 0 | 0 | 16.829 |
| A-B08 | 37 | 0 | 0 | 0 | 0.235 |
| A-B09 | 1 | 0 | 0 | 0 | 907.923 |
| 合計 | 2,255 | **0** | **0** | **0** | — |

JUnit の `tests` は subtest を含むため 2,255、Test case 単位では 1,206。
`skipped=0` なので、**PASS を skip で作った箇所はない。**

### T300 の逸脱がすべて解消されている

| 項目 | T300 | T303 |
|---|---|---|
| hash checkpoint | 途中省略 | **12 件**（PRE_RUN / POST_TARGETED / 各 batch 後 / FINAL）、mismatch 0 |
| batch-status event | 3 行のみ | **30 件**（10 invocation × PLANNED/START/END）、全 END が実 exit 0 |
| 異常終了 | A-B02 `0xC000013A`、A-B03 exit UNKNOWN | **なし。全 batch 実 exit 0** |
| Ctrl+C | Tester が送信申告 | **なし** |
| process 残存 | — | `all_direct_pids_absent=True`、`related_live_process_count=0` |
| 欠測 | 205 ID | **0** |

### 実行条件

- targeted 先行確認: 11 case、exit 0、0 failed、**5.192 秒**
- 通常 batch 最長 A-B02 **28.692 秒** / 上限 900 秒（余裕 31 倍）
- A-B09 **908.646 秒** / 上限 1260 秒（余裕 28%）
- Stage A 全体の実時間は約 **17 分**
- raw manifest 43 entries / hash mismatch 0、completion leaf hash 599 entries / mismatch 0

### 独立性

T302 が同一 Reviewer 責務で**実行前と最終の 2 回** APPROVED。
承認範囲を「修正後 source に対する Stage A だけ」に明示的に限定し、
Stage B / 実 provider / D072 の実会話品質・privacy 原文審査を除外している。適切である。
T302 の照合値（1,217 case、batch 内訳 99/255/160/68/156/215/215/37/1、group 別件数、
checkpoint 12、event 30）は本 session の独立計測と一致した。

## R07-4. 過去指摘の状態更新

| 指摘 | 状態 |
|---|---|
| R05-H1（205 BLOCKED は実態ではない。Repair は 4 ID を対象にすべき） | **解消**。4 ID を修正して全件再実行、約 17 分で完全 baseline を取得 |
| R05-H2（exit code は Ctrl+C と特定できる） | 該当なし。T303 に異常終了なし |
| R05-H3 / H5（privacy 回帰 / fail-open の疑い） | R06 で訂正済み。本 Run で該当 test が PASS |
| R06-I1（T300 CSV の A13-017 記述が raw と逆） | `logs/t301-repair/t300-results-corrected.csv` として訂正版が保存された |
| R06-I2（privacy 分離が未検証） | **解消**。R07-2 のとおり初めて実行され PASS |
| R06-I3（reason code taxonomy の二重決定） | 実質解消。test を実装へ合わせた。literal 一覧の一元化は未実施 |
| R04-G1（512 の根拠） | T297 で解消済み。残存リスク（pre-vote 余裕 2 token、provider 非 content UNKNOWN）は継続 |
| R03-F4（900 秒上限） | **実測で解消**。最長 28.7 秒 |

## R07-5. 新規指摘

| # | 内容 | 深刻度 |
|---|---|---|
| J1 | catalog の `source_sha256` 列が pre-repair identity のまま | LOW |

### J1【LOW】catalog の hash 列と現 source が 2 module で食い違う

`PHASE6_MASTER_TEST_CATALOG.csv` の `source_sha256` 列は FREEZE 時点の値を保持しており、
修正した 2 test file については現在の bytes と一致しない。
T302 / CURRENT_STATE がこの区別を明記しているため**誤りではない**が、
catalog だけを見た読み手は hash 不一致を欠陥と誤読し得る。

**最小修正案**: catalog の先頭か Plan §1 に
「`source_sha256` は FREEZE 時点の identity。repair 後の実 identity は
`logs/t303-master-run/frozen.json`」と 1 行書く。CSV 再生成は不要。

## R07-6. 結論

**Stage A は正式に完了した。890 ID 全 PASS、FAIL 0、BLOCKED 0、skip 0、
process 残存 0、source 191 件が実行前後で不変。**
修正は R06 の推奨どおり test 期待値の 4 箇所のみで、製品は 1 行も変えていない。
gate の迂回や skip による PASS 作りは、本 session の独立照合でも確認されなかった。

特に、**R06 で最大の懸念だった privacy 分離 assertion が初めて実行され PASS した**ことは、
Phase 6 完了条件 4 の deterministic 側の根拠が初めて成立したことを意味する。

**Phase 6 に残っているのは Stage B だけである。** B01–B11 は全件未実行で、
実 LLM ゲームの実行回数はいまだ 0 である。ROADMAP の完了条件
「前の発言を受けた会話が成立する」は、まだ一度も観測されていない。

Stage B の未知は R05 / 前回回答で挙げたものが変わらず残る。

1. 200 文字 / 512 token でプレイが成立するか（未確認）
2. `nine_seat_pre_vote` indented の余裕 2 token と provider 非 content 費用 UNKNOWN
3. 出力 5〜10 倍時の queue 待ちと 180 秒 day の関係（未測定）
4. accepted text 512 件上限（未証明。513 件で実ゲーム本体が FAIL する）
5. 実 model の会話品質そのもの

そして D073 の実 LLM 予算は **1 回 + 必要なら追加 1 回の計 2 回**である。

## R07-7. 検証した内容と検証していない内容

**検証した**: `logs/t301-repair/source-delta.json`、修正後 2 test file の該当 4 箇所、
`frozen.json` の source 191 件と現在 bytes の SHA-256 照合（製品 104 / tests 73）、
`logs/t303-master-run/results-by-test-id.csv` の 890 行集計と group 別内訳、
`aggregate-result.json` / `junit-mapping-check.json` / `hash-checkpoint.json` /
`process-release.json` / `post-run-checks.json` / `targeted-result.json` /
`final-verification.json` / `batch-status.json`、
private container `T303-...` の A-B01〜A-B09 JUnit 実物（tests / failures / errors / skipped / time）、
`test_p6f_parent_delivers_only_owner_envelope_after_relay_removal` の個別結果、
T302 レビュー handoff。

**検証していない**: pytest の再実行、private raw 本文、completion 原本 599 entries の個別 hash、
T303 Tester session の独立性（handoff の記載に依拠）、Stage B、実 provider / model / GPU、
`PHASE6_MASTER_TEST_CATALOG.csv` の全 890 行の hash 列。

---

# R08 — テスト内容のレビュー（何を実際に検証しているか）

日付: 2026-09-14
対象: `tests/` の Phase 6 関連 8 module と `tests/fixtures/phase6_private_review_vectors.json`、
catalog 890 ID の AST 走査、`run_phase5_local_smoke.py` の `responsive` 判定と
`machine_semantic_pass` 導出
判定: **テスト内容の質は高い。TEST_POLICY の literal oracle 要件は実際に守られている。
指摘 6 件はいずれも「読み違えを防ぐための明示」であり、test の作り直しを要するものはない**

R07 は「実行が健全か」を見た。本節は「**890 個の green が何を証明しているか**」を見る。

## R08-1. 実際に良かった点（具体的に確認したもの）

### (a) 20 record kind の visibility が手書きの literal table になっている

`tests/test_phase6_discussion_state.py::_all_record_kinds()` は 20 種すべてについて
`(record, EvidenceRecordKind.X, EvidenceVisibility.Y)` を**直値で列挙**している。
production の `_FIXED_VISIBILITY` を import して回しているのではない。

```
(ChatRecord(..., "opaque-public", ...),  CHAT,            PUBLIC)
(AbilityResultRecord(...),               ABILITY_RESULT,  AUTHORIZED_PRIVATE)
(KnownUnmodeledEventRecord(...),         KNOWN_UNMODELED, VISIBILITY_LOST)
...
```

TEST_POLICY Phase 6 の「期待する visibility を helper から導出してはならない」を満たしている。
`_FIXED_VISIBILITY` を書き換える regression は、この table との不一致で必ず落ちる。

### (b) R01-M4（private CHAT の母数欠落）が推奨以上に解消されている

R01-M4 / R03 で「`_terminal()` が kind から visibility を導出しており、
AUTHORIZED_PRIVATE な chat が G matrix に 1 行も無い」と指摘した。現在は、

1. `_terminal(player, capture, generation, event, *, visibility: str)` — **visibility が必須 kwarg**。
   導出をやめ、呼び出し側が明示する形になった。
2. 専用テストが 3 本ある。

```
test_private_chat_literal_visibility_matches_sealed_descriptor
    expected = vectors["fixture_contract"]["private_chat_visibility"]
    assert expected == "AUTHORIZED_PRIVATE"          ← literal 二重アンカー

test_private_chat_public_terminal_visibility_is_rejected
    with pytest.raises(ValueError, match="chat terminal visibility mismatch"):
        _private_chat_population("PUBLIC")           ← 誤分類の負例

test_private_chat_missing_population_is_rejected_separately
```

**私が「検出できない」と指摘した regression（private chat を PUBLIC と誤分類）は、
いま能動的に拒否される負例として存在する。** 指摘した範囲を超えて対処されている。

### (c) 境界が実バイト数の literal になっている

```
@pytest.mark.parametrize("target_size,accepted", ((16383, True), (16384, True), (16385, False)))
def test_proposal_canonical_byte_literal_boundary(...)
    assert len(canonical_json_bytes(proposal)) == target_size
```

16 KiB 境界を ±1 バイトで挟んでいる。`test_design_state_and_proposal_limits_are_literal` も併存。

### (d) 検証を持たない test は 890 中 2 件だけ

catalog 890 ID を AST で走査し、`assert` 文・`self.assertX` 呼び出し・`pytest.raises` の
いずれも持たない test 関数を数えた。

```
検証あり: 902   検証なし: 9   （うち catalog 890 に属するのは 5、Phase 6 群は 2）
```

group 別の平均検証数は 2.6〜11.0 で、極端に薄い群はない。

| group | tests | 検証合計 | 平均 |
|---|---:|---:|---:|
| A03 | 27 | 70 | 2.6 |
| A04 | 48 | 242 | 5.0 |
| A07 | 32 | 351 | 11.0 |
| A10 | 38 | 344 | 9.1 |
| A13 | 24 | 77 | 3.2 |
| A15 | 579 | 2,731 | 4.7 |

A03 が 2.6 と低いが、`test_phase6_discussion_context.py` は `pytest.raises` が 32 箇所ある
検証境界 module であり、1 テスト 1 負例の構成として妥当である。

### (e) A12 は 11 process の実起動 end-to-end である

```
assert result["machine_semantic_pass"] is True
assert result["semantic"]["responsive_accepted_count"] >= 1
assert result["semantic"]["pre_vote_reassessment_count"] >= 1
assert result["semantic"]["maximum_chat_starts_per_player_phase"] <= 2
assert len(result["cleanup"]) == 11 and all(not c["alive"] for c in result["cleanup"])
assert result["server"]["game_end"] is True
```

server + broker + 9 client の実プロセス、実 WebSocket、実ゲームコア、
実 discussion/brain/controller を通す。LLM だけを決定的 backend に置換している。
908 秒かかるが、完了条件 1 の骨格はここで証明されている。

### (f) `responsive` は形だけの指標ではない

`run_phase5_local_smoke.py:2123-2134` の `responsive=True` の条件は、

- peer reference が有効であること
- channel authorization が一致すること
- **引用元が当該発言より厳密に先行していること**

の 3 つを満たしたときだけである。本文が実際に応答になっているかは見ないが、
「許可された先行 peer record を実際に引用している」ことは機械的に保証される。
完了条件 2 の deterministic な代理指標として妥当である。

## R08-2. 指摘

| # | 内容 | 深刻度 |
|---|---|---|
| K1 | 890 ID のうち 2 件は helper process であり単体では何も証明しない | LOW |
| K2 | A12 の最強の主張 `machine_semantic_pass` は製品の自己判定 | MEDIUM |
| K3 | broker fingerprint gate と実 process 起動を同時に通す test が無い | MEDIUM |
| K4 | A12 の evidence container が `task_id="T293"` 固定 | LOW-MEDIUM |
| K5 | 890 の内訳（Phase 6 中核は 247 = 27.8%）が明示されていない | LOW |
| K6 | 会話内容そのものを検証する test は 0 件 | MEDIUM（設計どおり） |

### K1【LOW】2 ID は test ではなく helper process

`test_private_evidence_session_a` / `test_private_evidence_session_b`（A14）は
assertion を 1 つも持たない。これは欠陥ではなく、親テスト
`test_private_evidence_survives_real_pytest_cleanup` が subprocess として起動し、
その stdout の JSON を親が検証する構造だからである。

ただし **catalog 上は独立した 2 つの Test ID として PASS に数えられる。**
「890 ID 全 PASS」の 2 件は、単体では「例外なく終了した」以上を意味しない。

**最小修正案**: catalog の当該 2 行の `purpose` に「親テスト用の子 process。単体判定ではない」
と書く。件数は変えない。

### K2【MEDIUM】A12 の中心的主張は製品の自己判定である

`result["machine_semantic_pass"]` は runner 自身が
`semantic_requirements_met = responsive_count > 0 and cap_ok` として導出した値で、
test はそれを True と assert している。**製品が自分の合格を宣言し、test がそれを写している。**

これは D068 / design §11 の「acceptance objective ごとに semantic PASS authority は 1 つ」
「wrapper が同じ証拠を再解釈して競合する PASS を作らない」という決定の**意図どおり**であり、
欠陥ではない。実際 A12 は同時に

- `responsive_accepted_count >= 1`（authority の内部値だが独立した数値）
- `cleanup == 11` / `all not alive`（process 事実）
- `server.game_end is True`（server 側の事実）

という**authority 外の独立事実**も検証しており、完全な自己循環ではない。

**明示だけしておくべき点**: `machine_semantic_pass` が True であることは
「runner の semantic collector が自分の規則を満たしたと判定した」という意味であって、
第三者検証ではない。Stage B の B01–B11 判定時にこの区別が曖昧になると、
「A12 が PASS したから会話は成立している」という読み替えが起きうる。

**最小修正案**: Plan §3 の A12 行の PASS 条件に
「`machine_semantic_pass` は named semantic PASS authority の自己判定であり、
会話内容の第三者検証ではない」と 1 行足す。

### K3【MEDIUM】gate と実 process が同じ test を通っていない

T296 が追加した broker fingerprint gate は

```
if config.phase6 and not phase6_fixture:
    ... raise RuntimeError("broker ready fingerprint mismatch")
```

であり、**`phase6_fixture=True` では評価されない。**

- **A12**（実 11 process 起動）は `phase6_fixture=True` → **gate を通らない**
- **A11-017**（gate を検証）は `_spawn_owned` 等を全面 mock → **実 process は起動しない**

つまり「実 fingerprint 検証 + 実 process 起動」の組合せは Stage A のどこにも無い。
Stage B（`phase6_fixture=False`、実 provider）で初めてこの経路が実行される。

gate 自体は A11-017 で機能確認済みであり、実害が出るとすれば
「実 broker が返す `backend_identity` の実際の形が gate の期待と合わない」場合である。
**それは実 LLM 1 回目の起動直後に失敗する形で現れる。**

**最小修正案**: Stage B の実起動前 checklist に
「broker が `backend_identity.config_fingerprint` を実際に出力することを起動直後に確認する」
を入れる。新規 test の増設は不要（FREEZE 後の増設にあたるため）。B01 の観測項目で足りる。

### K4【LOW-MEDIUM】A12 の evidence container が停止した task ID に固定されている

```
create_private_evidence_container(..., evidence_kind="synthetic", task_id="T293", ...)
```

T293 は CANCELLED 済みの task である。この固定のため、T300 / T303 は
completion 原本と Run の対応付けを**時刻相関**で行わざるを得なかった
（`process-release.json` の `linkage_limit` が「T293 の fixture 固定 task ID。
batch 実行窓と completion-result.json 存在で今回 Run へ参照づけ、task ID 単独では帰属を主張しない」と明記）。

R01-H3 で指摘した「evidence の provenance が時刻相関でしか特定できない」問題が、
この 1 箇所に残っている。他は `game/` と `synthetic/` の分離で解消済みである。

**最小修正案**: `task_id` を実行時の Run ID から取る。ただし A12 は
G の排他所有 file ではなく P6-F 側の file なので、**FREEZE 後の変更になる。**
Repair cycle の枠内で扱うか、technical debt として次 Phase へ送るかは計画側の判断。
**Stage B の実ゲームは別 container（`game/`）なので、この問題は Stage B には波及しない。**

### K5【LOW】890 の内訳を明示しておくべき

| 区分 | ID 数 | 比率 |
|---|---:|---:|
| Phase 6 の新規・中核（A03–A14） | **247** | 27.8% |
| runner / profile 互換（A01・A02） | 64 | 7.2% |
| **既存回帰（A15）** | **579** | **65.1%** |

「890 全 PASS」は「Phase 6 の機能が 890 通り検証された」ではない。
**約 3 分の 2 は Phase 1–5 の既存資産が壊れていないことの確認**である。
これは健全な構成比だが、対外的な報告で混同されやすい。

**最小修正案**: `aggregate-result.json` か Plan §3 冒頭にこの 3 分類を書く。

### K6【MEDIUM】会話内容そのものを検証する test は 1 件も無い

Stage A の 890 ID は、schema の閉性、visibility 分類、correlation、
transaction の atomicity、上限、cleanup、privacy 境界を検証する。
**「質問に対して回答になっているか」「反論が主張に対応しているか」を判定する test は存在しない。**

これは design §11 と D072 の設計どおりである（NL 品質は Stage B と private Reviewer の領分）。
指摘として挙げる理由は、**完了条件 3（質問応答 / 反論 / 意見変更のいずれか）が
Stage B への依存度が最も高く、Stage A の green から何も推定できない**ことを
明示しておくためである。

A06 の `test_p6b_opinion_change_requires_exact_prior_and_new_evidence` などは
「OPINION_CHANGE を名乗るなら exact prior と new evidence を持て」という**構造要件**を
検証しており、意見が実際に変わったかは見ていない。これで正しい。

## R08-3. 結論

**テスト内容の質は高い。** 特に

- 20 kind の visibility literal table
- private CHAT の positive / negative / missing の 3 本
- 16383/16384/16385 のバイト境界
- `_terminal(*, visibility)` の必須 kwarg 化

は、TEST_POLICY Phase 6 が要求する「production helper から独立した literal known-answer」
「期待値を helper から導出しない」を実際に満たしている。
R01-M4 で挙げた唯一の実質的な内容欠陥は、推奨を超える形で解消されている。

**残る 6 件はいずれも test の作り直しを要しない。** K1 / K5 は catalog への注記、
K2 は Plan §3 への 1 行、K3 は Stage B checklist への 1 項目、
K4 は FREEZE 後の判断、K6 は明示のみである。

**最も重要なのは K2 と K6 を混同しないことである。**
A12 の `machine_semantic_pass=True` は「9 体が完走し、許可された先行発言を引用した発言が
1 件以上あり、CHAT 上限を守り、process が残らなかった」ことの製品による自己判定であって、
**会話が成立したことの証明ではない。** それは Stage B でしか得られない。

## R08-4. 検証した内容と検証していない内容

**検証した**: `tests/fixtures/phase6_private_review_vectors.json` 全体、
`_all_record_kinds()` の 20 行、`test_proposal_canonical_byte_literal_boundary`、
`_terminal()` の署名と private chat 3 テスト、A06 / A08 の全 test 名、
A12 の本体と assertion、catalog 890 ID の AST 走査（assert / assertX / raises の有無と件数）、
group 別の検証密度、`responsive` 判定の 3 条件（`run_phase5_local_smoke.py:2123-2134`）、
`semantic_requirements_met` の導出、`phase6_fixture` による gate 分岐、
`evidence_ref_for_record` を import している箇所 20 件の用途。

**検証していない**: pytest の実行、A15 の 579 件の個別内容（件数と検証密度のみ確認）、
A04 / A07 / A10 の全 test 本体（サンプルのみ）、
production 実装の正しさ（test が何を検証しているかのみを見た）、
`_case()` が構築する合成 population の妥当性、Stage B。

---

# R09 — Stage B 失敗の根本原因特定

日付: 2026-09-15
対象: `T307_MAIN_RESULT.md` / `T307_STAGE_B_EXECUTION.md` / `T307_STAGE_B_PREFLIGHT.md`、
`T308_STAGE_B_REVIEW.md`、`ai_client/llm/config.py`、`ai_client/llm/admission_types.py`、
`scripts/run_phase5_local_smoke.py`、`PHASE6_OUTPUT_BUDGET_CLARIFICATION.md`、
`PHASE6_STAGE_B_DETAILED_DESIGN.md`
判定: **根本原因は特定できる。モデルの遅さでも通信でもなく、設定の算術的矛盾である**

## R09-1. 結論

**`read_timeout_seconds = 3.5` / `request_timeout_seconds = 4.0` のまま
`max_output_tokens = 512` で実 9B に投げたため、最初の provider call が必ず timeout した。**

T307 は RC-01 を「モデルの遅さ、起動条件、通信等のどれが根因かは未確定」としているが、
**未確定ではない。** 設定値が確定しており、512 token の生成が 3.5 秒で終わることはない。

## R09-2. 証明

### (1) timeout の実値

```
# ai_client/llm/config.py:25-30
connect_timeout_seconds: float = 1.0
read_timeout_seconds:    float = 3.5     ←
write_timeout_seconds:   float = 1.0
pool_timeout_seconds:    float = 1.0
request_timeout_seconds: float = 4.0     ←
```

### (2) これらは CLI / 環境変数から変更できない

runner が受け付ける環境変数は `AIWOLF_LLM_ENDPOINT` / `AIWOLF_LLM_MODEL` /
`AIWOLF_LLM_API_KEY` の 3 つだけ（`run_phase5_local_smoke.py:88-90, 748-750`）。
timeout を渡す CLI 引数も環境変数も存在しない。
`_broker_bootstrap` は `config.settings.*_timeout_seconds` をそのまま子へ伝播し、
子側は既定 1.0 / 3.5 / 4.0 で復元する（同 2547-2551, 2988-2992）。
**Phase 6 用の上書きは無い。**

### (3) T307 が実際にこの値で走ったことの証明

backend config fingerprint は timeout を含む。実測で確認した。

```
既定 (read=3.5)  → 44d217baa5e3e010b13b91c25c04dceaaf24302c0cab99305c3d4827646d3d7c
read=60.0 に変更 → 19b03679538cc6eb28113e32318a470b276afa87cdca1acc7159c92c577ca6c9
```

そして `T307_STAGE_B_PREFLIGHT.md:57` はこう記録している。

> Phase 6 runner が要求する静的 backend config fingerprint は
> `44d217baa5e3e010b13b91c25c04dceaaf24302c0cab99305c3d4827646d3d7c` と算出した

**完全一致する。** したがって T307 は `max_output_tokens=512` かつ `read_timeout=3.5s` /
`request_timeout=4.0s` で実行された。

### (4) 観測された事象がこの説明と整合する

`T307_STAGE_B_EXECUTION.md` の admission event:

```
ENQUEUED = 9              9 client が最初の生成要求を投入
OFFERED  = 1              concurrency 1 なので 1 件だけ provider へ
PROVIDER_CALL_TERMINAL = 1
first provider backend code = REQUEST_TIMEOUT      ←
ADMISSION_POISONED = 1
```

そして開始 marker `14:55:13Z` → summary `14:56:17Z` = **64 秒**。
readiness 待ち + 1 回の 4 秒 timeout + admission poisoning + 停止処理の合計として整合する。
「実 game が 1200 秒走って会話が成立しなかった」のではなく、
**最初の 1 回の生成で止まった。** accepted text 0、manifest 0、9 shard 中非空 1 はその帰結である。

## R09-3. Phase 5 との対比 — なぜ今まで通っていたか

| | Phase 5（T154、成功） | Phase 6（T307、失敗） |
|---|---|---|
| `max_output_tokens` | **96** | **512**（5.3 倍） |
| `read_timeout_seconds` | **3.5** | **3.5**（不変） |
| `request_timeout_seconds` | **4.0** | **4.0**（不変） |
| provider call | 128 回すべて成立 | 1 回目で REQUEST_TIMEOUT |
| 観測出力 | 14〜18 文字 | 生成に到達せず |

Phase 5 は 96 token 上限だったため 3.5 秒に収まっていた。
Phase 6 が必要とする応答長は T297 が実測している。

```
rebuttal            296–482 token
opinion_change      296–441
relation_hypothesis 318–463
answer              313–477
nine_seat_pre_vote  368–510
```

**Phase 5 で成立していた生成の 3〜5 倍の長さを、同じ 3.5 秒の枠に入れようとした。**
モデルが何 token/秒であっても成立しない。

## R09-4. なぜ見落とされたか（構造的理由）

3 つの task が別々の層を見て、**接続する層を誰も見なかった。**

| task | 測ったもの | 見なかったもの |
|---|---|---|
| T283 / T297 | token **数**（78–510） | 生成に要する **時間** |
| T305 Stage B 設計 | **外側** wall clock（runner 1200s、outer 1500s、readiness 30s、status 60s、broker 15s、cleanup grace 120s） | **1 request あたりの HTTP read/request timeout** |
| T246 予算 clarification | 上限値の選択 | 同上 |

決定的なのは T246 の文言である。

> Preserve ... **existing finite timeouts/byte limits**.
> No schema abbreviation, ..., extra call, **timeout increase**, alternate model, fallback,
> new retry, game rerun, or soak is part of this route.

**「timeout を上げること」を route から明示的に除外した。** T246 の文脈
（失敗を timeout 延長で糊塗しない）では正しい判断だが、その後 token 上限が 96 → 512 に
上がったときに、この除外を再検討する task が存在しなかった。

T305 の Stage B 設計は wall clock を非常に丁寧に扱っている
（`_run_game` の readiness 各 30 秒、server result 待ち `max_seconds`、status 60 秒、
broker 15 秒、cleanup grace を列挙し、`TBD-BEFORE-LAUNCH` の間は起動不可とまで書いている）。
**それでも `read_timeout_seconds` は一度も現れない。** 監視の粒度が外側だけだった。

本 session の過去指摘も同じ層を外していた。R02 / R04-G1 /
`PHASE6_REDIRECTION_INSTRUCTION` §2.1 で「出力が 5〜10 倍になれば queue 待ちも比例して伸びる」
「まず 1 回回して queue 待ちと deadline 抑制の実測を見る」と書いたが、
**見るべきは queue 待ちより 1 段下の HTTP read timeout だった。**

## R09-5. 修正案

### L1【必須】Phase 6 用の provider timeout を実測に基づいて設定する

**推測で値を決めない。** 以下が最小手順である。

1. **1 回の生成の実時間を測る。** provider（PID 3260、`127.0.0.1:8080`）は稼働中である。
   game を起動せず、9 client も起動せず、`/v1/chat/completions` へ
   代表的な Phase 6 prompt を 1 回投げて `max_tokens=512` の wall 時間を測る。
   **これは game ではないので D073 の「実 LLM 2 回」予算を消費しない**（扱いは Main/ユーザー判断）。
2. 実測値から `read_timeout_seconds` / `request_timeout_seconds` を決める。
3. **同時に `provider_drain_grace_seconds` を上げる必要がある。**
   `admission_broker.py:295-297` が
   `provider_drain_grace_seconds < backend_request_timeout_seconds` を拒否する。
   既定は `5.0`（`admission_types.py:163`）なので、request timeout を 4.0 より上げると
   **この検証で起動前に失敗する。**
4. Phase 5 / Q8 の既存 96 token profile は現在値のまま保持する
   （`ShortChatConfig` と同じ扱い。新 profile を足す形）。

**この 3 値（read / request / drain grace）は連動している。**
1 つだけ上げると broker 構築時に例外になる。

### L2【必須】Stage B の test case に per-request timeout の観測を入れる

B10 は `token usage` と `timing` を取る項目だが、今回どちらも欠測した。
**最初の 1 call が timeout した時点で観測対象が生まれなかった**ためである。

起動前 checklist に「1 回の生成 wall 時間 < `read_timeout_seconds`」を
**実測で確認する項目**を入れる。新 test の増設ではなく preflight の 1 行でよい。

### L3【判断】D072 の「プレイが成立するまで増やす」の対象に timeout を含めるか

ユーザー指示「トークン数、昼、夜の時間はプレイが成立する方を優先。成立するまで増やして」に
**timeout は明示的に含まれていない。** しかし token を増やす以上、timeout は従属変数である。

`PHASE6_REDIRECTION_INSTRUCTION` §2.1 の表は 4 つの制限（chars / bytes / text token /
whole-response token）を挙げたが、**timeout を 5 番目として挙げていなかった。**
これは本 session の不足である。次の指示文では

> 出力上限を上げるときは、provider の `read_timeout_seconds` /
> `request_timeout_seconds` / `provider_drain_grace_seconds` を実測に基づいて連動させる

を明記すべきである。

## R09-6. 付随して確認した事項

- **raw は保全されている。** 61 file、主要 5 artifact の hash 不一致 0、
  T308 が同一原本を独立審査済み。空 aggregate を原本消失と混同していない。
- **product は変更されていない。** source 191 件中 190 一致、既知 control 追記 1、未許可差分 0。
- **cleanup は成立している。** runner 所有 11 child、実行後 alive 0。
  provider PID 3260 はユーザー所有として一切操作していない。
- **retry していない。** launch 1 / retry 0。失敗を隠した再実行はない。
- **RC-02（親 runner PID / shell exit code が UNKNOWN、開始共有の遅延）は未解消のまま保持。**
  今回の根本原因とは別の観測記録上の不足である。
- T308 は独立審査で PASS 0 / FAIL 6 / BLOCKED 5、mandatory FAIL と判定し、
  processor は前提不成立のため未起動、偽の checklist や成功 aggregate を作っていない。**適切である。**

## R09-7. 意味づけ

**今回の失敗は「会話品質の失敗」ではない。** モデルは一度も応答を生成していない。
したがって Phase 6 の完了条件 2 / 3（前の発言を受けた発言、質問応答・反論・意見変更）は
**まだ一度も試されていない。** B02–B05 の FAIL は「会話が成立しなかった」ではなく
「会話が始まる前に infra が止まった」である。

裏を返せば、**L1 を直せば初めて本来の試験ができる。**

### 【訂正 2026-09-15】「実 LLM 予算 2 回」は存在しない

本節の初版は「D073 の実 LLM 予算 2 回のうち 1 回を消費した」と書いたが、**誤りである。**
ユーザーはそのような回数上限を設定していない。

ユーザーが実際に承認した `T304_STAGE_B_LAUNCH_CONDITIONS.md` にあるのは次の 2 点だけである。

> T307 独立 Tester が既存 runner の Phase6 経路を **一回だけ** 起動し
> **再実行には別の明示承認が必要。**

すなわち承認されたのは「**1 回実行。追加は都度あらためて明示承認を取る**」であって、
**総回数の上限ではない。**

「2 回」は Master Plan §6（Main 起草）の記述であり、同 §6 自身が

> 1 回＋必要な既存 FAIL 修正確認 1 回は **本候補の提案であり、
> ユーザーが回数を確定承認したという意味ではない。**

と明記している。R03 disposition の F6 回答も「候補、確定承認や実起動許可ではない」としている。
**Main は一貫して候補として扱っており、確定として記述したのは本 session の誤りである。**

さらに本 session は R04-F6 で「計画側の 2 回上限とユーザー指示
『プレイが成立するまで増やして』は食い違うのでユーザーが確認すべき」と自ら書いた。
その未解決の論点を、確定事項として扱ってしまった。

**現在の正しい状態**: 実 LLM の総回数上限は未確定である。
次の実行は「残り 1 回」ではなく「新しい明示承認を要する 1 回」である。
ユーザー指示「プレイが成立するまで増やす」と Plan §6 の有限回数案の整合は、
**いまだユーザーの判断待ちである。**

## R09-8. 検証した内容と検証していない内容

**検証した**: `ai_client/llm/config.py:20-35` の timeout 既定値、
`admission_types.py:161-183` の `provider_drain_grace_seconds` と
`admission_broker.py:262-297` の連動制約、
`run_phase5_local_smoke.py` の環境変数 allowlist と `_broker_bootstrap` の timeout 伝播、
`LocalLLMSettings.backend_config().config_fingerprint` が timeout を含むことの実測、
その値と `T307_STAGE_B_PREFLIGHT.md:57` の記載の一致、
T307 実行 handoff の admission event 内訳と時刻 marker、
T307 Main 結果の 11 項目分類、T308 独立審査の結論、
`PHASE6_OUTPUT_BUDGET_CLARIFICATION.md` の timeout 記述 8 箇所、
`PHASE6_STAGE_B_DETAILED_DESIGN.md` の timeout 記述 6 箇所、
既存 handoff 群に `read_timeout` / `3.5` の記載が無いことの確認。

**検証していない**: 実 provider への生成要求（1 件も送っていない）、
実際の token/秒 throughput、prompt processing 時間、
T307 の private raw 本文、`REQUEST_TIMEOUT` が read timeout と request timeout の
どちらで発火したかの raw 上の区別、`ADMISSION_POISONED` 後の broker 挙動の詳細、
RC-02 の原因。

---

# R10 — 2 回目 Stage B（T316）のテスト結果レビュー

日付: 2026-09-15
対象: `T316_MAIN_RESULT.md` / `T316_STAGE_B_EXECUTION.md`、
`failures/2026-09-15_T316_PROVIDER_UNKNOWN.md`、`T318_STAGE_B_REVIEW.md`、
private raw `game/T316B-20260915T031500000000Z` の **metadata のみ**（下記 privacy 注記参照）
判定: **R09 の修正は効いた。今回は infra ではなく製品の失敗であり、原因は 3 つに分離できる**

## privacy 注記

本節の分析は生成記録の **metadata field のみ** を読んだ。
`status` / `attempt_ordinal` / `finish_reason` / `validation_code` / `backend_error_code` /
`prompt_bytes` / `response_bytes` / `completion_tokens` / `latency_microseconds` と、
prompt JSON の **key 名と byte 数だけ**である。
`prompt_json` の本文、`response_text`、`decision` の text、原文は一切読んでいない。
全原文審査は T318 / 後続 private Reviewer の責務であり、本 session はそれを代替しない。

## R10-1. R09 の修正は効いた

`read`/`request` timeout を 3.5s/4.0s → **20 秒**に変更して実行された。

| 指標 | T307（修正前） | T316（修正後） |
|---|---|---|
| wall | 64 秒 | **305.6 秒** |
| provider call terminal | 1 | **35** |
| `REQUEST_TIMEOUT` | 1 件（最初の call） | **0 件** |
| generation record | 実質 0 | **61** |
| モデルの応答 | **0 回** | **34 回** |

生成 latency の実測（61 record）は **min 0 / median 3.3 秒 / max 8.6 秒**。

**旧 3.5 秒の read timeout に対して median が 3.3 秒、max が 8.6 秒である。**
R09 の診断（512 token 化で 3.5 秒に収まらなくなった）は、この実測で定量的に裏付けられた。
20 秒は現在の観測範囲（max 8.6 秒）に対して十分である。

## R10-2. 完全な内訳（会計が閉じる）

```
attempt_ordinal = 1 （初回生成）
  DECISION          8
  OUTPUT_INVALID   26
  BACKEND_FAILED    1
                 ---- 35  = provider call terminal 35 件と一致

attempt_ordinal = 2 （repair）
  PROMPT_REJECTED  16
  BACKEND_FAILED   10
                 ---- 26  = OUTPUT_INVALID 26 件と一致

REPAIR_SUCCEEDED    0
```

- 初回 35 call のうち **valid は 8 件（22.9%）**
- OUTPUT_INVALID 26 件すべてが repair を起動し、**26 件すべて失敗した**
- `REPAIR_SUCCEEDED` は **0 件**

`terminal` 側も一致する: `ACCEPTED/AUTHORITATIVE_ACCEPTED` 8、
`ABORTED/PROMPT_REJECTED` 16、`ABORTED/BACKEND_FAILED` 11 = 35。

## R10-3. 512 token は今回の原因ではない

| 指標 | 実測 |
|---|---|
| `finish_reason` | **`stop` 34 件 / `None` 27 件。`length` は 0 件** |
| `completion_tokens` | n=34、min **110** / median **151** / max **385** |
| `response_bytes` | min 390 / median 534 / max 1,264 |

**1 件も length 打ち切りになっていない。** 最大でも 385 token で、512 の上限には届いていない。
R04-G1 で懸念し T297 が計測で否定した「512 不足」は、実 game でも発生しなかった。
**512 / 200 文字 / 600 bytes の選択は、今回の失敗とは無関係である。**

## R10-4. 失敗は 3 つに分離できる

### F-A【製品・最重要】モデルが closed schema に従えない — 34 件中 26 件（76%）が invalid

`validation_code` の内訳は **`SCHEMA` 13 件 / `VALUE_NOT_OFFERED` 13 件**。

- `SCHEMA` 13 — 出力が `additionalProperties:false` の閉じた schema に適合しなかった
- `VALUE_NOT_OFFERED` 13 — **提示されていない値・handle を選んだ**
  （offered option 外の投票先や、提示されていない evidence handle の引用）

これは通信でも timeout でもモデルの遅さでもない。
**Qwen3.5-9B-Q4_K_M が、Phase 6 の strict semantic 契約を 4 回に 1 回しか満たせていない。**

これが Phase 6 が本来知りたかったことの最初の実測である。R08-K6 で
「完了条件 3 は Stage B 依存度が最も高く Stage A の green から何も推定できない」と書いたが、
**その Stage B が、会話の質以前に schema 適合率で止まった。**

`VALUE_NOT_OFFERED` が半数を占める点は重要である。schema の形式問題だけでなく、
**モデルが「提示された選択肢の中から選ぶ」という制約自体を守れていない。**

### F-B【製品】repair が 100% 失敗している

`max_schema_repair_attempts = 1` に従い、OUTPUT_INVALID 26 件すべてが 1 回の repair を試みた。
**26 件すべて失敗し、`REPAIR_SUCCEEDED` は 0 件である。**

内訳は `PROMPT_REJECTED` 16 / `BACKEND_FAILED`（`ADMISSION_PROTOCOL`）10。

**`PROMPT_REJECTED` 16 件は prompt サイズで完全に分離する。**

| status | n | prompt_bytes min | median | max |
|---|---:|---:|---:|---:|
| DECISION | 8 | 13,288 | 14,807 | 15,936 |
| OUTPUT_INVALID | 26 | 13,322 | 15,790 | 15,920 |
| BACKEND_FAILED | 11 | 14,441 | 14,801 | 15,610 |
| **PROMPT_REJECTED** | **16** | **16,030** | **16,288** | **16,340** |

**通過した記録の最大は 15,936、拒否された記録の最小は 16,030。重なりが 1 件も無い。**
閾値は 15,936〜16,030 の間にある。

repair prompt は初回 prompt に 3 つ目の message（367 bytes）を足した構造で
（`roles = [system, user, user]`、3 つ目の user JSON の key は `repair` のみ）、
**その加算で閾値を越える。** 初回 prompt はゲーム進行とともに memory が積み上がり
13.3 KB → 15.9 KB と増えるため、**ゲームが進むほど repair が必ず失敗する構造**になっている。

**ここに未解決の矛盾がある。** `LLMBrainConfig.max_prompt_bytes` の既定は **32,768** で、
runner は `LLMBrainConfig(short_chat=DiscussionChatConfig() if phase6 else ShortChatConfig())`
としか構築しておらず、**この値を下げていない**（`run_phase5_local_smoke.py:2823-2825`）。
にもかかわらず実測の拒否閾値は約 16 KB である。
経路上 16 KiB 規模の唯一の bound は
`ai_client/discussion/projection.py:43` の `max_memory_section_bytes = 16 * 1024` である。

**本 session はこの不一致の機構を確定できていない。**
実測の閾値（15,936〜16,030）と設定値（32,768）が合わないという事実だけを報告する。
**Repair Plan の最初の 1 手はこの閾値の所在を特定することである。**

### F-C【設計】collector が失敗 status を受け付けず、証拠が一切生成されない

```python
# scripts/run_phase5_local_smoke.py:2048
if record.get("player_id") != player or record.get("status") not in {
        "DECISION", "EXPLICIT_NO_DECISION", "REPAIR_SUCCEEDED", "CANCELLED"}:
    raise ValueError("semantic generation status or identity invalid")

# :2055
if record["backend_error_code"] is not None or record["validation_code"] is not None:
    raise ValueError("generation contains schema or backend failure")
```

collector が受け付ける status は **4 つだけ**である。
今回発生した `OUTPUT_INVALID` / `PROMPT_REJECTED` / `BACKEND_FAILED` は**すべて拒否される。**
さらに `validation_code` か `backend_error_code` が非 null であるだけで例外になる。

**collector は「どの生成も失敗しない」という前提で書かれている。**
その前提は合成 fixture（`Phase6SemanticBackend` は失敗しない）と
Phase 5（短文・128 call 全成功）でのみ成立する。**実モデルでは成立しない。**

結果として、**accepted text が 8 件実在するのに manifest も accepted-text ledger も生成されず**、
B02 / B07 / B08 / B09 / B11 の 5 項目が BLOCKED になった。
P6-J（全 accepted text の原文審査）も前提不成立で起動できない。

**これは tuning ではなく設計上の欠陥である。**
実 game で 1 件でも生成が失敗すれば、成功した分の証拠まで失われる。

## R10-5. その他の観測

### B06 — accepted vote が 0 件

expected vote 9 件に対し **accepted vote 0 件**。有効な pre-vote 再評価も 0 件。
F-A の 76% invalid と `VALUE_NOT_OFFERED` 13 件を踏まえると、
**投票が一度も成立しなかった**ことになる。ゲームとして進行していない。

### 1 件の UNKNOWN が clean shutdown を壊した

35 provider terminal のうち 34 PROVEN / **1 UNKNOWN**。
最後の backend code は `ADMISSION_POISONED`。この 1 件が
`PROVIDER_QUIESCENCE_UNKNOWN` → `shutdown_clean=false` → B01 FAIL を引き起こした。
**owned cleanup 自体は 11/alive 0 で成立している。**

### `PROMPT_REJECTED` は拒否理由を記録していない

16 件の `PROMPT_REJECTED` 記録は `validation_code` も `backend_error_code` も null で、
`prompt_json` にも `projection_rejected` code を含まない。
非空 field は identity と prompt 関連のみである。

**R01-P0-1 で指摘した「43 箇所が `corrupt` 1 語に collapse する」のと同じ構造が、
今度は製品の audit record 側にある。** 拒否理由が記録されないため、
上記 F-B の閾値特定が raw からできない。

## R10-6. 指摘

| # | 内容 | 深刻度 | 時期 |
|---|---|---|---|
| M1 | collector が失敗 status を拒否し、成功分の証拠も失われる | **HIGH** | Repair Plan で最優先 |
| M2 | repair prompt が閾値を越えて 100% 失敗。閾値の所在が未特定 | **HIGH** | 同上 |
| M3 | モデルの schema 適合率 24%。うち半分は `VALUE_NOT_OFFERED` | **HIGH（製品判断）** | 同上 |
| M4 | `PROMPT_REJECTED` が拒否理由を audit に残さない | MEDIUM | M2 の前提 |
| M5 | 512 token / 200 文字 / 600 bytes は原因ではない。変更しない | — | 情報 |

### M1 の最小修正案

collector が失敗 status の generation record を**拒否ではなく計上**するようにする。
`OUTPUT_INVALID` / `PROMPT_REJECTED` / `BACKEND_FAILED` を population から除外しつつ、
件数と code を machine evidence に記録し、**accepted 分の manifest は生成する。**
「失敗が 1 件でもあれば全証拠を捨てる」を「失敗を数えて記録し、成功分は残す」に変える。

acceptance を緩めるものではない。B02 以降の判定は今までどおり accepted population に対して行う。
**現状は「証拠が作れないので判定できない」であり、判定が甘くなる方向の変更ではない。**

### M3 の扱い

これは**製品判断であり、技術的な修正では解決しない可能性がある。**選択肢は概ね 3 つ。

1. schema を緩める（`additionalProperties:false` の維持範囲、offered handle の扱い）
2. prompt を変える（few-shot、schema の提示方法、offered option の明示）
3. モデルを変える（D069 / 現在の承認範囲外。ユーザー判断）

**どれもユーザーの product 決定を要する。** Main が単独で選ぶべきではない。
なお 1 は「product/acceptance semantics を弱めない」という既存条件に触れる可能性がある。

## R10-7. 結論

**今回の失敗は infra ではなく製品の失敗である。** これは前進である。
T307 はモデルが一度も喋らなかった。T316 は **34 回喋り、8 回だけ契約を満たした。**

Phase 6 が答えを出したかった問い「前の発言を受けた会話が成立するか」には、まだ到達していない。
**その手前の「モデルが所定の形式で応答できるか」で 24% に留まっている。**

そして F-C により、**成功した 8 件すら証拠として取り出せていない。**
M1 を直せば、次の実行で「8 件が何だったのか」を初めて読めるようになる。

3 つの失敗は独立しており、**M1 と M2 は実装の問題、M3 は製品の問題**である。
M1・M2 を直さずに再実行しても、M3 の実態は今回以上には分からない。
逆に M1・M2 を直せば、**再実行しなくても今回の 61 record から M3 の詳細**
（どの schema 要素で落ちたか、どの handle が offered 外だったか）**を読める可能性がある。**
Repair Plan では「再実行の前に、保存済み raw から M3 を静的に分析する」を先に置くべきである。

## R10-8. 検証した内容と検証していない内容

**検証した**: `run_phase5_local_smoke.py:2040-2060` の collector status gate、
`ai_client/discussion/model.py:1118-1127` の `AiDiscussionGenerationStatus` 全 8 値、
`ai_client/llm/prompt.py:442/563-564/598-612` の拒否経路、
`ai_client/llm/types.py:537` の `max_prompt_bytes=32768`、
`run_phase5_local_smoke.py:2823-2825` の brain config 構築、
`ai_client/discussion/projection.py:43` の `max_memory_section_bytes`、
private raw の generation record 61 件 / terminal record の **metadata のみ**
（status、attempt_ordinal、finish_reason、validation_code、backend_error_code、
prompt_bytes、response_bytes、completion_tokens、latency、prompt JSON の key 名と byte 数）、
T316 handoff の B01–B11 分類と B10 の token subtotal、failure record、T318 の結論。

**検証していない**: 原文（`prompt_json` 本文、`response_text`、`decision` text）、
実 provider への追加要求、約 16 KB 拒否閾値の機構の確定、
`ADMISSION_PROTOCOL` 10 件の原因、1 件の UNKNOWN terminal の原因、
T318 が見た private 判定内容、`SCHEMA` 13 件がどの schema 要素で落ちたかの内訳、
`VALUE_NOT_OFFERED` 13 件の具体的な値。

---

# R11 — Stage B テストケース内容のレビュー

日付: 2026-09-15
対象: `Docs/ai/PHASE6_STAGE_B_TEST_CASES.csv`（B01–B11 の 16 列）と同 `.md`、
`PHASE6_STAGE_B_DETAILED_DESIGN.md`、
R10 修正に伴う test 内容の変化（T320 調査 → T321 設計 → T322 独立 review → T323 独立試験）
判定: **B01–B11 の oracle 設計は質が高い。ただし今回の失敗を支配した変数を観測する項目が 1 つも無い**

R08 は Stage A（890 ID）の内容を見た。本節は Stage B（11 項目）の内容を見る。

## R11-1. 良かった点 — negative_boundary が本物の anti-gaming 条項になっている

CSV は各項目に `pass_oracle` / `fail_oracle` / `blocked_oracle` / **`negative_boundary`** を持つ。
この 4 列目が効いている。**「どうすれば安易に PASS できてしまうか」を先回りして塞いでいる。**

| 項目 | negative_boundary（抜粋） |
|---|---|
| B02 | self / 後続 / 無許可 private / **ラベルだけ**は不合格 |
| B03 | **疑問符だけで決めない**。言換え / 回避は FAIL |
| B04 | 呼名 / 同意 / 別話題は FAIL。一部への根拠付き反証は PASS |
| B05 | **根拠追加のみで結論不変は FAIL** |
| B07 | credential / private 結果の転載・言換えは FAIL。**自発 role claim / 合法偽 CO だけでは FAIL にしない** |
| B09 | 全 game 2 回は閾値上可。**同一 player 直前なら 2 回目で FAIL** |
| B11 | **総 game token を 512 と比較しない** |

- B03 の「疑問符だけで決めない」は、`?` の有無で質問応答を数える安易な実装を明示的に否定している
- B05 の「根拠追加のみで結論不変は FAIL」は、意見変更を名乗るだけの出力を弾く
- B07 が**合法的な騙り（偽 CO）を privacy 違反と混同しない**と明記しているのは、
  人狼というゲームの性質を正しく踏まえている。ここを誤ると正常なプレイが FAIL になる
- B11 の「総 game token を 512 と比較しない」は T289 が指摘した単位混同を先回りして塞いでいる

また全項目が `real_llm_runs = 共通1回` で、**同一 game から 11 項目を評価する**構成を保っている
（R03 で評価した設計がそのまま維持されている）。
`exact1はPASS` が繰り返し書かれており、**閾値が 1 件であることを隠していない**点も誠実である。

B10 の blocker が `YES_IF_MISSING_NO_IF_PERFORMANCE_ONLY` で、
**計測の欠落は blocker、性能値そのものは non-blocker** と分離しているのも正しい。

## R11-2. 指摘

| # | 内容 | 深刻度 |
|---|---|---|
| N1 | generation の妥当性率を観測する項目が 1 つも無い | **HIGH** |
| N2 | B02–B05 が manifest 存在に全面依存し、degraded mode が無い | MEDIUM |
| N3 | `blocked_oracle` の「既知 FAIL なしで」が多義的 | LOW |

### N1【HIGH】今回の失敗を支配した変数が、どの項目の観測対象にもなっていない

全 11 項目の `artifacts_fields` を走査した結果、
**`status` / `validation_code` / `OUTPUT_INVALID` / `PROMPT_REJECTED` / 妥当性率を
観測対象に含む項目は 1 つも無い。**

| 項目 | artifacts_fields の主対象 |
|---|---|
| B01 | `game_end` / `process_topology` / `cleanup` / `shutdown_clean` |
| B02–B05 | `semantic_counts` / `proposal` / `memory.records` / `decision.text` / `checklist` |
| B06 | `pre_vote_reassessment_count` / accepted vote |
| B07 | `prompt.context` / `terminal visibility` / `privacy_safe` |
| B08 | `prompt.memory.*` / `prompt_bytes` / 各上限 |
| B09 | `non_repetitive` / `normalization_duplicate_count` |
| B10 | `PROVIDER_CALL_TERMINAL.*` / token / latency / queue / day |
| B11 | `config_fingerprint` / `attempt_ordinal` / `finish_reason` |

B11 が `finish_reason` と `attempt_ordinal` に触れるのが最も近いが、
その `pass_oracle` は「request cap 512 binding；length 不採用；repair ≤1；accepted ≤512」であり、
**「何件が妥当だったか」は判定条件に入っていない。**

T316 の実測は R10 のとおりである。

```
初回 35 call → DECISION 8 / OUTPUT_INVALID 26 / BACKEND_FAILED 1
妥当性率 8/34 = 23.5%
repair 26 件 → REPAIR_SUCCEEDED 0
```

**この 23.5% という数字は、B01–B11 のどの PASS/FAIL 条件にも現れない。**
B01（game_end=false）と B06（accepted vote 0）に**間接的な影響として**表れるだけで、
「モデルが契約形式で応答できた割合」という一次量は、
Stage B の設計上いかなる項目でも測られていない。

設計は「モデルは所定の形式で応答できる」を暗黙の前提に置き、
その上で会話の質（応答性・質問応答・反論・意見変更）を測る構成になっている。
**前提が崩れたとき、それを記録する場所が無い。**

これは R08-K6 で書いた
「完了条件 3 は Stage B 依存度が最も高く、Stage A の green から何も推定できない」の続きである。
Stage B に到達したが、**その手前の層が測定対象外だった。**

**最小修正案**: B01–B11 を増やさず、**B10 の `artifacts_fields` に
generation status の内訳（`status` 別件数、`validation_code` 別件数、
`attempt_ordinal` 別件数）を追加する。** B10 は既に
「計測欠落は blocker、値そのものは non-blocker」という正しい blocker 定義を持っているので、
**妥当性率をこの枠に入れれば、値が悪いことで FAIL にはならず、
測れないことだけが blocker になる。** 意味論を変えずに観測だけ増やせる。

FREEZE 済み Plan への項目追加ではなく **既存項目の観測フィールド追加**なので、
「FREEZE 後に Test ID を増やさない」規律にも触れない。

### N2【MEDIUM】B02–B05 が manifest 存在に全面依存している

5 項目すべての `blocked_oracle` が「必要原文・相関・state の欠落」で BLOCKED になる。
T316 では accepted text が **8 件実在した**にもかかわらず、
collector が manifest を生成しなかったため 5 項目とも BLOCKED になった。

**8 件を直接読んで「responsive が 1 件でもあるか」を見る degraded 経路が無い。**
manifest は相関の完全性を保証する仕組みであって、
「1 件でも存在するか」を答えるのに必ずしも必要ではない。

ただしこれは設計上の意図でもある。§11 は
「population は決してサンプリングしない」と定めており、
**部分的な母集団で PASS を出さない**という原則は正しい。

**最小修正案**: PASS 判定は現状維持のまま、
**BLOCKED 時に「機械的に観測できた下限値」を記録する欄を設ける。**
「responsive ≥1 を確認できなかった」と「responsive が 0 件だった」は別の情報であり、
現在はどちらも同じ BLOCKED になる。判定は変えず、記録だけ分ける。

### N3【LOW】`blocked_oracle` の「既知 FAIL なしで」が多義的

全項目の `blocked_oracle` が「**既知 FAIL なしで** 〜が欠落」という形をとる。
T316 では B01 と B06 が FAIL した状態で他 9 項目を BLOCKED としたが、
文言を「run 全体に既知 FAIL が無いこと」と読むと、この運用と矛盾する。

実際の意図は「**その項目自身に**既知 FAIL が無く、かつ証拠が欠落」であろう。
運用は妥当だが、**文言が run 単位とも項目単位とも読める。**

**最小修正案**: 「当該項目に既知 FAIL が無く」と 1 語足す。

## R11-3. R10 修正に伴う test 変化 — 適切に処理された事例として記録

T323 の独立試験で R1 batch に 3 failure が出た。
`test_private_review_accepts_exact_v1_v2_generation_linkage_and_rejects_unknown_version`
の 3 parametrize で、いずれも同じ assertion だった。

```python
rejected = _invoke(...)
assert rejected.returncode != 0
assert not (owner / "rejected.json").exists()      ← FAIL
```

R10-M1 の修正により processor が**拒否時にも redacted failure aggregate を書く**ようになったため、
「拒否時にファイルが存在しない」という旧期待と食い違った。

T322 独立 Reviewer の裁定は次のとおりで、**正しい。**

> 既存 processor の「linkage/evidence failure でも redacted failure aggregate を保存して
> 非 zero 終了する」契約と既存回帰に反する test 期待だった。**product 動作は fail-closed で正しかった。**

実装を確認した。`_process_locked` は `ReviewFailure` を捕捉して
`_write_aggregate` を実行したうえで **再送出**する。
したがって **非 zero 終了と `human_quality_pass=false` は維持されている。**
これは選択設計 §11 の
「oversize / unreadable / malformed / hash-mismatched / zero population は
fail aggregate を可能なら書き、決してサンプリングしない」と整合する。

**特筆すべきは訂正後の test 内容である。** assertion を削除したのではなく、置き換えている。

```python
assert rejected.returncode != 0
rejected_aggregate = json.loads((owner / "rejected.json").read_text(encoding="utf-8"))
assert rejected_aggregate["human_quality_pass"] is False
assert rejected_aggregate["linkage_failure_count"] == 1
assert "synthetic alpha" not in rejected_text
assert "synthetic alpha" not in rejected.stdout + rejected.stderr
```

否定 1 件が、**非 zero 終了 + aggregate 実在 + `human_quality_pass=false` +
counter の exact 値 + private sentinel 非漏洩（ファイルと stdout/stderr の両方）**の
5 件に置き換わった。**旧 assertion には無かった privacy 検査が加わっている。**

R06 で扱った test 期待の drift と同じ class だが、
今回は **fail-closed 境界に触れる変更**だったにもかかわらず、
独立 Reviewer が「product が正しい / test が誤り」を判定したうえで、
**より強い期待値に置き換える**という最良の処理がなされた。
R1-RETEST は 161 case + 140 subtest PASS、failure 0 である。

なお test の訂正を Main が行っている点は R01-M3 / R07-G5 と同じ pattern だが、
**判定した Reviewer は別 session であり、独立性は省略されていない。**

## R11-4. 結論

**B01–B11 の oracle 設計は質が高い。** 特に `negative_boundary` 列は、
各項目が安易に PASS しうる経路を具体的に塞いでおり、
人狼の合法的な騙りと privacy 違反を区別する判断も正しい。

**唯一の構造的な欠落は N1 である。**
Stage B は「モデルは所定の形式で応答できる」を前提に会話の質を測る設計で、
その前提が崩れたときに**前提の崩れ方を記録する場所が無い。**
T316 の 23.5% という数字は、どの項目の判定条件にも現れない。

B10 の観測フィールドに generation status 内訳を足すだけで解決する。
B10 の blocker 定義（欠落は blocker、値は non-blocker）がそのまま使えるため、
**acceptance を一切変えずに観測だけ増やせる。**

R10 修正に伴う test drift は適切に処理されており、指摘はない。

## R11-5. 検証した内容と検証していない内容

**検証した**: `PHASE6_STAGE_B_TEST_CASES.csv` の 11 行 × 16 列全体
（pass/fail/blocked/negative oracle、owner、blocker、runs、artifacts_fields）、
`artifacts_fields` に generation status 系の語が含まれないことの機械走査、
`logs/t319-r10-repair/test/R1.junit.xml` の 3 failure の assertion と traceback、
`tests/test_phase6_private_review.py:405-421` の訂正後 assertion、
`scripts/phase6_private_review.py:684-720` の失敗時 aggregate 書込みと再送出、
T321 設計 / T322 独立 review / T323 独立試験の handoff、
T319–T324 の board 状態。

**検証していない**: `PHASE6_STAGE_B_TEST_CASES.md` 本文 147 行の全文
（CSV と設計書を優先して読んだ）、T320 調査 handoff の全文、
R1-RETEST の JUnit 実物、修正後 processor の再実行、
B03–B05 を Reviewer がどう判定するかの具体手順、実 provider。

---

# R12 — 3 回目 Stage B（T330）のテスト結果レビュー

日付: 2026-09-15
対象: `failures/F008_STAGE_B_HTTP400_TIMEOUT.md`、`T330_STAGE_B_PREFLIGHT.md`、
`T331_STAGE_B_FRESH_REVIEW.md`、`logs/t319-r10-repair/` の修正 diff、
`ai_client/llm/backend.py`、private raw `game/T330B-...` の **metadata のみ**
判定: **R10 修正は目的を達成した。しかし T330 は T316 より後退しており、
原因は最も可能性の高い順に provider 側である**

privacy: R10 と同じく metadata field のみを読んだ。原文・`response_text` は読んでいない。

## R12-1. R10 修正は実測で目的を達成している

| R10 指摘 | 修正後の実測 |
|---|---|
| **M2** repair prompt が約 16 KB で 100% 拒否 | `prompt_bytes` max **16,058**（T316 の拒否閾値 16,030 超）で **`PROMPT_REJECTED` 0 件** |
| **M4** `PROMPT_REJECTED` が拒否理由を残さない | `prompt_rejection_code` field が追加された |
| **M1** collector が失敗 status を拒否 | generation record が `v2` になり、107 件すべてが記録された |

admission の経路も健全である。**107 件すべてが
`ENQUEUED` → `OFFERED` → `CLAIMED` → `PROVIDER_CALL_TERMINAL` → `TERMINAL` を通っている**
（各 107 件）。T316 で `OFFERED` が 1 件しか出なかった状態とは別物である。

**修正自体は効いた。**

## R12-2. しかし結果は T316 より後退した

| | T316 | T330 |
|---|---|---|
| provider call | 35 | **107** |
| モデルの応答 | **34 回** | **0 回** |
| accepted decision | **8** | **0** |
| `response_bytes` | median 954 / max 1,819 | **全件 0** |
| latency | median 3.7 s / max 8.6 s | **median 0.0 s / max 0.1 s** |
| generation status | DECISION 8 / OUTPUT_INVALID 26 / BACKEND_FAILED 11 | **BACKEND_FAILED 107（全件）** |
| terminal | ACCEPTED 8 ほか | **ABORTED 107（全件）** |
| `attempt_ordinal` | 1 が 35、2 が 26 | **全件 1**（repair に到達せず） |
| wall | 305.6 秒 | **1208.9 秒**（`GAME_TIMEOUT`） |

**107 call すべてが HTTP 400**、`backend_error_code = HTTP_STATUS` 107 件、usage 全件欠測。
server accepted chat/reservation は 0、brain call/failure は 107/107、
`vote.cast` はすべて `BRAIN_FAILED`。

T331 独立 Reviewer も同じ結論に達している。

> generation 107 件はすべて `BACKEND_FAILED`、terminal 107 件はすべて `ABORTED`、
> attempt ordinal は全件 1。response-present population は 0 で、
> **保存原本に provider response 本文や 400 の詳細理由はなく、HTTP 400 より先の根因は UNKNOWN。**

## R12-3. 400 の原因分析

### 除外できるもの

**サイズではない。** `request_bytes` は 2 回の run でほぼ同一である。

| | T316（34 件成功） | T330（107 件すべて 400） |
|---|---|---|
| `request_bytes` | min 13,375 / median 15,697 / max 16,023 | min 13,385 / median 15,893 / max 16,155 |

**最小 13,385 bytes の要求も 400 になっている。** T316 で同程度の要求は成功していた。

**処理前に拒否されている。** latency は median 0.0 秒 / max 0.1 秒。
llama-server は要求を読んだ直後に 400 を返している。生成を試みていない。

**client 側の要求構築は変更されていない。** R10 修正の diff は
`brain.py` / `types.py` / `phase6_private_review.py` / `run_phase5_local_smoke.py` と test 3 件で、
**`ai_client/llm/backend.py` は 1 行も変わっていない。**
HTTP body は `max_tokens` / `messages` / `model` / `response_format` / `stream` / `temperature`
の 6 key のみで、この構築コードも不変である。

**`transport_id` 変更は無関係である。** 修正で
`request_id` → `f"{request_id}:attempt:{attempt_ordinal}"` に変わったが、
`_request_payload` は `request_id` を body に入れない。HTTP 要求には現れない。

### 最有力の候補

**provider が別プロセスに替わっている。**

| run | provider PID | creation |
|---|---|---|
| T307 | 3260 | 2026-09-14 23:27 JST |
| T316 | 33700 | — |
| **T330** | **24380** | **2026-09-15 12:38 JST** |

T316 から T330 の間に llama-server が再起動されている。
T307 の preflight は起動条件を
「GPU layers 99、context 8192、**jinja 有効**、環境変数による thinking 無効」と記録していた。
**T330 の preflight は `context 8192` / `reasoning none` / `build b10697-093adb242` までは
記録しているが、起動引数（`--jinja` の有無など）を記録していない。**

client 側が不変で、同一 build・同一 model・同程度の要求サイズで、
**片方は 34 件応答し、もう片方は 107 件即 400** である以上、
差分は provider の起動構成にあると考えるのが最も自然である。

`response_format: {"type":"json_schema", "json_schema":{..., "strict":true}}` は
サーバ側の対応状況に依存する経路であり、起動フラグの違いで 400 になり得る。
**ただしこれは推定であり、確定には provider 側のログか 400 の本文が要る。**

F008 が「HTTP400 理由が保存済み原本で判明しない場合、
ユーザー所有 LLM の該当時刻の理由行を確認する」としているのは正しい対処である。

## R12-4. 指摘

| # | 内容 | 深刻度 |
|---|---|---|
| P1 | HTTP エラー本文を破棄しており、107 件の同一失敗から理由が 1 つも得られない | **HIGH** |
| P2 | preflight が provider の起動引数を記録していない | **MEDIUM** |
| P3 | 同一失敗を 107 回繰り返して 1200 秒を使い切る。早期停止が無い | MEDIUM |

### P1【HIGH】同じ「理由を捨てる」パターンの 3 度目

```python
# ai_client/llm/backend.py:351-356
if not 200 <= response.status_code <= 299:
    raise _backend_error(
        LLMBackendErrorCode.HTTP_STATUS,
        http_status=response.status_code,
        retryable=_is_retryable_status(response.status_code),
        provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
    )
```

**非 2xx のとき、response body を読まずに捨てている。**
llama.cpp は 400 に対して
`{"error":{"code":400,"message":"...","type":"invalid_request_error"}}` 形式の本文を返すが、
その `message` が保存されない。結果として **107 件の同一失敗サンプルから
診断情報が 1 bit も得られなかった。**

これは同じパターンの 3 度目である。

1. R01-P0-1 — `phase6_private_review.py` の 43 箇所が `"corrupt"` 1 語に collapse（修正済み）
2. R10-M4 — `PROMPT_REJECTED` が拒否理由を記録しない（R10 修正で `prompt_rejection_code` 追加）
3. **R12-P1 — HTTP エラー本文を破棄**

**最小修正案**: 非 2xx のとき body の先頭を有限長（例 512 bytes）だけ読み、
`backend_error_detail` として audit へ残す。
privacy の懸念はあるが、**この codebase には既に redaction 機構がある**し、
llama.cpp のエラーメッセージは schema/パラメータに関するもので原文を含まないのが通常である。
心配なら「private sentinel を含む場合は捨てる」検査を足せばよい。

**これを直さない限り、次の run でも同じ 400 が出たときに同じく何も分からない。**
P1 は次の実行より前に直す価値がある。

### P2【MEDIUM】provider の起動引数が preflight の記録対象に入っていない

T330 preflight は PID / creation / listener / health / model basename / context / reasoning /
build / GPU / GGUF hash / serving files 35 件の hash まで丁寧に記録している。
**しかし起動引数そのものを記録していない。**

T307 のときは「jinja 有効」がユーザー申告として本文に書かれていたが、
T330 ではその記述が無い。したがって **T316 と T330 で provider の構成が同じかどうかを、
保存された preflight だけからは判定できない。**

serving files の hash が同じでも、**起動フラグが違えば別の挙動になる。**
identity 照合が実行ファイルの同一性に集中し、実行構成が抜けている。

**最小修正案**: preflight の記録項目に provider process の command line を加える
（Windows なら `Get-CimInstance Win32_Process` の `CommandLine`）。
API key 等が含まれ得るので既存の redaction を通す。
provider は所有外だが、**読取りは操作ではない。**

### P3【MEDIUM】同一失敗 107 回で 1200 秒を使い切っている

最初の call が 400 を返した時点で、その後 106 回も同じ 400 を受け続け、
`GAME_TIMEOUT` まで 1208.9 秒走った。
`retryable` field は記録されているが、**同一 `backend_error_code` + 同一 `http_status` の
連続失敗で早期に停止する仕組みが無い。**

T316 では 1 件の失敗で `ADMISSION_POISONED` に至っているのに対し、
T330 では poison に至らず 107 回続いた。この差自体も説明が要る。

実害は 2 つある。

- **承認済みの一回の実行が、ほぼ情報ゼロで消費された。** 20 分走って得たのは
  「400 が 107 回」だけで、1 回目と 107 回目に情報差は無い
- 1200 秒のうち実質的な進行は 0 で、game timeout が本来の意味（進行が遅い）を持たない

**最小修正案**: 「同一 `(backend_error_code, http_status)` が N 回連続したら
安全停止して原本を保全する」を入れる。N は 3〜5 で十分である。
acceptance を変えるものではなく、**失敗時の時間と承認枠の浪費を防ぐ**変更である。
早期停止は「失敗を隠す」方向ではなく、**同じ失敗の反復を止める**方向である。

## R12-5. 結論

**R10 修正は効いた。** M2（16 KB 拒否）と M4（理由未記録）は実測で解消し、
admission 経路も 107 件すべてが正常に流れた。**修正自体に問題は見つからない。**

**T330 の失敗は R10 修正の副作用ではない可能性が高い。**
client 側の要求構築コードは不変で、要求サイズも T316 とほぼ同一、
`request_id` は HTTP body に入らない。一方で **provider は別プロセスに替わっている。**

したがって次の一手は再実行ではなく、**400 の理由を得ること**である。順序は次を推奨する。

1. **P1 を直す**（HTTP エラー本文の有限長保存）。これ無しで再実行しても同じ結果になる
2. **provider 側のログで該当時刻の 400 理由行を確認する**（F008 の指示どおり）
3. **P2 を直す**（起動引数の記録）。T316 と T330 の provider 構成差を確定させる
4. その後に再実行の承認可否を判断する

**なお Phase 6 の製品的な最大の未解決は R10-F-A のまま変わっていない。**
T316 で観測された「schema 適合率 23.5%、`VALUE_NOT_OFFERED` 13 件」は、
T330 では応答が 0 件だったため**追加情報が何も得られていない。**
T330 は R10-F-A の理解を 1 ミリも前進させていない。

## R12-6. 検証した内容と検証していない内容

**検証した**: T330 private raw の admission event 内訳（107 × 5 種）、
`http_status` 全 107 件、`PROVIDER_CALL_TERMINAL` の非空 field 一覧、
generation record の schema version / status / finish_reason / validation_code /
backend_error_code / prompt_rejection_code / prompt_bytes、
T316 と T330 の `request_bytes` / `response_bytes` / latency の分布比較、
`logs/t319-r10-repair/final-scoped.diff` の変更ファイルと hunk 一覧、
`brain.py.diff` の `transport_id` 変更、
`ai_client/llm/backend.py` の `_request_payload` の body 6 key と
`:351-356` の非 2xx 処理、T330 preflight の provider identity 記録項目、
T331 独立 review の B01–B05 判定と根拠、F008。

**検証していない**: 原文（`prompt_json` / `response_text`）、
provider 側のログ、T316 と T330 の provider 起動引数の実差分、
400 の実際の理由、`response_format.json_schema` が当該 build で拒否されるかの実験、
T330 で `ADMISSION_POISONED` に至らなかった理由、
B06–B11 の T331 判定本文（B01–B05 のみ確認）。

---

# R13 — P1/P2 診断保全実装（T332–T336）のレビュー

日付: 2026-09-15
対象: `Docs/ai/design/PHASE6_R12_DIAGNOSTIC_DESIGN.md`、
`Docs/ai/design/PHASE6_R12_DIAGNOSTIC_P2_ASYNC_SUBPROCESS_ADDENDUM.md`、
T336 実装差分（製品 5 file / test 6 file）、T333 独立 review（R5/R6）、T334 独立試験、
`Docs/ai/failures/2026-09-15_DIAGNOSTIC_REGRESSION_CLAIM_TIMEOUT.md`
判定: **P1/P2 実装は良質で承認に値する。
全体回帰の唯一の FAIL は T336 と無関係であることを A/B 実測で確定した。
ただし P2 は production の呼出し元が無く、運用上はまだ稼働していない**

本レビューでは repository を一切変更していない。
実測は `logs/t332-review/source-before-files/` の実装前 tree と現 tree を
scratchpad へ複製し、そこで pytest を実行した。

## R13-0. 先に R12 の訂正

R12-2 の比較表で `response_bytes` を
「T316 median 954 / max 1,819、T330 **全件 0**」と書き、
これを「モデルの応答が 0 回」の根拠の一つとして並べた。

`ai_client/llm/admission_broker.py:1446-1450` を読むと、この値は

```python
response_bytes=(
    _structured_response_size(response)
    if response is not None
    else 0
),
```

であり、**error path では常に 0 が入る固定値**である。
T330 の「全件 0」は測定結果ではなく、`response is None` の副作用にすぎない。

結論（応答 0 件、全件 HTTP 400）は `backend_error_code` / `terminal` /
`finish_reason` / usage 欠測から独立に導けるため変わらない。
しかし **「provider が空 body を返した」という含意は成り立たない。**
T330 の 400 応答に本文があったかどうかは、保存済み原本からは依然として不明である。

これは R13-Q2 の指摘に直結する。

## R13-1. 実装の質 — 設計は元提案より良い

私の `PHASE6_DIAGNOSTIC_REPAIR_PROPOSAL.md` に対し、T335 設計は 3 点で明確に上回っている。
いずれも私が見落としていた境界である。

**① detail を IPC へ渡さない判断。** 私は `LLMBackendError` に載せれば
そのまま記録されると考えていたが、backend と broker は同一 process にあり、
client へは `ERROR` frame で 4 属性が再構成される。
設計は detail を **broker の `_record_call` までで止め**、
owner-only metrics sink にだけ書く経路を選んでいる。
これにより `GENERATION_IPC_PROTOCOL` の exact key 集合、canonical bytes、
Brain、generation V1/V2 が一切変わらない。
実装後の `grep backend_error_detail` は製品側 4 file のみで、
`brain.py` / `audit.py` / `decision.py` / runner の public projection に 1 件も現れない。

**② `AdmissionMetrics` writer の資源 bug の発見。** 元の `_writer_loop` は
serialize/write 例外時に当該 item の `task_done()` を呼ばず、
`flush()` / `aclose()` が `queue.join()` で永久に待ち得た。
私の提案にはこの指摘が無い。修正後は `try/finally: task_done()` で
全 item が必ず対応づけられ、失敗時は残 queue を `get_nowait()` で有限 drain して終了する。

**③ `path=` と `handle=` の分離。** private detail の永続化を
**handle 注入時だけ**に限定し、未注入時は record 前に
`replace(metric, backend_error_detail=None)` で projection している
（`admission_metrics.py:303-304`）。
既定 serializer は `include_private_detail=False` 固定であり、
detail を出す経路は `_broker_child` の `phase6=True` 分岐だけである
（`run_phase5_local_smoke.py:3089-3095`）。**opt-in の向きが正しい。**

抽出 helper も堅い。strict UTF-8、`object_pairs_hook` による duplicate key 拒否、
`parse_constant` による NaN/Infinity 拒否、`RecursionError` 捕捉、
top-level と `error` の object 検査、3 key 以外を一切 copy しない、
非 printable の 1 対 1 space 置換、256 code point 切り。
`RESPONSE_TOO_LARGE` は drain 中に送出されるため partial bytes が detail にならない。

不変条件が **3 層で独立に検査されている**点も良い。
`LLMBackendError.__init__`（`types.py:387-396`）、
`AdmissionMetric.__post_init__`（`admission_metrics.py:130-139`）、
`_source_error_is_valid`（`admission_broker.py:1543-1548`）が
それぞれ「HTTP_STATUS 限定 / 1–256 / 全 printable」を再検査する。
1 箇所を迂回しても次で落ちる。

T333 の審査過程も健全である。R5 で
「kill deadline 後に deadline なしの `asyncio.gather` で待つため 2+1+1 秒を超え得る」
「terminate/kill 自体の race 例外が stable cleanup error を覆い得る」
を必須修正として差し戻し、R6 で freeze hash 照合のうえ承認している。
**自己承認は起きていない。**

## R13-2. 全体回帰 FAIL の切り分け — 実測で確定した

T333 は全体 regression gate を `NOT APPROVED / FAIL` とし、
新 failure 文書は
「変更前後同一 host 比較は行っておらず、host scheduling、TCP、今回差分との
因果関係を確定していない」と記録している。

**この比較を実施した。結果は明確である。**

比較対象は `logs/t332-review/source-before-files/`（T336 実装前の製品 + test）と現 tree。
当該 test 関数本体は両 tree で byte 一致を確認した
（AST source segment SHA-256 `e1e123923fb0ddeaac9bfd3958fce523d5f828f23d75014e784b6c880c6bc900`、46 行）。

| 実行条件 | 実装前 tree | 現 tree |
|---|---|---|
| 当該 test 単独 | **PASS 12/12** | **PASS 12/12** |
| `AdmissionBrokerTests` class 単独 | PASS 2/2 | — |
| module 単独 | PASS 4/4 | — |
| **固定 6 module batch** | **FAIL 3/3** | **FAIL 1/1** |
| `test_content_models.py` + module | FAIL | — |
| `test_content_models.py` + class | FAIL | — |
| `test_phase4_llm_backend.py` + module | FAIL | — |
| `test_phase4_llm_contracts.py` + module | FAIL | — |
| 2 module を collect し当該 test だけ実行 | PASS | — |
| 先行 test **1 件**のみ + class | PASS | — |
| GC 無効 + `test_content_models.py` + module | FAIL | — |

現 tree の batch は `1 failed, 283 passed, 175 subtests passed` で、
**T334 R4 の実測（283 PASS + 175 subtests / 1 FAIL）と完全に一致する。**
実装前 tree は `1 failed, 263 passed, 175 subtests passed` を 3 回とも再現した。

**したがって当該 FAIL は T336 の P1/P2 差分と無関係である。**
製品コードを 1 行も含まない実装前 tree が、同じ batch で同じ test を落とす。

さらに性質が特定できた。

- **単独では決して落ちない**（module 単独 4 回、class 単独 2 回、test 単独 24 回すべて PASS）
- **先行 module が 1 つでもあれば落ちる。** 先行が
  `test_content_models.py`（同期・純データ・broker と無関係）でも落ちる
- **collect だけでは落ちない。** 2 module を collect して当該 test だけ実行すると PASS。
  つまり import ではなく**先行 test の実行**が条件である
- **先行 test 1 件では落ちない。** 累積量の問題である
- **GC が原因ではない。** `gc.disable()` を入れても同じく FAIL

なお失敗する assertion は run ごとに移動する。
現 tree では `active.lease.claim()`（1173 行）が `ADMISSION_UNAVAILABLE`、
実装前 tree では `poisoned_successor.status` が `UNAVAILABLE`（1180 行）だった。
**同一 test 内の複数の 0.05 秒境界のうち、先に負けたものが表面化している。**

原因は test 自身の時間予算にある。この test は
`cancellation_grace_seconds=0.05` / `provider_drain_grace_seconds=0.05` /
`backend_request_timeout_seconds=0.05` を同時に設定しており、
`cancellation_grace_seconds` は `admission_client.py:767` で
**control ACK の timeout そのもの**として使われる。

```python
await self._send(operation, **fields)
async with asyncio.timeout(self._config.cancellation_grace_seconds):
    return await asyncio.shield(acknowledgement)
```

Windows の既定 timer 分解能（15.6 ms）に対して 50 ms は 3 tick 程度しかなく、
process が温まった状態では round trip が恒常的にこれを超える。

**「根因未確定」は解消できる。** 少なくとも
「T336 差分が原因である可能性」は実測で排除でき、
「host scheduling / TCP の偶発」でもない（決定的に再現する）。
残るのは test の時間予算設計だけである。

## R13-3. 指摘

| # | 内容 | 深刻度 |
|---|---|---|
| Q1 | P2 に production の呼出し元が無く、次回 preflight で自動的には記録されない | **HIGH** |
| Q2 | error body の byte 長が記録されないため `detail=None` と空 body を区別できない | MEDIUM |
| Q3 | 当該 test の 50 ms 予算が order 依存で決定的に破綻する | MEDIUM |
| Q4 | truncation の順序が最も診断価値の高い `message` を最初に捨てる | LOW |
| Q5 | 256 は code point 上限で byte 上限が無い | LOW |
| Q6 | 既存 test 1 件が rename されたが「追加 21 件」としか報告されていない | LOW |

### Q1【HIGH】P2 は実装されたが、誰も呼んでいない

`_record_provider_command_observation` の呼出し元を全走査した結果、
**production code には 1 件も無い。**

```
scripts/run_phase5_local_smoke.py:251   ← 定義
tests/test_phase5_local_smoke.py:2273   ← test
tests/test_phase5_local_smoke.py:2288   ← test
```

docstring は「Explicit preflight entrypoint; callers decide when observation is authorized」
としており、**呼ばないのは意図的な設計**である。
provider PID とユーザー承認が要る操作を自動実行しないのは正しい。

しかし `Docs/` 側を走査すると、この entrypoint に言及しているのは
設計書と T333/T336 の handoff だけで、
**RUNBOOK / OPERATIONS / preflight 手順のどこにも記載が無い。**

R12-P2 が指摘した gap は
「T316 と T330 で provider の構成が同じかどうかを保存済み preflight だけから判定できない」
だった。**この gap は現状まだ閉じていない。**
次回 Stage B の preflight を今の手順どおり実施すると、
T330 と同じく起動構成が記録されないまま実行される。

実装の品質は高いが、**呼ばれないなら診断能力は 0 のままである。**
T334 が検証したのは helper の内部挙動であって、preflight が記録を残すことではない。

**最小修正案**: Stage B preflight の手順書に
「provider PID 確定後、`_record_provider_command_observation(pid, <private path>, <既存 3 boolean>)`
を一回実行し、返り値の `command_canonical_sha256` を公開 handoff に記載する」
の 1 項目を加える。製品コードの変更は不要で、手順の 1 行である。

Stage B 再開より前に閉じる価値がある。

### Q2【MEDIUM】detail が `None` のとき、空 body なのか解析不能なのかが分からない

R13-0 のとおり、`response_bytes` は error path では常に 0 の固定値であり、
**HTTP error body の実際の長さはどこにも記録されない。**

P1 実装後、次の run で detail が `None` だった場合に区別できないものが 3 つある。

- provider が **本文なし**で 400 を返した
- 本文はあったが **JSON でなかった**（HTML error page など）
- 本文は JSON だったが **`error` object を持たない形**だった

いずれも `_extract_http_error_detail` は `None` を返し、
metric には何も残らない。**R12-P1 で指摘した「理由を捨てる」構造が、
detail が取れなかった場合に限って温存されている。**

`len(encoded)` は **内容ではなく件数**であり、原文でも secret でもない。
記録しても privacy boundary に触れない。

**最小修正案**: `AdmissionMetric` に
`backend_error_body_bytes: int | None`（HTTP_STATUS 時のみ）を足し、
drain 済み `encoded` の長さを入れる。private sink 限定にする必要すら無く、
公開可能な件数として扱える。

これがあれば、次に detail が `None` でも
「0 bytes だった」か「1,400 bytes あったが JSON ではなかった」かが即座に分かる。

### Q3【MEDIUM】当該 test の時間予算は order 依存で決定的に破綻する

R13-2 の実測のとおり、この test は
**単独なら必ず通り、先行 module があれば必ず落ちる。**
「flaky」ではなく「batch 実行では常に FAIL」である。

現在の運用上の実害は 2 つある。

- **固定 6 module batch が構造的に緑にならない。**
  T333 が全体 gate を `NOT APPROVED` にせざるを得ず、
  Stage B 再開の判断材料が 1 つ塞がれている
- **本来の検証（drain grace 期限切れによる permanent poison）に到達していない。**
  失敗が `claim()` や `wait_offer()` で起きるため、
  この test が守るはずの契約は **実質的に未検証**である

`Do Not Repeat` は「通過目的で test/製品の timeout を増加したり skip したりしない」
と定めており、これは正しい。**しかし今回必要なのは通過目的の緩和ではない。**
`cancellation_grace_seconds` はこの test では
「cancel 猶予」と「control ACK 期限」の 2 つの意味を同時に担っており、
検証したいのは前者だけである。

**最小修正案**: `admission_client` の control ACK timeout を
`cancellation_grace_seconds` から分離し、独立の設定値にする。
既定値は現行と同じにして既存契約を変えず、
当該 test だけが ACK 期限を実測に耐える値へ設定できるようにする。
`provider_drain_grace_seconds=0.05` は変えないので、
**検証対象である drain 期限の厳しさは一切緩まない。**

これは製品の意味論変更を伴うため、独立設計 → 独立承認の経路が要る。
本 Phase の完了条件とは無関係なので、**Phase 6 後でも構わない。**
ただし「根因未確定」のまま放置するのではなく、
**「原因は test の時間予算設計であり、外部要因ではない」と確定した上で**
保留すべきである。

### Q4【LOW】truncation が `message` を最初に捨てる

```python
parts.append(f"{key}={cleaned}")   # 順序は type, code, message 固定
detail = " ".join(parts)[:256]
```

`type` と `code` を先に置き、`message` を最後に置いて末尾から切っている。
**診断価値が最も高いのは `message`** であり、
`type` が長い provider では `message` が丸ごと落ちる。

llama.cpp の `type` は `invalid_request_error` 程度なので実害は小さい。
ただし別の provider や将来の build で成り立つ保証は無い。

**最小修正案**: 各 part に個別上限を置く（例 `type` 64 / `code` 32 / `message` 残り）。
合計 256 は変えない。

### Q5【LOW】256 は code point 上限で byte 上限が無い

`len(backend_error_detail) > 256` は code point 数である。
非 ASCII のみの message なら最大 1,024 bytes になり得る。

private artifact の 1 record としては無害な量だが、
設計が「有限境界」を強調している以上、
**bytes でも上限があることを言えた方がよい。**

**最小修正案**: 検査に
`len(backend_error_detail.encode("utf-8")) <= 1024` を追加する。

### Q6【LOW】既存 test 1 件の rename が報告に出ていない

collect した node を実装前後で diff した結果は次のとおりである。

```
実装前: 264 node
実装後: 284 node
追加 21 / 削除 1
```

削除されたのは
`AdmissionBrokerTests::test_http_error_envelope_preserves_every_attribute_and_privacy` で、
`::test_http_error_detail_uses_private_broker_metric_not_error_wire` へ rename されている。

**内容を照合した結果、問題は無い。** 旧 test の assertion は
`error.code` / `http_status` / `retryable` / `provider_quiescence` /
`str(error)` / `error.args` / `emitted` の exact 一致 / `wire_evidence` の
secret 非混入 / registry 非混入 / `metric.backend_code` まで**全て残っている**。
そのうえで `error.backend_error_detail is None`（client 側に来ない）、
`metric.backend_error_detail == detail`（private には来る）、
`detail not in wire_evidence`、private file の row 確認が加わっている。
**厳密な superset である。**

指摘は報告の粒度だけである。T334 と CURRENT_STATE は
「追加 21 case は全 PASS」と書いているが、
**既存 1 件が消えたことは書いていない。** 283 と前回 263 の差が
+21 ではなく +20 である理由が記録から辿れない。

**最小修正案**: handoff に「追加 21、rename により消滅 1、純増 20」と 1 行。
node 集合の前後 diff を測定項目に入れれば以後は自動的に露見する。

## R13-4. 結論

**P1/P2 の実装は良質である。** 設計は私の元提案が見落としていた
IPC 境界、writer の資源 bug、opt-in の向きの 3 点を正しく捉えており、
不変条件は 3 層で独立に検査され、detail の露出面は製品 4 file に閉じている。
T333 の審査も R5 差し戻し → R6 承認という正しい経路を通っている。

**全体回帰 FAIL は T336 と無関係である。** 実装前 tree で 3/3 再現し、
単独では両 tree とも 24/24 PASS する。
「host scheduling / TCP の偶発」ではなく、
**先行 module があれば決定的に破綻する test 側の時間予算**が原因である。
T333 が gate を `NOT APPROVED` にしたのは、
情報が無い時点の判断としては正しく、**今は根拠を持って解除できる。**

**残る実質的な問題は Q1 だけである。**
P2 は動くが呼ばれない。手順書に 1 行足さない限り、
次回 Stage B は T330 と同じく provider 起動構成が不明のまま実行される。
**R12-P2 の gap は実装によってではなく、手順によって閉じる。**

Q2 を併せて入れておくと、次の 400 で
「detail が取れない」場合にもなお情報が残る。
P1 の価値を確定させる小さな追加であり、Stage B 再開前が望ましい。

なお **Phase 6 の製品的な最大の未解決は R10-F-A のままである。**
P1/P2/Q1/Q2 はいずれも「次に失敗したとき原因が分かる状態」を作る変更で、
schema 適合率 23.5% そのものを前進させるものではない。
T330 以降、その数字に関する新しい情報は 1 bit も増えていない。

## R13-5. 検証した内容と検証していない内容

**検証した**: `PHASE6_R12_DIAGNOSTIC_DESIGN.md` 全文と
`P2_ASYNC_SUBPROCESS_ADDENDUM.md` 全文、
実装前 tree との全 file diff（製品 5 / test 6、他に差分が無いことの走査）、
`types.py` / `backend.py` / `admission_metrics.py` / `admission_broker.py` の diff 全体、
`_extract_http_error_detail` の JSON 境界処理、
`_writer_loop` / `flush` / `aclose` / `record_nowait` の書き換え、
`backend_error_detail` を参照する製品側全箇所（grep 全走査）、
`_broker_child` の private handle 経路、
`_record_provider_command_observation` と `_observe_windows_process_command_line` の
呼出し元全走査、`Docs/` 内の P2 entrypoint 言及の走査、
`response_bytes` の算出式、`admission_client._control` の ACK timeout 源、
当該 test 関数本体の AST source segment hash 一致、
実装前後 tree での collect node 集合 diff（264 / 284）、
rename された test の旧新 assertion 全比較、
上表 11 条件の pytest 実測（batch 4 回、部分組合せ 7 回、単独 24 回）、
T333 handoff の R5/R6 判定、T334 の実測値、新 failure 文書、
`python scripts/check_docs.py`（不整合なし、exit 0）。

**検証していない**: `scripts/run_phase5_local_smoke.py` の 519 行 diff の全文
（P2 helper と `_broker_child` 周辺を優先して読んだ）、
6 test module の新規 21 case の本文全件、
`_windows_command_line_to_argv` の FFI を実 provider に対して実行した場合の挙動、
実 provider / GPU / 実 game / CIM、
P2 の redaction が実際の llama-server argv に対して十分かどうか、
`asyncio` subprocess 補遺の cleanup 契約を実 process で走らせた場合の挙動、
当該 CLAIM timeout が interpreter のどの内部要因で累積悪化するかの特定
（GC は排除したが、それ以上は追っていない）、
Stage B B01–B11 の判定への影響。

---

# R14 — R13 指摘に対する修正（T337–T340）のレビュー

日付: 2026-09-15
対象: `Docs/ai/PHASE6_R13_TEST_REPAIR_PLAN.md`、
`tests/test_phase5_generation_admission.py` の T337 修正差分、
T338 調査 / T339 独立試験 / T340 独立 review の各 handoff、
`Docs/ai/failures/2026-09-15_R13_SANDBOX_FILESYSTEM.md`、
`2026-09-15_DIAGNOSTIC_REGRESSION_CLAIM_TIMEOUT.md` の追補
判定: **APPROVE。修正は正しく、test は弱まるどころか強くなっている。
固定 6 module は FAIL が現れて以降はじめて完全に緑になった。
残る Stage B の gate は Q1 一点である**

本レビューでも repository は EXTERNAL_REVIEW_LOG.md 以外変更していない。

## R14-0. 先に自分の訂正

R13-2 で私は次のように書いた。

> Windows の既定 timer 分解能（15.6 ms）に対して 50 ms は 3 tick 程度しかなく、
> process が温まった状態では round trip が恒常的にこれを超える。

**これは仮説であって、私の測定結果ではない。**
私が実測で確定したのは
「先行 module があれば決定的に FAIL し、単独では決定的に PASS する」
「実装前 tree でも同様に FAIL する」
「GC は原因ではない」の 3 点までで、
**timer 分解能が機序であることは検証していない。** 断定的に書いたのは私の誤りである。

T337 計画が

> 外部レビューにある「先行 module があれば必ず失敗」「Windows timer が原因」は
> 外部の測定・解釈として扱う。今回取得していない生ログや OS 内部原因を自分の実測としない。

としたのは正しい。**特に後者は、私の側で線を引くべきだった。**
R13-5 の「検証していない」に
「当該 CLAIM timeout が interpreter のどの内部要因で累積悪化するかの特定」と書いた範囲を、
本文で越えていた。

## R14-1. 修正内容 — 対象の期限は保持され、test は強化された

実装前（私が R13 で使った T336 時点の snapshot）と現 tree の全 file 比較を行った。
**差分は `tests/test_phase5_generation_admission.py` 1 file のみ。**
`admission_metrics.py` / `run_phase5_local_smoke.py` など mtime が動いていた file は
byte 一致で、「test-only 修正」の主張は正しい。

config の変化は次のとおりである。

| 設定 | 修正前 | 修正後 | 意味 |
|---|---|---|---|
| `provider_drain_grace_seconds` | 0.05 | **0.05** | **検証対象。保持** |
| `backend_request_timeout_seconds` | 0.05 | **0.05** | 保持 |
| `authentication_timeout_seconds` | 0.5 | 0.5 | 不変 |
| `cancellation_grace_seconds` | 0.05 | **0.5** | ACK 期限。緩和 |
| `shutdown_grace_seconds` | 0.1 | **2.0** | 回収期限。緩和 |

**検証対象の 50 ms は 1 ms も緩んでいない。**
緩んだ 2 つはいずれもこの test の検証対象ではなく、
`cancellation_grace_seconds=0.05` は
`test_missing_control_ack_closes_session_and_cleans_claimed_slot`（725–728 行）に、
短い `shutdown_grace_seconds=0.2` も同 test に残っている。
**設定値そのものの coverage は失われていない。**

しかも ACK 専用 test は **ACK が来ないこと**を検証する test なので、
round trip が 50 ms を超えても判定は変わらない。
緩和すべき test と残すべき test の切り分けが正しい。

より重要なのは、assertion が**置き換えではなく追加**である点である。

```python
await asyncio.sleep(0.08)          # 修正前: 推測
```
```python
await asyncio.wait_for(drain_started.wait(), timeout=2.0)      # 修正後: 観測
assert len(drain_tasks) == 1
await asyncio.wait_for(asyncio.shield(drain_tasks[0]), timeout=2.0)
assert config.provider_drain_grace_seconds == 0.05
assert poison_origins == [(True, True, 1, False, "PROVIDER_QUIESCENCE_UNKNOWN")]
assert backend.active == 0
assert not gate.is_set()
```

旧 assertion は 1 件も削られていない（`poisoned` / `poison_reason` /
`poisoned_successor.status` / `.lease` / `a._closed` / `_acks` /
`_control_lanes` / `_results` / `rejected.status`）。
そのうえで **poison が drain task 自身から、draining 状態で、
provider がまだ active=1 のときに、gate 未解放のまま発生した**ことを
tuple の exact 一致で固定している。

`_drain_timeout` は `admission_broker.py:1147` で
`asyncio.create_task` により独立 task として起動されるため、
`asyncio.current_task() is drain_tasks[0]` は実質的な意味を持つ。
**「別経路の poison が偶然同じ reason を出した」偽陽性が塞がれた。**

`assert backend.active == 0` と `assert not gate.is_set()` の組は、
provider が gate 解放ではなく **cancel によって終了した**ことを示す。
旧 test にはこの区別が無かった。

**R13-Q3 で私が挙げた「本来の検証に到達していない」は解消され、
到達したうえで旧 test より強い契約になっている。**

## R14-2. 独立検証 — 再現と mutation test

### 固定 6 module の再現

現 tree を scratchpad へ複製し、同一 batch を 3 回実行した。

| run | 結果 | 時間 |
|---|---|---|
| 1 | **284 passed, 175 subtests passed** | 109.54 s |
| 2 | **284 passed, 175 subtests passed** | 57.05 s |
| 3 | **284 passed, 175 subtests passed** | 57.11 s |

FAIL / ERROR / skip は 0。T339 通常 host 測定（284 PASS + 175 subtests、107.43 秒）と一致する。
R13 で私が測った同一 batch の **FAIL 3/3 とは反転している。**

当該 test 単体の実行時間は 0.66 秒で、
旧 test の `sleep(0.08)` + 失敗待ちより速い。

### mutation test — oracle が空でないことの確認

T337–T340 のいずれも、**修正後の test が製品の退行を検出できるか**を試していない。
「PASS した」ことは「検証している」ことを意味しないため、
製品側を故意に壊して確認した（scratchpad の複製に対してのみ実施、repository は不変）。

| 変異 | 内容 | 結果 |
|---|---|---|
| M1 | `_drain_timeout` の判定を `or True` にし poison を発生させない | **FAIL** |
| M2 | poison reason を `PROVIDER_TIMEOUT` に変更 | **FAIL** |
| — | 無変異（復元後） | PASS |

**test は空回りしていない。** drain 期限切れによる poison が起きなくなれば落ち、
理由 literal が変わっても落ちる。
M1/M2 とも 0.9 秒以内に落ちるため、退行時の feedback も速い。

### T340 の判定について

T340 が

> ACK .05→.5 秒、shutdown .1→2 秒は実値変更であり、**不変とは扱わない**

と明記したのは正しい。T337 計画本文の
「これは検証対象の期限を延長する変更ではない」は
drain 期限については真だが、書きぶりだけを見ると
「何も緩めていない」とも読める。
**独立 Reviewer がその読み替えを拒否した**のは、独立性が機能している証拠である。

Q6 の node diff についても、T340 が
「parameter 文字列内のスラッシュを path と誤認して 2 node の prefix が欠落」
を検出し訂正させている。
訂正後の 264 / 284・追加 21 / 削除 1 は、
R13 で私が pytest の collect 出力から得た値と完全に一致する。

## R14-3. 指摘

| # | 内容 | 深刻度 |
|---|---|---|
| S1 | Q1（P2 preflight 呼出し）が計画文書にしか無く、active な T328 packet に入っていない | **MEDIUM** |
| S2 | Q2 を後続へ回したまま Stage B を再実行すると、再び何も分からない場合がある | MEDIUM |
| S3 | T334 packet にあった「通常 host 必須」が T339 packet へ継承されず、1 回分の測定が無駄になった | MEDIUM |
| S4 | `poison_origins` は「遷移 1 回」ではなく「呼出し 1 回」を固定している | LOW |

### S1【MEDIUM】Q1 は計画されたが、packet に載っていない

R13-Q1（P2 の観測入口に production の呼出し元が無い）に対し、
T337 計画は `Docs/ai/PHASE6_R13_TEST_REPAIR_PLAN.md` §「Q1: 次回 Stage B preflight の追加作業」
に 5 項目の手順を書いた。**内容は妥当である。**
PID 確認 → owner-only 保存先用意 → 一度だけ呼出し →
raw 非公開 / state・hash だけ公開 → 独立確認、という順序も正しい。

しかし走査した結果、`_record_provider_command_observation` に言及する
task packet / handoff は `T336_DIAGNOSTIC_IMPLEMENTATION.md` だけで、
**`Docs/ai/tasks/T328_STAGE_B_PREPARATION.md`（Active task、BLOCKED）には 1 語も無い。**

計画文書自体は `CURRENT_STATE.md` から参照されているので行方不明にはならない。
だが **authority は packet にあり、計画文書には無い。**
T328 が次に動くとき、実施者が読むのは packet である。
計画は「次回の担当 packet へ転記し、その revision を独立 Reviewer へ通す」と述べているが、
**その転記はまだ行われていない。**

Q1 は現時点で **Stage B 再実行前に閉じるべき唯一の gate** である。
これが落ちれば次の run も T330 と同じく provider 起動構成が不明なまま走る。

**最小修正案**: T328 packet に計画 §Q1 の 5 項目を転記し、
`command_canonical_sha256` の公開 handoff 記載を launch の前提条件として明記する。
文書 1 箇所の追記で、製品変更もテストも不要である。

### S2【MEDIUM】Q2 の順序を明示しておくべき

T337 計画は Q2（error body の byte 数記録）を
「製品変更として後続設計」とした。**判断としては妥当である。**
承認済み抽出契約に触れる変更を、独立設計を経ずに入れるべきではない。

ただし順序の帰結は明示されていない。
現状で Stage B を再実行し、再び 400 が返り、
その body が空か非 JSON か `error` object を持たない形だった場合、
**`backend_error_detail` は `None` になり、body があったのかどうかも記録されない。**
P1 を入れた意味が、その場合に限って失われる。

R13-0 のとおり `response_bytes` は error path では常に 0 の固定値なので、
「0 だったから空だった」とも言えない。

**最小修正案**: 判断そのものは変えず、
「Q2 未実装のまま Stage B を再実行した場合、
detail が取れなければ原因は再び UNKNOWN になる」ことを
T328 packet か F008 の `Do Not Repeat` に 1 行残す。
そのうえで Q2 を先に入れるか、UNKNOWN の可能性を受け入れて走るかを
ユーザーが選べるようにする。

### S3【MEDIUM】既知の前提条件が後続 packet へ継承されていない

T339 の初回実行は workspace-write sandbox で行われ、
55.3 秒で 16 FAIL / 43 ERROR となった。原因はすべて
`C:/Users/<user>/AppData/Local/Temp/pytest-of-<user>` への `scandir` / cleanup 拒否である。

**この前提条件は既知だった。** T334 packet には

> private fixtures を含むため**最初から Owner 通常 host の sandbox 外実行を使用し**

と明記されている。同じ 6 module を同じ目的で走らせる T339 の packet に、
この一文が引き継がれていない。
sandbox 実行は packet 側で指定されたものではなく、既定の実行手段が使われた結果である。

実害は小さい（55 秒 1 回、ACL/TEMP は変更せず、
初回原本を保持したまま通常 host で再測定し、T340 が両方を照合している）。
対応は誠実で、隠蔽も blind retry も無い。

しかし **これは「前回の packet にあった安全条件が次の packet で消える」型の欠落**で、
R01-M3 や R07-G5 で扱った drift と同じ class である。
private fixture を含む batch は今後も繰り返し走る。

**最小修正案**: 「`tests/test_phase6_private_review.py` を含む batch は
通常 host（sandbox 外）で実行する」を TEST_POLICY か RUNBOOK の 1 行にし、
packet ごとの転記に依存させない。
**packet 間の転記で守られる条件は、いずれ転記されなくなる。**

なお初回 sandbox 実行が残した一時ディレクトリは
T340 が「cleanup 完了は本承認に含まれない」と明記しており、未回収のままである。
広域削除を避けた判断は正しい。回収は別途、対象を特定して行えばよい。

### S4【LOW】`poison_origins` は「遷移」ではなく「呼出し」を数えている

```python
def observe_poison(reason: str) -> None:
    poison_origins.append((...))
    original_poison(reason)
```

append が `original_poison` の**前**にあるため、
`_poison_locked` が 2 回呼ばれれば、実際には 2 回目が
`if self._poisoned: return` で no-op になっても
`poison_origins` は 2 要素になり assertion が落ちる。

現在の実装では 1 回しか呼ばれないので問題は無い。
ただし将来、防御的に `_poison_locked` を重ねて呼ぶ経路が入ると、
**製品の契約（遷移は 1 回）は守られているのに test が落ちる。**

判定を厳しくしている方向なので実害は小さく、
落ちれば人が見るので fail-closed でもある。
直すなら「遷移が起きた呼出しだけを記録する」に変えればよいが、
**今の形のままでも構わない。**気づいた点として記録しておく。

## R14-4. 結論

**修正は承認に値する。** 検証対象の drain 期限 50 ms は保持され、
緩和されたのは検証対象でない 2 つの予算だけで、
その 2 つの短い値は専用 test に残っている。
assertion は削られず、drain task 由来であることの証明が追加された。

**固定 6 module は、FAIL が現れて以降はじめて完全に緑になった。**
私の側でも 3 回連続 284 PASS / 0 FAIL を再現し、
さらに mutation test で **oracle が空でないこと**を確認した。
T339–T340 が行っていない検証であり、
「PASS した」と「検証している」の差はここで埋まった。

運用面では T340 が T337 計画の「不変とは扱わない」線引きを行い、
Q6 の証跡誤りも検出・訂正させている。**独立性は機能している。**

**残る Stage B の gate は Q1 一点である。**
P2 は動くが、呼ぶ手順が active な packet に載っていない。
S1 と S2 はどちらも文書 1 行の追記で閉じられ、実 LLM を消費しない。

そして **R10-F-A は依然として動いていない。**
R13 から R14 までに増えたのは「次に失敗したとき原因が分かる状態」と
「回帰が緑である状態」であって、
schema 適合率 23.5% に関する情報は 1 bit も増えていない。
**Phase 6 の完了はそこにかかっている。**

## R14-5. 検証した内容と検証していない内容

**検証した**: T336 時点 snapshot と現 tree の全 file 比較
（差分が対象 test 1 file のみであることの確認を含む）、
`tests/test_phase5_generation_admission.py` の修正差分全体、
class fixture の config 実値（261–266 行）と `_start` の既定値、
`test_missing_control_ack_closes_session_and_cleans_claimed_slot` の config 実値、
repository 内の `cancellation_grace_seconds` / `shutdown_grace_seconds` /
`provider_drain_grace_seconds` の設定値全走査、
`_ensure_drain_watch_locked` / `_drain_timeout` / `_poison_locked` / `snapshot` の実装、
drain が独立 task として起動されることの確認、
固定 6 module batch の 3 回実行（各 284 PASS + 175 subtests、FAIL/ERROR/skip 0）、
製品 2 変異による mutation test と復元後の再確認、
`Docs/ai/PHASE6_R13_TEST_REPAIR_PLAN.md` 全文、
T338 / T339 / T340 handoff、2 件の failure 文書、
`_record_provider_command_observation` に言及する packet の全走査、
`PHASE6_R13_TEST_REPAIR_PLAN.md` の被参照箇所の走査、
T328 packet 本文、TASKS.md の T340 行、CURRENT_STATE.md。

**検証していない**: `logs/t339-r13-host/` および `logs/t340-r13/` の原本 hash 照合
（T340 が実施済みのため重複を避けた）、
初回 sandbox 実行の 59 件の失敗内訳、
T338 調査 handoff の全文、
当該 CLAIM timeout の OS / interpreter 内部機序（R14-0 のとおり未確定のまま）、
残存する pytest 一時ディレクトリの実体、
実 provider / GPU / 実 game / CIM、
Stage B B01–B11 への影響、R10-F-A に関する新情報（存在しない）。

---

# R15 — provider 実体（`C:\AIagent`）の確認と R12 仮説の撤回

日付: 2026-09-15
対象: `C:\AIagent`（llama-server 実体、`agent/config.toml`、`agent/lib/serve.py`、`agent/serve.bat`）、
稼働中 provider process の実 command line、
T316 / T330 private evidence の `prompt_tokens` / `prompt_bytes`（metadata のみ）、
`ai_client/llm/backend.py:289-356` の HTTP 要求構築
判定: **R12-3 の「最有力候補 = provider の起動構成差」を撤回する。
T316 と T330 の provider 構成は同一だった。
400 の原因は repository 側からは決定できず、決定的証拠は稼働中 provider への 1 要求で得られる**

## R15-0. 撤回

R12-3 で次のように書いた。

> client 側が不変で、同一 build・同一 model・同程度の要求サイズで、
> 片方は 34 件応答し、もう片方は 107 件即 400 である以上、
> **差分は provider の起動構成にあると考えるのが最も自然である。**

**これは誤りである。** T330 の provider（PID 24380）は現在も稼働しており、
実 command line を読み取った結果、T316 preflight が記録した観測集合と一致した。

T333 独立 Reviewer が既に

> `logs/t328-stage-b-preparation/provider-observation.json` は T330 対象 provider について
> `command_ngl99=true`、`command_context8192=true`、`command_jinja=true` を既に保存している。
> したがって「T330 では jinja が記録されていない」は不正確である。

と指摘していた。**この指摘は正しく、私の側の確認が足りていなかった。**

## R15-1. 稼働中 provider の実 command line

`Win32_Process` を read-only で 1 回読んだ。provider の起動・停止・再起動は行っていない。

```
ProcessId    : 24380
CreationDate : 2026/09/15 12:38:17
CommandLine  : "C:\AIagent\llama-server.exe"
               -m C:\models\Qwen3.5-9\Qwen3.5-9B-Q4_K_M.gguf
               --host 127.0.0.1 --port 8080
               -ngl 99 -c 8192 --jinja --reasoning-format none
```

PID と creation は **T330 preflight の記録（PID 24380 / `2026-09-15T12:38:17.5183200+09:00`）と完全一致**する。
T330 を走らせた provider がそのまま生きている。

`--jinja` は**存在する。** R12 で疑った
「起動フラグの違いで `response_format.json_schema` が 400 になり得る」の
最有力候補は成立しない。

T316 preflight（PID 33700）は
「context 8192、GPU layers 99、jinja 有効、reasoning format `none`、build `b10697-093adb242`」
を記録している。**T330 の実 argv はこれと矛盾しない。**
両 run の provider 構成は、観測できる範囲ですべて一致する。

argv に credential/token/URI userinfo は含まれない。
P2 の redaction を通しても値は落ちず、
`command_canonical_sha256` をそのまま公開できる形である。

## R15-2. この provider は `serve.bat` 経由で起動されていない

`agent/config.toml` の `game` profile は次のとおりである。

```toml
[profile.game]
model = "C:\\models\\Qwen3.5-9\\Qwen3.5-9B-Q4_K_M.gguf"
args = "-ngl 99 -c 8192 --jinja --chat-template-kwargs '{\"enable_thinking\":false}'"
```

稼働中 provider との差は 2 点ある。

**① thinking 無効化の手段が違う。**
profile は `--chat-template-kwargs '{"enable_thinking":false}'`、
実 process は `--reasoning-format none`。

`config.toml` には手段について次の注意書きがある。

> `--chat-template-kwargs` で thinking を切る。付け忘れると推論だけで max_tokens を
> 使い切り、応答本文が空で返る（2026-08-30 に実際に発生）。

**② 引数の順序が `serve.py` の構築順と違う。**
`agent/lib/serve.py` は
`[exe, "-m", model, *profile_args, "--host", host, "--port", port]` の順に組む。
実 process は `--host` / `--port` が profile 引数より**前**にある。

したがってこの provider は `serve.bat` ではなく、**手で組んだ command line で起動されている。**
T307 preflight が「環境変数による thinking 無効」と記録し、
T316 / T330 が「reasoning format none」と記録しているのは、この手動起動と整合する。

運用上の含意は 1 つある。**`config.toml` は実際に使われた構成の記録になっていない。**
P2 が argv を直接記録する設計を選んだのは、この意味で正しい。

## R15-3. provider log file は存在しない

`agent/lib/serve.py` の起動は次の 1 行である。

```python
return subprocess.call(cmd)
```

**stdout / stderr を redirect していない。** `agent/` 配下の `infra-server.stderr.log` 等は
すべて 2026-09-07 の別作業のもので、09-14 以降に更新された file は
`C:\AIagent` 配下に 1 件も無い（zip / pytest cache を除く走査で確認）。

つまり **T330 の 400 に対応する provider 側のログ file は保存されていない。**

F008 の

> HTTP400 理由が保存済み原本で判明しない場合、
> ユーザー所有 LLM の該当時刻の理由行を確認する

を満たす経路は、**provider を起動した console window の scroll back のみ**である。
本 session の terminal は空なので、別 window である。
llama-server は要求ごとにログを出すため、
その window が開いたままなら **09-15 13:13–13:33 JST 付近に 400 の理由行が残っている。**

## R15-4. R13-Q1 の情報 gap は、現 provider については閉じた

R13-Q1 は「P2 の観測入口に production の呼出し元が無く、
次回 preflight で起動構成が記録されない」だった。

**現在稼働している provider については、上記 CIM 読取り 1 回で argv 全体が判明した。**
P2 の code path を通す必要はなかった。

ただし **Q1 自体は閉じていない。** 次に provider が再起動されれば、
そのときに誰かが記録しない限り同じ gap が再発する。
R14-S1 のとおり T328 packet への転記が要る。
今回得た argv は、その転記が行われるまでの暫定記録として本文に残す。

## R15-5. 消去法の現状 — 400 の原因はさらに狭まった

T316（34 応答）と T330（107 件即 400）の間で、
**repository 側から確認できる変数はほぼ尽きた。**

| 変数 | T316 | T330 | 差 |
|---|---|---|---|
| provider PID | 33700 | 24380 | process は別 |
| provider 構成（ngl/ctx/jinja/reasoning/build/model） | 記録あり | **実 argv で確認** | **無し** |
| `prompt_bytes` | min 13,288 / med 15,790 / max 16,340 | min 13,288 / med 15,796 / max 16,058 | ほぼ同一 |
| `prompt_tokens` | min 1,300 / med 1,883 / **max 1,960** | 全件欠測 | — |
| HTTP body の key 集合 | 6 key | 6 key | 無し（`backend.py` 不変） |
| `request_id` の HTTP 到達 | **無し** | **無し** | 無し |

**context 溢れも否定される。** T316 の `prompt_tokens` は最大 1,960 で、
`-c 8192` に対して 4 分の 1 以下である。
T330 の prompt_bytes 分布は T316 とほぼ同一なので、token 数も同程度のはずであり、
「要求が context を超えた」という説明は成り立たない。

`request_id` → `transport_id` の変更も無関係である。
`_request_payload`（`backend.py:289-312`）の body は
`max_tokens` / `messages` / `model` / `response_format` / `stream` / `temperature` の 6 key だけで、
header も `Accept` / `Content-Type`（+ api_key があれば `Authorization`）のみ。
**`request_id` は body にも header にも現れない。**

残っている未確認の変数は、実質的に
**要求の中身（`messages` の本文と `response_format.json_schema` の内容）**だけである。
これは private 原文であり、本レビューでは読んでいない。

## R15-6. 次の一手

**稼働中の provider へ 1 回だけ HTTP 要求を投げれば、400 の本文が直接得られる。**

- 対象 process は T330 を走らせたものと同一（PID 24380、creation 一致）
- T330 の実測 latency は median 0.0 秒 / max 0.1 秒で、
  **400 は生成に入る前に返る。GPU 時間をほぼ消費しない**
- 実 game ではないため、消費済みの「一回実行」承認とは別物である
- provider の起動・停止・再起動は不要

これは **私の判断だけで実行してよい操作ではない。**
provider はユーザー所有であり、本 project は一貫して
agent による provider 操作を禁じてきた。**ユーザーの明示許可があれば実施する。**

許可が出ない場合の代替は R15-3 の console scroll back である。
どちらも得られない場合、**400 の理由は永久に不明のまま**となり、
P1（detail 保存）を入れた次の run を待つしかない。
その場合でも R13-Q2（error body の byte 数）を先に入れておくべきである。

## R15-7. 検証した内容と検証していない内容

**検証した**: `C:\AIagent` 直下と `agent/` の構成、
`Win32_Process` による稼働中 llama-server の PID / creation / 実 command line、
`agent/config.toml` の `[server]` と `profile.dev` / `profile.game` 全文、
`agent/lib/serve.py` 全文（引数構築順と redirect 無しの確認）、
`agent/serve.bat` 全文、
`C:\AIagent` 配下で 2026-09-14 以降に更新された file の走査（該当 0 件）、
本 session terminal の内容（空）、
T316 / T330 private evidence の `prompt_tokens` / `completion_tokens` / `prompt_bytes` 分布（metadata のみ）、
`backend.py:289-312` の body 6 key と `:349-356` の header、
`logs/t319-r10-repair/final-scoped.diff` の `messages` / `output_schema` 周辺、
T316 / T318 / T330 / T333 handoff の provider identity 記述。

**検証していない**: provider への HTTP 要求（未実施。ユーザー許可待ち）、
provider console の scroll back、
T316 provider（PID 33700）の実 argv（process は消滅済み、preflight の観測項目のみ）、
`messages` 本文と `response_format.json_schema` の実内容（private 原文）、
`--reasoning-format none` と `--chat-template-kwargs` の挙動差が
`json_schema` 経路に影響するかどうか、
`C:\AIagent` の git 履歴、`agent/lib/llm.py` / `task_*.py`（本件と無関係）、
2 つの zip と pytest cache の中身。

---

# R16 — 根本原因特定と sampler 修正（T344–T355 / D074）のレビュー

日付: 2026-09-15
対象: `Docs/ai/decisions/D074_PHASE6_STRUCTURED_OUTPUT_AND_FAILURE_COLLECTION.md`、
`Docs/ai/design/PHASE6_SAMPLER_AND_COLLECTION_REPAIR.md`、
T348 調査 / T351 実装 / T353 独立 review / T354 独立試験の handoff、
製品差分（`types.py` / `config.py` / `backend.py` / `run_phase5_local_smoke.py`）、
`logs/t348-sampler/upstream/` に保存された **exact build `093adb242` の実 source**、
T344 private evidence の `backend_error_detail`（metadata のみ）
判定: **APPROVE。本 project で最良の調査である。
因果連鎖を実 source で独立に追跡し、修正が正しい code path に届くことを確認した。
ただし T316 の 34 件成功が説明されておらず、因果はまだ閉じていない**

privacy: `backend_error_detail` の原文は本文へ転記しない。
D074/T348 が公開境界として定めた範囲（`<think>` が公開 template literal であること）に留める。

## R16-0. P1 は導入後 1 回目の run で効いた

R13 でレビューした P1（HTTP error body の有限保存）の実効を確認した。

| run | `PROVIDER_CALL_TERMINAL` | `backend_error_detail` |
|---|---|---|
| T307 | 1 | field 無し |
| T316 | 35 | field 無し |
| T330 | 107 | field 無し |
| **T344** | **107** | **全 107 件に非 null の値あり** |

T330 では 107 件読んでも情報が 1 bit も得られなかった。
**T344 では 1 件読めば原因が分かる状態になっていた。**
detail は sampler 初期化の失敗であること、
および失敗した piece が `<think>` であることを名指ししており、
T348 が「grammar parse 失敗ではない」と切り分けられたのはこの値があったからである。

R12-P1 で「同じ理由を捨てるパターンの 3 度目」と書いた指摘は、
**実際に次の run で回収された。** 提案 → 設計 → 実装 → 独立検証 → 実測の連鎖が機能した例として記録する。

## R16-1. 因果連鎖を exact source で独立確認した

T348 の主張を、`logs/t348-sampler/upstream/` に保存された実 source で自分で追った。
T348 の記述は正確である。

**`common/chat.cpp:1200`**

```cpp
auto extract_reasoning = inputs.reasoning_format != COMMON_REASONING_FORMAT_NONE;
```

**`common/chat.cpp:1235-1240`**

```cpp
auto reasoning = p.eps();
if (supports_reasoning && extract_reasoning) {
    reasoning = p.optional("<think>" + p.space() + ...);
}
```

`reasoning_format = NONE` のとき、出力 grammar の reasoning 要素は **`p.eps()`（空）**になる。
つまり **grammar は `<think>` を受理できない。**

一方 generation prefix は template 由来で `<think>` を含む。
`common/sampling.cpp:297` が非 lazy grammar へ prefix token を順に投入するため、
`<think>` の時点で grammar stack が消失し、
`src/llama-grammar.cpp:1521` の例外が
`tools/server/server-context.cpp:1771` の sampler 初期化 catch から
invalid request として返る。

**T344 の実測（107/107 が sampler 初期化での stack 消失、
grammar parse 失敗の固定文言との一致 0 件、
107 schema すべてが実 `llama.dll` で parse 成功）と完全に整合する。**

T348 が「これは静的な source 照合であり、
実 C++ chat grammar を再生成して実 vocab で prefix を投入する新しい実測ではない」
と限界を明示しているのも正しい。私の確認も同じ性質のものである。

## R16-2. 修正は正しい code path に届く — ここが最大の検証点

修正は HTTP body に 2 key を追加する（`backend.py:313-318`）。

```python
if self._config.llama_cpp_structured_output is not None:
    profile = self._config.llama_cpp_structured_output
    body["reasoning_format"] = profile.reasoning_format
    body["chat_template_kwargs"] = {"enable_thinking": profile.enable_thinking}
```

**この 2 key が per-request で本当に効くのか**が、修正全体の成否を決める。
llama.cpp は未知の body field を黙って無視するため、
効かなければ次の run も同じ 400 になり、承認枠をもう 1 回失う。

実 source で確認した。**3 点とも成立する。**

**① `reasoning_format` の per-request override は実在する。**
`tools/server/server-common.cpp:1294-1296`

```cpp
inputs.reasoning_format = opt.reasoning_format;
if (body.contains("reasoning_format")) {
    inputs.reasoning_format = common_reasoning_format_from_name(body.at("reasoning_format").get<std::string>());
}
```

**② `"deepseek"` は有効な名前である。**
`common/chat.cpp:892-894` が `COMMON_REASONING_FORMAT_DEEPSEEK` を返す。
無効名は `:898` で `std::runtime_error` になるため、綴り違いは即座に露見する。

**③ `enable_thinking` は bool で送らなければならない。**
`server-common.cpp:1307-1320`

```cpp
for (const auto & item : chat_template_kwargs_object.items()) {
    inputs.chat_template_kwargs[item.key()] = item.value().dump();   // ← dump される
}
auto enable_thinking_kwarg = json_value(inputs.chat_template_kwargs, "enable_thinking", std::string(""));
if (enable_thinking_kwarg == "true")  { inputs.enable_thinking = true;  }
else if (enable_thinking_kwarg == "false") { inputs.enable_thinking = false; }
else if (!enable_thinking_kwarg.empty() && enable_thinking_kwarg[0] == '"') {
    throw std::invalid_argument("invalid type for \"enable_thinking\" (expected boolean, got string)");
}
```

JSON boolean `false` を送れば `.dump()` が `"false"` になり一致する。
**文字列 `"false"` を送ると `"\"false\""` になり、`:1318-1320` で `invalid_argument` → HTTP 400 になる。**

実装はここを型で塞いでいる。

```python
enable_thinking: Literal[False] = False
...
if type(self.enable_thinking) is not bool or self.enable_thinking is not False:
    raise ValueError("enable_thinking must be the bool False")
```

test も `self.assertIs(body["chat_template_kwargs"]["enable_thinking"], False)` で
**`assertIs`** を使っている。`0` や `"false"` では通らない。

**もう 1 回 400 を踏みに行く経路が、型 pin と `assertIs` で塞がれている。**
偶然ではなく、この境界を理解したうえで置かれた制約である。

そして `reasoning_format=deepseek` にすれば `extract_reasoning` が真になり、
grammar の reasoning が `p.optional("<think>" ...)` になって prefix を受理できる。
**修正は R16-1 で特定した機構をそのまま解く。**

## R16-3. P2 と違い、今回は production に配線されている

R13-Q1 で「P2 は実装されたが誰も呼んでいない」と指摘した。**今回は違う。**

- `run_phase5_local_smoke.py:1171-1175` — `if phase6:` の分岐で `LlamaCppStructuredOutputConfig()` を設定
- `:3563-3567` — parent → child bootstrap へ転記
- `:3036-3044` — broker child が bootstrap から復元
- `types.py:505-508` — `config_fingerprint` に含める

さらに **fail-closed になっている。**

```python
elif bootstrap.get("phase6") is True:
    raise ValueError("Phase 6 requires llama.cpp structured output profile")
```

Phase 6 で profile が欠落していれば broker child は起動しない。
**壊れた構成へ黙って戻ることができない。** これは正しい設計である。

既定は `None` のままなので、Phase 5 / Q8 と他の OpenAI 互換 provider への body は 1 byte も変わらない。
D074 の「provider-neutral default を変えない」も実装で守られている。

## R16-4. 独立再現

T354 と同じ 7 module 回帰を、現 tree の複製に対して自分で実行した。

```
431 passed, 1 deselected, 175 subtests passed in 66.34s
```

**T354 の実測（431 passed / 1 deselected / 175 subtests、65.4 秒）と一致する。**

synthetic completion（908 秒）は実行していない。T353 が JUnit と assertion を直接照合しているため重複を避けた。

## R16-5. 指摘

| # | 内容 | 深刻度 |
|---|---|---|
| U1 | T316 の 34 件成功が、特定された機構で説明されていない | **HIGH** |
| U2 | 400 が「空 content」へ化ける可能性があり、それは F-A と区別がつかない | MEDIUM |
| U3 | T316 の GGUF hash が記録されておらず、model 同一性を遡って照合できない | LOW |

### U1【HIGH】同じ機構なら T316 も全滅しているはずである

特定された機構は **request ごとに決定的**である。
`response_format.json_schema` を含む要求に対し、
`reasoning_format=NONE` かつ template が `<think>` prefix を出すなら、
sampler 初期化は必ず失敗する。確率的な要素は無い。

ところが T316 は **35 call 中 34 件が応答を返している**（DECISION 8 / OUTPUT_INVALID 26）。
T316 preflight は `reasoning format none` を記録している。
**このままでは矛盾する。**

T345 は「旧 T316 の UNKNOWN は履歴として保持する」と述べるだけで、
T348 / T352 / T353 のいずれもこの矛盾を扱っていない。

R15 で得た事実から、**説明できる再構成が 1 つある。**

`agent/config.toml` の `game` profile は
`-ngl 99 -c 8192 --jinja --chat-template-kwargs '{"enable_thinking":false}'` である。
一方 R15 で読み取った稼働中 provider（T330/T344 と同一 PID 24380）の実 argv は
`-ngl 99 -c 8192 --jinja --reasoning-format none` で、**kwargs を持たない。**

`enable_thinking=false` が CLI で渡っていれば template は `<think>` prefix を出さず、
`reasoning_format=none` で grammar が空でも**衝突が起きない**。
T316 の provider が profile どおりに起動され、
T330/T344 の provider が手動 command line で起動された、という差であれば辻褄が合う。

**ただしこれは私の再構成であって、検証されていない。**
T316 の provider（PID 33700）は既に消滅しており、
preflight が記録した `/props` 由来の値には `chat_template_kwargs` が含まれない。

重要なのは次の点である。

- **この再構成が正しければ、因果は閉じ、修正は十分である**
  （新実装は `reasoning_format` と `enable_thinking` の両方を送るため、どちらの構成でも整合する）
- **正しくなければ、T316 と T330/T344 を分けた変数が未特定のまま残る**

**最小修正案**: 次回 launch 前に、
R13-Q1 の手順（= R14-S1、T328 packet への転記）で **provider の実 argv を記録する**。
そのうえで「今回の provider は T330/T344 と同じ構成か」を hash 一致で確定させる。
これは Q1 を閉じれば自動的に得られる情報であり、追加作業は要らない。

**Stage B を再実行する前に、この矛盾を「未解決」と明記しておくべきである。**
再実行が成功したとき、
「修正が効いた」のか「たまたま provider が T316 と同じ構成で起動された」のかを
区別できる状態にしておく必要がある。

### U2【MEDIUM】400 が「空 content」へ化けると F-A と見分けがつかない

`reasoning_format=deepseek` では、モデルが出した reasoning は
`content` ではなく `reasoning_content` へ振り分けられる。

`agent/config.toml` には 2026-08-30 の実測として次の注記がある。

> 付け忘れると推論だけで max_tokens を使い切り、**応答本文が空で返る**（2026-08-30 に実際に発生）。

今回は `enable_thinking=false` も同時に送るため二重の防御になっている。
**しかし template がこの kwarg を見なければ**、
モデルは thinking を出し、それが `reasoning_content` へ吸われ、
`content` が空または途中で切れた JSON になる。

その結果は HTTP 200 / `OUTPUT_INVALID` であり、
**R10-F-A（schema 適合率 23.5%、`VALUE_NOT_OFFERED` 13 件）と見た目が同じになる。**
P1 の detail は 200 では出ないので、この場合の診断は
`finish_reason` / `completion_tokens` / accepted text 長に頼ることになる。

**最小修正案**: 次回 run の判定時に、
`OUTPUT_INVALID` が出た場合は `completion_tokens` と `finish_reason` を先に見て、
「本文が空 / 途中で切れた」のか「形式が違う」のかを分けて記録する。
製品変更は不要で、読み方の指定だけである。
B10 の観測 field に generation status 内訳を足す R11-N1 の提案がそのまま使える。

### U3【LOW】T316 の GGUF hash が記録されていない

T330 preflight は GGUF SHA-256 `03b74727...` を記録しているが、
T316 preflight には無い（走査で確認）。
したがって **T316 と T330 が同じ model file を使ったかを遡って照合できない。**

U1 の再構成を検証するうえで、model 差は排除しておきたい変数である。
現 provider は生きているので今から記録できるが、T316 については取れない。

**最小修正案**: preflight の必須項目に GGUF hash を入れる（T330 では実施済みなので、運用の固定化だけ）。

### R13-Q2 の位置づけ更新

R13-Q2（error body の byte 数記録）は「detail が取れなかったときのため」の提案だった。
**T344 では detail が 107/107 取れたため、今回は不要だった。**
提案の優先度は下がる。R15-6 で「Q2 を先に入れるべき」と書いたが、
**P1 が実際に効いた以上、その前提は変わった。** 後続設計のままでよい。

## R16-6. 結論

**T348 の調査は本 project で最良のものである。**
保存原本 107 件の実測、107 schema の実 DLL での parse 検査、
exact build の source 照合、対照 schema での切り分けを組み合わせ、
「JSON Schema の parse 失敗ではない」という否定まで含めて原因を確定している。
限界（vocab=null、実 chat grammar 再生成なし、HTTP 200 未検証）も自分で明示している。

**修正は正しい機構を、正しい code path で解いている。**
per-request override の実在、`deepseek` の有効性、
`enable_thinking` を bool で送る必要があることを実 source で確認した。
型 pin と `assertIs` により、もう 1 回 400 を踏む経路が塞がれている。
production 配線も fail-closed で、P2 のような「実装したが呼ばれない」状態にはなっていない。

**そして P1 が効いた。** T330 では 107 件から何も得られず、
T344 では 107 件すべてに理由が残り、そこから原因が特定された。

**残る唯一の構造的な問題は U1 である。**
T316 の 34 件成功が説明されないまま修正が入っている。
再構成は存在し、それが正しければ因果は閉じるが、**検証されていない。**
次回 launch 前に provider の実 argv を記録すれば自動的に決着する。
これは R14-S1 / R13-Q1 を閉じる作業と同じものである。

**Q1 は、もはや「記録が無いと困る」ではなく「これが無いと修正の成否を判定できない」項目になった。**

なお R10-F-A（schema 適合率）は依然として未解決である。
今回の修正で 400 が解消しても、その先に F-A が待っている。
U2 のとおり、両者が同じ見え方をする可能性があることに注意が要る。

## R16-7. 検証した内容と検証していない内容

**検証した**: D074 全文、
`PHASE6_SAMPLER_AND_COLLECTION_REPAIR.md` の該当節、
T348 handoff 全文、T353 最終 verdict、T354 handoff 全文、
T340 時点 snapshot との製品差分全 file（`backend.py` / `config.py` / `types.py` / runner と test 4 file のみ）、
`backend.py:313-318` の body 追加、
`types.py` の `LlamaCppStructuredOutputConfig` と `config_fingerprint` への反映、
`config.py` の検証、
runner の `_prepare_run` phase6 分岐 / bootstrap 転記 / broker child 復元 / fail-closed 分岐、
`_cleanup_phase6_wait_failure` の実装、
新 test の `assertIs` と None 側の key 非存在検査、
`llama_cpp_structured_output` の production 呼出し元全走査、
**exact build `093adb242` の保存 source**:
`common/chat.cpp:1195-1240`（`extract_reasoning` と grammar 構築）、
`common/chat.cpp:885-899`（`common_reasoning_format_from_name`）、
`tools/server/server-common.cpp:1290-1332`（per-request override と kwargs の dump/解釈）、
T307 / T316 / T330 / T344 private evidence の `PROVIDER_CALL_TERMINAL` 件数と
`backend_error_detail` の有無（metadata のみ、原文は本文へ転記せず）、
7 module 回帰の独立実行（431 passed / 1 deselected / 175 subtests）、
T316 preflight に GGUF hash が無いことの走査。

**検証していない**: synthetic completion test の独立実行（908 秒、T353 が照合済み）、
実 provider への HTTP 要求（未実施）、
修正後の HTTP 200 / schema 妥当性、
T316 provider の実 argv（process 消滅済み）、
U1 の再構成そのもの、
実 C++ chat grammar を再生成して実 vocab で prefix を投入する試験、
T344 の collector / recovery 経路の実測、
T351 / T352 / T355 handoff の全文、
`backend_error_detail` の原文（privacy 境界として意図的に読まない）、
R10-F-A に関する新情報（存在しない）。

---

# R17 — R16 採否と修正後 Stage B 準備（T356–T361）のレビュー

日付: 2026-09-15
対象: `Docs/ai/PHASE6_R16_REVIEW_ADOPTION.md`、
`Docs/ai/design/PHASE6_STAGE_B_R16_FREEZE_ADDENDUM.md`、
T356 / T357 / T359 / T360 / T361 handoff、
`logs/t359-stage-b/` の preflight projection・probe payload・probe contract、
`logs/t348-sampler/private-inspection.json` の template 検査結果、
稼働中 provider の実 command line（再取得）
判定: **採否と preflight は良質で、U1/U3 の手順は実際に実行され hash も一致した。
ただし probe が treatment のみで control を持たないため、
PASS しても「修正が効いた」ことを確定できない**

## R17-0. 私の U1 仮説は反証された。そして矛盾は強まった

R16-U1 で私は次の再構成を提示した。

> `enable_thinking=false` が CLI で渡っていれば template は `<think>` prefix を出さず、
> `reasoning_format=none` で grammar が空でも衝突が起きない。

**この仮説は採用されず、証拠で否定された。**

> T348 handoff と `private-inspection.json` の保存 template 検査では、
> thinking 無効時にも空の think block が残る。
> R16 の「enable_thinking=false なら prefix を出さない」という仮説は採用しない。

保存された検査結果を自分で確認した。

```
saved_template_checks[0]/disabled_branch_still_has_think_literal = True
saved_template_checks[0]/enabled_branch_has_think_literal       = True
template_literal_piece_counts/<think> = 107
```

**無効側の branch にも `<think>` literal が存在する。** したがって
`enable_thinking=false` でも generation prefix に `<think>` が入り、
`reasoning_format=none` の空 grammar とは同じく衝突する。
**私の再構成は成立しない。指摘は正しい。**

重要なのはその帰結である。**U1 の矛盾は解消されたのではなく、鋭くなった。**

R16-1 で確認した機構は request ごとに決定的で、確率的要素が無い。
T316 の preflight は `reasoning_format: none` / build `b10697-093adb242` /
context 8192 / jinja 有効を記録しており、T330・T344 と全項目一致する。
そして今、`enable_thinking` の違いという唯一の逃げ道も塞がれた。

**機構が完全なら、T316 は 35/35 失敗していたはずである。実際には 34 件が HTTP 200 を返した。**

（採否文書が「34 件を正常成功と呼ばない」「出力妥当性率と同一視しない」と釘を刺しているのは正しい。
T316 の内訳は DECISION 8 / OUTPUT_INVALID 26 である。
ただし U1 が問うているのは出力の質ではなく **HTTP 200 が返ったこと自体**であり、
`response_bytes` median 954 の実 body が 34 件存在した事実は動かない。）

## R17-1. R16 採否の線引きは正確である

`PHASE6_R16_REVIEW_ADOPTION.md` の採否表は、
**「採用する内容」と「採用しない断定」を各指摘ごとに分けている。**
外部指摘の扱いとして、これまで見た中で最も正確な形である。

| | 採用 | 不採用にした断定 |
|---|---|---|
| U1 | 差は未解明と明記し、次回 provider の実構成を保存・比較 | CLI thinking 差が原因と確定すること |
| U2 | 終了理由・token 数・本文状態・validation_code を分けて診断 | HTTP200 なら正常、空本文なら必ず OUTPUT_INVALID と無観測で確定すること |
| U3 | GGUF SHA-256 を必須確認し provider identity に結合 | 現 hash から T316 の model が同じと確定すること |

U2 の診断節も良い。
「現 backend は空文字・null・非文字列 content を
`RESPONSE_ENVELOPE_INVALID` として usage/finish_reason 取得より前に拒否する。
同 code は他の envelope 不正でも生じるため、code だけでは空本文の証拠にならない」
という指摘は、**私が U2 で示唆した診断手順より正確**である。
私は「`completion_tokens` と `finish_reason` を先に見る」と書いたが、
空本文はその 2 つを取得する前に弾かれるため、そのままでは機能しない場面がある。

## R17-2. U1/U3 の手順は実際に実行され、比較が成立した

R14-S1 / R16-U1 で「計画にはあるが packet に無い」と指摘した P2 の観測手順は、
T328 packet へ転記され、**T359 で実行された。**

`logs/t359-stage-b/preflight-observation.json`

| 項目 | 値 | 照合 |
|---|---|---|
| provider PID / creation | 24380 / `2026-09-15T03:38:17.5183200Z` | T330・T344 と同一 process |
| `command_observation` | `OBSERVED` | 取得成功 |
| `command_canonical_sha256` | `7d42b216…1776281` | **T344 の記録値と一致** |
| `gguf_sha256` | `03b74727…2b7e8` | **T330 の記録値と一致** |
| `environment_observation` | `NOT_OBSERVABLE` | 補完せず |
| build / context / slots | `b10697-093adb242` / 8192 / 4 | 一致 |

**R13-Q1 は閉じた。** 実装されて呼ばれなかった P2 が、運用手順として実際に使われ、
過去 run との hash 比較が成立している。R13 から 4 世代かけて閉じた。

そして副産物として、**実験設計が良くなった。**
provider が T344 と同一 process・同一 argv hash・同一 GGUF である以上、
次の run で結果が変われば、**変わったのは client 側の request だけ**である。
これは偶然そうなったのではなく、U1/U3 の手順を踏んだ結果として確認できた状態である。

## R17-3. probe 契約の質

`PHASE6_STAGE_B_R16_FREEZE_ADDENDUM.md` 第 6 節の probe 契約は厳密である。

- **既存 `_request_payload` serializer を使う。** 手書き HTTP body / curl / 別 serializer を禁止
- provider POST は最大 1 回、repair 0、retry 0、failure 後の自動再送なし
- PASS は HTTP 200 + body 全読了 + stream close + parse 完了 +
  exact `{"probe":"ok"}` + schema error 0 + usage 取得 + finish 適合 + terminal 確認の**全成立**
- FAIL と UNKNOWN のどちらでも game を起動しない。fallback・設定変更・provider 再起動へ自動移行しない
- probe PASS を game schema 全体の適合・品質・B01〜B11 の PASS とみなさない

生成された payload も確認した。`enable_thinking` は JSON boolean `false`
（R16-2 のとおり、文字列で送ると `server-common.cpp:1318-1320` で別の 400 になる）、
`reasoning_format` は `"deepseek"`、`strict: true`、537 bytes。
**R16 で確認した境界をすべて満たしている。**

game 内容を含まない合成 prompt で、schema も `{"probe": {"const":"ok"}}` の 1 field だけである。
T348 が game の 107 schema すべてを実 DLL で parse 成功させているため、
schema の単純さは今回の検証対象（prefix と grammar の衝突）を弱めない。

## R17-4. 指摘

| # | 内容 | 深刻度 |
|---|---|---|
| V1 | probe が treatment のみで control が無く、PASS しても因果を確定できない | **HIGH** |
| V2 | U1 の矛盾が鋭くなったまま、次の run が走ろうとしている | MEDIUM |
| V3 | 公開 projection の `argv_count` が provider の argv と誤読される | LOW |
| V4 | T360 handoff の「未到着」は既に stale | LOW |

### V1【HIGH】control probe が無い

現在の計画は **1 回の probe = 新 request 形（8 key）だけ**である。

これが PASS したとき言えることは
「**今この provider は、新 request 形に対して 200 を返す**」だけである。
言えないことが 1 つある。

**「同じ provider が、旧 request 形に対しては今も 400 を返す」**

この 2 つが揃ってはじめて、
「400 は request 形が原因であり、修正がそれを解いた」と言える。

そして R17-0 のとおり、**旧 request 形が今も 400 になるという保証は無い。**
T316 は記録上同一構成の provider で 34 件の 200 を得ており、
「400 は構成ではなく状態に依存する」可能性が排除されていない。
その可能性が生きている限り、treatment のみの probe は次の 2 つを区別できない。

- 修正が効いた
- 13:13 の時点と今とで provider の振る舞いが違う

**最小修正案**: probe を 2 回にする。順序は control → treatment。

control は **`llama_cpp_structured_output=None` で同じ serializer を通す**。
これは production が既定で使う経路そのものであり（`config.py:36`）、
手書き body でも別 serializer でも別 schema mode でもないため、
補遺の「既存 serializer 経路を使用する」制約に抵触しない。
payload は T330/T344 と同じ 6 key 形になる。

結果の読み方は次のとおりである。

| control | treatment | 結論 |
|---|---|---|
| **400** | 200 | **因果確定。** 修正が 400 を解いた。U1 は「T316 で何が違ったか」に縮小する |
| 200 | 200 | **修正は未検証。** 400 は現構成では再現せず、原因は別か状態依存。game を走らせる前に再検討が要る |
| 200 | 400 | 修正が有害。直ちに停止 |
| 400 | 400 | 修正が効いていない。game を走らせない |

**追加コストは HTTP 要求 1 回である。** T330/T344 の 400 は latency median 0.0 秒で、
生成に入る前に返る。GPU 時間はほぼ 0 で、承認済みの game 1 回とは別枠でもない。

補遺は「追加 probe、fallback、設定変更、provider 再起動へ自動移行しない」と定めているが、
これは **failure 後の自動リトライを禁じる条項**であって、
事前に設計された control を禁じる条項ではない。
control を PASS/FAIL の gate に入れず、**記録項目として実行する**なら判定式も変わらない。

game を 1 回消費する前に、**1 要求で「修正が原因を解いた」ことを確定できる。**
今の計画では、game が成功しても因果は推定のままになる。

### V2【MEDIUM】U1 の矛盾を抱えたまま次の run が走る

採否文書は U1 を「未解明と明記する」形で採用し、
「T316 の差の原因確定は、比較可能な当時の証拠が得られた場合に限る」
「未解明の T316 を解くための追加 run は自動で行わない」としている。
**判断としては正しい。** 過去の欠測を新観測で補完しない姿勢も一貫している。

ただし R17-0 で唯一の候補仮説が消えたことで、状況は変わった。
**現在の機構説明は、T316 の観測と両立しない。**
機構が完全なら T316 は不可能であり、T316 が事実である以上、機構は完全でない。

これは「未解明の過去」ではなく、**現在の因果理解の穴**である。

V1 の control probe は、この穴に直接効く。
control が 400 なら「T316 の時点で何かが違った」に縮小し、
control が 200 なら「機構説明が不完全」が確定する。
**どちらでも前進する。**

### V3【LOW】`argv_count` が provider の argv と誤読される

`preflight-observation.json` は `argv_count: 19` を記録している。
周囲の field が `command_ngl99` / `command_observation` /
`command_canonical_sha256` / `serving_files` と provider 一色のため、
**provider の argv 数と読める。**

実際には `prepare.py:65` の `len(operation["argv"])` で、
**次回 runner（`run_phase5_local_smoke.py`）の起動 argv の数**である。

provider の実 argv は 14 である。`CommandLineToArgvW` で確認した。

```
[0] C:\AIagent\llama-server.exe   [1] -m   [2] <model>
[3] --host  [4] 127.0.0.1  [5] --port  [6] 8080
[7] -ngl  [8] 99  [9] -c  [10] 8192  [11] --jinja
[12] --reasoning-format  [13] none            → argc = 14
```

値は間違っていない。**名前が誤解を招く。**
私自身、最初にこれを provider の argv 数と読み、
R15 で記録した command line が不完全ではないかと疑って再取得した。
独立 Reviewer が同じ誤読をすれば、存在しない不一致を追うことになる。

**最小修正案**: `runner_argv_count` に改名する。値と手順は変えない。

### V4【LOW】T360 の「未到着」は既に stale

T360 handoff（23:16）は
「T361 addendum、T359 preflight、private locator はいずれも現時点で未到着」
として `WAITING_FOR_T361_ADDENDUM` を返している。

実際には T359 handoff は 23:20、T361 addendum は 23:20、T361 handoff は 23:21 に存在する。
**T360 が先に書いたというだけで、欠落ではない。**

`CURRENT_STATE.md` は「T360/T361 審査中」と正しく書いているが、
handoff 本文だけを読むと成果物が欠けているように見える。
実害は無いが、**待機中の handoff が最終判定と同じ形式で残る**のは
これまで指摘してきた「記録が状態を誤らせる」型に近い。

**最小修正案**: 待機報告に観測時刻を明記する（`WAITING as of 23:16` の 1 語）。

### なお `EXTERNAL_REVIEW_LOG.md` の扱いについて

補遺は次のように書いている。

> `EXTERNAL_REVIEW_LOG.md` は R15 で個別 reconcile 対象とされた control log で、
> 8 source 置換には含めない。

**正しい扱いである。** 私はレビューのたびにこの file へ追記しており、
freeze 中に変化し続ける。製品差分として扱えば毎回 launch 阻害になり、
無視すれば control drift の検出が緩む。
別枠の control log として個別 reconcile する処理は、両方を避けている。

私の側でも、以後この file が freeze 判断に影響しないよう、
**追記は review 単位でまとめ、freeze 中の分割追記はしない。**

## R17-5. 結論

**採否文書は、外部指摘の扱いとして最も正確な形である。**
採用する内容と採用しない断定を分け、私の U1 仮説を証拠で否定し、
U2 では私の診断手順より正確なものを書いている。

**U1/U3 の手順は実際に実行され、R13-Q1 は 4 世代かけて閉じた。**
provider が T344 と同一 process・同一 argv hash・同一 GGUF であることが確認され、
副産物として「次の run で変わるのは client 側だけ」という良い実験条件が揃った。

**probe 契約は厳密で、R16 で確認した境界をすべて満たしている。**

**残る問題は V1 である。**
その良い実験条件を、treatment だけの probe では活かしきれない。
control が無いため、probe が PASS しても
「修正が効いた」のか「provider が 13:13 の時点と違う」のかを区別できない。
そして R17-0 で唯一の候補仮説が消えたことにより、後者は真剣に扱うべき可能性になった。

**HTTP 要求 1 回で、game を消費する前に因果を確定できる。**
control → treatment の順で 2 回投げ、control を判定式に入れず記録項目として残せばよい。

なお **R10-F-A は依然として動いていない。** probe が PASS し game が走っても、
その先に schema 適合率の問題が待っている。
U2 の診断手順が整備されたのは、そこへ向けた正しい準備である。

## R17-6. 検証した内容と検証していない内容

**検証した**: `PHASE6_R16_REVIEW_ADOPTION.md` 全文、
`PHASE6_STAGE_B_R16_FREEZE_ADDENDUM.md` の probe 契約節と freeze 節、
T356 / T357 / T359 / T360 / T361 handoff、
`logs/t359-stage-b/preflight-observation.json` 全 field、
`probe-contract.json` と `probe-payload.json` の実内容、
`logs/t359-stage-b/prepare.py` の `argv_count` 算出式と planned runner argv、
`logs/t348-sampler/private-inspection.json` の template 検査結果
（`disabled_branch_still_has_think_literal` 等）、
稼働中 provider の command line 再取得（152 文字）と
`CommandLineToArgvW` による argc = 14 の確認、
T344 の `command_canonical_sha256` および T330 の GGUF hash との照合、
T359 / T360 / T361 artifact の mtime、
`config.py` の `llama_cpp_structured_output` 既定値。

**検証していない**: probe の実行（未実施）、
control probe の結果（未実施・未計画）、
T316 の 34 件成功の原因、
`PHASE6_STAGE_B_R16_FREEZE_ADDENDUM.md` の 8 source 置換の現物照合
（T360 の審査対象であり重複を避けた）、
T361 handoff 全文、
private probe 原本と locator、
修正後の HTTP 200 / schema 妥当性、
R10-F-A に関する新情報（存在しない）。

---

# R18 — 修正版 Stage B（R7 / T362–T369）の結果レビュー

日付: 2026-09-16
対象: `T362_R17_DISPOSITION`、`T363_R7_RESULT`、`T364_FRESH_SESSION_REVIEW`、`T365_RESUMED_STAGE_B_TEST`、
run `T365B-20260915T171416287999Z` の private evidence（metadata のみ）と `summary.json`、
`ai_client/discussion/projection.py` の `token_proxy_units`、
`ai_client/llm/decision.py` の `parse_llm_output`
判定: **HTTP 400 は解消した。修正は実測で効いている。
しかし B08 の FAIL は単位の取り違えによる偽陽性であり、
schema 適合率は 400 を除去しても T316 とほぼ同じままである**

privacy: 原文は読まず、metadata と公開 summary の集計だけを扱った。

## R18-0. 400 は消えた

| | T330 | T344 | **R7** |
|---|---|---|---|
| provider call | 107 | 107 | **32** |
| HTTP 400 | **107** | **107** | **0** |
| `backend_error_code` | HTTP_STATUS 107 | HTTP_STATUS 107 | **ADMISSION_POISONED 1 のみ** |
| 実応答（`finish_reason=stop`） | 0 | 0 | **31** |
| accepted 発言 | 0 | 0 | **9** |

**`HTTP_STATUS` は 1 件も出ていない。** sampler / grammar 修正は実環境で効いた。

しかも provider は **T344 と同一 process**（PID 24380、argv hash・GGUF hash 一致、R17-2 で確認済み）である。
同じ provider instance が 13:13 には 107/107 失敗し、02:27 には 32 call 中 31 が terminal 成功した。
**変わったのは client の request だけである。**

R17-V1 で control probe を求めたのは、この因果を確定するためだった。
T362 はこれを実行必須条件としては不採用とした。
結果から見れば、**game 自体が n=32 の treatment arm として機能し、
control 1 回よりも強い前後比較になった。** V1 の必要性は事象に追い越された。

ただし T362 の留保は今も正しい。13 時間の間隔があり、
provider の内部状態変化を形式的に排除したわけではない。
「強く支持される」であって「隔離された」ではない。
またT362 が指摘した「control 要求が必ず生成前 400 で GPU 負担ほぼ 0 とは保証できない」は、
**私のコスト見積りに対する正当な訂正である。**

## R18-1. 私の U2 は空振りだった

R16-U2 で「`reasoning_format=deepseek` により thinking が出力予算を食い、
400 が空 content に化ける可能性がある」と書いた。**起きなかった。**

```
finish_reason: stop 31 / None 5   ← length は 0 件
completion_tokens: min 81 / median 135 / max 348   ← 512 に対して余裕
```

`length` が 1 件も無く、出力 token は上限の 7 割未満に収まっている。
**thinking が予算を使い切る事象は観測されなかった。撤回する。**

採否文書が U2 を「無観測で確定しない」形で採用し、
診断軸を分けて記録する手順にしたのは正しかった。
その手順のおかげで、**空振りであることが 1 回の run で確定できた。**

## R18-2. B08 の FAIL は単位の取り違えである【最重要】

独立判定は B08 を FAIL とした。

> B08: prompt token proxy 最大 8338、上限 8192。PROMPT_TOO_LARGE を 4 件観測

**しかし同じ run の `summary.json` に、実測値が並んで記録されている。**

| | proxy | provider 実測 | 比 |
|---|---|---|---|
| min | 6,752 | 1,304 | **5.18×** |
| median | 7,636 | 1,624 | **4.70×** |
| p95 | 8,295 | 1,928 | 4.30× |
| max | **8,338** | **1,931** | **4.32×** |

**実際の prompt token は最大 1,931 で、`n_ctx=8192` の 23.6% しか使っていない。**
context 圧迫は起きていない。

原因は `ai_client/discussion/projection.py:78-95` の proxy 定義にある。

```python
units += 1 if scalar.isascii() else len(scalar.encode("utf-8"))
```

**非 ASCII の 1 文字を UTF-8 byte 数（日本語なら 3 units）として数えている。**
実 BPE tokenizer は日本語 1–2 文字でおよそ 1 token なので、
**構造的に 3–6 倍の過大評価になる。** 実測比 4.3–5.2 倍はこれと整合する。

そしてこの proxy が `max_token_proxy_units = 8192` と比較されている。
この 8192 は **provider の `n_ctx` と同じ数値**である。
**proxy unit と token は別の単位であり、この比較は物理的な意味を持たない。**

実害は 2 つある。

1. **B08 の FAIL は偽陽性である。** 実使用率 23.6% を「上限超過」と判定している
2. **4 件（全 generation の 11%）が client 側で破棄された。**
   `PROMPT_TOO_LARGE` 4 件は provider へ送られていない。
   実 token では ~1,900 / 8,192 で、送れば通ったはずである。
   B01（game_end=false）と B06（再評価 0 件）にも寄与している可能性がある

これは R11 が B11 の `negative_boundary` に
「総 game token を 512 と比較しない」と先回りして書いた**単位混同と同型**である。
Stage B の oracle 設計はこの罠を 1 箇所では塞いでいたが、
proxy と n_ctx の比較は塞げていなかった。

**最小修正案**: `max_token_proxy_units` を n_ctx と切り離し、
**実測から較正する。** 較正データは既にある。
今回の 31 組（proxy, provider_prompt_tokens）から比を求め、
安全率を掛けた proxy 閾値を設定すればよい。
proxy 式そのものを直す必要はない（provider 非依存という設計意図は保てる）。
現在の実測比 4.3–5.2 に対し、8192 token を守るための proxy 閾値は
控えめに見ても 20,000 以上であって 8,192 ではない。

**製品の判定式を変えるので独立設計・承認が要るが、
これを直さない限り次の run でも同じ FAIL と同じ破棄が起きる。**

## R18-3. F-A は 400 を除去しても変わらなかった

| | T316 | **R7** |
|---|---|---|
| generation | 45 | 36 |
| DECISION | 8 | **9** |
| 適合率（応答ベース） | 8/34 = **23.5%** | 9/31 = **29.0%** |
| 初回のみ | — | 5/18 = **27.8%** |
| repair 成功 | 0 | 4 |
| `validation_code` | SCHEMA 13 / VALUE_NOT_OFFERED 13 | **SCHEMA 15 / VALUE_NOT_OFFERED 5 / OPTION_NOT_OFFERED 2** |

**HTTP 400 を完全に除去し、実応答を 31 件得ても、適合率はほぼ同じである。**

これは重要な確定である。**R10-F-A は sampler bug とは独立した別の問題であり、
400 の解消によって前進しない。** T330 以降「F-A は 1 ミリも動いていない」と
毎回書いてきたが、今回それが**実測で確定した。**

repair が 4 件成功したのは新しい（T316 では REPAIR_SUCCEEDED 0）。
R10 修正（16 KB 拒否の解消）の効果と見てよい。

### SCHEMA 15 の内訳が分からない — 4 度目の「理由を潰す」

`ai_client/llm/decision.py:253-282` の Phase 6 経路を読むと、
`DecisionValidationCode.SCHEMA` は**性質の違う複数の失敗に同じ code を付けている。**

```python
if not Draft202012Validator(_plain_json(projection.decision_schema)).is_valid(value):
    raise DecisionValidationError(DecisionValidationCode.SCHEMA)     # :264 送った schema 違反
_exact_keys(value, {"decision", "discussion"})                        # :265 追加の局所検査
...
except (TypeError, ValueError):
    raise DecisionValidationError(DecisionValidationCode.SCHEMA)      # :281 _validate_semantic_output 由来
```

**`:264` と `:281` は意味がまったく違う。**

- `:264` = **provider へ送った schema 自体を満たしていない。**
  `strict: true` の grammar 制約下では原理的に起きにくく、
  起きているなら **grammar が実際には効いていない**ことを意味する
- `:281` = 送っていない**追加の局所制約**を満たしていない。
  model は要求されていない条件で落とされている。
  grammar をどう強化しても改善しない

**現在の記録ではこの 2 つが区別できない。**
SCHEMA 15 が主に前者なら grammar の問題、後者なら**契約が過剰に厳しい**という話になり、
取るべき対策が正反対である。

これは R01-P0-1（43 箇所が `"corrupt"` 1 語に collapse）、
R10-M4（`PROMPT_REJECTED` が理由を残さない）、
R12-P1（HTTP error body 破棄）に続く **4 度目の同じパターン**である。

**最小修正案**: `SCHEMA` を最低 2 つに分ける
（例 `SCHEMA_CONTRACT` = 送った schema 違反 / `SCHEMA_LOCAL` = 局所追加検査）。
既存 code 名を変えたくなければ、`validation_detail` を 1 field 足すだけでもよい。

**保存済み原本だけで先に切り分けられる。** 実 LLM は不要である。
R7 の保存 response text に対して
`Draft202012Validator(decision_schema).is_valid(value)` を回し、
True/False の内訳を数えれば、それだけで方向が決まる。
**次に実行を消費する前にやる価値が最も高い作業である。**

## R18-4. Phase 6 の完了条件は 0 点である

```
accepted_text_count:        9
responsive_accepted_count:  0      ← 完了条件そのもの
semantic_requirements_met:  false
pre_vote_reassessment_count: 0
game_end:                   false
```

Phase 6 の完了条件は「前の発言を受けた会話が成立する」である。
**9 件の発言が受理され、そのうち前の発言を受けたものは 0 件だった。**

400 が解消し、実際に会話が始まり、9 件が場に出たうえでの 0 である。
**これは failure ではなく、はじめて得られた本来の測定値である。**
T316 は 8 件の accepted があったが manifest 欠落で母集団審査が成立せず、
T330/T344 は 0 件だった。**今回はじめて「会話の質」の実測点が 1 つ得られた。**

同時に、Phase 6 の残作業が何であるかもはっきりした。
400 でも sampler でも timeout でもなく、**prompt と出力契約の設計**である。

## R18-5. 指摘

| # | 内容 | 深刻度 |
|---|---|---|
| W1 | B08 の判定が proxy unit と token を同一視しており、偽陽性かつ実害がある | **HIGH** |
| W2 | `SCHEMA` が意味の違う失敗を 1 code に潰しており、F-A の方向が決められない | **HIGH** |
| W3 | 進行報告の誤り（RUNNING_OBSERVED）が一度公開された | LOW |
| W4 | run label が複数存在し、どれが正本か外部からは判別しにくい | LOW |

W1 / W2 は本文のとおり。

### W3【LOW】進行報告の訂正は適切だった

T363 は次を自己申告している。

> 一時的な RUNNING_OBSERVED は、launcher 親 Python を内部 game runner として表示した
> 不正確な補助観測だった。17:32:24Z の観測は内部 runner 終了 17:32:03Z の後だった。
> Main は一度「2:32 時点で動作継続」と報告したが、その後明示訂正した。

**隠さず、失われた snapshot を再生成せず、会話の tool 返却に残った事実も明記している。**
処理としては正しい。指摘は「そもそも親 process を runner と誤認する観測が
公開 status に出た」という 1 点だけで、
`_cleanup_owned_shielded` が label / pid / returncode を持っているのだから、
進行観測も同じ identity 集合で照合すべきである。

### W4【LOW】run label が紛らわしい

`logs/phase6-private-evidence/game/` には `T365B-` で始まる leaf が 11 個ある。

```
T365B-20260915T152522927556Z-d535f350-controller / -review
T365B-20260915T153554671443Z-c28177cf-controller / -review
T365B-20260915T171416287999Z            ← 実データはここだけ
T365B-20260915T171416287999Z-controller / -review
T365B-20260916T000000000000Z-controller / -review
T365B-20260916T001000000000Z-controller / -review
```

**generation record を持つのは 1 つだけ**で、残り 10 は空の controller/review leaf である。
T369 が「準備 label の UUID 末尾」を修正した経緯を踏まえれば残骸だと分かるが、
**外部から見ると、どれが正本かは中身を開くまで分からない。**
既存 leaf を消さない方針は正しいので、
**正本 label を 1 箇所に明記する**（freeze か summary に `canonical_run_label`）だけでよい。

## R18-6. 結論

**修正は効いた。** 同一 provider process に対して、107/107 の HTTP 400 が 0 になり、
31 件の実応答と 9 件の accepted 発言が得られた。
R09 の timeout、R10 の 16 KB 拒否、R12/R16 の sampler 衝突という 3 つの障害は、
すべて特定され、修正され、実測で解消されている。

**そして本丸が姿を現した。**
400 を除去しても schema 適合率は 29%（T316 は 23.5%）で、
完了条件である responsive な発言は **9 件中 0 件**だった。
Phase 6 に残っているのは prompt と出力契約の設計であり、
**実ゲームの反復では前進しない。**
T363 §7 が「実ゲームの繰り返しから保存原本の限定調査へ経路を切り替える」と
書いているのは正しい判断である。

**次に実行を消費する前にやるべきことが 2 つある。どちらも実 LLM を使わない。**

1. **W2 の切り分け。** 保存済み R7 response に対して
   `Draft202012Validator(decision_schema).is_valid()` を回し、
   SCHEMA 15 件のうち「送った schema 違反」と「局所追加検査」の比を出す。
   F-A の対策が grammar 側か契約側かが、これだけで決まる
2. **W1 の較正。** proxy 閾値を実測比から決め直す。
   放置すれば次の run でも 4 件前後が理由なく破棄され、B08 は再び FAIL する

**R10-F-A は依然として未解決であり、いまや Phase 6 の唯一の本質的な残件である。**
これまで毎回「F-A は動いていない」と書いてきたが、
今回それが「400 とは独立した別問題である」として**実測で確定した。**
遠回りに見えた 400 の解消は、F-A を単独で観測できる状態を作るために必要だった。

## R18-7. 検証した内容と検証していない内容

**検証した**: `T362_R17_DISPOSITION` の採否表全体、
`T363_R7_RESULT` 全文、`CURRENT_STATE.md` の authorization 節、
run `T365B-20260915T171416287999Z` の generation record を metadata で全走査
（status / finish_reason / attempt_ordinal / validation_code / backend_error_code /
prompt_rejection_code / prompt_tokens / completion_tokens / prompt_bytes）、
同 run の `summary.json` の `prompt_token_proxy` / `provider_prompt_tokens` /
`prompt_bytes` / `prompt_rejection_counts` / `generation_status_counts` /
`accepted_text_count` / `responsive_accepted_count` / `semantic_requirements_met` /
`pre_vote_reassessment_count` / `game_end`、
`ai_client/discussion/projection.py:78-95` の proxy 式と `:47` の閾値、
`ai_client/llm/decision.py:253-282` の Phase 6 parse 経路と SCHEMA の発生箇所、
`parse_llm_decision` との経路差、
`logs/phase6-private-evidence/game/` の `T365B-` leaf 一覧と各 leaf の record 有無、
T316 / T330 / T344 との数値比較。

**検証していない**: 原文（`prompt_json` / `response_text` / accepted text）、
`T364_FRESH_SESSION_REVIEW`（34 KB）の全文と B01–B11 各項の根拠、
`T365_RESUMED_STAGE_B_TEST`（21 KB）の全文、
probe 原本、
`game-r7-final-review.json` の判定内訳、
TERMINAL_EXPIRED → ADMISSION_POISONED → PROVIDER_QUIESCENCE_UNKNOWN の経路、
`_validate_semantic_output` の具体的な制約内容、
SCHEMA 15 件の実際の内訳（W2 はその切り分けを提案するもので、実施はしていない）、
T366–T369 の差分、実 provider への要求。

---

# R19 — R18-W2 の切り分け実施：F-A の正体

日付: 2026-09-16
対象: run `T365B-20260915T171416287999Z` の generation 原本 22 件（`validation_code` 非 null）、
`ai_client/llm/decision.py`、`ai_client/discussion/model.py:949-957`、
各 record の `prompt_json.output_schema`
判定: **HTTP 要求は不要だった。保存原本だけで決着した。
「schema 適合率 23.5% / 29%」は誤称である。
model が出した 22 件はすべて、送った schema を満たしている**

privacy について: 本節は `response_text` と `prompt_json.output_schema` の原文を読んだ。
これまで意図的に避けてきた読み方だが、W2 の切り分けはこれ無しには不可能で、
**外へ出すのは件数と enum 名・制約名だけ**とした。
発言本文、player 名、game 内容は 1 文字も本文へ転記していない。

## R19-1. 結論 — 送った schema の違反は 0 件だった

`validation_code` が付いた 22 件すべてについて、
保存 response を `Draft202012Validator(prompt_json.output_schema)` にかけた。

| `validation_code` | 送った schema を満たす | 違反 |
|---|---|---|
| SCHEMA | **15** | **0** |
| VALUE_NOT_OFFERED | **5** | **0** |
| OPTION_NOT_OFFERED | **2** | **0** |
| 合計 | **22** | **0** |

**22 / 22 が、provider へ送った JSON Schema を満たしている。**
JSON parse 失敗も 0、top-level key 集合も 15/15 が `('decision','discussion')` で正しい。

R18-W2 で挙げた 2 つの可能性のうち、
「`:264` 送った schema 自体の違反（= grammar が効いていない）」は **0 件**だった。

**grammar 制約は正しく効いている。**
`strict: true` の GBNF が期待どおり出力を拘束しており、
model は**与えられた契約を 100% 満たしている。**

したがって「schema 適合率 23.5% / 29%」という言い方は正しくない。
実際に起きているのは次のことである。

> **送っていない制約で落としている。**

## R19-2. 15 件の SCHEMA は全件 `_parse_proposal` で落ちている

`decision.py:276` の `_parse_proposal` を保存値に対して直接適用した。
**15/15 が `DiscussionValidationError` になる。** 内訳は次のとおり。

| 局所制約 | 件数 | 送った schema で表現できるか |
|---|---|---|
| `reaction trigger must be chat evidence` | **10** | **できる** |
| `proposal option_id nullability must match decision_kind` | 4 | できる（`if`/`then` または `oneOf`） |
| `relation endpoints must differ` | 1 | できない |

**14 / 15 は schema で表現できる制約である。**

### 支配的な 10 件の中身

`ai_client/discussion/model.py:949-957`

```python
@dataclass(frozen=True)
class ReactionAssessment:
    trigger: EvidenceRef
    ...
    def __post_init__(self) -> None:
        if not isinstance(self.trigger, EvidenceRef) or self.trigger.record_kind is not EvidenceRecordKind.CHAT:
            raise DiscussionValidationError("reaction trigger must be chat evidence")
```

**`reaction.trigger` の `record_kind` は CHAT でなければならない。**

一方、送っている schema の該当箇所は次のとおりである。

```json
"trigger": { "$ref": "#/$defs/evidence_ref" }
```

**汎用の `evidence_ref` をそのまま参照しており、`record_kind` を CHAT に限定していない。**

そして 10 件すべてで、model が実際に出した trigger は

```
{"order": …, "record_kind": "known_unmodeled", "visibility": …}
```

**`known_unmodeled` は送った schema が許している legal な enum 値である。**
model は許された選択肢を選び、client がそれを拒否している。

## R19-3. これは Phase 6 の完了条件そのものである

R18-4 で挙げた測定値を再掲する。

```
accepted_text_count:        9
responsive_accepted_count:  0
```

完了条件は「前の発言を受けた会話が成立する」であり、
`reaction.trigger` は**まさに「どの発言を受けたか」を指す field** である。

**model は反応しようとしている。**
36 generation のうち `reaction` が非 null だったのは、
SCHEMA 失敗 15 件の中だけでも 10 件ある（残り 5 件は `reaction: null`）。
その 10 件が、**trigger の record_kind が CHAT でないという理由だけで丸ごと破棄されている。**

`responsive_accepted_count: 0` と この 10 件の拒否は、**同じ現象の表と裏**である。

Phase 6 が止まっている理由は
「model が前の発言を受けた会話をできない」ではなく、
**「できた反応を、送っていない制約で捨てている」**可能性が高い。

## R19-4. 残る 7 件（VALUE_NOT_OFFERED 5 / OPTION_NOT_OFFERED 2）

こちらは性質が違う。

| code | decision.kind | 件数 |
|---|---|---|
| VALUE_NOT_OFFERED | `chat` | 4 |
| VALUE_NOT_OFFERED | `co_declare` | 1 |
| OPTION_NOT_OFFERED | `none` | 2 |

**提示されていない選択肢を選んだ**ケースで、これは schema だけでは塞げない
（提示 option は game state ごとに変わるため、schema に列挙しない設計は妥当）。
ただし **`option_id` の enum を毎回 schema へ動的に埋め込めば grammar で塞げる**。
`_decision_schema(options, max_text)` は既に options を受け取っているので、
そこまで到達している可能性もある。今回は確認していない。

7 件は「model の選択ミス」と呼べるが、**22 件中 7 件（32%）である。**
残り 15 件（68%）は model のミスではない。

## R19-5. 指摘

| # | 内容 | 深刻度 |
|---|---|---|
| X1 | `reaction.trigger` が汎用 `evidence_ref` を参照しており、CHAT 限定を送っていない | **HIGH** |
| X2 | 「schema 適合率」という語が、実態と逆の印象を与えている | **MEDIUM** |
| X3 | `_parse_proposal` の 3 種の失敗が `SCHEMA` 1 code に潰れている | MEDIUM |

### X1【HIGH】最小修正は schema 1 箇所

`$defs` に `chat_evidence_ref`（= `evidence_ref` に
`"record_kind": {"const": "CHAT"}` を加えたもの）を定義し、
`reaction.trigger` の `$ref` をそれに差し替える。

これで **GBNF が `known_unmodeled` を物理的に出せなくなる。**
今回の 10 件は原理的に発生しなくなる。

`_parse_proposal` 側の検査は残してよい（fail-closed の二重化）。
製品の判定式・game 規則・B01〜B11 は変わらない。

**実 LLM を使わずに設計・実装・synthetic 検証まで完了できる。**
効果の確認には実 game が要るが、**それは 1 回で足りる。**

### X2【MEDIUM】呼び名が判断を誤らせている

R10-F-A 以来「schema 適合率 23.5%」と呼ばれ、
私も R11 以降その語を使ってきた。**この語は model の能力不足を含意する。**
実際は送った schema への適合率は **100%** である。

正確には **「局所契約適合率」** あるいは
**「送っていない制約による棄却率」** と呼ぶべきものだった。
名前が対策の方向を model 側（prompt 強化、モデル変更、token 増）へ誘導しかねない。
**必要なのは契約の送り方の修正である。**

### X3【MEDIUM】5 度目の「理由を潰す」

`_parse_proposal` の 3 種の失敗はすべて `DecisionValidationCode.SCHEMA` になる
（`decision.py:281` の `except (TypeError, ValueError)` 経由）。
`DiscussionValidationError` は message を持つが、**その message は記録されない。**

R01-P0-1 / R10-M4 / R12-P1 / R18-W2 に続く **5 度目**である。
`validation_detail` を 1 field 足せば、次回は本節の分析が
保存 record を数えるだけで済む。**原文を読む必要がなくなる**ので、
privacy 境界の面でも望ましい。

## R19-6. 結論 — HTTP 要求は不要だった

質問への直接の答えは **「1 回も要らなかった」** である。
必要だったのは保存済み原本に validator をかけることだけで、
provider には一切触れていない。

得られたのは次の 3 点である。

1. **grammar は効いている。** 送った schema の違反は 22 件中 0 件
2. **失敗の 68%（15/22）は、送っていない制約による棄却**であり、
   うち 14 件は schema で表現できる
3. **支配的な 10 件は `reaction.trigger` の CHAT 限定を送っていないこと**に起因し、
   それは Phase 6 の完了条件が測っているものそのものである

**次に実 game を消費する前に X1 を直すべきである。**
今の状態で再実行しても、model は再び `known_unmodeled` を選び、
再び 10 件前後が捨てられ、`responsive_accepted_count` は再び 0 になる可能性が高い。

R18-W1（proxy 較正）と X1 は、どちらも実 LLM 0 回で直せる。
この 2 つを入れてから 1 回走らせるのが、いま最も効率のよい順序である。

**なお本節は「直せば完了条件を満たす」ことを証明していない。**
反応が受理されたあとに、その反応が意味として成立しているかは別の測定である。
B01（game_end=false）と B06（再評価 0 件）も未解決のまま残る。

## R19-7. 検証した内容と検証していない内容

**検証した**: `validation_code` 非 null の 22 件全件について、
保存 `response_text` の JSON parse、
`Draft202012Validator(prompt_json.output_schema)` による適合判定、
top-level key 集合、`_parse_proposal` の直接適用と例外 message、
15 件の `reaction.trigger` の key 集合と `record_kind` 値、
送った schema の `discussion.properties.reaction` 全文、
`ai_client/discussion/model.py:949-957` の `ReactionAssessment.__post_init__`、
`decision.py:253-282` の SCHEMA 発生経路、
7 件の `decision.kind` と key 集合。

**検証していない**: 発言本文・game 内容の意味（読んだが本文へ転記せず、判定にも使っていない）、
`_validate_semantic_output` の内容、
`_decision_schema` が `option_id` の enum を動的に埋めているかどうか、
X1 を直した場合に `responsive_accepted_count` が実際に 1 以上になるか、
B01 / B06 の原因、
`reaction` が null だった 5 件の理由、
DECISION 9 件（成功側）の内容、
実 provider への要求（0 回）。
