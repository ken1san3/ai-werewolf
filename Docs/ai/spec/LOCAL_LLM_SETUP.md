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

## サーバ

OpenAI 互換 API を `127.0.0.1:8080` で提供する。

```bat
cd C:\AIagent\agent
python tools\doctor.py   :: 設定とパスの健全性チェック
serve.bat                :: dev プロファイル
serve.bat game           :: game プロファイル
serve.bat --list         :: プロファイル一覧
```

**常駐させない。** 使うときだけ起動して `Ctrl+C` で落とす。
dev は RAM 約18GB と VRAM 約5GB を握るため、起動したままだと他の作業を圧迫する。

**したがってツールは「サーバが落ちている」状態を必ず取りうる。**
呼び出しが失敗したら、圧縮を諦めて生のまま進む。止まらない。

## プロファイル

8GB VRAM を共有するため **dev と game は同時に起動できない。**
ゲームを動かしながら開発補助を使うことはできない。

| profile | モデル | 用途 |
|---|---|---|
| dev | Qwen3.6-35B-A3B UD-Q4_K_XL | 開発補助。MoE エキスパートを CPU/RAM へ逃がす |
| game | Qwen3.5-9B Q4_K_M | Phase 4 以降のゲームエージェント。GPU 全載せ |

起動フラグ（`config.toml` より）:

```
dev  : -ngl 99 --n-cpu-moe 999 -fa 1 -c 32768 --jinja
       --temp 0.7 --top-p 0.8 --top-k 20 --presence-penalty 1.5 --min-p 0.0
game : -ngl 99 -c 8192 --jinja
```

`--n-cpu-moe 999` が dev の要点。35B MoE のエキスパートを CPU/RAM 側へ逃がすことで
8GB VRAM に収めている。

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
