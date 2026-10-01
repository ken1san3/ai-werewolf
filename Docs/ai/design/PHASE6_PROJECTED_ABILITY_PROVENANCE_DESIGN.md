# Phase 6 projected ability provenance 詳細設計

Status: APPROVED
Approved-By: Independent Design Reviewer
Approved-Content-SHA-256: 5469723e57478e69412b9d9b23351ac77e010a4280d73737be83483d96caa4b0
Approval-Scope: test-only S4_PROJECTED_ABILITY_PROVENANCE_V1 design
Task: T554
Contract: `S4_PROJECTED_ABILITY_PROVENANCE_V1`

## 1. 目的と非変更点

EvidenceRefを持たない既存context上のowner ability resultへ `PROJECTED_ABILITY` identityを追加し、将来のtest-only S4評価でlosslessに識別する。旧 `S4_COMMON_PROVENANCE_V1` の型、literal、投影、判定、T553 annotationは変更・変換・再計算しない。本契約を使う測定は別versionとする。

新しい権威は作らない。既存custodianが `q.build_fixture` と `verify_bindings` で確認したsource、owner、pointer、channel、authority、catalog selectionだけを一回freezeする。本文、order、alias、opaque ID、result IDの意味からhostがref・owner・権威を推測しない。真正性は既存custodian gateの責任であり、schemaやhashだけでは認証しない。

## 2. identityとcanonical wire

```text
ProvenanceIdentityV1 =
  {kind:"SOURCE_REF", ref:{record_kind:str, order:int>=0,
                           visibility:"PUBLIC"|"NON_PUBLIC"}}
| {kind:"PROJECTED_ABILITY", source_sha256:Digest,
   pointer:CanonicalPointer, owner_id:str,
   raw_value_sha256:Digest, projected_value_sha256:Digest}
```

`SOURCE_REF` は既存EvidenceRefだけに使う。`PROJECTED_ABILITY` はEvidenceRefではなく、一つのimmutable source projection内の値identityである。相互変換やorderからの補完は禁止する。PROJECTED_ABILITYをsurfaceのexplicit refとして表示せず、opaque provenance IDとsealed最小valueだけをReviewerへ渡す。

canonical wire/SHA-256はT552と同じUTF-8、key昇順、compact separatorである。重複key、float、nonfinite、boolをintとして受理することを禁止する。全dictはexact keys、全enumはclosed。`Digest` はlowercase 64 hex。`CanonicalPointer` はRFC 6901 absolute JSON Pointerで、空/root、末尾slash、非canonical `~` escape、配列の先頭0、`-` を拒否する。

## 3. source値とfreeze trust root

現在のlegacy owner ability pointerが指すraw値は次のexact型だけを許す。

```text
RawOwnerAbilityValueV1 == ProjectedAbilityValueV1 (exact keys):
  target_player_id: str|null
  result_id: str|null
  revealed_role_id: str|null
  event_type: str|null
  day: int|null
  phase: str|null
  order: int|null
```

7 keyは全て必須で、追加key、型違い、欠落を拒否する。projectionは各key/valueを同名でコピーし、rename、default、coerce、補完をしない。`raw_value_sha256 = SHA(canonical_wire(raw pointer value))`、`projected_value_sha256 = SHA(canonical_wire(projected value))` と別々に計算し、両hashと7値のexact一致を検査する。現在shapeでは両hashが同値でも、片方の検査で代用しない。不適合は `PROJECTED_SOURCE_VALUE_UNAVAILABLE` のUNKNOWNである。

一回の測定準備で既存custodianだけが次を作る。

```text
CustodianAbilityBindingV1 (exact keys):
  binding_id, disclose_id: str
  source_kind: "ABILITY_RESULT"
  source_sha256, raw_value_sha256, projected_value_sha256: Digest
  pointer: CanonicalPointer
  owner_id, channel_id: str
  channel_visibility: "PUBLIC"
  authority: "INTENTIONAL_OWNER_ABILITY"
  raw_value: RawOwnerAbilityValueV1
  projected_value: ProjectedAbilityValueV1

ProjectedAbilityCustodianFreezeV1 (exact keys):
  contract: "S4_PROJECTED_ABILITY_PROVENANCE_V1"
  fixture_builder_source_sha256, binding_verifier_source_sha256: Digest
  source_sha256, source_projection_sha256: Digest
  owner_pointer: "/context/player_id"
  owner_id: str
  owner_value_sha256: Digest
  public_channel_id: str
  catalog_disclose_ids: tuple[str,...]
  legacy_owner_ability_pointers: tuple[CanonicalPointer,...]
  bindings: tuple[CustodianAbilityBindingV1,...]
  freeze_sha256: Digest
```

`catalog_disclose_ids` とpointer列はsource上の順序を保ち、各列内ID/pointerは一意、bindingsのbinding/disclose/pointerも各々一意とする。binding列はcatalog disclose順で、その部分列はlegacy pointer集合と一対一である。0件は合法だがPROJECTED_ABILITYを作れない。重複、競合、欠測はfreeze作成を拒否する。

`source_sha256` と `source_projection_sha256` は既存fixtureの `source` exact objectのcanonical wire SHAで、現在の `q.build_fixture` が同一projectionへ保存する二fieldを別名のまま各々検査する。`owner_value_sha256` はpointer `/context/player_id` が解決した非空strのcanonical wire SHAである。各bindingのraw/projected hashは§3冒頭の定義による。freeze hash payloadは `ProjectedAbilityCustodianFreezeV1` から `freeze_sha256` だけを除いたexact keysで、bindingsと各tupleの順序を保持する。

```python
freeze_projected_ability_trust(*, fixture,
    fixture_builder_source_sha256: str,
    binding_verifier_source_sha256: str
) -> ProjectedAbilityCustodianFreezeV1
```

このcustodian APIは既存 `q.build_fixture` の出力へ既存 `verify_bindings` を成功させ、source digest/projection digest、`/context/player_id`、public channel、legacy owner-ability pointer集合、各bindingのsource_kind/actor/authority/value hashを照合し、raw pointer値を上記規則で投影してfreezeする。別sourceを探索しない。`freeze_sha256` は自身を除く全exact fieldのcanonical wire hashである。helperへsource bytesを渡す新captureを設けない。

二つのcode SHAの対象は、repository-relative POSIX path `scripts/phase6_quality_probe_v2.py` のファイル全binary bytesである。BOM除去、改行変換、関数source抽出、AST整形を行わない。現在 `q.build_fixture` と `verify_bindings` は同じfileにあるため二SHAは同値を要求する。custodianはfreeze前にそのpath、bytes、SHAを既存private evidence containerのimmutable source snapshotへ一回保存し、freezeは同snapshot digestを参照する。path不一致、bytes未保存、関数がその固定fileに存在しない場合は `PROJECTED_TRUST_ROOT_UNAVAILABLE` であり別fileを探索しない。

task preflightはcustodianとは別系統に、対象task/scopeと二つのhelper source SHAを確認して `expected_freeze_sha256` をimmutable bundle locatorへ固定する。評価helperはfreezeと、そのlocatorから渡すexpected digestをexact一致させる。入力freezeからexpected digestを再計算して承認扱いせず、callerが作った自己整合bundleだけではtrustを得ない。hashは同一bytesへのbindingであり認証ではない。既存custodianとMainのscope/hash確認がtrust rootである。必要fieldが保存されなければUNKNOWNとし、caller自己申告や別sourceで補完しない。

## 4. 新version exact row型

旧T552 `RowBindingV1.rubric_version="S4_COMMON_PROVENANCE_V1"` はliteralを含め不変で、新型へ変換・代入しない。

```text
ProjectedRowBindingV1 (exact keys):
  rubric_version: "S4_PROJECTED_ABILITY_PROVENANCE_V1"
  blind_id: str
  base_row_sha256, base_selection_envelope_sha256,
  base_catalog_sha256, base_canonical_set_sha256: Digest
  row_sha256, surface_sha256, accepted_plan_sha256,
  accepted_final_sha256, catalog_sha256,
  canonical_binding_set_sha256, custodian_freeze_sha256: Digest

ProjectedSelectionV1 (exact keys):
  blind_id, disclose_id, binding_id, opaque_provenance_id: str
  row_sha256, surface_sha256, accepted_plan_sha256,
  accepted_final_sha256, selection_envelope_sha256,
  custodian_freeze_sha256: Digest
  source_identity: ProvenanceIdentityV1(kind=PROJECTED_ABILITY)

TrustedRowSourceWitnessV1 (exact keys):
  contract: "S4_PROJECTED_ABILITY_PROVENANCE_V1"
  blind_id, owner_id, public_channel_id: str
  row_sha256, surface_sha256, accepted_plan_sha256,
  accepted_final_sha256, source_sha256, catalog_sha256,
  canonical_binding_set_sha256, custodian_freeze_sha256: Digest
  owner_pointer: "/context/player_id"
  owner_value_sha256, witness_sha256: Digest

ProjectedAbilityRowV1 (exact keys):
  binding: ProjectedRowBindingV1
  accepted_plan: AcceptedPlanViewV1
  accepted_surface: AcceptedSurfaceViewV1
  witness: TrustedRowSourceWitnessV1
  selections: tuple[ProjectedSelectionV1,...]
  custodian_freeze: ProjectedAbilityCustodianFreezeV1

ProjectedMechanicalRecordV1 (exact keys):
  contract: "S4_PROJECTED_ABILITY_PROVENANCE_V1"
  blind_id, opaque_provenance_id, binding_id, disclose_id: str
  origin: "SELECTED_DISCLOSURE"
  lane: "AUTHORITATIVE_ABILITY"
  selection: "SELECTED"
  identity_kind: "PROJECTED_ABILITY"
  source_resolution: "RESOLVED"
  actor_id, owner_id, channel_id: str
  visibility: "PUBLIC"
  authority: "INTENTIONAL_OWNER_ABILITY"
  canonical_value: ProjectedAbilityValueV1
  source_identity: ProvenanceIdentityV1(kind=PROJECTED_ABILITY)
  row_sha256, surface_sha256, accepted_plan_sha256,
  accepted_final_sha256, selection_envelope_sha256,
  custodian_freeze_sha256: Digest

ProjectedSemanticObservationV1 (exact keys):
  contract: "S4_PROJECTED_ABILITY_PROVENANCE_V1"
  base: SemanticObservationV1
  blind_id: str
  row_sha256, surface_sha256, accepted_final_sha256,
  witness_sha256, custodian_freeze_sha256: Digest

ProjectedS4DecisionV1 (exact keys):
  binding: ProjectedRowBindingV1|null
  metric_value: "PASS"|"FAIL"|"UNKNOWN"|"NOT_APPLICABLE"|
                "MEASUREMENT_NOT_OBSERVED"
  reason_code: closed reason in this section or unchanged T552 reason
  offending_provenance_ids: tuple[str,...]
  measurement_validity: "VALID"|"INVALID"
```

`ProjectedSemanticObservationV1.base.binding` は旧 `RowBindingV1` exact型のままで、rubric literalは `S4_COMMON_PROVENANCE_V1`。新bindingとの対応は `blind_id`、`surface_sha256`、旧 `row_sha256 = base_row_sha256`、旧 `selection_envelope_sha256 = base_selection_envelope_sha256`、旧 `catalog_sha256 = base_catalog_sha256`、旧 `canonical_set_sha256 = base_canonical_set_sha256` の全一致である。baseを新型へ変換せず、一項目でも違えば `PROJECTED_ROW_BINDING_MISMATCH`。

各hash payloadを次で固定する。

```text
ProjectedCatalogPayloadV1 exact keys:
  contract, base_catalog_sha256, catalog_disclose_ids

ProjectedBindingSetPayloadV1 exact keys:
  contract, source_sha256, bindings

ProjectedRowPayloadV1 exact keys:
  contract, blind_id, base_row_sha256, surface_sha256,
  accepted_plan_sha256, accepted_final_sha256, catalog_sha256,
  canonical_binding_set_sha256, custodian_freeze_sha256
```

`catalog_sha256` はcatalog順を保つ `catalog_disclose_ids`（重複なし）のpayload hash。`canonical_binding_set_sha256` はcatalog disclose順に並べた `CustodianAbilityBindingV1` exact dict列のpayload hashで、辞書順への並べ替えは禁止する。`row_sha256` はProjectedRowPayload hashである。`surface_sha256` はaccepted surface canonical wire、`accepted_plan_sha256` はaccepted plan canonical wire、`accepted_final_sha256` は保存accepted-final raw bytesのSHA-256である。base四hashは旧T552 artifactからそのまま受け、再計算・置換しない。

envelope hash用には完成selectionを直接入れず、次の自己digest除外型を使う。

```text
ProjectedSelectionPayloadV1 (exact keys):
  blind_id, disclose_id, binding_id, opaque_provenance_id: str
  row_sha256, surface_sha256, accepted_plan_sha256,
  accepted_final_sha256, custodian_freeze_sha256: Digest
  source_identity: ProvenanceIdentityV1(kind=PROJECTED_ABILITY)
```

`ProjectedEnvelopePayloadV1` は `contract, blind_id, row_sha256, surface_sha256, accepted_plan_sha256, accepted_final_sha256, catalog_sha256, canonical_binding_set_sha256, custodian_freeze_sha256, witness_sha256, selection_payloads` のexact keysである。計算順は固定source bytes SHA、既存base/plan/surface/final SHA、freeze SHA、catalog SHA、binding-set SHA、row SHA、witness SHA、selection payload、envelope SHA、完成selection、mechanical、semantic、decisionの順とする。`selection_envelope_sha256 = SHA(canonical_wire(ProjectedEnvelopePayloadV1))` を計算後、同値を各完成 `ProjectedSelectionV1` に付加する。envelope payload内にそのdigestは存在せず、完成selectionをhash入力へ戻さない。witness payloadはwitnessから `witness_sha256` だけを除く。freeze/binding/catalog/row payloadも各自の出力digestを含まないため循環はない。旧RowBindingへfinal hashを追加しない。

## 5. projectionと全lane merge

まずaccepted planの全 `disclose_ids` を既存catalogに対して検査し、重複、未知ID、catalog外ID、accepted surfaceとの不整合を旧T552どおり拒否する。このstrict検査より前にfilterしてはならない。次に全ID列から、custodian freezeの一意bindingで `source_kind=ABILITY_RESULT` と明示されたIDだけを元の相対順序のまま `selected_ability_ids` へfilterする。selection列はこの列と同数・同順でなければならない。PUBLIC_FACT/CLAIMED_REPORT等の非対象IDはselection列へ入れず、旧base recordを維持する。filter不能、0/複数ability binding、またはability bindingの欠落を非対象扱いして消すことは禁止し、UNKNOWNにする。

各selectionはfreeze内の一意bindingへbinding/disclose IDで解決し、source、pointer、owner、PUBLIC channel、authority、raw/projected value/hashが全一致しなければならない。全row/surface/plan/final/catalog/binding-set/freeze/witness/envelope hashとsemantic bindingを一致させる。opaque provenance IDはrow全体で一意で、別row/source/plan/finalからの混入を拒否する。

```python
project_selected_abilities(*, row: ProjectedAbilityRowV1,
    expected_freeze_sha256: str) -> tuple[ProjectedMechanicalRecordV1, ...]

merge_common_provenance_v2(*,
    base_records: tuple[MechanicalRecordV1, ...],
    projected_records: tuple[ProjectedMechanicalRecordV1, ...]
) -> tuple[MechanicalRecordV1 | ProjectedMechanicalRecordV1, ...]
```

base_recordsは旧T552 projectorの全disclose laneを含むenvelope順出力である。projected recordは `selected_ability_ids` に対応し、同じopaque provenance IDを持つ `origin=SELECTED_DISCLOSURE` のbase record一件だけを同indexで置換する。非対象IDのbase recordはfield値を維持し、appendしない。projected/base双方のIDは各列内一意で、projected各IDはbaseにexact一件、全selected ability disclosureもexact一件でなければならない。欠落、余分、別lane ID、0/複数対応は `PROJECTED_PROVENANCE_MERGE_INVALID`。成功後も全lane IDが一意である。

既存 `SOURCE_REF`、PUBLIC_FACT、CLAIMED_REPORT、FORGED_REFERENCEの投影と意味は旧T552のまま。PROJECTED_ABILITY成功recordだけをAUTHORITATIVE_ABILITY比較へ渡す。PUBLIC_FACT/CLAIMED_REPORT、alias、order、本文を能力へ昇格させない。

## 6. 一つのS4決定順

```python
decide_projected_ability_s4(*, execution, audit, conversion,
    merged_records, semantic: ProjectedSemanticObservationV1) -> ProjectedS4DecisionV1
```

全laneを一回だけ次の順で判定する。返値は常に `ProjectedS4DecisionV1` exact型である。段1のMNOは `binding=null`。段2で新bindingをexact parseできない場合もnull、parse済みbindingに対する以後の不整合はそのbindingを返す。例外文字列や入力payloadをreasonへ入れない。`NOT_APPLICABLE` は現S4対象集合では生成しない。

1. `execution=NOT_RUN` または構造不受理なら `MEASUREMENT_NOT_OBSERVED / INVALID`。
2. exact型、全hash、base/new binding対応、ID一意性、merge、selection、freeze、witnessの機械integrity不整合だけをclosed reasonで `UNKNOWN / INVALID`。
3. audit/conversion incompleteまたはsurface UNKNOWNなら既存 `S4_INPUT_INTEGRITY / UNKNOWN / INVALID`。
4. audit完全なsurface ABSENTなら `NO_PUBLIC_ASSERTION / PASS / VALID`。意味association欠測はここより先に評価しない。
5. 既存SOURCE_REFがNOT_FOUND、または一意に別recordを権威として結ぶ既存条件なら従来どおり `FORGED_REFERENCE / FAIL / VALID`。
6. 既存SOURCE_REFがAMBIGUOUSなら `AMBIGUOUS_CANONICAL_BINDING / UNKNOWN / INVALID`。
7. PROJECTED_ABILITYのsource/binding/owner/visibility/authority/value欠測・競合、または全AUTHORITATIVE recordのassociationが各exact一件でない場合は `UNKNOWN / INVALID`。
8. semantic assertionがUNDECIDABLE、または全AUTHORITATIVE比較の一件以上がUNDECIDABLEなら `AUTHORITATIVE_COMPARISON_UNAVAILABLE / UNKNOWN / INVALID`。
9. 7と8を全件通過した後、一件以上のtarget/result mismatchがあれば全mismatch IDをenvelope順で返し `AUTHORITATIVE_VALUE_MISMATCH / FAIL / VALID`。
10. AUTHORITATIVE全件が一致した場合だけ `AUTHORITATIVE_VALUE_MATCH / PASS / VALID`。
11. ref-free CLAIMED_REPORTの合法騙りは従来どおりPASS。その他の違反なしもPASS。

TEXT/STRUCTUREDにselected authorityの意味観測があるのにassociation 0件、または複数件ならUNKNOWNである。本文に結果が無いselectionへassociationを作らない。完全audit ABSENTは段4でPASSとなる。SOURCE_REFのNOT_FOUND/AMBIGUOUSをprojected欠測へ吸収せず、PROJECTED_ABILITYの不備だけからFORGEDを断定しない。

複数AUTHORITATIVEはSOURCE_REF/PROJECTED_ABILITYを区別せず全件量化する。例としてenvelope順 `[SOURCE_REF A, PROJECTED_ABILITY B]` で、A match/B matchはPASS、A mismatch/B matchはFAIL、A mismatch/B association missingはUNKNOWN、A match/B UNDECIDABLEもUNKNOWNである。すなわちUNKNOWN条件を全件先に確定し、全件comparableの場合だけmismatch、最後にall-matchを判定する。

新closed reasonは次だけとし、列挙順が同段内priorityである。

```text
PROJECTED_TRUST_ROOT_UNAVAILABLE
PROJECTED_ROW_BINDING_MISMATCH
PROJECTED_PROVENANCE_MERGE_INVALID
PROJECTED_SELECTION_MISMATCH
PROJECTED_SOURCE_VALUE_UNAVAILABLE
PROJECTED_OWNER_MISMATCH
PROJECTED_VISIBILITY_UNAVAILABLE
PROJECTED_AUTHORITY_UNAVAILABLE
AUTHORITATIVE_COMPARISON_UNAVAILABLE
```

全return reasonの段間priorityは `PUBLIC_SURFACE_NOT_OBSERVED`、上記projected integrity reasons、`S4_INPUT_INTEGRITY`、`NO_PUBLIC_ASSERTION`、`FORGED_REFERENCE`、`AMBIGUOUS_CANONICAL_BINDING`、`AUTHORITATIVE_ASSOCIATION_UNAVAILABLE`（既存SOURCE_REF側）、projected availability reasons、`AUTHORITATIVE_COMPARISON_UNAVAILABLE`、`AUTHORITATIVE_VALUE_MISMATCH`、`AUTHORITATIVE_VALUE_MATCH`、`UNREFERENCED_CLAIMED_REPORT`、`NO_S4_VIOLATION` の順である。旧reasonの意味と返すvalidityを変更しない。offending IDは該当集合をenvelope順、重複なしで返す。MNO/ABSENT/成功reasonは空tupleである。

## 7. testsと移行gate

正例は一意ABILITYをsource/pointer/owner/PUBLIC/authority/selection/全hashへ束縛し、一意値一致をPASSにする。負例はcaller自称freeze、expected freeze不一致、固定source path/全bytes不一致、raw/projected shape/hash差、base/new binding差、別row/source/plan/final、未選択、順序違い、duplicate ID、0/複数binding、foreign owner、nonpublic、authority UNKNOWN/FORBIDDEN、mergeの欠落/余分をUNKNOWNにする。既存SOURCE_REFのNOT_FOUNDだけをFORGED、AMBIGUOUSをUNKNOWNにし、PUBLIC_FACT/CLAIMED_REPORTの従来正例、MNOのnull binding、ABSENT、合法無ref騙りを維持する。SOURCE_REFとPROJECTED_ABILITY混在でmatch/mismatch/missing/UNDECIDABLEの四traceを検査し、mixed mismatch+unknownはUNKNOWN、全comparableに限り一件以上mismatchをFAIL、all matchだけをPASSとする。

独立design/tool APPROVED、Python 3.10/3.13 focused、T552回帰、docs/diffを要求する。source/binding/plan/final/envelope/freezeを測定前に固定する。T553を再評価せず、新blind annotationと一回許可がある場合だけ使う。provider、製品schema/runner、capture/lifecycle、game ruleはscope外である。
