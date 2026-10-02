"""Render REBUILD_MANIFEST.md (Japanese) from the TSV so that every list is generated, not hand-copied."""
import csv
import collections
from pathlib import Path

HERE = Path(__file__).resolve().parent
rows = list(csv.DictReader(open(HERE / "rebuild_manifest_main_5a5635f.tsv", encoding="utf-8"), delimiter="\t"))
by = collections.defaultdict(list)
for r in rows:
    by[r["disposition"]].append(r)


def lst(items, fmt=lambda r: f"`{r['path']}`"):
    return "\n".join(f"- {fmt(r)}" for r in items)


def select(disposition, prefix=None, pred=None):
    out = [r for r in by[disposition] if (prefix is None or r["path"].startswith(prefix))]
    if pred:
        out = [r for r in out if pred(r["path"])]
    return sorted(out, key=lambda r: r["path"])


def total(items):
    return sum(int(r["lines"]) for r in items)


keep = by["KEEP"]
edit = by["EDIT"]
delete = by["DELETE"]

docs_top = select("DELETE", "Docs/", lambda p: p.count("/") <= 2)
dirs = ["handoffs", "tasks", "design", "failures", "history", "prompts", "review_archive", "roadmap_archive", "roles"]
dir_rows = {d: select("DELETE", f"Docs/ai/{d}/") for d in dirs}

md = f"""# 再構築マニフェスト（何を残し、何を消すか）

作成: 2026-10-02 / 作成者: Claude（停滞分析セッション）
基準: `main` = `5a5635ff52d5f38cfcb08f80ddce69d18a290259`（2026-09-19）
全ファイルの機械可読な一覧: `rebuild_manifest_main_5a5635f.tsv`（path / disposition / lines / reason）
生成スクリプト: `make_manifest.py`（同じ規則で再生成・検証できる）

## 0. 前提

- 新しい作業ブランチは `main` から作る。ゲーム本体・サーバ・プロトコル・役職データは、mainとexperimentブランチで**完全に同一**（`git diff main experiment -- server protocol content` が空）。
- experimentブランチの102コミット（9/19〜10/2）は**新ブランチへ持ち込まない**。タグで保存する（§6）。
- 「消す」は新ブランチの作業ツリーから外すという意味。Gitの履歴とタグには残り、いつでも復元できる（§7）。
- 未追跡のローカルファイル（`C:\\AIwolf\\logs` など）はGitの対象外。**このマニフェストでは消さない**（§6）。

## 1. 集計

| 区分 | ファイル数 | 行数 | 意味 |
|---|---:|---:|---|
| KEEP（そのまま残す） | {len(keep)} | {total(keep):,} | 変更しない |
| EDIT（残して書き換える） | {len(edit)} | {total(edit):,} | §3の内容に書き換える |
| DELETE（消す） | {len(delete)} | {total(delete):,} | 新ブランチから `git rm`。タグに保存 |
| 合計 | {len(rows)} | {total(rows):,} | mainの追跡ファイル全部 |

**検証済み**: mainを書き出し、DELETEを適用したツリーで、残したテストを実行 → `204 passed, 661 subtests passed in 28.52s`（Windows / Python 3.13.3）。
残したファイルから、消したパッケージ（`ai_client` / `scripts`）へのimportは0件。

## 2. 残すもの（KEEP {len(keep)}件）

### 2.1 ゲームサーバ（{len(select('KEEP', 'server/'))}件）
{lst(select('KEEP', 'server/'))}

### 2.2 プロトコル（{len(select('KEEP', 'protocol/'))}件）
{lst(select('KEEP', 'protocol/'))}

### 2.3 役職・プリセットのデータ（{len(select('KEEP', 'content/'))}件）
{lst(select('KEEP', 'content/'))}

### 2.4 テスト（{len(select('KEEP', 'tests/'))}件）— サーバ・プロトコル・ネットワークの検証
{lst(select('KEEP', 'tests/'))}

### 2.5 仕様書（{len(select('KEEP', 'Docs/ai/spec/'))}件）
{lst(select('KEEP', 'Docs/ai/spec/'))}

### 2.6 ゲームルールの決定記録（{len(select('KEEP', 'Docs/ai/decisions/'))}件）— DESIGN.md が理由として参照している
{lst(select('KEEP', 'Docs/ai/decisions/'))}

### 2.7 ルート設定（{len([r for r in keep if '/' not in r['path']])}件）
{lst(sorted([r for r in keep if '/' not in r['path']], key=lambda r: r['path']))}

## 3. 残して書き換えるもの（EDIT {len(edit)}件）

| ファイル | 書き換え内容 |
|---|---|
| `AGENTS.md` | 全面置換。新しい1ページの規則（`AGENTS_NEW.md`）にする。旧規則（6役割・Design Gate・独立レビュー連鎖・狭い読込み範囲）は使わない |
| `README.md` | 現状（再構築中）、起動方法（サーバ・llama-server・AIエージェント）、構成、製品要件（初期構想書から引き継ぐ項目）に書き直す |
| `pyproject.toml` | `packages.find.include` を `["server*", "ai_agent*"]` に。pytest marker は `completion` だけ残し `windows_private` を削除 |
| `tests/conftest.py` | completion marker の対象を `tests/test_phase2_completion.py` の1件だけにする |
| `.gitignore` | ゲーム出力先 `games/` を追加 |
| `Docs/ai/TEST_POLICY.md` | 冒頭の「Phase 6 テストアーキテクチャ」節だけ削除。サーバ側の節はそのまま |

## 4. 消すもの（DELETE {len(delete)}件）

### 4.1 ルートの文書・設定（{len([r for r in delete if '/' not in r['path']])}件）
{lst(sorted([r for r in delete if '/' not in r['path']], key=lambda r: r['path']), lambda r: f"`{r['path']}` — {r['reason']}")}

### 4.2 GitHub設定（{len(select('DELETE', '.github/'))}件）— Actionsは停止中。必要になったら簡単なCIを新設
{lst(select('DELETE', '.github/'))}

### 4.3 AIクライアント全体（{len(select('DELETE', 'ai_client/'))}件、{total(select('DELETE', 'ai_client/')):,}行）
AIの判断・発話・LLM呼び出し・GPU待ち行列・Phase 6の会話状態はすべて作り直す。
`ai_client/network` と `ai_client/world`（通信と受信状態の管理、約4,700行）は単体では健全だが、新しいAIは使わずに動く（試作で確認）。
再接続や別PC対応が必要になった時点で、タグから復元して再評価する。
{lst(select('DELETE', 'ai_client/'), lambda r: f"`{r['path']}`（{r['lines']}行）")}

### 4.4 スクリプト（{len(select('DELETE', 'scripts/'))}件、{total(select('DELETE', 'scripts/')):,}行）
{lst(select('DELETE', 'scripts/'), lambda r: f"`{r['path']}`（{r['lines']}行）")}

### 4.5 テストとfixture（{len(select('DELETE', 'tests/'))}件、{total(select('DELETE', 'tests/')):,}行）— 消したAIクライアント・スクリプトを対象とするもの
{lst(select('DELETE', 'tests/'), lambda r: f"`{r['path']}`（{r['lines']}行）")}

### 4.6 `Docs/` 直下と `Docs/ai/` 直下の文書（{len(docs_top)}件）
{lst(docs_top)}

### 4.7 仕様書フォルダのうち消すもの（{len(select('DELETE', 'Docs/ai/spec/'))}件）
{lst(select('DELETE', 'Docs/ai/spec/'), lambda r: f"`{r['path']}` — {r['reason']}")}

`AI_WEREWOLF_CODEX_HANDOFF.md` は最初の構想書です。製品としての要件（ターン制にしない、全発言に反応しない、
古くなった発言は取り消す、人格、入力中表示、狼の秘密会話、人間参加・複数PC・異種LLM・MOD）は新しいREADMEへ移します。
AIの内部設計の節（§12〜§22、特に「LLMへ全履歴を丸投げしない」「思考と会話を分離する」）は、今回の停滞の出発点になった前提なので、
そのまま引き継がず、実ゲームで必要と分かった時点で採否を決めます。

### 4.8 決定記録のうち消すもの（{len(select('DELETE', 'Docs/ai/decisions/'))}件）— 運用・AIクライアント・Phase 6の決定
{lst(select('DELETE', 'Docs/ai/decisions/'))}

### 4.9 運用の記録フォルダ（全件消す）
| フォルダ | ファイル数 | 行数 | 中身 |
|---|---:|---:|---|
""" + "\n".join(
    f"| `Docs/ai/{d}/` | {len(dir_rows[d])} | {total(dir_rows[d]):,} | "
    + {"handoffs": "作業報告", "tasks": "作業指示（task packet）", "design": "Phase 3.1以降の詳細設計（AI側）",
       "failures": "失敗記録", "history": "退避した運用記録", "prompts": "役割別プロンプト",
       "review_archive": "レビュー記録", "roadmap_archive": "旧ロードマップの退避", "roles": "役割定義"}[d] + " |"
    for d in dirs
) + f"""

各ファイル名は TSV にすべて載っています。

## 5. 新しく作るもの（NEW）

| パス | 内容 |
|---|---|
| `AGENTS.md`（置換） | `AGENTS_NEW.md` の内容 |
| `ROADMAP.md` | 新しいロードマップ（Stage 0〜4）。短く保つ |
| `WORKLOG.md` | 1回の変更ごとに1〜3行：日付・変更・回したゲーム（seed）・観察・次の一手 |
| `ai_agent/` | 新しいAIエージェント。試作（`analysis/stagnation-2026-10-02/spike/spike_live_v2_improved_prompt.py`）を分割して正式化 |
| `tests/test_ai_agent_*.py` | プロンプト組立て・フィルタ・機械チェックの単体テスト、偽LLMで1ゲーム完走するスモークテスト |
| `Docs/analysis/2026-10-02/` | 今回の分析資料（`analysis/stagnation-2026-10-02/` 一式。private な実ゲーム本文は含まない）。参考資料で、コードからは参照しない |
| サーバの変更（Stage 1） | 人狼に相方を通知する処理とテスト。`DESIGN.md` の該当箇所も更新 |

## 6. Git以外とブランチ・タグの扱い

**タグ（消す前に必ず作る。origin へ push する：ユーザー決定済み）**

| タグ | 対象 | コミット |
|---|---|---|
| `archive/2026-10-02-main` | main | `5a5635f` |
| `archive/2026-10-02-experiment` | experiment/speech-act-kind-first-20260919 | `ee80895` |
| `archive/2026-10-02-codex-t575` | codex/context-audit-t575 | `4df84c6` |
| `archive/2026-10-02-capture-catchup` | fix/capture-catchup-20260921 | `b425cd1` |

既存の `autodev-freeze-2026-09-09` はそのまま。既存ブランチは消さない（タグ作成後に消すかどうかはユーザーが後で決める）。
新ブランチ `rebuild/simple-agent` も origin へ push する。main と他の既存ブランチは変更しない。

**未追跡のローカルファイル（`C:\\AIwolf` 内、37項目）**

| 対象 | 扱い |
|---|---|
| `logs/`（実ゲーム・実験の生ログ、private原文を含む） | **消さない。** 必要ならユーザーがリポジトリ外へ移動して保管 |
| `logs/t448-quality-cycles/ci-capture-worktree`（Gitのworktree） | 消さない。不要になったらユーザーが `git worktree remove` |
| `.pytest-*`、`.review-*`、`.tmp/` などの一時フォルダ | AIは消さない。不要と判断したらユーザーが削除 |

**作業場所**: 既存の `C:\\AIwolf` で、`main` から作った `rebuild/simple-agent` ブランチへ切り替える。
追跡ファイルに未コミットの変更が無いことは確認済み（2026-10-02）。未追跡ファイルは切り替え後もそのまま残る。
（別フォルダにworktreeを作る方法もあるが、Codexのサンドボックスが作業フォルダ外への書き込みを拒否する可能性があるため、既存フォルダでの切り替えを既定にした）

## 7. 復元方法

```bash
git checkout archive/2026-10-02-main -- ai_client/network ai_client/world
```

```bash
git show archive/2026-10-02-experiment:Docs/ai/CURRENT_STATE.md
```

ファイル単位・フォルダ単位で、いつでも取り出せます。履歴の書き換えはしないので、消したものはすべてGitに残ります。

## 8. ユーザーの決定（2026-10-02）

1. 初期構想書 `AI_WEREWOLF_CODEX_HANDOFF.md`: 外す。製品要件は新しい README に移す。
2. `ai_client/network`・`ai_client/world`: 外す。再接続などで必要になったらタグから復元する。
3. タグと新ブランチ `rebuild/simple-agent`: origin へ push する（通常の push のみ）。
4. 新ブランチへの commit: 許可。main と他の既存ブランチは変更しない。

## 9. 資料の保存場所

この一覧・プロンプト・分析資料の正本は `C:\\AIwolf\\logs\\rebuild_materials_2026-10-02\\`（Gitの対象外。ブランチを切り替えても消えない）。
T0 の後は `Docs/analysis/2026-10-02/` として新ブランチに入り、GitHub にも残る。
"""

(HERE / "REBUILD_MANIFEST.md").write_text(md, encoding="utf-8")
print("written", len(md.splitlines()), "lines")
