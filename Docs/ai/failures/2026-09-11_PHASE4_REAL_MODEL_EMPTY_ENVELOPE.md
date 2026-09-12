# Phase 4 real model returns no accepted content envelope

Date: 2026-09-11
Status: CLOSED — T030 independently tested/APPROVED and T025 final confirmation passed
Classification: host local-model configuration compatibility issue; AIwolf backend is correct

## Symptom

T025 attempt 3 started all nine clients successfully. All ten smoke-owned processes exited `0`, all
nine clients recorded game end with distinct PIDs, and client/server stderr was empty. The one real
LLM client made 24 backend calls, but all 24 ended `BACKEND_FAILED` with
`RESPONSE_ENVELOPE_INVALID`; it produced zero valid decisions. No LLM chat or vote was accepted, and
offered LLM abilities ended `BRAIN_FAILED`.

The model log shows completed generation work, including repeated responses that consumed the full
128-token generation limit, but the privacy-safe audit contains no response text for backend-envelope
failures. The current backend accepts only a non-empty string at `choices[0].message.content`.

## Preserved evidence

- `.phase4-real-smoke-retry3/run/`: nine status files, server result, empty child stderr, and bounded
  `ai.jsonl` metadata.
- `.phase4-real-smoke-retry3/model.stderr.log`: model startup and timing evidence.
- T025 attempt 3 used launcher PID `31296`, Python child `30784`, and llama child `33168`; the exact
  tracked tree was stopped and verified absent.

No credentials or complete prompt/response are included in this record.

## T029 confirmation

A single request reused a retained canonical message/schema contract and the exact production model,
temperature, 128-token bound, and `json_schema` mode, changing only
`chat_template_kwargs.enable_thinking=false`. It returned HTTP 200 with one non-empty 121-character
content string, no reasoning field, a parsed JSON object valid against the dynamic schema, finish
reason `stop`, and 53 completion tokens. Only structural metadata and lengths were printed. The newly
tracked model tree was stopped and verified absent.

This confirms the host game-profile setting as the bounded repair location. Strict backend parsing,
token bounds, and T025 acceptance must remain unchanged.

## T030 implementation

The host `profile.game.args` now retains its previous `-ngl 99 -c 8192 --jinja` flags and appends
only the same `--chat-template-kwargs '{"enable_thinking":false}'` setting already used by the dev
profile. The reconstruction mirror records that exact setting and the T029 one-request evidence.
TOML loading, host doctor, profile listing, AIwolf documentation checks, and diff checks pass without
starting a model. Independent Tester/Reviewer validation and the Integrator-owned newly tracked
profile process/probe remain required before this failure can be closed or T025 can resume.

## Required routing

T029 must distinguish a production backend-envelope defect from host game-profile thinking/config
behavior or an unsupported response-format interaction. It must use bounded metadata and may
recommend one explicitly scoped diagnostic probe if the retained evidence cannot decide. Do not
weaken strict structured output, accepted chat/vote, game-end, audit, or cleanup acceptance.
