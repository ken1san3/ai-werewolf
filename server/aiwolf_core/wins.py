"""Data-driven terminal win-condition evaluation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Mapping

from .events import EventVisibility, GameEvent
from .models import PlayerRoleState, WinCondition, resolve_effective_attributes, resolve_effective_win_conditions
from .state import GameResult

if TYPE_CHECKING:
    from .game import GameState


PRIMARY_WIN_CONDITION_DISPATCH_IDS = frozenset({"eliminate_role_tag", "count_parity"})


class WinEvaluator:
    """Evaluate victory from content, effective attributes, and living players."""

    def __init__(self, game: GameState) -> None:
        self.game = game

    def evaluate(self) -> GameResult | None:
        """Return a terminal result when one exists, without emitting an event."""

        if not any(player.alive for player in self.game.players.values()):
            return self._draw_result()
        winner_team = self._primary_winner_team()
        if winner_team is None:
            return None
        winner_team, additional_winner_ids = self._apply_survivor_conditions(winner_team)
        return self._team_victory_result(winner_team, additional_winner_ids)

    def evaluate_and_record(self) -> GameResult | None:
        """Store and publish a terminal result at a single authoritative boundary."""

        if self.game.game_result is not None:
            return self.game.game_result
        result = self.evaluate()
        if result is None:
            return None
        self.game.game_result = result
        self.game.event_bus.publish(
            GameEvent(
                type="GAME_ENDED",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "winner_team": result.winner_team,
                    "outcome": result.outcome,
                    "player_results": dict(result.player_results),
                },
            )
        )
        return result

    def _primary_winner_team(self) -> str | None:
        for team_id in self.game.rules.win_evaluation_order:
            conditions = self.game.content.teams[team_id].win_conditions
            primary_conditions = tuple(
                condition
                for condition in conditions
                if condition.type != "survive_when_others_win"
            )
            if primary_conditions and all(
                self._condition_matches(condition) for condition in primary_conditions
            ):
                return team_id
        return None

    def _apply_survivor_conditions(self, winner_team: str) -> tuple[str, set[str]]:
        """Apply living third-party survivors after a base team has won.

        ``replaces: true`` promotes one surviving third-party team to the sole
        winner.  ``false`` leaves the base team as winner while the surviving
        player receives an individual win; this preserves the current
        single-``winner_team`` result shape until Phase 8 supports multiple
        winner teams.
        """

        replacing_teams: set[str] = set()
        additional_winner_ids: set[str] = set()
        for player_id, player in self.game.players.items():
            if not player.alive:
                continue
            attributes = resolve_effective_attributes(
                PlayerRoleState(player.player_id, player.role, player.modifiers)
            )
            for condition in resolve_effective_win_conditions(
                PlayerRoleState(player.player_id, player.role, player.modifiers), self.game.content.teams
            ):
                if condition.type != "survive_when_others_win":
                    continue
                if attributes.team == winner_team:
                    continue
                if condition.data["replaces"]:
                    replacing_teams.add(attributes.team)
                else:
                    additional_winner_ids.add(player_id)
        if not replacing_teams:
            return winner_team, additional_winner_ids
        if len(replacing_teams) != 1:
            raise ValueError("multiple replacing survivor teams require the Phase 8 result model")
        return next(iter(replacing_teams)), set()

    def _team_victory_result(
        self, winner_team: str, additional_winner_ids: set[str]
    ) -> GameResult:
        player_results = {
            player_id: (
                "won"
                if (
                    resolve_effective_attributes(
                        PlayerRoleState(player.player_id, player.role, player.modifiers)
                    ).team
                    == winner_team
                    or player_id in additional_winner_ids
                )
                else "lost"
            )
            for player_id, player in self.game.players.items()
        }
        return GameResult(
            winner_team=winner_team,
            outcome="team_victory",
            player_results=player_results,
        )

    def _draw_result(self) -> GameResult:
        return GameResult(
            winner_team=None,
            outcome="draw",
            player_results={player_id: "lost" for player_id in self.game.players},
        )

    def _condition_matches(self, condition: WinCondition) -> bool:
        if condition.type not in PRIMARY_WIN_CONDITION_DISPATCH_IDS:
            raise RuntimeError(f"unsupported primary win condition '{condition.type}'")
        evaluators: Mapping[str, Callable[[WinCondition], bool]] = {
            "eliminate_role_tag": self._eliminate_role_tag,
            "count_parity": self._count_parity,
        }
        return evaluators[condition.type](condition)

    def _eliminate_role_tag(self, condition: WinCondition) -> bool:
        tag = condition.data["tag"]
        return not any(player.alive and tag in player.role.tags for player in self.game.players.values())

    def _count_parity(self, condition: WinCondition) -> bool:
        subject = condition.data["subject"]
        against = condition.data["against"]
        subject_count = 0
        against_count = 0
        for player in self.game.players.values():
            if not player.alive:
                continue
            count_as = resolve_effective_attributes(
                PlayerRoleState(player.player_id, player.role, player.modifiers)
            ).count_as
            subject_count += count_as == subject
            against_count += count_as == against
        operator = condition.data["operator"]
        comparisons: Mapping[str, Callable[[int, int], bool]] = {
            "gte": lambda left, right: left >= right,
            "gt": lambda left, right: left > right,
            "eq": lambda left, right: left == right,
            "lte": lambda left, right: left <= right,
            "lt": lambda left, right: left < right,
        }
        try:
            return comparisons[operator](subject_count, against_count)
        except KeyError as error:
            raise RuntimeError(f"unsupported count-parity operator '{operator}'") from error
