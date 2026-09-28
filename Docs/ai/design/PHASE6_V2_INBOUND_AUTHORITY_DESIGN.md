# Phase 6 v2 Inbound Authority 接続詳細設計

Status: APPROVED
Task: T516（T515依存）
Date: 2026-09-28
Parent: `Docs/ai/design/PHASE6_V2_CAPTURE_BINDING_DESIGN.md`

## 1. 目的と範囲

T515のoffline実装は、hash・sidecar・catalog・leaseの構造candidateを検証できるが、実`NetworkClient`が認証済みconnectionで
commitした入力と結ばれていない。`ServerEvent`、player ID、generationをcallerが渡す既存structural factoryはruntime authorityでは
なく、`promote_runtime_authority_v2(...)` は常にfail-closedである。

本設計は、次の一単位だけを詳細化する。

1. `NetworkClient`がschema validation、認証owner確認、sequence検査、credential checkpoint保存を完了したeventについてだけ、
   一回限りのopaque observationを発行する。
2. `WorldState`がそのexact eventを`WorldReducer`へ適用する直前にobservationをclaimし、source sliceをimmutable化・hashする。
3. reducerが実際に作った`ActionHandle` / `AbilityResultRecord`へ1対1でprovenance sidecarを結び、retentionとreconnectに合わせて
   runtime authority snapshotを更新する。

T514承認本文は変更しない。本単位はinbound provenanceだけを成立させ、public channel/disclosureの最終capture化、
`DiscussionStateStore`、brain、broker、durable audit、delivery finalizationには接続しない。server schema、wire protocol、ゲーム規則、
private permission、v1既定を変更せず、provider/LLM/game実行は0とする。

追加のユーザー製品選択は不要である。既存の認証・sequence・credential commit・single-writer reducer境界を、T514の
`AuthenticatedInboundRefV2`へ結ぶ実装順序だけを固定する。

## 2. 現行source ownershipの事実

### 2.1 NetworkClient

`NetworkClient._accept_server_message` は `ProtocolMessageValidator.decode_server` 後にprotocol versionとgame IDを照合し、
authentication前event、重複sequence、通常経路の非連続sequenceを拒否する。resume replayはrequested checkpointからの連続列を
bufferで検証し、`session.resumed`のplayer IDを確認してからcommitする。retention gap時の`game.state_sync`はsync barrierとして
通常の`previous+1`条件を置き換える。

`_commit_server_message` は現在、`ClientState.apply_server_event`、credential storeのatomic `save(SessionCheckpoint)`、
`ClientState.set_last_seq`、immutable `ServerEvent`作成、event stream公開の順で実行する。authority opt-inではruntimeが登録したexact
`InboundAuthorityPreflightV2`を使い、event作成、owner/sequence mode、typed action全件、generation、親day/phase、§5.1 allowlist
source sliceのcanonical bytes/hashを含む全fallible preflightを`ClientState.apply_server_event`後かつcredential `save`前に完了し、
private prepared valueとして保持する。preflight hookは1件だけ登録でき、v1ではnullで従来処理を変えない。
`save`と`set_last_seq`の成功後、`_publish(ServerEvent)`の直前はprepared valueのopaque wrapと
identity registryへの登録だけを行う。新しいvalidation、canonicalization、hash、外部callback、awaitを置かない。
credential save失敗、schema/sequence/authentication/preflight失敗、pre-auth resume buffer中にはcommitted observationを発行しない。

認証ownerはconfigや任意引数から得ない。connection generation開始時にnullへ戻すprivate
`_authenticated_player_id`を置き、期待した`session.joined`または`session.resumed` payloadの`player_id`からexactly onceで設定する。
resumeではbufferをcommitする前に設定する。既知playerやretained `session.ready`と不一致なら既存どおりfatalとし、observationを
発行しない。connection tokenはproof object、hash、log、public auditへ含めない。

### 2.2 WorldState / WorldReducer

`WorldState.run`はnetwork event streamをsingle consumerとして順に取り、`_consume(ServerEvent)`内で同期的に
`WorldReducer.apply_server_event`を呼ぶ。次eventをiteratorへ要求する前にreducer適用とworld commitが終わる。
このyield境界を、exact event objectに対する一回限りclaimの期限とする。

`WorldReducer`はhistoryのlocal `order`、live適用直前phase、state-sync replay中に採用した直前`PHASE_STARTED`を所有する。
これらを外部callerが再構成してはならない。reducerは任意sidecarを受け取らず、read-only observerへ実際のrecord appendと
sync entry位置を通知する。

## 3. Network commit observation

### 3.1 opaque型

network層に次のruntime-only型を置く。public constructorとmapping factoryは提供しない。credential save前の
`PreparedNetworkInboundV2`はNetworkClient private localから出さず、module-private issuer tokenを受ける`init=False`の
`NetworkCommittedInboundV2` wrapだけをsave成功後に登録する。`StructuralInboundCandidateV2`との継承・変換関係を持たせない。

```text
NetworkCommittedInboundV2 = opaque {
  schema_version: "aiwolf.network-committed-inbound.v2",
  event_object: exact immutable ServerEvent object,
  game_id: exact NetworkClient.config.game_id,
  player_id: exact authenticated connection owner,
  protocol_version: exact active validator version,
  connection_generation: exact private NetworkClient generation,
  sequence_mode: "CONTIGUOUS" | "RESUME_REPLAY" | "SYNC_BARRIER",
  previous_committed_seq: int,
  committed_seq: exact event.seq,
  credential_checkpoint_seq: exact committed_seq,
  client_snapshot_generation_after: int,
  action_generation_after: int,
  typed_actions_after: tuple[ActionHandle, ...],
  prepared_source_slices: opaque tuple from exact pre-save preflight,
  issuer_capability: process-local object identity  # hash/serialize禁止
}
```

`LifecycleChanged`の現行public型にはconnection generationがないため、その値だけをauthority準備完了の証拠にしない。
authority opt-in時のactual `NetworkClient._set_lifecycle`は、作成したexact `LifecycleChanged` objectに対して次のinternal observationも
同じprivate issuerで発行する。public constructor、mapping factory、wire fieldは追加しない。

```text
NetworkLifecycleObservationV2 = opaque {
  lifecycle_event_object: exact LifecycleChanged object,
  connection_generation: exact private NetworkClient generation at object creation,
  previous: exact lifecycle_event_object.previous,
  current: exact lifecycle_event_object.current,
  ready_after_sync_identity: SyncCommitIdentityV2 | null,
  issuer_capability: process-local object identity
}
SyncCommitIdentityV2 = {
  server_event_object: exact committed game.state_sync ServerEvent object,
  event_id, seq, connection_generation
}
```

`ready_after_sync_identity`は、同じ`_commit_server_message`呼出しで`SYNC_BARRIER`のcheckpoint保存、observation登録、
`ServerEvent` publishが成功し、その直後に`CONNECTED`へ遷移するときだけ、そのexact sync eventから設定する。他のlifecycleではnullである。
future generationを返し得る`source.snapshot()`、現在のclient property、event ID/seqだけから後付け構成しない。

`typed_actions_after`は`player.action_state`と`game.state_sync`だけで、当該eventを`ClientState`へ適用した直後の全actionをreceived
orderで保持する。他typeは空tupleである。actionをfilterして保存しない。tupleの全handleはobservationと同じconnection generation、
`action_generation_after`、親action-stateのday/phaseでなければcredential save前のpreflightでfatalとする。

`CONTIGUOUS`はcommit直前のstate `last_seq + 1 == event.seq`、`RESUME_REPLAY`はrequested checkpointから検査済みbuffer内の
exact連続位置、`SYNC_BARRIER`は`_awaiting_sync`かつ受理済み`game.state_sync`に限る。modeは`_commit_server_message`のcallerが
closed enumで渡し、message typeから後付け推測しない。sync barrier以外でsequence gapがあれば既存failure経路へ進み、
observationを作らない。

prepared valueは全closed enum、owner、event identity、typed tuple、必要source bytes/hashを検証済みで、post-save wrapはそれらの
参照代入だけである。
observationはevent queueへ別itemを追加せず、既存`ServerEvent`の1 slotに対応するside registry entryである。registry capacityは
event queue capacity以下、keyはpreflight済みevent object identityで重複不能とする。したがって新hookはpost-saveに新しい
consumer-overrun条件を追加しない。既存`_publish`のfailure分類・event順序は変更しない。

### 3.2 発行・claim・廃棄

`NetworkClient`はauthority対象type `player.action_state / game.state_sync / game.event`とauthority opt-in時のlifecycle eventについて、
event object identityをkeyに未claim observationを最大event queue容量まで保持する。event IDや`(seq, generation)`だけをkeyにしない。

Phase 6 runtime compositionはevent iteration開始前にexact `NetworkClient`からprocess-local
`InboundAuthorityClaimCapabilityV2`を1件だけ取得し、`_Phase6NetworkEventSource`のprivate fieldへ所有させる。2件目、iteration開始後、
別clientのcapabilityを拒否する。capabilityをWorldState、sink、event、snapshotへ渡さない。sourceは内部tokenを閉包した次のmethodだけを
`WorldState`へ公開する。

```text
claim_committed_inbound(event_object)
  -> NetworkCommittedInboundV2 | null
claim_lifecycle_observation(lifecycle_event_object)
  -> NetworkLifecycleObservationV2 | null
```

wrapper内部でexact NetworkClientへ `(event_object, private_capability)` を渡し、同じobject identity、未claim、対象type、
issuer/capability一致の場合だけ対応するobservationをpopして返す。値が等しい別`ServerEvent` / `LifecycleChanged`、
任意dict、`StructuralInboundCandidateV2`、bare player/generation/event IDではclaimできない。一度claimしたeventは再claimできない。
`NetworkClient.events()`がyieldから再開した時点で未claim observationを捨てる。publish失敗、consumer overrun、stream close、
connection generation更新でも未claim集合を消去する。したがってproofはqueue外へ再発行されず、保持数もboundedである。

reconnectでgenerationを進めるときは、(1) server/lifecycleを含む旧generationの未claim集合を消去、(2) `begin_connection`でactionをinvalidate、
(3) `RESUMING/JOINING` lifecycle eventをenqueue、(4) 新generationのserver eventをenqueue、の順とする。queue内に残っていた旧eventは
値としてWorldへ届いてもclaim不能なのでauthorityを全失効させる。特に旧`CONNECTED`は、対応する旧sync pendingがWorldに残っていても
lifecycle observationをclaimできず、`PUBLISH_SYNC`できない。FIFO上、`RESUMING/JOINING`は新generationのserver eventより先に
Worldへ届き、新sync成功前に新generation authorityを公開しない。

v1やauthority sinkを持たないconsumerには従来どおり`ServerEvent`を渡し、observationはyield再開時に捨てる。既存
`ClientEvent` wire表現、`ServerEvent.as_message()`、protocol schemaは変えない。

## 4. Reducer前transaction

### 4.1 注入境界

`WorldState`には既定nullの`InboundAuthoritySinkV2` protocolを任意注入する。world層はdiscussion型をimportしない。sinkがある場合、
`_consume(ServerEvent)`は次の順序を守る。

authority有効構成ではruntime compositionがexact NetworkClient、`_Phase6NetworkEventSource`、preflight、sinkを一組にし、wrapper issuerと
sink issuerが同一であることを示すprocess-local registration receiptを`WorldState` constructorで1回だけ照合する。任意の
`NetworkEventSource`実装や後付けsinkはauthority有効化に使えない。receipt/tokenはWorldのevent処理APIへ露出しない。

1. sourceからexact event observationをclaimする。対象typeなのにclaim不能ならevent自体は従来どおりreduceするが、authorityは0件とし、
   sinkのgeneration authorityをinvalidateする。
2. reducer適用前のworld version、`last_applied_seq`、phase、history retention/orderを読み、
   `sink.begin(observation, event, pre_state)`で`InboundAuthorityTxnV2`を作る。
3. transactionがobservation内のpre-save prepared source sliceとexact event/pathを照合し、stagingへ参照する。sourceの新しい
   canonicalization/hash生成は行わない。ここまでにawaitを挟まない。
4. 同じevent objectを`WorldReducer.apply_server_event(event, observer=txn)`へ1回だけ渡す。
5. reducer結果と実record emissionをtransactionが検証し、まだ非公開の`AuthorityDeltaV2`を返す。ここではsidecarをcommitしない。
6. `WorldState._commit_world_and_authority(cause, delta)`へ渡し、world snapshotとauthority snapshotを同じnext versionで公開する。

`_consume(LifecycleChanged)`では、`current=CONNECTED`のときに`claim_lifecycle_observation(exact_event_object)`をreducer/lifecycle適用前に
1回だけ呼び、結果を中央commitのcauseへ渡す。World、sink、任意callerはgenerationやsync identityを引数で補えない。他のlifecycleも
同じexact objectでclaimしてregistryを消費するが、中央表に従いauthorityをinvalidateする。claim不能な`CONNECTED`をpublic値だけで
補完しない。

sinkがnullのv1構成はclaim、txn、observer、authority snapshot処理を一切呼ばず、従来の
`WorldReducer.apply_server_event(event)`とworld commitだけを実行する。authority用例外がv1経路へ到達する分岐を置かない。

observation/event identity、game/protocol/seq、owner、generationのいずれかが違えばtxnを作らない。sidecar検証例外は
WorldStateをFAILEDにし、部分sidecarを公開しない。単にauthority対象外、malformed ability、phase欠測、history retentionで即時drop
されたrecordはworld処理を失敗させず、その対象sidecarだけを作らない。

### 4.2 neutral reducer observer

`WorldReducer`は既定nullのread-only observerへ、次だけを通知する。observerは値を返さず、ゲームstateを変更できない。

```text
before_sync_reset(event)
before_sync_history_entry(index, immutable_entry)
record_appended(actual_typed_record, origin)
after_sync_history_entry(index, accepted_phase_record_or_null)
after_apply(ReductionResult, retained_record_identities)
```

`origin`はnetwork event object identityと、liveなら`LIVE_EVENT`、syncなら`SYNC_HISTORY`＋history indexを持つprocess-local値である。
`_append`が受け取った実typed recordを通知し、caller supplied record/orderは受けない。state-sync loopは`enumerate(history)`を使い、
現在entryのraw `event_type=PHASE_STARTED`を検証し、そのentry処理中に実`PhaseTransitionRecord`をappendした場合だけを
「採用したphase source」として更新する。
unknown/malformed entry、後続entry、別sync envelopeをphase sourceにしない。

reducerはobserverがnullなら現行と同じ動作をする。observer追加でrole branch、permission判定、protocol解析を行わない。

### 4.3 全World commitの中央契約

authority sinkを有効にした`WorldState`では、既存の全`_commit()`呼出しをprivate
`_commit_world_and_authority(cause, delta=None)`へ集約する。対象はServerEventだけでなく、chat/player list、transport notice、
lifecycle、freshness変更、deadline、unknown noticeを含む。個別branchからworld versionだけを進めてはならない。

中央commitは次の2段階である。

1. **prepare（失敗可能）**: `next_version=current+1`の`WorldSnapshot`を作り、そのsnapshotのworld version、
   `last_applied_seq`、freshness、history retentionと、現在authority state、cause、optional deltaから
   `PreparedAuthorityStateV2`を作る。authority対象外commitはsidecar/sourceをcarryまたはretention pruneし、lifecycle/generation/
   gap/failure causeはinvalidateする。全hash、identity、retention、deltaを検証し、新しい`asyncio.Event`もここで準備する。
2. **publish（非失敗）**: await、外部callback、canonicalization、validationを行わず、`_version`、`_snapshot`、
   `_authority_state`、`_update_event`をprepared値へ順に参照代入し、最後に旧update eventをsetする。sink methodを呼ばない。

`PreparedAuthorityStateV2.snapshot.world_version == WorldSnapshot.version == next_version`、両者の`last_applied_seq`が一致しなければ
prepareを失敗させる。positive sidecarに変更がないcommitでもauthority snapshotを同じnext versionへ作り直す。
`WorldState.inbound_authority_snapshot()`はWorldStateが所有する `_authority_state.snapshot`だけを返し、sink内のstagingを公開しない。

authority stateは次のclosedな世代準備状態を持つ。`sync_identity`の`server_event_object`はprocess-local exact object identity、
hash対象はそのeventのstable `{event_id, seq, connection_generation}`だけとする。

```text
GenerationReadinessV2 = {
  status: "EMPTY" | "PENDING_SYNC" | "READY" | "INVALID",
  connection_generation: int | null,
  sync_identity: SyncCommitIdentityV2 | null
}
```

`PENDING_SYNC`は同generation・同sync identityのprivate pending一式を持つがpublic sidecarは空、`READY`だけが同generationの
positive sidecarを許す。`EMPTY / INVALID`はgeneration、sync identity、pending、positive、sourceをすべてnull/空とする。
`PENDING_SYNC / READY`ではgenerationとsync identityが必須である。遷移条件の不一致はcarryせず`INVALID`へ進める。

`cause`とauthority transitionは次のclosed表だけを許す。

| consumed cause / exact gate | transition | sidecar/sourceの扱い |
|---|---|---|
| verified `player.action_state`かつ`CONTIGUOUS`、readiness=`READY`、observation/readinessが同generation | `REPLACE_ACTIONS` | action全置換、retained ability carry、readiness維持、seq/version更新 |
| 上記gateを満たさない`player.action_state`（`RESUME_REPLAY`、sync公開前、世代違いを含む） | `INVALIDATE` | positive/pending/sourceを全消去 |
| verified successful `game.state_sync`かつ`SYNC_BARRIER` | `REPLACE_PENDING_SYNC` | sync identityと新action/ability/sourceを同generation private pendingへ全置換。public positiveは空 |
| verified successful `game.state_sync`かつ`CONTIGUOUS`、readiness=`READY`、同generation | `REPLACE_READY_SYNC` | 新sync一式を即時positiveへ全置換し、readiness=`READY`のsync identityも更新。lifecycle再発行を待たない |
| 上記2経路以外の`game.state_sync` | `INVALIDATE` | `RESUME_REPLAY`はpre-auth bufferでfatal。準備前/世代違いのCONTIGUOUSはpositive化せず全消去 |
| exact claim済み`NetworkLifecycleObservationV2(current=CONNECTED)`かつpending generationと`ready_after_sync_identity`がobject identityを含め完全一致 | `PUBLISH_SYNC` | 当該private pendingだけをpositive化し、readiness=`READY`へ移す |
| `CONNECTED` lifecycle observationがclaim不能、identity欠測、pending欠測、世代/sync identity不一致 | `INVALIDATE` | public/pending/sourceを全消去。`source.snapshot()`で補わない |
| verified `game.event`かつ`CONTIGUOUS`、readiness=`READY`、同generation | `APPEND_OR_CARRY` | actual ability sidecarだけappend、それ以外はcarry、retention prune、seq/version更新 |
| verified other `ServerEvent`（chat、player list/deaths、action receiptを含む） | `CARRY` | sidecar carry、retention prune、reducer last seq/version更新 |
| action accepted/rejected、timing等の後続typed notice | `CARRY` | source snapshotを読まず、現在sidecarとactual reducer seqをnext versionへcarry |
| `SequenceGapDetected`、claim不能、sync失敗 | `INVALIDATE` | positive/pending/sourceを全消去 |
| `JOINING/RESUMING/SYNCHRONIZING/RECONNECT_WAIT/STOPPING/ENDED/FAILED` | `INVALIDATE` | positive/pending/sourceを全消去 |
| freshness `ENDED/FAILED`、central prepare/txn/reducer失敗 | `INVALIDATE` | positive/pending/sourceを全消去 |

`CARRY`はreadinessとprivate pendingを同じ状態のまま次versionへ運べるが、`PENDING_SYNC`をpositive化できない。表にないcause/transition、
pending syncをordinary carryでpositive化する処理、`source.snapshot()`の先行last seq/generationをnext authorityへコピーする処理を拒否する。
1つのconsumed eventがServerEventとtyped noticeの2 commitを生む場合も各commitを表の順で別versionへ進め、両snapshotのversionを常に
一致させる。

sinkがnullのv1構成は従来の`_commit()`実装をそのまま使い、中央authority prepare/publish、claim、observerを全て迂回する。

### 4.4 abortとFAILED

reducer、observer、prepared source照合、txn検証、world/authority snapshot prepareのどこで例外が出ても、例外を外へ伝える前に
`WorldState`は次の非失敗abortを実行する。

1. staged txn/delta/source sliceを破棄する。
2. published `_authority_state`をprocess内preconstructed `INVALID_EMPTY`へ参照代入し、sidecar、private source bytes、positive snapshotを
   即時0件にする。旧positive authorityを保持したままFAILEDへ進まない。
3. freshnessをFAILEDにし、authority callbackなしのfailure commitでFAILED `WorldSnapshot`と空authority snapshotを同じnext versionへ
   公開する。failure commit前の短い区間でもauthority queryはempty/invalidだけを返す。

abort自身は外部値のhash、allocation、callback、awaitを行わない。failure commitのempty authority materialはWorldState初期化時に
準備済みのclosed sentinelを使う。sync途中でreducer mutable stateが変化済みでもworldを再利用せずFAILEDで終了し、旧sidecarを
rollback復活させない。

## 5. immutable sourceとruntime sidecar

### 5.1 PrivateSourceSliceV2

transactionはvalidated parsed valueを次のallowlist pathだけで解決する。raw transport JSON bytesの同一性は主張しない。

- action primary: `/payload/actions/{i}` または `/payload/action_state/actions/{i}`
- action parent: `/payload` または `/payload/action_state`
- live ability: `/payload`
- sync ability: `/payload/history/{i}`
- sync phase: `/payload/history/{preceding_i}`

```text
PrivateSourceSliceV2 = opaque {
  event_identity: {game_id, protocol_version, event_id, seq, message_type},
  player_id,
  connection_generation,
  source_path,
  canonical_source_bytes,
  source_sha256
}
```

bytesはcredential save前のexact `InboundAuthorityPreflightV2`が、T514 canonicalizationでimmutable event subtreeから1回だけ作り、
`sha256(bytes)`をprepared valueへ保存する。外部Mapping、caller supplied hash、pointer文字列を入力に取るpublic factoryは置かない。
prepared valueはsave成功前にclaim不能で、save成功後のobservationとexact event objectにより初めてruntime sourceとなる。
private source bytesはprompt/public auditへ出さず、runtime sidecar store内だけに置く。

storeはaction parent/primary sliceを最新action generationと共に全置換する。ability/result/phase sliceは対応するretained
`AbilityResultRecord`がある間だけ保持し、history eviction後に参照0となったsliceを同じtransactionで除去する。成功した
state-syncでは旧record/action sidecarとsource sliceを全置換し、不成功syncでは新sidecarをcommitせず既存authorityをinvalidateする。

### 5.2 runtime型と1対1条件

runtime用にconstructor非公開の `VerifiedInboundRefV2`、`VerifiedProvenanceSidecarEntryV2` を設ける。structural型をsubclassせず、
generic `promote_runtime_authority_v2(candidate)`は引き続き常時失敗とする。runtime factoryは`InboundAuthorityTxnV2`だけが呼べる。

action sidecarはobservationの全`typed_actions_after`と親`actions` arrayを同じreceived indexでzipし、件数、順序、全typed field、
connection/action generation、day/phaseをexact比較する。0 actionは合法な空authority setである。1件でも不一致なら当該eventの
action authority全体を作らず、部分catalogを残さない。さらにlive `player.action_state`は`CONTIGUOUS`かつ中央authority stateが
同じconnection generationの`READY`である場合だけpositive化する。`RESUME_REPLAY`または`PENDING_SYNC / EMPTY / INVALID`中のactionは、
typed整合が取れてもauthorityを全失効させる。

ability sidecarはclosed event matrixをreducerが実際にappendした`AbilityResultRecord`へ照合する。

| event | 必須source値 | null条件 |
|---|---|---|
| `INSPECT_RESULT` | target＋result | role null |
| `MEDIUM_RESULT` | target＋result | role null |
| `INSPECT_DEAD_ROLE_RESULT` | target＋role ID | result null |
| `GUARD_SUCCEEDED` | target | result/role null |

liveは適用直前のactual reducer phase、world version、last applied sequenceから
`ReducerPhaseWitnessV2`を作る。ここで`last_applied_sequence_before`は現在eventを渡す直前の
`WorldReducer.last_applied_seq`であり、credential commit後のcurrent event seqではない。live sidecarはgenerationのsync成功後、
`sequence_mode=CONTIGUOUS`、`reducer.last_applied_seq == observation.previous_committed_seq`、
`event.seq == reducer.last_applied_seq + 1`を全て満たす場合だけ作る。resume replay中のeventはobservationを検証してもpositive
sidecarを公開せず、後続syncで置換する。phaseが無い場合はrecordを保持してもsidecarを作らない。syncは同じsync envelope内でresult entryより
前にあり、reducerが採用した最後の`PHASE_STARTED` entryのactual path/bytes/hashを使う。indexや値だけを保存しない。

typed object hashは実`ActionHandle`または実`AbilityResultRecord`のcanonical bytesから作る。identityはactionが
`(action_generation, received_index)`、recordが`("ability_result", local_order)`である。同じidentityへの複数source、同じprimary
sourceの再利用、source/typed hash不一致をtxn commitで拒否する。

### 5.3 snapshotと失効

sinkはworld commitごとにimmutable `InboundAuthoritySnapshotV2`を発行する。

```text
InboundAuthoritySnapshotV2 = {
  schema_version,
  game_id, player_id, protocol_version, connection_generation,
  last_committed_server_seq,
  world_version,
  authority_revision,
  readiness_status: "EMPTY" | "PENDING_SYNC" | "READY" | "INVALID",
  readiness_connection_generation: int | null,
  readiness_sync_identity: {event_id, seq, connection_generation} | null,
  action_generation,
  action_sidecars: received-order tuple,
  ability_sidecars: retained-record-order tuple,
  snapshot_sha256
}
```

snapshotの`world_version`は中央commit開始時のcurrent version+1で、同じpublishが公開する`WorldSnapshot.version`とexactly一致する。
一致しないsnapshotは公開せずWorldをFAILEDにする。

snapshotはprivate source bytesを含めずref/hashだけを含む。future capture adapterはexact snapshot objectとworld versionを要求し、
structural candidateや各tupleの抜粋を受けない。本単位ではpublic channel/disclosure authorityへの昇格APIを有効化しない。

将来のruntime captureは `WorldState`が現在保持するexact `WorldSnapshot` objectとexact `InboundAuthoritySnapshotV2` object、
world instance identity、world version、authority revisionを一括照合しなければならない。callerが保持する旧positive snapshotはimmutableな
過去値として読めてもcurrent authorityへ再利用できない。abort後はcurrent pointerが`INVALID_EMPTY`へ替わるため、旧objectのhashが
正しくても照合に失敗する。

`PENDING_SYNC` snapshotはreadiness identityだけを示し、action/ability sidecar tupleは空である。`READY`だけがpositive tupleを持てる。
`JOINING / RESUMING / SYNCHRONIZING / RECONNECT_WAIT / STOPPING / ENDED / FAILED`へのlifecycle変化、connection generation変化、
sequence gap、claim不能、sync失敗でaction/ability authorityを全消去してrevisionを進める。新generationの旧recordを
connection owner proofとして再利用しない。`SYNC_BARRIER`なら同generationのexact `CONNECTED` observation、既に`READY`の
`CONTIGUOUS` syncならそのsync commit自体が成功するまでpositive authority snapshotを出さない。

## 6. fail-closed表

| 条件 | world処理 | runtime authority |
|---|---|---|
| schema/protocol/game/auth失敗 | 既存fatal | 発行0 |
| credential save失敗 | 既存fatal、event非公開 | 発行0 |
|通常sequence gap | 既存gap/reconnect | 発行0、既存authority失効 |
| pre-auth resume buffer | commit待ち | 発行0 |
| sync barrier受理 | 現行sync適用 | 成功txnをprivate pendingへ全置換。matching lifecycle observationまでpublic 0 |
| READY中の同generation contiguous sync | 現行sync適用 | 成功txnを即時全置換 |
| resume replay / sync公開前 / 別generationのaction state | worldは現行処理 | 全失効、部分action authority 0 |
| claim不能または別generation/別sync identityの`CONNECTED` | lifecycleは現行処理 | pendingを公開せず全失効 |
| plain `ServerEvent` / dict / structural candidate | 従来reduce可 | claim・昇格不可 |
| observationの別event object・二重claim | 従来reduce可 | 当該generation失効 |
| action件数/順序/typed値不一致 | worldは現行処理 | action authority 0 |
| ability malformed/phase source欠測 | malformed/recordは現行どおり | 対象sidecar 0 |
| reducer callback/hash/identity矛盾 | World FAILED | staged sidecar 0、旧positive authorityも即時0 |
| reconnect/lifecycle stale | 現行freshness | 全authority失効 |

authority欠測をNONE、silence、public chat、別actionへ変換しない。Worldの既存読取APIは維持するが、sidecarのないrecord/actionを
v2 authority catalogへ載せない。

## 7. Offline test contract

全testはfake socket、memory credential store、実`ProtocolMessageValidator`、実`NetworkClient`、実`WorldState/WorldReducer`で行う。
provider/LLM/game server/Actions実行は0とする。

### Positive

1. **I-P1 contiguous action-state**: 認証・初回sync後の連続`player.action_state`で、credential saveがevent seqまで完了してから
   observationを1回だけclaimし、readiness=`READY`と同generationを照合して全raw actionとtyped handleのsidecarをreceived orderで作る。
2. **I-P2 state-sync**: sync barrierのaction_state全件とhistoryを同じobservationへ束縛し、採用済み`PHASE_STARTED`に続く
   4種ability resultをactual record order/path/hashでprivate pendingへ登録する。exact barrier sync identityとgenerationを持つ
   `CONNECTED` lifecycle observationをclaimしたcommitだけがpendingを公開する。
3. **I-P3 live ability**: `READY`の同generationでcurrent reducer phaseを持つ連続live ability eventについて、適用前world
   version/sequenceのwitnessとactual recordを1対1登録する。
4. **I-P4 resume replay**: `session.resumed`が確定したownerをbuffer replay commit前に固定し、requested checkpointから連続な
   replay observationだけを新connection generationで検証する。replay由来positive sidecarは公開せず、後続sync成功で初めて置換する。
5. **I-P5 retention**: history evictionと同時にrecord sidecar/source sliceが消え、残るrecordのidentity/hashは変わらない。
6. **I-P6 zero action**:合法な0-action stateは空のaction authority setとしてcommitし、旧action sidecarを残さない。
7. **I-P7 central commit**: chat/player list/transport noticeを含むauthority対象外commitでも、sidecarをcarryしながらworld/authority
   snapshotのversionとlast applied sequenceが同時にnext値へ進む。
8. **I-P8 post-save path**: preflight済みprepared valueについてcredential save後はopaque wrap、identity registry登録、既存event publish
   だけが走り、新しいvalidation/hash/callbackがない。
9. **I-P9 contiguous state-sync**: 既に`READY`のgenerationへ連続sequenceの`game.state_sync`を適用すると、同じ中央commitで
   `REPLACE_READY_SYNC`し、追加`CONNECTED`なしで新sync sidecarがpositiveになる。

### Negative

1. **I-N1 arbitrary input**: dict、plain/同値別objectの`ServerEvent`、structural candidate、bare player/generation/hashからclaim・runtime
   sidecar生成ができない。
2. **I-N2 pre-commit**: schema不正、game/protocol不一致、authentication前event、credential save失敗、通常sequence gapでは
   observationとsidecarが0。
3. **I-N3 claim ownership**: 別client、2件目consumer、別event identity、二重claim、yield再開後claimを拒否する。World公開APIは
   capability引数を持たず、任意tokenを渡す経路がない。
4. **I-N4 action binding**: raw/typed件数、順序、type、target/channel、day/phase、action/connection generationの各1-field変異で
   event全体のaction authorityが0。先頭の正常1件だけを残さない。
5. **I-N5 live phase**: pre-reducer phase欠測、world version/last sequence witness 1-bit変異、別generation sourceを拒否する。
6. **I-N6 sync phase**: result後のphase entry、別sync envelope、誤index/path、phase source bytes/hash/day/phaseの各1-field変異を拒否する。
7. **I-N7 typed result**: 4種matrixのtarget/result/role欠測・余分field・型違い、actual record order/hash不一致ではsidecar 0。
8. **I-N8 reconnect**: lifecycle離脱またはgeneration更新直後に旧action/ability snapshotをqueryしてもauthority 0。新sync成功前に復活しない。
9. **I-N9 partial transaction**: 2件目actionまたはhistory後半で矛盾した場合、先行sidecar/source sliceも公開しない。
10. **I-N10 retention/reset**: evicted record、失敗sync前のstaged record、成功sync前generationのsource refを再利用できない。
11. **I-N11 live sequence meaning**: credential checkpointのcurrent seqやresume replay seqを
    `last_applied_sequence_before`へ代入したwitnessを拒否し、exact reducer pre-state値だけを受理する。
12. **I-N12 abort**: sync partial mutation、reducer/observer例外、txn hash不一致、world/authority commit prepare例外の各点で、
    staged sidecar 0、旧positive authority即時0、最終world freshness FAILEDを確認する。
13. **I-N13 post-save failure surface**: typed action/generation/owner/modeの各不一致はcredential save前に停止する。save後の
    observation side registryはqueue slotを増やさず、既存consumer overrun以外の新failureを発生させない。
14. **I-N14 queued old CONNECTED**: 旧generationのsync eventと`CONNECTED`がqueueに残る間にNetworkを次generationへ進め、
    lifecycle observation registryが先に消えることを確認する。Worldが旧syncをprivate pendingにしてから旧`CONNECTED`を消費しても
    claimはnullで、途中のどのsnapshotにも旧positiveを公開せず`INVALID`になる。
15. **I-N15 generation-ready gate**: `CONNECTED` observationのgeneration、sync event object、event ID、seqを各1-field変異し、
    pendingを公開せず全失効する。`source.snapshot()`が同じ値またはfuture generationを返しても結果は変わらない。
16. **I-N16 action/sync mode gate**: `RESUME_REPLAY` action、sync公開前action、別generation actionを全てinvalidateする。
    readiness前または別generationの`CONTIGUOUS game.state_sync`もinvalidateし、pre-auth replay中のstate syncは既存fatalでobservation 0とする。

既存network/world regressionでは、authority sinkなしのevent型・順序・snapshot・reducer結果がbyte/value同値であることも確認する。

## 8. 実装・review境界

承認後の最小実装read-setは次である。

| file / section | 目的 |
|---|---|
| `ai_client/network/client.py` のauthentication、`_accept_server_message`、`_commit_server_message`、`_set_lifecycle`、generation開始、`events` | owner固定、server/lifecycle observation、旧世代registry消去、one-shot claim（§3.1–3.2） |
| `ai_client/network/types.py` の`ServerEvent`/`LifecycleChanged`/internal event型 | opaque observation protocol。public/wire fieldは不変 |
| `ai_client/network/state.py` のaction-state適用/snapshot | commit時の全typed action取得 |
| `ai_client/world/service.py` の`_consume(ServerEvent/LifecycleChanged)`と全`_commit` call site | reducer前txn、generation-ready中央遷移、world/authority同時commit（§4.1、§4.3–4.4） |
| `ai_client/world/reducer.py` のstate-sync loop、core event、`_append` | actual index/phase/record emission |
| `ai_client/discussion/capture_v2.py` のstructural inbound/sidecar境界 | runtime型を別系統で追加しgeneric promotionを閉じたままにする |
| `ai_client/runtime.py` の`_Phase6NetworkEventSource` compositionだけ | exact NetworkClient claim capabilityとsinkを1組だけ接続 |
| focused network/world/capture authority tests | §7の正負 |

brain、controller、`DiscussionStateStore`、admission client/broker、backend、durable audit、delivery、server、protocol schemaは本単位で
編集しない。runtime sidecar snapshotの生成までを完了条件とし、public channel/disclosure/captureのruntime positiveやT515全Acceptanceを
完了扱いしない。

独立Reviewerは、少なくとも次を実測する。

- credential save前、任意`ServerEvent`、structural candidateからruntime sidecarを作れない。
- resume replay/sync barrier/normal contiguousの3経路でowner・generation・sequence modeが実control flow由来である。
- reducer適用前source hashと適用後actual typed identityがatomicに1対1登録される。
- reconnect、sync failure、retention、partial mismatchが旧または部分authorityを残さない。
- v1 sinkなし回帰、provider 0、protocol/schema/private permission不変。

本設計の独立承認前に実inbound hookを実装してはならない。
