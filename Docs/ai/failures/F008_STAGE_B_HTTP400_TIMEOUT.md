# F008 Stage BのHTTP 400とTimeoutError

## Attempt

T330B-20260915T034500000000Z、承認済み9B/9client/seed8625/clock180・60・60、一回実行。2026-09-15 13:13:23〜13:33:32 JST、wall1208.911259秒、exit1。source191/documents6は凍結から不変。起動前identity/private保存をT331が独立確認した。

## Problem

原本admissionのunique (client_id, invocation_id, call_ordinal) は107件、HTTP400が107件。usageは107件とも欠測。summaryはTimeoutErrorで失敗し、完了manifest/ledgerは生成されなかった。HTTP400の具体理由とTimeoutErrorへの正確な因果連鎖は未確定。未取得のusage・summary fieldを0へ補完しない。

## Result

途中修正/interrupt/追加game/retryは行わず、一回を終了。原本はpytest外private領域に保持。owned11はcleanup後alive=false、Mainは親/runnerの消失とユーザー所有providerの同一PID/creation継続を確認した。全B項目の最終独立判定はT331の実行後handoffを参照する。初回Testerのprovider_calls=0は原本と矛盾する誤記として履歴保持し107へ訂正した。実行中Tester→Main進行報告は0で、Mainが公開status/CIM/固定metricを監視した。これを報告要件達成へ読み替えない。

## Do Not Repeat

今回の一回許可は消費済み。HTTP400の根拠を得ずに同じgameを再実行しない。モデル/時計/ACL/TEMP/合否を変えて通過させない。manifest欠落を保全目録で代用しない。初期の不完全な11/61/65件目録は履歴保持し、最終scopeと混同しない。HTTP400理由が保存済み原本で判明しない場合、ユーザー所有LLMの該当時刻の理由行を確認する。private本文を公開せず、その後の有限調査・修正・独立確認と別の一回実行承認を区別する。
