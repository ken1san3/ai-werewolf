# consumer期限とprovider終了境界の混同

責務: T370 Main Integrator。観測根拠は独立T371調査、T373承認設計、T374実装、独立T375/T372検証。Main自身の未実施測定へ読み替えない。

## 再現と原因の範囲

旧brokerは開始済みHTTP呼出しもleaseの残時間で包んでいた。このためconsumerが期限切れ/ABANDONになった後の自然drain中でもbackend taskがcancelされ、providerの静止が確認できずUNKNOWNから恒久POISONEDへ至る経路があった。

T371はWindows/Python 3.13.3で実broker IPCと合成backendを使って再現した。queue待機のEXPIRED単独はpoisonせず、短いactive cutoffやABANDON後の同cutoffではcancel/poisonが生じた。provider同時実行は1、実LLM呼出しは0。旧設計にも残lease timeoutと自然drainの両要求があり、単なる既承認設計違反と断定せず、独立Architect/Reviewerで終了境界を明確化した。

R7保存原本では該当leaseのqueue待機35.996122秒、provider処理1.889119秒、UNKNOWNまで確認できた。一方、実cutoffとunclassified exception種別が欠測しているため、R7の排他的原因がこのtimerであったことやprovider内部根因はUNKNOWNのまま保持する。

## 是正

開始前の期限検査を維持し、開始済みproviderには呼出し開始からの既存request timeoutを使用。consumer期限で直接cancelせずDRAININGへ移し、安全なterminalまでslotを再利用しない。terminal側でもcutoffを確認して期限後の結果を配送しない。CLAIMED失効に伴うclient cleanupの競合/二重releaseも限定修正し、同じsessionの継続を検証した。

独立T375の固定6moduleは344 PASS＋179 subtests、OS exit0、timeoutなし、対象8source前後一致。T372は最終3source exact bytesをAPPROVED。ゲームcore、9人数、provider同時実行1、request/read期限20秒、feature期限44秒は変更していない。

## 証拠

- 再現: `logs/t371-admission-investigation/synthetic-results.json`。
- 調査: `Docs/ai/handoffs/tasks/T371_ADMISSION_CAPACITY_INVESTIGATION.md`。
- 承認契約: `Docs/ai/design/ADMISSION_DEADLINE_DRAIN_REPAIR.md`、`Docs/ai/design/ADMISSION_CLAIMED_EXPIRY_CLIENT_CLEANUP_REVISION.md`。
- 独立回帰: `logs/t375-verification/t374-r2-final-20260916`。
- 最終承認: `logs/t372-review/t374-implementation-r2-review.json`。
- 実LLM検証と残存問題: `Docs/ai/handoffs/tasks/T370_ADMISSION_CAPACITY_RECOVERY.md`。

能力/集中負荷によるqueue滞留は別の軸であり、この修正だけで9人分の処理能力やStage B全項目の合格を証明していない。
