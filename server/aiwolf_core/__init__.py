"""Content-driven domain models for the game core."""

from .content import ContentPack, ContentValidationError, Preset, load_content, load_preset
from .events import EventBus, EventSink, EventVisibility, GameEvent, InMemoryEventSink, JsonlEventLog
from .death import public_death_cause
from .game import GameState
from .interactions import ActionRejected, ChatSubmission
from .state import (
    ActionSpec,
    GameResult,
    Player,
    PlayerConfig,
    RandomSource,
    VoteResult,
    VoteResultKind,
)
from .models import (
    AppliedModifier,
    GamePhase,
    EffectiveAttributes,
    EffectReference,
    Knowledge,
    PlayerRoleState,
    expire_modifiers_at_dawn,
    resolve_effective_attributes,
    resolve_effective_win_conditions,
)

__all__ = [
    "AppliedModifier",
    "ActionSpec",
    "ActionRejected",
    "ContentPack",
    "ContentValidationError",
    "ChatSubmission",
    "EffectiveAttributes",
    "EventBus",
    "EventSink",
    "EventVisibility",
    "EffectReference",
    "GameEvent",
    "GamePhase",
    "GameResult",
    "GameState",
    "JsonlEventLog",
    "InMemoryEventSink",
    "Knowledge",
    "PlayerRoleState",
    "Player",
    "PlayerConfig",
    "Preset",
    "expire_modifiers_at_dawn",
    "load_content",
    "load_preset",
    "resolve_effective_attributes",
    "resolve_effective_win_conditions",
    "RandomSource",
    "VoteResult",
    "VoteResultKind",
    "public_death_cause",
]
