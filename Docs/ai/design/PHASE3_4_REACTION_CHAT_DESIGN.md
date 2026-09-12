Status: APPROVED — Reviewer / Claude (`claude-opus-4-8`), 2026-09-08; approval evidence is retained in the game-specific review record.

# Phase 3.4 Reaction Chat Detailed Design

## Purpose

AI プレイヤーが自由会話中に、フェーズ開始、他プレイヤーの発言、および利用可能になった CO action を契機として発言または CO を判断し、サーバー締切内に送信できるようにする。

発言内容と CO の選択は既存の `Brain` に委ねる。Phase 3.4 が担うのは、呼び出し時機、頻度制限、締切、送信結果の確定、拒否の可視化、および全席完走検証である。ゲームコア、サーバー、LLM の有無に依存しないクライアント制御として設計する。

## Scope

今回実装する範囲は次のとおり。

- フェーズ開始時の初回発言機会
- 同一 channel の他プレイヤー発言に対する reaction 機会
- CO action handle 出現時の CO 機会
- 1 席・1 フェーズあたりの発言回数上限、最小発言間隔、決定的 jitter
- World が公開する transport observation を用いた締切判定と `action.rejected` の観測
- 送信後の accepted/rejected 確定を待つ直列制御
- 意図的沈黙、締切抑止、timeout、拒否を区別できる観測 API
- 9 席同時実行と 1 席沈黙を含む Phase 3.4 完走テスト

## Files / Modules

### New

- `ai_client/reaction_chat/types.py`
  - controller 設定、公開 snapshot、outcome record、lifecycle、trigger 種別を定義する。
- `ai_client/reaction_chat/__init__.py`
  - Phase 3.4 の公開型と controller だけを export する。
- `ai_client/reaction_chat/controller.py`
  - World の履歴・action handle・transport observation を監視し、Brain 呼び出しを直列化する。
- `ai_client/reaction_chat/randomness.py`
  - master seed、player id、機会識別子から決定的な jitter を導出する。
- `ai_client/world/transport.py`
  - semantic history と分離した bounded transport journal を実装する。
- `tests/test_phase3_4_reaction_chat.py`
  - controller の単体・統合テストを置く。
- `tests/test_phase3_4_completion.py`
  - 実サーバーと複数 client process を用いる Phase 3.4 完走テストを置く。
- `tests/fixtures/phase3_4_reaction_client_process.py`
  - Network、World、BrainController、ReactionChatController を同一 clock domain で構成する完走用 client process とする。`PhaseBrainCoordinator` は起動しない。
- `tests/fixtures/phase3_4_reaction_brain.py`
  - 完走シナリオ専用の deterministic な speaking/silent Brain を定義する。production Brain として export しない。
- `tests/fixtures/reaction_chat_evidence.py`
  - Phase 3.4 固有の完走証跡を集計・検証する。既存の Phase 3.1 証跡分類定数を参照し、複製しない。

### Changed

- `ai_client/network/types.py`
  - transport の観測時刻と generation を持つ notice を追加・拡張する。
- `ai_client/network/client.py`
  - server timestamp と注入済み monotonic clock を対応付け、締切 mapping notice を発行する。retry は追加しない。
- `ai_client/network/__init__.py`
  - 新規・拡張 notice を公開 export する。
- `ai_client/world/model.py`
  - immutable な rejection/timing/deadline observation、query、retention、view を追加する。
- `ai_client/world/service.py`
  - Network notice を唯一消費する責務を維持したまま、bounded transport journal と current deadline を管理する。
- `ai_client/world/__init__.py`
  - transport observation の公開型を export する。
- `ai_client/brain/controller.py`
  - injected clock と filtered capture を追加し、optional な dispatch deadline の送信直前に World の最新 mapping と時刻を再検証する。`Brain` protocol は変更しない。
- `ai_client/brain/model.py`
  - deadline による非送信を表す結果状態と dispatch deadline value object を追加する。
- `ai_client/brain/__init__.py`
  - dispatch deadline と追加 outcome status を公開 export する。
- `tests/test_phase3_1_network_client.py`
  - notice payload、timer replacement、event/notice 順序の契約を更新する。
- `tests/test_phase3_2_world_state.py`
  - transport journal、current deadline、retention、generation の契約を追加する。
- `tests/test_phase3_3_brain_interface.py`
  - optional dispatch deadline の後方互換と送信直前抑止を追加する。既存の once-per-phase coordinator 契約は変更しない。

### Canonical documentation impact

- Phase 3.1 Network の公開 interface 記述には、`ActionRejected` の観測 metadata と新しい timing mapping notice を追記する必要がある。
- Phase 3.2 World の公開 interface 記述には、semantic history と分離された transport observation API を追記する必要がある。
- Phase 3.3 の `Brain` protocol、`PhaseBrainCoordinator` の once-per-phase 契約、および既存 Acceptance 9 は改訂しない。Phase 3.4 composition が coordinator を置換する。
- 上記は D055 が許可した transport observation 拡張だけであり、過去 phase の完了判定を遡及変更しない。

## Responsibilities

### NetworkClient

- server event の exclusive consumer にはならない。従来どおり raw `ServerEvent` を World 向けに publish する。
- server timestamp を受信したローカル monotonic 時刻と対応付ける。
- action state、state sync、day extension、day shortening から最新の local deadline mapping を発行する。
- `action.rejected` を retry せず、immutable notice として発行する。
- 古い deadline timer を cancel し、現在 generation の timer だけを有効にする。

### WorldState

- Network event/notice の唯一の consumer であり続ける。
- semantic snapshot/history と transport journal を別の store として管理する。
- current deadline を generation-aware に公開する。
- observation の追加・deadline の更新も World version の commit として扱う。
- client policy、retry、発言制御は持たない。

### BrainController

- `BrainInput` を `Brain.decide()` に渡す既存責務を維持する。
- 同時に active な判断を最大 1 個とし、queue は持たない。
- optional dispatch deadline が渡された場合だけ、send 直前に同一 mapping がまだ current であり、cutoff 前であることを確認する。
- coherent World view と action handle subset から `BrainInput` を構築する唯一の主体であり続ける。ReactionChatController は `BrainInput` を直接組み立てない。
- content、発言頻度、reaction policy は決めない。

### ReactionChatController

- World の公開 API だけを読み、Network notice を直接 consume しない。
- trigger を coalesce し、BrainController 呼び出しを直列化する。
- chat cap、間隔、jitter、cutoff、unresolved send を管理する。
- Brain に渡す action handles を、その機会に関係する handles だけへ絞る。
- send の最終結果を own accepted history または rejection observation で確定する。
- role 名、team、alignment、役職固有条件を解釈しない。

### Completion evidence

- 期待席・期待 action opportunity は server の action state から導出する。
- accepted chat は core authorization 後の server `queue_channel_message` 境界で spy する。
- client 自己申告だけで成功を判定しない。

## Public Interfaces

以下はデータ契約であり、名称の軽微な Python 上の配置は既存 module convention に合わせてよい。意味、必須 field、immutability は変更しない。

### Network notices

```text
ActionRejected
  action: str
  reason: str
  seq: int
  connection_generation: int
  observed_at_monotonic: float

PhaseTimingMapped
  phase: str
  day: int
  source_seq: int
  connection_generation: int
  action_generation: int
  server_timestamp: int
  phase_ends_at: int | None
  mapped_at_monotonic: float
  local_deadline_monotonic: float | None

PhaseDeadlineReached
  phase: str
  day: int
  connection_generation: int
  action_generation: int
  local_deadline_monotonic: float
  reached_at_monotonic: float
```

`local_deadline_monotonic` は次式で一度だけ計算する。

```text
mapped_at_monotonic + max(0, phase_ends_at - server_timestamp)
```

`server_timestamp` と `phase_ends_at` は protocol schema と同じ server domain の整数秒であり、右辺の差も整数秒である。local domain の `mapped_at_monotonic`、`local_deadline_monotonic`、`observed_at_monotonic` は `float` のままとする。`phase_ends_at` が無い state では local deadline も `None` とする。Network は同じ受信 payload の raw `ServerEvent` を先に publish し、その後 `PhaseTimingMapped` を publish する。到達時は対応する `PhaseDeadlineReached` を publish する。

### World transport observation

```text
TransportObservationRetention
  max_records: int = 256
  max_bytes: int = 262144

TransportObservationQuery
  after_order: int | None = None
  kinds: frozenset[TransportObservationKind] | None = None

ActionRejectionObservation
  order: int
  world_version: int
  action: str
  reason: str
  seq: int
  connection_generation: int
  observed_at_monotonic: float

PhaseTimingObservation
  order: int
  world_version: int
  phase: str
  day: int
  source_seq: int
  connection_generation: int
  action_generation: int
  server_timestamp: int
  phase_ends_at: int | None
  mapped_at_monotonic: float
  local_deadline_monotonic: float | None

PhaseDeadlineReachedObservation
  order: int
  world_version: int
  phase: str
  day: int
  connection_generation: int
  action_generation: int
  local_deadline_monotonic: float
  reached_at_monotonic: float

CurrentPhaseDeadline
  mapping_order: int
  phase: str
  day: int
  connection_generation: int
  action_generation: int
  local_deadline_monotonic: float | None

TransportObservationView
  world_version: int
  first_retained_order: int | None
  last_order: int | None
  gap_before_first: bool
  observations: tuple[TransportObservation, ...]
  current_deadline: CurrentPhaseDeadline | None

WorldState.transport_observations(query) -> TransportObservationView
```

records は immutable、`order` 昇順で返す。`after_order` より大きい records だけを返す。要求 cursor が retention により失われた場合は `gap_before_first=True` とする。byte accounting は既存 history retention と同じ deterministic encoding 方針を再利用する。最新 1 record 自体が byte limit を超える場合もその 1 record は保持し、古い records をすべて落とす。

semantic `WorldSnapshot.complete` と既存 `HistoryRetention` の意味は変更しない。transport gap は semantic history gap と混同しない。

### Brain dispatch deadline

```text
Clock = Callable[[], float]

DispatchDeadline
  mapping_order: int
  phase: str
  day: int
  connection_generation: int
  action_generation: int
  not_after_monotonic: float

BrainController(
  *,
  world: WorldState,
  sender: NetworkClient,
  brain: Brain,
  config: BrainRunConfig = BrainRunConfig(),
  clock: Clock = time.monotonic,
)

BrainController.capture_input(
  *,
  allowed_handles: tuple[ActionHandle, ...] | None = None,
) -> BrainInput | None

BrainController.decide_and_send(
  ...,
  dispatch_deadline: DispatchDeadline | None = None,
) -> DecisionOutcome
```

`clock` は constructor で保持し、dispatch deadline の全比較にこの callable だけを使う。既定値は NetworkClient の既定値と同じ `time.monotonic` であり、composition は同じ callable instance を NetworkClient、BrainController、ReactionChatController へ明示注入する。asyncio の待機制御が内部で loop clock を使っても、server deadline との比較には使わない。

`capture_input()` の引数省略時は Phase 3.3 と同じく current actions 全件を capture する。`allowed_handles` 指定時は、BrainController が再取得した coherent な `current_actions().actions` に全指定 handle が重複なく存在することを検証し、current actions の canonical order で subset を作る。空集合、重複、欠落、型不正、version/seq 不一致では `None` を返す。option id は subset を `action:0` から再採番し、各 `BrainActionOption.handle` は元の `ActionHandle` object を保持する。これにより Phase 3.3 の version/seq 一致、決定的 option id、元 handle 保持という capture contract を部分集合にもそのまま適用する。ReactionChatController は World から relevant handles を選び、この API に渡すだけで `BrainInput` を直接生成しない。

既存 caller は `allowed_handles=None`、`dispatch_deadline=None` のままで動作する。deadline が stale、current mapping 不在、または送信直前に `self._clock() >= not_after_monotonic` なら送信せず `DecisionStatus.DEADLINE_SUPPRESSED` を返す。これは Brain failure、timeout、NoDecision と別状態である。

### Reaction chat configuration and observation

```text
ReactionChatConfig
  max_chat_attempts_per_phase: int = 2
  minimum_accepted_chat_interval_seconds: float = 0.20
  initial_jitter_seconds: [0.00, 0.20]
  reaction_jitter_seconds: [0.05, 0.15]
  deadline_guard_seconds: float = 0.25
  brain_timeout_seconds: float = 0.25
  minimum_start_budget_seconds: float = 0.05
  outcome_retention: int = 256

ReactionChatController.start() -> None
ReactionChatController.stop() -> None
ReactionChatController.snapshot() -> ReactionChatSnapshot

ReactionChatController(
  *,
  world: WorldState,
  brain_controller: BrainController,
  master_seed: int,
  config: ReactionChatConfig = ReactionChatConfig(),
  clock: Clock = time.monotonic,
)
```

`ReactionChatSnapshot` は最低限、lifecycle、current phase key、history/transport cursor、chat Brain invocation 数、send 数、accepted 数、rejected 数、deadline-suppressed 数、intentional-silence 数、CO generation 状態、および bounded outcome records を持つ。snapshot は immutable とする。

outcome record は trigger、scheduled due time、Brain outcome、action kind、send/finalization 状態を持ち、少なくとも次を識別可能にする。

- `NO_DECISION`: Brain の意図的沈黙
- `DEADLINE_SUPPRESSED`: 締切または stale mapping により非送信
- `TIMED_OUT`: Brain 判断 timeout
- `ACCEPTED`: own history により受理確認
- `REJECTED`: rejection observation により拒否確認
- `TRANSPORT_GAP`: rejection の完全追跡不能
- `HISTORY_GAP`: reaction 入力の完全追跡不能

## Data Flow

1. Network が server action state を受信する。
2. Network は raw `ServerEvent` を publish し、World が semantic state/action handles を更新する。
3. Network は同じ payload の server timestamp と local receive clock から `PhaseTimingMapped` を publish する。
4. World は notice を transport journal に追加し、同一 commit で current deadline を更新する。
5. ReactionChatController は World の snapshot、history、transport view を読み、phase/action/deadline を同じ World version の観測として処理する。
6. 初回、他者 chat、または CO handle 出現を trigger として候補機会を生成し、決定的 jitter 後の due time を計算する。
7. due time 到達時に最新 World state、cap、interval、cutoff、gap を再検証する。
8. 関係 action handles だけを含む `BrainInput` を capture し、BrainController を呼ぶ。
9. BrainController は判断完了後、send 直前に World の current deadline mapping を再読し、有効なら既存 command sender へ送る。
10. ReactionChatController は次の Brain 呼び出しへ進まず、own accepted semantic history または matching rejection observation を待つ。
11. accepted/rejected/timeout/silence/deadline suppression を outcome に確定し、次の pending trigger を評価する。

## State / Lifecycle

### Controller lifecycle

```text
NEW -> RUNNING -> STOPPING -> STOPPED
```

- `start()` は 1 回だけ controller task を所有する。
- `stop()` は pending timer、World wait、active BrainController call を cancel し、子 task を回収する。
- disconnect 中は新規送信を開始しない。reconnect 後は新 connection generation の state sync と timing mapping が揃ってから再開する。

### Per-phase chat state

phase key は `(connection_generation, day, phase, action_generation)` で識別する。

保持する状態:

- initial opportunity が生成済みか
- latest pending reaction history order
- chat Brain invocation count（上限 2）
- accepted self-chat count と最後の accepted self-chat monotonic observation time
- unresolved chat send の有無
- matching rejection により chat path が閉じたか
- deadline/gap により chat path が閉じたか

新しい phase key で初期化する。chat cap は固定値 2 であり、生存者数や role から導出しない。Brain invocation を cap 消費単位とし、`NoDecision`、invalid decision、timeout も消費する。これにより失敗時の無限再試行を防ぐ。

### CO state

CO は chat cap と別に `(phase key, action_generation)` ごとに管理する。

- `CoDeclareAction` または `CoReportAction` handle が初めて出現したとき pending にする。
- 同じ generation では Brain 呼び出しを最大 1 回とする。
- handle が一度消えて再出現しても同じ generation なら再試行しない。
- rejection はその generation の CO path を閉じる。

### Deadline lifecycle

- action state/state sync/day extension/day shortening の新しい mapping が current を置換する。
- old timer は cancel する。cancel 済み timer が race で発火しても generation/mapping が current でなければ World current deadline を変更しない。
- disconnect/state reset 時に current deadline を clear する。transport journal 自体は bounded retention 内で保持し、ローカル証跡を失わせない。
- reconnect 後は同じ source seq の semantic state に続く新 generation の mapping により current を再確立する。
- extension 後に cap が残っていれば新しい発言機会を作ってよい。旧 mapping で開始した Brain decision は再利用せず stale として抑止する。

## Main Control Flow

1. `tests/fixtures/phase3_4_reaction_client_process.py` が同一 callable の monotonic clock を Network、BrainController、ReactionChatController に渡す。
2. Phase 3.4 client process は `PhaseBrainCoordinator` を起動せず、ReactionChatController を起動する。
3. controller は World change を待ち、phase key、current handles、history delta、transport delta を読む。
4. phase に `ChatAction` があれば initial trigger を 1 回生成する。
5. current player 以外の新しい `ChatRecord` で、current phase/day かつ送信対象と同じ channel のものだけを reaction trigger にする。self chat、system message、別 channel は除外する。
6. 複数 reaction が待機中なら history order が最新の 1 件へ coalesce する。無制限 queue は作らない。
7. CO pending と chat/reaction pending が同時なら CO を先に処理する。active Brain 呼び出しは preempt しない。
8. trigger の due time は deterministic jitter で決める。master seed と player id を SHA-256 で分離し、さらに phase key、trigger kind、source order、attempt ordinal を hash して 53-bit fraction に変換する。Python `hash()` と module-level `random` は使わない。
9. reaction の due time は `max(trigger observation time + reaction jitter, last accepted self-chat time + 0.20)` とする。initial send が未確定の間に届いた reaction は破棄せず latest-one として保持し、確定後にこの式で再評価する。
10. due time で以下を順に検証する。
   - lifecycle が RUNNING
   - current phase key と mapping が trigger 作成時と一致
   - relevant action handle が現在も存在
   - unresolved send が無い
   - chat の場合は invocation cap 未満
   - last accepted self chat から 0.20 秒以上
   - `cutoff = local_deadline_monotonic - 0.25`
   - `cutoff - clock() >= 0.05`
11. Brain timeout は `min(0.25, cutoff - clock())` とする。server epoch の `phase_ends_at` と local clock を直接比較しない。
12. ReactionChatController は current actions から relevant handles を選び、`BrainController.capture_input(allowed_handles=...)` を呼ぶ。BrainController が coherent view の再取得、subset 検証、deterministic 再採番を行う。role 名の分岐は行わない。
13. BrainController へ current mapping に対応する `DispatchDeadline` を渡す。
14. `SENT` 後は同種 action の結果を待つ。
   - chat: current player の同一 message/action に対応する accepted ChatRecord、または `action == chat.send` の rejection
   - CO: action kind が一致する semantic state 反映、または同 action kind の rejection
15. 1 件を確定してから pending trigger を再評価する。1 controller あたり unresolved send は最大 1 件である。

## Resolved Design Questions

### Q1. Phase 3.3 once-per-phase coordinator との整合

Phase 3.3 の `PhaseBrainCoordinator` と既存 Acceptance 9 は変更しない。Phase 3.4 の composition root が coordinator を ReactionChatController へ置換し、ReactionChatController が既存 BrainController を 1 回ずつ直列に複数回呼ぶ。BrainController の「active 最大 1・queue 無し」も維持する。

採用しない案: coordinator 自体を複数回化すると Phase 3.3 の承認済み契約を遡及変更し、reaction policy と phase coordination の責務が混ざる。

### Q2. 発言回数、間隔、締切

chat Brain invocation は 1 席・1 フェーズ最大 2 回、accepted self chat 間隔は最低 0.20 秒、締切 guard は 0.25 秒、開始最低 budget は 0.05 秒とする。初回 jitter は 0.00–0.20 秒、reaction jitter は 0.05–0.15 秒である。

完走シナリオは `standard_9.yaml` を読み込んだ後、テスト内の immutable `replace()` で `night_seconds=1`、`silence_after_dawn_seconds=0`、`day_seconds=2`、`vote_seconds=1` へ差し替える。`server/` と content preset は変更しない。

2回の chat 機会に必要な最低余裕は次のとおりで、要求された式では 1.15 秒、各 Brain が timeout 上限まで使う保守計算でも 0.65 秒残る。

```text
2.00 - guard 0.25 - initial jitter max 0.20
     - accepted interval 0.20 - reaction jitter max 0.15
     - minimum start budget 0.05
  = 1.15 seconds > 0

1.15 - first Brain timeout max 0.25 - second Brain timeout max 0.25
  = 0.65 seconds > 0
```

完走用 speaking Brain は同期的に決定を返すため、通常経路では timeout budget を消費しない。上記 0.65 秒は client scheduling と同一 host の server round-trip に対する余裕として残す。

採用しない案: Phase 3.3 と同じ `day_seconds=1` では保守計算が負になるため採用しない。server deadline 直前まで送信を許す案も scheduling/transport 遅延を吸収できない。

### Q3. reaction の coalescing と優先順位

他者 chat は最新 history order だけを pending として保持する。CO pending を chat より優先するが、active Brain call は中断しない。reaction backlog は作らない。

採用しない案: 発言ごとの FIFO queue は混雑時に stale な返答を生成し、deadline 後まで work を残す。

### Q4. action.rejected の相関と扱い

controller は unresolved send が 1 件だけであることを利用し、action kind と current connection/action generation で rejection を相関する。production policy は reason 文字列を分類せず、matching rejection は current phase/gen の対応 path を閉じ、同じ path を再送しない。unmatched rejection も journal と snapshot から観測可能にする。

完走テストのみ、既存 Phase 3.1 evidence の `BOUNDARY_REJECTION_REASONS` と `DEFECT_REJECTION_REASONS` を参照する。boundary rejection は証跡に残して許容、defect または unknown reason は失敗とする。

採用しない案: reason allowlist を production controller に複製すると server 文言と client policy が密結合になる。

### Q5. transport observation の retention と reconnect

semantic history と別に 256 records/256 KiB の bounded journal を持つ。disconnect/state reset は current deadline のみ clear し、journal は retention 範囲で保持する。generation が古い observation は証跡として返すが current state には昇格させない。

採用しない案: sync ごとに journal を全消去すると rejected send の最終結果を観測不能にする。

### Q6. deadline clock mapping

Network の receive 時点で server timestamp と injected monotonic clock を mapping し、World 経由で公開する。Reaction/Brain は local deadline だけを比較する。mapping identity を dispatch deadline に含め、extension/shortening/reconnect による stale decision を送信直前に抑止する。この API は Phase 3.5 でも再利用する。

採用しない案: `phase_ends_at` と `time.monotonic()` の直接比較は clock domain が異なり不正である。

### Q7. 沈黙と失敗の観測

Brain の `NoDecision` を intentional silence として数える。deadline suppression、Brain timeout、invalid/failed decision、send rejection、transport/history gap は別 outcome にする。単に send 数が 0 であることを沈黙と推定しない。

採用しない案: すべてを「発言なし」に集約すると Phase 3.4 の故障検出条件を満たせない。

### Q8. 完走テストの検証コントラクト

Q8 を最初に固定する。既存 Phase 3.1 の `_CompletionClock`、process cleanup、diagnostic dump、server reply classification を再利用し、Phase 3.4 固有 evidence は別 module に置く。

`tests/fixtures/phase3_4_reaction_brain.py` の `CompletionReactionBrain` を全 client process で使う。constructor は `player_id`、scenario `seed`、`mode`（`SPEAK` または `SILENT`）を受け、既存 `Brain` protocol の `async decide(BrainInput) -> BrainDecision` だけを実装する。

- `SPEAK`: filtered options に `ChatAction` がちょうど1件ある chat opportunity では、その option id を使う `ChatDecision` を即時返す。message は `phase3.4/{seed}/{player_id}/{day}/{phase}/{chat_ordinal}` の固定形式とし、ordinal はその Brain が返した ChatDecision の phase 内 1-based 番号とする。chat 以外の opportunity では `NoDecision` を返す。
- `SILENT`: opportunity 種別にかかわらず常に `NoDecision` を返す。silent seat でも controller は起動し、NoDecision、cap、game-end を通常経路で観測する。
- Brain 内では乱数、時刻、process hash seed を使わない。同じ seed、player id、同じ BrainInput 列では decision と message 列が完全一致する。controller jitter も既定済みの SHA-256 導出を使うため、同じ scenario seed の due-order/outcome 列は fake clock unit test で完全一致する。

`DummyBrain` は常に NoDecision で all-seat scenario を成立させられないため完走 fixture には使わない。Phase 4 の自然言語生成を先取りする Brain も採用しない。

完走テストは最低 2 scenario を 1 node で実行する。

1. all-seat reaction scenario
   - server action state が 9 席すべてに `chat.send` opportunity を提示する。
   - 9 席すべてが deadline より前に 1 件以上 accepted chat を持つ。
   - 指定した reaction phase では各非沈黙席がちょうど 2 件 accepted chat を持つ。
   - 全席が game end まで到達する。
2. one-silent scenario
   - expected set は silent seat を含む server 提示 9 席である。
   - silent seat の accepted chat は 0、他 8 席は deadline 前に 1 件以上である。
   - silent seat が他席の進行を block せず、全 9 席が game end まで到達する。

accepted chat の canonical spy point は core authorization 後の `queue_channel_message` とし、player id、day、phase、channel、server accepted time、phase deadline を 1 accepted message につき 1 record 保存する。accepted 条件は `accepted_at < phase_ends_at` である。

両 scenario は Q2 の同じ rules override と明示的な scenario seed を使う。all-seat は全席 `SPEAK`、one-silent は指定1席だけ `SILENT`、残り8席を `SPEAK` とする。各 scenario の seed は test constant とし、再実行中に生成しない。

集計は player/phase/ordinal と first speaker を exact histogram として出力する。許容範囲は 0–2 件であり、全員同数や均等順序の fairness 下限は課さない。seed bias は seed を少なくとも 2 種類使う deterministic unit test で first-due seat が異なることを確認し、固定席順の退行を検出する。

node 全体は 100 秒未満、各 scenario は 45 秒 timeout とし、cleanup は成功・失敗・timeout の全経路で `finish_process` を通す。失敗時は process exit、last server replies、accepted histogram、rejection histogram、controller outcomes、current deadline mapping を dump する。

採用しない案: client send log のみを成功証跡にすると、server rejection や deadline 超過を成功として誤認する。

## Reviewer Addendum Resolutions

- R-20260905-08: `CompletionReactionBrain` の SPEAK/SILENT 挙動、固定 message、同一 seed 再現性を Q8 に確定した。DummyBrain 流用は all-seat 条件を満たさないため不採用。
- R-20260905-09: completion rules を Q2 の4値に固定し、要求式 1.15 秒、Brain timeout 込み 0.65 秒の正の余裕を示した。`server/` は変更しない。
- R-20260905-10: `BrainController.__init__(..., clock: Clock = time.monotonic)` を Public Interfaces に追加し、deadline 比較の clock domain を一意にした。
- R-20260905-11: subset 構築主体を BrainController に固定し、`capture_input(allowed_handles=...)` と Phase 3.3 capture contract の維持方法を宣言した。ReactionChatController による直接構築は不採用。
- R-20260905-12: server domain の timestamp/deadline を schema と同じ `int` へ修正し、local monotonic 値だけを `float` に保った。

## Failure Handling

### Invalid input

- malformed/unknown Network notice は既存 Network validation failure として扱い、World record を部分生成しない。
- invalid retention/config（非正値、範囲逆転、guard/timeout が負）は construction 時に拒否する。
- Brain の invalid decision は既存 `INVALID` outcome とし、chat invocation cap は消費する。自動再試行しない。

### Timeout

- Brain timeout は残り cutoff budget 以下へ clamp する。
- cutoff まで 0.05 秒未満なら Brain を開始せず `DEADLINE_SUPPRESSED` とする。
- send 後の結果待ちは phase/generation change、deadline reached、disconnect のいずれかで打ち切り、未確定を対応 outcome として残す。

### Disconnect / reconnect

- disconnect で pending timer と新規 dispatch を停止し、World current deadline を clear する。
- active Brain call は cancel する。既に command sender へ渡した send の結果は unknown として残し、同じ generation で再送しない。
- reconnect 後は新 generation の state sync と timing mapping を待つ。旧 generation の pending work は再利用しない。

### Exception

- controller child task の予期しない例外は lifecycle を STOPPING へ移し、snapshot outcome と process supervision に公開する。黙って loop を再起動しない。
- World read/query 例外は active opportunity を失敗として閉じ、上位 supervisor へ伝播する。
- Brain exception は既存 `BRAIN_FAILED` のまま扱う。

### Partial failure / gaps

- transport journal gap は unresolved rejection の完全追跡を保証できないため、current phase の chat/CO send path を閉じる。
- semantic history gap は reaction source を完全に追跡できないため reaction trigger だけを抑止する。current state が complete で deadline/action handle が有効なら initial opportunity は許可する。
- matching rejection は retry せず path を閉じる。unmatched rejection は観測 record として保持する。
- deadline extension は cap が残る場合に新機会を許すが、旧 decision を再送しない。

## Concurrency

- ReactionChatController が controller loop、due timer、World wait、active Brain call の owner である。
- BrainController の active decision は最大 1。ReactionChatController も unresolved send を最大 1 に制限する。
- queue は置かない。reaction は latest-one coalescing、CO は 1 pending bit で表現する。
- World の immutable view と version/cursor を用い、共有 mutable record を controller 外へ渡さない。
- World update と controller wakeup の race は、dispatch 直前の phase key/action handle/deadline mapping 再検証で閉じる。
- cancel は task owner が行い、必ず await して回収する。cancel 済み deadline timer の notice は mapping/generation check により stale 扱いにする。
- master seed からの jitter は event arrival の task scheduling 順に依存させず、安定した opportunity identity から直接導出する。

## Explicitly Out of Scope

- `Brain` protocol または content generation contract の変更
- `PhaseBrainCoordinator` の once-per-phase 契約変更
- server protocol、game core、role/team/ability/content YAML の変更
- Network による retry、発言可否判定、reason policy
- role 名や特定役職に基づく CO 分岐
- prompt filtering による secret protection
- multi-channel routing の新仕様、private chat、固定発言順
- Phase 3.5 の ability execution、将来 phase のまとめ設計
- fairness を保証する scheduler、全席同数発言の要求
- unrelated refactor、履歴 store の統合

## Acceptance Criteria

1. Phase 3.4 completion client process で ReactionChatController が稼働し、PhaseBrainCoordinator は同時起動しない。
2. `Brain` protocol と Phase 3.3 once-per-phase coordinator の既存 test/契約が維持される。
3. current phase に chat handle がある各非沈黙席へ initial opportunity が 1 回生成される。
4. 同一 channel の他者 chat に reaction でき、self/system/別 channel chat は trigger にならない。
5. chat Brain invocation は 1 席・1 フェーズ 0–2 回で、accepted self chat の間隔が 0.20 秒未満にならない。
6. 複数 reaction は latest-one に coalesce され、unbounded queue が存在しない。
7. CO handle 出現時に generation あたり最大 1 回の CO opportunity があり、role 名を参照しない。
8. Network は action state/sync/extension/shortening を同一 monotonic domain の deadline に mapping し、World 経由で immutable に公開する。
9. stale mapping、cutoff 後、disconnect 後の Brain decision は send されず `DEADLINE_SUPPRESSED` 等で観測できる。
10. `action.rejected` は retry されず、World transport journal と Reaction snapshot の双方から確認できる。
11. intentional silence、timeout、invalid/failed、deadline suppression、accepted、rejected、gap が区別される。
12. all-seat completion scenario で 9 席すべてが deadline 前に accepted chat を持ち、指定 phase で期待件数を満たし、game end まで到達する。
13. one-silent scenario で silent seat が 0 件でも他 8 席と全体終了を block しない。
14. first-due seat の histogram が seed に応じて変わり、固定席順でないことを LLM 無しで再現できる。
15. completion node は 100 秒未満、各 scenario は 45 秒 timeout、全経路で子 process cleanup と診断出力を行う。
16. 既存 Phase 3.1–3.3 test、Phase 3.1 completion contract、`python scripts/check_docs.py` が通る。
17. completion fixture の speaking/silent Brain が Q8 の decision/message 列を返し、同一 seed の fake-clock 実行で同じ due-order/outcome 列を再現する。
18. completion preset は `day_seconds=2` を含む Q2 の override を test 内だけで適用し、保守計算でも cutoff 前の余裕が正である。
19. BrainController の clock 既定値・明示注入と filtered capture API が Public Interfaces の signature どおりである。

## Required Tests

### Network

- action state/state sync の mapping formula と `None` deadline
- day extension/day shortening による timer replacement
- stale/cancelled timer notice の generation metadata
- `ServerEvent -> PhaseTimingMapped` の publish 順序
- `ActionRejected` の action/reason/seq/generation/observed time と retry 無し
- existing reconnect、reader/writer lifecycle regression

### World

- notice から immutable observation への変換
- observation mutation ごとの world version commit
- current deadline の置換・clear・stale generation 無視
- `after_order`、kind filter、oldest-first order
- record/byte retention、oversized newest record、gap flag
- sync 後も journal は保持され current deadline は新 mapping まで空であること
- semantic snapshot complete/history retention が不変であること

### BrainController

- constructor の injected clock と `time.monotonic` default
- `capture_input()` 引数省略時の全件 capture 後方互換
- `allowed_handles` の canonical-order subset、再採番、元 handle identity、重複/欠落/空集合拒否
- dispatch deadline 未指定時の Phase 3.3 後方互換
- send 直前で current mapping が一致する場合の送信
- mapping replacement、cutoff 到達、deadline clear の各 suppression
- Brain 実行中の extension/shortening/reconnect race
- existing active-one/no-queue/cancellation tests
- existing PhaseBrainCoordinator once-per-phase test を無変更で通す

### ReactionChatController

- phase initial opportunity と chat handle 不在時の抑止
- same-channel other chat reaction、self/system/other-channel 除外
- latest reaction coalescing と CO priority
- phase chat cap 2、NoDecision/timeout/invalid も cap 消費
- accepted interval 0.20 秒
- deterministic jitter の同 seed 再現性、異なる seed の first-due 差、process hash seed 非依存
- relevant handle filtering と option id の deterministic renumbering
- CO generation あたり 1 回、handle 再出現で再試行しないこと
- unresolved send 最大 1、accepted/rejected 確定前に次を開始しないこと
- matching/unmatched rejection、reason 非依存の production behavior
- history gap と transport gap の異なる抑止範囲
- disconnect/cancel/phase change/deadline extension の stale work cleanup
- snapshot で全 outcome が区別でき retention が bounded であること

### Completion

- `CompletionReactionBrain(SPEAK)` の固定 ChatDecision/message、非chat NoDecision、同一入力列の再現性
- `CompletionReactionBrain(SILENT)` の全 opportunity NoDecision と accepted 0
- Q2 rules override が test 内だけに適用され、`day_seconds=2` であること
- all-seat reaction scenario の 9 席 expected/accepted/deadline/game-end exact assertion
- one-silent scenario の silent=0、others>=1、全席 game-end exact assertion
- accepted spy が authorization 後だけを数え、1 message 1 record であること
- boundary rejection は記録して許容、defect/unknown rejection は失敗
- player/phase/first-speaker histogram と controller outcome diagnostics
- scenario timeout、node total budget、success/failure/timeout の process cleanup
- `pyproject.toml` に登録済みの `completion` marker を使い、LLM/外部 network 無しで再現可能であること

## Deliberately Not Decided

- production Brain がどの発言文を生成するか、また発言を選ぶ確率は Brain 実装の責務として固定しない。Phase 3.4 completion fixture の Brain 挙動と文面は Q8 で完全に固定する。
- chat content の意味的品質、会話の自然さ、役職別 strategy は Phase 3.4 完了条件に含めない。
- fairness の統計閾値は設けない。観測 histogram と seed による順序変化だけを要求する。
- D055 の transport observation 範囲を超える server/protocol の拡張は決めない。
