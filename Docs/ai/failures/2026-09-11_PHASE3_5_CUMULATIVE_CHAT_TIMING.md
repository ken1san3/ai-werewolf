# Phase 3.5 cumulative chat timing nondeterminism

Date: 2026-09-11

Status: CLOSED — T018 ported the proven Phase 3.4 chat-wave gate and passed the bounded cumulative node

## Symptom

After T016 restored the inherited exact all-seat contract, the Phase 3.5 cumulative completion
node failed once in the first `same-a` scenario after 24.29 seconds. The large failure diagnostic
was truncated before the exact Day-1 count/player was retained. A speaking player's later Day-4
deadline suppression was visible but must not be mistaken for the missing Day-1 assertion.

## Reproduction history

```text
Combined Tester:
python -m pytest -q tests/test_phase3_5_completion.py
FAIL: 1 failed in 24.29s, same-a wrapper at line 651

T017 Investigator, one permitted instrumented exact run:
python -m pytest -q -s --tb=short tests/test_phase3_5_completion.py::PhaseThreeFiveCompletionTests::test_nine_process_cumulative_reservations_are_exact_and_reproducible
PASS: 1 passed in 64.95s
```

The passing run measured exactly two Day-1 chats for all nine seats in both all-seat scenarios,
and zero for the silent seat plus two for every other seat in the silent scenario.

## Classification

Completion-harness timing nondeterminism, not a demonstrated production/shared-arbiter failure.

Phase 3.4's proven completion harness holds Day-1 peer chats until all speaking seats complete
the first accepted wave, then flushes peer delivery and verifies the second wave. Phase 3.5
instead immediately broadcasts the full peer-chat burst inside the same compressed two-second
test day. The instrumented passing run observed only `reaction_chat` arbiter ownership in Day 1,
with sub-5ms invocations and no reservation contention.

T015 is not implicated. T016 exposed the weakness by restoring the correct assertion; the
assertion must not be weakened.

## Repair boundary

Port the Phase 3.4 Day-1 peer-delivery wave gate into the Phase 3.5 completion harness only.
Preserve the two-second day, all timeouts, retry/skip policy, server-authoritative acceptance,
and exact two-chat assertion. Add a compact failing-player/count summary before the complete
bounded diagnostic bundle so future tool-output truncation cannot hide the decisive value.

Do not change production ReactionChat, VoteAbility, BrainController, or BrainInvocationArbiter
without new evidence.

## Resolution

T018 ported the established Phase 3.4 Day-1 peer-delivery wave gate into only the Phase 3.5
completion harness. Author echo, reservation/action-state traffic, and replies remain immediate;
only Day-1 peer `chat.message` delivery is held until every speaking seat completes wave 1, then
wave 2. The harness now requires both waves to flush inside the unchanged two-second Day-1 budget.

The exact all-seat two-chat assertion, one-silent zero/other-speaking-at-least-one assertion,
server-authoritative reservation evidence, process identity checks, timeouts, retries, and skip
policy remain unchanged. Compact per-seat Day-1 counts and expected/actual failing-player evidence
now precede the larger bounded diagnostic bundle.

One post-repair cumulative run under normal local Windows permissions passed:

```text
python -m pytest tests/test_phase3_5_completion.py -q
PASS: 1 passed in 64.80s
```

No production code was changed. Reopen only if the exact cumulative node fails again after T018;
preserve the compact count/wave evidence when classifying any new failure.
