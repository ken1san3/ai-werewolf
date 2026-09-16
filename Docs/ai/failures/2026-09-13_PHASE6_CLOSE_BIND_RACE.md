# Phase 6 close-versus-first-bind race

Observed: 2026-09-13, Local Windows / CPython 3.13.3
Source: independent T232 review; raw `logs/t232-review/close-bind-probe.txt` and T232 handoff.
Affected frozen runtime SHA-256: 5daa5d8b7a583b7586b0048a0b59de199b30b0c250592fd1a21a6fe5a143be01
Classification: HIGH product lifecycle defect, separate from filesystem environment failures.

## Deterministic schedule and observations

Existing harness pending.on_bind schedules runtime.aclose. Hold audit.aclose with an explicit
async gate after cleanup has passed the absent store/feature owners, then allow first-bound startup
to resume. No scheduling sleeps are required. Startup constructs store/Brain/Arbiter/features and
starts both features while lifecycle is STOPPING. Releasing audit close does not stop these newly
created owners: cached cleanup gathers their still-live waits and close exceeds the probe's 2s
bound. Raw evidence records store_closed=false, reaction_stopped=false, vote_stopped=false and
remaining cleanup/monitor/feature tasks. The probe drains its fake owners afterward; no product
source was changed during reproduction.

## Cause and bounded repair scope

Phase 6 start suspends at first-bound wait. aclose may pass optional graph owners during that await.
The resumed composition never checks stop/cleanup ownership before creating and starting them.
The one cached cleanup pass cannot revisit owners it already passed.

Repair only ai_client/runtime.py and tests/test_phase6_runtime.py under a separate implementation
packet. Preserve lossless CURRENT ordering, once-only cleanup, public interfaces and Phase 5 path.
A close winning the bind race must prevent late graph/resource creation, finish boundedly, preserve
consistent final lifecycle/exit and cleanup-error reporting, and remain safe under repeated close
and cancellation. Add a deterministic public-observable regression using explicit gates. Do not
weaken assertions, add sleeps/retries, or change World/Network/provider/acceptance contracts.

## Independent follow-up and route reassessment

T234 verified T233 closes the late-owner hang but returned FAIL for two HIGH gaps on runtime hash
fd99b4378b64859975371c9dca636e28b56db15239e0ad1c6eb344da7e7908da:
- Normal close-won start creates no monitor, so public wait/run raises requires-start in all eight
  before/at-bind x run/start-wait x clean/audit-fault vectors despite a completed terminal exit.
- If on_bind schedules close and cancels run before startup consumes an already-failed bound
  Future, clean cleanup publishes successful STOPPED instead of preserving invalid context.
  The paired audit-error vector correctly retains stronger CLEANUP_FAILED.

Raw logs/t234-test/public-boundary-probe.txt: 10 vectors, 9 FAIL / 1 PASS, 0.183 seconds,
zero remaining tasks in every vector. T234 handoff records exact schedules and unchanged hashes.
Main verified that handoff SHA-256 on 2026-09-13. Existing supplied 29 + 45 subtests and selected
adjacent 291 + 17 pass; they do not close these public-entrypoint gaps.

Under D068, T235 reassesses the whole pre-monitor state/owner/result boundary after repeated P6-E
acceptance failures. Main verified its bounded route and complete matrix before T236 dispatch.

T235 also reproduced invalid-bind cancellation without explicit close losing the context code to
CancelledError, and HIGH F3: valid CURRENT during audit cleanup binds Pending after manifest disposal,
creating a synthetic context failure. Raw logs/t235-investigation/probe.txt has three bounded
observations in 0.055s with no remaining owners. Its handoff defines source retirement before data
disposal, preservation of ready facts, public completion distinction and unchanged legacy semantics.

## Verified closure

T236 repaired this complete pre-monitor boundary. T237 independently passed its matrix; T240
reran the unchanged assertions on final monitored-result repair bytes. T241 freshly APPROVED
complete P6-E with zero findings. Main verified exact evidence/hash before marking cumulative
implementation records DONE on2026-09-13. T241 handoff owns final independent approval; this record
retains original failures and does not replace measured evidence or active dispatch authority.
