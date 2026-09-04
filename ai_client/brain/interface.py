"""The replaceable asynchronous Brain boundary."""

from __future__ import annotations

from typing import Protocol

from .model import BrainDecision, BrainInput


class Brain(Protocol):
    async def decide(self, request: BrainInput) -> BrainDecision:
        """Return one structured decision without performing side effects."""
