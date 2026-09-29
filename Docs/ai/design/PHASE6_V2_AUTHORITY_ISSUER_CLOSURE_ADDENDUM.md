# Phase 6 v2 Authority Issuer Closure 限定追補設計

Status: APPROVED — T520独立review（承認時本文hashはT520_APPROVAL_BINDING.json）
Task: T520
Parent: `Docs/ai/design/PHASE6_V2_AUTHORITY_CAPTURE_BRIDGE_DESIGN.md` 2〜5節、7節
Evidence: `Docs/ai/handoffs/tasks/T519_V2_AUTHORITY_CAPTURE_BRIDGE_TOOL_REVIEW.md` Round 2 R9

## 1. 差分と非対象

T519ではactual ownerへstructural fake receipt/portを混ぜてもopaque authorityを発行できた。原因は、各factoryがconcrete objectを部分的に見る一方、
`NetworkClient -> claim capability -> source -> authority runtime -> World -> context receipt -> ports -> bridge`を同じ所有登録へ束縛していないこと、
およびfull bootstrap再検証の成功がreceipt issuerのone-shot入力になっていないことである。

本追補は通常factoryの引数とcomponent wiringを閉じる。Pythonの任意memory改変、R10のgeneration/parent内容、R11のauthority slots、lease、
broker、brain、request/wire、provider、game ruleは扱わない。T518のPENDING_SYNC起動順、private bytes/log境界、UNLEASED出力、v1 sink-nullを維持する。

## 2. exact composition owner登録

### 2.1 `AuthorityOwnerRegistrationV2`

v2 opt-in factoryだけが、次の順に候補を作る。

1. exact `NetworkClient`からpre-iteration single-owner APIでexact `InboundAuthorityClaimCapabilityV2`を1個取得する。
2. そのnetwork/capabilityからexact `_Phase6NetworkEventSource`とexact `InboundAuthorityRuntimeV2`を作る。
3. exact source/authorityからexact `WorldState`を作り、sourceへWorldをattachする。
4. 全辺を検査後、private `AuthorityOwnerRegistrationV2`を作り、network、capability、source、authority、Worldへ同じobject identityを一回だけ登録する。

登録のclosed identityは次である。snapshot値やhashだけの代用を認めない。

```text
AuthorityOwnerRegistrationV2 = opaque {
  exact_network_client,
  exact_claim_capability,
  exact_runtime_source,
  exact_authority_runtime,
  exact_world,
  exact_discussion_store: null | exact DiscussionStateStore,
  lifecycle: ACTIVE | RETIRED,
  issuer_capability
}
```

登録前に以下を同時に満たす。

- 各objectはexact concrete typeであり、subclass、Protocol実装、duck type、same-value copyではない。
- `capability._client is network`かつ`network._authority_capability is capability`である。
- `source._network is network`、`source._authority_capability is capability`、`source._world is world`である。
- `world._source is source`、`world._inbound_authority is authority`である。
- `authority._composition_capability is capability`である。
- network/capability/source/world/authorityはいずれも未登録、sourceは未retireで、network iterationは登録前に開始していない。

検査中は登録fieldを変更しない。候補registrationを構築後、network/capability/source/authority/Worldの空slotへno-await/no-callback/non-raising
assignmentで同一objectをpublishする。事前検査失敗では5者とも未登録、publish後は5者とも登録済みであり、部分登録状態を公開しない。登録後の再attach、別registrationへの差替え、同じnetwork
capabilityを別source/authority/Worldへ登録する操作を拒否する。source retire時は同じregistrationを`RETIRED`へ移し、以後のproof/port/readを拒否する。

### 2.2 context storeの一回attach

initial registrationの`exact_discussion_store`はnullである。context receipt発行後、runtime compositionだけが次を一回呼ぶ。

```text
attach_discussion_store_v2(
  exact ACTIVE registration,
  exact registered context receipt,
  exact DiscussionStateStore
) -> None
```

exact store型、`store._bound is receipt.exact_bound_context_object`、context object/hash、receiptのregistration/capability、owner thread/loop、
registrationとsourceに登録されたreceipt identityを全検査する。候補attachを構築後、registrationのnull slotとstoreの空owner-registration slotへ
同じregistration identityをno-await/no-callback/non-raising assignmentでpublishする。事前失敗では両slotとも空、成功後は両slotとも一致し、
一方だけのattachを公開しない。runtimeが選んだ1 store以外、同じBoundを使うsecond store、foreign store、二重attach、別receiptへの差替えを拒否する。
source retire時もattachは残してowner identityを監査できるが、registration RETIREDによりport/read発行権は失う。

通常v1 factoryはcapability、authority runtime、registrationを作らず、従来どおりsink-nullである。

## 3. full revalidation one-shot proof

### 3.1 発行

`_revalidate_authority_pending_v2(...)`は`None`ではなくprivate opaque `AuthorityPendingProofV2`を返す。入力はexact pending、exact current
World snapshot、exact active registrationであり、network game/playerをcaller文字列として受けない。registrationのexact ownerから値を読む。

```text
AuthorityPendingProofV2 = opaque {
  exact_pending,
  exact_bootstrap_validation_receipt,
  exact_manifest_material,
  exact_manifest_bytes,
  manifest_sha256,
  exact_context, context_sha256,
  exact_world_snapshot,
  exact_authority_snapshot,
  exact_owner_registration,
  authenticated_player_id,
  connection_generation,
  readiness_status: PENDING_SYNC | READY,
  state: ACTIVE | CONSUMED | RETIRED,
  issuer_capability
}
```

発行前にT518 3.1節のfull validationを再実行し、bootstrap validation receiptがexact pending/material/bytes/contextを保持すること、owner registrationが
ACTIVEであること、snapshotがregistrationのcurrent exact World objectであること、network/authorityのgame/player/generationとPENDING_SYNCまたは
READYを照合する。pendingごとのactive proofは最大1個とし、同時発行を拒否する。proofは検査対象へのstrong referenceを持つが、外部へserialize、
hash、repr、log、prompt、audit出力しない。

pendingにはprivate slot `pending._active_authority_proof_v2: null | exact proof`を1個だけ置く。proofはcaller非公開の`_state`、
`_secret_refs`、secretを含まない一意な`_proof_identity`を持つ。全revalidationとproof候補構築後、proofをACTIVEにしてpendingのnull slotへ同時に
non-raising publishする。revalidationまたは候補構築失敗ではslotはnullのままで、proofを外部へ返さない。

### 3.2 消費とreceipt発行

binding transactionだけが次のAPIを呼ぶ。

```text
consume_authority_pending_proof_v2(
  exact_active_proof,
  exact_runtime_source,
  exact_world
) -> (exact BoundDiscussionContext, ValidatedContextOwnerReceiptV2)
```

caller supplied boundは受けない。issuerがproof内のmanifest/context hashとexact contextから`BoundDiscussionContext`候補を作る。次に、proofがACTIVEかつ
pendingの唯一のactive proofであること、registration/source/world/network/capability/authorityの全identityが2節のまま、sourceが未bound・未retire、
current World/authority/networkがproofのworld/owner/game/player/generationに一致することを再検査する。authority revision/readinessは発行時provenanceとして
receiptへ記録し、PENDING_SYNCからREADYへの将来遷移で同値固定しない。

成功候補receiptはT518のfieldに次を追加する。`proof_identity`はsecret-free identityだけであり、proof objectやmanifest参照ではない。

```text
owner_registration_identity,
proof_identity,
port_issuer_capability
```

全検査と候補構築後、1つのnon-raising publish区間でproofを`ACTIVE -> CONSUMED`、`pending._active_authority_proof_v2`をnull、proofの
`_secret_refs`を空、sourceのbound/receiptをexact候補、pendingをconsumedへ移し、manifest materialを破棄する。receiptはsourceのregistered receiptとして同一identityで再読できる場合だけ有効である。二重consume、別source/World、
foreign/stolen proof、same-value copy、RETIRED proof/registrationを拒否する。

consume呼出し後に検査が失敗した場合は、同じnon-raising transitionでproofを`ACTIVE -> RETIRED`、pendingのexact active slotをnull、proofの
`_secret_refs`を空へ移すが、source bound/receipt、pending consumed、manifest discardを一切publishしない。
retryにはcurrent ownersを再読して新proofを発行する。候補Bound/receiptだけが局所生成されても外部へ返さない。publish後に失敗し得るvalidation、await、callback、
allocation、hash生成を置かない。

### 3.3 private material解放

bootstrap validation receiptはorigin routeだけを証明し、discard後のmanifest保管場所にしない。`PendingDiscussionContext.discard_manifest()`はv1/v2共通で、
pendingのmaterial/bytesに加え、bootstrap receiptとactive/retired proofが持つmanifest material/bytesへのstrong referenceを同じ同期処理で解除する。
v1 `bind()`、明示discard、runtime失敗cleanup、v2 consume成功の全経路でこの処理を通す。v2 consume失敗ではpendingを再試行可能に保つためmanifestを維持するが、
失敗proof自身のstrong referenceは解除する。context owner receiptが保持するのはcontext object、hash、owner identity/provenanceだけで、full manifest bytes/materialは保持しない。

明示discardまたはcleanup時にactive proofがあれば、まず同じnon-raising transitionで`ACTIVE -> RETIRED`、pending slotをnull、proof secret refsを空にする。
その後bootstrap receiptのmaterial/bytes参照とpendingのmaterial/bytesを空にする。CONSUMED/RETIRED proofはpending slotへ残らず、外部に残ったproof objectも
stateとsecret空を理由に再利用できない。active proofなしのdiscardは同じ結果へidempotentに到達する。

## 4. receipt-bound portとbridge発行

### 4.1 共通issuer条件

port/bridge factoryは単なるmodule-private名をauthority proofにしない。すべてexact type、exact registration、receiptのprivate
`port_issuer_capability`、sourceに登録されたreceipt identityを検査する。receiptは次を満たす必要がある。

- `type(receipt) is ValidatedContextOwnerReceiptV2`で、`receipt is source._context_owner_receipt_v2`である。
- receiptのsource/World/registration/bound/contextが現在のexact objectと一致する。
- receipt capabilityがissuer登録簿のexact capabilityであり、copyした値や別receipt capabilityではない。
- registrationはACTIVE、sourceは未retire、receipt発行時generationはregistration/current network/authorityと一致する。

factoryは検査と候補構築を完了してからorigin slotをpublishする。失敗時にport、bridge、登録slot、read stateを一切残さない。

### 4.2 World port

```text
world.issue_authority_read_port_v2(exact receipt)
```

World methodは引数のsourceを受けない。registered ownerからsourceを得る。exact World/source/authority/network/capability identityとreceipt条件を照合し、
`WorldAuthorityReadPortV2`へexact World、source、receipt、registration、receipt capability、port固有origin capabilityを閉包する。
同じreceipt/Worldのportは最大1個とし、fake receipt、foreign/stolen receipt、別World、retired sourceでは発行しない。

### 4.3 capture port

```text
store.issue_capture_read_port_v2(exact receipt)
```

exact `DiscussionStateStore`自身が、`store._bound is receipt.exact_bound_context_object`、context identity/hash、owner thread/loop、receipt registration/capabilityを
検査する。さらに`registration.exact_discussion_store is store`かつstoreのowner-registration backlinkが同じregistrationであることを要求する。
`DiscussionCaptureReadPortV2`へexact store/bound/receipt/registration/receipt capability/port origin capabilityを閉包する。
storeだけを入力にするfree factoryを廃止し、同じreceipt/storeのportは最大1個とする。

### 4.4 bridge

```text
issue_authority_capture_bridge_v2(
  exact receipt,
  exact WorldAuthorityReadPortV2,
  exact DiscussionCaptureReadPortV2
) -> AuthorityCaptureBridgeV2
```

3引数のexact concrete typeを要求する。両portのreceipt、registration、receipt capabilityが引数receiptと同一で、World portのWorld/sourceが
registration、capture portのstoreが`registration.exact_discussion_store`、boundがreceiptへ一致し、store backlinkと両portのorigin capabilityが
各issuer登録簿に存在することを検査する。
duck-typed method、real methodを委譲するwrapper、same-value objectは拒否する。

両portは`UNBOUND -> BRIDGE_BOUND`の一回だけのcomposition stateを持つ。全検査とbridge候補構築後、同じnon-raising publish区間で両portをexact
bridge identityへ束縛する。一方だけの消費、二重bridge、別receiptとの再利用を許さない。bridgeにもregistration、receipt capability、両port
origin identityを閉包し、各read前にsource registered receipt、ACTIVE registration、registration/store backlinkの同一性を再照合する。

## 5. compositionとpure readの分離

2〜4節はcomposition issuerだけが行う初期化・登録・one-shot消費である。`read_current()`と`prepare_current()`はproof、receipt、port、registrationを
発行・consume・repair・差替えせず、現在のidentityと状態を検査するだけである。PENDING_SYNC receiptの発行とport/bridge compositionは可能だが、
World port read、capture authority read、material prepareはcurrent same-generation authorityがREADYになるまで`AUTHORITY_NOT_READY`で拒否する。
queue済みexact CONNECTEDをWorldがconsumeするT518の起動順を変えない。

source retirement、registration RETIRED、registered receipt差替え、network/capability/World/storeのidentity不一致をread時に検出したらfail-closedとし、
旧positive authorityを返さない。read失敗でowner stateをmutateせず、再compositionもしない。

## 6. offline acceptance

actual NetworkClient、fake transport、WorldState、DiscussionStateStoreを使い、provider/LLM/game/Actionsは0とする。

### Positive

1. v2 opt-in factoryがexact network/capability/source/authority/Worldを1 registrationへ束縛し、receipt後にruntime選択のexact storeを一回attachする。
2. PENDING_SYNCでfull revalidation proofを発行・一回consumeし、exact Bound/registered receiptを原子的に得る。
3. 同じreceipt capabilityからWorld/store portとbridgeを一回構成し、READY後だけpure readからUNLEASED materialを得る。
4. READYで直接receiptを発行する経路も同じproof/registration条件を通る。
5. v1 bind、v2成功、明示discard、失敗cleanup後にbootstrap receipt/proofからfull manifest material/bytesへのstrong referenceが残らない。

### Negative

1. actual World/sourceへfake/structural/same-value receiptを渡してもWorld portを発行できない。
2. actual receiptへfake/structural/delegating World portまたはcapture portを渡してもbridgeを発行できない。
3. exact sourceへfake/subclass NetworkClient、fake claim capability、別client所有capability、capabilityと異なるauthority/Worldを混ぜるとregistration 0である。
4. proofなし、別pending/snapshot/material/contextを束縛したproof、foreign/stolen/retired proof、same-value copyを拒否する。
5. proofのdouble consume、別source/Worldでのconsume、consume失敗後の同proof再利用を拒否し、partial bound/receipt/discardを残さない。
6. receipt/portのforeign owner、別registration/generation/capability、retired source、registered receipt差替えを拒否する。
7. World/store portの二重発行、片方だけ別receiptのbridge、portの二重bridge bindを拒否し、partial bindを残さない。
8. 同じBoundのforeign second store、store二重attach、attach失敗を拒否し、registration/store backlinkのpartial publishを残さない。
9. PENDING_SYNCでread/prepareできず、READY遷移はproofやportを再発行・consumeしない。
10. consume成功/失敗、明示discardごとにproof state、pending active slot、secret refsがclosed transitionどおりになり、retired proofを再利用できない。
11. `read_current()`と`prepare_current()`前後でproof/receipt/registration/port state、World/store/network stateが不変である。
12. v1 sink-nullでauthority capability/registration/proof/`ValidatedContextOwnerReceiptV2`/port/bridgeが作られず、既存bind/discard結果が同値である。bootstrap validation receiptはfull validation route用に存在してもdiscard後にmaterialを保持しない。

## 7. 実装・review gate

承認後の最小read-setは、`network/inbound_authority_v2.py`と`network/client.py`のformal capability owner、`runtime.py`のv2 opt-in composition/binding、
`world/inbound_authority_v2.py`と`world/service.py`のregistration/World port、`discussion/context.py`のproof lifecycle、
`discussion/state.py`のreceipt-bound port、`discussion/authority_capture_bridge_v2.py`のexact issuer、およびfocused testsである。

独立Reviewerはregistration全辺、proof一回消費とmanifest解放、fake receipt/port拒否、発行失敗atomicity、composition/read分離だけを判定する。
R10/R11とT519の明白な既存契約修正は本設計の承認対象に含めない。
