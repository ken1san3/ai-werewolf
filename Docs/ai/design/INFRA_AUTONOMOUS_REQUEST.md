# 有限・承認範囲内の無人開発オーケストレーター — DESIGN REQUEST

Issued by: Reviewer / Sol（2026-09-07、D051）

For: Detailed Design / GPT-6 Astra

Status: REQUESTED — 成果物 `INFRA_AUTONOMOUS_DESIGN.md` が、設計作者と異なる model の Reviewer による独立承認を得るまで実装へ渡さない（D051 / D053）。

Request status: OPEN

```text
DESIGN: REQUIRED
```

Infrastructure implementation risk: **Red**。外部モデル呼出しの開始、複数roleの判定、作業ツリーへの適用、次タスクへの遷移を統括するため、実装後も上位Reviewerの最終確認を要する。

## Reason

D051の必要条件へ複数該当する。

- 新しい subsystem と public CLI を作る
- 複数component（上位model、Qwen、既存runner、Docs、git作業ツリー）の責務を分ける
- 永続state machine、停止・再開、timeout、排他を新設する
- 外部送信の許可、利用枠、有限budgetを扱う
- crash位置を誤ると外部呼出しの重複、未承認適用、無期限ループにつながる
- ゲームのDesign Gate、D053の異モデル承認、D057のrisk gateと複雑に相互作用する

したがって、Implementerが権限・停止条件・再送・次タスク選択を発明せずに済む詳細設計が必要である。

## Goal

ユーザーが明示的に承認した期間・回数・送信先・入力範囲・変更範囲の中で、次の有限サイクルをできるだけ無人で進める。

1. 承認済みscope recipeからQwenが実装契約または設計artifactの草稿を作る
2. 上位modelが契約を確定し、必要なDesign Gate / 異モデル設計レビューを実行する
3. 承認済みv1 runnerへ契約を渡し、Qwen実装と機械検証を行う
4. riskに応じたfresh / 上位レビューを行う
5. 許可された場合だけ適用し、証拠を固定する
6. 同じmanifest内で明示された次recipeへ進む

「無人」は無期限・無制限・自己承認を意味しない。許可、budget、gate、証拠のいずれかが不足すれば安全に停止し、人へ具体的なhandoffを返す。

## Design scope

第一候補は `scripts/autodev.py` と `scripts/autodev_lib/` に置く上位オーケストレーターである。`C:/AIagent/agent` の承認済みrunner v1は読取・CLI利用に限定し、v1の契約、gate、apply/rollback、lock、evidence semanticsを変更しない。

設計成果物は、最低限次を定義する。

- manifest / scope recipe / authorization / packet / stage result / cycle stateのversioned schema
- CLIと終了code
- component責務と、各role/modelが書けるfield・承認できるfield
- 永続state、atomic transition、lock、crash recovery
- 外部送信前後の記録、重複防止、結果のhash binding
- call数・期限・tokenまたはquota reserve・cycle数・task数の有限budget
- v1 runnerのplan/run/resume/status/apply/rollback/handoffとの接続
- Design Gate、risk gate、正式な完了承認、次recipe選択の関係
- 機械検証、raw evidence、監査可能なhandoff

## Questions the detailed design must resolve

各問について、採用案、採らなかった主要案、その理由を簡潔に書くこと。

### Q1. 明示的な実行権限を何に固定するか

- authorization manifestの正確なschemaを決める。少なくとも発行者、version、対象repo、開始・期限、最大cycle/task/stage/call、許可provider/model/role、送信可能packet種別、読取可能path/content class、変更・適用可能範囲を持たせる
- 「利用枠が残っている」と「ユーザーが使用を許可した」を別field・別判定にする
- commit / branch作成 / push / merge / PR / deploy / 他者へのmessage / reset credit / 追加購入を、それぞれ明示許可が無ければ拒否する
- authorizationのhash、取消し、期限切れ、途中縮小をどう検出するか決める

### Q2. 外部送信packetをどう制限し証拠化するか

- provider CLIへ渡せるargvを固定し、`shell=False`相当を保証する。model生成shell文字列を実行しない
- Claudeは利用可能toolが無い構成、Codexはread-onlyかつ`shell_tool=false`等、roleごとの最小権限を具体化する
- packet schema、許可path、最大bytes、hash、送信先、model、目的、response schemaを送信前に永続化する
- secrets / credentials / private keys / `.env` / runner外の暗黙contextを送らない。認証情報の探索や非公開endpoint利用をしない
- 外部送信はこの設計・実装中には行わない。将来の実行でもauthorizationに無いClaude/Codex/Gemini送信を開始しない

### Q3. call crash ambiguityをどう扱うか

- 外部call直前・送信中・response受信後・response保存前の各crash位置を列挙する
- provider側idempotencyまたは既存thread/result照合が無い場合、送信済みか不明なcallを自動再送しない
- intent ID、packet hash、provider/thread/request ID、raw response、usageの永続順を決める
- `UNKNOWN_DELIVERY`等の安全停止状態と、再開時に人が選べる操作を決める

### Q4. 有限サイクルと停止条件をどう表すか

- state machineを、準備、草稿、上位契約確定、Design Gate、runner、review、apply、次task選択、完了、停止、回復を含めて定義する
- 最大cycle / task / stage / external call / wall-clock期限をhard ceilingにし、子stageの残budgetが親budgetを超えないようにする
- loop上限到達、同一失敗の反復、進捗なし、未知state、破損stateを成功に読み替えない
- terminal stateとresumable stateを区別し、完了済みstageを再実行しない

### Q5. 利用枠不足・不明をどう止めるか

- `task_report.py` / quota snapshotの15分staleness、reset時刻、5時間・週枠、provider差を継承する
- `explicit-call-budget`（利用可能量が不明でも、ユーザーが許可した少数callだけ実行）と`strict-reserve`（必要枠が新鮮に取得できなければ呼ばない）を別modeにする
- quota不明、期限切れ、必要window欠落、reserve未達、provider不一致時の停止状態とhandoffを決める
- 利用枠の自動reset、credit消費、購入、plan名からの倍率推定をしない
- Qwenローカルcallにも回数・token・時間上限を置く

### Q6. Qwen草稿と上位確定の境界は何か

- Qwenが草稿できるcontract/design/test artifactと、上位roleだけが確定できるfieldを列挙する
- Qwenがrisk、Design Gate、invariant、acceptance、protected path、required test、送信権限を削除・緩和できない構造にする
- 上位契約確定結果をQwen草稿と別artifact・別署名・hashで残す
- 契約不足、重要な仕様判断、範囲外変更が出たら自動補完せず停止する

### Q7. 設計artifactと異モデル承認をどうstage化するか

- DESIGN REQUIRED時のrequest → design → independent review → APPROVED hash bindingをmanifest内の前段stageとして定義する
- 設計作者と承認modelの相違をD053どおり検証する
- REQUESTのOPEN/CLOSED、設計Status、実装開始可否を混同しない
- Phase 3.4の現行未承認設計とOPEN reviewを、この基盤の承認で解除しない

### Q8. 既存runner v1をどう呼ぶか

- subprocess argv、cwd、run directory、ledger、contract hash、exit code、handoff schemaの検証を一意にする
- READYを正式完了・Design Gate承認と解釈しない
- Green / Yellow / Red / Hard Redごとの次actionをD057から変えない
- ESCALATED、AWAITING_REVIEW、APPLYING、ROLLING_BACK、source conflict、lock busyをどう引き継ぐか決める
- v1が保持するrepo lockを迂回せず、オーケストレーター自身のsingle-cycle lockとの取得順を決める

### Q9. 適用・commit・次recipe進行の境界は何か

- Green/Yellowの自動applyを許可する条件、Redの外部approval binding、Hard Red停止を明記する
- apply後のReviewer検証と正式完了承認を誰が記録するか決める。QwenやREADYに代筆させない
- local commitをv1で行うか、行うなら明示authorization、対象path、staged diff再検証、commit失敗回復を設計する。push/mergeは別権限とする
- 次recipeは承認済みmanifestの有限リストまたは決定的selectorからだけ選ぶ。Qwenの提案だけで新しいtaskを追加しない
- OPEN review、Design Gate未承認、dirty conflict、完了証拠不足を飛ばして次へ進まない

### Q10. 人の作業・既存dirty変更とどう共存するか

- cycle開始時のrepo/index/worktree manifestを固定し、対象外dirty bytesを所有物として扱わない
- 人が途中で編集した場合はhash conflictとして停止し、上書き・reset・checkoutしない
- 同一repoに複数autodev process、通常runner、手動Reviewerがいる場合の排他とread-only statusを決める
- Ctrl-C、host再起動、期限切れ、authorization取消しが、子process停止とstate保存のどちらを先に行うか決める

### Q11. 次タスクの情報源と権限は何か

- `ai_status.py <role>`、CURRENT_STATE Next Task、ROADMAP、REVIEW_INBOX、承認済みmanifestの優先順位を決める
- bulk出力をローカルscript/Qwenで絞る場合も、canonical対象や差分を減らさないD034を守る
- ゲームPhaseの完了、Next Task、review resolutionを、その判定を実行していないroleが先取りして書かない
- selectorが一意に決められない場合の停止理由と、次に人が送る具体的な一文を生成する

### Q12. 監査・再開に必要な証拠は何か

- manifest、authorization、packet、response、usage、design/review binding、runner handoff、apply journal、git diff、test raw logをrunへ関連付ける
- event indexを単調増加させ、stateとevent logの不一致を検出する
- resume時に、完了済みstageのoutput hashと入力hashを再検証する
- 最終handoffが、停止理由、消費budget、残budget、変更されたpath、検証結果、次の許可されたcommandを単独で説明できるようにする

## Constraints

- `C:/AIagent/agent` の承認済みrunner v1実装bytesとcontract semanticsを変更しない
- AIwolfのゲームコア、server、protocol、ai_client機能を本基盤のために変更しない
- Phase 3.4の既存dirty変更、CURRENT_STATEのゲームNext Task、REVIEW_INBOX、review archiveをこの設計作業で変更しない
- serverは引き続きゲーム状態のsingle source of truthであり、autodevはゲーム判定へ入らない
- ゲーム本体と通常CIはLLM・autodev無しで動く
- provider呼出し失敗や利用枠不足を別providerへの暗黙fallbackで回避しない
- Geminiを通常経路の依存にしない
- model出力を承認、機械検証PASS、正式完了の証拠として採用しない
- shell command、path、環境変数、秘密情報、git mutationの権限はmodel出力から拡張しない
- 状態破損・未知値・hash不一致・権限不明はfail closed
- 無期限daemon、無制限再試行、無制限task生成を作らない

## Out of scope

- providerアカウントや認証情報の作成・取得
- 利用枠reset、追加購入、課金APIの自動契約
- push、merge、PR公開、deploy、外部メッセージ送信
- ゲームPhase 3.4の設計修正・承認・実装
- QwenによるDesign Gate、独立承認、正式完了承認
- 悪意あるコードを隔離するOS security sandbox
- 将来Phaseを自動発明する無期限planner
- GUI、web dashboard、常駐service、定期scheduler

## Acceptance criteria

1. authorizationが欠落・期限切れ・hash不一致なら、外部call・runner・applyを開始しない。
2. 実行された各actionは、事前承認されたprovider/model/role、packet種別、path、回数、期限、budget内にあることを機械的に照合できる。
3. Qwen草稿は上位確定artifactと分離され、Qwenが権限・risk・gate・invariant・acceptanceを緩和できない。
4. Design Gate REQUIREDのtaskは、hash-boundな異モデル承認が無ければrunnerへ渡らない。
5. 既存runnerのrisk semanticsを維持し、READYだけで正式完了や次taskへ進まない。
6. external callの送達が不明なら自動再送せず、証拠付きで停止する。
7. crash/resume後も完了済みstageを重複実行せず、入力・出力hashが一致しないstageを再利用しない。
8. hard ceiling到達、quota/reserve不足、未知状態、同一失敗反復、source conflict、lock競合で有限時間に停止する。
9. 対象外dirty変更、後からのユーザー編集、保護文書・テストを上書きしない。
10. apply・review・commit・次recipeの各境界で、実際の判定roleと証拠が保存される。
11. manifestに列挙された有限recipeを使い切るか停止条件に達すれば、子processとlockを残さずterminal/handoffへ到達する。
12. dry-runでは外部call・source writeを0にしながら、全stage遷移と停止判断を再現できる。

## Required tests

設計は少なくとも次の決定的テスト群を要求すること。通常テストで実providerへ接続しない。

- authorizationのmissing / expiry / changed hash / unknown field / scope拡張拒否
- provider、model、role、packet type、path、bytes、call count、期限の各上限拒否
- explicit-call-budgetとstrict-reserveの分岐、stale/unknown quota、reserve不足
- Qwen草稿がprotected/risk/gate/invariant/acceptanceを弱めた場合の拒否
- Design Gate未承認、同一model自己承認、古いdesign/review hashの拒否
- Green / Yellow / Red / Hard Redとrunner全terminal/resumable phaseのrouting
- external call前、送信中、response保存前後のcrash注入と重複call防止
- state/event破損、未知phase、途中artifact改変、resume idempotency
- cycle/task/stage/call/wall-clock hard ceilingと進捗なし停止
- autodev二重起動、runner lock、repo lock、手動編集との競合
- partial apply / rollback / commit失敗が次recipeへ進まないこと
- OPEN review、未承認Phase、曖昧な次taskが停止すること
- dry-runでcloud call 0、source write 0
- fake provider + disposable git repoによる有限end-to-end。草稿→上位確定→runner→review→apply→次recipe→terminalを証拠hash付きで通す

## This design must not be approved if

- authorizationとquota availabilityを同じ値で扱う
- provider callの送達不明時に自動再送する
- Qwenまたはrunner READYがDesign Gate・上位review・正式完了を代替する
- 利用枠unknownを0または無制限として扱う
- 最大cycle/task/call/期限のいずれかが無い
- 外部送信packetのpath/content/bytes/model allowlistが無い
- state遷移と各crash recoveryが定義されていない
- 既存runner v1のgate、lock、journal、evidenceを迂回する
- 対象外dirty変更をcheckout/reset/overwriteする
- Phase 3.4の未承認Design Gateを暗黙に解除する
- 実providerを通常テストの必須依存にする
- 無期限planner、暗黙provider fallback、自動購入/reset、push/merge/deployを含む
- 関数内部を逐語指定し、コードの二重管理になっている

## 次に送る指示

→ Detailed Design / GPT-6 Astraへ「`Docs/ai/design/INFRA_AUTONOMOUS_REQUEST.md` を読み、有限budget・明示authorization・外部call crash ambiguity・既存runner接続・Design Gate/risk/正式完了承認を一意にした `Docs/ai/design/INFRA_AUTONOMOUS_DESIGN.md` を作成してください。外部Claude送信と実装はまだ行わず、Status: IN_REVIEWで止めてください。」

理由: 新しい権限境界と永続state machineを実装前に固定し、別modelのReviewerがD053承認できる状態にするため。
