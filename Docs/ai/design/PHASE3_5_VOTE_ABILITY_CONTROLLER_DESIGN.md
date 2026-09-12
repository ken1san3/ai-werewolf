Status: APPROVED — T002 Architect R1 independently approved on 2026-09-11

# Phase 3.5 Vote・Ability Controller — Detailed Design

## Purpose

`WorldState` が公開する current `VoteAction` / `AbilityAction` handle だけから、投票、
決選投票、Night0、Night の予約行動を決定論的に選び、authoritative server deadline 前に
送信する。server の列挙と検証をクライアントへ複製せず、予約の受理、拒否、配送結果不明を
区別して観測可能にする。

Phase 3.4 Reaction Chat と同じ process で一つの `Brain` / `BrainController` を共有し、
同時 invocation を起こさない。Phase 3.5 は LLM-free な決定論的選択を完成させるが、
疑い・推論・自然言語品質は扱わない。

## Authority and Existing Constraints

優先順位は canonical specification、実装・protocol/schema facts、tests、本文の順である
（D051）。本文は次を具体化する。

- `Docs/ai/ROADMAP.md` §3.5: 受信列挙だけから vote / runoff / abstain / ability を送り、
  deadline、拒否、seed 再現性、9-process completion を検証する。
- `Docs/ai/spec/DESIGN.md` §4.2: ability の対象数、対象候補、使用残数は同じ content 宣言から
  server が検証・列挙する。未選択時の `random` / `skip` は server の解決規則である。
- `Docs/ai/spec/DESIGN.md` §5: 未選択 vote は棄権と同じであり、明示的な棄権は
  `target_player_id: null`。棄権可否と残回数は server が列挙へ反映する。
- `Docs/ai/spec/DESIGN.md` §6.3: vote / ability は締切まで上書き可能な予約で、最後の受理内容が
  有効。受理時に本人へ accepted / rejected を返し、解決・使用回数消費は締切時に行う。
- `Docs/ai/spec/DESIGN.md` §§9.3–9.4: server が締切を強制し、client clock を信用しない。
  `player.action_state` と typed handle が client の選択可能範囲である。
- Approved Phase 3.1–3.4: `WorldState` は `NetworkClient.events()` の唯一の consumer、
  `BrainController` は coherent capture・typed dispatch の唯一の主体、local deadline は
  `CurrentPhaseDeadline` / `DispatchDeadline` で扱い、`BrainController` は active invocation
  最大一個・内部 queue 無しである。

現在の実装には、accepted vote / ability request に direct server reply が無い。
`SendReceipt` は WebSocket sender が wire write を終えた事実だけで server acceptance ではない。
一方、canonical §6.3 は accepted / rejected の本人通知を要求する。この差は Phase 3.5 の
protocol/session/client observation 範囲で閉じる。また現行 vote core API は session が採取した
server receipt time を捨てるため、deadline 直後かつ tick 前の vote を受理できる。Phase 3.5 は
ability と同じ authoritative receipt-window contract を vote reservation 境界へ追加するが、投票の
候補、上書き、集計、公開、解決という game rule は変更しない。

## Scope

- vote / runoff 一回につき一つの vote reservation を選び送る。
- Night0 / Night 一回につき、列挙された ability 群から一つの final reservation を選び送る。
- seed、player、day、phase、action family、受信候補から安定した選択を作る。
- action generation、connection generation、deadline mapping replacement、disconnect / resume を
  明示的な opportunity / attempt lifecycle で扱う。
- server response と client request を `request_event_id` で相関し、accepted / rejected / unknown
  を区別する。
- Phase 3.4 と一つの Brain invocation arbiter を共有する。
- unit、integration、regression、LLM-free 9-process completion の証拠を定義する。

## Files / Modules

### New production modules

| Path | Responsibility |
|---|---|
| `ai_client/vote_ability/types.py` | immutable config、identity、lifecycle、attempt/outcome、snapshot |
| `ai_client/vote_ability/selection.py` | seeded deterministic vote / ability policy Brain decorator |
| `ai_client/vote_ability/controller.py` | opportunity observation、deadline、dispatch、server-result finalization、bounded retry |
| `ai_client/vote_ability/__init__.py` | Phase 3.5 public export |
| `ai_client/brain/invocation.py` | 二つの feature controller が共有する bounded priority arbiter |

### Changed production boundaries

| Path | Required change |
|---|---|
| `protocol/aiwolf-v1.1.schema.json` | protocol 1.1 の完全 schema。typed `action.accepted` と strict correlation-aware rejection を宣言 |
| `server/network/protocol.py`, `server/network/session.py` | active schema/version を 1.1 にし、exact-version handshake、vote receipt time、accepted/rejected correlation を実装 |
| `server/aiwolf_core/interactions.py` | `InteractionAcceptance` の説明を通信専用から player action 共通の immutable receipt evidence へ一般化 |
| `server/aiwolf_core/voting.py`, `game.py`, `__init__.py` | vote の no-time overload を廃止し、receipt-window validation と immutable acceptance return を公開 |
| `ai_client/network/types.py` | `ActionAccepted` と correlation-aware `ActionRejected` notice |
| `ai_client/network/client.py`, `credentials.py` | accepted/rejected notice publication、1.1 checkpoint binding。自動 retry は追加しない |
| `ai_client/network/protocol.py`, `ai_client/network/__init__.py` | 新しい typed server event / public value の検証・export |
| `ai_client/world/model.py`, `transport.py`, `service.py`, `__init__.py` | accepted observation を既存 bounded transport journal と query API に追加 |
| `ai_client/brain/controller.py`, `model.py`, `__init__.py` | arbiter が sent decision を安全に受け取る public one-shot result 境界。単一 active / no queue は維持 |
| `ai_client/reaction_chat/controller.py`, `types.py`, `__init__.py` | BrainController の直接 ownership を arbiter 利用へ変更し、public completion/failure result を追加。Phase 3.4 policy は変更しない |

`protocol/aiwolf-v1.schema.json` は既存 1.0 の historical schema として一切書き換えず残す。
active schema を新しい 1.1 file へ切り替える。envelope の field 集合は変えないが、新 event type と
既存 payload の strict field 追加を古い 1.0 の意味に偽装しないため minor version を上げる。
`content/`、role/effect logic、投票の候補・上書き・集計・解決規則は変更しない。

### Tests and completion evidence

| Path | Responsibility |
|---|---|
| `tests/test_phase3_5_vote_ability_controller.py` | selection、identity、lifecycle、deadline、result、retry、arbiter の focused tests |
| `tests/test_phase3_5_completion.py` | 9-process vote/ability/reaction cumulative completion |
| `tests/fixtures/phase3_5_client_process.py` | Network / World / shared arbiter / both controllers の実process composition |
| `tests/fixtures/phase3_5_brain.py` | deterministic action policy と既存 speaking/silent behavior を合成する LLM-free Brain |
| `tests/fixtures/phase3_5_evidence.py` | server-side accepted reservation evidence と diagnostics |
| `tests/conftest.py` | completion node marker 登録 |
| Existing Phase 3.1–3.4 network/world/brain/reaction tests | public-boundary regressions |

## Responsibilities

### `NetworkClient`

- 受信した `action.accepted` / `action.rejected` を schema 検証後、typed notice として publish する。
- outgoing request の `event_id` を `SendReceipt.event_id` として返す既存契約を維持する。
- `SendReceipt` を acceptance に昇格させない。retry、選択、deadline policy を持たない。

### Server network/session boundary

- WebSocket receive handler と ticker は既存の一つの `_dispatch_lock` を共有する。handler は lock を
  取得した後に `SessionManager` で `received_at = timestamp(server_clock())` を一回だけ採取し、その値を
  session→`GameState`→resolverへ渡す。Network layer 自身は deadline を判定しない。
- valid authenticated `vote.cast` / `ability.use` を game core が例外なく受理した後だけ、同じ席へ
  `action.accepted` を一件返す。
- rejected response と accepted response に元 request の `event_id` を載せる。schema validation 前で
  valid UUID を安全に取得できない rejection だけ correlation ID を `null` とする。
- response は既存 player-local `seq` と replay retention を通し、切断後の resume でも保持範囲内なら
  元 envelope を再送する。
- action legality、期限、候補、予約、上書き、解決は game core が決める。network は結果を変換するだけ。

### Authoritative vote reservation boundary

no-time overload を残さず、次の三境界を同じ引数・return contract にする。

```text
SessionGame.submit_vote(
  now: int, voter_player_id: str, target_player_id: str | None
) -> InteractionAcceptance

GameState.submit_vote(
  now: int, voter_player_id: str, target_player_id: str | None
) -> InteractionAcceptance

VoteResolver.submit(
  now: int, voter_player_id: str, target_player_id: str | None
) -> InteractionAcceptance
```

`VoteResolver.submit` の validation / mutation 順序は固定する。

1. current phase が `VOTE` / `RUNOFF` であること、actor が living であること、target/null、
   abstention、self-vote、runoff candidate を現行規則どおり検証する。
2. `received_at = timestamp(now)` を検証し、authoritative `phase_started_at` / `phase_ends_at` が無ければ
   internal invariant failure とする。
3. `received_at < phase_started_at` は `action_unavailable`、`received_at >= phase_ends_at` は
   `action_deadline_passed`。したがって equality は常に rejection である。
4. 全check後だけ `pending_votes`、SERVER event、live reveal を一回変更する。
5. 成功時だけ frozen `InteractionAcceptance(action="vote.cast", player_id, day, phase,
   accepted_at=received_at, phase_deadline=phase_ends_at)` を返す。拒否は全 mutation zero で return 無し。

session はこの immutable return を受け取った後だけ local ack と completion spy evidence を作る。
spy の target と request ID は検証済み request から、day/phase/accepted_at/deadline は acceptance から得る。
request handler が lock を先に得れば上記 check と mutation が完了してから tick、ticker が先なら phase が
進んだ後に request が `action_unavailable` となる。同じ lock 内で時刻採取から core return まで await を
入れず、request/tick のどちらの順序でも deadline 後の予約は受理しない。

### `WorldState`

- Network notice の唯一の consumer であり続ける。
- accepted / rejected / timing / deadline を同じ bounded transport journal に commit する。
- current semantic state、current actions、current deadline、transport cursor/gap を immutable view で返す。
- controller policy や result 推測を持たない。

### `BrainController`

- coherent `BrainInput` capture、Brain output validation、current handle/deadline 再検証、typed send を
  一回分だけ行う既存責務を維持する。
- active invocation 最大一個、内部 queue 無しを維持する。
- successful dispatch の `BrainDecision` を receipt と組にした immutable one-shot result として
  arbiter に返す。controller が private method に依存しない公開境界にする。
- action family の選択方針、controller priority、retry は持たない。

### `BrainInvocationArbiter`

- 一つの `BrainController` を唯一所有し、Reaction Chat と Vote/Ability からの invocation を直列化する。
- pending は owner ごと最大一件、全体最大二件とし、unbounded queue を作らない。
- active invocation は preempt しない。次の grant 時に reservation action を reaction より優先する。
- grant 後に `capture_input(allowed_handles=...)` を行う。lock 待機前に作った `BrainInput` は使わない。
- cutoff までに grant できなければ Brain を呼ばず deadline-suppressed result を返す。
- `stop()` だけが共有 BrainController を永久停止する。

### `ReactionChatController`

- trigger、chat/CO cap、jitter、semantic finalization という Phase 3.4 policy を維持する。
- `BrainController` を直接保持・停止せず、arbiter の reaction priority endpoint を使う。
- controller 自身の `stop()` は own task/pending wait だけを cancel/join する。

### `VoteAbilityController`

- World public API だけから current opportunity を観測する。Network events を直接読まない。
- vote / runoff と ability を別 reservation family として扱い、各 logical reservation の attempt ledger、
  deadline、result finalization、bounded retry を所有する。
- relevant received handles だけを arbiter へ渡す。`BrainInput` や handle を作らない。
- role ID、team、ability の効果、server rule、`no_selection` policy を解釈しない。
- accepted / rejected / unknown を request correlation により確定し、bounded snapshot に残す。

### Deterministic vote/ability Brain decorator

- filtered input が vote または ability だけなら本文の pure selection ruleで `VoteDecision` /
  `AbilityDecision` を返す。
- chat / CO input は constructor で与えられた一つの delegate `Brain` へ渡す。
- role名やserver stateを推測せず、受信 handle の field 以外から legality を再構築しない。
- 一つの composed Brain instance が共有 BrainController に入り、model/routerを新設しない。

### Process composition root

- 一つずつの Network、World、composed Brain、BrainController、arbiter、ReactionChatController、
  VoteAbilityController を constructor injection で接続する。
- `PhaseBrainCoordinator` は Phase 3.4 と同様に同時起動しない。
- Network / World を先に開始し、両 feature controller を開始する。終了時は feature controllers、
  arbiter/BrainController、World、Network の順に stop/cancel/join する。
- 一方の controller の予期しない failure を supervisor へ返し、もう一方を含む全所有 task を有限時間で
  回収する。黙って再起動しない。

## Public Interfaces

以下は型レベルの契約であり、関数内部の実装指定ではない。

```text
ActionAccepted
  action: str
  request_event_id: str
  seq: int
  observation_connection_generation: int
  observed_at_monotonic: float

ActionRejected                    # existing value, extended compatibly
  action: str
  reason: str
  seq: int
  observation_connection_generation: int
  observed_at_monotonic: float
  request_event_id: str | None = None

ActionAcceptedObservation
  order: int
  world_version: int
  action: str
  request_event_id: str
  seq: int
  observation_connection_generation: int
  observed_at_monotonic: float

ActionRejectionObservation        # existing value, same optional field
  ...
  request_event_id: str | None = None
```

Wire payloads:

```json
{ "type": "action.accepted",
  "payload": {
    "action": "vote.cast",
    "request_event_id": "the-client-request-uuid"
  } }
```

```json
{ "type": "action.rejected",
  "payload": {
    "action": "ability.use",
    "reason": "invalid_target",
    "request_event_id": "the-client-request-uuid-or-null"
  } }
```

`action.accepted.action` はこの task では `vote.cast` または `ability.use`。Python の rejection value は
既存 producer/test との source compatibility のため `request_event_id: str | None = None` とするが、
1.1 wire payload ではkey自体を必須かつnullableにする。Phase 3.5 のtyped valid requestは必ずnon-nullで
返す。unknown payload keys は各 strict type schema で拒否する。

`observation_connection_generation` は Network が event を観測・commit した local generation であり、
server が echo する値でも request origin でもない。送信 attempt は `SendReceipt` の
`connection_generation` を immutable `send_connection_generation` として別に保持する。両者を同一視せず、
相関の primary identity は全接続を通じて一意な client-generated `request_event_id` と action の組である。

### Protocol 1.1 and compatibility boundary

- 新規 `protocol/aiwolf-v1.1.schema.json` の `$id` は `urn:aiwolf:protocol:1.1`、title/version exampleも
  1.1 とする。client request / server event の全 envelope `protocol_version` は文字列 `"1.1"`。
- 現在の `protocol/aiwolf-v1.schema.json`（`$id ...:1.0`）は historical 1.0 schema として immutable に
  保ち、recorded 1.0 envelope の検証に使う。schema file を内容から推測せず、recorded envelope の exact
  version `1.0`→legacy file、`1.1`→new file と選択する。未知版を fallback schema で読まない。
- server/client の active `SCHEMA_PATH` と `PROTOCOL_VERSION` は 1.1 schema 由来の `"1.1"` に揃える。
  schema と手書きconstantが食い違えば起動時に失敗する。
- `action.accepted` payload は `action` と non-null UUID `request_event_id` の二keyのみを必須とし
  `additionalProperties: false`。1.1 `action.rejected` payload は `action`、`reason`、
  `request_event_id`（UUID または null）を必須とし `additionalProperties: false`。valid typed
  Phase 3.5 request の rejection ではnon-null、envelope/schema以前のunaddressable requestだけnullを許す。
- D031 の same-major rule は major compatibility の必要条件であって、新しい strict eventを理解する
  capability negotiationではない。1.1 server は join/resume handshake で exact `1.1` だけを認証し、
  既存1.0 peerはseat state/eventを送る前に `unsupported_protocol_version` として接続拒否する。
  1.0 peerへ1.1 eventを送らず、same-majorで誤って認証しない。
- `SessionCheckpoint` に `protocol_version: str` を必須追加し、credential file にatomic保存する。
  旧二field fileはload時に明示的なlegacy `1.0` として識別するが、1.1 clientはsocket接続前に
  `INCOMPATIBLE_PROTOCOL_CHECKPOINT` で停止し、1.1 resumeへ黙って転用・削除しない。
- active 1.0 game/session/replay history のin-place upgradeはしない。1.0 server/client pairで完走させて
  drainし、Phase 3.5 / 1.1 はfresh game、fresh entry token、fresh checkpointから開始する。1.1 sessionの
  retained replayはoriginal 1.1 envelopeをそのまま返し、1.0 eventをrelabel/rewriteしない。
- recorded 1.0 envelopes/logsはlegacy schemaで引き続き読める。runtimeが対応しない1.0 gameplayを
  「読める」と「resumeできる」は分ける。このtaskはmulti-version live serverを新設しない。

```text
DeterministicVoteAbilityBrain(
  *, master_seed: int, delegate: Brain
)

async DeterministicVoteAbilityBrain.decide(
  request: BrainInput
) -> BrainDecision

BrainInvocationPriority
  RESERVATION_ACTION
  REACTION

BrainDispatchResult
  outcome: DecisionOutcome
  dispatched_decision: BrainDecision | None

BrainInvocationArbiter(
  *, controller: BrainController, clock: Clock = time.monotonic
)

async BrainInvocationArbiter.invoke(
  *,
  owner: Literal["vote_ability", "reaction_chat"],
  priority: BrainInvocationPriority,
  allowed_handles: tuple[ActionHandle, ...],
  timeout_seconds: float,
  dispatch_deadline: DispatchDeadline,
) -> BrainDispatchResult

async BrainInvocationArbiter.stop() -> None

BrainController.take_dispatched_decision(
  receipt: SendReceipt
) -> BrainDecision | None
```

`DeterministicVoteAbilityBrain` は正規化済み一個の `master_seed` と一個の `delegate` をconstructorで
immutableに保持する。filtered optionsがすべてvote/abilityのときだけpure selectorを実行し、それ以外は
同じrequestを唯一のdelegateの`await decide(request)`へ一度だけ渡す。delegateの複製・切替・routingは
行わない。mixed reservation/reaction optionsはarbiter/capture invariant違反としてdecisionを返さない。

同一 owner の二重 pending、空/重複/型不正 handle、既に停止した arbiter の呼出しは construction /
call boundary で明示的に拒否する。`dispatched_decision` は `SENT` のときだけ存在し、receipt と一対一。
arbiterは`BrainController.decide_and_send(...)`の`SENT` result直後、他ownerをgrantする前に
`take_dispatched_decision(outcome.receipt)`を一度だけ呼ぶ。matching receiptはそのdecisionを返してconsume、
wrong/None/already-consumed receiptは`None`で、wrong receiptはmatching valueを破棄しない。`SENT`なのに
matching decisionが得られない場合はsilentに`None`へ変換せず invariant failureとしてcontrollerへ伝播する。
既存private `_take_dispatched_decision` は public alias として残さず、全callerをこのmethodへ移す。

```text
VoteAbilityConfig
  deadline_guard_seconds: float = 0.25
  brain_timeout_seconds: float = 0.25
  minimum_start_budget_seconds: float = 0.05
  max_pre_send_rearms_per_reservation: int = 2
  max_not_delivered_retries_per_reservation: int = 1
  outcome_retention: int = 256

VoteAbilityController(
  *,
  world: WorldState,
  invoker: BrainInvocationArbiter,
  config: VoteAbilityConfig = VoteAbilityConfig(),
  clock: Clock = time.monotonic,
)

VoteAbilityController.start() -> None
async VoteAbilityController.stop() -> None
async VoteAbilityController.wait() -> FeatureControllerExit
VoteAbilityController.snapshot() -> VoteAbilitySnapshot

ReactionChatController.start() -> None
async ReactionChatController.stop() -> None
async ReactionChatController.wait() -> FeatureControllerExit

FeatureControllerExitReason
  WORLD_ENDED
  WORLD_FAILED
  STOP_REQUESTED
  FAILED

FeatureControllerExit
  owner: Literal["vote_ability", "reaction_chat"]
  reason: FeatureControllerExitReason
  error_type: str | None
```

`clock` は Network / BrainController / arbiter / both feature controllers に同じ callable instance を
注入する。server timestamp と local monotonic を直接比較しない。

`start()`はown taskを一回だけ生成する。`wait()`はprivate taskを公開せず、start前は`RuntimeError`、
start後は同じfrozen terminal resultを複数回返す。waiter cancellationはown taskをcancelしない。
`stop()`はidempotentで、自身のwait/taskだけをcancel/joinして`STOP_REQUESTED`を確定し、共有arbiterは
止めない。World `ENDED` / `FAILED` は各reasonに写す。予期しない例外はlifecycle `FAILED`、reason
`FAILED`、non-empty `error_type`へ変換し、正常`STOPPED`に偽装しない。正常reasonの`error_type`はnull。

composition supervisorは両controllerをstart後、両方のpublic `wait()`とWorld/Network terminal waitを
`FIRST_COMPLETED`で監視する。controller `FAILED` / `WORLD_FAILED` を受けたらsibling controllerをstopし、
arbiter→World→Networkの順で有限join後、process failureとしてraiseする。`WORLD_ENDED`なら同じ順で
normal shutdown、外部shutdownで`STOP_REQUESTED`なら残所有物を回収する。private task/exceptionへ触れず、
controller failureをlogだけにして継続またはsilent restartしない。

```text
ReservationKey
  day: int
  phase: str
  family: Literal["vote", "ability"]

OpportunityKey
  reservation: ReservationKey
  connection_generation: int
  action_generation: int

VoteAbilityOutcome
  opportunity_key: OpportunityKey
  mapping_order: int | None
  action: Literal["vote.cast", "ability.use"]
  ability_id: str | None
  vote_target_player_id: str | None
  ability_target_player_ids: tuple[str, ...]
  request_event_id: str | None
  send_connection_generation: int | None
  observation_connection_generation: int | None
  status: VoteAbilityOutcomeStatus
  rejection_reason: str | None
  attempts: int
```

`VoteAbilityOutcomeStatus` は最低限、`NO_ELIGIBLE_SELECTION`、`NO_DECISION`、`INVALID_DECISION`、
`BRAIN_FAILED`、`TIMED_OUT`、`DEADLINE_SUPPRESSED`、`STALE`、`NOT_DELIVERED`、`ACCEPTED`、
`REJECTED`、`UNKNOWN`、`CANCELLED` を区別する。`SendReceipt` 後は terminal outcome を作らず、
matching server response まで snapshot の `unresolved` に置く。

`VoteAbilitySnapshot` は immutable で、lifecycle、current phase/opportunity、transport cursor、
pending count、unresolved reservation、accepted/rejected/unknown/deadline counters、bounded outcomes を
持つ。mutable handle、raw payload、Brain text は公開しない。

## Deterministic Selection

### Stable hash

選択は module-level `random`、Python `hash()`、時刻、process ID、arrival scheduling を使わない。
SHA-256 の domain `aiwolf.phase3.5.selection.v1` に、UTF-8 length-prefix した次を入れる。

```text
master_seed decimal
self player_id
ReservationKey.day
ReservationKey.phase
ReservationKey.family
candidate kind
candidate stable ID
```

各候補を digest bytes、次に stable ID の昇順で並べる。ability handle の stable ID は
`ability_id`、target の stable ID は `player_id`。同じ family 内の重複 ability ID は malformed
input として送信しない。connection generation、action generation、mapping order、deadline value は
validity identity であってselection seedに入れない。このため reconnect、sync refresh、extension /
shorteningで候補集合が同じなら同じ予約内容になる。

### Vote / runoff

1. relevant handles は current `VoteAction` のみ。0件なら opportunity は無い。2件以上、
   `target_count != 1`、候補重複/不正は `NO_ELIGIBLE_SELECTION` で閉じる。
2. `valid_targets` が非空なら SHA-256 ranking の先頭一人を選ぶ。`allows_abstain=True` でも、
   選べるtargetがある間はbaseline clientは棄権しない。
3. `valid_targets` が空で `allows_abstain=True` の場合だけ `VoteDecision(..., None)` を返す。
4. 空かつ棄権不可なら送信しない。

これがROADMAPで未決だった棄権条件である。推論を持たないbaselineが任意に棄権して
game progressを弱めることを避けつつ、serverが明示した棄権能力だけを使用する。
VOTEとRUNOFFはphase名が異なる別`ReservationKey`なので、生存席は各roundで一回送る。

### Night0 / Night ability

1. relevant handles は current `AbilityAction` のみ。`uses_remaining == 0`、
   `target_count < 1`、`len(valid_targets) < target_count` のhandleはeligibleでない。
2. `uses_remaining is None` または正数は「このreservationを一回送れる」ことだけを意味する。
   値の回数だけ複数送信しない。coreはactorごとにfinal reservation一件を保持するためである。
3. eligible abilityが複数ならability ID rankingの先頭一つを選ぶ。全handleを順次送って
   unintentionally last-write-winsにしない。
4. 選んだhandleの`valid_targets`をplayer ID rankingし、先頭`target_count`個を一回の
   `AbilityDecision`へ入れる。重複targetや列挙外targetは作らない。
5. eligibleが0なら送信しない。Night0も同じ処理で、serverがhandleを列挙しない役職・ruleを
   clientがrole名で推測しない。

flat `valid_targets` からserver restrictionの組合せを再構築しない。将来contentでflat projectionから
不正な組合せが生じた場合はserver rejectionを観測し、このtaskでrole-specific fallbackをしない。

## Opportunity Identity and Reservation Lifecycle

`ReservationKey` は同じserver-side final reservation slotを表す。`OpportunityKey` はそのslotを
操作できる一組のreceived handlesを表す。

- phase/day change: 新しいreservation。VOTE→RUNOFF、NIGHT0→DAY、NIGHT→DAYはいずれも別。
- connection generation change: 新しいopportunityだが同じday/phaseなら同じreservation ledger。
- action generation change: 新しいhandles/opportunity。旧handleは絶対に再利用しない。
- deadline mapping replacement: opportunity identityを変えない。pending dispatchだけ新mappingへ
  rebaseし、selection candidateが同じなら選択も変えない。
- current action familyが消えた: 未送信pendingをcancel。既にwire送信済みならresponse待ちledgerを
  保ち、phase/generation/result eventで確定する。

同じ `OpportunityKey` はBrain invocation最大一回、wire queue最大一回。同じ`ReservationKey`で
accepted/rejected/unknownになった後は、新しいmapping/action generationだけを理由に再送しない。
これはduplicate reservationと意図しない上書きを防ぐ。Phase 4で明示的な再考・置換を導入するなら
別Design Gateでreplacement triggerと上限を定義する。

各wire attemptは `(request_event_id, action, send_connection_generation, attempt_ordinal)` をimmutableに
保持する。`request_event_id` はqueue前にUUIDとして一回生成し、process内・再接続・retryを通じて再利用
しない。`send_connection_generation` は診断とNOT_DELIVERED retry条件にだけ使い、server response相関の
一致条件にはしない。NOT_DELIVERED retryは新generationでfresh request IDを作り、旧attemptとの親子関係を
同じreservation ledgerに残す。

## Data Flow

1. Network が action state / sync とlocal deadline mappingをpublishし、Worldが順にcommitする。
2. VoteAbilityControllerはCURRENT、complete、caught-up snapshot、current actions、current deadlineを読む。
3. day/phaseとhandleのconnection/action generationが一致し、deadline mappingも同じkeyなら
   `OpportunityKey`を作る。
4. controllerはvote familyかability familyのeligible handlesだけをarbiterへ登録する。
5. arbiterはactive call終了後、reservation priorityをgrantし、その時点でBrainControllerに
   filtered coherent inputをcaptureさせる。
6. deterministic Brain decoratorが受信optionsだけからdecisionを返す。BrainControllerが型、option、
   target count/membership、current handle、mapping、cutoffを再検証する。
7. typed Network sendがwire writeを終えると`SendReceipt`を返す。controllerはこれを`unresolved`へ置き、
   acceptedとは数えない。
8. server sessionがcore受理後に`action.accepted`、拒否時に`action.rejected`を同じrequest ID付きで返す。
9. Network→World transport journalへ入り、controllerがexact request ID/actionで相関する。通常応答の
   observation generationはsend generationと同じ、retained replayはそれより新しくてよい。
10. accepted/rejectedをterminal outcomeにし、次のlogical reservationまで送らない。disconnectや
    generation changeだけではUNKNOWNにせず、以下のresume recovery barrierまたはterminalを待つ。

### Resume correlation and recovery barrier

unresolved attemptは再接続開始時にもledgerへ残す。Network/Worldは次を順序付きtransport observationで
公開し、controllerはprivate Network stateを読まない。

```text
ResumeRecoveryBarrier
  connection_generation: int
  requested_last_seq: int
  replay_first_seq: int | None
  replay_last_seq: int | None
  replay_contiguous: bool
  replay_gap_or_floor: bool
  resumed_seq: int
  state_sync_seq: int
  world_version: int
  complete: bool
```

server送信順は retained `replay`（あれば）→`session.resumed`→authoritative `game.state_sync`、World commitも
この順を保つ。`complete` は同じ新generationのsyncを適用し、Worldがそのseqまでcaught-upになった後だけ
true。replay中の `(request_event_id, action)` 一致は observation generation N+1 でもorigin generation Nの
attemptを即ACCEPTED/REJECTEDにできる。wrong ID、wrong action、originより古いobservation generationは
unmatchedとして残し確定しない。

matching responseが無い場合は、(a) requested seqから`session.resumed`までのretained replayがcontiguousで
全範囲を覆い、そこにresponseが無かった、または (b) replay floor/gapにより範囲外となり、その後の
authoritative syncまで完了した、というbarrierで初めてUNKNOWNへ確定する。reconnect entry、generation
increment、`session.resumed`単体、途中のphase changeでは確定しない。resume不能なNetwork terminalや
World FAILED/ENDEDのように今後barrierを得られないterminalはUNKNOWN、明示stopはCANCELLEDへ有限確定
する。barrier後のnew-generation NOT_DELIVERED retryは既存上限どおりであり、
delivery-unknown/missing responseをretryしない。

## State / Lifecycle

Controller lifecycle:

```text
NEW -> RUNNING -> STOPPING -> STOPPED
          |             \-> FAILED
          +----------------> FAILED
```

- `start()` は一回だけown loopを生成する。
- World `ENDED` ではpending/unresolvedを有限に確定してSTOPPED、`FAILED`ではcontrollerもFAILED。
- `stop()` はpending World wait / arbiter waitをcancel/joinする。共有arbiter/Brainは止めない。
- construction前の`stop()`は副作用なくSTOPPEDにする。二回目以降はidempotent。

Reservation state:

```text
OBSERVED -> WAITING_FOR_GRANT -> DECIDING -> QUEUED_TO_NETWORK
       \          \               \             |
        \          +-> NO_SEND      +-> NO_SEND  +-> WAITING_SERVER_RESULT
         +------------------------------------------> ACCEPTED | REJECTED | UNKNOWN
```

`NO_SEND` は理由別outcome。server result待ちの間、同じreservationの新dispatchは作らない。
outcome dequeは256件。transport journal gapがunresolved requestをまたいでも即閉じず、resume recovery
barrierへ移す。gap/floor後のauthoritative syncでresponseが観測不能になって初めてUNKNOWNとする。

## Deadline and Mapping Rules

- `cutoff = current_deadline.local_deadline_monotonic - deadline_guard_seconds`。
- `cutoff - clock() < minimum_start_budget_seconds`ならBrain/arbiterへ登録せず抑止する。
- action controllerはphase観測後に人為的jitterを置かず、即座にreservation priorityへ登録する。
  複数clientのtarget分散はselection hashで行い、arrival timingをrandomnessにしない。
- arbiter待機後に残り時間を再計算し、Brain timeoutは
  `min(config.brain_timeout_seconds, cutoff - clock(), BrainRunConfig.max_decision_seconds)`。
- `DispatchDeadline`はcurrent mapping_order、day、phase、connection/action generation、cutoffを持つ。
- extension/shorteningでmappingが変われば旧waiting/decisionは送信直前に抑止する。wire未到達なら
  新mappingで同じreservationを最大`max_pre_send_rearms_per_reservation`回re-armできる。
- shorteningで新cutoffを過ぎていれば即`DEADLINE_SUPPRESSED`。extensionでもaccepted/rejected/unknownを
  再送しない。

client guardは努力目標でありserver authorityを置換しない。serverはreceipt time/phaseを最終判定する。

## Accepted / Rejected / Unknown Semantics

- `ACCEPTED`: `SendReceipt.event_id`と完全一致する`ActionAcceptedObservation.request_event_id`と同じ
  actionをWorldから観測した。observation generationはsend generation以上ならよく、retained replayの
  N→N+1を許す。serverがcore call成功後に生成する唯一の証拠。
- `REJECTED`: 同じrequest ID/actionの`ActionRejectionObservation`を観測した。observation generationは
  同じか後続を許す。reasonは記録するが
  production controllerはreason文字列でfallback/retry policyを分岐しない。
- `UNKNOWN`: senderがwire開始/完了した可能性があるが、accepted/rejectedを一意に観測できないまま
  completed recovery barrierでretained replay内に無いこと、またはgap/floor後のsyncで今後観測不能なことが
  証明された。resume不能terminalでも確定するが、generation/phase change単体では確定しない。
- `NOT_DELIVERED`: Networkがwire開始前を保証した。UNKNOWNとは分ける。

chat echo、vote reveal、ability result、phase transition、送信log、`SendReceipt`だけをaccepted証拠に
しない。これらはresolution/publicationであってrequest acceptance correlationではない。

## Retry and Replacement Boundaries

- matching `ACCEPTED`、`REJECTED`、`UNKNOWN`後の自動retryは0回。
- `SEND_DELIVERY_UNKNOWN`はUNKNOWNへ写し、再送しない。すでにserverが予約した可能性があるため。
- `NOT_DELIVERED`だけは副作用なしが保証されるため、新しいconnection generationとfresh sync/mappingを
  得た場合に限り一回retryできる。同じgenerationでbusy-loopしない。
- stale capture / stale mapping / arbiter cutoffはwire未到達なので、same reservationを新しいcurrent
  opportunity/mappingで最大2回re-armできる。上限到達かdeadlineで抑止をterminal記録する。
- accepted reservationをbaseline controllerが置換しない。serverのlast-write-wins能力は維持するが、
  Phase 3.5はその能力を無制限再判断に使わない。

## Coexistence and Concurrency

- process内のBrain instance、BrainController、arbiterは各一個。
- arbiterだけがBrainControllerをcall/stopする。二つのfeature controllerはarbiter clientである。
- active Brain call最大一件、ownerごとのpending最大一件、全pending最大二件。
- reservation actionは次grantでreactionより優先するが、active reactionをpreemptしない。
- actionはphase観測直後に登録し、既存Reaction Brain timeout上限0.25秒を待っても、completionの
  1秒vote/night phaseで `1.00 - 0.25 active - 0.25 decision - 0.25 guard - 0.05 start = 0.20`
  秒の保守余裕を残す。
- World readsは同じevent loopでawaitなしにcaptureし、immutable value/version/cursorを使う。
- lock/grant取得後、decision後、send直前にphase/handle/mappingを再検証する。
- controller cancellationは自身が作ったwait/taskだけをcancel/joinする。共有Brain shutdownはcomposition
  root→arbiterだけが行う。

## Main Control Flow

1. current viewがcoherentでなければWorld version更新を待つ。
2. current `VoteAction`または`AbilityAction`を抽出し、identityとledgerを照合する。
3. selection前validationでeligibleでなければ明示outcomeを記録し、そのopportunityを閉じる。
4. current mapping/cutoff budgetを検証し、reservation priorityでarbiterへ渡す。
5. grant後のcoherent captureからdeterministic Brainが一decisionを返す。
6. BrainControllerの既存validation/stale/deadline gate後、typed sendを一度だけ行う。
7. non-SENT statusをoutcomeへ写す。safe re-arm条件以外はそのreservationを閉じる。
8. SENTならrequest IDとdecisionをunresolvedへ保持し、World transport cursor以後を待つ。
9. exact accepted/rejectedを確定する。disconnect時はledgerを保持し、completed recovery barrierまたは
   resume不能terminalだけでmissing responseをUNKNOWNへ確定する。
10.次のday/phase reservationを待つ。RUNOFFは新しいvote reservationとして処理する。

## Failure Handling

- invalid config、clock、arbiter owner/priority、identityはconstructor/call時に例外。
- malformed handle集合はNetwork/schemaで通常拒否される。defensiveに届いた場合はsendせず
  `NO_ELIGIBLE_SELECTION` / `INVALID_DECISION`として観測する。
- Brain NoDecision、invalid、exception、timeoutを区別し、fallback targetを送らない。
- World history gapはselectionに不要。transport gapはunresolved response correlationを不完全にするが、
  UNKNOWN確定はrecovery barrier後まで延期する。
- accepted/rejected correlationのrequest ID/action不一致、またはsend generationより古いobservationは
  unmatched observationとしてjournalに残し、current requestを確定しない。
- disconnect中は新規dispatchしない。resume replay→`session.resumed`→authoritative syncのbarrier中は
  unresolvedを保持し、同じrequest responseが復元されれば観測generationが新しくても確定する。
  matching無しのUNKNOWNは本文のcompleted barrier/terminal条件だけで確定する。
- controller childの予期しない例外はFAILEDとしてsupervisorへ伝播し、silent restartしない。
- retention evictionはcurrent unresolved recordをcontroller ledgerから消さないが、対応journalの欠落を
  UNKNOWNとして明示する。

## Explicitly Out of Scope

- suspicion/inference、自然言語品質、role/ability意味に基づく戦略、Phase 4 LLM selection。
- target selector/restriction、uses、vote tally、no-selection ruleの複製・変更。game core変更は
  `vote.cast`を既存ability/communicationと同じstrict receipt windowへ揃えることだけ。
- chat/CO policy、Phase 3.4 cap/jitter/trigger semanticsの変更。
- accepted reservationの戦略的再考・複数replacement。
- server phase duration、content preset、role YAML、effect、protocol envelope/header field集合変更、
  multi-version live negotiation framework。
- UI、external service、multi-model routing、Autodev、scheduler/framework新設。

## Acceptance Criteria

1. controllerはWorld public APIだけを読み、Network event streamやserver packageをconsume/importしない。
2. Vote/Runoffごとにliving seatのcurrent VoteActionから一回だけ`vote.cast`を送る。
3. targetがあればseeded target、targetが無くallows_abstainならnull、両方無ければ非送信になる。
4. Night0/Nightでeligible AbilityAction群から一件だけ選び、exact target_countのunique listed targetsを送る。
5. uses_remaining=0、empty/insufficient candidates、duplicate ability identityを送信しない。
6. 同じseed/player/day/phase/候補集合はprocess/hash seed/arrival timingに関係なく同じdecisionになる。
7. 役職名、team、effect、rule literalで分岐しない。受信handle外のtarget/abilityを作らない。
8. mapping replacementだけでopportunity/selection/attemptが重複せず、shortening/extensionに追従する。
9. stale action/connection generationのhandleを送らず、disconnect中に新しいsendを開始しない。
10. `SendReceipt`はacceptedに数えず、request ID/action一致のserver responseだけでACCEPTED/REJECTEDを
    確定する。send generationは診断に保持し、後続generationのretained replayを相関できる。
11. reconnect開始だけでUNKNOWNにしない。retained replay→resumed→authoritative syncのcompleted barrierか
    resume不能terminalでmissing responseをUNKNOWNと確定し、delivery unknownとaccepted/rejectedを区別する。
12. accepted/rejected/unknown後は自動再送せず、NOT_DELIVERED retryとpre-send re-armは既定上限内。
13. accepted reservationをaction_state refreshやdeadline extensionだけで置換しない。
14. Reaction ChatとVote/Abilityは一つのBrain/BrainController/arbiterを共有し、active invocationは常に最大1。
15. reservation priority、bounded pending、no preemption、shutdown ownershipがrace testで決定的。
16. action.accepted/rejectedはplayer-local seq/replayを通り、origin Nの結果をN+1 observationから復元できる。
17. `vote.cast`はsessionで一回採取したserver receipt timeをno-time overload無しでcoreまで渡し、
    `phase_started_at <= accepted_at < phase_ends_at`だけをmutationする。deadline equality/afterは拒否され、
    request/tick lock順序に依存してlate reservationを作らない。
18. protocol 1.1 schema/constant/wireが一致し、1.0 peerはhandshakeでstate前に明示拒否、legacy 1.0
    schema/logは保持され、1.0 checkpointを1.1 resumeへ転用しない。
19. deterministic decorator、BrainController one-shot decision、両controller completion/failureが本文の
    exact public interfaceだけで合成でき、private task/methodを使わない。
20. content、投票候補/上書き/集計/解決規則、role logicは変更しない。
21. LLM-free 9-process standard_9 scenarioがReaction Chatを保ったままgame endまで完走する。
22. 全living seatの各VOTE/RUNOFF accepted予約と、各列挙ability seatの各night accepted予約を
    server-authoritative spyでexactに確認する。
23. focused tests、Phase 3.1–3.4 regressions、通常regression、docs/diff checksがpassする。

## Required Tests

### Selection and identity

- fixed vectorsでSHA-256 ranking、候補順入替、別seed/player/day/phaseの結果。
- vote target、empty+abstain、empty+no-abstain、target_count不正、duplicate handle。
- ability複数handle、multi-target、uses None/positive/zero、candidate不足、duplicate ability ID。
- Night0/Night/Vote/Runoffをrole名なしで処理し、mapping orderだけの変更でselectionが不変。
- connection/action generationはOpportunityKeyを更新するがReservationKey/selectionを変えない。

### Protocol / session / Network / World

- vote coreは`deadline - 1`でimmutable acceptanceと一mutation、equality/afterで
  `action_deadline_passed`かつzero mutation。phase前、deadline無し、invalid actor/targetのvalidation order。
- dispatch lockのrequest-firstはcore return/ack後にtick、tick-firstはphase advance後に
  `action_unavailable`。両raceでdeadline後のpending vote/live reveal/eventが無い。
- core成功後だけaction.accepted、拒否時はaccepted無し、request event ID exact echo。completion spyの
  target/request IDとacceptanceのday/phase/accepted_at/deadlineが同じrequestに属する。
- 1.1 accepted/rejected schema strictness、required nullable rejection ID、wrong UUID/missing/extra key rejection。
- 1.1 schema ID / wire / server/client constants、exact handshakeのnew-new success、1.0 client→1.1 serverと
  1.1 clientが1.0 handshake replyをcheckpoint/World commit前に拒否すること、1.0 recorded envelopeの
  legacy schema validation。
- legacy two-field checkpointを1.0としてloadし1.1 resume前に明示停止、1.1 checkpoint atomic round-trip、
  1.1 replay envelopeをversion rewriteせず再送。active 1.0 session drain policyのunit boundary。
- accepted/rejectedのplayer-local seq、retained replay N→N+1、replay-floor/gap→sync behavior。
- Network typed noticeのclock/observation generation/ID、World journal commit/version/query/retention/gap/
  completed recovery barrier。
- SendReceiptだけではcontroller accepted countが増えない。

### Controller and arbiter

- coherent capture、one attempt/opportunity、one final reservation/family、no duplicate on refresh/extension。
- deadline guard/start budget、mapping replace、shortening、extension、deadline equality。
- exact accepted/rejected correlation、N送信→N+1 replay確定、unmatched action/ID/older generation、
  reconnect entryでunresolved維持、contiguous replay missing response→resumed→sync UNKNOWN、
  replay-floor/gap→sync UNKNOWN、terminal without recovery。
- NOT_DELIVERED one retry only on new generation、unknown/rejected/accepted no retry、pre-send re-arm cap。
- Reactionとreservation同時pendingはnext grantでreservation first、active reactionはpreemptなし。
- deterministic decorator constructor/delegate one-shot/mixed input rejection、same seed contract。
- public `take_dispatched_decision`のmatching exact-once、wrong receipt non-consuming、missing-after-SENT invariant failure。
- active Brain call最大1、owner pending最大1、`wait()` before start/idempotent terminal result/waiter cancellation、
  STOP_REQUESTED/WORLD_ENDED/WORLD_FAILED/FAILED、compositionのstop/cancel/joinとunexpected failure propagation。
- Phase 3.3 BrainController testsとPhase 3.4 chat/CO trigger/finalization testsを弱めずpass。

### Completion

- `standard_9.yaml`をload後、test local immutable replaceでnight/vote/day durationだけを短縮する。
  content file/server defaultは変更しない。
- 9 separate client processes、one server process、LLM/external network無し。
- server spyはcore call成功後のaccepted vote/abilityについてplayer/day/phase/action/target/
  request event ID/server receipt time/phase deadlineを記録する。client send logを成功証拠にしない。
- 各VOTE/RUNOFF開始時のserver action stateからexpected living votersを導出し、各一acceptedをexact assertion。
- 各Night0/Night action stateからeligible ability seatsを導出し、actorごと一final accepted reservation、
  target_count/list membership/uses_remainingをassertする。
- all accepted recordsでauthoritative `accepted_at < phase_ends_at`。boundary/defect rejectionを別集計し、
  defect/unknown rejectionはfailure。
- Reaction Chatの既存all-seat/silent scenario assertionsを累積維持する。
- same seed二回でvote/ability decision列とserver accepted reservation列が完全一致し、別seed群で
  少なくとも一つのmulti-candidate selectionが変わる。
- node total 120秒未満、各scenario 55秒timeout、成功/失敗/timeout全経路で全processを回収する。
  failure時はlast replies、phase histogram、expected/accepted matrix、rejections、controller outcomes、
  deadline mapping、process exitsをdumpする。
- `tests/conftest.py`のcompletion markerへnode単位で登録する。

### Validation

- `python scripts/check_docs.py`
- `python -m pytest tests/test_phase3_5_vote_ability_controller.py -q`
- `python -m pytest tests/test_phase3_1_network_client.py tests/test_phase3_2_world_state.py tests/test_phase3_3_brain_interface.py tests/test_phase3_4_reaction_chat.py -q`
- `python -m pytest tests/test_phase3_5_completion.py -q`
- `python -m pytest -q`
- `python -m compileall -q ai_client server tests`
- `git diff --check`

## Resolved Design Questions and Rejected Alternatives

### Accepted result boundary

相関ID付き`action.accepted`を採用する。SendReceipt、phase transition、vote reveal、ability resultから
acceptedを推定する案は、hidden voteや結果通知のないabilityで証明できず、canonical §6.3にも届かない。
game coreのSERVER-only submit eventをclientへ公開する案はsecret/visibility境界を変えるため採用しない。

### Vote receipt deadline

sessionで採取した一個のserver receipt timestampをcore reservation APIの必須引数にし、strict
`started <= received < deadline`をmutation直前に検証する。Networkだけで判定する案はsingle source of
truthに反し、tickerだけに任せる案はdeadline後/tick前raceを残す。既存no-time overloadは残さない。

### Resume identity

globally unique request ID + actionをprimary identity、send/observation generationを別の診断属性とする。
同generation必須案はretained replayを相関不能にし、generation change即UNKNOWN案はreplay到着前に証拠を
捨てるため採用しない。UNKNOWNはresume replay/resumed/syncのcompleted barrierかresume不能terminalまで
延期する。

### Protocol version and migration

active formatを1.1へbumpし、1.0 schema/logをimmutableに残す。same-majorだけで混在させる案はstrict 1.0
clientへ未知eventを送るため不採用。live multi-version translationもevent replayの原形保存と実装範囲を
広げるため不採用。1.0 gameをdrainし、fresh 1.1 game/checkpointへ切り替える明示migration boundaryを採る。

### Brain coexistence

一つのbounded priority arbiterを採用する。二つのBrainController/Brainを作る案はGPU/strategy stateと
active ownershipを重複させる。BrainController内部へqueueを入れる案は承認済みno-queue契約を壊す。
Reaction Chat全体をPhase 3.5 controllerへ複製する案はpolicy責務を混ぜる。

### Public completion and dispatched-decision boundary

既存BrainControllerのprivate one-shot accessorをpublic typed methodに昇格し、arbiterがgrant内で即consume
する。mutable last-decision propertyは別invocationとの取り違えを許すため採用しない。各feature controllerは
public `wait() -> FeatureControllerExit`を持ち、compositionはprivate taskを覗かずfailureを区別・伝播する。

### Vote abstention

valid targetが無い場合だけ、serverがallows_abstainを列挙していればnullを送る。abstainを通常targetと
同じrandom poolに入れる案は推論のないbaselineでgame progressを不必要に弱める。常にnullを禁止する案は
serverが明示した合法opportunityを扱えない。

### Ability selection and use count

actorのfinal reservation slot一件に合わせ、eligible abilityを一つ、targetsをexact countだけ選ぶ。
全abilityを順次sendする案はlast-write-winsで前の選択を無意味にし、duplicate preventionに反する。
uses_remaining回送る案はreservation回数と解決時消費を混同する。

### Retry

definitely NOT_DELIVEREDだけを新connectionで一回retryする。delivery unknownやresponse missingをretryする
案はaccepted reservationを意図せず置換しうる。全失敗をretryしない案は副作用なしが証明された
pre-wire failureから安全に回復できない。

## Decision Status

`DECISION_REQUIRED: NO`。

選択、棄権、deadline、identity、outcome、retry、concurrencyはcanonical sourceと現行API制約の範囲で
確定した。protocol/sessionのaccepted ackは新しいgame ruleではなく、DESIGN §6.3に既に存在する
client-visible acceptanceを実装可能にする境界である。独立Reviewerはこのcross-component scopeを含めて
承認または修正要求を返す。承認前にPhase 3.5 productionへ進んではならない。
