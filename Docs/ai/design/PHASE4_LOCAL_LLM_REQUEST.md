# Phase 4 Local LLM — Detailed Design Request

Status: REQUESTED
Request status: CLOSED — Phase 4 independently completed and closure-approved on 2026-09-11
DESIGN: REQUIRED

Completion evidence: T025 final real-local smoke PASS; T032 full regression PASS (`540 passed`,
`736 subtests`, four existing deprecation warnings) and fresh Phase 4 closure Reviewer APPROVED.

## Gate evidence

Phase 3 is independently complete: one shared Brain boundary, Reaction Chat, deterministic
Vote/Ability control, authoritative result correlation, and LLM-free nine-process completion all
pass. Phase 4 introduces an external model process, HTTP lifecycle, prompts, structured output,
failure/cancellation behavior, privacy logging, and one real LLM-driven client. D051 therefore
requires independently approved detailed design before implementation.

## Canonical scope

ROADMAP Phase 4 requires:

- 4.1 LLM backend interface
- 4.2 Structured Output
- 4.3 natural-language chat generation
- 4.4 vote/ability selection
- completion: one AI Client can run through the existing Brain boundary using an LLM

Phase 5 shared nine-agent queue/GPU scheduling and Phase 6 belief/strategy quality are not Phase 4.

## Required design questions

1. Define module/file ownership and a model/vendor-neutral async backend interface. Model name,
   endpoint, sampling values, and credentials are configuration, never role/responsibility names.
2. Define the initial OpenAI-compatible local HTTP adapter, request/response types, connection and
   read timeouts, cancellation, bounded response size, error taxonomy, and resource ownership.
   The repository must remain testable with the local server absent.
3. Define a bounded prompt projection from immutable `BrainInput`: only information the client is
   authorized to know, no raw Network payload, no game-core/server import, no invented handles,
   no whole-history dump, and no model-specific role-name branches.
4. Define one structured-output schema/discriminator covering the existing `BrainDecision` types.
   The LLM may select only supplied option IDs/targets/values; `BrainController` remains the final
   mechanical validator/sender. Specify parse/schema/semantic failure handling and any bounded
   repair attempt without a free-form fallback action.
5. Define Phase 4 chat/CO text generation and reservation choice boundaries. Natural-language
   generation belongs here; suspicion/belief/long-term strategy quality remains Phase 6.
6. Define how `LLMBrain` composes with the existing deterministic decorator and shared arbiter so
   exactly one Brain instance is used and current deadline/cancellation semantics remain intact.
7. Define observability and privacy-safe `ai.jsonl` evidence: request metadata, redacted/bounded
   prompt/response information, latency, model/backend config identity, parse/validation outcome,
   and no connection/entry tokens or information outside the player view.
8. Define configuration validation and offline/failure behavior. A model outage must be visible and
   bounded; it must not block World/Network ingestion or silently substitute an unauthorized move.
9. Define unit/integration/completion tests using a deterministic fake HTTP backend, plus one
   bounded opt-in local game-profile smoke that proves a real LLM controls one client. Required CI
   tests must not need a model, network access, GPU, or `C:\AIagent`.
10. Partition implementation into non-overlapping tasks after approval and state all public APIs,
    lifecycle/state transitions, acceptance criteria, diagnostics, and rejected alternatives.

## Required sources

- `Docs/ai/spec/DESIGN.md` §§2, 6, 9, 10 and design invariants
- `Docs/ai/ROADMAP.md` Phase 4
- `Docs/ai/spec/AI_WEREWOLF_CODEX_HANDOFF.md` §§12–20, 24, 26–27, 33–36 as reference material
- `Docs/ai/design/PHASE3_3_BRAIN_INTERFACE_DESIGN.md`
- approved Phase 3.4 and Phase 3.5 detailed designs and final handoffs
- `Docs/ai/decisions/D013_LOGGING_AND_REPLAY.md`
- `Docs/ai/decisions/D034_LOCAL_LLM_DEV_ASSIST.md` and
  `Docs/ai/spec/LOCAL_LLM_SETUP.md` only as historical/current-machine environment evidence, not
  as authority to hardcode a model or revive the removed development runner

## Out of scope

- Phase 5 multi-agent shared generation queue and throughput policy
- Phase 6 belief/suspicion/strategy/memory-quality system
- UI, new game/content rules, server authority changes, model-routing framework, Autodev,
  development-role model coupling, remote provider integration, or unbounded prompt/repair loops

## Acceptance for the design

- Every ROADMAP Phase 4 item maps to explicit public interfaces, lifecycle, tests, and completion
  evidence.
- Existing Phase 3 server-authority, received-handle-only, deadline, correlation, arbiter, privacy,
  and LLM-free regression contracts remain intact.
- One-client real local smoke is finite, opt-in, diagnosable, and cleanly stoppable.
- No product decision is invented. If canonical sources truly leave a material user choice, record
  it in `OPEN_QUESTIONS.md`; otherwise choose and justify the smallest reversible design.
