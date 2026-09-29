"""Deterministic bounded private discussion state and transaction port."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, fields, is_dataclass, replace
from hashlib import sha256
from threading import get_ident
from typing import Any

from ai_client.world import (
    AbilityResultRecord,
    AbilityResultView,
    ActionAcceptedObservation,
    ActionRejectionObservation,
    ChatRecord,
    CoDeclarationRecord,
    CoReportRecord,
    CoView,
    DeathRecord,
    Freshness,
    GameLifecycleRecord,
    HistoryRecord,
    HistoryView,
    KnownUnmodeledEventRecord,
    MalformedEventRecord,
    PhaseDeadlineReachedObservation,
    PhaseTimingChangedRecord,
    PhaseTimingObservation,
    PhaseTransitionRecord,
    PublicNotifyRecord,
    ResumeRecoveryBarrier,
    TieResolvedRandomRecord,
    TransportObservation,
    TransportObservationView,
    UnknownEventRecord,
    VoteRevealRecord,
    VoteResultRecord,
    WorldSnapshot,
)

from .context import (
    BoundDiscussionContext,
    DiscussionContextError,
    canonical_json_bytes,
    canonical_sha256,
    channel_is_public,
    validate_bound_context_snapshot,
)
from .model import (
    MAX_ASSESSMENTS,
    MAX_CLAIMS,
    MAX_EVIDENCE_PER_ITEM,
    MAX_EVENT_PLAYERS,
    MAX_FOCUS_PLAYERS,
    MAX_IMPORTANT_EVENTS,
    MAX_RECENT_SEMANTIC_TURNS,
    MAX_RELATIONS,
    MAX_STATE_BYTES,
    AbortAck,
    AiDiscussionGenerationStatus,
    AssessmentUpdate,
    ClaimAssessment,
    ClaimUpdate,
    CoJudgmentDecision,
    CommitAck,
    DeliveryAck,
    DiscussionAbortReason,
    DiscussionCapture,
    DiscussionDeliveryStatus,
    DiscussionDispatchCorrelation,
    DiscussionGenerationAck,
    DiscussionObservationStatus,
    DiscussionProposal,
    DiscussionProvenance,
    DiscussionResetReason,
    DiscussionStateSnapshot,
    DiscussionTrigger,
    DiscussionValidationError,
    DispatchAck,
    EvidenceRecordKind,
    EvidenceRef,
    EvidenceVisibility,
    ImportantEvent,
    ObservationAck,
    OpinionDimension,
    PlayerAssessment,
    RecentSemanticTurn,
    RelationHypothesis,
    RelationUpdate,
    SpeechActAnswer,
    SpeechActClaim,
    SpeechActKind,
    SpeechActOpinionChange,
    SpeechActQuestion,
    SpeechActRebuttal,
    SpeechActRelationHypothesis,
    StageAck,
    StrategyState,
    StrategyUpdate,
    _proposal_evidence,
    _require_exact_bool,
    _require_id,
    _require_int,
    evidence_identity,
    evidence_sort_key,
)


class DiscussionStateError(DiscussionValidationError):
    """A deterministic state/correlation operation failed closed."""


class _CanonicalReplayConflict(DiscussionStateError):
    """One evidence identity was observed with conflicting canonical bytes."""


@dataclass(frozen=True)
class DiscussionViews:
    snapshot: WorldSnapshot
    history: HistoryView
    co: CoView
    ability_results: AbilityResultView
    transport_observations: TransportObservationView | None = None

    def __post_init__(self) -> None:
        for name, expected in (
            ("snapshot", WorldSnapshot),
            ("history", HistoryView),
            ("co", CoView),
            ("ability_results", AbilityResultView),
        ):
            if not isinstance(getattr(self, name), expected):
                raise DiscussionStateError(f"{name} has an invalid type")
        if self.transport_observations is not None and not isinstance(
            self.transport_observations, TransportObservationView
        ):
            raise DiscussionStateError("transport_observations has an invalid type")
        retention = self.snapshot.history_retention
        if (
            self.history.retention != retention
            or self.co.retention != retention
            or self.ability_results.retention != retention
        ):
            raise DiscussionStateError(
                "discussion views must share the snapshot history retention"
            )
        if (
            self.transport_observations is not None
            and self.transport_observations.world_version != self.snapshot.version
        ):
            raise DiscussionStateError(
                "transport observations must match the snapshot world version"
            )


@dataclass(frozen=True)
class DiscussionStateConfig:
    max_players: int = MAX_EVENT_PLAYERS
    max_assessments: int = MAX_ASSESSMENTS
    max_claims: int = MAX_CLAIMS
    max_relations: int = MAX_RELATIONS
    max_evidence_per_item: int = MAX_EVIDENCE_PER_ITEM
    max_focus_players: int = MAX_FOCUS_PLAYERS
    max_important_events: int = MAX_IMPORTANT_EVENTS
    max_recent_semantic_turns: int = MAX_RECENT_SEMANTIC_TURNS
    max_state_bytes: int = MAX_STATE_BYTES
    max_source_fingerprints: int = 2048

    def __post_init__(self) -> None:
        ceilings = {
            "max_players": MAX_EVENT_PLAYERS,
            "max_assessments": MAX_ASSESSMENTS,
            "max_claims": MAX_CLAIMS,
            "max_relations": MAX_RELATIONS,
            "max_evidence_per_item": MAX_EVIDENCE_PER_ITEM,
            "max_focus_players": MAX_FOCUS_PLAYERS,
            "max_important_events": MAX_IMPORTANT_EVENTS,
            "max_recent_semantic_turns": MAX_RECENT_SEMANTIC_TURNS,
            "max_state_bytes": MAX_STATE_BYTES,
            "max_source_fingerprints": 2048,
        }
        for name, ceiling in ceilings.items():
            minimum = 1 if name in {"max_state_bytes", "max_source_fingerprints"} else 0
            _require_int(name, getattr(self, name), minimum=minimum, maximum=ceiling)


_PUBLIC_HISTORY_TYPES: tuple[type[Any], ...] = (
    CoDeclarationRecord,
    CoReportRecord,
    VoteResultRecord,
    VoteRevealRecord,
    DeathRecord,
    PhaseTransitionRecord,
    PhaseTimingChangedRecord,
    GameLifecycleRecord,
    PublicNotifyRecord,
    TieResolvedRandomRecord,
)


def _record_kind(record: HistoryRecord | TransportObservation) -> EvidenceRecordKind:
    pairs: tuple[tuple[type[Any], EvidenceRecordKind], ...] = (
        (ChatRecord, EvidenceRecordKind.CHAT),
        (CoDeclarationRecord, EvidenceRecordKind.CO_DECLARATION),
        (CoReportRecord, EvidenceRecordKind.CO_REPORT),
        (VoteResultRecord, EvidenceRecordKind.VOTE_RESULT),
        (VoteRevealRecord, EvidenceRecordKind.VOTE_REVEAL),
        (DeathRecord, EvidenceRecordKind.DEATH),
        (PhaseTransitionRecord, EvidenceRecordKind.PHASE_TRANSITION),
        (PhaseTimingChangedRecord, EvidenceRecordKind.PHASE_TIMING_CHANGED),
        (AbilityResultRecord, EvidenceRecordKind.ABILITY_RESULT),
        (GameLifecycleRecord, EvidenceRecordKind.GAME_LIFECYCLE),
        (PublicNotifyRecord, EvidenceRecordKind.PUBLIC_NOTIFY),
        (TieResolvedRandomRecord, EvidenceRecordKind.TIE_RESOLVED_RANDOM),
        (KnownUnmodeledEventRecord, EvidenceRecordKind.KNOWN_UNMODELED),
        (UnknownEventRecord, EvidenceRecordKind.UNKNOWN),
        (MalformedEventRecord, EvidenceRecordKind.MALFORMED),
        (ActionAcceptedObservation, EvidenceRecordKind.ACTION_ACCEPTED),
        (ActionRejectionObservation, EvidenceRecordKind.ACTION_REJECTION),
        (PhaseTimingObservation, EvidenceRecordKind.PHASE_TIMING),
        (PhaseDeadlineReachedObservation, EvidenceRecordKind.PHASE_DEADLINE_REACHED),
        (ResumeRecoveryBarrier, EvidenceRecordKind.RESUME_RECOVERY_BARRIER),
    )
    for expected, kind in pairs:
        if isinstance(record, expected):
            return kind
    raise DiscussionStateError(f"unsupported evidence record: {type(record).__name__}")


def evidence_ref_for_record(
    record: HistoryRecord | TransportObservation,
    bound_context: BoundDiscussionContext,
) -> EvidenceRef:
    """Classify one typed record through the approved exhaustive matrix."""

    if not isinstance(bound_context, BoundDiscussionContext):
        raise DiscussionStateError("bound_context must be BoundDiscussionContext")
    kind = _record_kind(record)
    if kind is EvidenceRecordKind.CHAT:
        assert isinstance(record, ChatRecord)
        visibility = (
            EvidenceVisibility.PUBLIC
            if channel_is_public(bound_context.context, record.channel)
            else EvidenceVisibility.AUTHORIZED_PRIVATE
        )
    elif isinstance(record, _PUBLIC_HISTORY_TYPES):
        visibility = EvidenceVisibility.PUBLIC
    elif isinstance(
        record,
        (
            AbilityResultRecord,
            ActionAcceptedObservation,
            ActionRejectionObservation,
            PhaseTimingObservation,
            PhaseDeadlineReachedObservation,
            ResumeRecoveryBarrier,
        ),
    ):
        visibility = EvidenceVisibility.AUTHORIZED_PRIVATE
    elif isinstance(
        record,
        (KnownUnmodeledEventRecord, UnknownEventRecord, MalformedEventRecord),
    ):
        visibility = EvidenceVisibility.VISIBILITY_LOST
    else:  # pragma: no cover - kept separate from type-to-kind exhaustiveness
        raise DiscussionStateError("record is not present in the visibility matrix")
    return EvidenceRef(record_kind=kind, order=record.order, visibility=visibility)


def _prefix_by_scalars_and_bytes(text: str, max_scalars: int = 160, max_bytes: int = 768) -> str:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise DiscussionStateError("record text contains an invalid Unicode surrogate") from error
    pieces: list[str] = []
    byte_count = 0
    for character in text:
        encoded = character.encode("utf-8")
        if len(pieces) >= max_scalars or byte_count + len(encoded) > max_bytes:
            break
        pieces.append(character)
        byte_count += len(encoded)
    return "".join(pieces)


def _known_player_ids(snapshot: WorldSnapshot, maximum: int) -> frozenset[str]:
    if len(snapshot.players) > maximum:
        raise DiscussionStateError(f"snapshot exceeds {maximum} players")
    ids = tuple(player.player_id for player in snapshot.players)
    try:
        for index, value in enumerate(ids):
            _require_id(f"snapshot.players[{index}].player_id", value)
    except DiscussionValidationError as error:
        raise DiscussionStateError(str(error)) from error
    if len(ids) != len(set(ids)):
        raise DiscussionStateError("snapshot player IDs must be unique")
    return frozenset(ids)


def _current_explicit_ids(values: list[str | None], current: frozenset[str]) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value is not None and value in current}))


def _actor_target_ids(
    record: HistoryRecord,
    current: frozenset[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    actors: list[str | None] = []
    targets: list[str | None] = []
    if isinstance(record, ChatRecord):
        actors.append(record.player_id)
    elif isinstance(record, (CoDeclarationRecord, CoReportRecord)):
        actors.append(record.player_id)
        if isinstance(record, CoReportRecord):
            targets.append(record.target_player_id)
    elif isinstance(record, AbilityResultRecord):
        targets.append(record.target_player_id)
    elif isinstance(record, DeathRecord):
        targets.append(record.player_id)
    elif isinstance(record, VoteResultRecord):
        targets.extend(record.tallies.keys())
        targets.append(record.lynched_player_id)
        targets.extend(record.runoff_candidate_player_ids)
    elif isinstance(record, VoteRevealRecord):
        actors.append(record.voter_player_id)
        targets.append(record.target_player_id)
        for vote in record.final_votes:
            actors.append(vote.voter_player_id)
            targets.append(vote.target_player_id)
    elif isinstance(record, TieResolvedRandomRecord):
        targets.extend(record.candidate_player_ids)
        targets.append(record.selected_player_id)
    actor_ids = _current_explicit_ids(actors, current)
    target_ids = _current_explicit_ids(targets, current)
    if len(actor_ids) > MAX_EVENT_PLAYERS or len(target_ids) > MAX_EVENT_PLAYERS:
        raise DiscussionStateError("event actor/target bound exceeded")
    return actor_ids, target_ids


_BASE_IMPORTANCE: dict[EvidenceRecordKind, int] = {
    EvidenceRecordKind.CHAT: 40,
    EvidenceRecordKind.CO_DECLARATION: 80,
    EvidenceRecordKind.CO_REPORT: 80,
    EvidenceRecordKind.VOTE_RESULT: 90,
    EvidenceRecordKind.VOTE_REVEAL: 70,
    EvidenceRecordKind.DEATH: 90,
    EvidenceRecordKind.PHASE_TRANSITION: 70,
    EvidenceRecordKind.PHASE_TIMING_CHANGED: 70,
    EvidenceRecordKind.ABILITY_RESULT: 80,
    EvidenceRecordKind.GAME_LIFECYCLE: 90,
    EvidenceRecordKind.PUBLIC_NOTIFY: 90,
    EvidenceRecordKind.TIE_RESOLVED_RANDOM: 70,
    EvidenceRecordKind.KNOWN_UNMODELED: 20,
    EvidenceRecordKind.UNKNOWN: 20,
    EvidenceRecordKind.MALFORMED: 20,
}


def important_event_for_record(
    record: HistoryRecord,
    *,
    bound_context: BoundDiscussionContext,
    snapshot: WorldSnapshot,
    trigger_source: EvidenceRef | None = None,
    remembered_after_world_eviction: bool = False,
) -> ImportantEvent:
    """Create the bounded structural summary for one authorized history record."""

    source = evidence_ref_for_record(record, bound_context)
    if source.record_kind not in _BASE_IMPORTANCE:
        raise DiscussionStateError("transport observation cannot become ImportantEvent")
    current = _known_player_ids(snapshot, MAX_EVENT_PLAYERS)
    actors, targets = _actor_target_ids(record, current)
    importance = _BASE_IMPORTANCE[source.record_kind]
    self_id = bound_context.context.player_id
    if self_id in actors or self_id in targets:
        importance += 10

    channel_id: str | None = None
    text_excerpt: str | None = None
    text_scalars: int | None = None
    text_bytes: int | None = None
    text_truncated = False
    if isinstance(record, ChatRecord):
        channel_id = record.channel
        text = record.message
        text_excerpt = _prefix_by_scalars_and_bytes(text)
        text_scalars = len(text)
        text_bytes = len(text.encode("utf-8"))
        text_truncated = len(text_excerpt) != len(text) or len(text_excerpt.encode("utf-8")) != text_bytes
        self_display_name = next(
            (
                player.display_name
                for player in snapshot.players
                if player.player_id == bound_context.context.player_id
            ),
            None,
        )
        if self_id in text or (
            isinstance(self_display_name, str) and self_display_name and self_display_name in text
        ):
            importance += 10
    elif isinstance(record, CoDeclarationRecord):
        text = record.comment
        text_excerpt = _prefix_by_scalars_and_bytes(text)
        text_scalars = len(text)
        text_bytes = len(text.encode("utf-8"))
        text_truncated = len(text_excerpt) != len(text) or len(text_excerpt.encode("utf-8")) != text_bytes
    if trigger_source is not None and evidence_identity(source) == evidence_identity(trigger_source):
        if source != trigger_source:
            raise DiscussionStateError("trigger visibility conflicts with projected source")
        importance += 20

    marker = isinstance(
        record, (KnownUnmodeledEventRecord, UnknownEventRecord, MalformedEventRecord)
    )
    day = None if marker else getattr(record, "day", None)
    phase = None if marker else getattr(record, "phase", None)
    return ImportantEvent(
        schema_version="aiwolf.important-event.v1",
        source=source,
        day=day,
        phase=phase,
        actor_player_ids=actors,
        target_player_ids=targets,
        channel_id=channel_id,
        importance=min(100, importance),
        text_excerpt=text_excerpt,
        text_original_scalars=text_scalars,
        text_original_utf8_bytes=text_bytes,
        text_truncated=text_truncated,
        remembered_after_world_eviction=remembered_after_world_eviction,
    )


@dataclass(frozen=True)
class _StagedProposal:
    capture: DiscussionCapture
    proposal: DiscussionProposal
    generation_ack: DiscussionGenerationAck
    ack: StageAck
    next_state: DiscussionStateSnapshot


@dataclass(frozen=True)
class _CommittedProposal:
    stage: _StagedProposal
    ack: CommitAck


@dataclass(frozen=True)
class _StartedDispatch:
    committed: _CommittedProposal
    ack: DispatchAck


def _empty_provenance(
    *,
    process_restart: bool,
) -> DiscussionProvenance:
    return DiscussionProvenance(
        schema_version="aiwolf.discussion-provenance.v1",
        history_complete=False,
        co_complete=False,
        ability_results_complete=False,
        world_history_complete=True,
        world_history_dropped_count=0,
        world_history_dropped_through_order=None,
        remembered_after_world_eviction_count=0,
        known_unmodeled_event_count=0,
        unknown_event_count=0,
        malformed_event_count=0,
        model_state_reset=process_restart,
        reset_reason=(DiscussionResetReason.PROCESS_RESTART if process_restart else None),
    )


class DiscussionStateStore:
    """Single-owner deterministic store for one player/context/game tuple."""

    def __init__(
        self,
        bound_context: BoundDiscussionContext,
        config: DiscussionStateConfig = DiscussionStateConfig(),
        *,
        process_restart: bool = False,
    ) -> None:
        if not isinstance(bound_context, BoundDiscussionContext):
            raise DiscussionStateError("bound_context must be BoundDiscussionContext")
        if not isinstance(config, DiscussionStateConfig):
            raise DiscussionStateError("config must be DiscussionStateConfig")
        _require_exact_bool("process_restart", process_restart)
        self._bound = bound_context
        self._config = config
        self._epoch = 0
        self._revision = 0
        self._fact_revision = 0
        self._capture_ordinal = 0
        self._phase_key: tuple[int, str] | None = None
        self._connection_generation: int | None = None
        self._current_player_ids: frozenset[str] = frozenset()
        self._closed = False
        self._owner_token: tuple[int, int | None] | None = None
        self._authority_owner_registration_v2 = None
        self._capture_read_port_v2 = None
        self._offer_preparing_composition_v2 = None
        self._offer_preparing_mode_v2 = False
        self._reset_reason = (
            DiscussionResetReason.PROCESS_RESTART if process_restart else None
        )
        self._source_fingerprints: OrderedDict[
            tuple[EvidenceRecordKind, int], bytes
        ] = OrderedDict()
        self._forgotten_through_order: int | None = None
        self._important_memory: dict[
            tuple[EvidenceRecordKind, int], ImportantEvent
        ] = {}
        self._current_capture: DiscussionCapture | None = None
        self._staged: _StagedProposal | None = None
        self._committed: _CommittedProposal | None = None
        self._dispatch: _StartedDispatch | None = None
        self._delivery: DeliveryAck | None = None
        self._observation: ObservationAck | None = None
        self._last_abort: AbortAck | None = None
        self._state = DiscussionStateSnapshot(
            schema_version="aiwolf.discussion-state.v1",
            game_id=bound_context.context.game_id,
            player_id=bound_context.context.player_id,
            context_sha256=bound_context.context_sha256,
            epoch=0,
            revision=0,
            fact_revision=0,
            world_version=0,
            last_applied_seq=0,
            phase=None,
            day=None,
            assessments=(),
            claims=(),
            relations=(),
            strategy=None,
            important_events=(),
            recent_semantic_turns=(),
            provenance=_empty_provenance(process_restart=process_restart),
        )
        self._enforce_config(self._state)

    @property
    def snapshot(self) -> DiscussionStateSnapshot:
        return self._state

    @property
    def is_closed(self) -> bool:
        return self._closed

    def _create_capture_read_port_v2(self, receipt: object):
        from .authority_capture_bridge_v2 import _create_capture_read_port_v2
        return _create_capture_read_port_v2(self, receipt)

    def _check_owner(self) -> None:
        try:
            loop_id: int | None = id(asyncio.get_running_loop())
        except RuntimeError:
            loop_id = None
        token = (get_ident(), loop_id)
        if self._owner_token is None:
            self._owner_token = token
        elif self._owner_token != token:
            raise DiscussionStateError("discussion store used outside its owning event loop")

    def _require_open(self) -> None:
        self._check_owner()
        if self._closed:
            raise DiscussionStateError("discussion store is closed")

    def _freeze_terminal(self) -> None:
        if self._offer_preparing_composition_v2 is not None:
            from .offer_composition_v2 import _retire_offer_composition_v2
            _retire_offer_composition_v2(self)
        self._invalidate_current_work()
        self._closed = True

    def _invalidate_current_work(self) -> None:
        self._staged = None
        self._current_capture = None

    def _enforce_config(self, state: DiscussionStateSnapshot) -> None:
        checks = (
            (len(state.assessments), self._config.max_assessments, "assessments"),
            (len(state.claims), self._config.max_claims, "claims"),
            (len(state.relations), self._config.max_relations, "relations"),
            (len(state.important_events), self._config.max_important_events, "important_events"),
            (
                len(state.recent_semantic_turns),
                self._config.max_recent_semantic_turns,
                "recent_semantic_turns",
            ),
        )
        for actual, maximum, name in checks:
            if actual > maximum:
                raise DiscussionStateError(f"{name} exceeds configured limit")
        for assessment in state.assessments:
            if len(assessment.evidence) > self._config.max_evidence_per_item:
                raise DiscussionStateError("assessment evidence exceeds configured limit")
        for claim in state.claims:
            if len(claim.evidence) > self._config.max_evidence_per_item:
                raise DiscussionStateError("claim evidence exceeds configured limit")
        for relation in state.relations:
            if len(relation.evidence) > self._config.max_evidence_per_item:
                raise DiscussionStateError("relation evidence exceeds configured limit")
        for turn in state.recent_semantic_turns:
            if len(turn.evidence) > self._config.max_evidence_per_item:
                raise DiscussionStateError("semantic-turn evidence exceeds configured limit")
        if state.strategy is not None:
            if len(state.strategy.evidence) > self._config.max_evidence_per_item:
                raise DiscussionStateError("strategy evidence exceeds configured limit")
            if len(state.strategy.focus_player_ids) > self._config.max_focus_players:
                raise DiscussionStateError("strategy focus exceeds configured limit")
        encoded = canonical_json_bytes(state)
        if len(encoded) > self._config.max_state_bytes:
            raise DiscussionStateError("discussion state exceeds configured byte limit")

    @staticmethod
    def _clean_recovery(
        views: DiscussionViews,
        connection_generation: int,
    ) -> bool:
        transport = views.transport_observations
        if transport is None:
            return False
        barriers = [
            item
            for item in transport.observations
            if isinstance(item, ResumeRecoveryBarrier)
            and item.connection_generation == connection_generation
        ]
        if not barriers:
            return False
        barrier = max(barriers, key=lambda item: item.order)
        return bool(
            barrier.complete
            and barrier.replay_contiguous
            and not barrier.replay_gap_or_floor
        )

    @staticmethod
    def _normalize_records(
        views: DiscussionViews,
    ) -> tuple[tuple[HistoryRecord, bytes], ...]:
        by_identity: dict[tuple[EvidenceRecordKind, int], tuple[HistoryRecord, bytes]] = {}
        records: tuple[HistoryRecord, ...] = (
            tuple(views.history.records)
            + tuple(views.co.records)
            + tuple(views.ability_results.records)
        )
        for record in records:
            kind = _record_kind(record)
            if kind.value in {
                EvidenceRecordKind.ACTION_ACCEPTED.value,
                EvidenceRecordKind.ACTION_REJECTION.value,
                EvidenceRecordKind.PHASE_TIMING.value,
                EvidenceRecordKind.PHASE_DEADLINE_REACHED.value,
                EvidenceRecordKind.RESUME_RECOVERY_BARRIER.value,
            }:
                raise DiscussionStateError("transport record appeared in history evidence")
            identity = (kind, record.order)
            fingerprint = sha256(canonical_json_bytes(record)).digest()
            existing = by_identity.get(identity)
            if existing is not None and existing[1] != fingerprint:
                raise _CanonicalReplayConflict(
                    "same evidence identity has conflicting canonical bytes"
                )
            by_identity[identity] = (record, fingerprint)
        return tuple(
            by_identity[key]
            for key in sorted(by_identity, key=lambda item: (item[1], item[0].value))
        )

    def _updated_fingerprints(
        self,
        records: tuple[tuple[HistoryRecord, bytes], ...],
        *,
        reset: bool,
    ) -> tuple[
        OrderedDict[tuple[EvidenceRecordKind, int], bytes],
        int | None,
    ]:
        journal = OrderedDict() if reset else OrderedDict(self._source_fingerprints)
        forgotten = None if reset else self._forgotten_through_order
        for record, fingerprint in records:
            identity = (_record_kind(record), record.order)
            existing = journal.get(identity)
            if existing is not None:
                if existing != fingerprint:
                    raise _CanonicalReplayConflict("conflicting replay detected")
                journal.move_to_end(identity)
                continue
            if forgotten is not None and record.order <= forgotten:
                raise DiscussionStateError("replay is older than the bounded verification journal")
            journal[identity] = fingerprint
            while len(journal) > self._config.max_source_fingerprints:
                removed, _ = journal.popitem(last=False)
                forgotten = max(forgotten or 0, removed[1])
        return journal, forgotten

    def _select_important_events(
        self,
        current_events: tuple[ImportantEvent, ...],
        *,
        previous: dict[tuple[EvidenceRecordKind, int], ImportantEvent],
        trigger_source: EvidenceRef | None,
    ) -> tuple[ImportantEvent, ...]:
        current_by_id = {evidence_identity(item.source): item for item in current_events}
        candidates: dict[tuple[EvidenceRecordKind, int], ImportantEvent] = {
            identity: replace(item, remembered_after_world_eviction=True)
            for identity, item in previous.items()
            if identity not in current_by_id
        }
        candidates.update(current_by_id)
        required_identity = evidence_identity(trigger_source) if trigger_source is not None else None
        required = current_by_id.get(required_identity) if required_identity is not None else None
        if trigger_source is not None and (required is None or required.source != trigger_source):
            raise DiscussionStateError("peer trigger is absent from current authorized evidence")

        newest = sorted(
            current_by_id.values(),
            key=lambda item: (
                -item.source.order,
                item.source.record_kind.value,
            ),
        )[:12]
        protected: dict[tuple[EvidenceRecordKind, int], ImportantEvent] = {}
        if required_identity is not None and required is not None:
            protected[required_identity] = required
        for item in newest:
            protected[evidence_identity(item.source)] = item

        older = sorted(
            (
                item
                for identity, item in candidates.items()
                if identity not in protected
            ),
            key=lambda item: (
                -item.importance,
                -item.source.order,
                item.source.record_kind.value,
            ),
        )[:12]
        for item in older:
            protected[evidence_identity(item.source)] = item

        if len(protected) > self._config.max_important_events:
            self._invalidate_current_work()
            raise DiscussionStateError(
                "configured event bound cannot retain required projection evidence"
            )

        ranked = sorted(
            (
                item
                for identity, item in candidates.items()
                if identity not in protected
            ),
            key=lambda item: (
                -item.importance,
                -item.source.order,
                item.source.record_kind.value,
            ),
        )
        selected = dict(protected)
        for item in ranked:
            if len(selected) >= self._config.max_important_events:
                break
            selected[evidence_identity(item.source)] = item
        return tuple(
            sorted(selected.values(), key=lambda item: evidence_sort_key(item.source))
        )

    @staticmethod
    def _fact_signature(state: DiscussionStateSnapshot) -> tuple[Any, ...]:
        return (
            state.world_version,
            state.last_applied_seq,
            state.phase,
            state.day,
            state.important_events,
            state.provenance,
        )

    @staticmethod
    def _captured_events(
        capture: DiscussionCapture,
    ) -> dict[tuple[EvidenceRecordKind, int], ImportantEvent]:
        return {
            evidence_identity(event.source): event for event in capture.evidence
        }

    def _validate_proposal_for_capture(
        self,
        capture: DiscussionCapture,
        proposal: DiscussionProposal,
    ) -> None:
        if proposal.base_revision != capture.base_revision:
            raise DiscussionStateError("proposal base_revision does not match capture")

        event_by_identity = self._captured_events(capture)
        for reference in _proposal_evidence(proposal):
            event = event_by_identity.get(evidence_identity(reference))
            if event is None or event.source != reference:
                raise DiscussionStateError(
                    "proposal evidence is absent from the captured authorized projection"
                )

        players = self._current_player_ids
        self_id = self._bound.context.player_id

        def require_player(player_id: str, field_name: str, *, peer: bool = False) -> None:
            if player_id not in players or (peer and player_id == self_id):
                qualifier = "current peer" if peer else "current player"
                raise DiscussionStateError(f"{field_name} must identify a {qualifier}")

        speech = proposal.speech_act
        if isinstance(speech, SpeechActClaim):
            require_player(speech.subject_player_id, "claim subject")
        elif isinstance(speech, SpeechActQuestion):
            require_player(speech.addressee_player_id, "question addressee")
            if speech.subject_player_id is not None:
                require_player(speech.subject_player_id, "question subject")
        elif isinstance(speech, (SpeechActAnswer, SpeechActRebuttal)):
            source = event_by_identity[evidence_identity(speech.in_reply_to)]
            if (
                source.source.record_kind
                not in {
                    EvidenceRecordKind.CHAT,
                    EvidenceRecordKind.CO_DECLARATION,
                    EvidenceRecordKind.CO_REPORT,
                }
                or len(source.actor_player_ids) != 1
                or source.actor_player_ids[0] == self_id
                or speech.addressee_player_id != source.actor_player_ids[0]
            ):
                raise DiscussionStateError(
                    "answer/rebuttal addressee must equal the authorized peer source actor"
                )
        elif isinstance(speech, SpeechActOpinionChange):
            require_player(speech.subject_player_id, "opinion-change subject", peer=True)
            prior = next(
                (
                    value
                    for value in self._state.assessments
                    if value.player_id == speech.subject_player_id
                ),
                None,
            )
            if prior is None:
                raise DiscussionStateError("opinion change cannot fabricate an absent prior")
            prior_value = (
                prior.suspicion
                if speech.dimension is OpinionDimension.SUSPICION
                else prior.credibility
            )
            if speech.prior != prior_value:
                raise DiscussionStateError("opinion-change prior does not match stored state")
            existing = {evidence_identity(item) for item in prior.evidence}
            if all(evidence_identity(item) in existing for item in speech.causes):
                raise DiscussionStateError("opinion change requires a newly included cause")
        elif isinstance(speech, SpeechActRelationHypothesis):
            require_player(speech.source_player_id, "relation source")
            require_player(speech.target_player_id, "relation target")

        for update in proposal.assessment_updates:
            require_player(update.target_player_id, "assessment target", peer=True)
        for update in proposal.claim_updates:
            source = event_by_identity[evidence_identity(update.claim)]
            if (
                len(source.actor_player_ids) != 1
                or source.actor_player_ids[0] == self_id
                or update.speaker_player_id != source.actor_player_ids[0]
            ):
                raise DiscussionStateError(
                    "claim speaker must equal the authorized non-self source actor"
                )
        for update in proposal.relation_updates:
            require_player(update.source_player_id, "relation-update source")
            require_player(update.target_player_id, "relation-update target")
        if proposal.strategy_update is not None:
            for player_id in proposal.strategy_update.focus_player_ids:
                require_player(player_id, "strategy focus")

        trigger = capture.trigger
        expected_decisions = {
            "INITIAL_CHAT": {"none", "chat"},
            "PEER_CHAT": {"none", "chat"},
            "CO_OPPORTUNITY": {"none", "co_declare"},
            "PRE_VOTE": {"none", "vote"},
            "ABILITY": {"none", "ability"},
        }
        if proposal.decision_kind not in expected_decisions[trigger.kind]:
            raise DiscussionStateError("proposal decision kind does not match its trigger family")

        if trigger.kind == "PEER_CHAT":
            if proposal.reaction is None or proposal.reaction.trigger != trigger.source:
                raise DiscussionStateError("peer-chat proposal requires the exact trigger reaction")
        elif proposal.reaction is not None:
            raise DiscussionStateError("only peer-chat proposals may carry a reaction")

        if trigger.kind == "CO_OPPORTUNITY":
            if proposal.co_judgment is None:
                raise DiscussionStateError("CO opportunity requires a CoJudgment")
            declares = proposal.co_judgment.decision is CoJudgmentDecision.DECLARE
            if declares:
                if (
                    proposal.decision_kind != "co_declare"
                    or proposal.option_id != proposal.co_judgment.selected_option_id
                ):
                    raise DiscussionStateError("CO declaration identity does not match judgment")
            elif proposal.decision_kind != "none":
                raise DiscussionStateError("CO silence/defer requires no decision")
        elif proposal.co_judgment is not None:
            raise DiscussionStateError("CoJudgment is outside a CO opportunity")

        if trigger.kind == "PRE_VOTE":
            reassessment = proposal.pre_vote_reassessment
            if reassessment is None:
                raise DiscussionStateError("pre-vote trigger requires a reassessment")
            for player_id in reassessment.ranked_target_player_ids:
                require_player(player_id, "ranked vote target")
            if proposal.decision_kind == "vote":
                if proposal.option_id != reassessment.option_id:
                    raise DiscussionStateError("vote option does not match reassessment")
                if reassessment.preferred_target_player_id is None:
                    raise DiscussionStateError("vote decision requires a preferred target")
            elif reassessment.preferred_target_player_id is not None:
                raise DiscussionStateError("vote abstention requires a null preferred target")
        elif proposal.pre_vote_reassessment is not None:
            raise DiscussionStateError("PreVoteReassessment is outside a vote opportunity")

    def _next_model_state(
        self,
        capture: DiscussionCapture,
        request_id: str,
        proposal: DiscussionProposal,
        proposal_sha256: str,
    ) -> DiscussionStateSnapshot:
        assessments = {item.player_id: item for item in self._state.assessments}
        for update in proposal.assessment_updates:
            assessments[update.target_player_id] = PlayerAssessment(
                player_id=update.target_player_id,
                suspicion=update.suspicion,
                credibility=update.credibility,
                confidence=update.confidence,
                evidence=update.evidence,
            )

        claims = {evidence_identity(item.claim): item for item in self._state.claims}
        for update in proposal.claim_updates:
            claims[evidence_identity(update.claim)] = ClaimAssessment(
                claim=update.claim,
                speaker_player_id=update.speaker_player_id,
                verdict=update.verdict,
                confidence=update.confidence,
                evidence=update.evidence,
            )

        relations = {
            (item.source_player_id, item.target_player_id, item.relation): item
            for item in self._state.relations
        }
        for update in proposal.relation_updates:
            relations[(update.source_player_id, update.target_player_id, update.relation)] = (
                RelationHypothesis(
                    source_player_id=update.source_player_id,
                    target_player_id=update.target_player_id,
                    relation=update.relation,
                    confidence=update.confidence,
                    evidence=update.evidence,
                    provenance=update.provenance,
                )
            )

        strategy = self._state.strategy
        if proposal.strategy_update is not None:
            update = proposal.strategy_update
            strategy = StrategyState(
                scope=update.scope,
                mode=update.mode,
                focus_player_ids=update.focus_player_ids,
                evidence=update.evidence,
            )

        evidence_by_identity = {
            evidence_identity(item): item for item in _proposal_evidence(proposal)
        }
        turn = RecentSemanticTurn(
            schema_version="aiwolf.recent-semantic-turn.v1",
            committed_revision=capture.base_revision + 1,
            capture_id=capture.capture_id,
            request_id=request_id,
            day=capture.trigger.day,
            phase=capture.trigger.phase,
            decision_kind=proposal.decision_kind,
            option_id=proposal.option_id,
            speech_act_kind=proposal.speech_act.kind,
            evidence=tuple(
                sorted(evidence_by_identity.values(), key=evidence_sort_key)
            ),
            proposal_sha256=proposal_sha256,
        )
        turns = self._state.recent_semantic_turns + (turn,)
        if self._config.max_recent_semantic_turns == 0:
            turns = ()
        else:
            turns = turns[-self._config.max_recent_semantic_turns :]
        next_state = replace(
            self._state,
            revision=capture.base_revision + 1,
            assessments=tuple(sorted(assessments.values(), key=lambda item: item.player_id)),
            claims=tuple(
                sorted(claims.values(), key=lambda item: evidence_sort_key(item.claim))
            ),
            relations=tuple(
                sorted(
                    relations.values(),
                    key=lambda item: (
                        item.source_player_id,
                        item.target_player_id,
                        item.relation.value,
                    ),
                )
            ),
            strategy=strategy,
            recent_semantic_turns=turns,
        )
        self._enforce_config(next_state)
        return next_state

    def capture(
        self,
        views: DiscussionViews,
        trigger: DiscussionTrigger,
    ) -> DiscussionCapture:
        self._require_open()
        if not isinstance(views, DiscussionViews) or not isinstance(trigger, DiscussionTrigger):
            raise DiscussionStateError("capture requires DiscussionViews and DiscussionTrigger")
        snapshot = views.snapshot
        if snapshot.freshness in {Freshness.ENDED, Freshness.FAILED}:
            self._freeze_terminal()
            raise DiscussionStateError("World is terminal; discussion store was frozen")
        if snapshot.freshness is not Freshness.CURRENT:
            raise DiscussionStateError("capture requires a CURRENT World snapshot")
        try:
            validate_bound_context_snapshot(self._bound, snapshot)
        except DiscussionContextError:
            self._freeze_terminal()
            raise
        if not snapshot.is_caught_up:
            raise DiscussionStateError("capture requires a caught-up CURRENT snapshot")
        if snapshot.phase is None:
            raise DiscussionStateError("CURRENT snapshot has no phase")
        reconnect = (
            self._connection_generation is not None
            and trigger.connection_generation != self._connection_generation
        )
        reset = reconnect and not self._clean_recovery(views, trigger.connection_generation)
        phase_key = (snapshot.phase.day, snapshot.phase.phase)
        phase_changed = self._phase_key is not None and phase_key != self._phase_key
        if reset or phase_changed:
            self._invalidate_current_work()
        if trigger.day != snapshot.phase.day or trigger.phase != snapshot.phase.phase:
            raise DiscussionStateError("trigger does not match the authoritative phase")
        current_players = _known_player_ids(snapshot, self._config.max_players)
        if self._bound.context.player_id not in current_players:
            raise DiscussionStateError("bound player is absent from current snapshot")
        if self._staged is not None and not (reset or phase_changed):
            raise DiscussionStateError("cannot capture while a proposal is staged")
        if self._committed is not None and self._delivery is None:
            raise DiscussionStateError("cannot capture before a committed proposal is finalized")
        if self._dispatch is not None and self._delivery is None:
            raise DiscussionStateError("cannot capture while dispatch is unresolved")
        if (
            self._delivery is not None
            and self._delivery.status is DiscussionDeliveryStatus.LOCAL_SENT
            and self._observation is None
        ):
            raise DiscussionStateError("cannot capture with unresolved authoritative observation")

        try:
            records = self._normalize_records(views)
            journal, forgotten = self._updated_fingerprints(records, reset=reset)
            current_events = tuple(
                important_event_for_record(
                    record,
                    bound_context=self._bound,
                    snapshot=snapshot,
                    trigger_source=trigger.source,
                )
                for record, _ in records
            )
        except (DiscussionContextError, _CanonicalReplayConflict):
            self._freeze_terminal()
            raise
        if trigger.source is not None:
            trigger_event = next(
                (
                    item
                    for item in current_events
                    if evidence_identity(item.source) == evidence_identity(trigger.source)
                ),
                None,
            )
            if (
                trigger_event is None
                or trigger_event.source != trigger.source
                or not trigger_event.actor_player_ids
                or self._bound.context.player_id in trigger_event.actor_player_ids
            ):
                raise DiscussionStateError("PEER_CHAT source is not a current authorized peer record")

        base_state = self._state
        epoch = self._epoch + 1 if reset else self._epoch
        revision = 0 if reset else self._revision
        base_fact_revision = 0 if reset else self._fact_revision
        assessments = () if reset else base_state.assessments
        claims = () if reset else base_state.claims
        relations = () if reset else base_state.relations
        recent = () if reset else base_state.recent_semantic_turns
        strategy = None if reset else base_state.strategy
        if phase_changed and strategy is not None and strategy.scope == "PHASE":
            strategy = None
        previous_memory = {} if reset else self._important_memory
        important = self._select_important_events(
            current_events,
            previous=previous_memory,
            trigger_source=trigger.source,
        )
        provenance = DiscussionProvenance(
            schema_version="aiwolf.discussion-provenance.v1",
            history_complete=views.history.complete,
            co_complete=views.co.complete,
            ability_results_complete=views.ability_results.complete,
            world_history_complete=snapshot.history_retention.complete,
            world_history_dropped_count=snapshot.history_retention.dropped_count,
            world_history_dropped_through_order=snapshot.history_retention.dropped_through_order,
            remembered_after_world_eviction_count=sum(
                item.remembered_after_world_eviction for item in important
            ),
            known_unmodeled_event_count=snapshot.known_unmodeled_event_count,
            unknown_event_count=snapshot.unknown_event_count,
            malformed_event_count=snapshot.malformed_event_count,
            model_state_reset=reset or self._reset_reason is not None,
            reset_reason=(DiscussionResetReason.RECOVERY_GAP if reset else self._reset_reason),
        )
        try:
            provisional = DiscussionStateSnapshot(
                schema_version="aiwolf.discussion-state.v1",
                game_id=self._bound.context.game_id,
                player_id=self._bound.context.player_id,
                context_sha256=self._bound.context_sha256,
                epoch=epoch,
                revision=revision,
                fact_revision=base_fact_revision,
                world_version=snapshot.version,
                last_applied_seq=snapshot.last_applied_seq,
                phase=snapshot.phase.phase,
                day=snapshot.phase.day,
                assessments=assessments,
                claims=claims,
                relations=relations,
                strategy=strategy,
                important_events=important,
                recent_semantic_turns=recent,
                provenance=provenance,
            )
            previous_for_compare = base_state
            fact_changed = reset or self._fact_signature(provisional) != self._fact_signature(
                previous_for_compare
            )
            fact_revision = base_fact_revision + (1 if fact_changed else 0)
            next_state = replace(provisional, fact_revision=fact_revision)
            self._enforce_config(next_state)
        except DiscussionValidationError as error:
            message = str(error)
            if message not in {
                "discussion state exceeds 64 KiB",
                "discussion state exceeds configured byte limit",
            }:
                raise
            self._invalidate_current_work()
            if isinstance(error, DiscussionStateError):
                raise
            raise DiscussionStateError(
                "discussion state exceeds configured byte limit"
            ) from error

        capture_ordinal = self._capture_ordinal + 1
        state_hash = canonical_sha256(next_state)
        material = {
            "schema_version": "aiwolf.discussion-capture.v1",
            "capture_ordinal": capture_ordinal,
            "game_id": self._bound.context.game_id,
            "player_id": self._bound.context.player_id,
            "context_sha256": self._bound.context_sha256,
            "state_sha256": state_hash,
            "epoch": epoch,
            "base_revision": revision,
            "fact_revision": fact_revision,
            "world_version": snapshot.version,
            "last_applied_seq": snapshot.last_applied_seq,
            "trigger": trigger,
            "context": self._bound.context,
            "state": next_state,
            "evidence": important,
        }
        capture = DiscussionCapture(capture_id=canonical_sha256(material), **material)

        self._epoch = epoch
        self._revision = revision
        self._fact_revision = fact_revision
        self._capture_ordinal = capture_ordinal
        self._phase_key = phase_key
        self._connection_generation = trigger.connection_generation
        self._current_player_ids = current_players
        self._reset_reason = provenance.reset_reason
        self._source_fingerprints = journal
        self._forgotten_through_order = forgotten
        self._important_memory = {
            evidence_identity(item.source): item for item in important
        }
        self._state = next_state
        self._current_capture = capture
        self._staged = None
        self._committed = None
        self._dispatch = None
        self._delivery = None
        self._observation = None
        self._last_abort = None
        return capture

    def stage(
        self,
        capture: DiscussionCapture,
        request_id: str,
        proposal: DiscussionProposal,
        generation_ack: DiscussionGenerationAck,
    ) -> StageAck:
        self._require_open()
        if not isinstance(capture, DiscussionCapture):
            raise DiscussionStateError("stage requires a DiscussionCapture")
        if not isinstance(proposal, DiscussionProposal):
            raise DiscussionStateError("stage requires a DiscussionProposal")
        if not isinstance(generation_ack, DiscussionGenerationAck):
            raise DiscussionStateError("stage requires a DiscussionGenerationAck")
        if self._current_capture is None or capture != self._current_capture:
            raise DiscussionStateError("stage capture is not the current exact capture")
        if self._staged is not None:
            raise DiscussionStateError("duplicate stage")
        if self._committed is not None or self._delivery is not None:
            raise DiscussionStateError("capture transaction is already finalized")

        expected_request_id = f"phase6:{capture.capture_id}"
        if request_id != expected_request_id:
            raise DiscussionStateError("stage request_id does not match capture")
        if (
            capture.base_revision != self._revision
            or capture.state != self._state
            or capture.state_sha256 != canonical_sha256(self._state)
        ):
            raise DiscussionStateError("stage capture lost its base-revision CAS")
        proposal_digest = canonical_sha256(proposal)
        if generation_ack.generation_status not in {
            AiDiscussionGenerationStatus.DECISION,
            AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
            AiDiscussionGenerationStatus.REPAIR_SUCCEEDED,
        }:
            raise DiscussionStateError("stage requires a successful generation acknowledgement")
        if (
            generation_ack.capture_id != capture.capture_id
            or generation_ack.request_id != request_id
            or generation_ack.context_sha256 != capture.context_sha256
            or generation_ack.before_state_sha256 != capture.state_sha256
            or generation_ack.proposal_sha256 != proposal_digest
        ):
            raise DiscussionStateError("generation acknowledgement does not match stage material")
        if (
            generation_ack.generation_status
            is AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION
            and proposal.decision_kind != "none"
        ):
            raise DiscussionStateError("EXPLICIT_NO_DECISION requires a none proposal")
        if (
            generation_ack.generation_status is AiDiscussionGenerationStatus.DECISION
            and proposal.decision_kind == "none"
        ):
            raise DiscussionStateError("DECISION generation requires an action proposal")

        self._validate_proposal_for_capture(capture, proposal)
        next_state = self._next_model_state(
            capture,
            request_id,
            proposal,
            proposal_digest,
        )
        ack = StageAck(
            capture_id=capture.capture_id,
            request_id=request_id,
            base_revision=capture.base_revision,
            context_sha256=capture.context_sha256,
            before_state_sha256=capture.state_sha256,
            after_state_sha256=None,
            proposal_sha256=proposal_digest,
            staged=True,
        )
        self._staged = _StagedProposal(
            capture=capture,
            proposal=proposal,
            generation_ack=generation_ack,
            ack=ack,
            next_state=next_state,
        )
        return ack

    def commit(self, stage_ack: StageAck) -> CommitAck:
        self._require_open()
        if not isinstance(stage_ack, StageAck):
            raise DiscussionStateError("commit requires a StageAck")
        staged = self._staged
        if staged is None:
            raise DiscussionStateError("no proposal is staged")
        if stage_ack != staged.ack:
            raise DiscussionStateError("commit acknowledgement does not match the exact stage")
        if (
            self._revision != staged.capture.base_revision
            or self._state != staged.capture.state
            or canonical_sha256(self._state) != staged.ack.before_state_sha256
        ):
            raise DiscussionStateError("commit base-revision CAS failed")

        after_state_sha256 = canonical_sha256(staged.next_state)
        ack = CommitAck(
            capture_id=stage_ack.capture_id,
            request_id=stage_ack.request_id,
            base_revision=stage_ack.base_revision,
            committed_revision=stage_ack.base_revision + 1,
            context_sha256=stage_ack.context_sha256,
            before_state_sha256=stage_ack.before_state_sha256,
            after_state_sha256=after_state_sha256,
            proposal_sha256=stage_ack.proposal_sha256,
            committed=True,
        )
        committed = _CommittedProposal(stage=staged, ack=ack)
        self._state = staged.next_state
        self._revision = ack.committed_revision
        self._staged = None
        self._committed = committed
        return ack

    def abort(
        self,
        capture_id: str,
        request_id: str,
        reason: DiscussionAbortReason,
        proposal_sha256: str | None = None,
    ) -> AbortAck:
        self._require_open()
        if not isinstance(reason, DiscussionAbortReason):
            raise DiscussionStateError("abort reason is not closed")
        if self._last_abort is not None:
            previous = self._last_abort
            if (
                previous.capture_id == capture_id
                and previous.request_id == request_id
                and previous.reason is reason
                and previous.proposal_sha256 == proposal_sha256
            ):
                return previous
            raise DiscussionStateError("conflicting duplicate abort")
        capture = self._current_capture
        if capture is None or capture.capture_id != capture_id:
            raise DiscussionStateError("abort capture is not current")
        if request_id != f"phase6:{capture_id}":
            raise DiscussionStateError("abort request_id does not match capture")
        if self._committed is not None:
            raise DiscussionStateError("a committed proposal cannot be aborted")

        before_stage = {
            DiscussionAbortReason.PROMPT_REJECTED,
            DiscussionAbortReason.BACKEND_FAILED,
            DiscussionAbortReason.FINAL_OUTPUT_INVALID,
            DiscussionAbortReason.CANCELLED_BEFORE_RESULT,
            DiscussionAbortReason.AUDIT_FAILED,
            DiscussionAbortReason.STAGE_FAILED,
        }
        stage_existed = self._staged is not None
        if reason in before_stage and stage_existed:
            raise DiscussionStateError("before-stage abort cannot discard an existing stage")
        if reason not in before_stage and not stage_existed:
            raise DiscussionStateError("after-stage abort requires the exact stage")
        if self._staged is not None and proposal_sha256 != self._staged.ack.proposal_sha256:
            raise DiscussionStateError("abort proposal digest does not match the stage")

        ack = AbortAck(
            capture_id=capture.capture_id,
            request_id=request_id,
            base_revision=capture.base_revision,
            context_sha256=capture.context_sha256,
            before_state_sha256=capture.state_sha256,
            after_state_sha256=None,
            proposal_sha256=proposal_sha256,
            reason=reason,
            stage_existed=stage_existed,
            aborted=True,
        )
        self._staged = None
        self._current_capture = None
        self._last_abort = ack
        return ack

    def _require_committed(self, commit_ack: CommitAck) -> _CommittedProposal:
        if not isinstance(commit_ack, CommitAck):
            raise DiscussionStateError("operation requires a CommitAck")
        committed = self._committed
        if committed is None or committed.ack != commit_ack:
            raise DiscussionStateError("commit acknowledgement is not the current exact commit")
        return committed

    def finish_no_action(self, commit_ack: CommitAck) -> DeliveryAck:
        self._require_open()
        committed = self._require_committed(commit_ack)
        if committed.stage.proposal.decision_kind != "none":
            raise DiscussionStateError("NO_ACTION requires the staged none proposal")
        if self._dispatch is not None or self._delivery is not None:
            raise DiscussionStateError("proposal delivery is already finalized")
        ack = DeliveryAck(
            capture_id=commit_ack.capture_id,
            request_id=commit_ack.request_id,
            base_revision=commit_ack.base_revision,
            committed_revision=commit_ack.committed_revision,
            context_sha256=commit_ack.context_sha256,
            before_state_sha256=commit_ack.before_state_sha256,
            after_state_sha256=commit_ack.after_state_sha256,
            proposal_sha256=commit_ack.proposal_sha256,
            status=DiscussionDeliveryStatus.NO_ACTION,
            action=None,
            option_id=None,
            request_event_id=None,
            send_connection_generation=None,
            correlation=None,
        )
        self._delivery = ack
        return ack

    def mark_dispatch_started(
        self,
        commit_ack: CommitAck,
        action: str,
        option_id: str,
    ) -> DispatchAck:
        self._require_open()
        committed = self._require_committed(commit_ack)
        proposal = committed.stage.proposal
        if proposal.decision_kind == "none":
            raise DiscussionStateError("none proposal cannot start dispatch")
        if action != proposal.decision_kind or option_id != proposal.option_id:
            raise DiscussionStateError("dispatch identity does not match the staged proposal")
        if self._dispatch is not None or self._delivery is not None:
            raise DiscussionStateError("duplicate dispatch")
        ack = DispatchAck(
            capture_id=commit_ack.capture_id,
            request_id=commit_ack.request_id,
            base_revision=commit_ack.base_revision,
            committed_revision=commit_ack.committed_revision,
            context_sha256=commit_ack.context_sha256,
            before_state_sha256=commit_ack.before_state_sha256,
            after_state_sha256=commit_ack.after_state_sha256,
            proposal_sha256=commit_ack.proposal_sha256,
            action=action,  # type: ignore[arg-type]
            option_id=option_id,
        )
        self._dispatch = _StartedDispatch(committed=committed, ack=ack)
        return ack

    @staticmethod
    def _receipt_fields(receipt: object) -> tuple[str, int]:
        if not is_dataclass(receipt) or isinstance(receipt, type):
            raise DiscussionStateError("LOCAL_SENT requires the exact receipt record shape")
        if tuple(item.name for item in fields(receipt)) != (
            "event_id",
            "connection_generation",
            "sent",
        ):
            raise DiscussionStateError("receipt has absent or extra fields")
        event_id = getattr(receipt, "event_id")
        connection_generation = getattr(receipt, "connection_generation")
        try:
            _require_id("receipt.event_id", event_id)
            _require_int("receipt.connection_generation", connection_generation)
            _require_exact_bool("receipt.sent", getattr(receipt, "sent"))
        except DiscussionValidationError as error:
            raise DiscussionStateError(str(error)) from error
        if getattr(receipt, "sent") is not True:
            raise DiscussionStateError("LOCAL_SENT receipt must prove sent=True")
        return event_id, connection_generation

    def finish_dispatch(
        self,
        dispatch_ack: DispatchAck,
        status: DiscussionDeliveryStatus,
        receipt: object = None,
    ) -> DeliveryAck:
        self._require_open()
        if not isinstance(dispatch_ack, DispatchAck):
            raise DiscussionStateError("finish_dispatch requires a DispatchAck")
        if not isinstance(status, DiscussionDeliveryStatus) or status is DiscussionDeliveryStatus.NO_ACTION:
            raise DiscussionStateError("finish_dispatch requires an action delivery status")
        started = self._dispatch
        if started is None or started.ack != dispatch_ack:
            raise DiscussionStateError("dispatch acknowledgement is not the current exact dispatch")
        if self._delivery is not None:
            raise DiscussionStateError("dispatch delivery is already finalized")

        request_event_id: str | None = None
        send_generation: int | None = None
        correlation: DiscussionDispatchCorrelation | None = None
        if status is DiscussionDeliveryStatus.LOCAL_SENT:
            request_event_id, send_generation = self._receipt_fields(receipt)
            correlation = DiscussionDispatchCorrelation(
                capture_id=dispatch_ack.capture_id,
                request_id=dispatch_ack.request_id,
                context_sha256=dispatch_ack.context_sha256,
                before_state_sha256=dispatch_ack.before_state_sha256,
                after_state_sha256=dispatch_ack.after_state_sha256,
                proposal_sha256=dispatch_ack.proposal_sha256,
                generation_audit_sequence=started.committed.stage.generation_ack.audit_sequence,
                base_revision=dispatch_ack.base_revision,
                committed_revision=dispatch_ack.committed_revision,
                action=dispatch_ack.action,
                option_id=dispatch_ack.option_id,
                request_event_id=request_event_id,
                send_connection_generation=send_generation,
            )
        elif receipt is not None:
            raise DiscussionStateError("unsent delivery cannot carry a receipt")
        ack = DeliveryAck(
            capture_id=dispatch_ack.capture_id,
            request_id=dispatch_ack.request_id,
            base_revision=dispatch_ack.base_revision,
            committed_revision=dispatch_ack.committed_revision,
            context_sha256=dispatch_ack.context_sha256,
            before_state_sha256=dispatch_ack.before_state_sha256,
            after_state_sha256=dispatch_ack.after_state_sha256,
            proposal_sha256=dispatch_ack.proposal_sha256,
            status=status,
            action=dispatch_ack.action,
            option_id=dispatch_ack.option_id,
            request_event_id=request_event_id,
            send_connection_generation=send_generation,
            correlation=correlation,
        )
        self._delivery = ack
        return ack

    def observe_authoritative(
        self,
        correlation: DiscussionDispatchCorrelation,
        status: DiscussionObservationStatus,
        evidence: EvidenceRef | None = None,
    ) -> ObservationAck:
        self._require_open()
        if not isinstance(correlation, DiscussionDispatchCorrelation):
            raise DiscussionStateError("observation requires a DiscussionDispatchCorrelation")
        if not isinstance(status, DiscussionObservationStatus):
            raise DiscussionStateError("observation status is not closed")
        if (
            self._delivery is None
            or self._delivery.status is not DiscussionDeliveryStatus.LOCAL_SENT
            or self._delivery.correlation != correlation
        ):
            raise DiscussionStateError("observation correlation is not the exact local send")

        if status is DiscussionObservationStatus.ACCEPTED and evidence is not None:
            expected_kinds = {
                "chat": {EvidenceRecordKind.CHAT},
                "co_declare": {EvidenceRecordKind.CO_DECLARATION},
                "vote": {EvidenceRecordKind.ACTION_ACCEPTED},
                "ability": {EvidenceRecordKind.ACTION_ACCEPTED},
            }
            if evidence.record_kind not in expected_kinds[correlation.action]:
                raise DiscussionStateError("acceptance evidence kind does not match action")
        if (
            status is DiscussionObservationStatus.REJECTED
            and evidence is not None
            and evidence.record_kind is not EvidenceRecordKind.ACTION_REJECTION
        ):
            raise DiscussionStateError("rejection evidence must be an action rejection")

        ack = ObservationAck(
            capture_id=correlation.capture_id,
            request_id=correlation.request_id,
            base_revision=correlation.base_revision,
            committed_revision=correlation.committed_revision,
            context_sha256=correlation.context_sha256,
            before_state_sha256=correlation.before_state_sha256,
            after_state_sha256=correlation.after_state_sha256,
            proposal_sha256=correlation.proposal_sha256,
            generation_audit_sequence=correlation.generation_audit_sequence,
            action=correlation.action,
            option_id=correlation.option_id,
            request_event_id=correlation.request_event_id,
            send_connection_generation=correlation.send_connection_generation,
            status=status,
            authoritative_evidence=evidence,
        )
        if self._observation is not None:
            if self._observation == ack:
                return self._observation
            raise DiscussionStateError("conflicting duplicate authoritative observation")
        self._observation = ack
        return ack

    def close(self) -> None:
        self._check_owner()
        if self._closed:
            return
        if self._committed is not None and self._delivery is None:
            raise DiscussionStateError(
                "committed proposal requires delivery finalization before close"
            )
        if (
            self._delivery is not None
            and self._delivery.status is DiscussionDeliveryStatus.LOCAL_SENT
            and self._observation is None
        ):
            raise DiscussionStateError(
                "LOCAL_SENT correlation requires authoritative finalization before close"
            )
        if self._offer_preparing_composition_v2 is not None:
            from .offer_composition_v2 import _retire_offer_composition_v2
            _retire_offer_composition_v2(self)
        self._staged = None
        self._current_capture = None
        self._committed = None
        self._dispatch = None
        self._closed = True
