# Sol等の上位担当によるworkpackage準備

D059 controllerは承認済みworkpackage内を連続処理する。仕様やPhaseを自動発明しない。
最初の準備は、ユーザーが選んだ上位担当がこのガイドと承認済み設計を使って行う。
準備後の実装ループには人間のチャット入力は不要。

D060以降の新規設定はversion 2を使う。上位計画と3回の子レビューを使うversion 1は
旧run互換用。利用者の操作は `OVERNIGHT_QUICKSTART.md` にまとめる。
version 2: plannerはprovider=qwen/model=local-qwen/executable=現在のPython絶対パス、
plan_attempts=1、上位予約は2/unit、Qwen予約は1+runner_launches*2*(max_fixes+1)。
最初の1は計画/テスト案生成。上位Reviewerは設定済みupper_review_modelsから選ぶ。
入力はtokenizerで事前確認し、Qwenの出力長との合計が上限内に入るunitとする。
レビューの否認や曖昧な判断を追加上位呼出しで自動再試行しない。

## 担当への依頼例

> AGENTS.mdを読み、ai_status.py implement / infraから現行の承認済み範囲を確認する。
> D060の承認済み設計に従い、この範囲のversion 2 ordered workpackageを作成する。
> 仕様に未決の判断が必要な部分は含めず、OPEN_QUESTIONSへ記録する。
> 実装と計画/テスト案はQwen、通常の上位承認はSolを使う。Astraを通常運転へ割り当てない。
> 作業単位のacceptance/invariants/既存baseline tests/書込み範囲を先に固定し、
> 各単位に空のtest_slotと有限runner_launchesを割り当てる。
> 送信が許可された範囲だけをprovider別cloud_readへ列挙する。
> doctorが成功するまで準備を完了し、実在するpackageのresolve済み絶対パスを
> overnight.local.jsonへ設定する。ゲーム実行そのものはユーザーの実行範囲に従う。

この依頼はモデルの自己承認を許可しない。design_gateは実際のReviewer判定に基づく。
Solが書いた詳細設計にStatus: APPROVEDを付けるのは別modelのReviewer。

## 固定する内容

共通のキー、型、hashは `../design/OVERNIGHT_SUPERVISOR_DESIGN.md`、
version 2の差分と復旧規則は `../design/INFRA_EFFICIENT_OVERNIGHT_DESIGN.md` を参照する。
unit.templateは既存v1のfull contract。riskはred。初期read_list/allow_edit/既存testsは実在し、
allow_newとtest_slotは未存在。test_slotは宣言済み既存testディレクトリ下の新規Pythonファイル。
既存testsや基盤scripts、Docs/ai、AGENTS、設定は編集させない。

各unitは既存テストだけでもcollectでき、追加テストはそのunitの実装前にcollectできる形にする。
初期のuntracked入力・テストをv1 snapshotへ含める必要があればtemplate.read_listへ明示する。
後続unitが前の新規ファイルを読む場合はafter_readsに列挙する。
前unitの新規sourceと生成テスト・回帰nodeidsはcontrollerが引き継ぐ。
生成テストのrequired_testsには、そのtest_slot内の**新規nodeidだけ**をモデルが返す。
controllerが既存nodeidを自動保持するため、モデルに既存一覧を再掲させない。

providerのexecutableはnative実行ファイルの絶対パス。モデルIDは固定し、planner/reviewerは別model。
reviewerはAIagent設定のupper_review_modelsに含まれること。
通常例はgpt-5.6-solとclaude-opus-4-8。残量などの理由で担当を変えるなら、次のpackageを作る時に決める。
Geminiは通常パイプラインに入れない。

cloud_readは初期source、必要な設計・baseline test、全unitの将来allow_new/test_slotを含める。
個人ファイルや認証情報、送信許可のないゲームファイルを追加しない。
パケット上限は全体262144bytes以内。超えるなら承認範囲を維持したまま単位と必要な入力を整理する。
ファイルの途中切捨てで通さない。

## 上限の決め方

全unitについて上位 `2*plan_attempts+3`、Qwen `runner_launches*2*(max_fixes+1)` を合計する。
limits.upper_calls/qwen_callsはその合計以上かつ各100以下。plan_attempts=1..3、runner_launches=1..3。
secondsは1..28800。not_before/expires_atはUnix秒で、実行予定時間を包含する。
失敗を新しいrunへコピーして上限や送達不明を帳消しにしない。

strict_reserveは必要な窓の実測残量が新鮮なときのみ使用する。
不明な残量をゼロ扱い・無制限扱いせず、bounded_callsで上限を明示するか、情報が揃うまで止める。
機械的なreportだけで失敗/再試行を含むモデル別トークンと完了単位を比較できる。
追加LLMによる日次コスパ分析は通常不要。

## ゲームを変更しない接続試験

`python scripts/overnight_demo.py` は外部の新規ディレクトリへ算術fixtureを作り、
packageとstartコマンドを表示する。作成段階のprovider callsは0。
表示されたstartコマンドを実行すると実モデルを呼ぶため利用枠を消費する。
ゲーム用packageの代わりにdemoを既定設定したことにしない。
