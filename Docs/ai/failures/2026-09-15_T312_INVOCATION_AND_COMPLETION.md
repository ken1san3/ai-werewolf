# T312 起動失敗と完走失敗の記録

2026-09-15、Windows Owner通常host/Python3.13。製品/test sourceはlogs/t309-repair/source-after.jsonの3実ファイルに一致。

## 起動operationの反復失敗

初回exit4、訂正exit1、再開exit4はいずれもtests0。pytest module指定とStart-Process ArgumentListのmarker分割が起動上の原因。製品FAILには数えない。D068の同一objective三回として経路再評価し、shell joinを廃止、Popen list argvをMainが確認してから独立Testerが固定範囲を実行した。新framework/製品変更なし。前3回のprivate原本と公開JSONLを保持。

## その後の実テスト結果

固定9ファイル回帰692 PASS/0 FAIL/ERROR/SKIP。後続の必須synthetic9clientは一回1 FAIL、exit1、wall1207.768秒。内部TimeoutErrorを伴いmachine_semantic_pass assertionが失敗。外側1260秒timeoutではない。根因未確定で追加試行/修正なし。実LLM/GPUを使っておらず、provider速度の証拠ではない。

T311はcommands/JUnit/linkageとpytest外synthetic原本を独立確認。cleanup11全終了、source3実ファイル最終hash一致、原本残存。判定はSTATIC PASS / REQUIRED COMPLETION FAIL — IMPLEMENTATION NOT APPROVED。

公開の測定詳細はDocs/ai/handoffs/tasks/T312_R09_INDEPENDENT_TEST.md、独立判定はT311_R09_REVIEW.md、再開条件はT309_R09_REPAIR_RESULT.md。private本文/pathを本記録へ転記しない。過去T307の実LLM FAIL/UNKNOWNやT252/T299/T300履歴を成功へ読み替えない。

## T313調査時の原本結合に関する限定追補

T311本人への確認により、wrapper JUnit/commandとsynthetic fixture原本は、開始/終了時刻、test命名規則、TimeoutError/result一致、cleanup11/alive0の相関で選択されていた。Reviewer linkageにはfixture原本path/hashの明示参照がない。以前の「linkageを独立確認」は直接producer/hash結合を保証する意味ではなく、この相関の範囲に限定する。実測JUnitの692 PASS/1 FAIL、読まれた原本の存在/内容は変更しない。T313は相関の限界を明示して原因境界を調査し、新provenance層や証拠専用の監査chainは追加しない。
