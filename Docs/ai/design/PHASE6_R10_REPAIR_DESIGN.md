# Phase 6 R10 最小修復設計

Status: DRAFT  
Owner: T321 Architect  
Implementation owner: T319 Main Integrator

T322 の fresh 独立承認前は実装禁止。

## 1. 目的と権限境界

T316B の既存原本と FAIL/BLOCKED 判定を変更せず、次回の承認済み run で次の三つを
fail-closed のまま正しく記録・収集できるようにする。

1. collector が成功 generation だけに response schema/proposal 一致を要求し、失敗 generation
   も typed status、digest、identity、terminal coverage を満たす証拠として会計する。
2. `PROMPT_REJECTED` に安全な有限 enum の理由を残す。
3. 一つの capture/lease 内の初回と repair が admission transport 上で別 request identity を使う。

本設計の semantic PASS authority は既存の `_phase6_semantic_population` のままとする。
accepted-only population と全 attempt/terminal 会計を分離するが、競合する判定器や新audit
frameworkは作らない。旧rawの再書込み、旧判定の再解析、provider操作、実game、schema・offered
action・budget・timeout・repair回数の緩和はしない。

## 2. 調査結果の採否

### 2.1 M1 collector — 修復する

現実装は generation status を成功三種と `CANCELLED` に限定し、全recordへ
`backend_error_code is None`、`validation_code is None`、full capture projection、成功response schema
適合を要求する。このため、typed contract上正当な `PROMPT_REJECTED`、`BACKEND_FAILED`、
`OUTPUT_INVALID`、`REPAIR_FAILED` を含む shard をcollector自身が拒否する。

### 2.2 M2 repair prompt size — 変更しない

T320実測では16/16件がbytes 32,768以内、excerpt空、proxy 8,209–8,370で8,192超過だった。
これは既存詳細設計 §6.7 の「元projectionを固定し、excerptだけを削減し、それでも収まらなければ
backendを呼ばず拒否」に一致する。memory再投影、上限拡大、fixed overhead削減をR10修復へ含めない。

### 2.3 M3 output invalid — 今回は変更しない

T320の26件はJSON Schema直接違反1件、schema通過後constructor拒否12件、
`VALUE_NOT_OFFERED` 13件だった。内訳はreaction trigger、CO null条件、public claim、Unicode順、
none/option null条件、PEER_CHAT reaction、pre-vote reassessment、projection/capture一致であり、
既存の閉じたschema・意味規則・offered bindingが意図どおり拒否した結果である。
「additionalProperties違反13件」「提示外投票先13件」やモデル能力単独原因という説明は採用しない。
canonical semanticsを変えずに品質改善を保証する一意の修正は確定していないため、prompt全面改稿、
schema緩和、parser緩和、新retryを行わない。将来変更するなら別の原因・acceptance契約を要する。

### 2.4 M4 prompt rejection reason — 修復する

`PromptProjectionError.code` は呼出側の例外には残るが `_write_prompt_rejection` からgeneration
recordへ渡らない。`validation_code` は `OUTPUT_INVALID|REPAIR_FAILED` 専用なので流用しない。

### 2.5 admission repair identity — 修復する

初回とrepairは同じ logical `request_id = "phase6:" + capture_id` を
`AdmissionSession.generate` へ渡すため、同一leaseのrequest ID重複拒否によりrepair 10件が
`ADMISSION_PROTOCOL` となる。capture/audit/terminalのlogical identityはcanonicalどおり維持する。

## 3. 詳細契約

### 3.1 generation recordの有限な失敗理由

`AiDiscussionGenerationRecord` の末尾に field
`prompt_rejection_code: PromptRejectionCode | None` を追加し、新規writerが出すschema versionを
`aiwolf.ai-discussion-generation.v2` とする。
`PromptRejectionCode` は文字列enum `PROMPT_INVALID|PROMPT_TOO_LARGE` の二値だけとする。

- `status == PROMPT_REJECTED` のときだけ非nullを必須とする。
- その他のstatusでは必ずnullとする。
- `validation_code` と `backend_error_code` の既存status対応は変えない。
- v2 writerは全statusでfieldを常に出力し、`_write_prompt_rejection` は捕捉した
  `PromptProjectionError.code` をenumへ閉じて渡す。未知codeはaudit失敗として停止する。
- collectorは既存v1を旧exact field集合かつreason fieldなしとしてだけ許し、全statusの旧recordを
  読める。v1 `PROMPT_REJECTED` の理由は `UNKNOWN_LEGACY` 等に補作せず欠測件数だけを集計する。
- v2は「旧集合 + `prompt_rejection_code`」のexact field集合だけを許し、field欠落、未知field、
  未知enum、statusとの不一致を拒否する。時刻、file shape、run名からversionを推定しない。
- v1にreason fieldがある形、v2にreason fieldがない形も拒否する。任意の追加fieldを許す一般化は
  しない。
- serializerはrecord型の `schema_version` に従う。既存v1 bytesの読取りだけを残し、writerの
  新規generation生成入口は常にv2を構築する。terminal schemaは変更しない。terminalは従来どおり
  完全なgeneration record bytesのdigestとaudit sequenceを参照するため、v1/v2を同じ強度で結合する。

実装形は既存 `AiDiscussionGenerationRecord` v1を変更せず、共通既存invariantを継承するv2 recordを
追加して、v2固有schema versionとreason invariantだけを加える形を優先する。これにより既存v1
serializer/fixture bytesを暗黙にv2へ変換しない。別実装形を選ぶ場合も上のraw互換とexact shapeを
同じtestsで証明する。

旧rawは変更しない。次回runの新規recordはv2だけなので、v1互換は履歴読取りに限られる。

### 3.2 collectorのstatus別検証

raw dictをstatus別に独自再定義せず、top-level enumに加え、backend、decision、proposal、
speech act、各update、pre-vote、evidence/referenceを既存strict constructorへ再帰的に復元してから
generation recordの `__post_init__` を全generationへ適用する。未知nested field/enum、重複、
visibility/nullability違反を復元途中で拒否する。これによりresponse三点組、
provider metadata、backend/validation/prompt-rejection code、decision/proposal/digestのnullabilityを
既存typed contractで検証する。v1はreason fieldなしの旧record constructor、v2は新record
constructorを用い、v1 PROMPT_REJECTEDへreason値を生成して渡さない。

その後の検証を次の二層に分ける。

1. **全generation共通**: player/game/request/capture/attempt identity、prompt bytes/hash、
   responseが存在する場合のbytes/hash、context/before-state hash、base revision、重複keyを検証する。
   full projectionには既存 `_validate_phase6_captured_identity` と context hash を適用する。
   `aiwolf.discussion-prompt-rejected.v1` の閉じたmarkerは `PROMPT_REJECTED` だけに許し、exact key集合、
   marker schema version、二値code、prompt hashを検証する。generation v2ではmarker codeとrecordの
   `prompt_rejection_code` の一致も必須とする。reason概念のないgeneration v1では一致を補作せず、
   marker code自体を検証したうえでrecord reason欠測countへ含める。full captureが無いことを成功証拠へ
   補作しない。
2. **成功三statusだけ**: `DECISION|EXPLICIT_NO_DECISION|REPAIR_SUCCEEDED` に既存response JSON
   parse、保存output schema適合、response discussionとproposal一致、decision/action identity一致を
   全て要求する。失敗statusのresponseはdigestを保持するが、invalid responseへ成功schema適合を
   要求しない。

`OUTPUT_INVALID|REPAIR_FAILED` のresponse本文はprivate shardに残し、public aggregateやhandoffへ
出さない。`BACKEND_FAILED|CANCELLED|PROMPT_REJECTED` はtyped contractどおりresponseなしである。

T320が確認したT316Bの `PROMPT_REJECTED` 16件はすべてrepair attemptで、保存promptはmarkerではなく
元projectionとrepair instructionを含むfull projectionである。これらはfull captured identity経路で
検証し、reasonは旧v1 record上の欠測として数える。marker経路は初回projection失敗の既存生成経路と
fixtureを対象に維持する。

### 3.3 terminal、accepted population、集計

全captureについて従来どおりterminalをexact 1件要求し、terminalが指す
`(player, logical request_id, final_attempt_ordinal)`、audit sequence、generation record digest、
capture/context/state/proposal/base/day/phase/game identityを検証する。全generation capture集合と
terminal capture集合が一致しない場合は停止する。

- `ACCEPTED` terminalは成功三statusのgenerationだけを参照できる。失敗generation参照は拒否する。
- accepted decision/offered action/visibility/evidence/order/server receiptの既存検証を全て維持する。
- failed/incomplete gameのaccepted件が正しく結合できる場合も、accepted populationは
  accepted receiptだけから作る。ただし全attempt/terminal coverageが不明・破損ならmanifestを
  発行せず閉じる。
- aggregateの `generation_count` は全generation attempt数、accepted countはaccepted-onlyとする。
  status別generation countとprompt rejection reason別countを追加しても、private本文、player、
  capture ID、pathはpublic summaryへ出さない。
- `_write_phase6_evidence` のpublication順は維持し、collector成功後にaccepted artifact、最後に
  manifestを一回だけ原子的に発行する。collector失敗時はmanifestを発行しない。
- collector成功はgame/Stage B PASSを意味しない。failed/incomplete game、backend failure、
  prompt rejection、invalid outputの存在は既存B01–B11判定へそのまま渡し、PASSへ読み替えない。

### 3.4 logical identityとtransport identityの分離

`LLMBrain` 内のlogical `request_id` は全attemptで引き続き `phase6:<capture_id>` とし、generation
record、generation ack、terminal correlationの値を変えない。backendへ渡す
`StructuredGenerationRequest.request_id` だけを transport identity とする。

Phase 6 contextful callでは、各 `_attempt` が
`<logical request_id>:attempt:<attempt_ordinal>` を生成し、ordinal 1/2を別transport IDとして
backendへ渡す。構成は固定文字列と検証済みordinalだけで行う。context-free既存経路は現在の
request IDを維持する。repairは同じadmission lease、同じinvocation、最大2 call、call ordinal
1/2のままであり、新lease、新retry、新budgetを作らない。audit `_write_record` にはtransport IDを
渡さずlogical IDだけを渡す。

backendの `StructuredGenerationResponse.request_id` は各callのtransport IDと一致しなければならず、
既存response envelope照合もtransport IDを期待値として使う。response IDをlogical IDへ書き換えない。
generation recordはresponse IDを保存せず、logical IDとattempt ordinalでaudit/terminalを結ぶ。

transport IDはprivate provider/admission相関用であり、accepted/server identityへ昇格させない。
同一lease内の重複transport ID、ordinal不一致、3回目callは既存どおり
`ADMISSION_PROTOCOL` でfail-closedとする。

## 4. exact編集allowlistと実装順

変更を許すのは次だけである。

1. `ai_client/llm/types.py`: v1読取り互換、v2 record、`PromptRejectionCode` とstatus整合。
2. `ai_client/llm/brain.py`: rejection code伝播、Phase 6 transport request ID派生。
3. `scripts/run_phase5_local_smoke.py`: typed status別collector、旧v1限定互換、集計。
4. `scripts/phase6_private_review.py`: generation consumerをexact v1/v2だけへ拡張し、collectorで
   検証済みの両versionをdigestでterminal/accepted rowへ結ぶ。未知versionや未検証shapeは許さない。
5. `tests/test_phase6_semantic_completion.py`: failed generation/marker/terminal/accepted境界。
6. `tests/test_phase5_brain_admission.py`: 既存の同一lease、最大2 call、ordinal回帰を維持。
7. `tests/test_phase6_discussion_transaction.py`: generation v1/v2、hash、prompt rejection、terminal結合に
   加え、既存 `_controller_case` contextful fixtureとactual `BrokerAdmissionSession`を組み合わせた
   repair transport/logical identity結合testを置く。別test fileのprivate helperはimportしない。
8. `tests/test_phase6_private_review.py`: v1/v2/mixed shardのdigest結合と未知version拒否。
9. 必要な既存契約回帰だけを `tests/test_phase6_semantic_output.py`、
   `tests/test_phase5_local_smoke.py`、`tests/test_phase4_llm_brain.py` に追加する。
   新test file、fixture frameworkは作らない。

`prompt.py`、discussion model/projection、game/server/provider/broker protocolを
変更しない。もし実装にこれ以外のsource変更が必要ならT319は停止し、T321/T322契約の再評価へ戻す。

実装順は (a) enum/typed invariant、(b) brain reason/transport分離、(c) collector status分岐、
(d) focused tests、(e)固定回帰である。

## 5. acceptance と named tests

| Acceptance | 必須 named test |
|---|---|
| 成功、OUTPUT_INVALID、REPAIR_FAILED、BACKEND_FAILED、CANCELLED、PROMPT_REJECTEDをtyped契約どおり収集し、成功だけschema検査 | `tests/test_phase6_semantic_completion.py::test_p6f_machine_semantic_authority_links_exact_private_population`、同fileへ追加する `test_p6f_machine_semantic_authority_accounts_failed_generations_without_accepting_them` |
| marker型PROMPT_REJECTEDはreason一致時だけ許し、未知enum/field/mismatchを拒否 | 同fileへ追加する `test_p6f_prompt_rejection_marker_is_closed_and_reason_bound` |
| terminal coverage/digest/identityを全statusで維持し、ACCEPTED→failed generationを拒否 | 既存 `test_p6f_machine_semantic_authority_rejects_corrupt_or_ambiguous_evidence` と追加testのmutation cases |
| failed/incompleteでもaccepted-only populationを保持するが、不完全coverageではmanifest非発行 | 既存 `test_p6f_final_manifest_is_published_once_and_failures_remain_closed` と追加test |
| rejection reasonを新規recordへ保存し、validation_codeを汚染しない | `tests/test_phase6_semantic_completion.py` の追加test、既存discussion transactionのprompt rejection cases |
| repair 2 callが同一lease内で一意transport ID、response envelopeもtransport一致、logical audit/terminal ID同一、ordinal 1/2 | `tests/test_phase6_discussion_transaction.py` へ追加する `test_contextful_repair_uses_distinct_transport_ids_and_preserves_logical_identity`。既存 `_controller_case` とactual `BrokerAdmissionSession` を組み合わせ、別testのprivate helperをimportしない。`tests/test_phase5_brain_admission.py::test_one_commit_wraps_both_llm_repair_ordinals_in_one_lease` も維持 |
| private processorがv1/v2 accepted generationをdigestで復元し、混在shardでもterminal/server結合を維持、未知versionを拒否 | `tests/test_phase6_private_review.py` へ追加する `test_private_review_accepts_exact_v1_v2_generation_linkage_and_rejects_unknown_version` |
| privacy、schema、budget、repair一回、旧context-free経路を維持 | 下記固定回帰 |

追加testの名前は上記で固定し、同じacceptanceの別frameworkを増やさない。literal mutationで
未知status、未知reason、余分field、reason欠落、status/reason不一致、failed generationをACCEPTED、
terminal欠落、digest不一致を少なくとも各1件含める。

## 6. 固定検証範囲

focused（各command timeout 180秒、実LLM/completion/provider/network/GPU不要）:

```text
python -m pytest -q -m "not completion" tests/test_phase6_semantic_completion.py
python -m pytest -q -m "not completion" tests/test_phase5_brain_admission.py
python -m pytest -q -m "not completion" tests/test_phase6_discussion_transaction.py
```

回帰（各command timeout 300秒、実LLM/completion不要）:

```text
python -m pytest -q -m "not completion" tests/test_phase6_semantic_output.py tests/test_phase5_local_smoke.py tests/test_phase6_private_review.py tests/test_phase4_llm_brain.py
python scripts/check_docs.py
```

全pytest commandで `completion` markerを明示除外するため、900秒synthetic実game、実subprocess game、
provider/network/GPUを起動しない。markerなしの既存回帰だけを実行する。

T323はT319実装後のsource hashと上記raw pass/failを独立測定する。T322 fresh ReviewerのAPPROVEDが
実装開始前に必須であり、T321は自己承認しない。

## 7. 停止境界

次のいずれかなら実装または収集を停止し、未知をPASSで埋めない。

- T322が本設計を承認しない、または実装diffがallowlistを超える。
- status/reason/fieldに未知値、typed invariantと既存raw factsの矛盾がある。
- full projectionでもmarkerでもないprompt、marker code不一致、identity/digest/visibility不正がある。
- 全captureのterminal coverage、accepted server receipt集合、manifest publication完全性を証明できない。
- transport ID分離にprotocol変更、新lease、追加retry、budget/timeout/schema緩和が必要になる。
- T320の未確定provider quiescence等を、この三修復だけで解決済みと主張する必要が生じる。

失敗generationを収集できることは、そのgame、provider quiescence、B01–B11、Phase 6の合格を
意味しない。旧T316BのFAIL/BLOCKEDと欠測は履歴として維持する。
