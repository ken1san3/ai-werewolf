# Phase 6 v2 pre-claim hold 限定追補

Status: APPROVED

## 1. 差分

T536 5.5節の`PRECLAIM_FRESHNESS`でI2 CASが競合した場合、またはI2 publish後postcheckが不一致の場合に限り、
`OfferCompositionV2`へprivate nullable strong slot
`claim_activate_preclaim_hold_or_null: ReservedCaptureOwnerReceiptV2 | None`を追加する。slotはpre-claim検査で
portから再読して全identityを検証したexact Rのreceipt objectだけを保持する。copy、同値object、invalidated receipt、
cell、owner、任意caller値を格納しない。receiptの`exact_preparing_receipt.exact_composition`を経由する既存backlinkとの
cycleは、このUNKNOWN hold中だけ意図して許す。validatorは同backlinkに加え、preparing receipt、lease、capture等の
T533既存exact chainを一括照合する。

このslotはlifetime保持専用であり、claim/activate/broker control、freshness authority、store current、CAS expected、
receipt再構築の入力に使わない。owner発行、claim task、watcher、activation context、cleanup candidateは全て0/nullを
維持する。通常のI2成功、claim開始後の競合、ACTIVE系、cleanup系では必ずnullである。

## 2. closed matrix

| composition row | store / owner / broker | preclaim hold | 許可する操作 |
|---|---|---|---|
| `RESERVED_IDLE`、pre-claim検査中 | exact R / owner null / claim 0 | null。exact receiptはcallee localだけが一時所有 | 検査継続またはI2 CAS一回 |
| `INVALIDATED_PRECLAIM` | exact I2 / owner null / claim 0、lane held | null | T533 I2 lineageと別cleanup gateだけ |
| `CLAIM_ABORT_CONFLICT_HOLD`のpre-claim row | observed foreign current / owner null / claim 0 | exact original R receipt | T533 conflict observationの照合と専用recoveryだけ。再CAS 0 |
| `I2_POSTCHECK_UNKNOWN`のpre-claim row | exact I2 / owner null / claim 0 | exact original R receipt（stateはI2 publishによる`RETIRED`） | T533 postcheck observationの照合と専用recoveryだけ。repair/再通知 0 |
| claim taskを一度でも生成した全row、ACTIVE/I3、terminal/retired正常row | T536各正本row | null | T536既存owner graphだけ |

pre-claim CAS loserでは、localに保持済みのexact receiptをT533 conflict refsと同じno-await/non-raising区間でslotへ一回代入する。
CAS成功後postcheck不一致では、exact receiptをT533 postcheck refsおよび`I2_POSTCHECK_UNKNOWN` publishと同じ区間で代入する。
対象rowでslotがnull、foreign、別receiptならUNKNOWNを維持し、近いrowへのdowngrade、再読による追認、GC後の再構築をしない。
対象外rowでnon-nullも拒否する。

## 3. retire・GC・test

本scopeではUNKNOWN holdを自動clearしない。後続の専用recoveryが、同じcomposition、exact held receipt、T533
conflict/postcheck refs、store cell、owner null、claim 0を一括検証し、terminal successorを原子的にpublishした後だけ、
同じno-await/non-raising区間でslotをnullへclearできる。composition全体の明示retire/closeも、broker/control task 0と
successor ownership確定後だけclearする。slot単独clear、weakref化、timeoutによるclearは禁止する。

focused offline testは、両pre-claim UNKNOWN rowで外部receipt refを落として`gc.collect()`後もslotがexact receiptを保持し、
その`exact_preparing_receipt.exact_composition is composition`とT533 exact chainを確認する。recovery/retire clear後は外部strong refを全て落とし、compositionとreceiptの
weakrefが双方deadになることを確認する。他の全matrix rowではslotが常にnullであり、claim/activate/release/provider countが
既存T536値から変わらないこと、foreign/same-value receipt、片側slot mutation、早期clearを拒否することをnegativeにする。

## 4. pre-publication task lifetimeの限定補足

T536のclaim taskとworld/cancel/deadline watcherの4 taskを作る前に、compositionが所有する既存
`claim_activate_prepublication_hold_or_null`へ`PrepublicationTaskHoldV2`を一回だけ事前配置する。holdは未setのexact gate、
`claim_task_or_null`、`world_watcher_task_or_null`、`cancel_watcher_task_or_null`、
`deadline_watcher_task_or_null`の4固定nullable slot、`exact_tasks_or_null`、fixed failure codeだけを持つ。各
`asyncio.create_task`が返った直後、次のallocation・task作成・awaitより前に対応slotへ同一taskを代入する。
`create_task`が返さずraiseした位置のslotと後続slotはnullであり、それ以前のslotだけがexact taskを保持する。

4 task作成後にだけ、4 slot順のimmutable tupleを`exact_tasks_or_null`へ構築する。tuple allocationがraiseしても4 slotが
live taskを保持する。tuple成功後、ownerの4 task field、composition owner slot、owner stateを既存T536の同一
no-await publish区間で固定し、holdをnullへclearしてからgateをsetする。`exact_tasks_or_null`は成功時のread-only projectionであり、
task authority、起動許可、再構築元にはしない。途中失敗ではgateをsetせず、非null slotのtaskだけをcancel/gatherする。
全taskの終了確認後はholdをclearできる。settlement期限で一つでもliveまたは状態不明ならhold、4 slot、成功済tuple（存在時）を
`CLAIM_PREPUBLICATION_UNKNOWN`で保持し、owner publish、claim開始、外側controlは0のままとする。

closed testはtask生成0/1/2/3/4後、tuple allocation、owner publish直前の各failure pointを一回ずつ注入する。各rowで
slot prefixだけがnon-null、task identity/loopがexact、gate unset、`exact_tasks_or_null`は全4作成前null、作成後はnullまたは
4 slotとidentity/order一致、失敗cleanup後は全終了またはUNKNOWN hold保持のどちらかだけを認める。foreign task、slot穴、
順序差、tuple-only保持、hold事後allocation、live taskを残したhold clearを拒否する。

## 5. release failure originの限定補足

既存`RELEASE_RAISED`を使用せず、発生位置と`cleanup_task_or_null`のshapeを次のclosed rowへ分ける。failure codeは観測時に
一回固定し、後からtask refをnullにして別rowへdowngradeしない。

| failure code | exact cleanup task shape | origin / 次の操作 |
|---|---|---|
| `RELEASE_START_RAISED` | null | context exit、`release()` coroutine取得、または`create_task`がtaskを返す前のraise。取得済coroutineはcloseし、release再試行0 |
| `RELEASE_TASK_RAISED` | exact task、done、non-cancelled、`exception()` non-null | task実行中のraise。task strong refとexception観測を保持し、release再試行0 |
| `RELEASE_TASK_CANCELLED` | exact task、done、cancelled | release task取消し。task strong refを保持し、release再試行0 |
| `RELEASE_TIMEOUT` | exact task、not done | settlement期限時点のlive taskを保持し、別cleanup gateまで再cancel/release 0 |
| `RELEASE_UNAVAILABLE` / `RELEASE_SHAPE_MISMATCH` | exact task、done、non-cancelled、exception null | await正常return後のbroker terminal観測にだけ使用 |

全rowでclaim statusは`GRANTED`、cleanup terminalは未証明、owner/compositionは対応する
`CLAIM_CLEANUP_UNKNOWN`または`ACTIVE_CLEANUP_UNKNOWN`を維持する。validatorはfailure codeからtask shapeを推測して
修復せず、表のidentity/stateをexact照合する。focused negativeはtask-nullとdone-exceptionの相互置換、cancelledの
exception読出し、timeout taskのref clear、正常returnを`*_RAISED`へ分類する変更を拒否する。
