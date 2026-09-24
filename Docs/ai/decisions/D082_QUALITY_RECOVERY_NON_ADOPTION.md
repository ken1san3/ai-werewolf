# D082: T506品質回復programの製品不採用

日付: 2026-09-24
Status: ACCEPTED（探索結果と不採用の記録。製品変更の承認ではない）

## 根拠

D081により新しいpaired3seed、有限K=3、既存Gemma比較を実行。
詳細設計は独立APPROVED。実測source `e6f8f60`、12 block/384 row、406物理call。
原本・承認・private boundaryを維持し、旧候補の再生成・再採点はしていない。

新baseline→Qwen filterはHARD fail27→5/96、fabricated4→0、secret14→1、
state4→1、ability1→0、exact peer copy3→1。主指標48→48/96、質問29→16/54。
paired clusterでHARD改善は確認したが、主・副会話指標の非劣性を確認できなかった。
filter後の受理92件にも秘密自己開示1件が残り、機械検査だけで意味安全は保証できない。

Gemmaはchoice32 tokenで96/96途中終了し、本文生成0。内容各UNKNOWN96、比較INCONCLUSIVE。
これは今回のchoice契約の適合失敗であり、Gemmaの会話品質の劣位を示さない。

## 決定

- U1/U4/G4の探索手順を適用した証拠を保存する。
- U2の製品採用は見送る。製品schema/validator/state送信経路/本番modelを変更しない。
- GB1/filterの非NONE率やHARD平均改善を、会話品質・絶対安全条件の代用にしない。
- model比較は不成立をそのまま記録し、予算を事後変更した再実行はしない。
- G3は既存GB1実装との同一性を明記し、新案として重複生成しない。
- 未使用のCO/本人宛て未回答catalogは、本文推定をせず別設計が必要な範囲として残す。

best known stateは製品不変と、新しい凍結証拠・測定後限定修正済みtest-only tools。
製品採用対象がないため、同じ証拠へ製品review chainを追加しない。
Main探索annotationはblind/独立評価ではなく、将来の製品採用には独立確認が必要。

## 次の境界

T507はchoice出力予算のoffline適合性診断と次実験の設計のみ。
総512/context8192を増やさず、再割当・整形・model比較の交絡を分離する。
新provider測定は新しい設計・freezeと別の実行許可を要する。
通常game/Master Run/Phase7/Actions/main変更は未許可のまま。

結果: `Docs/ai/PHASE6_QUALITY_RECOVERY_RESULT_20260924.md`
安全な集計とbinding: `Docs/ai/handoffs/tasks/T506_SAFE_RESULTS.json`
