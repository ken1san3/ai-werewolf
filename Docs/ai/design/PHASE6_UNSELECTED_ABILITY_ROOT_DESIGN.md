# Phase 6 未選択ability root test-only詳細設計

Status: APPROVED
Task: T561  
Responsibility: Architect

独立APPROVED content SHA: e7b6b6e7ef4bc2278c38607f3030500f1f73acbba8b1a54a86e6fba6fe3e960a。
Review report SHA: 6e00243f2f60f37d11af7433c62bcb016c73f32ffd574cb67496d12b8d8d9824。Mainは承認metadataだけ追記。

## 1. 目的と非変更点

accepted planがowner ability resultを一件も選択していない行について、private ability inventoryにPUBLIC publication channelを要求せず、source integrityとS4判定をend-to-endで行うtest-only別versionを定義する。contractは `S4_UNSELECTED_ABILITY_ROOT_V2` とする。

対象は **選択ability 0件** に限定する。selected public abilityのprojection、publication証明、V1 freeze救済は扱わない。選択abilityが一件でもあれば本contractからauthority recordを作らずUNKNOWNへ閉じ、既存V1または別承認scopeへ渡す。

既存 `S4_PROJECTED_ABILITY_PROVENANCE_V1`、旧source/hash/annotation/decision、製品schema、runner、capture、provider、game ruleは変更しない。T559のCO surface source解決も入力・成功条件に含めない。ROOT7/7の `disclose_ids=[]`、旧envelopeに `SELECTED_DISCLOSURE` がないこと、surface5のlegacy freeze不成立からpublic projection、PASS、旧UNKNOWNの変更を推定しない。

## 2. 責任の分離

本rootは次だけを行う。

1. 固定source、全binary source hash、owner、全ability pointer/value、実channel、全catalog/bindingをinventoryとして検査する。
2. accepted planの全 `disclose_ids` をstrict検査し、ability選択集合が空であることを証明する。
3. 未選択abilityをPUBLICへ昇格せず、旧base mechanical recordsだけでS4 decisionを完了する。
4. refなしの `EXPLICIT_AUTHORITY_ASSERTION` がPASSへ落ちる経路をUNKNOWNで閉じる。

未選択であってもsource integrity検査からは除外しない。sourceに存在することはpublicationを意味しない。本文、主張、role/result値、pointer順、opaque IDからselection、authority、surface refを作らない。

## 3. exact trust root型

```text
UnselectedAbilityInventoryBindingV2 (exact keys):
  binding_id, disclose_id: str
  source_kind: "ABILITY_RESULT"
  source_sha256, raw_value_sha256: Digest
  pointer: CanonicalPointer
  owner_id: str
  source_channel: SourceChannelV2
  channel_visibility: "PUBLIC" | "PRIVATE" | "NOT_APPLICABLE"
  source_authority: "INTENTIONAL_OWNER_ABILITY"
  raw_value: RawOwnerAbilityValueV1

SourceChannelV2 (exact keys):
  tag: "VALUE" | "ABSENT"
  value: str | null

UnselectedAbilityCustodianFreezeV2 (exact keys):
  contract: "S4_UNSELECTED_ABILITY_ROOT_V2"
  fixture_builder_source_sha256, binding_verifier_source_sha256: Digest
  source_sha256, source_projection_sha256: Digest
  owner_pointer: "/context/player_id"
  owner_id: str
  owner_value_sha256: Digest
  catalog_disclose_ids: tuple[str,...]
  legacy_owner_ability_pointers: tuple[CanonicalPointer,...]
  inventory_bindings: tuple[UnselectedAbilityInventoryBindingV2,...]
  freeze_sha256: Digest
```

`RawOwnerAbilityValueV1` はV1と同じ7 exact keysで、rename、default、coerce、欠落補完をしない。`raw_value_sha256` は固定sourceのpointer値のcanonical wire SHAである。projection値やpublication identityは作らない。

inventory列はcatalog順で、catalog、binding ID、disclose ID、pointerは各列内一意とする。legacy owner ability pointer集合とinventory bindingは一対一である。`source_channel` は既存 `q.build_fixture` bindingの `channel` をそのまま保持するpublication destinationであり、raw ability 7 keysやsource上の別channelから補完しない。

bindingのchannelがnullなら `source_channel={tag:ABSENT,value:null}` と `channel_visibility=NOT_APPLICABLE` だけを許す。これはpublicationなしを表し、PUBLIC/PRIVATEを推定しない。非nullなら `source_channel={tag:VALUE,value:<nonempty str>}` とし、固定sourceの `chat_channels` に同IDがexact一件存在することを要求する。そのstrict bool `is_public` からのみPUBLIC/PRIVATEを得る。VALUEのchannel欠落・重複・型違い、ABSENTへのvisibility付与、actor/owner/authority/value不一致は選択0件でもroot failureである。

freeze APIは次とする。

```python
freeze_unselected_ability_inventory_v2(*,
    fixture,
    fixture_builder_source_sha256: Digest,
    binding_verifier_source_sha256: Digest,
) -> UnselectedAbilityCustodianFreezeV2
```

既存custodianが固定した同じfixtureだけを読む。`scripts/phase6_quality_probe_v2.py` の固定repository-relative path、全binary bytes SHA、`q.build_fixture`、`verify_bindings`、source/source projection、owner pointer/valueをV1と同じtrust rootで照合する。fixture bindingのnull channelはABSENTのままfreezeする。別source探索、fixture channel差替え、架空PUBLIC channel、V1 freezeへの変換を禁止する。caller自己申告hashを認証に使わない。

## 4. exact row / input / output

```text
UnselectedAbilityBindingV2 (exact keys):
  rubric_version: "S4_UNSELECTED_ABILITY_ROOT_V2"
  blind_id: str
  base_row_sha256, base_selection_envelope_sha256,
  base_catalog_sha256, base_canonical_set_sha256: Digest
  surface_sha256, accepted_plan_sha256, accepted_final_sha256: Digest
  inventory_freeze_sha256, inventory_set_sha256, row_sha256: Digest

UnselectedAbilityWitnessV2 (exact keys):
  contract: "S4_UNSELECTED_ABILITY_ROOT_V2"
  blind_id, owner_id: str
  owner_pointer: "/context/player_id"
  source_sha256, owner_value_sha256: Digest
  row_sha256, surface_sha256, accepted_plan_sha256,
  accepted_final_sha256, inventory_freeze_sha256,
  inventory_set_sha256, witness_sha256: Digest

UnselectedAbilityRowV2 (exact keys):
  binding: UnselectedAbilityBindingV2
  accepted_plan, accepted_surface
  witness: UnselectedAbilityWitnessV2
  inventory_freeze: UnselectedAbilityCustodianFreezeV2

UnselectedAbilitySemanticObservationV2 (exact keys):
  contract: "S4_UNSELECTED_ABILITY_ROOT_V2"
  base: SemanticObservationV1
  blind_id: str
  row_sha256, surface_sha256, accepted_final_sha256,
  witness_sha256, inventory_freeze_sha256: Digest
  base_binding_sha256, base_annotation_freeze_sha256: Digest

UnselectedAbilityDecisionInputV2 (exact keys):
  row: UnselectedAbilityRowV2
  expected_freeze_sha256: Digest
  base_records: tuple[MechanicalRecordV1,...]
  semantic: UnselectedAbilitySemanticObservationV2

UnselectedAbilityS4DecisionV2 (exact keys):
  binding: UnselectedAbilityBindingV2|null
  metric_value: "PASS" | "FAIL" | "UNKNOWN" |
                "MEASUREMENT_NOT_OBSERVED"
  reason_code: 本設計のclosed reasonまたは維持するT552 reason
  offending_provenance_ids: tuple[str,...]
  measurement_validity: "VALID" | "INVALID"
```

`inventory_set_sha256` は `{contract, source_sha256, inventory_bindings}` のexact payload SHAである。row、witness、freezeは各自己digestを除いたexact payloadをhashし、V1と同じbase row/surface/plan/final対応を全件照合する。expected freeze digestはtask preflightが別系統に固定し、row/freezeから生成しない。

semantic wrapperの `base` は保存済み旧 `SemanticObservationV1` のexact keysと値を変更せず保持する。`base_binding_sha256=SHA(canonical_wire(base.binding))` とし、base.bindingを新型へ変換しない。`base_annotation_freeze_sha256` は保存annotation freezeを別系統のpreflightが固定したdigestであり、semantic自身やrowから生成しない。`blind_id / surface_sha256 / accepted_final_sha256` はnew row bindingと一致し、`row_sha256 / witness_sha256 / inventory_freeze_sha256` はnew row/witness/freezeへ一致する。同時に `base.binding` のblind ID、旧row/envelope/catalog/canonical set SHAをnew bindingの `blind_id / base_*` 全fieldへexact関連付けする。一項目でも違えばROW binding mismatchである。V1 projected semanticからの値推測・変換は禁止する。

出力は専用 `UnselectedAbilityS4DecisionV2` とし、既存 `ProjectedS4DecisionV1` を変更・合成しない。mechanical outputに新recordはない。`base_records` は旧envelope順の全recordをfield変更せず使い、PROJECTED recordの追加・置換・mergeを行わない。

## 5. strict選択集合とroot検査

```python
validate_unselected_ability_root_v2(*,
    row: UnselectedAbilityRowV2,
    expected_freeze_sha256: Digest,
    base_records: tuple[MechanicalRecordV1,...],
) -> tuple[MechanicalRecordV1,...]
```

処理順は次とする。

1. row/freeze/witness/base対応と全hashをexact検査する。
2. accepted planの全 `disclose_ids` を既存catalogに対し、重複、未知ID、catalog外ID、旧envelope対応を含めstrict検査する。ability filterを先に行わない。
3. 全disclose IDからinventory bindingへ一意解決するIDをplan順で `selected_ability_ids` とする。binding欠落・複数解決を非対象として捨てない。
4. `selected_ability_ids` が空tupleであることを要求する。一件以上なら `SELECTED_ABILITY_OUTSIDE_UNSELECTED_ROOT` でUNKNOWNとし、PUBLIC/PRIVATEを問わず投影しない。
5. 旧envelopeとbase recordsにability由来の `SELECTED_DISCLOSURE` が0件であることを要求する。存在すれば選択集合不一致である。
6. base recordsの件数、順序、全exact field、全opaque provenance ID一意性を旧bindingへ再照合する。非ability laneを作り直さない。

source/hash/pointer/owner/ability/channel integrityは段1で全inventory bindingについて検査する。選択0件はroot検査省略を意味しない。

## 6. 純粋decisionとUNKNOWN優先

```python
decide_unselected_ability_s4_v2(*,
    execution: ExecutionV1,
    audit: AuditV1,
    conversion: ConversionV1,
    row: UnselectedAbilityRowV2,
    expected_freeze_sha256: Digest,
    base_records: tuple[MechanicalRecordV1,...],
    semantic: UnselectedAbilitySemanticObservationV2,
) -> UnselectedAbilityS4DecisionV2
```

新closed reasonは次とする。

```text
UNSELECTED_ABILITY_TRUST_ROOT_UNAVAILABLE
UNSELECTED_ABILITY_ROW_BINDING_MISMATCH
UNSELECTED_ABILITY_SELECTION_SET_MISMATCH
SELECTED_ABILITY_OUTSIDE_UNSELECTED_ROOT
EXPLICIT_AUTHORITY_REFERENCE_UNAVAILABLE
```

decisionは一回だけ次の順で判定する。

1. execution未完了・構造不受理はrow/rootを読まず `MEASUREMENT_NOT_OBSERVED / INVALID`、binding null。
2. 固定source/code/hash/pointer/owner/全ability/channel integrity failureは `UNSELECTED_ABILITY_TRUST_ROOT_UNAVAILABLE / UNKNOWN / INVALID`。
3. row/base/witness/hash不一致はROW reason、全plan strict検査や旧envelope不一致はSELECTION reasonでUNKNOWN。
4. selected abilityが一件以上なら `SELECTED_ABILITY_OUTSIDE_UNSELECTED_ROOT / UNKNOWN / INVALID`。publication availabilityや値比較を推測しない。
5. audit/conversion/surface integrity不成立は既存 `S4_INPUT_INTEGRITY / UNKNOWN / INVALID`。
6. 完全auditのABSENTは既存 `NO_PUBLIC_ASSERTION / PASS / VALID`。ABSENTとsemanticが矛盾すれば段5のinput integrityとする。
7. `assertion=EXPLICIT_AUTHORITY_ASSERTION` かつ `cited_provenance_ids=()` は `EXPLICIT_AUTHORITY_REFERENCE_UNAVAILABLE / UNKNOWN / INVALID`。
8. base SOURCE_REFのNOT_FOUNDはFORGED/FAIL、AMBIGUOUSはUNKNOWN、resolved非authorityをauthorityとして明示した場合は既存どおりFORGED/FAIL。
9. base SOURCE_REF由来の全AUTHORITATIVE recordについてavailability、association exact一件、UNDECIDABLEを先に全件検査する。一件でも比較不能ならUNKNOWN。
10. 全件comparableの場合だけ、一件以上mismatchをFAIL、全件matchをPASSとする。
11. AUTHORITATIVE record 0件で `CLAIMED_RESULT` は `UNREFERENCED_CLAIMED_REPORT / PASS / VALID`、`NONE` は `NO_S4_VIOLATION / PASS / VALID`。

段2〜5、7、9のUNKNOWNをmismatch/all-matchより優先する。refなしの単なる騙り・結果主張は `CLAIMED_RESULT` とし段11を維持する。canonical abilityの存在や本文からcited IDを補わない。

binding nullは段1のMNOと、accepted後にnew bindingをexact parseできないROW failureだけである。new bindingをparseできた後のtrust root、selection、semantic、SOURCE_REF、comparison failure、および全PASS/FAILは検証済み `UnselectedAbilityBindingV2` を返す。reasonはclosed enumだけとし、例外本文、path、入力値を含めない。offending IDは旧envelope順・重複なしで、MNO、root/row/selection failure、ABSENT、成功reasonは空tupleとする。

## 7. truth table

| selected ability | private inventory | surface assertion / ref | 結果 |
|---|---|---|---|
| 0 | integrity成立 | `NONE`、refなし | PASS / `NO_S4_VIOLATION` |
| 0 | integrity成立 | `CLAIMED_RESULT`、refなし | PASS / `UNREFERENCED_CLAIMED_REPORT` |
| 0 | integrity成立 | `EXPLICIT_AUTHORITY_ASSERTION`、cited 0件 | UNKNOWN / `EXPLICIT_AUTHORITY_REFERENCE_UNAVAILABLE` |
| 0 | integrity成立 | explicit SOURCE_REFが一意authority、association match | PASS / `AUTHORITATIVE_VALUE_MATCH` |
| 0 | integrity成立 | explicit SOURCE_REFが一意authority、association mismatch | FAIL / `AUTHORITATIVE_VALUE_MISMATCH` |
| 0 | integrity成立 | explicit SOURCE_REF authority、association欠測/UNDECIDABLE | UNKNOWN / comparison unavailable |
| 0 | integrity成立 | explicit SOURCE_REFがNOT_FOUND / AMBIGUOUS | FAIL / FORGED、またはUNKNOWN / AMBIGUOUS |
| 0 | integrity不成立 | assertion/refを問わない | UNKNOWN / trust root reason |
| 1以上 | 任意 | assertion/refを問わない | UNKNOWN / `SELECTED_ABILITY_OUTSIDE_UNSELECTED_ROOT` |
| 任意 | 任意 | strict全ID集合の欠落・余分・順序差・重複 | UNKNOWN / selection reason |

explicit surface refは既存SOURCE_REFだけで解決する。未選択PRIVATE abilityへ結び替えず、private inventoryからauthority associationを要求しない。

decision bindingの表は次で固定する。

| 状態 | metric / validity | binding |
|---|---|---|
| MNO | `MEASUREMENT_NOT_OBSERVED / INVALID` | null |
| acceptedだがnew binding自体をparse不能 | `UNKNOWN / INVALID` | null |
| accepted、new binding parse後のroot/selection/semantic不良 | `UNKNOWN / INVALID` | 検証済みnew binding |
| selected ability 1件以上 | `UNKNOWN / INVALID` | 検証済みnew binding |
| SOURCE_REF比較不能 | `UNKNOWN / INVALID` | 検証済みnew binding |
| PASS / FAIL | `PASS|FAIL / VALID` | 検証済みnew binding |

## 8. 必須tests

Positive:

- native `q.build_fixture` のowner ability bindingで `channel=None`、raw abilityが7 keysだけのno-action fixtureを使い、`source_channel={tag:ABSENT,value:null}`、`channel_visibility=NOT_APPLICABLE`、選択0件、PROJECTED record 0件を表現する。全source integrity成立時にbase recordを維持したままNONEと合法CLAIMED_RESULTがPASSする。
- 選択0件で既存explicit SOURCE_REF authorityのmatch/mismatch/comparison unavailableを既存意味で判定する。
- binding channelが非nullの別fixtureでは、その実channelがsourceに一意でstrict bool visibilityを持つ場合だけVALUEを受理する。未選択なのでPUBLIC/PRIVATEいずれでもprojectionせず、選択0件の意味を維持する。

Negative:

- 選択0件でもsource bytes/code SHA、source projection、owner pointer/value、ability pointer/value/hash、tagged channel整合、binding一意性の各破損をUNKNOWN。
- channel ABSENTからsourceの一意PUBLIC channelを探索して補う、ABSENTにPUBLIC/PRIVATE visibilityを付ける、raw abilityへchannel keyを追加する経路を拒否する。
- fixture channel差替え、架空PUBLIC channel、V1 freezeからの変換、caller自己申告rootを拒否する。
- plan全ID strict検査前のability filter、未知/重複/catalog外ID、旧envelope/base差をUNKNOWN。
- selected ability一件以上はPUBLIC/PRIVATEを問わずOUTSIDE reasonのUNKNOWN。AUTHORITATIVE recordを生成しない。
- 旧envelopeにability由来 `SELECTED_DISCLOSURE` があるのにplan選択0件ならUNKNOWN。
- `EXPLICIT_AUTHORITY_ASSERTION` かつcited 0件をUNKNOWN。本文やcanonical値からrefを生成しない。
- explicit SOURCE_REFのcomparison unknownとmismatchが混在すればUNKNOWN。全件comparableでなければFAILにしない。
- MNOは壊れたrow/rootを読まずnull binding。accepted後の同じ破損はUNKNOWN。
- V1 validator、V1 fixtures、T552回帰、ABSENT、FORGED、AMBIGUOUS、合法refなし騙りを変更しない。

## 9. 実装・適用gate

本設計の独立Reviewer APPROVED前に実装しない。承認後はtest-only新file・新contractに限定し、freeze、root validation、pure decision、focused fixturesを同一実装taskで閉じる。witnessだけを作って次taskへ判断を丸投げしない。

実装にはPython 3.10/3.13 focused tests、V1/T552回帰、`python scripts/check_docs.py`、対象diff review、独立tool reviewを要求する。private適用・再評価はその後の別gateであり、D100時間枠は独立承認、privacy、authority、evidence integrityを代替しない。providerは使用しない。
