# Phase 6 生成v2 Capture Binding 追加詳細設計

Status: APPROVED
Task: T514
Date: 2026-09-28
Parent: `Docs/ai/design/PHASE6_GENERATION_CONTRACT_V2_DESIGN.md`

## 1. 目的と差分の必要性

親設計§3、§8〜10は、`GenerationCaptureV2` がserver由来authority、catalog、freshness、leaseを同じ判断へ
束縛することを要求する。現行製品には `DiscussionCapture`、`BrainInput`、`DispatchDeadline` とbroker leaseがあるが、
次の接続規則は未定義である。

- 認証済み受信event/action offerからclient内のauthority proofへ変換するclosed型。
- 複数actionとchannel authorityの対応、private recipient欠測時の扱い。
- catalog/input/recipient/captureの非循環hash。
- 最大2 callのbroker leaseと、chat最大4 callを含む判断全体のstate leaseの関係。
- T/P、stage、commit、dispatchの各境界で同じcaptureを再検査するAPIと順序。

本追加設計はその実装境界だけを閉じる。親設計、protocol、ゲーム規則、privacy permission、v1既定、provider holdを
変更しない。channel名、role、本文、表示名からrecipientやauthorityを推測しない。

## 2. 設計判断

人間による新しい製品選択は不要である。現行authorityで証明できる範囲だけを次のように採る。

1. public chatは、認証済みaction offerと、game/player/selfへbind済みのcontextにある
   `AuthorizedChatChannelContext(is_public=True)` の両方が一致する場合だけ対応する。
2. private chatはrecipient集合とauthority revisionがclientへ届かないため、第一版では
   `UNSUPPORTED_PRIVATE_CHANNEL_RECIPIENTS` としbackend call 0で閉じる。
3. 1判断に複数のeligible `ChatAction` がある場合、承認済chat schemaにchat option selectorがない。optionを落とすことも
   hostが1件を選ぶこともせず、`UNSUPPORTED_MULTIPLE_CHAT_OPTIONS` としてbackend call 0で閉じる。
4. self能力結果は、認証済みconnectionへserverが配送したlive event、または同connection用state-sync historyの出所を
   client側で保持できる場合だけ `SELF_ABILITY_REPORT` にできる。protocol fieldは増やさない。
5. state leaseは判断全体を所有し、broker leaseはprovider callを直列化するsegmentとする。brokerの既存2 call上限を
   変更せず、chatで必要な場合だけ既存successor reservationによる第2 segmentを同じstate leaseへ追加する。

## 3. Authoritative source chain

### 3.1 共通受信証拠

`AuthenticatedInboundRefV2` はclient内部だけのimmutable型である。

| field | type / constraint |
|---|---|
| `schema_version` | const `aiwolf.authenticated-inbound-ref.v2` |
| `game_id` | `ServerEvent.game_id`。runtime configと一致 |
| `player_id` | 認証済みconnection owner。`BoundDiscussionContext.context.player_id` と一致 |
| `connection_generation` | 受信時のnetwork connection generation |
| `server_event_id` | `ServerEvent.event_id` |
| `server_seq` | `ServerEvent.seq`、0以上 |
| `message_type` | `game.event` / `game.state_sync` / `player.action_state` のclosed enum |
| `source_path` | liveは`/payload`、sync/actionはRFC 6901のexact path |
| `source_sha256` | pathが指す値のcanonical SHA-256 |
| `protocol_version` | validationに使用した `ServerEvent.protocol_version` |

出所は、protocol validation済みかつ連続sequence検査を通った `ServerEvent` に限る。serverはvisibility filter後のeventを
認証済みconnection ownerへだけ配送し、clientはそのconnection generationを同時に記録する。state-sync historyは
`/payload/history/{index}`、action offerは `/payload/action_state/actions/{index}` または
`/payload/actions/{index}` を使う。index、subtree bytes、event envelopeが一致しない場合は証拠を作らない。

`WorldReducer` の現在のlocal `order` だけは受信出所証拠にならない。後続実装はprotocolを変えず、reducerが値を捨てる前に
上記refをrecord/actionのsidecar provenanceへ保持する。resume後に新しいstate-syncから復元したentryは、新しい
state-sync event ID、index、hashを持つ別のsource identityとする。

`AuthenticatedInboundRefV2` だけをtyped値へ直接結び付けない。必ず次のclosed sidecarを介す。

```text
ProvenanceSidecarEntryV2 = {
  schema_version: "aiwolf.provenance-sidecar-entry.v2",
  subject_kind: "HISTORY_RECORD" | "ACTION_HANDLE",
  record_identity: {record_kind: str, local_order: int} | null,
  action_identity: {action_generation: int, received_index: int} | null,
  typed_value_sha256: sha256,
  source: AuthenticatedInboundRefV2,
  phase_attribution: PhaseAttributionV2 | null,
  action_context: ActionContextRefV2 | null
}

PhaseAttributionV2 = {
  schema_version: "aiwolf.phase-attribution.v2",
  mode: "LIVE_REDUCER_PHASE" | "SYNC_REPLAY_PHASE",
  day: int,
  phase: str,
  world_version_before: int | null,
  last_applied_sequence_before: int | null,
  state_sync_history_index: int | null,
  preceding_phase_entry_index: int | null,
  phase_source: AuthenticatedInboundRefV2 | null,
  reducer_phase_witness_sha256: sha256 | null
}

ActionContextRefV2 = {
  schema_version: "aiwolf.action-context-ref.v2",
  parent_source: AuthenticatedInboundRefV2,
  day: int,
  phase: non-empty str,
  connection_generation: int,
  action_generation: int
}
```

`subject_kind=HISTORY_RECORD` では `record_identity` と `phase_attribution` が必須で `action_identity=null`、
`action_context=null` とする。`subject_kind=ACTION_HANDLE` では `action_identity/action_context` が必須で
`record_identity/phase_attribution=null` とする。

actionのprimary `source` は `/payload/actions/{index}` または `/payload/action_state/actions/{index}` の個別action spec、
`parent_source.source_path` はそれぞれ `/payload` または `/payload/action_state` の親action-state objectである。両refは同じ
event envelopeを指し、primary pathがparentの`actions/{index}`直下で、parent hash、parentの`day/phase`、認証済み受信時の
connection/action generationが `ActionContextRefV2` と一致しなければならない。これによりtyped `ActionHandle` の
day/phase/generationを個別specだけから推測しない。同一action-stateのparent refは同じgeneration内の複数action sidecarで共有できるが、
各primary action refと `(action_generation, received_index)` は共有できない。

live eventでは、reducer適用直前のCURRENT phase、world version、last applied sequenceを
`LIVE_REDUCER_PHASE`として保存する。このmodeは `state_sync_history_index/preceding_phase_entry_index/phase_source=null`、
world/sequenceが非nullで、次のclosed objectのcanonical hashが `reducer_phase_witness_sha256` と一致する場合だけ有効である。

```text
ReducerPhaseWitnessV2 = {
  schema_version: "aiwolf.reducer-phase-witness.v2",
  game_id: exact source.game_id,
  connection_generation: exact source.connection_generation,
  day: exact PhaseAttributionV2.day,
  phase: exact PhaseAttributionV2.phase,
  world_version_before: exact PhaseAttributionV2.world_version_before,
  last_applied_sequence_before: exact PhaseAttributionV2.last_applied_sequence_before
}
```

state-sync replayでは、同じhistory array内でそのentryより前にあり、reducerが実際にphase contextへ採用した最後の
`PHASE_STARTED` entryを `SYNC_REPLAY_PHASE` に保存する。このmodeはworld/sequence/witness hashがnull、history indexと
preceding indexが非nullで、`phase_source` が同じstate-sync event envelopeの
`/payload/history/{preceding_phase_entry_index}` を指す。phase source subtree hashを照合して既存parserで
`PHASE_STARTED` とday/phaseを検証し、その値がattributionとrecordに一致することを要求する。record entryより後、別envelope、
path/hash不一致、対応するphase source欠測はauthority proofに使わない。ability wire/history result subtree自体にday/phaseが
無くても、typed record hashとこのhash-bounded phase attributionの組で一意に束縛する。

sidecar生成は次の順で行う。(1) source pathを解決しsubtree hashを照合、(2) 現行と同じtyped parser/reducer規則で値を作る、
(3) history recordはsourceのpayload field、受信時に割り当てた`local_order`、phase attributionから完成した
`AbilityResultRecord` 全fieldを、actionは個別source field、`ActionContextRefV2`、認証済み受信metadataから完成した
`ActionHandle` 全fieldをcanonical化し、
実際にstore/viewへ置くtyped objectのhashと `typed_value_sha256` を照合、(4) recordは `(record_kind, local_order)`、actionは
`(action_generation, received_index)` へ登録する。同じtyped identityへの2 entry、同じprimary source refの再利用、identity/hashの
不一致を拒否する。1 typed値にexactly 1 sidecarだけを許す。

### 3.2 self worldとcontext

`validate_discussion_bootstrap` はmanifest/contextのcanonical hash、network game ID、player IDを検査し、最初のCURRENT
`WorldSnapshot.self_view` のplayer/role/modifierと一致したときだけ `BoundDiscussionContext` を作る。各captureで
`validate_bound_context_snapshot` を再実行する。roleは一致検査だけに使い、channel recipientの導出には使わない。

`WorldSnapshot.version/last_applied_seq`、`DiscussionCapture.fact_revision/base_revision`、
`DiscussionTrigger.connection_generation/action_generation` は親設計の同名fieldへ移す。phase identityは次のclosed objectの
hashとする。

```text
PhaseIdentityV2 = {
  schema_version: "aiwolf.phase-identity.v2",
  game_id: str,
  day: int >= 0,
  phase: non-empty str
}
phase_identity = canonical_sha256(PhaseIdentityV2)
```

### 3.3 action offerとpublic channel

`ActionOptionBindingV2` はreceived orderの全eligible actionを保持する。

```text
ActionOptionBindingV2 = {
  schema_version: "aiwolf.action-option-binding.v2",
  option_id: stage-local oNNN,
  action_kind: "chat" | "vote" | "co_declare" | "ability",
  action_generation: int,
  connection_generation: int,
  phase_identity: sha256,
  action_sha256: canonical hash of the typed ActionHandle,
  source: ProvenanceSidecarEntryV2(subject_kind="ACTION_HANDLE"),
  channel_authority: PublicChannelAuthorityV2 | null
}
```

chat以外の `channel_authority` はnullである。chatはeligible `ChatAction` がexactに1件であり、次の全条件を満たす場合だけ
`PublicChannelAuthorityV2` を作る。

```text
PublicChannelAuthorityV2 = {
  schema_version: "aiwolf.public-channel-authority.v2",
  option_id: oNNN,
  channel_id: exact ChatAction.channel,
  audience: "PUBLIC",
  recipient_scope: "SERVER_FILTERED_PUBLIC",
  context_sha256: BoundDiscussionContext.context_sha256,
  action_source_sha256: canonical_sha256(ProvenanceSidecarEntryV2),
  connection_generation: int,
  action_generation: int,
  phase_identity: sha256,
  authority_revision_sha256: sha256 of all preceding authority fields
}
```

`SERVER_FILTERED_PUBLIC` は空recipient集合を意味しない。serverがcontent-authorized public recipientsへ配送する既存境界を
表すsymbolic setである。public disclosureのaudienceもPUBLICなので、具体的player IDをclientで列挙せず包含が成立する。
connected recipientの実集合やprivate recipient集合を証明したとは記録しない。

contextにchannelがない、`is_public=False`、source ref欠測、generation/phase不一致、chat action 0件または2件以上なら
capture全体を拒否する。安全な1件だけをcatalogへ残す処理は禁止する。これによりLLMのchannel/action戦略をhost固定へ
変更しない。

### 3.4 self ability disclosure

`DisclosureAuthorityV2` を作れるのは `AbilityResultRecord` と同じ値を指すauthenticated sidecarがあり、次を全て満たす
場合だけである。

- source messageはlive `game.event`、または認証済み`game.state_sync`のhistory entry。
- event typeはclosed matrix
  `INSPECT_RESULT / MEDIUM_RESULT / INSPECT_DEAD_ROLE_RESULT / GUARD_SUCCEEDED` のいずれか。
- serverの既存event routingで当該typeはPRIVATEかつrecipientはability actorであり、受信connection ownerと
  `capture.player_id` が一致する。
- event type、target、result/roleのcanonical値がsource subtreeから作ったtyped `AbilityResultRecord` と一致し、day/phaseは
  sidecarの `PhaseAttributionV2` とrecord hashへ一致する。
- source sequenceはcaptureの `last_applied_sequence` 以下で、source connection generationはcaptureと同じ。
- 選択可能なchat optionに §3.3のPUBLIC authorityがある。

```text
DisclosureAuthorityV2 = {
  schema_version: "aiwolf.disclosure-authority.v2",
  authority_kind: "SELF_ABILITY_REPORT",
  source: ProvenanceSidecarEntryV2(subject_kind="HISTORY_RECORD"),
  evidence_ref: {record_kind: "ability_result", order: int,
                 visibility: "AUTHORIZED_PRIVATE"},
  actor_player_id: exact capture.player_id,
  event_type: closed enum,
  target_player_id: str,
  result_id: str | null,
  revealed_role_id: str | null,
  read_visibility: "AUTHORIZED_PRIVATE",
  disclosure_audience: "PUBLIC",
  channel_authority_sha256: sha256(PublicChannelAuthorityV2),
  world_version: int,
  fact_revision: int
}
```

`result_id` と `revealed_role_id` のnull条件は受信eventの既存validatorと同じでなければならない。authority objectの
canonical hash順で`d000...`を割り当てる。sidecarが無い旧record、unknown event type、別connection、別player、
private channel、payload不一致はdisclosure catalogへ入れない。triggerの必須候補またはPF2期待候補が欠ける場合は
`CATALOG_INCOMPLETE` で判断全体を止め、別の発話へ置換しない。

## 4. Closed captureとhash DAG

### 4.1 Canonicalization

全hashは既存 `ai_client.discussion.context.canonical_json_bytes` のUTF-8 bytesに対するSHA-256 lowercase hexを使う。
型はclosed dataclassまたはclosed JSON objectとし、extra field、重複ID、unordered set、NaN/Infinityを拒否する。
tupleはJSON arrayへ写し、決定的順序を各catalog規則またはreceived action orderで固定する。hash前の値にprompt、model出力、
表示名、自由形式例外を含めない。

monotonic時刻はfloatをhashしない。`DispatchDeadline.not_after_monotonic` を
`floor(value * 1_000_000)` した非負整数 `expires_at_monotonic_us` とし、期限判定は
`ceil(clock() * 1_000_000) < expires_at_monotonic_us` のときだけ有効とする。これは現行controllerが
server由来 `CurrentPhaseDeadline.local_deadline_monotonic` からguardを引いたcutoffを安全側に丸める。

### 4.2 Hash DAG

hashは次の一方向だけに依存する。

1. `source_sha256`: 受信subtree。actionはprimary action specと親action-stateの両ref/hashを含む。
2. `phase_identity`、`action_sha256`、`authority_revision_sha256`。
3. `option_catalog_sha256`: received orderの全 `ActionOptionBindingV2`。
4. `recipient_proof_sha256`: chatではPUBLIC authority tuple、非chatではclosed sentinel
   `{schema_version, mode:"NOT_APPLICABLE", trigger_kind}`。
5. `catalog_sha256`: `GenerationCatalogV2` と、各短縮IDからsource pointer/authority hashへのclosed map。
6. `profile_bundle_sha256`: 次のclosed objectのhash。

```text
ProfileBundleV2 = {
  schema_version: "aiwolf.generation-profile-bundle.v2",
  generation_profile: "phase6_v2",
  system_instruction_version: non-empty str,
  generation_config_fingerprint: sha256
}
```

7. `stage_schema_sha256s`: 適用stageごとのfactory schema bytes hash。chatは `chat_plan,message`、他triggerは対応する
   1 stageだけを持ち、stage順は `chat_plan,message,pre_vote,co_opportunity,ability` の部分列とする。
8. `input_sha256`: stageに依存しないallowlist capture/state/catalog base、`profile_bundle_sha256`、
   `option_catalog_sha256`、`recipient_proof_sha256`、`stage_schema_sha256s` のhash。lease/capture ID、T plan、
   stage別messagesは含めない。
9. `capture_id`: §4.3のmaterialから `capture_id` 自身を除いたcanonical hash。

同じ対象bytesを複数hashへ含めてもよいが、後段hashから前段objectを逆参照しない。stage schema hashはfactory outputの
canonical schema bytesを指し、provider wireのkey順hashと混同しない。単一 `schema_sha256` を全stageへ流用しない。

### 4.3 GenerationCaptureV2

```text
GenerationCaptureV2 = {
  schema_version: "aiwolf.generation-capture.v2",
  capture_id: sha256 of all following fields,
  base_capture_id: exact DiscussionCapture.capture_id,
  game_id: str,
  player_id: str,
  base_revision: int,
  trigger: exact DiscussionTrigger,
  world_version: int,
  last_applied_sequence: int,
  fact_revision: int,
  phase_identity: sha256,
  action_generation: int,
  connection_generation: int,
  channel_authorities: tuple[PublicChannelAuthorityV2, ...],
  recipient_proof_sha256: sha256,
  catalog_sha256: sha256,
  option_catalog_sha256: sha256,
  input_sha256: sha256,
  profile_bundle_sha256: sha256,
  stage_schema_sha256s: tuple[{stage: closed stage enum, schema_sha256: sha256}, ...],
  state_lease_id: non-empty str,
  expires_at_monotonic_us: int
}
```

chatの `channel_authorities` はexactに1件、非chatは空tupleである。`base_capture_id` はv1 capture materialの照合に使い、
v2 `capture_id` として流用しない。plan/Pは同じcapture objectと `recipient_proof_sha256` をbyte-equalで使う。

`input_sha256` はstage-invariant baseだけを表し、実際に送るinput/messagesを表さない。attemptごとのprojectionは次を使う。

```text
StageProjectionBindingV2 = {
  schema_version: "aiwolf.stage-projection-binding.v2",
  stage: closed stage enum,
  schema_sha256: capture.stage_schema_sha256s[stage],
  base_input_sha256: capture.input_sha256,
  accepted_plan_sha256: sha256 | null,
  canonical_input_sha256: sha256,
  messages_sha256: sha256,
  projection_sha256: sha256 of all preceding fields
}
```

Tでは `accepted_plan_sha256=null`。Tがmechanical/semantic guardを通り、terminal audit ackを得た直後に、guard-accepted
canonical planのhashをstate leaseへexactly onceでCAS記録する。P projectionはそのplan hashを必須とし、選択済み
reply/fact/disclosure surfaceだけを含むcanonical inputとmessagesを作る。P retryは同じ
`StageProjectionBindingV2` をbyte-equalで再利用し、変えられるのはattempt/request ID/seedだけである。

## 5. State leaseとbroker lease

### 5.1 StateGenerationLeaseV2

state leaseはprocess内 `DiscussionStateStore` が所有する。

```text
StateGenerationLeaseV2 = {
  schema_version: "aiwolf.state-generation-lease.v2",
  state_lease_id: initial AdmissionRequest.invocation_id,
  owner_invocation_id: same value,
  lease_revision: int >= 0,
  capture_id: GenerationCaptureV2.capture_id | null,  # null only in PREPARING
  status: "PREPARING" | "RESERVED" | "ACTIVE" | "STAGED" |
          "COMMITTED" | "INVALIDATED" | "RECOVERY_PENDING" | "RELEASED",
  base_revision: int,
  world_version: int,
  last_applied_sequence: int,
  fact_revision: int,
  phase_identity: sha256,
  action_generation: int,
  connection_generation: int,
  input_sha256: sha256 | null,
  catalog_sha256: sha256 | null,
  option_catalog_sha256: sha256 | null,
  recipient_proof_sha256: sha256 | null,
  profile_bundle_sha256: sha256 | null,
  stage_schema_sha256s: closed stage tuple | empty tuple,
  expires_at_monotonic_us: int,
  broker_lease_ids: tuple[str, ...],  # 1..2 from PREPARING, unique, acquisition order
  accepted_plan_sha256: sha256 | null,
  stage_projections: tuple[StageProjectionBindingV2, ...],
  reserved_attempt: AttemptReservationV2 | null,
  attempt_ledger: tuple[AttemptLedgerEntryV2, ...],
  recovery_proof: RecoveryProofV2 | null
}
```

`PREPARING` のexact shapeは `capture_id=null`、5種のhashは全てnull、`stage_schema_sha256s=()`、
`broker_lease_ids=(initial_invocation_id,)`、`accepted_plan_sha256=null`、`stage_projections=()`、
`reserved_attempt=null`、`attempt_ledger=()`、`recovery_proof=null` である。base/freshness field、initial broker ID、owner、expiryだけを
確定し、`lease_revision=0` とする。`RESERVED` へのCASでcapture ID、5種hash、非空stage schema tupleを同時に確定し、以後変更しない。
chat以外の `accepted_plan_sha256` は常にnull、chatではT ACCEPTEDまではnullである。

state lease IDは既存broker protocolへ新fieldを追加せず、最初の `AdmissionRequest.invocation_id` を再利用する。
`broker_lease_ids[0]` は同じIDである。chatが3〜4 callを必要とするときだけ既存successor reservationで第2 IDを得て、
state lock下で同じowner/capture/deadlineへ登録してから利用する。2 segmentを並列activeにしない。TとPは同じstate lease、
同じimmutable captureを使い、successor取得を新しい判断や再captureとして扱わない。

### 5.2 Attempt ledger

```text
AttemptReservationV2 = {
  schema_version: "aiwolf.attempt-reservation.v2",
  stage: closed stage enum,
  attempt: 1 | 2,
  request_id: "phase6-v2:{capture_id}:{stage}:{attempt}",
  derived_seed: stage_seed(capture_id, stage, attempt),
  broker_segment_id: registered broker lease ID,
  call_ordinal: 1 | 2,
  projection_sha256: sha256,
  provider_request_sha256: sha256
}

AttemptLedgerEntryV2 = {
  schema_version: "aiwolf.attempt-ledger-entry.v2",
  stage: closed stage enum,
  attempt: 1 | 2,
  request_id: "phase6-v2:{capture_id}:{stage}:{attempt}",
  derived_seed: uint32,
  broker_segment_id: registered broker lease ID,
  call_ordinal: 1 | 2,
  projection_sha256: sha256,
  provider_request_sha256: sha256,
  status: "STARTED" | "ACCEPTED" | "SCHEMA_INVALID" |
          "MECHANICAL_INVALID" | "BACKEND_FAILED" | "TIMEOUT" |
          "CANCELLED" | "STALE" | "EXHAUSTED",
  started_audit_sequence: int,
  started_record_sha256: sha256,
  terminal_audit_sequence: int | null,
  terminal_record_sha256: sha256 | null
}
```

`status=STARTED` のときterminal 2 fieldは両方null、その他のterminal statusでは両方非nullとし、片方だけの状態を拒否する。
ledger順はSTARTED audit sequenceの昇順で、同じsegment内の`call_ordinal`は1,2のprefix、判断全体ではstage順とattempt順を
親設計どおり保つ。

`reserve_attempt(...)` はstate lock下のCASで `reserved_attempt` をexactly 1件置く。stage/attempt key、request ID、seedが
既存ledgerと重複せず、直前entryにterminal audit ackがあり、broker segmentが登録済みかつclaimed/activatedで、当該segmentの
既存ledger件数+1が `call_ordinal` と一致して1〜2以内、checkpoint CURRENTの場合だけ成功する。予約前にstage projectionと
実送信予定provider request bytesを完成させ、その2 hashも固定する。chat Pは `accepted_plan_sha256` とP projection bindingが
確定済みでなければreserveできない。

ownerはSTARTED auditをdurable writeし、ackのsequence/hash、projection/request hashを含む全reservation fieldが一致した場合だけ、state lock下で
`AttemptLedgerEntryV2(status=STARTED)`をappendしてreservationを消す。その直後のcheckpointを通ってからbackend dispatchする。
audit失敗はleaseをINVALIDATEDにし、同じrequest IDを再利用しない。response後はterminal audit ackを得て同じentryをterminal
statusへCAS更新するまで、次attempt、次stage、successor segmentへの移動を禁止する。broker segmentが変わっても
`(stage,attempt)`とrequest IDの一意性は判断全体のstate ledgerで検査する。

T ACCEPTEDのterminal更新と `accepted_plan_sha256` 記録は同じstate lock transactionで行う。Pの
`StageProjectionBindingV2` はplan hash、canonical input、messagesを1回だけ登録し、retryでbyte-equalを要求する。

cleanup uncertaintyを閉じる証拠は次のclosed型だけである。

```text
RecoveryProofV2 = {
  schema_version: "aiwolf.state-lease-recovery-proof.v2",
  mode: "ALL_SEGMENT_TERMINAL_ACKS" | "OWNED_BROKER_SHUTDOWN_CLEAN",
  owner_invocation_id: exact StateGenerationLeaseV2.owner_invocation_id,
  broker_lease_ids: exact StateGenerationLeaseV2.broker_lease_ids,
  terminal_ack_record_sha256s: tuple[sha256, ...],
  broker_config_fingerprint: sha256,
  broker_shutdown_clean: bool,
  delivery_terminal_record_sha256: sha256 | null,
  recovery_audit_sequence: int,
  recovery_audit_record_sha256: sha256
}
```

`ALL_SEGMENT_TERMINAL_ACKS` は全segmentについて既存 `release` / `cancel` / `cancel_successor` / `ABANDON` controlの
terminal replyを受け、そのsegment ID、operation、terminal codeをdurable auditしたrecord hashをacquisition orderでexactly
1件ずつ持ち、`broker_shutdown_clean=false` とする。`OWNED_BROKER_SHUTDOWN_CLEAN` はbrokerを所有する同じin-memory processが既存
`AdmissionBroker.aclose()` 完了後に `shutdown_clean=True` を直接観測してdurable auditした場合だけ許す。このmodeでは
`terminal_ack_record_sha256s=()`、`broker_shutdown_clean=true` とする。client `BrokerAdmissionSession.aclose()` の成功、transport close、PID、timeout、
別broker instanceのsnapshotはcleanup証明にしない。COMMITTEDからの回復だけは既存delivery finalizationのterminal audit hashを
必須とし、INVALIDATEDではnullとする。

state storeの `recover_state_lease(expected_lease_id, expected_lease_revision, proof)` だけが、state lock下でproofのowner、全segment、
config fingerprint、delivery条件を照合して `RECOVERY_PENDING` から `RELEASED` へCASできる。呼出者名や時間経過で代替しない。
証明を得られないprocess分離構成では `RECOVERY_PENDING` を維持する。

### 5.3 Lifecycle

1. **admission offer**: 現行 `AdmissionRequest` を作りOFFEREDを得る。未claim leaseでprovider callは0。
2. **prepare lease**: projection前にstate lockを取り、active leaseなし、CURRENT base/fact/world/phase/generation、owner、
   initial broker ID、expiryを検査する。`capture_id=null`、projection/hash field未確定の `PREPARING` placeholderを1件置く。
3. **projection/finalize capture**: lockを解放してcatalog/schema/input/captureを構築する。再びstate lockを取り、PREPARING時の
   全fieldと新しいfreshness witnessが同じ場合だけ、capture/hashを一括設定して`RESERVED`へCASする。途中値を公開しない。
4. **broker claim**: broker leaseをclaimする。GRANTED以外、cancel、expiryならstate leaseを`INVALIDATED`→`RELEASED`にし、
   captureを使用しない。
5. **activate**: broker activateとstate lease `ACTIVE` を同じownerで対応させる。backend callはこの区間だけ。
6. **stage/commit**: 全段成功後にcheckpointを通して `STAGED`、transaction commit後に`COMMITTED`。stale/cancel/invalidは
   commit前ならstate delta 0。
7. **dispatch/finalize**: dispatch直前checkpoint後、既存network action admissionへ渡す。delivery terminal audit後にrelease。
8. **release/recovery**: broker segmentを全てrelease/cancelしterminal replyをdurable記録してからstate leaseを
   `RELEASED`にする。cleanup結果不明は `RECOVERY_PENDING`へ移して次captureを禁止し、§5.2のproofを得た場合だけ
   state-store recovery CASへ渡す。

allowed transitionは次だけである。

| from | to | 条件 |
|---|---|---|
| PREPARING | RESERVED | 全fieldの2回目CASとcapture/hash一括確定 |
| PREPARING | INVALIDATED | projection、freshness、cancel、expiryの失敗 |
| RESERVED | ACTIVE | broker GRANTED、owner/expiry/current一致 |
| RESERVED | INVALIDATED | claim失敗、cancel、stale、expiry |
| ACTIVE | STAGED | 全attempt terminal、全段成功、stage前checkpoint PASS |
| ACTIVE | INVALIDATED | commit前のinvalid/stale/cancel/transport/audit失敗 |
| STAGED | COMMITTED | commit直前checkpointとbase revision CAS PASS |
| STAGED | INVALIDATED | commit前cancel/stale/revision conflict |
| COMMITTED | RELEASED | delivery terminal auditと全broker cleanup ack完了 |
| INVALIDATED | RELEASED | provider/dispatch非active、全broker cancel/release ack完了 |
| COMMITTED / INVALIDATED | RECOVERY_PENDING | broker cleanupまたはdelivery finalizationが不明 |
| RECOVERY_PENDING | RELEASED | §5.2 `RecoveryProofV2`をstate-store recovery CASが検証し、commit済みならdelivery terminalも完了 |

上表以外のstatus遷移、逆遷移、statusだけを書き戻すno-op遷移を拒否する。ledger append/terminal update、segment登録、
plan/projection登録などstatusを維持するfield mutationは、各専用APIが `expected_lease_revision` と当該fieldのexact旧値を検査し、
成功時に `lease_revision` を1増やすCASとしてだけ許す。PREPARINGを含むRELEASED以外のleaseが1件でもあれば新captureを
禁止する。`RECOVERY_PENDING` は時間経過だけで解除せず、上表の観測証拠を要求する。commit済みstateをrollbackしない。

state lockやworld single-writerをprovider待ちの間保持しない。state leaseは判断ownershipであり、server action permissionや
broker実行権を代替しない。

## 6. Freshness witnessとcheckpoints

`FreshnessWitnessV2` は同期関数で、awaitを挟まず次を読む。

- CURRENT `WorldSnapshot` のversion、last sequence、phase、self。
- `CurrentActionsView` のworld version/sequence、network sequence、全received actionとgeneration。
- `TransportObservationView.current_deadline` のmapping order、phase/day、connection/action generation、deadline。
- `DiscussionStateStore` のbase/fact revision、current capture、state lease owner/status。
- bound context hash、option/catalog/recipient proof/profile bundle/stage schema hash。
- stage開始後はaccepted plan、登録済みstage projection、attempt ledger/reservation、`lease_revision`。

最初と最後にworld versionを読み、同じでなければwitnessを作らない。3 viewのworld version/sequence、trigger、deadline、
action source、capture/lease全fieldが一致したときだけCURRENTである。event loop上の同期readから次のlocal transitionまでawaitを
入れない。backend待機中は既存world update monitorでcancelし、返却後のcheckpointを通らないoutputはstageしない。

checkpoint順は次で固定する。

1. `PREPARING` reserve CASの直前と直後（projection前）。
2. capture finalize `PREPARING→RESERVED` CASの直前と直後。
3. attempt STARTED auditの直前、durable ack直後、backend dispatch直前。
4. T accepted terminal audit ack後、P projection登録前と直後。
5. 各P retry予約前。
6. state stage直前。
7. commit直前。
8. network dispatch mark直前とactual send直前。

world/sequence/fact/phase/action/optionsの不一致は`STALE`、connection、deadline、lease owner/status、channel authority、
recipient proofの不一致は`LEASE_INVALID`とする。deadline到達は`LEASE_INVALID`である。hashが同じでもrevision/generationを
省略しない。authority event追加はworld/sequence/factのいずれかを進め、古いcaptureを再利用しない。

STARTED audit ack待ち中に変化した場合、dispatch前checkpointで停止し、そのattemptをSTALE/LEASE_INVALID terminalへ閉じる。
timeout/transport uncertaintyを同じattemptで再送しない。successor broker segmentも新しいattempt番号と既存call上限を守る。

## 7. Failure codesとfail-closed境界

| code | 条件 | side effect |
|---|---|---|
| `UNSUPPORTED_PRIVATE_CHANNEL_RECIPIENTS` | eligible chat channelがprivate | backend 0、option削除0 |
| `UNSUPPORTED_MULTIPLE_CHAT_OPTIONS` | eligible ChatActionが0件または2件以上 | backend 0、host選択0 |
| `UNSUPPORTED_DISCLOSURE_PROVENANCE` | 必須self resultにauthenticated sidecarがない | backend 0。候補を捏造しない |
| `CHANNEL_AUTHORITY_INVALID` | context/action/source/channel/hash不一致 | backend 0 |
| `CATALOG_INCOMPLETE` | trigger/PF2必須候補がtruncate後に欠ける | backend 0 |
| `STALE` | world/fact/phase/action/option変化 | state delta 0（commit前） |
| `LEASE_INVALID` | owner/connection/deadline/recipient/authority/lease不一致 | 新call 0、output不採用 |
| `AUDIT_FAILED` | STARTED/terminal durable ack不成立 | 新call 0、state lease invalidated |

private channel、別player能力結果、role/team/仲間ID、任意private memoryへ対応するfallbackは置かない。unsupportedを
publicへ変換、silence、NONE、別actionへ変換しない。親設計§7のschema exhaustion時defaultは、captureとauthorityが成立し
backendが合法に消尽した後だけ適用し、authority入力不備には適用しない。

## 8. Positive / negative test contract

全testはoffline fake world/network/broker/auditで行い、provider/LLM/game実行0とする。

### Positive

1. **C-P1 public chat**: bind済みcontext、CURRENT self snapshot、認証済み単一public `ChatAction`から
   `PublicChannelAuthorityV2`、recipient proof、captureを作り、同一入力で全hashが一致する。
2. **C-P2 live disclosure**: self宛てlive `INSPECT_RESULT` envelopeとrecordが一致し、PUBLIC chat authority下で
   受信直前phase witness、record identity、typed hashが1対1のsidecarを経て `d000/SELF_ABILITY_REPORT`を1件だけ作る。
   read visibilityはAUTHORIZED_PRIVATEのまま。
3. **C-P3 sync disclosure**: 認証済みstate-sync event ID、history index、entry hash、replayで採用した直前
   `PHASE_STARTED` indexから同じproofを作る。
4. **C-P4 multi option action**: PRE_VOTE/ABILITY/COの全received optionをreceived orderで束縛し、モデルが返した
   option IDだけを既存ActionHandleへ逆引きする。hostは先に選ばない。
5. **C-P5 lease two calls**: 2 call以内の判断は1 broker segment、1 state lease、同一captureで完結する。
6. **C-P6 lease four calls**: T/P合計4 callは既存上限2のbroker segmentを直列2件使い、state lease/captureは1件、
   attempt seed、request ID、segment内call ordinal、auditは4件全て一意になる。
7. **C-P7 T/P binding**: T terminal ackとplan hashを同一CASで記録し、P projectionのschema、base input、plan、messagesを
   1回だけ登録する。P retryはprojection hashをbyte-equalで再利用する。
8. **C-P8 lifecycle**: projection前PREPARINGから2回目CASでRESERVEDへ進み、全checkpointで同じfreshness witnessを受け、
   stage→commit→dispatch→terminal broker/delivery ack→releaseとなる。

### Negative

1. **C-N1 private**: private ChatActionだけ、またはpublic/private混在でcapture拒否。publicだけを残さない。
2. **C-N2 multiple chat**: public ChatAction 2件でcapture拒否。received order先頭をhost選択しない。
3. **C-N3 forged channel**: channel名、本文、roleから作ったauthority、contextにないchannel、action primary/parent source欠測、
   parent day/phase/hash/generation不一致を拒否。
4. **C-N4 disclosure origin**: sidecar欠測/重複、typed identity/hash不一致、live phase witness欠測/hash 1-bit変異、
   sync preceding phase source欠測/path/hash 1-bit変異、
   別player/connection/game、event ID/index/hash不一致、unknown event type、PUBLIC eventをABILITY_RESULTに偽装した入力を拒否。
5. **C-N5 mutation**: catalog pointer、option target、context、recipient proof、schema/configの1 bit変更で対応hashとcapture IDが
   不一致になり、backend 0。
6. **C-N6 reconnect/action refresh**: connection/action generationだけを進めても旧captureはLEASE_INVALID/STALE。
7. **C-N7 authority arrival**: backend中に新しいauthority eventでworld/sequence/fact revisionが進み、返却outputをstageしない。
8. **C-N8 deadline**: current deadline消失、mapping order変更、guard後expiry到達をLEASE_INVALIDにし、11.9秒は使わない。
9. **C-N9 lease ownership**: 別owner、未claim、release済み、3個目broker segment、同時active segmentを拒否。
10. **C-N10 attempt ledger**: STARTED ack待ち中のworld変化、予約とauditのprojection/request hash差、segmentを跨ぐ
    stage/attempt/request ID/seed再利用、call ordinal 3、terminal ack前の次attemptを拒否しprovider dispatch 0。
11. **C-N11 T/P drift**: Pでcapture、plan、recipient、catalog、stage schema、canonical input、messages、projection hashの
    いずれかが変わればP call 0。
12. **C-N12 prepare race**: PREPARING後projection中にfreshnessが変わればRESERVEDへ進めず、backend 0。別captureは
    INVALIDATED後の全broker terminal ackとRELEASEDまで拒否する。
13. **C-N13 cleanup uncertainty**: broker release/cancel結果不明、client session closeだけ、timeout/PID一致だけでは
    `RECOVERY_PENDING`を解除せず次captureを禁止する。exact segment terminal ack群または所有brokerの
    `shutdown_clean=True`証拠と、commit済みならdelivery terminal証拠が揃った場合だけrecovery CASを許す。

## 9. 実装・review境界

承認後の実装はclient内provenance sidecar、closed binding型/hash、state lease、controller/brain checkpointとoffline testに限る。
server、protocol schema、ゲーム規則、private permission、provider設定は変更しない。既存v1経路は既定のまま維持する。

独立Reviewerは少なくとも次を確認する。

- public symbolic recipient proofがprivate recipient集合の証明として再利用されない。
- authenticated envelope/state-sync path以外からSELF_ABILITY_REPORTを作れない。
- privateまたは複数chat optionを静かに削除・host選択しない。
- capture/hash DAGに循環がなく、T/Pとlease segmentを跨いで同一captureが維持される。
- broker 2 call上限、provider0 hold、deadline/freshness/audit fail-closedが保たれる。

本設計の独立承認前にcapture/state/brain接続を実装してはならない。
