# Phase 4 real-smoke server launch JSON rejects mappingproxy

Date: 2026-09-11
Status: CLOSED — T026 independently tested/APPROVED and T025 final confirmation passed
Classification: deterministic completion-runner serialization defect; production LLM/game code not implicated

## Symptom

The first approved real-local Phase 4 smoke exited in 6.5 seconds before any client readiness:

```text
Phase 4 smoke configuration error: Object of type mappingproxy is not JSON serializable
Phase 4 smoke FAILED; exits=[2]
```

The resulting bounded validation correctly reported missing game end, nine-client PID evidence, and
one-LLM evidence. The runner cleaned its owned game process. The separately tracked game-profile model
server was then stopped with its exact PID tree; no model process was left running.

## Cause

`scripts/run_phase4_local_smoke.py::_server_child` passes
`GameRegistry.entry_tokens_for(game_id)` directly to `_write_private_json`. The registry intentionally
returns a read-only `mappingproxy`; standard `json.dumps` does not serialize that container. The
offline completion uses a different proven harness and therefore did not execute this runner-only
launch-state serialization path.

## Repair boundary

- Materialize only the launch-state token mapping as a plain private JSON object before serialization.
- Preserve the token values, private-file permissions, runner diagnostics, process ownership, timeout,
  and every T024 acceptance assertion.
- Add a direct regression using a read-only mapping and verify round-trip output/permissions/cleanup.
- Do not change production Phase 4, game/server/protocol/content, model configuration, or time bounds.

The real smoke must be rerun after independent test and review of T026. No further runner issue is
inferred from this pre-client failure.

## T026 closure note

T026 materialized `entry_tokens` with `dict(...)` only while constructing the private server-launch
JSON object. Direct regression coverage round-tripped both an ordinary dictionary and a read-only
`MappingProxyType` with exact keys and values and verified the existing owner-read/write `chmod`
call. On Local Windows, the focused suite passed `9 passed, 2 subtests`, the Phase 3.5 plus LLMBrain
regression passed `24 passed, 23 subtests`, and process inspection found zero relevant orphans.

An independent Tester reproduced the exact round trip and passed the focused and related regression
suites; a fresh Reviewer approved the bounded change with no findings. This closes the isolated
serialization cause only. T025 owns the real-smoke rerun. T026 did not start a model or real smoke.
