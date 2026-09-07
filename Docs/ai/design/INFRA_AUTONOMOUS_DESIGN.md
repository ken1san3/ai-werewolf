Status: APPROVED — Reviewer / Sol independent D053 approval (2026-09-07); binding review: `Docs/ai/design/reviews/INFRA_AUTONOMOUS_REVIEW.md`.

# 無人実行の拡張: 契約確定・上位レビュー・継続キュー

Author: Detailed Design / GPT-6 Astra, 2026-09-07.

## Purpose and scope

ユーザーの「できるだけ無人でゲーム開発を続けられる」依頼に基づく。
上位モデルを工程管理に常駐させず、Qwen契約草稿、上位の確定、Qwen実装/修正、
必要な上位承認、適用、次タスクまでを有限キューで進める。
上位が一度決めたパス・検証条件・設計承認はモデルに拡大させない。
ゲーム本体の設計未承認を飛ばさず、ユーザーの仕様判断は引継ぐ。

## Files and responsibilities

- `scripts/autodev.py`: start/resume/status/stop/doctor/reportのCLI。
- `scripts/autodev_lib/`: 設定検証、永続状態、provider呼出し、キュー状態機械。
- `tests/test_autodev.py`: 模擬providerを用いた制約・再開・予算・状態遷移の検証。
- `Docs/ai/infra/autodev.example.json`: 限定キューの設定例。
- `Docs/ai/spec/AUTONOMOUS_DEVELOPMENT.md`: 日常運用、停止境界、起動例。

AIagent v1は既存Pythonライブラリとして再利用。変更しない。
ゲーム本体・通常CIはAIagentやモデルが無い環境でもテスト可能。
新テストはAIagentインストールが無ければintegration分だけskipする。

## Trusted campaign configuration

JSON version=1。repo_root、agent_root、expires_at（UTC秒）、max_cloud_calls（1..100）、
max_seconds（1..86400）、max_attempts（1..3）、max_packet_bytes（1..262144）、
max_output_bytes（1..1048576）、providers、quota_policy、jobsを持つ。
providerはgpt/claude、modelは実モデルID、executableはインストール済みCLI絶対パス。
provider別cloud_readはrepo相対ファイルの厳密リスト。ディレクトリglobや秘密情報は不可。
予算・送信範囲はこの信頼する設定をモデル応答から作らない。
上位が既存設計に基づき複数jobをまとめて準備する。人間は毎タスク入力しない。

jobはtaskまたはreview。id一意、instruction、read_list、reviewer（gpt/claude/auto）。
taskはv1 contractの固定templateを持つ。Qwenが補うのは
goal/contextだけ。不変条件/acceptance/required_testsやtest_commands、risk、design_gate等は固定。
レビュー済みの完全契約を既に持つ場合は同じschemaでdraft=falseとできるが上位確定は省かない。
reviewは対象のauthor_modelを必須とし、そのmodelとは異なるReviewerへ送る。
review-onlyの判断・指摘は署名されたrun証拠に保存し、canonicalの承認やNext Taskを代筆しない。
review-onlyで承認が得られた場合も、それだけで別のtaskのgame gateを解除しない。

## Model calls and authority

Qwenは既存llmクライアント、schema付きJSONで契約の2項目を出す。
上位Reviewerは構造化{verdict: APPROVED|FINDINGS|NEEDS_USER, reason: string}のみ返す。
承認はモデルのraw応答、実際に選んだprovider/model、packet hashへ結び付ける。
契約案の不採用は具体的reason付きでQwenへ返し、max_attempts以内で再作成する。
review-onlyのFINDINGS/NEEDS_USERは停止。必要な仕様判断や情報不足を自動承認しない。

Claudeは既存CLI --safe-mode --tools空 --print --no-session-persistence、JSON出力。
Codexは既存CLI exec --sandbox read-only --disable shell_tool --ephemeral、JSONL出力と構造化最終応答。
workspaceはrepoを公開せずpacketだけを置く独立ディレクトリ。保護規則を無効化しない。
カスタムMCP等が有効なCodex設定ではdoctorが確認対象を出し、自動呼出しは設定済み連携を
使わないよう制限した公式CLIの設定を使用する。承認の回避フラグは使わない。
モデルにコマンド実行・ファイル書込み・契約権限の変更を依頼しない。
出力不正、exit非0、timeout、usage欠損は証拠へ記録し、無条件に再送しない。

## State and lifecycle

campaign directoryはrepo外。startで設定bytesとhashを保存し、OS lockを保持する。
job index、attempt、state、cloud_calls、provider別usage、deadline、call recordsをatomic保存。
状態はREADY/DRAFTING/CONTRACT_REVIEW/RUNNING/RED_REVIEW/APPLYING/JOB_DONE/
PAUSED/NEEDS_USER/COMPLETE。各callは発行前に予算を予約して保存する。
応答保存済みなら再開時再利用。発行記録だけ残る不確実なcallは自動再送せずPAUSED。
PAUSEDからのresumeはdeadline/残りcall予算/既存証拠を再確認。期限を勝手に延長しない。
stopはファイルによる協調停止、進行中呼出しを無闇にkillしない。
起動したCLIのtimeoutだけ、その子プロセスツリーを既存v1のexecuteと同方式で終了する。
子Qwen runはmodel呼出し前にrunパスをcampaign stateへ保存。v1のresumeを使う。
APPLYINGからはv1 applyを再開し、次jobへ進む前にAPPLIEDを確認する。
ESCALATEDは自動再送しない。署名付き証拠を上位向けhandoffへ保存する。

## Inputs and isolation

read_list/contractのread_list/allow_edit/extra_read_contextと設計ファイルを列挙して読む。
全てsafe_pathでsymlink/reparse/秘密パスを拒否。送信直前にprovider cloud_readへ包含を確認。
契約レビューpacketには固定templateと草稿・入力ファイル全量を入れ、上限超過はtruncateせず停止。
Redレビューには候補差分の各ファイル全量、契約、全試行gate summaryと証拠hashを含める。
元入力hashを生成前後で比較し、変更されたjobは新campaignが必要。レビューした候補hashでRed承認を生成。
承認ファイルの署名metadataは実呼出しモデルからのみ構成、同モデル自己承認をしない。
v1が元入力・candidate・保護テスト・収集数と適用競合を再検査する。

## Quota and cost

strict_reserveは既存choose_providerで新鮮な5h/週情報とreserve条件を必須にする。
bounded_callsは不明枠でも明示call上限/期限内で実行する別モード。割合維持の保証をしない。
既知の枯渇はどちらでも新callを開始しない。非公開利用枠API・auth読取りは使わない。
auto選択は有効な余裕を優先し、不明時のfallbackは設定済みprovider順序のみ。
同モデルreview不可、リスク担当制約が予算優先度より強い。
rate limitエラーはPAUSEDにし、同じ呼出しの自動リトライ・reset credit使用はしない。
独立レビューは明示許可した別providerへ新callとして切替可能な設計にするが、自動fallbackはv2対象外。
report/status/doctorはLLM呼出し0。全call（失敗含む）と成功job数、tokens/成功jobを集計。
欠損usageはunknown。サブスク残量をtoken数やAPI金額へ換算しない。

## Required acceptance tests

契約の固定項目が改変不可、範囲外送信・symlink・出力過大・不正JSONが拒否される。
cloud call上限/期限/stop/既知枯渇で新callが0、予約済み不確実callを再送しない。
応答保存後の再開で重複課金しない、task path保存後の再開でv1 runを重複作成しない。
draft→契約レビュー→Qwen修正→Redレビュー→適用→次jobを模擬providerで無人完走。
異モデル要件、Red古いcandidate承認、source drift、apply途中停止を検証する。
review-onlyは署名済み判断を保存するがcanonical設計のStatusを変更しない。
実機でQwen+上位CLIの小規模smokeを行い、送信範囲・token証拠を保存する。

## Deliberately excluded

新フェーズの仕様をモデルが勝手に発明すること、ゲーム設計の自己承認、保護テスト改変、
無期限daemon、OS常駐登録、push/merge、API追加購入。最初の範囲・テスト設計の確定は上位が担う。
汎用的な保護文書の自動編集エンジンは追加しない。v1の検証境界を増やす案より、
重要判断のみ上位へ自動依頼する案を採用し、モデル呼出し回数と検証対象を抑える。

## Implementation contract clarification / Q1–Q12

この節が上記の概略より優先する。

### Q1 / Q4: exact manifest and budget

top-level exact keys:
`version, issuer, repo_root, agent_root, not_before, expires_at, max_seconds,
max_cloud_calls, max_local_calls, max_attempts, max_packet_bytes, max_output_bytes,
providers, quota_policy, jobs, apply`。
version=1(int)、issuerはユーザー権限を記録する非空文字列、applyはbool。
時間は有限UTC秒でnot_before < expires_at、max_secondsは1..86400。
cloud/local callは1..100、attemptは1..3。jobsは1..20件、idはASCII英数字-_の1..60文字。
cycle/task上限は固定jobs件数。stage上限はjobごとにdraft/reviewを最大attempt回、
runner1回（v1内部最大2fix＋fresh review）、Red review1回、completion review1回。
local call予約はjobのdraftごと1回、runner前に `2*(max_fixes+1)` 回分を保守的に予約する。
実消費が予約より小さくても予約を払い戻さず、不明消費を無制限にしない。
各stage timeoutはmin(親の残秒,300秒)。runner contract timeoutも残時間に縮小するが、
その値は上位契約レビュー前に固定する。runner全体は子process wall-clock timeoutで上限を守る。
開始期限/最大wallclockを満たさなければ子を開始しない。上限の変更は新campaignのみ。
stage回数はstateに保存した一意stepIDの集合でも有限と検査する。

provider dictのkeyはgpt/claude、value exact keys=`model,executable,cloud_read`。
modelはそれぞれgpt-/claude- prefixの非空文字列、executableは既存絶対ファイル。
cloud_readは厳密なrepo相対pathの一意リスト、上限200。sourceから暗黙に追加しない。
quota_policy exact keys=`mode,reserve_percent,snapshot`。
mode=bounded_calls/strict_reserve、reserve=0..100、snapshot=nullまたは絶対JSONパス。
snapshotはruntime読取情報でありauthorizationを上書きしない。
job common exact keys=`id,kind,instruction,read_list,reviewer,author_model`。
taskのみさらに`template,draft`。reviewer=gpt/claude/auto。draft bool。
author_modelは署名に使用する固定文字列。taskはlocal-qwen。
task templateは完全v1 contract。repo_rootはcampaignと一致し、risk hard_redは開始前に停止。
taskのtemplate全体はinitでvalidate_contract（将来allow_newが既に存在する場合を含め拒否）。
適用範囲は各templateのallow_edit/allow_newのみ。
job.read_listに加えtemplateのread_list/allow_edit/extra_read_context/designをpacketへ含める。

曖昧な権限を避けるためcommit/branch/push/merge/PR/deploy/他者message/reset/購入は常に対象外。
設定元pathをstateへ保存し、resume/各新stage時に元設定とコピーのhash一致を検査。
設定変更・縮小・削除は停止。stopファイルも各stage境界で確認。再許可は新manifest、新campaign。

### Q2 / Q3 / Q12: packet, provider, crash

packet exact keys=`version,job_id,stage,purpose,files,payload`。filesはpath→UTF8内容。
payloadはstage固有のcontractまたはcandidate/gates/previous_reviewで、scope由来のみ。
UTF8 JSONのbyte上限を送信前に検査。packetに本文/差分を入れる全pathをcloud_readへ照合。
proof directoryにpacket.jsonとrequest.jsonを先にatomic保存、stateのstepに
{status:INTENT,provider,model,packet_sha256,output_sha256:null,usage:null}を保存し予算予約。
INTENT保存直後からraw保存までは送達不明。再開ではUNKNOWN_DELIVERY停止し自動再送しない。
provider完了はraw.json（CLI exit/stdout/stderr/secondsと解析result/usage）をatomic保存、
そのhashをstateへ記録してDONEにする。INTENTかつraw存在なら解析/同一packet照合してDONEへ復旧。
DONE応答はhash確認して再利用。CLIの終了失敗はFAILEDとしてraw保存、同step再送不可。
request ID / thread IDは返ったものだけrawへ保持、無ければnull。provider側idempotencyは仮定しない。
UNKNOWN_DELIVERYから人ができるのはstatus/stop、証拠確認後の新manifestで再開始だけ。
新manifestで再送する場合には重複消費リスクを説明する。

Codex argvは `exec --ignore-user-config --sandbox read-only --disable shell_tool
--ephemeral --skip-git-repo-check --json -m MODEL -o ABS_OUTPUT -`。
ignore-user-configはカスタムMCP/skills/hookの暗黙context読取を避けるため。
execpolicy rules/admin policyは無効化せず、ignore-rules/bypassフラグは使用禁止。
stdinにpacketとJSON応答指示のみ。Claude argvは上記tools空方式。
実装中の実送信はユーザーの新規範囲承認がある場合だけ。通常テストはfakeのみ。

stateはatomicな単一snapshotを唯一の正とし、そこにevents配列を同居させる。
event indexはlen(events)と一致、前から1..N。別event logを権威にしない。
manifest hash/steps/raw hash/入力hash/job index/未知phaseをresume時に検査。
status/reportはreadonly、壊れたstateは成功表示せずexit4。

### Q5 / Q6: quota and immutable contract

autoの最初のprovider決定をstepへ固定する。クォータ不明時は設定順が既定、
発行後の失敗では別providerへ暗黙切替しない。review authorと同じmodelは候補から除外。
strict modeで条件を満たすproviderなしならPAUSED_QUOTA。bounded modeでも新鮮で有効な
既知windowがreserve以下なら呼出し禁止。古い値/欠損値ではreserve保証を主張しない。
call予約とprovider usageは別管理。quota refreshで予約call budgetを増やさない。
Qwen draft exact keys=goal/context。これ以外のキーを返したら失敗。templateの不変条件や
受入条件はそのまま上位へ渡し、上位にもtemplate改変権を与えない。
上位結果exact keys=verdict/reason。APPROVEDにも非空reason必須。
機械合否はv1証拠からのみ、意味的レビュー判定は実際の上位応答の署名でのみ記録する。

### Q7 / Q8 / Q9 / Q11: game gate and advancement

Design REQUIREDの制作/承認は既存D051の上位roleで行い、task開始前の条件とする。
review-onlyは前段の独立レビュー証拠を作るところまで。canonical承認反映と設計修正の
汎用自動編集は今回除外（安全な適用と役割署名の範囲を広げないため）。Q7の全自動化を
達成したとは報告しない。Reviewerはこの限定を承認するか依頼scopeを修正する。
taskはv1のhash-bound design_gateに加え、実repoのai_status implementでallowed確認。
High/MediumのOPEN指摘があるgame taskはSTOP_REVIEW。例外をモデルが作れない。
review-onlyは未承認でも実行可、異モデル承認の判定を署名保存する。
Next Task等の文章を自動的に権限へ変換しない。ROADMAP/REQUESTはjob入力として明示する。

v1はPython CLI `tools/task.py plan --contract ABS --runs-root ABS` をshell=Falseで実行。
planはモデルを呼ばない。plan結果からrunを得た後、run path/contract hashをstateへ保存し、
`run --run ABS` を開始。crashでrun記録が無い場合だけplanし直せる（古い未実行runは削除しない）。
runner応答はstdoutのJSONに加えread_stateでidentity/hash/phaseを確認、exit0/3だけ解釈。
runnerの履歴はresume時も既存runへ接続。ESCALATED/ROLLING_BACKは上位へ停止。
READYとAWAITING_REVIEWは機械合格であり正式完了ではない。
Redのみ候補全量＋契約＋gate証拠を上位へ送りAPPROVED応答に基づくversion1 approvalを作りapply。
apply=falseならREADY_TO_APPLYで停止。apply=trueならGreen/Yellowもv1 applyを明示起動。
APPLYINGは同じv1 applyへ復旧する。上位review packetに結び付けたcandidate hashを再検査。
APPLIED後は全riskで上位completion reviewを1回実施し、実応答APPROVEDだけJOB_DONE。
正式ゲームPhase完了のcanonical記録は別の既存Reviewerタスクであり、JOB_DONEと区別。
完了レビューFINDINGSはNEEDS_USERで停止、後続jobへ進めず証拠保存。自動rollbackもしない。
次jobは明示リストの次の1件のみ。全件JOB_DONEでCOMPLETE。

### Q10: concurrency, handoff, dry-run

campaign lock→v1 subprocess内部run lock→repo lockの順。親がv1のrepo lockを保持しない。
各job入力を初回固定し、new allow_new不存在も確認。draft/上位レビュー/runner開始前後と
apply前に入力hashを確認する。適用後はv1のsource/candidate証拠で自分の変更を区別する。
対象外dirtyファイルはv1 snapshotに含まれるが、autodevがcommit/reset/所有しない。
Ctrl-Cで親は中断記録を保存、起動した子process treeだけを終了してfinallyでlock解放。
host停止はOS lock解放に任せ、未知送達は上記規則で停止。
全終了経路でhandoffにreason/job/state/残budget/steps/runner path/next commandを保存。
stopファイルは次boundaryで停止（timeoutまで進行中callが続くことを明記）。
doctor/dry-runはmanifest検証と予定stageリストを出し、provider/sourceへ書込なし。
fake providerと使い捨てrepoでのE2Eはdry-runとは別コマンドのtestで行う。
exit0=complete/status正常、3=停止/判断待ち、4=検証/状態破損、5=lock競合。

## Review clarification (blocking points 1–5)

以下がphase、provider、lock、署名に関する唯一の規範であり、上の同名概略を置き換える。

### Phase table

全active状態からstop→PAUSED、quota不足→PAUSED_QUOTA、期限/予算不足/意味判断→NEEDS_USER、
送達不明→UNKNOWN_DELIVERY、既存game OPEN→STOP_REVIEW、破損→INVALIDの例外遷移を許可。
同phaseへのatomic保存は許可するが、予算を消費しないbusy retryは行わない。

| Phase | normal next | resume | exit |
|---|---|---|---|
| READY | DRAFTING / REVIEWING | 可 | 3 |
| DRAFTING | CONTRACT_REVIEW | 可、保存済応答再利用 | 3 |
| CONTRACT_REVIEW | DRAFTING / RUNNING | 可、attempt上限内のみ | 3 |
| RUNNING | RED_REVIEW / APPLYING / READY_TO_APPLY | 同じv1 runへ再接続 | 3 |
| RED_REVIEW | APPLYING / READY_TO_APPLY | 保存済応答再利用 | 3 |
| APPLYING | COMPLETION_REVIEW | 同じv1 applyへ再接続 | 3 |
| COMPLETION_REVIEW | JOB_DONE | 保存済応答再利用 | 3 |
| REVIEWING | APPROVED→JOB_DONE; FINDINGS/NEEDS_USER→NEEDS_USER | review-only、保存済応答再利用 | 3 |
| JOB_DONE | READY / COMPLETE | 次の明示jobだけ | 3 |
| PAUSED | 保存されたresume_phase | stop解除後、全制約再確認 | 3 |
| PAUSED_QUOTA | 保存されたresume_phase | fresh snapshot更新後のみ | 3 |
| READY_TO_APPLY | なし | terminal、apply許可は新campaign | 3 |
| NEEDS_USER | なし | terminal | 3 |
| UNKNOWN_DELIVERY | なし | terminal、自動再送なし | 3 |
| STOP_REVIEW | なし | terminal、game gate修正後は新campaign | 3 |
| INVALID | なし | terminal | 4 |
| COMPLETE | なし | readonly no-op | 0 |

statusは状態を変えず正常読取ならexit0。未知phaseはINVALIDとして扱う。
CONTRACT_REVIEWはAPPROVED→RUNNING、FINDINGS→上限内DRAFTING、NEEDS_USER/上限→NEEDS_USER。
RED_REVIEWはAPPROVEDだけAPPLYING/READY_TO_APPLY、他verdictはNEEDS_USER。
COMPLETION_REVIEWはAPPROVEDだけJOB_DONE、他verdictはNEEDS_USER。
stateにはphaseとresume_phase（activeまたはnull）を持たせる。
CLIはstart --manifest FILE --runs-root DIR、resume/status/stop/report --campaign DIR、
doctor --manifest FILE（alias dry-run）。resume時に元manifestを書き換えて権限を広げない。

### Shared lock and local reservation

canonical repo absolute pathをcasefoldしてSHA256し、信頼するagent_root/.task-locksに
autodev-repo-HASH.lockを置く。start/resume全期間でこのOS lockを保持し、
順序はshared autodev repo lock→campaign lock→v1 run lock→v1 repo lock。
通常v1とmanual editとの競合はv1のlock/hashで止める。status/stopはlock待ちをしない。
各v1 run/resume subprocess起動前に `2*(max_fixes+1)` local callを予約し返却しない。
既にterminalなv1を読み取るだけなら起動せず予約不要。計画済みrunのMODEL_RUNNING再送が
localで起きても、再起動予約の上限から逃れない。

### Exact provider process contract

Claude argv: `[EXE,"--safe-mode","-p","--model",MODEL,"--tools","",
"--no-session-persistence","--output-format","json","--json-schema",SCHEMA_JSON]`。
Codex argv: `[EXE,"exec","--ignore-user-config","--sandbox","read-only",
"--disable","shell_tool","--disable","apps","--disable","plugins",
"--disable","remote_plugin","--disable","multi_agent","--disable","image_generation",
"--disable","computer_use","--disable","browser_use","--disable","browser_use_external",
"--disable","browser_use_full_cdp_access","--disable","in_app_browser","--disable","hooks",
"--disable","memories","--disable","enable_mcp_apps",
"-c","web_search=\"disabled\"","-c","project_doc_max_bytes=0",
"--ephemeral","--skip-git-repo-check","--json","-m",MODEL,
"--output-schema",ABS_SCHEMA,"-o",ABS_OUTPUT,"-"]`。
プロジェクトのAGENTSはpacketで明示した内容だけ。execpolicy/admin policyは保持する。
shell=False、cwd=call固有の空directory（packet/schema/outputのみ、repoの下に置かない）。
envはPATH/SystemRoot/WINDIR/COMSPEC/PATHEXT/TEMP/TMP/USERPROFILE/HOMEDRIVE/HOMEPATH/
APPDATA/LOCALAPPDATA/PROGRAMDATA/PROGRAMFILES/PROGRAMFILES(X86)のみ親から継承し、
PYTHONIOENCODING=utf-8を固定。API key、proxy、CODEX_HOME、追加provider設定はコピーしない。
CLI自身の保存済み認証を使う。envの個別拡張は今回不可。
stdout/stderrはファイルへredirectし、pollで合計sizeとABS_OUTPUT sizeを0.1秒ごとに検査。
max_output_bytes到達/timeout/KeyboardInterruptで起動したprocess treeだけを終了、
常駐Qwenサーバ等の既存processをkillしない。上限到達後は先頭max bytesのみ証拠へ保存し、
成功にはしない。poll間の短い超過はあり得るが無制限読取り/メモリ確保はしない。
各CLIの正常exitと構造化結果が必要。Codexはturn.failed/errorが1つでもあれば失敗、
turn.completed＋final JSONのみ採用。返却schemaもローカルでexact検証する。
Codexで外部tool callイベントがあった結果は不採用とする。通常は上記無効化で発生しない。

### Exact review bindings

上位応答はexact `{verdict,reason}`、verdictは上記3値、reasonは非空文字列。
保存するsigned-review.jsonはexact
`{version:1,role:"Reviewer",provider,model,job_id,stage,packet_sha256,
response_sha256,subject_sha256,verdict,reason}`。
metadataのprovider/model/roleは実call record由来のみ。応答がこれらを代筆することはできない。
subject_sha256はcontractレビューでは完全契約JSON hash、review-onlyでは入力files hash、
Redではv1 candidate_sha256、completionでは下記completion subjectのJSON hash。

Red approvalはv1と同じexact7fields:
`approval_version:1,run_id,contract_sha256,candidate_sha256,verdict:"APPROVED",
reviewer_role:"Reviewer",reviewer_model:実call model`。
実call modelはagent configのupper_review_modelsにも含まれていなければapply不可。
approval作成前とapply直前のcandidate hashをsigned-review.subject_sha256へ照合。
v1側のapprove/manifest/source preflightをさらに実行する。

completion subjectはexact `{run_id,contract_sha256,candidate_sha256,
applied_files,gate_history,handoff_sha256}`。
applied_filesは全edit/new pathの現在bytes SHA256で、v1 candidate_manifestと照合する。
gate_historyは全試行のv1記録、handoff_sha256はAPPLIED handoffのbytes hash。
completion packetにはそのsubject、適用後ファイル全量、契約、v1テスト結果を含める。
review署名後もsubjectを再計算して一致する場合のみJOB_DONE。
testsはv1 gate実行の証拠を根拠とし、モデルの自己申告や追加の未実施テストを採用しない。
