"""Safe, machine-readable player-action rejection reasons."""

from __future__ import annotations


class ActionRejected(ValueError):
    """A core rejection that preserves a local detail but exposes only ``reason``."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        if not reason:
            raise ValueError("rejection reason must not be empty")
        super().__init__(detail if detail is not None else reason)
        self.reason = reason
