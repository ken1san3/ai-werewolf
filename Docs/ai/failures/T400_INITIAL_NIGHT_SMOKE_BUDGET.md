# T400 初夜だけで終了した測定条件不備

分類: F acceptance/test defect。Mainが測定時間を60logicalに指定したため、既存runnerの
初夜60logicalを消費してday0/night0でGAME_TIMEOUTとなった。製品品質や処理性能の失敗とは判定しない。
game実行1、LLM requests/provider calls/accepted chatは0。A–Iは全NOT_OBSERVED。
real600.000808秒、logical60.000081秒、scale0.1。runner全体619.3872184秒。
証拠: `logs/t400-short-quality-smoke/measurement.json` SHA256
`4d0e2f89c7a1712e5f3422f87ba9c455d22f8785f4ecf696a1ff75d669de6370`。
所有provider親子/listener停止済み。GPU575samplesは推論0なので負荷評価に使用しない。

限定修正: T401はゲーム設定を維持し120logicalとし、初夜60＋昼60を測る。
実GameStateでnight0→dawn→day1と時刻120でday継続を確認、起動契約と合わせ5 PASS。
製品bytes/承認は変更なし。T400原本を保存し、同条件再試行はしない。
