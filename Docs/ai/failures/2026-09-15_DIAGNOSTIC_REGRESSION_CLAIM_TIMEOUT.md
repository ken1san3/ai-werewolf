# 診断保全回帰中のCLAIM応答待ち時間切れ

## Attempt

T334の独立6module batch。2026-09-15 14:52:49〜14:54:34 JST、Windows Owner通常host、105.054972秒、exit1、timeoutなし。281 PASS、175 subtests PASS、1 FAIL。原本は `logs/t334-diagnostic-final/` のstdout、stderr、JUnit、run記録。191sourceの実行前後hash不変。

## Problem

`tests/test_phase5_generation_admission.py::AdmissionBrokerTests::test_drain_grace_expiry_permanently_poisoned` がprovider開始前の `active.lease.claim()` で失敗。expectedは`AdmissionStatus.GRANTED`、observedは`LLMBackendError: ADMISSION_UNAVAILABLE`。`admission_client._control` の0.05秒ACK待ちがTimeoutErrorになった。テスト本来のdrain expiry検証まで到達していない。

Mainは当該test本文がT336実装前snapshotとbyte一致することを確認した。しかし変更前後同一host比較は行っておらず、host scheduling、TCP、今回差分との因果関係を確定していない。既存コード上のFAILという意味であり、今回差分と無関係と証明したわけではない。

## Result

全batchを完遂し中断・単独retryなし。今回の新19caseは全PASSだったが、このFAILを成功へ読み替えない。T336 Implementerの先行3moduleでも同じtestのFAILと単独PASSが報告されており、その単独PASSを独立回帰の代用にしない。実provider/game/CIMは実行していない。

## Do Not Repeat

通過目的でtest/製品のtimeoutを増加したりskipしたりしない。今回別途検出されたprivate finalizer/P2 readerの修正後回帰は新sourceに対する測定として記録する。当該CLAIM failureの根因を解消したとは扱わず、再発時は単独blind retryをせずに有限の同期・通信境界調査へ切り替える。

## 最終R4測定の追補

別途検出されたP1/P2 lifecycle問題の修正後、T334が新freeze上の同じ6moduleを一括一回完遂。2026-09-15 15:14:37.035222〜15:16:25.004252 JST、PID32476、108.037609秒、timeoutなし、exit1。283 PASS＋175 subtests PASS／1 FAIL、JUnit459、errors0/skipped0。追加21case全PASSだが同じCLAIM ACK境界で再発した。新原本は `logs/t334-diagnostic-r4/`。191source前後不変。JUnit SHA-256 `b48cb9bcd91614df0d11d2aaf4e568220ebabec3fbf1fd1834858ad15cacc173`。

T333最終reviewはP1/P2限定APPROVEDと全体回帰NOT APPROVED / FAILを分離した。当該FAILの根因未確定を保持し、単独retry・timeout変更・追加調査をせず今回scope終了で停止する。前回結果と単独PASS履歴は変更しない。

## T337–T340 R13の修正後測定

新たなユーザー指示で有限調査し、通信・cleanup予算を既存class fixtureへ戻したtest-only修正を独立検証した。対象drain期限50msは維持し、実drain task由来のpoisonとprovider終了を直接確認する。ACK50ms欠測専用testは不変。T339通常hostの新6moduleは284 PASS＋175 subtests、FAIL/ERROR/skip0、T340 APPROVED。詳細は `Docs/ai/handoffs/tasks/T337_R13_TEST_REPAIR_PLAN.md`。旧T334 FAILと外部レビューの測定主張は保存し、OS内部原因の確定や旧FAILの取消とはしない。
