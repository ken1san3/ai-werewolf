# finish=stopでもwhole-file生成が途中で切れる

記録: Implementer / GPT-6 Astra、2026-09-07、Local Windows。

Qwen3.6-35B-A3Bへ厳密な契約検証moduleの全体生成を依頼したところ、2回とも末尾が切れた。
1回目は文字列途中のSyntaxError、2回目は関数本体不足のIndentationError。
finish理由はstopであり、モデルの終了理由だけでは完全性を判定できなかった。

候補は元リポジトリへ適用しなかった。上位担当が実装し、独立レビューとテストで確認した。
証拠: `C:/AIagent/agent/infra_contract.response.json` と `infra_contract.candidate.py`。
これらはgit対象外の初期投資ログであり、日常runnerの成功数へ含めない。

再発対策: 出力形式・サイズ・Python構文・保護テストを機械検証する。修正は最大2回。
複雑な権限検証をQwenへ繰り返し丸投げせず、契約を分割するか上位へ引継ぐ。
finish=stopを機械検証成功や承認として扱わない。
