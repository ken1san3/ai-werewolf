Status: APPROVED — T208 independently approved outer-observation ownership R2, 2026-09-13

# Phase 6 Discussion Quality — Detailed Design

## 1. Purpose and authority

This design adds model-neutral private belief, suspicion, strategy, important-event memory, and
responsive dialogue semantics to the existing Phase 3–5 AI client. It does not change game truth,
content, the wire protocol, server rules, or admission. Implementation remains blocked until an
independent Reviewer approves this document under D051.

Authority, in order, is `Docs/ai/spec/DESIGN.md`, `Docs/ai/ROADMAP.md`, D068, D069, current
interfaces/tests, the approved Phase 3.2–5 designs, and T169. D069 supersedes D068's earlier open-Q8
wording: `standard_9` keeps a 180-second day and existing shortening/extension, has no new general
server speech cap, and permits at most two **CHAT Brain starts per phase**. CO remains the existing
separate system-action path; vote and ability are also outside that CHAT count.

T154 proves the exact-9B mechanical path. Aggregate-only T158/T160 findings establish a quality
defect, but raw private utterances are not repeated here and no second transcript logger is added.

## 2. Selected architecture and non-scope

Each AI process gains exactly one `DiscussionStateStore`. World State remains facts-only. The store
consumes immutable, recipient-authorized World views, owns private inference, and supplies one
bounded immutable `DiscussionCapture` to the existing shared Brain path. `LLMBrain` produces one
closed network decision plus one closed semantic proposal in the same provider response. There is no
second model call, Brain, queue, or action path.

```text
trusted local composition root -- player-specific sealed context --\
World State -- authorized views --> DiscussionStateStore -----------+--> LLMBrain
Reaction trigger / pre-vote trigger --------------------------------/       |
                                                                            v
                                                        existing BrainController
                                                                            |
                                                        received-handle-only send
```

Explicitly out of scope are UI, new roles or modifier behavior, content/schema migration, game-core,
server, protocol, or `ai_client/world` changes, a server speech cap, tempo/default changes, CO-report
generation, provider/model routing, 35B/alternate/fallback use, model-generated summaries,
remote-server attestation, broad refactors, housekeeping, Autodev, and any Phase 7/8 gameplay work.

## 3. Trusted canonical discussion context

### 3.1 Trust boundary

The current wire does not carry a content digest, team, effective attributes, win conditions, or a
complete authorized teammate list. The client therefore must not load a guessed preset, infer from a
role name, or duplicate server resolution.

For the supported local Phase 6 composition, the runner's existing server child already owns the
exact validated `ContentPack`, selected `Preset`, and newly created `GameState`. Immediately after
that state is created and before readiness is published, the server-child adapter in
`scripts/run_phase5_local_smoke.py` creates one player-specific `AuthorizedDiscussionContext` from
those same in-memory objects, including only that player's authorized chat-channel descriptors and
their canonical `is_public` bits. It writes the sealed envelope map once to a runner-private temporary
relay path supplied in the server child's private stdin bootstrap. The parent reads and validates
the complete map, removes the relay before starting clients, and puts only the matching envelope in
each client's existing one-line private stdin bootstrap. A missing/duplicate seat, unreadable relay,
validation failure, or relay-removal failure aborts launch and runs bounded owned cleanup. The relay
and complete bootstrap envelope are never written to readiness, server-result, public/runner
evidence, command-line arguments, stdout/stderr, or broker metrics. Only the validated bounded owner
context enters that owner's private prompt/generation audit as §3.2 specifies.

This is a local composition-root adapter, not a change under `server/*`, `content/*`, or
`protocol/*`, and production `ai_client` never imports `server.*`. The bootstrap envelope is the
closed object

```text
{
  schema_version: "aiwolf.discussion-bootstrap.v1",
  manifest_material: ContentManifestMaterial,
  manifest_sha256: lowercase-hex SHA-256,
  context_payload: AuthorizedDiscussionContext,
  context_sha256: lowercase-hex SHA-256
}
```

`ContentManifestMaterial` is a receiver-verifiable canonical value with
`schema_version="aiwolf.content-manifest.v1"`, a `content_pack` object containing every declared
field of the exact validated `ContentPack` roots (`teams`, `roles`, `effects`, `passives`,
`selectors`, `restriction_types`, `action_timings`, `chat_channels`, `death_causes`, and
`modifiers`), and an `effective_preset` object containing every field of the selected `Preset`
*after* the runner's reviewed timing replacement. It contains definitions and role counts but no
player/seat assignment, credentials, process path, time, or random state.

Canonicalization recursively maps a dataclass to its declared field-name/value object, an `Enum` to
its string value, a mapping with string keys to a key-sorted object, a tuple/list to an order-
preserving array, and a set/frozenset to an array sorted by each member's canonical JSON bytes. Only
`null`, exact `bool`, exact integer, and Unicode string leaves are accepted; float, bytes, object
`repr`, unknown type, surrogate, NaN, and Infinity are rejected. The final bytes are UTF-8 JSON with
`ensure_ascii=False`, keys sorted by Unicode code point, compact separators, and no NaN.
`manifest_sha256 = SHA256(canonical(manifest_material))` and
`context_sha256 = SHA256(canonical(context_payload))`; neither digest field is included in its own
hash input. `context_payload.content_manifest_sha256` must equal `manifest_sha256`.

The parent and each client recompute both hashes. Before manifest disposal, each validates every
authorized-channel descriptor against `manifest_material.content_pack.chat_channels` as specified
in §3.2. The parent additionally requires all nine
canonical manifest byte strings and digests to be identical, every context seat to be unique and
complete, and every context `game_id` to equal the server-ready `game_id`. This verifies relay
integrity and same-composition identity as asserted by the trusted server child; it is not proof
against that trust root and is not remote attestation. A remote or separately launched server
cannot satisfy this contract without a later approved versioned projection/attestation design and
is rejected, not guessed.

Binding has two separate authorities. Before constructing network/backend/audit resources,
`Phase5ClientRuntimeConfig.network.game_id` must equal `context_payload.game_id` and
`Phase5ClientRuntimeConfig.player_id` must equal `context_payload.player_id`. At the first
authoritative `CURRENT` sync, `snapshot.self_view.player_id`, `role_id`, and sorted unique
`modifier_ids` must respectively equal the context player, role, and modifiers. `game_id` is never
read from `SelfView`, `WorldSnapshot`, or a new wire field. Missing context, malformed hashes,
identity mismatch, an unknown referenced ID, or a later role/modifier-set change is terminal
`DISCUSSION_CONTEXT_INVALID`: no backend call or Network action is made and the runtime closes
through its existing bounded cleanup. This is a configuration failure, never a fallback to the old
context-free prompt.

The manifest material is bounded to 64 entries in each root mapping, 128 Unicode scalars/512 UTF-8
bytes per ID, 64 KiB canonical bytes total, and the complete bootstrap envelope to 80 KiB. It exists
only in the server-child relay, parent validation, and client bootstrap validation, and the client
drops it after binding. The runtime/store retain only `manifest_sha256`, `context_sha256`, and the
validated player-specific context payload, including its bounded two-field own-channel descriptors.
Neither raw manifest material, other-channel definitions, preset role counts, names/descriptions,
nor other-role definitions enter the model projection or any audit/log/result.

### 3.2 Exact model-projected context

The nested channel value is exact and frozen:

```python
@dataclass(frozen=True)
class AuthorizedChatChannelContext:
    channel_id: str
    is_public: bool
```

`AuthorizedDiscussionContext` has `schema_version="aiwolf.discussion-context.v1"` and contains only:

- game/player identity, opaque role ID, sorted modifier IDs, and the content-manifest hash;
- effective `team`, `count_as`, `attack_result`, `inspect_result`, and `medium_result` IDs already
  resolved by the canonical game-content functions at the composition root;
- effective win conditions as the closed current unions
  `eliminate_role_tag(tag)`, `count_parity(subject, against, operator)`, and
  `survive_when_others_win(replaces)`; these structured conditions are the objective. No objective
  prose is invented;
- descriptive capabilities: ability ID/timing/available night/priority/resolution/target selector
  and count/uses/no-selection/effect IDs, passive type/priority/effect IDs, and
  `chat_channels: tuple[AuthorizedChatChannelContext, ...]`;
- a boolean canonical `knows_teammates` declaration, but **no teammate player IDs**. The current wire
  does not authorize a complete list, so `authorized_known_player_ids=()` and
  `known_players_complete=false` are mandatory in Phase 6.

Each descriptor has exactly the two displayed required non-null fields. `is_public` accepts exact
`bool`, never integer `0`/`1`. `channel_id` uses the normal ID bound. The tuple contains 0–8 unique
entries in Unicode-code-point channel-ID order; missing/extra fields, a duplicate, or any alternative
order is invalid. The trusted server child obtains exactly the effective `chat_channels_for(player)`
set from the same in-memory player/content objects used to create the game and copies each matching
`ContentPack.chat_channels[channel_id].is_public`; it never branches on a role or channel literal.

Before dropping the manifest, parent and client require each descriptor ID and exact boolean to
match the manifest channel entry and require the descriptor-ID set to equal the context's resolved
effective authorized-channel set recomputed generically from the bound role/modifier IDs and the
manifest. An unknown, missing, extra, duplicate, reordered, or boolean-
mismatched descriptor is `DISCUSSION_CONTEXT_INVALID` before backend/network construction. The
descriptor tuple is part of canonical context bytes and `context_sha256`; a bit, membership, or order
change therefore changes or invalidates the digest. `manifest_sha256` and its algorithm are unchanged.

After binding, the full manifest is still discarded; the runtime/store retain only the already-
approved context containing this player's descriptors. Those descriptors are mandatory private model
context and may occur in that owner's prompt and durable generation audit. They remain absent from
ready/result/argv/environment/stdout/stderr/broker metrics, network decisions, terminal/public
summaries, and every other client's context. This is not a raw-manifest retention exception.

Names, localized labels, preset role counts, other-seat assignments, other-channel definitions,
unrestricted YAML mappings, and capability descriptions are excluded. Capability context is
explanatory only. Current received `ActionHandle` values remain the sole action authority.
Unsupported future win-condition shapes or
capability shapes fail context creation. This phase does not create modifier semantics: the adapter
may project an already resolved current modifier set, but any runtime modifier-set change fails
closed and is deferred to the Phase 8 MOD Design Gate.

All IDs are 1–128 Unicode scalars and at most 512 UTF-8 bytes. Limits are: 8 modifiers, 8 win
conditions, 16 abilities, 16 passives, 8 chat channels, 8 effects per capability, and 8 KiB for the
canonical serialized context payload. The complete context is mandatory and is never trimmed to
meet a prompt budget. `bool` is never accepted as an integer. Duplicate IDs, extra fields,
NaN/Infinity, invalid UTF-8, and values outside these limits are rejected before runtime start.

## 4. Private discussion state

### 4.1 Owned immutable values

`ai_client.discussion` defines closed frozen values. Names below are contractual; helper names are
not.

```python
class EvidenceRecordKind(str, Enum):
    CHAT = "chat"
    CO_DECLARATION = "co_declaration"
    CO_REPORT = "co_report"
    VOTE_RESULT = "vote_result"
    VOTE_REVEAL = "vote_reveal"
    DEATH = "death"
    PHASE_TRANSITION = "phase_transition"
    PHASE_TIMING_CHANGED = "phase_timing_changed"
    ABILITY_RESULT = "ability_result"
    GAME_LIFECYCLE = "game_lifecycle"
    PUBLIC_NOTIFY = "public_notify"
    TIE_RESOLVED_RANDOM = "tie_resolved_random"
    KNOWN_UNMODELED = "known_unmodeled"
    UNKNOWN = "unknown"
    MALFORMED = "malformed"
    ACTION_ACCEPTED = "action_accepted"
    ACTION_REJECTION = "action_rejection"
    PHASE_TIMING = "phase_timing"
    PHASE_DEADLINE_REACHED = "phase_deadline_reached"
    RESUME_RECOVERY_BARRIER = "resume_recovery_barrier"

class EvidenceVisibility(str, Enum):
    PUBLIC = "PUBLIC"
    AUTHORIZED_PRIVATE = "AUTHORIZED_PRIVATE"
    VISIBILITY_LOST = "VISIBILITY_LOST"

class DiscussionResetReason(str, Enum):
    RECOVERY_GAP = "RECOVERY_GAP"
    PROCESS_RESTART = "PROCESS_RESTART"

class ClaimVerdict(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"

class RelationKind(str, Enum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    DEFENDS = "DEFENDS"
    ACCUSES = "ACCUSES"
    DISTANCES_FROM = "DISTANCES_FROM"

class StrategyMode(str, Enum):
    GATHER_INFORMATION = "GATHER_INFORMATION"
    TEST_CLAIM = "TEST_CLAIM"
    RESOLVE_CONTRADICTION = "RESOLVE_CONTRADICTION"
    BUILD_CONSENSUS = "BUILD_CONSENSUS"
    PROTECT_PRIVATE_INFORMATION = "PROTECT_PRIVATE_INFORMATION"
    PREPARE_VOTE = "PREPARE_VOTE"
    USE_OFFERED_CAPABILITY = "USE_OFFERED_CAPABILITY"
    WAIT = "WAIT"

@dataclass(frozen=True)
class EvidenceRef:
    record_kind: EvidenceRecordKind
    order: int
    visibility: EvidenceVisibility

@dataclass(frozen=True)
class ImportantEvent:
    schema_version: Literal["aiwolf.important-event.v1"]
    source: EvidenceRef
    day: int | None
    phase: str | None
    actor_player_ids: tuple[str, ...]
    target_player_ids: tuple[str, ...]
    channel_id: str | None
    importance: int
    text_excerpt: str | None
    text_original_scalars: int | None
    text_original_utf8_bytes: int | None
    text_truncated: bool
    remembered_after_world_eviction: bool

@dataclass(frozen=True)
class DiscussionProvenance:
    schema_version: Literal["aiwolf.discussion-provenance.v1"]
    history_complete: bool
    co_complete: bool
    ability_results_complete: bool
    world_history_complete: bool
    world_history_dropped_count: int
    world_history_dropped_through_order: int | None
    remembered_after_world_eviction_count: int
    known_unmodeled_event_count: int
    unknown_event_count: int
    malformed_event_count: int
    model_state_reset: bool
    reset_reason: DiscussionResetReason | None

@dataclass(frozen=True)
class RecentSemanticTurn:
    schema_version: Literal["aiwolf.recent-semantic-turn.v1"]
    committed_revision: int
    capture_id: str
    request_id: str
    day: int
    phase: str
    decision_kind: Literal["none", "chat", "vote", "ability", "co_declare"]
    option_id: str | None
    speech_act_kind: "SpeechActKind"
    evidence: tuple[EvidenceRef, ...]
    proposal_sha256: str

@dataclass(frozen=True)
class PlayerAssessment:
    player_id: str
    suspicion: int                 # 0..100
    credibility: int               # 0..100
    confidence: int                # 0..100
    evidence: tuple[EvidenceRef, ...]

@dataclass(frozen=True)
class ClaimAssessment:
    claim: EvidenceRef
    speaker_player_id: str
    verdict: ClaimVerdict
    confidence: int
    evidence: tuple[EvidenceRef, ...]

@dataclass(frozen=True)
class RelationHypothesis:
    source_player_id: str
    target_player_id: str
    relation: RelationKind
    confidence: int
    evidence: tuple[EvidenceRef, ...]
    provenance: Literal["PUBLIC_INFERENCE"]

@dataclass(frozen=True)
class StrategyState:
    scope: Literal["GAME", "PHASE"]
    mode: StrategyMode
    focus_player_ids: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]

@dataclass(frozen=True)
class DiscussionStateSnapshot:
    schema_version: Literal["aiwolf.discussion-state.v1"]
    game_id: str
    player_id: str
    context_sha256: str
    epoch: int
    revision: int
    fact_revision: int
    world_version: int
    last_applied_seq: int
    phase: str | None
    day: int | None
    assessments: tuple[PlayerAssessment, ...]
    claims: tuple[ClaimAssessment, ...]
    relations: tuple[RelationHypothesis, ...]
    strategy: StrategyState | None
    important_events: tuple[ImportantEvent, ...]
    recent_semantic_turns: tuple[RecentSemanticTurn, ...]
    provenance: DiscussionProvenance

@dataclass(frozen=True)
class DiscussionTrigger:
    owner: Literal["reaction_chat", "vote_ability"]
    kind: Literal["INITIAL_CHAT", "PEER_CHAT", "CO_OPPORTUNITY", "PRE_VOTE", "ABILITY"]
    day: int
    phase: str
    connection_generation: int
    action_generation: int
    mapping_order: int
    source: EvidenceRef | None

@dataclass(frozen=True)
class DiscussionCapture:
    schema_version: Literal["aiwolf.discussion-capture.v1"]
    capture_id: str
    capture_ordinal: int
    game_id: str
    player_id: str
    context_sha256: str
    state_sha256: str
    epoch: int
    base_revision: int
    fact_revision: int
    world_version: int
    last_applied_seq: int
    trigger: DiscussionTrigger
    context: "AuthorizedDiscussionContext"
    state: DiscussionStateSnapshot
    evidence: tuple[ImportantEvent, ...]
```

All enum values serialize as their displayed strings; frozen records serialize as objects with
exactly the displayed fields; tuples serialize as arrays; and `None` serializes as JSON `null`.
Missing or extra fields are invalid. `EvidenceRef.order` is a non-negative integer and its kind must
name the typed source record. Visibility is the following closed, code-derived three-value safety
lattice: `PUBLIC` means the trusted inputs prove public publication, `AUTHORIZED_PRIVATE` means they
prove non-public or client-local authorization, and `VISIBILITY_LOST` means the client was authorized
to receive the record but its retained typed value no longer proves the original audience. Model
output may repeat the expected value but cannot select, infer, or upgrade it:

| `EvidenceRecordKind` | Required `EvidenceVisibility` |
|---|---|
| `CHAT` | matching descriptor `is_public=true` → `PUBLIC`; false → `AUTHORIZED_PRIVATE`; absent descriptor → capture failure |
| `CO_DECLARATION` | `PUBLIC` |
| `CO_REPORT` | `PUBLIC` |
| `VOTE_RESULT` | `PUBLIC` |
| `VOTE_REVEAL` | `PUBLIC` |
| `DEATH` | `PUBLIC` |
| `PHASE_TRANSITION` | `PUBLIC` |
| `PHASE_TIMING_CHANGED` | `PUBLIC` |
| `ABILITY_RESULT` | `AUTHORIZED_PRIVATE` |
| `GAME_LIFECYCLE` | `PUBLIC` |
| `PUBLIC_NOTIFY` | `PUBLIC` |
| `TIE_RESOLVED_RANDOM` | `PUBLIC` |
| `KNOWN_UNMODELED` | `VISIBILITY_LOST` |
| `UNKNOWN` | `VISIBILITY_LOST` |
| `MALFORMED` | `VISIBILITY_LOST` |
| `ACTION_ACCEPTED` | `AUTHORIZED_PRIVATE` |
| `ACTION_REJECTION` | `AUTHORIZED_PRIVATE` |
| `PHASE_TIMING` | `AUTHORIZED_PRIVATE` |
| `PHASE_DEADLINE_REACHED` | `AUTHORIZED_PRIVATE` |
| `RESUME_RECOVERY_BARRIER` | `AUTHORIZED_PRIVATE` |

The fixed mapping branches only on the closed typed record variant. It never branches on opaque
event type, role ID, channel ID, phase, display name, or message. In particular the three marker
records remain `VISIBILITY_LOST` even when their bounded `event_type` resembles a known public or
private event; their erased raw payload/audience is never recovered. A `CHAT` source performs the sole descriptor lookup; an explicit channel absent from
the sealed tuple is `DISCUSSION_CONTEXT_INVALID`, with zero backend, stage, commit, or send. A
provider visibility mismatch, unknown enum, or use of private/lost evidence where `PUBLIC` is
required invalidates the complete proposal before state mutation or send.
The already-closed `ImportantEvent` identity, actor/target/text, ordering, and bounds are unchanged.
Its computed visibility is already nested in canonical event/state/capture bytes, so the existing
`state_sha256` and `capture_id` algorithms cover it without a new digest field.

`ImportantEvent.source` is its sole identity and may use only the fifteen history kinds through
`MALFORMED`; transport-observation kinds are reserved for terminal observation evidence. Day and
phase copy the typed source's nullable values
exactly. Actor and target arrays contain distinct current player IDs in Unicode-code-point order,
never inferred IDs: chat/CO supply their explicit speaker; CO report and ability result supply their
explicit target; death supplies the dead player; vote result/reveal and tie resolution supply the
union of their explicit voters/candidates/targets; other kinds supply no actor/target. Each array is
limited to 32. `channel_id` is required exactly for `CHAT` and null otherwise. For `CHAT` and
`CO_DECLARATION`, all three text fields are present: the excerpt is the authorized message/comment,
prefix-truncated without splitting a Unicode scalar to at most 160 scalars and 768 UTF-8 bytes;
original counts are non-negative and not below excerpt counts; `text_truncated` is true exactly when
either original count exceeds its excerpt count. For every other kind the three text fields are null
and `text_truncated=false`. Importance is an integer 0..100. IDs/phase/channel follow the 1–128
scalar/512-byte identifier bound. `remembered_after_world_eviction` is true only when this exact
source was observed earlier in the same uninterrupted epoch but is absent from the current retained
World prefix.

`DiscussionProvenance` copies the three view-completeness flags independently. The World retention
fields copy `HistoryRetention.complete`, `dropped_count`, and `dropped_through_order`; dropped count
is non-negative, `dropped_through_order` is null exactly when it is zero, and is non-negative when
present. All other counts are non-negative integers. The remembered count equals the number of
flagged `important_events`. `model_state_reset=false` requires a null reason; true requires exactly
`RECOVERY_GAP` or `PROCESS_RESTART`, retained for that epoch. A fresh new game is not represented as
a reset. Completeness and reset fields are facts, not model claims or prompt-omission counters.

`RecentSemanticTurn` is the bounded no-prose summary appended by the model-state CAS commit. Its
`decision_kind` and `option_id` come only from the staged proposal's required identity fields defined
in §7; `commit(stage_ack)` retrieves that exact proposal from the one matching internal stage and
does not infer either value from a handle, text, player, time, acknowledgement, or later observation.
It also contains the proposal's speech-act kind, de-duplicated evidence refs in
`(order, record_kind)` order, and proposal digest. `request_id` is exactly
`"phase6:" + capture_id`; `committed_revision` is positive and unique in the tuple; option is null
exactly for `none` and otherwise is the validated offered option. Evidence has at most eight entries.
Turns are ordered by increasing committed revision and only the newest 16 are retained. They survive
a clean phase transition in the same game, contain no generated text or delivery/acceptance claim,
and are cleared with model-derived state on an epoch reset.

`state_sha256` is SHA-256 of the canonical JSON object containing, in the displayed schema, every
`DiscussionStateSnapshot` field from `schema_version` through `provenance`, including complete nested
`important_events`, `recent_semantic_turns`, and provenance; the hash is not itself a field. The
canonicalizer rejects missing/extra nested fields and validates all tuple orders before hashing.
`DiscussionCapture.evidence` must byte-equal `state.important_events`. `capture_id` is SHA-256 of the
canonical `DiscussionCapture` fields other than `capture_id`, after `state_sha256` is populated,
including the complete context, state, trigger, and repeated evidence tuple. `capture_ordinal` is a
positive per-store counter advanced only when a capture is successfully returned. Therefore repeated
authorized inputs remain byte-identical at the state layer while separate calls still receive
distinct, deterministic, bounded correlation IDs. `DiscussionTrigger.source` is required only for
`PEER_CHAT`, must be one projected authorized peer `ChatRecord` classified `PUBLIC` or
`AUTHORIZED_PRIVATE`, and is absent for the other closed trigger kinds. A `VISIBILITY_LOST` marker
is never a peer-chat trigger.

Absence means unknown; no neutral numeric belief is fabricated. Assessments may target only current
snapshot player IDs other than self. Relation endpoints must be distinct current player IDs.
`DISTANCES_FROM` is only a public-evidence hypothesis (the ROADMAP's line-cutting item), never actual
team knowledge. `AUTHORIZED_PRIVATE` and `VISIBILITY_LOST` evidence may update the owning private
state and may be cited by private semantic fields when they inform an action. Neither may satisfy a
public-inference/public-claim precondition or become `PUBLIC` merely because bounded generated chat
or a received-handle action was sent. Their references and raw contents are never serialized into a
network action or another client's context.

Hard defaults/ceilings are: 32 players/assessments, 32 claim assessments, 32 relations, 8 evidence
references per state item or semantic turn, 4 focus players, 32 important events, 16 recent semantic
turns, 16 KiB per semantic proposal, and 64 KiB per canonical state serialization. Event actor/target
arrays are each at most 32. Config may lower but never raise an absolute ceiling. State has no
wall-clock value or random UUID, so the same initial context and same ordered authorized inputs
serialize byte-for-byte identically using UTF-8 canonical JSON (`sort_keys`, compact separators, no
NaN). Important events are stored chronologically by `(source.order, source.record_kind)`; their
bounded authority selection is the trigger/newest/importance union in §5.1 and restores that
chronological order before storage. They are therefore not an importance-only top-32 list.
Assessments are ordered by
player ID, claims by claim identity, relations by endpoints/relation, focus IDs lexically, and every
evidence tuple by `(order, record_kind)`; duplicates or any alternative order are invalid.

### 4.2 State lifecycle

- One store belongs to one `(game_id, player_id, context_sha256)` and one event loop. It is never
  shared across clients or games and has at most one staged proposal because the existing arbiter has
  one active Brain invocation.
- A new runtime/game starts empty with `epoch=0`. The first matching authoritative sync atomically
  builds deterministic fact memory. Model assessments/claims/relations/strategy start absent.
- Incremental records are folded once by `(record_kind, order)`. Exact replay is a no-op; the same key
  with different canonical bytes is terminal corruption.
- A clean reconnect with a complete contiguous recovery barrier preserves state and deduplicates
  replay. A connection-generation change with a gap/floor/incomplete recovery increments `epoch`,
  aborts the staged proposal, clears model-derived state, and atomically rebuilds fact memory from
  the new authorized sync. It never restores an evicted fact from model prose.
- The sealed authorized-channel descriptor tuple is immutable for the runtime and survives a clean
  reconnect and phase change through the retained context. A changed tuple/hash or the already-
  prohibited later modifier-set change is terminal context mismatch, never an in-place visibility
  reclassification.
- During one uninterrupted epoch, an already observed important record may remain as bounded
  `MEMORIZED_AFTER_WORLD_EVICTION`. A later authoritative rebuild retains only records present in
  the new views and marks prefix incompleteness explicitly.
- Phase change retains game-scoped assessments/claims/relations and game-scoped strategy, clears
  phase-scoped strategy/reaction state, and invalidates every capture from the previous phase.
- `ENDED`, `FAILED`, stop, or context mismatch aborts staged work and freezes the last snapshot. A
  process restart does not reload private state or audit; it creates a new store and records
  `model_state_reset=true` while rebuilding only deterministic facts from authoritative views.

`WorldSnapshot.complete`, `HistoryView.complete`, `CoView.complete`, and
`AbilityResultView.complete` remain separate. No one is substituted for another.

## 5. Authorized evidence, importance, and reaction score

The evidence universe is the de-duplicated union of typed `history`, `co`, and `ability_results`
views. Identity is `(record kind, order)`. Equal duplicates are one record; unequal duplicates fail
projection. Visibility is derived exactly by the §4 kind/descriptor matrix, never selected by the
model.

`ImportantEvent` is a bounded deterministic structural summary: identity, day/phase, actor/target
IDs when present, channel, the three-value visibility, bounded text excerpt when the authorized
record has text, original text scalar/byte counts, and a truncation flag. It does not contain a
generated explanation.

Importance is an integer clamped to 0–100:

- 90: game lifecycle, death, vote result, or public notification;
- 80: CO declaration/report or own authorized ability result;
- 70: vote reveal, phase transition/timing change, or random tie result;
- 40: chat;
- 20: another modeled record or an explicit unknown/unmodeled marker;
- add 10 when self is an actor/target, add 10 for an exact self ID/display-name mention in an
  authorized chat, and add 20 when the record is the current reaction trigger.

This formula contains no role/team/model branch. Stable selection order is score descending, record
order descending, then record-kind lexicographic; selected output is finally chronological by order
then kind.

### 5.1 Capture evidence-authority selection

T192 replaces the former importance-only state selection with one bounded union inside the existing
32-event field. It does not add a capture field or a second evidence authority. Let the chronological
identity key be `(source.order, source.record_kind.value)`, the newest rank be order descending then
record-kind value ascending, and the existing importance rank be importance descending, order
descending, then record-kind value ascending.

For each successful `capture()` the store performs these exact steps:

1. Normalize the complete currently retained, recipient-authorized `history`, `co`, and
   `ability_results` views by `(record_kind, order)`. Canonically equal duplicates collapse to one;
   unequal duplicates remain terminal corruption. Build `current` from those normalized records.
   Build `remembered` only from the prior bounded important-event tuple whose identities are absent
   from `current`, setting `remembered_after_world_eviction=true`; a current record of the same
   identity replaces the remembered form and has that flag false.
2. If a peer trigger exists, resolve it to one byte-equal event in `current`. A remembered-only,
   missing, unequal, non-chat, self-authored, or unauthorized source retains the existing fail-closed
   peer-trigger behavior. Call that optional singleton `trigger`.
3. `newest_ceiling` is the first at most **12** events from `current` under the newest rank. This is
   the hard Phase 6 ceiling set, not the later prompt's possibly lowered count and never includes a
   remembered-only event.
4. From the union of current and remembered candidates, remove the identities in `trigger` and
   `newest_ceiling`; select the first at most **12** remaining events under the existing importance
   rank as `older_ceiling`.
5. The protected authority union is `trigger ∪ newest_ceiling ∪ older_ceiling`, de-duplicated by
   evidence identity. Its maximum cardinality is **25**: one non-overlapping trigger, 12 newest, and
   12 additional older importance-ranked events. If its actual cardinality exceeds
   `DiscussionStateConfig.max_important_events`, invalidate the prior current capture and staged
   work, leave the last committed state, revision, fact revision, fingerprint journal, epoch, and
   capture ordinal unchanged, and raise
   `DiscussionStateError("configured event bound cannot retain required projection evidence")`
   before returning a capture. The existing controller/runtime capture-failure path then makes zero
   backend, stage, commit, or send calls. It must not truncate any protected member.
6. Otherwise include the complete protected union, then fill remaining configured slots—up to the
   unchanged absolute maximum 32—from all remaining current/remembered candidates under the
   importance rank. Finally sort the selected tuple by the chronological identity key. Thus the
   selected count is `min(max_important_events, candidate_count)` only after the protected union is
   proven to fit. The protected union is never text-trimmed or member-trimmed again to satisfy
   `max_state_bytes`; if the complete next state crosses that configured/hard 64-KiB gate, use the
   same prior-work invalidation and atomic no-capture result, with the existing
   `DiscussionStateError("discussion state exceeds configured byte limit")`.

With defaults, a non-overlapping trigger plus both 12-record ceiling sets uses at most 25 slots and
leaves at least seven slots for the ordinary importance fill; an absent trigger or a trigger already
in the newest set leaves at least eight. A lowered P6-A event capacity never lowers the protected
quotas or silently changes their meaning: an empty candidate universe may still produce an empty
capture at capacity zero, while any actual protected union that does not fit is the atomic failure
above.

The P6-B prompt configuration remains independently lower-only: newest `N <= 12`, older important
`O <= 12`, and combined unique `U <= 24`. It does not feed back into or renegotiate capture
authority. The hard-ceiling union is a provable superset for every such lower configuration: events
in the 12-newest ceiling but outside a smaller `N` are already present when P6-B ranks its older
pool, and the 12 best remaining candidates are also present. This avoids hidden cross-config state
and keeps the frozen capture schema unchanged.

`DiscussionCapture.evidence` still byte-equals `state.important_events`. No field, enum,
schema-version, canonicalizer, digest input, or hard byte/count ceiling changes. The revised selected
tuple can intentionally change `fact_revision`, `state_sha256`, and `capture_id`; those existing
algorithms cover the complete tuple. `context_sha256`, proposal hashing, stage/CAS, phase/recovery
invalidation, and replay rules remain unchanged. Only already-authorized bounded records enter the
union, so including a newest authorized-private record expands neither its audience nor any network,
terminal, public-log, or cross-client surface.

### 5.2 Alternatives rejected by T192

- **Selected: bounded union in the existing field.** The protected set is at most 25 and therefore
  fits below the existing absolute 32-event/64-KiB gates at the default configuration. It preserves
  one evidence universe, byte-equal proposal references, the existing stage authorization check,
  and all existing capture/state fields and digest algorithms. The remaining default slots still
  use the old importance rank.
- **Rejected: a separate bounded capture-projection evidence field while state remains
  importance-only.** That route would change the frozen capture schema/version and every canonical
  capture/hash constructor, create two overlapping evidence universes for stage and semantic
  validation, and duplicate authorized-private material. Those costs buy no additional capacity or
  product behavior because the exact required union already fits the existing field.
- **Rejected: P6-B intersects newest records with the old importance-only capture or silently drops
  missing identities.** The 40-record vector would still lose chats 33–40 and would weaken the
  newest-record contract without an observable failure.
- **Rejected: P6-B projects raw World JSON, relaxes `stage()`, or adds a post-hash overlay.** Raw JSON
  is not capture authorization; each route breaks byte-equal `EvidenceRef`, visibility, state/capture
  hashing, replay, and CAS authority. Increasing or unbounding the 32-event capture is also
  unnecessary and would expand memory/privacy exposure.

This is an internal evidence-selection correction, not a game/product choice. It changes no server,
content, protocol, World, model/provider, D069, role/channel rule, or natural-language acceptance.

For a peer-chat call, `semantic_reaction_score` (0–100), reason enum, and the exact trigger evidence
reference are required in the semantic proposal. The score is model-produced but structurally tied
to the authorized trigger and applied deterministically. It is **not** Phase 5
`event_importance`: it cannot affect frequency probability, cooldown, jitter, evaluation count,
admission priority, or the D069 CHAT cap. A peer source that is missing, no longer retained, stale,
wrong-channel,
self-authored, or not included in the final projection suppresses the call before the backend.
An `AUTHORIZED_PRIVATE` peer chat remains eligible under the existing same-authorized-channel
Reaction boundary. Any emitted `ChatDecision` must use the same opaque channel carried by both the
source and current received handle; a different or public channel fails before state mutation/send.
This equality check adds no opportunity and cannot authorize public inference from the private
source.

## 6. Bounded memory and prompt projection

`DiscussionPromptConfig` defaults to and may only lower these absolute ceilings:

| Bound | Ceiling |
|---|---:|
| important old summaries | 12 records |
| newest records | 12 records |
| combined unique records | 24 records |
| one important summary | 768 UTF-8 bytes / 160 text scalars |
| one recent record | 2,048 UTF-8 bytes / 512 text scalars |
| combined memory section | 16 KiB |
| projected context | 8 KiB |
| projected cognitive state | 8 KiB |
| complete semantic proposal | 16 KiB |
| provider-independent token proxy | 8,192 units |
| complete prompt | existing 32,768 UTF-8 bytes |

The token proxy is calculated before a backend call as follows: every maximal ASCII
letter/digit/underscore run costs `ceil(bytes/4)` units; each other ASCII scalar costs one; every
non-ASCII scalar costs its UTF-8 byte length. It is a deterministic conservative planning proxy, not
the provider tokenizer or a claim about cache reuse. Provider-reported token counts remain
post-call evidence only.

The effective complete-byte limit is
`min(32_768, LLMBrainConfig.max_prompt_bytes, DiscussionPromptConfig.max_prompt_bytes)`, and the
effective proxy limit is `min(8_192, DiscussionPromptConfig.max_token_proxy_units)`. A legacy
`LLMBrainConfig.max_prompt_bytes` above 32,768 is accepted but cannot raise the Phase 6 ceiling; a
value below it lowers the limit. Every configured section/count/text limit is likewise the lower of
its configured value and the hard ceiling in the table. Bytes and proxy units are always measured
over the same complete `canonical_prompt_json(messages, output_schema)` representation used for
`prompt_sha256`, not just the user message.

Projection order is exact:

1. Validate/bind context and atomically fold the coherent authorized views.
2. Start with an empty projection and add, in this order, the static system instruction, closed
   request-local schema, complete context, current received action options, lifecycle/completeness
   markers, and the minimum state identity/revision fields. After **each** addition, canonicalize the
   complete candidate and debit both UTF-8 bytes and proxy units. These items are mandatory; if any
   individual section, effective complete-byte limit, or effective proxy limit is exceeded, fail
   before a backend call. Context is never partially projected.
3. Normalize and de-duplicate records and reserve the exact current peer trigger when one exists.
   Add that trigger next under both budgets; inability to fit it suppresses/fails the reaction call,
   never silently drops the source.
4. Add optional cognitive-state items in stable priority order: phase strategy, game strategy,
   assessments by confidence descending/player ID, claims by confidence descending/claim identity,
   then relations by confidence descending/endpoints. For every candidate, recompute the complete
   byte/proxy totals; omit that item and all lower-priority state items when either budget or the
   8-KiB state-section limit would be crossed. Record exact per-kind omitted counts.
5. From the §5.1-authorized capture tuple select the configured newest `N` only among events with
   `remembered_after_world_eviction=false`, using order descending then record-kind value ascending.
   After removing the reserved trigger and those newest identities, select the configured older `O`
   by the importance rank. Try candidates in the exact order trigger, newest newest-first, then older
   importance-first, de-duplicating before applying the configured combined-unique limit `U`. At the
   defaults, a trigger outside the newest set creates 25 candidates; because `U=24`, the twelfth
   older candidate is deterministically omitted after the trigger, 12 newest, and first 11 older
   candidates. For every candidate, recompute both complete totals and the per-record/memory-section
   totals. The trigger is mandatory and failure to retain or fit it is `PROMPT_INVALID` or
   `PROMPT_TOO_LARGE` with no backend call; other non-fitting/count-excess candidates are omitted
   with exact counters. Publish accepted items chronologically. Records represented here are
   referenced rather than duplicated in CO/ability sections. A semantic reference absent from this
   final projection invalidates the whole output before `stage()`; P6-B never recovers it from raw
   JSON or bypasses the capture authority.
6. Canonicalize the final messages and schema again. Recompute every count, scalar, section-byte,
   complete-byte, and proxy total from the emitted representation; verify `context_sha256` and
   `state_sha256`; then compute `prompt_sha256`. Any mismatch or any total above its effective limit
   is `PROMPT_TOO_LARGE`/`PROMPT_INVALID` with no backend call. No truncation or mutation occurs
   after this recheck/hash.
7. A repair request runs the same algorithm over the original immutable projection plus the bounded
   invalid-output excerpt and mandatory repair instruction. It may omit/truncate only that excerpt
   under its existing bound; if the complete repaired bytes or proxy units do not fit, repair is not
   called and the invocation fails closed.

### 6.1 T380承認addendum: repair envelopeの常時予約

step 4–5でoptionalなstate/memoryを追加する際は、元projectionへ必須の空repair turnを追加した場合の
完全なUTF-8 byte数とproxy unitsを、effective limitから常に予約して判定する。予約量は観測定数へ
固定せず、closedな全validation code、空excerpt、truncated booleanの両値、SHA-256の64 hex、
`invalid_output_original_scalars`と`invalid_output_original_utf8_bytes`を`sys.maxsize`として、既存の
sort/separator/UTF-8規則で実serializeした最大差分から算出する。projection開始時に一度算出し、
各optional候補で再計算しない。mandatory context/options/lifecycle/minimum state identityとPEER triggerは
削除しないため、これらと予約を合わせて収まらない場合は従来どおりbackend前に
`PROMPT_TOO_LARGE`とする。8,192 proxy units、32,768 bytes、section上限、縮約順序、repair 1回の上限は
変更しない。

The user JSON contains separate markers for World-prefix loss, visibility-lost and unknown/malformed
interpretation, CO completeness, ability completeness, remembered-after-eviction records, prompt
record omission, per-text truncation, memory-byte exhaustion, token-proxy exhaustion, and state-projection omission.
Counts and dropped-through orders are included. There is no context-omission marker: missing or
oversize canonical context is invalid. `complete=true` is asserted only for its own layer. The static
system instruction is byte-identical across roles/games; names, IDs, context, state, history,
excerpts, and repair material remain untrusted dynamic JSON. No model call summarizes data.

## 7. Strict semantic result

The provider returns the existing closed action branch with one required `discussion` object. The
parser validates both branches as one unit and returns the following wrapper; this makes the
proposal available to the controller without attaching private cognition to a network decision.

```python
class SpeechActKind(str, Enum):
    NONE = "NONE"
    CLAIM = "CLAIM"
    QUESTION = "QUESTION"
    ANSWER = "ANSWER"
    REBUTTAL = "REBUTTAL"
    OPINION_CHANGE = "OPINION_CHANGE"
    RELATION_HYPOTHESIS = "RELATION_HYPOTHESIS"

@dataclass(frozen=True)
class DiscussionProposal:
    schema_version: Literal["aiwolf.discussion-proposal.v1"]
    base_revision: int
    decision_kind: Literal["none", "chat", "vote", "ability", "co_declare"]
    option_id: str | None
    speech_act: "SpeechAct"             # closed tagged union
    reaction: "ReactionAssessment | None"
    assessment_updates: tuple["AssessmentUpdate", ...]
    claim_updates: tuple["ClaimUpdate", ...]
    relation_updates: tuple["RelationUpdate", ...]
    strategy_update: "StrategyUpdate | None"
    co_judgment: "CoJudgment | None"
    pre_vote_reassessment: "PreVoteReassessment | None"

@dataclass(frozen=True)
class BrainResult:
    decision: BrainDecision
    discussion: DiscussionProposal | None
    audit_ack: "DiscussionGenerationAck | None"

BrainOutput: TypeAlias = BrainDecision | BrainResult
```

`decision_kind` and `option_id` are required model-output fields inside every proposal and are part
of its closed schema. Their only valid pairings with the simultaneously parsed network decision and
the request-local offered handle are:

| `BrainDecision` | proposal identity | offered-handle requirement |
|---|---|---|
| `NoDecision` | `decision_kind="none"`, `option_id=null` | no option is selected |
| `ChatDecision` | `decision_kind="chat"`, `option_id == decision.option_id` | that exact option is a received `ChatAction` |
| `VoteDecision` | `decision_kind="vote"`, `option_id == decision.option_id` | that exact option is a received `VoteAction` |
| `AbilityDecision` | `decision_kind="ability"`, `option_id == decision.option_id` | that exact option is a received `AbilityAction` |
| `CoDeclareDecision` | `decision_kind="co_declare"`, `option_id == decision.option_id` | that exact option is a received `CoDeclareAction` |

The `none` branch alone requires a null option; every action branch requires one non-empty bounded
option ID. The strict parser validates the action, proposal identity, and exact offered handle as one
unit before constructing a successful generation record. `BrainResult` and `BrainController`
repeat the decision/proposal identity check before `stage()`. Any changed kind, nullability,
option ID, or handle family invalidates the complete result before stage, commit, or send. It is
never repaired by copying the network decision into the proposal. A contextful `CoReportDecision`
is invalid because the Phase 6 identity vocabulary and provider schema deliberately omit
`co_report`; the existing raw context-free `CoReportDecision` compatibility is unchanged.

`NoDecision` still carries and commits one valid semantic proposal, may contain the already-allowed
private assessment/strategy/CO/pre-vote updates, follows `stage` → `commit` → `finish_no_action`,
and sends nothing. This clarification adds no new relationship between `NoDecision` and a particular
`speech_act` branch; all existing §7 trigger-specific semantic rules remain unchanged.

`Brain.decide()` returns `BrainOutput` during the compatibility transition. For a request with
`BrainInput.discussion is None`, the controller accepts the existing raw `BrainDecision`; existing
deterministic/fake Brains therefore remain unchanged. For a request with a non-null discussion
capture, only `BrainResult` with a valid proposal is accepted, including when `decision` is
`NoDecision`; a raw decision or a missing branch is an invalid final output. `LLMBrain` preserves
its legacy raw return for a context-free request and returns the wrapper for Phase 6. The controller
requires the matching durable `audit_ack`, dispatches only `result.decision`, and stages only
`result.discussion`.

The referenced subobjects are closed tagged objects with these exact semantic fields (all evidence
fields are bounded tuples of `EvidenceRef`; `None` is permitted only where shown):

| Object/branch | Exact semantic fields after its tag |
|---|---|
| `SpeechAct.NONE` | none |
| `SpeechAct.CLAIM` | `subject_player_id`, `topic`, `stance`, `evidence` |
| `SpeechAct.QUESTION` | `addressee_player_id`, nullable `subject_player_id`, `topic`, nullable `source` |
| `SpeechAct.ANSWER` | `addressee_player_id`, `in_reply_to`, `source_interpretation="QUESTION"`, `topic`, `stance`, `evidence` |
| `SpeechAct.REBUTTAL` | `addressee_player_id`, `in_reply_to`, `source_interpretation="CLAIM"`, `topic`, `stance`, `evidence` |
| `SpeechAct.OPINION_CHANGE` | `subject_player_id`, `dimension`, `prior`, `current`, `causes` |
| `SpeechAct.RELATION_HYPOTHESIS` | `source_player_id`, `target_player_id`, `relation`, `confidence`, `evidence` |
| `AssessmentUpdate` | `target_player_id`, `suspicion`, `credibility`, `confidence`, `evidence` |
| `ClaimUpdate` | `claim`, `speaker_player_id`, `verdict`, `confidence`, `evidence` |
| `RelationUpdate` | `source_player_id`, `target_player_id`, `relation`, `confidence`, `evidence`, `provenance="PUBLIC_INFERENCE"` |
| `StrategyUpdate` | `scope`, `mode`, `focus_player_ids`, `evidence` |
| `ReactionAssessment` | `trigger`, `score`, `reason` |
| `CoJudgment` | `decision`, nullable `selected_option_id`, nullable `claimed_role_id` |
| `PreVoteReassessment` | `option_id`, `ranked_target_player_ids`, nullable `preferred_target_player_id`, `evidence` |

`topic` is one of `ALIGNMENT`, `ROLE_CLAIM`, `VOTE`, `EVENT`, `RELATION`, or `STRATEGY`;
`stance` is `SUPPORT`, `OPPOSE`, or `UNCERTAIN`; `dimension` is `SUSPICION` or `CREDIBILITY`.
`prior` and `current` are integers 0–100. `ReactionAssessment.reason` is one of
`DIRECT_QUESTION`, `DIRECT_MENTION`, `CLAIM_CONFLICT`, `VOTE_PRESSURE`, `NEW_INFORMATION`, or
`OTHER_AUTHORIZED`. `CoJudgment.decision` is `DECLARE`, `SILENCE`, or `DEFER`. These are generic
semantic categories; no concrete role name or role-specific branch is introduced. Natural-language
message/comment text remains solely in the existing network decision and is not copied into these
objects.

Every object uses `additionalProperties:false`; tagged-union branches have exact required keys.
There are at most four assessment/claim updates, two relation updates, one strategy update, one CO
judgment, one pre-vote reassessment, and eight total distinct evidence references. Scores/confidence
are integers 0–100; bool/float/NaN are rejected. IDs must be exact current player/context/option IDs.
Evidence must be in the final projection. Relation hypotheses about other players and claim verdicts
asserted as public inference may cite only `PUBLIC` evidence; other private proposal fields may cite
`AUTHORIZED_PRIVATE` or `VISIBILITY_LOST` evidence, but references and raw evidence never enter the
network payload. The proposal's visibility must exactly equal the captured reference; any attempted
upgrade or downgrade invalidates the whole result.
Duplicate targets/relations/references, absent/extra keys, an omitted or unknown source, a stale base
revision, and an unoffered action/target/claim ID are invalid.

Record attribution is mechanical, never model-selected. `authorized_actor(record)` returns
`ChatRecord.player_id`, `CoDeclarationRecord.player_id`, or `CoReportRecord.player_id` for those
typed records and returns no actor for every system/lifecycle/result record. For `ANSWER` and
`REBUTTAL`, the referenced record must have a non-null current-player actor other than self and
`addressee_player_id` must equal that actor. For every `ClaimUpdate`, `claim` must identify a
projected `PUBLIC` Chat/CO record with a non-self actor and `speaker_player_id` must equal that actor;
this is the intentionally public-only claim-verdict path.
A missing/system actor, self-source in either of these peer-attribution paths, or unequal opaque ID
invalidates the entire provider result before stage/send. A typed CO declaration/report follows the
same actor rule; its payload's target is not substituted for its speaker.

Semantic relationships are exact:

- `ANSWER` and `REBUTTAL` name one earlier projected authorized peer Chat/CO record and carry respectively
  `source_interpretation="QUESTION"` or `"CLAIM"`. Code validates identity, visibility, ordering,
  projection membership, and the actor binding above; it does not pretend the wire already carries
  peer semantic tags. `source_interpretation` remains untrusted private model inference: it is not a
  wire fact, visibility upgrade, authorization input, server truth, or source for action
  availability. Deterministic paired fixtures and the private human review validate semantic
  relevance;
- `OPINION_CHANGE` supplies the current stored prior value, a different new value, and at least one
  newly included cause reference;
- `RELATION_HYPOTHESIS` uses only `PUBLIC_INFERENCE`; no output can create authorized knowledge;
- a peer-reaction chat must reference the exact trigger and include a reaction assessment. For an
  `AUTHORIZED_PRIVATE` chat, the emitted decision's opaque channel must equal both source and current
  received-handle channel and no public-inference update may cite it; a silent `NoDecision` may still
  contain a valid private update;
- a CO opportunity requires `CoJudgment(DECLARE|SILENCE|DEFER)`. `DECLARE` requires an offered
  `selected_option_id` and matching offered `claimed_role_id`, and must match the emitted
  `CoDeclareDecision`; silence/defer requires both nullable fields absent and `NoDecision`.
  Generated `CoReportDecision` remains absent because the current action lacks report
  kind/target/result vocabulary;
- a vote opportunity requires one `PreVoteReassessment` tied to the current vote `option_id`.
  Ranked targets are a duplicate-free subset of that handle's offered targets. A non-null preferred
  target is first in the ranking and must match the emitted vote; abstention/`NoDecision` requires a
  null preferred target and must be permitted by the received handle.

Strategy, CO judgment, and pre-vote reassessment are typed subobjects, not extra speeches or model
calls. Chat text remains under the approved short-chat bounds. Schema repair remains at most one
attempt inside the same admission lease/CHAT invocation. Final invalidity yields zero state commit
and zero send; no retry or fallback is added.

## 8. Capture and state transaction

`BrainInput` gains optional `discussion: DiscussionCapture | None` for mechanical compatibility;
the Phase 6 runtime requires it. `DispatchDeadline` gains optional
`discussion_trigger: DiscussionTrigger | None`; Reaction and VoteAbility create this typed value and
the existing arbiter forwards the same deadline unchanged. `BrainController.capture_input()` gives
that trigger and its coherent World views to the store. Reaction passes only the peer record
identity, not copied raw text; the retained record supplies text. Vote passes `PRE_VOTE`, CO passes
`CO_OPPORTUNITY`, and ability passes `ABILITY`.

One `Phase5ClientRuntime` owns exactly one store and the existing one `JsonlAiAuditSink`, and passes
the same objects to `LLMBrain`, `BrainController`, `ReactionChatController`, and
`VoteAbilityController`. Only the audit sink's existing FIFO writer touches `ai.jsonl`; controllers
submit typed entries and never open the file.

The correlation and acknowledgement values are closed frozen records:

```python
class AiDiscussionGenerationStatus(str, Enum):
    PROMPT_REJECTED = "PROMPT_REJECTED"
    BACKEND_FAILED = "BACKEND_FAILED"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    DECISION = "DECISION"
    EXPLICIT_NO_DECISION = "EXPLICIT_NO_DECISION"
    REPAIR_SUCCEEDED = "REPAIR_SUCCEEDED"
    REPAIR_FAILED = "REPAIR_FAILED"
    CANCELLED = "CANCELLED"

class DiscussionAbortReason(str, Enum):
    PROMPT_REJECTED = "PROMPT_REJECTED"
    BACKEND_FAILED = "BACKEND_FAILED"
    FINAL_OUTPUT_INVALID = "FINAL_OUTPUT_INVALID"
    CANCELLED_BEFORE_RESULT = "CANCELLED_BEFORE_RESULT"
    AUDIT_FAILED = "AUDIT_FAILED"
    STAGE_FAILED = "STAGE_FAILED"
    CANCELLED_AFTER_STAGE = "CANCELLED_AFTER_STAGE"
    STALE = "STALE"
    DEADLINE = "DEADLINE"
    REVISION_CONFLICT = "REVISION_CONFLICT"

class DiscussionDeliveryStatus(str, Enum):
    NO_ACTION = "NO_ACTION"
    NOT_DELIVERED = "NOT_DELIVERED"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"
    LOCAL_SENT = "LOCAL_SENT"

class DiscussionObservationStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    RECOVERY_UNKNOWN = "RECOVERY_UNKNOWN"

class DiscussionTerminalStatus(str, Enum):
    ABORTED = "ABORTED"
    NO_ACTION = "NO_ACTION"
    NOT_DELIVERED = "NOT_DELIVERED"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    RECOVERY_UNKNOWN = "RECOVERY_UNKNOWN"

class DiscussionTerminalReason(str, Enum):
    PROMPT_REJECTED = "PROMPT_REJECTED"
    BACKEND_FAILED = "BACKEND_FAILED"
    FINAL_OUTPUT_INVALID = "FINAL_OUTPUT_INVALID"
    CANCELLED_BEFORE_RESULT = "CANCELLED_BEFORE_RESULT"
    STAGE_FAILED = "STAGE_FAILED"
    CANCELLED_AFTER_STAGE = "CANCELLED_AFTER_STAGE"
    STALE = "STALE"
    DEADLINE = "DEADLINE"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    EXPLICIT_NO_DECISION = "EXPLICIT_NO_DECISION"
    SEND_NOT_DELIVERED = "SEND_NOT_DELIVERED"
    SEND_DELIVERY_UNKNOWN = "SEND_DELIVERY_UNKNOWN"
    AUTHORITATIVE_ACCEPTED = "AUTHORITATIVE_ACCEPTED"
    AUTHORITATIVE_REJECTED = "AUTHORITATIVE_REJECTED"
    AUTHORITATIVE_AMBIGUOUS = "AUTHORITATIVE_AMBIGUOUS"
    RECOVERY_GAP = "RECOVERY_GAP"
    PHASE_CHANGED = "PHASE_CHANGED"
    OWNER_STOPPED = "OWNER_STOPPED"

@dataclass(frozen=True)
class DiscussionGenerationAck:
    capture_id: str
    request_id: str
    final_attempt_ordinal: Literal[1, 2]
    audit_sequence: int
    generation_record_sha256: str
    generation_status: AiDiscussionGenerationStatus
    context_sha256: str
    before_state_sha256: str
    after_state_sha256: Literal[None]
    proposal_sha256: str | None
    durable: Literal[True]

@dataclass(frozen=True)
class StageAck:
    capture_id: str
    request_id: str
    base_revision: int
    context_sha256: str
    before_state_sha256: str
    after_state_sha256: Literal[None]
    proposal_sha256: str
    staged: Literal[True]

@dataclass(frozen=True)
class CommitAck:
    capture_id: str
    request_id: str
    base_revision: int
    committed_revision: int
    context_sha256: str
    before_state_sha256: str
    after_state_sha256: str
    proposal_sha256: str
    committed: Literal[True]

@dataclass(frozen=True)
class DispatchAck:
    capture_id: str
    request_id: str
    base_revision: int
    committed_revision: int
    context_sha256: str
    before_state_sha256: str
    after_state_sha256: str
    proposal_sha256: str
    action: Literal["chat", "vote", "ability", "co_declare"]
    option_id: str

@dataclass(frozen=True)
class AbortAck:
    capture_id: str
    request_id: str
    base_revision: int
    context_sha256: str
    before_state_sha256: str
    after_state_sha256: Literal[None]
    proposal_sha256: str | None
    reason: DiscussionAbortReason
    stage_existed: bool
    aborted: Literal[True]

@dataclass(frozen=True)
class DeliveryAck:
    capture_id: str
    request_id: str
    base_revision: int
    committed_revision: int
    context_sha256: str
    before_state_sha256: str
    after_state_sha256: str
    proposal_sha256: str
    status: DiscussionDeliveryStatus
    action: Literal["chat", "vote", "ability", "co_declare"] | None
    option_id: str | None
    request_event_id: str | None
    send_connection_generation: int | None
    correlation: "DiscussionDispatchCorrelation | None"

@dataclass(frozen=True)
class ObservationAck:
    capture_id: str
    request_id: str
    base_revision: int
    committed_revision: int
    context_sha256: str
    before_state_sha256: str
    after_state_sha256: str
    proposal_sha256: str
    generation_audit_sequence: int
    action: Literal["chat", "vote", "ability", "co_declare"]
    option_id: str
    request_event_id: str
    send_connection_generation: int
    status: DiscussionObservationStatus
    authoritative_evidence: EvidenceRef | None

@dataclass(frozen=True)
class DiscussionDispatchCorrelation:
    capture_id: str
    request_id: str
    context_sha256: str
    before_state_sha256: str
    after_state_sha256: str
    proposal_sha256: str
    generation_audit_sequence: int
    base_revision: int
    committed_revision: int
    action: Literal["chat", "vote", "ability", "co_declare"]
    option_id: str
    request_event_id: str
    send_connection_generation: int
```

For a Phase 6 capture, `request_id` is exactly `"phase6:" + capture_id`; `LLMBrain` does not call
its internal request-ID factory on that path. `proposal_sha256` is SHA-256 of the complete canonical
`DiscussionProposal`, including `decision_kind` and nullable `option_id`, without any hash field.
The successful generation record's `AiAuditDecision` must match those two fields exactly:
`DECISION` requires one of the four action identities, `EXPLICIT_NO_DECISION` requires
`none`/null, and `REPAIR_SUCCEEDED` permits either matching shape. A contextful `co_report`, a
mismatch, or a success record whose identity does not match its proposal is invalid and cannot yield
a `DiscussionGenerationAck`. Every hash is lowercase 64-hex and every acknowledgement
constructor rechecks its linked IDs/hashes. The store rejects an unknown ID, wrong request ID,
wrong digest, duplicate stage/commit/dispatch, or revision mismatch without mutation.

Every digest above is lowercase 64-hex. `DiscussionGenerationAck.after_state_sha256` and
`StageAck.after_state_sha256` are always null because neither proves a commit. A generation ack's
proposal digest is present exactly for `DECISION`, `EXPLICIT_NO_DECISION`, or `REPAIR_SUCCEEDED` and
null for every other generation status. An abort ack always carries context/before-state, never an
after-state. `PROMPT_REJECTED`, `BACKEND_FAILED`, `FINAL_OUTPUT_INVALID`,
`CANCELLED_BEFORE_RESULT`, and `AUDIT_FAILED` require `stage_existed=false` and a null proposal;
`STAGE_FAILED` requires `stage_existed=false` and a non-null proposal; the four after-stage reasons
require `stage_existed=true` and a non-null proposal.

`DeliveryAck` always carries all four correlation hashes. `NO_ACTION` requires null action, option,
receipt, and correlation fields. The other statuses require action and option. Receipt fields and
the byte-equal populated correlation are present exactly for `LOCAL_SENT`; receipt and correlation
are null for `NOT_DELIVERED`/`DELIVERY_UNKNOWN`.
`ObservationAck` repeats all four hashes and the non-null exact local receipt. Evidence is required
for `ACCEPTED`/`REJECTED`; for `RECOVERY_UNKNOWN` it is either one authorized typed reference or null
when a gap/phase-change/stop supplies no retained record. An identical second observation returns
the same ack; any changed field or second terminal status is rejected.

`DecisionOutcome` gains nullable `discussion: DiscussionDispatchCorrelation`. It is required for a
Phase 6 `SENT` outcome and absent for context-free legacy calls. The unchanged
`BrainDispatchResult` carries it through `outcome`; `take_dispatched_decision()` remains the
exactly-once decision handoff. `ReactionOutcome` stores the correlation for a local send and final
observation. `UnresolvedReservation` and `VoteAbilityOutcome` store the same correlation for
vote/ability. No controller reconstructs a key from text, player ID, or timestamps.

```python
class DiscussionStatePort(Protocol):
    def capture(self, views, trigger) -> DiscussionCapture: ...
    def stage(self, capture, request_id, proposal, generation_ack) -> StageAck: ...
    def commit(self, stage_ack) -> CommitAck: ...
    def abort(self, capture_id, request_id, reason, proposal_sha256=None) -> AbortAck: ...
    def finish_no_action(self, commit_ack) -> DeliveryAck: ...
    def mark_dispatch_started(self, commit_ack, action, option_id) -> DispatchAck: ...
    def finish_dispatch(self, dispatch_ack, status, receipt=None) -> DeliveryAck: ...
    def observe_authoritative(self, correlation, status, evidence=None) -> ObservationAck: ...
    def close(self) -> None: ...
```

All port methods construct the frozen values above and reject absent, extra, or branch-incompatible
fields rather than filling sentinels. `finish_dispatch(..., LOCAL_SENT, receipt)` returns the exact
fully populated `DiscussionDispatchCorrelation` through the delivery ack fields; controllers do not
reconstruct it from text, player IDs, or time.

There are two deliberately separate atomic state-mutation layers around one durable generation step:

1. **Fact fold:** `capture()` idempotently advances deterministic authorized memory, increments the
   capture ordinal, hashes the exact state/capture material, and returns the immutable capture. It
   never applies model inference.
2. **Generation:** the contextful `LLMBrain` uses the capture-derived request ID. Strict parse and
   semantic validation produce one decision/proposal pair, compute `proposal_sha256`, append the
   generation record, await its durable `AuditWriteAck`, and only then return a matching
   `BrainResult.audit_ack`. A contextful `LLMInvocationError` carries the durable generation ack
   when one exists. Cancellation appends a bounded `CANCELLED` generation record under shield before
   propagating; an audit-sink failure is the sole case in which no durable ack can be returned.
3. **Model proposal:** `BrainController.stage()` validates the ack and proposal but does not expose
   staged state. After decision/handle validation and the final World/handle/deadline check it calls
   `commit(stage_ack)`, whose base-revision CAS creates the only model-state mutation. Commit is
   immediately followed, with no intervening await, by either `finish_no_action()` or
   `mark_dispatch_started()`. The stage retains the exact proposal; commit derives the new
   `RecentSemanticTurn.decision_kind`, `option_id`, speech-act kind, sorted de-duplicated evidence,
   capture/request IDs, and proposal digest from that retained value, using
   `committed_revision=base_revision+1`. `StageAck`, `CommitAck`, and the port signatures do not gain
   duplicate decision fields because their existing proposal hash binds the complete identity.
   Commit validates the derived turn and complete after-state before the CAS, then makes that state
   and its `after_state_sha256` visible atomically; no field is filled or mutated afterward. It
   records reasoning/intent, never server acceptance.

For an action, `mark_dispatch_started()` runs before awaiting the existing sender and
`finish_dispatch()` records `LOCAL_SENT`, `NOT_DELIVERED`, or `DELIVERY_UNKNOWN`. `SENT` still means
the existing local `SendReceipt`, not server acceptance. Only the outer controller holding the
returned correlation may call `observe_authoritative()`. Vote/ability require their exact
request-event ID and recovery barrier. Chat/CO retain their weaker rule: exactly one newly observed
self-authored typed record must match decision text/fields, day, phase, and channel where applicable;
zero or multiple candidates, a gap, phase change, or ambiguous rejection becomes
`RECOVERY_UNKNOWN`, never acceptance. The matched authorized record enters fact memory on the next
idempotent fold; observation metadata cannot create server truth. Reasoning is not rolled back when
delivery fails because it does not claim delivery.

The same private writer accepts a closed `AiAuditEntry` union. Context-free calls retain
`aiwolf.ai-log.v1`. The two new record schemas are exactly:

```python
@dataclass(frozen=True)
class AiDiscussionGenerationRecord:
    schema_version: Literal["aiwolf.ai-discussion-generation.v1"]
    recorded_at_utc: str
    game_id: str
    player_id: str
    request_id: str
    capture_id: str
    phase: str
    day: int
    world_version: int
    backend: BackendIdentity
    attempt_ordinal: Literal[1, 2]
    prompt_sha256: str
    prompt_bytes: int
    prompt_json: str
    response_sha256: str | None
    response_bytes: int | None
    response_text: str | None
    latency_microseconds: int
    provider_model: str | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    status: AiDiscussionGenerationStatus
    backend_error_code: LLMBackendErrorCode | None
    validation_code: DecisionValidationCode | None
    decision: AiAuditDecision | None
    context_sha256: str
    before_state_sha256: str
    after_state_sha256: Literal[None]
    base_revision: int
    proposal: DiscussionProposal | None
    proposal_sha256: str | None

@dataclass(frozen=True)
class AiDiscussionTerminalRecord:
    schema_version: Literal["aiwolf.ai-discussion-terminal.v1"]
    recorded_at_utc: str
    game_id: str
    player_id: str
    request_id: str
    capture_id: str
    phase: str
    day: int
    final_attempt_ordinal: Literal[1, 2]
    generation_audit_sequence: int
    generation_record_sha256: str
    context_sha256: str
    before_state_sha256: str
    after_state_sha256: str | None
    proposal_sha256: str | None
    base_revision: int
    committed_revision: int | None
    decision_kind: Literal["none", "chat", "vote", "ability", "co_declare"] | None
    option_id: str | None
    status: DiscussionTerminalStatus
    reason: DiscussionTerminalReason
    request_event_id: str | None
    send_connection_generation: int | None
    authoritative_evidence: EvidenceRef | None
```

The generation record preserves every field and nullability rule of the current record, with the
displayed Phase 6 additions. Its context and before-state hashes always equal its capture; after-state
is always null. Response hash/bytes/text are all present exactly for `OUTPUT_INVALID`, `DECISION`,
`EXPLICIT_NO_DECISION`, `REPAIR_SUCCEEDED`, and `REPAIR_FAILED`, and otherwise all null. Provider
metadata is permitted only with a response. Backend error is present exactly for `BACKEND_FAILED`;
validation code exactly for `OUTPUT_INVALID`/`REPAIR_FAILED`. Decision, proposal, and matching
proposal digest are all present exactly for `DECISION`, `EXPLICIT_NO_DECISION`, or
`REPAIR_SUCCEEDED`; they are all null otherwise. `DECISION` requires an action,
`EXPLICIT_NO_DECISION` requires `none`, and repaired success may carry either. `CANCELLED` has no
response/decision/proposal. `generation_record_sha256` in the ack/terminal is SHA-256 of the complete
canonical generation record, excluding the writer sequence because that is assigned by the writer.

The terminal record contains no prompt, response, generated text, proposal body, private evidence
body, or player mapping and is capped at 16 KiB. All terminal rows have non-null context and
before-state hashes plus the final durable generation sequence/hash. Its remaining nullability and
closed reason pairing are:

| Terminal status | Allowed reason(s) | after-state / committed revision | proposal / decision / option | receipt | authoritative evidence |
|---|---|---|---|---|---|
| `ABORTED` before valid result | `PROMPT_REJECTED`, `BACKEND_FAILED`, `FINAL_OUTPUT_INVALID`, `CANCELLED_BEFORE_RESULT` | null / null | null / null / null | both null | null |
| `ABORTED` at `stage()` | `STAGE_FAILED` | null / null | required / required / required iff decision is not `none` | both null | null |
| `ABORTED` after stage | `CANCELLED_AFTER_STAGE`, `STALE`, `DEADLINE`, `REVISION_CONFLICT` | null / null | required / required / required iff decision is not `none` | both null | null |
| `NO_ACTION` | `EXPLICIT_NO_DECISION` | required / required | required / exactly `none` / null | both null | null |
| `NOT_DELIVERED` | `SEND_NOT_DELIVERED` | required / required | required / action / required | both null | null |
| `DELIVERY_UNKNOWN` | `SEND_DELIVERY_UNKNOWN` | required / required | required / action / required | both null | null |
| `ACCEPTED` | `AUTHORITATIVE_ACCEPTED` | required / required | required / action / required | both required | required |
| `REJECTED` | `AUTHORITATIVE_REJECTED` | required / required | required / action / required | both required | required |
| `RECOVERY_UNKNOWN` | `AUTHORITATIVE_AMBIGUOUS`, `RECOVERY_GAP`, `PHASE_CHANGED`, `OWNER_STOPPED` | required / required | required / action / required | both required | optional |

Here “required” for proposal means a lowercase digest; the proposal body itself never enters a
terminal. Receipt fields are an indivisible pair. Every status/reason pair not shown, every wrong
nullability combination, and `AUDIT_FAILED` as a terminal reason is invalid. Audit failure may return
an `AbortAck(reason=AUDIT_FAILED)` but cannot durably write or claim a terminal.

Repair may create two generation records with the same request ID and distinct attempt ordinals,
but exactly one terminal record points to the final durable generation sequence/hash. Every capture
that starts Brain receives exactly one terminal record except when terminal-reservation capacity or
the audit sink/acknowledgement authority fails; those are the one `AUDIT_FAILED` no-terminal class.
Duplicate/conflicting terminal submission is rejected. Such an audit-path failure aborts any
uncommitted work when still possible, fails the runtime, and cannot falsely manufacture a terminal.

### 8.1 Bounded terminal-submission reservation ledger

T198 makes the exactly-once memory in `DiscussionTransaction` explicit and bounded without changing
any terminal row, hash, nullability, sink serialization, or controller ordering. One
`BrainController` constructs and owns exactly one `DiscussionTransaction` for the same one-player,
one-game runtime lifetime as its `DiscussionStateStore`. That transaction owns the sole in-memory
terminal-submission authority; the shared `JsonlAiAuditSink` remains only the FIFO durable writer
and is not queried, rescanned, indexed, or made a second de-duplication authority.

The ledger is exactly a `set[bytes]` of decoded capture digests. Each key is
`bytes.fromhex(record.capture_id)`, therefore exactly 32 bytes. It retains no terminal record,
timestamp, status/reason, request or option ID, generation/proposal hash, evidence, acknowledgement,
prompt, response, text, or player mapping. The hard and default capacity is exactly **512** distinct
capture IDs. `DiscussionTransaction.__init__` gains only the keyword
`max_terminal_reservations: int = 512`; this is an internal lower-only test/composition seam, not a
game or user setting. Validation requires `type(value) is int` and `1 <= value <= 512`, otherwise it
raises exactly
`ValueError("max_terminal_reservations must be an int in [1, 512]")`. Thus `0`, negative values,
`bool`, and values above 512 are invalid at construction; there is no enabled zero-capacity mode.

For every open-transaction `write_terminal(record)` call, the synchronous pre-await order is exact:

1. require one valid `AiDiscussionTerminalRecord` and reject a closed transaction;
2. decode its already-validated lowercase 64-hex `capture_id` to the 32-byte key;
3. test ledger membership **before** testing capacity; an existing key raises the unchanged
   `DiscussionTransactionError("duplicate terminal submission")` whether the supplied record is
   byte-identical or conflicting and whether the first sink write is in flight, durable, cancelled,
   failed, or returned an invalid acknowledgement;
4. if the key is new and the set already has 512 entries (or the configured lower limit), raise
   `DiscussionTransactionError("terminal reservation capacity exhausted")`; do not insert the key,
   construct a sink task, or call the sink; and
5. otherwise add the key and only then construct/await the single existing
   `audit.write(record)` task.

There is no await, task scheduling yield, or sink call between the membership/capacity checks and
the insertion. The existing event-loop owner and one-active-invocation controller make that one
atomic reservation step; direct helper tests and a possible later outer-observation owner still go
through the same `write_terminal()` step. No controller-local active-invocation flag is an
alternative idempotency authority. The existing `discussion_terminal_attempted` guard remains a
second, request-local misuse check, but it neither replaces nor resets the transaction ledger.

A reservation is never removed while the transaction is open. A valid durable acknowledgement
leaves it present. Caller cancellation still shields and owns the one sink task through completion;
after a valid durable acknowledgement the original cancellation propagates, with the reservation
retained. Sink exception, a cancelled sink task, or an invalid acknowledgement also retains the
reservation permanently, because the row may already be durable and retry cannot be proved safe.
Repeated cancellation or any later identical/conflicting call therefore creates no second writer.
There is no FIFO/LRU eviction, TTL, phase/epoch/reconnect reset, audit-file scan, or successful-write
removal.

Capacity exhaustion is the pre-reservation form of the already-approved audit-authority failure;
sink exception/cancellation or invalid acknowledgement is its post-reservation form. Each maps to
the existing `AUDIT_FAILED` **no-terminal exception**, not to a new terminal status or reason:
`AUDIT_FAILED` remains invalid in `AiDiscussionTerminalRecord`, no substitute terminal is submitted,
and no successful `DecisionOutcome` is returned. The controller propagates the
`DiscussionTransactionError` as runtime-fatal and the owning runtime enters its existing failed/
shutdown path. If state remains uncommitted, its existing audit-failure abort/close path discards
staged work. If commit, send, delivery, or authoritative observation already occurred, that fact is
not rolled back or relabelled; the runtime and Phase 6 evidence fail, the reservation remains when
one was made, and no retry or second terminal is permitted. A caller is not allowed to catch either
form and continue the runtime.

`DiscussionTransaction.close()` is a new synchronous, idempotent, one-way ownership boundary. It
sets an internal closed flag before clearing the set, never reopens, never closes the shared sink or
state store, and never cancels an already-owned sink task. A writer already past reservation keeps
the same shield-to-completion behavior; because closed is checked before membership, every direct
`write_terminal()` after close raises exactly
`DiscussionTransactionError("discussion transaction is closed")`, even for a formerly reserved
key. Clearing after the flag is set can therefore release memory without making a key reusable.
`BrainController.stop()`/runtime shutdown invokes this boundary only after new invocation admission
is permanently stopped and the active contextful terminal writer is owned through completion. The
existing outer-owner rule still requires any `LOCAL_SENT` correlation to receive its one
authoritative/recovery terminal before successful owner close; T198 neither implements nor redesigns
P6-C.

Clean phase changes, reconnects, recovery resets, and `DiscussionStateStore.epoch` changes do not
clear or replace the ledger. The capture ordinal is monotonic for the store lifetime and is part of
the capture digest. A new game/runtime/process owns a new state store, controller, transaction, and
ledger; game/context/state material is capture-hashed, and `process_restart=true` is represented in
the rebuilt state provenance. An old controller is permanently stopping/closed, and its transaction
rejects direct writes after close. Thus no reachable production path can reuse a cleared key. Direct
construction/write helpers remain supported only on their still-open transaction and receive the
same membership, capacity, failure-retention, and close checks as the controller path.

The selected 512-entry bound matches the already bounded 512-record private-review population while
keeping the runtime ledger small. Literal retained digest material is
`512 * 32 = 16,384` bytes (16 KiB). On the project execution boundary, CPython 3.13.3 64-bit, a
512-member set of distinct 32-byte `bytes` values measured 32,984 bytes for the set table plus
33,280 bytes for the key objects, exactly **66,264 bytes (64.71 KiB)**, excluding the fixed
transaction object and allocator bookkeeping. The number of objects, and therefore memory, remains
hard-bounded even if a game reaches exhaustion. A conservative 128-KiB engineering envelope covers
the measured ledger with allocator margin; the normative portable bounds remain 512 keys of exactly
32 logical bytes, not a platform-specific `sys.getsizeof` result.

Alternatives are closed as follows:

| Alternative | Decision and reason |
|---|---|
| Bounded fail-closed lifetime ledger of digest identities | **Selected.** It preserves rejection both during and after durability, has one small fixed owner, adds no I/O or second writer, and fails observably rather than re-authorizing an old key. |
| Active-invocation-only reservation | Rejected. Releasing on invocation completion would admit a later direct/outer duplicate, while `LOCAL_SENT` authoritative finalization deliberately outlives the Brain invocation. |
| Sink-authoritative de-duplication | Rejected. The append-only FIFO sink exposes no lookup or uniqueness transaction; rescanning/indexing it would add blocking I/O, a second authority, persistence semantics, and a new failure surface. |
| LRU/FIFO/TTL or phase/epoch eviction | Rejected. No canonical source proves that an evicted capture can never be resubmitted, so eviction could silently permit a reachable duplicate. |
| Retain complete terminal rows or their canonical hashes | Rejected. Capture identity alone rejects every second submission; retaining private terminal bodies or another digest increases memory without strengthening the one-write decision. |

The bounded F2 repair is owned only by `ai_client/discussion/transaction.py` for the set, validation,
ordered reservation, failure retention, and `close()` behavior; `ai_client/brain/controller.py` only
owns the one-way stop/runtime-fatal integration; and
`tests/test_phase6_discussion_transaction.py` owns the focused regression. It does not change
`ai_client/llm/audit.py`, any terminal schema/type, P6-A capture/state behavior, F1 cancellation
repair, or either F3 test-matrix expansion. Required literal tests are:

- `test_p6b_terminal_ledger_capacity_511_512_513_fails_closed`: 511 and 512 distinct submissions
  succeed exactly once; the 513th produces the exact exhaustion error, no 513th sink call, and no
  growth. A lowered capacity repeats one-under/equal/one-over, while constructor vectors reject
  zero, bool, negative, and above-hard values.
- `test_p6b_terminal_ledger_duplicate_precedes_capacity_inflight_and_durable`: at full capacity, a
  reserved key still gets the duplicate error rather than exhaustion; identical and field-conflicting
  second records are rejected while the first acknowledgement is gated and after it is durable.
- `test_p6b_terminal_ledger_cancel_and_failure_keep_reservation`: repeated caller cancellation,
  sink exception/cancel, and invalid acknowledgement each make at most one sink call and leave that
  key unavailable; a post-reservation failure is never retried.
- `test_p6b_terminal_ledger_close_is_irreversible_for_direct_helpers`: close is idempotent, releases
  retained keys only behind the closed flag, leaves an in-flight owned write to finish, and rejects
  every same/new direct helper write afterward without a sink call.
- `test_p6b_terminal_ledger_stores_only_digest_keys`: the live collection has at most 512 exact
  32-byte keys and retains no `AiDiscussionTerminalRecord` or terminal body sentinel.

The complete existing terminal constructor/nullability matrix, audit-failure exception, shared FIFO
sequence test, direct concurrent duplicate test, controller double-cancel test, repair linkage,
context-free compatibility, and controller ordering tests remain required unchanged. The 511/512/
513 vector derives its expected capacity literally, not from the production constant alone.

Every “append terminal” below means exactly `await shared_audit.write(terminal_record)` followed by
validation of its `AuditWriteAck(sequence>=1, durable=True)` before the owning controller reports a
successful terminal outcome.

| Terminal path | Exact owner calls and audit | Network/outer result |
|---|---|---|
| suppression before `capture()` | no store or discussion-audit call | existing stale/deadline/cancel result; no send |
| context/capture failure | no proposal/stage; fail runtime | no send and no backend |
| projection/backend/final-invalid/cancel before valid result | `abort(capture_id, request_id, reason, None)`; controller appends one terminal linked to the failure generation ack | no send |
| terminal-reservation capacity or audit sink/ack failure | if uncommitted and not already state-finalized, `abort(capture_id, request_id, AUDIT_FAILED, None)`; otherwise retain the finalized state/fact; never submit a substitute or retry; fail runtime | no successful terminal outcome; any already-attempted send/observation is not rolled back or relabelled |
| valid result, `stage()` failure | `abort(capture_id, request_id, STAGE_FAILED, proposal_sha256)`; append terminal | no send |
| stale/deadline/cancel/revision failure after stage | `abort(capture_id, request_id, closed_after_stage_reason, proposal_sha256)`; append terminal | no send |
| valid `NoDecision` | `stage` → `commit` → `finish_no_action`; append `NO_ACTION` terminal | no send |
| action before sender await | `stage` → final checks → `commit` → `mark_dispatch_started` with no await between commit/start | dispatch is now potentially observable |
| sender proves not delivered | `finish_dispatch(..., NOT_DELIVERED)`; controller appends terminal | existing `SEND_NOT_DELIVERED` |
| sender is uncertain/cancelled after start | `finish_dispatch(..., DELIVERY_UNKNOWN)`; controller appends terminal | existing `SEND_DELIVERY_UNKNOWN` |
| valid local receipt | `finish_dispatch(..., LOCAL_SENT)`; return correlation; no terminal yet | existing `SENT`, acceptance unknown |
| Reaction/CO authoritative match | outer `observe_authoritative(..., ACCEPTED, evidence)`; outer appends terminal | `ACCEPTED`; weak correlation explicitly recorded |
| Reaction/CO rejection or ambiguity/gap/stop | outer `observe_authoritative(..., REJECTED or RECOVERY_UNKNOWN, evidence?)`; outer appends terminal | never inferred accepted |
| VoteAbility exact acceptance/rejection/barrier | outer `observe_authoritative(..., ACCEPTED/REJECTED/RECOVERY_UNKNOWN, evidence?)`; outer appends terminal | preserves exact request-ID rules |

Every controller `finally` calls the same fully populated idempotent `abort(...)` only while capture/stage is
uncommitted; an identical second abort returns the same ack, while abort-after-commit and every
second commit fail. A commit failure occurs before dispatch and returns fail-closed `BRAIN_FAILED`.
If an outer controller stops with a `LOCAL_SENT` correlation, it must finalize
`RECOVERY_UNKNOWN` and append its one terminal before successful close. A terminal-record failure
after an attempted send fails the runtime and Phase 6 evidence; it never changes the authoritative
observation or permits another call.

### 8.2 Outer authoritative-observation ownership

T203 closes the implementation seam between the already-complete P6-B local-send correlation and
P6-C/P6-D. A `DiscussionDispatchCorrelation` alone deliberately cannot reconstruct a terminal: it
does not contain the final generation-record digest, attempt ordinal, capture, or proposal. Feature
controllers therefore must not access `BrainController._discussion`, a concrete state-store field,
or an audit file; must not construct another `DiscussionTransaction`; and must not rescan/index the
FIFO audit sink. The existing transaction and its 512-key digest ledger remain the sole terminal
submission authority.

The feature-controller-facing API belongs to the existing `BrainInvocationArbiter`, which is already
the single dependency through which Reaction and VoteAbility invoke the Brain:

```python
async def finalize_discussion_observation(
    self,
    *,
    owner: Literal["reaction_chat", "vote_ability"],
    correlation: DiscussionDispatchCorrelation,
    status: DiscussionObservationStatus,
    reason: DiscussionTerminalReason,
    evidence: EvidenceRef | None = None,
) -> ObservationAck: ...
```

The arbiter is the one public owner supplied to feature controllers. Its only Brain-side delegate is
the following explicit public method on its owned `BrainController`; no feature controller is given
the controller or calls this method directly:

```python
async def finalize_discussion_observation(
    self,
    *,
    correlation: DiscussionDispatchCorrelation,
    status: DiscussionObservationStatus,
    reason: DiscussionTerminalReason,
    evidence: EvidenceRef | None = None,
) -> ObservationAck: ...
```

The arbiter alone validates/records feature `owner` and exact registered correlation. The controller
alone validates the exact retained correlation plus the closed status/reason/evidence matrix and
owns state observation, terminal construction, and terminal submission. Neither layer reclassifies a
typed World record: the outer feature supplies only the `EvidenceRef` returned by the canonical
classifier described below. This division avoids duplicate owner, classification, state, or ledger
authority while making the cross-component call a named method rather than private-field access.

Only the outer feature owner that received the exact correlation may call this method. The arbiter
checks exact frozen types, that `owner` is the owner registered for the unresolved result, and that
the correlation byte-equals the one it handed to that owner. It rejects a context-free result, an
unknown owner, a different correlation, or a cross-owner action before state mutation or audit:
`chat`/`co_declare` belong to `reaction_chat`, while `vote`/`ability` belong to `vote_ability`.
Reaction and VoteAbility do not receive the transaction, capture, generation acknowledgement, or
proposal and cannot call the state port directly.

`BrainController` retains the material and provides the arbiter one explicit brain-layer
finalization operation; no feature module calls that operation directly. The operation uses the
existing `DiscussionStatePort.observe_authoritative()`, the existing
`DiscussionTransaction.terminal_from_observation()`, and the same transaction's
`write_terminal()` in that order. It returns the `ObservationAck` only after the terminal writer's
existing durable acknowledgement has validated. This is a deliberate method boundary between two
existing Brain components, not permission for either component to read the other's private fields.

The status/reason matrix is closed:

| Observation status | Exact allowed terminal reason | Evidence |
|---|---|---|
| `ACCEPTED` | `AUTHORITATIVE_ACCEPTED` | required exact authorized typed reference |
| `REJECTED` | `AUTHORITATIVE_REJECTED` | required exact `ACTION_REJECTION` reference |
| `RECOVERY_UNKNOWN` | `AUTHORITATIVE_AMBIGUOUS` | optional authorized typed reference; null when no single record is authoritative |
| `RECOVERY_UNKNOWN` | `RECOVERY_GAP` | optional recovery/transport reference; null when retention erased it |
| `RECOVERY_UNKNOWN` | `PHASE_CHANGED` | optional phase/lifecycle reference; null when none is retained |
| `RECOVERY_UNKNOWN` | `OWNER_STOPPED` | null |

Every other pairing and `OWNER_STOPPED` with evidence is rejected before state observation. The
existing state/terminal constructors continue to enforce action-compatible evidence kinds and
nullability. An accepted or rejected outcome is never inferred from local send, generated text,
message similarity alone, a model score, time, player display name, or an absent/ambiguous record.

The canonical input and mapping are exact:

| Outer fact | Canonical classifier input | Final status / reason / evidence |
|---|---|---|
| one exact new self `ChatRecord` matching the sent chat | that `ChatRecord` and the owner's exact `BoundDiscussionContext` | `ACCEPTED` / `AUTHORITATIVE_ACCEPTED` / its classified `CHAT` reference |
| one exact new self `CoDeclarationRecord` matching the sent declaration | that `CoDeclarationRecord` and the same bound context | `ACCEPTED` / `AUTHORITATIVE_ACCEPTED` / its classified `CO_DECLARATION` reference |
| one exact `ActionAcceptedObservation` matching the closed wire action, request event ID, and an observation generation at least the send generation | that observation and the same bound context | `ACCEPTED` / `AUTHORITATIVE_ACCEPTED` / its classified `ACTION_ACCEPTED` reference |
| one exact `ActionRejectionObservation` matching the closed wire action, non-null request event ID, and an observation generation at least the send generation | that observation and the same bound context | `REJECTED` / `AUTHORITATIVE_REJECTED` / its classified `ACTION_REJECTION` reference |
| completed gap/floor recovery or history/transport retention gap | exact new completed gap/floor `ResumeRecoveryBarrier` when it is the retained proof; otherwise no record | `RECOVERY_UNKNOWN` / `RECOVERY_GAP` / classified barrier reference when retained, otherwise null |
| completed contiguous recovery in a later generation with no exact response | that exact completed contiguous `ResumeRecoveryBarrier` and the same bound context | `RECOVERY_UNKNOWN` / `AUTHORITATIVE_AMBIGUOUS` / its classified `RESUME_RECOVERY_BARRIER` reference |
| authoritative phase key differs before a unique acceptance/rejection | exact new `PhaseTransitionRecord` or `PhaseTimingObservation` when retained; otherwise no record | `RECOVERY_UNKNOWN` / `PHASE_CHANGED` / its classified reference when retained, otherwise null |
| zero, multiple, or conflicting final candidates with no gap or phase change | the one typed insufficient record only when exactly one record (for example a rejection with null request ID) is itself the ambiguity proof; otherwise no record | `RECOVERY_UNKNOWN` / `AUTHORITATIVE_AMBIGUOUS` / that classified reference or null |
| explicit owner/arbiter stop, invoke ownership loss before result consumption, or fatal post-send cleanup before any other terminal fact | no record | `RECOVERY_UNKNOWN` / `OWNER_STOPPED` / null |

The classifier's input is always an actual immutable `HistoryRecord | TransportObservation` obtained
from the owner-authorized World view plus the exact bound context; a dict, copied fields, synthesized
record, guessed visibility, channel-name heuristic, or correlation itself is never valid input.
Acceptance/rejection matching happens before classification, and classification never decides
matching. If more than one candidate exists, no arbitrarily selected record may be cited.

For VoteAbility, the closed action map is `vote` → `vote.cast` and `ability` → `ability.use`.
Acceptance and rejection require byte-equal mapped action and request event ID, plus literally
`observation.observation_connection_generation >= correlation.send_connection_generation`; an older
observation is not a candidate and does not itself finalize anything. After examining the complete
new observation batch for exact responses, a `ResumeRecoveryBarrier` is terminal only when
`complete is True` and its `connection_generation` is greater than the send generation. If
`replay_contiguous is True` and `replay_gap_or_floor is False`, absence of an exact response has the
contiguous-recovery row above; zero candidates before that barrier continue waiting. If
`replay_gap_or_floor is True` and `replay_contiguous is False`, it has the gap row. Exact response
candidates take precedence over either barrier in the same batch. These rules preserve the Phase
3.5 N→N+1 replay behavior and do not turn an older or absent response into rejection.

`BrainController` retains exactly zero or one unresolved value containing references to the exact
already-bounded `DiscussionCapture`, `DiscussionGenerationAck`, `DiscussionProposal`, and
`DiscussionDispatchCorrelation`. It is installed synchronously after `LOCAL_SENT` is validated and
before the `SENT` result becomes observable. These are the original immutable objects; no JSON,
prompt, response, excerpt, proposal, terminal body, or other private prose is copied. The capture's
state/context bounds and the proposal's 16-KiB bound remain authoritative, and there is never a map
or list of unresolved bodies. After durable completion, the heavy references may be released; at
most one small completed tuple of the exact correlation/status/reason/evidence/`ObservationAck` may
remain until the next local send solely to return an identical duplicate. It is not a terminal
submission authority and never bypasses the transaction ledger.

The first valid call marks the request-local finalization attempt before its first await. An exactly
identical call after durable completion returns the byte-equal `ObservationAck` without another
state call or sink call. A changed correlation, owner, status, reason, or evidence is a conflicting
duplicate and fails. Cancellation, sink exception/cancellation, invalid acknowledgement, or ledger
capacity failure leaves the attempt consumed; no retry or substitute terminal is permitted. The
arbiter owns one finalization task through completion under caller cancellation. A post-observation
audit failure does not roll state back or relabel the observation; it poisons the Brain/arbiter
runtime, fails Phase 6 evidence, and allows no later invocation.

The arbiter owns one additional **single-slot observation gate**, not a queue. T205 fixes its
registration and result-delivery order. `BrainController` first installs its exact material slot
synchronously before returning a contextful `SENT`. The arbiter then registers owner, correlation,
pending invocation, and an initially unselected finalization attempt under its lock while that
invocation is still active. There is no await or result publication between recognizing the exact
`SENT` and this registration.

The remainder of the order is exact:

1. On the direct path, registration precedes setting the feature future, clearing the active slot,
   and considering another grant.
2. On the admitted path, registration precedes leaving the post-Brain cleanup owner, atomically
   detaching and cancelling an attached successor, the one awaited parent `lease.release()`, setting
   the feature future, and running/resuming any successor or replacement-suspended request.
3. The admitted owner exits `lease.activate()`, atomically removes any `_attached_successor` from the
   runnable field, retains its unfinished pending feature request, and awaits exactly one
   `SuccessorReservation.cancel()` **before** invoking parent `lease.release()`. This cancel occurs
   while the successor is still attached at the broker, so a clean `CANCELLED` acknowledgement
   prevents promotion to `ENQUEUED`, an `OFFER` for that successor, and creation of its client control
   lane or lease. No detached handle is passed to `wait_offer()` or claim. Whether cancellation
   succeeds or raises, the owner next awaits parent release exactly once. A cancellation error does
   not skip release; a later release error does not retry cancellation; neither operation is ever
   retried.
4. Only after required successor cancellation and parent release both return an allowed terminal
   result is cleanup complete. Only then may a non-cancelled owner's exact contextful result be
   published. No correlation-less `BRAIN_FAILED` result replaces an already registered local send.
5. The `invoke()` coroutine sets an internal `result_consumed` bit synchronously, with no await,
   immediately after its shielded future returns and before returning the result to the feature.
   Publication alone is not ownership transfer; this bit is.

Thus gate registration may overlap only the bounded post-Brain cleanup above. No outer-evidence wait
begins and no feature receives a correlation until the parent provider lease and external successor
reservation are gone. The gate never retains a provider/GPU lease or a broker successor reservation.
While it is set, the other owner's one existing bounded request may be parked, but no request is
granted, captured, offered to admission, or started in the Brain. A second request by the unresolved
owner is rejected rather than deadlocking.

The three existing admitted topologies have separate exact transitions:

- An ordinary `_pending` other-owner request remains the same object in `_pending`; it has no live
  external offer/handle and cannot be selected while the gate is set.
- An `_attached_successor` is removed from that runnable field at registration. A clean
  pre-release `CANCELLED` acknowledgement retains rather than completes its `_PendingInvocation`,
  creates no broker `OFFER` or client control lane/lease, clears its old `admission_invocation_id`,
  and reinserts that same object into `_pending` after parent release. An `EXPIRED`
  acknowledgement retains the same object only until the gate clears, then completes the existing
  deadline-suppressed result without acquisition. Any other status, invalid value, cancellation, or
  exception is fatal cleanup failure.
- A `_suspended_reaction` displaced by a replacement is removed from that special field when the
  replacement produces contextful `SENT`; its already-terminal replaced identity is cleared and the
  same unfinished object is reinserted into `_pending`. It is not immediately resumed by
  `_run_replacement()`.

After durable finalization, one normal selection pass handles every parked object. It first honors
caller cancellation and the existing context/deadline/stale checks. Only a still-current request
creates a fresh admission identity through the existing factory and enters the unchanged
priority/fairness path. A parked caller cancellation removes and completes only that request; expiry
creates no acquire, capture, Brain start, provider call, or new identity. This adds no queue and does
not complete a feature future merely because its former external reservation was cancelled.

Direct and admitted caller cancellation use the same ownership rule. A queued request cancelled
before Brain start is removed normally. Once execution owns it, cancellation marks
`owner_cancelled` and the owned execution/cleanup is driven to a definite result; existing admitted
pre-send cancellation may still prevent a send. If exact contextful `SENT` is registered and
`result_consumed` is false—including cancellation immediately before completion, after controller
completion during cleanup, or after future publication at the await boundary—the Arbiter owns one
null-evidence `RECOVERY_UNKNOWN / OWNER_STOPPED` finalization after cleanup and never publishes the
discarded result. If the no-await consumption step completed, ownership has transferred and the
feature's normal finalizer/stop rule applies. A non-SENT result needs no observation recovery.

Successor cancellation or parent lease release failure after gate registration is one fatal
pre-publication ownership-loss class. The Arbiter atomically marks itself poisoned, publishes no
result, selects the same single `RECOVERY_UNKNOWN / OWNER_STOPPED / None` attempt, prevents all
grants, and snapshots and clears ordinary pending, replacement-suspended, and attached/detached
successor requests. It awaits the successor-cancel task and then the parent-release task exactly once
even when the first fails, never retries either, never runs a successor, then owns the recovery
terminal through durability. All non-cancelled feature futures fail with
`RuntimeError("BrainInvocationArbiter is poisoned")`; later `invoke()` and finalizer calls fail the
same way. The poison cleanup then awaits the existing admission session's idempotent `aclose()` once
so an uncertain claim/reservation is not abandoned, and calls `BrainController.stop()` only after
the sole terminal attempt has completed or failed. Admission-close or terminal failure is retained
as fatal, never authorizes another terminal, and cannot roll state back. The entire poison shutdown
is one shielded Arbiter-owned task, so caller cancellation, simultaneous stop, and cleanup failure
cannot abandon it.

All explicit finalizer, owner-cancellation recovery, cleanup-failure recovery, and stop requests
compete under the Arbiter lock for the gate's single `attempt` transition. The first transition alone
creates the finalization task. An identical explicit duplicate may obtain its completed ack; every
conflict fails. Stop or poison awaits an already-selected task and never substitutes
`OWNER_STOPPED`; it selects `OWNER_STOPPED` only while `attempt` is empty. Durable success clears the
gate and resumes selection unless poisoned/stopped. Terminal failure permanently poisons the arbiter
and drains every waiter without a backend call.

Normal shutdown remains outer-owned. A Reaction or VoteAbility controller holding a local-send
correlation converts an explicit stop to `RECOVERY_UNKNOWN` / `OWNER_STOPPED` and awaits this API
before its `stop()` succeeds. Phase replacement uses `PHASE_CHANGED`; transport/history/recovery
loss uses `RECOVERY_GAP`; a retained but zero/multiple/conflicting final candidate uses
`AUTHORITATIVE_AMBIGUOUS`. The arbiter's own `stop()` first prevents new/pending work, then, only as
a bounded cleanup fallback for its one registered unresolved owner with no started finalization,
performs the same null-evidence `OWNER_STOPPED` finalization and owns its writer. If the owner's
finalization task already started, stop awaits that exact task and never substitutes a second status
or writer. Only after the finalization task has finished does it call `BrainController.stop()` and
close the transaction. An audit failure still propagates as runtime-fatal, but no writer task,
provider lease, or reachable retry is abandoned.

Feature controllers derive both trigger and observation references only with the existing public
`evidence_ref_for_record(record, bound_context)` classifier and the exact
`BoundDiscussionContext` supplied later by P6-E. Their constructors gain an optional keyword-only
`discussion_context: BoundDiscussionContext | None = None`; absence keeps the complete context-free
Phase 3–5 path unchanged, while the Phase 6 runtime requires the same bound object used by its
store. A correlation's `context_sha256` must equal that bound context before classification or
finalization. No feature controller branches on a channel, role, event-type, or visibility literal.

Reaction constructs `DiscussionTrigger` only after the existing lifecycle/mapping/handle/gap,
frequency, cooldown, cap, and cutoff gates have admitted the same opportunity, and attaches it to
the existing `DispatchDeadline` before `invoker.invoke()`:

| Existing Reaction trigger | Phase 6 trigger | Source |
|---|---|---|
| `INITIAL_CHAT` | owner `reaction_chat`, kind `INITIAL_CHAT` | null |
| `REACTION_CHAT` | owner `reaction_chat`, kind `PEER_CHAT` | exact classified peer `ChatRecord` reference |
| `CO_ACTION` | owner `reaction_chat`, kind `CO_OPPORTUNITY` | null |

Day, phase, connection generation, action generation, and current mapping order byte-equal the
dispatch deadline. The peer source remains a current, non-self, retained typed chat on one current
received-handle channel; state capture/projection remains the final authority and fails before a
backend call if it is missing, evicted, conflicting, unprojected, or no longer authorized. The
trigger carries no copied text. The existing private pending message/fingerprint used by the
unchanged deterministic frequency policy is neither the trigger nor semantic authority.

For any sent chat, authoritative matching uses the exact selected received handle's opaque channel,
not merely a nullable pending channel. A peer reaction additionally requires source channel,
selected-handle channel, and emitted-chat channel to be the same opaque string. The outer owner scans
only records newer than its pre-send cursors. Exactly one self-authored `ChatRecord` matching day,
phase, selected channel, and emitted message is accepted; for a contextful CO opportunity, exactly
one self-authored `CoDeclarationRecord` matching day, phase, claimed role, and comment is accepted.
Contextful CO report remains prohibited. For Reaction/CO, an exact rejection requires the newly
observed closed wire action (`chat.send` or `co.declare`), non-null request-event ID, and current
connection generation; VoteAbility uses the explicit non-older comparator above. Both an acceptance
and a rejection, duplicate candidates, a missing request ID, or any other non-unique match is
ambiguous, never accepted/rejected. Evidence is classified once by the public classifier above.

`ReactionOutcome` gains one optional public field
`discussion: DiscussionDispatchCorrelation | None = None`. A contextful local send retains that
exact correlation in its final bounded outcome; legacy context-free outcomes retain null. Its
existing status remains `ACCEPTED` or `REJECTED` for those exact observations and
`TRANSPORT_GAP` for every `RECOVERY_UNKNOWN` reason, so no existing status, counter, or completion
contract is redefined. The exact recovery reason and evidence remain in the private terminal, not a
new public log. Every contextful local send, including owner stop, receives one such bounded outcome
and one terminal unless the declared audit-authority failure class occurs.

Required literal seam tests, owned by the bounded pre-C/D repair packet below, are:

- `test_p6bc_outer_finalizer_accepted_and_rejected_link_one_exact_terminal`;
- `test_p6bc_outer_finalizer_recovery_reason_and_evidence_matrix`;
- `test_p6bc_outer_finalizer_duplicate_is_idempotent_and_conflict_is_rejected`;
- `test_p6bc_outer_finalizer_cancellation_and_audit_failure_never_retry`;
- `test_p6bc_arbiter_holds_other_owner_without_holding_provider_lease`;
- `test_p6bc_arbiter_expired_waiter_resumes_without_brain_start`;
- `test_p6bc_stop_finalizes_owner_stopped_before_transaction_close`; and
- `test_p6bc_context_free_dispatch_has_no_observation_gate`.

T205 adds these literal seam tests without weakening the eight above:

- `test_p6bc_contextful_sent_registers_gate_before_cleanup_and_publication`;
- `test_p6bc_parent_release_failure_recovers_once_poisons_and_closes_resources`;
- `test_p6bc_successor_cancel_failure_recovers_once_poisons_and_closes_resources`;
- `test_p6bc_invoke_cancel_before_consumption_direct_and_admitted_recovers_once`;
- `test_p6bc_invoke_cancel_after_consumption_leaves_feature_owner`;
- `test_p6bc_gate_parks_ordinary_attached_and_replacement_with_fresh_identity`;
- `test_p6bc_gated_parked_cancel_or_expiry_never_acquires_or_starts`;
- `test_p6bc_vote_ability_n_to_n_plus_one_accepts_and_older_is_not_a_match`; and
- `test_p6bc_contiguous_recovery_without_response_is_ambiguous_with_barrier`.

T207 adds one literal ordering test while preserving every T203/T205 vector:

- `test_p6bc_attached_successor_cancel_precedes_release_without_offer_lane_or_lease`.

The two cleanup-failure tests assert registration before the injected failure; one release/cancel
attempt in the exact cancel-then-release order under either injected failure; one `OWNER_STOPPED`
observation/terminal attempt; no normal result, successor `OFFER`, client control lane/lease,
successor wait/claim, new acquire/capture/Brain/backend call, or second writer; all topology futures
drained; admission close and controller/transaction close ordering; and no live task/lease/
reservation. The T207 ordering test additionally holds the parent release until the cancel
acknowledgement and proves release is not invoked early. The cancellation
test is parameterized over direct/admitted and just-before/just-after controller local-send
completion, and distinguishes future publication from the no-await consumption step. The parking
tests parameterize ordinary pending, attached successor, and replacement-suspended paths and assert
same pending-object identity, no premature future completion, cleared old admission ID, a different
fresh ID after durable finalization, existing priority, parked cancellation, and expiry before
acquire. The VoteAbility tests use exact wire action/request ID and generation N, N+1, and N-1; the
contiguous barrier test asserts exact classified barrier evidence and distinguishes it from a
gap/floor `RECOVERY_GAP` and from candidate ambiguity.

The seam Implementer and independent Tester run the two complete owned files, then the unchanged
Brain/feature regressions exactly as follows; selecting only the new tests is insufficient evidence:

```text
python -m pytest tests/test_phase6_discussion_transaction.py tests/test_phase5_brain_admission.py -q
python -m pytest tests/test_phase3_3_brain_interface.py tests/test_phase3_4_reaction_chat.py tests/test_phase3_5_vote_ability_controller.py tests/test_phase4_llm_brain.py -q
python scripts/check_docs.py
python -m compileall ai_client
git diff --check
```

P6-C additionally owns these literal integration tests in
`tests/test_phase6_reaction_semantics.py`:

- `test_p6c_trigger_mapping_and_opaque_visibility_uses_sealed_context`;
- `test_p6c_private_peer_same_channel_response_is_accepted_and_linked`;
- `test_p6c_private_peer_different_or_public_channel_fails_before_mutation_send`;
- `test_p6c_private_source_cannot_authorize_public_inference`;
- `test_p6c_accept_reject_ambiguity_gap_phase_and_stop_terminal_matrix`;
- `test_p6c_source_missing_evicted_stale_self_or_unprojected_suppresses_backend`;
- `test_p6c_safety_frequency_coalescing_and_two_chat_cap_are_unchanged`; and
- `test_p6c_semantic_reaction_score_never_changes_frequency_or_admission`.

The tests use opaque public/private channel IDs and literal expected `EvidenceRef`, correlation,
status/reason, terminal linkage, state revision, call count, and counter values. They do not derive
expected outcomes from production maps, access private fields, run a provider, weaken a Phase 3–5
assertion, or use exact natural-language wording as an oracle.

## 9. Existing controller and D069 preservation

- Reaction retains its current safety order, deterministic Phase 5 frequency policy, cooldown,
  coalescing, admission, and two-CHAT-start counter. Discussion source/context projection occurs
  before backend admission; it neither adds nor resets a CHAT opportunity. Once Brain starts, the
  current counter is consumed even for silence, timeout, or invalid output. Existing same-authorized-
  channel behavior is retained for both public and private channels using opaque equality only;
  neither channel nor role names are hardcoded.
- CO keeps exactly one existing opportunity per action generation and its own bookkeeping. Its
  semantic judgment is produced inside that existing call and never increments the CHAT counter.
- Pre-vote reassessment is part of the existing fresh vote Brain invocation. It adds no reservation,
  provider call, retry, deterministic fallback, or second vote action. Ability retains the same
  shared Brain path and may update strategy only inside its existing call.
- One process still owns one Brain, one arbiter, one broker session, and one audit writer. Across nine
  processes the broker still permits one provider call at a time. Cancellation, lease drain,
  poison/quiescence, deadlines, stale handles, action correlation, and cleanup remain Phase 3–5
  authority.
- `standard_9.day_seconds` is 180 in the Phase 6 validation composition. Existing content-defined
  shortening/extension are unchanged; there is no server or client-neutral speech cap.

## 10. Offline acceptance

All implementation tests below are LLM-, provider-, network-, and GPU-free unless explicitly named
as completion. They use opaque synthetic IDs and private sentinels, not real role-name branches.

1. **Context/privacy:** same validated synthetic content/player state yields byte-identical manifest/
   context; canonicalizer and all one-under/equal/one-over limits; one-byte manifest/payload damage,
   cross-seat manifest mismatch, wrong runtime `network.game_id`, wrong player/self role/modifiers,
   unknown union, extra key, bool-as-int, and other-seat sentinel fail closed. Assert manifest bytes
   are dropped after bind and absent from ready/result/argv/environment/stdout/stderr/metrics/audit/
   prompt. Descriptor vectors cover empty/seven/eight entries, nine rejection, ID scalar/UTF-8 edges,
   exact bool, null/integer-as-bool, unknown/missing/extra/duplicate/unsorted IDs, manifest-bit
   mismatch, and shuffled-source canonical sorting. Only the owner's two-field descriptors may reach
   its private prompt/generation audit; full/other-channel manifest sentinels never do. Production
   `ai_client` has zero `server.*` imports. Malicious dynamic data occurs only in user JSON; the system
   message remains byte-identical.
2. **State/lifecycle:** identical authorized sequences yield byte-identical event/provenance/recent-
   turn/state/capture bytes and hashes. Missing/extra fields, every wrong enum/nullability/order,
   each one-field mutation, and one-under/equal/one-over event/actor/target/text/count/recent-turn/
   state-byte bound are exercised. Replay idempotence, conflicting replay rejection, clean reconnect
   preservation, gap/full-sync reset, prefix loss, phase reset, end/stop, process restart, and no
   cross-game/player sentinel are exact. An opaque true/false channel pair yields
   `PUBLIC`/`AUTHORIZED_PRIVATE`; an absent channel fails capture. One parameterized vector covers all
   20 record kinds exactly, and public-looking/private-looking marker `event_type` values remain
   `VISIBILITY_LOST`. A visibility-bit mutation changes/rejects context/state/capture hashes; clean
   reconnect and phase transition retain the sealed mapping. Production classification has no
   canonical channel or role literal branch. Proposal identity constructors accept exactly
   `none`/null and the four action/non-null shapes; changing either identity field changes the
   proposal digest. Stage validates the matching successful generation status/hash, and commit
   derives a byte-exact `RecentSemanticTurn` from the retained proposal without a Brain import or a
   changed port/ack signature.
3. **Transaction table:** generation/terminal constructors reject missing/extra fields, every unknown
   status/reason, wrong status/reason pair, wrong nullable field, and each mismatch among context,
   before-state, after-state, proposal, and generation-record hashes. Projection/backend/final-invalid/audit/stage failures, repair success,
   no-decision, stale before and after audit, cutoff equality, cancellation races, revision conflict,
   not-delivered, delivery-unknown, sent, accepted, rejected, and recovery-unknown assert every exact
   port call, capture/request/attempt/generation-record/context/before/after/proposal hash, ack, revision, receipt,
   outer-owner transfer, and exactly one matching terminal record. Terminal-reservation capacity and
   audit sink/acknowledgement failure are the explicit `AUDIT_FAILED` no-terminal class and fail the
   runtime. No path double-commits or claims an unobserved acceptance.
   Positive vectors cover all five proposal identity shapes. Kind/option nullability mutations,
   proposal/audit identity mismatch, changed offered option or handle family, contextful `co_report`,
   and stage/commit identity/hash substitutions fail before mutation; `NoDecision` commits the exact
   `none` turn and sends nothing. The §8.2 outer-finalizer matrix additionally proves that the
   feature-facing arbiter API alone can close a local send, one unresolved correlation gates C/D
   without holding admission, identical duplication is read-only, conflict/audit failure cannot
   retry, and owner stop finishes before transaction close.
4. **Memory/bounds:** `test_p6a_capture_authority_preserves_newest_twelve_in_40_record_regression`
   uses deaths at orders 1–32 and lower-importance chats at 33–40 and proves none of required orders
   29–40 is excluded. `test_p6a_capture_authority_union_trigger_newest_old_and_chronology`,
   `test_p6a_capture_authority_deduplicates_equal_and_rejects_conflicts`,
   `test_p6a_capture_authority_default_and_lowered_capacity_fail_closed`,
   `test_p6a_capture_authority_hash_and_private_sentinel_regression`, and
   `test_p6a_stage_rejects_reference_outside_authority_union` cover the exact maximum-25 protected
   union, trigger overlap, final chronology, equal/conflicting identity behavior, capacity 0 and the
   one-under/equal protected-cardinality boundary, hash changes, visibility, owner-private sentinel,
   and zero stale stage/commit on failure. P6-B additionally owns
   `test_p6b_lowered_record_limits_select_exact_capture_subset` and
   `test_p6b_uncaptured_or_unprojected_reference_fails_before_stage`. Existing old-important/newest,
   trigger reservation, score/tie ordering, multibyte truncation, lifecycle, replay/recovery, and
   transaction tests remain required. One-under/equal/one-over cases cover mandatory-only,
   context-only, state-only, memory-only, combined byte/proxy exhaustion, repair, and legacy
   `max_prompt_bytes > 32768`; final canonical rechecks match emitted bytes.
   World-prefix versus projection omission remains distinct and no summarizer/backend runs on reject.
5. **Strict output:** positive vectors cover claim, question, answer, rebuttal, opinion change,
   public relation/line hypothesis, strategy, CO judgment, reaction score, and pre-vote reassessment.
   Missing/extra/duplicate/wrong-type/out-of-range/unknown/omitted/private-public/unoffered,
   wrong-addressee, wrong-claim-speaker, missing/system actor, and disallowed self-source cases get no
   send/state and at most the existing one repair; correct peer Chat and typed-CO actors pass. Any
   visibility upgrade/downgrade, unknown visibility, or private/lost citation in a public-inference or
   public-claim branch fails the whole result; valid private-state use remains private.
6. **Responsive dialogue:** paired deterministic inputs differing only in prior authorized peer
   speech produce a different typed relationship/state decision; question→answer and
   claim→rebuttal cite the exact retained order; new evidence creates an exact prior/new opinion
   change. An opaque authorized-private peer chat may drive a private answer/rebuttal only when the
   emitted and received-handle channels equal its source; a different/public channel or public
   inference yields zero mutation/send. D069 count and Phase 5 frequency/admission outcomes remain
   byte-for-byte unchanged. Tests assert semantics and references, never exact natural-language wording.
7. **CO:** declare/silence uses only offered claim IDs, one call per action generation, CHAT count is
   unchanged, and CO report remains ineligible.
8. **Pre-vote:** late authorized discussion changes the typed reassessment used in the same fresh vote
   call; selected target is offered; existing reservation, deadline, re-arm, acceptance, rejection,
   and recovery-unknown behavior remains exact.
9. **Regression:** Phase 3.2 World, 3.3 Brain, 3.4 Reaction, 3.5 VoteAbility, Phase 4 prompt/parser/
   audit/Brain, Phase 5 short-chat/frequency/admission/runtime/completion, docs, compile, and diff
   checks pass. Default tests remain real-LLM-free.
10. **Private-review fixture:** all-pass and one-failure-per-dimension vectors, missing/corrupt shard,
    duplicate/unmatched linkage, zero population, normalization duplicates, and exact aggregate
    thresholds produce reproducible results while public output remains aggregate-only.

No exact prose, speech-count target, 800–1,000-token target, KV-cache claim, or alternate-model result
is an acceptance criterion.

## 11. One post-review exact-9B validation

After all implementation packets and independent code review pass, a Tester runs exactly one finite
`standard_9` game through the reviewed shared-provider path using only the canonical
`Qwen3.5-9B-Q4_K_M.gguf` identity. The runner must preflight the exact configured identity and abort
on mismatch; it must not search for a model, load 35B, fall back, retry the game, or start a soak.

The game uses D069's 180-second day, current shortening/extension, no new server cap, at most two CHAT
Brain starts per player/phase, the existing separate CO path, one broker, nine clients, provider
concurrency one, and a finite reviewed hard timeout. Machine evidence has one semantic PASS authority
and records identity, game end, provider/queue/e2e latency, prompt bytes/proxy/provider tokens,
accepted speech, semantic-reference counts, per-player/phase CHAT calls, strict-output/audit outcome,
owned process/listener cleanup, and artifact hashes.

Machine semantic PASS requires at least one accepted responsive chat whose private audited semantic
act validly references a prior authorized peer record, at least one valid pre-vote reassessment, no
schema/authorization violation, all CHAT caps respected, game end, durable audit, and clean owned
cleanup. It does not claim natural-language quality.

A separate authorized human Reviewer, in a session independent from the Implementer and Tester,
reviews **every server-accepted text decision** in that one game: every accepted `ChatDecision`
message and accepted `CoDeclareDecision` comment, ordered by server record order. Missing/unreadable/
corrupt private shard, duplicate/unmatched accepted record, or any accepted text without exactly one
generation→terminal correlation is an immediate human-review FAIL. The population is never sampled.

For each correlated record the Reviewer records five closed results in an owner-only structured
checklist keyed only by `capture_id` (no copied text or player mapping):

- `coherent`: one intelligible, game-related proposition, question, or response; no internally
  contradictory clause;
- `source_relevant`: for a reaction or `ANSWER`/`REBUTTAL`, the text addresses the cited authorized
  source and agrees with its private semantic-act category; otherwise `NOT_APPLICABLE`;
- `objective_consistent`: the act does not plainly undermine the validated own objective and the
  committed strategy. Legal deception/false CO is not failed merely for differing from the true
  role;
- `privacy_safe`: no credential, admission token, raw private-channel excerpt, or own-private result
  is disclosed outside an offered action explicitly intended to disclose that datum;
- `non_repetitive`: after NFKC normalization, stripping, and collapsing Unicode whitespace to one
  ASCII space, it is not identical to that player's immediately previous accepted text, and the
  normalized text occurs at most twice in the whole game.

Human PASS is exact: population count is greater than zero; correlation/missing/corrupt failures are
zero; every record passes `coherent`, `objective_consistent`, and `privacy_safe`; every applicable
`source_relevant` passes; every record passes `non_repetitive`; and at least one machine-qualified
accepted responsive chat is also human `source_relevant=PASS`. Privacy and authorization have zero
tolerance. The checklist processor derives the aggregate and emits publicly only reviewer task ID,
reviewed population/applicable counts, per-dimension pass/fail counts, linkage/missing/corrupt and
duplicate counts, private checklist/evidence-manifest SHA-256 values, and final PASS/FAIL. Raw
utterances, normalized strings, reasons, capture IDs, and player mappings remain in owner-only
evidence and are never copied into active docs.

The exclusive processor implementation is new `scripts/phase6_private_review.py`; its focused tests
are new `tests/test_phase6_private_review.py`, and its only repository fixture is new
`tests/fixtures/phase6_private_review_vectors.json`. No other packet may edit those files. The script
owns two `additionalProperties:false` schemas:

- owner-only checklist `aiwolf.phase6-private-review-checklist.v1`: exact fields
  `schema_version`, `reviewer_task_id`, `evidence_manifest_sha256`, and ordered `records`. Each of at
  most 512 records has only `capture_id`, `coherent`, `source_relevant`, `objective_consistent`,
  `privacy_safe`, and `non_repetitive`; the four ordinary results are `PASS|FAIL` and
  `source_relevant` is `PASS|FAIL|NOT_APPLICABLE`. IDs are non-empty and bounded by 128 scalars/512
  bytes. It contains no text, reason, player ID, or mapping;
- aggregate `aiwolf.phase6-private-review-aggregate.v1`: exact fields `schema_version`,
  `reviewer_task_id`, `evidence_manifest_sha256`, `private_checklist_sha256`, `population_count`,
  `responsive_population_count`, `source_relevant_applicable_count`, `dimension_counts`,
  `linkage_failure_count`, `missing_count`, `corrupt_count`, `duplicate_count`,
  `normalization_duplicate_count`, and `human_quality_pass`. `dimension_counts` has exactly the five
  dimension keys and each maps to exact non-negative `pass`, `fail`, and `not_applicable` counts;
  `not_applicable` must be zero outside `source_relevant`.

The only processor interface, from repository root, is
`python scripts/phase6_private_review.py --run-dir RUN_DIR --checklist CHECKLIST_JSON --output AGGREGATE_JSON`.
The post-Test Reviewer supplies absolute owner-only paths from the Tester handoff. The processor
reconstructs the complete accepted-text population from the machine manifest and private shards,
orders it by server record order, verifies every generation/terminal/server correlation and manifest
hash, rejects a checklist with any missing/extra/duplicate/out-of-order capture ID, recomputes
`non_repetitive` under the specified normalization and rejects a human/computed mismatch, then writes
one canonical aggregate without private fields. Oversize (>512), unreadable, malformed, hash-mismatched,
or zero populations write a fail aggregate when possible and never sample.

The canonical `AGGREGATE_JSON` produced by that invocation is the **sole human-quality PASS
authority**: only its exact `human_quality_pass=true` field, with its recorded aggregate artifact
SHA-256 and successful processor completion, is PASS. A handoff or wrapper may cite that field/hash
but cannot independently assert, replace, or combine it. Machine semantic PASS remains its separate
machine-evidence authority. The Tester never fills the checklist or judges content.

A deterministic private fixture covers one all-pass population and one isolated failure for each
dimension, plus missing shard, corrupt shard, unmatched/duplicate correlation, zero population,
normalization duplicates, and threshold boundaries. It tests population construction and aggregate
derivation without treating any fixed natural-language sentence as the production acceptance
oracle. Both machine PASS and this private human PASS are required for Phase 6 closure. First failure
preserves evidence and stops; repair/re-run requires a new scoped task and review, never automatic
retry.

## 12. Non-overlapping implementation packets

Each packet has exclusive file ownership. Integrator updates to `TASKS.md`/`CURRENT_STATE.md` and
Reviewer/Tester handoffs are outside these Implementer writes.

| Packet / dependency | Exclusive files | Contract and required evidence |
|---|---|---|
| P6-A context/state foundation | new `ai_client/discussion/__init__.py`, `model.py`, `context.py`, `state.py`; new `tests/test_phase6_discussion_context.py`, `tests/test_phase6_discussion_state.py` | Frozen bounded types, canonical serialization, sealed channel-descriptor validation/retention, all-20-kind visibility classification, unknown-channel failure, context binding, fact fold, lifecycle, stage/CAS/commit/abort. Owns opaque descriptor/matrix/hash/lifecycle focused tests plus proposal identity vocabulary/nullability/hash and commit-derived `RecentSemanticTurn` vectors. Runs first. |
| P6-A projection-authority seam repair, after T192 independent approval and before B resumes | only `ai_client/discussion/state.py`; `tests/test_phase6_discussion_state.py`; the repair task handoff | Implement the exact §5.1 union and atomic insufficient-capacity rejection without changing `model.py`, capture schema, hashes, World, or any P6-B partial file. Owns the named 40-record, trigger/newest/older, de-dup/conflict, capacity, hash/private-sentinel, uncitable-stage, and unchanged lifecycle/transaction vectors in §10.4. A distinct Tester runs both complete P6-A suites plus World regression and writes only its handoff; a session distinct from both then freshly reviews the scoped repair and writes only its handoff. T191 remains blocked with its partial files preserved until that verdict is exactly `APPROVED`. |
| P6-B projection/semantic/audit transaction, after A | new `ai_client/discussion/projection.py`, `transaction.py`; `ai_client/brain/model.py`, `interface.py`, `controller.py`, `__init__.py`; `ai_client/llm/types.py`, `prompt.py`, `decision.py`, `brain.py`, `audit.py`, `__init__.py`; new `tests/test_phase6_memory_projection.py`, `tests/test_phase6_semantic_output.py`, `tests/test_phase6_discussion_transaction.py`; `tests/test_phase3_3_brain_interface.py`, `tests/test_phase4_llm_contracts.py`, `tests/test_phase4_llm_brain.py`, `tests/test_phase4_ai_audit.py` | One serial owner for `BrainOutput`, schema/parser, same-writer audit, projection, and commit gate. Implements exact budgets, closed semantic response, no visibility upgrade/public-inference misuse, and the transaction table while accepting legacy raw decisions only for context-free requests. Owns decision/proposal/offered-handle matching for all five identities, contextful `co_report` rejection, generation-audit linkage/mutation vectors, no-upgrade/visibility semantic vectors, and named Brain/LLM regressions. |
| P6-B/C outer-observation seam, after B and before C/D | only `ai_client/brain/controller.py`, `ai_client/brain/invocation.py`; necessary updates only to `tests/test_phase6_discussion_transaction.py`, `tests/test_phase5_brain_admission.py` | Implements only §8.2's feature-facing arbiter API, explicit controller delegate, one bounded controller material slot, registration-before-cleanup observation gate, attached-successor cancel-before-parent-release, invoke-consumption transfer, ordinary/attached/replacement parking, fatal cleanup/stop ownership, and all T203/T205/T207 literal seam tests. `ai_client/brain/model.py`, `ai_client/brain/interface.py`, `ai_client/brain/__init__.py`, every `ai_client/discussion/*` file (including `model.py`, `state.py`, and `transaction.py`), and `ai_client/llm/audit.py` are explicitly unchanged: no decision/correlation/state/terminal type, state transition, transaction ledger, audit schema/writer, provider/admission priority, or feature behavior changes. A distinct Tester and then fresh Reviewer must pass before C/D. |
| P6-C reaction integration, after the outer-observation seam | `ai_client/reaction_chat/types.py`, `controller.py`, `__init__.py`; new `tests/test_phase6_reaction_semantics.py`; necessary `tests/test_phase3_4_reaction_chat.py` updates | Exact authorized peer trigger handoff/source suppression, sealed-context classification, opaque private same-channel response, finalizer use, and semantic reaction evidence. `frequency.py` is untouched. Owns the eight literal §8.2 P6-C tests, private-channel/different-channel/public-inference rejection vectors, safety order, and two-CHAT cap. May run parallel with D. |
| P6-D pre-vote integration, after the outer-observation seam | `ai_client/vote_ability/types.py`, `controller.py`, `__init__.py`; new `tests/test_phase6_pre_vote_reassessment.py`; `tests/test_phase3_5_vote_ability_controller.py` | Reassessment inside the existing reserved call and current handle; uses the same public arbiter finalizer and sealed-context evidence classifier. Preserves correlation/re-arm/recovery. May run parallel with C. |
| P6-E runtime composition, after A–D | `ai_client/runtime.py`, top-level `ai_client/__init__.py`; new `tests/test_phase6_runtime.py`; `tests/test_runtime_capabilities.py` | Exactly one context/store, required Phase 6 composition, bind/start/close failure paths, redacted config, no alternate Brain/backend. |
| P6-F trusted local adapter and deterministic completion, after E | `scripts/run_phase5_local_smoke.py`, `tests/test_phase5_local_smoke.py`; new `tests/fixtures/phase6_semantic_backend.py`, `tests/test_phase6_semantic_completion.py` | Same-object server-child construction of each exact authorized-channel descriptor, manifest equality verification/disposal, one-shot runner-private relay, per-client stdin delivery, privacy/failure cleanup vectors, D069 Phase 6 plan, and deterministic nine-client semantic completion. Must retain the old Phase 5/Q8 profiles unchanged. |
| P6-G private-review processor, after F | new `scripts/phase6_private_review.py`, `tests/test_phase6_private_review.py`, `tests/fixtures/phase6_private_review_vectors.json` | Implement the closed owner-only checklist input, population/correlation processor, aggregate-only output, and all deterministic fixture branches in §11. Does not open real evidence. |
| P6-H independent implementation review, after A–G | handoff only | Review complete scoped diff including the private processor, privacy, transaction table, D069, and focused/regression evidence. No self-approval. |
| P6-I Tester validation, after H approval | test/evidence output and handoff only | Run offline matrix and finite completion, then exactly one reviewed exact-9B game. Preserve the machine manifest/private shards and owner-only checklist destination for J; do not inspect or approve content. No product edit, fallback, 35B, soak, or automatic retry. |
| P6-J post-Test private Reviewer, after I | owner-only checklist, aggregate artifact, and handoff only | A session distinct from every Implementer and the Tester reviews every accepted text, fills the checklist, invokes the reviewed processor once, and reports only the sole aggregate artifact field/hash. No product edit, rerun, or self-approval. |

Packets A/B/the outer-observation seam/E/F/G are serial. C and D alone may run concurrently after the
seam because their files do not overlap. A finding that requires `server/*`, `content/*`, or
`protocol/*` is not absorbed: it returns
to the Integrator for a new Design Gate.
This visibility correction adds no `server/*`, `content/*`, `protocol/*`, or `ai_client/world/*`
owner. T203 inserted one bounded technical seam before C/D; T205 R1 closed its four cleanup,
caller-cancellation, parking, and recovery-correlation gaps; T207 R2 changes only attached-successor
cancel/release ordering and does not otherwise change the P6-A–J dependency graph.

## 13. Design Gate result

All required Phase 6 product questions remain closed without a new user product decision. T207's R2
of the technical outer-observation clarification requires fresh independent review. The safe context
boundary is the trusted local same-content composition; unsupported launchers fail closed. State
truth, delivery, and authoritative acceptance are separated explicitly. Every new collection, text,
score, byte payload, and provider-visible projection is bounded. Implementation is partitioned, but
is **not authorized** until the latest complete revision of this design receives an independent
Reviewer handoff whose result is exactly `APPROVED`; an earlier review of an earlier revision cannot
satisfy that gate.
