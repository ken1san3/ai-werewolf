# Project AI Rules

## このリポジトリの動かし方

ユーザーが打つ指示は原則3つだけ。**指示を受けたら `Docs/ai/RUNBOOK.md` の該当節を読み、
そこに書かれた手順に従う。ユーザーへ追加の指示を求めない。**

| 指示 | 担当 | 手順 |
|---|---|---|
| 「Phase X.Y を実装して」「次のフェーズを実装して」 | Codex | RUNBOOK §1 |
| 「レビューして」 | Claude (Cowork) | RUNBOOK §2 |
| 「レビュー内容を確認して修正して」 | Codex | RUNBOOK §3 |

- フェーズ番号の指定が無ければ `Docs/ai/CURRENT_STATE.md` の Next Task に従う
- スコープ（含む / 含まない / 完了条件）は `Docs/ai/ROADMAP.md` の該当サブPhaseにある
- 判断に迷ったら `Docs/ai/OPEN_QUESTIONS.md` へ起票し、避けて進められるなら続行する
- 現状の要約は `python scripts/ai_status.py` で取得できる

## Roles

| 役割 | 担当 | やること |
|---|---|---|
| Implementer | **Codex** | 実装 / テスト / CURRENT_STATE・handoff・decision・failure の更新 |
| Reviewer | **Claude (Cowork)** | 仕様整合レビュー / 指摘の REVIEW_INBOX 起票 / 仕様と設計ドキュメントの整備 |

- Reviewer は原則コードを書かない。指摘は `Docs/ai/REVIEW_INBOX.md` へ残し、修正は Implementer が行う。
- Implementer は仕様を勝手に変更しない。疑問は `Docs/ai/OPEN_QUESTIONS.md` へ起票する。
- 仕様・ルールの最終決定権はユーザーにある。両者とも決定を `Docs/ai/decisions/` へ記録する。

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
| 作業手順 | `Docs/ai/RUNBOOK.md` |
| フェーズのスコープ | `Docs/ai/ROADMAP.md` |
| 設計書（結論） | `Docs/ai/spec/DESIGN.md` |
| 検証項目 | `Docs/ai/TEST_POLICY.md` |
| マスター仕様 | `Docs/ai/spec/AI_WEREWOLF_CODEX_HANDOFF.md` |
| 参照実装の事実 | `Docs/ai/spec/JUDGMENT_REFERENCE.md` |
| 運用ガイド | `Docs/ai/spec/CODEX_TOKEN_EFFICIENT_WORKFLOW.md` |
| Phase間引継ぎ | `Docs/ai/handoffs/` |
| 設計判断 | `Docs/ai/decisions/` |
| 失敗記録 | `Docs/ai/failures/` |
| レビュー指摘 | `Docs/ai/REVIEW_INBOX.md` |
| 未決事項 | `Docs/ai/OPEN_QUESTIONS.md` |
| 初回セットアップ用の文面 | `Docs/ai/PROMPTS.md` |

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

## Review checklist

レビュー時に必ず確認する。**この一覧が唯一の置き場所であり、各Decisionには複製しない。**

- ゲームコアに役職固有の分岐（`if role == "seer"`）が入っていないか
- 占い・霊能が `team` を参照していないか（`inspect_result` / `medium_result` を使うこと）
- 勝利条件の人数計算が `team` を数えていないか（`count_as` を使うこと）
- 判定が Role を直接読んでいないか（Role + Modifiers の実効属性を経由すること）
- 内部死因を含むイベントがクライアントへ送られていないか
- private 通知がブロードキャスト経路に乗っていないか
- ネットワーク層に行動の可否判定が書かれていないか
- ランダムな選択の結果がイベントとして記録されているか
- モジュールレベルの `random` を直接呼んでいないか
- ルールの既定値がコードへ埋め込まれていないか
- 役職の追加に Python の変更が必要になっていないか

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
