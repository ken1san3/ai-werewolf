Status: APPROVED — T047 cancellation-lifecycle R1 independently approved 2026-09-11

# Phase 5 Nine AI Agents — Detailed Design

## Purpose and Gate

Phase 5 connects nine independently running AI client processes to one already-running loopback
LLM server without allowing those processes to overload the model, observe another player's
response, retain stale generation work, or bypass the Phase 3/4 authority boundaries. It also adds
model-neutral speaking-frequency control, a strict short-chat profile, LLM-free nine-client
completion evidence, one finite opt-in real-model completion smoke, and the measurement needed for
`OPEN_QUESTIONS.md` Q8.

This design does not implement Phase 6 belief, suspicion, strategy, conversation-quality scoring,
or any game/server rule. It requires independent Reviewer approval under D051 before any Phase 5
implementation packet starts.

## Authority and Existing Facts

The authority order is canonical specification, current implementation/protocol facts, tests, then
this detailed design.

- The game server remains the only source of game truth. It sends each client only that player's
  authorized view and validates every action at receipt time.
- Each client process owns exactly one `WorldState`, one `LLMBrain`, one `BrainController`, one
  `BrainInvocationArbiter`, one `ReactionChatController`, and one `VoteAbilityController`.
- `BrainInvocationArbiter` already permits at most one active local Brain invocation, at most one
  pending request per feature owner, reservation priority at the next non-preemptive grant, and no
  internal unbounded queue.
- `BrainController` alone captures coherent received handles, validates a `BrainDecision`, watches
  World changes, rechecks current handles/deadline, and performs the typed Network send.
- Phase 4 `LLMBrain` uses strict request-local structured output, zero transport retry, at most one
  schema-repair call, cancellation propagation, and a durable audit record before returning an
  action-bearing result.
- Phase 4's `JsonlAiAuditSink` is deliberately one-process/one-writer. Nine processes must not append
  concurrently to the same `logs/<game_id>/ai.jsonl` file.
- The target machine evidence is one game-profile OpenAI-compatible llama.cpp service on loopback,
  but endpoint, model, timing values, and model process identity remain configuration/evidence, not
  game-role or development-responsibility authority.
- ROADMAP Phase 5 requires one shared LLM server, multiple agents, generation admission, frequency
  adjustment, short chat, nine-AI completion, and Q8 measurement. The canonical short-chat reference
  is 5–30 model tokens; all nine agents must not run long inference for every message.

## Scope

- one bounded, per-game, cross-process generation-admission broker
- an authenticated client session and `StructuredLLMBackend` proxy for each AI process
- exactly one direct `OpenAICompatibleBackend` owned by the broker
- global reservation/reaction ordering, fairness, deadlines, cancellation, backpressure, metrics,
  and finite shutdown
- an optional shared-admission hook in the existing process-local Brain arbiter
- earlier cancellation when a deadline mapping or received handle becomes stale
- a deterministic model/role-neutral speaking-frequency policy
- prompt/schema/parser short-chat enforcement without silent truncation
- private player-sharded Phase 4 AI audit files and one metadata-only admission log
- offline unit/integration/nine-client completion tests
- one finite opt-in nine-real-client smoke and a finite Q8 benchmark matrix

## Explicit Non-Scope

- any server, game-core, content, or wire-protocol change
- Phase 6 belief/suspicion/strategy, semantic truth assessment, conversation quality, deception,
  personality, or long-term memory
- role-name, team-name, model-name, provider-name, or development-role branching
- remote providers, failover, model selection/routing, batching, speculative decoding, or more than
  one active generation
- a development-task orchestrator, daemon, scheduler, merge system, or Autodev replacement
- starting, stopping, replacing, downloading, or tuning the external model service
- a new server-side chat limit or a change to canonical/preset discussion duration
- automatic retry after overload, timeout, unknown delivery, invalid model output, or broker failure

## 1. Exact Ownership and Process Topology

One Phase 5 game has the following topology:

```text
Game server process (no AI imports)
        ^  nine independent WebSocket sessions
        |
AI client p0 --\
AI client p1 ---\
...               > authenticated loopback framed IPC
AI client p8 ---/          |
                           v
                GenerationAdmissionBroker process
                  - one bounded admission queue
                  - one active lease maximum
                  - one OpenAICompatibleBackend
                  - one metadata-only metrics writer
                           |
                           v
              external loopback LLM server (already running)
```

The Phase 5 launcher owns the broker and nine AI client child processes. It may also own the game
server process in completion/smoke runs. It does not own the external LLM server and never starts,
stops, signals, or kills it. The broker is an inference-resource gate, not a game service or a
development-task orchestrator.

There is one broker per game run in Phase 5. Multi-game host scheduling is deliberately deferred.
The broker accepts exactly the registered nine opaque client identities and one connection per
identity. It owns the only direct HTTP backend used by those nine clients, so the externally observed
generation concurrency is structurally at most one. Client processes use a broker proxy and cannot
construct a second direct backend in the Phase 5 composition root.

The broker receives the selected prompt/schema because it must make the external inference call.
It never sends that value to another client, never caches it after the request becomes terminal, and
never writes it to its metrics log. Privacy is still established first by server delivery and the
Phase 4 allowlisted `BrainInput` projection; the broker is a private local transport boundary, not a
second privacy filter.

## 2. Public Model-Neutral Interfaces

Minor private helper names are not contractual. These frozen values and semantics are.

```python
class GenerationPriority(IntEnum):
    RESERVATION = 0
    REACTION = 1


class AdmissionStatus(str, Enum):
    OFFERED = "OFFERED"
    GRANTED = "GRANTED"
    REPLACED = "REPLACED"
    EXPIRED = "EXPIRED"
    OVERLOADED = "OVERLOADED"
    UNAVAILABLE = "UNAVAILABLE"
    CANCELLED = "CANCELLED"
    POISONED = "POISONED"


@dataclass(frozen=True)
class AdmissionRequest:
    invocation_id: str
    priority: GenerationPriority
    phase: str
    day: int
    action_generation: int
    mapping_order: int
    not_after_monotonic: float


@dataclass(frozen=True)
class AdmissionResult:
    status: AdmissionStatus
    lease: GenerationLease | None
    queue_wait_microseconds: int


class SuccessorReservation(Protocol):
    @property
    def invocation_id(self) -> str: ...
    async def wait_offer(self) -> AdmissionResult: ...
    async def cancel(self) -> AdmissionStatus: ...


class GenerationAdmission(Protocol):
    async def acquire(self, request: AdmissionRequest) -> AdmissionResult: ...
    async def cancel(self, invocation_id: str) -> AdmissionStatus: ...
    async def replace_waiting(
        self,
        old_invocation_id: str,
        replacement: AdmissionRequest,
    ) -> AdmissionResult: ...
    async def reserve_successor(
        self,
        active_invocation_id: str,
        successor: AdmissionRequest,
    ) -> SuccessorReservation: ...
    async def cancel_successor(
        self, successor_invocation_id: str
    ) -> AdmissionStatus: ...
    async def aclose(self) -> None: ...


class GenerationLease(Protocol):
    @property
    def invocation_id(self) -> str: ...
    async def claim(self) -> AdmissionStatus: ...
    def activate(self) -> AbstractContextManager[None]: ...
    async def release(self) -> None: ...


class BrokeredStructuredLLMBackend(StructuredLLMBackend):
    def __init__(self, session: BrokerAdmissionSession) -> None: ...
    @property
    def identity(self) -> BackendIdentity: ...
    async def generate(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse: ...
    async def aclose(self) -> None: ...
```

`AdmissionRequest` contains scheduling metadata only. It contains no role/team/model name, chat
text, prompt, credential, target, private result, or action payload. `invocation_id` is a locally
generated UUID distinct from LLM `request_id` and server action `event_id`.

`acquire()` returns only after the entry is `OFFERED`; an offer consumes no provider capacity and is
still replaceable. `claim()` is the single atomic boundary that changes it to `GRANTED`. `activate()`
is legal only after that acknowledgement and is used only by the process-local arbiter around its one
`BrainController` call. `BrokeredStructuredLLMBackend.generate()` is legal only while that same
session has the claimed active lease. There is no task-local implicit global and no second Brain
wrapper. A call without a current matching lease, a third backend call under one lease, or a request
ID reused under a lease fails closed with a stable admission protocol error. One lease permits at
most the Phase 4 initial call plus one repair call.

`replace_waiting()` is one broker transaction: when the old reaction is `ENQUEUED` or `OFFERED` and
has made zero `GENERATE` calls, it terminals that invocation as `REPLACED` and installs the reservation
in the same client slot before selection resumes. The old `acquire()` returns `REPLACED`; the replacing
call waits for or receives the one offer.

`cancel(invocation_id)` is the ordinary-entry cancellation operation owned by the authenticated
`GenerationAdmission` session. It accepts the same exact non-empty bounded invocation ID used by
`AdmissionRequest`, sends `CANCEL`, and owns its shielded acknowledgement until a bounded terminal.
It is valid only for that session's ordinary `ENQUEUED`, `OFFERED`, or concurrently `CLAIMED` entry;
it is not an attached-successor operation and never replaces `cancel_successor()`.
Because `GenerationAdmission` is runtime-checkable, `cancel` participates in structural validation:
the arbiter rejects an object missing it during construction before registering work. The arbiter
calls the declared method directly, never via `getattr`, and validates that its result is an exact
`AdmissionStatus` before applying the state table below.

The broker serializes `CANCEL` with offer, expiry, replacement, and claim under its state lock:

| State when the locked cancel transition wins/observes | `cancel()` result | Effect |
|---|---|---|
| `ENQUEUED` or `OFFERED` | `CANCELLED` | terminal the entry, invalidate any offered lease, remove the client slot, then run selection |
| `CLAIMED` | `GRANTED` | no mutation and no preemption; the claimant retains lease/active cleanup ownership |
| `REPLACED`, `EXPIRED`, `CANCELLED`, or `POISONED` | that exact terminal status | idempotent, no mutation |
| authenticated session closes while waiting | `UNAVAILABLE` locally | session-loss cleanup removes its ordinary entry; no reconnect reuse |

If cancel wins a cancel/claim race, `claim()` observes `CANCELLED` and activation is impossible. If
claim wins, cancel observes `GRANTED`; before Brain/provider start the arbiter releases the claimed
lease, while after provider start it cancels the consumer Brain so the existing `ABANDON` path owns
drain-or-poison. Cancel racing `OFFER` removes either the enqueued or already-offered entry and returns
`CANCELLED`. Cancel racing expiry/replacement returns whichever exact terminal won the lock. A foreign,
successor, malformed, released, or otherwise invalid-lifecycle ID is an admission protocol violation,
not a guessed success.

One authenticated session owns exactly one bounded control lane per ordinary invocation from offer
until its caller/lease terminal cleanup. `claim()`, public ordinary `cancel()`, `ABANDON`, and
`RELEASE` for that ID all use this lane. The lane admits at most one sent control operation and one
shielded acknowledgement future at a time; an opposite operation waits without allocating a second
future. This deliberately retains `ACK(invocation_id, status)` and removes the current ambiguous
two-waiter case rather than adding a new wire frame.

For concurrent public `claim()` and `cancel()` on the same offered lease, the first operation admitted
to the lane sends its frame and the broker applies that frame under the broker state lock. The waiter
that entered second observes the broker-acknowledged disposition cached by the lane and sends no
second frame:

- cancel first: both public calls return `CANCELLED`, the offered lease is invalid, and activation is
  impossible;
- claim first: both public calls return `GRANTED`, the lease remains claimed by its claimant, and
  cancel neither releases nor preempts it;
- expiry/replacement/poison/session loss first: both observe that exact applicable terminal status and
  no operation can revive the entry.

The winning acknowledgement is consumed once and updates local lease state before the lane admits or
resolves the second call. An unsolicited terminal `ACK` for the still-owned acquire result also sets
the same cached disposition; a subsequently admitted control call returns it locally, and the broker
must not emit a second acknowledgement merely because a queued frame observes an already-notified
terminal. Thus a valid race produces no duplicate or unowned acknowledgement.

Outer cancellation while waiting to enter the lane sends nothing and simply removes that waiter.
Outer cancellation after a control frame is sent cannot cancel or orphan its acknowledgement future:
the session retains the lane, consumes the exact acknowledgement within
`cancellation_grace_seconds`, applies local state, and only then re-raises `CancelledError`. If a
cancelled `claim()` observes `GRANTED`, the session performs an acknowledged `ABANDON` in the same
lane before releasing it, retires the local lease, and then re-raises; no claimed lease is handed to a
cancelled caller. If an acknowledgement is missing or the session closes, the session closes/keeps
closed the transport, resolves every lane/result waiter exactly once as `UNAVAILABLE`, clears local
ownership, and relies on broker disconnect cleanup. A late, duplicate, conflicting, wrong-ID, or
otherwise unowned `ACK` on an open session is an admission protocol violation and closes that session;
it can never resolve another invocation or operation. Lane/tombstone state is removed only after no
caller, lease, result future, or compensating cleanup owns it, so retained control state stays within
the existing one ordinary slot plus one attached-successor bound.

After `CANCELLED`, the arbiter drops the offered lease and caller references before returning its
stale/deadline/cancel outcome. It calls ordinary `cancel()` only before a claim is known to have won;
claimed/active cleanup uses `RELEASE` or `ABANDON` under the same lane.

`reserve_successor()` is accepted only for a claimed same-client reaction. Before sending the frame,
the session creates exactly one shielded result future keyed by the successor invocation ID; the
returned `SuccessorReservation` is the sole public owner of that future. The call returns after the
broker has either acknowledged attachment or supplied an immediate terminal status. It attaches one
reservation metadata record to the occupied client slot, not a second entry or lease. A second
attached successor or concurrent second `wait_offer()` is a protocol error. The process-local arbiter
owns the handle and the original reservation feature caller continues to own one
`invoke() -> BrainDispatchResult` future; feature controllers never call admission directly.

When the active reaction releases safely, the broker atomically converts the attached metadata to
`ENQUEUED` before selection. The authenticated session routes the later `OFFER(successor_id)` to the
shielded future. `wait_offer()` returns `AdmissionResult(OFFERED, new_successor_lease, wait)`; that
lease has the successor invocation ID and is claimed/activated/released by the normal path. It never
reuses the reaction lease. Before offer, expiry, caller cancellation, poison, or session loss resolves
the same future once as `EXPIRED`, `CANCELLED`, `POISONED`, or `UNAVAILABLE`, respectively, with no
lease. After offer, the returned lease owns claim/activation and the handle owns no further result.

`SuccessorReservation.cancel()` delegates to `cancel_successor()`. Cancellation of `wait_offer()`
does not cancel the shielded session future; the arbiter catches caller cancellation, explicitly
cancels the exact successor, awaits its acknowledgement within the control-plane grace, and consumes
the result. Expected terminal statuses are returned, not raised; only invalid types/protocol use raise.
These operations remain owner-internal and bounded.

The proxy exposes the sanitized `BackendIdentity` returned during authenticated readiness. Its
`aclose()` closes only that client session; only the broker closes the direct backend.

The existing arbiter receives one additive optional dependency:

```python
class BrainInvocationArbiter:
    def __init__(
        self,
        *,
        controller: BrainController,
        clock: Clock = time.monotonic,
        admission: GenerationAdmission | None = None,
        invocation_id_factory: Callable[[], str] = uuid4_string,
    ) -> None: ...

    async def invoke(
        self,
        *,
        owner: BrainInvocationOwner,
        priority: BrainInvocationPriority,
        allowed_handles: tuple[ActionHandle, ...],
        timeout_seconds: float,
        dispatch_deadline: DispatchDeadline,
        on_brain_start: Callable[[], None] | None = None,
    ) -> BrainDispatchResult: ...
```

`admission=None` is the exact Phase 3/4 direct behavior. Phase 5 composition supplies the authenticated
session. The owner already fixes the global priority without inspecting a prompt: `vote_ability`
maps to `RESERVATION`; `reaction_chat` maps to `REACTION`. `on_brain_start`, when present, is called
synchronously exactly once after `claim()` succeeds and immediately before
`BrainController.decide_and_send`; it is never called for queued, offered, replaced, expired, stale,
or cancelled work. An exception from it fails the invocation and releases the lease without a Brain
call. This is the counter-commit boundary used by the frequency controller.

To prevent work made stale while waiting globally, `BrainController` adds a public, synchronous,
side-effect-free check and a compatible capture extension:

```python
def dispatch_context_is_current(
    self,
    *,
    allowed_handles: tuple[ActionHandle, ...],
    dispatch_deadline: DispatchDeadline,
) -> bool: ...

def capture_input(
    self,
    *,
    allowed_handles: tuple[ActionHandle, ...] | None = None,
    dispatch_deadline: DispatchDeadline | None = None,
) -> BrainInput | None: ...
```

The check performs the existing coherent snapshot/action test, exact received-handle membership,
current deadline-mapping identity, and `clock() < not_after_monotonic`. It creates no input and sends
nothing. Existing callers that omit the new argument remain unchanged.

### Broker configuration and finite bounds

```python
@dataclass(frozen=True)
class GenerationBrokerConfig:
    max_clients: int = 9
    max_pending_total: int = 9
    max_pending_per_client: int = 1
    max_active: Literal[1] = 1
    max_calls_per_lease: Literal[2] = 2
    max_frame_bytes: int = 131072
    authentication_timeout_seconds: float = 5.0
    cancellation_grace_seconds: float = 0.25
    provider_drain_grace_seconds: float = 5.0
    shutdown_grace_seconds: float = 6.0
    metrics_queue_capacity: int = 64
```

All integer fields reject `bool`, zero, negative, and values above their documented fixed maximum.
All time fields must be positive and finite. `provider_drain_grace_seconds` must be at least the
direct backend's `request_timeout_seconds`; `shutdown_grace_seconds` must be at least
`provider_drain_grace_seconds + cancellation_grace_seconds`. Phase 5 requires exactly nine registered
clients; generic values above nine are not exposed. `max_pending_total` counts occupied client slots:
one active slot plus at most eight waiting slots. An active slot may hold one bounded successor
reservation metadata record but never a second prompt, entry, offer, lease, or provider call. Prompts
are not placed into the admission queue.

### Optional provider timing evidence

Q8 needs prompt and generation throughput, which the base OpenAI envelope does not guarantee. The
model-neutral response therefore gains one optional value:

```python
@dataclass(frozen=True)
class ProviderTiming:
    prompt_tokens: int
    prompt_microseconds: int
    completion_tokens: int
    completion_microseconds: int


@dataclass(frozen=True)
class StructuredGenerationResponse:
    # existing fields unchanged
    provider_timing: ProviderTiming | None = None


class ProviderQuiescence(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    PROVEN_TERMINAL = "PROVEN_TERMINAL"
    UNKNOWN = "UNKNOWN"


class LLMBackendError(RuntimeError):
    def __init__(
        self,
        code: LLMBackendErrorCode,
        *,
        http_status: int | None = None,
        retryable: bool = False,
        provider_quiescence: ProviderQuiescence = ProviderQuiescence.UNKNOWN,
    ) -> None: ...
```

The loopback adapter maps only a complete top-level `timings` object with llama.cpp's exact keys
`prompt_n`, `prompt_ms`, `predicted_n`, and `predicted_ms` to this value. It accepts the value only
when both counts are exact non-negative integers and both durations are finite non-negative numbers,
and converts milliseconds to integer microseconds by round-half-up. A partial/malformed optional
timing object is ignored, never
used for game behavior, and never makes a valid generation fail. The target Q8 run requires timing
availability to report separated throughput; the ordinary completion smoke does not.

`ProviderQuiescence` is transport evidence, never provider-specific cancellation control. A success,
or an error classified only after the complete HTTP status and response body were received, is
`PROVEN_TERMINAL`. A connect/pool failure proven to occur before any request byte was sent is
`NOT_STARTED`. Timeout, caller cancellation, partial response, write/read/decoding transport failure,
early size rejection, and every error that cannot prove either condition are `UNKNOWN`; the default
is deliberately `UNKNOWN`. Phase 4 callers may ignore this additive field. The Phase 5 broker must
apply the fail-closed quiescence rules in §4.

## 3. IPC, Authentication, Readiness, and Privacy

The IPC transport is loopback TCP selected with port `0`, not a public AIwolf wire protocol. Frames
are four-byte unsigned big-endian length followed by strict UTF-8 JSON. Zero length, over-bound length,
invalid UTF-8/JSON, duplicate keys, NaN/Infinity, unknown message type, missing/extra key, wrong type,
or protocol version mismatch closes only that client and cancels its work. The version literal is
`aiwolf.generation-ipc.v1`.

After `HELLO(protocol, client_id, token) -> READY(backend_identity)`, the closed message vocabulary is
`ENQUEUE(invocation metadata)`, `OFFER(invocation_id)`, `CLAIM(invocation_id)`,
`REPLACE(old_invocation_id, replacement metadata)`, `RESERVE_SUCCESSOR(active_invocation_id,
successor metadata)`, `SUCCESSOR_ATTACHED(active_invocation_id, successor_invocation_id)`,
`CANCEL_SUCCESSOR(successor_invocation_id)`, `GENERATE(invocation_id, call_ordinal,
structured_request)`, `RESULT(invocation_id, call_ordinal, structured_response)`, `ERROR(invocation_id,
call_ordinal, code, retryable, provider_quiescence[, http_status])`, `ABANDON(invocation_id)`, `CANCEL(invocation_id)`,
`RELEASE(invocation_id)`, and `ACK(invocation_id, status)`. All
non-handshake frames require an invocation owned by the authenticated connection; ordinals are exactly
1 then optional 2. `REPLACE` and `RESERVE_SUCCESSOR` are broker-atomic.
`SUCCESSOR_ATTACHED` is routed only to the session future already registered for that exact successor;
its later `OFFER` or terminal `ACK` resolves that same future. Only the launcher's private control pipe
can request broker shutdown.

### Exact `ERROR` envelope (T040 bounded addendum)

`ERROR` is one flat JSON object. The non-HTTP key set is exactly:

```text
protocol, type, invocation_id, call_ordinal, code, retryable, provider_quiescence
```

When and only when `code == "HTTP_STATUS"`, the exact key set additionally contains
`http_status`. JSON object order is not semantic; duplicate and extra keys remain forbidden. The
enclosing four-byte length prefix and global `max_frame_bytes` bound are unchanged. This adds no
free-form error field: protocol/type/code/quiescence are fixed enums, invocation ID and ordinal reuse
their existing bounded validators, retryability is one boolean, and HTTP status is one bounded
integer.

The receiver validates the complete object before constructing an exception:

- `protocol` is exactly `aiwolf.generation-ipc.v1`, `type` is exactly `ERROR`, `invocation_id` belongs
  to the authenticated session's one outstanding call, and `call_ordinal` is that call's exact `1` or
  `2`;
- `code` is one exact `LLMBackendErrorCode` value;
- `retryable` is required and `type(value) is bool`; it is never inferred from code or status;
- `provider_quiescence` is required and one exact `ProviderQuiescence` value; it is never inferred;
- for `HTTP_STATUS`, `http_status` is required with `type(value) is int` (not `bool`) and
  `100 <= value <= 599`;
- for every other code, `http_status` must be absent. `null`, zero, a placeholder, or a copied status
  on a non-HTTP code is invalid.

After validation the proxy reconstructs without transformation:

```python
LLMBackendError(
    LLMBackendErrorCode(frame["code"]),
    http_status=frame.get("http_status"),
    retryable=frame["retryable"],
    provider_quiescence=ProviderQuiescence(frame["provider_quiescence"]),
)
```

Thus all four public attributes and the existing code-only exception message are identical across
the broker boundary. The sender must encode the attributes of the actual sanitized
`LLMBackendError`; it may not derive, default, redact, or replace one with a placeholder.

Missing/extra/duplicate keys, wrong JSON types, unknown enums, mismatched invocation/ordinal,
code-specific `http_status` violations, or an over-bound frame are an admission protocol violation.
The proxy constructs no partial backend error and fabricates no HTTP status. It closes that session,
resolves its other local waiters under the existing unavailable rules, and fails the affected local
call with a new sanitized `LLMBackendError(ADMISSION_PROTOCOL, http_status=None, retryable=False,
provider_quiescence=UNKNOWN)`. This local protocol error is explicitly not represented as the
original provider error. The broker applies its existing connection-loss cleanup to the closed
session. If the broker cannot validate/encode its own source error, it treats that internal terminal
as unclassified and executes the universal poison path rather than sending a malformed frame.

For a direct backend `UNKNOWN` terminal, the broker first acquires its state lock, changes the run to
`POISONED`, resolves attached/queued work as already specified, and queues the bounded poison metric;
only then may it serialize and send the exact original `ERROR` to the current caller. A failed or
malformed socket delivery cannot undo poison. For `PROVEN_TERMINAL` or `NOT_STARTED`, the same
classification occurs before delivery but does not poison; the current lease remains claimed until
normal release. This ordering is identical for call ordinals 1 and 2.

The envelope and logs may contain only code, exact HTTP status when applicable, retryable,
quiescence, and the previously approved routing/timing metadata. They never contain exception text,
raw exception repr, URL, headers, body, prompt, schema, response text, credential, player identity,
role, model output, or stack trace. `http_status`, `retryable`, and quiescence are sanitized bounded
facts and may be retained in the player-private Phase 4 audit and broker metadata record.

Normative vectors (shown in explanatory key order) are:

```json
{"protocol":"aiwolf.generation-ipc.v1","type":"ERROR","invocation_id":"00000000-0000-4000-8000-000000000001","call_ordinal":1,"code":"HTTP_STATUS","http_status":503,"retryable":true,"provider_quiescence":"PROVEN_TERMINAL"}
{"protocol":"aiwolf.generation-ipc.v1","type":"ERROR","invocation_id":"00000000-0000-4000-8000-000000000002","call_ordinal":2,"code":"REQUEST_TIMEOUT","retryable":true,"provider_quiescence":"UNKNOWN"}
```

The first reconstructs exactly `HTTP_STATUS/503/True/PROVEN_TERMINAL` and does not poison. The second
has no `http_status`, reconstructs exactly `REQUEST_TIMEOUT/None/True/UNKNOWN`, and the broker must
already be poisoned before the client-side receive hook observes it. A third malformed vector is the
first object with `http_status` omitted; it closes the session and produces only the new local
`ADMISSION_PROTOCOL/None/False/UNKNOWN` error, never `HTTP_STATUS` with fabricated metadata.

Before spawn, the launcher generates one 256-bit random admission token per opaque client ID. Secrets
and backend API key are delivered through inherited stdin/bootstrap pipes, never command arguments,
environment dumps, repository files, logs, reprs, or status JSON. Broker readiness returns only its
loopback address, protocol version, sanitized backend identity, and config fingerprint through a
dedicated control pipe. Each client authenticates once with its opaque client ID and token and receives
`READY` before Network/World/controller startup. One token authenticates one live connection; replay,
wrong token, duplicate connection, and unregistered identity are rejected without revealing which
check failed.

The broker routes offers, claim/result/error, cancellation/abandonment acknowledgement, and release
acknowledgement only on the authenticated connection that owns the exact invocation. It never offers
a list of clients, queued work, prompts, response text, or usage to peers. Opaque client IDs are
random per run and have no role/model/seat meaning inside the broker. The private smoke manifest maps
them to player IDs after shutdown for evidence; the broker does not need that mapping.

Startup order is:

1. validate all settings and output paths before any process starts;
2. start the already-configured external model separately (operator responsibility);
3. launcher spawns broker and passes its private registry/backend settings through bootstrap pipe;
4. broker binds loopback, creates exactly one direct backend and metrics writer, then reports READY;
5. launcher spawns nine clients and passes each only its own admission secret/address plus its own
   game entry material;
6. each client authenticates, starts its own audit shard, then starts Network, World, local arbiter,
   Reaction, and Vote/Ability in the existing ownership order.

Broker READY proves configuration, bind, authentication service, and resource creation. As in Phase
4, actual model readiness is established only by the first successful structured request; there is
no provider-specific health call or fallback model.

## 4. Queue Ordering, Fairness, Deadlines, and Backpressure

### Entry state machine

```text
broker slot:
NEW -> ENQUEUED -> OFFERED -> CLAIMED -> [ACTIVE_CALL_1 -> ACTIVE_CALL_2] -> RELEASED
          |           |         |               |
          +-----------+---------+---------------+-> EXPIRED/CANCELLED/FAILED
          |           |
          +-----------+-> REPLACED -> ENQUEUED reservation in the same slot
                                CLAIMED/ACTIVE + attached successor
                                  -> terminal active -> ENQUEUED reservation in the same slot

provider abandonment:
ACTIVE_CALL_n -> DRAINING -> ABANDONED_DRAINED
                         \-> POISONED

attached successor:
REGISTERED -> ATTACHED -> ENQUEUED -> OFFERED -> CLAIMED -> ordinary lease lifecycle
                 \-----------> EXPIRED/CANCELLED/UNAVAILABLE/POISONED

local arbiter:
IDLE -> WAITING_ADMISSION -> ACTIVE_BRAIN -> IDLE
                |                 |
                +-> SUSPENDED_REACTION <-+  (one bounded retained caller only)
```

`WAITING_ADMISSION` means the local request is enqueued/offered but has not won `CLAIM`; it owns no
provider work and has not begun `BrainController`. `ACTIVE_BRAIN` begins only after claim
acknowledgement and the `on_brain_start` commit. Broker `ACTIVE_CALL_n` is narrower still: the direct
HTTP operation actually exists. These distinctions are observable in snapshots and tests.

Every terminal state is recorded once in bounded metadata. A client owns at most one broker slot and
one offered/claimed lease. Duplicate invocation IDs and an unattached concurrent request from one
client are protocol errors. The only additional retained work is (a) one local suspended reaction
caller and (b) one reservation successor metadata record attached to the same occupied broker slot;
neither contains a prompt or owns a lease/provider task.

### Same-client replacement and successor rule

The process-local arbiter, not either feature controller, owns this transition:

1. If a reservation arrives while the same client's reaction is `WAITING_ADMISSION`, the arbiter
   retains that reaction's caller as `SUSPENDED_REACTION` and calls `replace_waiting()`. The broker
   atomically replaces an `ENQUEUED` or `OFFERED` reaction with the reservation in the same slot. The
   reaction's old invocation terminals `REPLACED`; it does not resolve the feature caller, call the
   Brain, consume a lease, or send. The reservation is now globally visible at `RESERVATION` priority.
2. If the reservation arrives after the reaction's `CLAIM` won, the reaction is non-preemptible. The
   arbiter calls `reserve_successor()`, retains the reservation caller locally, and owns the returned
   `SuccessorReservation`. The broker attaches it to the active slot. When that reaction releases
   after a quiescence-safe terminal, the broker converts the slot to the successor reservation before
   running selection, so no other reaction can win the intervening grant. The arbiter awaits
   `wait_offer()`, then claims and activates the returned successor lease through the ordinary path.
   The active Brain/provider operation is never cancelled merely to promote the reservation.
3. After the reservation terminals, the suspended reaction is re-enqueued with a fresh admission
   invocation ID only if its exact phase, handle set, deadline mapping, cutoff, and controller
   lifecycle remain current. Otherwise its caller receives the existing stale/deadline outcome. A
   resumed reaction retains its already-prepared frequency decision and source; it is not evaluated,
   counted, or drawn again.

Broker state transitions are serialized under one lock. In a `CLAIM`/`REPLACE` race, replacement
winning first makes the reaction unclaimable; claim winning first makes it nonreplaceable and the
reservation follows the successor path. In a `CANCEL`/`REPLACE` race, cancellation winning removes
the old slot and the reservation performs a normal enqueue; replacement winning terminals the old ID
and cancellation of that ID is an idempotent `REPLACED` acknowledgement. `OFFER` alone never commits
a Brain invocation. The arbiter rechecks local priority and dispatch currency immediately before
`CLAIM`, and never activates an unacknowledged or replaced lease.

Attached-successor transitions use that same broker lock. If its deadline wins before active release,
the broker detaches it, records `EXPIRED`, and resolves `wait_offer()` without a lease. If safe active
release wins, the successor becomes `ENQUEUED` atomically; cancellation winning before `CLAIM`
removes it from `ATTACHED`, `ENQUEUED`, or `OFFERED` and both `cancel_successor()` and `wait_offer()`
observe `CANCELLED`. If `CLAIM` wins first, `cancel_successor()` returns `GRANTED` and cannot preempt;
caller cancellation then follows ordinary active-Brain abandonment. Repeating cancel after any
terminal returns that exact terminal status and changes nothing. Poison wins over release/offer and
resolves the handle `POISONED`; authenticated session loss resolves it `UNAVAILABLE`, removes the
successor from the broker, and can never be repaired by reconnecting. No race can resolve the handle
twice, return the old reaction lease, or leave the reservation caller pending after broker shutdown.

The local arbiter serializes reservation arrival against active-lease release. If arrival wins, it
must receive `SUCCESSOR_ATTACHED` (or a terminal handle result) before permitting release; if release
wins, local state is already `IDLE` and the reservation uses ordinary `acquire()` rather than
`reserve_successor()`. A direct stale `reserve_successor(active_id, ...)` observed after that active ID
released resolves its handle `UNAVAILABLE` and mutates no broker queue. This prevents an undocumented
release/attach retry or a window in which a reservation known before release is hidden from selection.

Frequency evaluation and its repetition fingerprint are consumed exactly once when the original
reaction passes the policy. `ReactionChatSnapshot.chat_brain_invocations` and the per-phase chat
invocation count increment only in `on_brain_start`, after claim and immediately before the one Brain
call. Thus a reaction replaced before claim consumes one frequency evaluation but zero chat Brain
invocations; if later resumed and claimed it consumes exactly one invocation. Reservation opportunity
and wire-attempt ledgers retain their Phase 3.5 semantics; their Brain-start evidence likewise commits
only after claim. Queue metrics record the old reaction as `REPLACED` and start the reservation's wait
at the atomic replacement time.

### Selection rule

The broker uses strict reservation priority at each new offer and never preempts a claimed Brain or
active generation.
Within one priority class it uses seeded round-robin over registered opaque client IDs:

1. sort identities by `SHA-256("aiwolf.phase5.fairness.v1", fairness_seed, opaque_client_id)`;
2. begin at the seed-derived start cursor;
3. scan cyclically for the first pending, non-expired identity in the selected class;
4. after successful claim, move that class's cursor to the following identity.

`fairness_seed` is explicit launcher configuration, recorded in private run evidence, and fixed in
deterministic tests. It is not Python `hash()`, process scheduling, arrival order, a model value, or a
game role. A continuously pending client in one class receives a grant within at most eight completed
same-class grants, assuming each active lease terminates. Reservation work may delay reaction work;
that is the existing intentional next-grant priority. Expired reaction work is suppressed, not run
late. Different fixed seeds must change the initial client in a test vector, preventing permanent
seat-zero preference.

### Deadline and stale-work rule

All broker/client processes are on the same host and use Python's system-wide `time.monotonic()`
domain. The ticket copies the exact `DispatchDeadline.not_after_monotonic` cutoff. The client checks
the dispatch context before enqueue; the broker rejects expired enqueue, removes expired pending
entries before every offer, and wraps each direct HTTP call in the lesser of the backend timeout and
the remaining lease budget.

While waiting for admission, the local arbiter races acquire against `WorldState.wait_for_update()`.
After every update it calls `dispatch_context_is_current`. A changed phase, connection/action
generation, deadline mapping, lost received handle, World terminal, stop, or cutoff cancels the exact
broker entry or attached successor. After an offer, the arbiter rechecks the same context, local
priority, and `capture_input(..., dispatch_deadline=...)` before claim. Failure cancels the offer
without projecting a prompt or calling the model. Only a successful claim is activated.

`BrainController.decide_and_send` passes its optional `DispatchDeadline` into the active decision
watcher. When it is non-null, the watcher rechecks the exact current mapping tuple and
`clock() < not_after_monotonic` before waiting and after every World update, and bounds its next wait
by both the ordinary decision timeout and the remaining dispatch cutoff. Mapping replacement, mapping
clear, or cutoff equality/expiry takes precedence over a concurrently completed Brain result: the
controller marks `DEADLINE_SUPPRESSED`, cancels its owned Brain task, awaits that cancellation within
the existing Brain cancellation grace, consumes/discards any raced result, and returns with zero
typed send. Other semantic phase/handle staleness with the exact deadline still current remains
`STALE`. The existing final pre-send handle/deadline recheck remains mandatory.

For a brokered Brain already inside `generate()`, cancelling the owned consumer task propagates to
the proxy, which sends the existing `ABANDON` and waits only for the bounded control-plane
acknowledgement—never for the old provider response. The broker shields its direct backend task,
enters `DRAINING`, and applies the existing drain-or-poison rule; lease release cannot authorize a
successor before quiescence. The controller outcome may therefore finish promptly while the broker is
still draining. A Brain that ignores local cancellation triggers the existing bounded unresponsive
fail-closed state and still cannot send. With `dispatch_deadline=None`, the watcher does not query
deadline mappings, add a cutoff timer, or alter Phase 3.3 timeout/stale/cancellation behavior.

The exact post-`ABANDON` consumer disposition is:

1. `ABANDON` for a claimed or active ordinary invocation runs in that invocation's control lane. The
   broker acknowledges `CANCELLED` after atomically marking the consumer abandoned; if provider work
   exists, the broker slot remains `DRAINING`. `POISONED` or `UNAVAILABLE` may instead win only through
   the already-defined poison/session-loss races.
2. On `CANCELLED` or `POISONED` acknowledgement, the session marks the local lease
   `ABANDON_ACKNOWLEDGED`, exits any activation, removes claimed invocation, call-ordinal, request-ID,
   and active generation-future bookkeeping, and retains the authenticated connection. It does not
   remove or resolve an attached-successor future. On `UNAVAILABLE`, it marks the distinct local
   `ABANDON_SESSION_LOST` disposition and performs the same local claim cleanup while normal
   session-loss cleanup resolves the successor `UNAVAILABLE`.
3. `GenerationLease.release()` on either locally retired abandonment disposition returns `None`
   without sending `RELEASE`; on `ABANDON_ACKNOWLEDGED` it performs the final local
   `ABANDONED -> RETIRED` transition, while `ABANDON_SESSION_LOST` is already retired. Repeating
   release on either is the same local no-op. Normal non-abandoned claimed leases still send one
   `RELEASE`; ordinary duplicate release remains invalid. The arbiter's mandatory `finally` cleanup
   therefore cannot issue `RELEASE` in broker `DRAINING`, close a healthy session, or replace the
   already-selected `DEADLINE_SUPPRESSED`/`STALE`/`CANCELLED` result with `BRAIN_FAILED`.
4. If a complete safe provider terminal wins just before `ABANDON`, its result is consumed/discarded,
   the broker observes the later abandonment from `CLAIMED`, and finishes `ABANDONED_DRAINED`. If
   `ABANDON` wins first, the safe terminal finishes the same state from `DRAINING`. If poison wins on
   either side, the lease is locally retired and the successor resolves `POISONED`. These races never
   restore consumer-current, emit a typed game send, or require a release frame.
5. A still-current attached successor remains attached throughout normal abandonment and provider
   drain. No offer occurs while `DRAINING`; safe drain atomically converts it to `ENQUEUED` and its
   existing public handle later receives the offer, while its own deadline may still yield `EXPIRED`.
   Poison yields `POISONED`. Only genuine authenticated session loss yields `UNAVAILABLE`; normal
   acknowledged abandonment and local lease retirement never close the session or discard the
   successor caller/future.

The active watcher establishes its stale/deadline/cancel outcome before this cleanup. Every
acknowledgement, provider-terminal, poison, release, and session-close race updates cleanup evidence
but cannot overwrite that primary `BrainDispatchResult`.

The broker/client frame order is part of this guarantee. If `ABANDON` wins the broker lock, it marks
the consumer non-current before emitting its acknowledgement, so a later provider terminal emits no
`RESULT`/`ERROR` to that consumer. If a safe/poison provider terminal wins after the cancellation has
entered the proxy but before the broker handles `ABANDON`, its `RESULT`/`ERROR` is enqueued before the
ABANDON acknowledgement; the client retains the generation future through that acknowledgement and
consumes/discards the earlier frame before clearing it. No provider frame may arrive after an
acknowledged ABANDON as an unowned result. If the Brain/proxy task had already completed before
cancellation reached `generate()`, no ABANDON is required: the watcher discards the raced Brain result
and the arbiter performs the ordinary quiescent `RELEASE`, with the same primary outcome and successor
ordering.

Queued stale work is never sent to the model. Provider work already started is drained/discarded or
poisons admission under the rule below, and can never produce a later dispatch.

### Cancellation and provider-neutral quiescence

The broker applies one rule to **every** direct backend-call terminal, whether the authenticated
consumer is current, abandoned, disconnected, or shutting down and whether the call is ordinal 1 or
the repair call at ordinal 2. Under the broker state lock, before resolving a caller, releasing a
lease, converting an attached successor, or running selection:

1. `StructuredGenerationResponse` is a successful, complete HTTP response and proves terminality.
2. `LLMBackendError(PROVEN_TERMINAL)` or `LLMBackendError(NOT_STARTED)` is safe to terminal the direct
   call. The exact sanitized error still fails its current caller; no retry is added.
3. `LLMBackendError(UNKNOWN)`, `CancelledError`, or any unclassified exception atomically and
   permanently poisons admission. The current caller receives the original sanitized backend error
   with `UNKNOWN` (or `ADMISSION_POISONED` for an unclassified terminal), the lease cannot unpoison on
   release, every attached successor resolves `POISONED`, and no new `OFFER` is emitted.

A proven-safe ordinal-1 success only returns the broker from `ACTIVE_CALL_1` to the same client's
`CLAIMED` lease; it does not admit another client while Phase 4 validation may request ordinal 2. An
ordinal-2 terminal follows the identical classification. Complete-body encoding/envelope/HTTP errors
may be `PROVEN_TERMINAL`; timeout, partial body, read/write/decompression failure, early response-size
abort, and any failure without a fully drained response are `UNKNOWN`. A connect/pool failure is
`NOT_STARTED` only when the adapter proves no request byte was written. Thus an ordinary connected
caller's local timeout/error can never release admission while unowned provider work might continue.

- Cancelling a pending `acquire()` sends `CANCEL`, waits for a shielded terminal acknowledgement,
  and removes the entry before returning.
- If offer and cancel cross, the broker either proves the entry was still queued/offered or observes
  a completed claim. The client cannot activate an unacknowledged lease.
- Consumer cancellation, World staleness, deadline expiry, or connection loss during a provider call
  sends/acts as `ABANDON`. The broker marks the result undeliverable but does **not** cancel the direct
  HTTP task merely because its caller left. It shields that one owned task, enters `DRAINING`, and
  consumes/discards the response to a natural terminal. No successor is offered while draining.
- If the task completes within `provider_drain_grace_seconds` with success,
  `ProviderQuiescence.PROVEN_TERMINAL`, or `NOT_STARTED`, the broker records
  `ABANDONED_DRAINED`, releases the slot, and may select a successor. Invalid JSON is still a complete
  response and is quiescent; it remains an ordinary fail-closed model error and is never dispatched.
- If the grace expires or the task ends with `ProviderQuiescence.UNKNOWN`, the broker enters permanent
  run state `POISONED`, records `PROVIDER_QUIESCENCE_UNKNOWN` and `ADMISSION_POISONED`, cancels/awaits
  its local HTTP task for at most the remaining `cancellation_grace_seconds` only to clean broker-owned
  resources, and refuses every successor/new admission with `POISONED`. If the local task is still
  live, broker shutdown reports cleanup incomplete and the launcher terminates that recorded broker
  child within its outer bound. A later idle-looking provider, closed socket, or late task completion
  cannot unpoison that run. Only a new broker run after operator-established model readiness can admit
  work.
- `cancellation_grace_seconds` bounds only the client's wait for the broker to acknowledge that
  pending cancellation or active abandonment was registered. It is not evidence of provider stop and
  never authorizes a successor. `provider_drain_grace_seconds` bounds natural drain after abandonment.
  `shutdown_grace_seconds` covers the complete broker drain/control-plane interval.
- Broker shutdown stops admission, terminals queued/offered work, marks the active consumer abandoned,
  and waits for the same shielded drain rule. It then drains/closes metrics, closes the backend and
  connections, and joins its task group within the configured grace; otherwise the launcher applies
  its recorded-child termination bound. It exits non-zero if quiescence becomes unknown/poisoned.
- Launcher success requires broker/game/nine client children to exit zero. Failure/timeout performs
  bounded graceful shutdown, then terminates only recorded owned child PIDs and verifies none remain.
  On `PROVIDER_QUIESCENCE_UNKNOWN` it returns non-zero, preserves the poison/drain evidence, and does
  not claim external-provider cleanup. The external model PID/listener is inventory-only and is never
  an owned cleanup target.

There is no condition under which an expired/cancelled response can be cached for a later phase or
returned to a different invocation. A late frame with a terminal invocation ID is discarded and
counted as a protocol violation. Broker snapshots expose `offered`, `claimed`,
`provider_call_active`, `draining`, `poisoned`, and sanitized `poison_reason`; metadata terminal codes
include `REPLACED`, `ABANDONED_DRAINED`, `PROVIDER_QUIESCENCE_UNKNOWN`, and `ADMISSION_POISONED`.
Every direct-call metrics record includes call ordinal, original stable backend code,
`provider_quiescence`, consumer-current/abandoned state, and poison transition. Shutdown racing any
call first stops new offers, then observes the same locked terminal classification; neither shutdown
nor active `release()` can overwrite `POISONED`.

### Overload and failure propagation

Normal nine-client topology cannot exceed capacity because each registered client has at most one
entry. If a malformed/duplicate producer or configured capacity violation occurs, admission returns
`OVERLOADED` immediately; it does not evict another entry, block beyond the caller deadline, retry, or
fall back to a direct backend. Before a Brain call, the arbiter maps expired admission to existing
`DEADLINE_SUPPRESSED`; overload/unavailable to a sanitized `BRAIN_FAILED` error type. During a backend
call, the proxy maps stable admission errors to new `LLMBackendErrorCode` values
`ADMISSION_UNAVAILABLE`, `ADMISSION_OVERLOADED`, `ADMISSION_EXPIRED`, `ADMISSION_POISONED`, or
`ADMISSION_PROTOCOL`, so the Phase 4 audit/fail-closed path remains intact. `REPLACED` is internal to
the arbiter and never appears as a model error. Consumer cancellation remains `CancelledError`.

Every direct backend error first passes the universal quiescence transition above. This error mapping
cannot release a slot, deliver an offer, or overwrite poison before that transition completes.

Broker/backend failure fails every affected waiter once and stops new admission. AI clients continue
to ingest World/Network and the game server observes only silence/no-selection. No alternate model,
deterministic move, canned chat, or retry is selected.

## 5. Phase 3/4 Composition Without Two Brains

Each Phase 5 client has this exact composition:

```text
BrokerAdmissionSession <--------- BrainInvocationArbiter shared-admission hook
          |
          +-> BrokeredStructuredLLMBackend -> LLMBrain
                                                    |
WorldState -> ReactionChatController --\            v
                                            BrainController -> NetworkClient
WorldState -> VoteAbilityController ----/
```

There is one direct `LLMBrain`; it is not wrapped in `DeterministicVoteAbilityBrain`, and there is no
Brain per action family. Both feature controllers retain their current process-local arbiter endpoint.
The local arbiter obtains, claims, and activates one global lease around exactly one existing
`BrainController.decide_and_send` call. Phase 4 projection/schema/audit/repair run unchanged inside
that call, and both repair attempts stay under the same bounded lease.

Server-authoritative action handles, action acceptance/rejection correlation, delivery-unknown
semantics, reservation ledgers, pre-send re-arm caps, chat semantic finalization, and no-retry rules
remain unchanged. Admission grant proves only permission to use the model; it is neither a game action
permit nor server acceptance.

## 6. Speaking-Frequency Policy

Frequency control is a pure, injected client policy above the LLM. It never changes authorization or
the server clock and never reads a role, team, alignment, model, provider, or hidden state.

```python
@dataclass(frozen=True)
class SpeakingProfile:
    talkativeness: float = 0.5
    ordinary_event_importance: float = 0.5
    direct_mention_importance: float = 1.0
    initial_event_importance: float = 1.0
    cooldown_seconds: float = 0.20
    max_trigger_evaluations_per_phase: int = 32
    repetition_window: int = 8


class FrequencySuppression(str, Enum):
    PROBABILITY = "PROBABILITY"
    COOLDOWN = "COOLDOWN"
    REPETITION = "REPETITION"
    SELF_CHAIN = "SELF_CHAIN"
    INVOCATION_CAP = "INVOCATION_CAP"
    EVALUATION_CAP = "EVALUATION_CAP"


@dataclass(frozen=True)
class SpeakingOpportunity:
    phase_key: ReactionPhaseKey
    trigger_kind: ReactionTriggerKind
    source_order: int | None
    source_player_id: str | None
    source_channel: str | None
    source_message: str | None
    self_player_id: str
    self_display_name: str
    attempt_ordinal: int


@dataclass(frozen=True)
class PreparedSpeakingOpportunity:
    opportunity: SpeakingOpportunity
    event_importance: float
    source_message_sha256: str | None


@dataclass(frozen=True)
class FrequencyDecision:
    should_invoke: bool
    suppression: FrequencySuppression | None
    event_importance: float
    threshold: float
    draw: float


@dataclass(frozen=True)
class SpeakingFrequencyState:
    phase_key: ReactionPhaseKey | None
    evaluation_count: int
    committed_brain_invocations: int
    last_accepted_chat_at: float | None
    recent_source_fingerprints: tuple[str, ...]


class SpeakingFrequencyPolicy(Protocol):
    @property
    def profile(self) -> SpeakingProfile: ...

    def prepare(self, opportunity: SpeakingOpportunity) -> PreparedSpeakingOpportunity: ...
    def evaluate(self, prepared: PreparedSpeakingOpportunity) -> FrequencyDecision: ...


class DeterministicSpeakingFrequencyPolicy(SpeakingFrequencyPolicy):
    def __init__(self, *, profile: SpeakingProfile, master_seed: int) -> None: ...


class ReactionChatController:
    def __init__(
        self,
        *,
        world: WorldState,
        invoker: BrainInvocationArbiter,
        master_seed: int,
        config: ReactionChatConfig = ReactionChatConfig(),
        frequency_policy: SpeakingFrequencyPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None: ...
```

`profile` is a required read-only property returning the policy's construction-stable frozen
`SpeakingProfile`; it is part of the public structural interface, not an undocumented dependency on
the built-in policy. The controller reads it exactly once during construction and retains that
immutable value for cap-independent cooldown, evaluation-bound, and repetition behavior. A
replacement policy that supplies this property plus both declared methods is structurally accepted;
it need not subclass `DeterministicSpeakingFrequencyPolicy`.

Both policy methods are synchronous, deterministic, side-effect-free, and perform no I/O, clock read,
World read, admission, or model call. `prepare()` exclusively owns source fingerprinting and the
mechanical importance score; `evaluate()` exclusively owns threshold/draw. The controller owns hard
checks and mutable per-phase state. A missing, mutable, or wrong-typed profile is rejected during
construction without starting the controller. A wrong method return type, non-finite value,
inconsistent opportunity, or policy exception transitions a running controller to its existing
`FAILED` path without mutating frequency counters/deques or calling Brain.

`frequency_policy=None` is exact Phase 3/4 behavior: no new frequency outcome, counter, defer, or draw
affects dispatch. Phase 5 composition must explicitly inject
`DeterministicSpeakingFrequencyPolicy(profile=..., master_seed=master_seed)`. This preserves the
approved heuristic/LLM/mixed Brain decision as the final replaceable reaction decision; frequency is
only a pre-admission opportunity gate.

All probabilities are finite in `[0, 1]`; integer bounds reject `bool` (`max_trigger_evaluations` at
most 64 and repetition window at most 32). Cooldown is finite and at least
`ReactionChatConfig.minimum_accepted_chat_interval_seconds`. There is exactly one chat Brain-call cap:
the existing `ReactionChatConfig.max_chat_attempts_per_phase`, whose only valid values are the integers
`1` and `2` (`bool`, `0`, `3`, and every other out-of-range value are rejected at construction).
`SpeakingProfile` does not declare an invocation or accepted-chat cap. `committed_brain_invocations`
is the existing per-phase chat invocation count, and accepted chats are necessarily less than or
equal to it. Phase 5 does not increase model calls.

The built-in `prepare()` scorer is deliberately narrow:

- initial opportunity: `initial_event_importance`;
- peer chat containing the exact current player's non-empty display name or player ID as a Unicode
  substring: `direct_mention_importance`;
- every other peer chat: `ordinary_event_importance`.

It does not classify truth, question/answer quality, sentiment, suspicion, CO meaning, or strategic
importance. Untrusted message text only affects a bounded frequency score; it never changes prompt
authority or action legality. Future semantic importance belongs to Phase 6 behind the same interface.

For a peer-chat trigger all source fields are exact non-empty `ChatRecord` values. Initial chat has
all four source fields null. Direct mention is an exact Unicode substring match of the current
non-empty display name or player ID; there is no normalization or case folding.

### Ordered controller transition

At a pending chat's due time the controller performs exactly this order; the first failing condition
is the recorded suppression and later steps do not execute:

1. Existing lifecycle/current phase, mapping, received handle, history/transport-gap, rejection,
   deadline/cutoff, and minimum-start-budget checks.
2. `ReactionChatConfig.max_chat_attempts_per_phase` against committed Brain invocations;
   failure is `INVOCATION_CAP`.
3. `SpeakingProfile.max_trigger_evaluations_per_phase` against `evaluation_count`; failure is
   `EVALUATION_CAP`.
4. Call `prepare()` once. If its non-null fingerprint is already in the per-phase deque, fail as
   `REPETITION`.
5. Query the current channel's newest accepted World chat. If it is the current player's chat and no
   later peer chat exists, fail as `SELF_CHAIN`.
6. Compare server-observed `last_accepted_chat_at + profile.cooldown_seconds` with `now`. On the first
   cooldown encounter, if that due time is strictly before cutoff, retain and reschedule the identical
   pending opportunity once. Deferral consumes no evaluation, draw, fingerprint, admission, or Brain
   count. If it cannot fit before cutoff, or the one already-deferred opportunity is still in cooldown,
   fail as `COOLDOWN`.
7. Call `evaluate()` once. It returns importance unchanged from `prepare`, threshold exactly
   `talkativeness * event_importance`, the deterministic draw below, and either `None` or
   `PROBABILITY`. After a valid return, increment `evaluation_count` and append the non-null source
   fingerprint exactly once, whether the decision invokes or probability-suppresses. Invoke iff
   `draw < threshold`.

Latest-one reaction coalescing happens before this transition: replacing an unevaluated pending peer
trigger mutates no policy state. Once step 7 returns invoke, its opportunity, prepared score, draw, and
fingerprint are frozen through `WAITING_ADMISSION`, same-client reservation replacement, and later
reaction requeue; newer peer chat cannot replace or reevaluate it. Such a newer record may become the
single latest pending trigger only after the frozen opportunity terminals. `on_brain_start` increments
`committed_brain_invocations` exactly once; no earlier state does. Server-correlated `ACCEPTED` alone
updates existing accepted count and `last_accepted_chat_at`; rejection, unknown delivery, and model
failure do not. A new `ReactionPhaseKey` atomically resets evaluation count, committed per-phase
invocations, cooldown-deferred flag, last-accepted time, and the fingerprint deque. CO opportunities
retain their separate one-per-generation path and never enter this policy.

If step 7 produced a valid decision but any subsequent cutoff/remaining-time or current-context check
fails before `BrainInvocationArbiter.invoke`, the terminal is the existing `DEADLINE_SUPPRESSED`, not
`FREQUENCY_SUPPRESSED`. It carries the complete frozen evaluation ordinal, importance, threshold,
draw, fingerprint, and suppression-null evidence already produced at step 7. The evaluation and
fingerprint remain consumed; they are never rolled back. Admission, Brain, send, and Brain-start
counter commit are all zero. Deadline failure before step 7 continues to leave fields that were never
computed null.

`ReactionOutcomeStatus` adds `FREQUENCY_SUPPRESSED`. `ReactionOutcome` adds optional immutable
frequency evidence: evaluation ordinal, importance, threshold, draw, fingerprint, and exact
`FrequencySuppression`; hard suppressions leave fields that were not computed as null. The snapshot
adds `SpeakingFrequencyState`. A frequency-suppressed opportunity makes zero
Brain/admission/backend calls and is not mislabeled `NO_DECISION`.

### P5-D/P5-C callback integration boundary

The callback in the public `BrainInvocationArbiter.invoke` signature in §3 is the sole permission to
commit either chat Brain-invocation counter. `ReactionChatController` never calls its commit callback
itself and has no callback-absent pre-commit fallback. In a frequency-enabled invocation it passes a
non-null callback to the arbiter; only the arbiter may invoke it at the approved post-claim,
immediately-pre-`BrainController.decide_and_send` boundary.

P5-D may land its policy and controller mechanics before P5-C, but the current P5-A arbiter lacks the
named `on_brain_start` parameter. Therefore controller construction with `frequency_policy is not
None` must inspect the bound `BrainInvocationArbiter.invoke` signature before storing controller
state and require an explicitly named keyword-capable `on_brain_start` parameter. `**kwargs` alone is
not proof of the contract. If absent, construction raises exactly
`RuntimeError("frequency_policy requires BrainInvocationArbiter.on_brain_start")`; it does not call
`profile`, `prepare`, `evaluate`, admission, or Brain, and creates no task or frequency/counter state.
P5-C adds the named optional callback exactly as specified in §3. After that dependency exists, the
same constructor accepts frequency-enabled composition and always supplies a callable.

`frequency_policy=None` is an explicit compatibility branch: it does not read a policy profile,
inspect callback support, create frequency state, pass `on_brain_start`, or alter the existing P5-A
`invoke` call. Thus the Phase 3/4 path remains byte-for-behavior compatible before and after P5-C.
A P5-D-only callback-capable test double may prove controller policy mechanics, but it is not evidence
that the production arbiter obeys the commit boundary.

P5-C owns the production integration proof using the actual `BrainInvocationArbiter` implementation:
a request queued behind an active owner and then cancelled, replaced before claim, rejected after
`stop()`, or made stale by phase/handle/deadline-mapping change before claim commits zero phase and
snapshot chat Brain invocations. The granted/current vector asserts the callback is called exactly
once after claim and immediately before the first `BrainController.decide_and_send`, so it commits
exactly one even if the backend later performs the one permitted schema-repair call. Offer alone,
claim failure, cancellation, and a non-starting deadline result never commit. These assertions belong
in `tests/test_phase5_brain_admission.py`; P5-D tests must not replace them with subclass-only proof.

### Exact hashing and public vectors

The exact domain separator is one NUL byte with numeric value zero. Normatively:

```python
SOURCE_DOMAIN = b"aiwolf.phase5.source.v1" + bytes((0x00,))
DRAW_DOMAIN = b"aiwolf.phase5.speaking.v1" + bytes((0x00,))
NULL_FIELD = bytes((0x00,))
NON_NULL_TAG = bytes((0x01,))

def frame(data: bytes | None) -> bytes:
    if data is None:
        return NULL_FIELD
    return NON_NULL_TAG + len(data).to_bytes(4, "big") + data
```

The separator is not the four ASCII characters backslash, `x`, `0`, `0`, and no additional
separator follows it. Text field data is exact UTF-8. Integer field data is canonical base-10 ASCII
(`0`, otherwise optional `-` followed by digits, with no leading zero). No delimiter, normalization,
case folding, Python `hash()`, JSON serialization, wall clock, process ID, model result, or arrival
scheduling enters either hash.

The peer source fingerprint is lowercase hex SHA-256 over `SOURCE_DOMAIN`, followed in order by
framed `source_player_id`, `source_channel`, and `source_message`. Initial chat uses null rather than a
fingerprint. The draw digest is SHA-256 over `DRAW_DOMAIN`, followed in order by framed `master_seed`,
`self_player_id`, `connection_generation`, `day`, `phase`, `action_generation`,
`trigger_kind.value`, `source_order`, `source_message_sha256` (null or lowercase ASCII), and
`attempt_ordinal`, with every item individually framed. `attempt_ordinal` is the committed
Brain-invocation count before this opportunity.
The draw is `(int.from_bytes(digest[0:7], "big") >> 3) / 2**53`.

The required cross-process public vectors use seed `7`, player `p2`, phase key `(1, 1, "day", 3)`,
and attempt `0`:

| Trigger/source | Source fingerprint | Draw digest | Draw | Decision at talkativeness 0.5 |
|---|---|---|---:|---|
| initial / all source fields null | null | `2d91ed4fae8d9fdf86efa5a46704d021c3a01d7b4a5abf6b370a3279035ba0ed` | `0.17800791926725024` | invoke |
| reaction / order 42, `p7`, `public`, `hello p2` | `b954070ef25497d406ba932f9c94f55a7b31a60b9f367965b4bdb9fd36243c9b` | `31165349e182795a8c902239a93043a2f55f6bc7556b1711a97f97d65c7214d7` | `0.19174690774662817` | invoke as direct mention |

Tests must construct these through the public policy/controller APIs and assert the exact prepared
values, digest-derived draw, decision/reason, outcome evidence, and final `SpeakingFrequencyState` in
two spawned Python processes as well as in-process. Each vector uses a fresh controller because both
decisions invoke. Immediately after evaluation and before claim, each has phase key
`(1, 1, "day", 3)`, evaluation count `1`, committed Brain count `0`, and no accepted-chat time; the
initial deque is empty and the reaction deque contains only its published source fingerprint. A
successful claim then changes only that controller's committed Brain count to `1`.

The default profile is a reversible client operational default, not a server rule. Completion and
Q8 commands state every value explicitly. Changing a profile never changes a responsibility, model,
role, or task contract.

## 7. Short-Chat Policy

The canonical 5–30 token range is a generation target, not a portable exact validator: tokenizer
boundaries differ by model and JSON syntax consumes output tokens too. Phase 5 therefore uses all of
the following without claiming that characters equal model tokens:

```python
@dataclass(frozen=True)
class ShortChatConfig:
    target_min_text_tokens: Literal[5] = 5
    target_max_text_tokens: Literal[30] = 30
    max_text_chars: int = 80
    max_text_utf8_bytes: int = 96
    max_output_tokens: int = 96
```

- The system instruction says one short utterance, target 5–30 model tokens, and includes the exact
  character/UTF-8 bounds.
- The request-local JSON schema sets `minLength: 1`, `maxLength: 80` on `ChatDecision.message` and
  `CoDeclareDecision.comment`. Five tokens remains guidance because shorter valid interjections exist.
- The semantic parser independently requires non-empty text, at most 80 Unicode code points, and at
  most 96 UTF-8 bytes. Failure is `TEXT_BOUND`; no prefix is substituted.
- `GenerationSettings.max_output_tokens=96` applies to the complete provider JSON response, not only
  message text. Its extra budget above the 30-token text target is reserved for discriminator,
  property names, escaping, and structural syntax. It is not reported as a 96-token speech allowance.
- Provider `finish_reason` indicating a length limit or incomplete JSON fails strict parsing and may
  use only the existing single schema-repair attempt while the same lease/deadline remains valid.
- Audit stores the exact accepted response and decision under existing record bounds. It never stores
  an invented truncation.

The char/byte caps are deterministic tokenizer-independent safety guards, not an exact model-token
counter. Q8 records provider completion tokens and emitted text chars/bytes so the relationship is
measured on the configured model. A future model change may alter the explicit profile after review;
it never silently relaxes validation.

## 8. Audit and Metrics Ownership

Each client has one private shard:

```text
logs/<game-id>/ai/manifest.json
logs/<game-id>/ai/<opaque-client-id>/ai.jsonl
logs/<game-id>/ai/admission.jsonl
```

The directory and files receive the same private-permission treatment as existing credentials/audit
artifacts. One `JsonlAiAuditSink` writes one shard; no file has multiple live writers. Existing Phase
4 record validation, durability-before-decision, queue bound, secret exclusions, and close semantics
remain unchanged. After every shard and broker are closed, the launcher writes `manifest.json`
atomically with player/opaque-ID mapping, path, byte count, SHA-256, record count, and terminal status.
The manifest is private and contains no token, prompt, or response. A combined historical stream may
be reconstructed after close by ordering `(recorded_at_utc, opaque_client_id, request_id,
attempt_ordinal)`; live shared append is rejected.

The broker's single metadata writer records bounded entries with opaque client ID, invocation ID,
priority, terminal status, enqueue/offer/claim/finish monotonic microseconds relative to broker start,
queue/generation latency, request/response byte counts, provider token/timing counts when present,
error code, code-valid `http_status`, `retryable`, and `provider_quiescence`. It never records full
prompt/schema/response, raw exception text/repr or stack, URL/header/body, model credential,
admission token, game entry/connection token, or player mapping.

## 9. Deterministic Offline Tests

All mandatory CI tests use no GPU, external network, model process, or `C:\AIagent`.

### Contract and broker unit tests

- frozen value/type/range/secret-repr validation and exact framed JSON round trips
- exact T040 HTTP vector reconstructs `HTTP_STATUS/503/True/PROVEN_TERMINAL`; parameterize status
  boundaries 100/599, both retryable values, and all quiescence values without attribute loss
- exact T040 non-HTTP vector reconstructs `REQUEST_TIMEOUT/None/True/UNKNOWN`; parameterize every
  other `LLMBackendErrorCode`, both retryable values, and all quiescence values with `http_status`
  absent, including an encode/decode/re-encode field-equality round trip
- malformed ERROR vectors cover missing/null/bool/99/600 HTTP status, any present HTTP status on a
  non-HTTP code, missing/null/numeric retryable, missing/unknown quiescence/code, wrong
  invocation/ordinal, duplicate/extra key, free-form error text, and frame overrun; each closes only
  that session, reconstructs no partial/original error, and returns the exact local
  `ADMISSION_PROTOCOL/None/False/UNKNOWN`
- install a send barrier for the non-HTTP `UNKNOWN` vector and assert broker `poisoned=True`, queued
  and attached work terminal `POISONED`, and the poison metric enqueued before the client observes
  `ERROR`; failed delivery leaves poison set and no successor offer
- seed the source exception/message/body/header/prompt with sentinel secrets and prove none occur in
  the ERROR frame, broker metadata, client audit error fields, repr, or status output
- frame overrun, duplicate keys, malformed UTF-8/JSON, unknown/extra fields, wrong protocol version
- authentication success; wrong/replayed token, duplicate connection, cross-client invocation/result
  access, and indistinguishable authentication rejection
- one pending per client, total capacity, overload fail-closed, no eviction/retry/direct fallback
- exact reservation-before-reaction grant, non-preemption, seeded round-robin vectors, eight-grant
  same-class fairness bound, seed-separated first grant
- expiry before enqueue/offer, equality expiry, active deadline abandonment, mapping cancellation,
  cancel/offer/claim races, connection loss, broker failure, idempotent release/close
- public ordinary `cancel(invocation_id)` table-vectors for `ENQUEUED`, `OFFERED`, `CLAIMED`, every
  retained terminal, offer/claim/expiry/replace races, outer cancellation, missing acknowledgement,
  foreign/successor ID rejection, and exact slot/ack/result-future cleanup; successor cancel remains
  separately covered
- public session-level same-invocation `claim()`/`cancel()` races with deterministic lane barriers:
  force cancel-first and assert both return `CANCELLED` with impossible activation; force claim-first
  and assert both return `GRANTED` with the lease still non-preemptively claimed. In both, assert one
  acknowledgement future at a time, no second loser frame, no unowned ACK, no session close, and exact
  lane/result/lease cleanup after the owner terminals
- cancel each public race waiter both before lane admission and after its frame is sent; assert the
  sent operation retains/consumes its ACK, a cancelled granted claim performs serialized ABANDON, and
  missing ACK/session close resolves boundedly without a live waiter or slot. Inject late/duplicate,
  conflicting, and wrong-ID ACKs separately and assert fail-closed session cleanup, not cross-waiter
  delivery
- replace-before-claim: with client X's backend active, client A's reaction waiting, B/C reactions
  waiting, and a later A reservation, A's one slot atomically becomes the reservation, that
  reservation is the next claim,
  the old reaction makes no Brain/backend/send, and its still-current frozen opportunity is requeued
  only after the reservation; assert one A Brain instance, one frequency evaluation, one eventual
  reaction Brain-start count, and one reservation attempt
- claim-first through the public API: claim A's reaction, call `reserve_successor()` for A's later
  reservation, assert its returned handle owns one still-pending `wait_offer()`, queue another client's
  reaction, safely release A's active lease, receive `AdmissionResult(OFFERED)` with a distinct
  successor-ID lease, then claim/activate it and deliver the final `BrainDispatchResult` only to the
  original reservation caller before any reaction offer; separately force deadline, caller-cancel,
  cancel/release/offer/claim races, poison, disconnect, and broker shutdown and assert the handle's
  exact one-time status and zero leaked caller/future/lease
- scripted slow backend proves peak active `generate()` count is exactly one across nine processes;
  every backend call belongs to the currently granted lease and result routes only to its owner
- first invalid/second valid repair stays one lease and exactly two calls; third call is rejected
- a simulated provider remains active after caller cancellation acknowledgement: broker stays
  `DRAINING` and offers no successor until a natural complete response is drained; the inside-grace
  variant then grants exactly one successor, while the unknown/over-grace variant becomes permanently
  `POISONED`, rejects every successor, makes the launcher exit non-zero, cleans broker-owned tasks,
  and never reports or attempts external-provider cleanup
- table-drive success, `PROVEN_TERMINAL`, and `NOT_STARTED` direct-call terminals to a safe release,
  and every `UNKNOWN`/unexpected terminal to poison before any release or offer
- with a current connected caller, ordinal 1 returns each `UNKNOWN` timeout, partial-body, read,
  write, transport-decode, and early-size-abort failure while the simulated provider remains active:
  poison is recorded before the caller receives its sanitized error, queued and attached successors
  receive no offer, shutdown is bounded/non-zero, owned tasks are inventoried, and evidence never
  claims external-provider cleanup; repeat with ordinal-2 repair failure and with shutdown racing the
  same terminal
- broker shutdown applies the same drain-or-poison rule and leaves no owned listener/child/thread
- optional provider timing parsing, malformed timing ignored, exact throughput arithmetic

### Frequency and short-chat tests

- all boundary values and explicit profile default; the sole chat cap accepts only integer `1`/`2`
  and rejects `bool`/`0`/`3`; no role/model fields or branches
- a replacement policy with the public read-only `profile` property plus `prepare/evaluate` is
  accepted structurally without subclassing the built-in policy
- exact importance for initial/direct mention/ordinary chat and no semantic classification
- the two public SHA-256 vectors in §6 through `prepare/evaluate` in-process and in two spawned
  processes, with exact fingerprint/digest draw, decision/reason, outcome evidence, and final state
- talkativeness/importance zero and one, probability suppression, the single ReactionChat invocation
  cap, and evaluation cap; accepted count never exceeds committed invocation count
- exact hard-check precedence; cooldown first-defer/no-mutation then suppress/evaluate; fingerprint
  append/evaluation mutation; phase reset; self-chain; latest-one pre-evaluation coalescing; bounded state
- waiting reaction replacement/requeue preserves its first score/draw/fingerprint and consumes one
  evaluation but no Brain count until claim; probability/hard suppression makes no admission call
- before P5-C, the actual callback-absent P5-A arbiter fails frequency-enabled controller construction
  with the exact error and no policy call/task/state/counter mutation; `frequency_policy=None` uses
  the unchanged invocation shape and passes the complete Phase 3.4 reaction suite
- a deterministic clock boundary that expires after valid evaluation records `DEADLINE_SUPPRESSED`
  with all computed frequency evidence, retains the evaluation/fingerprint mutation, and makes zero
  admission/Brain/send/counter commit
- frequency suppression produces zero Brain/admission/backend call and explicit outcome evidence
- schema/prompt/parser exact 1/80-char and 96-byte boundaries, multi-byte Unicode, JSON escaping
- 5–30 token wording present only as guidance; `max_output_tokens=96` is provider envelope bound
- over-bound response is rejected/one-repair only; no silent truncation or fallback action
- unchanged Phase 4 default config and tests remain valid when short profile is not selected

### Arbiter/controller integration tests

- `admission=None` is byte-for-behavior compatible with Phase 3.5/4 tests
- owner maps to exact priority; claim wraps one existing Brain call and at most two backend attempts
- queue/offer wait does not block Network/World version progress
- phase/handle/mapping replacement before claim cancels without LLM/audit action record
- with an actual production controller/arbiter/session/broker and a blocking backend call active,
  replace or clear the exact deadline mapping and prove the invocation returns
  `DEADLINE_SUPPRESSED` within the client cancellation bound before the backend is manually released,
  sends `ABANDON`, enters broker `DRAINING`, releases no broker slot and offers no successor during
  drain, and sends no typed action; then release the test backend and prove safe drain/cleanup, with
  a separate UNKNOWN/over-grace poison vector
- attach one still-current reservation successor before making the active Brain mapping stale. Assert
  `ABANDON -> CANCELLED`, local claimed/activated/call/future cleanup, repeated abandoned-lease release
  as a no-frame no-op, retained authenticated session, and the original `DEADLINE_SUPPRESSED` result.
  Before releasing the backend assert zero successor offers; after safe drain assert the same handle
  receives and can claim the successor lease. In the paired UNKNOWN/over-grace vector assert the handle
  resolves `POISONED`, not `UNAVAILABLE`, with no caller/future/lease leak
- barrier provider-safe-terminal-before-ABANDON and ABANDON-before-safe-terminal orders and assert both
  end `ABANDONED_DRAINED` without a delivered model/game result; separately close the session before
  ABANDON acknowledgement and assert local lease cleanup, successor `UNAVAILABLE`, broker-owned drain,
  and no overwrite of the primary controller outcome
- cutoff equality without a World update wakes the same watcher and returns `DEADLINE_SUPPRESSED`;
  a concurrently completed Brain result is discarded, while `dispatch_deadline=None` preserves the
  existing Phase 3.3 blocking-Brain timeout/stale behavior
- a runtime structural `GenerationAdmission` conformer implementing the complete declared interface,
  including ordinary `cancel`, returns an `OFFERED` lease; make the deadline mapping stale after that
  offer and before claim, then assert the exact ID is cancelled, terminal is
  `DEADLINE_SUPPRESSED`, no caller/result/ack future, slot, or lease owner remains, and Brain/send
  counts are zero; repeat with only handle staleness for `STALE`, and reject a conformer missing
  `cancel` at arbiter construction
- offer/claim/cancel and broker-disconnect races leave no unowned lease and no late typed send
- a WAITING reaction is atomically replaced by a later same-client reservation; a claimed reaction is
  not preempted and exposes one attached reservation as the next global successor
- using the actual production arbiter after P5-C, queued-then-cancelled, replaced-before-claim,
  stopped, and phase/handle/deadline-stale reaction vectors each leave both chat Brain-invocation
  counters at zero; the granted/current vector observes exactly one callback after claim and before
  the first Brain call, including the two-backend-call repair variant
- server acceptance correlation, delivery unknown, NOT_DELIVERED retry, and reservation re-arm contracts
  pass unchanged

### Required offline nine-LLM completion

`tests/test_phase5_completion.py` is marked `completion`. It starts one real game server process, one
real generation broker process with a deterministic test-only `StructuredLLMBackend`, and nine
independent client processes. Every client uses production Network, World, one production `LLMBrain`,
BrainController, local arbiter with shared admission, Reaction, Vote/Ability, short-chat and frequency
policy. No client uses `DeterministicVoteAbilityBrain` or a direct/fake Brain. The fake exists only
behind the broker backend and returns schema-valid request-local decisions.

The completion node asserts:

- exactly nine distinct non-parent client PIDs, one broker PID, one server PID, and no model PID;
- each client authenticates, is configured as LLM mode, and completes at least one brokered
  structured generation;
- global peak backend concurrency exactly one, queue capacity never exceeded, and every lease/call is
  paired and terminal;
- all selected options/targets come from the received request, all accepted reservations precede the
  authoritative deadline, and accepted/rejected correlation remains exact;
- at least one server-authoritatively accepted short chat from every Day-1 living seat, exact
  character/byte bounds, and frequency/cooldown/cap evidence;
- the existing expected vote/runoff/ability cardinality and game-end evidence for all nine seats;
- nine durable audit shards, matching manifest hashes/counts, one metadata log, no cross-player
  response routing, and sentinel secret scan;
- success/failure/timeout cleanup of server, broker, clients, sockets, writer/executor tasks, and all
  recorded child PIDs.

It reuses test-local immutable phase-duration overrides but does not weaken Phase 3.5 assertions.
Node budget is 180 seconds, readiness steps are 15 seconds each, and no retry/xfail/skip converts a
requested run to success.

## 10. Finite Real Smoke and Q8 Measurement

### One nine-real-client completion smoke

`scripts/run_phase5_local_smoke.py` never starts/stops the model. With an already-running game profile,
it starts one game server, one broker, and nine clients, all using production brokered `LLMBrain`.
It explicitly uses `standard_9` plus test-run-only `day_seconds=60`, `vote_seconds=45`,
`night_seconds=45`, and `silence_after_dawn_seconds=0`; content files are unchanged. It supplies the
frequency profile explicitly with talkativeness `1.0`, initial importance `1.0`, direct mention `1.0`,
ordinary importance `0.5`, cooldown `0.20`, evaluation cap `32`, repetition window `8`, and the
separate existing `ReactionChatConfig.max_chat_attempts_per_phase=2`. The hard scenario timeout is 1200
seconds.

Success requires every client to make at least one real structured call, all nine to reach game end,
global peak generation concurrency exactly one, all broker entries terminal, Day-1 accepted short
chat from every living seat, exact expected accepted vote/ability reservations before deadline, zero
fabricated handles, durable sharded audit/manifest/metadata evidence, zero non-empty child stderr, and
zero owned processes/listeners after cleanup. Model failure, missing settings, broker failure,
deadline violation, invalid response, audit failure, missing required action, or owned orphan is a
non-zero exit. The external model process/listener is checked as unowned before/after and not killed.

### Q8 matrix

The opt-in `--q8` mode runs this finite matrix on the target 8GB host and writes raw JSONL plus one
machine-readable summary. It has no effect on production defaults or game rules.

| Row | Workload | Requests / bound | Purpose |
|---|---|---:|---|
| Q8-A | sequential short structured generations at three retained prompt-size buckets | 3 buckets x 3 = 9 | prompt/generation throughput baseline |
| Q8-B | nine simultaneous reaction tickets, one per authenticated client | 9, 120s | burst queue latency and round-robin |
| Q8-C | one active reaction, then four reservation and four reaction tickets | 9, 120s | non-preemption and next-grant reservation priority |
| Q8-D | the nine-real-client complete game described above | 1 game, 1200s | actual suppression, speech, deadlines, game duration |

Prompt buckets are deterministic retained projections from the offline fixture: minimum valid,
median retained history, and maximum configured retained history; they contain synthetic data only
and still pass the production prompt bound. Each real request retains the same strict short schema.
No unbounded warm-up exists; Q8-A's first request is separately labeled cold and the remaining values
are warm measurements.

The summary reports per row and overall:

- enqueue-to-offer and enqueue-to-claim queue latency min/median/p95/max, by priority and opaque client;
- end-to-end request latency and provider prompt/completion tokens;
- provider prompt tokens/second and generation tokens/second from complete `ProviderTiming`;
- admission expiry, pre-model stale cancellation, active cancellation, overload, backend failure,
  deadline miss, and speaking-frequency suppression counts/reasons;
- accepted speech count by day and seat, chars/UTF-8 bytes, inter-accepted interval, and calls per
  accepted speech;
- active/pending queue high-water mark and peak backend concurrency;
- per-phase and total game wall duration and terminal process status;
- audit/metrics/manifest hashes and cleanup inventory;
- when `--gpu-model-pid` is supplied and exact PID sampling is available, `nvidia-smi` start/peak/end
  used MiB at 250ms sampling. Missing command/PID support is recorded as `UNAVAILABLE`, never guessed
  from total system memory and never used to kill the model.

The Q8 measurement run requires complete provider timing for separate prompt/generation throughput.
If the provider omits it, Q8 exits non-zero with `PROVIDER_TIMING_UNAVAILABLE`; the ordinary real
completion result remains independently usable. Percentiles use nearest-rank over raw integer
microseconds and the summary records sample counts, config fingerprint, frequency profile, model
identity, Python/OS, scenario seed, and command arguments excluding secrets.

### Engineering acceptance versus product choice

The following are engineering gates, not gameplay preference decisions:

- peak backend concurrency is exactly one;
- zero unauthorized/cross-client result, stale dispatch, deadline-late accepted reservation, orphan,
  or audit/manifest mismatch;
- Q8-B/Q8-C terminate without overload and with the specified priority/order;
- Q8-D finishes within 1200 seconds with all nine clients and reports every required metric.

Measured latency cannot determine whether users prefer a 60-, 180-, or 360-second discussion, nor
whether human clients should be subject to a new server-side speech limit. ROADMAP requires Q8 to be
measured, not that this Architect invent those product rules. Therefore Q8 remains a post-measurement
product decision with these exact alternatives to retain in `OPEN_QUESTIONS.md`:

1. **Current rules:** keep preset `day_seconds=180`, existing shortening/extension, no new server chat
   cap; AI clients keep two Brain invocations and therefore at most two accepted chats.
2. **Tempo change:** choose `day_seconds=60`, `180`, or `360` after reviewing Q8 queue p95/max,
   accepted speech/seat/day, and full-game duration; shortening/extension remain unchanged.
3. **General server cap:** add a content-defined, client-type-neutral per-player/per-day accepted-chat
   cap in a new Design Gate. This affects humans and every client and therefore cannot be smuggled in
   as an AI-only network rule.

No choice is required before implementing the broker, policy, tests, smoke, and Q8 measurement. Q8
must not be marked resolved merely because the engineering run passes; after evidence exists, the
Integrator records the measured recommendation and routes the material gameplay choice to the user.

## Acceptance Mapping

| Required item | Interface/evidence |
|---|---|
| shared LLM server | one external endpoint and one broker-owned direct backend |
| multiple agents | nine independent production AI client processes |
| generation queue | bounded authenticated broker, one active lease, nine total entries |
| frequency adjustment | injected policy, deterministic score/draw/cooldown, existing single cap |
| short chat | 5–30 token guidance plus strict 80-char/96-byte/96-output-token boundaries |
| nine-AI completion | offline nine-LLM completion and opt-in real nine-LLM game |
| Q8 measurement | finite Q8-A through Q8-D metrics and optional exact-PID VRAM sampling |

Implementation is accepted only when all of the following hold.

1. Server/game/content/protocol have no Phase 5 change or AI import.
2. Nine clients use one Brain each and one broker-owned backend; direct client HTTP is absent.
3. Queue/prompt/result/auth data cannot cross client sessions and secrets are absent from evidence.
4. One active backend call is structurally enforced, including cancellation/failure races.
5. Queue and all retained state have the exact finite bounds above; overload is fail-closed.
6. Reservation priority, non-preemption, and seeded round-robin fairness pass exact vectors.
7. Expired/cancelled/stale work cannot start a new model call or send a late result; already-started
   provider work drains before a successor or permanently poisons admission.
8. Existing Phase 3 authority, handles, deadlines, correlation, retry, and arbiter semantics remain.
9. Frequency uses no role/model branch and records every allow/suppression deterministically.
10. Short chat is bounded at prompt/schema/parser/provider layers and never silently truncated.
11. Nine audit shards and metadata evidence are durable/private and have no concurrent file writer.
12. Mandatory tests are model/GPU/network independent and the offline nine-client completion passes.
13. The real smoke is finite, opt-in, all-nine-LLM, server-authoritatively verified, and leaves zero
    owned processes while never owning the model.
14. Q8 outputs every required metric or fails explicitly where provider timing is unavailable.
15. Full Phase 3.1–3.5 and Phase 4 regressions, completion nodes, full pytest, compile, docs, and diff
    checks pass before Phase 5 closure.

## Implementation Packets and Conflict Boundaries

After independent design approval, the Integrator creates these dependency-ordered packets. Board,
current state, request status, and final phase closure remain Integrator-owned.

1. **P5-A — short-output, timing, and quiescence contracts.** Owns `ai_client/llm/types.py`,
   `ai_client/llm/backend.py`, `ai_client/llm/prompt.py`, `ai_client/llm/decision.py`, the affected
   existing Phase 4 focused tests, and new `tests/test_phase5_short_chat.py`. It adds the optional
   timing value, provider-quiescence evidence, and explicit short profile, with no
   broker/controller/composition change. Focused: Phase 4 backend/Brain plus short bounds and exact
   pre-send/complete/unknown transport classification. Regression: all Phase 4 LLM tests.
2. **P5-B — admission broker and proxy.** Depends on P5-A. Owns new
   `ai_client/llm/admission_types.py`, `admission_client.py`, `admission_broker.py`,
   `admission_metrics.py`, and `tests/test_phase5_generation_admission.py`. Tests import modules
   directly; package exports are not edited. Focused: IPC/auth/queue/fairness/deadline/cancel/privacy/
   metrics/one-concurrency, one-per-invocation control-lane/ACK ownership, post-ABANDON local lease
   retirement, public ordinary-entry cancel and its structural/race cleanup, waiting replacement,
   attached-successor survival, and drain-or-poison. Regression: Phase 4 backend/audit/Brain.
3. **P5-C — Brain admission and stale cancellation.** Depends on P5-B; its cross-packet frequency
   integration acceptance also depends on the P5-D interface/repair. Owns
   `ai_client/brain/invocation.py`, `ai_client/brain/controller.py`,
   `tests/test_phase3_3_brain_interface.py`, `tests/test_phase3_5_vote_ability_controller.py`, and new
   `tests/test_phase5_brain_admission.py`. Focused: no-admission compatibility, priority/lease, World
   progress, local WAITING/ACTIVE replacement, named `on_brain_start` callback integration, actual
   production queued/cancelled/replaced/stopped/stale zero-commit vectors, granted exactly-once commit,
   active deadline-watcher cancellation/ABANDON evidence, structural-conformer stale-after-offer
   cleanup, and mapping/cancel races. Regression: Phase 3.3–3.5 controller suites.
4. **P5-D — speaking frequency.** May run in parallel with P5-B/P5-C after interface review. Owns new
   `ai_client/reaction_chat/frequency.py`, existing `ai_client/reaction_chat/types.py` and
   `controller.py`, existing reaction tests, and new `tests/test_phase5_speaking_frequency.py`.
   Focused: complete structural protocol injection, public hash vectors, ordered state mutations,
   cooldown/repetition/chain/single-cap/evidence, post-evaluation deadline evidence, and the exact
   callback-absent pre-P5-C construction failure. It may use a callback-capable test double only for
   policy mechanics; production arbiter zero/once proof remains P5-C-owned. Regression: complete Phase
   3.4 reaction suite with `frequency_policy=None`. It touches no LLM/admission/Brain file.
5. **P5-E — production composition and offline completion.** Depends on P5-A through P5-D. Owns new
   `ai_client/runtime.py`, all public exports in `ai_client/__init__.py`, `ai_client/brain/__init__.py`,
   `ai_client/llm/__init__.py`, and `ai_client/reaction_chat/__init__.py`; new Phase 5 deterministic
   broker/client fixtures, `tests/test_phase5_completion.py`, and only the new completion registration
   in `tests/conftest.py`. Focused: one broker/nine LLM clients/audit shards/game end/cleanup.
   Regression: Phase 3.4, Phase 3.5, and Phase 4 completion nodes.
6. **P5-F — real smoke and Q8 evidence.** Depends on approved P5-E. Owns
   `scripts/run_phase5_local_smoke.py`, its CLI/unit tests, and retained run-evidence documentation.
   It makes no production/game/config change. Focused: bounded runner failure/cleanup/secret-redaction,
   matrix aggregation, percentile/timing/GPU-unavailable behavior. Then an independent Tester runs the
   one real completion and Q8 matrix, followed by full regression and independent closure review.

No packet edits `server/`, `protocol/`, `content/`, `TASKS.md`, `CURRENT_STATE.md`, or
`OPEN_QUESTIONS.md`. A discovered need for those files returns to the Integrator and a new Design Gate.
The shared `__init__.py` files and completion marker are deliberately assigned only to P5-E.

## Rejected Alternatives

- **Nine direct HTTP clients with no global gate:** cannot bound aggregate concurrency, fairness, or
  queue cancellation.
- **A queue inside the game server:** introduces an AI/model dependency into the source of game truth.
- **One in-process queue per client:** preserves nine independent queues and does not solve host
  admission.
- **A permit-only broker while HTTP remains in clients:** a crashed/hung lease holder can continue an
  HTTP call after the broker grants a successor, so peak concurrency and zero-orphan are not structural.
- **One shared Brain process:** merges private World/strategy ownership across players and bypasses
  the established per-client Brain/handle boundary.
- **Two Brains per client or a deterministic vote decorator around LLMBrain:** duplicates ownership or
  prevents reservation decisions from reaching the LLM.
- **Prompt/schema inspection to infer queue priority:** couples scheduling to untrusted/private data;
  the existing arbiter owner already supplies a model-neutral class.
- **FIFO/global arrival order:** makes process scheduling a persistent fairness bias. Fixed seat order
  has the same problem.
- **Preempt an active reaction for a reservation:** may abandon an in-flight provider operation and
  contradicts the approved non-preemptive arbiter contract.
- **Unbounded queue, retries, or result cache:** creates stale work and ambiguous duplicate actions.
- **One concurrently appended `ai.jsonl`:** Phase 4 offers no inter-process writer serialization;
  per-player shards retain durability and privacy without file corruption.
- **Tokenizer-specific hard 30-token validation:** couples game behavior to one model/tokenizer and
  still ignores JSON envelope overhead.
- **Silently truncate long model text:** changes the model's action after validation and makes audit
  differ from the sent decision.
- **Invoke every agent for every message and let it return `none`:** still spends all inference and
  violates the canonical prohibition; frequency gating happens before admission.
- **Use role, team, model, or provider to tune talkativeness/priority:** violates role/model separation
  and makes game content require Python changes.
- **Treat a passing benchmark as the discussion-duration decision:** latency cannot select a user
  preference or authorize a new server rule.

## Decision Status

`T035_R2_BASELINE_APPROVED: YES`

Independent review evidence:
`Docs/ai/handoffs/tasks/T035_PHASE5_DESIGN_R2_REVIEW.md`

`T040_ERROR_ENVELOPE_ADDENDUM_READY_FOR_REVIEW: YES`

`T040_ERROR_ENVELOPE_ADDENDUM_APPROVED: YES`

`P5_B_ERROR_ENVELOPE_IMPLEMENTATION_BLOCKED_PENDING_T040_REVIEW: NO`

`T041_FREQUENCY_INTEGRATION_ADDENDUM_READY_FOR_REVIEW: YES`

`T041_FREQUENCY_INTEGRATION_ADDENDUM_APPROVED: YES`

`P5_D_FREQUENCY_REPAIR_BLOCKED_PENDING_T041_REVIEW: NO`

`T046_ACTIVE_STALE_CANCEL_ADDENDUM_READY_FOR_REVIEW: YES`

`T046_ACTIVE_STALE_CANCEL_ADDENDUM_APPROVED: YES — completed by approved T047 R1`

`P5_C_REPAIR_BLOCKED_PENDING_T046_REVIEW: NO`

`T047_CANCELLATION_LIFECYCLE_R1_READY_FOR_REVIEW: YES`

`T047_CANCELLATION_LIFECYCLE_R1_APPROVED: YES`

`P5_C_REPAIR_BLOCKED_PENDING_T047_REVIEW: NO`

`DECISION_REQUIRED_BEFORE_IMPLEMENTATION: NO`

`Q8_PRODUCT_DECISION_AFTER_MEASUREMENT: YES` — choose among the exact duration/server-cap alternatives
in §10 after the finite measurements exist. This does not block Phase 5 implementation or the Q8 run.
