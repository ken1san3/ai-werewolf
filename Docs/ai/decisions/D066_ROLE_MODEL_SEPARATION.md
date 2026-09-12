# D066 — Responsibility / model separation and repository-backed coordination

Date: 2026-09-09

Status: Accepted by explicit user instruction.

## Context

The former workflow mixed durable responsibility names with replaceable executor models and
relied on long-running chat context. The autonomous-development runtime is already frozen
and removed under D065. AIwolf now needs a small repository-backed coordination layer for
an Integrator and multiple short-lived workers.

## Decision

- Responsibility, model, chat/session, and task are separate concepts.
- Responsibility defines authority and required output; it does not change with the model.
- Current model assignments are operational settings stored only in
  `Docs/ai/MODEL_ASSIGNMENTS.md`.
- Active workflow documents and task packets are model-neutral.
- Past model-specific status lines, reviews, handoffs, failures, decisions, and research are
  retained as historical evidence rather than rewritten.
- D051's Design Gate, criteria for detailed design, and canonical-source precedence remain
  active. Only D051's concrete default-model assignment table is superseded by this decision.
- D053's model-specific routing is historical. Its evidence for using independent review is
  retained.
- Self-approval remains prohibited. Independence is defined by responsibility plus session
  independence; high-risk use of a different model is an operational preference, not an
  authority rule.
- Current coordination uses `CURRENT_STATE.md`, `TASKS.md`, model-neutral packets under
  `tasks/`, and concise evidence under `handoffs/tasks/`.
- Workers are short-lived and Integrator continuity must survive loss of chat history.

## Consequences

- A model can be replaced by editing one assignment file without changing prompts, role
  definitions, task scope, or review authority.
- The Integrator owns task partitioning, overlap checks, verified state transitions, and the
  next wave; workers do not concurrently mutate shared coordination files by default.
- A READY implementation task still cannot bypass an unsatisfied Design Gate.
- Historical actor/model labels describe who actually performed old work and remain valid
  evidence, but they do not route current work.
