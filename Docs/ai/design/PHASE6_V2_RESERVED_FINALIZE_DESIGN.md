# Phase 6 v2 PREPARING → RESERVED final capture 詳細設計

Status: APPROVED
Task: T530
Responsibility: Architect
依存: T514 capture/lease契約、T522/T525の承認済みOFFER→PREPARING所有経路

## 1. 目的と境界

本設計は、T525が実broker OFFERから `DiscussionStateStore` に発行した、所有者つき
`PREPARING` leaseを、T514の `GenerationCaptureV2` とhash DAGが確定した
`RESERVED` leaseへ一度だけ進める接続を定義する。

この単位が発行する権限は「状態と入力を予約した」というstore内の所有証拠までである。
broker `claim`、`activate`、generation request、provider、LLM、投影、送信、game actionは呼ばない。
`PF2` と `PF3` は `UNKNOWN`、budgetは `UNSET` のままとする。T526/T529等のtoken診断を
本経路のPASS根拠にしない。

通常のv1 `invoke`、通常game/Master、server schema、protocol、content、private permissionは
変更しない。v2経路は既存の明示的offline compositionにだけ接続し、positive完了後も
providerへ到達しない。

## 2. 現在の実接点と不足

### 2.1 既に存在する所有系列

T525の `PreparingLeaseOwnerReceiptV2` は次を同一identityで保持している。

- exact composition / store / arbiter / pending / driver task
- exact initial ticket / OFFER source / OFFER receipt
- exact broker session / control lane / client lease / request
- exact `DispatchDeadline`
- exact `PreparedAuthorityCaptureMaterialV2` とそのhidden owner receipt
- PREPARING発行時の `(capture, revision, fact_revision, epoch)`
- broker deadlineと `expires_at_monotonic_us`

storeの `_preparing_lease_bundle_v2` は `(PREPARING lease, exact owner receipt)` という一つの
tuple参照である。finalizeはこの参照そのものをcompare-and-swap（CAS）の期待値に使う。
同値dataclass、同値dict、同値hash、callerが構築したsnapshotは期待値にもauthorityにも
ならない。

### 2.2 実装上まだ無いもの

現在は次が未接続であり、`StructuralStateLeaseSimulatorV2.finalize_capture` で代用しては
ならない。

1. private materialのhidden owner readから、exact capture/state/world/actionsを再取得する
   product owner port
2. `GenerationCatalogV2` と実IDの対応表を作るproduct catalog compiler
3. runtimeの `PublicChannelAuthorityV2` identityを保持したまま、captureへ入れるclosedな
   data projectionを作る処理
4. profile bundle、stage schema、input、captureを同じhash DAGで構築する処理
5. storeのPREPARING bundleをRESERVED bundleへ一参照で置換するproduct CAS
6. PREPARING発行後のallocation/freshness/cancel失敗に使う、事前構築済みINVALIDATED bundle

これらは本設計の実装対象である。公開 `GenerationCaptureV2`、公開
`StateGenerationLeaseV2`、公開hashだけを受け取るfactoryは追加しない。

## 3. private interfaceと所有権

### 3.1 composition時に固定するprofile source

`OfferPreparingCompositionV2` の構築時に、次のprivate immutable
`GenerationProfileBundleV2` を一度だけ構築する。

```text
GenerationProfileBundleV2 {
  schema_version = "aiwolf.generation-profile-bundle.v2"
  generation_profile = "phase6_v2"
  system_instruction_version = PHASE6_V2_SYSTEM_INSTRUCTION_VERSION
  generation_config_fingerprint = session.identity.config_fingerprint
}
```

`PHASE6_V2_SYSTEM_INSTRUCTION_VERSION` はv2 projectorと共有する非空のversion定数とする。
本文やpromptを本taskで実装しない。`generation_config_fingerprint` は認証済みbroker READYから
得た `BackendIdentity.config_fingerprint` であり、caller文字列や
`GenerationBrokerConfig.config_fingerprint` で置き換えない。bundleとそのSHA-256を
composition、caller port、以後のowner receiptが同一objectで保持する。設定選択肢は増やさない。

### 3.2 final capture source read

`AuthorityCaptureBridgeV2` にprivate method
`_read_final_capture_source_v2(prepared_material, hidden_owner_receipt)` を追加する。戻り値
`OwnedFinalCaptureSourceV2` はmodule-private tokenでのみ作成でき、immutable、redacted repr、
pickle/public serialization禁止とする。

戻り値は次のexact objectを保持する。

- bridge、registration、context receipt、store、world、runtime source
- `OwnedDiscussionCaptureReadV2` のexact capture/state/bound context
- `OwnerBoundInboundReadV2` のexact world snapshot/current actions/authority snapshot
- `PreparedAuthorityCaptureMaterialV2` のexact action bindings、public channel authorities、
  disclosure candidates
- capture tuple、network fingerprint、source retention fingerprint
- stable material projectionと `prepared_material_sha256`

methodは `prepare_current()` と同じowner/read検査を再実行し、引数materialのhidden receiptが
同bridge発行物であり、capture read、inbound read、materialの全stable fieldとhashが一致する
場合だけ返す。hidden tupleを別moduleが展開してはならない。equal copy、foreign bridge、古い
authority successor、古いconnection/action generationは拒否する。

このreadはconsumeもstore mutationも行わない。戻り値そのものもauthorityではない。
RESERVED authorityは3.5節のstore CASが、exact source readとexact PREPARING bundleを同時に
検証した場合だけ発生する。

### 3.3 one-shot finalize入口

private同期関数を次で固定する。

```text
_finalize_preparing_owned_v2(
    exact OfferPreparingCallerPortV2,
    exact PreparingLeaseOwnerReceiptV2,
) -> (exact StateGenerationLeaseV2, exact GenerationCaptureV2,
      exact ReservedCaptureOwnerReceiptV2)
```

呼出し位置は `BrainInvocationArbiter._run_offer_preparing_offline_v2` の
`_publish_preparing_from_offer_v2` 成功直後、既存terminal resultの通知前である。関数内に
`await`、callback、Future通知、broker I/Oは無い。arbiter lockは保持しない。same event-loop task、
exact active pending、exact driver、未完了result、`cancel_requested == false`、
`current_task().cancelling() == 0` を要求する。

caller port、owner receipt、storeの現bundle、composition/ticket/source/OFFER receiptの状態が
すべて3.6節のPREPARING行と一致しなければ処理しない。二回目、foreign task、公開leaseだけ、
同値copyだけによる呼出しは拒否する。

### 3.4 allocation-safe abort bundle

PREPARINGを最初にpublishする前に、T525側でopaque immutable
`PreparingBundleIdentityV2`を一つ作る。PREPARING bundle、Preparing receipt、以下のabort
bundleはこの同一objectを保持し、同値copyを拒否する。Preparing receiptからabort bundleへは
exact参照してよいが、abort bundleからPreparing receipt/bundleへstrong back referenceを張らず、
`PreparingBundleIdentityV2`だけをpredecessor identityとして使う。これで循環を作らずにCAS対象を
一意にする。

失敗classとpending resultの対応は次のclosed enum/mapだけとする。

```text
FinalizeAbortClassV2 =
  INTERNAL_BUILD | STALE_FRESHNESS | DEADLINE_EXPIRED | CANCEL_REQUESTED

FinalizeTerminalMapV2 = opaque immutable {
  INTERNAL_BUILD:    exact terminal_candidates.internal_failure,
  STALE_FRESHNESS:   exact terminal_candidates.stale,
  DEADLINE_EXPIRED:  exact terminal_candidates.deadline_suppressed,
  CANCEL_REQUESTED:  exact terminal_candidates.preparing_complete
}

FinalizeConflictObservationV2 = private preallocated slot {
  purpose: PRECAPTURE_ABORT | RESERVED_ABORT,
  exact_composition,
  exact_expected_tuple,
  exact_observed_tuple_or_null,
  observed_shape_or_null:
    null | KNOWN_PREPARING | KNOWN_RESERVED | KNOWN_INVALIDATED | FOREIGN,
  state: EMPTY | RECORDED
}

FinalizeAbortSelectionV2 = private preallocated one-shot slot {
  exact_composition,
  exact_pending,
  exact_terminal_map,
  exact_preparing_identity: PreparingBundleIdentityV2,
  exact_preparing_receipt_weakref_or_null,
  exact_abort_bundle_weakref_or_null,
  selected_class_or_null: null | FinalizeAbortClassV2,
  exact_selected_result_or_null: null | exact terminal candidate alias,
  state:
    ALLOCATED | BOUND_EMPTY | SELECTED_UNPUBLISHED | PUBLISHED |
    DELIVERED | NOTIFICATION_UNKNOWN | RETIRED_UNSELECTED | CONFLICT_HELD
}

FinalizeNotificationObservationSlotV2 = private preallocated one-shot slot {
  purpose: ABORT_TERMINAL | RESERVED_SUCCESS,
  exact_observation: NotificationFailureObservationV2,
  exact_pending, exact_future, exact_session, exact_expected_result,
  state: EMPTY | RECORDED
}

FinalizePostcheckObservationV2 = private preallocated one-shot slot {
  exact_expected_store_tuple,
  exact_expected_lease, exact_expected_capture, exact_expected_receipt,
  exact_expected_composition, exact_expected_ticket,
  exact_expected_source, exact_expected_offer_receipt,
  exact_expected_pending, exact_expected_driver,
  exact_expected_session, exact_expected_lane, exact_expected_client_lease,
  observed_store_tuple_or_null,
  observed_lease_or_null, observed_capture_or_null, observed_receipt_or_null,
  observed_composition_or_null, observed_ticket_or_null,
  observed_source_or_null, observed_offer_receipt_or_null,
  observed_pending_or_null, observed_driver_or_null,
  observed_session_or_null, observed_lane_or_null, observed_client_lease_or_null,
  expected_composition_state, expected_slot_state,
  expected_ticket_state, expected_source_state, expected_offer_receipt_state,
  observed_composition_state_or_null, observed_slot_state_or_null,
  observed_ticket_state_or_null, observed_source_state_or_null,
  observed_offer_receipt_state_or_null,
  first_mismatch_or_null:
    null | STORE_TUPLE | LEASE | CAPTURE | RECEIPT | COMPOSITION | SLOT |
    TICKET | SOURCE | OFFER_RECEIPT | PENDING | DRIVER | SESSION | LANE | CLIENT_LEASE,
  state: EMPTY | RECORDED
}
```

`INTERNAL_BUILD` はallocation/catalog/schema/hash/typed validation例外、
`STALE_FRESHNESS` はworld/sequence/phase/action/connection/owner/session/lane/CAS前不一致、
`DEADLINE_EXPIRED` は期限境界、`CANCEL_REQUESTED` はexact pendingのcancelだけに使う。
abort CAS conflictは原因を推測できないためこのenumへ入れず、通知もせず3.6節の
`ABORT_CAS_CONFLICT_HOLD`へ閉じる。mapの4resultはticket発行前の
`OfferPreparingTerminalCandidatesV2`内objectのaliasであり、新resultや同値copyを許さない。

selection slotはterminal map、composition、pending、Preparing identityを設定して`ALLOCATED`で
先に作る。Preparing receipt/bundle完成後、そのexact objectを指すprivate weakrefをslotへ一回だけ設定して
`BOUND_EMPTY`へ進める。receipt/bundleも同じexact slotを保持し、この相互bindingが完成するまで
PREPARINGをpublishしない。失敗class判定は対応するmap entryを一回の参照代入で固定して
`SELECTED_UNPUBLISHED`へ進める。class/result組はmapどおりでなければならず、組替えや同値copyを
拒否する。abort CAS成功で`PUBLISHED`、通知成功で`DELIVERED`、通知失敗で
`NOTIFICATION_UNKNOWN`となる。CAS conflictでは通知権限へ昇格せず`CONFLICT_HELD`、RESERVED
成功またはresult通知を伴わないretireで未選択のまま`RETIRED_UNSELECTED`となる。terminal
stateから再選択しない。

selection slotのnullabilityはclosedである。`ALLOCATED`だけはreceipt/bundle weakrefs null、class/result
null、`BOUND_EMPTY`と`RETIRED_UNSELECTED`は両weakrefのderefがexact receipt/bundleかつclass/result
null、その他は全owner refs exactかつclass/result non-nullである。owner graphがactiveな間にweakref
derefがnullまたはforeignならfail-closedであり、再bindしない。これによりbundle→selection→bundleの
strong cycleを作らない。

notification slotは既存`NotificationFailureObservationV2`をexact aliasとして再利用する。
`EMPTY`ではその3fieldが全てnullである。通知がraiseした時だけ、new objectを作らず
`future_done: bool`、`exact_result_visible: exact expected result|foreign result|null`、
`session_results_member: bool`を既存規則で一回記録し`RECORDED`へ進める。partial write、二回記録、
別pending/future/session/resultでの使用を拒否する。通知成功時は`EMPTY`のまま保持する。

postcheck slotはRESERVED candidateと全expected backlinkを構築した後、CAS前に完成させる。
CAS後は上記field順にidentity/stateを読み、最初の不一致codeと全observed ref/stateを参照代入して
`RECORDED`へ一回だけ進める。値本文、repr、例外文字列、新tuple/dictを保存しない。全一致時は
`EMPTY`のままにする。slot自体のallocation失敗はCAS前`INTERNAL_BUILD`であり、RESERVEDを
publishしない。

これら4種のslotはcomposition issuer tokenだけが作成・前進できる。public constructor、serializer、
copy/deepcopy/pickle、reprによるfield露出は無く、foreign slotや別flow slotを受け付けない。
conflict slotの`EMPTY`はobserved tuple/shapeがともにnull、`RECORDED`はともにnon-nullである。
postcheck slotの`EMPTY`は全observed fieldとfirst mismatchがnull、`RECORDED`はfirst mismatchが
non-nullで、各observed fieldは実際に読めたexact ref/state、欠落時だけnullである。途中までの
記録を`RECORDED`とせず、全field参照代入後にstateを最後に進める。

PREPARING publish前に次のclosed shapeを全て構築する。

以下のidentity/reference fieldは構築後不変である。`state`とpreallocated observation slotだけを
exact issuerが表の前進遷移で参照代入でき、callerはmutateできない。

```text
PreCaptureInvalidatedOwnerReceiptV2 = opaque immutable {
  schema_version = "aiwolf.pre-capture-invalidated-owner-receipt.v2",
  predecessor_identity: exact PreparingBundleIdentityV2,
  exact composition/store/ticket/pending/driver/source/OFFER receipt,
  exact session/lane/client lease/request/deadline,
  exact PREPARING lease,
  exact revision-1 INVALIDATED lease,
  terminal_map: exact FinalizeTerminalMapV2,
  abort_selection: exact FinalizeAbortSelectionV2,
  abort_notification_observation: exact FinalizeNotificationObservationSlotV2,
  state: ARMED | PUBLISHED | RETIRED | CONFLICT_HELD
}

PreCaptureFinalizeAbortBundleV2 = opaque immutable {
  predecessor_identity: exact PreparingBundleIdentityV2,
  invalidated_lease: exact receipt.exact revision-1 INVALIDATED lease,
  invalidated_receipt: exact PreCaptureInvalidatedOwnerReceiptV2,
  terminal_map: exact same FinalizeTerminalMapV2,
  abort_selection: exact same FinalizeAbortSelectionV2,
  conflict_observation: exact preallocated FinalizeConflictObservationV2,
  abort_notification_observation: exact same FinalizeNotificationObservationSlotV2,
  state: ARMED | PUBLISHED | RETIRED | CONFLICT_HELD
}
```

revision 1 INVALIDATED leaseは`capture_id=None`、capture/hash/schema tupleはnull/empty、reasonは
固定`FINALIZE_ABORT`である。PREPARING publish後の失敗は選択したclass/resultをexact
`FinalizeAbortSelectionV2`へ固定してから、storeのexact PREPARING tupleをこのbundleへ一参照で
CASする。CAS成功後は既存
result aliasの参照代入と通知だけを行い、result、receipt、error textを構築しない。

state遷移は`ARMED -> PUBLISHED`（abort CAS成功）、`ARMED -> RETIRED`（RESERVED CAS成功で
pre-capture abort不使用確定）、`ARMED -> CONFLICT_HELD`（CAS mismatch）だけである。
`PUBLISHED/RETIRED/CONFLICT_HELD`から戻さない。

strong refsは`ARMED`で上記owner graph一組を保持する。terminal stateでも後続recoveryが
未設計の本taskでは同じ一組を保持し、secret-free化やclearを偽装しない。一active flowにつき
一組だけで、次flowをblockするため増殖しない。process owner graph teardown時だけ一括解放する。
例外文字列やprivate dataをreceiptへ保存しない。

### 3.5 RESERVED owner receiptとstore bundle

`ReservedCaptureOwnerReceiptV2` はmodule-private、immutable、redactedで、次をexactに保持する。

- composition/store/bridge/profile bundle
- predecessor PREPARING bundle identity
- initial ticket/source/OFFER receipt/session/lane/client lease/request/deadline
- final source readとruntime public channel authority objects
- catalog binding、option binding、stage schemas、hash DAG
- exact `GenerationCaptureV2` とRESERVED lease
- RESERVEDからINVALIDATEDへ進める事前構築済みrevision 2 abort bundle
- exact `FinalizeNotificationObservationSlotV2(purpose=RESERVED_SUCCESS)`
- exact preallocated `FinalizePostcheckObservationV2`

構築時にopaque immutable `ReservedBundleIdentityV2`を一つ作り、RESERVED tuple、Reserved
receipt、revision 2 abort bundleで共有する。abort bundleはReserved receipt/bundleをstrong
back referenceせず、このidentityとexact lease/captureをpredecessorとして束縛する。

```text
ReservedInvalidatedOwnerReceiptV2 = opaque immutable {
  schema_version = "aiwolf.reserved-invalidated-owner-receipt.v2",
  predecessor_identity: exact ReservedBundleIdentityV2,
  exact composition/store/ticket/pending/driver/source/OFFER receipt,
  exact session/lane/client lease/request/deadline,
  exact GenerationCaptureV2,
  exact revision-1 RESERVED lease,
  exact revision-2 INVALIDATED lease,
  exact ReservedCaptureOwnerReceiptV2.owner_identity,
  reserved_notification_observation: exact FinalizeNotificationObservationSlotV2,
  postcheck_observation: exact FinalizePostcheckObservationV2,
  state: ARMED | PUBLISHED | RETIRED | CONFLICT_HELD
}

ReservedFinalizeAbortBundleV2 = opaque immutable {
  predecessor_identity: exact ReservedBundleIdentityV2,
  exact capture,
  invalidated_lease: exact receipt.exact revision-2 INVALIDATED lease,
  invalidated_receipt: exact ReservedInvalidatedOwnerReceiptV2,
  conflict_observation: exact preallocated FinalizeConflictObservationV2,
  reserved_notification_observation: exact same FinalizeNotificationObservationSlotV2,
  postcheck_observation: exact same FinalizePostcheckObservationV2,
  state: ARMED | PUBLISHED | RETIRED | CONFLICT_HELD
}
```

Reserved receiptは`owner_identity`とabort bundleを保持するが、abort receiptはReserved receiptへ
back referenceしない。revision 2はRESERVEDのcapture/hash/schema tupleをbyte-exactに保持し、
status/revision/reasonだけを`INVALIDATED`/2/`RESERVED_RETIRE`へ変える。abort bundleのnullable
fieldは無い。PreCapture/Reserved双方で表外null、foreign identity、同値別lease/capture/receiptを
拒否する。各stateのstrong refは3.4節と同様にowner graph teardownまで一組保持する。
Reserved abortの遷移は、RESERVED CAS成功時に`ARMED`、revision 2 CAS成功時に`PUBLISHED`、
CAS mismatch時に`CONFLICT_HELD`である。本scopeでは正常terminal通知後も`ARMED`を維持し、
`RETIRED`は後続の承認済みrecoveryがowner graphをteardownする時だけ使う。未publish local
candidateはowner graphへattachせず破棄できる。

公開serialize、log、prompt、audit本文への出力は禁止する。公開可能なのは各canonical SHA-256、
状態、個数、固定error codeだけである。

storeのactive v2 bundleはclosed unionとする。

```text
PREPARING:  (PREPARING lease, PreparingLeaseOwnerReceiptV2)
RESERVED:   (RESERVED lease, GenerationCaptureV2, ReservedCaptureOwnerReceiptV2)
INVALIDATED_FROM_PREPARING:
            (INVALIDATED lease, PreCaptureInvalidatedOwnerReceiptV2)
INVALIDATED_FROM_RESERVED:
            (INVALIDATED lease, GenerationCaptureV2,
             ReservedInvalidatedOwnerReceiptV2)
```

内部slotは一つのtuple参照であり、`lease`、`capture`、stage-specific receiptのpropertiesは
毎回この一参照を読み、型とstatusが一致する時だけ値を返す。別々のslotを順にpublishしない。

### 3.6 owner/state matrix

表が全状態の正本である。`lane held` はexact client leaseが未claim、未release、未retire、
`callers == 0`、sessionのclaimed/activated IDがnullであることを表す。

| published state | store tuple / lease / capture / receipt | composition / slot | ticket / source / OFFER receipt | pending / driver / notification | broker | strong refs / retry |
|---|---|---|---|---|---|---|
| PREPARING | exact tuple、rev0 `PREPARING`、capture/hashなし、exact Preparing | `PREPARING` / `PREPARING_HELD` | `CONSUMED_PREPARING` / `PREPARING` / `CONSUMED_PREPARING` | exact pending、exact current driver、unfinished | lane held | Preparing owner graph＋両abort candidate保持。retry/new flow禁止 |
| LOCAL_BUILD | published tupleはPREPARINGと同一 | 同一 | 同一 | 同一 | lane held、I/O 0 | local candidateだけ追加。失敗時破棄しabortへ |
| RESERVED | exact tuple、rev1 `RESERVED`、exact capture/Reserved receipt | `RESERVED` / `RESERVED_HELD` | `CONSUMED_RESERVED` / `RESERVED` / `CONSUMED_RESERVED` | terminal通知前はexact pending/driver、unfinished | lane held | Reserved owner graph＋abort保持。finalize retry禁止 |
| RESERVED_TERMINAL_DELIVERED | 直前と同一exact RESERVED tuple | `RESERVED` / `RESERVED_HELD` | RESERVED行と同一 | futureにexact `preparing_complete`、既存finally後pending/driver active slot null | lane held | Reserved owner graph保持。retry/new flow禁止 |
| INVALIDATED_NOTIFY_PENDING | PREPARINGからexact rev1 INVALIDATED tuple、captureなし、exact PreCaptureInvalidated | `FINALIZE_FAILED_HELD` / `INVALIDATED_HELD` | `CONSUMED_INVALIDATED` / `INVALIDATED_HELD` / `CONSUMED_INVALIDATED` | exact pending/driver、selected prebuilt result、通知未開始 | lane held | class/result/owner graph保持。new flow禁止 |
| INVALIDATED_TERMINAL_DELIVERED | 直前と同一tuple | `FINALIZE_FAILED_HELD` / `INVALIDATED_HELD` | 直前と同一 | futureにexact selected result、既存finally後driver null | lane held | owner graph保持。retry/new flow禁止 |
| INVALIDATED_NOTIFICATION_UNKNOWN | 直前と同一tuple | `FINALIZE_NOTIFICATION_UNKNOWN` / `INVALIDATED_HELD` | 直前と同一 | exact pending/driverとselected result、future/wakeup shape未証明 | lane held | notification observationを保持。修復・retry/new flow禁止 |
| PREPARING_RETIRED | PREPARINGからexact rev1 INVALIDATED tuple、captureなし、exact PreCaptureInvalidated | lifecycle `RETIRED`、`FINALIZE_RETIRED_HELD` / `INVALIDATED_HELD` | `RETIRED_HELD` / `RETIRED_HELD` / `RETIRED_HELD` | 最後のexact pending/driver/resultを保持、通知0 | lane held | owner graph保持。retry/new flow禁止 |
| RESERVED_NOTIFICATION_UNKNOWN | exact RESERVED tupleを維持 | `RESERVED_NOTIFICATION_UNKNOWN` / `RESERVED_HELD` | RESERVED行と同一 | exact pending/driverとprebuilt `preparing_complete`、future/wakeup shape未証明 | lane held | Reserved owner graph保持。rollback・retry/new flow禁止 |
| RESERVED_POSTCHECK_UNKNOWN | CAS済みexact RESERVED tuple。component backlinkの一つ以上が不一致 | `RESERVED_POSTCHECK_UNKNOWN` / `RESERVED_HELD` | 観測値を保持し新規mutationなし | exact pending/driver、通知0 | lane held | expected/observed refsを保持。abort・retry/new flow禁止 |
| ABORT_CAS_CONFLICT_HOLD | storeは現tupleを上書きせず、prebuilt `FinalizeConflictObservationV2`だけがexact expected/observed tupleとclosed shapeを記録 | expectedがPREPARINGなら`FINALIZE_CONFLICT_HELD` / `CONFLICT_HELD`、RESERVEDなら`RESERVED_POSTCHECK_UNKNOWN` / `RESERVED_HELD` | expected側の直前正本行とexact同一、追加遷移0 | expected側のexact pending/driver、result選択/通知0 | lane held | expected/observed/abort refs保持。第2CAS・retry/new flow禁止 |
| RESERVED_RETIRED | exact rev2 INVALIDATED tuple、exact capture/ReservedInvalidated receipt | lifecycle `RETIRED`、`FINALIZE_RETIRED_HELD` / `INVALIDATED_HELD` | `RETIRED_HELD` / `RETIRED_HELD` / `RETIRED_HELD` | 最後のexact pending/driver/resultを保持、新通知0 | lane held | Reserved owner graph保持。retry/new flow禁止 |

補助slotのstate/nullabilityは次表を正本とする。`pre`/`reserved`はそれぞれinvalid receiptとabort
bundleが同じstateであることを表す。`conflict`、`abort notify`、`reserved notify`、`postcheck`は
各exact slotのstateであり、`null`はそのcandidateがまだowner graphへattachされていないことだけを
表す。

| published state | pre receipt/bundle | reserved receipt/bundle | abort selection | conflict | abort notify | reserved notify | postcheck |
|---|---|---|---|---|---|---|---|
| PREPARING | `ARMED` | null | `BOUND_EMPTY` | `EMPTY` | `EMPTY` | null | null |
| LOCAL_BUILD（Reserved candidate完成前） | `ARMED` | null | `BOUND_EMPTY` | `EMPTY` | `EMPTY` | null | null |
| LOCAL_BUILD（Reserved candidate完成済み） | `ARMED` | local `ARMED`、未attach | `BOUND_EMPTY` | pre/reservedとも`EMPTY` | `EMPTY` | `EMPTY` | `EMPTY` |
| RESERVED | `RETIRED` | `ARMED` | `RETIRED_UNSELECTED` | pre/reservedとも`EMPTY` | `EMPTY` | `EMPTY` | `EMPTY` |
| RESERVED_TERMINAL_DELIVERED | `RETIRED` | `ARMED` | `RETIRED_UNSELECTED` | `EMPTY` | `EMPTY` | `EMPTY` | `EMPTY` |
| INVALIDATED_NOTIFY_PENDING | `PUBLISHED` | null | `PUBLISHED` | `EMPTY` | `EMPTY` | null | null |
| INVALIDATED_TERMINAL_DELIVERED | `PUBLISHED` | null | `DELIVERED` | `EMPTY` | `EMPTY` | null | null |
| INVALIDATED_NOTIFICATION_UNKNOWN | `PUBLISHED` | null | `NOTIFICATION_UNKNOWN` | `EMPTY` | `RECORDED` | null | null |
| PREPARING_RETIRED | `PUBLISHED` | null | `RETIRED_UNSELECTED` | `EMPTY` | `EMPTY` | null | null |
| RESERVED_NOTIFICATION_UNKNOWN | `RETIRED` | `ARMED` | `RETIRED_UNSELECTED` | `EMPTY` | `EMPTY` | `RECORDED` | `EMPTY` |
| RESERVED_POSTCHECK_UNKNOWN | `RETIRED` | `ARMED` | `RETIRED_UNSELECTED` | `EMPTY` | `EMPTY` | `EMPTY` | `RECORDED` |
| ABORT_CAS_CONFLICT_HOLD（PREPARING） | `CONFLICT_HELD` | null | `CONFLICT_HELD` | pre=`RECORDED` | `EMPTY` | null | null |
| ABORT_CAS_CONFLICT_HOLD（RESERVED） | `RETIRED` | `CONFLICT_HELD` | `RETIRED_UNSELECTED` | reserved=`RECORDED` | `EMPTY` | `EMPTY` | `EMPTY` |
| RESERVED_RETIRED | `RETIRED` | `PUBLISHED` | `RETIRED_UNSELECTED` | `EMPTY` | `EMPTY` | `EMPTY` | `EMPTY` |

表外のnull、state逆行、selectionとclass/resultの組替え、notification/postcheck/conflict slot間の
代用を拒否する。`PUBLISHED`、`RETIRED`、`CONFLICT_HELD`から別の成功行へ推測downgradeしない。

LOCAL_BUILDは公開stateではない。正常RESERVED後の既存offline terminal通知成功時はarbiterの
active pending/driverを既存手順で解放してよいが、store tupleとcompositionの
`RESERVED_HELD` は残す。`Future.set_result`の失敗はRESERVEDを巻き戻さず、
`RESERVED_NOTIFICATION_UNKNOWN`へ閉じる。semantic publish後のbacklink不一致は通知前の
`RESERVED_POSTCHECK_UNKNOWN`であり、notification unknownと混同しない。

全行でstore tupleは一参照であり、lease/capture/receipt propertyは同じtupleから読む。
component backlinkも表のexact objectへ一致しなければならない。validatorは値から別行を推測、
repair、downgradeしない。各行についてtuple element、lease revision/status、capture identity、
receipt identity、composition/slot、ticket/source/OFFER state、pending/driver、laneの一つだけを
変えたcaseを拒否する。

stop/retireとの競合は次表だけを許す。同一event-loop taskのno-await CAS区間ではinterleaveしない。

| 最初に成功したCAS | loserの観測 | revision / loser処理 |
|---|---|---|
| finalize PREPARING→RESERVED | retireはexact RESERVEDを観測 | retireがReserved abortを一回だけCASし`RESERVED_RETIRED`へ。finalize post-checkがrev2を観測した場合は同じ正本行を受理し、第2CAS/通知をしない |
| failure abort PREPARING→INVALIDATED | retireはexact rev1 INVALIDATEDを観測 | retireはCASせず既存abort行をlifecycle RETIRED heldへ閉じる。revision 1のまま |
| retire PREPARING→INVALIDATED | finalize/abortはexact `PREPARING_RETIRED`を観測 | loserはその正本行を受理し、通知/result選択/第2CASを行わない |
| foreign/破損tupleが先行 | winnerを認定できない | storeを変更せず`ABORT_CAS_CONFLICT_HOLD`。revisionを推測しない |

CAS loserは観測したexact current tupleとexpected tupleを保持するだけで、現owner receiptのstateや
brokerを変更しない。既にINVALIDATEDのtupleへ同じabortを再適用してrevisionを増やさない。

本taskはbroker cleanup/releaseを行わない。従ってINVALIDATEDやretireを `RELEASED` と称さず、
active bundleを消去せず、次のinvocationを許可しない。cleanup/recovery proofを伴う
`RELEASED` は後続scopeである。

## 4. catalog compiler

### 4.1 入力と一般規則

compilerは3.2節のexact sourceだけを受け、caller dictや任意の
`GenerationCatalogV2` を受けない。生成するprivate immutable `FinalCatalogBindingV2` は、
`GenerationCatalogV2`、4.3節のclosed binding tuple、各bindingが参照するexact opaque objectを
同時に保持する。binding projectionはprivateであり、実player/role/ability ID、source path、
typed valueをpublic result、log、prompt、audit、reprへ出さない。

共通projectionは次で固定する。fieldの記載順がtuple recordのfield順で、値の型は
`str`、non-negative `int`、`bool`、`null`、またはここで定義したtuple/recordだけである。
canonical JSONはkey sortを行うが、tuple順は変えない。

```text
EvidenceRefProjectionV2 = {
  record_kind: enum.value, order: int, visibility: enum.value
}

SourceRefProjectionV2 = {
  event_id: str, seq: int, message_type: str,
  connection_generation: int, source_path: str, source_sha256: sha256
}

SubjectSourceBindingProjectionV2 = {
  typed_value_sha256: sha256,
  source_ref: SourceRefProjectionV2,
  parent_source_ref_or_null: SourceRefProjectionV2|null,
  phase_source_ref_or_null: SourceRefProjectionV2|null
}

SourceRootWitnessV2 = {
  root_kind: WORLD_PLAYERS | CAPTURE_EVIDENCE | DISCUSSION_STATE |
             PREPARED_ACTION | PREPARED_DISCLOSURE | VALIDATED_CONTEXT,
  root_sha256: sha256,
  world_version: int|null, last_applied_sequence: int|null,
  capture_revision: int|null, fact_revision: int|null,
  authority_revision: int|null
}
```

root kind別nullabilityは次を正本とする。`WORLD_PLAYERS`はworld/seqだけ、
`CAPTURE_EVIDENCE`と`DISCUSSION_STATE`はworld/seq/capture revision/fact revision、
`PREPARED_ACTION`と`PREPARED_DISCLOSURE`はその4値とauthority revision、
`VALIDATED_CONTEXT`は5値全てnullである。表外のnull/non-nullを許さない。

既存opaque projectionの展開形も固定する。

```text
PublicChannelAuthorityProjectionV2 = {
  schema_version, option_id, channel_id, audience, recipient_scope,
  context_sha256, action_source_sha256, connection_generation,
  action_generation, phase_identity, authority_revision_sha256
}

ActionBindingProjectionV2 = {
  schema_version, option_id, action_kind, action_generation,
  connection_generation, phase_identity, action_sha256,
  source: SubjectSourceBindingProjectionV2,
  channel_authority: PublicChannelAuthorityProjectionV2|null
}

ProvenanceSidecarProjectionV2 = {
  schema_version, subject_kind,
  record_identity_or_null, action_identity_or_null,
  typed_value_sha256,
  source: AuthenticatedInboundRefV2 plain projection,
  phase_attribution_or_null: existing PhaseAttributionV2 plain projection,
  action_context_or_null: existing ActionContextRefV2 plain projection
}

DisclosureAuthorityProjectionV2 = {
  schema_version, authority_kind,
  source: ProvenanceSidecarProjectionV2,
  evidence_ref: EvidenceRefProjectionV2,
  actor_player_id, event_type, target_player_id, result_id,
  revealed_role_id, read_visibility, disclosure_audience,
  channel_authority_sha256, world_version, fact_revision
}

DisclosureCandidateProjectionV2 = {
  candidate_id,
  authority: DisclosureAuthorityProjectionV2,
  authority_sha256
}
```

`AuthenticatedInboundRefV2`、`PhaseAttributionV2`、`ActionContextRefV2`はT514/T516で承認済みの
全field順とnullabilityをそのまま使い、省略した縮約形を新設しない。

sidecar lookupは必ず次のclosed三分岐を返す。

```text
ProvenanceLookupOutcomeV2 =
  ABSENT | EXACT_ONE_MATCH | PRESENT_INVALID_OR_MULTIPLE
```

対象identityに該当するsidecarが0件だけを`ABSENT`とする。1件あり、subject kind/identity、
typed value hash、source path/hash、parent/phase ref、connection/authority revisionの全fieldがexpectedと
一致する時だけ`EXACT_ONE_MATCH`である。候補が1件でも存在していずれかが不一致、または候補が
複数なら`PRESENT_INVALID_OR_MULTIPLE`であり、`ABSENT`へ縮退させない。

`SourceRefProjectionV2`は`EXACT_ONE_MATCH`でactual sidecarからlosslessに作る。fact/reply/claimは
`ABSENT`の時だけsource refを`null`とし、exact snapshot object＋index＋
`SourceRootWitnessV2`へfallbackできる。action/role/ability/disclosureはprepared materialが既に
sidecar provenanceを持つため`EXACT_ONE_MATCH`を必須とし、`ABSENT`もrejectする。replyは参照先
factと同じlookup outcome、role/abilityは参照先actionと同じoutcomeをexact aliasで保持する。
player/opinionはdirect sidecar対象ではなく、最初からworld/state root＋indexだけを使う。
`PRESENT_INVALID_OR_MULTIPLE`は全bindingで`CATALOG_UNREPRESENTABLE`でありroot fallback禁止である。
存在しないpath/hashを合成しない。
root SHAは次のclosed projectionから作る。

- `WORLD_PLAYERS`: exact `world_snapshot.players` の全`PlayerView`を元順の
  `{player_id, display_name, alive, death_or_null}`へ写したtuple。exact world snapshot identityも
  Reserved receiptに保持する。
- `CAPTURE_EVIDENCE`: exact base captureの全`ImportantEvent` plain projection、
  `base_capture_id/base_revision/fact_revision`。
- `DISCUSSION_STATE`: exact state snapshot全体の既存`state_sha256`と同値なplain projection。
- `PREPARED_ACTION`: exact prepared materialの全`ActionOptionBindingV2._projection()`、
  `action_catalog_sha256/prepared_material_sha256/authority_snapshot_sha256`。
- `PREPARED_DISCLOSURE`: exact materialの全`DisclosureCandidateV2._projection()`、
  `disclosure_catalog_sha256/prepared_material_sha256`。
- `VALIDATED_CONTEXT`: exact bound contextの既存`context_sha256`と、そのfull validation receiptに
  束縛されたabilities projection。

exact opaque snapshot/authority/binding objectはReserved receiptに保持し、hashへは上記closed
projectionだけを入れる。同値projectionを持つforeign opaque objectはowner identity検査で拒否する。
全tupleは下記の順序で生成し、重複、上限超過、参照不能、型不一致は
`CATALOG_UNREPRESENTABLE`として`INTERNAL_BUILD` abortへ進む。

- player: `world.players` を `player_id` 昇順にし `p000` から付番。最大32、ID一意、selfを
  ちょうど一つ含む。
- peer: player tupleからselfを除いた順序。
- fact: `capture.evidence` の末尾48件を元のchronological順のまま `f000` から付番。
- reply: `PEER_CHAT` のみ `trigger.source` と一致するfactが必要で `r000` 一件。
  `INITIAL_CHAT` は空。他triggerも空。
- observed claim: `state.claims` のcanonical順の先頭32件を `k000` から付番する。
- utterance claim: 現在は承認済みcanonical sourceが無いため空。推測したclaim候補を追加しない。
- opinion basis: 各 `state.assessments` の順にSUSPICION、CREDIBILITYの順で `u000` から付番。
  priorは対応score、allowed currentは `{0,25,50,75,100}` からpriorを除いた順序、
  prior factsはassessment evidenceのうちfact catalogに存在するものをfact順で使う。
  priorが5値外なら全体をrejectする。
- disclosure: exact prepared materialの `DisclosureCandidateV2` 順と `dNNN` をそのまま使う。

末尾48/先頭32という選択は`SelectionWindowV2 {source_count, selected_start,
selected_count, limit}`としてbinding projectionへ含める。factsは
`selected_start=max(0, source_count-48)`、claimsは`selected_start=0`である。切り捨てを
完全性と称さない。

### 4.2 action option

prepared materialの `action_bindings` とfinal sourceのexact `current_actions` は同じindex、
同じ `oNNN`、同じtyped hashで1:1でなければならない。triggerに対応するactionだけを
generation optionsへ入れるが、option catalogは全受信actionのbinding/hashを保持する。

- chat: `INITIAL_CHAT` / `PEER_CHAT` ではexactly one `ChatAction` とexactly one runtime
  public channel authorityが必要。generation optionには入れず、capture recipientへ入れる。
- vote: `PRE_VOTE` は一件以上の `VoteAction` が必要。各actionは `target_count == 1` を要求し、
  valid targetをplayer mappingで置換して `VoteOptionV2` とする。`allows_abstain` はwire値。
- CO: `CO_OPPORTUNITY` は一件以上の `CoDeclareAction` が必要。各role IDをaction順、role順で
  globally uniqueな `qNNN` にし、`CoOptionV2` と実role ID bindingを同時に作る。
- ability: `ABILITY` は一件以上の `AbilityAction` が必要。`ability_id` とcontext abilityが
  ちょうど一件一致し、target count、valid targetsが表現可能でなければならない。
  `allows_none` は既存contextの `no_selection == "skip"` の時だけtrueとし、`random` はfalse。
  その他の値は推測せずrejectする。
- `CoReportAction` は既存bridgeどおりunsupportedである。

targetやroleの実IDはprivate bindingにのみ保持し、公開safe reportには出さない。

### 4.3 closed binding records

各recordのtuple上限は生成catalogの上限に合わせ、player 32、reply 1、fact 48、claim 32、
opinion 64、action/vote/CO/ability option各32、disclosure 16、roleは全CO action内合計32、
validated context abilityは既存`MAX_ABILITIES=16`とする。action binding自体も本compilerでは32を
上限とし、上限を超えた時に黙って追加切捨てせずrejectする。

```text
PlayerCatalogBindingV2 = {
  short_id, sorted_player_index, source_player_index,
  real_player_id, display_name, alive, death_projection_or_null,
  world_root: SourceRootWitnessV2
}

FactCatalogBindingV2 = {
  short_id, capture_evidence_index,
  important_event_projection, important_event_sha256,
  evidence_ref: EvidenceRefProjectionV2,
  provenance_lookup_outcome: ABSENT|EXACT_ONE_MATCH,
  source_ref_or_null: SourceRefProjectionV2|null,
  capture_root: SourceRootWitnessV2
}

ReplyCatalogBindingV2 = {
  short_id, fact_short_id, capture_evidence_index,
  evidence_ref: EvidenceRefProjectionV2,
  provenance_lookup_outcome: ABSENT|EXACT_ONE_MATCH,
  source_ref_or_null: SourceRefProjectionV2|null,
  capture_root: SourceRootWitnessV2
}

ObservedClaimCatalogBindingV2 = {
  short_id, state_claim_index,
  claim_assessment_projection, claim_assessment_sha256,
  claim_ref: EvidenceRefProjectionV2,
  provenance_lookup_outcome: ABSENT|EXACT_ONE_MATCH,
  source_ref_or_null: SourceRefProjectionV2|null,
  state_root: SourceRootWitnessV2
}

OpinionCatalogBindingV2 = {
  basis_id, state_assessment_index, subject_player_short_id,
  dimension: SUSPICION|CREDIBILITY,
  prior, allowed_current, prior_fact_ids,
  player_binding_sha256, player_assessment_projection,
  player_assessment_sha256, state_root: SourceRootWitnessV2
}
```

`important_event_projection`は`ImportantEvent`の全field、
`claim_assessment_projection`は`ClaimAssessment`の全field、
`player_assessment_projection`は`PlayerAssessment`の全fieldをplain化し、各`EvidenceRef`は
`EvidenceRefProjectionV2`で表す。fact/reply/claimの`source_ref_or_null`は上記lookup outcomeを
binding内にも保存し、`ABSENT`時だけnull＋capture/state root、`EXACT_ONE_MATCH`時だけactual refを
使う。`PRESENT_INVALID_OR_MULTIPLE`はbindingを作らない。

```text
ActionOptionCatalogBindingV2 = {
  option_id, action_received_index, action_kind,
  action_generation, connection_generation, phase_identity,
  typed_action_sha256,
  provenance_lookup_outcome = EXACT_ONE_MATCH,
  action_binding_projection: exact ActionBindingProjectionV2,
  action_source_binding: exact SubjectSourceBindingProjectionV2,
  channel_authority_projection_or_null,
  prepared_action_root: SourceRootWitnessV2
}

RoleOptionBindingV2 = {
  role_short_id, option_id, action_received_index, role_index,
  real_role_id, typed_action_sha256,
  provenance_lookup_outcome = EXACT_ONE_MATCH,
  action_source_binding: SubjectSourceBindingProjectionV2,
  prepared_action_root: SourceRootWitnessV2
}

AbilityOptionBindingV2 = {
  option_id, action_received_index, ability_context_index,
  real_ability_id, context_ability_projection, context_ability_sha256,
  typed_action_sha256, target_count,
  real_valid_target_ids, valid_target_short_ids, allows_none,
  provenance_lookup_outcome = EXACT_ONE_MATCH,
  action_source_binding: SubjectSourceBindingProjectionV2,
  prepared_action_root: SourceRootWitnessV2,
  context_root: SourceRootWitnessV2
}

DisclosureCatalogBindingV2 = {
  disclose_id, disclosure_index,
  candidate_projection: exact DisclosureCandidateProjectionV2,
  disclosure_authority_sha256,
  provenance_lookup_outcome = EXACT_ONE_MATCH,
  authority_source_binding_projection: exact ProvenanceSidecarProjectionV2,
  prepared_disclosure_root: SourceRootWitnessV2
}
```

`ActionOptionCatalogBindingV2.typed_action_sha256`は既存bindingの`action_sha256`とbyte-equal、
source bindingは既存`ActionOptionBindingV2.source` projectionからlosslessに作る。
`channel_authority_projection_or_null`も既存binding内exact authorityの既存`_projection()`だけを
使う。parent/phase refが無い場合だけ対応fieldをnullとし、存在するrefを落とさない。roleはexact
`CoDeclareAction.claimed_role_ids`のaction内index、abilityはexact
`DiscussionAbilityContext`全fieldとcontext内indexを保持する。disclosureは既存candidate
projectionをそのままclosed recordへ埋め、そのauthority内`source` projectionもlosslessに
保持し、別のauthority hashを作らない。

tuple順はplayer=`short_id`、fact=`capture_evidence_index`、reply=`short_id`、claim=
`state_claim_index`、opinion=`state_assessment_index`の後にSUSPICION/CREDIBILITY、action=
`action_received_index`、role=`action_received_index,role_index`、ability=`action_received_index`、
disclosure=`disclosure_index`である。入力順を並べ替えて同じIDを再利用しない。

### 4.4 catalog hash

次のclosed projectionをcanonical JSON（UTF-8、key sort、空白なし、NaN禁止）でSHA-256する。

```text
CatalogBindingProjectionV2 {
  schema_version = "aiwolf.final-catalog-binding.v2"
  generation_catalog = plain GenerationCatalogV2,
  player_window, fact_window, observed_claim_window,
  player_entries: tuple[PlayerCatalogBindingV2],
  reply_entries: tuple[ReplyCatalogBindingV2],
  fact_entries: tuple[FactCatalogBindingV2],
  observed_claim_entries: tuple[ObservedClaimCatalogBindingV2],
  utterance_claim_entries = []
  opinion_entries: tuple[OpinionCatalogBindingV2]
}
```

これを `catalog_sha256` とする。option側は次を同じ規則でhashする。

```text
OptionCatalogProjectionV2 {
  schema_version = "aiwolf.final-option-catalog.v2"
  action_catalog_sha256 = prepared material value
  disclosure_catalog_sha256 = prepared material value
  all_action_bindings: tuple[ActionOptionCatalogBindingV2]
  selected_vote_co_ability_options = plain generated option tuples
  role_id_bindings: tuple[RoleOptionBindingV2]
  ability_id_bindings: tuple[AbilityOptionBindingV2]
  disclosure_bindings: tuple[DisclosureCatalogBindingV2]
}
```

これを `option_catalog_sha256` とする。private opaque object自体、hidden receipt、runtime
capability、raw private source bytesはcanonicalizeしない。各runtime authorityは既存closed
projectionだけを入れる。record fieldの追加、省略、nullable化、同値別opaque objectへの置換、
tuple reorder、window境界変更は全てhash/owner mismatchで拒否する。

## 5. hash DAGとfinal capture

### 5.1 runtime channel projectionとrecipient proof

runtime `PublicChannelAuthorityV2` のexact objectはReserved receiptに保持する。captureの
`channel_authorities` には、その `_projection()` から構築した
`StructuralPublicChannelCandidateV2` をdata projectionとして入れてよい。このcopyは
authorityではなく、単独ではclaim、send、discloseを許可しない。

`recipient_proof_sha256_v2(trigger.kind, channel projections)` を使用する。RESERVED CASでは
各projectionがReserved receipt内のexact runtime authority projectionとbyte-equalであることを
再検査する。chat以外は両tupleが空である。

### 5.2 stage schema

triggerからstage tupleを固定する。

| trigger | stage tuple |
|---|---|
| `INITIAL_CHAT`, `PEER_CHAT` | `chat_plan`, `message` |
| `PRE_VOTE` | `pre_vote` |
| `CO_OPPORTUNITY` | `co_opportunity` |
| `ABILITY` | `ability` |

各stageを同一 `GenerationCatalogV2` で `build_generation_v2_schema` に渡し、schema objectと
`StageSchemaHashV2(stage, canonical_sha256_v2(schema))` を同時に保持する。stage順、schema
object、hashの一つでも変わればcaptureを再利用しない。

### 5.3 profile/input/capture hash

profile bundle SHAは3.1節のclosed projectionのcanonical SHA-256である。input SHAは次を
canonical SHA-256する。

```text
GenerationInputBaseV2 {
  schema_version = "aiwolf.generation-input-base.v2"
  base_capture_id
  game_id
  player_id
  context_sha256
  state_sha256
  base_revision
  fact_revision
  world_version
  last_applied_sequence
  phase_identity
  action_generation
  connection_generation
  trigger
  prepared_material_sha256
  catalog_sha256
  option_catalog_sha256
  recipient_proof_sha256
  profile_bundle_sha256
  stage_schema_sha256s
}
```

`make_capture_v2` へ渡す値はT514 4.3節どおりであり、`state_lease_id` はbroker invocation ID、
expiryはPREPARINGと同値、channel authoritiesは5.1節のdata projectionsである。
`capture_id` は既存 `make_capture_v2` が全fieldから計算する。

RESERVED leaseはPREPARINGを `replace` したrevision 1で、capture IDと5hash、stage schema tuple
だけを設定する。accepted plan、projection、attempt、ledger、recovery proofは空/nullのままである。

## 6. build、再読、CAS

### 6.1 処理順

1. 3.3節の全owner edgeとPREPARING bundle identityを検査する。
2. clockを読み、`ceil(now * 1_000_000) < expires_at_monotonic_us` を要求する。
3. bridgeから最初のexact final source readを取得する。
4. storeやbrokerを変更せず、catalog、option catalog、profile、schemas、hash、capture、
   RESERVED lease/receipt/bundle、RESERVED abort bundle、reserved notification/postcheck/conflict
   observation slotをすべて構築する。
5. bridgeから二回目のexact source readを取得する。stable material全field/hash、capture tuple、
   world/version/seq、phase、connection/action generation、network/source fingerprints、
   current actionsとauthority projectionsが最初のreadと一致することを要求する。
6. current deadline、clock、pending cancel、composition owner、session/lane/client leaseを再検査する。
7. storeのprivate CASへ、expected PREPARING bundle identityと事前構築済みRESERVED bundleを渡す。
8. CASの単一tuple代入後、await/callbackを挟まない区間でcomposition、ticket、source、OFFER
   receiptを3.6節のRESERVED行へ進める。
9. exact store bundleと全backlinkを再読し、全一致ならpostcheck slotを`EMPTY`のまま、最初の
   不一致ならpreallocated slotだけを`RECORDED`へ進める。全一致後だけ既存offline terminal通知を
   行い、raise時はreserved notification slotへ記録する。

step 3から7にawaitを入れない。allocationはstep 4までに終える。step 7以後のsemantic publish
区間は参照代入と固定enum代入だけであり、hash、schema、repr、log、Future通知を行わない。

### 6.2 final CAS条件

store CASは少なくとも次をすべて要求する。

- storeがopenでsame owner loop
- current active bundle `is expected_preparing_bundle`
- expected lease/receiptがexact PREPARING shape、revision 0、same invocation
- `_current_capture is owner.exact_store_capture_tuple[0]` かつrevision/fact/epoch同値
- staged/committed/dispatch/delivery/observationが増えていない
- composition lifecycle ACTIVE、flow/slotがPREPARING行、same port/bridge/registration
- exact current task/pending/driver、未完了result、cancelなし
- source/ticket/OFFER receiptのidentity/stateがPREPARING行
- session/lane/client leaseがsame identity、lane disposition null/callers 0、claim/activate/release 0
- broker/session identityとprofile bundleがcomposition固定値と一致
- current deadline、capture/material freshness、candidate capture、RESERVED leaseの全field一致
- candidate bundleの三要素とReserved receipt backlinksがexact identity一致

CASは内容が等しい別bundleを拒否する。CAS mismatch時は他ownerの値を上書きしない。

### 6.3 failure、cancel、retire

- PREPARING publish前の失敗はT525の既存OFFER cleanup契約を使う。
- PREPARING publish後、RESERVED CAS前の失敗は、3.4節のclosed classへ一度だけ分類する。
  allocation/catalog/schema/hash/typed validationは`INTERNAL_BUILD`、二回のowner/freshness/session/
  lane再読不一致は`STALE_FRESHNESS`、期限境界は`DEADLINE_EXPIRED`、exact pending cancelだけは
  `CANCEL_REQUESTED`である。複数条件が同時ならownerを過大に断定しないため優先順を
  `CANCEL_REQUESTED, DEADLINE_EXPIRED, STALE_FRESHNESS, INTERNAL_BUILD`へ固定する。
- classをprivate flow fieldへ参照代入した後、exact PREPARING tupleからprebuilt abort bundleへ
  一回だけCASする。このfieldは3.4節のexact `FinalizeAbortSelectionV2`であり、local variableや
  compositionへの任意追加fieldではない。broker I/Oは行わない。成功時は
  `INVALIDATED_NOTIFY_PENDING`の全backlinkを同じno-await/no-callback区間でpublishする。
- CAS成功後、terminal mapのexact aliasを既存`_finish`へ一回だけ渡す。成功時は
  `INVALIDATED_TERMINAL_DELIVERED`、raise/結果可視性不明時は
  `INVALIDATED_NOTIFICATION_UNKNOWN`へ進め、exact pending/driver/resultとabort bundleが保持する
  exact notification observation slotへ一回記録する。ここでresult、error、receiptをallocateしない。
- expected PREPARING bundleが既に別値ならabortでも上書きせず、class/resultを通知せず
  `ABORT_CAS_CONFLICT_HOLD`へ閉じる。現store ownerをforeignに書き換えない。
- RESERVED publish後にhash/build失敗は存在しない。backlink post-check/terminal通知失敗では
  RESERVEDを巻き戻さない。post-check不一致は`RESERVED_POSTCHECK_UNKNOWN`で通知0、post-check
  成功後の`preparing_complete`通知失敗だけを`RESERVED_NOTIFICATION_UNKNOWN`とする。
- stop/retireがPREPARINGまたはRESERVEDを観測した場合は、各receiptのprebuilt abort bundleへ
  同じidentity CASを行ってからcompositionをRETIREDにする。CAS不能なら現bundleを保持し、
  cleanup済みと称さない。CAS winner/loserは3.6節の競合表に従い、loserは第2CAS、結果通知、
  revision補正をしない。
- INVALIDATEDは再finalize不可、同じinvocationも新invocationも開始不可である。

abort/retire後もbroker laneを保持するのは意図的なfail-closedである。terminal ackを観測して
いないため、削除、RELEASED化、slot EMPTY化、次lease発行を行わない。

## 7. 実装対象と非対象

### 7.1 対象file

- `ai_client/discussion/reserved_finalize_v2.py`（新規）: private source/catalog/hash/finalize
- `ai_client/discussion/authority_capture_bridge_v2.py`: exact final source read port
- `ai_client/discussion/offer_composition_v2.py`: profile固定、prebuilt abort、closed states
- `ai_client/discussion/state.py`: closed bundle union、RESERVED/abort CAS、stage-specific read
- `ai_client/brain/invocation.py`: PREPARING成功直後の同期finalize呼出しとoffline terminal境界
- `tests/test_phase6_reserved_finalize_v2.py`（新規）と必要最小の既存offer test更新

`capture_v2.py` と `generation_v2.py` の公開contractは変更不要である。runtime authority projectionは
既存structural candidateをdata envelopeとして使用し、product authority判定は必ずReserved
receiptとstore CASで行う。

### 7.2 非対象

claim/activate/request/provider、projector/messages、attempt ledger、delivery、broker cleanup/recovery、
PF2/PF3証明、budget決定、通常runtimeからv2 driverを呼ぶ配線は実装しない。

## 8. 有限acceptance matrix

### 8.1 positive

| ID | 条件 | 期待結果 |
|---|---|---|
| P1 | 各5 trigger、exact owner/current source | trigger別stage tupleで一回だけRESERVED |
| P2 | evidence 49件以上、claims 33件以上 | 固定選択規則、mapping/hash再現可能、完全性は主張しない |
| P3 | chat runtime channel authority | exact runtime objectはreceipt保持、capture copy単独は無権限 |
| P4 | vote/CO/ability複数option | action indexと `oNNN` が1:1、実ID bindingがprivate |
| P5 | finalize後offline terminal成功 | arbiter driverは有限終了、storeはRESERVED_HELD |
| P6 | 全positive | broker claim/activate/generate/provider/action call count 0 |
| P7 | facts 49件、claims 33件の境界 | window `{source_count,start,count,limit}`と全entry root/indexが再現可能 |
| P8 | internal/stale/deadline/cancelの各abort | exact `internal_failure/stale/deadline_suppressed/preparing_complete` identityを一回通知 |
| P9 | stop/retireがfinalize/abortに先行・後続 | 3.6節のwinnerだけが一回CASし、revision 1または2が表どおり |
| P10 | fact/reply/claimにsidecar 0件またはexact 1件 | 0件だけroot fallback、1件はlossless source ref。action/role/ability/disclosureはexact 1件のみ |
| P11 | abort/RESERVED通知成功 | exact selection/notification slotはmatrixどおり、observationは`EMPTY`のまま |

### 8.2 negative

| ID | 変異/失敗点 | 期待結果 |
|---|---|---|
| N1 | public capture/lease/catalog、equal copy、foreign port | owner mismatch、publish 0 |
| N2 | active PREPARING bundle identityを1-bit相当で差替え | CAS拒否、他値を上書きしない |
| N3 | revision/world/seq/fact/phase/action/connectionを各1項変更 | `STALE_FRESHNESS`→INVALIDATED、exact `stale` result |
| N4 | material/source/authority/channel projection/hashを各1項変更 | `STALE_FRESHNESS`→INVALIDATED、copyをauthority化しない |
| N5 | deadline object、mapping order、expiry境界を変更 | INVALIDATED、`ceil(now_us) == expiry` はexact `deadline_suppressed` |
| N6 | exact pending cancel、foreign/done task、driver/active変更 | cancelはexact `preparing_complete`、他はstale。CAS前owner拒否も表どおり |
| N7 | lane disposition/caller/claim/release/retire/session registry変更 | INVALIDATED/terminal hold、broker call 0 |
| N8 | duplicate/missing player、unmapped target、vote target_count≠1、未知no_selection | catalog reject→INVALIDATED＋exact `internal_failure` |
| N9 | schema stage/order/hash、profile fingerprint、input/capture hash変更 | CAS拒否→INVALIDATED、または競合hold。第2CAS 0 |
| N10 | step 3〜6の各allocation/hash/schema位置で例外 | prebuilt abortとexact `internal_failure`だけを使用、partial RESERVED 0 |
| N11 | finalize二回、INVALIDATEDからfinalize | 拒否、revision増加なし |
| N12 | PREPARING時stop、RESERVED時stop | 対応abortへ一回遷移、RELEASED/EMPTYを偽装しない |
| N13 | RESERVED semantic publish後のterminal通知失敗 | `RESERVED_NOTIFICATION_UNKNOWN`、store/owner/pending/driver/result保持、新規flow停止 |
| N14 | structural simulator/candidateだけをproduct CASへ渡す | 拒否 |
| N15 | normal v1 invoke/runtime | 既存挙動、v2 source/catalog/finalize call 0 |
| N16 | player/fact/claim/opinion/action/role/ability/disclosure各entryを1 field変更 | catalog/option hashまたはowner不一致、publish 0 |
| N17 | binding tuple並替え、49→48/33→32切捨て境界変更、window metadata欠落 | `CATALOG_UNREPRESENTABLE`、黙った再付番0 |
| N18 | projection同値だがforeignなworld/state/action/disclosure/context opaque object | owner identity mismatch、hash一致をauthorityにしない |
| N19 | actual source refのparent/phase/path/hashを1 field変更、または存在するrefをnull化 | source binding mismatch、publish 0 |
| N20 | PreCapture/Reserved invalid receiptのpredecessor identity、lease/capture/result aliasを同値別objectへ変更 | abort CAS拒否、現tuple保持 |
| N21 | abort terminal通知で`_finish`がraise | INVALIDATED tupleを維持し`INVALIDATED_NOTIFICATION_UNKNOWN`、result再構築/再通知0 |
| N22 | RESERVED CAS後backlink 1 field不一致 | `RESERVED_POSTCHECK_UNKNOWN`、terminal通知/rollback/abort 0 |
| N23 | abort CAS expected tupleをforeign current tupleへ置換 | `ABORT_CAS_CONFLICT_HOLD`、result選択/通知/第2CAS 0 |
| N24 | 3.6節の各rowでstore tuple/component backlink/pending/laneを1 field変更 | validator拒否、行推測・修復・downgrade 0 |
| N25 | finalize/abort/retire CAS raceのwinner後にloserを再実行 | revision不変、第2CAS/二重通知 0 |
| N26 | sidecar 1件のtyped hash/path/parent/phase/revision不一致、または同identity複数 | `PRESENT_INVALID_OR_MULTIPLE`、root fallback禁止、catalog reject |
| N27 | action/role/ability/disclosure sidecar 0件 | `ABSENT`を許可せずcatalog reject |
| N28 | abort selectionのclass/resultを組替え、同値result copy、foreign slot、二重select | selection拒否、abort CAS/通知 0 |
| N29 | selection/pre-abort observation allocation失敗、またはReserved observation allocation失敗 | 前者はPREPARING publish 0でT525 cleanup、後者は`INTERNAL_BUILD` abort。post-publish allocation 0 |
| N30 | observation same-value copy、別flow slot、partial field、二重record、first mismatch変更 | state前進拒否、repair/通知retry 0 |

fault testは各位置を独立caseにし、分母を落とさない。private data、raw source、role/ability
実値をfailure textやpublic artifactへ出さない。

## 9. 実装・review gate

実装開始条件は本designの独立 `APPROVED` と、依存するT525 approval bindingのhash一致である。
実装後はfocused test、関連T519/T521/T524/T525回帰、全Python版の指定suite、
`python scripts/check_docs.py`、diff検査、独立tool reviewを行う。

本designのAPPROVEDは実装済み、RESERVED実測済み、provider実行可能、PF3 PASSを意味しない。
次段のclaim/activateには、exact `ReservedCaptureOwnerReceiptV2` を受ける別の承認済み設計と
実装gateが必要である。追加のユーザー製品選択は本単位には無い。
