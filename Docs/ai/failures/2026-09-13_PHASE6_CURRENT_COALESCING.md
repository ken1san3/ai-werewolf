# Phase 6 CURRENT snapshot coalescing race

## Attempt

T228 independently tested the frozen T227 P6-E runtime, then T229 reproduced both affected
schedules with bounded, model-free, no-sleep in-memory probes:

- invalid first CURRENT followed by valid CURRENT before the bind waiter resumes;
- valid bound CURRENT followed by invalid and then valid CURRENT before the monitor resumes.

T229 also exercised the proposed sole-consumer handshake with the real `WorldState` and an
in-memory public `NetworkEventSource`. The full evidence is in
`Docs/ai/handoffs/tasks/T229_PHASE6_CURRENT_COALESCING_INVESTIGATION.md` (SHA-256
`05b9419763fc801ec27b7bc1d71ba0422a0c477ddc728b9216203dea064398a9`).

## Problem

`WorldState.run()` can synchronously consume and commit more than one already-ready Network event
without yielding. `_commit()` retains only the latest immutable snapshot, while
`wait_for_update(version)` deliberately returns that latest snapshot. T227's first-bind and later
context-monitor waiters can therefore miss an overwritten invalid CURRENT. This is a confirmed
cross-component production/design-contract race, not a timing flake.

## Result

T229 returned `READY_FOR_BOUNDED_REPAIR`. The smallest repair is a private Phase-6-only proxy in
`ai_client/runtime.py`: it is the sole physical Network iterator consumer, yields every event once
to World, and inspects the exact committed public World snapshot before requesting the next event.
A one-shot first-CURRENT composition gate prevents subsequent events until the P6-E graph and
context outcome are installed. The repair and deterministic regressions remain limited to
`ai_client/runtime.py` and `tests/test_phase6_runtime.py`.

No repair, product/test source edit, provider/model run, Q8, ACL/security change, or Git history
operation was performed by T229. P6-E remains unapproved and P6-F remains blocked.

## Do Not Repeat

Do not repeat T228/T229's diagnosis, rely on polling or scheduler priority, reject version jumps,
infer authoritative identity from raw Network events, add a second consumer or unbounded queue, or
serialize only the test. Do not change World/Network public contracts unless the bounded runtime
proxy is independently shown insufficient. Keep the Acronis `WinError 5` TemporaryDirectory denial
classified separately as environment evidence; do not weaken acceptance, retry-loop it, relocate
the test, modify ACLs, or disable security software.
