"""Deterministic, LLM-free Brain used by Phase 3.3."""

from __future__ import annotations

from .model import BrainInput, BrainDecision, NoDecision


class DummyBrain:
    """A deliberately conservative Brain for completion and wiring tests.

    Phase 3.3 does not define action-selection policy.  Returning ``NoDecision``
    lets the server's existing no-selection rules advance the game while this
    implementation still exercises the complete Brain boundary.
    """

    def __init__(self, seed: int) -> None:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError("seed must be an integer")
        self.seed = seed
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    async def decide(self, request: BrainInput) -> BrainDecision:
        if not isinstance(request, BrainInput):
            raise TypeError("request must be BrainInput")
        self._call_count += 1
        return NoDecision()
