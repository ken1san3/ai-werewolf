# Phase 6 Discussion Quality — Detailed Design Request

Status: REQUESTED
Request status: OPEN — implementation active under T178-approved detailed design
DESIGN: REQUIRED

## Gate evidence

Phase 5 is complete and checkpointed. T154 proved the exact canonical-9B shared-provider runtime;
T167 approved the external-review remediation with zero findings. D069 closes Q8 by selecting the
current 180-second/no-general-server-cap/two-chat-invocations baseline until Phase 6 quality and
latency are measured. CO remains its existing separate system-action path. Phase 6 changes AI meaning
and prompt/memory behavior, so D051 requires independently approved detailed design before
implementation.

## Canonical scope

ROADMAP Phase 6 requires Belief, Suspicion, Strategy, important-event memory, reaction score,
question/answer, rebuttal, opinion change, line cutting, CO judgment, and pre-vote reassessment. Its
completion condition is that conversation responds to previous speech. D068 adds the measured
quality defect and requires authorized role/objective context, bounded summary plus recent history,
explicit omission/gap signals, privacy, hard byte/token bounds, and a separate private transcript
quality review.

## Required design questions

1. Define model-neutral, role-name-neutral private AI state for belief/suspicion/strategy and its
   ownership, reset/reconnect lifecycle, deterministic bounds, update inputs, and serialization.
2. Define how important events and reaction scores are derived from already-authorized World views;
   never infer or expose hidden server facts and never add AI dependencies to the game core.
3. Define the exact role/team/objective/capability context available to the model from canonical
   data, including missing/unknown behavior, without hardcoded role branches or untrusted text in the
   system instruction.
4. Define a bounded prompt projection using deterministic important-memory summary plus recent
   authorized records. Specify record/byte/token-proxy limits, stable ordering, omission markers,
   complete/gap semantics, and privacy checks. Do not assume an 800–1,000-token optimum or cache gain.
5. Define how the model expresses claims, questions, answers, rebuttals, changed opinions, CO
   judgment, strategy, and pre-vote reassessment through strict structured output without silently
   accepting malformed or unauthorized actions.
6. Define responsive-conversation acceptance using deterministic offline brains/fixtures and
   semantic evidence rather than exact natural-language wording. Separate transport PASS from
   private human content-quality review.
7. Preserve D069: 180-second day, existing shortening/extension, no general server speech cap, and
   at most two chat Brain invocations per phase as a revisitable baseline, with CO left on its
   existing separate system-action path. Phase 6 may measure but may not silently change these
   values.
8. Preserve Phase 3–5 lifecycle, shared admission, deadlines, stale/cancel handling, audit privacy,
   single shared LLM, exact 9B identity, and LLM-free testability.
9. Partition implementation into small non-overlapping tasks with exact files, public interfaces,
   focused/regression/completion tests, and one finite post-implementation canonical-9B game.

## Required sources

- `Docs/ai/spec/DESIGN.md` and design invariants
- `Docs/ai/ROADMAP.md` Phase 6
- `Docs/ai/decisions/D068_EXTERNAL_PHASE5_REVIEW_DISPOSITION_AND_ACCEPTANCE_AUTHORITY.md`
- `Docs/ai/decisions/D069_Q8_PHASE6_BASELINE.md`
- approved Phase 3.2–5 designs and current implementation/tests
- T154 retained measurement and aggregate-only T158/T160 quality findings

## Out of scope

- UI, new roles/MOD/schema migration, game-core rule changes, server speech cap, tempo/default change,
  model routing, 35B/alternate/fallback, model-generated summarization, remote providers, Autodev,
  broad refactors, artifact cleanup, or acceptance weakening

## Design acceptance

- Every Phase 6 item maps to an owned state/interface, deterministic update, bounded projection,
  privacy rule, and test.
- No role-name branch, hidden fact, raw private evidence publication, unbounded prompt/history, or
  model-dependent game rule is introduced.
- The design names objective offline quality evidence and one bounded exact-9B validation path.
- An independent Reviewer approves the design before any Phase 6 implementation starts.
