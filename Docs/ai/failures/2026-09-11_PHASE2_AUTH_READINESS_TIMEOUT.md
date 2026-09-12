# Phase 2 completion readiness timeout caused by stale protocol 1.0 fixture

Date: 2026-09-11
Status: CLOSED — T014 fixture repair independently APPROVED 2026-09-11
Classification: deterministic completion-harness compatibility defect; not a production bug

## Symptom

The Phase 2 nine-process completion node fails after approximately ten seconds with:

```text
AssertionError: timed out waiting for all clients to publish authentication readiness
```

It was first observed as the only failure in T012's full regression (`406 passed`, `707
subtests passed`, `1 failed`) and then reproduced once as the isolated exact node (`1 failed in
11.11s`).

## Cause

`tests/fixtures/network_dummy_client.py` sends `protocol_version: "1.0"`. The active server was
intentionally upgraded by T009 to exact protocol `1.1`; an unauthenticated version mismatch is
rejected before `session.joined` and player state. Because the fixture publishes readiness only
after `session.joined` / `session.resumed`, all readiness markers are impossible and the outer
wait hides the immediate handshake incompatibility behind a timeout.

This is not Windows scheduling nondeterminism. Earlier T007 repeated passes occurred before the
T009 active protocol transition. Exact 1.1 rejection of a 1.0 peer is correct production behavior
under the approved Phase 3.5 design.

## Reproduction

Run the exact node when validating a repair, using normal local Windows permissions:

```text
python -m pytest tests/test_phase2_completion.py::PhaseTwoCompletionTests::test_separate_process_clients_complete_game_using_only_protocol_actions_and_server_ticks -q
```

Current result before repair: `FAIL — 1 failed in 11.11s`, at the initial nine-client readiness
wait before replacement-client and game-action assertions.

## Repair boundary

- Change only the Phase 2 completion fixture/test needed to use active protocol 1.1 and report an
  early child exit clearly.
- Preserve the ten-second readiness bound, one-second game phases, authentication/readiness PID
  assertions, zero action-rejection assertion, positive action evidence, and process cleanup.
- Do not re-enable live 1.0 peers, edit the historical 1.0 schema, extend timeouts, add retries,
  skip the test, or weaken assertions.
- T012 / Phase 3.5 Vote・Ability Controller is not implicated.

Full investigation evidence:
`Docs/ai/handoffs/tasks/T013_PHASE2_AUTH_READINESS_FLAKE_INVESTIGATION.md`.

## Closure

T014 updated only the completion fixture to exact protocol 1.1 and added bounded early-child-exit
diagnostics without extending the readiness timeout or weakening acceptance. The exact node passed
in Implementer and independent Tester runs (15.70s, 15.65s, and 15.22s), and an independent
Reviewer approved the repair. No production file was changed for this failure.
