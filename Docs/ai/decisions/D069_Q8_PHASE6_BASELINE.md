# D069 — Q8 Phase 6 discussion baseline

Date: 2026-09-12

Status: Accepted baseline — revisit after Phase 6 conversation-quality and latency measurement

## Decision

The user selected Q8 option 1 as the Phase 6 baseline:

- `day_seconds=180` for `standard_9`;
- preserve the existing content-defined shortening and extension behavior;
- add no general/client-neutral server speech cap; and
- use at most two AI chat Brain invocations per phase as the baseline; CO remains a separate
  existing system-action path under its current rules.

This is not a permanent product rule. Phase 6 must measure responsive-conversation quality,
accepted speech, queue wait, end-to-end latency, prompt size, and completion behavior on the exact
canonical 9B route. A later change requires explicit evidence and the applicable product/design
decision; the current baseline must not silently become an immutable server rule.

## Existing implementation

No runtime change is required to adopt this baseline. `content/presets/standard_9.yaml` already sets
the day to 180 seconds and retains its content-defined shortening/extension rules.
`ReactionChatConfig.max_chat_attempts_per_phase` defaults to two and rejects values above two. This
is a chat-attempt bound, not a newly invented aggregate cap over CO, vote, or ability decisions.
The server has no general client-type-specific or client-neutral speech cap beyond its existing
authoritative action/deadline/rule validation.

## Evidence and limits

T154 completed the exact canonical-9B Q8 engineering measurement. It proves the shared-provider,
admission, timing, audit, game-end, and cleanup path, but its test-only 60-second day and observed
traffic do not establish an optimal discussion tempo or permanent cap. The external review's
estimated speech/queue targets are not authority.

The Phase 6 detailed design must preserve privacy and the exact-9B-only boundary, define bounded
authorized context and measurable dialogue-quality acceptance, and require one finite real game
after implementation. 35B, alternate, fallback, automatic model selection, and an unbounded soak
remain outside this decision.

## Consequences

- Q8 is closed.
- Phase 6 detailed design may begin.
- Phase 6 implementation still requires an independently approved Design Gate.
- Any later tempo, AI chat-invocation cap, CO relationship, or general server speech-cap change is a
  separate product and design decision based on Phase 6 evidence.
