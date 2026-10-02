# Phase 6 CO surface source binding 設計

Status: APPROVED
Task: T559
Contract: `S4_CO_SURFACE_SOURCE_BINDING_V1`

独立APPROVED content SHA: 8c0370cc5fbc8758b7a585c701449dde76b6e53f9823d1450df1cb6c600dbc5b。
Review report SHA: e9f0b57a82b0587cce22ac447a587ddefb0dd3f3ab9a52037f0385dcf81eef7a。Mainは承認metadataだけ追記。

## 1. 目的と境界

T558で機械観測された `FINAL_SURFACE` 5行について、accepted finalの `co_option_id` / `claimed_role_option_id` を表示値とみなさず、hash-pinned fixtureのopaque binding IDとして解決し、保存public claimの `option` / `claimed_role` とexact一致することを証明するtest-only契約を定義する。

本契約は本文、alias、role名、option名の意味を読まない。authorityを新設・昇格せず、保存source/binding/catalogに既にあるactor/source kind/authorityだけを検査する。旧T555/T556 helper、旧surface、raw final、annotation、decision、score、hashは変更しない。ROOT_BINDING 7行、publication channel、provider、game、製品schemaは対象外である。

T558の5/5一致は設計入力の診断であり、witness完成や再評価結果ではない。本設計の独立承認後に、別実装とsource freezeで再現できた行だけを証明済みにする。

## 2. 入力trust root

入力はT556で固定済みのsource manifest/preflight、fixture input、accepted-final locator、旧packet/mechanical/mapping、row root pinである。Mainは別系統expected digestを渡す。adapterは各段の開始・終了で全source fileのSHA/size、connector/helper source SHA、preflight、Main pinを再照合する。hashは真正性そのものではなく、既存custodianとMainのscope/hash確認をtrust rootとする。

対象行は次をすべて満たすものに限定する。

- 旧executionが `COMPLETE/ACCEPTED`。
- T556 reasonが `SAVED_ACCEPTED_FINAL_UNAVAILABLE` かつ限定診断classが `FINAL_SURFACE`。
- source formatは `CANDIDATE_V2`。baseline用opaque CO ID dereferenceは定義しない。
- response whole bytes、`/choices/0/message/content` bytes、保存row.finalがT556 locator/outcome/container sealへexact一致する。
- rowのcase/seed/stage/ordinal、fixture 3 digest、old binding/envelope/base record、accepted plan/surface hashが固定値へ一致する。

対象5行の欠測、重複、型/hash不正は補完せずUNKNOWNとする。非対象187行は§7の排他的partitionにより `NOT_APPLICABLE` または既存MNOへ閉じ、入力不正と扱わない。

## 3. closed型

```text
CoSurfaceBindingWitnessV1 exact keys:
  contract: "S4_CO_SURFACE_SOURCE_BINDING_V1"
  evaluation_id: opaque str
  old_blind_id: str                 # custodian-private
  row_identity_sha256: Digest
  source_sha256, bindings_sha256, catalog_sha256: Digest
  accepted_final_sha256, accepted_plan_sha256,
  old_row_binding_sha256, old_selection_envelope_sha256,
  saved_surface_sha256: Digest
  raw_selection: {
    decision: "DECLARE"
    option_binding_id: nonempty str
    role_binding_id: nonempty str
    comment_sha256: Digest
    fact_ids: tuple[nonempty str,...] # 0..2、順序保持
  }
  option_resolution: CoOpaqueResolutionV1
  role_resolution: CoOpaqueResolutionV1
  catalog_membership_sha256, fact_binding_set_sha256: Digest
  claim_projection_sha256: Digest
  witness_sha256: Digest

CoOpaqueResolutionV1 exact keys:
  binding_id: nonempty str
  source_kind: "CO_OPTION"|"CLAIMED_ROLE_OPTION"
  projection_sha256, value_sha256: Digest
  pointer: absolute JSON pointer
  actor_id: nonempty str
  channel: null
  authority: "PUBLIC"
  resolved_value_sha256: Digest
```

`witness_sha256` は自身を除くpayloadのcanonical hash。`row_identity_sha256` は `{case_id,seed,stage,ordinal}` exact payloadのhashであり、値はpublic出力へ出さない。resolved value本体もprivate witness外へ出さず、digestだけを保持する。

行結果は次のclosed型とする。

```text
CoSurfaceBindingResultV1 exact keys:
  evaluation_id: str
  status: "WITNESS_PROVED"|"UNKNOWN"|"NOT_APPLICABLE"|
          "MEASUREMENT_NOT_OBSERVED"
  reason: "EXACT_CO_SOURCE_BINDING"|
          "CO_SOURCE_BINDING_MISSING"|
          "CO_CATALOG_MEMBERSHIP_INVALID"|
          "CO_SURFACE_MISMATCH"|"INPUT_INTEGRITY"|
          "PREVIOUSLY_EVALUATED"|"ROOT_BINDING_OUT_OF_SCOPE"|
          "PUBLIC_SURFACE_NOT_OBSERVED"
  witness_sha256: Digest|null
```

`WITNESS_PROVED` はsource/display接続だけを表し、S4 PASS/FAIL、意味annotation、projected row完成を表さない。

## 4. accepted finalのclosed抽出

outer responseとinner JSONはduplicate key/nonfinite/UTF-8不能を拒否する。innerは次のexact shapeだけを許す。

```text
{
  "decision": "DECLARE",
  "co_option_id": <nonempty str>,
  "claimed_role_option_id": <nonempty str>,
  "comment": <nonempty str>,
  "fact_ids": [<nonempty str>, ...]  // 0..2、重複なし
}
```

上記5 keysとのexact一致を要求する。追加key、欠測、null ID/comment、空comment、`True`/数値、未知decision、messageとの同居、fact ID重複または3件以上を拒否する。現 `_co_schema` のDECLARE branchはcommentを必須nonempty stringとするため、comment nullのDECLARE variantは本contractに存在しない。保存surfaceを見てshapeやpointerを選ばず、expected kindは常に `TEXT_AND_STRUCTURED`、commentはsurface `comment` へだけ写す。本文の意味は読まない。

`co_option_id` と `claimed_role_option_id` は表示値ではなく、それぞれbinding lookup keyとしてだけ使用する。

## 5. source-bound dereference

### 5.1 option

`co_option_id` と同じkeyのbindingがexact一件必要である。binding exact keysは既存fixtureの `{projection_sha256,pointer,source_kind,actor,channel,authority,value_sha256}`。次を全て要求する。

- `source_kind="CO_OPTION"`、`actor == source/context/player_id`、`channel=null`、`authority="PUBLIC"`。
- `projection_sha256 == SHA(canonical source)`。
- pointerは `/action_context/options/<i>` のcanonical decimal indexだけを許し、解決先はdict。
- 解決先は `type="co_declare"`、`option_id` は非空str、`claimed_role_ids` は重複のない非空str列。他fieldは意味解釈しないがwhole resolved object hashを `value_sha256` とexact比較する。
- 同pointerを指す別 `CO_OPTION` binding、同binding IDの重複、index範囲外を拒否する。

### 5.2 claimed role

`claimed_role_option_id` と同じkeyのbindingがexact一件必要である。

- `source_kind="CLAIMED_ROLE_OPTION"`、actor/authority/channel/projection SHAはoptionと同条件。
- pointerは選択option pointerに `/claimed_role_ids/<j>` を付けたものだけを許す。別option配下のroleは拒否する。
- 解決値は非空strで、option objectの `claimed_role_ids[j]` と型・値がexact一致し、binding `value_sha256` と一致する。

### 5.3 catalog membership

保存 `GenerationCatalogV2.co_options` を型付き構造として検査する。全 `option_id` は一意、各 `claimed_role_option_ids` は一意である。選択option IDに一致する `CoOptionV2` がexact一件あり、そのrole列に選択role IDがexact一回含まれることを要求する。別CO optionへの所属、catalog外ID、複数membershipを拒否する。

`catalog_membership_sha256` は `{option_binding_id, role_binding_id, co_option_index, role_index}` のcanonical hash。prefixやindexだけから所属を推論せず、catalog entryとsource pointerの両方を照合する。

### 5.4 fact IDs

raw `fact_ids` は順序を保持し、保存catalogの `fact_ids` のsubsetであることを要求する。各IDにはfixture bindings内のexact一件が必要で、`source_kind="PUBLIC_FACT"`、`authority="PUBLIC"`、projection SHA、pointer解決値、actor、value SHAを既存 `verify_bindings` と同じ条件で照合する。factの本文や意味は読まない。

`fact_binding_set_sha256` はraw順の `{fact_id,binding_sha256,resolved_value_sha256}` 列のcanonical hash。空列も合法な明示値としてhashする。fact IDを消去、並べ替え、trimせず、旧base recordへ新規投影しない。

## 6. 保存surfaceへのprojection

source解決後だけ次を構築する。

```text
resolved_claim = {
  decision: "DECLARE",
  option: <option pointer先 object.option_id>,
  claimed_role: <role pointer先 string>
}
```

保存surfaceはexact keys `{kind,text,comment,claims}` を要求する。claimsはexact一件で、そのitemは `{decision,option,claimed_role}` exact三keys、型も一致する。`{kind:"TEXT_AND_STRUCTURED",text:null,comment:<raw nonempty comment>,claims:[resolved_claim]}` とexact一致させる。STRUCTURED/comment nullを本schema versionの成功形にしない。

`claim_projection_sha256` はresolved claimのcanonical hash。raw opaque IDと保存表示値を直接比較しない。保存claim値を使ってbinding候補を選ばない。source resolutionとcatalog membershipを完了後、唯一のprojectionを保存surfaceへ比較する。これにより期待値に合わせて解決先を選ぶ循環を防ぐ。

## 7. plan・row・old baseとの結合

accepted planは旧mechanical rowの保存値をbyte-equivalentに保持し、そのhashをwitnessへ入れる。old selection envelope、old row binding、base records、execution/audit/conversionもT556固定値とexact一致させる。raw CO finalの `fact_ids` をold plan/envelopeへ追加・削除せず、両者にfact selection表現が存在する場合だけID列と順序のexact一致を要求する。表現が無い場合は `NOT_REPRESENTED` としてprivate診断に固定し、矛盾補正や新base provenanceを作らない。CO option/role IDもplan schemaに存在すると推定せず追加しない。

同じ `evaluation_id` のaccepted final locator、fixture source/bindings/catalog、old row、saved surfaceだけを結合する。case/seed/stage/ordinalまたはpairを跨ぐsource利用、別行の同名binding ID、caller提供surfaceへの差替えは禁止する。Mainはsource snapshotから全hash、全192行集合、次の排他的partitionを独立再計算する。

- `TARGET_SURFACE_GAP`: 5行。本契約の唯一の検査対象。
- `PRIOR_READY`: 167行。`NOT_APPLICABLE / PREVIOUSLY_EVALUATED`。
- `ROOT_GAP`: 7行。`NOT_APPLICABLE / ROOT_BINDING_OUT_OF_SCOPE`。
- `MEASUREMENT_NOT_OBSERVED`: 13行。既存MNOを維持する。

行は上記exact一つに属し、順序はT556 frozen row順、`evaluation_id` は全192で一意とする。5+167+7+13以外、重複、欠落、partitionの付替えはglobal input integrity failureである。ROOT7の「selected disclosureなし」という別診断を本契約の判定材料にしない。

```text
CoSurfaceCandidateManifestV1 exact keys:
  contract: "S4_CO_SURFACE_SOURCE_BINDING_V1"
  version: "V1"
  source_manifest_sha256, preflight_sha256: Digest
  target_scope_sha256: Digest
  rows: tuple[{
    evaluation_id: nonempty str
    old_row_binding_sha256, saved_surface_sha256: Digest
    partition: "TARGET_SURFACE_GAP"|"PRIOR_READY"|
               "ROOT_GAP"|"MEASUREMENT_NOT_OBSERVED"
    status: CoSurfaceBindingResultV1.status
    reason: CoSurfaceBindingResultV1.reason
    witness_sha256: Digest|null
  }, ...]                         # T556 frozen順、exact 192
  counts: {target:5, prior_ready:167, root_gap:7, mno:13,
           proved:strict int>=0, target_unknown:strict int>=0}
  manifest_sha256: Digest
```

`target_scope_sha256` は `{contract,ordered_evaluation_ids}`（5 ID、T556 frozen順）のcanonical hashで、MainがT558の固定diagnostic scopeから別系統に計算する。`manifest_sha256` は自身を除く全payloadのcanonical hash。`proved + target_unknown == 5` を要求し、非対象行のwitnessはnullでなければならない。

```text
MainPinnedCoSurfaceWitnessRootsV1 exact keys:
  contract: "S4_CO_SURFACE_SOURCE_BINDING_V1"
  task: "T559"
  source_manifest_sha256, preflight_sha256,
  candidate_manifest_sha256, target_scope_sha256,
  coverage_rows_sha256: Digest
  rows: tuple[{
    evaluation_id: str
    expected_witness_sha256: Digest
  }, ...]                         # WITNESS_PROVED候補だけ、元row順
  counts: {target:5, prior_ready:167, root_gap:7, mno:13,
           proved:strict int>=0, target_unknown:strict int>=0}
  pin_sha256: Digest
```

`coverage_rows_sha256` はcandidate `rows` 全192のcanonical hash。`pin_sha256` は自身を除くpayload hash。全digestはlowercase 64 hex、countはboolを拒否するstrict intで、`proved + target_unknown == 5`。Mainはcandidateとは別入力のfrozen T556 roots、T558 target scope、source snapshotsからpartition、全row hash、各witness SHAを再計算し、自己申告digestをexpected値へコピーしない。adapterは外部引数 `expected_pin_sha256` と完成pinのexact一致を全段開始・終了で要求する。

## 8. 決定順

1. global source manifest、preflight、Main pin、candidate manifest、192行集合・順序、partition、code SHA不整合はartifact未完成として全体停止。
2. MNO 13行は `PUBLIC_SURFACE_NOT_OBSERVED / MEASUREMENT_NOT_OBSERVED` を維持する。
3. `PRIOR_READY` 167行は `PREVIOUSLY_EVALUATED / NOT_APPLICABLE`、`ROOT_GAP` 7行は `ROOT_BINDING_OUT_OF_SCOPE / NOT_APPLICABLE` とし、witnessを作らない。
4. `TARGET_SURFACE_GAP` 5行だけを以下へ進める。対象class/source format/row association不正は `INPUT_INTEGRITY / UNKNOWN`。
5. raw finalのclosed抽出不能、binding 0件/複数、pointer/value/actor/authority不一致は `CO_SOURCE_BINDING_MISSING / UNKNOWN`。
6. catalog option/role membership不一致は `CO_CATALOG_MEMBERSHIP_INVALID / UNKNOWN`。
7. 一意projectionと保存surfaceが不一致なら `CO_SURFACE_MISMATCH / UNKNOWN`。
8. 全条件成立時だけ `EXACT_CO_SOURCE_BINDING / WITNESS_PROVED`。

一つの対象行で複数失敗がある場合は上記最初のreasonだけを記録する。reasonにID、値、path、本文を含めない。非対象187行を対象検証へ流したり `INPUT_INTEGRITY` に分類したりしない。

## 9. artifactと再適用

新private containerへsource snapshot、preflight、row inputs、witness候補、Main pin、診断結果、safe summaryをcreate-only保存する。旧artifactへ追記しない。safe summaryはcontract/version、hash、全192行の状態count、対象5行のreason count、provider calls 0だけを含み、condition mapping、ID、値、path、本文を含めない。

診断artifactはT556の同じ `evaluation_id`、old row binding SHA、saved surface SHAへのsidecar associationだけを持てる。association exact keysは `{contract,evaluation_id,old_row_binding_sha256,saved_surface_sha256,witness_sha256}` とし、そのcanonical hashをMainが別pinする。sidecarは旧row、旧decision、旧freezeへ埋め込まず、projected rowやdecision入力でもない。

新しいoffline診断では、対象5行すべてが既存Main `derive_source_freeze` で `ProjectedAbilityInputError` になることも確認された。surface witnessとROOT/freeze成立は別条件であり、`WITNESS_PROVED` をS4回復、READY、PASSへ数えない。5行delta packet、annotation、decision、全192行の新version合成は、別のROOT/freeze設計が独立承認され実装・検証されるまで実施不可である。既に意味評価済みの167行を再評価せず、既存annotationを対象5行へ移植せず、本witnessからPASS/FAILを推定しない。旧T556結果は不変保存する。

## 10. 公開synthetic acceptance

正例:

- opaque option/role IDがそれぞれ正しいbindingへ解決し、同じCO optionのcatalog membership、owner、authority、pointer、whole value hash、saved claimがexact一致する。
- 現schemaどおりnonempty comment＋0..2 fact IDs。空fact列と非空fact列の両方。
- 同名IDが別行に存在してもrow/source rootを跨がない。

負例:

- optionまたはrole binding欠測/重複、wrong source kind、foreign actor、authority UNKNOWN/FORBIDDEN、channel非null、projection/value hash差。
- roleが別option所属、catalog外/重複membership、pointer index差、option objectのrole列とbinding値差。
- raw IDを表示値として直接採用、保存claimからbindingを逆引き、alias/role意味一致による補正。
- final/surface/plan/row/envelope/source/Main pinの一バイト改変、cross-row/case/seed/stage/ordinal。
- comment null/空、fact_ids欠測/重複/3件以上/catalog外、PUBLIC_FACT binding不正、fact順序改変、old plan/envelopeとの明示selection矛盾。
- claim件数/順序/key/type、comment/kind/textの差、unknown extra field、`True == 1` 型混同。
- ROOT_BINDING行、publication channel、旧annotation/scoreを入力にする試み。

Python 3.10/3.13 focused、全5 target＋非target187、T555/T556関連回帰、開始終了source hash、create-only、payload-free error、safe非漏洩を検査する。独立design APPROVED後だけ実装し、独立tool APPROVED後だけprivate適用へ進む。

## 11. 未解決境界

T558は5行の値一致を報告したが、実装contractによるsource/root/Main pin付き再現は未実施である。本設計は5行回復を確定しない。binding/catalog shapeまたはrow associationが一件でも証明不能なら、その行はUNKNOWNを維持する。

ROOT_BINDING 7行は本契約で扱わない。明示publication fieldがない現状を変更せず、T557どおりUNKNOWNを維持する。製品captureへ新fieldを追加する判断も本設計には含めない。
