# D086 Phase6評価v2採用・製品v2 offline実装許可

Status: ACCEPTED
Date: 2026-09-28
Authority: ユーザーがT512のU1=A・U2=A推奨への「承認します」と回答

## Decision

U1=A: 独立承認済PHASE6_EVALUATION_V2_DESIGNを採用。C1 margin −0.10、両側95% case-cluster bootstrap、安全と会話を分離する。D077の絶対安全条件を同設計§10へ整合する。
U2=A: v1既定を維持し、承認済PHASE6_GENERATION_CONTRACT_V2_DESIGNに従う製品v2の段階的offline実装を許可する。T513から開始し、focusedと独立reviewを維持する。

## Retained holds

provider/LLM起動・実測、旧baseline新規判定/再採点、通常game/Master Run/Phase7、model変更/DL、Actions、main変更は未許可。PF3 UNKNOWNはstage budget UNSET/provider0。512 cap、authority/privacy、D072とゲーム規則を維持。
製品既定切替え・品質改善の承認ではない。承認済設計と過去証拠のbytesは変更しない。
