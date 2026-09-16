# T255保全probeの検証境界と独立再評価

Status: 保全方法の修正・独立検証はT260で承認済み。T255 r1〜r4 FAILとr5拒否/未実行は履歴保持。
T252原本欠落は未解消。この報告は原本欠落の解消を意味しない。

T255のr1〜r3はprivate sentinel作成後のPowerShell ACL呼出し/出力検証で失敗した。
外側の通常文脈では新規containerのprotected DACL OW/SY/BAとsentinel hashを観測したが、
保全test全体がPASSした証拠ではない。r4は後続pytest PASS後、basetempのstrict resolveで失敗。
原raw/argv/UTC/終了値はlogs/t255-implement、停止sourceのhashは
logs/p6f-resume-20260914/t255-stopped-frozen.jsonに保持する。

停止時handoffはr4を「pytestに削除された」と解釈した。しかし後続nodeはtmp_path等を要求しない
assert Trueだけのtestであり、installed pytestはbasetempを遅延作成する。
したがって未作成だった可能性を排除できず、実cleanupの観測とは扱わない。
現r5の「存在するときだけ削除、その後不在」は未作成のケースをPASSにするため不十分。
削除前の存在・固有marker・pytest自身によるcleanupの証拠が必要である。

自動承認reviewはr5を、複数failure後の条件緩和/blind retryに見えるとして拒否し、D068の
独立判断または明示承認を要求した。Mainは別InvestigatorのT256によるread-only調査へ切り替えた。
新たなpytest、同じr5、ACL変更、旧pytest-of-<user>整理、実gameは行っていない。

T256は併せて、helperのresolve後reparse検査とbasenameだけのanchor判定、child timeoutの
不足を静的確認している。悪用・private漏洩を観測したという主張ではない。
最終根拠と修正範囲はT256日本語handoffに基づき、別の限定packetで確定する。
T252 FAIL/原本欠落と過去50fileはこの新しい失敗と混同せず、内容を変更していない。

## 後続の是正と承認

T256の独立再評価後、T257で実pytest A/B因果と厳密pathを補正し、一回の保全実験はPASSした。
ただし後続の負例を誤ってsandbox/default tempで実行したため、既知WinError5のsetup failureで停止。
Mainはこの手順不備を記録し、通常収集Noneと途中観測保存の不足をT258で限定補正した。
T259は通常非昇格・新専有明示basetempでA/Bと負例を一回ずつ独立測定しPASS。
creator終了後・後続pytestの実cleanup後もraw全bytes/hashが不変で、private ACLも確認した。
T260 fresh Reviewerはmanifest修正と保全方法を指摘なしでAPPROVED。Mainも原本13fileと公開sealを照合。
各T255/T257の旧FAILと未実行履歴は変更していない。同じr5を再申請したものでもない。
新規必須完走T261は一回のみでPASS。原本/linkage独立検証T264と全F fresh review T263がAPPROVED。
Mainが現在hashと担当解放を確認し、P6-Fを正式DONEとした。T252のFAIL/原本欠落は未解消の履歴のまま。
詳細は `Docs/ai/handoffs/MAIN_INTEGRATOR_P6F_COMPLETION_2026-09-14.md`。P6-Gは開始していない。
