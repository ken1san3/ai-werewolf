Status: APPROVED — T329開始identity改訂、T331独立承認済み

# Phase 6 Stage B 詳細設計

T329改訂status: **T331独立承認済み**。T327が承認したR11本文、旧T306承認revision、T307/T316の実行履歴は不変であり、本改訂はT331承認後の次回実行freezeにだけ適用する。設計承認後も、実game直前の具体条件に対するユーザーの明示承認を別に要する。

Base task: T305  
Base revision status: APPROVED — Stage B実行未承認  
旧revision独立承認: `Docs/ai/handoffs/tasks/T306_STAGE_B_DESIGN_REVIEW.md`。今回のR11改訂はこの旧承認を継承せず、T327の独立承認を取得した。設計承認はStage Bの起動許可ではない。

R11独立承認: `Docs/ai/handoffs/tasks/T327_R11_FRESH_REVIEW.md`。開始identity改訂の独立review: `Docs/ai/handoffs/tasks/T331_STAGE_B_FRESH_REVIEW.md`（設計承認済み、preflight判定は別）。

## 1. 目的とauthority

本設計は、凍結済み `P6-PLAN-20260914-R1` §5–7の `P6-B01`–`P6-B11` を、同一の実LLMゲーム一回から判定できる粒度へ具体化する。製品・test・schema・runner・fixture・既存R1 CSV・既存rawは変更しない。

優先順位は、D073、R1 Master Test Plan §5–7、D072、ROADMAP Phase 6、server truth/private境界、承認済み旧詳細設計の順とする。旧詳細設計§11にある `coherent`、`source_relevant`、`objective_consistent` の全件PASSとpre-vote 1件以上は診断・評価対象として残すが、D072とR1 §5に反してPhase 6のhidden mandatory gateへ戻さない。Stage Bの会話合格式は次で固定する。

```text
conversation = B02 == PASS AND (B03 == PASS OR B04 == PASS OR B05 == PASS)
```

- B03–B05の一つ以上がPASSなら後半はPASS。
- 三つともFAILなら後半はFAIL。
- PASSがなく、一つ以上がBLOCKEDなら後半はBLOCKED。
- B06は必ず評価するが、単独FAIL/BLOCKEDをPhase 6 completion blockerにしない。実機能破壊の疑いは既存P6-A09との不整合として別に記録し、Stage A PASSを推測で覆さない。
- closureに必須なのは conversation に加え、B01、B07、B08、B09、B10の必須measurement取得、B11、および承認済みStage Aに新しい重大regression根拠がないこと。各IDの正確なblocker規則はテストケース表に従う。

## 2. 実行単位、一回制限、証拠再利用境界

Stage Bの母集団は、同一の `standard_9`、seed `8625`、9 client、共有provider concurrency 1の実game一回だけである。B01–B11は、共通の `tester_task_id`、run count `1/1`、opaque run label、game manifest SHA-256、artifact内のgame IDで同じ一回へ運用上相関する。現行runtime artifactに `run_id` fieldはなく、opaque run labelもruntime schema fieldではない運用識別子である。IDごとのgame、negative例用game、境界例用game、確認用fixtureは起動しない。negative/境界例は、得られた同一原本に対する判定例または机上のoracleであり、追加runを発生させない。

R1 Stage AはT303/T302により890 Test ID、1,206 caseがPASS済みである。この事実はStage Bの開始前提にだけ使う。Stage Aのraw、fixture、semantic completion結果をB01–B11の実測値、実会話、private原文審査、provider identity、token、latency、queue、day所要時間へ流用しない。旧T299/T300のFAIL/BLOCKED、旧原本欠落も新B結果へ読み替えない。

実LLM回数の今回候補は一回である。通常のB ID FAIL後も同じ保存済み原本から安全に評価できる残りのB IDを最後まで評価する。次の場合は危険な依存評価を停止し、実行済みIDを確定し、未評価IDをBLOCKEDにして原本とcleanupを保全する。

- 起動不能、crash、全体timeout、source/freeze変化、データ破壊、private漏洩の継続リスク、所有process cleanup不能。
- manifest/shard破損または欠落により、以降のprivate原文アクセスが安全・完全でない。
- accepted-text populationが513へ到達してcollectorが固定reasonで停止する。

同じrunの保存済み原本を再読することはrerunではない。processorはReviewerが一度だけ起動する。game、provider、tokenizer、fixture、pytest、collectionの再起動は自動で行わない。修正確認を含む追加gameは今回承認に含めず、新しい有限scopeと実起動前のユーザー明示承認が必要である。

## 3. 起動前Human Gate

独立Testerは、次を一つの承認パッケージとして提示し、ユーザーが具体条件を明示承認するまで何も起動しない。

1. T306の旧設計承認とT302のStage A限定承認に加え、T322がR10修正7 sourceを最終 `APPROVED` としたhandoff、T323の独立試験結果、T327がR11の3文書を `APPROVED` としたhandoff、およびT331が本開始identity改訂のexact revisionを `APPROVED` としたhandoff。T331未承認の間は起動条件を満たさない。
2. 実行時identityを二層で照合する。第一層はT303 freeze/checkpointの191 `sources` を履歴基礎とし、T314/T315で承認・確認済みの旧5 source置換を中間履歴として保持したうえで、T322が最終承認しT323が独立検証したR10の7 sourceだけを `logs/t319-r10-repair/source-retest.json`（191件、SHA-256 `e5f628b8108afea1c9ace54f65eccfa4a614e4100b3e10577d87fbc62fc4683f`）の値へ移した新しいRun freezeを作る。7 sourceは `ai_client/llm/types.py` = `02dbae2f80e95970b11e75dad0375247ed6e67c85d9884dd1fa3ec94ecea8278`、`ai_client/llm/brain.py` = `5b66aa0254d9a73e58853c0a3d6a7fae356676e9309859247a5cd28abb6b9342`、`scripts/run_phase5_local_smoke.py` = `6ff3943b5c79b9dd0453aea304b00517f516f84276dd460978be7b0ccf2bc30d`、`scripts/phase6_private_review.py` = `6f5c803686e207eabef1174c33286bf33d713dd596b58d7ae093cb9eee929963`、`tests/test_phase6_semantic_completion.py` = `aafbac4e89276904fd2e59251567d34d6e7e216931f0e68ee83ba0693955e6f5`、`tests/test_phase6_private_review.py` = `950262f9a0c103ad0139a05840d0372132c0e0bb8da9e830daf7bd8119b9fc55`、`tests/test_phase6_discussion_transaction.py` = `08fb5cad3e67a67c6c7e350cd51c0341e43a2df7a7bd04078094da4d8c9d3841` とする。`EXTERNAL_REVIEW_LOG.md` を除く残り183 sourceは同manifestとexact hash一致を要求し、未列挙差分は承認済み変更として扱わず停止する。`EXTERNAL_REVIEW_LOG.md` 1件は第一層のexact一致対象から除外し、第二層の個別reconcileだけで扱う。T322最終handoff SHA-256は `4608fbad5fbee7a0feecc90aaf6a0366e8de233d00f484e40740d43081356426` であり、T323は再測定前後とも191件・差異0を確認した。新しいRun freezeは起動直前の現物と差異0でなければならず、実行開始後はsourceもfreezeも変更しない。第二層のhuman-facing/control文書は、T303後の許可済み変更を個別に列挙して現hashとauthorityをreconcileする。R11の承認対象3文書は `Docs/ai/PHASE6_STAGE_B_TEST_CASES.md`、`Docs/ai/PHASE6_STAGE_B_TEST_CASES.csv`、本設計であり、T327 handoff（SHA-256 `2b763a14181a3a835a15934201e77f37171f87ca5dc417734af8633115145c75`）とT325の統合記録を根拠に、T327が審査したexact hashから承認headerおよび本T329改訂だけを明示的にreconcileする。`EXTERNAL_REVIEW_LOG.md` はR10/R11を含む現hashを別のcontrol文書として個別reconcileし、第一層の7 source置換や未列挙差分の許容根拠にしない。R1 plan/catalog/batches、Stage Bの製品・clock・budget・B01–B11・PASS/FAIL/BLOCKED条件、privacy、whole-response 512は変更しない。T303の890 Test ID / 1,206 case PASS、T307/T316のFAIL/BLOCKED/UNKNOWN、T312の692 PASSとsynthetic completion 1 FAIL、T315の697 case、T323の初回FAILと再測定PASSはそれぞれ別の履歴として不変であり、いずれも新しいStage B全体PASSへ読み替えない。
3. canonical model名 `Qwen3.5-9B-Q4_K_M.gguf`、endpoint、外部provider PID、GGUF SHA-256、serving executable SHA-256、provider実装/version/build/commit、profile引数・profile SHA-256、backend config fingerprint、GPU名と使用対象。未確定値は `TBD-BEFORE-LAUNCH` とし、承認時までに実値で置換する。
4. runnerの実効条件: `--phase6`、9 client、`standard_9`、seed 8625、day 180秒、vote 60秒、night 60秒、`--max-seconds 1200`、共有provider concurrency 1、CHAT開始最大2/player/phase、CO別経路、repair最大1/同一lease。ここで1200秒はserver result待ちへ渡す値であり、親processの起動から原本集計・cleanupまでの総wall hard capではない。
5. 出力profile: 200 chars、600 UTF-8 bytes、text誘導20–120、各requestのwhole-response上限512。512は総game token予算ではなく、全応答が常に収まる保証でもない。
6. 総token予算。現行runnerにはgame全体の受付token hard capもlive stop機能もないため、値を実装済みの停止条件として提示しない。承認前に「見積り/監視用の有限値」と「超過時の人手停止判断」を決め、actual enforcementがないことを明記する。未測定なら `TBD-BEFORE-LAUNCH` のまま承認を求めない。
7. owner-only raw保存先、review workspace、公開aggregate/handoffのallowlist、retention期間・削除責任者。既存ACL/security/TEMPを変更しない。
8. 一回だけ、no fallback、no 35B、no soak、no automatic retry、項目別runなし、FAIL時も安全な残り評価継続、危険時停止、owned processだけをcleanupする条件。

R1の「全体1200秒」とactual runnerには未解消の範囲差がある。`_run_game` にはserver/broker/client readiness各最大30秒、server result待ち最大`config.max_seconds`、status最大60秒、broker最大15秒、その後のcleanup grace/terminate/kill待ちがあり得る。Stage Aの外側1260秒をStage Bへ流用して総wall保証とはしない。起動前にMain/Testerは、ゲーム待ち1200秒を維持したまま外側監視に与える有限上限、timeout時のowned cleanup完了待ち、どの時計をB01/B10へ記録するかを具体値で提示し、ユーザー承認を得る。この値が `TBD-BEFORE-LAUNCH` の間は起動不可である。外側監視は既存runnerのcleanupを放棄する強制終了にしてはならず、本設計は新wrapper実装を許可しない。

runnerの `--provider-*` build/profile引数は `--q8-provider-timing-diagnostic` 経路だけで検証される。`--phase6` commandへ付けてもexact binary/profile証明にならないため、Stage B commandには含めず、上記の起動前静的運用証拠として別記する。Phase 6 runtimeが直接検査するのはcanonical model名とbroker readyのbackend config fingerprintである。

## 4. 未実行commandテンプレート

次は実在する公開CLI引数だけを使う。`<...>` は起動前に実値へ置換するplaceholderであり、この設計では実行しない。出力leafは既存文法 `[A-Z][A-Z0-9_-]{1,31}-YYYYMMDDTHHMMSSffffffZ` に適合し、まだ存在しないowner-only pathでなければならない。

```powershell
$controllerLogParent = <ABSOLUTE_EXISTING_OWNER_ONLY_CONTROLLER_LOG_PARENT>
$runnerStdout = Join-Path $controllerLogParent '<RUN_LABEL>.stdout.log'
$runnerStderr = Join-Path $controllerLogParent '<RUN_LABEL>.stderr.log'
python scripts/run_phase5_local_smoke.py `
  --phase6 `
  --endpoint <APPROVED_ENDPOINT> `
  --model Qwen3.5-9B-Q4_K_M.gguf `
  --gpu-model-pid <APPROVED_EXTERNAL_PROVIDER_PID> `
  --seed 8625 `
  --max-seconds 1200 `
  --output-dir logs/phase6-private-evidence/game/<TESTER_TASK_ID>-<YYYYMMDDTHHMMSSffffffZ> `
  1> $runnerStdout `
  2> $runnerStderr
```

`ABSOLUTE_EXISTING_OWNER_ONLY_CONTROLLER_LOG_PARENT` は起動前にprivate検査済みで既に存在し、新規run leafの外側にある専有directoryである。`--output-dir` のrun leafはcommand開始時に存在してはならない。runnerはPhase 6を `SMOKE` label、`<RUN_OUTPUT>/smoke` に実行し、成功/失敗メッセージへprivate evidence pathを含めるため、上記のstdout/stderrは開始時からowner-only controller logへ直接redirectし、public console、handoff、CI annotationへ転載しない。`summary.json` の `rows[0]` が共通game rowである。

private Reviewerのprocessor commandも未実行であり、`RUN_DIR` はrootではなく `<RUN_OUTPUT>/smoke`、`CHECKLIST_JSON` と `AGGREGATE_JSON` はrun_dir外の別owner-only review workspaceに置く。

```powershell
python scripts/phase6_private_review.py `
  --run-dir <ABSOLUTE_RUN_OUTPUT>\smoke `
  --checklist <ABSOLUTE_OWNER_ONLY_REVIEW_WORKSPACE>\checklist.json `
  --output <ABSOLUTE_OWNER_ONLY_REVIEW_WORKSPACE>\aggregate.json
```

## 5. 状態遷移と責任分離

| 状態 | 進入条件 | 実施者 | 退出条件 |
|---|---|---|---|
| `DESIGN_PROPOSED` | T305成果物完成 | Architect | T306 verdict |
| `DESIGN_APPROVED` | T306 exact revision APPROVED | Main | 承認パッケージ完成 |
| `AWAITING_HUMAN_LAUNCH_APPROVAL` | 未確定値0、path/ownership/retention確定 | Main/Tester | ユーザー明示承認 |
| `PREFLIGHT_READY` | 承認条件と現物identity一致 | Tester | 一回のlaunch event記録 |
| `RUNNING_ONCE` | run count 0→1 | Tester | exit/timeout/safety stop |
| `RAW_PRESERVED` | owned cleanup完了、artifact hash採取 | Tester | machine判定完了 |
| `MACHINE_EVALUATED` | B01/B02/B06/B08/B10/B11と機械境界の判定 | Tester | Reviewerへowner-only path handoff |
| `PRIVATE_REVIEWING` | 独立Reviewerが全populationを読める | Reviewer | checklistとprocessor一回完了 |
| `AGGREGATED` | aggregate hash、B03–B05/B07/B09判定確定 | Reviewer | Mainが全11 ID集計 |
| `COMPLETE` | mandatory式PASS、全IDが非NOT RUN | Main | Phase 6 closure判断 |
| `STOPPED` | safety stop、BLOCKED、またはFAIL集計完了 | Main | 新しいscope/承認なしに再起動しない |

Architectは設計のみを作り、承認・実行・原文判定をしない。Testerは一回のrun、process所有、機械artifact/hash、machine判定を担当し、自然言語品質を判定しない。ReviewerはImplementer/Testerと別sessionで、許可された全accepted text原文と相関するsemantic act/contextだけを読み、owner-only checklistを作りprocessorを一度起動する。Mainは公開aggregateと各IDを集計するがprivate原文を受け取らない。

providerは外部unowned processであり、runnerは `gpu_model_pid` として識別しても停止・killしない。runnerが所有するserver 1、broker 1、client 9だけをcleanup対象にする。外部providerの生存確認と実行中の担当者は起動前に確定する。timeout後もowned processの停止、exit/生存結果、raw保全を先に行い、追加runをしない。

process時間は少なくとも、親runner wall、server result待ち、`server.result.json.phase_wall_durations`、`total_game_wall_microseconds`、cleanup開始/完了を区別する。現行summaryが全てを一つの総wall fieldへ統合しているとは主張しない。外側監視値とcleanup所要の保存方法は既存運用証拠で起動前に確定し、新schemaを追加しない。

## 6. artifact、field、ownership

| 区分 | 実在artifact/field | 所有・公開境界 |
|---|---|---|
| run root | `<RUN_OUTPUT>/summary.json`, `raw.jsonl`; `summary.schema=aiwolf.phase6-run-summary.v1`, `mode`, `model_identity`, `seed`, `arguments`, `game_plan`, `rows[0]`, `overall_metrics`, `gpu`, `external_model_inventory`, `cleanup` | owner-only。stdout/stderrも同等にprivate扱い。pathを公開しない |
| game root | `<RUN_OUTPUT>/smoke/server.result.json`, `broker.result.json`, player status、process logs、`<game_id>/ai/` | Testerが保全。Reviewerへread許可を限定付与 |
| machine manifest | `<RUN_OUTPUT>/smoke/<game_id>/ai/manifest.json`; schema `aiwolf.phase6-private-manifest.v1`、`game_id`、`player_to_opaque_client_id`、`shards`、`metadata`、`accepted_text`、`semantic_counts`、`terminal` | owner-only。公開はmanifest SHA-256だけ |
| accepted population | manifestの `accepted_text` entryが指す `accepted-text.jsonl`; `server_record_order`, `player_id`, `request_event_id`, `decision_kind`, `day`, `phase`, `text_sha256`, `capture_id`, generation/terminal sequence/hash, `responsive`, `source_relevant_applicable` | owner-only。全件、順序保持、samplingなし |
| per-client shard | manifest `shards` entryが指す各 `ai.jsonl`; generation/terminal records | Reviewerのみ原文閲覧可。本文/path/capture/player対応をpublicへ出さない |
| semantic machine counts | manifest `semantic_counts` および `summary.rows[0].semantic`: `responsive_accepted_count`, `pre_vote_reassessment_count`, `accepted_text_count`, `chat_caps_respected`, `maximum_chat_starts_per_player_phase`, `chat_start_count`, `generation_count`, latency/prompt distributions, `semantic_requirements_met` | 公開はallowlistした数・判定・hashのみ。private pathなし |
| reviewer checklist | `aiwolf.phase6-private-review-checklist.v1`: `reviewer_task_id`, `evidence_manifest_sha256`, ordered `records`; recordは `capture_id`, `coherent`, `source_relevant`, `objective_consistent`, `privacy_safe`, `non_repetitive` | run_dir外のowner-only review workspace。非公開 |
| aggregate | `aiwolf.phase6-private-review-aggregate.v1`: hashes、population/responsive/applicable counts、5 dimension counts、linkage/missing/corrupt/duplicate/normalization counts、`human_quality_pass` | canonical生成物はowner-only。handoffへallowlist fieldとaggregate SHA-256のみ転記 |
| public result | Tester/Reviewer handoffとB01–B11 result CSV | private本文、path、normalized text、reason、capture ID、player mapping、credential/tokenを含めない |

時刻だけでartifactを対応づけない。公開結果は共通 `tester_task_id`、run count `1/1`、実行対象freeze hash、個別reconcile済みcontrol文書hash、game IDの公開可否を事前確認したopaque run label、manifest SHA-256、checklist SHA-256、aggregate SHA-256、各artifact内の対応hash fieldを用いる。opaque run labelはruntime schema fieldではなく、Main/Testerが一回の運用記録へ付ける識別子である。private pathはTester→Reviewerのowner-only handoffだけで渡す。

## 7. population 512とtoken予算の正確な境界

accepted-text上限512はglobal population上限であり、shard単位ではない。collector `_phase6_semantic_population` はserver `accepted_text` を順に相関し、513件目を追加した直後に `Phase6PopulationExceeded(accepted_text_count=513, reason=ACCEPTED_TEXT_POPULATION_EXCEEDED)` を送出する。これはserver/broker/clientの終了結果を読んだ後、`_write_phase6_evidence` の集計時に検出する。live game stopでも、途中samplingでもない。

513時はaccepted ledgerとmanifestをpublishせず、summary semanticにcount/reasonを残し、全owned processをcleanupする。既に各shardとserver resultへ存在する原本を切り捨て・分割・削除しない。B11はFAILである。ただし現行processorはmanifestを必要とするため、B03–B09の完全相関評価はBLOCKEDになり得る。これを追加gameで埋めない。

runner全体の `_MAX_RAW_RECORDS=20,000` はadmission metricsを `raw.jsonl` へ集約する別上限で、accepted population 512とは無関係である。超過時はraw metricsを20,000件へ切り詰め `RAW_EVIDENCE_BOUND_EXCEEDED` とする。この場合B10のqueue/provider集計は完全性を失うためBLOCKED/FAIL判定に従い、accepted原文が512件保持された証拠として使わない。

`GenerationSettings.max_output_tokens=512` は各provider requestのwhole-response上限である。`prompt_tokens` と `completion_tokens` はprovider usageが返る場合だけgeneration record/admission metricに入るnullable fieldである。現行runnerは全request受付token総量のhard cap、予算超過のlive停止、全provider tokenが必ず得られる保証を実装していない。欠測を0やPASSで埋めない。

## 8. Reviewerの閲覧範囲と原文oracle

Reviewerはhash検証済みmanifest、accepted population、対応するgeneration/terminal record、各generationの `prompt_json`、`proposal`、`decision.text`、許可情報を表すbound `context`/projected `memory`、server accepted receiptだけを、全populationについてserver record orderで読む。credential、admission token、entry tokenはレビュー対象外であり、見えた場合はB07 FAILとして漏洩経路を閉じる。Reviewerは他playerに許可されないprivate rawを、そのplayerが知っていたはずの情報として使わない。

B03–B05はsemantic labelだけでPASSにしない。

B03–B05の判定依存はB02のresultではなく、共通runの原本、manifest、generation→terminal→server correlationが安全かつ完全に読めることである。B02がFAILでも三項目を独立に評価し、会話式の各operandを埋める。private危険で閲覧を止めた場合だけ、既知違反のない未判定項目をBLOCKEDにする。

manifestまたは完全相関を欠いても、同一gameのserver accepted receiptについて保存hash、server order、identityの重複なしを確認できた件数だけを `observed_accepted_lower_bound` として部分観測できる。確認不能なら `UNKNOWN` とする。対応する既存record由来を特定できた `responsive` labelだけを `observed_responsive_label_count` に数える。読めない範囲、未観測範囲、完全母集団の大きさは `UNKNOWN` とし、0に補完せず、未知母集団を分母とする割合を算出しない。これは既存private原本から算出して公開handoffへ載せる診断labelであり、新runtime fieldではない。`responsive` labelを内容上のresponsive品質またはReviewerの `source_relevant=PASS` と扱わない。B02–B05の正式PASSと全体0件のFAIL断定は従来どおりhash検証済みmanifest、完全母集団、全件review、完全相関を必須とする。部分診断だけでresultを変えない一方、別の既存oracleで当該項目の違反を確定できた場合はN3のFAIL優先を維持する。

- B03: `speech_act.kind=ANSWER` の候補について、引用元が実際に質問を含み、回答文がその問いへ内容上答えている。対象・引用・時間順・visibilityが一致する。
- B04: `speech_act.kind=REBUTTAL` の候補について、先行する具体的主張を特定でき、反論文がその主張の内容・理由・結論のいずれかを実質的に否定または弱める。単なる不同意語や無関係な主張はFAIL。
- B05: `speech_act.kind=OPINION_CHANGE` 等の候補について、先行する新情報、変更前の判断、変更後の判断が原文/投影state/semantic actで特定でき、新情報との因果的対応が読める。state hashだけ、同じ結論の言い換え、根拠のないtarget変更はPASSにしない。

B07では、自発的な役職claimや合法な偽COを直ちにprivate漏洩としない。offered actionがそのdatumの開示を明示的に意図する場合のclaimと、credential/admission token/private channelの原文引用/自分だけに通知されたinspect・medium等のprivate結果の公開を区別する。前者はobjective/coherenceの診断対象、後者は `privacy_safe=FAIL` かつB07 FAILである。他人に許可されないprivate情報を言い換えて公開した場合も漏洩であり、文字列完全一致だけに限定しない。

既存processorのD072後 `human_quality_pass` は、population/linkage整合、`privacy_safe.fail==0`、`non_repetitive.fail==0` をclosure側へ反映する。旧3dimensionはaggregateへ残る診断値であり、単独FAILで `human_quality_pass` を落とさない。会話3種は別途R1式で判定する。

## 9. B08/B10の測定可能性とgap

B08で実在する観測は、各generation `prompt_json` の `memory.records`, `included_records`, `omitted_records`, `omitted_through_order`, `memory_byte_exhausted`, `token_proxy_exhausted`、`state.omitted_counts`, `state_projection_omitted`、generation `prompt_bytes`、再計算したcomplete prompt proxyである。上限はimportant old 12、新しいrecord 12、combined unique 24、important text 768 bytes/160 scalars、recent text 2,048 bytes/512 scalars、memory 16 KiB、context 8 KiB、state 8 KiB、proposal 16 KiB、prompt 32,768 bytes、proxy 8,192 unitsである。全generationを検査し、一件でも超過、必須trigger欠落、後hash mutationがあればFAIL。`before_state_sha256`/`after_state_sha256` はdigestであり、未投影を含む全stateの常時memory量を測った証拠ではない。この未取得量をPASSで埋めない。

B10のactual sourceは次である。

- generation latency: generation record `latency_microseconds` と semantic `latency_microseconds` distribution。
- prompt size/proxy: `prompt_bytes`, semantic `prompt_bytes`, `prompt_token_proxy`。
- provider usageと実総token: private admission rawの全 `event=PROVIDER_CALL_TERMINAL` を対象に、実call identity `(client_id, invocation_id, call_ordinal)` で一意性を検査する。original/repairは同じ`invocation_id`でも`call_ordinal`が異なるため両方を各一回数え、invocation単独でdedupeしない。各実callのnullable `prompt_tokens` と `completion_tokens` から `total_prompt_tokens`、`total_completion_tokens`、両者の合計 `total_tokens` をprivate側で算出する。既存集計の `provider_prompt_tokens`, `provider_completion_tokens`, provider rates、`provider_timing_complete` と母数を照合するが、新schema/fieldをruntimeへ追加しない。publicへ出すのは三つのtotal、実call count、原本raw artifactのSHA-256だけであり、raw本文やpathは公開しない。
- queue: admission metricsの `queue_wait_microseconds`、集計 `queue_enqueue_to_offer_microseconds`, `queue_enqueue_to_claim_microseconds`, `request_end_to_end_microseconds`, priority別分布。公開summaryからopaque client別は除去される。
- day/game所要時間: `server.result.json.phase_wall_durations[*].wall_microseconds`, `total_game_wall_microseconds` とsummary row同field。

追加の非acceptance診断として、private generation record全件から `status`、responseを持つstatusでのnullable `validation_code`、`attempt_ordinal`、v2 `prompt_rejection_code` を別々の分布として内訳化し、分布間を加算しない。response妥当性率を報告する場合は、分子を `DECISION|EXPLICIT_NO_DECISION|REPAIR_SUCCEEDED`、分母をresponseを持つ `DECISION|EXPLICIT_NO_DECISION|REPAIR_SUCCEEDED|OUTPUT_INVALID|REPAIR_FAILED` とし、分子・分母のcountを必ず併記する。分母0は `N/A`、破損・未読・母集団不完全なら全体率は `UNKNOWN` とする。prompt/backend/cancel等responseなしstatus、妥当statusでの非適用null `N/A`、欠測、v1 rejection reason欠測、partial observationを別分類する。attempt record数、generation record数、provider `PROVIDER_CALL_TERMINAL` の実call数は別の単位であり相互に代用しない。この診断は既存runtime fieldだけからprivate側で算出し、新schema/codeを追加しない。診断値やその欠測だけでB10のPASS/FAIL/BLOCKEDを変更せず、B10の必須測定は従来の使用token、generation latency、queue wait、day所要時間の四種のままである。

実call identityの重複または同一identity内の矛盾、数値fieldの型/値矛盾、既存rawと集計の既知不一致はB10 FAILとし、他の欠測が併存してもFAILを優先する。実callの欠落、provider usageの一件でもnull、metrics drop、raw 20,000超の切詰め、必要分布count不足により完全値を決められない場合は、当該項目に既知FAILがない限りB10 BLOCKEDとする。いずれもtotalを部分和や0で補完しない。数値が高いだけならB10 completion blockerではない。deadline超過や進行不能としてB01/B02を壊す場合は対応IDでFAILとする。現行artifactだけでprovider内部のKV-cache、GPU kernel時間、受付token総量hard-cap遵守は測れないため、Stage B PASS項目に捏造しない。

## 10. 判定値と集計

各IDの実行前statusは `NOT RUN`。実行後の正式resultは `PASS|FAIL|BLOCKED` の一つとし、`NOT RUN` をPASSとして扱わない。ここで既知FAILはrun全体ではなく当該ID自身のoracle違反を指す。個別IDでは当該項目の既知違反が一つでもあればFAILを優先し、別の必須証拠欠落が併存してもBLOCKEDへ弱めない。当該項目に既知違反がなく、必要artifact/原文/measurement欠落または安全停止により判定できない場合だけBLOCKEDとする。他IDのFAILは当該IDの独立判定を変更しない。PASSは全oracle成立である。特にaccepted 513を固定count/reasonで確認した場合、manifest未発行が併存してもB11はFAILである。

全11 IDの公開集計はTest ID、result、completion blocker `YES|NO|CONDITIONAL`、根拠となる公開可field/count/hash、実行回数 `1/1`、NOT RUN残数を含む。private path、本文、capture/player対応を含めない。Mainは次の順に決定する。

1. B01–B11を個別に確定する。
2. B02のresultと独立してB03–B05を評価し、後半OR、次にconversation式を三値で評価する。ORは一つPASSならPASS、全部FAILならFAIL、PASSなしで未判定が一つ以上ならBLOCKED。
3. B06の結果を診断として記録する。
4. B01/B07/B08/B09/B10/B11、conversation、Stage A重大regressionなしを結合する。
5. mandatory全体も既知FAILを優先する。mandatory項目にFAILが一つでもあればFAIL。FAILがなく、BLOCKEDが一つ以上ならBLOCKED。全てPASSの場合だけclosure候補とし、Mainがauthority文書を更新する。

## 11. 現在の結論

本設計、全Bテストケース、CSV、handoffはいずれも文書作成のみである。本R11改訂による次回Stage Bは **NOT RUN** であり、今回の改訂sessionで実provider/model/GPU/tokenizer/API probe、実LLM game、processorを実行していない。旧T307/T316の実行結果は各handoff/stateに履歴保持する。本改訂にはT327の独立APPROVEDと、次回起動前Human Gateが必要であり、T326は自己承認しない。
