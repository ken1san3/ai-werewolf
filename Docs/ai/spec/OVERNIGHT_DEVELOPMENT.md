# 承認済み作業の無人実行

利用者向けの最新の操作・停止条件は **[OVERNIGHT_QUICKSTART.md](../infra/OVERNIGHT_QUICKSTART.md)** を参照する。

```powershell
& "C:\AIwolf\Run-Overnight.cmd"
```

D060の新規workpackageはversion 2。Qwenが計画/テスト案を作り、上位は計画承認と
最終候補承認の2回/単位、適用後の完了確認は機械テストで行う。
起動途中のpointer置換失敗は、既存runを検証して復旧する。重複候補や壊れた証拠は停止する。
詳細な契約は `../design/INFRA_EFFICIENT_OVERNIGHT_DESIGN.md`。
旧runの期限・予算・証拠は新方式に変更しない。

## version 1互換方式の記録

以下はD059 version 1の設定担当向け詳細。既存runの再読・復旧用であり、
新規作業の上位2回方式を説明するものではない。



2026-09-08: 開発基盤の設計・実装を独立承認済み。新基盤48件、既存369件と612サブテストが成功。
実モデルの2単位連続実行も完了した。証拠は `../handoffs/OVERNIGHT_HANDOFF.md` と
`../design/reviews/OVERNIGHT_SUPERVISOR_IMPLEMENTATION_REVIEW.md` を参照。

通常運転は **Solが契約の補足とテストを作成 → 別モデルが承認 → Qwenが実装 → 独立レビュー・適用**。
同じ作業一覧の次の単位へ自動で進む。Astraは常駐せず、Geminiは通常工程に含めない。
ゲーム機能やPhase全体の完了承認は、この基盤の完成とは別である。

## 一度だけ準備するもの

Sol等の上位担当が、承認済み詳細設計から順序付きworkpackageを作る。
各単位の変更可能ファイル・不変条件・受入条件・既存テスト・新規テストの空きパスを先に固定する。
実行中のモデルは権限を広げられない。対象設計に新しい判断が必要ならNEEDS_DESIGNで停止する。
作業一覧の準備方法は `../infra/OVERNIGHT_PACKAGE_GUIDE.md`、正確なスキーマは
`../design/OVERNIGHT_SUPERVISOR_DESIGN.md` を参照する。

必要なもの: Python、Git、既存AIagent v1、ログイン済みのCodex/Claudeのnative CLI、
稼働中の共有Qwenサーバとtokenizer。共有Qwenサーバの起動・終了はこのcontrollerの担当外。
未起動ならクラウド呼び出し前に停止する。認証情報をworkpackageに書かない。
Claudeへ渡すファイル範囲は、今回の送信について許可されたものだけをcloud_readへ列挙する。

## コマンド

PowerShellで、上位担当が準備した実在のpackageパスを使う。

```powershell
& "C:\AIwolf\Run-Overnight.cmd" doctor --package "C:\path\workpackage.json"
& "C:\AIwolf\Run-Overnight.cmd" start --package "C:\path\workpackage.json"
```

doctorはモデルを呼ばず、依存関係・設計承認・入力範囲・Qwen readinessを確認する。
startは冒頭に実際のrunパスを表示し、以後は追加入力なしで続く。
状態・停止・再開・利用量の確認にはそのrunパスを使う。

```powershell
& "C:\AIwolf\Run-Overnight.cmd" status --run "C:\path\overnight-..."
& "C:\AIwolf\Run-Overnight.cmd" stop --run "C:\path\overnight-..."
& "C:\AIwolf\Run-Overnight.cmd" resume --run "C:\path\overnight-..." --clear-stop
& "C:\AIwolf\Run-Overnight.cmd" report --run "C:\path\overnight-..."
```

通常の中断からの再開にはresumeだけでよい。stopを発行した場合に限り、再開意思を示す--clear-stopが必要。
同じrunを再利用する。期限や枠が増えたり、送達不明の要求が自動再送されたりはしない。
stopは実行境界で親と子を停止する。送信済み要求の取消しを意味せず、無関係なQwenサーバを終了しない。

繰り返しの長いコマンドを省くには、AI担当が `C:\AIwolf\overnight.local.json` を作る。
内容は絶対パスを持つ `{"package":"..."}` または `{"run":"..."}` のどちらかだけ。
packageから起動すると自動的に実際のrunへ切り替わり、次から同じrunを再開する。

```powershell
& "C:\AIwolf\Run-Overnight.cmd"
```

親runの既定は `%USERPROFILE%/.aiwolf-runs`。Windowsのnative CLIが扱える短いパスを使い、
最長経路が250文字以上ならモデルを呼ぶ前に拒否する。AppData仮想化を考慮し、保存パスはresolve済みの実パスにする。
旧 `Run-Autodev.cmd` はD058の固定契約キュー用として引き続き使える。

## 時間・利用枠・コスパ

最長8時間。実行全体の期限は作成時に固定され、再開しても延長しない。
各単位の上位最大枠は `2 × plan_attempts + 3`、Qwen最大枠は
`runner_launches × 2 × (max_fixes + 1)`。開始前に全単位分の合計が設定内か検証する。
予約した枠は失敗・中断でも返却せず、上限を超える再起動を拒否する。
これらは**呼び出し回数の上限**であり、契約プランのトークン残量を正確に予測するものではない。

- bounded_calls: 残量が取得できなくても固定呼出上限で動く。新鮮な情報で残す割合を下回っていれば停止。
- strict_reserve: 必要な利用窓の新鮮な実測情報が揃い、指定割合を残せる場合だけ動く。
- 5時間枠や週間枠が取得できない場合は不明のまま扱う。Proへの変更から数値を推定しない。
- providerを実行中に勝手に変更しない。余っているモデルの採用は次のtrusted packageの準備時に決める。
  独立レビューのmodelはplannerと異なり、AIagentのupper_review_modelsに登録されている必要がある。

quota snapshotの形式は既存の `LOCAL_IMPLEMENTATION_RUNNER.md` / `AUTONOMOUS_DEVELOPMENT.md` を参照。
reportは保存済みの実測usageを集計するだけなので、GPT/Claudeの追加トークンを使わない。
モデル別の入力・出力、未知の項目数、完了単位当たりトークン、最大予約と実際のdispatchを区別する。
サブスクリプションをAPI料金に換算した架空の金額は表示しない。

## 停止状態と記録

| 状態 | 意味 |
|---|---|
| COMPLETE | この承認済み作業一覧を完了。ゲームPhase全体の承認ではない |
| NEEDS_DESIGN | 新しい設計判断が必要 |
| BLOCKED | 規定回数のレビュー/実装で解決しない、または依存不足 |
| PAUSED / PAUSED_QUOTA | 停止要求・中断、または利用枠条件待ち。同じrunを再開可能 |
| UNKNOWN_DELIVERY | 要求を送った可能性があるが応答が未保存。自動再送しない |
| LIMIT_REACHED | 固定期限・割当上限に到達 |
| INVALID | 入力・承認・状態の不整合。自動的に修復したことにしない |

run配下にpackage/source/state、呼出packet/raw、署名付き承認、子campaign、handoffを保存する。
基盤同士の同時更新はD058と共通のリポジトリlockで排除する。手動編集との競合はhashで検出する。
モデルにshellやworkspace-writeは渡さず、承認済みテストと実装は既存v1の実行環境で検証する。
これは任意の悪意あるPythonを隔離するOS sandboxではない。
