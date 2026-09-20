# T448 S1 single-shapeの条件付きfield不整合

## 観測

一回の固定32caseでcandidate schema29合格、strict kind mapは2合格/27不合格、JSON不完結3。非該当fieldの非null25、必須null24（重複あり）。宣言順はJSON-valid29件で実現。source/config/所有processの異常なし。

独立HARD30fail、本文/act不一致27、質問回答10/18。本文の回答改善でHARD悪化を相殺せず不採用。旧結果の再生成/意味再採点、同条件retryなし。

## 原因分類と処置

test-only生成schemaでkind依存制約をnullable共通shapeへ移した結果、生成値は多くのkind依存条件を守らなかった。strict adapterは仕様どおり拒否し、validatorを緩めなかった。一般的なsingle-shape方式の否定ではなく、今回のschema/messages/モデル条件に限る結果。

S1を隔離。製品baseline保持、rollback不要。次はstrict planと本文を分離する2-callの独立詳細設計。無発話で不一致だけ低下する退化も別に評価する。

## 証拠

`Docs/ai/PHASE6_QUALITY_CYCLES_20260921.md`、T450品質/T451測定handoff。
result SHA `c5b77afbf603d3050808ba33daad1dbeeb6148d0bc01f205f795f315bac8d987`。
annotation SHA `383fa99eada92e769397c7a88c7d2c6a76661ad8e0e6016e0e6248cb5e797a4f`。
