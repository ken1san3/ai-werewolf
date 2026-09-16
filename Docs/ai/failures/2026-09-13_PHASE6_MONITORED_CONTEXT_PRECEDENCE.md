# Phase 6 monitored context-result precedence

Observed by fresh T238 Reviewer on Local Windows / CPython3.13.3, 2026-09-13.
HIGH product failure on runtime SHA-256
0fbfcae6153c361ac6797d4979d51ca6863a8eacf7698f1830f7449a46fd909d.

After successful first-CURRENT startup, complete Reaction FAILED and schedule bad CURRENT2 then
good CURRENT3 using ordinary call_soon. Exact source consumes valid1 and bad2 only. At monitor
arbitration, non-suspending trace proves raw context Future already contains DiscussionContextError
with DISCUSSION_CONTEXT_INVALID, but wrapper task is not done/in the completed set. Cleanup has
not been created and stop is false. Monitor publishes WORLD_FAILED / false / FAILED / FAILED;
required public result is CONTROLLER_FAILED / false / DISCUSSION_CONTEXT_INVALID / FAILED.

Cause: monitored precedence uses wrapper completion as a proxy for authoritative context truth;
cleanup drains the exception without transferring it to final result. Preserve known context fact
across arbitration/publication, with actual cleanup failure stronger. No post-retirement semantic
change, queue, extra consumer or World/Network/API change is required.

Evidence: T238 handoff and logs/t238-review/context-precedence-probe.txt (0.0283s),
context-arbitration-trace.txt (0.033s). Both diagnostics exit0 confirms observation/drain, not PASS.
No backend/action, one complete cleanup/iterator close, no live tasks/warnings. Fresh40+108 tests
passed but lacked this schedule. Main verified exact review handoff/hash before T239 dispatch.
T236 pre-monitor findings remain closed on measured evidence; P6-E approval remains pending.

## Verified closure

T239 repaired source-fact ownership at arbitration and publication. T240 independently passed
42 tests +111 subtests, adjacent291 +17 and8 public probes including the after-selection/before-
retirement window and caller cancellation. T241 freshly APPROVED complete P6-E with zero findings,
also passing the natural game-end drain clean/audit pair. Main verified exact handoff/product
hashes before closing P6-E on2026-09-13. Final runtime hash:
57ada4dd04ff30b7bfd52aef83fbed2e22961090b19b653f0d6e9eb412293b1c.
