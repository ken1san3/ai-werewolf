# Phase 6 Master Test Plan — FREEZE候補

Plan ID: P6-PLAN-20260914-R1
Status: APPROVED / FROZEN
作成: Main Integrator、2026-09-14。権限: D073、ユーザー運用変更指示、D072。

**ユーザー実行依頼とT298独立APPROVEDを受け、TEST PLAN FREEZE。Stage Aを一巡する。**
ユーザーの修正依頼によりT296で実装・予定nodeを一括補正する。旧T293の取消cycleは復活させない。
本書と `PHASE6_MASTER_TEST_CATALOG.csv` が全Test IDの一覧である。後から担当者判断で項目を増やさない。
R0はlogs/r03-plan-revision/plan-r0.mdに保存。実行batchもPHASE6_MASTER_TEST_BATCHES.csvで固定する。

FREEZE記録: T298の独立承認をMainが照合。承認時内容hashはlogs/t299-master-run/frozen.json。
内容/CSV/sourceを固定し、Stage B実起動は別途承認を維持する。

### 最新実行条件（以下の旧候補時の停止/修正記述より優先）

2026-09-14の「これを実行」「完遂が不可能となるエラーの場合は終了」「それ以外は完遂までコード変更禁止」を適用する。
T298の一回の独立計画確認→Main FREEZE→独立Tester T299の固定Stage Aを実行する。
製品・test・設定・fixture・実行scriptの変更/新規作成を禁止する。raw/結果/管理文書だけを記録する。
通常assertion FAILは記録し、コードを変更せず後続項目を続ける。
起動不能、crash、timeout、後続にも影響する環境error、source変化、漏洩/破壊、所有cleanup不能等で
予定を安全に完遂できなくなった場合は停止し、raw保全・所有cleanup・未実行項目の集計を行う。
原因不明の実行ERRORは安全側に停止する。旧「timeout後に次batchへ」の記述/CSV欄をこの条件で上書きする。
同じ条件は全batchへ適用し、timeout後の分割/延長/再実行はしない。
今回の実行中・エラー停止後の自動Repair Cycleは禁止。修正には結果提示後の新たなユーザー指示が必要。
実LLMは共通1回を計画上限とし、追加1回は今回承認しない。Stage Bは既存どおり実起動直前に
モデル/条件/token/retention/512対処/rerun禁止を提示して明示承認を得る。一般的な今回の実行依頼だけでは起動しない。
合格式・既存Test ID・module/marker・900/1260秒・privacy境界は変更しない。
以下の未承認/未起動の記述は候補作成時の履歴。現在の状態は冒頭StatusとCURRENT_STATEに従う。

## 1. 対象と現時点

HEAD: 71607b14b3b06aade2f4c00b95710f562005d1d6、dirty main、commitなし。
現在のコード・設定・文書1,020file/8,995,866bytesとGit状態を
`logs/phase6-test-plan-reset-20260914/snapshot/`、同`git-status.txt`、`tracked-diff.patch`へ保存した。
これはローカル保存であり、D:外部backup更新ではない。private rawは従来位置に残し移動・再検証しない。

T293初回は180 PASS/19 FAIL/35 ERROR、所有監視UNKNOWN。再試験は留保completionを誤起動して強制停止、
pytest exit未取得、既知pytest＋server/broker/9clientの12 PID残存0という担当報告を受領した。
途中結果を新PlanのPASSへ流用しない。旧T252 FAIL/原private61欠落も不変。

既存50 test moduleを実行せずASTで読み、関数・method単位884定義をCSVへ列挙した。
parameter/subtestは既存sourceで宣言された全値を含むため、884はpytest実行件数ではない。
`tests/conftest.py` の明示completion集合と直接markerを照合した。旧Phaseの重いcompletion21定義は対象外。
それらは削除せず、今回の主要回帰は既存non-completion全体とPhase6の専用completion一つで扱う。
R03外部ReviewerはR0についてcollect-only 1,207items/905基底node、catalog884＋旧completion21、
completion22/non-completion1,185、集合差異0を報告した。Mainの再実行結果とは区別して採用する。
R1最終sourceのMain collectionはexit 0、1,227 items / 911基底node（0.88秒）。
completion22 / non-completion1,205、catalog890＋旧completion21、集合差異0。
R0の884 Test IDを維持し、予定の6基底nodeだけ追加した。items増分20は6新node＋既存診断matrixの14増。
実試験は0件。収集記録と最終source hashはlogs/r03-plan-revision/に保存した。

## 2. 判定・共通実行条件

### G1補完 — A16のFREEZE前参考計測（2026-09-14）

ユーザーのG1修正依頼により、新profileの代表contentをT297で一度だけoffline計測した。
200文字600bytesの同一合成日本語本文を持つANSWER/REBUTTAL/OPINION_CHANGE/RELATION_HYPOTHESISと
8対照形状をcompact/indentedで計24文字列確認し、initial/repair parser受理、content全24件≤512。
新境界本文の最大482 token、全体最大は本文なしpre-voteの510 token。512/200/600を維持する。
詳細・hash・raw linkageは`handoffs/tasks/T297_PHASE6_G1_PROFILE_COUNT.md`。
旧96bytes corpusだけを新profileの根拠とする不足は補完したが、全許容出力やprovider非content費用の
十分性を証明したわけではない。後者はUNKNOWN、pre-voteのcontent余裕2 tokenも留保する。
既存length拒否/repair最大1回/実game承認を維持し、実ゲーム成功を主張しない。
A16本体のpytest実行とは区別する参考証拠であり、A21・新batch・tokenizer再実行を追加しない。
計画は引き続きNOT APPROVED / NOT FROZEN。同じ計画のレビューへ補完結果を戻す。
G1補完だけのfresh独立確認はAPPROVED（`handoffs/tasks/T297_G1_INDEPENDENT_REVIEW.md`）。
この承認をMaster Plan全体の承認や実試験PASSへ拡張しない。

### 共通条件

- 各IDはPASS/FAIL/BLOCKEDのいずれか。未実行はBLOCKED、未収集・想定外skipもPASSにしない。
- PASSは表の条件と対応nodeの全assert成立。FAILは条件不成立・例外・timeout。
- CSVの各IDはgroupの対象/目的/PASS/FAIL/blocker欄と、自身のnode/fixture/全parameterを継承する。
  親groupは子IDの全結果を集計する単位であり、追加の試験・review層ではない。
- groupの「最悪値」で子全件のblocker値を決めない。特にA15はFAILした子IDごとに
  `result`、`Phase completion blocker: YES/NO`、観測された影響と判断理由を記録する。
  機能回帰と文書/運用だけの失敗を同一blockerへ合算しない。CSVの予定blocker値も各子へ展開する。
- group集計はPASS/FAIL/BLOCKED件数とblocker YESの子ID一覧で表す。
  文書/運用だけのNOを理由に全579件やPhase6をBLOCKEDとせず、重大機能FAILをNOへ薄めない。
- YESは明示機能または重大regressionのblocker。条件付き項目は表の条件だけで判断する。
- 通常非管理者Windows/CPython3.13を使用。TEMP/TMPと既存ACL/securityを変更しない。
- basetempは `logs/phase6-test-work/<run>-<batch>/` の専有領域。親を先に作り、証拠baseとは重ねない。
  stdout/stderr/resultは開始時から `logs/phase6-private-evidence/synthetic/<task>-<utc>/`。
  実gameは同baseの `game/<task>-<utc>/`。既存helperの文法を使い、新schemaは作らない。
- raw、簡単なcommand/exit/result、必要なhash/既存manifestを残す。追加のprovenance監査やsealのsealを作らない。
- 実行中にcode/testを変更しない。`-x`/`--maxfail=1`を使わず、通常FAILも全計画項目を集計する。
  重大安全問題で危険な依存先を実行できなければ、そのIDをBLOCKEDにして安全な残りを続ける。
- Windows privateの既知sandbox拒否はretryせず、通常の許可された実行環境を用いる。
- Stage Aは§3の8通常batchを各900秒、独立したPhase6 fixture batchを内側1200秒/外側1260秒で固定する。
  900秒以内の完了を実測済みとは主張しない。timeoutはbatch失敗として残し、未実行の子IDをBLOCKEDで記録して
  次の安全な固定batchへ進む。時間超過を理由にその場で分割/延長/再実行しない。

## 3. Stage A — 既存deterministic / synthetic

通常batchは下表とPHASE6_MASTER_TEST_BATCHES.csvのexact module集合を順に実行する。
`python -m pytest <そのbatchのmodule列> -m "not completion" -q -p no:cacheprovider --basetemp <専有領域>`
を使う。`tests`全体を1,185件/900秒として一括起動するR0案は撤回した。
最終freezeの収集対象はCSVと一致させる。A16–A20の事前列挙した不足検証を同じ既存moduleへ組み込む場合も、
コード・入力をFREEZE前に確定する。通常batchとcompletionを同じ起動へ混在させない。

| Batch ID | 固定内容 | 上限秒 |
|---|---|---|
| A-B01 | short_chat / local_smoke / semantic_output | 900 |
| A-B02 | discussion_context / discussion_state / memory_projection / discussion_transaction | 900 |
| A-B03 | reaction_semantics / pre_vote_reassessment / runtime / semantic_completionのnon-completion | 900 |
| A-B04 | private_review / evidence_retention | 900 |
| A-B05 | core/content/protocolの既存回帰 | 900 |
| A-B06 | network / Phase2・3のnon-completion回帰 | 900 |
| A-B07 | Phase4・5の残りnon-completion回帰 / runtime_capabilities | 900 |
| A-B08 | ai_status / long_regressionの既存単体試験 | 900 |
| A-B09 | P6-A12のexact completion node一つ | 1260（内側1200） |

全member moduleはbatch CSVに列挙する。実測を得るためだけの事前試験は追加しない。
この分割・marker・上限もFREEZE対象に含む。通常8batchは重複moduleなし、A-B09だけが別markerの一件。

| Test ID / 定義数 | 対象・目的 / 必要な既存テスト | 入力 | PASS条件 | FAIL条件 | 区分 | completion blocker |
|---|---|---|---|---|---|---|
| P6-A01 / 19 | profile・旧短文互換 / test_phase5_short_chat.py | 旧profile、新200/600/20–120、境界JSON | 旧80/96/5–30/96を維持、新型を受理、length拒否・repair最大1 | 設定漏れ、旧値変更、過剰repair、不正JSON通過 | deterministic | YES |
| P6-A02 / 45 | runner/bootstrap・既存Q8互換 / test_phase5_local_smoke.py | 既存fake process/settings/manifest | 既存親子設定・秘密非出力・cleanup契約成立 | 設定/秘密/manifest/主要互換破壊 | deterministic | YES |
| P6-A03 / 27 | context/privacy境界 / test_phase6_discussion_context.py | 公開/許可private/不正context、境界サイズ | 許可contextだけ受理、権限外/改変/過大を拒否 | 権限逸脱、不正入力受理、正常入力破壊 | deterministic | YES |
| P6-A04 / 48 | belief/state/履歴 / test_phase6_discussion_state.py | 記憶更新・再接続・visibility・replay | bounded stateと正しい更新/無効化、秘密を昇格しない | 不正commit/漏洩/上限超過/状態破壊 | deterministic | YES |
| P6-A05 / 11 | bounded memory/projection / test_phase6_memory_projection.py | 既存one-under/equal/over、記憶集合 | サイズ/件数上限、欠落表示、必要情報の選択契約成立 | 上限逸脱、private混入、正常projection破壊 | deterministic | YES |
| P6-A06 / 12 | semantic schema / test_phase6_semantic_output.py | QUESTION/ANSWER/REBUTTAL/OPINION_CHANGE等全既存vector | closed schema、出典/相手/新旧判断の対応を検証 | 不正proposal受理、schema/参照/visibility破壊 | deterministic | YES |
| P6-A07 / 32 | transaction / test_phase6_discussion_transaction.py | stage/commit/abort、stale/cancel、全既存mutation | atomic commitと正確な相関、取消時はcommitなし | 二重送信、不正commit、相関破壊 | deterministic | YES |
| P6-A08 / 8 | reaction・CO / test_phase6_reaction_semantics.py | prior speech、CO、deadline | reaction成立、CHAT最大2、CO別経路 | 応答起点の破壊、CHAT超過、CO混同 | deterministic | YES |
| P6-A09 / 18 | pre-vote機能 / test_phase6_pre_vote_reassessment.py | 評価更新・deadline・null/不正入力 | 既存再評価契約成立、権限外判断を拒否 | 再評価機能破壊、不正な更新 | deterministic | YES |
| P6-A10 / 38 | runtime/lifecycle / test_phase6_runtime.py | current更新、reconnect、close、private入力 | 制御連携・終了・入力境界が成立 | 進行不能、情報漏洩、主要runtime回帰 | deterministic | YES |
| P6-A11 / 23 | adapter・semantic population / test_phase6_semantic_completion.pyのnon-completion | 合成receipt/shard/contextと不正linkage | accepted全件を正しく照合し不正を拒否、manifest一回 | 欠落/誤分類/二重公開/不正なPASS | synthetic | YES |
| P6-A12 / 1 | 9-client fixture / 同file::test_p6f_nine_client_semantic_completion | Phase6SemanticBackend、standard_9、seed8625 | game_end、responsive≥1、CHAT≤2、owned cleanup0、rawがpytest外 | 未完走、必要項目欠落、残存、危険な漏洩 | synthetic | YES |
| P6-A13 / 24 | private review / test_phase6_private_review.py | 公開/非公開CHAT、CO、schema/反復/破損の既存vector | private CHATを正しく含め、誤PUBLIC・漏洩・反復を拒否、診断はredacted | 誤受理、private診断漏洩、D072外のhidden gate | synthetic | YES（provenanceの追加厳密性だけならNO） |
| P6-A14 / 5 | 現行保全helper / test_phase6_evidence_retention.py | 新syntheticと専有pytest作業領域 | 既存の終了後/cleanup後保持、base重なり拒否 | 原本消失・privacy境界破壊・正規利用不能 | synthetic | YES（追加管理改善だけならNO） |
| P6-A15 / 579 | 主要既存回帰 / CSV記載の残りnon-completion | 既存core/network/controller/Phase4/5/runtime fixture全体 | 主要機能の既存assert成立、Phase5/Q8互換 | 既存assert不成立。重大機能回帰はblocker | deterministic | 条件付き：重大回帰YES、文書/運用便利さのみNO |

全Stage Aの実LLMゲーム数は0。A12は独立したexact node一つを一度だけ実行し、
同じファイル名を通常batchへ無条件指定してcompletionを誤起動しない。
T293の中断fixtureは旧cycleの中断履歴であり、将来の新Plan一回を実行済みともPASSとも数えない。
新Plan自体がまだ未承認なので、現在はその一回を起動しない。

## 4. Stage A — FREEZE前に内容を確定する既知の不足範囲

以下は実行後に派生した新taskではなく、今回のMaster Planにあらかじめ列挙する5修正の残り検証である。
T296とMainの限定補完で実装した。A16–A20は既存module内のnodeへ対応し、追加の独立実行単位ではない。
以下の全予定nodeをR1 collectionで確認済み。実際のassert成立はFREEZE後の試験で判定する。

不足範囲のnode対応を次で確定する。新規6nodeはcatalogの既存module groupに追加し、既存IDは改番しない。
FREEZE後は担当者判断で増設しない。

- A16: test_phase6_discussion_profile_defaults_bounds_and_runtime_types（既存）と
  test_phase6_discussion_profile_prompt_parser_boundary（test_phase6_semantic_output.pyへ統合）。
  入力は200/201chars、600/601bytes、bool、min>max、各設定の0/1/上端/上端+1、旧profile既知値。
- A17: test_phase6_bootstrap_effective_profile_and_rejects_malformed（test_phase5_local_smoke.py）。
  入力matrixは次表の全型/値、Phase6有無、親/再構築/ready fingerprint一致・不一致、昼夜秒数。
- A18: test_phase6_evidence_path_partition_and_legacy_compatibility（test_phase6_evidence_retention.py）と
  test_phase6_runner_rejects_invalid_game_output_path（test_phase5_local_smoke.py）。
  game/synthetic、valid/invalid task・UTC、同名衝突、base外/pytest重複、旧省略形の有限matrix。
- A19: test_phase6_population_limit_and_summary_stop と test_phase6_zero_pre_vote_is_diagnostic
  （test_phase6_semantic_completion.py）。accepted 511/512/513、pre-vote0/1、responsive0/1の必要組合せ。
- A20: test_private_review_closed_diagnostic_is_one_lineとprivate CHATの既存3node、
  test_private_review_each_dimension_isolated_fail（test_phase6_private_review.py）を表のliteral matrixへ揃える。
  closed codes全件、想定外例外sentinel、private正常/誤PUBLIC/欠落、5dimension単独FAILを対象とする。

| Test ID | 対象/目的・予定file | 入力 | PASS条件 | FAIL条件 | 区分 / blocker | 必要実LLM |
|---|---|---|---|---|---|---|
| P6-A16 | 新profileの全伝播 / test_phase5_short_chat.py、test_phase6_semantic_output.py | 200chars/600bytes等価・1超過、20–120、bool/min-max境界、旧profile | prompt/parser/公開型が新値を使い、旧profileとlength/repairを維持 | 上限無視、型漏れ、不正入力通過、旧互換破壊 | deterministic / YES | 0 |
| P6-A17 | 実効budgetとPhase6可変plan伝播 / test_phase5_local_smoke.py | 親→bootstrap→fake broker/provider、512、missing/stray/false/null/string/float/bool/511/513、旧96、Phase6時間180/60/60・240/90/90・600/60/60 | 新512とfingerprintが全経路一致、malformed拒否、旧96と60/45/45不変。Phase6初期180/60/60とreplaceしたplan値がserverまで伝播 | 設定伝播漏れ、旧変更、D072入力との差 | deterministic / YES | 0 |
| P6-A18 | 新path分離 / test_phase6_evidence_retention.py、test_phase5_local_smoke.py | game/synthetic、task/UTC、衝突、base逸脱、legacy呼出し | 新規は正しいkindへ、既存原本不変、危険path拒否、旧CI呼出し互換 | 混在・上書き・危険path受理・既存呼出し破壊 | synthetic / YES（単なる管理改善NO） | 0 |
| P6-A19 | global512停止とsummary / test_phase6_semantic_completion.py | 有効accepted receipt 511/512/513、pre-vote0・responsive1 | 511/512受理、513で固定count/reason、manifest公開前停止・cleanup、pre-vote0だけで失格にしない | sampling/上限変更/誤PASS、記録欠落、cleanup不成立 | synthetic / YES | 0 |
| P6-A20 | literal診断・private CHAT・新判定 / test_phase6_private_review.py | 各closed codeの固定literal、sentinel、不正PUBLIC/欠落、旧3dimension単独FAIL | stderr1行で本文/pathなし、private oracle独立、privacy/反復必須、旧3項目は診断のみ | 漏洩、誤受理、コードが入力を転記、hidden gate | synthetic / YES | 0 |

R0時点の実効Phase6は180/45/45で、外部原指示の「60据置き」は事実誤認だった。
D072が明示採用したPhase6初期値180/60/60へT296で揃え、旧Phase5/Q8の60/45/45は保持する。
GamePlanのtime3fieldはintとして既存replace経路の可変値を表す。元classには__post_init__がなく、
Literal自体が実行時にValueErrorで拒否するというR03説明は採用しない。新CLIや自動調整機構は追加しない。
合成A17の可変入力は実ゲームでその値を使う承認ではない。Stage B初期値は上表で固定する。
T293の旧FAIL未分類分もA01–A20の同一IDで一括triageし、個別の新test cycleへ分解しない。

## 5. Stage B — 同一の実LLMゲーム1回から評価

実施条件: Stage Aのblockerなし、計画承認/FREEZE済み、実起動直前のユーザー明示承認。
モデル候補はcanonical `Qwen3.5-9B-Q4_K_M.gguf`、共有provider1、9 client、standard_9、seed8625。
whole-response上限512、text誘導20–120、200chars/600bytes、day180/vote60/night60、全体1200秒。
総ゲームtoken数を512とは扱わない。各request上限512・同lease repair1・CHAT2を維持し、
総token数/queue wait/latency/day所要時間をその1回で取得する。予測総tokenは未測定。
providerのexact識別情報・実設定・token予算・retention・rerun禁止条件を実起動承認時に提示する。
runnerの設定はA17対応の差分で整合させた。実効動作の試験は未実行。今回providerの調査・起動はしない。

方式は既存runnerの `--phase6` 経路。実起動用exact commandは上記条件と実provider識別値を埋めて提示し、
承認前に実行しない。B01–B11は同じ一つのrunを参照し、項目別のゲームは作らない。

| Test ID | 検証対象・目的 | 入力/方法 | PASS条件 | FAIL条件 | completion blocker |
|---|---|---|---|---|---|
| P6-B01 | 9 AI完走 | 共通runのserver結果と既存cleanup記録 | game_end到達、9client構成、owned残存0 | 未完走、進行不能、残存 | YES |
| P6-B02 | 前発言への応答 | 同run accepted ledgerとprivate原文 | 他人の発言を受けたaccepted responsive chat≥1 | 0件または対応不成立 | YES |
| P6-B03 | question→answer | 同run原文とsemantic actをReviewerが照合 | 実際の質問に対応する回答≥1 | 0件または形式だけの応答 | 条件付き：B03–B05全てFAILならYES |
| P6-B04 | 主張へのrebuttal | 同run原文とsemantic act | 対応する主張への反論≥1 | 0件または主張との対応なし | B03と同条件 |
| P6-B05 | 新情報によるbelief/判断更新 | 同runの新旧状態・原文・semantic act | 新情報に対応する判断/意見変更≥1 | 0件または新情報との対応なし | B03と同条件 |
| P6-B06 | pre-vote再評価 | 同runのpre-vote記録・判断 | 有効再評価≥1、変化しない結論も根拠の再評価があれば可 | 未観測、または無効な再評価 | NO（実機能破壊ならA09のFAILへ対応） |
| P6-B07 | private漏洩防止 | 全accepted原文と各受信者に許可された情報、既存送信記録 | 権限外AI/公開境界へのprivate本文・結果・token漏洩0 | 1件以上、確認不能はBLOCKED | YES |
| P6-B08 | bounded memory | 同runの既存projection/state記録 | 記録された件数/bytesが既定上限内、超過なし | 上限逸脱、確認不能はBLOCKED | YES |
| P6-B09 | 異常反復 | 同run全accepted text、既存正規化集計と原文review | 同一player直前文の反復0、同一正規化文3回以上0 | いずれか不成立 | YES |
| P6-B10 | token/latency/queue/day計測 | 同runの既存provider/queue/timing記録 | 使用token、生成latency、queue wait、day所要時間を実値で取得 | 欠測/非数値、確認不能はBLOCKED | 計測の欠落YES。性能値だけならNO、deadline等明示機能破壊はB01/B02へ |
| P6-B11 | 予算・population境界 | 同run設定/finishとaccepted count | provider上限512、length拒否、accepted≤512、全原文保持 | 設定違反、513以上、原本消失 | YES。超過時は固定reason/countで停止し全原本保持 |

全B項目の区分はreal-game、必要な既存テストはA01–A20、必要実LLMは共通1回。
質問応答・反論・判断更新・pre-voteは全て評価するが、現在のD072は会話3種のいずれか1件を必須としている。
上表はその完了条件と新指示の評価対象を区別した候補。FREEZEレビューでこのblocker列を先に承認する。
新指示を根拠に、旧3品質dimensionやpre-vote件数を暗黙の全項目必須へ戻さない。
**B02単独PASSではPhase6は完了しない。** 会話条件は
`B02 == PASS AND (B03 == PASS OR B04 == PASS OR B05 == PASS)`である。
B03–B05にPASSがなく一部がBLOCKEDなら会話条件もBLOCKED、全てFAILならFAILとする。
さらにB01/B07/B08/B09/B10/B11の必須条件とStage Aの重大regressionなしを要求する。
D072で採用済みの「会話3種のうち1件」を維持した候補であり、レビュー文からB02だけの合格へ変更しない。
候補の承認時にこの式と実LLM回数を明示対象とする。現在は候補未承認で実起動許可もない。
私的原文の審査結果は種別/件数/判定だけを公開し、本文・private pathを転記しない。

## 6. 回数、集計、修正、最終回帰

候補のMaster RunはStage A一巡＋Stage B実LLM1回。今回の起動数は0。
Stage Aの途中で通常FAILを個別修正せず、全IDを集計し原因をまとめる。
Stage Aが不安定ならStage Bは未実行BLOCKEDで残し、Aの一括修正を先に行う。
repairは原則最大2cycle、各cycleで既存FAIL IDとその依存回帰だけを再試験する。
最終回帰はA01–A20の凍結範囲。A12を含む再試験も必要性を一括Repair Planで先に決め、自動ループしない。
実LLMは1回を基準とし、旧FAIL修正確認に不可欠なら追加1回までを候補とするが、
追加は自動許可ではなく実起動前のユーザー承認を要する。無条件の調整5runや項目別runは予定しない。
2回の実LLM後にblockerが残れば停止し、別モデルや追加runで迂回しない。
R03-F6の「成立するまで増やす」は無制限runの承認へ読み替えない。最新D073は先に回数を決める方式を要求する。
1回＋必要な既存FAIL修正確認1回は本候補の提案であり、ユーザーが回数を確定承認したという意味ではない。
別の回数を選ぶ場合もFREEZE前に有限回数を確定する。FREEZE後の各担当による追加は禁止する。

計画作成者/実装者から独立したReviewerが計画を一度審査し、同じReviewer責務で最終ID判定を行える。
実行は独立Tester一人に集約する。各FAILごとの新Reviewer/Investigator/Testerを派生させない。
今は両担当をdispatchしない。既存の有効な独立性を保持するが、改善だけを追加blockerにしない。

## 7. Deferred Finding

計画外の発見は、DF-xxx / 現象 / 影響 / 再現条件 / Severity / Phase completion blocker YES・NO / 推奨対応を記録。
NOは今回扱わない。YES候補はクラッシュ、データ破壊、private漏洩、重大security、明示完了条件不成立、
進行不能、主要機能の重大regressionに限定する。証拠管理・監査・provenance・独立性の改善だけではYESにしない。
危険な依存項目をBLOCKEDにする判断はできるが、担当者が凍結範囲外のテスト/調査/reviewを自動追加してはならない。
Final Run後の非blockerはtechnical debt/次Phaseへ送る。

## 8. FREEZE候補の提示と停止

候補範囲はA01–A20、B01–B11とcatalog/batch CSV。FREEZE前提を次の順序に固定する。

1. 新packet T296で既存5修正を完成し、A16–A20を実在nodeへ対応させる。旧FAILは原因群と残る未確認を区別。
2. 最終sourceに対して`--collect-only`を一度実行し、予定nodeが全て収集されることを確認する。
3. catalogを再生成し、R0の905基底nodeとの差分が予定範囲だけであることを照合する。
   batch CSVの集合・marker・固定上限もここで確定する。collection成功をテストPASSとはしない。
4. 同じ計画のレビューと候補承認で、§5の合格式、§6の実LLM回数を確定する。
   この前提が揃った後だけTEST PLAN FREEZEを宣言し、その後に全計画テストを実行する。

上記1–3の実装・静的照合・collection/catalog更新を完了した。A17のready fingerprint拒否は
A19の同一fake runner経路でも確認する予定とし、別nodeや実gameを増やしていない。
今はNOT APPROVED/NOT FROZEN。T296/Mainの静的完成やcollectionだけでは独立承認/実試験PASS/Phase6 DONEとしない。
改訂候補報告後に停止し、旧T293/T294/T295の取消テストサイクルを再開しない。
