# Phase 6 saved control plan-locked 限定評価設計

Task: T574  
Status: DRAFT

## 1. 目的と非目的

保存済みT550 control本文だけをT569の5 metricでfreshに校正するtest-only modeを定義する。母集団はprepared順96、TARGET 77、非対象19（PLAN_NOT_OBSERVED 7、NOT_MESSAGE_DOMAIN 12）、control本文OBSERVED 69、欠測8、question opportunity 54で固定する。新candidate、paired改善、一般HARD、製品採用は判定しない。

旧annotation、旧score、旧評価raw、T566 verifier、T568 provider/result/freezeは入力にしない。NOT_MEASURED側へ本文、response、status、仮T564/T568 freeze、sentinel artifactを発明しない。frozen T567/T569/T570/T573と実装中T572は変更しない。

## 2. physical authority

Main custodyはprepared locatorが指すphysical input、preflight、source-manifest、Main pin、old T550 sealと15 source rolesを開始時とfreeze直前に独立検証する。15 roleはT573 §3のexact順・entry型を再利用する。saved control本文は`saved_rows` embedded native member、T550 manifest member、old sealの同名physical SHAが三者一致するものだけを使う。固定ChosenIntentはprepared row、saved plan、fixture binding、public reply/trigger/current stateの既存SHA/locatorにlossless結合する。本文やact literalからChosenIntent、question、populationを推測しない。

```text
SavedControlAuthorityPinV1 exact keys:
  contract: "PHASE6_SAVED_CONTROL_AUTHORITY_PIN_V1"
  prepared_locator_file_sha256: hex64
  input_file_sha256: hex64
  preflight_file_sha256: hex64
  source_manifest_file_sha256: hex64
  main_pin_file_sha256: hex64
  old_t550_seal_file_sha256: hex64
  source_roles_root_sha256: hex64
  saved_control_members_root_sha256: hex64
  chosen_intent_bindings_root_sha256: hex64
  fixture_bindings_root_sha256: hex64
  code_bundle_sha256: hex64
  expected_population_root_sha256: hex64
  pin_sha256: hex64
```

canonical wireはUTF-8、object key昇順、compact separator、非ASCII保持で、boolをintとして受理せず非有限数を拒否する。各file SHAはphysical bytes、各rootはexact ordered payloadのcanonical SHAで相互代用しない。`pin_sha256`は自身を除く全fieldのSHAである。Mainがexternal expected pinを作り、workerがcaller rowsからexpected値を自己生成することを禁止する。

```text
SavedControlRowBindingV1 exact keys:
  ordinal: strict int 0..95
  row_id, case_id: nonempty str
  seed: strict int（bool禁止）
  population_status: "TARGET" | "PLAN_NOT_OBSERVED" | "NOT_MESSAGE_DOMAIN"
  prepared_row_sha256, native_member_sha256,
  fixture_binding_sha256, chosen_intent_binding_sha256: hex64
  control_locator: NativeTextLocatorV1 | null
  row_binding_sha256: hex64
```

TARGET 69行だけcontrol locatorを持ち、T573のCONTROL exact locator（source role `saved_rows`、prepared `original_name`、ordinal 1、pointer `/final/message`）を使う。TARGET残り8行と非対象19行はnullである。96 rowはordinal順、case/seed一意、question 54はpublic fixture metadataから固定する。

### 2.1 physical再構築payload

```text
PhysicalFileRefV1 exact keys:
  role: str
  relative_name: safe relative str
  sha256: hex64
  size: strict int >= 0

SourceRoleRefV1 exact keys:
  role: closed 15-role literal
  source_id: nonempty str
  original_name: safe relative str
  sha256: hex64
  size: strict int >= 0

SourceRolesRootPayloadV1 exact keys:
  contract: "PHASE6_SAVED_CONTROL_SOURCE_ROLES_ROOT_V1"
  prepared_input, preflight, source_manifest,
  main_pin, old_t550_seal: PhysicalFileRefV1
  roles: tuple[SourceRoleRefV1,...]  # T573 §3 exact role順15

SavedControlMemberBindingV1 exact keys:
  ordinal: strict int 0..95
  row_id: nonempty str
  saved_rows_source_id: nonempty str
  original_name: safe relative str
  manifest_member_sha256: hex64
  old_seal_member_sha256: hex64
  embedded_member_sha256: hex64
  parsed_final_sha256: hex64 | null
  control_locator: NativeTextLocatorV1 | null

SavedControlMembersRootPayloadV1 exact keys:
  contract: "PHASE6_SAVED_CONTROL_MEMBERS_ROOT_V1"
  members: tuple[SavedControlMemberBindingV1,...]  # ordinal順96

ChosenIntentSourceBindingV1 exact keys:
  ordinal: strict int 0..95
  row_id: nonempty str
  saved_plan_original_name: safe relative str | null
  saved_plan_member_sha256: hex64 | null
  plan_pointer: "/plan" | null
  plan_value_sha256: hex64 | null
  reply_source_sha256, public_trigger_source_sha256,
  current_public_state_source_sha256: hex64 | null
  chosen_intent_observation_sha256: hex64 | null

ChosenIntentBindingsRootPayloadV1 exact keys:
  contract: "PHASE6_SAVED_CONTROL_CHOSEN_INTENT_ROOT_V1"
  bindings: tuple[ChosenIntentSourceBindingV1,...]  # ordinal順96

FixtureSourceBindingV1 exact keys:
  ordinal: strict int 0..95
  row_id, case_id: nonempty str
  seed: strict int
  fixture_index: strict int >= 0
  fixture_source_id, fixture_original_name: nonempty str
  fixture_member_sha256, fixture_binding_sha256: hex64
  population_status: closed population literal
  question_opportunity: bool

FixtureBindingsRootPayloadV1 exact keys:
  contract: "PHASE6_SAVED_CONTROL_FIXTURE_BINDINGS_ROOT_V1"
  bindings: tuple[FixtureSourceBindingV1,...]  # ordinal順96

SavedControlPopulationRootPayloadV1 exact keys:
  contract: "PHASE6_SAVED_CONTROL_POPULATION_ROOT_V1"
  rows: tuple[SavedControlRowBindingV1,...]  # ordinal順96
  rows_sha256: hex64
  source_roles_root_sha256, saved_control_members_root_sha256,
  chosen_intent_bindings_root_sha256,
  fixture_bindings_root_sha256: hex64
```

各rootはpayload全体のcanonical SHAで、payloadにself hashを含めない。`rows_sha256`は96 row列だけのcanonical SHA、population rootはその値と4 rootを再結合する。`seed`はstrict intでbool禁止。nullはphysical sourceに該当member/pointerが無い場合だけ許し、本文やChosenIntentの値から別fieldを推測しない。

Main専有custody APIは次である。

```text
reconstruct_saved_control_authority(
  owned_root, prepared_locator,
  *, expected_prepared_locator_file_sha256,
  expected_input_file_sha256, expected_preflight_file_sha256,
  expected_source_manifest_file_sha256, expected_main_pin_file_sha256,
  expected_old_t550_seal_file_sha256,
  expected_code_bundle_sha256
) -> (SavedControlAuthorityPinV1, SavedControlPopulationRootPayloadV1)
```

このAPIはpath安全性確認後にphysical bytesを読み、T567の`_run_bundle`、`validate_source_root`、`validate_identity_roles`、`reconstruct_expected_pin`を順に実行する。次にsource manifest/old sealからmemberを名前で一意解決し、prepared 96 rowをordinal順にnative saved row、fixture、saved plan/public sourceへjoinして上記4 payloadとpopulationを構築する。callerはrow、member ref、locator、各root actualを渡せない。Main external file SHAとcode bundleだけを入力し、worker builderは返されたpin/rootをexpectedとして自己発行できない。workerはMainが別経路で保存したpin SHA、5 root SHA、96 row SHAを受けてphysical sourceから再構築し全field一致を要求する。

### 2.2 code bundle

`CodeBundleEntryV1`はexact `{role,repository_path,sha256,size}`、`SavedControlCodeBundleV1`はexact `{contract:"PHASE6_SAVED_CONTROL_CODE_BUNDLE_V1",files,bundle_sha256}`である。filesは次の順に固定する。

1. `SAVED_CONTROL_HELPER` — `tests/fixtures/phase6_saved_control_plan_locked.py`
2. `MAIN_CUSTODY` — `tests/fixtures/phase6_saved_control_plan_locked_custody.py`
3. `T567_PROBE` — `scripts/phase6_resolved_subject_probe.py`
4. `T567_CUSTODY` — `scripts/phase6_resolved_subject_custody.py`
5. `T570_RUBRIC` — `tests/fixtures/phase6_plan_locked_rubric.py`
6. `T574_DESIGN` — `Docs/ai/design/PHASE6_SAVED_CONTROL_PLAN_LOCKED_DESIGN.md`
7. `T574_REVIEW` — `Docs/ai/handoffs/tasks/T574_SAVED_CONTROL_PLAN_LOCKED_DESIGN_REVIEW.md`

`bundle_sha256`は自身を除く全fieldのcanonical SHAで、各entryはcurrent physical bytesを開始時/freeze直前に照合する。新2 helperはAPPROVED後まで存在しないため現時点のcode bundle gateは未成立である。

## 3. blind packetの別mode

packetは各TARGET rowについて`CONTROL`と`NOT_MEASURED`を暗号学的random bitでA/Bへ独立配置する。private mappingだけがconditionを保持し、評価者へcondition、case、seed、model、改善方向を渡さない。CONTROL 69行は一側だけOBSERVED、欠測8行は両側NOT_OBSERVEDである。NOT_MEASUREDは常にobservation nullで、実artifactを指さない。

```text
SavedControlSourcePacketV1 exact keys:  # private custody only
  contract: "PHASE6_SAVED_CONTROL_SOURCE_PACKET_V1"
  mode: "SAVED_CONTROL_WITH_NOT_MEASURED"
  rubric_sha256, population_sha256, packet_binding_sha256: hex64
  rows: tuple[PlanLockedBlindRowV1,...]  # random blind順exact 96

SavedControlPrivateMappingRowV1 exact keys:
  blind_id: opaque str
  A_condition: "CONTROL" | "NOT_MEASURED"
  B_condition: "CONTROL" | "NOT_MEASURED"
  condition_permutation_sha256: hex64
```

各mapping rowはCONTROL/NOT_MEASUREDをexact一回含む。public row型、ChosenIntent、PublicMessageObservation、PublicTextBinding、question flagはT569 exact型を再利用する。NOT_MEASURED sideは`observation=null`であり、新statusを既存`PublicMessageObservationV1`へ追加しない。非対象19はchosen_intent null、両side null、既存population statusを保持する。

```text
SavedControlPrivateMappingV1 exact keys:
  contract: "PHASE6_SAVED_CONTROL_PRIVATE_MAPPING_V1"
  source_packet_sha256: hex64
  rows: tuple[SavedControlPrivateMappingRowV1,...]  # source packet blind順96
  mapping_sha256: hex64

validate_saved_control_mapping(source_packet, mapping,
  *, expected_source_packet_sha256, expected_mapping_sha256
) -> SavedControlPrivateMappingV1
```

validatorはsource packet digest、96 blind IDの同順・一意性、各row exact keys、A/Bが`CONTROL|NOT_MEASURED`をexact一回含むこと、`condition_permutation_sha256 = SHA({blind_id,A_condition,B_condition})`、self hash、external expected mapping SHAを検査する。T570のCONTROL/CANDIDATE mapping validatorやcondition enumをoverloadしない。packet freeze、annotation freeze、unblindの各入口で再実行する。

`SavedControlSourcePacketV1`とmapping/freezeはprivate custodyにだけ置く。評価者へ渡すのは§6のgeneric T570 `semantic_packet_view` bytesだけであり、public contract/modeにSAVED_CONTROL、CONTROL、NOT_MEASUREDを出さない。一側本文欠測から観測側を推測できるため完全なcondition blindとは主張せず、control-only校正としてだけ解釈し、pair比較を生成しない。

## 4. packet freeze

```text
SavedControlPacketFreezeV1 exact keys:
  contract: "PHASE6_SAVED_CONTROL_PACKET_FREEZE_V1"
  version: 1
  mode: "SAVED_CONTROL_WITH_NOT_MEASURED"
  source_manifest_sha256, authority_pin_sha256,
  population_sha256, mapping_sha256, rubric_sha256,
  source_packet_sha256, packet_binding_sha256,
  semantic_packet_view_sha256: hex64
  rows: 96
  target_rows: 77
  non_target_rows: 19
  control_observed_rows: 69
  control_missing_rows: 8
  not_measured_observed_rows: 0
  side_a_body_rows, side_b_body_rows: strict int
  both_body_rows: 0
  side_a_only_body_rows, side_b_only_body_rows: strict int
  neither_body_rows: 8
  question_opportunities: 54
  question_target_rows, question_plan_missing_rows,
  question_non_message_rows: strict int
  freeze_sha256: hex64
```

side A/B body countsは合計69、question三分類は合計54である。freezeはcreate-onlyで、自身を除く全fieldからself hashを作る。T570 V1の`t564_final_freeze_sha256`やT573 V2 native measurement rootを持たず、それらのvalidatorへ渡さない。

## 5. annotationと集計

評価者はT569の同じ5 metric、value/reason/path規則を使い、A/Bを独立判定する。observation null sideは全metricが`MEASUREMENT_NOT_OBSERVED/OBSERVATION_MISSING`、非対象はNOT_REQUIRED/metrics空である。公開文脈不足はUNKNOWNを優先し、本文regexやhost分類で補完しない。

```text
SavedControlAnnotationFreezeV1 exact keys:
  contract: "PHASE6_SAVED_CONTROL_ANNOTATION_FREEZE_V1"
  version: 1
  mode: "SAVED_CONTROL_WITH_NOT_MEASURED"
  source_packet_sha256, semantic_packet_view_sha256,
  rubric_sha256, packet_freeze_sha256,
  authority_pin_sha256, source_manifest_sha256,
  annotation_sha256, annotation_rows_sha256,
  evaluator_identity_sha256: hex64
  rows: 96
  annotated_rows: 77
  not_required_rows: 19
  side_a_metric_value_reason_counts: exact T570 closed count object
  side_b_metric_value_reason_counts: exact T570 closed count object
  both_body_rows: 0
  side_a_only_body_rows, side_b_only_body_rows: strict int
  neither_body_rows: 8
  question_opportunities: 54
  fresh_independent_evaluator: true
  freeze_sha256: hex64
```

unblind後の出力はcontrol conditionだけを集計する。各5 metricの`PASS|FAIL|UNKNOWN|MEASUREMENT_NOT_OBSERVED|NOT_APPLICABLE` count、control observed69/missing8、非対象19、question denominator54とそのPASS/FAIL/UNKNOWN/MNOを示す。NOT_MEASURED側はcoverage確認として全TARGET×5 metricがMNOであることだけを検査し、pair outcome、IMPROVED/REGRESSED、candidate countを生成しない。

fresh evaluatorが読むpacket bytesはcreate-only `semantic_packet_view`だけである。T570 `PlanLockedAnnotationEnvelopeV1.packet_sha256`はそのexact bytesのSHA、同envelopeの`packet_freeze_sha256`はprivate SavedControl freeze SHAを保持する。annotation freezeはprivate source packet SHA、semantic view SHA、annotation SHAを別fieldで一方向に結び、異なるpacket SHAを同一fieldとして扱わない。

## 6. pure APIと共有validator

新しい専有helperは次を提供する。

```text
build_saved_control_packet(owned_root, prepared_locator, authority_pin,
  *, expected_authority_pin_sha256, expected_source_roles_root_sha256,
  expected_saved_control_members_root_sha256,
  expected_chosen_intent_bindings_root_sha256,
  expected_fixture_bindings_root_sha256,
  expected_population_root_sha256, expected_code_bundle_sha256,
  expected_rubric_sha256, rng
) -> (source_packet, semantic_packet_view, private_mapping, packet_binding)

validate_saved_control_mapping(source_packet, mapping,
  *, expected_source_packet_sha256,
  expected_mapping_sha256) -> SavedControlPrivateMappingV1

freeze_saved_control_packet(source_packet, semantic_packet_view,
  mapping, packet_binding,
  *, expected_authority_pin_sha256, expected_source_manifest_sha256,
  expected_population_sha256, expected_mapping_sha256,
  expected_rubric_sha256, expected_source_packet_sha256,
  expected_packet_binding_sha256,
  expected_semantic_packet_view_sha256) -> SavedControlPacketFreezeV1

freeze_saved_control_annotation(annotation, source_packet,
  semantic_packet_view, mapping, packet_freeze,
  *, evaluator_identity, expected_evaluator_identity_sha256,
  expected_packet_freeze_sha256, expected_authority_pin_sha256,
  expected_source_manifest_sha256, expected_population_sha256,
  expected_mapping_sha256, expected_rubric_sha256,
  expected_source_packet_sha256, expected_packet_binding_sha256,
  expected_semantic_packet_view_sha256
) -> SavedControlAnnotationFreezeV1

unblind_saved_control(annotation, source_packet, semantic_packet_view,
  mapping, packet_freeze, annotation_freeze, *, evaluator_identity,
  expected_evaluator_identity_sha256,
  expected_annotation_freeze_sha256,
  expected_packet_freeze_sha256, expected_authority_pin_sha256,
  expected_source_manifest_sha256, expected_population_sha256,
  expected_mapping_sha256, expected_rubric_sha256,
  expected_source_packet_sha256, expected_packet_binding_sha256,
  expected_semantic_packet_view_sha256
) -> SavedControlSummaryV1
```

packet builderはphysical owned rootから§2.1をworker側でも再構築し、Main external pin/rootと一致後だけpacketを作る。T570 `validate_packet`用のexact `{contract:"PHASE6_PLAN_LOCKED_RUBRIC_V1", rubric_sha256, population_sha256, packet_binding_sha256, rows}` をcreate-only `semantic_packet_view`として保存する。全fieldはprivate source packetからlosslessに写し、rowsはdeep-equalである。Mainが別経路で固定した`expected_semantic_packet_view_sha256`をT570 validatorの`expected_packet_sha256`へ渡す。T564/T568 fieldや本文を補わない。

評価者とT570 `validate_annotation`は同じfrozen semantic view bytesを受ける。exact呼出は `validate_annotation(annotation, semantic_packet_view, saved_control_packet_freeze, ...)` であり、annotation envelopeのpacket SHAはsemantic view digest、freeze SHAはSavedControl freeze digestとなる。private source packet digestはSavedControl freezeとannotation freezeの専用fieldだけに保持する。これにより評価者が見たrow/rubric/freezeとannotation SHAを一意に結合する。5 metric意味表、value/reason/path、annotation row検査を複製しない。T570 frozen fileの変更が必要なら実装を止め、別amendment reviewを要求する。

各freeze APIはauthority/source/code/packet/binding/semantic viewのexternal rootsを最初に再検証し、続けて`validate_saved_control_mapping`をexternal mapping SHA付きで呼ぶ。annotation APIは共有annotation validator、fresh evaluator identity、create-only annotation freezeの順に処理する。unblindは同じfreeze builderでobject全体とexternal expected annotation freeze SHAを照合してからprivate mappingを一回だけ適用する。

## 7. 正負testとgate

positiveは96/77/19、control69/8、question54、合法NONE、公開context不足UNKNOWN、NOT_MEASURED全MNO、非対象NOT_REQUIRED、A/B permutationの両向きを含むphysical synthetic fixtureをMain custody→packet→annotation freeze→unblindまで通す。

negativeはsource role/file SHA/old seal/member/locator/pointer/row ordinal/case/seed/fixture/ChosenIntent binding、69/8、54、mapping permutation、cross-row、blind order、packet/freeze/annotation/identity SHAを一件ずつtamperし、self hash更新後もexternal expected値で拒否する。physical member変更とcaller row/root再計算の同時tamperもMain external file/root pinで拒否する。private source packetまたはsemantic viewの片方だけを自己整合変更する、annotation packet SHAをsource packet SHAへ置換する、mapping順/condition/permutationを自己整合変更する、code bundle fileを変更する負例を含める。NOT_MEASUREDへ本文/status/bindingを置く、condition/modeをpublicへ出す、V1/T573 V2 validatorへ渡す、仮T564/T568 freezeを補完する、旧annotationを読む、MNOをUNKNOWN/PASSへ変える、pair outcomeを生成する例を拒否する。

独立design APPROVED後に新専有helper/testを実装し、Python 3.10/3.13 focused、関連T570回帰、`check_docs.py`、独立tool reviewを通す。その後create-only source packet/freeze、対象実装と独立なfresh evaluator、create-only annotation freeze、Main unblindの順にgateする。provider、T568 measurement、T566 verifier、旧評価読込みは実行しない。
