"""Immutable public values for Phase 3.4 reaction chat control."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import TYPE_CHECKING

from ai_client.discussion import DiscussionDispatchCorrelation

if TYPE_CHECKING:
    from .frequency import FrequencySuppression, SpeakingFrequencyState


def _require_int(name: str, value: object, *, minimum: int = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _require_number(name: str, value: object, *, minimum: float = 0.0) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
    ):
        raise ValueError(f"{name} must be a finite number >= {minimum}")


def _require_optional_string(name: str, value: object) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{name} must be a non-empty string when supplied")


class ReactionChatLifecycle(str, Enum):
    NEW = "new"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


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
    FREQUENCY_SUPPRESSED = "frequency_suppressed"


@dataclass(frozen=True)
class ReactionPhaseKey:
    connection_generation: int
    day: int
    phase: str
    action_generation: int

    def __post_init__(self) -> None:
        _require_int("connection_generation", self.connection_generation)
        _require_int("day", self.day, minimum=1)
        if not isinstance(self.phase, str) or not self.phase:
            raise ValueError("phase must be a non-empty string")
        _require_int("action_generation", self.action_generation)


@dataclass(frozen=True)
class ReactionTrigger:
    kind: ReactionTriggerKind
    phase_key: ReactionPhaseKey
    source_order: int | None
    attempt_ordinal: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ReactionTriggerKind):
            raise TypeError("kind must be ReactionTriggerKind")
        if not isinstance(self.phase_key, ReactionPhaseKey):
            raise TypeError("phase_key must be ReactionPhaseKey")
        if self.source_order is not None:
            _require_int("source_order", self.source_order)
        _require_int("attempt_ordinal", self.attempt_ordinal)


@dataclass(frozen=True)
class ReactionOutcome:
    trigger: ReactionTrigger
    scheduled_due_monotonic: float
    brain_outcome: str | None
    action_kind: str | None
    status: ReactionOutcomeStatus
    frequency_evaluation_ordinal: int | None = None
    frequency_event_importance: float | None = None
    frequency_threshold: float | None = None
    frequency_draw: float | None = None
    frequency_source_fingerprint: str | None = None
    frequency_suppression: FrequencySuppression | None = None
    discussion: DiscussionDispatchCorrelation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.trigger, ReactionTrigger):
            raise TypeError("trigger must be ReactionTrigger")
        _require_number("scheduled_due_monotonic", self.scheduled_due_monotonic)
        _require_optional_string("brain_outcome", self.brain_outcome)
        _require_optional_string("action_kind", self.action_kind)
        if not isinstance(self.status, ReactionOutcomeStatus):
            raise TypeError("status must be ReactionOutcomeStatus")
        if self.frequency_evaluation_ordinal is not None:
            _require_int(
                "frequency_evaluation_ordinal",
                self.frequency_evaluation_ordinal,
                minimum=1,
            )
        for name in (
            "frequency_event_importance",
            "frequency_threshold",
            "frequency_draw",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_number(name, value)
                if value > 1:
                    raise ValueError(f"{name} must not exceed 1")
        if self.frequency_source_fingerprint is not None and (
            len(self.frequency_source_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.frequency_source_fingerprint
            )
        ):
            raise ValueError("frequency_source_fingerprint must be lowercase SHA-256 hex")
        if self.frequency_suppression is not None:
            from .frequency import FrequencySuppression

            if not isinstance(self.frequency_suppression, FrequencySuppression):
                raise TypeError("frequency_suppression must be FrequencySuppression")
        if self.discussion is not None:
            if not isinstance(self.discussion, DiscussionDispatchCorrelation):
                raise TypeError(
                    "discussion must be DiscussionDispatchCorrelation or None"
                )
            if self.status not in {
                ReactionOutcomeStatus.ACCEPTED,
                ReactionOutcomeStatus.REJECTED,
                ReactionOutcomeStatus.TRANSPORT_GAP,
            }:
                raise ValueError(
                    "discussion correlation requires a finalized local-send status"
                )
            expected_action_kind = {
                "chat": "chat.send",
                "co_declare": "co.declare",
            }.get(self.discussion.action)
            if self.action_kind != expected_action_kind:
                raise ValueError(
                    "discussion correlation does not match the Reaction action kind"
                )
        if (
            self.status is ReactionOutcomeStatus.FREQUENCY_SUPPRESSED
            and self.frequency_suppression is None
        ):
            raise ValueError("frequency-suppressed outcome requires its exact reason")
        if (
            self.status is not ReactionOutcomeStatus.FREQUENCY_SUPPRESSED
            and self.frequency_suppression is not None
        ):
            raise ValueError("frequency suppression is only valid on suppressed outcomes")
        evaluated = (
            self.frequency_evaluation_ordinal,
            self.frequency_event_importance,
            self.frequency_threshold,
            self.frequency_draw,
        )
        if self.frequency_evaluation_ordinal is not None and any(
            value is None for value in evaluated
        ):
            raise ValueError("evaluated frequency evidence must be complete")
        if self.frequency_evaluation_ordinal is None and any(
            value is not None
            for value in (self.frequency_threshold, self.frequency_draw)
        ):
            raise ValueError("threshold and draw require an evaluation ordinal")

    @property
    def evaluation_ordinal(self) -> int | None:
        return self.frequency_evaluation_ordinal

    @property
    def event_importance(self) -> float | None:
        return self.frequency_event_importance

    @property
    def threshold(self) -> float | None:
        return self.frequency_threshold

    @property
    def draw(self) -> float | None:
        return self.frequency_draw

    @property
    def source_fingerprint(self) -> str | None:
        return self.frequency_source_fingerprint

    @property
    def suppression(self) -> FrequencySuppression | None:
        return self.frequency_suppression


@dataclass(frozen=True)
class CoGenerationState:
    action_generation: int
    invoked: bool
    closed: bool

    def __post_init__(self) -> None:
        _require_int("action_generation", self.action_generation)
        if not isinstance(self.invoked, bool) or not isinstance(self.closed, bool):
            raise TypeError("invoked and closed must be bool")


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
    frequency_state: SpeakingFrequencyState | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.lifecycle, ReactionChatLifecycle):
            raise TypeError("lifecycle must be ReactionChatLifecycle")
        if self.current_phase_key is not None and not isinstance(
            self.current_phase_key, ReactionPhaseKey
        ):
            raise TypeError("current_phase_key must be ReactionPhaseKey when supplied")
        for name in (
            "history_cursor",
            "transport_cursor",
            "chat_brain_invocations",
            "send_count",
            "accepted_count",
            "rejected_count",
            "deadline_suppressed_count",
            "intentional_silence_count",
        ):
            _require_int(name, getattr(self, name))
        if self.co_generation_state is not None and not isinstance(
            self.co_generation_state, CoGenerationState
        ):
            raise TypeError("co_generation_state must be CoGenerationState when supplied")
        object.__setattr__(self, "outcomes", tuple(self.outcomes))
        if any(not isinstance(outcome, ReactionOutcome) for outcome in self.outcomes):
            raise TypeError("outcomes must contain ReactionOutcome values")
        if self.frequency_state is not None:
            from .frequency import SpeakingFrequencyState

            if not isinstance(self.frequency_state, SpeakingFrequencyState):
                raise TypeError("frequency_state must be SpeakingFrequencyState")

    @property
    def speaking_frequency_state(self) -> SpeakingFrequencyState | None:
        return self.frequency_state


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

    def __post_init__(self) -> None:
        _require_int(
            "max_chat_attempts_per_phase", self.max_chat_attempts_per_phase, minimum=1
        )
        if self.max_chat_attempts_per_phase > 2:
            raise ValueError("max_chat_attempts_per_phase must not exceed 2")
        _require_int("outcome_retention", self.outcome_retention, minimum=1)
        for name in (
            "minimum_accepted_chat_interval_seconds",
            "deadline_guard_seconds",
            "brain_timeout_seconds",
            "minimum_start_budget_seconds",
        ):
            _require_number(name, getattr(self, name))
        for name in ("initial_jitter_seconds", "reaction_jitter_seconds"):
            value = getattr(self, name)
            if not isinstance(value, tuple) or len(value) != 2:
                raise ValueError(f"{name} must be a two-item tuple")
            lower, upper = value
            _require_number(f"{name}[0]", lower)
            _require_number(f"{name}[1]", upper)
            if lower > upper:
                raise ValueError(f"{name} lower bound must not exceed upper bound")
