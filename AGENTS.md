# AIwolf Project Agent Rules

## Session bootstrap

責任・モデル・会話・タスクは別概念。権限は責任が定め、モデルは
`Docs/ai/MODEL_ASSIGNMENTS.md` で選ぶ実行者。モデル変更で責任やタスク契約は変わらない。

At the start of every session:

1. Read this file's active rules.
2. Run `python scripts/ai_status.py <entry> --task <assigned-id>` (Main未割当時は`--task`省略)。
3. Read the assigned task packet.
4. Read only the canonical files/sections explicitly named by that packet.
5. Inspect `git status` and the relevant diff before writing.
6. Read additional evidence only when a concrete unresolved question requires it.

CURRENT_STATEやhandoffの過去全文を起動時に展開しない。現値はactive snapshot、履歴は
history/archiveに分離する。引継ぎはpath/hashと今回の差分だけ。詳細はD075とOPERATIONS。

Canonical entries are `integrate`, `architect`, `implement`, `review`, `fix`, `test`,
and `investigate`; `design` remains a compatibility alias for `architect`. Do not use
conversation history as the sole source of project state.

## Responsibilities

| Responsibility | Authority and output |
|---|---|
| Integrator | critical path/board/競合/証拠照合/統合を管理。既定実行主体として限定作業を行い、riskと独立性で委譲を選ぶ。 |
| Architect | 必要な詳細設計のinterface/lifecycle/state/concurrency/protocol/acceptance/testを定義。実装・自己承認しない。 |
| Implementer | 承認契約または明白な既存仕様を実装し、focused/regressionと実測証拠を残す。仕様を独断変更しない。 |
| Reviewer | 仕様/設計/実装/test/diffを独立照合し指摘と実測を記録。本人の対象設計を承認しない。 |
| Tester | 必要な独立測定と有限testを実行しcommand/raw/時間/失敗を保全。設計判断はしない。 |
| Investigator | 未知/横断/flaky/concurrency/E2E失敗を再現・分離し原因と最小修正scopeを報告。広い実装修正を始めない。 |

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
Autodev代替を作らない。Mainを既定実行主体とし、D075のAgent Dispatch Gateで必要な責務だけを選ぶ。
追加担当で得る独立性・専門判断・測定結果を一文で説明できなければ呼ばない。役割chainを作らない。
必要な独立Tester/Reviewer gateを維持する。fresh sessionは独立性不成立またはcanonical明示時だけ。
required independenceをMainや対象の実装・設計を担当したsessionで代替しない。
agent thread limit時は既存担当の独立性確認と再利用→不要な完了担当の解放→Mainで非依存作業→
対象gateだけ保留の順。固定agent回数上限は設けない。上限だけで全体停止、レビュー省略、
Main自己承認、未実行の完了扱いは禁止。不要な担当は証拠回収後に解放し、
解放APIがない場合はその制約を記録する。中断・archiveだけで枠解放済みと推定しない。
Reviewer→必要な修正→再レビュー→admission/実ゲームのgateを維持する。
明確な修正は直接、原因不明は早期調査へ。
D068はtask名や症状が異なっても同一acceptance/evidence objectiveの失敗3round後に経路再評価を
要求する（`OPERATIONS.md`）。canonical/decision/code/Architectでも解けない重要なproduct選択
だけをユーザーへ上げ、判断待ちの間も独立した安全な作業を進める。

## Agent Dispatch Gate（D075）

- Architect: public interface/protocol/schema/state transition/lifecycle/concurrency ownership/
  acceptance/product rule/component boundaryを変更する場合だけ。既存設計内の局所修正では不要。
- Investigator: 原因不明、flaky/race/concurrency、component横断、またはMainの1回の限定診断で
  原因候補を絞れない場合。既知原因には挟まない。
- Implementer: 独立した実装単位を分離する価値がある場合。小さく明白な既存契約内修正はMainが行う。
- Tester: acceptance独立測定、E2E/concurrency/long-running/completion/実provider/実game、
  または実装者の測定だけでは不足する場合。focused/regression/syntax/check_docsはMainでも可。
- Reviewer: 製品code/test contractの実質変更、detailed design、acceptance独立判定、高risk運用変更。
  status/handoff整形/集計/hash照合/証拠コピーだけでは新担当を呼ばない。

既存Reviewerは対象設計・実装を担当せず自己採点の立場でなければ複数taskへ再利用できる。
fresh必須は本人の対象設計/実装、以前の判断による直接の独立性毀損、canonicalの明示要求だけ。
second Reviewerは通常経路に置かず、Critical/High、protocol/concurrency/security/authority境界、
重要設計gate、第一担当UNKNOWN/判断不能、実質的見解差、同種見落としの高い再発riskだけで検討する。
担当完了後は「新しい未知/独立性/設計判断/測定」の4点を再判定し、全NOならMainが続行する。
同一artifact SHA-256かつ承認scope/依存/acceptance/環境条件が同じなら既存判定を再利用し、
同一証拠・同一diffの再レビューは禁止。新runの所有権・秘密境界・一回許可は旧承認で代替しない。

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

## ローカル検証とGitHub Actions（2026-09-21ユーザー指示）

通常はローカル検証→commit/push。自動CI停止を維持し、workflow変更等で再開しない。
Actionsの有効化/実行/再実行はローカル同等検証と理由説明後、ユーザー明示許可時だけ。
明示指定がなければ完了/merge条件にしない。過去CI要求より本指示を優先する。
複数Python検証・停止済みworkflow等の詳細は`Docs/ai/OPERATIONS.md`。

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
