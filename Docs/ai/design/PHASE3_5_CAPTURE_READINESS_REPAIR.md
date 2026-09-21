# Phase 3.5 Capture Readiness 限定修正設計

Task: T483 design / T481 implementation / T482 independent review
Status: DRAFT

## 1. 問題と修正範囲

承認済みcatch-up契約では、admission offer前はcontext coherenceだけを確認し、`BrainInput`と`DiscussionState.capture()`を伴う完全captureはOFFERED後に一回だけ行う。現実装はoffer前とOFFERED後の双方で`_capture_with_one_catchup()`を呼び、discussion capture instrumentationを二回進める。既存`captures_once`契約に反する。

またlegacy `capture_input()`の早期STALE経路が、STALE返却に不要な`snapshot.version`を読むため、最小snapshot fixtureと従来のreservation probeを壊す。この二点だけを直し、deadline、cancel/stop、replacement、poison、lease ownership、最大一回wait、完全capture後のBrain/wire契約は変更しない。

## 2. pure readiness interface

`ai_client.brain.controller`へ副作用のない次のpublic値/APIを追加する。

```python
@dataclass(frozen=True)
class CaptureReadiness:
    status: Literal["READY", "NETWORK_AHEAD", "STALE"]
    after_world_version: int | None

BrainController.capture_readiness(
    *,
    allowed_handles: tuple[ActionHandle, ...],
    dispatch_deadline: DispatchDeadline,
) -> CaptureReadiness
```

組合せは`NETWORK_AHEAD`だけが非負`after_world_version`を持ち、`READY`/`STALE`は必ず`None`とする。unknown status、bool version、負値、その他の組合せをconstructorで拒否する。

readinessは`snapshot()`と`current_actions()`、allowed handles、deadline、および既存`_deadline_allows_dispatch()`がauthority確認に使うread-only `world.transport_observations().current_deadline`だけを読む。このcurrent deadline取得は必須のmapping order、generation、cutoff検査であり、新しいreadを追加せず既存deadline checkをそのまま使う。transportの送信履歴、receipt、その他observation materialは読まない。`history()`、`co_for_day()`、`ability_results()`、`DiscussionState.capture()`、Brain、sender、admissionを呼ばない。次を全て満たすcaught-up状態だけを`READY`とする。

- snapshotがCURRENTかつcompleteでphaseあり。
- deadlineのday/phase、mapping order、connection/action generation、local deadline/cutoffがcurrent。
- allowed handlesがnonempty/unique/typedでdeadline identityと一致する。
- actionsのworld version/last seqがsnapshotと一致する。
- network seq == world last seq、`is_caught_up`、allowed handlesがcurrent actionsに全て存在する。

上記のうちNetwork cursorだけが先行し、snapshot/actions/deadline/handlesの他条件が有効なら`NETWORK_AHEAD`とし、その時だけsnapshot versionをwait cursorとして返す。それ以外は`STALE`とする。早期STALEではversion propertyを結果生成のために追加読取しない。

## 3. admission lifecycle

offer前の`_run_admitted_inner`は完全captureを呼ばず、readiness専用`_await_readiness_with_one_catchup(pending, allow_replacement=True)`を使う。

- 初回`READY`: admission request/既存offer waitへ進む。
- 初回`STALE`: 既存context terminal mappingで終了し、admission request 0。
- 初回`NETWORK_AHEAD`: 元dispatch deadline内でWorld更新またはarbiter state changeを待つ。World waiterとstate waiterは全経路cancel/joinする。
- wake後は同じlock順でpoison元fatal、cancel/stop、active ownership、replacementを検査する。
- World更新時だけreadinessを一回再検査する。二回目`READY`だけ進み、`NETWORK_AHEAD`/`STALE`は既存terminalへ進む。loopしない。

OFFERED後は従来どおり`_capture_with_one_catchup()`で完全captureする。ここだけが`BrainInput`を構築し、discussionなら`DiscussionState.capture()`を呼ぶ。claim、Brain開始、wireは完全capture成功後だけである。OFFERED中のreplacementは`replace_waiting`一回、旧offer cancelと二重実行しない。caller cancel、stop、deadline、nonreplacement staleは既存exclusive cleanupを維持する。

direct/no-admission経路は従来の完全capture catch-upを維持する。

## 4. legacy compatibility

`capture_input()`の返値`BrainInput | None`、`capture_input_attempt()`の公開status、caught-up成功時のrequest bytesを維持する。実装はreadiness検査と完全materializationを共有してよいが、readinessとlegacy早期STALEは完全capture用viewを読まない。

`CaptureAttempt.after_world_version`はNETWORK_AHEAD wait cursorとして使われる。最小変更として型を`int | None`へ拡張し、`NETWORK_AHEAD`と既存`CAPTURED`は非負int、早期`STALE`だけ`None`を正本とする。これによりcaught-up成功の観測値を維持しつつ、STALEのためだけのversion読取を除く。既存consumerがSTALE versionへ依存しないことをstatic/focusedで確認する。fixtureへversionを足して余分なreadを隠す修正は禁止する。

## 5. focused acceptance

- admission通常成功でreadiness回数と完全capture回数を別計測し、offer前full capture 0、OFFERED後full capture 1、`DiscussionState.capture()` exact 1、claim/Brain/wire exact 1。
- offer前Network-ahead→World更新→offer→OFFERED後full captureでもdiscussion capture exact 1。
- offer前二回目Network-ahead、phase/mapping/generation/handle stale、deadline、caller cancel、stop、replacement、poison元fatalについて、Brain/wire/full capture 0、wait task残存0。
- OFFERED後の既存成功、replacement、cancel/stop、deadline/stale、poison testsを維持し、lease cancel/release/replace/claimのexclusive countを回帰する。
- direct catch-upは成功full capture 1、二回目aheadで0、deadline/cancel/stop/poisonの既存結果を維持する。
- side-effect spyでreadinessがhistory/co/ability/discussion captureやtransportの送信履歴・receipt materializationを一度も呼ばず、transport observationは既存deadline checkによるread-only `current_deadline`取得一回だけで追加readがないことを確認する。
- versionを持たないearly-stale snapshot fixtureでlegacy `capture_input()`が`None`を返し、`test_reservation_probe`を含む既存focusedが通る。
- Python 3.10/3.13でcapture focused、Phase 3.3/3.4/3.5、Phase 5 admission、CIで失敗したinstrumentation/once/reservation 3件を測定する。

## 6. source・承認・停止条件

変更はcontroller、invocation、公開export、対応focused testsの最小差分に限定する。製品schema/model/rule/privacy、deadline値、admission broker、discussion state実装を変更しない。T482の独立reviewでinterface、side-effect 0、capture exact once、全terminal cleanupを承認後にのみ統合する。

MainはRED 3件のraw command/result、修正source SHA、T482 review SHA、新CI run/commit/resultをhashchainへ保存する。3件のいずれかが残る、discussion captureが0または2、waiter/leaseが残る、既存terminal mappingが変わる場合は完了扱いにせず修正へ戻す。provider/game/model実行は不要である。
