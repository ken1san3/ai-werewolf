# AI Documentation Index

AIエージェントが「どのファイルを読むか」だけを決めるためのファイル。
内容そのものはここに書かない。

## Always read

- `CURRENT_STATE.md`
- `REVIEW_INBOX.md`（OPEN の Critical / High があれば新機能より先に対応）

## Current phase

**Phase 1.1 着手可（設計判断は D001〜D007 で確定済み）**

Relevant:
- `spec/AI_WEREWOLF_CODEX_HANDOFF.md` の §1, §2, §5〜§11, §26, §30, §31, §32, §37
- `spec/JUDGMENT_REFERENCE.md`（参照実装の事実。**再調査せずここを見る**）
- `SPEC_REVIEW.md`
- `OPEN_QUESTIONS.md`（Q1〜Q4 は Phase 1 実装前に決める必要がある）
- `decisions/D001_SERVER_AUTHORITATIVE.md`
- `decisions/D002_ROLE_ABILITY_EFFECT_MODEL.md`
- `decisions/D003_ROLE_ATTRIBUTE_MODEL.md`
- `decisions/D004_REFERENCE_IMPLEMENTATION.md`
- `decisions/D005_WIN_CONDITIONS.md`
- `decisions/D006_RULE_OPTIONS.md`

## Read only if needed

| 目的 | ファイル |
|---|---|
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
