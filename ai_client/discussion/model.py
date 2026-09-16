"""Closed immutable values for Phase 6 private discussion state.

This module is intentionally data-only.  It must remain usable by later Brain,
LLM, and controller packets without importing any of those layers.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
import re
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias

if TYPE_CHECKING:
    from .context import AuthorizedDiscussionContext


MAX_ID_SCALARS = 128
MAX_ID_UTF8_BYTES = 512
MAX_EVIDENCE_PER_ITEM = 8
MAX_EVENT_PLAYERS = 32
MAX_IMPORTANT_EVENTS = 32
MAX_RECENT_SEMANTIC_TURNS = 16
MAX_ASSESSMENTS = 32
MAX_CLAIMS = 32
MAX_RELATIONS = 32
MAX_FOCUS_PLAYERS = 4
MAX_PROPOSAL_BYTES = 16 * 1024
MAX_STATE_BYTES = 64 * 1024

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DiscussionValidationError(ValueError):
    """A closed Phase 6 value violated its contract."""


def _require_exact_bool(name: str, value: object) -> None:
    if type(value) is not bool:
        raise DiscussionValidationError(f"{name} must be an exact bool")


def _require_int(
    name: str,
    value: object,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> None:
    if type(value) is not int or value < minimum:
        raise DiscussionValidationError(f"{name} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise DiscussionValidationError(f"{name} must be <= {maximum}")


def _require_text(name: str, value: object, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise DiscussionValidationError(f"{name} must be {qualifier}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise DiscussionValidationError(f"{name} contains an invalid Unicode surrogate") from error
    return value


def _require_id(name: str, value: object) -> str:
    text = _require_text(name, value)
    if len(text) > MAX_ID_SCALARS or len(text.encode("utf-8")) > MAX_ID_UTF8_BYTES:
        raise DiscussionValidationError(
            f"{name} exceeds {MAX_ID_SCALARS} scalars/{MAX_ID_UTF8_BYTES} UTF-8 bytes"
        )
    return text


def _require_optional_id(name: str, value: object) -> None:
    if value is not None:
        _require_id(name, value)


def _require_hash(name: str, value: object) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise DiscussionValidationError(f"{name} must be lowercase SHA-256 hex")
    return value


def _require_enum(name: str, value: object, enum_type: type[Enum]) -> None:
    if not isinstance(value, enum_type):
        raise DiscussionValidationError(f"{name} must be {enum_type.__name__}")


def _as_tuple(name: str, value: object) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)):
        raise DiscussionValidationError(f"{name} must be a tuple-like collection")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise DiscussionValidationError(f"{name} must be a tuple-like collection") from error


def _normalize_ids(
    owner: object,
    field_name: str,
    *,
    maximum: int,
    ordered: bool = True,
) -> tuple[str, ...]:
    values = _as_tuple(field_name, getattr(owner, field_name))
    if len(values) > maximum:
        raise DiscussionValidationError(f"{field_name} must contain at most {maximum} values")
    for index, value in enumerate(values):
        _require_id(f"{field_name}[{index}]", value)
    if len(values) != len(set(values)):
        raise DiscussionValidationError(f"{field_name} must not contain duplicates")
    if ordered and values != tuple(sorted(values)):
        raise DiscussionValidationError(f"{field_name} must use Unicode-code-point order")
    object.__setattr__(owner, field_name, values)
    return values


def evidence_sort_key(value: "EvidenceRef") -> tuple[int, str]:
    return value.order, value.record_kind.value


def evidence_identity(value: "EvidenceRef") -> tuple[EvidenceRecordKind, int]:
    return value.record_kind, value.order


def _normalize_evidence(
    owner: object,
    field_name: str = "evidence",
    *,
    maximum: int = MAX_EVIDENCE_PER_ITEM,
    require_nonempty: bool = False,
) -> tuple["EvidenceRef", ...]:
    values = _as_tuple(field_name, getattr(owner, field_name))
    if require_nonempty and not values:
        raise DiscussionValidationError(f"{field_name} must not be empty")
    if len(values) > maximum:
        raise DiscussionValidationError(f"{field_name} must contain at most {maximum} values")
    if any(not isinstance(value, EvidenceRef) for value in values):
        raise DiscussionValidationError(f"{field_name} must contain EvidenceRef values")
    identities = [evidence_identity(value) for value in values]
    if len(identities) != len(set(identities)):
        raise DiscussionValidationError(
            f"{field_name} must not repeat an evidence identity"
        )
    if values != tuple(sorted(values, key=evidence_sort_key)):
        raise DiscussionValidationError(f"{field_name} must use (order, record_kind) order")
    object.__setattr__(owner, field_name, values)
    return values


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


_FIXED_VISIBILITY: dict[EvidenceRecordKind, EvidenceVisibility] = {
    EvidenceRecordKind.CO_DECLARATION: EvidenceVisibility.PUBLIC,
    EvidenceRecordKind.CO_REPORT: EvidenceVisibility.PUBLIC,
    EvidenceRecordKind.VOTE_RESULT: EvidenceVisibility.PUBLIC,
    EvidenceRecordKind.VOTE_REVEAL: EvidenceVisibility.PUBLIC,
    EvidenceRecordKind.DEATH: EvidenceVisibility.PUBLIC,
    EvidenceRecordKind.PHASE_TRANSITION: EvidenceVisibility.PUBLIC,
    EvidenceRecordKind.PHASE_TIMING_CHANGED: EvidenceVisibility.PUBLIC,
    EvidenceRecordKind.ABILITY_RESULT: EvidenceVisibility.AUTHORIZED_PRIVATE,
    EvidenceRecordKind.GAME_LIFECYCLE: EvidenceVisibility.PUBLIC,
    EvidenceRecordKind.PUBLIC_NOTIFY: EvidenceVisibility.PUBLIC,
    EvidenceRecordKind.TIE_RESOLVED_RANDOM: EvidenceVisibility.PUBLIC,
    EvidenceRecordKind.KNOWN_UNMODELED: EvidenceVisibility.VISIBILITY_LOST,
    EvidenceRecordKind.UNKNOWN: EvidenceVisibility.VISIBILITY_LOST,
    EvidenceRecordKind.MALFORMED: EvidenceVisibility.VISIBILITY_LOST,
    EvidenceRecordKind.ACTION_ACCEPTED: EvidenceVisibility.AUTHORIZED_PRIVATE,
    EvidenceRecordKind.ACTION_REJECTION: EvidenceVisibility.AUTHORIZED_PRIVATE,
    EvidenceRecordKind.PHASE_TIMING: EvidenceVisibility.AUTHORIZED_PRIVATE,
    EvidenceRecordKind.PHASE_DEADLINE_REACHED: EvidenceVisibility.AUTHORIZED_PRIVATE,
    EvidenceRecordKind.RESUME_RECOVERY_BARRIER: EvidenceVisibility.AUTHORIZED_PRIVATE,
}


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

    def __post_init__(self) -> None:
        _require_enum("record_kind", self.record_kind, EvidenceRecordKind)
        _require_int("order", self.order)
        _require_enum("visibility", self.visibility, EvidenceVisibility)
        if self.record_kind is EvidenceRecordKind.CHAT:
            if self.visibility is EvidenceVisibility.VISIBILITY_LOST:
                raise DiscussionValidationError("chat visibility must come from a sealed descriptor")
        elif self.visibility is not _FIXED_VISIBILITY[self.record_kind]:
            raise DiscussionValidationError("evidence visibility contradicts its fixed record kind")


_HISTORY_EVIDENCE_KINDS = frozenset(tuple(EvidenceRecordKind)[:15])
_PUBLIC_CLAIM_KINDS = frozenset(
    {
        EvidenceRecordKind.CHAT,
        EvidenceRecordKind.CO_DECLARATION,
        EvidenceRecordKind.CO_REPORT,
    }
)


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

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.important-event.v1":
            raise DiscussionValidationError("invalid ImportantEvent schema_version")
        if not isinstance(self.source, EvidenceRef) or self.source.record_kind not in _HISTORY_EVIDENCE_KINDS:
            raise DiscussionValidationError("ImportantEvent source must be a history EvidenceRef")
        if self.day is not None:
            _require_int("day", self.day)
        if self.phase is not None:
            _require_id("phase", self.phase)
        _normalize_ids(self, "actor_player_ids", maximum=MAX_EVENT_PLAYERS)
        _normalize_ids(self, "target_player_ids", maximum=MAX_EVENT_PLAYERS)
        _require_optional_id("channel_id", self.channel_id)
        _require_int("importance", self.importance, maximum=100)
        _require_exact_bool("text_truncated", self.text_truncated)
        _require_exact_bool(
            "remembered_after_world_eviction", self.remembered_after_world_eviction
        )

        kind = self.source.record_kind
        if kind is EvidenceRecordKind.CHAT:
            if self.channel_id is None:
                raise DiscussionValidationError("chat ImportantEvent requires channel_id")
        elif self.channel_id is not None:
            raise DiscussionValidationError("only chat ImportantEvent may carry channel_id")

        has_text = kind in {EvidenceRecordKind.CHAT, EvidenceRecordKind.CO_DECLARATION}
        text_fields = (
            self.text_excerpt,
            self.text_original_scalars,
            self.text_original_utf8_bytes,
        )
        if has_text:
            if any(value is None for value in text_fields):
                raise DiscussionValidationError("chat/CO declaration text metadata must be complete")
            excerpt = _require_text("text_excerpt", self.text_excerpt, allow_empty=True)
            assert self.text_original_scalars is not None
            assert self.text_original_utf8_bytes is not None
            _require_int("text_original_scalars", self.text_original_scalars)
            _require_int("text_original_utf8_bytes", self.text_original_utf8_bytes)
            excerpt_scalars = len(excerpt)
            excerpt_bytes = len(excerpt.encode("utf-8"))
            if excerpt_scalars > 160 or excerpt_bytes > 768:
                raise DiscussionValidationError("text_excerpt exceeds its hard bound")
            if self.text_original_scalars < excerpt_scalars or self.text_original_utf8_bytes < excerpt_bytes:
                raise DiscussionValidationError("original text counts cannot be below excerpt counts")
            expected_truncated = (
                self.text_original_scalars > excerpt_scalars
                or self.text_original_utf8_bytes > excerpt_bytes
            )
            if self.text_truncated is not expected_truncated:
                raise DiscussionValidationError("text_truncated does not match original counts")
        elif any(value is not None for value in text_fields) or self.text_truncated:
            raise DiscussionValidationError("non-text event cannot carry text metadata")

        if kind in {
            EvidenceRecordKind.KNOWN_UNMODELED,
            EvidenceRecordKind.UNKNOWN,
            EvidenceRecordKind.MALFORMED,
        } and (self.day is not None or self.phase is not None):
            raise DiscussionValidationError("visibility-lost marker day/phase must be null")


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

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.discussion-provenance.v1":
            raise DiscussionValidationError("invalid DiscussionProvenance schema_version")
        for name in (
            "history_complete",
            "co_complete",
            "ability_results_complete",
            "world_history_complete",
            "model_state_reset",
        ):
            _require_exact_bool(name, getattr(self, name))
        for name in (
            "world_history_dropped_count",
            "remembered_after_world_eviction_count",
            "known_unmodeled_event_count",
            "unknown_event_count",
            "malformed_event_count",
        ):
            _require_int(name, getattr(self, name))
        if self.world_history_dropped_count == 0:
            if self.world_history_dropped_through_order is not None:
                raise DiscussionValidationError("dropped_through_order must be null when count is zero")
        else:
            _require_int("world_history_dropped_through_order", self.world_history_dropped_through_order)
        if self.model_state_reset:
            _require_enum("reset_reason", self.reset_reason, DiscussionResetReason)
        elif self.reset_reason is not None:
            raise DiscussionValidationError("reset_reason requires model_state_reset")


class SpeechActKind(str, Enum):
    NONE = "NONE"
    CLAIM = "CLAIM"
    QUESTION = "QUESTION"
    ANSWER = "ANSWER"
    REBUTTAL = "REBUTTAL"
    OPINION_CHANGE = "OPINION_CHANGE"
    RELATION_HYPOTHESIS = "RELATION_HYPOTHESIS"


class DiscussionTopic(str, Enum):
    ALIGNMENT = "ALIGNMENT"
    ROLE_CLAIM = "ROLE_CLAIM"
    VOTE = "VOTE"
    EVENT = "EVENT"
    RELATION = "RELATION"
    STRATEGY = "STRATEGY"


class DiscussionStance(str, Enum):
    SUPPORT = "SUPPORT"
    OPPOSE = "OPPOSE"
    UNCERTAIN = "UNCERTAIN"


class OpinionDimension(str, Enum):
    SUSPICION = "SUSPICION"
    CREDIBILITY = "CREDIBILITY"


class ReactionReason(str, Enum):
    DIRECT_QUESTION = "DIRECT_QUESTION"
    DIRECT_MENTION = "DIRECT_MENTION"
    CLAIM_CONFLICT = "CLAIM_CONFLICT"
    VOTE_PRESSURE = "VOTE_PRESSURE"
    NEW_INFORMATION = "NEW_INFORMATION"
    OTHER_AUTHORIZED = "OTHER_AUTHORIZED"


class CoJudgmentDecision(str, Enum):
    DECLARE = "DECLARE"
    SILENCE = "SILENCE"
    DEFER = "DEFER"


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
    speech_act_kind: SpeechActKind
    evidence: tuple[EvidenceRef, ...]
    proposal_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.recent-semantic-turn.v1":
            raise DiscussionValidationError("invalid RecentSemanticTurn schema_version")
        _require_int("committed_revision", self.committed_revision, minimum=1)
        _require_hash("capture_id", self.capture_id)
        if self.request_id != f"phase6:{self.capture_id}":
            raise DiscussionValidationError("request_id must be derived from capture_id")
        _require_int("day", self.day)
        _require_id("phase", self.phase)
        if self.decision_kind not in {"none", "chat", "vote", "ability", "co_declare"}:
            raise DiscussionValidationError("invalid decision_kind")
        _require_optional_id("option_id", self.option_id)
        if (self.decision_kind == "none") is (self.option_id is not None):
            raise DiscussionValidationError("option_id nullability must match decision_kind")
        _require_enum("speech_act_kind", self.speech_act_kind, SpeechActKind)
        _normalize_evidence(self)
        _require_hash("proposal_sha256", self.proposal_sha256)


@dataclass(frozen=True)
class PlayerAssessment:
    player_id: str
    suspicion: int
    credibility: int
    confidence: int
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        _require_id("player_id", self.player_id)
        for name in ("suspicion", "credibility", "confidence"):
            _require_int(name, getattr(self, name), maximum=100)
        _normalize_evidence(self)


@dataclass(frozen=True)
class ClaimAssessment:
    claim: EvidenceRef
    speaker_player_id: str
    verdict: ClaimVerdict
    confidence: int
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.claim, EvidenceRef):
            raise DiscussionValidationError("claim must be EvidenceRef")
        if self.claim.visibility is not EvidenceVisibility.PUBLIC or self.claim.record_kind not in _PUBLIC_CLAIM_KINDS:
            raise DiscussionValidationError("claim must identify a public Chat/CO record")
        _require_id("speaker_player_id", self.speaker_player_id)
        _require_enum("verdict", self.verdict, ClaimVerdict)
        _require_int("confidence", self.confidence, maximum=100)
        evidence = _normalize_evidence(self)
        if any(item.visibility is not EvidenceVisibility.PUBLIC for item in evidence):
            raise DiscussionValidationError("claim assessment evidence must be public")


@dataclass(frozen=True)
class RelationHypothesis:
    source_player_id: str
    target_player_id: str
    relation: RelationKind
    confidence: int
    evidence: tuple[EvidenceRef, ...]
    provenance: Literal["PUBLIC_INFERENCE"]

    def __post_init__(self) -> None:
        _require_id("source_player_id", self.source_player_id)
        _require_id("target_player_id", self.target_player_id)
        if self.source_player_id == self.target_player_id:
            raise DiscussionValidationError("relation endpoints must differ")
        _require_enum("relation", self.relation, RelationKind)
        _require_int("confidence", self.confidence, maximum=100)
        evidence = _normalize_evidence(self)
        if self.provenance != "PUBLIC_INFERENCE":
            raise DiscussionValidationError("relation provenance must be PUBLIC_INFERENCE")
        if any(item.visibility is not EvidenceVisibility.PUBLIC for item in evidence):
            raise DiscussionValidationError("public relation evidence must be public")


@dataclass(frozen=True)
class StrategyState:
    scope: Literal["GAME", "PHASE"]
    mode: StrategyMode
    focus_player_ids: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if self.scope not in {"GAME", "PHASE"}:
            raise DiscussionValidationError("strategy scope must be GAME or PHASE")
        _require_enum("mode", self.mode, StrategyMode)
        _normalize_ids(self, "focus_player_ids", maximum=MAX_FOCUS_PLAYERS)
        _normalize_evidence(self)


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

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.discussion-state.v1":
            raise DiscussionValidationError("invalid DiscussionStateSnapshot schema_version")
        _require_id("game_id", self.game_id)
        _require_id("player_id", self.player_id)
        _require_hash("context_sha256", self.context_sha256)
        for name in ("epoch", "revision", "fact_revision", "world_version", "last_applied_seq"):
            _require_int(name, getattr(self, name))
        if self.phase is None:
            if self.day is not None:
                raise DiscussionValidationError("day must be null when phase is null")
        else:
            _require_id("phase", self.phase)
            _require_int("day", self.day)

        assessments = _as_tuple("assessments", self.assessments)
        claims = _as_tuple("claims", self.claims)
        relations = _as_tuple("relations", self.relations)
        events = _as_tuple("important_events", self.important_events)
        turns = _as_tuple("recent_semantic_turns", self.recent_semantic_turns)
        limits = (
            ("assessments", assessments, MAX_ASSESSMENTS, PlayerAssessment),
            ("claims", claims, MAX_CLAIMS, ClaimAssessment),
            ("relations", relations, MAX_RELATIONS, RelationHypothesis),
            ("important_events", events, MAX_IMPORTANT_EVENTS, ImportantEvent),
            ("recent_semantic_turns", turns, MAX_RECENT_SEMANTIC_TURNS, RecentSemanticTurn),
        )
        for name, values, maximum, expected_type in limits:
            if len(values) > maximum or any(not isinstance(value, expected_type) for value in values):
                raise DiscussionValidationError(f"invalid {name}")
            object.__setattr__(self, name, values)
        if assessments != tuple(sorted(assessments, key=lambda item: item.player_id)):
            raise DiscussionValidationError("assessments must be ordered by player_id")
        if len({item.player_id for item in assessments}) != len(assessments):
            raise DiscussionValidationError("assessment player IDs must be unique")
        if claims != tuple(sorted(claims, key=lambda item: evidence_sort_key(item.claim))):
            raise DiscussionValidationError("claims must be ordered by claim identity")
        if len({evidence_identity(item.claim) for item in claims}) != len(claims):
            raise DiscussionValidationError("claim identities must be unique")
        relation_key = lambda item: (item.source_player_id, item.target_player_id, item.relation.value)
        if relations != tuple(sorted(relations, key=relation_key)):
            raise DiscussionValidationError("relations must use canonical order")
        if len({relation_key(item) for item in relations}) != len(relations):
            raise DiscussionValidationError("relations must be unique")
        if events != tuple(sorted(events, key=lambda item: evidence_sort_key(item.source))):
            raise DiscussionValidationError("important_events must be chronological")
        if len({evidence_identity(item.source) for item in events}) != len(events):
            raise DiscussionValidationError("important event identities must be unique")
        if turns != tuple(sorted(turns, key=lambda item: item.committed_revision)):
            raise DiscussionValidationError("recent semantic turns must be revision ordered")
        if len({item.committed_revision for item in turns}) != len(turns):
            raise DiscussionValidationError("committed revisions must be unique")
        if self.strategy is not None and not isinstance(self.strategy, StrategyState):
            raise DiscussionValidationError("strategy must be StrategyState when supplied")
        if not isinstance(self.provenance, DiscussionProvenance):
            raise DiscussionValidationError("provenance must be DiscussionProvenance")
        remembered = sum(item.remembered_after_world_eviction for item in events)
        if remembered != self.provenance.remembered_after_world_eviction_count:
            raise DiscussionValidationError("remembered event count does not match provenance")
        from .context import canonical_json_bytes

        if len(canonical_json_bytes(self)) > MAX_STATE_BYTES:
            raise DiscussionValidationError("discussion state exceeds 64 KiB")


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

    def __post_init__(self) -> None:
        if self.owner not in {"reaction_chat", "vote_ability"}:
            raise DiscussionValidationError("invalid trigger owner")
        allowed = {
            "reaction_chat": {"INITIAL_CHAT", "PEER_CHAT", "CO_OPPORTUNITY"},
            "vote_ability": {"PRE_VOTE", "ABILITY"},
        }
        if self.kind not in allowed[self.owner]:
            raise DiscussionValidationError("trigger kind does not belong to owner")
        _require_int("day", self.day)
        _require_id("phase", self.phase)
        for name in ("connection_generation", "action_generation", "mapping_order"):
            _require_int(name, getattr(self, name))
        if self.kind == "PEER_CHAT":
            if (
                not isinstance(self.source, EvidenceRef)
                or self.source.record_kind is not EvidenceRecordKind.CHAT
                or self.source.visibility is EvidenceVisibility.VISIBILITY_LOST
            ):
                raise DiscussionValidationError("PEER_CHAT requires an authorized chat source")
        elif self.source is not None:
            raise DiscussionValidationError("only PEER_CHAT may carry a source")


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

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.discussion-capture.v1":
            raise DiscussionValidationError("invalid DiscussionCapture schema_version")
        _require_hash("capture_id", self.capture_id)
        _require_int("capture_ordinal", self.capture_ordinal, minimum=1)
        _require_id("game_id", self.game_id)
        _require_id("player_id", self.player_id)
        _require_hash("context_sha256", self.context_sha256)
        _require_hash("state_sha256", self.state_sha256)
        for name in ("epoch", "base_revision", "fact_revision", "world_version", "last_applied_seq"):
            _require_int(name, getattr(self, name))
        if not isinstance(self.trigger, DiscussionTrigger):
            raise DiscussionValidationError("trigger must be DiscussionTrigger")
        from .context import AuthorizedDiscussionContext, canonical_sha256

        if not isinstance(self.context, AuthorizedDiscussionContext):
            raise DiscussionValidationError("context must be AuthorizedDiscussionContext")
        if not isinstance(self.state, DiscussionStateSnapshot):
            raise DiscussionValidationError("state must be DiscussionStateSnapshot")
        evidence = _as_tuple("evidence", self.evidence)
        if evidence != self.state.important_events:
            raise DiscussionValidationError("capture evidence must byte-equal state important_events")
        object.__setattr__(self, "evidence", evidence)
        if canonical_sha256(self.context) != self.context_sha256:
            raise DiscussionValidationError("capture context hash mismatch")
        if canonical_sha256(self.state) != self.state_sha256:
            raise DiscussionValidationError("capture state hash mismatch")
        linked = (
            self.game_id == self.state.game_id == self.context.game_id
            and self.player_id == self.state.player_id == self.context.player_id
            and self.context_sha256 == self.state.context_sha256
            and self.epoch == self.state.epoch
            and self.base_revision == self.state.revision
            and self.fact_revision == self.state.fact_revision
            and self.world_version == self.state.world_version
            and self.last_applied_seq == self.state.last_applied_seq
            and self.trigger.day == self.state.day
            and self.trigger.phase == self.state.phase
        )
        if not linked:
            raise DiscussionValidationError("capture fields do not match context/state/trigger")
        material = {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if item.name != "capture_id"
        }
        if canonical_sha256(material) != self.capture_id:
            raise DiscussionValidationError("capture_id does not match canonical capture material")


# Strict semantic proposal -------------------------------------------------


@dataclass(frozen=True)
class SpeechActNone:
    kind: Literal[SpeechActKind.NONE]

    def __post_init__(self) -> None:
        if self.kind is not SpeechActKind.NONE:
            raise DiscussionValidationError("SpeechActNone kind must be NONE")


@dataclass(frozen=True)
class SpeechActClaim:
    kind: Literal[SpeechActKind.CLAIM]
    subject_player_id: str
    topic: DiscussionTopic
    stance: DiscussionStance
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if self.kind is not SpeechActKind.CLAIM:
            raise DiscussionValidationError("SpeechActClaim kind must be CLAIM")
        _require_id("subject_player_id", self.subject_player_id)
        _require_enum("topic", self.topic, DiscussionTopic)
        _require_enum("stance", self.stance, DiscussionStance)
        _normalize_evidence(self)


@dataclass(frozen=True)
class SpeechActQuestion:
    kind: Literal[SpeechActKind.QUESTION]
    addressee_player_id: str
    subject_player_id: str | None
    topic: DiscussionTopic
    source: EvidenceRef | None

    def __post_init__(self) -> None:
        if self.kind is not SpeechActKind.QUESTION:
            raise DiscussionValidationError("SpeechActQuestion kind must be QUESTION")
        _require_id("addressee_player_id", self.addressee_player_id)
        _require_optional_id("subject_player_id", self.subject_player_id)
        _require_enum("topic", self.topic, DiscussionTopic)
        if self.source is not None and not isinstance(self.source, EvidenceRef):
            raise DiscussionValidationError("question source must be EvidenceRef when supplied")


@dataclass(frozen=True)
class SpeechActAnswer:
    kind: Literal[SpeechActKind.ANSWER]
    addressee_player_id: str
    in_reply_to: EvidenceRef
    source_interpretation: Literal["QUESTION"]
    topic: DiscussionTopic
    stance: DiscussionStance
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if self.kind is not SpeechActKind.ANSWER or self.source_interpretation != "QUESTION":
            raise DiscussionValidationError("ANSWER tag/source_interpretation mismatch")
        _require_id("addressee_player_id", self.addressee_player_id)
        if not isinstance(self.in_reply_to, EvidenceRef):
            raise DiscussionValidationError("in_reply_to must be EvidenceRef")
        _require_enum("topic", self.topic, DiscussionTopic)
        _require_enum("stance", self.stance, DiscussionStance)
        _normalize_evidence(self)


@dataclass(frozen=True)
class SpeechActRebuttal:
    kind: Literal[SpeechActKind.REBUTTAL]
    addressee_player_id: str
    in_reply_to: EvidenceRef
    source_interpretation: Literal["CLAIM"]
    topic: DiscussionTopic
    stance: DiscussionStance
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if self.kind is not SpeechActKind.REBUTTAL or self.source_interpretation != "CLAIM":
            raise DiscussionValidationError("REBUTTAL tag/source_interpretation mismatch")
        _require_id("addressee_player_id", self.addressee_player_id)
        if not isinstance(self.in_reply_to, EvidenceRef):
            raise DiscussionValidationError("in_reply_to must be EvidenceRef")
        _require_enum("topic", self.topic, DiscussionTopic)
        _require_enum("stance", self.stance, DiscussionStance)
        _normalize_evidence(self)


@dataclass(frozen=True)
class SpeechActOpinionChange:
    kind: Literal[SpeechActKind.OPINION_CHANGE]
    subject_player_id: str
    dimension: OpinionDimension
    prior: int
    current: int
    causes: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if self.kind is not SpeechActKind.OPINION_CHANGE:
            raise DiscussionValidationError("SpeechActOpinionChange kind must be OPINION_CHANGE")
        _require_id("subject_player_id", self.subject_player_id)
        _require_enum("dimension", self.dimension, OpinionDimension)
        _require_int("prior", self.prior, maximum=100)
        _require_int("current", self.current, maximum=100)
        if self.prior == self.current:
            raise DiscussionValidationError("opinion change requires different prior/current")
        _normalize_evidence(self, "causes", require_nonempty=True)


@dataclass(frozen=True)
class SpeechActRelationHypothesis:
    kind: Literal[SpeechActKind.RELATION_HYPOTHESIS]
    source_player_id: str
    target_player_id: str
    relation: RelationKind
    confidence: int
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if self.kind is not SpeechActKind.RELATION_HYPOTHESIS:
            raise DiscussionValidationError("speech act kind must be RELATION_HYPOTHESIS")
        _require_id("source_player_id", self.source_player_id)
        _require_id("target_player_id", self.target_player_id)
        if self.source_player_id == self.target_player_id:
            raise DiscussionValidationError("relation endpoints must differ")
        _require_enum("relation", self.relation, RelationKind)
        _require_int("confidence", self.confidence, maximum=100)
        evidence = _normalize_evidence(self)
        if any(item.visibility is not EvidenceVisibility.PUBLIC for item in evidence):
            raise DiscussionValidationError("public relation evidence must be public")


SpeechAct: TypeAlias = (
    SpeechActNone
    | SpeechActClaim
    | SpeechActQuestion
    | SpeechActAnswer
    | SpeechActRebuttal
    | SpeechActOpinionChange
    | SpeechActRelationHypothesis
)


@dataclass(frozen=True)
class AssessmentUpdate:
    target_player_id: str
    suspicion: int
    credibility: int
    confidence: int
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        _require_id("target_player_id", self.target_player_id)
        for name in ("suspicion", "credibility", "confidence"):
            _require_int(name, getattr(self, name), maximum=100)
        _normalize_evidence(self)


@dataclass(frozen=True)
class ClaimUpdate:
    claim: EvidenceRef
    speaker_player_id: str
    verdict: ClaimVerdict
    confidence: int
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.claim, EvidenceRef)
            or self.claim.visibility is not EvidenceVisibility.PUBLIC
            or self.claim.record_kind not in _PUBLIC_CLAIM_KINDS
        ):
            raise DiscussionValidationError("claim update must identify public Chat/CO evidence")
        _require_id("speaker_player_id", self.speaker_player_id)
        _require_enum("verdict", self.verdict, ClaimVerdict)
        _require_int("confidence", self.confidence, maximum=100)
        evidence = _normalize_evidence(self)
        if any(item.visibility is not EvidenceVisibility.PUBLIC for item in evidence):
            raise DiscussionValidationError("claim update evidence must be public")


@dataclass(frozen=True)
class RelationUpdate:
    source_player_id: str
    target_player_id: str
    relation: RelationKind
    confidence: int
    evidence: tuple[EvidenceRef, ...]
    provenance: Literal["PUBLIC_INFERENCE"]

    def __post_init__(self) -> None:
        _require_id("source_player_id", self.source_player_id)
        _require_id("target_player_id", self.target_player_id)
        if self.source_player_id == self.target_player_id:
            raise DiscussionValidationError("relation endpoints must differ")
        _require_enum("relation", self.relation, RelationKind)
        _require_int("confidence", self.confidence, maximum=100)
        evidence = _normalize_evidence(self)
        if self.provenance != "PUBLIC_INFERENCE" or any(
            item.visibility is not EvidenceVisibility.PUBLIC for item in evidence
        ):
            raise DiscussionValidationError("relation update must be public inference")


@dataclass(frozen=True)
class StrategyUpdate:
    scope: Literal["GAME", "PHASE"]
    mode: StrategyMode
    focus_player_ids: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if self.scope not in {"GAME", "PHASE"}:
            raise DiscussionValidationError("strategy scope must be GAME or PHASE")
        _require_enum("mode", self.mode, StrategyMode)
        _normalize_ids(self, "focus_player_ids", maximum=MAX_FOCUS_PLAYERS)
        _normalize_evidence(self)


@dataclass(frozen=True)
class ReactionAssessment:
    trigger: EvidenceRef
    score: int
    reason: ReactionReason

    def __post_init__(self) -> None:
        if not isinstance(self.trigger, EvidenceRef) or self.trigger.record_kind is not EvidenceRecordKind.CHAT:
            raise DiscussionValidationError("reaction trigger must be chat evidence")
        _require_int("score", self.score, maximum=100)
        _require_enum("reason", self.reason, ReactionReason)


@dataclass(frozen=True)
class CoJudgment:
    decision: CoJudgmentDecision
    selected_option_id: str | None
    claimed_role_id: str | None

    def __post_init__(self) -> None:
        _require_enum("decision", self.decision, CoJudgmentDecision)
        _require_optional_id("selected_option_id", self.selected_option_id)
        _require_optional_id("claimed_role_id", self.claimed_role_id)
        present = self.selected_option_id is not None and self.claimed_role_id is not None
        if self.decision is CoJudgmentDecision.DECLARE:
            if not present:
                raise DiscussionValidationError("DECLARE requires option and claimed role")
        elif self.selected_option_id is not None or self.claimed_role_id is not None:
            raise DiscussionValidationError("SILENCE/DEFER require null option and role")


@dataclass(frozen=True)
class PreVoteReassessment:
    option_id: str
    ranked_target_player_ids: tuple[str, ...]
    preferred_target_player_id: str | None
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        _require_id("option_id", self.option_id)
        ranked = _normalize_ids(
            self, "ranked_target_player_ids", maximum=MAX_EVENT_PLAYERS, ordered=False
        )
        _require_optional_id("preferred_target_player_id", self.preferred_target_player_id)
        if self.preferred_target_player_id is not None and (
            not ranked or ranked[0] != self.preferred_target_player_id
        ):
            raise DiscussionValidationError("preferred vote target must be first in ranking")
        _normalize_evidence(self)


def _proposal_evidence(proposal: "DiscussionProposal") -> tuple[EvidenceRef, ...]:
    values: list[EvidenceRef] = []
    speech = proposal.speech_act
    for name in ("evidence", "causes"):
        values.extend(getattr(speech, name, ()))
    for name in ("source", "in_reply_to"):
        value = getattr(speech, name, None)
        if isinstance(value, EvidenceRef):
            values.append(value)
    if proposal.reaction is not None:
        values.append(proposal.reaction.trigger)
    for collection in (
        proposal.assessment_updates,
        proposal.claim_updates,
        proposal.relation_updates,
    ):
        for item in collection:
            values.extend(item.evidence)
            claim = getattr(item, "claim", None)
            if isinstance(claim, EvidenceRef):
                values.append(claim)
    if proposal.strategy_update is not None:
        values.extend(proposal.strategy_update.evidence)
    if proposal.pre_vote_reassessment is not None:
        values.extend(proposal.pre_vote_reassessment.evidence)
    return tuple(values)


@dataclass(frozen=True)
class DiscussionProposal:
    schema_version: Literal["aiwolf.discussion-proposal.v1"]
    base_revision: int
    decision_kind: Literal["none", "chat", "vote", "ability", "co_declare"]
    option_id: str | None
    speech_act: SpeechAct
    reaction: ReactionAssessment | None
    assessment_updates: tuple[AssessmentUpdate, ...]
    claim_updates: tuple[ClaimUpdate, ...]
    relation_updates: tuple[RelationUpdate, ...]
    strategy_update: StrategyUpdate | None
    co_judgment: CoJudgment | None
    pre_vote_reassessment: PreVoteReassessment | None

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.discussion-proposal.v1":
            raise DiscussionValidationError("invalid DiscussionProposal schema_version")
        _require_int("base_revision", self.base_revision)
        if self.decision_kind not in {"none", "chat", "vote", "ability", "co_declare"}:
            raise DiscussionValidationError("invalid proposal decision_kind")
        _require_optional_id("option_id", self.option_id)
        if (self.decision_kind == "none") is (self.option_id is not None):
            raise DiscussionValidationError(
                "proposal option_id nullability must match decision_kind"
            )
        if not isinstance(
            self.speech_act,
            (
                SpeechActNone,
                SpeechActClaim,
                SpeechActQuestion,
                SpeechActAnswer,
                SpeechActRebuttal,
                SpeechActOpinionChange,
                SpeechActRelationHypothesis,
            ),
        ):
            raise DiscussionValidationError("speech_act is not a closed SpeechAct branch")
        if self.reaction is not None and not isinstance(self.reaction, ReactionAssessment):
            raise DiscussionValidationError("reaction must be ReactionAssessment when supplied")
        collections = (
            ("assessment_updates", self.assessment_updates, AssessmentUpdate, 4),
            ("claim_updates", self.claim_updates, ClaimUpdate, 4),
            ("relation_updates", self.relation_updates, RelationUpdate, 2),
        )
        for name, raw, expected, maximum in collections:
            values = _as_tuple(name, raw)
            if len(values) > maximum or any(not isinstance(value, expected) for value in values):
                raise DiscussionValidationError(f"invalid {name}")
            object.__setattr__(self, name, values)
        if len({item.target_player_id for item in self.assessment_updates}) != len(self.assessment_updates):
            raise DiscussionValidationError("assessment update targets must be unique")
        if len(
            {evidence_identity(item.claim) for item in self.claim_updates}
        ) != len(self.claim_updates):
            raise DiscussionValidationError("claim update identities must be unique")
        relation_keys = {
            (item.source_player_id, item.target_player_id, item.relation)
            for item in self.relation_updates
        }
        if len(relation_keys) != len(self.relation_updates):
            raise DiscussionValidationError("relation updates must be unique")
        for name, value, expected in (
            ("strategy_update", self.strategy_update, StrategyUpdate),
            ("co_judgment", self.co_judgment, CoJudgment),
            ("pre_vote_reassessment", self.pre_vote_reassessment, PreVoteReassessment),
        ):
            if value is not None and not isinstance(value, expected):
                raise DiscussionValidationError(f"{name} has an invalid type")
        proposal_evidence = _proposal_evidence(self)
        by_identity: dict[tuple[EvidenceRecordKind, int], EvidenceRef] = {}
        for reference in proposal_evidence:
            identity = evidence_identity(reference)
            existing = by_identity.get(identity)
            if existing is not None and existing != reference:
                raise DiscussionValidationError(
                    "proposal repeats one evidence identity with conflicting visibility"
                )
            by_identity[identity] = reference
        if len(by_identity) > MAX_EVIDENCE_PER_ITEM:
            raise DiscussionValidationError("proposal may reference at most eight distinct evidence values")
        from .context import canonical_json_bytes

        if len(canonical_json_bytes(self)) > MAX_PROPOSAL_BYTES:
            raise DiscussionValidationError("proposal exceeds 16 KiB")


# Capture/state transaction ------------------------------------------------


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


def _validate_ack_core(value: object, *, committed: bool = False) -> None:
    capture_id = _require_hash("capture_id", getattr(value, "capture_id"))
    if getattr(value, "request_id") != f"phase6:{capture_id}":
        raise DiscussionValidationError("ack request_id must derive from capture_id")
    _require_hash("context_sha256", getattr(value, "context_sha256"))
    _require_hash("before_state_sha256", getattr(value, "before_state_sha256"))
    _require_hash("proposal_sha256", getattr(value, "proposal_sha256"))
    _require_int("base_revision", getattr(value, "base_revision"))
    if committed:
        _require_hash("after_state_sha256", getattr(value, "after_state_sha256"))
        _require_int("committed_revision", getattr(value, "committed_revision"), minimum=1)
        if getattr(value, "committed_revision") != getattr(value, "base_revision") + 1:
            raise DiscussionValidationError("committed_revision must equal base_revision + 1")


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

    def __post_init__(self) -> None:
        capture_id = _require_hash("capture_id", self.capture_id)
        if self.request_id != f"phase6:{capture_id}":
            raise DiscussionValidationError("generation request_id must derive from capture_id")
        if type(self.final_attempt_ordinal) is not int or self.final_attempt_ordinal not in {1, 2}:
            raise DiscussionValidationError("final_attempt_ordinal must be exact integer 1 or 2")
        _require_int("audit_sequence", self.audit_sequence, minimum=1)
        _require_hash("generation_record_sha256", self.generation_record_sha256)
        _require_enum("generation_status", self.generation_status, AiDiscussionGenerationStatus)
        _require_hash("context_sha256", self.context_sha256)
        _require_hash("before_state_sha256", self.before_state_sha256)
        if self.after_state_sha256 is not None:
            raise DiscussionValidationError("generation after_state_sha256 must be null")
        success = self.generation_status in {
            AiDiscussionGenerationStatus.DECISION,
            AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
            AiDiscussionGenerationStatus.REPAIR_SUCCEEDED,
        }
        if success:
            _require_hash("proposal_sha256", self.proposal_sha256)
        elif self.proposal_sha256 is not None:
            raise DiscussionValidationError("failed generation cannot carry proposal_sha256")
        if self.durable is not True:
            raise DiscussionValidationError("generation acknowledgement must be durable=True")


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

    def __post_init__(self) -> None:
        _validate_ack_core(self)
        if self.after_state_sha256 is not None or self.staged is not True:
            raise DiscussionValidationError("StageAck requires null after-state and staged=True")


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

    def __post_init__(self) -> None:
        _validate_ack_core(self, committed=True)
        if self.committed is not True:
            raise DiscussionValidationError("CommitAck requires committed=True")


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

    def __post_init__(self) -> None:
        _validate_ack_core(self, committed=True)
        if self.action not in {"chat", "vote", "ability", "co_declare"}:
            raise DiscussionValidationError("invalid dispatch action")
        _require_id("option_id", self.option_id)


_ABORT_BEFORE = frozenset(
    {
        DiscussionAbortReason.PROMPT_REJECTED,
        DiscussionAbortReason.BACKEND_FAILED,
        DiscussionAbortReason.FINAL_OUTPUT_INVALID,
        DiscussionAbortReason.CANCELLED_BEFORE_RESULT,
        DiscussionAbortReason.AUDIT_FAILED,
    }
)
_ABORT_AFTER = frozenset(
    {
        DiscussionAbortReason.CANCELLED_AFTER_STAGE,
        DiscussionAbortReason.STALE,
        DiscussionAbortReason.DEADLINE,
        DiscussionAbortReason.REVISION_CONFLICT,
    }
)


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

    def __post_init__(self) -> None:
        capture_id = _require_hash("capture_id", self.capture_id)
        if self.request_id != f"phase6:{capture_id}":
            raise DiscussionValidationError("abort request_id must derive from capture_id")
        _require_int("base_revision", self.base_revision)
        _require_hash("context_sha256", self.context_sha256)
        _require_hash("before_state_sha256", self.before_state_sha256)
        if self.after_state_sha256 is not None:
            raise DiscussionValidationError("abort after_state_sha256 must be null")
        _require_enum("reason", self.reason, DiscussionAbortReason)
        _require_exact_bool("stage_existed", self.stage_existed)
        if self.aborted is not True:
            raise DiscussionValidationError("AbortAck requires aborted=True")
        if self.reason in _ABORT_BEFORE:
            expected_stage, needs_proposal = False, False
        elif self.reason is DiscussionAbortReason.STAGE_FAILED:
            expected_stage, needs_proposal = False, True
        elif self.reason in _ABORT_AFTER:
            expected_stage, needs_proposal = True, True
        else:  # pragma: no cover - enum exhaustiveness defense
            raise DiscussionValidationError("unknown abort reason")
        if self.stage_existed is not expected_stage:
            raise DiscussionValidationError("abort stage_existed does not match reason")
        if needs_proposal:
            _require_hash("proposal_sha256", self.proposal_sha256)
        elif self.proposal_sha256 is not None:
            raise DiscussionValidationError("abort proposal_sha256 does not match reason")


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

    def __post_init__(self) -> None:
        _validate_ack_core(self, committed=True)
        _require_int("generation_audit_sequence", self.generation_audit_sequence, minimum=1)
        if self.action not in {"chat", "vote", "ability", "co_declare"}:
            raise DiscussionValidationError("invalid correlation action")
        _require_id("option_id", self.option_id)
        _require_id("request_event_id", self.request_event_id)
        _require_int("send_connection_generation", self.send_connection_generation)


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
    correlation: DiscussionDispatchCorrelation | None

    def __post_init__(self) -> None:
        _validate_ack_core(self, committed=True)
        _require_enum("status", self.status, DiscussionDeliveryStatus)
        if self.status is DiscussionDeliveryStatus.NO_ACTION:
            if any(
                value is not None
                for value in (
                    self.action,
                    self.option_id,
                    self.request_event_id,
                    self.send_connection_generation,
                    self.correlation,
                )
            ):
                raise DiscussionValidationError("NO_ACTION delivery fields must be null")
            return
        if self.action not in {"chat", "vote", "ability", "co_declare"}:
            raise DiscussionValidationError("action delivery requires a closed action")
        _require_id("option_id", self.option_id)
        if self.status is DiscussionDeliveryStatus.LOCAL_SENT:
            _require_id("request_event_id", self.request_event_id)
            _require_int("send_connection_generation", self.send_connection_generation)
            if not isinstance(self.correlation, DiscussionDispatchCorrelation):
                raise DiscussionValidationError("LOCAL_SENT requires correlation")
            for name in (
                "capture_id",
                "request_id",
                "base_revision",
                "committed_revision",
                "context_sha256",
                "before_state_sha256",
                "after_state_sha256",
                "proposal_sha256",
                "action",
                "option_id",
                "request_event_id",
                "send_connection_generation",
            ):
                if getattr(self.correlation, name) != getattr(self, name):
                    raise DiscussionValidationError("delivery correlation fields must byte-equal")
        elif any(
            value is not None
            for value in (
                self.request_event_id,
                self.send_connection_generation,
                self.correlation,
            )
        ):
            raise DiscussionValidationError("unsent delivery cannot carry receipt/correlation")


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

    def __post_init__(self) -> None:
        _validate_ack_core(self, committed=True)
        _require_int("generation_audit_sequence", self.generation_audit_sequence, minimum=1)
        if self.action not in {"chat", "vote", "ability", "co_declare"}:
            raise DiscussionValidationError("invalid observation action")
        _require_id("option_id", self.option_id)
        _require_id("request_event_id", self.request_event_id)
        _require_int("send_connection_generation", self.send_connection_generation)
        _require_enum("status", self.status, DiscussionObservationStatus)
        if self.status in {
            DiscussionObservationStatus.ACCEPTED,
            DiscussionObservationStatus.REJECTED,
        }:
            if not isinstance(self.authoritative_evidence, EvidenceRef):
                raise DiscussionValidationError("accepted/rejected observation requires evidence")
        elif self.authoritative_evidence is not None and not isinstance(
            self.authoritative_evidence, EvidenceRef
        ):
            raise DiscussionValidationError("recovery evidence must be EvidenceRef when supplied")


class DiscussionStatePort(Protocol):
    def capture(self, views: Any, trigger: DiscussionTrigger) -> DiscussionCapture: ...

    def stage(
        self,
        capture: DiscussionCapture,
        request_id: str,
        proposal: DiscussionProposal,
        generation_ack: DiscussionGenerationAck,
    ) -> StageAck: ...

    def commit(self, stage_ack: StageAck) -> CommitAck: ...

    def abort(
        self,
        capture_id: str,
        request_id: str,
        reason: DiscussionAbortReason,
        proposal_sha256: str | None = None,
    ) -> AbortAck: ...

    def finish_no_action(self, commit_ack: CommitAck) -> DeliveryAck: ...

    def mark_dispatch_started(
        self, commit_ack: CommitAck, action: str, option_id: str
    ) -> DispatchAck: ...

    def finish_dispatch(
        self,
        dispatch_ack: DispatchAck,
        status: DiscussionDeliveryStatus,
        receipt: Any = None,
    ) -> DeliveryAck: ...

    def observe_authoritative(
        self,
        correlation: DiscussionDispatchCorrelation,
        status: DiscussionObservationStatus,
        evidence: EvidenceRef | None = None,
    ) -> ObservationAck: ...

    def close(self) -> None: ...
