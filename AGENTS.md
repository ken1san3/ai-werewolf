# AIwolf Project Agent Rules

## Session bootstrap

Responsibilities, models, chats, and tasks are separate concepts. A responsibility grants
authority; a model is only the current executor selected in `Docs/ai/MODEL_ASSIGNMENTS.md`.
Changing a model never changes a responsibility or task contract.

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
| Integrator | Maintains the critical path and task board, partitions non-overlapping work, verifies handoffs and evidence, resolves conflicts, decides the next wave, and coordinates phase/MVP completion. It does not absorb all routine implementation. |
| Architect | Defines public interfaces, lifecycle, state transitions, concurrency, protocol interaction, acceptance criteria, and required tests for a task that passed a required Design Gate. It does not implement or self-approve. |
| Implementer | Implements an approved contract or unambiguous existing specification, adds focused/regression tests, and writes measured handoff evidence. It does not change specifications on its own. |
| Reviewer | Independently checks specification, design, implementation, tests, and diff; records findings and actual evidence. It does not approve a detailed design created in the same session. |
| Tester | Runs focused, integration, completion, regression, and bounded long-running tests; preserves raw commands, logs, timing, and failures. It verifies facts and does not make design decisions. |
| Investigator | Reproduces and isolates unclear, cross-component, flaky, concurrency, or E2E failures; reports cause, evidence, and recommended repair scope. It does not begin a broad repair without a separate implementation task. |

The user retains final authority over product rules, scope, and direction.

## External memory

`Docs/ai/INDEX.md` is the routing index. Current facts live in `CURRENT_STATE.md`; the
live work queue is `TASKS.md`; stable boundaries are summarized in `ARCHITECTURE.md`;
delegation and recovery procedures live in `OPERATIONS.md`; role contracts live under
`roles/`. One worker assignment is one file under `tasks/`; concise results go under
`handoffs/tasks/`. Canonical game conclusions remain in `spec/DESIGN.md`, phase scope in
`ROADMAP.md`, and rationale in `decisions/`.

Worker sessions are short-lived: finish one task, or a tightly related small set, write a
handoff, then stop. An Integrator session may live longer, but Git, tests, task packets,
handoffs, decisions, `CURRENT_STATE.md`, and `TASKS.md` remain authoritative.

Before parallel dispatch, the Integrator checks expected/shared files and conflict risk.
Tasks that edit the same file run serially. Shared board/state files are updated by the
Integrator, not concurrently by workers unless the packet explicitly assigns that write.

The Integrator uses the host's standard task delegation and isolation facilities. It does
not create a daemon, scheduler, workflow engine, model router, automatic merge system, or
Autodev replacement. Normal review findings return directly to an Implementer; after three
failed rounds on the same cause, route to an Investigator. Ask the user only when canonical
sources, decisions, code, and an Architect cannot determine a material product choice. Keep
independent tasks moving while one task awaits that decision.

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
- Stop at the task boundary; do not begin the next task implicitly.
