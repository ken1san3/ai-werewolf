# D092: T550 v2品質測定の判定不能と製品採用見送り

Status: RECORDED
Date: 2026-09-30

## 根拠

D091/判断6の追加枠を消費し、design訂正1回・tool review1回は独立APPROVED。新identity実測は96/96行、integrity/cleanup=true、生成195回（probe3+actual192）。再接続121回を照合・記録した。元の生成0回で停止したclaim/evidenceは不変。

保存T506 baseline96行と候補96行の独立blind初回意味評価を凍結し、その後だけunblind集計した。原注釈・旧score不変。結果の正本は `Docs/ai/handoffs/tasks/T550_WP4_EVALUATION.md` と `Docs/ai/handoffs/tasks/T550_WP5_COMPARISON.json`。

## WP5の結論

**判定不能（MEASUREMENT_INVALID）。v2を製品採用しない。**

- 未観測はbaseline3、candidate10。candidateは直前自己反復5、long peer copy3、構造不正2で最終不受理だった。公開本文を観測していない行を品質FAIL/0へ補完しない。
- S1とS5/S6aにUNKNOWNが残る。canonicalの既知範囲・履歴の不完全さをhost推測で埋めない。
- 本文回答の観測PASSは33→35、trigger応答は47→53。一方S3 FAILは2→3、S4 FAILは0→4。C1の全対象行が判定可能ではないのでbootstrapは未実施、非劣位や改善を認定しない。
- S4の4件は存在しない権威結果の報告。回答数や非NONEが増えても、この安全回帰を許容して採用しない。

方式全体を統計的に劣位と判定したのではない。比較が無効であり、探索QUALIFIED/製品品質承認を出せないという結論である。

## best known stateと次の最小scope

v1を製品既定として維持する。判断6の承認済みtest-only runner・両Python検証・凍結測定・独立注釈を証拠として残す。test-only候補を製品に取り込んでいないため、品質不採用を理由とするsource rollbackは不要。

次の推奨scopeは**S4の4件について、保存済みcatalog/plan/accepted outputだけで責務のどこが欠けたかを限定診断すること**。新LLM測定は行わず、authority/visibilityを緩めない。欠測・truth不足を含む比較基盤は別問題として分離する。

今後の有効な品質比較には、全対象行で評価truth/履歴が判定可能で、受理surfaceが観測される基盤が必要。同じrawの再採点・欠測補完・baseline再生成で解決した扱いにはしない。新測定の具体的scopeと一回許可は別途必要であり、ここでは与えない。

T550 WP0〜WP5の作業を完了し、次のscope判断待ちとしてboardをDECISION_REQUIREDにする。packetのWP5どおり結果を示して停止する。人間判断の一点は「次taskとして保存証拠によるS4限定診断を優先してよいか」。

T515/T549は一時停止を維持する。製品prior以外の変更、provider追加実行・同条件retry、game/Master Run/Phase7、model変更・DL/API、Actions、main変更・mergeは禁止のまま。
