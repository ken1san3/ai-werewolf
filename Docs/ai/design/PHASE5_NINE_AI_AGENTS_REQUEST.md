# Phase 5 Nine AI Agents — Detailed Design Request

Status: REQUESTED
Request status: OPEN
DESIGN: REQUIRED

## Gate evidence

Phase 4 is independently complete. One real LLM client produced valid structured chat, CO, vote, and
ability decisions in a nine-client game; server-authoritative acceptance, private audit, bounded
failure behavior, full cleanup, and the complete 540-test regression passed. Phase 5 adds shared
cross-client generation admission, concurrency/fairness/backpressure, frequency control, short-chat
policy, nine real LLM clients, and Q8 measurement. D051 therefore requires independently approved
detailed design before implementation.

## Canonical scope

ROADMAP Phase 5 requires:

- one shared LLM server
- multiple AI agents
- a generation queue
- speaking-frequency adjustment
- short chat
- completion: nine AI clients automatically finish a game
- measure `OPEN_QUESTIONS.md` Q8 on the target 8GB VRAM host

`AI_WEREWOLF_CODEX_HANDOFF.md` §§35–37 additionally establishes 5–30 tokens as the short-chat
reference, talkativeness plus event importance and cooldown, and prohibits invoking all nine agents
for long inference on every message.

## Required design questions

1. Define the exact ownership/topology of the shared generation queue across nine independently
   running AI clients and one external loopback LLM server. Do not create a development-task
   orchestrator, model router, or server/game-core dependency on AI.
2. Define model-neutral public request/result/admission interfaces, lifecycle, process boundaries,
   startup/readiness/shutdown, bounded resources, and privacy isolation between players.
3. Define queue ordering, fairness, reservation-versus-reaction priority, deadlines, cancellation,
   stale work removal, overload/backpressure, failure propagation, and zero-orphan guarantees.
4. Preserve the Phase 3 shared Brain arbiter and server-authoritative handles/correlation. State how
   per-client `LLMBrain` composes with shared admission without two Brain instances or late sends.
5. Define speaking-frequency inputs and state without role-name/model-name branches: talkativeness,
   event importance, cooldown, repetition/chain suppression, per-phase bounds, and deterministic
   tests. Do not implement Phase 6 belief, strategy, or conversation quality.
6. Define short-chat enforcement at prompt/schema/validation boundaries, including the relationship
   between output-token budget, JSON overhead, text character/token guidance, and existing strict
   structured output. Do not silently truncate model text into a different action.
7. Define offline deterministic unit/integration/completion tests that need no GPU/network/model, plus
   one finite opt-in nine-real-client smoke on the existing game profile with exact cleanup/evidence.
8. Define the Q8 benchmark matrix and measured outputs: queue latency, prompt/generation throughput,
   deadline miss/suppression, accepted speech per day/seat, peak concurrency, game duration, and GPU
   memory evidence if safely observable. Separate measurement from any product rule decision.
9. State whether Q8 can be resolved by measured safe defaults already bounded by canonical sources.
   If a material discussion-duration or server speech-limit product choice remains, record the exact
   alternatives in `OPEN_QUESTIONS.md` rather than inventing one.
10. Partition implementation into non-overlapping tasks with public APIs, expected files, focused
    tests, regressions, completion evidence, and explicit rejected alternatives.

## Required sources

- `Docs/ai/spec/DESIGN.md` §§4.6, 5, 6, 9, and design invariants
- `Docs/ai/ROADMAP.md` Phase 5
- `Docs/ai/spec/AI_WEREWOLF_CODEX_HANDOFF.md` Phase 5 and §§35–37
- `Docs/ai/OPEN_QUESTIONS.md` Q8
- approved `Docs/ai/design/PHASE3_3_BRAIN_INTERFACE_DESIGN.md`
- approved Phase 3.4, Phase 3.5, and Phase 4 detailed designs and final handoffs
- `Docs/ai/decisions/D034_LOCAL_LLM_DEV_ASSIST.md`
- `Docs/ai/spec/LOCAL_LLM_SETUP.md` as current-machine evidence only

## Out of scope

- Phase 6 belief/suspicion/strategy/conversation-quality logic
- server/game/content/protocol rule expansion, UI, roles/MOD, remote providers, provider failover,
  model routing, development-role model coupling, Autodev, automatic task supervision, or unbounded
  retries/queues/prompts

## Acceptance for the design

- Every ROADMAP Phase 5 item maps to explicit interfaces, lifecycle, tests, and evidence.
- Nine clients cannot overload or leak across the shared queue; stale/cancelled work cannot dispatch.
- Existing Phase 3/4 authority, privacy, structured-output, audit, deadline, and LLM-free contracts
  remain intact.
- Q8 measurement is finite, reproducible, and cannot leave model/game/client processes.
- No product decision is invented and implementation is not started before independent approval.
