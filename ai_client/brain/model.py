"""Immutable values shared by Brain implementations and their controller."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import TypeAlias

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


# Keep the vocabulary in one enum while offering descriptive import names.
DecisionOutcomeStatus = DecisionStatus
OutcomeStatus = DecisionStatus


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

    @property
    def sent_receipt(self) -> SendReceipt | None:
        return self.receipt

    @property
    def failure_type(self) -> str | None:
        return self.error_type


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
