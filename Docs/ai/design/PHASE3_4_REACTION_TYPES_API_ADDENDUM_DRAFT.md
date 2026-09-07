Status: APPROVED — Reviewer / Sol (`gpt-5.6-sol`), 2026-09-08; exact pre-approval bytes are bound in the review record

# Phase 3.4 reaction types / randomness API addendum

Author role: Detailed Design

Author model: GPT-6 Astra (`gpt-6-astra`)

Canonical parent: `Docs/ai/design/PHASE3_4_REACTION_CHAT_DESIGN.md`

Parent SHA256 reviewed for this draft:
`0be7022f7c9d121bd33ebe6949c3027b7a07ad86d3e9cbc0eb0314c68e90c1cb`

## Reason and scope

D059 run `overnight-9738f6620f004b829756bd9410749df2` stopped before writing
tests or source. Its actual `gpt-5.5` planner found that the approved parent fixes
the required semantics but does not name the public jitter callable or the Python
records and fields that its generated tests must import. This addendum fixes only
those names and signatures. Controller behavior, network/world integration, Brain
changes, transport, process fixtures, and completion scenarios remain governed by
the parent design and are outside this addendum.

## Exact public API

`ai_client.reaction_chat.types` defines and
`ai_client.reaction_chat` re-exports these values:

```python
class ReactionChatLifecycle(str, Enum):
    NEW = "new"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"

class ReactionTriggerKind(str, Enum):
    INITIAL_CHAT = "initial_chat"
    REACTION_CHAT = "reaction_chat"
    CO_ACTION = "co_action"

class ReactionOutcomeStatus(str, Enum):
    NO_DECISION = "no_decision"
    INVALID = "invalid"
    BRAIN_FAILED = "brain_failed"
    DEADLINE_SUPPRESSED = "deadline_suppressed"
    TIMED_OUT = "timed_out"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    TRANSPORT_GAP = "transport_gap"
    HISTORY_GAP = "history_gap"

@dataclass(frozen=True)
class ReactionPhaseKey:
    connection_generation: int
    day: int
    phase: str
    action_generation: int

@dataclass(frozen=True)
class ReactionTrigger:
    kind: ReactionTriggerKind
    phase_key: ReactionPhaseKey
    source_order: int | None
    attempt_ordinal: int

@dataclass(frozen=True)
class ReactionOutcome:
    trigger: ReactionTrigger
    scheduled_due_monotonic: float
    brain_outcome: str | None
    action_kind: str | None
    status: ReactionOutcomeStatus

@dataclass(frozen=True)
class CoGenerationState:
    action_generation: int
    invoked: bool
    closed: bool

@dataclass(frozen=True)
class ReactionChatSnapshot:
    lifecycle: ReactionChatLifecycle
    current_phase_key: ReactionPhaseKey | None
    history_cursor: int
    transport_cursor: int
    chat_brain_invocations: int
    send_count: int
    accepted_count: int
    rejected_count: int
    deadline_suppressed_count: int
    intentional_silence_count: int
    co_generation_state: CoGenerationState | None
    outcomes: tuple[ReactionOutcome, ...]

@dataclass(frozen=True)
class ReactionChatConfig:
    max_chat_attempts_per_phase: int = 2
    minimum_accepted_chat_interval_seconds: float = 0.20
    initial_jitter_seconds: tuple[float, float] = (0.00, 0.20)
    reaction_jitter_seconds: tuple[float, float] = (0.05, 0.15)
    deadline_guard_seconds: float = 0.25
    brain_timeout_seconds: float = 0.25
    minimum_start_budget_seconds: float = 0.05
    outcome_retention: int = 256
```

`ai_client.reaction_chat.randomness` defines and
`ai_client.reaction_chat` re-exports this callable:

```python
def deterministic_jitter_seconds(
    *,
    master_seed: int,
    player_id: str,
    phase_key: ReactionPhaseKey,
    trigger_kind: ReactionTriggerKind,
    source_order: int | None,
    attempt_ordinal: int,
    lower_seconds: float,
    upper_seconds: float,
) -> float: ...
```

## Validation and derivation

- `bool` is rejected wherever an integer or float is required. Numeric values must
  be finite. Counts, cursors, generations, source order when present, and attempt
  ordinal are non-negative. `day` is positive; strings are non-empty.
- Config requires `max_chat_attempts_per_phase > 0`, `outcome_retention > 0`,
  non-negative durations, and ordered jitter pairs (`lower <= upper`).
- Snapshot counters and cursors are non-negative. The number of retained outcomes
  may not exceed the config at the controller boundary; the value object itself
  stores the supplied immutable tuple without global configuration state.
- The jitter callable applies the same numeric/string validation and requires
  `0 <= lower_seconds <= upper_seconds`. Equal endpoints return that endpoint.
- For a non-degenerate range, the SHA-256 identity excludes `lower_seconds` and
  `upper_seconds`. In this exact order, encode the byte strings
  `b"aiwolf/reaction-chat/jitter/v1"`, canonical base-10 `master_seed`, UTF-8
  `player_id`, canonical base-10 `phase_key.connection_generation`, canonical
  base-10 `phase_key.day`, UTF-8 `phase_key.phase`, canonical base-10
  `phase_key.action_generation`, UTF-8 `trigger_kind.value`, either ASCII `none`
  or canonical base-10 `source_order`, and canonical base-10 `attempt_ordinal`.
  Prefix each byte string with its unsigned 4-byte big-endian byte length and
  concatenate all prefixed values. Strings are encoded exactly as supplied with
  no Unicode, case, or whitespace normalization. Hash that byte sequence with
  SHA-256. Set `n = int.from_bytes(digest[0:7], "big") >> 3` (the first 53 bits),
  set `fraction = n / (2**53 - 1)`, and return
  `lower_seconds + (upper_seconds - lower_seconds) * fraction`.
- The function must not use Python `hash()`, `random`, time, process state, mutable
  module state, or scheduling/arrival order.

## Gate and acceptance

This draft introduces public names that were absent from the approved parent.
Under D051/D053 it is not implementation authority until a model different from
the Detailed Design author reviews the exact bytes and changes the status to
`APPROVED` with a hash-bound review record. The stopped run stays terminal and is not resumed or
rewritten. A later workpackage must bind the approved addendum hash and use a new
run.

An implementation of this isolated unit is acceptable only when generated tests
import exactly the API above, cover immutability/validation and deterministic
separation, retain the Phase 3.3 baseline node, and neither import nor modify the
server, game core, Network, World, Brain, or controller modules.
