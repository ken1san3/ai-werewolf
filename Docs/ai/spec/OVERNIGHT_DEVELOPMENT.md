# 承認済み作業を人の入力なしで連続実行する（D059）

## まず何をすればよいか（利用者向け）

**作業設定を準備したPCでは、PowerShellに打つのはこの1行だけ。**

```powershell
& "C:\AIwolf\Run-Overnight.cmd"
```

開始後は作業一覧を順番に処理する。一つの作業が終わっただけでは終了しない。
毎回同じコマンドで同じ実行を参照する。完了済みなら完了状態を表示し、別の作業を勝手に始めない。
Qwenサーバが稼働していることは必要で、このコマンドはサーバを起動しない。

2026-09-08の現状: 初回1件は公開API契約不足でNEEDS_DESIGNに停止した後、
補足を実Reviewer Solが独立承認。修正版を登録しdoctorが成功した。
同じコマンドで `.infra-runs/overnight-ready-v2/workpackage.json` の1件を開始する。
残り上位6回/Qwen8回、終了期限は元runと同じ**2026-09-08 16:26:53 JST**。
旧runと25,016tokensの消費記録は保持。以下の初回設定は修正前の記録。
詳細は `../failures/2026-09-08_OVERNIGHT_UNSPECIFIED_API.md`。

初回設定: 承認済みPhase 3.4の型・設定・決定的乱数処理の1件。
`C:/AIwolf/.infra-runs/overnight-ready/workpackage.json` を `C:/AIwolf/overnight.local.json` に登録済み。
最大8時間、上位モデル7回、Qwen8回。計画はGPT-5.5、独立レビューはSol、実装はQwen。
利用枠残量は未取得のため、残量20%の確保を保証する設定ではなく呼出回数で制限する。
作業設定の有効期限は2026-09-09 08:17:45 JST。実行中もこの期限を超えない。
事前doctorはLocal Windowsでready=true / provider_calls=0だったが、契約の意味的な完全性は検査しない。
初回実行後は上位1回、Qwen0回、ゲームコード変更なし。修正版の実装結果はまだ未確認。

`No workpackage configured` は `C:/AIwolf/overnight.local.json` がないという意味。
担当エージェントが `../infra/OVERNIGHT_PACKAGE_GUIDE.md` に従って実在する作業設定を作り、
doctorで検証してからこのファイルに登録する。利用者がJSONや仮のファイル名を手入力する必要はない。

## いつ終了するか（利用者向け）

**次のいずれかに最初に達したところで止まる。朝まで必ず動き続ける設定ではない。**

| 止まる条件 | 表示 | 次にすること |
|---|---|---|
| 用意した作業一覧をすべて完了 | COMPLETE | 正常終了。次の作業一覧はSolが準備する |
| 承認済み設計だけでは判断できない問題が出た | NEEDS_DESIGN | 上位担当が設計判断する |
| 設定した終了時刻・実行時間に到達 | LIMIT_REACHED | 終了。再開しても期限は延びない |
| 呼出上限、利用枠条件、規定回数の修正・レビューで進めない | LIMIT_REACHED / PAUSED_QUOTA / BLOCKED | 停止理由を確認する |
| 入力や承認の不整合、応答が保存されない等の異常 | INVALID / UNKNOWN_DELIVERY / BLOCKED | 自動再送せず、上位担当が記録を確認する |
| 利用者がCtrl+Cで中断、またはstopを発行 | PAUSED | 状態を確認し、同じ実行を再開する |

実行時間のシステム上限は8時間。**実際の期限と呼出上限は登録した作業設定で決まる。**
COMPLETEは「今回の作業一覧が終わった」という意味で、ゲーム全体の完成とは限らない。
Ctrl+Cは通常PAUSEDになるが、応答が未保存なら再開時にUNKNOWN_DELIVERYとなり、自動再送しない。
stopコマンドで止めた場合は明示的な--clear-stopが必要。詳しい管理コマンドは後半にある。

以下は設定担当向けの詳細。

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
