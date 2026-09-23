# D080: derived-grounding候補の不採用

日付: 2026-09-23
状態: 決定済み

## 決定と理由

T504のtest-only candidate `minimal_derived_grounding_v1`を不採用とする。
T503独立意味評価の訂正版と固定gateに基づく。全32件を保持し、追加生成・同条件retryはしない。
構造的受理は前候補T499の4/32から28/32へ改善したが、品質受入は満たさない。
これは別の生成結果の比較であり、旧24件の転帰や意味品質への単独因果を断定しない。

| 指標 | 保存baseline | T504 |
|---|---:|---:|
| HARD fail | 14 | 27 |
| SEMANTIC PASS | 7 | 14 |
| STYLE PASS | 15 | 8 |
| act/text mismatch | 23 | 29 |
| fabricated evidence | 0 | 1 |
| secret disclosure | 5 | 1 |
| state contradiction | 3 | 1 |
| ability contradiction | 0 | 1 |
| UNKNOWN | 0 | 0 |
| 質問回答 | 9/18 | 5/18 |
| 合法NONE | 2/2 | 2/2 |
| copy | 4 | 22 |

機械FAILはTEXT_INVALID 4件。機械peer exact-copyは28件中4件、残る4件は未実施。
applicability UNRESOLVED 28 / INVALID 4と品質UNKNOWN 0を混同しない。
機械PASSの全28件でspeech_actはNONE。独立意味評価では不受理4件も含め、raw上は全32件NONE。

## 評価と証拠

初稿の「NONE＋本文を一律不整合/HARD FAIL」とする評価軸は既存契約に反していたため不採用。
同じ独立Reviewerが今回の未確定annotationを訂正し、合法NONEとHARD/SEMANTIC/STYLEの分離を維持した。
暫定版はローカルへ別名保全。旧候補の再採点、provider再実行、Mainによる意味採点はない。

- 独立tool APPROVED: `Docs/ai/handoffs/tasks/T503_DERIVED_GROUNDING_TOOL_REVIEW.md`
- 測定: `Docs/ai/handoffs/tasks/T504_DERIVED_GROUNDING_MEASUREMENT.md`
- 独立意味評価: `Docs/ai/handoffs/tasks/T503_DERIVED_GROUNDING_QUALITY.md`
- 公開集計: `Docs/ai/handoffs/tasks/T504_DERIVED_GROUNDING_QUALITY_SUMMARY.json`
  SHA-256 `b68b4b2d196f7bf65ab5dbb5e42f369b6d254f20fa0d2e9aa3339f1e67a8e60d`

## best known state / 次のscope

製品schema・validator・model・promptは不変。承認済みtest-only測定器と不採用証拠を保持する。
candidateの製品統合はせず、実装削除や履歴rollbackは不要。
参照の二重生成除去は構造受理に有効な観測を得たが、copy・応答不全・act/text不一致は残る。
次はT505のproviderなし静的契約診断だけを候補とする。NONEを発話禁止にせず、
既存action/speech_act/utterance契約を対応付けて次の一因子scopeを選ぶ。
新実装は必要な設計・独立review後、新providerは別の明示許可とfreezeを要求する。
Phase6未達。通常game/Master Run/Phase7へ進まない。
