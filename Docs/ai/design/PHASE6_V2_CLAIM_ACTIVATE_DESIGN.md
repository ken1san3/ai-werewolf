# Phase 6 v2 RESERVED → claim / ACTIVE 限定詳細設計

Status: APPROVED

## 1. 目的と境界

本設計は、T533で発行済みのexact `ReservedCaptureOwnerReceiptV2` とstore-owned
`RESERVED` cellから、既存`_ClientGenerationLease.claim()`、
`_ClientGenerationLease.activate()`を一度だけ使い、`StateGenerationLeaseV2.status`を
`ACTIVE`へ進める境界だけを定義する。

T533のRESERVED terminal通知後は、元の`_PendingInvocation`のresultとdriverが終了してよい。
本設計はそのpendingを再開せず、現在のstore cellを起点に別のprivate one-shot ownerを発行する。
公開`AdmissionResult`、`StateGenerationLeaseV2`、同値のreceipt、callerが渡すsession/lane/leaseは
authorityにしない。

次は本scope外であり、call countを0に保つ。

- structured generation request、provider、LLM、tokenizer、PF3判定
- projector、messages、attempt ledger、stage/commit、dispatch、delivery
- successor segment、reserve successor、audit record
- game action、network送信、通常`invoke()`へのv2既定接続

PF3、512 token予算、schema、prompt、privacy、game rule、v1既定動作を変更しない。本設計には
新しいユーザー製品選択はない。

## 2. 実接点と設計判断

### 2.1 継承するexact owner chain

開始点はstoreのprivate readが返す現在の`LeaseBundleCellV2(stage="RESERVED")`である。
cellのtupleはexact `(revision 1 RESERVED lease, GenerationCaptureV2,
ReservedCaptureOwnerReceiptV2)`であり、次をidentityで結ぶ。

- exact store、composition、authority bridge、arbiter
- exact initial ticket、offer source、offer receipt
- exact `BrokerAdmissionSession`、`_ControlLane`、`_ClientGenerationLease`
- exact `AdmissionRequest`、invocation ID、dispatch deadline
- exact capture、runtime authority、catalog、profile、hash DAG
- exact RESERVED abort bundleとT533 cell registry

T533の通知後に元pending/driver slotがnullでも、それは正常な
`RESERVED_TERMINAL_DELIVERED`形である。元pendingのresultはdoneであり、新しいclaim ownerの
待機・取消し通知には使わない。

### 2.2 新しいprivate入口

composition時に`ClaimActivateCallerPortV2`を一つだけ作り、exact composition、arbiter、store、
session、owner loopを閉包する。公開constructor、copy、pickle、reprによる内部値出力は禁止する。

```text
async _claim_activate_reserved_owned_v2(port) -> object
async _retire_active_owned_v2(port) -> object
```

どちらもreceipt/session/lane/leaseを引数に取らない。portはstoreのcurrent cellを一回readし、
3.1節のvalidatorを通してexact ownerを選ぶ。戻り値はauthorityではない。後続処理は常に
storeとcompositionのprivate owner slotを再読する。

claim入口は同一compositionにつき一回だけである。compositionの
`claim_activate_owner_or_null`がnullのときだけissuerがownerを発行し、同じno-await区間でslotへ
publishする。発行済みownerの再発行、同値別owner、foreign port、直接helper呼出しを拒否する。

compositionは別に`claim_activate_prepublication_hold_or_null`を一つ持つ。通常はnullである。owner
publish前に作成済みgated taskのcancel/gatherがabsolute deadlineを越えた場合だけ、private
`PrepublicationTaskHoldV2(exact gate, exact task tuple, failure_code)`を保持し、flowを
`CLAIM_PREPUBLICATION_UNKNOWN`へ進める。このholdがある間はowner発行、claim、retire、providerを禁止する。

### 2.3 one-shot owner

```text
ClaimActivateOwnerV2 = private mutable closed record {
  exact_port, exact_composition, exact_arbiter, exact_store,
  exact_reserved_cell_weakref, exact_reserved_identity,
  exact_reserved_receipt, exact_reserved_lease, exact_capture,
  exact_session, exact_lane, exact_client_lease, exact_request,
  exact_dispatch_deadline, exact_owner_loop,
  exact_claim_driver_task,
  claim_start_gate,
  claim_task_or_null,
  world_watcher_task_or_null,
  cancel_watcher_task_or_null,
  deadline_watcher_task_or_null,
  hard_deadline_monotonic,
  claim_wait_deadline_monotonic,
  settlement_deadline_monotonic,
  activation_context_or_null,
  active_candidate_cell_or_null,
  active_receipt_or_null,
  active_cleanup_candidates_or_null,
  active_cleanup_selection,
  cleanup_task_or_null,
  observation: exact ClaimActivateObservationV2,
  state
}
```

`exact_claim_driver_task`は発行時の`asyncio.current_task()`であり、claim入口の実行中だけliveを
要求する。ACTIVE publish後の所有権はtask identityではなく、store current cell、
compositionのexact owner slot、owner loop、active receiptの相互identityへ移る。
cleanup入口は別taskでもよいが、同じowner loop上で、composition slotにあるexact ownerだけを
消費する。任意task、任意receiptから所有権を復元しない。

`ClaimActivateObservationV2`は処理前に一つ構築し、次のnullable fieldを持つ。

```text
state: EMPTY | CLAIM_TERMINAL | CLAIM_UNKNOWN | CLEANUP_TERMINAL |
       CLEANUP_UNKNOWN | POSTCHECK_UNKNOWN
claim_status_or_null
cleanup_status_or_null
failure_code_or_null
expected_reserved_identity
observed_store_identity_or_null
observed_lane_disposition_or_null
observed_claimed_invocation_or_null
observed_activated_invocation_or_null
observed_lease_claimed_or_null
observed_lease_released_or_null
observed_lease_retired_or_null
```

例外object、arbitrary dict、raw frame、credential、token、private catalog本文を保持しない。
観測値は既存objectの固定enum、bool、invocation IDのexact aliasだけで、public serialization、log、
prompt、audit出力を禁止する。

observationは`state`を最後に一回だけ変更する。各stateのclosed shapeは次表である。
`broker snapshot`はstore identity、lane disposition、claimed/activated invocation、lease 3 flagsの
全fieldを同じowner-loop turnで記録したことを表し、実値がnullであるfieldもstateによって「観測済み」
と区別する。

| state | claim status | cleanup status | failure code | broker snapshot |
|---|---|---|---|---|
| EMPTY | null | null | null | 全field null、未観測 |
| CLAIM_TERMINAL | CANCELLED / EXPIRED / POISONED / UNAVAILABLE | null | `CLAIM_RETURNED_NON_GRANT` | 全field観測済み。claim task normal doneだけ |
| CLAIM_TERMINAL | CANCELLED / EXPIRED / POISONED / UNAVAILABLE | null | `CLAIM_CANCELLED_NON_GRANT` | 全field観測済み。claim task cancelled done、ABANDON 0だけ |
| CLAIM_TERMINAL | CANCELLED / EXPIRED / POISONED | null | `CLAIM_CANCELLED_ABANDONED` | 全field観測済み。claim task cancelled done、ABANDON 1、acknowledgedだけ |
| CLAIM_UNKNOWN | nullまたは観測済みstatus | null | `CLAIM_TASK_RAISED` / `CLAIM_SETTLE_TIMEOUT` / `WATCHER_CLEANUP_FAILED` / `CLAIM_SHAPE_MISMATCH` | 全field観測済み |
| CLEANUP_TERMINAL | GRANTED | RELEASED / EXPIRED / POISONED | selected cleanup reason | 全field観測済み |
| CLEANUP_UNKNOWN | GRANTED | nullまたはUNAVAILABLE | `RELEASE_RAISED` / `RELEASE_TIMEOUT` / `RELEASE_UNAVAILABLE` / `RELEASE_SHAPE_MISMATCH` | 全field観測済み |
| POSTCHECK_UNKNOWN | GRANTED | null | `ACTIVE_POSTCHECK_MISMATCH` | 全field観測済み |

表外status、failure code、部分snapshot、二回目記録を拒否する。

## 3. 開始validatorとfreshness

### 3.1 owner validator

claim開始直前に、次を一回のowner-loop readで全て要求する。

1. port、composition、arbiter、store、sessionがcomposition時に固定したexact objectである。
2. compositionは`ACTIVE`、flowは`RESERVED`、initial slotは`RESERVED_HELD`である。
3. store current cellがT533 registry発行のexact `RESERVED` cellであり、receiptのweakrefが同じ
   cell、identity、tuple末尾へ一致する。
4. leaseはrevision 1 `RESERVED`、captureと全hash fieldがreceipt/captureへ一致する。
5. T533のticket/source/OFFER receiptはそれぞれ
   `CONSUMED_RESERVED` / `RESERVED` / `CONSUMED_RESERVED`である。
6. T533元pending resultはdone、元driverとarbiter active slotはnullである。
7. sessionはopen、transport open、reader task live、writer closingでない。
8. `session._control_lanes[invocation_id] is exact_lane`、`lane.lease is exact_client_lease`、
   `lane.callers == 0`、`lane.disposition is None`である。
9. client leaseは`_claimed == _released == _retired == False`、abandon disposition null、
   invocation ID一致である。
10. sessionのclaimed/activated invocationは共にnull、当該IDのcall ordinal/request IDsは未登録、
    result future registryはT533 acquire終了により未登録である。
11. composition owner slot、prepublication hold、claim task、activation context、ACTIVE candidateは
    全てnullである。

1 fieldでも違えばbroker call 0、store mutation 0で拒否する。validatorはstageを推測、修復、
再bindしない。

### 3.2 freshness

broker claim前と、claimが`GRANTED`を返した直後の二回、同じclosed検査を行う。

- `ceil(clock * 1_000_000) < lease.expires_at_monotonic_us`
- 現在の`DispatchDeadline`のday、phase、mapping order、action generation、connection generation、
  local deadlineがreceiptのexact deadlineと一致し、現在時刻がnot-afterより前
- T518/T530のowner-bound final source readを再実行し、world version、last sequence、fact revision、
  phase identity、action/connection generation、capture/material/source fingerprint、current actions、
  public channel authorityがRESERVED receiptの保存値とexact一致
- store current cell、receipt、composition owner graphが3.1節のまま
- claim driverにcancel要求がなく、composition/arbiterがretire/stopされていない

`DiscussionStateStore.capture()`は呼ばない。任意channel名、role、recipientを推測しない。
claim前の不一致はT533のprebuilt RESERVED abortへ進む。GRANTED後の不一致はbroker cleanupを先に
完了し、そのclean terminalを確認した後だけ同じabortへ進む。

## 4. ACTIVE candidateとatomic publish

### 4.1 closed ACTIVE shape

T533 cell amendmentへ次の1行だけを追加する。

| cell stage | identity type | tuple arity | lease status/revision | receipt type |
|---|---|---|---|---|
| ACTIVE | ActiveBundleIdentityV2 | 3: lease,capture,receipt | ACTIVE/2 | ActiveCaptureOwnerReceiptV2 |

`ActiveBundleIdentityV2`とcellは既存store-owned issuer/WeakKeyDictionaryを使う。RESERVED identityを
再利用しない。

```text
ActiveCaptureOwnerReceiptV2 = opaque private immutable {
  schema_version = "aiwolf.active-capture-owner-receipt.v2",
  predecessor_identity: exact ReservedBundleIdentityV2,
  exact_reserved_receipt,
  exact capture,
  exact revision-2 ACTIVE lease,
  claim_owner_identity,
  claim_owner_weakref,
  exact activation context,
  exact session/lane/client lease/request/deadline,
  cell_identity, cell_weakref,
  postcheck_observation,
  cleanup_selection_slot,
  state: ACTIVE | POSTCHECK_UNKNOWN | CLEANUP_PENDING | RETIRED
}
```

ACTIVE leaseはRESERVED leaseを`replace`し、`status="ACTIVE"`、`lease_revision=2`だけを変更する。
capture/hash/broker lease IDsはbyte-equalである。`StateGenerationLeaseV2.__post_init__()`をpublish前に
実行する。

ACTIVEからclean cleanupする候補はpublish前に一つ作る。

```text
ActiveCleanupCandidateV2 = opaque private immutable {
  reason: ACTIVE_SCOPE_END | ACTIVE_CANCEL | ACTIVE_STALE | ACTIVE_EXPIRED,
  candidate_cell: exact ACTIVE_INVALIDATED LeaseBundleCellV2
}

ActiveCleanupSelectionV2 = private mutable closed record {
  candidates: exact tuple[ActiveCleanupCandidateV2, 4],
  selected_reason_or_null,
  selected_candidate_identity_or_null,
  state: BOUND_EMPTY | SELECTED | PUBLISHED | CONFLICT_HELD | UNKNOWN
}

ActiveInvalidatedOwnerReceiptV2 = opaque private immutable {
  predecessor_identity: exact ActiveBundleIdentityV2,
  exact revision-2 ACTIVE lease,
  exact capture,
  claim_owner_identity,
  exact revision-3 INVALIDATED lease,
  reason: ACTIVE_SCOPE_END | ACTIVE_CANCEL | ACTIVE_STALE | ACTIVE_EXPIRED,
  cell_identity, cell_weakref,
  state: ARMED | PUBLISHED | CONFLICT_HELD
}
```

同じcandidateを複数reasonへ流用しない。順序を
`(ACTIVE_SCOPE_END, ACTIVE_CANCEL, ACTIVE_STALE, ACTIVE_EXPIRED)`に固定した4 candidate tupleを
ACTIVE publish前に全部prebuildする。selectionは一つのreason/candidate identityを一回だけ選ぶ。
未選択candidateはpublishせず、clean publishと同一区間でowner tupleから除去する。same-value別tuple、
順序変更、別reason candidateを拒否する。各candidateは新しいidentity/cellを持ち、predecessor cellへは
weakrefだけを持つ。

reason選択は次だけである。複数条件では上から先の一つを選ぶ。

| 条件 | selected reason |
|---|---|
| stopまたは明示cancel | ACTIVE_CANCEL |
| deadline/lease expiry | ACTIVE_EXPIRED |
| owner-bound source/freshness不一致 | ACTIVE_STALE |
| 後続gateへ渡さず正常にoffline scopeを閉じる | ACTIVE_SCOPE_END |

`ClaimActivateOwnerV2`はweakrefableなprivate identityを一つ持つ。active receiptからownerへは
weakrefだけとし、owner→receipt→ownerのcycleを作らない。current ACTIVE中はcomposition owner slotが
ownerを強所有し、receiptのweakrefがそのexact ownerを返すことを必須とする。clean INVALIDATED publishと
同じno-await区間でcomposition slotをnullにし、ownerのclaim task、activation context、ACTIVE/abort
candidate refsをclearする。invalidated receiptはsecret-free owner identityだけをlineageとして残し、
ownerを強参照しない。`ActiveInvalidatedOwnerReceiptV2`はactive receipt、activation context、session、
lane、client lease、requestを保持しない。保持できるstrong refsはimmutable ACTIVE/INVALIDATED lease、
capture、predecessor/owner lineage identityだけである。UNKNOWN行では診断・回復のためowner graphを
保持する。

### 4.2 claimとactivateの順序

1. 3.1節/3.2節のpre-claim検査を行う。
2. owner、observation、未setの`asyncio.Event` start gate、world/cancel/deadline watcher coroutine、
   absolute deadline値を構築する。ownerはまだcompositionから到達不能である。
3. claim coroutineは最初にstart gateをawaitし、その後だけ
   `exact_client_lease.claim()`を一回呼ぶ。claim taskと3 watcher taskを全てcreateする。
4. event loopへ制御を返さない同じno-await区間で、全taskをownerへ設定し、composition owner slotへ
   exact ownerをpublishし、最後にstart gateをsetする。taskはこの区間中にclaimへ進めない。
5. task/coroutine/Event/owner allocationまたはtask createが一つでも失敗した場合、start gateをsetせず、
   作成済みtaskをcancel/gatherしてから返る。owner slotはnull、R不変、broker control count 0である。
   owner publish後task nullという正規stateは設けない。
6. claim task、world update、stop/cancel signal、absolute claim-wait deadlineを待つ。
   watcherの詳細は4.4節に従う。
7. claimが非`GRANTED`なら5.1節へ、結果/cleanupが確定不能なら5.3節へ進む。
8. `GRANTED`ならsession/lane/leaseのexact granted shapeと3.2節のpost-claim freshnessを検査する。
9. existing `exact_client_lease.activate()`を一回だけ呼び、返ったexact context、ACTIVE cell、
   active receipt、4つのcleanup candidate、postcheck slotを全て構築・検証する。
10. no-await/no-callback区間でactivation contextの`__enter__()`を一回呼ぶ。直後に
   `session._activated_invocation == invocation_id`を要求する。
11. store `_cas_active_lease_v2(expected_reserved_cell, active_cell)`を一回呼ぶ。CASは3.1節の
   static owner edge、exact GRANTED shape、ACTIVE candidate、composition owner slotを再検査し、
   current cellを一参照でACTIVE cellへ交換する。
12. 同じno-await区間でowner/receipt/compositionを次のACTIVE行へ進める。
13. read-only postcheckを一回行う。全一致ならexact active receiptを返す。不一致なら
    `ACTIVE_POSTCHECK_UNKNOWN`へ閉じ、activationを保持して新しい処理を禁止する。

step 10後、step 11の前またはCAS内で例外が出た場合は、同じtaskでcontext `__exit__()`を一回呼び、
session activated slotがnullになったことを確認してから5.2節のreleaseへ進む。ACTIVE publish後に
RESERVEDへrollbackしない。

| field | RESERVED before | ACTIVE after |
|---|---|---|
| store cell | R | A |
| active receipt / owner | local candidate / CLAIM_GRANTED_BUILD | ACTIVE / ACTIVE |
| T533 RESERVED abort bundle / invalid receipt | ARMED / ARMED | RETIRED / RETIRED |
| initial ticket / offer source / offer receipt | CONSUMED_RESERVED / RESERVED / CONSUMED_RESERVED | CONSUMED_ACTIVE / ACTIVE / CONSUMED_ACTIVE |
| composition lifecycle / flow / slot | ACTIVE / RESERVED / RESERVED_HELD | ACTIVE / ACTIVE / ACTIVE_HELD |
| session | claimed exact、activated null | claimed exact、activated exact |

表のstate参照代入はACTIVE cell CAS成功後の同じnon-raising区間で行う。T533 abortをACTIVE後に
再利用しない。

### 4.4 finite watcher

clockはsessionとcompositionが固定した同じmonotonic sourceを使う。

```text
hard_deadline = min(dispatch_deadline.not_after_monotonic,
                    expires_at_monotonic_us / 1_000_000)
cleanup_reserve = exact session config.cancellation_grace_seconds
claim_wait_deadline = hard_deadline - cleanup_reserve
settlement_deadline = hard_deadline
```

開始時に`now < claim_wait_deadline < settlement_deadline`を要求する。world watcherはexact
`world.wait_for_update(capture.world_version)`、cancel watcherはcompositionが所有するprivate one-shot
cancel Event、deadline watcherは`claim_wait_deadline`までのmonotonic sleepである。通常pendingのdone
futureやcaller生成Eventをsignal sourceにしない。`stop()`は同じcomposition owner slotを確認してcancel
Eventを一回setする。

claim taskが先に終わった場合、3 watcherをcancelし、`settlement_deadline`までに
`gather(return_exceptions=True)`を完了させる。world/cancel/deadlineが先ならclaim taskを一回だけcancelし、
既存claim内部がcontrol completionと必要なABANDONを処理するのを同deadlineまで待つ。外側はCANCEL、
ABANDON、RELEASEを重ねて送らない。同じloop turnで複数doneなら、(1) claim taskのinspectable result、
(2) cancel、(3) world update、(4) deadlineの順で一つを選ぶ。claim GRANTEDと他signalが同時ならACTIVEへ
進まず5.2節のclean releaseを選ぶ。deadline時点でtaskまたはwatcherがlive、gatherが未完了なら
`CLAIM_UNKNOWN`とし、全task refsをownerに保持する。

owner publish前の失敗では、taskへwrapしていないcoroutine objectを明示的にcloseし、作成済みtaskを
cancel/gatherする。start gateはunsetなのでclaim controlは0である。cancel/gather自体がsettlement
deadlineまでに完了しなければ、ownerをpublishせずlocal task refsをarbiterのprepublication hold slotへ
移し、新規v2 flowを禁止する`PREPUBLICATION_TASK_UNKNOWN`へ閉じる。このholdだけはRを変更せず、task
終了をcleanと推定しない。

broker control上限はclaim operation 1、claim内部ABANDON operation 0または1、GRANTED clean経路の
release operation 0または1である。
T536外側から送るCANCEL/ABANDONは0。時刻経過、task.done、transport closeだけでcleanに昇格しない。

### 4.3 ACTIVE postcheck

postcheck slotはACTIVE candidateより先に構築し、expected/observedを次について比較する。

- store cell identity/stage/tupleとactive receipt backlink
- lease revision/statusとcapture/hash全field
- exact composition/owner/port/session/lane/client lease
- `lane.disposition == GRANTED`、`lane.lease is client lease`、`lane.callers == 0`
- client lease claimed true、released/retired false、abandon null
- session claimed invocationとactivated invocationが共にexact invocation ID
- call ordinal 0、request ID set empty、generation in-flight 0
- provider/generation/audit/action call count 0

観測slotはpublish前に確保する。postcheck中にdict/tuple/error textを新規構築しない。最初の不一致を
固定enumで記録するだけである。

## 5. terminal、cleanup、UNKNOWN

### 5.1 claim非GRANTEDまたはcancelled claim ABANDON確定

許可する確定terminalは`CANCELLED`、`EXPIRED`、`POISONED`、`UNAVAILABLE`だけである。
対応する既存client状態、すなわちlane disposition、lane.lease null、client lease retired true、
claimed/activated null、control lane/terminal tombstoneの既存規則を確認する。その後に限り、T533の
RESERVED abort candidateをexact current RESERVED cellへCASし、revision 2 INVALIDATEDへ進める。

ABANDON 0のdirect/cancelled non-GRANTは、`lane.disposition == status`、`lane.lease is null`、
client leaseのclaimed/released/retired/abandonが`false/false/true/null`、lane callers 0、control laneなし、
terminal tombstone exact status、session claimed/activated null、ack/call ordinal/request IDなし、session/
transport openを全て要求する。cancelled GRANTEDから内部ABANDON 1がack済みの枝は、final statusを
`CANCELLED`、`EXPIRED`、`POISONED`のいずれかに限定し、同じregistry shapeに加えてclient leaseを
`false/false/true/ABANDON_ACKNOWLEDGED`、lane abandon dispositionを
`ABANDON_ACKNOWLEDGED`と要求する。`UNAVAILABLE`＋`ABANDON_SESSION_LOST`は確定ABANDONに含めない。

claim resultが`OFFERED`、unknown text、同値別status、またはterminal shape不一致ならUNKNOWNとする。
非GRANTEDでは`release()`や`activate()`を呼ばない。

### 5.2 GRANTED後のclean cleanup

GRANTED後、ACTIVE publish前にcancel/stale/expiry/allocation/activation/CAS failureが起きた場合は、
activationへ入っていればまずexact contextを一回exitする。次にrelease taskを一つ作り、
`exact_client_lease.release()`を一回だけ呼ぶ。実APIの戻り値は常に`None`であり、status authorityには
使わない。

release開始時に`cleanup_deadline = loop.time() + exact session
config.shutdown_grace_seconds`を一回固定する。cancelされてもrelease task自体を二重cancelせず、
deadlineまでshieldして待つ。deadline時点でliveならtask refを保持してUNKNOWNへ閉じる。

await完了後のexact shapeだけで次を区別する。

| lane disposition | lease claimed/released/retired/abandon | lane/registries | 判定 |
|---|---|---|---|
| `RELEASED` | false / true / false / null | callers 0、lane.lease null、control laneなし、terminal tombstoneなし、claimed/activated null、call ordinal/request IDsなし | clean |
| `EXPIRED` | false / true / true / `ABANDON_ACKNOWLEDGED` | callers 0、lane.lease null、control laneなし、terminal tombstone exact EXPIRED、claimed/activated null、call ordinal/request IDsなし | clean |
| `POISONED` | false / true / false / null | callers 0、lane.lease null、control laneなし、terminal tombstone exact POISONED、claimed/activated null、call ordinal/request IDsなし | clean |
| `UNAVAILABLE` | false / true / false / null | 既存client shapeを観測・保持 | UNKNOWN。cleanへ昇格しない |
| その他、raise、cancel、timeout | 観測値をそのまま保持 | mixedを許容しない | UNKNOWN |

全行でgeneration in-flight 0を要求する。clean確認後だけ5.5節の共通I2 publishを呼ぶ。UNKNOWNでは
storeをRESERVEDのまま`CLAIM_CLEANUP_UNKNOWN`へ閉じる。二回目のrelease、claim、activate、abort
CASを行わない。

### 5.3 claim結果不明

claim task取消し時は既存`BrokerAdmissionSession._ordinary_control`がcontrol completionを収束させ、
GRANTEDならABANDONを試みる。T536はその実装結果を再読する。

D068再評価で確認したclaim controlの全return/exception/terminal枝とI2対応は次表を正本とする。

| source branch | claim task outcome / internal control | accepted final shape | next row |
|---|---|---|---|
| cached controlまたは通常CLAIM ackを`_apply_control_result`が許可4 terminalへ適用 | normal return、ABANDON 0 | 5.1節ABANDON 0 exact terminal | `CLAIM_RETURNED_NON_GRANT` |
| task cancel後、`_finish_cancelled_control`が完了しCLAIM apply結果が許可4 terminal | taskは`CancelledError`、ABANDON 0。GRANTED textがconcurrent EXPIREDでEXPIREDへ置換される枝を含む | 5.1節ABANDON 0 exact terminal | `CLAIM_CANCELLED_NON_GRANT` |
| task cancel後、CLAIM applyがGRANTED、内部ABANDON ackがCANCELLED/EXPIRED/POISONED | taskは`CancelledError`、ABANDON 1 | 5.1節`ABANDON_ACKNOWLEDGED` exact terminal | `CLAIM_CANCELLED_ABANDONED` |
| normal CLAIM applyがGRANTED | normal return、ABANDON 0 | exact claimed shape | ACTIVEへ進むか5.2節release。I2 non-GRANT行へ入れない |
| OFFERED、REPLACED、OVERLOADED、unknown text、protocol error、control/task exception | return/raiseを問わず | 5.1節の許可shape外 | `CLAIM_RESULT_UNKNOWN` |
| cancelled GRANTED後のABANDONがUNAVAILABLE、`ABANDON_SESSION_LOST`、raise、cancel、timeout、ack/shape不一致 | taskはcancelまたはexception、ABANDON 0または1 | session lossまたはmixed | `CLAIM_RESULT_UNKNOWN` |
| settlement deadline時にclaim/ABANDON/watcherがliveまたはgather未完了 | 未確定 | terminal証明なし | `CLAIM_RESULT_UNKNOWN` |

許可3行はtask outcome、failure code、control count、final broker shapeが同じ行に一致した場合だけI2へ
進む。一つでも別行の値を混ぜず、例外後に偶然似たterminal値を観測してもUNKNOWNから昇格しない。
ここでABANDON 1は835〜904行相当のcancelled-claim内部ABANDON operationへ一回入ったことを表す。
既にlane EXPIREDなら`_send`がframeを抑止するためwire ABANDON frameは0、それ以外は1であり、test counterは
operation entryとwire frameを別々に記録する。どちらも5.1節の同じexact final shapeなしにはI2へ進めない。

- exact non-GRANTED terminal shape、またはack済みABANDON terminal shapeまで確認できれば5.1節の
  INVALIDATED CASへ進む。
- GRANTED clean shapeなら5.2節のreleaseへ進む。
- task exception、session loss、ABANDON不明、lane/lease/sessionのmixed shapeは
  `CLAIM_RESULT_UNKNOWN`へ閉じる。

UNKNOWNではstore current cellをRESERVEDに保ち、owner、claim task、observation、session/lane/lease
refsを保持する。新flow、再claim、activate、release、provider、store abortを全て禁止する。
時刻経過だけでcleanと推定しない。回復には親設計5.2節の`RecoveryProofV2`相当の別承認gateが必要で、
本scopeでは実装しない。

### 5.4 ACTIVE cleanup

`_retire_active_owned_v2(port)`はexact ACTIVE cell/receiptとcomposition owner slotだけを選ぶ。
4.1節の優先表からreasonを一つ選択し、selection slotを`BOUND_EMPTY -> SELECTED`へ一回進める。
contextを一回exitし、session activated slot nullを確認してからleaseを一回releaseする。releaseの
戻り値は使わず、5.2節のpost-release matrixでcleanを確認し、selected revision 3 INVALIDATED
candidateだけをCASする。

release unknownならACTIVE cellを維持して`ACTIVE_CLEANUP_UNKNOWN`へ閉じる。activation contextをexit済み
でもstateをRESERVED/INVALIDATEDと推定しない。store CAS conflictなら
`ACTIVE_ABORT_CONFLICT_HOLD`とし、観測したvalid cell identity/shapeだけを記録する。二回目のcleanupや
別candidate適用を禁止する。

successful I3 publishは次の一行だけである。

| field | before | after |
|---|---|---|
| store cell | exact A | selected exact I3 |
| selection / selected invalid receipt | SELECTED / ARMED | PUBLISHED / PUBLISHED |
| unselected candidates | owner tuple内にexact 3件 | 全てRETIRED、strong refs clear |
| active receipt | CLEANUP_PENDING | RETIRED |
| ticket / source / offer receipt | CONSUMED_ACTIVE / ACTIVE / CONSUMED_ACTIVE | RETIRED_HELD / RETIRED_HELD / RETIRED_HELD |
| composition lifecycle / flow / slot | ACTIVE / ACTIVE / ACTIVE_HELD | RETIRED / ACTIVE_RETIRED_HELD / INVALIDATED_HELD |
| claim owner slot/context/candidates | exact owner / exited context / tuple | null / null / null |
| invalidated cell refs | local candidate | ACTIVE/INVALIDATED lease、capture、lineage identityのみ |

CAS前に全fieldを検証し、CAS後は同じno-await non-raising区間で表の参照代入だけを行う。CAS conflictは
ACTIVE_ABORT_CONFLICT_HOLD、CAS後postcheck不一致は`I3_POSTCHECK_UNKNOWN`でI3とowner graphを保持する。

### 5.5 共通I2 publish

pre-claim freshness failure、claim非GRANTED、GRANTED後clean release、cancel/ABANDON収束の入口は
private `_publish_reserved_invalidated_after_claim_v2(port, claim_owner_or_null)`一つへ集約する。
owner nullを許すのはclaim taskを一つも生成していないpre-claim failureだけであり、portからexact current
R/Reserved receiptを再読する。その他はexact composition owner slotと同じowner必須である。T533の
terminal resultを選び直さず、既存RESERVED abort candidateをretire用途で使う。

| field | before | successful I2 publish |
|---|---|---|
| store cell | exact R | exact T533 `RESERVED_INVALIDATED` candidate I2 |
| RESERVED abort bundle / invalid receipt | ARMED / ARMED | PUBLISHED / PUBLISHED |
| PREPARING abort bundle / invalid receipt / selection | RETIRED / RETIRED / RETIRED_UNSELECTED | 同じ |
| Reserved receipt | RESERVED | RETIRED |
| initial ticket / offer source / offer receipt | CONSUMED_RESERVED / RESERVED / CONSUMED_RESERVED | RETIRED_HELD / RETIRED_HELD / RETIRED_HELD |
| composition lifecycle / flow / slot | ACTIVE / RESERVED / RESERVED_HELD | RETIRED / FINALIZE_RETIRED_HELD / INVALIDATED_HELD |
| T533 pending future | exact `preparing_complete` done | 同じ。再通知0 |
| arbiter old active / old driver | null / null | null / null |
| claim owner slot | 下表の入口別shape | null |
| claim task/watchers/context/candidates | 下表の入口別shape | 発行済みなら全clear。observationはI2 receiptへ入れずsecret-free fixed outcomeだけhandoff |

I2入口のbefore/broker/ref shapeは次の5行だけである。`internal ABANDON`はoperation entry countであり、
wire frame countは前節の0/1規則を別fieldで照合する。

| entry | owner / observation before | broker calls | broker before I2 | refs after I2 | next cleanup/recovery gate |
|---|---|---|---|---|---|
| PRECLAIM_FRESHNESS | owner未発行、observation未発行、task/watcher/context/candidate 0 | claim 0、activate 0、release 0、outer control 0 | exact lane held、disposition null、lane.lease exact client lease、claimed/released/retired false、session claimed/activated null | T536 owner refsなし。T533 I2 lineageだけ | offered leaseは未cleanup。別gateでexact lane CANCELし、GRANTEDならreleaseし、terminal ackを得るまでRELEASED禁止 |
| CLAIM_RETURNED_NON_GRANT | exact owner、CLAIM_TERMINAL/`CLAIM_RETURNED_NON_GRANT` observation、claim task normal done、watchers回収済み、context/candidate 0 | claim 1、activate 0、release 0、internal ABANDON 0 | 5.1節のABANDON 0 exact non-GRANT terminal | owner slot/task/observationをclearし、fixed entry kindだけ保持 | broker terminal ackをstate cleanup gateへ渡す。再control不要、RELEASEDは本scope外 |
| CLAIM_CANCELLED_NON_GRANT | exact owner、CLAIM_TERMINAL/`CLAIM_CANCELLED_NON_GRANT` observation、claim task cancelled done、watchers回収済み、context/candidate 0 | claim 1、activate 0、release 0、internal ABANDON 0 | 5.1節のABANDON 0 exact non-GRANT terminal | owner slot/task/observationをclearし、fixed entry kindだけ保持 | broker terminal ackをstate cleanup gateへ渡す。再control不要、RELEASEDは本scope外 |
| CLAIM_CANCELLED_ABANDONED | exact owner、CLAIM_TERMINAL/`CLAIM_CANCELLED_ABANDONED` observation、claim task cancelled done、watchers回収済み、context/candidate 0 | claim 1、activate 0、release 0、internal ABANDON 1 | 5.1節のexact `ABANDON_ACKNOWLEDGED` terminal。final statusはCANCELLED/EXPIRED/POISONEDだけ | owner slot/task/observationをclearし、fixed entry kindだけ保持 | acknowledged terminalをstate cleanup gateへ渡す。再ABANDON/release禁止、RELEASEDは本scope外 |
| GRANTED_CLEAN_RELEASE | exact owner、CLEANUP_TERMINAL observation、claim/release task done、watchers回収済み、context nullまたはexit済み | claim 1、activate 0またはenter後exit 1、release 1、outer control 0 | 5.2節のRELEASED/EXPIRED/POISONED exact clean shape | owner slot/tasks/context/candidates/observationをclearし、fixed entry kindだけ保持 | clean terminal ackをstate cleanup gateへ渡す。再release不要、RELEASEDは本scope外 |

PRECLAIM_FRESHNESSはbroker terminalを主張しない。I2 publish後もlane heldをexactに保持し、時刻経過や
INVALIDATED statusだけでcleanup済みと推定しない。他4行のterminal/clean shapeをpre-claim行へ補完しない。

全candidateと上表の参照代入をCAS前に検証する。no-await区間でstore cell CASを一回行い、成功後は
固定objectへの参照代入だけを行う。この区間はallocation、hash、repr、Future通知を含まず、代入を
non-raising実装へ固定する。したがってI2 publish後のpartial stateを正規化しない。

CAS前validate failureまたはCAS loserは、preallocated conflict observationへexpected/observed cell
identity/stageを記録して`CLAIM_ABORT_CONFLICT_HOLD`へ進む。CASを再試行しない。CAS後postcheckが上表と
違えばI2 cellを保持して`I2_POSTCHECK_UNKNOWN`とする。PRECLAIM_FRESHNESSではT536 ownerが無いため
T533 conflict/postcheck refsだけを保持し、他4行ではowner refsをclearしない。T533 pending futureは既に
doneなので新しいnotificationを行わず、notification failureという正規行も設けない。async helperの
return/caller cancellationはI2 authorityを変更しない。

## 6. 全state × field/source matrix

この表を実装の正本とする。`R`はexact RESERVED cell、`A`はexact ACTIVE cell、`I2`はT533
revision 2 INVALIDATED cell、`I3`はACTIVE cleanup revision 3 INVALIDATED cellである。

| state | store cell | owner/task | claim/activation | broker exact shape | retry / next |
|---|---|---|---|---|---|
| RESERVED_IDLE | R | owner null、task null | 0 / 0 | unclaimed lane held | 一回だけowner発行可 |
| TASKS_CREATED_LOCAL | R | owner unpublished、start gate unset、全task exact | 0 / 0 | unclaimed lane held | 同turn publishまたは全task cancel/gather |
| PREPUBLICATION_TASK_UNKNOWN | R | owner null、exact prepublication hold | 0 / 0 | unclaimed lane held | 回復gate以外禁止 |
| CLAIM_WAIT | R | exact owner/task live | 1 in-flight / 0 | callers 0または1、result未確定 | watcherだけ |
| CLAIM_RETURNED_NON_GRANT_TERMINAL | R→I2 | owner保持、task normal done | claim 1 / activate 0 / ABANDON 0 | 5.1節ABANDON 0 exact terminal | CAS一回、再claim不可 |
| CLAIM_CANCELLED_NON_GRANT_TERMINAL | R→I2 | owner保持、task cancelled done | claim 1 / activate 0 / ABANDON 0 | 5.1節ABANDON 0 exact terminal | CAS一回、再claim不可 |
| CLAIM_CANCELLED_ABANDONED_TERMINAL | R→I2 | owner保持、task cancelled done | claim 1 / activate 0 / ABANDON 1 | exact acknowledged ABANDON terminal | CAS一回、再claim/ABANDON不可 |
| CLAIM_GRANTED_BUILD | R | owner保持、task done | 1 GRANTED / 0 | claimed exact、activated null | ACTIVE候補構築またはclean release |
| ACTIVATION_ENTERED_LOCAL | R | owner/context保持 | 1 / enter 1 | claimed+activated exact | await/callback 0、ACTIVE CASだけ |
| ACTIVE | A | owner/active receipt保持、claim task ref clear可 | 1 / entered | claimed+activated exact | 後続gateまたはcleanup一回 |
| ACTIVE_POSTCHECK_UNKNOWN | A | 全owner/observation保持 | 1 / entered | observed shape保持 | 全新規flow禁止 |
| ACTIVE_CLEANUP_PENDING | A | owner/context保持 | 1 / exited | release一回in-flight | cleanup completionのみ |
| ACTIVE_INVALIDATED | I3 | claim owner slot null、I3 lineage receiptだけ | 1 / exited | exact clean terminal | 新規flowは後続RELEASED gateまで禁止 |
| INVALIDATED_PRECLAIM | I2 | owner未発行、T533 invalid receipt | claim 0 / activate 0 | unclaimed lane held、terminal証明なし | broker offer cleanup gate必須 |
| INVALIDATED_RETURNED_NON_GRANT | I2 | claim owner slot null、T533 invalid receipt | claim 1 / activate 0 / ABANDON 0 | exact non-GRANT terminal | terminal ackをstate cleanup gateへ渡す |
| INVALIDATED_CANCELLED_NON_GRANT | I2 | claim owner slot null、T533 invalid receipt | claim 1 / activate 0 / ABANDON 0 | exact non-GRANT terminal | terminal ackをstate cleanup gateへ渡す |
| INVALIDATED_CANCELLED_ABANDONED | I2 | claim owner slot null、T533 invalid receipt | claim 1 / activate 0 / ABANDON 1 | exact acknowledged ABANDON terminal | terminal ackをstate cleanup gateへ渡す |
| INVALIDATED_AFTER_RELEASE | I2 | claim owner slot null、T533 invalid receipt | claim 1 / activate 0またはexit済み | exact clean release terminal | clean ackをstate cleanup gateへ渡す |
| CLAIM_RESULT_UNKNOWN | R | owner/task/observation保持 | 1 / 0 | mixed/不明 | 回復gate以外禁止 |
| CLAIM_CLEANUP_UNKNOWN | R | owner/observation保持 | 1 / 0またはexit済み | cleanup不明 | 回復gate以外禁止 |
| ACTIVE_CLEANUP_UNKNOWN | A | owner/observation保持 | 1 / exit済み | cleanup不明 | 回復gate以外禁止 |
| ACTIVE_ABORT_CONFLICT_HOLD | observed current保持 | owner/observation保持 | 1 / exit済み | cleanでもCAS不一致 | 二回目CAS禁止 |
| I3_POSTCHECK_UNKNOWN | I3 | owner/observation保持 | 1 / exit済み | exact clean terminal | repair/再CAS禁止 |
| CLAIM_ABORT_CONFLICT_HOLD | observed current保持 | preclaimはowner null、他はowner/observation保持 | entryどおり / 0 | lane heldまたはbroker terminal/clean | 二回目CAS禁止、entry別cleanup gate |
| I2_POSTCHECK_UNKNOWN | I2 | preclaimはT533 refsのみ、他はowner/observation保持 | entryどおり / 0 | lane heldまたはbroker terminal/clean | repair/再通知/CAS禁止、entry別cleanup gate |

全行でcomposition owner slot、store cell、receipt backlink、session/lane/client leaseのうち一つでも
表外ならUNKNOWNへ閉じる。validatorは近い行へdowngrade、repair、推測しない。INVALIDATEDは
RELEASEDではなく、同一invocationを完了扱いにするには別cleanup/recovery gateが必要である。

owner recordのnullable/state shapeは次表で固定する。`built`はexact active receipt、ACTIVE cell、4つの
I3候補が全て存在すること、`entered`はactivation contextがsessionのexact activated invocationを
所有することを表す。

| owner state | composition owner slot | claim/watchers | activation context | ACTIVE candidates | cleanup task | observation / ref解放 |
|---|---|---|---|---|---|---|
| RESERVED_IDLE | null | null | null | null | null | owner未発行 |
| TASKS_CREATED_LOCAL | null | exact gated claim＋3 watcher | null | null | null | EMPTY、failure時全task cancel/gather |
| PREPUBLICATION_TASK_UNKNOWN | null、hold exact | hold内task tuple | null | null | null | CLAIM_UNKNOWN、全refsをholdに保持 |
| CLAIM_WAIT | exact owner | exact live claim＋3 watcher | null | null | null | EMPTY、保持 |
| CLAIM_DONE_BUILD | exact owner | exact done claim、watchers null | null | null→built | null | CLAIM_TERMINALまたはEMPTY、保持 |
| ACTIVATION_ENTERED_LOCAL | exact owner | exact done claim、watchers null | exact entered | built | null | EMPTY、保持 |
| ACTIVE | exact owner | null | exact entered | built | null | EMPTY、claim/watchers clear |
| ACTIVE_POSTCHECK_UNKNOWN | exact owner | done claimまたはnull | exact entered | built | null | POSTCHECK_UNKNOWN、全て保持 |
| CLEANUP_PENDING | exact owner | null | exact exited | built | exact live release | EMPTY、全て保持 |
| INVALIDATED_PRECLAIM | null | null | null | null | null | T536 observationなし。T533 invalid receipt/lineageだけを保持し、unclaimed lane cleanupは別gate |
| INVALIDATED_RETURNED_NON_GRANT | null | null | null | null | null | secret-free `CLAIM_RETURNED_NON_GRANT` entry kindだけをhandoffし、owner graph clear |
| INVALIDATED_CANCELLED_NON_GRANT | null | null | null | null | null | secret-free `CLAIM_CANCELLED_NON_GRANT` entry kindだけをhandoffし、owner graph clear |
| INVALIDATED_CANCELLED_ABANDONED | null | null | null | null | null | secret-free `CLAIM_CANCELLED_ABANDONED` entry kindだけをhandoffし、owner graph clear |
| INVALIDATED_AFTER_RELEASE | null | null | null | null | null | secret-free `GRANTED_CLEAN_RELEASE` entry kindだけをhandoffし、owner graph clear |
| CLAIM/CLEANUP_UNKNOWN | exact owner | exact done/cancelled/live taskまたはnull | nullまたはexact exited | 構築済み分だけ | exact done/live releaseまたはnull | 対応UNKNOWN、全て保持 |
| ABORT_CONFLICT_HOLD | exact owner | null | exact exited | built | exact done release | CLEANUP_TERMINAL、全て保持 |
| CLAIM_ABORT_CONFLICT_PRECLAIM | null | null | null | null | null | T536 owner/observationなし。exact T533 conflict hold refsだけ保持 |
| CLAIM_ABORT_CONFLICT_AFTER_CLAIM | exact owner | exact done claim | nullまたはexact exited | 構築済み分だけ | doneまたはnull | entry対応terminal observation、全て保持 |
| I2_POSTCHECK_UNKNOWN_PRECLAIM | null | null | null | null | null | T536 owner/observationなし。exact T533 postcheck refsだけ保持 |
| I2_POSTCHECK_UNKNOWN_AFTER_CLAIM | exact owner | exact done claim | nullまたはexact exited | 構築済み分だけ | doneまたはnull | entry対応terminal observation、全て保持 |
| I3_POSTCHECK_UNKNOWN | exact owner | null | exact exited | built | exact done release | CLEANUP_TERMINAL、全て保持 |

表外のnull、live/done逆転、foreign context/candidate、owner slotだけのclearを拒否する。clean終了時以外に
strong refsを部分解放しない。

## 7. raceとatomicity

- **stop/cancel vs claim**: watcherがclaim taskをcancelし、broker control収束を待つ。exact terminalが
  証明できた場合だけI2へ進み、そうでなければCLAIM_RESULT_UNKNOWN。
- **freshness vs GRANTED**: post-claim再読がstaleならactivateせずrelease。release clean後だけI2。
- **retire vs ACTIVE CAS**: 同一owner loopのno-await CASで直列化する。先にRを置換した側だけ勝ち、
  loserは再CASしない。
- **activation enter vs CAS**: 間にawait/callbackを置かない。CAS failureは同taskでexitしてからrelease。
- **ACTIVE vs stop**: stopは同期的にcellを書き換えず、exact ownerのasync cleanup taskを一つ選び、
  5.2節のabsolute cleanup deadlineまでshieldして完了またはUNKNOWNを記録する。通常T533の同期retire
  helperをACTIVEへ流用しない。
- **session close**: claimed/activated中のcloseはABANDON/session-lost shapeを観測し、clean terminalを
  証明できなければUNKNOWN。PID、timeout、transport closeだけをclean proofにしない。
- **same-value replacement**: new lease/capture/receipt/cell、foreign identity、dead current weakrefを拒否。

semantic publishはstore cell一参照交換と固定済みstate参照代入だけである。Future通知、hash、repr、
allocation、network I/Oはpublish区間へ入れない。

## 8. offline acceptance

actual `BrokerAdmissionSession`と同じobservable fields/control semanticsを持つ有限mockを使う。
provider、generation、LLM、model、DLL、server、GPU、game、Actionsは0回である。

### 8.1 positive

| ID | 条件 | 期待 |
|---|---|---|
| P1 | exact R、claim GRANTED、freshness current | activate enter一回、A revision 2を一回publish |
| P2 | GRANTED後deadline stale | activate 0、release一回、clean後I2 |
| P3 | claim CANCELLED/EXPIRED/POISONED/UNAVAILABLE | activate/release 0、terminal shape確認後I2 |
| P4 | ACTIVEからscope end cleanup | exit一回、release一回、I3 revision 3 |
| P5 | post-notification T533形 | 元pending/driver nullでもexact Rから新ownerを発行できる |
| P6 | 全positive | provider/request/generate/audit/delivery/action count 0 |
| P7 | release `None`、lane disposition RELEASED/EXPIRED/POISONED各形 | 戻り値を使わずpost-release matrixだけでclean判定 |
| P8 | claim task cancel後にCLAIM applyが許可non-GRANTへ収束 | ABANDON 0、`CLAIM_CANCELLED_NON_GRANT` observationとexact terminal後I2 |
| P9 | claim task cancel後にCLAIM applyがGRANTED、内部ABANDONがCANCELLED/EXPIRED/POISONEDへ収束 | ABANDON 1、`CLAIM_CANCELLED_ABANDONED` observationとack shape後I2 |

### 8.2 negative

| ID | 変異/race | 期待 |
|---|---|---|
| N1 | public lease/receipt copy、foreign port/store/session/lane | claim 0、store不変 |
| N2 | current cell same-value replacement、identity再利用、dead weakref | claim 0、store不変 |
| N3 | lane disposition/callers/lease backlink、session claimed/activatedを1 field変更 | claim 0 |
| N4 | capture/hash/deadline/world/seq/fact/phase/action/connectionを1 field変更 | claim前ならI2、GRANTED後ならclean release後I2 |
| N5 | claim二回、owner二回発行、foreign taskでclaim入口 | 拒否、broker call増加0 |
| N6 | claim待機中cancel、GRANTEDとの両順序 | exact terminalならI2、mixedならCLAIM_RESULT_UNKNOWN |
| N7 | GRANTED後`activate()` allocation failure、enter failure | release一回、clean後I2 |
| N8 | enter後ACTIVE CAS conflict | exit一回、release一回、cleanならconflict hold、rollbackなし |
| N9 | ACTIVE postcheckのstore/session/lane/leaseを1 field変更 | ACTIVE_POSTCHECK_UNKNOWN、provider 0 |
| N10 | release raise/timeout/cancel/unknown/mixed shape | corresponding cleanup UNKNOWN、再release 0 |
| N11 | ACTIVE cleanup二回、別reason candidate、同値別I3 | 拒否、CAS 0 |
| N12 | session close/reconnect/transport loss | clean proofなしはUNKNOWN、時刻推定なし |
| N13 | normal v1 invokeまたはT533未opt-in composition | 新入口到達0、既存動作不変 |
| N14 | backend.generateを試行 | 本scopeのcall count assertionで失敗、state進行0 |
| N15 | Event/coroutine/task/watcherの各生成位置で失敗 | owner slot null、start gate unset、作成済みtask全回収、claim 0、R不変 |
| N16 | release後UNAVAILABLEまたは各terminalのflag/tombstoneを1 field変更 | clean昇格せずCLEANUP_UNKNOWN |
| N17 | I2 publish前後のbundle/receipt/ticket/source/offer/compositionを1 field変更 | conflictまたはI2_POSTCHECK_UNKNOWN、再CAS/通知0 |
| N18 | cleanup candidate tuple順序、reason、selected identity、active receipt強参照を変更 | publish 0、same-value候補拒否 |
| N19 | observation各stateのnullable/value/failure codeを1 field変更 | row拒否、repairなし |
| N20 | watcher winner同時、cancel/gather deadline超過、claim task live | CLAIM_UNKNOWN、task refs保持、外側control 0 |
| N21 | cancelled GRANTED後ABANDONがUNAVAILABLE/session lost/raise/timeout/mixed | CLAIM_RESULT_UNKNOWN、I2/CAS 0、owner refs保持 |
| N22 | direct/cancelled/abandonedのtask outcome、failure code、ABANDON count、terminal shapeを別subrowと混合 | row拒否、近いterminalへの補完なし |

全caseでclaim、activate、release、CASの各countを分母から落とさず記録する。UNKNOWNはFAILやcleanへ
再分類しない。

## 9. 実装・review gate

実装対象はprivate claim/activate module、T533 composition/arbiter/storeのprivate slotとCAS、有限mock
testに限定する。公開API、protocol、schema、server、brain生成経路は変更しない。

実装開始条件は本designの独立APPROVED、T533 approval bindingとcell amendment bindingのhash一致で
ある。実装後はfocused test、T519/T521/T524/T525/T533回帰、対応Python版、check_docs、diff検査、
独立tool reviewを行う。

本designのAPPROVEDはprovider実行可能、ACTIVE以後のgeneration実装済み、PF3 PASS、実game許可を
意味しない。次段はexact `ActiveCaptureOwnerReceiptV2`を受けるattempt/request設計を別gateで定義する。
