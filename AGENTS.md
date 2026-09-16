# AIwolf Project Agent Rules

## Session bootstrap

責任・モデル・会話・タスクは別概念。権限は責任が定め、モデルは
`Docs/ai/MODEL_ASSIGNMENTS.md` で選ぶ実行者。モデル変更で責任やタスク契約は変わらない。

At the start of every session:

1. Read this file.
2. Run `python scripts/ai_status.py <entry>` for the assigned responsibility.
3. Read the named task packet and only the canonical/design files it references.
4. Inspect `git status` and the relevant diff before writing.

Canonical entries are `integrate`, `architect`, `implement`, `review`, `fix`, `test`,
and `investigate`; `design` remains a compatibility alias for `architect`. Do not use
conversation history as the sole source of project state.

## Responsibilities

| Responsibility | Authority and output |
|---|---|
| Integrator | Maintains the critical path and task board, partitions non-overlapping work, verifies handoffs and evidence, resolves conflicts, decides the next wave, and coordinates phase/MVP completion. It chooses decomposition, delegation, and verification by risk and may perform bounded work locally when separate responsibility or independence is not required. |
| Architect | Defines public interfaces, lifecycle, state transitions, concurrency, protocol interaction, acceptance criteria, and required tests for a task that passed a required Design Gate. It does not implement or self-approve. |
| Implementer | Implements an approved contract or unambiguous existing specification, adds focused/regression tests, and writes measured handoff evidence. It does not change specifications on its own. |
| Reviewer | Independently checks specification, design, implementation, tests, and diff; records findings and actual evidence. It does not approve a detailed design created in the same session. |
| Tester | Runs focused, integration, completion, regression, and bounded long-running tests; preserves raw commands, logs, timing, and failures. It verifies facts and does not make design decisions. |
| Investigator | Reproduces and isolates unclear, cross-component, flaky, concurrency, or E2E failures; reports cause, evidence, and recommended repair scope. It does not begin a broad repair without a separate implementation task. |

The user retains final authority over product rules, scope, and direction.

## External memory

権限と選択的読込みは `Docs/ai/INDEX.md` に従う。phase/target/critical path/holdは
`CURRENT_STATE.md`、task lifecycleは `TASKS.md` のみが正本。CURRENT_STATEのTask stateは
検査用mirror。packetはscope/acceptance契約で、statusは割当時snapshot。handoffは実測証拠であり
dispatch指示ではない。境界は `ARCHITECTURE.md`、運用は `OPERATIONS.md`、責務は `roles/`、
ゲーム正本は `spec/DESIGN.md`、phase scopeは `ROADMAP.md` を参照する。

workerは割当境界で終了。Mainはユーザーscope/hold/独立性を守り、安全な承認済みcritical pathを
packetごとの確認なしに続行する。next action記録やwave完了だけでは停止しない。
割当前に最新Git・host・証拠と管理記録を照合する。古いhandoffからDONEを再割当したり、
IN_PROGRESSを重複実行しない。host所有権不明なら照合まで保留。過去のprocess数は現在の証拠ではない。

並列割当前に専有/共有fileと競合を確認。同一fileの編集は直列化する。
board/stateはMainが更新し、packetの明示割当なしにworkerが並行更新しない。

host標準の委譲/隔離を使い、daemon、scheduler、workflow engine、model router、automatic merge、
Autodev代替を作らない。責務は必要に応じ選び、独立Tester/fresh Reviewer gateを維持する。
required independenceをMainや対象の実装・設計を担当したsessionで代替しない。
agent thread limit時は既存担当の状態と独立性を確認し、利用可能な独立Reviewerを再利用する。
次に不要な完了担当をhost標準機能で終了・解放して生成、並列数を減らして直列生成、
それでも不可なら利用可能な既存Reviewerへの割当を順に検討する。上限だけで全体停止せず、
レビュー省略・Main自己承認・未実行の完了扱いは禁止。不要な担当は証拠回収後に解放し、
解放APIがない場合はその制約を記録する。中断・archiveだけで枠解放済みと推定しない。
Reviewer→必要な修正→再レビュー→admission/実ゲームのgateを維持する。
明確な修正は直接、原因不明は早期調査へ。
D068はtask名や症状が異なっても同一acceptance/evidence objectiveの失敗3round後に経路再評価を
要求する（`OPERATIONS.md`）。canonical/decision/code/Architectでも解けない重要なproduct選択
だけをユーザーへ上げ、判断待ちの間も独立した安全な作業を進める。

## Design Gate

New implementation work follows D051. A task marked as requiring design may be implemented
only when the selected detailed design is independently approved. Detailed design is below
canonical sources in this order:

1. canonical specification
2. implementation and protocol/schema facts
3. tests
4. approved detailed design

Self-approval is prohibited by responsibility plus session independence, regardless of
which model executes either session. Questions that materially change scope or product
rules go to `Docs/ai/OPEN_QUESTIONS.md`.

## Design invariants

1. Server is the single source of truth. Never trust the client.
2. Server and AI logic are separate programs. The game core has no AI dependency.
3. Realtime free chat. No fixed speaking order.
4. No role names hardcoded in the game core or AI client.
5. Keep Role, Team, Alignment, Knowledge, Ability, Passive, Effect, WinCondition, and ChatPermission separated.
6. Roles, teams, and game modes are loaded from YAML, never Python literals.
7. Send each client only information it may know. Never filter secrets in the prompt.
8. The protocol stays language-independent and versioned.
9. Target RTX 3070 Ti / 8GB VRAM with one shared LLM server.
10. The game core remains fully testable without any LLM.

## Review checklist

- No role-specific branch such as `if role == "seer"` in the game core.
- Inspection/medium results use `inspect_result` / `medium_result`, not `team`.
- Win counts use `count_as`, not `team`.
- Rule evaluation uses effective Role + Modifiers attributes.
- Internal death causes never reach clients.
- Private notifications never use broadcast paths.
- The network layer does not decide whether an action is allowed.
- Random selections are recorded as events; no direct module-level `random` calls.
- No rule default is hardcoded and adding a role requires no Python change.
- `python scripts/check_docs.py` passes.

## 人が読むリポジトリ文書の共通ルール

今後、全AI agentが新規作成・実質的更新するhuman-facing repository documentationは、
原則日本語とする。handoff、調査・レビュー報告、failure summary、recovery instruction、
Main Integratorの再開・停止報告などを含む。技術上の原文維持が必要な識別子・パス・
コマンド・ログ・エラー・API・スキーマ・機械処理用の見出し/フィールド/状態値は維持し、
説明やユーザー手順は日本語にする。既存英語文書の翻訳だけの変更は不要。証拠は改変しない。

Main IntegratorがBLOCKEDで停止する場合、停止報告と再開記録に次を日本語で明示する。
該当なし/未確認も理由を記す。
1. 完了済みの作業と検証・承認範囲。
2. 未完了の作業（未実行・失敗・未承認の区別）。
3. 直接のblockerと観測根拠。
4. 自律解消できない理由。
5. 具体的で確認可能な再開条件。
6. 必要なユーザー操作・判断と対象（不要ならその旨）。
7. 条件成立後のnext actionと維持する検証・承認条件。

## Working-tree and evidence rules

- Preserve all user and inherited uncommitted changes; work around unrelated edits.
- Never use `git reset --hard`, `git clean -fd`, force push, or history rewriting.
- Do not commit unless the user explicitly authorizes it.
- A responsibility records only decisions and measurements it actually performed.
- Test evidence names the execution environment and raw pass/fail result.
- Do not infer approval from a worker's completion claim.
- Do not restore or execute the archived autonomous-development runtime.

## End of task

Before handing work back:

- Run focused tests, relevant regressions, `python scripts/check_docs.py`, and diff checks.
- Review the complete scoped diff.
- Write the task handoff with measured evidence.
- Let the Integrator update `TASKS.md` and `CURRENT_STATE.md` from verified evidence.
- Record new decisions in `decisions/` and repeatable failures in `failures/`.
- Workers stop at their assignment boundary. Main continues within user-authorized scope until
  completion, an explicit hold, or no safe work remains. Pause affected work for an unresolved
  material product choice, irreconcilable authority, destructive operation, or missing external
  authorization; keep independent authorized work moving.
