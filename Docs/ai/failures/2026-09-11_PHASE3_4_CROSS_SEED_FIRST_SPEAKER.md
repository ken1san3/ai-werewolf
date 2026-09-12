# Phase 3.4 cross-seed first-speaker scheduling nondeterminism

Date: 2026-09-11

Status: ISOLATED — deterministic completion-clock repair recommended

## Symptom

The unchanged Phase 3.4 completion node completed both primary scenarios during T048 testing, but
the final strict cross-seed assertion failed because seed 7341 and seed 7342 both had `player-3` as
the first server-accepted Day-1 speaker. An unchanged bounded retry passed. All game-end, per-seat
chat count, deadline, rejection, delivery-wave, and process-cleanup assertions passed in the failing
run.

T049 reproduced the same failure class without T048 activity: one direct run of each existing
scenario passed every primary assertion but both seeds produced `player-8` as first speaker.

## Classification

Completion-harness / cross-process clock and scheduling nondeterminism. This is not evidence of a
production Reaction Chat randomness defect and is independent of the T048 active-stale/admission
repair.

The production jitter derivation is stable. Its focused unit/controller tests pass and the selected
seeds have different deterministic first-due seats:

```text
seed 7341: player-5, jitter 0.071146630 seconds
seed 7342: player-1, jitter 0.032223832 seconds
```

The completion assertion does not compare this deterministic due order. It compares the order in
which a Windows server event loop accepts messages from nine independently scheduled child
processes. Each child `_BarrierClock` creates `_origin = time.monotonic()` at its own process startup,
sets `_started_at` when that process first observes the clock-start file, and sets
`_day_one_released_at` when that process first observes the Day-1 release file. Consequently an
initial due time is `process-local base + deterministic jitter`, and actual wake/send/accept order
also contains a second process-local release-observation delay.

The T049 run inferred 0.260010 seconds of base-time spread for seed 7341 and 0.291072 seconds for seed
7342. Either exceeds the complete configured initial-jitter range of 0.200000 seconds. The existing
T004 readiness and delivery-wave barriers guarantee current mappings and exact per-seat counts, but
they do not create a shared cross-process clock origin or a shared real release instant. Thus the
server-accepted first speaker can differ from the deterministic first-due seat and can coincide for
two seeds.

## Evidence

Environment: Local Windows, CPython 3.13.3, inherited dirty `main` at
`994188a8ccc7cc14c4d6626caee4fa633cdc642c`.

```text
T048:
python -m pytest tests/test_phase3_4_completion.py -q
FAIL: 1 failed, 2 passed, 2 subtests passed in 47.42s
first_speaker: seed 7341 == seed 7342 == player-3

same unchanged command, one bounded retry
PASS: 3 passed, 2 subtests passed in 46.51s

T049 deterministic due-order checks:
python -m pytest tests/test_phase3_4_reaction_chat.py::ReactionTypeAndRandomnessTests::test_jitter_is_exactly_reproducible_and_range_independent tests/test_phase3_4_reaction_chat.py::ReactionControllerTests::test_fake_clock_controller_runs_are_reproducible_and_seed_separated -q
PASS: 2 passed in 1.21s

T049 direct existing scenario, seed 7341 / all-seat:
PASS: first_speaker=player-8; Day-1 two-wave completion=0.486893900s

T049 direct existing scenario, seed 7342 / one-silent:
PASS: first_speaker=player-8; Day-1 two-wave completion=0.446481400s
```

No model, production mutation, full regression, retry loop, or external service was used.

## Repair Boundary

Use a separate test-only packet limited to `tests/test_phase3_4_completion.py` and
`tests/fixtures/phase3_4_reaction_client_process.py`.

- Give the parent and all child clocks a shared, parent-authored future monotonic start/release
  target rather than process-local origins and file-observation epochs.
- Hold all child logical clocks at the same Day-1 boundary until every readiness marker exists, then
  release them against that shared future target. Assert no Day-1 chat was accepted before release.
- Preserve the strict cross-seed comparison, the selected seeds, nine processes, two-second server
  day, exact chat counts, delivery waves, deadlines, timeouts, and no-retry behavior. Prefer also
  checking that the observed first speakers are the already specified `player-5` and `player-1`.
- Verify the two deterministic unit nodes and run the unchanged complete Phase 3.4 node a bounded
  three consecutive times under normal local Windows permissions. Do not start a full-regression
  loop from this harness-only repair.

Do not change production Reaction Chat, Brain, Network, server, jitter ranges, seeds, or assertion
strength without new evidence.

## Do Not Repeat

Do not accept one passing retry as proof of deterministic cross-seed order, and do not try to fix the
coincidence by adding sleeps, widening timeouts, changing seeds, weakening/removing the comparison,
or altering production randomness. The nondeterministic value is the cross-process timing base, not
the SHA-256 jitter.
