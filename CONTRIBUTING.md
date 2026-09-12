# Contributing

AIwolf uses repository-backed external memory so an Integrator and short-lived workers can
collaborate without relying on a long chat history.

## Responsibilities

| Responsibility | Purpose |
|---|---|
| Integrator | Maintains current state, critical path, task partitioning, dependencies, integration evidence, and the next wave |
| Architect | Defines high-risk interfaces, lifecycle, state, concurrency, acceptance, and tests after a required Design Gate |
| Implementer | Implements an approved task contract and produces focused/regression evidence |
| Reviewer | Independently checks specification, design, code, tests, and diff |
| Tester | Independently runs mechanical validation and preserves commands, logs, timing, and failures |
| Investigator | Reproduces and isolates unclear, flaky, concurrent, cross-component, or E2E failures |
| User | Makes final product-rule, scope, and direction decisions |

These are responsibilities, not model names. Current executor preferences are isolated in
`Docs/ai/MODEL_ASSIGNMENTS.md` and never change task authority. An Architect cannot
approve its own design in the same session, and a worker cannot approve its own output.

## Starting a session

1. Read `AGENTS.md`.
2. Run the responsibility entry shown by the assigned task:
   `python scripts/ai_status.py integrate|architect|implement|review|fix|test|investigate`.
3. Read the packet named in `Docs/ai/TASKS.md`.
4. Read only the packet's canonical/design sources.
5. Inspect Git status and the relevant diff.

Do not require previous chat history. Current facts belong in `CURRENT_STATE.md`; current
work belongs in `TASKS.md`; one worker assignment belongs in `tasks/T*.md`; one concise
result belongs in `handoffs/tasks/T*.md`.

## Work lifecycle

```text
Integrator: READY packet and non-overlapping scope
Worker:     IN_PROGRESS -> tested handoff -> stop
Reviewer:   independent evidence and findings
Integrator: verify -> DONE or another bounded task
```

Workers are short-lived and complete one task or a tightly related small group. Tasks that
write the same file run serially. Shared coordination documents are updated by the
Integrator unless the packet explicitly assigns another writer.

## Documentation ownership

| Information | Source of truth |
|---|---|
| Permanent responsibility and safety rules | `AGENTS.md` |
| Workflow lifecycle | `Docs/ai/WORKFLOW.md` |
| Stable system and coordination boundaries | `Docs/ai/ARCHITECTURE.md` |
| Delegation, escalation, recovery, and long-test procedures | `Docs/ai/OPERATIONS.md` |
| Current facts and measured tests | `Docs/ai/CURRENT_STATE.md` |
| Current board | `Docs/ai/TASKS.md` |
| Current model preferences | `Docs/ai/MODEL_ASSIGNMENTS.md` |
| Phase scope and completion | `Docs/ai/ROADMAP.md` |
| Canonical game conclusions | `Docs/ai/spec/DESIGN.md` |
| Decisions and rationale | `Docs/ai/decisions/` |
| Active review findings | `Docs/ai/REVIEW_INBOX.md` |
| Unresolved user decisions | `Docs/ai/OPEN_QUESTIONS.md` |
| Test obligations | `Docs/ai/TEST_POLICY.md` |

Historical status lines and actor/model labels are evidence and are not current routing
authority. Do not rewrite them merely to modernize terminology.

## Implementation rules

The review checklist in `AGENTS.md` is authoritative. In particular:

- Do not add role-specific branches to the game core or AI client.
- Adding a role must not require Python changes.
- Use `inspect_result` / `medium_result` for those results, never `team`.
- Use `count_as` for win counts.
- Keep internal death causes and private information off unauthorized network paths.
- Do not hardcode rule defaults.
- Do not change specification to make an implementation pass.

## Tests and evidence

Typical validation is:

```bash
python scripts/check_docs.py
python -m pytest -q
git diff --check
```

Run the focused tests and regressions named by the packet as well. Handoffs record the
execution environment, command, exit status, exact pass/fail counts, files changed, known
limitations, and review status. Do not record another responsibility's unperformed approval.

## Git safety

Preserve inherited and user changes. Do not use destructive cleanup, history rewriting, or
force push. Do not commit unless the user explicitly authorizes it.

## Ending a task

- Review the scoped diff.
- Write the packet's task handoff.
- Record new decisions or repeatable failures in their canonical directories.
- Return to the Integrator for verification.
- Stop without beginning the next task.
