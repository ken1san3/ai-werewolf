"""Deterministic, model-neutral speaking-frequency policy for Phase 5."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import math
from typing import Protocol

from .types import ReactionPhaseKey, ReactionTriggerKind


_SOURCE_DOMAIN = b"aiwolf.phase5.source.v1" + bytes((0x00,))
_DRAW_DOMAIN = b"aiwolf.phase5.speaking.v1" + bytes((0x00,))
_NULL_FIELD = bytes((0x00,))
_NON_NULL_TAG = bytes((0x01,))


def _require_probability(name: str, value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        raise ValueError(f"{name} must be a finite number in [0, 1]")


def _require_non_negative_int(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _require_bounded_int(name: str, value: object, *, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > maximum
    ):
        raise ValueError(f"{name} must be an integer in [0, {maximum}]")


def _require_non_empty_string(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _frame(data: bytes | None) -> bytes:
    if data is None:
        return _NULL_FIELD
    return _NON_NULL_TAG + len(data).to_bytes(4, "big") + data


def _text(value: str | None) -> bytes | None:
    return None if value is None else value.encode("utf-8")


def _integer(value: int | None) -> bytes | None:
    return None if value is None else str(value).encode("ascii")


@dataclass(frozen=True)
class SpeakingProfile:
    talkativeness: float = 0.5
    ordinary_event_importance: float = 0.5
    direct_mention_importance: float = 1.0
    initial_event_importance: float = 1.0
    cooldown_seconds: float = 0.20
    max_trigger_evaluations_per_phase: int = 32
    repetition_window: int = 8

    def __post_init__(self) -> None:
        for name in (
            "talkativeness",
            "ordinary_event_importance",
            "direct_mention_importance",
            "initial_event_importance",
        ):
            _require_probability(name, getattr(self, name))
        if (
            isinstance(self.cooldown_seconds, bool)
            or not isinstance(self.cooldown_seconds, (int, float))
            or not math.isfinite(self.cooldown_seconds)
            or self.cooldown_seconds < 0
        ):
            raise ValueError("cooldown_seconds must be a finite number >= 0")
        _require_bounded_int(
            "max_trigger_evaluations_per_phase",
            self.max_trigger_evaluations_per_phase,
            maximum=64,
        )
        _require_bounded_int("repetition_window", self.repetition_window, maximum=32)


class FrequencySuppression(str, Enum):
    PROBABILITY = "PROBABILITY"
    COOLDOWN = "COOLDOWN"
    REPETITION = "REPETITION"
    SELF_CHAIN = "SELF_CHAIN"
    INVOCATION_CAP = "INVOCATION_CAP"
    EVALUATION_CAP = "EVALUATION_CAP"


@dataclass(frozen=True)
class SpeakingOpportunity:
    phase_key: ReactionPhaseKey
    trigger_kind: ReactionTriggerKind
    source_order: int | None
    source_player_id: str | None
    source_channel: str | None
    source_message: str | None
    self_player_id: str
    self_display_name: str
    attempt_ordinal: int

    def __post_init__(self) -> None:
        if not isinstance(self.phase_key, ReactionPhaseKey):
            raise TypeError("phase_key must be ReactionPhaseKey")
        if self.trigger_kind not in {
            ReactionTriggerKind.INITIAL_CHAT,
            ReactionTriggerKind.REACTION_CHAT,
        }:
            raise ValueError("trigger_kind must be a chat trigger")
        _require_non_empty_string("self_player_id", self.self_player_id)
        _require_non_empty_string("self_display_name", self.self_display_name)
        _require_non_negative_int("attempt_ordinal", self.attempt_ordinal)
        source_values = (
            self.source_order,
            self.source_player_id,
            self.source_channel,
            self.source_message,
        )
        if self.trigger_kind is ReactionTriggerKind.INITIAL_CHAT:
            if any(value is not None for value in source_values):
                raise ValueError("initial chat source fields must all be None")
            return
        _require_non_negative_int("source_order", self.source_order)
        _require_non_empty_string("source_player_id", self.source_player_id)
        _require_non_empty_string("source_channel", self.source_channel)
        _require_non_empty_string("source_message", self.source_message)


@dataclass(frozen=True)
class PreparedSpeakingOpportunity:
    opportunity: SpeakingOpportunity
    event_importance: float
    source_message_sha256: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.opportunity, SpeakingOpportunity):
            raise TypeError("opportunity must be SpeakingOpportunity")
        _require_probability("event_importance", self.event_importance)
        if self.opportunity.trigger_kind is ReactionTriggerKind.INITIAL_CHAT:
            if self.source_message_sha256 is not None:
                raise ValueError("initial chat must not have a source fingerprint")
        elif (
            not isinstance(self.source_message_sha256, str)
            or len(self.source_message_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.source_message_sha256
            )
        ):
            raise ValueError("reaction source fingerprint must be lowercase SHA-256 hex")


@dataclass(frozen=True)
class FrequencyDecision:
    should_invoke: bool
    suppression: FrequencySuppression | None
    event_importance: float
    threshold: float
    draw: float

    def __post_init__(self) -> None:
        if not isinstance(self.should_invoke, bool):
            raise TypeError("should_invoke must be bool")
        if self.should_invoke:
            if self.suppression is not None:
                raise ValueError("an invoking decision cannot have a suppression")
        elif self.suppression is not FrequencySuppression.PROBABILITY:
            raise ValueError(
                "a non-invoking policy decision must be PROBABILITY suppressed"
            )
        for name in ("event_importance", "threshold", "draw"):
            _require_probability(name, getattr(self, name))


@dataclass(frozen=True)
class SpeakingFrequencyState:
    phase_key: ReactionPhaseKey | None
    evaluation_count: int
    committed_brain_invocations: int
    last_accepted_chat_at: float | None
    recent_source_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.phase_key is not None and not isinstance(
            self.phase_key, ReactionPhaseKey
        ):
            raise TypeError("phase_key must be ReactionPhaseKey when supplied")
        _require_non_negative_int("evaluation_count", self.evaluation_count)
        _require_non_negative_int(
            "committed_brain_invocations", self.committed_brain_invocations
        )
        if self.last_accepted_chat_at is not None and (
            isinstance(self.last_accepted_chat_at, bool)
            or not isinstance(self.last_accepted_chat_at, (int, float))
            or not math.isfinite(self.last_accepted_chat_at)
            or self.last_accepted_chat_at < 0
        ):
            raise ValueError("last_accepted_chat_at must be finite and non-negative")
        fingerprints = tuple(self.recent_source_fingerprints)
        if any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in fingerprints
        ):
            raise ValueError("recent_source_fingerprints must contain lowercase SHA-256 hex")
        if len(fingerprints) > 32:
            raise ValueError("recent_source_fingerprints must contain at most 32 values")
        object.__setattr__(self, "recent_source_fingerprints", fingerprints)


class SpeakingFrequencyPolicy(Protocol):
    @property
    def profile(self) -> SpeakingProfile: ...

    def prepare(self, opportunity: SpeakingOpportunity) -> PreparedSpeakingOpportunity: ...

    def evaluate(self, prepared: PreparedSpeakingOpportunity) -> FrequencyDecision: ...


class DeterministicSpeakingFrequencyPolicy(SpeakingFrequencyPolicy):
    """The approved narrow importance scorer and framed SHA-256 draw."""

    def __init__(self, *, profile: SpeakingProfile, master_seed: int) -> None:
        if not isinstance(profile, SpeakingProfile):
            raise TypeError("profile must be SpeakingProfile")
        if isinstance(master_seed, bool) or not isinstance(master_seed, int):
            raise ValueError("master_seed must be an integer")
        self._profile = profile
        self.master_seed = master_seed

    @property
    def profile(self) -> SpeakingProfile:
        return self._profile

    def prepare(self, opportunity: SpeakingOpportunity) -> PreparedSpeakingOpportunity:
        if not isinstance(opportunity, SpeakingOpportunity):
            raise TypeError("opportunity must be SpeakingOpportunity")
        if opportunity.trigger_kind is ReactionTriggerKind.INITIAL_CHAT:
            return PreparedSpeakingOpportunity(
                opportunity,
                self.profile.initial_event_importance,
                None,
            )
        source_fingerprint = hashlib.sha256(
            _SOURCE_DOMAIN
            + _frame(_text(opportunity.source_player_id))
            + _frame(_text(opportunity.source_channel))
            + _frame(_text(opportunity.source_message))
        ).hexdigest()
        assert opportunity.source_message is not None
        direct_mention = (
            opportunity.self_display_name in opportunity.source_message
            or opportunity.self_player_id in opportunity.source_message
        )
        importance = (
            self.profile.direct_mention_importance
            if direct_mention
            else self.profile.ordinary_event_importance
        )
        return PreparedSpeakingOpportunity(opportunity, importance, source_fingerprint)

    def evaluate(self, prepared: PreparedSpeakingOpportunity) -> FrequencyDecision:
        if not isinstance(prepared, PreparedSpeakingOpportunity):
            raise TypeError("prepared must be PreparedSpeakingOpportunity")
        opportunity = prepared.opportunity
        key = opportunity.phase_key
        fields: tuple[bytes | None, ...] = (
            _integer(self.master_seed),
            _text(opportunity.self_player_id),
            _integer(key.connection_generation),
            _integer(key.day),
            _text(key.phase),
            _integer(key.action_generation),
            _text(opportunity.trigger_kind.value),
            _integer(opportunity.source_order),
            _text(prepared.source_message_sha256),
            _integer(opportunity.attempt_ordinal),
        )
        digest = hashlib.sha256(
            _DRAW_DOMAIN + b"".join(_frame(field) for field in fields)
        ).digest()
        draw = (int.from_bytes(digest[0:7], "big") >> 3) / 2**53
        threshold = self.profile.talkativeness * prepared.event_importance
        should_invoke = draw < threshold
        return FrequencyDecision(
            should_invoke=should_invoke,
            suppression=None if should_invoke else FrequencySuppression.PROBABILITY,
            event_importance=prepared.event_importance,
            threshold=threshold,
            draw=draw,
        )
