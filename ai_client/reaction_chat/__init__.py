"""Public Phase 3.4 Reaction Chat API."""

from .controller import ReactionChatController
from ai_client.brain import FeatureControllerExit, FeatureControllerExitReason
from .frequency import (
    DeterministicSpeakingFrequencyPolicy,
    FrequencyDecision,
    FrequencySuppression,
    PreparedSpeakingOpportunity,
    SpeakingFrequencyPolicy,
    SpeakingFrequencyState,
    SpeakingOpportunity,
    SpeakingProfile,
)
from .randomness import deterministic_jitter_seconds
from .types import (
    CoGenerationState,
    ReactionChatConfig,
    ReactionChatLifecycle,
    ReactionChatSnapshot,
    ReactionOutcome,
    ReactionOutcomeStatus,
    ReactionPhaseKey,
    ReactionTrigger,
    ReactionTriggerKind,
)

__all__ = [
    "CoGenerationState",
    "FeatureControllerExit",
    "FeatureControllerExitReason",
    "DeterministicSpeakingFrequencyPolicy",
    "FrequencyDecision",
    "FrequencySuppression",
    "PreparedSpeakingOpportunity",
    "ReactionChatConfig",
    "ReactionChatController",
    "ReactionChatLifecycle",
    "ReactionChatSnapshot",
    "ReactionOutcome",
    "ReactionOutcomeStatus",
    "ReactionPhaseKey",
    "ReactionTrigger",
    "ReactionTriggerKind",
    "SpeakingFrequencyPolicy",
    "SpeakingFrequencyState",
    "SpeakingOpportunity",
    "SpeakingProfile",
    "deterministic_jitter_seconds",
]
