"""Explicit game-duration conversion; monotonic clocks and safety waits stay real."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from typing import Mapping


@dataclass(frozen=True)
class GameTime:
    time_scale: float = 1.0

    def __post_init__(self) -> None:
        if (isinstance(self.time_scale, bool)
                or not isinstance(self.time_scale, (int, float))
                or not math.isfinite(self.time_scale)
                or self.time_scale <= 0
                or not math.isfinite(1.0 / self.time_scale)):
            raise ValueError("AIWOLF_TIME_SCALE must be finite and positive")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> GameTime:
        values = os.environ if environ is None else environ
        try:
            return cls(float(values.get("AIWOLF_TIME_SCALE", "1.0")))
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("AIWOLF_TIME_SCALE must be finite and positive") from error

    def real_budget(self, logical_seconds: float) -> float:
        result = logical_seconds / self.time_scale
        if not math.isfinite(result) or result < 0:
            raise ValueError("game duration must be finite and non-negative")
        return result

    def logical_elapsed(self, real_seconds: float) -> float:
        result = real_seconds * self.time_scale
        if not math.isfinite(result) or result < 0:
            raise ValueError("elapsed duration must be finite and non-negative")
        return result

    def evidence(self, real_seconds: float | None) -> dict[str, float | None]:
        return {
            "time_scale": self.time_scale,
            "real_duration_sec": real_seconds,
            "logical_duration_sec": None if real_seconds is None else self.logical_elapsed(real_seconds),
        }
