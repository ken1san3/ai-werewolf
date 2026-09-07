# INFRA_AUTONOMOUS_DESIGN independent review

Verdict: APPROVED

Reviewed design: `Docs/ai/design/INFRA_AUTONOMOUS_DESIGN.md`

Design SHA256: `8AEC8FBD4B8D7412A15017B00BBA0A0FA8281D0FE43CE8B7E24D21DC876DBB5A`

Review basis: D051, D053, D056, D057, `AGENTS.md`, `Docs/ai/design/INFRA_AUTONOMOUS_REQUEST.md`, the approved AIagent v1 task contract/runner/apply boundary, and read-only Local Windows checks of the installed Codex and Claude CLI capabilities.

The design is implementable without leaving a material authority, retry, or recovery decision to the Implementer. The final clarification closes the blocking findings from the initial review:

- one authoritative phase table now covers both job kinds, all verdict routes, terminal/resumable states, and exit behavior;
- a canonical repo-scoped OS lock serializes campaigns, and every v1 run/resume launch reserves its full possible local-call cost without refund;
- provider calls use fixed argv, an isolated packet-only working directory, minimal environment, schema-constrained output, pre-call intent records, bounded streamed output, and process-tree timeout termination;
- unknown delivery never retries, quota/authorization ceilings remain finite, and provider failure never causes implicit fallback;
- Qwen can draft only `goal` and `context`; all path, test, invariant, acceptance, risk, and Design Gate authority remains in the trusted template;
- review-only evidence cannot edit canonical design status, while Red approval and post-apply completion review use exact schemas bound to the actual model, contract, candidate, APPLIED handoff, file hashes, and v1 gate evidence.

The deliberate limitation is acceptable: v2 automates a finite list of already scoped jobs and produces review evidence, but does not invent new phases or automatically modify canonical approval/Next Task documents. It therefore cannot bypass the existing Phase 3.4 Design Gate. Any edit to the approved design invalidates this hash binding and requires fresh independent review.

Review environment: Local Windows. No provider request was sent and no implementation was reviewed.

Signed: Reviewer / Sol, independent D053 lane, 2026-09-07.
