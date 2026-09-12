Status: APPROVED — independent re-review 2026-09-10

Review evidence: `Docs/ai/handoffs/tasks/T006_PHASE3_4_SERVER_RECEIPT_DEADLINE_DESIGN_REREVIEW.md`

# Phase 3.4 server receipt-time deadline design

Task: `T006`

Request: `Docs/ai/design/PHASE3_4_SERVER_RECEIPT_DEADLINE_REQUEST.md`

## Decision Summary

For each authenticated gameplay request, `SessionManager` samples one integer server
receipt time after envelope/authentication validation and before core dispatch.  Chat,
CO declaration, and CO report pass that exact value into the game core.  The core owns
the deadline decision and rejects a valid communication request when
`received_at >= phase_ends_at`.

The existing `WebSocketGameServer._dispatch_lock` remains the serialization boundary
between request dispatch and `TickDriver`.  The network does not advance phases and does
not decide whether a communication is legal.  A successful chat carries an immutable
internal acceptance record through `SessionResult` to `queue_channel_message`; the record
contains the exact receipt time and deadline snapshot used by authorization.  Completion
evidence consumes that record rather than sampling the clock or mutable game state again.

No wire payload, content, rule, phase duration, or client API changes.

## Scope and Non-Scope

This design changes only the server/core boundary for `chat.send`, `co.declare`, and
`co.report`, plus internal accepted-chat delivery metadata and the tests needed to prove
the contract.  It also makes the already-existing ability dispatch use the same request
sample passed into `_dispatch_game_action`, so the refactor cannot accidentally introduce
a second action timestamp.

It does not change voting semantics, action enumeration, phase duration, Reaction Chat
scheduling, Brain behavior, protocol schemas, public event payloads, client deadline
mapping, or Phase 3.5.  It does not make the network layer a rule engine and does not use
test retries, assertion filtering, or relaxed deadline comparisons.

## Ownership

| Concern | Owner | Contract |
|---|---|---|
| Integer server clock | `SessionManager` | Sample once per authenticated gameplay dispatch and normalize with `timestamp()` |
| Request/tick ordering | `WebSocketGameServer` | Keep request handling and `TickDriver.advance_once()` mutually exclusive under `_dispatch_lock` |
| Phase/deadline legality | `PlayerInteractions` in the game core | Validate the supplied receipt time against the current authoritative phase window |
| Phase mutation | `PhaseManager` through `TickDriver` | Unchanged; request dispatch does not advance a phase |
| Chat history | `GameState` / `PlayerViews` | Record only after core acceptance, unchanged wire-visible content |
| Recipient selection and delivery | `EventDeliveryRouter` | Continue to filter recipients; carry but never interpret internal acceptance metadata |
| Completion evidence | Phase 3.4 completion fixture | Read immutable acceptance metadata at the existing post-authorization queue spy point |

`phase_ends_at`, `phase_started_at`, current phase, and current day remain owned by
`GameState`.  A client-supplied timestamp is never accepted.

## Public and Internal Interfaces

### Core acceptance value

`server.aiwolf_core.interactions` defines and `server.aiwolf_core` re-exports:

```python
@dataclass(frozen=True)
class InteractionAcceptance:
    action: str
    player_id: str
    day: int
    phase: str
    accepted_at: int
    phase_deadline: int

@dataclass(frozen=True)
class ChatSubmission:
    channel_id: str
    message: dict[str, str]
    acceptance: InteractionAcceptance
```

`action` is exactly one of `chat.send`, `co.declare`, or `co.report`.  Constructors are
internal; production code does not accept an arbitrary client value for this field.
`accepted_at` is the normalized receipt time.  `phase_deadline`, `day`, and `phase` are
snapshots taken from the same core state against which legality was checked.

The stable game-core entry points become:

```python
GameState.submit_chat(
    now: int, player_id: str, channel_id: str, message: str
) -> ChatSubmission

GameState.declare_co(
    now: int, player_id: str, claimed_role_id: str, comment: str
) -> InteractionAcceptance

GameState.report_co(
    now: int,
    player_id: str,
    kind: str,
    target_player_id: str,
    claimed_result: str,
) -> InteractionAcceptance
```

`PlayerInteractions` exposes the same three signatures.  `now` is first, matching
`GameState.submit_action(now, ...)`; it has no default.  All direct core callers and tests
must therefore supply an explicit authoritative or controlled logical time.  Keeping the
old no-time overload is forbidden because it would retain a deadline-bypass path.

`SessionGame` mirrors these signatures.  `SessionManager._dispatch_game_action` adds a
required keyword-only `received_at: int`.  `handle_message` obtains it once with:

```python
received_at = timestamp(self._clock())
```

after authenticated envelope validation and before `_dispatch_game_action`.  The method
passes the value unchanged to chat, both CO calls, and ability submission.  Rejection reply
enveloping may sample the clock later for the reply's protocol timestamp; that envelope
timestamp is not authorization evidence and must never be fed back into core legality.

### Accepted-chat delivery metadata

`SessionResult.channel_messages` changes from anonymous `(channel_id, message)` pairs to
`tuple[ChatSubmission, ...]`.  For each submission, `WebSocketGameServer` calls:

```python
EventDeliveryRouter.queue_channel_message(
    game_id,
    submission.channel_id,
    submission.message,
    acceptance=submission.acceptance,
)
```

`queue_channel_message` adds the keyword-only parameter
`acceptance: InteractionAcceptance | None = None`.  The resulting `OutboundDelivery` adds
the same optional field.  It is internal diagnostic metadata: `_deliver` still serializes
only the existing `message_type` and `payload`, and the `chat.message` wire payload remains
byte-for-byte schema compatible.  `publish_channel_message`, which publishes an already
accepted server-originated payload, leaves `acceptance=None` and preserves its current API.

The production client-chat path must always provide a non-`None` acceptance whose action
is `chat.send`; this is asserted at the WebSocket/session integration boundary.  Delivery
code may copy/store the frozen value but may not make legality decisions from it.

CO operations keep the existing public `CO_DECLARED` and `CO_REPORTED` event payloads.
Their core return value is the non-wire acceptance evidence.  No receipt time, deadline,
role truth, or other internal field is added to client-visible protocol events.

## Core Authorization Algorithm

Each `PlayerInteractions` operation preserves its existing semantic checks and uses
`_require_available(...)` to determine whether the specific communication action is
currently offered **before** applying the receipt-time window guard.  Only an offered
action is eligible for deadline validation.  The exact operation order is:

1. Run the operation's existing non-mutating semantic validation (message/comment/report
   shape, known claim/target, and declaration quota checks) in its current order.
2. Call `_require_available` for the exact action and, for chat, the exact channel.  If it
   is not offered, preserve the current `action_unavailable` or earlier semantic reason.
3. Call `_require_open_communication_window(now)`.  This is the first point at which an
   offered action's receipt time is interpreted.
4. Only after steps 1–3 succeed, perform public-activity/history/quota/event mutation and
   construct the immutable acceptance.

The shared window helper is therefore private to the post-availability path:

```python
def _require_open_communication_window(self, now: int) -> tuple[int, int]:
    received_at = timestamp(now)
    started_at = self.game.phase_started_at
    deadline = self.game.phase_ends_at
    if started_at is None or deadline is None:
        raise RuntimeError("communication actions require an authoritative deadline")
    if received_at < started_at:
        raise ActionRejected("action_unavailable")
    if received_at >= deadline:
        raise ActionRejected("action_deadline_passed")
    return received_at, deadline
```

Calling this helper before `_require_available` is forbidden.  A missing `phase_started_at`
or `phase_ends_at` is a `RuntimeError` only because reaching the helper proves that the core
has offered a communication action without its required authoritative window.  Legitimate
SETUP, EXECUTION, and GAME_END states have no deadline and offer no communication action,
so valid communication attempts there stop at `_require_available` with
`action_unavailable`.  The same rule applies in deadline-bearing phases such as DAWN or
VOTE that do not offer the requested communication.

An acceptance object is created only after every check succeeds.  Chat history,
public-activity counts, CO counts, and CO events are mutated/published only on success.

For a semantically valid chat, declaration, or report:

| Action availability / receipt relative to state | Result |
|---|---|
| Requested communication is not offered, with or without a deadline | preserve `action_unavailable` or an earlier existing semantic rejection; do not inspect the window |
| Communication is offered but start or deadline is absent | `RuntimeError` invariant failure; no mutation/evidence |
| Offered and `now < phase_started_at` | reject `action_unavailable`; no mutation/evidence |
| Offered and `phase_started_at <= now < phase_ends_at` | accept after existing semantic checks; return immutable acceptance |
| Otherwise-valid offered action and `now == phase_ends_at` | reject `action_deadline_passed`; no mutation/evidence |
| Otherwise-valid offered action and `now > phase_ends_at` | reject `action_deadline_passed`; no mutation/evidence |

Protocol shape failures are still rejected by `ProtocolMessageValidator` before dispatch.
For a protocol-valid, semantically valid, currently offered action, the strict window check
always precedes mutation, so equality cannot be accepted.  It does not override legitimate
unavailability or existing semantic rejection for an action which is not eligible in the
current state.

Action enumeration remains a snapshot and may briefly advertise an action until the next
tick.  Enumeration is not authorization: receipt-time validation is the final authority.

## Tick, Extension, and Shortening Ordering

The dispatch lock gives a total order; there is no simultaneous core mutation.

1. If request dispatch acquires the lock first, it samples `received_at` and the core uses
   the current deadline.  At equality it rejects.  A later tick may then advance the phase.
2. If the tick acquires the lock first, it advances the due phase and emits its events.
   The later request is checked against the new current phase/window and its normal action
   availability.  It is never retroactively attributed to the ended phase.
3. If an extension commits first, the request sees and records the extended deadline.  If
   acceptance commits first, it records the pre-extension deadline and remains accepted.
4. If shortening commits first, a request at or after the shortened deadline is rejected.
   If acceptance commits first, later shortening does not retroactively revoke it.

Session dispatch must not call `advance_if_due`; phase advancement stays with the ticker.
No new lock is added inside `GameState`.  Direct, non-WebSocket core users remain responsible
for serial invocation, as before.

All work from timestamp sampling through core return and accepted chat queueing remains in
the current synchronous section under `_dispatch_lock`; no `await` may be inserted in that
critical section.

## Failure and Compatibility Behavior

- For an offered communication, `bool`, float, string, and other non-integer `now` values
  fail via `timestamp()` and do not mutate game state.  An unavailable action retains its
  normal rejection without requiring or interpreting a time.
- Only a state which actually advertises the requested communication but lacks
  `phase_started_at` or `phase_ends_at` is a server invariant failure (`RuntimeError`).
  Legitimate no-deadline or non-communication phases return the existing safe rejection.
- Deadline rejection reason is exactly `action_deadline_passed` for an otherwise-valid,
  offered chat or CO action at/after the deadline.  Existing `action_unavailable`, content,
  target, quota, and payload reasons remain unchanged when the action is not eligible.
- Failed operations produce no `InteractionAcceptance`, chat history, delivery, public
  activity, CO quota increment, or CO event.
- Existing accepted wire payloads and public CO events do not gain fields.  Sequence and
  protocol envelope timestamps retain their current independent meaning.
- Source compatibility intentionally breaks for direct core communication calls without a
  logical time.  Repository callers in `tests/test_player_interactions.py`,
  `tests/test_state_delivery.py`, and any other search result must be mechanically updated
  to pass the phase's controlled open time.  There is no compatibility shim.

## Completion Evidence Reconciliation

The Phase 3.4 completion `queue_spy` remains at the approved post-authorization
`queue_channel_message` point, but its signature accepts the keyword-only `acceptance`.
For a client chat it records:

- `player_id` from both message and acceptance, asserting equality;
- `day`, `phase`, `accepted_at`, and `phase_deadline` only from acceptance;
- `channel` and message from the queued submission.

It must not call the fixture clock and must not read `game.day`, `game.phase`, or
`game.phase_ends_at` for an accepted record.  It asserts one acceptance per queued client
chat, `accepted_at < phase_deadline`, and the existing exact Day 1 counts.  Later-phase
records remain in the check; no day/phase filtering is added to hide a boundary failure.

The wire assertion additionally proves that `InteractionAcceptance` is absent from the
serialized `chat.message` payload.  Server-originated `publish_channel_message` deliveries
with `acceptance=None` are outside the player-acceptance histogram.

## Required Tests

### Focused core tests

- For chat, CO declaration, and CO report, use a fresh controlled game to test
  `deadline - 1`, `deadline`, and `deadline + 1`.  Before accepts; equality/after reject
  exactly `action_deadline_passed`.
- For each of the same three operations, issue a semantically valid request in SETUP (a
  legitimate no-deadline/non-communication phase) with an explicit logical time.  Each must
  reject `action_unavailable`, not raise `RuntimeError`, and produce no acceptance or
  mutation.
- For each operation, enter DAWN (a deadline-bearing/non-communication phase) and issue a
  semantically valid request at `phase_ends_at - 1`, `phase_ends_at`, and
  `phase_ends_at + 1`.  Every request must preserve `action_unavailable`; the deadline guard
  must not override action availability merely because a deadline exists or has elapsed.
- Construct an intentionally inconsistent DAY state which still advertises each requested
  communication but has `phase_started_at=None`, then separately `phase_ends_at=None`.
  Each operation must raise `RuntimeError` with zero mutation.  These are the only
  missing-window cases expected to raise.
- Assert the successful acceptance has exact action/player/day/phase/accepted/deadline
  values and is frozen.
- Assert equality/after cause no history, delivery, public activity, CO quota mutation, or
  CO public event.  This preserves sudden-death and quota semantics.
- For an offered action, test `now < phase_started_at` and non-integer/bool time.  Also pin
  one existing semantic error for each operation to prove the revision has not reordered it
  into a deadline or invariant failure.
- Preserve renamed-channel/content-derived CO tests and update all direct callers with an
  explicit open logical time.

### Session and network tests

- Inject a counting/sequence clock and a recording `SessionGame`.  Prove that one sample is
  passed unchanged into each of chat, declaration, report, and ability dispatch; no second
  authorization sample occurs.
- Prove the network does not reject a deadline itself: a recording core receives the value
  and its `ActionRejected` result is merely translated to `action.rejected`.
- At exact equality against a real game, assert the three safe rejections and no delivery.
  A later reply-envelope clock sample must not change the core's recorded time.
- Through a real `SessionManager`, repeat the SETUP and DAWN matrices above and assert safe
  `action.rejected` replies with reason `action_unavailable`; no request path may leak a
  `RuntimeError` or close the session.
- Exercise both deterministic dispatch-lock orders at a boundary (request-first and
  tick-first), plus extension-first/acceptance-first and shortening-first/acceptance-first.
  Assertions use barriers/events rather than sleeps.
- Assert client chat produces one `OutboundDelivery` with matching acceptance metadata,
  while the serialized payload is unchanged.  Assert server-originated channel publication
  remains supported with no acceptance.

### Mutation detection

The focused tests must fail if any of these mutations is applied:

- `received_at >= deadline` becomes `received_at > deadline`;
- core dispatch samples the clock a second time or evidence samples after authorization;
- the supplied `received_at` is replaced by `phase_started_at`, current tick time, or an
  envelope timestamp;
- acceptance stores a live/current deadline instead of the authorization snapshot;
- rejected communication mutates activity, quota, history, event, or delivery state;
- the window helper runs before `_require_available`, turning SETUP/DAWN attempts into
  `RuntimeError` or deadline rejections;
- internal acceptance metadata leaks into a wire payload;
- the network makes the equality decision without calling the core.

### Regression and completion

- `python -m pytest tests/test_player_interactions.py tests/test_available_actions.py tests/test_phase_manager.py -q`
- `python -m pytest tests/test_network_sessions.py tests/test_state_delivery.py -q`
- `python -m pytest tests/test_phase3_4_reaction_chat.py -q`
- `python -m pytest tests/test_phase2_completion.py -q`
- `python -m pytest tests/test_phase3_4_completion.py -q`
- `python -m pytest -q`
- `python scripts/check_docs.py`
- `python -m compileall server tests`
- `git diff --check`

Completion retains its existing budgets and exact acceptance criteria.  Test commands may
be run serially when host load makes concurrent multiprocess evidence invalid.

## Acceptance Criteria

1. Session dispatch samples one integer receipt time and passes the same value into core
   legality for chat, both CO actions, and the existing ability path.
2. The core rejects each otherwise-valid, currently offered communication at or after its
   current deadline with `action_deadline_passed` and performs no partial mutation.
3. An accepted chat's immutable metadata contains the same time/deadline/day/phase used by
   authorization and reaches the existing queue spy point.
4. Completion no longer takes a second clock or mutable-state sample and continues to check
   every accepted record with strict `<`, including later phases.
5. Existing tick serialization has deterministic request-first/tick-first,
   extension/shortening behavior without moving phase or legality ownership to network code.
6. Accepted chat and CO wire payloads are unchanged; no timestamp or internal evidence is
   disclosed to clients.
7. Direct core callers cannot bypass the deadline by omitting `now`.
8. Focused boundary, mutation-detection, Phase 2/3.4 completion, and full regression evidence
   pass before implementation is accepted.
9. Valid communication attempts in legitimate no-deadline and deadline-bearing
   non-communication phases preserve `action_unavailable`; `RuntimeError` is restricted to
   an actually offered communication action whose authoritative window is missing.

## Rejected Alternatives

- Sampling in `queue_spy` or `EventDeliveryRouter`: too late and not the value used for
  authorization; this caused the observed ambiguity.
- Sampling separately in each core or delivery call: permits equality drift across integer
  clock boundaries and cannot prove one receipt fact.
- Comparing the client timestamp or Phase 3 local monotonic deadline: wrong authority and
  clock domain.
- Letting `SessionManager` reject on the deadline: moves a game rule into the network layer.
- Calling `advance_if_due` from request dispatch: makes request handling a second phase
  driver and broadens this repair beyond the current ticker contract.
- Preserving no-time core overloads: leaves an untestable deadline bypass.
- Adding acceptance fields to public chat/CO payloads: unnecessary protocol expansion and
  information surface.
- Filtering completion evidence to Day 1 or changing `<` to `<=`: weakens the approved
  acceptance contract instead of fixing the authority gap.

## Gate

This document is not implementation authority until a separate Reviewer approves its exact
contents and records review evidence.  The authoring Architect must not self-approve.
