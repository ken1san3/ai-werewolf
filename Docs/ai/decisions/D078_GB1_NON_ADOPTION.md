# D078 GB1候補の不採用

## Status

Accepted（既存D077と承認済み実験gateの適用結果。製品規則・受理境界の変更なし）

## Context

T480新32ケースをT478が独立意味評価した。判定原本は
`Docs/ai/handoffs/tasks/T478_GROUNDING_BASIS_QUALITY.md`、SHA-256
`f4c17f6e148d6df8df215318ba686d81587491269d28a1bc6083f132883ce34e`。

## Decision

GB1を製品採用しない。保存結果を維持し、同条件で再生成・再採点しない。

## Why

HARD失敗・SEMANTIC・STYLE・act/text不一致は改善したが、秘密開示、固定質問回答、
合法NONE、長文peer copyの個別gateに不合格。改善指標との相殺は認めない。

## Consequences

会話生成baselineを維持する。独立承認済みGB1測定toolとCO整合offline helperはtest-onlyで保持する。
次はchoiceの意味的適合と、選択basisが主張を支える関係を分けた最小設計が必要。
モデル能力を単独原因と断定せず、通常game・Phase7・新provider cycleへ進まない。
