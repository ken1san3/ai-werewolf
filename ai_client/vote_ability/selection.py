"""Stable, received-handle-only vote and ability selection."""

from __future__ import annotations

import hashlib
from typing import Iterable

from ai_client.brain import (
    AbilityDecision,
    BrainDecision,
    BrainInput,
    NoDecision,
    VoteDecision,
)
from ai_client.network import AbilityAction, VoteAction


_DOMAIN = b"aiwolf.phase3.5.selection.v1"


def _stable_digest(*parts: object) -> bytes:
    digest = hashlib.sha256()
    digest.update(_DOMAIN)
    for part in parts:
        encoded = str(part).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.digest()


def _rank(
    candidates: Iterable[str],
    *,
    master_seed: int,
    player_id: str,
    day: int,
    phase: str,
    family: str,
    candidate_kind: str,
) -> tuple[str, ...]:
    values = tuple(candidates)
    return tuple(
        sorted(
            values,
            key=lambda candidate: (
                _stable_digest(
                    master_seed,
                    player_id,
                    day,
                    phase,
                    family,
                    candidate_kind,
                    candidate,
                ),
                candidate,
            ),
        )
    )


def _reservation_decision(
    request: BrainInput, *, master_seed: int
) -> BrainDecision:
    options = request.action_context.options
    if not options or request.snapshot.phase is None or request.snapshot.self_view is None:
        return NoDecision()
    handles = tuple(option.handle for option in options)
    if all(isinstance(handle, VoteAction) for handle in handles):
        if len(options) != 1:
            return NoDecision()
        option = options[0]
        handle = option.handle
        assert isinstance(handle, VoteAction)
        targets = handle.valid_targets
        if (
            not isinstance(handle.target_count, int)
            or isinstance(handle.target_count, bool)
            or handle.target_count != 1
            or len(set(targets)) != len(targets)
            or any(not isinstance(target, str) or not target for target in targets)
        ):
            return NoDecision()
        if not targets:
            return VoteDecision(option.option_id, None) if handle.allows_abstain else NoDecision()
        ranked = _rank(
            targets,
            master_seed=master_seed,
            player_id=request.snapshot.self_view.player_id,
            day=request.snapshot.phase.day,
            phase=request.snapshot.phase.phase,
            family="vote",
            candidate_kind="vote_target",
        )
        return VoteDecision(option.option_id, ranked[0])
    if all(isinstance(handle, AbilityAction) for handle in handles):
        ability_ids = tuple(handle.ability_id for handle in handles)
        if (
            len(set(ability_ids)) != len(ability_ids)
            or any(not isinstance(ability_id, str) or not ability_id for ability_id in ability_ids)
        ):
            return NoDecision()
        eligible = []
        for option in options:
            handle = option.handle
            assert isinstance(handle, AbilityAction)
            targets = handle.valid_targets
            if (
                handle.uses_remaining == 0
                or not isinstance(handle.target_count, int)
                or isinstance(handle.target_count, bool)
                or handle.target_count < 1
                or len(targets) < handle.target_count
                or len(set(targets)) != len(targets)
                or any(not isinstance(target, str) or not target for target in targets)
                or (
                    handle.uses_remaining is not None
                    and (
                        isinstance(handle.uses_remaining, bool)
                        or not isinstance(handle.uses_remaining, int)
                        or handle.uses_remaining < 0
                    )
                )
            ):
                continue
            eligible.append(option)
        if not eligible:
            return NoDecision()
        player_id = request.snapshot.self_view.player_id
        day = request.snapshot.phase.day
        phase = request.snapshot.phase.phase
        ability_order = _rank(
            (option.handle.ability_id for option in eligible),
            master_seed=master_seed,
            player_id=player_id,
            day=day,
            phase=phase,
            family="ability",
            candidate_kind="ability",
        )
        selected_id = ability_order[0]
        selected = next(
            option for option in eligible if option.handle.ability_id == selected_id
        )
        selected_handle = selected.handle
        assert isinstance(selected_handle, AbilityAction)
        targets = _rank(
            selected_handle.valid_targets,
            master_seed=master_seed,
            player_id=player_id,
            day=day,
            phase=phase,
            family="ability",
            candidate_kind="ability_target",
        )[: selected_handle.target_count]
        return AbilityDecision(selected.option_id, targets)
    return NoDecision()


def handles_have_eligible_selection(
    request: BrainInput, *, master_seed: int
) -> bool:
    """Return whether the pure selector can produce a reservation decision."""

    return isinstance(
        _reservation_decision(request, master_seed=master_seed),
        (VoteDecision, AbilityDecision),
    )


class DeterministicVoteAbilityBrain:
    """Decorate one Brain with pure vote/ability selection, never model routing."""

    def __init__(self, *, master_seed: int, delegate) -> None:
        if isinstance(master_seed, bool) or not isinstance(master_seed, int):
            raise ValueError("master_seed must be an integer")
        if not hasattr(delegate, "decide") or not callable(delegate.decide):
            raise TypeError("delegate must provide an async decide(request) method")
        self.master_seed = master_seed
        self.delegate = delegate

    async def decide(self, request: BrainInput) -> BrainDecision:
        if not isinstance(request, BrainInput):
            raise TypeError("request must be BrainInput")
        handles = tuple(option.handle for option in request.action_context.options)
        reservation_count = sum(
            isinstance(handle, (VoteAction, AbilityAction)) for handle in handles
        )
        if reservation_count:
            if reservation_count != len(handles):
                return NoDecision()
            return _reservation_decision(request, master_seed=self.master_seed)
        return await self.delegate.decide(request)
