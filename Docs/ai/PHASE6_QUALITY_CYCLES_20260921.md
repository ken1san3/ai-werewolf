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

現在の製品baselineを維持。S1/P2はtest-only隔離され、製品採用なし。独立承認済み検証基盤を保持。Cycle3 IC2は設計承認・offline PASS、tool審査中・未生成。

## Cycle 2: 2-call Plan→Message（不採用）

S1では自由なnullable単一shapeがkindごとの制約を満たせず、strict adapterは30件を受理しなかった。validatorを緩めず、次は旧grounded planを厳密schemaで先に生成し、その確定planから本文を別callで生成する仮説へ進む。S1は再実行せず、独立意味評価と並行してT452詳細設計を行う。モデル/製品境界は不変。新予算/呼出回数/失敗原子性を設計し、T453独立承認後だけ実装する。

### Cycle 2 現在の証拠

- T452設計 SHA `c2f46835bcb13e7f6008b0fb5e74e3cda0817b722d24030aca72b7497ce1db0c` はT453独立APPROVED。
- T455がpure helper/受理同値testsを担当し、Mainがrunner/outer接点・呼出数/失敗経路testsを実装。製品schema/code不変。
- Python3.13関連237 PASS、追加outer routing1 PASS。Python3.10 focused118 PASS。check_docs/diff-check PASS。
- 公式converter32/32 PASS、native tokenizer71有限fixture、生成0。多byte最大文字数fixture1件は128 tokens超過したが、診断値として保存しschema受理集合やtoken予算を変更しない。
- 2call合計512 tokens（384/128）、最大32 decisions/64calls、retry/repair0。新freeze `logs/t448-quality-cycles/frozen-p2/plan.json`、tool審査対象5filesは `p2-review-sources.json` に固定。
- Windowsの置換保存でPermissionError5を観測。dispatch前の消費をexclusive個別ファイルへ記録し、結果snapshotは既存方式を維持した。通常のoffline fixture/保存不具合として修正し継続。
- tool最終APPROVED後にT454で32判断/60callを一回測定し、T453独立意味評価まで完了。

### Cycle 2 採否

| 軸 | baseline | P2 |
| --- | ---: | ---: |
| HARD FAIL | 14 | 22 |
| SEMANTIC PASS | 7 | 4 |
| STYLE PASS | 15 | 15 |
| act/text不一致 | 23 | 28 |
| fabricated evidence | 0 | 2 |
| 秘密 / 状態 / 能力矛盾 | 5 / 3 / 0 | 1 / 2 / 2 |
| UNKNOWN | 0 | 0 |
| 固定18質問への回答 | 9 | 7 |

P2不採用。全32構造PASSでも全actがNONE、本文28件はすべて不一致。peer copy9/self copy7、合法NONE control0/2。32判断は302.679 REAL秒、prompt108,772/completion7,507、GPU peak6,322MiB/100%、終了process残存0。品質と性能は別評価。原本は保存し、製品rollback不要（製品未変更）。

### Cycle 3 scope

P2は本文を別callへ分離したが、intent kind選択時にgrounding/state update全体の生成costが残っていた。次はIC2として、call1でkindだけを等しいshapeのenumから選び、call2でそのkindを固定した旧完全出力を生成する設計をT456へ割当。NONEを禁止せず、旧validator/authority/512 tokens合計/32caseを維持する。field順やoneOf枝順の再調整ではない。設計独立承認前は実装・生成しない。

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

### Cycle 3 実装gate

T456設計はT457独立APPROVED。kind-only choice32tokens→元kind枝locked full480tokens、全valid choiceで必ず2call。Main/T459がtest-only実装。Python3.13合同310＋freeze7 PASS、3.10 181 PASS。P2既存10mock条件の抽出前後hash一致、converter224/native263fixture合格。製品境界不変、tool審査中、実生成なし。
