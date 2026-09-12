"""Stable jitter derivation for reaction opportunities."""

from __future__ import annotations

import hashlib
import math

from .types import ReactionPhaseKey, ReactionTriggerKind


def _framed(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big", signed=False) + value


def _number(name: str, value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{name} must be a finite non-negative number")
    return float(value)


def deterministic_jitter_seconds(
    *,
    master_seed: int,
    player_id: str,
    phase_key: ReactionPhaseKey,
    trigger_kind: ReactionTriggerKind,
    source_order: int | None,
    attempt_ordinal: int,
    lower_seconds: float,
    upper_seconds: float,
) -> float:
    """Map one stable opportunity identity into the requested closed interval."""

    if isinstance(master_seed, bool) or not isinstance(master_seed, int):
        raise ValueError("master_seed must be an integer")
    if not isinstance(player_id, str) or not player_id:
        raise ValueError("player_id must be a non-empty string")
    if not isinstance(phase_key, ReactionPhaseKey):
        raise TypeError("phase_key must be ReactionPhaseKey")
    if not isinstance(trigger_kind, ReactionTriggerKind):
        raise TypeError("trigger_kind must be ReactionTriggerKind")
    if source_order is not None and (
        isinstance(source_order, bool)
        or not isinstance(source_order, int)
        or source_order < 0
    ):
        raise ValueError("source_order must be a non-negative integer when supplied")
    if (
        isinstance(attempt_ordinal, bool)
        or not isinstance(attempt_ordinal, int)
        or attempt_ordinal < 0
    ):
        raise ValueError("attempt_ordinal must be a non-negative integer")
    lower = _number("lower_seconds", lower_seconds)
    upper = _number("upper_seconds", upper_seconds)
    if lower > upper:
        raise ValueError("lower_seconds must not exceed upper_seconds")
    if lower == upper:
        return lower

    values = (
        b"aiwolf/reaction-chat/jitter/v1",
        str(master_seed).encode("ascii"),
        player_id.encode("utf-8"),
        str(phase_key.connection_generation).encode("ascii"),
        str(phase_key.day).encode("ascii"),
        phase_key.phase.encode("utf-8"),
        str(phase_key.action_generation).encode("ascii"),
        trigger_kind.value.encode("utf-8"),
        b"none" if source_order is None else str(source_order).encode("ascii"),
        str(attempt_ordinal).encode("ascii"),
    )
    digest = hashlib.sha256(b"".join(_framed(value) for value in values)).digest()
    n = int.from_bytes(digest[0:7], "big") >> 3
    fraction = n / (2**53 - 1)
    return lower + (upper - lower) * fraction
