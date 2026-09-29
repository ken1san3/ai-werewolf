# Phase 6 v2 Authority Capture Bridge 限定詳細設計

Status: APPROVED — T518独立review（承認時本文hashはT518_APPROVAL_BINDING.json）
Task: T518
Parent: `Docs/ai/design/PHASE6_V2_CAPTURE_BINDING_DESIGN.md` 3.1〜3.4節、4.2〜4.3節
Baseline: `Docs/ai/handoffs/tasks/T517_APPROVAL_BINDING.json`
Binding SHA-256: `49ce67decbab9da05de0b9492b77780a7b31ba596d46356d04736b740e18c0cc`

## 1. 目的と境界

残る差分は、actual ownerからpublic channel、self disclosure、capture準備材料を純readで得る接点である。public constructorを持つ
`InboundAuthoritySnapshotV2`、caller supplied snapshot/dict、T515の`Structural*CandidateV2`はauthority proofにしない。

本単位の出力はprivate memory内のopaqueな`PreparedAuthorityCaptureMaterialV2(status="UNLEASED")`までとする。
`GenerationCaptureV2`、state lease、expiry、request/wireを作らず、実行・finalize入口は`LEASE_REQUIRED`で拒否する。broker、brain、projection、
provider、delivery、server/protocol/private permission/game ruleは対象外で、v1 sink-nullを維持する。state mutationを伴う
`DiscussionStateStore.capture(...)`はread内から呼ばない。既存canonicalだけで閉じ、新しいuser選択は不要である。

## 2. exact owner chain

authorityを発行できるchainを次へ限定する。

```text
actual NetworkClient -> exact _Phase6NetworkEventSource claim capability
 -> exact WorldState + current InboundAuthorityRuntimeV2 -> WorldAuthorityReadPortV2

validate_discussion_bootstrap full validation + private validation receipt
 -> exact runtime source bind -> ValidatedContextOwnerReceiptV2

exact DiscussionStateStore for that bound context -> DiscussionCaptureReadPortV2

three private owners -> AuthorityCaptureBridgeV2 -> UNLEASED prepared material
```

各receipt/port/bridgeはmodule-private issuerで作り、object identity tokenをserialize/hash/log/promptへ出さない。public
`WorldSnapshot / InboundAuthoritySnapshotV2 / BoundDiscussionContext / DiscussionCapture`、bare `GenerationCaptureV2`、任意lease IDを入力にして
発行しない。`promote_runtime_authority_v2(...)`と`create_runtime_state_lease_store_v2()`はfail-closedのままである。

## 3. bootstrap validationとcontext owner receipt

### 3.1 validator receipt

`PendingDiscussionContext`のpublic constructorはvalidation経由を証明しない。authority opt-in時だけ、
`validate_discussion_bootstrap(...)`がfull validation成功後にmodule-private `BootstrapValidationReceiptV2`を発行し、作成したexact
`PendingDiscussionContext`へ保持させる。receiptはvalidatorを通ったというroute markerであり、それ単独をauthority proofにしない。

bind時には、receipt identityに加えてretained manifest material/bytesとcontextへ次のfull validationを再実行する。これがbootstrap inputの
必要十分な検証境界であり、Python内部tokenだけで検証を省略しない。

1. canonical manifest bytesがretained bytesとbyte-equalで、そのSHA-256が`manifest_sha256`に一致する。
2. canonical context hash、`content_manifest_sha256`、manifest内role/modifier/channel descriptorを照合する。
3. context game/playerがactual network config、認証済み`ClientSnapshot.player_id`に一致する。
4. exact CURRENT `WorldSnapshot.self_view`のplayer/role/modifiersに一致する。
5. current inbound authorityのgame/playerとexact owner/generationが一致し、readinessが`PENDING_SYNC`または`READY`である。

全検証後、manifestをdiscardする直前に同一同期区間で`ValidatedContextOwnerReceiptV2`を発行する。発行に失敗したらdiscardせずbindも成立させない。
direct constructorにはvalidator receiptが無いためauthority receiptを発行しない。通常のv1 `bind()`の入力、返値、discard順は変えない。

```text
ValidatedContextOwnerReceiptV2 = opaque {
  exact_runtime_source_object, exact_world_object,
  exact_bound_context_object, exact_context_object,
  network_game_id, authenticated_player_id,
  manifest_sha256, context_sha256,
  connection_generation,
  issued_world_snapshot_object,
  issued_authority_revision,
  issued_readiness_status: PENDING_SYNC | READY,
  issuer_capability
}
```

`issued_authority_revision`は発行時provenanceであり、`PENDING_SYNC -> READY`後も同値固定を要求しない。owner/generation不一致、`INVALID`、
`EMPTY`では発行しない。PENDING_SYNC時のreceiptはcontext ownerだけを証明し、channel/disclosure/capture/materialのread権限を持たない。

### 3.2 startup順序

actual順序は、sync consumeでWorldがCURRENTになってもauthorityがPENDING_SYNCのまま、runtime sourceがfirst CURRENT bind後にcomposition gateで
待ち、queue済みの同generation `CONNECTED`をWorldがconsumeしてREADYになる。したがってcontext receiptはPENDING_SYNCで発行可能とするが、
bridge/materialはgate解放後にcurrent authorityを再読し、同owner/generationのREADYを確認して初めて許可する。future generationのsnapshotや
別lifecycle objectで代用しない。channel visibilityはその後もreceipt-bound descriptorだけから得て、role名、channel名、IDから推測しない。

## 4. owner-bound pure read

### 4.1 World portとatomicity

`WorldState`はauthority opt-in compositionへexact World/storeを閉包する`WorldAuthorityReadPortV2`を1件発行する。
`read_current()`は引数を取らず、context receiptとcurrent authorityが同owner/generationのREADYでなければ拒否する。別event loop/thread、run終了後、
FAILED/ENDEDでも拒否し、同期処理中はawait、callback、World/authority/source mutationを行わない。

開始時にworld snapshot/store、authority snapshot、network fingerprint、retained ability records/retentionをownerから取る。source解決後に再読し、
world/store identity、authority revision/hash、network fingerprint、retentionの1 fieldでも変化したら`STALE_OWNER_READ`として全stagingを破棄する。

```text
network fingerprint = {
  lifecycle, player_id, last_seq, snapshot_generation,
  connection_generation, action_generation, canonical_sha256(actions)
}

OwnerBoundInboundReadV2 = opaque {
  exact_world_snapshot_object, exact_authority_store/snapshot_objects,
  network_fingerprint, current_actions_view,
  retained_ability_records, history_retention,
  action_sidecars, ability_sidecars, resolved_source_receipts,
  owner_capability
}
```

### 4.2 `current_actions`意味の共通化

既存`WorldState.current_actions()`のpredicateをprivate pure helperへ抽出し、public methodとowner readの両方が使う。

```text
caught_up = reducer.last_applied_seq == network_snapshot.last_seq
actionsを出す条件 = caught_up
  and network lifecycle == CONNECTED
  and World freshness == CURRENT
```

owner readはさらにview/world version、world/network last seq、player IDを照合する。World/authority pairが一致してもnetworkが先行、再接続中、
action invalidate後は拒否する。

### 4.3 Private source resolver

World portだけがprivate capabilityでcurrent storeへ一括resolveする。各sidecarの`source / parent_source / phase_source`について、private storeの
`(event_id, source_path)`からexact `PrivateSourceSliceV2`を得て、次をすべて検査する。

- eventのgame/protocol/event ID/seq/type、player、connection generation、pathが`VerifiedInboundRefV2`と一致する。
- RFC 6901相当のclosed resolverで`event_object`のexact JSON pointer subtreeを再取得し、network層と同じcanonical JSON関数でbytes化する。
- 再取得bytesが`canonical_source_bytes`とbyte-equalで、そのSHA-256がprivate source hashとverified ref hashの両方に一致する。
- action primary/parentは同じenvelopeで、primaryがreceived index直下にある。
- sync phase sourceは同じstate-sync envelopeでresult indexより前にある。

path不存在、escape不正、container/index型不一致は拒否する。source bytesとevent objectはreceipt外へ出さず、
prepared material、prompt、public auditへ含めない。

## 5. Discussion capture pure readとbridge

### 5.1 capture port

`DiscussionCaptureReadPortV2.read_current()`はcaller captureを受けず、mutating `_check_owner()`も呼ばない。composition時に保存したexact
`_owner_token`と現在のthread ID / running loop identityを比較し、owner未確定・不一致を拒否する。read前後で次のclosed tupleを取り、
object identityを含めexact-equalの場合だけ`OwnedDiscussionCaptureReadV2`を返す。

```text
capture read tuple = {
  exact_bound_context_object,
  exact_current_capture_object, capture_id,
  exact_state_object, canonical_state_sha256,
  epoch, revision, fact_revision, capture_ordinal,
  staged_object_or_null, committed_object_or_null,
  dispatch_object_or_null, delivery_object_or_null,
  observation_object_or_null, last_abort_object_or_null,
  closed
}
```

current captureなし、capture/state/hash/revision不一致、closed、またはstaged/committed/dispatch/delivery/observation/abortのいずれかがnon-nullなら拒否する。
counter、owner token、state、memory、deliveryを変更しない。

### 5.2 bridge検査順

`AuthorityCaptureBridgeV2`はpublic constructorを持たない。runtime factoryがWorld port、capture port、context receiptのWorld/source/store/
bound context object identityを一度だけ照合して作る。`prepare_current()`は引数なしの同期pure readで、次を順に行う。

1. current authorityを再読し、context receiptと同owner/generationのREADYを確認する。
2. store portからexact current capture、World portからatomic inbound readを得る。
3. receiptのcontextをcurrent Worldへ再検証する。
4. captureのgame/player/context identity/hash、world version/seq、trigger day/phaseをowner readへ照合する。
5. action、ability、source、public channel、disclosureを6節どおり全検証する。
6. private prepared materialを一括構築後、capture read tupleとWorld owner fingerprintを再照合する。

失敗時に部分catalogを返さず、authority/stateを変更・retry・captureしない。

## 6. runtime材料とclosed hash

### 6.1 Action catalogとpublic channel

network/current actionsとauthority action sidecarをreceived orderで全件照合する。件数、
`(network.action_generation, received_index)`連続列、`subject_kind=ACTION_HANDLE`、generation、day/phase、sourceをexact照合する。
typed hashはconcrete dataclass型名と**全field**から作る。共通fieldに加え、`ChatAction.channel`、`VoteAction.valid_targets/target_count/allows_abstain`、
`CoDeclareAction.claimed_role_ids`、`AbilityAction.ability_id/description/valid_targets/target_count/uses_remaining`を含む。
`CoReportAction`は`UNSUPPORTED_ACTION_KIND`でfail-closedとし、別kindへ変換しない。

全actionへreceived orderで`o000...`を付ける。chat trigger `INITIAL_CHAT / PEER_CHAT`ではchat actionがexactly 1件で、actual handle/source
channelとreceipt-bound contextのexact descriptorが一致し、`is_public is True`の場合だけpublic authorityを作る。0/複数/private/descriptor
欠測・重複は全準備を拒否する。non-chat triggerのpublic authority tupleは空で、hostがchannel/action戦略を選ばない。
`SERVER_FILTERED_PUBLIC`はsymbolic recipient setであり、具体的player集合やprivate recipientを捏造しない。

### 6.2 Ability disclosure

各authority ability sidecarをretained actual `AbilityResultRecord`へ`("ability_result", order)`でexactly oneに結ぶ。concrete typed hash、closed
event type、target、result/role null条件、day/phase、live witnessまたはsync index/phase source、private sourceを検査する。
orphan/duplicate sidecar、不一致は全準備を拒否する。

chat triggerでpublic authorityが成立した場合だけ、同player、同connection、source seq `<= capture.last_applied_seq`のrecordから
`SELF_ABILITY_REPORT`候補を作る。sidecarなしretained recordはdisclosureにせず、`unproven_ability_record_identities`へ残す。将来の必須候補が
そこを要求したら`CATALOG_INCOMPLETE`とし、別recordへ置換しない。

### 6.3 exact catalog material

prepared hashへ入れるsource refはbytes/event object/capabilityを含めず、既存`VerifiedInboundRefV2`からlosslessに得る次のclosed metadataだけとする。

```text
SourceRefMaterialV2 = {
  event_id, seq, message_type, connection_generation,
  source_path, source_sha256
}

SubjectSourceBindingV2 = {
  typed_value_sha256,
  source_ref,
  parent_source_ref_or_null,
  phase_source_ref_or_null
}
```

`typed_value_sha256`はsidecar subject rootに1個だけ置く。parent/phase refへtyped hashを要求・合成せず、値を捏造しない。
`action_bindings`はreceived orderのtupleで、ID `oNNN`、concrete action型と全field、authority kind、subject source binding、
public channel authority hashまたはnullを含む。`action_catalog_sha256`はこのtupleのcanonical SHA-256である。
`disclosure_candidates`はcanonical authority hash、source identity、typed value hashの順でstable sortし、ID `dNNN`、closed authority全field、
subject source bindingを含む。sort key重複または同じsource identityの重複は拒否する。`disclosure_catalog_sha256`はこのtupleと
`unproven_ability_record_identities`のcanonical SHA-256である。

### 6.4 Prepared outputとprivacy

```text
PreparedAuthorityCaptureMaterialV2 = opaque {
  schema_version: "aiwolf.prepared-authority-capture-material.v2",
  status: "UNLEASED",
  base_capture_id, game_id, player_id, context_sha256,
  base_revision, fact_revision, trigger,
  world_version, last_applied_sequence,
  authority_revision, authority_snapshot_sha256,
  phase_identity, connection_generation, action_generation,
  world_owner_fingerprint, capture_read_fingerprint, source_retention_fingerprint,
  action_bindings, public_channel_authorities,
  disclosure_candidates, unproven_ability_record_identities,
  action_catalog_sha256, disclosure_catalog_sha256,
  prepared_material_sha256,
  hidden_owner_receipt
}
```

`prepared_material_sha256`は上記stable fieldすべてをschema順のclosed mappingとしてcanonical化したSHA-256である。除外はself hash
`prepared_material_sha256`とprocess-local private capability `hidden_owner_receipt`だけである。hidden receiptはprepareに使用したexact
World/authority/capture/source fingerprintへissuer内部で束縛し、copy/equality値では代用できない。

prepared object全体も未選択private ability dataを持つためprivate memory onlyとする。6.3〜6.4節で定義した内部closed stable projectionの
canonical bytes/hash生成だけを許可する。opaque prepared object、hidden receipt、private dataの汎用・外部・public serialization/hash、
log/prompt/public auditへの出力は禁止する。`repr`はredacted、public serializerは持たない。binding/channel/disclosure型もprivate issuerだけが作り、同値structural型を受理しない。
outputにlease ID、expiry、最終capture ID、profile/stage schema/input/projection/request/wire fieldを置かない。

## 7. lease / executable境界

prepared materialはimmutableかつUNLEASEDであり、request/wire/finalize/execution入口へ渡すと`LEASE_REQUIRED`になる。bare
`GenerationCaptureV2`へコピーしてもexecutableではない。将来のlease finalizeはsuccessor taskで設計・実装・検査する。本taskでは、owner readを
再実行した際に旧capture、次World commit、authority revision変更、source pruneをstaleとして観測できるところまでを保証する。

successor gate（T518では`NOT_RUN`）は、exact prepared objectとhidden receiptの再照合、同一CAS区間でのstate lease/final capture確定、
request factoryのopaque executable wrapper受理、prepare後prune/replacement拒否を含む。これらをT518 PASSとは数えない。

## 8. fail-closed / offline acceptance

testはactual NetworkClient/World/Reducer/DiscussionStateStoreとfake transportを使い、provider/LLM/game/Actionsは0とする。

### Positive

1. actual startup順で同generation PENDING_SYNCのcontext receiptを発行し、composition gate解放後、queue済みexact CONNECTEDをconsumeしてREADYにし、current reread後だけmaterialを作る。
2. READY ownersから同version/seq/revisionのopaque readを得て、public `current_actions()`とbridge helperが同じcaught-up/action結果を返す。
3. validator receiptとretained bytesのfull再検証を通したPUBLIC descriptor、exactly one chatから1 public authorityを作る。
4. live/sync各ability event shapeをactual record/phase/sourceへ結び、closed順序のcatalog/hashを作る。
5. prepare前後でWorld fingerprint、capture read tuple、source retentionが不変である。
6. outputがprivate immutable UNLEASEDで、public serializationできず、request/finalize/execution入口が`LEASE_REQUIRED`を返す。

### Negative

1. 同値別snapshot/dict/BoundContext/structural candidate、validator tokenのないdirect Pendingからreceipt/materialを作れない。
2. tokenだけ、manifest/context/channel hash 1-bit変異、別game/player、owner/generation不一致、INVALID/EMPTYでreceiptを発行しない。
3. PENDING_SYNC receiptでchannel/capture/materialを読めず、別generation CONNECTEDやfuture snapshotでもREADYへ昇格しない。
4. READY後も別World/store、旧capture、次World commit、別authority revision、network seq先行、reconnect/action invalidateを拒否する。
5. private slice metadata/hashが一致してもevent objectのexact path subtree bytesが異なる場合、全準備0とする。
6. capture read中のowner token/loop/thread、capture/state identity/hash、epoch/revision/fact/ordinal、outstanding tupleの各変異を拒否する。
7. action件数/index/concrete型のkind-specific 1 field/typed hash/parent・phase source/generation各変異と`CoReportAction`を拒否する。
8. chat 0/2、private/欠測/重複descriptor、action channel不一致を拒否する。
9. ability orphan/duplicate、record order、actor、event/target/result/role、phase/witness/index/source各変異を拒否する。
10. prepare後にowner readを再実行するとpruned/replaced sourceまたは旧captureをstaleとして観測する。future finalizeの拒否は`NOT_RUN`である。
11. prepareがstore capture/stage/commit/abort、World commit/invalidate、source retention変更を呼ばず、sink-null v1の値・順序・失敗分類が同値である。

K1のwire生成空間、K3のtoken/time、PF2/PF3、品質を本read成功からPASSと推論しない。欠測をNONE、silence、別actionへ変換しない。

## 9. 実装・review gate

承認後の必要read-setは、`discussion/context.py`のvalidator receipt/bind、`world/service.py`のatomic port/action helper、
`world/inbound_authority_v2.py`と`network/inbound_authority_v2.py`のprivate source resolve、`discussion/state.py`のpure capture port、新規
`discussion/authority_capture_bridge_v2.py`、`runtime.py`のopt-in composition、およびfocused testsである。`capture_v2.py`のstructural simulator、
GenerationCapture/lease simulator、brain/broker/backend/request/wire/delivery/server/protocol/game/mainは変更しない。

独立Reviewerは、R1 startup順、R2 token+full validation、R3 closed hash/private material、R4 UNLEASED境界、R5 exact adaptersを差分確認する。
承認前に実装せず、承認後もgeneration実行、lease finalize、PF2/PF3、品質、provider可否を完了扱いしない。
