# AIwolf Runbook

Responsibility labels below are model-independent. Current executor preferences live only
in `MODEL_ASSIGNMENTS.md`. Detailed role contracts are under `roles/`.

## 1. Integrator session (`integrate`)

1. Run `python scripts/ai_status.py integrate`; inspect Git status and the relevant diff.
2. Reconcile `CURRENT_STATE.md`, `TASKS.md`, handoffs, OPEN reviews, and measured tests.
3. Select READY work with satisfied dependencies and Design Gate. Compare expected/shared
   files before parallel dispatch; serialize overlapping writers.
4. Dispatch one model-neutral packet to each short-lived worker using the host's standard
   delegation/isolation. Never construct a custom supervisor or automatic merge engine.
5. Route architecture work to an Architect, implementation to an Implementer, mechanical
   evidence to a Tester, independent evaluation to a Reviewer, and unclear causes to an
   Investigator.
6. On ordinary `CHANGES_REQUIRED`, return the bounded issue to an Implementer, then Tester,
   then a fresh Reviewer. After three failed rounds on the same cause, use an Investigator.
7. Ask the user only for a material product choice unresolved by canonical sources,
   decisions, code, and Architect analysis. Continue independent READY tasks while waiting.
8. Verify actual diff, tests, findings, handoff, and conflicts. Only then update `TASKS.md`
   and `CURRENT_STATE.md`; never pre-record another responsibility's approval or evidence.
9. Keep the main-thread report compact (`▶ START`, `✓ DONE`, `↻ RETRY`, `⚠ DECISION`,
   `✕ BLOCKED`) and name the exact next action.

## 2. Architect session (`architect`; `design` compatibility alias)

1. Run `python scripts/ai_status.py architect` and read the assigned task packet.
2. Read the target ROADMAP section, request, canonical specification, and only the needed
   decisions and implementation facts.
3. Define scope, non-scope, public interfaces, data ownership, lifecycle/state, failure
   behavior, concurrency, acceptance criteria, and required tests. Do not implement code.
4. Escalate a product choice only when sources do not determine it; provide bounded options,
   effects, and a recommendation to the Integrator.
5. Leave the design reviewable and write the required handoff. A separate Reviewer session
   must approve it before implementation becomes READY.

## 3. Implementation session (`implement`)

1. Run `python scripts/ai_status.py implement` and read the assigned task packet.
2. Read only its canonical sources, approved design, and relevant test policy.
3. Stop if dependencies, allowed scope, or a required Design Gate are unresolved.
4. Preserve unrelated changes and modify only expected files unless scope expansion is
   explicitly coordinated with the Integrator.
5. Implement the contract without changing product specification. Add focused/regression
   tests but do not weaken existing assertions or hide production bugs in tests.
6. Run focused checks and write the packet handoff with exact evidence. Return to the
   Integrator; do not self-approve or begin adjacent work.

## 4. Review session (`review`)

1. Run `python scripts/ai_status.py review` and read the assigned packet and handoff.
2. Independently compare canonical specification, actual code/schema, tests, and detailed
   design in that order. Do not rely on the worker's completion claim.
3. Apply `AGENTS.md`, inspect the scoped diff, and run proportionate verification.
4. Return exactly `APPROVED`, `CHANGES_REQUIRED`, or `ARCHITECTURE_REVIEW_REQUIRED` with
   issues, severity, required fix, test assessment, and regression risk.
5. Record actionable findings only when assigned that queue write. Never approve a design
   authored in the same session and never implement fixes while acting only as Reviewer.

## 5. Review-fix session (`fix`)

1. Run `python scripts/ai_status.py fix` and read the assigned findings and task packet.
2. Address OPEN findings in severity order and only within their required scope.
3. Preserve user changes, avoid opportunistic redesign, and add or strengthen a regression
   that fails for the reported defect.
4. Run focused and relevant normal tests plus document/diff checks. Append measured repair
   evidence to the task handoff; a separate Reviewer closes the review.

## 6. Test session (`test`)

1. Run `python scripts/ai_status.py test` and read the assigned task packet and handoff.
2. Verify commands and environment independently. Run focused tests first, then named
   regressions/completion checks, then the normal project suite when proportionate.
3. Preserve stdout/stderr, exit codes, pass/fail/skip counts, durations, timeouts, and the
   first reproducible failure. Do not edit product code or make a design judgment.
4. For a hang or unexplained/flaky failure, stop the bounded run and return deterministic
   reproduction evidence to the Integrator for Investigator routing.
5. Report measured evidence using the handoff format and stop.

## 7. Investigation session (`investigate`)

1. Run `python scripts/ai_status.py investigate` and read the investigation packet.
2. Reproduce with the smallest safe command; preserve raw exit status and decisive output.
3. Isolate cause across component boundaries without silently changing specification.
4. Prefer read-only diagnostics and minimal probes. Do not begin a broad fix.
5. Write reproduction steps, evidence, likely cause, affected scope, risks, and recommended
   implementation task to the handoff. Then stop.
