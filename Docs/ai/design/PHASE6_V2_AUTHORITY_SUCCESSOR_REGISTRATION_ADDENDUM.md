# Phase 6 v2 Authority Successor Registration 限定追補設計

Status: APPROVED
Task: T521
Parent: `Docs/ai/design/PHASE6_V2_AUTHORITY_ISSUER_CLOSURE_ADDENDUM.md` 2/4/5節
Preserves: T517のWorld/authority atomic commit・abort契約

## 1. 差分と原則

`InboundAuthorityRuntimeV2.prepare_commit()`と`prepare_invalid_empty()`は`copy.copy`でprepared successorを作り、`WorldState._commit()`または
`_abort_inbound_authority()`が`world._inbound_authority`をそのsuccessorへ差し替える。したがってT520の
`registration.exact_authority_runtime`を初期objectへ永久固定すると、正規commit直後からowner chainがstaleになる。

本追補では`registration.exact_authority_runtime`を**current authority pointer**として扱う。初期登録値は維持するが、その後はexact World所有の
commit/abort publishだけがexact predecessorから正式なprepared successorへ進める。prepare、read、validator、外部factoryはpointerを更新しない。
既存World/authority同version/seq、failure-safe abort、waiter wake、v1 sink-null、visibility/private境界を変更しない。

### 1.1 constructorで固定するowner mode

authority付きWorldはconstructor時に次の意図を固定し、run中に相互downgradeしない。

```text
UNREGISTERED_STRUCTURAL
  T517 structural/offline用。既存copy/abort lifecycleを維持する。
  owner registration、receipt、proof、owner port/read/bridgeは発行不能。

REGISTRATION_PENDING -> REGISTERED_OWNER
  v2 opt-in runtime factoryだけがprivate construction capabilityで選ぶ。
  initial registrationと正式abort bundleの同時publish完了前はrun/commit/readへ公開しない。
```

通常constructorの既定は`UNREGISTERED_STRUCTURAL`である。v2 factoryが指定した`REGISTRATION_PENDING`で登録slot欠落や登録準備失敗が起きた場合、
structural modeへfallbackせずfactory全体を失敗させる。`REGISTERED_OWNER`になったWorldがregistrationを失ってもstructural modeへ戻さない。
authorityなしv1 sink-nullは両modeの外で従来どおりである。

## 2. prepared successor ticket

### 2.1 closed型

Worldへだけ登録されたprivate `authority_successor_publish_capability`をT520のinitial owner registration時に1個作る。capabilityはWorldとregistrationの
exact identityへ束縛し、source、receipt、port、read結果へ出さない。

authority successorは裸objectではpublishできず、次のopaque one-shot ticketと組にする。

```text
AuthoritySuccessorTicketV2 = opaque {
  exact_registration_or_null,
  exact_world_or_null,
  exact_predecessor_authority_or_null,
  exact_successor_authority_or_null,
  transition_kind: COMMIT | ABORT,
  terminal_only: bool,
  expected_world_version,
  expected_last_applied_seq,
  exact_parent_commit_ticket_or_null,
  parent_commit_lineage_identity_or_null,
  lineage_identity,
  state: PREPARED | ARMED_ABORT | PUBLISHED | RETIRED,
  issuer_capability_or_null
}
```

successorはexact `InboundAuthorityRuntimeV2`で、`successor is not predecessor`、composition capabilityはregistrationのclaim capability、
owner-registration backlinkは同じregistrationである。`copy.copy`で継承したpredecessor用ticket/temporary markerは明示的に空へ戻し、今回ticketの
successor identityだけを保持する。predecessorがregistration backlinkを保持していても、それはlineage provenanceにすぎずcurrent権限ではない。
current判定は常に`registration.exact_authority_runtime is world._inbound_authority is candidate`の3者identityで行う。

`lineage_identity`はticket作成時にissuerが作る、object referenceやprivate bytesを含まないopaque immutable identityである。ticketとsuccessor
candidate markerの全state×field shapeは次の表を正本とし、表外の組合せを拒否する。

| state / kind | registration / World / issuer | predecessor / successor | parent ref | parent lineage | `successor._prepared_successor_ticket_v2` |
|---|---|---|---|---|---|
| PREPARED COMMIT | exact / exact / exact | exact current / exact successor | null | null | self |
| PREPARED child ABORT | exact / exact / exact | exact COMMIT successor / exact invalid successor | exact PREPARED COMMIT | null | self |
| ARMED_ABORT initial/current | exact / exact / exact | exact current / exact invalid successor | null | null | self |
| ARMED_ABORT after COMMIT publish | exact / exact / exact | exact current / exact invalid successor | null | exact parent lineage identity | self |
| PUBLISHED | null / null / null | null / null | null | null | clear |
| RETIRED | null / null / null | null / null | null | null | clear |

`transition_kind`、`terminal_only`、expected version/seq、self `lineage_identity`は全stateでscalar provenanceとして保持してよいが、runtime owner graphへの
referenceを含めない。遷移は次に限定する。

- successful COMMIT publish: COMMIT `PREPARED -> PUBLISHED`、child ABORT `PREPARED -> ARMED_ABORT`、交換される旧saved ABORT
  `ARMED_ABORT -> RETIRED`を同じnon-raising区間で行う。child parent refをnull、parent lineageをCOMMIT lineageへ移す。
- prepare/publish前失敗: new COMMITとchild ABORTを`PREPARED -> RETIRED`にした後、既存saved ABORTを実行して
  `ARMED_ABORT -> PUBLISHED`にする。
- actual abort: saved ABORTを`ARMED_ABORT -> PUBLISHED`にする。実行済みticketをRETIREDとは記録しない。
- 未使用candidate/bundleの破棄・交換だけをRETIREDにする。

各PUBLISHED/RETIRED遷移で表のobject refsとcandidate markerをclearする。registrationやauthorityから過去ticket/predecessorのchainを保持せず、
保持量をcurrent pair＋保存済みARMED abort pairのO(1)に閉じる。

### 2.2 COMMIT prepare

REGISTERED_OWNERのWorld commit prepareだけが、exact current predecessorとpublish capabilityをauthorityのprivate prepare入口へ渡す。入口は次を検査してから
successorとCOMMIT ticketを返す。

- registrationはACTIVEで、4.1節のstatic core owner辺が成立する。
- `world._inbound_authority is registration.exact_authority_runtime is predecessor`である。
- predecessor backlinkがregistration、publish capabilityがWorld/registrationのexact capabilityである。
- predecessorは現在別ticketのsuccessor候補ではなく、今回World commitのcurrent published authorityである。
- prepared successor snapshotが予定するnext World version/last seqと一致する。

prepare中はWorld、registration、predecessorのcurrent identityを変更しない。allocation、copy、hash、authority apply、snapshot検証が失敗した場合、
ticketをpublishせず、両current pointerはpredecessorのままである。predecessorのsnapshot、revision、private source、pending/action/ability状態も
変更せず、全変更はsuccessor copyだけへ適用する。

### 2.3 ABORT prepareとlineage

T517のabort bundleはcurrent authorityに対して常に次のinvalid successorを先に準備する。

- 既にpublishedなcurrentから作る場合、ABORT ticketは`ARMED_ABORT`で、predecessorはcurrent、parent ref/lineageはnullである。
- 新COMMIT successor用の次回abort bundleを準備する場合、ABORT ticketのpredecessorはそのexact COMMIT successor、
  stateは`PREPARED`、`exact_parent_commit_ticket`はそのPREPARED COMMIT ticket、parent lineageはnullである。

後者はpredecessorがまだregistration currentでない唯一の許可例である。exact parent ticketのsuccessor identity、registration、World、予定version/seqを
検査してlineageを固定する。任意の未publish copy、別commit ticket、stale ticketからABORT successorを準備できない。
ABORT successor snapshotはfailed World pairのversion/last seqに一致し、readinessは既存契約どおり`INVALID`でprivate positive authorityを持たない。

### 2.4 initial abort bundle

v2 World constructor時点ではT520 owner registrationがまだpublishされていないため、登録済みpredecessorを要求するABORT ticketは作れない。
`REGISTRATION_PENDING`のv2 opt-in compositionはWorldを外部へ返す前に、initial registration候補、publish capability、initial World/authority pairから最初のABORT successor/ticket/
bundleを準備する。ここだけは未publish registration候補を許可し、network/capability/source/authority/Worldの初期5辺、initial authority identity、
version/seqをinitial registration issuerが直接検査する。

全準備後、T520 initial registrationの5-owner backlink publishと`registration.exact_authority_runtime = initial authority`、Worldのinitial abort bundleを
同じno-await/no-callback/non-raising composition区間でpublishする。initial ABORT ticketは`ARMED_ABORT`、parent ref/lineageはnullとする。
準備失敗ではregistration 0、正式abort bundle 0のままv2 factory全体を失敗させ、
未登録Worldをrun/readへ公開しない。World constructorが作るticketなしraw bundleを正式bundleとして再利用・公開しない。v1 constructor経路は変更しない。

`UNREGISTERED_STRUCTURAL`はregistration/ticketを作らず、既存T517どおりraw prepared authorityとabort bundleをWorld内部だけでcopy/publishする。
structural authorityを後からREGISTERED_OWNERへ昇格できず、structural modeからcontext owner receipt/port/bridgeを発行しない。

## 3. World所有atomic publish

### 3.1 successful commit

`WorldState._commit()`はprepared World、COMMIT successor/ticket、新abort bundle、successor update eventをすべて構築してからpublishへ入る。
publish直前に次を再検査する。

1. current World snapshot、`world._inbound_authority`、`registration.exact_authority_runtime`がticketのexact predecessorと一致する。
2. ticketはPREPAREDかつ未使用で、World/registration/publish capability、予定version/seqがexact一致する。
3. successor backlinkはregistrationで、snapshotはprepared Worldとversion/last seqが一致する。
4. 新abort bundleのexpected current authorityはsuccessorで、invalid authorityのPREPARED ABORT ticketはこのPREPARED COMMIT ticketをexact parentに持ち、parent lineageはnullである。
5. 4.1節のstatic core owner辺が成立し、receipt/store段階は現在のphase-aware shapeとして矛盾がない。

全検査後、既存のno-await/no-callback/non-raising publish区間へ次を含める。

```text
world._inbound_authority = exact successor
registration.exact_authority_runtime = exact successor
COMMIT ticket.state = PUBLISHED
COMMIT ticket registration/World/issuer/authority/parent refs = clear
child ABORT ticket: PREPARED -> ARMED_ABORT
child exact parent ref = null
child parent lineage = COMMIT ticket.lineage_identity
old saved ABORT ticket: ARMED_ABORT -> RETIRED; all object refs/marker = clear
world._abort_bundle = exact new bundle
world version/snapshot/update event = prepared values
```

両authority pointerの片方だけを公開しない。previous update eventの`set()`は全field publish後に行う。predecessorや旧ticketをcurrentへ戻さず、
old predecessorを使うport/readはcurrent 3者identity不一致で拒否する。

### 3.2 failureとabort

commit prepareまたはpublish前検査が失敗した場合、新COMMIT ticket/successorとその子ABORT ticketを`RETIRED`にし、current pointerを動かさない。
その後、既にcurrent predecessor用として保存済みのabort bundleだけを使う。

`_abort_inbound_authority()`はmutation前に、bundleのexpected World/authority、registration current、ABORT ticket predecessor、World/registration/
publish capability、invalid successor backlink、failed pair version/seqをexact照合する。成功時は既存のno-await/no-callback/non-raising区間で次をpublishする。

```text
world._inbound_authority = exact invalid successor
registration.exact_authority_runtime = exact invalid successor
ABORT ticket.state = PUBLISHED
ABORT ticket registration/World/issuer/authority/parent refs and successor marker = clear
world freshness/version/snapshot/update event = exact FAILED pair
abort bundle = consumed
```

全field publish後にprevious update eventを`set()`し、waiterを有限wakeする。検査失敗ではWorld pointer、registration pointer、snapshot、bundleを一切変更せず、
foreign successorを追認しない。正常なcommit準備例外では保存済みbundleが必ずexact current predecessor用なので、この不整合経路へ入らない。
同じticket/bundleの二重publish、PUBLISHED/RETIRED ticket、別World、別registration、別predecessorを拒否する。

### 3.3 RETIRED後のterminal transfer

runtime cleanupはsourceをcloseしてregistrationをRETIREDにした後、World stop/cancelが`ENDED`または`FAILED` commitを行い得る。RETIREDではreceipt、
proof、port、bridge、positive readの発行・利用を常に拒否するが、exact Worldがowner pairを安全に閉じる次のtransferだけを許す。

- predecessorはretire時の`world._inbound_authority is registration.exact_authority_runtime`のexact currentである。
- COMMIT successorはterminal World pair専用で、authority readinessは`INVALID`、action/ability/pending/private positive authorityは空である。
- ticketは`terminal_only is True`で、World freshness targetは`ENDED`または`FAILED`に限る。
- またはretire前から保存済みのexact current predecessor用ABORT ticket/bundleを使う。
- 4.1節のstatic core identity/backlinkは成立し、dynamic network generation/readinessやreceipt/storeの存在を要求しない。

terminal prepare/publishも3.1〜3.2節と同じticket、両pointer同時publish、version/seq、waiter wake、二重利用拒否を通る。RETIREDでREADY/PENDING_SYNC、
positive sidecar/private sourceを持つsuccessor、non-terminal World snapshot、新しいreceipt/store attachを作らない。terminal publish後もregistrationはRETIREDのままである。

## 4. phase-aware validatorとread純粋性

### 4.1 transition用static owner validator

commit/abort prepare・publishはstatic owner identityだけを検査する。

registrationは単調な`discussion_owner_stage`を持つ。

```text
NO_RECEIPT -> RECEIPT_ONLY -> STORE_ATTACHED
```

registrationには`registered_receipt: null | exact ValidatedContextOwnerReceiptV2`を持たせる。initial registration publishではstageを
`NO_RECEIPT`、registered receiptをnullに固定する。T520のcontext receipt consume成功と同じnon-raising publish区間でregistration/sourceの両receipt
slotへ同じexact receiptを入れて`RECEIPT_ONLY`へ進め、store one-shot attachと同じ区間で`STORE_ATTACHED`へ進める。逆行、skip、同stageへの別object
差替えを許さず、source retirement後もstageとexact receipt/store identityを保持する。

```text
core5:
  exact network <-> exact claim capability
  exact source -> network/capability/World
  exact World -> source/current authority
  registration -> same network/capability/source/World/current authority
  current authority backlink -> same registration

stage-exact edges:
  NO_RECEIPT:
    registration.registered_receipt is null
    source._context_owner_receipt_v2 is null
    registration.exact_discussion_store is null
  RECEIPT_ONLY:
    registration.registered_receipt is source._context_owner_receipt_v2
    receipt.owner_registration is registration
    receipt exact source/World/bound match registration/source
    registration.exact_discussion_store is null
  STORE_ATTACHED:
    registration.registered_receipt is source._context_owner_receipt_v2
    receipt.owner_registration is registration
    receipt exact source/World/bound match registration/source
    registration.exact_discussion_store is exact store
    store._authority_owner_registration_v2 is registration
    store._bound is receipt.exact_bound_context_object
```

markerから現在値を推測せず、markerごとに上記shapeをexact検査する。receipt/store fieldが欠落しても前stageへdowngradeしない。registration/storeの片辺だけ、
registration/source片方だけnullまたはforeign receipt、store non-nullでreceipt null、receipt owner/source/World/bound不一致、store backlink/bound不一致を拒否する。
networkが次generationへ先行しauthority commitが追随する途中を許すため、network/authority connection/action generation、
readiness、PENDING_SYNC/READY、pending/bound状態をtransition用static validatorへ入れない。これらはevent/authority commit契約とpositive issuer/read側で検査する。

ACTIVEは通常commit/abort、RETIREDは3.3節のterminal-only例外だけを許す。UNREGISTERED_STRUCTURALはこのvalidatorを使わず、registered owner APIへ渡せない。

### 4.2 positive issuer/read validator

T520の共通owner validatorは、authorityについて次だけをcurrent条件とする。

```text
registration.lifecycle == ACTIVE
registration.exact_world is world
world._authority_owner_registration_v2 is registration
world._inbound_authority is registration.exact_authority_runtime
world._inbound_authority._authority_owner_registration_v2 is registration
```

初期authority objectとのidentity固定を要求しない。successor backlinkだけを見てregistration pointerを更新する、またはWorld pointerだけを見て
successorを正式化することは禁止する。backlinkを持つcopy、ticketなしsuccessor、old predecessor、foreign invalid successorはcurrentではない。
positive receipt/port/readはこのcurrent条件に加え、registration ACTIVE、source未retire、該当段階のreceipt/store exact edge、current network/authority
game/player/connection/action generation、freshness/readiness/caught-up条件を既存T518/T520どおり検査する。

World/capture port、bridge、`read_current()`、`prepare_current()`は上記を検査するだけで、registration/world pointer、ticket state、abort bundleを
変更しない。read中にcommitが進めば開始時と終了時のauthority object/revision/fingerprint不一致で`STALE_OWNER_READ`とし、新successorをそのreadへ
採用し直さない。次の独立readがWorldとregistrationの同時publish済みsuccessorを読む。

registration RETIRED、source retired、FAILED/ENDED、authority `INVALID`時の既存fail-closed条件を維持する。abort後のinvalid successorはowner chain上は
正式currentだが、positive channel/disclosure/material authorityを持たない。

## 5. offline acceptance

actual NetworkClient、fake transport、WorldStateを用い、provider/LLM/game/Actionsは0とする。

### Positive

1. REGISTRATION_PENDINGはinitial registration/正式ABORT bundleを同時publishしてREGISTERED_OWNERとなり、World/registrationが同じinitial authorityを指す。
2. UNREGISTERED_STRUCTURALのT517 offline Worldは既存copy/abortを維持し、receipt/owner port/readを発行しない。
3. `NO_RECEIPT -> RECEIPT_ONLY -> STORE_ATTACHED`がregistration/source両receiptとstore publishの同じ区間で単調に進み、各startup段階でsync/notice commitが成功する。
4. authorityあり/なしnoticeを含む連続commitごとにexact predecessorから新successorへ両pointerが同時に進み、version/seqとbacklinkが一致する。
5. reconnectでnetwork generationがauthorityより先行してもstatic transitionを誤rejectせず、commit後のpositive readではcurrent generationを再検査する。
6. PENDING_SYNC、READY、action/ability更新を跨ぐ複数commit後もpositive owner validator/readがcurrent successorを受理する。
7. COMMIT successor用PREPARED ABORTがexact parent refを持ち、commit publishと同時にARMED_ABORTへ進んでparent refをsecret-free lineageへ置換する。
8. prepare/apply/snapshot構築失敗ではcurrent predecessor用bundleからinvalid successorへ両pointerが移り、FAILED pairとwaiter wakeが一致する。
9. source retire後のstop/cancelはexact terminal INVALID successorまたは保存済みABORT successorへ両pointerを移し、positive authorityを発行しない。
10. read前後にcommitが無ければpointer/ticket/bundleは不変であり、commit競合時はstaleとして次readへ分離される。
11. 複数commit後もretired ticket/candidate marker/parent ticket chainが残らず、strong retentionはcurrent pairと保存済みabort pairへ有限である。
12. v1 sink-nullではticket/transferを作らず、commit/abort結果が既存契約と同値である。

### Negative

1. REGISTRATION_PENDINGの登録失敗やslot欠落をUNREGISTERED_STRUCTURALへsilent downgradeせず、未登録Worldを公開しない。
2. UNREGISTERED_STRUCTURAL authorityを後から登録・receipt/owner portへ昇格できない。
3. registration backlinkだけをcopyしたauthority、裸`prepare_commit()`結果、ticketなしsuccessorをWorldまたはvalidatorが受理しない。
4. initial abort bundle準備失敗でregistration/backlink/World公開のpartial stateが残らず、ticketなしraw bundleを正式化しない。
5. wrong predecessor/World/registration/capability/version/seq、stale・reused・retired ticketでpublishできず、両pointerは旧predecessorのままである。
6. World pointerだけ、registration pointerだけをforeign/old authorityへ差し替えた状態をvalidator/readが拒否し、追認・修復しない。
7. unrelated PREPARED COMMIT ticketからABORT descendantを作れず、parent successor/expected currentの1 field変異を拒否する。
8. commit候補または新abort bundleの準備失敗で、registration pointer、World pointer、旧abort bundle、update eventにpartial mutationがない。
9. abort bundleのexpected current authority、ticket predecessor、invalid successor backlink、failed version/seq各変異でpublish 0とする。
10. RETIREDでnon-terminal/positive successor、新receipt/store attach、READY/PENDING_SYNC authorityへのtransferを拒否する。
11. registration receiptだけnull/foreign、source receiptだけnull/foreign、receipt owner/source/World/boundの各1 field不一致、STORE_ATTACHED後のstore field/backlink nullを拒否し、前stageへdowngradeしない。
12. PREPARED/ARMED_ABORT/PUBLISHED/RETIREDのregistration/World/issuer、authority refs、parent ref/lineage、candidate markerのclosed matrix外を拒否し、commit publish後にparent ticket chainを残さない。
13. receipt/storeのpartial edge、foreign store backlink、dynamic generationだけを理由にしたstatic pointer追認を拒否する。
14. old predecessor/ticket/bundleの再利用、successorの二重publish、別Worldへの移植を拒否する。
15. readがsuccessor ticketを発行・consumeしたり、backlinkからregistration currentを更新したりしない。

## 6. 実装・review gate

承認後の最小変更は`world/inbound_authority_v2.py`のprepared successor ticket、`world/service.py`のcommit/abort transfer、
`discussion/authority_capture_bridge_v2.py`のcurrent owner validator、およびfocused testsである。T520設計、context/store proof/port、lease、broker、
brain、request/wire、provider、server/game/mainは変更しない。

独立Reviewerはpredecessor→successor lineage、World/registration同時publish、abort bundle親子関係、外部copy拒否、read非mutationだけを判定する。
