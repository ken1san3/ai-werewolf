"""Day-end sudden-death resolution driven entirely by content rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .death import DeathResolver
from .models import CoreDeathCause
from .wins import WinEvaluator

if TYPE_CHECKING:
    from .game import GameState


class DayEndResolver:
    """Resolve configured silence deaths before the game enters Vote."""

    def __init__(self, game: GameState) -> None:
        self.game = game

    def resolve_sudden_death(self) -> bool:
        """Kill every living player with neither public chat nor a CO declaration today."""

        if not self.game.rules.sudden_death.enabled:
            return False
        silent_player_ids = tuple(
            player_id
            for player_id, player in self.game.players.items()
            if player.alive
            and self.game.public_chat_counts.get((self.game.day, player_id), 0) == 0
            and self.game.co_declaration_counts.get((self.game.day, player_id), 0) == 0
        )
        for player_id in silent_player_ids:
            DeathResolver(self.game).record(player_id, CoreDeathCause.SUDDEN_DEATH.value)
        if silent_player_ids:
            WinEvaluator(self.game).evaluate_and_record()
        return bool(silent_player_ids)
