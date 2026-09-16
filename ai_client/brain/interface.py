"""The replaceable asynchronous Brain boundary."""

from __future__ import annotations

from typing import Protocol

from .model import BrainInput, BrainOutput


class Brain(Protocol):
    async def decide(self, request: BrainInput) -> BrainOutput:
        """Return one structured decision without performing side effects."""
