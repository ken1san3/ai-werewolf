# 自動開発の起動と終了条件

設定済みPCのPowerShellで実行する。

```powershell
& "C:\AIwolf\Run-Overnight.cmd"
```

同じコマンドは登録済みrunを参照する。起動途中のpointer更新に失敗した場合も、
次回は同一packageの既存runを検査する。新しいrunを重複作成しない。
複数の候補や壊れた記録がある場合は選び直さず停止する。
同じruns_rootにpackageを特定できない未完成ディレクトリがある場合も、重複防止のため停止する。

| 操作 | コマンドの末尾 |
|---|---|
| 現在の状態 | `status` |
| 利用量・単位別結果 | `report` |
| 実行せずpointerだけ復旧 | `recover` |
| 停止要求 | `stop` |
| stopを解除して同じrunを再開 | `resume --clear-stop` |

例: `& "C:\AIwolf\Run-Overnight.cmd" report`
まだrunを開始していない場合、status/reportは開始せずNOT_STARTEDを表示する。

## どこで止まるか

- 今回の作業一覧の全件完了（COMPLETE）。Phase全体・ゲーム全体の完成とは別。
- 新しい設計判断が必要（NEEDS_DESIGN）。勝手に仕様を決めない。
- 承認拒否、修正上限、Qwen停止、検証失敗（BLOCKED等）。
- 固定期限・実行時間上限（LIMIT_REACHED）。再開で期限を延長しない。
- 利用枠条件（PAUSED_QUOTA）、送達不明（UNKNOWN_DELIVERY）、証拠不整合（INVALID）。
- Ctrl+Cまたはstop。送達不明の呼出しは自動再送しない。

## 新方式（version 2 / D060）

Qwenが計画・テスト案を生成し、上位モデルが1回承認する。
Qwenが実装・修正した候補を上位モデルが1回最終承認し、適用後は機械テストで確認する。
上位は2回/単位を予約する。否認後の第三レビューは自動追加しない。
Qwenは計画1回＋有限の実装/修正/freshレビュー。小さい関数ごとに作業を分割しない。
旧version 1の実行記録を新方式に読み替えない。

reportはモデルを呼ばず、失敗・再試行を含むunit別の入力/出力tokens、呼出数、
所要時間、結果、tokens/completed unitを表示する。不明な利用量を0と偽らない。
API料金換算や週間枠の残量を推測しない。bounded_callsは残量割合の保証ではない。

Qwenは共有サーバの稼働が必要。サーバを手動起動する既存コマンド:

```powershell
& "C:\AIagent\agent\serve.bat"
```

このサーバ用ウィンドウを開いたまま、別のPowerShellで開発コマンドを実行する。
controller自身はサーバを起動・終了しない。Geminiは通常工程に依存させない。

設定担当向け: `OVERNIGHT_PACKAGE_GUIDE.md` と
`../design/INFRA_EFFICIENT_OVERNIGHT_DESIGN.md`。

## このPCの初回設定（2026-09-08）

`.infra-runs/overnight-ready-v3/workpackage.json`、承認済みPhase 3.4の型と決定的jitterの1作業。
最大8時間、Sol最大2回、Qwen最大9回。設定の有効期限は2026-09-09 20:54:05 JST。
期限と8時間のうち先に来た時点で停止する。残量20%を保証する設定ではない。
今回の整備ではゲーム実装を開始していない。共有Qwenサーバは起動済みだが、
PC再起動やサーバ終了後は先にサーバを起動する必要がある。
