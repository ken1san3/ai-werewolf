Status: APPROVED — Reviewer / Claude 承認（2026-09-04）。依頼書の4点すべてに採らなかった案付きで答えており、ROADMAP §3.3 の完了条件5項目が Acceptance 1〜14 へ対応している。Reviewer が実コードで確認した点: `WorldSnapshot.complete` は property として実在し `not (unknown_event_count or malformed_event_count)` を返す。設計が「履歴 view が incomplete でも snapshot が complete なら呼ぶ」と分けているのは正しい。`CurrentActionsView` は `world_version` / `world_last_applied_seq` / `network_last_seq` / `is_caught_up` / `actions` を実際に持つ。5つの Decision の field は `send_chat` / `send_vote` / `send_ability` / `send_co_declare` / `send_co_report` の実シグネチャと一致する。**Brain は `option_id` しか返せず handle を作れない**（承認されない条件の2番目を構造で塞いでいる）。Brain は専用 task で、World / Network の ingest を止めない。Phase 4 の LLM adapter が同じ protocol へ入ることも示されている。
2026-09-04 承認条件（実装と同じ変更に含めること）: (1) 新設する `tests/test_phase3_3_completion.py` を `tests/conftest.py` の `_COMPLETION_TESTS` へ node 単位で登録する。登録しないと9プロセスの完走テストが `-m "not completion"` の高速側に残る（C7 の分類方針に反する）。(2) 設計の「変更しない」に挙がっている `shared/` はリポジトリに存在しない。実装時に新設しないこと。

# Phase 3.3 Brain Interface / Dummy 詳細設計

## Purpose

`WorldState` が保持するクライアント視点の状態と、`NetworkClient` の型付き送信 API の間に、
差し替え可能な非同期判断境界を一つ設ける。Phase 3.3 では外部 LLM を使わない
`DummyBrain` を同じ境界へ接続し、9 クライアントがゲーム終了まで進めることを確認する。

この境界は、Phase 4 の LLM 実装が `WorldState`、機械的な送信処理、または既存の
Network / World 公開 API を変更せず差し替えられる形にする。

## Scope

今回実装する範囲は次に限定する。

- `WorldState` の承認済み read API だけから、一回の Brain 呼び出しに渡す immutable な
  `BrainInput` を構成する。
- `WorldSnapshot.version` と、その入力に列挙した action handle がどの
  `CurrentActionsView` に由来するかを明示する。
- 非同期 `Brain` protocol、構造化された決定型、決定の検証、元の action handle への解決、
  および型付き Network send への機械的 dispatch を定義する。
- Phase 3.3 の基準動作として、各ゲーム phase につき高々一回 Brain を呼ぶ coordinator を
  定義する。
- timeout、cancellation、例外、stale input、不正な Brain 出力に対する「送信しない」既定動作を
  定義する。
- seed を受け取る決定的かつ LLM 非依存の `DummyBrain` を実装対象にする。
- Brain の差し替え、非 blocking 性、不正出力の遮断、および 9 process 完走を検証する。

## Files / Modules

### 新規

- `ai_client/brain/__init__.py`
  - Phase 3.3 の公開型、`Brain`、`DummyBrain`、`BrainController`、
    `PhaseBrainCoordinator` を再 export する。
- `ai_client/brain/model.py`
  - immutable な入力、action option、決定 union、実行結果、設定値を定義する。
- `ai_client/brain/interface.py`
  - 差し替え境界である `Brain` protocol を定義する。
- `ai_client/brain/dummy.py`
  - seed を明示的に受け取る `DummyBrain` を定義する。外部サービスや LLM は使わない。
- `ai_client/brain/controller.py`
  - 一回分の入力構築、Brain task の deadline 管理、出力検証、stale 再検証、型付き send の
    dispatch、および結果の返却を担う。
- `ai_client/brain/coordinator.py`
  - `WorldState.wait_for_update()` を利用し、Phase 3.3 の「phase ごとに高々一回」という
    invocation policy を担う。Network の受信ループや World の reducer loop は所有しない。
- `tests/test_phase3_3_brain_interface.py`
  - interface、入力整合性、dispatch、不正出力、deadline、cancellation、差し替え、決定性を
    process 内で検証する。
- `tests/fixtures/phase3_3_brain_client_process.py`
  - completion test の各クライアント process で Network、World、Dummy Brain を合成する。
- `tests/test_phase3_3_completion.py`
  - 実 server と 9 個の独立クライアント process による完走を検証する。

### 変更しない

- `ai_client/network/`
- `ai_client/world/`
- `server/`
- `shared/`
- `content/`

既存 API に Phase 3.3 の都合を持ち込まず、Brain package が既存の型を import して合成する。

## Responsibilities

### `WorldState`

- Network event の唯一の consumer であり続ける。
- `snapshot()`、`current_actions()`、`history()`、`co_for_day()`、
  `ability_results()`、`wait_for_update()` を介して読み取り値を返す。
- Brain を import、生成、呼び出し、または待機しない。

### `Brain`

- 渡された immutable な `BrainInput` 一個から、構造化された `BrainDecision` 一個を非同期に
 返す。
- `WorldState` や `NetworkClient` 自体を保持または呼び出さない。
- action handle を生成しない。出力では入力内の request-local な `option_id` だけを参照する。
- server legality を再計算しない。
- Network send を行わない。
- cancellation を協調的に処理し、cancel 後に送信などの副作用を残さない。

### `DummyBrain`

- Phase 3.3 では常に `NoDecision` を返す。
- seed は constructor で必須入力として保持する。同一 seed と同一入力に対する結果は必ず同一に
 する。
- module-level `random`、時刻、process ID、到着順以外の隠れた状態、外部 I/O を使わない。
- 発言文、投票先、能力対象、CO 内容を仮実装しない。これらを選ばないこと自体が Phase 3.3 の
 既定動作である。

### `BrainController`

- 一回の invocation の orchestration を担う。
- World の read API を、途中に `await` を挟まず読み、一貫した `BrainInput` を構成する。
- Brain task を Network receiver / World reducer と別 task として実行し、timeout と cancellation を
 所有する。
- Brain 出力の `option_id` と決定種別を検証し、入力に実在した元の handle だけへ解決する。
- send 直前に `current_actions()` を再取得し、元の handle が現在も列挙されている場合に限り、
 `NetworkClient` の対応する型付き send API を呼ぶ。
- 結果を `DecisionOutcome` として呼び出し元へ返す。自動 retry や別 action への fallback はしない。
- role、team、channel、cause、ability の意味解釈や選択方針を持たない。

### `PhaseBrainCoordinator`

- Phase 3.3 の基準 invocation policy だけを所有する。
- `WorldState.wait_for_update()` で version 更新を待ち、利用可能な最初の CURRENT snapshot を各
  phase 一回だけ `BrainController` へ渡す。
- Network / World の `run()` task を生成または停止しない。process composition root が lifecycle を
 所有する。
- 同一 phase 中の chat、action-state refresh、同期再生による World version 更新では Brain を
 再度呼ばない。
- 後続 phase が固有の発話間隔や行動 timing を導入するときは、この基準 invocation policy を
 対象 controller で置き換える。ただし `Brain` protocol と `BrainController` の一回分の境界は
 変更しない。

### process composition root

- `NetworkClient`、`WorldState`、選択した `Brain` instance、`BrainController`、
  `PhaseBrainCoordinator` を constructor injection で接続する。
- Network、World、coordinator の task を並行起動し、終了時に停止順序を制御する。
- production server package をクライアント process に import しない。

## Public Interfaces

以下は必要な公開契約を示す型レベルの signature であり、実装手順ではない。

```python
class Brain(Protocol):
    async def decide(self, request: BrainInput) -> BrainDecision: ...


@dataclass(frozen=True)
class BrainInput:
    snapshot: WorldSnapshot
    action_context: BrainActionContext
    history: HistoryView
    co: CoView
    ability_results: AbilityResultView


@dataclass(frozen=True)
class BrainActionContext:
    world_version: int
    world_last_applied_seq: int
    network_last_seq: int
    is_caught_up: bool
    options: tuple[BrainActionOption, ...]


@dataclass(frozen=True)
class BrainActionOption:
    option_id: str
    handle: ActionHandle
```

`BrainActionContext` の先頭四フィールドは、入力構築時に取得した
`CurrentActionsView` の同名フィールドを写した値である。各 option の `handle` は同 view の
`actions` に入っていた object そのものであり、copy、再構成、世代番号の置換をしない。

`option_id` は `CurrentActionsView.actions` の順序から `action:0`、`action:1`、…の形で生成する。
これは一個の `BrainInput` 内だけで有効な opaque identifier であり、server action ID、role ID、
または protocol field ではない。Brain はこの文字列を解析してはならない。

`BrainDecision` は次の closed union とする。

```python
BrainDecision = (
    NoDecision
    | ChatDecision
    | VoteDecision
    | AbilityDecision
    | CoDeclareDecision
    | CoReportDecision
)

@dataclass(frozen=True)
class NoDecision:
    pass

@dataclass(frozen=True)
class ChatDecision:
    option_id: str
    message: str

@dataclass(frozen=True)
class VoteDecision:
    option_id: str
    target_player_id: str | None

@dataclass(frozen=True)
class AbilityDecision:
    option_id: str
    target_player_ids: tuple[str, ...]

@dataclass(frozen=True)
class CoDeclareDecision:
    option_id: str
    claimed_role_id: str
    comment: str

@dataclass(frozen=True)
class CoReportDecision:
    option_id: str
    kind: str
    target_player_id: str
    claimed_result: str
```

各 field は既存 Network send API の引数へ対応するが、この定義は値をどう選ぶかを規定しない。
選択方針は Phase 3.4 / 3.5、LLM による内容生成は Phase 4 の範囲である。

Controller は decision subtype と元 handle subtype の対応を次のように固定する。

| Decision | 必須の元 handle | 呼び出す API |
|---|---|---|
| `ChatDecision` | `ChatAction` | `send_chat(handle, message)` |
| `VoteDecision` | `VoteAction` | `send_vote(handle, target_player_id)` |
| `AbilityDecision` | `AbilityAction` | `send_ability(handle, target_player_ids)` |
| `CoDeclareDecision` | `CoDeclareAction` | `send_co_declare(handle, claimed_role_id, comment)` |
| `CoReportDecision` | `CoReportAction` | `send_co_report(handle, kind, target_player_id, claimed_result)` |

`NoDecision` は Network API を一切呼ばない。

Controller の公開契約は次のとおりとする。

```python
@dataclass(frozen=True)
class BrainRunConfig:
    max_decision_seconds: float = 5.0
    cancellation_grace_seconds: float = 0.25


class BrainController:
    def __init__(
        self,
        *,
        world: WorldState,
        sender: NetworkClient,
        brain: Brain,
        config: BrainRunConfig = BrainRunConfig(),
    ) -> None: ...

    def capture_input(self) -> BrainInput | None: ...

    async def decide_and_send(
        self,
        request: BrainInput,
        *,
        timeout_seconds: float | None = None,
    ) -> DecisionOutcome: ...

    async def stop(self) -> None: ...


class PhaseBrainCoordinator:
    def __init__(self, *, world: WorldState, controller: BrainController) -> None: ...

    async def run(self) -> CoordinatorExit: ...
    async def stop(self) -> None: ...
```

`timeout_seconds` を省略した場合は `max_decision_seconds` を使う。指定値は正の有限値かつ
`max_decision_seconds` 以下でなければならず、不正値は呼び出し前の programmer error として
`ValueError` にする。

`DecisionOutcome` は少なくとも次の status を区別する。

- `NO_DECISION`
- `SENT`
- `INVALID_DECISION`
- `TIMED_OUT`
- `BRAIN_FAILED`
- `STALE`
- `CANCELLED`
- `SEND_NOT_DELIVERED`
- `SEND_DELIVERY_UNKNOWN`

`SENT` は既存 Network 契約と同じく `SendReceipt` が返ったこと、すなわち socket send 完了までを
示し、server が action を受理した証明ではない。送信した場合は既存の `SendReceipt` を outcome に
保持する。`NotDeliveredError` と `DeliveryUnknownError` はそれぞれ対応する failure status に写す。
例外の raw message、prompt、secret を outcome に格納せず、安定した status と例外型名までに留める。

## Main Design Decisions

### 1. Brain input と snapshot / action version の関係

Brain には `WorldState` object ではなく、一回の同期 capture で得た immutable value を渡す。
入力を構成できるのは次をすべて満たす場合だけである。

1. `snapshot.freshness is CURRENT`。
2. `snapshot.is_caught_up is True`。
3. `snapshot.phase` が存在する。
4. `snapshot.complete is True`。
5. `current_actions.is_caught_up is True`。
6. `current_actions.world_version == snapshot.version`。
7. `current_actions.world_last_applied_seq == snapshot.last_applied_seq`。
8. `current_actions.network_last_seq == snapshot.last_applied_seq`。

`history()`、当日の `co_for_day(day)`、`ability_results()` も同じ synchronous capture 内で取得する。
各 read API の間に `await` を挟まないため、World reducer task は途中で割り込まない。
`HistoryView`、`CoView`、`AbilityResultView` の `complete` / retention metadata はそのまま入力へ渡し、
Brain が完全な履歴だと推測しないようにする。履歴 retention により query view が incomplete でも
snapshot 自体が complete なら呼び出しは許可する。

条件を満たさない場合は Brain を呼ばず、次の World version を待つ。空の action tuple は正当な
CURRENT view であり、空 options の入力で Brain を呼ぶことを許す。

却下した代案:

- `WorldState` を Brain に渡して都度参照させる案は、一回の判断中に version が混在し、Brain が
  Network / World lifecycle を所有できてしまうため採用しない。
- snapshot と action handle を独立に渡して対応 version を省略する案は、同期遅延時に別世代の
  handle を判断対象にできるため採用しない。
- Brain が action handle を返す案は、将来の LLM adapter が handle を捏造できるため採用しない。

### 2. 非同期性、deadline、既定動作

Brain 呼び出しは必ず専用 asyncio task とし、`WorldState.run()` と
`NetworkClient.run()` の task から待たない。`BrainController` は Brain task、timeout timer、
World の更新通知を競合させ、結果が得られるまでにも World / Network の ingest を継続させる。

deadline の起点は一貫した `BrainInput` の capture が完了し、Brain task を生成した時点とする。
Phase 3.3 の hard ceiling は既定 5 秒である。後続 controller は自身の policy に基づく、より短い
正の budget を一回の呼び出しに指定できるが、hard ceiling を延長できない。

`WorldSnapshot.phase.phase_ends_at` は BrainInput に含まれる snapshot metadata として渡す。ただし
承認済み World API は server deadline を client monotonic deadline へ変換した値を公開していない。
したがって Phase 3.3 は `phase_ends_at` と local clock の直接比較を安全性根拠にしない。hard ceiling
は推論資源の上限であり、phase deadline 内の送信保証ではない。phase 遷移・disconnect・action
generation 更新は後述の stale 再検証で遮断し、最終的な期限判定は既存の Network / server 契約へ
委ねる。Phase 固有の送信余裕時間は Phase 3.4 / 3.5 の設計範囲である。

timeout、Brain 例外、cancellation、stale、不正出力の既定結果はすべて「何も送らない」とする。
投票・能力の代替 target、固定 chat、synthetic abstain は生成しない。server 側の既存 no-selection
規則がゲーム進行を担う。同一 phase で自動 retry しない。

却下した代案:

- Brain を World event consumer 内で直接 await する案は、受信と reducer を推論 latency の間
  止めるため採用しない。
- `phase_ends_at` をそのまま local monotonic clock と比較する案は、公開 API が clock-domain の
  同一性を保証していないため採用しない。
- timeout 時に最初の target や固定発言を送る案は、Phase 3.4 / 3.5 の policy を先取りし、server の
  列挙を超えた意味判断を持ち込むため採用しない。

### 3. Brain invocation の単位と回数

Phase 3.3 の `PhaseBrainCoordinator` は `PhaseKey(day, phase)` ごとに高々一回呼ぶ。trigger は、その
phase について前述の capture 条件を初めて満たした World version である。試行済み flag は Brain
task の開始前に立てる。

- 同一 phase 中に World version が更新されても再呼び出ししない。
- timeout、例外、`NoDecision`、不正出力、send failure の後も同じ phase では再試行しない。
- Brain 開始前に STALE になった場合は試行済みにせず、同一 phase が CURRENT に戻れば一回だけ
  呼べる。
- Brain 開始後に disconnect または phase 遷移した場合はその invocation を cancel / stale とし、
  reconnect 後の同一 phase では再試行しない。
- 次の `PhaseKey` では新しい一回を許す。

この一回/phase は Phase 3.3 の接続・lifecycle を検証する基準 policy であり、発言 timing、chat
反応、vote / ability の再試行 policy ではない。Phase 3.4 / 3.5 が固有 trigger を設計するときも、
一回分の `BrainInput -> BrainDecision -> validation` 境界を再利用する。

却下した代案:

- World version ごとに呼ぶ案は、chat traffic や同期 replay の量に推論回数が比例し、重複送信と
  stale work を生むため採用しない。
- action type ごとの scheduling を Phase 3.3 で決める案は、Phase 3.4 / 3.5 の責務を先取りするため
  採用しない。
- timeout 後に同じ phase で繰り返す案は、期限付近の task 増殖と予測不能な負荷を生むため採用
  しない。

### 4. Brain の差し替え単位

差し替え単位は process 起動時に `BrainController` constructor へ渡す `Brain` instance 一個とする。
`DummyBrain`、test fake、将来の `LLMBrain` はすべて同じ `async decide(BrainInput)` protocol と
`BrainDecision` union を使う。World、Controller、Network のコードは実装種別を判定しない。

一つの game 中の hot swap は行わない。未完了 task、seed state、timeout ownership を跨ぐ必要が
ある場合は、process composition root が現在の coordinator を停止してから新しい instance を持つ
新 lifecycle を開始しなければならないが、その運用自体は Phase 3.3 の対象外とする。

却下した代案:

- `if dummy` / `if llm` を Controller に置く案は、Phase 4 差し替え時に Controller 変更が必要に
  なるため採用しない。
- role ごとの Brain class や Brain instance を作る案は、role hardcode と一 agent 一 model を招く
  ため採用しない。
- invocation 中の hot swap は、どの実装が task と cancellation を所有するか不明になるため採用
  しない。

## Data Flow

1. `WorldState.run()` は Network event を継続して reducer へ適用する。
2. `PhaseBrainCoordinator` は `wait_for_update(after_version)` で新しい snapshot を受け取る。
3. 未試行の `PhaseKey` であれば、coordinator は `snapshot()` と `current_actions()` および必要な
   query read を `await` なしで取得する。
4. version / seq / freshness 条件を検査し、`CurrentActionsView.actions` の各 handle に request-local
   `option_id` を割り当てた `BrainInput` を構成する。
5. `BrainController` は Brain task と timeout / lifecycle watcher を開始する。Network receiver と
   World reducer は並行して動き続ける。
6. `NoDecision` なら `NO_DECISION` を返し、Network API は呼ばない。
7. action decision なら option map から元 handle を引き、decision subtype と handle subtype、および
   handle が列挙した値の範囲を検証する。
8. Controller は `current_actions()` を再取得し、同じ handle が現在も列挙され、CURRENT / caught-up
   で同じ `PhaseKey` であることを確認する。
9. 条件を満たす場合だけ、元 handle と Brain が返した payload を対応する型付き send API へ渡す。
10. send receipt または failure classification を `DecisionOutcome` として返す。Controller は server
    acceptance を推測せず、自動 retry しない。

## State / Lifecycle

`PhaseBrainCoordinator` の lifecycle は次の状態を持つ。

- `CREATED`: 未起動。
- `RUNNING`: World update を監視可能。
- `STOPPING`: 新規 invocation を禁止し、所有する Brain task を cancel 中。
- `STOPPED`: task がなく再起動不可。
- `FAILED`: 内部契約違反により Brain 側を停止済み。Network / World task は process owner が停止する
  まで継続可能。

一回の invocation は次の状態を持つ。

- `CAPTURING`
- `RUNNING`
- `VALIDATING`
- `DISPATCHING`
- terminal: `NO_DECISION` / `SENT` / `INVALID_DECISION` / `TIMED_OUT` /
  `BRAIN_FAILED` / `STALE` / `CANCELLED` / send failure

terminal state から別 decision への fallback はない。

World が `ENDED` になった場合、active Brain task を cancel して coordinator は正常終了する。
`FAILED` の場合も送信せず active task を cancel し、failure exit を返す。`STALE` は disconnect 中の
一時状態として待機するが、active invocation があれば stale にして送信を禁止する。

## Main Control Flow

### 入力 capture

1. snapshot が CURRENT / caught-up / complete か検査する。
2. `PhaseKey` が未試行か検査する。
3. `current_actions()` を取得し、snapshot version / seq との一致を検査する。
4. query view を取得し、`BrainInput` を freeze する。
5. `PhaseKey` を試行済みにして Brain task を開始する。

### 結果 validation

1. object が `BrainDecision` union の既知 subtype か検査する。
2. `NoDecision` 以外では `option_id` が入力 map に一意に存在するか検査する。
3. decision subtype と元 handle subtype が上表どおり一致するか検査する。
4. target、targets、claimed role が、元 handle が列挙する許可値と一致するか検査する。
5. `target_count`、重複 target、`allows_abstain`、空であってはならない文字列など、既存 typed send
   contract に直接対応する構造条件を検査する。
6. 未知の role / ability / channel / cause の意味を解釈せず、列挙値との membership だけを見る。
7. 一項目でも不正なら `INVALID_DECISION` とし、send method を一つも呼ばない。

この validation は server legality の再実装ではない。受信済み handle が明示する選択肢へ Brain
出力を束縛し、Network の型付き send API を安全に呼べる形へ変換するだけである。server は最終的な
合法性と期限を引き続き判定する。

### send 前 stale 検査

1. 最新 snapshot と `current_actions()` を再取得する。
2. CURRENT / caught-up でない、phase が違う、または元 handle と完全一致する handle が現在の
   actions に存在しなければ `STALE` とする。
3. World version の増加だけでは自動的に stale としない。同一 handle がなお列挙されている場合は
   判断元 version を outcome に残したうえで送信可能とする。
4. Network send が最終的に stale generation を検出した場合は、その既存結果を send failure として
   返し、retry しない。

## Failure Handling

### invalid input

- CURRENT / caught-up / version / seq 条件を満たさない capture は Brain を呼ばず、次の World update
  を待つ。
- `snapshot.complete is False` は利用不能とし、その version では呼ばない。
- query retention による incomplete は metadata を保持して呼び出し可能とする。
- public config の NaN、無限、0 以下、hard ceiling 超過は `ValueError` とする。

### invalid Brain output

- 未知 subtype、未知 `option_id`、decision / handle 種別不一致、列挙外 target / role、個数不一致、
  重複 target、禁止された abstain、不正な文字列は `INVALID_DECISION` とする。
- 元 handle を似た field から作り直したり、最初の option に置換したりしない。
- Network send API は一つも呼ばない。結果は `DecisionOutcome` で観測可能にする。

### timeout

- hard ceiling または短い per-call budget に達した時点で Brain task を cancel し、`TIMED_OUT` を
  返す。
- 既定動作は no send、同じ phase の retry なしである。
- cancellation grace を超えて cancel に応答しない Brain は unresponsive とみなし、その task の
  結果を永久に無効化し、同 process で新しい Brain invocation を開始しない。World / Network の
  ingest とゲーム終了待ちは継続する。

### disconnect / stale work

- invocation 中に freshness が STALE、FAILED、ENDED になるか phase が変わった場合、その結果は
  送信しない。
- reconnect で同じ phase が戻っても、開始済みだった phase は再試行しない。
- Brain task が cancellation 後に値を返しても、invocation token が terminal なら捨てる。

### Brain exception

- `CancelledError` は lifecycle に応じ `CANCELLED` または `STALE` として扱う。
- その他の例外は `BRAIN_FAILED` とし、Network / World task へ伝播させない。
- 例外後の既定動作は no send、同じ phase の retry なし。次 phase では、Brain が cancellation に
  応答済みで controller が RUNNING なら再び一回呼べる。

### send failure / partial failure

- `NOT_DELIVERED` と `DELIVERY_UNKNOWN` を区別して outcome に写す。
- 自動 resend しない。`DELIVERY_UNKNOWN` を「未送信」と推測しない。
- `SENT` 後の server acceptance / rejection は既存 Network event の責務であり、Phase 3.3 は同期的な
  成功へ読み替えない。
- 一個の decision は一個の send method にだけ対応し、複数送信の partial commit を作らない。

## Concurrency

- `NetworkClient.run()`、`WorldState.run()`、`PhaseBrainCoordinator.run()` は sibling task として process
  composition root が所有する。
- World event の consumer は `WorldState` 一つだけであり、Brain / coordinator は Network event
  iterator を読まない。
- 一クライアントにつき active Brain invocation は最大一個とする。queue は設けない。
- 同一 phase の更新は coalesce し、pending Brain request を積まない。
- Brain の待機中も World update watcher を動かし、phase change / terminal freshness を検出する。
- `stop()` は冪等とし、新規 invocation を閉じてから active Brain task を cancel する。
- lock は追加しない。World の read API を同じ event loop で `await` なしに capture し、version / seq
  検査と immutable value によって整合性を担保する。
- cancellation 後または timeout 後に完了した stale result は、世代 token を照合して必ず破棄する。
- Brain 実装は blocking I/O や CPU-heavy 推論を event loop 上で直接実行してはならない。将来の
  LLM adapter は async I/O または自身が所有する cancel 可能な worker 境界を用いる。

## Explicitly Out of Scope

- Phase 3.4 の発言間隔、phase ごとの発言上限、chat への反応、CO trigger、発言 rejection policy。
- Phase 3.5 の vote / runoff / abstain、ability target、deadline margin、action retry の選択方針。
- Phase 4 の LLM backend、model process、prompt、schema repair、自然言語生成、structured-output retry。
- Phase 6 の Belief / Suspicion / Evidence / Credibility、推論品質、発言品質。
- role、team、alignment、ability、channel、cause の意味表や role 固有分岐。
- Network / World API、server、protocol schema、content YAML の変更。
- 複数 Brain の ensemble、game 中 hot swap、優先度 queue、batching。
- Phase 3.1 の completion evidence contract の変更や再設計。
- Phase 5 の shared LLM queue、GPU resource management、backpressure policy。

## Acceptance Criteria

1. `ai_client.brain` の production code が `server.aiwolf_core` と `server.network` を import しない。
2. `ai_client/network/`、`ai_client/world/`、server、schema、content に Phase 3.3 の変更がない。
3. Brain は `BrainInput` の immutable value だけを受け取り、World / Network object や raw protocol
   payload を受け取らない。
4. 各 `BrainInput` に `WorldSnapshot.version` と対応する World / Network seq が保持され、列挙 action
   が同じ `CurrentActionsView` に由来することを検査できる。
5. Brain 出力は request-local `option_id` を参照し、action handle を生成または返せない。
6. 列挙にない option、種別不一致、列挙外 payload を返した Brain に対し、Controller は send API を
   呼ばず `INVALID_DECISION` を返す。
7. timeout、exception、disconnect、phase change、stale handle のいずれでも fallback action を送ら
   ない。
8. Brain の待機中も Network receive と World reducer の version 更新が進む。
9. Phase 3.3 coordinator は各 `PhaseKey` につき高々一回だけ Brain を呼び、同一 phase の update、
   timeout、reconnect で自動 retry しない。
10. `DummyBrain` は外部 LLM、外部 I/O、module-level random を使わず、同一 seed と同一入力で常に
    同一結果を返す。
11. constructor に渡す Brain instance だけを `DummyBrain` から別の protocol 適合 Brain へ替え、
    `WorldState` と `BrainController` を変更せず同じ入力・検証・dispatch 経路を通せる。
12. 9 個の独立 client process がそれぞれ Network、World、Dummy Brain を合成し、server の既存
    no-selection 規則の下で全 process が `GAME_ENDED` まで到達する。
13. completion 実行中、クライアント process に LLM server / model process が存在せず、server
    package import guard に違反しない。
14. `python scripts/check_docs.py` が成功する。

## Required Tests

### 型・入力 contract

- `BrainInput`、option、decision、outcome が frozen / immutable である。
- snapshot version と `CurrentActionsView.world_version` が一致しない場合は Brain を呼ばない。
- world / network seq が一致しない場合、STALE / FAILED / ENDED / incomplete snapshot の場合は Brain
  を呼ばない。
- empty actions でも CURRENT / caught-up なら空 options の入力を一回構成できる。
- option ID が入力内で一意かつ順序に対し決定的で、元 handle object を保持する。
- history / CO / ability result の completeness と retention metadata が失われない。

### decision validation / dispatch

- 各 decision subtype が対応する handle と組になったときだけ、対応する send method を一回呼ぶ。
- `NoDecision` は send method を呼ばない。
- 未知 option ID、未知 object、handle subtype 不一致、列挙外 target / claimed role、target 数不一致、
  duplicate target、不許可 abstain をそれぞれ `INVALID_DECISION` とし、全 send spy が 0 call である。
- validation 後に action generation、connection generation、phase が変わった場合は `STALE` となり、
  send が 0 call である。
- 同一 World version で Network が先行した `current_actions()` の空転化を stale として扱う。
- World version が増えても、同一 phase かつ元 handle が現在も列挙される場合だけ送信可能である。
- `SendReceipt`、`NotDeliveredError`、`DeliveryUnknownError` がそれぞれ `SENT`、
  `SEND_NOT_DELIVERED`、`SEND_DELIVERY_UNKNOWN` に写り、自動 retry されない。

### async / lifecycle

- 未完了 Brain を待っている間に Network event を追加し、World version が進むことを確認する。
- timeout で所定時間内に no-send outcome が返り、同一 phase の再 invocation がない。
- Brain 例外が Network / World task を終了させず、no-send outcome になる。
- phase transition、disconnect、World ENDED、World FAILED、明示 stop が active Brain task を無効化
  する。
- cancellation を無視する test Brain が結果を返しても送信されず、新しい invocation が増殖しない。
- `run()` / `stop()` の二重呼び出し lifecycle 契約を検証する。

### invocation policy

- 同一 day / phase で複数 World version が来ても一回だけ呼ぶ。
- CURRENT になる前の STALE update は call count に含めない。
- invocation 開始後の timeout / exception / reconnect で同一 phase を再試行しない。
- day または phase が変われば次の一回を許す。
- empty options の phase も一回として記録する。

### replacement / determinism

- 同じ `BrainController` と World fake に `DummyBrain` と scripted async Brain をそれぞれ constructor
  injection し、Controller code を変えずに動作する。
- 同一 seed と同一 `BrainInput` の複数 instance / 複数 process で `DummyBrain` の結果が一致する。
- module-level `random`、wall clock、LLM client が呼ばれないことを spy / import guard で確認する。

### 9-process completion

- 実 server 一個と、独立した 9 client process を起動する。
- 各 process は production `NetworkClient`、production `WorldState`、production `DummyBrain`、
  production Brain controller / coordinator を使う。
- 各 phase の Brain call count が高々一回であること、Dummy がすべて `NoDecision` であることを
  process 結果から検証する。
- server の no-selection handling によりゲームが進み、9 process 全てが `GAME_ENDED` を観測して
  timeout 内に clean exit することを確認する。
- クライアント process の server package import guard、LLM 非起動、process isolation、異常終了時の
  cleanup を検証する。
