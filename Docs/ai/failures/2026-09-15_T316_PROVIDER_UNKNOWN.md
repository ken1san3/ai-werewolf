# T316実game未完走と証拠生成停止

2026-09-15、承認済み9B/9client/seed8625、read/request 20秒の一回実行。Run ID `T316B-20260915T031500000000Z`、wall 305.6002045秒、exit 1、SMOKE_FAILED。直接failureは `PROVIDER_QUIESCENCE_UNKNOWN`。game_end=false、shutdown_clean=false、owned cleanup 11件/alive 0。外側1500秒timeoutではない。

35 unique provider terminalの34件はPROVEN、1件UNKNOWN。最後のbackend codeはADMISSION_POISONED。provider内部根因は未確定で、REQUEST_TIMEOUTを推定結果として記入しない。ユーザー所有providerは終了確認時も同一identityで生存、agentは操作していない。

runtime protocol manifest/accepted-text ledgerは未生成。generation enumはACCEPTED 8、OUTPUT_INVALID 26、PROMPT_REJECTED 16、BACKEND_FAILED 11で、collector許容statusと不整合。collector停止をprompt budget違反の証拠にはしない。観測accepted 8件は完全母集団として未認証。processorはmanifest_missing入力境界で起動0、全原文審査はBLOCKED。usage欠測1件も独立に確認した。

P6-B01/B06 FAIL、その他9件BLOCKED。private raw61件/1,431,431 bytesをpytest外で保持し、独立Reviewerが全size/hash一致を確認。retention manifest SHA-256 `2f12f7ea2a0b763d74a090328bdb966c093483f673114fc4b8e55ebab1b2cfa8`。private本文/pathは公開しない。source191差異0。個別retry、追加game、修正、過去FAIL/UNKNOWNの書換えなし。

旧T307にもquiescence UNKNOWNはあるが、今回と同一根因と断定しない。同じ実game完走objectiveの履歴としてD068の経路再評価対象に含め、task名を変えて失敗roundをリセットしない。次は保存原本から停止経路、producer/collector契約、usage欠測の最小境界を調査し、まとめたRepair Planへ進む。今回の一回承認は消費済み。

実測・独立判定・ユーザー操作と再開条件の正確な範囲は `Docs/ai/handoffs/tasks/T316_MAIN_RESULT.md` とT316/T318 handoffに保持する。
