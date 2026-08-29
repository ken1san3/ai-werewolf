# Contributing

個人開発プロジェクトですが、**AIエージェントに実装を任せる前提**で運用ルールを固めています。
人間が参加する場合も同じルールに従います。

## 役割

| 役割 | 担当 | やること |
|---|---|---|
| Implementer | Codex | 実装 / テスト / 状態ファイルの更新 |
| Reviewer | Claude | 仕様整合レビュー / 指摘の起票 / 設計書の維持 |
| 仕様の決定 | 人間 | ルール・スコープ・方向性の決定 |

Reviewer は原則コードを書きません。指摘は `Docs/ai/REVIEW_INBOX.md` へ残し、
修正は Implementer が行います。実装したAIが自分の設計前提を引きずるのを避けるためです。

## 基本方針

会話履歴を長期記憶にせず、**リポジトリを記憶装置として使います。**
セッションが途中で切れても、次の担当が以下を読めば作業を再開できる状態を保ちます。

```
AGENTS.md
Docs/ai/RUNBOOK.md
Docs/ai/CURRENT_STATE.md
git status / git diff
```

「前のチャットを読めないと続きが分からない」状態を作らないこと。

## 作業の流れ

指示は3つだけです。手順は [`Docs/ai/RUNBOOK.md`](Docs/ai/RUNBOOK.md) にあります。

```
Phase X.Y を実装して            → RUNBOOK §1
レビューして                    → RUNBOOK §2
レビュー内容を確認して修正して  → RUNBOOK §3
```

現在地の確認:

```bash
python scripts/ai_status.py
```

## ドキュメントの置き場所

同じ内容を複数のファイルへ複製しないでください。

| 種類 | 置き場所 |
|---|---|
| 恒久ルール・レビュー観点 | `AGENTS.md` |
| 作業手順 | `Docs/ai/RUNBOOK.md` |
| 設計の結論 | `Docs/ai/spec/DESIGN.md` |
| なぜそう決めたか | `Docs/ai/decisions/` |
| 現在地 | `Docs/ai/CURRENT_STATE.md` |
| フェーズのスコープ | `Docs/ai/ROADMAP.md` |
| 検証項目 | `Docs/ai/TEST_POLICY.md` |
| レビュー指摘 | `Docs/ai/REVIEW_INBOX.md` |
| 未決事項 | `Docs/ai/OPEN_QUESTIONS.md` |
| 参照実装の事実 | `Docs/ai/spec/JUDGMENT_REFERENCE.md` |

`Docs/ai/spec/AI_WEREWOLF_CODEX_HANDOFF.md` は出発点になった旧仕様で、
`DESIGN.md` と矛盾する箇所があります。**矛盾したら DESIGN.md が優先です。**

## 実装のルール

`AGENTS.md` の Review checklist が唯一の一覧です。特に重要なもの:

- ゲームコアに役職固有の分岐（`if role == "seer"`）を入れない
- **役職の追加に Python の変更が必要になったら設計の失敗**
- 占い・霊能は `inspect_result` / `medium_result` を見る。`team` を見ない
- 勝利条件の人数計算は `count_as` を数える。`team` を数えない
- 判定は Role + Modifiers の実効属性を経由する
- 内部死因をクライアントへ送らない
- ルールの既定値をコードへ埋め込まない

## テスト

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

- ゲームコアは LLM なしでテストできること
- 既定値に依存したテストを書かない。必要なルール設定を各テストが明示的に組み立てる
- 分岐のある設定は両方の値でテストする

検証項目の一覧は [`Docs/ai/TEST_POLICY.md`](Docs/ai/TEST_POLICY.md) にあります。
Phase の完了判定はこれで行います。

## コミット

Phase または責務の単位でコミットします。

```
feat(core): ...
feat(network): ...
fix(core): ...
test(core): ...
docs(ai): ...
```

## 作業終了時

```
[ ] テスト実行（報告は成功数・失敗数・重要エラーのみ）
[ ] git diff 確認 → コミット
[ ] CURRENT_STATE.md 更新（Test Status に commit hash を入れる）
[ ] REVIEW_INBOX.md の OPEN を確認
[ ] 新しい判断があれば decisions/、再発しそうな失敗があれば failures/
[ ] Phase 完了時のみ handoffs/ を作成
```
