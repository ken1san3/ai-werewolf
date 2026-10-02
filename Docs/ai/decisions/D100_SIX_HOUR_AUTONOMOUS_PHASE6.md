# D100 — Phase6 最大6 REAL時間の自律継続

日付: 2026-10-02
Status: ACCEPTED

## ユーザー事前承認

開始 2026-10-02 07:18:31 JST、上限 13:18:31 JST。時間を使い切ることは目的ではない。
通常のDesign/Review Gate、FAIL、不採用、INCONCLUSIVE、MEASUREMENT_INVALIDでは停止しない。
既存境界内のoffline診断、別versionの限定rubric/annotation、test-only設計/実装、独立review、有限な次候補、文書整理を許可する。旧raw/hash/annotationは保存する。
D099および既存packetのHuman Gateと一致する判断は本承認で満たす。独立承認は代替しない。

## 新provider測定の必要条件

offline診断による必要性、一因子、独立design/tool APPROVED、source/config/model/runtime/hash freeze、process ownership、privacy/authority/model/context/game rule不変、新規外部費用なし、有限上限、同条件retryでない、別identityへの保存、cleanup保証をすべて満たす場合だけ一回実行する。
通常game/Master Run/Phase7/Actions/main直接変更は本scopeに含めない。

## 停止条件

privacy変更、authority/security弱化、game rule/本番model変更、model DL、外部費用、main merge、破壊操作、credential、同root causeで2cycle進展なし、rollback不能、evidence integrity破損、6 REAL時間到達のみ。
未満で終了する場合、なぜ本事前承認で継続不能かを記録する。通常失敗は限定修正/次仮説へ進む。

## 記録と終了

各有限cycleに仮説/差分/検証/review/結果/採否/次scopeを残す。最も根拠が強い状態を保持する。
終了時または大きなcandidate系列完結時だけContext Size Auditを専用担当へ一回委任する。通常commit/push許可とActions停止を維持する。
