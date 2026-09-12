# D067 — Standard multi-agent roles and operating boundary

Date: 2026-09-10

Status: Accepted by explicit user instruction.

## Context

D065 removed the archived autonomous-development runtime and D066 separated durable
responsibilities from replaceable executor models. The active five-responsibility catalog
still used the label Detailed Designer and had no independent mechanical Tester. The user
requires one Main Integrator coordinating short-lived standard agents without recreating an
orchestration product.

## Decision

- The active roles are Integrator, Architect, Implementer, Reviewer, Tester, and
  Investigator.
- Architect replaces the active Detailed Designer label. Historical uses of Detailed
  Design/Designer remain evidence and do not need bulk rewriting.
- Tester owns mechanical execution evidence but no product or architecture decision.
- Role contracts are model-neutral under `Docs/ai/roles/`; current executor choices remain
  only in `MODEL_ASSIGNMENTS.md`.
- The Main Integrator uses the host's standard delegation and isolation facilities, concise
  task packets, Git, tests, and handoffs. It does not introduce a supervisor, scheduler,
  state machine, resume engine, model router, quota manager, or automatic merge framework.
- Ordinary review findings loop through Implementer, Tester, and a fresh Reviewer without
  user intervention. After three failed rounds on one cause, use Investigator.
- Architect handles material boundaries. Only a product choice still unresolved after
  canonical-source and Architect review is escalated to the user. Other independent READY
  work continues while that decision waits.

## Consequences

Changing an executor assignment does not require changing roles, prompts, external-memory
structure, task packets, or authority. `DECISION_REQUIRED` is a task-local state, not a
global pause. Long-running validation may repeat ordinary tests with bounded processes and
ignored logs, but it never writes source or resumes archived autonomous work.
