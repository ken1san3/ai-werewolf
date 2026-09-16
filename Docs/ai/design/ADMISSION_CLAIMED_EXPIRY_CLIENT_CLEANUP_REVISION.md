# CLAIMED expiry後のclient cleanup限定revision

Status: DRAFT

Revision state: T372初回CHANGES_REQUIRED C1反映済み、独立再承認待ち。

T373の承認済み `ADMISSION_DEADLINE_DRAIN_REPAIR.md` は変更しない。本revisionは、brokerがprovider未開始の `CLAIMED` leaseを自動 `EXPIRED` terminal化した後、通常consumerのABANDON/RELEASE cleanupがauthenticated admission connectionを失わないための限定追加契約である。T372の独立承認前は実装不可。

## 問題と境界

broker所有slot/task/cleanupのbounded completionはbroker/test二fileで成立する。しかし現clientでは、`ACK(EXPIRED)` はcontrol lane dispositionだけを更新し、後続ABANDON/RELEASEの正常terminalとして扱われない。

- `_cached_control_result` はABANDON/RELEASEに対するcached `EXPIRED` を返さない。
- `_apply_control_result` のABANDONは `CANCELLED` / `POISONED` / `UNAVAILABLE`、RELEASEは `RELEASED` / `POISONED` / `UNAVAILABLE` だけを受理する。
- deadline ACKが先なら、後続controlはbrokerから二度目のACKを得られずcontrol timeoutとなる。ACKがABANDON/RELEASE wait中に届けば、そのoperationが `EXPIRED` を不正statusとして扱いprotocol failureとなる。
- いずれもclientがtransportを閉じ、runtimeが保持する初期admission connectionを失う。これはprovider call 0やbroker有限解放とは別の、通常9AI稼働に対する新しいconnection lifecycle regressionである。

T374の修正中合成test `test_claimed_cutoff_cancellation_keeps_session_reusable` は、backend call 0を確認した後、期待 `session._closed is False` に対して実値trueでFAILした（`1 failed, 31 deselected in 0.89s`、JUnit SHA-256 `0ba079735a774fbb3d46260209876bb53dfcfc79101f7d2c5918e99af47b9e5d`）。従ってこの問題はコード読解だけでなく、通常cancel経路の有限再現で確認済みである。

`ACK(EXPIRED)` でgeneration futureを解決してはならない。期限後 `RESULT` / `ERROR` は0、新wireは0、未知terminalとprovider quiescenceの分類は不変とする。

## 限定client契約

対象は、同じauthenticated session・同じinvocationのcontrol laneがbroker由来の正規 `AdmissionStatus.EXPIRED` を保持または受信した場合だけである。

1. `_cached_control_result(lane, "ABANDON")` と同 `"RELEASE"` は、lane dispositionが `EXPIRED` ならcached terminalとして返し、新しいcontrol frameを送らない。
2. `_apply_control_result(lane, "ABANDON", "EXPIRED")` は正常なlocal retirementとして受理する。leaseのclaimed状態を解除し、abandon dispositionを既存のlocal-retired/no-further-RELEASE経路へ置き、laneを `EXPIRED` のまま保持し、`_clear_claim(invocation_id)` を実行する。connectionはopenのままとする。
3. `_apply_control_result(lane, "RELEASE", "EXPIRED")` も正常なlocal retirementとして受理する。leaseのclaimed状態を解除・retireし、laneを `EXPIRED` のまま保持し、`_clear_claim(invocation_id)` を実行する。後続releaseはframeを送らない。
4. ACKが既に配送済みでも、ABANDON/RELEASEのack wait中に到着しても同じ最終状態に収束する。control lane lockが一つのoperation ownerを維持し、cleanup、claim clear、lease retirementを一回だけ行う。
5. ABANDON/RELEASE frameがdeadlineより先にbroker lockを取った場合は、既存 `CANCELLED` / `RELEASED` が勝ち、その既存処理を変えない。deadline terminalが先または同時に勝った場合だけ `EXPIRED` cleanupとなる。
6. `EXPIRED` ACKは `_generations` futureをset_result/set_exceptionしない。通常callerのdeadline cancellationがgeneration waitを終え、finallyでfuture bookkeepingを除去する。
7. `POISONED`、`UNAVAILABLE`、`CANCELLED`、`RELEASED` の既存意味は変えない。未知status、foreign invocation、重複・unowned ACKは引き続きprotocol failureとする。
8. provider taskを開始済みのACTIVE/DRAINING expiryには適用しない。その経路は既存のABANDON/drain/UNKNOWN poison契約を維持し、`EXPIRED` をquiescence証明へ転用しない。

### C1: 初回cache missとACK waiter登録の競合を閉じる順序

初回 `_cached_control_result` 確認だけでは十分でない。readerはlane lockを取らずにACKをrouteでき、cache miss後から別task `_control` がACK waiterを登録する前までに `EXPIRED` が到着し得る。実装はABANDON/RELEASEの双方で次の一意な順序または同等に隙間のない順序を満たす。

1. lane lockを保持する一つのcontrol ownerが初回terminal cacheを確認する。
2. cache missなら、送信予定operationのACK waiterを先に登録する。
3. control frameのwrite ownershipを取得した後、実際の `writer.write` の直前に、同じlaneのterminal dispositionを再確認する。この再確認から同期的なwriteまでにevent-loop yieldを置かない。
4. 再確認時に同一invocationの `EXPIRED` が先着していれば、frameを0件のまま抑止し、登録済みwaiterを残留させず、cached `EXPIRED` local retirementへ収束する。
5. 再確認時にterminalがなくsendが先に勝てば、登録済みwaiterを所有したままframeを一回だけ送り、broker lockで勝った `CANCELLED` / `RELEASED` / `EXPIRED` のACKを受けて対応する既存または本revisionのcleanupへ収束する。
6. ACKがwaiter登録後からwrite直前再確認まで、またはsend後に到着しても、waiterとlane dispositionを二重解決せず、timeout、duplicate frame、unowned ACKを生じさせない。

この順序のための内部helper変更は許すが、public API・wire frame・timeout値を増やさない。一般 `_send` の全用途を広く変更せず、ordinary ABANDON/RELEASE controlの競合閉鎖に限定する。

## 最小scope

- `ai_client/llm/admission_client.py`
- `tests/test_phase5_generation_admission.py`

public protocol、enum、wire frame、brain、runtime、brokerの承認済みdeadline処理、ゲーム期限、timeout値は変更しない。実装は既存control laneとlease retirement helperを再利用し、別schedulerやreconnectを追加しない。

## 反証可能な必須テスト

1. **ACK先行 + ABANDON**: CLAIMEDをbroker deadlineでEXPIREDにし、clientがACKをroute済み後にgeneration callerをcancelする。追加ABANDON frame 0、control timeout 0、generation bookkeeping/claim/activation 0、connection open、次invocationのacquire/claim/release成功。
2. **ABANDON待機中 + ACK競合**: ABANDON frameまたはack waitをbarrierで保持し、deadline `ACK(EXPIRED)` をそのoperationのackとして到着させる。protocol failure/transport close 0、cleanup一回、結果配送0。
3. **ACK先行 + RELEASE**: provider未開始CLAIMEDをEXPIREDにした後のmandatory finally releaseがcached terminalでlocal retireする。追加RELEASE frame 0、重複release no-op、connection open、次lease成功。
4. **RELEASE待機中 + ACK競合**: RELEASEとdeadlineをbarrierで競合させ、`RELEASED` 勝者と `EXPIRED` 勝者の双方を決定的に検証する。各経路でACK ownership一つ、claim clear一回、connection open。
5. **C1 cache/waiter gap — ABANDON**: 初回cache確認直後、ACK waiter登録前にbarrierでreaderへ `ACK(EXPIRED)` をrouteする。その後control taskを進め、追加ABANDON frame 0、timeout 0、cleanup一回、connection openを確認する。
6. **C1 cache/waiter gap — RELEASE**: 同じbarrier順序をRELEASEで実行し、追加RELEASE frame 0、timeout 0、cleanup一回、connection openを確認する。
7. ACK waiter登録後・frame write ownership取得前にも `EXPIRED` をrouteし、ABANDON/RELEASE双方でsend 0、waiter残留0、同じlocal retirementとなることを確認する。
8. `ACK(EXPIRED)` がgeneration futureを解決しないこと、期限後 `RESULT` / `ERROR` 0、backend call 0を確認する。
9. 既存ABANDON `CANCELLED` / `POISONED` / `UNAVAILABLE`、RELEASE `RELEASED` / `POISONED` / `UNAVAILABLE`、unknown/unowned ACK、control cancellation、二重release、active provider drain/poison回帰を実行する。

## Gate

T372が本revisionのexact hashを独立承認した後だけT374または別Implementerがclient/test差分を実装する。独立Testerとfresh review前に受入PASSへ進めない。provider/probe/game操作は本revisionに含まない。
