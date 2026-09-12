"""Deterministic speaking/silent Brain used only by Phase 3.4 completion."""

from __future__ import annotations

from enum import Enum

from ai_client.brain import BrainInput, ChatDecision, NoDecision
from ai_client.network import ChatAction


class CompletionReactionMode(str, Enum):
    SPEAK = "SPEAK"
    SILENT = "SILENT"


class CompletionReactionBrain:
    def __init__(
        self, *, player_id: str, seed: int, mode: CompletionReactionMode
    ) -> None:
        if not isinstance(player_id, str) or not player_id:
            raise ValueError("player_id must be a non-empty string")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("seed must be an integer")
        if not isinstance(mode, CompletionReactionMode):
            raise TypeError("mode must be CompletionReactionMode")
        self.player_id = player_id
        self.seed = seed
        self.mode = mode
        self._chat_ordinals: dict[tuple[int, str], int] = {}
        self.call_count = 0

    async def decide(self, request: BrainInput):
        self.call_count += 1
        if self.mode is CompletionReactionMode.SILENT:
            return NoDecision()
        chat_options = tuple(
            option
            for option in request.action_context.options
            if isinstance(option.handle, ChatAction)
        )
        if len(chat_options) != 1 or len(request.action_context.options) != 1:
            return NoDecision()
        phase = request.snapshot.phase
        if phase is None:
            return NoDecision()
        key = (phase.day, phase.phase)
        ordinal = self._chat_ordinals.get(key, 0) + 1
        self._chat_ordinals[key] = ordinal
        return ChatDecision(
            option_id=chat_options[0].option_id,
            message=(
                f"phase3.4/{self.seed}/{self.player_id}/"
                f"{phase.day}/{phase.phase}/{ordinal}"
            ),
        )
