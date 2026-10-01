# Phase 6 保存行 projected S4 接続詳細設計

Status: APPROVED
Approved-Content-SHA-256: 5f7d9561e4f0991d5d1f5492874fb3098382a76f576680f9d3a795f8e83dc6b8
Approved-By: Independent Design Reviewer
Task: T556
Contract: `S4_PROJECTED_ABILITY_SAVED_BINDING_V1`

## 1. 目的と固定入力

D099で許可された保存済みT553の192行（96 pair、accepted 179、未観測13）へ、承認済み `S4_PROJECTED_ABILITY_PROVENANCE_V1` をversionごとに一回接続する。旧T553 packet `125864ed…`、freeze `22b9b8…`、mechanical `b3da6132…`、mapping `4bf300…` はMainがfull digestをpreflightで固定する。ここでは短縮値をexpected値として使わず、SAFE metadataの64桁digestと実bytes一致を要求する。限定修正・再適用が必要なら理由とchanged bytesを記録した新version/containerを作り、旧versionを保持する。

旧annotation、score、意味判定は読まない。旧packet/binding/mechanical/mapping/sourceを変更・再hash・置換せず、新containerへ新version artifactを作る。新provider、生成、game、本文解析は行わない。

## 2. 一回の入力pinとsource witness

Mainは変換前に `SavedBindingPreflightV1` を別系統で固定する。

```text
SavedBindingPreflightV1 exact keys:
  contract: "S4_PROJECTED_ABILITY_SAVED_BINDING_V1"
  permission_decision_sha256, base_design_sha256,
  amendment_sha256, projected_helper_sha256,
  connector_source_sha256, probe_source_sha256,
  old_packet_sha256, old_freeze_sha256,
  old_mechanical_sha256, old_mapping_sha256,
  source_manifest_sha256: Digest
  rows: 192
  pairs: 96
  accepted_rows: 179
  unobserved_rows: 13
  preflight_sha256: Digest
```

`source_manifest` は使用する全fileをrepository/private-container相対path、全binary bytes SHA、sizeで列挙し、path昇順のcanonical wireをhashする。対象は旧4artifact、T506 baselineの保存accepted-final files、T550 candidateのsealed outcome/response/fixture inputs、`scripts/phase6_quality_probe_v2.py`、新connector source、T555 helperである。後述V1/V2 extractorとstrict parserはconnector source内だけに実装し、外部product parser/schema validatorをimportしない。このsource全binary bytes SHAをpreflightで開始時/終了時に照合する。旧annotation/scoreはmanifestへ含めず開かない。connector source bytesもprivate source snapshotへコピーする。

`preflight_sha256` は自身を除くpreflight exact payloadのhash。connectorは入力から作り直さず、Mainがtask/scope/全digestを確認して固定した値を引数で受け、開始時と終了時に再照合する。hashは認証ではなく、custodian検証とMainのscope/hash pinがtrust rootである。

行ごとに `SavedSourceWitnessV1` を作る。

```text
SavedSourceWitnessV1 exact keys:
  evaluation_id: cryptographic opaque str
  old_blind_id: str                 # custodian-private only
  old_row_binding_sha256, old_mechanical_row_sha256: Digest
  condition_key_sha256, pair_key_sha256: Digest  # custodian-private only
  fixture_source_sha256, fixture_bindings_sha256,
  fixture_catalog_sha256, accepted_final_sha256: Digest|null
  accepted_final_locator: {
    container_seal_sha256: Digest
    snapshot_file_sha256: Digest
    member_object_sha256: Digest
    member_name: str
    json_pointer: "/final_content"|"/choices/0/message/content"
    encoding: "JSON_STRING_UTF8"
  }|null
  execution_status: "ACCEPTED"|"MEASUREMENT_NOT_OBSERVED"
```

baselineはsource manifestに固定された `{case_id}.final.json` wrapperをduplicate-key/nonfinite拒否でparseし、outer exact keysを `final_content, final_output_sha256` に限定する。`final_content` はUTF-8可能なstr、`final_output_sha256 == SHA(final_content.encode("utf-8"))` を要求する。innerもduplicate-key/nonfinite/UTF-8不能を拒否するstrict parserで読む。旧acceptanceを再判定せず、connector内のclosed extractorだけを使う。

```text
baseline V1 extractor:
  parsed.decision: dict|null、parsed.discussion: dict|null
  message = decision.message (null または非空str)
  co = discussion.co_judgment (dict|null)
  co.decision == "DECLARE" の時だけ selected_option_id と
    claimed_role_id を非空strとして要求し、claimを
    {decision:"DECLARE", option:selected_option_id,
     claimed_role:claimed_role_id} へkey renameする
  それ以外はclaimなし
  surface = {kind, text:message, comment:null, claims:[claim]または[]}
  kind = TEXT_AND_STRUCTURED|TEXT|STRUCTURED|ABSENT（存在組合せ）
```

missing/non-dict container、空message、DECLARE必須field欠測はconnector UNKNOWN。追加fieldは読まずauthorityや意味を作らない。抽出surfaceは保存T553 public surfaceの `decision/option/claimed_role` 表示とexact一致しなければならない。baseline側に同名の保存 `row.final` があると仮定せず、inner値自身との再parse一致を採用証拠にしない。Mainが固定する96 wrapperのwhole-file SHAと `final_output_sha256` を全件照合し、accepted 93件だけを接続する。3不受理は旧executionのMNOを維持する。`accepted_final_sha256` はwrapper bytesでなく `SHA(final_content.encode("utf-8"))`、locatorはwrapper whole-file hashと `/final_content` member hashを別fieldで持つ。

candidate accepted行はsealed `actual/{case_id}-{seed}-{terminal_stage}-{ordinal}-outcome.json` がstatus=ACCEPTEDかつordinal 1または2で一意であり、同prefixのsealed `-response.json` と対応することを要求する。container sealがoutcome/response両fileのwhole-file SHAを列挙し、outcomeのcase/seed/terminal_stage/ordinalと保存rowが一致しなければならない。response outer JSONと `/choices/0/message/content` のinner JSONをいずれもduplicate-key/nonfinite拒否のstrict parserで読む。contentはUTF-8可能なstr、inner値は保存row.finalと型・全key・全値がexact一致する。旧acceptanceを再判定せず、connector内のclosed extractorを使う。

```text
candidate V2 extractor（上から最初の一致だけ）:
  final.message keyあり: messageは非空str、
    {kind:"TEXT", text:message, comment:null, claims:[]}
  final.decision == "DECLARE": co_option_id と claimed_role_option_id は非空str、
    commentはnullまたは非空str、claimは
    {decision:"DECLARE", option:co_option_id,
     claimed_role:claimed_role_option_id}、
    kindはcommentありTEXT_AND_STRUCTURED、なしSTRUCTURED、text:null
  上記以外: {kind:"ABSENT", text:null, comment:null, claims:[]}
```

写像結果を保存T553 surfaceへexact一致させる。型違い・曖昧shapeはconnector UNKNOWN。`accepted_final_sha256 = SHA(content.encode("utf-8"))` とし、raw textをJSON再serializeしない。locatorはresponse snapshot file whole hashと、pointerで得たmember object bytes `content.encode("utf-8")` のhashを別fieldで持つ。

旧mappingの `source_final_sha256` はcase/seed/plan/final objectのcanonical hashなので流用しない。実memberが無い、ordinal/responseが0件または複数、seal不一致、UTF-8不能ならhash/locatorをnullとし、そのaccepted行は `SAVED_ACCEPTED_FINAL_UNAVAILABLE / UNKNOWN / INVALID` に閉じてprojected row/decision helperを呼ばない。ここでのfile/member/JSON構造parseはlossless byte接続であり許可する。「本文解析禁止」は自然言語の意味・ref・意図をhostが推測することを指す。未観測13行もlocator/hashともnullである。

fixtureは保存case IDを既存fixed suiteの一件へ機械対応させ、保存source/bindings/catalogとexact一致後に `q.verify_bindings` を通す。legacy owner abilityはこのfixtureのsource/pointer/actor/public channel/authorityだけから `freeze_projected_ability_trust` へ渡す。PUBLIC_FACT、CLAIMED_REPORT、SOURCE_REFをPROJECTED_ABILITYへ変換しない。

## 3. 旧base保持と新row構築

旧mechanical rowから次をbyte-equivalentな値としてdeep copyする。

- 旧 `RowBindingV1` 全field
- 旧base mechanical record列の全fieldと順序
- execution/audit/conversion
- 旧surface viewとaccepted plan view

旧hashは再計算して正規化せず、値を変えない。保存mechanicalのaccepted row exact shapeと、全recordのbindingが同じ保存RowBindingへ一致することだけを検査する。不一致は旧値を直さず `SAVED_BASE_BINDING_MISMATCH / UNKNOWN`。旧helperでbase row/hashを再生成しない。

accepted行は保存accepted planの全selected IDをcatalogへstrict検査する。ABILITY_RESULT bindingだけを順序保持filterし、fixture custodian freezeから `ProjectedAbilityRowV1`、selection、witnessをT554 exact payload順で作る。preflightに固定したrow別freeze digestを `expected_freeze_sha256` として使い、row自身からexpected値を得ない。accepted-final hashは§2 witnessと一致させる。

旧baseの `SELECTED_DISCLOSURE` recordと新PROJECTED recordは同じ既存opaque provenance IDで `merge_common_provenance_v2` に渡す。IDを本文、prefix、順番から対応させず、旧selection envelopeのbinding ID/selected IDと保存plan selectionのexplicit一致だけを使う。非ability recordは旧base exact値・位置を保つ。選択なしはprojected列0件を合法とする。

未観測13行はprojected rowを作らず、`execution != COMPLETE/ACCEPTED`、row/root/records/semantic=nullでdecisionを呼び、MNO/null bindingを得る。FAIL/PASSや空recordで補完しない。

### 3.1 row rootの別系統pin

custodianがsource/fixture/finalを検証してrow freeze候補を作った後、blind packet作成より前に停止する。候補artifactは次のexact型である。

```text
RowRootCandidateManifestV1 exact keys:
  contract: "S4_PROJECTED_ABILITY_SAVED_BINDING_V1"
  preflight_sha256: Digest
  rows: tuple[{
    evaluation_id: str
    old_row_binding_sha256: Digest
    source_witness_sha256: Digest
    projected_row_sha256: Digest|null
    custodian_freeze_sha256: Digest|null
    status: "READY"|"MEASUREMENT_NOT_OBSERVED"|
            "SAVED_ACCEPTED_FINAL_UNAVAILABLE"|"INPUT_INVALID"
  }, ...]                         # private conversion order、192件
  manifest_sha256: Digest
```

`manifest_sha256` は自身を除くpayload hash。READYだけprojected row/rootが非null、未観測と欠測は両方null。evaluation ID、old binding、witnessは一意である。custodianはこの候補とsource snapshotをcreate-only保存して終了し、自身ではexpected rootを承認しない。

Mainは別process/stepでpreflight、source manifest、candidate manifest、192件/96pair、各READY freeze hashを照合し、次を別private pin artifactとして固定する。

```text
MainPinnedRowRootsV1 exact keys:
  contract: "S4_PROJECTED_ABILITY_SAVED_BINDING_V1"
  task: "T556"
  preflight_sha256, candidate_manifest_sha256: Digest
  rows: tuple[{evaluation_id:str,
               expected_freeze_sha256:Digest}, ...] # READYのみ、candidate順
  ready_count, unavailable_count, unobserved_count: int>=0
  pin_sha256: Digest
```

`pin_sha256` は自身を除くpayload hash。Mainはcandidate内のfreeze digestを入力bundleから再計算して採用せず、source snapshotとT555 helperを独立照合した結果だけをpinする。packet sealing/evaluation/finalize APIは `main_pin` と呼出側に別保存された `expected_pin_sha256` を必須入力とし、両者のexact一致、preflight/candidate digest、READY evaluation ID集合・順序を検査する。rowへ渡す `expected_freeze_sha256` はこのpinの対応entryだけから得る。input row/freezeからexpected値を生成しない。

## 4. fresh blind packetと意味評価

192個の新 `evaluation_id` をcryptographic randomで一回生成し、全行をSystemRandomでshuffleする。同pair隣接0、ID一意、192行/96pairをfreeze前に検査する。condition、profile、seed、model、run順、旧blind ID、旧score、source path、plan内部値をEvaluator packetへ出さない。対応はcustodian-private mappingだけに置く。

Reviewer向け `ProjectedS4BlindRowV1` は次だけを持つ。

```text
evaluation_id
evaluation_status: "READY"|"CONNECTOR_SKIPPED"|"MEASUREMENT_NOT_OBSERVED"
execution: {run_status, structural_status}
audit: {sealed_audit, public_surface}
conversion: {status}
accepted_surface
provenance: ordered tuple of {
  opaque_provenance_id, lane, ref_resolution, selection,
  actor_match, visibility, authority, canonical_value, binding_status,
  identity_kind:"SOURCE_REF"|"PROJECTED_ABILITY"|null
}
sealed_selected_values: ordered tuple of {
  opaque_provenance_id, target, result
}
```

SOURCE_REF/PUBLIC_FACT/CLAIMED_REPORTは旧base exact表示、legacy selected abilityだけ新PROJECTED表示に置換する。source pointer、owner ID、raw role/team、conditionはReviewerへ渡さない。公開surface本文は保存値をそのまま渡し、hostが意味/ref/意図を抽出しない。

fresh evaluator rubricはS4だけを扱う。accepted rowごとに `assertion=NONE|CLAIMED_RESULT|EXPLICIT_AUTHORITY_ASSERTION|UNDECIDABLE`、cited provenance ID列、各AUTHORITATIVE主張の `target/result` tagged atom associationを返す。自由文の戦略品質、真偽、他metricを採点しない。`EXPLICIT_AUTHORITY_ASSERTION` は既存S4の「能力結果の権威として明示」の意味に限定する。判断不能はUNDECIDABLE/associationなしであり、推測で埋めない。

`BlindAnnotationV1` exact keys:

```text
evaluation_id
status: "ANNOTATED"|"CONNECTOR_SKIPPED"|"NOT_REQUIRED"
assertion: enum|null
cited_provenance_ids: tuple[str,...]
associations: tuple[{opaque_provenance_id,target:SemanticAtomV1,
                     result:SemanticAtomV1},...]
```

Main pinでREADYのaccepted行だけANNOTATEDとし、assertion/refs/associationsを要求する。元未観測13行はNOT_REQUIRED/null/空列。acceptedだがrow-root statusが `SAVED_ACCEPTED_FINAL_UNAVAILABLE` または `INPUT_INVALID` の行はCONNECTOR_SKIPPED/null/空列で、Evaluatorへ意味判断を要求せず `SavedS4RowResultV1.CONNECTOR_UNKNOWN` へ直結する。packet内のevaluation_statusとannotation statusは一対一対応し、全192 IDがexact一回、余分・欠落なしになるまでfreezeしない。一人のfresh independent evaluatorがREADY rowだけを意味評価し、packet/rubric/freezeだけを受け、mapping、old annotation、mechanical private artifactを受けない。

## 5. annotation freezeからdecisionへ

annotation freeze後、custodianはprivate mappingでevaluation IDを内部old blind IDへ戻し、ANNOTATED内容だけを `ProjectedSemanticObservationV1.base` へ写す。blind IDとrow/surface/final/witness/freeze hashは保存rowから機械付与し、Reviewerに生成させない。annotationの意味fieldは変更しない。

各accepted行で次を一回実行する。

```python
projected = project_selected_abilities(row=row,
    expected_freeze_sha256=preflight_pinned_row_root)
merged = merge_common_provenance_v2(
    base_records=old_base_records, projected_records=projected)
decision = decide_projected_ability_s4(
    execution=execution, audit=audit, conversion=conversion,
    row=row, expected_freeze_sha256=preflight_pinned_row_root,
    merged_records=merged, semantic=semantic)
```

旧priority、UNKNOWN先、全AUTHORITATIVE量化を変更しない。T555 decision reason enumへconnector reasonを追加しない。全192行は次の外側exact型を一件ずつ持つ。

```text
SavedS4RowResultV1 exact keys:
  evaluation_id: str
  status: "DECIDED"|"MEASUREMENT_NOT_OBSERVED"|"CONNECTOR_UNKNOWN"
  connector_reason: "NONE"|"SAVED_ACCEPTED_FINAL_UNAVAILABLE"|
                    "SAVED_BASE_BINDING_MISMATCH"|"CONNECTOR_INPUT_INTEGRITY"
  projected_decision: ProjectedS4DecisionV1|null
  metric_value: "PASS"|"FAIL"|"UNKNOWN"|"MEASUREMENT_NOT_OBSERVED"
  measurement_validity: "VALID"|"INVALID"
  binding: ProjectedRowBindingV1|null
  offending_provenance_ids: tuple[str,...]
```

`DECIDED` はconnector_reason=NONE、decision非nullで、metric/validity/binding/offending IDsをdecisionからexact mirrorする。`MEASUREMENT_NOT_OBSERVED` もreason=NONE、T555 MNO decision非null、metric=MNO/INVALID/binding=null/IDs空。`CONNECTOR_UNKNOWN` だけdecision=null、metric=UNKNOWN/INVALID/binding=null/IDs空で、connector reasonはNONE以外exact一つ。connector例外でrun全体を中断せずsafe enumをwrapperへ記録する。ただしsource/root/permission/row数のglobal preflight failureはartifactをCOMPLETEにせず全体停止する。

## 6. artifact、集計、不可逆性

新private containerへcreate-onlyで次を保存する。

1. connector source snapshot
2. source manifest
3. preflight/root
4. row source witnesses
5. custodian projected rows/freezes/merged mechanical
6. fresh blind packetとhuman rubric
7. evaluator locator（packet/rubric/freezeだけ）
8. frozen annotation
9. `SavedS4RowResultV1` 192件（内側decisionは実行可能行だけ）
10. secret mapping
11. final freeze

final freezeは各artifact SHA、contract/helper/design/amendment/review SHA、preflight/candidate manifest/Main pin SHA、rows=192、pairs=96、source_accepted=179、annotation_count=ready_count、connector_skipped_count=unavailable_count、not_required_count=13、かつ三count合計192、provider_calls=0、old_annotation_read=false、old_artifacts_modified=falseを含む。countはMain pinから動的に得てannotation status別countと一致させる。作成後追記しない。再適用が必要なら原因とchanged source SHAを記録した新container/versionを作り、旧containerを変更しない。

safe outputはlocator path、condition mapping、本文、annotationを含めず、artifact SHA、status、行/pair/count、metric対象集合、PASS/FAIL/UNKNOWN/MNOとreason別count、original hash checks、provider_callsだけを出す。集計はwrapperの `metric_value` を唯一の分類値とし、connector UNKNOWNもUNKNOWNへ含める。connector reasonと内側decision reasonは別counterにする。条件別集計はannotation freeze後にprivate mappingで行い、各条件96、pair96、全体192の分母を固定する。UNKNOWN/MNOを除外した率だけを主指標にせず、全分母countを必ず併記する。他metric・旧scoreは変更しない。

## 7. 公開syntheticと有限コマンドgate

実装前に独立design APPROVEDを要求する。connectorはtest-only一file、公開synthetic fixture/test一組を推奨する。正例はSOURCE_REF/public fact/claim位置保持、owner ability projection、selectionなし、mixed authority、未観測を含む。負例はold hash差、wrong source/pointer/owner/channel/authority、accepted-final差、expected root自己生成、cross-row ID、duplicate/missing/extra annotation、condition漏洩、mixed mismatch+unknownを含む。

実行接点は一つの有限CLIとする。

```text
python -X utf8 <connector> prepare-roots --preflight <private-pin> --output <new-private-container>
python -X utf8 <connector> seal-packet --preflight <same-pin> --main-row-pin <pin> --expected-pin-sha256 <digest> --output <same-container>
python -X utf8 <connector> finalize --preflight <same-pin> --main-row-pin <same-pin-file> --expected-pin-sha256 <same-digest> --annotation <frozen-file> --output <same-container>
```

`prepare-roots` は既存artifact照合、新container作成、192行source witness/projected row候補とcandidate manifestを保存して終了する。Mainの別系統pin後、`seal-packet` が同じpreflight/candidate/rootを照合してpacket/rubric/evaluator locator、annotation未凍結freezeを作る。`finalize` は同じpin/containerを再照合し、annotationを一回だけfreezeして192 decisions/safe outputを作る。各subcommandはrow上限192、再帰探索なし、provider/network/process起動なし。同phase二回目、別root、既存file上書きを拒否する。

Python 3.10/3.13 focused、旧T555回帰、公開synthetic end-to-end、docs/diff、独立tool review後にのみreal prepareへ進む。prepare freeze後にfresh evaluator一回、finalize後にhash/countを独立測定する。
