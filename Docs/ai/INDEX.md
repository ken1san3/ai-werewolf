# AI Documentation Index

This is the routing index for repository-backed external memory. A new session reads in
this order and stops when it has the task-specific context it needs:

```text
AGENTS.md
  -> python scripts/ai_status.py <entry>
  -> CURRENT_STATE.md
  -> TASKS.md and the named task packet
  -> relevant ARCHITECTURE.md section and role contract
  -> only the canonical/design/decision/handoff files named by that packet
```

Do not preload the entire documentation tree.

The `ai_status.py` entry names the responsibility of the current session. A Main session
uses `integrate`; a dispatched worker uses the entry matching the task record's `Role`.
Do not switch to the active task's worker role merely because an Integrator is inspecting it.

## Live coordination memory

| Purpose | Source of truth |
|---|---|
| Current facts, active target, tests, critical path | `CURRENT_STATE.md` |
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

| Purpose | Path |
|---|---|
| Phase completion handoffs | `handoffs/PHASE*_HANDOFF.md` |
| Repeatable failures | `failures/` |
| Historical review evidence | `review_archive/` |
| Historical roadmap scope | `roadmap_archive/` |
| Original source specification | historical handoff under `spec/` (read only when needed) |
| Autodev archive/removal record | `decisions/D065_AUTODEV_FREEZE_AND_REMOVAL.md` |
| Responsibility/model separation | `decisions/D066_ROLE_MODEL_SEPARATION.md` |

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
