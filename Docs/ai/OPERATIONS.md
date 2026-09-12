# Multi-Agent Operations

This is the small operating contract for the Main Integrator and short-lived workers. It
uses standard agent delegation and Git/test tools; it is not an autonomous-development
runtime.

## Main loop

1. Run `python scripts/ai_status.py integrate`; reconcile Git, state, board, handoffs,
   reviews, and latest measured tests.
2. Choose READY work on the critical path. Check dependencies, Design Gate, expected/shared
   files, and conflict risk.
3. Dispatch one packet per worker. Use Architect for material boundaries, Implementer for
   code, Tester for mechanical evidence, Reviewer for independent judgment, and Investigator
   for unclear causes.
4. Prefer platform-provided worktree/isolation. In a shared working tree, parallelize only
   read-only tasks or writers with non-overlapping files; never let workers concurrently
   write board/state/review queues.
5. Verify returned evidence. Normal findings loop automatically through Implementer ->
   Tester -> fresh Reviewer. After three failed rounds on the same cause, use Investigator.
6. Update `TASKS.md` and `CURRENT_STATE.md` only at meaningful transitions, then select the
   next independent wave.

Live agent/task IDs are runtime state supplied by the host and are not copied into repository
memory. A replacement Main session inspects the host task tree before redispatching an
`IN_PROGRESS` task; if runtime state is unavailable, it treats the assignment as needing
reconciliation rather than silently creating a duplicate worker.

Main-thread transitions stay compact:

```text
▶ START  Txxx Reviewer
✓ DONE   APPROVED
↻ RETRY  Txxx Implementer
⚠ DECISION Tyyy
✕ BLOCKED Tzzz
```

Do not display executor model names in normal status. Put long output in handoffs or ignored
test logs.

## Task record

Each `TASKS.md` record contains `Task ID`, `Title`, `Role`, `State`, `Priority`,
`Dependencies`, `Scope`, `Goal`, `Acceptance`, `Tests`, `Notes`, `Expected files`,
`Design Gate`, `Review required`, `Task packet`, and `Handoff path`. A design-producing task
uses `Design Gate: PRODUCES DESIGN`; implementation uses `APPROVED` or `NOT REQUIRED`.
Allowed states are `READY`, `IN_PROGRESS`, `REVIEW`, `BLOCKED`, `DECISION_REQUIRED`,
`DONE`, and `CANCELLED`.
Task packets add canonical sources, allowed/out-of-scope boundaries, shared-file conflict
risk, and handoff destination.

## Handoff and review

Workers use `handoffs/tasks/TEMPLATE.md`. Reviewers use
`handoffs/tasks/REVIEW_TEMPLATE.md`. A completion claim is evidence, not approval. The
Integrator checks the actual diff and an independent test result before `DONE`.

## Decision routing

Public API, Brain/World boundary, protocol, concurrency, persistence, server lifecycle,
large data-model changes, or multiple fundamentally conflicting implementations go to an
Architect. Ask the user only when canonical sources, decisions, code, and Architect analysis
still leave a material product choice.

Use this form:

```text
━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠ DECISION REQUIRED — Txxx
━━━━━━━━━━━━━━━━━━━━━━━━━━
内容:
なぜユーザー判断が必要か:
影響範囲:
案A（利点 / 欠点）:
案B（利点 / 欠点）:
Architect推奨と理由:
回答方法: A / B / その他
継続中の独立task:
```

Mark only the affected task `DECISION_REQUIRED`. Continue unrelated READY tasks. Stop all
work only for repository-destruction risk, missing credentials/external authentication, or
when every safe task depends on the same unresolved user decision.

## Failure and recovery

- Preserve the first failure, command, environment, logs, and reproduction.
- A clear local defect returns to an Implementer; an architectural mismatch goes to an
  Architect; a flaky/cross-component/unknown cause goes to an Investigator.
- Isolate only the failed task. Do not resume an archived run, build recovery state, discard
  inherited changes, or auto-merge.
- On conflict, compare base/current diff and integrate deliberately. Never erase another
  worker's changes.

## Bounded long regression

The long runner executes ordinary tests repeatedly and never edits source files:

```powershell
python scripts/run_long_regression.py --hours 7 --per-run-timeout 900
```

Logs and `SUMMARY.md` go to ignored `logs/long-regression/<timestamp>/`. Each pytest child
has a hard timeout; timeout/cancel terminates its process tree. Stop with `Ctrl+C`, or create
the displayed `STOP` file for a graceful stop between cycles. A nonzero test exit ends the
run by default and preserves the first failure. Morning review begins with `SUMMARY.md`,
then only the first failed run's logs. This is test repetition, not unattended code writing.

Safe smoke command:

```powershell
python scripts/run_long_regression.py --hours 0.01 --per-run-timeout 60 --max-runs 1 --target tests/test_ai_status.py::DesignGateTests::test_task_records_are_model_neutral_board_records
```

Before and after a long run, record `git status --short`; no product source diff should be
created. The Integrator does not begin implementation merely because a long test completed.
