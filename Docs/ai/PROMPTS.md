# Prompts

通常運用でユーザーが打つのは次の4つだけ。各エージェントは `AGENTS.md` を
読んだあと、指示に対応する `python scripts/ai_status.py <role>` を実行する。

| 指示 | 役割 | 入口 |
|---|---|---|
| 「レビューして」 | Reviewer | `python scripts/ai_status.py review` |
| 「Phase X.Y を詳細設計して」 | Detailed Design | `python scripts/ai_status.py design` |
| 「Phase X.Y を実装して」「次のフェーズを実装して」 | Implementer | `python scripts/ai_status.py implement` |
| 「レビュー内容を確認して修正して」 | Implementer | `python scripts/ai_status.py fix` |

```
Phase X.Y を実装して
Phase X.Y を詳細設計して
レビューして
レビュー内容を確認して修正して
```

フェーズ番号を省略すると `ai_status.py` の出力にある Next Task が使われる。

通常の実装モデルはQwen（D057）。上位モデルは必要な設計・独立レビュー・難問を担当し、
日常の実行/修正ループはrunnerに任せる。基盤入口は `python scripts/ai_status.py infra`。
複数の承認済み契約は `task_batch.py` で逐次実行・再開できる。
詳細手順: `Docs/ai/spec/LOCAL_IMPLEMENTATION_RUNNER.md`。Geminiは例外補助のみ。

方向性の変更・新しい仕様判断・スコープの変更は、この4つの外側であり、
ユーザーが個別に指示する。

---

## 初回のみ Implementer へ貼る文面

Implementer の新しいチャットで、AGENTS.md を自動で読まない場合の保険。
2回目以降は不要。

```
このリポジトリの恒久ルールは AGENTS.md にある。

セッション開始時に必ず AGENTS.md を読み、指示に応じて次を実行すること:
1. Phase の実装: `python scripts/ai_status.py implement`
2. レビュー内容の修正: `python scripts/ai_status.py fix`

以降、私は次の4つしか指示しない。

- 「Phase X.Y を実装して」   → `python scripts/ai_status.py implement`
- 「Phase X.Y を詳細設計して」 → `python scripts/ai_status.py design`
- 「レビューして」            → `python scripts/ai_status.py review`
- 「レビュー内容を確認して修正して」→ `python scripts/ai_status.py fix`

`DESIGN: REQUIRED` の Phase は、Reviewer が承認した詳細設計が無いかぎり実装しない。
Design Gate の状態は `python scripts/ai_status.py implement` の出力で確認する。

指示を受けたら、追加の質問をせずに `ai_status.py` の出力に従って進めること。
仕様に疑問が出たら `OPEN_QUESTIONS.md` へ起票し、
避けて進められるなら続行、進められないなら止めて報告する。
```

---

## Implementer の新しいチャットへ移るとき

会話が長くなって新しいチャットに移る際、最初に貼る文面。
**現在地や残課題は書かない。** それらは `CURRENT_STATE.md` と
`REVIEW_INBOX.md` にあり、ここへ複製すると必ず片方が古くなる。
ここに書くのは、リポジトリを読んでも分からない環境の話だけ。

```
このリポジトリの恒久ルールは AGENTS.md にあり、動的な作業手順は
`python scripts/ai_status.py <role>` の出力で決まる。
まず AGENTS.md を読み、指示に応じて `python scripts/ai_status.py implement`
または `python scripts/ai_status.py fix` を実行すること。リポジトリ全体は読まない。

あなたは Implementer。私が打つ指示は次の4つだけ。

- 「Phase X.Y を実装して」「次のフェーズを実装して」 → `python scripts/ai_status.py implement`
- 「Phase X.Y を詳細設計して」                    → `python scripts/ai_status.py design`
- 「レビューして」                               → `python scripts/ai_status.py review`
- 「レビュー内容を確認して修正して」            → `python scripts/ai_status.py fix`

`DESIGN: REQUIRED` の Phase は、承認済み詳細設計が無いかぎり実装しない。
「レビューして」は Reviewer が担当する。
指示を受けたら追加の質問をせず、`ai_status.py` の出力どおり進めること。
仕様に疑問が出たら `OPEN_QUESTIONS.md` へ起票し、
避けて進められるなら続行、進められないなら止めて報告する。

リポジトリを読んでも分からない環境の話:

- 開発補助のローカルLLMは C:\AIagent にある別リポジトリ。使う前に
  C:\AIagent\agent\serve.bat を起動する（PowerShell では .\serve.bat）。
  使い方・入力長の上限・失敗時の切り分けは D034 / D043 / F004 / F005。
  C:\AIagent 側の変更も、そちらのリポジトリでコミットする
- git の index.lock が残って操作が止まることがある。対処は F003
- push は私が行う。あなたはコミットまで

指示に対応する `ai_status.py` の出力を確認してから作業を始めること。
```
