Status: IN_REVIEW
2026-09-03: Detailed Design / Sol が REQUEST Addendum B（R-20260903-03 / 05）へ回答。
§Addendum B の A案と終了 barrier を追加し、本文の競合する記述を改訂した。Claude の再承認待ち。
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

今回の改訂範囲は末尾の Addendum B に限定する。サーバ enqueue 順の D047 への復元と、
既存 WorldState consumer の終了境界だけを例外的に含む。完走テスト検証コントラクトは改訂しない。

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
    resume_replay_capacity: int = 128
    shutdown_timeout_seconds: float = 5.0

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
2. receiver が1本ずつ JSON decode、strict schema 検証、major version / game_id 検査を行う。
   Resume の ACK 前は replay を generation-local buffer に保留し、state / checkpoint / 上位には
   渡さない。ACK 検証後に受信順で解放する（Addendum B1）。
3. sequence gate が `last_seq` と比較し、連続イベント、重複、欠番、sync recovery barrier を分ける。
4. state holder が対象 payload を immutable snapshot へ反映する。
5. checkpoint store へ token / 新しい連続 `last_seq` を atomic に保存する。
6. 保存完了後に server event と派生した client notice を event stream へ公開する。
7. `player.action_state` または `game.state_sync.action_state` から action generation と
   deadline notification を置き換える。ただし復旧中は送信と deadline 通知を開かず、
   非終端の全量 sync 完了後にのみ新しい timer を開始する。

認証直後は `session.ready` を送る（既知の終端候補がある場合を除く）。Resume 済みの席でも server 側で冪等であり、
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
SYNCHRONIZING ── 終了を検出 ─> 同期継続 ── 全量 sync commit ─> ENDED
any nonterminal ── fatal error / reconnect budget exhausted ─> FAILED
```

- `CONNECTED` になる条件は認証応答だけでなく、その socket generation の
  `game.state_sync` を受理して state を再基準化したこととする。
- 再接続を開始した時点で既存 action handles を inactive にし、送信 API を閉じる。
- 同期後に新しい action generation を発行する。古い handle は state 内容が同じでも stale。
- 通常運転の `GAME_ENDED` は checkpoint と event 公開後に正常終了する。
  復旧中の replay / sync.history 内の終了は Addendum B2 に従い、全量 sync の commit より
  前に終了しない。終端 sync では一時的にも `CONNECTED` へ移らない。
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
- Resume 中は ACK 検証後に保持範囲内 replay を連続順に適用する。要求 checkpoint 以下の
  stale duplicate は state と checkpoint を戻さず無視する。保留列内部の検査は Addendum B1 に従う。
- replay 保持外では server が不完全 replay を送らず新しい `game.state_sync` を送る。
  replay 無しの Resume ACK に欠番を見た場合は後続 incremental event を適用せず、sync timeout まで
  `game.state_sync` を待つ。その sync は authoritative recovery barrier として seq jump を許し、
  state 全体を置換して sync の seq を新しい checkpoint にする。
- sync 前に再切断したら最後に保存済みの checkpoint から Resume する。認証前の保留分と
  保持外 gap の未適用分では進めない。認証後に連続 replay / ACK を保存済みならその位置を使う。
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

- World State / Memory の構造化、belief、イベント意味解釈（3.2）。Addendum B2 の終了境界のみ例外
- Brain interface、Dummy Brain の production 実装（3.3）
- 発言生成、反応頻度、CO 内容、投票・能力の選択方針（3.4 / 3.5）
- LLM backend、structured output、推論 timeout の実装（Phase 4）
- server package（Addendum B1 の enqueue 順復元を除く）、schema、ゲームコア、content、protocol message type の変更
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
- recovery sync 前の再切断で最後の保存済み checkpoint から再試行する。
  未認証 replay と保持外 gap では checkpoint が進まない。

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

## Addendum B — Resume 配送順と終了をまたぐ復旧

### Purpose / Scope / canonical 根拠

R-20260903-03 / 05 に対し、D047 の配送順を維持して認証前公開を防ぎ、
新プロセスでも本人視点の全量 baseline を取得してから終了できるようにする。
根拠は REQUEST Addendum B、D047 / D049、spec DESIGN §9.2、PHASE3_1_HANDOFF の
認証・checkpoint 契約、World State 詳細設計の全量置換契約とする。
F007 の retained / sync-only 再現は原因が既知であり、再調査ではなく下記の回帰テストへ移す。

### Files / Modules（今回の実装差分）

| File | 変更責務 |
|---|---|
| `server/network/server.py` | 同じ dispatch lock / FIFO writer のまま enqueue を replay → ACK → sync へ戻す |
| `ai_client/network/client.py` | ACK 前保留、単一の seq gate、終端候補と sync barrier、有限 cleanup、非破壊の stream close |
| `ai_client/network/types.py` | 上記2設定と buffer 超過終了理由、`GameEnded.event` の意味の明記 |
| `ai_client/world/service.py` | raw 終了イベントによる早期 ENDED を除去し、baseline 消費後の通知で終了する |
| `tests/test_state_delivery.py`、`tests/test_network_review_regressions.py` | 実サーバの配送順・元 envelope 保持・接続置換の回帰 |
| `tests/test_phase3_1_network_client.py` | ACK gate、checkpoint、終端 barrier、timeout / cleanup の回帰 |
| `tests/test_phase3_2_world_state.py` | consumer の順序・最終 baseline・終了通知の回帰。実サーバ + 新 NetworkClient + 新 WorldState の2経路もここに置く |

`ClientState` / `WorldReducer` の既存全量置換 API を再利用する。役職・勝敗の解釈、
履歴正規化、公開情報の選別を追加せず、Network → World の一方向依存も変えない。

### B1 Decision — A案を採用する

D047 は supersede しない。サーバでは enqueue 順だけを復元し、採番・retention・
dispatch lock・writer ownership・認証と公開権限を変えない。
`_resume_ack_sequence` と「ACK の後で、それより小さい replay seq を受け入れる」分岐を除去する。

不採用の B案: ACK 先行は不要で、認証前公開はクライアントの有限 buffer で防げる。
採番と配送を逆転させるために Accepted decision と gap 検出を変更する理由がない。

#### Interfaces / state

- `resume_replay_capacity` は ACK 前に保留する envelope 件数の上限。正の整数（bool 不可）、
  既定128は D049 のサーバ既定保持数に対応する。保持数を増やす環境はクライアントも明示設定する。
  inbound event queue と別の容量であり、disk spool / 無制限の task / 自動増量はしない。
- 超過時は新しい `ClientExitReason.RESUME_BUFFER_OVERRUN`、`success=False` で終了する。
  consumer 遅延の `CONSUMER_OVERRUN` と区別する。ACK 到着を阻む buffer 空き待ちはしない。
- receiver が requested checkpoint、認証済み flag、保留 FIFO、sync 待ち、gap 状態を所有する。
  ACK 前 buffer は generation 終了時に必ず破棄し、別 socket へ持ち越さない。

#### Data Flow / authentication gate

1. Resume request を送った generation だけが ACK 前 replay を保留できる。全件を schema / major /
   game_id 検証してから保留する。Join の ACK 前に通常イベントが来た場合は従来どおり不正。
2. ACK 前は公開 snapshot の payload / last_seq / player_id、credential store、ServerEvent、
   派生通知、action / deadline を更新しない。接続 lifecycle と payload を含まない失敗通知は可。
   replay に private event や `GAME_ENDED` が含まれても例外にしない。
3. 現 generation が要求した `session.resumed`、payload.last_seq の要求値一致を確認する。
   同一 instance で既知の player_id があればその一致も確認する。新プロセスでは検証済み ACK の
   player_id を本人とし、token 以外から本人を推測しない。ACK seq は要求 checkpoint と有効保留列末尾より大きいこと。
   保留列も含めて検査を終えるまで公開しない。
4. 正常 ACK を内部で受理後、保留 replay → ACK の順に state 反映・各件保存・公開を行う。
   wire envelope / seq / event_id をそのまま使い、並べ替え・再採番はしない。
   内部の認証完了順と、上位へ公開する ServerEvent の順を混同しない。
5. `session.ready` は認証完了後に同じ sender へ一度だけ enqueue する。送信完了を receiver が
   待って後続 sync を止めない。終端候補が既知なら送信不要。通常の typed send は全量 sync まで閉じる。

ACK type 違い、payload 不一致、二重 ACK、ACK より先の `game.state_sync` は
`INVALID_SERVER_MESSAGE`。二重 ACK は stale seq 判定より先に検査し、黙って無視しない。
ACK が来ないまま待機 / transport close した場合は下記の有限 timeout / 再接続で扱う。
「ACK 欠落を拒否」は sync 到着を ACK の代用にしない意味であり、通信断を認証成功にしない。

#### Sequence / checkpoint

| 認証済み Resume の列 | 適用と checkpoint |
|---|---|
| 保持内: 要求 C の次から連続 replay、続いて ACK | ACK 検証後、連続する各件を保存してから公開。ACK も1件として保存 |
| replay 無し、ACK = C + 1 | ACK を通常 commit し、全量 sync を待つ |
| 保持外: replay 無し、ACK > C + 1 | `SequenceGapDetected(C+1, ACK.seq, generation)` と ACK を公開するが、last_seq / checkpoint は C のまま。後続 incremental は適用しない |
| 全量 sync | ACK 受理済みで、先行 ACK / 受信列より新しい sync だけを barrier として全量置換・保存・公開。保持外はここでのみ seq jump を commit |

既存の live gap の検出記録を generation をまたいで保持し、復旧 sync 後の
`SequenceGapRecovered` は未回復の gap に対して1回だけ公開する。
保留列の stale duplicate（要求 C 以下）は公開も保存もせず除外できる。
C より大きい新規 replay は連続順を要求し、途中欠番・逆順・重複、または非空 replay と ACK の
間の欠番は `INVALID_SERVER_MESSAGE` とする。D049 は保持外で部分 replay を返さないため、
この形を retention 回復として正当化しない。保持外 ACK 後の incremental は引き続き無視するが、
ACK より小さい新規 seq の「後追い replay」は不正とする。wire `[4, 3, 5]` の互換経路を残さない。

切断時は最後に保存済みの seq を使う（R-93 の各件保存を維持）。認証後の replay を途中まで
保存・公開して切断した場合、次回 sync がその時点の不完全な上位 state を必ず置き換える。
終端候補だけを理由に checkpoint を巻き戻したり、保存を省略したりしない。

#### 「単調増加」の確定した意味 / 文書反映

新規 envelope は player-local に欠番なく採番する。replay は既存 envelope の再送なので
再採番しない。保持内 Resume では要求 last_seq の次から replay → 新規 ACK → 新規 sync の順に
配送し、その socket 上の seq は増加する。保持外では未配送区間があり得るが、逆順は許さず、
全量 sync による再基準化でのみ欠番を回復する。異なるプレイヤー間の seq は比較しない。
順序の根拠はサーバの enqueue / FIFO であり、seq で並べ替えて整合したことにはしない。

承認後、Reviewer が `Docs/ai/spec/DESIGN.md` §9.2 に上記の意味を反映し、handoff の終了境界も
この契約へ整合させる。本セッションは RUNBOOK §4 に従いこの `_DESIGN.md` のみ改訂し、
canonical 本文の変更・承認・実装済みの記録を代行しない。

### B2 Decision — Network が終了を検出し、sync の後に通知する

Network は許可済み protocol fact としての終了だけを検出する。勝者・勝利条件・役職の
意味を判定しない。World は全量 baseline の構造化 commit と、その後の終了通知の消費を所有する。
Network が World の完了 ACK / callback を待つ仕組みは作らない。

不採用: replay の終了で即 close する案は sync を失う。World だけが履歴から Network を停止する案は
Network 単独利用で終了できず依存が逆転する。phase 名 / 空の action からの終了推測も行わない。

#### Interfaces / terminal fact

- `GameEnded` は既存の `event: ServerEvent` を維持する。通常運転での通知は実際の top-level
  終了イベント、復旧完了時の通知は commit 済みの実際の `game.state_sync` を event に持つ。
  後者は replay の終了を覚えていた場合も同じ。consumer は event.type を確認して参照する。
- sync.history 内の `{type: game.event, payload.event_type: GAME_ENDED}` を Network が検出する。
  history entry は envelope ではない。架空の seq / event_id / timestamp を付けた ServerEvent を
  生成せず、履歴 entry をもう一度 event stream へばらして公開することもしない。
- 終了判定は schema 検証済みの本人用 sync.history 全体に対して行う。World の履歴保持上限で
  trim された後の有無には依存しない。文字列を含む chat や別 event_type は終了根拠にならない。

#### State / Main Control Flow

| 状態・入力 | receiver の処理 | 終了可否 |
|---|---|---|
| ACK 前 replay の終了 | 未認証 buffer に置くだけ | 不可。ACK と sync の受信を続ける |
| ACK 後、sync 待ち中の有効な replay の終了 | 通常の各件 commit / 公開、認証済み終端候補を記憶 | 不可。sync 待ちを解除しない |
| 有効 sync の history に終了 | 終端候補を確定してから下記 commit 順に進む | sync の commit 後のみ可 |
| sync 受理時に認証済み終端候補あり | history 内で重複して見つかっても通知は1回 | 同上 |
| 非終端 sync | 通常どおり CONNECTED、action / deadline を開始 | 継続 |
| CONNECTED の top-level 終了 | 既に baseline があるので、当該 event の保存・公開後に通知 | 正常終了 |

認証済み終端候補は同じ client run 内の再接続でも保持する。未認証 buffer と、gap のため
適用しなかったイベントからは候補を作らない。別プロセスでは保存済み checkpoint と新しい
本人用 sync.history から回復し、terminal flag を credential file に追加しない。

復旧完了の順序は次に固定する。

1. 有効な全量 sync を読み、Network の4種 payload と self / revealed_roles / history を
   全量置換する。sync.seq の checkpoint 保存成功を確認して ServerEvent を公開する。
2. 非終端なら gap の `SequenceGapRecovered` を公開し、通常の CONNECTED に進む。
   終端なら送信を閉じて下記の有限 cleanup を完了させ、通知の容量を確認する。
3. 終端の成功 suffix を、gap があれば `SequenceGapRecovered` → `GameEnded(event=その sync)` →
   `LifecycleChanged(..., ENDED)` の順に一括公開し、stream を閉じる。
   `ClientExitReason.GAME_ENDED` / `success=True` を返す。途中の CONNECTED、action 開放、
   deadline timer 開始は行わない。
4. World は FIFO で sync を reducer に適用し、全量 baseline の snapshot/version を commit
   してから GameEnded を消費して `Freshness.ENDED` を commit する。raw replay の終了は履歴へ
   記録するだけで ENDED にしない。notice から履歴を追加しない。stream を最後まで drain して run を返す。

終端 sync が game_end の phase/day、self、公開役職、死亡・履歴を持つなら、その**受信値**が
最終 WorldSnapshot になる。client が phase を game_end に書き換えて帳尻を合わせてはならない。
Network の run 完了と World の run 完了は同時ではない。両 task の owner は結果を両方待つ。
World を使わない場合でも Network 単独で同じ理由を返せる。

#### World の終了と失敗の関係

有効 baseline と終了通知を順に消費した World は `Freshness.ENDED` /
`WorldStateExitReason.CLIENT_ENDED` になる。`Freshness.ENDED` 単独はゲーム完了の証拠ではない。
明示 stop も ENDED になり得るため、ゲーム完了の区別には Network の `ClientExitReason.GAME_ENDED`
を使う。Network の成功だけで World の reducer 成功まで保証したと解釈しない。

World は正常 commit した最新 sync の seq を内部に保持する（受理拒否でも進み得る
last_applied_seq で代用しない）。sync を指す GameEnded はその seq が一致する場合だけ終了成功として
受け入れる。拒否した sync や既存 FAILED を notice / ENDED lifecycle / source の最終 ENDED で
成功に上書きしない。必要な baseline が未確定のまま source が閉じた場合は、既存の失敗終了
（`SOURCE_CLOSED`、既に FAILED ならその失敗）を使う。これは終了成功の guard であり、
recovery sync の拒否条件・診断・その後の CONNECTED による鮮度変更は R-20260903-04 に委ねる。

公開権限は変えない。本人の replay / sync.history に終了が無い場合、他席・core・server の
終端状態を参照せず終了を宣言しない。死亡後の閲覧が無効な席などについて、見えていない
GAME_ENDED を届ける保証はしない。接続が健全で終端根拠が無い場合の明示 stop は既存 owner の責務。

### Failure Handling / Concurrency / 有限時間

- sync 前に切断したら正常終了しない。未認証 buffer は破棄、認証済み終端候補は保持し、
  最後の保存済み checkpoint から Resume する。次の有効 sync を得て初めて終了する。
  復旧できなければ `RECONNECT_EXHAUSTED`、認証拒否・schema 不正・保存失敗は既存の fatal 理由。
- `sync_timeout_seconds` は socket 接続成立後、Join / Resume 送信開始から全量 sync commit までの
  固定 deadline。handshake send、ACK、buffer 解放、sync を含み、受信ごとや終了候補検出で延長しない。
  timeout は transient。ただし checkpoint save 自体の timeout は保存結果不明なので
  `CREDENTIAL_SAVE_FAILED` として停止し、同一 run で書込みを競合させない。
- 復旧全体は `max_disconnected_seconds` で区切る。稼働中の切断検出時、または保存 checkpoint を
  使う新 run の最初の接続開始時から数え、connect / sync / backoff / 中間 cleanup を含める。
  各 await の予算を残り時間で切り詰め、ACK、replay、終端候補、失敗 generation で reset しない。
  非終端の全量 sync 成功だけが次回切断の budget を更新する。
- `shutdown_timeout_seconds` は正の有限値（bool 不可）、既定5秒。supervisor は最終 cleanup 全体に1つの
  deadline を置き、sender / deadline / connect 等の所有 task を cancel / join し socket を閉じる。
  中間 generation の cleanup はこの値と復旧 budget の残りの小さい方で区切る。
  cleanup は idempotent とし、run / generation の二重 finally で新たな待ち時間を始めない。
  close / wait_closed 待ちが期限を超えたら接続の transport を abort し、待機 task を回収する。
  成功 cleanup のふりはせず `INTERNAL_ERROR`（detail: shutdown timeout）で非成功終了する。
  終了通知の成功 suffix は cleanup 成功後にのみ確定する。設定時間はゲームの締切ではない。
- したがって復旧開始から正常終了または失敗確定までの上限は、既定で60秒 + 最終 cleanup 5秒。
  sync-only は有効 sync の commit 後に追加 server event を待たない。この時間保証は event loop が
  動き、注入した I/O が cancellation に協調する条件での transport 保証であり、OS / disk の
  停止や上位による event loop のブロックまで防ぐものではない。
- 終端処理開始で送信と timer を閉じ、未送信 / 送信結果不明を既存規則で確定する。受信状態の
  writer は引き続き1つ。未確定の全量 sync を別 task で後から commit する設計にしない。
- terminal suffix（必要な GapRecovered、GameEnded、ENDED）は容量を確認して一括公開する。
  一部の成功通知だけ出してから queue overflow で成功扱いにしない。sync の公開や suffix の
  容量が足りなければ `CONSUMER_OVERRUN` で非成功終了し、黙ってデータを落とさない。
- stream close は queue の古い要素を捨てて sentinel を入れる実装にしない。closed flag と
  wake-up を queue 容量とは独立に保持し、受理済み FIFO を drain したら iterator を終える。
  fatal 通知を置く容量も無い場合でも snapshot の FAILED と run の非成功結果を確定し、空待ちを起こす。
  Network は consumer の drain を待たない。World は先行データを drain する前に source.snapshot の
  最終 lifecycle だけを見て run を終了しない。
- stop / cancellation は保留終了より優先し、GAME_ENDED 成功に偽装しない。World の waiter は
  snapshot commit / source 終了で起床する。Network / World を途中停止させた owner の責務は変えない。

### Out of Scope（Addendum B）

- schema、ゲームコア、content、公開権限、D047 / D049 の retention と採番の変更。
- 完走検証の quorum / interruption / defect 分類、completion fixture の変更（R-20260903-02 / 06）。
- recovery sync 拒否後の CONNECTED / action gate 修正（R-20260903-04）、無関係な tooling 修正。
- World の履歴構造・意味解釈の作り直し、World から Network を停止する逆依存、将来 Phase の設計。

### Acceptance Criteria / Required Tests（Addendum B）

1. 実サーバで retained replay の元 envelope が保存され、wire 順が replay → ACK → sync、
   例では `[3, 4, 5]` になる。接続置換時も旧 writer の取消しとこの順序を両立する。
2. ACK を任意の地点で止める fake peer で、private replay / action_state / GAME_ENDED を渡しても
   state、checkpoint、上位 ServerEvent / 終了通知が変化しない。正しい ACK 後だけ元の順で公開する。
   Join 前通常イベント、sync-before-ACK、誤 ACK、重複 ACK、game_id / major 不一致は個別に拒否する。
3. 保持内連続、保持外 ACK jump、stale duplicate、非空 replay 欠番、逆順、旧 `[4,3,5]` を分けて検証。
   `_resume_ack_sequence` の互換特例が無く、保存回数 / GapDetected / GapRecovered が上記表に一致する。
4. buffer 上限ちょうどは受理、超過は `RESUME_BUFFER_OVERRUN`。上限変更も検証し、
   consumer が遅い場合の inbound overflow と区別する。ACK 待ちによる buffer deadlock を起こさない。
5. F007 と同じ2経路を実サーバで作る。保持内は終了を含む未読配送を残し、sync-only は終了前に
   切断して、どちらも保存 credential から**新 NetworkClient + 新 WorldState** を起動する。
   両 run を test timeout 内で待ち、Network GAME_ENDED / success、World CLIENT_ENDED / ENDED、
   最終 self / phase / day / players / deaths / revealed_roles と履歴が実際の本人用 sync に基づくことを検証。
   最後の last_seq / checkpoint / world.last_applied_seq は復旧 sync.seq と一致し、通知は1回。
6. fake peer で replay 終了後の ACK 前、ACK 後 sync 前、途中保存後の各切断を作り、早期終了無しと
   正しい checkpoint での再試行を検証。次回 sync で正常終了する場合と、期限切れで非成功の場合を分ける。
7. sync.history のみに終了がある場合、replay と history の両方にある場合、World の履歴 trim で
   終了 record が残らない場合でも通知は1回。許可履歴に終了が無い場合、phase / chat 文言 / 空の
   actions から終了を推測しない。通常 CONNECTED の top-level 終了も従来どおり終了する。
8. World は raw replay 終了だけでは ENDED にならず、sync commit 後の通知で ENDED になる。
   遅い consumer に対して Network が先に終了しても全量 sync が drain される。GameEnded の参照は
   実 sync envelope であり、notice による履歴二重追加がない。sync 拒否 / reducer 失敗を終了成功で上書きしない。
9. ACK 無応答、sync 無応答、途切れない replay、handshake send 停滞、cleanup 停滞、checkpoint
   保存失敗、queue が閉鎖時ちょうど満杯、stop / cancel を検証する。期限の延長や reset、sync の
   sentinel による脱落、子 task / socket / waiter の放置、失敗経路の GAME_ENDED 成功がない。
10. 上記は LLM 無しの境界回帰とする。実サーバ試験の core 操作で終了状態を用意してもよいが、
    ROADMAP の「server tick のみで9プロセス完走」の証拠とは数えない。完走検証は別設計の責務。

設計承認後の実装時には上記4つの既存テストファイル群と全 suite を実行し、
`python scripts/check_docs.py` を通す。本改訂は設計のみで、回帰テストの追加・実装はまだ行っていない。
