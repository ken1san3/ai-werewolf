"""Player-view action enumeration built from authoritative core constraints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .action_constraints import ActionConstraints
from .models import GamePhase
from .state import ActionSpec, Player
from .targets import chat_channels_for
from .voting import can_abstain, valid_vote_target_ids

if TYPE_CHECKING:
    from .game import GameState


class ActionAvailability(ActionConstraints):
    """Build ActionSpec records without changing game state or using a network layer."""

    def get(self, player_id: str) -> list[ActionSpec]:
        try:
            player = self.game.players[player_id]
        except KeyError as error:
            raise ValueError(f"unknown player '{player_id}'") from error
        if not player.alive:
            return []

        actions = self.ability_actions(player)
        chat_actions = self.chat_actions(player)
        actions.extend(chat_actions)
        actions.extend(self.phase_actions(player, chat_actions))
        return actions

    def ability_actions(self, player: Player) -> list[ActionSpec]:
        actions: list[ActionSpec] = []
        for ability in player.role.abilities:
            try:
                self.validate_ability_timing(player, ability)
            except ValueError:
                continue
            actions.append(
                ActionSpec(
                    type="ability",
                    ability_id=ability.id,
                    description=ability.description,
                    valid_targets=self.available_target_ids(player, ability),
                    target_count=ability.target.count,
                    uses_remaining=self.uses_remaining(player, ability),
                )
            )
        return actions

    def chat_actions(self, player: Player) -> list[ActionSpec]:
        phase_id = self.game.phase.value
        return [
            ActionSpec(type="chat", channel=channel_id)
            for channel_id in chat_channels_for(player)
            if phase_id in self.game.content.chat_channels[channel_id].phases
        ]

    def phase_actions(self, player: Player, chat_actions: list[ActionSpec]) -> list[ActionSpec]:
        actions: list[ActionSpec] = []
        if any(
            action.channel is not None
            and self.game.content.chat_channels[action.channel].allows_co
            for action in chat_actions
        ):
            if self.game.can_declare_co(player.player_id):
                actions.append(ActionSpec(type="co_declare"))
            actions.append(ActionSpec(type="co_report"))
        if self.game.phase not in {GamePhase.VOTE, GamePhase.RUNOFF}:
            return actions
        actions.append(
            ActionSpec(
                type="vote",
                valid_targets=valid_vote_target_ids(self.game, player),
                target_count=1,
                allows_abstain=can_abstain(self.game, player.player_id),
            )
        )
        return actions
