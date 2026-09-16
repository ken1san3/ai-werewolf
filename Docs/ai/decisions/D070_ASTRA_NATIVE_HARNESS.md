# D070 — Astra-native harness authority and continuation

Date: 2026-09-13
Status: Adopted within the user's explicit harness-migration authorization; product resumption
requires the separate human review recorded in CURRENT_STATE.

## Context and applicability

Migration found conflicting live-next-action copies: the old Main snapshot asks for T230 repair,
the board has T231 IN_PROGRESS, and T231 already has a returned PASS artifact. Repeated Main
stop/pipeline instructions make those stale copies operationally hazardous. No product decision
or acceptance change is needed to resolve this harness problem.

## Decision

`AGENTS.md`, `INDEX.md` and `OPERATIONS.md` now own the operating rules. TASKS alone owns lifecycle;
CURRENT_STATE owns phase/target/holds and pointers, with its existing checker-enforced state mirror.
Packets and handoffs retain assignment/history evidence. Main selects work and responsibilities by
risk within authorized scope and continues across packet/wave boundaries; workers stay bounded.

This supersedes only D051's mandatory copied next-message/Next Task delivery convention and D067's
universal Implementer -> Tester -> Reviewer routing where the task does not require those separate
gates. D051's gate criteria, independent design approval, no self-approval and source precedence;
D065 archive boundary; D066 responsibility/model separation; D068 semantic PASS authority and
three-failed-objective route reassessment; all existing product/Phase 6 acceptance gates remain.
No model assignment, runtime/provider policy, product test or approved design is changed.

## Evidence and limits

`../handoffs/ASTRA_MIGRATION_REPORT_2026-09-13.md` records the mechanism audit, independent review,
measured validation, exact change scope and deferred work. It is a migration result, not another
live state authority. No scheduler/state database, process owner registry or automatic dispatch
was added. Future product continuation requires explicit user release of the migration hold.
