# Project AI Rules

AI人狼プロジェクトの恒久ルール。ここには**短く安定した規約のみ**を書く。
現在の進捗・タスク・設計理由はここに書かない（置き場所は下記）。

## Start of session

1. Read `Docs/ai/INDEX.md`
2. Read `Docs/ai/CURRENT_STATE.md`
3. Read `Docs/ai/REVIEW_INBOX.md`
4. Read only the documents INDEX lists for the current phase.

Do not scan the whole repository unless necessary.

## Where information lives

| 種類 | 置き場所 |
|---|---|
| 恒久ルール | `AGENTS.md`（このファイル） |
| 読む場所の地図 | `Docs/ai/INDEX.md` |
| 現在地 | `Docs/ai/CURRENT_STATE.md` |
| マスター仕様 | `Docs/ai/spec/AI_WEREWOLF_CODEX_HANDOFF.md` |
| 運用ガイド | `Docs/ai/spec/CODEX_TOKEN_EFFICIENT_WORKFLOW.md` |
| Phase間引継ぎ | `Docs/ai/handoffs/` |
| 設計判断 | `Docs/ai/decisions/` |
| 失敗記録 | `Docs/ai/failures/` |
| レビュー指摘 | `Docs/ai/REVIEW_INBOX.md` |
| 未決事項 | `Docs/ai/OPEN_QUESTIONS.md` |
| セッション用プロンプト | `Docs/ai/PROMPTS.md` |

同じ内容を複数ファイルへ複製しない。

## Design invariants

1. Server is the single source of truth. Never trust the client.
2. Server and AI logic are separate programs. The game core has no AI dependency.
3. Realtime free chat. No fixed speaking order.
4. No role names hardcoded in the game core or in the AI client.
5. Keep `Role` / `Team` / `Alignment` / `Knowledge` / `Ability` / `Passive` / `Effect` / `WinCondition` / `ChatPermission` separated.
6. Roles, teams and game modes are loaded from YAML, never from Python literals.
7. Send each client only the information that client may know. Never filter secrets in the prompt.
8. The protocol must stay language-independent and versioned.
9. Must run on RTX 3070 Ti / 8GB VRAM: one shared LLM server, not one model per agent.
10. Game core must be fully testable without any LLM.

## Prohibitions

```
Do not reread the whole repository just to refresh context.
Use the AI documentation as the persistent project memory.

Do not repeat an investigation already documented in decisions or failures
unless current code contradicts that documentation.

Prefer targeted reads, searches, diffs, and batched independent operations.

Do not redesign a completed phase without an explicit request.
Do not change roles or the protocol on your own judgement; raise it in OPEN_QUESTIONS.md.

Before ending the session, externalize all information required by the next
agent into the repository.
```

## End of session checklist

```
[ ] tests run
[ ] git diff reviewed
[ ] CURRENT_STATE.md updated (phase / next task / current problem)
[ ] REVIEW_INBOX.md OPEN items checked
[ ] new decision -> Docs/ai/decisions/
[ ] repeatable failure -> Docs/ai/failures/
[ ] phase finished -> Docs/ai/handoffs/PHASE<N>_HANDOFF.md
[ ] list the files the next agent should read
```

## Commit convention

```
feat(core): ...
feat(network): ...
fix(core): ...
test(core): ...
docs(ai): ...
```

Phase または責務単位でコミットする。
