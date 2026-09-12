"""Immutable public values for deterministic vote/ability reservations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Literal


ReservationFamily = Literal["vote", "ability"]
ReservationAction = Literal["vote.cast", "ability.use"]


class VoteAbilityLifecycle(str, Enum):
    NEW = "NEW"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class VoteAbilityOutcomeStatus(str, Enum):
    NO_ELIGIBLE_SELECTION = "NO_ELIGIBLE_SELECTION"
    NO_DECISION = "NO_DECISION"
    INVALID_DECISION = "INVALID_DECISION"
    BRAIN_FAILED = "BRAIN_FAILED"
    TIMED_OUT = "TIMED_OUT"
    DEADLINE_SUPPRESSED = "DEADLINE_SUPPRESSED"
    STALE = "STALE"
    NOT_DELIVERED = "NOT_DELIVERED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class VoteAbilityConfig:
    deadline_guard_seconds: float = 0.25
    brain_timeout_seconds: float = 0.25
    minimum_start_budget_seconds: float = 0.05
    max_pre_send_rearms_per_reservation: int = 2
    max_not_delivered_retries_per_reservation: int = 1
    outcome_retention: int = 256

    def __post_init__(self) -> None:
        for name in (
            "deadline_guard_seconds",
            "brain_timeout_seconds",
            "minimum_start_budget_seconds",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be a finite non-negative number")
        if self.brain_timeout_seconds <= 0:
            raise ValueError("brain_timeout_seconds must be positive")
        for name in (
            "max_pre_send_rearms_per_reservation",
            "max_not_delivered_retries_per_reservation",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if (
            isinstance(self.outcome_retention, bool)
            or not isinstance(self.outcome_retention, int)
            or self.outcome_retention < 1
        ):
            raise ValueError("outcome_retention must be a positive integer")


@dataclass(frozen=True)
class ReservationKey:
    day: int
    phase: str
    family: ReservationFamily

    def __post_init__(self) -> None:
        if isinstance(self.day, bool) or not isinstance(self.day, int) or self.day < 0:
            raise ValueError("day must be a non-negative integer")
        if not isinstance(self.phase, str) or not self.phase:
            raise ValueError("phase must be a non-empty string")
        if self.family not in {"vote", "ability"}:
            raise ValueError("family must be 'vote' or 'ability'")


@dataclass(frozen=True)
class OpportunityKey:
    reservation: ReservationKey
    connection_generation: int
    action_generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.reservation, ReservationKey):
            raise TypeError("reservation must be ReservationKey")
        for name in ("connection_generation", "action_generation"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True)
class VoteAbilityOutcome:
    opportunity_key: OpportunityKey
    mapping_order: int | None
    action: ReservationAction
    ability_id: str | None
    vote_target_player_id: str | None
    ability_target_player_ids: tuple[str, ...]
    request_event_id: str | None
    send_connection_generation: int | None
    observation_connection_generation: int | None
    status: VoteAbilityOutcomeStatus
    rejection_reason: str | None
    attempts: int

    def __post_init__(self) -> None:
        if not isinstance(self.opportunity_key, OpportunityKey):
            raise TypeError("opportunity_key must be OpportunityKey")
        if self.mapping_order is not None and (
            isinstance(self.mapping_order, bool)
            or not isinstance(self.mapping_order, int)
            or self.mapping_order < 0
        ):
            raise ValueError("mapping_order must be a non-negative integer when supplied")
        if self.action not in {"vote.cast", "ability.use"}:
            raise ValueError("action must be vote.cast or ability.use")
        if self.action == "vote.cast" and self.ability_id is not None:
            raise ValueError("vote outcome cannot carry ability_id")
        if self.ability_id is not None and (
            not isinstance(self.ability_id, str) or not self.ability_id
        ):
            raise ValueError("ability_id must be a non-empty string when supplied")
        object.__setattr__(
            self, "ability_target_player_ids", tuple(self.ability_target_player_ids)
        )
        if len(set(self.ability_target_player_ids)) != len(
            self.ability_target_player_ids
        ):
            raise ValueError("ability targets must be unique")
        if any(
            not isinstance(player_id, str) or not player_id
            for player_id in self.ability_target_player_ids
        ):
            raise ValueError("ability targets must be non-empty strings")
        if not isinstance(self.status, VoteAbilityOutcomeStatus):
            raise TypeError("status must be VoteAbilityOutcomeStatus")
        if self.request_event_id is not None and (
            not isinstance(self.request_event_id, str) or not self.request_event_id
        ):
            raise ValueError("request_event_id must be a non-empty string when supplied")
        for name in (
            "send_connection_generation",
            "observation_connection_generation",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer when supplied")
        if self.rejection_reason is not None and (
            not isinstance(self.rejection_reason, str) or not self.rejection_reason
        ):
            raise ValueError("rejection_reason must be a non-empty string when supplied")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int) or self.attempts < 0:
            raise ValueError("attempts must be a non-negative integer")


@dataclass(frozen=True)
class UnresolvedReservation:
    opportunity_key: OpportunityKey
    mapping_order: int
    action: ReservationAction
    ability_id: str | None
    vote_target_player_id: str | None
    ability_target_player_ids: tuple[str, ...]
    request_event_id: str
    send_connection_generation: int
    attempt_ordinal: int
    transport_after_order: int

    def __post_init__(self) -> None:
        if not isinstance(self.opportunity_key, OpportunityKey):
            raise TypeError("opportunity_key must be OpportunityKey")
        if (
            isinstance(self.mapping_order, bool)
            or not isinstance(self.mapping_order, int)
            or self.mapping_order < 0
        ):
            raise ValueError("mapping_order must be a non-negative integer")
        if self.action not in {"vote.cast", "ability.use"}:
            raise ValueError("action must be vote.cast or ability.use")
        if self.ability_id is not None and (
            not isinstance(self.ability_id, str) or not self.ability_id
        ):
            raise ValueError("ability_id must be a non-empty string when supplied")
        object.__setattr__(
            self, "ability_target_player_ids", tuple(self.ability_target_player_ids)
        )
        if not isinstance(self.request_event_id, str) or not self.request_event_id:
            raise ValueError("request_event_id must be a non-empty string")
        for name in (
            "send_connection_generation",
            "attempt_ordinal",
            "transport_after_order",
        ):
            value = getattr(self, name)
            minimum = 1 if name == "attempt_ordinal" else 0
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class VoteAbilitySnapshot:
    lifecycle: VoteAbilityLifecycle
    current_phase: tuple[int, str] | None
    current_opportunity: OpportunityKey | None
    transport_cursor: int
    pending_count: int
    unresolved_reservation: UnresolvedReservation | None
    accepted_count: int
    rejected_count: int
    unknown_count: int
    deadline_suppressed_count: int
    outcomes: tuple[VoteAbilityOutcome, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.lifecycle, VoteAbilityLifecycle):
            raise TypeError("lifecycle must be VoteAbilityLifecycle")
        if self.current_phase is not None:
            object.__setattr__(self, "current_phase", tuple(self.current_phase))
        object.__setattr__(self, "outcomes", tuple(self.outcomes))
        for name in (
            "transport_cursor",
            "pending_count",
            "accepted_count",
            "rejected_count",
            "unknown_count",
            "deadline_suppressed_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
