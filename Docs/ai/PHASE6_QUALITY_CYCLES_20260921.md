# Phase 6 有限品質改善 2026-09-21

## 運用

開始22:14:13Z、上限翌04:14:13Z（6 REAL時間）、最大6cycle。通常のtest/候補失敗は次の観測として継続。人間専有選択、秘密/所有/整合安全性、同根本原因2cycle進展なし、上限到達のみ停止条件。旧証拠再生成/再採点、通常game/Master Run/Phase7は0を維持する。

開始時main5a5635f、専用branch8b322d1。既存CI35518262838は全9job PASS。I1実装/所有修正/審査/32測定は前回完了、不一致24のため不採用。今回それを再実行しない。

## Cycle 1: S1 single shape

- 仮説: speech_act objectのoneOf分岐を除くと、本文との整合がHARDを増やさず改善する。
- 変更: baselineのspeech_actだけ17field closed shape。kind以外はschema上nullable、strict mapで必須/適用nullable/非該当nullを区別し、旧validatorへ無損失変換。
- 非変更: prompt/messages/製品schema/model/profile/予算/旧証拠。
- 設計: T449、T450独立APPROVED。source_interpretationの2const衝突は設計段階で限定修正済み。
- focused: Python3.13関連340 PASS、3.10 focused104 PASS、Reviewer84 PASS。公式converter32 PASS、生成0。
- tool review: APPROVED、`handoffs/tasks/T450_SINGLE_SHAPE_TOOL_REVIEW.md`。
- 人工suite: T451で32生成完了、retry0/repair0。候補schema29/32、strict map2/29、旧受理2/32。構造不適合30によりHARD上限14を必ず超え、不採用。独立意味評価32/32完了。
- 構造違反の限定分類: 非該当非null25、必須null24、JSON不完結3（重複あり）。S1は隔離、製品rollback不要。次はcycle2の2-call詳細設計。

## Best known state

現在の製品baselineを維持。S1はtest-only隔離され、製品採用なし。承認済み検証基盤を保持。S1は不採用、P2は設計中・未測定。

## Cycle 2: 2-call Plan→Message（設計中）

S1では自由なnullable単一shapeがkindごとの制約を満たせず、strict adapterは30件を受理しなかった。validatorを緩めず、次は旧grounded planを厳密schemaで先に生成し、その確定planから本文を別callで生成する仮説へ進む。S1は再実行せず、独立意味評価と並行してT452詳細設計を行う。モデル/製品境界は不変。新予算/呼出回数/失敗原子性を設計し、T453独立承認後だけ実装する。

### Cycle 1 最終比較

| 指標 | 保存baseline | S1 |
|---|---:|---:|
| HARD fail | 14 | 30 |
| SEMANTIC pass | 7 | 4 |
| STYLE pass | 15 | 16 |
| act/text mismatch | 23 | 27 |
| fabricated evidence | 0 | 0 |
| secret disclosure | 5 | 5 |
| state contradiction | 3 | 3 |
| ability contradiction | 0 | 0 |
| UNKNOWN | 0 | 0 |
| 固定18質問の回答 | 9 | 10 |

G14無発話controlは2件とも発話を選択。JSON-valid29件の実17key順は全件設計どおり、3件はJSON不正で順評価不能。tool初期falseを順序違反と数えない。候補schema29/32→strict2/29で拒否しており、受理境界は守られたが生成品質は悪化した。

独立判定 `handoffs/tasks/T450_SINGLE_SHAPE_QUALITY.md`、annotation SHA `383fa99eada92e769397c7a88c7d2c6a76661ad8e0e6016e0e6248cb5e797a4f`。全体採否は不採用。候補隔離、製品rollbackなし。次scopeは2-call詳細設計で、人間判断待ちにしない。
