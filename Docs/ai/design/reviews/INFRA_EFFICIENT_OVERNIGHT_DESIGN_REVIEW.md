# D060 efficient overnight design review

Verdict: APPROVED

Actual reviewer: Reviewer / Sol (`gpt-5.6-sol`)

Review date: 2026-09-08

Reviewed design: `Docs/ai/design/INFRA_EFFICIENT_OVERNIGHT_DESIGN.md`

Initial reviewed pre-approval SHA256: `735dfbd81d3bb9d86af447fb40e1a8f3c3220b0c40c825af53047ec9a0a9b4ca` (superseded by the post-apply scope correction)

Revised reviewed pre-approval SHA256: `f3a93567bdfd3ee6f948449ec0b4904a9e736c66666a6566378e805b24d95328`

Revised approved design SHA256 after the status-only change: `a3ef24da6c11d4b8bf0c209e219d8610e5466de2e1fde306f2766140cc0c2908`

Final evidence-binding pre-approval SHA256: `736db89fa9b2de66be99dbdece9135d2efd49cdd9b7ddff621c16098f1c054d9`

Final approved design SHA256 after the status-only change: `16cb60788a158d4ee6d47687e9f0fbf097ffe26f12ed22600a79077038230c11`

Author: Infrastructure / Astra (`gpt-6-astra`)

The design is implementation-ready for its infrastructure-only scope. Version 2 is explicitly opt-in and uses a separate verifier, while version-1 D059 runs, budgets, evidence and interpretation remain unchanged.

The two-call workflow preserves the trust boundary. Qwen may generate only the bounded plan clarification and protected test proposal. A real configured upper reviewer binds the resulting contract, exact test bytes, source and packet evidence. The direct v1 runner then produces a mechanically gated red candidate. A second real upper review binds the exact candidate and gate evidence before v1 transactional apply. Completion after apply is mechanical and requires unchanged approved bytes plus the same collection, baseline-node, required-node, execution, JUnit, probe and configured-check predicates.

The exact extension fixes the version discriminator, allocation formulas, parent and child records, call identities, direct task.py commands, final packet and signed evidence, recovery rules, post-apply gate inputs, receipt compaction, launch bounds and pointer replacement behavior. Unknown delivery, ambiguous or damaged runs, source or approval changes, exhausted launches, failed final review and failed post-apply gates all stop without an extra cloud call or fresh candidate.

The revised post-apply contract avoids applying the whole-tree `task_source.manifest` helper to a live Git repository. It fixes an ordered live scope from `task_source.selection(contract)` plus `allow_new`, rechecks that scope around every gate hash, and injects the scoped manifest only inside a hash-bound trusted subprocess wrapper. Tests still run with the actual repository as cwd and retain the v1 collection, baseline, required-node, JUnit, probe and configured-check predicates. No installed AIagent module is modified.

The final evidence-binding revision gives version-2 receipts a `post_sha256`, keeps version-1 receipt keys unchanged, and requires every collection, execution, probe, JUnit and configured-check artifact plus the wrapper producer records to remain hash-verifiable. An interrupted host may finalize an existing wrapper result, but cannot resend a model request, reapply the candidate, or replace the immutable post request.

Approval includes the bounded Windows pointer recovery and same-package deduplication contract. Temporary pointer files remain hints rather than authority, searches are bounded to the declared runs root, and no provider call occurs before the selected run is durably recorded.

Gemini is not a runtime dependency. This approval does not authorize game implementation, quota reset, modification of old runs, automatic commits, or a claim of game Phase completion.

No implementation, provider call, or game execution was performed in this review. Documentation validation is run after recording approval.

Signed: Reviewer / Sol (`gpt-5.6-sol`), independent design-review lane, 2026-09-08.
