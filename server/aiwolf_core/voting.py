"""Vote reservation, tally, reveal, runoff, and execution transition logic."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Sequence

from .clock import timestamp
from .death import DeathResolver
from .events import EventVisibility, GameEvent
from .models import CoreDeathCause, GamePhase
from .phase import PhaseManager
from .rejections import ActionRejected
from .state import VoteResult, VoteResultKind
from .targets import alive_player
from .wins import WinEvaluator

if TYPE_CHECKING:
    from .game import GameState
    from .state import Player


def valid_vote_target_ids(game: GameState, voter: Player) -> tuple[str, ...]:
    """Return vote targets accepted for this voter in the current round."""

    candidates = (
        game.runoff_candidate_player_ids
        if game.phase is GamePhase.RUNOFF
        else tuple(game.players)
    )
    return tuple(
        player_id
        for player_id in candidates
        if game.players[player_id].alive
        and (game.rules.vote.self_vote or player_id != voter.player_id)
    )


def can_abstain(game: GameState, voter_player_id: str) -> bool:
    """Return whether the vote's explicit abstention choice is still legal."""

    abstain = game.rules.vote.abstain
    return abstain.enabled and (
        abstain.max_per_player is None
        or game.abstentions_used.get(voter_player_id, 0) < abstain.max_per_player
    )


class VoteResolver:
    """Own server-private vote reservations and their authoritative resolution."""

    def __init__(self, game: GameState) -> None:
        self.game = game

    def submit(self, voter_player_id: str, target_player_id: str | None) -> None:
        """Reserve or replace one living player's vote for the current round."""

        if self.game.phase not in {GamePhase.VOTE, GamePhase.RUNOFF}:
            raise ActionRejected(
                "vote_unavailable", "votes can only be submitted during vote or runoff phases"
            )
        try:
            voter = alive_player(self.game, voter_player_id, "voter")
        except ValueError as error:
            raise ActionRejected("actor_unavailable", str(error)) from error
        if target_player_id is None:
            self.validate_abstention(voter.player_id)
        else:
            if target_player_id not in self.game.players:
                raise ActionRejected(
                    "unknown_target", f"unknown vote target '{target_player_id}'"
                )
            try:
                target = alive_player(self.game, target_player_id, "vote target")
            except ValueError as error:
                raise ActionRejected("invalid_target", str(error)) from error
            if target.player_id not in valid_vote_target_ids(self.game, voter):
                if not self.game.rules.vote.self_vote and voter.player_id == target.player_id:
                    raise ActionRejected(
                        "self_vote_disabled", "self-voting is disabled by the current rules"
                    )
                raise ActionRejected(
                    "invalid_target", "runoff votes must target a runoff candidate"
                )
        self.game.pending_votes[voter.player_id] = target_player_id
        self.game.event_bus.publish(
            GameEvent(
                type="VOTE_SUBMITTED",
                visibility=EventVisibility.SERVER,
                payload={
                    "day": self.game.day,
                    "phase": self.game.phase.value,
                    "voter_player_id": voter.player_id,
                    "target_player_id": target_player_id,
                },
            )
        )
        if self.game.rules.vote.reveal == "live":
            self.record_live_reveal(voter.player_id, target_player_id)

    def resolve(self, now: int) -> VoteResult:
        """Confirm the round at its deadline and enter Runoff or Execution."""

        if self.game.phase not in {GamePhase.VOTE, GamePhase.RUNOFF}:
            raise ValueError("votes can only be resolved during vote or runoff phases")
        now = timestamp(now)
        if self.game.phase_started_at is None or self.game.phase_ends_at is None:
            raise RuntimeError("vote phases must have an authoritative deadline")
        if now < self.game.phase_ends_at:
            raise ValueError("votes cannot be resolved before their deadline")
        if now < self.game.phase_started_at:
            raise ValueError("votes cannot be resolved before the phase starts")
        alive_player_ids = tuple(
            player_id for player_id, player in self.game.players.items() if player.alive
        )
        final_votes = {
            player_id: self.game.pending_votes[player_id]
            for player_id in alive_player_ids
            if player_id in self.game.pending_votes
        }
        self.consume_abstentions(final_votes)
        tallies = vote_tallies(final_votes, alive_player_ids)
        result = (
            VoteResult(kind=VoteResultKind.NO_LYNCH, tallies=tallies)
            if not alive_player_ids
            else self.resolve_tally(tallies)
        )
        self.game.last_vote_result = result
        if self.game.rules.vote.reveal == "after":
            self.record_after_reveal(final_votes)
        self.game.pending_votes.clear()
        self.record_result(result)
        phase = PhaseManager(self.game)
        if result.kind is VoteResultKind.RUNOFF:
            self.game.runoff_candidate_player_ids = result.runoff_candidate_player_ids
            phase.enter(GamePhase.RUNOFF, now)
            return result

        self.game.runoff_candidate_player_ids = ()
        # Execution is daytime-classified; enter it before the lynch event so
        # public death masking derives from the intended resolution phase.
        phase.enter(GamePhase.EXECUTION, now)
        if result.kind is VoteResultKind.LYNCH:
            DeathResolver(self.game).record(result.lynched_player_id, CoreDeathCause.LYNCHED.value)
        if WinEvaluator(self.game).evaluate_and_record() is not None:
            phase.enter(GamePhase.GAME_END, now)
        return result

    def resolve_tally(self, tallies: Mapping[str, int]) -> VoteResult:
        highest_vote_count = max(tallies.values())
        tied_player_ids = tuple(
            player_id for player_id, count in tallies.items() if count == highest_vote_count
        )
        if highest_vote_count == 0 and self.game.phase is GamePhase.VOTE:
            return self.resolve_tie(tied_player_ids, self.game.rules.vote.tie_without_runoff, tallies)
        if len(tied_player_ids) == 1:
            return VoteResult(
                kind=VoteResultKind.LYNCH,
                tallies=dict(tallies),
                lynched_player_id=tied_player_ids[0],
            )
        if self.game.phase is GamePhase.VOTE and self.game.rules.vote.runoff:
            return VoteResult(
                kind=VoteResultKind.RUNOFF,
                tallies=dict(tallies),
                runoff_candidate_player_ids=tied_player_ids,
            )
        tie_rule = (
            self.game.rules.vote.tie_after_runoff
            if self.game.phase is GamePhase.RUNOFF
            else self.game.rules.vote.tie_without_runoff
        )
        return self.resolve_tie(tied_player_ids, tie_rule, tallies)

    def resolve_tie(
        self, tied_player_ids: tuple[str, ...], tie_rule: str, tallies: Mapping[str, int]
    ) -> VoteResult:
        if tie_rule == "no_lynch":
            return VoteResult(kind=VoteResultKind.NO_LYNCH, tallies=dict(tallies))
        selected_player_id = self.game.rng.choice(tied_player_ids)
        self.game.event_bus.publish(
            GameEvent(
                type="TIE_RESOLVED_RANDOM",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "day": self.game.day,
                    "phase": self.game.phase.value,
                    "candidate_player_ids": list(tied_player_ids),
                    "selected_player_id": selected_player_id,
                },
            )
        )
        return VoteResult(
            kind=VoteResultKind.LYNCH,
            tallies=dict(tallies),
            lynched_player_id=selected_player_id,
        )

    def record_result(self, result: VoteResult) -> None:
        self.game.event_bus.publish(
            GameEvent(
                type="VOTE_RESOLVED",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "day": self.game.day,
                    "phase": self.game.phase.value,
                    "result": result.kind.value,
                    "tallies": dict(result.tallies),
                    "lynched_player_id": result.lynched_player_id,
                    "runoff_candidate_player_ids": list(result.runoff_candidate_player_ids),
                },
            )
        )

    def record_live_reveal(self, voter_player_id: str, target_player_id: str | None) -> None:
        self.game.event_bus.publish(
            GameEvent(
                type="VOTE_REVEALED_LIVE",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "day": self.game.day,
                    "phase": self.game.phase.value,
                    "voter_player_id": voter_player_id,
                    "target_player_id": target_player_id,
                },
            )
        )

    def record_after_reveal(self, final_votes: Mapping[str, str | None]) -> None:
        self.game.event_bus.publish(
            GameEvent(
                type="VOTES_REVEALED_AFTER",
                visibility=EventVisibility.PUBLIC,
                payload={
                    "day": self.game.day,
                    "phase": self.game.phase.value,
                    "votes": [
                        {"voter_player_id": voter, "target_player_id": target}
                        for voter, target in sorted(final_votes.items())
                    ],
                },
            )
        )

    def validate_abstention(self, voter_player_id: str) -> None:
        abstain = self.game.rules.vote.abstain
        if not abstain.enabled:
            raise ActionRejected(
                "abstention_disabled", "abstaining is disabled by the current rules"
            )
        if not can_abstain(self.game, voter_player_id):
            raise ActionRejected(
                "abstention_limit_reached", "the abstention limit has been reached"
            )

    def consume_abstentions(self, final_votes: Mapping[str, str | None]) -> None:
        for voter_player_id, target_player_id in final_votes.items():
            if target_player_id is None:
                self.game.abstentions_used[voter_player_id] = (
                    self.game.abstentions_used.get(voter_player_id, 0) + 1
                )


def vote_tallies(
    pending_votes: Mapping[str, str | None], alive_player_ids: Sequence[str]
) -> dict[str, int]:
    """Count final reservations, including every living player with zero votes."""

    alive_player_id_set = set(alive_player_ids)
    tallies = {player_id: 0 for player_id in sorted(alive_player_ids)}
    for voter_player_id in alive_player_ids:
        target_player_id = pending_votes.get(voter_player_id)
        if target_player_id in alive_player_id_set:
            tallies[target_player_id] += 1
    return tallies
