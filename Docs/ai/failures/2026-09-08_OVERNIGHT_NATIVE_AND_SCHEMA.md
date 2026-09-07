# D059実機試験で検出した停止（2026-09-08）

記録者: Infrastructure Implementer / GPT-6 Astra。実測環境: Local Windows。
実機試験は新規算術fixtureのみ。ゲームを実装・送信していない。

## 新規nodeidの一覧にbaselineを再掲

初回Solはtests/test_baseline.py::test_totalを新規required_testsへ含めた。
固定test_slot以外のnodeidを拒否してINVALIDで停止し、テストやsourceは未変更。
旧run: C:/Users/<user>/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/AIwolf/fixtures/overnight-98c231cb04bd4e66afa34594160f2222/runs/overnight-e7364d79f2d947eaa0e9ffc5ed35151b
実測8,855tokens。旧runは再送・状態修復しない。

対策: baselineをcontrollerが自動保持することを明示し、応答schemaのrequired_tests.items.patternを
当該test_slotだけへ固定した。今後のsemantic不正応答はraw.errorとして保存し、
FAILEDとraw hashを結びBLOCKEDにする。未知送達と混同しない。回帰テストで再読込/再送なしを確認する。

## Windows native cwdの長さ

2回目はSol提案とClaude承認に成功し、子のnative CLI起動でWinError267。
旧run: C:/Users/<user>/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/AIwolf/fixtures/overnight-5eb7a583d8f14e46b6abb1c139c3ed9a/runs/overnight-ed9fdbd3325441529ddceb3f0671dc74
childのcwdは267文字。is_dir=Trueでも無害なPython subprocess cwd probeでWinError267を再現した。
実測判明分15,146tokens、未報告項目2（起動例外のchild call）。UNKNOWN_DELIVERYのまま保持し再送しない。

対策: 親runの既定を短い%USERPROFILE%/.aiwolf-runsへ変更。
実際のparent/child/candidate/task_id/相対入力パスから最長経路を計算し、
250文字以上ならrun作成やINTENTより前に拒否する。AppData仮想化の実パス解決は維持する。

## 修正後の実機試験

C:/Users/<user>/.aiwolf-runs/overnight-fac214eba669489d88a02e50cd948dfc
は2/2 unit COMPLETE。Sol2回、Claude8回、Qwen2回、実測78,667tokens、未報告項目0。
失敗2runの判明分も含む接続検証合計は102,668tokens。起動例外の未報告項目は推定しない。
これは小規模算術fixtureの数字であり、ゲーム開発の平均効率とはしない。
