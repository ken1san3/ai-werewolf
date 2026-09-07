# Windows autodev launcher — Implementation Design Gate

DESIGN: NOT REQUIRED

Reason: This is a small launcher over the approved D058 CLI. It adds no subsystem, public state, queue, retry, provider policy, approval rule, or mutation path. The implementation is determined by the existing `doctor`, `start`, `resume`, and `report` commands and their exit codes.

Implementation scope:

- accept an explicit manifest and run `doctor` successfully before `start`, then run `report` for the created campaign;
- accept an explicit existing campaign and run `resume`, then `report`;
- with no argument, read one repo-local launcher setting that selects exactly one of manifest or campaign;
- preserve the primary `doctor`/`start`/`resume` exit result while making the final report available;
- invoke the existing Python CLI without constructing a shell command from path text.

Acceptance criteria:

- a failed `doctor` never reaches `start`;
- one launcher invocation performs at most one `start` or one `resume` and never retries it;
- it does not delete a stop file, resume a terminal campaign, rewrite a manifest/campaign, extend a deadline or call budget, start Qwen, reset usage, or infer authorization;
- missing, conflicting, malformed, or out-of-repo local settings fail before `start`/`resume`;
- quoted Windows paths work, child output remains visible or durably captured, and the launcher returns a nonzero code for validation, lock, paused, or failed execution;
- tests use fake/disposable inputs and prove the call order, fail-closed branches, single-attempt behavior, and exit propagation.

Files to read: `scripts/autodev.py`, `Docs/ai/spec/AUTONOMOUS_DEVELOPMENT.md`, and the launcher-local setting/example only.

Binding boundary: `Docs/ai/design/INFRA_AUTONOMOUS_DESIGN.md` and D058 remain authoritative. This gate does not approve a game job, Phase 3.4, an external send, or any automatic approval.

Signed: Reviewer / Sol, D051 Design Gate, 2026-09-07.

## Implementation review

IMPLEMENTATION REVIEW: APPROVED

Reviewed files and SHA256:

```text
144e636f0f7e6955a9dd31ed69735e02a45073880eea2439a8aa19af3a655ee1  scripts/run_autodev.py
fc2a40d3c6eb574a057907195e3dd2f02b78de3b08fe6157c78886fd189bfbcc  Run-Autodev.cmd
ba8b432e15dc5060ab9c6075853a1e668f5709e850fcf745c7d8fdd7a74a8ea4  autodev.example.json
6049c3f11ead41b35adfb5c48b5ff28f311e10eb55475bcc369638ce4a8b442c  tests/test_autodev_launcher.py
f3c532257673fdddad9054559cad8e2c53e0bf983d77cc8d52a6f4af7af1a915  Docs/ai/spec/AUTONOMOUS_DEVELOPMENT.md
```

The launcher implements the approved local sequence without adding authority: manifest mode performs one successful doctor before at most one start and then reports; campaign mode checks status, resumes at most once only for resumable phases, and reports terminal phases without resuming them. It preserves the primary exit result, does not alter the local setting or campaign, and passes paths as subprocess arguments rather than shell text.

The initial Medium finding is closed. Child stdout is now line-streamed with flush while retained for campaign-receipt parsing, and stderr is inherited rather than left in an unread pipe. The regression test proves that the parent receives the start receipt before the child is allowed to exit.

Verification: independent Local Windows run `14 passed in 0.15s`, exit 0. Implementer evidence records `14 passed in 0.81s`, exit 0, and a real `Run-Autodev.cmd --check` exit 0. No LLM or provider call was made by this review.

This approval remains limited to the launcher over D058. It does not approve Phase 3.4, a game campaign manifest, an external send, or automatic expansion of scope, time, calls, or approval.

Signed: Reviewer / Sol, independent implementation-review lane, 2026-09-08.
