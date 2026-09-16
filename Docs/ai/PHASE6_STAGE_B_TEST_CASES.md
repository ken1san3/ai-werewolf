# Phase 6 Stage B テストケース

Plan: `P6-PLAN-20260914-R1` Stage B  
Design: `Docs/ai/design/PHASE6_STAGE_B_DETAILED_DESIGN.md`  
Current status: **R11改訂はT327独立承認済み。各行のNOT RUNは次回実行templateの初期値**

旧T306承認revisionとT307/T316の実行結果・履歴は変更しない。R11改訂はT327承認後に次回実行freezeへ照合し、旧runへ遡及適用しない。R16の追加診断はT357の独立承認記録と改訂hashを照合して次回へ継承する。T327承認をR16へ流用しない。

## 共通前提

- T306が設計を独立承認し、Stage A blocker 0、product code/test/config/runner/fixture/R1 freeze一致、T303後の許可済みhuman-facing/control文書変更の個別reconcile、実provider/model/設定/token/retention/rerun条件をユーザーが起動直前に明示承認する。
- canonical 9B、共有provider 1、9 client、standard_9、seed 8625、day/vote/night 180/60/60秒、`--max-seconds 1200`、whole-response 512/request、200chars/600bytes、20–120 text token誘導、repair最大1、CHAT最大2/player/phase、CO別経路。1200秒はserver result待ちへ渡す値で、親起動からcleanupまでの総wall hard capではない。外側の有限監視上限とcleanup待ちを起動前承認で別途固定する。
- 実gameは共通一回だけ。以下のnegative/境界例は同一原本への判定例であり、追加runをしない。
- Testerは自然言語を判定せず、Reviewerは全accepted textをsamplingなしで読む。public文書へprivate本文/path/capture/player対応を出さない。
- 各resultは実行後 `PASS|FAIL|BLOCKED`。各行の `NOT RUN` は次回実行templateの初期値であり、旧T307/T316の結果を変更せず、BLOCKEDやPASSへ読み替えない。個別IDもmandatory全体も既知FAILを優先する。既知違反がなく必須判定材料だけが欠ける場合にBLOCKEDとする。B03–B05のOR群は一つPASSならPASS、全部FAILならFAIL、PASSなしで未判定があればBLOCKEDとする。
- この「既知FAIL」は当該ID自身のoracle違反を指す。他IDのFAILは当該IDをBLOCKEDからFAILへ変えず、当該IDの既知違反は別証拠の欠測が併存してもFAILを優先する。
- manifestまたは完全相関を欠く場合も、同一gameのserver accepted receiptについて保存hash、server order、identityの重複なしを確認できた件数だけを `observed_accepted_lower_bound` として診断記録できる。確認不能なら `UNKNOWN` とする。対応する既存record由来を特定できた `responsive` labelだけを `observed_responsive_label_count` に数える。ただし未知の完全母集団を分母にした割合を出さず、未観測を0にせず、全件review・manifest・相関を必要とするB02–B05の正式PASSや全体0件のFAIL断定、`source_relevant` のReviewer判断へ流用しない。部分診断だけでresultを変えず、別の既存oracleで確定した当該項目FAILは保持する。`responsive` は機械labelの部分観測であり、内容上のresponsive品質成立を意味しない。これらは新runtime fieldではなく、既存private原本から算出して公開handoffへ載せる診断labelである。

## テストケース

### P6-B01 — 9 AI完走

- 目的: server truthによるgame end、exact topology、owned cleanupを確認する。
- 手順: 共通runの `summary.json.rows[0]`、`server.result.json`、各status、cleanupを照合する。
- artifact/field: `rows[0].server.game_end`, `process_topology`, `cleanup[*].alive/returncode`, broker `shutdown_clean`, `total_game_wall_microseconds`。
- PASS: game_end true、server 1/broker 1/client 9、owned残存0、必要child正常終了。
- FAIL: 未完走、進行不能、owned残存、topology違反。既知FAILがなく確認不能ならBLOCKED。
- negative/境界: 8 client、game_end false、cleanup一件aliveはFAIL。外部provider生存はowned残存に数えない。server待ち1200秒以内でも総wall上限の証拠にはならず、既知FAILがなければ外側時計欠測はBLOCKED。
- 責任/依存: Tester。Stage A/T306/Human Gate。
- 証拠: owner-only run raw、publicには結果/count/hashのみ。
- blocker: YES。現在NOT RUN。

### P6-B02 — 前発言へのaccepted応答

- 目的: 他者の先行発言に対応したaccepted responsive chatを1件以上確認する。
- 手順: manifest/accepted-textとgeneration→terminal→server receiptを全件相関し、`responsive=true` のsourceが先行order、他player、許可visibility、同一channel条件を満たすことをReviewer原文でも照合する。
- artifact/field: `semantic_counts.responsive_accepted_count`, accepted row `responsive`, `source_relevant_applicable`, generation `proposal.speech_act`, terminal `authoritative_evidence`, aggregate `responsive_population_count`, `dimension_counts.source_relevant`。
- PASS: machine-qualified responsive count≥1、対応する原文が実際にsourceへ応答しReviewer `source_relevant=PASS`。
- FAIL: 完全母集団のcount 0、先行/相手/visibility/channel/内容対応が不成立。当該項目に既知FAILがなく原本欠落・相関不能ならBLOCKED。
- negative/境界: 自分の発言、後続発言、無許可private、ラベルだけANSWER、内容無関係は不合格。exact 1件はPASS。
- 責任/依存: Testerが機械相関、Reviewerが原文対応。共通原本/manifest/correlationの可読性とB07の安全な閲覧境界に依存するが、B07 PASSには依存しない。
- 証拠: private raw/checklist、public counts/hash。
- blocker: YES。現在NOT RUN。

### P6-B03 — question→answer

- 目的: 実際の質問に内容上対応する回答を確認する。
- 手順: 全accepted `ANSWER` 候補について、先行source原文が質問か、addressee/source/order/visibility/channelが一致するか、回答が問いへ具体的に答えるかを照合する。
- artifact/field: generation `proposal.speech_act.kind/in_reply_to/addressee_player_id`、projected `memory.records`、decision text、accepted/terminal correlation、checklist `source_relevant`。
- PASS: 上記を全て満たす1件以上。FAIL: 完全母集団で0件、形式ラベルだけ、問いと回答が無関係。当該項目に既知FAILがなく、読めない原文/対応不能ならBLOCKED。
- negative/境界: 疑問符の有無だけでは決めない。質問を言い換えただけ、答えを避けた発言はFAIL。exact 1件でPASS。
- 責任/依存: 独立Reviewer。共通原本/manifest/correlationの可読性とB07の安全な閲覧境界。B02 PASSには依存せず、B02 FAILでも独立判定する。
- 証拠: private原文/checklist、publicには種別/count/result/hash。
- blocker: CONDITIONAL（B03–B05の全てにPASSなしの場合）。現在NOT RUN。

### P6-B04 — 主張へのrebuttal

- 目的: 先行する具体的主張への実質的反論を確認する。
- 手順: 全accepted `REBUTTAL` 候補についてsource主張を特定し、反論が内容・理由・結論のいずれかを否定/弱化するか、対象/order/visibility/channelを照合する。
- artifact/field: B03と同じ相関field、`speech_act.kind=REBUTTAL`、checklist `source_relevant`。
- PASS: 対応する反論1件以上。FAIL: 完全母集団で0件、単なる「違う」、対象不明、無関係。当該項目に既知FAILがなく原本欠落等で判定不能ならBLOCKED。
- negative/境界: 相手名を呼ぶだけ、同意、別話題はFAIL。主張の一部へ根拠付き反証する1件はPASS。
- 責任/依存/証拠: 独立Reviewer、共通原本/manifest/correlationの可読性とB07閲覧境界、private原文/checklist、public count/result/hash。B02 PASSには依存せず、B02 FAILでも独立判定する。
- blocker: CONDITIONAL。現在NOT RUN。

### P6-B05 — 新情報によるbelief/判断更新

- 目的: 新情報に対応した意見または判断の変更を確認する。
- 手順: 全accepted `OPINION_CHANGE` 等の候補から、先行新情報、変更前判断、変更後判断を原文、projected state、semantic actで特定し、因果的対応を照合する。
- artifact/field: generation `proposal`、prompt `state`/`memory.records`、`before_state_sha256`、相関するterminal/accepted receipt、decision text。hashだけではPASSにしない。
- PASS: 三要素が具体的に一致する1件以上。FAIL: 完全母集団で0件、同じ結論の言い換え、根拠なしtarget変更、hash変化だけ。当該項目に既知FAILがなく必要state/原文欠落で判定不能ならBLOCKED。
- negative/境界: 根拠追加のみで結論不変はB05 PASSにしない。明示的な判断変更1件でPASS。
- 責任/依存/証拠: 独立Reviewer、共通原本/manifest/correlationの可読性とB07閲覧境界、private raw、public count/result/hash。B02 PASSには依存せず、B02 FAILでも独立判定する。
- blocker: CONDITIONAL。現在NOT RUN。

### P6-B06 — pre-vote再評価

- 目的: 有効な投票前再評価を観測し、診断する。
- 手順: accepted voteのgeneration proposalとoffered targets/evidenceを照合し、`pre_vote_reassessment_count` を確認する。
- artifact/field: `semantic_counts.pre_vote_reassessment_count`, proposal `pre_vote_reassessment.option_id/preferred_target_player_id/ranked_target_player_ids/evidence`、accepted vote terminal。
- PASS: 有効再評価≥1。結論不変でも根拠付き再評価なら可。FAIL: 0または無効。既知FAILがなくartifact欠落ならBLOCKED。
- negative/境界: ranked targetがoffered外、evidence未投影はFAIL。0件はFAILだが単独completion blockerではない。
- 責任/依存: Testerの機械照合。P6-A09は既にPASSだが実game結果へ流用しない。
- 証拠: private semantic raw、public count/result/hash。
- blocker: NO。現在NOT RUN。

### P6-B07 — private漏洩防止

- 目的: public/権限外AIへのprivate channel本文、own-private result、credential/token漏洩0を全原文で確認する。
- 手順: 全accepted textと各playerのbound context/visibility/offered actionを照合し、machine schema/authorization/linkage違反も0か確認する。
- artifact/field: generation prompt `context`/`memory`, decision text、terminal visibility、manifest linkage、checklist `privacy_safe`、aggregate `dimension_counts.privacy_safe.fail`, linkage/missing/corrupt/duplicate counts。
- PASS: 全populationでprivacy_safe fail 0、機械境界違反0、確認不能0。FAIL: 一件以上の漏洩。既知FAILがなく全件確認不能ならBLOCKED。
- negative/境界: credentialやprivate結果の転載/言換えはFAIL。自発role claim/合法な偽COは、それだけで漏洩にしない。offered disclosureと無許可開示を区別する。
- 責任/依存: Reviewerが原文、Testerが機械境界。B02–B05より優先するzero-tolerance。
- 証拠: owner-only raw/checklist/aggregate、publicはfail count/hashのみ。
- blocker: YES。現在NOT RUN。

### P6-B08 — bounded memory/prompt projection

- 目的: 実gameで投影されたmemory/state/context/promptが既定上限内で、必須triggerを保持したことを確認する。
- 手順: 全generationの `prompt_json` をcanonical parse/recomputeし、records/count/bytes/scalars/proxyとomission markersを照合する。
- artifact/field: prompt `memory.records/included_records/omitted_records/omitted_through_order/memory_byte_exhausted/token_proxy_exhausted`, `state.omitted_counts/state_projection_omitted`, generation `prompt_bytes/prompt_sha256`。
- PASS: 全件でold≤12/newest≤12/combined≤24、text・section・context/state/proposal/prompt/proxy各上限内、必須trigger保持、hash一致。
- FAIL: 一件でも超過、trigger欠落、不整合。既知FAILがなく必要prompt原本欠落ならBLOCKED。
- negative/境界: exact ceilingはPASS、1超過はFAIL。`before_state_sha256`だけでは全state量PASSの証拠にしない。未投影全state常時量は現行rawから取得不能として別記する。
- 責任/依存: Testerがcanonical/mechanical確認、Reviewerはprivate内容の権限だけ確認。
- 証拠: private generation raw、public max/count/result/hash。
- blocker: YES。現在NOT RUN。

### P6-B09 — 異常反復

- 目的: 同一player直前文一致0、全game同一正規化文3回以上0を確認する。
- 手順: processorが全accepted textをNFKC、strip、Unicode whitespace collapseで再計算し、Reviewer checklistと照合する。
- artifact/field: checklist `non_repetitive`、aggregate `dimension_counts.non_repetitive.fail`, `normalization_duplicate_count`, `population_count`。
- PASS: fail 0かつnormalization duplicate 0。FAIL: 直前一致または全game3回以上が一件以上。既知FAILがなく欠落/不完全populationならBLOCKED。
- negative/境界: 同一正規化文2回は全game閾値だけなら許容、3回はFAIL。ただし同一playerの直前一致は2回目でFAIL。
- 責任/依存: processor計算、Reviewer全population確認。
- 証拠: private原文/checklist、public counts/hash。
- blocker: YES。現在NOT RUN。

### P6-B10 — token/latency/queue/day実測

- 目的: 同一runの使用token、generation latency、queue wait、day所要時間をactual fieldから取得する。
- 手順: generation records、admission metrics、server phase wall、summary distributionを母数・hashと照合する。全 `PROVIDER_CALL_TERMINAL` を `(client_id, invocation_id, call_ordinal)` で一意化し、original/repairを別callとして各一回数え、全callのprompt/completion tokenを合計する。invocation単独dedupeやnullable usageの0補完をしない。
- artifact/field: generation `prompt_tokens/completion_tokens/latency_microseconds/prompt_bytes`; `PROVIDER_CALL_TERMINAL.client_id/invocation_id/call_ordinal/prompt_tokens/completion_tokens`; private算出 `total_prompt_tokens/total_completion_tokens/total_tokens`、実call count; metrics `queue_wait_microseconds`, provider timing fields; aggregate distributions; `phase_wall_durations[*].wall_microseconds`, `total_game_wall_microseconds`, `metrics_dropped`, `provider_timing_complete`。親runner wallとcleanup所要は起動前に定めた既存運用証拠から取得する。
- 追加診断: private generation recordを全件走査し、`status`、responseを持つstatusでのnullable `validation_code`、`attempt_ordinal`、v2 `prompt_rejection_code` の各内訳を別分布として記録し、互いに加算しない。response妥当性率を出す場合は分子を `DECISION|EXPLICIT_NO_DECISION|REPAIR_SUCCEEDED`、分母をresponseを持つ `DECISION|EXPLICIT_NO_DECISION|REPAIR_SUCCEEDED|OUTPUT_INVALID|REPAIR_FAILED` に限定して両countを併記する。分母0は `N/A`、破損・未読・母集団不完全なら全体率は `UNKNOWN` とする。prompt/backend/cancel等responseなしstatus、妥当statusでの非適用null `N/A`、欠測、v1 rejection reason欠測、partial observationを別々に数え、0や割合へ補完しない。generation/attempt内訳と `PROVIDER_CALL_TERMINAL` の実call countを同一視しない。
- R16診断手順: `Docs/ai/PHASE6_R16_REVIEW_ADOPTION.md` U2に従い、responseを持つ失敗の `finish_reason` / `completion_tokens` とprivate `response_text` / `response_bytes` / `validation_code` を照合する。空contentは現backendで `RESPONSE_ENVELOPE_INVALID` となりusage/finish欠測があり得るため、同codeやnull/bytes=0だけで空と断定しない。本文状態・上限到達・形式/値違反・UNKNOWNを別軸で記録し、reasoning原因を推測しない。追加診断でoracleを変更しない。
- PASS: 必須四種を非負数で全必要母数について取得し、三つのtoken totalと実call countが整合し、承認済みの外側有限上限・cleanup記録も存在する。FAIL: fieldの型/値不正、call identity重複/矛盾、rawと集計の既知不一致。既知FAILがなければ、call欠落、nullable欠測、metrics drop、raw 20,000切詰め、外側時計/cleanup所要欠測、確認不能はBLOCKED。
- negative/境界: 0µsはfield semantics上有効な場合のみ数値として扱う。性能値が高いだけならnon-blocker。deadline破壊はB01/B02で判定。
- 責任/依存: Tester。providerがusage/timingを返すことは起動前preconditionとして確認するがprobeはHuman Gate前にしない。
- 証拠: owner-only metrics、public distributions/count/hash。
- blocker: 既存必須四測定の欠落YES、性能値だけNO。追加generation診断の欠測だけではB10をBLOCKEDにせず、PASS/FAIL、mandatory式、閾値を変えない。現在NOT RUN。

### P6-B11 — output budget/population境界

- 目的: request上限512、length拒否、accepted population≤512、全原本保持を確認する。
- 手順: run metadata/backend fingerprint、全generation attempt/finish、manifest/accepted population、summary error/count/reasonを照合する。
- artifact/field: `summary.model_identity.config_fingerprint`, generation `backend`, `attempt_ordinal`, `finish_reason`, `prompt_tokens`, `completion_tokens`, manifest `accepted_text.record_count`, semantic `accepted_text_count`; overflow時summary `failure_reason=ACCEPTED_TEXT_POPULATION_EXCEEDED`。
- PASS: provider request cap 512が実効設定にbinding、length response不採用、repair≤1/同一lease、accepted≤512、全件manifest/shard保持。
- FAIL: cap違反、length受理、513以上、sampling/分割/原本消失。513を固定count/reasonで検出した場合はmanifest未発行が併存してもFAIL。既知違反がなくmanifest欠落で完全性だけ判定不能ならBLOCKED。
- negative/境界: accepted 512はPASS、513はFAIL。これはlive stopではなく終了後collector検出。総game tokenを512と比較しない。
- 責任/依存: Tester。Reviewerはpopulation全件性をprocessorで再照合。
- 証拠: owner-only raw/manifest/summary、public count/reason/hash。
- blocker: YES。現在NOT RUN。

## 最終集計式

```text
conversation = B02 AND (B03 OR B04 OR B05)
mandatory = B01 AND conversation AND B07 AND B08 AND B09 AND B10_measurement AND B11
```

B06、`coherent`、`objective_consistent`、旧 `source_relevant` 全件PASSは評価・診断として報告するが、D072/R1にないhidden mandatory gateへしない。mandatoryにFAILがあればPhase 6 FAIL、FAILがなくBLOCKEDがあればBLOCKED、全てPASSの場合だけclosure候補である。
