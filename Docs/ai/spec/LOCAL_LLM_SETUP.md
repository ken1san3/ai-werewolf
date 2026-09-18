# ローカルLLM 開発補助環境の構成記録

**このプロジェクトはこの環境に依存しない。**
`server/aiwolf_core` は LLM 無しで完全にテストできる（Design invariant 10）。
ここに書くのは開発補助として使っているローカルLLM環境の**記録**であり、
リポジトリの動作要件ではない。環境が無くても実装・テスト・CI はすべて通る。

用途と禁止事項は `decisions/D034_LOCAL_LLM_DEV_ASSIST.md`。
この文書は「どう組んであるか」だけを扱う。

正本は当該マシンの `C:\AIagent\agent\README.md`。
ここはマシンが失われたときに再構築できるようにするための複製である。

## 置き場所

リポジトリの外。`C:\AIagent`。

```
C:\AIagent\
  llama-server.exe ...        llama.cpp CUDA 12.4 ビルド
  agent\
    config.toml               このマシンの設定（モデルのパス・ポート・起動フラグ）
    serve.bat                 サーバ起動
    lib\                      conf / llm クライアント / serve 本体
    tools\                    doctor / summarize / ask
    projects\                 プロジェクトごとの地図（aiwolf.toml など）
```

`agent\` 以下はプロジェクトに依存しない。特定リポジトリの話は `projects\` にだけ書く。

## モデル配置（2026-09-18 実ファイル確認）

ユーザー指定により、今後のモデル比較・provider設定・実行スクリプトは
`C:\models\` 配下を基準とする。以下はこのhostの配置記録であり、モデル採用や
実行許可、ロード可否・品質・性能の検証結果ではない。

| モデル / 量子化 | GGUF絶対パス | サイズ（bytes） |
|---|---|---:|
| Qwen3.5-9B / Q4_K_M | `C:\models\Qwen3.5-9\Qwen3.5-9B-Q4_K_M.gguf` | 5680522464 |
| Llama 3.1 8B Instruct / Q4_K_M | `C:\models\Llama-3.1-8B-Instruct\Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` | 4920739232 |
| Gemma 3 12B Instruct / IQ4_XS | `C:\models\Gemma-3-12B-Instruct\google_gemma-3-12b-it-IQ4_XS.gguf` | 6550761504 |
| Gemma 3 12B Instruct / Q4_K_M | `C:\models\Gemma-3-12B-Instruct\google_gemma-3-12b-it-Q4_K_M.gguf` | 7300575264 |
| Gemma 3 12B Instruct / Q4_K_S | `C:\models\Gemma-3-12B-Instruct\google_gemma-3-12b-it-Q4_K_S.gguf` | 6935130144 |
| Ternary Bonsai 2 27B / PTQ1_0 | `C:\models\Ternary-Bonsai-2-27B\Ternary-Bonsai-2-27B-PTQ1_0.gguf` | 5946648928 |

Gemmaには3候補がある。比較時は量子化と完全なファイル名を明示し、
ディレクトリ内の先頭ファイルやワイルドカードによって暗黙選択しない。
この一覧だけから、各モデルが現在のllama-serverで動作すると推定しない。

`C:\AIagent\agent\config.toml` の既存Qwen設定は維持する。
新モデルを試す際は既存Qwenの設定を上書きせず、別profileまたは一時設定で切り替える。
新profileや一時設定は今回作成していない。既存の起動手順・過去の測定記録は維持する。

再確認用（PowerShell）:

```powershell
Get-ChildItem -LiteralPath 'C:\models\Gemma-3-12B-Instruct' -Filter *.gguf |
    Select-Object FullName, Length
Get-ChildItem -LiteralPath 'C:\models\Ternary-Bonsai-2-27B' -Filter *.gguf |
    Select-Object FullName, Length
```

照合範囲はファイルの存在・名前・サイズ。GGUF本文読取、hash測定、provider起動は未実施。
証拠: `Docs/ai/handoffs/tasks/T423_LOCAL_MODEL_PATHS.md`。

## サーバ

OpenAI 互換 API を `127.0.0.1:8080` で提供する。

```bat
cd C:\AIagent\agent
python tools\doctor.py   :: 設定とパスの健全性チェック
serve.bat                :: dev プロファイル
serve.bat game           :: game プロファイル
serve.bat --list         :: プロファイル一覧
```

**PowerShell では `.\serve.bat` と書く。** PowerShell はカレントディレクトリを
実行パスに含めないため、`serve.bat` だけでは `CommandNotFoundException` になる。
cmd.exe ではどちらでも動く。

起動コマンドを手打ちしない。`config.toml` と二重管理になり、
モデルやフラグを変えたときに古い設定で起動しても失敗として現れない。
`serve.bat` は exe とモデルの存在確認をし、起動時に profile / モデル名 / URL を表示する。
dev と game は同時起動できないので、どちらを立てたかが表示されるのは実用上重要である。

開発の連続処理中は共有devサーバを使い、終了後は `Ctrl+C` で停止できる。
現行設定の評価資料ではpeak VRAM 6949 MiB。旧設定の約5GBという見積りは流用しない。

**したがってツールは「サーバが落ちている」状態を必ず取りうる。**
圧縮呼び出しが失敗したら生のまま進み、失敗と証拠を作業記録へ残す。

## プロファイル

8GB VRAM を共有するため **dev と game は同時に起動できない。**
ゲームを動かしながら開発補助を使うことはできない。

| profile | モデル | 用途 |
|---|---|---|
| dev | Qwen3.6-35B-A3B UD-Q4_K_XL | 開発補助。MoE エキスパートを CPU/RAM へ逃がす |
| game | Qwen3.5-9B Q4_K_M | Phase 4 以降のゲームエージェント。GPU 全載せ |

起動フラグ（`config.toml` より）:

```
dev  : -ngl 99 --n-cpu-moe 35 -fa 1 -c 32768 --jinja
       --temp 0.7 --top-p 0.8 --top-k 20 --presence-penalty 1.5 --min-p 0.0
game : -ngl 99 -c 8192 --jinja
       --chat-template-kwargs '{"enable_thinking":false}'
```

`--n-cpu-moe 35` が dev の要点。35B MoE のエキスパートを CPU/RAM 側へ逃がすことで
8GB VRAM に収めている。

game profile も structured output の短い上限内で推論だけに token を使い切らないよう、
chat template の thinking を明示的に無効化する。Phase 4 実機確認では、この一項目だけを
変えた同一 request が非空かつ schema-valid な JSON content を返した。

## 使われたかを確認する

`lib/llm.py` が呼び出しごとに `C:\AIagent\agent\usage.jsonl` へ1行書く。
**自己申告ではなく実際の記録**である。

```bat
python tools\usage.py            :: 直近20件
python tools\usage.py --today    :: 今日の分だけ
```

記録が無ければ「一度も使われていません」と出る。
エージェントの報告と突き合わせるための客観記録であり、
「使ったつもり」「言い忘れ」の両方を検出できる。

## 実測値（2026-08-30 / RTX 3070 Ti 8GB + i7-12700K + RAM 32GB）

| profile | 生成 | プロンプト処理 |
|---|---:|---:|
| dev | 45.8 t/s | 336 t/s |
| game | 88.9 t/s | 2948 t/s |

dev はプロンプト処理 336 t/s なので、入力 5,000 トークンで前処理 15秒、
20,000 トークンで 60秒。**それを超える入力は渡す前に機械的に削る**（D034）。

game の 88.9 t/s は Q8（9体同時稼働時の生成速度）の初期見積もりになるが、
9体分の KV キャッシュを載せた実測は Phase 5 で別途行う。

## ツール

```bat
python tools\summarize.py --kind test -f test.log
python -m pytest > test.log 2>&1 && python tools\summarize.py --kind test -f test.log

python tools\ask.py "変更の要点を5行で" -f C:\AIwolf\server\network\session.py
python tools\ask.py "Phase 2.2 の残課題を挙げて" -p aiwolf -d current -d review_inbox
```

`-p aiwolf` は `projects\aiwolf.toml` を指す。そこに repo のパスと、
読ませたい文書のキー（`current` / `review_inbox` / `design` など）が定義されている。
文書の内容は複製せず、パスだけを持つ。

## 再構築するとき

1. llama.cpp の Windows CUDA ビルドを `C:\AIagent` へ置く
2. モデルを `C:\models\` へ置く（上表のファイル名）
3. `agent\config.toml` を上のフラグで作る
4. `python tools\doctor.py` で健全性を確認する
5. プロジェクトを足すときは `projects\<名前>.toml` を作る。
   repo のパスと文書のキーだけを書き、内容は複製しない
