# Qwen runner v1 — independent implementation review

Verdict: APPROVED

Design reviewed against: `Docs/ai/design/INFRA_QWEN_RUNNER_DESIGN.md`

Approved design SHA256: `DD4FABD48A4C398F44D220A0F072DCC6894518D315281B7C1464ADF69866F505`

## Reviewed implementation bytes

```text
agent/lib/task_contract.py  C15CC3A7E2CDC65DE0B364B1F5F84B38E60009624EDB5B44CFF7C43ED1D7B8FE
agent/lib/task_state.py     A9E131305A072155A5162D807581BCA4CFC3E0404696B6B312A59FFBD4229496
agent/lib/task_source.py    AA6B4834F9A34A5DB399009CA52181C1E8B497A0B735199F5721A20E7FA7FB63
agent/lib/task_gate.py      F1F22BDB9BFE2A961C779549451EA5EF6FE2AAE8927655ACB195CE0207D8A961
agent/lib/task_runner.py    28A729DD870A0537768AA7233FB668F27563E0BD3771E8F860D01BF7ACB8F5D4
agent/lib/task_apply.py     ACB14C9EBE00F51BF5A25DF05A4A56DF5A43FC7DC76B5C8A82D42D4043370875
agent/tools/task.py         4DC3C607BB9761464DD4239B7ABFBB15073B7E0656225608BC9ADCCFBF9462FF
agent/tests/test_task_runner.py  1C7B834368697A1F1A0F116D9B54113CE93FC2E16A464D31231772C051231BB1
```

## Findings

No open High or Medium finding remains.

The initial review found the following defects. Each was corrected and re-read before this approval:

1. **High — fixed:** apply/rollback could save a terminal phase without a final all-target verification. It now rehashes all targets, re-verifies the full apply source state, leaves an interrupted/conflicted transaction in its recoverable phase, and refreshes handoff evidence on mutation errors.
2. **Medium — fixed:** a valid JSON model response whose top level was not an object could raise an uncaught `AttributeError`. The response envelope is now checked before dictionary access and malformed/subprocess failures end in an evidenced `ESCALATED` state.
3. **Medium — fixed:** a crash after saving the model response but before saving usage could reuse the response without restoring its usage record. Model steps now persist a pending record before the call and idempotently backfill it from saved response evidence on resume.
4. **Medium — fixed:** handoff omitted manifests and earlier-attempt evidence and always pointed to `status`. It now carries source/candidate manifests, baseline nodeids, gate history, all result/model evidence indexes, and a phase-specific next command.
5. **High acceptance gap — fixed:** deterministic negative tests were added for malformed and oversized output, collection failure/loss, protected drift, both exit/JUnit disagreement directions, timeout, editable-target drift, post-write rehash, partial apply/rollback recovery, repo-lock serialization, approval binding, link/reparse rejection, usage recovery, and Yellow finding/fix/re-review behavior.

## Verification

- Local Windows / Python 3.13.3: Reviewer-targeted final groups passed: `9 passed in 18.60s` and `7 passed in 18.01s` (malformed output, limits, collection/integrity, timeout/report disagreement, target drift, link/reparse, Red approval, and Yellow review paths).
- Local Windows: all seven reviewed Python modules compiled from source (`compiled 7`).
- The Implementer-provided raw final suite log `C:/AIagent/agent/infra-all-tests.log` was inspected; its raw last line is `88 passed, 1 skipped in 89.27s (0:01:29)`. This line is cited as Implementer evidence, not re-attributed as a Reviewer measurement.
- The earlier sandboxed pytest attempt was excluded because the sandbox denied pytest's temp directory; the Reviewer-targeted results above used normal Local Windows permissions.

The code preserves the design's stated boundary: trusted local contracts and developer code run with user privileges; snapshots are integrity isolation, not an OS security sandbox. No code was edited by this Reviewer. Any change to the implementation hashes above requires re-review of the changed files.

Re-signed after LF-only design normalization (reviewed implementation bytes unchanged): Reviewer / Sol, independent D053 lane, 2026-09-07.
