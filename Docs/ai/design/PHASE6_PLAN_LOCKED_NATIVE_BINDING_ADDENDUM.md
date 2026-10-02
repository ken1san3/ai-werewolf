# Phase 6 plan-locked native physical binding 補遺

Status: REQUESTED  
Task: T573  
Responsibility: Architect  
Contract: `PHASE6_PLAN_LOCKED_NATIVE_BINDING_V1`

## 1. 目的と不変境界

本補遺はT572が受け取るnormalized 96 rowsを、T567/T568の物理artifactへ一意に結ぶ。T569/T570のpublic packet、annotation、metric、UNKNOWN/MNO/N/A、A/B blind schemaは変更しない。normalized rowsや同一helperが算出した5 rootをauthorityにせず、Main独立custodyが固定したphysical rootsをauthorityとする。

provider、意味判定、本文regex、guard rawからの本文復元は行わない。96/77/7/12、control本文69/欠測8、question54を維持し、欠測・partial・integrity failureをPASSや本文へ補完しない。

## 2. authority所有者と外部pin

Main独立custodyはworker adapterとは別code pathで、次の物理ファイルを開始時と終了時に読む。各expected SHAはMainが承認済みgateとprepared locatorから固定し、adapter入力自身から採用しない。

```text
NativeAuthorityPinV1 exact keys:
  contract: "PHASE6_PLAN_LOCKED_NATIVE_BINDING_V1"
  prepared_locator_file_sha256: Digest
  input_file_sha256: Digest
  preflight_file_sha256: Digest
  source_manifest_file_sha256: Digest
  main_pin_file_sha256: Digest
  old_t550_seal_file_sha256: Digest
  measurement_results_file_sha256: Digest
  measurement_seal_file_sha256: Digest
  runner_safe_file_sha256, operator_safe_file_sha256,
  operator_seal_file_sha256: Digest
  source_roles_root_sha256: Digest
  response_members_root_sha256: Digest
  code_bundle_sha256: Digest
  expected_population_root_sha256: Digest
  pin_sha256: Digest
```

Mainはpathを公開packetへ入れず、private operator locatorからfixed owned rootを解決する。adapterは `validate_owned_layout` と同じ規則でroot直下の `input.json/preflight.json/source-manifest.json/main-pin.json`、`source/sources/<source_id>`、`measurement/results.json`、`measurement/response-<row_id>-<ordinal>.json`、`measurement/seal.json` だけを許す。absolute path、`..`、symlink、reparse point、root外、case-fold重複を拒否する。

## 3. 15-role sourceとprepared root

source manifestはexact `{contract,files,manifest_sha256}`、filesはsource_id昇順の15件で、role集合を次に固定する。

```text
t550_manifest, t550_seal, saved_rows, saved_requests, saved_outcomes,
saved_plans, fixture_inputs, runner_source, probe_source, product_source,
profile, config, model_metadata, design_approval, task_packet
```

各entryはexact `{source_id,role,original_name,sha256,size}`。physical bytesのsize/SHAを再計算し、role重複・欠落・extraを拒否する。`source_roles_root_sha256` はrole順の `{role,source_id,sha256,size}` 列のcanonical SHAである。

inputはexact prepared bundle `{contract,preflight,source_manifest,main_pin,rows}`。preflightは96/77/7/12、88、77/11、154、237、8192とsource/code/profile/config/model/runtime/old seal/design/approval rootsを外部expected値へ照合する。Main pinは `reconstruct_expected_pin` が15 physical rolesから再構築した値とexact一致し、adapterがnormalized rowsから再計算したpinをauthorityにしない。

T550 manifestのmember `{original_name,sha256}` 全件とold sealの同名SHAを一致させる。control native row、request、outcome、plan、fixtureのoriginal member name/SHAはこのmember集合のexact一件へ結ぶ。

## 4. 新measurement physical layout

```text
NativeMeasurementSealV1:
  exact map relative_member_name -> physical file SHA-256

NativeResultEnvelopeV1 exact keys:
  contract: "PHASE6_RESOLVED_SUBJECT_PLAN_LOCKED_V1"
  rows: tuple[NativeResultRowV1,...]  # prepared input順exact 96

NativeResultRowV1 exact keys:
  row_id: nonempty str
  status: "ACCEPTED" | "GUARD_REJECT" | "STRUCTURE_INVALID" | "LENGTH" |
          "MISSING_RESPONSE" | "TRANSPORT_GENERATION_LOST" |
          "TRANSPORT_OR_OWNERSHIP" | "CONTEXT_INVALID" |
          "INPUT_NOT_CHANGED" | "PLAN_NOT_OBSERVED" |
          "NOT_MESSAGE_DOMAIN" | "MEASUREMENT_NOT_OBSERVED"
  attempts: tuple[{ordinal:1|2,status:"ACCEPTED"|"GUARD_REJECT"|
                   "STRUCTURE_INVALID"|"LENGTH"|"MISSING_RESPONSE"|
                   "TRANSPORT_GENERATION_LOST"|"TRANSPORT_OR_OWNERSHIP"|
                   "CONTEXT_INVALID"},...]
  candidate_text: nonempty str | null
```

sealはseal自身を除くmeasurement配下の全fileをexact一回覆う。未列挙file、missing file、別SHA、duplicate/case-fold collisionを拒否する。resultsはsealの `results.json` SHAと外部expected result SHAの双方へ一致する。row_idはprepared 96と同順・同集合で重複なし、各 `(case_id,seed)` はstrict `seed:int`（bool禁止）で96一意、ordinalは1→2の順で最大2件である。

TARGET 77だけがattemptまたは `INPUT_NOT_CHANGED` を持てる。attempt非空ならresult statusは末尾attempt statusと同値で、ACCEPTEDは末尾だけ、ordinal 2はordinal 1が非ACCEPTEDのときだけ許す。`INPUT_NOT_CHANGED` はattempt空・text null・generation 0。`PLAN_NOT_OBSERVED=7` と `NOT_MESSAGE_DOMAIN=12` はattempt空、candidate text nullでprepared source statusと同値である。run途中停止で未処理のTARGETは `MEASUREMENT_NOT_OBSERVED`、attempt空、text nullを許し成功へ数えない。

provider count authorityはoperator sealが覆うphysical `runner-safe.json` exact pointer `/provider_calls` のstrict int（bool禁止、0..154）である。`operator-safe.json` の同値pointerと一致させ、safe値単体はtrustしない。measurement sealが覆うattempt filesの集合とresults全attempt列はexact一致させる。COMPLETEでは総attempt件数=`provider_calls`。途中停止UNKNOWNでは総attempt件数は`provider_calls`または`provider_calls + 1`だけを許し、後者は末尾のactive attemptがresponse無しの`TRANSPORT_OR_OWNERSHIP`である場合に限る。response filesはattempt subsetで、各ACCEPTED attemptはresponse exact一件である。operator sealはrunner-safe、operator-safe、measurement seal/results/attempt/responseのphysical SHAを覆い、外部expected operator seal SHAへ一致させる。

## 5. native rowとfixture association

```text
NativeRowBindingV1 exact keys:
  ordinal: strict int 0..95
  row_id, case_id: nonempty str
  seed: strict int（bool禁止）
  population_status: "TARGET" | "PLAN_NOT_OBSERVED" | "NOT_MESSAGE_DOMAIN"
  prepared_row_sha256, native_member_sha256,
  fixture_binding_sha256, result_row_sha256: Digest
```

各prepared rowのordinalはinput list位置そのもので、0..95を欠落・重複なく使う。saved_rowsのembedded native `{original_name,member_sha256,value}` とT550 seal memberを再検証し、valueのcase/seed/plan/final/projection SHAをprepared rowへ結ぶ。fixture_inputsは32件のcurrent public fixture bytesとexact一致し、各rowのfixture_index、case ID、seed、source/projection/bindings/catalog SHAを一意に結ぶ。

question opportunityはpublic fixture metadataだけから再構築し、54件を固定する。本文やact literalからquestionを推定しない。populationはTARGET77、PLAN_NOT_OBSERVED7、NOT_MESSAGE_DOMAIN12で、control本文はsaved native finalのmessage memberが存在する69件だけOBSERVED、残り8件はNOT_OBSERVEDとする。

## 6. PublicTextBindingV1の物理導出

T569 `PublicTextBindingV1` の3 digestはadapterが次のexact locatorから導出し、caller値を受け取らない。

```text
NativeTextLocatorV1 exact keys:
  condition: "CONTROL" | "CANDIDATE"       # private mappingだけ
  row_id, case_id: nonempty str
  seed: strict int
  ordinal: 1 | 2
  source_role: "saved_rows" | "measurement_response"
  member_name: nonempty safe relative name
  member_sha256: Digest
  json_pointer: fixed str
  encoding: "JSON_STRING_UTF8"
  locator_sha256: Digest
```

| condition | source role | member name | ordinal | JSON pointer |
|---|---|---|---:|---|
| CONTROL | `saved_rows`（embedded old native member + T550 seal） | prepared native `original_name` exact値 | 1 | `/final/message` |
| CANDIDATE | `measurement_response` | `response-<row_id>-<terminal ordinal>.json` | terminal 1または2 | `/choices/0/message/content` |

CONTROL ordinalは比較上の固定1で、旧native finalへordinal fieldを発明しない。CANDIDATE terminal ordinalはresults末尾ACCEPTED attemptと一致する。member名とpointerは上表以外を許さない。parsed object payloadは両conditionともexact `{"message": nonempty Unicode str}` である。

- `member_sha256` はCONTROLではT550 sealがpinしたoriginal native member SHA、かつphysical `saved_rows` snapshot内embedded member SHAと一致させる。CANDIDATEではmeasurement sealがpinしたphysical response file bytes SHAである。
- `locator_sha256` は自身を除くlocator全fieldのcanonical SHAである。row/case/seed/ordinal、source role、member名、pointerが違うlocatorを拒否する。
- `parsed_result_sha256` は既存generation-v2 message parserが返したpublic parsed objectのcanonical SHAである。CONTROLはsaved native final `{"message": nonempty str}`、CANDIDATEはACCEPTED terminal ordinalのresponse `/choices/0/message/content`を既存parserへ通した同objectを使う。
- public `text` はparsed objectのmessageとbyte-equivalentなUnicode strで、trim、deduplicate、normalizationをしない。

CANDIDATE ACCEPTEDはterminal accepted ordinalのresponse fileがexact一件あり、result candidate_text、parsed message、binding3 SHAを全て一致させる。別row、別ordinal、未seal response、任意hex bindingを拒否する。GUARD_REJECT/STRUCTURE_INVALID/LENGTH/MISSING/transport/context failureは、runnerが保存したpublic parsed message memberが存在し上記parser/locator/hashが全成立する場合だけOBSERVEDにできる。現runnerがparsed memberを保存しないfailureはNOT_OBSERVED/null/nullであり、raw responseから復元しない。

## 7. 5 native rootのexact payload

全payloadはUTF-8、object key昇順、separator `(',', ':')`、非ASCII保持のcanonical JSONでdigestする。boolをintとして受理せず、非有限数を拒否する。`PhysicalFileRefV1` はexact `{role: str, relative_name: str, sha256: hex64, size: strict int >= 0}` で、`sha256` はphysical file bytesのSHA-256である。parsed objectのcanonical SHAとは区別し、相互代用しない。root payload自身にはself hashを入れず、外側のroot fieldがpayload全体のcanonical SHAを保持する。

```text
PreparedRootPayloadV1 exact keys:
  contract: "T568_PREPARED_ROOT_PAYLOAD_V1"
  input: PhysicalFileRefV1
  preflight: PhysicalFileRefV1
  source_manifest: PhysicalFileRefV1
  main_pin: PhysicalFileRefV1
  old_t550_seal: PhysicalFileRefV1
  source_roles: tuple[PhysicalFileRefV1, ...]

SourceRowsRootPayloadV1 exact keys:
  contract: "T568_SOURCE_ROWS_ROOT_PAYLOAD_V1"
  rows: tuple[NativeRowBindingV1, ...]
  control_locators: tuple[NativeTextLocatorV1, ...]

NativeResultBindingV1 exact keys:
  ordinal: strict int
  row_id: str
  result_row_sha256: hex64
  attempt_member_refs: tuple[PhysicalFileRefV1, ...]

ResultRowsRootPayloadV1 exact keys:
  contract: "T568_RESULT_ROWS_ROOT_PAYLOAD_V1"
  results_file: PhysicalFileRefV1
  rows: tuple[NativeResultBindingV1, ...]

ResponseMemberBindingV1 exact keys:
  row_ordinal: strict int
  row_id: str
  attempt_ordinal: strict int
  file: PhysicalFileRefV1
  locator: NativeTextLocatorV1

ResponseMembersRootPayloadV1 exact keys:
  contract: "T568_RESPONSE_MEMBERS_ROOT_PAYLOAD_V1"
  members: tuple[ResponseMemberBindingV1, ...]

SealCoverageRefV1 exact keys:
  relative_name: str
  member_sha256: hex64

PhysicalSealRootPayloadV1 exact keys:
  contract: "T568_PHYSICAL_SEAL_ROOT_PAYLOAD_V1"
  measurement_seal: PhysicalFileRefV1
  runner_safe: PhysicalFileRefV1
  operator_safe: PhysicalFileRefV1
  operator_seal: PhysicalFileRefV1
  measurement_coverage: tuple[SealCoverageRefV1, ...]
  operator_coverage: tuple[SealCoverageRefV1, ...]

NativePopulationRootsV1 exact keys:
  contract: "T568_NATIVE_POPULATION_ROOTS_V1"
  prepared_root_sha256: hex64
  source_rows_root_sha256: hex64
  result_rows_root_sha256: hex64
  response_members_root_sha256: hex64
  physical_seal_root_sha256: hex64
  roots_sha256: hex64
```

`source_roles` は§3の15 role順、`rows` はordinal 0..95、`control_locators` は対象69行のrow ordinal順、`attempt_member_refs` はattempt ordinal順、`members` はrow ordinal・attempt ordinal順である。coverageはUTF-8 byte順の`relative_name`昇順で、duplicate/missing/extraを拒否する。`roots_sha256` は自身を除く`NativePopulationRootsV1`全fieldのcanonical SHAである。Main custodyはphysical filesから5 payloadとrootを独立再構築し、T572 adapterは別code pathでphysical files、外部root、authority pinを再検証する。normalized rowsからexpected rootを作らない。

authority取得順は固定する。Mainは測定開始前にsafe別identityとして保存済みのinput、preflight、source manifest、Main pin、15 source roles、old T550 sealのphysical SHA/sizeをexternal pinへ固定する。独立Testerのrun後、MainはTesterが保存したsafe locatorからmeasurement rootを解決し、physical results、attempt、response、measurement seal、runner-safe、operator-safe、operator sealのbytesを読んで後半rootを構築する。worker/adapterはcaller supplied rowsをhashして同じ値をexpectedとして返してはならず、Main external pinとMain custody再構築値を入力として照合する。prepared側とmeasurement側のidentityを同じcaller objectから派生させない。

## 8. T570 freezeとの限定互換境界

T564はdesign taskであり、実測`T564 final freeze` artifactは存在しない。したがってT570 `PlanLockedPacketFreezeV1.t564_final_freeze_sha256` へT568 native root、T564 design SHA、sentinelのいずれも格納しない。field意味をoverloadせず、本native経路はV1 freezeを発行しない。凍結済みT569/T570文書・V1 builder・V1 validatorは変更しない。

public packet、annotation、5 metricの型は維持する。native physical bindingを使う経路だけ、独立承認後のT572で次のV2専用builder/validatorを追加する。

```text
PlanLockedNativePacketFreezeV2 exact keys:
  contract: "PHASE6_PLAN_LOCKED_NATIVE_PACKET_FREEZE_V2"
  version: "V2"
  source_manifest_sha256: hex64
  native_authority_pin_sha256: hex64
  native_population_roots_sha256: hex64
  population_sha256: hex64
  mapping_sha256: hex64
  rubric_sha256: hex64
  packet_sha256: hex64
  packet_binding_sha256: hex64
  rows: 96
  target_rows: 77
  nontarget_rows: 19
  side_a_body_rows: strict int
  side_b_body_rows: strict int
  both_body_rows: strict int
  side_a_only_body_rows: strict int
  side_b_only_body_rows: strict int
  neither_body_rows: strict int
  question_opportunities: 54
  question_target_rows: strict int
  question_plan_missing_rows: strict int
  question_non_message_rows: strict int
  freeze_sha256: hex64
```

`freeze_sha256` は自身を除く全fieldのcanonical SHAである。side body partitionは合計77、question 3分類は合計54で、packetの公開rowから機械的に再計算する。private `packet_binding_sha256` は各blind row/private mappingをnative row、locator、5 rootへ一方向に結ぶ。public packetからprivate rootを復元できるfieldは加えない。このV2追加は既存V1の互換改変ではなく限定amendmentであり、本designの独立APPROVEDを実装gateとする。

### 8.1 V2 annotation lifecycle

T572はV1関数へV2 objectを渡さず、次のpure APIを同moduleへ追加する。

```text
validate_native_packet_freeze_v2(
  packet, mapping, packet_freeze_v2,
  *, expected_packet_freeze_sha256,
  expected_source_manifest_sha256,
  expected_native_authority_pin_sha256,
  expected_native_population_roots_sha256,
  expected_population_sha256, expected_mapping_sha256,
  expected_rubric_sha256, expected_packet_sha256,
  expected_packet_binding_sha256
) -> PlanLockedNativePacketFreezeV2

freeze_annotation_v2(
  annotation, packet, mapping, packet_freeze_v2,
  *, evaluator_identity,
  expected_evaluator_identity_sha256,
  expected_packet_freeze_sha256,
  expected_source_manifest_sha256,
  expected_native_authority_pin_sha256,
  expected_native_population_roots_sha256,
  expected_population_sha256, expected_mapping_sha256,
  expected_rubric_sha256, expected_packet_sha256,
  expected_packet_binding_sha256
) -> PlanLockedNativeAnnotationFreezeV2

unblind_v2(
  annotation, packet, mapping, packet_freeze_v2,
  annotation_freeze_v2,
  *, evaluator_identity,
  expected_evaluator_identity_sha256,
  expected_annotation_freeze_sha256,
  expected_packet_freeze_sha256,
  expected_source_manifest_sha256,
  expected_native_authority_pin_sha256,
  expected_native_population_roots_sha256,
  expected_population_sha256, expected_mapping_sha256,
  expected_rubric_sha256, expected_packet_sha256,
  expected_packet_binding_sha256
) -> existing T570 unblind result shape
```

各APIは最初に`validate_native_packet_freeze_v2`を呼び、external expected値とsource/native authority/native population/packet/mapping/rubric/population/bindingの全rootを照合する。`freeze_annotation_v2`はその後、frozen T570 helperの`validate_annotation`を直接一回呼び、`PlanLockedAnnotationEnvelopeV1`、row順、5 metric順、value/reason/path、観測有無、TARGET77/NOT_REQUIRED19、evaluator identity proofを共有validatorで検査する。T572はmetric合法表やannotation row validatorを複製しない。V2 objectはT570 `validate_packet_freeze`、`freeze_annotation`、`unblind`へ渡さず、V1用`t564_final_freeze_sha256`を補完しない。

```text
PlanLockedNativeAnnotationFreezeV2 exact keys:
  contract: "PHASE6_PLAN_LOCKED_NATIVE_ANNOTATION_FREEZE_V2"
  version: "V2"
  packet_sha256: hex64
  rubric_sha256: hex64
  packet_freeze_sha256: hex64
  native_authority_pin_sha256: hex64
  native_population_roots_sha256: hex64
  source_manifest_sha256: hex64
  annotation_sha256: hex64
  annotation_rows_sha256: hex64
  evaluator_identity_sha256: hex64
  rows: 96
  annotated_rows: 77
  not_required_rows: 19
  side_a_metric_value_reason_counts: exact T570 5-metric closed count object
  side_b_metric_value_reason_counts: exact T570 5-metric closed count object
  both_body_rows: strict int
  side_a_only_body_rows: strict int
  side_b_only_body_rows: strict int
  neither_body_rows: strict int
  question_opportunities: 54
  fresh_independent_evaluator: true
  freeze_sha256: hex64
```

`freeze_annotation_v2`はT570のexport済み`METRICS`、`VALUES`、`POSITIVE`、`NEGATIVE`を唯一のclosed vocabularyとしてcount objectを初期化し、共有`validate_annotation`が受理したrowだけを集計する。これは意味判定の再実装ではなく、validated enumの機械countである。body partitionはpacket freeze V2とexact一致させ、self hashは自身を除く全fieldから作る。evaluator identityの生値はfreezeへ保存せず、external expected identity SHAと`evaluator_identity_sha256`を一致させる。

`unblind_v2`は上記2 validatorを再実行し、annotation freezeを同じpure builderで再構築してexternal expected annotation freeze SHAとobject全体を照合してからだけprivate mappingを適用する。T570の公開mapping schemaとpair比較規則をそのまま使い、独自metric解釈を加えない。全96、TARGET77、非対象19、control observed 69/control missing 8、body partition 77、question opportunity 54、condition別5 metric count、各metricの`IMPROVED|REGRESSED|UNCHANGED|COMPARISON_UNKNOWN|NOT_APPLICABLE` pair countを再計算し、各閉集合の合計を照合する。candidate-onlyを改善へ数えない。返却shapeと意味はT570 unblind resultと同じであり、V2 authority fieldsはprivate custodyにだけ残す。

## 9. code bundle

```text
CodeBundleEntryV1 exact keys:
  role: str
  repository_path: str
  sha256: hex64
  size: strict int >= 0

NativeCodeBundleV1 exact keys:
  contract: "T573_NATIVE_CODE_BUNDLE_V1"
  files: tuple[CodeBundleEntryV1, ...]
  bundle_sha256: hex64
```

`files` は次のexact順で、追加・欠落・並替えを拒否する。

1. `T572_ADAPTER` — `scripts/phase6_plan_locked_packet_binding.py`
2. `MAIN_CUSTODY` — `tests/fixtures/phase6_plan_locked_native_custody.py`
3. `T567_PROBE` — `scripts/phase6_resolved_subject_probe.py`
4. `T567_CUSTODY` — `scripts/phase6_resolved_subject_custody.py`
5. `T570_RUBRIC` — `tests/fixtures/phase6_plan_locked_rubric.py`
6. `T569_DESIGN` — `Docs/ai/design/PHASE6_PLAN_LOCKED_RUBRIC_DESIGN.md`
7. `T570_REVIEW` — `Docs/ai/handoffs/tasks/T570_PLAN_LOCKED_RUBRIC_TOOL_REVIEW.md`
8. `T573_DESIGN` — `Docs/ai/design/PHASE6_PLAN_LOCKED_NATIVE_BINDING_ADDENDUM.md`
9. `T573_REVIEW` — `Docs/ai/handoffs/tasks/T573_PLAN_LOCKED_NATIVE_BINDING_DESIGN_REVIEW.md`

`bundle_sha256` は自身を除く全fieldのcanonical SHAで、各entryはcurrent physical bytesのSHA/sizeである。開始時とartifact seal直前に再照合する。`MAIN_CUSTODY`は承認後に新規実装するため、現design時点ではbundle gate未成立である。design/review SHAをnative result rootの代替に使わない。

## 10. failureと責務分離

physical source/seal/row/locator/responseのintegrity failureはpacketを作らずglobal stopとする。個別rowで合法に本文が無い場合は96分母を維持しMNO/UNKNOWNへ写す。hostは欠測本文、chosen intent、metric、PASSを生成しない。

- Main custody: external expected hash、physical root、15 roles、Main pin、seal coverageを再構築。
- T572 adapter: physical入力を再検証し、normalized public rowsとprivate mappingをlossless生成。
- Reviewer: design/toolとnegativeを独立確認。
- Evaluator:freeze済みpublic packetだけを意味評価し、private locator/mappingを読まない。

## 11. acceptance tests

positiveはpublic syntheticのphysical 15-role source、old seal、96 prepared/result rows、69/8 control、54 question、candidate response/sealを実fileとして作り、Main別root→adapter→T570 packet/freezeまで通す。

negativeは、15 roleのmissing/extra/reorder、source file size/SHA、old seal member、input/preflight/Main pin、results file、measurement seal coverage、response member、locator pointer/member/ordinal、row ordinal/order、cross-row/case/seed、duplicate identity、bool seed、fixture association、非対象status/attempt/text、ACCEPTED parsed response/result mismatchを各一件変更し、自己hashを更新しても外部pinで拒否する。guard rawだけから本文を復元するfixtureも拒否する。加えてV2 objectをT570 V1 validator/lifecycleへ渡す、V1 `t564_final_freeze_sha256`を偽造・補完する、native authority/population root、source root、packet/mapping root、annotation freeze、evaluator identity、96/77/19、body partition、69/8、54 question、condition metric count、pair outcome countのいずれかを自己整合tamperする負例を必須とする。

独立design APPROVED後にT572だけを限定修正する。Python 3.10/3.13 focused、public96 physical E2E、関連T570回帰、`check_docs.py`、独立tool reviewを要求する。実measurement、provider、private適用、意味評価は別gateである。
