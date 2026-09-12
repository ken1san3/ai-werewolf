# Phase 5 admission terminal diagnostic evidence loss

Status: **UNRESOLVED**

Date: 2026-09-11

Environment: Windows 11 host, CPython 3.13.3, repository `C:\AIwolf`, inherited dirty
`main` at `994188a8ccc7cc14c4d6626caee4fa633cdc642c`; no commit.

## Original bounded signal

T053's sole Phase 5 completion reached `_assert_completion` after the broker had reported success,
clean shutdown, zero dropped metrics, no pending/offered/claimed/provider/draining state, and
`snapshot_after_close.poisoned == false`. It then failed because at least one durable admission
metric had `terminal_status` in `{OVERLOADED, POISONED}`. T053's temporary evidence root was removed,
so it did not preserve the offending invocation or sequence.

The assertion order and broker state machine narrow the T053 terminal to `OVERLOADED`: a durable
`TERMINAL/POISONED` record is created only from the broker's permanent poison transition, and that
transition cannot later clear `snapshot.poisoned`. The earlier successful `poisoned == false`
assertion therefore excludes `POISONED`. This proves the terminal category for the T053 run, but not
its client, invocation ID, sequence, or triggering interleaving.

## T054 diagnostic

The existing aggregate assertion in `tests/test_phase5_completion.py` was left semantically
unchanged and given a failure-only JSON message containing:

- the exact offending durable metric;
- the nearby global sequence, nearby same-client sequence, and complete same-invocation sequence;
- the opaque-client-to-player correlation already held privately by the parent test;
- that client's runtime, Brain, Reaction, and Vote/Ability terminal summary; and
- sanitized broker snapshots, counts, and backend concurrency/close fields.

No prompt, response text, entry token, admission token, credential, role, or model secret was added.
No timeout, retry, topology, fixture-process behavior, count, or pass/fail condition changed.

Static checks before execution:

```text
python -m compileall -q tests/test_phase5_completion.py
PASS (exit 0)

git diff --check
PASS (exit 0; inherited CRLF warnings only)

git diff --no-index --check -- NUL tests/test_phase5_completion.py
Whitespace PASS; exit 1 is the expected untracked-file content difference
```

## Sole diagnostic completion execution

Exactly one run was started under normal local Windows permissions:

```text
python -m pytest tests/test_phase5_completion.py::PhaseFiveOfflineCompletionTests::test_one_broker_nine_production_llm_clients_complete -q
```

The pytest parent PID `30512` started at 23:34:13 JST. All server, broker, and nine client children
had exited and written terminal artifacts by approximately 23:34:35. The parent then remained idle
with unchanged CPU time after the node and fixture bounds had been exceeded. The repository-local
`TemporaryDirectory`, `C:\AIwolf\tmp1jf_uxts`, was observed in a partially deleted state: cleanup had
already removed the admission metrics, broker result, players 0--4 evidence, audit shards, and other
files, while 25 files for the server and players 5--8 remained.

After more than seven minutes, the hung pytest parent alone was interrupted. The unified execution
returned exit 1 with no captured pytest output. No second completion run was started. At 23:43:57
JST, a normal-permission process inventory reported zero Python processes whose command line matched
the Phase 5 completion test or Phase 5 server/broker/client fixtures.

The partial cleanup destroyed the diagnostic payload before it could be read. Consequently T054
cannot state the exact offending invocation, metric sequence, client, or correlated broker snapshot.
The fact that the T054 run reached or failed the new aggregate assertion is also not recoverable from
the remaining files and is not claimed.

## Surviving bounded evidence

The surviving server result is terminal and internally successful:

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

Players 5--8 each recorded `runtime_success=true`, `runtime_exit=GAME_ENDED`,
`world_exit=CLIENT_ENDED`, and both controller exits `WORLD_ENDED`. Their stdout/stderr logs are
empty, their Brain failure counts are zero, and none retained an unresolved reservation. Their
backend-call counts were respectively 15, 12, 21, and 12. This establishes that these surviving
clients and the game server terminated normally; it does not identify the missing admission event.

## Code-path isolation

`GenerationAdmissionBroker._enqueue_locked` emits `OVERLOADED` only when the authenticated client
already owns a slot or the global slot count is at `max_pending_total`. In the required topology the
registry and global capacity are both exactly nine and each identity owns at most one slot. Thus a
normal-topology `OVERLOADED` means that one authenticated client attempted a second ordinary enqueue
while its previous slot was still live. It is not backend overload: no provider call is required for
this terminal, and the deterministic backend cannot emit it.

The missing sequence is required to decide between a production arbiter/session lifecycle defect,
a deterministic completion-fixture clock/phase interleaving, and a scheduling-only race. Relaxing
broker capacity or the completion assertion would violate the approved one-slot-per-client contract
and is not a valid repair.

## Classification and next boundary

Classification: **UNRESOLVED** (high confidence that the available evidence is insufficient; high
confidence that T053 observed `OVERLOADED`; no supported confidence for its initiating interleaving).

The only safe next task is a test-only evidence-preservation investigation. It should own
`tests/test_phase5_completion.py` and a bounded, gitignored diagnostic artifact outside the
permission-restricted `TemporaryDirectory`; durably write only the same sanitized diagnostic object
before raising, preserve the existing acceptance/timing/topology, and run one bounded completion.
It must also verify deletion or explicit retention of that exact diagnostic root and all owned
processes. No production repair is authorized until that run recovers the same-client sequence.

If and only if the recovered sequence proves a second production arbiter `ENQUEUE` before the first
slot's acknowledged terminal/release/successor transition, the subsequent minimal repair boundary
is `ai_client/brain/invocation.py` plus a deterministic vector in
`tests/test_phase5_brain_admission.py`, followed by the unchanged Phase 5 completion node. Broker
capacity and acceptance criteria must remain unchanged.
