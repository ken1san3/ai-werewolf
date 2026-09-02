"""Immutable public models for the World State and bounded memory.

The world package deliberately exposes facts, not the protocol dictionaries that
carried those facts.  Every collection in this module is normalized to a tuple
or a read-only mapping at construction time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, TypeAlias


def freeze(value: Any) -> Any:
    """Recursively convert protocol-shaped values to immutable values."""

    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze(item) for item in value)
    return value


class Freshness(str, Enum):
    EMPTY = "EMPTY"
    CURRENT = "CURRENT"
    STALE = "STALE"
    ENDED = "ENDED"
    FAILED = "FAILED"


class WorldStateExitReason(str, Enum):
    STOPPED = "STOPPED"
    SOURCE_CLOSED = "SOURCE_CLOSED"
    CLIENT_ENDED = "CLIENT_ENDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class WorldStateConfig:
    max_history_records: int = 1024
    max_history_bytes: int = 2 * 1024 * 1024

    def __post_init__(self) -> None:
        if isinstance(self.max_history_records, bool) or self.max_history_records < 1:
            raise ValueError("max_history_records must be a positive integer")
        if isinstance(self.max_history_bytes, bool) or self.max_history_bytes < 1:
            raise ValueError("max_history_bytes must be a positive integer")


@dataclass(frozen=True)
class PlayerView:
    player_id: str
    display_name: str
    alive: bool = True
    death: "DeathView | None" = None

    @property
    def is_alive(self) -> bool:
        return self.alive


@dataclass(frozen=True)
class DeathView:
    player_id: str
    day: int | None
    public_cause: str | None = None

    @property
    def cause(self) -> str | None:
        return self.public_cause


@dataclass(frozen=True)
class PhaseView:
    phase: str
    day: int
    phase_ends_at: int | None = None

    @property
    def deadline(self) -> int | None:
        return self.phase_ends_at


@dataclass(frozen=True)
class SelfView:
    player_id: str
    role_id: str
    modifier_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "modifier_ids", tuple(self.modifier_ids))

    @property
    def modifiers(self) -> tuple[str, ...]:
        return self.modifier_ids


@dataclass(frozen=True)
class VoteEntry:
    voter_player_id: str
    target_player_id: str | None


@dataclass(frozen=True)
class ChatRecord:
    order: int
    day: int | None
    phase: str | None
    channel: str
    player_id: str | None
    display_name: str | None
    message: str
    kind: ClassVar[str] = "chat"
    record_kind: ClassVar[str] = "chat"


@dataclass(frozen=True)
class CoDeclarationRecord:
    order: int
    day: int | None
    phase: str | None
    player_id: str
    claimed_role_id: str
    comment: str
    kind: ClassVar[str] = "co_declaration"
    record_kind: ClassVar[str] = "co_declaration"


@dataclass(frozen=True)
class CoReportRecord:
    order: int
    day: int | None
    phase: str | None
    player_id: str
    kind: str
    target_player_id: str
    claimed_result: str
    record_kind: ClassVar[str] = "co_report"

    @property
    def report_kind(self) -> str:
        return self.kind


@dataclass(frozen=True)
class VoteResultRecord:
    order: int
    day: int | None
    phase: str | None
    result_id: str
    tallies: Mapping[str, int] = field(default_factory=dict)
    lynched_player_id: str | None = None
    runoff_candidate_player_ids: tuple[str, ...] = ()
    kind: ClassVar[str] = "vote_result"
    record_kind: ClassVar[str] = "vote_result"

    def __post_init__(self) -> None:
        object.__setattr__(self, "tallies", MappingProxyType(dict(self.tallies)))
        object.__setattr__(self, "runoff_candidate_player_ids", tuple(self.runoff_candidate_player_ids))

    @property
    def result(self) -> str:
        return self.result_id


@dataclass(frozen=True)
class VoteRevealRecord:
    order: int
    day: int | None
    phase: str | None
    voter_player_id: str | None = None
    target_player_id: str | None = None
    final_votes: tuple[VoteEntry, ...] = ()
    kind: ClassVar[str] = "vote_reveal"
    record_kind: ClassVar[str] = "vote_reveal"

    def __post_init__(self) -> None:
        object.__setattr__(self, "final_votes", tuple(self.final_votes))

    @property
    def votes(self) -> tuple[VoteEntry, ...]:
        return self.final_votes


@dataclass(frozen=True)
class DeathRecord:
    order: int
    day: int | None
    phase: str | None
    player_id: str
    public_cause: str | None
    kind: ClassVar[str] = "death"
    record_kind: ClassVar[str] = "death"


@dataclass(frozen=True)
class PhaseTransitionRecord:
    order: int
    day: int | None
    phase: str
    phase_ends_at: int | None
    kind: ClassVar[str] = "phase_transition"
    record_kind: ClassVar[str] = "phase_transition"


@dataclass(frozen=True)
class PhaseTimingChangedRecord:
    order: int
    day: int | None
    phase: str | None
    event_type: str
    phase_ends_at: int | None
    extensions_used: int | None = None
    kind: ClassVar[str] = "phase_timing_changed"
    record_kind: ClassVar[str] = "phase_timing_changed"


@dataclass(frozen=True)
class AbilityResultRecord:
    order: int
    day: int | None
    phase: str | None
    event_type: str
    target_player_id: str | None = None
    result_id: str | None = None
    revealed_role_id: str | None = None
    kind: ClassVar[str] = "ability_result"
    record_kind: ClassVar[str] = "ability_result"

    @property
    def result(self) -> str | None:
        return self.result_id

    @property
    def role_id(self) -> str | None:
        return self.revealed_role_id


@dataclass(frozen=True)
class GameLifecycleRecord:
    order: int
    day: int | None
    phase: str | None
    event_type: str
    game_id: str | None = None
    winner_team_id: str | None = None
    outcome_id: str | None = None
    player_results: Mapping[str, str] = field(default_factory=dict)
    players: tuple[PlayerView, ...] = ()
    kind: ClassVar[str] = "game_lifecycle"
    record_kind: ClassVar[str] = "game_lifecycle"

    def __post_init__(self) -> None:
        object.__setattr__(self, "player_results", MappingProxyType(dict(self.player_results)))
        object.__setattr__(self, "players", tuple(self.players))

    @property
    def winner_team(self) -> str | None:
        return self.winner_team_id

    @property
    def outcome(self) -> str | None:
        return self.outcome_id

    @property
    def player_views(self) -> tuple[PlayerView, ...]:
        return self.players


@dataclass(frozen=True)
class PublicNotifyRecord:
    order: int
    day: int | None
    phase: str | None
    notify_id: str
    kind: ClassVar[str] = "public_notify"
    record_kind: ClassVar[str] = "public_notify"


@dataclass(frozen=True)
class TieResolvedRandomRecord:
    order: int
    day: int | None
    phase: str | None
    candidate_player_ids: tuple[str, ...]
    selected_player_id: str
    kind: ClassVar[str] = "tie_resolved_random"
    record_kind: ClassVar[str] = "tie_resolved_random"

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_player_ids", tuple(self.candidate_player_ids))


@dataclass(frozen=True)
class KnownUnmodeledEventRecord:
    order: int
    event_type: str
    kind: ClassVar[str] = "known_unmodeled"
    record_kind: ClassVar[str] = "known_unmodeled"


@dataclass(frozen=True)
class UnknownEventRecord:
    order: int
    event_type: str
    kind: ClassVar[str] = "unknown"
    record_kind: ClassVar[str] = "unknown"


@dataclass(frozen=True)
class MalformedEventRecord:
    order: int
    event_type: str
    kind: ClassVar[str] = "malformed"
    record_kind: ClassVar[str] = "malformed"


HistoryRecord: TypeAlias = (
    ChatRecord
    | CoDeclarationRecord
    | CoReportRecord
    | VoteResultRecord
    | VoteRevealRecord
    | DeathRecord
    | PhaseTransitionRecord
    | PhaseTimingChangedRecord
    | AbilityResultRecord
    | GameLifecycleRecord
    | PublicNotifyRecord
    | TieResolvedRandomRecord
    | KnownUnmodeledEventRecord
    | UnknownEventRecord
    | MalformedEventRecord
)


@dataclass(frozen=True)
class HistoryRetention:
    total_seen: int
    retained_count: int
    retained_bytes: int
    dropped_count: int
    dropped_through_order: int | None
    first_retained_order: int | None
    last_order: int | None
    max_history_records: int
    max_history_bytes: int
    complete: bool


@dataclass(frozen=True)
class HistoryQuery:
    kinds: frozenset[str] = frozenset()
    day: int | None = None
    player_id: str | None = None
    after_order: int | None = None
    limit: int | None = None
    kind: str | frozenset[str] | None = None

    def __post_init__(self) -> None:
        kinds = set(self.kinds)
        if self.kind is not None:
            kinds.update({self.kind} if isinstance(self.kind, str) else self.kind)
        object.__setattr__(self, "kinds", frozenset(kinds))
        if self.limit is not None and (isinstance(self.limit, bool) or self.limit < 1):
            raise ValueError("history limit must be positive")


@dataclass(frozen=True)
class HistoryView:
    records: tuple[HistoryRecord, ...]
    complete: bool
    retention: HistoryRetention

    @property
    def items(self) -> tuple[HistoryRecord, ...]:
        return self.records


@dataclass(frozen=True)
class CoView:
    declarations: tuple[CoDeclarationRecord, ...]
    reports: tuple[CoReportRecord, ...]
    complete: bool
    retention: HistoryRetention

    @property
    def records(self) -> tuple[HistoryRecord, ...]:
        return tuple(sorted(self.declarations + self.reports, key=lambda record: record.order))


@dataclass(frozen=True)
class AbilityResultView:
    records: tuple[AbilityResultRecord, ...]
    complete: bool
    retention: HistoryRetention

    @property
    def results(self) -> tuple[AbilityResultRecord, ...]:
        return self.records


@dataclass(frozen=True)
class CurrentActionsView:
    world_version: int
    world_last_applied_seq: int
    network_last_seq: int
    is_caught_up: bool
    actions: tuple[Any, ...] = ()

    @property
    def current_actions(self) -> tuple[Any, ...]:
        return self.actions


@dataclass(frozen=True)
class WorldSnapshot:
    version: int = 0
    freshness: Freshness = Freshness.EMPTY
    is_caught_up: bool = False
    last_applied_seq: int = 0
    players: tuple[PlayerView, ...] = ()
    alive_player_ids: tuple[str, ...] = ()
    deaths: tuple[DeathView, ...] = ()
    phase: PhaseView | None = None
    self_view: SelfView | None = None
    history_retention: HistoryRetention = field(
        default_factory=lambda: HistoryRetention(
            total_seen=0,
            retained_count=0,
            retained_bytes=0,
            dropped_count=0,
            dropped_through_order=None,
            first_retained_order=None,
            last_order=None,
            max_history_records=1024,
            max_history_bytes=2 * 1024 * 1024,
            complete=True,
        )
    )
    unknown_event_count: int = 0
    known_unmodeled_event_count: int = 0
    malformed_event_count: int = 0

    @property
    def self_info(self) -> SelfView | None:
        return self.self_view

    @property
    def alive_ids(self) -> tuple[str, ...]:
        return self.alive_player_ids

    @property
    def complete(self) -> bool:
        return not (self.unknown_event_count or self.malformed_event_count)


@dataclass(frozen=True)
class WorldStateExit:
    reason: WorldStateExitReason
    freshness: Freshness
    last_applied_seq: int
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.reason in {
            WorldStateExitReason.STOPPED,
            WorldStateExitReason.CLIENT_ENDED,
        }
