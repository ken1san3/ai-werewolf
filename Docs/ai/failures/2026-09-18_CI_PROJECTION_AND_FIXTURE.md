# CI: 必須投票prompt予算とsynthetic返答の不整合

## 必須prompt予算

9人、8投票候補、棄権可、本人の必須最新能力結果を含むcontextで、任意memoryを
選ぶ前に8192 proxyを超過した。候補/結果を落とす、上限を増やす回避は不適切。
ユーザー許可後、同一candidate enum/ranking arrayを既存`$defs`で共有した。
旧Schema完全展開との16case一致、人工36contextは26受理/10拒否から36受理/0拒否。
最大はrepair予約込み8188/8192。CI35317496383では旧projection失敗codeは出なかった。
再発防止: `tests/test_ci_schema_sharing.py` と9人grounding matrix。

## synthetic返答とlong-copy guard

上記CIは代わりに`DAY1_CHAT_COVERAGE`だけでFAILした。固定集計には欠落席の
個別原因がなく、そのCIの全因果を断定しない。
Mainの限定再現ではsynthetic backendの同一返答が53文字/9語であり、他playerの
公開履歴にある場合、現行long-copy guardで拒否されることを確認した。
fixtureの返答だけを短い同意/反対文にし、先行発言に依存するstance/sourceを維持。
製品guard/閾値/acceptanceは変更しない。
再発防止: `tests/test_phase6_fixture_repetition.py` はpeer短文同意を受理し、
self同文と旧長文コピーは引き続き拒否する。関連回帰は3.10/3.13とも170 PASS。
独立Reviewer APPROVED。新bytesのCI35319487229で実completionとの因果を確認する。

証拠: `Docs/ai/handoffs/tasks/T410_SCHEMA_SHARING.md`、
`Docs/ai/handoffs/tasks/T410_CI_CLEANUP.md`、`Docs/ai/handoffs/tasks/T421_CI_SCHEMA_MEASUREMENT.md`。
private本文・秘密値・request payloadは本記録に含めない。

## 修正後CI

aa7a8a2 / CI35319487229は全9job PASS、Windows74/74 PASS、failure kind/codeなし。
独立TesterとMainが同じHEAD/終端を照合。変更前runや個別seatの原本因果は推測で補わない。

## completionの別再発と同期修正

文書のみ9433fd4のCI35321051364は8job PASS / completion FAIL。
Phase3.3では最後のnight中のclient invocationが先行phaseのawaitと重なり、
coordinatorの観測が次phaseをまとめる余地があった。fixtureのtickerを、全9席の
実invocation完了ackが揃うまで（0秒phaseも含め）進めない。production、時計、
watchdog、assertionは不変。独立審査APPROVED、Windows9process1 PASS（19.319秒）。

Phase3.5はsame-bのplayer-1 / day3 night能力がSTALE一件。mapping内一回の
attempt契約は維持し、追加await/read/retryなしのbounded固定値probeをfixtureへ追加。
独立審査APPROVED、Windows1 node PASS（66.602秒）、STALE NOT_OBSERVED。
7e57780のCI35323433097は全9job PASS。STALE直接原因の確定/修正とは扱わない。

新barrier単体のPython3.10実行で一度Path.replaceがWinError5となった。
権限/属性確認後のfresh directory有限再現確認は1 PASS、9processでも再現なし。
元の11 PASS / 1 FAIL証拠は保持し、外部lock等の原因は断定しない。
