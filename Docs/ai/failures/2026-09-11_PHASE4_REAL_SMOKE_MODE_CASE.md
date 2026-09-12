# Phase 4 real-smoke client mode casing mismatch

Date: 2026-09-11
Status: CLOSED — T028 independently tested/APPROVED and T025 final confirmation passed
Classification: deterministic completion-runner/fixture launch-contract defect; production LLM/game code not implicated

## Symptom

T025 real-local attempt 2 passed model readiness and server launch, then all nine client processes
exited with code 2. Each bounded client stderr reported:

```text
phase4_client_process.py: error: argument --mode: invalid choice: 'speak' (choose from SPEAK, SILENT)
```

The game server exited normally after reaching game end with no accepted client actions. The runner
reported missing nine-client status/PID and one-LLM evidence. The tracked game-model process tree was
stopped and verified absent.

## Initial evidence

`scripts/run_phase4_local_smoke.py` passes lowercase `speak`, while
`tests/fixtures/phase4_client_process.py` derives its accepted values from
`CompletionReactionMode`, whose current values are uppercase `SPEAK` and `SILENT`. The offline
completion path does not invoke this real-runner argument construction.

## Required routing

T027 must independently confirm the exact contract, explain why T024 coverage missed it, and define
the smallest regression-backed repair. Do not change production/game/model/config/timeouts or weaken
T025 acceptance. Any repair must pass Implementer -> independent Tester -> fresh Reviewer before a
third finite T025 attempt.

## T027 disposition

T027 confirmed that the runner is the sole lowercase producer, while the Phase 3.4/3.5 offline
launchers already serialize `CompletionReactionMode.SPEAK.value`. T024's offline path therefore used
the correct contract, and its injected runner factory did not inspect argv. T028 owns only the enum
value substitution and a no-child argv/enum-round-trip regression. No user decision is required.

## T028 closure note

T028 replaced the runner's lowercase mode literal with the existing
`CompletionReactionMode.SPEAK.value` authority. The no-child partial-spawn regression now captures
the first client argv, asserts the exact authority value, and round-trips it through
`CompletionReactionMode(value)`. It does not start a real client or model.

On Local Windows with Python 3.13.3, both the Implementer and independent Tester passed the focused
suite (`9 passed, 2 subtests`) and Phase 3.5 plus LLMBrain regression (`24 passed, 23 subtests`).
Compile, docs, diff, and zero-relevant-process checks passed. A fresh Reviewer approved the bounded
change with no findings. The first restricted-sandbox invocations encountered the already-recorded
temporary-directory WinError 5; the exact commands passed under normal local permissions. T025 owns
the later real-smoke rerun.
