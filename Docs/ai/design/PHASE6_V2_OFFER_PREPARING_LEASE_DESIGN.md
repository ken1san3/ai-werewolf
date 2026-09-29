# Phase 6 v2 OFFER ownership → PREPARING lease 限定詳細設計

Status: APPROVED
Task: T522
Parent: `Docs/ai/design/PHASE6_V2_CAPTURE_BINDING_DESIGN.md` 5.1/5.3/6節
Baseline: `Docs/ai/handoffs/tasks/T519_APPROVAL_BINDING.json`

## 1. 目的と境界

本単位は、actual `BrokerAdmissionSession`が受信した最初のOFFERとexact control lane/lease/requestを、T519で承認済みのactual
context/source/World/store owner chainへ結び、`DiscussionStateStore`所有の`StateGenerationLeaseV2(status="PREPARING")`を1件だけ置く。

出力はPREPARING placeholderとprivate owner receiptまでである。`RESERVED`、final `GenerationCaptureV2`、projection/hash確定、broker claim、activate、
provider call、attempt ledger、第2segment、durable audit、delivery、state leaseのrecovery/RELEASED遷移は作らない。公開`AdmissionResult`、invocation ID文字列、caller構築lease、
structural simulatorをbroker authorityとして扱わない。通常v1、既存logical clock、timeout、token見積り、T519 material/hash/sourceを変更しない。
既存`GenerationLease.claim/activate/release`の契約・実装も変更しない。

## 2. exact compositionとsource

### 2.1 `OfferPreparingCompositionV2`

v2 runtime compositionだけが、session接続・T521 owner registrationの`STORE_ATTACHED`後、OFFER待機前に次を一回発行する。

```text
OfferPreparingCompositionV2 = opaque {
  exact_broker_session,
  exact_owner_registration,
  exact_runtime_source, exact_world,
  exact_discussion_store,
  exact_discussion_transaction,
  exact_authority_capture_bridge,
  exact_context_receipt,
  exact_arbiter, exact_caller_port,
  exact_clock_callable,
  owner_thread_loop,
  lifecycle: ACTIVE | RETIRED,
  initial_invocation_slot: EMPTY | PENDING_SELECTED | TICKET_ACTIVE |
                           PREPARING_HELD | CLEANUP_UNKNOWN,
  flow_state: IDLE | SOURCE_REGISTERED | ACQUIRE_REGISTERED |
              OFFER_READY_UNNOTIFIED | OFFER_RESOLVED_REGISTERED | OFFERED |
              PREPARING | OFFER_CLEANUP_PENDING | OFFER_CLEANUP_GRANTED |
              OFFER_RELEASE_PENDING |
              OFFER_CLEANUP_UNKNOWN | OFFER_NOTIFICATION_UNKNOWN |
              PREPARING_TERMINAL_HELD,
  active_initial_ticket_or_null,
  active_offer_source_or_null,
  prebuilt_pre_ticket_abort_bundle,
  issuer_capability
}
```

発行時にexact concrete `BrokerAdmissionSession`、session未close/transport未close/reader task未完了、claimed/activated invocationともnull、
active result/control/generation registryなし、T521 registration ACTIVE/STORE_ATTACHED、registrationのexact
source/World/store/context receipt、bridgeのexact registration/ports/store、caller portとclockのruntime wiringを検査する。session、store、caller portへ
同じcomposition backlinkをno-await/no-callback/non-raising区間で一回だけpublishする。事前失敗では全slot null、成功後は全slot exactである。
別session/store/clockへの差替え、二重bind、同じstoreを別sessionへbindする操作を拒否する。v1 session/storeにはbacklinkを作らない。

ここでactual callerは`BrainInvocationArbiter`である。private `OfferPreparingCallerPortV2`はexact arbiterを閉包し、
`arbiter._admission is exact_broker_session`、`arbiter.controller.world is exact_world`、
`type(arbiter.controller._discussion) is DiscussionTransaction`、`arbiter.controller._discussion.state is exact_discussion_store`、
transaction未close、`arbiter._clock is exact_clock_callable`を発行時と各使用時に検査する。実在しない`controller.discussion_state`や新public propertyを作らない。
portは通常`GenerationAdmission` interfaceへ公開せず、exact arbiterのinitial acquire箇所だけが呼べる。public `AdmissionRequest`/
`AdmissionResult`を受け取る任意caller、同値arbiter、別clockからの呼出しはissuer capabilityを得られない。

本単位のprivate offline入口は
`caller_port.acquire_and_prepare_initial_v2(exact_initial_ticket) -> StateGenerationLeaseV2`とする。`AdmissionRequest`やdeadlineを引数に取らない。内部でowned acquire、
OFFER receipt取得、freshness確認、PREPARING CASまでを行い、public `AdmissionResult`やclient leaseをcallerへ返さない。通常arbiter driverの
OFFER後capture/claim経路へ合流せずPREPARING snapshotで停止する。この入口以外からrequest/resultを後付けしてPREPARINGへ変換できない。

composition発行はT519/T521の明示offline opt-in factoryで作られたowner graphへだけ追加する。通常`connect_phase6`が作るv1
event source/session/arbiter wiringには発行しない。実装は`runtime.py`のauthority bridge作成後かつarbiter作成直後に、exact session/registration/
bridge/arbiter/clockを一度束縛するprivate helperとする。このT522単位を実装してもfeature start、OFFER送信、PREPARING作成を自動実行せず、
 focused offline wiringから明示して初めて有効になる。

composition bind時のinitial slotはEMPTYである。T522 offline modeの`_invoke_admitted`はarbiter lock下で最初のexact pendingだけを
`EMPTY -> PENDING_SELECTED`として登録し、同じpendingを通常pending mapへ一回置く。slotがEMPTY以外のinvoke、同時の別owner/pendingは
pending mapへ入れる前に明示errorで有限終了する。clean retire後だけ新しいopaque slot identityを作ってEMPTYへ戻せる。PREPARING/UNKNOWN holdでは戻さない。
composition bind時にpre-ticket failure resultと元のEMPTY slot identityをabort bundleとして構築する。bind candidateのallocation失敗では
composition自体を発行しない。

### 2.2 initial offer source

exact admission driverだけがOFFER待機直前にarbiter lock下で次のone-shot ticketを登録する。

```text
InitialOfferTicketV2 = opaque {
  exact_composition, exact_caller_port, exact_arbiter,
  exact_pending_invocation, exact_owner,
  exact_dispatch_deadline, exact_admission_driver_task,
  exact_initial_slot,
  exact_request,
  exact_offer_task_or_null,
  state: ACTIVE | CONSUMED_SOURCE | CONSUMED_PREPARING |
         RETIRED_CLEAN | RETIRED_UNKNOWN,
  issuer_capability
}
```

ticket発行条件は、`arbiter._active is exact_pending_invocation`、`_pending`空、`_admission_driver is current_task()`、
`_admission_state == "WAITING_ADMISSION"`、`_admission_wait_task/_admission_lease/_suspended_reaction/_attached_successor`が全てnull、
pendingのresult未完了・`execution_complete`未set・cancel false・`admission_invocation_id` null、composition flow IDLE、initial slotが
exact pendingを保持するPENDING_SELECTEDである。
exact pendingのowner、allowed handles、deadline object、initial slotを候補へ固定した後、v2 issuerは副作用なしの
`build_initial_request_candidate_v2(pending, invocation_id_candidate)`でinvocation IDと`AdmissionRequest`を構築する。既存v1
`_new_admission_request`は変更せず、v2候補生成中は`pending.admission_invocation_id`をmutateしない。invocation ID factoryの失敗やID消費は
authority publishではなく、後続candidate失敗時にそのIDを再利用しない。

ticket、request、後述terminal candidate bundleを全て構築して最終再検査した後、`pending.admission_invocation_id`、arbiterのactive ticket slot、
composition initial slot `TICKET_ACTIVE`、admission state `V2_INITIAL_REGISTERED`をno-await/no-callback/non-raising lock区間で同時publishする。
候補構築失敗ではpending IDを含む全slotが旧値のままである。caller supplied request、同値別pending/deadline/requestは入力にできない。

active ticket発行後は、同じarbiter/sessionの通常initial acquire、別owner invoke、replacement、successor、claim pathを拒否する。
driverは通常分岐へ戻らずticketをexact caller portへ一回渡す。portは`ACTIVE -> CONSUMED_SOURCE`とsource登録を同時に行い、
二重使用を拒否する。

ticket候補と同時に次の全terminal候補を構築する。後続transitionはこのbundle内objectだけを使い、terminal後にresultやnew EMPTY slotをallocateしない。

```text
OfferPreparingTerminalCandidatesV2 = opaque {
  preparing_complete: existing CANCELLED BrainDispatchResult,
  stale: existing STALE BrainDispatchResult,
  deadline_suppressed: existing DEADLINE_SUPPRESSED BrainDispatchResult,
  admission_terminals: closed map[
    REPLACED | EXPIRED | OVERLOADED | UNAVAILABLE | CANCELLED | POISONED
      -> existing _admission_terminal result
  ],
  internal_failure: BRAIN_FAILED(error_type="OfferPreparingFailureV2"),
  cleanup_unknown: BRAIN_FAILED(error_type="OfferCleanupUnknownV2"),
  next_empty_slot_identity
}
```

bundle allocation失敗はticket/pending ID publish前である。composition bind時のabort bundleを使い、arbiter lock下でpending map/active selectionをclearし、
元のEMPTY slotを戻し、pre-ticket failure resultへresolveする。ここでも新規allocationしない。terminal candidate bundleが完成した段階だけを
ticket publish可能点とする。

```text
InitialOfferSourceV2 = opaque {
  exact_composition, exact_caller_port, exact_arbiter,
  exact_request, exact_dispatch_deadline,
  exact_control_lane_or_null, exact_result_future_or_null,
  exact_client_lease_or_null, exact_offer_receipt_or_null,
  exact_initial_ticket,
  state: SOURCE_REGISTERED | ACQUIRE_REGISTERED | OFFER_READY_UNNOTIFIED |
         OFFER_RESOLVED_REGISTERED |
         OFFERED | PREPARING | CLEANUP_PENDING | CLEANUP_GRANTED |
         RELEASE_PENDING | CLEANUP_UNKNOWN | NOTIFICATION_UNKNOWN |
         RETIRED_CLEAN | TERMINAL_HELD
}
```

本単位は通常`acquire`の最初のsegmentだけを対象とし、`reserve_successor`、successor ID、claim済みsegmentを入力に取らない。waiting replacementは
本単位ではauthority化せず、v2 callerがreplacementへ移る場合は旧sourceをclean terminalへ閉じ、新requestで別のinitial flowを最初から作る後続scopeとする。

source登録では、ticket/request/pending object identity、invocation ID、priority、phase/day/action generation/mapping order/deadlineとexact
pending `DispatchDeadline`を照合する。
deadlineのconnection generationとdiscussion triggerはrequestに無いため捏造せず、exact DispatchDeadline objectから保持する。composition ACTIVE/idle、
session openかつcomposition backlink exact、claimed/activated invocation null、storeにactive state leaseなし、同じinvocation IDのlane/result/terminal tombstoneなしを
検査する。source候補構築後、composition active slotへ一回publishし、
sessionのprivate owned-acquire入口だけがsourceを入力としてexact requestからcontrol lane/result future候補を先に構築する。session dictionariesとsourceの
lane/future refsをno-await/no-callback/non-raising区間で同時publishして
`SOURCE_REGISTERED -> ACQUIRE_REGISTERED`へ進める。通常`session.acquire(request)`のpublic resultだけではsource proofを得られない。
候補構築・ENQUEUE送信前の失敗ではsession registry 0件のままsourceをclean retireする。publish後のsend/cancel/transport失敗は既存control laneを使って
terminal確認し、不明なら同じsource/ticketを保持する。laneだけ、futureだけを残すpartial registrationは許さない。

### 2.3 OFFER receipt issuer

session `_route_frame`は既存protocol validation後、public resultをresolveする直前に次を検査する。

- raw parsed frameはexact keys `invocation_id/protocol/queue_wait_microseconds/type`で、typeはOFFERである。
- invocation IDはactive source、exact request、exact `_ControlLane`、exact result futureの全てに一致する。
- laneは未terminal、leaseなし、同じsessionに登録済みである。
- session/composition/sourceはACTIVEで、session close/transport failureは無い。

成功時は、exact `_ClientGenerationLease`、private `BrokerOfferOwnershipReceiptV2`、public `AdmissionResult(OFFERED, lease, wait)`、
publish後のexact return tuple、cleanup用empty sentinelを全て候補として先に構築する。候補間identity、future未完了、session registry/lane/source/ticketの
旧shapeを最後に再検査する。`Future.set_result`をnon-raisingとは仮定せず、semantic owner publishとnotificationを次の3段階へ分ける。

1. no-await/no-callback/non-raising semantic区間でlane lease、source receipt/lease、lease receipt backlinkをpublishする。ただしreceiptは
   `PENDING_NOTIFY`、source/compositionは`OFFER_READY_UNNOTIFIED`でconsume gateを閉じ、PREPARING authorityとして使用不能にする。
2. exact prebuilt `AdmissionResult`で`future.set_result`を1回だけ呼ぶ。これはfallible notification区間であり、Task wakeup callbackのschedule/
   Handle allocationをsemantic publishへ含めない。
3. `set_result`が正常returnした場合だけ、callbackが実行されない同一event-loop tick内のnon-raising区間でreceiptをACTIVE、source/compositionを
   `OFFER_RESOLVED_REGISTERED`へ進める。その後route関数がreturnして初めてwaiter callbackを実行可能とする。

`set_result`がraiseした場合はfutureがpending/doneのどちらでもrollback・再通知せず、観測shapeをreceipt/sourceへ固定し、receipt/ticketを
`RETIRED_UNKNOWN`、source/composition/initial slotを`OFFER_NOTIFICATION_UNKNOWN`/`CLEANUP_UNKNOWN`へ移す。lease/receipt/result/future/laneの
strong refを保持し、consume/cancel retry/new invoke/PREPARING/providerをblockする。通知failure後のwaiter wakeを成功扱いせず、runtime closeの既存
owner cancellation以外で再開を推測しない。部分scheduleされたwaiterが後で再開してもsource/ticket UNKNOWN gateで停止し、OFFERED/PREPARINGへ進めない。
duplicate/unowned OFFERではsemantic publish前に既存protocol failureへ閉じる。

receiptはsession、source、request、raw frame object、future、lane、lease、queue wait、offer観測時刻、compositionをexact identityで保持する。

publish前だけ存在するprivate candidateのclosed shapeは次であり、外部へserialize/logしない。

```text
OfferPublishCandidatesV2 = {
  exact_client_lease,
  exact_offer_receipt,
  exact_admission_result,
  exact_owned_completion_tuple: (exact_admission_result, exact_offer_receipt),
  exact_no_offer_cleanup_sentinel,
  preallocated_notification_failure_observation_slot
}
```

owned acquireはOFFERED時にexact completion tuple、OFFER前terminal時に事前構築済みcleanup sentinelのどちらかだけを内部portへ返す。

owned acquire coroutineが再開すると、future result identityを照合し、既存`finally`でsession `_results[invocation_id]`だけをpopする。source/receiptはdone futureと
resultを保持し、laneはsession registryへ残す。この非例外transitionで`OFFER_RESOLVED_REGISTERED -> OFFERED`へ進める。future/result pop後に
再allocation、別result構築、authority再発行を行わない。pop前後のshapeは3.1節のsession行列を正本とする。

public `AdmissionResult`や`GenerationLease` Protocolだけからreceiptを再構成・取得できない。exact caller portがsourceとreturned exact leaseを照合した場合だけ、
receiptを次のPREPARING issuerへ渡せる。

## 3. 全state×field matrix

### 3.1 offer source / receipt / composition

次のflow行列とsession行列を合わせて正本とし、表外のnull/exact組合せ、逆遷移、別object差替えを拒否する。`cleared`はretired objectの
非秘密terminal identity以外のstrong refをclear済み、`held`は不明解消用の既知refを保持、を表す。

| flow行 | lifecycle / flow | initial slot | ticket | active source / source state | lease / receipt | store lease | call counter `(cancel,release,claim,activate,provider)` | refs |
|---|---|---|---|---|---|---|---|---|
| IDLE | ACTIVE / IDLE | EMPTY | null | null / none | null / none | null | `(0,0,0,0,0)` | owner backlinksだけ |
| PENDING_SELECTED | ACTIVE / IDLE | PENDING_SELECTED | null | null / none | null / none | null | `(0,0,0,0,0)` | exact pendingだけ保持 |
| TICKET_ACTIVE | ACTIVE / IDLE | TICKET_ACTIVE | ACTIVE | null / none | null / none | null | `(0,0,0,0,0)` | exact pending/request/deadline/driver保持 |
| SOURCE_REGISTERED | ACTIVE / SOURCE_REGISTERED | TICKET_ACTIVE | CONSUMED_SOURCE | exact / SOURCE_REGISTERED | null / none | null | `(0,0,0,0,0)` | ticket/source相互identity |
| ACQUIRE_REGISTERED | ACTIVE / ACQUIRE_REGISTERED | TICKET_ACTIVE | CONSUMED_SOURCE | exact / ACQUIRE_REGISTERED | null / none | null | `(0,0,0,0,0)` | lane/future保持 |
| OFFER_READY_UNNOTIFIED | ACTIVE / OFFER_READY_UNNOTIFIED | TICKET_ACTIVE | CONSUMED_SOURCE | exact / OFFER_READY_UNNOTIFIED | exact unclaimed / PENDING_NOTIFY | null | `(0,0,0,0,0)` | consume gate閉、future pending |
| OFFER_RESOLVED_REGISTERED | ACTIVE / OFFER_RESOLVED_REGISTERED | TICKET_ACTIVE | CONSUMED_SOURCE | exact / same | exact unclaimed / ACTIVE | null | `(0,0,0,0,0)` | result futureはsession登録中 |
| OFFERED | ACTIVE / OFFERED | TICKET_ACTIVE | CONSUMED_SOURCE | exact / OFFERED | exact unclaimed / ACTIVE | null | `(0,0,0,0,0)` | done future/resultをsource保持 |
| PREPARING | ACTIVE / PREPARING | PREPARING_HELD | CONSUMED_PREPARING | exact / PREPARING | exact unclaimed / CONSUMED_PREPARING | exact PREPARING | `(0,0,0,0,0)` | hidden owner receiptが全source保持 |
| OFFER_CLEANUP_PENDING | ACTIVE / OFFER_CLEANUP_PENDING | TICKET_ACTIVE | CONSUMED_SOURCE | exact / CLEANUP_PENDING | exact unclaimed / ACTIVE cleanup-owned | null | `(1,0,0,0,0)` | cancel terminal ack待ち |
| OFFER_CLEANUP_GRANTED | ACTIVE / OFFER_CLEANUP_GRANTED | TICKET_ACTIVE | CONSUMED_SOURCE | exact / CLEANUP_GRANTED | exact internally claimed / ACTIVE cleanup-owned | null | `(1,0,0,0,0)` | cancel GRANTED exact state、provider 0 |
| OFFER_RELEASE_PENDING | ACTIVE / OFFER_RELEASE_PENDING | TICKET_ACTIVE | CONSUMED_SOURCE | exact / RELEASE_PENDING | exact internally claimed / ACTIVE cleanup-owned | null | `(1,1,0,0,0)` | release terminal ack待ち |
| OFFER_CLEANUP_UNKNOWN | ACTIVE / OFFER_CLEANUP_UNKNOWN | CLEANUP_UNKNOWN | RETIRED_UNKNOWN | exact held / CLEANUP_UNKNOWN | held / RETIRED_UNKNOWN | null | `(1,0|1,0,0,0)` | 全新規block |
| OFFER_NOTIFICATION_UNKNOWN | ACTIVE / OFFER_NOTIFICATION_UNKNOWN | CLEANUP_UNKNOWN | RETIRED_UNKNOWN | exact held / NOTIFICATION_UNKNOWN | held / RETIRED_UNKNOWN | null | `(0,0,0,0,0)` | wake shape不明、全新規block |
| PREPARING_TERMINAL_HELD | ACTIVE / PREPARING_TERMINAL_HELD | PREPARING_HELD | CONSUMED_PREPARING | exact held / TERMINAL_HELD | held / CONSUMED_PREPARING | exact PREPARING | `(0,0,0,0,0)` | successor recoveryまでblock |
| RETIRED_CLEAN_CANCEL | ACTIVE / IDLE | EMPTY（new identity） | RETIRED_CLEAN | null / RETIRED_CLEAN cleared | clear / RETIRED_CLEAN | null | `(1,0,0,0,0)` | exact cancel terminal identityだけ保持可 |
| RETIRED_CLEAN_RELEASE | ACTIVE / IDLE | EMPTY（new identity） | RETIRED_CLEAN | null / RETIRED_CLEAN cleared | clear / RETIRED_CLEAN | null | `(1,1,0,0,0)` | exact release terminal identityだけ保持可 |
| COMPOSITION_RETIRED clean | RETIRED / IDLE | EMPTY | nullまたはRETIRED_CLEAN | null / noneまたはcleared | clear | null | closed terminal counter | retired owner identityだけ保持 |
| COMPOSITION_RETIRED held | RETIRED / CLEANUP_UNKNOWN、NOTIFICATION_UNKNOWNまたはTERMINAL_HELD | CLEANUP_UNKNOWNまたはPREPARING_HELD | RETIRED_UNKNOWNまたはCONSUMED_PREPARING | exact held | held | nullまたはexact PREPARING | 直前値固定 | cleanup/recoveryまで保持、再bind不可 |

sessionのexact field shapeは次表を正本とする。`results`と`tombstone`は当該invocation IDについてのmembershipである。

| session行 | `_results` / held future | `_control_lanes` / lane fields | lease fields | claimed/activated | tombstone |
|---|---|---|---|---|---|
| IDLE / TICKET / SOURCE_REGISTERED | absent / null | absent | null | null/null | absent |
| ACQUIRE_REGISTERED | exact pending future / same | exact lane、lease null、disposition null、callers 0 | null | null/null | absent |
| OFFER_READY_UNNOTIFIED | exact pending future / same | exact lane、exact lease、disposition null、callers 0 | unclaimed/unreleased/unretired、receipt PENDING_NOTIFY | null/null | absent |
| OFFER_RESOLVED_REGISTERED | exact done future / same exact result | exact lane、exact lease、disposition null、callers 0 | unclaimed/unreleased/unretired | null/null | absent |
| OFFERED / PREPARING | absent / sourceがdone future+exact result保持 | exact lane、exact lease、disposition null、callers 0 | unclaimed/unreleased/unretired | null/null | absent |
| CANCEL_PENDING | absent / source保持 | exact lane、exact lease、disposition null、callers 1 | unclaimed/unreleased/unretired | null/null | absent |
| CANCEL_CLEAN_ACKED | absent / source保持 | lane absent、旧lane.lease null、旧lane.disposition exact `CANCELLED|EXPIRED|POISONED`、callers 0 | retired | null/null | same exact terminal |
| CANCEL_GRANTED | absent / source保持 | exact lane、exact lease、disposition `GRANTED`、callers 0 | claimed true、released false | exact invocation/null | absent |
| RELEASE_PENDING | absent / source保持 | exact lane、exact lease、disposition `GRANTED`、callers 1 | claimed true、released false | exact invocation/null | absent |
| RELEASE_CLEAN_ACKED | absent / source保持 | lane absent、旧lane.lease null、旧lane.disposition `RELEASED|EXPIRED|POISONED`、callers 0 | released true、EXPIREDならretired true | null/null | RELEASEDはabsent、他はsame exact terminal |
| NOTIFICATION_UNKNOWN | exact registryにpending/done future、またはcallbackが走った後のabsent / sourceはexact futureと観測resultを保持 | exact lane、exact staged lease、disposition null、callers 0 | unclaimed/unreleased/unretired、receipt RETIRED_UNKNOWN | null/null | absent |
| CLEANUP_UNKNOWN | absent / source保持 | 観測済み最終shapeを保持し修復しない | exact既知flags | nullまたはexact既知値、activatedは常にnull | absentまたはexact既知値 |

cancel replyは`CANCELLED/EXPIRED/POISONED`だけをclean、`GRANTED`だけを既存releaseへ進む分岐とする。`UNAVAILABLE/OVERLOADED/REPLACED`、
timeout、transport close、protocol mismatch、遅延/重複ACKはUNKNOWNである。release replyは`RELEASED/EXPIRED/POISONED`だけをclean、
`UNAVAILABLE`、timeout、transport close、protocol mismatch、遅延/重複ACKはUNKNOWNである。clean判定は上表のsession field全てを照合した後だけ行い、
当該invocationのtombstoneを同一ID再利用の根拠にしない。

clean retireの同一区間でcompositionのactive ticket/source slotをnullにし、terminal bundleで事前構築した`next_empty_slot_identity`をpublishする。
old ticket/source/receiptは非秘密terminal identity以外の
pending/request/deadline/task/future/lane/lease/result/raw frame strong refをclearする。UNKNOWNとPREPARINGはactive slotとstrong refをclearしない。
active slotをnullにしただけでold objectをACTIVEへ戻すことはできない。

正常遷移は`IDLE -> PENDING_SELECTED -> TICKET_ACTIVE -> SOURCE_REGISTERED -> ACQUIRE_REGISTERED -> OFFER_READY_UNNOTIFIED ->
OFFER_RESOLVED_REGISTERED -> OFFERED -> PREPARING`だけである。notification failureだけは`OFFER_READY_UNNOTIFIED -> OFFER_NOTIFICATION_UNKNOWN`へ閉じる。
owned acquire publish前の失敗はclean retire、OFFER後PREPARING前の失敗は
`OFFERED -> OFFER_CLEANUP_PENDING -> RETIRED_CLEAN_CANCEL | OFFER_CLEANUP_GRANTED -> OFFER_RELEASE_PENDING -> RETIRED_CLEAN_RELEASE`
または任意のcleanup段階から`OFFER_CLEANUP_UNKNOWN`である。PREPARING後のexpiry/cancel/session close/owner retireはplaceholderを消さず
`PREPARING -> PREPARING_TERMINAL_HELD`とする。本単位はPREPARINGをRELEASED/INVALIDATEDへ移さない。

正常PREPARING経路では`_ClientGenerationLease.claim/activate/release` callは全て0である。publish前失敗cleanupだけcancel callをexactly 1回許し、
cancel=`GRANTED`の場合だけ既存`lease.release()` callをexactly 1回要求する。cleanup releaseをPREPARING、claim権限、provider実行へ流用しない。
explicit `claim()`、`activate()`、provider callは全経路0であり、既存lease APIの契約を変更しない。

### 3.2 arbiter ticket lifecycle

ticket/source/session lifecycleとarbiterは次表の組合せだけを許す。active ticket中は`_invoke_admitted`のlock内入口で別pendingを登録せず、
callerを待機させず明示errorへ閉じる。通常driverはticket分岐後にreplacement/successor/capture/claimへ戻らない。

| arbiter段階 | exact active/pending | admission state / wait task | pending result / execution | ticket gate |
|---|---|---|---|---|
| ticket発行前 | `_active is pending`、`_pending`空 | `WAITING_ADMISSION` / null | unfinished / clear | null |
| ticket/source | same exact active、別pending禁止 | `V2_INITIAL_REGISTERED` / null | unfinished / clear | ACTIVEまたはCONSUMED_SOURCE |
| acquire待機 | same exact active | `V2_WAITING_OFFER` / exact owned acquire task | unfinished / clear | CONSUMED_SOURCE、task exact |
| OFFER処理後 | same exact active | `V2_PREPARING` / exact done task、次いでnull | unfinished / clear | CONSUMED_SOURCE |
| PREPARING成功 | finallyまでsame active、以後null | `V2_PREPARING_HELD` / null | exact既存`CANCELLED` resultへ1回resolve、finallyでexecution set | CONSUMED_PREPARINGを保持 |
| cleanup clean | finallyまでsame active、以後null | cleanup中専用state、完了後`IDLE` / null | exact失敗原因に対応する既存terminal resultへ1回resolve、execution set | RETIRED_CLEAN、active slot clear |
| cleanup unknown | finallyまでsame active、以後null | `V2_CLEANUP_UNKNOWN` / null | redacted `BRAIN_FAILED(error_type="OfferCleanupUnknownV2")`へ1回resolve、execution set | RETIRED_UNKNOWNを保持 |
| OFFER notification unknown | exact active/pending/taskを保持 | `V2_NOTIFICATION_UNKNOWN` / exact wait task | prebuilt cleanup_unknownで既存owner cancellationを待つ。wake成功を捏造しない | RETIRED_UNKNOWNを保持 |

PREPARING成功時の`CANCELLED`はoffline structural入口の終了sentinelであり、user cancel、broker cancel、state lease解放を表さない。
通常runtime/feature startへこのmodeを接続しないため製品判断結果には現れない。`_run_admitted` finallyは`V2_PREPARING_HELD`/
`V2_CLEANUP_UNKNOWN`を`IDLE`へ上書きせず、pending queueを再startしない。notification unknown以外では呼出元のawaitは必ず上表のresultで有限終了し、
`pending.result_consumed`と`execution_complete`は既存arbiter契約どおり確定する。composition/source/lease holdが残る間は新invokeを拒否する。

ただし`Future.set_result`自身のTask wakeup scheduleがraiseした`V2_NOTIFICATION_UNKNOWN`は、通知成功を仮定してpendingを完了扱いにしない。
prebuilt resultを保持し、runtime closeが既存owner cancellationを完了できたときだけterminalへ進む。close通知も成立しないprocess-level allocation failureでは
waiter有限wakeを証明せずUNKNOWN holdのままにする。この例外をPREPARING成功、clean retire、retry許可へ読み替えない。

cleanup clean後のpending resultは、deadline到達ならprebuilt `deadline_suppressed`、その他のcurrent mismatchならprebuilt `stale`、brokerが
OFFER前terminalを返した場合はprebuilt `admission_terminals[status]`、それ以外のtyped internal/session failureはprebuilt `internal_failure`へ固定する。
terminal時に`_offered_context_terminal`、`_admission_terminal`、`_admission_failure`を新規呼出ししてresultをallocateしない。cleanup操作自体の成功を
generation成功、NO_DECISION、SENTとして返さない。

### 3.3 `BrokerOfferOwnershipReceiptV2`

```text
BrokerOfferOwnershipReceiptV2 = opaque {
  exact_composition, exact_session,
  exact_offer_source, exact_request,
  exact_raw_offer_frame_object,
  exact_result_future, exact_control_lane, exact_client_lease,
  invocation_id, queue_wait_microseconds,
  offer_observed_monotonic, offer_observed_monotonic_us,
  notification_failure_observation_or_null: {
    future_done, exact_result_visible, session_results_member
  },
  state: PENDING_NOTIFY | ACTIVE | CONSUMED_PREPARING |
         RETIRED_CLEAN | RETIRED_UNKNOWN,
  issuer_capability
}
```

PENDING_NOTIFYはowner stagingだけを示しconsume不能である。ACTIVEかつcomposition/sourceがともにOFFEREDであるreceiptだけがPREPARINGへ一回consumeできる。cleanup移行と同時にprivate consume gateを閉じるため、
cleanup-owned ACTIVEはPREPARINGへconsumeできない。成功publishで`ACTIVE -> CONSUMED_PREPARING`となりstore hidden receiptへexact receiptを保持する。
clean terminalで`RETIRED_CLEAN`にしてraw frame/future/lane/request/deadlineへの不要refをclearする。不明時は`RETIRED_UNKNOWN`で既知refを保持し、
same process内の後続判断をblockする。same-value copy、foreign/stolen/reused receipt、別lease/source/sessionではconsumeできない。

### 3.4 `StateGenerationLeaseV2` PREPARING exact shape

storeが公開するsnapshotはimmutable dataで、broker authority proofではない。private `PreparingLeaseOwnerReceiptV2`だけがsource identityを保持する。

| field | PREPARING exact value/source |
|---|---|
| schema_version | `aiwolf.state-generation-lease.v2` |
| state_lease_id | exact offered `AdmissionRequest.invocation_id` |
| owner_invocation_id | same exact value |
| lease_revision | `0` |
| capture_id | null |
| status | `PREPARING` |
| base_revision / fact_revision | exact T519 prepared material + current store tuple |
| world_version / last_applied_sequence | exact T519 material + current World witness |
| phase_identity | exact T519 material |
| action_generation / connection_generation | exact material + DispatchDeadline/current actions |
| input/catalog/option/recipient/profile hashes | 全てnull |
| stage_schema_sha256s | empty tuple |
| expires_at_monotonic_us | exact deadlineの保守的microsecond導出値 |
| broker_lease_ids | `(state_lease_id,)` |
| accepted_plan_sha256 | null |
| stage_projections | empty tuple |
| reserved_attempt | null |
| attempt_ledger | empty tuple |
| recovery_proof | null |

`PreparingLeaseOwnerReceiptV2`はexact composition、store、T521 registration/context receipt、initial ticket/pending/driver、
offer receipt/source/session/lane/lease/request/deadline、
exact T519 prepared materialとhidden owner receipt、store capture tuple、World/current actions/transport deadline witness、raw deadline floatとderived usを保持する。
snapshot、owner receiptともPREPARING中はfield mutationしない。本単位にlease_revisionを増やすAPIは無い。

## 4. deadlineとfreshness source

### 4.1 時刻の意味

- OFFER `queue_wait_microseconds`はbrokerが返した待機telemetryであり、local expiry、経過補正、budget根拠に使わない。
- expiryの正本はexact `DispatchDeadline.not_after_monotonic`であり、exact `AdmissionRequest.not_after_monotonic`と一致させる。current
  `CurrentPhaseDeadline.local_deadline_monotonic`はserver由来の未guard deadlineなので同値を要求しない。mapping order/phase/day/connection/action
  generation一致、local deadline非null、かつdispatch cutoff `<=` current local deadlineを要求し、caller値による期限延長を許さない。
- OFFER観測時刻とCAS時刻はcompositionに束縛した、既存runtime/arbiterと同じclock callableからだけ読む。caller supplied `now`は受けない。
- `offer_observed_monotonic_us = ceil(offer_observed_monotonic * 1_000_000)`とし、raw値とともにreceiptへ固定する。clock値は有限・非負を要求し、
  CAS clockはraw offer観測値以上でなければならない。この観測値をexpiry算出には使わない。
- `expires_at_monotonic_us = floor(not_after_monotonic * 1_000_000)`とし、有限・非負・整数範囲を検査する。raw floatもprivate owner receiptへ保持する。
  T514どおり`ceil(clock() * 1_000_000) < expires_at_monotonic_us`のときだけ有効とし、整数化でdeadlineを延長しない。

OFFER frame自体はexpiryを持たないため、queue waitや受信時刻から期限を捏造しない。deadline不在/不一致、CAS時刻到達は`LEASE_INVALID`である。

### 4.2 synchronous freshness witness

PREPARING issuerはOFFER await後、以降await/callbackなしで次を行う。

1. exact bridgeの`prepare_current()`からprivate T519 materialを得る。
2. World snapshot/current actions/transport observations、store capture tuple/owner/lease slot、T521 registrationを読む。
3. materialのhidden owner receiptがexact bridge/World/store/capture readを指すことを検査する。
4. World CURRENT、version/sequence、phase/day、self、action/connection generation、current actions、context、base/fact revisionをmaterialへ照合する。
5. current transport deadlineのmapping order/phase/day/generationをexact DispatchDeadline/requestへ照合し、dispatch cutoffが非null current local
   deadline以下であることを検査する。
6. exact clockを読み、offer観測時刻以上かつ`ceil(now * 1_000_000) < expires_at_monotonic_us`を検査する。
7. 全candidate object/hash/derived usを構築後、同じWorld/store/offer/session tupleを再読し、exact一致を確認する。
8. store ownerの同期CASを行い、PREPARING snapshot/owner receipt、offer receipt CONSUMED、composition stateを一括publishする。
9. allocationを伴わないidentity readでpublish結果を確認する。

first/last World version、authority/current actions/transport world version、material world/seq、store tupleの1 fieldでも変化したらstaleとする。CASは
`DiscussionStateStore`の既存thread/loop ownerで行い、active lease slot null、current capture/material identity、staged/committed/dispatch/delivery等なしを要求する。
provider待ちやnetwork update待ちの間にstate lockを保持しない。

## 5. failure atomicity・retire・one-shot

### 5.1 PREPARING publish

PREPARING snapshot/owner receipt、publish確認tuple、prebuilt `preparing_complete` resultを含む全candidateを先に構築する。
candidate構築・全検証中はstore lease slotとreceipt stateを変更しない。成功時だけno-await/no-callback/non-raising区間で次を同時publishする。

```text
store._state_generation_lease_v2 = exact immutable PREPARING snapshot
store._preparing_lease_owner_receipt_v2 = exact private owner receipt
offer receipt.state = CONSUMED_PREPARING
initial ticket.state = CONSUMED_PREPARING
source.state = PREPARING
composition.flow_state = PREPARING
```

一部だけのpublish、同じoffer/materialの二重consume、同じstoreへの第2lease、同じinvocation IDの再利用を拒否する。publish後にhash、allocation、
fallible callbackを置かない。直後のarbiter pending terminalにはprebuilt `preparing_complete`だけを使う。public snapshotをcopyしてもowner receiptが無いためauthorityへ昇格できない。

### 5.2 publish前失敗

OFFER受信後のfreshness/deadline/material/CAS検査失敗ではstore leaseを置かず、source/composition flowを`OFFER_CLEANUP_PENDING`へ移し、
receiptは再consume不能なcleanup-owned ACTIVEとしてexact callerに
既存broker cancelを要求する。terminal ackが`CANCELLED/EXPIRED/POISONED`でlane/leaseもterminalと確認できた場合だけ`RETIRED_CLEAN`へ進みslot/refをclearする。
cancelが`GRANTED`を返した場合は、既存sessionが設定したexact claimed leaseを照合して`OFFER_CLEANUP_GRANTED`へ進み、providerを呼ばず既存broker
lease releaseをexactly 1回行い`OFFER_RELEASE_PENDING`とする。releaseの`RELEASED/EXPIRED/POISONED` replyと全session field一致だけをclean retireする。
cancel/releaseのtimeout、transport close、`UNAVAILABLE`、表外reply、ack/lane/lease不一致は`OFFER_CLEANUP_UNKNOWN`としてrefとblockを保持する。
同条件retryや別invocationへの置換を行わない。

clean/UNKNOWN transitionがpendingへ返すresultとclean後のnew EMPTY slotはticket発行前のterminal bundleから選ぶだけで、terminal ack後にallocateしない。
選択・最終session照合に失敗した場合はrefをclearせずUNKNOWNへ閉じる。

### 5.3 publish後terminal

PREPARING後にlocal expiry、cancel、session close、source/world/store close、registration retireを観測してもplaceholder/owner receiptを削除・rollbackしない。
compositionを`PREPARING_TERMINAL_HELD`へ移し、claim/activate/new capture/new offerを拒否する。broker cleanupが成功したように見えても、本単位はT514の
`PREPARING -> INVALIDATED -> RELEASED`を実装しないためPREPARINGを維持する。後続taskがexact terminal proofとstore CASを設計するまで自動解放しない。

store close時もprivate source refを即時破棄せず、cleanup不明を監査可能なprocess内owner receiptとして保持する。秘密値はrepr/log/prompt/public audit/
serializerへ出さない。clean release/recovery設計が消費するまでstrong refは1 active slot分だけで、複数offerを蓄積しない。

## 6. offline acceptance

actual `BrokerAdmissionSession`、socket fake broker、actual World/store/bridge、shared logical clockを用い、provider/LLM/game/Actionsは0とする。

### Positive

1. exact runtime compositionがsession/registration/source/World/store/bridge/caller/clock backlinkを一回発行する。
2. exact arbiter active pendingからpure request candidateとone-shot ticketを同時発行し、owned acquireのexact lane/futureへactual OFFERをrouteする。
   lease、private receipt、public result全候補を先に構築して一括発行する。
3. session内部のexact result futureは従来どおりpublic `AdmissionResult(OFFERED, ...)`へresolveされるが、private T522入口はそれを外部callerへ返さず、
   exact caller portだけがprivate receiptをPREPARING issuerへ渡す。
4. T519 material、World/current actions/transport deadline/store tupleが一致すると、1 PREPARING snapshotとowner receiptを原子的に置く。
5. PREPARING matrix全fieldが表どおりで、broker ID/owner/state lease IDはexact request invocation ID、revision 0、全未確定hashはnull/emptyである。
6. offer wait telemetry、raw deadline、observed/CAS clock、derived expiryの意味が分離され、既存deadlineを延長しない。
7. 正常PREPARINGではcancel/release/claim/activate/provider callが全て0であり、offline pendingは有限に`CANCELLED` sentinelへ閉じる。
8. v1 session/store/callerはbinding/receipt/PREPARINGを作らず既存acquire/claim動作が同値である。
9. Brain owner chainはactual `controller._discussion.state`でstore identityを照合し、new public propertyを追加しない。

### Negative

1. public `AdmissionResult`、invocation ID文字列、Protocol lease、fake/foreign session/lane/future/requestからreceiptやPREPARINGを作れない。
2. unowned/duplicate OFFER、別invocation、wrong exact keys、negative/non-int queue waitでpartial lease/receipt/public OFFERを発行しない。
   lease/receipt/result/return tuple/sentinelの各allocation失敗とfuture done直前変化では旧shapeのまま、`Future.set_result`/wakeup failureでは
   consume不能`OFFER_NOTIFICATION_UNKNOWN`、正常時だけ完全OFFERになる。ACTIVE receiptだけのpartial authorityは作らない。
3. foreign/stolen/reused receipt、same-value pending/request/deadline/material、structural material、別store/bridge/clockを拒否する。
4. requestとDispatchDeadlineのphase/day/action generation/mapping/deadline、deadlineとcurrent transportのconnection generationを各1 field変異して拒否する。
5. world/seq/phase/generation、base/fact revision、capture/material hidden receipt、session/lane/lease flagsの各1 field変異でpublish 0とする。
6. active ticket/PREPARING/offer sourceがある状態の第2判断、同じinvocation ID、double consumeを拒否する。driver active/wait task active、
   foreign pending、pending queue挿入、replacement/successor/別initial acquireも拒否し、待機callerを残さない。
7. 正常PREPARINGは全control call 0。publish前失敗はcancel exactly 1、GRANTEDの場合だけrelease exactly 1、claim/activate/providerは常に0である。
8. CAS直前のWorld/store/deadline/receipt変化でpartial placeholderを置かずcleanup pendingへ進む。
9. OFFER前後の`_results` membership、future done/result、lane lease/disposition/callers、claimed/activated、tombstoneの各1 field変異を拒否する。
   cancelは`CANCELLED/EXPIRED/POISONED`、releaseは`RELEASED/EXPIRED/POISONED`だけをcleanとし、timeout/UNAVAILABLE/session close/遅延・重複ACK/
   registry不一致をUNKNOWNとしてblock・ref保持する。
10. PREPARING後terminalでplaceholderを消去・RELEASED化せず、new offer/captureをblockする。
11. snapshot copy、owner receipt欠落、status/hash/null matrix 1 field変異をauthorityとして受理しない。
12. tombstoneを同じinvocation IDの再利用証拠にせず、ACK後registry cleanupやlate ACKでもretired sourceを復活させない。
13. request/ticket/terminal result/new EMPTY slotの各candidate allocation失敗で`pending.admission_invocation_id`とowner slotsを変えず、
    PREPARING/clean/UNKNOWN publish後にそれらを新規allocateしない。
14. replacement/successor reservation、第2broker ID、claim/activate/RESERVED/final capture APIが本単位から到達不能である。

## 7. F009 K1〜K8と実装gate

本単位はprompt/schema/outputを生成しない。K1/PF1、K2/PF2、K3/PF3/PF4をPASSへ進めずUNKNOWN/NOT_RUNを維持する。K4はPREPARINGの
null/empty条件をstoreが決定論的に設定し、モデルへ委ねない。K5の例文、K6のbaseline比較、K7の品質判定、K8の返信/action選択は行わない。
OFFER取得やPREPARING成功を品質・token/time成立・provider許可の証拠にしない。

承認後の最小実装read-setは`llm/admission_client.py`のsession/lane/lease/OFFER route、`llm/admission_types.py`の既存request/result、
`brain/invocation.py`のinitial acquireとdeadline source、`discussion/state.py`のsingle-owner PREPARING CAS、
`discussion/authority_capture_bridge_v2.py`のprivate material owner照合、`runtime.py`のexact composition、およびfocused offline testsである。
brain decision/projection、RESERVED以降、backend/provider、server/game/mainは変更しない。

独立Reviewerは全state×field matrix、session-origin OFFER、deadline source、PREPARING atomic CAS、one-shot/retire/UNKNOWN hold、provider0を判定する。
