Status: APPROVED — Reviewer 承認（2026-09-01）。schema / close code / clock /
replay 保持の記述は実物と突き合わせて一致を確認した。実装時の確認事項は R-20260901-93。

# Phase 3.1 Network Client — Detailed Design

## Purpose

AI Client の上位層から WebSocket、認証、protocol envelope、再接続、受信順の欠落、
送信直列化を隠蔽し、サーバが許可した本人視点の情報を受け取り続ける基盤を提供する。
この基盤はサーバを唯一の正しい状態として扱い、受信データを保持して上位へ渡すが、
役職・ゲームルール・発言内容・投票先・能力対象を判断しない。

## Scope

ROADMAP §3.1 の「繋がり続けて、受け取って、送れる」までを実装対象とする。
初回 Join、Resume、strict schema 検証、最新受信状態、締切通知、typed send API、
欠番回復、切断時の有限再接続、明示的な終了結果を含む。

## Files / Modules

| File | Responsibility |
|---|---|
| `ai_client/__init__.py` | AI Client 側パッケージの境界。ゲームサーバを import しない |
| `ai_client/network/__init__.py` | 下記の public interface と public type だけを再 export する |
| `ai_client/network/client.py` | 接続 supervisor、Join / Resume、receive / send task、再接続、終了処理 |
| `ai_client/network/types.py` | 設定、lifecycle、immutable snapshot、action handle、通知、終了理由 |
| `ai_client/network/state.py` | 検証済み受信データの最新値、連続 checkpoint、action generation、締切世代を保持する |
| `ai_client/network/protocol.py` | canonical JSON schema を直接読み、受信検証と client envelope 作成を行う |
| `ai_client/network/credentials.py` | checkpoint store interface と atomic JSON file adapter |
| `pyproject.toml` | `ai_client*` を配布対象へ追加する。依存は既存の `websockets` / `jsonschema` を使う |
| `tests/test_phase3_1_network_client.py` | lifecycle、schema、状態、送信、欠番、再接続、失敗処理の単体・結合テスト |
| `tests/fixtures/phase3_1_network_client_process.py` | protocol 列挙だけを使う別プロセス Dummy driver |
| `tests/test_phase3_1_completion.py` | 9プロセス、1プロセス再起動、server tick のみでの完走テスト |

`ai_client/` は `server/` と同じトップレベルに置く独立パッケージとする。
`server.aiwolf_core`、`server.network`、テスト fixture のコードは import しない。
サーバとの共有物は `protocol/aiwolf-v1.schema.json` だけであり、schema を複製しない。

## Responsibilities

### Network client

- socket generation ごとの接続、認証、同期完了、切断、再接続を所有する。
- 検証済みイベントだけを state と上位 event stream へ渡す。
- 接続中の current action handle だけを client request へ変換する。
- ゲーム上の可否、対象選択、発言生成、拒否後の方針は所有しない。

### Protocol boundary

- 受信 JSON を canonical schema の `server_event` と type-specific schema の両方で
  strict に検証する。
- outbound request も送信前に同じ schema の `client_request` と type-specific schema で
  検証する。
- `protocol_version` の major を、状態更新より前に client の対応 major と比較する。
- schema path と protocol version の source を client 側 Python literal に二重化しない。
  既定 schema は repository の canonical file とし、テストでは明示的に差し替え可能にする。

### State holder

- `game.state_sync`、`player.list`、`player.deaths`、`player.action_state` の payload を
  immutable snapshot として保持する。
- `game.state_sync` は players / deaths / action_state / self / revealed_roles / history を
  1回の更新で置換する。個別3イベントは対応する最新値だけを置換する。
- payload の意味、役職、勝敗、行動理由を解釈しない。
- action spec を transport 用 capability handle へ写すことだけは行う。これは
  server が列挙したフィールドの固定であり、ゲームルールの再実装ではない。

### Credential storage

- 起動側が store instance と保存場所を所有し、1席ごとに別 store を注入する。
- Network Client は token と連続 checkpoint の整合した更新時点を所有する。
- file adapter は `{connection_token, last_seq}` を同一ディレクトリの一時ファイルへ書き、
  atomic replace する。token や保存内容をログへ出さない。

### Upper layers

- 3.2 は event stream を速やかに取り込み、Brain の推論を receive path で実行しない。
- 3.3〜3.5 は snapshot に含まれる current action handle を選び、typed send API へ渡す。
- `action.rejected`、stale action、delivery unknown の後に再試行するかは上位が決める。

## Public Interfaces

以下は API の形を固定するための signature であり、内部 helper の指定ではない。

```python
@dataclass(frozen=True)
class NetworkClientConfig:
    uri: str
    game_id: str
    entry_token: str | None
    protocol_version: str | None = None  # None は canonical schema の版を使う
    connect_timeout_seconds: float = 5.0
    sync_timeout_seconds: float = 5.0
    inbound_event_capacity: int = 1024
    outbound_command_capacity: int = 64

@dataclass(frozen=True)
class ReconnectPolicy:
    initial_delay_seconds: float = 0.25
    multiplier: float = 2.0
    max_delay_seconds: float = 5.0
    jitter_ratio: float = 0.2
    max_disconnected_seconds: float = 60.0

@dataclass(frozen=True)
class SessionCheckpoint:
    connection_token: str
    last_seq: int

class CredentialStore(Protocol):
    async def load(self) -> SessionCheckpoint | None: ...
    async def save(self, checkpoint: SessionCheckpoint) -> None: ...

class NetworkClient:
    def __init__(
        self,
        config: NetworkClientConfig,
        credential_store: CredentialStore,
        *,
        reconnect_policy: ReconnectPolicy = ReconnectPolicy(),
    ) -> None: ...

    async def run(self) -> ClientExit: ...
    async def stop(self) -> None: ...
    def snapshot(self) -> ClientSnapshot: ...
    def events(self) -> AsyncIterator[ClientEvent]: ...

    async def send_chat(self, action: ChatAction, message: str) -> SendReceipt: ...
    async def send_vote(
        self, action: VoteAction, target_player_id: str | None
    ) -> SendReceipt: ...
    async def send_ability(
        self, action: AbilityAction, target_player_ids: Sequence[str]
    ) -> SendReceipt: ...
    async def send_co_declare(
        self, action: CoDeclareAction, claimed_role_id: str, comment: str
    ) -> SendReceipt: ...
    async def send_co_report(
        self,
        action: CoReportAction,
        kind: str,
        target_player_id: str,
        claimed_result: str,
    ) -> SendReceipt: ...
```

`ClientSnapshot` は lifecycle、player_id、最後に commit した `last_seq`、上記4種の
最新 payload、current action handles、snapshot generation を読み取り専用で返す。
未受信の値は `None` とする。返却値から内部 state を変更できてはならない。

`ClientEvent` は少なくとも次を区別する。

- schema 検証済みの server event
- lifecycle transition
- `SequenceGapDetected` / `SequenceGapRecovered`
- `ActionRejected`
- `PhaseDeadlineReached`
- outbound の `NotDelivered` / `DeliveryUnknown`
- `GameEnded` / fatal termination

`SendReceipt` は request の `event_id` と「WebSocket の send が完了した」ことだけを示す。
サーバによる受理を示さない。action request には応答相関IDが無いため、
`action.rejected` と特定 request の1対1対応を API として約束しない。

raw message を任意に送る public API は作らない。typed API は次を直接検査する。

- handle が現在の connection / action generation のものか
- chat channel と ability ID は handle 自身の値を使っているか
- vote / ability target が列挙内か、件数が列挙値と一致するか
- abstain が列挙された `allows_abstain` に従うか
- CO role が列挙された `claimed_role_ids` に含まれるか
- `co_report` は current `co_report` handle の存在だけを要求し、kind / result の意味は判断しない

これらは受信列挙との構造的一致だけであり、サーバの可否判定を複製しない。

## Data Flow

### Receive

1. supervisor が checkpoint の有無に応じて Join または Resume を送る。
2. receiver が1本ずつ JSON decode、strict schema 検証、major version 検査を行う。
3. sequence gate が `last_seq` と比較し、連続イベント、重複、欠番、sync recovery barrier を分ける。
4. state holder が対象 payload を immutable snapshot へ反映する。
5. checkpoint store へ token / 新しい連続 `last_seq` を atomic に保存する。
6. 保存完了後に server event と派生した client notice を event stream へ公開する。
7. `player.action_state` または `game.state_sync.action_state` から action generation と
   deadline notification を置き換える。

認証直後は毎回 `session.ready` を送る。Resume 済みの席でも server 側で冪等であり、
Join 後に ready 送信前で落ちた場合を追加の永続 state なしで回復できる。

### Send

1. Controller が最新 snapshot から action handle を選ぶ。
2. typed API が current generation と列挙フィールドとの直接一致を検査する。
3. protocol module が新しい `event_id` を持つ request envelope を作り schema 検証する。
4. bounded outbound queue へ追加する。
5. connection generation に1つだけの sender task が FIFO で WebSocket へ送る。
6. send 完了で receipt を返す。切断境界で結果不明なら `DeliveryUnknown` とする。

## State / Lifecycle

```text
NEW
 ├─ checkpoint なし ─> JOINING ─> SYNCHRONIZING ─> CONNECTED
 └─ checkpoint あり ─> RESUMING ─> SYNCHRONIZING ─> CONNECTED

CONNECTED ── transport loss / seq gap ─> RECONNECT_WAIT ─> RESUMING
JOINING / RESUMING / SYNCHRONIZING ── transient failure ─> RECONNECT_WAIT

any nonterminal ── stop / cancellation ─> STOPPING ─> ENDED
CONNECTED ── GAME_ENDED ─> ENDED
any nonterminal ── fatal error / reconnect budget exhausted ─> FAILED
```

- `CONNECTED` になる条件は認証応答だけでなく、その socket generation の
  `game.state_sync` を受理して state を再基準化したこととする。
- 再接続を開始した時点で既存 action handles を inactive にし、送信 API を閉じる。
- 同期後に新しい action generation を発行する。古い handle は state 内容が同じでも stale。
- `GAME_ENDED` を受信したら新規送信を閉じ、checkpoint と event 公開を完了して正常終了する。
- `phase_ends_at` がある場合、受信 envelope の server `timestamp` との差を待ち時間とする。
  client monotonic clock は sleep のためだけに使い、締切や行動可否を確定しない。
  新しい action state、再同期、切断、終了で以前の timer generation を cancel する。

## Main Control Flow

通常運転の1周は、receiver が server event を検証・sequence commit・状態反映・永続化・
event 公開し、その間 sender が上位からの request を独立して送る流れである。
上位は event を state/memory へ取り込んだ後に必要なら Brain を別 task で起動し、
結果が締切前に得られたときだけ current handle で送信する。Brain が返らなくても
Network Client は受信と再接続を継続し、代替発言を生成しない。

## Failure Handling

### Invalid input and protocol

- 起動設定、credential file、outbound payload が不正なら socket へ送らず `FAILED`。
- 受信 JSON、strict schema、server event type が不正なら server bug / incompatible endpoint として
  socket を閉じ、再接続せず `FAILED`。
- major version 不一致は状態を一切反映せず `INCOMPATIBLE_PROTOCOL` で `FAILED`。
  同一 major の minor 差は schema 検証を通る範囲だけ受理する。
- 認証前の close 1008、無効 token、破損 checkpoint は fatal。保存済み credential がある場合、
  entry token への fallback はしない。

### Timeout and disconnect

- connect と state sync に別 timeout を設ける。timeout は transient transport failure として再接続する。
- 1013、1011、abnormal close、I/O exception は checkpoint があれば Resume する。
- 4001（別接続による置換）は古いプロセスが再奪取しないよう fatal とする。
- Join request 送信後、`session.joined` 保存前に切れた場合は同じ entry token で有限再試行する。
  token が既に消費済みなら次の認証拒否で `JOIN_OUTCOME_UNKNOWN` として明示終了する。
- disconnect 中も上位 inactivity や LLM timeout は終了理由にしない。単に送信しない。

### Sequence gap recovery

- 通常受信で `seq > last_seq + 1` を見たら、期待値と受信値を観測可能に記録する。
  そのイベントを適用せず socket を閉じ、最後に commit 済みの連続 `last_seq` から Resume する。
- Resume 中は保持範囲内 replay を連続順に適用する。`seq <= last_seq` は stale duplicate として
  state と checkpoint を戻さず無視する。
- replay 保持外では server が不完全 replay を送らず新しい `game.state_sync` を送る。
  Resume 中に再び欠番を見た場合は後続 incremental event を適用せず、sync timeout まで
  `game.state_sync` を待つ。その sync は authoritative recovery barrier として seq jump を許し、
  state 全体を置換して sync の seq を新しい checkpoint にする。
- sync 前に再切断したら checkpoint を進めず同じ位置から Resume する。
- `game.state_sync` request は schema に存在しないため新設しない。

### Action rejection and partial send

- `action.rejected` は state に握り潰さず、action / reason / seq を上位へ通知する。
  Network Client は自動再試行せず、current action state も推測で変更しない。
- disconnect 時、まだ send を開始していない旧 generation の command は `NotDelivered` として破棄する。
  send 開始後に接続が失われた command は server 受理の有無を判定できないため
  `DeliveryUnknown` とし、自動再送しない。
- credential の atomic save に失敗したら、それ以降のイベントを公開せず fatal 終了する。
  特に `session.joined` の token を永続化できないまま運転を続けない。
- inbound event queue が満杯なら receiver を待たせず `CONSUMER_OVERRUN` で明示終了する。
  server event を黙って捨てない。

## Concurrency

- 1 client instance は1席・1 asyncio event loop に属し、thread-safe API は提供しない。
- supervisor が socket generation と child task の所有者である。各 generation に receiver 1つ、
  sender 1つだけを作り、どちらかの終了時に他方を cancel して socket を閉じる。
- state を書くのは receiver だけ。reader は immutable snapshot の参照を取るため lock を持たない。
- 複数 Controller からの send は bounded queue と単一 sender で直列化する。
- event delivery は callback ではなく bounded async iterator とし、Brain の処理を同期実行しない。
- caller が send API の await を cancel しても、queue 受理済み command の送信取消しは保証しない。
  stale work は disconnect 時の generation invalidation と current handle 検査で遮断する。
- reconnect delay の jitter は instance ごとの乱数源を使い、module-level random を使わない。
  テストでは delay と乱数源を注入して実時間待ちを避ける。

## Resolved Design Questions and Rejected Alternatives

### 1. `seq` 欠番時の回復方式

**Decision:** 最後の連続 checkpoint から Resume する。保持内なら replay、保持外なら
Resume 後の `game.state_sync` を authoritative barrier として回復する。

- 不採用: client から `game.state_sync` を要求する。対応 request が schema に無く server 変更になる。
- 不採用: 欠番後のイベントを client で並べ替える。`seq` を順序保証へ転用し、欠落状態を正当化する。
- 不採用: 最初の欠番で永久終了する。D049 の replay / sync 回復経路を利用できない。

### 2. 接続トークンの保存責務

**Decision:** 保存媒体と場所は起動側が所有し、store として注入する。Network Client は
token / checkpoint の更新タイミングと atomicity を所有する。

- 不採用: Network Client が固定 path を決める。9プロセスの席分離と運用差し替えを壊す。
- 不採用: memory のみ。単回使用 entry token の後にプロセス再起動できない。
- 不採用: 起動側が event を監視して手動保存する。Join 応答と state 公開の間に競合が生じる。

### 3. receive / send の asyncio 構造

**Decision:** generation ごとに receiver と sender を分離し、supervisor が両方を所有する。
上位へは bounded queue と immutable snapshot で渡し、Brain を receive task から呼ばない。

- 不採用: 単一 receive/send loop。Brain または送信待ちが server event 受信を止める。
- 不採用: message ごとの無制限 task。順序、socket ownership、memory 上限が不明になる。
- 不採用: 上位 callback の直接呼出し。遅い callback が receive path に戻ってくる。

### 4. `action.rejected` の責務境界

**Decision:** Network Client は検証・観測・上位通知までを行い、自動再試行も state 推測も行わない。

- 不採用: 同じ payload を自動再送する。拒否原因が stale phase / target の場合に再拒否を繰り返す。
- 不採用: ログだけに残して吸収する。上位が方針を修正できず、完了条件の観測性も失う。

### 5. reconnect backoff と終了条件

**Decision:** 0.25秒から2倍、最大5秒、±20% jitter で再試行し、最初の切断から
60秒以内に全量 sync へ戻れなければ `RECONNECT_EXHAUSTED` で非0終了する。
全量 sync 成功時に budget を reset する。テストは policy を短縮できる。

- 不採用: 無期限再試行。無効 credential や停止済み server で zombie process になる。
- 不採用: 即時終了。一時的切断と1013の正規回復経路を使えない。
- 不採用: 固定間隔。9プロセスが同時に再接続し続ける。

## Explicitly Out of Scope

- World State / Memory の構造化、belief、イベント意味解釈（3.2）
- Brain interface、Dummy Brain の production 実装（3.3）
- 発言生成、反応頻度、CO 内容、投票・能力の選択方針（3.4 / 3.5）
- LLM backend、structured output、推論 timeout の実装（Phase 4）
- server package、schema、ゲームコア、content、protocol message type の変更
- action request の自動再試行、exactly-once 保証、server 受理 acknowledgement の新設
- credential 配布 UI、再接続 UI、観戦、リプレイ再生
- client event queue overflow 時の disk spool。Phase 3.1 は明示終了を選ぶ

## Acceptance Criteria

- `ai_client.network` が `server.aiwolf_core` と `server.network` を import せず単独起動できる。
- 初回 Join で connection token を atomic 保存し、以後の起動は同じ checkpoint で Resume する。
- strict envelope / payload validation と major version gate を state 更新前に通す。
- 4種の最新本人視点 payload と current action handles を snapshot から取得できる。
- typed send API 以外から gameplay request を送れず、ID / target は受信列挙から得る。
- receive は Brain / Controller の処理や outbound send によって停止しない。
- 欠番検出と、replay または authoritative sync による回復が event stream から観測できる。
- `action.rejected` が action / reason / seq とともに上位へ届き、自動再送されない。
- 再接続不能、major mismatch、credential failure、consumer overrun が非0終了理由として区別できる。
- 別プロセス9個が server tick だけで `GAME_ENDED` まで到達する。
- 任意の1個を終了・別PIDで再起動して同じ席へ Resume し、欠落範囲を回復して全員完走する。
- 完走 driver の行動は current `player.action_state` / sync 内 action state の列挙だけから選ぶ。
- 全テストが LLM 無しで走り、既存 Phase 2 テストを壊さない。

## Required Tests

### Protocol and state

- 全 server message type の strict validation、未知 envelope / payload field、invalid JSON。
- major mismatch は Join / Resume 完了前に fatal。同一 major の schema-compatible minor は受理。
- state sync の atomic replace、個別 player list / deaths / action state の置換、immutable snapshot。
- action state 更新・再同期ごとの generation 更新と stale handle 拒否。
- server timestamp と `phase_ends_at` による deadline notice、更新・切断時の stale timer cancel。

### Authentication and credentials

- credential 無しは Join、ありは Resume。Resume 失敗時に Join へ fallback しない。
- `session.joined` の token を最初の state 公開前に atomic 保存する。
- 各連続 event と sync recovery barrier の `last_seq` 更新を restart 後に読み戻せる。
- credential corruption / save failure は明示 fatal。token をログへ出さない。

### Sequence recovery

- live stream の欠番を期待値・受信値付きで観測し、欠番イベントを適用しない。
- replay 保持内では最後の連続 seq から Resume し、欠落イベントを回復する。
- D049 の保持上限外では不完全 replay を適用せず、state sync で snapshot と checkpoint を置換する。
- duplicate / stale seq で state と checkpoint が後退しない。
- recovery sync 前の再切断で同じ checkpoint から再試行する。

### Send and rejection

- chat channel、role ID、ability ID、target を client literal で作らず action handle から送る。
- vote abstain、ability target count、CO role の列挙一致と stale generation を検査する。
- generic raw-send API が public export に無い。
- `action.rejected` が上位へ届き、自動再試行も action state の推測変更も起きない。
- disconnect 前の未送信 command は NotDelivered、send 境界の不明なものは DeliveryUnknown となる。

### Lifecycle and concurrency

- receiver と sender が独立し、遅い Brain 相当 task と遅い send の間も receive / checkpoint が進む。
- outbound queue 上限、inbound queue 上限、consumer overrun の明示終了。
- reconnect delay の増加・上限・jitter 範囲・60秒 budget reset / exhaustion を fake clock で検証する。
- 1013 / abnormal close は Resume、4001 / 1008 / protocol error は fatal。
- stop / task cancellation で child task、timer、socket が残らない。

### Separate-process completion

- 9個すべてがテスト親とは別 PID で、server tick 以外から phase を進めず完走する。
- 1個を action / chat 中を含む決定論的な位置で停止し、同じ credential file で別PIDから Resume する。
- replay 保持内と保持外の少なくとも2ケースを分け、保持外ケースで欠番検出と sync 回復を観測する。
- 全 client が `GAME_ENDED`、再起動 client が Resume、拒否注入ケースが `ActionRejected` を記録する。
- subprocess の import 記録または import guard で `server.aiwolf_core` / `server.network` 非依存を固定する。

## Deliberately Not Decided

- internal helper の名前、dataclass の private field、ログ文言、テスト helper の分割は決めない。
- immutable payload の具体的表現（deep-frozen mapping または同等の値 object）は、外部から変更不能で
  schema 情報を失わない限り Implementer が選んでよい。
- jitter の具体的乱数アルゴリズムは、instance-local・注入可能・指定範囲内であれば決めない。
