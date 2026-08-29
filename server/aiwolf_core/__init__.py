"""Content-driven domain models for the game core."""

from .content import ContentPack, ContentValidationError, Preset, load_content, load_preset
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
    "EffectReference",
    "Knowledge",
    "PlayerRoleState",
    "Preset",
    "expire_modifiers_at_dawn",
    "load_content",
    "load_preset",
    "resolve_effective_attributes",
    "resolve_effective_win_conditions",
]
