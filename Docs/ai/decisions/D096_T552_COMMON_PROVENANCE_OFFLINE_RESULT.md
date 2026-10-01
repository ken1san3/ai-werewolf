# D096: S4将来契約とoffline helperの承認結果

Status: RECORDED
Date: 2026-10-01

T552の限定設計、test-only実装、focused/関連回帰、独立tool reviewを完了した。新metric S4_COMMON_PROVENANCE_V1を旧S4と分け、合法な無ref騙りと権威refの誤投影・偽装を区別する。hostは本文から意味/refを作らない。

最終design/clarification/toolは独立APPROVED、両Pythonfocused29PASS・関連回帰139PASS。環境差は既存3.10 venvとowner tokenの新tmpで解消し、assertion/privacy/timeoutは不変。証拠: `Docs/ai/handoffs/tasks/T552_SAFE_RESULTS.json`、`Docs/ai/handoffs/tasks/T552_COMMON_PROVENANCE_S4_ALIGNMENT.md`。

これは将来のoffline評価境界の承認であり、LLM品質達成や製品採用ではない。T550/T551のraw、annotation、S4値、D092 MEASUREMENT_INVALIDは不変。新provider0、main/Actions/game0。v1既定とT515/T549 holdを維持する。

## 次の最小scope

T553で新rubricの比較用評価枠を確定する。旧保存baseline/candidateへ新S4だけを別annotationとして初回blind評価する案は、旧再採点禁止の例外となるためユーザーの明示判断が必要。許可が無ければBASELINE_NOT_COMPARABLEを維持し、その案を実行しない。旧注釈の上書き・生成や同条件retryは提案しない。

不足は新rubricの評価枠の許可であり、通常のtest/review失敗ではない。T552の承認状態をbest known stateとして保存し、T553 DECISION_REQUIREDへ引き継ぐ。次の測定や製品変更をT552承認だけで許可しない。
