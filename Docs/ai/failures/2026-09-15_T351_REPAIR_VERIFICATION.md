# T351 修正中の失敗と訂正の記録

## 原因修正前の履歴

T344 GAME_TIMEOUT/107 HTTP400、T348原因調査は既存記録を保持する。本記録はその原本や結果を上書きしない。

## 今回の環境・回帰結果

- 最初のMain focused実行はrestricted sandboxの一時領域アクセス拒否で35 setup ERROR。test本体未実行。runner-focused.xmlに保持し、通常Owner hostで同じcaseを実行して35 PASSとした。ACL/TEMP/security変更0。
- 関連4module初回は298 PASS/132 subtests、1 FAIL。既存Phase6 broker cleanup fixtureが新profileを持たず、狙ったcleanup例外より前に設定検証で拒否された。fixtureだけを明示profileへ合わせ、同4moduleは299 PASS/132 subtests。
- T353静的reviewで内部artifact pathの公開projection残留と複合secondary failure test不足を指摘。runnerのfailure projectionとtestのみ修正し、304 PASS/132 subtestsを通常hostで確認した。詳細raw/JUnitはlogs/t351-repairの各版に保持。

## 独立sessionの環境制約

最初の標準CLI起動はreadonly state DBで初期化失敗、通常hostからworkspace-writeのままArchitect/Reviewer/Implementerを起動できた。Architect再開1回はCLI既定modelの互換性エラーで作業前失敗、明示指定を維持した再開で解消。いずれもproviderへの要求ではない。

CLI版T354 Testerはapproval neverでnormal hostを申請できず、pytest0で終了。これは自動承認による拒否ではない。新しい標準collaboration Testerを確保し、同じ未実行scopeを引き継いだ。旧hand-off/logはlogs/t354-test.pre-recovery-blocked等へ保持。先行独立回帰は426 PASS＋175 subtestsで終了。Mainはその後の関連pytest不在を通常hostで確認し、次の実行前に担当を一時中断して静的review修正を反映した。再凍結後に同じ独立Testerを再開。

## 維持条件

旧失敗は削除せず、修正後実測は別artifact。実LLM/GPU/provider操作なし。最終独立テスト・審査はT351 handoffへ反映する。
