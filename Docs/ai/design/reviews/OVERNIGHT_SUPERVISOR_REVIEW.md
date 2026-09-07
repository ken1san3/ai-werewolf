# Overnight supervisor — Implementation Design Gate

Superseded on 2026-09-08: the workspace-writing prototype reviewed below was replaced by the independently approved D059 deterministic controller in `Docs/ai/design/OVERNIGHT_SUPERVISOR_DESIGN.md`. Its implementation is approved in `Docs/ai/design/reviews/OVERNIGHT_SUPERVISOR_IMPLEMENTATION_REVIEW.md`. The original BLOCKING verdict remains below as historical evidence for the rejected prototype.

Signed: Reviewer / Sol, D059 supersession record, 2026-09-08.

DESIGN: REQUIRED

Verdict: BLOCKING

Reason: `scripts/run_overnight.py` is not only a launcher. It grants one model an eight-hour `workspace-write` and network-enabled lifecycle that repeatedly creates contracts and protected tests, invokes Qwen and upper reviews, applies changes, and decides when a new design is required. This adds authority, concurrency, retry/budget, and failure-boundary decisions covered by D051.

Blocking design points:

1. The stated 12 upper-review and 60 Qwen-call ceilings exist only in the prompt; code mechanically enforces only wall time and output bytes. Define and enforce durable counters across every child campaign/call.
2. The supervisor lock is private to `.infra-runs/overnight`; it does not acquire the canonical repo-scoped D058 lock, so another campaign can mutate the same repository concurrently.
3. `--add-dir C:/AIagent/agent` grants workspace write authority to the whole AIagent installation while its source/config prohibition is prompt-only. Limit the writable surface mechanically to the required run/log/lock/usage locations, or use an execution boundary that cannot edit AIagent code/config.
4. The prompt may start Qwen, changing the existing pre-started-server boundary and adding process ownership/cleanup/failure semantics. Either retain the existing prerequisite or specify and test one bounded owner lifecycle.

Required design scope: exact writable/readable roots, shared lock order, durable call accounting, provider/send scope, subprocess ownership and cleanup, crash/restart semantics, result evidence, and tests proving each ceiling and refusal path. No implementation approval is given. Review was limited to `scripts/run_overnight.py`, `Run-Overnight.cmd`, and `Docs/ai/infra/OVERNIGHT_PROMPT.md`; tests and real execution were not run by this Reviewer.

Signed: Reviewer / Sol, D051 Design Gate, 2026-09-08.
