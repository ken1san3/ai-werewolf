# Prompts

通常運用でユーザーが打つのは次の3つだけ。各エージェントは `AGENTS.md` を
読んだあと、指示に応じて
`python scripts/ai_status.py implement` または
`python scripts/ai_status.py fix` を実行する。

```
Phase X.Y を実装して
レビューして
レビュー内容を確認して修正して
```

フェーズ番号を省略すると `ai_status.py` の出力にある Next Task が使われる。

方向性の変更・新しい仕様判断・スコープの変更は、この3つの外側であり、
ユーザーが個別に指示する。

---

## 初回のみ Codex へ貼る文面

Codex の新しいチャットで、AGENTS.md を自動で読まない場合の保険。
2回目以降は不要。

```
このリポジトリの恒久ルールは AGENTS.md にある。

セッション開始時に必ず AGENTS.md を読み、指示に応じて次を実行すること:
1. Phase の実装: `python scripts/ai_status.py implement`
2. レビュー内容の修正: `python scripts/ai_status.py fix`

以降、私は次の3つしか指示しない。

- 「Phase X.Y を実装して」   → `python scripts/ai_status.py implement`
- 「レビュー内容を確認して修正して」→ `python scripts/ai_status.py fix`

（「レビューして」は別のエージェントが担当するので、あなたは実行しない）

指示を受けたら、追加の質問をせずに `ai_status.py` の出力に従って進めること。
仕様に疑問が出たら `OPEN_QUESTIONS.md` へ起票し、
避けて進められるなら続行、進められないなら止めて報告する。
```

---

## Codex の新しいチャットへ移るとき

会話が長くなって新しいチャットに移る際、最初に貼る文面。
**現在地や残課題は書かない。** それらは `CURRENT_STATE.md` と
`REVIEW_INBOX.md` にあり、ここへ複製すると必ず片方が古くなる。
ここに書くのは、リポジトリを読んでも分からない環境の話だけ。

```
このリポジトリの恒久ルールは AGENTS.md にあり、動的な作業手順は
`python scripts/ai_status.py <role>` の出力で決まる。
まず AGENTS.md を読み、指示に応じて `python scripts/ai_status.py implement`
または `python scripts/ai_status.py fix` を実行すること。リポジトリ全体は読まない。

あなたは Implementer（Codex）。私が打つ指示は次の2つだけ。

- 「Phase X.Y を実装して」「次のフェーズを実装して」 → `python scripts/ai_status.py implement`
- 「レビュー内容を確認して修正して」            → `python scripts/ai_status.py fix`

「レビューして」は別のエージェント（Claude）が担当する。あなたは実行しない。
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
