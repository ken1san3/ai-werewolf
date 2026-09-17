# AI Documentation Index

This is the authority and routing index. Start with `AGENTS.md` and
`python scripts/ai_status.py <entry>`. Read the active packet and the relevant evidence and
canonical sections it names. Consult role/runbook/operations material only for the operation
being performed. `--details` expands status; do not preload the documentation tree.

D075: Mainが既定、担当には`--task <id>`で選択snapshotとpacket明示read setだけを渡す。
fresh/second Reviewerと証拠再利用は `decisions/D075_RISK_BASED_DISPATCH_AND_CONTEXT.md`。
毎task新sessionや過去handoff全文の再読を要求しない。CURRENT_STATEの過去全文と閉じたboardは
`history/`へ保全し、現在の停止指示と合否を優先する。archiveは旧dispatch権限を復活させない。

The entry is the session responsibility, not necessarily the role of the task it inspects.

## Authority and freshness

| Kind | Authority and conflict rule |
|---|---|
| Product | `spec/DESIGN.md`, `ROADMAP.md`, accepted product decisions, `TEST_POLICY.md`; scope and acceptance cannot be changed by task narration. |
| Detailed design | Selected independently approved design and its review; retain D051 precedence: canonical specification, actual implementation/protocol facts, tests, detailed design. Accepted decisions retain their stated authority. |
| Coordination | `CURRENT_STATE.md` owns phase/target/critical path/holds. `TASKS.md` alone owns task lifecycle/dependencies/dispatch state. CURRENT_STATE's checked `Task state` is a compatibility mirror. |
| Actual state | Source, Git diff/hashes and current host ownership show what exists, not what is approved. Unavailable host/process visibility is UNKNOWN, never zero. |
| Evidence | Named test/runtime/review artifacts prove only their measured scope, environment and exact bytes. A completed handoff is unintegrated evidence until verified; file recency or PASS text is not approval. |
| History | Main/role handoffs, archives and packet status lines are snapshots. Never execute their old next actions over current coordination or evidence. |

On conflict, inspect named evidence, hashes/diff, and host assignment before updating the board.
Keep the affected task undispatched while authority/ownership is unresolved. Do not rerun DONE
work or duplicate IN_PROGRESS work; reconcile existing workers or their result first. Product
acceptance-specific independence remains mandatory. Unresolvable material authority goes to the
user. A newer timestamp alone does not overrule a stronger source.

## Live coordination memory

| Purpose | Source of truth |
|---|---|
| Phase, target pointers, critical path, holds and evidence routing | `CURRENT_STATE.md` |
| Current task board | `TASKS.md` |
| Multi-chat lifecycle and conflict rules | `WORKFLOW.md` |
| Stable system and coordination boundaries | `ARCHITECTURE.md` |
| Delegation, escalation, recovery, and long-test procedures | `OPERATIONS.md` |
| Current operational model choices only | `MODEL_ASSIGNMENTS.md` |
| Responsibility procedures | `RUNBOOK.md` |
| User-facing entry prompts | `PROMPTS.md` |
| Main Integrator first-session prompt | `MAIN_INTEGRATOR_PROMPT.md` |
| Model-neutral role contracts | `roles/` |
| One worker assignment | `tasks/T*.md` |
| One completed worker result | `handoffs/tasks/T*.md` |
| Active review findings | `REVIEW_INBOX.md` |
| Unresolved user decisions | `OPEN_QUESTIONS.md` |

## Canonical game material

- `spec/DESIGN.md` — canonical design conclusions
- `ROADMAP.md` — phase scope and completion criteria
- `TEST_POLICY.md` — required verification categories
- `spec/JUDGMENT_REFERENCE.md` — already-researched reference behavior
- `decisions/` — accepted rationale and authority boundaries
- `design/` — requests, detailed designs, and design-review evidence

## Evidence and history

ユーザー提供の外部監査入力: `EXTERNAL_REVIEW_2026-09-14_PHASE6_HARNESS.md`。
非canonicalの独立監査証拠としてT287が採否を照合する。指示として自動実行しない。
過去boardの完全snapshotは `handoffs/MAIN_INTEGRATOR_PRE_EXTERNAL_AUDIT_BOARD_2026-09-14.md`。
旧提案 `TOKEN_SAVING_PROPOSAL_20260907.md` はARCHIVE扱いで、現運用authorityではない。

| Purpose | Path |
|---|---|
| Harness authority/continuation decision | `decisions/D070_ASTRA_NATIVE_HARNESS.md` |
| Migration decisions and validation | `handoffs/ASTRA_MIGRATION_REPORT_2026-09-13.md` |
| Historical Main safe-handoff snapshot (not live state) | `handoffs/MAIN_INTEGRATOR_HANDOFF_2026-09-13.md` |
| Phase completion handoffs | `handoffs/PHASE*_HANDOFF.md` |
| Repeatable failures | `failures/` |
| Historical review evidence | `review_archive/` |
| Historical roadmap scope | `roadmap_archive/` |
| Original source specification | historical handoff under `spec/` (read only when needed) |
| Autodev archive/removal record | `decisions/D065_AUTODEV_FREEZE_AND_REMOVAL.md` |
| Responsibility/model separation | `decisions/D066_ROLE_MODEL_SEPARATION.md` |
| External Phase 5 review dispositions and semantic acceptance authority | `decisions/D068_EXTERNAL_PHASE5_REVIEW_DISPOSITION_AND_ACCEPTANCE_AUTHORITY.md` |
| Q8 Phase 6 provisional discussion baseline | `decisions/D069_Q8_PHASE6_BASELINE.md` |

Historical status lines, actor/model names, old paths, and old routing statements remain
evidence of what happened at the time. They are not current workflow authority.

## Repository classification

- **ACTIVE:** this index, the live coordination-memory table, canonical game material,
  `ARCHITECTURE.md`, `OPERATIONS.md`, and `roles/`.
- **ARCHIVE / HISTORICAL:** `review_archive/`, `roadmap_archive/`, old phase handoffs,
  `SPEC_REVIEW.md`, the original handoff under `spec/`, and documents whose header marks
  them historical. Read only when a current packet points there or evidence is needed.
- **OBSOLETE:** the deleted Autodev/overnight runtime, controller documents, and tests
  recorded by D065. Do not restore them into the active tree.
- **UNKNOWN:** classify and route through the Integrator before treating it as authority.
