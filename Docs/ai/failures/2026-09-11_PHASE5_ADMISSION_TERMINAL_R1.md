# Phase 5 durable admission diagnostic did not materialize

Status: **UNRESOLVED**

Date: 2026-09-11

Environment: Windows 11 host, CPython 3.13.3, repository `C:\AIwolf`, inherited dirty
`main` at `994188a8ccc7cc14c4d6626caee4fa633cdc642c`; no commit.

## Purpose and precondition

T055 attempted to recover the exact same-client sequence behind T053's durable
`TERMINAL/OVERLOADED` record. Before execution, both of these paths were confirmed absent:

```text
C:\AIwolf\_to_delete\t055_phase5_admission_terminal.json
C:\AIwolf\_to_delete\t055_phase5_admission_terminal.json.tmp
```

The `_to_delete/` directory was already ignored by repository policy. T054's retained evidence root
`C:\AIwolf\tmp1jf_uxts` was not changed or deleted.

## Diagnostic-only change

`tests/test_phase5_completion.py` now assigns the existing sanitized assertion diagnostic to one
object and serializes it once. Only when the unchanged `admission_failures` predicate is true, the
test writes that exact payload to a sibling `.json.tmp`, flushes and `fsync`s it, and uses
`os.replace` to publish the required path immediately before the same `assertFalse` call.

The durable object contains only the already-approved terminal metric windows, correlated client
terminal summary, and sanitized broker/backend closure fields. It adds no prompt, response text,
entry/admission token, credential, environment dump, role, or model data. No fixture subprocess,
deadline, timeout, retry, topology, count, or pass/fail predicate changed.

Pre-run checks:

```text
python -m compileall -q tests/test_phase5_completion.py
PASS (exit 0)

git diff --check
PASS (exit 0; inherited CRLF warnings only)

git diff --no-index --check -- NUL tests/test_phase5_completion.py
Whitespace PASS; exit 1 is the expected untracked-file content difference
```

## Sole completion execution

Exactly one completion was started under normal local Windows permissions:

```text
python -m pytest tests/test_phase5_completion.py::PhaseFiveOfflineCompletionTests::test_one_broker_nine_production_llm_clients_complete -q
```

The pytest parent PID `31868` started at 23:49:48 JST. The surviving artifact timestamps show the
server and clients wrote terminal results by approximately 23:50:10 and the parent began removal of
its repository-local root by 23:50:11. All Phase 5 server/broker/client fixture processes were gone;
only the pytest parent remained, idle with unchanged CPU time.

The required durable JSON and its `.tmp` never appeared. At the 180-second node bound the parent was
still present. After the allowed additional 60 seconds, the process inventory still contained only
that exact pytest PID, so it alone was interrupted. The unified execution returned exit 1 without
captured pytest output. No second completion was started.

At 23:54:17 JST the process inventory contained zero Python processes matching the Phase 5
completion pytest, server, broker, or client commands. The required durable JSON and `.tmp` were
still absent.

## Surviving evidence

The new permission-restricted root `C:\AIwolf\tmpcv9tcgcz` remains intentionally retained. Its
cleanup stopped after deleting the admission metrics, broker result, audit shards, players 0--3
evidence, and part of player 4 evidence. It contains 28 files and zero directories. The remaining
server result reports:

```text
success=true
failure=null
game_end=true
listener_closed=true
accepted chats=58
distinct Day-1 chat players=9
expected reservations=35
accepted reservations=35
rejected reservations=1
```

Players 4--8 each report `runtime_success=true`, `runtime_exit=GAME_ENDED`,
`world_exit=CLIENT_ENDED`, and both controller exits `WORLD_ENDED`. Their Brain failure counts are
zero, no reservation remains unresolved, and stdout/stderr are empty. Their backend-call counts are
21, 15, 12, 21, and 12 respectively.

The absence of the durable diagnostic proves only that the failure-persistence branch did not
successfully publish a payload. It does not prove that the complete aggregate validation ran, that
the run had no admission terminal, or that the T053 condition was a scheduling flake. An earlier
assertion, a later assertion, or a fully successful body followed by the cleanup hang cannot be
distinguished because pytest never emitted its captured result and the temporary broker/metrics
evidence was deleted.

## Classification

Admission terminal cause: **UNRESOLVED**. T053 remains narrowed to `OVERLOADED`, but T055 supplies no
exact client, invocation, event sequence, or interleaving. No production-bug, fixture-semantic, or
scheduling-flake classification is supported.

Observed T055 failure layer: completion-harness cleanup. Two consecutive diagnostic runs have now
left a repository-local `TemporaryDirectory` partially deleted after every owned fixture child
exited, while the pytest parent remained idle beyond all declared bounds. Confidence is high that
this hang is outside the game, broker, and client child processes. The exact Windows ACL/filesystem
operation is not traced and is not claimed.

## Minimal repair/test boundary

Before another completion, create one test-only harness task owning
`tests/test_phase5_completion.py`. Replace the unbounded context-manager cleanup path with an
explicit bounded cleanup helper that restores owner-only delete permissions, records a sanitized
terminal/cleanup stage outside the root, and either proves complete deletion or returns a bounded
failure without trapping pytest. Add a focused local regression containing private-mode files that
proves the helper returns and removes its root. Preserve the existing secret scan, admission
predicate, topology, timings, and all acceptance criteria.

Only after that focused cleanup regression passes should one bounded completion be used to recover
the admission sequence. No `ai_client/` or broker-capacity repair is authorized from T055 evidence.
