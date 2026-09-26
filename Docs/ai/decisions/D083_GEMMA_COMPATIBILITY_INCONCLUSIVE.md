# D083: Gemma choice64/output448は適合性未確定・製品採用なし

日付: 2026-09-27
Status: ACCEPTED（観測結果と製品採用見送りの記録）

## 根拠

T507承認設計に従い、T508で明示許可された32case×seed4242027の一回測定を実施した。
20分blockの残時間条件で終了。32分母中17choice完結、13本文受理、4 K_EXHAUSTED、15未実施。
42callすべてfinish=stop。旧Gemma32-token条件の切断と異なり、観測範囲では64token内に完結した。
ただし32/32互換性条件を満たさず、適合性はINCONCLUSIVE。未実施分の補完は行わない。

独立content-only評価はHARD PASS13/FAIL4/UNKNOWN15、SEMANTIC9/8/15、STYLE12/5/15。
受理済み13本文にもHARD違反3件が残る（fabricated evidence、ability、state各1件）。
したがって欠測を別にしても製品安全条件は満たさない。秘密自己開示・exact copyの観測0を
未実施15件へ外挿せず、合法NONEの未実施2件もUNKNOWNとする。

正本: `Docs/ai/handoffs/tasks/T508_SAFE_RESULTS.json`、独立意味評価は
`Docs/ai/handoffs/tasks/T508_GEMMA_CHOICE_SEMANTIC_REVIEW.md`。

## 決定と次の境界

- 配分適合・本文品質の改善を確定しない。製品への採用なし、D082の判断は変更しない。
- 製品schema/validator/modelを維持。rollback対象の製品変更はなく、test-only toolと証拠を保存。
- 旧Gemmaとの比較は互換性だけ。Qwen同seed集計は参考値で、model・予算・欠測・評価者差を含み、
  paired3seedの改善証明に使わない。旧annotationはhash照合と集計だけで再採点しない。
- 次はT509の保存済み構造拒否/受理本文のHARD違反/欠測の限定分類。新provider、別予算、
  整形制約、model変更、通常gameへ自動移行しない。境界変更は別Design Gateと実行許可を要する。

best known stateは製品不変、独立承認済みtest-only接続、sealされた新測定と独立評価である。
