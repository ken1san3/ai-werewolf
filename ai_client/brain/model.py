"""Immutable values shared by Brain implementations and their controller."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Literal, TypeAlias

from ai_client.network import ActionHandle, SendReceipt
from ai_client.world import (
    AbilityResultView,
    CoView,
    HistoryView,
    WorldSnapshot,
)


@dataclass(frozen=True)
class BrainActionOption:
    """A request-local identifier paired with the original typed action handle."""

    option_id: str
    handle: ActionHandle

    def __post_init__(self) -> None:
        if not isinstance(self.option_id, str) or not self.option_id:
            raise ValueError("option_id must be a non-empty string")
        if not isinstance(self.handle, ActionHandle):
            raise TypeError("handle must be an ActionHandle")


@dataclass(frozen=True)
class BrainActionContext:
    world_version: int
    world_last_applied_seq: int
    network_last_seq: int
    is_caught_up: bool
    options: tuple[BrainActionOption, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", tuple(self.options))


@dataclass(frozen=True)
class BrainInput:
    """One synchronous, immutable view of the client's world."""

    snapshot: WorldSnapshot
    action_context: BrainActionContext
    history: HistoryView
    co: CoView
    ability_results: AbilityResultView


@dataclass(frozen=True)
class NoDecision:
    """The Brain intentionally selected no Network action."""


@dataclass(frozen=True)
class ChatDecision:
    option_id: str
    message: str


@dataclass(frozen=True)
class VoteDecision:
    option_id: str
    target_player_id: str | None


@dataclass(frozen=True)
class AbilityDecision:
    option_id: str
    target_player_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_player_ids", tuple(self.target_player_ids))


@dataclass(frozen=True)
class CoDeclareDecision:
    option_id: str
    claimed_role_id: str
    comment: str


@dataclass(frozen=True)
class CoReportDecision:
    option_id: str
    kind: str
    target_player_id: str
    claimed_result: str


BrainDecision: TypeAlias = (
    NoDecision
    | ChatDecision
    | VoteDecision
    | AbilityDecision
    | CoDeclareDecision
    | CoReportDecision
)


class DecisionStatus(str, Enum):
    NO_DECISION = "NO_DECISION"
    SENT = "SENT"
    INVALID_DECISION = "INVALID_DECISION"
    TIMED_OUT = "TIMED_OUT"
    BRAIN_FAILED = "BRAIN_FAILED"
    STALE = "STALE"
    CANCELLED = "CANCELLED"
    SEND_NOT_DELIVERED = "SEND_NOT_DELIVERED"
    SEND_DELIVERY_UNKNOWN = "SEND_DELIVERY_UNKNOWN"
    DEADLINE_SUPPRESSED = "DEADLINE_SUPPRESSED"


@dataclass(frozen=True)
class DecisionOutcome:
    """A redacted, observable result of one Brain invocation."""

    status: DecisionStatus
    request_version: int | None = None
    phase: str | None = None
    day: int | None = None
    option_id: str | None = None
    receipt: SendReceipt | None = None
    error_type: str | None = None
    invocation_started: bool = False
    request_event_id: str | None = None
    attempt_action: str | None = None
    send_connection_generation: int | None = None
    vote_target_player_id: str | None = None
    ability_id: str | None = None
    ability_target_player_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "ability_target_player_ids",
            tuple(self.ability_target_player_ids),
        )
        for name in (
            "request_event_id",
            "attempt_action",
            "vote_target_player_id",
            "ability_id",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a non-empty string when supplied")
        if self.send_connection_generation is not None and (
            isinstance(self.send_connection_generation, bool)
            or not isinstance(self.send_connection_generation, int)
            or self.send_connection_generation < 0
        ):
            raise ValueError(
                "send_connection_generation must be a non-negative integer when supplied"
            )
        if any(
            not isinstance(player_id, str) or not player_id
            for player_id in self.ability_target_player_ids
        ):
            raise ValueError("ability targets must be non-empty strings")


@dataclass(frozen=True)
class BrainRunConfig:
    max_decision_seconds: float = 5.0
    cancellation_grace_seconds: float = 0.25

    def __post_init__(self) -> None:
        for name, value in (
            ("max_decision_seconds", self.max_decision_seconds),
            ("cancellation_grace_seconds", self.cancellation_grace_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be a finite non-negative number")
        if self.max_decision_seconds <= 0:
            raise ValueError("max_decision_seconds must be positive")


@dataclass(frozen=True)
class PhaseKey:
    day: int
    phase: str


@dataclass(frozen=True)
class DispatchDeadline:
    mapping_order: int
    phase: str
    day: int
    connection_generation: int
    action_generation: int
    not_after_monotonic: float

    def __post_init__(self) -> None:
        for name in (
            "mapping_order",
            "day",
            "connection_generation",
            "action_generation",
        ):
            value = getattr(self, name)
            minimum = 0
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if not isinstance(self.phase, str) or not self.phase:
            raise ValueError("phase must be a non-empty string")
        if (
            isinstance(self.not_after_monotonic, bool)
            or not isinstance(self.not_after_monotonic, (int, float))
            or not math.isfinite(self.not_after_monotonic)
            or self.not_after_monotonic < 0
        ):
            raise ValueError("not_after_monotonic must be a finite non-negative number")


class FeatureControllerExitReason(str, Enum):
    WORLD_ENDED = "WORLD_ENDED"
    WORLD_FAILED = "WORLD_FAILED"
    STOP_REQUESTED = "STOP_REQUESTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class FeatureControllerExit:
    owner: Literal["vote_ability", "reaction_chat"]
    reason: FeatureControllerExitReason
    error_type: str | None = None

    def __post_init__(self) -> None:
        if self.owner not in {"vote_ability", "reaction_chat"}:
            raise ValueError("owner must be 'vote_ability' or 'reaction_chat'")
        if not isinstance(self.reason, FeatureControllerExitReason):
            raise TypeError("reason must be FeatureControllerExitReason")
        if self.error_type is not None and (
            not isinstance(self.error_type, str) or not self.error_type
        ):
            raise ValueError("error_type must be a non-empty string when supplied")
        if (
            self.reason is not FeatureControllerExitReason.FAILED
            and self.error_type is not None
        ):
            raise ValueError("error_type is only valid for FAILED")


class CoordinatorState(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class CoordinatorExitReason(str, Enum):
    WORLD_ENDED = "WORLD_ENDED"
    WORLD_FAILED = "WORLD_FAILED"
    STOPPED = "STOPPED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class CoordinatorExit:
    reason: CoordinatorExitReason
    attempted_phases: tuple[PhaseKey, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempted_phases", tuple(self.attempted_phases))

    @property
    def success(self) -> bool:
        return self.reason in {
            CoordinatorExitReason.WORLD_ENDED,
            CoordinatorExitReason.STOPPED,
        }
