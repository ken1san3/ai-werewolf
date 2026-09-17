# T399 起動準備失敗（F: acceptance/test defect）

runnerは0.615秒・exit2で停止。`FileExistsError: --output-dir must not exist`。
Tester wrapperがrunner管理の出力leafを先に作成した。加えて、そのparentとtimestamp桁数も現runner契約と不一致だった。
game_started=false / LLM requests=0。品質smokeはNOT_EXECUTED。T399原本/claimを保全し、同条件再試行なし。
provider親子/listenerは停止済み。GPU1sampleは起動時だけでゲーム中性能測定ではない。

Main修正: T400では既存 `_prepare_run` に実argvを渡して起動前検証し、正規game parentの未作成leafをrunnerへ渡す。
stdioは承認済private evidence helperの別synthetic container。製品・秘密境界・時間条件は不変。
`logs/t400-short-quality-smoke/test_preflight.py` 4 PASS、PowerShell構文PASS。
品質smokeの実測はT400の別一回claimへ引継ぎ、T399を成功扱いしない。
