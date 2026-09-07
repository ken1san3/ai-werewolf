# 有限キューの無人開発

入口: `python scripts/ai_status.py infra`。D056/D057のQwen runnerをD058で連結する。
信頼する上位担当が、設計済みの小さな実装契約と送信範囲を一度manifestへまとめる。
以後、各工程で人間がチャットを打つ必要はない。

`Qwenによるgoal/context整理 → 独立上位の契約確認 → Qwen実装・機械テスト・上限内修正
→ Redの上位レビュー → 適用 → 上位完了確認 → 次の明示job`

レビューだけのjobも登録できる。最大20 job、最大24時間の有限実行である。
ゲームPhaseの詳細設計を新たに発明・承認する機能はない。既存のOPEN指摘や未承認Design Gateが
あるゲーム実装は停止する。ゲームのcanonical文書を自動でAPPROVEDにしない。

## 操作

### まとめて起動する

ルートの `Run-Autodev.cmd` が起動用。初回に `autodev.example.json` を
`autodev.local.json` としてコピーし、manifestを**実在する承認済みcampaign設定**へ変更する。
その後はダブルクリックでdoctor→start→reportまで1回実行する。終了画面はキーを押すまで残る。
Qwen devサーバと上位CLIのログインは事前に用意する。

継続する場合は、表示されたパスを使って設定を `{"campaign":"C:/.../campaign-..."}` にする。
するとstatus→必要な場合だけresume→reportを実行する。完了・要判断のterminalは再実行しない。
**manifest設定のまま再起動すると新しいcampaignを作る**。継続にはcampaign設定を使う。
設定ファイルはGit管理外で、スクリプトはその内容・期限・予算を変更しない。
新しいゲームタスクのmanifestは設計と範囲を確定した上位担当が用意する。見本はゲーム実行許可ではない。

```powershell
.\Run-Autodev.cmd --check
.\Run-Autodev.cmd --manifest "C:/work/approved-campaign.json"
.\Run-Autodev.cmd --campaign "C:/.../campaign-..."
```

`--check`はdoctor/statusだけを実行し、LLMを呼ばない。
任意の作業ディレクトリから `python C:/AIwolf/scripts/run_autodev.py` でも利用できる。
ラッパーは出力を逐次表示する。start直後に表示されるcampaignパスから実行中の記録を参照できる。
stopの自動解除、常駐、定期再送はしない。既存CLIの停止理由とexitを保持する。

### 個別コマンド

Python、既存AIagent v1、起動済み共有Qwen devサーバ、利用する上位CLIの保存済みログインが必要。
manifestの`agent_root`は例として `C:/AIagent/agent`。実行記録はrepo外に置く。
上位モデルのCLIには、`.cmd`ではなくインストール済みネイティブ`.exe`の絶対パスを指定する。
作業ディレクトリは `C:/AIwolf`。

```powershell
python scripts/autodev.py doctor --manifest C:/work/campaign.json
python scripts/autodev.py start --manifest C:/work/campaign.json
python scripts/autodev.py status --campaign "<表示されたcampaign絶対パス>"
python scripts/autodev.py report --campaign "<campaign絶対パス>"
python scripts/autodev.py stop --campaign "<campaign絶対パス>"
python scripts/autodev.py resume --campaign "<campaign絶対パス>"
```

`doctor`と`dry-run`は同義。静的な入力・設計確認のみで、通信、ゲーム変更、実行記録作成を行わない。
実行時の残量、競合、ゲームGateの成功まで保証するものではない。
startは開始直後にcampaignパスのJSONを1行、停止・終了時に結果JSONを1行出す（JSON Lines）。
resumeは終了結果1行。status/report/doctorは単一のJSONを出す。
exit 0=完了または正常読取り、3=停止/要対応、4=不正入力、5=lock競合。
既定の保存先は `%LOCALAPPDATA%/AIwolf/autodev`。`--runs-root`でrepo外の別場所を指定できる。

stopは次の境界で停止する。実行中の通信を即座に取り消したとは扱わない。
再開するときはcampaign内の`stop`ファイルを削除してからresumeする。
PAUSED/PAUSED_QUOTAは同じcampaignへ再接続する。適用中断は同じv1 journalで復旧する。
期限や予算は再開しても増えない。NEEDS_USER、UNKNOWN_DELIVERY、STOP_REVIEWは自動再開しない。
特に送達不明のクラウド要求を自動再送しない。応答保存済みの段階は再利用する。

## manifestを用意する担当へ

スキーマは `scripts/autodev_lib/policy.py` のexact検証、規範は
`Docs/ai/design/INFRA_AUTONOMOUS_DESIGN.md`。余分なキー、重複JSONキー、NaNは拒否する。
実行可能な合成例の生成コードは `scripts/autodev_smoke.py`。これはクラウドを実際に呼ぶ検証用である。

| 項目 | 内容 |
|---|---|
| version / issuer | 1 / 許可の根拠を示す文字列。文字列だけで外部送信の許可が得られるわけではない |
| repo_root / agent_root | 信頼するrepoと既存AIagentの絶対パス |
| not_before / expires_at | Unix秒。元manifestも保存コピーも実行中に書き換えない |
| max_seconds | 1〜86400。実行開始からの総時間上限 |
| max_cloud_calls / max_local_calls | 各1〜100。失敗・中断の予約も返却しない |
| max_attempts | 契約の整理・差戻し回数、1〜3 |
| max_packet_bytes / max_output_bytes | 入力packet / subprocess出力の上限 |
| providers | gpt / claudeごとのmodel、executable、cloud_read（許可済み相対パス一覧） |
| quota_policy | mode、reserve_percent、snapshot（絶対パスまたはnull） |
| jobs | 順番を固定したtask / reviewの配列 |
| apply | trueならレビュー後適用、falseならREADY_TO_APPLYで停止 |

job共通キーは `id, kind, instruction, read_list, reviewer, author_model`。
reviewerは`gpt`、`claude`または`auto`。author_modelは実際の作者の明示モデルID。
同じモデルによる自己レビューを除外する。taskは `template, draft` を加え、author_modelは`local-qwen`。
templateはD057の完全なv1契約。Qwenが変更できるのはgoal/contextだけである。
テスト・acceptance・invariants・編集範囲・riskをQwen自身に緩めさせない。
上位へ渡る全ファイルをprovidersのcloud_readへ明示する。不足すれば停止する。
テストを含め送信許可が必要なファイルを、許可なしに一覧へ追加しない。
共有Qwen呼出には追加の予約があるため、local予算にはdraftとv1再開分の余裕を持たせる。

## 利用枠と効率

`strict_reserve`は15分以内の5時間枠・週間枠の両方が必要で、指定残量を守れるproviderだけを選ぶ。
`bounded_calls`は不明な残量でも明示した呼出数以内で実行する。残量の割合は保証しない。
どちらも新鮮な測定で不足が判明したproviderは使用しない。autoなら独立性と送信範囲が適合する
候補から余裕のあるproviderを選ぶ。失敗後の別providerへの自動再送はしない。
Pro変更前の枠を推測で流用しない。スナップショットはD057の形式:

```json
{"gpt":{"observed_at":0,"windows":[{"duration_minutes":300,"used_percent":0,"resets_at":0},{"duration_minutes":10080,"used_percent":0,"resets_at":0}]}}
```

これは形式例であり、有効な残量ではない。観測値と時刻は実取得の値に置き換える。
Claudeを観測できなければ不明のまま扱う。report自体はCLIへもLLMへも問い合わせない。
reportはQwenと上位の入力・出力token、失敗・再試行を含む記録、完了jobあたりtokenを集計する。
予約呼出数と実測tokenは別物。不明値を0扱いせず、月額契約をAPIドル単価に換算しない。
小さなjobと大きなjobの単純なtoken比較を性能順位としない。

## 証拠と停止後

campaignにmanifest、state、handoff、calls内のpacket/response/署名、jobsの完了receipt、runsのv1証拠を残す。
レビューは実行モデルと対象hashに結び付ける。入力や候補が変われば保存済み承認を再利用しない。
handoffの理由とnext_commandから次の担当が再開できる。
自動commit/push、利用枠リセット、課金、恒久daemonは行わない。
新たなゲーム設計や意味的指摘が必要な時点では、上位担当がその判断を済ませて次の有限キューを作る。
