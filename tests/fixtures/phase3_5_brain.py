"""LLM-free cumulative Phase 3.5 Brain composition."""

from __future__ import annotations

from ai_client.vote_ability import DeterministicVoteAbilityBrain
from tests.fixtures.phase3_4_reaction_brain import (
    CompletionReactionBrain,
    CompletionReactionMode,
)


class CompletionPhase35Brain(DeterministicVoteAbilityBrain):
    """Compose the approved selector with the existing Reaction test Brain."""

    def __init__(
        self, *, player_id: str, seed: int, mode: CompletionReactionMode
    ) -> None:
        self.reaction_brain = CompletionReactionBrain(
            player_id=player_id,
            seed=seed,
            mode=mode,
        )
        self.call_count = 0
        self.decisions: list[dict[str, object]] = []
        super().__init__(master_seed=seed, delegate=self.reaction_brain)

    async def decide(self, request):
        self.call_count += 1
        decision = await super().decide(request)
        record: dict[str, object] = {"type": type(decision).__name__}
        for name in ("option_id", "target_player_id", "target_player_ids"):
            if hasattr(decision, name):
                value = getattr(decision, name)
                record[name] = list(value) if isinstance(value, tuple) else value
        self.decisions.append(record)
        return decision
