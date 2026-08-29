# AI Documentation Index

AIエージェントが「どのファイルを読むか」だけを決めるためのファイル。
内容そのものはここに書かない。

## Always read

- `RUNBOOK.md` — 指示に対応する手順。**まずこれ**
- `ROADMAP.md` — 対象サブPhaseのスコープ
- `CURRENT_STATE.md`
- `REVIEW_INBOX.md`（OPEN の Critical / High があれば新機能より先に対応）

## 実装に入る前に読む

- `spec/DESIGN.md` — 設計の結論。**まずこれを読む**
- `TEST_POLICY.md` — 検証項目。Phase 1 の完了判定はこれで行う

`decisions/` は「なぜそう決めたか」の記録。
設計を変えたくなったとき、または DESIGN.md の意図が読み取れないときだけ開く。

## Current phase

**Phase 1.1 着手可（設計判断は D001〜D007 で確定済み）**

Relevant:

- `spec/DESIGN.md`
- `TEST_POLICY.md`
- `spec/JUDGMENT_REFERENCE.md`（参照実装の事実。**再調査せずここを見る**）
- `OPEN_QUESTIONS.md`

## Read only if needed

| 目的 | ファイル |
|---|---|
| 設計判断の理由 | `decisions/`（D001〜D017） |
| 元仕様への指摘 | `SPEC_REVIEW.md` |
| 元仕様の原文 | `spec/AI_WEREWOLF_CODEX_HANDOFF.md` |
| 運用ルールの根拠 | `spec/CODEX_TOKEN_EFFICIENT_WORKFLOW.md` |
| ロードマップ全体 | `ROADMAP.md` |
| Phase引継ぎ | `handoffs/` |
| 過去の失敗 | `failures/` |

## Not needed in the current phase

Phase 1（ゲームコア）では以下を読まない。

```
AI Client 仕様          spec master §12〜§21, §33〜§36
LLM / 人格 / typing演出  spec master §16〜§21
ネットワーク詳細         spec master §4（プロトコル境界の把握のみで可）
Web UI                  spec master §Phase 7
MOD                     spec master §23
```

## Phaseごとの主読込範囲

| Phase | 読むもの | 読まないもの |
|---|---|---|
| 1 ゲームコア | Role / Team / Ability / Effect / GameState / WinCondition / tests | AI Client 仕様 |
| 2 ネットワーク | CURRENT_STATE / PHASE1_HANDOFF / Protocol / Session / 公開・非公開状態 | LLM詳細 |
| 3 AI Skeleton | Protocol / Client / WorldState / Dummy Brain | Roleエンジン内部（必要時のみ） |
| 4 Local LLM | Brain Interface / LLM Backend / Structured Output | ゲームコア内部 |
