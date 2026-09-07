# Qwen中心の開発runner

実行基盤は `C:/AIagent/agent`。ゲーム・通常CIはこの基盤やLLMなしで動く。
移行根拠はD056/D057、設計と独立承認は `python scripts/ai_status.py infra` で検査する。
ゲームのDesign Gateは従来どおり `python scripts/ai_status.py implement` で確認する。

## 日常の流れ

1. ReviewerがDesign Gateとリスクを決める。必要な詳細設計は別モデルが承認する。
2. Qwenで調査・草稿を作り、上位担当が必要な判断を確定する。信頼する実装契約をJSONで保存する。
3. runnerがQwen実装、機械検証、最大2回の修正を行う。Yellowのみfreshレビュー。Redは上位承認待ち、Hard Redは実装前に引継ぐ。
4. READYを確認し適用する。READYは機械検証合格であり、仕様全体の完了承認ではない。

```powershell
cd C:/AIagent/agent
python tools/task.py plan --contract C:/path/task.json
python tools/task.py run --contract C:/path/task.json
python tools/task.py resume --run C:/path/to/run
python tools/task.py status --run C:/path/to/run
python tools/task.py apply --run C:/path/to/run
python tools/task.py rollback --run C:/path/to/run
```

`run --contract` は新runを作る。再開は必ず `--run` を使う。
run directoryは対象repo外。AIagent自身を対象にする場合は `--runs-root C:/AIwolf/.infra-runs` 等を指定する。
途中停止でOSロックは解放される。生存プロセスからロックを奪わない。
`unlock --run` はOSロックを取れた場合にだけ診断記録を更新する。

## 契約

正確な形式は承認済み `Docs/ai/design/INFRA_QWEN_RUNNER_DESIGN.md` のImplementation contract clarification。
実行可能な最小例は `C:/AIagent/agent/tools/task_smoke.py` の契約生成箇所。
必須項目: goal/context、read_list、allow_edit/allow_new、protected、invariants、acceptance、
required_tests、test_paths/test_command、checks、limits、design_gate、risk、repo_root、task_id、contract_version。

`test_command` は固定pytest起動へ追加するオプション配列（例 `['-q']`）。
`checks` は人間・上位担当が確定したargv配列。AIwolfは `['python','scripts/check_docs.py']` を含める。
実行コマンドをQwenの返答から採用しない。tests/config/canonical docsは既定で保護する。
テストを変更する専門タスクの自動認定・mutation suite生成はv1対象外。

Design GateがREQUIREDなら設計ファイルのSHA256と独立署名を契約に含める。
これは信頼する担当者の記録を検証する仕組みで、署名者の身元を暗号学的に証明するものではない。
新しい重要判断をQwenが必要とする契約は実行へ進めない。

## 手入力を減らす

信頼する契約を複数用意したら、次を一度実行する。上位モデルの工程管理呼出しは不要。

```powershell
python tools/task_batch.py --contracts C:/path/a.json C:/path/b.json --ledger C:/path/batch.json --runs-root C:/path/runs --apply-ready
```

`--apply-ready` は列挙契約のGreen/Yellow READY候補を適用する明示的な指定。
Red承認待ち・Hard Red・失敗で停止し、証拠を保存する。承認・中断復旧後は同じコマンドで再開する。
ESCALATEDは自動再試行しない。上位担当が原因を解決し、修正契約と新ledgerで残りのタスクを実行する。
契約一覧や契約本文が変わったら新ledgerを使う。先行タスクの変更が必要な後続タスクは、この逐次実行で新しい入力からplanされる。
無期限daemonやクラウドへの自動送信は行わない。契約確定・上位承認が必要な時だけ担当者へ戻す。

## 利用枠とコスパ

```powershell
python tools/task_report.py --runs-root C:/path/runs
python tools/task_report.py --runs-root C:/path/runs --quota quota_snapshot.json --reserve 20
```

集計と推奨はローカル算術のみ。GPT/Claude/Qwen呼出しは0。
`--reserve 20` は両利用枠を20%残す比較。今週の基盤投資に全枠を使う判断なら明示的に0を指定できる。
GPT/Claudeの5時間・週枠の両方が新鮮で有効な場合に、残す分を引いた余裕の大きい側を推奨する。
リスクと独立レビュー要件が優先される。推奨自体はクラウド呼出しの許可ではない。

quota JSONはprovider別に `observed_at`（UTC Unix秒）、`windows` 配列の
`duration_minutes`（300/10080）、`used_percent`、`resets_at` を持つ。
デフォルト15分より古い値、既に過ぎたreset、欠けた枠、重複・不正値はunknown。
reset予定時刻だけで「回復済み」とは判断しない。プラン変更後は古い情報を捨てて取得し直す。
2026-09-07のCodexアプリ取得値はPro、通常モデルでは週枠のみ。Sparkの5時間枠を通常モデルへ流用しない。
Proには複数の利用枠水準があるため、plan名だけから倍率を決めない（[公式利用枠説明](https://learn.chatgpt.com/docs/pricing)）。
Claudeの利用枠を自動取得する接続は未実装。認証情報や非公開エンドポイントを使って取得しない。
Codexの現在値はアプリの利用枠ツールから記録できるが、standalone runnerから同ツールは呼べない。
したがって情報不明の場合は、ローカル処理を続け、上位が必要な作業はhandoffで止める。

レポートは失敗・再試行分も含む入出力トークンと、機械検証完了タスクあたりの消費を集計する。
API金額をサブスク料金と混同しない。欠損usageからトークン数を捏造しない。
`--cloud-usage FILE.jsonl` で実測した上位モデルのタスク単位記録も入力できる。
各行は provider/input_tokens/output_tokens/accepted/seconds。acceptedは集計上の完了ラベルで、正式承認記録とは別。
基盤作成の一度きりの投資はrunner日常タスクとは別に記録し、実運用の削減率とは分ける。

## 検証と証拠

```powershell
python tools/task_smoke.py
python -m pytest tests -q
```

run内にcontract、base、候補、元入力・候補のhash、rawモデル応答、使用量、pytest収集一覧、
JUnit、stdout/stderr、適用前後journal、backup、handoff.jsonを保存する。
変更前の収集が失敗したら開始しない。変更後にbaselineテストが消えたら合格させない。
モデル応答の途中切れ、保護ファイル改変、古い入力は失敗扱い。
適用・rollbackはリポジトリ単位で排他し、後から入ったユーザー変更を上書きしない。

候補コピーはOSのsecurity sandboxではない。テストはローカルユーザー権限で動く。
信頼できない任意コードの隔離実行環境としては使わない。
devとgameは同時起動しない。開発中の連続処理では共有devサーバを使い、終わったら停止できる。
ソース・モデルのコミット、push、mergeはrunnerが自動で行わない。
