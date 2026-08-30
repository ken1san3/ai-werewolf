"""Content-driven domain models for the game core."""

from .content import ContentPack, ContentValidationError, Preset, load_content, load_preset
from .events import EventBus, EventSink, EventVisibility, GameEvent, InMemoryEventSink, JsonlEventLog
from .game import (
    GamePhase,
    GameState,
    Player,
    PlayerConfig,
    RandomSource,
    VoteResult,
    VoteResultKind,
)
from .models import (
    AppliedModifier,
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
    "ContentPack",
    "ContentValidationError",
    "EffectiveAttributes",
    "EventBus",
    "EventSink",
    "EventVisibility",
    "EffectReference",
    "GameEvent",
    "GamePhase",
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
]
