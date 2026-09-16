"""Immutable values shared by Brain implementations and their controller."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Literal, TypeAlias

from ai_client.discussion.model import (
    AiDiscussionGenerationStatus,
    DiscussionCapture,
    DiscussionDispatchCorrelation,
    DiscussionGenerationAck,
    DiscussionProposal,
    DiscussionTrigger,
)
from ai_client.discussion.context import canonical_sha256
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
    discussion: DiscussionCapture | None = None

    def __post_init__(self) -> None:
        if self.discussion is not None and not isinstance(
            self.discussion, DiscussionCapture
        ):
            raise TypeError("discussion must be DiscussionCapture or None")


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


def brain_decision_identity(decision: BrainDecision) -> tuple[str, str | None]:
    """Return the closed Phase 6 proposal identity for a network decision."""

    if isinstance(decision, NoDecision):
        return "none", None
    if isinstance(decision, ChatDecision):
        return "chat", decision.option_id
    if isinstance(decision, VoteDecision):
        return "vote", decision.option_id
    if isinstance(decision, AbilityDecision):
        return "ability", decision.option_id
    if isinstance(decision, CoDeclareDecision):
        return "co_declare", decision.option_id
    if isinstance(decision, CoReportDecision):
        return "co_report", decision.option_id
    raise TypeError("decision must be a BrainDecision")


@dataclass(frozen=True)
class BrainResult:
    """One contextful network decision and its private semantic transaction."""

    decision: BrainDecision
    discussion: DiscussionProposal | None
    audit_ack: DiscussionGenerationAck | None

    def __post_init__(self) -> None:
        if not isinstance(
            self.decision,
            (
                NoDecision,
                ChatDecision,
                VoteDecision,
                AbilityDecision,
                CoDeclareDecision,
                CoReportDecision,
            ),
        ):
            raise TypeError("decision must be a BrainDecision")
        if self.discussion is not None and not isinstance(
            self.discussion, DiscussionProposal
        ):
            raise TypeError("discussion must be DiscussionProposal or None")
        if self.audit_ack is not None and not isinstance(
            self.audit_ack, DiscussionGenerationAck
        ):
            raise TypeError("audit_ack must be DiscussionGenerationAck or None")
        if (self.discussion is None) is not (self.audit_ack is None):
            raise ValueError("discussion proposal and audit acknowledgement must be paired")
        if self.discussion is None:
            return
        if isinstance(self.decision, CoReportDecision):
            raise ValueError("contextful co_report is not supported")
        kind, option_id = brain_decision_identity(self.decision)
        if (
            self.discussion.decision_kind != kind
            or self.discussion.option_id != option_id
        ):
            raise ValueError("decision identity does not match discussion proposal")
        assert self.audit_ack is not None
        if self.audit_ack.generation_status not in {
            AiDiscussionGenerationStatus.DECISION,
            AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION,
            AiDiscussionGenerationStatus.REPAIR_SUCCEEDED,
        }:
            raise ValueError("BrainResult requires a successful generation acknowledgement")
        if self.audit_ack.proposal_sha256 != canonical_sha256(self.discussion):
            raise ValueError("generation acknowledgement does not bind the proposal")
        if (
            self.audit_ack.generation_status
            is AiDiscussionGenerationStatus.EXPLICIT_NO_DECISION
        ) != isinstance(self.decision, NoDecision):
            if self.audit_ack.generation_status is not AiDiscussionGenerationStatus.REPAIR_SUCCEEDED:
                raise ValueError("generation status does not match decision identity")


BrainOutput: TypeAlias = BrainDecision | BrainResult


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
    discussion: DiscussionDispatchCorrelation | None = None

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
        if self.discussion is not None and not isinstance(
            self.discussion, DiscussionDispatchCorrelation
        ):
            raise TypeError(
                "discussion must be DiscussionDispatchCorrelation or None"
            )
        if self.discussion is not None and self.status is not DecisionStatus.SENT:
            raise ValueError("discussion correlation is only valid for SENT")


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
    discussion_trigger: DiscussionTrigger | None = None

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
        if self.discussion_trigger is not None:
            if not isinstance(self.discussion_trigger, DiscussionTrigger):
                raise TypeError(
                    "discussion_trigger must be DiscussionTrigger or None"
                )
            trigger = self.discussion_trigger
            if (
                trigger.mapping_order != self.mapping_order
                or trigger.phase != self.phase
                or trigger.day != self.day
                or trigger.connection_generation != self.connection_generation
                or trigger.action_generation != self.action_generation
            ):
                raise ValueError(
                    "discussion_trigger must match the dispatch deadline identity"
                )


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
