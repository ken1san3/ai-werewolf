# Architecture Boundaries

This file is a compact map, not a replacement specification. Follow the sources in the
authority order below and open only the section needed by the current task.

## Authority order

`INDEX.md` distinguishes product authority, coordination, actual state, evidence, and history.
The ordering below compares product sources, not timestamps or dispatch permission.

1. `spec/DESIGN.md` — canonical game conclusions
2. implementation plus protocol/schema facts
3. tests and measured runtime evidence
4. accepted decisions under `decisions/`
5. independently approved detailed design under `design/`
6. task packet and handoff

A lower source cannot silently override a higher one. Product ambiguity goes through the
Architect and, only when still unresolved, the user.

## Game lifecycle

The playable critical path is boot -> player initialization -> role assignment -> day
observation/chat -> voting/execution -> night abilities/death processing -> win evaluation
-> next day or game end. `ROADMAP.md` defines phase scope and completion; this file does not
add behavior.

## Product boundaries that must remain intact

- The server is the single source of truth. Network clients submit requests; the server
  validates permissions, targets, timing, transitions, and results.
- The game core and AI client are separate programs. The core has no language-model
  dependency and remains testable without one.
- Network transports validated player-visible events. World builds one client's immutable
  view; Brain decides behind its interface; controllers schedule and dispatch only the
  actions authorized by received handles/deadlines.
- Phase transitions and random choices are server-owned and event-recorded. Network code
  does not reimplement game rules.
- Roles, teams, alignments, knowledge, abilities, passives, effects, win conditions, and chat
  permissions remain separate data concepts. Game content comes from YAML, not role-name
  branches in Python.
- Secrets are routed by visibility at the server boundary. Internal death causes and other
  private facts never travel through broadcast paths.
- Protocol messages remain language-independent and versioned. Public API, protocol,
  lifecycle, concurrency, persistence, and data-ownership changes require Architect review.

Task-specific detail lives in `spec/DESIGN.md`, `ROADMAP.md`, accepted decisions, and the
approved phase design named by the packet.

## Coordination architecture

Responsibility, executor model, session, and task are independent:

```text
Role contract -> grants authority and required output
Task packet   -> grants bounded scope and acceptance criteria
Model setting -> selects the current executor only
Session       -> executes authorized scope; independent approval needs a separate session
```

The Integrator is the only normal writer of `CURRENT_STATE.md` and `TASKS.md`. Workers write
task output and one concise handoff. Tester supplies mechanical evidence. Reviewer supplies
an independent verdict. Architect supplies design, not approval. This separation survives
executor changes because role documents and task packets contain no model assignment.

## External-memory topology

- `INDEX.md`: route to the minimum context
- `CURRENT_STATE.md`: phase/target/critical path/hold and evidence pointers
- `TASKS.md`: sole lifecycle authority; `tasks/`: bounded contracts and assignment snapshots
- `roles/`, `RUNBOOK.md`, `OPERATIONS.md`: stable responsibility and operating rules
- `handoffs/tasks/`: concise worker results
- `REVIEW_INBOX.md`: actionable review findings only
- `decisions/`, `failures/`: durable rationale and repeatable failures
- archive/review history: evidence, never live routing authority

No daemon, scheduler, planner engine, resume engine, state machine, quota manager, model
router, or automatic merge layer belongs in this architecture. Standard task delegation,
Git, tests, and repository memory are sufficient.
