# Phase 3.5 同一invocation内 Capture Catch-up 設計

Task: T465
Status: DRAFT

## 1. 目的と限定境界

T463は、arbiterへ登録した時点ではcurrentだったreservationが、grant後のcapture直前にNetworkだけ先行すると、`BrainController.capture_input`がNoneを返し、Brain 0回・wire 0回の`STALE`になることを決定的に再現した。その後Worldが同じphase、handle、mappingへ追従しても、controller側では同じmappingが消費済みで再armされず、reservationが未送信のまま終わる。

修正候補は、同じarbiter pending invocationがgrantを所有し、Brainもwireも未開始の間だけ、Network先行を有限に待って一度再captureする。新reservation、同mapping re-arm、Brain retry、wire retry、attempt追加は行わない。authority、server acceptance、deadline、mapping replacement、最大pre-send re-arm、NOT_DELIVERED retryの規則は不変である。

本taskは詳細設計だけであり、現在のGC2品質実験sourceを維持するため製品code/testを変更しない。実装は独立Reviewer承認と品質実験freeze後に別taskで行う。

## 2. capture結果interface

`BrainController.capture_input`の既存`BrainInput | None` public contractは既存caller用に維持する。arbiter専用の追加同期interfaceを設ける。

```python
@dataclass(frozen=True)
class CaptureAttempt:
    status: Literal["CAPTURED", "NETWORK_AHEAD", "STALE"]
    request: BrainInput | None
    after_world_version: int

def capture_input_attempt(
    *,
    allowed_handles: tuple[ActionHandle, ...],
    dispatch_deadline: DispatchDeadline,
) -> CaptureAttempt: ...
```

`CAPTURED`だけがrequestを持つ。`NETWORK_AHEAD`と`STALE`はrequestを持たず、`after_world_version`は判定に使用したimmutable World snapshot versionである。型、組合せ、非負versionを`__post_init__`で閉じる。既存`capture_input`は同じ内部capture関数を呼び、`CAPTURED`ならrequest、それ以外はNoneを返す。captureロジックを二重実装しない。

`NETWORK_AHEAD`は次を同じevent-loop turnの同期readで全て満たす場合だけ返す。

- World snapshotがCURRENT、completeで、phase/dayがdispatch deadlineと一致する。
- `current_actions`の`world_version`がsnapshot versionと一致する。
- `network_last_seq > world_last_applied_seq == snapshot.last_applied_seq`で、唯一の不整合がNetwork先行である。
- current deadlineがmapping order、phase、day、connection generation、action generationについてpending deadlineと一致し、local deadlineが存在し、pendingの有限cutoff前である。
- pendingの全allowed handleが型正しく一意で、phase/day、connection/action generationがpending deadlineと一致する。

Networkが追従済みなのにallowed handleが存在しない、phase/mapping/generation/cutoff不一致、World非CURRENT/不完全、Network cursor逆行、その他のcapture不能は`STALE`とする。Network先行時はlive actionsを権限根拠として覗いたり、hidden actionsからrequestを作ったりしない。WorldがcommitしてcoherentになるまでBrainInputは作らない。

## 3. arbiter lifecycle

direct pathとadmission-enabled pathの両方が同じprivate helperを使う。

```python
async def _capture_with_one_catchup(
    pending: _PendingInvocation,
) -> BrainInput | BrainDispatchResult: ...
```

1. deadline、`cancel_requested`、arbiter stop/poisonを確認する。
2. `capture_input_attempt`を一回行う。`CAPTURED`なら既存処理へ進み、`STALE`なら既存のstale/deadline resultを返す。
3. `NETWORK_AHEAD`だけは、`world.wait_for_update(after_world_version)`とarbiterの単一state-change wake eventを待つ。待機上限は元`dispatch_deadline.not_after_monotonic - clock()`で、timeout値やcutoffを延長しない。wake eventは理由を表さず、取消結果を直接決めない。
4. いずれかがwakeしたらarbiter lock下で、poison、caller cancel、stop、admission replacement、context change、World更新の順に現在状態を判別する。World更新として続行できる場合も、phase/day、connection/action generation、mapping order、cutoff、allowed handle集合、pending ownershipを再検査する。その後captureを一回だけ再実行する。
5. 二回目が`CAPTURED`なら既存Brain pathへ進む。二回目も`NETWORK_AHEAD`または`STALE`なら待機を繰り返さず、既存`STALE`へ終端する。deadline到達は`DEADLINE_SUPPRESSED`、cancel/stopは既存`CANCELLED`へ写す。

World更新待ちは最大一回であり、version更新なしのpoll、sleep、loop、callback再登録をしない。再captureは同じ`_PendingInvocation`、同じresult Future、同じowner grant、同じallowed handles、同じdispatch deadlineを使う。reservation controllerの`_invoked_mappings`、attempt ledger、pre-send re-arm counterを変更しない。

arbiterは既存`_admission_changed`相当の単一state-change eventをwake通知に使えるが、終端理由をeventへ畳まない。caller cancellationと通常のarbiter/controller stopだけを既存`CANCELLED`へ写す。poison時は`_mark_poisoned_locked`が設定した同一fatal exceptionとshutdown ownershipを維持し、helperはresultを`CANCELLED`や`STALE`で上書きしない。direct pathのcontext changeは既存stale/deadline resultへ、admission reaction replacementは後述のtransitionへ渡す。

catch-up helperが作ったWorld wait taskとstate-change wait taskは、勝敗や外側cancelにかかわらずarbiterがcancel/joinする。callerの取消しだけで孤児waitを残さず、pending result公開と`execution_complete`の既存順序を維持する。lockを保持したままWorld待機、lease操作、task joinをawaitしない。

## 4. direct pathとadmission path

direct pathでは、現在の`_execute_direct`の最初のcaptureをhelperへ置換する。catch-up中も`_active is pending`を維持し、reactionや別reservationへgrantを渡さない。capture成功後に残時間を再計算し、Brain timeoutは従来どおり`min(pending.timeout_seconds, remaining, max_decision_seconds)`とする。`on_brain_start`はcapture成功後、Brainを実際に開始する直前の一回だけ呼ぶ。

admission pathでは二箇所を同じ規則にする。

- offer取得前のcontext確認でNetwork先行なら、admission requestを作る前にcatch-upする。
- OFFERED lease取得後のcaptureでNetwork先行になった場合、arbiterが所有する未claim leaseを保持したまま同じ有限catch-upを行う。成功時だけ既存replacement確認とclaimへ進む。caller cancel、stop、deadline、非replacement staleでは、この経路の単一ownerだけが既存`_cancel_offered`をexact一回実行する。

catch-up中にvote ability replacementが勝った場合、lock下判別はreaction pendingを`CANCELLED`/`STALE`へ終端せず、既存`_replace_before_claim_if_needed`が作る`_ReplacementTransition`へ制御を渡す。既存どおり旧reactionを`_suspended_reaction`へ移し、`_run_replacement`でreplacementを実行し、observation gate/cancel/stop条件を満たす場合だけ旧reactionをresumeする。direct pathにはこのadmission replacementを新設しない。

OFFERED leaseと`replace_waiting`の所有は排他的にする。replacementが選ばれた時点でcatch-up helperは旧offerへ`_cancel_offered`を呼ばず、`_replace_before_claim_if_needed`／brokerの`replace_waiting(old_id, request)`だけに旧waiting offerの置換を委ねる。反対にcancel/stop/deadline/stale経路では`replace_waiting`を開始せず`_cancel_offered`だけを使う。transition作成後の例外やcancelは既存`_run_replacement` ownershipで処理し、同じofferにcancelとreplaceを二重実行しない。

lease保持中も待機は元dispatch cutoffまで、World更新最大一回である。新しいadmission request/invocation ID、再offer、successor、claimは作らない。claim前なのでgeneration slotはactive化せず、Brain/wireは0。lease protocolに保持中の有限waitを認めない実装であることが実装前検査で判明した場合は、admission pathを変更せず`STALE`とするが、direct pathの承認範囲へ黙って一般化しない。この場合だけ追加設計reviewを必要とする。

## 5. identity・authority再検査

待機前後で次を値同一として確認する。

- owner、pending object identity、priority、allowed handle tuple。
- handleのtype、option identity、phase/day、connection/action generation。
- `DispatchDeadline`のmapping order、phase/day、connection/action generation、discussion trigger、cutoff。
- current deadline mappingとcutoff、World freshness/completeness、Network/World cursor coherence。

再capture成功時は、BrainInputに含まれるhandleがallowed tupleに存在し、current actionsにも存在することを既存captureが保証する。arbiterはhandleを差し替えず、World snapshotをcurrentに見せかけず、Brainのcoherence checkを緩和しない。mapping extension/replacementやaction generation変更は同じpendingで追随せず、既存controllerの新mapping re-arm規則へ返す。

send直前の`BrainController`再検証、typed Network send、server authoritative accepted/rejected/unknown処理は不変である。catch-up待機中または終了時にrequest IDを生成せず、wire started/unknown後にはこの経路へ戻らない。

## 6. 終了・失敗原子性

| 条件 | 結果 |
| --- | --- |
| 初回capture成功 | 従来どおりBrainへ進む |
| Network先行→一回のWorld更新→同一context capture成功 | 同一invocationでBrain最大1、wire最大1 |
| World更新なしでcutoff | `DEADLINE_SUPPRESSED`、Brain/wire 0 |
| 更新後もNetwork先行 | `STALE`、追加wait 0、Brain/wire 0 |
| phase/mapping/generation/handle失効 | `STALE`または既存deadline result、古いhandle送信0 |
| caller cancel / controller stop / arbiter stop | 既存`CANCELLED`、wait taskをjoin |
| poison | 既存fatal exceptionとpoison shutdownを維持し、再分類しない |
| admission reaction replacement | 旧pendingをsuspendし、replacement実行後に既存条件でresume |
| World ENDED/FAILED | 既存terminalへ有限終了 |
| wire到達後 | catch-up経路なし、既存receipt/unknown規則 |

outcomeは同じpendingにつき一件で、catch-up観測を新attempt/outcomeにしない。診断には公開秘密を増やさず、`capture_catchup_waited` bool、`capture_attempts` 1/2、terminal reason、wait duration microsecondsをbounded snapshotへ追加できる。player role、action target、Brain input本文は追加しない。

## 7. 実装scope

承認後の最小製品変更候補は`ai_client/brain/controller.py`の構造化capture結果と共通内部capture、`ai_client/brain/invocation.py`の一回wait/cancel ownershipである。必要な型exportとfocused testだけを加える。`vote_ability/controller.py`、Network、World、server、config、retry上限は変更しない。既存`WorldState.wait_for_update`を使い、新しいcondition、background task、schedulerを追加しない。

品質実験のsource freeze中は実装しない。GC2の測定・source整合確認が完了してから製品diffを作り、独立Reviewerが設計一致を確認する。

## 8. deterministic tests

- T463再現順序をfocused化する。eligible登録→grant→Networkだけseq先行→初回`NETWORK_AHEAD`→同mappingのWorld追従。Brain1、wire1、authoritative acceptance1、outcome1、reservation attempt1、re-arm0を確認する。
- 初回capture成功の既存pathはwait0、capture attempts1でbody/handle/send/resultが不変。
- Network先行後、phase/day、connection generation、action generation、mapping order、handleの各一点を変更する表でBrain/wire 0を確認する。
- World未追従、World ENDED/FAILED、cutoffちょうど/直前、caller cancel、arbiter stopを仮想clockとEventで決定的に制御し、有限終了、wait task残存0、owner解放を確認する。
- World versionが一度進んでもまだNetwork先行なら二回目capture後`STALE`となり、三回目captureやbusy loopがないことを確認する。
- catch-up中に複数World更新を発生させてもpending/grant/result/outcomeが一つ、Brain/wireが最大一つであることを確認する。
- admissionなし/ありの両pathで同じidentity検査を行う。OFFERED後waitの成功、deadline/cancel時lease release exact一回、claim0または成功時claim1、新admission request0を検査する。
- direct/admissionのcatch-up中にcaller cancel、stop、poison、context changeを競合させ、wake後のlock下分類、fatal result非上書き、wait task残存0を確認する。
- admission reactionのcatch-up中にvote replacementを入れ、旧reactionが`CANCELLED`/`STALE`にならずsuspendされ、replacement/successorが一回だけ実行され、既存条件で一回だけresumeすることを確認する。OFFERED後の同競合では旧offerに`replace_waiting`一回、`_cancel_offered` 0回であり、他の終端では逆にcancel一回、replace 0回であること、Brain/wire重複0を検査する。
- reaction ownerでも同じ有限性を確認し、reservation priority、active非preempt、ownerごとpending最大1、全pending最大2を維持する。
- `test_pre_wire_deadline_suppression_rearms_on_later_extension`、`test_pre_wire_shortening_consumes_rearm_cap_without_busy_loop`、`test_mapping_replacement_rearms_before_wire_without_duplicate_send`とarbiter cancellation/observation/admission回帰を実行する。
- 独立Testerが該当nine-process completionを実行し、exact reservations、attempts、accepted evidence、process cleanupを確認する。T463再現の修正前FAIL/修正後PASSも保存する。

## 9. Design Gate判定

**DECISION_REQUIRED: NO。** 原因は確定し、server authorityやretry規則を変えず、同一pendingのpre-Brain同期窓として有限に閉じられる。重要なproduct選択は不要であり、Architect設計と独立Reviewer承認で自律解消できる。

独立Reviewerは、`NETWORK_AHEAD`判定が狭いこと、最大一回wait、wakeとlock下reason分類、replacement suspend/resume、poison fatal ownership、OFFERED leaseのcancel/replace排他、cancel/join、同一identity再検査、attempt/re-arm/send上限不変、品質実験後の実装順序を確認する。
