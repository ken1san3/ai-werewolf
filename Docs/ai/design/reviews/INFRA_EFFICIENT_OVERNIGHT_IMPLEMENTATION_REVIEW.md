# D060 efficient overnight implementation review

Verdict: APPROVED

Actual reviewer: Reviewer / Sol (`gpt-5.6-sol`)

Review date: 2026-09-08

Approved design: `Docs/ai/design/INFRA_EFFICIENT_OVERNIGHT_DESIGN.md`

Approved design SHA256: `16cb60788a158d4ee6d47687e9f0fbf097ffe26f12ed22600a79077038230c11`

## Reviewed implementation bytes

```text
scripts/overnight_lib/efficient.py 5dd09269795831e5047c21eadea6efe536fd262a2a0531206b66af83be1e8a56
scripts/overnight_lib/pointer.py e40054bd3359c4c107b8d780fe75b7390d8b846561bd066e6c187f0d6c4ddbfd
scripts/overnight_lib/local_plan.py f828c2de661bda7d3a5b56c6872241878ab0897abe5361b79229b3c0a8b3778b
scripts/overnight_lib/engine.py f77b6e712b8e6ce76b720fc1d7f6c0de83eb27b0ca0e55b159adf450d835a430
scripts/overnight_lib/evidence.py 9ab82e88cb3a0c17afa8f4ac051d9dfb0b806725bd37c1ad795b7fc26d8dfeaa
scripts/overnight_lib/policy.py 5b05db808d3cd6567b2c525eae4070fab1e4b72cfc4f3634e3f760db659b3dd3
scripts/overnight_lib/provider.py 11e9873486a904315b66a03436edda800a6c111abe83476d4299a63eb5b3f143
scripts/overnight_lib/report.py 37558b142596f478067aee007148b702622f7b8dbe9e2fdeb9391c740b283e38
scripts/run_overnight.py 86caa0670985cde3284f00c0cb3ab47918176b10849458f160cc60caf3d9e682
tests/test_overnight_efficient.py 4d19705d3c9af1fa37954a54b0e52c6debe6feba7019bd4a98bfb3dc931093f6
tests/test_overnight_pointer.py 81f8d038128ce527d03c4e24dc1afd0bc86acb2bb40c5dcc2151514c39fc2a77
```

The implementation preserves version-1 behavior and makes version 2 opt-in. Qwen planning consumes the local allocation and accepts only one normally finished schema response. The first upper review binds the deterministic proposal and protected test. The direct red v1 run cannot manufacture another contract review. Its final packet binds the signed plan approval, contract, complete candidate changes, manifests and raw gates before the second upper call. Only that exact approval is mapped to v1 apply authority.

Launch recovery holds the shared repository lock, validates D059/D060 release bytes, reuses one exact matching run, refuses ambiguous or damaged roots, and writes the local pointer only after run identity is known. A failed atomic replacement keeps the run and recovery tmp without dispatching another model call.

Apply recovery accepts only v1 journal before/after hashes already bound to the approved candidate. Missing final raw remains UNKNOWN_DELIVERY and is never resent. The post-apply wrapper contains its own scope enforcement and binds its immutable request, wrapper, producer and result. Version-2 receipts bind `post.json` raw bytes, while post verification requires the collection, execution, probe, JUnit, configured-check and producer artifacts. Constructor and report validation read this evidence without rerunning tests, applying code or dispatching a provider. Valid ESCALATED, ROLLING_BACK and ROLLED_BACK child evidence remains reportable and cannot proceed to final review or apply.

Allocation reconstruction permits exactly two upper dispatch intents per completed version-2 unit: plan/test approval and final candidate approval. Qwen planning, implementation, fresh review and fixes remain separately bounded. Rejection, quota, timeout, tampering and incomplete delivery do not refund or create a hidden review attempt. Gemini is not a dependency.

## Verification evidence

Execution environment: Local Windows. Commands and results were executed by the root Implementer/review coordinator; this Reviewer inspected the resulting logs and exact final bytes and does not claim those executions as its own.

- D059 legacy and pointer group: 59 passed in 136.28 seconds. The concurrent first attempt also showed 23 v2 failures caused by D060 approval metadata being updated after pytest startup; each failed closed at `check_version` and made no implementation claim.
- Version-2 main group after stable approval metadata: 20 passed in 148.19 seconds. Three newly added failure-report tests then failed only because their test fixture opened a persisted relative child path against the process cwd.
- Corrected failure-report regression group: 3 passed, 20 deselected in 15.13 seconds.
- Final changed-scope total: 82 passed across the stable groups above.
- Earlier D059/D058/launcher regression set: 138 passed on Local Windows before the final test-only path correction; the final 59-test legacy/pointer group revalidated the affected legacy surface.
- `python scripts/check_docs.py`: passed during this review.
- `git diff --check` on the reviewed implementation and tests: passed during this review.

This approval covers D060 infrastructure behavior only. It does not approve a game implementation, modify or resume an expired old run, reset quota, invoke an external model, commit, push, or claim completion of Phase 3.4.

Signed: Reviewer / Sol (`gpt-5.6-sol`), independent implementation-review lane, 2026-09-08.
