# Multi-chat Development Workflow

This workflow coordinates an Integrator and multiple short-lived workers through repository
memory. Responsibilities, sessions, tasks, and models remain separate.

```text
                 Integrator
                     |
       Architect (when required)
                     |
          +----------+----------+
          |          |          |
     Implementer Implementer Investigator
          |          |          |
          +----------+----------+
                     |
                   Tester
                     |
                  Reviewer
                     |
                 Integrator
```

## Integration loop

1. Read `CURRENT_STATE.md` and `TASKS.md` through `ai_status.py integrate`.
2. Select READY tasks whose dependencies and Design Gate are satisfied.
3. Create or verify a model-neutral task packet under `tasks/`.
4. Compare expected/shared files and conflict risk before parallel dispatch.
5. Give each worker one task, or one tightly related small group.
6. The worker changes only its scope, runs tests, writes `handoffs/tasks/T*.md`, and stops.
7. Route mechanical validation to a Tester and design/implementation evaluation to an
   independent Reviewer when required.
8. For an ordinary `CHANGES_REQUIRED`, return the same bounded task to an Implementer,
   run Tester evidence, and ask a fresh Reviewer session to re-review. Do not ask the user.
9. After three failed rounds on the same cause, route it to an Investigator. A public API,
   lifecycle, concurrency, persistence, protocol, or unresolved product choice goes to an
   Architect. Only a material choice still unresolved after that goes to the user.
10. The Integrator checks actual diff, raw test evidence, review status, and conflicts.
11. Only the Integrator advances `TASKS.md` and `CURRENT_STATE.md` from verified evidence.
12. Select the next safe wave. A `DECISION_REQUIRED` task does not stop independent READY
    tasks; stop globally only for repository risk, missing credentials/authentication, or
    when every safe task depends on the same user decision.

## Short-lived workers

A worker does not need prior weeks of conversation. Its startup set is `AGENTS.md`, the
task packet, packet-named canonical/design files, and current Git status/diff. The handoff
records results and measurements, not hidden reasoning or a transcript. Completing a packet
ends the worker session; starting adjacent work requires another task.

## Integrator continuity

The Integrator may retain a longer chat, but chat history is never the sole memory. A new
Integrator must be able to resume from `CURRENT_STATE.md`, `TASKS.md`, decisions, task
handoffs, Git, and tests. Any fact needed after a lost chat must be externalized there.

## Parallel safety

- Every packet declares expected files, shared files, and conflict risk.
- Tasks with overlapping writes are serial by default.
- Read-only overlap is allowed; concurrent mutation of board/state/review queues is not.
- Prefer the host's standard task/worktree isolation. Do not build a custom supervisor,
  scheduler, state machine, resume engine, model router, or automatic merge layer.
- Workers preserve unrelated dirty changes and never use destructive Git cleanup.
- The Integrator resolves necessary scope expansion before a worker edits extra files.

## Review independence

Independence is defined by responsibility and session independence. An Architect
cannot approve its own design in the same session. A worker cannot approve its own
implementation. Using another model for a high-risk review is an operational choice in
`MODEL_ASSIGNMENTS.md`, not workflow authority.

## State transitions

Task states are `READY`, `IN_PROGRESS`, `REVIEW`, `BLOCKED`, `DECISION_REQUIRED`, `DONE`,
and `CANCELLED`.

```text
READY -> IN_PROGRESS -> REVIEW -> DONE
  |           |           |
  +--------> BLOCKED <-----+
  +--------> DECISION_REQUIRED
  +---------------------> CANCELLED
```

Only verified evidence moves a task to DONE. A worker completion message alone does not.

## Main-thread display

Use one short status line per transition: `▶ START`, `✓ DONE`, `↻ RETRY`, `⚠ DECISION`,
or `✕ BLOCKED`. Keep worker logs in handoffs/test logs and show details only when they affect
the next action. Display responsibilities, not executor model names.
