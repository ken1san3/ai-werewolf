# Phase 6 GPU監視

`scripts/monitor_phase6_gpu.py` は有限REAL時間の独立CLI。GPU全体の使用率、
memory controller使用率、VRAM使用量/総量、温度、消費電力をJSONLへ保存する。
LLM要求、ゲーム設定変更、process起動停止は行わない。ゲーム固有のGPU負荷と
他applicationの負荷は分離しないため、実行時のhost条件を併記する。

```powershell
python scripts/monitor_phase6_gpu.py --output <新しいJSONLパス> --max-seconds 14700 --watch-pid <今回のrunner PID>
```

出力先の親directoryは事前に作る。既存fileは上書きしない。
Windowsでは監視開始時にrunnerのprocess handleを取得し、PID再利用で別processを追跡しない。
runner終了またはREAL上限で終了する。読取りは既定2 REAL秒まで、間隔は各query後1 REAL秒。
`AIWOLF_TIME_SCALE` は参照しない。process作成/終了処理やOS schedulingの時間による
小さい上限超過はあり得る。異常終了は非0 exitと保存済み行から判定する。

sampleにはUTC timestamp、REAL経過、GPU UUID/index、測定値、missing_fieldsを保存する。
N/Aはnull、query失敗はERRORであり、使用率0%とは区別する。
summaryにはREAL経過、終了理由、samples、errorsと設定上限を保存する。
exit 0でもmissing_fieldsは確認する。全GPUの数値はperformance evidenceであり、
それだけでPhase6 Functional Acceptanceを合格扱いしない。

T391実機検査: scale0.1、3 REAL秒、3 samples、errors0、欠測なし。
LLM停止後のGPU使用率0%、VRAM1055/8192 MiB、温度35–36℃、電力16.50–16.91 W。
この値はidle検査であり、実ゲーム中の性能測定ではない。
